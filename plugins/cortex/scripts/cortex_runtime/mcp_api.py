"""Public MCP registry and stdio transport, independent of orchestration policy.

The stdio protocol does not carry a trustworthy per-call actor identity.  A
server process therefore receives one immutable audience at launch time.  The
ordinary Desktop launch uses the fresh public union.  A host that can
establish separate trusted channels may opt into coordinator and worker
projections.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from typing import Any

from cortex_runtime.public_contracts import is_internal_question_category

from cortex_runtime.v11_submission import (
    MAX_DIAGNOSTICS,
    json_pointer as _submission_json_pointer,
)
from cortex_runtime.v11_responses import (
    PUBLIC_ACTIONS,
    PUBLIC_FAILURE_ACTIONS,
    PUBLIC_NATIVE_DISPATCH_ACTION_SHAPES,
    PUBLIC_SUCCESS_ACTIONS,
    PUBLIC_UNCHANGED_RETRY_ERROR_CODES,
    TASK_REF_PATTERN,
    ResponseValidationError,
    WAIT_POLICY_REPEAT_UNTIL_TERMINAL,
    expected_native_transition,
    native_dispatch_authority_message,
    public_response_text,
    validate_private_response,
    validate_response,
)
MCP_AUDIENCES = frozenset({"default", "coordinator", "worker"})
DEFAULT_MCP_AUDIENCE = "default"
SUPPORTED_LEGACY_PROTOCOL_VERSIONS = ("2025-06-18",)
DEFAULT_LEGACY_PROTOCOL_VERSION = SUPPORTED_LEGACY_PROTOCOL_VERSIONS[0]
_LEGACY_CONNECTION_NEW = "NEW"
_LEGACY_CONNECTION_INITIALIZE_RESPONSE_SENT = "INITIALIZE_RESPONSE_SENT"
_LEGACY_CONNECTION_READY = "READY"
_MAX_COMPLETE_TOOLS_PAGE = 128
_TOOLS_CALL_RATE_CAPACITY = 600.0
_TOOLS_CALL_RATE_REFILL_PER_SECOND = 10.0
_LEDGER_BUSY_RETRY_DELAY_MS = 250


class _JsonRpcProtocolError(Exception):
    """A caller-owned JSON-RPC boundary error with one standard wire code."""

    code = -32603
    wire_message = "Internal error"


class _JsonRpcInvalidRequest(_JsonRpcProtocolError):
    code = -32600
    wire_message = "Invalid Request"


class _JsonRpcMethodNotFound(_JsonRpcProtocolError):
    code = -32601
    wire_message = "Method not found"


class _JsonRpcInvalidParams(_JsonRpcProtocolError):
    code = -32602
    wire_message = "Invalid params"

# Codex injects the addressed host thread into CallToolRequest.params._meta.
# It is transport authority, never part of a model-facing schema or backend
# argument object.  Context-local storage keeps concurrent/embedded transports
# isolated and makes reset-on-every-path mechanically enforceable.
_HOST_THREAD_ID: ContextVar[str | None] = ContextVar(
    "cortex_transport_host_thread_id", default=None,
)
_HOST_THREAD_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")


def current_host_thread_id() -> str | None:
    """Return the transport-private Codex thread identity for this tool call."""
    return _HOST_THREAD_ID.get()


def _call_host_thread_id(params: Mapping[str, Any]) -> str | None:
    """Strictly decode the sole trusted CallToolRequest metadata location."""
    meta = params.get("_meta")
    if not isinstance(meta, Mapping):
        return None
    value = meta.get("threadId")
    if not isinstance(value, str) or _HOST_THREAD_ID_RE.fullmatch(value) is None:
        return None
    return value

# Raw host credentials must never cross the MCP/public response boundary.
# Keep this projection at the last point before JSON-RPC serialization so an
# individual lifecycle handler cannot re-introduce a bearer/proof through a
# success, recovery, error, transcript, or briefing.
_PUBLIC_SECRET_KEYS = frozenset({
    "dispatch_ref",
    "assignment_ref",
    "coordinator_ref",
    "coordinator_capability",
    "coordinator_recovery_proof",
    "previous_coordinator_recovery_proof",
    "authorization_update",
    "agent_id",
    "session_id",
    "turn_id",
    "host_agent_id",
    "coordinator_host_thread_id",
    "worker_host_thread_id",
    "worker_host_start_turn_id",
})
_ASSIGNMENT_REF_VALUE_RE = re.compile(r"assignment-v1-[0-9a-f]{64}")


def _supplied_coordinator_refs(request: object) -> frozenset[str]:
    """Return only explicit coordinator bearers from one JSON-RPC request."""
    if not isinstance(request, Mapping):
        return frozenset()
    rpc_params = request.get("params")
    arguments = rpc_params.get("arguments") if isinstance(rpc_params, Mapping) else None
    candidate = str(arguments.get("coordinator_ref") or "").strip().lower() if isinstance(arguments, Mapping) else ""
    return frozenset({candidate}) if re.fullmatch(r"[0-9a-f]{64}", candidate) else frozenset()


def _scrub_public_response(
    value: object,
    *,
    allow_coordinator_ref: bool = False,
    allow_worker_context_refs: bool = False,
    supplied_coordinator_refs: frozenset[str] = frozenset(),
) -> object:
    """Remove authority except canonical coordinator, dispatch, and refresh slots."""
    protected_values: set[str] = set(supplied_coordinator_refs)
    current_thread = current_host_thread_id()
    if current_thread:
        protected_values.add(current_thread)

    # A native dispatch reference is model-visible authority required to run
    # exactly the returned call and bootstrap its worker briefing. Preserve it
    # only after the complete flat lifecycle response (including the native
    # call-specific dispatch branch) has passed canonical validation. Merely
    # placing a dispatch-shaped object elsewhere never creates an exception.
    allowed_native_dispatch_paths: set[tuple[object, ...]] = set()
    allowed_native_message_authorities: dict[tuple[object, ...], str] = {}
    if (
        isinstance(value, Mapping)
        and value.get("action") in PUBLIC_NATIVE_DISPATCH_ACTION_SHAPES
    ):
        try:
            validated = _validate_flat_public_response(dict(value))
        except (ResponseValidationError, ValueError, TypeError, KeyError):
            pass
        else:
            for index, item in enumerate(validated.get("dispatches") or []):
                if isinstance(item, Mapping) and isinstance(item.get("dispatch_ref"), str):
                    allowed_native_dispatch_paths.add(("dispatches", index, "dispatch_ref"))
                    allowed_native_message_authorities[
                        ("dispatches", index, "arguments", "message")
                    ] = str(item["dispatch_ref"])

    def collect(node: object) -> None:
        if isinstance(node, Mapping):
            for key, item in node.items():
                if str(key) in _PUBLIC_SECRET_KEYS and isinstance(item, str) and item:
                    protected_values.add(item)
                collect(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                collect(item)

    collect(value)

    def scrub(node: object, path: tuple[object, ...]) -> object:
        if isinstance(node, Mapping):
            result: dict[str, object] = {}
            for key, item in node.items():
                normalized = str(key)
                child_path = (*path, normalized)
                if normalized == "coordinator_ref":
                    if (
                        allow_coordinator_ref and path == () and isinstance(item, str)
                        and re.fullmatch(r"[0-9a-f]{64}", item)
                    ):
                        result[normalized] = item
                    continue
                if normalized in _PUBLIC_SECRET_KEYS:
                    if (
                        allow_worker_context_refs
                        and path == ()
                        and normalized == "dispatch_ref"
                        and isinstance(item, str)
                        and re.fullmatch(r"dispatch-[A-Za-z0-9._:-]{1,160}", item)
                    ):
                        result[normalized] = item
                    elif (
                        child_path in allowed_native_dispatch_paths
                        and isinstance(item, str)
                    ):
                        result[normalized] = item
                    continue
                result[normalized] = scrub(item, child_path)
            return result
        if isinstance(node, list):
            return [scrub(item, (*path, index)) for index, item in enumerate(node)]
        if isinstance(node, tuple):
            return [scrub(item, (*path, index)) for index, item in enumerate(node)]
        if isinstance(node, str):
            scrubbed = _ASSIGNMENT_REF_VALUE_RE.sub("<redacted-assignment-ref>", node)
            allowed_message_authority = allowed_native_message_authorities.get(path)
            for protected in protected_values:
                if protected == allowed_message_authority:
                    continue
                scrubbed = scrubbed.replace(protected, "<redacted-private-host-value>")
            return scrubbed
        return node

    return scrub(value, ())


def _canonical_jsonrpc_request_id(value: object) -> str:
    """Encode one already-validated JSON-RPC id for lifecycle composition."""
    if isinstance(value, str):
        return "string:" + json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int) and not isinstance(value, bool):
        return f"integer:{value}"
    raise TypeError("JSON-RPC request id must be a string or signed i64 integer")


_JSONRPC_I64_MIN = -(2**63)
_JSONRPC_I64_MAX = 2**63 - 1
_LEGACY_METHODS = frozenset({
    "initialize",
    "notifications/initialized",
    "tools/list",
    "resources/list",
    "resources/templates/list",
    "tools/call",
    "ping",
})


# These operations are implementation ports used by the orchestration engine,
# never model-facing MCP operations. Keep the boundary explicit: a new
# internal handler must not become callable merely because it was added to the
# handler registry. The public registry below is the only model contract.
# These names remain implementation ports for the SQLite engine only; they
# are deliberately not operation contracts and must never be projected to a
# model-facing MCP channel.
SERVER_ONLY_TOOL_NAMES = frozenset({
    "init_task", "get_task_status", "finalize_attempt",
    "record_evidence", "execute_verification_command", "cortex.question",
    "publish_worker_question", "list_worker_questions", "answer_worker_question",
    "get_worker_question_updates", "commit_gate", "update_pipeline",
    "reassess_pipeline", "acquire_lock",
    "release_lock", "create_handoff", "claim_resource", "release_resource",
    "create_lane", "get_lane_status", "claim_lane", "release_lane",
    "retire_lane", "bind_task_lane", "claim_lane_resource",
    "release_lane_resource", "materialize_lane", "reconcile_lane",
    "activate_orchestration", "deactivate_orchestration",
    "classify_task", "resolve_dispatch_route",
})


def _response_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    import hashlib
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _minimal_field_schema(raw_schema: Mapping[str, Any], *, depth: int = 2) -> dict[str, Any]:
    """Project only bounded, closed, model-actionable JSON Schema facets."""
    safe_schema: dict[str, Any] = {}
    raw_type = raw_schema.get("type")
    allowed_types = {"object", "array", "string", "integer", "boolean"}
    if isinstance(raw_type, str) and raw_type in allowed_types:
        safe_schema["type"] = raw_type
    elif isinstance(raw_type, list):
        safe_schema["type"] = next((value for value in raw_type if isinstance(value, str) and value in allowed_types), "object")
    for key in ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"):
        value = raw_schema.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            safe_schema[key] = value
    for key in ("minimum", "maximum"):
        value = raw_schema.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            safe_schema[key] = value
    pattern = raw_schema.get("pattern")
    if isinstance(pattern, str) and len(pattern) <= 1024:
        safe_schema["pattern"] = pattern
    format_name = raw_schema.get("format")
    if format_name == "project-relative-path":
        safe_schema["format"] = format_name
    enum = raw_schema.get("enum")
    if isinstance(enum, list):
        values = [
            value for value in enum[:64]
            if (
                isinstance(value, bool)
                or (isinstance(value, int) and not isinstance(value, bool))
                or (isinstance(value, str) and len(value) <= 256)
            )
        ]
        if values:
            safe_schema["enum"] = values
    const = raw_schema.get("const")
    if (
        isinstance(const, bool)
        or (isinstance(const, int) and not isinstance(const, bool))
        or (isinstance(const, str) and len(const) <= 256)
    ):
        safe_schema["const"] = const
    if isinstance(raw_schema.get("uniqueItems"), bool):
        safe_schema["uniqueItems"] = raw_schema["uniqueItems"]
    if isinstance(raw_schema.get("additionalProperties"), bool):
        safe_schema["additionalProperties"] = raw_schema["additionalProperties"]
    required = raw_schema.get("required")
    if isinstance(required, list):
        names = [str(name)[:256] for name in required[:64] if isinstance(name, str) and name]
        if names:
            safe_schema["required"] = names
    if depth > 0:
        properties = raw_schema.get("properties")
        if isinstance(properties, Mapping):
            projected = {
                str(name)[:256]: _minimal_field_schema(child, depth=depth - 1)
                for name, child in list(properties.items())[:64]
                if str(name) and isinstance(child, Mapping)
            }
            if projected:
                safe_schema["properties"] = projected
        raw_items = raw_schema.get("items")
        if isinstance(raw_items, Mapping):
            item_schema = _minimal_field_schema(raw_items, depth=depth - 1)
            if item_schema:
                safe_schema["items"] = item_schema
    if not safe_schema:
        safe_schema = {"type": "object"}
    return safe_schema


def _minimal_diagnostic(raw: object, *, default_code: str) -> dict[str, Any]:
    item = raw if isinstance(raw, Mapping) else {}
    pointer = str(item.get("json_pointer") or "").strip()
    if not pointer:
        path = str(item.get("path") or "").strip()
        if path:
            pointer = _submission_json_pointer(path)
    if pointer and not pointer.startswith("/"):
        pointer = "/" + pointer
    raw_schema = item.get("field_schema") if isinstance(item.get("field_schema"), Mapping) else {}
    diagnostic: dict[str, Any] = {
        "code": str(item.get("code") or default_code)[:160],
        "json_pointer": pointer[:2048],
        "message": str(item.get("message") or "The response could not be completed.")[:2000],
        "field_schema": _minimal_field_schema(raw_schema),
    }
    # These fields carry Cortex-issued references or opaque capabilities.  A
    # format card describes their wire shape, not a value a model is allowed
    # to invent.  Mark them without echoing the submitted or canonical value.
    field = pointer.rsplit("/", 1)[-1] if pointer else ""
    explicit_source = str(item.get("value_source") or "").strip()
    if explicit_source in {"model", "cortex"}:
        diagnostic["value_source"] = explicit_source
    elif field in {
        "task_ref", "assignment_ref", "coordinator_ref", "question_ref",
        "cursor", "next_cursor", "dispatch_ref",
        "attempt_result_ref", "repair_capsule", "base_payload_digest",
        "request_id", "source_task_ref", "result_refs", "step",
    }:
        diagnostic["value_source"] = "cortex"
    for name in ("required_with", "forbidden_with"):
        values = item.get(name)
        if isinstance(values, list):
            pointers = [str(value)[:2048] for value in values[:32] if isinstance(value, str) and value.startswith("/")]
            if pointers:
                diagnostic[name] = pointers
    branch = str(item.get("branch") or "").strip()
    if branch:
        diagnostic["branch"] = branch[:160]
    return diagnostic


def _same_operation_change(diagnostic: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return one legal retry edit, or ``None`` when a retry would guess.

    Cortex-issued values are never reconstructed from a regex.  They are
    repairable here only when the legal change is to remove a forbidden
    field; adding or replacing one requires an exact value already delivered
    by a separate authoritative response and therefore cannot be authorized
    by this error card alone.
    """
    pointer = str(diagnostic.get("json_pointer") or "").strip()
    if not pointer.startswith("/"):
        return None
    message = str(diagnostic.get("message") or "").strip().lower()
    code = str(diagnostic.get("code") or "").strip().lower()
    removal = any(marker in message for marker in (
        "unsupported field", "unsupported worker_question field",
        "unsupported read_dispatch_briefing field",
        "is not allowed", "must omit", "accepts only",
        "remove this field", "forbidden",
    )) or (
        message.startswith("unsupported ") and " field" in message
    ) or code in {"validation_unknown", "unknown_field"}
    if removal:
        operations = ["remove"]
    elif diagnostic.get("value_source") == "cortex":
        return None
    elif any(marker in message for marker in ("is required", "are required", "requires ", "require ", "missing")):
        operations = ["add"]
    else:
        operations = ["replace"]
    return {"json_pointer": pointer, "allowed_ops": operations}


def _same_operation_changes(
    diagnostics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]] | None:
    """Return a complete de-duplicated legal edit set for one retry."""
    changes: list[dict[str, Any]] = []
    by_pointer: dict[str, tuple[str, ...]] = {}
    for diagnostic in diagnostics:
        change = _same_operation_change(diagnostic)
        if change is None:
            return None
        pointer = str(change["json_pointer"])
        operations = tuple(str(item) for item in change["allowed_ops"])
        prior = by_pointer.get(pointer)
        if prior is not None:
            if prior != operations:
                return None
            continue
        by_pointer[pointer] = operations
        changes.append(change)
    return changes or None


def _minimal_failure_card(
    source: Mapping[str, Any],
    *,
    default_code: str,
    retryable: bool,
    operation: str = "unknown_operation",
) -> dict[str, Any]:
    raw = source.get("diagnostics")
    diagnostics = (
        [_minimal_diagnostic(item, default_code=default_code) for item in raw[:64]]
        if isinstance(raw, list) and raw
        else [_minimal_diagnostic({"code": source.get("code") or default_code, "message": source.get("message") or source.get("next_action") or "The operation could not be completed."}, default_code=default_code)]
    )
    raw_retry = source.get("retry") if isinstance(source.get("retry"), Mapping) else {}
    requested_kind = str(raw_retry.get("kind") or "").strip()
    allowed_changes: list[dict[str, Any]] | None = None
    if requested_kind == "repair_patch_only":
        retry_kind = "repair_patch_only"
    elif not retryable:
        retry_kind = "terminal_stop"
    elif requested_kind == "inspect_server_state" or str(source.get("code") or "").endswith("_stale"):
        retry_kind = "inspect_server_state"
    else:
        allowed_changes = _same_operation_changes(diagnostics)
        retry_kind = "same_operation" if allowed_changes is not None else "terminal_stop"
    code = str(source.get("code") or default_code)[:160]
    category = (
        "integrity" if "integrity" in code else
        "authority" if any(token in code for token in ("identity", "capability", "authorization")) else
        "stale" if "stale" in code or retry_kind == "inspect_server_state" else
        "unavailable" if retry_kind == "terminal_stop" else
        "validation" if "validation" in code or retry_kind == "same_operation" else "internal"
    )
    message = str(source.get("message") or source.get("next_action") or "Cortex rejected this operation.").strip()[:512]
    effective_retryable = bool(retryable) and retry_kind != "terminal_stop"
    recovery: dict[str, Any] = {
        "kind": retry_kind,
        "operation": str(raw_retry.get("operation") or operation)[:64] or "unknown_operation",
        "retryable": effective_retryable,
        "state_mutated": False,
    }
    if retry_kind == "same_operation" and allowed_changes is not None:
        recovery["allowed_changes"] = allowed_changes
    return {
        "error": {"code": code, "category": category, "message": message or "Cortex rejected this operation.", "diagnostics": diagnostics},
        "recovery": recovery,
    }


def _lifecycle_step(source: Mapping[str, Any]) -> int:
    raw = source.get("step")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1:
        return raw
    match = re.search(r"(\d+)$", str(source.get("wave_id") or ""))
    return max(1, int(match.group(1))) if match else 1


def _real_question(source: Mapping[str, Any]) -> dict[str, Any] | None:
    result = source.get("result") if isinstance(source.get("result"), Mapping) else {}
    candidate = result.get("question")
    if not isinstance(candidate, Mapping):
        return None
    question_ref = str(candidate.get("question_ref") or "").strip()
    question_text = candidate.get("question_text")
    if (
        not re.fullmatch(r"question-[A-Za-z0-9._:-]{1,160}", question_ref)
        or not isinstance(question_text, str)
        or not question_text
    ):
        return None
    return {"question_ref": question_ref, "question_text": question_text}


def _plan_decision(source: Mapping[str, Any]) -> dict[str, Any] | None:
    result = source.get("result") if isinstance(source.get("result"), Mapping) else {}
    review = result.get("plan_review") if isinstance(result.get("plan_review"), Mapping) else source.get("plan_review")
    review = review if isinstance(review, Mapping) else {}
    interaction = source.get("chat_interaction") if isinstance(source.get("chat_interaction"), Mapping) else {}
    request_id = str(
        review.get("request_id") or review.get("approval_request_id") or result.get("request_id")
        or interaction.get("interaction_ref") or ""
    ).strip()
    result_ref = str(review.get("result_ref") or review.get("plan_result_ref") or result.get("attempt_result_ref") or "").strip()
    if not re.fullmatch(r"approval-[A-Za-z0-9._:-]{1,160}", request_id):
        return None
    if not re.fullmatch(r"attempt-result-[A-Za-z0-9._:-]{1,160}", result_ref):
        return None
    digest_value = review.get("plan_digest") or review.get("digest") or review
    return {
        "request_id": request_id,
        "plan_result_ref": result_ref,
        "plan_digest": (
            str(digest_value)
            if isinstance(digest_value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest_value)
            else ("sha256:" + str(digest_value) if isinstance(digest_value, str) and re.fullmatch(r"[0-9a-f]{64}", digest_value) else _response_digest(digest_value))
        ),
        "choices": ["approve_with_recommendations", "approve_without_recommendations", "cancel"],
    }


def _completed_handoff(source: Mapping[str, Any]) -> dict[str, Any] | None:
    summary = source.get("state_summary") if isinstance(source.get("state_summary"), Mapping) else {}
    result = source.get("result") if isinstance(source.get("result"), Mapping) else {}
    handoff = result.get("context_handoff") if isinstance(result.get("context_handoff"), Mapping) else result.get("handoff")
    if not isinstance(handoff, Mapping):
        handoff = summary
    durable_close_verified = summary.get("close_verified") is True
    durable_handoff_created = summary.get("handoff_created") is True
    if not (durable_close_verified and durable_handoff_created):
        return None
    basis = handoff if handoff else {
        "state": "completed",
        "result": result,
    }
    digest = _response_digest(basis)
    return {
        "ref": "handoff-" + digest.removeprefix("sha256:")[:24],
        "digest": digest,
        "close_verified": True,
    }


def _profile_resolution_projection(source: Mapping[str, Any]) -> dict[str, str]:
    """Expose a capability normalization without changing native arguments."""
    requested = str(source.get("requested_profile") or "").strip()
    resolved = str(source.get("resolved_profile") or "").strip()
    reason = str(source.get("resolution_reason") or "").strip()
    if not requested or not resolved or requested == resolved or not reason:
        return {}
    return {
        "requested_profile": requested,
        "resolved_profile": resolved,
        "resolution_reason": reason,
    }


def _compiled_plan_projection(source: Mapping[str, Any]) -> str | None:
    """Return the bounded authoritative compiled plan as one flat text value."""
    pipeline = source.get("pipeline")
    if not isinstance(pipeline, Mapping):
        return None
    rendered = json.dumps(
        pipeline, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    if not rendered or len(rendered) > 65_536:
        raise ValueError("compiled plan projection exceeds the public lifecycle bound")
    return rendered


def private_lifecycle_response(
    old: dict[str, Any],
    task_ref: str,
    *,
    native_arguments: Callable[[dict[str, Any]], dict[str, Any]],
    public_schema: str,
    coordinator_lock: str,
    include_result: bool = False,
    start_replayed: bool | None = None,
) -> dict[str, Any]:
    """Compose one private engine lifecycle receipt before flat projection."""
    del public_schema, coordinator_lock, include_result
    source = old if isinstance(old, Mapping) else {}
    base = {
        "schema": "cortex/lifecycle-response/v11",
        "task_ref": str(task_ref or ""),
    }
    rework_receipt = source.get("rework_receipt")
    if isinstance(rework_receipt, str) and rework_receipt:
        base["rework_receipt"] = rework_receipt
    if isinstance(source.get("idempotent"), bool):
        base["idempotent"] = bool(source["idempotent"])
    if start_replayed is True:
        response = {
            "schema": "cortex/lifecycle-response/v11",
            "ok": False,
            "outcome": "failed",
            "action": {"kind": "none"},
            **_minimal_failure_card(
                {"code": "coordinator_capability_lost", "message": "The successful start was already consumed and its coordinator capability cannot be reissued.", "diagnostics": []},
                default_code="coordinator_capability_lost",
                retryable=False,
                operation="start_orchestration",
            ),
        }
        return validate_private_response("private.coordinator.start", response)

    if not source.get("ok"):
        failure_card = _minimal_failure_card(
            source,
            default_code=str(source.get("code") or "orchestration_failed"),
            retryable=bool(source.get("retryable", source.get("recoverable", True))),
        )
        recovery_kind = str(failure_card["recovery"]["kind"])
        response = {
            **base,
            "ok": False,
            "outcome": "needs_input" if recovery_kind == "same_operation" else "failed",
            "action": {"kind": (
                "retry_same_operation" if recovery_kind == "same_operation" else
                "inspect_or_retry" if recovery_kind == "inspect_server_state" else
                "none"
            )},
            **failure_card,
        }
        return validate_private_response("private.coordinator.lifecycle", response)

    requests = source.get("spawn_requests") if isinstance(source.get("spawn_requests"), list) else []
    dispatches: list[dict[str, Any]] = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        if str(request.get("native_call") or "") == "followup_task":
            target = str(request.get("followup_target") or "")
            message = str(request.get("message") or "")
            dispatches.append({
                "call": "followup_task",
                "dispatch_ref": str(request.get("dispatch_ref") or ""),
                "host_task_name": target,
                "arguments": {"target": target, "message": message},
                **_profile_resolution_projection(request),
            })
        else:
            dispatches.append({
                "call": "spawn_agent",
                "dispatch_ref": str(request.get("dispatch_ref") or ""),
                "arguments": native_arguments(request),
                **_profile_resolution_projection(request),
            })
    step = _lifecycle_step(source)
    if dispatches:
        response = {
            **base,
            "ok": True,
            "outcome": "ready_to_spawn",
            "action": {"kind": "invoke_dispatches"},
            "step": step,
            "dispatches": dispatches,
        }
        compiled_plan = _compiled_plan_projection(source)
        if compiled_plan is not None:
            response["compiled_plan"] = compiled_plan
        return validate_private_response("private.coordinator.lifecycle", response)

    state = str(source.get("state") or "").strip()
    if state == "completion_pending":
        response = {
            **base,
            "ok": True,
            "outcome": "completion_pending",
            "action": {"kind": "read_worker_wave"},
            "step": step,
        }
        return validate_private_response("private.coordinator.lifecycle", response)

    if state == "host_epoch_resume_required":
        response = {
            **base,
            "ok": True,
            "outcome": "resume_required",
            "action": {"kind": "resume_orchestration"},
            "content": str(source.get("content") or ""),
        }
        return validate_private_response("private.coordinator.lifecycle", response)

    if state == "context_inspection_required":
        response = {
            **base,
            "ok": True,
            "outcome": "context_inspection_required",
            "action": {"kind": "inspect_orchestration"},
            "content": str(source.get("content") or ""),
        }
        return validate_private_response("private.coordinator.lifecycle", response)

    if state == "rework_preflight_required":
        result = source.get("result") if isinstance(source.get("result"), Mapping) else {}
        receipt = result.get("product_rework") if isinstance(result.get("product_rework"), Mapping) else {}
        source_result_ref = str(receipt.get("source_result_ref") or "").strip()
        if not source_result_ref:
            raise ValueError("product rework lifecycle is missing its canonical source result")
        response = {
            **base,
            "ok": True,
            "outcome": "rework_required",
            "action": {"kind": "append_rework_wave"},
            "source_result_ref": source_result_ref,
        }
        return validate_private_response("private.coordinator.lifecycle", response)

    if state in {"waiting_workers", "waiting"}:
        response = {
            **base,
            "ok": True,
            "outcome": "waiting",
            "action": {"kind": "wait_for_bound_workers"},
            "step": step,
        }
        return validate_private_response("private.coordinator.lifecycle", response)

    if state == "awaiting_plan_approval":
        decision = _plan_decision(source)
        if decision is not None:
            response = {
                **base,
                "ok": True,
                "outcome": "plan_approval",
                "action": {"kind": "obtain_plan_approval"},
                "decision": decision,
            }
            return validate_private_response("private.coordinator.lifecycle", response)

    if state == "needs_input":
        question = _real_question(source)
        if question is not None:
            response = {
                **base,
                "ok": True,
                "outcome": "needs_input",
                "action": {"kind": "obtain_user_decision"},
                "question": question,
            }
            return validate_private_response("private.coordinator.lifecycle", response)

    if state == "completed":
        handoff = _completed_handoff(source)
        if handoff is not None:
            response = {
                **base,
                "ok": True,
                "outcome": "completed",
                "action": {"kind": "deliver_handoff"},
                "handoff": handoff,
            }
            return validate_private_response("private.coordinator.lifecycle", response)
        failure = _minimal_failure_card(
            {
                "code": "completion_not_close_verified",
                "message": "The task reached a terminal engine state without durable close and handoff evidence.",
            },
            default_code="completion_not_close_verified",
            retryable=False,
            operation="manage_orchestration",
        )
        return validate_private_response("private.coordinator.lifecycle", {
            **base,
            "ok": False,
            "outcome": "failed",
            "action": {"kind": "none"},
            **failure,
        })

    if state == "bootstrap_terminal_failure":
        response = {
            "schema": "cortex/lifecycle-response/v11",
            "ok": False,
            "outcome": "failed",
            "action": {"kind": "none"},
            **_minimal_failure_card(
                source,
                default_code="bootstrap_terminal_failure",
                retryable=False,
                operation="manage_orchestration",
            ),
        }
        return validate_private_response("private.coordinator.lifecycle", response)

    failure_card = _minimal_failure_card(
        source,
        default_code=str(source.get("code") or "lifecycle_state_unavailable"),
        retryable=bool(source.get("recoverable", True)),
        operation="manage_orchestration",
    )
    response = {
        **base,
        "ok": False,
        "outcome": "failed",
        "action": {"kind": "none" if failure_card["recovery"]["kind"] == "terminal_stop" else "inspect_or_retry"},
        **failure_card,
    }
    return validate_private_response("private.coordinator.lifecycle", response)


_PUBLIC_RESPONSE_FAMILIES = {
    "start_orchestration": "public.flat",
    "continue_orchestration": "public.flat",
    "manage_orchestration": "public.flat",
    "manage_governance": "public.flat",
    "read_worker_result": "public.flat",
    "refresh_worker_context": "public.flat",
    "read_dispatch_briefing": "public.flat",
    "record_attempt_event": "public.flat",
    "record_worker_finding": "public.flat",
    "worker_question": "public.flat",
    "complete_attempt": "public.flat",
}


def _public_task_ref(source: Mapping[str, Any], arguments: Mapping[str, Any]) -> str | None:
    """Select only a syntactically valid explicit task reference for projection."""
    for candidate in (source.get("task_ref"), arguments.get("task_ref")):
        value = str(candidate or "").strip()
        if re.fullmatch(TASK_REF_PATTERN, value):
            return value
    return None


def _public_internal_failure(tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return the uniform fail-closed machine envelope."""
    del arguments
    return _validate_flat_public_response({
        "ok": False,
        "action": "none",
        "retryable": False,
        "state_mutated": False,
        "error_code": "public_response_projection_failed",
        "error": f"Cortex could not safely project the {tool} response.",
    })


def _public_argument_shape_failure(tool: str) -> dict[str, Any]:
    """Return one executable correction for a non-object arguments value."""
    return _validate_flat_public_response({
        "ok": False,
        "action": "retry_same_operation",
        "retryable": True,
        "state_mutated": False,
        "error_code": "tool_arguments_invalid",
        "error": "Tool arguments must be the closed object advertised by tools/list.",
        "allowed_changes": [{
            "path": "/arguments",
            "op": "replace",
            "expected": "a closed JSON object",
        }],
    })


def _public_host_identity_failure() -> dict[str, Any]:
    """Return a closed receipt when trusted host call identity is unavailable."""
    return _validate_flat_public_response({
        "ok": False,
        "action": "none",
        "retryable": False,
        "state_mutated": False,
        "error_code": "host_thread_identity_required",
        "error": "This lifecycle call requires valid host thread metadata.",
    })


def _public_transient_retry_failure(
    *,
    error_code: str,
    error: str,
    retry_after_ms: int,
) -> dict[str, Any]:
    """Return an unchanged, bounded retry without inventing field repairs."""
    bounded_delay = max(1, min(int(retry_after_ms), 60_000))
    return _validate_flat_public_response({
        "ok": False,
        "action": "retry_same_operation",
        "retryable": True,
        "state_mutated": False,
        "error_code": str(error_code)[:160],
        "error": f"{str(error)[:1_900]} Retry the identical call after at least {bounded_delay} ms.",
    })


def _public_schema_failure(
    schema: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Validate arguments from the exact schema object advertised by tools/list."""
    changes: list[dict[str, str]] = []

    def pointer(path: tuple[object, ...]) -> str:
        return "".join(
            "/" + str(part).replace("~", "~0").replace("/", "~1")
            for part in path
        )

    def invalid(path: tuple[object, ...], *, op: str, expected: str) -> None:
        changes.append({"path": pointer(path), "op": op, "expected": expected})

    def validate(value: object, current: Mapping[str, Any], path: tuple[object, ...]) -> None:
        expected_type = current.get("type")
        valid_type = (
            (expected_type == "object" and isinstance(value, Mapping))
            or (expected_type == "array" and isinstance(value, list))
            or (expected_type == "string" and isinstance(value, str))
            or (expected_type == "integer" and type(value) is int)
            or (expected_type == "boolean" and type(value) is bool)
        )
        if expected_type in {"object", "array", "string", "integer", "boolean"} and not valid_type:
            invalid(path, op="replace", expected=f"a JSON {expected_type} accepted by the advertised inputSchema")
            return
        if "const" in current and value != current["const"]:
            invalid(path, op="replace", expected="the constant advertised by inputSchema")
        enum = current.get("enum")
        if isinstance(enum, list) and value not in enum:
            invalid(path, op="replace", expected="one of the values advertised by inputSchema")
        if isinstance(value, str):
            minimum = current.get("minLength")
            maximum = current.get("maxLength")
            pattern = current.get("pattern")
            if isinstance(minimum, int) and len(value) < minimum:
                invalid(path, op="replace", expected="a string meeting the advertised minimum length")
            elif isinstance(maximum, int) and len(value) > maximum:
                invalid(path, op="replace", expected="a string meeting the advertised maximum length")
            elif isinstance(pattern, str):
                try:
                    matched = re.fullmatch(pattern, value) is not None
                except re.error:
                    matched = False
                if not matched:
                    invalid(path, op="replace", expected="a string matching the advertised pattern")
        elif type(value) is int:
            minimum = current.get("minimum")
            maximum = current.get("maximum")
            if isinstance(minimum, int) and value < minimum:
                invalid(path, op="replace", expected="an integer meeting the advertised minimum")
            elif isinstance(maximum, int) and value > maximum:
                invalid(path, op="replace", expected="an integer meeting the advertised maximum")
        elif isinstance(value, list):
            minimum = current.get("minItems")
            maximum = current.get("maxItems")
            if isinstance(minimum, int) and len(value) < minimum:
                invalid(path, op="replace", expected="an array meeting the advertised minimum size")
            elif isinstance(maximum, int) and len(value) > maximum:
                invalid(path, op="replace", expected="an array meeting the advertised maximum size")
            if current.get("uniqueItems") is True:
                encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) for item in value]
                if len(encoded) != len(set(encoded)):
                    invalid(path, op="replace", expected="an array with unique items")
            items = current.get("items")
            if isinstance(items, Mapping):
                for index, item in enumerate(value):
                    validate(item, items, (*path, index))
        elif isinstance(value, Mapping):
            properties = current.get("properties")
            visible = properties if isinstance(properties, Mapping) else {}
            required = current.get("required")
            if isinstance(required, list):
                for name in required:
                    if isinstance(name, str) and name not in value:
                        invalid((*path, name), op="add", expected="the required value advertised by inputSchema")
            if current.get("additionalProperties") is False:
                for name in value:
                    if name not in visible:
                        invalid((*path, name), op="remove", expected="field omitted from this closed inputSchema")
            for name, item in value.items():
                child = visible.get(name)
                if isinstance(child, Mapping):
                    validate(item, child, (*path, name))

    validate(arguments, schema, ())
    if not changes:
        return None
    return _validate_flat_public_response({
        "ok": False,
        "action": "retry_same_operation",
        "retryable": True,
        "state_mutated": False,
        "error_code": "tool_arguments_invalid",
        "error": "Tool arguments do not match the advertised inputSchema.",
        "allowed_changes": changes[:MAX_DIAGNOSTICS],
    })


def _public_internal_question_failure() -> dict[str, Any]:
    """Reject known technical categories before generic enum repair can misroute them."""
    return _validate_flat_public_response({
        "ok": False,
        "action": "server_recovery",
        "retryable": False,
        "state_mutated": False,
        "error_code": "internal_worker_question_forbidden",
        "error": (
            "Internal technical conditions cannot be recorded as user questions. End this native turn "
            "without a question; use the server-owned compiler/reconciler recovery path."
        ),
    })


_FLAT_PUBLIC_COMMON_FIELDS = {
    "ok", "action", "retryable", "state_mutated",
}
_FLAT_PUBLIC_SUCCESS_FIELDS = {
    "task_ref", "coordinator_ref", "step", "dispatches", "content", "report", "outcome",
    "next_cursor", "complete", "question_ref", "request_id", "choices",
    "source_result_ref", "result_refs", "receipt_ref", "digest", "compiled_plan", "terminal",
    "next_native_action", "read_worker_wave_allowed", "wait_policy",
    "dispatch_ref", "frontier_ref", "catalog_ref", "question_status",
}
_FLAT_PUBLIC_FAILURE_FIELDS = {
    "error_code", "error", "allowed_changes", "repair_capsule",
    "base_payload_digest", "repair_changes",
    "next_native_action", "read_worker_wave_allowed", "wait_policy",
}


def _flat_response_base(action: str, *, state_mutated: bool = False) -> dict[str, Any]:
    return {
        "ok": True,
        "action": str(action),
        "retryable": False,
        "state_mutated": bool(state_mutated),
    }


def _attach_native_transition(value: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the one canonical post-dispatch/wait transition contract."""
    response = dict(value)
    transition = expected_native_transition(response)
    if transition is not None:
        response["next_native_action"], response["read_worker_wave_allowed"] = transition
        if transition == ("wait_agent", False):
            response["wait_policy"] = WAIT_POLICY_REPEAT_UNTIL_TERMINAL
            if response.get("action") == "wait_for_bound_workers":
                response["outcome"] = "waiting_workers"
    return response


def _flat_change(raw: object, *, repair: bool = False) -> dict[str, Any] | None:
    item = raw if isinstance(raw, Mapping) else {}
    path = str(
        item.get("repair_pointer") if repair else
        item.get("json_pointer") or item.get("path") or ""
    )
    if path and not path.startswith("/"):
        path = "/" + "/".join(
            part.replace("~", "~0").replace("/", "~1")
            for part in path.removeprefix("$.").split(".") if part
        )
    if not path and repair:
        return None
    allowed = item.get("allowed_ops")
    op = str(allowed[0]) if isinstance(allowed, list) and allowed else (
        "remove" if str(item.get("code") or "").endswith("unknown") else "replace"
    )
    if op not in {"add", "replace", "remove"}:
        op = "replace"
    expected = item.get("field_schema")
    if expected is None:
        expected = item.get("expected")
    if expected is None:
        expected = "a value accepted by the advertised flat field"
    return {"path": path, "op": op, "expected": _flat_report(expected, _PUBLIC_SCHEMA_FIELDS)}


def _flat_failure(tool: str, source: Mapping[str, Any]) -> dict[str, Any]:
    nested_error = source.get("error") if isinstance(source.get("error"), Mapping) else {}
    nested_recovery = source.get("recovery") if isinstance(source.get("recovery"), Mapping) else {}
    repair = source.get("repair") if isinstance(source.get("repair"), Mapping) else (
        nested_recovery.get("repair") if isinstance(nested_recovery.get("repair"), Mapping) else {}
    )
    diagnostics = source.get("diagnostics")
    if not isinstance(diagnostics, list) or not diagnostics:
        diagnostics = nested_error.get("diagnostics")
    if not isinstance(diagnostics, list):
        diagnostics = []
    retryable = bool(source.get("retryable", nested_recovery.get("retryable", False)))
    state_mutated = bool(source.get("state_mutated", nested_recovery.get("state_mutated", False)))
    code = str(source.get("code") or nested_error.get("code") or f"{tool}_failed")[:160]
    message = str(
        source.get("message") or nested_error.get("message")
        or next((item.get("message") for item in diagnostics if isinstance(item, Mapping) and item.get("message")), "")
        or "Cortex rejected this operation."
    )[:2_000]
    if repair:
        repair_diagnostics = repair.get("diagnostics")
        if not isinstance(repair_diagnostics, list) or not repair_diagnostics:
            repair_diagnostics = diagnostics
        changes = [
            change for change in (
                _flat_change(item, repair=True)
                for item in list(repair_diagnostics)[:MAX_DIAGNOSTICS]
            ) if change is not None
        ]
        return {
            "ok": False, "action": "repair_patch_only", "retryable": True,
            "state_mutated": False, "error_code": code, "error": message,
            "repair_capsule": str(repair.get("repair_capsule") or ""),
            "base_payload_digest": str(repair.get("base_payload_digest") or ""),
            "repair_changes": changes,
        }
    changes = [
        change for change in (
            _flat_change(item) for item in list(diagnostics)[:MAX_DIAGNOSTICS]
        ) if change is not None
    ]
    return {
        "ok": False,
        "action": (
            "server_recovery"
            if code in {
                "internal_worker_question_forbidden",
                "native_completion_observation_unavailable",
                "worker_attestation_server_state_unavailable",
            } else
            "read_required_context_then_retry"
            if code == "attempt_read_receipts_incomplete" else
            # A native model mismatch cannot be repaired in the same child:
            # followup_task starts another turn in that already-created child
            # and therefore preserves the wrong native model.  This response
            # contains no server-issued replacement dispatch, so fail closed.
            "none"
            if code.startswith("native_subagent_model_") else
            "wait_for_bound_workers"
            if code == "native_completion_observation_required" else
            "retry_same_operation"
            if code in PUBLIC_UNCHANGED_RETRY_ERROR_CODES
            and retryable and not state_mutated else
            "retry_same_operation" if retryable and not state_mutated and changes else
            "inspect_or_retry" if retryable and not state_mutated else "none"
        ),
        "retryable": retryable and not state_mutated,
        "state_mutated": state_mutated,
        "error_code": code,
        "error": message,
        **({"allowed_changes": changes} if changes else {}),
    }


def _native_dispatches(value: object) -> list[dict[str, Any]]:
    """Project exact native calls instead of an ambiguous flat field bag.

    Spawn and same-child follow-up are distinct closed call shapes.  A
    follow-up target is accepted only when it equals the server-issued native
    task name stored beside the private host binding; a raw host thread/session
    identifier can therefore never become model-visible dispatch authority.
    """
    rows: list[dict[str, Any]] = []
    seen_dispatch_refs: set[str] = set()
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, Mapping):
            raise ResponseValidationError([{
                "code": "native_dispatch_invalid", "message": "native dispatch must be an object",
            }])
        call = str(raw.get("call") or "")
        arguments = raw.get("arguments")
        if call not in {"spawn_agent", "followup_task"} or not isinstance(arguments, Mapping):
            raise ResponseValidationError([{
                "code": "native_dispatch_invalid", "message": "native dispatch call or arguments are invalid",
            }])
        if call == "spawn_agent":
            if set(arguments) - {"task_name", "message", "fork_turns", "reasoning_effort", "model"}:
                raise ResponseValidationError([{
                    "code": "native_dispatch_invalid", "message": "spawn_agent contains unsupported arguments",
                }])
            native_arguments = {
                "task_name": str(arguments.get("task_name") or ""),
                "message": str(arguments.get("message") or ""),
                "fork_turns": str(arguments.get("fork_turns") or ""),
                "reasoning_effort": str(arguments.get("reasoning_effort") or ""),
            }
            if not all(native_arguments.values()) or native_arguments["fork_turns"] != "none":
                raise ResponseValidationError([{
                    "code": "native_dispatch_invalid", "message": "spawn_agent arguments are incomplete",
                }])
            if arguments.get("model") not in {None, ""}:
                native_arguments["model"] = str(arguments["model"])
        else:
            if set(arguments) != {"target", "message"}:
                raise ResponseValidationError([{
                    "code": "native_dispatch_invalid", "message": "followup_task requires only target and message",
                }])
            target = str(arguments.get("target") or "")
            task_name = str(raw.get("host_task_name") or "")
            message = str(arguments.get("message") or "")
            if not task_name or target != task_name or not message:
                raise ResponseValidationError([{
                    "code": "native_dispatch_invalid",
                    "message": "followup_task target must equal the server-issued native task name",
                }])
            native_arguments = {"target": task_name, "message": message}
        dispatch_ref = str(raw.get("dispatch_ref") or "")
        if not dispatch_ref:
            raise ResponseValidationError([{
                "code": "native_dispatch_invalid", "message": "native dispatch_ref is required",
            }])
        if dispatch_ref in seen_dispatch_refs:
            raise ResponseValidationError([{
                "code": "native_dispatch_invalid",
                "message": "native dispatch_ref must be unique within one dispatch response",
            }])
        seen_dispatch_refs.add(dispatch_ref)
        try:
            native_arguments["message"] = native_dispatch_authority_message(
                dispatch_ref, str(native_arguments.get("message") or ""),
            )
        except ValueError as exc:
            raise ResponseValidationError([{
                "code": "native_dispatch_invalid", "message": str(exc),
            }]) from exc
        row = {
            "dispatch_ref": dispatch_ref,
            "call": call,
            "arguments": native_arguments,
            **_profile_resolution_projection(raw),
        }
        rows.append(row)
    if len(rows) > 8:
        raise ResponseValidationError([{
            "code": "dispatch_bound_exceeded",
            "message": "one public dispatch wave may contain no more than eight workers",
        }])
    return rows


_PUBLIC_SCHEMA_FIELDS = frozenset({
    "type", "const", "enum", "pattern", "format", "minLength", "maxLength",
    "minItems", "maxItems", "minProperties", "maxProperties", "minimum",
    "maximum", "uniqueItems", "additionalProperties", "required", "properties", "items",
})
_PUBLIC_QUESTION_FIELDS = frozenset({
    "question_ref", "question_text",
})
_PUBLIC_DECISION_FIELDS = frozenset({"request_id", "plan_result_ref", "plan_digest", "choices"})
_PUBLIC_HANDOFF_FIELDS = frozenset({
    "ref", "digest", "close_verified", "handoff_created", "status", "summary",
    "report", "completed", "next_action", "decisions", "risks",
})
_PUBLIC_RESULT_FIELDS = frozenset({
    "status", "summary", "report", "content", "findings", "decisions_needed",
    "unresolved", "claims", "severity", "title", "text", "type", "verification",
    "observations", "events", "recommendation", "rationale", "result", "results",
})
_PUBLIC_GOVERNANCE_FIELDS = frozenset({
    "status", "summary", "report", "content", "outcome", "decision", "decisions",
    "records", "record", "evidence", "findings", "requirements", "risks",
    "recommendation", "rationale", "title", "text", "type", "digest", "receipt_ref",
    "approved", "complete", "items", "results", "initiative", "initiatives", "snapshot",
    "initiative_ref", "parent_ref", "children", "task_links", "dependencies",
    "dependency", "link", "relationship", "source_type", "source_ref",
    "target_type", "target_ref", "dependency_type", "milestone", "deliverable",
    "corrective", "expected_revision", "revision", "record_ref", "record_type",
    "supersedes", "expires_at", "created_at", "goal", "proposals",
    "acceptance_oracle_artifact_ref", "content_artifact_ref", "content_digest",
})
_PUBLIC_INSPECTION_FIELDS = frozenset({
    "status", "summary", "report", "content", "outcome", "state", "phase", "step",
    "complete", "question", "recommendation", "choices", "items", "results",
    "findings", "decisions", "risks", "title", "text", "type", "digest", "receipt_ref",
    "artifact_ref", "name", "mime_type", "size", "kind",
})
_PRIVATE_SEMANTIC_KEY_PARTS = (
    "task_id", "attempt_id", "assignment", "coordinator", "dispatch", "capability",
    "session", "lane", "resource", "principal", "worker_id", "agent_id", "event_key",
    "path", "file", "directory", "project_root", "ledger", "database", "host",
)


def _project_semantic(value: object, allowed_fields: frozenset[str]) -> object:
    """Project a structured semantic value before it can become public text."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_project_semantic(item, allowed_fields) for item in value]
    if not isinstance(value, Mapping):
        raise TypeError("public semantic values must be JSON data")
    projected: dict[str, object] = {}
    for raw_key, item in value.items():
        key = str(raw_key)
        lowered = key.lower()
        if key not in allowed_fields or any(part in lowered for part in _PRIVATE_SEMANTIC_KEY_PARTS):
            continue
        projected[key] = _project_semantic(item, allowed_fields)
    return projected


def project_public_governance_semantic(value: object) -> object:
    """Return the JSON-ready governance projection used before public paging."""
    return _project_semantic(value, _PUBLIC_GOVERNANCE_FIELDS)


def _flat_report(value: object, allowed_fields: frozenset[str]) -> str:
    if isinstance(value, str):
        return value
    projected = _project_semantic(value, allowed_fields)
    return json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _durable_handoff_report(source: Mapping[str, Any], kind: str) -> str | None:
    """Return only an explicit verified durable handoff semantic receipt."""
    if kind != "deliver_handoff":
        return None
    candidate = source.get("handoff_receipt")
    if not isinstance(candidate, Mapping):
        candidate = source.get("handoff")
    if not isinstance(candidate, Mapping) or candidate.get("close_verified") is not True:
        return None
    has_durable_receipt = (
        candidate.get("handoff_created") is True
        or (
            re.fullmatch(r"handoff-[A-Za-z0-9._:-]{1,160}", str(candidate.get("ref") or "")) is not None
            and re.fullmatch(r"sha256:[0-9a-f]{64}", str(candidate.get("digest") or "")) is not None
        )
    )
    if not has_durable_receipt:
        return None
    report = _flat_report(candidate, _PUBLIC_HANDOFF_FIELDS)
    return report if report not in {"", "{}", "[]"} else None


def _flat_success(tool: str, source: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    task_ref = _public_task_ref(source, arguments)
    requested_action = str(arguments.get("action") or "")
    if tool in {"start_orchestration", "continue_orchestration", "manage_orchestration"}:
        raw_dispatches = source.get("dispatches")
        if requested_action == "question_answer" and isinstance(source.get("resume"), Mapping):
            if raw_dispatches not in (None, (), []):
                raise ResponseValidationError([{
                    "code": "native_dispatch_invalid",
                    "message": "question resume cannot carry two dispatch sources",
                }])
            resume = source["resume"]
            task_name = str(resume.get("task_name") or "")
            raw_dispatches = [{
                "dispatch_ref": str(resume.get("dispatch_ref") or ""),
                "call": "followup_task",
                "host_task_name": task_name,
                "arguments": {
                    "target": task_name,
                    "message": str(resume.get("message") or ""),
                },
            }]
        dispatches = _native_dispatches(raw_dispatches)
        nested_action = source.get("action") if isinstance(source.get("action"), Mapping) else {}
        kind = str(nested_action.get("kind") or source.get("action") or "")
        outcome = str(source.get("outcome") or source.get("state") or "")
        local_reads = {
            "inspect", "recover_inspect", "read_lifecycle_page", "question_show", "plan_prompt",
            "artifact_list", "artifact_metadata", "artifact_read", "lane_inspect", "lane_reconcile",
        }
        if requested_action == "question_answer" and outcome == "question_answered":
            action = "resume_bound_worker" if dispatches else "none"
        elif dispatches:
            action = "invoke_dispatches"
        elif kind == "read_worker_wave" or outcome == "completion_pending":
            action = "read_worker_wave"
        elif kind == "wait_for_bound_workers" or outcome == "waiting":
            action = "wait_for_bound_workers"
        elif kind == "resume_orchestration" or outcome == "resume_required":
            action = "resume_orchestration"
        elif kind == "inspect_orchestration" or outcome == "context_inspection_required":
            action = "inspect_orchestration"
        elif kind == "inspect_orchestration_recovery":
            action = "inspect_orchestration_recovery"
        elif kind == "terminal_continue":
            action = "terminal_continue"
        elif kind == "obtain_user_decision" or outcome == "awaiting_user":
            action = "obtain_user_decision"
        elif kind == "obtain_plan_approval" or outcome in {"plan_approval", "awaiting_plan_approval"}:
            action = "obtain_plan_approval"
        elif kind == "deliver_handoff" and requested_action in local_reads:
            return _flat_failure(tool, {
                "code": "local_action_projection_unavailable", "retryable": False,
                "message": "The requested local read returned a terminal lifecycle envelope instead of its local result.",
            })
        elif kind == "deliver_handoff":
            action = "deliver_handoff" if _durable_handoff_report(source, kind) is not None else "none"
        elif requested_action in {"inspect", "recover_inspect", "read_lifecycle_page", "question_show", "plan_prompt", "artifact_list", "artifact_metadata", "artifact_read", "lane_inspect", "lane_reconcile"}:
            action = (
                "read_more" if source.get("next_cursor") else
                "continue" if kind == "continue" else
                "resume_orchestration" if kind == "resume_orchestration" else
                "inspect_orchestration" if kind == "inspect_orchestration" else
                "none"
            )
        elif requested_action == "question_answer":
            # Recording an answer does not complete the paused native worker.
            # Expose the required host transition directly so the coordinator
            # cannot mistake a durable answer receipt for wave completion.
            action = (
                "wait_for_bound_workers"
                if outcome == "question_resume_pending"
                else "none"
            )
        else:
            action = "none"
        default_state_mutated = (
            outcome != "question_resume_pending"
            and (
                tool in {"start_orchestration", "continue_orchestration"}
                or requested_action not in {"inspect", "recover_inspect", "question_show", "plan_prompt", "artifact_list", "artifact_metadata", "artifact_read", "lane_inspect", "lane_reconcile"}
            )
        )
        response = _flat_response_base(
            action,
            state_mutated=(
                bool(source.get("state_mutated"))
                if isinstance(source.get("state_mutated"), bool)
                else default_state_mutated
            ),
        )
        if task_ref:
            response["task_ref"] = task_ref
        coordinator_ref = source.get("coordinator_ref")
        if tool == "start_orchestration" and isinstance(coordinator_ref, str) and coordinator_ref:
            response["coordinator_ref"] = coordinator_ref
        step = source.get("step")
        if isinstance(step, int) and not isinstance(step, bool) and step >= 1:
            response["step"] = step
        if dispatches:
            response["dispatches"] = dispatches
        if source.get("compiled_plan") not in {None, ""}:
            response["compiled_plan"] = str(source["compiled_plan"])
        question = source.get("question")
        if isinstance(question, Mapping):
            if question.get("question_ref"):
                response["question_ref"] = str(question["question_ref"])
            response["content"] = _flat_report(question, _PUBLIC_QUESTION_FIELDS)
        decision = source.get("decision")
        if isinstance(decision, Mapping):
            if decision.get("request_id"):
                response["request_id"] = str(decision["request_id"])
            choices = decision.get("choices")
            if isinstance(choices, list):
                response["choices"] = [str(item) for item in choices]
            response["content"] = _flat_report(decision, _PUBLIC_DECISION_FIELDS)
        for field in ("content", "report", "next_cursor", "question_ref", "request_id"):
            if source.get(field) not in {None, ""}:
                if field in {"content", "report"} and isinstance(source[field], (Mapping, list, tuple)):
                    response[field] = _flat_report(source[field], _PUBLIC_INSPECTION_FIELDS)
                else:
                    response[field] = str(source[field])
        if isinstance(source.get("complete"), bool):
            response["complete"] = source["complete"]
        if requested_action == "question_answer" and action == "resume_bound_worker":
            question_ref = str(source.get("question_ref") or "")
            response["content"] = (
                "Call the returned followup_task exactly once for the same server-bound native child for durable "
                f"question {question_ref}. Then invoke wait_agent for that exact bound child and call "
                "read_worker_wave only after wait_agent reports it terminal. Cortex retains authorization "
                "server-side; do not call continue_orchestration first "
                "and do not spawn a replacement."
            )
        elif requested_action == "question_answer" and action == "wait_for_bound_workers":
            response["content"] = (
                "The same-child durable-answer follow-up instruction was already issued. Invoke wait_agent for "
                "that exact bound child; do not send another followup_task. Call read_worker_wave only after "
                "wait_agent reports it terminal. Cortex retains authorization server-side."
            )
        if requested_action == "artifact_list" and isinstance(source.get("artifacts"), list):
            response["report"] = _flat_report(source["artifacts"], _PUBLIC_INSPECTION_FIELDS)
        if requested_action == "artifact_read":
            if source.get("content_part") is not None:
                response["content"] = str(source.get("content_part") or "")
            elif source.get("content_base64") is not None:
                response["content"] = str(source.get("content_base64") or "")
        handoff_report = _durable_handoff_report(source, kind)
        if action == "deliver_handoff" and handoff_report is not None:
            response["report"] = handoff_report
        elif outcome == "completed" and requested_action not in local_reads:
            return _flat_failure(tool, {
                "code": "durable_handoff_unavailable", "retryable": False,
                "message": "The completed state has no explicit verified durable handoff receipt.",
            })
        elif not any(field in response for field in ("content", "report")) and requested_action in local_reads:
            semantic = source.get("result")
            if semantic not in (None, {}, []):
                response["report"] = _flat_report(semantic, _PUBLIC_INSPECTION_FIELDS)
        return response
    if tool == "manage_governance":
        response = _flat_response_base(
            "read_more" if source.get("next_cursor") else "none",
            state_mutated=requested_action not in {"inspect_initiative", "list_records", "snapshot", "promotion_inspect"},
        )
        report = source.get("report") if source.get("report") is not None else source.get("result")
        if report not in (None, {}, []):
            response["report"] = _flat_report(report, _PUBLIC_GOVERNANCE_FIELDS)
        if source.get("next_cursor"):
            response["next_cursor"] = str(source["next_cursor"])
        return response
    if tool == "read_dispatch_briefing":
        if not isinstance(source.get("complete"), bool):
            raise ValueError("dispatch briefing reads require an explicit boolean complete marker")
        response = _flat_response_base("read_more" if source.get("next_cursor") else "none")
        response["content"] = str(source.get("content") or "")
        response["complete"] = source["complete"]
        if source.get("next_cursor"):
            response["next_cursor"] = str(source["next_cursor"])
        return response
    if tool == "refresh_worker_context":
        if not isinstance(source.get("complete"), bool):
            raise ValueError("worker context reads require an explicit boolean complete marker")
        response = _flat_response_base(str(source.get("action") or (
            "read_more" if source.get("next_cursor") else "use_result_as_context"
        )))
        for field in ("dispatch_ref", "frontier_ref", "catalog_ref"):
            value = source.get(field)
            if isinstance(value, str) and value:
                response[field] = value
        context = source.get("content") if source.get("content") is not None else source.get("report")
        if context not in (None, "", {}, []):
            response["content"] = _flat_report(context, _PUBLIC_RESULT_FIELDS)
        response["complete"] = source["complete"]
        if source.get("question_ref"):
            response["question_ref"] = str(source["question_ref"])
        if source.get("question_status") in {"open", "answered"}:
            response["question_status"] = str(source["question_status"])
        if source.get("next_cursor"):
            response["next_cursor"] = str(source["next_cursor"])
        return response
    if tool in {"record_attempt_event", "record_worker_finding"}:
        response = _flat_response_base(
            "none",
            state_mutated=not bool(source.get("idempotent")),
        )
        if source.get("receipt_ref") not in {None, ""}:
            response["receipt_ref"] = str(source["receipt_ref"])
        if source.get("digest") not in {None, ""}:
            response["digest"] = str(source["digest"])
        return response
    if tool == "complete_attempt":
        return {**_flat_response_base("none", state_mutated=True), "terminal": True}
    if tool == "worker_question":
        outcome = str(source.get("outcome") or "")
        response = _flat_response_base(
            "read_more" if source.get("next_cursor") else (
                "use_result_as_context" if "answered" in outcome else "obtain_user_decision"
            ),
            state_mutated=outcome in {"question_recorded", "question_superseded"},
        )
        if source.get("question_ref"):
            response["question_ref"] = str(source["question_ref"])
        answer = source.get("answer")
        if answer is not None and "content" not in source:
            response["content"] = _flat_report(answer, _PUBLIC_RESULT_FIELDS)
        for field in ("content", "report", "next_cursor"):
            if source.get(field) not in {None, ""}:
                if field in {"content", "report"} and isinstance(source[field], (Mapping, list, tuple)):
                    response[field] = _flat_report(source[field], _PUBLIC_RESULT_FIELDS)
                else:
                    response[field] = str(source[field])
        return response
    # read_worker_result
    source_action = str(source.get("action") or "")
    response = _flat_response_base(
        source_action if source_action in {
            "read_more", "continue", "resume_orchestration", "inspect_orchestration", "terminal_continue", "revise_or_continue", "append_rework_wave",
            "use_result_as_context",
            "resume_bound_worker", "obtain_user_decision", "invoke_dispatches", "wait_for_bound_workers",
        } else ("read_more" if source.get("next_cursor") else "none"),
        state_mutated=bool(source.get("state_mutated")),
    )
    if task_ref:
        response["task_ref"] = task_ref
    dispatches = _native_dispatches(source.get("dispatches"))
    if dispatches:
        response["dispatches"] = dispatches
    if source_action in {
        "resume_bound_worker", "obtain_user_decision", "invoke_dispatches", "revise_or_continue",
        "append_rework_wave", "continue", "resume_orchestration",
        "wait_for_bound_workers",
    }:
        content = source.get("content")
        if content not in {None, ""}:
            response["content"] = str(content)
        if source.get("question_ref") not in {None, ""}:
            response["question_ref"] = str(source["question_ref"])
    requested_action = str(arguments.get("action") or "")
    if requested_action in {"list_reports", "read_predecessor"}:
        if not isinstance(source.get("complete"), bool):
            raise ValueError("worker report reads require an explicit boolean complete marker")
        response["content"] = str(source.get("content") or "")
        response["complete"] = source["complete"]
        if source.get("next_cursor"):
            response["next_cursor"] = str(source["next_cursor"])
        return response
    report = source.get("report")
    if report is None:
        report = None if "content" in response else source.get("content")
    if report is None:
        report = source.get("result_view")
    if report is None and isinstance(source.get("results"), list):
        report = source.get("results")
    if report is None and isinstance(source.get("result_views"), list):
        report = source.get("result_views")
    if report not in (None, {}, []):
        response["report"] = _flat_report(report, _PUBLIC_RESULT_FIELDS)
    if source.get("next_cursor"):
        response["next_cursor"] = str(source["next_cursor"])
    if isinstance(source.get("complete"), bool):
        response["complete"] = source["complete"]
    result_refs = source.get("result_refs")
    if isinstance(result_refs, list):
        response["result_refs"] = [str(item) for item in result_refs]
    source_result_ref = source.get("source_result_ref")
    if source_result_ref not in {None, ""}:
        response["source_result_ref"] = str(source_result_ref)
    step = source.get("step")
    if isinstance(step, int) and not isinstance(step, bool) and step >= 1:
        response["step"] = step
    return response


def _validate_flat_public_response(value: Mapping[str, Any]) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    allowed = _FLAT_PUBLIC_COMMON_FIELDS | _FLAT_PUBLIC_SUCCESS_FIELDS | _FLAT_PUBLIC_FAILURE_FIELDS
    unknown = sorted(set(value) - allowed)
    if unknown:
        diagnostics.append({"code": "response_schema_invalid", "message": "unsupported flat response fields: " + ", ".join(unknown)})
    for field, expected in (("ok", bool), ("action", str), ("retryable", bool), ("state_mutated", bool)):
        if not isinstance(value.get(field), expected):
            diagnostics.append({"code": "response_schema_invalid", "message": f"{field} has an invalid type"})
    if "complete" in value and not isinstance(value.get("complete"), bool):
        diagnostics.append({"code": "response_schema_invalid", "message": "complete has an invalid type"})
    if isinstance(value.get("action"), str) and value["action"] not in PUBLIC_ACTIONS:
        diagnostics.append({"code": "response_schema_invalid", "message": "action is outside the canonical public action enum"})
    if value.get("ok") is False:
        if value.get("action") not in PUBLIC_FAILURE_ACTIONS:
            diagnostics.append({"code": "response_schema_invalid", "message": "failure action is outside the canonical failure enum"})
        for field in ("error_code", "error"):
            if not isinstance(value.get(field), str) or not value[field]:
                diagnostics.append({"code": "response_schema_invalid", "message": f"failure requires {field}"})
        if value.get("action") == "repair_patch_only":
            for field in ("repair_capsule", "base_payload_digest", "repair_changes"):
                if field not in value:
                    diagnostics.append({"code": "response_schema_invalid", "message": f"repair requires {field}"})
    elif value.get("ok") is True and value.get("action") not in PUBLIC_SUCCESS_ACTIONS:
        diagnostics.append({"code": "response_schema_invalid", "message": "success action is outside the canonical success enum"})
    if diagnostics:
        raise ResponseValidationError(diagnostics)
    return validate_response("public.flat", value)


def project_public_response(
    tool: str,
    value: object,
    *,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one private v11 value to the uniform hard-cut flat envelope."""
    if tool not in _PUBLIC_RESPONSE_FAMILIES or not isinstance(value, Mapping):
        raise ResponseValidationError([{
            "code": "response_schema_invalid",
            "message": "public handler returned an unsupported response",
        }])
    allowed_flat_fields = (
        _FLAT_PUBLIC_COMMON_FIELDS | _FLAT_PUBLIC_SUCCESS_FIELDS | _FLAT_PUBLIC_FAILURE_FIELDS
    )
    if _FLAT_PUBLIC_COMMON_FIELDS.issubset(value) and set(value).issubset(allowed_flat_fields):
        return _validate_flat_public_response(_attach_native_transition(value))
    source = dict(value)
    if not source.get("ok"):
        return _validate_flat_public_response(_attach_native_transition(_flat_failure(tool, source)))
    return _validate_flat_public_response(_attach_native_transition(_flat_success(tool, source, arguments)))


def _safe_public_response(
    tool: str,
    value: object,
    *,
    arguments: Mapping[str, Any],
    supplied_coordinator_refs: frozenset[str],
) -> dict[str, Any]:
    """Project, scrub, and validate the single flat response contract."""
    try:
        projected = project_public_response(tool, value, arguments=arguments)
    except (ResponseValidationError, ValueError, TypeError, KeyError):
        projected = _public_internal_failure(tool, arguments)
    scrubbed = _scrub_public_response(
        projected,
        allow_coordinator_ref=tool == "start_orchestration",
        allow_worker_context_refs=tool == "refresh_worker_context",
        supplied_coordinator_refs=supplied_coordinator_refs,
    )
    try:
        return _validate_flat_public_response(scrubbed if isinstance(scrubbed, Mapping) else {})
    except ResponseValidationError:
        fallback = _public_internal_failure(tool, arguments)
        scrubbed_fallback = _scrub_public_response(
            fallback,
            allow_coordinator_ref=False,
            allow_worker_context_refs=False,
            supplied_coordinator_refs=supplied_coordinator_refs,
        )
        return _validate_flat_public_response(
            scrubbed_fallback if isinstance(scrubbed_fallback, Mapping) else {},
        )


def configure_internal_schemas(tools: dict[str, tuple[Callable[..., Any], dict[str, Any]]]) -> set[str]:
    """Apply authorization requirements to internal handlers before projection."""
    authorized = {
        "init_task", "get_task_status", "finalize_attempt", "record_evidence", "execute_verification_command",
        "cortex.question", "publish_worker_question", "list_worker_questions", "answer_worker_question", "get_worker_question_updates",
        "commit_gate", "update_pipeline", "reassess_pipeline", "acquire_lock", "release_lock",
        "create_handoff", "claim_resource", "release_resource",
        "create_lane", "get_lane_status", "claim_lane", "release_lane", "retire_lane", "bind_task_lane",
        "claim_lane_resource", "release_lane_resource", "materialize_lane", "reconcile_lane",
    }
    for name in authorized:
        schema = tools[name][1]
        schema.setdefault("properties", {}).setdefault("principal", {"type": "string", "minLength": 1})
        if "principal" not in schema.setdefault("required", []):
            schema["required"].append("principal")
    # Workspace selection is host-owned: MCP tools resolve the current
    # thread through the validated SessionStart binding. Never add the
    # historical project_root field to model-facing or internal schemas.
    for name, fields in {
        "claim_resource": ["expires_at"], "claim_lane": ["expires_at"], "claim_lane_resource": ["expires_at"],
        "create_handoff": ["completed", "next_action"], "retire_lane": ["confirm"],
    }.items():
        for field in fields:
            if field not in tools[name][1]["required"]:
                tools[name][1]["required"].append(field)
    tools["retire_lane"][1]["properties"]["confirm"] = {"type": "boolean"}
    return authorized


def public_tools(
    internal_handlers: Mapping[str, tuple[Callable[..., Any], dict[str, Any]]],
    *,
    contracts: Mapping[str, Mapping[str, Any]],
    worker_question: Callable[..., Any],
    record_attempt_event: Callable[..., Any],
    record_worker_finding: Callable[..., Any],
    complete_attempt: Callable[..., Any],
    read_dispatch_briefing: Callable[..., Any],
    read_worker_result: Callable[..., Any],
    manage_governance: Callable[..., Any],
    read_worker_context: Callable[..., Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Bind each canonical model contract to its private backend operation."""
    operations: dict[str, Callable[..., Any]] = {
        "start_orchestration": internal_handlers["start_orchestration"][0],
        "continue_orchestration": internal_handlers["continue_orchestration"][0],
        "manage_orchestration": internal_handlers["manage_orchestration"][0],
        "manage_governance": manage_governance,
        "worker_question": worker_question,
        "record_attempt_event": record_attempt_event,
        "record_worker_finding": record_worker_finding,
        "complete_attempt": complete_attempt,
        "read_dispatch_briefing": read_dispatch_briefing,
        "read_worker_result": read_worker_result,
        "read_worker_context": read_worker_context,
    }
    bound: dict[str, dict[str, Any]] = {}
    for name, contract in contracts.items():
        base_operation = str(contract.get("base_operation") or "")
        handler = operations.get(base_operation)
        schema = contract.get("inputSchema")
        if handler is None or not isinstance(schema, dict):
            raise ValueError(f"invalid public contract binding for {name}")
        bound[str(name)] = {**dict(contract), "handler": handler}
    return bound


def public_tools_for_audience(
    all_public_tools: Mapping[str, Mapping[str, Any]],
    audience: str,
) -> dict[str, dict[str, Any]]:
    """Project the public registry for one launch-time MCP audience.

    ``audience`` is intentionally not accepted from JSON-RPC initialization or
    individual tool arguments: those values are controlled by the caller and
    cannot establish a privilege boundary.  The host selects it before the
    process starts.  Unknown/missing audiences use the default fresh union;
    hosts that need role separation select ``worker`` or ``coordinator``.
    """
    selected = str(audience or "").strip().lower()
    if selected not in MCP_AUDIENCES:
        selected = DEFAULT_MCP_AUDIENCE
    if selected == DEFAULT_MCP_AUDIENCE:
        return {str(name): dict(value) for name, value in all_public_tools.items()}
    return {
        str(name): dict(value)
        for name, value in all_public_tools.items()
        if value.get("audience") == selected
    }


def serve_stdio(
    *,
    public_tools: Mapping[str, Mapping[str, Any]],
    internal_handlers: Mapping[str, tuple[Callable[..., Any], dict[str, Any]]],
    server_version: str,
    instructions: str,
    log_tool_error: Callable[[object, object, str, Exception], None],
    transport_metadata_audit: Callable[[str, Mapping[str, Any], str | None, bool], None] | None = None,
    audience: str = DEFAULT_MCP_AUDIENCE,
) -> None:
    """Run the narrow JSON-RPC transport without importing orchestration internals.

    The selected tool mapping is fixed for the process lifetime.  This is the
    strongest boundary available to a plain stdio transport: it cannot trust a
    role supplied by the client after the process has started.
    """
    normalized_audience = str(audience or "").strip().lower()
    if normalized_audience not in MCP_AUDIENCES:
        normalized_audience = DEFAULT_MCP_AUDIENCE
    # ``serve_stdio`` is also imported by source-mode tests and embedding
    # hosts.  Enforce the projection here rather than relying exclusively on
    # the CLI entry point to pass an already-filtered mapping.
    all_public_names = frozenset(public_tools)
    public_tools = public_tools_for_audience(public_tools, normalized_audience)
    # JSON-RPC request ids are scoped to one MCP connection.  Include a fresh
    # connection nonce before handing the id to lifecycle code so a new Codex
    # thread that starts a fresh MCP process can never replay a prior thread's
    # numeric id.  A repeated id on this same transport remains a stable
    # request identity for server-side idempotency.
    transport_connection_nonce = secrets.token_hex(16)
    def tool_result(name: object, value: Mapping[str, Any]) -> dict[str, Any]:
        """Build one MCP result from the already-validated flat contract."""
        del name
        failed = value.get("ok") is False
        text_value = public_response_text(value)
        return {
            "content": [{"type": "text", "text": text_value}],
            "structuredContent": dict(value),
            "isError": failed,
        }

    def write_message(value: Mapping[str, Any]) -> None:
        sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def write_error(request_id: object, code: int, message: str) -> None:
        write_message({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        })

    def request_params(request: Mapping[str, Any], *, required: bool = False) -> dict[str, Any]:
        if "params" not in request:
            if required:
                raise _JsonRpcInvalidParams
            return {}
        params = request["params"]
        if not isinstance(params, dict):
            raise _JsonRpcInvalidParams
        return params

    def complete_tools_page_params(request: Mapping[str, Any]) -> None:
        params = request_params(request)
        if set(params) - {"cursor", "_meta"}:
            raise _JsonRpcInvalidParams
        if "_meta" in params and not isinstance(params["_meta"], Mapping):
            raise _JsonRpcInvalidParams
        cursor = params.get("cursor")
        if cursor is not None and cursor != "":
            raise _JsonRpcInvalidParams

    connection_state = _LEGACY_CONNECTION_NEW
    rate_tokens = _TOOLS_CALL_RATE_CAPACITY
    rate_updated_at = time.monotonic()

    def consume_tools_call() -> int | None:
        """Consume one private connection token or return a bounded delay."""
        nonlocal rate_tokens, rate_updated_at
        now = time.monotonic()
        elapsed = max(0.0, now - rate_updated_at)
        rate_tokens = min(
            _TOOLS_CALL_RATE_CAPACITY,
            rate_tokens + elapsed * _TOOLS_CALL_RATE_REFILL_PER_SECOND,
        )
        rate_updated_at = now
        if rate_tokens >= 1.0:
            rate_tokens -= 1.0
            return None
        missing = 1.0 - rate_tokens
        return max(1, min(math.ceil(1_000 * missing / _TOOLS_CALL_RATE_REFILL_PER_SECOND), 60_000))

    while True:
        line = sys.stdin.readline()
        if not line:
            return
        request: object = None
        request_id: object = None
        has_request_id = False
        request_id_established = False
        try:
            try:
                request = json.loads(
                    line,
                    parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON number")),
                )
            except (json.JSONDecodeError, ValueError):
                write_error(None, -32700, "Parse error")
                continue

            if not isinstance(request, dict):
                raise _JsonRpcInvalidRequest
            has_request_id = "id" in request
            request_id = request.get("id")
            if has_request_id:
                if not (
                    isinstance(request_id, str)
                    or (
                        isinstance(request_id, int)
                        and not isinstance(request_id, bool)
                        and _JSONRPC_I64_MIN <= request_id <= _JSONRPC_I64_MAX
                    )
                ):
                    raise _JsonRpcInvalidRequest
                request_id_established = True
            if request.get("jsonrpc") != "2.0":
                raise _JsonRpcInvalidRequest
            method = request.get("method")
            if not isinstance(method, str) or not method:
                raise _JsonRpcInvalidRequest

            # Resolve method support before applying the connection state
            # machine.  An unknown method is always -32601; lifecycle-order
            # errors apply only to methods this transport actually knows.
            if method not in _LEGACY_METHODS:
                raise _JsonRpcMethodNotFound

            # MCP 2025-06-18 is connection-oriented. Ping is the sole method
            # allowed without advancing this initialization state machine.
            if method != "ping":
                if connection_state == _LEGACY_CONNECTION_NEW and method != "initialize":
                    if not has_request_id:
                        continue
                    raise _JsonRpcInvalidRequest
                if (
                    connection_state == _LEGACY_CONNECTION_INITIALIZE_RESPONSE_SENT
                    and method != "notifications/initialized"
                ):
                    if not has_request_id:
                        continue
                    raise _JsonRpcInvalidRequest
                if (
                    connection_state == _LEGACY_CONNECTION_READY
                    and method in {"initialize", "notifications/initialized"}
                ):
                    if not has_request_id:
                        continue
                    raise _JsonRpcInvalidRequest

            if method == "initialize":
                if not has_request_id:
                    continue
                params = request_params(request, required=True)
                requested_version = params.get("protocolVersion")
                if not isinstance(requested_version, str) or not requested_version.strip():
                    raise _JsonRpcInvalidParams
                capabilities = params.get("capabilities")
                client_info = params.get("clientInfo")
                if not isinstance(capabilities, dict) or not isinstance(client_info, dict):
                    raise _JsonRpcInvalidParams
                for key in ("name", "version"):
                    value = client_info.get(key)
                    if not isinstance(value, str) or not value.strip():
                        raise _JsonRpcInvalidParams
                if "title" in client_info and not isinstance(client_info["title"], str):
                    raise _JsonRpcInvalidParams
                if requested_version not in SUPPORTED_LEGACY_PROTOCOL_VERSIONS:
                    raise _JsonRpcInvalidParams
                selected_version = DEFAULT_LEGACY_PROTOCOL_VERSION
                result: dict[str, Any] = {
                    "protocolVersion": selected_version,
                    "capabilities": {"tools": {}, "resources": {"subscribe": False, "listChanged": False}},
                    "serverInfo": {"name": "cortex", "version": server_version},
                    "instructions": instructions,
                }
            elif method == "notifications/initialized":
                if has_request_id:
                    raise _JsonRpcMethodNotFound
                request_params(request)
                connection_state = _LEGACY_CONNECTION_READY
                continue
            elif method == "tools/list":
                if not has_request_id:
                    continue
                complete_tools_page_params(request)
                if len(public_tools) > _MAX_COMPLETE_TOOLS_PAGE:
                    raise RuntimeError("public tool catalog exceeds its bounded complete page")
                result = {"tools": [
                    {
                        "name": name,
                        "description": str(contract["description"]),
                        "inputSchema": contract["inputSchema"],
                    }
                    for name, contract in public_tools.items()
                ]}
            elif method == "resources/list":
                if not has_request_id:
                    continue
                request_params(request)
                result = {"resources": []}
            elif method == "resources/templates/list":
                if not has_request_id:
                    continue
                request_params(request)
                result = {"resourceTemplates": []}
            elif method == "tools/call":
                if not has_request_id:
                    continue
                params = request_params(request, required=True)
                name = params.get("name")
                if not isinstance(name, str) or not name:
                    raise _JsonRpcInvalidParams
                coordinator_refs = _supplied_coordinator_refs(request)
                arguments = params.get("arguments", {})
                if name not in public_tools:
                    if name in all_public_names:
                        value = _scrub_public_response(
                            _public_internal_failure(
                                name, arguments if isinstance(arguments, dict) else {},
                            ),
                            supplied_coordinator_refs=coordinator_refs,
                        )
                        result = tool_result(name, value)
                        # This is a structured routing receipt, not an
                        # unhandled tool exception.  Do not log the request:
                        # the receipt contains all actionable advice and the
                        # transport must not retain caller payloads here.
                        write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
                        continue
                    if name in internal_handlers:
                        raise _JsonRpcInvalidParams
                    raise _JsonRpcInvalidParams
                if not isinstance(arguments, dict):
                    value = _scrub_public_response(
                        _public_argument_shape_failure(name),
                        supplied_coordinator_refs=coordinator_refs,
                    )
                    result = tool_result(name, value)
                    write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
                    continue
                contract = public_tools[name]
                base_operation = str(contract["base_operation"])
                schema = contract["inputSchema"]
                if base_operation == "start_orchestration" and request_id is None:
                    raise _JsonRpcInvalidParams
                if (
                    name == "ask_worker_question"
                    and is_internal_question_category(arguments.get("question_category"))
                ):
                    result = tool_result(name, _scrub_public_response(
                        _public_internal_question_failure(),
                        supplied_coordinator_refs=coordinator_refs,
                    ))
                    write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
                    continue
                schema_failure = _public_schema_failure(schema, arguments)
                if schema_failure is not None:
                    result = tool_result(name, _scrub_public_response(
                        schema_failure, supplied_coordinator_refs=coordinator_refs,
                    ))
                    write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
                    continue
                host_thread_id = _call_host_thread_id(params)
                if transport_metadata_audit is not None:
                    try:
                        transport_metadata_audit(str(name), arguments, host_thread_id, host_thread_id is not None)
                    except (OSError, RuntimeError, TypeError, ValueError):
                        pass
                if host_thread_id is None:
                    result = tool_result(name, _scrub_public_response(
                        _public_host_identity_failure(),
                        supplied_coordinator_refs=coordinator_refs,
                    ))
                    write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
                    continue
                retry_after_ms = consume_tools_call()
                if retry_after_ms is not None:
                    value = _scrub_public_response(
                        _public_transient_retry_failure(
                            error_code="tool_rate_limited",
                            error="This connection exceeded the bounded Cortex tool-call burst.",
                            retry_after_ms=retry_after_ms,
                        ),
                        supplied_coordinator_refs=coordinator_refs,
                    )
                    result = tool_result(name, value)
                    write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
                    continue
                backend_arguments = {
                    **arguments,
                    **dict(contract.get("injected_arguments") or {}),
                }
                if base_operation == "start_orchestration":
                    backend_arguments["_transport_request_id"] = (
                        f"{transport_connection_nonce}:{_canonical_jsonrpc_request_id(request_id)}"
                    )
                host_thread_token: Token[str | None] = _HOST_THREAD_ID.set(host_thread_id)
                try:
                    try:
                        backend_value = contract["handler"](backend_arguments)
                    except Exception as exc:
                        if exc.__class__.__name__ != "LedgerBusyError":
                            raise
                        value = _scrub_public_response(
                            _public_transient_retry_failure(
                                error_code="ledger_busy",
                                error="Cortex storage is temporarily busy and the operation did not mutate state.",
                                retry_after_ms=_LEDGER_BUSY_RETRY_DELAY_MS,
                            ),
                            supplied_coordinator_refs=coordinator_refs,
                        )
                    else:
                        value = _safe_public_response(
                            name if name == "refresh_worker_context" else base_operation,
                            backend_value,
                            arguments=backend_arguments,
                            supplied_coordinator_refs=coordinator_refs,
                        )
                finally:
                    _HOST_THREAD_ID.reset(host_thread_token)
                result = tool_result(name, value)
            elif method == "ping":
                if not has_request_id:
                    continue
                request_params(request)
                result = {}
            else:
                raise _JsonRpcMethodNotFound
            write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
            if method == "initialize":
                connection_state = _LEGACY_CONNECTION_INITIALIZE_RESPONSE_SENT
        except _JsonRpcProtocolError as exc:
            if isinstance(exc, _JsonRpcInvalidRequest):
                write_error(
                    request_id if request_id_established else None,
                    exc.code,
                    exc.wire_message,
                )
            elif has_request_id:
                write_error(request_id, exc.code, exc.wire_message)
        except Exception as exc:
            is_ledger_busy = exc.__class__.__name__ == "LedgerBusyError"
            if not is_ledger_busy:
                # The configured logger records only bounded input shape and
                # redacted exception text; the wire response never reflects
                # private arguments, capabilities, or backend exception data.
                log_tool_error(request, request_id, line.rstrip("\n"), exc)
            if has_request_id:
                if is_ledger_busy:
                    error = {
                        "code": -32009,
                        "message": "Cortex is busy; retry the same operation without changing its input.",
                        "data": {
                            "schema": "cortex/ledger-busy/v1",
                            "code": "ledger_busy",
                            "retryable": True,
                            "retry_after_ms": 250,
                        },
                    }
                    write_message({"jsonrpc": "2.0", "id": request_id, "error": error})
                else:
                    write_error(request_id, -32603, "Internal error")
