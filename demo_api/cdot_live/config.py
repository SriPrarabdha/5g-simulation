from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class UpfMapping:
    metric: str
    job: str
    pod: str
    smf: str
    ul_limit: float
    dl_limit: float


@dataclass(slots=True)
class LiveConfig:
    prometheus_url: str
    smf_url: str
    timeout_seconds: float
    poll_seconds: float
    history_hours: int
    bucket_seconds: int
    watermark_seconds: int
    stale_seconds: int
    mappings: dict[str, UpfMapping]
    dnn: dict[str, str]
    synthetic_dnn: dict[str, str]
    queries: dict[str, str]
    units: str = "pps-proxy"
    calibration_status: str = "uncalibrated_proxy"
    config_path: str = ""

    @classmethod
    def from_env(cls) -> "LiveConfig":
        path = Path(os.environ.get("CDOT_LIVE_CONFIG", ROOT / "configs" / "cdot_live.json"))
        raw = json.loads(path.read_text(encoding="utf-8"))
        mapping_override = os.environ.get("CDOT_LIVE_UPF_MAPPING")
        if mapping_override:
            supplied = json.loads(mapping_override)
            for metric, values in supplied.items():
                raw.setdefault("upfs", {}).setdefault(metric, {}).update(values)
        limits_override = os.environ.get("CDOT_LIVE_PROXY_LIMITS")
        if limits_override:
            for metric, limits in json.loads(limits_override).items():
                raw.setdefault("upfs", {}).setdefault(metric, {}).update(limits)
        query_override = os.environ.get("CDOT_LIVE_QUERIES")
        if query_override:
            raw.setdefault("queries", {}).update(json.loads(query_override))
        mappings = {
            metric: UpfMapping(
                metric=metric,
                job=str(item["job"]),
                pod=str(item["pod"]),
                smf=str(item["smf"]),
                ul_limit=float(item["ul_limit"]),
                dl_limit=float(item["dl_limit"]),
            )
            for metric, item in raw["upfs"].items()
        }
        return cls(
            prometheus_url=os.environ.get("CDOT_PROMETHEUS_URL", "http://192.168.218.8:29090").rstrip("/"),
            smf_url=os.environ.get("CDOT_SMF_URL", "http://192.168.218.8:30956").rstrip("/"),
            timeout_seconds=float(os.environ.get("CDOT_LIVE_TIMEOUT_SECONDS", "3")),
            poll_seconds=float(os.environ.get("CDOT_LIVE_POLL_SECONDS", "15")),
            history_hours=int(os.environ.get("CDOT_LIVE_HISTORY_HOURS", "24")),
            bucket_seconds=600,
            watermark_seconds=int(os.environ.get("CDOT_LIVE_WATERMARK_SECONDS", "30")),
            stale_seconds=int(os.environ.get("CDOT_LIVE_STALE_SECONDS", "90")),
            mappings=mappings,
            dnn={str(key): str(value) for key, value in raw.get("dnn", {}).items()},
            synthetic_dnn={str(key): str(value) for key, value in raw.get("synthetic_dnn", {}).items()},
            queries={str(key): str(value) for key, value in raw.get("queries", {}).items()},
            units=str(raw.get("units", "pps-proxy")),
            calibration_status=str(raw.get("calibration", {}).get("status", "uncalibrated_proxy")),
            config_path=str(path),
        )

    def mapping_status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.mappings),
            "traffic_to_operational_to_smf": {
                key: {"job": item.job, "pod": item.pod, "smf": item.smf}
                for key, item in self.mappings.items()
            },
            "dnn": dict(self.dnn),
            "direct_mapping_configurable": True,
        }
