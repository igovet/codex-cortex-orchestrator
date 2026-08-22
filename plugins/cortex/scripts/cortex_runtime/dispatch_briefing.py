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


def _bounded_artifact_max_bytes(value: Any, *, label: str) -> tuple[int, int | None, bool]:
    """Normalize a bounded artifact chunk request."""
    if value is None:
        return 16 * 1024, None, False
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} max_bytes must be an integer")
    if value < 1:
        raise ValueError(f"{label} max_bytes must be at least 1")
    effective = min(value, _runtime.ARTIFACT_TRANSPORT_MAX_BYTES)
    return effective, value, effective != value


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


def _dispatch_briefing_failure(exc: BaseException) -> dict[str, Any]:
    """Return safe retryable argument diagnostics or an integrity blocker."""
    message = redact(str(exc), 1000)
    lowered = message.lower()
    caller_correctable = isinstance(exc, ValueError) and any(fragment in lowered for fragment in (
        "unsupported read_dispatch_briefing fields", "is required; copy the exact value",
        "profile must be an exact cortex worker profile", "profile does not match",
        "dispatch_ref does not match", "briefing_digest must be", "briefing_digest does not match",
        "briefing cursor", "briefing max_bytes",
    ))
    if caller_correctable:
        path = _dispatch_briefing_error_path(message)
        fix = (
            "Omit max_bytes or use an integer from 1 through 32768, then retry read_dispatch_briefing on this same worker attempt."
            if path == "max_bytes" else
            "Copy the exact field from the native dispatch bootstrap or the last returned next_cursor, then retry read_dispatch_briefing on this same worker attempt."
        )
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": False, "outcome": "needs_correction",
            "code": "dispatch_briefing_request_invalid",
            "diagnostics": [{"code": "dispatch_briefing_request_invalid", "path": path, "message": message, "fix": fix}],
            "retryable": True, "attempt_budget_consumed": False,
            "next_action": "Apply the diagnostic fix and retry read_dispatch_briefing on this same attempt. Stop only if a later response explicitly returns retryable=false or outcome=blocked.",
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
            raise ValueError("unsupported read_dispatch_briefing fields: " + ", ".join(unknown))
        for field in {"project_root", "task_id", "attempt_id", "profile", "dispatch_ref", "briefing_digest"}:
            if not str(params.get(field) or "").strip():
                raise ValueError(f"{field} is required; copy the exact value from the native dispatch bootstrap")
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
        raw_cursor = params.get("cursor")
        if raw_cursor:
            decoded = _runtime.db_decode_artifact_cursor(root, str(raw_cursor))
            expected = {"type": "briefing_read", "task_id": state["task_id"], "artifact_ref": artifact["artifact_ref"], "digest_sha256": briefing_digest, "audience": audience}
            if any(decoded.get(key) != value for key, value in expected.items()):
                raise ValueError("briefing cursor is not valid for this dispatch, worker identity, or content version")
            byte_offset = decoded.get("byte_offset")
            if isinstance(byte_offset, bool) or not isinstance(byte_offset, int) or byte_offset < 0:
                raise ValueError("briefing cursor byte offset is invalid")
        effective_max, requested_max, max_bytes_normalized = _bounded_artifact_max_bytes(params.get("max_bytes"), label="briefing")
        chunked = bool(raw_cursor) or requested_max is not None or artifact["byte_size"] > _runtime.ARTIFACT_TRANSPORT_MAX_BYTES
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
        return _dispatch_briefing_failure(exc)
