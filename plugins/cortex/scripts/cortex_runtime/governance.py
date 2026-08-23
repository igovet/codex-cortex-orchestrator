"""Server-owned governance primitives for the v12 Cortex ledger.

The orchestration engine remains responsible for sequencing workers.  This
module owns the durable governance contract that sits beside that pipeline:
mode classification, initiatives and dependency integrity, append-only
records, active snapshots, exceptions, and coordinator-approved promotion
proposals.  It intentionally depends only on :mod:`ledger_db`, which keeps
the public MCP facade as the composition root and makes the helpers useful in
focused migration and adversarial tests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from cortex_runtime import canonical_json, ledger_db


GOVERNANCE_SCHEMA = "cortex/governance/v1"
GOVERNANCE_MODES = {"auto", "required", "off"}
EFFECTIVE_MODES = {"minimal", "light", "full"}
RECORD_TYPES = {
    "policy", "decision", "ruling", "preference", "assumption", "risk",
    "learning", "reflection", "exception", "promotion",
}
RECORD_STATUSES = {"pending", "active", "approved", "rejected", "superseded", "expired"}
INITIATIVE_STATUSES = {"proposed", "active", "blocked", "completed", "closed", "cancelled"}
LINK_RELATIONSHIPS = {"milestone", "deliverable", "corrective"}
DEPENDENCY_TYPES = {"blocks", "requires", "relates_to", "follows"}
ACTOR_ROLES = {"coordinator", "worker", "reviewer"}
HARD_TRIGGER_KEYS = {
    "security", "privacy", "credentials", "sensitive_data", "destructive",
    "migration", "external_action", "public_contract", "authorization",
    "artifact_integrity", "result_integrity", "verification_integrity",
}
OFF_ASSESSMENT_KEYS = HARD_TRIGGER_KEYS | {
    "multiple_repositories", "related_tasks", "long_lived_lanes",
    "conflicting_resources", "multi_session_handoff",
}

_CLOSE_EVIDENCE_TYPES = {
    "oracle_evidence": {"oracle_evidence", "acceptance_oracle", "acceptance_oracle_evidence"},
    "risk_disposition": {"risk_disposition", "risk_register", "risk_evidence"},
    "falsification_review": {"falsification_review", "falsification_strategy", "falsifier_review"},
    "retrospective": {"retrospective", "reflection", "lessons_learned"},
    "independent_review": {"independent_review", "independent_governance_review", "governance_review"},
}


class GovernanceError(ValueError):
    """A caller-correctable governance policy or integrity violation."""

    def __init__(self, message: str, *, code: str = "governance_invalid") -> None:
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    try:
        return canonical_json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceError("governance content must be strict JSON", code="content_invalid") from exc


def _bounded_content_json(value: Any, *, label: str) -> str:
    """Return strict canonical JSON without a backend content-size quota."""
    _validate_content_shape(value)
    del label
    return _canonical(value)


def _bounded_governance_text(value: Any, *, label: str, required: bool = False) -> str:
    """Normalize a scalar governance field without a content-size quota."""
    text = str(value or "").strip()
    if required and not text:
        raise GovernanceError(f"{label} is required", code="initiative_fields_required")
    return text


def _digest(value: Any) -> str:
    return canonical_json.digest(value)


def _parse_timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernanceError(f"{label} must be an ISO-8601 timestamp", code="invalid_expiry") from exc
    if parsed.tzinfo is None:
        raise GovernanceError(f"{label} must include a timezone", code="invalid_expiry")
    return parsed.astimezone(timezone.utc)


def _safe_ref(value: Any, label: str, *, prefix: str | None = None) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 160 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", raw):
        raise GovernanceError(f"{label} is invalid", code="invalid_ref")
    if prefix and not raw.startswith(prefix):
        raise GovernanceError(f"{label} must start with {prefix}", code="invalid_ref")
    return raw


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else {str(key): row[key] for key in row.keys()}


def _strict_json_loads(value: str, label: str) -> Any:
    """Decode JSON without accepting JavaScript-only constants such as NaN."""
    try:
        return json.loads(
            value,
            parse_constant=lambda _constant: (_ for _ in ()).throw(ValueError("non-finite JSON constant")),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GovernanceError(f"stored governance {label} is invalid", code="ledger_corrupt") from exc


def _scope_key(initiative_ref: str | None, task_id: str | None) -> str:
    """Return the non-null, collision-free database identity for a scope."""
    initiative = str(initiative_ref or "").strip()
    task = str(task_id or "").strip()
    if initiative and task:
        return f"initiative-task:{len(initiative)}:{initiative}:{len(task)}:{task}"
    if initiative:
        return f"initiative:{len(initiative)}:{initiative}"
    if task:
        return f"task:{len(task)}:{task}"
    return "project:"


def _connection(root: Path, *, write: bool = False):
    # Keep this wrapper in one place so all governance calls inherit the
    # ledger's private-root, WAL, FK, lock, and transaction policy.
    return ledger_db.connection(root, write=write)


_GOVERNANCE_BLOB_MIME = "application/json"


def _governance_blob_chunks(raw: bytes) -> list[bytes]:
    """Return the canonical UTF-8 chunks used by the v7 blob catalog."""
    return ledger_db.text_chunk_boundaries(raw)


def _validate_blob_with_connection(
    connection: sqlite3.Connection,
    blob_ref: str,
    *,
    expected_digest: str | None = None,
    require_governance_mime: bool = True,
) -> dict[str, Any]:
    """Verify a content-addressed blob and every immutable chunk.

    Governance records deliberately use the shared canonical blob catalog
    instead of adding a second content store.  A digest in a row is not
    sufficient evidence by itself: the chunk rows are reassembled and
    hashed on every trust-boundary read so a tampered ledger cannot be used
    as close or policy evidence.
    """
    row = connection.execute(
        "SELECT blob_id, digest_sha256, mime_type, byte_size, chunk_count, encoding "
        "FROM artifact_blobs WHERE blob_id = ?",
        (blob_ref,),
    ).fetchone()
    if row is None:
        raise GovernanceError("governance artifact is not present in the immutable blob catalog", code="artifact_not_found")
    if require_governance_mime and (
        str(row["mime_type"]) != _GOVERNANCE_BLOB_MIME or str(row["encoding"]) != "utf-8"
    ):
        raise GovernanceError("governance artifact has unsupported blob metadata", code="artifact_integrity_failed")
    chunks = connection.execute(
        "SELECT chunk_no, text_content, blob_content, byte_size, digest_sha256 "
        "FROM artifact_blob_chunks WHERE blob_id = ? ORDER BY chunk_no",
        (blob_ref,),
    ).fetchall()
    if len(chunks) != int(row["chunk_count"]) or not chunks:
        raise GovernanceError("governance artifact chunk count is inconsistent", code="artifact_integrity_failed")
    assembled: list[bytes] = []
    for expected_no, chunk in enumerate(chunks):
        if int(chunk["chunk_no"]) != expected_no or (
            (chunk["text_content"] is None) == (chunk["blob_content"] is None)
        ):
            raise GovernanceError("governance artifact chunk encoding is inconsistent", code="artifact_integrity_failed")
        if chunk["text_content"] is not None:
            try:
                data = str(chunk["text_content"]).encode("utf-8")
            except UnicodeError as exc:
                raise GovernanceError("governance artifact chunk is not valid UTF-8", code="artifact_integrity_failed") from exc
        else:
            data = bytes(chunk["blob_content"])
        if int(chunk["byte_size"]) != len(data) or str(chunk["digest_sha256"]) != hashlib.sha256(data).hexdigest():
            raise GovernanceError("governance artifact chunk digest is invalid", code="artifact_integrity_failed")
        assembled.append(data)
    raw = b"".join(assembled)
    digest = hashlib.sha256(raw).hexdigest()
    if int(row["byte_size"]) != len(raw) or str(row["digest_sha256"]) != digest:
        raise GovernanceError("governance artifact blob digest is invalid", code="artifact_integrity_failed")
    if expected_digest and digest != str(expected_digest):
        raise GovernanceError("governance artifact digest does not match its record", code="artifact_integrity_failed")
    return {
        "artifact_ref": blob_ref,
        "blob_id": blob_ref,
        "digest_sha256": digest,
        "mime_type": str(row["mime_type"]),
        "byte_size": len(raw),
        "chunk_count": len(chunks),
        "immutable": True,
    }


def _validate_governance_artifact(
    connection: sqlite3.Connection,
    artifact_ref: Any,
    *,
    expected_digest: str | None = None,
    initiative_ref: str | None = None,
) -> dict[str, Any]:
    """Resolve a governance artifact reference to immutable content.

    A close-evidence binding may point at a canonical blob, a logical artifact
    from a task, or a governance record. Record references are accepted only
    when their record belongs to the same initiative and their own immutable
    content artifact validates recursively.
    """
    ref = str(artifact_ref or "").strip()
    if not ref or len(ref) > 256:
        raise GovernanceError("governance evidence requires an artifact reference", code="artifact_required")
    if ref.startswith("record-"):
        record = connection.execute(
            "SELECT record_ref, initiative_ref, task_id, record_type, content_digest, content_artifact_ref "
            "FROM governance_records WHERE record_ref = ?",
            (ref,),
        ).fetchone()
        if record is None or not record["content_artifact_ref"]:
            raise GovernanceError("governance evidence record has no immutable content artifact", code="artifact_not_found")
        if initiative_ref and record["initiative_ref"] != initiative_ref:
            raise GovernanceError("governance evidence record is outside the initiative scope", code="artifact_scope_mismatch")
        if expected_digest and str(record["content_digest"]) != str(expected_digest):
            raise GovernanceError("governance evidence record digest does not match its binding", code="artifact_integrity_failed")
        return _validate_governance_artifact(
            connection,
            record["content_artifact_ref"],
            expected_digest=str(record["content_digest"]),
            initiative_ref=initiative_ref,
        ) | {
            "record_ref": ref,
            "record_type": str(record["record_type"]),
            "record_initiative_ref": str(record["initiative_ref"] or "") or None,
            "record_task_id": str(record["task_id"] or "") or None,
        }
    if ref.startswith("artifact-"):
        logical = connection.execute(
            "SELECT artifact_id, task_id, mime_type, digest_sha256, byte_size, chunk_count, immutable, blob_id "
            "FROM logical_artifacts WHERE artifact_id = ?",
            (ref,),
        ).fetchone()
        if logical is None:
            raise GovernanceError("governance evidence logical artifact is not present", code="artifact_not_found")
        if not bool(logical["immutable"]):
            raise GovernanceError("governance evidence logical artifact is mutable", code="artifact_integrity_failed")
        if expected_digest and str(logical["digest_sha256"]) != str(expected_digest):
            raise GovernanceError("governance logical artifact digest does not match its binding", code="artifact_integrity_failed")
        metadata = _validate_blob_with_connection(
            connection,
            str(logical["blob_id"]),
            expected_digest=str(logical["digest_sha256"]),
            require_governance_mime=False,
        )
        if int(logical["byte_size"]) != metadata["byte_size"] or int(logical["chunk_count"]) != metadata["chunk_count"]:
            raise GovernanceError("governance logical artifact metadata is inconsistent", code="artifact_integrity_failed")
        return metadata | {"artifact_ref": ref, "task_id": str(logical["task_id"])}
    metadata = _validate_blob_with_connection(connection, ref, expected_digest=expected_digest)
    if initiative_ref:
        scoped = connection.execute(
            "SELECT 1 FROM governance_records WHERE content_artifact_ref = ? AND initiative_ref = ? LIMIT 1",
            (ref, initiative_ref),
        ).fetchone()
        if scoped is None:
            raise GovernanceError("governance blob is not server-scoped to the initiative", code="artifact_scope_mismatch")
    return metadata


def _store_governance_artifact(
    connection: sqlite3.Connection,
    body_json: str,
    digest: str,
    supplied_ref: str | None = None,
    *,
    initiative_ref: str | None = None,
    task_id: str | None = None,
) -> str:
    """Persist or verify the immutable JSON artifact for every record."""
    if supplied_ref:
        supplied = str(supplied_ref).strip()
        # A bare content-addressed blob has no logical task/initiative binding
        # and cannot be supplied as a record's immutable content artifact.
        # Auto-created record bodies still use the internal blob catalog below;
        # caller-supplied artifacts must carry the logical artifact contract.
        if not supplied.startswith("artifact-"):
            raise GovernanceError(
                "content_artifact_ref must identify a logical immutable artifact",
                code="artifact_scope_required",
            )
        metadata = _validate_governance_artifact(
            connection,
            supplied,
            expected_digest=digest,
            initiative_ref=initiative_ref,
        )
        artifact_task = str(metadata.get("task_id") or "").strip()
        if task_id and artifact_task != str(task_id).strip():
            raise GovernanceError(
                "content artifact task does not match the governance record scope",
                code="artifact_scope_mismatch",
            )
        if initiative_ref and artifact_task:
            linked = connection.execute(
                "SELECT 1 FROM initiative_task_links WHERE initiative_ref = ? AND task_id = ? LIMIT 1",
                (initiative_ref, artifact_task),
            ).fetchone()
            if linked is None:
                raise GovernanceError(
                    "content artifact task is not linked to the governance initiative",
                    code="artifact_scope_mismatch",
                )
        return supplied
    raw = body_json.encode("utf-8")
    blob_ref = ledger_db.content_addressed_blob_ref(digest, _GOVERNANCE_BLOB_MIME, len(raw))
    chunks = _governance_blob_chunks(raw)
    now = _now()
    connection.execute(
        "INSERT INTO artifact_blobs(blob_id, digest_sha256, mime_type, byte_size, chunk_count, encoding, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'utf-8', ?) ON CONFLICT(digest_sha256, mime_type, byte_size) DO NOTHING",
        (blob_ref, digest, _GOVERNANCE_BLOB_MIME, len(raw), len(chunks), now),
    )
    row = connection.execute(
        "SELECT blob_id, chunk_count, encoding FROM artifact_blobs WHERE digest_sha256 = ? AND mime_type = ? AND byte_size = ?",
        (digest, _GOVERNANCE_BLOB_MIME, len(raw)),
    ).fetchone()
    if row is None or str(row["blob_id"]) != blob_ref or int(row["chunk_count"]) != len(chunks) or str(row["encoding"]) != "utf-8":
        raise GovernanceError("governance content artifact metadata is inconsistent", code="artifact_integrity_failed")
    existing = int(connection.execute("SELECT COUNT(*) FROM artifact_blob_chunks WHERE blob_id = ?", (blob_ref,)).fetchone()[0])
    if existing == 0:
        for chunk_no, chunk in enumerate(chunks):
            connection.execute(
                "INSERT INTO artifact_blob_chunks(blob_id, chunk_no, text_content, blob_content, byte_size, digest_sha256) VALUES (?, ?, ?, NULL, ?, ?)",
                (blob_ref, chunk_no, chunk.decode("utf-8"), len(chunk), hashlib.sha256(chunk).hexdigest()),
            )
    elif existing != len(chunks):
        raise GovernanceError("governance content artifact chunks are inconsistent", code="artifact_integrity_failed")
    _validate_blob_with_connection(connection, blob_ref, expected_digest=digest)
    return blob_ref


def _initiative_row(row: sqlite3.Row) -> dict[str, Any]:
    value = _row_dict(row) or {}
    value["acceptance_oracle_artifact_ref"] = value.get("acceptance_oracle_artifact_ref") or None
    return value


def _record_row(row: sqlite3.Row) -> dict[str, Any]:
    value = _row_dict(row) or {}
    for key in ("content_json", "approval_basis_json"):
        if value.get(key) is not None:
            value[key] = _strict_json_loads(str(value[key]), key)
    return value


def _validate_content_shape(value: Any, *, depth: int = 0) -> None:
    """Validate strict JSON types without a content-volume or depth quota."""
    del depth
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise GovernanceError("governance content object keys must be strings", code="content_invalid")
            _validate_content_shape(item)
        return
    if isinstance(value, list):
        for item in value:
            _validate_content_shape(item)
        return
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise GovernanceError("governance content must not contain non-finite numbers", code="content_invalid")
        return
    raise GovernanceError("governance content must use strict JSON types", code="content_invalid")


def _validated_artifact_payload(connection: sqlite3.Connection, metadata: dict[str, Any]) -> Any:
    """Decode a previously digest-verified JSON governance artifact."""
    blob_ref = str(metadata.get("blob_id") or "").strip()
    if not blob_ref:
        raise GovernanceError("governance evidence has no canonical blob", code="artifact_integrity_failed")
    chunks = connection.execute(
        "SELECT chunk_no, text_content, blob_content FROM artifact_blob_chunks WHERE blob_id = ? ORDER BY chunk_no",
        (blob_ref,),
    ).fetchall()
    raw = b"".join(
        str(chunk["text_content"]).encode("utf-8") if chunk["text_content"] is not None else bytes(chunk["blob_content"])
        for chunk in chunks
    )
    try:
        value = _strict_json_loads(raw.decode("utf-8"), "content artifact")
        _validate_content_shape(value)
        return value
    except (UnicodeDecodeError, GovernanceError) as exc:
        raise GovernanceError("governance evidence artifact must contain canonical JSON", code="artifact_schema_invalid") from exc


def _validate_record_lifecycle(root: Path, connection: sqlite3.Connection, row: sqlite3.Row) -> None:
    """Fail closed unless the indexed lifecycle projection has an exact chain.

    The record row is intentionally convenient for snapshot indexes, but it
    is not the authority for its mutable status or approval basis.  Every
    value must be reproduced by the append-only lifecycle history and every
    event binds to its predecessor.  This detects raw SQL changes even when a
    writer bypassed the database transition trigger.
    """
    try:
        record_ref = str(row["record_ref"])
        sequence = int(row["lifecycle_sequence"])
        binding = str(row["lifecycle_binding"] or "")
        if sequence < 0 or not re.fullmatch(r"[0-9a-f]{64}", binding):
            raise ValueError("record lifecycle projection is malformed")
        events = connection.execute(
            "SELECT lifecycle.lifecycle_ref,lifecycle.lifecycle_sequence,lifecycle.previous_binding,"
            "lifecycle.status,lifecycle.approval_basis_json,lifecycle.binding,lifecycle.action,"
            "lifecycle.actor_role,lifecycle.created_at,auth.envelope_hmac "
            "FROM governance_record_lifecycle AS lifecycle "
            "LEFT JOIN governance_record_lifecycle_auth AS auth ON auth.lifecycle_ref=lifecycle.lifecycle_ref "
            "WHERE lifecycle.record_ref=? ORDER BY lifecycle.lifecycle_sequence",
            (record_ref,),
        ).fetchall()
        if len(events) != sequence + 1:
            raise ValueError("record lifecycle event count is inconsistent")
        previous: str | None = None
        for expected_sequence, event in enumerate(events):
            event_sequence = int(event["lifecycle_sequence"])
            event_previous = str(event["previous_binding"] or "") or None
            event_status = str(event["status"])
            event_basis = str(event["approval_basis_json"]) if event["approval_basis_json"] is not None else None
            event_binding = str(event["binding"] or "")
            expected_binding = ledger_db.governance_lifecycle_binding(
                record_ref=record_ref,
                sequence=expected_sequence,
                previous_binding=previous,
                status=event_status,
                approval_basis_json=event_basis,
            )
            if (
                event_sequence != expected_sequence
                or event_previous != previous
                or event_binding != expected_binding
            ):
                raise ValueError("record lifecycle event binding is inconsistent")
            expected_hmac = ledger_db.governance_lifecycle_envelope_hmac(
                root,
                lifecycle_ref=str(event["lifecycle_ref"]),
                record_ref=record_ref,
                lifecycle_sequence=event_sequence,
                previous_binding=event_previous,
                status=event_status,
                approval_basis_json=event_basis,
                binding=event_binding,
                action=str(event["action"]),
                actor_role=str(event["actor_role"]),
                created_at=str(event["created_at"]),
            )
            supplied_hmac = str(event["envelope_hmac"] or "")
            if not re.fullmatch(r"[0-9a-f]{64}", supplied_hmac) or not hmac.compare_digest(supplied_hmac, expected_hmac):
                raise ValueError("record lifecycle event authentication is inconsistent")
            previous = event_binding
        latest = events[-1]
        row_basis = str(row["approval_basis_json"]) if row["approval_basis_json"] is not None else None
        latest_basis = str(latest["approval_basis_json"]) if latest["approval_basis_json"] is not None else None
        if (
            previous != binding
            or str(latest["status"]) != str(row["status"])
            or latest_basis != row_basis
        ):
            raise ValueError("record lifecycle projection does not match authority")
    except (KeyError, TypeError, ValueError, sqlite3.DatabaseError) as exc:
        raise GovernanceError("governance record lifecycle authority is invalid", code="ledger_corrupt") from exc


def _append_record_lifecycle_transition(
    root: Path,
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    status: str,
    approval_basis_json: str | None,
    actor_role: str,
) -> sqlite3.Row:
    """Append and apply one authorized governance status/basis transition."""
    _validate_record_lifecycle(root, connection, row)
    record_ref = str(row["record_ref"])
    current_status = str(row["status"])
    current_basis = str(row["approval_basis_json"]) if row["approval_basis_json"] is not None else None
    target_status = str(status)
    if target_status not in RECORD_STATUSES:
        raise GovernanceError("record status is invalid", code="invalid_record_status")
    if current_status == target_status and current_basis == approval_basis_json:
        return row
    sequence = int(row["lifecycle_sequence"]) + 1
    previous_binding = str(row["lifecycle_binding"])
    binding = ledger_db.governance_lifecycle_binding(
        record_ref=record_ref,
        sequence=sequence,
        previous_binding=previous_binding,
        status=target_status,
        approval_basis_json=approval_basis_json,
    )
    lifecycle_ref = "lifecycle-" + hashlib.sha256(
        f"{record_ref}:{sequence}:{binding}".encode("utf-8")
    ).hexdigest()[:32]
    created_at = _now()
    connection.execute(
        "INSERT INTO governance_record_lifecycle(lifecycle_ref,record_ref,lifecycle_sequence,previous_binding,status,approval_basis_json,binding,action,actor_role,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (lifecycle_ref, record_ref, sequence, previous_binding, target_status, approval_basis_json, binding, "transition", actor_role, created_at),
    )
    ledger_db.insert_governance_lifecycle_auth(
        root,
        connection,
        lifecycle_ref=lifecycle_ref,
        record_ref=record_ref,
        lifecycle_sequence=sequence,
        previous_binding=previous_binding,
        status=target_status,
        approval_basis_json=approval_basis_json,
        binding=binding,
        action="transition",
        actor_role=actor_role,
        created_at=created_at,
    )
    connection.execute(
        "UPDATE governance_records SET status=?,approval_basis_json=?,lifecycle_sequence=?,lifecycle_binding=? WHERE record_ref=?",
        (target_status, approval_basis_json, sequence, binding, record_ref),
    )
    updated = connection.execute("SELECT * FROM governance_records WHERE record_ref=?", (record_ref,)).fetchone()
    if updated is None:
        raise GovernanceError("governance record disappeared during lifecycle transition", code="ledger_corrupt")
    return updated


def _record_from_storage(root: Path, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """Read a record from its immutable artifact and verify the cache.

    ``content_json`` remains in the v9 table only as an indexed migration
    cache.  It is never authoritative after v10: any disagreement
    between that cache and the immutable artifact is ledger corruption.
    """
    _validate_record_lifecycle(root, connection, row)
    record = _record_row(row)
    artifact_ref = str(row["content_artifact_ref"] or "").strip()
    if not artifact_ref:
        raise GovernanceError("governance revision is missing its immutable content artifact", code="ledger_corrupt")
    try:
        metadata = _validate_governance_artifact(
            connection,
            artifact_ref,
            expected_digest=str(row["content_digest"]),
        )
        body = _validated_artifact_payload(connection, metadata)
    except GovernanceError as exc:
        raise GovernanceError("governance record immutable artifact is invalid", code="ledger_corrupt") from exc
    if _digest(body) != str(row["content_digest"]) or _canonical(body) != str(row["content_json"]):
        raise GovernanceError("governance record cache does not match immutable artifact", code="ledger_corrupt")
    record["content_json"] = body
    return record


def _normalise_trigger_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _text_trigger_hits(values: Iterable[Any]) -> dict[str, str]:
    text = " ".join(str(value or "") for value in values).lower()
    patterns: dict[str, tuple[str, ...]] = {
        # Keep trigger terms specific.  A substring such as ``auth`` would
        # classify ordinary prose like "authoring" as a security task.
        "security": ("security", "secure", "authentication", "authorization", "permission", "attack", "безопас", "аутентификац", "авторизац", "разрешени"),
        "privacy": ("privacy", "private data", "personal data", "pii", "персональ", "конфиденциальн", "приватн"),
        "credentials": ("credential", "secret", "password", "token", "api key", "private key", "учетн", "учётн", "парол", "токен", "секрет"),
        "sensitive_data": ("sensitive data", "confidential", "regulated data", "чувствительн", "регулируем данн"),
        "destructive": ("destructive", "delete", "remove", "irreversible", "hard to reverse", "удалени", "удалить", "необратим", "разрушительн"),
        "migration": ("migration", "migrate data", "schema change", "database change", "data move", "миграц", "перенос данн", "изменени схем", "изменить схем"),
        "external_action": ("external action", "publish", "deploy", "send email", "billing", "outside workspace", "публикац", "развертыв", "развёртыв", "внешн", "вне рабочего пространства", "отправк письм"),
        "public_contract": ("public api", "public contract", "wire format", "data format", "публичн api", "публичного api", "публичный api", "публичн контракт", "формат данн"),
        "authorization": ("authorization", "access control", "permission model", "контроль доступ", "модел разрешени"),
        "artifact_integrity": ("artifact integrity", "immutable history", "ledger integrity"),
        "result_integrity": ("result contract", "result integrity"),
        "verification_integrity": ("verification invariant", "verification integrity"),
        "multiple_repos": ("multiple repositories", "several repositories", "multi-repository"),
        "linked_tasks": ("linked tasks", "related tasks", "several cortex tasks", "multiple tasks"),
        "long_lived_lanes": ("long-lived lane", "long lived lane", "conflicting resource", "resource collision"),
        "multi_session_handoff": ("multiple sessions", "multi-session", "session handoff", "cross-session"),
    }
    return {key: key for key, needles in patterns.items() if any(needle in text for needle in needles)}


def classify_governance(
    *,
    complexity: Any = "C2",
    requested_mode: Any = "auto",
    objective: Any = "",
    requirements: Iterable[Any] | None = None,
    scope: Iterable[Any] | None = None,
    allowed_paths: Iterable[Any] | None = None,
    task: dict[str, Any] | None = None,
    initiative_ref: str | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic requested/effective governance classification.

    Only the explicitly documented trigger classes are considered.  The
    resolver accepts structured trigger hints for callers that already have a
    domain classification, but never invents a numeric scope threshold.
    """
    complexity_value = str(complexity or "C2").strip().upper()
    if complexity_value not in {"C1", "C2", "C3"}:
        raise GovernanceError("complexity must be C1, C2, or C3", code="invalid_complexity")
    mode = str(requested_mode or "auto").strip().lower().replace("-", "_")
    if mode not in GOVERNANCE_MODES:
        raise GovernanceError("governance_mode must be auto, required, or off", code="invalid_governance_mode")

    task_values = task if isinstance(task, dict) else {}
    text_values: list[Any] = [objective, *(requirements or []), *(scope or []), *(allowed_paths or [])]
    hits = _text_trigger_hits(text_values)
    structured = (
        task_values.get("risk_triggers")
        if "risk_triggers" in task_values
        else task_values.get("governance_triggers")
    )
    if isinstance(structured, dict):
        for key, value in structured.items():
            if value:
                normalized = _normalise_trigger_key(key)
                if normalized in HARD_TRIGGER_KEYS or normalized in {
                    "multiple_repos", "multiple_repositories", "linked_tasks", "related_tasks",
                    "long_lived_lanes", "conflicting_resources", "multi_session_handoff",
                }:
                    hits[normalized] = "structured trigger"
    elif isinstance(structured, (list, tuple, set)):
        for item in structured:
            normalized = _normalise_trigger_key(item)
            if normalized in HARD_TRIGGER_KEYS or normalized in {
                "multiple_repos", "multiple_repositories", "linked_tasks", "related_tasks",
                "long_lived_lanes", "conflicting_resources", "multi_session_handoff",
            }:
                hits[normalized] = "structured trigger"
    for key in ("multiple_repositories", "related_tasks", "long_lived_lanes", "conflicting_resources", "multi_session_handoff"):
        if task_values.get(key):
            hits[_normalise_trigger_key(key)] = "structured trigger"
    # Counts alone are deliberately not governance triggers.  A coordinator
    # must state the applicable multi-repository/task or resource trigger
    # explicitly; otherwise harmless numeric metadata would silently raise
    # the governance floor.

    reasons: list[str] = []
    if complexity_value == "C3":
        reasons.append("complexity:C3")
    if mode == "required":
        reasons.append("requested:required")
    if hits:
        reasons.extend(f"trigger:{key}" for key in sorted(hits))
    if mode == "off" and (complexity_value != "C1" or hits):
        why = "C2/C3 requires governance" if complexity_value != "C1" else "risk trigger requires full governance"
        raise GovernanceError(f"governance_mode=off is not permitted: {why}", code="governance_off_rejected")
    if mode == "off":
        if not isinstance(structured, dict):
            raise GovernanceError(
                "governance_mode=off requires a complete boolean risk_triggers assessment",
                code="governance_assessment_required",
            )
        normalized_assessment = {_normalise_trigger_key(key): value for key, value in structured.items()}
        missing_assessment = sorted(OFF_ASSESSMENT_KEYS - set(normalized_assessment))
        invalid_assessment = sorted(
            key for key in OFF_ASSESSMENT_KEYS
            if key in normalized_assessment and type(normalized_assessment[key]) is not bool
        )
        if missing_assessment or invalid_assessment:
            detail = []
            if missing_assessment:
                detail.append("missing " + ", ".join(missing_assessment))
            if invalid_assessment:
                detail.append("non-boolean " + ", ".join(invalid_assessment))
            raise GovernanceError(
                "governance_mode=off requires a complete boolean risk_triggers assessment: " + "; ".join(detail),
                code="governance_assessment_required",
            )

    if mode == "required" or complexity_value == "C3" or hits:
        effective = "full"
    elif complexity_value == "C2":
        effective = "light"
    else:
        effective = "minimal"
    if mode == "off":
        effective = "minimal"
        reasons.append("requested:off")
    if not reasons:
        reasons.append(f"baseline:{complexity_value}")

    trigger_evidence = [{"trigger": key, "evidence": hits[key]} for key in sorted(hits)]
    initiative = str(initiative_ref or task_values.get("initiative_ref") or "").strip() or None
    snapshot = dict(policy) if isinstance(policy, dict) else {
        "schema": "cortex/governance-policy/v1",
        "mode_baseline": {"C1": "minimal", "C2": "light", "C3": "full"},
        "required_floor": "full",
        "automatic_full_triggers": sorted(hits),
        "promotion_window_days": 90,
        "promotion_threshold_scopes": 3,
    }
    if mode == "off":
        snapshot["off_assessment"] = {
            key: normalized_assessment[key] for key in sorted(OFF_ASSESSMENT_KEYS)
        }
    close_obligations = {
        "minimal": ["verification_evidence", "audit_receipt"],
        "light": ["policy_snapshot", "decision_assumption_risk_evidence", "process_reflection", "verification_evidence"],
        "full": ["acceptance_oracle_evidence", "risk_register", "falsification_strategy", "independent_governance_review", "retrospective", "verification_evidence", "audit_receipt"],
    }[effective]
    return {
        "schema": GOVERNANCE_SCHEMA,
        "requested_mode": mode,
        "effective_mode": effective,
        "complexity": complexity_value,
        "reasons": reasons,
        "trigger_evidence": trigger_evidence,
        "initiative_ref": initiative,
        "autonomous_scope_ref": initiative or "governance-scope-autonomous",
        "policy_snapshot": snapshot,
        "policy_snapshot_digest": _digest(snapshot),
        "close_obligations": close_obligations,
    }


def resolve_governance(root: Path | None = None, **kwargs: Any) -> dict[str, Any]:
    """Classify a task and, when supplied, verify its initiative reference."""
    task_value = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
    supplied_initiative = kwargs.get("initiative_ref") or task_value.get("initiative_ref")
    if supplied_initiative:
        _safe_ref(supplied_initiative, "initiative_ref", prefix="initiative-")
    result = classify_governance(**kwargs)
    if root is not None and result.get("initiative_ref"):
        ensure = ledger_db.ensure_database
        ensure(root)
        with _connection(root) as connection:
            exists = connection.execute(
                "SELECT 1 FROM initiatives WHERE initiative_ref = ?", (result["initiative_ref"],)
            ).fetchone()
        if exists is None:
            raise GovernanceError("initiative_ref does not identify an existing initiative", code="initiative_not_found")
    return result


def _initiative_depth(connection: sqlite3.Connection, parent_ref: str | None) -> int:
    depth = 1
    seen: set[str] = set()
    current = parent_ref
    while current:
        if current in seen:
            raise GovernanceError("initiative hierarchy contains a cycle", code="initiative_cycle")
        seen.add(current)
        row = connection.execute("SELECT parent_ref FROM initiatives WHERE initiative_ref = ?", (current,)).fetchone()
        if row is None:
            raise GovernanceError("parent initiative does not exist", code="initiative_parent_not_found")
        depth += 1
        current = str(row["parent_ref"] or "") or None
    return depth


def create_initiative(
    root: Path,
    *,
    title: str,
    goal: str,
    owner: str,
    risk: str = "moderate",
    initiative_ref: str | None = None,
    parent_ref: str | None = None,
    acceptance_oracle_artifact_ref: str | None = None,
) -> dict[str, Any]:
    title_value = _bounded_governance_text(title, label="initiative title", required=True)
    goal_value = _bounded_governance_text(goal, label="initiative goal", required=True)
    owner_value = _bounded_governance_text(owner, label="initiative owner", required=True)
    risk_value = str(risk or "moderate").strip().lower()
    if risk_value not in {"low", "moderate", "high", "critical"}:
        raise GovernanceError("initiative risk is invalid", code="initiative_risk_invalid")
    parent = str(parent_ref or "").strip() or None
    if parent:
        _safe_ref(parent, "parent_ref", prefix="initiative-")
    ref = str(initiative_ref or "").strip()
    if not ref:
        ref = "initiative-" + hashlib.sha256(f"{title_value}\0{goal_value}\0{owner_value}\0{_now()}".encode()).hexdigest()[:20]
    _safe_ref(ref, "initiative_ref", prefix="initiative-")
    # Validate all caller-controlled persistent text before creating/opening
    # the ledger writer.
    ledger_db.ensure_database(root)
    now = _now()
    with _connection(root, write=True) as connection:
        if connection.execute("SELECT 1 FROM initiatives WHERE initiative_ref = ?", (ref,)).fetchone() is not None:
            existing = connection.execute("SELECT * FROM initiatives WHERE initiative_ref = ?", (ref,)).fetchone()
            replay_matches = bool(existing) and all(
                (
                    str(existing[key] or "") if key in {"parent_ref", "acceptance_oracle_artifact_ref"}
                    else str(existing[key])
                ) == value
                for key, value in (
                    ("title", title_value),
                    ("goal", goal_value),
                    ("owner", owner_value),
                    ("risk", risk_value),
                    ("parent_ref", parent or ""),
                    ("acceptance_oracle_artifact_ref", str(acceptance_oracle_artifact_ref or "")),
                )
            )
            if replay_matches:
                return _initiative_row(existing)
            raise GovernanceError("initiative_ref already belongs to a different initiative", code="initiative_replay_conflict")
        if parent and connection.execute("SELECT 1 FROM initiatives WHERE initiative_ref = ?", (parent,)).fetchone() is None:
            raise GovernanceError("parent initiative does not exist", code="initiative_parent_not_found")
        if _initiative_depth(connection, parent) > 3:
            raise GovernanceError("initiative hierarchy is limited to three levels", code="initiative_depth_exceeded")
        connection.execute(
            "INSERT INTO initiatives(initiative_ref,parent_ref,title,goal,owner,risk,acceptance_oracle_artifact_ref,status,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ref, parent, title_value, goal_value, owner_value, risk_value, acceptance_oracle_artifact_ref, "proposed", 1, now, now),
        )
        row = connection.execute("SELECT * FROM initiatives WHERE initiative_ref = ?", (ref,)).fetchone()
    return _initiative_row(row) if row else {"initiative_ref": ref}


def inspect_initiative(root: Path, initiative_ref: str) -> dict[str, Any] | None:
    ledger_db.ensure_database(root)
    ref = _safe_ref(initiative_ref, "initiative_ref", prefix="initiative-")
    with _connection(root) as connection:
        row = connection.execute("SELECT * FROM initiatives WHERE initiative_ref = ?", (ref,)).fetchone()
        if row is None:
            return None
        result = _initiative_row(row)
        result["children"] = [
            _initiative_row(item)
            for item in connection.execute("SELECT * FROM initiatives WHERE parent_ref = ? ORDER BY initiative_ref", (ref,)).fetchall()
        ]
        result["task_links"] = [
            dict(item)
            for item in connection.execute("SELECT * FROM initiative_task_links WHERE initiative_ref = ? ORDER BY task_id, relationship", (ref,)).fetchall()
        ]
        result["dependencies"] = [
            dict(item)
            for item in connection.execute("SELECT * FROM initiative_dependencies WHERE source_ref = ? OR target_ref = ? ORDER BY dependency_ref", (ref, ref)).fetchall()
        ]
    return result


def link_task(
    root: Path,
    *,
    initiative_ref: str,
    task_id: str,
    relationship: str,
    milestone: str | None = None,
    deliverable: str | None = None,
    corrective: bool = False,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    ledger_db.ensure_database(root)
    ref = _safe_ref(initiative_ref, "initiative_ref", prefix="initiative-")
    task = _safe_ref(task_id, "task_id")
    relation = str(relationship or "").strip().lower()
    if relation not in LINK_RELATIONSHIPS:
        raise GovernanceError("initiative task relationship must be milestone, deliverable, or corrective", code="invalid_link")
    with _connection(root, write=True) as connection:
        initiative = connection.execute("SELECT revision,status FROM initiatives WHERE initiative_ref = ?", (ref,)).fetchone()
        if initiative is None:
            raise GovernanceError("initiative does not exist", code="initiative_not_found")
        task_row = connection.execute("SELECT status FROM tasks WHERE task_id = ?", (task,)).fetchone()
        if task_row is None:
            raise GovernanceError("task does not exist", code="task_not_found")
        current_revision = int(initiative["revision"])
        existing = connection.execute(
            "SELECT * FROM initiative_task_links WHERE initiative_ref = ? AND task_id = ? AND relationship = ?",
            (ref, task, relation),
        ).fetchone()
        if existing is not None:
            if str(existing["milestone"] or "") == str(milestone or "") and str(existing["deliverable"] or "") == str(deliverable or "") and bool(existing["corrective"]) == bool(corrective):
                return dict(existing)
            raise GovernanceError("initiative task link replay conflicts with existing link", code="link_replay_conflict")
        if (
            str(initiative["status"]) in {"completed", "closed"}
            and relation in {"milestone", "deliverable"}
            and str(task_row["status"]) != "completed"
        ):
            raise GovernanceError(
                "terminal initiative requires linked milestone/deliverable task terminal success",
                code="linked_task_unresolved",
            )
        if expected_revision is not None and int(expected_revision) != current_revision:
            raise GovernanceError("initiative revision is stale", code="stale_revision")
        now = _now()
        connection.execute(
            "INSERT INTO initiative_task_links(initiative_ref,task_id,relationship,milestone,deliverable,corrective,expected_revision,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (ref, task, relation, milestone, deliverable, int(bool(corrective or relation == "corrective")), current_revision, now),
        )
        connection.execute("UPDATE initiatives SET revision = revision + 1, updated_at = ? WHERE initiative_ref = ?", (now, ref))
        row = connection.execute(
            "SELECT * FROM initiative_task_links WHERE initiative_ref = ? AND task_id = ? AND relationship = ?",
            (ref, task, relation),
        ).fetchone()
    return dict(row) if row else {"initiative_ref": ref, "task_id": task, "relationship": relation}


def _endpoint_exists(connection: sqlite3.Connection, endpoint_type: str, endpoint_ref: str) -> bool:
    if endpoint_type == "initiative":
        return connection.execute("SELECT 1 FROM initiatives WHERE initiative_ref = ?", (endpoint_ref,)).fetchone() is not None
    return connection.execute("SELECT 1 FROM tasks WHERE task_id = ?", (endpoint_ref,)).fetchone() is not None


def _dependency_cycle(connection: sqlite3.Connection, source: tuple[str, str], target: tuple[str, str]) -> bool:
    # Dependencies are directed source -> target.  Adding source -> target
    # creates a cycle when target can already reach source.
    edges: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for row in connection.execute("SELECT source_type,source_ref,target_type,target_ref FROM initiative_dependencies"):
        edges.setdefault((str(row["source_type"]), str(row["source_ref"])), set()).add((str(row["target_type"]), str(row["target_ref"])))
    edges.setdefault(source, set()).add(target)
    stack = [target]
    visited: set[tuple[str, str]] = set()
    while stack:
        current = stack.pop()
        if current == source:
            return True
        if current in visited:
            continue
        visited.add(current)
        stack.extend(edges.get(current, set()))
    return False


def add_dependency(
    root: Path,
    *,
    source_type: str,
    source_ref: str,
    target_type: str,
    target_ref: str,
    dependency_type: str = "blocks",
    dependency_ref: str | None = None,
) -> dict[str, Any]:
    ledger_db.ensure_database(root)
    source_kind, target_kind = str(source_type or "").lower(), str(target_type or "").lower()
    if source_kind not in {"initiative", "task"} or target_kind not in {"initiative", "task"}:
        raise GovernanceError("dependency endpoints must be initiative or task", code="invalid_dependency_endpoint")
    src = _safe_ref(source_ref, "source_ref", prefix="initiative-" if source_kind == "initiative" else None)
    dst = _safe_ref(target_ref, "target_ref", prefix="initiative-" if target_kind == "initiative" else None)
    kind = str(dependency_type or "blocks").lower()
    if kind not in DEPENDENCY_TYPES:
        raise GovernanceError("dependency_type is invalid", code="invalid_dependency_type")
    ref = str(dependency_ref or "").strip() or "dependency-" + hashlib.sha256(f"{source_kind}:{src}:{target_kind}:{dst}:{kind}".encode()).hexdigest()[:20]
    _safe_ref(ref, "dependency_ref", prefix="dependency-")
    with _connection(root, write=True) as connection:
        if not _endpoint_exists(connection, source_kind, src) or not _endpoint_exists(connection, target_kind, dst):
            raise GovernanceError("dependency endpoint does not exist", code="dependency_endpoint_not_found")
        existing = connection.execute("SELECT * FROM initiative_dependencies WHERE dependency_ref = ?", (ref,)).fetchone()
        if existing is not None:
            if all(str(existing[key]) == value for key, value in (("source_type", source_kind), ("source_ref", src), ("target_type", target_kind), ("target_ref", dst), ("dependency_type", kind))):
                return dict(existing)
            raise GovernanceError("dependency_ref replay conflicts with existing edge", code="dependency_replay_conflict")
        duplicate = connection.execute(
            "SELECT * FROM initiative_dependencies WHERE source_type=? AND source_ref=? AND target_type=? AND target_ref=? AND dependency_type=?",
            (source_kind, src, target_kind, dst, kind),
        ).fetchone()
        if duplicate is not None:
            return dict(duplicate)
        if _dependency_cycle(connection, (source_kind, src), (target_kind, dst)):
            raise GovernanceError("initiative/task dependency would create a cycle", code="dependency_cycle")
        created = _now()
        connection.execute(
            "INSERT INTO initiative_dependencies(dependency_ref,source_type,source_ref,target_type,target_ref,dependency_type,created_at) VALUES(?,?,?,?,?,?,?)",
            (ref, source_kind, src, target_kind, dst, kind, created),
        )
        row = connection.execute("SELECT * FROM initiative_dependencies WHERE dependency_ref = ?", (ref,)).fetchone()
    return dict(row) if row else {"dependency_ref": ref}


def _unresolved_completion_dependencies(connection: sqlite3.Connection, initiative_ref: str) -> list[str]:
    """Return blocking/requires edges whose target has not reached done state."""
    unresolved: list[str] = []
    rows = connection.execute(
        "SELECT dependency_ref,target_type,target_ref FROM initiative_dependencies "
        "WHERE source_type='initiative' AND source_ref=? AND dependency_type IN ('blocks','requires') "
        "ORDER BY dependency_ref",
        (initiative_ref,),
    ).fetchall()
    for dependency in rows:
        target_type = str(dependency["target_type"])
        target_ref = str(dependency["target_ref"])
        if target_type == "initiative":
            target = connection.execute("SELECT status FROM initiatives WHERE initiative_ref=?", (target_ref,)).fetchone()
        else:
            target = connection.execute("SELECT status FROM tasks WHERE task_id=?", (target_ref,)).fetchone()
        if target is None or str(target["status"]) not in {"completed", "closed"}:
            unresolved.append(str(dependency["dependency_ref"]))
    return unresolved


def _unresolved_linked_task_completions(connection: sqlite3.Connection, initiative_ref: str) -> list[str]:
    """Return milestone/deliverable tasks that have not durably succeeded.

    A task's ``completed`` ledger status is the lifecycle's terminal success
    state.  `blocked`, `cancelled`, and an absent task row must never be
    interpreted as delivery merely because an initiative has its own status
    transition or independent governance-close evidence.
    """
    rows = connection.execute(
        "SELECT links.task_id,links.relationship,tasks.status FROM initiative_task_links AS links "
        "LEFT JOIN tasks ON tasks.task_id=links.task_id "
        "WHERE links.initiative_ref=? AND links.relationship IN ('milestone','deliverable') "
        "ORDER BY links.task_id,links.relationship",
        (initiative_ref,),
    ).fetchall()
    return [
        f"{row['relationship']}:{row['task_id']}"
        for row in rows
        if row["status"] is None or str(row["status"]) != "completed"
    ]


_CLOSE_EVIDENCE_KEYS = (
    "oracle_evidence",
    "risk_disposition",
    "falsification_review",
    "retrospective",
    "independent_review",
)


def _close_evidence_ref(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("artifact_ref", "content_artifact_ref", "evidence_ref", "record_ref", "ref"):
        candidate = str(value.get(key) or "").strip()
        if candidate:
            return candidate
    nested = value.get("artifact")
    if isinstance(nested, dict):
        return _close_evidence_ref(nested)
    return None


def _validate_independent_review_attestation(
    connection: sqlite3.Connection,
    *,
    initiative_ref: str,
    owner: str,
    initiative_revision: int,
    metadata: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Bind close review evidence to the canonical reviewer attempt/session."""
    task_id = str(metadata.get("task_id") or payload.get("task_id") or "").strip()
    attempt_id = str(payload.get("attempt_id") or "").strip()
    result_ref = str(payload.get("attempt_result_ref") or "").strip()
    if not task_id or str(payload.get("task_id") or task_id).strip() != task_id or not attempt_id or not result_ref:
        raise GovernanceError("independent review evidence lacks a canonical task, attempt, or result", code="review_attestation_invalid")
    if connection.execute(
        "SELECT 1 FROM initiative_task_links WHERE initiative_ref=? AND task_id=? LIMIT 1",
        (initiative_ref, task_id),
    ).fetchone() is None:
        raise GovernanceError("independent review task is outside the initiative", code="review_attestation_invalid")
    task_row = connection.execute("SELECT state_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if task_row is None:
        raise GovernanceError("independent review task is unavailable", code="review_attestation_invalid")
    try:
        state = json.loads(str(task_row["state_json"]))
    except json.JSONDecodeError as exc:
        raise GovernanceError("independent review task state is invalid", code="review_attestation_invalid") from exc
    attempt = next(
        (
            item for item in state.get("attempts", [])
            if isinstance(item, dict)
            and str(item.get("attempt_id") or "") == attempt_id
            and not item.get("invalidated")
        ),
        None,
    )
    if (
        not isinstance(attempt, dict)
        or str(attempt.get("gate") or "") != "governance_close"
        or str(attempt.get("agent") or "") != "code_reviewer"
        or str(attempt.get("status") or "") != "passed"
        or result_ref != str(attempt.get("attempt_result_ref") or "")
    ):
        raise GovernanceError("independent review is not backed by a passed governance_close reviewer attempt", code="review_attestation_invalid")
    canonical_result = connection.execute(
        "SELECT result_ref,result_status,lifecycle_status,workspace_observation_json,changed_files_status "
        "FROM attempt_results WHERE result_ref=? AND task_id=? AND attempt_id=?",
        (result_ref, task_id, attempt_id),
    ).fetchone()
    if (
        canonical_result is None
        or str(canonical_result["result_status"]) != "completed"
        or str(canonical_result["lifecycle_status"]) != "COMPLETED"
        or str(canonical_result["changed_files_status"]) != "server_observed"
    ):
        raise GovernanceError("independent review is not backed by a finalized server-observed attempt result", code="review_attestation_invalid")
    workspace_observation = _strict_json_loads(
        str(canonical_result["workspace_observation_json"]), "attempt workspace observation",
    )
    if not isinstance(workspace_observation, dict) or not (
        workspace_observation.get("complete") is True
        and workspace_observation.get("safe_to_attribute") is True
    ):
        raise GovernanceError("independent review requires a complete server workspace observation", code="review_attestation_invalid")
    observed_verification = connection.execute(
        "SELECT 1 FROM attempt_events WHERE task_id=? AND attempt_id=? "
        "AND event_type='verification_observed' AND actor='cortex' LIMIT 1",
        (task_id, attempt_id),
    ).fetchone()
    if observed_verification is None:
        raise GovernanceError("independent review requires a server verification observation", code="review_attestation_invalid")
    session = connection.execute(
        "SELECT host_agent_id, host_task_name, status FROM worker_sessions "
        "WHERE task_id=? AND attempt_id=? AND status='completed' ORDER BY generation DESC LIMIT 1",
        (task_id, attempt_id),
    ).fetchone()
    reviewer = str(session["host_agent_id"] or "").strip() if session is not None else ""
    declared_reviewer = str(
        payload.get("reviewer_identity") or payload.get("reviewer_id") or payload.get("reviewer") or ""
    ).strip()
    if not reviewer or reviewer == owner or (declared_reviewer and declared_reviewer != reviewer):
        raise GovernanceError("independent review is not bound to an independent completed worker session", code="review_attestation_invalid")
    try:
        reviewed_initiative_revision = int(payload.get("reviewed_initiative_revision"))
    except (TypeError, ValueError) as exc:
        raise GovernanceError("independent review must bind the initiative revision", code="review_attestation_invalid") from exc
    if reviewed_initiative_revision != initiative_revision:
        raise GovernanceError("independent review is stale for the current initiative revision", code="review_attestation_invalid")
    reviewed_task_revisions = payload.get("reviewed_task_revisions")
    if not isinstance(reviewed_task_revisions, dict):
        raise GovernanceError("independent review must bind reviewed task revisions", code="review_attestation_invalid")
    linked_tasks = connection.execute(
        "SELECT links.task_id, tasks.revision FROM initiative_task_links AS links "
        "JOIN tasks ON tasks.task_id=links.task_id WHERE links.initiative_ref=? GROUP BY links.task_id",
        (initiative_ref,),
    ).fetchall()
    expected_task_revisions = {str(item["task_id"]): int(item["revision"]) for item in linked_tasks}
    actual_task_revisions: dict[str, int] = {}
    try:
        actual_task_revisions = {str(key): int(value) for key, value in reviewed_task_revisions.items()}
    except (TypeError, ValueError) as exc:
        raise GovernanceError("independent review task revisions are invalid", code="review_attestation_invalid") from exc
    if actual_task_revisions != expected_task_revisions:
        raise GovernanceError("independent review is stale for linked task revisions", code="review_attestation_invalid")
    reviewed_artifacts = payload.get("reviewed_artifact_digests")
    if not isinstance(reviewed_artifacts, dict) or not reviewed_artifacts:
        raise GovernanceError("independent review must bind reviewed immutable artifact digests", code="review_attestation_invalid")
    for artifact_ref, digest in reviewed_artifacts.items():
        reference = str(artifact_ref or "").strip()
        expected_digest = str(digest or "").strip().lower()
        if not reference or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            raise GovernanceError("independent review artifact digest binding is invalid", code="review_attestation_invalid")
        _validate_governance_artifact(
            connection,
            reference,
            expected_digest=expected_digest,
            initiative_ref=initiative_ref,
        )


def _validate_close_evidence(
    connection: sqlite3.Connection,
    initiative: sqlite3.Row,
    evidence: dict[str, Any],
) -> None:
    """Validate server-resolved, scoped, immutable initiative-close evidence."""
    initiative_ref = str(initiative["initiative_ref"])
    owner = str(initiative["owner"] or "").strip()
    missing: list[str] = []
    used_artifacts: set[str] = set()
    for key in _CLOSE_EVIDENCE_KEYS:
        item = evidence.get(key)
        if not isinstance(item, dict):
            missing.append(key)
            continue
        artifact_ref = _close_evidence_ref(item)
        digest = str(item.get("digest") or item.get("digest_sha256") or "").strip().lower()
        scope = str(item.get("scope_ref") or item.get("initiative_ref") or "").strip()
        if not artifact_ref or not digest or scope != initiative_ref:
            missing.append(key)
            continue
        # Raw blob ids are not task/initiative-scoped evidence.  Require a
        # logical artifact or governance record so the server can establish
        # how the bytes belong to this initiative.
        if not (artifact_ref.startswith("artifact-") or artifact_ref.startswith("record-")):
            missing.append(key)
            continue
        if artifact_ref in used_artifacts:
            # One generic artifact must not satisfy every independent close
            # obligation.  Each proof is independently typed and auditable.
            missing.append(key)
            continue
        try:
            metadata = _validate_governance_artifact(
                connection,
                artifact_ref,
                expected_digest=digest,
                initiative_ref=initiative_ref,
            )
            task_id = str(metadata.get("task_id") or "").strip()
            if task_id:
                linked = connection.execute(
                    "SELECT 1 FROM initiative_task_links WHERE initiative_ref = ? AND task_id = ? LIMIT 1",
                    (initiative_ref, task_id),
                ).fetchone()
                if linked is None:
                    raise GovernanceError("governance evidence task is not linked to the initiative", code="artifact_scope_mismatch")
            payload = _validated_artifact_payload(connection, metadata)
            if not isinstance(payload, dict):
                raise GovernanceError("governance evidence artifact must contain an object", code="artifact_schema_invalid")
            payload_scope = str(
                payload.get("initiative_ref")
                or payload.get("scope_ref")
                or payload.get("governance_scope_ref")
                or ""
            ).strip()
            if payload_scope and payload_scope != initiative_ref:
                raise GovernanceError("governance evidence payload is outside the initiative scope", code="artifact_scope_mismatch")
            obligation = str(
                payload.get("obligation")
                or payload.get("evidence_type")
                or payload.get("governance_obligation")
                or ""
            ).strip().lower()
            if obligation not in _CLOSE_EVIDENCE_TYPES.get(key, set()):
                raise GovernanceError("governance evidence artifact has the wrong obligation type", code="artifact_type_mismatch")
            if metadata.get("record_ref"):
                record_scope = str(metadata.get("record_initiative_ref") or "").strip()
                record_task = str(metadata.get("record_task_id") or "").strip()
                if record_scope and record_scope != initiative_ref:
                    raise GovernanceError("governance evidence record is outside the initiative scope", code="artifact_scope_mismatch")
                if record_task and not task_id:
                    linked = connection.execute(
                        "SELECT 1 FROM initiative_task_links WHERE initiative_ref = ? AND task_id = ? LIMIT 1",
                        (initiative_ref, record_task),
                    ).fetchone()
                    if linked is None:
                        raise GovernanceError("governance evidence record task is not linked to the initiative", code="artifact_scope_mismatch")
        except GovernanceError:
            missing.append(key)
            continue
        used_artifacts.add(artifact_ref)
        if key == "oracle_evidence":
            declared_oracle = str(initiative["acceptance_oracle_artifact_ref"] or "").strip()
            if declared_oracle and artifact_ref != declared_oracle:
                missing.append(key)
        if key == "independent_review":
            try:
                _validate_independent_review_attestation(
                    connection,
                    initiative_ref=initiative_ref,
                    owner=owner,
                    initiative_revision=int(initiative["revision"]),
                    metadata=metadata,
                    payload=payload,
                )
            except GovernanceError:
                missing.append(key)
    if missing:
        raise GovernanceError(
            "initiative close requires immutable, digest-bound, initiative-scoped oracle, risk, falsifier, retrospective, and independent review evidence: "
            + ", ".join(dict.fromkeys(missing)),
            code="close_evidence_required",
        )


def transition_initiative(
    root: Path,
    *,
    initiative_ref: str,
    status: str,
    expected_revision: int | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ledger_db.ensure_database(root)
    ref = _safe_ref(initiative_ref, "initiative_ref", prefix="initiative-")
    target = str(status or "").lower()
    if target not in INITIATIVE_STATUSES:
        raise GovernanceError("initiative status is invalid", code="invalid_initiative_status")
    evidence_value = evidence if isinstance(evidence, dict) else {}
    with _connection(root, write=True) as connection:
        row = connection.execute("SELECT * FROM initiatives WHERE initiative_ref = ?", (ref,)).fetchone()
        if row is None:
            raise GovernanceError("initiative does not exist", code="initiative_not_found")
        current = str(row["status"])
        revision = int(row["revision"])
        if expected_revision is not None and int(expected_revision) != revision:
            raise GovernanceError("initiative revision is stale", code="stale_revision")
        # Replaying an already-applied transition is safe and must not create
        # a new revision or unexpectedly demand close evidence a second time.
        if target == current:
            return _initiative_row(row)
        allowed = {
            "proposed": {"active", "cancelled"},
            "active": {"blocked", "completed", "cancelled"},
            "blocked": {"active", "cancelled"},
            "completed": {"closed"},
            "closed": set(),
            "cancelled": set(),
        }
        if target != current and target not in allowed.get(current, set()):
            raise GovernanceError(f"initiative cannot transition from {current} to {target}", code="invalid_transition")
        if target in {"completed", "closed"}:
            unresolved = _unresolved_completion_dependencies(connection, ref)
            if unresolved:
                raise GovernanceError(
                    "initiative has unresolved blocks/requires dependencies: " + ", ".join(unresolved),
                    code="dependency_unresolved",
                )
            incomplete_tasks = _unresolved_linked_task_completions(connection, ref)
            if incomplete_tasks:
                raise GovernanceError(
                    "initiative completion requires terminal success for linked milestone/deliverable tasks: "
                    + ", ".join(incomplete_tasks),
                    code="linked_task_unresolved",
                )
        if target == "closed":
            _validate_close_evidence(connection, row, evidence_value)
        now = _now()
        connection.execute("UPDATE initiatives SET status=?, revision=revision+1, updated_at=? WHERE initiative_ref=?", (target, now, ref))
        result = connection.execute("SELECT * FROM initiatives WHERE initiative_ref = ?", (ref,)).fetchone()
    return _initiative_row(result) if result else {"initiative_ref": ref, "status": target}


def create_record(
    root: Path,
    *,
    record_type: str,
    content: Any,
    initiative_ref: str | None = None,
    task_id: str | None = None,
    created_by: str = "coordinator",
    status: str | None = None,
    supersedes: str | None = None,
    expires_at: str | None = None,
    approval_basis: Any = None,
    content_artifact_ref: str | None = None,
    record_ref: str | None = None,
    actor_role: str | None = None,
    submission_id: str | None = None,
) -> dict[str, Any]:
    # Promotion evaluation may call this helper while holding the one-writer
    # connection. Re-running migration setup on a nested connection would
    # try to acquire a second BEGIN IMMEDIATE and self-deadlock SQLite.
    kind = str(record_type or "").lower()
    if kind not in RECORD_TYPES:
        raise GovernanceError("record_type is invalid", code="invalid_record_type")
    created_by_value = _bounded_governance_text(created_by or "coordinator", label="governance created_by") or "coordinator"
    role = str(actor_role or ("coordinator" if created_by_value.lower() in {"coordinator", "system"} else "worker")).strip().lower()
    if role not in ACTOR_ROLES:
        raise GovernanceError("actor_role must be coordinator, worker, or reviewer", code="invalid_actor_role")
    initiative = str(initiative_ref or "").strip() or None
    task = str(task_id or "").strip() or None
    if initiative is None and task is None and kind not in {"policy", "promotion"}:
        raise GovernanceError(
            "governance record requires initiative_ref or task_id; only project policy and promotion records may use project scope",
            code="record_scope_required",
        )
    if initiative:
        _safe_ref(initiative, "initiative_ref", prefix="initiative-")
    if task:
        _safe_ref(task, "task_id")
    status_value = str(status or ("pending" if kind in {"promotion", "exception"} else "active")).lower()
    if kind == "policy" and status is None:
        status_value = "pending"
    if status_value not in RECORD_STATUSES:
        raise GovernanceError("record status is invalid", code="invalid_record_status")
    if role != "coordinator" and kind in {"policy", "promotion", "exception"} and status_value in {"active", "approved"}:
        raise GovernanceError("worker and reviewer proposals cannot approve or activate policy governance records", code="coordinator_approval_required")
    body = content
    body_json = _bounded_content_json(body, label="governance content")
    digest = _digest(body)
    ref = str(record_ref or "").strip() or "record-" + hashlib.sha256(f"{kind}:{initiative}:{task}:{digest}:{_now()}".encode()).hexdigest()[:24]
    _safe_ref(ref, "record_ref", prefix="record-")
    submission = str(submission_id or "").strip() or None
    if submission:
        _safe_ref(submission, "submission_id")
    approval_json = (
        _bounded_content_json(approval_basis, label="governance approval_basis")
        if approval_basis is not None
        else None
    )
    # All direct caller content has now passed its strict byte/shape boundary.
    # Only then may this helper initialize or enter the durable ledger.
    if not ledger_db.in_transaction(root):
        ledger_db.ensure_database(root)
    scope = _scope_key(initiative, task)
    # Submission idempotency represents caller intent, not server-derived
    # retention timestamps or generated record refs.  In particular, retries
    # of a sensitive-record request must not fail merely because ``now`` has
    # advanced while calculating its retention window.
    submission_command_digest = _digest({
        "scope_key": scope,
        "initiative_ref": initiative,
        "task_id": task,
        "record_type": kind,
        "status": status_value,
        "supersedes": str(supersedes or "").strip() or None,
        "content_digest": digest,
        "content_artifact_ref": str(content_artifact_ref or "").strip() or None,
        "approval_basis_json": approval_json,
        "created_by": created_by_value,
        "expires_at_requested": str(expires_at or "").strip() or None,
    })
    with _connection(root, write=True) as connection:
        if initiative and connection.execute("SELECT 1 FROM initiatives WHERE initiative_ref = ?", (initiative,)).fetchone() is None:
            raise GovernanceError("initiative does not exist", code="initiative_not_found")
        if task and connection.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task,)).fetchone() is None:
            raise GovernanceError("task does not exist", code="task_not_found")
        if initiative and task and connection.execute(
            "SELECT 1 FROM initiative_task_links WHERE initiative_ref=? AND task_id=? LIMIT 1",
            (initiative, task),
        ).fetchone() is None:
            raise GovernanceError("task is not linked to the governance initiative", code="record_scope_mismatch")
        record_expires_at = expires_at
        if kind != "policy" and _contains_sensitive_marker(body):
            sensitive_policy = _sensitive_record_policy(
                root,
                connection,
                initiative_ref=initiative,
                task_id=task,
                record_type=kind,
            )
            if sensitive_policy is None:
                raise GovernanceError(
                    "sensitive governance records require an approved policy for this record type with retention and access controls",
                    code="sensitive_policy_required",
                )
            allowed_roles = set(sensitive_policy["allowed_roles"])
            if role not in allowed_roles:
                raise GovernanceError("sensitive governance record actor is not allowed by policy", code="sensitive_access_denied")
            _validate_sensitive_field_policy(body, sensitive_policy)
            created_at = datetime.now(timezone.utc)
            latest_expiry = created_at + timedelta(days=int(sensitive_policy["retention_days"]))
            if sensitive_policy.get("policy_expires_at"):
                latest_expiry = min(
                    latest_expiry,
                    _parse_timestamp(sensitive_policy["policy_expires_at"], "policy expires_at"),
                )
            if latest_expiry <= created_at:
                raise GovernanceError(
                    "sensitive governance policy has no remaining retention window",
                    code="sensitive_policy_expired",
                )
            if record_expires_at:
                supplied_expiry = _parse_timestamp(record_expires_at, "expires_at")
                if supplied_expiry <= created_at or supplied_expiry > latest_expiry:
                    raise GovernanceError(
                        "sensitive governance record expiry must be in the future and within policy retention",
                        code="sensitive_retention_exceeded",
                    )
                record_expires_at = supplied_expiry.isoformat()
            else:
                record_expires_at = latest_expiry.isoformat()
        elif record_expires_at:
            record_expires_at = _parse_timestamp(record_expires_at, "expires_at").isoformat()
        command_envelope = {
            "record_ref": ref,
            "scope_key": scope,
            "initiative_ref": initiative,
            "task_id": task,
            "record_type": kind,
            "status": status_value,
            "supersedes": str(supersedes or "").strip() or None,
            "content_digest": digest,
            "content_artifact_ref": str(content_artifact_ref or "").strip() or None,
            "approval_basis_json": approval_json,
            "created_by": created_by_value,
            "expires_at": record_expires_at,
        }
        command_digest = submission_command_digest
        if submission:
            submitted = connection.execute(
                "SELECT command_digest, record_ref FROM governance_submissions WHERE submission_id=?",
                (submission,),
            ).fetchone()
            if submitted is not None:
                if str(submitted["command_digest"]) != command_digest:
                    raise GovernanceError("submission_id replay conflicts with existing command", code="submission_replay_conflict")
                stored = connection.execute("SELECT * FROM governance_records WHERE record_ref=?", (submitted["record_ref"],)).fetchone()
                if stored is None:
                    raise GovernanceError("submission points to a missing governance record", code="ledger_corrupt")
                return _record_from_storage(root, connection, stored)
        existing = connection.execute("SELECT * FROM governance_records WHERE record_ref = ?", (ref,)).fetchone()
        if existing is not None:
            existing_envelope = {
                "record_ref": str(existing["record_ref"]),
                "scope_key": str(existing["scope_key"]),
                "initiative_ref": str(existing["initiative_ref"] or "") or None,
                "task_id": str(existing["task_id"] or "") or None,
                "record_type": str(existing["record_type"]),
                "status": str(existing["status"]),
                "supersedes": str(existing["supersedes"] or "") or None,
                "content_digest": str(existing["content_digest"]),
                # A server-created blob is not caller-authored; only an
                # explicit supplied artifact participates in replay equality.
                "content_artifact_ref": str(existing["content_artifact_ref"] or "") if content_artifact_ref else None,
                "approval_basis_json": str(existing["approval_basis_json"] or "") or None,
                "created_by": str(existing["created_by"]),
                "expires_at": str(existing["expires_at"] or "") or None,
            }
            if existing_envelope == command_envelope:
                result = _record_from_storage(root, connection, existing)
                if submission:
                    connection.execute(
                        "INSERT INTO governance_submissions(submission_id,command_digest,record_ref,created_at) VALUES(?,?,?,?)",
                        (submission, command_digest, ref, _now()),
                    )
                return result
            raise GovernanceError("record_ref replay conflicts with existing record", code="record_replay_conflict")
        if supersedes:
            supersedes_ref = _safe_ref(supersedes, "supersedes", prefix="record-")
            predecessor = connection.execute("SELECT * FROM governance_records WHERE record_ref = ?", (supersedes_ref,)).fetchone()
            if predecessor is None:
                raise GovernanceError("supersedes record does not exist", code="supersedes_not_found")
            if str(predecessor["record_type"]) != kind or str(predecessor["scope_key"]) != scope:
                raise GovernanceError("supersedes must stay within one record scope and type", code="supersedes_scope_mismatch")
            if connection.execute("SELECT 1 FROM governance_records WHERE supersedes=? LIMIT 1", (supersedes_ref,)).fetchone() is not None:
                raise GovernanceError("superseded revision already has a successor", code="supersedes_conflict")
            revision = int(predecessor["revision"]) + 1
            if status_value in {"active", "approved"}:
                current_ref: str | None = supersedes_ref
                visited: set[str] = set()
                while current_ref and current_ref not in visited:
                    visited.add(current_ref)
                    current = connection.execute(
                        "SELECT * FROM governance_records WHERE record_ref=?",
                        (current_ref,),
                    ).fetchone()
                    if current is None:
                        raise GovernanceError("governance revision chain is missing a predecessor", code="ledger_corrupt")
                    if str(current["status"]) not in {"superseded", "expired", "rejected"}:
                        _append_record_lifecycle_transition(
                            root,
                            connection,
                            current,
                            status="superseded",
                            approval_basis_json=(str(current["approval_basis_json"]) if current["approval_basis_json"] is not None else None),
                            actor_role="system",
                        )
                    current_ref = str(current["supersedes"] or "").strip() if current is not None else None
        else:
            supersedes_ref = None
            row = connection.execute("SELECT COALESCE(MAX(revision),0) AS value FROM governance_records WHERE scope_key=? AND record_type=?", (scope, kind)).fetchone()
            revision = int(row["value"]) + 1
        created = _now()
        artifact_ref = _store_governance_artifact(
            connection,
            body_json,
            digest,
            content_artifact_ref,
            initiative_ref=initiative,
            task_id=task,
        )
        lifecycle_binding = ledger_db.governance_lifecycle_binding(
            record_ref=ref,
            sequence=0,
            previous_binding=None,
            status=status_value,
            approval_basis_json=approval_json,
        )
        lifecycle_ref = "lifecycle-" + hashlib.sha256(
            f"{ref}:0:{lifecycle_binding}".encode("utf-8")
        ).hexdigest()[:32]
        connection.execute(
            "INSERT INTO governance_records(record_ref,initiative_ref,task_id,record_type,revision,supersedes,status,content_json,content_digest,content_artifact_ref,approval_basis_json,created_by,created_at,expires_at,scope_key,lifecycle_sequence,lifecycle_binding) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ref, initiative, task, kind, revision, supersedes_ref, status_value, body_json, digest, artifact_ref, approval_json, created_by_value, created, record_expires_at, scope, 0, lifecycle_binding),
        )
        connection.execute(
            "INSERT INTO governance_record_lifecycle(lifecycle_ref,record_ref,lifecycle_sequence,previous_binding,status,approval_basis_json,binding,action,actor_role,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (lifecycle_ref, ref, 0, None, status_value, approval_json, lifecycle_binding, "created", role, created),
        )
        ledger_db.insert_governance_lifecycle_auth(
            root,
            connection,
            lifecycle_ref=lifecycle_ref,
            record_ref=ref,
            lifecycle_sequence=0,
            previous_binding=None,
            status=status_value,
            approval_basis_json=approval_json,
            binding=lifecycle_binding,
            action="created",
            actor_role=role,
            created_at=created,
        )
        if submission:
            connection.execute(
                "INSERT INTO governance_submissions(submission_id,command_digest,record_ref,created_at) VALUES(?,?,?,?)",
                (submission, command_digest, ref, created),
            )
        row = connection.execute("SELECT * FROM governance_records WHERE record_ref = ?", (ref,)).fetchone()
        result = _record_from_storage(root, connection, row) if row else {"record_ref": ref}
    return result


def revise_record(
    root: Path,
    *,
    record_ref: str,
    content: Any,
    created_by: str = "coordinator",
    status: str | None = None,
    approval_basis: Any = None,
    actor_role: str | None = None,
    submission_id: str | None = None,
) -> dict[str, Any]:
    """Append a revision, with durable replay handling for a lost response.

    The original revision remains the named predecessor on a retry.  Passing
    the same submission id therefore reaches ``create_record``'s durable
    receipt before the one-successor check and returns the already-committed
    successor instead of treating a network retry as a sibling revision.
    """
    ledger_db.ensure_database(root)
    ref = _safe_ref(record_ref, "record_ref", prefix="record-")
    with _connection(root) as connection:
        row = connection.execute("SELECT * FROM governance_records WHERE record_ref = ?", (ref,)).fetchone()
    if row is None:
        raise GovernanceError("record does not exist", code="record_not_found")
    return create_record(
        root,
        record_type=str(row["record_type"]),
        content=content,
        initiative_ref=row["initiative_ref"],
        task_id=row["task_id"],
        created_by=created_by,
        status=status,
        supersedes=ref,
        approval_basis=approval_basis,
        actor_role=actor_role,
        submission_id=submission_id,
    )


def list_records(
    root: Path,
    *,
    initiative_ref: str | None = None,
    task_id: str | None = None,
    record_type: str | None = None,
    active_only: bool = False,
    limit: int = 256,
    offset: int = 0,
) -> list[dict[str, Any]]:
    ledger_db.ensure_database(root)
    try:
        page_limit, page_offset = int(limit), int(offset)
    except (TypeError, ValueError) as exc:
        raise GovernanceError("record history pagination values must be integers", code="invalid_pagination") from exc
    if page_limit < 1 or page_limit > 256:
        raise GovernanceError("record history limit must be between 1 and 256", code="invalid_pagination")
    if page_offset < 0:
        raise GovernanceError("record history offset must be non-negative", code="invalid_pagination")
    clauses, values = [], []
    if initiative_ref is not None:
        clauses.append("initiative_ref = ?"); values.append(initiative_ref)
    if task_id is not None:
        clauses.append("task_id = ?"); values.append(task_id)
    if record_type is not None:
        clauses.append("record_type = ?"); values.append(str(record_type).lower())
    if active_only:
        clauses.append("status NOT IN ('superseded','expired','rejected','pending')")
        clauses.append("(expires_at IS NULL OR expires_at > ?)"); values.append(_now())
    query = "SELECT * FROM governance_records" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY created_at, record_ref LIMIT ? OFFSET ?"
    values.extend((page_limit, page_offset))
    with _connection(root) as connection:
        rows = connection.execute(query, tuple(values)).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            records.append(_record_from_storage(root, connection, row))
    return records


def inspect_record(root: Path, record_ref: str) -> dict[str, Any] | None:
    """Return one exact revision without inheriting list pagination limits."""
    ledger_db.ensure_database(root)
    ref = _safe_ref(record_ref, "record_ref", prefix="record-")
    with _connection(root) as connection:
        row = connection.execute("SELECT * FROM governance_records WHERE record_ref=?", (ref,)).fetchone()
        return _record_from_storage(root, connection, row) if row is not None else None


def active_snapshot(
    root: Path,
    *,
    initiative_ref: str | None = None,
    task_id: str | None = None,
    limit: int = 256,
    offset: int = 0,
) -> dict[str, Any]:
    records = list_records(
        root,
        initiative_ref=initiative_ref,
        task_id=task_id,
        active_only=True,
        limit=limit,
        offset=offset,
    )
    # An unapproved policy is never active, even if a caller supplied
    # status=active.  The explicit approval status is the policy activation
    # boundary and is checked again here at projection time.
    records = [item for item in records if item.get("record_type") != "policy" or item.get("status") == "approved"]
    payload = {
        "schema": "cortex/governance-snapshot/v1",
        "initiative_ref": initiative_ref,
        "task_id": task_id,
        "limit": int(limit),
        "offset": int(offset),
        "records": records,
    }
    payload["digest"] = _digest(payload)
    return payload


_CREDENTIAL_KEY_PARTS = {
    "password", "passwd", "passphrase", "secret", "token", "access_token",
    "refresh_token", "api_key", "apikey", "private_key", "client_secret",
    "authorization", "cookie", "session", "credential", "credentials",
}


def _json_pointer_join(prefix: str, key: str) -> str:
    return prefix + "/" + key.replace("~", "~0").replace("/", "~1")


def _credential_paths(content: Any, *, prefix: str = "") -> list[tuple[str, Any]]:
    """Find credential-like fields regardless of caller supplied labels."""
    matches: list[tuple[str, Any]] = []
    if isinstance(content, dict):
        for key, value in content.items():
            path = _json_pointer_join(prefix, key)
            normalized = _normalise_trigger_key(key)
            if normalized in _CREDENTIAL_KEY_PARTS or any(
                part in normalized.split("_") for part in _CREDENTIAL_KEY_PARTS
            ):
                matches.append((path, value))
            matches.extend(_credential_paths(value, prefix=path))
    elif isinstance(content, list):
        for index, value in enumerate(content):
            matches.extend(_credential_paths(value, prefix=f"{prefix}/{index}"))
    return matches


def _contains_sensitive_marker(content: Any) -> bool:
    """Identify explicit labels and credential-shaped data recursively.

    Credential-looking keys are sensitive even when a caller omits the old
    opt-in marker.  This gives record ingestion a default-deny boundary for
    accidental password/token storage while preserving ordinary prose fields.
    """
    if _credential_paths(content):
        return True
    if isinstance(content, dict):
        for key, value in content.items():
            normalized = _normalise_trigger_key(key)
            if normalized in {
                "sensitive", "sensitive_data", "confidential", "restricted", "contains_credentials",
                "credentials", "secret", "secrets", "personal_data", "pii", "security_classification",
            }:
                if isinstance(value, bool):
                    if value:
                        return True
                elif str(value or "").strip().lower() in {
                    "true", "yes", "1", "sensitive", "restricted", "confidential", "secret",
                }:
                    return True
            if isinstance(value, (dict, list)) and _contains_sensitive_marker(value):
                return True
        return False
    if isinstance(content, list):
        return any(_contains_sensitive_marker(item) for item in content)
    text = str(content or "").strip().lower()
    return any(marker in text for marker in ("[sensitive]", "[restricted]", "[confidential]", "contains credentials"))


def _path_allowed(path: str, allowed: Iterable[str]) -> bool:
    """Match explicit JSON Pointer policies."""
    for raw in allowed:
        value = str(raw or "").strip()
        if not value:
            continue
        if value == path or value == "*":
            return True
        if value.startswith("/") and path.startswith(value.rstrip("/") + "/"):
            return True
    return False


def _value_is_safe_credential_projection(value: Any) -> bool:
    """Accept only redaction metadata or a digest, never a raw credential."""
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered in {"<redacted>", "[redacted]", "redacted", "<secret-redacted>"} or bool(
            re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", lowered)
        )
    if isinstance(value, dict):
        if value.get("redacted") is True:
            return True
        digest = str(value.get("digest") or value.get("sha256") or "").strip().lower()
        return bool(re.fullmatch(r"[0-9a-f]{64}", digest))
    return False


def _validate_sensitive_field_policy(body: Any, policy: dict[str, Any]) -> None:
    """Enforce nested field controls without disclosing sensitive values."""
    if not isinstance(body, (dict, list)):
        return
    allowed_fields = [str(item).strip() for item in policy.get("allowed_fields", [])]
    redacted_fields = [str(item).strip() for item in policy.get("redacted_fields", [])]
    credential_paths = _credential_paths(body)
    if credential_paths and not allowed_fields:
        raise GovernanceError(
            "sensitive credential fields require explicit allowed_fields policy paths",
            code="sensitive_fields_rejected",
        )
    for path, value in credential_paths:
        if not _path_allowed(path, allowed_fields):
            raise GovernanceError("sensitive credential field is not allowed by policy", code="sensitive_fields_rejected")
        if not _value_is_safe_credential_projection(value):
            raise GovernanceError("sensitive credential values must be redacted or represented by a digest", code="sensitive_redaction_required")

    def leaves(value: Any, prefix: str = "") -> list[str]:
        if isinstance(value, dict):
            return [leaf for key, item in value.items() for leaf in leaves(item, _json_pointer_join(prefix, key))]
        if isinstance(value, list):
            return [leaf for index, item in enumerate(value) for leaf in leaves(item, f"{prefix}/{index}")]
        return [prefix]

    paths = leaves(body)
    if allowed_fields:
        disallowed = [path for path in paths if not _path_allowed(path, allowed_fields)]
        if disallowed:
            raise GovernanceError("sensitive governance record contains fields not allowed by policy", code="sensitive_fields_rejected")
    exposed = [path for path in paths if _path_allowed(path, redacted_fields)]
    if exposed:
        raise GovernanceError("sensitive governance record contains fields that policy requires to be redacted", code="sensitive_redaction_required")


def _sensitive_record_policy(
    root: Path,
    connection: sqlite3.Connection,
    *,
    initiative_ref: str | None,
    task_id: str | None,
    record_type: str,
) -> dict[str, Any] | None:
    """Require an approved policy explicitly covering this record type.

    A generic policy in the same initiative is not sufficient for sensitive
    content.  The policy must name the exact record type and declare at least
    a retention/disposition control plus an access/redaction control.
    """
    clauses = [
        "record_type = 'policy'",
        "status = 'approved'",
        "(expires_at IS NULL OR expires_at > ?)",
    ]
    values: list[Any] = [_now()]
    if initiative_ref:
        clauses.append("initiative_ref = ?")
        values.append(initiative_ref)
    elif task_id:
        clauses.append("task_id = ?")
        values.append(task_id)
    else:
        return None
    rows = connection.execute(
        "SELECT * FROM governance_records WHERE " + " AND ".join(clauses) + " ORDER BY created_at DESC",
        tuple(values),
    ).fetchall()
    for row in rows:
        try:
            body = _record_from_storage(root, connection, row).get("content_json")
        except GovernanceError as exc:
            raise GovernanceError("sensitive policy ledger record is corrupt", code="ledger_corrupt") from exc
        if not isinstance(body, dict):
            continue
        covered = body.get("record_type")
        if covered is None:
            covered = body.get("record_types")
        if covered is None:
            covered = body.get("applies_to")
        if isinstance(covered, str):
            covered_types = {_normalise_trigger_key(covered)}
        elif isinstance(covered, (list, tuple, set)):
            covered_types = {_normalise_trigger_key(item) for item in covered}
        else:
            covered_types = set()
        if _normalise_trigger_key(record_type) not in covered_types:
            continue
        retention_days = body.get("retention_days")
        if type(retention_days) is not int or retention_days < 1 or retention_days > 36500:
            continue
        allowed_roles = body.get("allowed_roles")
        if (
            not isinstance(allowed_roles, list)
            or not allowed_roles
            or any(str(item or "").strip().lower() not in ACTOR_ROLES for item in allowed_roles)
        ):
            continue
        allowed_fields = body.get("allowed_fields")
        if allowed_fields is not None and (
            not isinstance(allowed_fields, list)
            or any(not str(item or "").strip() for item in allowed_fields)
        ):
            continue
        redacted_fields = body.get("redacted_fields")
        if redacted_fields is not None and (
            not isinstance(redacted_fields, list)
            or any(not str(item or "").strip() for item in redacted_fields)
        ):
            continue
        return {
            "record_ref": str(row["record_ref"]),
            "retention_days": retention_days,
            "policy_expires_at": str(row["expires_at"] or "") or None,
            "allowed_roles": [str(item).strip().lower() for item in allowed_roles],
            "allowed_fields": [str(item).strip() for item in (allowed_fields or [])],
            "redacted_fields": [str(item).strip() for item in (redacted_fields or [])],
        }
    return None


def link_record(
    root: Path,
    *,
    record_ref: str,
    relationship: str,
    initiative_ref: str | None = None,
    task_id: str | None = None,
    lane_id: str | None = None,
    finding_fingerprint: str | None = None,
    evidence_ref: str | None = None,
    link_ref: str | None = None,
) -> dict[str, Any]:
    ledger_db.ensure_database(root)
    record = _safe_ref(record_ref, "record_ref", prefix="record-")
    relation = str(relationship or "").lower()
    if relation not in {"initiative", "task", "lane", "finding", "evidence"}:
        raise GovernanceError("governance link relationship is invalid", code="invalid_link")
    targets = {"initiative": initiative_ref, "task": task_id, "lane": lane_id, "finding": finding_fingerprint, "evidence": evidence_ref}
    target = str(targets[relation] or "").strip()
    if not target:
        raise GovernanceError(f"{relation} link target is required", code="link_target_required")
    for target_kind, target_value in targets.items():
        if target_kind != relation and target_value is not None and str(target_value).strip():
            raise GovernanceError("governance links accept exactly one typed target", code="link_target_conflict")
    if relation == "initiative":
        target = _safe_ref(target, "initiative_ref", prefix="initiative-")
    elif relation == "task":
        target = _safe_ref(target, "task_id")
    elif relation == "lane":
        target = _safe_ref(target, "lane_id")
    else:
        target = _safe_ref(target, f"{relation}_ref")
    ref = str(link_ref or "").strip() or "link-" + hashlib.sha256(f"{record}:{relation}:{target}".encode()).hexdigest()[:24]
    _safe_ref(ref, "link_ref", prefix="link-")
    with _connection(root, write=True) as connection:
        if connection.execute("SELECT 1 FROM governance_records WHERE record_ref = ?", (record,)).fetchone() is None:
            raise GovernanceError("record does not exist", code="record_not_found")
        if relation == "initiative" and connection.execute("SELECT 1 FROM initiatives WHERE initiative_ref = ?", (target,)).fetchone() is None:
            raise GovernanceError("initiative link target does not exist", code="link_target_not_found")
        if relation == "task" and connection.execute("SELECT 1 FROM tasks WHERE task_id = ?", (target,)).fetchone() is None:
            raise GovernanceError("task link target does not exist", code="link_target_not_found")
        if relation == "lane" and connection.execute("SELECT 1 FROM lanes WHERE lane_id = ?", (target,)).fetchone() is None:
            raise GovernanceError("lane link target does not exist", code="link_target_not_found")
        value = {
            "initiative": target if relation == "initiative" else None,
            "task": target if relation == "task" else None,
            "lane": target if relation == "lane" else None,
            "finding": target if relation == "finding" else None,
            "evidence": target if relation == "evidence" else None,
        }
        existing = connection.execute(
            "SELECT * FROM governance_links WHERE record_ref=? AND relationship=? AND "
            "initiative_ref IS ? AND task_id IS ? AND lane_id IS ? AND finding_fingerprint IS ? AND evidence_ref IS ?",
            (record, relation, value["initiative"], value["task"], value["lane"], value["finding"], value["evidence"]),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        ref_existing = connection.execute("SELECT * FROM governance_links WHERE link_ref = ?", (ref,)).fetchone()
        if ref_existing is not None:
            if all(ref_existing[key] == value.get(key) for key in ("record_ref", "initiative_ref", "task_id", "lane_id", "finding_fingerprint", "evidence_ref")) and str(ref_existing["relationship"]) == relation:
                return dict(ref_existing)
            raise GovernanceError("link_ref replay conflicts with existing link", code="link_replay_conflict")
        created = _now()
        connection.execute(
            "INSERT INTO governance_links(link_ref,record_ref,initiative_ref,task_id,lane_id,finding_fingerprint,evidence_ref,relationship,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (ref, record, value["initiative"], value["task"], value["lane"], value["finding"], value["evidence"], relation, created),
        )
        row = connection.execute("SELECT * FROM governance_links WHERE link_ref = ?", (ref,)).fetchone()
    return dict(row) if row else {"link_ref": ref}


def evaluate_promotion(
    root: Path,
    *,
    fingerprint: str,
    threshold: int = 3,
    window_days: int = 90,
    created_by: str = "coordinator",
    initiative_ref: str | None = None,
) -> dict[str, Any]:
    """Create one pending proposal after repeated equivalent findings.

    The ledger's immutable risk records and canonical task findings are the
    only authoritative candidate sources.
    """
    if threshold < 1 or window_days < 1:
        raise GovernanceError("promotion threshold and window must be positive", code="invalid_promotion_policy")
    ledger_db.ensure_database(root)
    needle = str(fingerprint or "").strip()
    if not needle:
        raise GovernanceError("fingerprint is required", code="fingerprint_required")
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    candidates: list[dict[str, Any]] = []
    for record in list_records(root, record_type="risk", active_only=False):
        body = record.get("content_json") if isinstance(record.get("content_json"), dict) else {}
        if str(body.get("fingerprint") or body.get("finding_fingerprint") or "") == needle:
            candidates.append({"task_id": record.get("task_id"), "created_at": record.get("created_at"), "record_ref": record.get("record_ref")})
    # Closure findings are canonical in task_findings and cannot be
    # manufactured outside this durable source of truth.
    with _connection(root) as connection:
        for row in connection.execute(
            "SELECT task_id, fingerprint, first_seen_at, updated_at FROM task_findings WHERE fingerprint = ?",
            (needle,),
        ):
            candidates.append({
                "task_id": row["task_id"],
                "created_at": row["first_seen_at"] or row["updated_at"],
                "record_ref": None,
            })
    distinct_scopes: dict[str, dict[str, Any]] = {}
    for item in candidates:
        stamp = str(item.get("created_at") or "")
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            when = datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when.astimezone(timezone.utc) < cutoff:
            continue
        scope = str(item.get("task_id") or item.get("initiative_ref") or item.get("scope") or "").strip()
        if scope:
            distinct_scopes.setdefault(scope, item)
    if len(distinct_scopes) < threshold:
        return {"proposal_created": False, "fingerprint": needle, "distinct_scopes": sorted(distinct_scopes), "threshold": threshold, "window_days": window_days}
    proposal_body = {
        "fingerprint": needle,
        "finding_scopes": sorted(distinct_scopes),
        "window_days": window_days,
        "threshold": threshold,
        "proposal": "review repeated finding for project policy promotion",
    }
    # Serialize the lookup and insert under the ledger's one-writer boundary.
    # A read-then-create pair on separate connections allowed concurrent MCP
    # callers to publish duplicate proposals for the same canonical finding.
    with ledger_db.transaction(root):
        with _connection(root) as connection:
            existing_rows = connection.execute(
                "SELECT * FROM governance_records WHERE record_type = 'promotion' AND initiative_ref IS ? "
                "ORDER BY created_at, record_ref",
                (initiative_ref,),
            ).fetchall()
            existing = [
                item for item in (_record_from_storage(root, connection, row) for row in existing_rows)
                if isinstance(item.get("content_json"), dict)
                and item["content_json"].get("fingerprint") == needle
                and item.get("status") not in {"rejected", "superseded", "expired"}
            ]
            if existing:
                return {
                    "proposal_created": False,
                    "proposal": existing[-1],
                    "distinct_scopes": sorted(distinct_scopes),
                    "threshold": threshold,
                    "window_days": window_days,
                }
            proposal = create_record(
                root,
                record_type="promotion",
                content=proposal_body,
                initiative_ref=initiative_ref,
                created_by=created_by,
                status="pending",
            )
    return {"proposal_created": True, "proposal": proposal, "distinct_scopes": sorted(distinct_scopes), "threshold": threshold, "window_days": window_days}


def approve_promotion(root: Path, *, proposal_ref: str, actor_role: str, approval_basis: Any = None, created_by: str = "coordinator") -> dict[str, Any]:
    if str(actor_role or "").lower() != "coordinator":
        raise GovernanceError("only the coordinator may approve a policy promotion", code="coordinator_approval_required")
    proposal_id = _safe_ref(proposal_ref, "proposal_ref", prefix="record-")
    ledger_db.ensure_database(root)
    # Proposal status and the promoted policy form one domain transition.  A
    # crash must expose either neither change or both, never an active policy
    # paired with a pending proposal.
    with ledger_db.transaction(root):
        with _connection(root) as connection:
            row = connection.execute("SELECT * FROM governance_records WHERE record_ref = ? AND record_type = 'promotion'", (proposal_id,)).fetchone()
            if row is None:
                raise GovernanceError("promotion proposal does not exist", code="proposal_not_found")
            proposal = _record_from_storage(root, connection, row)
            body = proposal.get("content_json") if isinstance(proposal.get("content_json"), dict) else {}
            policy_ref = "record-policy-" + hashlib.sha256(proposal_id.encode("utf-8")).hexdigest()[:24]
            if proposal.get("status") == "approved":
                # Replay must resolve the deterministic canonical policy ref
                # directly.  Searching list_records() is pagination-bound at
                # 256 rows and can miss the already committed policy after a
                # busy ledger grows beyond one page.
                policy_row = connection.execute(
                    "SELECT * FROM governance_records "
                    "WHERE record_ref = ? AND record_type = 'policy'",
                    (policy_ref,),
                ).fetchone()
                if policy_row is not None:
                    replay_policy = _record_from_storage(root, connection, policy_row)
                    replay_body = replay_policy.get("content_json")
                    if isinstance(replay_body, dict) and replay_body.get("promoted_from") == proposal_id:
                        return {
                            "proposal": proposal,
                            "policy": replay_policy,
                            "policy_snapshot": active_snapshot(
                                root,
                                initiative_ref=proposal.get("initiative_ref"),
                                task_id=proposal.get("task_id"),
                            ),
                        }
                raise GovernanceError("approved promotion has no canonical policy", code="ledger_corrupt")
            if proposal.get("status") != "pending":
                raise GovernanceError("promotion proposal is no longer pending", code="proposal_not_pending")
            policy = create_record(root, record_type="policy", content={"promoted_from": proposal_id, "finding": body}, initiative_ref=proposal.get("initiative_ref"), task_id=proposal.get("task_id"), created_by=created_by, status="approved", approval_basis=approval_basis or {"proposal_ref": proposal_id, "actor_role": "coordinator"}, record_ref=policy_ref, actor_role="coordinator")
            updated = _append_record_lifecycle_transition(
                root,
                connection,
                row,
                status="approved",
                approval_basis_json=_canonical(approval_basis or {"policy_ref": policy["record_ref"], "actor_role": "coordinator"}),
                actor_role="coordinator",
            )
            updated_proposal = _record_from_storage(root, connection, updated) if updated else proposal
            snapshot = active_snapshot(root, initiative_ref=proposal.get("initiative_ref"), task_id=proposal.get("task_id"))
    return {"proposal": updated_proposal, "policy": policy, "policy_snapshot": snapshot}


def request_exception(root: Path, *, trigger: str, reason: str, actor_role: str = "coordinator", initiative_ref: str | None = None, task_id: str | None = None, created_by: str = "coordinator") -> dict[str, Any]:
    key = _normalise_trigger_key(trigger)
    if not key:
        raise GovernanceError("exception trigger is required", code="exception_trigger_required")
    if key in HARD_TRIGGER_KEYS or key in {
        "hard_invariant", "runtime_invariant", "security_invariant", "c3_downgrade",
        "governance_off", "required_governance", "full_governance",
    }:
        raise GovernanceError("governance exceptions cannot disable hard security/runtime invariants", code="hard_invariant_exception_rejected")
    if str(actor_role or "").lower() != "coordinator":
        raise GovernanceError("only the coordinator may request a governance exception", code="coordinator_approval_required")
    if not str(reason or "").strip():
        raise GovernanceError("exception reason is required", code="exception_reason_required")
    exception_body = {"trigger": key, "reason": str(reason).strip()}
    exception_ref = "record-exception-" + hashlib.sha256(
        _canonical({"initiative_ref": initiative_ref, "task_id": task_id, **exception_body}).encode("utf-8")
    ).hexdigest()[:24]
    return create_record(root, record_type="exception", content=exception_body, initiative_ref=initiative_ref, task_id=task_id, created_by=created_by, status="approved", approval_basis={"actor_role": "coordinator"}, record_ref=exception_ref, actor_role="coordinator")


def manage_governance(root: Path, payload: dict[str, Any], *, actor_role: str = "coordinator") -> dict[str, Any]:
    """Dispatch one explicit, idempotent governance intent.

    The MCP facade supplies the project root and this payload.  No operation
    silently selects a task or initiative; every mutation names its scope.
    """
    if not isinstance(payload, dict):
        raise GovernanceError("governance payload must be an object", code="payload_required")
    role = str(actor_role or "").strip().lower()
    if role not in ACTOR_ROLES:
        raise GovernanceError("actor_role must be coordinator, worker, or reviewer", code="invalid_actor_role")
    action = str(payload.get("action") or payload.get("intent") or "").strip().lower().replace("-", "_")
    if not action:
        raise GovernanceError("governance action is required", code="action_required")
    if action in {"create", "create_initiative"} and payload.get("entity", "initiative") in {"initiative", "initiatives"}:
        return {"initiative": create_initiative(root, title=payload.get("title", ""), goal=payload.get("goal", ""), owner=payload.get("owner", actor_role), risk=payload.get("risk", "moderate"), initiative_ref=payload.get("initiative_ref"), parent_ref=payload.get("parent_ref"), acceptance_oracle_artifact_ref=payload.get("acceptance_oracle_artifact_ref"))}
    if action in {"inspect", "inspect_initiative"}:
        return {"initiative": inspect_initiative(root, _safe_ref(payload.get("initiative_ref"), "initiative_ref", prefix="initiative-"))}
    if action in {"link_record", "record_link"} or (action == "link" and payload.get("entity") in {"record", "governance_record"}):
        return {"link": link_record(root, record_ref=payload.get("record_ref", ""), relationship=payload.get("relationship", "evidence"), initiative_ref=payload.get("initiative_ref"), task_id=payload.get("task_id"), lane_id=payload.get("lane_id"), finding_fingerprint=payload.get("finding_fingerprint"), evidence_ref=payload.get("evidence_ref"), link_ref=payload.get("link_ref"))}
    if action in {"link_task", "link"}:
        return {"link": link_task(root, initiative_ref=payload.get("initiative_ref", ""), task_id=payload.get("task_id", ""), relationship=payload.get("relationship", "deliverable"), milestone=payload.get("milestone"), deliverable=payload.get("deliverable"), corrective=bool(payload.get("corrective")), expected_revision=payload.get("expected_revision"))}
    if action in {"add_dependency", "dependency"}:
        return {"dependency": add_dependency(root, source_type=payload.get("source_type", "initiative"), source_ref=payload.get("source_ref", ""), target_type=payload.get("target_type", "initiative"), target_ref=payload.get("target_ref", ""), dependency_type=payload.get("dependency_type", "blocks"), dependency_ref=payload.get("dependency_ref"))}
    if action in {"transition", "transition_initiative"}:
        return {"initiative": transition_initiative(root, initiative_ref=payload.get("initiative_ref", ""), status=payload.get("status", ""), expected_revision=payload.get("expected_revision"), evidence=payload.get("evidence"))}
    if action in {"create_record", "record_create"} or (action == "create" and payload.get("entity") in {"record", "governance_record"}):
        return {"record": create_record(root, record_type=payload.get("record_type", ""), content=payload.get("content"), initiative_ref=payload.get("initiative_ref"), task_id=payload.get("task_id"), created_by=payload.get("created_by", actor_role), status=payload.get("status"), supersedes=payload.get("supersedes"), expires_at=payload.get("expires_at"), approval_basis=payload.get("approval_basis"), content_artifact_ref=payload.get("content_artifact_ref"), record_ref=payload.get("record_ref"), actor_role=role, submission_id=payload.get("submission_id"))}
    if action in {"revise_record", "record_revise", "revise"}:
        return {"record": revise_record(root, record_ref=payload.get("record_ref", ""), content=payload.get("content"), created_by=payload.get("created_by", role), status=payload.get("status"), approval_basis=payload.get("approval_basis"), actor_role=role, submission_id=payload.get("submission_id"))}
    if action in {"inspect_record", "record_inspect", "history", "list_records", "snapshot", "snapshot_inspect"}:
        if action in {"snapshot", "snapshot_inspect"}:
            return {
                "snapshot": active_snapshot(
                    root,
                    initiative_ref=payload.get("initiative_ref"),
                    task_id=payload.get("task_id"),
                    limit=payload.get("limit", 256),
                    offset=payload.get("offset", 0),
                )
            }
        if action in {"history", "list_records"}:
            return {
                "records": list_records(
                    root,
                    initiative_ref=payload.get("initiative_ref"),
                    task_id=payload.get("task_id"),
                    record_type=payload.get("record_type"),
                    active_only=False,
                    limit=payload.get("limit", 256),
                    offset=payload.get("offset", 0),
                )
            }
        return {"record": inspect_record(root, payload.get("record_ref"))}
    if action in {"request_exception", "exception_request"}:
        return {"exception": request_exception(root, trigger=payload.get("trigger", ""), reason=payload.get("reason", ""), actor_role=actor_role, initiative_ref=payload.get("initiative_ref"), task_id=payload.get("task_id"), created_by=payload.get("created_by", actor_role))}
    if action in {"evaluate_promotion", "promotion_evaluate", "promotion_inspect"}:
        if action == "promotion_inspect":
            records = list_records(root, record_type="promotion", initiative_ref=payload.get("initiative_ref"), active_only=False)
            if payload.get("record_ref"):
                records = [item for item in records if item.get("record_ref") == payload.get("record_ref")]
            return {"proposals": records}
        return evaluate_promotion(root, fingerprint=payload.get("fingerprint", ""), threshold=int(payload.get("threshold", 3)), window_days=int(payload.get("window_days", 90)), created_by=payload.get("created_by", actor_role), initiative_ref=payload.get("initiative_ref"))
    if action in {"approve_promotion", "promotion_approve", "approve"}:
        return approve_promotion(root, proposal_ref=payload.get("proposal_ref") or payload.get("record_ref", ""), actor_role=role, approval_basis=payload.get("approval_basis"), created_by=payload.get("created_by", role))
    if action in {"reject_promotion", "promotion_reject", "reject"}:
        proposal_ref = _safe_ref(payload.get("proposal_ref") or payload.get("record_ref"), "proposal_ref", prefix="record-")
        with _connection(root, write=True) as connection:
            proposal = connection.execute("SELECT * FROM governance_records WHERE record_ref=? AND record_type='promotion'", (proposal_ref,)).fetchone()
            if proposal is None or str(proposal["status"]) != "pending":
                raise GovernanceError("promotion proposal is not pending", code="proposal_not_pending")
            _append_record_lifecycle_transition(
                root,
                connection,
                proposal,
                status="rejected",
                approval_basis_json=(str(proposal["approval_basis_json"]) if proposal["approval_basis_json"] is not None else None),
                actor_role=role,
            )
        return {"proposal_ref": proposal_ref, "status": "rejected"}
    raise GovernanceError("governance action is not recognized", code="unknown_action")


__all__ = [
    "GOVERNANCE_SCHEMA", "GovernanceError", "classify_governance", "resolve_governance",
    "create_initiative", "inspect_initiative", "link_task", "add_dependency",
    "transition_initiative", "create_record", "revise_record", "list_records", "inspect_record",
    "active_snapshot", "link_record", "evaluate_promotion", "approve_promotion",
    "request_exception", "manage_governance",
]
