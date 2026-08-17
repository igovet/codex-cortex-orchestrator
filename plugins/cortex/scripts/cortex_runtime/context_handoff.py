"""Durable context-compaction handoff rendering."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cortex_runtime.core.runtime_bindings import bind_symbols


bind_symbols(
    "context_handoff",
    globals(),
    (
        "AWAITING_HOST_SPAWN",
        "MAX_CONTEXT_REPORTS",
        "_contained_path",
        "_open_blocking_questions",
        "_orchestrate_pipeline_snapshot",
        "_orchestrate_summary",
        "_plan_approval",
        "_report_index",
        "_v3_task_ref",
        "_wave_for_gates",
        "active_gates",
        "now",
        "redact",
        "report_bus_paths",
        "report_markdown_link",
        "report_markdown_path",
        "safe_id",
        "sanitize_structured",
    ),
)

def _context_handoff(
    task_dir: Path,
    state: dict[str, Any],
    task: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Build a bounded, ledger-backed recovery handoff after context compaction.

    The host may compact or replay the conversation without preserving the
    exact skill version or the coordinator's transient protocol state.  This
    handoff is deliberately derived from the task ledger and report index, so
    the coordinator can rehydrate from durable evidence instead of trusting a
    raw transcript or starting a duplicate task.
    """
    report_index = _report_index(report_bus_paths(task_dir), state["task_id"])
    report_items = [
        item for item in report_index.get("reports", [])
        if isinstance(item, dict)
    ][-MAX_CONTEXT_REPORTS:]
    report_handoffs: list[dict[str, Any]] = []
    changed_files: list[str] = []
    verified_facts: list[dict[str, Any]] = []
    # Context handoff runs outside lifecycle locks.  Recreate the optional
    # Desktop export from the canonical report object before emitting its link.
    from cortex_runtime.reports import ensure_report_markdown_path

    for item in report_items:
        report_ref = safe_id(str(item.get("report_id") or ""))
        phase = redact(item.get("gate", "report"), 128) or "report"
        summary = redact(item.get("summary", ""), 2400)
        raw_files = item.get("changed_files")
        files: list[Any] = raw_files if isinstance(raw_files, list) else []
        compact_files = [redact(value, 500) for value in files[:32]]
        for value in compact_files:
            if value and value not in changed_files:
                changed_files.append(value)
        report_handoff = {
            "report_ref": report_ref,
            "phase": phase,
            "profile": redact((item.get("producer") or {}).get("profile", ""), 128),
            "summary": summary,
            "changed_files": compact_files,
        }
        try:
            markdown_path = ensure_report_markdown_path(task_dir, state, report_ref)
            report_handoff.update({
                "report_markdown_path": str(markdown_path),
                "report_markdown_link": report_markdown_link(task_dir, report_ref, phase),
            })
        except (OSError, ValueError) as exc:
            # A report reference is still readable from its canonical SQLite
            # artifact.  Do not turn a rebuildable Desktop export into a
            # context-recovery failure.
            report_handoff["projection_error"] = redact(str(exc), 500)
        report_handoffs.append(report_handoff)
        verified_facts.append({
            "source": report_ref,
            "phase": phase,
            "fact": summary,
            "changed_files": compact_files,
        })

    decisions: list[dict[str, Any]] = []
    for collection_name in ("pipeline_changes", "adaptive_events"):
        collection = state.get(collection_name)
        if not isinstance(collection, list):
            continue
        for value in collection[-8:]:
            if isinstance(value, dict):
                decisions.append(sanitize_structured({
                    "source": collection_name,
                    "at": value.get("at"),
                    "reason": redact(value.get("reason", ""), 1200),
                    "signals": [redact(item, 800) for item in (value.get("signals") or [])[:8]],
                    "from": value.get("from"),
                    "to": value.get("to") or value.get("pipeline"),
                    "operations": value.get("operations") or [],
                }))
    approval = _plan_approval(state)
    if approval.get("policy") == "required" or approval.get("plan_report_ref"):
        decisions.append({
            "source": "plan_approval",
            "status": redact(approval.get("status", ""), 64),
            "plan_report_ref": redact(approval.get("plan_report_ref", ""), 128) or None,
            "feedback": redact(approval.get("feedback", ""), 1200) or None,
        })

    commands: list[dict[str, Any]] = []
    for evidence in state.get("evidence", [])[-8:]:
        if not isinstance(evidence, dict):
            continue
        command = redact(evidence.get("command", ""), 1000)
        argv = [redact(value, 300) for value in (evidence.get("argv") or [])[:32]]
        if not command and not argv:
            continue
        commands.append({
            "gate": redact(evidence.get("gate", ""), 64),
            "command": command or None,
            "argv": argv,
            "cwd": redact(evidence.get("cwd", ""), 500) or None,
            "exit_code": evidence.get("exit_code"),
            "verified_execution": bool(evidence.get("verified_execution")),
            "stdout": redact(evidence.get("stdout", ""), 1200),
            "stderr": redact(evidence.get("stderr", ""), 1200),
        })

    open_questions = [
        {
            "question_ref": redact(item.get("question_id", ""), 128),
            "attempt_id": redact(item.get("attempt_id", ""), 128),
            "header": redact(item.get("header", ""), 300),
            "question": redact(item.get("question", ""), 1600),
        }
        for item in _open_blocking_questions(task_dir, state)
        if isinstance(item, dict)
    ][:8]
    blockers: list[str] = []
    if state.get("status") == "blocked":
        blockers.append(redact(state.get("blocked_reason", "The task is blocked."), 2000))
    blockers.extend(
        redact(item.get("reason", ""), 1200)
        for item in state.get("recovery_events", [])[-8:]
        if isinstance(item, dict) and item.get("reason")
    )

    pending_dispatches: list[dict[str, Any]] = []
    active_workers: list[dict[str, Any]] = []
    stopped_workers: list[dict[str, Any]] = []
    current_wave = _wave_for_gates(plan, active_gates(state))
    wave_attempt_ids = list((current_wave or {}).get("attempt_ids") or [])
    worker_slots = {str(attempt_id): index for index, attempt_id in enumerate(wave_attempt_ids, 1)}
    for attempt in state.get("attempts", []):
        if not isinstance(attempt, dict) or attempt.get("invalidated"):
            continue
        spawn_request = attempt.get("spawn_request") or {}
        common = {
            "attempt_id": redact(attempt.get("attempt_id", ""), 128),
            "phase": redact(attempt.get("gate", ""), 64),
            "profile": redact(attempt.get("profile", ""), 128),
            "display_name": redact(attempt.get("display_name", ""), 128),
            "dispatch_ref": redact(attempt.get("dispatch_ref", ""), 128),
            "task_name": redact(spawn_request.get("task_name", ""), 128),
            "worker": worker_slots.get(str(attempt.get("attempt_id") or "")),
        }
        if attempt.get("status") == AWAITING_HOST_SPAWN:
            briefing_file = str(attempt.get("briefing_file") or "")
            briefing_path = _contained_path(task_dir, task_dir / briefing_file, "dispatch briefing")
            pending_dispatches.append({
                **common,
                "briefing_path": str(briefing_path),
                "briefing_digest": redact(attempt.get("briefing_digest", ""), 128),
                "recovery_authority": "invoke_only_the_matching_top_level_inspect_dispatch",
            })
        elif attempt.get("host_stopped_at"):
            stopped_workers.append({
                **common,
                "status": redact(attempt.get("status", ""), 64),
                "outcome": redact(attempt.get("host_stop_outcome", ""), 128),
                "report_refs": [
                    redact(item, 128) for item in (attempt.get("host_report_refs") or [])[:8]
                ],
                "question_refs": [
                    redact(item, 128) for item in (attempt.get("host_question_refs") or [])[:8]
                ],
                "reason": redact(attempt.get("finalization_reason", ""), 1000) or None,
                "host_agent_id": redact((attempt.get("host_spawn") or {}).get("agent_id", ""), 256),
                "stopped_at": attempt.get("host_stopped_at"),
            })
        elif attempt.get("status") == "running":
            host_spawn = attempt.get("host_spawn") or {}
            active_workers.append({
                **common,
                "host_agent_id": redact(host_spawn.get("agent_id", ""), 256),
                "host_task_name": redact(host_spawn.get("task_name", ""), 128),
                "host_model": redact(host_spawn.get("model", ""), 128),
                "reasoning_effort": redact(host_spawn.get("reasoning_effort", ""), 64),
                "started_at": host_spawn.get("confirmed_at") or attempt.get("started_at"),
            })

    task_ref = _v3_task_ref(state["task_id"])
    recovery_actions = []
    if pending_dispatches:
        recovery_actions.append(
            "Invoke only the matching top-level inspect dispatches; this handoff is descriptive and never itself "
            "authorizes spawn."
        )
    if active_workers:
        active_ids = [item["host_agent_id"] for item in active_workers if item.get("host_agent_id")]
        recovery_actions.append(
            "Do not respawn active workers; wait only on these exact persisted child ids: "
            + ", ".join(active_ids)
            + "."
        )
    if stopped_workers:
        recovery_actions.append(
            "Never wait on or respawn stopped_workers. Consume their recorded report refs, surface their durable "
            "questions, or submit their exact non-success result to continue_orchestration as indicated."
        )
    next_action = (
        f"Call manage_orchestration(intent=inspect, task_ref={task_ref}) once after context compaction; "
        "treat the returned context_handoff and current ledger as authoritative. Do not call "
        "start_orchestration again, do not replay completed dispatches, and do not use a raw transcript. "
        "After rehydration, follow the returned relative step and publish every exact report_markdown_link "
        "before the next lifecycle or report-read call. "
        + " ".join(recovery_actions)
    )
    return {
        "schema": "cortex/context-handoff/v1",
        "task_ref": task_ref,
        "task_id": redact(state.get("task_id", ""), 128),
        "generated_at": now(),
        "goal": redact(task.get("user_request") or task.get("objective", ""), 4000),
        "acceptance_criteria": [redact(item, 1000) for item in (task.get("acceptance_criteria") or [])[:32]],
        "verified_facts": verified_facts[-MAX_CONTEXT_REPORTS:],
        "decisions": decisions[-16:],
        "changed_files": changed_files[:64],
        "commands": commands,
        "open_questions": open_questions,
        "blockers": [item for item in blockers if item][:16],
        "state": _orchestrate_summary(state),
        "pipeline": _orchestrate_pipeline_snapshot(state, plan),
        "reports": report_handoffs,
        "pending_dispatches": pending_dispatches,
        "active_workers": active_workers,
        "stopped_workers": stopped_workers,
        "protocol": {
            "coordinator": "The main/root agent is the sole user-facing coordinator; project operations belong to workers.",
            "worker_language": "Worker-authored commentary, tool arguments, reports, questions, and native final output are English-only.",
            "hidden_dispatch": "Hidden spawn_agent requests retain fork_turns=none so the coordinator transcript is not inherited.",
            "dispatch_transport": "Each pending dispatch uses one compact bootstrap plus an immutable scoped briefing path and SHA-256; the coordinator does not read the briefing.",
            "dispatch_recovery": "Only top-level dispatches returned by inspect authorize an unstarted spawn; active_workers are waitable exact child ids, while stopped_workers must never be waited on or respawned.",
            "report_publication": "Read each report_ref, then publish the returned report_markdown_link verbatim in the main chat before any other lifecycle call or report read.",
            "instruction_source": "cortex:orchestrator and cortex-control skills; this handoff restores state and invariants, not a replacement skill source.",
        },
        "next_action": next_action,
    }
