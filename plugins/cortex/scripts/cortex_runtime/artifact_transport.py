"""Bounded coordinator transport for immutable SQLite-backed artifacts."""
from __future__ import annotations

from typing import Any

import cortex as _runtime


MAX_ARTIFACT_PAGE_SIZE = 50
DEFAULT_ARTIFACT_PAGE_SIZE = 20
DEFAULT_ARTIFACT_READ_BYTES = 16 * 1024
# Keep a text page large enough for one complete UTF-8 scalar.  This lets the
# lower storage transport keep its strict effective-byte ceiling without
# returning a broken fragment or stalling on a four-byte scalar.
MIN_ARTIFACT_READ_BYTES = 4


def _payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = params.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("artifact management requires an object payload")
    unknown = sorted(set(payload) - {
        "action", "artifact_ref", "kind", "cursor", "page_size", "max_bytes",
    })
    if unknown:
        raise ValueError("unsupported artifact management payload fields: " + ", ".join(unknown))
    return payload


def _cursor(root, *, cursor: object, expected: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cursor, str) or not cursor:
        raise ValueError("artifact cursor must be a non-empty opaque string returned by this server")
    decoded = _runtime.db_decode_artifact_cursor(root, cursor)
    for key, value in expected.items():
        if decoded.get(key) != value:
            raise ValueError("artifact cursor is not valid for this task, artifact, permission scope, or content version")
    return decoded


def _read_cursor(root, metadata: dict[str, Any], *, byte_offset: int) -> str:
    return _runtime.db_encode_artifact_cursor(root, {
        "type": "artifact_read",
        "task_id": metadata["task_id"],
        "artifact_ref": metadata["artifact_ref"],
        "digest_sha256": metadata["digest_sha256"],
        "byte_offset": byte_offset,
        "audience": "coordinator",
    })


def manage_task_artifacts(
    params: dict[str, Any],
    task_dir,
    state: dict[str, Any],
    task_ref: str,
) -> dict[str, Any]:
    """List metadata or stream one immutable artifact without large MCP results."""
    payload = _payload(params)
    action = str(payload.get("action") or "list").strip().lower()
    if action not in {"list", "metadata", "read"}:
        raise ValueError("artifact management action must be list, metadata, or read")
    task_id = str(state["task_id"])
    root = _runtime._task_document_root(task_dir, task_id)
    if action == "list":
        kind = str(payload.get("kind") or "").strip() or None
        page_size = payload.get("page_size", DEFAULT_ARTIFACT_PAGE_SIZE)
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= MAX_ARTIFACT_PAGE_SIZE:
            raise ValueError(f"artifact page_size must be an integer from 1 through {MAX_ARTIFACT_PAGE_SIZE}")
        offset = 0
        if "cursor" in payload:
            decoded = _cursor(root, cursor=payload["cursor"], expected={
                "type": "artifact_list", "task_id": task_id, "kind": kind, "audience": "coordinator",
            })
            offset = decoded.get("offset")
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ValueError("artifact list cursor offset is invalid")
        artifacts, next_offset = _runtime.db_list_artifacts(
            root, task_id, kind=kind, offset=offset, page_size=page_size,
        )
        result = {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "artifact_page",
            "task_ref": task_ref,
            "artifacts": artifacts,
            "complete": next_offset is None,
        }
        if next_offset is not None:
            result["next_cursor"] = _runtime.db_encode_artifact_cursor(root, {
                "type": "artifact_list", "task_id": task_id, "kind": kind,
                "offset": next_offset, "audience": "coordinator",
            })
        result["next_action"] = (
            "Use next_cursor only with manage_orchestration(intent='artifacts', payload={action:'list', ...}) "
            "to retrieve the next metadata page; never request all artifact bodies together."
        )
        return result

    artifact_ref = _runtime.safe_id(str(payload.get("artifact_ref") or ""))
    if not artifact_ref:
        raise ValueError("artifact_ref is required for artifact metadata or read")
    metadata = _runtime.db_get_artifact_metadata(root, task_id, artifact_ref)
    if metadata is None:
        raise ValueError("artifact_ref is unavailable for the selected task")
    if action == "metadata":
        return {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "artifact_metadata",
            "task_ref": task_ref,
            "artifact": metadata,
            "read_cursor": _read_cursor(root, metadata, byte_offset=0),
            "next_action": "Use the opaque read_cursor to request a bounded artifact part; its digest and task scope are checked on every read.",
        }

    requested_max_bytes = payload.get("max_bytes", DEFAULT_ARTIFACT_READ_BYTES)
    if isinstance(requested_max_bytes, bool) or not isinstance(requested_max_bytes, int):
        raise ValueError("artifact max_bytes must be an integer")
    if requested_max_bytes < 1:
        raise ValueError("artifact max_bytes must be at least 1")
    max_bytes = max(MIN_ARTIFACT_READ_BYTES, min(requested_max_bytes, _runtime.ARTIFACT_TRANSPORT_MAX_BYTES))
    byte_offset = 0
    if "cursor" in payload:
        decoded = _cursor(root, cursor=payload["cursor"], expected={
            "type": "artifact_read", "task_id": task_id, "artifact_ref": artifact_ref,
            "digest_sha256": metadata["digest_sha256"], "audience": "coordinator",
        })
        byte_offset = decoded.get("byte_offset")
        if isinstance(byte_offset, bool) or not isinstance(byte_offset, int) or byte_offset < 0:
            raise ValueError("artifact read cursor offset is invalid")
    part = _runtime.db_read_artifact_range(
        root, task_id, artifact_ref, byte_offset=byte_offset, max_bytes=max_bytes,
    )
    result = {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "outcome": "artifact_part",
        "task_ref": task_ref,
        "requested_max_bytes": requested_max_bytes,
        "effective_max_bytes": max_bytes,
        "max_bytes_normalized": max_bytes != requested_max_bytes,
        **part,
    }
    if part["next_byte_offset"] is not None:
        result["next_cursor"] = _read_cursor(root, metadata, byte_offset=part["next_byte_offset"])
    result["next_action"] = (
        "If complete is false, use next_cursor with the same artifact_ref to fetch only the next bounded part; "
        "max_bytes is safely normalized to the UTF-8-safe server range."
    )
    return result
