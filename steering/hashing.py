from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping


def rendezvous_select(session_key: str, policy_id: str, weights: Mapping[str, float]) -> tuple[str, str]:
    """Select a UPF using deterministic weighted rendezvous hashing.

    Returns both the selected UPF and the winning hash value for audit records.
    Zero-weight destinations are ignored. Ties are resolved by UPF ID.
    """
    candidates: list[tuple[float, str, str]] = []
    for upf_id, weight in weights.items():
        if weight <= 0:
            continue
        digest = hashlib.sha256(f"{session_key}\x1f{policy_id}\x1f{upf_id}".encode()).hexdigest()
        integer = int(digest, 16)
        uniform = (integer + 0.5) / (2**256)
        candidates.append((-math.log(uniform) / weight, upf_id, digest))
    if not candidates:
        raise ValueError("at least one positive weight is required")
    _, selected, digest = min(candidates, key=lambda item: (item[0], item[1]))
    return selected, digest

