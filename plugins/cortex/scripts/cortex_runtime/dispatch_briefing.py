"""Fresh-only immutable dispatch-briefing transport.

Worker completion is represented solely by ``AttemptResult`` and
``AttemptEvent``.  The editable draft transport has no
runtime implementation in v11.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path
from typing import Any

import cortex as _runtime
from cortex import (
    AWAITING_HOST_SPAWN,
    PUBLIC_ORCHESTRATION_SCHEMA,
    _attempt,
    _contained_path,
    _read_private_text,
    canonical_profile,
    load_state,
    redact,
    safe_id,
    select_project_root,
)
from cortex_runtime import attempt_protocol
from cortex_runtime.ledger_db import _governance_lifecycle_hmac_key
from cortex_runtime.pagination import decode_cursor, encode_cursor, scope_digest
from cortex_runtime.public_contracts import backend_schema_for
from cortex_runtime.validation import ValidationFailure
from cortex_runtime.v11_submission import json_pointer


# Briefings are model-facing MCP payloads.  Keep each response small enough
# that a normal assignment cannot consume a worker's context window before it
# has read the actual task.  Page size is entirely server-owned.
DEFAULT_DISPATCH_BRIEFING_PAGE_BYTES = 16 * 1024

def _dispatch_schema(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Materialize the private adapter view of the canonical public schema."""
    registry = getattr(_runtime, "PUBLIC_CONTRACTS", None)
    return backend_schema_for(registry, "read_dispatch_briefing", params) if isinstance(registry, dict) else {
        "type": "object", "additionalProperties": False, "properties": {}, "required": [],
    }


def _dispatch_diagnostic_defaults(item: dict[str, Any]) -> dict[str, Any]:
    """Return one small, non-echoing caller-correction diagnostic.

    A worker already owns the submitted value.  Reflecting it back can expose
    bearer-shaped inputs in tool transcripts and is not necessary to patch the
    request.  The JSON pointer and its field card are the complete repair
    surface for this operation.
    """
    source = dict(item)
    path = str(source.get("path") or "$")
    field = path.rsplit(".", 1)[-1]
    properties = _dispatch_schema().get("properties") or {}
    schema = properties.get(field, {"type": "object"}) if isinstance(properties, dict) else {"type": "object"}
    supplied_pointer = source.get("json_pointer")
    pointer = (
        str(supplied_pointer)
        if isinstance(supplied_pointer, str) and (
            supplied_pointer == "" or supplied_pointer.startswith("/")
        )
        else json_pointer(path)
    )
    diagnostic = {
        "code": str(source.get("code") or "dispatch_briefing_request_invalid"),
        "json_pointer": pointer,
        "message": redact(str(source.get("message") or "invalid value"), 300),
        "field_schema": dict(source.get("field_schema") or schema),
    }
    if field in {"dispatch_ref", "cursor"}:
        diagnostic["value_source"] = "cortex"
    return diagnostic


def _dispatch_preflight(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the direct adapter from the exact advertised contract."""
    schema = _dispatch_schema(params)
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    diagnostics: list[dict[str, Any]] = []
    for field in sorted(set(params) - set(properties)):
        pointer = "/" + field.replace("~", "~0").replace("/", "~1")
        diagnostics.append({
            "code": "dispatch_briefing_request_invalid",
            "path": f"$.{field}",
            "json_pointer": pointer,
            "message": "unsupported read_dispatch_briefing field",
            "field_schema": {"type": "object", "additionalProperties": False},
        })
    for field in schema.get("required", []) if isinstance(schema.get("required"), list) else []:
        value = params.get(field)
        if not isinstance(value, str) or not value.strip():
            diagnostics.append({
                "code": "dispatch_briefing_request_invalid", "path": f"$.{field}",
                "message": f"{field} is required",
            })
    for field, value in params.items():
        field_schema = properties.get(field)
        if not isinstance(field_schema, dict):
            continue
        if field_schema.get("type") == "string" and not isinstance(value, str):
            diagnostics.append({
                "code": "dispatch_briefing_request_invalid", "path": f"$.{field}",
                "message": f"{field} must be a string",
            })
            continue
        if isinstance(value, str):
            pattern = field_schema.get("pattern")
            if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
                diagnostics.append({
                    "code": "dispatch_briefing_request_invalid", "path": f"$.{field}",
                    "message": f"{field} has an invalid format",
                })
    return [_dispatch_diagnostic_defaults(item) for item in diagnostics]


def _dispatch_briefing_error_path(message: str) -> str:
    lowered = message.lower()
    for marker, path in (
        ("cursor", "cursor"),
        ("briefing_digest", "briefing_digest"), ("digest does not match", "briefing_digest"),
        ("dispatch_ref", "dispatch_ref"), ("attempt_id", "attempt_id"),
        ("task_id", "task_id"), ("profile", "profile"), ("project_root", "project_root"),
    ):
        if marker in lowered:
            return path
    return "$"


def _dispatch_briefing_failure(
    exc: BaseException,
    *,
    params: dict[str, Any] | None = None,
    recovery_path: Path | None = None,
) -> dict[str, Any]:
    """Return safe retryable argument diagnostics or an integrity blocker."""
    message = redact(str(exc), 1000)
    if isinstance(exc, _runtime.WorkerAssignmentError):
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": False, "outcome": "dispatch_unavailable",
            "code": "worker_dispatch_unavailable",
            "retryable": False,
        }
    lowered = message.lower()
    collected = getattr(exc, "diagnostics", None)
    caller_correctable = isinstance(exc, ValueError) and (
        isinstance(collected, list) and bool(collected)
    ) or (isinstance(exc, ValueError) and any(fragment in lowered for fragment in (
        "unsupported read_dispatch_briefing fields", "is required; copy the exact value",
        "profile must be an exact cortex worker profile", "profile does not match",
        "dispatch_ref does not match", "briefing_digest must be", "briefing_digest does not match",
        "briefing cursor",
    )))
    if caller_correctable:
        path = _dispatch_briefing_error_path(message)
        diagnostics = getattr(exc, "diagnostics", None)
        if not isinstance(diagnostics, list) or not diagnostics:
            diagnostics = [{"code": "dispatch_briefing_request_invalid", "path": path, "message": message}]
        else:
            diagnostics = [dict(item) for item in diagnostics if isinstance(item, dict)]
        diagnostics = [_dispatch_diagnostic_defaults(item) for item in diagnostics]
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": False, "outcome": "needs_correction",
            "code": "dispatch_briefing_request_invalid",
            "diagnostics": diagnostics,
            "retryable": True,
        }
    response: dict[str, Any] = {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": False, "outcome": "briefing_unavailable",
        "code": "dispatch_briefing_unavailable",
        "retryable": False,
    }
    if isinstance(exc, FileNotFoundError) and recovery_path is not None and recovery_path.is_absolute():
        response["recovery"] = {
            "kind": "read_exact_host_path_once",
            "path": str(recovery_path),
            "max_reads": 1,
        }
    return response


def _read_dispatch_briefing_impl(params: dict[str, Any]) -> dict[str, Any]:
    """Read exactly one active worker's immutable briefing."""
    briefing_path: Path | None = None
    try:
        original = dict(params)
        preflight = _dispatch_preflight(original)
        if preflight:
            raise ValidationFailure(preflight)
        project, task_dir, state, attempt, profile = _runtime.authorize_worker_assignment(
            original, "read_dispatch_briefing",
        )
        attempt_id = safe_id(str(attempt["attempt_id"]))
        dispatch_ref = safe_id(str(attempt["dispatch_ref"]))
        briefing_digest = str(attempt["briefing_digest"]).strip().lower()
        if attempt.get("invalidated") or attempt.get("status") not in {AWAITING_HOST_SPAWN, "running"} or not attempt.get("facade_managed"):
            raise ValueError("dispatch briefing reads require an active, non-invalidated public worker attempt")
        relative = str(attempt.get("briefing_file") or "").strip()
        relative_path = Path(relative)
        if not relative or relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
            raise ValueError("dispatch briefing path is outside its task scope")
        briefing_path = _contained_path(task_dir, task_dir / relative_path, "dispatch briefing")
        info = briefing_path.lstat()
        if not stat.S_ISREG(info.st_mode) or briefing_path.is_symlink():
            raise ValueError("dispatch briefing must remain a regular non-symlink file")
        if stat.S_IMODE(info.st_mode) & 0o222:
            raise ValueError("dispatch briefing lost immutable read-only permissions")
        briefing = _read_private_text(briefing_path, "dispatch briefing", max_bytes=None)
        if hashlib.sha256(briefing.encode("utf-8")).hexdigest() != briefing_digest:
            raise ValueError("immutable dispatch briefing digest changed after dispatch")
        root = _runtime._task_document_root(task_dir, state["task_id"])
        artifact_ref = str(attempt.get("briefing_artifact_ref") or "")
        artifact = _runtime.db_get_artifact_metadata(root, state["task_id"], artifact_ref) if artifact_ref else None
        if artifact is None:
            artifact = _runtime.db_get_artifact_for_export_path(root, state["task_id"], relative)
        if artifact is None or artifact.get("kind") != "dispatch_briefing" or artifact.get("digest_sha256") != briefing_digest:
            raise ValueError("dispatch briefing has no matching immutable artifact catalog entry")
        if _runtime.db_read_artifact_content(root, state["task_id"], artifact["artifact_ref"]) != briefing:
            raise ValueError("dispatch briefing export differs from its immutable artifact")
        audience = f"worker:{attempt_id}:{profile}"
        selector = "read_dispatch_briefing"
        cursor_binding = scope_digest({
            "task_id": state["task_id"], "artifact_ref": artifact["artifact_ref"],
            "digest_sha256": briefing_digest,
        })
        cursor_secret = _governance_lifecycle_hmac_key(root, create=False)
        byte_offset = 0
        has_cursor = "cursor" in params
        raw_cursor = params.get("cursor")
        if has_cursor:
            if not isinstance(raw_cursor, str) or not raw_cursor:
                raise ValueError("briefing cursor must be a non-empty opaque string returned by this server")
            byte_offset = decode_cursor(
                raw_cursor, cursor_secret, selector=selector,
                audience=audience, digest=cursor_binding,
            )
        base = {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "briefing_read"}
        part = _runtime.db_read_artifact_range(
            root,
            state["task_id"],
            artifact["artifact_ref"],
            byte_offset=byte_offset,
            max_bytes=DEFAULT_DISPATCH_BRIEFING_PAGE_BYTES,
        )
        result = {
            **base,
            "content": (
                part["content_base64"]
                if part.get("content_base64") is not None
                else part.get("content_part", "")
            ),
            "encoding": part["encoding"],
            "complete": bool(part["complete"]),
        }
        if part["next_byte_offset"] is not None:
            result["next_cursor"] = encode_cursor(
                cursor_secret, selector=selector, audience=audience,
                digest=cursor_binding, offset=part["next_byte_offset"],
            )
        if part["complete"]:
            attempt_protocol.acknowledge_briefing(root, task_id=state["task_id"], attempt_id=attempt_id, dispatch_ref=dispatch_ref, digest=briefing_digest)
        return result
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return _dispatch_briefing_failure(exc, params=params, recovery_path=briefing_path)


def read_dispatch_briefing(params: dict[str, Any]) -> dict[str, Any]:
    """Return only the closed immutable-briefing page contract."""
    from cortex_runtime.mcp_api import project_public_response
    return project_public_response(
        "read_dispatch_briefing", _read_dispatch_briefing_impl(params), arguments=params,
    )
