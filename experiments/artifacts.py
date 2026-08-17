from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from simulator.macro.sinks import file_sha256


@dataclass(frozen=True, slots=True)
class ArtifactPolicy:
    policy_id: str = "stage1-tiered-retention/1.0"
    salt: str = "cdot-stage1"
    silver_percentage: float = 1.0
    gold_pair_keys: frozenset[str] = field(default_factory=frozenset)
    row_group_size: int = 4096
    audit_mode: str = "tiered"
    jsonl_enabled: bool = False
    checkpoint_interval_seconds: int = 6 * 3600

    def __post_init__(self) -> None:
        if not self.policy_id or not self.salt:
            raise ValueError("artifact policy identity and deterministic salt are required")
        if not 0 <= self.silver_percentage <= 100:
            raise ValueError("silver_percentage must be in [0, 100]")
        if not 1 <= self.row_group_size <= 4096:
            raise ValueError("row_group_size must be in [1, 4096]")
        if self.audit_mode not in {"tiered", "count", "hash_sample", "reservoir", "parquet"}:
            raise ValueError("unsupported artifact-policy audit mode")
        if self.checkpoint_interval_seconds <= 0:
            raise ValueError("checkpoint interval must be positive")
        object.__setattr__(self, "gold_pair_keys", frozenset(self.gold_pair_keys))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["schema_version"] = "artifact-policy/1.0"
        result["gold_pair_keys"] = sorted(self.gold_pair_keys)
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ArtifactPolicy":
        payload = dict(value)
        schema = payload.pop("schema_version", "artifact-policy/1.0")
        if schema != "artifact-policy/1.0":
            raise ValueError("unsupported artifact policy schema")
        payload["gold_pair_keys"] = frozenset(payload.get("gold_pair_keys", ()))
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    tier: str
    pair_key: str
    assignment_sha256: str
    bucket: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def gold_pair_key(topology_id: str, scenario_id: str, seed: int) -> str:
    return f"{topology_id}|{scenario_id}|{seed}"


def assign_retention(
    policy: ArtifactPolicy, *, topology_id: str, scenario_id: str, seed: int
) -> RetentionDecision:
    pair = gold_pair_key(topology_id, scenario_id, seed)
    material = "\x1f".join((policy.policy_id, policy.salt, topology_id, scenario_id, str(seed)))
    digest = hashlib.sha256(material.encode()).hexdigest()
    bucket = int(digest[:16], 16) % 1_000_000
    if pair in policy.gold_pair_keys:
        tier = "gold"
    elif bucket < round(policy.silver_percentage * 10_000):
        tier = "silver"
    else:
        tier = "bronze"
    return RetentionDecision(tier, pair, digest, bucket)


def topology_identity(manifest_payload: dict[str, Any]) -> str:
    explicit = manifest_payload.get("topology_id")
    if explicit:
        return str(explicit)
    corpus = manifest_payload.get("corpus", {})
    topology = corpus.get("topology")
    if isinstance(topology, str):
        return topology
    canonical = json.dumps(
        {"upfs": manifest_payload.get("upfs", []), "groups": [
            {"key": item.get("key"), "eligible_upfs": item.get("eligible_upfs")}
            for item in manifest_payload.get("groups", [])
        ]}, sort_keys=True, separators=(",", ":"),
    ).encode()
    return f"topology-{hashlib.sha256(canonical).hexdigest()[:16]}"


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate_published_shard(path: Path) -> dict[str, Any]:
    """Treat metadata.json as the sole commit marker and verify every artifact."""
    metadata_path = path / "metadata.json"
    if not metadata_path.is_file():
        raise ValueError(f"incomplete shard has no commit marker: {path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("schema_version") != "experiment-shard/2.0":
        raise ValueError(f"unsupported shard metadata: {metadata_path}")
    for artifact in metadata.get("artifacts", []):
        candidate = path / artifact["path"]
        if not candidate.is_file():
            raise ValueError(f"published artifact is missing: {candidate}")
        if candidate.stat().st_size != int(artifact["bytes"]):
            raise ValueError(f"published artifact size mismatch: {candidate}")
        if file_sha256(candidate) != artifact["sha256"]:
            raise ValueError(f"published artifact hash mismatch: {candidate}")
    return metadata


def artifact_records(paths: Iterable[tuple[str, Path]]) -> list[dict[str, Any]]:
    return [
        {"kind": kind, "path": path.name, "sha256": file_sha256(path), "bytes": path.stat().st_size}
        for kind, path in paths
    ]
