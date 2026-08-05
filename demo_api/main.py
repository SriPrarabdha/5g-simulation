import json
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audit import AuditStore
from .runtime import CONTROLLERS, RunManager
from .security import Identity, TokenService


ROOT = Path(__file__).resolve().parents[1]


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateRunRequest(BaseModel):
    scenario_id: str = "demo-three-upf-two-zone"
    controller: str = "predictive"
    seed: int | None = None


class ControlRequest(BaseModel):
    speed: float | None = None
    controller: str | None = None
    surge: float | None = None
    telemetry_gap_steps: int | None = Field(default=None, ge=0, le=120)
    fault: dict[str, Any] | None = None


def create_app(
    *,
    scenario_path: str | Path | None = None,
    audit_path: str | Path = ":memory:",
) -> FastAPI:
    application = FastAPI(
        title="C-DOT Closed-Loop 5G Traffic Engineering Demonstrator",
        version="1.0.0",
        description="Synthetic, deterministic UPF forecasting and steering demonstration.",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    manager = RunManager(scenario_path or ROOT / "configs" / "demo_scenario.json")
    tokens = TokenService()
    audit = AuditStore(audit_path)
    application.state.manager = manager
    application.state.tokens = tokens
    application.state.audit = audit

    def identity(authorization: Annotated[str | None, Header()] = None) -> Identity:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token required")
        try:
            return tokens.verify(authorization.split(" ", 1)[1])
        except ValueError as error:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error

    def presenter(user: Annotated[Identity, Depends(identity)]) -> Identity:
        if user.role != "presenter":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "presenter role required")
        return user

    def run_or_404(run_id: str):
        try:
            return manager.get(run_id)
        except KeyError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    @application.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "schema_version": "health/1.0", "synthetic": True,
                "active_runs": len(manager.runs)}

    @application.post("/api/v1/auth/login")
    def login(request: LoginRequest) -> dict[str, Any]:
        expected_user = os.environ.get("CDOT_DEMO_USER", "presenter")
        expected_password = os.environ.get("CDOT_DEMO_PASSWORD", "demo")
        if request.username != expected_user or request.password != expected_password:
            audit.append(None, request.username, "login.denied", {})
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid presenter credentials")
        token = tokens.issue(request.username, "presenter")
        audit.append(None, request.username, "login.accepted", {})
        return {"access_token": token, "token_type": "bearer", "role": "presenter", "expires_in": 3600}

    @application.post("/api/v1/auth/renew")
    def renew(user: Annotated[Identity, Depends(identity)]) -> dict[str, Any]:
        return {"access_token": tokens.issue(user.subject, user.role), "token_type": "bearer",
                "role": user.role, "expires_in": 3600}

    @application.post("/api/v1/viewer/session")
    def viewer_session() -> dict[str, Any]:
        return {"access_token": tokens.issue("audience", "viewer", 4 * 3600), "token_type": "bearer",
                "role": "viewer", "expires_in": 4 * 3600}

    @application.get("/api/v1/scenarios")
    def scenarios() -> dict[str, Any]:
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
    def artifacts() -> dict[str, Any]:
        registry = json.loads((ROOT / "configs" / "traffic_model_registry.json").read_text(encoding="utf-8"))
        return {"items": [
            {"artifact_id": "traffic-model/1.0", "kind": "parameter_registry", "immutable": True,
             "synthetic": True, "metadata": registry},
            {"artifact_id": "demo-replay/1.0", "kind": "deterministic_generator", "immutable": True,
             "synthetic": True, "seed": manager.scenario.seed},
        ]}

    @application.post("/api/v1/runs", status_code=status.HTTP_201_CREATED)
    def create_run(request: CreateRunRequest, user: Annotated[Identity, Depends(presenter)]) -> dict[str, Any]:
        if request.scenario_id != manager.scenario.scenario_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown scenario")
        if request.controller not in CONTROLLERS or request.controller == "oracle":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "controller is not deployable")
        run = manager.create(request.controller, request.seed)
        audit.append(run.run_id, user.subject, "run.created", request.model_dump())
        return run.snapshot()

    @application.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str, _: Annotated[Identity, Depends(identity)]) -> dict[str, Any]:
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

    @application.get("/api/v1/runs/{run_id}/telemetry")
    def telemetry(run_id: str, _: Annotated[Identity, Depends(identity)], limit: int = Query(120, ge=1, le=2000)):
        return {"items": run_or_404(run_id).history[-limit:], "synthetic": True}

    @application.get("/api/v1/runs/{run_id}/forecasts")
    def forecasts(run_id: str, _: Annotated[Identity, Depends(identity)]):
        return {"items": run_or_404(run_id).forecasts, "synthetic": True}

    @application.get("/api/v1/runs/{run_id}/topology")
    def topology(run_id: str, _: Annotated[Identity, Depends(identity)]):
        return run_or_404(run_id).snapshot()["payload"]["topology"]

    @application.get("/api/v1/runs/{run_id}/policy")
    def policy(run_id: str, _: Annotated[Identity, Depends(identity)]):
        run = run_or_404(run_id)
        return {"current": run.actuator.current, "history": run.actuator.history}

    @application.get("/api/v1/runs/{run_id}/decisions")
    def decisions(run_id: str, _: Annotated[Identity, Depends(identity)]):
        return {"items": run_or_404(run_id).decision_trace}

    @application.get("/api/v1/runs/{run_id}/comparison")
    def comparison(run_id: str, _: Annotated[Identity, Depends(identity)]):
        return run_or_404(run_id).comparison()

    @application.get("/api/v1/runs/{run_id}/audit")
    def audit_events(run_id: str, _: Annotated[Identity, Depends(identity)]):
        run_or_404(run_id)
        return {"items": audit.list(run_id)}

    @application.get("/api/v1/models")
    def models() -> dict[str, Any]:
        return {"items": [{
            "model_id": "calendar-ensemble+ACI/1.0-demo",
            "label": "Calendar-aware nonlinear ensemble",
            "inputs": ["calendar", "event_schedule", "lagged_demand", "quality_flags"],
            "targets": ["arrivals", "offered_throughput", "sessions", "residual_load"],
            "quantiles": [0.5, 0.9, 0.95], "horizons_minutes": list(range(10, 81, 10)),
            "calibration": "split conformal + adaptive conformal inference",
            "validation_selection": "MAE-weighted ensemble", "synthetic_training_data": True,
        }]}

    @application.get("/metrics", response_class=PlainTextResponse)
    def metrics(run_id: str | None = None) -> str:
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

    static = ROOT / "demo_api" / "static"
    if static.exists():
        assets = static / "assets"
        if assets.exists():
            application.mount("/assets", StaticFiles(directory=assets), name="assets")

        @application.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = static / path
            if path and candidate.is_file() and static in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(static / "index.html")

    return application


app = create_app()
