"""Bounded coordinator transport for immutable SQLite-backed artifacts."""
from __future__ import annotations

import json
from typing import Any

import cortex as _runtime
from cortex_runtime.ledger_db import _governance_lifecycle_hmac_key
from cortex_runtime.pagination import decode_cursor, encode_cursor, scope_digest


DEFAULT_ARTIFACT_PAGE_SIZE = 20
DEFAULT_ARTIFACT_READ_BYTES = 16_384
DEFAULT_ARTIFACT_METADATA_CHARS = 16_384


def _payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = params.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("artifact management requires an object payload")
    unknown = sorted(set(payload) - {
        "action", "artifact_ref", "kind", "cursor",
    })
    if unknown:
        raise ValueError("unsupported artifact management payload fields: " + ", ".join(unknown))
    return payload


def _cursor(
    root, *, cursor: object, selector: str, audience: str, digest: str,
) -> int:
    return decode_cursor(
        cursor, _governance_lifecycle_hmac_key(root, create=False),
        selector=selector, audience=audience, digest=digest,
    )


def _read_cursor(root, metadata: dict[str, Any], *, byte_offset: int) -> str:
    return encode_cursor(
        _governance_lifecycle_hmac_key(root, create=False),
        selector="manage_orchestration.artifact_read", audience="coordinator",
        digest=scope_digest({
            "task_id": metadata["task_id"], "artifact_ref": metadata["artifact_ref"],
            "digest_sha256": metadata["digest_sha256"],
        }), offset=byte_offset,
    )


def manage_task_artifacts(
    params: dict[str, Any],
    task_dir,
    state: dict[str, Any],
    task_ref: str,
) -> dict[str, Any]:
    """List metadata or read one exact immutable artifact.

    Every growing result uses a server-fixed bounded page and opaque cursor.
    """
    payload = _payload(params)
    action = str(payload.get("action") or "list").strip().lower()
    if action not in {"list", "metadata", "read"}:
        raise ValueError("artifact management action must be list, metadata, or read")
    task_id = str(state["task_id"])
    root = _runtime._task_document_root(task_dir, task_id)
    if action == "list":
        kind = str(payload.get("kind") or "").strip() or None
        selector = "manage_orchestration.artifact_list"
        binding = scope_digest({"task_id": task_id, "kind": kind})
        page_size = DEFAULT_ARTIFACT_PAGE_SIZE
        offset = 0
        if "cursor" in payload:
            offset = _cursor(
                root, cursor=payload["cursor"], selector=selector,
                audience="coordinator", digest=binding,
            )
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
            result["next_cursor"] = encode_cursor(
                _governance_lifecycle_hmac_key(root, create=False),
                selector=selector, audience="coordinator", digest=binding,
                offset=next_offset,
            )
        result["next_action"] = (
            "Call manage_orchestration again with action='artifact_list', unchanged read fields, "
            "and this exact next_cursor."
        )
        return result

    artifact_ref = _runtime.safe_id(str(payload.get("artifact_ref") or ""))
    if not artifact_ref:
        raise ValueError("artifact_ref is required for artifact metadata or read")
    metadata = _runtime.db_get_artifact_metadata(root, task_id, artifact_ref)
    if metadata is None:
        raise ValueError("artifact_ref is unavailable for the selected task")
    if action == "metadata":
        selector = "manage_orchestration.artifact_metadata"
        binding = scope_digest({
            "task_id": task_id, "artifact_ref": artifact_ref,
            "digest_sha256": metadata["digest_sha256"],
        })
        char_offset = 0
        if "cursor" in payload:
            char_offset = _cursor(
                root, cursor=payload["cursor"], selector=selector,
                audience="coordinator", digest=binding,
            )
        full = json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str)
        content = full[char_offset:char_offset + DEFAULT_ARTIFACT_METADATA_CHARS]
        next_offset = char_offset + len(content)
        result = {
            "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "artifact_metadata",
            "task_ref": task_ref,
            "content": content,
            "complete": next_offset >= len(full),
        }
        if next_offset < len(full):
            result["next_cursor"] = encode_cursor(
                _governance_lifecycle_hmac_key(root, create=False),
                selector=selector, audience="coordinator", digest=binding,
                offset=next_offset,
            )
        return result

    byte_offset = 0
    if "cursor" in payload:
        byte_offset = _cursor(
            root, cursor=payload["cursor"], selector="manage_orchestration.artifact_read",
            audience="coordinator", digest=scope_digest({
                "task_id": task_id, "artifact_ref": artifact_ref,
                "digest_sha256": metadata["digest_sha256"],
            }),
        )
    part = _runtime.db_read_artifact_range(
        root, task_id, artifact_ref,
        byte_offset=byte_offset, max_bytes=DEFAULT_ARTIFACT_READ_BYTES,
    )
    result = {
        "schema": _runtime.PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "outcome": "artifact_part",
        "task_ref": task_ref,
        **part,
    }
    if part["next_byte_offset"] is not None:
        result["next_cursor"] = _read_cursor(root, metadata, byte_offset=part["next_byte_offset"])
    result["next_action"] = (
        "If complete is false, use next_cursor with the same artifact_ref to fetch the next exact part."
    )
    return result
