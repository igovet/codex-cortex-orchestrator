"""One canonical JSON boundary for durable values and digests.

Canonical records must retain JSON scalar types: ``1`` and ``"1"`` are
different values and therefore must not be normalized through ``str`` before
being persisted or hashed. Redaction remains a separate ingress concern.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any


def normalize(value: Any) -> Any:
    """Return a strict-JSON-shaped value without changing scalar types."""
    if isinstance(value, Mapping):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number is not canonical")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"value of type {type(value).__name__} is not strict JSON")


def dumps(value: Any) -> str:
    """Serialize one value using the repository's canonical JSON rules."""
    return json.dumps(
        normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    """Return the SHA-256 digest of canonical JSON bytes."""
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


__all__ = ["normalize", "dumps", "digest"]
