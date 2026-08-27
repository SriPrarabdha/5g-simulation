"""C-DOT live/replay pipeline service.

Rewritten around the demand cube.  What changed from the Codex version:

* **Rolling window, not closed ten-minute buckets.**  The old code needed a
  complete UTC-aligned bucket before it would do anything, and gated both bucket
  freshness and UPF health on one 90 s constant -- so Apply was live for about
  60 s out of every 600 s.
* **One joint solve** over every selection group instead of a per-group loop.
* **Two sources**: live Prometheus or CSV replay, chosen by config or per call,
  so the whole demo runs with C-DOT's lab down.
* **Three acts**: baseline (advisory withheld) -> optimised (advisory applied)
  -> scorecard, which is the running order Akash asked for.
* Forecast and solve run in a worker thread; they used to block the event loop.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .cdot_forecaster import CdotForecaster, ForecastError
from .config import LiveConfig
from .counterfactual import Counterfactual
from .counterfactual import run as run_counterfactual
from .demand import DemandCube, build_demand_cube, parse_group_id
from .optimizer import AllocationPlan, OptimizerError
from .optimizer import solve as solve_allocation_joint
from .smf import (
    H2CSmfClient,
    canonical_state_hash,
    extract_tuples,
    extract_weights,
    tuple_key,
    with_weights,
)
from .sources import ReplayClock, ReplaySource, SourceError, build_source

ACTS = ("preload", "baseline", "optimized", "scorecard")


class LiveConflict(RuntimeError):
    pass


class LiveRejected(RuntimeError):
    pass


class UpstreamFailure(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class CdotLiveService:
    def __init__(
        self,
        config: LiveConfig | None = None,
        *,
        source: Any | None = None,
        smf: H2CSmfClient | None = None,
        audit_callback: Callable[[str, str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.config = config or LiveConfig.from_env()
        self.source = source or build_source(self.config)
        self.smf = smf or H2CSmfClient(
            self.config.smf_url, timeout_seconds=self.config.timeout_seconds
        )
        self.audit_callback = audit_callback
        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._poll_task: asyncio.Task[None] | None = None
        self._sequence = 0
        self._clock: ReplayClock | None = None
        self._cube: DemandCube | None = None
        self._forecaster: CdotForecaster | None = None
        self._forecast: dict[str, Any] | None = None
        self._plan: AllocationPlan | None = None
        self._proposal: dict[str, Any] | None = None
        self._proposal_frozen = False
        self._counterfactual: Counterfactual | None = None
        self._smf_state: Any = None
        self._smf_hash: str | None = None
        self._last_error: str | None = None
        self._last_poll: str | None = None
        self._source_ready: bool | None = None
        self._smf_ready: bool | None = None
        self._audits: list[dict[str, Any]] = []
        self._applications: list[dict[str, Any]] = []
        self._verification: dict[str, Any] | None = None
        self._stage = "idle"
        self._act = "preload"
        if isinstance(self.source, ReplaySource):
            span = self.source.span()
            if span:
                self._clock = ReplayClock(span[0], span[1])
                # Start "now" one history window into the recording, so the very
                # first "load the last three hours" has three hours behind it
                # instead of a single sample.
                self._clock.seek(
                    span[0] + timedelta(seconds=self.config.cadence.history_seconds)
                )

    # ------------------------------------------------------------ plumbing

    def _audit(self, actor: str, action: str, payload: dict[str, Any]) -> None:
        event = {
            "id": len(self._audits) + 1,
            "wall_time": _now(),
            "actor": actor,
            "action": action,
            "payload": copy.deepcopy(payload),
        }
        self._audits.append(event)
        if self.audit_callback:
            self.audit_callback(actor, action, payload)

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self._sequence += 1
        event = {
            "schema_version": "cdot-live-stream/1.0",
            "sequence": self._sequence,
            "wall_time": _now(),
            "type": event_type,
            "payload": payload,
        }
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def start(self) -> None:
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop(), name="cdot-live-poller")

    async def close(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        closer = getattr(self.source, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:  # pragma: no cover - best-effort shutdown
                pass

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.cadence.decision_interval_seconds)
            try:
                await self.evaluate(actor="system-poller", audit=False)
            except Exception as error:  # isolated external degradation
                self._last_error = str(error)
                self._stage = "degraded"
                await self._emit("pipeline.degraded", {"error": str(error)})

    # --------------------------------------------------------------- timing

    def _now_trace(self) -> datetime:
        """Current time on the source's clock -- wall clock, or the replay clock."""
        if self._clock is not None:
            return self._clock.now()
        return datetime.now(timezone.utc)

    async def _load_window(self, seconds: int | None = None) -> DemandCube:
        span_seconds = seconds or self.config.cadence.history_seconds
        end = self._now_trace()
        start = end - timedelta(seconds=span_seconds)
        span = getattr(self.source, "span", lambda: None)()
        if span is not None and start < span[0]:
            # Never hand back a window that is mostly empty just because the
            # replay clock has not travelled far enough yet.
            start = span[0]
            end = min(span[1], start + timedelta(seconds=span_seconds))
        rows = await self.source.window(start, end)
        if not rows:
            raise SourceError(
                f"source returned no samples for {_iso(start)}..{_iso(end)}"
            )
        return build_demand_cube(
            rows,
            upfs=self.config.upf_ids,
            step_seconds=self.config.cadence.telemetry_step_seconds,
            start=start,
            end=end,
        )

    # ---------------------------------------------------------------- status

    async def refresh_status(self) -> dict[str, Any]:
        async def smf_read() -> tuple[bool, Any, str | None, str | None]:
            try:
                state = await self.smf.get_state()
                return True, state, canonical_state_hash(state), None
            except Exception as error:
                return False, None, None, str(error)

        async def source_read() -> bool:
            try:
                ready = getattr(self.source, "ready", None)
                return bool(await ready()) if ready else True
            except Exception:
                return False

        source_ready, smf_result = await asyncio.gather(source_read(), smf_read())
        self._source_ready = source_ready
        self._smf_ready, state, state_hash, smf_error = smf_result
        if state_hash is not None:
            self._smf_state, self._smf_hash = state, state_hash
        if smf_error:
            self._last_error = smf_error
        elif self._source_ready:
            self._last_error = None
        return self.status()

    def status(self) -> dict[str, Any]:
        freshness = None
        if self._cube is not None and self._cube.latest_time is not None:
            freshness = max(
                0.0, (self._now_trace() - self._cube.latest_time).total_seconds()
            )
        stale_after = self.config.cadence.telemetry_stale_seconds
        # A replay source can serve data with no live endpoint at all, so
        # readiness is about the *source in use*, not about Prometheus.
        healthy = self._source_ready is not False
        return {
            "schema_version": "cdot-live-status/2.0",
            "mode": "live_external_data",
            "isolated_from_synthetic": True,
            "status": "healthy" if healthy else "degraded" if self._last_error else "connecting",
            "stage": self._stage,
            "act": self._act,
            "source": self.source.describe(),
            "endpoints": {
                "prometheus": {
                    "url": self.config.prometheus_url,
                    "ready": self._source_ready,
                    "in_use": self.config.source_mode == "prometheus",
                },
                "smf": {
                    "url": self.config.smf_url,
                    "ready": self._smf_ready,
                    "protocol": "h2c-prior-knowledge",
                },
            },
            "freshness": {
                "latest_sample_age_seconds": freshness,
                "stale_after_seconds": stale_after,
                "fresh": freshness is not None and freshness <= stale_after,
            },
            "cadence": {
                "telemetry_step_seconds": self.config.cadence.telemetry_step_seconds,
                "decision_interval_seconds": self.config.cadence.decision_interval_seconds,
                "forecast_horizon_seconds": self.config.cadence.forecast_horizon_seconds,
                "history_seconds": self.config.cadence.history_seconds,
            },
            "capacity": {
                "per_upf_pps": self.config.capacity.per_upf_pps,
                "safe_utilization": self.config.capacity.safe_utilization,
                "safe_pps": self.config.capacity.safe_pps,
                "confirmed_by_cdot": self.config.capacity.confirmed_by_cdot,
            },
            "assumptions": self.config.unconfirmed_assumptions(),
            "units": {"traffic": "pps", "mbps": False},
            "current_smf_state_hash": self._smf_hash,
            "last_poll": self._last_poll,
            "last_error": self._last_error,
            "config_error": self.config.load_error,
        }

    # -------------------------------------------------------------- snapshot

    def snapshot(self) -> dict[str, Any]:
        cube = self._cube
        capacity = self.config.capacity
        upfs: list[dict[str, Any]] = []
        latest = cube.latest_upf_load() if cube else {}
        projected = self._plan.projected_load_pps if self._plan else {}
        for upf in self.config.upf_ids:
            observed = latest.get(upf, {"ul": 0.0, "dl": 0.0})
            total = observed["ul"] + observed["dl"]
            upfs.append(
                {
                    "upf": upf,
                    "smf": self.config.smf_name(upf),
                    "observed": {**observed, "total": total},
                    "projected": projected.get(upf),
                    "capacity_pps": capacity.per_upf_pps,
                    "safe_pps": capacity.safe_pps,
                    "utilization": total / capacity.per_upf_pps if capacity.per_upf_pps else None,
                    "headroom_pps": capacity.per_upf_pps - total,
                    "overloaded": total > capacity.per_upf_pps,
                    "unit": "pps",
                }
            )
        return {
            "schema_version": "cdot-live-snapshot/2.0",
            "sequence": self._sequence,
            "wall_time": _now(),
            "trace_time": _iso(self._now_trace()),
            "status": self.status(),
            "act": self._act,
            "acts": list(ACTS),
            "pipeline": {
                "stage": self._stage,
                "stages": [
                    "ingest",
                    "demand_cube",
                    "forecast",
                    "highs_optimization",
                    "presenter_review",
                    "smf_apply",
                    "get_verify",
                ],
            },
            "units": {"traffic": "pps", "mbps": False},
            "telemetry": {
                "series": cube.to_series_payload() if cube else None,
                "upfs": upfs,
            },
            "forecast": copy.deepcopy(self._forecast),
            "proposal": copy.deepcopy(self._proposal),
            "counterfactual": self._counterfactual.as_dict() if self._counterfactual else None,
            "smf": {
                "state": copy.deepcopy(self._smf_state),
                "state_hash": self._smf_hash,
                "verification": copy.deepcopy(self._verification),
            },
            "audit_events": copy.deepcopy(self._audits[-200:]),
            "rollback": {
                "available": bool(self._applications),
                "application_id": (
                    self._applications[-1]["application_id"] if self._applications else None
                ),
            },
        }

    # ------------------------------------------------------------ act control

    async def set_act(self, act: str, *, actor: str = "presenter") -> dict[str, Any]:
        if act not in ACTS:
            raise LiveRejected(f"unknown act {act!r}; expected one of {ACTS}")
        self._act = act
        self._audit(actor, "cdot-live.act", {"act": act})
        await self._emit("demo.act", {"act": act})
        return self.snapshot()

    async def preload(
        self, *, hours: float | None = None, actor: str = "presenter"
    ) -> dict[str, Any]:
        """Load the last N hours and score baseline vs advisory over it.

        This is the "don't wait ten minutes for traffic to build" path Akash
        asked for: with a replay source it fills every chart instantly, and with
        a live source it pulls real history from Prometheus.
        """
        seconds = int((hours or self.config.cadence.history_seconds / 3600.0) * 3600)
        async with self._lock:
            self._stage = "ingest"
            await self._emit("pipeline.stage", {"stage": self._stage})
            cube = await self._load_window(seconds)
            self._cube = cube
            self._stage = "forecast"
            await self._emit("pipeline.stage", {"stage": self._stage})
            counterfactual = await asyncio.to_thread(run_counterfactual, cube, self.config)
            self._counterfactual = counterfactual
            self._act = "baseline"
            self._last_poll = _now()
            self._stage = "presenter_review"
            self._audit(
                actor,
                "cdot-live.preload",
                {"seconds": seconds, "samples": len(cube), "scorecard": counterfactual.scorecard()},
            )
            await self._emit("demo.preloaded", counterfactual.scorecard())
            return self.snapshot()

    # -------------------------------------------------------------- evaluate

    async def evaluate(self, *, actor: str, audit: bool = True) -> dict[str, Any]:
        async with self._lock:
            if self._proposal_frozen:
                # A presenter has the review drawer open; overwriting the
                # proposal underneath them is what made Apply race into a
                # spurious 409 in the Codex build.
                return self.snapshot()
            self._stage = "ingest"
            await self._emit("pipeline.stage", {"stage": self._stage})
            try:
                cube = await self._load_window()
            except (SourceError, ValueError) as error:
                self._last_error = str(error)
                self._stage = "degraded"
                await self._emit("pipeline.degraded", {"error": str(error)})
                raise
            self._cube = cube
            self._last_poll = _now()

            self._stage = "forecast"
            await self._emit("pipeline.stage", {"stage": self._stage})
            try:
                forecaster, plan = await asyncio.to_thread(self._fit_and_solve, cube)
            except (ForecastError, OptimizerError, ValueError) as error:
                self._last_error = str(error)
                self._stage = "degraded"
                await self._emit("pipeline.degraded", {"error": str(error)})
                raise
            self._forecaster = forecaster
            self._plan = plan
            self._forecast = self._forecast_payload(forecaster, cube)

            self._stage = "highs_optimization"
            await self._emit("pipeline.stage", {"stage": self._stage})

            smf_rows = self._state_rows(self._smf_state) if self._smf_state is not None else {}
            self._proposal = self._build_proposal(plan, cube, smf_rows)
            self._stage = "presenter_review"
            if audit:
                self._audit(
                    actor,
                    "cdot-live.evaluate",
                    {
                        "proposal_id": self._proposal["proposal_id"],
                        "status": plan.status,
                        "hottest": self._proposal["summary"],
                    },
                )
            await self._emit("pipeline.proposal", self._proposal["summary"])
            self._last_error = None
            return self.snapshot()

    def _fit_and_solve(self, cube: DemandCube) -> tuple[CdotForecaster, AllocationPlan]:
        """Blocking half of evaluate -- runs in a worker thread."""
        forecaster = CdotForecaster.fit(
            cube,
            horizon=self.config.cadence.horizon_steps,
            carry_over=self._forecaster,
        )
        plan = solve_allocation_joint(
            cube,
            forecaster.predict(cube),
            self.config,
            issued_at=cube.latest_time,
            previous_policy=self._plan.policy if self._plan else None,
        )
        return forecaster, plan

    def _forecast_payload(self, forecaster: CdotForecaster, cube: DemandCube) -> dict[str, Any]:
        predictions = forecaster.predict(cube)
        rows = []
        for selection_id, per_direction in sorted(predictions.items()):
            dnn, tac = parse_group_id(selection_id)
            rows.append(
                {
                    "selection_id": selection_id,
                    "dnn": dnn,
                    "tac": tac,
                    "ul": per_direction["ul"].as_dict(),
                    "dl": per_direction["dl"].as_dict(),
                    "total_p50": per_direction["ul"].p50 + per_direction["dl"].p50,
                }
            )
        return {
            "model": forecaster.summary(),
            "issued_at": _iso(cube.latest_time) if cube.latest_time else _now(),
            "target_seconds_ahead": self.config.cadence.forecast_horizon_seconds,
            "unit": "pps",
            "rows": rows,
        }

    def _build_proposal(
        self,
        plan: AllocationPlan,
        cube: DemandCube,
        smf_rows: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        observed = cube.current_weights()
        rows: list[dict[str, Any]] = []
        for selection_id, weights in sorted(plan.integer_weights.items()):
            dnn, tac = parse_group_id(selection_id)
            proposed = {self.config.smf_name(upf): value for upf, value in weights.items()}
            existing = smf_rows.get(selection_id)
            current = extract_weights(existing) if existing else {}
            template = existing or {"dnn": dnn, "tac": tac}
            rows.append(
                {
                    "selection_id": selection_id,
                    "dnn": dnn,
                    "tac": tac,
                    "observed_share": observed.get(selection_id, {}),
                    "current_weights": current,
                    "proposed_weights": proposed,
                    "changed": current != proposed,
                    "outgoing_json": with_weights(template, proposed),
                    # Every tuple in a joint solve is only meaningful alongside
                    # the others, so readiness is a property of the batch.
                    "actuation_ready": bool(proposed),
                }
            )
        hot_upf, hot_pps = plan.hottest("projected")
        base_upf, base_pps = plan.hottest("baseline")
        capacity = self.config.capacity
        return {
            "proposal_id": f"cdot-{self._sequence + 1}-{int(self._now_trace().timestamp())}",
            "created_at": _now(),
            "status": plan.status,
            "message": plan.message,
            "base_smf_state_hash": self._smf_hash,
            "unit": "pps",
            "summary": {
                "hottest_projected": {"upf": hot_upf, "pps": round(hot_pps, 1)},
                "hottest_baseline": {"upf": base_upf, "pps": round(base_pps, 1)},
                "peak_reduction": (
                    round(1.0 - hot_pps / base_pps, 4) if base_pps > 0 else None
                ),
                "capacity_pps": capacity.per_upf_pps,
                "baseline_overloaded": base_pps > capacity.per_upf_pps,
                "projected_overloaded": hot_pps > capacity.per_upf_pps,
                "max_safe_utilization": plan.max_safe_utilization,
                "solver_runtime_ms": plan.solver_runtime_ms,
            },
            "projected_load_pps": plan.projected_load_pps,
            "baseline_load_pps": plan.baseline_load_pps,
            "eligibility": {str(k): v for k, v in plan.eligibility.items()},
            "rows": rows,
            "actuation_ready": any(row["changed"] for row in rows) and plan.policy is not None,
        }

    def freeze_proposal(self, frozen: bool = True) -> None:
        """Hold the current proposal steady while a presenter reviews it."""
        self._proposal_frozen = frozen

    def _state_rows(self, state: Any) -> dict[str, dict[str, Any]]:
        return {tuple_key(item): item for item in extract_tuples(state)}

    async def apply(self, proposal_id: str, expected_hash: str, confirmation: bool, *, actor: str) -> dict[str, Any]:
        if not confirmation:
            raise LiveRejected("explicit presenter confirmation is required")
        async with self._lock:
            proposal = self._proposal
            if proposal is None or proposal.get("proposal_id") != proposal_id:
                raise LiveConflict("proposal is missing, expired, or has been replaced")
            if not proposal.get("actuation_ready"):
                raise LiveRejected("proposal has not passed every actuation guardrail")
            initial = await self.smf.get_state()
            initial_hash = canonical_state_hash(initial)
            if expected_hash != initial_hash or proposal.get("base_smf_state_hash") != initial_hash:
                self._audit(actor, "cdot-live.apply_rejected", {"reason": "smf_state_changed", "observed_hash": initial_hash})
                raise LiveConflict("SMF state changed after proposal evaluation")
            before = copy.deepcopy(initial)
            working = initial
            changed = [row for row in proposal["rows"] if row["actuation_ready"] and row["current_weights"] != row["proposed_weights"]]
            if not changed:
                raise LiveRejected("proposal contains no SMF weight change")
            posted: list[str] = []
            try:
                # Required read-before-write optimistic concurrency gate.
                observed = await self.smf.get_state()
                if canonical_state_hash(observed) != canonical_state_hash(working):
                    raise LiveConflict("concurrent SMF-state change detected before POST")
                # One array POST for the whole batch.  A joint solve's tuples are
                # only correct together -- applying them one at a time walks the
                # network through allocations nobody chose.
                await self.smf.post_tuples([row["outgoing_json"] for row in changed])
                posted = [row["selection_id"] for row in changed]
                verified = await self.smf.get_state()
                verified_rows = self._state_rows(verified)
                for row in changed:
                    item = verified_rows.get(row["selection_id"])
                    if item is None or extract_weights(item) != row["proposed_weights"]:
                        raise UpstreamFailure(
                            f"GET verification mismatch for {row['selection_id']}"
                        )
                working = verified
            except Exception as error:
                # Preserve an exact rollback target even when only a prefix of
                # the requested tuple set reached the SMF.
                try:
                    observed_after_failure = await self.smf.get_state()
                    working = observed_after_failure
                except Exception:
                    pass
                if posted:
                    self._applications.append({
                        "application_id": f"partial-{proposal_id}", "proposal_id": proposal_id,
                        "actor": actor, "applied_at": _now(), "pre_state": before,
                        "pre_state_hash": initial_hash, "post_state": copy.deepcopy(working),
                        "post_state_hash": canonical_state_hash(working),
                        "changed_selection_ids": list(posted),
                        "outgoing": [copy.deepcopy(row["outgoing_json"]) for row in changed if row["selection_id"] in posted],
                        "partial": True,
                    })
                self._smf_state, self._smf_hash = working, canonical_state_hash(working)
                self._verification = {"status": "failed", "verified": False, "posted": posted, "error": str(error), "at": _now()}
                self._audit(actor, "cdot-live.apply_failed", self._verification)
                await self._emit("smf.apply_failed", self._verification)
                raise UpstreamFailure(f"SMF application failed after {len(posted)} changed tuple(s): {error}") from error
            after_hash = canonical_state_hash(working)
            application = {
                "application_id": f"apply-{proposal_id}", "proposal_id": proposal_id,
                "actor": actor, "applied_at": _now(), "pre_state": before,
                "pre_state_hash": initial_hash, "post_state": copy.deepcopy(working),
                "post_state_hash": after_hash, "changed_selection_ids": posted,
                "outgoing": [copy.deepcopy(row["outgoing_json"]) for row in changed],
            }
            self._applications.append(application)
            self._smf_state, self._smf_hash = working, after_hash
            self._verification = {"status": "verified", "verified": True, "posted": posted, "state_hash": after_hash, "at": _now()}
            self._audit(actor, "cdot-live.apply_verified", {key: value for key, value in application.items() if key not in {"pre_state", "post_state"}})
            await self._emit("smf.apply_verified", self._verification)
            return self.snapshot()

    async def rollback(self, application_id: str, expected_hash: str, confirmation: bool, *, actor: str) -> dict[str, Any]:
        if not confirmation:
            raise LiveRejected("explicit presenter confirmation is required")
        async with self._lock:
            application = next((item for item in reversed(self._applications) if item["application_id"] == application_id), None)
            if application is None:
                raise LiveConflict("unknown application id")
            current = await self.smf.get_state()
            current_hash = canonical_state_hash(current)
            if current_hash != expected_hash or current_hash != application["post_state_hash"]:
                raise LiveConflict("SMF state changed after the selected application")
            pre_rows = self._state_rows(application["pre_state"])
            current_rows = self._state_rows(current)
            working = current
            restored = []
            try:
                observed = await self.smf.get_state()
                if canonical_state_hash(observed) != canonical_state_hash(working):
                    raise LiveConflict("concurrent SMF-state change detected before rollback POST")
                payloads = []
                expected: dict[str, dict[str, int]] = {}
                for selection_id in application["changed_selection_ids"]:
                    if selection_id in pre_rows:
                        payload = copy.deepcopy(pre_rows[selection_id])
                        expected[selection_id] = extract_weights(payload)
                    else:
                        # The tuple did not exist before we wrote it: an empty
                        # weight set is how their SMF clears an entry.
                        payload = with_weights(current_rows[selection_id], {})
                        expected[selection_id] = {}
                    payloads.append(payload)
                await self.smf.post_tuples(payloads)
                verified = await self.smf.get_state()
                verified_rows = self._state_rows(verified)
                for selection_id, want in expected.items():
                    actual = extract_weights(verified_rows.get(selection_id) or {})
                    if actual != want:
                        raise UpstreamFailure(
                            f"rollback GET verification mismatch for {selection_id}"
                        )
                    restored.append(selection_id)
                working = verified
            except Exception as error:
                self._verification = {"status": "rollback_failed", "verified": False, "restored": restored, "error": str(error), "at": _now()}
                self._audit(actor, "cdot-live.rollback_failed", self._verification)
                raise UpstreamFailure(str(error)) from error
            self._smf_state, self._smf_hash = working, canonical_state_hash(working)
            self._verification = {"status": "rollback_verified", "verified": True, "restored": restored,
                                  "state_hash": self._smf_hash, "at": _now()}
            self._applications.remove(application)
            self._audit(actor, "cdot-live.rollback_verified", self._verification)
            await self._emit("smf.rollback_verified", self._verification)
            return self.snapshot()
