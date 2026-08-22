"""Stable non-authoritative hashes used by gateway DTOs and identifiers."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.blake2b(encoded, digest_size=32).hexdigest()


def stable_u128(payload: Any) -> int:
    value = int(stable_hash(payload)[:32], 16)
    return value if value > 0 else 1
