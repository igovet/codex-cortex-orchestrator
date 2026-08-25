"""Fresh-only, bounded context-compaction handoffs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cortex_runtime.core.runtime_bindings import bind_symbols
from cortex_runtime.handoff_compiler import build_dispatch_handoff


bind_symbols(
    "context_handoff",
    globals(),
    (
        "AWAITING_HOST_SPAWN",
        "_delegation_package",
        "_open_blocking_questions",
        "_orchestrate_pipeline_snapshot",
        "_orchestrate_summary",
        "_v11_task_ref",
        "active_gates",
        "db_list_worker_sessions",
        "now",
        "redact",
    ),
)


def _target_handoff(task_dir: Path, state: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any] | None:
    """Rebuild one role-targeted semantic handoff from its dispatch package."""
    try:
        package = _delegation_package(task_dir, state["task_id"], str(attempt.get("attempt_id") or ""))
        return build_dispatch_handoff(package, str(attempt.get("profile") or ""))
    except (OSError, ValueError):
        return None


def _context_handoff(
    task_dir: Path,
    state: dict[str, Any],
    task: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Build a read-only recovery snapshot from attempts and canonical results.

    No predecessor artifact or generic worker body is consulted. A
    successor receives only the target-specific handoff already granted by
    the dispatch package.
    """
    completed_results: list[dict[str, Any]] = []
    pending_dispatches: list[dict[str, Any]] = []
    active_workers: list[dict[str, Any]] = []
    stopped_workers: list[dict[str, Any]] = []
    open_questions: list[dict[str, Any]] = []
    # The state projection is normally sufficient. The SQLite worker session
    # table is a task-scoped lifecycle-telemetry fallback for native
    # spawn/wait/stop observations; it never grants worker authority.
    session_by_attempt: dict[str, dict[str, Any]] = {}
    try:
        ledger_root = task_dir.parent.parent
        for session in db_list_worker_sessions(ledger_root, str(state.get("task_id") or "")):
            attempt_key = str(session.get("attempt_id") or "").strip()
            if attempt_key and attempt_key not in session_by_attempt:
                session_by_attempt[attempt_key] = session
    except (OSError, ValueError, TypeError):
        session_by_attempt = {}
    for attempt in state.get("attempts") or []:
        if not isinstance(attempt, dict) or attempt.get("invalidated"):
            continue
        attempt_id = redact(attempt.get("attempt_id", ""), 128)
        phase = redact(attempt.get("gate", ""), 128)
        profile = redact(attempt.get("profile", ""), 128)
        result_ref = redact(attempt.get("attempt_result_ref", ""), 160) or None
        status = str(attempt.get("status") or "")
        try:
            attempt_questions = _open_blocking_questions(task_dir, state, str(attempt.get("attempt_id") or ""))
        except (OSError, ValueError, TypeError):
            attempt_questions = []
        question_refs = [
            redact(item.get("question_id"), 160)
            for item in attempt_questions
            if isinstance(item, dict) and str(item.get("question_id") or "").strip()
        ]
        for question in attempt_questions:
            if isinstance(question, dict):
                open_questions.append({
                    "attempt_id": attempt_id,
                    "phase": phase,
                    "dispatch_ref": redact(attempt.get("dispatch_ref", ""), 160) or None,
                    "question_ref": redact(question.get("question_id"), 160),
                    "question": redact(question.get("question", ""), 2000),
                    "header": redact(question.get("header", ""), 300) or None,
                })
        identity = {
            "attempt_id": attempt_id,
            "phase": phase,
            "profile": profile,
            "dispatch_ref": redact(attempt.get("dispatch_ref", ""), 160) or None,
        }
        if result_ref:
            completed_results.append({
                **identity,
                "attempt_result_ref": result_ref,
                "lifecycle_status": redact(attempt.get("lifecycle_status", ""), 128) or None,
            })
        if status == AWAITING_HOST_SPAWN:
            pending_dispatches.append({**identity, "target_handoff": _target_handoff(task_dir, state, attempt)})
        elif (
            status == "running"
            and not attempt.get("host_stopped_at")
            and not attempt.get("worker_session_reconciled_at")
        ):
            host_spawn = attempt.get("host_spawn") or {}
            session = session_by_attempt.get(str(attempt.get("attempt_id") or ""), {})
            active_workers.append({
                **identity,
                # Host identity is server-observed at SubagentStart and is
                # durably stored in the immutable spawn observation, never in
                # worker-authored attempt data.  Recovery must surface that
                # exact native child id so a compacted coordinator can wait on
                # the existing worker instead of dispatching a replacement.
                "host_agent_id": redact(
                    attempt.get("host_agent_id")
                    or host_spawn.get("agent_id")
                    or session.get("host_agent_id")
                    or "",
                    160,
                ) or None,
            })
        elif attempt.get("host_stopped_at") or attempt.get("worker_session_reconciled_at"):
            finalization_pending = str(attempt.get("host_stop_outcome") or "") == "work_completed_finalization_pending"
            awaiting_user = str(attempt.get("host_stop_outcome") or "") == "awaiting_user" or bool(question_refs)
            stopped_workers.append({
                **identity,
                "attempt_result_ref": result_ref,
                "finalization_pending": finalization_pending,
                "awaiting_user": awaiting_user,
                "question_refs": question_refs,
                # A result/session reconciliation is terminal worker state,
                # not a synthetic failure or a claim that a host stop hook
                # was observed. It must therefore be recoverable only through
                # the canonical coordinator continuation, never as a live
                # native worker.
                "failure_status": None if finalization_pending or awaiting_user or attempt.get("worker_session_reconciled_at") else redact(attempt.get("host_stop_outcome", ""), 160) or None,
                "failure_reason": None if finalization_pending or awaiting_user or attempt.get("worker_session_reconciled_at") else redact(attempt.get("finalization_reason", ""), 1000) or None,
                "resumable": awaiting_user,
            })
    active = active_gates(state)
    finalizing = [item for item in stopped_workers if item["finalization_pending"]]
    if finalizing:
        next_action = "retry complete_attempt on that exact persisted attempt only; do not spawn or submit a replacement worker."
    elif pending_dispatches:
        next_action = "Invoke only the returned pending dispatches, then follow the current canonical step."
    else:
        next_action = "Follow the current canonical lifecycle state; use only attempt_result_ref values for completed predecessors."
    return {
        "schema": "cortex/context-handoff/v11",
        # ``task_ref`` is the opaque public identity derived from the exact
        # task id.  Passing the whole state projection here hashes its string
        # representation and manufactures a different reference after every
        # state change, which can make a compacted coordinator recover the
        # wrong task identity.
        "task_ref": _v11_task_ref(str(state.get("task_id") or "")),
        "task_id": redact(state.get("task_id", ""), 128),
        "generated_at": now(),
        "goal": redact(task.get("user_request_projection") or task.get("user_request", ""), 4000),
        "acceptance_criteria": [redact(item, 1000) for item in (task.get("acceptance_criteria") or [])],
        "verification": [redact(item, 1000) for item in (task.get("verification") or [])],
        "state": _orchestrate_summary(state),
        "pipeline": _orchestrate_pipeline_snapshot(state, plan),
        "active_gates": active,
        "completed_results": completed_results,
        "pending_dispatches": pending_dispatches,
        "active_workers": active_workers,
        "stopped_workers": stopped_workers,
        "open_questions": open_questions,
        "protocol": {
            "coordinator": "The main/root agent is the sole user-facing coordinator; project operations belong to workers.",
            "dispatch_transport": "Each pending dispatch uses one compact bootstrap plus an immutable scoped briefing path and SHA-256; the coordinator does not read the briefing.",
            "result_transport": "Read canonical AttemptResult views with read_worker_result by exact attempt_result_ref. Generated result views are non-authoritative.",
        },
        "next_action": next_action,
    }
