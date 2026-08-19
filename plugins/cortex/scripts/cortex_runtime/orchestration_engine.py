"""SQLite-native orchestration state machine behind the public facade.

The eight public MCP handlers stay composed by :mod:`cortex`. This module owns
orchestration transactions, waves, recovery and management operations, and is
loaded lazily by the facade after the entrypoint has completed initialization.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cortex_runtime.core.runtime_bindings import bind_symbols, bound_symbol


class PlanReapprovalRequired(ValueError):
    """A post-plan dispatch cannot use the currently approved evidence basis."""


# The executable facade is the composition root.  It supplies this explicitly
# declared port set after it has finished initialization; this module never
# imports the facade and therefore cannot form a reverse import cycle.
bind_symbols(
    "orchestration_engine",
    globals(),
    (
        "ACTIVATION_COMMAND",
        "AGENTS",
        "AVAILABLE_GATES",
        "AWAITING_HOST_SPAWN",
        "MAX_CONTEXT_REPORTS",
        "MAX_ORCHESTRATE_GATE_FAILURES",
        "MAX_SAME_STRATEGY_FAILURES",
        "MAX_WORK_PACKAGES",
        "NORMAL_COMMAND",
        "ORCHESTRATE_MUTATING_OPERATIONS",
        "ORCHESTRATE_OPERATIONS",
        "ORCHESTRATE_SCHEMA",
        "ORCHESTRATION_PLAN_SCHEMA",
        "ORCHESTRATION_TRANSACTION_SCHEMA",
        "PIPELINE_CONTRACT_VERSION",
        "REPORT_SCHEMA",
        "SUPPORTED_MODELS",
        "TERMINAL_ATTEMPT_STATUSES",
        "_attempt",
        "_collect_orchestrate_diagnostics",
        "_context_handoff_service",
        "_delegation_package",
        "_ledger_root_for_artifact",
        "_open_blocking_questions",
        "_plan_approval",
        "_plan_approval_is_pending",
        "_report_index",
        "_validate_report_decision_closure",
        "_write_delegation_package",
        "acquire_lock",
        "activate_orchestration",
        "active_gates",
        "answer_worker_question",
        "authorize",
        "authorize_principal",
        "bind_task_lane",
        "canonical_pipeline_gate",
        "capture_project_manifest",
        "claim_lane",
        "claim_lane_resource",
        "claim_resource",
        "classify",
        "classify_task",
        "close_audit",
        "compare_manifests",
        "cortex_question",
        "create_lane",
        "current_planning_manifest",
        "db_get_classification",
        "db_get_operation",
        "db_list_task_findings",
        "db_load_task",
        "db_put_operation",
        "db_update_task_plan",
        "deactivate_orchestration",
        "digest_text",
        "execute_verification",
        "finalize_attempt",
        "get_worker_question_updates",
        "handoff",
        "init_task",
        "lane_status",
        "ledger_root",
        "list_worker_questions",
        "load_state",
        "load_task_definition",
        "materialize_lane",
        "normalize_parallel_groups",
        "now",
        "primary_gate",
        "publish_worker_question",
        "read_immutable_json_artifact",
        "reassess_pipeline",
        "reconcile_lane",
        "record_delegation",
        "record_evidence",
        "record_gate",
        "redact",
        "release_lane",
        "release_lane_resource",
        "release_lock",
        "release_resource",
        "render_gate_briefing",
        "report_bus_paths",
        "report_markdown_link",
        "report_markdown_path",
        "resolve_dispatch_route",
        "resume_task",
        "retire_lane",
        "safe_id",
        "sanitize_report_payload",
        "sanitize_structured",
        "save_state",
        "select_project_root",
        "state_lock",
        "status",
        "task_manifest_baseline",
        "task_paths",
        "update_pipeline",
    ),
)

def _orchestrate_error(
    operation: str,
    code: str,
    message: object,
    *,
    phase: str = "validation",
    recoverable: bool = True,
    next_operation: str | None = None,
    task_id: str | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_next_operation = next_operation or (operation if operation in ORCHESTRATE_OPERATIONS else None)
    return {
        "schema": ORCHESTRATE_SCHEMA,
        "ok": False,
        "operation": operation,
        "transaction_id": None,
        "task_id": task_id,
        "wave_id": None,
        "state": "blocked" if not recoverable else "needs_input",
        "spawn_requests": [],
        "phase": phase,
        "code": code,
        "diagnostics": diagnostics or [{"code": code, "phase": phase, "message": redact(message, 1000)}],
        "recoverable": recoverable,
        "next_operation": resolved_next_operation,
        "next_action": (
            f"retry orchestrate(operation={resolved_next_operation}) with a new submission_id after correcting the diagnostic"
            if recoverable and resolved_next_operation else
            "retry orchestrate with a supported operation" if recoverable else
            "inspect the Cortex installation"
        ),
    }


def _orchestrate_state_name(state: dict[str, Any]) -> str:
    if state.get("status") == "completed":
        return "completed"
    if state.get("status") == "blocked":
        return "blocked"
    if _plan_approval_is_pending(state):
        return "awaiting_plan_approval"
    current = set(active_gates(state))
    attempts = [item for item in state.get("attempts", []) if item.get("gate") in current and not item.get("invalidated")]
    if any(item.get("status") == AWAITING_HOST_SPAWN for item in attempts):
        return "ready_to_spawn"
    if any(item.get("status") == "running" for item in attempts):
        return "waiting_workers"
    return "needs_input"


def _orchestrate_summary(state: dict[str, Any]) -> dict[str, Any]:
    done = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    return {
        "status": state.get("status"),
        "revision": state.get("revision"),
        "complexity": state.get("complexity"),
        "current_gates": active_gates(state),
        "completed_gates": list(state.get("completed_gates", [])),
        "skipped_gates": list(state.get("skipped_gates", [])),
        "current_pipeline": list(state.get("current_pipeline", [])),
        "remaining_gates": [gate for gate in state.get("current_pipeline", []) if gate not in done],
        "close_verified": any(
            item.get("gate") == "close"
            and item.get("verified_execution")
            and item.get("exit_code") == 0
            for item in state.get("evidence", [])
        ),
        "handoff_created": bool(state.get("handoff_created")),
        "plan_approval": {
            "policy": _plan_approval(state).get("policy", "auto"),
            "status": _plan_approval(state).get("status", "not_required"),
            "plan_report_ref": _plan_approval(state).get("plan_report_ref"),
            "approved_basis": _plan_approval(state).get("approved_basis"),
        },
        "pipeline_contract_version": _pipeline_contract_version(state),
        "attempts": [
            {
                "attempt_id": item.get("attempt_id"),
                "gate": item.get("gate"),
                "profile": item.get("profile"),
                "status": item.get("status"),
                "wave_id": item.get("orchestration_wave_id"),
            }
            for item in state.get("attempts", [])
            if not item.get("invalidated")
        ],
    }


def _context_handoff(
    task_dir: Path,
    state: dict[str, Any],
    task: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Public delegating entrypoint for the context-compaction handoff renderer."""
    return _context_handoff_service(task_dir, state, task, plan)



def _orchestrate_pipeline_snapshot(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Expose the coordinator-owned canonical plan without durable attempt ids."""
    completed = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    current = set(active_gates(state))
    waves = []
    for index, wave in enumerate(plan.get("waves", []), 1):
        gates = list(wave.get("gates", []))
        if gates and set(gates).issubset(completed):
            status_value = "completed"
        elif set(gates) == current and state.get("status") == "blocked":
            status_value = "blocked"
        elif set(gates) == current:
            status_value = "active"
        else:
            status_value = "pending"
        waves.append({
            "wave": index,
            "status": status_value,
            "workers": [
                {
                    "phase": item.get("gate"),
                    "profile": item.get("agent"),
                }
                for item in wave.get("delegations", [])
                if isinstance(item, dict)
            ],
        })
    return {
        "authority": "coordinator",
        "revision": state.get("revision"),
        "waves": waves,
        "change_policy": (
            "Follow this plan by default. The coordinator may replace future_waves when new evidence changes "
            "ownership, dependencies, risk, or validation; include the reason. Cortex validates canonical phases, "
            "profile ownership, mandatory documentation/close, and duplicate gates."
        ),
    }


def _orchestrate_transaction_path(root: Path, submission_id: str) -> Path:
    """Return the ledger root; transaction receipts are SQLite records."""
    safe_id(submission_id)
    return root


def _orchestrate_request_digest(params: dict[str, Any]) -> str:
    return digest_text(json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


def _begin_orchestrate_transaction(root: Path, params: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any] | None]:
    submission_id = safe_id(str(params.get("submission_id", "")))
    path = _orchestrate_transaction_path(root, submission_id)
    request_digest = _orchestrate_request_digest(params)
    receipt = db_get_operation(root, submission_id)
    if receipt is not None:
        if receipt.get("schema") != ORCHESTRATION_TRANSACTION_SCHEMA:
            raise ValueError("orchestrate submission_id was reused with different content")
        retryable_future_correction = (
            params.get("operation") == "advance"
            and receipt.get("operation") == "advance"
            and receipt.get("status") == "failed"
            and receipt.get("phase") == "gates_recorded"
        )
        if receipt.get("request_digest") != request_digest and not retryable_future_correction:
            raise ValueError("orchestrate submission_id was reused with different content")
        if receipt.get("request_digest") != request_digest:
            # Completion and gate recording already committed before a later
            # future-wave/briefing validation failed.  Resume that durable
            # transaction with the corrected future contract instead of
            # forcing a stale payload that the caller was explicitly told to
            # correct.
            receipt["request_digest"] = request_digest
            receipt["corrected_at"] = now()
        if receipt.get("status") == "committed" and isinstance(receipt.get("result"), dict):
            replay = dict(receipt["result"])
            replay["idempotent"] = True
            return path, receipt, replay
        receipt["resumed_at"] = now()
        receipt["status"] = "running"
        db_put_operation(root, submission_id, receipt)
        return path, receipt, None
    receipt = {
        "schema": ORCHESTRATION_TRANSACTION_SCHEMA,
        "transaction_id": f"transaction-{submission_id}",
        "submission_id": submission_id,
        "operation": params["operation"],
        "request_digest": request_digest,
        "task_id": str(params.get("task_id") or (params.get("task") or {}).get("task_id") or "") or None,
        "status": "running",
        "phase": "started",
        "context": {},
        "created_at": now(),
        "updated_at": now(),
    }
    db_put_operation(root, submission_id, receipt)
    return path, receipt, None


def _checkpoint_orchestrate_transaction(path: Path, receipt: dict[str, Any], phase: str, **context: Any) -> None:
    receipt["phase"] = phase
    receipt.setdefault("context", {}).update(context)
    receipt["updated_at"] = now()
    db_put_operation(path, safe_id(str(receipt["submission_id"])), receipt)


def _commit_orchestrate_transaction(path: Path, receipt: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    result = {**result, "transaction_id": receipt["transaction_id"], "idempotent": False}
    receipt.update({"status": "committed", "phase": "committed", "result": result, "updated_at": now(), "committed_at": now()})
    db_put_operation(path, safe_id(str(receipt["submission_id"])), receipt)
    return result


def _leave_orchestrate_transaction_retryable(
    path: Path,
    receipt: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Return a recoverable host-bound result without caching it as success."""
    result = {**result, "transaction_id": receipt["transaction_id"], "idempotent": False}
    receipt.update(
        {
            "status": "retryable",
            "phase": "awaiting_host_capability",
            "result": result,
            "updated_at": now(),
        }
    )
    db_put_operation(path, safe_id(str(receipt["submission_id"])), receipt)
    return result


def _default_profile_for_gate(gate: str) -> str:
    return {
        "scope": "planner",
        "plan": "planner",
        "discover": "explorer",
        "architecture": "architect",
        "database_architecture": "database_architect",
        "implementation": "general",
        "qa": "qa_engineer",
        "security": "security_auditor",
        "performance": "performance_engineer",
        "accessibility": "accessibility_engineer",
        "ux": "ux_designer",
        "review": "code_reviewer",
        "documentation": "technical_writer",
        "close": "build_verification",
    }.get(gate, "general")


def _default_task_kind_for_gate(gate: str) -> str:
    return {
        "scope": "scoping", "plan": "planning", "discover": "discovery", "architecture": "architecture",
        "database_architecture": "database", "implementation": "implementation", "qa": "testing",
        "security": "security", "performance": "performance", "accessibility": "accessibility",
        "ux": "ux", "review": "code_review", "documentation": "documentation", "close": "verification",
    }.get(gate, gate)


def _normalize_orchestrate_waves(
    raw_waves: object,
    task: dict[str, Any],
    host_capabilities: dict[str, Any],
    project_root_value: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(raw_waves, list) or not raw_waves:
        raise ValueError("start requires a non-empty waves array")
    by_gate: dict[str, list[dict[str, Any]]] = {}
    original_ids: dict[tuple[str, ...], str] = {}
    proposed_pipeline: list[str] = []
    proposed_groups: list[list[str]] = []
    for index, raw_wave in enumerate(raw_waves, 1):
        if not isinstance(raw_wave, dict):
            raise ValueError("each wave must be an object")
        wave_id = safe_id(str(raw_wave.get("wave_id") or f"wave-{index:02d}"))
        raw_delegations = raw_wave.get("delegations")
        if not isinstance(raw_delegations, list) or not raw_delegations or len(raw_delegations) > 32:
            raise ValueError("each wave requires 1..32 delegation specs")
        group: list[str] = []
        for raw_spec in raw_delegations:
            if not isinstance(raw_spec, dict):
                raise ValueError("delegation specs must be objects")
            gate = canonical_pipeline_gate(raw_spec.get("gate") or "")
            if gate not in AVAILABLE_GATES:
                raise ValueError(f"unsupported gate in wave: {gate}")
            if gate not in group:
                group.append(gate)
            if gate not in proposed_pipeline:
                proposed_pipeline.append(gate)
            by_gate.setdefault(gate, []).append(dict(raw_spec))
        original_ids[tuple(group)] = wave_id
        proposed_groups.append(group)
    complexity = str(task.get("complexity", "C2")).upper()
    classification = classify({
        "complexity": complexity,
        "requirements": task.get("requirements", []),
        "pipeline": proposed_pipeline,
        "parallel_groups": proposed_groups,
    })
    spawn_models = host_capabilities.get("spawn_agent_models") or host_capabilities.get("available_models")
    thread_models = host_capabilities.get("create_thread_models") or host_capabilities.get("available_thread_models")
    configured_default_model = str(
        host_capabilities.get("spawn_agent_default_model")
        or host_capabilities.get("configured_default_model")
        or ""
    ).strip()
    if configured_default_model and configured_default_model not in SUPPORTED_MODELS:
        raise ValueError("host_capabilities.spawn_agent_default_model must be a supported model")
    if not isinstance(spawn_models, list) or not spawn_models:
        raise ValueError("host_capabilities.spawn_agent_models must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, group in enumerate(classification["parallel_groups"], 1):
        wave_id = original_ids.get(tuple(group), f"wave-{index:02d}")
        if wave_id in used_ids:
            wave_id = f"wave-{index:02d}"
        used_ids.add(wave_id)
        delegations: list[dict[str, Any]] = []
        for gate in group:
            specs = by_gate.get(gate) or [{"gate": gate}]
            for spec_index, raw_spec in enumerate(specs, 1):
                agent = str(raw_spec.get("agent") or _default_profile_for_gate(gate))
                if agent not in AGENTS:
                    raise ValueError(f"unknown Cortex profile: {agent}")
                briefing = render_gate_briefing(gate, task.get("objective", ""), agent)
                objective = str(raw_spec.get("objective") or briefing["objective"]).strip()
                ownership = str(raw_spec.get("ownership") or briefing["ownership"]).strip()
                task_kind = str(raw_spec.get("task_kind") or _default_task_kind_for_gate(gate))
                risk = str(raw_spec.get("risk") or ("high" if gate == "security" else "low" if gate in {"scope", "plan", "discover", "documentation"} else "moderate"))
                spec = {
                    **raw_spec,
                    "gate": gate,
                    "agent": agent,
                    "task_kind": task_kind,
                    "risk": risk,
                    "objective": objective,
                    "ownership": ownership,
                    "allowed_paths": raw_spec.get("allowed_paths") or task.get("allowed_paths") or ["."],
                    "acceptance_criteria": raw_spec.get("acceptance_criteria") or briefing["acceptance_criteria"],
                    "verification": raw_spec.get("verification") or briefing["verification"],
                    "available_models": raw_spec.get("available_models") or spawn_models,
                    "available_thread_models": raw_spec.get("available_thread_models") or thread_models,
                    "configured_default_model": raw_spec.get("configured_default_model") or configured_default_model,
                    "parallel": len(group) > 1 or len(specs) > 1,
                    "facade_managed": True,
                    "orchestration_wave_id": wave_id,
                    "orchestration_delegation_key": f"{wave_id}-{gate}-{spec_index:02d}",
                }
                route = resolve_dispatch_route({
                    **spec,
                    "complexity": complexity,
                    "_security_gate": gate == "security",
                    "project_root": project_root_value,
                })
                if (
                    str(spec.get("dispatch_mode", "hidden_subagent")) == "visible_thread"
                    and (not isinstance(thread_models, list) or "gpt-5.6-luna" not in thread_models)
                ):
                    raise ValueError("visible_thread requires create_thread_models to include gpt-5.6-luna")
                delegations.append(spec)
        normalized.append({"wave_id": wave_id, "gates": list(group), "delegations": delegations, "status": "pending"})
    return normalized, classification


def _validate_v2_wave_contract(
    waves: list[dict[str, Any]],
    *,
    plan_approval: str,
) -> None:
    """Enforce evidence-first ordering for newly created pipeline-v2 tasks."""
    positions = {
        gate: index
        for index, wave in enumerate(waves)
        for gate in wave.get("gates", [])
    }
    if "scope" in positions and "discover" in positions and positions["scope"] >= positions["discover"]:
        raise ValueError("pipeline contract v2 requires scope before discover")
    design_gates = [gate for gate in ("architecture", "database_architecture", "ux") if gate in positions]
    if "discover" in positions:
        late_design = [gate for gate in design_gates if positions[gate] <= positions["discover"]]
        if late_design:
            raise ValueError("pipeline contract v2 requires discovery before design gates: " + ", ".join(late_design))
    preplan = [
        gate for gate in ("scope", "discover", "architecture", "database_architecture", "ux")
        if gate in positions
    ]
    if "plan" in positions:
        late = [gate for gate in preplan if positions[gate] >= positions["plan"]]
        if late:
            raise ValueError(
                "pipeline contract v2 requires final plan after scope, discovery, and pre-implementation design gates: "
                + ", ".join(late)
            )
    if plan_approval == "required":
        if "plan" not in positions:
            raise ValueError("plan_approval=required requires a singleton final plan wave")
        plan_wave = waves[positions["plan"]]
        if plan_wave.get("gates") != ["plan"] or len(plan_wave.get("delegations", [])) != 1:
            raise ValueError("plan_approval=required requires a singleton final plan wave")
        if plan_wave["delegations"][0].get("agent") != "planner":
            raise ValueError("plan_approval=required requires the planner profile for the final plan wave")
    if "implementation" in positions:
        for audit in ("security", "performance", "accessibility"):
            if audit in positions and positions[audit] <= positions["implementation"]:
                raise ValueError(f"pipeline contract v2 requires {audit} after implementation")
    if "review" in positions:
        for audit in ("security", "performance", "accessibility"):
            if audit in positions and positions[audit] >= positions["review"]:
                raise ValueError(f"pipeline contract v2 requires {audit} before review")


def _orchestrate_plan_path(task_dir: Path) -> Path:
    """Compatibility label; canonical plans are stored in ``tasks.plan_json``."""
    return _ledger_root_for_artifact(task_dir) / "cortex.db"


def _write_orchestrate_plan(task_dir: Path, plan: dict[str, Any]) -> None:
    plan["updated_at"] = now()
    db_update_task_plan(_ledger_root_for_artifact(task_dir), safe_id(str(plan.get("task_id") or "")), plan)


def _orchestrate_wave_contract(waves: object) -> list[dict[str, Any]]:
    """Return the immutable portion of a facade wave plan for replay checks."""
    if not isinstance(waves, list):
        return []
    return [
        {
            "wave_id": wave.get("wave_id"),
            "gates": wave.get("gates"),
            "delegations": wave.get("delegations"),
        }
        for wave in waves
        if isinstance(wave, dict)
    ]


def _load_orchestrate_plan(task_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Load the canonical plan; tasks without one are not orchestration tasks."""
    loaded = db_load_task(_ledger_root_for_artifact(task_dir), safe_id(str(state.get("task_id") or "")))
    plan = loaded[2] if loaded is not None else None
    if plan is None:
        raise ValueError("canonical orchestration plan is missing from the SQLite task record")
    if plan.get("schema") != ORCHESTRATION_PLAN_SCHEMA or plan.get("task_id") != state.get("task_id"):
        raise ValueError("orchestrate plan schema or task identity is not supported")
    return plan


def _wave_for_gates(plan: dict[str, Any], gates: list[str]) -> dict[str, Any] | None:
    gate_set = set(gates)
    return next((wave for wave in plan.get("waves", []) if set(wave.get("gates", [])) == gate_set), None)


def _predecessor_context_report_ids(
    state: dict[str, Any],
    required_gates: set[str] | None = None,
) -> list[str]:
    """Select verified reports from completed predecessor attempts in ledger order."""
    completed = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    valid_report_ids = {
        str(item.get("report_id"))
        for item in state.get("evidence", [])
        if item.get("report_id") and not item.get("invalidated")
    }
    selected: list[str] = []
    for attempt in state.get("attempts", []):
        if (
            attempt.get("status") != "passed"
            or attempt.get("invalidated")
            or attempt.get("gate") not in completed
            or (required_gates is not None and attempt.get("gate") not in required_gates)
        ):
            continue
        for report_id in attempt.get("report_ids", []):
            value = str(report_id)
            if value in valid_report_ids and value not in selected:
                selected.append(value)
    return selected


def _transitive_context_frontier(state: dict[str, Any], report_ids: list[str]) -> list[str]:
    """Collapse acknowledged predecessor chains without losing durable history.

    A passed report can cover only the exact predecessor refs granted to its
    attempt. Report intake already proves that it read and acknowledged every
    one of those refs. Keeping the reports that are not covered by another
    selected report therefore bounds a successor handoff by the current DAG
    frontier while the immutable ledger and plan-basis digest retain the full
    history.
    """
    selected = list(dict.fromkeys(str(report_id) for report_id in report_ids))
    selected_set = set(selected)
    covered: set[str] = set()
    for attempt in state.get("attempts", []):
        if attempt.get("status") != "passed" or attempt.get("invalidated"):
            continue
        produced = set(str(report_id) for report_id in attempt.get("report_ids", []))
        if not produced.intersection(selected_set):
            continue
        covered.update(
            str(report_id) for report_id in attempt.get("context_report_ids", [])
            if str(report_id) in selected_set
        )
    return [report_id for report_id in selected if report_id not in covered]


def _rework_context_report_ids(state: dict[str, Any], gates: set[str]) -> list[str]:
    """Keep the report that opened an active corrective wave in its context.

    A closure rework invalidates its predecessor gate, so ordinary predecessor
    selection intentionally excludes that report.  The corrective worker still
    needs it: otherwise it receives a generic implementation task with no
    durable statement of the defect it must resolve.
    """
    selected: list[str] = []
    for source_gate, rework in (state.get("closure_rework") or {}).items():
        if not isinstance(rework, dict):
            continue
        if rework.get("status") != "rework_required" or not (
            rework.get("target_gate") in gates or source_gate in gates
        ):
            continue
        for report_ref in rework.get("source_report_refs") or []:
            value = str(report_ref).strip()
            if value and value not in selected:
                selected.append(value)
    return selected


def _unresolved_rework_findings(
    root: Path,
    state: dict[str, Any],
    gate: str,
) -> list[dict[str, Any]]:
    """Return findings that their originating verification gate still has to close."""
    findings = {
        str(item.get("fingerprint")): item
        for item in db_list_task_findings(root, state["task_id"], include_resolved=False)
    }
    unresolved: list[dict[str, Any]] = []
    for source_gate, rework in (state.get("closure_rework") or {}).items():
        # A corrective implementation/documentation worker may perform the
        # change, but the gate that found the defect must verify it.  Holding
        # the source gate rather than the writer preserves the canonical
        # implementation -> QA or documentation -> review route and prevents
        # a generic writer report from being treated as proof of a fix.
        if (
            not isinstance(rework, dict)
            or rework.get("status") != "rework_required"
            or source_gate != gate
        ):
            continue
        fingerprints = [str(item) for item in rework.get("finding_fingerprints") or []]
        open_findings = [
            findings[fingerprint]
            for fingerprint in fingerprints
            if findings.get(fingerprint, {}).get("status") == "open"
        ]
        if open_findings:
            unresolved.extend(open_findings)
        elif rework.get("status") == "rework_required":
            rework["status"] = "resolved"
            rework["resolved_at"] = now()
            rework["resolved_by_gate"] = gate
    return unresolved


def _prepare_orchestrate_wave(params: dict[str, Any], task_dir: Path, state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    current_gates = active_gates(state)
    if not current_gates:
        return {"wave_id": None, "spawn_requests": [], "attempt_ids": [], "state": state}
    _assert_approved_plan_fresh(task_dir, state, plan)
    wave = _wave_for_gates(plan, current_gates)
    if wave is None:
        raise ValueError("orchestrate plan has no wave for the current gates")
    retired_failures = False
    for attempt in state.get("attempts", []):
        if (
            attempt.get("gate") in current_gates
            and attempt.get("status") in {"failed", "cancelled", "superseded"}
            and not attempt.get("invalidated")
        ):
            attempt["invalidated"] = True
            attempt["invalidated_at"] = now()
            attempt["invalidation_reason"] = "retry_after_failure"
            retired_failures = True
    if retired_failures:
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "retry_invalidation",
            "retired unsuccessful attempts before retry",
        )
    prepared_attempts: list[dict[str, Any]] = []
    predecessor_report_ids = _predecessor_context_report_ids(state)
    rework_report_ids = _rework_context_report_ids(state, set(current_gates))
    for spec in wave["delegations"]:
        key = spec["orchestration_delegation_key"]
        existing = next(
            (
                item for item in state.get("attempts", [])
                if not item.get("invalidated")
                and item.get("status") in {AWAITING_HOST_SPAWN, "running", "passed"}
                and (
                    item.get("orchestration_delegation_key") == key
                    or (
                        not item.get("orchestration_delegation_key")
                        and item.get("gate") == spec["gate"]
                        and item.get("agent") == spec["agent"]
                    )
                )
            ),
            None,
        )
        if existing is not None:
            if not existing.get("orchestration_wave_id"):
                existing["orchestration_wave_id"] = wave["wave_id"]
                existing["orchestration_delegation_key"] = key
            prepared_attempts.append(existing)
            continue
        observed = status({**params, "task_id": state["task_id"]})
        if "context_report_ids" in spec:
            context_report_ids = list(spec.get("context_report_ids") or [])
        elif "context_gates" in spec:
            context_report_ids = _predecessor_context_report_ids(
                state,
                {canonical_pipeline_gate(item) for item in spec.get("context_gates") or []},
            )
        else:
            context_report_ids = predecessor_report_ids
        if spec.get("gate") == "plan" and _pipeline_contract_version(state) >= 2:
            required_basis, _ = _verified_plan_predecessor_basis(task_dir, state)
            # The final planner's verified scope/discovery/design basis is a
            # server-owned safety dependency.  A compact future-wave request
            # may narrow its ordinary context_gates, but must never make the
            # coordinator reconstruct or guess these durable report refs.
            context_report_ids = list(dict.fromkeys(
                context_report_ids + [item["report_ref"] for item in required_basis]
            ))
        # A verified successor report transitively covers only refs that its
        # own passed attempt acknowledged. Preserve active rework sources even
        # when a later ordinary handoff covered them: they are the current
        # corrective mission, not merely historical context.
        context_report_ids = list(dict.fromkeys(
            rework_report_ids + _transitive_context_frontier(state, context_report_ids)
        ))
        delegated = record_delegation({
            **params,
            **spec,
            **(
                {"plan_feedback": _plan_approval(state).get("feedback")}
                if spec.get("gate") == "plan" and _plan_approval(state).get("feedback") else {}
            ),
            "context_report_ids": context_report_ids,
            "task_id": state["task_id"],
            "expected_revision": observed["state"]["revision"],
            "status_receipt": observed["status_receipt"],
        })
        if delegated.get("recorded") is False:
            raise ValueError(str(delegated.get("reason") or "wave delegation was not recorded"))
        state = delegated["state"]
        prepared_attempts.append(_attempt(state, delegated["attempt_id"]))
    wave["status"] = "active"
    wave["attempt_ids"] = [item["attempt_id"] for item in prepared_attempts]
    _write_orchestrate_plan(task_dir, plan)
    save_state(task_dir, task_dir / "state.sqlite", state, "orchestrate_wave", wave["wave_id"])
    spawn_requests = [
        {**item["spawn_request"], "attempt_id": item["attempt_id"]}
        for item in prepared_attempts
        if item.get("status") == AWAITING_HOST_SPAWN
    ]
    return {"wave_id": wave["wave_id"], "spawn_requests": spawn_requests, "attempt_ids": wave["attempt_ids"], "state": state}


def _orchestrate_response(
    operation: str,
    state: dict[str, Any],
    *,
    wave_id: str | None = None,
    spawn_requests: list[dict[str, Any]] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    facade_state = _orchestrate_state_name(state)
    if facade_state == "ready_to_spawn":
        next_action = "invoke every returned native spawn request, wait for the wave, then call orchestrate(operation=advance) once"
    elif facade_state == "waiting_workers":
        next_action = "wait for every worker in the current wave, then call orchestrate(operation=advance) once"
    elif facade_state == "completed":
        next_action = "report the verified task result to the user"
    elif facade_state == "blocked":
        next_action = "resolve the blocker, then call orchestrate(operation=resume)"
    elif facade_state == "awaiting_plan_approval":
        next_action = (
            "read the planner report, present a concise main-chat plan summary, and call plan_approval with "
            "decision=prompt. An initialized stdio host receives native Approve/Cancel controls; direct callers "
            "receive the cortex/plan-approval/v1 fallback interaction and must submit only its embedded response "
            "arguments, or leave the plan pending when the host cannot render it"
        )
    else:
        next_action = "inspect the returned diagnostics or provide the required completion data"
    response = {
        "schema": ORCHESTRATE_SCHEMA,
        "ok": True,
        "operation": operation,
        "transaction_id": None,
        "task_id": state.get("task_id"),
        "wave_id": wave_id,
        "state": facade_state,
        "state_summary": _orchestrate_summary(state),
        "spawn_requests": spawn_requests or [],
        "diagnostics": diagnostics or [],
        "next_action": next_action,
    }
    if facade_state == "waiting_workers":
        response.update({
            "output_policy": "silent",
            "allowed_visible_events": [
                "user_message", "worker_question", "worker_completed", "worker_failed", "blocking_error",
            ],
        })
    if isinstance(plan, dict):
        response["pipeline"] = _orchestrate_pipeline_snapshot(state, plan)
    if result is not None:
        response["result"] = result
        if result.get("decision") == "cancelled":
            response["output_policy"] = "silent"
            response["allowed_visible_events"] = ["user_message"]
            response["next_action"] = (
                "keep the plan approval pending and wait for a later user message; do not dispatch, revise, "
                "or emit cancellation commentary"
            )
    return response


def _plan_approval_request_id(state: dict[str, Any], approval: dict[str, Any]) -> str:
    """Return the opaque request id bound to one pending plan revision."""
    pending_basis = approval.get("pending_basis")
    if not isinstance(pending_basis, dict):
        pending_basis = {
            key: approval.get(key)
            for key in (
                "pipeline_contract_version", "plan_revision", "plan_report_ref",
                "verified_predecessor_digest", "semantic_pipeline_version",
                "semantic_future_pipeline_digest",
            )
            if approval.get(key) is not None
        }
    seed = {
        "task_id": str(state.get("task_id") or ""),
        "pending_basis": pending_basis,
    }
    return "plan-approval-" + digest_text(json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")))[:32]


def _orchestrate_start(params: dict[str, Any], transaction_path: Path, transaction: dict[str, Any]) -> dict[str, Any]:
    task = params.get("task")
    if not isinstance(task, dict):
        raise ValueError("start requires a task object")
    task_id = safe_id(str(task.get("task_id", "")))
    objective = str(task.get("objective", "")).strip()
    if not objective:
        raise ValueError("start task.objective is required")
    principal = str(params.get("principal", "")).strip()
    thread_id = str(params.get("thread_id", "")).strip()
    if not principal or not thread_id:
        raise ValueError("start requires principal and thread_id")
    host_capabilities = params.get("host_capabilities")
    if not isinstance(host_capabilities, dict):
        raise ValueError("start requires host_capabilities")
    waves, classification_preview = _normalize_orchestrate_waves(params.get("waves"), task, host_capabilities, str(params["project_root"]))
    root = ledger_root(params)
    existing_contract_version = PIPELINE_CONTRACT_VERSION
    try:
        _, _, existing_preview = load_state(task_id, params)
    except (FileNotFoundError, ValueError):
        existing_preview = None
    if isinstance(existing_preview, dict):
        existing_contract_version = int(existing_preview.get("pipeline_contract_version") or 1)
    if existing_contract_version >= 2:
        _validate_v2_wave_contract(
            waves,
            plan_approval=str(task.get("plan_approval") or "auto"),
        )
    with state_lock(root):
        activated = activate_orchestration({**params, "user_command": ACTIVATION_COMMAND})
        if not activated.get("active"):
            raise ValueError(str(activated.get("next_action") or "orchestration activation failed"))
        _checkpoint_orchestrate_transaction(transaction_path, transaction, "activated", task_id=task_id)
        existing_state = None
        existing_task_dir = None
        try:
            _, existing_task_dir, existing_state = load_state(task_id, params)
            authorize_principal(existing_state, params)
        except (FileNotFoundError, ValueError):
            existing_state = None
            existing_task_dir = None
        if existing_state is None:
            classification_id = str(transaction.get("context", {}).get("classification_id") or "")
            # Checkpoints are durable only after the encompassing state
            # transaction commits.  If an exception rolled that transaction
            # back, a receipt can still retain the in-memory classification
            # identifier.  Never reuse that dangling reference on retry.
            if classification_id and db_get_classification(root, classification_id) is None:
                classification_id = ""
            if not classification_id:
                classified = classify_task({
                    **params,
                    "complexity": classification_preview["complexity"],
                    "requirements": task.get("requirements", []),
                    "pipeline": classification_preview["pipeline"],
                    "parallel_groups": classification_preview["parallel_groups"],
                })
                classification_id = classified["classification_id"]
                _checkpoint_orchestrate_transaction(transaction_path, transaction, "classified", classification_id=classification_id)
            created = init_task({
                **params,
                **task,
                "task_id": task_id,
                "objective": objective,
                "classification_id": classification_id,
            })
            state = created["state"]
            _, task_dir, _ = task_paths(task_id, params)
            _checkpoint_orchestrate_transaction(transaction_path, transaction, "initialized", task_directory=task_dir.name)
        else:
            state = existing_state
            task_dir = existing_task_dir
            stored_task = load_task_definition(task_dir, state)
            if stored_task.get("objective") != redact(objective):
                raise ValueError("existing task_id belongs to a different objective")
        plan = {
            "schema": ORCHESTRATION_PLAN_SCHEMA,
            "task_id": task_id,
            "pipeline_contract_version": int(state.get("pipeline_contract_version") or 1),
            "semantic_pipeline_version": 1,
            "history": [],
            "waves": waves,
            "host_capabilities": sanitize_structured(host_capabilities),
            "classification": classification_preview,
            "created_at": now(),
        }
        existing_plan = db_load_task(_ledger_root_for_artifact(task_dir), task_id)
        existing_plan = existing_plan[2] if existing_plan is not None else None
        if existing_plan is not None:
            if digest_text(json.dumps(_orchestrate_wave_contract(existing_plan.get("waves")), sort_keys=True)) != digest_text(json.dumps(_orchestrate_wave_contract(waves), sort_keys=True)):
                raise ValueError("existing task has a different orchestration wave plan")
            plan = existing_plan
        else:
            _write_orchestrate_plan(task_dir, plan)
        _checkpoint_orchestrate_transaction(transaction_path, transaction, "plan_recorded")
        prepared = _prepare_orchestrate_wave(params, task_dir, state, plan)
        _checkpoint_orchestrate_transaction(transaction_path, transaction, "wave_prepared", wave_id=prepared["wave_id"], attempt_ids=prepared["attempt_ids"])
        return _orchestrate_response(
            "start",
            prepared["state"],
            wave_id=prepared["wave_id"],
            spawn_requests=prepared["spawn_requests"],
            plan=plan,
        )


def _report_receipt_for_attempt(task_dir: Path, state: dict[str, Any], attempt_id: str) -> dict[str, Any] | None:
    paths = report_bus_paths(task_dir)
    reports = [
        item for item in _report_index(paths, state["task_id"]).get("reports", [])
        if item.get("attempt_id") == attempt_id
    ]
    if not reports:
        return None
    report_id = safe_id(str(reports[-1]["report_id"]))
    try:
        receipt, _ = read_immutable_json_artifact(
            task_dir,
            state["task_id"],
            f"reports/receipts/report-receipt-{report_id}.json",
            kinds={"report_receipt"},
        )
    except ValueError:
        return None
    return receipt


def _pipeline_contract_version(state: dict[str, Any]) -> int:
    """Treat active tasks created before the additive field as pipeline v1."""
    return int(state.get("pipeline_contract_version") or 1)


def _semantic_future_pipeline(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Project approval-relevant semantics after the most recent plan wave.

    Wave completion is execution progress, not a semantic pipeline change.
    Keeping already-executed post-plan waves in this projection makes the
    approval digest stable while the approved pipeline advances normally.
    """
    semantic: list[dict[str, Any]] = []
    waves = [wave for wave in plan.get("waves", []) if isinstance(wave, dict)]
    plan_positions = [
        index for index, wave in enumerate(waves)
        if "plan" in wave.get("gates", [])
    ]
    selected = waves[plan_positions[-1] + 1:] if plan_positions else waves
    for wave in selected:
        if not isinstance(wave, dict):
            continue
        workers = []
        for spec in wave.get("delegations", []):
            if not isinstance(spec, dict):
                continue
            dependencies: object = (
                list(spec.get("context_gates") or [])
                if "context_gates" in spec else "all_verified_predecessors"
            )
            workers.append({
                "phase": spec.get("gate"),
                "profile": spec.get("agent"),
                "objective": str(spec.get("objective") or ""),
                "strategy": str(spec.get("strategy") or "default"),
                "paths": list(spec.get("allowed_paths") or []),
                "dependencies": dependencies,
                "context_files": list(spec.get("context_files") or []),
                "acceptance_criteria": list(spec.get("acceptance_criteria") or []),
                "verification": list(spec.get("verification") or []),
            })
        if workers:
            semantic.append({"workers": workers})
    return semantic


def _semantic_future_pipeline_digest(plan: dict[str, Any]) -> str:
    return digest_text(json.dumps(
        _semantic_future_pipeline(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ))


def _verified_plan_predecessor_basis(
    task_dir: Path,
    state: dict[str, Any],
) -> tuple[list[dict[str, str]], str]:
    pipeline = list(state.get("current_pipeline") or [])
    plan_index = pipeline.index("plan") if "plan" in pipeline else len(pipeline)
    required_gates = {
        gate for gate in ("scope", "discover", "architecture", "database_architecture", "ux")
        if gate in pipeline and pipeline.index(gate) < plan_index
    }
    report_refs = _predecessor_context_report_ids(state, required_gates)
    basis: list[dict[str, str]] = []
    for report_ref in report_refs:
        record, _ = read_immutable_json_artifact(
            task_dir,
            state["task_id"],
            f"reports/records/{safe_id(str(report_ref))}.json",
            kinds={"worker_report", "report_record"},
        )
        basis.append({
            "phase": str(record.get("gate") or ""),
            "report_ref": safe_id(str(record.get("report_id") or "")),
            "content_digest": str(record.get("content_digest") or ""),
        })
    digest = digest_text(json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return basis, digest


def _current_plan_basis(
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    report_ref: str,
) -> dict[str, Any]:
    manifest = current_planning_manifest(task_dir)
    if not isinstance(manifest, dict) or manifest.get("source_report_ref") != report_ref:
        raise ValueError("plan approval requires the current planning revision from the final planner report")
    predecessor_reports, predecessor_digest = _verified_plan_predecessor_basis(task_dir, state)
    return {
        "pipeline_contract_version": _pipeline_contract_version(state),
        "plan_revision": str(manifest.get("revision") or ""),
        "plan_report_ref": report_ref,
        "verified_predecessor_reports": predecessor_reports,
        "verified_predecessor_digest": predecessor_digest,
        "semantic_pipeline_version": int(plan.get("semantic_pipeline_version") or 1),
        "semantic_future_pipeline_digest": _semantic_future_pipeline_digest(plan),
    }


def _assert_approved_plan_fresh(
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> None:
    if _pipeline_contract_version(state) < 2:
        return
    approval = _plan_approval(state)
    if approval.get("policy") != "required" or "plan" not in state.get("completed_gates", []):
        return
    current_gates = set(active_gates(state))
    if not current_gates or current_gates <= {"scope", "discover", "architecture", "database_architecture", "ux", "plan"}:
        return
    if approval.get("status") != "approved":
        raise PlanReapprovalRequired("post-plan work requires an explicitly approved current plan revision")
    report_ref = safe_id(str(approval.get("plan_report_ref") or ""))
    current = _current_plan_basis(task_dir, state, plan, report_ref=report_ref)
    approved = approval.get("approved_basis") if isinstance(approval.get("approved_basis"), dict) else {}
    keys = (
        "plan_revision", "plan_report_ref", "verified_predecessor_digest",
        "semantic_pipeline_version", "semantic_future_pipeline_digest",
    )
    mismatches = [key for key in keys if approved.get(key) != current.get(key)]
    if mismatches:
        raise PlanReapprovalRequired(
            "approved plan basis is stale for: " + ", ".join(mismatches)
            + "; rework the plan and obtain explicit approval before dispatch"
        )


def _plan_review_payload(task_dir: Path, state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded coordinator-facing summary of the completed plan."""
    planner_attempts = [
        item for item in state.get("attempts", [])
        if item.get("gate") == "plan" and item.get("status") == "passed" and not item.get("invalidated")
    ]
    if not planner_attempts:
        raise ValueError("plan approval requires a passed planner report")
    planner_attempt = planner_attempts[-1]
    report_refs = [safe_id(str(item)) for item in planner_attempt.get("report_ids", []) if str(item).strip()]
    if not report_refs:
        report_refs = [
            safe_id(str(item.get("report_id")))
            for item in _report_index(report_bus_paths(task_dir), state["task_id"]).get("reports", [])
            if item.get("attempt_id") == planner_attempt["attempt_id"] and str(item.get("report_id") or "").strip()
        ]
    if not report_refs:
        raise ValueError("plan approval requires a persisted planner report")
    report_ref = report_refs[-1]
    record, _ = _pre_recorded_report(task_dir, state, planner_attempt["attempt_id"], report_ref)
    report = sanitize_report_payload(record.get("report"))
    manifest = current_planning_manifest(task_dir)
    artifact_summary = None
    if manifest and manifest.get("source_report_ref") == report_ref:
        artifact_summary = {
            "manifest_ref": "sqlite:task_documents/planning_current",
            # Active tasks created by the immediately preceding release retain
            # their already-authorized legacy overview projection. New plans
            # always persist an explicit immutable revision-scoped path.
            "overview_path": manifest.get("overview_artifact_path") or "planning/overview.md",
            "revision": manifest.get("revision"),
            "work_packages": [
                {
                    "id": package.get("id"), "title": package.get("title"),
                    "depends_on": package.get("depends_on", []),
                    "microtask_count": package.get("microtask_count", 0),
                    "artifact_path": package.get("artifact_path"),
                }
                for package in manifest.get("work_packages", [])[:MAX_WORK_PACKAGES]
                if isinstance(package, dict)
            ],
        }
    basis = _current_plan_basis(task_dir, state, plan, report_ref=report_ref)
    return {
        "report_ref": report_ref,
        **basis,
        # This payload is persisted while the orchestration transaction is
        # held.  Keep it wholly canonical: a Markdown projection is optional
        # filesystem output and is resolved only once the transaction commits.
        "report_phase": planner_attempt.get("gate", "plan"),
        "summary": redact(report["summary"], 2400),
        "findings": [redact(item, 1000) for item in report.get("findings", [])][:12],
        "uncertainty": [redact(item, 1000) for item in report.get("uncertainty", [])][:12],
        "remaining_phases": list(active_gates(state)),
        **({"planning_artifacts": artifact_summary} if artifact_summary else {}),
    }


def _materialize_response_report_links(params: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """Add Desktop Markdown links only after the business transaction commits."""
    result = response.get("result")
    review = result.get("plan_review") if isinstance(result, dict) else None
    if not isinstance(review, dict) or "report_markdown_path" in review:
        return response
    report_ref = safe_id(str(review.get("report_ref") or ""))
    if not report_ref:
        return response
    task_id = safe_id(str(response.get("task_id") or params.get("task_id") or ""))
    if not task_id:
        return response
    _, task_dir, state = load_state(task_id, params)
    from cortex_runtime.reports import ensure_report_markdown_path

    markdown_path = ensure_report_markdown_path(task_dir, state, report_ref)
    review["report_markdown_path"] = str(markdown_path)
    review["report_markdown_link"] = report_markdown_link(
        task_dir,
        report_ref,
        review.get("report_phase", "plan"),
    )
    return response


def _hold_for_plan_approval(task_dir: Path, state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
    """Persist the post-plan human gate before any successor is prepared."""
    approval = _plan_approval(state)
    if (
        approval.get("policy") != "required"
        or "plan" not in state.get("completed_gates", [])
        or not active_gates(state)
        or state.get("status") != "active"
    ):
        return None
    if approval.get("status") == "approved":
        return None
    if approval.get("status") == "awaiting_user":
        if not str(approval.get("request_id") or "").strip():
            approval["request_id"] = _plan_approval_request_id(state, approval)
            state["plan_approval"] = approval
            save_state(task_dir, task_dir / "state.sqlite", state, "plan_approval_request", "bound request id to the pending plan approval")
        return dict(approval.get("review") or {})
    review = _plan_review_payload(task_dir, state, plan)
    history = approval.setdefault("history", [])
    history.append({"event": "requested", "at": now(), "plan_review": dict(review)})
    approval.update({
        "policy": "required",
        "status": "awaiting_user",
        "review": review,
        "plan_report_ref": review["report_ref"],
        "pending_basis": {key: review[key] for key in (
            "pipeline_contract_version", "plan_revision", "plan_report_ref",
            "verified_predecessor_digest", "semantic_pipeline_version",
            "semantic_future_pipeline_digest",
        )},
        "requested_at": now(),
    })
    approval["request_id"] = _plan_approval_request_id(state, approval)
    state["plan_approval"] = approval
    save_state(task_dir, task_dir / "state.sqlite", state, "plan_approval", "awaiting explicit user approval of the completed plan")
    return review


def _pre_recorded_report(
    task_dir: Path,
    state: dict[str, Any],
    attempt_id: str,
    report_ref: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report_id = safe_id(str(report_ref or ""))
    if not report_id:
        raise ValueError("passed completion requires report_ref")
    record, _ = read_immutable_json_artifact(
        task_dir,
        state["task_id"],
        f"reports/records/{report_id}.json",
        kinds={"worker_report", "report_record"},
    )
    if (
        record.get("schema") != REPORT_SCHEMA
        or record.get("task_id") != state.get("task_id")
        or record.get("attempt_id") != attempt_id
    ):
        raise ValueError("report_ref does not belong to the active worker attempt")
    sanitize_report_payload(record.get("report"))
    receipt, _ = read_immutable_json_artifact(
        task_dir,
        state["task_id"],
        f"reports/receipts/report-receipt-{report_id}.json",
        kinds={"report_receipt"},
    )
    if (
        receipt.get("schema") != REPORT_SCHEMA
        or receipt.get("report_id") != report_id
        or receipt.get("task_id") != state.get("task_id")
        or receipt.get("attempt_id") != attempt_id
        or receipt.get("invalidated")
    ):
        raise ValueError("report_ref receipt is invalid for the active worker attempt")
    return record, receipt


def _validate_retry_strategy(
    state: dict[str, Any],
    attempt: dict[str, Any],
    completion: dict[str, Any],
) -> None:
    if str(completion.get("status", "passed")).strip().lower() != "failed":
        return
    attempt_id = str(attempt.get("attempt_id") or "")
    current_strategy = str(attempt.get("strategy") or "default").strip()
    same_strategy_failures = 1 + sum(
        1
        for prior in state.get("attempts", [])
        if prior.get("attempt_id") != attempt_id
        and prior.get("gate") == attempt.get("gate")
        and prior.get("status") == "failed"
        and str(prior.get("strategy") or "default").strip().casefold() == current_strategy.casefold()
    )
    phase_failure_number = int(
        state.get("orchestrate_gate_failure_counts", {}).get(attempt.get("gate"), 0)
    ) + 1
    if (
        same_strategy_failures >= MAX_SAME_STRATEGY_FAILURES
        and phase_failure_number < MAX_ORCHESTRATE_GATE_FAILURES
        and not completion.get("pipeline_replanned")
    ):
        next_strategy = str(completion.get("next_strategy") or "").strip()
        if not next_strategy:
            raise ValueError(
                "same_strategy_limit reached after two failed attempts; provide a materially different "
                "next_strategy or replan future waves before the third phase attempt"
            )
        if next_strategy.casefold() == current_strategy.casefold():
            raise ValueError("next_strategy must materially differ from the failed strategy")


def _preflight_orchestrate_completion(
    task_dir: Path,
    state: dict[str, Any],
    completion: dict[str, Any],
) -> None:
    """Validate a host completion without mutating the task ledger."""
    attempt_id = safe_id(str(completion.get("attempt_id", "")))
    attempt = _attempt(state, attempt_id)
    requested_status = str(completion.get("status", "passed")).strip().lower()
    if requested_status not in TERMINAL_ATTEMPT_STATUSES:
        raise ValueError("completion status must be passed, failed, blocked, cancelled, or superseded")
    _validate_retry_strategy(state, attempt, completion)
    if attempt.get("status") in TERMINAL_ATTEMPT_STATUSES:
        if attempt.get("status") != requested_status:
            raise ValueError("completion status does not match the terminal ledger attempt")
        return
    open_questions = _open_blocking_questions(task_dir, state, attempt_id)
    if open_questions:
        refs = ", ".join(str(item["question_id"]) for item in open_questions)
        raise ValueError(
            f"attempt has unanswered blocking worker question(s): {refs}; "
            "answer the question and resume the same worker before completion"
        )
    observation_source = str(completion.get("host_observation_source") or "").strip()
    if observation_source != "unattested_parent_result":
        required_host_fields = ("host_tool", "host_agent_id", "host_task_name", "host_model", "host_reasoning_effort")
        missing_host = [field for field in required_host_fields if not str(completion.get(field, "")).strip()]
        if missing_host:
            raise ValueError("completion requires actual host fields: " + ", ".join(missing_host))
        spawn_request = attempt.get("spawn_request") or {}
        expected = {
            "host_tool": spawn_request.get("host_tool") or "spawn_agent",
            "host_task_name": spawn_request.get("task_name") or attempt.get("agent"),
            # `model` is absent for configured-default requests.  The host still
            # reports its effective model and it is checked against the durable
            # expected_model metadata instead.
            "host_model": spawn_request.get("model") or spawn_request.get("expected_model") or attempt.get("expected_model"),
            "host_reasoning_effort": spawn_request.get("reasoning_effort"),
        }
        mismatches = [
            field for field, expected_value in expected.items()
            if expected_value is not None and str(completion.get(field)) != str(expected_value)
        ]
        if mismatches:
            raise ValueError("host completion mismatch for: " + ", ".join(mismatches))
    if requested_status == "passed":
        report_ref = str(completion.get("report_ref") or "").strip()
        if not report_ref:
            raise ValueError("passed completion requires report_ref from record_report")
        record, _ = _pre_recorded_report(task_dir, state, attempt_id, report_ref)
        _validate_report_decision_closure(task_dir, state, attempt, record["report"])
    elif not str(completion.get("reason", "")).strip():
        raise ValueError("non-success completion requires an explicit reason")


def _apply_next_retry_strategies(
    wave: dict[str, Any],
    state: dict[str, Any],
    completions: list[dict[str, Any]],
) -> None:
    """Carry an explicitly revised strategy into only the matching retry slot."""
    by_key = {
        str(spec.get("orchestration_delegation_key") or ""): spec
        for spec in wave.get("delegations", [])
        if isinstance(spec, dict)
    }
    for completion in completions:
        next_strategy = str(completion.get("next_strategy") or "").strip()
        if not next_strategy:
            continue
        attempt = _attempt(state, safe_id(str(completion.get("attempt_id", ""))))
        key = str(attempt.get("orchestration_delegation_key") or "")
        spec = by_key.get(key)
        if spec is None:
            raise ValueError("next_strategy cannot identify the matching retry slot")
        spec["strategy"] = next_strategy


def _auto_handoff(params: dict[str, Any], task_dir: Path, state: dict[str, Any], next_action: str) -> dict[str, Any]:
    baseline = task_manifest_baseline(task_dir, state)
    current = capture_project_manifest(Path(baseline["project_root"]), policy=baseline.get("policy"))
    comparison = compare_manifests(baseline, current)
    completed = [
        f"{gate}: {state.get('gates', {}).get(gate, {}).get('summary') or state.get('gates', {}).get(gate, {}).get('outcome', 'completed')}"
        for gate in state.get("completed_gates", [])
    ] or [f"Prepared handoff for {primary_gate(state)}"]
    # A v3 caller has no task principal: it owns only the opaque task_ref.
    # Reconstruct the durable task identity here instead of forwarding a
    # coordinator/session alias into the authorization boundary.  Resolve the
    # public handoff seam at call time so host integrations (and compatibility
    # tests) can replace that facade adapter without re-importing this engine.
    return bound_symbol("orchestration_engine", "handoff")({
        **params,
        "task_id": state["task_id"],
        "principal": state.get("principal"),
        "thread_id": state.get("thread_id"),
        "expected_revision": state["revision"],
        "name": f"orchestrate-{primary_gate(state)}-{state['revision'] + 1}",
        "completed": completed,
        "files": comparison["changed_paths"],
        "decisions": ["Unified orchestrate facade reconciled the current wave."],
        "risks": [],
        "next_action": next_action,
    })


def _complete_orchestrate_attempt(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    completion: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    attempt_id = safe_id(str(completion.get("attempt_id", "")))
    attempt = _attempt(state, attempt_id)
    requested_status = str(completion.get("status", "passed")).strip().lower()
    if requested_status not in TERMINAL_ATTEMPT_STATUSES:
        raise ValueError("completion status must be passed, failed, blocked, cancelled, or superseded")
    if attempt.get("status") in TERMINAL_ATTEMPT_STATUSES:
        if attempt.get("status") != requested_status:
            raise ValueError("completion status does not match the terminal ledger attempt")
        return state, _report_receipt_for_attempt(task_dir, state, attempt_id)
    report_ref = str(completion.get("report_ref") or "").strip()
    if requested_status == "passed" and report_ref:
        record, receipt = _pre_recorded_report(task_dir, state, attempt_id, report_ref)
        if attempt.get("status") == AWAITING_HOST_SPAWN:
            attempt["status"] = "running"
            attempt["dispatch_correlation"] = "worker_report_received"
            attempt["expected_route"] = {
                "tool": (attempt.get("spawn_request") or {}).get("host_tool") or "spawn_agent",
                "model": (attempt.get("spawn_request") or {}).get("model"),
                "expected_model": (attempt.get("spawn_request") or {}).get("expected_model") or attempt.get("expected_model"),
                "reasoning_effort": (attempt.get("spawn_request") or {}).get("reasoning_effort"),
            }
        attempt.setdefault("report_ids", [])
        if record["report_id"] not in attempt["report_ids"]:
            attempt["report_ids"].append(record["report_id"])
        package = _delegation_package(task_dir, state["task_id"], attempt_id)
        package["spawn_status"] = "worker_report_received"
        package["dispatch_correlation"] = "worker_report_received"
        package["report_ref"] = record["report_id"]
        _write_delegation_package(task_dir, state["task_id"], attempt_id, package)
        save_state(task_dir, task_dir / "state.sqlite", state, "worker_report", attempt_id)
        finalized = finalize_attempt({
            **params,
            "task_id": state["task_id"],
            "attempt_id": attempt_id,
            "expected_revision": state["revision"],
            "status": "passed",
        })
        if finalized.get("recorded") is False:
            raise ValueError(str(finalized.get("reason") or "worker report attempt finalization failed"))
        return finalized["state"], receipt
    observation_source = str(completion.get("host_observation_source") or "").strip()
    completion_fields = dict(completion)
    if observation_source == "unattested_parent_result" and attempt.get("status") == AWAITING_HOST_SPAWN:
        # V3 deliberately does not ask Luna to echo host metadata.  A returned
        # parent result proves that the dispatch ran, but it is not independent
        # evidence of the effective model or reasoning effort.
        attempt["status"] = "running"
        attempt["dispatch_correlation"] = observation_source
        attempt["expected_route"] = {
            "tool": (attempt.get("spawn_request") or {}).get("host_tool") or "spawn_agent",
            "model": (attempt.get("spawn_request") or {}).get("model"),
            "expected_model": (attempt.get("spawn_request") or {}).get("expected_model") or attempt.get("expected_model"),
            "reasoning_effort": (attempt.get("spawn_request") or {}).get("reasoning_effort"),
        }
        package = _delegation_package(task_dir, state["task_id"], attempt_id)
        package["spawn_status"] = "parent_result_received"
        package["dispatch_correlation"] = observation_source
        package["expected_route"] = attempt["expected_route"]
        _write_delegation_package(task_dir, state["task_id"], attempt_id, package)
        save_state(task_dir, task_dir / "state.sqlite", state, "parent_result", attempt_id)
        for field in ("host_tool", "host_agent_id", "host_task_name", "host_model", "host_reasoning_effort"):
            completion_fields.pop(field, None)
    if requested_status == "passed":
        raise ValueError("passed completion requires report_ref from record_report")
    finalized = finalize_attempt({
        **params,
        **completion_fields,
        "task_id": state["task_id"],
        "attempt_id": attempt_id,
        "status": requested_status,
        "reason": str(completion.get("reason") or "host adapter reported terminal non-success"),
    })
    if finalized.get("recorded") is False:
        raise ValueError(str(finalized.get("reason") or "attempt finalization failed"))
    return finalized["state"], None


def _ensure_attempt_evidence(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    attempt: dict[str, Any],
    receipt: dict[str, Any] | None,
    *,
    command: bool = False,
) -> dict[str, Any]:
    existing = next((item for item in state.get("evidence", []) if item.get("attempt_id") == attempt["attempt_id"] and not item.get("invalidated")), None)
    if existing is not None:
        return state
    evidence_params = {
        **params,
        "task_id": state["task_id"],
        "expected_revision": state["revision"],
        "gate": attempt["gate"],
        "attempt_id": attempt["attempt_id"],
        "report_receipt": receipt.get("receipt_id") if receipt else None,
        "summary": f"Unified facade accepted the {attempt['gate']} report from {attempt['agent']}",
        "paths": [],
    }
    if attempt["gate"] == "documentation":
        report_record, _ = (
            read_immutable_json_artifact(
                task_dir,
                state["task_id"],
                f"reports/records/{safe_id(str(receipt['report_id']))}.json",
                kinds={"worker_report", "report_record"},
            )
            if receipt else ({"report": {}}, {})
        )
        changed_files = report_record.get("report", {}).get("changed_files", [])
        evidence_params.update({
            "kind": "documentation",
            "decision": "updated" if changed_files else "not_applicable",
            "justification": "The documentation worker reported no changed documentation files." if not changed_files else "The documentation worker reported updated files.",
            "paths": changed_files,
        })
        result = record_evidence(evidence_params)
    elif command:
        result = execute_verification({**evidence_params, "verification_id": "benign_success"})
    else:
        result = record_evidence({**evidence_params, "kind": "report"})
    if result.get("recorded") is False:
        raise ValueError(str(result.get("reason") or "attempt evidence was not recorded"))
    return result["state"]


def _canonical_gate_decision(task_dir: Path, state: dict[str, Any], gate: str) -> str | None:
    """Read the strongest canonical decision published by passed gate attempts."""
    priority = {"pass": 0, "rework": 1, "fail": 2, "blocked": 3}
    decisions: list[str] = []
    for attempt in state.get("attempts", []):
        if attempt.get("gate") != gate or attempt.get("invalidated") or attempt.get("status") != "passed":
            continue
        for report_ref in attempt.get("report_ids", []):
            record, _ = read_immutable_json_artifact(
                task_dir,
                state["task_id"],
                f"reports/records/{safe_id(str(report_ref))}.json",
                kinds={"worker_report", "report_record"},
            )
            envelope = record.get("gate_result") or record.get("closure")
            if not isinstance(envelope, dict):
                continue
            decision = str(envelope.get("decision") or "").strip().lower()
            if decision in priority:
                decisions.append(decision)
    return max(decisions, key=priority.__getitem__) if decisions else None


def _replace_future_orchestrate_waves(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    raw_future: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    task = load_task_definition(task_dir, state)
    host_capabilities = plan.get("host_capabilities") or {}
    future, classification = _normalize_orchestrate_waves(raw_future, task, host_capabilities, str(params["project_root"]))
    old_semantic_pipeline = _semantic_future_pipeline(plan)
    old_semantic_digest = _semantic_future_pipeline_digest(plan)
    candidate_plan = {**plan, "waves": [
        *[wave for wave in plan.get("waves", []) if wave.get("status") == "completed"],
        *future,
    ]}
    new_semantic_digest = _semantic_future_pipeline_digest(candidate_plan)
    semantic_changed = old_semantic_digest != new_semantic_digest
    approval_before = _plan_approval(state)
    invalidate_approval = (
        _pipeline_contract_version(state) >= 2
        and semantic_changed
        and approval_before.get("status") in {"awaiting_user", "approved"}
    )
    if invalidate_approval:
        first_future = future[0] if future else {}
        singleton_plan = (
            first_future.get("gates") == ["plan"]
            and len(first_future.get("delegations", [])) == 1
            and first_future["delegations"][0].get("agent") == "planner"
        )
        if not params.get("allow_rework", False) or not singleton_plan:
            raise PlanReapprovalRequired(
                "a material approved-future change requires rework=true and a singleton Planner plan wave before affected work"
            )
    completed_set = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    requested_future_gates = {gate for wave in future for gate in wave["gates"]}
    rework_gates = sorted(completed_set & requested_future_gates)
    if rework_gates and not params.get("allow_rework", False):
        raise ValueError("future_waves cannot reintroduce completed gates without allow_rework=true")
    if rework_gates:
        revised = update_pipeline({
            **params,
            "task_id": state["task_id"],
            "expected_revision": state["revision"],
            "operations": [{"op": "rework", "gate": gate} for gate in rework_gates],
            "allow_rework": True,
            "reason": "Unified facade explicitly reintroduced completed gates in future_waves.",
        })
        state = revised["state"]
        completed_set = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    completed_waves = [wave for wave in plan.get("waves", []) if set(wave.get("gates", [])).issubset(completed_set)]
    relative_v3 = any(
        item.get("host_observation_source") == "unattested_parent_result"
        for item in params.get("completions", [])
        if isinstance(item, dict)
    )
    if relative_v3:
        for index, wave in enumerate(future, len(completed_waves) + 1):
            wave["wave_id"] = f"wave-{index:02d}"
            for delegation_index, delegation in enumerate(wave.get("delegations", []), 1):
                delegation["orchestration_wave_id"] = wave["wave_id"]
                delegation["orchestration_delegation_key"] = (
                    f"{wave['wave_id']}-{delegation['gate']}-{delegation_index:02d}"
                )
    full_pipeline = [gate for gate in state["current_pipeline"] if gate in completed_set]
    for gate in classification["pipeline"]:
        if gate not in full_pipeline:
            full_pipeline.append(gate)
    full_groups = [[gate] for gate in full_pipeline if gate in completed_set] + [wave["gates"] for wave in future]
    normalized_current_groups = normalize_parallel_groups(state.get("parallel_groups"), state["current_pipeline"])
    normalized_future_groups = normalize_parallel_groups(full_groups, full_pipeline)
    pipeline_or_group_change = (
        full_pipeline != state["current_pipeline"]
        or normalized_future_groups != normalized_current_groups
    )
    reassessed = reassess_pipeline({
        **params,
        "task_id": state["task_id"],
        "expected_revision": state["revision"],
        "signals": ["Coordinator replaced the not-yet-started facade waves."],
        "pipeline": full_pipeline,
        "parallel_groups": full_groups,
        "intent": "resequence",
        "decision": "updated" if pipeline_or_group_change else "unchanged",
        "reason": (
            "Unified facade accepted an explicit future_waves replacement."
            if pipeline_or_group_change
            else "Unified facade confirmed that future_waves already match the active pipeline."
        ),
        "allow_rework": bool(params.get("allow_rework", False)),
        "apply": pipeline_or_group_change,
    })
    state = reassessed["state"]
    plan["waves"] = completed_waves + future
    if semantic_changed:
        previous_version = int(plan.get("semantic_pipeline_version") or 1)
        plan.setdefault("history", []).append({
            "event": "semantic_pipeline_replaced",
            "at": now(),
            "reason": redact(params.get("reason") or "Coordinator replaced future waves.", 2000),
            "semantic_pipeline_version": previous_version,
            "semantic_future_pipeline_digest": old_semantic_digest,
            "semantic_future_pipeline": old_semantic_pipeline,
            **({"approval": json.loads(json.dumps(approval_before))} if invalidate_approval else {}),
        })
        plan["semantic_pipeline_version"] = previous_version + 1
    if invalidate_approval:
        approval = _plan_approval(state)
        approval.setdefault("history", []).append({
            "event": "material_pipeline_change",
            "at": now(),
            "reason": redact(params.get("reason") or "Coordinator recorded a material future-wave change.", 2000),
            "previous_plan_review": dict(approval_before.get("review") or {}),
            "previous_approved_basis": dict(approval_before.get("approved_basis") or {}),
        })
        for key in (
            "review", "plan_report_ref", "pending_basis", "approved_basis",
            "requested_at", "approved_at",
        ):
            approval.pop(key, None)
        approval.update({"policy": "required", "status": "pending_plan", "feedback": None})
        state["plan_approval"] = approval
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "plan_approval",
            "material future-wave change requires a replacement plan and approval",
        )
    _write_orchestrate_plan(task_dir, plan)
    return state, plan


def _orchestrate_advance(params: dict[str, Any], transaction_path: Path, transaction: dict[str, Any]) -> dict[str, Any]:
    task_id = safe_id(str(params.get("task_id", "")))
    completions = params.get("completions")
    if not isinstance(completions, list) or not completions:
        raise ValueError("advance requires a non-empty completions array")
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(task_id, params)
        authorize(state, params)
        plan = _load_orchestrate_plan(task_dir, state)
        current_wave = _wave_for_gates(plan, active_gates(state))
        requested_wave_id = safe_id(str(params.get("wave_id", "")))
        if current_wave is None or current_wave.get("wave_id") != requested_wave_id:
            prior_wave = next((wave for wave in plan.get("waves", []) if wave.get("wave_id") == requested_wave_id), None)
            transaction_phase = str(transaction.get("phase", ""))
            if (
                prior_wave is None
                or prior_wave.get("status") not in {"completed", "blocked"}
                or transaction_phase not in {"gates_recorded", "next_wave_prepared"}
            ):
                raise ValueError("advance wave_id does not match the active Cortex wave")
            # The prior call crossed the gate boundary but crashed before its
            # transaction receipt was committed. Continue only the remaining
            # post-gate phases; never replay its completions into the new wave.
            if transaction_phase == "gates_recorded" and params.get("future_waves") is not None and state.get("status") == "active":
                state, plan = _replace_future_orchestrate_waves(params, task_dir, state, plan, params["future_waves"])
            if state.get("status") == "completed":
                audited = close_audit({**params, "task_id": task_id})
                return _orchestrate_response("advance", audited["state"], wave_id=requested_wave_id, result={"report_count": audited["report_count"]}, plan=plan)
            if state.get("status") == "blocked":
                return _orchestrate_response("advance", state, wave_id=requested_wave_id, plan=plan)
            review = _hold_for_plan_approval(task_dir, state, plan)
            if review is not None:
                return _orchestrate_response(
                    "advance", state, wave_id=requested_wave_id,
                    result={"plan_review": review}, plan=plan,
                )
            prepared = _prepare_orchestrate_wave(params, task_dir, state, plan)
            _checkpoint_orchestrate_transaction(transaction_path, transaction, "next_wave_prepared", wave_id=prepared["wave_id"], attempt_ids=prepared["attempt_ids"])
            return _orchestrate_response(
                "advance",
                prepared["state"],
                wave_id=prepared["wave_id"],
                spawn_requests=prepared["spawn_requests"],
                plan=plan,
            )
        expected_attempt_ids = set(current_wave.get("attempt_ids") or [
            item["attempt_id"] for item in state.get("attempts", [])
            if item.get("gate") in current_wave["gates"] and not item.get("invalidated")
        ])
        provided_attempt_ids = {safe_id(str(item.get("attempt_id", ""))) for item in completions if isinstance(item, dict)}
        if len(provided_attempt_ids) != len(completions):
            raise ValueError("advance completion attempt_ids must be unique")
        unexpected = sorted(provided_attempt_ids - expected_attempt_ids)
        if unexpected:
            raise ValueError("advance contains attempts outside the active wave: " + ", ".join(unexpected))
        missing = sorted(expected_attempt_ids - provided_attempt_ids - {
            item["attempt_id"] for item in state.get("attempts", []) if item.get("status") in TERMINAL_ATTEMPT_STATUSES
        })
        if missing:
            raise ValueError("advance is missing completions for: " + ", ".join(missing))
        for completion in completions:
            if not isinstance(completion, dict):
                raise ValueError("completion entries must be objects")
            if params.get("future_waves") is not None:
                completion["pipeline_replanned"] = True
            _preflight_orchestrate_completion(task_dir, state, completion)
        if params.get("future_waves") is not None:
            task = load_task_definition(task_dir, state)
            future_preview, _ = _normalize_orchestrate_waves(
                params["future_waves"], task, plan.get("host_capabilities") or {}, str(params["project_root"])
            )
            prospective_completed = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", [])) | set(current_wave["gates"])
            reintroduced = sorted(prospective_completed & {gate for wave in future_preview for gate in wave["gates"]})
            if reintroduced and not params.get("allow_rework", False):
                raise ValueError("future_waves cannot reintroduce completed gates without allow_rework=true")
        receipts: dict[str, dict[str, Any] | None] = {}
        for completion in completions:
            if not isinstance(completion, dict):
                raise ValueError("completion entries must be objects")
            state, receipt = _complete_orchestrate_attempt(params, task_dir, state, completion)
            receipts[safe_id(str(completion["attempt_id"]))] = receipt
        _apply_next_retry_strategies(current_wave, state, completions)
        _checkpoint_orchestrate_transaction(transaction_path, transaction, "attempts_completed", attempt_ids=sorted(provided_attempt_ids))
        if state.get("require_delegation") and not state.get("reassessment_receipts") and "close" in current_wave["gates"]:
            reassessed = reassess_pipeline({
                **params,
                "task_id": task_id,
                "expected_revision": state["revision"],
                "signals": ["Unified facade reached the close wave without a material pipeline change."],
                "intent": "resequence",
                "decision": "unchanged",
                "reason": "No pipeline change was required before close.",
                "apply": False,
            })
            state = reassessed["state"]
        gate_outcomes = params.get("gate_outcomes") if isinstance(params.get("gate_outcomes"), dict) else {}
        for gate in list(current_wave["gates"]):
            if gate in state.get("completed_gates", []) or gate in state.get("skipped_gates", []):
                continue
            gate_attempts = [item for item in state.get("attempts", []) if item.get("gate") == gate and not item.get("invalidated")]
            statuses = {item.get("status") for item in gate_attempts}
            default_outcome = "blocked" if "blocked" in statuses else "failed" if statuses & {"failed", "cancelled", "superseded"} else "passed"
            gate_decision = _canonical_gate_decision(task_dir, state, gate)
            unresolved_rework = _unresolved_rework_findings(root, state, gate)
            if gate_decision == "blocked":
                default_outcome = "blocked"
            elif gate_decision == "fail":
                default_outcome = "failed"
            elif gate_decision == "rework":
                # record_gate consults canonical blockers and performs the
                # fail-back transition instead of completing this gate.
                default_outcome = "passed"
            elif unresolved_rework:
                # A corrective worker cannot advance the pipeline merely by
                # completing unrelated work.  Treat an unclosed inherited
                # finding as a failed attempt; normal bounded retry handling
                # will either dispatch another corrective worker or block.
                default_outcome = "failed"
            outcome = str(gate_outcomes.get(gate, default_outcome))
            failure_counts = state.setdefault("orchestrate_gate_failure_counts", {})
            failure_count_changed = False
            if outcome == "failed":
                failure_count = int(failure_counts.get(gate, 0)) + 1
                failure_counts[gate] = failure_count
                failure_count_changed = True
                if failure_count >= MAX_ORCHESTRATE_GATE_FAILURES:
                    outcome = "blocked"
                    state["blocked_reason"] = (
                        f"automatic {gate} rework budget exhausted after {failure_count} failed attempts"
                    )
            elif outcome == "passed":
                failure_count_changed = failure_counts.pop(gate, None) is not None
            if failure_count_changed:
                save_state(
                    task_dir,
                    task_dir / "state.sqlite",
                    state,
                    "orchestrate_gate_recovery",
                    f"{gate}: automatic failure count {failure_counts.get(gate, 0)}",
                )
            if outcome == "passed":
                passed = [item for item in gate_attempts if item.get("status") == "passed"]
                for index, attempt in enumerate(passed):
                    receipt = receipts.get(attempt["attempt_id"]) or _report_receipt_for_attempt(task_dir, state, attempt["attempt_id"])
                    state = _ensure_attempt_evidence(
                        params,
                        task_dir,
                        state,
                        attempt,
                        receipt,
                        command=gate == "close" and index == 0,
                    )
            if outcome in {"blocked"} or (outcome == "passed" and gate == "close" and state.get("require_handoff")):
                handed = _auto_handoff(params, task_dir, state, "Resume after resolving the blocker." if outcome == "blocked" else "Close the Cortex task.")
                if handed.get("recorded") is False:
                    raise ValueError("automatic handoff manifest reconciliation failed")
                state = handed["state"]
            gate_summary = f"Unified facade recorded {gate} as {outcome}."
            if unresolved_rework and outcome == "failed":
                gate_summary += " Required corrective findings remain open: " + ", ".join(
                    str(item["fingerprint"]) for item in unresolved_rework
                ) + "."
            if outcome == "blocked" and state.get("blocked_reason"):
                gate_summary += " " + str(state["blocked_reason"])
            recorded = record_gate({
                **params,
                "task_id": task_id,
                "expected_revision": state["revision"],
                "gate": gate,
                "outcome": outcome,
                "summary": gate_summary,
                "enforce_canonical_findings": gate_decision in {"rework", "fail"},
            })
            if recorded.get("recorded") is False:
                raise ValueError(str(recorded.get("reason") or "gate outcome was not recorded"))
            state = recorded["state"]
        current_wave["status"] = "completed" if state.get("status") != "blocked" else "blocked"
        _write_orchestrate_plan(task_dir, plan)
        _checkpoint_orchestrate_transaction(transaction_path, transaction, "gates_recorded", gates=current_wave["gates"])
        # A coordinator may discover a bounded defect in the final close
        # report and explicitly reintroduce documentation/review/close. The
        # close gate transitions the task to completed before this replacement
        # is applied, so accepting future waves only while active silently
        # discarded the authorized rework and produced a false terminal
        # success. update_pipeline(allow_rework=True) intentionally reopens a
        # completed task and invalidates every downstream receipt.
        if params.get("future_waves") is not None and state.get("status") in {"active", "completed"}:
            state, plan = _replace_future_orchestrate_waves(params, task_dir, state, plan, params["future_waves"])
        if state.get("status") == "completed":
            audited = close_audit({**params, "task_id": task_id})
            return _orchestrate_response("advance", audited["state"], wave_id=requested_wave_id, result={"report_count": audited["report_count"]}, plan=plan)
        if state.get("status") == "blocked":
            return _orchestrate_response("advance", state, wave_id=requested_wave_id, plan=plan)
        review = _hold_for_plan_approval(task_dir, state, plan)
        if review is not None:
            return _orchestrate_response(
                "advance", state, wave_id=requested_wave_id,
                result={"plan_review": review}, plan=plan,
            )
        prepared = _prepare_orchestrate_wave(params, task_dir, state, plan)
        _checkpoint_orchestrate_transaction(transaction_path, transaction, "next_wave_prepared", wave_id=prepared["wave_id"], attempt_ids=prepared["attempt_ids"])
        return _orchestrate_response(
            "advance",
            prepared["state"],
            wave_id=prepared["wave_id"],
            spawn_requests=prepared["spawn_requests"],
            plan=plan,
        )


def _orchestrate_plan_approval(params: dict[str, Any]) -> dict[str, Any]:
    """Resolve the explicit user review that follows a completed plan wave."""
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    unknown = sorted(set(payload) - {"decision", "feedback", "request_id"})
    if unknown:
        raise ValueError("unsupported plan_approval payload fields: " + ", ".join(unknown))
    decision_raw = str(payload.get("decision") or "").strip().lower().replace("-", "_").replace(" ", "_")
    decision = {
        "approve": "approve", "approved": "approve", "accept": "approve",
        "cancel": "cancel", "canceled": "cancel", "cancelled": "cancel",
        "revise": "revise", "changes": "revise", "request_changes": "revise",
    }.get(decision_raw)
    if not decision:
        raise ValueError("plan_approval decision must be approve, cancel, or revise")
    feedback = redact(payload.get("feedback", ""), 2000).strip()
    if decision == "revise" and not feedback:
        raise ValueError("plan_approval revise requires non-empty feedback")
    request_id = str(payload.get("request_id") or "").strip()
    if decision in {"approve", "cancel"} and not request_id:
        raise ValueError("plan_approval button response requires request_id")
    if decision == "revise" and request_id:
        raise ValueError("plan_approval revise does not accept request_id")

    task_id = safe_id(str(params.get("task_id", "")))
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(task_id, params)
        authorize(state, params)
        approval = _plan_approval(state)
        if approval.get("policy") != "required":
            raise ValueError("this task does not require post-plan approval")
        if approval.get("status") != "awaiting_user":
            raise ValueError("there is no pending plan approval for this task")
        expected_request_id = str(approval.get("request_id") or _plan_approval_request_id(state, approval))
        if decision in {"approve", "cancel"} and request_id != expected_request_id:
            raise ValueError("plan approval request_id does not match the current pending approval")
        plan = _load_orchestrate_plan(task_dir, state)
        history = approval.setdefault("history", [])
        review = dict(approval.get("review") or {})

        if decision == "cancel":
            return _orchestrate_response(
                "plan_approval",
                state,
                result={"decision": "cancelled", "request_id": request_id, "plan_review": review},
                plan=plan,
            )

        if decision == "approve":
            report_ref = safe_id(str(approval.get("plan_report_ref") or ""))
            current_basis = _current_plan_basis(task_dir, state, plan, report_ref=report_ref)
            pending_basis = approval.get("pending_basis") if isinstance(approval.get("pending_basis"), dict) else {}
            basis_keys = (
                "pipeline_contract_version", "plan_revision", "plan_report_ref",
                "verified_predecessor_digest", "semantic_pipeline_version",
                "semantic_future_pipeline_digest",
            )
            mismatches = [key for key in basis_keys if pending_basis.get(key) != current_basis.get(key)]
            if mismatches:
                raise PlanReapprovalRequired(
                    "plan review basis changed before approval: " + ", ".join(mismatches)
                )
            approval.update({
                "status": "approved",
                "approved_at": now(),
                "feedback": None,
                "approved_basis": current_basis,
                "request_id": expected_request_id,
            })
            history.append({"event": "approved", "at": now(), "plan_review": review, "approved_basis": current_basis})
            state["plan_approval"] = approval
            save_state(task_dir, task_dir / "state.sqlite", state, "plan_approval", "user approved the completed plan")
            prepared = _prepare_orchestrate_wave(params, task_dir, state, plan)
            return _orchestrate_response(
                "plan_approval", prepared["state"], wave_id=prepared["wave_id"],
                spawn_requests=prepared["spawn_requests"],
                result={"decision": "approved", "plan_review": review}, plan=plan,
            )

        revised = update_pipeline({
            **params,
            "task_id": task_id,
            "expected_revision": state["revision"],
            "operations": [{"op": "rework", "gate": "plan"}],
            "allow_rework": True,
            "reason": "User requested changes after reviewing the completed plan.",
        })
        state = revised["state"]
        approval = _plan_approval(state)
        for key in ("review", "plan_report_ref", "pending_basis", "approved_basis", "request_id", "requested_at", "approved_at"):
            approval.pop(key, None)
        approval.update({"policy": "required", "status": "pending_plan", "feedback": feedback})
        history = approval.setdefault("history", [])
        history.append({"event": "revision_requested", "at": now(), "feedback": feedback, "previous_plan_review": review})
        state["plan_approval"] = approval
        for wave in plan.get("waves", []):
            if "plan" in wave.get("gates", []):
                wave["status"] = "pending"
                wave.pop("attempt_ids", None)
        _write_orchestrate_plan(task_dir, plan)
        save_state(task_dir, task_dir / "state.sqlite", state, "plan_approval", "user requested planner revision")
        prepared = _prepare_orchestrate_wave(params, task_dir, state, plan)
        return _orchestrate_response(
            "plan_approval", prepared["state"], wave_id=prepared["wave_id"],
            spawn_requests=prepared["spawn_requests"],
            result={"decision": "revise", "feedback": feedback}, plan=plan,
        )


def _orchestrate_inspect(params: dict[str, Any]) -> dict[str, Any]:
    task_id = safe_id(str(params.get("task_id", "")))
    _, task_dir, state = load_state(task_id, params)
    authorize(state, params)
    task = load_task_definition(task_dir, state)
    plan = _load_orchestrate_plan(task_dir, state)
    current_wave = _wave_for_gates(plan, active_gates(state))
    spawn_requests = [
        {**item["spawn_request"], "attempt_id": item["attempt_id"]}
        for item in state.get("attempts", [])
        if item.get("status") == AWAITING_HOST_SPAWN
        and (current_wave is None or item.get("gate") in current_wave.get("gates", []))
        and not item.get("invalidated")
    ]
    report_index = _report_index(report_bus_paths(task_dir), state["task_id"])
    from cortex_runtime.reports import ensure_report_markdown_path

    available_reports = []
    for item in report_index.get("reports", []):
        if not isinstance(item, dict):
            continue
        report_ref = safe_id(str(item.get("report_id") or ""))
        if not report_ref:
            continue
        report_view = {
            "report_ref": report_ref,
            "phase": item.get("gate"),
            "profile": (item.get("producer") or {}).get("profile"),
            "summary": item.get("summary"),
        }
        try:
            markdown_path = ensure_report_markdown_path(task_dir, state, report_ref)
            report_view.update({
                "report_markdown_path": str(markdown_path),
                "report_markdown_link": report_markdown_link(task_dir, report_ref, item.get("gate", "report")),
            })
        except (OSError, ValueError) as exc:
            # Report content remains canonical in SQLite.  An optional lazy
            # Markdown projection may be temporarily leased or unavailable,
            # but that must not make durable state inspection unrecoverable.
            report_view["projection_error"] = redact(str(exc), 500)
        available_reports.append(report_view)
    available_reports = available_reports[-MAX_CONTEXT_REPORTS:]
    context_handoff = _context_handoff(task_dir, state, task, plan)
    return _orchestrate_response(
        "inspect",
        state,
        wave_id=current_wave.get("wave_id") if current_wave else None,
        spawn_requests=spawn_requests,
        result={
            "plan": [{"wave_id": wave["wave_id"], "gates": wave["gates"], "status": wave.get("status", "pending")} for wave in plan.get("waves", [])],
            "available_reports": available_reports,
            "pending_dispatches": context_handoff["pending_dispatches"],
            "active_workers": context_handoff["active_workers"],
            "stopped_workers": context_handoff["stopped_workers"],
            "context_handoff": context_handoff,
            **(
                {"plan_review": dict(_plan_approval(state).get("review") or {})}
                if _plan_approval_is_pending(state) else {}
            ),
        },
        plan=plan,
    )


def _orchestrate_resume(params: dict[str, Any]) -> dict[str, Any]:
    task_id = safe_id(str(params.get("task_id", "")))
    _, task_dir, state = load_state(task_id, params)
    authorize(state, params)
    resumed = resume_task({
        **params,
        "task_id": task_id,
        "expected_revision": state["revision"],
        "reason": params.get("reason") or "Unified facade resumed the blocked task.",
    })
    resumed_state = resumed["state"]
    failure_counts = resumed_state.setdefault("orchestrate_gate_failure_counts", {})
    for gate in active_gates(resumed_state):
        failure_counts.pop(gate, None)
    invalidated = False
    for attempt in resumed_state.get("attempts", []):
        if attempt.get("gate") in active_gates(resumed_state) and attempt.get("status") == "blocked" and not attempt.get("invalidated"):
            attempt["invalidated"] = True
            attempt["invalidated_at"] = now()
            attempt["invalidation_reason"] = "retry_after_resume"
            invalidated = True
    if invalidated:
        save_state(task_dir, task_dir / "state.sqlite", resumed_state, "resume_invalidation", "retired blocked attempts before retry")
    plan = _load_orchestrate_plan(task_dir, resumed_state)
    prepared = _prepare_orchestrate_wave(params, task_dir, resumed_state, plan)
    return _orchestrate_response("resume", prepared["state"], wave_id=prepared["wave_id"], spawn_requests=prepared["spawn_requests"], plan=plan)


def _orchestrate_lane(params: dict[str, Any]) -> dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    command = str(payload.get("command", "")).strip()
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "create": create_lane,
        "inspect": lane_status,
        "claim": claim_lane,
        "release": release_lane,
        "retire": retire_lane,
        "bind_task": bind_task_lane,
        "materialize": materialize_lane,
        "reconcile": reconcile_lane,
        "claim_resource": claim_lane_resource,
        "release_resource": release_lane_resource,
    }
    if command not in handlers:
        raise ValueError("lane payload.command is unsupported")
    result = handlers[command]({**params, **payload})
    state = result.get("state") or {}
    return {
        "schema": ORCHESTRATE_SCHEMA,
        "ok": True,
        "operation": "lane",
        "transaction_id": None,
        "task_id": state.get("task_id") or params.get("task_id"),
        "wave_id": None,
        "state": "completed",
        "spawn_requests": [],
        "diagnostics": [],
        "result": result,
        "next_action": "continue the lane lifecycle with orchestrate(operation=lane) when needed",
    }


def _orchestrate_resource(params: dict[str, Any]) -> dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    command = str(payload.get("command", "")).strip()
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "claim": claim_resource,
        "release": release_resource,
        "acquire_lock": acquire_lock,
        "release_lock": release_lock,
    }
    if command not in handlers:
        raise ValueError("resource payload.command is unsupported")
    task_id = safe_id(str(params.get("task_id") or payload.get("task_id") or ""))
    _, _, state = load_state(task_id, params)
    result = handlers[command]({**params, **payload, "task_id": task_id, "expected_revision": state["revision"]})
    return _orchestrate_response("resource", result["state"], result=result)


def _orchestrate_question(params: dict[str, Any]) -> dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    command = str(payload.get("command", "ask")).strip()
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "ask": cortex_question,
        "publish": publish_worker_question,
        "list": list_worker_questions,
        "answer": answer_worker_question,
        "updates": get_worker_question_updates,
    }
    if command not in handlers:
        raise ValueError("question payload.command is unsupported")
    task_id = safe_id(str(params.get("task_id") or payload.get("task_id") or ""))
    _, _, state = load_state(task_id, params)
    # Coordinator identity is resolved by the facade and must never be
    # overridden by a question payload.  In particular, a model must not be
    # able to guess task/principal/thread values until one happens to pass.
    reserved = {
        key: params[key]
        for key in ("project_root", "task_id", "principal", "thread_id", "submission_id")
        if key in params
    }
    reserved["user_language"] = str(state.get("user_language") or "en")
    result = handlers[command]({**payload, **reserved})
    return _orchestrate_response("question", state, result=result)


def orchestrate(params: dict[str, Any]) -> dict[str, Any]:
    """Single public Cortex state-machine facade."""
    operation = str(params.get("operation", "")).strip()
    if operation not in ORCHESTRATE_OPERATIONS:
        return _orchestrate_error(operation or "unknown", "unsupported_operation", "operation must be start, advance, inspect, resume, deactivate, lane, resource, question, or plan_approval", recoverable=True)
    try:
        preflight_diagnostics = _collect_orchestrate_diagnostics(params)
        if preflight_diagnostics:
            return _orchestrate_error(
                operation,
                "orchestrate_validation_failed",
                "request failed preflight validation",
                phase="preflight",
                recoverable=True,
                next_operation=operation,
                task_id=str(params.get("task_id") or (params.get("task") or {}).get("task_id") or "") or None,
                diagnostics=preflight_diagnostics,
            )
        select_project_root(params)
        if operation == "inspect":
            return _orchestrate_inspect(params)
        mutating = operation in ORCHESTRATE_MUTATING_OPERATIONS
        payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
        if operation == "lane" and str(payload.get("command", "")) == "inspect":
            mutating = False
        if operation == "question" and str(payload.get("command", "ask")) in {"list", "updates"}:
            mutating = False
        transaction_path = None
        transaction = None
        if mutating:
            if not str(params.get("submission_id", "")).strip():
                raise ValueError(f"{operation} requires submission_id")
            root = ledger_root(params)
            transaction_path, transaction, replay = _begin_orchestrate_transaction(root, params)
            if replay is not None:
                return _materialize_response_report_links(params, replay)
        if operation == "start":
            result = _orchestrate_start(params, transaction_path, transaction)
        elif operation == "advance":
            result = _orchestrate_advance(params, transaction_path, transaction)
        elif operation == "resume":
            result = _orchestrate_resume(params)
        elif operation == "deactivate":
            result = {
                "schema": ORCHESTRATE_SCHEMA,
                "ok": True,
                "operation": "deactivate",
                "transaction_id": None,
                "task_id": params.get("task_id"),
                "wave_id": None,
                "state": "completed",
                "spawn_requests": [],
                "diagnostics": [],
                "result": deactivate_orchestration({**params, "user_command": NORMAL_COMMAND}),
                "next_action": "Cortex orchestration is inactive for this coordinator",
            }
        elif operation == "lane":
            result = _orchestrate_lane(params)
        elif operation == "resource":
            result = _orchestrate_resource(params)
        elif operation == "plan_approval":
            result = _orchestrate_plan_approval(params)
        else:
            result = _orchestrate_question(params)
        if mutating:
            nested_result = result.get("result") if isinstance(result.get("result"), dict) else {}
            if operation == "question" and nested_result.get("status") == "elicitation_unavailable":
                return _leave_orchestrate_transaction_retryable(transaction_path, transaction, result)
            committed = _commit_orchestrate_transaction(transaction_path, transaction, result)
            return _materialize_response_report_links(params, committed)
        return _materialize_response_report_links(
            params,
            {**result, "transaction_id": None, "idempotent": False},
        )
    except PlanReapprovalRequired as exc:
        task_id = str(params.get("task_id") or (params.get("task") or {}).get("task_id") or "") or None
        error = _orchestrate_error(
            operation,
            "plan_reapproval_required",
            exc,
            phase="validation",
            recoverable=True,
            next_operation=operation,
            task_id=task_id,
        )
        error["state"] = "plan_reapproval_required"
        error["next_action"] = (
            "Record the coordinator's material-change decision and reason, insert a singleton Planner plan rework "
            "before affected work, then obtain explicit approval of the replacement plan."
        )
        if "transaction_path" in locals() and transaction_path is not None and transaction is not None:
            transaction.update({"status": "failed", "result": error, "updated_at": now(), "failed_at": now()})
            db_put_operation(transaction_path, safe_id(str(transaction["submission_id"])), transaction)
            error["transaction_id"] = transaction.get("transaction_id")
        return error
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        task_id = str(params.get("task_id") or (params.get("task") or {}).get("task_id") or "") or None
        error = _orchestrate_error(
            operation,
            "orchestrate_validation_failed",
            exc,
            phase=(transaction or {}).get("phase", "preflight") if "transaction" in locals() else "preflight",
            recoverable=True,
            next_operation=operation,
            task_id=task_id,
        )
        if "transaction_path" in locals() and transaction_path is not None and transaction is not None:
            transaction.update({"status": "failed", "result": error, "updated_at": now(), "failed_at": now()})
            db_put_operation(transaction_path, safe_id(str(transaction["submission_id"])), transaction)
            error["transaction_id"] = transaction.get("transaction_id")
        return error
