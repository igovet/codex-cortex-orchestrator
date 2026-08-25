"""Worker-facing adapters for the database-centric attempt protocol.

The worker supplies semantic facts.  This facade resolves its already-issued
attempt, records the canonical result/event rows, verifies machine-side read
receipts, derives the workspace observation, and exposes only a regenerated
non-authoritative AttemptResult read view.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import cortex as _runtime

from cortex_runtime import attempt_protocol, ledger_db, pagination
from cortex_runtime.validation import ValidationFailure
from cortex_runtime import v11_submission
from cortex_runtime.public_contracts import backend_schema_for


_CLOSURE_GATES = {"review", "governance_activation", "governance_close", "close"}
_ACTIVE_ATTEMPT_STATUSES = {_runtime.AWAITING_HOST_SPAWN, "running"}
_WORKER_REFERENCE_FIELDS = {"dispatch_ref"}


def _facade_schema(operation: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Derive the backend view from the same action-specific MCP contract."""
    registry = getattr(_runtime, "PUBLIC_CONTRACTS", None)
    return backend_schema_for(registry, operation, params) if isinstance(registry, Mapping) else {
        "type": "object", "additionalProperties": False, "properties": {}, "required": [],
    }


def _compact_facade_schema(operation: str) -> dict[str, Any]:
    """Return a bounded public envelope for validation receipts.

    A worker must receive the invalid field's contract, never a private
    worker-session property.
    """
    return json.loads(json.dumps(_facade_schema(operation), ensure_ascii=False))


def _validation_request_schema(operation: str, diagnostics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Keep validation receipts bounded and free of worker-session fields."""
    del diagnostics
    return _compact_facade_schema(operation)


def _json_pointer(path: object) -> str:
    """Convert the public diagnostic path to an RFC 6901 pointer."""
    raw = str(path or "$")
    if raw.startswith("/"):
        return raw
    if raw == "$":
        return ""
    raw = raw[2:] if raw.startswith("$.") else raw.lstrip(".")
    raw = re.sub(r"\[([0-9]+)\]", r".\1", raw)
    parts: list[str] = []
    for segment in raw.split("."):
        if segment:
            parts.append(segment.replace("~", "~0").replace("/", "~1"))
    return "/" + "/".join(parts)


def _facade_field_schema(operation: str, pointer: str) -> dict[str, Any]:
    """Return the exact public leaf schema for one facade diagnostic."""
    return v11_submission.schema_for_path(_facade_schema(operation), pointer)


def _facade_diagnostic(
    operation: str,
    field: str,
    message: str,
    *,
    code: str | None = None,
    pointer: str | None = None,
    unknown: bool = False,
) -> dict[str, Any]:
    resolved_pointer = pointer or "/" + field.replace("~", "~0").replace("/", "~1")
    return {
        "code": code or f"{operation}_invalid",
        "phase": "payload",
        "path": f"$.{field}",
        "json_pointer": resolved_pointer,
        "message": message,
        "field_schema": (
            {"type": "object", "additionalProperties": False}
            if unknown else _facade_field_schema(operation, resolved_pointer)
        ),
    }


def _facade_validation_failure(operation: str, params: Mapping[str, Any], fields: Sequence[str]) -> ValidationFailure:
    diagnostics = []
    for field in fields:
        diagnostics.append(_facade_diagnostic(
            operation,
            field,
            f"unsupported {operation} field {field!r}",
            unknown=True,
        ))
    return ValidationFailure(diagnostics)


def _facade_required_failure(operation: str, params: Mapping[str, Any]) -> ValidationFailure | None:
    """Collect missing required properties before any context lookup or write."""
    schema = _facade_schema(operation, params)
    receipt_schema = _compact_facade_schema(operation)
    diagnostics: list[dict[str, Any]] = []
    required_fields = list(schema.get("required", []))
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
                "field_schema": receipt_schema.get("properties", {}).get(field, {"type": "string"}),
                "fix": f"Provide {path} using the exact value from the worker briefing, then retry {operation}.",
            })
    return ValidationFailure(diagnostics) if diagnostics else None


def _schema_preflight(operation: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Collect closed-form corrections from the selected canonical schema."""
    schema = _facade_schema(operation, params)
    properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    diagnostics = [
        _facade_diagnostic(
            operation,
            str(field),
            f"unsupported {operation} field {field!r}",
            code="validation_unknown",
            unknown=True,
        )
        for field in sorted(set(params) - set(properties))
    ]
    for field in schema.get("required", []) if isinstance(schema.get("required"), list) else []:
        if field not in params:
            diagnostics.append(_facade_diagnostic(
                operation, str(field), f"required field {field!r} is missing",
                code="validation_required",
            ))
    for field, value in params.items():
        field_schema = properties.get(field)
        if not isinstance(field_schema, Mapping):
            continue
        expected_type = field_schema.get("type")
        valid_type = (
            (expected_type == "string" and isinstance(value, str))
            or (expected_type == "integer" and type(value) is int)
            or (expected_type == "boolean" and type(value) is bool)
            or (expected_type == "array" and isinstance(value, list))
            or (expected_type == "object" and isinstance(value, Mapping))
        )
        if expected_type in {"string", "integer", "boolean", "array", "object"} and not valid_type:
            diagnostics.append(_facade_diagnostic(operation, str(field), f"{field} has an invalid type"))
            continue
        enum = field_schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            diagnostics.append(_facade_diagnostic(operation, str(field), f"{field} is outside the advertised enum"))
        if "const" in field_schema and value != field_schema["const"]:
            diagnostics.append(_facade_diagnostic(operation, str(field), f"{field} differs from the server-selected operation"))
        if isinstance(value, str):
            pattern = field_schema.get("pattern")
            minimum = field_schema.get("minLength")
            maximum = field_schema.get("maxLength")
            if isinstance(minimum, int) and len(value) < minimum:
                diagnostics.append(_facade_diagnostic(operation, str(field), f"{field} is shorter than the advertised minimum"))
            elif isinstance(maximum, int) and len(value) > maximum:
                diagnostics.append(_facade_diagnostic(operation, str(field), f"{field} exceeds the advertised maximum"))
            elif isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
                diagnostics.append(_facade_diagnostic(operation, str(field), f"{field} has an invalid format"))
    return diagnostics


def _complete_attempt_preflight(
    params: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Validate the selected completion form from its canonical MCP schema."""
    return _schema_preflight("complete_attempt", params)


def _record_attempt_event_preflight(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate the event form from its canonical MCP schema."""
    return _schema_preflight("record_attempt_event", params)


def _read_worker_result_preflight(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate the selected result read from its canonical MCP schema."""
    return _schema_preflight("read_worker_result", params)


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


def _worker_context(
    params: Mapping[str, Any], operation: str,
) -> tuple[Any, Any, dict[str, Any], dict[str, Any], str]:
    project, task_dir, state, attempt, profile = _runtime.authorize_worker_assignment(params, operation)
    task_id = _runtime.safe_id(str(state["task_id"]))
    attempt_id = _runtime.safe_id(str(attempt["attempt_id"]))
    if attempt.get("invalidated"):
        raise ValueError("attempt is invalidated")
    if attempt.get("profile") != profile:
        raise ValueError("profile does not match the exact dispatched worker")
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
    if isinstance(exc, _runtime.WorkerAssignmentError) and not finalization:
        pointer = "/dispatch_ref"
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": "needs_input",
            "code": "worker_dispatch_unavailable",
            "diagnostics": [{
                "code": "worker_dispatch_unavailable",
                "path": "$.dispatch_ref",
                "json_pointer": "/dispatch_ref",
                "message": message,
                "received": "<redacted>",
                "field_schema": _facade_field_schema(operation, pointer),
                "fix": "Stop this quarantined worker and ask the coordinator to create a fresh native dispatch.",
            }],
            "retryable": False,
            "attempt_budget_consumed": False,
            "worker_replacement_authorized": False,
            "next_action": "Do not infer or replace dispatch authority; stop this worker.",
        }
    terminal_code: str | None = None
    terminal_pointer = ""
    if operation == "record_attempt_event" and "event stream is closed" in message:
        terminal_code = "record_attempt_event_closed"
        terminal_pointer = "/event_type"
    elif operation == "read_worker_result" and (
        "successor worker" in message or "predecessor result refs" in message
    ):
        terminal_code = "read_worker_result_not_authorized"
        terminal_pointer = "/attempt_result_ref"
    if terminal_code is not None:
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": "failed",
            "code": terminal_code,
            "diagnostics": [{
                "code": terminal_code,
                "path": "$" + terminal_pointer.replace("/", "."),
                "json_pointer": terminal_pointer,
                "message": message,
                "field_schema": {"type": "string"},
            }],
            "retryable": False,
            "attempt_budget_consumed": False,
            "worker_replacement_authorized": False,
            "next_action": "Stop task-scoped worker calls and return this neutral terminal failure to the coordinator.",
        }
    code = "attempt_finalization_pending" if finalization else f"{operation}_invalid"
    collected = getattr(exc, "diagnostics", None)
    diagnostics = (
        [dict(item) for item in collected if isinstance(item, Mapping)]
        if isinstance(collected, list) and collected else []
    )
    if not diagnostics:
        lowered = message.lower()
        pointer = ""
        if operation == "record_attempt_event":
            for marker, candidate in (
                ("event_type", "/event_type"), ("event type", "/event_type"),
                ("event_key", "/event_key"), ("event key", "/event_key"),
                ("payload", "/payload"),
            ):
                if marker in lowered:
                    pointer = candidate
                    break
        elif operation == "read_worker_result":
            for marker, candidate in (
                ("attempt_result_ref", "/attempt_result_ref"),
                ("result refs", "/attempt_result_ref"),
                ("coordinator_ref", "/coordinator_ref"),
                ("step", "/step"),
            ):
                if marker in lowered:
                    pointer = candidate
                    break
        diagnostics = [{
            "code": code,
            "path": "$" + pointer.replace("/", ".") if pointer else "$",
            "json_pointer": pointer,
            "message": message,
            "field_schema": (
                _facade_field_schema(operation, pointer)
                if pointer else {"type": "object"}
            ),
        }]
    # Do not leak the coordinator-only routing lock for caller-correctable
    # worker payloads.  The shared runtime helper supplies a concrete retry
    # operation; its contract is supplemented with the exact tool schema.
    for item in diagnostics:
        if isinstance(item, dict) and not finalization:
            if item.get("path") and not (
                isinstance(item.get("json_pointer"), str)
                and (item["json_pointer"] == "" or item["json_pointer"].startswith("/"))
            ):
                item["json_pointer"] = _json_pointer(item["path"])
            item.setdefault("phase", "payload")
            item.setdefault("fix", f"Correct {item.get('path', '$')} and retry {operation} on the same attempt.")
            pointer = str(item.get("json_pointer") or "")
            item.setdefault(
                "field_schema",
                _facade_field_schema(operation, pointer)
                if pointer else {"type": "object"},
            )
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
    if operation == "read_worker_result" and any(fragment in message.lower() for fragment in (
        "current active wave is unavailable",
        "current wave expected assignments",
        "current wave canonical results are incomplete",
        "current wave contains unexpected assignments",
    )):
        result["retry"] = {"kind": "inspect_server_state", "operation": operation}
    if not finalization:
        result["validation"] = {
            "schema": "cortex/validation-error/v1",
            "operation": operation,
            "diagnostics_are_complete": True,
            "request_schema": _validation_request_schema(operation, diagnostics),
            "invalid_paths": [item.get("path") for item in diagnostics if isinstance(item, dict) and item.get("path")],
            "invalid_json_pointers": [item.get("json_pointer") for item in diagnostics if isinstance(item, dict) and item.get("json_pointer") is not None],
            "retry": {"same_call": True, "preserve_valid_fields": True, "replacement_worker_authorized": False},
        }
    return result


def _record_attempt_event_impl(params: dict[str, Any]) -> dict[str, Any]:
    """Persist one bounded semantic checkpoint for the active worker."""
    try:
        original = dict(params)
        params = original
        preflight = _record_attempt_event_preflight(original)
        if preflight:
            raise ValidationFailure(preflight)
        project, _task_dir, state, attempt, profile = _worker_context(params, "record_attempt_event")
        if attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
            raise ValueError("attempt event stream is closed")
        event_type = str(params.get("event_type") or "").strip()
        result = attempt_protocol.record_attempt_event(
            _runtime.ledger_root({"project_root": str(project)}),
            task_id=state["task_id"],
            attempt_id=str(attempt["attempt_id"]),
            event_type=event_type,
            payload={"text": str(params["text"]).strip()},
            event_key=None,
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


def _v11_repair_failure(
    original: Mapping[str, Any],
    root: Any,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
    exc: BaseException,
) -> dict[str, Any] | None:
    """Return a bounded repair contract backed only by private escrow state."""
    diagnostics = getattr(exc, "diagnostics", None)
    if not isinstance(diagnostics, list) or not diagnostics:
        return None
    try:
        submit_schema = _facade_schema("complete_attempt", original)
        normalized = v11_submission.normalize_diagnostics(
            diagnostics,
            schema=submit_schema,
        )
        # ``create_rejected_draft_escrow`` scopes diagnostics below the
        # semantic kind prefix.  The current public submission is already
        # flat, while its private repair target remains
        # ``{"status": ..., "report": ...}``.  Add that private-only prefix
        # so public /status and /report failures become executable semantic
        # /status and /report patches instead of collapsing /report to the
        # forbidden whole-payload root.
        escrow_diagnostics: list[dict[str, Any]] = []
        for item in normalized:
            pointer = str(item.get("json_pointer") or "")
            if pointer in {"/status", "/report"} or pointer.startswith(("/status/", "/report/")):
                item = dict(item)
                item["json_pointer"] = "/report" + pointer
                escrow_diagnostics.append(item)
        draft = v11_submission.create_rejected_draft_escrow(
            original,
            escrow_diagnostics,
            schema=submit_schema,
        )
        patch_paths = list(dict.fromkeys(
            str(item.get("repair_pointer") or "")
            for item in draft.get("diagnostics") or []
            if str(item.get("repair_pointer") or "").startswith("/")
        ))
        escrow = ledger_db.store_repair_escrow(
            root,
            task_id=str(state["task_id"]),
            attempt_id=str(attempt["attempt_id"]),
            dispatch_ref_digest=v11_submission.canonical_digest(str(original["dispatch_ref"])),
            kind=str(draft["kind"]),
            base_payload_digest=str(draft["base_payload_digest"]),
            payload=draft["payload"],
            diagnostics=draft["diagnostics"],
            allowed_paths=patch_paths,
        )
    except (ValueError, TypeError, OSError, RuntimeError):
        return None
    return _v11_pending_repair_response(root, escrow)


def _v11_pending_repair_response(
    root: Any,
    escrow: Mapping[str, Any],
    *,
    submission_diagnostics: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project the exact immutable repair contract already bound to an attempt."""
    token = v11_submission.sign_repair_handle(
        str(escrow["handle_id"]),
        str(escrow["escrow_digest"]),
        _runtime._governance_lifecycle_hmac_key(root, create=False),
    )
    normalized = v11_submission.normalize_diagnostics(
        list(escrow.get("diagnostics") or [])
    )
    public_diagnostics = (
        [dict(item) for item in submission_diagnostics]
        if submission_diagnostics else
        normalized
    )
    repair_diagnostics: list[dict[str, Any]] = []
    kind_prefix = "/" + str(escrow.get("kind") or "")
    for item in normalized:
        pointer = str(item.get("json_pointer") or "")
        repair_pointer = str(item.get("repair_pointer") or "")
        if not repair_pointer:
            if pointer == kind_prefix:
                repair_pointer = "/"
            elif pointer.startswith(kind_prefix + "/"):
                repair_pointer = pointer[len(kind_prefix):]
        if repair_pointer.startswith("/"):
            repair_diagnostics.append({**item, "repair_pointer": repair_pointer})
    patch_paths = list(dict.fromkeys(str(item) for item in escrow.get("allowed_paths") or []))
    return {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "outcome": "needs_correction",
        "code": (
            "complete_attempt_repair_patch_invalid"
            if submission_diagnostics else
            "complete_attempt_validation_failed"
        ),
        "diagnostics": public_diagnostics,
        "retryable": True,
        "attempt_budget_consumed": False,
        "worker_replacement_authorized": False,
        "validation": {
            "schema": "cortex/validation-error/v1",
            "diagnostics_are_complete": True,
            "invalid_json_pointers": [item.get("json_pointer") for item in public_diagnostics],
            "zero_state_mutation": True,
        },
        "repair": {
            "mode": "same_attempt_structural_patch",
            "repair_capsule": token,
            "base_payload_digest": escrow["base_payload_digest"],
            "patch_paths": patch_paths,
            "diagnostics": repair_diagnostics,
            "preserve_valid_fields": True,
            "preserve_exact_dispatch_ref": True,
            "rejected_draft_unchanged": True,
        },
        "next_action": (
            "Retry complete_attempt on this same worker with the exact unchanged dispatch_ref, "
            "the same repair_capsule and base_payload_digest, and the repair branch only: patches must be "
        "limited to the server-issued repair paths. Do not submit a new report while this repair is pending."
        ),
    }


def _repair_submission_failure_diagnostics(
    repair_submission: Mapping[str, Any],
    escrow: Mapping[str, Any],
    exc: BaseException,
) -> list[dict[str, Any]]:
    """Return deterministic request-local diagnostics for one bad patch retry.

    The immutable semantic repair cards stay in ``recovery.repair``.  These
    cards identify what was malformed in the *current* patches array, so a
    worker can correct that array without changing the capsule, digest, or
    rejected semantic base.
    """
    repair_schema = _facade_schema("complete_attempt", repair_submission)
    raw = getattr(exc, "diagnostics", None)
    if isinstance(raw, list) and raw:
        diagnostics = v11_submission.normalize_diagnostics(
            raw,
            schema=repair_schema,
        )
    else:
        message = _runtime.redact(str(exc), 1000) or "repair patch is invalid"
        lowered = message.lower()
        if "handle" in lowered:
            pointers = ["/repair_capsule"]
        elif "digest" in lowered:
            pointers = ["/base_payload_digest"]
        else:
            patches = repair_submission.get("patches")
            patch_count = len(patches) if isinstance(patches, list) and patches else 1
            field = (
                "op" if " op " in f" {lowered} " else
                "path" if any(token in lowered for token in ("path", "scope", "parent", "index", "field")) else
                "value"
            )
            pointers = [f"/patches/{index}/{field}" for index in range(patch_count)]
        diagnostics = v11_submission.normalize_diagnostics([{
            "code": "repair_patch_invalid",
            "path": pointer,
            "json_pointer": pointer,
            "message": message,
            "field_schema": v11_submission.schema_for_path(
                repair_schema,
                pointer,
            ),
        } for pointer in pointers], schema=repair_schema)

    # Post-patch semantic validation points into the flat report submission. Translate
    # each such diagnostic back to the submitted patch value that produced it.
    kind_prefix = "/" + str(escrow.get("kind") or "")
    patches = repair_submission.get("patches")
    patch_items = patches if isinstance(patches, list) else []
    localized: list[dict[str, Any]] = []
    for raw_diagnostic in diagnostics:
        diagnostic = dict(raw_diagnostic)
        pointer = str(diagnostic.get("json_pointer") or "")
        semantic_pointer = (
            pointer[len(kind_prefix):]
            if kind_prefix != "/" and (
                pointer == kind_prefix or pointer.startswith(kind_prefix + "/")
            ) else
            ""
        )
        if semantic_pointer:
            for index, patch in enumerate(patch_items):
                if not isinstance(patch, Mapping):
                    continue
                patch_path = str(patch.get("path") or "")
                if semantic_pointer == patch_path:
                    suffix = ""
                elif patch_path and semantic_pointer.startswith(patch_path + "/"):
                    suffix = semantic_pointer[len(patch_path):]
                else:
                    continue
                request_pointer = f"/patches/{index}/value{suffix}"
                diagnostic["path"] = request_pointer
                diagnostic["json_pointer"] = request_pointer
                diagnostic["message"] = (
                    f"patch value reconstructs invalid {semantic_pointer}: "
                    + str(diagnostic.get("message") or "invalid value")
                )
                break
        localized.append(diagnostic)
    return localized


def _v11_repair_retry_failure(
    root: Any,
    repair_submission: Mapping[str, Any],
    escrow: Mapping[str, Any],
    exc: BaseException,
) -> dict[str, Any]:
    """Return the same immutable capsule after a corrected draft still fails.

    The attempted patches never become a new repair base.  This keeps retries
    idempotent and prevents a partially valid patch from rewriting already
    valid fields in the originally rejected semantic payload.
    """
    diagnostics = _repair_submission_failure_diagnostics(
        repair_submission,
        escrow,
        exc,
    )
    return _v11_pending_repair_response(
        root,
        escrow,
        submission_diagnostics=diagnostics,
    )


def _v11_repair_rejected(exc: BaseException) -> dict[str, Any]:
    """Fail closed only for tampered/stale authority or base integrity."""
    return {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "outcome": "repair_rejected",
        "code": "complete_attempt_repair_rejected",
        "diagnostics": [{
            "code": "complete_attempt_repair_rejected",
            "path": "$",
            "json_pointer": "",
            "message": _runtime.redact(str(exc), 1000),
        }],
        "retryable": False,
        "attempt_budget_consumed": False,
        "worker_replacement_authorized": False,
        "state_mutated": False,
        "next_action": (
            "Stop task-scoped worker calls. The capsule, base digest, identity, or terminal-attempt "
            "authority failed integrity validation; do not guess a replacement capability."
        ),
    }


def _complete_attempt_impl(params: dict[str, Any]) -> dict[str, Any]:
    """Validate one flat v11 report/repair submission, then persist evidence."""
    project: Any = None
    state: dict[str, Any] | None = None
    attempt: dict[str, Any] | None = None
    root: Any = None
    original = dict(params)
    full_submission: dict[str, Any] | None = None
    pending_repair: dict[str, Any] | None = None
    try:
        submission_schema = _facade_schema("complete_attempt", original)
        submit_schema = _facade_schema("complete_attempt", {"action": "submit"})
        repair_schema = _facade_schema("complete_attempt", {"action": "repair"})
        dispatch_ref = original.get("dispatch_ref")
        authority_valid = (
            isinstance(dispatch_ref, str)
            and re.fullmatch(v11_submission.DISPATCH_REF_PATTERN, dispatch_ref) is not None
        )
        if not authority_valid:
            try:
                v11_submission.validate_submission(
                    original,
                    schema=submission_schema,
                )
            except v11_submission.ValidationFailure as exc:
                diagnostics = [dict(item) for item in exc.diagnostics]
            else:
                diagnostics = []
            if not diagnostics:
                for field in ("dispatch_ref",):
                    diagnostics.append(_facade_diagnostic(
                        "complete_attempt", field, f"{field} is unavailable",
                    ))
            return {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": False,
                "outcome": "dispatch_unavailable",
                "code": "worker_dispatch_unavailable",
                "diagnostics": diagnostics,
                "retryable": False,
            }
        project, task_dir, state, attempt, profile = _worker_context(original, "complete_attempt")
        root = _runtime.ledger_root({"project_root": str(project)})
        existing_result = attempt_protocol.get_attempt_result(
            root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
        )
        if existing_result is None:
            try:
                pending_repair = ledger_db.get_pending_repair_escrow(
                    root,
                    task_id=str(state["task_id"]),
                    attempt_id=str(attempt["attempt_id"]),
                )
            except (ValueError, TypeError, OSError, RuntimeError) as exc:
                return _v11_repair_rejected(exc)

        if pending_repair is not None:
            # Once a rejected draft has an issued repair handle, the attempt is
            # locked to that exact immutable base. Full report retries
            # are never evaluated and cannot replace the diagnostic scope.
            supplied_capsule = original.get("repair_capsule")
            if supplied_capsule is None:
                return _v11_pending_repair_response(root, pending_repair)
            secret = _runtime._governance_lifecycle_hmac_key(root, create=False)
            try:
                # A syntactically malformed copy is a caller/model transport
                # error, not proof that the server-issued capability was
                # tampered with. Reissue the exact locked repair unchanged.
                v11_submission.repair_handle_id(supplied_capsule)
            except (ValueError, TypeError) as exc:
                return _v11_repair_retry_failure(root, original, pending_repair, exc)
            try:
                v11_submission.verify_repair_handle(
                    supplied_capsule,
                    str(pending_repair["escrow_digest"]),
                    secret,
                )
                if (
                    str(pending_repair["task_id"]) != str(state["task_id"])
                    or str(pending_repair["attempt_id"]) != str(attempt["attempt_id"])
                    or str(pending_repair["dispatch_ref_digest"]) != v11_submission.canonical_digest(original.get("dispatch_ref"))
                ):
                    raise ValueError("repair handle does not match this dispatch")
                supplied_digest = original.get("base_payload_digest")
                if supplied_digest is not None and supplied_digest != pending_repair["base_payload_digest"]:
                    raise ValueError("repair base_payload_digest integrity check failed")
                if supplied_digest is None:
                    return _v11_pending_repair_response(root, pending_repair)
                checked = v11_submission.validate_submission(
                    original,
                    schema=repair_schema,
                )
                escrow = {
                    "schema": "cortex/private-repair-draft/v1",
                    "dispatch_ref": checked["dispatch_ref"],
                    "kind": pending_repair["kind"],
                    "base_payload_digest": pending_repair["base_payload_digest"],
                    "payload": pending_repair["payload"],
                    "diagnostics": pending_repair["diagnostics"],
                }
                full_submission = v11_submission.apply_repair_escrow(
                    escrow,
                    original,
                    repair_schema=repair_schema,
                    submit_schema=submit_schema,
                )
            except v11_submission.ValidationFailure as exc:
                return _v11_repair_retry_failure(root, original, pending_repair, exc)
            except (ValueError, TypeError, OSError, RuntimeError) as exc:
                # Handle/digest/identity integrity was checked above.  Any
                # remaining error describes a caller-correctable patch shape,
                # operation, path, or value and must preserve the same repair.
                message = str(exc).lower()
                if any(marker in message for marker in (
                    "handle", "integrity", "identity", "task and assignment",
                )):
                    return _v11_repair_rejected(exc)
                return _v11_repair_retry_failure(root, original, pending_repair, exc)
        else:
            preflight = _complete_attempt_preflight(original)
            if preflight:
                # These are envelope/branch-shape corrections, not rejected
                # semantic drafts. They must not create repair escrow: the
                # caller can apply the complete remove/add edit set directly.
                return _public_failure("complete_attempt", ValidationFailure(preflight))
            checked = v11_submission.validate_submission(
                original,
                schema=submission_schema,
            )
            if checked["mode"] == "repair":
                secret = _runtime._governance_lifecycle_hmac_key(root, create=False)
                try:
                    handle_id = v11_submission.repair_handle_id(checked["repair_capsule"])
                    escrow_row = ledger_db.get_repair_escrow(
                        root,
                        handle_digest=v11_submission.repair_handle_digest(handle_id),
                    )
                    if escrow_row is None:
                        raise ValueError("repair handle is stale or unavailable")
                    v11_submission.verify_repair_handle(
                        checked["repair_capsule"],
                        str(escrow_row["escrow_digest"]),
                        secret,
                    )
                    if (
                        str(escrow_row["handle_id"]) != handle_id
                        or str(escrow_row["task_id"]) != str(state["task_id"])
                        or str(escrow_row["attempt_id"]) != str(attempt["attempt_id"])
                        or str(escrow_row["dispatch_ref_digest"]) != v11_submission.canonical_digest(checked["dispatch_ref"])
                        or str(escrow_row["base_payload_digest"]) != str(checked["base_payload_digest"])
                    ):
                        raise ValueError("repair handle does not match this dispatch")
                    escrow = {
                        "schema": "cortex/private-repair-draft/v1",
                        "dispatch_ref": checked["dispatch_ref"],
                        "kind": escrow_row["kind"],
                        "base_payload_digest": escrow_row["base_payload_digest"],
                        "payload": escrow_row["payload"],
                        "diagnostics": escrow_row["diagnostics"],
                    }
                    full_submission = v11_submission.apply_repair_escrow(
                        escrow,
                        original,
                        repair_schema=repair_schema,
                        submit_schema=submit_schema,
                    )
                except (ValueError, TypeError, OSError, RuntimeError) as exc:
                    return _v11_repair_rejected(exc)
            else:
                full_submission = checked
        kind = str(full_submission.get("kind") or "")
        if kind == "report":
            semantic_result = {
                "status": full_submission.get("status"),
                "summary": full_submission.get("report"),
                "findings": [], "decisions_needed": [], "unresolved": [], "claims": [],
            }
        else:
            raise ValidationFailure([{
                "code": "submission_kind_invalid", "path": "$",
                "message": "complete_attempt requires action=submit with status and report",
            }])

        # Everything above is read-only.  Receipt checks and backend-derived
        # workspace/artifact evidence also complete before the first write.
        _receipt_guard(root, state, attempt)
        if str(semantic_result.get("status") or "").strip().lower() == "completed":
            missing_artifacts = _runtime.required_artifact_diagnostics(project, task_dir, state, attempt)
            if missing_artifacts:
                raise ValidationFailure(missing_artifacts)
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
        # Every retry must pass
        # through the protocol's immutable semantic comparison.  A corrected
        # through immutable semantic comparison and may not smuggle a new
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
                "terminal": True,
            }
        _mark_attempt(
            project, state["task_id"], attempt["attempt_id"],
            lifecycle_status="work_completed",
            result=canonical,
        )
        if canonical["lifecycle_status"] == attempt_protocol.LIFECYCLE_WORK_COMPLETED:
            attempt_protocol.begin_attempt_finalization(
                root, task_id=state["task_id"], attempt_id=attempt["attempt_id"],
            )
        attempt_protocol.build_attempt_result_view(
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
            "terminal": True,
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
        if pending_repair is not None:
            # Backend semantic validation of a patched reconstruction is still
            # part of the same locked repair.  Never replace its private base
            # or strand the worker on a generic correction branch.
            return _v11_repair_retry_failure(root, original, pending_repair, exc)
        if full_submission is None and original.get("action") == "repair":
            return _v11_repair_rejected(exc)
        if root is not None and state is not None and attempt is not None and full_submission is None:
            repair = _v11_repair_failure(original, root, state, attempt, exc)
            if repair is not None:
                return repair
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
                return _public_failure("complete_attempt", exc, finalization=True)
        return _public_failure("complete_attempt", exc)


def _read_worker_result_impl(params: dict[str, Any]) -> dict[str, Any]:
    """Read assigned predecessor evidence or the complete current coordinator wave.

    Workers retain the explicit predecessor-result capability. Coordinators do
    not transport worker result references through child messages: the exact
    current step selects the server-owned wave and Cortex derives every expected
    canonical result in deterministic dispatch order.
    """
    try:
        original = dict(params)
        params = original
        preflight = _read_worker_result_preflight(original)
        if preflight:
            raise ValidationFailure(preflight)
        worker_context = "dispatch_ref" in original
        if worker_context:
            project, task_dir, state, worker_attempt, worker_profile = _runtime.authorize_worker_assignment(
                original, "read_worker_result",
            )
            task_ref = ""
            raw_attempt_id = str(worker_attempt["attempt_id"])
            raw_profile = worker_profile
        else:
            _project, task_dir, state, _task, task_ref = _runtime.authorize_coordinator_ref(
                params, "read_worker_result",
            )
            raw_attempt_id = ""
            raw_profile = ""
        result_ref = ""
        if worker_context:
            result_ref = _runtime.safe_id(str(params.get("attempt_result_ref") or ""))
            if not result_ref:
                raise ValueError("attempt_result_ref is required")
            attempt_id = _runtime.safe_id(raw_attempt_id)
            attempt = _runtime._attempt(state, attempt_id)
            profile = _runtime.canonical_profile(raw_profile)
            if attempt.get("invalidated") or attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
                raise ValueError("successor worker result reads require an active, non-invalidated attempt")
            if attempt.get("profile") != profile:
                raise ValueError("successor worker profile does not match the delegated attempt")
            allowed_refs = {str(item) for item in attempt.get("context_result_refs") or []}
            if result_ref not in allowed_refs:
                raise ValueError("successor worker may read only predecessor result refs supplied in its dispatch")
        root = _runtime._task_document_root(task_dir, state["task_id"])
        if worker_context:
            source = next(
                (candidate for candidate in state.get("attempts") or []
                 if str(candidate.get("attempt_result_ref") or "") == result_ref),
                None,
            )
            if not isinstance(source, Mapping):
                raise ValueError("attempt_result_ref does not belong to the selected Cortex task")
            view = attempt_protocol.build_attempt_result_view(
                root, task_id=state["task_id"], attempt_id=str(source["attempt_id"]),
            )
            if view["attempt_result_ref"] != result_ref:
                raise ValueError("attempt_result_ref does not match its canonical result")
            result = {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "attempt_result_ref": result_ref,
                "result_view": view,
                "complete": True,
            }
            result["predecessor_receipt"] = attempt_protocol.record_predecessor_read(
                root, task_id=state["task_id"], attempt_id=attempt_id, predecessor_result_ref=result_ref,
            )
            return result

        plan = _runtime._load_orchestrate_plan(task_dir, dict(state))
        wave = _runtime._wave_for_gates(plan, _runtime.active_gates(dict(state)))
        if not isinstance(wave, Mapping):
            raise ValueError("current active wave is unavailable")
        wave_match = _runtime.re.search(r"(\d+)$", str(wave.get("wave_id") or ""))
        expected_step = int(wave_match.group(1)) if wave_match else 0
        if expected_step < 1 or params.get("step") != expected_step:
            raise ValueError("step does not match the exact current active wave")
        wave_attempt_ids = [str(item).strip() for item in wave.get("attempt_ids") or [] if str(item).strip()]
        if not wave_attempt_ids or len(set(wave_attempt_ids)) != len(wave_attempt_ids):
            raise ValueError("current wave expected assignments are missing or duplicated")
        attempts_by_id: dict[str, Mapping[str, Any]] = {}
        current_wave_id = str(wave.get("wave_id") or "")
        extras: list[str] = []
        for candidate in state.get("attempts") or []:
            if not isinstance(candidate, Mapping) or candidate.get("invalidated"):
                continue
            candidate_id = str(candidate.get("attempt_id") or "").strip()
            if candidate_id in attempts_by_id:
                raise ValueError("current task contains duplicate attempt identities")
            attempts_by_id[candidate_id] = candidate
            if (
                str(candidate.get("orchestration_wave_id") or "") == current_wave_id
                and candidate_id not in wave_attempt_ids
            ):
                extras.append(candidate_id)
        if extras:
            raise ValueError("current wave contains unexpected assignments")
        terminal_lifecycles = {
            attempt_protocol.LIFECYCLE_COMPLETED,
            attempt_protocol.LIFECYCLE_BLOCKED,
            attempt_protocol.LIFECYCLE_FAILED,
        }
        result_views: list[dict[str, Any]] = []
        result_refs: list[str] = []
        dispatch_refs: list[str] = []
        sources: list[Mapping[str, Any]] = []
        for attempt_id in wave_attempt_ids:
            source = attempts_by_id.get(attempt_id)
            if not isinstance(source, Mapping) or source.get("invalidated"):
                raise ValueError("current wave expected assignment is unavailable")
            result_ref = str(source.get("attempt_result_ref") or "").strip()
            dispatch_ref = str(source.get("dispatch_ref") or "").strip()
            if not result_ref or not dispatch_ref:
                raise ValueError("current wave canonical results are incomplete")
            canonical = attempt_protocol.get_attempt_result(
                root, task_id=str(state["task_id"]), attempt_id=attempt_id,
            )
            if (
                canonical is None
                or str(canonical.get("result_ref") or "") != result_ref
                or canonical.get("lifecycle_status") not in terminal_lifecycles
            ):
                raise ValueError("current wave canonical results are incomplete")
            view = attempt_protocol.build_attempt_result_view(
                root, task_id=str(state["task_id"]), attempt_id=attempt_id,
            )
            if str(view.get("attempt_result_ref") or "") != result_ref:
                raise ValueError("current wave result identity does not match canonical state")
            sources.append(source)
            result_views.append(view)
            result_refs.append(result_ref)
            dispatch_refs.append(dispatch_ref)
        if len(set(result_refs)) != len(result_refs) or len(set(dispatch_refs)) != len(dispatch_refs):
            raise ValueError("current wave result or dispatch identity is duplicated")
        result = {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "task_ref": task_ref,
            "result_views": result_views,
            "complete": True,
        }
        if all(view.get("lifecycle_status") == attempt_protocol.LIFECYCLE_COMPLETED for view in result_views):
            result["continuation"] = {
                "task_id": str(state["task_id"]),
                "step": expected_step,
                "results": [
                    ({"attempt_result_ref": result_ref, "worker": slot} if len(result_refs) > 1 else {"attempt_result_ref": result_ref})
                    for slot, result_ref in enumerate(result_refs, 1)
                ],
            }
        else:
            terminal_source = next(
                (source for source, view in zip(sources, result_views)
                 if view.get("lifecycle_status") in {attempt_protocol.LIFECYCLE_BLOCKED, attempt_protocol.LIFECYCLE_FAILED}),
                None,
            )
            if terminal_source is not None:
                terminal_ref = str(terminal_source.get("attempt_result_ref") or "")
                terminal_view = result_views[sources.index(terminal_source)]
                terminal_continuation, _ = _coordinator_terminal_continuation(
                    task_dir, state, terminal_source, terminal_ref,
                    lifecycle_status=terminal_view.get("lifecycle_status"), result_view=terminal_view,
                )
                if terminal_continuation is not None:
                    result["terminal_continuation"] = terminal_continuation
        return result
    except (ValueError, TypeError, OSError) as exc:
        return _public_failure("read_worker_result", exc)


def record_attempt_event(params: dict[str, Any]) -> dict[str, Any]:
    """Return only the closed public worker-event receipt."""
    from cortex_runtime.mcp_api import project_public_response
    return project_public_response(
        "record_attempt_event", _record_attempt_event_impl(params), arguments=params,
    )


def complete_attempt(params: dict[str, Any]) -> dict[str, Any]:
    """Return only the closed public completion or repair receipt."""
    from cortex_runtime.mcp_api import project_public_response
    return project_public_response(
        "complete_attempt", _complete_attempt_impl(params), arguments=params,
    )


def _public_result_projection(value: Any) -> Any:
    """Project one canonical internal result as the flat public report form."""
    if isinstance(value, list):
        return [_public_result_projection(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    canonical = value.get("result")
    if isinstance(canonical, Mapping):
        status = str(canonical.get("result_status") or canonical.get("status") or "")
        report = canonical.get("summary")
        if isinstance(report, str) and status:
            return {"status": status, "report": report}
    # Fail closed for a malformed server view: do not expose private view
    # topology or silently invent a second public result representation.
    raise ValueError("canonical result view cannot be projected as status and report")


def _flat_result_page(result: dict[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    """Return deterministic immutable report pages bound to the selected action."""
    if not result.get("ok"):
        return result
    action = str(params.get("action") or "")
    refs = [str(item.get("attempt_result_ref") or "") for item in result.get("result_views") or []]
    if action == "read_predecessor":
        refs = [str(result.get("attempt_result_ref") or "")]
        source = _public_result_projection(result.get("result_view") or {})
        text = json.dumps(source, ensure_ascii=False, sort_keys=True)
    else:
        source = _public_result_projection(result.get("result_views") or [])
        text = json.dumps(source, ensure_ascii=False, sort_keys=True)
    selector = {
        "authority": params.get("dispatch_ref") if action == "read_predecessor" else result.get("task_ref"),
        "action": action,
        "refs": refs,
    }
    digest = pagination.scope_digest({"selector": selector, "text": text})
    selector_name = f"result.{action}"
    if action == "read_predecessor":
        project, _task_dir, state, attempt, _profile = _runtime.authorize_worker_assignment(
            params, "read_worker_result",
        )
        audience_basis = {
            "task_id": state.get("task_id"),
            "attempt_id": attempt.get("attempt_id"),
        }
    else:
        project, _task_dir, state, _task, _task_ref = _runtime.authorize_coordinator_ref(
            params, "read_worker_result",
        )
        audience_basis = {"task_id": state.get("task_id"), "role": "coordinator"}
    # Bind the cursor to the already-authorized durable subject, not to a
    # digest of the bearer capability. The opaque cursor therefore carries
    # no reusable verifier derived from dispatch_ref or coordinator_ref.
    audience = f"{action}.{pagination.scope_digest(audience_basis)[:32]}"
    root = _runtime.ledger_root({"project_root": str(project)})
    secret = _runtime._governance_lifecycle_hmac_key(root, create=False)
    offset = 0
    cursor = params.get("cursor")
    if cursor is not None:
        offset = pagination.decode_cursor(
            cursor,
            secret,
            selector=selector_name,
            audience=audience,
            digest=digest,
        )
    if offset > len(text):
        raise ValueError("cursor is outside the selected result content")
    content = text[offset:offset + 8000]
    page = {"schema": result.get("schema"), "ok": True, "action": "read_more", "content": content}
    if offset + len(content) < len(text):
        page["next_cursor"] = pagination.encode_cursor(
            secret,
            selector=selector_name,
            audience=audience,
            digest=digest,
            offset=offset + len(content),
        )
        return page
    page["action"] = "use_result_as_context" if action == "read_predecessor" else ("continue" if result.get("continuation") else "terminal_continue")
    if action == "read_wave":
        page["result_refs"] = refs[:32]
        page["step"] = params.get("step")
    return page


def read_worker_result(params: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical semantic result without its internal projection."""
    from cortex_runtime.mcp_api import project_public_response
    return project_public_response(
        "read_worker_result", _flat_result_page(_read_worker_result_impl(params), params), arguments=params,
    )
