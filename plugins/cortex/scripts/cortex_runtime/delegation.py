"""Small model-owned delegation projections for Cortex v12."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from cortex_runtime.model_routing import native_spawn_arguments, validate_model_selection


def native_task_name(profile_name: object, instance: object = 1) -> str:
    """Return a host-safe name that visibly identifies a worker profile.

    The first use of a profile keeps its exact packaged profile name. Later
    same-profile siblings receive a one-based suffix, which keeps each native
    worker addressable without hiding its role behind an opaque identifier.
    """
    if not isinstance(profile_name, str) or re.fullmatch(r"[a-z][a-z0-9_]*", profile_name) is None:
        raise ValueError("profile_name must be a host-safe profile name")
    if isinstance(instance, bool) or not isinstance(instance, int) or instance < 1:
        raise ValueError("instance must be a positive integer")
    result = profile_name if instance == 1 else f"{profile_name}_{instance}"
    if len(result) > 64:
        raise ValueError("native task name exceeds the host limit")
    return result


def legacy_native_task_name(delegation_id: object) -> str:
    """Return the prior opaque task name retained for live old workers."""
    if not isinstance(delegation_id, str):
        raise ValueError("delegation_id must be a string")
    return "cortex_" + hashlib.sha256(delegation_id.encode("utf-8")).hexdigest()[:32]


def is_profile_native_task_name(task_name: object, profile_name: object) -> bool:
    """Whether a persisted name is the profile or one of its numbered siblings."""
    if not isinstance(task_name, str) or not isinstance(profile_name, str):
        return False
    return task_name == profile_name or re.fullmatch(re.escape(profile_name) + r"_[1-9][0-9]*", task_name) is not None


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
