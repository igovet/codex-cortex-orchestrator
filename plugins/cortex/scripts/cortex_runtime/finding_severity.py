"""Canonical finding severity normalization for every Cortex runtime path."""
from __future__ import annotations


# This is the runtime's only public-to-persisted severity registry.
PUBLIC_TO_CANONICAL_FINDING_SEVERITY = {
    "low": "P3",
    "medium": "P2",
    "high": "P1",
    "critical": "P0",
}
PUBLIC_FINDING_SEVERITIES = tuple(PUBLIC_TO_CANONICAL_FINDING_SEVERITY)
CANONICAL_FINDING_SEVERITY_RANK = {
    "info": 0,
    "P3": 1,
    "P2": 2,
    "P1": 3,
    "P0": 4,
}
INTRINSIC_BLOCKING_FINDING_SEVERITIES = frozenset({"P2", "P1", "P0"})


def normalize_finding_severity(value: object, *, default: str = "info") -> str:
    """Return one exact persisted severity or reject unsupported input."""
    raw = str(value).strip() if value is not None else str(default)
    canonical = PUBLIC_TO_CANONICAL_FINDING_SEVERITY.get(raw, raw)
    if canonical not in CANONICAL_FINDING_SEVERITY_RANK:
        raise ValueError("finding severity is outside the canonical public or persisted enum")
    return canonical


def finding_severity_is_intrinsically_blocking(value: object) -> bool:
    """Return whether the normalized severity is authoritative rework debt."""
    return normalize_finding_severity(value) in INTRINSIC_BLOCKING_FINDING_SEVERITIES


__all__ = [
    "CANONICAL_FINDING_SEVERITY_RANK",
    "INTRINSIC_BLOCKING_FINDING_SEVERITIES",
    "PUBLIC_FINDING_SEVERITIES",
    "PUBLIC_TO_CANONICAL_FINDING_SEVERITY",
    "finding_severity_is_intrinsically_blocking",
    "normalize_finding_severity",
]
