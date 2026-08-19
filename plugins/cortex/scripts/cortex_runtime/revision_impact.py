"""Deterministic material-steer impact classification.

The classifier deliberately stays small and auditable.  It does not ask a
model to reinterpret the user's request at the authorization boundary; it
maps explicit canonical-English change language to the earliest pipeline
contract that must be reconsidered.  Unknown changes conservatively affect
the currently active gate.
"""
from __future__ import annotations

import re
from typing import Any, Iterable


_GATE_ORDER = (
    "scope",
    "plan",
    "architecture",
    "implementation",
    "qa",
    "security",
    "review",
    "documentation",
    "governance_close",
    "close",
)

_CATEGORY_PATTERNS: dict[str, tuple[str, ...]] = {
    "requirements": (
        r"\b(requirement|acceptance criteri|objective|scope|must also|instead of)\b",
        r"\b(add|remove|change)\b.{0,48}\b(feature|behavior|workflow|support)\b",
    ),
    "persistence": (
        r"\b(database|schema|migration|storage|persistence|transaction|sqlite|postgres|mysql)\b",
    ),
    "public_contract": (
        r"\b(public api|public contract|wire format|protocol|endpoint|backward compatib)\b",
    ),
    "security": (
        r"\b(authentication|authorization|permission|tenant|credential|secret|token|privacy|security)\b",
    ),
    "verification": (
        r"\b(test|verification|benchmark|performance budget|acceptance check|qa)\b",
    ),
    "documentation": (
        r"\b(documentation|readme|changelog|docs only|comment only)\b",
    ),
}

_CATEGORY_GATE = {
    "requirements": "scope",
    "persistence": "architecture",
    "public_contract": "architecture",
    "security": "architecture",
    "verification": "qa",
    "documentation": "documentation",
}


def _normalise_gates(values: Iterable[Any]) -> list[str]:
    return [str(value or "").strip().lower().replace("-", "_") for value in values if str(value or "").strip()]


def classify_revision_impact(
    message_en: str,
    *,
    pipeline: Iterable[Any],
    current_gates: Iterable[Any],
    active_attempt_ids: Iterable[Any] = (),
) -> dict[str, Any]:
    """Return the earliest affected gate and downstream invalidation contract."""
    text = " ".join(str(message_en or "").strip().lower().split())
    pipeline_values = _normalise_gates(pipeline)
    current_values = _normalise_gates(current_gates)
    categories = [
        category
        for category, patterns in _CATEGORY_PATTERNS.items()
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)
    ]
    documentation_only = bool(categories) and set(categories) == {"documentation"}
    desired = "documentation" if documentation_only else (
        min((_CATEGORY_GATE[item] for item in categories), key=_GATE_ORDER.index)
        if categories else (current_values[0] if current_values else "implementation")
    )
    # Preserve the required contract even when a lightweight pipeline omitted
    # that gate. The facade can then add it at the safe post-attempt boundary
    # instead of silently selecting a later, weaker phase.
    earliest = desired
    if earliest in pipeline_values:
        earliest_index = pipeline_values.index(earliest)
        downstream = pipeline_values[earliest_index:]
    else:
        desired_index = _GATE_ORDER.index(desired) if desired in _GATE_ORDER else 0
        downstream = [
            gate for gate in pipeline_values
            if gate in _GATE_ORDER and _GATE_ORDER.index(gate) >= desired_index
        ]
        downstream.insert(0, earliest)
    active_ids = [str(item) for item in active_attempt_ids if str(item)]
    return {
        "schema": "cortex/revision-impact/v1",
        "classification": "documentation_only" if documentation_only else "material_pipeline_revision",
        "categories": categories or ["active_work"],
        "earliest_affected_gate": earliest,
        "required_gate_missing": earliest not in pipeline_values,
        "invalidate_gates": downstream,
        "affected_work_packages": active_ids,
        "new_work_packages": [],
        "obsolete_work_packages": [],
        "dependency_changes": [],
        "requires_plan_revision": bool(earliest and earliest in {"scope", "plan", "architecture"}),
    }


__all__ = ["classify_revision_impact"]
