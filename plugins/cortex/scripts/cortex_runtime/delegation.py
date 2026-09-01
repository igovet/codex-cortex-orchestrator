"""Small model-owned delegation projections for Cortex v12."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from cortex_runtime.model_routing import validate_model_selection


NATIVE_DISPATCH_MAX_BYTES = 64 * 1024


def _native_dispatch_digest(assignment_ref: str, native_arguments: Mapping[str, object]) -> str:
    canonical = json.dumps(
        {"assignment_ref": assignment_ref, "native_arguments": native_arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def validate_native_dispatch_projection(
    projection: Mapping[str, object],
    *,
    assignment_ref: object,
) -> dict[str, object]:
    """Validate and return the one server-issued native spawn projection.

    This is deliberately a pure, closed adapter seam: it never discovers an
    assignment, chooses a pending record, or reconstructs a worker message.
    The host must pass the returned native arguments unchanged to its spawn
    operation.  A digest mismatch therefore fails before any child exists.
    """
    if not isinstance(assignment_ref, str) or not assignment_ref:
        raise ValueError("assignment_ref must be non-empty")
    if not isinstance(projection, Mapping):
        raise ValueError("native dispatch projection must be an object")
    native_arguments = projection.get("native_arguments")
    bound_projection = isinstance(native_arguments, Mapping)
    if native_arguments is None:
        # Public open_assignment uses one literal-callable host projection.
        # Its three fields are forwarded directly; assignment identity and the
        # private digest are kept in the surrounding server receipt.
        if set(projection) != {"fork_turns", "message", "task_name"}:
            raise ValueError("native dispatch arguments are missing")
        native_arguments = projection
    elif projection.get("assignment_ref") != assignment_ref:
        raise ValueError("native dispatch assignment mismatch")
    if not isinstance(native_arguments, Mapping):
        raise ValueError("native dispatch arguments are missing")
    # open_assignment records an explicit coordinator-owned model/effort pair.
    # Preserve that pair in the one literal-callable host projection; otherwise
    # the durable assignment and actual child can silently use different
    # routing even when the message and task name match.
    required = ("fork_turns", "message", "task_name", "reasoning_effort")
    if any(key not in native_arguments for key in required):
        raise ValueError("native dispatch arguments are incomplete")
    if set(native_arguments) not in (
        set(required),
        set(required) | {"model"},
    ):
        raise ValueError("native dispatch arguments are not closed")
    if native_arguments.get("fork_turns") != "none":
        raise ValueError("native dispatch must use isolated history")
    message = native_arguments.get("message")
    if not isinstance(message, str) or not message or len(message.encode("utf-8")) > NATIVE_DISPATCH_MAX_BYTES:
        raise ValueError("native dispatch message is invalid or oversized")
    task_name = native_arguments.get("task_name")
    if not isinstance(task_name, str) or not task_name:
        raise ValueError("native dispatch task name is invalid")
    routed_model = native_arguments.get("model", "gpt-5.6-luna")
    validate_model_selection(routed_model, native_arguments.get("reasoning_effort"))
    if native_arguments.get("model") == "gpt-5.6-luna":
        raise ValueError("native Luna dispatch must use the configured default model")
    if bound_projection:
        digest = projection.get("dispatch_digest")
        expected = _native_dispatch_digest(assignment_ref, dict(native_arguments))
        if digest != expected:
            raise ValueError("native dispatch digest mismatch")
    return dict(native_arguments)


def native_dispatch_projection(
    *,
    assignment_ref: str,
    task_name: str,
    message: str,
    model: str,
    reasoning_effort: str,
) -> dict[str, object]:
    """Build one closed, digest-bound projection for the native host adapter."""
    if not isinstance(message, str) or not message or len(message.encode("utf-8")) > NATIVE_DISPATCH_MAX_BYTES:
        raise ValueError("rendered_message exceeds the native dispatch bound")
    selection = validate_model_selection(model, reasoning_effort)
    native_arguments: dict[str, object] = {
        "fork_turns": "none",
        "message": message,
        "task_name": task_name,
        "reasoning_effort": selection.reasoning_effort,
    }
    if selection.model != "gpt-5.6-luna":
        native_arguments["model"] = selection.model
    return {
        "assignment_ref": assignment_ref,
        "dispatch_digest": _native_dispatch_digest(assignment_ref, native_arguments),
        "native_arguments": native_arguments,
    }


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
    return (
        task_name == profile_name
        or re.fullmatch(re.escape(profile_name) + r"_[1-9][0-9]*", task_name) is not None
        or re.fullmatch(re.escape(profile_name) + r"_d_[0-9a-f]{12}", task_name) is not None
    )


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
    task_ref: object,
    project_root: object,
    semantic_objective: object,
    profile_proof: Mapping[str, object],
    effective_contract: Mapping[str, object] | None = None,
    dispatch_correlation_marker: object = None,
    dispatch_correlation_digest: object = None,
) -> dict[str, Any]:
    """Return a semantic brief plus one closed native dispatch projection."""
    selection = delegation_model_metadata(
        {"model": model, "reasoning_effort": reasoning_effort},
    )
    if not isinstance(task_name, str) or not task_name:
        raise ValueError("task_name must be non-empty")
    if not isinstance(message, str) or not message:
        raise ValueError("rendered_message must be non-empty")
    if not isinstance(delegation_ref, str) or not delegation_ref:
        raise ValueError("delegation_ref must be non-empty")
    if not isinstance(task_ref, str) or re.fullmatch(r"t_[0-9a-f]{12}", task_ref) is None:
        raise ValueError("task_ref must be an emitted compact task reference")
    if not isinstance(project_root, str) or not project_root:
        raise ValueError("project_root must be non-empty")
    if not isinstance(semantic_objective, str) or not semantic_objective:
        raise ValueError("semantic_objective must be non-empty")
    if not isinstance(dispatch_correlation_marker, str) or re.fullmatch(r"dc_[0-9a-f]{32}", dispatch_correlation_marker) is None:
        raise ValueError("dispatch_correlation_marker must be server-issued")
    expected_digest = "sha256:" + hashlib.sha256(dispatch_correlation_marker.encode("utf-8")).hexdigest()
    if dispatch_correlation_digest != expected_digest:
        raise ValueError("dispatch_correlation_digest must match marker")
    native_dispatch = native_dispatch_projection(
        assignment_ref=delegation_ref,
        task_name=task_name,
        message=message,
        model=selection["model"],
        reasoning_effort=selection["reasoning_effort"],
    )
    result = {
        "mode": "assignment_worker",
        "task_name": task_name,
        "semantic_objective": semantic_objective,
        "recommended_model": selection["model"],
        "recommended_reasoning_effort": selection["reasoning_effort"],
        "delegation_ref": delegation_ref,
        # The host gives this immutable brief to the native worker, which does
        # not otherwise see the coordinator's earlier task-opening receipt.
        # Keep the two typed anchors distinct so a worker cannot mistake its
        # assignment reference for the task reference required by task reads.
        "task_ref": task_ref,
        "project_root": project_root,
        "profile_proof": dict(profile_proof),
        "dispatch_correlation_marker": dispatch_correlation_marker,
        "dispatch_correlation_fingerprint": expected_digest,
        # This is the sole host spawn projection.  The host must consume it
        # unchanged; semantic fields above remain durable evidence only.
        "native_dispatch": native_dispatch,
    }
    return result
