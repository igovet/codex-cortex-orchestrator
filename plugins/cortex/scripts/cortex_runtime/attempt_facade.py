"""Worker-facing adapters for the database-centric attempt protocol.

The worker supplies semantic facts.  This facade resolves its already-issued
attempt, records the canonical result/event rows, verifies machine-side read
receipts, derives the workspace observation, and exposes only a regenerated
non-authoritative AttemptResult read view.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import cortex as _runtime

from cortex_runtime import attempt_protocol
from cortex_runtime.validation import ValidationFailure
from cortex_runtime.worker_identity import (
    SERVER_OWNED_FIELDS,
    WorkerBindingError,
    bind_semantic_params,
    current_binding,
    require_binding,
)


_CLOSURE_GATES = {"review", "governance_activation", "governance_close", "close"}
_ACTIVE_ATTEMPT_STATUSES = {_runtime.AWAITING_HOST_SPAWN, "running"}
_PUBLIC_IDENTITY_FIELDS = set(SERVER_OWNED_FIELDS)
# Keep the worker-facing error contract next to the worker-facing adapters.
# The runtime's orchestration validator has a richer plan schema, but these
# tools must still describe their own envelope when a worker submits malformed
# JSON.  In particular, an unknown-field error is useful only when the caller
# can see the complete allowed property set and the received value.
_FACADE_FIELDS: dict[str, dict[str, Any]] = {
    "complete_attempt": {
        "type": "object",
        "required": ["status", "summary"],
        "properties": {
            field: {"type": "string"} for field in ("status", "summary")
        } | {
            "findings": {"type": "array"}, "decisions_needed": {"type": "array"},
            "unresolved": {"type": "array"}, "claims": {"type": "array"},
            "planning": {"type": "object"}, "base_payload_digest": {"type": "string"},
            "patches": {"type": "array", "items": {"type": "object", "required": ["op", "path", "value"]}},
        },
    },
    "record_attempt_event": {
        "type": "object", "required": ["event_type", "payload"],
        "properties": {
            "event_type": {"type": "string"}
        } | {"payload": {}, "event_key": {"type": "string"}},
    },
    "repair_planning": {
        "type": "object", "required": ["base_payload_digest", "patches"],
        "properties": {
            "base_payload_digest": {"type": "string"}
        } | {"patches": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["op", "path", "value"]}}},
    },
    "read_worker_result": {
        "type": "object", "required": ["attempt_result_ref"],
        "properties": {
            "attempt_result_ref": {"type": "string"},
        },
    },
}


def _facade_schema(operation: str) -> dict[str, Any]:
    """Use the exact MCP schema once the runtime registry is initialized."""
    registry = getattr(_runtime, "PUBLIC_SCHEMA_REGISTRY", None)
    schema = registry.get(operation) if isinstance(registry, dict) else None
    return schema if isinstance(schema, dict) else _FACADE_FIELDS[operation]


def _json_pointer(path: object) -> str:
    """Convert the public diagnostic path to an RFC 6901 pointer."""
    raw = str(path or "$")
    if raw.startswith("/"):
        return raw
    if raw == "$":
        return ""
    raw = raw[2:] if raw.startswith("$.") else raw.lstrip(".")
    parts: list[str] = []
    for segment in raw.replace("]", "").replace("[", ".").split("."):
        if segment:
            parts.append(segment.replace("~", "~0").replace("/", "~1"))
    return "/" + "/".join(parts)


def _facade_validation_failure(operation: str, params: Mapping[str, Any], fields: Sequence[str]) -> ValidationFailure:
    schema = _facade_schema(operation)
    diagnostics = []
    for field in fields:
        path = f"$.{field}"
        diagnostics.append({
            "code": f"{operation}_invalid",
            "phase": "payload",
            "path": path,
            "json_pointer": _json_pointer(path),
            "message": f"unsupported {operation} field {field!r}",
            "received": params.get(field),
            "expected": "omit this field; use only the documented properties below",
            "field_schema": schema,
            "fix": f"Remove {path} and retry {operation} with the same valid fields.",
        })
    return ValidationFailure(diagnostics)


def _facade_required_failure(operation: str, params: Mapping[str, Any]) -> ValidationFailure | None:
    """Collect missing required properties before any context lookup or write."""
    schema = _facade_schema(operation)
    diagnostics: list[dict[str, Any]] = []
    required_fields = list(schema.get("required", []))
    # The public completion form has two valid branches: a normal semantic
    # result and a PATCH-only planner repair.  The schema expresses this with
    # oneOf, while this direct Python facade must enforce the same branch rule
    # before touching the bound attempt or ledger.
    if operation == "complete_attempt" and not {
        "base_payload_digest", "patches",
    }.issubset(params):
        required_fields.extend(field for field in ("status", "summary") if field not in required_fields)
    for field in required_fields:
        value = params.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            path = f"$.{field}"
            diagnostics.append({
                "code": f"{operation}_invalid",
                "phase": "payload",
                "path": path,
                "json_pointer": _json_pointer(path),
                "message": f"required field {field!r} is missing or empty",
                "received": value,
                "expected": schema.get("properties", {}).get(field, {"type": "string"}),
                "field_schema": schema,
                "fix": f"Provide {path} using the exact value from the worker briefing, then retry {operation}.",
            })
    return ValidationFailure(diagnostics) if diagnostics else None


def _coordinator_continuation(
    task_dir: Any,
    state: Mapping[str, Any],
    source: Mapping[str, Any],
    result_ref: str,
    *,
    lifecycle_status: object,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return the sole server-derived success payload for the current wave.

    A coordinator may read historical predecessor results at any time. Only a
    finalized result owned by an active slot is safe to turn into a public
    continuation payload: deriving the relative step from the current plan
    avoids an LLM inferring the next wave number or copying a view reference.
    """
    if lifecycle_status != attempt_protocol.LIFECYCLE_COMPLETED:
        return None, "attempt_result_not_finalized"
    if source.get("invalidated"):
        return None, "attempt_result_not_current"
    plan = _runtime._load_orchestrate_plan(task_dir, dict(state))
    wave = _runtime._wave_for_gates(plan, _runtime.active_gates(dict(state)))
    if not isinstance(wave, Mapping):
        return None, "no_active_wave"
    # ``attempt_ids`` is the server-owned slot order.  In particular, do not
    # turn the result that happened to be read last into a singleton advance
    # for a parallel wave.  Every member must have its own final canonical
    # result and its own dispatch identity before the coordinator receives one
    # atomic continuation payload.
    wave_attempt_ids = [
        str(item).strip()
        for item in (wave.get("attempt_ids") or [])
        if str(item).strip()
    ]
    if not wave_attempt_ids:
        return None, "active_wave_attempts_unavailable"
    if len(set(wave_attempt_ids)) != len(wave_attempt_ids):
        return None, "active_wave_attempt_identity_conflict"
    source_attempt_id = str(source.get("attempt_id") or "").strip()
    if not source_attempt_id or source_attempt_id not in wave_attempt_ids:
        return None, "attempt_result_not_current"
    if source.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
        return None, "attempt_result_not_current"
    attempts = {
        str(candidate.get("attempt_id") or "").strip(): candidate
        for candidate in state.get("attempts") or []
        if isinstance(candidate, Mapping)
    }
    wave_results: list[dict[str, Any]] = []
    result_refs: list[str] = []
    dispatch_refs: list[str] = []
    root = _runtime._task_document_root(task_dir, state["task_id"])
    for slot, attempt_id in enumerate(wave_attempt_ids, 1):
        attempt = attempts.get(attempt_id)
        if (
            not isinstance(attempt, Mapping)
            or attempt.get("invalidated")
            or attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES
        ):
            return None, "parallel_wave_attempt_not_current"
        dispatch_ref = str(attempt.get("dispatch_ref") or "").strip()
        result_ref = str(attempt.get("attempt_result_ref") or "").strip()
        if not dispatch_ref or not result_ref:
            return None, "parallel_wave_results_pending"
        canonical = attempt_protocol.get_attempt_result(
            root, task_id=str(state["task_id"]), attempt_id=attempt_id,
        )
        if (
            canonical is None
            or str(canonical.get("attempt_id") or "") != attempt_id
            or str(canonical.get("result_ref") or "") != result_ref
            or canonical.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
        ):
            return None, "parallel_wave_results_pending"
        dispatch_refs.append(dispatch_ref)
        result_refs.append(result_ref)
        result: dict[str, Any] = {"attempt_result_ref": result_ref}
        if len(wave_attempt_ids) > 1:
            result["worker"] = slot
        wave_results.append(result)
    if len(set(dispatch_refs)) != len(dispatch_refs):
        return None, "parallel_wave_dispatch_identity_conflict"
    if len(set(result_refs)) != len(result_refs):
        return None, "parallel_wave_result_identity_conflict"
    wave_match = _runtime.re.search(r"(\d+)$", str(wave.get("wave_id") or ""))
    if wave_match is None:
        return None, "active_step_unavailable"
    return {
        "task_id": str(state["task_id"]),
        "step": int(wave_match.group(1)),
        "results": wave_results,
    }, None


def _coordinator_terminal_continuation(
    task_dir: Any,
    state: Mapping[str, Any],
    source: Mapping[str, Any],
    result_ref: str,
    *,
    lifecycle_status: object,
    result_view: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Return the exact non-success receipt for a terminal current wave.

    A blocked/failed AttemptResult is already terminal evidence, but it is not
    a successful ``continuation.results`` item and therefore cannot be exposed
    through the success-only helper above.  When the entire current wave is
    terminal, derive the non-success result set from the immutable attempt
    identities so the coordinator can advance to the server-owned blocked or
    failed state without waiting for a child that no longer exists.
    """
    status_by_lifecycle = {
        attempt_protocol.LIFECYCLE_BLOCKED: "blocked",
        attempt_protocol.LIFECYCLE_FAILED: "failed",
    }
    if lifecycle_status not in status_by_lifecycle or source.get("invalidated"):
        return None, "attempt_result_not_terminal_current"
    plan = _runtime._load_orchestrate_plan(task_dir, dict(state))
    wave = _runtime._wave_for_gates(plan, _runtime.active_gates(dict(state)))
    if not isinstance(wave, Mapping):
        return None, "no_active_wave"
    wave_attempt_ids = [
        str(item).strip()
        for item in (wave.get("attempt_ids") or [])
        if str(item).strip()
    ]
    source_attempt_id = str(source.get("attempt_id") or "").strip()
    if not wave_attempt_ids or source_attempt_id not in wave_attempt_ids:
        return None, "attempt_result_not_current"
    if len(set(wave_attempt_ids)) != len(wave_attempt_ids):
        return None, "active_wave_attempt_identity_conflict"
    attempts = {
        str(candidate.get("attempt_id") or "").strip(): candidate
        for candidate in state.get("attempts") or []
        if isinstance(candidate, Mapping)
    }
    root = _runtime._task_document_root(task_dir, state["task_id"])
    terminal_results: list[dict[str, Any]] = []
    for slot, attempt_id in enumerate(wave_attempt_ids, 1):
        attempt = attempts.get(attempt_id)
        if not isinstance(attempt, Mapping) or attempt.get("invalidated"):
            return None, "parallel_wave_attempt_not_current"
        dispatch_ref = str(attempt.get("dispatch_ref") or "").strip()
        attempt_result_ref = str(attempt.get("attempt_result_ref") or "").strip()
        if not dispatch_ref or not attempt_result_ref:
            return None, "parallel_wave_results_pending"
        canonical = attempt_protocol.get_attempt_result(
            root, task_id=str(state["task_id"]), attempt_id=attempt_id,
        )
        if (
            canonical is None
            or str(canonical.get("result_ref") or "") != attempt_result_ref
            or canonical.get("lifecycle_status") not in {
                attempt_protocol.LIFECYCLE_COMPLETED,
                attempt_protocol.LIFECYCLE_BLOCKED,
                attempt_protocol.LIFECYCLE_FAILED,
            }
        ):
            return None, "parallel_wave_results_pending"
        result_status = str(canonical.get("status") or canonical.get("result_status") or "").strip().lower()
        item: dict[str, Any] = {}
        if result_status == "completed":
            item["attempt_result_ref"] = attempt_result_ref
        elif result_status in {"blocked", "failed"}:
            semantic = canonical
            summary = str(semantic.get("summary") or "").strip()
            unresolved = semantic.get("unresolved") if isinstance(semantic.get("unresolved"), list) else []
            reason = summary or "; ".join(
                str(entry.get("summary") or entry.get("message") or entry)
                if isinstance(entry, Mapping) else str(entry)
                for entry in unresolved
            ).strip()
            if not reason and attempt_id == source_attempt_id:
                result_payload = result_view.get("result") if isinstance(result_view.get("result"), Mapping) else {}
                reason = str(result_payload.get("summary") or "").strip()
            item.update({
                "status": result_status,
                "dispatch_ref": dispatch_ref,
                "reason": reason or f"worker attempt {attempt_id} ended {result_status}",
            })
        else:
            return None, "attempt_result_status_unavailable"
        if len(wave_attempt_ids) > 1:
            item["worker"] = slot
        terminal_results.append(item)
    if not any(str(item.get("status") or "") in {"blocked", "failed"} for item in terminal_results):
        return None, "attempt_result_not_terminal_current"
    wave_match = _runtime.re.search(r"(\d+)$", str(wave.get("wave_id") or ""))
    if wave_match is None:
        return None, "active_step_unavailable"
    return {
        "task_id": str(state["task_id"]),
        "step": int(wave_match.group(1)),
        "results": terminal_results,
    }, None


def _worker_context(params: Mapping[str, Any]) -> tuple[Any, Any, dict[str, Any], dict[str, Any], str]:
    binding = require_binding()
    project = _runtime.select_project_root({"project_root": binding["project_root"]})
    task_id = _runtime.safe_id(binding["task_id"])
    attempt_id = _runtime.safe_id(binding["attempt_id"])
    profile = _runtime.canonical_profile(binding["profile"])
    _, task_dir, state = _runtime.load_state(task_id, {"project_root": str(project)})
    attempt = _runtime._attempt(state, attempt_id)
    if attempt.get("invalidated"):
        raise ValueError("attempt is invalidated")
    if attempt.get("profile") != profile:
        raise ValueError("profile does not match the exact dispatched worker")
    if binding.get("dispatch_ref") and str(attempt.get("dispatch_ref") or "") != binding["dispatch_ref"]:
        raise ValueError("worker binding dispatch_ref does not match the exact dispatched worker")
    if binding.get("briefing_digest") and str(attempt.get("briefing_digest") or "").lower() != binding["briefing_digest"].lower():
        raise ValueError("worker binding briefing_digest does not match the exact dispatched worker")
    if attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
        existing = attempt_protocol.get_attempt_result(
            _runtime.ledger_root({"project_root": str(project)}),
            task_id=task_id,
            attempt_id=attempt_id,
        )
        if existing is None:
            raise ValueError("attempt is not active and has no canonical result")
    return project, task_dir, state, attempt, profile


def _public_failure(operation: str, exc: Exception, *, finalization: bool = False) -> dict[str, Any]:
    message = _runtime.redact(str(exc), 1000)
    if isinstance(exc, WorkerBindingError) and not finalization:
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": "needs_input",
            "code": f"{operation}_unavailable",
            "diagnostics": [{
                "code": f"{operation}_unavailable",
                "path": "$",
                "json_pointer": "",
                "message": message,
                "field_schema": {"type": "object"},
                "fix": "Preserve this server-owned worker-session diagnostic; it cannot be repaired by changing tool arguments.",
            }],
            "retryable": False,
            "attempt_budget_consumed": False,
            "worker_replacement_authorized": False,
            "next_action": "Keep the same task resumable and use the server-owned worker-session recovery action; do not create a replacement worker.",
        }
    code = "attempt_finalization_pending" if finalization else f"{operation}_invalid"
    collected = getattr(exc, "diagnostics", None)
    diagnostics = collected if isinstance(collected, list) and collected else [{"code": code, "path": "$", "message": message}]
    # Do not leak the coordinator-only routing lock for caller-correctable
    # worker payloads.  The shared runtime helper supplies a concrete retry
    # operation; its contract is supplemented with the exact tool schema.
    for item in diagnostics:
        if isinstance(item, dict) and not finalization:
            if item.get("path"):
                item.setdefault("json_pointer", _json_pointer(item["path"]))
            item.setdefault("phase", "payload")
            item.setdefault("fix", f"Correct {item.get('path', '$')} and retry {operation} on the same attempt.")
            item.setdefault("field_schema", _facade_schema(operation) if operation in _FACADE_FIELDS else {"type": "object"})
    result = {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "outcome": "finalization_pending" if finalization else "needs_correction",
        "code": code,
        "diagnostics": diagnostics,
        "retryable": True,
        "attempt_budget_consumed": False,
        "worker_replacement_authorized": False,
        "next_action": (
            "Retry complete_attempt on this same completed attempt; Cortex retained the canonical AttemptResult and events. "
            "Do not spawn, request, or authorize a replacement worker."
            if finalization else
            _runtime._validation_next_action(operation, diagnostics)
            if hasattr(_runtime, "_validation_next_action") else
            f"Correct every listed diagnostic path in the same {operation} request and retry this same attempt."
        ),
    }
    if not finalization:
        result["validation"] = {
            "schema": "cortex/validation-error/v1",
            "operation": operation,
            "diagnostics_are_complete": True,
            "request_schema": _facade_schema(operation) if operation in _FACADE_FIELDS else {"type": "object"},
            "invalid_paths": [item.get("path") for item in diagnostics if isinstance(item, dict) and item.get("path")],
            "invalid_json_pointers": [item.get("json_pointer") for item in diagnostics if isinstance(item, dict) and item.get("json_pointer") is not None],
            "retry": {"same_call": True, "preserve_valid_fields": True, "replacement_worker_authorized": False},
        }
    return result


def _planning_repair_failure(response: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    """Attach the server-owned repair contract to a rejected planner draft.

    The rejected draft is immutable and already contains every field that
    passed persistence-level copying.  Returning only its digest and the
    diagnostic paths prevents the coordinator from regenerating a full plan
    and accidentally rewriting valid packages, paths, or verification data.
    """
    # A rejected draft is immutable and is the authoritative source of the
    # planning validation contract.  If the caller accidentally resubmits the
    # full planning object, the boundary error is intentionally generic
    # (PATCH-only is required), but that generic error must not replace the
    # original field-level diagnostics.  Replaying the draft diagnostics keeps
    # the retry actionable and prevents a second regeneration loop.
    draft_diagnostics = draft.get("diagnostics")
    if isinstance(draft_diagnostics, list) and draft_diagnostics:
        diagnostics = [dict(item) for item in draft_diagnostics if isinstance(item, dict)]
        if diagnostics:
            # Draft diagnostics are replayed from the immutable document, so
            # apply the same public receipt normalization that _public_failure
            # applies to a fresh exception.  This keeps retries machine-
            # readable even after the first response has been persisted.
            for item in diagnostics:
                path = item.get("path")
                if path:
                    item.setdefault("json_pointer", _json_pointer(path))
                item.setdefault("phase", "payload")
                item.setdefault(
                    "fix",
                    f"Correct {path or '$'} and retry complete_attempt on the same attempt.",
                )
                item.setdefault("field_schema", _facade_schema("complete_attempt"))
            response["diagnostics"] = diagnostics
    else:
        diagnostics = response.get("diagnostics") or []
    response["base_payload_digest"] = draft.get("base_payload_digest")
    response["rejected_draft_ref"] = f"planning_rejected_draft:{draft.get('attempt_id', '')}"
    response["planning_repair"] = {
        "mode": "same_attempt_patch",
        "base_payload_digest": draft.get("base_payload_digest"),
        "diagnostic_paths": [
            item.get("path") for item in diagnostics
            if isinstance(item, dict) and item.get("path")
        ],
        "patch_paths": _runtime.planning_diagnostic_patch_paths(diagnostics),
        "preserve_other_fields": True,
        "replacement_worker_authorized": False,
        "coordinator_must_not": [
            "regenerate or resend the full planning object",
            "perform project inspection or edits",
            "spawn, request, or authorize a replacement worker",
        ],
        "instruction": (
            "Use complete_attempt on this same attempt with base_payload_digest and JSON patches only. "
            "Use planning_repair.patch_paths as the RFC6901 JSON Pointer path source. "
            "Patch only those paths; all other rejected-draft fields are retained server-side."
        ),
    }
    patch_paths = _runtime.planning_diagnostic_patch_paths(diagnostics)
    path_hint = (
        " Exact PATCH paths: " + ", ".join(patch_paths) + "."
        if patch_paths else
        " Use only the diagnostic-scoped paths returned by planning_repair.patch_paths."
    )
    response["next_action"] = (
        "Call repair_planning on this same planner attempt with base_payload_digest copied exactly from "
        "the rejected draft and patches containing only the returned planning_repair.patch_paths. "
        "Do not resend the full planning object, inspect or modify the project, or spawn/request/authorize "
        "a replacement worker; the server preserves every valid rejected-draft field."
        + path_hint
    )
    return response


def record_attempt_event(params: dict[str, Any]) -> dict[str, Any]:
    """Persist one bounded semantic checkpoint for the active worker."""
    try:
        original = dict(params)
        params = bind_semantic_params(original)
        allowed = {"event_type", "payload", "event_key"}
        unknown = sorted(set(original) - allowed)
        if unknown:
            raise _facade_validation_failure("record_attempt_event", params, unknown)
        required_failure = _facade_required_failure("record_attempt_event", params)
        if required_failure:
            raise required_failure
        project, _task_dir, state, attempt, profile = _worker_context(params)
        if attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
            raise ValueError("attempt event stream is closed")
        event_type = str(params.get("event_type") or "").strip()
        if "payload" not in params:
            raise ValueError("payload is required")
        result = attempt_protocol.record_attempt_event(
            _runtime.ledger_root({"project_root": str(project)}),
            task_id=state["task_id"],
            attempt_id=str(attempt["attempt_id"]),
            event_type=event_type,
            payload=params["payload"],
            event_key=(str(params.get("event_key") or "").strip() or None),
        )
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "attempt_event_recorded",
            "event_ref": result["event"]["event_ref"],
            "event_type": result["event"]["event_type"],
            "attempt_id": attempt["attempt_id"],
            "profile": profile,
            "idempotent": bool(result.get("idempotent")),
            "next_action": "Continue this attempt; checkpoint another material fact when useful, then call complete_attempt once.",
        }
    except (ValueError, TypeError, OSError) as exc:
        return _public_failure("record_attempt_event", exc)


def _path_is_allowed(path: str, allowed_paths: Sequence[object]) -> bool:
    for raw in allowed_paths:
        allowed = str(raw or "").strip().rstrip("/")
        if allowed == "." or path == allowed or (allowed and path.startswith(allowed + "/")):
            return True
    return False


def _workspace_observation(project: Any, task_dir: Any, state: Mapping[str, Any], attempt: Mapping[str, Any]) -> dict[str, Any]:
    baseline = _runtime.attempt_manifest_baseline(task_dir, dict(attempt))
    current = _runtime.capture_project_manifest(project, policy=baseline.get("policy"))
    comparison = _runtime.compare_manifests(baseline, current)
    changed = list(comparison.get("changed_paths") or [])
    allowed = attempt.get("allowed_paths") if isinstance(attempt.get("allowed_paths"), list) else []
    peers = [
        item for item in state.get("attempts", [])
        if isinstance(item, dict)
        and item.get("attempt_id") != attempt.get("attempt_id")
        and not item.get("invalidated")
        and item.get("status") in _ACTIVE_ATTEMPT_STATUSES
    ]
    peer_overlap = any(
        any(_path_is_allowed(path, peer.get("allowed_paths") or []) for path in changed)
        for peer in peers
    )
    safe = bool(comparison.get("complete")) and not peer_overlap and all(
        _path_is_allowed(path, allowed) for path in changed
    )
    return {
        "baseline_ref": attempt.get("result_baseline_ref"),
        "current_digest_sha256": str(current.get("digest") or ""),
        "complete": bool(comparison.get("complete")),
        "safe_to_attribute": safe,
        "changed_files": changed if safe else [],
    }


def _receipt_guard(root: Any, state: Mapping[str, Any], attempt: Mapping[str, Any]) -> dict[str, Any]:
    receipts = attempt_protocol.attempt_receipts(
        root,
        task_id=str(state["task_id"]),
        attempt_id=str(attempt["attempt_id"]),
    )
    missing: list[str] = []
    if not receipts.get("briefing_receipt"):
        missing.append("briefing_read")
    required = {str(item) for item in attempt.get("context_result_refs") or []}
    observed = {str(item) for item in (receipts.get("predecessor_receipts") or {})}
    missing.extend("predecessor:" + item for item in sorted(required - observed))
    if missing:
        raise ValueError("server read receipts are incomplete: " + ", ".join(missing))
    return receipts


def _mark_attempt(
    project: Any,
    task_id: str,
    attempt_id: str,
    *,
    lifecycle_status: str,
    result: Mapping[str, Any],
    terminal_status: str | None = None,
    finalization_error: str | None = None,
) -> None:
    """Project one canonical result without leaving its native worker live.

    The task projection intentionally remains ``running`` until the
    coordinator accepts the exact result through ``continue_orchestration``.
    That is a gate-consumption state, not evidence that the native worker is
    still runnable.  Terminal AttemptResult lifecycles therefore reconcile the
    server-owned worker-session row here, before the result can be surfaced to
    the coordinator.  A missing session fails closed instead of manufacturing
    a host identity or allowing a stale ``awaiting_spawn``/``running`` row to
    survive a terminal result.
    """
    root = _runtime.ledger_root({"project_root": str(project)})
    with _runtime.state_lock(root):
        _, task_dir, state = _runtime.load_state(task_id, {"project_root": str(project)})
        attempt = _runtime._attempt(state, attempt_id)
        result_lifecycle = str(result.get("lifecycle_status") or "").upper()
        terminal_lifecycles = {
            attempt_protocol.LIFECYCLE_COMPLETED,
            attempt_protocol.LIFECYCLE_BLOCKED,
            attempt_protocol.LIFECYCLE_FAILED,
        }
        if result_lifecycle in terminal_lifecycles:
            terminal_at = (
                result.get("completed_at")
                or result.get("work_completed_at")
                or _runtime.now()
            )
            _runtime.db_reconcile_terminal_worker_session(
                root,
                task_id=state["task_id"],
                attempt_id=attempt_id,
                terminated_at=str(terminal_at),
            )
            # Do not assert an observed SubagentStop: a host stop hook may
            # arrive later. This state records only the server-side terminal
            # reconciliation that prevents a durable session from being
            # recovered as live work.
            attempt["worker_session_reconciled_at"] = str(terminal_at)
            attempt["worker_session_terminal_status"] = "completed"
        attempt["lifecycle_status"] = lifecycle_status
        attempt["attempt_result_ref"] = result.get("result_ref")
        attempt["work_completed_at"] = result.get("work_completed_at")
        attempt["host_resumable"] = False
        if finalization_error:
            attempt["finalization_error_code"] = finalization_error
        else:
            attempt.pop("finalization_error_code", None)
        if terminal_status:
            attempt["status"] = terminal_status
            attempt["finalized_at"] = _runtime.now()
            attempt["finalization_reason"] = f"semantic_attempt_{terminal_status}"
        _runtime.save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "attempt_result_state",
            f"{attempt_id}: {lifecycle_status}",
        )


def complete_attempt(params: dict[str, Any]) -> dict[str, Any]:
    """Persist semantic completion and finalize its canonical result view."""
    project: Any = None
    state: dict[str, Any] | None = None
    attempt: dict[str, Any] | None = None
    root: Any = None
    rejected_draft: dict[str, Any] | None = None
    try:
        original = dict(params)
        params = bind_semantic_params(original)
        allowed = {
            "status", "summary", "findings", "decisions_needed", "unresolved", "claims",
            "planning", "base_payload_digest", "patches",
            # Private server-to-server handoff used only after a validated
            # diagnostic-scoped patch has been applied.  It is not part of
            # the public MCP schema and cannot be supplied by the worker.
            "_validated_planning_repair",
        }
        unknown = sorted(set(original) - allowed)
        if unknown:
            raise _facade_validation_failure("complete_attempt", params, unknown)
        required_failure = _facade_required_failure("complete_attempt", params)
        if required_failure:
            raise required_failure
        project, task_dir, state, attempt, profile = _worker_context(params)
        if "base_payload_digest" in params or "patches" in params:
            return repair_planning(params, _trusted=True)
        plan_attempt = profile == "planner" and str(attempt.get("gate") or "") == "plan"
        if "planning" in params and not plan_attempt:
            raise ValueError("planning is supported only for planner attempts on the plan gate")
        # Validate the planner-only sibling before any receipt or canonical
        # completion work.  A malformed/missing plan must remain a correction
        # on this attempt, never a partially committed AttemptResult.
        normalized_planning = None
        if plan_attempt:
            if "planning" not in params:
                current = _runtime.current_planning_manifest(task_dir)
                if not isinstance(current, dict):
                    raise ValueError("planner plan attempts require a planning payload")
            else:
                # Once a rejected draft exists, the planner must not submit a
                # second full object—even if that object happens to validate.
                # The server-owned draft is the base for a same-attempt PATCH
                # and is the only place from which valid fields may be reused.
                existing_draft = _runtime.get_planning_rejected_draft(
                    task_dir, state["task_id"], attempt["attempt_id"],
                )
                if isinstance(existing_draft, dict) and not params.get("_validated_planning_repair"):
                    rejected_draft = existing_draft
                    raise ValueError(
                        "planner rejected draft requires PATCH-only repair; omit the full planning object"
                    )
                try:
                    normalized_planning = _runtime.sanitize_planning_payload(
                        params["planning"], persisted=True,
                    )
                except _runtime.PlanningValidationError as exc:
                    raise
        root = _runtime.ledger_root({"project_root": str(project)})
        _receipt_guard(root, state, attempt)
        if str(params.get("status") or "").strip().lower() == "completed":
            missing_artifacts = _runtime.required_artifact_diagnostics(
                project, task_dir, state, attempt,
            )
            if missing_artifacts:
                # Keep the attempt and all prior valid evidence untouched. The
                # worker must materialize the exact declared paths and retry
                # this same attempt; no replacement worker is authorized.
                raise ValidationFailure(missing_artifacts)
        semantic_result = {
            "status": params.get("status"),
            "summary": params.get("summary"),
            "findings": params.get("findings", []),
            "decisions_needed": params.get("decisions_needed", []),
            "unresolved": params.get("unresolved", []),
            "claims": params.get("claims", []),
        }
        existing = attempt_protocol.get_attempt_result(
            root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
        )
        observation = (
            {
                **existing["workspace_observation"],
                "changed_files": existing.get("changed_files") or [],
            }
            if existing is not None else
            _workspace_observation(project, task_dir, state, attempt)
        )
        # Every retry, including planner materialization retries, must pass
        # through the protocol's immutable semantic comparison.  A corrected
        # planning sibling may be materialized, but it may not smuggle a new
        # AttemptResult into an already completed attempt.
        completed = attempt_protocol.complete_attempt(
            root,
            task_id=state["task_id"],
            attempt_id=attempt["attempt_id"],
            result=semantic_result,
            submission_id=(existing.get("submission_id") if existing else None),
            workspace_observation=observation,
        )
        canonical = completed["result"]
        terminal = canonical.get("result_status") or canonical["status"]
        if terminal != "completed":
            _mark_attempt(
                project, state["task_id"], attempt["attempt_id"],
                lifecycle_status=canonical["lifecycle_status"].lower(),
                result=canonical,
                terminal_status=terminal,
            )
            return {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "outcome": f"attempt_{terminal}",
                "attempt_result_ref": canonical["result_ref"],
                "dispatch_ref": attempt.get("dispatch_ref"),
                "status": terminal,
                "summary": canonical["summary"],
                "idempotent": bool(completed.get("idempotent")),
                "worker_replacement_authorized": False,
                "next_action": "Return this semantic non-success to the coordinator; Cortex retained the attempt facts.",
            }
        if plan_attempt:
            if normalized_planning is not None:
                _runtime.materialize_planning_payload(
                    task_dir,
                    state,
                    attempt,
                    str(canonical["result_ref"]),
                    normalized_planning,
                )
            else:
                current = _runtime.current_planning_manifest(task_dir)
                if not isinstance(current, dict) or current.get("source_result_ref") != canonical.get("result_ref"):
                    raise ValueError("planner plan attempts require a planning payload")
        _mark_attempt(
            project, state["task_id"], attempt["attempt_id"],
            lifecycle_status="work_completed",
            result=canonical,
        )
        if canonical["lifecycle_status"] == attempt_protocol.LIFECYCLE_WORK_COMPLETED:
            attempt_protocol.begin_attempt_finalization(
                root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
            )
        projection = attempt_protocol.build_attempt_result_view(
            root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
        )
        finalized = attempt_protocol.finalize_attempt(
            root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
        )["result"]
        _mark_attempt(
            project, state["task_id"], attempt["attempt_id"],
            lifecycle_status="result_finalized",
            result=finalized,
        )
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "attempt_completed",
            # Put the bearer lookup token first and label the generated view
            # separately.  Native parents frequently serialize this object
            # verbatim; keeping the canonical token first reduces accidental
            # selection of the non-authoritative projection ref while the
            # prompt/schema still require the field name to be copied exactly.
            "attempt_result_ref": canonical["result_ref"],
            "projection_ref": projection["projection_ref"],
            "summary": canonical["summary"],
            "idempotent": bool(completed.get("idempotent")),
            "worker_replacement_authorized": False,
            "next_action": "Return only ATTEMPT_COMPLETED, attempt_result_ref, and at most a two-sentence summary to the coordinator.",
        }
    except (ValueError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        if isinstance(exc, attempt_protocol.CanonicalResultConflict):
            return {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": False,
                "outcome": "canonical_result_conflict",
                "code": "attempt_canonical_result_conflict",
                "diagnostics": exc.diagnostics,
                "retryable": False,
                "attempt_budget_consumed": False,
                "worker_replacement_authorized": False,
                "next_action": "Read the existing canonical AttemptResult by result_ref; do not resubmit the changed payload and do not spawn a replacement worker.",
            }
        # Planning validation is deliberately persisted before returning the
        # correction response.  This is a draft only: no AttemptResult,
        # worker replacement, or mutable planning pointer is created.
        if (
            state is not None and attempt is not None and project is not None
            and profile == "planner" and str(attempt.get("gate") or "") == "plan"
            and "planning" in params
            and isinstance(exc, _runtime.PlanningValidationError)
            and not (root is not None and attempt_protocol.get_attempt_result(root, task_id=state["task_id"], attempt_id=attempt["attempt_id"]))
        ):
            try:
                diagnostics = getattr(exc, "diagnostics", None)
                if not isinstance(diagnostics, list) or not diagnostics:
                    diagnostics = [{"code": "planning_validation_failed", "message": _runtime.redact(str(exc), 1000), "path": "planning"}]
                try:
                    rejected_draft = _runtime.planning_rejected_draft_document(
                        task_dir, state, attempt, params["planning"], diagnostics,
                        {key: params.get(key) for key in ("status", "summary", "findings", "decisions_needed", "unresolved", "claims")},
                    )
                except ValueError as draft_error:
                    # The first rejected draft is immutable.  A model retry
                    # that accidentally regenerates the full object must get
                    # the original repair contract back, never a bare digest
                    # mismatch that starts another regeneration loop.
                    if "immutable" not in str(draft_error):
                        raise
                    rejected_draft = _runtime.get_planning_rejected_draft(
                        task_dir, state["task_id"], attempt["attempt_id"],
                    )
            except (ValueError, TypeError, OSError, RuntimeError):
                pass
        if root is not None and state is not None and attempt is not None:
            existing = attempt_protocol.get_attempt_result(
                root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
            )
            if existing is not None and str(existing.get("result_status") or existing.get("status") or "") == "completed":
                try:
                    attempt_protocol.record_finalization_failure(
                        root,
                        task_id=state["task_id"],
                        attempt_id=attempt["attempt_id"],
                        reason_code="generated_projection_failed",
                    )
                    _mark_attempt(
                        project, state["task_id"], attempt["attempt_id"],
                        lifecycle_status="work_completed",
                        result=existing,
                        finalization_error="generated_projection_failed",
                    )
                except (ValueError, OSError, RuntimeError):
                    pass
                response = _public_failure("complete_attempt", exc, finalization=True)
                return _planning_repair_failure(response, rejected_draft) if rejected_draft else response
        response = _public_failure("complete_attempt", exc)
        return _planning_repair_failure(response, rejected_draft) if rejected_draft else response


def repair_planning(params: dict[str, Any], *, _trusted: bool = False) -> dict[str, Any]:
    """Repair a rejected planner draft with diagnostic-scoped JSON patches."""
    draft: dict[str, Any] | None = None
    try:
        original = dict(params)
        if not _trusted:
            params = bind_semantic_params(original)
        allowed = {"base_payload_digest", "patches", "planning", "status", "summary", "findings", "decisions_needed", "unresolved", "claims"}
        unknown = sorted(set(original) - (allowed | (_PUBLIC_IDENTITY_FIELDS if _trusted else set())))
        if unknown:
            raise _facade_validation_failure("repair_planning", params, unknown)
        required_failure = _facade_required_failure("repair_planning", params)
        if required_failure:
            raise required_failure
        project, task_dir, state, attempt, profile = _worker_context(params)
        if profile != "planner" or str(attempt.get("gate") or "") != "plan":
            raise ValueError("repair_planning is supported only for planner attempts on the plan gate")
        draft = _runtime.get_planning_rejected_draft(task_dir, state["task_id"], attempt["attempt_id"])
        if not isinstance(draft, dict):
            raise ValueError("no rejected planning draft exists for this attempt")
        if str(params.get("base_payload_digest") or "") != str(draft.get("base_payload_digest") or ""):
            raise ValueError("planning_correction_digest_mismatch")
        if "planning" in params:
            raise ValueError(
                "planning repair is PATCH-only; omit the full planning object and send patches for diagnostic paths"
            )
        if "patches" not in params:
            raise ValueError("repair_planning requires patches")
        patches = params["patches"]
        if not isinstance(patches, list) or not patches:
            raise ValueError("patches must be a non-empty array")
        patch_paths = [str(item.get("path") or "") for item in patches if isinstance(item, dict)]
        if len(patch_paths) != len(patches) or not _runtime.planning_diagnostic_scope_allows(
            draft.get("diagnostics") or [], patch_paths,
        ):
            raise ValueError(
                "planning_correction_scope_violation: every patch path must target a returned diagnostic path"
            )
        repaired = _runtime.apply_planning_repair(draft, patches)
        normalized = _runtime.sanitize_planning_payload(repaired, persisted=True)
        semantic = {key: params[key] if key in params else draft.get("result_payload", {}).get(key) for key in ("status", "summary", "findings", "decisions_needed", "unresolved", "claims")}
        completion_params = {
            **semantic,
            "planning": normalized,
            "_validated_planning_repair": True,
        }
        response = complete_attempt(completion_params)
        if response.get("ok") is True:
            # Store only the repair shape alongside the already-materialized
            # current projection.  This is written after canonical completion,
            # so a validation failure can never leave a partial repair record.
            try:
                current = _runtime.current_planning_manifest(task_dir)
                if isinstance(current, dict):
                    current["repair"] = {
                        "mode": "same_attempt_patch",
                        "patch_count": len(patches),
                        "patch_paths": list(patch_paths),
                    }
                    _runtime.db_put_task_document(
                        _runtime._task_document_root(task_dir, state["task_id"]),
                        state["task_id"], "planning_current", current,
                    )
            except (ValueError, OSError, RuntimeError):
                pass
        return response
    except (ValueError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        response = _public_failure("repair_planning", exc)
        return _planning_repair_failure(response, draft) if draft else response


def read_worker_result(params: dict[str, Any]) -> dict[str, Any]:
    """Read one canonical result view for the coordinator or assigned successor.

    The result reference is resolved against the selected task's attempt rows;
    no secondary artifact or editable body participates in
    authorization or recovery.
    """
    try:
        binding = current_binding()
        if binding is not None:
            original = dict(params)
            params = bind_semantic_params(original)
            unknown = sorted(set(original) - {"attempt_result_ref"})
            if unknown:
                raise _facade_validation_failure("read_worker_result", params, unknown)
            project = _runtime.select_project_root({"project_root": binding["project_root"]})
            task_id = _runtime.safe_id(binding["task_id"])
            _, task_dir, state = _runtime.load_state(task_id, {"project_root": str(project)})
            task_ref = str(binding.get("task_ref") or _runtime._v3_task_ref(state))
            worker_context = True
            raw_attempt_id = binding["attempt_id"]
            raw_profile = binding["profile"]
        else:
            allowed = {"task_ref", "attempt_result_ref"}
            unknown = sorted(set(params) - allowed)
            if unknown:
                raise _facade_validation_failure("read_worker_result", params, unknown)
            required_failure = _facade_required_failure("read_worker_result", params)
            if required_failure:
                raise required_failure
            bound_params = _runtime._bind_task_project_root(params, include_completed=True)
            if bound_params is None:
                raise ValueError(
                    "task_ref could not be resolved to one host-bound project root"
                )
            params = bound_params
            resolved = _runtime._v3_resolve_task(params, require_task_ref=True)
            if isinstance(resolved, dict):
                return resolved
            task_dir, state, _project, task_ref = resolved
            worker_context = False
            raw_attempt_id = ""
            raw_profile = ""
        result_ref = _runtime.safe_id(str(params.get("attempt_result_ref") or ""))
        if not result_ref:
            raise ValueError("attempt_result_ref is required")
        if not worker_context and bool(raw_attempt_id) != bool(raw_profile):
            raise ValueError("successor worker result reads require both attempt_id and profile")
        if worker_context:
            attempt_id = _runtime.safe_id(raw_attempt_id)
            attempt = _runtime._attempt(state, attempt_id)
            profile = _runtime.canonical_profile(raw_profile)
            if attempt.get("invalidated") or attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
                raise ValueError("successor worker result reads require an active, non-invalidated attempt")
            if attempt.get("profile") != profile:
                raise ValueError("successor worker profile does not match the delegated attempt")
            if binding and binding.get("dispatch_ref") and str(attempt.get("dispatch_ref") or "") != binding["dispatch_ref"]:
                raise ValueError("worker binding dispatch_ref does not match the exact dispatched worker")
            if binding and binding.get("briefing_digest") and str(attempt.get("briefing_digest") or "").lower() != binding["briefing_digest"].lower():
                raise ValueError("worker binding briefing_digest does not match the exact dispatched worker")
            allowed_refs = {str(item) for item in attempt.get("context_result_refs") or []}
            if result_ref not in allowed_refs:
                raise ValueError("successor worker may read only predecessor result refs supplied in its dispatch")
        source = next(
            (candidate for candidate in state.get("attempts") or []
             if str(candidate.get("attempt_result_ref") or "") == result_ref),
            None,
        )
        if not isinstance(source, Mapping):
            raise ValueError("attempt_result_ref does not belong to the selected Cortex task")
        root = _runtime._task_document_root(task_dir, state["task_id"])
        view = attempt_protocol.build_attempt_result_view(
            root, task_id=state["task_id"], attempt_id=str(source["attempt_id"]),
        )
        if view["attempt_result_ref"] != result_ref:
            raise ValueError("attempt_result_ref does not match its canonical result")
        continuation, continuation_unavailable_reason = _coordinator_continuation(
            task_dir,
            state,
            source,
            result_ref,
            lifecycle_status=view.get("lifecycle_status"),
        )
        terminal_continuation, terminal_unavailable_reason = _coordinator_terminal_continuation(
            task_dir,
            state,
            source,
            result_ref,
            lifecycle_status=view.get("lifecycle_status"),
            result_view=view,
        )
        result = {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "task_ref": task_ref,
            "attempt_result_ref": result_ref,
            "phase": view["phase"],
            "profile": view["producer"],
            "result_view": view,
            "complete": True,
        }
        if worker_context:
            result["predecessor_receipt"] = attempt_protocol.record_predecessor_read(
                root, task_id=state["task_id"], attempt_id=attempt_id, predecessor_result_ref=result_ref,
            )
            result["continuation_unavailable_reason"] = continuation_unavailable_reason
            result["next_action"] = (
                "Use this canonical predecessor result as scoped evidence context. Cortex recorded the complete "
                "read receipt; distinguish worker verification claims from server verification observations."
            )
        else:
            if terminal_continuation is not None:
                result["terminal_continuation"] = terminal_continuation
                result["next_action"] = (
                    "Copy terminal_continuation.task_id, terminal_continuation.step, and terminal_continuation.results "
                    "verbatim into continue_orchestration. This is a terminal blocked/failed receipt; do not wait, "
                    "respawn, replace, or fabricate a successful result."
                )
            elif continuation is not None:
                result["continuation"] = continuation
                result["next_action"] = (
                    "Keep the existing task_ref, verify continuation.task_id against this task, and copy "
                    "continuation.step and continuation.results verbatim into the next continue_orchestration call. "
                    "Do not increment step or use projection_ref, formatted ref text, dispatch_ref, reason, "
                    "next_strategy, or worker for this singleton success."
                )
            else:
                terminal_lifecycle = view.get("lifecycle_status") in {
                    attempt_protocol.LIFECYCLE_BLOCKED,
                    attempt_protocol.LIFECYCLE_FAILED,
                }
                result["continuation_unavailable_reason"] = (
                    terminal_unavailable_reason if terminal_lifecycle else continuation_unavailable_reason
                )
                result["next_action"] = (
                    "This result is not the finalized current active worker slot, so it cannot authorize a continuation. "
                    "Use it only as canonical read context and follow the active task state."
                )
        return result
    except (ValueError, TypeError, OSError) as exc:
        return _public_failure("read_worker_result", exc)
