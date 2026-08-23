"""SQLite-native orchestration state machine behind the public facade.

The nine public MCP handlers stay composed by :mod:`cortex`. This module owns
orchestration transactions, waves, recovery and management operations, and is
loaded lazily by the facade after the entrypoint has completed initialization.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex_runtime.core.runtime_bindings import bind_symbols, bound_symbol
from cortex_runtime import attempt_protocol, canonical_json
from cortex_runtime.context_compiler import context_domain_from_canonical


class ReworkRequestIdempotent(ValueError):
    """A repeated no-op rework request must not create a successor attempt."""

    def __init__(self, state: dict[str, Any], plan: dict[str, Any], digest: str):
        self.state = state
        self.plan = plan
        self.digest = digest
        super().__init__("identical completed-gate rework is already recorded")


# A repeated failure is not itself proof that the task should fail.  It is,
# however, enough evidence to stop autonomous retries when every material
# input to the corrective attempt stayed the same.  The pause below therefore
# preserves the failed gate and asks for a new user-backed strategy; it never
# synthesizes a pass/fail result.
_NO_PROGRESS_REPEAT_LIMIT = 3


# Inspection is intentionally split from repair.  A coordinator can obtain a
# bounded durable snapshot while another task mutation is in flight; it must
# never acquire the project-wide mutation lock merely to discover that a
# worker lease or a stopped result needs recovery.  The write-capable mode is
# explicit so a caller cannot accidentally turn routine recovery/compaction
# inspection into a state transition.
_INSPECT_MODE_READ_ONLY = "read_only"
_INSPECT_MODE_RECOVER_LIFECYCLE = "recover_lifecycle"
_INSPECT_MODES = {_INSPECT_MODE_READ_ONLY, _INSPECT_MODE_RECOVER_LIFECYCLE}


def _preflight_dispatch_context(task: dict[str, Any], state: dict[str, Any]) -> None:
    """Prove that the durable task can produce a worker context before mutation.

    A dispatch briefing is compiled only after a wave boundary has been
    recorded, because it needs the successor's server-owned attempt identity.
    Its task/domain validation, however, is independent of that identity.
    Validate the same canonical boundary before completing attempts, recording
    gates, reopening a plan, or invalidating a retry.  This keeps a compiler
    rejection retryable from the exact unchanged ledger state instead of
    stranding a task at ``gates_recorded`` with no worker to dispatch.

    This is deliberately a validation call rather than a normalizer: the
    compiler remains the one source of truth for what a dispatch may contain,
    and no task field is silently truncated or rewritten here.
    """
    context_domain_from_canonical({
        "task": {
            "user_request_projection": task.get("user_request_projection"),
            "user_request": task.get("user_request"),
            "requirements": task.get("requirements") or task.get("task_requirements"),
            "constraints": task.get("constraints") or task.get("task_constraints"),
            "acceptance_criteria": task.get("acceptance_criteria"),
            "verification": task.get("verification") or task.get("verification_requirements"),
        },
        "state": state,
    })


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
        "MAX_CONTEXT_RESULTS",
        "MAX_WORK_PACKAGES",
        "NORMAL_COMMAND",
        "ORCHESTRATE_MUTATING_OPERATIONS",
        "ORCHESTRATE_OPERATIONS",
        "ORCHESTRATE_SCHEMA",
        "ORCHESTRATION_PLAN_SCHEMA",
        "ORCHESTRATION_TRANSACTION_SCHEMA",
        "PIPELINE_CONTRACT_VERSION",
        "SUPPORTED_MODELS",
        "TERMINAL_ATTEMPT_STATUSES",
        "_attempt",
        "_collect_orchestrate_diagnostics",
        "_context_handoff_service",
        "_delegation_package",
        "_activate_closure_rework",
        "_ledger_root_for_artifact",
        "_open_blocking_questions",
        "_plan_approval",
        "_plan_approval_is_pending",
        "_write_delegation_package",
        "_governance_boundary_recheck",
        "_governance_obligations_for_gate",
        "acquire_lock",
        "activate_orchestration",
        "active_gates",
        "append_pipeline_change",
        "apply_pipeline_operations",
        "answer_worker_question",
        "authorize",
        "authorize_principal",
        "bind_task_lane",
        "canonical_pipeline_gate",
        "capture_project_manifest",
        "canonicalize_full_governance_parallel_groups",
        "canonicalize_full_governance_pipeline",
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
        "current_plan_tracker",
        "db_get_classification",
        "db_get_operation",
        "db_list_task_findings",
        "db_upsert_task_finding",
        "db_load_task",
        "db_put_operation",
        "db_put_worker_session",
        "db_update_task_plan",
        "deactivate_orchestration",
        "digest_text",
        "execute_verification",
        "finalize_attempt",
        "get_worker_question_updates",
        "handoff",
        "init_task",
        "invalidate_plan_approval_for_reopened_plan",
        "invalidate_reworked_result_bindings",
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
        "_rehydrate_dispatch_spawn_request",
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
        "render_lifecycle",
        "quality_checks",
        "resolve_dispatch_route",
        "resume_task",
        "retire_lane",
        "safe_id",
        "sanitize_structured",
        "save_state",
        "select_project_root",
        "state_lock",
        "status",
        "sync_current_wave",
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
    # A recoverable validation failure is a machine-retryable error, not a
    # request for a user decision.  Keeping it in ``needs_input`` causes the
    # public adapter to render a Question/user_message even when an active
    # worker is merely protected from a malformed coordinator payload (for
    # example a future_waves replacement).  Preserve the diagnostic and
    # retryability, but expose the correct non-question lifecycle state.
    validation_error = code == "orchestrate_validation_failed"
    return _segregate_orchestration_output({
        "schema": ORCHESTRATE_SCHEMA,
        "ok": False,
        "operation": operation,
        "transaction_id": None,
        "task_id": task_id,
        "wave_id": None,
        # A malformed request or an unavailable route must never turn the
        # Cortex pipeline into a terminal system block.  Keep the task (or the
        # not-yet-created task) resumable and expose the exact correction or
        # user decision required by the diagnostics.
        "state": "error" if validation_error else "needs_input",
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
    })


def _segregate_orchestration_output(response: dict[str, Any]) -> dict[str, Any]:
    """Expose a stable human view beside the coordinator stable shape.

    Existing callers still receive the historical top-level protocol fields.
    ``user_view`` is deliberately small and contains no task/dispatch IDs or
    machine recovery instructions; ``internal`` is the explicit machine
    boundary for coordinators that want to avoid rendering those fields.
    """
    raw_state = str(response.get("state") or "needs_input")
    # ``blocked`` is a durable audit marker used while the server derives a
    # corrective route.  It is never a public lifecycle state: exposing it
    # makes coordinators treat an internal recovery transition as a hard stop.
    # Preserve the raw marker only inside the machine-readable boundary.
    state = "recovery_pending" if raw_state == "blocked" else raw_state
    if raw_state == "blocked":
        response = {**response, "state": state}
        response.setdefault("internal", {})
        response["internal"] = {
            **(response.get("internal") if isinstance(response.get("internal"), dict) else {}),
            "ledger_state": raw_state,
        }
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    state_summary = response.get("state_summary") if isinstance(response.get("state_summary"), dict) else {}
    communication_config = {
        "communication_profile": response.get("communication_profile") or state_summary.get("communication_profile") or "natural",
        "user_language": response.get("user_language") or state_summary.get("user_language") or "en",
    }
    rendered = render_lifecycle(
        # ``recovery_pending`` is an internal progress checkpoint.  Render it
        # through the ordinary active/progress copy so the user never sees a
        # question-shaped fallback merely because the internal state name is
        # not part of the presentation vocabulary.
        "ready_to_spawn" if state == "recovery_pending" else state,
        ok=(
            bool(response.get("ok", True))
            or state in {"recovery_pending", "rework_preflight_required"}
        ),
        config=communication_config,
        metadata={"state": state},
    )
    language_is_ru = str(communication_config.get("user_language") or "").lower().startswith("ru")
    # ``blocked`` and ``rework_preflight_required`` are internal recovery
    # markers, never a user decision.  Only an explicit plan review or a
    # server-returned question may stop the visible chat turn.
    result_outcome = str(result.get("outcome") or "").strip().lower()
    requires_decision = state == "awaiting_plan_approval" or (
        state == "needs_input" and (
            bool(result.get("requires_user_decision") or result.get("question"))
            or result_outcome in {"awaiting_user", "question"}
            or bool(result.get("question_ref") or result.get("question_refs"))
        )
    )
    if state == "awaiting_plan_approval":
        next_step = "Утвердите план, запросите доработку или отмените." if language_is_ru else "Choose approve, revise, or cancel."
        recommendation = (
            "Утвердите план, если он соответствует цели; иначе запросите доработку."
            if language_is_ru else
            "Approve the plan if it matches the requested outcome; otherwise request a revision."
        )
    elif state in {"recovery_pending", "blocked", "rework_preflight_required"}:
        next_step = (
            "Следуйте серверному маршруту исправления; задача продолжится автоматически."
            if language_is_ru else
            "Follow the server-derived corrective route; Cortex will continue the task automatically."
        )
        recommendation = (
            "Cortex направит исправление тому же конвейеру и сохранит исходные данные."
            if language_is_ru else
            "Cortex will route the correction through the same pipeline and preserve the recorded data."
        )
    else:
        next_step = rendered.get("next_step") or ("Продолжите текущий шаг." if language_is_ru else "Continue the current work step.")
        recommendation = rendered.get("message") or ("Продолжите после готовности результата." if language_is_ru else "Continue when the result is ready.")
    profile = str(rendered.get("profile") or communication_config.get("communication_profile") or "natural")

    def _safe_visible_field(value: object, fallback: str) -> tuple[str, list[str]]:
        """Apply the same plain-language gate to every visible text field.

        ``render_lifecycle`` validates its primary message, but orchestration
        adds ``why_it_matters``, ``next_step`` and ``recommendation`` after
        rendering.  Validate those additions too so a technical template
        cannot leak into a natural user view while still reporting quality OK.
        """
        candidate = str(value or "").strip()
        failures = quality_checks(candidate, profile=profile, next_step="continue")
        if failures:
            return fallback, failures
        return candidate, []

    if language_is_ru:
        why_it_matters = (
            "Следующий шаг зависит от этого решения."
            if requires_decision else
            "Задача продвигается на основе проверенных данных."
        )
        why_fallback = "Следующая часть задачи зависит от этого решения." if requires_decision else "Задача продвигается по проверенному плану."
        next_fallback = "Укажите решение, чтобы продолжить." if requires_decision else "Продолжите задачу, когда будете готовы."
        recommendation_fallback = "Выберите безопасный вариант для продолжения." if requires_decision else "Продолжите после проверки результата."
    else:
        why_it_matters = (
            "The next step depends on this decision."
            if requires_decision else
            "The task is moving forward using checked information."
        )
        why_fallback = "The next step depends on your decision." if requires_decision else "The task is moving forward with checked information."
        next_fallback = "Provide the decision to continue." if requires_decision else "Continue the task when you are ready."
        recommendation_fallback = "Choose the safe option to continue." if requires_decision else "Continue after reviewing the result."
    why_it_matters, why_failures = _safe_visible_field(why_it_matters, why_fallback)
    next_step, next_failures = _safe_visible_field(next_step, next_fallback)
    recommendation, recommendation_failures = _safe_visible_field(recommendation, recommendation_fallback)
    field_failures = why_failures + next_failures + recommendation_failures
    quality = dict(rendered.get("quality") or {})
    if field_failures:
        quality["checks"] = list(quality.get("checks") or []) + field_failures
        quality["fallback_applied"] = True
        # All rejected candidates were replaced with complete safe copy.
        quality["ok"] = True
    user_view = {
        "message": rendered.get("message"),
        "profile": rendered.get("profile"),
        "detail_level": rendered.get("detail_level"),
        "quality": quality,
        "message_type": "decision_required" if requires_decision else "progress",
        "why_it_matters": why_it_matters,
        "next_step": next_step,
        "requires_user_decision": requires_decision,
        "recommendation": recommendation,
        "risks": [],
    }
    dispatches = response.get("dispatches")
    if not isinstance(dispatches, list) or not dispatches:
        dispatches = response.get("spawn_requests", [])
    response["user_view"] = user_view
    if state == "waiting_workers":
        response["user_view"] = None
        response["allowed_visible_events"] = []
    internal = {
        key: value for key, value in response.items()
        if key not in {"user_view", "internal"}
    }
    internal.update({
        "task_ref": response.get("task_id"),
        "wave_id": response.get("wave_id"),
        "dispatches": dispatches,
        "next_action": response.get("next_action"),
        "diagnostics": response.get("diagnostics", []),
    })
    response["internal"] = internal
    return response


def _orchestrate_state_name(state: dict[str, Any]) -> str:
    if state.get("status") == "completed":
        return "completed"
    if state.get("status") == "blocked":
        return "blocked"
    if _plan_approval_is_pending(state) and _plan_approval_user_requested(state):
        return "awaiting_plan_approval"
    if state.get("status") == "needs_input":
        # A needs_input projection is user-visible only when a durable worker
        # question is actually open. Empty/stale technical projections must
        # enter silent server-owned recovery instead of presenting a generic
        # question or asking the coordinator to repair Cortex state.
        if any(
            isinstance(item, dict)
            and not item.get("invalidated")
            and (
                item.get("lifecycle_status") == "paused_awaiting_user"
                or item.get("host_stop_outcome") == "awaiting_user"
            )
            for item in state.get("attempts", [])
        ):
            return "needs_input"
        return "recovery_pending"
    current = set(active_gates(state))
    attempts = [item for item in state.get("attempts", []) if item.get("gate") in current and not item.get("invalidated")]
    if any(item.get("status") == AWAITING_HOST_SPAWN for item in attempts):
        return "ready_to_spawn"
    # A native ``SubagentStop`` can leave a finalized canonical result before
    # the coordinator advances the exact attempt-result reference.  That
    # worker has stopped and must not be presented as waitable.
    live_attempts = [
        item for item in attempts
        if item.get("status") == "running"
        and item.get("lifecycle_status") not in {"paused_awaiting_user", attempt_protocol.LIFECYCLE_COMPLETED}
    ]
    if live_attempts:
        return "waiting_workers"
    if any(
        item.get("status") == "running"
        and item.get("lifecycle_status") == attempt_protocol.LIFECYCLE_COMPLETED
        for item in attempts
    ):
        return "completion_pending"
    return "needs_input"


def _orchestrate_summary(state: dict[str, Any]) -> dict[str, Any]:
    done = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    return {
        "status": state.get("status"),
        "revision": state.get("revision"),
        "complexity": state.get("complexity"),
        "communication_profile": state.get("communication_profile") or "natural",
        "user_language": state.get("user_language") or "en",
        "current_gates": active_gates(state),
        "completed_gates": list(state.get("completed_gates", [])),
        "skipped_gates": list(state.get("skipped_gates", [])),
        "current_pipeline": list(state.get("current_pipeline", [])),
        "chosen_pipeline": list(state.get("chosen_pipeline") or state.get("current_pipeline", [])),
        "chosen_parallel_groups": [list(group) for group in state.get("chosen_parallel_groups") or state.get("parallel_groups") or []],
        "recommended_pipeline": list(state.get("recommended_pipeline") or []),
        "recommended_parallel_groups": [list(group) for group in state.get("recommended_parallel_groups") or []],
        "pipeline_authority": state.get("pipeline_authority") or "orchestrator",
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
            "user_requested": bool(_plan_approval_user_requested(state)),
            "plan_result_ref": _plan_approval(state).get("plan_result_ref"),
            "approved_basis": _plan_approval(state).get("approved_basis"),
        },
        "pipeline_contract_version": _pipeline_contract_version(state),
        "attempts": [
            {
                "attempt_id": item.get("attempt_id"),
                "gate": item.get("gate"),
                "profile": item.get("profile"),
                "status": item.get("status"),
                "completion_transport_status": item.get("completion_transport_status"),
                "gate_decision": item.get("gate_decision"),
                "lifecycle_status": item.get("lifecycle_status"),
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
        "authority": "orchestrator",
        "revision": state.get("revision"),
        "chosen_pipeline": list(state.get("chosen_pipeline") or state.get("current_pipeline") or []),
        "recommended_pipeline": list(state.get("recommended_pipeline") or []),
        "waves": waves,
        "change_policy": (
            "Execute the orchestrator-selected pipeline. Cortex recommendations, governance conventions, and "
            "gate ordering are advisory; only integrity, capability, authorization, and safety boundaries can "
            "reject an otherwise executable choice."
        ),
    }


def _orchestrate_transaction_path(root: Path, submission_id: str) -> Path:
    """Return the ledger root; transaction receipts are SQLite records."""
    safe_id(submission_id)
    return root


def _orchestrate_request_digest(params: dict[str, Any]) -> str:
    return digest_text(canonical_json.dumps(params))


def _begin_orchestrate_transaction(root: Path, params: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any] | None]:
    submission_id = safe_id(str(params.get("submission_id", "")))
    path = _orchestrate_transaction_path(root, submission_id)
    request_digest = _orchestrate_request_digest(params)
    receipt = db_get_operation(root, submission_id)
    if receipt is not None:
        if receipt.get("schema") != ORCHESTRATION_TRANSACTION_SCHEMA:
            raise ValueError("orchestrate submission_id was reused with different content")
        if receipt.get("request_digest") != request_digest:
            raise ValueError("orchestrate submission_id was reused with different content")
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
        "governance_activation": "code_reviewer",
        "governance_close": "code_reviewer",
    }.get(gate, "general")


def _default_task_kind_for_gate(gate: str) -> str:
    return {
        "scope": "scoping", "plan": "planning", "discover": "discovery", "architecture": "architecture",
        "database_architecture": "database", "implementation": "implementation", "qa": "testing",
        "security": "security", "performance": "performance", "accessibility": "accessibility",
        "ux": "ux", "review": "code_review", "documentation": "documentation", "close": "verification",
        "governance_activation": "governance_activation", "governance_close": "governance_close",
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
    chosen_wave_specs: list[tuple[str, list[str], list[dict[str, Any]]]] = []
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
        wave_specs: list[dict[str, Any]] = []
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
            wave_specs.append(dict(raw_spec))
        original_ids[tuple(group)] = wave_id
        proposed_groups.append(group)
        # ``classification`` is advisory. Preserve the exact wave/group
        # selected by the orchestrator as the executable contract, including
        # an intentional order or parallel grouping that the classifier would
        # otherwise normalize away.
        chosen_wave_specs.append((wave_id, group, wave_specs))
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
    for index, (requested_wave_id, group, raw_specs) in enumerate(chosen_wave_specs, 1):
        wave_id = requested_wave_id or original_ids.get(tuple(group), f"wave-{index:02d}")
        if wave_id in used_ids:
            wave_id = f"wave-{index:02d}"
        used_ids.add(wave_id)
        delegations: list[dict[str, Any]] = []
        for gate in group:
            specs = [spec for spec in raw_specs if canonical_pipeline_gate(spec.get("gate") or "") == gate] or [{"gate": gate}]
            for spec_index, raw_spec in enumerate(specs, 1):
                agent = str(raw_spec.get("agent") or _default_profile_for_gate(gate))
                if agent not in AGENTS:
                    raise ValueError(f"unknown Cortex profile: {agent}")
                briefing = render_gate_briefing(gate, task.get("user_request", ""), agent)
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
    classification = {
        **classification,
        "recommended_pipeline": list(classification.get("pipeline") or []),
        "recommended_parallel_groups": [list(group) for group in classification.get("parallel_groups") or []],
        "chosen_pipeline": list(proposed_pipeline),
        "chosen_parallel_groups": [list(group) for group in proposed_groups],
    }
    return normalized, classification


def _validate_v2_wave_contract(
    waves: list[dict[str, Any]],
    *,
    plan_approval: str,
) -> None:
    """Validate only the shape of a v2 wave contract.

    Ordering and plan-approval rules are Cortex recommendations, not
    authorization. The orchestrator owns the chosen pipeline and may
    intentionally skip, reorder, or repeat policy-oriented gates. Capability
    and dispatch-shape validation remains in wave normalization and route
    resolution.
    """
    del waves, plan_approval
    return


def _orchestrate_plan_path(task_dir: Path) -> Path:
    """Stable label; canonical plans are stored in ``tasks.plan_json``."""
    return _ledger_root_for_artifact(task_dir) / "cortex.db"


def _write_orchestrate_plan(task_dir: Path, plan: dict[str, Any]) -> None:
    plan["updated_at"] = now()
    db_update_task_plan(_ledger_root_for_artifact(task_dir), safe_id(str(plan.get("task_id") or "")), plan)


def _record_chosen_pipeline(
    task_dir: Path,
    state: dict[str, Any],
    pipeline: list[str],
    parallel_groups: list[list[str]],
    *,
    recommended_pipeline: list[str] | None = None,
    recommended_parallel_groups: list[list[str]] | None = None,
    reason: str,
    reset_gates: list[str] | None = None,
) -> dict[str, Any]:
    """Persist the orchestrator's chosen route without a policy rewrite.

    ``update_pipeline`` is intentionally policy-aware for legacy callers. The
    orchestration engine has a different contract: a valid coordinator choice
    is executable even when it differs from Cortex's recommended governance
    route. This helper keeps capability/syntax validation at the boundary,
    records both routes for audit, and uses the existing state-only projection
    helper to invalidate only explicitly reworked downstream evidence.
    """
    chosen = [canonical_pipeline_gate(item) for item in pipeline if str(item).strip()]
    if not chosen:
        raise ValueError("chosen pipeline must contain at least one gate")
    if len(chosen) != len(set(chosen)):
        raise ValueError("chosen pipeline cannot contain duplicate gates")
    chosen_set = set(chosen)
    groups: list[list[str]] = []
    seen: set[str] = set()
    for raw_group in parallel_groups:
        if not isinstance(raw_group, list) or not raw_group:
            raise ValueError("chosen parallel_groups must contain non-empty arrays")
        group = []
        for item in raw_group:
            gate = canonical_pipeline_gate(item)
            if gate not in chosen_set or gate in seen:
                raise ValueError("chosen parallel_groups must partition chosen_pipeline")
            group.append(gate)
            seen.add(gate)
        groups.append(group)
    if seen != chosen_set:
        groups.extend([[gate] for gate in chosen if gate not in seen])
    previous = list(state.get("current_pipeline") or [])
    previous_groups = [list(group) for group in state.get("parallel_groups") or []]
    change = {
        "pipeline": chosen,
        "parallel_groups": groups,
        "changed": chosen != previous or groups != previous_groups,
        "parallel_groups_changed": groups != previous_groups,
        "from": previous,
        "operations": [{"op": "chosen_pipeline", "reason": redact(reason, 1000)}],
        "reset_gates": list(dict.fromkeys(reset_gates or [])),
    }
    if change["changed"] or change["reset_gates"]:
        append_pipeline_change(state, change, redact(reason, 2000), [])
    state["chosen_pipeline"] = list(chosen)
    state["chosen_parallel_groups"] = [list(group) for group in groups]
    state["recommended_pipeline"] = list(recommended_pipeline or [])
    state["recommended_parallel_groups"] = [list(group) for group in (recommended_parallel_groups or [])]
    state["pipeline_authority"] = "orchestrator"
    state["pipeline_obligations"] = list(chosen)
    if state.get("status") == "completed" and any(
        gate not in state.get("completed_gates", []) and gate not in state.get("skipped_gates", [])
        for gate in chosen
    ):
        state["status"] = "active"
    sync_current_wave(state)
    save_state(task_dir, task_dir / "state.sqlite", state, "chosen_pipeline", redact(reason, 2000))
    return state


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
    for wave in plan.get("waves", []):
        wave_gates = set(wave.get("gates", []))
        if wave_gates == gate_set:
            # Keep the canonical plan object authoritative. The executable
            # subset is a durable projection of the current gate state; it is
            # never represented by copying and mutating a detached wave.
            wave["executable_gates"] = [gate for gate in wave.get("gates", []) if gate in gate_set]
            wave.pop("partial_parallel_wave", None)
            return wave
        if gate_set and gate_set < wave_gates:
            # A parallel sibling may have completed (or be independently
            # paused for no-progress) while another gate still needs a retry.
            # Preserve the original wave identity and full gate list. The
            # current executable subset is recorded on that same canonical
            # wave, so restart/inspect/replan retain completed siblings and
            # the source wave cannot be marked complete prematurely.
            wave["executable_gates"] = [gate for gate in wave.get("gates", []) if gate in gate_set]
            wave["partial_parallel_wave"] = True
            return wave
    return None



def _predecessor_context_result_refs(
    state: dict[str, Any],
    required_gates: set[str] | None = None,
) -> list[str]:
    """Select finalized AttemptResults from completed predecessor attempts.

    Dispatch context is a canonical semantic dependency, not a projection
    projection dependency.  A completed gate contributes only its exact
    server-issued AttemptResult reference.
    """
    completed = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    selected: list[str] = []
    for attempt in state.get("attempts", []):
        if (
            not isinstance(attempt, dict)
            or attempt.get("status") != "passed"
            or attempt.get("invalidated")
            or attempt.get("gate") not in completed
            or (required_gates is not None and attempt.get("gate") not in required_gates)
        ):
            continue
        raw_result_ref = str(attempt.get("attempt_result_ref") or "").strip()
        if not raw_result_ref:
            continue
        result_ref = safe_id(raw_result_ref)
        if result_ref and result_ref not in selected:
            selected.append(result_ref)
    return selected



def _transitive_result_context_frontier(state: dict[str, Any], result_refs: list[str]) -> list[str]:
    """Collapse only canonical AttemptResult predecessor chains."""
    selected: list[str] = []
    for item in result_refs:
        raw_result_ref = str(item or "").strip()
        if not raw_result_ref:
            continue
        result_ref = safe_id(raw_result_ref)
        if result_ref not in selected:
            selected.append(result_ref)
    selected_set = set(selected)
    covered: set[str] = set()
    for attempt in state.get("attempts", []):
        if not isinstance(attempt, dict) or attempt.get("status") != "passed" or attempt.get("invalidated"):
            continue
        raw_produced = str(attempt.get("attempt_result_ref") or "").strip()
        if not raw_produced:
            continue
        produced = safe_id(raw_produced)
        if produced not in selected_set:
            continue
        for result_ref in attempt.get("context_result_refs", []):
            raw_result_ref = str(result_ref or "").strip()
            if raw_result_ref and safe_id(raw_result_ref) in selected_set:
                covered.add(safe_id(raw_result_ref))
    return [result_ref for result_ref in selected if result_ref not in covered]


def _compiled_implementation_spec(
    task_dir: Path,
    state: dict[str, Any],
    wave: dict[str, Any],
) -> dict[str, Any] | None:
    """Compile the approved Planner DAG into one dependency-safe dispatch.

    A single composite worker preserves package/microtask order without
    pretending dependent work can run in parallel. Independent concurrency can
    be introduced later by a scheduler with explicit completion barriers; the
    current runtime must prefer a truthful executable contract over generic
    task-wide implementation text.
    """
    manifest = current_planning_manifest(task_dir)
    if not isinstance(manifest, dict) or not manifest.get("work_packages"):
        return None
    packages: list[dict[str, Any]] = []
    for summary in manifest.get("work_packages", []):
        if not isinstance(summary, dict) or not str(summary.get("artifact_path") or ""):
            raise ValueError("approved planning manifest has no package artifact path")
        record, _ = read_immutable_json_artifact(
            task_dir,
            state["task_id"],
            str(summary["artifact_path"]),
            kinds={"planning_revision"},
        )
        package = record.get("package") if isinstance(record, dict) else None
        if not isinstance(package, dict):
            raise ValueError("approved planning package artifact is invalid")
        packages.append(package)
    microtasks = [
        {**microtask, "package_id": package["id"]}
        for package in packages
        for microtask in package.get("microtasks", [])
        if isinstance(microtask, dict)
    ]
    if not microtasks:
        raise ValueError("approved plan contains no implementation microtasks")
    by_id = {str(item["id"]): item for item in microtasks}
    package_dependencies = {
        str(package["id"]): [str(value) for value in package.get("depends_on", [])]
        for package in packages
    }
    package_microtasks = {
        str(package["id"]): {
            str(item["id"]) for item in package.get("microtasks", []) if isinstance(item, dict)
        }
        for package in packages
    }
    ordered: list[dict[str, Any]] = []
    remaining = dict(by_id)
    completed: set[str] = set()
    while remaining:
        ready = [
            item for item in remaining.values()
            if (
                set(str(dep) for dep in item.get("depends_on", []))
                | {
                    microtask_id
                    for package_id in package_dependencies.get(str(item["package_id"]), [])
                    for microtask_id in package_microtasks.get(package_id, set())
                }
            ).issubset(completed)
        ]
        if not ready:
            raise ValueError("approved planning microtask dependency graph cannot be scheduled")
        for item in sorted(ready, key=lambda value: str(value["id"])):
            ordered.append(item)
            completed.add(str(item["id"]))
            remaining.pop(str(item["id"]), None)
    paths = list(dict.fromkeys(
        str(path)
        for item in ordered
        for path in item.get("allowed_paths", [])
        if str(path).strip()
    ))
    if not paths or any(path.strip() in {".", "*"} for path in paths):
        raise ValueError(
            "approved implementation microtasks require explicit non-broad allowed_paths before dispatch"
        )
    profiles = [str(item.get("profile") or "") for item in ordered if str(item.get("profile") or "")]
    non_devops = [profile for profile in profiles if profile != "devops_engineer"]
    profile_set = set(non_devops or profiles)
    if "mobile_dev" in profile_set:
        agent = "mobile_dev"
    elif profile_set and profile_set <= {"backend_dev", "data_engineer", "debugger"}:
        agent = "backend_dev"
    elif profile_set == {"frontend_dev"}:
        agent = "frontend_dev"
    elif profile_set == {"fullstack_dev"} or {"frontend_dev", "backend_dev"}.issubset(profile_set):
        agent = "fullstack_dev"
    elif len(profile_set) == 1:
        agent = next(iter(profile_set))
    else:
        agent = "general"
    if agent == "devops_engineer" and any(
        not str(path).startswith((".github/", "infra/", "deploy/", "ops/")) for path in paths
    ):
        agent = "general"
    acceptance = list(dict.fromkeys(
        str(value)
        for item in ordered
        for value in item.get("acceptance_criteria", [])
        if str(value).strip()
    ))
    verification = list(dict.fromkeys(
        str(value)
        for item in ordered
        for value in item.get("verification", [])
        if str(value).strip()
    ))
    base = dict((wave.get("delegations") or [{}])[0])
    revision = str(manifest.get("revision") or "")
    plan_unit = {
        "schema": "cortex/compiled-plan-unit/v1",
        "plan_revision": revision,
        "source_result_ref": manifest.get("source_result_ref"),
        "package_ids": [str(package["id"]) for package in packages],
        "microtasks": [
            {
                "sequence": index,
                "package_id": item["package_id"],
                "id": item["id"],
                "title": item["title"],
                "objective": item["objective"],
                "profile": item.get("profile"),
                # The compiled plan is the canonical living tracker snapshot
                # consumed by implementation workers.  Runtime status starts
                # at the Planner-declared value and is advanced by lifecycle
                # receipts; order and gates remain explicit rather than being
                # inferred from prompt text.
                "status": item.get("status") or "pending",
                "order": item.get("order", index),
                "gates": item.get("gates") or ["implementation"],
                "allowed_paths": item.get("allowed_paths") or [],
                "depends_on": item.get("depends_on") or [],
                "acceptance_criteria": item.get("acceptance_criteria") or [],
                "verification": item.get("verification") or [],
                "required_artifacts": item.get("required_artifacts") or [],
            }
            for index, item in enumerate(ordered, 1)
        ],
    }
    return {
        **base,
        "gate": "implementation",
        "agent": agent,
        # The exact work breakdown is a digest-bound immutable artifact.  Do
        # not repeat its titles (or any other plan content) in this field:
        # ``objective`` reaches the worker briefing and would otherwise bloat
        # the transport with a second copy of the plan.
        "objective": "Execute the approved immutable Planner plan in dependency order.",
        "selection_reason": (
            f"Compiled from approved plan revision {revision}; selected {agent} from microtask profiles while "
            "preventing deployment-only routing from owning application-code changes."
        ),
        "allowed_paths": paths,
        "acceptance_criteria": acceptance,
        "verification": verification,
        "plan_unit": plan_unit,
        "orchestration_delegation_key": (
            f"{wave['wave_id']}-implementation-plan-{digest_text(revision)[:12]}"
        ),
    }



def _active_rework_corrective_receipts(
    root: Path,
    state: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return retained corrective receipts and active routes missing one.

    The ordinary predecessor frontier intentionally removes a result once a
    successor has acknowledged it.  That is normally the right compaction,
    but it is insufficient for an active closure-rework route: the originating
    verifier must receive every current, server-bound corrective receipt that
    it may need to produce a resolution receipt.  The second return value is a
    durable-dispatch preflight: it names an active route that has no usable
    correction for at least one of its open finding/origin pairs.  Callers use
    it only when dispatching that route's origin gate, so a corrective worker
    is still allowed to run before any correction exists.
    """
    current_revision = int(state.get("task_revision") or 1)
    active_routes: list[dict[str, Any]] = []
    for source_gate, rework in (state.get("closure_rework") or {}).items():
        if (
            not isinstance(rework, dict)
            or rework.get("status") != "rework_required"
            or int(rework.get("task_revision") or 0) != current_revision
        ):
            continue
        target_gate = str(rework.get("target_gate") or "")
        source_refs = {
            str(item) for item in rework.get("source_result_refs") or [] if str(item)
        }
        fingerprints = {
            str(item) for item in rework.get("finding_fingerprints") or [] if str(item)
        }
        origin_gate = str(source_gate or "")
        if target_gate and origin_gate and source_refs and fingerprints:
            active_routes.append({
                "origin_gate": origin_gate,
                "target_gate": target_gate,
                "source_refs": source_refs,
                "fingerprints": fingerprints,
            })
    if not active_routes:
        return [], []

    open_findings = {
        str(item.get("fingerprint") or ""): item
        for item in db_list_task_findings(root, state["task_id"], include_resolved=False)
        if isinstance(item, dict)
    }
    passed_result_gates: dict[str, str] = {}
    result_order: list[str] = []
    for attempt in state.get("attempts") or []:
        if (
            not isinstance(attempt, dict)
            or attempt.get("status") != "passed"
            or attempt.get("invalidated")
        ):
            continue
        gate = str(attempt.get("gate") or "")
        value = str(attempt.get("attempt_result_ref") or "")
        if value:
            passed_result_gates[value] = gate
            if value not in result_order:
                result_order.append(value)

    retained: set[str] = set()
    missing_routes: list[dict[str, Any]] = []
    for route in active_routes:
        route_receipts: set[str] = set()
        missing_pairs: list[tuple[str, str]] = []
        for fingerprint in sorted(route["fingerprints"]):
            finding = open_findings.get(fingerprint)
            for origin_result_ref in sorted(route["source_refs"]):
                pair_receipts: set[str] = set()
                if isinstance(finding, dict):
                    for source in finding.get("source_evidence") or []:
                        if (
                            not isinstance(source, dict)
                            or source.get("transition") != "corrective_reported"
                            or source.get("gate") != route["target_gate"]
                            or source.get("origin_result_ref") != origin_result_ref
                            or int(source.get("task_revision") or 0) != current_revision
                        ):
                            continue
                        result_ref = str(source.get("attempt_result_ref") or "")
                        if (
                            result_ref
                            and passed_result_gates.get(result_ref) == route["target_gate"]
                        ):
                            pair_receipts.add(result_ref)
                if not pair_receipts:
                    missing_pairs.append((fingerprint, origin_result_ref))
                route_receipts.update(pair_receipts)
        retained.update(route_receipts)
        if missing_pairs:
            # Do not expose private result refs in a public dispatch error.
            # The coordinator only needs the canonical origin/target route and
            # count to route the missing corrective work safely.
            missing_routes.append({
                "origin_gate": route["origin_gate"],
                "target_gate": route["target_gate"],
                "missing_binding_count": len(missing_pairs),
            })

    return [item for item in result_order if item in retained], missing_routes



def _assert_origin_verifier_rework_preflight(
    root: Path,
    state: dict[str, Any],
    current_gates: list[str],
) -> None:
    """Record missing corrective receipts as advice, never a dispatch veto."""
    del root, state, current_gates
    return


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
        # a generic writer result from being treated as proof of a fix.
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


def _closure_unresolved_corrective_findings(
    root: Path,
    state: dict[str, Any],
    attempt_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Materialize closure ``unresolved`` items as server-owned findings.

    ``unresolved`` is intentionally a semantic AttemptResult field and is not
    normally a task-finding projection.  A closure verifier is the one place
    where those items are a gate-level defect: the completed result cannot
    pass its own closure gate.  Persist a stable, task-scoped finding here so
    the normal closure-rework route can carry the exact item to a corrective
    worker and later require a fresh origin-verifier receipt.
    """
    findings: list[dict[str, Any]] = []
    result_refs: list[str] = []
    for attempt_id in attempt_ids:
        attempt = _attempt(state, safe_id(str(attempt_id)))
        result_ref = str(attempt.get("attempt_result_ref") or "").strip()
        if not result_ref:
            continue
        canonical = attempt_protocol.get_attempt_result(
            root, task_id=str(state["task_id"]), attempt_id=str(attempt["attempt_id"]),
        )
        if (
            not isinstance(canonical, dict)
            or str(canonical.get("result_ref") or "") != result_ref
            or canonical.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
        ):
            continue
        unresolved = canonical.get("unresolved")
        if not isinstance(unresolved, list):
            continue
        result_refs.append(result_ref)
        for index, raw in enumerate(unresolved):
            value = dict(raw) if isinstance(raw, dict) else {"summary": str(raw)}
            summary = str(value.get("summary") or value.get("message") or raw).strip()
            if not summary:
                summary = "Closure verifier reported unresolved work"
            fingerprint = str(value.get("fingerprint") or "").strip()
            if not fingerprint:
                fingerprint = "closure-unresolved-" + digest_text(canonical_json.dumps({
                    "result_ref": result_ref,
                    "index": index,
                    "item": value,
                }))[:32]
            details = value.get("details")
            if details is None:
                details = {"unresolved": value}
                if isinstance(value.get("affected_paths"), list):
                    details["affected_paths"] = list(value["affected_paths"])
            finding = {
                "fingerprint": fingerprint,
                "severity": (
                    str(value.get("severity") or "P1")
                    if str(value.get("severity") or "P1") in {"info", "P3", "P2", "P1", "P0"}
                    else "P1"
                ),
                "status": "open",
                "blocking": True,
                "summary": redact(summary, 2000),
                "details": details,
            }
            db_upsert_task_finding(
                root,
                str(state["task_id"]),
                finding,
                source={
                    "transition": "opened",
                    "source_type": "closure_attempt_unresolved",
                    "attempt_id": str(attempt["attempt_id"]),
                    "attempt_result_ref": result_ref,
                    "origin_result_ref": result_ref,
                    "gate": str(attempt.get("gate") or ""),
                    "finding_index": index,
                    "task_revision": int(state.get("task_revision") or 1),
                },
            )
            findings.append(finding)
    return findings, list(dict.fromkeys(result_refs))


def _record_server_corrective_receipts(
    root: Path,
    state: dict[str, Any],
    attempt: dict[str, Any],
    result_ref: str,
) -> None:
    """Bind a passed corrective result to every active closure finding.

    The worker result is semantic evidence, not an authority-bearing routing
    instruction.  Once the server finalizes a corrective target attempt, it
    records the exact target result as the receipt that permits the originating
    verifier to run again.  This prevents a missing parent-authored
    ``corrective_reported`` marker from turning a valid corrective dispatch
    into a preflight blocker.
    """
    target_gate = str(attempt.get("gate") or "")
    current_revision = int(state.get("task_revision") or 1)
    open_findings = {
        str(item.get("fingerprint") or ""): item
        for item in db_list_task_findings(root, state["task_id"], include_resolved=False)
        if isinstance(item, dict)
    }
    for origin_gate, rework in (state.get("closure_rework") or {}).items():
        if (
            not isinstance(rework, dict)
            or rework.get("status") != "rework_required"
            or str(rework.get("target_gate") or "") != target_gate
            or int(rework.get("task_revision") or 0) != current_revision
        ):
            continue
        for fingerprint in rework.get("finding_fingerprints") or []:
            finding = open_findings.get(str(fingerprint))
            if not isinstance(finding, dict):
                continue
            for origin_result_ref in rework.get("source_result_refs") or []:
                db_upsert_task_finding(
                    root,
                    str(state["task_id"]),
                    finding,
                    source={
                        "transition": "corrective_reported",
                        "source_type": "server_corrective_dispatch",
                        "gate": target_gate,
                        "origin_gate": str(origin_gate),
                        "origin_result_ref": str(origin_result_ref),
                        "attempt_id": str(attempt.get("attempt_id") or ""),
                        "attempt_result_ref": str(result_ref),
                        "task_revision": current_revision,
                    },
                )


def _prepare_orchestrate_wave(params: dict[str, Any], task_dir: Path, state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    state, plan = _repair_delivery_graph_before_closure(params, task_dir, state, plan)
    current_gates = active_gates(state)
    if not current_gates:
        return {"wave_id": None, "spawn_requests": [], "attempt_ids": [], "state": state}
    _assert_origin_verifier_rework_preflight(
        _ledger_root_for_artifact(task_dir), state, current_gates,
    )
    _assert_approved_plan_fresh(task_dir, state, plan)
    wave = _wave_for_gates(plan, current_gates)
    if wave is None:
        # A stale projection may retain a valid chosen pipeline while its
        # materialized plan lost the active wave. Rehydrate that wave from the
        # orchestrator-owned frontier; this is lifecycle reconciliation, not a
        # governance rewrite.
        task_definition = load_task_definition(task_dir, state)
        generated, _ = _normalize_orchestrate_waves(
            [{
                "wave_id": "reconciled-current-wave",
                "delegations": [{"gate": gate} for gate in current_gates],
            }],
            task_definition,
            plan.get("host_capabilities") or {},
            str(params["project_root"]),
        )
        wave = generated[0]
        completed_gates = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
        plan["waves"] = [
            item for item in plan.get("waves", [])
            if not set(item.get("gates", [])).intersection(set(current_gates))
            or set(item.get("gates", [])).issubset(completed_gates)
        ] + [wave]
        _write_orchestrate_plan(task_dir, plan)
    executable_gates = list(wave.get("executable_gates") or current_gates)
    task_definition = load_task_definition(task_dir, state)
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
    prepared_attempts: list[tuple[dict[str, Any], dict[str, Any]]] = []
    predecessor_result_refs = _predecessor_context_result_refs(state)
    effective_delegations = [
        spec for spec in wave["delegations"]
        if str(spec.get("gate") or "") in executable_gates
    ]
    if executable_gates == ["implementation"]:
        compiled = _compiled_implementation_spec(task_dir, state, wave)
        if compiled is not None:
            effective_delegations = [compiled]
    for spec in effective_delegations:
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
            prepared_attempts.append((existing, _rehydrate_dispatch_spawn_request(
                task_dir, task_definition, existing,
            )))
            continue
        observed = status({**params, "task_id": state["task_id"]})
        if "context_result_refs" in spec:
            context_result_refs = list(spec.get("context_result_refs") or [])
        elif "context_gates" in spec:
            context_result_refs = _predecessor_context_result_refs(
                state,
                {canonical_pipeline_gate(item) for item in spec.get("context_gates") or []},
            )
        else:
            context_result_refs = predecessor_result_refs
        # Closure-rework dispatches must carry the immutable origin result
        # that raised the finding.  The origin attempt is intentionally
        # invalidated by rework, so ordinary predecessor selection cannot
        # recover this context from the active completed-gate frontier.
        corrective_context_refs = [
            str(result_ref)
            for rework in (state.get("closure_rework") or {}).values()
            if isinstance(rework, dict)
            and rework.get("status") == "rework_required"
            and int(rework.get("task_revision") or 0) == int(state.get("task_revision") or 1)
            and str(rework.get("target_gate") or "") == str(spec.get("gate") or "")
            for result_ref in rework.get("source_result_refs") or []
            if str(result_ref).strip()
        ]
        context_result_refs = list(dict.fromkeys(context_result_refs + corrective_context_refs))
        if spec.get("gate") == "plan" and _pipeline_contract_version(state) >= 2:
            required_gates = {
                gate for gate in ("scope", "discover", "architecture", "database_architecture", "ux")
                if gate in [item for wave_item in plan.get("waves", []) for item in wave_item.get("gates", [])]
            }
            context_result_refs = list(dict.fromkeys(
                context_result_refs + _predecessor_context_result_refs(state, required_gates)
            ))
        # A successor result transitive frontier is derived solely from the
        # AttemptResult refs that its completed attempt was assigned.
        context_result_refs = _transitive_result_context_frontier(state, context_result_refs)
        plan_feedback = str(_plan_approval(state).get("feedback") or "").strip()
        if spec.get("gate") == "plan":
            latest_intent = str(
                task_definition.get("current_user_intent")
                or task_definition.get("user_request")
                or ""
            ).strip()
            latest_revision = int(task_definition.get("current_user_intent_revision") or 1)
            authoritative = (
                f"Authoritative latest user intent (revision {latest_revision}): {latest_intent}. "
                "Preserve every earlier requirement unless this revision explicitly supersedes it. "
                "Do not restore an older environment, verification route, or deployment target from the original request. "
                "Return requirement_coverage rows for every retained requirement and recommend approve only when "
                "material questions and uncertainties are resolved."
            )
            plan_feedback = "\n\n".join(item for item in (plan_feedback, authoritative) if item)
        delegated = record_delegation({
            **params,
            **spec,
            **({"plan_feedback": plan_feedback} if plan_feedback else {}),
            "context_result_refs": context_result_refs,
            "task_id": state["task_id"],
            "expected_revision": observed["state"]["revision"],
            "status_receipt": observed["status_receipt"],
        })
        if delegated.get("recorded") is False:
            raise ValueError(str(delegated.get("reason") or "wave delegation was not recorded"))
        state = delegated["state"]
        prepared_attempts.append((
            _attempt(state, delegated["attempt_id"]),
            dict(delegated["spawn_request"]),
        ))
    wave["status"] = "active"
    wave["executable_gates"] = list(executable_gates)
    prior_attempt_ids = [str(item) for item in wave.get("attempt_ids") or [] if str(item).strip()]
    wave["attempt_ids"] = list(dict.fromkeys(prior_attempt_ids + [item[0]["attempt_id"] for item in prepared_attempts]))
    _write_orchestrate_plan(task_dir, plan)
    save_state(task_dir, task_dir / "state.sqlite", state, "orchestrate_wave", wave["wave_id"])
    spawn_requests = [
        {**request, "attempt_id": attempt["attempt_id"]}
        for attempt, request in prepared_attempts
        if attempt.get("status") == AWAITING_HOST_SPAWN
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
    ledger_state = _orchestrate_state_name(state)
    # A blocked ledger row is an internal recovery checkpoint, not a state in
    # which the public Cortex pipeline may stop.  The advance/resume paths
    # normally dispatch the corrective route before reaching this function;
    # this normalization is the final safety net for stale projections and
    # direct inspection/replay responses.
    facade_state = "recovery_pending" if ledger_state == "blocked" else ledger_state
    if facade_state == "ready_to_spawn":
        next_action = "invoke every returned native spawn request, wait for the wave, then call orchestrate(operation=advance) once"
    elif facade_state == "waiting_workers":
        next_action = "wait for every worker in the current wave, then call orchestrate(operation=advance) once"
    elif facade_state == "completion_pending":
        next_action = (
            "read the verified stopped-worker result candidates, then explicitly select exactly one immutable "
            "attempt_result_ref for each affected worker slot when calling orchestrate(operation=advance); do not wait, "
            "respawn, or resume the stopped worker"
        )
    elif facade_state == "completed":
        next_action = "present the verified task result to the user"
    elif facade_state == "recovery_pending":
        # ``blocked`` is an internal ledger/recovery marker only.  It must
        # never become an instruction that stops the Cortex pipeline or asks
        # the coordinator to repair state by hand.  The advance/resume paths
        # derive a corrective dispatch; when a human decision is genuinely
        # required they return a question-shaped result instead.
        next_action = (
            "Cortex retained a recoverable lifecycle condition. Follow the server-derived corrective dispatch "
            "or surface the exact user question returned in result; do not edit lifecycle state manually."
        )
    elif facade_state == "awaiting_plan_approval":
        next_action = (
            "read the planner result, present a concise main-chat plan summary, and call plan_approval with "
            "decision=prompt. An initialized stdio host receives native Approve/Cancel controls plus free-form "
            "input; direct callers receive the equivalent cortex/plan-approval/v1 fallback interaction. Non-empty "
            "custom text requests Planner revision; otherwise submit only the selected action's embedded response "
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
    if ledger_state == "blocked":
        response["internal_ledger_state"] = ledger_state
    if facade_state == "waiting_workers":
        response.update({
            "output_policy": "silent",
            "allowed_visible_events": [
                "user_message", "worker_question", "worker_completed", "worker_failed", "blocking_error",
            ],
        })
    if isinstance(plan, dict):
        response["pipeline"] = _orchestrate_pipeline_snapshot(state, plan)
    if isinstance(state.get("governance"), dict):
        # Governance is persisted in the task state, so every lifecycle
        # response (not only the initial start response) carries the same
        # server-resolved mode and policy digest for coordinator inspection.
        response["governance"] = dict(state["governance"])
    if result is not None:
        response["result"] = result
        if result.get("decision") == "cancelled":
            response["output_policy"] = "silent"
            response["allowed_visible_events"] = ["user_message"]
            response["next_action"] = (
                "keep the plan approval pending and wait for a later user message; do not dispatch, revise, "
                "or emit cancellation commentary"
            )
    return _segregate_orchestration_output(response)


def _plan_approval_request_id(state: dict[str, Any], approval: dict[str, Any]) -> str:
    """Return the opaque request id bound to one pending plan revision."""
    pending_basis = approval.get("pending_basis")
    if not isinstance(pending_basis, dict):
        pending_basis = {
            key: approval.get(key)
            for key in (
                "pipeline_contract_version", "plan_revision", "plan_result_ref",
                "verified_predecessor_digest", "semantic_pipeline_version",
                "semantic_future_pipeline_digest",
            )
            if approval.get(key) is not None
        }
    seed = {
        "task_id": str(state.get("task_id") or ""),
        "pending_basis": pending_basis,
    }
    return "plan-approval-" + digest_text(canonical_json.dumps(seed))[:32]


def _orchestrate_start(params: dict[str, Any], transaction_path: Path, transaction: dict[str, Any]) -> dict[str, Any]:
    task = params.get("task")
    if not isinstance(task, dict):
        raise ValueError("start requires a task object")
    task_id = safe_id(str(task.get("task_id", "")))
    user_request = str(task.get("user_request", "")).strip()
    if not user_request:
        raise ValueError("start task.user_request is required")
    principal = str(params.get("principal", "")).strip()
    thread_id = str(params.get("thread_id", "")).strip()
    if not principal or not thread_id:
        raise ValueError("start requires principal and thread_id")
    host_capabilities = params.get("host_capabilities")
    if not isinstance(host_capabilities, dict):
        raise ValueError("start requires host_capabilities")
    waves, classification_preview = _normalize_orchestrate_waves(params.get("waves"), task, host_capabilities, str(params["project_root"]))
    # Reject a task that cannot build the immutable worker context before
    # activation/classification/task initialization leave durable records.
    _preflight_dispatch_context(task, {})
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
                    "user_request": user_request,
                    "requirements": task.get("requirements", []),
                    "pipeline": classification_preview["pipeline"],
                    "parallel_groups": classification_preview["parallel_groups"],
                })
                classification_id = classified["classification_id"]
                _checkpoint_orchestrate_transaction(transaction_path, transaction, "classified", classification_id=classification_id)
            # Only explicit start-level markers are trusted as a user request
            # for a visible plan review.  Nested ``task.plan_approval`` is a
            # policy/configuration field and must not manufacture user intent.
            trusted_start_plan_review = any(
                params.get(marker) is True or task.get(marker) is True
                for marker in (
                    "plan_approval_user_requested",
                    "user_requested_plan_approval",
                    "plan_review_requested",
                    "explicit_plan_approval_requested",
                )
            )
            created = init_task({
                **params,
                **task,
                "task_id": task_id,
                "user_request": user_request,
                "classification_id": classification_id,
                "plan_approval_user_requested": trusted_start_plan_review,
            })
            state = created["state"]
            _, task_dir, _ = task_paths(task_id, params)
            _checkpoint_orchestrate_transaction(transaction_path, transaction, "initialized", task_directory=task_dir.name)
        else:
            state = existing_state
            task_dir = existing_task_dir
            stored_task = load_task_definition(task_dir, state)
            if stored_task.get("user_request") != user_request:
                raise ValueError("existing task_id belongs to a different user_request")
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
            if digest_text(canonical_json.dumps(_orchestrate_wave_contract(existing_plan.get("waves")))) != digest_text(canonical_json.dumps(_orchestrate_wave_contract(waves))):
                raise ValueError("existing task has a different orchestration wave plan")
            plan = existing_plan
        else:
            _write_orchestrate_plan(task_dir, plan)
        # Keep the coordinator's route as the executable choice. Cortex's
        # classifier remains useful evidence, but it cannot silently replace
        # or append policy gates to that choice.
        chosen_pipeline = list(classification_preview.get("chosen_pipeline") or [
            gate for wave in waves for gate in wave.get("gates", [])
        ])
        chosen_groups = [list(wave.get("gates") or []) for wave in waves]
        recommended_pipeline = list(classification_preview.get("recommended_pipeline") or [])
        recommended_groups = [
            list(group) for group in classification_preview.get("recommended_parallel_groups") or []
        ]
        if existing_plan is not None:
            chosen_pipeline = list(plan.get("chosen_pipeline") or chosen_pipeline)
            chosen_groups = [list(group) for group in plan.get("chosen_parallel_groups") or chosen_groups]
            recommended_pipeline = list(plan.get("recommended_pipeline") or recommended_pipeline)
            recommended_groups = [
                list(group) for group in plan.get("recommended_parallel_groups") or recommended_groups
            ]
        plan.update({
            "chosen_pipeline": chosen_pipeline,
            "chosen_parallel_groups": chosen_groups,
            "recommended_pipeline": recommended_pipeline,
            "recommended_parallel_groups": recommended_groups,
            "pipeline_authority": "orchestrator",
        })
        _write_orchestrate_plan(task_dir, plan)
        state = _record_chosen_pipeline(
            task_dir,
            state,
            chosen_pipeline,
            chosen_groups,
            recommended_pipeline=recommended_pipeline,
            recommended_parallel_groups=recommended_groups,
            reason="Recorded orchestrator-selected pipeline; Cortex recommendation is advisory.",
        )
        # C2/C3 defaults may populate ``plan_approval=required`` for legacy
        # policy reasons. That default is not user intent. Only an explicit
        # request marker may make plan review visible and pause the pipeline.
        explicit_plan_review = bool(
            any(
                params.get(marker) is True or task.get(marker) is True
                for marker in (
                    "plan_approval_user_requested",
                    "user_requested_plan_approval",
                    "plan_review_requested",
                    "explicit_plan_approval_requested",
                )
            )
            or state.get("plan_approval_user_requested") is True
            or state.get("user_requested_plan_approval") is True
            or _plan_approval(state).get("user_requested") is True
        )
        approval = _plan_approval(state)
        approval["user_requested"] = explicit_plan_review
        if explicit_plan_review:
            approval["policy"] = "required"
            if approval.get("status") == "not_required":
                approval["status"] = "pending_plan"
        else:
            approval["policy"] = "auto"
            if approval.get("status") in {"pending_plan", "awaiting_user"}:
                approval["status"] = "not_required"
        state["plan_approval"] = approval
        state["plan_approval_user_requested"] = explicit_plan_review
        save_state(task_dir, task_dir / "state.sqlite", state, "plan_approval_policy", "recorded explicit plan-review intent")
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
    return digest_text(canonical_json.dumps(_semantic_future_pipeline(plan)))


def _semantic_pipeline_gates(pipeline: list[dict[str, Any]]) -> set[str]:
    """Return canonical gates represented by an approval-semantic pipeline."""
    return {
        str(worker.get("phase") or "")
        for wave in pipeline
        for worker in (wave.get("workers") or [])
        if isinstance(worker, dict) and str(worker.get("phase") or "")
    }


def _is_singleton_recovery_planner(wave: object) -> bool:
    """Identify a legacy Planner-shaped wave for diagnostics only."""
    if not isinstance(wave, dict):
        return False
    delegations = wave.get("delegations")
    return (
        wave.get("gates") == ["plan"]
        and isinstance(delegations, list)
        and len(delegations) == 1
        and isinstance(delegations[0], dict)
        and str(delegations[0].get("agent") or "") == "planner"
    )


def _recovery_contract(waves: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Project the recovery-relevant plan semantics without opaque metadata.

    This deliberately excludes generated wave/dispatch identifiers and default
    presentation text.  The circuit breaker must compare the actual route,
    strategy, and verification contract—not regenerated IDs or paraphrased
    failure prose.
    """
    pipeline: list[dict[str, Any]] = []
    strategy: list[dict[str, Any]] = []
    verification: list[dict[str, Any]] = []
    for wave in waves:
        if not isinstance(wave, dict):
            continue
        workers = [item for item in wave.get("delegations", []) if isinstance(item, dict)]
        if not workers:
            continue
        pipeline_workers: list[dict[str, Any]] = []
        for worker in workers:
            gate = str(worker.get("gate") or "")
            agent = str(worker.get("agent") or "")
            pipeline_workers.append({
                "gate": gate,
                "agent": agent,
                "allowed_paths": list(worker.get("allowed_paths") or []),
                "context_gates": list(worker.get("context_gates") or []),
                "context_files": list(worker.get("context_files") or []),
            })
            strategy.append({
                "gate": gate,
                "agent": agent,
                "objective": str(worker.get("objective") or ""),
                "strategy": str(worker.get("strategy") or ""),
                "requested_model": str(worker.get("requested_model") or ""),
                "requested_reasoning_effort": str(worker.get("requested_reasoning_effort") or ""),
            })
            verification.append({
                "gate": gate,
                "acceptance_criteria": list(worker.get("acceptance_criteria") or []),
                "verification": list(worker.get("verification") or []),
            })
        pipeline.append({"gates": list(wave.get("gates") or []), "workers": pipeline_workers})
    return {
        "pipeline": pipeline,
        "strategy": strategy,
        "verification": verification,
    }


def _recovery_baseline_from_gate(plan: dict[str, Any], gate: str) -> list[dict[str, Any]]:
    """Return the persisted route from the failed gate onward for comparison."""
    waves = [wave for wave in plan.get("waves", []) if isinstance(wave, dict)]
    for index, wave in enumerate(waves):
        if gate in {str(item) for item in wave.get("gates", [])}:
            return waves[index:]
    return waves


_RECOVERY_REMEDIATION_MARKERS = {
    "infrastructure": (
        "network", "transport", "connection", "service", "host", "mcp", "timeout", "rate limit",
    ),
    "environment": (
        "dependency", "permission", "binary", "configuration", "sandbox", "disk", "toolchain",
    ),
}
_RECOVERY_REMEDIATION_ACTIONS = (
    "repair", "remediate", "restore", "configure", "provision", "install", "grant", "restart", "resolve",
)

_NEGATION_WORDS = {"not", "no", "never", "without", "avoid", "dont", "don't"}


def _has_non_negated_term(text: str, term: str) -> bool:
    """Return whether *term* occurs as a standalone, non-negated phrase.

    Recovery and failure classification are safety-sensitive routing hints. A
    substring match such as ``network`` in ``networking`` or ``restart`` in
    ``do not restart`` must not release a circuit breaker or misclassify a
    failure. Negation scope is deliberately limited to the current clause so
    that ``do not restart; repair the network`` still recognises the repair.
    """
    escaped = re.escape(term)
    # Treat underscores as separators as well: host lifecycle reasons are
    # commonly serialized as ``native_worker_stopped_without_attempt_result``.
    # Keep letters, digits, apostrophes and hyphens as word characters so a
    # marker such as ``network`` does not match ``networking``.
    pattern = re.compile(rf"(?<![A-Za-z0-9'-]){escaped}(?![A-Za-z0-9'-])", re.IGNORECASE)
    for match in pattern.finditer(text):
        clause = re.split(r"[;,.!?\n]", text[:match.start()])[-1]
        words = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", clause.casefold())
        if any(word in _NEGATION_WORDS for word in words[-4:]):
            continue
        return True
    return False


def _has_matching_environment_remediation(
    pause: dict[str, Any],
    planner_wave: dict[str, Any],
) -> bool:
    """Accept an explicit, class-matched environment remediation in Planner.

    A generic "retry" is intentionally insufficient.  The plan must name an
    action and a condition that matches the paused infrastructure/environment
    class, so a coordinator cannot evade the circuit breaker with only new
    prose in its resume reason.
    """
    failure_class = str(pause.get("failure_class") or "").strip()
    markers = _RECOVERY_REMEDIATION_MARKERS.get(failure_class)
    if not markers:
        return False
    delegations = planner_wave.get("delegations") if isinstance(planner_wave, dict) else []
    planner = delegations[0] if isinstance(delegations, list) and delegations else {}
    if not isinstance(planner, dict):
        return False
    text = " ".join(
        str(value or "")
        for value in (
            planner.get("objective"),
            planner.get("strategy"),
            *(planner.get("acceptance_criteria") or []),
            *(planner.get("verification") or []),
        )
    ).casefold()
    return any(_has_non_negated_term(text, marker) for marker in markers) and any(
        _has_non_negated_term(text, action) for action in _RECOVERY_REMEDIATION_ACTIONS
    )


def _validate_no_progress_recovery_plan(
    state: dict[str, Any],
    plan: dict[str, Any],
    future_waves: object,
    *,
    pause: dict[str, Any] | None = None,
) -> None:
    """Record no-progress as advice; never make it a dispatch veto."""
    del state, plan, future_waves, pause
    return


def _validate_pending_implementation_retained(
    state: dict[str, Any],
    old_semantic_pipeline: list[dict[str, Any]],
    requested_future_gates: set[str],
    completed_gates: set[str],
    obligation_gates: set[str] | None = None,
) -> None:
    """Keep the historical check as an advisory-only compatibility seam."""
    del state, old_semantic_pipeline, requested_future_gates, completed_gates, obligation_gates
    return


_DELIVERY_RECOVERY_ORDER = (
    "implementation", "qa", "security", "performance", "review", "documentation", "close",
)
_IMPLEMENTATION_PROFILES = {
    "backend_dev", "data_engineer", "debugger", "devops_engineer", "frontend_dev",
    "fullstack_dev", "general", "mobile_dev", "refactorer",
}


def _pipeline_obligation_gates(
    state: dict[str, Any],
    plan: dict[str, Any],
    task: dict[str, Any] | None = None,
) -> set[str]:
    """Recover immutable delivery obligations, including pre-v9 task history."""
    obligations = {
        str(gate) for gate in state.get("pipeline_obligations", []) if str(gate)
    }
    if isinstance(task, dict):
        obligations.update(
            str(gate)
            for gate in (task.get("initial_pipeline") or task.get("base_pipeline") or [])
            if str(gate)
        )
    for change in state.get("pipeline_changes", []):
        if isinstance(change, dict):
            obligations.update(str(gate) for gate in change.get("from", []) if str(gate))
    for entry in plan.get("history", []):
        if isinstance(entry, dict):
            obligations.update(
                _semantic_pipeline_gates(entry.get("semantic_future_pipeline") or [])
            )
    obligations.update(str(gate) for gate in state.get("current_pipeline", []) if str(gate))
    # A blocked delivery attempt is itself durable evidence that the delivery
    # obligation still exists.  Older task states may not have copied the
    # implementation phase into ``pipeline_obligations`` before the worker
    # stopped, so relying only on the projected pipeline can manufacture a
    # planner-only recovery wave and then fail the retention invariant.  Keep
    # only non-invalidated, unfinished attempts here; completed attempts are
    # deliberately not allowed to re-enter the recovery graph implicitly.
    obligations.update(
        str(attempt.get("gate") or "")
        for attempt in state.get("attempts", [])
        if isinstance(attempt, dict)
        and str(attempt.get("gate") or "") == "implementation"
        and not attempt.get("invalidated")
        and str(attempt.get("status") or "") in {
            "blocked", "failed", "running", "waiting_question", AWAITING_HOST_SPAWN,
        }
    )
    return obligations


def _approved_plan_delivery_gap(
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Report delivery coverage as advice, never as executable obligations.

    An approved planning artifact may describe implementation work even when
    the orchestrator deliberately selected another route.  The old helper
    expanded that description into mandatory implementation/QA/review/docs/
    close gates, which could manufacture a recovery dispatch.  Keep the
    return shape for compatibility, but the first list is intentionally empty
    (there are no server-owned required gates); the second list is an advisory
    coverage report only.
    """
    if "plan" not in state.get("completed_gates", []):
        return [], []
    approval_status = str(_plan_approval(state).get("status") or "")
    if approval_status not in {"approved", "not_required"}:
        return [], []
    manifest = current_planning_manifest(task_dir)
    if not isinstance(manifest, dict) or not manifest.get("work_packages"):
        return [], []
    planning_requires_implementation = False
    for package_summary in manifest.get("work_packages", []):
        if not isinstance(package_summary, dict):
            continue
        artifact_path = str(package_summary.get("artifact_path") or "")
        if not artifact_path:
            continue
        try:
            package_record, _ = read_immutable_json_artifact(
                task_dir,
                state["task_id"],
                artifact_path,
                kinds={"planning_revision"},
            )
        except ValueError:
            planning_requires_implementation = True
            break
        package = package_record.get("package") if isinstance(package_record, dict) else None
        if not isinstance(package, dict):
            planning_requires_implementation = True
            break
        if any(
            str(microtask.get("profile") or "") in _IMPLEMENTATION_PROFILES
            for microtask in package.get("microtasks", [])
            if isinstance(microtask, dict)
        ):
            planning_requires_implementation = True
            break
        if any(
            str(path).strip() in {".", "*"}
            or not (
                str(path).replace("\\", "/").startswith("docs/")
                or str(path).lower().endswith((".md", ".mdx"))
            )
            for path in package.get("allowed_paths", [])
        ):
            planning_requires_implementation = True
            break
    if not planning_requires_implementation:
        return [], []
    recommended = ["implementation", "qa", "review", "documentation", "close"]
    passed = {
        str(attempt.get("gate") or "")
        for attempt in state.get("attempts", [])
        if attempt.get("status") == "passed"
        and not attempt.get("invalidated")
        and attempt.get("attempt_result_ref")
    }
    missing = [gate for gate in recommended if gate not in passed]
    advice = {
        "code": "approved_plan_delivery_coverage_advisory",
        "severity": "warning",
        "message": "The approved plan describes implementation work; delivery coverage is advisory and does not alter the orchestrator-selected pipeline.",
        "recommended_gates": recommended,
        "missing_gates": missing,
        "chosen_pipeline_unchanged": True,
    }
    existing = state.setdefault("pipeline_advice", [])
    if isinstance(existing, list) and advice not in existing:
        existing.append(advice)
    return [], missing


def _historical_recovery_specs(plan: dict[str, Any], gate: str) -> list[dict[str, Any]]:
    """Reuse the most recent semantic worker contract for one restored gate."""
    semantic_versions = [
        entry.get("semantic_future_pipeline") or []
        for entry in reversed(plan.get("history", []))
        if isinstance(entry, dict)
    ]
    semantic_versions.append(_semantic_future_pipeline(plan))
    for semantic in semantic_versions:
        workers = [
            worker
            for wave in semantic
            for worker in (wave.get("workers") or [])
            if isinstance(worker, dict) and worker.get("phase") == gate
        ]
        if workers:
            return [
                {
                    "gate": gate,
                    "agent": str(worker.get("profile") or _default_profile_for_gate(gate)),
                    **({"objective": worker["objective"]} if worker.get("objective") else {}),
                    **({"strategy": worker["strategy"]} if worker.get("strategy") else {}),
                    **({"allowed_paths": list(worker["paths"])} if worker.get("paths") else {}),
                    **({"context_gates": list(worker["dependencies"])} if isinstance(worker.get("dependencies"), list) else {}),
                    **({"context_files": list(worker["context_files"])} if worker.get("context_files") else {}),
                    **({"acceptance_criteria": list(worker["acceptance_criteria"])} if worker.get("acceptance_criteria") else {}),
                    **({"verification": list(worker["verification"])} if worker.get("verification") else {}),
                }
                for worker in workers
            ]
    return [{"gate": gate, "agent": _default_profile_for_gate(gate)}]


def _delivery_recovery_waves(
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    required_gates: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return recovery waves for the coordinator-selected route.

    Recovery used to manufacture a ``recovery-plan`` wave and then replay all
    historically required delivery gates.  That was a policy-owned pipeline
    replacement: a stale projection or a failed worker could silently change
    what the coordinator had chosen.  Recovery is now deliberately narrow:
    preserve the durable chosen/current frontier, reuse the matching worker
    contracts already present in the plan, and fall back to the current
    attempt's contract only when the projection has lost its wave details.

    ``required_gates`` is retained for callers that predate the chosen-pipeline
    fields.  It is only a fallback when no chosen/current frontier exists; it
    can never add a Planner wave or override a selected route.
    """
    del task_dir
    completed = {
        str(gate)
        for gate in (*state.get("completed_gates", []), *state.get("skipped_gates", []))
        if str(gate).strip()
    }
    unfinished_attempt_gates = [
        attempt.get("gate")
        for attempt in state.get("attempts", [])
        if isinstance(attempt, dict)
        and not attempt.get("invalidated")
        and str(attempt.get("status") or "") in {
            "blocked", "failed", "running", "waiting_question", AWAITING_HOST_SPAWN,
        }
    ]
    selected = [
        str(gate)
        for gate in (
            state.get("chosen_pipeline")
            or state.get("current_pipeline")
            or unfinished_attempt_gates
            or required_gates
        )
        if str(gate).strip() and str(gate) not in completed
    ]
    selected = list(dict.fromkeys(selected))
    if not selected:
        return []

    selected_set = set(selected)
    waves: list[dict[str, Any]] = []
    for index, wave in enumerate(plan.get("waves") or []):
        if not isinstance(wave, dict):
            continue
        delegations = [
            dict(item)
            for item in wave.get("delegations") or []
            if isinstance(item, dict)
            and str(item.get("gate") or "") in selected_set
            and str(item.get("gate") or "") not in completed
        ]
        if not delegations:
            continue
        waves.append({
            "wave_id": str(wave.get("wave_id") or f"retry-{index + 1}"),
            "delegations": delegations,
        })
    if waves:
        return waves

    # A partially written plan may have lost its wave projection.  Reuse the
    # last worker contract for exactly the selected frontier; this is a retry,
    # not permission to introduce a new planning or governance phase.
    return [
        {
            "wave_id": f"retry-{gate}",
            "delegations": _historical_recovery_specs(plan, gate),
        }
        for gate in selected
    ]


def _repair_delivery_graph_before_closure(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Leave delivery routing to the orchestrator's chosen pipeline.

    Historically this hook injected missing implementation/QA/review waves
    before close. That made a governance convention an execution veto and
    could strand a valid coordinator decision. Missing coverage remains
    available in diagnostics; it is not silently inserted into the route.
    """
    del params, task_dir
    return state, plan


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
    result_refs = _predecessor_context_result_refs(state, required_gates)
    basis: list[dict[str, str]] = []
    for result_ref in result_refs:
        attempt = next(
            (
                item for item in state.get("attempts", [])
                if isinstance(item, dict) and str(item.get("attempt_result_ref") or "") == result_ref
            ),
            None,
        )
        if attempt is None:
            raise ValueError("predecessor AttemptResult does not belong to this task")
        canonical = attempt_protocol.get_attempt_result(
            _ledger_root_for_artifact(task_dir),
            task_id=state["task_id"],
            attempt_id=str(attempt.get("attempt_id") or ""),
        )
        if (
            canonical is None
            or canonical.get("result_ref") != result_ref
            or canonical.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
        ):
            raise ValueError("predecessor AttemptResult is not finalized")
        basis.append({
            "phase": str(attempt.get("gate") or ""),
            "result_ref": result_ref,
            "content_digest": str(canonical.get("content_digest") or ""),
        })
    digest = digest_text(canonical_json.dumps(basis))
    return basis, digest


def _current_plan_basis(
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    result_ref: str,
) -> dict[str, Any]:
    manifest = current_planning_manifest(task_dir)
    if not isinstance(manifest, dict) or manifest.get("source_result_ref") != result_ref:
        raise ValueError("plan approval requires the current planning revision from the finalized planner result")
    predecessor_AttemptResults, predecessor_digest = _verified_plan_predecessor_basis(task_dir, state)
    return {
        "pipeline_contract_version": _pipeline_contract_version(state),
        "plan_revision": str(manifest.get("revision") or ""),
        "plan_result_ref": result_ref,
        "verified_predecessor_AttemptResults": predecessor_AttemptResults,
        "verified_predecessor_digest": predecessor_digest,
        "semantic_pipeline_version": int(plan.get("semantic_pipeline_version") or 1),
        "semantic_future_pipeline_digest": _semantic_future_pipeline_digest(plan),
    }


def _assert_approved_plan_fresh(
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> None:
    # Plan freshness is an advisory recommendation. The orchestrator owns
    # the chosen pipeline and may deliberately continue with a revised or
    # stale plan while recording the deviation in its durable audit trail.
    # Explicit user plan approval is handled only by the public approval
    # operation when the task itself requested that interaction.
    del task_dir, state, plan
    return


def _plan_review_payload(task_dir: Path, state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded coordinator-facing summary of the completed plan."""
    planner_attempts = [
        item for item in state.get("attempts", [])
        if item.get("gate") == "plan" and item.get("status") == "passed" and not item.get("invalidated")
    ]
    if not planner_attempts:
        raise ValueError("plan approval requires a passed planner result")
    planner_attempt = planner_attempts[-1]
    result_ref = safe_id(str(planner_attempt.get("attempt_result_ref") or ""))
    if not result_ref:
        raise ValueError("plan approval requires a persisted planner result")
    result = attempt_protocol.get_attempt_result(
        _ledger_root_for_artifact(task_dir),
        task_id=state["task_id"],
        attempt_id=planner_attempt["attempt_id"],
    )
    if (
        result is None
        or result.get("result_ref") != result_ref
        or result.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
    ):
        raise ValueError("plan approval requires a finalized canonical planner result")
    manifest = current_planning_manifest(task_dir)
    tracker = current_plan_tracker(task_dir, state)
    artifact_summary = None
    work_package_details: list[dict[str, Any]] = []
    verification_summary: list[str] = []
    if manifest and manifest.get("source_result_ref") == result_ref:
        artifact_summary = {
            "manifest_ref": "sqlite:task_documents/planning_current",
            # Plans always persist an explicit immutable revision-scoped path.
            "overview_path": manifest.get("overview_artifact_path") or "planning/overview.md",
            "revision": manifest.get("revision"),
            "tracker_ref": "sqlite:task_documents/plan_tracker_current",
            "work_packages": [
                {
                    "id": package.get("id"), "title": package.get("title"),
                    "depends_on": package.get("depends_on", []),
                    "microtask_count": package.get("microtask_count", 0),
                    "artifact_path": package.get("artifact_path"),
                }
                for package in manifest.get("work_packages", [])
                if isinstance(package, dict)
            ],
        }
        for package_summary in manifest.get("work_packages", []):
            if not isinstance(package_summary, dict) or not str(package_summary.get("artifact_path") or ""):
                continue
            package_record, _ = read_immutable_json_artifact(
                task_dir,
                state["task_id"],
                str(package_summary["artifact_path"]),
                kinds={"planning_revision"},
            )
            package = package_record.get("package") if isinstance(package_record, dict) else None
            if not isinstance(package, dict):
                raise ValueError("plan approval work package artifact is invalid")
            microtasks = []
            for microtask in package.get("microtasks", []):
                if not isinstance(microtask, dict):
                    continue
                checks = [redact(item, 1000) for item in microtask.get("verification", [])]
                verification_summary.extend(checks)
                microtasks.append({
                    "id": microtask.get("id"),
                    "title": microtask.get("title"),
                    "objective": redact(microtask.get("objective", ""), 1600),
                    "profile": microtask.get("profile"),
                    "allowed_paths": list(microtask.get("allowed_paths") or package.get("allowed_paths") or []),
                    "depends_on": list(microtask.get("depends_on") or []),
                    "acceptance_criteria": [redact(item, 1000) for item in microtask.get("acceptance_criteria", [])],
                    "verification": checks,
                })
            work_package_details.append({
                "id": package.get("id"),
                "title": package.get("title"),
                "objective": redact(package.get("objective", ""), 1600),
                "allowed_paths": list(package.get("allowed_paths") or []),
                "depends_on": list(package.get("depends_on") or []),
                "microtasks": microtasks,
            })
    task = load_task_definition(task_dir, state)
    basis = _current_plan_basis(task_dir, state, plan, result_ref=result_ref)
    return {
        "result_ref": result_ref,
        **basis,
        # This payload is persisted while the orchestration transaction is
        # held.  It is a generated summary of the canonical AttemptResult.
        "phase": planner_attempt.get("gate", "plan"),
        "user_request": redact(task.get("user_request", ""), 2400),
        "user_intent_artifact_ref": task.get("user_intent_artifact_ref"),
        "summary": redact(result["summary"], 2400),
        "work_packages": work_package_details,
        "verification": list(dict.fromkeys(verification_summary)),
        "findings": [redact(item, 1000) for item in result.get("findings", [])],
        "uncertainty": [redact(item, 1000) for item in result.get("unresolved", [])],
        "remaining_phases": list(active_gates(state)),
        "recommendation": (manifest or {}).get("recommendation", "approve"),
        "recommendation_rationale": redact((manifest or {}).get("recommendation_rationale", ""), 2400),
        "requirement_coverage": list((manifest or {}).get("requirement_coverage") or []),
        "resolved_questions": list((manifest or {}).get("resolved_questions") or []),
        "risks": [redact(item, 1000) for item in (manifest or {}).get("risks", [])],
        "plan_tracker": (
            {
                "revision": tracker.get("revision"),
                "items": [
                    {
                        "id": item.get("id"), "kind": item.get("kind"),
                        "status": item.get("status"), "order": item.get("order"),
                        "gates": list(item.get("gates") or []),
                        "depends_on": list(item.get("depends_on") or []),
                        "title": item.get("title"),
                    }
                    for item in tracker.get("items", []) if isinstance(item, dict)
                ],
            }
            if isinstance(tracker, dict) else None
        ),
        **({"planning_artifacts": artifact_summary} if artifact_summary else {}),
    }


def _materialize_response_result_projection(params: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """Return transactional output without creating a second result authority."""
    del params
    return _segregate_orchestration_output(response)


def _plan_approval_user_requested(state: dict[str, Any]) -> bool:
    """Return whether the task explicitly requested a visible plan review."""
    approval = _plan_approval(state)
    return bool(
        approval.get("user_requested")
        or state.get("plan_approval_user_requested")
        or state.get("user_requested_plan_approval")
    )


def _hold_for_plan_approval(task_dir: Path, state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
    """Persist the post-plan human gate before any successor is prepared."""
    approval = _plan_approval(state)
    if (
        approval.get("policy") != "required"
        or not _plan_approval_user_requested(state)
        or "plan" not in state.get("completed_gates", [])
        or not active_gates(state)
        or state.get("status") != "active"
    ):
        return None
    if approval.get("status") == "approved":
        # Older ledgers can retain an approved basis while a replacement
        # planner result has already become ``planning_current``.  This is
        # most visible when that replacement result is completion-pending:
        # consuming it used to reach _assert_approved_plan_fresh and fail on
        # the old result ref.  Recover inside this state transaction by
        # retiring the old approval and immediately producing a review for the
        # current immutable planner revision.
        manifest = current_planning_manifest(task_dir)
        approved_result_ref = safe_id(str(approval.get("plan_result_ref") or ""))
        current_result_ref = safe_id(
            str(manifest.get("source_result_ref") or "")
        ) if isinstance(manifest, dict) else ""
        if current_result_ref and current_result_ref != approved_result_ref:
            invalidate_plan_approval_for_reopened_plan(
                state,
                reason=(
                    "Recovered a stale approved plan after a replacement planner result "
                    "became the current planning revision."
                ),
                event="stale_approved_recovery",
            )
            approval = _plan_approval(state)
        else:
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
        "plan_result_ref": review["result_ref"],
        "pending_basis": {key: review[key] for key in (
            "pipeline_contract_version", "plan_revision", "plan_result_ref",
            "verified_predecessor_digest", "semantic_pipeline_version",
            "semantic_future_pipeline_digest",
        )},
        "requested_at": now(),
    })
    approval["request_id"] = _plan_approval_request_id(state, approval)
    state["plan_approval"] = approval
    save_state(task_dir, task_dir / "state.sqlite", state, "plan_approval", "awaiting explicit user approval of the completed plan")
    return review


def _validate_retry_strategy(
    state: dict[str, Any],
    attempt: dict[str, Any],
    completion: dict[str, Any],
) -> None:
    # Strategy remains optional for an ordinary evidence-backed retry. Once
    # repeated failures are recorded, recovery still retries the selected
    # route. The coordinator may provide a new strategy, but Cortex does not
    # require a Planner or a user-authored replan to make progress.
    return


def _normalized_failure_reason(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _failure_class_from_completion(completion: dict[str, Any]) -> str:
    """Classify a non-success host outcome without trusting worker routing.

    Public v3 completions intentionally expose only a reason, not an
    authority-bearing failure-class field.  This conservative projection is
    used exclusively for liveness messaging and evidence grouping; it never
    changes a gate decision or weakens a canonical gate result.
    """
    reason = _normalized_failure_reason(completion.get("reason"))
    categories = (
        ("infrastructure", ("infrastructure", "network", "transport", "connection", "timeout", "rate limit", "service unavailable", "host unavailable", "mcp")),
        ("environment", ("environment", "dependency", "permission", "missing binary", "configuration", "sandbox", "disk full", "toolchain")),
        ("policy", ("policy", "governance", "authorization", "forbidden")),
        # Hook-produced stop reasons use the stable compound token below.  It
        # must be explicit because the standalone-word matcher intentionally
        # does not treat ``worker`` inside ``native_worker`` as a match.
        ("worker", ("native_worker_stopped_without_result", "worker", "agent stopped", "child stopped", "lease expired")),
    )
    for failure_class, markers in categories:
        if any(_has_non_negated_term(reason, marker) for marker in markers):
            return failure_class
    return "product"


def _corrective_evidence(
    root: Path,
    state: dict[str, Any],
    gate: str,
    gate_attempts: list[dict[str, Any]],
    completions: list[dict[str, Any]],
    unresolved_rework: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the bounded, durable evidence projection for retry liveness.

    The circuit breaker compares finding fingerprints, the dispatch manifest
    baseline, result observations, verification contract and failure class.
    A changed workspace, verification contract, result, or strategy starts a
    fresh run; it is not treated as a no-progress loop.
    """
    # Corrective liveness is owned by the gate being retried.  A finding from
    # a sibling verifier is useful task evidence, but must never reset or
    # advance this gate's circuit-breaker signature.
    finding_fingerprints = {
        str(item.get("fingerprint") or "").strip()
        for item in unresolved_rework
        if isinstance(item, dict) and str(item.get("fingerprint") or "").strip()
    }
    for source_gate, rework in (state.get("closure_rework") or {}).items():
        if (
            source_gate != gate
            or not isinstance(rework, dict)
            or rework.get("status") != "rework_required"
        ):
            continue
        finding_fingerprints.update(
            str(item).strip() for item in rework.get("finding_fingerprints") or [] if str(item).strip()
        )
    try:
        for item in db_list_task_findings(root, state["task_id"], include_resolved=False):
            if not isinstance(item, dict) or not str(item.get("fingerprint") or "").strip():
                continue
            sources = item.get("source_evidence")
            origin_gate = next(
                (
                    str(source.get("gate") or "")
                    for source in sources
                    if isinstance(source, dict) and source.get("transition") == "opened"
                    and str(source.get("gate") or "")
                ),
                "",
            ) if isinstance(sources, list) else ""
            if origin_gate == gate:
                finding_fingerprints.add(str(item["fingerprint"]).strip())
    except (OSError, ValueError):
        # A liveness safeguard must never turn an unavailable diagnostic
        # projection into a synthetic gate decision.  The remaining immutable
        # attempt evidence is still sufficient to detect exact repeats.
        pass
    relevant_attempts = [item for item in gate_attempts if not item.get("invalidated")]
    manifest_digests = sorted({
        str(item.get("result_baseline_digest") or state.get("initial_manifest_digest") or "").strip()
        for item in relevant_attempts
        if str(item.get("result_baseline_digest") or state.get("initial_manifest_digest") or "").strip()
    })
    verification_contract = [
        {
            "acceptance_criteria": list(item.get("acceptance_criteria") or []),
            "verification": list(item.get("verification") or []),
        }
        for item in relevant_attempts
    ]
    raw_reasons = [
        _normalized_failure_reason(item.get("reason"))
        for item in completions
        if str(item.get("status") or "").lower() != "passed"
    ]
    # A passed worker result with unresolved canonical findings is still a
    # corrective non-progress observation.  Its canonical fingerprint set is
    # safer than a mutable worker summary and remains stable across retries.
    if not raw_reasons and finding_fingerprints:
        raw_reasons = ["unresolved canonical findings"]
    failure_classes = sorted({
        _failure_class_from_completion(item)
        for item in completions
        if str(item.get("status") or "").lower() != "passed"
    })
    if not failure_classes and finding_fingerprints:
        failure_classes = ["product"]
    failure_observations = [
        {"status": status_value, "failure_class": failure_class}
        for status_value, failure_class in sorted({
            (
                str(item.get("status") or "").strip().lower(),
                _failure_class_from_completion(item),
            )
            for item in completions
            if str(item.get("status") or "").lower() != "passed"
        })
    ]
    if not failure_observations and finding_fingerprints:
        failure_observations = [{"status": "canonical_rework", "failure_class": "product"}]
    strategy_values = sorted({
        str(item.get("next_strategy") or item.get("strategy") or "default").strip()
        for item in relevant_attempts + completions
        if str(item.get("next_strategy") or item.get("strategy") or "default").strip()
    })
    evidence = {
        "gate": gate,
        "finding_fingerprints": sorted(finding_fingerprints),
        "manifest_digests": manifest_digests,
        # Free-text host reasons are audit evidence, not stable state-machine
        # identity. Equivalent failures frequently differ only in agent or
        # transport phrasing; grouping them by outcome/failure class prevents
        # that prose churn from bypassing the no-progress circuit breaker.
        "result_digest": digest_text(canonical_json.dumps(failure_observations)),
        "verification_digest": digest_text(canonical_json.dumps(verification_contract)),
        "failure_classes": failure_classes or ["product"],
        "strategy_digest": digest_text(canonical_json.dumps(strategy_values)),
    }
    evidence["signature"] = digest_text(canonical_json.dumps(evidence))
    # Keep a privacy-preserving audit trail that can explain why two attempts
    # were grouped, but do not let mutable prose participate in the liveness
    # identity calculated immediately above.
    evidence["reason_audit_digest"] = digest_text(canonical_json.dumps(raw_reasons))
    return evidence


def _active_no_progress_pauses(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return advisory retry records, never a pipeline-pausing state."""
    raw = state.get("rework_pauses")
    pauses = {
        str(gate): dict(pause)
        for gate, pause in raw.items()
        if isinstance(pause, dict) and pause.get("status") == "retry_pending"
        and str(gate).strip()
    } if isinstance(raw, dict) else {}
    return pauses


def _normalize_no_progress_pause_state(state: dict[str, Any]) -> bool:
    """Migrate legacy Planner pauses into non-blocking retry advice."""
    raw = state.get("rework_pauses")
    if not isinstance(raw, dict):
        return False
    changed = False
    for pause in raw.values():
        if isinstance(pause, dict) and pause.get("status") == "planner_recovery_pending":
            pause["status"] = "retry_pending"
            pause["advisory"] = True
            changed = True
    if changed:
        _store_no_progress_pauses(state, raw)
    return changed


def _store_no_progress_pauses(state: dict[str, Any], pauses: dict[str, dict[str, Any]]) -> None:
    """Persist gate-scoped pauses."""
    active = {
        str(gate): dict(pause)
        for gate, pause in pauses.items()
        if isinstance(pause, dict) and pause.get("status") in {"planner_recovery_pending", "retry_pending"}
    }
    if active:
        state["rework_pauses"] = active
    else:
        state.pop("rework_pauses", None)


def _record_corrective_progress(
    root: Path,
    state: dict[str, Any],
    gate: str,
    gate_attempts: list[dict[str, Any]],
    completions: list[dict[str, Any]],
    unresolved_rework: list[dict[str, Any]],
    *,
    outcome: str,
) -> dict[str, Any] | None:
    """Update no-progress evidence and return a pause only for exact repeats."""
    progress = state.setdefault("rework_progress", {})
    if not isinstance(progress, dict):
        progress = {}
        state["rework_progress"] = progress
    if outcome in {"passed", "skipped"}:
        progress.pop(gate, None)
        return None
    if outcome != "failed":
        return None
    evidence = _corrective_evidence(root, state, gate, gate_attempts, completions, unresolved_rework)
    prior = progress.get(gate) if isinstance(progress.get(gate), dict) else {}
    same = prior.get("signature") == evidence["signature"]
    consecutive = int(prior.get("consecutive_identical_iterations") or 0) + 1 if same else 1
    event = {
        **evidence,
        "consecutive_identical_iterations": consecutive,
        "last_observed_at": now(),
    }
    history = list(prior.get("history") or []) if isinstance(prior, dict) else []
    history.append({
        "signature": evidence["signature"],
        "failure_classes": evidence["failure_classes"],
        "result_digest": evidence["result_digest"],
        "manifest_digests": evidence["manifest_digests"],
        "at": event["last_observed_at"],
    })
    event["history"] = history[-16:]
    progress[gate] = event
    if consecutive < _NO_PROGRESS_REPEAT_LIMIT:
        return None
    failure_class = str(evidence["failure_classes"][0] or "product")
    recovery = (
        "retry the orchestrator-selected route and record the infrastructure/environment remediation"
        if failure_class in {"infrastructure", "environment"}
        else "retry the orchestrator-selected route and delegate the correction to the responsible worker"
    )
    return {
        "status": "retry_pending",
        "advisory": True,
        "gate": gate,
        "consecutive_identical_iterations": consecutive,
        "repeat_limit": _NO_PROGRESS_REPEAT_LIMIT,
        "failure_class": failure_class,
        "finding_fingerprints": evidence["finding_fingerprints"],
        "manifest_digests": evidence["manifest_digests"],
        "result_digest": evidence["result_digest"],
        "verification_digest": evidence["verification_digest"],
        "strategy_digest": evidence["strategy_digest"],
        "required_recovery": recovery,
        "paused_at": now(),
    }


def _apply_pending_revision_impact(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Apply a durable semantic steer impact only after its worker completes.

    ``cortex._v3_active_steer`` keeps the native worker alive and writes this
    receipt.  The engine consumes it at the next safe gate boundary, reopening
    the earliest affected gate and every downstream receipt atomically before
    it can dispatch a stale successor.
    """
    impact = state.get("pending_revision_impact")
    if not isinstance(impact, dict):
        return state, False
    earliest = str(impact.get("earliest_affected_gate") or "").strip()
    # Older durable impacts may contain ``replacement_waves`` from the former
    # policy-owned steer implementation.  Never replay that payload: it can
    # overwrite the coordinator's chosen route after the fact.  Retain a
    # bounded advisory record for diagnostics and continue with the selected
    # pipeline below.
    replacement = impact.pop("replacement_waves", None)
    if isinstance(replacement, list) and replacement:
        state.setdefault("pipeline_advice", []).append({
            "code": "legacy_replacement_waves_ignored",
            "severity": "warning",
            "message": "Ignored a legacy policy replacement route; the orchestrator-selected pipeline remains authoritative.",
            "wave_count": len(replacement),
            "at": now(),
        })
    if not earliest or earliest not in state.get("current_pipeline", []):
        state.pop("pending_revision_impact", None)
        return state, False
    change = apply_pipeline_operations(
        state,
        operations=[{"op": "rework", "gate": earliest}],
        allow_rework=True,
    )
    append_pipeline_change(
        state,
        change,
        "Applied the durable semantic impact of a completed active-task steer.",
        [
            f"task_revision={impact.get('task_revision')}",
            *[str(item) for item in impact.get("categories") or []],
        ],
    )
    invalidate_reworked_result_bindings(task_dir, state)
    # Semantic steering invalidates the meaning of every outstanding
    # corrective route, not merely the attempts it happened to reopen.  Keep
    # the route for audit history, but remove it from current handoff and
    # resolution eligibility; a new verifier finding will create a route for
    # the new task revision.
    for rework in (state.get("closure_rework") or {}).values():
        if isinstance(rework, dict) and rework.get("status") == "rework_required":
            rework.update({
                "status": "superseded",
                "superseded_at": now(),
                "superseded_by_task_revision": impact.get("task_revision"),
            })
    sync_current_wave(state)
    if active_gates(state):
        state["status"] = "active"
    applied = {**impact, "applied_at": now(), "reset_gates": list(change.get("reset_gates") or [])}
    state.setdefault("applied_revision_impacts", []).append(applied)
    state["applied_revision_impacts"] = state["applied_revision_impacts"][-32:]
    state.pop("pending_revision_impact", None)
    save_state(
        task_dir,
        task_dir / "state.sqlite",
        state,
        "semantic_revision_impact",
        f"reopened {earliest} and downstream gates after task revision {impact.get('task_revision')}",
    )
    return state, True


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
            # AttemptResults its effective model and it is checked against the durable
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
        result_ref = str(completion.get("attempt_result_ref") or "").strip()
        if not result_ref:
            raise ValueError("passed completion requires attempt_result_ref from complete_attempt")
        if result_ref != str(attempt.get("attempt_result_ref") or ""):
            raise ValueError("passed completion must select the canonical result for its exact attempt")
        result = attempt_protocol.get_attempt_result(
            _ledger_root_for_artifact(task_dir),
            task_id=state["task_id"], attempt_id=attempt_id,
        )
        if (
            result is None
            or str(result.get("result_ref") or "") != result_ref
            or str(result.get("lifecycle_status") or "") != attempt_protocol.LIFECYCLE_COMPLETED
        ):
            raise ValueError("passed completion requires a finalized canonical attempt result")
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
    # public handoff seam at call time so host integrations (and stable
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
        return state, None
    result_ref = str(completion.get("attempt_result_ref") or "").strip()
    if requested_status == "passed" and result_ref:
        if result_ref != str(attempt.get("attempt_result_ref") or ""):
            raise ValueError("attempt_result_ref does not belong to the active worker attempt")
        canonical = attempt_protocol.get_attempt_result(
            _ledger_root_for_artifact(task_dir),
            task_id=state["task_id"], attempt_id=attempt_id,
        )
        if canonical is None or canonical.get("result_ref") != result_ref or canonical.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED:
            raise ValueError("attempt_result_ref is not a finalized canonical result")
        attempt["completion_transport_status"] = "not_applicable"
        attempt["gate_decision"] = "completed"
        if attempt.get("status") == AWAITING_HOST_SPAWN:
            attempt["status"] = "running"
            attempt["dispatch_correlation"] = "worker_result_received"
            attempt["expected_route"] = {
                "tool": (attempt.get("spawn_request") or {}).get("host_tool") or "spawn_agent",
                "model": (attempt.get("spawn_request") or {}).get("model"),
                "expected_model": (attempt.get("spawn_request") or {}).get("expected_model") or attempt.get("expected_model"),
                "reasoning_effort": (attempt.get("spawn_request") or {}).get("reasoning_effort"),
            }
        package = _delegation_package(task_dir, state["task_id"], attempt_id)
        package["spawn_status"] = "worker_result_received"
        package["dispatch_correlation"] = "worker_result_received"
        package["lifecycle_status"] = canonical.get("lifecycle_status")
        package["attempt_status"] = attempt.get("status")
        package["attempt_result_ref"] = result_ref
        _write_delegation_package(task_dir, state["task_id"], attempt_id, package)
        # The coordinator may be completing a stale in-memory snapshot while
        # lifecycle recovery has already retired the mutable attempt in
        # SQLite.  Merge that server-owned retirement marker before the first
        # projection write; otherwise this write would resurrect the old
        # attempt and hide the exact race that recovery is meant to reconcile.
        durable = db_load_task(
            _ledger_root_for_artifact(task_dir), str(state["task_id"]),
        )
        if durable is not None:
            durable_attempt = next(
                (
                    item for item in (durable[1].get("attempts") or [])
                    if isinstance(item, dict)
                    and str(item.get("attempt_id") or "") == attempt_id
                ),
                None,
            )
            if isinstance(durable_attempt, dict) and durable_attempt.get("invalidated"):
                attempt["invalidated"] = True
                for field in ("invalidated_at", "invalidation_reason"):
                    if durable_attempt.get(field) is not None:
                        attempt[field] = durable_attempt[field]
        save_state(task_dir, task_dir / "state.sqlite", state, "worker_result", attempt_id)
        try:
            finalized = finalize_attempt({
                **params,
                "task_id": state["task_id"],
                "attempt_id": attempt_id,
                "expected_revision": state["revision"],
                "status": "passed",
            })
            if finalized.get("recorded") is False:
                raise ValueError(str(finalized.get("reason") or "worker result attempt finalization failed"))
            finalized_state = finalized["state"]
            finalized_attempt = _attempt(finalized_state, attempt_id)
            if finalized.get("recovered_invalidated_attempt"):
                # The protocol result is already immutable and complete.  Do
                # not resurrect or mutate the retired attempt in the protocol
                # ledger; only reconcile the current orchestration projection.
                finalized_state = state
                finalized_attempt = _attempt(finalized_state, attempt_id)
                finalized_attempt["status"] = "passed"
                finalized_attempt["lifecycle_status"] = attempt_protocol.LIFECYCLE_COMPLETED
                finalized_attempt["completion_transport_status"] = "not_applicable"
                finalized_attempt["gate_decision"] = "completed"
                finalized_attempt["finalized_at"] = canonical.get("completed_at") or canonical.get("updated_at") or now()
                finalized_attempt["reconciled_from_canonical_result"] = True
                save_state(
                    task_dir,
                    task_dir / "state.sqlite",
                    finalized_state,
                    "canonical_result_reconciled",
                    f"reconciled immutable AttemptResult for {attempt_id} without mutating the invalidated protocol attempt",
                )
        except ValueError as exc:
            # A completed canonical result may outlive a retry/rework
            # transition that invalidated the mutable attempt projection.
            # The result is immutable and already authoritative; trying to
            # mutate that old attempt again is precisely the technical
            # condition that previously leaked as a Cortex blocker.  Reconcile
            # only the orchestration projection and continue through the
            # normal gate/recovery path.  No worker replacement or user
            # decision is involved.
            if "invalidated attempt" not in str(exc).lower():
                raise
            finalized_state = state
            finalized_attempt = _attempt(finalized_state, attempt_id)
            finalized_attempt["status"] = "passed"
            finalized_attempt["lifecycle_status"] = attempt_protocol.LIFECYCLE_COMPLETED
            finalized_attempt["completion_transport_status"] = "not_applicable"
            finalized_attempt["gate_decision"] = "completed"
            finalized_attempt["finalized_at"] = canonical.get("completed_at") or canonical.get("updated_at") or now()
            finalized_attempt["reconciled_from_canonical_result"] = True
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                finalized_state,
                "canonical_result_reconciled",
                f"reconciled immutable AttemptResult for {attempt_id} without mutating the invalidated protocol attempt",
            )
        _record_server_corrective_receipts(
            _ledger_root_for_artifact(task_dir),
            finalized_state,
            finalized_attempt,
            result_ref,
        )
        package = _delegation_package(task_dir, state["task_id"], attempt_id)
        package["lifecycle_status"] = canonical.get("lifecycle_status")
        package["attempt_status"] = finalized_attempt.get("status")
        _write_delegation_package(task_dir, state["task_id"], attempt_id, package)
        return finalized_state, None
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
        raise ValueError("passed completion requires attempt_result_ref from complete_attempt")
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
    terminal_attempt = _attempt(finalized["state"], attempt_id)
    terminal_attempt["completion_transport_status"] = "not_recorded"
    terminal_attempt["gate_decision"] = requested_status
    save_state(
        task_dir,
        task_dir / "state.sqlite",
        finalized["state"],
        "attempt_semantic_status",
        f"{attempt_id}: transport=not_recorded decision={requested_status}",
    )
    return finalized["state"], None


def _ensure_attempt_evidence(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    attempt: dict[str, Any],
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
        "attempt_result_ref": attempt.get("attempt_result_ref"),
        "summary": f"Unified facade accepted the {attempt['gate']} result from {attempt['agent']}",
        "paths": [],
    }
    attempt_gate = str(attempt.get("gate") or "")
    governance_obligations = (
        list(_governance_obligations_for_gate(state, attempt_gate))
        if attempt_gate in {"governance_activation", "governance_close"}
        else []
    )
    if governance_obligations:
        # Public workers submit only canonical attempt results.  The server
        # therefore owns the typed governance-evidence projection that binds
        # the canonical result binding, immutable evidence artifact, verified
        # execution, scope, and independent reviewer identity.  Never accept
        # these authority-bearing fields from the parent result payload.
        reviewer_identity = str(
            attempt.get("host_agent_id")
            or attempt.get("dispatch_ref")
            or attempt.get("attempt_id")
            or ""
        ).strip()
        evidence_params.update({
            "governance_obligations": governance_obligations,
            "reviewer_identity": reviewer_identity,
            "reviewer_role": str(attempt.get("agent") or ""),
            "independent_reviewer": True,
        })
    if attempt["gate"] == "documentation":
        changed_files: list[str] = []
        evidence_params.update({
            "kind": "documentation",
            "decision": "updated" if changed_files else "not_applicable",
            "justification": "The documentation result contains no server-observed changed files." if not changed_files else "The documentation result contains changed files.",
            "paths": changed_files,
        })
        result = record_evidence(evidence_params)
    elif governance_obligations:
        result = execute_verification({
            **evidence_params,
            "verification_id": "benign_success",
        })
    elif command:
        result = execute_verification({**evidence_params, "verification_id": "benign_success"})
    else:
        result = record_evidence({**evidence_params, "kind": "result"})
    if result.get("recorded") is False:
        raise ValueError(str(result.get("reason") or "attempt evidence was not recorded"))
    return result["state"]


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
    completed_set = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    new_semantic_gates = _semantic_pipeline_gates(_semantic_future_pipeline(candidate_plan))
    _validate_pending_implementation_retained(
        state,
        old_semantic_pipeline,
        new_semantic_gates,
        completed_set,
        _pipeline_obligation_gates(state, plan, task),
    )
    approval_before = _plan_approval(state)
    explicit_plan_review = bool(
        _plan_approval_user_requested(state)
        or params.get("plan_approval_user_requested")
        or params.get("user_requested_plan_approval")
        or params.get("plan_review_requested")
    )
    # A changed future route is the orchestrator's chosen decision. Keep the
    # previous approval in history for audit, but never turn a policy refresh
    # into a second user-facing approval gate.
    # A material route change only reopens a plan review when the user
    # explicitly requested visible plan approval.  Governance defaults and
    # legacy ``policy=required`` values are internal recommendations; they
    # cannot turn a coordinator-selected future-wave update into a Cortex
    # pause.
    invalidate_approval = bool(semantic_changed and explicit_plan_review)
    requested_future_gates = {gate for wave in future for gate in wave["gates"]}
    rework_gates = sorted(completed_set & requested_future_gates)
    # Reopening/reordering a completed gate is valid coordinator control flow;
    # the server records the rework and preserves the immutable old evidence.
    rework_request_digest = digest_text(canonical_json.dumps({
        "completed_gate_rework": rework_gates,
        "future_pipeline": _semantic_future_pipeline(candidate_plan),
    })) if rework_gates else ""
    rework_history = state.get("rework_history")
    rework_history = [
        dict(item) for item in rework_history
        if isinstance(item, dict) and str(item.get("request_digest") or "").strip()
    ] if isinstance(rework_history, list) else []
    matching_history = next(
        (item for item in reversed(rework_history)
         if str(item.get("request_digest") or "") == rework_request_digest),
        None,
    ) if rework_request_digest else None
    # A completed gate is not a license to repeatedly reopen the same plan.
    # The first semantically identical request is a safe no-op.  A materially
    # changed request is admitted once (and may begin with any executable
    # worker selected by the orchestrator); replaying
    # that same material digest is paused before update_pipeline can invalidate
    # any receipt or mint a successor attempt. Failed-result retries do not
    # enter this path because their gate is not in completed_set.
    if matching_history is not None:
        if matching_history.get("material_change"):
            # This is an idempotent replay of a material rework that the
            # server has already accepted.  It is not a user decision and it
            # must not reopen the same gate or mint another worker.  Keep a
            # durable reconciliation receipt; the already accepted
            # The chosen route remains the sole owner of any next dispatch.
            state.pop("blocked_reason", None)
            state.pop("rework_pauses", None)
            state["status"] = "active" if state.get("status") in {"blocked", "needs_input"} else state.get("status", "active")
            state.setdefault("rework_recovery", {})
            state["rework_recovery"].update({
                "schema": "cortex/recovery-contract/v1",
                "mode": "chosen_pipeline",
                "status": "idempotent_replay",
                "request_digest": rework_request_digest,
                "rework_gates": list(rework_gates),
                "replacement_worker_authorized": False,
                "at": now(),
            })
            state.setdefault("rework_history", []).append({
                "request_digest": rework_request_digest,
                "rework_gates": list(rework_gates),
                "semantic_future_pipeline_digest": new_semantic_digest,
                "material_change": True,
                "outcome": "idempotent_replay",
                "at": now(),
            })
            state["rework_history"] = [
                item for item in state["rework_history"][-32:]
                if isinstance(item, dict)
            ]
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "rework_reconciled",
                "reconciled repeated material rework to the existing server-owned Planner route",
            )
            return state, plan
        raise ReworkRequestIdempotent(state, plan, rework_request_digest)
    if rework_request_digest and not semantic_changed:
        state.setdefault("rework_history", []).append({
            "request_digest": rework_request_digest,
            "rework_gates": list(rework_gates),
            "semantic_future_pipeline_digest": new_semantic_digest,
            "material_change": False,
            "outcome": "idempotent",
            "at": now(),
        })
        state["rework_history"] = [
            item for item in state["rework_history"][-32:]
            if isinstance(item, dict)
        ]
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "rework_idempotent",
            "recorded identical completed-gate rework without invalidation",
        )
        raise ReworkRequestIdempotent(state, plan, rework_request_digest)
    material_rework = bool(semantic_changed)
    if semantic_changed and not explicit_plan_review:
        advice = {
            "code": "material_pipeline_change_advisory",
            "severity": "warning",
            "message": "The coordinator selected a materially different future route; no user plan approval was requested, so execution continues with the chosen pipeline.",
            "recommended_next": "record_or_review_plan_evidence",
            "chosen_pipeline_unchanged": True,
            "at": now(),
        }
        existing_advice = state.setdefault("pipeline_advice", [])
        if isinstance(existing_advice, list) and advice not in existing_advice:
            existing_advice.append(advice)
    # Planner recovery is a recommendation only. The selected future
    # waves may begin with whichever executable worker the orchestrator chose.
    if rework_gates:
        # Rework is a chosen-pipeline decision. Retain immutable evidence but
        # clear only the explicitly reopened gates; no policy-owned or
        # governance promotion is inserted by the backend.
        completed_set -= set(rework_gates)
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
    chosen_future_pipeline = [gate for wave in future for gate in wave.get("gates", [])]
    for gate in chosen_future_pipeline:
        if gate not in full_pipeline:
            full_pipeline.append(gate)
    full_groups = [[gate] for gate in full_pipeline if gate in completed_set] + [list(wave["gates"]) for wave in future]
    normalized_current_groups = [list(group) for group in state.get("parallel_groups") or []]
    normalized_future_groups = [list(group) for group in full_groups]
    pipeline_or_group_change = (
        full_pipeline != state["current_pipeline"]
        or normalized_future_groups != normalized_current_groups
    )
    if pipeline_or_group_change or rework_gates:
        state = _record_chosen_pipeline(
            task_dir,
            state,
            full_pipeline,
            full_groups,
            recommended_pipeline=list(classification.get("pipeline") or []),
            recommended_parallel_groups=[list(group) for group in classification.get("parallel_groups") or []],
            reason="Coordinator replaced future waves; Cortex recommendation retained as advisory.",
            reset_gates=rework_gates,
        )
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
            "review", "plan_result_ref", "pending_basis", "approved_basis",
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
    elif not explicit_plan_review:
        # Migrate a legacy required/pending projection back to an advisory
        # state while this coordinator-selected route is being applied.  The
        # old review payload is stale for the new route and must not remain
        # capable of surfacing a hidden user stop on a later continuation.
        approval = _plan_approval(state)
        if approval.get("status") in {"pending_plan", "awaiting_user"}:
            for key in (
                "review", "plan_result_ref", "pending_basis", "approved_basis",
                "requested_at", "approved_at", "request_id",
            ):
                approval.pop(key, None)
            approval.update({"policy": "auto", "status": "not_required", "user_requested": False, "feedback": None})
            state["plan_approval"] = approval
            state["plan_approval_user_requested"] = False
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "plan_approval_advisory",
                "migrated legacy plan approval policy to advisory mode for the chosen route",
            )
    if rework_request_digest:
        history_entry = {
            "request_digest": rework_request_digest,
            "rework_gates": list(rework_gates),
            "semantic_future_pipeline_digest": new_semantic_digest,
            "material_change": material_rework,
            "outcome": "applied",
            "at": now(),
        }
        state.setdefault("rework_history", []).append(history_entry)
        state["rework_history"] = [
            item for item in state["rework_history"][-32:]
            if isinstance(item, dict)
        ]
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "rework_request",
            "recorded canonical completed-gate rework digest before dispatch",
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
        if _normalize_no_progress_pause_state(state):
            state["status"] = "active"
            state.pop("blocked_reason", None)
            sync_current_wave(state)
            save_state(task_dir, task_dir / "state.sqlite", state, "retry_advisory_migration", "migrated legacy Planner pause to automatic retry advice")
        plan = _load_orchestrate_plan(task_dir, state)
        task = load_task_definition(task_dir, state)
        # Do this before completing the current worker or recording a gate.
        # A successor briefing is rendered later, but its task-domain
        # validation must never be the first operation that discovers an
        # invalid durable task after the source wave has been consumed.
        _preflight_dispatch_context(task, state)
        if params.get("future_waves") is not None:
            try:
                _governance_boundary_recheck(
                    params,
                    task,
                    state,
                    future_waves=params.get("future_waves"),
                    results=params.get("completions"),
                )
            except ValueError as exc:
                state.setdefault("orchestration_advice", []).append({
                    "code": "governance_recommendation",
                    "severity": "warning",
                    "message": redact(str(exc), 1600),
                    "at": now(),
                })
                save_state(task_dir, task_dir / "state.sqlite", state, "governance_advice", "recorded governance recommendation without veto")
        current_wave = _wave_for_gates(plan, active_gates(state))
        executable_gates = list(current_wave.get("executable_gates") or current_wave.get("gates", [])) if current_wave else []
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
                if audited["state"].get("status") != "completed":
                    # close_audit downgraded a stale completion projection
                    # because a worker was still non-terminal. Treat that as
                    # lifecycle recovery, not a user-visible block, and retry
                    # the selected frontier when one is available.
                    recovery = _dispatch_server_owned_recovery(
                        params,
                        task_dir,
                        audited["state"],
                        plan,
                        reason="Completion projection contained a non-terminal worker; continue the selected route.",
                    )
                    if recovery is not None:
                        recovered_state, recovered_plan, prepared, receipt = recovery
                        return _orchestrate_response(
                            "advance",
                            recovered_state,
                            wave_id=prepared["wave_id"],
                            spawn_requests=prepared["spawn_requests"],
                            result={"recovery": receipt},
                            plan=recovered_plan,
                        )
                return _orchestrate_response("advance", audited["state"], wave_id=requested_wave_id, result={"result_count": audited["result_count"]}, plan=plan)
            if state.get("status") == "blocked":
                if state.get("user_stop_requested"):
                    return _orchestrate_response("advance", state, wave_id=requested_wave_id, plan=plan)
                # A technical blocked projection is an internal recovery
                # checkpoint, never a reason to stop the coordinator. Reopen
                # it and retry the orchestrator-selected frontier immediately.
                state["status"] = "active"
                state.pop("blocked_reason", None)
                state.setdefault("orchestration_advice", []).append({
                    "code": "blocked_projection_recovered",
                    "severity": "warning",
                    "message": "Recovered a technical blocked projection and continued the selected pipeline.",
                    "recommended_next": "continue_selected_pipeline",
                    "at": now(),
                })
                save_state(
                    task_dir,
                    task_dir / "state.sqlite",
                    state,
                    "blocked_projection_recovery",
                    "reopened a technical blocked projection for automatic selected-route recovery",
                )
                recovery = _dispatch_server_owned_recovery(
                    params,
                    task_dir,
                    state,
                    plan,
                    reason="Recovered a technical blocked projection; retry the orchestrator-selected route.",
                )
                if recovery is not None:
                    recovered_state, recovered_plan, prepared, receipt = recovery
                    return _orchestrate_response(
                        "advance",
                        recovered_state,
                        wave_id=prepared["wave_id"],
                        spawn_requests=prepared["spawn_requests"],
                        result={"recovery": receipt},
                        plan=recovered_plan,
                    )
                return _orchestrate_response(
                    "advance",
                    state,
                    wave_id=requested_wave_id,
                    result={"recovery": {"status": "reconciliation_pending", "automatic": True}},
                    plan=plan,
                )
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
        # A partial parallel retry deliberately retains the source wave's
        # identity, whose historical ``attempt_ids`` include siblings that
        # already completed. Derive the expected live slots from durable
        # attempts in that case instead of treating those stale ids as the
        # current retry contract.
        expected_attempt_ids = set(
            item["attempt_id"] for item in state.get("attempts", [])
            if item.get("gate") in executable_gates
            and not item.get("invalidated")
            and item.get("status") not in TERMINAL_ATTEMPT_STATUSES
        ) if current_wave.get("partial_parallel_wave") else set(current_wave.get("attempt_ids") or [
            item["attempt_id"] for item in state.get("attempts", [])
            if item.get("gate") in executable_gates and not item.get("invalidated")
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
            prospective_completed = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", [])) | set(executable_gates)
            requested_future_gates = {gate for wave in future_preview for gate in wave["gates"]}
            _validate_pending_implementation_retained(
                state,
                _semantic_future_pipeline(plan),
                requested_future_gates,
                prospective_completed,
                _pipeline_obligation_gates(state, plan, task),
            )
            reintroduced = sorted(prospective_completed & requested_future_gates)
            # Reintroduced gates are an explicit chosen-pipeline rework, not a
            # policy violation. Immutable prior evidence remains in the ledger.
            # Validate the complete approval/rework shape before recording any
            # attempt or gate.  Previously this check happened only inside
            # _replace_future_orchestrate_waves after the current gate and a
            # reintroduced Planner had already been durably mutated, leaving
            # an active task with plan=approved and no dispatch on rejection.
            preview_completed_waves = [
                wave
                for wave in plan.get("waves", [])
                if wave.get("status") == "completed"
                or set(wave.get("gates", [])).issubset(prospective_completed)
            ]
            preview_plan = {**plan, "waves": [*preview_completed_waves, *future_preview]}
            semantic_changed = (
                _semantic_future_pipeline_digest(preview_plan)
                != _semantic_future_pipeline_digest(plan)
            )
        for completion in completions:
            if not isinstance(completion, dict):
                raise ValueError("completion entries must be objects")
            state, _unused = _complete_orchestrate_attempt(params, task_dir, state, completion)
        _apply_next_retry_strategies(current_wave, state, completions)
        _checkpoint_orchestrate_transaction(transaction_path, transaction, "attempts_completed", attempt_ids=sorted(provided_attempt_ids))
        if state.get("require_delegation") and not state.get("reassessment_receipts") and "close" in executable_gates:
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
        completions_by_attempt = {
            safe_id(str(item.get("attempt_id") or "")): item
            for item in completions
            if isinstance(item, dict)
        }
        no_progress_pauses = _active_no_progress_pauses(state)
        newly_paused_gates: list[str] = []
        for gate in list(executable_gates):
            if gate in state.get("completed_gates", []) or gate in state.get("skipped_gates", []):
                continue
            gate_attempts = [item for item in state.get("attempts", []) if item.get("gate") == gate and not item.get("invalidated")]
            statuses = {item.get("status") for item in gate_attempts}
            # A corrective attempt is a new authoritative observation for the
            # gate.  Do not let a historical blocked/failed attempt keep
            # winning the aggregate after the current worker has passed: that
            # stranded the gate in a no-progress pause, re-entered diagnostic
            # recovery, and returned no dispatch on the next turn.  For a
            # parallel wave, any current non-success still wins; a prior
            # attempt is otherwise historical evidence only.
            current_gate_attempts = [
                item for item in gate_attempts
                if item.get("attempt_id") in completions_by_attempt
            ]
            current_statuses = {item.get("status") for item in current_gate_attempts}
            if current_statuses:
                default_outcome = (
                    "failed" if "blocked" in current_statuses
                    else "failed" if current_statuses & {"failed", "cancelled", "superseded"}
                    else "passed"
                )
            else:
                default_outcome = "failed" if "blocked" in statuses else "failed" if statuses & {"failed", "cancelled", "superseded"} else "passed"
            gate_decision = None
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
                # finding as a failed attempt; unbounded corrective handling
                # dispatches another worker with progressively higher effort.
                default_outcome = "failed"
            outcome = str(gate_outcomes.get(gate, default_outcome))
            if outcome == "blocked":
                # Worker/gate ``blocked`` is a technical observation, not a
                # Cortex lifecycle state. Preserve the attempt evidence and
                # route a retry through the selected gate.
                state.setdefault("orchestration_advice", []).append({
                    "code": "gate_blocked_retryable",
                    "gate": gate,
                    "message": "The worker reported a retryable technical condition; retry the chosen route.",
                    "at": now(),
                })
                outcome = "failed"
            if outcome == "passed" and current_gate_attempts:
                # A worker may have reported a transient blocked/failed
                # transport before the server-owned corrective attempt was
                # dispatched.  Once the current attempt is accepted, retire
                # only those old projections that have no canonical result.
                # Keep their ledger/event evidence, but exclude them from the
                # accepted-attempt audit so an incomplete transport receipt
                # cannot strand an otherwise successful gate.
                successful_current = {
                    str(item.get("attempt_id") or "")
                    for item in current_gate_attempts
                    if item.get("status") == "passed"
                }
                superseded = False
                for prior in gate_attempts:
                    prior_id = str(prior.get("attempt_id") or "")
                    if (
                        prior_id
                        and prior_id not in successful_current
                        and prior.get("status") in {"blocked", "failed", "cancelled"}
                        and not str(prior.get("attempt_result_ref") or "").strip()
                    ):
                        prior["status"] = "superseded"
                        prior["lifecycle_status"] = "SUPERSEDED"
                        prior["invalidated"] = True
                        prior["invalidation_reason"] = "server_owned_corrective_attempt_passed"
                        prior["superseded_by_attempt_id"] = sorted(successful_current)[0] if successful_current else None
                        prior["superseded_at"] = now()
                        superseded = True
                if superseded:
                    save_state(
                        task_dir,
                        task_dir / "state.sqlite",
                        state,
                        "corrective_attempt_reconciled",
                        f"{gate}: retired incomplete prior transport after corrective pass",
                    )
            failure_counts = state.setdefault("orchestrate_gate_failure_counts", {})
            failure_count_changed = False
            if outcome == "failed":
                failure_count = int(failure_counts.get(gate, 0)) + 1
                failure_counts[gate] = failure_count
                failure_count_changed = True
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
                    state = _ensure_attempt_evidence(
                        params,
                        task_dir,
                        state,
                        attempt,
                        command=gate == "close" and index == 0,
                    )
            if outcome in {"blocked"} or (outcome == "passed" and gate == "close" and state.get("require_handoff")):
                handed = _auto_handoff(
                    params,
                    task_dir,
                    state,
                    "Continue the selected route through internal corrective recovery." if outcome == "blocked" else "Close the Cortex task.",
                )
                if handed.get("recorded") is False:
                    raise ValueError(str(handed.get("reason") or "automatic handoff was not recorded"))
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
                if recorded.get("reason") == "closure_attempt_unresolved":
                    # A finalized closure AttemptResult with unresolved items
                    # is a server-owned corrective route, not a coordinator
                    # validation error.  Keep the original result immutable,
                    # reopen the canonical corrective target, and dispatch the
                    # next worker under this same task.  The returned dispatch
                    # is the only authority for the follow-up call.
                    unresolved_attempt_ids = [
                        safe_id(str(item))
                        for item in recorded.get("candidate_attempt_ids") or []
                        if str(item).strip()
                    ]
                    corrective_findings, source_result_refs = _closure_unresolved_corrective_findings(
                        root,
                        state,
                        unresolved_attempt_ids,
                    )
                    if not corrective_findings or not source_result_refs:
                        raise ValueError(
                            "closure_attempt_unresolved did not resolve to a current canonical result"
                        )
                    target_gate = _activate_closure_rework(
                        state,
                        gate=gate,
                        findings=corrective_findings,
                        source_result_refs=source_result_refs,
                    )
                    save_state(
                        task_dir,
                        task_dir / "state.sqlite",
                        state,
                        "closure_rework",
                        f"{gate}: server-owned corrective route for unresolved AttemptResult",
                    )
                    plan = _load_orchestrate_plan(task_dir, state)
                    prepared = _prepare_orchestrate_wave(params, task_dir, state, plan)
                    state = prepared["state"]
                    corrective = {
                        "schema": "cortex/corrective-dispatch/v1",
                        "mode": "closure_unresolved",
                        "task_id": str(state["task_id"]),
                        "origin_gate": gate,
                        "target_gate": target_gate,
                        "source_attempt_ids": unresolved_attempt_ids,
                        "source_result_refs": source_result_refs,
                        "replacement_worker_authorized": False,
                    }
                    save_state(
                        task_dir,
                        task_dir / "state.sqlite",
                        state,
                        "closure_rework_dispatch",
                        f"{gate}: dispatched server-owned corrective target {target_gate}",
                    )
                    return _orchestrate_response(
                        "advance",
                        state,
                        wave_id=prepared["wave_id"],
                        spawn_requests=prepared["spawn_requests"],
                        result={"corrective_dispatch": corrective},
                        plan=plan,
                    ) | {
                        "next_action": (
                            "Invoke only the returned native corrective dispatch request(s) from this "
                            "cortex/orchestration/v5 response for the same task, wait for those exact "
                            "workers, read each canonical AttemptResult, then call orchestrate(operation=advance) "
                            "with the server-returned completion(s)."
                        ),
                    }
                raise ValueError(str(recorded.get("reason") or "gate outcome was not recorded"))
            state = recorded["state"]
            # The gate-result projection is durable only after record_gate.
            # Re-evaluate then, rather than only before it, so a fresh Review
            # that just resolved an inherited finding also retires the active
            # corrective route before Close/handoff is prepared.
            if outcome == "passed":
                _unresolved_rework_findings(root, state, gate)
            # A steer is material new evidence, so it must first reopen the
            # affected pipeline.  Do not let the liveness detector classify
            # the just-completed pre-steer work as a repeated corrective loop.
            if not isinstance(state.get("pending_revision_impact"), dict):
                pause = _record_corrective_progress(
                    root,
                    state,
                    gate,
                    gate_attempts,
                    [
                        completions_by_attempt[item["attempt_id"]]
                        for item in gate_attempts
                        if item.get("attempt_id") in completions_by_attempt
                    ],
                    unresolved_rework,
                    outcome=outcome,
                )
                save_state(
                    task_dir,
                    task_dir / "state.sqlite",
                    state,
                    "rework_progress",
                    f"{gate}: recorded corrective evidence for outcome {outcome}",
                )
                if pause is not None:
                    no_progress_pauses[gate] = pause
                    newly_paused_gates.append(gate)
        state, semantic_rework = _apply_pending_revision_impact(params, task_dir, state, plan)
        if newly_paused_gates and not semantic_rework:
            # Retain every failed gate exactly as recorded, but never turn a
            # local retry circuit breaker into a task-wide stop. Repeated
            # failures are advisory evidence and the chosen gate is retried
            # automatically through the normal dispatch path. Do not leave a
            # live pause in active_gates: that would silently strand the
            # selected route until an unrelated resume call.
            paused_gates = sorted(no_progress_pauses)
            state.setdefault("pipeline_advice", []).append({
                "code": "no_progress_recovery_recommended",
                "severity": "warning",
                "gates": paused_gates,
                "message": "Repeated failure evidence was recorded; retry or delegate the selected gate.",
                "recommended_next": "retry_or_delegate_selected_gate",
            })
            no_progress_pauses.clear()
            _store_no_progress_pauses(state, no_progress_pauses)
            state["status"] = "active"
            state.pop("blocked_reason", None)
            sync_current_wave(state)
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "no_progress_pause",
                (
                    "paused no-progress retries for gate(s) " + ", ".join(sorted(set(newly_paused_gates)))
                    + "; other executable gates remain active"
                ),
            )
        original_gates = list(current_wave.get("gates", []))
        terminal_gates = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
        all_original_terminal = all(gate in terminal_gates for gate in original_gates)
        current_wave["status"] = "completed" if all_original_terminal else ("blocked" if state.get("status") == "blocked" else "active")
        current_wave["executable_gates"] = list(active_gates(state))
        _write_orchestrate_plan(task_dir, plan)
        _checkpoint_orchestrate_transaction(transaction_path, transaction, "gates_recorded", gates=original_gates)
        # A coordinator may discover a bounded defect in the final close
        # result and explicitly reintroduce documentation/review/close. The
        # close gate transitions the task to completed before this replacement
        # is applied, so accepting future waves only while active silently
        # discarded the authorized rework and produced a false terminal
        # success. update_pipeline(allow_rework=True) intentionally reopens a
        # completed task and invalidates every downstream receipt.
        if params.get("future_waves") is not None and state.get("status") in {"active", "completed"}:
            state, plan = _replace_future_orchestrate_waves(params, task_dir, state, plan, params["future_waves"])
        if state.get("status") == "completed":
            audited = close_audit({**params, "task_id": task_id})
            return _orchestrate_response("advance", audited["state"], wave_id=requested_wave_id, result={"result_count": audited["result_count"]}, plan=plan)
        if state.get("status") == "blocked":
            # A terminal worker result is recoverable orchestration evidence,
            # not a terminal state for Cortex itself.  Derive the corrective
            # route on the server immediately; the coordinator must never
            # manufacture ``future_waves`` or issue a second, replacement
            # dispatch just to get the task moving again.
            dispatched = _dispatch_server_owned_recovery(
                params,
                task_dir,
                state,
                plan,
                reason="Server-derived corrective recovery for terminal worker result.",
            )
            if dispatched is not None:
                state, plan, prepared, recovery_receipt = dispatched
                return _orchestrate_response(
                    "advance",
                    state,
                    wave_id=prepared["wave_id"],
                    spawn_requests=prepared["spawn_requests"],
                    result={"recovery": recovery_receipt},
                    plan=plan,
                )
            # The fallback is only reachable when the durable task has no
            # recoverable gate frontier at all.  Keep this as an internal
            # reconciliation receipt rather than manufacturing a user
            # question or leaving Cortex in ``needs_input``.
            state["status"] = "active"
            state.pop("blocked_reason", None)
            state["diagnostic_recovery"] = {
                "schema": "cortex/recovery-contract/v1",
                "status": "reconciliation_pending",
                "mode": "planner_diagnostic",
                "reason": "no recoverable gate frontier was present in the durable projection",
                "replacement_worker_authorized": False,
                "at": now(),
            }
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "diagnostic_recovery_pending",
                "recorded an internal reconciliation receipt for a missing gate frontier",
            )
            return _orchestrate_response(
                "advance", state, wave_id=requested_wave_id,
                result={
                    "recovery": dict(state["diagnostic_recovery"]),
                },
                plan=plan,
            )
        if state.get("status") == "needs_input" and state.get("blocked_reason"):
            # Older projections may retain a needs_input marker after a
            # technical pause. Reconcile it through the chosen route; only a
            # real worker question is allowed to remain user-visible.
            state["status"] = "active"
            state.pop("blocked_reason", None)
            dispatched = _dispatch_server_owned_recovery(
                params,
                task_dir,
                state,
                plan,
                reason="Reconciled stale technical needs_input projection through the chosen pipeline.",
            )
            if dispatched is not None:
                state, plan, prepared, recovery_receipt = dispatched
                return _orchestrate_response(
                    "advance", state, wave_id=prepared["wave_id"],
                    spawn_requests=prepared["spawn_requests"],
                    result={"recovery": recovery_receipt}, plan=plan,
                )
            state["status"] = "active"
            state.pop("blocked_reason", None)
            state["diagnostic_recovery"] = {
                "schema": "cortex/recovery-contract/v1",
                "status": "reconciliation_pending",
                "mode": "planner_diagnostic",
                "replacement_worker_authorized": False,
                "at": now(),
            }
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "diagnostic_recovery_pending",
                "reconciled stale needs_input projection without user decision",
            )
            return _orchestrate_response(
                "advance", state, wave_id=requested_wave_id,
                result={"recovery": dict(state["diagnostic_recovery"])}, plan=plan,
            )
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
    unknown = sorted(set(payload) - {"decision", "approval_mode", "feedback", "request_id"})
    if unknown:
        raise ValueError("unsupported plan_approval payload fields: " + ", ".join(unknown))
    decision_raw = str(payload.get("decision") or "").strip().lower().replace("-", "_").replace(" ", "_")
    decision = {
        "approve": "approve", "approved": "approve", "accept": "approve",
        "approve_with_recommendations": "approve_with_recommendations",
        "approve_without_recommendations": "approve_without_recommendations",
        "cancel": "cancel", "canceled": "cancel", "cancelled": "cancel",
        "revise": "revise", "changes": "revise", "request_changes": "revise",
    }.get(decision_raw)
    if not decision:
        raise ValueError("plan_approval decision must be approve_with_recommendations, approve_without_recommendations, cancel, or revise")
    feedback = str(payload.get("feedback") or "").strip()
    approval_mode = str(payload.get("approval_mode") or "").strip().lower().replace("-", "_")
    if approval_mode and approval_mode not in {"approve_with_recommendations", "approve_without_recommendations"}:
        raise ValueError("plan_approval approval_mode must be approve_with_recommendations or approve_without_recommendations")
    if decision == "revise" and not feedback:
        raise ValueError("plan_approval revise requires non-empty feedback")
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id:
        raise ValueError("plan_approval chat response requires the pending interaction request_id")

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
        if request_id != expected_request_id:
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

        if decision in {"approve", "approve_with_recommendations", "approve_without_recommendations"}:
            result_ref = safe_id(str(approval.get("plan_result_ref") or ""))
            current_basis = _current_plan_basis(task_dir, state, plan, result_ref=result_ref)
            pending_basis = approval.get("pending_basis") if isinstance(approval.get("pending_basis"), dict) else {}
            basis_keys = (
                "pipeline_contract_version", "plan_revision", "plan_result_ref",
                "verified_predecessor_digest", "semantic_pipeline_version",
                "semantic_future_pipeline_digest",
            )
            mismatches = [key for key in basis_keys if pending_basis.get(key) != current_basis.get(key)]
            if mismatches:
                # The approval interaction is explicit user intent, but a
                # stale digest is still an internal reconciliation detail.
                # Preserve the deviation and approve the current canonical
                # planner basis instead of emitting a Cortex block.
                approval.setdefault("advisories", []).append({
                    "code": "plan_review_basis_reconciled",
                    "fields": list(mismatches),
                    "at": now(),
                })
            approval.update({
                "status": "approved",
                "approved_at": now(),
                "feedback": None,
                "approved_basis": current_basis,
                "request_id": expected_request_id,
                "approval_mode": (
                    decision if decision.startswith("approve_") else
                    approval_mode or "approve_with_recommendations"
                ),
            })
            history.append({"event": "approved", "at": now(), "approval_mode": approval["approval_mode"], "plan_review": review, "approved_basis": current_basis})
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
        for key in ("review", "plan_result_ref", "pending_basis", "approved_basis", "request_id", "requested_at", "approved_at"):
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


def _reported_attempt_completion_candidates(
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return canonical AttemptResult candidates for stopped workers.

    Host-stop metadata is not result authority.  A recovered candidate is
    valid only when the exact attempt has a completed, identity-bound
    AttemptResult in SQLite; result projections and result-registry rows are not
    consulted.
    """
    active = set(active_gates(state))
    wave = _wave_for_gates(plan, list(active))
    slots = {
        str(attempt_id): index
        for index, attempt_id in enumerate((wave or {}).get("attempt_ids") or [], 1)
    }

    candidates: list[dict[str, Any]] = []
    for attempt in state.get("attempts", []):
        if (
            not isinstance(attempt, dict)
            or attempt.get("invalidated")
            or attempt.get("status") != "running"
            or not attempt.get("host_stopped_at")
            or attempt.get("gate") not in active
        ):
            continue
        attempt_id = safe_id(str(attempt.get("attempt_id") or ""))
        result_refs: list[str] = []
        rejected_results: list[dict[str, str]] = []
        try:
            result = attempt_protocol.get_attempt_result(
                _ledger_root_for_artifact(task_dir),
                task_id=state["task_id"],
                attempt_id=attempt_id,
            )
            metadata = result.get("metadata") if isinstance(result, dict) and isinstance(result.get("metadata"), dict) else {}
            identity = metadata.get("identity") if isinstance(metadata.get("identity"), dict) else {}
            result_ref = safe_id(str(result.get("result_ref") or "")) if isinstance(result, dict) else ""
            if (
                not result_ref
                or str(result.get("lifecycle_status") or "") != attempt_protocol.LIFECYCLE_COMPLETED
                or str(metadata.get("phase") or "") != str(attempt.get("gate") or "")
                or str(identity.get("attempt_id") or "") != attempt_id
            ):
                raise ValueError("canonical AttemptResult does not validate the stopped worker")
            result_refs.append(result_ref)
        except ValueError as exc:
            rejected_results.append({"attempt_result_ref": str(attempt.get("attempt_result_ref") or ""), "reason": redact(str(exc), 300)})
        candidate = {
            "attempt_id": attempt_id,
            "worker": slots.get(attempt_id),
            "phase": str(attempt.get("gate") or ""),
            "candidate_attempt_result_refs": result_refs,
            "selection_required": True,
            "host_stopped_at": attempt.get("host_stopped_at"),
        }
        if rejected_results:
            candidate["rejected_attempt_results"] = rejected_results
        candidates.append(candidate)
    return candidates


def _fail_unselectable_reported_attempts(
    root: Path,
    task_dir: Path,
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[str]:
    """Record a stopped worker with no result eligible for continuation.

    We retain the host-stop refs and immutable records for audit, but a stale
    planner revision (or an invalid receipt) cannot be treated as a living
    worker or retried through the same attempt. The selected pipeline remains
    active; the normal technical recovery path dispatches the responsible
    selected gate without exposing a Cortex blocker.
    """
    rejected: list[str] = []
    for candidate in candidates:
        if candidate.get("candidate_attempt_result_refs"):
            continue
        attempt_id = safe_id(str(candidate.get("attempt_id") or ""))
        attempt = _attempt(state, attempt_id)
        if attempt.get("status") != "running" or attempt.get("lifecycle_status") != "result_recorded":
            continue
        attempt["status"] = "failed"
        attempt["lifecycle_status"] = "needs_recovery"
        attempt["finalized_at"] = now()
        attempt["finalization_reason"] = "stopped_result_unusable"
        attempt["host_stop_outcome"] = "result_unusable"
        attempt["host_resumable"] = False
        db_put_worker_session(root, {
            "task_id": state["task_id"],
            "attempt_id": attempt_id,
            "host_agent_id": (attempt.get("host_spawn") or {}).get("agent_id"),
            "host_task_name": str((attempt.get("spawn_request") or {}).get("task_name") or ""),
            "host_tool": str((attempt.get("spawn_request") or {}).get("host_tool") or "spawn_agent"),
            "status": "stopped_recoverable",
            "resumable": False,
            "started_at": attempt.get("spawn_requested_at") or attempt.get("started_at"),
            "terminated_at": attempt.get("finalized_at"),
        })
        rejected.append(attempt_id)
    if rejected:
        # A stopped worker with no usable result is a technical lifecycle
        # defect. Retire that attempt, keep the task active, and let the
        # normal chosen-pipeline recovery dispatch a corrected attempt. Never
        # expose a blocked state to the coordinator/user.
        state["status"] = "active"
        state.pop("blocked_reason", None)
        state.setdefault("stopped_result_recovery", []).append({
            "attempt_ids": rejected,
            "reason": "stopped_result_unusable",
            "advisory": True,
            "at": now(),
        })
        state["stopped_result_recovery"] = state["stopped_result_recovery"][-32:]
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "stopped_result_recovery",
            "stopped worker AttemptResults were ineligible for canonical continuation",
        )
    return rejected


def _inspect_mode(params: dict[str, Any]) -> str:
    """Return the explicit inspection mode without silently accepting writes.

    ``inspect`` historically accepted no operation-specific payload.  The
    facade still permits a generic payload for its other operations, so a
    missing payload selects the read-only default and every other shape is rejected
    every other shape rather than treating a typo as recovery authority.
    """
    raw_payload = params.get("payload")
    if raw_payload is None:
        return _INSPECT_MODE_READ_ONLY
    if not isinstance(raw_payload, dict):
        raise ValueError("inspect payload must be an object with mode=read_only or mode=recover_lifecycle")
    unknown = sorted(set(raw_payload) - {"mode"})
    if unknown:
        raise ValueError("inspect payload supports only mode: " + ", ".join(unknown))
    mode = raw_payload.get("mode", _INSPECT_MODE_READ_ONLY)
    if not isinstance(mode, str) or mode not in _INSPECT_MODES:
        raise ValueError("inspect payload.mode must be read_only or recover_lifecycle")
    return mode


def _expired_lifecycle_attempt_ids(
    state: dict[str, Any],
    *,
    current_time: datetime | None = None,
) -> list[str]:
    """Identify expired leases without changing the durable task projection."""
    observed_at = current_time or datetime.now(timezone.utc)
    expired: list[str] = []
    for attempt in state.get("attempts", []):
        if attempt.get("invalidated") or attempt.get("status") not in {AWAITING_HOST_SPAWN, "running"}:
            continue
        if attempt.get("lifecycle_status") in {"paused_awaiting_user", "result_recorded"}:
            continue
        expiry_field = (
            "spawn_lease_expires_at" if attempt.get("status") == AWAITING_HOST_SPAWN
            else "worker_lease_expires_at"
        )
        raw_expiry = str(attempt.get(expiry_field) or "").strip()
        if not raw_expiry:
            continue
        try:
            expiry = datetime.fromisoformat(raw_expiry).astimezone(timezone.utc)
        except (OverflowError, ValueError):
            # A malformed server-owned lease cannot be presented as a live
            # worker indefinitely.  Recovery records the same explicit
            # lifecycle outcome as it did before inspection became read-only.
            expiry = observed_at
        if expiry <= observed_at:
            attempt_id = safe_id(str(attempt.get("attempt_id") or ""))
            if attempt_id:
                expired.append(attempt_id)
    return expired


def _expire_lifecycle_attempts(
    root: Path,
    task_dir: Path,
    state: dict[str, Any],
) -> list[str]:
    """Persist the explicit lifecycle-recovery transition for expired leases."""
    expired_attempt_ids = _expired_lifecycle_attempt_ids(state)
    for attempt_id in expired_attempt_ids:
        attempt = _attempt(state, attempt_id)
        stopped_at = now()
        attempt["status"] = "failed"
        attempt["lifecycle_status"] = "needs_recovery"
        attempt["orphaned_at"] = stopped_at
        attempt["host_stopped_at"] = stopped_at
        attempt["host_stop_outcome"] = "lifecycle_lease_expired"
        attempt["finalization_reason"] = "lifecycle_lease_expired"
        attempt["host_resumable"] = False
        db_put_worker_session(root, {
            "task_id": state["task_id"],
            "attempt_id": attempt_id,
            "host_agent_id": (attempt.get("host_spawn") or {}).get("agent_id"),
            "host_task_name": str((attempt.get("spawn_request") or {}).get("task_name") or ""),
            "host_tool": str((attempt.get("spawn_request") or {}).get("host_tool") or "spawn_agent"),
            "status": "stopped_recoverable",
            "resumable": False,
            "started_at": attempt.get("spawn_requested_at") or attempt.get("started_at"),
            "terminated_at": stopped_at,
        })
    if expired_attempt_ids:
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "worker_lease_recovery",
            "expired worker lifecycle lease marked needs_recovery",
        )
    return expired_attempt_ids


def _orchestrate_inspect(params: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded snapshot, or run explicitly requested lifecycle repair.

    The usual ``inspect`` path must stay safe while another worker is writing:
    it never takes ``state_lock`` and never changes task/attempt/projection
    state.  Only a caller that sets ``payload.mode`` to
    ``recover_lifecycle`` may mark expired leases or reject unusable stopped
    result receipts.  Both views are derived from the same durable snapshot so
    recovery never relies on a coordinator reconstructing worker state.
    """
    task_id = safe_id(str(params.get("task_id", "")))
    mode = _inspect_mode(params)
    root = ledger_root(params)
    recovered_attempt_ids: list[str] = []
    expired_attempt_ids: list[str] = []
    recovery_receipt: dict[str, Any] | None = None

    if mode == _INSPECT_MODE_RECOVER_LIFECYCLE:
        with state_lock(root):
            _, task_dir, state = load_state(task_id, params)
            authorize(state, params)
            expired_attempt_ids = _expire_lifecycle_attempts(root, task_dir, state)
            plan = _load_orchestrate_plan(task_dir, state)
            completion_candidates = _reported_attempt_completion_candidates(task_dir, state, plan)
            recovered_attempt_ids = _fail_unselectable_reported_attempts(
                root, task_dir, state, completion_candidates
            )
            task = load_task_definition(task_dir, state)
            if recovered_attempt_ids:
                # The failed state changes the recovery projection from a
                # completion selection prompt to selected-route recovery. Do
                # not expose stale candidate refs as selectable after this
                # authoritative transition.
                completion_candidates = []
            # Lease expiry is only the retirement half of lifecycle recovery.
            # The same request must also derive and prepare the corrective
            # route, otherwise the task is left with a failed attempt, no
            # worker, and the public facade projects ``needs_input`` even
            # though no user decision is required.  Do this inside the
            # explicit recovery operation, never during read-only inspect.
            stale_attempt = any(
                isinstance(item, dict)
                and item.get("gate") in set(active_gates(state))
                and not item.get("invalidated")
                and item.get("status") in TERMINAL_ATTEMPT_STATUSES
                and not str(item.get("attempt_result_ref") or "").strip()
                and str(item.get("lifecycle_status") or "") in {"needs_recovery", "FAILED", "BLOCKED"}
                for item in state.get("attempts", [])
            )
            if (
                not (_plan_approval_is_pending(state) and _plan_approval_user_requested(state))
                and not _open_blocking_questions(task_dir, state)
                and (expired_attempt_ids or recovered_attempt_ids or stale_attempt)
            ):
                dispatched = _dispatch_server_owned_recovery(
                    params,
                    task_dir,
                    state,
                    plan,
                    reason="Server-derived recovery after lifecycle lease retirement.",
                )
                if dispatched is not None:
                    state, plan, prepared, recovery_receipt = dispatched
                    completion_candidates = []
                    recovery_receipt = {
                        **recovery_receipt,
                        "lifecycle_recovery": {
                            "mode": mode,
                            "state_changed": True,
                            "expired_attempt_ids": expired_attempt_ids,
                            "unselectable_result_attempt_ids": recovered_attempt_ids,
                            "required": False,
                        },
                    }
                    lifecycle_recovery = recovery_receipt["lifecycle_recovery"]
                    context_handoff = _context_handoff(task_dir, state, task, plan)
                    return _orchestrate_response(
                        "inspect",
                        state,
                        wave_id=prepared["wave_id"],
                        spawn_requests=prepared["spawn_requests"],
                        result={
                            "plan": [{"wave_id": wave["wave_id"], "gates": wave["gates"], "status": wave.get("status", "pending")} for wave in plan.get("waves", [])],
                            "available_results": context_handoff["completed_results"],
                            "pending_dispatches": context_handoff["pending_dispatches"],
                            "active_workers": context_handoff["active_workers"],
                            "context_handoff": context_handoff,
                            "lifecycle_recovery": lifecycle_recovery,
                            "recovery": recovery_receipt,
                        },
                        plan=plan,
                    )
    else:
        _, task_dir, state = load_state(task_id, params)
        authorize(state, params)
        plan = _load_orchestrate_plan(task_dir, state)
        completion_candidates = _reported_attempt_completion_candidates(task_dir, state, plan)
        expired_attempt_ids = _expired_lifecycle_attempt_ids(state)

    unselectable_result_attempt_ids = [
        str(candidate.get("attempt_id") or "")
        for candidate in completion_candidates
        if not candidate.get("candidate_attempt_result_refs")
    ]
    # An ordinary read must show that recovery is needed without presenting
    # unusable canonical results as a selectable completion.  The explicit
    # recovery mode retains the existing blocked-state transition instead.
    if mode == _INSPECT_MODE_READ_ONLY:
        completion_candidates = [
            candidate for candidate in completion_candidates
            if candidate.get("candidate_attempt_result_refs")
        ]

    task = load_task_definition(task_dir, state)
    current_wave = _wave_for_gates(plan, active_gates(state))
    spawn_requests = [
        {**_rehydrate_dispatch_spawn_request(task_dir, task, item), "attempt_id": item["attempt_id"]}
        for item in state.get("attempts", [])
        if item.get("status") == AWAITING_HOST_SPAWN
        and (current_wave is None or item.get("gate") in current_wave.get("gates", []))
        and not item.get("invalidated")
    ]
    context_handoff = _context_handoff(task_dir, state, task, plan)
    lifecycle_recovery = {
        "mode": mode,
        # Read-only inspection derives expired/unselectable attempts from the
        # snapshot but never persists those observations. Only the explicit
        # recovery mode may truthfully result a durable state transition.
        "state_changed": (
            bool(expired_attempt_ids or recovered_attempt_ids)
            if mode == _INSPECT_MODE_RECOVER_LIFECYCLE else False
        ),
        "expired_attempt_ids": expired_attempt_ids,
        "unselectable_result_attempt_ids": (
            recovered_attempt_ids
            if mode == _INSPECT_MODE_RECOVER_LIFECYCLE else unselectable_result_attempt_ids
        ),
    }
    if mode == _INSPECT_MODE_READ_ONLY and (expired_attempt_ids or unselectable_result_attempt_ids):
        lifecycle_recovery.update({
            "required": True,
            "recovery_intent": "recover_inspect",
            "next_action": (
                "Use the explicit recover_inspect lifecycle-recovery intent for this exact task. "
                "The server derives and applies only the listed lifecycle recovery transitions."
            ),
        })
    elif mode == _INSPECT_MODE_RECOVER_LIFECYCLE:
        lifecycle_recovery["required"] = False
    return _orchestrate_response(
        "inspect",
        state,
        wave_id=current_wave.get("wave_id") if current_wave else None,
        spawn_requests=spawn_requests,
        result={
            "plan": [{"wave_id": wave["wave_id"], "gates": wave["gates"], "status": wave.get("status", "pending")} for wave in plan.get("waves", [])],
            "available_results": context_handoff["completed_results"],
            "pending_dispatches": context_handoff["pending_dispatches"],
            "active_workers": context_handoff["active_workers"],
            "stopped_workers": context_handoff["stopped_workers"],
            "context_handoff": context_handoff,
            "lifecycle_recovery": lifecycle_recovery,
            **(
                {"stopped_result_recovery": {"attempt_ids": recovered_attempt_ids}}
                if recovered_attempt_ids else {}
            ),
            **(
                {"pending_result_completions": completion_candidates}
                if completion_candidates else {}
            ),
            **(
                {"plan_review": dict(_plan_approval(state).get("review") or {})}
                if (_plan_approval_is_pending(state) and _plan_approval_user_requested(state)) else {}
            ),
        },
        plan=plan,
    )


def _terminal_blocked_recovery(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Retired compatibility seam; selected-pipeline recovery owns this route."""
    # Keep the symbol for old in-process callers, but never synthesize a route
    # or mutate the coordinator-selected pipeline.
    del params, task_dir, state, plan
    return None



def _server_owned_diagnostic_recovery(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Retired compatibility seam; selected-pipeline recovery owns this route."""
    # Do not create a policy-owned route for an orchestration defect. The
    # active chosen pipeline is the only recovery authority.
    del params, task_dir, state, plan
    return None

def _dispatch_server_owned_recovery(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    reason: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Derive one idempotent recovery dispatch for the selected pipeline."""
    # Recovery follows the orchestrator-selected route. A failed/stale worker
    # is retried in its own chosen wave; Cortex must not inject a policy-owned
    # route or rewrite the pipeline behind the coordinator's back.
    if state.get("status") == "blocked":
        state["status"] = "active"
        state.pop("blocked_reason", None)
    _normalize_no_progress_pause_state(state)
    pauses = state.get("rework_pauses")
    if isinstance(pauses, dict):
        for gate, pause in list(pauses.items()):
            if isinstance(pause, dict) and pause.get("status") == "planner_recovery_pending":
                pause["status"] = "retry_pending"
        _store_no_progress_pauses(state, pauses)
    current = active_gates(state)
    if not current:
        completed = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
        current = [
            str(gate)
            for wave in plan.get("waves", [])
            if isinstance(wave, dict)
            for gate in wave.get("gates", [])
            if str(gate).strip() and str(gate) not in completed
        ]
        if current:
            chosen = list(state.get("chosen_pipeline") or state.get("current_pipeline") or current)
            groups = [list(group) for group in state.get("chosen_parallel_groups") or state.get("parallel_groups") or [[gate] for gate in chosen]]
            state = _record_chosen_pipeline(
                task_dir,
                state,
                chosen,
                groups,
                recommended_pipeline=list(state.get("recommended_pipeline") or []),
                recommended_parallel_groups=[list(group) for group in state.get("recommended_parallel_groups") or []],
                reason="Reconciled the chosen pipeline frontier after a technical lifecycle failure.",
            )
    if not current:
        return None
    state["status"] = "active"
    state.pop("blocked_reason", None)
    save_state(task_dir, task_dir / "state.sqlite", state, "technical_recovery", redact(reason, 2000))
    prepared = _prepare_orchestrate_wave(params, task_dir, state, plan)
    recovery_kind = "chosen_pipeline_recovery"
    receipt = {
        "schema": "cortex/recovery-contract/v1",
        "mode": "chosen_pipeline_retry",
        "origin_gates": list(current),
        "target_gates": list(current),
        "replacement_worker_authorized": False,
        "reason": redact(reason, 1600),
        "status": "prepared",
    }
    state = prepared["state"]
    receipt_key = "technical_recovery"
    state.setdefault(receipt_key, {})["status"] = "dispatched"
    if prepared.get("spawn_requests"):
        state[receipt_key]["dispatch_ref"] = prepared["spawn_requests"][0].get("dispatch_ref")
    save_state(
        task_dir,
        task_dir / "state.sqlite",
        state,
        recovery_kind + "_dispatch",
        reason,
    )
    return state, plan, prepared, {**receipt, "status": "dispatched", "recovery_kind": recovery_kind}


def _ensure_technical_forward_progress(
    params: dict[str, Any],
    operation: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Normalize every technical lifecycle stop into forward progress.

    This is the low-level safety net for all mutating orchestration calls. A
    stale projection, failed transport, missing canonical result, or retired
    worker may be recorded as internal JSONC evidence, but it is never a user
    decision and never a terminal Cortex outcome. The server derives the same
    idempotent corrective route used by explicit recovery, preserving accepted
    results and issuing at most one selected-route dispatch.
    """
    if operation not in {"advance", "resume", "recover_inspect", "recover_blocked"}:
        return result
    if not result.get("ok") or str(result.get("state") or "") not in {
        "needs_input", "blocked", "recovery_pending", "rework_preflight_required",
    }:
        return result
    task_id = safe_id(str(params.get("task_id") or ""))
    if not task_id:
        return result
    root = ledger_root(params)
    try:
        with state_lock(root):
            _, task_dir, state = load_state(task_id, params)
            authorize(state, params)
            # Real task questions and explicit plan approval are the only
            # legitimate stops. Everything else is an internal recovery
            # condition and must remain invisible to the user.
            if (_plan_approval_is_pending(state) and _plan_approval_user_requested(state)) or _open_blocking_questions(task_dir, state):
                return result
            plan = _load_orchestrate_plan(task_dir, state)
            dispatched = _dispatch_server_owned_recovery(
                params,
                task_dir,
                state,
                plan,
                reason="Low-level technical lifecycle reconciliation; continue through the server-owned corrective route.",
            )
            if dispatched is not None:
                state, plan, prepared, receipt = dispatched
                return _orchestrate_response(
                    operation,
                    state,
                    wave_id=prepared["wave_id"],
                    spawn_requests=prepared["spawn_requests"],
                    result={"recovery": receipt},
                    plan=plan,
                )
            if state.get("status") != "completed":
                state["status"] = "active"
                state.pop("blocked_reason", None)
                state["technical_reconciliation"] = {
                    "schema": "cortex/recovery-contract/v1",
                    "status": "reconciled",
                    "operation": operation,
                    "replacement_worker_authorized": False,
                    "at": now(),
                }
                save_state(
                    task_dir,
                    task_dir / "state.sqlite",
                    state,
                    "technical_reconciliation",
                    "reconciled a technical lifecycle stop without a user decision",
                )
                return _orchestrate_response(
                    operation,
                    state,
                    wave_id=None,
                    result={"recovery": dict(state["technical_reconciliation"])},
                    plan=plan,
                )
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        # The original result remains the durable diagnostic if reconciliation
        # itself races with another writer. The next server call retries the
        # same idempotent route; never turn this guard into a new user block.
        return result
    return result


def _recover_internal_lifecycle_exception(
    params: dict[str, Any],
    operation: str,
    task_id: str | None,
    transaction: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Recover a post-transition internal exception before it becomes a stop.

    Caller validation fails before a durable lifecycle phase is reached and
    must remain a precise correction response. Once a transaction has crossed
    a durable checkpoint, however, an exception is an internal reconciliation
    event: persist it in the ledger and continue through the server-owned
    corrective route instead of returning ``management_failed``/``needs_input``.
    """
    if operation not in ORCHESTRATE_MUTATING_OPERATIONS or not task_id:
        return None
    phase = str((transaction or {}).get("phase") or "started")
    if phase in {"started", "activated", "classified"}:
        return None
    root = ledger_root(params)
    try:
        with state_lock(root):
            _, task_dir, state = load_state(task_id, params)
            authorize(state, params)
            if state.get("status") == "completed" or ((_plan_approval_is_pending(state) and _plan_approval_user_requested(state)) or _open_blocking_questions(task_dir, state)):
                return None
            plan = _load_orchestrate_plan(task_dir, state)
            dispatched = _dispatch_server_owned_recovery(
                params,
                task_dir,
                state,
                plan,
                reason="Server-owned recovery after an internal lifecycle exception; continue the task automatically.",
            )
            if dispatched is None:
                return None
            state, plan, prepared, receipt = dispatched
            receipt = {
                **receipt,
                "internal_exception_recovered": True,
                "replacement_worker_authorized": False,
            }
            return _orchestrate_response(
                operation,
                state,
                wave_id=prepared["wave_id"],
                spawn_requests=prepared["spawn_requests"],
                result={"recovery": receipt},
                plan=plan,
            )
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return None


def _orchestrate_resume(params: dict[str, Any]) -> dict[str, Any]:
    task_id = safe_id(str(params.get("task_id", "")))
    _, task_dir, state = load_state(task_id, params)
    authorize(state, params)
    if _normalize_no_progress_pause_state(state):
        state["status"] = "active"
        state.pop("blocked_reason", None)
        sync_current_wave(state)
        save_state(task_dir, task_dir / "state.sqlite", state, "retry_advisory_migration", "migrated legacy Planner pause to automatic retry advice")
    task = load_task_definition(task_dir, state)
    plan = _load_orchestrate_plan(task_dir, state)
    terminal_recovery_requested = bool(params.get("terminal_recovery"))
    terminal_recovery: dict[str, Any] | None = None
    # If the orchestrator supplied a chosen route together with a recovery
    # hint, the chosen route wins. Terminal recovery remains a server-owned
    # audit record, not an input-shape veto.
    if terminal_recovery_requested and params.get("future_waves") is None:
        dispatched = _dispatch_server_owned_recovery(
            params,
            task_dir,
            state,
            plan,
            reason="Server-derived corrective recovery for terminal worker result.",
        )
        if dispatched is None:
            # No terminal result is not a user decision.  If the durable
            # frontier is still recoverable, derive the diagnostic Planner
            # route exactly as recover_inspect does.  Only an exhausted
            # completed task legitimately has no next dispatch.
            if state.get("status") == "completed":
                return _orchestrate_response("resume", state, wave_id=None, plan=plan)
            state["status"] = "active"
            state.pop("blocked_reason", None)
            save_state(task_dir, task_dir / "state.sqlite", state, "recovery_reconciled", "reconciled missing terminal result without user decision")
            dispatched = _dispatch_server_owned_recovery(
                params,
                task_dir,
                state,
                plan,
                reason="Server-derived diagnostic recovery for a missing terminal result.",
            )
        if dispatched is None:
            return _orchestrate_response("resume", state, wave_id=None, plan=plan)
        state, plan, prepared, terminal_recovery = dispatched
        return {
            **_orchestrate_response(
                "resume", state, wave_id=prepared["wave_id"],
                spawn_requests=prepared["spawn_requests"], plan=plan,
                result={"recovery": terminal_recovery},
            ),
            "next_action": (
                "invoke the returned corrective selected-route dispatch exactly once; it is server-derived and idempotent. "
                "Do not provide future_waves, spawn a replacement worker, or dispatch the origin gate directly."
            ),
        }
    # Resume can replace a recovery plan and invalidate blocked attempts.
    # Validate dispatch context before either durable transition, so a
    # compiler rejection keeps the exact recovery state available for retry.
    _preflight_dispatch_context(task, state)
    if params.get("future_waves") is not None:
        try:
            _governance_boundary_recheck(
                params,
                task,
                state,
                future_waves=params.get("future_waves"),
            )
        except ValueError as exc:
            state.setdefault("orchestration_advice", []).append({
                "code": "governance_recommendation",
                "severity": "warning",
                "message": redact(str(exc), 1600),
                "at": now(),
            })
            save_state(task_dir, task_dir / "state.sqlite", state, "governance_advice", "recorded governance recommendation without veto")
    original_status = str(state.get("status") or "")
    active_recovery = (
        original_status in {"active", "needs_input"}
        and params.get("future_waves") is not None
        and bool(active_gates(state))
        and not any(
            attempt.get("gate") in active_gates(state)
            and not attempt.get("invalidated")
            and attempt.get("status") in {AWAITING_HOST_SPAWN, "running", "waiting_question"}
            for attempt in state.get("attempts", [])
        )
    )
    if original_status == "active" and params.get("future_waves") is not None and not active_recovery:
        # A live/pending worker owns the current attempt. Preserve it and
        # ignore a replacement route for this resume call; the orchestrator
        # can submit its chosen route after the current result is consumed.
        params = {key: value for key, value in params.items() if key != "future_waves"}
    closure_rework = state.get("closure_rework") if isinstance(state.get("closure_rework"), dict) else {}
    no_progress_pauses = _active_no_progress_pauses(state)
    requested_rework_gate = str(params.get("rework_gate") or "").strip()
    if requested_rework_gate and requested_rework_gate not in no_progress_pauses:
        requested_rework_gate = ""
    if len(no_progress_pauses) > 1 and not requested_rework_gate:
        # Choose deterministically; all pauses remain durable evidence and can
        # be addressed by subsequent worker corrections.
        requested_rework_gate = sorted(no_progress_pauses)[0]
    no_progress_pause = (
        no_progress_pauses.get(requested_rework_gate)
        if requested_rework_gate else next(iter(no_progress_pauses.values()), None)
    )
    # Identical failures are advisory evidence. Resume may retry the chosen
    # route; the server records the pause and does not ask the user to invent a
    # Planner strategy.
    exhausted_closure_rework = any(
        isinstance(item, dict) and item.get("status") == "rework_required"
        for item in closure_rework.values()
    ) and str(state.get("blocked_reason") or "").startswith("automatic close rework budget exhausted")
    # Closure/no-progress budgets are retained as diagnostics only. Never
    # synthesize a policy-owned route or require a new user-authored plan.
    if params.get("future_waves") is not None:
        state, plan = _replace_future_orchestrate_waves(
            params, task_dir, state, plan, params["future_waves"]
        )
        for item in (state.get("closure_rework") or {}).values():
            if isinstance(item, dict) and item.get("status") == "rework_required":
                item["target_gate"] = "plan"
        state.pop("blocked_reason", None)
        if no_progress_pause:
            resumed_pause = {**no_progress_pause, "resumed_at": now(), "resume_reason": redact(params.get("reason") or "", 1000)}
            state.setdefault("no_progress_pause_history", []).append(resumed_pause)
            state["no_progress_pause_history"] = state["no_progress_pause_history"][-32:]
            no_progress_pauses.pop(str(no_progress_pause.get("gate") or requested_rework_gate), None)
            _store_no_progress_pauses(state, no_progress_pauses)
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "resume_replan",
            "recorded an atomic recovery plan before resuming the blocked task",
        )
    if active_recovery or original_status in {"active", "needs_input"}:
        state.setdefault("resume_events", []).append({
            "reason": redact(
                params.get("reason") or "Recovered an active pipeline with no dispatch.", 2000
            ),
            "mode": "active_stranded_recovery",
            "at": now(),
        })
        state["status"] = "active"
        state.pop("blocked_reason", None)
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "resume_user_decision" if original_status == "needs_input" else "active_stranded_recovery",
            (
                "user supplied the decision required to resume a non-blocking orchestration question"
                if original_status == "needs_input"
                else "recovered an active pipeline that had no live or pending dispatch"
            ),
        )
        resumed_state = state
    else:
        resumed = resume_task({
            **params,
            "task_id": task_id,
            "expected_revision": state["revision"],
            "reason": params.get("reason") or "Unified facade resumed the blocked task.",
        })
        resumed_state = resumed["state"]
    failure_counts = resumed_state.setdefault("orchestrate_gate_failure_counts", {})
    resume_state_changed = False
    if params.get("future_waves") is not None:
        recovered_failure_gates = (
            {str(gate) for gate in closure_rework}
            if exhausted_closure_rework else set(active_gates(resumed_state))
        )
        for gate in recovered_failure_gates:
            resume_state_changed = failure_counts.pop(gate, None) is not None or resume_state_changed
    else:
        for gate in active_gates(resumed_state):
            resume_state_changed = failure_counts.pop(gate, None) is not None or resume_state_changed
    invalidated = False
    for attempt in resumed_state.get("attempts", []):
        if attempt.get("gate") in active_gates(resumed_state) and attempt.get("status") == "blocked" and not attempt.get("invalidated"):
            attempt["invalidated"] = True
            attempt["invalidated_at"] = now()
            attempt["invalidation_reason"] = "retry_after_resume"
            invalidated = True
    if invalidated or resume_state_changed:
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            resumed_state,
            "resume_invalidation",
            "retired blocked attempts and reset the recovered gate budget before retry",
        )
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
    reserved["communication_profile"] = str(state.get("communication_profile") or "natural")
    call_payload = {**payload, **reserved}
    if command == "answer":
        # The user's next ordinary chat message is the durable resume event.
        # Coordinator-only identity and idempotency data are server-derived;
        # the model supplies only the exact interaction ref and answer.
        call_payload["submission_id"] = "chat-" + digest_text(canonical_json.dumps({
            "question_id": payload.get("question_id"),
            "answer": payload.get("answer"),
            "answer_en": payload.get("answer_en"),
        }))[:24]
        call_payload["resume_context"] = {
            "source": "ordinary_chat_message",
            "interaction_ref": payload.get("question_id"),
            "user_language": str(state.get("user_language") or "en"),
        }
    result = handlers[command](call_payload)
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
                return _materialize_response_result_projection(params, replay)
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
        # One low-level boundary owns technical-stop normalization across all
        # mutating lifecycle operations. Validation errors and real user
        # questions remain explicit; every other empty/needs_input projection
        # is reconciled into a server-owned corrective dispatch before the
        # transaction response is exposed.
        result = _ensure_technical_forward_progress(params, operation, result)
        if mutating:
            committed = _commit_orchestrate_transaction(transaction_path, transaction, result)
            return _materialize_response_result_projection(params, committed)
        return _materialize_response_result_projection(
            params,
            {**result, "transaction_id": None, "idempotent": False},
        )
    except ReworkRequestIdempotent as exc:
        task_id = str(params.get("task_id") or (params.get("task") or {}).get("task_id") or "") or None
        result = _orchestrate_response(
            operation,
            exc.state,
            wave_id=None,
            spawn_requests=[],
            result={
                "rework": {
                    "outcome": "idempotent",
                    "request_digest": exc.digest,
                    "spawned": False,
                },
            },
            plan=exc.plan,
        )
        if "transaction_path" in locals() and transaction_path is not None and transaction is not None:
            committed = _commit_orchestrate_transaction(transaction_path, transaction, result)
            return _materialize_response_result_projection(params, committed)
        return result
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        task_id = str(params.get("task_id") or (params.get("task") or {}).get("task_id") or "") or None
        recovered = _recover_internal_lifecycle_exception(params, operation, task_id, transaction if "transaction" in locals() else None)
        if recovered is not None:
            if "transaction_path" in locals() and transaction_path is not None and transaction is not None:
                return _commit_orchestrate_transaction(transaction_path, transaction, recovered)
            return recovered
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
