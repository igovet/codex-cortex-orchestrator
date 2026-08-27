"""Coordinator-owned native model selection helpers.

V12 deliberately keeps model and reasoning-effort selection out of the ledger
and governance policy. This module is the narrow transport boundary: it
validates the native Codex values chosen by the coordinator and projects one
``spawn_agent`` argument object without changing that choice.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


LUNA_MODEL = "gpt-5.6-luna"
TERRA_MODEL = "gpt-5.6-terra"
SOL_MODEL = "gpt-5.6-sol"

NATIVE_MODELS = (LUNA_MODEL, TERRA_MODEL, SOL_MODEL)
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
    """Validate, but never derive or rewrite, a coordinator-selected pair."""
    exact_model = _required_text(model, "model")
    exact_effort = _required_text(reasoning_effort, "reasoning_effort")
    if exact_model not in NATIVE_MODELS:
        raise ValueError("model is not supported by the native subagent transport")
    if exact_effort not in NATIVE_REASONING_EFFORTS:
        raise ValueError("reasoning_effort is not supported by the native subagent transport")
    return ModelSelection(model=exact_model, reasoning_effort=exact_effort)


def native_spawn_arguments(
    *,
    model: object,
    reasoning_effort: object,
    task_name: object,
    message: object,
    fork_turns: object = "none",
) -> dict[str, str]:
    """Build the exact native ``spawn_agent`` arguments for one selection.

    Luna is the configured native default and therefore *must not* appear as a
    ``model`` argument. Terra and Sol are explicit native overrides. The effort
    is always carried unchanged, including for Luna. Host-default configuration
    is an installation/preflight responsibility, never a ledger or governance
    gate.
    """
    selection = validate_model_selection(model, reasoning_effort)
    exact_task_name = _required_text(task_name, "task_name")
    exact_message = _required_text(message, "message")
    if fork_turns != "none":
        raise ValueError("fork_turns must be 'none' for a native subagent")

    arguments = {
        "task_name": exact_task_name,
        "message": exact_message,
        "reasoning_effort": selection.reasoning_effort,
        "fork_turns": "none",
    }
    if selection.model != LUNA_MODEL:
        arguments["model"] = selection.model
    return arguments


def model_effort_registry(_model_routing: Mapping[str, Any] | None = None) -> dict[str, tuple[str, ...]]:
    """Return native transport support without treating profiles as authority.

    The retained optional argument preserves import compatibility for source
    tooling while making clear that V12 does not load a backend capability
    matrix from profile metadata.
    """
    return {model: NATIVE_REASONING_EFFORTS for model in NATIVE_MODELS}


def supported_effort_sequence(_model_routing: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    """Return the ordered reasoning-effort values accepted by the host."""
    return NATIVE_REASONING_EFFORTS


def model_effort_pair_is_allowed(
    _registry: Mapping[str, Sequence[str]] | None,
    model: object,
    effort: object,
) -> bool:
    """Compatibility predicate for exact native transport support."""
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
        if model in NATIVE_MODELS and effort in NATIVE_REASONING_EFFORTS:
            result[str(model)] = str(effort)
    return result


def model_effort_pair_text(_model_routing: Mapping[str, Any] | None = None) -> str:
    """Render the native support boundary for user-facing prompt guidance."""
    efforts = " or ".join(NATIVE_REASONING_EFFORTS)
    return "; ".join(f"{model} -> {efforts}" for model in NATIVE_MODELS)
