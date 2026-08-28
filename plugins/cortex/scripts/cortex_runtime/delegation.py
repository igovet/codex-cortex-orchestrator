"""Small model-owned delegation projections for Cortex v12."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from cortex_runtime.model_routing import validate_model_selection


def native_task_name(profile_name: object, instance: object = 1) -> str:
    """Return a stable human-readable task name for the host-facing brief.

    The first use of a profile keeps its exact packaged profile name. Later
    same-profile siblings receive a one-based suffix, which keeps each native
    worker addressable without hiding its role behind an opaque identifier.
    """
    if not isinstance(profile_name, str) or re.fullmatch(r"[a-z][a-z0-9_]*", profile_name) is None:
        raise ValueError("profile_name must be a packaged profile name")
    if isinstance(instance, bool) or not isinstance(instance, int) or instance < 1:
        raise ValueError("instance must be a positive integer")
    result = profile_name if instance == 1 else f"{profile_name}_{instance}"
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


def dispatch_brief_projection(
    *,
    task_name: object,
    message: object,
    model: object,
    reasoning_effort: object,
    delegation_ref: object,
    project_root: object,
    semantic_objective: object,
    profile_proof: Mapping[str, object],
    effective_contract: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Return a host-neutral dispatch brief for the active coordinator.

    The brief deliberately contains no static host argument object.  Codex is
    responsible for mapping its semantic fields to the active spawn operation.
    """
    selection = delegation_model_metadata(
        {"model": model, "reasoning_effort": reasoning_effort},
    )
    if not isinstance(task_name, str) or not task_name:
        raise ValueError("task_name must be non-empty")
    if not isinstance(message, str) or not message:
        raise ValueError("rendered_message must be non-empty")
    if not isinstance(delegation_ref, str) or not delegation_ref:
        raise ValueError("delegation_ref must be non-empty")
    if not isinstance(project_root, str) or not project_root:
        raise ValueError("project_root must be non-empty")
    if not isinstance(semantic_objective, str) or not semantic_objective:
        raise ValueError("semantic_objective must be non-empty")
    result = {
        "task_name": task_name,
        "rendered_message": message,
        "semantic_objective": semantic_objective,
        "recommended_model": selection["model"],
        "recommended_reasoning_effort": selection["reasoning_effort"],
        "delegation_ref": delegation_ref,
        "project_root": project_root,
        "profile_proof": dict(profile_proof),
    }
    if effective_contract is not None:
        result["effective_contract"] = dict(effective_contract)
    return result
