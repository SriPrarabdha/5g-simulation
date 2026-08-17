from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from schemas import (
    Capacity,
    ConstraintSlack,
    ExistingLoad,
    Fallback,
    Forecast,
    GroupKey,
    Policy,
    PolicyGroup,
    Quantiles,
    SolverReport,
    TimeWindow,
    UPFState,
)
from steering.policy import PolicyValidationError, ValidationConfig, validate_policy


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = ROOT / "configs" / "demo_mpc_scenario.json"
WINDOW_SECONDS = 600
CONTROLLERS = ("static", "reactive", "cohort-mpc")
RISKS = ("p50", "p90")


@dataclass(frozen=True, slots=True)
class WorkshopEvent:
    group_id: str
    group_label: str
    surge_multiplier: float
    start_window: int = 12
    duration_windows: int = 4
    synthetic: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CertificationResult:
    status: str
    message: str
    requested_weights: dict[str, float]
    applied_weights: dict[str, float]
    fallback_used: bool
    fallback_reason: str | None
    policy_id: str
    existing_sessions_anchored: bool
    projected_ul_mbps_by_upf: dict[str, float]

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"accepted": self.accepted}


@dataclass(frozen=True, slots=True)
class WorkshopDecision:
    schema_version: str
    team_id: str
    selected_event: dict[str, Any]
    controller: str
    forecast_risk: str
    expected_outcome: dict[str, Any]
    explanation: str
    policy_status: str
    fallback_used: bool
    synthetic: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scenario() -> dict[str, Any]:
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))


def _group_id(group: Mapping[str, Any]) -> str:
    key = group["key"]
    return f"{key['zone']}|{key['dnn']}|{key['snssai']}"


def group_options() -> dict[str, str]:
    """Return stable group IDs and readable workshop labels."""
    result: dict[str, str] = {}
    for group in _scenario()["groups"]:
        key = group["key"]
        result[_group_id(group)] = f"{key['zone'].title()} · {key['dnn']} · S-NSSAI {key['snssai']}"
    return result


def create_traffic_event(
    group_id: str,
    surge_multiplier: float,
    *,
    start_window: int = 12,
    duration_windows: int = 4,
) -> WorkshopEvent:
    """Validate the participant's event without altering prior history."""
    options = group_options()
    if group_id not in options:
        raise ValueError(f"unknown traffic group {group_id!r}; choose one of {sorted(options)}")
    if not math.isfinite(surge_multiplier) or not 1.25 <= surge_multiplier <= 8.0:
        raise ValueError("surge_multiplier must be finite and between 1.25 and 8.0")
    if start_window < 6:
        raise ValueError("start_window must leave at least six closed history windows")
    if duration_windows < 1:
        raise ValueError("duration_windows must be positive")
    return WorkshopEvent(
        group_id=group_id,
        group_label=options[group_id],
        surge_multiplier=float(surge_multiplier),
        start_window=start_window,
        duration_windows=duration_windows,
    )


def _selected_group(event: WorkshopEvent) -> dict[str, Any]:
    return next(group for group in _scenario()["groups"] if _group_id(group) == event.group_id)


def simulate_event(event: WorkshopEvent, *, windows: int = 20) -> list[dict[str, Any]]:
    """Create a deterministic demand trace with explicit traffic semantics.

    This is a small teaching trace, not the full macro simulator. It preserves the
    same scenario group parameters and keeps offered demand independent of what the
    network can carry.
    """
    if windows <= event.start_window + event.duration_windows:
        raise ValueError("windows must include history, the event, and a recovery window")
    group = _selected_group(event)
    start = datetime.fromisoformat(_scenario()["start_time"].replace("Z", "+00:00"))
    baseline = (
        float(group["arrivals_per_step"])
        * 20
        * float(group["offered_mbps_per_session"]["ul"])
    )
    # The teaching trace gives the selected class a bounded portion of shared safe
    # capacity; other active classes consume the rest.
    class_carrying_limit = max(baseline * 1.7, baseline + 28.0)
    shape = (0.92, 0.96, 1.00, 1.04, 1.08, 1.02, 0.98, 1.03)
    rows: list[dict[str, Any]] = []
    for index in range(windows):
        factor = shape[index % len(shape)]
        # A visible but causal pre-event buildup; the surge itself starts only at
        # start_window and therefore never appears in forecast features.
        if event.start_window - 3 <= index < event.start_window:
            factor *= 1.0 + 0.06 * (index - (event.start_window - 4))
        in_event = event.start_window <= index < event.start_window + event.duration_windows
        if in_event:
            factor *= event.surge_multiplier
        offered = baseline * factor
        carried = min(offered, class_carrying_limit)
        overload = max(0.0, offered - class_carrying_limit)
        rows.append({
            "window": index,
            "start": (start + timedelta(seconds=index * WINDOW_SECONDS)).isoformat().replace("+00:00", "Z"),
            "offered_ul_mbps": round(offered, 3),
            "carried_ul_mbps": round(carried, 3),
            "overload_ul_mbps": round(overload, 3),
            "loss_ul_mbps": round(max(0.0, offered - carried), 3),
            "event_active": in_event,
        })
    return rows


def traffic_plot(rows: Sequence[Mapping[str, Any]], *, width: int = 900, height: int = 300) -> str:
    """Return a dependency-free, notebook-safe SVG of offered and carried traffic."""
    if not rows:
        raise ValueError("rows are required")
    margin_left, margin_right, margin_top, margin_bottom = 58, 24, 24, 42
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    maximum = max(float(row["offered_ul_mbps"]) for row in rows) * 1.08

    def point(index: int, value: float) -> tuple[float, float]:
        x = margin_left + plot_w * index / max(1, len(rows) - 1)
        y = margin_top + plot_h * (1.0 - value / maximum)
        return x, y

    offered = " ".join(f"{x:.1f},{y:.1f}" for x, y in (
        point(i, float(row["offered_ul_mbps"])) for i, row in enumerate(rows)
    ))
    carried = " ".join(f"{x:.1f},{y:.1f}" for x, y in (
        point(i, float(row["carried_ul_mbps"])) for i, row in enumerate(rows)
    ))
    event_indices = [i for i, row in enumerate(rows) if row.get("event_active")]
    event_rect = ""
    if event_indices:
        x0, _ = point(min(event_indices), 0)
        x1, _ = point(max(event_indices) + 0.8, 0)
        event_rect = (
            f'<rect x="{x0:.1f}" y="{margin_top}" width="{x1 - x0:.1f}" height="{plot_h}" '
            'fill="#f4b942" opacity="0.12"/><text x="{:.1f}" y="{}" fill="#9b6a10" '
            'font-size="11" font-family="IBM Plex Mono, monospace">SURGE</text>'
        ).format(x0 + 8, margin_top + 15)
    grid = []
    for tick in range(5):
        value = maximum * tick / 4
        _, y = point(0, value)
        grid.append(
            f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width-margin_right}" y2="{y:.1f}" '
            f'stroke="#d7e1e5" stroke-width="1"/><text x="{margin_left-8}" y="{y+4:.1f}" '
            f'text-anchor="end" fill="#62737a" font-size="10">{value:.0f}</text>'
        )
    return f"""<svg viewBox="0 0 {width} {height}" role="img" aria-label="Offered and carried uplink traffic" style="max-width:100%;background:#f7fafb;border:1px solid #d7e1e5;border-radius:4px">
<rect width="{width}" height="{height}" fill="#f7fafb"/>{''.join(grid)}{event_rect}
<polyline points="{offered}" fill="none" stroke="#753bbd" stroke-width="3" stroke-linejoin="round"/>
<polyline points="{carried}" fill="none" stroke="#087f8c" stroke-width="3" stroke-linejoin="round"/>
<text x="{margin_left}" y="{height-13}" fill="#394b52" font-size="11">closed 10-minute windows →</text>
<text x="16" y="{margin_top+plot_h/2:.1f}" fill="#394b52" font-size="11" transform="rotate(-90 16 {margin_top+plot_h/2:.1f})">UL Mbps</text>
<line x1="{width-250}" y1="17" x2="{width-228}" y2="17" stroke="#753bbd" stroke-width="3"/><text x="{width-222}" y="21" fill="#394b52" font-size="11">offered demand</text>
<line x1="{width-128}" y1="17" x2="{width-106}" y2="17" stroke="#087f8c" stroke-width="3"/><text x="{width-100}" y="21" fill="#394b52" font-size="11">carried</text>
</svg>"""


def causal_ma_forecast(
    rows: Sequence[Mapping[str, Any]],
    event: WorkshopEvent,
    *,
    planning_risk: str = "p90",
    history_windows: int = 6,
) -> Forecast:
    """Build the real forecast/1.0 contract from closed history only."""
    if planning_risk not in RISKS:
        raise ValueError(f"planning_risk must be one of {RISKS}")
    if history_windows != 6:
        raise ValueError("the workshop checkpoint uses an exact six-window moving average")
    history = [row for row in rows if int(row["window"]) < event.start_window]
    if len(history) < history_windows:
        raise ValueError("six closed windows are required before the target")
    features = history[-history_windows:]
    if any(int(row["window"]) >= event.start_window for row in features):
        raise ValueError("forecast leakage: a feature overlaps the target window")
    p50_ul = sum(float(row["offered_ul_mbps"]) for row in features) / history_windows
    p90_ul = p50_ul * 1.20
    p95_ul = p50_ul * 1.32
    group = _selected_group(event)
    per_session_ul = float(group["offered_mbps_per_session"]["ul"])
    start = datetime.fromisoformat(rows[event.start_window]["start"].replace("Z", "+00:00"))
    target = TimeWindow(start, start + timedelta(seconds=WINDOW_SECONDS))
    key_data = group["key"]
    key = GroupKey(key_data["zone"], key_data["dnn"], key_data["snssai"])
    residual_ul = {"upf-a": 60.0, "upf-b": 80.0, "upf-c": 110.0}
    residual_dl = {"upf-a": 115.0, "upf-b": 105.0, "upf-c": 150.0}
    residual_sessions = {"upf-a": 920.0, "upf-b": 880.0, "upf-c": 1210.0}

    def q(value: float) -> Quantiles:
        return Quantiles(round(value, 3), round(value * 1.32, 3), round(value * 1.20, 3))

    return Forecast(
        forecast_id=f"workshop-ma6-{event.start_window}",
        issued_at=start,
        source_window_end=start,
        target_window=target,
        horizon_steps=1,
        group=key,
        new_session_count=Quantiles(
            round(p50_ul / per_session_ul, 3),
            round(p95_ul / per_session_ul, 3),
            round(p90_ul / per_session_ul, 3),
        ),
        new_load_ul_mbps=Quantiles(round(p50_ul, 3), round(p95_ul, 3), round(p90_ul, 3)),
        new_load_dl_mbps=q(p50_ul * 0.34),
        existing_load_by_upf=[
            ExistingLoad(upf_id, q(residual_sessions[upf_id]), q(residual_ul[upf_id]), q(residual_dl[upf_id]))
            for upf_id in ("upf-a", "upf-b", "upf-c")
        ],
        model_version="moving-average/6-workshop",
        quality_flags=["synthetic_workshop_trace"],
    )


def _upf_states(event: WorkshopEvent, at: datetime) -> list[UPFState]:
    scenario = _scenario()
    eligible = set(_selected_group(event)["eligible_upfs"])
    states: list[UPFState] = []
    for item in scenario["upfs"]:
        upf_id = item["upf_id"]
        states.append(UPFState(
            measurement_time=at,
            upf_id=upf_id,
            capacity_mbps=Capacity(**item["capacity_mbps"]),
            safe_utilization=Capacity(**item["safe_utilization"]),
            session_capacity=int(item["session_capacity"]),
            session_safe_utilization=float(item["session_safe_utilization"]),
            health="healthy",
            zone=item["zone"],
            eligible_groups=[event.group_id] if upf_id in eligible else [],
            path_latency_ms_by_zone=dict(item["path_latency_ms_by_zone"]),
            state_ttl_seconds=WINDOW_SECONDS,
            calibration_version="synthetic-workshop-envelope/1.0",
        ))
    return states


def recommended_weights(controller: str, event: WorkshopEvent, planning_risk: str) -> dict[str, float]:
    if controller not in CONTROLLERS:
        raise ValueError(f"controller must be one of {CONTROLLERS}")
    if planning_risk not in RISKS:
        raise ValueError(f"planning_risk must be one of {RISKS}")
    eligible = set(_selected_group(event)["eligible_upfs"])
    profiles = {
        "static": {"upf-a": 0.45, "upf-b": 0.30, "upf-c": 0.25},
        "reactive": {"upf-a": 0.25, "upf-b": 0.35, "upf-c": 0.40},
        "cohort-mpc": {"upf-a": 0.10, "upf-b": 0.30, "upf-c": 0.60},
    }
    raw = {upf_id: weight for upf_id, weight in profiles[controller].items() if upf_id in eligible}
    total = sum(raw.values())
    if total <= 0:
        raise ValueError("the selected group has no eligible destination")
    return {upf_id: round(weight / total, 10) for upf_id, weight in raw.items()}


def _quantile(value: Quantiles, risk: str) -> float:
    result = getattr(value, risk)
    if result is None:
        raise ValueError(f"forecast does not contain {risk}")
    return float(result)


def _make_policy(
    forecast: Forecast,
    weights: Mapping[str, float],
    states: Sequence[UPFState],
    *,
    planning_risk: str,
    fallback: Fallback | None = None,
    policy_id: str = "workshop-candidate",
) -> Policy:
    state_by_id = {state.upf_id: state for state in states}
    residual_ul = {state.upf_id: 0.0 for state in states}
    residual_dl = {state.upf_id: 0.0 for state in states}
    residual_sessions = {state.upf_id: 0.0 for state in states}
    for load in forecast.existing_load_by_upf:
        residual_ul[load.upf_id] = _quantile(load.ul_mbps, planning_risk)
        residual_dl[load.upf_id] = _quantile(load.dl_mbps, planning_risk)
        residual_sessions[load.upf_id] = _quantile(load.surviving_sessions, planning_risk)
    projected_ul = dict(residual_ul)
    projected_dl = dict(residual_dl)
    projected_sessions = dict(residual_sessions)
    for upf_id, weight in weights.items():
        if upf_id in projected_ul:
            projected_ul[upf_id] += float(weight) * _quantile(forecast.new_load_ul_mbps, planning_risk)
            projected_dl[upf_id] += float(weight) * _quantile(forecast.new_load_dl_mbps, planning_risk)
            projected_sessions[upf_id] += float(weight) * _quantile(forecast.new_session_count, planning_risk)
    slack_ul = {
        upf_id: max(0.0, projected_ul[upf_id] - state.safe_capacity_mbps.ul)
        for upf_id, state in state_by_id.items()
    }
    slack_dl = {
        upf_id: max(0.0, projected_dl[upf_id] - state.safe_capacity_mbps.dl)
        for upf_id, state in state_by_id.items()
    }
    slack_sessions = {
        upf_id: max(0.0, projected_sessions[upf_id] - state.safe_session_capacity)
        for upf_id, state in state_by_id.items()
    }
    has_slack = any(value > 1e-5 for values in (slack_ul, slack_dl, slack_sessions) for value in values.values())
    return Policy(
        policy_id=policy_id,
        policy_version=1,
        created_at=forecast.target_window.start,
        validity=forecast.target_window,
        forecast_id=forecast.forecast_id,
        upf_state_time=forecast.target_window.start,
        solver=SolverReport("workshop-controller", "feasible_with_slack" if has_slack else "optimal", 1),
        constraint_slack=ConstraintSlack(slack_ul, slack_dl, slack_sessions),
        groups=[PolicyGroup(forecast.group, dict(weights))],
        fallback=fallback or Fallback(),
        validator_version="independent-policy-validator/1.0",
    )


def _place_new_sessions(weights: Mapping[str, float], count: int = 24) -> tuple[dict[str, str], dict[str, str]]:
    existing = {f"existing-{index:02d}": ("upf-a", "upf-b", "upf-c")[index % 3] for index in range(18)}
    after = dict(existing)
    ordered = sorted(weights)
    cumulative: list[tuple[float, str]] = []
    total = 0.0
    for upf_id in ordered:
        total += float(weights[upf_id])
        cumulative.append((total, upf_id))
    for index in range(count):
        digest = hashlib.sha256(f"workshop-new-{index}".encode()).digest()
        value = int.from_bytes(digest[:8], "big") / 2**64
        selected = cumulative[-1][1]
        for boundary, upf_id in cumulative:
            if value <= boundary:
                selected = upf_id
                break
        after[f"new-{index:02d}"] = selected
    return existing, after


def certify_recommendation(
    forecast: Forecast,
    event: WorkshopEvent,
    *,
    controller: str,
    planning_risk: str,
    weights: Mapping[str, float] | None = None,
    migrate_existing: bool = False,
) -> CertificationResult:
    """Certify a recommendation or retain the last safe static policy.

    Invalid weights, eligibility, health, capacity, causality, or a request to
    migrate established sessions all take the same visible safe fallback path.
    """
    if controller not in CONTROLLERS:
        raise ValueError(f"controller must be one of {CONTROLLERS}")
    if planning_risk not in RISKS:
        raise ValueError(f"planning_risk must be one of {RISKS}")
    requested = dict(weights) if weights is not None else recommended_weights(controller, event, planning_risk)
    states = _upf_states(event, forecast.target_window.start)
    safe_weights = recommended_weights("static", event, planning_risk)
    safe_policy = _make_policy(
        forecast,
        safe_weights,
        states,
        planning_risk=planning_risk,
        policy_id="workshop-last-safe-static",
    )
    safe_report = validate_policy(
        safe_policy,
        [forecast],
        states,
        activation_time=forecast.target_window.start,
        config=ValidationConfig(planning_quantile=planning_risk),
    )
    try:
        if forecast.source_window_end > forecast.target_window.start:
            raise PolicyValidationError("forecast features overlap the target window")
        if migrate_existing:
            raise PolicyValidationError("established sessions are anchored; migration is outside the control contract")
        candidate = _make_policy(
            forecast,
            requested,
            states,
            planning_risk=planning_risk,
            policy_id=f"workshop-{controller}-{planning_risk}",
        )
        report = validate_policy(
            candidate,
            [forecast],
            states,
            activation_time=forecast.target_window.start,
            config=ValidationConfig(planning_quantile=planning_risk),
        )
        existing, after = _place_new_sessions(requested)
        anchored = all(after[session_id] == upf_id for session_id, upf_id in existing.items())
        if not anchored:
            raise PolicyValidationError("established session assignment changed")
        return CertificationResult(
            status="accepted",
            message="SAFE TO RECOMMEND · contract, eligibility, capacity, and anchoring checks passed",
            requested_weights=requested,
            applied_weights=requested,
            fallback_used=False,
            fallback_reason=None,
            policy_id=candidate.policy_id,
            existing_sessions_anchored=True,
            projected_ul_mbps_by_upf={key: round(value, 3) for key, value in report.projected_ul_mbps_by_upf.items()},
        )
    except (TypeError, ValueError, PolicyValidationError) as error:
        return CertificationResult(
            status="fallback",
            message=f"REJECTED · retained last safe/static policy · {error}",
            requested_weights=requested,
            applied_weights=safe_weights,
            fallback_used=True,
            fallback_reason=str(error),
            policy_id=safe_policy.policy_id,
            existing_sessions_anchored=True,
            projected_ul_mbps_by_upf={key: round(value, 3) for key, value in safe_report.projected_ul_mbps_by_upf.items()},
        )


def close_loop(
    rows: Sequence[Mapping[str, Any]],
    event: WorkshopEvent,
    certification: CertificationResult,
) -> dict[str, Any]:
    """Estimate the matched one-window consequence for workshop discussion."""
    actual = float(rows[event.start_window]["offered_ul_mbps"])
    residual = {"upf-a": 95.0, "upf-b": 110.0, "upf-c": 150.0}
    safe_capacity = {"upf-a": 192.0, "upf-b": 240.0, "upf-c": 300.0}

    def area(weights: Mapping[str, float]) -> float:
        return sum(
            max(0.0, residual[upf_id] + actual * float(weights.get(upf_id, 0.0)) - safe_capacity[upf_id])
            * WINDOW_SECONDS
            for upf_id in safe_capacity
        )

    static = recommended_weights("static", event, "p90")
    static_area = area(static)
    selected_area = area(certification.applied_weights)
    improvement = 0.0 if static_area == 0 else 100.0 * (static_area - selected_area) / static_area
    return {
        "actual_offered_ul_mbps": round(actual, 2),
        "static_overload_area_mbps_seconds": round(static_area, 2),
        "selected_overload_area_mbps_seconds": round(selected_area, 2),
        "modeled_exposure_change_percent": round(improvement, 2),
        "established_sessions_migrated": False,
        "interpretation": "Reduced modeled exposure" if improvement > 0 else "No modeled exposure reduction",
        "claim_boundary": "Synthetic estimate; not guaranteed overload prevention or production readiness.",
    }


def _team_id() -> str:
    configured = os.environ.get("CDOT_WORKSHOP_TEAM_ID")
    if configured:
        return configured
    for directory in (Path.cwd(), *Path.cwd().parents):
        config = directory / "team_config.json"
        if config.is_file():
            try:
                value = json.loads(config.read_text(encoding="utf-8")).get("team_id")
                if value:
                    return str(value)
            except (OSError, json.JSONDecodeError):
                break
        if directory == ROOT:
            break
    return "unassigned"


def build_decision(
    event: WorkshopEvent,
    certification: CertificationResult,
    outcome: Mapping[str, Any],
    *,
    controller: str,
    planning_risk: str,
    explanation: str,
) -> WorkshopDecision:
    if not explanation.strip():
        raise ValueError("explanation is required")
    return WorkshopDecision(
        schema_version="workshop-decision/1.0",
        team_id=_team_id(),
        selected_event=event.to_dict(),
        controller=controller,
        forecast_risk=planning_risk,
        expected_outcome=dict(outcome),
        explanation=explanation.strip(),
        policy_status=certification.status,
        fallback_used=certification.fallback_used,
    )


def save_decision(decision: WorkshopDecision, output_dir: str | Path | None = None) -> Path:
    if output_dir is None:
        output_dir = ROOT / "output" / "workshop" / decision.team_id
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "WorkshopDecision.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(decision.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target
