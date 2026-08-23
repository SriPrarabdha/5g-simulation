"""Exporter for the versioned ``twin-replay/1.0`` workshop contract."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


def _group_id(item: Mapping[str, Any]) -> str:
    key = item["key"]
    return f"{key['zone']}|{key['dnn']}|{key['snssai']}"


def _positions(manifest: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    zones = sorted({item["key"]["zone"] for item in manifest["groups"]})
    nodes: list[dict[str, Any]] = []
    for index, zone in enumerate(zones):
        angle = 2 * math.pi * index / max(1, len(zones))
        nodes.append({"id": f"zone:{zone}", "kind": "demand_zone", "label": zone.title(),
                      "position": {"x": round(28 * math.cos(angle), 4), "y": 0, "z": round(22 * math.sin(angle), 4)},
                      "zone": zone, "synthetic": True})
        nodes.append({"id": f"gnb:{zone}", "kind": "gnb", "label": f"gNB · {zone.title()}",
                      "position": {"x": round(18 * math.cos(angle), 4), "y": 0, "z": round(14 * math.sin(angle), 4)},
                      "zone": zone, "synthetic": True})
    for index, item in enumerate(manifest["upfs"]):
        angle = 2 * math.pi * index / max(1, len(manifest["upfs"]))
        nodes.append({"id": item["upf_id"], "kind": "upf", "label": item["upf_id"].upper(),
                      "position": {"x": round(8 * math.cos(angle), 4), "y": 0, "z": round(8 * math.sin(angle), 4)},
                      "zone": item.get("zone", "core"), "synthetic": True})
    links = []
    for zone in zones:
        links.append({"source": f"zone:{zone}", "target": f"gnb:{zone}", "kind": "radio"})
    for group in manifest["groups"]:
        for upf in group["eligible_upfs"]:
            link = {"source": f"gnb:{group['key']['zone']}", "target": upf, "kind": "user_plane"}
            if link not in links:
                links.append(link)
    return nodes, links


def _rows(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("PyArrow is required to export a replay from run.parquet") from error
    return pq.read_table(path).to_pylist()


def _bins(rows: list[dict[str, Any]], max_frames: int) -> Iterable[list[dict[str, Any]]]:
    width = max(1, math.ceil(len(rows) / max_frames))
    for start in range(0, len(rows), width):
        yield rows[start:start + width]


def export_replay(run_parquet: str | Path, metadata: str | Path | Mapping[str, Any],
                  output: str | Path | None = None, *, max_frames: int = 240,
                  selection_audits: str | Path | None = None) -> dict[str, Any]:
    if not 1 <= max_frames <= 1000:
        raise ValueError("max_frames must be in [1, 1000]")
    manifest = json.loads(Path(metadata).read_text(encoding="utf-8")) if isinstance(metadata, (str, Path)) else dict(metadata)
    rows = _rows(Path(run_parquet))
    if not rows:
        raise ValueError("run.parquet contains no frames")
    nodes, links = _positions(manifest)
    capacities = {item["upf_id"]: item for item in manifest["upfs"]}
    frames: list[dict[str, Any]] = []
    for bucket in _bins(rows, max_frames):
        seconds = sum((row["window_end"] - row["window_start"]).total_seconds() for row in bucket)
        metrics: dict[str, dict[str, Any]] = {}
        flows: dict[tuple[str, str], dict[str, float]] = {}
        total_offered = total_carried = total_loss = total_overload = 0.0
        for row in bucket:
            interval = (row["window_end"] - row["window_start"]).total_seconds()
            for upf in row["upfs"]:
                ident = upf["upf_id"]
                item = metrics.setdefault(ident, {"offered_mbit": 0.0, "carried_mbit": 0.0, "loss_mbit": 0.0,
                                                  "queue_mbit": 0.0, "sessions": 0.0, "health": upf["health"]})
                offered = upf["ul"]["offered_bytes"] * 8 / 1_000_000
                carried = upf["ul"]["carried_bytes"] * 8 / 1_000_000
                loss = (upf["ul"]["dropped_bytes"] + upf["ul"]["rejected_bytes"]) * 8 / 1_000_000
                item["offered_mbit"] += offered; item["carried_mbit"] += carried; item["loss_mbit"] += loss
                item["queue_mbit"] += upf["ul"]["queued_bytes"] * 8 / 1_000_000
                item["sessions"] += upf["active_sessions"] * interval
                item["health"] = upf["health"]
                total_offered += offered; total_carried += carried; total_loss += loss
                safe = float(upf["ul"]["safe_capacity_mbps"])
                total_overload += max(0.0, offered - safe * interval)
            for flow in row.get("group_upf_buckets") or []:
                key = (flow["group_id"], flow["upf_id"])
                item = flows.setdefault(key, {"demand_mbit": 0.0, "admitted_sessions": 0.0})
                item["demand_mbit"] += float(flow["offered_ul_mbps"]) * interval
                item["admitted_sessions"] += float(flow["admitted_sessions"])
        upf_metrics = []
        for ident, item in sorted(metrics.items()):
            safe_capacity = float(capacities[ident]["capacity_mbps"]["ul"]) * float(capacities[ident]["safe_utilization"]["ul"])
            average_offered = item["offered_mbit"] / seconds
            utilization = average_offered / max(safe_capacity, 1e-12)
            upf_metrics.append({"upf_id": ident, "health": item["health"], "utilization": utilization,
                                "safe_envelope_violation": utilization > 1, "queue_mbits": item["queue_mbit"],
                                "active_sessions": item["sessions"] / seconds,
                                "offered_mbps": average_offered, "carried_mbps": item["carried_mbit"] / seconds,
                                "loss_mbps": item["loss_mbit"] / seconds})
        group_totals: dict[str, float] = {}
        for (group, _), item in flows.items():
            group_totals[group] = group_totals.get(group, 0.0) + item["admitted_sessions"]
        flow_rows = [{"group_id": group, "source": f"gnb:{group.split('|')[0]}", "target": upf,
                      "demand_mbps": item["demand_mbit"] / seconds,
                      "routing_weight": item["admitted_sessions"] / group_totals[group] if group_totals[group] else 0.0,
                      "future_sessions_only": True}
                     for (group, upf), item in sorted(flows.items())]
        frames.append({"index": len(frames), "start": bucket[0]["window_start"].isoformat(),
                       "end": bucket[-1]["window_end"].isoformat(), "source_steps": [bucket[0]["step"], bucket[-1]["step"]],
                       "policy_id": bucket[-1]["policy_id"], "upfs": upf_metrics, "flows": flow_rows,
                       "aggregates": {"offered_mbit": total_offered, "carried_mbit": total_carried,
                                      "loss_mbit": total_loss, "overload_mbit": total_overload},
                       "causality": {"policy_applies_to": "future_sessions_only", "existing_sessions_anchored": True,
                                     "history_recomputed": False}})
    events = [{"id": f"event-{index + 1}", "step": event["step"], "kind": event["event_type"],
               "label": f"{event['event_type']} · {event.get('upf_id') or event.get('group_id')}", "details": event}
              for index, event in enumerate(manifest.get("events", []))]
    payload = {"schema_version": "twin-replay/1.0", "metadata": {
        "title": "C-DOT synthetic UPF digital twin replay", "synthetic": True,
        "spatial_layout": "synthetic", "scenario_id": manifest["scenario_id"], "seed": rows[0]["seed"],
        "generated_at": datetime.now(timezone.utc).isoformat(), "source": str(run_parquet),
        "source_frame_count": len(rows), "frame_count": len(frames), "selection_audits": str(selection_audits) if selection_audits else None,
        "control_scope": "new_session_placement_only", "established_session_migration": False},
        "topology": {"nodes": nodes, "links": links},
        "groups": [{"id": _group_id(group), "zone": group["key"]["zone"], "dnn": group["key"]["dnn"],
                    "snssai": group["key"]["snssai"], "eligible_upfs": group["eligible_upfs"]} for group in manifest["groups"]],
        "frames": frames, "events": events}
    validate_replay(payload)
    if output is not None:
        destination = Path(output); destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def validate_replay(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != "twin-replay/1.0":
        raise ValueError("unsupported replay schema")
    if payload.get("metadata", {}).get("spatial_layout") != "synthetic":
        raise ValueError("replay must label the spatial layout synthetic")
    frames = payload.get("frames", [])
    if not frames or any(frame["index"] != index for index, frame in enumerate(frames)):
        raise ValueError("replay frames must be non-empty and ordered")
    for frame in frames:
        if not frame["causality"]["existing_sessions_anchored"] or frame["causality"]["history_recomputed"]:
            raise ValueError("replay violates new-session-only causality")


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert campaign run.parquet into twin-replay/1.0")
    parser.add_argument("run_parquet", type=Path); parser.add_argument("metadata", type=Path)
    parser.add_argument("--output", type=Path, required=True); parser.add_argument("--selection-audits", type=Path)
    parser.add_argument("--max-frames", type=int, default=240)
    args = parser.parse_args()
    export_replay(args.run_parquet, args.metadata, args.output, max_frames=args.max_frames,
                  selection_audits=args.selection_audits)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
