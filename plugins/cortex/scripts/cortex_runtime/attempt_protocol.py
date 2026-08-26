"""Database-centric worker completion protocol.

Workers communicate compact facts while Cortex owns all attempt identity,
dispatch context, timestamps, and observable workspace metadata.  The module
is intentionally independent from the stdio facade and editable-draft
workflows: public transport adapters can call these small primitives without
making a document projection authoritative.

``AttemptResult`` is the one semantic terminal payload.  ``AttemptEvent`` is
an append-only checkpoint stream.  Read views are derived from these rows and
never become a second worker-authored transport authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from cortex_runtime import canonical_json, ledger_db
from cortex_runtime.finding_severity import (
    CANONICAL_FINDING_SEVERITY_RANK,
    finding_severity_is_intrinsically_blocking,
)
from cortex_runtime.validation import ValidationFailure
from cortex_runtime.verification_contract import (
    bind_verification_evidence_payload,
    pending_verification_evidence_payload,
)


ATTEMPT_RESULT_SCHEMA = "cortex/attempt-result/v1"
ATTEMPT_EVENT_SCHEMA = "cortex/attempt-event/v1"
ATTEMPT_RESULT_VIEW_SCHEMA = "cortex/attempt-result-view/v1"

RESULT_STATUSES = frozenset({"completed", "blocked", "failed"})
LIFECYCLE_WORK_COMPLETED = "WORK_COMPLETED"
LIFECYCLE_FINALIZING = "FINALIZING"
LIFECYCLE_COMPLETED = "COMPLETED"
LIFECYCLE_BLOCKED = "BLOCKED"
LIFECYCLE_FAILED = "FAILED"
TERMINAL_LIFECYCLES = frozenset({LIFECYCLE_COMPLETED, LIFECYCLE_BLOCKED, LIFECYCLE_FAILED})

WORKER_EVENT_TYPES = frozenset({
    "finding_recorded", "decision_evidence", "verification_claimed", "progress", "note",
})
SYSTEM_EVENT_TYPES = frozenset({
    "briefing_acknowledged", "predecessor_read", "verification_observed", "question_created", "question_answered", "decision_resolved", "work_completed", "finalizing", "finalization_failed", "completed",
})
EVENT_TYPES = WORKER_EVENT_TYPES | SYSTEM_EVENT_TYPES

_EVENT_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SUBMISSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


AttemptValidationError = ValidationFailure


class CanonicalResultConflict(ValueError):
    """A retry attempted to replace an immutable canonical AttemptResult."""

    def __init__(self, *, result_ref: str) -> None:
        self.result_ref = str(result_ref)
        self.diagnostics = [{
            "code": "attempt_canonical_result_conflict",
            "path": "result",
            "message": "attempt already has a different canonical result; read the existing result and do not resubmit a changed payload",
            "result_ref": self.result_ref,
        }]
        super().__init__(self.diagnostics[0]["message"])


@dataclass(frozen=True)
class AttemptResult:
    """Minimal worker-authored semantic output.

    Identity, dispatch, phase, predecessor links, timestamps, and workspace
    metadata do not belong here.  Cortex observes those facts itself and
    attaches them during :func:`complete_attempt`.
    """

    status: str
    summary: str
    findings: tuple[Any, ...] = ()
    decisions_needed: tuple[Any, ...] = ()
    unresolved: tuple[Any, ...] = ()
    claims: tuple[Any, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": ATTEMPT_RESULT_SCHEMA,
            "status": self.status,
            "summary": self.summary,
            "findings": list(self.findings),
            "decisions_needed": list(self.decisions_needed),
            "unresolved": list(self.unresolved),
            "claims": list(self.claims),
        }


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    try:
        return canonical_json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("attempt protocol payload must be JSON-serializable") from exc


def _decode_json(text: object, label: str, *, expected: type | tuple[type, ...] | None = None) -> Any:
    try:
        value = json.loads(str(text))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"attempt protocol {label} is invalid") from exc
    if expected is not None and not isinstance(value, expected):
        raise ValueError(f"attempt protocol {label} has an invalid shape")
    return value


def _bounded_json(value: Any, *, label: str, maximum: int | None = None) -> Any:
    """Validate exact JSON without imposing a content-volume quota.

    Attempt rows are canonical SQLite evidence.  Their size is guidance for
    worker prompts, not a reason to reject a complete result or event.
    """
    del label, maximum
    _canonical_json(value)
    return value


def _normalise_collection(value: Any, *, label: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"attempt result {label} must be an array")
    values = tuple(value)
    _bounded_json(values, label=label)
    return values


def _normalise_result(
    result: AttemptResult | Mapping[str, Any] | None,
    *,
    status: str | None,
    summary: str | None,
    findings: Any,
    decisions_needed: Any,
    unresolved: Any,
    claims: Any,
) -> AttemptResult:
    diagnostics: list[dict[str, Any]] = []

    def issue(path: str, message: str) -> None:
        diagnostics.append({"code": "attempt_result_invalid", "path": path, "message": message})

    if isinstance(result, AttemptResult):
        if any(value is not None for value in (status, summary, findings, decisions_needed, unresolved, claims)):
            raise ValueError("pass either result or individual AttemptResult fields, not both")
        candidate = result
    elif isinstance(result, Mapping):
        if any(value is not None for value in (status, summary, findings, decisions_needed, unresolved, claims)):
            raise ValueError("pass either result or individual AttemptResult fields, not both")
        unexpected = sorted(set(result) - {"status", "summary", "findings", "decisions_needed", "unresolved", "claims"})
        if unexpected:
            issue("result", "unsupported fields: " + ", ".join(str(item) for item in unexpected))
        candidate = AttemptResult(
            status=str(result.get("status") or ""),
            summary=str(result.get("summary") or ""),
            findings=result.get("findings"), decisions_needed=result.get("decisions_needed"),
            unresolved=result.get("unresolved"), claims=result.get("claims"),
        )
    else:
        candidate = AttemptResult(
            status=str(status or ""),
            summary=str(summary or ""),
            findings=findings, decisions_needed=decisions_needed,
            unresolved=unresolved, claims=claims,
        )
    collections: dict[str, tuple[Any, ...]] = {}
    for label, value in (("findings", candidate.findings), ("decisions_needed", candidate.decisions_needed), ("unresolved", candidate.unresolved), ("claims", candidate.claims)):
        try:
            collections[label] = _normalise_collection(value, label=label)
        except ValueError as exc:
            issue(label, str(exc))
            collections[label] = ()
    normalized_status = str(candidate.status or "").strip().lower()
    if normalized_status not in RESULT_STATUSES:
        issue("status", "must be completed, blocked, or failed")
    exact_summary = str(candidate.summary or "")
    if not exact_summary.strip():
        issue("summary", "is required")
    if diagnostics:
        raise AttemptValidationError(diagnostics)
    normalized = AttemptResult(
        status=normalized_status,
        summary=exact_summary,
        findings=collections["findings"], decisions_needed=collections["decisions_needed"],
        unresolved=collections["unresolved"], claims=collections["claims"],
    )
    _bounded_json(normalized.as_dict(), label="result")
    return normalized


def _load_task_and_attempt(
    connection: Any,
    *,
    task_id: str,
    attempt_id: str,
    allow_invalidated: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not task_id or not attempt_id:
        raise ValueError("task_id and attempt_id are required")
    row = connection.execute(
        "SELECT definition_json,state_json FROM tasks WHERE task_id=?", (task_id,)
    ).fetchone()
    if row is None:
        raise ValueError("attempt protocol task is unavailable")
    definition = _decode_json(row["definition_json"], "task definition", expected=dict)
    state = _decode_json(row["state_json"], "task state", expected=dict)
    attempts = state.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("attempt protocol task state has no attempts")
    attempt = next(
        (
            item for item in attempts
            if isinstance(item, dict) and str(item.get("attempt_id") or "") == attempt_id
        ),
        None,
    )
    if attempt is None:
        raise ValueError("attempt protocol attempt is unavailable")
    if bool(attempt.get("invalidated")) and not allow_invalidated:
        raise ValueError("attempt protocol cannot mutate an invalidated attempt")
    return definition, state, attempt


def _safe_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in output:
            output.append(item)
    return output


def _attempt_metadata(definition: Mapping[str, Any], state: Mapping[str, Any], attempt: Mapping[str, Any]) -> dict[str, Any]:
    """Return only objective metadata derived from the canonical task state."""
    selected_model = attempt.get("selected_model") or attempt.get("model")
    selected_effort = attempt.get("selected_reasoning_effort") or attempt.get("reasoning_effort")
    assignment_task_revision = attempt.get("assignment_task_revision")
    if (
        isinstance(assignment_task_revision, bool)
        or not isinstance(assignment_task_revision, int)
        or assignment_task_revision < 1
    ):
        raise ValueError("attempt metadata requires immutable assignment task revision")
    metadata = {
        "schema": "cortex/attempt-metadata/v1",
        "identity": {
            "attempt_id": str(attempt.get("attempt_id") or ""),
            "profile": str(attempt.get("profile") or attempt.get("agent") or "worker"),
            "agent": str(attempt.get("agent") or attempt.get("profile") or "worker"),
            "dispatch_ref": str(attempt.get("dispatch_ref") or "") or None,
            "selected_model": str(selected_model) if selected_model else None,
            "selected_reasoning_effort": str(selected_effort) if selected_effort else None,
        },
        "phase": str(attempt.get("gate") or "") or None,
        "phase_ref": str(attempt.get("phase_ref") or "") or None,
        "wave_ref": str(attempt.get("wave_ref") or "") or None,
        "operation_kind": str(attempt.get("operation_kind") or "") or None,
        "acceptance_contract_digest": str(
            attempt.get("acceptance_contract_digest") or ""
        ) or None,
        "plan_revision": attempt.get("plan_revision"),
        "plan_digest": str(attempt.get("plan_digest") or "") or None,
        "project_root": str(definition.get("project_root") or "") or None,
        "briefing": {
            "artifact_ref": str(attempt.get("briefing_artifact_ref") or "") or None,
            "digest_sha256": str(attempt.get("briefing_digest") or "") or None,
            "dispatch_ref": str(attempt.get("dispatch_ref") or "") or None,
        },
        "predecessor_result_refs": _safe_list(attempt.get("predecessor_result_refs")),
        "result_baseline": {
            "snapshot_ref": str(attempt.get("result_baseline_ref") or "") or None,
            "digest_sha256": str(attempt.get("result_baseline_digest") or "") or None,
        },
    }
    # Governance-close authority is carried by its exact active-plan receipt
    # and private attempt occurrence.  Task revisions must not cross that
    # dedicated closure boundary into worker-visible or canonical metadata.
    if str(attempt.get("operation_kind") or "") != "close":
        metadata["task_revision"] = assignment_task_revision
    return metadata


def _safe_project_relative_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if "\\x00" in value or "\\" in value or "://" in value or any(ord(char) < 32 for char in value):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    normalized = path.as_posix()
    return normalized if normalized == value else None


def _workspace_metadata(attempt: Mapping[str, Any], observation: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[str], str]:
    """Accept only server-produced, baseline-bound workspace observations.

    A shared checkout can contain another worker's or a user's concurrent
    changes.  We expose changed files only when the caller explicitly proves
    that the observation is complete *and* safely attributable to this exact
    attempt.  Otherwise the projection says that file attribution is absent,
    rather than falsely asserting an empty change set.
    """
    baseline_ref = str(attempt.get("result_baseline_ref") or "") or None
    baseline_digest = str(attempt.get("result_baseline_digest") or "") or None
    base = {
        "schema": "cortex/workspace-observation/v1",
        "baseline_ref": baseline_ref,
        "baseline_digest_sha256": baseline_digest,
        "current_digest_sha256": None,
        "complete": False,
        "safe_to_attribute": False,
    }
    if observation is None:
        return base, [], "unavailable"
    if not isinstance(observation, Mapping):
        raise ValueError("workspace observation must be a server-produced object")
    observed_baseline = observation.get("baseline_ref")
    if observed_baseline is not None and str(observed_baseline) != str(baseline_ref or ""):
        raise ValueError("workspace observation does not match the attempt baseline")
    observed_digest = observation.get("baseline_digest_sha256")
    if observed_digest is not None and str(observed_digest) != str(baseline_digest or ""):
        raise ValueError("workspace observation baseline digest does not match the attempt")
    current_digest = str(observation.get("current_digest_sha256") or "")
    if current_digest and not _SHA256_RE.fullmatch(current_digest):
        raise ValueError("workspace observation current digest is invalid")
    complete = observation.get("complete") is True
    safe_to_attribute = observation.get("safe_to_attribute") is True
    base.update({
        "current_digest_sha256": current_digest or None,
        "complete": complete,
        "safe_to_attribute": safe_to_attribute,
    })
    if not complete:
        return base, [], "incomplete"
    if not safe_to_attribute:
        return base, [], "not_attributable"
    raw_paths = observation.get("changed_files")
    if not isinstance(raw_paths, list):
        raise ValueError("attributable workspace observation requires changed_files")
    changed_files: list[str] = []
    for raw_path in raw_paths:
        path = _safe_project_relative_path(raw_path)
        if path is None:
            raise ValueError("workspace observation contains an unsafe changed file path")
        if path not in changed_files:
            changed_files.append(path)
    _bounded_json(changed_files, label="changed_files")
    return base, changed_files, "server_observed"


def _event_row(row: Any) -> dict[str, Any]:
    return {
        "schema": ATTEMPT_EVENT_SCHEMA,
        "event_ref": str(row["event_ref"]),
        "task_id": str(row["task_id"]),
        "attempt_id": str(row["attempt_id"]),
        "event_key": str(row["event_key"]),
        "sequence": int(row["sequence"]),
        "event_type": str(row["event_type"]),
        "payload": _decode_json(row["payload_json"], "event payload"),
        "actor": str(row["actor"]),
        "occurred_at": str(row["occurred_at"]),
        "created_at": str(row["created_at"]),
    }


def _append_event(
    connection: Any,
    *,
    task_id: str,
    attempt_id: str,
    event_type: str,
    payload: Any,
    actor: str,
    event_key: str | None = None,
    maximum_payload_bytes: int | None = None,
) -> tuple[dict[str, Any], bool]:
    if event_type not in EVENT_TYPES:
        raise ValueError("attempt event type is unsupported")
    if actor not in {"worker", "cortex", "system"}:
        raise ValueError("attempt event actor is unsupported")
    _bounded_json(payload, label="event", maximum=maximum_payload_bytes)
    payload_json = _canonical_json(payload)
    if event_key is None:
        event_key = "event-" + hashlib.sha256(
            f"{task_id}\0{attempt_id}\0{event_type}\0{payload_json}".encode("utf-8")
        ).hexdigest()[:32]
    if not _EVENT_KEY_RE.fullmatch(event_key):
        raise ValueError("attempt event_key is invalid")
    existing = connection.execute(
        "SELECT * FROM attempt_events WHERE task_id=? AND attempt_id=? AND event_key=?",
        (task_id, attempt_id, event_key),
    ).fetchone()
    if existing is not None:
        event = _event_row(existing)
        if event["event_type"] != event_type or event["actor"] != actor or _canonical_json(event["payload"]) != payload_json:
            raise ValueError("attempt event_key was reused with different content")
        return event, True
    sequence = int(connection.execute(
        "SELECT COALESCE(MAX(sequence),0)+1 FROM attempt_events WHERE task_id=? AND attempt_id=?",
        (task_id, attempt_id),
    ).fetchone()[0])
    event_ref = "attempt-event-" + hashlib.sha256(
        f"{task_id}\0{attempt_id}\0{event_key}".encode("utf-8")
    ).hexdigest()[:32]
    stamp = _now()
    connection.execute(
        "INSERT INTO attempt_events(event_ref,task_id,attempt_id,event_key,sequence,event_type,payload_json,actor,occurred_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (event_ref, task_id, attempt_id, event_key, sequence, event_type, payload_json, actor, stamp, stamp),
    )
    created = connection.execute("SELECT * FROM attempt_events WHERE event_ref=?", (event_ref,)).fetchone()
    if created is None:  # Defensive: the insert has a primary key and is in this transaction.
        raise ValueError("attempt event could not be read after persistence")
    return _event_row(created), False


def _require_briefing_receipt(connection: Any, *, task_id: str, attempt_id: str) -> None:
    """Require the server-owned briefing read before worker progress/completion.

    This check deliberately runs inside the same write transaction as the
    caller's mutation.  A worker that skipped ``read_dispatch_briefing`` must
    receive a retryable correction and leave neither an event nor a canonical
    result behind.  Keeping the guard in the protocol (rather than only in a
    transport facade) protects every runtime ingress, including the small
    contract adapter used by integration tests and local hosts.
    """
    receipt = connection.execute(
        "SELECT 1 FROM attempt_events "
        "WHERE task_id=? AND attempt_id=? AND event_type='briefing_acknowledged' "
        "ORDER BY sequence LIMIT 1",
        (task_id, attempt_id),
    ).fetchone()
    if receipt is None:
        raise ValueError(
            "briefing read receipt is required before worker progress or completion; "
            "retry read_dispatch_briefing on this same attempt"
        )


def _result_row(row: Any) -> dict[str, Any]:
    metadata = _decode_json(row["metadata_json"], "result metadata", expected=dict)
    workspace = _decode_json(row["workspace_observation_json"], "workspace observation", expected=dict)
    result = {
        "schema": ATTEMPT_RESULT_SCHEMA,
        "result_ref": str(row["result_ref"]),
        "task_id": str(row["task_id"]),
        "attempt_id": str(row["attempt_id"]),
        "status": str(row["result_status"]),
        "summary": str(row["summary"]),
        "findings": _decode_json(row["findings_json"], "result findings", expected=list),
        "decisions_needed": _decode_json(row["decisions_needed_json"], "result decisions_needed", expected=list),
        "unresolved": _decode_json(row["unresolved_json"], "result unresolved", expected=list),
        "claims": _decode_json(row["claims_json"], "result claims", expected=list),
        "metadata": metadata,
        "workspace_observation": workspace,
        "changed_files": _decode_json(row["changed_files_json"], "result changed_files", expected=list),
        "changed_files_status": str(row["changed_files_status"]),
        "lifecycle_status": str(row["lifecycle_status"]),
        "submission_id": str(row["submission_id"]),
        "content_digest": str(row["content_digest"]),
        "work_completed_at": str(row["work_completed_at"]) if row["work_completed_at"] is not None else None,
        "finalizing_at": str(row["finalizing_at"]) if row["finalizing_at"] is not None else None,
        "completed_at": str(row["completed_at"]) if row["completed_at"] is not None else None,
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }
    return result


def record_attempt_event(
    root: Any,
    *,
    task_id: str,
    attempt_id: str,
    event_type: str,
    payload: Any,
    event_key: str | None = None,
) -> dict[str, Any]:
    """Append one worker checkpoint without asking it to rewrite prior state.

    This accepts only worker fact event types.  Lifecycle events are emitted
    server-side by completion/finalization functions so a worker cannot claim
    that a projection or downstream consumer has completed.
    """
    if event_type not in WORKER_EVENT_TYPES:
        raise ValueError("workers may record only finding-recorded, decision, verification-claimed, progress, or note events")
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root, write=True) as connection:
        _load_task_and_attempt(connection, task_id=task_id, attempt_id=attempt_id)
        _require_briefing_receipt(connection, task_id=task_id, attempt_id=attempt_id)
        result = connection.execute(
            "SELECT lifecycle_status FROM attempt_results WHERE task_id=? AND attempt_id=?",
            (task_id, attempt_id),
        ).fetchone()
        if result is not None:
            raise ValueError("attempt result already exists; worker event stream is closed")
        event, idempotent = _append_event(
            connection,
            task_id=task_id,
            attempt_id=attempt_id,
            event_type=event_type,
            payload=payload,
            actor="worker",
            event_key=event_key,
        )
    return {"ok": True, "event": event, "idempotent": idempotent}


def _pending_worker_findings_connection(
    connection: Any,
    *,
    task_id: str,
    attempt_id: str,
    attempt: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Read and validate worker findings inside the caller's transaction."""
    rows = connection.execute(
        "SELECT payload_json FROM attempt_events WHERE task_id=? AND attempt_id=? "
        "AND event_type='finding_recorded' AND actor='worker' ORDER BY sequence",
        (task_id, attempt_id),
    ).fetchall()
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        payload = _decode_json(row["payload_json"], "worker finding", expected=dict)
        finding = payload.get("finding")
        binding = payload.get("binding")
        if not isinstance(finding, Mapping) or not isinstance(binding, Mapping):
            raise ValueError("worker finding evidence is malformed")
        fingerprint = str(finding.get("fingerprint") or "")
        severity = str(finding.get("severity") or "")
        summary = str(finding.get("summary") or "")
        expected_binding = {
            "schema": "cortex/worker-finding-binding/v1",
            "task_id": task_id,
            "attempt_id": attempt_id,
            "dispatch_digest": hashlib.sha256(
                str(attempt.get("dispatch_ref") or "").encode("utf-8")
            ).hexdigest(),
            "assignment_lineage_digest": str(attempt.get("assignment_lineage_digest") or ""),
            "wave_ref": str(attempt.get("wave_ref") or attempt.get("orchestration_wave_id") or ""),
            "phase_ref": str(attempt.get("phase_ref") or ""),
            "phase_kind": str(attempt.get("phase_kind") or attempt.get("gate") or ""),
            "plan_revision": int(attempt.get("plan_revision") or 0),
            "plan_digest": str(attempt.get("plan_digest") or ""),
            "workspace_baseline_ref": str(attempt.get("result_baseline_ref") or ""),
        }
        if (
            re.fullmatch(r"finding-[0-9a-f]{32}", fingerprint) is None
            or severity not in CANONICAL_FINDING_SEVERITY_RANK
            or str(finding.get("status") or "") != "open"
            or bool(finding.get("blocking"))
            is not finding_severity_is_intrinsically_blocking(severity)
            or not summary.strip()
            or set(finding) != {"fingerprint", "severity", "status", "blocking", "summary"}
            or dict(binding) != expected_binding
        ):
            raise ValueError("worker finding evidence binding is invalid")
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        findings.append(dict(finding))
    return findings


def pending_worker_findings(root: Any, *, task_id: str, attempt_id: str) -> list[dict[str, Any]]:
    """Return a non-authoritative inspection view of recorded worker findings.

    Canonical completion never uses this separate read.  It reloads and
    validates the same rows inside its own ``BEGIN IMMEDIATE`` transaction, so
    a concurrent finding cannot be stranded after the result closes the event
    stream.
    """
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root) as connection:
        _definition, _state, attempt = _load_task_and_attempt(
            connection, task_id=task_id, attempt_id=attempt_id,
        )
        return _pending_worker_findings_connection(
            connection, task_id=task_id, attempt_id=attempt_id, attempt=attempt,
        )


def _merge_recorded_findings(
    normalized: AttemptResult,
    recorded: Sequence[Mapping[str, Any]],
) -> AttemptResult:
    """Merge exact server-recorded findings into one semantic result."""
    merged = list(normalized.findings)
    by_fingerprint = {
        str(item.get("fingerprint") or ""): item
        for item in merged if isinstance(item, Mapping) and item.get("fingerprint")
    }
    for finding in recorded:
        fingerprint = str(finding.get("fingerprint") or "")
        prior = by_fingerprint.get(fingerprint)
        if prior is not None:
            if _canonical_json(prior) != _canonical_json(finding):
                raise ValueError("attempt result finding conflicts with its recorded server evidence")
            continue
        exact = dict(finding)
        merged.append(exact)
        by_fingerprint[fingerprint] = exact
    return AttemptResult(
        status=normalized.status,
        summary=normalized.summary,
        findings=tuple(merged),
        decisions_needed=normalized.decisions_needed,
        unresolved=normalized.unresolved,
        claims=normalized.claims,
    )


def worker_finding_fingerprint(
    binding: Mapping[str, Any], *, severity: str, summary: str,
) -> str:
    """Issue one exact origin-occurrence finding fingerprint server-side."""
    origin = {
        "task_id": str(binding.get("task_id") or ""),
        "attempt_id": str(binding.get("attempt_id") or ""),
        "assignment_lineage_digest": str(binding.get("assignment_lineage_digest") or ""),
        "wave_ref": str(binding.get("wave_ref") or ""),
        "phase_ref": str(binding.get("phase_ref") or ""),
        "plan_revision": binding.get("plan_revision"),
        "severity": str(severity or ""),
        "summary": str(summary or ""),
    }
    if (
        not all(origin[key] for key in (
            "task_id", "attempt_id", "assignment_lineage_digest", "wave_ref", "phase_ref",
        ))
        or isinstance(origin["plan_revision"], bool)
        or not isinstance(origin["plan_revision"], int)
        or origin["plan_revision"] < 1
        or origin["severity"] not in CANONICAL_FINDING_SEVERITY_RANK
        or not origin["summary"].strip()
    ):
        raise ValueError("worker finding origin identity is incomplete")
    return "finding-" + hashlib.sha256(
        _canonical_json(origin).encode("utf-8")
    ).hexdigest()[:32]


def record_system_event(
    root: Any,
    *,
    task_id: str,
    attempt_id: str,
    event_type: str,
    payload: Any,
    event_key: str,
) -> dict[str, Any]:
    """Append one server-owned non-worker transition to an active attempt.

    Question and decision documents remain their own durable records.  These
    compact events make their lifecycle transitions discoverable alongside the
    exact AttemptResult that will later close this attempt's stream.
    """
    if event_type not in {"question_created", "question_answered", "decision_resolved"}:
        raise ValueError("system event type is unsupported")
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root, write=True) as connection:
        _load_task_and_attempt(connection, task_id=task_id, attempt_id=attempt_id)
        result = connection.execute(
            "SELECT lifecycle_status FROM attempt_results WHERE task_id=? AND attempt_id=?",
            (task_id, attempt_id),
        ).fetchone()
        if result is not None:
            raise ValueError("attempt result already exists; event stream is closed")
        event, idempotent = _append_event(
            connection,
            task_id=task_id,
            attempt_id=attempt_id,
            event_type=event_type,
            payload=payload,
            actor="cortex",
            event_key=event_key,
        )
    return {"ok": True, "event": event, "idempotent": idempotent}


def acknowledge_briefing(
    root: Any,
    *,
    task_id: str,
    attempt_id: str,
    dispatch_ref: str,
    digest: str,
) -> dict[str, Any]:
    """Record a machine-side receipt for one exact immutable briefing read.

    This records a server-owned read receipt after the complete immutable
    briefing has been consumed. The reader transport calls this only after a
    successful complete scoped read of the immutable briefing artifact.
    """
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root, write=True) as connection:
        _definition, _state, attempt = _load_task_and_attempt(
            connection, task_id=task_id, attempt_id=attempt_id,
        )
        if str(attempt.get("dispatch_ref") or "") != str(dispatch_ref or ""):
            raise ValueError("briefing receipt dispatch_ref does not match the attempt")
        if str(attempt.get("briefing_digest") or "") != str(digest or ""):
            raise ValueError("briefing receipt digest does not match the attempt")
        event, idempotent = _append_event(
            connection,
            task_id=task_id,
            attempt_id=attempt_id,
            event_type="briefing_acknowledged",
            payload={"dispatch_ref": str(dispatch_ref), "digest_sha256": str(digest)},
            actor="cortex",
            event_key=f"briefing_acknowledged:{str(dispatch_ref)}:{str(digest)}",
        )
    return {"ok": True, "receipt": event, "idempotent": idempotent}


def record_predecessor_read(
    root: Any,
    *,
    task_id: str,
    attempt_id: str,
    predecessor_result_ref: str,
) -> dict[str, Any]:
    """Record a machine-side receipt for one scope-authorized predecessor read."""
    reference = str(predecessor_result_ref or "")
    if not reference:
        raise ValueError("predecessor receipt requires predecessor_result_ref")
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root, write=True) as connection:
        _definition, _state, attempt = _load_task_and_attempt(
            connection, task_id=task_id, attempt_id=attempt_id,
        )
        if reference not in _safe_list(attempt.get("predecessor_result_refs")):
            raise ValueError("predecessor receipt is not authorized for this attempt")
        event, idempotent = _append_event(
            connection,
            task_id=task_id,
            attempt_id=attempt_id,
            event_type="predecessor_read",
            payload={"predecessor_result_ref": reference},
            actor="cortex",
            event_key=f"predecessor_read:{reference}",
        )
    return {"ok": True, "receipt": event, "idempotent": idempotent}


def record_optional_report_read(
    root: Any,
    *,
    task_id: str,
    attempt_id: str,
    result_ref: str,
) -> dict[str, Any]:
    """Record one non-blocking complete optional-report read."""
    reference = str(result_ref or "")
    if not reference:
        raise ValueError("optional report receipt requires result_ref")
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root, write=True) as connection:
        _definition, _state, attempt = _load_task_and_attempt(
            connection, task_id=task_id, attempt_id=attempt_id,
        )
        if reference not in _safe_list(attempt.get("optional_report_result_refs")):
            raise ValueError("optional report receipt is not authorized for this attempt")
        event, idempotent = _append_event(
            connection,
            task_id=task_id,
            attempt_id=attempt_id,
            event_type="note",
            payload={"receipt_kind": "optional_report_read", "result_ref": reference},
            actor="cortex",
            event_key=f"optional_report_read:{reference}",
        )
    return {"ok": True, "receipt": event, "idempotent": idempotent}


def attempt_receipts(root: Any, *, task_id: str, attempt_id: str) -> dict[str, Any]:
    """Project verified briefing/predecessor reads from the event authority."""
    briefing_receipt: dict[str, Any] | None = None
    predecessors: dict[str, dict[str, Any]] = {}
    for event in list_attempt_events(root, task_id=task_id, attempt_id=attempt_id):
        payload = event.get("payload")
        if event.get("event_type") == "briefing_acknowledged" and isinstance(payload, dict):
            briefing_receipt = event
        elif event.get("event_type") == "predecessor_read" and isinstance(payload, dict):
            reference = str(payload.get("predecessor_result_ref") or "")
            if reference:
                predecessors[reference] = event
    return {"briefing_receipt": briefing_receipt, "predecessor_receipts": predecessors}


def complete_attempt(
    root: Any,
    *,
    task_id: str,
    attempt_id: str,
    result: AttemptResult | Mapping[str, Any] | None = None,
    status: str | None = None,
    summary: str | None = None,
    findings: Any = None,
    decisions_needed: Any = None,
    unresolved: Any = None,
    claims: Any = None,
    submission_id: str | None = None,
    workspace_observation: Mapping[str, Any] | None = None,
    metadata_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one canonical worker result and close its event stream.

    Successful work stops at ``WORK_COMPLETED``.  Result/Handoff projection
    failures must be retried through :func:`finalize_attempt`; they must never
    create a replacement worker attempt or discard this durable result.

    ``workspace_observation`` is intentionally an internal, server-produced
    input.  It is accepted only when it binds to the attempt baseline and says
    the delta is safely attributable; worker supplied ``changed_files`` are
    not part of this API.
    """
    normalized = _normalise_result(
        result,
        status=status,
        summary=summary,
        findings=findings,
        decisions_needed=decisions_needed,
        unresolved=unresolved,
        claims=claims,
    )
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root, write=True) as connection:
        definition, state, attempt = _load_task_and_attempt(
            connection, task_id=task_id, attempt_id=attempt_id,
        )
        _require_briefing_receipt(connection, task_id=task_id, attempt_id=attempt_id)
        # ``BEGIN IMMEDIATE`` is already held. A finding transaction that won
        # serialization committed before this snapshot and is included; one
        # that loses serialization resumes only after the result exists and is
        # rejected by the closed event-stream guard.
        normalized = _merge_recorded_findings(
            normalized,
            _pending_worker_findings_connection(
                connection,
                task_id=task_id,
                attempt_id=attempt_id,
                attempt=attempt,
            ),
        )
        existing = connection.execute(
            "SELECT * FROM attempt_results WHERE task_id=? AND attempt_id=?", (task_id, attempt_id)
        ).fetchone()
        if existing is not None:
            stored = _result_row(existing)
            # Retry identity is semantic, not a snapshot of mutable task
            # state. Finalization records lifecycle/result-reference state after
            # the original commit and may legitimately advance the task
            # revision; recomputing metadata here used to make an otherwise
            # identical retry appear to be a conflicting result.
            stored_semantic = {
                "status": stored["status"],
                "summary": stored["summary"],
                "findings": stored["findings"],
                "decisions_needed": stored["decisions_needed"],
                "unresolved": stored["unresolved"],
                "claims": stored["claims"],
            }
            # ``AttemptResult.as_dict`` includes its transport schema; compare
            # only the durable semantic columns above.
            submitted_semantic = {
                "status": normalized.status,
                "summary": normalized.summary,
                "findings": list(normalized.findings),
                "decisions_needed": list(normalized.decisions_needed),
                "unresolved": list(normalized.unresolved),
                "claims": list(normalized.claims),
            }
            if _canonical_json(stored_semantic) != _canonical_json(submitted_semantic):
                raise CanonicalResultConflict(result_ref=str(stored["result_ref"]))
            if submission_id is not None and stored["submission_id"] != str(submission_id):
                raise ValueError("attempt completion retry must reuse its original submission_id")
            return {"ok": True, "result": stored, "idempotent": True, "finalization_required": stored["lifecycle_status"] == LIFECYCLE_WORK_COMPLETED}
        metadata = _attempt_metadata(definition, state, attempt)
        assignment_task_revision = int(attempt["assignment_task_revision"])
        if metadata_overrides is not None:
            # Only the trusted runtime calls this protocol seam. Public worker
            # fields never reach metadata_overrides.
            overrides = _bounded_json(dict(metadata_overrides), label="attempt metadata overrides")
            if set(overrides) - {"governance_closure"}:
                raise ValueError("attempt metadata overrides contain unsupported fields")
            metadata.update(overrides)
        workspace, changed_files, changed_files_status = _workspace_metadata(attempt, workspace_observation)
        # Manifest reconciliation is the one verification fact Cortex can
        # observe directly.  Materialize its pending receipt in this same
        # transaction from the complete server workspace observation; no
        # worker-authored event or /usr/bin/true command participates.
        current_workspace_digest = str(workspace.get("current_digest_sha256") or "")
        if (
            str(attempt.get("operation_kind") or "") in {"verify", "close"}
            and "manifest_reconciliation" in set(attempt.get("required_verification_kinds") or [])
            and workspace.get("complete") is True
            and current_workspace_digest
        ):
            manifest_payload = pending_verification_evidence_payload(
                task_id=task_id,
                attempt=attempt,
                verification_kind="manifest_reconciliation",
                verification_id="server_manifest_reconciliation",
                task_revision=assignment_task_revision,
                workspace_digest=current_workspace_digest,
                server_receipt={
                    "schema": "cortex/server-observation-receipt/v1",
                    "source": "server_manifest",
                    "receipt_scope": "manifest_reconciliation",
                    "evidence_digest": current_workspace_digest,
                    "status": "recorded",
                },
                tests=[{"kind": "manifest_reconciliation", "status": "passed"}],
            )
            _append_event(
                connection,
                task_id=task_id,
                attempt_id=attempt_id,
                event_type="verification_observed",
                payload=manifest_payload,
                actor="cortex",
                event_key="server_manifest:" + hashlib.sha256(
                    f"{attempt_id}\0{current_workspace_digest}".encode("utf-8")
                ).hexdigest(),
            )
        pending_rows = connection.execute(
            "SELECT * FROM attempt_events WHERE task_id=? AND attempt_id=? "
            "AND event_type IN ('verification_claimed','verification_observed') "
            "AND actor IN ('worker','cortex') ORDER BY sequence",
            (task_id, attempt_id),
        ).fetchall()
        pending_refs: list[str] = []
        for row in pending_rows:
            decoded = _decode_json(row["payload_json"], "verification observation")
            if isinstance(decoded, Mapping) and decoded.get("binding_status") == "pending_result":
                pending_refs.append(str(row["event_ref"]))
        metadata["verification_evidence_refs"] = pending_refs
        semantic = normalized.as_dict()
        digest_payload = {
            "result": semantic,
            "metadata": metadata,
            "workspace_observation": workspace,
            "changed_files": changed_files,
            "changed_files_status": changed_files_status,
        }
        content_digest = hashlib.sha256(_canonical_json(digest_payload).encode("utf-8")).hexdigest()
        submission = str(submission_id or f"completion-{content_digest[:24]}")
        if not _SUBMISSION_ID_RE.fullmatch(submission):
            raise ValueError("attempt completion submission_id is invalid")
        lifecycle = (
            LIFECYCLE_WORK_COMPLETED if normalized.status == "completed"
            else LIFECYCLE_BLOCKED if normalized.status == "blocked"
            else LIFECYCLE_FAILED
        )
        stamp = _now()
        result_ref = "attempt-result-" + hashlib.sha256(
            f"{task_id}\0{attempt_id}\0{content_digest}".encode("utf-8")
        ).hexdigest()[:32]
        connection.execute(
            "INSERT INTO attempt_results(result_ref,task_id,attempt_id,result_status,lifecycle_status,summary,findings_json,decisions_needed_json,unresolved_json,claims_json,metadata_json,workspace_observation_json,changed_files_json,changed_files_status,content_digest,submission_id,work_completed_at,finalizing_at,completed_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                result_ref, task_id, attempt_id, normalized.status, lifecycle, normalized.summary,
                _canonical_json(list(normalized.findings)), _canonical_json(list(normalized.decisions_needed)),
                _canonical_json(list(normalized.unresolved)), _canonical_json(list(normalized.claims)),
                _canonical_json(metadata), _canonical_json(workspace), _canonical_json(changed_files),
                changed_files_status, content_digest, submission, stamp, None, None, stamp, stamp,
            ),
        )
        # Findings are part of the same canonical transaction as the result.
        # Gate transitions therefore see them immediately, including blocked
        # and failed results, and a retry cannot leave a half-materialized
        # projection behind.
        ledger_db.materialize_attempt_findings(
            connection,
            task_id=task_id,
            attempt_id=attempt_id,
            result_ref=result_ref,
            gate=str(attempt.get("gate") or "") or None,
            task_revision=assignment_task_revision,
            findings=normalized.findings,
        )
        result_binding = {
            "result_ref": result_ref,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "content_digest": content_digest,
            "workspace_observation": workspace,
            "metadata": metadata,
        }
        for pending_row in pending_rows:
            pending_event = _event_row(pending_row)
            payload = pending_event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            bound_payload = bind_verification_evidence_payload(
                source_event_ref=str(pending_event.get("event_ref") or ""),
                pending=payload,
                task_id=task_id,
                attempt=attempt,
                result=result_binding,
            )
            if bound_payload is None:
                continue
            _append_event(
                connection,
                task_id=task_id,
                attempt_id=attempt_id,
                event_type=str(pending_event.get("event_type") or ""),
                payload=bound_payload,
                actor=str(pending_event.get("actor") or ""),
                event_key="verification_bound:" + hashlib.sha256(
                    f"{pending_event['event_ref']}\0{result_ref}".encode("utf-8")
                ).hexdigest(),
            )
        event, _ = _append_event(
            connection,
            task_id=task_id,
            attempt_id=attempt_id,
            event_type="work_completed",
            payload={"result_ref": result_ref, "result_status": normalized.status, "lifecycle_status": lifecycle},
            actor="cortex",
            event_key=f"completion:{result_ref}",
        )
        stored_row = connection.execute("SELECT * FROM attempt_results WHERE result_ref=?", (result_ref,)).fetchone()
        if stored_row is None:
            raise ValueError("attempt result could not be read after persistence")
        stored = _result_row(stored_row)
    return {
        "ok": True,
        "result": stored,
        "work_completed_event": event,
        "idempotent": False,
        "finalization_required": stored["lifecycle_status"] == LIFECYCLE_WORK_COMPLETED,
    }


def begin_attempt_finalization(root: Any, *, task_id: str, attempt_id: str) -> dict[str, Any]:
    """Durably enter ``FINALIZING`` before an external projection operation."""
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root, write=True) as connection:
        _load_task_and_attempt(connection, task_id=task_id, attempt_id=attempt_id)
        row = connection.execute(
            "SELECT * FROM attempt_results WHERE task_id=? AND attempt_id=?", (task_id, attempt_id)
        ).fetchone()
        if row is None:
            raise ValueError("attempt result is unavailable for finalization")
        stored = _result_row(row)
        lifecycle = stored["lifecycle_status"]
        if lifecycle == LIFECYCLE_COMPLETED:
            return {"ok": True, "result": stored, "idempotent": True}
        if lifecycle != LIFECYCLE_WORK_COMPLETED:
            raise ValueError("only a successfully completed attempt can enter finalization")
        stamp = _now()
        connection.execute(
            "UPDATE attempt_results SET lifecycle_status=?,finalizing_at=?,updated_at=? WHERE result_ref=?",
            (LIFECYCLE_FINALIZING, stamp, stamp, stored["result_ref"]),
        )
        event, _ = _append_event(
            connection,
            task_id=task_id,
            attempt_id=attempt_id,
            event_type="finalizing",
            payload={"result_ref": stored["result_ref"]},
            actor="cortex",
            event_key=f"finalizing:{stored['result_ref']}",
        )
        updated = connection.execute("SELECT * FROM attempt_results WHERE result_ref=?", (stored["result_ref"],)).fetchone()
        if updated is None:
            raise ValueError("attempt result is unavailable after finalization transition")
        return {"ok": True, "result": _result_row(updated), "event": event, "idempotent": False}


def record_finalization_failure(
    root: Any,
    *,
    task_id: str,
    attempt_id: str,
    reason_code: str,
) -> dict[str, Any]:
    """Checkpoint a projection/infrastructure failure without failing work.

    The public message or traceback is intentionally not persisted here.  A
    short reason code preserves retry observability without duplicating a
    potentially sensitive host error into the worker handoff stream.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_:-]{0,95}", str(reason_code)):
        raise ValueError("finalization failure reason_code is invalid")
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root, write=True) as connection:
        _load_task_and_attempt(connection, task_id=task_id, attempt_id=attempt_id)
        row = connection.execute(
            "SELECT * FROM attempt_results WHERE task_id=? AND attempt_id=?", (task_id, attempt_id)
        ).fetchone()
        if row is None:
            raise ValueError("attempt result is unavailable for finalization failure")
        stored = _result_row(row)
        if stored["lifecycle_status"] not in {LIFECYCLE_WORK_COMPLETED, LIFECYCLE_FINALIZING}:
            raise ValueError("finalization failure can be recorded only after successful work completion")
        event, idempotent = _append_event(
            connection,
            task_id=task_id,
            attempt_id=attempt_id,
            event_type="finalization_failed",
            payload={"result_ref": stored["result_ref"], "reason_code": reason_code},
            actor="cortex",
            event_key=f"finalization_failed:{stored['result_ref']}:{reason_code}",
        )
        return {"ok": True, "result": stored, "event": event, "idempotent": idempotent, "retryable": True}


def finalize_attempt(root: Any, *, task_id: str, attempt_id: str) -> dict[str, Any]:
    """Mark a projection-ready result as ``COMPLETED`` atomically.

    Call this only after the caller's handoff/materialization work succeeds.
    A projection exception must instead use :func:`record_finalization_failure`
    and retry from the stored result; it is not a worker execution failure.
    """
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root, write=True) as connection:
        # A lifecycle recovery can retire the mutable task projection after
        # the canonical result was committed but before a coordinator retries
        # the finalization receipt.  In that case the immutable result is the
        # authority: a completed result is already finalized and must be
        # returned idempotently.  Do not reject a valid receipt merely because
        # the historical attempt row was invalidated by recovery.
        _definition, _state, attempt = _load_task_and_attempt(
            connection, task_id=task_id, attempt_id=attempt_id,
            allow_invalidated=True,
        )
        row = connection.execute(
            "SELECT * FROM attempt_results WHERE task_id=? AND attempt_id=?", (task_id, attempt_id)
        ).fetchone()
        if row is None:
            raise ValueError("attempt result is unavailable for completion")
        stored = _result_row(row)
        if attempt.get("invalidated") and stored["lifecycle_status"] == LIFECYCLE_COMPLETED:
            return {"ok": True, "result": stored, "idempotent": True, "recovered_invalidated_attempt": True}
        if attempt.get("invalidated"):
            raise ValueError(
                "attempt finalization is superseded by lifecycle recovery; "
                "use the server-derived corrective dispatch for this task"
            )
        if stored["lifecycle_status"] == LIFECYCLE_COMPLETED:
            return {"ok": True, "result": stored, "idempotent": True}
        if stored["lifecycle_status"] not in {LIFECYCLE_WORK_COMPLETED, LIFECYCLE_FINALIZING}:
            raise ValueError("only a successfully completed attempt can finalize")
        result_ref = stored["result_ref"]
        if stored["lifecycle_status"] == LIFECYCLE_WORK_COMPLETED:
            finalizing_at = _now()
            connection.execute(
                "UPDATE attempt_results SET lifecycle_status=?,finalizing_at=?,updated_at=? WHERE result_ref=?",
                (LIFECYCLE_FINALIZING, finalizing_at, finalizing_at, result_ref),
            )
            _append_event(
                connection,
                task_id=task_id,
                attempt_id=attempt_id,
                event_type="finalizing",
                payload={"result_ref": result_ref},
                actor="cortex",
                event_key=f"finalizing:{result_ref}",
            )
        completed_at = _now()
        connection.execute(
            "UPDATE attempt_results SET lifecycle_status=?,completed_at=?,updated_at=? WHERE result_ref=?",
            (LIFECYCLE_COMPLETED, completed_at, completed_at, result_ref),
        )
        event, _ = _append_event(
            connection,
            task_id=task_id,
            attempt_id=attempt_id,
            event_type="completed",
            payload={"result_ref": result_ref},
            actor="cortex",
            event_key=f"completed:{result_ref}",
        )
        updated = connection.execute("SELECT * FROM attempt_results WHERE result_ref=?", (result_ref,)).fetchone()
        if updated is None:
            raise ValueError("attempt result is unavailable after completion transition")
        return {"ok": True, "result": _result_row(updated), "event": event, "idempotent": False}


def get_attempt_result(root: Any, *, task_id: str, attempt_id: str) -> dict[str, Any] | None:
    """Read the canonical result, never a mutable export."""
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root) as connection:
        row = connection.execute(
            "SELECT * FROM attempt_results WHERE task_id=? AND attempt_id=?", (task_id, attempt_id)
        ).fetchone()
    return None if row is None else _result_row(row)


def list_attempt_events(
    root: Any,
    *,
    task_id: str,
    attempt_id: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return the complete ordered checkpoint stream for an exact attempt."""
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValueError("attempt event limit must be a positive integer when supplied")
    ledger_root = _root(root)
    ledger_db.ensure_database(ledger_root)
    with ledger_db.connection(ledger_root) as connection:
        # Lifecycle recovery may retire an attempt after its canonical result
        # and event stream were committed.  This operation is read-only and
        # is required to rebuild predecessor/result projections during
        # corrective dispatch; mutation guards must not hide that evidence.
        _load_task_and_attempt(
            connection, task_id=task_id, attempt_id=attempt_id,
            allow_invalidated=True,
        )
        if limit is None:
            rows = connection.execute(
                "SELECT * FROM attempt_events WHERE task_id=? AND attempt_id=? ORDER BY sequence",
                (task_id, attempt_id),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM attempt_events WHERE task_id=? AND attempt_id=? ORDER BY sequence LIMIT ?",
                (task_id, attempt_id, limit),
            ).fetchall()
    return [_event_row(row) for row in rows]


def build_attempt_result_view(root: Any, *, task_id: str, attempt_id: str) -> dict[str, Any]:
    """Build a non-authoritative read view from canonical result/event rows.

    The returned object intentionally has no editable worker-authored body,
    mutable projection id, or mutable export artifact. It is deterministic and may be
    regenerated after recovery without creating another completion authority.
    """
    result = get_attempt_result(root, task_id=task_id, attempt_id=attempt_id)
    if result is None:
        raise ValueError("attempt result is unavailable")
    events = list_attempt_events(root, task_id=task_id, attempt_id=attempt_id)
    projection_digest = hashlib.sha256(
        _canonical_json({"result": result, "events": events}).encode("utf-8")
    ).hexdigest()
    return {
        "schema": ATTEMPT_RESULT_VIEW_SCHEMA,
        "projection_ref": "attempt-result-view-" + projection_digest[:32],
        "task_id": task_id,
        "attempt_id": attempt_id,
        "attempt_result_ref": result["result_ref"],
        "phase": result["metadata"].get("phase"),
        "producer": result["metadata"].get("identity"),
        "lifecycle_status": result["lifecycle_status"],
        "result": result,
        "events": events,
        "created_at": result["updated_at"],
        "content_digest": projection_digest,
    }


def _root(root: Any) -> Any:
    """Keep the public functions friendly to ``Path`` and path-like callers."""
    try:
        return root if isinstance(root, Path) else Path(root)
    except TypeError as exc:
        raise ValueError("attempt protocol ledger root is invalid") from exc
