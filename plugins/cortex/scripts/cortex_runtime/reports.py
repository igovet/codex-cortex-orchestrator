"""Scoped report and immutable-briefing operations.

The public facade supplies durable ledger helpers while this module owns
the worker-facing report transport and keeps the public v4 surface narrow.
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
    MAX_BRIEFING_BYTES,
    MAX_REPORTS_PER_ATTEMPT,
    MAX_REPORTS_PER_TASK,
    MAX_REPORT_AGGREGATE_BYTES,
    PUBLIC_ORCHESTRATION_SCHEMA,
    REPORT_SCHEMA,
    _attempt,
    _attempt_identity_aliases,
    _contained_path,
    _delegation_report_index,
    _open_blocking_questions,
    _read_private_text,
    _recover_report_receipt,
    _report_index,
    _write_delegation_report_index,
    _write_report_index,
    _report_markdown,
    _report_metadata,
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
from cortex_runtime.ledger_db import fail_projection_job
from cortex_runtime.record_report import build_compatibility_facade

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
        open_questions = _open_blocking_questions(task_dir, state, attempt_id)
        if open_questions:
            refs = ", ".join(str(item["question_id"]) for item in open_questions)
            raise ValueError(
                f"cannot publish a report while blocking worker question(s) remain unanswered: {refs}; "
                "resume this same worker after the coordinator records the user answer"
            )
        report = sanitize_report_payload(params.get("report"))
        is_closure_gate = attempt.get("gate") in {"review", "close"}
        actor_ids = {str(params.get("principal") or "").strip(), str(params.get("profile") or "").strip()}
        actor_ids.update(str(alias).strip() for alias in _attempt_identity_aliases(attempt))
        closure = sanitize_closure_payload(params["closure"], actor_ids={item for item in actor_ids if item}) if params.get("closure") is not None else None
        gate_result = _runtime.sanitize_gate_result_payload(
            params["gate_result"], actor_ids={item for item in actor_ids if item}
        ) if params.get("gate_result") is not None else None
        if closure is not None and attempt.get("gate") not in {"review", "close"}:
            raise ValueError("closure is only valid for review and close attempts")
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
            raise ValueError("review and close reports require a top-level closure sibling or canonical gate_result")
        if gate_result is None and closure is not None:
            gate_result = {**closure, "failure_class": "product"}
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
            planning = sanitize_planning_payload(raw_planning)
        elif params.get("_require_plan_artifact") and attempt.get("gate") == "plan":
            raise ValueError("planner reports require a planning artifact with overview and work_packages")
        raw_scoping = params.get("scoping")
        scoping = None
        if raw_scoping is not None:
            if attempt.get("gate") != "scope" or attempt.get("profile") != "planner":
                raise ValueError("scoping artifacts may be published only by the active planner scope attempt")
            scoping = sanitize_scoping_payload(raw_scoping)
        elif params.get("_require_scope_artifact") and attempt.get("gate") == "scope":
            raise ValueError(
                "planner scope reports require a scoping artifact with overview, context_files, and discovery_domains"
            )
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
            return {"idempotent": True, "report": existing, "receipt": receipt, "host_confirmation_pending": host_confirmation_pending, "principal_correction": principal_correction, "state": state}
        attempt_count = sum(1 for item in authoritative if item.get("attempt_id") == attempt_id)
        aggregate_bytes = sum(len(json.dumps(item.get("report", {}), ensure_ascii=False, sort_keys=True).encode("utf-8")) for item in authoritative)
        report_bytes = len(json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if attempt_count >= _runtime.MAX_REPORTS_PER_ATTEMPT or len(authoritative) >= _runtime.MAX_REPORTS_PER_TASK:
            raise ValueError("report count quota exhausted")
        if aggregate_bytes + report_bytes > _runtime.MAX_REPORT_AGGREGATE_BYTES:
            raise ValueError("report aggregate byte quota exhausted")
        report_id = f"report-{max(occupied_numbers, default=0) + 1:04d}"
        record = {
            "schema": REPORT_SCHEMA, "report_id": report_id, "task_id": state["task_id"],
            "gate": attempt["gate"], "attempt_id": attempt_id, "submission_id": submission_id,
            "producer": {"profile": attempt["profile"], "model": attempt["selected_model"], "reasoning_effort": attempt["selected_reasoning_effort"]},
            "report": report, "planning": planning,
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
        return {"idempotent": False, "report": record, "receipt": receipt, "host_confirmation_pending": host_confirmation_pending, "principal_correction": principal_correction, "state": state}


def _restore_report_projections(result: dict[str, Any], params: dict[str, Any]) -> None:
    """Leave optional report exports in the durable outbox after commit.

    Recording a report must succeed even if a workstation cannot currently
    create a Markdown or JSON projection.  The canonical artifacts and their
    projection intents are committed by ``_record_report_locked``; explicit
    report reads or reconciliation can materialize them later.
    """
    del result, params


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


def publish_worker_report(params: dict[str, Any]) -> dict[str, Any]:
    """Public worker adapter: persist a report and return only a compact receipt."""
    try:
        unknown = sorted(set(params) - {"project_root", "task_id", "attempt_id", "profile", "report", "scoping", "planning", "gate_result", "closure"})
        if unknown:
            raise ValueError("unsupported record_report fields: " + ", ".join(unknown))
        for field in ("project_root", "task_id", "attempt_id", "profile"):
            if not str(params.get(field) or "").strip():
                raise ValueError(f"{field} is required; copy the exact value from this worker's Cortex briefing")
        profile = canonical_profile(params.get("profile") or "")
        if profile not in AGENTS:
            raise ValueError("profile must be an exact Cortex worker profile")
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
                "options. Return only its question_ref and concise summary; do not record a report until the user "
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
                "report.evidence as one string item, then retry record_report once on this same attempt."
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
                "only the main coordinator may localize content for the user, then retry record_report once."
            )
        elif "changed_files" in message:
            code = "report_changed_files_invalid"
            outcome = "needs_correction"
            next_action = (
                "Keep only safe project-relative file paths in report.changed_files, move explanatory prose to "
                "findings or evidence, then retry record_report once on this same attempt."
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
            "report uncertainty must", "report exceeds the", "report count quota exhausted",
            "report aggregate byte quota exhausted", "idempotent report submission_id",
            "project_root is required", "project_root must be an absolute path", "CORTEX_ROOT is not supported",
            "scoping ", "planner scope reports require", "planning ", "planner reports require", "C2/C3 close report",
            "result requires", "result evidence", "result contains unresolved", "result test", "read-only result gate",
            "project files changed during read-only",
            "review and close reports require a top-level closure sibling",
        )):
            code = "report_validation_failed"
            outcome = "needs_correction"
            next_action = (
                "Correct only the report fields named by the diagnostic and retry record_report once on this same "
                "task and attempt. Do not guess identity, remove required evidence, or paste the report into the "
                "parent channel."
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
        "idempotent": bool(result.get("idempotent")),
        "next_action": "Return only REPORT_RECORDED, report_ref, and at most a two-sentence summary to the parent coordinator.",
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
        requested_max = params.get("max_bytes")
        if requested_max is not None and (isinstance(requested_max, bool) or not isinstance(requested_max, int)):
            raise ValueError("briefing max_bytes must be an integer")
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
                max_bytes=requested_max or 16 * 1024,
            )
            result = {
                **base,
                "content_part": part.get("content_part"), "encoding": part["encoding"],
                "byte_offset": part["byte_offset"], "returned_bytes": part["returned_bytes"], "complete": part["complete"],
            }
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
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": "blocked",
            "code": "dispatch_briefing_unavailable",
            "diagnostics": [{
                "code": "dispatch_briefing_unavailable",
                "message": redact(str(exc), 1000),
            }],
            "next_action": (
                "Stop before project work and return this exact diagnostic to the parent coordinator. Never list "
                "the Cortex ledger, substitute another briefing, or guess task identity."
            ),
        }


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


def read_worker_report(params: dict[str, Any]) -> dict[str, Any]:
    """Read one active-task report by compact ref for a coordinator or successor worker."""
    try:
        resolved = _v3_resolve_task(params)
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
        requested_max = params.get("max_bytes")
        if requested_max is not None and (isinstance(requested_max, bool) or not isinstance(requested_max, int)):
            raise ValueError("report max_bytes must be an integer")
        chunked = bool(raw_cursor) or requested_max is not None or artifact["byte_size"] > _runtime.ARTIFACT_TRANSPORT_MAX_BYTES
        if chunked:
            part = _runtime.db_read_artifact_range(
                root, state["task_id"], artifact["artifact_ref"], byte_offset=byte_offset,
                max_bytes=requested_max or 16 * 1024,
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
            }
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
                **({"scoping": record["scoping"]} if isinstance(record.get("scoping"), dict) else {}),
                **({"planning": record["planning"]} if isinstance(record.get("planning"), dict) else {}),
                "result_validation": record.get("result_validation"),
                "report_artifact": artifact,
                "complete": True,
            }
        if worker_context:
            result["next_action"] = (
                "Use this supplied predecessor report only as evidence context, verify consequential claims in the "
                "current project, and include the exact generated Predecessor review acknowledgement in report.evidence."
            )
        else:
            markdown_path = ensure_report_markdown_path(task_dir, state, report_ref)
            result.update({
                "report_markdown_path": str(markdown_path),
                "report_markdown_link": report_markdown_link(task_dir, report_ref, phase),
                "next_action": (
                    "Publish report_markdown_link verbatim in the main chat before any other Cortex lifecycle call; "
                    "the link is mandatory coordinator output, not optional metadata."
                ),
            })
        return result
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": "needs_correction",
            "code": "report_unavailable",
            "diagnostics": [{"code": "report_unavailable", "message": redact(str(exc), 1000)}],
            "next_action": "Supply the exact project_root and persisted report_ref from the active task; do not guess report or task identifiers.",
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
