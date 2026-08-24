from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any, Callable

from .adapter import CdotTelemetryAdapter
from .config import LiveConfig
from .forecast import GuardedTransferForecaster
from .optimizer import LiveOptimizer
from .prometheus import PrometheusClient
from .smf import H2CSmfClient, canonical_state_hash, extract_tuples, extract_weights, tuple_key, with_weights


class LiveConflict(RuntimeError):
    pass


class LiveRejected(RuntimeError):
    pass


class UpstreamFailure(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CdotLiveService:
    def __init__(
        self,
        config: LiveConfig | None = None,
        *,
        prometheus: PrometheusClient | None = None,
        smf: H2CSmfClient | None = None,
        audit_callback: Callable[[str, str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.config = config or LiveConfig.from_env()
        self.prometheus = prometheus or PrometheusClient(self.config)
        self.smf = smf or H2CSmfClient(self.config.smf_url, timeout_seconds=self.config.timeout_seconds)
        self.adapter = CdotTelemetryAdapter(self.config)
        self.forecaster = GuardedTransferForecaster(self.config)
        self.optimizer = LiveOptimizer(self.config)
        self.audit_callback = audit_callback
        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._poll_task: asyncio.Task[None] | None = None
        self._sequence = 0
        self._history: list[dict[str, Any]] = []
        self._operational: dict[str, dict[str, Any]] = {}
        self._forecast: dict[str, Any] | None = None
        self._proposal: dict[str, Any] | None = None
        self._smf_state: Any = None
        self._smf_hash: str | None = None
        self._last_error: str | None = None
        self._last_poll: str | None = None
        self._prometheus_ready: bool | None = None
        self._smf_ready: bool | None = None
        self._audits: list[dict[str, Any]] = []
        self._applications: list[dict[str, Any]] = []
        self._verification: dict[str, Any] | None = None
        self._stage = "idle"

    def _audit(self, actor: str, action: str, payload: dict[str, Any]) -> None:
        event = {
            "id": len(self._audits) + 1, "wall_time": _now(), "actor": actor,
            "action": action, "payload": copy.deepcopy(payload),
        }
        self._audits.append(event)
        if self.audit_callback:
            self.audit_callback(actor, action, payload)

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self._sequence += 1
        event = {"schema_version": "cdot-live-stream/1.0", "sequence": self._sequence,
                 "wall_time": _now(), "type": event_type, "payload": payload}
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

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.evaluate(actor="system-poller", audit=False)
            except Exception as error:  # isolated external degradation
                self._last_error = str(error)
                self._stage = "degraded"
                await self._emit("pipeline.degraded", {"error": str(error)})
            await asyncio.sleep(self.config.poll_seconds)

    async def refresh_status(self) -> dict[str, Any]:
        async def smf_read() -> tuple[bool, Any, str | None, str | None]:
            try:
                state = await self.smf.get_state()
                return True, state, canonical_state_hash(state), None
            except Exception as error:
                return False, None, None, str(error)

        prometheus_result, smf_result = await asyncio.gather(self.prometheus.ready(), smf_read())
        self._prometheus_ready = bool(prometheus_result)
        self._smf_ready, state, state_hash, smf_error = smf_result
        if state_hash is not None:
            self._smf_state, self._smf_hash = state, state_hash
        if smf_error:
            self._last_error = smf_error
        elif self._prometheus_ready:
            self._last_error = None
        return self.status()

    def status(self) -> dict[str, Any]:
        latest = self._history[-1] if self._history else None
        freshness = None
        if latest:
            end = datetime.fromisoformat(latest["end"].replace("Z", "+00:00"))
            freshness = max(0.0, (datetime.now(timezone.utc) - end).total_seconds())
        healthy = self._prometheus_ready is True and self._smf_ready is True
        return {
            "schema_version": "cdot-live-status/1.0",
            "mode": "live_external_data", "isolated_from_synthetic": True,
            "status": "healthy" if healthy else "degraded" if self._last_error else "connecting",
            "stage": self._stage,
            "endpoints": {
                "prometheus": {"url": self.config.prometheus_url, "ready": self._prometheus_ready},
                "smf": {"url": self.config.smf_url, "ready": self._smf_ready, "protocol": "h2c-prior-knowledge"},
            },
            "freshness": {"latest_closed_bucket_age_seconds": freshness, "stale_after_seconds": self.config.stale_seconds,
                          "fresh": freshness is not None and freshness <= self.config.stale_seconds},
            "mapping": self.config.mapping_status(),
            "calibration": {"status": self.config.calibration_status, "unit": self.config.units,
                            "production_safety_claim": False},
            "current_smf_state_hash": self._smf_hash,
            "last_poll": self._last_poll, "last_error": self._last_error,
        }

    def snapshot(self) -> dict[str, Any]:
        latest_load = {upf: {"ul": 0.0, "dl": 0.0} for upf in self.config.mappings}
        if self._history:
            for row in self._history[-1].get("tuples", []):
                if row["upf"] in latest_load:
                    latest_load[row["upf"]]["ul"] += float(row["ul_rate"])
                    latest_load[row["upf"]]["dl"] += float(row["dl_rate"])
        upfs = []
        for metric, mapping in self.config.mappings.items():
            observed = latest_load[metric]
            state = self._operational.get(metric, {})
            upfs.append({
                **state, "upf": metric, "smf": mapping.smf,
                "observed": observed,
                "proxy_safe_limit": {"ul": mapping.ul_limit, "dl": mapping.dl_limit},
                "utilization": {"ul": observed["ul"] / mapping.ul_limit, "dl": observed["dl"] / mapping.dl_limit},
                "headroom": {"ul": mapping.ul_limit - observed["ul"], "dl": mapping.dl_limit - observed["dl"]},
                "unit": self.config.units, "calibration": self.config.calibration_status,
            })
        return {
            "schema_version": "cdot-live-snapshot/1.0", "sequence": self._sequence,
            "wall_time": _now(), "status": self.status(),
            "pipeline": {
                "stage": self._stage,
                "stages": ["prometheus_read", "bucket_closed", "forecast", "highs_optimization", "presenter_review", "smf_apply", "get_verify"],
                "watermark_seconds": self.config.watermark_seconds,
                "bucket_seconds": self.config.bucket_seconds,
            },
            "units": {"traffic": self.config.units, "label": "UNCALIBRATED PROXY", "mbps": False},
            "telemetry": {"buckets": copy.deepcopy(self._history[-144:]), "upfs": upfs},
            "forecast": copy.deepcopy(self._forecast), "proposal": copy.deepcopy(self._proposal),
            "smf": {"state": copy.deepcopy(self._smf_state), "state_hash": self._smf_hash,
                    "verification": copy.deepcopy(self._verification)},
            "audit_events": copy.deepcopy(self._audits[-200:]),
            "rollback": {"available": bool(self._applications),
                         "application_id": self._applications[-1]["application_id"] if self._applications else None},
        }

    async def evaluate(self, *, actor: str, audit: bool = True) -> dict[str, Any]:
        async with self._lock:
            self._stage = "prometheus_read"
            await self._emit("pipeline.stage", {"stage": self._stage})
            try:
                ul_result, dl_result = await self.prometheus.traffic_history()
                operational_task = asyncio.create_task(self.prometheus.operational_state())
                smf_task = asyncio.create_task(self.smf.get_state())
                now = datetime.now(timezone.utc)
                counters_ul = "rate(" not in self.config.queries["ul"]
                counters_dl = "rate(" not in self.config.queries["dl"]
                ul = self.adapter.aggregate_direction_results(ul_result, "ul", now=now, counters=counters_ul)
                dl = self.adapter.aggregate_direction_results(dl_result, "dl", now=now, counters=counters_dl)
                expected = int(self.config.bucket_seconds / 15 * 0.9)
                history = self.adapter.merge_buckets(ul, dl, expected_samples=expected)
                operational, smf_state = await asyncio.gather(operational_task, smf_task)
                if not history:
                    raise UpstreamFailure("Prometheus returned no complete closed ten-minute buckets")
                self._history = history[-144:]
                self._operational = operational
                self._smf_state = smf_state
                self._smf_hash = canonical_state_hash(smf_state)
                self._prometheus_ready = True
                self._smf_ready = True
                self._stage = "bucket_closed"
                await self._emit("telemetry.updated", {"latest": history[-1], "count": len(history)})
                self._stage = "forecast"
                forecast = self.forecaster.forecast(history)
                self._forecast = forecast
                latest_end = datetime.fromisoformat(history[-1]["end"].replace("Z", "+00:00"))
                fresh = (now - latest_end).total_seconds() <= self.config.stale_seconds
                self._stage = "highs_optimization"
                self._proposal = self.optimizer.solve(forecast, operational, smf_state, self._smf_hash,
                                                       telemetry_fresh=fresh)
                self._stage = "presenter_review"
                self._last_error = None
                self._last_poll = _now()
                if audit:
                    self._audit(actor, "cdot-live.evaluate", {"proposal_id": self._proposal["proposal_id"], "read_only": True})
                await self._emit("pipeline.evaluated", {"forecast": forecast, "proposal": self._proposal})
                return self.snapshot()
            except Exception as error:
                self._stage = "degraded"
                self._last_error = str(error)
                self._last_poll = _now()
                self._prometheus_ready = False if not self._history else self._prometheus_ready
                if audit:
                    self._audit(actor, "cdot-live.evaluate_failed", {"error": str(error), "read_only": True})
                await self._emit("pipeline.degraded", {"error": str(error)})
                return self.snapshot()

    def _state_rows(self, state: Any) -> dict[str, dict[str, Any]]:
        return {self.optimizer._selection_key(item): item for item in extract_tuples(state)}

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
                for row in changed:
                    # Required read-before-every-write optimistic concurrency gate.
                    observed = await self.smf.get_state()
                    if canonical_state_hash(observed) != canonical_state_hash(working):
                        raise LiveConflict("concurrent SMF-state change detected before POST")
                    await self.smf.post_tuple(row["outgoing_json"])
                    posted.append(row["selection_id"])
                    verified = await self.smf.get_state()
                    verified_item = self._state_rows(verified).get(row["selection_id"])
                    if verified_item is None or extract_weights(verified_item) != row["proposed_weights"]:
                        raise UpstreamFailure(f"GET verification mismatch for {row['selection_id']}")
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
                for selection_id in application["changed_selection_ids"]:
                    observed = await self.smf.get_state()
                    if canonical_state_hash(observed) != canonical_state_hash(working):
                        raise LiveConflict("concurrent SMF-state change detected before rollback POST")
                    if selection_id in pre_rows:
                        payload = copy.deepcopy(pre_rows[selection_id])
                        expected_weights = extract_weights(payload)
                    else:
                        payload = with_weights(current_rows[selection_id], {})
                        expected_weights = {}
                    await self.smf.post_tuple(payload)
                    verified = await self.smf.get_state()
                    actual_item = self._state_rows(verified).get(selection_id)
                    actual_weights = extract_weights(actual_item or {})
                    if actual_weights != expected_weights:
                        raise UpstreamFailure(f"rollback GET verification mismatch for {selection_id}")
                    working = verified
                    restored.append(selection_id)
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
