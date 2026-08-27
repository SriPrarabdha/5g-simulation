import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audit import AuditStore
from .cdot_live import CdotLiveService, LiveConfig
from .cdot_live.service import LiveConflict, LiveRejected, UpstreamFailure
from .runtime import CONTROLLERS, RunManager
from .security import Identity, TokenService


ROOT = Path(__file__).resolve().parents[1]


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateRunRequest(BaseModel):
    scenario_id: str = "demo-three-upf-two-zone"
    controller: str = "mpc"
    seed: int | None = None


class ControlRequest(BaseModel):
    speed: float | None = None
    controller: str | None = None
    surge: float | None = None
    telemetry_gap_steps: int | None = Field(default=None, ge=0, le=120)
    fault: dict[str, Any] | None = None
    min_hold_epochs: int | None = Field(default=None, ge=0, le=8)
    hysteresis: float | None = Field(default=None, ge=0, le=0.5)
    churn_budget: float | None = Field(default=None, ge=0, le=1)
    pause_at_step: int | None = Field(default=None, ge=1, le=100000)


class StoryRewindRequest(BaseModel):
    checkpoint_id: str
    autoplay: bool = True


class CdotLiveApplyRequest(BaseModel):
    proposal_id: str
    expected_smf_state_hash: str
    confirmation: bool


class CdotLiveRollbackRequest(BaseModel):
    application_id: str
    expected_smf_state_hash: str
    confirmation: bool


def create_app(
    *,
    scenario_path: str | Path | None = None,
    audit_path: str | Path = ":memory:",
) -> FastAPI:
    manager = RunManager(scenario_path or ROOT / "configs" / "demo_mpc_scenario.json")
    tokens = TokenService()
    audit = AuditStore(audit_path)
    live = CdotLiveService(
        LiveConfig.from_env(),
        audit_callback=lambda actor, action, payload: audit.append("cdot-live", actor, action, payload),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if os.environ.get("CDOT_LIVE_POLL_ENABLED", "1").lower() not in {"0", "false", "no"}:
            await live.start()
        try:
            yield
        finally:
            await live.close()

    application = FastAPI(
        title="C-DOT Closed-Loop 5G Traffic Engineering Demonstrator",
        version="1.0.0",
        description="Synthetic, deterministic UPF forecasting and steering demonstration.",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.state.manager = manager
    application.state.tokens = tokens
    application.state.audit = audit
    application.state.cdot_live = live

    async def identity(authorization: Annotated[str | None, Header()] = None) -> Identity:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token required")
        try:
            return tokens.verify(authorization.split(" ", 1)[1])
        except ValueError as error:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error

    async def presenter(user: Annotated[Identity, Depends(identity)]) -> Identity:
        if user.role != "presenter":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "presenter role required")
        return user

    def run_or_404(run_id: str):
        try:
            return manager.get(run_id)
        except KeyError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    @application.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "schema_version": "health/1.0", "synthetic": True,
                "active_runs": len(manager.runs)}

    @application.post("/api/v1/auth/login")
    async def login(request: LoginRequest) -> dict[str, Any]:
        expected_user = os.environ.get("CDOT_DEMO_USER", "presenter")
        expected_password = os.environ.get("CDOT_DEMO_PASSWORD", "demo")
        if request.username != expected_user or request.password != expected_password:
            audit.append(None, request.username, "login.denied", {})
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid presenter credentials")
        token = tokens.issue(request.username, "presenter")
        audit.append(None, request.username, "login.accepted", {})
        return {"access_token": token, "token_type": "bearer", "role": "presenter", "expires_in": 3600}

    @application.post("/api/v1/auth/renew")
    async def renew(user: Annotated[Identity, Depends(identity)]) -> dict[str, Any]:
        return {"access_token": tokens.issue(user.subject, user.role), "token_type": "bearer",
                "role": user.role, "expires_in": 3600}

    @application.post("/api/v1/viewer/session")
    async def viewer_session() -> dict[str, Any]:
        return {"access_token": tokens.issue("audience", "viewer", 4 * 3600), "token_type": "bearer",
                "role": "viewer", "expires_in": 4 * 3600}

    @application.get("/api/v1/scenarios")
    async def scenarios() -> dict[str, Any]:
        config = manager.scenario
        return {"items": [{
            "scenario_id": config.scenario_id,
            "name": "Stadium surge and heterogeneous three-UPF topology",
            "description": "Deterministic accelerated replay with capacity degradation and health events.",
            "seed": config.seed,
            "duration_seconds": config.steps * config.step_seconds,
            "resolution_seconds": config.step_seconds,
            "bucket_seconds": config.step_seconds * config.decision_interval_steps,
            "upfs": len(config.upfs), "groups": len(config.groups), "synthetic": True,
        }]}

    @application.get("/api/v1/artifacts")
    async def artifacts() -> dict[str, Any]:
        registry = json.loads((ROOT / "configs" / "traffic_model_registry.json").read_text(encoding="utf-8"))
        items = [
            {"artifact_id": "traffic-model/1.0", "kind": "parameter_registry", "immutable": True,
             "synthetic": True, "metadata": registry},
            {"artifact_id": "demo-replay/1.0", "kind": "deterministic_generator", "immutable": True,
             "synthetic": True, "seed": manager.scenario.seed},
        ]
        if manager.forecast_bundle is not None:
            items.append({
                "artifact_id": manager.forecast_bundle.model_version,
                "kind": "trained_forecast_bundle", "immutable": True,
                "synthetic": True, "metadata": manager.forecast_bundle.metadata,
            })
        items.extend([
            {
                "artifact_id": manager.mpc_profile["profile_id"],
                "kind": "cohort_mpc_profile",
                "immutable": True,
                "synthetic": True,
                "metadata": manager.mpc_profile,
            },
            {
                "artifact_id": manager.campaign_evidence["campaign_id"],
                "kind": "paired_campaign_evidence",
                "immutable": True,
                "synthetic": True,
                "metadata": manager.campaign_evidence,
            },
        ])
        return {"items": items}

    @application.post("/api/v1/runs", status_code=status.HTTP_201_CREATED)
    async def create_run(request: CreateRunRequest, user: Annotated[Identity, Depends(presenter)]) -> dict[str, Any]:
        if request.scenario_id != manager.scenario.scenario_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown scenario")
        if request.controller not in CONTROLLERS or request.controller == "oracle":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "controller is not deployable")
        run = manager.create(request.controller, request.seed)
        audit.append(run.run_id, user.subject, "run.created", request.model_dump())
        return run.snapshot()

    @application.get("/api/v1/runs/{run_id}")
    async def get_run(run_id: str, _: Annotated[Identity, Depends(identity)]) -> dict[str, Any]:
        return run_or_404(run_id).snapshot()

    @application.post("/api/v1/runs/{run_id}/{action}")
    async def run_action(run_id: str, action: str, user: Annotated[Identity, Depends(presenter)]) -> dict[str, Any]:
        run = run_or_404(run_id)
        if action not in {"start", "pause", "resume", "reset"}:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown run action")
        if action in {"start", "resume"}:
            await run.start()
        elif action == "pause":
            await run.pause()
        else:
            await run.reset()
        audit.append(run_id, user.subject, f"run.{action}", {})
        return run.snapshot()

    @application.patch("/api/v1/runs/{run_id}/controls")
    async def controls(run_id: str, request: ControlRequest,
                       user: Annotated[Identity, Depends(presenter)]) -> dict[str, Any]:
        run = run_or_404(run_id)
        changes = {key: value for key, value in request.model_dump().items() if value is not None}
        try:
            await run.apply_controls(changes)
        except ValueError as error:
            audit.append(run_id, user.subject, "controls.rejected", {"changes": changes, "reason": str(error)})
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
        audit.append(run_id, user.subject, "controls.applied", changes)
        return run.snapshot()

    @application.post("/api/v1/runs/{run_id}/story/rewind")
    async def rewind_story(
        run_id: str,
        request: StoryRewindRequest,
        user: Annotated[Identity, Depends(presenter)],
    ) -> dict[str, Any]:
        run = run_or_404(run_id)
        try:
            await run.rewind(request.checkpoint_id, autoplay=request.autoplay)
        except ValueError as error:
            audit.append(run_id, user.subject, "story.rewind_rejected", {
                "checkpoint_id": request.checkpoint_id,
                "reason": str(error),
            })
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
        audit.append(run_id, user.subject, "story.rewound", request.model_dump())
        return run.snapshot()

    @application.get("/api/v1/runs/{run_id}/telemetry")
    async def telemetry(run_id: str, _: Annotated[Identity, Depends(identity)], limit: int = Query(120, ge=1, le=2000)):
        return {"items": run_or_404(run_id).history[-limit:], "synthetic": True}

    @application.get("/api/v1/runs/{run_id}/forecasts")
    async def forecasts(run_id: str, _: Annotated[Identity, Depends(identity)]):
        return {"items": run_or_404(run_id).forecasts, "synthetic": True}

    @application.get("/api/v1/runs/{run_id}/topology")
    async def topology(run_id: str, _: Annotated[Identity, Depends(identity)]):
        return run_or_404(run_id).snapshot()["payload"]["topology"]

    @application.get("/api/v1/runs/{run_id}/policy")
    async def policy(run_id: str, _: Annotated[Identity, Depends(identity)]):
        run = run_or_404(run_id)
        return {"current": run.actuator.current, "history": run.actuator.history}

    @application.get("/api/v1/runs/{run_id}/decisions")
    async def decisions(run_id: str, _: Annotated[Identity, Depends(identity)]):
        return {"items": run_or_404(run_id).decision_trace}

    @application.get("/api/v1/runs/{run_id}/comparison")
    async def comparison(run_id: str, _: Annotated[Identity, Depends(identity)]):
        return run_or_404(run_id).comparison()

    @application.get("/api/v1/runs/{run_id}/audit")
    async def audit_events(run_id: str, _: Annotated[Identity, Depends(identity)]):
        run_or_404(run_id)
        return {"items": audit.list(run_id)}

    @application.get("/api/v1/models")
    async def models() -> dict[str, Any]:
        return {"items": [{
            "model_id": "deterministic-trend-envelope/1.0-demo",
            "label": "Deterministic replay trend envelope",
            "inputs": ["calendar", "event_schedule", "lagged_demand", "quality_flags"],
            "targets": ["arrivals", "offered_throughput", "sessions", "residual_load"],
            "quantiles": [0.5, 0.9, 0.95], "horizons_minutes": list(range(10, 81, 10)),
            "calibration": "fixed empirical demo envelope",
            "validation_selection": "not release-calibrated", "synthetic_training_data": True,
        }]}

    @application.get("/api/v1/cdot-live/status")
    async def cdot_live_status(_: Annotated[Identity, Depends(identity)]) -> dict[str, Any]:
        return await live.refresh_status()

    @application.get("/api/v1/cdot-live/snapshot")
    async def cdot_live_snapshot(_: Annotated[Identity, Depends(identity)]) -> dict[str, Any]:
        return live.snapshot()

    @application.post("/api/v1/cdot-live/evaluate")
    async def cdot_live_evaluate(user: Annotated[Identity, Depends(presenter)]) -> dict[str, Any]:
        try:
            return await live.evaluate(actor=user.subject)
        except Exception as error:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error

    @application.post("/api/v1/cdot-live/preload")
    async def cdot_live_preload(
        user: Annotated[Identity, Depends(presenter)], hours: float = 3.0
    ) -> dict[str, Any]:
        try:
            return await live.preload(hours=hours, actor=user.subject)
        except Exception as error:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error

    @application.post("/api/v1/cdot-live/act")
    async def cdot_live_act(
        user: Annotated[Identity, Depends(presenter)], act: str
    ) -> dict[str, Any]:
        try:
            return await live.set_act(act, actor=user.subject)
        except LiveRejected as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error

    @application.post("/api/v1/cdot-live/apply")
    async def cdot_live_apply(
        request: CdotLiveApplyRequest,
        user: Annotated[Identity, Depends(presenter)],
    ) -> dict[str, Any]:
        try:
            return await live.apply(
                request.proposal_id, request.expected_smf_state_hash, request.confirmation,
                actor=user.subject,
            )
        except LiveConflict as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        except LiveRejected as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
        except UpstreamFailure as error:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error

    @application.post("/api/v1/cdot-live/rollback")
    async def cdot_live_rollback(
        request: CdotLiveRollbackRequest,
        user: Annotated[Identity, Depends(presenter)],
    ) -> dict[str, Any]:
        try:
            return await live.rollback(
                request.application_id, request.expected_smf_state_hash, request.confirmation,
                actor=user.subject,
            )
        except LiveConflict as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        except LiveRejected as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
        except UpstreamFailure as error:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error

    @application.get("/metrics", response_class=PlainTextResponse)
    async def metrics(run_id: str | None = None) -> str:
        if run_id is None:
            if not manager.runs:
                return "# HELP cdot_demo_active_runs Number of demo runs.\n# TYPE cdot_demo_active_runs gauge\ncdot_demo_active_runs 0\n"
            run_id = next(reversed(manager.runs))
        return run_or_404(run_id).prometheus_text()

    @application.websocket("/api/v1/ws/runs/{run_id}")
    async def websocket_stream(websocket: WebSocket, run_id: str, token: str = Query(...)) -> None:
        try:
            tokens.verify(token)
            run = manager.get(run_id)
        except (ValueError, KeyError):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        queue = run.subscribe()
        try:
            await websocket.send_json(run.snapshot())
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            run.unsubscribe(queue)

    @application.websocket("/api/v1/ws/cdot-live")
    async def cdot_live_websocket(websocket: WebSocket, token: str = Query(...)) -> None:
        try:
            tokens.verify(token)
        except ValueError:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        queue = live.subscribe()
        try:
            await websocket.send_json({"type": "snapshot", "payload": live.snapshot()})
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            live.unsubscribe(queue)

    static = ROOT / "demo_api" / "static"
    if static.exists():
        assets = static / "assets"
        if assets.exists():
            application.mount("/assets", StaticFiles(directory=assets), name="assets")

        @application.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            candidate = static / path
            if path and candidate.is_file() and static in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(static / "index.html")

    return application


app = create_app()
