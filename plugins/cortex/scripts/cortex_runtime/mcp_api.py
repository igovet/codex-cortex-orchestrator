"""Small, policy-neutral MCP stdio transport for the Cortex v12 ledger.

This module intentionally knows nothing about coordinator/worker roles, native
children, host threads, governance state, or lifecycle progression.  It only
advertises one fixed catalogue, validates its schemas, and transports durable
service results as JSON-RPC MCP tool results.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cortex_runtime.provenance import verify_runtime
from cortex_runtime.event_journal import EventJournal
from cortex_runtime.raw_diagnostic import append as raw_diagnostic
from cortex_runtime.observation_generation import ObservationGenerationError, candidate_codex_home, claim_generation, write_ready_receipt
from cortex_runtime.v12_service import V12ServiceError
from cortex_runtime.v12_contract import MCP_OPERATION_MAX_BYTES, record_ref, record_ref_parts, task_ref, task_ref_parts
from cortex_runtime.semantic_registry import OPERATION_NAMES, error_contract, spec_for


# MCP core versions understood by this deliberately core-only server.  The
# newest version is selected for clients that offer it; the older entry keeps
# the established stdio contract available to clients that have not upgraded
# yet.  Optional 2025-11-25 extensions (Tasks, elicitation, sampling) remain
# unadvertised and are not accepted merely because the version is selected.
MCP_SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")
MCP_PROTOCOL_VERSION = MCP_SUPPORTED_PROTOCOL_VERSIONS[0]
_MAX_TOOLS = len(OPERATION_NAMES)
# MCP stdio is JSONL.  Enforce this at the byte transport boundary before a
# JSON parser can allocate for an unbounded physical line.  The terminating
# newline is part of the physical frame when present.
MAX_PHYSICAL_JSONL_FRAME_BYTES = 256 * 1024
_FRAME_READ_LIMIT = MAX_PHYSICAL_JSONL_FRAME_BYTES + 1
_MAX_REQUEST_ID_BYTES = 512
_TOOLS_LIST_CURSOR_PREFIX = "cortex-tools-list-v1:"
_SAFE_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_PATH_RE = re.compile(r"^\$(?:\.[A-Za-z_][A-Za-z0-9_]{0,63}|\[[0-9]{1,4}\]){0,16}$")
_SAFE_EXPECTED_VALUES = frozenset({
    "restart_without_cursor", "task_ref", "required_field", "no_extra_properties",
    "string", "integer", "object", "array", "permitted_value", "constant",
    "bounded_length", "bounded_range", "lowercase_section_label", "unique_items",
    "permitted_input_shape", "bounded_json_value", "r_[0-9a-f]{12}",
    "progress|result|synthesis|plan", "complete_outcome_object",
    "current_semantic_outcome", "unique_current_semantic_outcome",
    "non_overlapping_outcome_scope",
})
_SAFE_VALIDATION_REASONS = frozenset({
    "required", "additional_property", "type", "enum", "constant", "length",
    "range", "pattern", "unique_items", "conditional_shape", "encoded_size",
    "canonical_semantic_invalid", "evidence_missing", "evidence_invalid",
    "command_evidence_incomplete", "not_run_reason_missing", "evidence_state_invalid",
    "documentation_impact_incomplete",
    "contract_coverage_missing", "contract_coverage_invalid", "contract_coverage_extra",
    "contract_coverage_duplicate", "contract_coverage_incomplete",
    "semantic_outcome_missing", "semantic_outcome_ambiguous",
    "stale_current_outcome", "ownership_conflict", "scope_overlap",
    "invalid_decomposition", "unchanged_retry", "correction_exhausted",
})


def catalogue_identity(public_tools: Mapping[str, Mapping[str, Any]]) -> dict[str, object]:
    """Return safe, deterministic registration identity without exposing a list."""
    catalogue = tuple(
        {
            "name": name, "description": str(contract["description"]),
            "inputSchema": dict(contract["inputSchema"]),
            "outputSchema": dict(contract["outputSchema"]),
        }
        for name, contract in public_tools.items()
    )
    return {
        "catalogue_count": len(catalogue),
        "catalogue_digest": hashlib.sha256(json.dumps(
            catalogue, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")).hexdigest(),
    }


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _SchemaError(ValueError):
    def __init__(
        self, path: str, message: str, *, missing_fields: tuple[str, ...] = (),
        actual_bytes: int | None = None, max_bytes: int | None = None,
        correction_state: str | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.message = message
        self.missing_fields = missing_fields
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes
        self.correction_state = correction_state


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


def _tools_list_cursor(offset: int) -> str:
    """Return the opaque continuation cursor for one catalog position."""
    return f"{_TOOLS_LIST_CURSOR_PREFIX}{offset}"


def _tools_list_offset(cursor: object, total: int) -> int | None:
    """Resolve one tools/list cursor without accepting alternate spellings."""
    if cursor is None or cursor == "":
        return 0
    if not isinstance(cursor, str) or not cursor.startswith(_TOOLS_LIST_CURSOR_PREFIX):
        return None
    suffix = cursor.removeprefix(_TOOLS_LIST_CURSOR_PREFIX)
    if not suffix.isascii() or not suffix.isdecimal():
        return None
    offset = int(suffix)
    return offset if 0 < offset < total else None


def _validate_schema(schema: Mapping[str, Any], value: object, path: str = "$") -> None:
    """Validate the compact JSON-Schema subset used by the V12 public API."""
    const = schema.get("const")
    if "const" in schema and value != const:
        raise _SchemaError(path, "value does not match the required constant")
    # Validate the outer object shape before a discriminated union.  Without
    # this ordering, an omitted common field (for example ``mode`` or the
    # decision prose fields) can be reported as a misleading field from the
    # first non-matching variant, forcing callers into trial-and-error.
    if isinstance(value, Mapping):
        required = schema.get("required")
        missing = tuple(name for name in required if isinstance(name, str) and name not in value) if isinstance(required, list) else ()
        if missing:
            names = ", ".join(repr(name) for name in missing)
            wording = "property" if len(missing) == 1 else "properties"
            missing_path = f"{path}.{missing[0]}" if len(missing) == 1 else path
            raise _SchemaError(missing_path, f"missing required {wording} {names}", missing_fields=missing)
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
                # A union commonly has a discriminator branch and one or more
                # shape branches.  Returning the first failure made a useful
                # missing-field error lose to a root-level ``not`` guard.
                # Prefer the deepest safe JSON location, then a concrete
                # missing/unsupported-field diagnostic, without exposing a
                # caller value.
                def failure_rank(error: _SchemaError) -> tuple[int, int]:
                    return (
                        error.path.count(".") + error.path.count("["),
                        int(error.message.startswith(("missing required", "unsupported"))),
                    )
                raise max(failures, key=failure_rank)
            raise _SchemaError(path, "value does not match a permitted input shape")
    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        matches = 0
        failures: list[_SchemaError] = []
        alternatives = [item for item in one_of if isinstance(item, Mapping)]
        # Prefer a branch whose explicit discriminator already matches the
        # supplied object. This keeps a missing operation field from being
        # reported as an unrelated report shape.
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
        actual_bytes = _encoded_json_bytes(value, path)
        if actual_bytes > maximum_bytes:
            raise _SchemaError(
                path, "JSON value exceeds the maximum encoded byte length",
                actual_bytes=actual_bytes, max_bytes=maximum_bytes,
            )
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
        missing = tuple(name for name in required if isinstance(name, str) and name not in value) if isinstance(required, list) else ()
        if missing:
            names = ", ".join(repr(name) for name in missing)
            wording = "property" if len(missing) == 1 else "properties"
            missing_path = f"{path}.{missing[0]}" if len(missing) == 1 else path
            raise _SchemaError(missing_path, f"missing required {wording} {names}", missing_fields=missing)
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(property_map)
            if extras:
                raise _SchemaError(path, f"unsupported property {sorted(map(str, extras))[0]!r}")
        for name, item in value.items():
            child_schema = property_map.get(name)
            if isinstance(child_schema, Mapping):
                _validate_schema(child_schema, item, f"{path}.{name}")


def _validate_public_call_shape(tool_name: str, arguments: Mapping[str, Any]) -> None:
    """Enforce mode/subject relations without obscuring advertised tool types.

    JSON-Schema intersections and partial ``oneOf`` branches collapse to
    ``unknown`` in the Codex TypeScript tool declaration.  Keep the advertised
    object flat and fully typed, then enforce its documented conditional rules
    here before any storage lookup or mutation.
    """

    def require(*names: str) -> None:
        for name in names:
            if name not in arguments:
                raise _SchemaError(f"$.{name}", f"missing required property '{name}'")

    def forbid(*names: str) -> None:
        for name in names:
            if name in arguments:
                raise _SchemaError(f"$.{name}", f"unsupported property '{name}'")

    if tool_name == "submit_report":
        mode = arguments.get("mode")
        plan_fields = ("review_policy", "supersedes_report_ref")
        if mode == "begin":
            require("report_type")
            forbid("report_ref", "status", "content", "section", "abort_reason_en")
            if arguments.get("report_type") != "plan":
                forbid(*plan_fields)
        elif mode == "append":
            require("report_ref", "section", "content")
            forbid("report_type", "status", "abort_reason_en", *plan_fields)
        elif mode == "finalize":
            require("report_ref", "status")
            forbid("report_type", "content", "section", "abort_reason_en", *plan_fields)
        elif mode == "abort":
            require("report_ref", "abort_reason_en")
            forbid("report_type", "status", "content", "section", *plan_fields)
    elif tool_name == "submit_governance_closure":
        # The flat public schema keeps the first-call declaration concrete;
        # task closures nevertheless cannot carry initiative-only state.
        # Enforce that relation at the MCP boundary before service lookup.
        if arguments.get("subject_type") == "task":
            forbid("initiative_status")
    elif tool_name == "record_user_decision":
        subject_type = arguments.get("subject_type")
        decision_type = arguments.get("decision_type")
        approval_fields = ("approval_handle", "approval_view_content_digest", "approval_view_source_sequence")
        if subject_type in {"task", "delegation", "initiative"}:
            forbid("subject_digest", *approval_fields)
        elif subject_type == "report":
            require("subject_digest")
            forbid(*approval_fields)
        elif subject_type == "plan":
            require("subject_digest")
            if decision_type == "approve":
                require(*approval_fields)
            else:
                forbid(*approval_fields)
        if decision_type == "steer":
            if subject_type != "task" or arguments.get("subject_ref") != arguments.get("task_ref"):
                raise _SchemaError("$.subject_ref", "value does not match the required constant")
            require("steering_delta")
            delta = arguments.get("steering_delta")
            if not isinstance(delta, Mapping) or not any(isinstance(delta.get(name), list) and delta.get(name) for name in ("retire_item_refs", "add")):
                raise _SchemaError("$.steering_delta", "value does not match a permitted input shape")
        else:
            forbid("steering_delta")


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
    missing_fields = value.get("missing_fields")
    if isinstance(missing_fields, (list, tuple)):
        safe_missing = tuple(
            name for name in missing_fields
            if isinstance(name, str) and _SAFE_FIELD_RE.fullmatch(name)
        )
        if safe_missing and len(safe_missing) == len(missing_fields):
            details["missing_fields"] = list(safe_missing[:32])
    expected = value.get("expected")
    if isinstance(expected, str) and expected in _SAFE_EXPECTED_VALUES:
        details["expected"] = expected
    reason = value.get("reason")
    if isinstance(reason, str) and reason in _SAFE_VALIDATION_REASONS:
        details["reason"] = reason
    retry_after_ms = value.get("retry_after_ms")
    if isinstance(retry_after_ms, int) and not isinstance(retry_after_ms, bool) and 0 <= retry_after_ms <= 60_000:
        details["retry_after_ms"] = retry_after_ms
    for name in ("actual_bytes", "max_bytes"):
        number = value.get(name)
        if isinstance(number, int) and not isinstance(number, bool) and 0 <= number <= MAX_PHYSICAL_JSONL_FRAME_BYTES:
            details[name] = number
    sections = value.get("sections")
    if isinstance(sections, list):
        safe_sections: list[dict[str, object]] = []
        for item in sections[:16]:
            if not isinstance(item, Mapping):
                continue
            section = item.get("section")
            encoded_bytes = item.get("encoded_bytes")
            if (
                isinstance(section, str)
                and _SAFE_FIELD_RE.fullmatch(section)
                and isinstance(encoded_bytes, int)
                and not isinstance(encoded_bytes, bool)
                and 0 <= encoded_bytes <= MAX_PHYSICAL_JSONL_FRAME_BYTES
            ):
                safe_sections.append({"section": section, "encoded_bytes": encoded_bytes})
        if safe_sections:
            details["sections"] = safe_sections
    return details


def _safe_message(code: object) -> str:
    """Return a fixed public explanation without rendering exception text."""
    return error_contract(code).message


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
    contract = error_contract(code)
    return contract.retryable, contract.action


def _failure_text(*, code: str, details: object, mutation: str, retryable: bool, action: str) -> str:
    """Render one bounded TextContent failure without echoing private data."""
    parts = [f"Cortex tool error [{code}]: {_safe_message(code)}"]
    if isinstance(details, Mapping):
        path = details.get("path")
        field = details.get("field")
        expected = details.get("expected")
        reason = details.get("reason")
        if isinstance(path, str) and not (path == "$" and isinstance(field, str)):
            parts.append(f"Location: {path}.")
        if isinstance(field, str):
            parts.append(f"Field: {field}.")
        missing_fields = details.get("missing_fields")
        if isinstance(missing_fields, list) and missing_fields and all(isinstance(item, str) for item in missing_fields):
            parts.append(f"Missing fields: {', '.join(missing_fields[:32])}.")
        if isinstance(expected, str):
            parts.append(f"Expected: {expected[:256]}.")
        if isinstance(reason, str):
            parts.append(f"Reason: {reason}.")
        retry_after_ms = details.get("retry_after_ms")
        if isinstance(retry_after_ms, int) and not isinstance(retry_after_ms, bool):
            parts.append(f"Retry after: {retry_after_ms} ms.")
        actual_bytes = details.get("actual_bytes")
        max_bytes = details.get("max_bytes")
        if (
            isinstance(actual_bytes, int) and not isinstance(actual_bytes, bool)
            and isinstance(max_bytes, int) and not isinstance(max_bytes, bool)
        ):
            parts.append(f"Encoded bytes: actual={actual_bytes}, maximum={max_bytes}.")
        sections = details.get("sections")
        if isinstance(sections, list) and sections:
            rendered_sections = ", ".join(
                f"{item['section']}={item['encoded_bytes']}"
                for item in sections
                if isinstance(item, Mapping)
                and isinstance(item.get("section"), str)
                and isinstance(item.get("encoded_bytes"), int)
            )
            if rendered_sections:
                parts.append(f"Largest known sections: {rendered_sections} bytes.")
    parts.append(f"Mutation: {mutation}.")
    parts.append(f"Action: {action}")
    if code in {"invalid_identifier", "task_not_found", "delegation_not_found", "report_not_found", "initiative_not_found", "decision_not_found"}:
        parts.append("Handle rule: do not retry a shortened, ellipsized, inferred, or reconstructed value; reuse the exact structuredContent.handles value from the last success.")
    parts.append("Retryable now: yes." if retryable else "Retryable unchanged: no; correct the request first.")
    return " ".join(parts)[:2_048]


def _service_failure(error: V12ServiceError) -> dict[str, Any]:
    """Extract the service's bounded public code, message, details, and action."""
    candidate = getattr(error, "code", "ledger_error")
    contract = error_contract(candidate)
    code = contract.code
    details = _safe_details(getattr(error, "details", None))
    retryable, action = _recovery(code, details)
    return {
        "code": code,
        "message": _safe_message(code),
        "details": details,
        "retryable": retryable,
        "action": action,
    }


def _known_section_sizes(
    arguments: Mapping[str, Any], input_schema: Mapping[str, Any],
) -> list[dict[str, object]]:
    """Return bounded sizes for advertised top-level sections only."""
    properties = input_schema.get("properties")
    if not isinstance(properties, Mapping):
        return []
    sizes: list[dict[str, object]] = []
    for name in properties:
        if (
            isinstance(name, str)
            and _SAFE_FIELD_RE.fullmatch(name)
            and name in arguments
        ):
            sizes.append({
                "section": name,
                "encoded_bytes": _encoded_json_bytes({name: arguments[name]}, "$"),
            })
    sizes.sort(key=lambda item: (-int(item["encoded_bytes"]), str(item["section"])))
    return sizes[:8]


def _validation_failure(
    error: _SchemaError, *, tool_name: str, arguments: Mapping[str, Any],
    input_schema: Mapping[str, Any],
) -> dict[str, Any]:
    # Keep a closed-schema failure actionable without echoing arbitrary parser
    # messages or caller values.  The schema validator itself records a safe
    # JSON path; root-level required/additional-property failures need the
    # bounded field name recovered from their fixed validator wording.
    field: str | None = None
    # For object-level additional-property failures the safe path identifies
    # the containing object, while the fixed validator message identifies the
    # actual unsupported child. Prefer that child so correction never removes
    # the valid enclosing publication object.
    named = re.fullmatch(
        r"missing required property '([a-z][a-z0-9_]{0,63})'|"
        r"missing required properties ((?:'[a-z][a-z0-9_]{0,63}'(?:, )?)+)|"
        r"unsupported property '([a-z][a-z0-9_]{0,63})'|"
        r"property '([a-z][a-z0-9_]{0,63})' is not permitted for this input shape",
        error.message,
    )
    if named is not None:
        field = named.group(1) or named.group(3) or named.group(4)
        if field is None and named.group(2):
            field = re.search(r"'([a-z][a-z0-9_]{0,63})'", named.group(2)).group(1)
    if field is None:
        direct = re.fullmatch(r"\$\.([a-z][a-z0-9_]{0,63})", error.path)
        if direct is not None:
            field = direct.group(1)
        else:
            nested = re.search(r"\.([a-z][a-z0-9_]{0,63})(?:\[[0-9]{1,4}\])?$", error.path)
            if nested is not None:
                field = nested.group(1)
    message = error.message
    reason, expected = ("conditional_shape", "permitted_input_shape")
    for prefix, mapped_reason, mapped_expected in (
        ("missing required", "required", "required_field"),
        ("unsupported", "additional_property", "no_extra_properties"),
        ("value has the wrong type", "type", "string"),
        ("value is not one", "enum", "progress|result|synthesis|plan" if field == "report_type" else "permitted_value"),
        ("value does not match the required constant", "constant", "constant"),
        ("string is ", "length", "bounded_length"),
        ("number is ", "range", "bounded_range"),
        ("string does not match", "pattern", "lowercase_section_label" if field == "section" else "r_[0-9a-f]{12}" if field == "report_refs" else "permitted_value"),
        ("array has ", "length", "bounded_length"),
        ("array items must", "unique_items", "unique_items"),
        ("JSON value exceeds", "encoded_size", "bounded_json_value"),
    ):
        if message.startswith(prefix):
            reason, expected = mapped_reason, mapped_expected
            break
    if error.correction_state == "unchanged":
        reason = "unchanged_retry"
    elif error.correction_state == "exhausted":
        reason = "correction_exhausted"
    sections = (
        _known_section_sizes(arguments, input_schema)
        if reason in {"encoded_size", "unchanged_retry", "correction_exhausted"}
        and error.path == "$"
        else []
    )
    details = _safe_details({
        "path": error.path,
        "field": field,
        "missing_fields": error.missing_fields,
        "reason": reason,
        "expected": expected,
        "actual_bytes": error.actual_bytes,
        "max_bytes": error.max_bytes,
        "sections": sections,
    })
    retryable, action = _recovery("validation_error", details)
    if error.missing_fields:
        action = (
            "Add every missing required property to one complete request: "
            + ", ".join(error.missing_fields)
            + ". Then call the same tool once with the corrected complete payload."
        )
    if error.path == "$" and reason == "encoded_size":
        action = (
            "Compact redundant prose and formatting across the identified advertised sections, "
            "preserve every required semantic section and evidence fact, preflight the complete "
            "compact UTF-8 JSON object against the reported maximum, then make exactly one "
            "materially smaller complete corrected call. Do not ellipsize, byte-slice, omit, "
            "infer, or reconstruct content."
        )
    elif reason == "unchanged_retry":
        action = (
            "Stop this correction path: the unchanged oversize request is not a material "
            "correction and must not be called again."
        )
    elif reason == "correction_exhausted":
        action = (
            "Stop this correction path: the single permitted aggregate-size correction has "
            "already failed and no further retry is allowed on this worker connection."
        )
    if tool_name == "create_delegation" and "delegation_id" in arguments:
        action = (
            "create_delegation is creation-only: never pass delegation_id to it. "
            "For retrieval, call read_delegation({delegation_ref, after_sequence}) exactly "
            "with the emitted delegation_ref and durable sequence. For an exact mutation retry, "
            "reuse the original complete create_delegation payload with its returned idempotency_key."
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
            for field in ("status", "source_sequence", "content_digest")
            if field in value
        }
        if value.get("status") == "ready" and isinstance(value.get("markdown_link"), str) and value.get("markdown_link"):
            result["markdown_link"] = value["markdown_link"]
        return result
    result = {
        field: value[field]
        for field in (
            "report_content_digest",
            "status",
            "source_sequence",
            "content_digest",
            "approval_handle",
        )
        if field in value
    }
    if value.get("status") == "ready" and isinstance(value.get("markdown_link"), str) and value.get("markdown_link"):
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
    """Replace nested views and remove legacy caller-owned receipt material.

    The semantic MCP boundary owns command identity on the server.  The legacy
    storage facade may still return its private receipt fields, but exposing
    them in a semantic success teaches callers to invent properties that no
    semantic input schema accepts.  Strip those implementation-only values at
    the single transport composition boundary for every public operation.
    """
    result = {
        key: item for key, item in value.items()
        if key not in {"idempotency_key", "retry_handle", "dispatch_correlation_marker"}
    }
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
    assignment_ref = value.get("assignment_ref")
    if isinstance(assignment_ref, str) and re.fullmatch(r"^d_[0-9a-f]{12}$", assignment_ref):
        result["assignment_ref"] = assignment_ref
    for field, pattern in (("bootstrap_capability", r"^wb_[0-9a-f]{32}$"), ("continuation_ref", r"^wc_[0-9a-f]{32}$")):
        token = value.get(field)
        if isinstance(token, str) and re.fullmatch(pattern, token):
            result[field] = token
    binding_ref = value.get("binding_ref")
    if isinstance(binding_ref, str) and re.fullmatch(r"^cb_[0-9a-f]{32}$", binding_ref):
        result["binding_ref"] = binding_ref
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
    emitted_decision_ref = value.get("decision_ref")
    if not isinstance(emitted_decision_ref, str) or record_ref_parts(emitted_decision_ref, label="decision_ref") is None:
        emitted_decision_ref = None
    decision = value.get("decision")
    if isinstance(decision, Mapping):
        subject_type = decision.get("subject_type")
        subject_id = decision.get("subject_id")
        # A decision can immediately follow a plan/report clarification.  Keep
        # that exact selected report in the callable envelope so a subsequent
        # read cannot be redirected by stale coordinator state from another
        # task.  The durable subject ID itself remains non-callable evidence.
        if subject_type in {"plan", "report"} and isinstance(subject_id, str):
            report_id = report_id or subject_id
        elif subject_type == "delegation" and isinstance(subject_id, str):
            delegation_id = delegation_id or subject_id
        elif subject_type == "initiative" and isinstance(subject_id, str):
            initiative_id = initiative_id or subject_id
    # Public compact publication/family projections have intentionally
    # removed canonical IDs. Treat their bounded emitted refs as authoritative
    # handles rather than trying to reconstruct an internal record ID.
    def emitted_ref(candidate: object, field: str, label: str) -> str | None:
        item = candidate.get(field) if isinstance(candidate, Mapping) else None
        return item if isinstance(item, str) and record_ref_parts(item, label=label) is not None else None
    emitted_report_ref = emitted_ref(value.get("report"), "report_ref", "report_ref")
    emitted_delegation_ref = (
        emitted_ref(value.get("approval_view"), "delegation_ref", "delegation_ref")
        or emitted_ref(value.get("report"), "delegation_ref", "delegation_ref")
    )
    brief = value.get("worker_brief")
    task_id = task_id or entity_id(brief, "task_id")
    delegation_id = delegation_id or entity_id(brief, "delegation_id")
    reports = value.get("reports")
    if isinstance(reports, list):
        if task_id is None:
            task_id = next((entity_id(item, "task_id") for item in reports if entity_id(item, "task_id") is not None), None)
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
        result["after_sequence"] = sequence
    for field, approval in (("human_view", False), ("approval_view", True)):
        projected = _public_view(value.get(field), approval=approval, owner=value)
        if projected is not None:
            result[field] = projected
    compact = task_ref(task_id)
    if compact is None and task_ref_parts(value.get("task_ref")) is not None:
        compact = value["task_ref"]
    if compact is not None:
        result["task_ref"] = compact
    for canonical, compact_name in ((delegation_id, "delegation_ref"), (report_id, "report_ref"), (decision_id, "decision_ref"), (initiative_id, "initiative_ref")):
        compact_entity = record_ref(canonical)
        if compact_entity is not None:
            result[compact_name] = compact_entity
    if emitted_decision_ref is not None:
        result["decision_ref"] = emitted_decision_ref
    if emitted_report_ref is not None:
        result["report_ref"] = emitted_report_ref
    if emitted_delegation_ref is not None:
        result["delegation_ref"] = emitted_delegation_ref
    return result


def _handles_for_output_schema(
    value: Mapping[str, Any], output_schema: Mapping[str, Any]
) -> dict[str, Any]:
    """Project callable handles through the operation's advertised contract.

    ``_handles`` discovers authoritative values at fixed result locations, but
    not every discovered relation is a useful next call for every operation.
    A closed operation-specific handles schema is the authority for that
    distinction.  Intersecting here prevents a generic result walker from
    leaking an unrelated sibling handle or making a valid server result fail
    its own advertised output contract.
    """
    discovered = _handles(value)
    properties = output_schema.get("properties")
    handles_schema = properties.get("handles") if isinstance(properties, Mapping) else None
    if not isinstance(handles_schema, Mapping) or handles_schema.get("additionalProperties") is not False:
        return discovered
    advertised = handles_schema.get("properties")
    if not isinstance(advertised, Mapping):
        return {}
    return {name: item for name, item in discovered.items() if name in advertised}


def _success_tool_result(value: Mapping[str, Any]) -> dict[str, Any]:
    structured = _project_public_views(value)
    serialized = json.dumps(structured, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    leading_view = None
    for view_name in ("approval_view", "human_view"):
        view = structured.get(view_name)
        if isinstance(view, Mapping) and view.get("status") == "ready" and isinstance(view.get("markdown_link"), str):
            leading_view = view["markdown_link"]
            break
    content = []
    if leading_view is not None:
        content.append({"type": "text", "text": leading_view})
    # Duplicating a large structured result into TextContent can make an
    # otherwise bounded result exceed the physical JSONL frame. Preserve the
    # compatibility duplicate for ordinary responses, but use one fixed
    # non-authoritative notice once the structured body itself is large. The
    # complete authoritative value remains in ``structuredContent``.
    # Keep ordinary compact results duplicated for older clients, but do not
    # double a medium/large assignment into both MCP content channels. The
    # aggregate operation limit is the conservative model-visible threshold;
    # using one quarter leaves room for the outer JSON-RPC envelope and keeps
    # large structured assignment evidence from being displaced by its own
    # redundant text copy.
    text_duplicate_max_bytes = MCP_OPERATION_MAX_BYTES // 4
    text_payload = (
        serialized
        if len(serialized.encode("utf-8")) <= text_duplicate_max_bytes
        else "Complete Cortex result is available in structuredContent."
    )
    content.append({"type": "text", "text": text_payload})
    return {
        # MCP recommends serialized structured content in TextContent for
        # clients that predate structuredContent.  Both blocks are derived
        # from the same projected value; neither is an independent authority.
        "content": content,
        "structuredContent": structured,
        "isError": False,
    }


def _tool_error_result(failure: Mapping[str, Any], *, mutation: str) -> dict[str, Any]:
    """Return a safe, machine-readable tool failure envelope.

    The text and ``structuredContent.error`` carry the same stable Cortex
    code, safe reason, retry guidance, and one next action.  Error results are
    explicitly marked ``isError`` and are not success values for the tool's
    advertised output schema.
    """
    error = {
        "code": str(failure["code"]),
        "message": _safe_message(str(failure["code"])),
        "retryable": bool(failure.get("retryable")),
        "action": str(failure.get("action") or "Review the advertised input contract."),
    }
    details = failure.get("details")
    if isinstance(details, Mapping) and details:
        error["details"] = dict(details)
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
        "structuredContent": {"error": error},
        "isError": True,
    }


def _internal_ledger_failure() -> dict[str, Any]:
    """Sanitize an unexpected dispatch fault into a non-retryable tool error."""
    return {
        "code": "ledger_error",
        "message": _safe_message("ledger_error"),
        "details": {},
        "retryable": False,
        "action": _recovery("ledger_error", {})[1],
    }


def serve_stdio(
    *,
    public_tools: Mapping[str, Mapping[str, Any]],
    server_version: str,
    instructions: str,
) -> None:
    """Serve a fixed V12 MCP tool catalogue over standard input/output."""
    package_root = Path(__file__).absolute().parents[2]
    try:
        verify_runtime(package_root, server_version, allow_source_mode=os.environ.get("CORTEX_SOURCE_MODE") == "1")
    except RuntimeError as exc:
        raise SystemExit(f"Cortex candidate provenance verification failed: {exc}") from exc
    if len(public_tools) != _MAX_TOOLS:
        raise RuntimeError("Cortex public tool registry does not match semantic registry")
    for name, contract in public_tools.items():
        if not isinstance(name, str) or not isinstance(contract, Mapping):
            raise RuntimeError("Cortex v12 public tool registry is invalid")
        if not isinstance(contract.get("description"), str):
            raise RuntimeError("Cortex v12 public tool description is invalid")
        if (
            not isinstance(contract.get("inputSchema"), Mapping)
            or not isinstance(contract.get("outputSchema"), Mapping)
            or not isinstance(contract.get("runtimeOutputSchema"), Mapping)
            or not callable(contract.get("handler"))
        ):
            raise RuntimeError("Cortex v12 public tool binding is invalid")

    catalog_tools = tuple(
        {
            "name": name,
            "description": str(contract["description"]),
            "inputSchema": dict(contract["inputSchema"]),
            "outputSchema": dict(contract["outputSchema"]),
        }
        for name, contract in public_tools.items()
    )
    # MCP outputSchema is optional.  Keep the family-specific result schemas
    # private to the runtime validation boundary below instead of repeating
    # them in every deferred host-tool declaration.  The successful result's
    # structuredContent remains unchanged and self-describing, while one
    # complete tools/list response stays small enough for bounded host tool
    # discovery without truncating later operations from the model's view.
    # This is a digest of the complete advertised catalogue, not a second
    # wire catalogue.  It gives the passive observer compact registration
    # parity without retaining tool names or schemas in the event stream.
    identity = catalogue_identity(public_tools)
    catalogue_digest = str(identity["catalogue_digest"])
    provenance = verify_runtime(
        package_root, server_version,
        allow_source_mode=os.environ.get("CORTEX_SOURCE_MODE") == "1",
    )
    generation = None
    try:
        generation, _request = claim_generation(
            package_root=package_root, build_id=provenance["build_id"],
            candidate_version=package_root.name, catalogue_count=len(catalog_tools),
            catalogue_digest=catalogue_digest,
            # Live-dev supplies the exact nonce created for this tmux session.
            # Passing it into the server-side claim prevents an older MCP
            # process from taking over a newer session's lease.
            session_nonce=os.environ.get("CORTEX_SESSION_NONCE"),
        )
        event_journal = EventJournal.from_generation(
            generation=generation, build_id=provenance["build_id"],
            code_home=candidate_codex_home(package_root),
        )
    except ObservationGenerationError:
        # Observation is deliberately non-authoritative. Candidate identity and
        # canonical MCP behavior remain intact when no live generation exists.
        event_journal = EventJournal(None, build_id=provenance["build_id"])
    server_ready_observed = False

    def observe_call(
        name: str, arguments: Mapping[str, Any], *, success: bool,
        fault: str | None = None, result: Mapping[str, Any] | None = None,
        failure: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit a best-effort safe observation without changing MCP behavior."""
        raw_diagnostic(kind="mcp_observation", payload={"operation": name, "arguments": arguments, "success": success, "fault": fault, "result": result, "failure": failure})
        try:
            specification = spec_for(name)
            kind = specification.kind
        except KeyError:
            kind = "unknown"
        command = kind == "command"
        mutation: str | None = None
        if command:
            if success:
                mutation = "replay" if isinstance(result, Mapping) and result.get("replayed") is True else "new"
            elif isinstance(fault, str) and (fault.endswith("_conflict") or fault in {"command_conflict", "publication_conflict"}):
                mutation = "conflict"
            else:
                mutation = "error"
        task_anchor = arguments.get("task_ref")
        assignment_anchor = arguments.get("assignment_ref")
        if isinstance(result, Mapping):
            task_anchor = task_anchor if isinstance(task_anchor, str) else result.get("task_ref")
            assignment_anchor = assignment_anchor if isinstance(assignment_anchor, str) else result.get("assignment_ref")
            report = result.get("report")
            if isinstance(report, Mapping):
                publication_type = report.get("report_type")
                publication_status = report.get("status")
            else:
                publication_type = None
                publication_status = None
            brief = result.get("dispatch_brief")
            delivery = result.get("host_delivery")
            dispatch_marker = (
                brief.get("dispatch_correlation_marker") if isinstance(brief, Mapping)
                else delivery.get("dispatch_correlation_marker") if isinstance(delivery, Mapping)
                else result.get("dispatch_correlation_marker")
            )
        else:
            publication_type = None
            publication_status = None
            dispatch_marker = None
        details = failure.get("details") if isinstance(failure, Mapping) else None
        validation_location = details.get("path") if isinstance(details, Mapping) else None
        validation_field = details.get("field") if isinstance(details, Mapping) else None
        validation_expected = details.get("expected") if isinstance(details, Mapping) else None
        if fault == "report_incomplete":
            validation_expected = "complete_evidence_envelope"
            corrective_action = "correct_publication_evidence"
        elif fault == "validation_error":
            corrective_action = "review_advertised_schema"
        elif fault in {"invalid_identifier", "task_not_found", "delegation_not_found", "report_not_found"}:
            corrective_action = "reuse_typed_handle"
        else:
            corrective_action = None
        event_journal.emit(
            operation=name, kind=kind, success=success, fault=fault,
            mutation=mutation, task_anchor=task_anchor,
            assignment_anchor=assignment_anchor,
            publication_type=publication_type, publication_status=publication_status,
            dispatch_correlation_marker=dispatch_marker,
            validation_location=validation_location, validation_field=validation_field,
            validation_expected=validation_expected, corrective_action=corrective_action,
        )

    def render(value: Mapping[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    def write(value: Mapping[str, Any]) -> None:
        sys.stdout.write(render(value) + "\n")
        sys.stdout.flush()

    def reply(request_id: object, result: Mapping[str, Any]) -> bool:
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
            return False
        write(payload)
        return True

    def finish_tool_call(
        request_id: object, name: str, arguments: Mapping[str, Any],
        result: Mapping[str, Any], *, success: bool, fault: str | None = None,
        public_result: Mapping[str, Any] | None = None,
        failure: Mapping[str, Any] | None = None,
    ) -> None:
        """Write the final tool reply, then observe that exact wire outcome.

        A handler success is not a client success until its complete physical
        JSONL response fits the wire. This is the single terminal observation
        point for every recognised public tool call.
        """
        wire_success = reply(request_id, result)
        if wire_success:
            observe_call(name, arguments, success=success, fault=fault, result=public_result, failure=failure)
        else:
            observe_call(name, arguments, success=False, fault="ledger_error")

    def finish_malformed_tool_call(request_id: object, *, respond: bool) -> None:
        """Record one safe terminal observation for a malformed call envelope."""
        if respond:
            rpc_error(request_id, -32602, "Invalid params")
        observe_call("unknown", {}, success=False, fault="validation_error")

    def finish_internal_tool_error(request_id: object, name: str, arguments: Mapping[str, Any]) -> None:
        """Emit the standard JSON-RPC internal fault and one matching event."""
        rpc_error(request_id, -32603, "Cortex server state is unavailable", data={"cortex_code": "ledger_error"})
        observe_call(name, arguments, success=False, fault="ledger_error")

    def tools_list_page(request_id: object, cursor: object) -> Mapping[str, Any] | None:
        """Build the largest complete catalog page that fits one JSONL frame.

        MCP defines ``tools/list`` pagination through ``nextCursor``. Cortex
        keeps its fixed public catalogue in the first bounded response because
        ordinary Codex discovery consumes that response as the advertised
        catalog. The wire carries each operation's complete input and output
        schema; structuredContent remains unchanged on every call.
        """
        start = _tools_list_offset(cursor, len(catalog_tools))
        if start is None:
            return None
        page: list[Mapping[str, Any]] = []
        for offset in range(start, len(catalog_tools)):
            candidate = [*page, catalog_tools[offset]]
            next_offset = offset + 1
            result: dict[str, Any] = {"tools": candidate}
            if next_offset < len(catalog_tools):
                result["nextCursor"] = _tools_list_cursor(next_offset)
            payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
            if len(render(payload).encode("utf-8")) > MAX_PHYSICAL_JSONL_FRAME_BYTES:
                if not page:
                    # One declared tool cannot be safely represented at the
                    # transport boundary. Keep the established sanitized
                    # server error rather than emitting an oversized frame.
                    return {"tools": candidate}
                break
            page = candidate
        if not page:
            return {"tools": []}
        next_offset = start + len(page)
        result = {"tools": page}
        if next_offset < len(catalog_tools):
            result["nextCursor"] = _tools_list_cursor(next_offset)
        return result

    def rpc_error(request_id: object, code: int, message: str, *, data: Mapping[str, Any] | None = None) -> None:
        error: dict[str, Any] = {"code": code, "message": message}
        if data:
            error["data"] = dict(data)
        write({"jsonrpc": "2.0", "id": request_id, "error": error})

    session_state = "new"
    # This binding is scoped to one stdio connection. It is established only
    # by a successful open_task or a successful task-scoped call carrying an
    # exact task_ref. A restarted server begins unbound and must receive an
    # exact selector again; the transport never guesses from ledger recency.
    active_task_ref: str | None = None
    # Private per-connection actor and bounded-read state. It is never merged
    # into advertised schemas or returned structured content.
    connection_context: dict[str, Any] = {}
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
                if method == "tools/call":
                    finish_malformed_tool_call(request_id, respond=has_request_id)
                    continue
                raise _RpcError(-32602, "Invalid params")

            if method == "initialize":
                if not has_request_id:
                    continue
                if session_state != "new":
                    raise _RpcError(-32600, "Invalid Request")
                if set(params) - {"protocolVersion", "capabilities", "clientInfo", "_meta"}:
                    raise _RpcError(-32602, "Invalid params")
                client_info = params.get("clientInfo")
                # Initialize is the only phase in which a server can select a
                # compatible protocol.  Echo a supported request and
                # counter-offer the newest supported version otherwise.  The
                # client is responsible for disconnecting if it cannot accept
                # that counter-offer, as required by MCP lifecycle negotiation.
                if (
                    not isinstance(params.get("protocolVersion"), str)
                    or not params["protocolVersion"]
                    or len(params["protocolVersion"].encode("utf-8")) > 64
                    or not isinstance(params.get("capabilities"), Mapping)
                    or not isinstance(client_info, Mapping)
                    or not isinstance(client_info.get("name"), str)
                    or not isinstance(client_info.get("version"), str)
                    or not client_info["name"]
                    or not client_info["version"]
                ):
                    raise _RpcError(-32602, "Invalid params")
                try:
                    provenance = verify_runtime(package_root, server_version, allow_source_mode=os.environ.get("CORTEX_SOURCE_MODE") == "1")
                except RuntimeError as exc:
                    raise SystemExit(f"Cortex candidate provenance verification failed: {exc}") from exc
                requested_protocol_version = params["protocolVersion"]
                negotiated_protocol_version = (
                    requested_protocol_version
                    if requested_protocol_version in MCP_SUPPORTED_PROTOCOL_VERSIONS
                    else MCP_SUPPORTED_PROTOCOL_VERSIONS[0]
                )
                server_info = {"name": "cortex", "version": server_version}
                # The launcher supplies provenance only for isolated candidate
                # runs.  Keep it additive and read-only so normal MCP clients
                # retain the stable semantic version contract.
                server_info.update({
                    "buildId": provenance["build_id"],
                    "sourceDigest": provenance["source_digest"],
                    "candidatePath": provenance["candidate_path"],
                    "parityVerified": provenance["parity_verified"] == "true",
                    "runtimeMode": provenance["runtime_mode"],
                })
                initialize_wire_success = reply(request_id, {
                    "protocolVersion": negotiated_protocol_version,
                    "capabilities": {"tools": {}},
                    "serverInfo": server_info,
                    "instructions": instructions,
                })
                # A ready observation is not an MCP operation result.  It is
                # emitted only after the actual initialize reply has survived
                # the physical JSONL boundary, and at most once per server
                # process even if a client retries initialization incorrectly.
                if initialize_wire_success and not server_ready_observed:
                    event_journal.emit_server_ready(
                        catalogue_count=len(catalog_tools),
                        catalogue_digest=catalogue_digest,
                    )
                    if generation is not None:
                        try:
                            write_ready_receipt(
                                generation, build_id=provenance["build_id"],
                                catalogue_count=len(catalog_tools), catalogue_digest=catalogue_digest,
                            )
                        except ObservationGenerationError:
                            # As with journal failure, receipt publication does
                            # not retroactively falsify the wire reply.
                            pass
                    server_ready_observed = True
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
                result = tools_list_page(request_id, cursor)
                if result is None:
                    raise _RpcError(-32602, "Invalid params")
                reply(request_id, result)
                continue
            if method != "tools/call":
                raise _RpcError(-32601, "Method not found")
            if not has_request_id:
                # A tools/call notification has no wire reply, but it still
                # crossed the public composition boundary and must be visible
                # to the observer exactly once when malformed. Valid tool-call
                # notifications retain the established no-dispatch/no-reply
                # behavior; valid non-tools notifications are handled above.
                name = params.get("name")
                arguments = params.get("arguments", {})
                if (
                    set(params) - {"name", "arguments", "_meta"}
                    or not isinstance(name, str)
                    or name not in public_tools
                    or not isinstance(arguments, Mapping)
                ):
                    finish_malformed_tool_call(request_id, respond=False)
                    continue
                continue
            if set(params) - {"name", "arguments", "_meta"}:
                finish_malformed_tool_call(request_id, respond=True)
                continue
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or name not in public_tools or not isinstance(arguments, Mapping):
                # Do not retain an arbitrary unrecognised caller-provided
                # name. The observation still proves an MCP call failed at
                # the composition boundary.
                finish_malformed_tool_call(request_id, respond=True)
                continue
            contract = public_tools[name]
            resolved_arguments = dict(arguments)
            input_schema = contract.get("_runtimeInputSchema", contract["inputSchema"])
            correcting_aggregate = False
            try:
                actual_argument_bytes = _encoded_json_bytes(arguments, "$")
                corrections = connection_context.setdefault("validation_corrections", {})
                correction = corrections.get(name) if isinstance(corrections, dict) else None
                correcting_aggregate = name == "publish_result" and isinstance(correction, dict)
                if correcting_aggregate and correction.get("exhausted") is True:
                    raise _SchemaError(
                        "$", "JSON aggregate correction is exhausted",
                        actual_bytes=actual_argument_bytes,
                        max_bytes=MCP_OPERATION_MAX_BYTES,
                        correction_state="exhausted",
                    )
                if actual_argument_bytes > MCP_OPERATION_MAX_BYTES:
                    correction_state = None
                    if name == "publish_result" and isinstance(corrections, dict):
                        rendered_arguments = json.dumps(
                            arguments, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False,
                        ).encode("utf-8")
                        request_digest = hashlib.sha256(rendered_arguments).hexdigest()
                        if not isinstance(correction, dict):
                            corrections[name] = {
                                "digest": request_digest,
                                "actual_bytes": actual_argument_bytes,
                                "exhausted": False,
                            }
                            correction_state = "available"
                        elif correction.get("digest") == request_digest:
                            correction["exhausted"] = True
                            correction_state = "unchanged"
                        else:
                            correction["exhausted"] = True
                            correction_state = "exhausted"
                    raise _SchemaError(
                        "$", "JSON value exceeds the maximum encoded byte length",
                        actual_bytes=actual_argument_bytes,
                        max_bytes=MCP_OPERATION_MAX_BYTES,
                        correction_state=correction_state,
                    )
                _validate_schema(input_schema, arguments)
                if correcting_aggregate and isinstance(corrections, dict):
                    # A complete schema-valid correction has crossed the
                    # pre-dispatch boundary. Durable publication idempotency,
                    # not the validation retry guard, owns any later replay.
                    corrections.pop(name, None)
                properties = input_schema.get("properties") if isinstance(input_schema, Mapping) else None
                if (
                    name != "open_task"
                    and isinstance(properties, Mapping)
                    and "task_ref" in properties
                    and "task_ref" not in resolved_arguments
                ):
                    if active_task_ref is None:
                        raise _SchemaError(
                            "$.task_ref",
                            "missing required property 'task_ref' because this MCP connection has no active task context",
                        )
                    resolved_arguments["task_ref"] = active_task_ref
                _validate_public_call_shape(name, resolved_arguments)
            except _SchemaError as error:
                if (
                    correcting_aggregate
                    and error.correction_state is None
                    and isinstance(corrections, dict)
                    and isinstance(corrections.get(name), dict)
                ):
                    corrections[name]["exhausted"] = True
                    error.correction_state = "exhausted"
                failure = _validation_failure(
                    error, tool_name=name, arguments=arguments,
                    input_schema=input_schema,
                )
                finish_tool_call(request_id, name, arguments, _tool_error_result(failure, mutation=name), success=False, fault=str(failure["code"]), failure=failure)
                continue
            try:
                handler_arguments = dict(resolved_arguments)
                if name in {"read_task", "publish_plan", "publish_result", "publish_documentation"}:
                    handler_arguments["_connection_context"] = connection_context
                result = contract["handler"](**handler_arguments)
            except V12ServiceError as error:
                failure = _service_failure(error)
                finish_tool_call(request_id, name, resolved_arguments, _tool_error_result(failure, mutation=name), success=False, fault=str(failure["code"]), failure=failure)
            except sqlite3.Error as error:
                if _sqlite_is_busy(error):
                    failure = {
                        "code": "storage_busy",
                        "message": _safe_message("storage_busy"),
                        "details": {"retry_after_ms": 100},
                        "retryable": True,
                        "action": _recovery("storage_busy", {})[1],
                    }
                    finish_tool_call(request_id, name, resolved_arguments, _tool_error_result(failure, mutation=name), success=False, fault="storage_busy", failure=failure)
                else:
                    failure = _internal_ledger_failure()
                    finish_tool_call(request_id, name, resolved_arguments, _tool_error_result(failure, mutation=name), success=False, fault="ledger_error", failure=failure)
            except (TypeError, ValueError):
                # Public input validation and typed V12ServiceError cover all
                # caller-correctable failures. A raw Python type/value error
                # after dispatch is an implementation fault, not a second
                # inconsistent tool-error protocol.
                failure = _internal_ledger_failure()
                finish_tool_call(request_id, name, resolved_arguments, _tool_error_result(failure, mutation=name), success=False, fault="ledger_error", failure=failure)
            except Exception:
                failure = _internal_ledger_failure()
                finish_tool_call(request_id, name, resolved_arguments, _tool_error_result(failure, mutation=name), success=False, fault="ledger_error", failure=failure)
            else:
                if not isinstance(result, Mapping) or not _is_json_value(result):
                    finish_internal_tool_error(request_id, name, resolved_arguments)
                    continue
                discovered_task_ref = result.get("task_ref")
                # Keep one private, observation-only snapshot before public
                # projection removes internal correlation metadata.  The
                # snapshot is consumed synchronously by observe_call after
                # the wire reply; it never participates in schema validation
                # or the public MCP envelope.
                observation_result = dict(result)
                result = _project_public_views(result)
                # Validate against the complete closed private schema.  The
                # compact outputSchema is discovery-only and intentionally
                # permits additional observational receipt fields.
                public_output_schema = contract["outputSchema"]
                output_schema = contract["runtimeOutputSchema"]
                try:
                    if isinstance(output_schema, Mapping):
                        _validate_schema(output_schema, result)
                except _SchemaError as error:
                    raw_diagnostic(
                        kind="mcp_output_contract_violation",
                        payload={
                            "operation": name,
                            "path": error.path,
                            "message": error.message,
                            "result": result,
                        },
                    )
                    finish_internal_tool_error(request_id, name, resolved_arguments)
                    continue
                if isinstance(discovered_task_ref, str) and task_ref_parts(discovered_task_ref) is not None:
                    active_task_ref = discovered_task_ref
                elif (
                    isinstance(resolved_arguments.get("task_ref"), str)
                    and task_ref_parts(resolved_arguments["task_ref"]) is not None
                ):
                    active_task_ref = resolved_arguments["task_ref"]
                finish_tool_call(
                    request_id, name, resolved_arguments,
                    _success_tool_result(dict(result)), success=True,
                    public_result=observation_result,
                )
        except _RpcError as error:
            if has_request_id:
                rpc_error(request_id, error.code, error.message)
            elif error.code == -32700:
                rpc_error(None, error.code, error.message)
