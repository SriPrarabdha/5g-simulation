from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping


def rendezvous_select(session_key: str, policy_id: str, weights: Mapping[str, float]) -> tuple[str, str]:
    """Select a UPF using deterministic weighted rendezvous hashing.

    Returns both the selected UPF and the winning hash value for audit records.
    Zero-weight destinations are ignored. Ties are resolved by UPF ID.
    """
    winner: tuple[float, str, bytes] | None = None
    for upf_id, weight in weights.items():
        if weight <= 0:
            continue
        digest = hashlib.sha256(f"{session_key}\x1f{policy_id}\x1f{upf_id}".encode()).digest()
        integer = int.from_bytes(digest, "big")
        uniform = (integer + 0.5) / (2**256)
        candidate = (-math.log(uniform) / weight, upf_id, digest)
        if winner is None or candidate[:2] < winner[:2]:
            winner = candidate
    if winner is None:
        raise ValueError("at least one positive weight is required")
    _, selected, digest = winner
    return selected, digest.hex()
