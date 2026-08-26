"""Worker-facing adapters for the database-centric attempt protocol.

The worker supplies semantic facts.  This facade resolves its already-issued
attempt, records the canonical result/event rows, verifies machine-side read
receipts, derives the workspace observation, and exposes only a regenerated
non-authoritative AttemptResult read view.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import cortex as _runtime

from cortex_runtime import attempt_protocol, canonical_json, ledger_db, pagination
from cortex_runtime.finding_severity import (
    PUBLIC_TO_CANONICAL_FINDING_SEVERITY,
    finding_severity_is_intrinsically_blocking,
)
from cortex_runtime.assignment_evaluator import (
    persist_assignment_evaluation,
)
from cortex_runtime.assignment_compiler import compiled_wave_execution_position
from cortex_runtime.validation import ValidationFailure
from cortex_runtime import v11_submission
from cortex_runtime.v11_responses import WAIT_BEFORE_READ_INSTRUCTION
from cortex_runtime.public_contracts import backend_schema_for
from cortex_runtime.native_lifecycle_observer import (
    promote_incomplete_stop,
    reconcile_native_stop_inbox,
)
from cortex_runtime.verification_contract import (
    WORKER_VERIFICATION_KINDS,
    pending_verification_evidence_payload,
    worker_machine_evidence,
)


_CLOSURE_GATES = {"review", "governance_activation", "governance_close", "close"}
_ACTIVE_ATTEMPT_STATUSES = {_runtime.AWAITING_HOST_SPAWN, "running"}
_PENDING_WAVE_ATTEMPT_STATUSES = {
    _runtime.AWAITING_HOST_SPAWN,
    "running",
    "waiting_question",
}
_WORKER_REFERENCE_FIELDS = {"dispatch_ref"}


class NativeCompletionObservationRequired(RuntimeError):
    """Raised when canonical result consumption still needs a host wait observation."""


class NativeCompletionObservationUnavailable(RuntimeError):
    """Raised when the trusted local observer needs bounded server recovery."""


def _write_orchestrate_plan(task_dir: Any, plan: dict[str, Any]) -> None:
    """Persist through the orchestration engine's canonical plan writer."""
    from cortex_runtime.orchestration_engine import _write_orchestrate_plan as write_plan
    write_plan(task_dir, plan)


def _current_dispatch_exposure_failure(
    original: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return a non-mutating correction for an exact authority copy in report.

    Authorization has already proved that the dedicated dispatch field is the
    current worker's exact capability and that no foreign capability-shaped
    value is present.  Do not echo that bearer value in this receipt.
    """
    if original.get("action") not in {"submit", "governance_closure"}:
        return None
    dispatch_ref = original.get("dispatch_ref")
    report = original.get("report")
    if not isinstance(dispatch_ref, str) or not isinstance(report, str):
        return None
    if dispatch_ref not in report:
        return None
    pointer = "/report"
    message = (
        "The semantic report contains the current worker's opaque authority. "
        "Remove all opaque authority or capability values, preserve the semantic "
        "evidence, and retry the same operation with the dedicated authority field unchanged."
    )
    return {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "outcome": "needs_correction",
        "code": "complete_attempt_report_exposes_authority",
        "message": message,
        "diagnostics": [{
            "code": "complete_attempt_report_exposes_authority",
            "json_pointer": pointer,
            "message": message,
            "field_schema": _facade_field_schema("complete_attempt", pointer),
        }],
        "retryable": True,
        "state_mutated": False,
    }


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
    plan = _runtime._load_orchestrate_plan(task_dir, state)
    wave, frontier_attempts = _runtime._effective_plan_frontier(plan, state)
    if not isinstance(wave, Mapping):
        return None, "no_active_wave"
    # ``attempt_ids`` is the server-owned slot order.  In particular, do not
    # turn the result that happened to be read last into a singleton advance
    # for a parallel wave.  Every member must have its own final canonical
    # result and its own dispatch identity before the coordinator receives one
    # atomic continuation payload.
    wave_attempt_ids = [
        str(item.get("attempt_id") or "").strip()
        for item in frontier_attempts
        if str(item.get("attempt_id") or "").strip()
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
    return {
        "task_id": str(state["task_id"]),
        "step": compiled_wave_execution_position(
            [item for item in plan.get("waves") or [] if isinstance(item, Mapping)],
            str(wave.get("wave_ref") or ""),
        ),
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
    plan = _runtime._load_orchestrate_plan(task_dir, state)
    wave, frontier_attempts = _runtime._effective_plan_frontier(plan, state)
    if not isinstance(wave, Mapping):
        return None, "no_active_wave"
    wave_attempt_ids = [
        str(item.get("attempt_id") or "").strip()
        for item in frontier_attempts
        if str(item.get("attempt_id") or "").strip()
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
    return {
        "task_id": str(state["task_id"]),
        "step": compiled_wave_execution_position(
            [item for item in plan.get("waves") or [] if isinstance(item, Mapping)],
            str(wave.get("wave_ref") or ""),
        ),
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
            _runtime.ledger_root_path_internal(project, create=False),
            task_id=task_id,
            attempt_id=attempt_id,
        )
        if existing is None:
            raise ValueError("attempt is not active and has no canonical result")
    return project, task_dir, state, attempt, profile


def _public_failure(operation: str, exc: Exception, *, finalization: bool = False) -> dict[str, Any]:
    message = _runtime.redact(str(exc), 1000)
    if isinstance(exc, _runtime.WorkerAssignmentError) and not finalization:
        pending_identity = exc.code == "native_subagent_start_required"
        model_attestation_failure = exc.code.startswith("native_subagent_model_")
        public_code = (
            exc.code if pending_identity or model_attestation_failure
            else "worker_dispatch_unavailable"
        )
        pointer = "/dispatch_ref"
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": (
                "native_identity_pending" if pending_identity else
                "native_model_attestation_failed" if model_attestation_failure else
                "needs_input"
            ),
            "code": public_code,
            "diagnostics": [{
                "code": public_code,
                "path": "$.dispatch_ref",
                "json_pointer": "/dispatch_ref",
                "message": message,
                "received": "<redacted>",
                "field_schema": _facade_field_schema(operation, pointer),
                "fix": (
                    "Retry the same operation unchanged after a bounded delay while trusted local native identity joins."
                    if pending_identity else
                    "Stop this quarantined worker and return control to coordinator server recovery."
                ),
            }],
            "retryable": pending_identity,
            "attempt_budget_consumed": False,
            "worker_replacement_authorized": False,
            "next_action": (
                "Retry this exact worker call unchanged after a bounded delay."
                if pending_identity else
                "Do not infer or replace dispatch authority; stop this worker."
            ),
        }
    terminal_code: str | None = None
    terminal_pointer = ""
    if operation in {"record_attempt_event", "record_worker_finding"} and "event stream is closed" in message:
        terminal_code = "record_attempt_event_closed"
        terminal_pointer = "/event_type" if operation == "record_attempt_event" else "/summary"
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
    code = (
        "worker_attestation_server_state_unavailable"
        if operation == "record_attempt_event" and "complete server workspace snapshot" in message.lower()
        else "attempt_finalization_pending" if finalization
        else f"{operation}_invalid"
    )
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
                ("verification_kind", "/verification_kind"),
                ("status=passed", "/text"), ("machine fact", "/text"),
                ("worker verification attestation text", "/text"),
                ("passed_tests", "/text"), ("viewports", "/text"),
                ("keyboard_checks", "/text"), ("console_errors", "/text"),
                ("external_requests", "/text"),
                ("verification attestations", "/event_type"),
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
        "effective current wave contains ambiguous assignments",
        "current active wave has no dispatched assignment frontier",
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
        protocol_root = _runtime.ledger_root_path_internal(project, create=False)
        receipt_digest: str | None = None
        if event_type == "verification_observation":
            if str(attempt.get("operation_kind") or "") not in {"verify", "close"}:
                raise ValueError(
                    "verification attestations require an exact verify or close assignment"
                )
            verification_kind = str(params.get("verification_kind") or "").strip()
            if not verification_kind:
                diagnostic = _facade_diagnostic(
                    "record_attempt_event",
                    "verification_kind",
                    "verification_kind is required for verification_observation",
                )
                diagnostic["allowed_ops"] = ["add"]
                raise ValidationFailure([diagnostic])
            if verification_kind not in WORKER_VERIFICATION_KINDS:
                raise ValueError("verification_kind is not worker-observable")
            if verification_kind not in set(attempt.get("required_verification_kinds") or []):
                raise ValueError(
                    "verification_kind is not required by this compiled assignment"
                )
            evidence, evidence_digest = worker_machine_evidence(
                verification_kind, params.get("text"),
            )
            receipt_digest = evidence_digest
            workspace = _workspace_observation(project, _task_dir, state, attempt)
            workspace_digest = str(workspace.get("current_digest_sha256") or "")
            if workspace.get("complete") is not True or not workspace_digest:
                raise ValueError(
                    "worker verification attestation requires a complete server workspace snapshot"
                )
            receipt = {
                "schema": "cortex/server-storage-receipt/v1",
                "source": "worker_attestation",
                "receipt_scope": "identity_digest_storage",
                "status": "recorded",
                "evidence_digest": evidence_digest,
                "machine_evidence": evidence,
            }
            payload = pending_verification_evidence_payload(
                task_id=str(state["task_id"]),
                attempt=attempt,
                verification_kind=verification_kind,
                verification_id=f"worker_event:{verification_kind}",
                task_revision=attempt.get("assignment_task_revision"),
                workspace_digest=workspace_digest,
                server_receipt=receipt,
                tests=[{
                    "kind": verification_kind,
                    "status": "worker_attested",
                    "evidence_digest": evidence_digest,
                }],
            )
            result = attempt_protocol.record_attempt_event(
                protocol_root,
                task_id=str(state["task_id"]),
                attempt_id=str(attempt["attempt_id"]),
                event_type="verification_claimed",
                payload=payload,
                event_key="worker_attestation:" + hashlib.sha256(
                    (
                        f"{attempt['attempt_id']}\0{verification_kind}\0{evidence_digest}"
                    ).encode("utf-8")
                ).hexdigest(),
            )
        else:
            if "verification_kind" in params:
                diagnostic = _facade_diagnostic(
                    "record_attempt_event",
                    "verification_kind",
                    "verification_kind is allowed only for verification_observation",
                )
                diagnostic["allowed_ops"] = ["remove"]
                raise ValidationFailure([diagnostic])
            result = attempt_protocol.record_attempt_event(
                protocol_root,
                task_id=state["task_id"],
                attempt_id=str(attempt["attempt_id"]),
                event_type=event_type,
                payload={"text": str(params["text"]).strip()},
                event_key=None,
            )
        response = {
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
        if event_type == "verification_observation":
            response.update({
                "receipt_ref": result["event"]["event_ref"],
                "digest": "sha256:" + str(receipt_digest),
            })
        return response
    except (ValueError, TypeError, OSError) as exc:
        return _public_failure("record_attempt_event", exc)


def _record_worker_finding_impl(params: dict[str, Any]) -> dict[str, Any]:
    """Persist one flat worker finding with server-owned immutable binding."""
    try:
        original = dict(params)
        preflight = _schema_preflight("record_worker_finding", original)
        if preflight:
            raise ValidationFailure(preflight)
        project, _task_dir, state, attempt, _profile = _worker_context(
            original, "record_worker_finding",
        )
        if attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
            raise ValueError("attempt event stream is closed")
        public_severity = str(original.get("severity") or "").strip().lower()
        canonical_severity = PUBLIC_TO_CANONICAL_FINDING_SEVERITY[public_severity]
        summary = str(original.get("summary") or "")
        if not summary.strip():
            diagnostic = _facade_diagnostic(
                "record_worker_finding", "summary",
                "summary must contain non-whitespace arbitrary-Unicode text",
            )
            diagnostic["allowed_ops"] = ["replace"]
            raise ValidationFailure([diagnostic])
        binding = {
            "schema": "cortex/worker-finding-binding/v1",
            "task_id": str(state["task_id"]),
            "attempt_id": str(attempt["attempt_id"]),
            "dispatch_digest": _runtime.digest_text(str(original["dispatch_ref"])),
            "assignment_lineage_digest": str(attempt.get("assignment_lineage_digest") or ""),
            "wave_ref": str(attempt.get("wave_ref") or attempt.get("orchestration_wave_id") or ""),
            "phase_ref": str(attempt.get("phase_ref") or ""),
            "phase_kind": str(attempt.get("phase_kind") or attempt.get("gate") or ""),
            "plan_revision": int(attempt.get("plan_revision") or state.get("plan_revision") or 0),
            "plan_digest": str(attempt.get("plan_digest") or state.get("plan_digest") or ""),
            "workspace_baseline_ref": str(attempt.get("result_baseline_ref") or ""),
        }
        fingerprint = attempt_protocol.worker_finding_fingerprint(
            binding, severity=canonical_severity, summary=summary,
        )
        finding = {
            "fingerprint": fingerprint,
            "severity": canonical_severity,
            "status": "open",
            "blocking": finding_severity_is_intrinsically_blocking(canonical_severity),
            "summary": summary,
        }
        root = _runtime.ledger_root_path_internal(project, create=False)
        result = attempt_protocol.record_attempt_event(
            root,
            task_id=str(state["task_id"]),
            attempt_id=str(attempt["attempt_id"]),
            event_type="finding_recorded",
            payload={"finding": finding, "binding": binding},
            event_key=f"worker_finding:{fingerprint}",
        )
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "worker_finding_recorded",
            "receipt_ref": result["event"]["event_ref"],
            "digest": "sha256:" + _runtime.digest_text(fingerprint),
            "idempotent": bool(result.get("idempotent")),
        }
    except (ValueError, TypeError, OSError, KeyError) as exc:
        return _public_failure("record_worker_finding", exc)


def _path_is_allowed(path: str, patch_paths: Sequence[object]) -> bool:
    for raw in patch_paths:
        allowed = str(raw or "").strip().rstrip("/")
        if allowed == "." or path == allowed or (allowed and path.startswith(allowed + "/")):
            return True
    return False


def _workspace_observation(project: Any, task_dir: Any, state: Mapping[str, Any], attempt: Mapping[str, Any]) -> dict[str, Any]:
    baseline = _runtime.attempt_manifest_baseline(task_dir, dict(attempt))
    current = _runtime.capture_project_manifest(project, policy=baseline.get("policy"))
    comparison = _runtime.compare_manifests(baseline, current)
    changed = list(comparison.get("changed_paths") or [])
    # A single-worker occurrence has an exclusive baseline/completion window
    # and can therefore expose its exact manifest delta. Parallel writers do
    # not: their shared checkout delta remains deliberately unattributed.
    safe = bool(comparison.get("complete")) and not bool(attempt.get("parallel"))
    return {
        "baseline_ref": attempt.get("result_baseline_ref"),
        "baseline_digest_sha256": str(baseline.get("digest") or ""),
        "current_digest_sha256": str(current.get("digest") or ""),
        "complete": bool(comparison.get("complete")),
        "safe_to_attribute": safe,
        "mutation_count": len(changed),
        "changed_files": changed,
    }


class AttemptReadReceiptsIncomplete(ValueError):
    """A valid semantic submission is waiting on required server read receipts."""


def _receipt_guard(root: Any, state: Mapping[str, Any], attempt: Mapping[str, Any]) -> dict[str, Any]:
    recovery_context = attempt.get("recovery_context")
    if isinstance(recovery_context, Mapping):
        expected_context_digest = "sha256:" + hashlib.sha256(
            canonical_json.dumps(dict(recovery_context)).encode("utf-8")
        ).hexdigest()
        if (
            recovery_context.get("schema") != "cortex/technical-recovery-context/v1"
            or str(attempt.get("recovery_context_digest") or "")
            != expected_context_digest
        ):
            raise ValueError("technical recovery context integrity validation failed")
        source_status = str(recovery_context.get("source_result_status") or "")
        source_ref = str(attempt.get("recovery_source_result_ref") or "")
        source_digest = str(attempt.get("recovery_source_result_digest") or "")
        if source_status == "available":
            source_attempt = next((
                item for item in state.get("attempts") or []
                if isinstance(item, Mapping)
                and str(item.get("attempt_result_ref") or "") == source_ref
            ), None)
            canonical_source = attempt_protocol.get_attempt_result(
                root,
                task_id=str(state.get("task_id") or ""),
                attempt_id=str((source_attempt or {}).get("attempt_id") or ""),
            ) if isinstance(source_attempt, Mapping) else None
            expected_source_digest = (
                "sha256:" + hashlib.sha256(
                    canonical_json.dumps(dict(canonical_source)).encode("utf-8")
                ).hexdigest()
                if isinstance(canonical_source, Mapping) else ""
            )
            if (
                not source_ref
                or source_ref not in set(str(item) for item in attempt.get("predecessor_result_refs") or [])
                or source_digest != expected_source_digest
            ):
                raise ValueError("technical recovery source result integrity validation failed")
        elif source_status == "absent":
            if source_ref or source_digest:
                raise ValueError("result-less technical recovery carries a source result")
        else:
            raise ValueError("technical recovery source result status is invalid")
        chain_refs = [
            str(item) for item in attempt.get("recovery_chain_result_refs") or []
            if str(item)
        ]
        chain_digests = attempt.get("recovery_chain_result_digests")
        chain_digests = dict(chain_digests) if isinstance(chain_digests, Mapping) else {}
        required_refs = set(str(item) for item in attempt.get("predecessor_result_refs") or [])
        if (
            len(chain_refs) != len(set(chain_refs))
            or set(chain_digests) != set(chain_refs)
            or not set(chain_refs).issubset(required_refs)
        ):
            raise ValueError("technical recovery result chain integrity validation failed")
        for chain_ref in chain_refs:
            chain_attempt = next((
                item for item in state.get("attempts") or []
                if isinstance(item, Mapping)
                and str(item.get("attempt_result_ref") or "") == chain_ref
            ), None)
            canonical_chain = attempt_protocol.get_attempt_result(
                root,
                task_id=str(state.get("task_id") or ""),
                attempt_id=str((chain_attempt or {}).get("attempt_id") or ""),
            ) if isinstance(chain_attempt, Mapping) else None
            expected_chain_digest = (
                "sha256:" + hashlib.sha256(
                    canonical_json.dumps(dict(canonical_chain)).encode("utf-8")
                ).hexdigest()
                if isinstance(canonical_chain, Mapping) else ""
            )
            if str(chain_digests.get(chain_ref) or "") != expected_chain_digest:
                raise ValueError("technical recovery result chain digest changed")
    elif any(str(attempt.get(field) or "") for field in (
        "recovery_context_digest", "recovery_source_result_ref", "recovery_source_result_digest",
    )):
        raise ValueError("technical recovery binding is missing its canonical context")
    receipts = attempt_protocol.attempt_receipts(
        root,
        task_id=str(state["task_id"]),
        attempt_id=str(attempt["attempt_id"]),
    )
    missing: list[str] = []
    if not receipts.get("briefing_receipt"):
        missing.append("briefing_read")
    if (
        str(attempt.get("recovery_question_ref") or "")
        and not str(attempt.get("recovery_question_answer_read_at") or "")
    ):
        missing.append("durable_question_answer_read")
    required = {str(item) for item in attempt.get("predecessor_result_refs") or []}
    observed = {str(item) for item in (receipts.get("predecessor_receipts") or {})}
    missing.extend("predecessor:" + item for item in sorted(required - observed))
    if missing:
        raise AttemptReadReceiptsIncomplete(
            "required server read receipts are incomplete"
        )
    return receipts


def _open_question_for_attempt(
    root: Any,
    state: Mapping[str, Any],
    attempt_id: str,
) -> Mapping[str, Any] | None:
    """Return the one current durable question that legally pauses a worker."""
    from cortex_runtime.questions import permitted_question_categories

    rows, _has_more = ledger_db.page_durable_questions(
        root,
        str(state["task_id"]),
        offset=0,
        limit=2,
        attempt_id=attempt_id,
        status="open",
        categories=permitted_question_categories(),
    )
    if len(rows) > 1:
        raise ValueError("one worker attempt has multiple open durable questions")
    return rows[0] if rows else None


def _latest_answered_question_for_attempt(
    root: Any,
    state: Mapping[str, Any],
    attempt_id: str,
) -> Mapping[str, Any] | None:
    """Return the latest canonical answer while the stopped child awaits resume."""
    from cortex_runtime.questions import permitted_question_categories

    offset = 0
    latest: Mapping[str, Any] | None = None
    while True:
        rows, has_more = ledger_db.page_durable_questions(
            root,
            str(state["task_id"]),
            offset=offset,
            limit=128,
            attempt_id=attempt_id,
            status="answered",
            categories=permitted_question_categories(),
        )
        if rows:
            latest = max(
                rows,
                key=lambda item: int(item.get("answered_sequence") or 0),
            )
        if not has_more:
            return latest
        offset += len(rows)


def _same_child_repair_message(root: Any, escrow: Mapping[str, Any]) -> str:
    """Build one compact, server-owned follow-up for a stopped repair turn."""
    repair = _v11_pending_repair_response(root, escrow)
    recovery = repair.get("repair") if isinstance(repair.get("repair"), Mapping) else {}
    capsule = str(recovery.get("repair_capsule") or "")
    base_digest = str(recovery.get("base_payload_digest") or "")
    patch_paths = [
        str(item) for item in recovery.get("patch_paths") or []
        if str(item).startswith("/")
    ]
    if not capsule or not base_digest or not patch_paths:
        raise ValueError("pending repair cannot be projected as a bounded same-child retry")
    return (
        "Resume the same Cortex assignment for its one bounded completion repair. "
        "First call refresh_worker_context with no arguments and consume every returned page until complete. "
        "Use only the exact durable pending-repair contract restored by that response; do not reconstruct it "
        "from compacted context. Then call repair_attempt exactly once, preserving every valid field from the "
        "rejected draft and changing only the server-authorized patch paths. Do not inspect Cortex source or "
        "private state. "
        "If the repair succeeds, return immediately; if it is rejected or unavailable, return a neutral failure "
        "and stop so coordinator recovery can replace this attempt."
    )


def _terminalize_stopped_attempt(
    root: Any,
    task_dir: Any,
    state: dict[str, Any],
    attempt: dict[str, Any],
    *,
    reason: str,
    preserve_task_needs_input: bool = False,
) -> bool:
    """Persist a result-less trusted Stop as failure, never as live work."""
    if (
        attempt.get("status") == "failed"
        and attempt.get("host_stop_outcome") == "native_worker_stopped_without_result"
    ):
        return False
    evidence = attempt.get("native_incomplete_stop_evidence")
    stopped_at = str(
        evidence.get("observed_at") if isinstance(evidence, Mapping) else ""
    ) or _runtime.now()
    attempt["status"] = "failed"
    attempt["lifecycle_status"] = "needs_recovery"
    attempt["host_stop_outcome"] = "native_worker_stopped_without_result"
    attempt["host_resumable"] = False
    attempt["host_stopped_at"] = stopped_at
    attempt["finalized_at"] = _runtime.now()
    attempt["finalization_reason"] = reason
    if preserve_task_needs_input:
        if state.get("status") != "needs_input":
            raise ValueError("auxiliary Stop recovery requires the original durable question pause")
    else:
        state["status"] = "active"
        state.pop("blocked_reason", None)
    failures = state.get("native_stop_failures")
    if not isinstance(failures, list):
        failures = []
    logical_key = str(attempt.get("logical_delegation_key") or "").strip()
    assignment_digest = str(attempt.get("assignment_lineage_digest") or "").strip()
    plan_assignment_digest = str(attempt.get("plan_assignment_lineage_digest") or "").strip()
    if not logical_key or not assignment_digest or not plan_assignment_digest:
        raise ValueError("result-less orchestrated attempt lacks immutable assignment lineage")
    failure_fingerprint = _runtime.digest_text(json.dumps(
        {
            "logical_delegation_key": logical_key,
            "assignment_lineage_digest": assignment_digest,
            "plan_assignment_lineage_digest": plan_assignment_digest,
            "reason": reason,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ))
    failures.append({
        "attempt_id": str(attempt.get("attempt_id") or ""),
        "failure_fingerprint": failure_fingerprint,
        "logical_delegation_key": logical_key,
        "assignment_lineage_digest": assignment_digest,
        "plan_assignment_lineage_digest": plan_assignment_digest,
        "wave_ref": str(attempt.get("wave_ref") or ""),
        "phase_ref": str(attempt.get("phase_ref") or ""),
        "reason": reason,
        "recorded_at": _runtime.now(),
    })
    state["native_stop_failures"] = [
        item for item in failures[-32:]
        if isinstance(item, Mapping)
    ]
    sessions = [
        item for item in ledger_db.list_worker_sessions(root, str(state["task_id"]))
        if str(item.get("attempt_id") or "") == str(attempt.get("attempt_id") or "")
    ]
    for session in sessions:
        ledger_db.put_worker_session(root, {
            **session,
            "status": "stopped_recoverable",
            "resumable": False,
            "terminated_at": stopped_at,
        })
    _runtime.save_state(
        task_dir,
        task_dir / "state.sqlite",
        state,
        "native_stop_without_result",
        "trusted native child stopped without a canonical result",
    )
    return True


def _record_resultless_recovery_breaker(
    task_dir: Any,
    state: dict[str, Any],
    attempt_ids: Sequence[str],
) -> None:
    """Route an exhausted same-child Stop into the sole reliability ladder."""
    current_ids = {str(item).strip() for item in attempt_ids if str(item).strip()}
    failures = [
        item for item in state.get("native_stop_failures") or []
        if isinstance(item, Mapping)
    ]
    current_fingerprints = {
        str(item.get("failure_fingerprint") or "")
        for item in failures
        if str(item.get("attempt_id") or "") in current_ids
        and str(item.get("failure_fingerprint") or "")
    }
    if not current_fingerprints:
        return None
    receipts = [
        item for item in state.get("terminal_recovery_breakers") or []
        if isinstance(item, Mapping)
    ]
    known = {
        str(item.get("fingerprint") or "") for item in receipts
        if str(item.get("fingerprint") or "")
    }
    additions: list[dict[str, Any]] = []
    for fingerprint in sorted(current_fingerprints):
        if fingerprint in known:
            continue
        source = next(
            item for item in reversed(failures)
            if str(item.get("failure_fingerprint") or "") == fingerprint
        )
        additions.append({
            "schema": "cortex/terminal-recovery-breaker/v1",
            "kind": "native_worker_stopped_without_result",
            "fingerprint": fingerprint,
            "logical_delegation_key": str(source.get("logical_delegation_key") or ""),
            "assignment_lineage_digest": str(source.get("assignment_lineage_digest") or ""),
            "plan_assignment_lineage_digest": str(source.get("plan_assignment_lineage_digest") or ""),
            "wave_ref": str(source.get("wave_ref") or ""),
            "phase_ref": str(source.get("phase_ref") or ""),
            "failure_class": "technical",
            "terminal_reason": str(source.get("reason") or ""),
            "failure_count": 1,
            "created_at": _runtime.now(),
        })
        known.add(fingerprint)
    if not additions:
        return None
    state["terminal_recovery_breakers"] = [*receipts, *additions][-32:]
    state["status"] = "active"
    state.pop("blocked_reason", None)
    _runtime.save_state(
        task_dir,
        task_dir / "state.sqlite",
        state,
        "resultless_reliability_recovery_required",
        "recorded bounded reliability recovery after same-child recovery was exhausted",
    )
    return None


def _resume_offer_matches_stop(
    offer: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> bool:
    """Return whether this read observes the turn for which resume was issued."""
    offer_generation = int(offer.get("session_generation") or 1)
    evidence_generation = int(evidence.get("session_generation") or 1)
    offer_sequence = int(offer.get("stop_sequence") or 0)
    evidence_sequence = int(evidence.get("sequence") or 0)
    return (
        offer_generation == evidence_generation
        and (offer_sequence == 0 or offer_sequence == evidence_sequence)
    )


def _same_child_resume_pending(
    task_ref: str,
    *,
    question_ref: str = "",
) -> dict[str, Any]:
    """Project an issued follow-up as waiting without issuing it again."""
    return {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "action": "wait_for_bound_workers",
        "task_ref": task_ref,
        **({"question_ref": question_ref} if question_ref else {}),
        "content": (
            "The same-child follow-up instruction was already issued for this stopped native turn. "
            "Invoke wait_agent for that exact bound child. Re-read the server lifecycle/wave state "
            "only after wait_agent reports it terminal. Cortex retains authorization server-side; "
            "do not call followup_task again."
        ),
        "state_mutated": False,
    }


def _same_child_followup_dispatch(
    attempt: Mapping[str, Any], message: str,
) -> list[dict[str, Any]]:
    """Return one safe exact follow-up keyed by the original native task name."""
    spawn_request = attempt.get("spawn_request")
    if not isinstance(spawn_request, Mapping):
        raise ValueError("same-child follow-up spawn binding is unavailable")
    task_name = str(spawn_request.get("task_name") or "").strip()
    dispatch_ref = str(attempt.get("dispatch_ref") or "").strip()
    if not task_name or not dispatch_ref or not message:
        raise ValueError("same-child follow-up native task binding is unavailable")
    return [{
        "dispatch_ref": dispatch_ref,
        "call": "followup_task",
        "host_task_name": task_name,
        "arguments": {"target": task_name, "message": message},
    }]


def _stopped_wave_control(
    root: Any,
    task_dir: Any,
    state: dict[str, Any],
    task_ref: str,
    attempt_ids: Sequence[str],
    *,
    preserve_task_needs_input: bool = False,
) -> dict[str, Any] | None:
    """Resolve trusted result-less Stops before they can become endless waits."""
    attempts_by_id = {
        str(item.get("attempt_id") or ""): item
        for item in state.get("attempts") or []
        if isinstance(item, dict) and not item.get("invalidated")
    }
    # Re-project an already persisted result-less terminal state
    # idempotently.  This matters both for durable-question auxiliary waves
    # and for a coordinator replay after the replacement circuit breaker has
    # fired; neither replay may fall back to a completion wait.
    terminalized = any(
        isinstance(attempts_by_id.get(str(attempt_id)), dict)
        and attempts_by_id[str(attempt_id)].get("status") == "failed"
        and attempts_by_id[str(attempt_id)].get("host_stop_outcome")
        == "native_worker_stopped_without_result"
        for attempt_id in attempt_ids
    )
    for attempt_id in attempt_ids:
        attempt = attempts_by_id.get(str(attempt_id))
        if not isinstance(attempt, dict):
            continue
        if attempt_protocol.get_attempt_result(
            root, task_id=str(state["task_id"]), attempt_id=str(attempt_id),
        ) is not None:
            continue
        evidence = attempt.get("native_incomplete_stop_evidence")
        if not isinstance(evidence, Mapping) or evidence.get("observed") is not True:
            continue

        question = _open_question_for_attempt(root, state, str(attempt_id))
        if isinstance(question, Mapping):
            changed = False
            if attempt.get("status") != "waiting_question":
                attempt["status"] = "waiting_question"
                attempt["lifecycle_status"] = "paused_awaiting_user"
                attempt["host_stop_outcome"] = "awaiting_user"
                attempt["host_resumable"] = True
                changed = True
            if state.get("status") != "needs_input":
                state["status"] = "needs_input"
                changed = True
            if changed:
                _runtime.save_state(
                    task_dir, task_dir / "state.sqlite", state,
                    "native_question_pause_reconciled",
                    "reconciled trusted native stop with durable open question",
                )
            return {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "action": "obtain_user_decision",
                "task_ref": task_ref,
                "question_ref": str(question.get("question_ref") or ""),
                "content": str(question.get("question_text") or ""),
                "state_mutated": changed,
            }

        if (
            attempt.get("status") == "waiting_question"
            or attempt.get("host_stop_outcome") == "awaiting_user"
        ):
            answered = _latest_answered_question_for_attempt(
                root, state, str(attempt_id),
            )
            question_offer = attempt.get("native_question_resume_offer")
            if isinstance(answered, Mapping) and not isinstance(question_offer, Mapping):
                question_ref = str(answered.get("question_ref") or "")
                if not question_ref:
                    raise ValueError("answered durable question has no question_ref")
                attempt["native_question_resume_offer"] = {
                    "stop_sequence": int(evidence.get("sequence") or 0),
                    "session_generation": int(evidence.get("session_generation") or 1),
                    "question_ref": question_ref,
                    "offered_at": _runtime.now(),
                }
                _runtime.save_state(
                    task_dir, task_dir / "state.sqlite", state,
                    "native_question_resume_offered",
                    "offered one bounded same-child durable-answer resume",
                )
                message = (
                    "Resume the same Cortex assignment. Call poll_worker_question for the durable "
                    f"answered question {question_ref}, use the complete canonical answer as task "
                    "context, finish the assigned work, and call submit_attempt before returning."
                )
                return {
                    "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                    "ok": True,
                    "action": "resume_bound_worker",
                    "task_ref": task_ref,
                    "question_ref": question_ref,
                    "dispatches": _same_child_followup_dispatch(attempt, message),
                    "content": (
                        "Call the returned followup_task exactly once for the same bound native child. Then "
                        "invoke wait_agent for that exact bound child. Call read_worker_wave only after "
                        "wait_agent reports it terminal. Cortex retains authorization server-side; do not continue orchestration first "
                        "and do not spawn a replacement."
                    ),
                    "state_mutated": True,
                }
            if isinstance(answered, Mapping) and isinstance(question_offer, Mapping):
                if _resume_offer_matches_stop(question_offer, evidence):
                    return _same_child_resume_pending(
                        task_ref,
                        question_ref=str(answered.get("question_ref") or ""),
                    )
                terminalized = _terminalize_stopped_attempt(
                    root, task_dir, state, attempt,
                    reason="same_child_question_resume_exhausted",
                    preserve_task_needs_input=preserve_task_needs_input,
                ) or terminalized
                continue

        escrow = ledger_db.get_pending_repair_escrow(
            root,
            task_id=str(state["task_id"]),
            attempt_id=str(attempt_id),
        )
        sequence = int(evidence.get("sequence") or 0)
        generation = int(evidence.get("session_generation") or 1)
        offer = attempt.get("native_repair_resume_offer")
        if isinstance(escrow, Mapping) and not isinstance(offer, Mapping):
            message = _same_child_repair_message(root, escrow)
            attempt["native_repair_resume_offer"] = {
                "stop_sequence": sequence,
                "session_generation": generation,
                "offered_at": _runtime.now(),
            }
            attempt["host_stop_outcome"] = "repair_resume_required"
            _runtime.save_state(
                task_dir, task_dir / "state.sqlite", state,
                "native_repair_resume_offered",
                "offered one bounded same-child completion repair",
            )
            return {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "action": "resume_bound_worker",
                "task_ref": task_ref,
                "dispatches": _same_child_followup_dispatch(attempt, message),
                "content": message,
                "state_mutated": True,
            }

        if isinstance(escrow, Mapping) and isinstance(offer, Mapping):
            if _resume_offer_matches_stop(offer, evidence):
                return _same_child_resume_pending(task_ref)

        # A later failed resumed Stop or a missing repair contract converges
        # on durable selected-route replacement. Replays of the original Stop
        # were handled above as waiting and can never issue a second follow-up.
        reason = (
            "same_child_repair_exhausted"
            if isinstance(escrow, Mapping) and isinstance(offer, Mapping)
            else "stopped_without_repairable_condition"
        )
        terminalized = _terminalize_stopped_attempt(
            root, task_dir, state, attempt, reason=reason,
            preserve_task_needs_input=preserve_task_needs_input,
        ) or terminalized
    if terminalized:
        _record_resultless_recovery_breaker(task_dir, state, attempt_ids)
        return {
            "_server_recovery_required": True,
            "_recovery_attempt_ids": [
                str(attempt_id) for attempt_id in attempt_ids
                if isinstance(attempts_by_id.get(str(attempt_id)), dict)
                and attempts_by_id[str(attempt_id)].get("status") == "failed"
                and attempts_by_id[str(attempt_id)].get("host_stop_outcome")
                == "native_worker_stopped_without_result"
            ],
        }
    return None


def _terminal_wave_results(
    root: Any,
    state: Mapping[str, Any],
    attempt_ids: Sequence[str],
) -> list[tuple[Mapping[str, Any], str]]:
    """Resolve exact terminal result bindings for one immutable wave snapshot."""
    normalized_ids = [str(item).strip() for item in attempt_ids if str(item).strip()]
    if not normalized_ids or len(normalized_ids) != len(attempt_ids):
        raise ValueError("current wave expected assignments are missing")
    if len(set(normalized_ids)) != len(normalized_ids):
        raise ValueError("current wave expected assignments are duplicated")
    attempts_by_id: dict[str, Mapping[str, Any]] = {}
    for item in state.get("attempts") or []:
        if not isinstance(item, Mapping) or item.get("invalidated"):
            continue
        attempt_id = str(item.get("attempt_id") or "").strip()
        if not attempt_id or attempt_id in attempts_by_id:
            raise ValueError("current task contains missing or duplicate attempt identities")
        attempts_by_id[attempt_id] = item
    terminal_lifecycles = {
        attempt_protocol.LIFECYCLE_COMPLETED,
        attempt_protocol.LIFECYCLE_BLOCKED,
        attempt_protocol.LIFECYCLE_FAILED,
    }
    results: list[tuple[Mapping[str, Any], str]] = []
    for attempt_id in normalized_ids:
        attempt = attempts_by_id.get(attempt_id)
        if not isinstance(attempt, Mapping):
            raise ValueError("current wave expected assignment is unavailable")
        result_ref = str(attempt.get("attempt_result_ref") or "").strip()
        canonical = attempt_protocol.get_attempt_result(
            root, task_id=str(state["task_id"]), attempt_id=attempt_id,
        )
        canonical_ref = str(canonical.get("result_ref") or "").strip() if canonical else ""
        canonical_lifecycle = str(canonical.get("lifecycle_status") or "") if canonical else ""
        if result_ref and canonical_ref and canonical_ref != result_ref:
            raise ValueError("current wave canonical result identity conflicts with task state")
        if not result_ref or canonical is None or canonical_lifecycle not in terminal_lifecycles:
            # An expected assignment that is still owned by the live native
            # lifecycle is not a malformed read request. Awaiting spawn,
            # active work, durable-question pause/resume, result finalization,
            # and either side of the result/Stop race all converge on the same
            # coordinator direction: wait again, then repeat the read. Keep
            # structural wave/identity corruption on the hard failure branch.
            if (
                str(attempt.get("status") or "") in _PENDING_WAVE_ATTEMPT_STATUSES
                or canonical_lifecycle in {
                    attempt_protocol.LIFECYCLE_WORK_COMPLETED,
                    attempt_protocol.LIFECYCLE_FINALIZING,
                }
            ):
                raise NativeCompletionObservationRequired(
                    "The current bound native wave has not reached a consumable terminal result."
                )
            raise ValueError("current wave terminal state has no canonical result")
        results.append((attempt, result_ref))
    return results


def require_wave_native_completion_observed(
    root: Any,
    state: Mapping[str, Any],
    attempt_ids: Sequence[str],
) -> list[tuple[Mapping[str, Any], str]]:
    """Require canonical results and exact terminal SubagentStop markers.

    Native ``wait_agent`` remains the coordinator's progress primitive, but the
    terminal result is therefore consumable once the trusted SubagentStop
    marker is durable; no synthetic second wait or prose confirmation exists.
    """
    results = _terminal_wave_results(root, state, attempt_ids)
    try:
        health = [
            ledger_db.native_lifecycle_observer_health(
                root, task_id=str(state["task_id"]),
                attempt_id=str(attempt["attempt_id"]),
            ) for attempt, _result_ref in results
        ]
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeCompletionObservationUnavailable(
            "The trusted local lifecycle observer state is temporarily unavailable."
        ) from exc
    if "unavailable" in health:
        raise NativeCompletionObservationUnavailable(
            "The trusted local lifecycle observer requires bounded server recovery."
        )
    for attempt, result_ref in results:
        stop = attempt.get("native_terminal_stop")
        host_thread = str(attempt.get("worker_host_thread_id") or "")
        sequence = stop.get("sequence") if isinstance(stop, Mapping) else None
        if (
            not host_thread
            or not isinstance(stop, Mapping)
            or stop.get("observed") is not True
            or type(sequence) is not int
            or sequence < 1
            or str(stop.get("result_digest") or "") != _runtime.digest_text(result_ref)
        ):
            raise NativeCompletionObservationRequired(
                "Invoke wait_agent for the exact bound child, then retry the same server lifecycle read or "
                "continuation only after wait_agent reports it terminal; Cortex retains authorization server-side."
            )
    return results


def _mark_attempt(
    root: Any,
    task_dir: Any,
    task_id: str,
    attempt_id: str,
    *,
    lifecycle_status: str,
    result: Mapping[str, Any],
    terminal_status: str | None = None,
    projection_status: str | None = None,
    finalization_error: str | None = None,
) -> None:
    """Project one canonical result without leaving its native worker live.

    A finalized canonical success becomes a non-runnable result-ready slot
    until the coordinator consumes it through ``continue_orchestration``.
    Terminal semantic failures use their terminal attempt status directly.
    Both paths reconcile the server-owned worker session before the result can
    be surfaced. A missing session fails closed instead of manufacturing host
    identity or leaving an awaiting/running worker projection behind.
    """
    with _runtime.state_lock(root):
        loaded = ledger_db.load_task(root, _runtime.safe_id(task_id))
        if loaded is None:
            raise ValueError("attempt task is unavailable")
        state = loaded[1]
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
        if terminal_status and projection_status:
            raise ValueError("attempt projection cannot be both terminal and result-ready")
        if projection_status:
            if projection_status != _runtime.RESULT_READY:
                raise ValueError("unsupported non-terminal attempt result projection")
            attempt["status"] = projection_status
            attempt["result_ready_at"] = _runtime.now()
        if terminal_status:
            attempt["status"] = terminal_status
            attempt["finalized_at"] = _runtime.now()
            attempt["finalization_reason"] = f"semantic_attempt_{terminal_status}"
        if result_lifecycle in terminal_lifecycles:
            # Close the Stop-before-result race only from exact observer
            # evidence already persisted for this bound child/session.
            promote_incomplete_stop(attempt, state, str(result.get("result_ref") or ""))
            persist_assignment_evaluation(root, state, attempt)
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
            patch_paths=patch_paths,
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
    patch_paths = list(dict.fromkeys(str(item) for item in escrow.get("patch_paths") or []))
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
        action = str(original.get("action") or "")
        is_governance_close = str(attempt.get("gate") or "") == "governance_close"
        if is_governance_close and action != "governance_closure":
            return {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": False,
                "outcome": "needs_correction",
                "code": "governance_closure_tool_required",
                "retryable": True,
                "state_mutated": False,
                "next_action": (
                    "Call submit_governance_closure for this exact dispatch. "
                    "Do not retry through submit_attempt."
                ),
            }
        if not is_governance_close and action == "governance_closure":
            return {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": False,
                "outcome": "needs_correction",
                "code": "governance_closure_dispatch_required",
                "retryable": False,
                "state_mutated": False,
                "next_action": "Use submit_attempt for this non-governance worker.",
            }
        # ``_worker_context`` has already resolved ``project`` from the exact
        # dispatch and attested child binding.  A worker MCP thread does not
        # own the coordinator's SessionStart record, so re-entering the public
        # workspace selector here would reject an otherwise authorized worker
        # before semantic repair escrow can be created.
        root = _runtime.ledger_root_path_internal(project, create=False)
        exposure_failure = _current_dispatch_exposure_failure(original)
        if exposure_failure is not None:
            return exposure_failure
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
        metadata_overrides: dict[str, Any] | None = None
        if kind == "report":
            semantic_result = {
                "status": full_submission.get("status"),
                "summary": full_submission.get("report"),
                "findings": [], "decisions_needed": [], "unresolved": [], "claims": [],
            }
        elif kind == "governance_closure":
            closure_outcome = str(full_submission.get("closure_outcome") or "")
            blocking_gaps_text = str(full_submission.get("blocking_gaps_text") or "")
            basis = attempt.get("governance_closure_basis")
            if closure_outcome == "verified":
                basis_issues: list[str] = []
                if not isinstance(basis, Mapping):
                    basis_issues.append("server-derived closure basis is unavailable")
                else:
                    if basis.get("complete") is not True:
                        basis_issues.append("server-derived closure basis is incomplete")
                    if basis.get("effective_mode") != "full":
                        basis_issues.append("server-derived effective_mode=full authority is absent")
                    if basis.get("execution_verified") is not True:
                        basis_issues.append("execution provenance is not verified")
                    if list(basis.get("issues") or []):
                        basis_issues.append("server-derived closure basis contains issues")
                    if not str(basis.get("plan_revision") or "").strip():
                        basis_issues.append("current plan revision is unavailable")
                    for field in (
                        "plan_digest", "frontier_digest", "policy_digest",
                        "manifest_digest", "acceptance_contract_digest",
                    ):
                        if re.fullmatch(r"sha256:[0-9a-f]{64}", str(basis.get(field) or "")) is None:
                            basis_issues.append(f"{field} is unavailable")
                    try:
                        _runtime.validate_current_governance_closure_basis(
                            state, basis, artifact_root=root, attempt=attempt,
                        )
                    except (ValueError, TypeError, OSError, RuntimeError) as exc:
                        basis_issues.append(str(exc))
                blockers = _runtime.db_task_findings_blockers(root, str(state.get("task_id") or ""))
                if blockers:
                    basis_issues.append("canonical task findings still contain blockers")
                open_questions = _runtime._open_blocking_questions(
                    task_dir, state, str(attempt.get("attempt_id") or ""),
                )
                if open_questions:
                    basis_issues.append("blocking worker questions remain unanswered")
                if basis_issues:
                    return {
                        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                        "ok": False,
                        "outcome": "needs_correction",
                        "code": "governance_closure_basis_incomplete",
                        "retryable": True,
                        "state_mutated": False,
                        "diagnostics": [{
                            "code": "governance_closure_basis_incomplete",
                            "json_pointer": "/closure_outcome",
                            "message": "; ".join(dict.fromkeys(basis_issues)),
                            "field_schema": {"type": "string", "enum": ["blocked"]},
                        }],
                        "next_action": (
                            "Submit closure_outcome=blocked with non-empty blocking_gaps_text. "
                            "Do not claim verified closure or inspect Cortex private state."
                        ),
                    }
            basis_digest = (
                v11_submission.canonical_digest(dict(basis))
                if isinstance(basis, Mapping) else None
            )
            semantic_result = {
                "status": "completed" if closure_outcome == "verified" else "blocked",
                "summary": full_submission.get("report"),
                "findings": [],
                "decisions_needed": [],
                "unresolved": ([{"summary": blocking_gaps_text}] if closure_outcome == "blocked" else []),
                "claims": [],
            }
            metadata_overrides = {"governance_closure": {
                "closure_outcome": closure_outcome,
                "closure_basis_digest": basis_digest,
                "plan_revision": basis.get("plan_revision") if isinstance(basis, Mapping) else None,
            }}
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
            metadata_overrides=metadata_overrides,
        )
        canonical = completed["result"]
        terminal = canonical.get("result_status") or canonical["status"]
        if terminal != "completed":
            _mark_attempt(
                root, task_dir, state["task_id"], attempt["attempt_id"],
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
            root, task_dir, state["task_id"], attempt["attempt_id"],
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
            root, task_dir, state["task_id"], attempt["attempt_id"],
            lifecycle_status=attempt_protocol.LIFECYCLE_COMPLETED,
            result=finalized,
            projection_status=_runtime.RESULT_READY,
        )
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "terminal": True,
        }
    except (ValueError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        if isinstance(exc, AttemptReadReceiptsIncomplete):
            return {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": False,
                "outcome": "required_context_unread",
                "code": "attempt_read_receipts_incomplete",
                "message": (
                    "Required server read receipts are incomplete. Read the complete dispatch "
                    "briefing, then call read_predecessor_result for every predecessor supplied "
                    "by that briefing and consume every returned page. Retry the same semantic "
                    "submission unchanged only after all reads succeed."
                ),
                "retryable": True,
                "state_mutated": False,
                "attempt_budget_consumed": False,
                "worker_replacement_authorized": False,
                "next_action": (
                    "Read every required authorized context page, then retry the exact unchanged "
                    "completion operation. Do not inspect Cortex implementation or private state."
                ),
            }
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
                "next_action": (
                    "Stop this worker because its canonical AttemptResult is already final. "
                    "The coordinator must call read_worker_wave and follow the server-derived "
                    "lifecycle action; do not resubmit the changed payload or spawn a replacement."
                ),
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
                        root, task_dir, state["task_id"], attempt["attempt_id"],
                        lifecycle_status="work_completed",
                        result=existing,
                        finalization_error="generated_projection_failed",
                    )
                except (ValueError, OSError, RuntimeError):
                    pass
                return _public_failure("complete_attempt", exc, finalization=True)
        return _public_failure("complete_attempt", exc)


def _read_coordinator_wave_locked(
    params: Mapping[str, Any],
    task_dir: Any,
    state: dict[str, Any],
    task_ref: str,
    root: Any,
) -> dict[str, Any]:
    """Project one coordinator wave from a single locked canonical snapshot."""
    from cortex_runtime.orchestration_engine import (
        _active_terminal_recovery_breakers,
        _materialize_wave_recovery_queue,
        _pending_product_rework_action,
    )

    reconciliation_count = len(state.get("frontier_reconciliation_receipts") or [])
    plan = _runtime._load_orchestrate_plan(task_dir, state)
    if len(state.get("frontier_reconciliation_receipts") or []) != reconciliation_count:
        _runtime.save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "frontier_reconciled",
            "repaired cached current-wave assignments from canonical plan identity",
        )
    auxiliary = next(
        (
            item for item in plan.get("auxiliary_waves") or []
            if isinstance(item, Mapping) and item.get("status") == "active"
        ),
        None,
    )
    if isinstance(auxiliary, Mapping):
        auxiliary_attempt_ids = [
            str(item).strip() for item in auxiliary.get("attempt_ids") or [] if str(item).strip()
        ]
        if not auxiliary_attempt_ids:
            raise ValueError("active auxiliary wave has no canonical attempt assignments")
        stopped_control = _stopped_wave_control(
            root, task_dir, state, task_ref, auxiliary_attempt_ids,
            preserve_task_needs_input=True,
        )
        if stopped_control is not None:
            if stopped_control.get("_server_recovery_required"):
                stopped_control["_auxiliary_wave_id"] = str(auxiliary.get("wave_id") or "")
            return stopped_control
        bindings = require_wave_native_completion_observed(root, state, auxiliary_attempt_ids)
        sources = [attempt for attempt, _result_ref in bindings]
        result_refs = [result_ref for _attempt, result_ref in bindings]
        result_views = [
            attempt_protocol.build_attempt_result_view(
                root,
                task_id=str(state["task_id"]),
                attempt_id=str(source["attempt_id"]),
            )
            for source in sources
        ]
        question_attempt_id = str(auxiliary.get("question_attempt_id") or "")
        question_ref = str(auxiliary.get("question_ref") or "")
        question = _open_question_for_attempt(root, state, question_attempt_id)
        if (
            not isinstance(question, Mapping)
            or str(question.get("question_ref") or "") != question_ref
            or str(question.get("status") or "") != "open"
        ):
            raise ValueError("the auxiliary wave no longer matches its exact durable open question")
        auxiliary["status"] = "completed"
        auxiliary["result_refs"] = result_refs
        auxiliary["completed_at"] = _runtime.now()
        _write_orchestrate_plan(task_dir, plan)
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "action": "obtain_user_decision",
            "task_ref": task_ref,
            "question_ref": question_ref,
            "content": str(question.get("question_text") or ""),
            "report": result_views,
            "result_views": result_views,
            "result_refs": result_refs,
            "complete": True,
            "state_mutated": True,
        }
    pending_reworks = [
        item for item in state.get("pending_product_reworks") or []
        if isinstance(item, Mapping) and str(item.get("source_result_ref") or "")
    ]
    if pending_reworks and not _active_terminal_recovery_breakers(state, plan):
        return _pending_product_rework_action(task_ref, pending_reworks[0])
    wave, frontier_attempts = _runtime._effective_plan_frontier(plan, state)
    if not isinstance(wave, Mapping):
        raise ValueError("current active wave is unavailable")
    expected_step = compiled_wave_execution_position(
        [item for item in plan.get("waves") or [] if isinstance(item, Mapping)],
        str(wave.get("wave_ref") or ""),
    )
    wave_attempt_ids = [
        str(item.get("attempt_id") or "").strip()
        for item in frontier_attempts
        if str(item.get("attempt_id") or "").strip()
    ]
    if not wave_attempt_ids:
        raise ValueError("current active wave has no dispatched assignment frontier")
    try:
        resumed_epoch = _runtime._v11_resumed_host_epoch_recovery_required(task_dir, state)
    except ValueError:
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "action": "none",
            "task_ref": task_ref,
            "code": "native_host_epoch_recovery_unavailable",
            "content": (
                "This active task predates authenticated host-epoch ownership or its host boundary is "
                "ambiguous. Stop task-scoped calls. Do not wait, spawn, continue, or infer ownership."
            ),
            "complete": True,
            "retryable": False,
            "state_mutated": False,
        }
    if resumed_epoch:
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "action": "resume_orchestration",
            "task_ref": task_ref,
            "content": (
                "Call resume_orchestration exactly once now with the exact task_ref and coordinator_ref. "
                "Cortex authenticated a new exclusive Codex host epoch and will replace every unfinished "
                "prior-epoch assignment with new native children. Do not wait, reread this wave, continue, "
                "or create a child first."
            ),
            "complete": True,
            "state_mutated": False,
        }
    orphaned_followup = _runtime._v11_unemitted_same_child_followup_attempt(
        root, task_dir, state,
    )
    if (
        isinstance(orphaned_followup, Mapping)
        and str(orphaned_followup.get("attempt_id") or "") in set(wave_attempt_ids)
    ):
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "action": "continue",
            "task_ref": task_ref,
            "content": (
                "Call continue_orchestration now with the exact task_ref and coordinator_ref. "
                "Cortex will reconcile the one orphaned same-child repair response and return its exact "
                "followup_task. Do not call wait_agent or read_worker_wave again before that continuation, "
                "and do not spawn a new child."
            ),
            "complete": True,
            "state_mutated": False,
        }
    stopped_control = _stopped_wave_control(
        root, task_dir, state, task_ref, wave_attempt_ids,
    )
    if stopped_control is not None:
        return stopped_control
    bindings = require_wave_native_completion_observed(root, state, wave_attempt_ids)
    sources = [attempt for attempt, _result_ref in bindings]
    result_refs = [result_ref for _attempt, result_ref in bindings]
    dispatch_refs = [str(source.get("dispatch_ref") or "").strip() for source in sources]
    if (
        any(not item for item in dispatch_refs)
        or len(set(result_refs)) != len(result_refs)
        or len(set(dispatch_refs)) != len(dispatch_refs)
    ):
        raise ValueError("current wave result or dispatch identity is duplicated")
    result_views = [
        attempt_protocol.build_attempt_result_view(
            root,
            task_id=str(state["task_id"]),
            attempt_id=str(source["attempt_id"]),
        )
        for source in sources
    ]
    if any(
        str(view.get("attempt_result_ref") or "") != result_ref
        for view, result_ref in zip(result_views, result_refs)
    ):
        raise ValueError("current wave result identity does not match canonical state")
    recovery_queue = _materialize_wave_recovery_queue(
        task_dir,
        state,
        plan,
        wave,
        sources,
        event="worker_wave_recovery_queue_materialized",
        consume_continuation=False,
    )
    result: dict[str, Any] = {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "task_ref": task_ref,
        "result_views": result_views,
        "complete": True,
    }
    if (
        recovery_queue["product_reworks"]
        and not recovery_queue["technical_failure_attempts"]
    ):
        return _pending_product_rework_action(
            task_ref,
            recovery_queue["product_reworks"][0],
            state_mutated=bool(recovery_queue["state_mutated"]),
        )
    if all(view.get("lifecycle_status") == attempt_protocol.LIFECYCLE_COMPLETED for view in result_views):
        result["continuation"] = {
            "task_id": str(state["task_id"]),
            "step": expected_step,
            "results": [
                (
                    {"attempt_result_ref": result_ref, "worker": slot}
                    if len(result_refs) > 1
                    else {"attempt_result_ref": result_ref}
                )
                for slot, result_ref in enumerate(result_refs, 1)
            ],
        }
    else:
        terminal_source = next(
            (
                source for source, view in zip(sources, result_views)
                if view.get("lifecycle_status")
                in {attempt_protocol.LIFECYCLE_BLOCKED, attempt_protocol.LIFECYCLE_FAILED}
            ),
            None,
        )
        if terminal_source is not None:
            terminal_ref = str(terminal_source.get("attempt_result_ref") or "")
            terminal_view = result_views[sources.index(terminal_source)]
            terminal_continuation, _ = _coordinator_terminal_continuation(
                task_dir,
                state,
                terminal_source,
                terminal_ref,
                lifecycle_status=terminal_view.get("lifecycle_status"),
                result_view=terminal_view,
            )
            if terminal_continuation is not None:
                result["terminal_continuation"] = terminal_continuation
    return result


def _completion_wait_failure() -> dict[str, Any]:
    return {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "outcome": "waiting_workers",
        "action": "wait_for_bound_workers",
        "message": (
            "Invoke wait_agent again now for the exact bound child. Do not call read_worker_wave again until "
            "wait_agent reports that child terminal and gives the host time to record its terminal SubagentStop; "
            "if this response recurs, invoke wait_agent again."
        ),
        "content": WAIT_BEFORE_READ_INSTRUCTION,
        "retryable": False,
        "state_mutated": False,
    }


def _completion_barrier_unavailable(code: str = "native_completion_observation_unavailable") -> dict[str, Any]:
    return {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "outcome": code,
        "code": code,
        "message": "The durable native completion observation boundary is temporarily unavailable.",
        "retryable": True,
        "state_mutated": False,
    }


def _recover_stopped_auxiliary_wave(
    params: Mapping[str, Any],
    project: Any,
    task_ref: str,
    auxiliary_wave_id: str,
    failed_attempt_ids: Sequence[str],
) -> dict[str, Any]:
    """Replace only the failed auxiliary slot while preserving its question pause."""
    project_root = str(project)
    root = _runtime.ledger_root_path_internal(project, create=False)
    with _runtime.state_lock(
        root,
        operation="recover_stopped_auxiliary_wave",
        task_id=str(params.get("task_id") or ""),
    ):
        _project, task_dir, state, task, resolved_ref = _runtime.authorize_coordinator_ref(
            params, "read_worker_result",
        )
        if resolved_ref != task_ref or state.get("status") != "needs_input":
            raise ValueError("auxiliary recovery lost the original durable question pause")
        plan = _runtime._load_orchestrate_plan(task_dir, state)
        auxiliary = next(
            (
                item for item in plan.get("auxiliary_waves") or []
                if isinstance(item, dict)
                and str(item.get("wave_id") or "") == auxiliary_wave_id
                and item.get("status") == "active"
            ),
            None,
        )
        if not isinstance(auxiliary, dict):
            raise ValueError("the stopped auxiliary wave is no longer active")
        expected_ids = [str(item) for item in auxiliary.get("attempt_ids") or []]
        failed_ids = [str(item) for item in failed_attempt_ids if str(item)]
        if len(expected_ids) != 1 or failed_ids != expected_ids:
            raise ValueError("auxiliary recovery must identify exactly its one stopped assignment")
        stopped = next(
            (
                item for item in state.get("attempts") or []
                if isinstance(item, dict) and str(item.get("attempt_id") or "") == failed_ids[0]
            ),
            None,
        )
        if (
            not isinstance(stopped, dict)
            or stopped.get("invalidated")
            or stopped.get("status") != "failed"
            or stopped.get("host_stop_outcome") != "native_worker_stopped_without_result"
            or str(stopped.get("orchestration_wave_id") or "") != auxiliary_wave_id
        ):
            raise ValueError("auxiliary recovery target is not the exact result-less stopped assignment")
        recovery_count = int(auxiliary.get("recovery_count") or 0)
        if recovery_count >= 1:
            pending_question = _open_question_for_attempt(
                root, state, str(auxiliary.get("question_attempt_id") or ""),
            )
            if (
                not isinstance(pending_question, Mapping)
                or str(pending_question.get("question_ref") or "")
                != str(auxiliary.get("question_ref") or "")
                or str(pending_question.get("status") or "") != "open"
            ):
                raise ValueError("auxiliary recovery exhausted without its exact original open question")
            return {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "action": "obtain_user_decision",
                "outcome": "auxiliary_recovery_exhausted",
                "task_ref": task_ref,
                "question_ref": str(pending_question.get("question_ref") or ""),
                "content": str(pending_question.get("question_text") or ""),
                "report": (
                    "The bounded auxiliary replacement stopped without a canonical result and no further "
                    "auxiliary replacement is authorized. The original durable question remains unchanged."
                ),
                "state_mutated": True,
            }

        question_attempt_id = str(auxiliary.get("question_attempt_id") or "")
        question_ref = str(auxiliary.get("question_ref") or "")
        question = _open_question_for_attempt(root, state, question_attempt_id)
        question_attempt = next(
            (
                item for item in state.get("attempts") or []
                if isinstance(item, dict) and str(item.get("attempt_id") or "") == question_attempt_id
            ),
            None,
        )
        if (
            not isinstance(question, Mapping)
            or str(question.get("question_ref") or "") != question_ref
            or str(question.get("status") or "") != "open"
            or not isinstance(question_attempt, dict)
            or question_attempt.get("status") != "waiting_question"
        ):
            raise ValueError("auxiliary recovery cannot prove the exact original open question binding")
        question_before = _runtime.canonical_json.dumps(question)
        attempt_before = _runtime.canonical_json.dumps(question_attempt)
        delegations = auxiliary.get("delegations")
        if not isinstance(delegations, list) or len(delegations) != 1 or not isinstance(delegations[0], dict):
            raise ValueError("auxiliary recovery has no exact coordinator-authored worker route")
        replacement_spec = dict(delegations[0])
        replacement_spec["orchestration_wave_id"] = auxiliary_wave_id
        replacement_spec["orchestration_delegation_key"] = (
            f"{auxiliary_wave_id}-{str(replacement_spec.get('gate') or '')}-recovery-01"
        )

        stopped["invalidated"] = True
        stopped["invalidated_at"] = _runtime.now()
        stopped["invalidation_reason"] = "auxiliary_resultless_stop_replacement"
        _runtime.save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "auxiliary_stop_retired",
            "retired only the result-less stopped auxiliary assignment",
        )
        observed = _runtime.status({
            "project_root": project_root,
            "principal": state.get("principal"),
            "task_id": state["task_id"],
        })
        delegated = _runtime.record_delegation({
            "project_root": project_root,
            "principal": state.get("principal"),
            "task_id": state["task_id"],
            "expected_revision": observed["state"]["revision"],
            "status_receipt": observed["status_receipt"],
            "_pending_question_auxiliary": True,
            **replacement_spec,
        })
        if delegated.get("recorded") is False:
            raise ValueError(str(delegated.get("reason") or "auxiliary replacement was not recorded"))
        state = delegated["state"]
        replacement_id = str(delegated["attempt_id"])
        auxiliary["attempt_ids"] = [replacement_id]
        auxiliary["replacement_of"] = failed_ids[0]
        auxiliary["recovery_count"] = recovery_count + 1
        auxiliary["recovered_at"] = _runtime.now()
        _write_orchestrate_plan(task_dir, plan)

        after_question = _open_question_for_attempt(root, state, question_attempt_id)
        after_attempt = next(
            (
                item for item in state.get("attempts") or []
                if isinstance(item, dict) and str(item.get("attempt_id") or "") == question_attempt_id
            ),
            None,
        )
        if (
            state.get("status") != "needs_input"
            or not isinstance(after_question, Mapping)
            or not isinstance(after_attempt, dict)
            or _runtime.canonical_json.dumps(after_question) != question_before
            or _runtime.canonical_json.dumps(after_attempt) != attempt_before
        ):
            raise RuntimeError("auxiliary recovery changed the original question or bound worker")
        old = {
            "ok": True,
            "state": "ready_to_spawn",
            "task_id": state["task_id"],
            "wave_id": auxiliary_wave_id,
            "spawn_requests": [{
                **dict(delegated["spawn_request"]),
                "attempt_id": replacement_id,
            }],
        }

    private = _runtime._v11_response(old, task_ref, include_result=True)
    nested_action = private.get("action") if isinstance(private.get("action"), Mapping) else {}
    if private.get("ok") is True and nested_action.get("kind") == "invoke_dispatches":
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "action": "invoke_dispatches",
            "task_ref": task_ref,
            "dispatches": private.get("dispatches") or [],
            "content": (
                "Spawn only the returned auxiliary replacement once, preserve its exact returned child identifier, "
                "then invoke wait_agent for that child. Read the worker wave only after it is terminal. The original "
                "durable question remains open on its exact worker."
            ),
            "state_mutated": True,
        }
    if private.get("ok") is False:
        return dict(private)
    return {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "action": "obtain_user_decision",
        "outcome": "auxiliary_recovery_unavailable",
        "task_ref": task_ref,
        "question_ref": question_ref,
        "content": str(after_question.get("question_text") or ""),
        "report": (
            "No exact auxiliary replacement dispatch was available. The original durable question remains "
            "unchanged and is the only executable continuation."
        ),
        "state_mutated": True,
    }


def _recover_stopped_wave(
    project: Any,
    state: Mapping[str, Any],
    task_ref: str,
) -> dict[str, Any]:
    """Dispatch selected-route recovery after terminal observation failure.

    This runs only after the read lock is released because dispatch hydration
    acquires the same task lock. It covers either a trusted result-less Stop or
    an expired canonical-result wait whose native Stop never arrived; neither
    condition may be turned back into an unbounded wait response.
    """
    task_id = str(state.get("task_id") or "")
    principal = str(state.get("principal") or "")
    failures = state.get("native_stop_failures")
    active_attempt_ids = {
        str(item.get("attempt_id") or "")
        for item in state.get("attempts") or []
        if isinstance(item, Mapping)
        and not item.get("invalidated")
        and item.get("status") == "failed"
        and item.get("host_stop_outcome") in {
            "native_worker_stopped_without_result",
            "native_completion_observation_expired",
        }
        and str(item.get("attempt_id") or "")
    }
    recovery_failures = sorted(
        (
            {
                "attempt_id": str(item.get("attempt_id") or ""),
                "wave_ref": str(item.get("wave_ref") or ""),
                "phase_ref": str(item.get("phase_ref") or ""),
                "recorded_at": str(item.get("recorded_at") or ""),
            }
            for item in failures or []
            if isinstance(failures, list)
            if isinstance(item, Mapping)
            and str(item.get("attempt_id") or "") in active_attempt_ids
        ),
        key=lambda item: (
            item["wave_ref"], item["phase_ref"], item["attempt_id"], item["recorded_at"],
        ),
    )
    if not recovery_failures:
        raise ValueError("native completion recovery has no exact active failure batch")
    completion_observation_expired = any(
        isinstance(item, Mapping)
        and str(item.get("attempt_id") or "") in active_attempt_ids
        and item.get("host_stop_outcome") == "native_completion_observation_expired"
        for item in state.get("attempts") or []
    )
    recovery_identity = json.dumps(
        {
            "task_id": task_id,
            "failures": recovery_failures,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    submission_id = "native-stop-recovery-" + _runtime.digest_text(recovery_identity)[:20]
    old = _runtime._engine_manage_lifecycle("resume", {
        "project_root": str(project),
        "principal": principal,
        "task_id": task_id,
        "submission_id": submission_id,
        "reason": (
            "Canonical worker result reached its bounded native Stop deadline."
            if completion_observation_expired
            else "Trusted native child stopped without a canonical AttemptResult."
        ),
        "terminal_recovery": True,
    })
    private = _runtime._v11_response(old, task_ref, include_result=True)
    nested_action = private.get("action") if isinstance(private.get("action"), Mapping) else {}
    action = str(nested_action.get("kind") or "")
    if private.get("ok") is True and action == "invoke_dispatches":
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "action": "invoke_dispatches",
            "task_ref": task_ref,
            "step": private.get("step"),
            "dispatches": private.get("dispatches") or [],
            "content": (
                "Spawn every returned selected-route replacement exactly once, then wait for those exact "
                "native children and read_worker_wave again. Do not resume the stopped child."
            ),
            "state_mutated": True,
        }
    if private.get("ok") is False:
        return dict(private)
    return {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "outcome": "native_stop_recovery_unavailable",
        "code": "native_stop_recovery_unavailable",
        "message": (
            "The trusted native child is terminal and no safe selected-route replacement was produced. "
            "Stop this task-scoped operation; do not wait for or resume the stopped child."
        ),
        "retryable": False,
        "state_mutated": True,
    }

def _report_ref(
    secret: bytes, dispatch_ref: str, result_ref: str,
) -> str:
    payload = f"{dispatch_ref}\0{result_ref}".encode("utf-8")
    return "report-v1-" + hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _dispatch_report_bindings(
    root: Any,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> tuple[dict[str, str], set[str], set[str]]:
    """Resolve one dispatch's required and optional opaque report catalog."""
    required = [str(item) for item in attempt.get("predecessor_result_refs") or []]
    optional = [str(item) for item in attempt.get("optional_report_result_refs") or []]
    if (
        any(not item for item in required + optional)
        or len(required) != len(set(required))
        or len(optional) != len(set(optional))
        or set(required).intersection(optional)
    ):
        raise ValueError("dispatch report catalog authority is invalid")
    expected_digest = "sha256:" + _runtime.digest_text(canonical_json.dumps({
        "required": required, "optional": optional,
    }))
    if str(attempt.get("report_catalog_digest") or "") != expected_digest:
        raise ValueError("dispatch report catalog digest changed")
    secret = _runtime._governance_lifecycle_hmac_key(root, create=False)
    dispatch_ref = str(attempt.get("dispatch_ref") or "")
    bindings: dict[str, str] = {}
    for result_ref in [*required, *optional]:
        sources = [
            item for item in state.get("attempts") or []
            if isinstance(item, Mapping)
            and str(item.get("attempt_result_ref") or "") == result_ref
        ]
        if len(sources) != 1:
            raise ValueError("dispatch report catalog result is unavailable")
        report_ref = _report_ref(secret, dispatch_ref, result_ref)
        if report_ref in bindings:
            raise ValueError("dispatch report catalog reference collision")
        bindings[report_ref] = result_ref
    expected_required_report_refs = [
        report_ref for report_ref, result_ref in bindings.items() if result_ref in set(required)
    ]
    expected_optional_report_refs = [
        report_ref for report_ref, result_ref in bindings.items() if result_ref in set(optional)
    ]
    if (
        list(attempt.get("required_report_refs") or []) != expected_required_report_refs
        or list(attempt.get("optional_report_refs") or []) != expected_optional_report_refs
    ):
        raise ValueError("dispatch opaque report references changed")
    return bindings, set(required), set(optional)


def _worker_report_catalog(
    root: Any,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> dict[str, Any]:
    bindings, required, _optional = _dispatch_report_bindings(root, state, attempt)
    entries: list[dict[str, Any]] = []
    for report_ref, result_ref in bindings.items():
        source = next(
            item for item in state.get("attempts") or []
            if isinstance(item, Mapping)
            and str(item.get("attempt_result_ref") or "") == result_ref
        )
        require_wave_native_completion_observed(
            root, state, [str(source.get("attempt_id") or "")],
        )
        view = attempt_protocol.build_attempt_result_view(
            root,
            task_id=str(state.get("task_id") or ""),
            attempt_id=str(source.get("attempt_id") or ""),
        )
        evaluation = source.get("acceptance_evaluation")
        if isinstance(evaluation, Mapping):
            view["server_evaluation"] = dict(evaluation)
        projection = _public_result_projection(view)
        findings = projection.get("findings") or []
        severities: dict[str, int] = {}
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            severity = str(finding.get("severity") or "unspecified")
            severities[severity] = severities.get(severity, 0) + 1
        labels = sorted({
            str(item.get("kind") or "")
            for item in projection.get("verification_observations") or []
            if isinstance(item, Mapping) and str(item.get("kind") or "")
        })
        content_length = len(json.dumps(
            projection, ensure_ascii=False, sort_keys=True,
        ))
        entries.append({
            "report_ref": report_ref,
            "required": result_ref in required,
            "phase_ref": str(source.get("phase_ref") or ""),
            "wave_ref": str(source.get("wave_ref") or ""),
            "profile": str(source.get("profile") or source.get("agent") or ""),
            "operation_kind": str(source.get("operation_kind") or ""),
            "outcome": str(projection.get("status") or ""),
            "summary": str(projection.get("summary") or "")[:320],
            "finding_count": len(findings),
            "finding_severity_counts": severities,
            "verification_labels": labels,
            "changed_file_count": len(projection.get("changed_files") or []),
            "content_length": content_length,
            "page_count": max(1, (content_length + 7999) // 8000),
            "audit_status": (
                "invalidated" if source.get("invalidated") else
                "superseded" if str(source.get("status") or "") == "superseded" else
                "current"
            ),
        })
    return {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "action": "list_reports",
        "catalog_entries": entries,
        "complete": True,
    }


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
            project, task_dir, state, _task, task_ref = _runtime.authorize_coordinator_ref(
                params, "read_worker_result",
            )
            raw_attempt_id = ""
            raw_profile = ""
        result_ref = ""
        if worker_context:
            attempt_id = _runtime.safe_id(raw_attempt_id)
            attempt = _runtime._attempt(state, attempt_id)
            profile = _runtime.canonical_profile(raw_profile)
            if attempt.get("invalidated") or attempt.get("status") not in _ACTIVE_ATTEMPT_STATUSES:
                raise ValueError("successor worker result reads require an active, non-invalidated attempt")
            if attempt.get("profile") != profile:
                raise ValueError("successor worker profile does not match the delegated attempt")
        root = _runtime._task_document_root(task_dir, state["task_id"])
        if worker_context:
            if str(params.get("action") or "") == "list_reports":
                return _worker_report_catalog(root, state, attempt)
            report_ref = str(params.get("report_ref") or "").strip()
            bindings, _required_refs, _optional_refs = _dispatch_report_bindings(
                root, state, attempt,
            )
            result_ref = bindings.get(report_ref, "")
            if not result_ref:
                raise ValueError("report_ref is not authorized for this dispatch")
            source = next(
                (candidate for candidate in state.get("attempts") or []
                 if str(candidate.get("attempt_result_ref") or "") == result_ref),
                None,
            )
            if not isinstance(source, Mapping):
                raise ValueError("report_ref does not belong to the selected Cortex task")
            # A predecessor result is consumable only after its exact native
            # child has emitted terminal SubagentStop. This keeps successor
            # reads on the same host completion boundary as coordinator reads.
            require_wave_native_completion_observed(
                root, state, [str(source.get("attempt_id") or "")],
            )
            view = attempt_protocol.build_attempt_result_view(
                root, task_id=state["task_id"], attempt_id=str(source["attempt_id"]),
            )
            if view["attempt_result_ref"] != result_ref:
                raise ValueError("attempt_result_ref does not match its canonical result")
            result = {
                "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "report_ref": report_ref,
                "attempt_result_ref": result_ref,
                "result_view": view,
                "complete": True,
            }
            evaluation = source.get("acceptance_evaluation")
            if isinstance(evaluation, Mapping):
                view["server_evaluation"] = dict(evaluation)
            return result

        with _runtime.state_lock(
            root,
            operation="read_worker_wave",
            task_id=str(state["task_id"]),
        ):
            project, task_dir, state, _task, task_ref = _runtime.authorize_coordinator_ref(
                params, "read_worker_result",
            )
            root = _runtime._task_document_root(task_dir, state["task_id"])
            # Recheck the authenticated host epoch under the same task lock
            # before Stop reconciliation, lease expiry, replay, or recovery.
            # A prior-host child is never safe to read or wait after a proven
            # coordinator-incarnation handoff.
            try:
                compact_boundary = _runtime._v11_pending_context_boundary(task_dir, state)
                resumed_epoch = _runtime._v11_resumed_host_epoch_recovery_required(
                    task_dir, state,
                )
            except ValueError:
                return {
                    "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                    "ok": False,
                    "action": "none",
                    "task_ref": task_ref,
                    "code": "native_host_epoch_recovery_unavailable",
                    "content": (
                        "This active task has no authenticated recoverable host epoch. "
                        "Stop task-scoped calls; do not wait, read, continue, spawn, or infer ownership."
                    ),
                    "complete": True,
                    "retryable": False,
                    "state_mutated": False,
                }
            if compact_boundary is not None:
                return {
                    "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                    "ok": True,
                    "action": "inspect_orchestration",
                    "task_ref": task_ref,
                    "content": (
                        "Call inspect_orchestration now and consume every returned page until complete=true. "
                        "Do not wait, reread this wave, continue, resume, replay a followup_task, or create a child first."
                    ),
                    "complete": True,
                    "state_mutated": False,
                }
            if resumed_epoch:
                return {
                    "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
                    "ok": True,
                    "action": "resume_orchestration",
                    "task_ref": task_ref,
                    "content": (
                        "Call resume_orchestration exactly once now with the exact task_ref and "
                        "coordinator_ref. Do not wait, reread this wave, continue, replay a "
                        "followup_task, or create a child first."
                    ),
                    "complete": True,
                    "state_mutated": False,
                }
            reconcile_native_stop_inbox(root, task_dir, state)
            from cortex_runtime.orchestration_engine import _expire_lifecycle_attempts

            expired_attempt_ids = _expire_lifecycle_attempts(root, task_dir, state)
            completion_expired = [
                attempt_id for attempt_id in expired_attempt_ids
                if str(_runtime._attempt(state, attempt_id).get("host_stop_outcome") or "")
                == "native_completion_observation_expired"
            ]
            if completion_expired:
                read_result = {
                    "_server_recovery_required": True,
                    "_recovery_attempt_ids": completion_expired,
                }
            else:
                read_result = _read_coordinator_wave_locked(
                    params, task_dir, state, task_ref, root,
                )
        auxiliary_wave_id = str(read_result.pop("_auxiliary_wave_id", "") or "")
        recovery_attempt_ids = read_result.pop("_recovery_attempt_ids", [])
        server_recovery_required = bool(read_result.pop("_server_recovery_required", False))
        if server_recovery_required and auxiliary_wave_id:
            return _recover_stopped_auxiliary_wave(
                params,
                project,
                task_ref,
                auxiliary_wave_id,
                recovery_attempt_ids if isinstance(recovery_attempt_ids, list) else [],
            )
        if server_recovery_required:
            return _recover_stopped_wave(project, state, task_ref)
        return read_result

    except NativeCompletionObservationRequired:
        return _completion_wait_failure()
    except NativeCompletionObservationUnavailable:
        return _completion_barrier_unavailable()
    except _runtime.LedgerBusyError:
        return _completion_barrier_unavailable("ledger_busy")
    except (ValueError, TypeError, OSError) as exc:
        return _public_failure("read_worker_result", exc)
    except Exception:
        # An unclassified server defect must never become an infinite
        # same-operation loop. Exact recoverable states are projected above;
        # anything reaching this boundary stops rather than inventing work.
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "code": "read_worker_wave_internal_error",
            "message": (
                "Cortex could not project a legal canonical worker-wave action. Stop this task-scoped "
                "operation; do not retry, wait, inspect private state, or create a replacement child."
            ),
            "retryable": False,
            "state_mutated": False,
        }


def record_attempt_event(params: dict[str, Any]) -> dict[str, Any]:
    """Return only the closed public worker-event receipt."""
    from cortex_runtime.mcp_api import project_public_response
    return project_public_response(
        "record_attempt_event", _record_attempt_event_impl(params), arguments=params,
    )


def record_worker_finding(params: dict[str, Any]) -> dict[str, Any]:
    """Return only the closed public worker-finding receipt."""
    from cortex_runtime.mcp_api import project_public_response
    return project_public_response(
        "record_worker_finding", _record_worker_finding_impl(params), arguments=params,
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
        summary = canonical.get("summary")
        if isinstance(summary, str) and status:
            projection = {
                "status": status,
                "summary": summary,
                "findings": list(canonical.get("findings") or []),
                "decisions_needed": list(canonical.get("decisions_needed") or []),
                "unresolved": list(canonical.get("unresolved") or []),
                "claims": list(canonical.get("claims") or []),
                "changed_files": list(canonical.get("changed_files") or []),
                "changed_files_status": str(canonical.get("changed_files_status") or ""),
                "verification_observations": [
                    event.get("payload")
                    for event in value.get("events") or []
                    if isinstance(event, Mapping)
                    and str(event.get("event_type") or "") == "verification_observation"
                    and isinstance(event.get("payload"), Mapping)
                ],
            }
            evaluation = value.get("server_evaluation")
            if isinstance(evaluation, Mapping):
                projection["server_evaluation"] = {
                    key: evaluation.get(key)
                    for key in (
                        "protocol_status", "acceptance_status", "failure_class", "reasons",
                        "required_verification_kinds", "server_observed_verification_kinds",
                        "worker_attested_verification_kinds", "missing_verification_kinds",
                        "blocking_finding_fingerprints",
                    )
                    if evaluation.get(key) not in (None, "", [], {})
                }
            return projection
    # Fail closed for a malformed server view: do not expose private view
    # topology or silently invent a second public result representation.
    raise ValueError("canonical result view cannot be projected as status and report")


def _flat_result_page(result: dict[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    """Return deterministic immutable report pages bound to the selected action."""
    if not result.get("ok"):
        return result
    if str(result.get("action") or "") in {
        "resume_bound_worker", "obtain_user_decision", "invoke_dispatches",
        "wait_for_bound_workers", "append_rework_wave", "continue",
        "resume_orchestration",
    }:
        return result
    action = str(params.get("action") or "")
    refs = [str(item.get("attempt_result_ref") or "") for item in result.get("result_views") or []]
    if action == "list_reports":
        entries = [
            item for item in result.get("catalog_entries") or []
            if isinstance(item, Mapping)
        ]
        refs = [str(item.get("report_ref") or "") for item in entries]
        text = "\n".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) for item in entries
        )
    elif action == "read_predecessor":
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
    receipt_attempt: Mapping[str, Any] | None = None
    receipt_state: Mapping[str, Any] | None = None
    if action in {"read_predecessor", "list_reports"}:
        project, _task_dir, state, attempt, _profile = _runtime.authorize_worker_assignment(
            params, "read_worker_result",
        )
        receipt_attempt = attempt
        receipt_state = state
        audience_basis = {
            "task_id": state.get("task_id"),
            "attempt_id": attempt.get("attempt_id"),
        }
        root = _runtime.ledger_root_path_internal(project, create=False)
    else:
        project, _task_dir, state, _task, _task_ref = _runtime.authorize_coordinator_ref(
            params, "read_worker_result",
        )
        audience_basis = {"task_id": state.get("task_id"), "role": "coordinator"}
        root = _runtime.ledger_root({"project_root": str(project)})
    # Bind the cursor to the already-authorized durable subject, not to a
    # digest of the bearer capability. The opaque cursor therefore carries
    # no reusable verifier derived from dispatch_ref or coordinator_ref.
    audience = f"{action}.{pagination.scope_digest(audience_basis)[:32]}"
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
    page = {
        "schema": result.get("schema"), "ok": True, "action": "read_more",
        "content": content, "complete": False,
    }
    if offset + len(content) < len(text):
        page["next_cursor"] = pagination.encode_cursor(
            secret,
            selector=selector_name,
            audience=audience,
            digest=digest,
            offset=offset + len(content),
        )
        return page
    page["complete"] = True
    page["action"] = (
        "reports_listed"
        if action == "list_reports"
        else
        "use_result_as_context"
        if action == "read_predecessor"
        else "revise_or_continue"
        if result.get("continuation") or result.get("terminal_continuation")
        else "terminal_continue"
    )
    if action == "read_predecessor":
        if not isinstance(receipt_attempt, Mapping) or not isinstance(receipt_state, Mapping):
            raise ValueError("complete report read lost worker authorization")
        result_ref = str(result.get("attempt_result_ref") or "")
        if result_ref in {
            str(item) for item in receipt_attempt.get("predecessor_result_refs") or []
        }:
            attempt_protocol.record_predecessor_read(
                root,
                task_id=str(receipt_state.get("task_id") or ""),
                attempt_id=str(receipt_attempt.get("attempt_id") or ""),
                predecessor_result_ref=result_ref,
            )
        elif result_ref in {
            str(item) for item in receipt_attempt.get("optional_report_result_refs") or []
        }:
            attempt_protocol.record_optional_report_read(
                root,
                task_id=str(receipt_state.get("task_id") or ""),
                attempt_id=str(receipt_attempt.get("attempt_id") or ""),
                result_ref=result_ref,
            )
        else:
            raise ValueError("complete report read is outside dispatch catalog authority")
    if action == "read_wave":
        page["result_refs"] = refs[:32]
        if page["action"] == "revise_or_continue":
            page["report"] = page.pop("content")
            page["content"] = (
                "This is the complete canonical wave report. If its evidence requires changing pending "
                "work, call revise_future_pipeline with these result_refs; otherwise call "
                "continue_orchestration. result_refs are AttemptResult evidence capabilities, never "
                "artifact references. Do not call read_task_artifact unless the canonical report "
                "explicitly supplies a distinct artifact-* reference."
            )
    return page


def read_worker_result(params: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical semantic result without its internal projection."""
    from cortex_runtime.mcp_api import project_public_response
    try:
        return project_public_response(
            "read_worker_result", _flat_result_page(_read_worker_result_impl(params), params), arguments=params,
        )
    except Exception:
        # Response projection is still inside the public tool-execution
        # boundary. Keep handler defects structured; JSON-RPC framing and
        # protocol faults remain owned by the transport outside this call.
        return {
            "ok": False,
            "action": "none",
            "retryable": False,
            "state_mutated": False,
            "error_code": "read_worker_wave_internal_error",
            "error": (
                "Cortex could not project a legal canonical worker-wave action. Stop this task-scoped "
                "operation; do not retry, wait, inspect private state, or create a replacement child."
            ),
        }
