from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]

CONFIG_SCHEMA = "cdot-live-config/2.0"


class LiveConfigError(RuntimeError):
    """Raised when the C-DOT live configuration is missing or malformed."""


def _flag(env: str, default: bool) -> bool:
    raw = os.environ.get(env)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class UpfMapping:
    metric: str
    smf: str
    job: str
    pod: str


@dataclass(frozen=True, slots=True)
class Cadence:
    telemetry_step_seconds: int = 30
    decision_interval_seconds: int = 60
    forecast_horizon_seconds: int = 600
    history_seconds: int = 10_800
    telemetry_stale_seconds: int = 90
    decision_stale_seconds: int = 120

    @property
    def horizon_steps(self) -> int:
        """Forecast horizon expressed in telemetry steps."""
        return max(1, round(self.forecast_horizon_seconds / self.telemetry_step_seconds))


@dataclass(frozen=True, slots=True)
class Solver:
    planning_quantile: str = "p50"


@dataclass(frozen=True, slots=True)
class WeightBounds:
    min_share: float = 0.05
    max_share: float = 0.75
    max_step_delta_pp: int = 100


@dataclass(frozen=True, slots=True)
class Autopilot:
    """Cadence and guardrails for the unattended closed loop.

    Two clocks, deliberately different.  ``telemetry_poll_seconds`` is how often
    the loop touches Prometheus -- fast, so an outage is visible within one
    scrape rather than one control period.  ``control_interval_seconds`` is how
    often the optimizer runs and weights are written to the SMF -- slow, because
    every write re-steers live PDU-session establishment and C-DOT asked for a
    ten-minute cadence.
    """

    enabled: bool = False
    telemetry_poll_seconds: int = 30
    control_interval_seconds: int = 600
    poll_overlap_seconds: int = 120
    require_fresh_seconds: int = 180
    min_history_seconds: int = 1_800
    unhealthy_after_failures: int = 3
    dry_run: bool = False
    log_file: str | None = None
    log_max_bytes: int = 10_000_000
    log_backups: int = 5
    history_enabled: bool = True
    history_dir: str = "logs/history"
    history_max_bytes: int = 50_000_000
    history_backups: int = 3
    history_telemetry_every_n_polls: int = 1
    poll_log_limit: int = 240
    cycle_log_limit: int = 120


@dataclass(frozen=True, slots=True)
class Capacity:
    per_upf_pps: float
    safe_utilization: float
    confirmed_by_cdot: bool
    source: str

    @property
    def safe_pps(self) -> float:
        return self.per_upf_pps * self.safe_utilization


@dataclass(slots=True)
class LiveConfig:
    prometheus_url: str
    smf_url: str
    timeout_seconds: float
    poll_seconds: float
    capacity: Capacity
    cadence: Cadence
    weight_bounds: WeightBounds
    solver: Solver
    mappings: dict[str, UpfMapping]
    dnn: dict[str, str]
    declared_eligibility: dict[int, list[str]]
    eligibility_mode: str
    permutation: dict[str, str]
    permutation_enabled: bool
    queries: dict[str, str]
    source_mode: str
    replay_root: str
    replay_timezone: str
    upf_identity_confirmed: bool
    queries_confirmed: bool
    traffic_unit: str = "pps"
    autopilot: Autopilot = field(default_factory=Autopilot)
    config_path: str = ""
    load_error: str | None = None

    # ------------------------------------------------------------------ load

    @classmethod
    def from_env(cls) -> "LiveConfig":
        """Load the live config, degrading to a usable default on any failure.

        A missing or malformed file must not take down the whole FastAPI app --
        ``create_app`` constructs the service at import time.  The failure is
        recorded in ``load_error`` and surfaced through the status payload.
        """
        path = Path(os.environ.get("CDOT_LIVE_CONFIG", ROOT / "configs" / "cdot_live.json"))
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("schema_version") != CONFIG_SCHEMA:
                raise LiveConfigError(
                    f"expected {CONFIG_SCHEMA}, got {raw.get('schema_version')!r}"
                )
            return cls._build(raw, path, None)
        except (OSError, ValueError, KeyError, LiveConfigError) as error:
            return cls._build(_FALLBACK, path, f"{type(error).__name__}: {error}")

    @classmethod
    def _build(cls, raw: dict[str, Any], path: Path, load_error: str | None) -> "LiveConfig":
        for name, env in (("upfs", "CDOT_LIVE_UPF_MAPPING"), ("queries", "CDOT_LIVE_QUERIES")):
            override = os.environ.get(env)
            if override:
                for key, value in json.loads(override).items():
                    if isinstance(value, dict):
                        raw.setdefault(name, {}).setdefault(key, {}).update(value)
                    else:
                        raw.setdefault(name, {})[key] = value

        source_raw_early = dict(raw.get("source", {}))
        source_mode = os.environ.get(
            "CDOT_LIVE_SOURCE", str(source_raw_early.get("mode", "replay"))
        )
        # The two sources do not carry the same quantity.  The recorded Grafana
        # export is packets/second; the only per-class series C-DOT's Prometheus
        # publishes live is a *byte* counter, despite "packets" in its name.  So
        # the unit -- and therefore the capacity line the LP is bounded by --
        # follows the source, and nothing downstream may hardcode "pps".
        units_raw = dict(raw.get("traffic_units", {}))
        traffic_unit = str(units_raw.get(source_mode, units_raw.get("replay", "pps")))

        capacity_raw = dict(raw.get("capacity", {}))
        default_capacity = (
            capacity_raw.get("per_upf_bytes_per_second", 1.0e9)
            if source_mode == "prometheus"
            else capacity_raw.get("per_upf_pps", 70_000.0)
        )
        capacity = Capacity(
            per_upf_pps=float(os.environ.get("CDOT_LIVE_CAPACITY_PPS", default_capacity)),
            safe_utilization=float(
                os.environ.get("CDOT_LIVE_SAFE_UTILIZATION", capacity_raw.get("safe_utilization", 0.8))
            ),
            confirmed_by_cdot=bool(capacity_raw.get("confirmed_by_cdot", False)),
            source=str(capacity_raw.get(
                "source_bytes_per_second" if source_mode == "prometheus" else "source",
                capacity_raw.get("source", "unspecified"),
            )),
        )

        cadence_raw = dict(raw.get("cadence", {}))
        cadence = Cadence(
            telemetry_step_seconds=int(cadence_raw.get("telemetry_step_seconds", 30)),
            decision_interval_seconds=int(
                os.environ.get("CDOT_LIVE_DECISION_SECONDS", cadence_raw.get("decision_interval_seconds", 60))
            ),
            forecast_horizon_seconds=int(cadence_raw.get("forecast_horizon_seconds", 600)),
            history_seconds=int(
                os.environ.get("CDOT_LIVE_HISTORY_SECONDS", cadence_raw.get("history_seconds", 10_800))
            ),
            telemetry_stale_seconds=int(cadence_raw.get("telemetry_stale_seconds", 90)),
            decision_stale_seconds=int(cadence_raw.get("decision_stale_seconds", 120)),
        )

        bounds_raw = dict(raw.get("weight_bounds", {}))
        weight_bounds = WeightBounds(
            min_share=float(bounds_raw.get("min_share", 0.05)),
            max_share=float(bounds_raw.get("max_share", 0.75)),
            max_step_delta_pp=int(bounds_raw.get("max_step_delta_pp", 100)),
        )

        solver = Solver(
            planning_quantile=str(raw.get("solver", {}).get("planning_quantile", "p50"))
        )

        mappings = {
            metric: UpfMapping(
                metric=metric,
                smf=str(item["smf"]),
                job=str(item.get("job", "")),
                pod=str(item.get("pod", "")),
            )
            for metric, item in raw["upfs"].items()
        }

        eligibility_raw = dict(raw.get("eligibility", {}))
        declared = {
            int(tac): [str(upf) for upf in upfs]
            for tac, upfs in eligibility_raw.get("declared", {}).items()
        }
        permutation_raw = dict(raw.get("class_label_permutation", {}))
        source_raw = source_raw_early
        autopilot_raw = dict(raw.get("autopilot", {}))
        autopilot = Autopilot(
            enabled=_flag("CDOT_LIVE_AUTOPILOT", autopilot_raw.get("enabled", False)),
            telemetry_poll_seconds=int(os.environ.get(
                "CDOT_LIVE_AUTOPILOT_POLL_SECONDS",
                autopilot_raw.get("telemetry_poll_seconds", cadence.telemetry_step_seconds),
            )),
            control_interval_seconds=int(os.environ.get(
                "CDOT_LIVE_AUTOPILOT_CONTROL_SECONDS",
                autopilot_raw.get("control_interval_seconds", 600),
            )),
            poll_overlap_seconds=int(autopilot_raw.get("poll_overlap_seconds", 120)),
            require_fresh_seconds=int(autopilot_raw.get("require_fresh_seconds", 180)),
            min_history_seconds=int(autopilot_raw.get("min_history_seconds", 1_800)),
            unhealthy_after_failures=int(autopilot_raw.get("unhealthy_after_failures", 3)),
            dry_run=_flag("CDOT_LIVE_AUTOPILOT_DRY_RUN", autopilot_raw.get("dry_run", False)),
            log_file=os.environ.get("CDOT_LIVE_LOG_FILE", autopilot_raw.get("log_file")) or None,
            log_max_bytes=int(autopilot_raw.get("log_max_bytes", 10_000_000)),
            log_backups=int(autopilot_raw.get("log_backups", 5)),
            history_enabled=_flag(
                "CDOT_LIVE_HISTORY", autopilot_raw.get("history_enabled", True)
            ),
            history_dir=os.environ.get(
                "CDOT_LIVE_HISTORY_DIR", autopilot_raw.get("history_dir", "logs/history")
            ),
            history_max_bytes=int(autopilot_raw.get("history_max_bytes", 50_000_000)),
            history_backups=int(autopilot_raw.get("history_backups", 3)),
            history_telemetry_every_n_polls=int(
                autopilot_raw.get("history_telemetry_every_n_polls", 1)
            ),
            poll_log_limit=int(autopilot_raw.get("poll_log_limit", 240)),
            cycle_log_limit=int(autopilot_raw.get("cycle_log_limit", 120)),
        )

        return cls(
            prometheus_url=os.environ.get("CDOT_PROMETHEUS_URL", "http://192.168.218.8:29090").rstrip("/"),
            smf_url=os.environ.get("CDOT_SMF_URL", "http://192.168.218.8:30956").rstrip("/"),
            timeout_seconds=float(os.environ.get("CDOT_LIVE_TIMEOUT_SECONDS", "15")),
            poll_seconds=float(
                os.environ.get("CDOT_LIVE_POLL_SECONDS", cadence.decision_interval_seconds)
            ),
            capacity=capacity,
            cadence=cadence,
            weight_bounds=weight_bounds,
            solver=solver,
            mappings=mappings,
            dnn={str(key): str(value) for key, value in raw.get("dnn", {}).items()},
            declared_eligibility=declared,
            eligibility_mode=os.environ.get(
                "CDOT_LIVE_ELIGIBILITY_MODE", str(eligibility_raw.get("mode", "union"))
            ),
            permutation={str(k): str(v) for k, v in permutation_raw.get("map", {}).items()},
            permutation_enabled=bool(permutation_raw.get("enabled", False)),
            queries={str(key): str(value) for key, value in raw.get("queries", {}).items()},
            source_mode=source_mode,
            replay_root=os.environ.get(
                "CDOT_LIVE_REPLAY_ROOT",
                str(ROOT / source_raw.get("replay_root", "cdot-upf-metrics-v02/metrics")),
            ),
            replay_timezone=str(source_raw.get("replay_timezone", "Asia/Kolkata")),
            upf_identity_confirmed=bool(raw.get("upf_identity_confirmed_by_cdot", False)),
            queries_confirmed=bool(raw.get("queries_confirmed_by_cdot", False)),
            traffic_unit=traffic_unit,
            autopilot=autopilot,
            config_path=str(path),
            load_error=load_error,
        )

    # ---------------------------------------------------------------- helpers

    @property
    def upf_ids(self) -> list[str]:
        return sorted(self.mappings)

    def smf_name(self, upf: str) -> str:
        return self.mappings[upf].smf

    def upf_for_smf_name(self, name: str) -> str | None:
        for metric, mapping in self.mappings.items():
            if mapping.smf == name:
                return metric
        return None

    def apply_permutation(self, upf: str) -> str:
        if not self.permutation_enabled:
            return upf
        return self.permutation.get(upf, upf)

    def eligibility(self, observed: dict[int, set[str]] | None = None) -> dict[int, list[str]]:
        """Resolve TAC -> eligible UPFs under the configured mode.

        ``declared`` follows the constraint CSV literally; ``observed`` uses only
        UPF/TAC pairs seen carrying traffic; ``union`` takes both.  Their trace
        contradicts the CSV, so ``union`` keeps the current routing feasible.
        """
        observed = observed or {}
        tacs = set(self.declared_eligibility) | set(observed)
        result: dict[int, list[str]] = {}
        for tac in sorted(tacs):
            declared = set(self.declared_eligibility.get(tac, ()))
            seen = set(observed.get(tac, ()))
            if self.eligibility_mode == "declared":
                allowed = declared
            elif self.eligibility_mode == "observed":
                allowed = seen
            else:
                allowed = declared | seen
            allowed &= set(self.mappings)
            if allowed:
                result[tac] = sorted(allowed)
        return result

    def eligibility_provenance(self, observed: dict[int, set[str]] | None = None) -> list[dict[str, Any]]:
        """Per (tac, upf) record of why it is eligible -- shown in the console."""
        observed = observed or {}
        rows = []
        for tac, upfs in self.eligibility(observed).items():
            for upf in upfs:
                declared = upf in set(self.declared_eligibility.get(tac, ()))
                seen = upf in set(observed.get(tac, ()))
                rows.append({
                    "tac": tac,
                    "upf": upf,
                    "declared": declared,
                    "observed": seen,
                    "basis": "declared" if declared else "observed-only (contradicts constraint CSV)",
                })
        return rows

    def status(self) -> dict[str, Any]:
        return {
            "config_path": self.config_path,
            "load_error": self.load_error,
            "units": self.traffic_unit,
            "capacity": {
                "per_upf_pps": self.capacity.per_upf_pps,
                "safe_utilization": self.capacity.safe_utilization,
                "safe_pps": self.capacity.safe_pps,
                "confirmed_by_cdot": self.capacity.confirmed_by_cdot,
                "source": self.capacity.source,
                "unit": self.traffic_unit,
            },
            "cadence": {
                "telemetry_step_seconds": self.cadence.telemetry_step_seconds,
                "decision_interval_seconds": self.cadence.decision_interval_seconds,
                "forecast_horizon_seconds": self.cadence.forecast_horizon_seconds,
                "history_seconds": self.cadence.history_seconds,
            },
            "source_mode": self.source_mode,
            "autopilot": {
                "enabled": self.autopilot.enabled,
                "telemetry_poll_seconds": self.autopilot.telemetry_poll_seconds,
                "control_interval_seconds": self.autopilot.control_interval_seconds,
                "require_fresh_seconds": self.autopilot.require_fresh_seconds,
                "min_history_seconds": self.autopilot.min_history_seconds,
                "dry_run": self.autopilot.dry_run,
            },
            "eligibility_mode": self.eligibility_mode,
            "upf_to_smf": {key: item.smf for key, item in self.mappings.items()},
            "dnn": dict(self.dnn),
            "unconfirmed_assumptions": self.unconfirmed_assumptions(),
        }

    def unconfirmed_assumptions(self) -> list[str]:
        """Everything the demo asserts that C-DOT has not yet confirmed."""
        pending = []
        if not self.capacity.confirmed_by_cdot:
            pending.append(
                f"Per-UPF capacity {self.capacity.per_upf_pps:,.0f} {self.traffic_unit} is a "
                "placeholder, not a C-DOT figure."
            )
        if self.source_mode == "prometheus" and self.traffic_unit != "pps":
            pending.append(
                f"Live telemetry is in {self.traffic_unit}, not packets/second: the only per-class "
                "series C-DOT publishes is a byte counter, so every load figure here is bytes, and "
                "it cannot be compared with the packets/second figures from the recorded trace."
            )
        if not self.upf_identity_confirmed:
            pending.append(
                "UPF identity between Grafana pod panels and upf-1..upf-4 is unconfirmed; "
                "only pod upf-1 matches class label upf-2 in the trace."
            )
        if not self.queries_confirmed:
            pending.append("Prometheus metric names are inferred, not supplied by C-DOT.")
        if self.eligibility_mode == "union":
            pending.append(
                "TAC eligibility is declared-union-observed because the trace carries traffic on "
                "UPF/TAC pairs the constraint CSV forbids."
            )
        return pending


_FALLBACK: dict[str, Any] = {
    "schema_version": CONFIG_SCHEMA,
    "units": "pps",
    "capacity": {"per_upf_pps": 70_000.0, "safe_utilization": 0.8, "confirmed_by_cdot": False,
                 "source": "built-in fallback"},
    "upfs": {
        "upf-1": {"smf": "UPF1", "job": "upf1", "pod": "upf-0"},
        "upf-2": {"smf": "UPF2", "job": "upf2", "pod": "upf1-0"},
        "upf-3": {"smf": "UPF3", "job": "upf3", "pod": "upf2-0"},
        "upf-4": {"smf": "UPF4", "job": "upf4", "pod": "upf3-0"},
    },
    "dnn": {"1": "ims", "2": "internet"},
    "eligibility": {"mode": "union", "declared": {
        "1": ["upf-1", "upf-4"], "2": ["upf-1", "upf-2"],
        "3": ["upf-1", "upf-2", "upf-3"], "4": ["upf-1", "upf-3", "upf-4"],
    }},
    "class_label_permutation": {"enabled": False, "map": {}},
    "cadence": {},
    "weight_bounds": {},
    "source": {"mode": "replay", "replay_root": "cdot-upf-metrics-v02/metrics",
               "replay_timezone": "Asia/Kolkata"},
    "queries": {
        "ul": "upf_class_ul_packets_total", "dl": "upf_class_dl_packets_total",
        "sessions": "pfcp_sessions_total", "cpu": "upf_cpu_usage_cores",
        "memory": "upf_memory_usage_bytes", "tsi": "upf_tsi",
        "drop": "upf_downlink_drop_rate_percent",
        "forwarding_efficiency": "upf_downlink_forwarding_efficiency_percent",
    },
}
