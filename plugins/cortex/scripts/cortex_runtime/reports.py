"""Scoped report and immutable-briefing operations.

The public facade supplies durable ledger helpers while this module owns
the worker-facing report transport and keeps the public v5 surface narrow.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cortex as _runtime

from cortex import (
    AGENTS,
    AWAITING_HOST_SPAWN,
    MAX_BRIEFING_BYTES,
    MAX_REPORTS_PER_ATTEMPT,
    PUBLIC_ORCHESTRATION_SCHEMA,
    REPORT_FIELDS,
    REPORT_SCHEMA,
    EXECUTED_CHECK_RESULT_GATES,
    WRITE_REQUIRED_RESULT_GATES,
    _attempt,
    _attempt_identity_aliases,
    _contained_path,
    _delegation_report_index,
    _open_blocking_questions,
    _read_private_text,
    _recover_report_receipt,
    _resolved_user_decisions,
    _report_index,
    _write_delegation_report_index,
    _write_report_index,
    _report_markdown,
    _report_metadata,
    _predecessor_review_marker,
    _result_contract_markers,
    _v3_resolve_task,
    _validate_close_report,
    _validate_gate_result_report,
    _validate_harvest_coverage_manifest,
    _validate_knowledge_review,
    _validate_predecessor_review,
    _validate_report_decision_closure,
    active_gates,
    append_journal_best_effort,
    authorize,
    authorize_principal,
    canonical_profile,
    digest_text,
    dispatch_briefing_review_marker,
    ledger_root,
    load_task_definition,
    load_state,
    materialize_planning_artifacts,
    now,
    redact,
    report_bus_paths,
    report_markdown_link,
    report_markdown_path,
    sanitize_scoping_payload,
    sanitize_planning_payload,
    sanitize_report_payload,
    sanitize_closure_payload,
    safe_id,
    select_project_root,
    state_lock,
    store_immutable_artifact,
)
from cortex_runtime.projection_service import (
    enqueue as enqueue_projection,
    materialize_job as materialize_projection_job,
    repair as repair_projection_job,
    verify_job as verify_projection_job,
)
from cortex_runtime.ledger_db import (
    delete_task_document as db_delete_task_document,
    fail_projection_job,
    get_task_document as db_get_task_document,
    list_task_documents as db_list_task_documents,
    put_task_document as db_put_task_document,
)
from cortex_runtime.record_report import build_compatibility_facade

_REPORT_DRAFT_SCHEMA = "cortex/report-draft-file/v2"
_LEGACY_REPORT_DRAFT_SCHEMA = "cortex/report-draft-file/v1"
_REPORT_DRAFT_TTL = timedelta(hours=1)
_REPORT_DRAFT_PAYLOAD_FIELDS = {"report", "scoping", "planning", "gate_result", "closure"}
_GATE_RESULT_REQUIRED_GATES = {
    "review", "governance_activation", "governance_close", "close",
}


def _report_draft_key(attempt_id: str, draft_ref: str) -> str:
    return f"report_draft:{safe_id(attempt_id)}:{safe_id(draft_ref)}"


def _report_draft_relative_path(attempt_id: str, draft_ref: str) -> Path:
    return Path("report-drafts") / safe_id(attempt_id) / f"{safe_id(draft_ref)}.json"


def _parse_utc_timestamp(value: object, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"report draft {field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"report draft {field} is invalid")
    return parsed.astimezone(timezone.utc)


def _delete_report_draft(
    root: Path,
    *,
    task_id: str,
    document_key: str,
    draft_path: Path,
) -> None:
    """Remove one scoped temporary draft and its metadata after commit or supersession."""
    try:
        try:
            info = draft_path.lstat()
        except FileNotFoundError:
            info = None
        if info is not None and (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            draft_path.unlink()
            _runtime._fsync_directory(draft_path.parent)
    finally:
        db_delete_task_document(root, task_id, document_key)


def _stage_report_draft_file(
    root: Path,
    task_dir: Path,
    *,
    project_root: str,
    task_id: str,
    attempt_id: str,
    profile: str,
    envelope: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    """Create one private editable report file before validation."""
    draft_ref = "draft-" + secrets.token_hex(16)
    document_key = _report_draft_key(attempt_id, draft_ref)
    relative_path = _report_draft_relative_path(attempt_id, draft_ref)
    draft_path = _contained_path(task_dir, task_dir / relative_path, "report draft")
    created_at = datetime.now(timezone.utc)
    metadata = {
        "schema": _REPORT_DRAFT_SCHEMA,
        "draft_ref": draft_ref,
        "project_root": project_root,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "profile": profile,
        "relative_path": relative_path.as_posix(),
        "created_at": created_at.isoformat(),
        "expires_at": (created_at + _REPORT_DRAFT_TTL).isoformat(),
    }
    _runtime.write_json(draft_path, envelope)
    try:
        db_put_task_document(root, task_id, document_key, metadata)
    except Exception:
        if draft_path.exists():
            draft_path.unlink()
        raise
    prefix = f"report_draft:{safe_id(attempt_id)}:"
    for key, previous in db_list_task_documents(root, task_id, prefix=prefix):
        if key == document_key:
            continue
        previous_relative = Path(str(previous.get("relative_path") or ""))
        if previous_relative and not previous_relative.is_absolute() and ".." not in previous_relative.parts:
            previous_path = _contained_path(task_dir, task_dir / previous_relative, "superseded report draft")
            _delete_report_draft(
                root, task_id=task_id, document_key=key, draft_path=previous_path,
            )
        else:
            db_delete_task_document(root, task_id, key)
    return metadata, draft_path


def _load_report_draft_file(
    root: Path,
    task_dir: Path,
    *,
    project_root: str,
    task_id: str,
    attempt_id: str,
    profile: str,
    draft_ref: str,
) -> tuple[dict[str, Any], dict[str, Any], str, Path]:
    normalized_ref = safe_id(draft_ref)
    if not re.fullmatch(r"draft-[0-9a-f]{32}", normalized_ref):
        raise ValueError("draft_ref is invalid; copy it exactly from get_report_template")
    document_key = _report_draft_key(attempt_id, normalized_ref)
    metadata = db_get_task_document(root, task_id, document_key)
    if metadata is None:
        raise ValueError(
            "report draft is unavailable or superseded; call get_report_template again on this attempt"
        )
    required = {
        "schema", "draft_ref", "project_root", "task_id", "attempt_id", "profile", "relative_path",
        "created_at", "expires_at",
    }
    legacy_required = required | {"validation_digest", "validated_at"}
    if not (
        (set(metadata) == required and metadata.get("schema") == _REPORT_DRAFT_SCHEMA)
        or (set(metadata) == legacy_required and metadata.get("schema") == _LEGACY_REPORT_DRAFT_SCHEMA)
    ):
        raise ValueError("report draft metadata is invalid")
    if any(str(metadata.get(field) or "") != value for field, value in (
        ("draft_ref", normalized_ref),
        ("project_root", project_root),
        ("task_id", task_id),
        ("attempt_id", attempt_id),
        ("profile", profile),
    )):
        raise ValueError("report draft does not belong to this exact worker attempt")
    relative_path = Path(str(metadata["relative_path"]))
    expected_relative = _report_draft_relative_path(attempt_id, normalized_ref)
    if relative_path != expected_relative or relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("report draft path is outside its exact worker attempt")
    draft_path = _contained_path(task_dir, task_dir / relative_path, "report draft")
    if _parse_utc_timestamp(metadata["expires_at"], field="expires_at") <= datetime.now(timezone.utc):
        raise ValueError("report draft has expired; call get_report_template again on this attempt")
    try:
        info = draft_path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("report draft file is missing; call get_report_template again on this attempt") from exc
    if draft_path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError("report draft must remain a private regular non-symlink file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("report draft permissions are too broad; restore private 0600 permissions")
    text = _read_private_text(draft_path, "report draft", max_bytes=_runtime.MAX_JSON_BYTES)
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"report draft JSON is invalid at line {exc.lineno}, column {exc.colno}") from exc
    allowed = {"project_root", "task_id", "attempt_id", "profile"} | _REPORT_DRAFT_PAYLOAD_FIELDS
    if not isinstance(envelope, dict) or set(envelope) - allowed:
        raise ValueError("report draft must contain only worker identity and report envelope fields")
    for field, expected in (
        ("project_root", project_root),
        ("task_id", task_id), ("attempt_id", attempt_id), ("profile", profile),
    ):
        if str(envelope.get(field) or "") != expected:
            raise ValueError(f"report draft {field} does not match this exact worker attempt")
    return envelope, metadata, document_key, draft_path


def _write_report_draft_file(draft_path: Path, envelope: dict[str, Any]) -> None:
    _runtime.write_json(draft_path, envelope)


def _merge_patch(target: Any, patch: Any) -> Any:
    """Apply RFC 7396 JSON Merge Patch semantics to an in-memory draft."""
    if not isinstance(patch, dict):
        return patch
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = _merge_patch(result.get(key), value)
    return result


def _bounded_artifact_max_bytes(value: Any, *, label: str) -> tuple[int, int | None, bool]:
    """Normalize a caller chunk request without widening the transport bound."""
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
        ("max_bytes", "max_bytes"),
        ("cursor", "cursor"),
        ("briefing_digest", "briefing_digest"),
        ("digest does not match", "briefing_digest"),
        ("dispatch_ref", "dispatch_ref"),
        ("attempt_id", "attempt_id"),
        ("task_id", "task_id"),
        ("profile", "profile"),
        ("project_root", "project_root"),
    ):
        if marker in lowered:
            return path
    return "$"


def _dispatch_briefing_failure(exc: BaseException) -> dict[str, Any]:
    """Keep caller request mistakes retryable and reserve blockers for integrity/storage failures."""
    message = redact(str(exc), 1000)
    lowered = message.lower()
    caller_correctable = isinstance(exc, ValueError) and any(fragment in lowered for fragment in (
        "unsupported read_dispatch_briefing fields",
        "is required; copy the exact value",
        "profile must be an exact cortex worker profile",
        "profile does not match",
        "dispatch_ref does not match",
        "briefing_digest must be",
        "briefing_digest does not match",
        "briefing cursor",
        "briefing max_bytes",
    ))
    if caller_correctable:
        path = _dispatch_briefing_error_path(message)
        fix = (
            "Omit max_bytes or use an integer from 1 through 32768, then retry read_dispatch_briefing on this same "
            "worker attempt."
            if path == "max_bytes"
            else "Copy the exact field from the native dispatch bootstrap or the last returned next_cursor, then "
            "retry read_dispatch_briefing on this same worker attempt."
        )
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": "needs_correction",
            "code": "dispatch_briefing_request_invalid",
            "diagnostics": [{
                "code": "dispatch_briefing_request_invalid",
                "path": path,
                "message": message,
                "fix": fix,
            }],
            "retryable": True,
            "attempt_budget_consumed": False,
            "next_action": (
                "Apply the diagnostic fix and retry read_dispatch_briefing now on this same attempt. Caller or "
                "schema validation errors never justify ending the worker. Stop only if a later response explicitly "
                "returns retryable=false or outcome=blocked."
            ),
        }
    return {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "outcome": "blocked",
        "code": "dispatch_briefing_unavailable",
        "diagnostics": [{
            "code": "dispatch_briefing_unavailable",
            "path": "$",
            "message": message,
            "fix": "Preserve this integrity or storage diagnostic; it cannot be repaired by changing tool arguments.",
        }],
        "retryable": False,
        "attempt_budget_consumed": False,
        "next_action": (
            "Stop before project work and return this exact non-retryable diagnostic to the parent coordinator. "
            "Never list the Cortex ledger, substitute another briefing, or guess task identity."
        ),
    }


def _record_report_locked(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        raw_attempt_id = str(params.get("attempt_id") or "").strip()
        candidate_attempt_id = safe_id(raw_attempt_id) if raw_attempt_id else ""
        supplied_identity = str(params.get("principal") or params.get("thread_id") or "").strip()
        identity_candidates = []
        if supplied_identity:
            for item in state.get("attempts", []):
                if item.get("invalidated") or item.get("status") not in {"running", AWAITING_HOST_SPAWN}:
                    continue
                aliases = _attempt_identity_aliases(item)
                if supplied_identity in aliases:
                    identity_candidates.append(item)
        principal_correction = None
        try:
            authorize(state, params)
        except ValueError as exc:
            candidate_attempt = _attempt(state, candidate_attempt_id) if candidate_attempt_id else None
            if not candidate_attempt and len(identity_candidates) == 1:
                candidate_attempt = identity_candidates[0]
                candidate_attempt_id = candidate_attempt["attempt_id"]
            worker_aliases = set()
            if candidate_attempt:
                worker_aliases.update(_attempt_identity_aliases(candidate_attempt))
            if "different principal" not in str(exc) or not supplied_identity or not identity_candidates:
                raise
            if candidate_attempt and supplied_identity not in worker_aliases:
                raise
            # A native worker may identify itself by its exact canonical
            # profile.  It can publish only for its own active attempt; all
            # task mutations remain bound to the coordinator principal.
            authorize(state, {
                "principal": state.get("principal"),
                "thread_id": state.get("thread_id"),
                "project_root": str(select_project_root(params)),
            })
            principal_correction = {"requested": supplied_identity, "used": state.get("principal")}
        current_wave = active_gates(state)
        if not candidate_attempt_id:
            eligible = [
                item for item in state.get("attempts", [])
                if item.get("gate") in current_wave
                and item.get("status") in {"running", AWAITING_HOST_SPAWN}
                and not item.get("invalidated")
            ]
            if len(identity_candidates) > 1:
                return {
                    "recorded": False,
                    "reason": "delegation_attempt_required",
                    "candidate_attempt_ids": [item["attempt_id"] for item in identity_candidates],
                    "next_action": "retry_record_report_with_attempt_id",
                    "recoverable": True,
                    "principal_correction": principal_correction,
                    "state": state,
                }
            if len(identity_candidates) == 1:
                candidate_attempt_id = identity_candidates[0]["attempt_id"]
            elif len(eligible) == 1:
                candidate_attempt_id = eligible[0]["attempt_id"]
            else:
                return {
                    "recorded": False,
                    "reason": "delegation_attempt_required",
                    "candidate_attempt_ids": [item["attempt_id"] for item in identity_candidates or eligible],
                    "next_action": "retry_record_report_with_attempt_id",
                    "recoverable": True,
                    "principal_correction": principal_correction,
                    "state": state,
                }
        attempt_id = safe_id(candidate_attempt_id)
        attempt = _attempt(state, attempt_id)
        host_confirmation_pending = attempt.get("status") == AWAITING_HOST_SPAWN
        if attempt.get("invalidated") or attempt.get("status") not in {"running", AWAITING_HOST_SPAWN}:
            raise ValueError("cannot publish a report for an invalidated or terminal attempt")
        draft_document_key = None
        draft_path = None
        supplied_draft_ref = str(params.get("_draft_ref") or "").strip()
        if supplied_draft_ref:
            draft_envelope, _draft_metadata, draft_document_key, draft_path = _load_report_draft_file(
                root,
                task_dir,
                project_root=str(select_project_root(params)),
                task_id=state["task_id"],
                attempt_id=attempt_id,
                profile=str(attempt.get("profile") or ""),
                draft_ref=supplied_draft_ref,
            )
            params = {**params, **draft_envelope}
        open_questions = _open_blocking_questions(task_dir, state, attempt_id)
        if open_questions:
            refs = ", ".join(str(item["question_id"]) for item in open_questions)
            raise ValueError(
                f"cannot publish a report while blocking worker question(s) remain unanswered: {refs}; "
                "resume this same worker after the coordinator records the user answer"
            )
        report = sanitize_report_payload(params.get("report"))
        is_closure_gate = attempt.get("gate") in _GATE_RESULT_REQUIRED_GATES
        actor_ids = {str(params.get("principal") or "").strip(), str(params.get("profile") or "").strip()}
        actor_ids.update(str(alias).strip() for alias in _attempt_identity_aliases(attempt))
        closure = sanitize_closure_payload(params["closure"], actor_ids={item for item in actor_ids if item}) if params.get("closure") is not None else None
        gate_result = _runtime.sanitize_gate_result_payload(
            params["gate_result"], actor_ids={item for item in actor_ids if item}
        ) if params.get("gate_result") is not None else None
        if closure is not None and attempt.get("gate") not in _GATE_RESULT_REQUIRED_GATES:
            raise ValueError(
                "closure is only valid for review, governance review, and close attempts"
            )
        if gate_result is not None and closure is not None:
            compatible = {key: gate_result[key] for key in ("decision", "findings", "verification", "workspace")}
            if compatible != closure:
                raise ValueError("gate_result and closure must describe the same review/close outcome")
        result_validation = None
        if params.get("_require_gate_validation"):
            result_validation = _validate_gate_result_report(task_dir, state, attempt, report)
        if params.get("_require_close_validation"):
            _validate_close_report(task_dir, state, attempt, report)
        if is_closure_gate and gate_result is None and closure is None:
            if attempt.get("gate") in {"review", "close"}:
                raise ValueError(
                    "review and close reports require the canonical top-level gate_result"
                )
            raise ValueError(
                "governance review reports require the canonical top-level gate_result"
            )
        if gate_result is None and closure is not None:
            gate_result = {**closure, "failure_class": "product"}
        # ``closure`` remains accepted only as an input compatibility alias.
        # Canonical digests, artifacts, reports, and successor prompts contain
        # exactly one result envelope: gate_result.
        closure = None
        raw_planning = params.get("planning")
        planning = None
        if raw_planning is not None:
            if attempt.get("gate") != "plan" or attempt.get("profile") != "planner":
                raise ValueError("planning artifacts may be published only by the active planner attempt")
        _validate_report_decision_closure(task_dir, state, attempt, report)
        if params.get("_require_predecessor_review"):
            _validate_predecessor_review(report, list(attempt.get("context_report_ids") or []))
        if params.get("_require_knowledge_review"):
            _validate_knowledge_review(report, list(attempt.get("knowledge_index_files") or []))
        if params.get("_require_harvest_manifest"):
            _validate_harvest_coverage_manifest(
                select_project_root(params),
                load_task_definition(task_dir, state),
                str(attempt.get("gate") or ""),
            )
        if raw_planning is not None:
            planning = sanitize_planning_payload(raw_planning, persisted=bool(supplied_draft_ref))
        elif params.get("_require_plan_artifact") and attempt.get("gate") == "plan":
            raise ValueError("planner reports require a planning artifact with overview and work_packages")
        raw_scoping = params.get("scoping")
        scoping = None
        if raw_scoping is not None:
            if attempt.get("gate") != "scope" or attempt.get("profile") != "planner":
                raise ValueError("scoping artifacts may be published only by the active planner scope attempt")
            scoping = sanitize_scoping_payload(raw_scoping, persisted=bool(supplied_draft_ref))
        elif params.get("_require_scope_artifact") and attempt.get("gate") == "scope":
            raise ValueError(
                "planner scope reports require a scoping artifact with overview, context_files, and discovery_domains"
            )
        resolved_user_decisions = _resolved_user_decisions(task_dir, state)
        # Idempotency covers the worker-authored envelope. The automatically
        # attached decision snapshot is independently bound by the immutable
        # artifact digest and must not make an identical retry fail merely
        # because another parallel answer arrived after the first commit.
        digest_payload = {"report": report, "planning": planning}
        if scoping is not None:
            digest_payload["scoping"] = scoping
        if gate_result is not None:
            digest_payload["gate_result"] = gate_result
        if closure is not None:
            digest_payload["closure"] = closure
        content_digest = digest_text(json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        raw_submission_id = str(params.get("submission_id") or "").strip()
        submission_id = safe_id(raw_submission_id) if raw_submission_id else f"submission-{attempt_id}-report-{content_digest[:16]}"
        paths = report_bus_paths(task_dir)
        index = _report_index(paths, state["task_id"])
        submission_key = f"{attempt_id}:{submission_id}"
        authoritative: list[dict[str, Any]] = []
        occupied_numbers: list[int] = []
        for metadata in index.get("reports", []):
            report_id = safe_id(str(metadata.get("report_id") or ""))
            match = re.fullmatch(r"report-(\d+)", report_id)
            if not report_id or match is None:
                raise ValueError("SQLite report index contains an invalid report id")
            record, _ = _runtime.read_immutable_json_artifact(
                task_dir,
                state["task_id"],
                f"reports/records/{report_id}.json",
                kinds={"worker_report"},
            )
            if record.get("task_id") != state["task_id"] or record.get("report_id") != report_id:
                raise ValueError("SQLite report artifact crosses task scope")
            occupied_numbers.append(int(match.group(1)))
            authoritative.append(record)
        # A process can fail after committing immutable content but before it
        # updates the small mutable report index. Rebuild only that missing
        # index entry from the task-scoped SQLite artifact catalog; filesystem
        # exports are deliberately not consulted.
        indexed_ids = {str(item.get("report_id") or "") for item in index.get("reports", []) if isinstance(item, dict)}
        artifact_offset = 0
        while True:
            artifacts, next_offset = _runtime.db_list_artifacts(
                root,
                state["task_id"],
                kind="worker_report",
                offset=artifact_offset,
                page_size=100,
            )
            for artifact in artifacts:
                export_path = str(artifact.get("export_path") or "")
                match = re.fullmatch(r"reports/records/(report-\d+)\.json", export_path)
                if match is None or match.group(1) in indexed_ids:
                    continue
                record, _ = _runtime.read_immutable_json_artifact(
                    task_dir,
                    state["task_id"],
                    export_path,
                    kinds={"worker_report"},
                )
                if record.get("task_id") != state["task_id"] or record.get("report_id") != match.group(1):
                    raise ValueError("SQLite report artifact crosses task scope")
                authoritative.append(record)
                occupied_numbers.append(int(match.group(1).removeprefix("report-")))
            if next_offset is None:
                break
            artifact_offset = next_offset
        authoritative.sort(key=lambda item: safe_id(str(item.get("report_id") or "")))
        rebuilt_metadata = [_runtime._report_metadata(item) for item in authoritative]
        rebuilt_submissions = {
            f"{item.get('attempt_id')}:{item.get('submission_id')}": safe_id(str(item.get("report_id") or ""))
            for item in authoritative
        }
        if rebuilt_metadata != index.get("reports", []) or rebuilt_submissions != index.get("submissions", {}):
            index = {
                "schema": REPORT_SCHEMA,
                "task_id": state["task_id"],
                "reports": rebuilt_metadata,
                "submissions": rebuilt_submissions,
                "updated_at": now(),
            }
            _write_report_index(paths, state["task_id"], index)
        existing = next((item for item in authoritative if f"{item.get('attempt_id')}:{item.get('submission_id')}" == submission_key), None)
        existing_id = existing.get("report_id") if existing else None
        if existing_id:
            if existing.get("content_digest") != content_digest:
                raise ValueError("idempotent report submission_id was reused with different content")
            attempt = _attempt(state, safe_id(str(existing["attempt_id"])))
            if isinstance(existing.get("planning"), dict):
                materialize_planning_artifacts(
                    task_dir, state, attempt, str(existing["report_id"]),
                    sanitize_report_payload(existing.get("report")),
                    sanitize_planning_payload(existing["planning"], persisted=True),
                )
            receipt, _ = _recover_report_receipt(paths, existing, state, bool(attempt.get("invalidated")))
            # The receipt and Markdown artifacts were committed with the
            # first submission.  Re-storing them here may create a distinct
            # immutable artifact for an already-owned export path (for
            # example after receipt recovery adjusts metadata).  The public
            # use case schedules all three canonical artifacts for repair
            # after this return, preserving retry semantics without a
            # duplicate logical export registration.
            outcome = {"idempotent": True, "report": existing, "receipt": receipt, "host_confirmation_pending": host_confirmation_pending, "principal_correction": principal_correction, "state": state}
            if draft_document_key is not None:
                assert draft_path is not None
                outcome["_draft_cleanup"] = {
                    "task_id": state["task_id"],
                    "document_key": draft_document_key,
                    "draft_path": str(draft_path),
                }
            return outcome
        attempt_count = sum(1 for item in authoritative if item.get("attempt_id") == attempt_id)
        if attempt_count >= _runtime.MAX_REPORTS_PER_ATTEMPT:
            raise ValueError("per-attempt report count quota exhausted")
        report_id = f"report-{max(occupied_numbers, default=0) + 1:04d}"
        record = {
            "schema": REPORT_SCHEMA, "report_id": report_id, "task_id": state["task_id"],
            "gate": attempt["gate"], "attempt_id": attempt_id, "submission_id": submission_id,
            "producer": {"profile": attempt["profile"], "model": attempt["selected_model"], "reasoning_effort": attempt["selected_reasoning_effort"]},
            "report": report, "planning": planning,
            "resolved_user_decisions": resolved_user_decisions,
            **({"scoping": scoping} if scoping is not None else {}),
            "result_validation": result_validation,
            **({"gate_result": gate_result} if gate_result is not None else {}),
            **({"closure": closure} if closure is not None else {}),
            "content_digest": content_digest, "created_at": now(),
        }
        report_artifact = store_immutable_artifact(
            task_dir,
            state["task_id"],
            kind="worker_report",
            title=f"reports/records/{report_id}.json",
            mime_type="application/json",
            content=json.dumps(
                # The durable database copy is the complete authoritative
                # report record.  The materialized JSON file receives the
                # artifact references afterwards and is an export/repair
                # view, never the sole source for a report read.
                record,
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ),
            export_path=f"reports/records/{report_id}.json",
        )
        record["report_artifact_ref"] = report_artifact["artifact_ref"]
        canonical_gate_result = gate_result or closure
        if canonical_gate_result is not None:
            for finding in canonical_gate_result["findings"]:
                _runtime.db_upsert_task_finding(root, state["task_id"], finding, source={"report_id": report_id, "attempt_id": attempt_id})
            missing_checks = canonical_gate_result["verification"]["required_missing"]
            verification_finding = {
                "fingerprint": "verification-required-missing",
                "severity": "P1" if missing_checks else "P3",
                "status": "open" if missing_checks else "resolved",
                "blocking": bool(missing_checks),
                "summary": "Required verification is missing" if missing_checks else "Required verification is complete",
                "details": missing_checks,
            }
            _runtime.db_upsert_task_finding(root, state["task_id"], verification_finding, source={"report_id": report_id, "attempt_id": attempt_id, "kind": "verification"})
        receipt = {
            "schema": REPORT_SCHEMA, "receipt_id": f"report-receipt-{report_id}", "report_id": report_id,
            "task_id": state["task_id"], "gate": attempt["gate"], "attempt_id": attempt_id,
            "content_digest": content_digest, "consumed_at": None, "consumed_by_evidence_id": None,
            "invalidated": False, "created_at": now(),
        }
        receipt_artifact = store_immutable_artifact(
            task_dir,
            state["task_id"],
            kind="report_receipt",
            title=f"reports/receipts/{receipt['receipt_id']}.json",
            mime_type="application/json",
            content=json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            export_path=f"reports/receipts/{receipt['receipt_id']}.json",
        )
        markdown = _report_markdown(record)
        markdown_artifact = store_immutable_artifact(
            task_dir,
            state["task_id"],
            kind="report_markdown",
            title=f"reports/markdown/{report_id}.md",
            mime_type="text/markdown",
            content=markdown,
            export_path=f"reports/markdown/{report_id}.md",
        )
        record["markdown_artifact_ref"] = markdown_artifact["artifact_ref"]
        # Every task file is a replaceable export. Reconcile from the database
        # rather than treating an existing projection as a report collision.
        projection_root = _runtime._ledger_root_for_artifact(task_dir)
        enqueue_projection(root=projection_root, task_id=state["task_id"], artifact_id=report_artifact["artifact_ref"], projection_type="report_json", export_path=f"reports/records/{report_id}.json")
        enqueue_projection(root=projection_root, task_id=state["task_id"], artifact_id=receipt_artifact["artifact_ref"], projection_type="report_receipt", export_path=f"reports/receipts/{receipt['receipt_id']}.json")
        enqueue_projection(root=projection_root, task_id=state["task_id"], artifact_id=markdown_artifact["artifact_ref"], projection_type="report_markdown", export_path=f"reports/markdown/{report_id}.md")
        if planning is not None:
            materialize_planning_artifacts(task_dir, state, attempt, report_id, report, planning)
        index.setdefault("reports", []).append(_report_metadata(record))
        index.setdefault("submissions", {})[submission_key] = report_id
        index["updated_at"] = now()
        _write_report_index(paths, state["task_id"], index)
        _, delegation_index = _delegation_report_index(paths, state["task_id"], attempt_id)
        delegation_index["owned_report_ids"] = sorted(set(delegation_index.get("owned_report_ids", [])) | {report_id})
        delegation_index["updated_at"] = now()
        _write_delegation_report_index(paths, state["task_id"], attempt_id, delegation_index)
        append_journal_best_effort(task_dir, "report", f"{attempt_id} published {report_id}")
        outcome = {"idempotent": False, "report": record, "receipt": receipt, "host_confirmation_pending": host_confirmation_pending, "principal_correction": principal_correction, "state": state}
        if draft_document_key is not None:
            assert draft_path is not None
            outcome["_draft_cleanup"] = {
                "task_id": state["task_id"],
                "document_key": draft_document_key,
                "draft_path": str(draft_path),
            }
        return outcome


def _restore_report_projections(result: dict[str, Any], params: dict[str, Any]) -> None:
    """Leave optional report exports in the durable outbox after commit.

    Recording a report must succeed even if a workstation cannot currently
    create a Markdown or JSON projection.  The canonical artifacts and their
    projection intents are committed by ``_record_report_locked``; explicit
    report reads or reconciliation can materialize them later.
    """
    cleanup = result.pop("_draft_cleanup", None)
    if not isinstance(cleanup, dict):
        return
    try:
        root = ledger_root(params)
        task_id = safe_id(str(cleanup["task_id"]))
        document_key = str(cleanup["document_key"])
        draft_path = Path(str(cleanup["draft_path"]))
        _contained_path(root / "tasks", draft_path, "report draft cleanup")
        with state_lock(root):
            _delete_report_draft(
                root,
                task_id=task_id,
                document_key=document_key,
                draft_path=draft_path,
            )
    except (KeyError, OSError, ValueError):
        # The report is already committed. A later get_report_template call
        # supersedes stale draft metadata and retries file cleanup without
        # turning successful report persistence into a worker failure.
        return


# The public callable is now a vertical-slice facade.  Its adapters retain the
# established mutation code while exposing explicit ports for its eventual
# repository-level replacement; both public module paths keep the same result
# protocol and object identity through this direct alias.
_RECORD_REPORT_FACADE = build_compatibility_facade(
    mutation=_record_report_locked,
    restore_projections=_restore_report_projections,
)
record_report = _RECORD_REPORT_FACADE.record_report


def list_task_reports(params: dict[str, Any]) -> dict[str, Any]:
    _, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    index = _report_index(report_bus_paths(task_dir), state["task_id"])
    return {"schema": REPORT_SCHEMA, "task_id": state["task_id"], "reports": index.get("reports", [])}


def _publish_worker_report(params: dict[str, Any]) -> dict[str, Any]:
    """Run the shared public report adapter as one atomic validation and persistence operation."""
    try:
        unknown = sorted(set(params) - {
            "project_root", "task_id", "attempt_id", "profile", "report", "scoping", "planning",
            "gate_result", "closure", "draft_ref",
        })
        if unknown:
            raise ValueError("unsupported record_report fields: " + ", ".join(unknown))
        for field in ("project_root", "task_id", "attempt_id", "profile"):
            if not str(params.get(field) or "").strip():
                raise ValueError(f"{field} is required; copy the exact value from this worker's Cortex briefing")
        profile = canonical_profile(params.get("profile") or "")
        if profile not in AGENTS:
            raise ValueError("profile must be an exact Cortex worker profile")
        draft_ref = str(params.get("draft_ref") or "").strip()
        payload_fields = {"report", "scoping", "planning", "gate_result", "closure"} & set(params)
        if draft_ref and payload_fields:
            raise ValueError(
                "record_report with draft_ref must not resend report, scoping, planning, gate_result, or closure"
            )
        result = _runtime.record_report({
            "project_root": params.get("project_root"),
            "task_id": params.get("task_id"),
            "attempt_id": params.get("attempt_id"),
            "principal": profile,
            "report": params.get("report"),
            "scoping": params.get("scoping"),
            "planning": params.get("planning"),
            "gate_result": params.get("gate_result"),
            "closure": params.get("closure"),
            "_draft_ref": draft_ref,
            "_require_predecessor_review": True,
            "_require_knowledge_review": True,
            "_require_harvest_manifest": True,
            "_require_plan_artifact": True,
            "_require_scope_artifact": True,
            "_require_gate_validation": True,
            "_require_close_validation": True,
        })
    except ValueError as exc:
        message = str(exc)
        if "blocking worker question(s) remain unanswered" in message:
            code = "blocking_question_open"
            outcome = "awaiting_user"
            next_action = (
                "Return this exact blocker to the parent coordinator, remain available, poll the question answer "
                "on this same attempt after the coordinator signals it, then resume before recording a report."
            )
        elif "final report questions must be empty" in message:
            code = "unresolved_report_questions"
            outcome = "needs_input"
            next_action = (
                "Do not delete or disguise a material question. Publish it with worker_question(action=ask), return "
                "the question_ref to the coordinator, and resume this same attempt after the user answers. Move only "
                "genuinely non-blocking evidence limitations to report.uncertainty."
            )
        elif "intent clarification required before this phase" in message:
            code = "intent_clarification_required"
            outcome = "needs_input"
            next_action = (
                "Call worker_question(action=ask) now with the smallest material product-intent question and useful "
                "options. Return its question_ref plus a complete decision handoff with context, concrete options, "
                "trade-offs, and a recommendation; do not record a report until the user "
                "answers and this same attempt resumes."
            )
        elif (
            "acknowledge every supplied predecessor handoff" in message
            or "acknowledge every available repository knowledge index" in message
            or "acknowledge the immutable dispatch briefing" in message
        ):
            code = "report_evidence_incomplete"
            outcome = "needs_correction"
            next_action = (
                "Complete the required review, copy the exact generated acknowledgement from the diagnostic into "
                "report.evidence as one string item, then retry record_report on this same attempt. If another "
                "caller-correctable validation diagnostic is returned, correct it and retry again without ending "
                "the worker or changing the attempt."
            )
        elif "dispatch briefing" in message:
            code = "dispatch_briefing_invalid"
            outcome = "blocked"
            next_action = (
                "Stop this worker and preserve the exact diagnostic. The issued immutable briefing is missing, "
                "writable, out of scope, or digest-mismatched; never substitute another Cortex file or continue."
            )
        elif "English-only" in message:
            code = "worker_output_language_violation"
            outcome = "needs_correction"
            next_action = (
                "Rewrite every worker-authored report field in English. Keep the durable worker protocol in English; "
                "only the main coordinator may localize content for the user, then retry record_report on this same "
                "attempt. Continue correcting caller-correctable validation diagnostics until the report is accepted."
            )
        elif "changed_files" in message:
            code = "report_changed_files_invalid"
            outcome = "needs_correction"
            next_action = (
                "Keep only safe project-relative file paths in report.changed_files, move explanatory prose to "
                "findings or evidence, then retry record_report on this same attempt. Continue correcting any later "
                "caller-correctable validation diagnostic until the report is accepted."
            )
        elif any(fragment in message for fragment in (
            "does not exist", "does not belong to this task", "owned by a different principal",
            "profile must be an exact Cortex worker profile", "attempt_id", "task_id",
            "invalidated or terminal attempt",
        )):
            code = "report_identity_invalid"
            outcome = "needs_correction"
            next_action = (
                "Use the exact project_root, task_id, attempt_id, and profile copied from this worker's Cortex "
                "briefing. Do not guess or borrow identity from another task; if the exact values are unavailable, "
                "return this diagnostic to the parent coordinator and stop."
            )
        elif any(fragment in message for fragment in (
            "canonical harvest project document",
            "harvest canonical project documentation",
            "harvest project index",
            "harvest coverage manifest",
            "harvest coverage matrix",
            "harvest feature pages",
        )):
            code = "harvest_manifest_invalid"
            outcome = "needs_correction"
            next_action = (
                "Complete and verify the canonical harvest project documents, coverage manifest, and feature pages "
                "named by the diagnostic before retrying record_report on this same attempt."
            )
        elif "unsuccessful executed check(s)" in message:
            code = "worker_verification_failed"
            outcome = "failed"
            next_action = (
                "Do not omit, disguise, or relabel the failing check. If the defect is inside this worker's allowed "
                "write scope, correct it and rerun every affected check before retrying record_report; otherwise "
                "return this exact error and a short blocker to the coordinator so Cortex can authorize rework."
            )
        elif any(fragment in message for fragment in (
            "unsupported record_report fields", "report must contain exactly", "report summary and next_action",
            "report findings must", "report questions must", "report tests must", "report evidence must",
            "report uncertainty must", "report exceeds the", "per-attempt report count quota exhausted",
            "idempotent report submission_id",
            "draft_ref is invalid", "report draft", "record_report with draft_ref",
            "project_root is required", "project_root must be an absolute path", "CORTEX_ROOT is not supported",
            "scoping ", "planner scope reports require", "planning ", "planner reports require", "C2/C3 close report",
            "result requires", "result evidence", "result contains unresolved", "result test", "read-only result gate",
            "project files changed during read-only",
            "review and close reports require the canonical top-level gate_result",
            "governance review reports require the canonical top-level gate_result",
            "closure ", "gate_result ", "non-pass gate_result ",
        )):
            code = "report_validation_failed"
            outcome = "needs_correction"
            next_action = (
                "Correct only the report fields named by the diagnostic and retry record_report on this same task "
                "and attempt. Repeat for every later caller-correctable validation diagnostic until the report is "
                "accepted; rejected validation calls do not create a worker failure, and pipeline rework has no attempt budget. Do not guess "
                "identity, remove required evidence, end the worker, or paste the report into the parent channel."
            )
        else:
            raise
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": outcome,
            "code": code,
            "diagnostics": [{"code": code, "message": redact(message, 1000)}],
            "next_action": next_action,
            "retryable": outcome == "needs_correction",
            "attempt_budget_consumed": False,
        }
    if result.get("recorded") is False:
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": "report_rejected",
            "code": result.get("reason") or "report_rejected",
            "diagnostics": [{
                "code": result.get("reason") or "report_rejected",
                "message": result.get("reason") or "Cortex rejected the worker report.",
            }],
            "next_action": "Return the exact report error to the parent coordinator; do not paste the report body into the parent channel.",
        }
    record = result["report"]
    receipt = result["receipt"]
    return {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "outcome": "report_recorded",
        "report_ref": record["report_id"],
        "receipt_ref": receipt["receipt_id"],
        "summary": redact(record.get("report", {}).get("summary", ""), 500),
        "report_transport_status": "recorded",
        "gate_decision": (
            (record.get("gate_result") or {}).get("decision")
            if isinstance(record.get("gate_result"), dict) else "reported"
        ),
        "idempotent": bool(result.get("idempotent")),
        "next_action": "Return only REPORT_RECORDED, report_ref, and at most a two-sentence summary to the parent coordinator.",
    }


def publish_worker_report(params: dict[str, Any]) -> dict[str, Any]:
    """Public worker adapter: update, validate, and atomically persist one report draft."""
    draft_ref = str(params.get("draft_ref") or "").strip()
    if not draft_ref:
        return _publish_worker_report(params)
    prepared, invalid = _prepare_draft_for_record(params)
    if invalid is not None:
        return invalid
    assert prepared is not None
    return _draft_record_failure(_publish_worker_report(prepared), draft_ref=draft_ref)


def _draft_diagnostic_path(message: str) -> str:
    """Map a bounded validation message to the most actionable payload path."""
    lowered = message.lower()
    match = re.search(r"result test (\d+)", lowered)
    if match:
        index = max(int(match.group(1)) - 1, 0)
        if "evidence" in lowered or "observed output" in lowered:
            return f"report.tests[{index}].evidence"
        if "command" in lowered:
            return f"report.tests[{index}].command"
        if "cwd" in lowered:
            return f"report.tests[{index}].cwd"
        if "exit_code" in lowered or "unsuccessful" in lowered:
            return f"report.tests[{index}].exit_code"
        return f"report.tests[{index}]"
    for marker, path in (
        ("changed_files", "report.changed_files"),
        ("report summary", "report.summary"),
        ("report findings", "report.findings"),
        ("report questions", "report.questions"),
        ("final report questions", "report.questions"),
        ("report tests", "report.tests"),
        ("report evidence", "report.evidence"),
        ("result evidence", "report.evidence"),
        ("report uncertainty", "report.uncertainty"),
        ("planning package", "planning.work_packages"),
        ("planning ", "planning"),
        ("planner reports require", "planning"),
        ("scoping ", "scoping"),
        ("planner scope reports require", "scoping"),
        ("gate_result", "gate_result"),
        ("closure", "closure"),
        ("profile", "profile"),
        ("attempt_id", "attempt_id"),
        ("task_id", "task_id"),
        ("project_root", "project_root"),
    ):
        if marker in lowered:
            return path
    if "report must contain exactly" in lowered or "unsupported record_report fields" in lowered:
        return "report"
    return "$"


def _draft_shape_diagnostics(params: dict[str, Any]) -> list[dict[str, str]]:
    """Collect independent JSON-shape errors before dependent semantic checks."""
    diagnostics: list[dict[str, str]] = []
    report = params.get("report")
    if not isinstance(report, dict):
        return [{
            "code": "report_field_invalid",
            "path": "report",
            "message": "report must be one JSON object containing the complete cortex/report/v1 draft",
            "fix": "Set report to one object with exactly: " + ", ".join(REPORT_FIELDS) + ".",
        }]
    expected = set(REPORT_FIELDS)
    for field in sorted(expected - set(report)):
        diagnostics.append({
            "code": "report_field_missing",
            "path": f"report.{field}",
            "message": f"required report field is missing: {field}",
            "fix": f"Add report.{field}; use [] only when this array field has no observed items.",
        })
    for field in sorted(set(report) - expected):
        diagnostics.append({
            "code": "report_field_unknown",
            "path": f"report.{field}",
            "message": f"unsupported report field: {field}",
            "fix": f"Remove report.{field}; move relevant observed detail into findings, evidence, or uncertainty.",
        })
    if "summary" in report and (not isinstance(report["summary"], str) or not report["summary"].strip()):
        diagnostics.append({
            "code": "report_field_invalid",
            "path": "report.summary",
            "message": "report.summary must be a non-empty string",
            "fix": "Replace report.summary with a concise English result summary grounded in completed work.",
        })
    for field in ("findings", "questions", "changed_files", "tests", "evidence", "uncertainty"):
        if field in report and not isinstance(report[field], list):
            diagnostics.append({
                "code": "report_field_invalid",
                "path": f"report.{field}",
                "message": f"report.{field} must be an array",
                "fix": f"Replace report.{field} with a JSON array; use [] only when no items are required.",
            })
    tests = report.get("tests")
    if isinstance(tests, list):
        required_test_fields = {"command", "cwd", "exit_code", "evidence"}
        for index, item in enumerate(tests):
            if not isinstance(item, dict):
                diagnostics.append({
                    "code": "report_test_invalid",
                    "path": f"report.tests[{index}]",
                    "message": "each report.tests item must be one object",
                    "fix": "Use exactly command, cwd, exit_code, and evidence for this test item.",
                })
                continue
            for field in sorted(required_test_fields - set(item)):
                diagnostics.append({
                    "code": "report_test_field_missing",
                    "path": f"report.tests[{index}].{field}",
                    "message": f"required test field is missing: {field}",
                    "fix": f"Add report.tests[{index}].{field} with the exact observed value.",
                })
            for field in sorted(set(item) - required_test_fields):
                diagnostics.append({
                    "code": "report_test_field_unknown",
                    "path": f"report.tests[{index}].{field}",
                    "message": f"unsupported test field: {field}",
                    "fix": f"Remove report.tests[{index}].{field}; each test has exactly four fields.",
                })
    return diagnostics


def _draft_placeholder_diagnostics(value: Any, path: str = "$") -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            diagnostics.extend(_draft_placeholder_diagnostics(item, f"{path}.{key}" if path != "$" else key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            diagnostics.extend(_draft_placeholder_diagnostics(item, f"{path}[{index}]"))
    elif isinstance(value, str) and "<replace" in value.lower():
        diagnostics.append({
            "code": "report_placeholder_unresolved",
            "path": path,
            "message": f"template placeholder remains unresolved at {path}",
            "fix": f"Replace {path} with concrete observed data in the existing draft file.",
        })
    return diagnostics


def _draft_invalid_result(
    diagnostics: list[dict[str, str]],
    *,
    draft_ref: str | None = None,
    draft_path: Path | None = None,
    draft_persisted: bool | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "outcome": "report_draft_invalid",
        "code": "report_validation_failed",
        "draft_valid": False,
        "persisted": False,
        "draft_persisted": bool(draft_ref and draft_path) if draft_persisted is None else draft_persisted,
        "diagnostics": diagnostics,
        "retryable": True,
        "attempt_budget_consumed": False,
        "next_action": (
            "Apply only the diagnostic fixes to the existing draft file or send a small JSON Merge Patch, then "
            "call record_report again with the same draft_ref. Draft validation and rejected record attempts never "
            "consume worker attempts."
        ),
    }
    if draft_ref:
        result["draft_ref"] = draft_ref
    if draft_path is not None:
        result["draft_path"] = str(draft_path)
    return result


def _prepare_draft_for_record(params: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Apply an optional update and preflight the private draft before atomic recording."""
    draft_ref = str(params.get("draft_ref") or "").strip()
    draft_path: Path | None = None
    try:
        allowed = {
            "project_root", "task_id", "attempt_id", "profile", "draft_ref", "patch",
        } | _REPORT_DRAFT_PAYLOAD_FIELDS
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ValueError("unsupported record_report fields: " + ", ".join(unknown))
        for field in ("project_root", "task_id", "attempt_id", "profile", "draft_ref"):
            if not str(params.get(field) or "").strip():
                raise ValueError(f"{field} is required; copy it exactly from get_report_template")
        project_root = str(select_project_root(params))
        root = ledger_root(params)
        identity = {
            "project_root": project_root,
            "task_id": safe_id(str(params["task_id"])),
            "attempt_id": safe_id(str(params["attempt_id"])),
            "profile": canonical_profile(params["profile"]),
        }
        with state_lock(root):
            task_dir, state, attempt, profile = _public_worker_template_context(identity)
            envelope, _metadata, _document_key, draft_path = _load_report_draft_file(
                root,
                task_dir,
                project_root=project_root,
                task_id=state["task_id"],
                attempt_id=attempt["attempt_id"],
                profile=profile,
                draft_ref=draft_ref,
            )
            payload_fields = _REPORT_DRAFT_PAYLOAD_FIELDS & set(params)
            patch = params.get("patch")
            if patch is not None and payload_fields:
                raise ValueError("patch must not be combined with a complete report envelope replacement")
            if patch is not None:
                if not isinstance(patch, dict):
                    raise ValueError("patch must be one JSON Merge Patch object")
                unknown_patch = sorted(set(patch) - _REPORT_DRAFT_PAYLOAD_FIELDS)
                if unknown_patch:
                    raise ValueError(
                        "patch may change only report, scoping, planning, gate_result, or closure: "
                        + ", ".join(unknown_patch)
                    )
                envelope = _merge_patch(envelope, patch)
                _write_report_draft_file(draft_path, envelope)
            elif payload_fields:
                if "report" not in payload_fields:
                    raise ValueError("a complete draft replacement requires report")
                envelope = {**identity, **{field: params[field] for field in payload_fields}}
                _write_report_draft_file(draft_path, envelope)

            diagnostics = _draft_shape_diagnostics(envelope)
            diagnostics.extend(_draft_placeholder_diagnostics(envelope))
            if diagnostics:
                return None, _draft_invalid_result(diagnostics, draft_ref=draft_ref, draft_path=draft_path)
            return {**identity, "draft_ref": draft_ref}, None
    except ValueError as exc:
        message = redact(str(exc), 1000)
        return None, _draft_invalid_result([{
            "code": "report_validation_failed",
            "path": _draft_diagnostic_path(message),
            "message": message,
            "fix": "Use the exact draft_ref and worker identity from get_report_template, then correct only the named field.",
        }], draft_ref=draft_ref or None, draft_path=draft_path)


def _draft_record_failure(result: dict[str, Any], *, draft_ref: str) -> dict[str, Any]:
    """Keep a rejected draft mutable while preserving the normal report diagnostics."""
    if result.get("outcome") != "needs_correction":
        return result
    diagnostics = []
    for item in result.get("diagnostics", []):
        message = str(item.get("message") or "Report draft validation failed.")
        diagnostics.append({
            "code": item.get("code") or result.get("code") or "report_validation_failed",
            "path": _draft_diagnostic_path(message),
            "message": message,
            "fix": result.get("next_action") or "Correct the named field in the existing draft file.",
        })
    # _prepare_draft_for_record loaded this exact draft and no rejected record
    # path removes it, so callers can safely retry against the same ref.
    return _draft_invalid_result(diagnostics, draft_ref=draft_ref, draft_persisted=True)


def _public_worker_template_context(params: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any], str]:
    allowed = {"project_root", "task_id", "attempt_id", "profile"}
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ValueError("unsupported get_report_template fields: " + ", ".join(unknown))
    for field in allowed:
        if not str(params.get(field) or "").strip():
            raise ValueError(f"{field} is required; copy the exact value from this worker's Cortex briefing")
    project = select_project_root(params)
    task_id = safe_id(str(params["task_id"]))
    attempt_id = safe_id(str(params["attempt_id"]))
    profile = canonical_profile(params["profile"])
    if profile not in AGENTS:
        raise ValueError("profile must be an exact Cortex worker profile")
    _, task_dir, state = load_state(task_id, {"project_root": str(project)})
    attempt = _attempt(state, attempt_id)
    if attempt.get("invalidated") or attempt.get("status") not in {AWAITING_HOST_SPAWN, "running"}:
        raise ValueError("get_report_template requires an active, non-invalidated worker attempt")
    if attempt.get("profile") != profile:
        raise ValueError("profile does not match the exact dispatched worker")
    return task_dir, state, attempt, profile


def get_report_template(params: dict[str, Any]) -> dict[str, Any]:
    """Return the exact report skeleton and requirements for one active worker attempt."""
    try:
        task_dir, state, attempt, profile = _public_worker_template_context(params)
        gate = str(attempt.get("gate") or "")
        task = load_task_definition(task_dir, state)
        evidence = [dispatch_briefing_review_marker(str(attempt.get("briefing_digest") or ""))]
        predecessor_refs = sorted(str(item) for item in attempt.get("context_report_ids") or [])
        if predecessor_refs:
            evidence.append(_predecessor_review_marker(predecessor_refs))
        knowledge_indexes = sorted(str(item) for item in attempt.get("knowledge_index_files") or [])
        if knowledge_indexes:
            evidence.append("Knowledge reviewed: " + ", ".join(knowledge_indexes))
        evidence.extend(
            prefix + "<replace with concrete observed proof>"
            for prefix, _criterion in _result_contract_markers(attempt, task)
        )
        report = {
            "summary": "<replace with concise result summary>",
            "findings": [],
            "questions": [],
            "changed_files": (
                ["<replace with each changed project-relative path>"]
                if gate in WRITE_REQUIRED_RESULT_GATES else []
            ),
            "tests": (
                [{
                    "command": "<replace with exact executed command>",
                    "cwd": "<replace with project root or safe relative directory>",
                    "exit_code": 0,
                    "evidence": "<replace with concrete observed output or behavior>",
                }]
                if gate in EXECUTED_CHECK_RESULT_GATES else []
            ),
            "evidence": evidence,
            "uncertainty": [],
        }
        template: dict[str, Any] = {
            "project_root": str(select_project_root(params)),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": profile,
            "report": report,
        }
        required_top_level = ["project_root", "task_id", "attempt_id", "profile", "report"]
        if gate == "scope" and profile == "planner":
            template["scoping"] = {
                "overview": "<replace with evidence-backed scope overview>",
                "context_files": [],
                "discovery_domains": [{
                    "id": "<replace with stable_domain_id>",
                    "title": "<replace with domain title>",
                    "objective": "<replace with domain objective>",
                    "paths": ["<replace with project-relative path>"],
                    "context": ["<replace with verified context>"],
                    "depends_on": [],
                    "acceptance_criteria": ["<replace with observable acceptance criterion>"],
                    "verification": ["<replace with exact verification>"],
                }],
            }
            required_top_level.append("scoping")
        if gate == "plan" and profile == "planner":
            template["planning"] = {
                "overview": "<replace with implementation plan overview>",
                "work_packages": [{
                    "id": "<replace with stable_package_id>",
                    "title": "<replace with package title>",
                    "objective": "<replace with package objective>",
                    "allowed_paths": ["<replace with narrow project-relative path>"],
                    "depends_on": [],
                    "microtasks": [{
                        "id": "<replace with globally_unique_microtask_id>",
                        "title": "<replace with microtask title>",
                        "objective": "<replace with microtask objective>",
                        "profile": "<replace with canonical implementation profile>",
                        "allowed_paths": ["<replace with narrow project-relative path>"],
                        "depends_on": [],
                        "acceptance_criteria": ["<replace with observable acceptance criterion>"],
                        "verification": ["<replace with exact verification>"],
                    }],
                }],
            }
            required_top_level.append("planning")
        if gate in _GATE_RESULT_REQUIRED_GATES:
            template["gate_result"] = {
                "decision": "pass",
                "failure_class": "product",
                "findings": [],
                "verification": {
                    "executed": [], "not_executed": [], "required_missing": [], "limitations": [],
                },
                "workspace": {
                    "modified": [], "untracked": [], "staged": [], "committed": "not_required",
                },
            }
            required_top_level.append("gate_result")
        project_root = str(select_project_root(params))
        root = ledger_root(params)
        with state_lock(root):
            locked_task_dir, locked_state, locked_attempt, locked_profile = _public_worker_template_context(params)
            if (
                locked_task_dir != task_dir
                or locked_state["task_id"] != state["task_id"]
                or locked_attempt["attempt_id"] != attempt["attempt_id"]
                or locked_profile != profile
            ):
                raise ValueError("worker attempt changed while the report template was being prepared")
            metadata, draft_path = _stage_report_draft_file(
                root,
                task_dir,
                project_root=project_root,
                task_id=state["task_id"],
                attempt_id=attempt["attempt_id"],
                profile=profile,
                envelope=template,
            )
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "report_template_ready",
            "gate": gate,
            "required_top_level": required_top_level,
            "draft_ref": metadata["draft_ref"],
            "draft_path": str(draft_path),
            "draft_expires_at": metadata["expires_at"],
            "placeholders_must_be_replaced": True,
            "persisted": False,
            "draft_persisted": True,
            "attempt_budget_consumed": False,
            "next_action": (
                "Open draft_path and replace every angle-bracket placeholder with observed data while keeping all "
                "required keys, then call record_report with this worker identity and draft_ref only. If the worker "
                "sandbox cannot edit the file, send one full replacement once or a small patch instead."
            ),
        }
    except (ValueError, OSError) as exc:
        message = redact(str(exc), 1000)
        terminal = isinstance(exc, OSError) or "requires an active, non-invalidated worker attempt" in message.lower()
        code = "report_template_unavailable" if terminal else "report_template_request_invalid"
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": "blocked" if terminal else "needs_correction",
            "code": code,
            "diagnostics": [{
                "code": code,
                "path": _draft_diagnostic_path(message),
                "message": message,
                "fix": (
                    "The worker attempt is no longer active; do not substitute another identity."
                    if terminal else
                    "Correct only the named field using the exact active briefing identity, then retry "
                    "get_report_template on this same worker attempt."
                ),
            }],
            "persisted": False,
            "retryable": not terminal,
            "attempt_budget_consumed": False,
            "next_action": (
                "Stop because this response is explicitly non-retryable."
                if terminal else
                "Correct the diagnostic field and retry get_report_template on this same attempt; rejected caller "
                "validation does not consume an attempt and must not end the worker."
            ),
        }


def read_dispatch_briefing(params: dict[str, Any]) -> dict[str, Any]:
    """Read exactly one active worker's immutable briefing as a scoped fallback."""
    try:
        allowed = {
            "project_root", "task_id", "attempt_id", "profile",
            "dispatch_ref", "briefing_digest", "cursor", "max_bytes",
        }
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
        if (
            attempt.get("invalidated")
            or attempt.get("status") not in {AWAITING_HOST_SPAWN, "running"}
            or not attempt.get("facade_managed")
        ):
            raise ValueError("dispatch briefing reads require an active, non-invalidated public worker attempt")
        if attempt.get("profile") != profile:
            raise ValueError("profile does not match the exact dispatched worker")
        if attempt.get("dispatch_ref") != dispatch_ref:
            raise ValueError("dispatch_ref does not match the exact dispatched worker")
        if str(attempt.get("briefing_digest") or "").lower() != briefing_digest:
            raise ValueError("briefing_digest does not match the exact dispatched worker")
        relative = str(attempt.get("briefing_file") or "").strip()
        relative_path = Path(relative)
        if not relative or relative_path.is_absolute() or any(
            part in {"", ".", ".."} for part in relative_path.parts
        ):
            raise ValueError("dispatch briefing path is outside its task scope")
        briefing_path = _contained_path(task_dir, task_dir / relative_path, "dispatch briefing")
        info = briefing_path.lstat()
        if not stat.S_ISREG(info.st_mode) or briefing_path.is_symlink():
            raise ValueError("dispatch briefing must remain a regular non-symlink file")
        if stat.S_IMODE(info.st_mode) & 0o222:
            raise ValueError("dispatch briefing lost immutable read-only permissions")
        briefing = _read_private_text(briefing_path, "dispatch briefing", max_bytes=MAX_BRIEFING_BYTES)
        actual_digest = hashlib.sha256(briefing.encode("utf-8")).hexdigest()
        if actual_digest != briefing_digest:
            raise ValueError("immutable dispatch briefing digest changed after dispatch")
        root = _runtime._task_document_root(task_dir, state["task_id"])
        artifact_ref = str(attempt.get("briefing_artifact_ref") or "")
        artifact = (
            _runtime.db_get_artifact_metadata(root, state["task_id"], artifact_ref)
            if artifact_ref else None
        )
        if artifact is None:
            artifact = _runtime.db_get_artifact_for_export_path(root, state["task_id"], relative)
        if artifact is None or artifact.get("kind") != "dispatch_briefing" or artifact.get("digest_sha256") != briefing_digest:
            raise ValueError("dispatch briefing has no matching immutable artifact catalog entry")
        canonical = _runtime.db_read_artifact_content(root, state["task_id"], artifact["artifact_ref"])
        if canonical != briefing:
            raise ValueError("dispatch briefing export differs from its immutable artifact")
        audience = f"worker:{attempt_id}:{profile}"
        byte_offset = 0
        raw_cursor = params.get("cursor")
        if raw_cursor:
            decoded = _runtime.db_decode_artifact_cursor(root, str(raw_cursor))
            expected = {
                "type": "briefing_read", "task_id": state["task_id"],
                "artifact_ref": artifact["artifact_ref"], "digest_sha256": briefing_digest, "audience": audience,
            }
            if any(decoded.get(key) != value for key, value in expected.items()):
                raise ValueError("briefing cursor is not valid for this dispatch, worker identity, or content version")
            byte_offset = decoded.get("byte_offset")
            if isinstance(byte_offset, bool) or not isinstance(byte_offset, int) or byte_offset < 0:
                raise ValueError("briefing cursor byte offset is invalid")
        effective_max, requested_max, max_bytes_normalized = _bounded_artifact_max_bytes(
            params.get("max_bytes"), label="briefing",
        )
        chunked = bool(raw_cursor) or requested_max is not None or artifact["byte_size"] > _runtime.ARTIFACT_TRANSPORT_MAX_BYTES
        base = {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "briefing_read",
            "task_id": task_id,
            "attempt_id": attempt_id,
            "profile": profile,
            "dispatch_ref": dispatch_ref,
            "briefing_digest": briefing_digest,
            "briefing_artifact": artifact,
            "review_marker": dispatch_briefing_review_marker(briefing_digest),
        }
        if chunked:
            part = _runtime.db_read_artifact_range(
                root, state["task_id"], artifact["artifact_ref"], byte_offset=byte_offset,
                max_bytes=effective_max,
            )
            result = {
                **base,
                "content_part": part.get("content_part"), "encoding": part["encoding"],
                "byte_offset": part["byte_offset"], "returned_bytes": part["returned_bytes"], "complete": part["complete"],
                "effective_max_bytes": effective_max,
                "max_bytes_normalized": max_bytes_normalized,
            }
            if requested_max is not None:
                result["requested_max_bytes"] = requested_max
            if part.get("content_base64") is not None:
                result["content_base64"] = part["content_base64"]
            if part["next_byte_offset"] is not None:
                result["next_cursor"] = _runtime.db_encode_artifact_cursor(root, {
                    "type": "briefing_read", "task_id": state["task_id"],
                    "artifact_ref": artifact["artifact_ref"], "digest_sha256": briefing_digest,
                    "byte_offset": part["next_byte_offset"], "audience": audience,
                })
            result["next_action"] = (
                "If complete is false, call read_dispatch_briefing again with the same exact identity/digest tuple and next_cursor. "
                "Do not substitute another briefing or read another Cortex path."
            )
            return result
        return {
            **base,
            "briefing": briefing,
            "next_action": (
                "Follow this complete validated briefing. Do not read another Cortex ledger path or briefing, and "
                "include review_marker exactly once as its own report.evidence item after actually reviewing it."
            ),
        }
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return _dispatch_briefing_failure(exc)


def ensure_report_markdown_path(
    task_dir: Path,
    state: dict[str, Any],
    report_ref: str,
) -> Path:
    """Return a verified Markdown path, materializing it only on demand.

    This is deliberately an outer-response operation: callers must invoke it
    only after any state-lock or business transaction has committed.  The
    immutable SQLite object, not a sibling JSON record, supplies a missing or
    stale projection.  Projection failures are persisted in the outbox and
    raised to the caller; they never invalidate the canonical report.
    """
    report_id = safe_id(str(report_ref or ""))
    if not report_id:
        raise ValueError("report_ref is required")
    root = _runtime._task_document_root(task_dir, state["task_id"])
    relative = f"reports/markdown/{report_id}.md"
    artifact = _runtime.db_get_artifact_for_export_path(root, state["task_id"], relative)
    if artifact is None or artifact.get("kind") != "report_markdown":
        raise ValueError("worker report Markdown artifact is unavailable")
    job = enqueue_projection(
        root=root, task_id=state["task_id"], artifact_id=artifact["artifact_ref"],
        projection_type="report_markdown", export_path=relative,
    )
    try:
        worker_id = f"report-link-{report_id}"
        # A ready acknowledgement may describe a projection removed by local
        # cleanup.  ``repair`` verifies it first and creates a distinct
        # durable repair attempt only when the export is absent or stale.
        materialized = (
            repair_projection_job(root, job, worker_id=worker_id)
            if job.get("status") == "ready"
            else materialize_projection_job(root, job, worker_id=worker_id)
        )
        if (
            materialized.get("status") != "ready"
            or str(materialized.get("materialized_digest") or "") != str(artifact["digest_sha256"])
        ):
            raise ValueError("worker report Markdown projection is not ready")
        verification = verify_projection_job(root, materialized)
        if not verification.valid:
            raise ValueError("worker report Markdown projection digest is invalid")
    except Exception as exc:
        # Keep the canonical report readable and leave an auditable failed
        # outbox item for reconciliation.  This is an on-demand convenience
        # export, never a report-publication precondition.
        try:
            fail_projection_job(root, str(job["projection_key"]), str(exc))
        except (KeyError, ValueError):
            pass
        raise
    return report_markdown_path(task_dir, report_id)


def _worker_report_error_path(message: str) -> str:
    lowered = message.lower()
    for marker, path in (
        ("max_bytes", "max_bytes"), ("cursor", "cursor"),
        ("report_ref", "report_ref"), ("attempt_id", "attempt_id"),
        ("profile", "profile"), ("task", "task_ref"),
        ("project_root", "project_root"),
    ):
        if marker in lowered:
            return path
    return "$"


def _claim_coordinator_report_publication(
    root: Path,
    state: dict[str, Any],
    record: dict[str, Any],
    report_ref: str,
    *,
    complete: bool,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Issue one completion publication only after the native worker stopped.

    Report reads are intentionally repeatable. Human-visible completion
    publication is not: it is an at-most-once event bound to the durable
    SubagentStop receipt, and worker-scoped predecessor reads never claim it.
    """
    if not complete:
        return False, "report_read_incomplete", None
    attempt_id = safe_id(str(record.get("attempt_id") or ""))
    attempt = next(
        (
            item for item in state.get("attempts", [])
            if isinstance(item, dict) and str(item.get("attempt_id") or "") == attempt_id
        ),
        None,
    )
    if not isinstance(attempt, dict):
        return False, "producer_attempt_unavailable", None
    if attempt.get("invalidated"):
        return False, "producer_attempt_invalidated", None
    stopped_reports = {safe_id(str(item)) for item in attempt.get("host_report_refs") or []}
    if (
        attempt.get("host_stop_outcome") != "report_recorded"
        or not attempt.get("host_stopped_at")
        or report_ref not in stopped_reports
    ):
        return False, "native_worker_not_completed", None

    document_key = "report_publication:" + report_ref
    with state_lock(root):
        existing = db_get_task_document(root, str(state["task_id"]), document_key)
        if isinstance(existing, dict):
            return False, "already_issued", existing
        publication = {
            "schema": "cortex/report-publication/v1",
            "status": "issued",
            "task_id": str(state["task_id"]),
            "report_ref": report_ref,
            "attempt_id": attempt_id,
            "phase": str(record.get("gate") or "report"),
            "host_stopped_at": attempt.get("host_stopped_at"),
            "issued_at": now(),
        }
        db_put_task_document(root, str(state["task_id"]), document_key, publication)
    return True, "native_worker_completed", publication


def _report_completion_update(
    state: dict[str, Any],
    record: dict[str, Any],
    report_ref: str,
) -> dict[str, Any]:
    report = record.get("report") if isinstance(record.get("report"), dict) else {}
    phase = str(record.get("gate") or "report")
    completed_or_skipped = {
        str(item) for item in [*state.get("completed_gates", []), *state.get("skipped_gates", [])]
    }
    remaining = [
        str(item) for item in state.get("current_pipeline", [])
        if str(item) != phase and str(item) not in completed_or_skipped
    ]
    next_step = (
        f"Evaluate this completed result, then continue the Cortex pipeline. "
        f"Next pending pipeline phase: {remaining[0]}."
        if remaining else
        "Evaluate this completed result, then ask Cortex to finalize the pipeline; no later phase remains pending."
    )
    return {
        "schema": "cortex/report-completion-update/v1",
        "report_ref": report_ref,
        "phase": phase,
        "worker": redact(str((record.get("producer") or {}).get("profile") or "worker"), 100),
        "summary": redact(str(report.get("summary") or "Worker completed the delegated phase."), 500),
        "remaining_phases": remaining,
        "next": next_step,
    }


def read_worker_report(params: dict[str, Any]) -> dict[str, Any]:
    """Read one active-task report by compact ref for a coordinator or successor worker."""
    try:
        resolved = _v3_resolve_task(params, require_task_ref=True)
        if isinstance(resolved, dict):
            return resolved
        task_dir, state, _, task_ref = resolved
        report_ref = safe_id(str(params.get("report_ref") or ""))
        if not report_ref:
            raise ValueError("report_ref is required")
        raw_attempt_id = str(params.get("attempt_id") or "").strip()
        raw_profile = str(params.get("profile") or "").strip()
        if bool(raw_attempt_id) != bool(raw_profile):
            raise ValueError("successor worker report reads require both attempt_id and profile")
        worker_context = bool(raw_attempt_id)
        if worker_context:
            attempt = _attempt(state, safe_id(raw_attempt_id))
            profile = canonical_profile(raw_profile)
            if attempt.get("invalidated") or attempt.get("status") not in {"running", AWAITING_HOST_SPAWN}:
                raise ValueError("successor worker report reads require an active, non-invalidated attempt")
            if attempt.get("profile") != profile:
                raise ValueError("successor worker profile does not match the delegated attempt")
            allowed_report_refs = {safe_id(str(item)) for item in attempt.get("context_report_ids") or []}
            if report_ref not in allowed_report_refs:
                raise ValueError("successor worker may read only predecessor report refs supplied in its dispatch")
        root = _runtime._task_document_root(task_dir, state["task_id"])
        # The export file is deliberately not consulted: report reads must
        # stay valid after a clean-up/rebuild and must not turn a Markdown/JSON
        # projection into a second source of truth.
        artifact = _runtime.db_get_artifact_for_export_path(
            root, state["task_id"], f"reports/records/{report_ref}.json",
        )
        if artifact is None or artifact.get("kind") not in {"worker_report", "report_record"}:
            raise ValueError("report_ref has no immutable artifact catalog entry")
        artifact_content = _runtime.db_read_artifact_content(root, state["task_id"], artifact["artifact_ref"])
        if not isinstance(artifact_content, str):
            raise ValueError("report artifact content is not UTF-8 JSON")
        record = json.loads(artifact_content)
        if not isinstance(record, dict) or record.get("task_id") != state.get("task_id") or record.get("report_id") != report_ref:
            raise ValueError("report artifact does not belong to the selected Cortex task or report ref")
        phase = record.get("gate") or "report"
        audience = (
            f"worker:{safe_id(raw_attempt_id)}:{canonical_profile(raw_profile)}"
            if worker_context else "coordinator"
        )
        byte_offset = 0
        raw_cursor = params.get("cursor")
        if raw_cursor:
            decoded = _runtime.db_decode_artifact_cursor(root, str(raw_cursor))
            expected = {
                "type": "report_read", "task_id": state["task_id"],
                "artifact_ref": artifact["artifact_ref"], "digest_sha256": artifact["digest_sha256"],
                "audience": audience,
            }
            if any(decoded.get(key) != value for key, value in expected.items()):
                raise ValueError("report cursor is not valid for this report, task, reader scope, or content version")
            byte_offset = decoded.get("byte_offset")
            if isinstance(byte_offset, bool) or not isinstance(byte_offset, int) or byte_offset < 0:
                raise ValueError("report cursor byte offset is invalid")
        effective_max, requested_max, max_bytes_normalized = _bounded_artifact_max_bytes(
            params.get("max_bytes"), label="report",
        )
        chunked = bool(raw_cursor) or requested_max is not None or artifact["byte_size"] > _runtime.ARTIFACT_TRANSPORT_MAX_BYTES
        if chunked:
            part = _runtime.db_read_artifact_range(
                root, state["task_id"], artifact["artifact_ref"], byte_offset=byte_offset,
                max_bytes=effective_max,
            )
            result = {
                "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "task_ref": task_ref,
                "report_ref": report_ref,
                "phase": phase,
                "profile": (record.get("producer") or {}).get("profile"),
                "report_artifact": artifact,
                "content_part": part.get("content_part"),
                "encoding": part["encoding"],
                "byte_offset": part["byte_offset"],
                "returned_bytes": part["returned_bytes"],
                "complete": part["complete"],
                "effective_max_bytes": effective_max,
                "max_bytes_normalized": max_bytes_normalized,
            }
            if requested_max is not None:
                result["requested_max_bytes"] = requested_max
            if part.get("content_base64") is not None:
                result["content_base64"] = part["content_base64"]
            if part["next_byte_offset"] is not None:
                result["next_cursor"] = _runtime.db_encode_artifact_cursor(root, {
                    "type": "report_read", "task_id": state["task_id"],
                    "artifact_ref": artifact["artifact_ref"], "digest_sha256": artifact["digest_sha256"],
                    "byte_offset": part["next_byte_offset"], "audience": audience,
                })
        else:
            report_payload = record.get("report")
            if not isinstance(report_payload, dict):
                raise ValueError("report artifact content is invalid")
            result = {
                "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "task_ref": task_ref,
                "report_ref": report_ref,
                "phase": phase,
                "profile": (record.get("producer") or {}).get("profile"),
                "report": report_payload,
                "resolved_user_decisions": (
                    record.get("resolved_user_decisions")
                    if isinstance(record.get("resolved_user_decisions"), list) else []
                ),
                **({"scoping": record["scoping"]} if isinstance(record.get("scoping"), dict) else {}),
                **({"planning": record["planning"]} if isinstance(record.get("planning"), dict) else {}),
                "result_validation": record.get("result_validation"),
                "report_artifact": artifact,
                "complete": True,
            }
        if worker_context:
            result["next_action"] = (
                "Use this supplied predecessor report as evidence context, but treat resolved_user_decisions as "
                "durable user authority. Never ask a materially equivalent question again unless the user's current "
                "message explicitly reopens that decision. Verify consequential repository claims in the current "
                "project, and include the exact generated Predecessor review acknowledgement in report.evidence."
            )
        else:
            publication_required = False
            publication_reason = "report_read_incomplete"
            publication = None
            if result.get("complete"):
                # Materialize before claiming the at-most-once event so an
                # export failure cannot permanently consume publication.
                markdown_path = ensure_report_markdown_path(task_dir, state, report_ref)
                publication_required, publication_reason, publication = _claim_coordinator_report_publication(
                    root, state, record, report_ref, complete=True,
                )
            result["publication_required"] = publication_required
            result["publication_reason"] = publication_reason
            if publication_required:
                completion_update = _report_completion_update(state, record, report_ref)
                result.update({
                    "report_markdown_path": str(markdown_path),
                    "report_markdown_link": report_markdown_link(task_dir, report_ref, phase),
                    "completion_update": completion_update,
                    "publication": publication,
                    "next_action": (
                        "Publish report_markdown_link exactly once in the same main-chat message as a concise "
                        "user-language summary of completion_update.summary and what happens next from "
                        "completion_update.next. Never publish a bare link. Then evaluate the report before the "
                        "next Cortex lifecycle call."
                    ),
                })
            else:
                result["next_action"] = (
                    "Use the report content for evaluation only. Do not publish a report link or repeat a prior "
                    "completion update; publication is allowed only on the first complete coordinator read after "
                    "the native worker's durable completion."
                )
        return result
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        message = redact(str(exc), 1000)
        lowered = message.lower()
        terminal = isinstance(exc, (OSError, json.JSONDecodeError)) or any(fragment in lowered for fragment in (
            "active, non-invalidated attempt",
            "artifact content is not utf-8 json",
            "artifact does not belong",
            "artifact content is invalid",
            "markdown projection is not ready",
            "markdown projection digest is invalid",
        ))
        code = "report_unavailable" if terminal else "report_read_request_invalid"
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": "blocked" if terminal else "needs_correction",
            "code": code,
            "diagnostics": [{
                "code": code,
                "path": _worker_report_error_path(message),
                "message": message,
                "fix": (
                    "The selected persisted artifact cannot be read safely; do not substitute or guess another report."
                    if terminal else
                    "Correct only this field using the active task, predecessor grant, or last next_cursor, then "
                    "retry read_worker_report on this same worker attempt."
                ),
            }],
            "retryable": not terminal,
            "attempt_budget_consumed": False,
            "next_action": (
                "Stop because this response is explicitly non-retryable."
                if terminal else
                "Correct the diagnostic field and retry read_worker_report on this same attempt; rejected caller "
                "validation does not consume an attempt and must not end the worker."
            ),
        }


def get_delegation_reports(params: dict[str, Any]) -> dict[str, Any]:
    _, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    attempt_id = safe_id(str(params.get("attempt_id", "")))
    _attempt(state, attempt_id)
    paths = report_bus_paths(task_dir)
    _, delegation_index = _delegation_report_index(paths, state["task_id"], attempt_id)
    allowed = set(delegation_index.get("owned_report_ids", [])) | set(delegation_index.get("context_report_ids", []))
    requested = [safe_id(str(item)) for item in params.get("report_ids", [])]
    if not requested:
        raise ValueError("get_delegation_reports requires explicit report_ids")
    denied = sorted(set(requested) - allowed)
    if denied:
        raise ValueError("delegation is not granted the requested report bodies: " + ", ".join(denied))
    reports = []
    for report_id in requested:
        record, _ = _runtime.read_immutable_json_artifact(
            task_dir,
            state["task_id"],
            f"reports/records/{report_id}.json",
            kinds={"worker_report", "report_record"},
        )
        if record.get("task_id") != state["task_id"]:
            raise ValueError("report record crosses task scope")
        # A coordinator completing the producer attempt needs the one-use
        # receipt, but a downstream context grant must not transfer that
        # capability.  Return it only when this attempt owns the report and
        # validate the durable binding before exposing it.
        if record.get("attempt_id") == attempt_id:
            receipt_id = f"report-receipt-{report_id}"
            receipt, _ = _runtime.read_immutable_json_artifact(
                task_dir,
                state["task_id"],
                f"reports/receipts/{receipt_id}.json",
                kinds={"report_receipt"},
            )
            if (
                receipt.get("schema") != REPORT_SCHEMA
                or receipt.get("task_id") != state["task_id"]
                or receipt.get("report_id") != report_id
                or receipt.get("attempt_id") != attempt_id
                or receipt.get("gate") != record.get("gate")
            ):
                raise ValueError("report receipt does not match its owned report")
            record = {**record, "receipt": receipt}
        reports.append(record)
    return {"schema": REPORT_SCHEMA, "task_id": state["task_id"], "attempt_id": attempt_id, "reports": reports}
