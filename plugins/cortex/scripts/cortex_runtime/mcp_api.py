"""Small, policy-neutral MCP stdio transport for the Cortex v12 ledger.

This module intentionally knows nothing about coordinator/worker roles, native
children, host threads, governance state, or lifecycle progression.  It only
advertises one fixed catalogue, validates its schemas, and transports durable
service results as JSON-RPC MCP tool results.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections.abc import Mapping
from typing import Any

from cortex_runtime.v12_service import V12ServiceError
from cortex_runtime.v12_contract import MAX_PAGE_LIMIT, record_ref, record_ref_parts, task_ref, task_ref_parts


MCP_PROTOCOL_VERSION = "2025-06-18"
_MAX_TOOLS = 11
# MCP stdio is JSONL.  Enforce this at the byte transport boundary before a
# JSON parser can allocate for an unbounded physical line.  The terminating
# newline is part of the physical frame when present.
MAX_PHYSICAL_JSONL_FRAME_BYTES = 256 * 1024
_FRAME_READ_LIMIT = MAX_PHYSICAL_JSONL_FRAME_BYTES + 1
_MAX_REQUEST_ID_BYTES = 512
_SERVER_STATE_CODES = frozenset({"storage_unavailable", "ledger_corrupt", "schema_unsupported", "ledger_error"})
_RECOVERY_ACTIONS = {
    "validation_error": "Do not retry a shortened or UI-ellipsized value. For task-anchored tools, reuse structuredContent.handles.task_ref from the last success, then correct any remaining field shape.",
    "invalid_argument": "Correct the named argument and call the same tool again.",
    "invalid_identifier": "Do not retry a shortened value. For task-anchored tools, reuse structuredContent.handles.task_ref byte-for-byte; otherwise reuse the exact emitted identifier.",
    "content_invalid": "Supply finite JSON within the advertised size and depth bounds.",
    "cross_project_reference": "Use references that belong to the supplied task and its resolved project shard.",
    "task_not_found": "Use the task_ref emitted by create_task and verify it was copied byte-for-byte.",
    "task_ref_ambiguous": "Do not guess or expand the task_ref. Use the exact task_ref from the successful create_task result.",
    "delegation_not_found": "Use the exact delegation_ref emitted for this task.",
    "report_not_found": "Use the exact report_ref emitted for this task.",
    "initiative_not_found": "Use the exact initiative_ref from the same resolved project ledger.",
    "decision_not_found": "Use the exact decision_ref emitted for this task.",
    "idempotency_conflict": "Reuse the exact retry handle and byte-identical arguments for the same mutation. A new mutation receives a distinct server retry handle.",
    "task_exists": "Create a distinct task contract or reuse only the exact returned retry handle for the original mutation.",
    "delegation_exists": "Create a distinct delegation contract or reuse only the exact returned retry handle for the original mutation.",
    "report_exists": "Create a distinct report operation or reuse only the exact returned retry handle for the original mutation.",
    "invalid_model_selection": "Select one advertised model and one advertised reasoning_effort as the required atomic pair.",
    "profile_unavailable": "Select an advertised packaged profile after the plugin profile catalogue is available; do not substitute the free-form role.",
    "governance_gate_preapproval": "Before plan approval, use only the allowed planner path or the explicitly parent-linked discovery path with its finalized planner handoff.",
    "governance_gate_links_required": "For a post-approval delegation, include the finalized plan in input_report_refs and the exact approved decision in approval_decision_ref. Keep any additional finalized evidence reports in input_report_refs.",
    "governance_gate_evidence_mismatch": "Refresh the finalized plan and approval decision, then reuse their current compact refs; do not construct or substitute approval evidence.",
    "documentation_impact_required": "Before closure, create a post-approval technical_writer delegation with the approved plan in input_report_refs, approval_decision_ref, and every relevant finalized result report; then have its worker submit a finalized result.",
    "documentation_impact_evidence_missing": "Use the technical_writer worker's concise Summary and exact Report ref from its completed handoff; do not reread the report body as coordinator. Ensure the technical_writer delegation durably links the approved plan, exact approval decision, and every required finalized predecessor report, then retry closure.",
    "initiative_closure_required": "Close every initiative related to the task first. Then submit_governance_closure with subject_type=task and subject_ref exactly equal to task_ref.",
    "invalid_report": "Use report metadata allowed by the selected mode and report_type; plan-only metadata requires a plan creation.",
    "invalid_report_operation": "Use exactly the fields required by the selected report mode and omit fields belonging to other modes.",
    "report_chunk_too_large": "Reduce this content chunk to the advertised report chunk bound and retry the same next chunk index.",
    "report_quota_exceeded": "Reduce retained or assembling report content before creating another report chunk.",
    "report_state_conflict": "Inspect report metadata with read_reports and use an operation valid for its current assembly state.",
    "report_chunk_conflict": "Keep the acknowledged chunk unchanged, or use the next_chunk_index from the accepted append.",
    "report_chunk_out_of_order": "Append exactly the next_chunk_index acknowledged by the report receipt.",
    "report_manifest_mismatch": "Read report metadata, then finalize with the exact current chunk count and content digest.",
    "report_cursor_invalid": "Restart read_reports without cursor, or copy the last returned cursor byte-for-byte.",
    "report_cursor_scope_mismatch": "Reuse the cursor only with the exact original report_refs order and sections filter.",
    "report_cursor_stale": "Restart read_reports without cursor because the selected report snapshot changed.",
    "invalid_governance_mode": "Use one advertised governance mode and source value.",
    "invalid_initiative_status": "Use one advertised initiative status value.",
    "invalid_initiative_parent": "Choose an existing same-project parent that does not introduce a parent cycle.",
    "invalid_closure_subject": "Match subject_type to subject_ref. For a task closure use subject_ref exactly equal to task_ref; omit initiative_status, while opaque completion_notes are allowed.",
    "invalid_decision_subject": "Use an existing subject of the selected type in the supplied task scope.",
    "invalid_decision_type": "Use one advertised decision_type value.",
    "decision_subject_not_finalized": "Finalize the selected plan report with completed status before recording a plan decision.",
    "decision_subject_digest_mismatch": "Copy the current selected subject digest byte-for-byte before recording the decision.",
    "approval_view_required": "For plan approval, read the exact finalized plan until its returned approval_view is ready, then copy its handle and view identifiers byte-for-byte.",
    "approval_view_not_ready": "Refresh the exact plan read until a new ready approval_view is returned; do not construct a path or reuse a stale view.",
    "approval_view_mismatch": "Use the exact report and ready approval_view values returned together by Cortex.",
    "approval_handle_not_found": "Use only the opaque approval_handle returned in the ready approval_view.",
    "approval_handle_mismatch": "Use the exact plan report digest, view digest, source sequence, and single-use approval_handle from one ready approval_view.",
    "approval_handle_consumed": "Read the plan again and obtain a new ready approval_view before recording a different decision.",
    "decision_response_required": "Ask the user for one new explicit approve, request-revision, or cancel response after the ready view, then record that response.",
    "decision_response_reused_original": "The original task request cannot be reused as plan approval; ask for one new explicit response after the ready view.",
}
_PUBLIC_ERROR_MESSAGES = {
    "validation_error": "The supplied arguments do not satisfy the advertised tool schema.",
    "invalid_argument": "A supplied public argument is invalid.",
    "invalid_identifier": "A supplied Cortex identifier is invalid.",
    "content_invalid": "A supplied JSON value is invalid.",
    "cross_project_reference": "A supplied reference is outside the task's project scope.",
    "task_not_found": "The referenced task does not exist in the resolved V12 shard.",
    "task_ref_ambiguous": "The compact task locator is ambiguous and was not resolved.",
    "delegation_not_found": "The referenced delegation does not exist in the anchored task.",
    "report_not_found": "The referenced report does not exist in the anchored task.",
    "initiative_not_found": "The referenced initiative does not exist in the resolved V12 shard.",
    "decision_not_found": "The referenced user decision does not exist in the anchored task.",
    "idempotency_conflict": "The idempotency key was already used for different arguments.",
    "task_exists": "The supplied task identifier already exists.",
    "delegation_exists": "The supplied delegation identifier already exists.",
    "report_exists": "The supplied report identifier already exists.",
    "invalid_model_selection": "The supplied model and reasoning-effort selection is invalid.",
    "profile_unavailable": "The selected packaged profile is unavailable.",
    "governance_gate_preapproval": "The delegation is not permitted before plan approval.",
    "governance_gate_links_required": "The delegation is missing required approved-plan evidence.",
    "governance_gate_evidence_mismatch": "The supplied approved-plan evidence is inconsistent.",
    "documentation_impact_required": "A worker-owned documentation-impact assessment is required before closure.",
    "documentation_impact_evidence_missing": "The required worker-owned documentation-impact evidence is not durably linked to the approved plan, decision, and predecessor reports.",
    "initiative_closure_required": "Every task-related initiative requires a distinct closure before task closure.",
    "invalid_report": "The supplied report metadata is invalid.",
    "invalid_report_operation": "The supplied report operation is invalid.",
    "report_chunk_too_large": "The supplied report chunk exceeds its allowed size.",
    "report_quota_exceeded": "The report retention or assembly quota is exhausted.",
    "report_state_conflict": "The report is not in a state that permits this operation.",
    "report_chunk_conflict": "The supplied report chunk conflicts with an accepted chunk.",
    "report_chunk_out_of_order": "The supplied report chunk is not the next accepted chunk.",
    "report_manifest_mismatch": "The supplied report manifest does not match the current assembly.",
    "report_cursor_invalid": "The supplied report cursor is invalid.",
    "report_cursor_scope_mismatch": "The supplied report cursor belongs to a different read scope.",
    "report_cursor_stale": "The supplied report cursor is stale.",
    "invalid_governance_mode": "The supplied governance mode is invalid.",
    "invalid_initiative_status": "The supplied initiative status is invalid.",
    "invalid_initiative_parent": "The supplied initiative parent is invalid.",
    "invalid_closure_subject": "The supplied closure subject is invalid.",
    "invalid_decision_subject": "The supplied decision subject is invalid.",
    "invalid_decision_type": "The supplied decision type is invalid.",
    "decision_subject_not_finalized": "The selected decision subject is not finalized evidence.",
    "decision_subject_digest_mismatch": "The supplied decision subject digest does not match.",
    "approval_view_required": "The plan approval requires an exact ready approval view.",
    "approval_view_not_ready": "The approval view is not currently ready.",
    "approval_view_mismatch": "The supplied approval view does not match the plan.",
    "approval_handle_not_found": "The supplied approval handle was not found.",
    "approval_handle_mismatch": "The supplied approval handle does not match the ready plan view.",
    "approval_handle_consumed": "The supplied approval handle has already been used.",
    "decision_response_required": "The plan decision requires a new explicit user response.",
    "decision_response_reused_original": "The original task request cannot be reused as a plan decision.",
    "project_root_invalid": "The supplied project root is unavailable or is not a directory.",
    "storage_busy": "The V12 ledger is temporarily busy.",
}
_PUBLIC_SERVICE_CODES = frozenset((*_RECOVERY_ACTIONS, *_SERVER_STATE_CODES, "project_root_invalid", "storage_busy"))
_SAFE_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_PATH_RE = re.compile(r"^\$(?:\.[A-Za-z_][A-Za-z0-9_]{0,63}|\[[0-9]{1,4}\]){0,16}$")
_SAFE_EXPECTED_VALUES = frozenset({"restart_without_cursor", "advertised_input_schema"})


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _SchemaError(ValueError):
    def __init__(self, path: str, message: str) -> None:
        super().__init__(message)
        self.path = path
        self.message = message


def _is_json_value(value: object) -> bool:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True


def _type_matches(value: object, expected: object) -> bool:
    expected_values = expected if isinstance(expected, list) else [expected]
    for item in expected_values:
        if item == "object" and isinstance(value, Mapping):
            return True
        if item == "array" and isinstance(value, list):
            return True
        if item == "string" and isinstance(value, str):
            return True
        if item == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if item == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if item == "boolean" and isinstance(value, bool):
            return True
        if item == "null" and value is None:
            return True
    return False


def _encoded_json_bytes(value: object, path: str) -> int:
    """Return a compact UTF-8 JSON size or a public validation failure."""
    try:
        rendered = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise _SchemaError(path, "value is not valid JSON") from error
    return len(rendered.encode("utf-8"))


def _utf8_size_exceeds(value: str, maximum: int) -> bool:
    """Measure a text-stream frame without materializing an encoded copy."""
    total = 0
    for character in value:
        codepoint = ord(character)
        total += 1 if codepoint < 0x80 else len(character.encode("utf-8"))
        if total > maximum:
            return True
    return False


def _drain_binary_line(stream: Any, chunk: bytes) -> None:
    """Discard the remainder of one oversized binary JSONL physical line."""
    while chunk and not chunk.endswith(b"\n"):
        chunk = stream.readline(_FRAME_READ_LIMIT)


def _drain_text_line(stream: Any, chunk: str) -> None:
    """Discard the remainder of one oversized text JSONL physical line."""
    while chunk and not chunk.endswith("\n"):
        chunk = stream.readline(_FRAME_READ_LIMIT)


def _read_physical_jsonl_frame(stream: Any) -> tuple[str | None, bool]:
    """Read one bounded JSONL frame and drain an oversized physical line.

    ``True`` in the second tuple item means an overlong or non-UTF-8 frame
    was consumed and must receive only a generic parse error.  The binary
    path is used for normal stdio so the 256 KiB limit applies to bytes, not
    decoded characters.  The text fallback keeps embedded/unit-test streams
    bounded and applies the equivalent UTF-8 measurement.
    """
    binary_stream = getattr(stream, "buffer", None)
    if callable(getattr(binary_stream, "readline", None)):
        chunk = binary_stream.readline(_FRAME_READ_LIMIT)
        if not chunk:
            return None, False
        if not isinstance(chunk, bytes):
            return "", True
        oversized = len(chunk) > MAX_PHYSICAL_JSONL_FRAME_BYTES
        if oversized:
            _drain_binary_line(binary_stream, chunk)
            return "", True
        try:
            return chunk.decode("utf-8"), False
        except UnicodeDecodeError:
            return "", True

    chunk = stream.readline(_FRAME_READ_LIMIT)
    if not chunk:
        return None, False
    if not isinstance(chunk, str):
        return "", True
    oversized = (
        len(chunk) >= _FRAME_READ_LIMIT
        or _utf8_size_exceeds(chunk, MAX_PHYSICAL_JSONL_FRAME_BYTES)
    )
    if oversized:
        _drain_text_line(stream, chunk)
        return "", True
    return chunk, False


def _validate_schema(schema: Mapping[str, Any], value: object, path: str = "$") -> None:
    """Validate the compact JSON-Schema subset used by the V12 public API."""
    const = schema.get("const")
    if "const" in schema and value != const:
        raise _SchemaError(path, "value does not match the required constant")
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for item in all_of:
            if isinstance(item, Mapping):
                _validate_schema(item, value, path)
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        failures: list[_SchemaError] = []
        for item in any_of:
            if not isinstance(item, Mapping):
                continue
            try:
                _validate_schema(item, value, path)
                break
            except _SchemaError as error:
                failures.append(error)
        else:
            if failures:
                raise failures[0]
            raise _SchemaError(path, "value does not match a permitted input shape")
    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        matches = 0
        failures: list[_SchemaError] = []
        alternatives = [item for item in one_of if isinstance(item, Mapping)]
        # Prefer a branch whose explicit discriminator already matches the
        # supplied object.  This keeps a missing append/finalize field from
        # being reported as the unrelated legacy-single branch.
        if isinstance(value, Mapping):
            matching_discriminators = [
                item for item in alternatives
                if any(
                    isinstance(property_schema, Mapping)
                    and "const" in property_schema
                    and value.get(str(name)) == property_schema.get("const")
                    for name, property_schema in (item.get("properties") or {}).items()
                )
            ]
            if matching_discriminators:
                alternatives = matching_discriminators
        for item in alternatives:
            try:
                _validate_schema(item, value, path)
                matches += 1
            except _SchemaError as error:
                failures.append(error)
        if matches != 1:
            if matches == 0 and failures:
                raise failures[0]
            raise _SchemaError(path, "value must match exactly one permitted input shape")
    prohibited = schema.get("not")
    if isinstance(prohibited, Mapping):
        try:
            _validate_schema(prohibited, value, path)
        except _SchemaError:
            pass
        else:
            required = prohibited.get("required")
            if isinstance(required, list) and len(required) == 1 and isinstance(required[0], str):
                raise _SchemaError(path, f"property {required[0]!r} is not permitted for this input shape")
            raise _SchemaError(path, "value contains a property not permitted for this input shape")
    expected_type = schema.get("type")
    if expected_type is not None and not _type_matches(value, expected_type):
        raise _SchemaError(path, "value has the wrong type")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise _SchemaError(path, "value is not one of the permitted values")
    maximum_bytes = schema.get("maxBytes")
    if isinstance(maximum_bytes, int) and not isinstance(maximum_bytes, bool):
        if _encoded_json_bytes(value, path) > maximum_bytes:
            raise _SchemaError(path, "JSON value exceeds the maximum encoded byte length")
    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise _SchemaError(path, "string is shorter than the minimum length")
        if isinstance(maximum, int) and len(value) > maximum:
            raise _SchemaError(path, "string is longer than the maximum length")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
            raise _SchemaError(path, "string does not match the required pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise _SchemaError(path, "number is below the minimum")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise _SchemaError(path, "number is above the maximum")
    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise _SchemaError(path, "array has too few items")
        if isinstance(maximum, int) and len(value) > maximum:
            raise _SchemaError(path, "array has too many items")
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(value):
                if any(item == earlier for earlier in value[:index]):
                    raise _SchemaError(f"{path}[{index}]", "array items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_schema(item_schema, item, f"{path}[{index}]")
    if isinstance(value, Mapping):
        properties = schema.get("properties")
        property_map = properties if isinstance(properties, Mapping) else {}
        required = schema.get("required")
        for name in required if isinstance(required, list) else []:
            if name not in value:
                raise _SchemaError(path, f"missing required property {name!r}")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(property_map)
            if extras:
                raise _SchemaError(path, f"unsupported property {sorted(map(str, extras))[0]!r}")
        for name, item in value.items():
            child_schema = property_map.get(name)
            if isinstance(child_schema, Mapping):
                _validate_schema(child_schema, item, f"{path}.{name}")


def _safe_details(value: object) -> dict[str, object]:
    """Keep only bounded, named public detail scalars on the tool wire.

    A V12 service exception is ordinarily sanitized, but the MCP facade is a
    separate trust boundary.  Do not render arbitrary exception detail JSON:
    it may later gain host paths, SQLite diagnostics, or caller data.
    """
    if not isinstance(value, Mapping):
        return {}
    details: dict[str, object] = {}
    path = value.get("path")
    if isinstance(path, str) and _SAFE_PATH_RE.fullmatch(path):
        details["path"] = path
    field = value.get("field")
    if isinstance(field, str) and _SAFE_FIELD_RE.fullmatch(field):
        details["field"] = field
    expected = value.get("expected")
    if isinstance(expected, str) and expected in _SAFE_EXPECTED_VALUES:
        details["expected"] = expected
    retry_after_ms = value.get("retry_after_ms")
    if isinstance(retry_after_ms, int) and not isinstance(retry_after_ms, bool) and 0 <= retry_after_ms <= 60_000:
        details["retry_after_ms"] = retry_after_ms
    return details


def _safe_message(code: object) -> str:
    """Return a fixed public explanation without rendering exception text."""
    if isinstance(code, str):
        return _PUBLIC_ERROR_MESSAGES.get(code, "The tool request could not be completed.")
    return "The tool request could not be completed."


def _sqlite_is_busy(error: BaseException) -> bool:
    """Recognize only SQLite's primary BUSY/LOCKED codes, not error text."""
    sqlite_code = getattr(error, "sqlite_errorcode", None)
    try:
        primary_code = int(sqlite_code) & 0xFF
    except (TypeError, ValueError):
        return False
    return primary_code in {
        getattr(sqlite3, "SQLITE_BUSY", -1),
        getattr(sqlite3, "SQLITE_LOCKED", -1),
    }


def _recovery(code: str, details: object) -> tuple[bool, str]:
    if code == "storage_busy":
        return True, "Retry this same mutation once with the same idempotency_key after the stated delay."
    if code == "storage_unavailable":
        return True, "Retry the same idempotent mutation after local storage is available; do not change its idempotency_key."
    action = _RECOVERY_ACTIONS.get(code)
    if action is not None:
        return False, action
    if isinstance(details, Mapping) and details.get("field"):
        return False, "Correct the named public field and call the same tool again."
    return False, "Review the advertised tool schema and use only durable IDs and values emitted by Cortex."


def _failure_text(*, code: str, details: object, mutation: str, retryable: bool, action: str) -> str:
    """Render one bounded TextContent failure without echoing private data."""
    parts = [f"Cortex tool error [{code}]: {_safe_message(code)}"]
    if isinstance(details, Mapping):
        path = details.get("path")
        field = details.get("field")
        expected = details.get("expected")
        if isinstance(path, str):
            parts.append(f"Location: {path}.")
        elif isinstance(field, str):
            parts.append(f"Field: {field}.")
        if isinstance(expected, str):
            parts.append(f"Expected: {expected[:256]}.")
        retry_after_ms = details.get("retry_after_ms")
        if isinstance(retry_after_ms, int) and not isinstance(retry_after_ms, bool):
            parts.append(f"Retry after: {retry_after_ms} ms.")
    parts.append(f"Mutation: {mutation}.")
    parts.append(f"Action: {action}")
    if code in {"validation_error", "invalid_identifier", "task_not_found", "delegation_not_found", "report_not_found", "initiative_not_found", "decision_not_found"}:
        parts.append("Handle rule: do not retry a shortened, ellipsized, inferred, or reconstructed value; reuse the exact structuredContent.handles value from the last success.")
    parts.append("Retryable now: yes." if retryable else "Retryable unchanged: no; correct the request first.")
    return " ".join(parts)[:2_048]


def _service_failure(error: V12ServiceError) -> dict[str, Any]:
    """Extract the service's bounded public code, message, details, and action."""
    candidate = getattr(error, "code", "ledger_error")
    code = candidate if isinstance(candidate, str) and candidate in _PUBLIC_SERVICE_CODES else "ledger_error"
    details = _safe_details(getattr(error, "details", None))
    retryable, action = _recovery(code, details)
    return {
        "code": code,
        "message": _safe_message(code),
        "details": details,
        "retryable": retryable,
        "action": action,
    }


def _validation_failure(error: _SchemaError, *, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    # Keep a closed-schema failure actionable without echoing arbitrary parser
    # messages or caller values.  The schema validator itself records a safe
    # JSON path; root-level required/additional-property failures need the
    # bounded field name recovered from their fixed validator wording.
    field: str | None = None
    direct = re.fullmatch(r"\$\.([a-z][a-z0-9_]{0,63})", error.path)
    if direct is not None:
        field = direct.group(1)
    else:
        named = re.fullmatch(
            r"(?:missing required|unsupported) property '([a-z][a-z0-9_]{0,63})'",
            error.message,
        )
        if named is not None:
            field = named.group(1)
    details = _safe_details({"path": error.path, "field": field, "expected": "advertised_input_schema"})
    retryable, action = _recovery("validation_error", details)
    if tool_name == "create_delegation" and "delegation_id" in arguments:
        action = (
            "create_delegation is creation-only: never pass delegation_id to it. "
            "For retrieval, call read_delegation({delegation_ref, after_sequence}) exactly "
            "with the emitted delegation_ref and durable sequence. For an exact mutation retry, "
            "reuse the original complete create_delegation payload with its returned retry_handle."
        )
    elif tool_name == "set_governance_mode" and "governance_gate" in arguments:
        action = (
            "governance_gate is an output-only durable relation. Omit it from "
            "set_governance_mode and use only its advertised input fields; a "
            "successful receipt returns the gate for later evidence handling."
        )
    elif tool_name == "record_user_decision" and arguments.get("decision_type") == "approve":
        required = (
            "approval_handle",
            "approval_view_content_digest",
            "approval_view_source_sequence",
        )
        missing = [name for name in required if name not in arguments]
        if missing:
            action = (
                "For decision_type=approve, copy "
                + ", ".join(missing)
                + " byte-for-byte from one ready approval_view, then correct the request."
            )
    elif tool_name in {"inspect_task", "read_delegation", "inspect_governance"} and "limit" in arguments:
        action = (
            f"Use an integer limit from 1 through {MAX_PAGE_LIMIT}; limit={MAX_PAGE_LIMIT} is the maximum. "
            "For additional chronology, copy the returned next_sequence unchanged into after_sequence."
        )
    return {
        "code": "validation_error",
        "message": _safe_message("validation_error"),
        "details": details,
        "retryable": retryable,
        "action": action,
    }


def _public_view(value: object, *, approval: bool, owner: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Project a nested public view to its compact, callable surface.

    Services retain canonical IDs so they can validate durable relationships.
    Views are public next-call material, however, and must never make a caller
    transcribe those IDs. Whitelisting also keeps future nested service fields
    from accidentally becoming public handles.
    """
    if not isinstance(value, Mapping):
        return None
    if not approval:
        result = {
            field: value[field]
            for field in ("status", "path", "source_sequence", "content_digest")
            if field in value
        }
        if value.get("status") == "ready" and isinstance(value.get("path"), str) and value.get("path") and isinstance(value.get("markdown_link"), str) and value.get("markdown_link"):
            result["markdown_link"] = value["markdown_link"]
        return result
    result = {
        field: value[field]
        for field in (
            "report_content_digest",
            "status",
            "path",
            "source_sequence",
            "content_digest",
            "approval_handle",
        )
        if field in value
    }
    if value.get("status") == "ready" and isinstance(value.get("path"), str) and value.get("path") and isinstance(value.get("markdown_link"), str) and value.get("markdown_link"):
        result["markdown_link"] = value["markdown_link"]
    fallback: Mapping[str, Any] = {}
    if owner is not None and isinstance(owner.get("reports"), list):
        for report in owner["reports"]:
            if isinstance(report, Mapping) and report.get("report_type") == "plan":
                fallback = report
                break
    for canonical_name, compact_name in (("report_id", "report_ref"), ("delegation_id", "delegation_ref")):
        compact = record_ref(value.get(canonical_name)) or record_ref(fallback.get(canonical_name))
        if compact is None:
            emitted = value.get(compact_name)
            if record_ref_parts(emitted, label=canonical_name) is not None:
                compact = emitted
        if compact is not None:
            result[compact_name] = compact
    return result


def _project_public_views(value: Mapping[str, Any]) -> dict[str, Any]:
    """Replace every public nested handle/view with its compact projection."""
    result = dict(value)
    for field, approval in (("human_view", False), ("approval_view", True)):
        projected = _public_view(result.get(field), approval=approval, owner=result)
        if projected is not None:
            result[field] = projected
    return result


def _handles(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic, non-recursive next-call handle envelope.

    Only fixed public result locations are considered.  In particular, report
    bodies, worker prose, timeline payloads, and arbitrary JSON never become
    handles.  This prevents one nested or displayed value from being mistaken
    for an authoritative durable identifier.
    """
    result: dict[str, Any] = {}
    for field in ("idempotency_key", "retry_handle"):
        candidate = value.get(field)
        if isinstance(candidate, str) and candidate:
            result[field] = candidate

    def entity_id(candidate: object, name: str) -> str | None:
        value = candidate.get(name) if isinstance(candidate, Mapping) else None
        return value if isinstance(value, str) and value else None

    # Canonical IDs are durable evidence, never public next-call handles.  The
    # compact typed refs below are the only callable entity locators emitted
    # from this function.
    task_id = next((entity_id(value.get(name), "task_id") for name in ("task", "delegation", "report", "initiative", "closure", "decision", "assessment") if entity_id(value.get(name), "task_id") is not None), None)
    delegation_id = next((entity_id(value.get(name), "delegation_id") for name in ("delegation", "report") if entity_id(value.get(name), "delegation_id") is not None), None)
    report_id = entity_id(value.get("report"), "report_id")
    initiative_id = next((entity_id(value.get(name), "initiative_id") for name in ("initiative", "assessment") if entity_id(value.get(name), "initiative_id") is not None), None)
    decision_id = entity_id(value.get("decision"), "decision_id")
    brief = value.get("worker_brief")
    task_id = task_id or entity_id(brief, "task_id")
    delegation_id = delegation_id or entity_id(brief, "delegation_id")
    action = value.get("next_action")
    action_task_ref = action.get("task_ref") if isinstance(action, Mapping) else None
    if not isinstance(action_task_ref, str) and isinstance(action, Mapping):
        arguments = action.get("arguments")
        action_task_ref = arguments.get("task_ref") if isinstance(arguments, Mapping) else None
    reports = value.get("reports")
    if isinstance(reports, list):
        report_ids = [item["report_id"] for item in reports if isinstance(item, Mapping) and isinstance(item.get("report_id"), str) and item["report_id"]]
        if report_ids:
            # Read order remains observable in the structured body; only the
            # compact refs belong in a public callable handle envelope.
            if len(report_ids) == 1:
                report_id = report_id or report_ids[0]
            result["report_refs"] = [record_ref(item) for item in report_ids]
    cursor = value.get("next_cursor")
    if isinstance(cursor, str) and cursor:
        result["cursor"] = cursor
    sequence = value.get("next_sequence")
    if isinstance(sequence, int) and not isinstance(sequence, bool) and sequence >= 0:
        result["next_sequence"] = sequence
    for field, approval in (("human_view", False), ("approval_view", True)):
        projected = _public_view(value.get(field), approval=approval, owner=value)
        if projected is not None:
            result[field] = projected
    compact = task_ref(task_id)
    if compact is None and task_ref_parts(action_task_ref) is not None:
        compact = action_task_ref
    if compact is not None:
        result["task_ref"] = compact
    for canonical, compact_name in ((delegation_id, "delegation_ref"), (report_id, "report_ref"), (decision_id, "decision_ref"), (initiative_id, "initiative_ref")):
        compact_entity = record_ref(canonical)
        if compact_entity is not None:
            result[compact_name] = compact_entity
    return result


def _success_tool_result(value: Mapping[str, Any]) -> dict[str, Any]:
    structured = _project_public_views(value)
    compact_handles = json.dumps({"handles": structured["handles"]}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "content": [{"type": "text", "text": compact_handles + "\nCopy only structuredContent.handles compact typed refs and server-issued opaque tokens byte-for-byte; canonical durable IDs in any rendered evidence are non-callable. Task-anchored calls use task_ref. Entity-derived calls use delegation_ref, report_ref/report_refs, decision_ref, or initiative_ref as advertised. If structuredContent.next_action is present, it is the exact required next call and its task_ref must be copied byte-for-byte. Delegation recovery: create_delegation is creation-only; retrieve an existing delegation with read_delegation({delegation_ref, after_sequence}) exactly. Read all result data from structuredContent; it is not duplicated into TextContent so bounded 200-event pages remain one valid JSONL frame."}],
        "structuredContent": structured,
        "isError": False,
    }


def _tool_error_result(failure: Mapping[str, Any], *, mutation: str) -> dict[str, Any]:
    """Correctable tool errors intentionally have TextContent only.

    The text carries the stable Cortex code, safe reason, affected public
    field/path when available, and one next action.  Omitting
    ``structuredContent`` avoids presenting a second success-output shape to
    clients that validate it against the advertised output schema.
    """
    return {
        "content": [{
            "type": "text",
            "text": _failure_text(
                code=str(failure["code"]),
                details=failure.get("details"),
                mutation=mutation,
                retryable=bool(failure.get("retryable")),
                action=str(failure.get("action") or "Review the advertised input contract."),
            ),
        }],
        "isError": True,
    }


def serve_stdio(
    *,
    public_tools: Mapping[str, Mapping[str, Any]],
    server_version: str,
    instructions: str,
) -> None:
    """Serve a fixed V12 MCP tool catalogue over standard input/output."""
    if len(public_tools) != _MAX_TOOLS:
        raise RuntimeError("Cortex v12 requires exactly eleven public tools")
    for name, contract in public_tools.items():
        if not isinstance(name, str) or not isinstance(contract, Mapping):
            raise RuntimeError("Cortex v12 public tool registry is invalid")
        if not isinstance(contract.get("description"), str):
            raise RuntimeError("Cortex v12 public tool description is invalid")
        if (
            not isinstance(contract.get("inputSchema"), Mapping)
            or not isinstance(contract.get("outputSchema"), Mapping)
            or not callable(contract.get("handler"))
        ):
            raise RuntimeError("Cortex v12 public tool binding is invalid")

    def render(value: Mapping[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    def write(value: Mapping[str, Any]) -> None:
        sys.stdout.write(render(value) + "\n")
        sys.stdout.flush()

    def reply(request_id: object, result: Mapping[str, Any]) -> None:
        payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
        if len(render(payload).encode("utf-8")) > MAX_PHYSICAL_JSONL_FRAME_BYTES:
            # A valid structured result must not be split across JSONL frames.
            # The duplicate compact TextContent is intentionally bounded at
            # this final wire boundary rather than relying on any individual
            # service field's encoding characteristics.
            write({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": "Cortex server response is unavailable",
                    "data": {"cortex_code": "ledger_error"},
                },
            })
            return
        write(payload)

    def rpc_error(request_id: object, code: int, message: str, *, data: Mapping[str, Any] | None = None) -> None:
        error: dict[str, Any] = {"code": code, "message": message}
        if data:
            error["data"] = dict(data)
        write({"jsonrpc": "2.0", "id": request_id, "error": error})

    session_state = "new"
    while True:
        line, frame_rejected = _read_physical_jsonl_frame(sys.stdin)
        if frame_rejected:
            # Do not parse or echo any caller-controlled oversized frame.
            # It has already been drained, so the next JSONL request begins
            # at a known record boundary.
            rpc_error(None, -32700, "Parse error")
            continue
        if line is None:
            return
        request_id: object = None
        has_request_id = False
        try:
            try:
                request = json.loads(line, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
            except (json.JSONDecodeError, ValueError):
                raise _RpcError(-32700, "Parse error")
            if not isinstance(request, Mapping) or request.get("jsonrpc") != "2.0":
                raise _RpcError(-32600, "Invalid Request")
            has_request_id = "id" in request
            request_id = request.get("id")
            if has_request_id and not (
                isinstance(request_id, str)
                or (isinstance(request_id, int) and not isinstance(request_id, bool))
                or request_id is None
            ):
                raise _RpcError(-32600, "Invalid Request")
            if isinstance(request_id, str) and len(request_id.encode("utf-8")) > _MAX_REQUEST_ID_BYTES:
                raise _RpcError(-32600, "Invalid Request")
            method = request.get("method")
            if not isinstance(method, str):
                raise _RpcError(-32600, "Invalid Request")
            params = request.get("params", {})
            if not isinstance(params, Mapping):
                raise _RpcError(-32602, "Invalid params")

            if method == "initialize":
                if not has_request_id:
                    continue
                if session_state != "new":
                    raise _RpcError(-32600, "Invalid Request")
                if set(params) - {"protocolVersion", "capabilities", "clientInfo", "_meta"}:
                    raise _RpcError(-32602, "Invalid params")
                client_info = params.get("clientInfo")
                if (
                    params.get("protocolVersion") != MCP_PROTOCOL_VERSION
                    or not isinstance(params.get("capabilities"), Mapping)
                    or not isinstance(client_info, Mapping)
                    or not isinstance(client_info.get("name"), str)
                    or not isinstance(client_info.get("version"), str)
                    or not client_info["name"]
                    or not client_info["version"]
                ):
                    raise _RpcError(-32602, "Invalid params")
                reply(request_id, {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "cortex", "version": server_version},
                    "instructions": instructions,
                })
                session_state = "initialize_response_sent"
                continue
            if method == "notifications/initialized":
                if has_request_id:
                    raise _RpcError(-32600, "Invalid Request")
                if set(params) - {"_meta"}:
                    continue
                if session_state == "initialize_response_sent":
                    session_state = "ready"
                continue
            if session_state != "ready":
                raise _RpcError(-32002, "Server not initialized")
            if method == "ping":
                if has_request_id:
                    reply(request_id, {})
                continue
            if method == "tools/list":
                if not has_request_id:
                    continue
                if set(params) - {"cursor", "_meta"}:
                    raise _RpcError(-32602, "Invalid params")
                cursor = params.get("cursor")
                if cursor not in {None, ""}:
                    raise _RpcError(-32602, "Invalid params")
                reply(request_id, {"tools": [
                    {
                        "name": name,
                        "description": str(contract["description"]),
                        "inputSchema": dict(contract["inputSchema"]),
                        "outputSchema": dict(contract["outputSchema"]),
                    }
                    for name, contract in public_tools.items()
                ]})
                continue
            if method != "tools/call":
                raise _RpcError(-32601, "Method not found")
            if not has_request_id:
                continue
            if set(params) - {"name", "arguments", "_meta"}:
                raise _RpcError(-32602, "Invalid params")
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or name not in public_tools or not isinstance(arguments, Mapping):
                raise _RpcError(-32602, "Invalid params")
            contract = public_tools[name]
            try:
                _validate_schema(contract["inputSchema"], arguments)
            except _SchemaError as error:
                reply(request_id, _tool_error_result(_validation_failure(error, tool_name=name, arguments=arguments), mutation=name))
                continue
            try:
                result = contract["handler"](**dict(arguments))
            except V12ServiceError as error:
                failure = _service_failure(error)
                if str(failure["code"]) in _SERVER_STATE_CODES:
                    rpc_error(request_id, -32603, "Cortex server state is unavailable", data={"cortex_code": str(failure["code"])})
                else:
                    reply(request_id, _tool_error_result(failure, mutation=name))
            except sqlite3.Error as error:
                if _sqlite_is_busy(error):
                    reply(request_id, _tool_error_result({
                        "code": "storage_busy",
                        "message": _safe_message("storage_busy"),
                        "details": {"retry_after_ms": 100},
                        "retryable": True,
                        "action": _recovery("storage_busy", {})[1],
                    }, mutation=name))
                else:
                    rpc_error(request_id, -32603, "Cortex server state is unavailable", data={"cortex_code": "ledger_error"})
            except (TypeError, ValueError):
                # Public input validation and typed V12ServiceError cover all
                # caller-correctable failures. A raw Python type/value error
                # after dispatch is an implementation fault, not a second
                # inconsistent tool-error protocol.
                rpc_error(request_id, -32603, "Cortex server state is unavailable", data={"cortex_code": "ledger_error"})
            except Exception:
                rpc_error(request_id, -32603, "Cortex server state is unavailable", data={"cortex_code": "ledger_error"})
            else:
                if not isinstance(result, Mapping) or not _is_json_value(result):
                    rpc_error(request_id, -32603, "Cortex server state is unavailable", data={"cortex_code": "ledger_error"})
                    continue
                result = _project_public_views(result)
                result["handles"] = _handles(result)
                try:
                    _validate_schema(contract["outputSchema"], result)
                except _SchemaError:
                    rpc_error(request_id, -32603, "Cortex server state is unavailable", data={"cortex_code": "ledger_error"})
                    continue
                reply(request_id, _success_tool_result(dict(result)))
        except _RpcError as error:
            if has_request_id:
                rpc_error(request_id, error.code, error.message)
            elif error.code == -32700:
                rpc_error(None, error.code, error.message)
