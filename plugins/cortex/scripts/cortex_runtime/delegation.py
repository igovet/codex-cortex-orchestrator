"""Small model-owned delegation projections for Cortex v12."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from cortex_runtime.model_routing import native_spawn_arguments, validate_model_selection


def native_task_name(delegation_id: object) -> str:
    """Return the exact host-safe native task name for one durable delegation."""
    if not isinstance(delegation_id, str):
        raise ValueError("delegation_id must be a string")
    return "cortex_" + hashlib.sha256(delegation_id.encode("utf-8")).hexdigest()[:32]


def delegation_model_metadata(values: Mapping[str, object]) -> dict[str, str]:
    """Return exact coordinator selection metadata for a durable delegation.

    This function neither selects a role nor inspects governance, task state,
    or a profile. The returned metadata is audit context, never an authority
    check for creating, reading, or reporting a delegation.
    """
    selection = validate_model_selection(
        values.get("model"),
        values.get("reasoning_effort"),
    )
    return {
        "model": selection.model,
        "reasoning_effort": selection.reasoning_effort,
    }


def native_delegation_projection(
    *,
    task_name: object,
    message: object,
    model: object,
    reasoning_effort: object,
) -> dict[str, Any]:
    """Project one coordinator-owned selection to a native spawn request.

    The logical pair remains visible for durable audit while the nested native
    arguments obey the Luna-default transport rule. No lifecycle, recovery, or
    profile metadata is introduced.
    """
    selection = delegation_model_metadata(
        {"model": model, "reasoning_effort": reasoning_effort},
    )
    return {
        "task_name": task_name,
        "selection": selection,
        "native_arguments": native_spawn_arguments(
            model=selection["model"],
            reasoning_effort=selection["reasoning_effort"],
            task_name=task_name,
            message=message,
        ),
    }
