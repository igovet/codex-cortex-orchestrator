"""Delegation persistence service behind the stable Cortex facade."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import cortex as _runtime

from cortex_runtime.delegation import (
    delegation_lists,
    dispatch_context,
    select_profile as select_delegation_profile,
    spawn_request as build_spawn_request,
    task_kind_and_risk,
)
from cortex import (
    AGENTS,
    AWAITING_HOST_SPAWN,
    DOCUMENTATION_EVIDENCE_KINDS,
    PROFILES,
    QUESTION_SCHEMA,
    REPORT_SCHEMA,
    SCHEMA,
    _contained_path,
    _delegation_report_index,
    _write_delegation_package,
    _write_delegation_report_index,
    _project_knowledge_context,
    _report_index,
    _v3_task_ref,
    active_gates,
    authorize,
    canonical_profile,
    capture_project_manifest,
    digest_text,
    host_spawn_bootstrap,
    host_spawn_prompt,
    ledger_root,
    load_task_definition,
    load_state,
    native_worker_task_name,
    now,
    primary_gate,
    profiles_for_gate,
    redact,
    render_gate_briefing,
    report_bus_paths,
    safe_id,
    sanitize_structured,
    save_state,
    select_implementation_profile,
    select_project_root,
    state_lock,
    worker_display_name,
    worker_module_label,
    store_manifest_snapshot,
    store_immutable_artifact,
    write_text_immutable,
)


def _next_attempt_id(state: dict[str, Any], task_dir: Path, gate: str) -> str:
    """Allocate a monotonic attempt id even after a briefing-only crash.

    Briefings are intentionally immutable and are written before the mutable
    SQLite attempt row.  If a later step fails, the briefing remains as an
    orphan.  Reusing ``len(state["attempts"]) + 1`` would then address the
    same path with different bytes on recovery.  Include both durable attempts
    and orphan briefing ordinals when selecting the next number.
    """
    highest = len(state.get("attempts", []))
    pattern = re.compile(r"^[a-z0-9_]+-(\d+)\.dispatch-[a-z0-9]+\.briefing\.md$")
    delegations = task_dir / "delegations"
    if delegations.is_dir():
        for path in delegations.iterdir():
            match = pattern.fullmatch(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return f"{gate}-{highest + 1:02d}"

def record_delegation(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        requested_revision = params.get("expected_revision")
        revision_correction = (
            {"requested": requested_revision, "used": state["revision"]}
            if requested_revision is not None and requested_revision != state["revision"] else None
        )
        expected_status_receipt = "status-" + digest_text(json.dumps({
            "task_id": state["task_id"],
            "principal": state.get("principal", "local"),
            "revision": state["revision"],
        }, sort_keys=True))[:24]
        status_receipt = str(params.get("status_receipt") or "").strip()
        observed = (
            {"status_receipt": expected_status_receipt, "revision": state["revision"]}
            if status_receipt == expected_status_receipt else None
        )
        receipt_correction = observed is None
        status_receipt = expected_status_receipt
        wave = active_gates(state)
        gate = str(primary_gate(state) or "")
        requested_gate = str(params.get("gate") or gate)
        if requested_gate in wave:
            gate = requested_gate
        task_definition = load_task_definition(task_dir, state)
        requested_agent = canonical_profile(params.get("agent") or "")
        agent, selection_reason, _ = select_delegation_profile(
            params,
            gate=gate,
            task_definition=task_definition,
            agents=AGENTS,
            canonical_profile=canonical_profile,
            select_implementation_profile=select_implementation_profile,
            profiles_for_gate=profiles_for_gate,
        )
        agent_correction = ({"requested": requested_agent or None, "used": agent} if requested_agent != agent else None)
        if state["status"] != "active":
            raise ValueError(f"cannot delegate while task status is '{state['status']}'")
        if gate == "documentation" and agent != "technical_writer":
            raise ValueError("documentation gate must be delegated to technical_writer")
        retry = int(params.get("retry", 0))
        if retry < 0 or retry > 2:
            raise ValueError("retry must be between 0 and 2")
        if gate == "documentation":
            # A facade documentation wave may intentionally contain several
            # parallel writers.  Their orchestration keys are the identity of
            # each slot; treating any active documentation attempt as a
            # duplicate prevents the third and later writers from ever being
            # materialized.  Direct/non-facade delegation has no slot key and
            # keeps the single-documentation-attempt retry guard.
            delegation_key = str(params.get("orchestration_delegation_key") or "").strip()
            active_documentation = [
                item for item in state["attempts"]
                if item.get("gate") == gate
                and item.get("status") in {AWAITING_HOST_SPAWN, "running", "passed"}
                and not item.get("invalidated")
            ]
            existing = (
                [
                    item for item in active_documentation
                    if item.get("orchestration_delegation_key") == delegation_key
                ]
                if delegation_key
                else active_documentation
            )
            if existing:
                evidence = [
                    item for item in state.get("evidence", [])
                    if item.get("gate") == gate
                    and item.get("attempt_id") in {attempt["attempt_id"] for attempt in existing}
                    and item.get("kind") in DOCUMENTATION_EVIDENCE_KINDS
                    and item.get("decision") in {"updated", "not_applicable"}
                ]
                return {
                    "recorded": False,
                    "reason": "documentation_attempt_already_available",
                    "candidate_attempt_ids": [item["attempt_id"] for item in existing],
                    "next_action": (
                        "confirm_host_spawn"
                        if any(item.get("status") == AWAITING_HOST_SPAWN for item in existing)
                        else "record_gate_outcome" if evidence else "record_evidence"
                    ),
                    "recoverable": True,
                    "state": state,
                }
        prior_failures = sum(
            1 for attempt in state["attempts"]
            if attempt["gate"] == gate and attempt["status"] == "failed" and not attempt.get("invalidated")
        )
        if prior_failures >= 2:
            raise ValueError(f"retry budget exhausted for gate '{gate}'")
        briefing = render_gate_briefing(gate, task_definition.get("objective", ""), agent)
        ownership = str(params.get("ownership", "")).strip() or briefing["ownership"]
        objective = str(params.get("objective", "")).strip() or briefing["objective"]
        requested_task_kind = str(params.get("task_kind") or "").strip()
        requested_risk = str(params.get("risk") or "").strip().lower()
        task_kind, risk = task_kind_and_risk(params, gate)
        dispatch_mode, luna_fallback, route, thread_environment = dispatch_context(
            params,
            gate=gate,
            agent=agent,
            task_kind=task_kind,
            complexity=str(state.get("complexity", "C1")),
            resolve_dispatch_route=_runtime.resolve_dispatch_route,
        )
        required_lists = delegation_lists(params, task_definition, briefing)
        context_report_ids = [safe_id(str(item)) for item in params.get("context_report_ids", [])]
        report_paths = report_bus_paths(task_dir)
        available_reports = {item["report_id"] for item in _report_index(report_paths, state["task_id"]).get("reports", [])}
        if len(context_report_ids) != len(set(context_report_ids)) or not set(context_report_ids).issubset(available_reports):
            raise ValueError("context_report_ids must be unique reports from this task")
        attempt_id = _next_attempt_id(state, task_dir, gate)
        # The role label remains canonical, but the native task key must be
        # unique per task/attempt.  Keeping only ``agent`` here lets the host
        # mistake a fresh dispatch for a continuation of an older child.
        module = worker_module_label(
            task_definition.get("user_request") or task_definition.get("objective") or objective,
            required_lists["allowed_paths"],
            gate,
        )
        display_name = worker_display_name(agent, module)
        task_name = native_worker_task_name(agent, state["task_id"], attempt_id, module)
        visible_thread = dispatch_mode == "visible_thread"
        spawn_request = build_spawn_request(
            dispatch_mode=dispatch_mode,
            gate=gate,
            agent=agent,
            display_name=display_name,
            task_name=task_name,
            profiles=PROFILES,
            selection_reason=selection_reason,
            route=route,
            thread_environment=thread_environment,
        )
        facade_managed = bool(params.get("facade_managed", False))
        question_route = (
            {"mode": "native_parent", "answer_location": "main_chat"}
            if facade_managed else
            {
                "mode": "pull",
                "worker_tool": "cortex.question",
                "publish_tool": "publish_worker_question",
                "updates_tool": "get_worker_question_updates",
                "coordinator_list_tool": "list_worker_questions",
                "coordinator_answer_tool": "answer_worker_question",
                "coordinator_ui_tool": "cortex.question",
                "answer_location": "main_chat",
            }
        )
        orchestration_wave_id = str(params.get("orchestration_wave_id", "")).strip() or None
        orchestration_delegation_key = str(params.get("orchestration_delegation_key", "")).strip() or None
        project_root = select_project_root(params)
        context_files, knowledge_index_files = _project_knowledge_context(project_root, params.get("context_files"))
        result_baseline = capture_project_manifest(project_root)
        result_baseline_ref = store_manifest_snapshot(task_dir, result_baseline)
        dispatch_ref = "dispatch-" + digest_text(
            "\0".join((state["task_id"], attempt_id, agent, task_name))
        )[:24]
        briefing_file = f"delegations/{attempt_id}.{dispatch_ref}.briefing.md"
        briefing_path = _contained_path(task_dir, task_dir / briefing_file, "dispatch briefing")
        package = {"schema": SCHEMA, "task_id": state["task_id"], "task_ref": _v3_task_ref(state["task_id"]), "gate": gate, "attempt_id": attempt_id, "agent": agent, "profile": agent, "display_name": display_name, "spawn_request": spawn_request, **route, "luna_fallback": luna_fallback, "retry": retry, "parallel": bool(params.get("parallel", False)), "task_objective": redact(task_definition.get("objective", ""), 4000), "task_requirements": [redact(item, 1000) for item in task_definition.get("requirements", [])][:100], "task_scope": [redact(item, 500) for item in task_definition.get("scope", [])][:100], "task_acceptance_criteria": [redact(item, 1000) for item in task_definition.get("acceptance_criteria", [])][:100], "task_verification": [redact(item, 1000) for item in task_definition.get("verification", [])][:100], "budget": redact(task_definition.get("budget", ""), 500), "pause_conditions": [redact(item, 1000) for item in task_definition.get("pause_conditions", [])][:100], "plan_feedback": redact(params.get("plan_feedback", ""), 2000) or None, "objective": redact(objective, 4000), "ownership": redact(ownership, 1000), "context_files": [redact(item, 500) for item in context_files], "knowledge_index_files": knowledge_index_files, "context_report_ids": context_report_ids, "report_index": "sqlite:task_documents/report_index", "result_baseline_ref": result_baseline_ref, "allowed_paths": [redact(item, 500) for item in required_lists["allowed_paths"]][:50], "acceptance_criteria": [redact(item, 1000) for item in required_lists["acceptance_criteria"]][:50], "verification": [redact(item, 1000) for item in required_lists["verification"]][:50], "project_root": str(project_root), "coordinator_principal": state.get("principal", "local"), "coordinator_thread_id": state.get("thread_id", ""), "internal_language": "en", "visibility": "visible" if visible_thread else "hidden", "user_facing": visible_thread, "user_owned_thread": visible_thread, "thread_environment": thread_environment, "question_route": question_route, "escalation_route": "main_chat", "handoff_route": "main_chat", "subdelegation": "forbidden_unless_explicitly_authorized", "report_contract": REPORT_SCHEMA, "question_contract": QUESTION_SCHEMA, "facade_managed": facade_managed, "orchestration_wave_id": orchestration_wave_id, "orchestration_delegation_key": orchestration_delegation_key, "status_receipt": status_receipt, "dispatch_correlation": "host_spawn_required", "spawn_status": "requested", "created_at": now()}
        package["dispatch_ref"] = dispatch_ref
        package["briefing_file"] = briefing_file
        package["pause_conditions"] = [redact(item, 1000) for item in task_definition.get("pause_conditions", [])][:100]
        if isinstance(task_definition.get("follow_up"), dict):
            package["follow_up"] = sanitize_structured(task_definition["follow_up"])
        package["task_user_request"] = redact(
            task_definition.get("user_request") or task_definition.get("objective", ""), 4000
        )
        package["intent_clarification_required"] = bool(task_definition.get("intent_clarification_required", False))
        package["intent_clarification_reason"] = redact(
            task_definition.get("intent_clarification_reason", ""), 500
        ) or None
        full_briefing = host_spawn_prompt(agent, package)
        briefing_digest = write_text_immutable(briefing_path, full_briefing)
        briefing_artifact = store_immutable_artifact(
            task_dir, state["task_id"], kind="dispatch_briefing", title=briefing_file,
            mime_type="text/markdown", content=full_briefing, export_path=briefing_file,
        )
        package["briefing_digest"] = briefing_digest
        package["briefing_artifact_ref"] = briefing_artifact["artifact_ref"]
        spawn_request["dispatch_ref"] = dispatch_ref
        spawn_request["briefing_file"] = briefing_file
        spawn_request["briefing_path"] = str(briefing_path)
        spawn_request["briefing_digest"] = briefing_digest
        spawn_request["message"] = host_spawn_bootstrap(
            agent, briefing_path, briefing_digest, dispatch_ref, state["task_id"], attempt_id, project_root
        )
        if visible_thread:
            # create_thread calls this field `prompt`; retaining `message`
            # keeps the package readable by existing coordinator adapters.
            spawn_request["prompt"] = spawn_request["message"]
            spawn_request["title"] = display_name
        _write_delegation_package(task_dir, state["task_id"], attempt_id, package)
        state["attempts"].append({"attempt_id": attempt_id, "gate": gate, "agent": agent, "profile": agent, "display_name": display_name, "dispatch_ref": dispatch_ref, "briefing_file": briefing_file, "briefing_digest": briefing_digest, "briefing_artifact_ref": briefing_artifact["artifact_ref"], "spawn_request": spawn_request, **route, "luna_fallback": luna_fallback, "ownership": package["ownership"], "result_baseline_ref": result_baseline_ref, "result_baseline_digest": result_baseline.get("digest"), "allowed_paths": package["allowed_paths"], "acceptance_criteria": package["acceptance_criteria"], "verification": package["verification"], "context_files": package["context_files"], "knowledge_index_files": knowledge_index_files, "context_report_ids": context_report_ids, "visibility": package["visibility"], "user_facing": visible_thread, "user_owned_thread": visible_thread, "thread_environment": thread_environment, "return_route": "main_chat", "facade_managed": facade_managed, "orchestration_wave_id": orchestration_wave_id, "orchestration_delegation_key": orchestration_delegation_key, "status": AWAITING_HOST_SPAWN, "parallel": bool(params.get("parallel", False)), "evidence_ids": [], "report_ids": [], "created_at": now()})
        _, delegation_index = _delegation_report_index(report_paths, state["task_id"], attempt_id)
        delegation_index["context_report_ids"] = context_report_ids
        delegation_index["updated_at"] = now()
        _write_delegation_report_index(report_paths, state["task_id"], attempt_id, delegation_index)
        save_state(task_dir, task_dir / "state.sqlite", state, "delegation", f"{gate} → {agent} ({attempt_id})")
        return {
            "delegation_ref": f"dispatch:{attempt_id}",
            "briefing_file": str(briefing_path),
            "briefing_digest": briefing_digest,
            "dispatch_ref": dispatch_ref,
            "attempt_id": attempt_id,
            "spawn_request": spawn_request,
            "state": state,
            "gate_correction": ({"requested": requested_gate, "used": gate} if requested_gate != gate else None),
            "revision_correction": revision_correction,
            "receipt_correction": receipt_correction,
            "agent_correction": agent_correction,
            "task_kind_correction": ({"requested": requested_task_kind or None, "used": task_kind} if requested_task_kind != task_kind else None),
            "risk_correction": ({"requested": requested_risk or None, "used": risk} if requested_risk != risk else None),
        }
