"""Coordinator-owned, host-neutral model recommendation helpers.

V12 persists the coordinator's recommendation as delegation context.  It does
not validate a host catalogue, serialize a host call, or turn a recommendation
into native-agent lifecycle authority.  The active Codex host maps a brief to
its current ``spawn_agent`` schema at dispatch time.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


# These are packaged recommendations, not an advertised host capability list.
# The host remains authoritative for model availability and effort values.
NATIVE_MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
NATIVE_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelSelection:
    """One exact native model/effort pair chosen by the coordinator."""

    model: str
    reasoning_effort: str


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def validate_model_selection(model: object, reasoning_effort: object) -> ModelSelection:
    """Validate bounded recommendation text without asserting host support."""
    exact_model = _required_text(model, "model")
    exact_effort = _required_text(reasoning_effort, "reasoning_effort")
    return ModelSelection(model=exact_model, reasoning_effort=exact_effort)


def model_effort_registry(_model_routing: Mapping[str, Any] | None = None) -> dict[str, tuple[str, ...]]:
    """Return packaged recommendations for source tooling only."""
    return {model: NATIVE_REASONING_EFFORTS for model in NATIVE_MODELS}


def supported_effort_sequence(_model_routing: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    """Return bundled recommendation labels without claiming host support."""
    return NATIVE_REASONING_EFFORTS


def model_effort_pair_is_allowed(
    _registry: Mapping[str, Sequence[str]] | None,
    model: object,
    effort: object,
) -> bool:
    """Compatibility predicate for bounded recommendation text."""
    try:
        validate_model_selection(model, effort)
    except ValueError:
        return False
    return True


def model_recommendation_registry(model_routing: Mapping[str, Any]) -> dict[str, str]:
    """Read optional advisory recommendations without affecting validation."""
    recommendations = model_routing.get("recommendations")
    if not isinstance(recommendations, list):
        return {}
    result: dict[str, str] = {}
    for item in recommendations:
        if not isinstance(item, Mapping):
            continue
        model = item.get("model")
        effort = item.get("recommended_effort")
        if isinstance(model, str) and model and isinstance(effort, str) and effort:
            result[str(model)] = str(effort)
    return result


def model_effort_pair_text(_model_routing: Mapping[str, Any] | None = None) -> str:
    """Render packaged recommendations, never a host capability assertion."""
    efforts = " or ".join(NATIVE_REASONING_EFFORTS)
    return "; ".join(f"{model} -> {efforts}" for model in NATIVE_MODELS)
