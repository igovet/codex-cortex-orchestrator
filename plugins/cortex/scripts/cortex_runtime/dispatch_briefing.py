"""Fresh-only immutable dispatch-briefing transport.

Worker completion is represented solely by ``AttemptResult`` and
``AttemptEvent``.  The editable draft transport has no
runtime implementation in v10.
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
    AGENTS,
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
from cortex_runtime.validation import ValidationFailure, collect_validations


_MISSING = object()

_DISPATCH_FIELD_SCHEMAS: dict[str, dict[str, Any]] = {
    "project_root": {"type": "string", "minLength": 1},
    "task_id": {"type": "string", "minLength": 1},
    "attempt_id": {"type": "string", "minLength": 1},
    "profile": {"type": "string"},
    "dispatch_ref": {"type": "string", "minLength": 1},
    "briefing_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "cursor": {"type": "string", "minLength": 1},
    "max_bytes": {"type": "integer", "minimum": 1},
}


def _dispatch_diagnostic_defaults(item: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(item)
    path = str(result.get("path") or "$")
    field = path.rsplit(".", 1)[-1]
    schema = _DISPATCH_FIELD_SCHEMAS.get(field, {"type": "object"})
    if field == "profile":
        schema = {**schema, "enum": sorted(AGENTS)}
    result.setdefault("field_schema", schema)
    result.setdefault("json_pointer", path)
    if "received" not in result and params is not None and path.startswith("$."):
        result["received"] = params.get(path[2:])
    result.setdefault("expected", schema)
    result.setdefault("fix", f"Correct only {path} according to field_schema, then retry read_dispatch_briefing with the same worker identity.")
    return result
def _bounded_artifact_max_bytes(value: Any, *, label: str) -> tuple[int | None, int | None, bool]:
    """Validate an optional caller-selected artifact page size."""
    if value is _MISSING:
        return None, None, False
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} max_bytes must be a positive integer")
    return value, value, False


def _dispatch_briefing_error_path(message: str) -> str:
    lowered = message.lower()
    for marker, path in (
        ("max_bytes", "max_bytes"), ("cursor", "cursor"),
        ("briefing_digest", "briefing_digest"), ("digest does not match", "briefing_digest"),
        ("dispatch_ref", "dispatch_ref"), ("attempt_id", "attempt_id"),
        ("task_id", "task_id"), ("profile", "profile"), ("project_root", "project_root"),
    ):
        if marker in lowered:
            return path
    return "$"


def _dispatch_briefing_failure(exc: BaseException, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return safe retryable argument diagnostics or an integrity blocker."""
    message = redact(str(exc), 1000)
    lowered = message.lower()
    collected = getattr(exc, "diagnostics", None)
    caller_correctable = isinstance(exc, ValueError) and (
        isinstance(collected, list) and bool(collected)
    ) or (isinstance(exc, ValueError) and any(fragment in lowered for fragment in (
        "unsupported read_dispatch_briefing fields", "is required; copy the exact value",
        "profile must be an exact cortex worker profile", "profile does not match",
        "dispatch_ref does not match", "briefing_digest must be", "briefing_digest does not match",
        "briefing cursor", "briefing max_bytes",
    )))
    if caller_correctable:
        path = _dispatch_briefing_error_path(message)
        fix = (
            "Omit max_bytes or use a positive integer, then retry read_dispatch_briefing on this same worker attempt."
            if path == "max_bytes" else
            "Copy the exact field from the native dispatch bootstrap or the last returned next_cursor, then retry read_dispatch_briefing on this same worker attempt."
        )
        diagnostics = getattr(exc, "diagnostics", None)
        if not isinstance(diagnostics, list) or not diagnostics:
            diagnostics = [{"code": "dispatch_briefing_request_invalid", "path": path, "message": message, "fix": fix}]
        else:
            diagnostics = [dict(item) for item in diagnostics if isinstance(item, dict)]
        diagnostics = [_dispatch_diagnostic_defaults(item, params) for item in diagnostics]
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": False, "outcome": "needs_correction",
            "code": "dispatch_briefing_request_invalid",
            "diagnostics": diagnostics,
            "retryable": True, "attempt_budget_consumed": False,
            "next_action": "Correct every listed path according to its field_schema, preserve the exact project_root/task_id/attempt_id/profile/dispatch_ref/briefing_digest identity, and retry read_dispatch_briefing. No briefing write or replacement worker is authorized.",
            "validation": {
                "schema": "cortex/validation-error/v1",
                "diagnostics_are_complete": True,
                "invalid_paths": [item.get("path") for item in diagnostics if item.get("path")],
                "retry": {"same_attempt": True, "attempt_budget_consumed": False, "replacement_worker_authorized": False},
                "apply_all_diagnostics_atomically": True,
            },
            "repair": {
                "tool": "read_dispatch_briefing",
                "same_attempt": True,
                "patch_only": True,
                "paths": [item.get("json_pointer") for item in diagnostics if item.get("json_pointer")],
                "preserve_paths_not_listed": True,
            },
        }
    return {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": False, "outcome": "blocked",
        "code": "dispatch_briefing_unavailable",
        "diagnostics": [{
            "code": "dispatch_briefing_unavailable", "path": "$", "message": message,
            "fix": "Preserve this integrity or storage diagnostic; it cannot be repaired by changing tool arguments.",
        }],
        "retryable": False, "attempt_budget_consumed": False,
        "next_action": "Stop before project work and return this exact non-retryable diagnostic to the parent coordinator.",
    }


def read_dispatch_briefing(params: dict[str, Any]) -> dict[str, Any]:
    """Read exactly one active worker's immutable briefing."""
    try:
        allowed = {"project_root", "task_id", "attempt_id", "profile", "dispatch_ref", "briefing_digest", "cursor", "max_bytes"}
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ValidationFailure([{"code": "dispatch_briefing_request_invalid", "path": f"$.{field}", "message": "unsupported read_dispatch_briefing field", "fix": "Remove this field and retry on the same worker attempt."} for field in unknown])
        collect_validations(
            ((field, lambda field=field: None if str(params.get(field) or "").strip() else f"{field} is required; copy the exact value from the native dispatch bootstrap")
             for field in ("project_root", "task_id", "attempt_id", "profile", "dispatch_ref", "briefing_digest")),
            code="dispatch_briefing_request_invalid",
        )
        project = select_project_root(params)
        task_id = safe_id(str(params["task_id"]))
        attempt_id = safe_id(str(params["attempt_id"]))
        profile = canonical_profile(params["profile"])
        dispatch_ref = safe_id(str(params["dispatch_ref"]))
        briefing_digest = str(params["briefing_digest"]).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", briefing_digest):
            raise ValueError("briefing_digest must be the exact SHA-256 from the native dispatch bootstrap")
        _, task_dir, state = load_state(task_id, {"project_root": str(project)})
        attempt = _attempt(state, attempt_id)
        if attempt.get("invalidated") or attempt.get("status") not in {AWAITING_HOST_SPAWN, "running"} or not attempt.get("facade_managed"):
            raise ValueError("dispatch briefing reads require an active, non-invalidated public worker attempt")
        if attempt.get("profile") != profile:
            raise ValueError("profile does not match the exact dispatched worker")
        if attempt.get("dispatch_ref") != dispatch_ref:
            raise ValueError("dispatch_ref does not match the exact dispatched worker")
        if str(attempt.get("briefing_digest") or "").lower() != briefing_digest:
            raise ValueError("briefing_digest does not match the exact dispatched worker")
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
        byte_offset = 0
        has_cursor = "cursor" in params
        raw_cursor = params.get("cursor")
        if has_cursor:
            if not isinstance(raw_cursor, str) or not raw_cursor:
                raise ValueError("briefing cursor must be a non-empty opaque string returned by this server")
            try:
                decoded = _runtime.db_decode_artifact_cursor(root, raw_cursor)
            except ValueError as exc:
                raise ValueError("briefing cursor is invalid") from exc
            expected = {"type": "briefing_read", "task_id": state["task_id"], "artifact_ref": artifact["artifact_ref"], "digest_sha256": briefing_digest, "audience": audience}
            if any(decoded.get(key) != value for key, value in expected.items()):
                raise ValueError("briefing cursor is not valid for this dispatch, worker identity, or content version")
            byte_offset = decoded.get("byte_offset")
            if isinstance(byte_offset, bool) or not isinstance(byte_offset, int) or byte_offset < 0:
                raise ValueError("briefing cursor byte offset is invalid")
        raw_max_bytes = params["max_bytes"] if "max_bytes" in params else _MISSING
        effective_max, requested_max, max_bytes_normalized = _bounded_artifact_max_bytes(raw_max_bytes, label="briefing")
        chunked = has_cursor or requested_max is not None
        base = {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "briefing_read", "task_id": task_id, "attempt_id": attempt_id, "profile": profile, "dispatch_ref": dispatch_ref, "briefing_digest": briefing_digest, "briefing_artifact": artifact}
        if chunked:
            part = _runtime.db_read_artifact_range(root, state["task_id"], artifact["artifact_ref"], byte_offset=byte_offset, max_bytes=effective_max)
            result = {**base, "content_part": part.get("content_part"), "encoding": part["encoding"], "byte_offset": part["byte_offset"], "returned_bytes": part["returned_bytes"], "complete": part["complete"], "effective_max_bytes": effective_max, "max_bytes_normalized": max_bytes_normalized}
            if requested_max is not None:
                result["requested_max_bytes"] = requested_max
            if part.get("content_base64") is not None:
                result["content_base64"] = part["content_base64"]
            if part["next_byte_offset"] is not None:
                result["next_cursor"] = _runtime.db_encode_artifact_cursor(root, {"type": "briefing_read", "task_id": state["task_id"], "artifact_ref": artifact["artifact_ref"], "digest_sha256": briefing_digest, "byte_offset": part["next_byte_offset"], "audience": audience})
            if part["complete"]:
                result["briefing_receipt"] = attempt_protocol.acknowledge_briefing(root, task_id=state["task_id"], attempt_id=attempt_id, dispatch_ref=dispatch_ref, digest=briefing_digest)
            result["next_action"] = "If complete is false, call read_dispatch_briefing again with the same exact identity/digest tuple and next_cursor. Do not substitute another briefing or read another Cortex path."
            return result
        receipt = attempt_protocol.acknowledge_briefing(root, task_id=state["task_id"], attempt_id=attempt_id, dispatch_ref=dispatch_ref, digest=briefing_digest)
        return {**base, "briefing": briefing, "briefing_receipt": receipt, "next_action": "Follow this complete validated briefing. Cortex recorded the server-owned read receipt; do not author an acknowledgement marker or read another Cortex ledger path or briefing."}
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return _dispatch_briefing_failure(exc, params=params)
