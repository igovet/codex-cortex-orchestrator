"""SQLite-native orchestration state machine behind the public facade.

The nine public MCP handlers stay composed by :mod:`cortex`. This module owns
orchestration transactions, waves, recovery and management operations, and is
loaded lazily by the facade after the entrypoint has completed initialization.
"""
from __future__ import annotations

import hmac
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cortex_runtime.core.runtime_bindings import bind_symbols, bound_symbol
from cortex_runtime import attempt_protocol, canonical_json, ledger_db
from cortex_runtime.assignment_evaluator import (
    assignment_recovery_class,
    persist_assignment_evaluation,
    requires_product_rework,
)
from cortex_runtime.finding_severity import normalize_finding_severity
from cortex_runtime.context_compiler import context_domain_from_canonical
from cortex_runtime.assignment_compiler import (
    acceptance_contract_digest,
    compiled_wave_execution_order,
    compile_assignment,
    effective_result_contract,
    reliability_recovery_target,
    resolve_profile_for_operation,
)
from cortex_runtime.verification_contract import required_verification_kinds, sha256_hex


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
_NATIVE_STOP_RESULT_GRACE_SECONDS = 30


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
        "MAX_WORK_PACKAGES",
        "MODEL_EFFORTS",
        "MODEL_RECOMMENDED_EFFORTS",
        "NORMAL_COMMAND",
        "LIFECYCLE_RUNTIME_SCHEMA",
        "V11_LIFECYCLES",
        "ORCHESTRATION_PLAN_SCHEMA",
        "ORCHESTRATION_TRANSACTION_SCHEMA",
        "PUBLIC_ORCHESTRATION_SCHEMA",
        "PROFILES",
        "PROFILE_CONTRACT",
        "PLAN_TRACKER_SCHEMA",
        "PLANNING_SCHEMA",
        "PIPELINE_CONTRACT_VERSION",
        "RESULT_READY",
        "SUPPORTED_MODELS",
        "TERMINAL_ATTEMPT_STATUSES",
        "_attempt",
        "_collect_lifecycle_diagnostics",
        "_context_handoff_service",
        "_completed_handoff_occurrences",
        "_delegation_package",
        "_append_closure_rework",
        "_ledger_root_for_artifact",
        "_open_blocking_questions",
        "_plan_approval",
        "_plan_approval_is_pending",
        "_write_delegation_package",
        "_governance_boundary_recheck",
        "_governance_obligations_for_gate",
        "validate_governance_closure_authority",
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
        "db_put_task_document",
        "db_put_worker_session",
        "db_transaction",
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
        "reopen_blocked_lifecycle_state",
        "retire_lane",
        "safe_id",
        "sanitize_structured",
        "save_state",
        "select_project_root",
        "state_lock",
        "status",
        "store_immutable_artifact",
        "sync_current_wave",
        "task_manifest_baseline",
        "task_paths",
        "update_pipeline",
    ),
)

def _lifecycle_error(
    lifecycle: str,
    code: str,
    message: object,
    *,
    phase: str = "validation",
    recoverable: bool = True,
    next_lifecycle: str | None = None,
    task_id: str | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_next_lifecycle = next_lifecycle or (lifecycle if lifecycle in V11_LIFECYCLES else None)
    # A recoverable validation failure is a machine-retryable error, not a
    # request for a user decision.  Keeping it in ``needs_input`` causes the
    # public adapter to render a Question/user_message even when an active
    # worker is merely protected from a malformed coordinator payload (for
    # example a server-derived successor route). Preserve the diagnostic and
    # retryability, but expose the correct non-question lifecycle state.
    validation_error = code == "orchestrate_validation_failed"
    return _segregate_orchestration_output({
        "schema": LIFECYCLE_RUNTIME_SCHEMA,
        "ok": False,
        "lifecycle": lifecycle,
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
        "next_lifecycle": resolved_next_lifecycle,
        "next_action": (
            f"retry the {resolved_next_lifecycle} Cortex lifecycle with a new submission_id after correcting the diagnostic"
            if recoverable and resolved_next_lifecycle else
            "retry the explicit Cortex lifecycle after correcting the diagnostic" if recoverable else
            "inspect the Cortex installation"
        ),
    })


def _segregate_orchestration_output(response: dict[str, Any]) -> dict[str, Any]:
    """Attach the canonical human view and a minimal machine boundary."""
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
    visible_output = {
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
    response["visible_output"] = visible_output
    if state == "waiting_workers":
        response["visible_output"] = None
        response["allowed_visible_events"] = []
    internal = {}
    existing_internal = response.get("internal")
    if isinstance(existing_internal, dict):
        internal.update(existing_internal)
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
    if state.get("status") == "terminal_blocked":
        return "terminal_blocked"
    if state.get("status") == "blocked":
        return "blocked"
    if state.get("status") == "rework_preflight_required":
        return "rework_preflight_required"
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
    current_occurrence = next(
        (
            item for item in state.get("orchestration_wave_occurrences") or []
            if isinstance(item, Mapping)
            and str(item.get("wave_ref") or item.get("wave_id") or "")
            not in set(state.get("completed_orchestration_wave_ids") or [])
            and str(item.get("wave_ref") or item.get("wave_id") or "")
            not in set(state.get("skipped_orchestration_wave_ids") or [])
        ),
        None,
    )
    current_wave_ref = (
        str(current_occurrence.get("wave_ref") or current_occurrence.get("wave_id") or "")
        if isinstance(current_occurrence, Mapping) else ""
    )
    attempts = [
        item for item in state.get("attempts", [])
        if isinstance(item, Mapping)
        and not item.get("invalidated")
        and (
            str(item.get("wave_ref") or item.get("orchestration_wave_id") or "")
            == current_wave_ref
        )
    ] if current_wave_ref else []
    if any(
        item.get("status") == AWAITING_HOST_SPAWN
        and item.get("dispatch_delivery_status") == "pending"
        for item in attempts
    ):
        return "ready_to_spawn"
    if any(
        item.get("status") == AWAITING_HOST_SPAWN
        and item.get("dispatch_delivery_status") == "delivered"
        for item in attempts
    ):
        # Delivery is a one-shot server fence. Until SubagentStart binds the
        # native child, replay observes that already-issued work as waiting;
        # it must never advertise a second dispatch with an empty payload.
        return "waiting_workers"
    # Canonical finalization ends worker execution but does not consume the
    # gate result. Keep that distinction explicit: result-ready is non-runnable
    # and becomes readable only after the matching exact SubagentStop digest.
    result_ready_attempts = [
        item for item in attempts
        if item.get("status") == RESULT_READY
        and item.get("lifecycle_status") == attempt_protocol.LIFECYCLE_COMPLETED
    ]
    result_ready_with_exact_stop = [
        item for item in result_ready_attempts
        if isinstance(item.get("native_terminal_stop"), Mapping)
        and item["native_terminal_stop"].get("observed") is True
        and str(item["native_terminal_stop"].get("result_digest") or "")
        == digest_text(str(item.get("attempt_result_ref") or ""))
    ]
    stopless_bound_result_attempts = [
        item for item in result_ready_attempts
        if str(item.get("worker_host_thread_id") or "").strip()
        and not isinstance(item.get("native_terminal_stop"), Mapping)
        and not (
            isinstance(item.get("native_incomplete_stop_evidence"), Mapping)
            and item["native_incomplete_stop_evidence"].get("observed") is True
        )
    ]
    live_attempts = [
        item for item in attempts
        if item.get("status") == "running"
        and item.get("lifecycle_status") != "paused_awaiting_user"
        and str(item.get("worker_host_thread_id") or "").strip()
        and not isinstance(item.get("native_terminal_stop"), Mapping)
        and not (
            isinstance(item.get("native_incomplete_stop_evidence"), Mapping)
            and item["native_incomplete_stop_evidence"].get("observed") is True
        )
    ]
    if live_attempts or stopless_bound_result_attempts:
        return "waiting_workers"
    if result_ready_attempts and len(result_ready_with_exact_stop) == len(result_ready_attempts):
        return "completion_pending"
    # An active task without a live child, readable result, executable
    # dispatch, or durable user question is an internal reconciliation point.
    # Never project it as user input and never instruct the coordinator to
    # wait: the lifecycle recovery ladder must prepare or replace its exact
    # current occurrence.
    return "recovery_pending"


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
        "close_verified": bool(state.get("close_verified")),
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
    """Expose the compact backend-compiled plan without attempt identities."""
    waves = []
    for index, wave in enumerate(plan.get("waves", []), 1):
        gates = list(wave.get("gates", []))
        status_value = str(wave.get("status") or "pending")
        workers = []
        for item in wave.get("delegations", []):
            if not isinstance(item, dict):
                continue
            worker = {
                "profile": item.get("resolved_profile") or item.get("agent"),
                "operation_kind": item.get("operation_kind"),
                "model": item.get("model"),
                "reasoning_effort": item.get("reasoning_effort"),
            }
            requested = str(item.get("requested_profile") or "").strip()
            resolved = str(item.get("resolved_profile") or item.get("agent") or "").strip()
            if requested and resolved and requested != resolved:
                worker.update({
                    "requested_profile": requested,
                    "resolved_profile": resolved,
                    "resolution_reason": str(item.get("resolution_reason") or ""),
                })
            workers.append(worker)
        waves.append({
            "wave_ref": wave.get("wave_ref") or wave.get("wave_id"),
            "execution_order": index,
            "wave_index": wave.get("wave_index") or index,
            "phase_kind": wave.get("phase_kind") or (gates[0] if len(gates) == 1 else None),
            "status": status_value,
            "workers": workers,
        })
    classification = plan.get("classification") if isinstance(plan.get("classification"), dict) else {}
    return {
        "revision": state.get("revision"),
        "serialized_mutating_waves": int(classification.get("serialized_mutating_waves") or 0),
        "waves": waves,
    }


def _orchestrate_transaction_path(root: Path, submission_id: str) -> Path:
    """Return the ledger root; transaction receipts are SQLite records."""
    safe_id(submission_id)
    return root


def _orchestrate_request_digest(params: dict[str, Any]) -> str:
    # Private server callbacks (for example the start materialization fence)
    # are execution authority, not request semantics and are not JSON data.
    semantic_params = {key: value for key, value in params.items() if not str(key).startswith("_")}
    return digest_text(canonical_json.dumps(semantic_params))


def _begin_orchestrate_transaction(
    root: Path,
    params: dict[str, Any],
    lifecycle: str,
) -> tuple[Path, dict[str, Any], dict[str, Any] | None]:
    submission_id = safe_id(str(params.get("submission_id", "")))
    path = _orchestrate_transaction_path(root, submission_id)
    request_digest = digest_text(canonical_json.dumps({
        "lifecycle": lifecycle,
        "request_digest": _orchestrate_request_digest(params),
    }))
    receipt = db_get_operation(root, submission_id)
    if receipt is not None:
        if receipt.get("schema") != ORCHESTRATION_TRANSACTION_SCHEMA:
            raise ValueError("lifecycle submission_id was reused with different content")
        if receipt.get("request_digest") != request_digest:
            raise ValueError("lifecycle submission_id was reused with different content")
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
        "lifecycle": lifecycle,
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
        "accessibility": "accessibility_auditor",
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


def _assignment_lineage_digest(spec: Mapping[str, Any]) -> str:
    """Digest semantic assignment authority without wave/attempt identity."""
    semantic = {
        key: spec.get(key)
        for key in (
            "gate", "phase_kind", "phase_ref", "wave_ref", "wave_index", "operation_kind",
            "predecessor_wave_refs", "read_only", "can_write",
            "required_verification_kinds",
            "agent", "task_kind", "risk", "model", "reasoning_effort",
            "objective", "ownership", "strategy", "acceptance_criteria", "verification",
            "task_acceptance_criteria", "task_verification",
            "server_acceptance_obligations", "server_verification_obligations",
            "acceptance_contract_digest",
            "context_files", "requirements", "constraints", "scope",
            "recovery_source_result_ref", "recovery_source_result_digest",
            "recovery_chain_result_refs", "recovery_chain_result_digests",
            "recovery_context", "recovery_context_digest", "recovery_stage",
            "recovery_question_ref", "recovery_question_source_attempt_id",
            "recovery_question_source_dispatch_ref",
        )
        if spec.get(key) not in (None, "", [], {})
    }
    return digest_text(canonical_json.dumps(semantic))


def _normalize_orchestrate_waves(
    raw_waves: object,
    task: dict[str, Any],
    host_capabilities: dict[str, Any],
    project_root_value: str,
    *,
    prior_wave_refs: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(raw_waves, list) or not raw_waves:
        raise ValueError("start requires a non-empty waves array")
    task_acceptance = list(task.get("acceptance_criteria") or [])
    task_verification = list(task.get("verification") or [])
    server_acceptance = list(task.get("server_acceptance_obligations") or [])
    server_verification = list(task.get("server_verification_obligations") or [])
    effective_acceptance, effective_verification = effective_result_contract(
        task_acceptance, task_verification,
        server_acceptance_obligations=server_acceptance,
        server_verification_obligations=server_verification,
    )
    contract_digest = acceptance_contract_digest(
        task_acceptance, task_verification,
        server_acceptance_obligations=server_acceptance,
        server_verification_obligations=server_verification,
    )
    if str(task.get("acceptance_contract_digest") or "") != contract_digest:
        raise ValueError("start task acceptance contract digest is missing or inconsistent")
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
        if len(group) != 1:
            raise ValueError(
                "each orchestration wave must own exactly one phase; put different phases in separate waves"
            )
        original_ids[tuple(group)] = wave_id
        proposed_groups.append(group)
        # ``classification`` is advisory. Preserve the exact wave/group
        # selected by the orchestrator as the executable contract, including
        # an intentional order or parallel grouping that the classifier would
        # otherwise normalize away.
        chosen_wave_specs.append((wave_id, group, wave_specs))
    complexity = str(task.get("complexity", "C2")).upper()
    classified_groups: list[list[str]] = []
    classified_gates: set[str] = set()
    for group in proposed_groups:
        unique_group = [gate for gate in group if gate not in classified_gates]
        if unique_group:
            classified_groups.append(unique_group)
            classified_gates.update(unique_group)
    classification = classify({
        "complexity": complexity,
        "requirements": task.get("requirements", []),
        "pipeline": proposed_pipeline,
        "parallel_groups": classified_groups,
    })
    spawn_models = host_capabilities.get("spawn_agent_models")
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
    base_predecessor_wave_refs = [str(item) for item in prior_wave_refs or []]
    if (
        len(base_predecessor_wave_refs) != len(set(base_predecessor_wave_refs))
        or any(re.fullmatch(r"wave-0*[1-9][0-9]*", item) is None for item in base_predecessor_wave_refs)
    ):
        raise ValueError("prior compiled wave refs must be unique immutable identities")
    used_ids: set[str] = set()
    executable_wave_specs: list[tuple[str, list[str], list[dict[str, Any]]]] = []
    serialized_semantic_waves = 0
    for requested_wave_id, group, raw_specs in chosen_wave_specs:
        if len(raw_specs) > 1 and any(
            str(spec.get("operation_kind") or "") == "modify" for spec in raw_specs
        ):
            serialized_semantic_waves += 1
            executable_wave_specs.extend(
                (requested_wave_id, list(group), [dict(spec)]) for spec in raw_specs
            )
        else:
            executable_wave_specs.append((requested_wave_id, group, raw_specs))
    first_requested_index = next((
        int(spec.get("wave_index"))
        for _wave_id, _group, specs in executable_wave_specs
        for spec in specs[:1]
        if isinstance(spec.get("wave_index"), int) and not isinstance(spec.get("wave_index"), bool)
    ), 1)
    for local_index, (_requested_wave_id, group, raw_specs) in enumerate(executable_wave_specs, 1):
        wave_index = first_requested_index + local_index - 1
        wave_id = f"wave-{wave_index:02d}"
        if wave_id in used_ids:
            raise ValueError("compiled global wave identity is duplicated")
        used_ids.add(wave_id)
        wave_ref = wave_id
        phase_ref = f"phase-{wave_index:04d}"
        default_predecessor_wave_refs = [
            str(item.get("wave_ref") or "") for item in normalized
        ]
        delegations: list[dict[str, Any]] = []
        for gate in group:
            specs = [spec for spec in raw_specs if canonical_pipeline_gate(spec.get("gate") or "") == gate] or [{"gate": gate}]
            for spec_index, raw_spec in enumerate(specs, 1):
                if str(raw_spec.get("dispatch_mode") or "hidden_subagent") != "hidden_subagent":
                    raise ValueError("native spawn_agent dispatch is the only supported worker transport")
                requested_agent = str(raw_spec.get("agent") or _default_profile_for_gate(gate))
                operation_kind = str(raw_spec.get("operation_kind") or "").strip()
                agent, resolution_reason = resolve_profile_for_operation(
                    requested_agent, operation_kind, gate, PROFILES,
                )
                if agent not in AGENTS:
                    raise ValueError(f"unknown Cortex profile: {agent}")
                for required_route_field in ("model", "reasoning_effort"):
                    if not isinstance(raw_spec.get(required_route_field), str) or not raw_spec[required_route_field].strip():
                        raise ValueError(
                            f"worker {required_route_field} is required; the orchestrator must select it explicitly"
                        )
                briefing = render_gate_briefing(gate, task.get("user_request", ""), agent)
                objective = str(raw_spec.get("objective") or briefing["objective"]).strip()
                ownership = str(raw_spec.get("ownership") or briefing["ownership"]).strip()
                task_kind = str(
                    raw_spec.get("task_kind")
                    or ("implementation" if operation_kind == "modify" else f"read_only_{operation_kind}")
                )
                risk = str(raw_spec.get("risk") or ("high" if gate == "security" else "low" if gate in {"scope", "plan", "discover", "documentation"} else "moderate"))
                spec = {
                    **raw_spec,
                    "gate": gate,
                    "agent": agent,
                    "profile": agent,
                    "requested_profile": requested_agent,
                    "resolved_profile": agent,
                    "resolution_reason": resolution_reason,
                    "task_kind": task_kind,
                    "risk": risk,
                    "objective": objective,
                    "ownership": ownership,
                    "task_acceptance_criteria": list(task_acceptance),
                    "task_verification": list(task_verification),
                    "server_acceptance_obligations": list(server_acceptance),
                    "server_verification_obligations": list(server_verification),
                    "acceptance_contract_digest": contract_digest,
                    "acceptance_criteria": list(dict.fromkeys([
                        *effective_acceptance,
                        *(raw_spec.get("acceptance_criteria") or briefing["acceptance_criteria"]),
                    ])),
                    "verification": list(dict.fromkeys([
                        *effective_verification,
                        *(raw_spec.get("verification") or briefing["verification"]),
                    ])),
                    "configured_default_model": raw_spec.get("configured_default_model") or configured_default_model,
                    "parallel": len(group) > 1 or len(specs) > 1,
                    "facade_managed": True,
                    "orchestration_wave_id": wave_id,
                    "orchestration_delegation_key": f"{wave_id}-{gate}-{spec_index:02d}",
                    "dispatch_mode": "hidden_subagent",
                }
                route = resolve_dispatch_route({
                    **spec,
                    "complexity": complexity,
                    "_security_gate": gate == "security",
                    "project_root": project_root_value,
                })
                predecessor_wave_refs = [
                    *base_predecessor_wave_refs,
                    *default_predecessor_wave_refs,
                ]
                spec = compile_assignment(
                    spec,
                    profiles=PROFILES,
                    operation_kinds=PROFILE_CONTRACT["operation_kinds"],
                    phase_kind=gate,
                    phase_ref=phase_ref,
                    wave_ref=wave_ref,
                    wave_index=wave_index,
                    predecessor_wave_refs=predecessor_wave_refs,
                    route=route,
                )
                spec["logical_delegation_key"] = f"{phase_ref}-{gate}-{spec_index:02d}"
                spec["assignment_lineage_digest"] = _assignment_lineage_digest(spec)
                spec["plan_assignment_lineage_digest"] = spec["assignment_lineage_digest"]
                delegations.append(spec)
        if len(delegations) > 1 and any(
            str(spec.get("operation_kind") or "") == "modify" for spec in delegations
        ):
            raise ValueError("compiled mutating wave violates assignment isolation")
        normalized.append({
            "wave_id": wave_id,
            "wave_ref": wave_ref,
            "wave_index": wave_index,
            "phase_ref": phase_ref,
            "phase": group[0],
            "phase_kind": group[0],
            "gates": list(group),
            "delegations": delegations,
            "status": "pending",
        })
    classification = {
        **classification,
        "recommended_pipeline": list(classification.get("pipeline") or []),
        "recommended_parallel_groups": [list(group) for group in classification.get("parallel_groups") or []],
        "chosen_pipeline": list(proposed_pipeline),
        "chosen_parallel_groups": [list(group) for group in proposed_groups],
        "serialized_mutating_waves": serialized_semantic_waves,
        "compiled_wave_count": len(normalized),
    }
    return normalized, classification


def _orchestrate_plan_path(task_dir: Path) -> Path:
    """Stable label; canonical plans are stored in ``tasks.plan_json``."""
    return _ledger_root_for_artifact(task_dir) / "cortex.db"


def _write_orchestrate_plan(
    task_dir: Path,
    plan: dict[str, Any],
    *,
    preserve_updated_at: bool = False,
) -> None:
    if not preserve_updated_at:
        plan["updated_at"] = now()
    db_update_task_plan(_ledger_root_for_artifact(task_dir), safe_id(str(plan.get("task_id") or "")), plan)


def _compiled_wave_occurrence_authority(wave: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable execution identity for one compiled wave occurrence."""
    wave_ref = str(wave.get("wave_ref") or "").strip()
    phase_ref = str(wave.get("phase_ref") or "").strip()
    phase_kind = str(wave.get("phase_kind") or "").strip()
    wave_index = wave.get("wave_index")
    gates = [str(item).strip() for item in wave.get("gates") or [] if str(item).strip()]
    if (
        not wave_ref
        or str(wave.get("wave_id") or "") != wave_ref
        or not phase_ref
        or not phase_kind
        or isinstance(wave_index, bool)
        or not isinstance(wave_index, int)
        or wave_index < 1
        or not gates
    ):
        raise ValueError("compiled wave occurrence identity is incomplete")
    lineages: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for assignment in wave.get("delegations") or []:
        if not isinstance(assignment, Mapping):
            raise ValueError("compiled wave occurrence has an invalid assignment")
        if (
            str(assignment.get("wave_ref") or "") != wave_ref
            or str(assignment.get("phase_ref") or "") != phase_ref
            or assignment.get("wave_index") != wave_index
            or str(assignment.get("phase_kind") or "") != phase_kind
        ):
            raise ValueError("compiled assignment does not belong to its wave occurrence")
        slot = (
            str(assignment.get("logical_delegation_key") or "").strip(),
            str(assignment.get("plan_assignment_lineage_digest") or "").strip(),
        )
        if not all(slot) or slot in seen:
            raise ValueError("compiled wave occurrence has ambiguous assignment lineage")
        seen.add(slot)
        lineages.append({
            "logical_delegation_key": slot[0],
            "plan_assignment_lineage_digest": slot[1],
        })
    if not lineages:
        raise ValueError("compiled wave occurrence has no assignment lineage")
    lineages.sort(key=lambda item: (
        item["logical_delegation_key"], item["plan_assignment_lineage_digest"],
    ))
    identity = {
        "wave_ref": wave_ref,
        "wave_index": wave_index,
        "phase_ref": phase_ref,
        "phase_kind": phase_kind,
        "gates": gates,
        "assignment_lineages": lineages,
    }
    lineage_digest = digest_text(canonical_json.dumps(lineages))
    occurrence_key = "occurrence-" + digest_text(canonical_json.dumps({
        **identity,
        "assignment_lineage_digest": lineage_digest,
    }))
    return {
        **identity,
        "assignment_lineage_digest": lineage_digest,
        "occurrence_key": occurrence_key,
    }


def _occurrence_gate_key(authority: Mapping[str, Any], gate: str) -> str:
    occurrence_key = str(authority.get("occurrence_key") or "").strip()
    if not occurrence_key or not str(gate).strip():
        raise ValueError("occurrence-scoped gate key requires exact identity")
    return f"{occurrence_key}:{gate}"


def _validate_occurrence_frontier_submission(
    current_frontier_attempts: Sequence[Mapping[str, Any]],
    submitted_attempt_ids: set[str],
) -> None:
    """Require exactly the server-selected unresolved assignment frontier."""
    frontier_ids = [
        str(item.get("attempt_id") or "").strip()
        for item in current_frontier_attempts
    ]
    if not frontier_ids or any(not item for item in frontier_ids):
        raise ValueError("effective current wave has no canonical assignment frontier")
    if len(frontier_ids) != len(set(frontier_ids)):
        raise ValueError("effective current wave contains duplicate attempt identity")
    expected_attempt_ids = set(frontier_ids)
    unexpected = sorted(submitted_attempt_ids - expected_attempt_ids)
    if unexpected:
        raise ValueError(
            "continue contains attempts outside the active wave: "
            + ", ".join(unexpected)
        )
    missing = sorted(expected_attempt_ids - submitted_attempt_ids)
    if missing:
        raise ValueError(
            "continue server-derived active attempt set is incomplete for: "
            + ", ".join(missing)
        )


def _wave_occurrence_reduction_attempts(
    state: Mapping[str, Any],
    wave: Mapping[str, Any],
    *,
    submitted_attempt_ids: set[str],
) -> list[dict[str, Any]]:
    """Reduce a retry frontier plus exact already-routed sibling outcomes.

    A retry frontier may contain only the replaced slots in a parallel wave.
    The other slots are eligible only when their canonical evaluation was
    consumed for this exact compiled occurrence and either passed or owns an
    exact durable product-rework queue receipt. A failed or superseded
    generation, stale occurrence, or mismatched lineage can therefore never
    fill a slot implicitly.
    """
    authority = _compiled_wave_occurrence_authority(wave)
    expected_slot_order = [
        (
            item["logical_delegation_key"],
            item["plan_assignment_lineage_digest"],
        )
        for item in authority["assignment_lineages"]
    ]
    expected_slots = set(expected_slot_order)
    submitted_by_slot: dict[tuple[str, str], dict[str, Any]] = {}
    consumed_successes_by_slot: dict[tuple[str, str], list[dict[str, Any]]] = {}
    consumed_product_reworks_by_slot: dict[
        tuple[str, str], list[dict[str, Any]]
    ] = {}
    submitted_id_counts = {attempt_id: 0 for attempt_id in submitted_attempt_ids}
    queued_product_reworks = {
        str(item.get("source_result_ref") or ""): item
        for item in state.get("pending_product_reworks") or []
        if isinstance(item, Mapping)
        and str(item.get("source_result_ref") or "").strip()
    }

    def exact_occurrence_slot(item: Mapping[str, Any]) -> tuple[str, str] | None:
        slot = (
            str(item.get("logical_delegation_key") or "").strip(),
            str(item.get("plan_assignment_lineage_digest") or "").strip(),
        )
        if (
            item.get("invalidated")
            or str(item.get("wave_ref") or "") != authority["wave_ref"]
            or str(item.get("orchestration_wave_id") or "") != authority["wave_ref"]
            or item.get("wave_index") != authority["wave_index"]
            or str(item.get("phase_ref") or "") != authority["phase_ref"]
            or str(item.get("phase_kind") or "") != authority["phase_kind"]
            or str(item.get("gate") or "") not in authority["gates"]
            or slot not in expected_slots
        ):
            return None
        return slot

    def is_consumed_canonical_success(item: Mapping[str, Any]) -> bool:
        evaluation = item.get("acceptance_evaluation")
        result_ref = str(item.get("attempt_result_ref") or "").strip()
        return bool(
            str(item.get("protocol_status") or "") == "completed"
            and str(item.get("acceptance_status") or "") == "passed"
            and result_ref
            and str(item.get("continuation_consumed_at") or "").strip()
            and isinstance(evaluation, Mapping)
            and str(evaluation.get("schema") or "")
            == "cortex/assignment-evaluation/v2"
            and str(evaluation.get("attempt_id") or "")
            == str(item.get("attempt_id") or "")
            and str(evaluation.get("attempt_result_ref") or "") == result_ref
            and str(evaluation.get("wave_ref") or "") == authority["wave_ref"]
            and str(evaluation.get("phase_ref") or "") == authority["phase_ref"]
            and str(evaluation.get("protocol_status") or "") == "completed"
            and str(evaluation.get("acceptance_status") or "") == "passed"
        )

    def is_consumed_canonical_product_rework(item: Mapping[str, Any]) -> bool:
        """Accept only an exact needs-rework slot already owned by the queue."""
        evaluation = item.get("acceptance_evaluation")
        result_ref = str(item.get("attempt_result_ref") or "").strip()
        receipt = queued_product_reworks.get(result_ref)
        return bool(
            str(item.get("protocol_status") or "") == "completed"
            and str(item.get("acceptance_status") or "") == "needs_rework"
            and result_ref
            and str(item.get("continuation_consumed_at") or "").strip()
            and isinstance(evaluation, Mapping)
            and requires_product_rework(evaluation)
            and str(evaluation.get("schema") or "")
            == "cortex/assignment-evaluation/v2"
            and str(evaluation.get("attempt_id") or "")
            == str(item.get("attempt_id") or "")
            and str(evaluation.get("attempt_result_ref") or "") == result_ref
            and str(evaluation.get("wave_ref") or "") == authority["wave_ref"]
            and str(evaluation.get("phase_ref") or "") == authority["phase_ref"]
            and isinstance(receipt, Mapping)
            and str(receipt.get("source_attempt_id") or "")
            == str(item.get("attempt_id") or "")
            and str(receipt.get("wave_ref") or "") == authority["wave_ref"]
            and str(receipt.get("phase_ref") or "") == authority["phase_ref"]
            and str(receipt.get("logical_delegation_key") or "")
            == str(item.get("logical_delegation_key") or "")
            and str(receipt.get("plan_assignment_lineage_digest") or "")
            == str(item.get("plan_assignment_lineage_digest") or "")
        )

    for item in state.get("attempts") or []:
        if not isinstance(item, dict):
            continue
        attempt_id = str(item.get("attempt_id") or "").strip()
        if not attempt_id:
            continue
        slot = exact_occurrence_slot(item)
        if attempt_id in submitted_attempt_ids:
            submitted_id_counts[attempt_id] += 1
            if slot is None:
                raise ValueError(
                    "attempt does not match the exact compiled wave occurrence"
                )
            if slot in submitted_by_slot:
                raise ValueError(
                    "submitted frontier contains duplicate assignment lineage"
                )
            submitted_by_slot[slot] = item
            continue
        if slot is not None and is_consumed_canonical_success(item):
            consumed_successes_by_slot.setdefault(slot, []).append(item)
        elif slot is not None and is_consumed_canonical_product_rework(item):
            consumed_product_reworks_by_slot.setdefault(slot, []).append(item)

    duplicate_ids = sorted(
        attempt_id for attempt_id, count in submitted_id_counts.items() if count > 1
    )
    if duplicate_ids:
        raise ValueError(
            "wave occurrence contains duplicate attempt identity: "
            + ", ".join(duplicate_ids)
        )
    missing = sorted(
        attempt_id for attempt_id, count in submitted_id_counts.items() if count == 0
    )
    if missing:
        raise ValueError("wave occurrence attempts are unavailable: " + ", ".join(missing))

    selected: list[dict[str, Any]] = []
    for slot in expected_slot_order:
        submitted = submitted_by_slot.get(slot)
        prior_successes = consumed_successes_by_slot.get(slot, [])
        prior_product_reworks = consumed_product_reworks_by_slot.get(slot, [])
        if submitted is not None:
            if prior_successes or prior_product_reworks:
                raise ValueError(
                    "submitted frontier overlaps an already-consumed assignment slot"
                )
            selected.append(submitted)
            continue
        prior_routed = [*prior_successes, *prior_product_reworks]
        if len(prior_routed) != 1:
            raise ValueError(
                "wave occurrence does not have one exact terminal-success or "
                "durably-routed product-rework attempt per unsubmitted assignment lineage"
            )
        selected.append(prior_routed[0])
    return selected


def _occurrence_acceptance_decision(attempts: Sequence[Mapping[str, Any]]) -> str:
    """Reduce exact canonical assignment evaluations to one occurrence decision."""
    if not attempts:
        raise ValueError("occurrence acceptance requires at least one assignment")
    statuses: set[str] = set()
    for attempt in attempts:
        protocol_status = str(attempt.get("protocol_status") or "").strip()
        acceptance_status = str(attempt.get("acceptance_status") or "").strip()
        evaluation = attempt.get("acceptance_evaluation")
        if (
            protocol_status not in {"completed", "failed", "blocked"}
            or acceptance_status not in {"passed", "failed", "blocked", "needs_rework"}
            or not isinstance(evaluation, Mapping)
            or str(evaluation.get("schema") or "")
            != "cortex/assignment-evaluation/v2"
            or str(evaluation.get("attempt_id") or "")
            != str(attempt.get("attempt_id") or "")
            or str(evaluation.get("attempt_result_ref") or "")
            != str(attempt.get("attempt_result_ref") or "")
            or str(evaluation.get("wave_ref") or "")
            != str(attempt.get("wave_ref") or "")
            or str(evaluation.get("phase_ref") or "")
            != str(attempt.get("phase_ref") or "")
            or str(evaluation.get("protocol_status") or "") != protocol_status
            or str(evaluation.get("acceptance_status") or "") != acceptance_status
            or not str(attempt.get("attempt_result_ref") or "").strip()
            or not str(attempt.get("continuation_consumed_at") or "").strip()
        ):
            raise ValueError("occurrence assignment lacks a consumed canonical acceptance evaluation")
        statuses.add(acceptance_status)
    if "blocked" in statuses:
        return "blocked"
    if "failed" in statuses:
        return "failed"
    if "needs_rework" in statuses:
        return "needs_rework"
    if statuses == {"passed"}:
        return "passed"
    raise ValueError("occurrence acceptance statuses are not terminal")


def _product_rework_receipt(
    source: Mapping[str, Any], evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the one durable receipt shape for a product-defective slot."""
    receipt = {
        "schema": "cortex/product-rework-required/v1",
        "source_attempt_id": str(source.get("attempt_id") or ""),
        "source_result_ref": str(source.get("attempt_result_ref") or ""),
        "phase_ref": str(source.get("phase_ref") or ""),
        "wave_ref": str(source.get("wave_ref") or ""),
        "logical_delegation_key": str(source.get("logical_delegation_key") or ""),
        "plan_assignment_lineage_digest": str(
            source.get("plan_assignment_lineage_digest") or ""
        ),
        "acceptance_evaluation": dict(evaluation),
        "technical_replacement_budget_consumed": False,
        "recorded_at": now(),
    }
    if not all(receipt.get(field) for field in (
        "source_attempt_id", "source_result_ref", "phase_ref", "wave_ref",
        "logical_delegation_key", "plan_assignment_lineage_digest",
    )):
        raise ValueError("product rework receipt lacks compiled assignment identity")
    return receipt


def _materialize_wave_recovery_queue(
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    wave: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    *,
    event: str,
    consume_continuation: bool = True,
) -> dict[str, Any]:
    """Classify and durably queue every exact terminal slot in one wave.

    Both the coordinator read boundary and continuation use this reducer.  It
    is deliberately the only producer of product-rework queue receipts and it
    records all simultaneous technical breakers before either caller selects
    a next action.  Replays preserve the byte-exact first receipt and its
    deterministic compiled-slot ordering.
    """
    authority = _compiled_wave_occurrence_authority(wave)
    expected_slot_order = [
        (
            str(item.get("logical_delegation_key") or ""),
            str(item.get("plan_assignment_lineage_digest") or ""),
        )
        for item in authority["assignment_lineages"]
    ]
    expected_slots = set(expected_slot_order)
    by_slot: dict[tuple[str, str], dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("wave recovery source must be a canonical attempt")
        slot = (
            str(source.get("logical_delegation_key") or ""),
            str(source.get("plan_assignment_lineage_digest") or ""),
        )
        if (
            source.get("invalidated")
            or slot not in expected_slots
            or slot in by_slot
            or str(source.get("wave_ref") or "") != authority["wave_ref"]
            or str(source.get("orchestration_wave_id") or "") != authority["wave_ref"]
            or source.get("wave_index") != authority["wave_index"]
            or str(source.get("phase_ref") or "") != authority["phase_ref"]
            or str(source.get("phase_kind") or "") != authority["phase_kind"]
            or str(source.get("gate") or "") not in authority["gates"]
            or not str(source.get("attempt_result_ref") or "")
        ):
            raise ValueError("wave recovery source does not match its compiled occurrence")
        by_slot[slot] = source
    ordered_sources = [by_slot[slot] for slot in expected_slot_order if slot in by_slot]
    if len(ordered_sources) != len(sources):
        raise ValueError("wave recovery sources contain an unknown assignment slot")

    classified: list[tuple[dict[str, Any], dict[str, Any]]] = []
    root = _ledger_root_for_artifact(task_dir)
    attempt_projection_changed = False
    for source in ordered_sources:
        before = canonical_json.dumps({
            "protocol_status": source.get("protocol_status"),
            "acceptance_status": source.get("acceptance_status"),
            "acceptance_evaluation": source.get("acceptance_evaluation"),
            "continuation_consumed_at": source.get("continuation_consumed_at"),
        })
        evaluation = persist_assignment_evaluation(root, state, source)
        if consume_continuation:
            source["continuation_consumed_at"] = (
                source.get("continuation_consumed_at") or now()
            )
        after = canonical_json.dumps({
            "protocol_status": source.get("protocol_status"),
            "acceptance_status": source.get("acceptance_status"),
            "acceptance_evaluation": source.get("acceptance_evaluation"),
            "continuation_consumed_at": source.get("continuation_consumed_at"),
        })
        attempt_projection_changed = attempt_projection_changed or before != after
        classified.append((source, dict(evaluation)))

    existing_queue = [
        item for item in state.get("pending_product_reworks") or []
        if isinstance(item, dict) and str(item.get("source_result_ref") or "")
    ]
    if len(existing_queue) != len({
        str(item["source_result_ref"]) for item in existing_queue
    }):
        raise ValueError("product rework queue contains duplicate canonical result identity")
    queue_by_result = {
        str(item["source_result_ref"]): item for item in existing_queue
    }
    new_receipts: list[dict[str, Any]] = []
    for source, evaluation in classified:
        if not requires_product_rework(evaluation):
            continue
        candidate = _product_rework_receipt(source, evaluation)
        result_ref = str(candidate["source_result_ref"])
        prior = queue_by_result.get(result_ref)
        if prior is not None:
            for field in (
                "source_attempt_id", "source_result_ref", "phase_ref", "wave_ref",
                "logical_delegation_key", "plan_assignment_lineage_digest",
            ):
                if str(prior.get(field) or "") != str(candidate.get(field) or ""):
                    raise ValueError("product rework queue receipt changed compiled identity")
            continue
        queue_by_result[result_ref] = candidate
        new_receipts.append(candidate)
    queue_order = list(dict.fromkeys([
        *[str(item["source_result_ref"]) for item in existing_queue],
        *[str(item["source_result_ref"]) for item in new_receipts],
    ]))
    queued = [queue_by_result[result_ref] for result_ref in queue_order]

    breaker_count = len(state.get("terminal_recovery_breakers") or [])
    technical_sources = [
        source for source, evaluation in classified
        if assignment_recovery_class(evaluation) == "technical"
        and not _is_terminal_governance_closure_attempt(root, state, source)
    ]
    for source in technical_sources:
        _record_technical_recovery_breaker(
            state,
            source,
            failure_kind="canonical_attempt_result_technical_failure",
            attempt_result_ref=str(source.get("attempt_result_ref") or ""),
        )
    for source, evaluation in classified:
        disposition = (
            "product_rework_queued"
            if requires_product_rework(evaluation)
            else "technical_recovery_bound"
            if assignment_recovery_class(evaluation) == "technical"
            else "accepted"
        )
        source["occurrence_consumption"] = {
            "schema": "cortex/assignment-occurrence-consumption/v1",
            "wave_ref": authority["wave_ref"],
            "phase_ref": authority["phase_ref"],
            "logical_delegation_key": str(
                source.get("logical_delegation_key") or ""
            ),
            "plan_assignment_lineage_digest": str(
                source.get("plan_assignment_lineage_digest") or ""
            ),
            "attempt_result_ref": str(source.get("attempt_result_ref") or ""),
            "disposition": disposition,
        }
    occurrence_receipt = {
        "schema": "cortex/wave-occurrence-consumption/v1",
        "wave_ref": authority["wave_ref"],
        "phase_ref": authority["phase_ref"],
        "wave_index": authority["wave_index"],
        "assignments": [
            dict(source["occurrence_consumption"])
            for source, _evaluation in classified
        ],
    }
    occurrence_receipt["receipt_digest"] = "sha256:" + digest_text(
        canonical_json.dumps(occurrence_receipt)
    )
    occurrence_receipts = state.get("wave_occurrence_consumption_receipts")
    if occurrence_receipts is None:
        occurrence_receipts = {}
    if not isinstance(occurrence_receipts, dict):
        raise ValueError("wave occurrence consumption receipts are invalid")
    prior_occurrence_receipt = occurrence_receipts.get(authority["wave_ref"])
    occurrence_changed = prior_occurrence_receipt != occurrence_receipt
    occurrence_receipts[authority["wave_ref"]] = occurrence_receipt
    state["wave_occurrence_consumption_receipts"] = occurrence_receipts
    mutated = attempt_projection_changed or occurrence_changed or bool(new_receipts) or len(
        state.get("terminal_recovery_breakers") or []
    ) != breaker_count
    if queued:
        state["pending_product_reworks"] = queued
    else:
        state.pop("pending_product_reworks", None)
    if mutated:
        with ledger_db.transaction(root):
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                event,
                (
                    f"queued {len(new_receipts)} product route(s) and recorded "
                    f"{len(technical_sources)} technical slot(s)"
                ),
            )
    return {
        "product_reworks": queued,
        "technical_failure_attempts": technical_sources,
        "evaluations": [evaluation for _source, evaluation in classified],
        "state_mutated": mutated,
    }


def _pending_product_rework_action(
    task_ref: str, receipt: Mapping[str, Any], *, state_mutated: bool = False,
) -> dict[str, Any]:
    """Project one exact queued source without dispatching any worker."""
    return {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "action": "append_rework_wave",
        "task_ref": str(task_ref),
        "source_result_ref": str(receipt.get("source_result_ref") or ""),
        "content": (
            "The canonical worker result is protocol-complete but did not pass its compiled "
            "assignment acceptance contract. Call append_rework_wave with source_result_ref "
            "unchanged and a coordinator-selected mutating worker; do not retry or replace "
            "the completed assignment."
        ),
        "acceptance_evaluation": dict(receipt.get("acceptance_evaluation") or {}),
        "complete": True,
        "state_mutated": bool(state_mutated),
    }


def _require_wave_occurrence_consumption(
    state: Mapping[str, Any],
    wave: Mapping[str, Any],
    *,
    source_result_ref: str,
) -> dict[str, Any]:
    """Require one complete, non-technical occurrence-consumption receipt."""
    authority = _compiled_wave_occurrence_authority(wave)
    receipts = state.get("wave_occurrence_consumption_receipts")
    receipt = (
        receipts.get(authority["wave_ref"])
        if isinstance(receipts, Mapping) else None
    )
    if not isinstance(receipt, Mapping):
        raise ValueError("product rework source wave has no canonical occurrence receipt")
    assignments = receipt.get("assignments")
    expected = [
        (
            str(item.get("logical_delegation_key") or ""),
            str(item.get("plan_assignment_lineage_digest") or ""),
        )
        for item in authority["assignment_lineages"]
    ]
    actual = [
        (
            str(item.get("logical_delegation_key") or ""),
            str(item.get("plan_assignment_lineage_digest") or ""),
        )
        for item in assignments or [] if isinstance(item, Mapping)
    ]
    digest_basis = {
        key: receipt[key]
        for key in ("schema", "wave_ref", "phase_ref", "wave_index", "assignments")
        if key in receipt
    }
    expected_digest = "sha256:" + digest_text(canonical_json.dumps(digest_basis))
    if (
        str(receipt.get("schema") or "")
        != "cortex/wave-occurrence-consumption/v1"
        or str(receipt.get("wave_ref") or "") != authority["wave_ref"]
        or str(receipt.get("phase_ref") or "") != authority["phase_ref"]
        or receipt.get("wave_index") != authority["wave_index"]
        or actual != expected
        or str(receipt.get("receipt_digest") or "") != expected_digest
    ):
        raise ValueError("product rework source wave occurrence receipt is invalid")
    dispositions = [
        str(item.get("disposition") or "")
        for item in assignments or [] if isinstance(item, Mapping)
    ]
    if any(item not in {"accepted", "product_rework_queued"} for item in dispositions):
        raise ValueError("product rework must wait for exact technical sibling recovery")
    source_entries = [
        item for item in assignments or []
        if isinstance(item, Mapping)
        and str(item.get("attempt_result_ref") or "") == str(source_result_ref)
        and str(item.get("disposition") or "") == "product_rework_queued"
    ]
    if len(source_entries) != 1:
        raise ValueError("product rework source lacks exact queue-owned occurrence consumption")
    for item in assignments or []:
        if not isinstance(item, Mapping):
            raise ValueError("wave occurrence receipt contains an invalid assignment")
        matches = [
            attempt for attempt in state.get("attempts") or []
            if isinstance(attempt, Mapping)
            and str(attempt.get("wave_ref") or "") == authority["wave_ref"]
            and str(attempt.get("phase_ref") or "") == authority["phase_ref"]
            and str(attempt.get("logical_delegation_key") or "")
            == str(item.get("logical_delegation_key") or "")
            and str(attempt.get("plan_assignment_lineage_digest") or "")
            == str(item.get("plan_assignment_lineage_digest") or "")
            and str(attempt.get("attempt_result_ref") or "")
            == str(item.get("attempt_result_ref") or "")
            and str(attempt.get("continuation_consumed_at") or "")
            and attempt.get("occurrence_consumption") == item
        ]
        if len(matches) != 1:
            raise ValueError("wave occurrence receipt is not bound to one consumed result")
    return dict(receipt)


def _occurrence_gate_passed(
    state: Mapping[str, Any],
    authority: Mapping[str, Any],
    gate: str,
    attempts: Sequence[Mapping[str, Any]],
) -> bool:
    """Prove that this exact gate occurrence, not a semantic predecessor, passed."""
    key = _occurrence_gate_key(authority, gate)
    receipt = (state.get("gate_occurrences") or {}).get(key)
    if not isinstance(receipt, Mapping):
        return False
    if any(
        str(receipt.get(field) or "") != str(authority.get(field) or "")
        for field in (
            "occurrence_key", "wave_ref", "phase_ref", "phase_kind",
            "assignment_lineage_digest",
        )
    ) or str(receipt.get("gate") or "") != gate:
        return False
    if receipt.get("outcome") == "skipped":
        return True
    if receipt.get("outcome") != "passed":
        return False
    expected = sorted(
        (
            str(item.get("attempt_id") or ""),
            str(item.get("attempt_result_ref") or ""),
            str(item.get("logical_delegation_key") or ""),
            str(item.get("plan_assignment_lineage_digest") or ""),
        )
        for item in attempts
    )
    recorded = sorted(
        (
            str(item.get("attempt_id") or ""),
            str(item.get("attempt_result_ref") or ""),
            str(item.get("logical_delegation_key") or ""),
            str(item.get("plan_assignment_lineage_digest") or ""),
        )
        for item in receipt.get("assignments") or []
        if isinstance(item, Mapping)
    )
    return bool(expected) and recorded == expected and all(
        str(item.get("acceptance_status") or "") == "passed"
        and str(item.get("continuation_consumed_at") or "").strip()
        for item in attempts
    )


def _sync_orchestration_wave_occurrences(state: dict[str, Any], plan: dict[str, Any]) -> None:
    """Bind facade progress to durable wave identity, not semantic phase names."""
    raw_plan_waves = list(plan.get("waves") or [])
    if any(not isinstance(wave, Mapping) for wave in raw_plan_waves):
        raise ValueError("orchestration plan contains a non-object wave")
    plan_waves = list(raw_plan_waves)
    compiled_wave_execution_order(plan_waves)
    occurrences = [
        {
            **_compiled_wave_occurrence_authority(wave),
            "wave_id": str(wave.get("wave_ref") or ""),
        }
        for wave in plan_waves
    ]
    if not occurrences or any(
        not item["wave_ref"]
        or not item["phase_ref"]
        or not item["phase_kind"]
        or not item["gates"]
        or isinstance(item["wave_index"], bool)
        or not isinstance(item["wave_index"], int)
        or item["wave_index"] < 1
        for item in occurrences
    ):
        raise ValueError("orchestration waves require exact compiled occurrence identity")
    wave_ids = [item["wave_id"] for item in occurrences]
    if len(wave_ids) != len(set(wave_ids)):
        raise ValueError("orchestration wave identities must be unique")
    state["orchestration_wave_occurrences"] = occurrences
    authorities = {
        str(item["occurrence_key"]): item
        for item in occurrences
    }
    # Drop obsolete semantic-name bookkeeping instead of migrating it into a
    # new authority. Only receipts that already prove an exact compiled
    # occurrence survive a plan/state synchronization.
    state.pop("orchestrate_gate_failure_counts", None)
    for field in (
        "gate_occurrences",
        "orchestrate_occurrence_failure_counts",
        "rework_progress",
        "rework_pauses",
    ):
        raw = state.get(field)
        if not isinstance(raw, dict):
            state.pop(field, None)
            continue
        exact: dict[str, dict[str, Any]] = {}
        for key, receipt in raw.items():
            if not isinstance(receipt, dict):
                continue
            occurrence_key = str(receipt.get("occurrence_key") or "")
            authority = authorities.get(occurrence_key)
            gate = str(receipt.get("gate") or "")
            if (
                authority is None
                or str(key) != _occurrence_gate_key(authority, gate)
                or gate not in authority["gates"]
                or str(receipt.get("wave_ref") or "") != authority["wave_ref"]
                or str(receipt.get("phase_ref") or "") != authority["phase_ref"]
                or str(receipt.get("assignment_lineage_digest") or "")
                != authority["assignment_lineage_digest"]
            ):
                continue
            exact[str(key)] = receipt
        if exact:
            state[field] = exact
        else:
            state.pop(field, None)
    state["completed_orchestration_wave_ids"] = [
        wave_id for wave_id in state.get("completed_orchestration_wave_ids") or []
        if wave_id in set(wave_ids)
    ]
    state["skipped_orchestration_wave_ids"] = [
        wave_id for wave_id in state.get("skipped_orchestration_wave_ids") or []
        if wave_id in set(wave_ids)
    ]
    sync_current_wave(state)


def _effective_plan_frontier(
    plan: Mapping[str, Any],
    state: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Derive the current wave and assignments from compiled durable identity.

    Semantic ``active_gates`` is only a presentation projection. A repeated
    phase, technical replacement, or product rework can make it ambiguous.
    The effective plan occurrence plus exact non-invalidated assignment
    lineage is the sole executable frontier; no attempt-id cache is stored in
    the plan.

    This helper is deliberately read-only.  Ambiguous or stale assignments
    fail closed; reconciliation belongs to the atomic replacement/rework
    transaction that created the successor assignment.
    """
    raw_waves = list(plan.get("waves") or [])
    if any(not isinstance(item, Mapping) for item in raw_waves):
        raise ValueError("effective plan contains a non-object wave")
    waves = list(raw_waves)
    completed = {
        str(item) for item in state.get("completed_orchestration_wave_ids") or []
        if str(item).strip()
    }
    skipped = {
        str(item) for item in state.get("skipped_orchestration_wave_ids") or []
        if str(item).strip()
    }
    compiled_wave_execution_order(waves)
    seen_wave_refs: set[str] = set()
    current: dict[str, Any] | None = None
    for wave in waves:
        wave_ref = str(wave.get("wave_ref") or "").strip()
        phase_ref = str(wave.get("phase_ref") or "").strip()
        phase_kind = str(wave.get("phase_kind") or "").strip()
        wave_index = wave.get("wave_index")
        if (
            not wave_ref
            or str(wave.get("wave_id") or "") != wave_ref
            or wave_ref in seen_wave_refs
            or isinstance(wave_index, bool)
            or not isinstance(wave_index, int)
            or not phase_kind
        ):
            raise ValueError("effective plan contains invalid compiled wave identity")
        seen_wave_refs.add(wave_ref)
        if current is None and wave_ref not in completed and wave_ref not in skipped:
            current = wave
    if current is None:
        return None, []

    wave_ref = str(current["wave_ref"])
    phase_ref = str(current["phase_ref"])
    specs = [item for item in current.get("delegations") or [] if isinstance(item, dict)]
    if not specs:
        raise ValueError("effective current wave has no compiled assignments")
    expected_slots: dict[tuple[str, str], dict[str, Any]] = {}
    for spec in specs:
        if (
            str(spec.get("wave_ref") or "") != wave_ref
            or str(spec.get("phase_ref") or "") != phase_ref
            or spec.get("wave_index") != current.get("wave_index")
            or str(spec.get("phase_kind") or "") != str(current.get("phase_kind") or "")
            or str(spec.get("operation_kind") or "") not in {"inspect", "modify", "verify", "close"}
        ):
            raise ValueError("effective plan assignment does not match its compiled wave")
        slot = (
            str(spec.get("logical_delegation_key") or "").strip(),
            str(spec.get("plan_assignment_lineage_digest") or "").strip(),
        )
        if not all(slot) or slot in expected_slots:
            raise ValueError("effective plan contains an ambiguous assignment slot")
        expected_slots[slot] = spec

    candidates_by_slot: dict[tuple[str, str], list[dict[str, Any]]] = {}
    extras: list[str] = []
    for attempt in state.get("attempts") or []:
        if not isinstance(attempt, dict) or attempt.get("invalidated"):
            continue
        attempt_wave_ref = str(attempt.get("wave_ref") or "").strip()
        if attempt_wave_ref != wave_ref:
            continue
        if (
            str(attempt.get("orchestration_wave_id") or "") != wave_ref
            or str(attempt.get("phase_ref") or "") != phase_ref
            or attempt.get("wave_index") != current.get("wave_index")
            or str(attempt.get("phase_kind") or "") != str(current.get("phase_kind") or "")
            or str(attempt.get("operation_kind") or "") not in {"inspect", "modify", "verify", "close"}
        ):
            extras.append(str(attempt.get("attempt_id") or ""))
            continue
        slot = (
            str(attempt.get("logical_delegation_key") or "").strip(),
            str(attempt.get("plan_assignment_lineage_digest") or "").strip(),
        )
        if slot not in expected_slots:
            extras.append(str(attempt.get("attempt_id") or ""))
            continue
        candidates_by_slot.setdefault(slot, []).append(attempt)
    # A stale cached assignment is never allowed to make the canonical plan
    # unreadable.  Pick the newest exact generation for the read projection;
    # ``reconcile_current_frontier`` durably retires every extra/older row and
    # records the repair.  The plan slot identity, not list position or phase
    # prose, remains authoritative.
    current_by_slot = {
        slot: candidates[-1]
        for slot, candidates in candidates_by_slot.items()
        if candidates
    }
    return current, [
        current_by_slot[slot]
        for slot in expected_slots
        if slot in current_by_slot
        and not (
            current_by_slot[slot].get("acceptance_status") == "passed"
            and current_by_slot[slot].get("continuation_consumed_at")
        )
    ]


def reconcile_current_frontier(
    plan: Mapping[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Repair cached assignment/current-wave projections from canonical plan.

    This function performs no I/O and opens no transaction.  Every lifecycle
    caller invokes it while holding its existing task transaction, then saves
    the returned state in that same unit of work.  Product results remain
    immutable; only stale non-canonical assignment projections are retired.
    """
    wave, selected = _effective_plan_frontier(plan, state)
    selected_ids = {
        str(item.get("attempt_id") or "") for item in selected
        if str(item.get("attempt_id") or "")
    }
    changed_attempts: list[str] = []
    if isinstance(wave, Mapping):
        wave_ref = str(wave.get("wave_ref") or "")
        phase_ref = str(wave.get("phase_ref") or "")
        expected_slots = {
            (
                str(spec.get("logical_delegation_key") or ""),
                str(spec.get("plan_assignment_lineage_digest") or ""),
            )
            for spec in wave.get("delegations") or []
            if isinstance(spec, Mapping)
        }
        # Recompute the latest exact generation per slot independently of the
        # filtered public frontier (which omits already-consumed passed rows).
        latest_by_slot: dict[tuple[str, str], str] = {}
        for attempt in state.get("attempts") or []:
            if (
                isinstance(attempt, Mapping)
                and not attempt.get("invalidated")
                and str(attempt.get("wave_ref") or "") == wave_ref
                and str(attempt.get("phase_ref") or "") == phase_ref
            ):
                slot = (
                    str(attempt.get("logical_delegation_key") or ""),
                    str(attempt.get("plan_assignment_lineage_digest") or ""),
                )
                if slot in expected_slots:
                    latest_by_slot[slot] = str(attempt.get("attempt_id") or "")
        selected_ids.update(latest_by_slot.values())
        for attempt in state.get("attempts") or []:
            if (
                not isinstance(attempt, dict)
                or attempt.get("invalidated")
                or str(attempt.get("wave_ref") or "") != wave_ref
            ):
                continue
            attempt_id = str(attempt.get("attempt_id") or "")
            exact = (
                str(attempt.get("phase_ref") or "") == phase_ref
                and (
                    str(attempt.get("logical_delegation_key") or ""),
                    str(attempt.get("plan_assignment_lineage_digest") or ""),
                ) in expected_slots
                and attempt_id in selected_ids
            )
            if exact:
                continue
            attempt["invalidated"] = True
            attempt["invalidated_at"] = now()
            attempt["invalidation_reason"] = "frontier_reconciliation_retired"
            changed_attempts.append(attempt_id)
        current_gates = [
            str(item) for item in wave.get("gates") or [] if str(item).strip()
        ]
        if current_gates and list(state.get("current_gates") or []) != current_gates:
            state["current_gates"] = list(current_gates)
            changed_attempts.append("current_wave_projection")
    if changed_attempts:
        receipt = {
            "schema": "cortex/frontier-reconciliation/v1",
            "wave_ref": str(wave.get("wave_ref") or "") if isinstance(wave, Mapping) else None,
            "phase_ref": str(wave.get("phase_ref") or "") if isinstance(wave, Mapping) else None,
            "retired_attempt_ids": [
                item for item in changed_attempts if item != "current_wave_projection"
            ],
            "current_wave_repaired": "current_wave_projection" in changed_attempts,
            "recorded_at": now(),
        }
        receipts = [
            item for item in state.get("frontier_reconciliation_receipts") or []
            if isinstance(item, Mapping)
        ]
        state["frontier_reconciliation_receipts"] = [*receipts[-31:], receipt]
    else:
        receipt = None
    wave, selected = _effective_plan_frontier(plan, state)
    return {
        "wave": wave,
        "attempts": selected,
        "changed": bool(changed_attempts),
        "receipt": receipt,
    }


def _has_open_orchestration_closure_obligations(
    state: Mapping[str, Any],
) -> bool:
    """Return whether exact post-wave work still forbids task completion."""
    closure_rework = state.get("closure_rework")
    if closure_rework is not None and not isinstance(closure_rework, Mapping):
        raise ValueError("closure rework authority must be an occurrence-keyed object")
    product_routes = state.get("product_rework_routes")
    if product_routes is not None and not isinstance(product_routes, Mapping):
        raise ValueError("product rework authority must be an occurrence-keyed object")
    product_route_open = False
    for route in (product_routes or {}).values():
        if not isinstance(route, Mapping):
            raise ValueError("product rework authority contains an invalid route")
        status = str(route.get("status") or "")
        if status not in {"active", "awaiting_close", "resolved", "superseded"}:
            raise ValueError("product rework authority has an invalid status")
        required = [
            "source_result_ref", "source_result_digest", "source_attempt_id", "source_wave_ref",
            "source_phase_ref", "source_logical_delegation_key",
            "source_plan_assignment_lineage_digest", "corrective_wave_ref",
            "corrective_phase_ref", "corrective_logical_delegation_key",
            "corrective_plan_assignment_lineage_digest", "verifier_wave_ref",
            "verifier_phase_ref", "verifier_logical_delegation_key",
            "verifier_plan_assignment_lineage_digest",
        ]
        if any(not str(route.get(field) or "") for field in required):
            raise ValueError("product rework authority lacks exact route identity")
        has_close = any(str(route.get(f"close_{field}") or "") for field in (
            "wave_ref", "phase_ref", "logical_delegation_key",
            "plan_assignment_lineage_digest",
        ))
        if has_close and any(not str(route.get(f"close_{field}") or "") for field in (
            "wave_ref", "phase_ref", "logical_delegation_key",
            "plan_assignment_lineage_digest",
        )):
            raise ValueError("product rework close authority is incomplete")
        if status in {"active", "awaiting_close"}:
            product_route_open = True
            continue
        if status == "superseded":
            continue
        resolved_roles = ["corrective", "verifier"] + (["close"] if has_close else [])
        if any(
            not str(route.get(f"{role}_result_ref") or "")
            or not str(route.get(f"{role}_result_digest") or "")
            or not isinstance(route.get(f"{role}_evidence"), Mapping)
            for role in resolved_roles
        ):
            raise ValueError("resolved product rework authority lacks exact role evidence")
    current_revision = int(state.get("task_revision") or 1)
    return bool(
        state.get("pending_product_reworks")
        or product_route_open
        or isinstance(state.get("pending_revision_impact"), Mapping)
        or any(
            isinstance(item, Mapping)
            and item.get("status") == "rework_required"
            and int(item.get("task_revision") or 0) == current_revision
            for item in (closure_rework or {}).values()
        )
    )


def _complete_orchestration_wave_occurrence(
    state: dict[str, Any], plan: dict[str, Any], wave: dict[str, Any],
) -> None:
    """Advance exactly one completed occurrence, allowing a later repeated phase."""
    wave_id = str(wave.get("wave_id") or "").strip()
    if not wave_id:
        raise ValueError("completed orchestration wave has no durable identity")
    completed = list(state.get("completed_orchestration_wave_ids") or [])
    if wave_id not in completed:
        completed.append(wave_id)
    state["completed_orchestration_wave_ids"] = completed
    _sync_orchestration_wave_occurrences(state, plan)
    next_wave, _next_frontier = _effective_plan_frontier(plan, state)
    if next_wave is not None:
        next_gates = [
            str(item) for item in next_wave.get("gates") or [] if str(item).strip()
        ]
        if not next_gates:
            raise ValueError("effective successor wave has no semantic projection")
        # This semantic state is presentation and delegation preflight only.
        # The exact successor wave_ref above is the execution authority, so a
        # repeated phase kind is deliberately reopened for its new occurrence.
        state["completed_gates"] = [
            gate for gate in state.get("completed_gates") or [] if gate not in next_gates
        ]
        state["skipped_gates"] = [
            gate for gate in state.get("skipped_gates") or [] if gate not in next_gates
        ]
        state["status"] = "active"
        sync_current_wave(state)
    elif _has_open_orchestration_closure_obligations(state):
        # A closure route must compile its successor occurrence before task
        # completion is legal. Keep the task executable/recoverable instead
        # of allowing a semantic gate projection to erase the obligation.
        state["status"] = "active"
        sync_current_wave(state)
    else:
        state["status"] = "completed"
        sync_current_wave(state)


def _migrate_compiled_assignment_identity(
    task_dir: Path, state: dict[str, Any], plan: dict[str, Any],
) -> bool:
    """One-way compile active pre-instance plans into the current canonical form."""
    waves = [item for item in plan.get("waves") or [] if isinstance(item, dict)]
    if not waves:
        raise ValueError("canonical orchestration plan has no waves")
    required_wave = {"wave_ref", "wave_index", "phase_ref", "phase_kind"}
    required_spec = {
        "wave_ref", "wave_index", "phase_ref", "phase_kind", "operation_kind",
        "read_only", "can_write", "predecessor_wave_refs", "required_verification_kinds",
    }
    canonical_plan = all(
        required_wave.issubset(wave)
        and str(wave.get("wave_id") or "") == str(wave.get("wave_ref") or "")
        and all(
            required_spec.issubset(spec)
            and str(spec.get("profile") or "") == str(spec.get("agent") or "")
            and str(spec.get("wave_ref") or "") == str(wave.get("wave_ref") or "")
            and str(spec.get("phase_ref") or "") == str(wave.get("phase_ref") or "")
            and str(spec.get("orchestration_wave_id") or "") == str(wave.get("wave_ref") or "")
            and str(spec.get("logical_delegation_key") or "").startswith(
                f"{str(wave.get('phase_ref') or '')}-"
            )
            for spec in wave.get("delegations") or [] if isinstance(spec, dict)
        )
        for wave in waves
    )
    compiled_attempt_fields = required_spec | {
        "profile", "orchestration_wave_id", "logical_delegation_key",
        "assignment_lineage_digest", "plan_assignment_lineage_digest",
        "plan_revision", "plan_digest",
    }
    canonical_attempts = all(
        compiled_attempt_fields.issubset(attempt)
        and str(attempt.get("orchestration_wave_id") or "") == str(attempt.get("wave_ref") or "")
        and str(attempt.get("logical_delegation_key") or "").startswith(
            f"{str(attempt.get('phase_ref') or '')}-"
        )
        for attempt in state.get("attempts") or []
        if isinstance(attempt, dict) and attempt.get("facade_managed")
    )
    if canonical_plan and canonical_attempts:
        removed_cached_frontier = any(
            "attempt_ids" in wave or "executable_gates" in wave
            for wave in waves
        )
        if removed_cached_frontier:
            for wave in waves:
                wave.pop("attempt_ids", None)
                wave.pop("executable_gates", None)
            root = _ledger_root_for_artifact(task_dir)
            ledger_db._persist_v19_migrated_plan(
                root, str(state.get("task_id") or ""), plan,
            )
        return removed_cached_frontier
    inspect_phases = {"scope", "plan", "discover", "architecture", "database_architecture", "ux"}
    verify_phases = {
        "qa", "security", "performance", "accessibility", "review", "governance_activation",
    }
    close_phases = {"close", "governance_close"}
    attempts = [item for item in state.get("attempts") or [] if isinstance(item, dict)]
    authority = ledger_db.get_active_plan_revision(
        _ledger_root_for_artifact(task_dir), str(state.get("task_id") or ""),
    )
    plan_revision = int(
        (authority or {}).get("plan_revision") or state.get("plan_revision") or 1
    )
    plan_digest = sha256_hex(
        (authority or {}).get("plan_digest") or state.get("plan_digest")
    )
    if not plan_digest:
        raise ValueError("compiled assignment migration requires exact active plan identity")
    package_updates: list[tuple[str, dict[str, Any]]] = []
    migrated_attempt_ids: set[str] = set()
    for position, wave in enumerate(waves, 1):
        gates = [str(item) for item in wave.get("gates") or [] if str(item).strip()]
        if len(gates) != 1:
            raise ValueError("pre-compiler wave migration requires one unambiguous semantic phase")
        phase_kind = gates[0]
        wave_ref, phase_ref = f"wave-{position:02d}", f"phase-{position:04d}"
        old_wave_id = str(wave.get("wave_id") or "")
        old_current_attempt_ids = {
            str(item) for item in wave.get("attempt_ids") or [] if str(item).strip()
        }
        wave.pop("attempt_ids", None)
        wave.pop("executable_gates", None)
        wave.update({
            "wave_id": wave_ref, "wave_ref": wave_ref, "wave_index": position,
            "phase_ref": phase_ref, "phase_kind": phase_kind,
        })
        specs = [item for item in wave.get("delegations") or [] if isinstance(item, dict)]
        if not specs:
            raise ValueError("pre-compiler wave migration requires an assignment")
        for slot, spec in enumerate(specs, 1):
            old_delegation_key = str(spec.get("orchestration_delegation_key") or "").strip()
            old_logical_key = str(spec.get("logical_delegation_key") or "").strip()
            if phase_kind in close_phases:
                operation_kind = "close"
            elif phase_kind in verify_phases:
                operation_kind = "verify"
            elif phase_kind in inspect_phases:
                operation_kind = "inspect"
            else:
                operation_kind = ""
            profile_name = str(spec.get("profile") or spec.get("agent") or "").strip()
            profile = PROFILES.get(profile_name)
            if not isinstance(profile, dict) and operation_kind:
                candidates = [
                    (name, value) for name, value in PROFILES.items()
                    if isinstance(value, dict)
                    and phase_kind in (value.get("gates") or [])
                    and operation_kind in (value.get("operation_kinds") or [])
                ]
                if len(candidates) != 1:
                    raise ValueError("pre-compiler assignment profile migration is ambiguous")
                profile_name, profile = candidates[0]
            capabilities = profile.get("operation_kinds") if isinstance(profile, dict) else None
            if not isinstance(capabilities, list):
                raise ValueError("pre-compiler assignment profile capability is unavailable")
            if not operation_kind:
                if "modify" not in capabilities:
                    raise ValueError("pre-compiler assignment mutability is ambiguous")
                operation_kind = "modify"
            if operation_kind not in capabilities:
                raise ValueError("pre-compiler assignment conflicts with profile operation capability")
            read_only = operation_kind != "modify"
            spec.update({
                "agent": profile_name, "profile": profile_name,
                "phase_kind": phase_kind, "phase_ref": phase_ref,
                "wave_ref": wave_ref, "wave_index": position,
                "operation_kind": operation_kind, "read_only": read_only,
                "can_write": not read_only,
                "predecessor_wave_refs": [f"wave-{prior:02d}" for prior in range(1, position)],
                "required_verification_kinds": list(
                    required_verification_kinds(phase_kind, operation_kind)
                ),
                "plan_revision": plan_revision,
                "plan_digest": plan_digest,
                "orchestration_wave_id": wave_ref,
                "orchestration_delegation_key": f"{wave_ref}-{phase_kind}-{slot:02d}",
                "logical_delegation_key": f"{phase_ref}-{phase_kind}-{slot:02d}",
            })
            spec["assignment_lineage_digest"] = _assignment_lineage_digest(spec)
            spec["plan_assignment_lineage_digest"] = spec["assignment_lineage_digest"]
            matching_attempts = [
                item for item in attempts
                if str(item.get("orchestration_wave_id") or "") == old_wave_id
                and str(item.get("gate") or "") == phase_kind
                and str(item.get("attempt_id") or "") not in migrated_attempt_ids
                and (
                    len(specs) == 1
                    or (
                        old_delegation_key
                        and str(item.get("orchestration_delegation_key") or "") == old_delegation_key
                    )
                    or (
                        old_logical_key
                        and str(item.get("logical_delegation_key") or "") == old_logical_key
                    )
                )
            ]
            if len(specs) > 1 and not matching_attempts and any(
                str(item.get("orchestration_wave_id") or "") == old_wave_id
                and str(item.get("gate") or "") == phase_kind
                and str(item.get("attempt_id") or "") not in migrated_attempt_ids
                for item in attempts
            ):
                raise ValueError("pre-compiler assignment-to-attempt identity is ambiguous")
            for attempt in matching_attempts:
                attempt_id = str(attempt.get("attempt_id") or "")
                migrated_attempt_ids.add(attempt_id)
                attempt.update({
                    key: spec[key] for key in (
                        "agent", "profile",
                        "phase_kind", "phase_ref", "wave_ref", "wave_index", "operation_kind",
                        "read_only", "can_write", "predecessor_wave_refs",
                        "required_verification_kinds",
                        "orchestration_wave_id", "orchestration_delegation_key",
                        "logical_delegation_key", "assignment_lineage_digest",
                        "plan_assignment_lineage_digest",
                        "plan_revision", "plan_digest",
                    )
                })
                if (
                    old_current_attempt_ids
                    and attempt_id not in old_current_attempt_ids
                    and str(attempt.get("status") or "")
                    in {"passed", "failed", "blocked", "cancelled", "superseded"}
                ):
                    attempt["invalidated"] = True
                    attempt["invalidated_at"] = attempt.get("invalidated_at") or now()
                    attempt["invalidation_reason"] = (
                        attempt.get("invalidation_reason")
                        or "retired_by_compiled_frontier_migration"
                    )
                package = _delegation_package(
                    task_dir, str(state["task_id"]), attempt_id,
                )
                package.update({
                    key: attempt[key] for key in (
                        "agent", "profile",
                        "phase_kind", "phase_ref", "wave_ref", "wave_index", "operation_kind",
                        "read_only", "can_write", "predecessor_wave_refs",
                        "required_verification_kinds",
                        "orchestration_wave_id", "orchestration_delegation_key",
                        "logical_delegation_key", "assignment_lineage_digest",
                        "plan_assignment_lineage_digest",
                    )
                })
                package_updates.append((attempt_id, package))
    # Retired historical generations may no longer have a slot in the
    # effective plan, but their immutable result/event identity must still be
    # complete.  Compile the evidence-binding fields from their already
    # server-issued occurrence identity; never revive them or attach them to a
    # current slot.
    for attempt in attempts:
        if not attempt.get("facade_managed") or str(attempt.get("attempt_id") or "") in migrated_attempt_ids:
            continue
        operation_kind = str(attempt.get("operation_kind") or "")
        phase_kind = str(attempt.get("phase_kind") or attempt.get("gate") or "")
        if (
            operation_kind not in {"inspect", "modify", "verify", "close"}
            or not phase_kind
            or not str(attempt.get("phase_ref") or "")
            or not str(attempt.get("wave_ref") or "")
        ):
            raise ValueError("retired compiled assignment identity is incomplete")
        attempt["required_verification_kinds"] = list(
            required_verification_kinds(phase_kind, operation_kind)
        )
        if not isinstance(attempt.get("plan_revision"), int) or isinstance(attempt.get("plan_revision"), bool):
            attempt["plan_revision"] = plan_revision
        if not sha256_hex(attempt.get("plan_digest")):
            attempt["plan_digest"] = plan_digest
        attempt_id = str(attempt["attempt_id"])
        package = _delegation_package(task_dir, str(state["task_id"]), attempt_id)
        package.update({
            "required_verification_kinds": list(attempt["required_verification_kinds"]),
            "plan_revision": attempt["plan_revision"],
            "plan_digest": attempt["plan_digest"],
        })
        package_updates.append((attempt_id, package))
    state["completed_orchestration_wave_ids"] = [
        str(wave["wave_ref"])
        for wave in waves
        if str(wave.get("status") or "") == "completed"
    ]
    state["skipped_orchestration_wave_ids"] = [
        str(wave["wave_ref"])
        for wave in waves
        if str(wave.get("status") or "") == "skipped"
    ]
    _sync_orchestration_wave_occurrences(state, plan)
    root = _ledger_root_for_artifact(task_dir)
    with ledger_db.transaction(root):
        for attempt_id, package in package_updates:
            _write_delegation_package(
                task_dir, str(state["task_id"]), attempt_id, package,
            )
        ledger_db._persist_v19_migrated_plan(
            root, str(state.get("task_id") or ""), plan,
        )
        save_state(
            task_dir, task_dir / "state.sqlite", state,
            "compiled_assignment_migration", "compiled active plan into occurrence-bound assignments",
        )
    return True


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

    ``update_pipeline`` may retain policy assessment for its own advisory
    operation. The orchestration engine has a different contract: a valid coordinator choice
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
            "phase": wave.get("phase"),
            "gates": wave.get("gates"),
            "delegations": wave.get("delegations"),
        }
        for wave in waves
        if isinstance(wave, dict)
    ]


def _coordinator_plan_digest(waves: object) -> str:
    """Bind generated planning artifacts to the exact normalized start waves."""
    return digest_text(canonical_json.dumps(_orchestrate_wave_contract(waves)))


def _coordinator_planning_payload(
    task: dict[str, Any],
    waves: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive the machine plan solely from coordinator-authored start waves.

    Worker completion remains one free-form report.  All structure required by
    scheduling, plan review, and the live tracker is generated here from the
    already validated orchestration plan instead of being reconstructed from a
    planner's prose.
    """
    compiled_wave_execution_order(waves)
    package_by_wave_ref: dict[str, str] = {}
    package_order_by_wave_ref: dict[str, int] = {}
    package_identity_by_wave_ref: dict[str, tuple[str, str]] = {}
    phase_refs: set[str] = set()
    for execution_order, wave in enumerate(waves, 1):
        if not isinstance(wave, dict):
            raise ValueError("coordinator planning waves must be objects")
        wave_ref = str(wave.get("wave_ref") or "").strip()
        phase_ref = str(wave.get("phase_ref") or "").strip()
        if (
            not wave_ref
            or not phase_ref
            or wave_ref in package_by_wave_ref
            or phase_ref in phase_refs
        ):
            raise ValueError("coordinator planning requires unique canonical wave/phase occurrence identity")
        package_id = f"package-{wave_ref}-{phase_ref}"
        package_by_wave_ref[wave_ref] = package_id
        package_order_by_wave_ref[wave_ref] = execution_order
        package_identity_by_wave_ref[wave_ref] = (package_id, phase_ref)
        phase_refs.add(phase_ref)
    packages: list[dict[str, Any]] = []
    microtask_ids: set[str] = set()
    for execution_order, wave in enumerate(waves, 1):
        phase = canonical_pipeline_gate(wave.get("phase_kind") or "")
        wave_ref = str(wave.get("wave_ref") or "").strip()
        package_id, phase_ref = package_identity_by_wave_ref[wave_ref]
        delegations = [item for item in wave.get("delegations") or [] if isinstance(item, dict)]
        microtasks: list[dict[str, Any]] = []
        package_dependencies: list[str] = []
        package_artifacts: list[dict[str, Any]] = []
        for spec_index, spec in enumerate(delegations, 1):
            gate = canonical_pipeline_gate(spec.get("gate") or phase)
            if (
                str(spec.get("wave_ref") or "") != wave_ref
                or str(spec.get("phase_ref") or "") != phase_ref
            ):
                raise ValueError("coordinator planning assignment is not bound to its exact wave occurrence")
            for predecessor_wave_ref in spec.get("predecessor_wave_refs") or []:
                predecessor_ref = str(predecessor_wave_ref or "")
                dependency = package_by_wave_ref.get(predecessor_ref)
                if dependency and package_order_by_wave_ref[predecessor_ref] >= execution_order:
                    raise ValueError("coordinator planning dependency is not an earlier wave occurrence")
                if dependency and dependency != package_id and dependency not in package_dependencies:
                    package_dependencies.append(dependency)
            required_artifacts = [
                dict(item) for item in spec.get("required_artifacts") or [] if isinstance(item, dict)
            ]
            package_artifacts.extend(required_artifacts)
            delegation_key = str(spec.get("orchestration_delegation_key") or "").strip()
            microtask_id = safe_id(delegation_key)
            if (
                not delegation_key
                or microtask_id != delegation_key
                or microtask_id in microtask_ids
            ):
                raise ValueError("coordinator planning assignment identity is invalid")
            microtask_ids.add(microtask_id)
            microtasks.append({
                "id": microtask_id,
                "title": f"{gate.replace('_', ' ').title()} worker {spec_index}",
                "objective": str(spec.get("objective") or f"Complete the {gate} phase."),
                "profile": str(spec.get("agent") or _default_profile_for_gate(gate)),
                "status": "pending",
                "order": spec_index,
                "gates": [gate],
                "depends_on": [],
                "acceptance_criteria": [str(item) for item in spec.get("acceptance_criteria") or []],
                "verification": [str(item) for item in spec.get("verification") or []],
                "required_artifacts": required_artifacts,
            })
        packages.append({
            "id": package_id,
            "title": (
                f"{phase.replace('_', ' ').title()} phase "
                f"[{wave_ref}/{phase_ref}]"
            ),
            "objective": "\n".join(str(item.get("objective") or "") for item in delegations).strip()
            or f"Complete the {phase} phase.",
            "status": "pending",
            "order": execution_order,
            "gates": [phase],
            "depends_on": package_dependencies,
            "required_artifacts": package_artifacts,
            "microtasks": microtasks,
        })
    plan_refs = [str(package["id"]) for package in packages]
    user_request = str(task.get("user_request") or "").strip()
    return {
        "schema": PLANNING_SCHEMA,
        "overview": user_request,
        "work_packages": packages,
        "requirement_coverage": ([{
            "requirement": user_request,
            "plan_refs": plan_refs,
            "verification": [str(item) for item in task.get("verification") or []],
            "status": "covered",
        }] if user_request and plan_refs else []),
        "recommendation": "approve",
        "recommendation_rationale": "The coordinator-authored waves passed the canonical start validation.",
        "recommendation_actions": [],
        "resolved_questions": [],
        "risks": [],
    }


def _materialize_coordinator_plan(
    task_dir: Path,
    state: dict[str, Any],
    task: dict[str, Any],
    plan: dict[str, Any],
    *,
    replace_stale_current: bool = False,
) -> dict[str, Any]:
    """Publish one idempotent immutable plan derived from normalized waves."""
    task_id = safe_id(str(state.get("task_id") or ""))
    root = _ledger_root_for_artifact(task_dir)
    plan_digest = _coordinator_plan_digest(plan.get("waves"))
    planning = _coordinator_planning_payload(task, list(plan.get("waves") or []))
    revision = f"plan-coordinator-{plan_digest[:24]}"
    revision_root = f"planning/revisions/{revision}"
    existing = current_planning_manifest(task_dir)
    replace_colliding_packages = False
    if isinstance(existing, dict):
        if existing.get("source_authority") != "coordinator_start_waves":
            raise ValueError("current planning manifest does not match the coordinator-authored start waves")
        if (
            existing.get("source_plan_digest") != plan_digest
            and not replace_stale_current
        ):
            raise ValueError("current planning manifest does not match the coordinator-authored start waves")
        expected_summaries = [
            {
                "id": str(package["id"]),
                "title": str(package["title"]),
                "depends_on": list(package.get("depends_on") or []),
                "microtask_count": len(package.get("microtasks") or []),
                "artifact_path": f"{revision_root}/packages/{package['id']}.json",
            }
            for package in planning["work_packages"]
        ]
        actual_summaries = existing.get("work_packages")
        occurrence_scoped = (
            existing.get("revision") == revision
            and isinstance(actual_summaries, list)
            and len(actual_summaries) == len(expected_summaries)
            and len({str(item.get("id") or "") for item in actual_summaries if isinstance(item, dict)})
            == len(expected_summaries)
            and len({str(item.get("artifact_path") or "") for item in actual_summaries if isinstance(item, dict)})
            == len(expected_summaries)
        )
        if occurrence_scoped:
            for actual, expected, package in zip(
                actual_summaries, expected_summaries, planning["work_packages"], strict=True,
            ):
                if not isinstance(actual, dict) or any(
                    actual.get(key) != expected[key]
                    for key in ("id", "title", "depends_on", "microtask_count", "artifact_path")
                ) or not str(actual.get("artifact_ref") or ""):
                    occurrence_scoped = False
                    break
                try:
                    record, _metadata = read_immutable_json_artifact(
                        task_dir,
                        task_id,
                        expected["artifact_path"],
                        kinds={"planning_revision"},
                    )
                except (FileNotFoundError, ValueError):
                    occurrence_scoped = False
                    break
                if (
                    record.get("schema") != PLANNING_SCHEMA
                    or record.get("revision") != revision
                    or record.get("source_authority") != "coordinator_start_waves"
                    or record.get("source_plan_digest") != plan_digest
                    or canonical_json.dumps(record.get("package"))
                    != canonical_json.dumps(package)
                ):
                    occurrence_scoped = False
                    break
        if occurrence_scoped:
            return existing
        replace_colliding_packages = True
    overview_path = f"{revision_root}/overview.md"
    summaries: list[dict[str, Any]] = []
    package_artifacts: list[tuple[dict[str, Any], str, str]] = []
    for package in planning["work_packages"]:
        package_id = str(package["id"])
        package_path = f"{revision_root}/packages/{package_id}.json"
        package_artifacts.append(({
            "schema": PLANNING_SCHEMA,
            "revision": revision,
            "source_authority": "coordinator_start_waves",
            "source_plan_digest": plan_digest,
            "package": package,
        }, package_id, package_path))
        summaries.append({
            "id": package_id,
            "title": package["title"],
            "depends_on": list(package.get("depends_on") or []),
            "microtask_count": len(package.get("microtasks") or []),
            "artifact_path": package_path,
        })
    manifest = {
        "schema": PLANNING_SCHEMA,
        "revision": revision,
        "source_authority": "coordinator_start_waves",
        "source_plan_digest": plan_digest,
        "overview": planning["overview"],
        "overview_artifact_path": overview_path,
        "overview_artifact_ref": None,
        "work_packages": summaries,
        "requirement_coverage": list(planning.get("requirement_coverage") or []),
        "recommendation": "approve",
        "recommendation_rationale": planning["recommendation_rationale"],
        "recommendation_actions": [],
        "resolved_questions": [],
        "risks": [],
        "created_at": now(),
        "updated_at": now(),
    }
    tracker_items = [
        {
            "kind": kind,
            "id": str(item.get("id") or ""),
            "title": item.get("title"),
            "objective": item.get("objective"),
            "status": item.get("status") or "pending",
            "order": item.get("order", 1),
            "gates": list(item.get("gates") or []),
            "depends_on": list(item.get("depends_on") or []),
            "acceptance_criteria": list(item.get("acceptance_criteria") or []),
            "verification": list(item.get("verification") or []),
            "required_artifacts": list(item.get("required_artifacts") or []),
            "package_id": package_id,
            **({"profile": item.get("profile")} if kind == "microtask" else {}),
        }
        for package in planning["work_packages"]
        for package_id in [str(package["id"])]
        for kind, item in [("package", package)] + [
            ("microtask", microtask)
            for microtask in package.get("microtasks") or []
            if isinstance(microtask, dict)
        ]
    ]
    tracker = {
        "schema": PLAN_TRACKER_SCHEMA,
        "task_id": task_id,
        "revision": revision,
        "source_authority": "coordinator_start_waves",
        "source_plan_digest": plan_digest,
        "task_revision": int(state.get("task_revision") or 1),
        "recommendation": "approve",
        "recommendation_rationale": planning["recommendation_rationale"],
        "recommendation_actions": [],
        "requirement_coverage": list(planning.get("requirement_coverage") or []),
        "resolved_questions": [],
        "risks": [],
        "plan_items": tracker_items,
        "items": [],
        "updated_at": now(),
        "last_event": "plan_created",
    }
    overview = "# Work plan\n\n" + planning["overview"] + "\n"
    with db_transaction(root):
        if replace_colliding_packages:
            ledger_db.delete_planning_revision_package_artifacts(
                root, task_id, revision,
            )
        overview_metadata = store_immutable_artifact(
            task_dir, task_id,
            kind="planning_revision",
            title=f"{revision}:overview",
            mime_type="text/markdown; charset=utf-8",
            content=overview,
            export_path=overview_path,
        )
        manifest["overview_artifact_ref"] = overview_metadata["artifact_ref"]
        for package_record, package_id, package_path in package_artifacts:
            metadata = store_immutable_artifact(
                task_dir, task_id,
                kind="planning_revision",
                title=f"{revision}:package:{package_id}",
                mime_type="application/json",
                content=canonical_json.dumps(package_record),
                export_path=package_path,
            )
            next(item for item in summaries if item["id"] == package_id)["artifact_ref"] = metadata["artifact_ref"]
        db_put_task_document(root, task_id, "planning_current", manifest)
        db_put_task_document(root, task_id, "plan_tracker_current", tracker)
    return manifest


def _migrate_coordinator_planning_package_identity(
    task_dir: Path, state: dict[str, Any], plan: dict[str, Any],
) -> bool:
    """Atomically hard-cut persisted V19 coordinator packages to occurrences."""
    existing = current_planning_manifest(task_dir)
    if not isinstance(existing, dict):
        return False
    if existing.get("source_authority") != "coordinator_start_waves":
        return False
    before = canonical_json.dumps(existing)
    migrated = _materialize_coordinator_plan(
        task_dir,
        state,
        load_task_definition(task_dir, state),
        plan,
        replace_stale_current=True,
    )
    return canonical_json.dumps(migrated) != before


def _load_orchestrate_plan(task_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Load the canonical plan; tasks without one are not orchestration tasks."""
    loaded = db_load_task(_ledger_root_for_artifact(task_dir), safe_id(str(state.get("task_id") or "")))
    plan = loaded[2] if loaded is not None else None
    if plan is None:
        raise ValueError("canonical orchestration plan is missing from the SQLite task record")
    if plan.get("schema") != ORCHESTRATION_PLAN_SCHEMA or plan.get("task_id") != state.get("task_id"):
        raise ValueError("orchestrate plan schema or task identity is not supported")
    state_version = _pipeline_contract_version(state)
    plan_version = plan.get("pipeline_contract_version")
    if plan_version != state_version:
        raise ValueError(
            "orchestrate plan pipeline_contract_version does not match the current canonical state"
        )
    required_wave = {"wave_ref", "wave_index", "phase_ref", "phase_kind"}
    required_assignment = {
        "wave_ref", "wave_index", "phase_ref", "phase_kind", "operation_kind",
        "profile", "predecessor_wave_refs", "required_verification_kinds",
        "logical_delegation_key", "plan_assignment_lineage_digest",
    }
    if not all(
        required_wave.issubset(wave)
        and str(wave.get("wave_id") or "") == str(wave.get("wave_ref") or "")
        and all(
            required_assignment.issubset(spec)
            for spec in wave.get("delegations") or [] if isinstance(spec, dict)
        )
        for wave in plan.get("waves") or [] if isinstance(wave, dict)
    ):
        raise ValueError("canonical V19 orchestration assignment identity is incomplete")
    _sync_orchestration_wave_occurrences(state, plan)
    reconcile_current_frontier(plan, state)
    return plan


def _finalized_result_refs_for_gates(
    state: dict[str, Any],
    gate_filter: set[str] | None = None,
) -> list[str]:
    """Select finalized AttemptResults from completed gates for governance reads.

    This helper is for server-side governance projections. Dispatch dependency
    routing is occurrence-based and uses ``_predecessor_result_refs_for_assignment``.
    """
    completed = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    selected: list[str] = []
    for attempt in state.get("attempts", []):
        if (
            not isinstance(attempt, dict)
            or attempt.get("status") != "passed"
            or attempt.get("invalidated")
            or attempt.get("gate") not in completed
            or (gate_filter is not None and attempt.get("gate") not in gate_filter)
        ):
            continue
        raw_result_ref = str(attempt.get("attempt_result_ref") or "").strip()
        if not raw_result_ref:
            continue
        result_ref = safe_id(raw_result_ref)
        if result_ref and result_ref not in selected:
            selected.append(result_ref)
    return selected


def _predecessor_result_refs_for_assignment(
    state: dict[str, Any],
    spec: Mapping[str, Any],
) -> list[str]:
    """Derive exact predecessor results from compiled occurrence dependencies."""
    predecessor_wave_refs = spec.get("predecessor_wave_refs")
    if not isinstance(predecessor_wave_refs, list) or any(
        not isinstance(item, str) or not item for item in predecessor_wave_refs
    ):
        raise ValueError("compiled assignment predecessor_wave_refs must be a string array")
    if len(predecessor_wave_refs) != len(set(predecessor_wave_refs)):
        raise ValueError("compiled assignment predecessor wave identities must be unique")
    # The required frontier is deliberately small.  Ordinary workers require
    # the complete immediate predecessor occurrence; close workers additionally
    # require every accepted verification occurrence.  Older canonical task
    # reports remain available through the dispatch-scoped optional catalog.
    predecessor_waves = set(predecessor_wave_refs[-1:])
    selected: list[str] = []
    for attempt in state.get("attempts", []):
        if (
            not isinstance(attempt, dict)
            or attempt.get("status") != "passed"
            or attempt.get("invalidated")
            or str(attempt.get("wave_ref") or attempt.get("orchestration_wave_id") or "")
            not in predecessor_waves
        ):
            continue
        raw_result_ref = str(attempt.get("attempt_result_ref") or "").strip()
        if not raw_result_ref:
            continue
        result_ref = safe_id(raw_result_ref)
        if result_ref and result_ref not in selected:
            selected.append(result_ref)
    if str(spec.get("operation_kind") or "") == "close":
        for attempt in state.get("attempts", []):
            if (
                not isinstance(attempt, Mapping)
                or attempt.get("status") != "passed"
                or attempt.get("invalidated")
                or str(attempt.get("wave_ref") or "") not in set(predecessor_wave_refs)
                or str(attempt.get("operation_kind") or "") != "verify"
            ):
                continue
            result_ref = str(attempt.get("attempt_result_ref") or "").strip()
            if result_ref and result_ref not in selected:
                selected.append(result_ref)
    recovery_source_ref = str(spec.get("recovery_source_result_ref") or "").strip()
    if recovery_source_ref:
        exact_sources = [
            attempt for attempt in state.get("attempts", [])
            if isinstance(attempt, Mapping)
            and str(attempt.get("attempt_result_ref") or "") == recovery_source_ref
            and str(attempt.get("logical_delegation_key") or "")
            == str(spec.get("recovery_source_logical_delegation_key") or "")
            and str(attempt.get("plan_assignment_lineage_digest") or "")
            == str(spec.get("recovery_source_plan_assignment_lineage_digest") or "")
        ]
        if len(exact_sources) != 1:
            raise ValueError("technical recovery source result does not match its exact retired assignment")
        if recovery_source_ref not in selected:
            selected.append(recovery_source_ref)
    recovery_chain_refs = [
        str(item).strip() for item in spec.get("recovery_chain_result_refs") or []
        if str(item).strip()
    ]
    if len(recovery_chain_refs) != len(set(recovery_chain_refs)):
        raise ValueError("technical recovery result chain contains duplicate references")
    for chain_ref in recovery_chain_refs:
        exact_sources = [
            attempt for attempt in state.get("attempts", [])
            if isinstance(attempt, Mapping)
            and str(attempt.get("attempt_result_ref") or "") == chain_ref
        ]
        if len(exact_sources) != 1:
            raise ValueError("technical recovery result chain is not task-authorized")
        if chain_ref not in selected:
            selected.append(chain_ref)
    return selected


def _report_catalog_result_refs_for_assignment(
    state: Mapping[str, Any],
    spec: Mapping[str, Any],
    required_refs: Sequence[str],
) -> list[str]:
    """Return every prior canonical task report eligible for this dispatch."""
    predecessor_wave_refs = {
        str(item) for item in spec.get("predecessor_wave_refs") or [] if str(item)
    }
    selected: list[str] = []
    for attempt in state.get("attempts") or []:
        if not isinstance(attempt, Mapping):
            continue
        result_ref = str(attempt.get("attempt_result_ref") or "").strip()
        wave_ref = str(attempt.get("wave_ref") or "")
        if not result_ref or wave_ref not in predecessor_wave_refs:
            continue
        if result_ref not in selected:
            selected.append(result_ref)
    for result_ref in required_refs:
        if result_ref and result_ref not in selected:
            selected.append(result_ref)
    return selected


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
        if isinstance(microtask, dict) and "implementation" in {
            canonical_pipeline_gate(gate) for gate in microtask.get("gates") or []
        }
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
            str(item["id"])
            for item in package.get("microtasks", [])
            if isinstance(item, dict) and str(item.get("id") or "") in by_id
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
    profiles = [str(item.get("profile") or "") for item in ordered if str(item.get("profile") or "")]
    unknown_profiles = sorted(set(profiles) - set(PROFILES))
    if unknown_profiles:
        raise ValueError("approved planning microtask profile is unavailable")
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
    if agent not in PROFILES:
        raise ValueError("approved planning microtask profile is unavailable")
    base = dict((wave.get("delegations") or [{}])[0])
    authored_mission = str(base.get("objective") or "").strip()
    compiled_mission = "Execute the approved immutable coordinator plan in dependency order."
    mission = "\n\n".join(dict.fromkeys(
        value for value in (authored_mission, compiled_mission) if value
    ))
    acceptance = list(dict.fromkeys(
        str(value)
        for source in (
            base.get("acceptance_criteria", []),
            *(item.get("acceptance_criteria", []) for item in ordered),
        )
        for value in source
        if str(value).strip()
    ))
    verification = list(dict.fromkeys(
        str(value)
        for source in (
            base.get("verification", []),
            *(item.get("verification", []) for item in ordered),
        )
        for value in source
        if str(value).strip()
    ))
    revision = str(manifest.get("revision") or "")
    plan_unit = {
        "schema": "cortex/compiled-plan-unit/v1",
        "plan_revision": revision,
        "source_authority": manifest.get("source_authority"),
        "source_plan_digest": manifest.get("source_plan_digest"),
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
                "depends_on": item.get("depends_on") or [],
                "acceptance_criteria": item.get("acceptance_criteria") or [],
                "verification": item.get("verification") or [],
                "required_artifacts": item.get("required_artifacts") or [],
            }
            for index, item in enumerate(ordered, 1)
        ],
    }
    compiled = {
        **base,
        "gate": "implementation",
        "agent": agent,
        # The exact work breakdown remains in its digest-bound immutable
        # artifact, but the coordinator-authored mission is independent
        # semantic authority. Preserve it verbatim in the briefing-facing
        # objective, then append only the compact execution-order directive;
        # never replace user-decision or other mission obligations with a
        # generic implementation summary.
        "objective": mission,
        "selection_reason": (
            f"Compiled from approved plan revision {revision}; selected {agent} from microtask profiles while "
            "preventing deployment-only routing from owning application-code changes."
        ),
        "acceptance_criteria": acceptance,
        "verification": verification,
        "plan_unit": plan_unit,
        "orchestration_delegation_key": (
            f"{wave['wave_id']}-implementation-plan-{digest_text(revision)[:12]}"
        ),
    }
    logical_key = str(base.get("logical_delegation_key") or "").strip()
    if not logical_key:
        raise ValueError("compiled implementation assignment lacks its normalized logical delegation key")
    compiled["logical_delegation_key"] = logical_key
    plan_lineage = str(base.get("plan_assignment_lineage_digest") or "").strip()
    if not plan_lineage:
        raise ValueError("compiled implementation assignment lacks normalized plan authority lineage")
    compiled["plan_assignment_lineage_digest"] = plan_lineage
    compiled["assignment_lineage_digest"] = _assignment_lineage_digest(compiled)
    return compiled



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
    for _route_key, rework in (state.get("closure_rework") or {}).items():
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
        raw_finding_origins = rework.get("finding_origin_result_refs")
        finding_origins = {
            fingerprint: {
                str(item) for item in (
                    raw_finding_origins.get(fingerprint) or source_refs
                ) if str(item)
            }
            for fingerprint in fingerprints
        } if isinstance(raw_finding_origins, dict) else {
            fingerprint: set(source_refs) for fingerprint in fingerprints
        }
        origin_gate = str(rework.get("origin_gate") or "")
        verifier_gate = str(rework.get("verifier_gate") or "")
        if target_gate and origin_gate and verifier_gate and source_refs and fingerprints:
            active_routes.append({
                "origin_gate": origin_gate,
                "verifier_gate": verifier_gate,
                "target_gate": target_gate,
                "source_refs": source_refs,
                "fingerprints": fingerprints,
                "finding_origins": finding_origins,
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
            for origin_result_ref in sorted(route["finding_origins"].get(fingerprint) or []):
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
    for _route_key, rework in (state.get("closure_rework") or {}).items():
        # A corrective implementation/documentation worker may perform the
        # change, but the gate that found the defect must verify it.  Holding
        # the source gate rather than the writer preserves the canonical
        # implementation -> QA or documentation -> review route and prevents
        # a generic writer result from being treated as proof of a fix.
        if (
            not isinstance(rework, dict)
            or rework.get("status") != "rework_required"
            or str(rework.get("verifier_gate") or "") != gate
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
                "severity": normalize_finding_severity(
                    value.get("severity"), default="P1",
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
            findings.append({**finding, "_origin_result_ref": result_ref})
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
    evaluation = attempt.get("acceptance_evaluation")
    if (
        not isinstance(evaluation, Mapping)
        or evaluation.get("acceptance_status") != "passed"
        or list(evaluation.get("blocking_finding_fingerprints") or [])
        or list(evaluation.get("missing_verification_kinds") or [])
    ):
        return
    current_revision = int(state.get("task_revision") or 1)
    open_findings = {
        str(item.get("fingerprint") or ""): item
        for item in db_list_task_findings(root, state["task_id"], include_resolved=False)
        if isinstance(item, dict)
    }
    for _route_key, rework in (state.get("closure_rework") or {}).items():
        origin_gate = str(rework.get("origin_gate") or "") if isinstance(rework, dict) else ""
        if (
            not isinstance(rework, dict)
            or rework.get("status") != "rework_required"
            or str(rework.get("target_gate") or "") != target_gate
            or int(rework.get("task_revision") or 0) != current_revision
            or not _closure_rework_occurrence_matches(rework, attempt, "corrective")
            or str(attempt.get("attempt_result_ref") or "") != str(result_ref)
        ):
            continue
        rework["corrective_result_ref"] = str(result_ref)
        rework["corrective_resolution_receipt"] = {
            "wave_ref": str(attempt.get("wave_ref") or ""),
            "phase_ref": str(attempt.get("phase_ref") or ""),
            "logical_delegation_key": str(attempt.get("logical_delegation_key") or ""),
            "plan_assignment_lineage_digest": str(
                attempt.get("plan_assignment_lineage_digest") or ""
            ),
            "assignment_ref": str(attempt.get("dispatch_ref") or ""),
            "attempt_result_ref": str(result_ref),
        }
        for fingerprint in rework.get("finding_fingerprints") or []:
            finding = open_findings.get(str(fingerprint))
            if not isinstance(finding, dict):
                continue
            raw_finding_origins = rework.get("finding_origin_result_refs")
            origin_result_refs = (
                raw_finding_origins.get(str(fingerprint))
                if isinstance(raw_finding_origins, dict)
                else rework.get("source_result_refs")
            ) or []
            for origin_result_ref in origin_result_refs:
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


def _resolve_origin_verified_findings(
    root: Path,
    state: dict[str, Any],
    attempt: dict[str, Any],
    result_ref: str,
) -> list[str]:
    """Resolve inherited blockers only after their exact verifier lineage."""
    gate = str(attempt.get("gate") or "")
    evaluation = attempt.get("acceptance_evaluation")
    if (
        not isinstance(evaluation, Mapping)
        or evaluation.get("acceptance_status") != "passed"
        or list(evaluation.get("blocking_finding_fingerprints") or [])
        or list(evaluation.get("missing_verification_kinds") or [])
    ):
        return []
    current_revision = int(state.get("task_revision") or 1)
    resolved: list[str] = []
    for _route_key, rework in (state.get("closure_rework") or {}).items():
        if not isinstance(rework, dict):
            continue
        origin_gate = str(rework.get("origin_gate") or "")
        verifier_gate = str(rework.get("verifier_gate") or "")
        if (
            verifier_gate != gate
            or rework.get("status") != "rework_required"
            or int(rework.get("task_revision") or 0) != current_revision
            or not _closure_rework_occurrence_matches(rework, attempt, "verifier")
            or str(attempt.get("attempt_result_ref") or "") != str(result_ref)
            or not str(rework.get("corrective_result_ref") or "")
        ):
            continue
        raw_finding_origins = rework.get("finding_origin_result_refs")
        route_resolved: list[str] = []
        for fingerprint in rework.get("finding_fingerprints") or []:
            origins = [
                str(item) for item in (
                    raw_finding_origins.get(str(fingerprint))
                    if isinstance(raw_finding_origins, dict)
                    else rework.get("source_result_refs")
                ) or []
                if str(item)
            ]
            resolution = ledger_db.resolve_task_finding(
                root,
                str(state["task_id"]),
                str(fingerprint),
                origin_result_refs=origins,
                resolving_attempt_result_ref=result_ref,
                origin_gate=str(origin_gate),
                resolving_gate=gate,
                task_revision=current_revision,
            )
            if resolution is not None:
                route_resolved.append(str(fingerprint))
        if route_resolved:
            resolved.extend(route_resolved)
            rework["verifier_result_ref"] = str(result_ref)
            rework["verification_resolution_receipt"] = {
                "origin_gate": str(origin_gate),
                "verifier_gate": verifier_gate,
                "wave_ref": str(attempt.get("wave_ref") or ""),
                "phase_ref": str(attempt.get("phase_ref") or ""),
                "logical_delegation_key": str(attempt.get("logical_delegation_key") or ""),
                "plan_assignment_lineage_digest": str(
                    attempt.get("plan_assignment_lineage_digest") or ""
                ),
                "assignment_ref": str(attempt.get("dispatch_ref") or ""),
                "attempt_result_ref": str(result_ref),
                "task_revision": current_revision,
            }
            finding_status = {
                str(item.get("fingerprint") or ""): str(item.get("status") or "")
                for item in db_list_task_findings(
                    root, str(state["task_id"]), include_resolved=True,
                )
                if isinstance(item, Mapping)
            }
            if all(
                finding_status.get(str(fingerprint)) == "resolved"
                for fingerprint in rework.get("finding_fingerprints") or []
            ):
                rework["status"] = "resolved"
                rework["resolved_at"] = rework.get("resolved_at") or now()
                rework["resolved_by_gate"] = gate
    return resolved


def _closure_rework_occurrence_matches(
    rework: Mapping[str, Any], attempt: Mapping[str, Any], role: str,
) -> bool:
    """Match one result to the exact server-compiled rework occurrence."""
    if role not in {"corrective", "verifier", "close"}:
        raise ValueError("closure rework occurrence role is invalid")
    expected = {
        "wave_ref": str(rework.get(f"{role}_wave_ref") or ""),
        "phase_ref": str(rework.get(f"{role}_phase_ref") or ""),
        "logical_delegation_key": str(
            rework.get(f"{role}_logical_delegation_key") or ""
        ),
        "plan_assignment_lineage_digest": str(
            rework.get(f"{role}_plan_assignment_lineage_digest") or ""
        ),
        "dispatch_ref": str(rework.get(f"{role}_assignment_ref") or ""),
    }
    return bool(
        all(expected.values())
        and all(str(attempt.get(field) or "") == value for field, value in expected.items())
    )


def _bind_closure_rework_assignments(
    state: dict[str, Any], attempts: Sequence[Mapping[str, Any]],
) -> None:
    """Bind generated native assignment capabilities to exact rework slots."""
    routes = [
        rework for rework in (state.get("closure_rework") or {}).values()
        if isinstance(rework, dict) and rework.get("status") == "rework_required"
    ] + [
        rework for rework in (state.get("product_rework_routes") or {}).values()
        if isinstance(rework, dict)
        and rework.get("status") in {"active", "awaiting_close"}
    ]
    for rework in routes:
        if not isinstance(rework, dict):
            continue
        for role in ("corrective", "verifier", "close"):
            identity_fields = (
                "wave_ref", "phase_ref", "logical_delegation_key",
                "plan_assignment_lineage_digest",
            )
            expected = {
                field: str(rework.get(f"{role}_{field}") or "")
                for field in identity_fields
            }
            if not all(expected.values()):
                continue
            matches = [
                attempt for attempt in attempts
                if all(str(attempt.get(field) or "") == value for field, value in expected.items())
            ]
            if not matches:
                continue
            if len(matches) != 1:
                raise ValueError("closure rework assignment occurrence is ambiguous")
            assignment_ref = str(matches[0].get("dispatch_ref") or "")
            if not assignment_ref:
                raise ValueError("closure rework assignment capability is unavailable")
            field = f"{role}_assignment_ref"
            existing = str(rework.get(field) or "")
            if existing and existing != assignment_ref:
                raise ValueError("closure rework assignment capability changed")
            rework[field] = assignment_ref


def _rebind_active_rework_route_assignment(
    state: dict[str, Any],
    source: Mapping[str, Any],
    replacement: Mapping[str, Any],
    *,
    recovery_occurrence_key: str,
) -> list[dict[str, str]]:
    """Roll exact incomplete route roles forward to one replacement lineage.

    Route identity is durable capability state. A technical replacement keeps
    the immutable wave/phase/logical slot but issues a new assignment lineage
    and dispatch capability. Every active route collection must move that
    exact role atomically; completed roles and stale/mismatched capabilities
    fail closed instead of being silently detached from their result.
    """
    identity_fields = (
        "wave_ref", "phase_ref", "logical_delegation_key",
        "plan_assignment_lineage_digest",
    )
    source_identity = {
        field: str(source.get(field) or "") for field in identity_fields
    }
    replacement_identity = {
        field: str(replacement.get(field) or "") for field in identity_fields
    }
    source_assignment_ref = str(source.get("dispatch_ref") or "")
    if (
        not all(source_identity.values())
        or not all(replacement_identity.values())
        or not source_assignment_ref
        or any(
            replacement_identity[field] != source_identity[field]
            for field in ("wave_ref", "phase_ref", "logical_delegation_key")
        )
        or replacement_identity["plan_assignment_lineage_digest"]
        == source_identity["plan_assignment_lineage_digest"]
    ):
        raise ValueError("technical route rebinding requires one exact replacement lineage")

    completion_receipt_fields = {
        "corrective": "corrective_resolution_receipt",
        "verifier": "verification_resolution_receipt",
        "close": "close_resolution_receipt",
    }
    route_sets = (
        ("closure_rework", {"rework_required"}),
        ("product_rework_routes", {"active", "awaiting_close"}),
    )
    pending: list[
        tuple[str, str, dict[str, Any], str, list[dict[str, Any]], dict[str, Any]]
    ] = []
    rebound_at = now()
    for collection_name, active_statuses in route_sets:
        raw_routes = state.get(collection_name)
        routes = {} if raw_routes is None else raw_routes
        if not isinstance(routes, Mapping):
            raise ValueError(f"{collection_name} route registry is invalid")
        for route_key, raw_route in routes.items():
            if not isinstance(raw_route, dict):
                raise ValueError(f"{collection_name} contains an invalid route")
            matching_roles: list[str] = []
            for role in ("corrective", "verifier", "close"):
                role_identity = {
                    field: str(raw_route.get(f"{role}_{field}") or "")
                    for field in identity_fields
                }
                if role_identity == source_identity:
                    matching_roles.append(role)
            if not matching_roles:
                continue
            if len(matching_roles) != 1:
                raise ValueError("technical replacement matches multiple roles in one rework route")
            role = matching_roles[0]
            if str(raw_route.get("status") or "") not in active_statuses:
                raise ValueError("completed or inactive rework route cannot be technically rebound")
            bound_assignment_ref = str(raw_route.get(f"{role}_assignment_ref") or "")
            if bound_assignment_ref != source_assignment_ref:
                raise ValueError("active rework route assignment capability disagrees with replacement source")
            if (
                isinstance(raw_route.get(completion_receipt_fields[role]), Mapping)
                or isinstance(raw_route.get(f"{role}_evidence"), Mapping)
            ):
                raise ValueError("completed rework route role cannot be technically rebound")

            other_roles = {
                other: {
                    key: json.loads(json.dumps(value, ensure_ascii=False))
                    for key, value in raw_route.items()
                    if key.startswith(f"{other}_")
                }
                for other in ("corrective", "verifier", "close")
                if other != role
            }
            prior_binding = {
                **source_identity,
                "assignment_ref": bound_assignment_ref,
                "attempt_result_ref": str(raw_route.get(f"{role}_result_ref") or ""),
                "superseded_by_plan_assignment_lineage_digest": replacement_identity[
                    "plan_assignment_lineage_digest"
                ],
                "recovery_occurrence_key": recovery_occurrence_key,
                "superseded_at": rebound_at,
            }
            history_field = f"{role}_binding_history"
            history = raw_route.get(history_field) or []
            if not isinstance(history, list) or any(
                not isinstance(item, Mapping) for item in history
            ):
                raise ValueError("rework route role binding history is invalid")
            pending.append((
                collection_name,
                str(route_key),
                raw_route,
                role,
                [dict(item) for item in history],
                {"prior_binding": prior_binding, "other_roles": other_roles},
            ))

    # Apply only after every exact route and role has passed validation. This
    # keeps a completed/mismatched route from leaving an earlier valid route
    # partially rebound when the operation fails closed.
    rebound: list[dict[str, str]] = []
    for collection_name, route_key, raw_route, role, history, snapshots in pending:
        prior_binding = snapshots["prior_binding"]
        other_roles = snapshots["other_roles"]
        history_field = f"{role}_binding_history"
        raw_route[history_field] = [
            *history, prior_binding,
        ]
        for field, value in replacement_identity.items():
            raw_route[f"{role}_{field}"] = value
        raw_route.pop(f"{role}_assignment_ref", None)
        raw_route.pop(f"{role}_result_ref", None)
        raw_route.pop(f"{role}_evidence", None)
        raw_route["updated_at"] = rebound_at
        for other, snapshot in other_roles.items():
            current = {
                key: value for key, value in raw_route.items()
                if key.startswith(f"{other}_")
            }
            if current != snapshot:
                raise ValueError("technical route rebinding changed an unrelated role")
        rebound.append({
            "collection": collection_name,
            "route_key": route_key,
            "role": role,
        })
    return rebound


def _record_product_rework_route_result(
    task_dir: Path,
    state: dict[str, Any],
    attempt: Mapping[str, Any],
    result_ref: str,
) -> None:
    """Advance only the exact passed role in an active product-rework route."""
    evaluation = attempt.get("acceptance_evaluation")
    if (
        not isinstance(evaluation, Mapping)
        or str(evaluation.get("acceptance_status") or "") != "passed"
        or list(evaluation.get("blocking_finding_fingerprints") or [])
        or list(evaluation.get("missing_verification_kinds") or [])
        or str(attempt.get("attempt_result_ref") or "") != str(result_ref)
    ):
        return
    for route in (state.get("product_rework_routes") or {}).values():
        if (
            not isinstance(route, dict)
            or route.get("status") not in {"active", "awaiting_close"}
        ):
            continue
        roles = [
            role for role in ("corrective", "verifier", "close")
            if _closure_rework_occurrence_matches(route, attempt, role)
        ]
        if len(roles) > 1:
            raise ValueError("product rework result matches multiple route roles")
        if not roles:
            continue
        role = roles[0]
        canonical = attempt_protocol.get_attempt_result(
            _ledger_root_for_artifact(task_dir),
            task_id=str(state.get("task_id") or ""),
            attempt_id=str(attempt.get("attempt_id") or ""),
        )
        if (
            not isinstance(canonical, Mapping)
            or str(canonical.get("result_ref") or "") != str(result_ref)
        ):
            raise ValueError("product rework role result is not canonical")
        route[f"{role}_result_ref"] = str(result_ref)
        route[f"{role}_result_digest"] = (
            "sha256:" + digest_text(canonical_json.dumps(canonical))
        )
        route[f"{role}_evidence"] = {
            "wave_ref": str(attempt.get("wave_ref") or ""),
            "phase_ref": str(attempt.get("phase_ref") or ""),
            "logical_delegation_key": str(
                attempt.get("logical_delegation_key") or ""
            ),
            "plan_assignment_lineage_digest": str(
                attempt.get("plan_assignment_lineage_digest") or ""
            ),
            "assignment_ref": str(attempt.get("dispatch_ref") or ""),
            "attempt_result_ref": str(result_ref),
        }
        if role == "verifier":
            if not str(route.get("corrective_result_ref") or ""):
                raise ValueError("product rework verifier lacks a passed corrective result")
            route["status"] = (
                "awaiting_close" if str(route.get("close_wave_ref") or "") else "resolved"
            )
        elif role == "close":
            if (
                not str(route.get("corrective_result_ref") or "")
                or not str(route.get("verifier_result_ref") or "")
            ):
                raise ValueError("product rework close lacks corrective and verifier results")
            route["status"] = "resolved"
        if route.get("status") == "resolved":
            route["resolved_at"] = route.get("resolved_at") or now()
        route["updated_at"] = now()


def _product_rework_context_refs(
    task_dir: Path,
    state: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> list[str]:
    """Authorize only the exact origin/corrective/verifier route chain."""
    identity_fields = (
        "wave_ref", "phase_ref", "logical_delegation_key",
        "plan_assignment_lineage_digest",
    )
    spec_identity = {
        field: str(spec.get(field) or "") for field in identity_fields
    }
    matches: list[tuple[Mapping[str, Any], str]] = []
    for route in (state.get("product_rework_routes") or {}).values():
        if not isinstance(route, Mapping):
            raise ValueError("product rework route is invalid")
        if str(route.get("status") or "") not in {"active", "awaiting_close"}:
            continue
        for role in ("corrective", "verifier", "close"):
            role_identity = {
                field: str(route.get(f"{role}_{field}") or "")
                for field in identity_fields
            }
            if all(role_identity.values()) and role_identity == spec_identity:
                matches.append((route, role))
    if not matches:
        return []
    if len(matches) != 1:
        raise ValueError("compiled assignment matches multiple product rework routes")
    route, role = matches[0]
    required_roles = {
        "corrective": (),
        "verifier": ("corrective",),
        "close": ("corrective", "verifier"),
    }[role]
    source_ref = str(route.get("source_result_ref") or "")
    refs = [source_ref]
    digest_by_ref = {
        source_ref: str(route.get("source_result_digest") or ""),
    }
    for prior_role in required_roles:
        result_ref = str(route.get(f"{prior_role}_result_ref") or "")
        refs.append(result_ref)
        digest_by_ref[result_ref] = str(
            route.get(f"{prior_role}_result_digest") or ""
        )
    if any(not ref for ref in refs) or len(refs) != len(set(refs)):
        raise ValueError("product rework route result chain is incomplete or ambiguous")
    root = _ledger_root_for_artifact(task_dir)
    for result_ref in refs:
        exact_attempts = [
            item for item in state.get("attempts") or []
            if isinstance(item, Mapping)
            and str(item.get("attempt_result_ref") or "") == result_ref
        ]
        if len(exact_attempts) != 1:
            raise ValueError("product rework route result is not task-authorized")
        canonical = attempt_protocol.get_attempt_result(
            root,
            task_id=str(state.get("task_id") or ""),
            attempt_id=str(exact_attempts[0].get("attempt_id") or ""),
        )
        if not isinstance(canonical, Mapping):
            raise ValueError("product rework route result is unavailable")
        expected_digest = "sha256:" + digest_text(canonical_json.dumps(canonical))
        if (
            str(canonical.get("result_ref") or "") != result_ref
            or digest_by_ref.get(result_ref) != expected_digest
        ):
            raise ValueError("product rework route result digest changed")
    return refs


def _prepare_orchestrate_wave(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Prepare one exact active assignment frontier atomically."""
    root = _ledger_root_for_artifact(task_dir)
    with ledger_db.transaction(root):
        return _prepare_orchestrate_wave_transaction(params, task_dir, state, plan)


def _current_wave_attempt_frontier(
    effective_delegations: list[dict[str, Any]],
    prepared_attempts: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[str]:
    """Return the exact ordered current attempt for every delegation slot."""
    expected_slot_keys = [
        str(spec.get("orchestration_delegation_key") or "").strip()
        for spec in effective_delegations
    ]
    current_slot_keys = [
        str(attempt.get("orchestration_delegation_key") or "").strip()
        for attempt, _request in prepared_attempts
    ]
    current_attempt_ids = [
        str(attempt.get("attempt_id") or "").strip()
        for attempt, _request in prepared_attempts
    ]
    if (
        not current_attempt_ids
        or len(current_attempt_ids) != len(effective_delegations)
        or any(not item for item in expected_slot_keys + current_slot_keys + current_attempt_ids)
        or len(set(expected_slot_keys)) != len(expected_slot_keys)
        or len(set(current_slot_keys)) != len(current_slot_keys)
        or len(set(current_attempt_ids)) != len(current_attempt_ids)
        or current_slot_keys != expected_slot_keys
    ):
        raise ValueError("active wave replacement assignments do not match exact delegation slots")
    return current_attempt_ids


def _prepare_orchestrate_wave_transaction(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    if _active_terminal_recovery_breakers(state, plan):
        raise ValueError("active technical recovery breakers forbid dispatch before the full wave is transformed")
    state, plan = _repair_delivery_graph_before_closure(params, task_dir, state, plan)
    wave, _current_assignments = _effective_plan_frontier(plan, state)
    if wave is None:
        return {"wave_id": None, "spawn_requests": [], "attempt_ids": [], "state": state}
    current_gates = [str(gate) for gate in wave.get("gates") or [] if str(gate).strip()]
    if not current_gates:
        raise ValueError("effective current wave has no semantic phase projection")
    _assert_origin_verifier_rework_preflight(
        _ledger_root_for_artifact(task_dir), state, current_gates,
    )
    _assert_approved_plan_fresh(task_dir, state, plan)
    executable_gates = list(current_gates)
    task_definition = load_task_definition(task_dir, state)
    retired_failures = False
    for attempt in state.get("attempts", []):
        if (
            str(attempt.get("wave_ref") or "") == str(wave.get("wave_ref") or "")
            and attempt.get("status") in {"blocked", "failed", "cancelled", "superseded"}
            and not attempt.get("invalidated")
        ):
            attempt["invalidated"] = True
            attempt["invalidated_at"] = now()
            attempt["invalidation_reason"] = "retry_after_terminal_non_success"
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
    satisfied_slots = {
        (
            str(item.get("logical_delegation_key") or ""),
            str(item.get("plan_assignment_lineage_digest") or ""),
        )
        for item in state.get("attempts") or []
        if isinstance(item, dict)
        and not item.get("invalidated")
        and str(item.get("wave_ref") or "") == str(wave.get("wave_ref") or "")
        and item.get("acceptance_status") == "passed"
        and item.get("continuation_consumed_at")
    }
    pending_product_rework_slots = {
        (
            str(item.get("logical_delegation_key") or ""),
            str(item.get("plan_assignment_lineage_digest") or ""),
        )
        for item in state.get("pending_product_reworks") or []
        if isinstance(item, Mapping)
        and str(item.get("logical_delegation_key") or "")
        and str(item.get("plan_assignment_lineage_digest") or "")
    }
    effective_delegations = [
        spec for spec in wave["delegations"]
        if str(spec.get("gate") or "") in executable_gates
        and (
            str(spec.get("logical_delegation_key") or ""),
            str(spec.get("plan_assignment_lineage_digest") or ""),
        ) not in satisfied_slots
        and (
            str(spec.get("logical_delegation_key") or ""),
            str(spec.get("plan_assignment_lineage_digest") or ""),
        ) not in pending_product_rework_slots
    ]
    rework_wave_refs = {
        str(item.get("wave_ref") or "")
        for item in plan.get("rework_appends") or []
        if isinstance(item, dict) and str(item.get("wave_ref") or "")
    }
    if (
        executable_gates == ["implementation"]
        and str(wave.get("wave_ref") or "") not in rework_wave_refs
    ):
        compiled = _compiled_implementation_spec(task_dir, state, wave)
        if compiled is not None:
            compiled_slot = (
                str(compiled.get("logical_delegation_key") or ""),
                str(compiled.get("plan_assignment_lineage_digest") or ""),
            )
            effective_delegations = [] if compiled_slot in satisfied_slots else [compiled]
    if not effective_delegations:
        raise ValueError("effective current wave has no unresolved assignment slots")
    for spec in effective_delegations:
        if not str(spec.get("logical_delegation_key") or "").strip():
            raise ValueError("effective delegation lacks its normalized logical delegation key")
        if not str(spec.get("plan_assignment_lineage_digest") or "").strip():
            raise ValueError("effective delegation lacks its normalized plan authority lineage")
        spec["assignment_lineage_digest"] = _assignment_lineage_digest(spec)

    def existing_for(spec: dict[str, Any]) -> dict[str, Any] | None:
        slot = (
            str(spec.get("logical_delegation_key") or ""),
            str(spec.get("plan_assignment_lineage_digest") or ""),
        )
        return next(
            (
                item for item in state.get("attempts", [])
                if not item.get("invalidated")
                and item.get("status") in {
                    AWAITING_HOST_SPAWN, "running", RESULT_READY, "passed", "waiting_question",
                }
                and str(item.get("wave_ref") or "") == str(wave.get("wave_ref") or "")
                and str(item.get("phase_ref") or "") == str(wave.get("phase_ref") or "")
                and (
                    str(item.get("logical_delegation_key") or ""),
                    str(item.get("plan_assignment_lineage_digest") or ""),
                ) == slot
            ),
            None,
        )

    # Reserve the exact current wave frontier before creating immutable
    # dispatch artifacts. Attempt ids are server-deterministic from the
    # current state cardinality, and the encompassing ledger transaction
    # makes this reservation, every delegation row, and the final state one
    # atomic operation. Governance-close provenance therefore sees the final
    # active plan receipt rather than the prior pending-wave snapshot.
    planned_attempt_ids: list[str] = []
    next_attempt_number = len(state.get("attempts") or [])
    for spec in effective_delegations:
        existing = existing_for(spec)
        if existing is not None:
            planned_attempt_ids.append(str(existing.get("attempt_id") or ""))
            continue
        next_attempt_number += 1
        planned_attempt_ids.append(f"{spec['gate']}-{next_attempt_number:02d}")
    if (
        len(planned_attempt_ids) != len(effective_delegations)
        or any(not attempt_id for attempt_id in planned_attempt_ids)
        or len(set(planned_attempt_ids)) != len(planned_attempt_ids)
    ):
        raise ValueError("active wave frontier reservation is invalid")
    wave["status"] = "active"
    _write_orchestrate_plan(task_dir, plan)
    save_state(
        task_dir,
        task_dir / "state.sqlite",
        state,
        "orchestrate_wave_frontier_reserved",
        wave["wave_id"],
    )

    for slot_index, spec in enumerate(effective_delegations):
        key = spec["orchestration_delegation_key"]
        existing = existing_for(spec)
        if existing is not None:
            if str(existing.get("attempt_id") or "") != planned_attempt_ids[slot_index]:
                raise ValueError("existing delegation does not match its reserved wave assignment")
            if not existing.get("orchestration_wave_id"):
                existing["orchestration_wave_id"] = wave["wave_id"]
                existing["orchestration_delegation_key"] = key
            if existing.get("status") == RESULT_READY:
                # The canonical result is already immutable and awaits only
                # its exact read/continuation boundary.  Never reconstruct a
                # native dispatch for this non-runnable occurrence.
                prepared_attempts.append((existing, {}))
                continue
            prepared_attempts.append((existing, _rehydrate_dispatch_spawn_request(
                task_dir, task_definition, existing,
            )))
            continue
        observed = status({**params, "task_id": state["task_id"]})
        predecessor_result_refs = _predecessor_result_refs_for_assignment(state, spec)
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
            and str(rework.get("corrective_wave_ref") or "") == str(spec.get("wave_ref") or "")
            and str(rework.get("corrective_phase_ref") or "") == str(spec.get("phase_ref") or "")
            and str(rework.get("corrective_logical_delegation_key") or "")
            == str(spec.get("logical_delegation_key") or "")
            and str(rework.get("corrective_plan_assignment_lineage_digest") or "")
            == str(spec.get("plan_assignment_lineage_digest") or "")
            for result_ref in rework.get("source_result_refs") or []
            if str(result_ref).strip()
        ]
        product_rework_context_refs = _product_rework_context_refs(
            task_dir, state, spec,
        )
        predecessor_result_refs = list(dict.fromkeys(
            predecessor_result_refs
            + corrective_context_refs
            + product_rework_context_refs
        ))
        report_catalog_result_refs = _report_catalog_result_refs_for_assignment(
            state, spec, predecessor_result_refs,
        )
        optional_report_result_refs = [
            result_ref for result_ref in report_catalog_result_refs
            if result_ref not in set(predecessor_result_refs)
        ]
        report_catalog_digest = "sha256:" + digest_text(canonical_json.dumps({
            "required": predecessor_result_refs,
            "optional": optional_report_result_refs,
        }))
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
            "predecessor_result_refs": predecessor_result_refs,
            "optional_report_result_refs": optional_report_result_refs,
            "report_catalog_digest": report_catalog_digest,
            "task_id": state["task_id"],
            "expected_revision": observed["state"]["revision"],
            "status_receipt": observed["status_receipt"],
            "plan_revision": state.get("plan_revision"),
            "plan_digest": state.get("plan_digest"),
        })
        if delegated.get("recorded") is False:
            raise ValueError(str(delegated.get("reason") or "wave delegation was not recorded"))
        if str(delegated.get("attempt_id") or "") != planned_attempt_ids[slot_index]:
            raise ValueError("recorded delegation does not match its reserved wave assignment")
        state = delegated["state"]
        prepared_attempts.append((
            _attempt(state, delegated["attempt_id"]),
            dict(delegated["spawn_request"]),
        ))
    current_attempt_ids = _current_wave_attempt_frontier(
        effective_delegations, prepared_attempts,
    )
    # ``attempt_ids`` is the current executable frontier, not lineage. A
    # retired assignment remains in state.attempts as immutable history but
    # must be replaced in its exact ordered slot so strict result reads cannot
    # mistake it for a current assignment. Exact replay finds the same active
    # attempts above and therefore writes this identical frontier.
    if current_attempt_ids != planned_attempt_ids:
        raise ValueError("recorded wave frontier differs from its atomic reservation")
    _bind_closure_rework_assignments(
        state, [attempt for attempt, _request in prepared_attempts],
    )
    save_state(task_dir, task_dir / "state.sqlite", state, "orchestrate_wave", wave["wave_id"])
    spawn_requests = [
        {**request, "attempt_id": attempt["attempt_id"]}
        for attempt, request in prepared_attempts
        if attempt.get("status") == AWAITING_HOST_SPAWN
    ]
    return {
        "wave_id": wave["wave_id"],
        "spawn_requests": spawn_requests,
        "attempt_ids": list(planned_attempt_ids),
        "state": state,
    }


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
    terminal_closure = _terminal_governance_closure_breaker(state)
    # A blocked ledger row is an internal recovery checkpoint, not a state in
    # which the public Cortex pipeline may stop.  The advance/resume paths
    # normally dispatch the corrective route before reaching this function;
    # this normalization is the final safety net for stale projections and
    # direct inspection/replay responses.
    facade_state = (
        "terminal_blocked" if terminal_closure is not None else
        "recovery_pending" if ledger_state == "blocked" else
        ledger_state
    )
    if facade_state == "ready_to_spawn":
        next_action = (
            "invoke every returned native spawn request, wait for the wave, then call "
            "read_worker_wave and follow its server-derived lifecycle action"
        )
    elif facade_state == "waiting_workers":
        next_action = (
            "wait for every worker in the current wave, then call read_worker_wave and "
            "follow its server-derived lifecycle action"
        )
    elif facade_state == "completion_pending":
        next_action = (
            "call read_worker_wave now so Cortex derives the exact stopped-worker canonical results; "
            "follow the returned lifecycle action and call continue_orchestration only when directed. "
            "Do not wait, respawn, resume the stopped worker, or construct result references"
        )
    elif facade_state == "completed":
        next_action = "present the verified task result to the user"
    elif facade_state == "terminal_blocked":
        next_action = (
            "stop this task lifecycle; the terminal server receipt authorizes no retry, resume, "
            "replacement, prior-wave reopen, user question, wait, or additional dispatch"
        )
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
            "Read the bounded plan card with manage_orchestration action='plan_prompt' and follow every returned "
            "cursor page. Use action='plan' with the exact request_id and decision='approve_with_recommendations', "
            "decision='approve_without_recommendations', or decision='cancel'. Use action='plan_revise' with the exact "
            "request_id and the user's requested changes in text. Leave the plan pending when no decision is available."
        )
    else:
        next_action = "inspect the returned diagnostics or provide the required completion data"
    terminal_failure = facade_state == "terminal_blocked"
    response = {
        "schema": LIFECYCLE_RUNTIME_SCHEMA,
        "ok": not terminal_failure,
        "lifecycle": operation,
        "transaction_id": None,
        "task_id": state.get("task_id"),
        "wave_id": wave_id,
        "state": facade_state,
        "state_summary": _orchestrate_summary(state),
        "spawn_requests": spawn_requests or [],
        "diagnostics": diagnostics or [],
        "next_action": next_action,
    }
    if terminal_failure:
        governance_terminal = terminal_closure is not None
        terminal_code = (
            "governance_closure_terminal_blocked"
            if governance_terminal else
            str(state.get("blocked_reason") or "technical_reliability_terminal_blocked")
        )
        response.update({
            "code": terminal_code,
            "message": (
                "The accepted governance closure is blocked and terminal for this task lifecycle."
                if governance_terminal else
                "Cortex could not derive an executable next route from the durable assignment frontier and stopped the task."
                if terminal_code == "technical_recovery_route_unavailable" else
                "The bounded Luna-to-Terra-to-Sol technical recovery ladder is exhausted for the exact assignment occurrence."
            ),
            "recoverable": False,
            "retryable": False,
            "retry": {"kind": "terminal_stop", "operation": operation},
        })
        response["spawn_requests"] = []
    if ledger_state == "blocked":
        response.setdefault("internal", {})
        response["internal"]["ledger_state"] = ledger_state
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
    return "approval-" + digest_text(canonical_json.dumps(seed))[:32]


def _orchestrate_start(params: dict[str, Any], transaction_path: Path, transaction: dict[str, Any]) -> dict[str, Any]:
    materialization_fence = params.get("_materialization_fence")

    def assert_materialization_owner() -> None:
        if callable(materialization_fence) and not materialization_fence():
            raise RuntimeError("start materialization ownership was lost before durable commit")

    task = params.get("task")
    if not isinstance(task, dict):
        raise ValueError("start requires a task object")
    task_id = safe_id(str(task.get("task_id", "")))
    user_request = str(task.get("user_request", "")).strip()
    if not user_request:
        raise ValueError("start task.user_request is required")
    principal = str(params.get("principal", "")).strip()
    if not principal:
        raise ValueError("start requires the server-owned principal")
    host_capabilities = params.get("host_capabilities")
    if not isinstance(host_capabilities, dict):
        raise ValueError("start requires host_capabilities")
    waves, classification_preview = _normalize_orchestrate_waves(params.get("waves"), task, host_capabilities, str(params["project_root"]))
    # Reject a task that cannot build the immutable worker context before
    # activation/classification/task initialization leave durable records.
    _preflight_dispatch_context(task, {})
    root = ledger_root(params)
    with state_lock(root):
        assert_materialization_owner()
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
            assert_materialization_owner()
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
            # The flat public start adapter records the caller's
            # ``plan_approval=required`` selection on the canonical task.
            # Preserve that exact choice alongside the older private intent
            # markers so the required post-plan gate remains executable.
            trusted_start_plan_review = bool(
                task.get("plan_approval") == "required"
                or any(
                    params.get(marker) is True or task.get(marker) is True
                    for marker in (
                        "plan_approval_user_requested",
                        "user_requested_plan_approval",
                        "plan_review_requested",
                        "explicit_plan_approval_requested",
                    )
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
            "pipeline_contract_version": _pipeline_contract_version(state),
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
        # Keep the coordinator's route as the executable choice. Cortex's
        # classifier remains useful evidence, but it cannot silently replace
        # or append policy gates to that choice.
        chosen_pipeline = [gate for wave in waves for gate in wave.get("gates", [])]
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
        if existing_plan is None:
            _write_orchestrate_plan(task_dir, plan)
        # The public planner completion is intentionally text-only.  Persist
        # the structure needed by approval and scheduling from the exact
        # coordinator-authored start waves before the first worker dispatch.
        _materialize_coordinator_plan(
            task_dir,
            state,
            load_task_definition(task_dir, state),
            plan,
        )
        state_pipeline = list(dict.fromkeys(chosen_pipeline))
        state_groups: list[list[str]] = []
        grouped: set[str] = set()
        for group in chosen_groups:
            unique_group = [gate for gate in group if gate not in grouped]
            if unique_group:
                state_groups.append(unique_group)
                grouped.update(unique_group)
        state = _record_chosen_pipeline(
            task_dir,
            state,
            state_pipeline,
            state_groups,
            recommended_pipeline=recommended_pipeline,
            recommended_parallel_groups=recommended_groups,
            reason="Recorded orchestrator-selected pipeline; Cortex recommendation is advisory.",
        )
        _sync_orchestration_wave_occurrences(state, plan)
        save_state(
            task_dir, task_dir / "state.sqlite", state,
            "orchestration_wave_occurrences",
            "bound coordinator-selected semantic phases to durable wave identities",
        )
        # Preserve the flat public plan-approval selection as exact user
        # choice; private start callers may still use the explicit markers.
        explicit_plan_review = bool(
            task.get("plan_approval") == "required"
            or any(
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
        assert_materialization_owner()
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
    """Return the current pipeline contract; never infer or migrate older state."""
    raw = state.get("pipeline_contract_version")
    try:
        version = int(raw)
    except (TypeError, ValueError):
        version = None
    if version != PIPELINE_CONTRACT_VERSION:
        raise ValueError(
            f"unsupported pipeline_contract_version {raw!r}; expected current canonical contract "
            f"{PIPELINE_CONTRACT_VERSION}"
        )
    return version


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
            workers.append({
                "profile": spec.get("agent"),
                "operation_kind": str(spec.get("operation_kind") or ""),
                "objective": str(spec.get("objective") or ""),
                "strategy": str(spec.get("strategy") or "default"),
                "model": str(spec.get("model") or ""),
                "reasoning_effort": str(spec.get("reasoning_effort") or ""),
                "dependencies": list(spec.get("predecessor_wave_refs") or []),
                "context_files": list(spec.get("context_files") or []),
                "acceptance_criteria": list(spec.get("acceptance_criteria") or []),
                "verification": list(spec.get("verification") or []),
            })
        if workers:
            phases = {
                str(spec.get("gate") or "")
                for spec in wave.get("delegations", [])
                if isinstance(spec, dict)
            }
            if len(phases) != 1:
                raise ValueError("canonical wave persistence requires exactly one inherited phase")
            semantic.append({"phase_kind": next(iter(phases)), "workers": workers})
    return semantic


def _semantic_future_pipeline_digest(plan: dict[str, Any]) -> str:
    return digest_text(canonical_json.dumps(_semantic_future_pipeline(plan)))


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
                "predecessor_wave_refs": list(worker.get("predecessor_wave_refs") or []),
                "context_files": list(worker.get("context_files") or []),
            })
            strategy.append({
                "gate": gate,
                "agent": agent,
                "objective": str(worker.get("objective") or ""),
                "strategy": str(worker.get("strategy") or ""),
                "model": str(worker.get("model") or ""),
                "reasoning_effort": str(worker.get("reasoning_effort") or ""),
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


_DELIVERY_RECOVERY_ORDER = (
    "implementation", "qa", "security", "performance", "review", "documentation", "close",
)
_IMPLEMENTATION_PROFILES = {
    "backend_dev", "data_engineer", "debugger", "devops_engineer", "frontend_dev",
    "fullstack_dev", "general", "mobile_dev", "refactorer",
}


def _historical_recovery_specs(plan: dict[str, Any], gate: str) -> list[dict[str, Any]]:
    """Reuse the most recent semantic worker contract for one restored gate."""
    semantic_versions = [
        entry.get("semantic_future_pipeline") or []
        for entry in reversed(plan.get("history", []))
        if isinstance(entry, dict)
    ]
    semantic_versions.append(_semantic_future_pipeline(plan))
    allowed_worker_fields = {
        "profile", "operation_kind", "objective", "strategy", "dependencies", "model",
        "reasoning_effort", "context_files", "acceptance_criteria", "verification",
    }
    compiled_wave_positions = {
        str(wave.get("wave_ref") or ""): index
        for index, wave in enumerate(plan.get("waves") or [])
        if isinstance(wave, dict) and str(wave.get("wave_ref") or "")
    }
    compiled_phase_occurrences = [
        (index, str(wave.get("phase_kind") or ""))
        for index, wave in enumerate(plan.get("waves") or [])
        if isinstance(wave, dict)
    ]
    validated_versions: list[list[dict[str, Any]]] = []
    for semantic in semantic_versions:
        if not isinstance(semantic, list):
            raise ValueError("historical semantic pipeline must be an array of phase waves")
        if any(not isinstance(wave, dict) for wave in semantic):
            raise ValueError("historical semantic pipeline waves must be objects")
        previous_occurrence = -1
        for wave in semantic:
            if set(wave) != {"phase_kind", "workers"}:
                raise ValueError("historical semantic waves require exactly phase_kind and workers")
            phase = wave.get("phase_kind")
            if not isinstance(phase, str) or phase not in AVAILABLE_GATES:
                raise ValueError("historical semantic wave phase must be canonical")
            workers = wave.get("workers")
            if not isinstance(workers, list) or not workers or any(not isinstance(worker, dict) for worker in workers):
                raise ValueError("historical semantic phase wave requires non-empty worker objects")
            dependency_positions = [
                compiled_wave_positions[item]
                for worker in workers
                for item in (worker.get("dependencies") or [])
                if isinstance(item, str) and item in compiled_wave_positions
            ]
            earliest_position = max([previous_occurrence, *dependency_positions], default=previous_occurrence)
            occurrence = next((
                index for index, compiled_phase in compiled_phase_occurrences
                if index > earliest_position and compiled_phase == phase
            ), None)
            if occurrence is None:
                raise ValueError("historical semantic wave has no matching compiled phase occurrence")
            previous_occurrence = occurrence
            for worker in workers:
                if "phase" in worker:
                    raise ValueError("historical semantic workers must inherit phase from their wave")
                unsupported = sorted(set(worker) - allowed_worker_fields)
                if unsupported:
                    raise ValueError(
                        "historical semantic worker contains unsupported fields: " + ", ".join(unsupported)
                    )
                missing = sorted(allowed_worker_fields - set(worker))
                if missing:
                    raise ValueError(
                        "historical semantic worker is missing canonical fields: " + ", ".join(missing)
                    )
                for field in ("profile", "objective", "strategy"):
                    if not isinstance(worker.get(field), str):
                        raise ValueError(f"historical semantic worker {field} must be a string")
                if worker.get("operation_kind") not in {"inspect", "modify", "verify", "close"}:
                    raise ValueError("historical semantic worker operation_kind must be canonical")
                for field in ("context_files", "acceptance_criteria", "verification"):
                    value = worker.get(field)
                    if (
                        not isinstance(value, list)
                        or any(not isinstance(item, str) for item in value)
                    ):
                        raise ValueError(f"historical semantic worker {field} must be a string array")
                dependencies = worker.get("dependencies")
                if not isinstance(dependencies, list) or any(
                    not isinstance(item, str)
                    or item not in compiled_wave_positions
                    or compiled_wave_positions[item] >= occurrence
                    for item in dependencies
                ):
                    raise ValueError(
                        "historical semantic worker dependencies must be exact prior compiled wave refs"
                    )
        validated_versions.append(semantic)

    for semantic in validated_versions:
        matching_waves = [wave for wave in semantic if wave["phase_kind"] == gate]
        if matching_waves:
            workers = matching_waves[-1]["workers"]
            return [
                {
                    "gate": gate,
                    "agent": str(worker.get("profile") or _default_profile_for_gate(gate)),
                    "operation_kind": str(worker.get("operation_kind") or ""),
                    **({"objective": worker["objective"]} if worker.get("objective") else {}),
                    **({"strategy": worker["strategy"]} if worker.get("strategy") else {}),
                    "model": worker["model"],
                    "reasoning_effort": worker["reasoning_effort"],
                    **({"predecessor_wave_refs": list(worker["dependencies"])} if isinstance(worker.get("dependencies"), list) else {}),
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
) -> list[dict[str, Any]]:
    """Return recovery waves for the coordinator-selected route.

    Recovery used to manufacture a ``recovery-plan`` wave and then replay all
    historically required delivery gates.  That was a policy-owned pipeline
    replacement: a stale projection or a failed worker could silently change
    what the coordinator had chosen.  Recovery is now deliberately narrow:
    preserve the durable chosen/current frontier, reuse the matching worker
    contracts already present in the plan, and fall back to the current
    attempt's contract only when the projection has lost its wave details.

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
    predecessor_gate_filter = {
        gate for gate in ("scope", "discover", "architecture", "database_architecture", "ux")
        if gate in pipeline and pipeline.index(gate) < plan_index
    }
    result_refs = _finalized_result_refs_for_gates(state, predecessor_gate_filter)
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
    if (
        not isinstance(manifest, dict)
        or manifest.get("source_authority") != "coordinator_start_waves"
        or manifest.get("source_plan_digest") != _coordinator_plan_digest(plan.get("waves"))
    ):
        raise ValueError("plan approval requires the current coordinator-authored planning revision")
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
    if (
        isinstance(manifest, dict)
        and manifest.get("source_authority") == "coordinator_start_waves"
        and manifest.get("source_plan_digest") == _coordinator_plan_digest(plan.get("waves"))
    ):
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
                    "depends_on": list(microtask.get("depends_on") or []),
                    "acceptance_criteria": [redact(item, 1000) for item in microtask.get("acceptance_criteria", [])],
                    "verification": checks,
                })
            work_package_details.append({
                "id": package.get("id"),
                "title": package.get("title"),
                "objective": redact(package.get("objective", ""), 1600),
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
        # Structured plan authority is the immutable coordinator start waves,
        # not a worker-authored result.  An approved basis remains current as
        # long as the manifest is still bound to those exact waves.
        manifest = current_planning_manifest(task_dir)
        if (
            not isinstance(manifest, dict)
            or manifest.get("source_authority") != "coordinator_start_waves"
            or manifest.get("source_plan_digest") != _coordinator_plan_digest(plan.get("waves"))
        ):
            invalidate_plan_approval_for_reopened_plan(
                state,
                reason="The approved coordinator wave manifest is missing or no longer matches.",
                event="stale_approved_recovery",
            )
            approval = _plan_approval(state)
        else:
            return None
    if approval.get("status") == "awaiting_user":
        changed = False
        if not str(approval.get("request_id") or "").strip():
            approval["request_id"] = _plan_approval_request_id(state, approval)
            changed = True
        review = dict(approval.get("review") or {})
        if review.get("request_id") != approval.get("request_id"):
            review["request_id"] = approval["request_id"]
            approval["review"] = review
            changed = True
        if changed:
            state["plan_approval"] = approval
            save_state(task_dir, task_dir / "state.sqlite", state, "plan_approval_request", "bound request id to the pending plan approval")
        return review
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
    review["request_id"] = approval["request_id"]
    approval["review"] = review
    state["plan_approval"] = approval
    save_state(task_dir, task_dir / "state.sqlite", state, "plan_approval", "awaiting explicit user approval of the completed plan")
    return review


def _validate_retry_strategy(
    state: dict[str, Any],
    attempt: dict[str, Any],
    outcome: dict[str, Any],
) -> None:
    # Strategy remains optional for an ordinary evidence-backed retry. Once
    # repeated failures are recorded, recovery still retries the selected
    # route. The coordinator may provide a new strategy, but Cortex does not
    # require a Planner or a user-authored replan to make progress.
    return


def _normalized_failure_reason(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _failure_class_from_outcome(outcome: dict[str, Any]) -> str:
    """Classify compact outcomes from status, never arbitrary report prose."""
    return "technical" if str(outcome.get("status") or "").lower() != "passed" else "product"


def _corrective_evidence(
    root: Path,
    state: dict[str, Any],
    gate: str,
    gate_attempts: list[dict[str, Any]],
    attempt_outcomes: list[dict[str, Any]],
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
    for _route_key, rework in (state.get("closure_rework") or {}).items():
        if (
            not isinstance(rework, dict)
            or str(rework.get("origin_gate") or "") != gate
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
        for item in attempt_outcomes
        if str(item.get("status") or "").lower() != "passed"
    ]
    # A passed worker result with unresolved canonical findings is still a
    # corrective non-progress observation.  Its canonical fingerprint set is
    # safer than a mutable worker summary and remains stable across retries.
    if not raw_reasons and finding_fingerprints:
        raw_reasons = ["unresolved canonical findings"]
    failure_classes = sorted({
        _failure_class_from_outcome(item)
        for item in attempt_outcomes
        if str(item.get("status") or "").lower() != "passed"
    })
    if not failure_classes and finding_fingerprints:
        failure_classes = ["product"]
    failure_observations = [
        {"status": status_value, "failure_class": failure_class}
        for status_value, failure_class in sorted({
            (
                str(item.get("status") or "").strip().lower(),
                _failure_class_from_outcome(item),
            )
            for item in attempt_outcomes
            if str(item.get("status") or "").lower() != "passed"
        })
    ]
    if not failure_observations and finding_fingerprints:
        failure_observations = [{"status": "canonical_rework", "failure_class": "product"}]
    strategy_values = sorted({
        str(item.get("next_strategy") or item.get("strategy") or "default").strip()
        for item in relevant_attempts + attempt_outcomes
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


def _active_no_progress_pauses(
    state: dict[str, Any],
    authority: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return advisory retry records, never a pipeline-pausing state."""
    raw = state.get("rework_pauses")
    pauses = {
        str(occurrence_gate_key): dict(pause)
        for occurrence_gate_key, pause in raw.items()
        if isinstance(pause, dict) and pause.get("status") == "retry_pending"
        and str(occurrence_gate_key).strip()
        and (
            authority is None
            or (
                str(pause.get("occurrence_key") or "")
                == str(authority.get("occurrence_key") or "")
                and str(pause.get("wave_ref") or "")
                == str(authority.get("wave_ref") or "")
                and str(pause.get("phase_ref") or "")
                == str(authority.get("phase_ref") or "")
                and str(pause.get("assignment_lineage_digest") or "")
                == str(authority.get("assignment_lineage_digest") or "")
            )
        )
    } if isinstance(raw, dict) else {}
    return pauses


def _store_no_progress_pauses(state: dict[str, Any], pauses: dict[str, dict[str, Any]]) -> None:
    """Persist only exact occurrence-scoped retry receipts."""
    active = {
        str(occurrence_gate_key): dict(pause)
        for occurrence_gate_key, pause in pauses.items()
        if isinstance(pause, dict) and pause.get("status") == "retry_pending"
        and str(pause.get("occurrence_key") or "").strip()
        and str(pause.get("wave_ref") or "").strip()
        and str(pause.get("phase_ref") or "").strip()
        and str(pause.get("assignment_lineage_digest") or "").strip()
    }
    if active:
        state["rework_pauses"] = active
    else:
        state.pop("rework_pauses", None)


def _record_corrective_progress(
    root: Path,
    state: dict[str, Any],
    authority: Mapping[str, Any],
    gate: str,
    gate_attempts: list[dict[str, Any]],
    attempt_outcomes: list[dict[str, Any]],
    unresolved_rework: list[dict[str, Any]],
    *,
    outcome: str,
) -> dict[str, Any] | None:
    """Update no-progress evidence and return a pause only for exact repeats."""
    occurrence_gate_key = _occurrence_gate_key(authority, gate)
    progress = state.setdefault("rework_progress", {})
    if not isinstance(progress, dict):
        progress = {}
        state["rework_progress"] = progress
    if outcome in {"passed", "skipped"}:
        progress.pop(occurrence_gate_key, None)
        return None
    if outcome != "failed":
        return None
    evidence = _corrective_evidence(root, state, gate, gate_attempts, attempt_outcomes, unresolved_rework)
    prior = (
        progress.get(occurrence_gate_key)
        if isinstance(progress.get(occurrence_gate_key), dict)
        else {}
    )
    same = prior.get("signature") == evidence["signature"]
    consecutive = int(prior.get("consecutive_identical_iterations") or 0) + 1 if same else 1
    event = {
        **evidence,
        "occurrence_key": str(authority["occurrence_key"]),
        "wave_ref": str(authority["wave_ref"]),
        "phase_ref": str(authority["phase_ref"]),
        "phase_kind": str(authority["phase_kind"]),
        "assignment_lineage_digest": str(authority["assignment_lineage_digest"]),
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
    progress[occurrence_gate_key] = event
    if consecutive < _NO_PROGRESS_REPEAT_LIMIT:
        return None
    failure_class = str(evidence["failure_classes"][0] or "product")
    recovery = (
        "retry the orchestrator-selected route through bounded technical recovery"
        if failure_class == "technical"
        else "retry the orchestrator-selected route and delegate the correction to the responsible worker"
    )
    return {
        "status": "retry_pending",
        "advisory": True,
        "gate": gate,
        "occurrence_key": str(authority["occurrence_key"]),
        "wave_ref": str(authority["wave_ref"]),
        "phase_ref": str(authority["phase_ref"]),
        "phase_kind": str(authority["phase_kind"]),
        "assignment_lineage_digest": str(authority["assignment_lineage_digest"]),
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


def _update_occurrence_failure_count(
    state: dict[str, Any],
    authority: Mapping[str, Any],
    gate: str,
    outcome: str,
) -> tuple[int, bool]:
    """Update the automatic retry counter for one exact gate occurrence."""
    counts = state.setdefault("orchestrate_occurrence_failure_counts", {})
    if not isinstance(counts, dict):
        counts = {}
        state["orchestrate_occurrence_failure_counts"] = counts
    key = _occurrence_gate_key(authority, gate)
    if outcome == "failed":
        prior = counts.get(key) if isinstance(counts.get(key), Mapping) else {}
        failure_count = int(prior.get("failure_count") or 0) + 1
        counts[key] = {
            "occurrence_key": str(authority["occurrence_key"]),
            "wave_ref": str(authority["wave_ref"]),
            "phase_ref": str(authority["phase_ref"]),
            "phase_kind": str(authority["phase_kind"]),
            "gate": gate,
            "assignment_lineage_digest": str(authority["assignment_lineage_digest"]),
            "failure_count": failure_count,
            "updated_at": now(),
        }
        return failure_count, True
    if outcome in {"passed", "skipped"}:
        changed = counts.pop(key, None) is not None
        if not counts:
            state.pop("orchestrate_occurrence_failure_counts", None)
        return 0, changed
    return int((counts.get(key) or {}).get("failure_count") or 0), False


def _apply_pending_revision_impact(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    completed_source_wave: Mapping[str, Any],
    *,
    transaction_path: Path | None = None,
    transaction: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Compile a material steer into fresh immutable wave occurrences.

    The completed source occurrence and every older occurrence remain immutable
    audit evidence.  When the steer reaches into completed work, the affected
    semantic suffix is cloned after the just-completed frontier.  When it
    targets only an unexecuted future occurrence, only that future tail is
    replaced.  Semantic phase names are never reset or used to invalidate
    attempts.
    """
    impact = state.get("pending_revision_impact")
    if not isinstance(impact, dict):
        return state, False
    task_revision = int(impact.get("task_revision") or 0)
    target = impact.get("target_occurrence")
    frontier_at_revision = impact.get("frontier_occurrence_at_revision")
    if task_revision < 1 or not isinstance(target, Mapping) or not isinstance(
        frontier_at_revision, Mapping
    ):
        raise ValueError("pending semantic revision lacks exact occurrence authority")

    waves = [item for item in plan.get("waves") or [] if isinstance(item, dict)]
    compiled_wave_execution_order(waves)
    source_authority = _compiled_wave_occurrence_authority(completed_source_wave)
    source_wave_ref = str(source_authority["wave_ref"])
    if source_wave_ref not in {
        str(item) for item in state.get("completed_orchestration_wave_ids") or []
    }:
        raise ValueError("semantic revision source occurrence is not durably completed")
    source_position = next((
        index for index, wave in enumerate(waves)
        if str(wave.get("wave_ref") or "") == source_wave_ref
    ), -1)
    target_wave_ref = str(target.get("wave_ref") or "")
    target_position = next((
        index for index, wave in enumerate(waves)
        if str(wave.get("wave_ref") or "") == target_wave_ref
    ), -1)
    if source_position < 0 or target_position < 0:
        raise ValueError("semantic revision occurrence is outside the canonical plan")
    target_wave = waves[target_position]
    target_authority = _compiled_wave_occurrence_authority(target_wave)
    for field in ("wave_ref", "phase_ref", "phase_kind", "wave_index"):
        if str(target_authority.get(field)) != str(target.get(field)):
            raise ValueError("semantic revision target occurrence identity changed")
    if list(target_authority.get("gates") or []) != list(target.get("gates") or []):
        raise ValueError("semantic revision target occurrence gates changed")
    for field in ("wave_ref", "phase_ref", "phase_kind", "wave_index"):
        if str(source_authority.get(field)) != str(frontier_at_revision.get(field)):
            raise ValueError("semantic revision completed a different frontier occurrence")
    if list(source_authority.get("gates") or []) != list(
        frontier_at_revision.get("gates") or []
    ):
        raise ValueError("semantic revision completed frontier gates changed")

    source_attempts = [
        item for item in state.get("attempts") or []
        if isinstance(item, dict)
        and not item.get("invalidated")
        and str(item.get("wave_ref") or "") == source_wave_ref
        and str(item.get("phase_ref") or "") == str(source_authority["phase_ref"])
        and str(item.get("attempt_result_ref") or "")
        and str(item.get("continuation_consumed_at") or "")
    ]
    source_result_refs = [
        str(item["attempt_result_ref"]) for item in source_attempts
    ]
    if not source_result_refs or len(source_result_refs) != len(set(source_result_refs)):
        raise ValueError("semantic revision source results are unavailable or ambiguous")
    if any(
        isinstance(item, Mapping)
        and not item.get("invalidated")
        and str(item.get("wave_ref") or "") in {
            str(wave.get("wave_ref") or "") for wave in waves[source_position + 1:]
        }
        for item in state.get("attempts") or []
    ):
        raise ValueError("semantic revision cannot replace an already-dispatched successor")

    # Preserve every completed/current occurrence.  If the target is a future
    # occurrence, also preserve the unaffected future prefix before it.
    prefix_end = max(source_position + 1, target_position)
    immutable_prefix = list(waves[:prefix_end])
    semantic_suffix = list(waves[target_position:])
    if not semantic_suffix:
        raise ValueError("semantic revision has no affected occurrence suffix")
    from cortex_runtime.assignment_compiler import next_compiled_wave_index
    allocated = next_compiled_wave_index(waves, count=len(semantic_suffix))
    task = load_task_definition(task_dir, state)
    current_intent = redact(
        task.get("current_user_intent") or task.get("user_request") or "", 2_000,
    )
    raw_waves: list[dict[str, Any]] = []
    semantic_fields = {
        "gate", "agent", "profile", "requested_profile", "operation_kind",
        "objective", "ownership", "acceptance_criteria", "verification",
        "model", "reasoning_effort", "configured_default_model", "task_kind",
        "risk", "strategy",
        "context_files", "requirements", "constraints", "scope",
    }
    for offset, old_wave in enumerate(semantic_suffix):
        identity = allocated[offset]
        delegations: list[dict[str, Any]] = []
        for old_spec in old_wave.get("delegations") or []:
            if not isinstance(old_spec, Mapping):
                raise ValueError("semantic revision source assignment is invalid")
            spec = {
                key: json.loads(json.dumps(old_spec[key], ensure_ascii=False))
                for key in semantic_fields if key in old_spec
            }
            revision_requirement = (
                f"Apply task revision {task_revision}: {current_intent}"
            )
            requirements = [
                str(item) for item in spec.get("requirements") or [] if str(item)
            ]
            spec["requirements"] = list(dict.fromkeys([
                *requirements, revision_requirement,
            ]))
            spec["objective"] = (
                f"Re-evaluate this exact occurrence for task revision {task_revision}. "
                f"Material steer: {current_intent}\n\n"
                f"Prior occurrence objective: {str(spec.get('objective') or '').strip()}"
            ).strip()
            acceptance = [
                str(item) for item in spec.get("acceptance_criteria") or [] if str(item)
            ]
            spec["acceptance_criteria"] = [
                *acceptance,
                f"The result satisfies task revision {task_revision} and the material steer.",
            ]
            verification = [
                str(item) for item in spec.get("verification") or [] if str(item)
            ]
            spec["verification"] = [
                *verification,
                f"Verify the result against task revision {task_revision} and its source evidence.",
            ]
            delegations.append(spec)
        if not delegations:
            raise ValueError("semantic revision source wave has no assignments")
        if offset == 0:
            delegations[0]["wave_index"] = identity
        raw_waves.append({
            "wave_id": f"wave-{identity:02d}",
            "delegations": delegations,
        })
    inserted, _classification = _normalize_orchestrate_waves(
        raw_waves,
        task,
        plan.get("host_capabilities") or {},
        str(task["project_root"]),
        prior_wave_refs=[
            str(wave.get("wave_ref") or "") for wave in immutable_prefix
        ],
    )
    if len(inserted) != len(semantic_suffix):
        raise ValueError("semantic revision changed the compiled occurrence cardinality")
    effective = [*immutable_prefix, *inserted]
    compiled_wave_execution_order(effective)
    old_attempt_audit_digest = digest_text(canonical_json.dumps([
        {
            key: item.get(key)
            for key in (
                "attempt_id", "wave_ref", "phase_ref", "gate", "status",
                "attempt_result_ref", "continuation_consumed_at", "invalidated",
            )
        }
        for item in state.get("attempts") or [] if isinstance(item, Mapping)
    ]))
    base_pipeline_digest = str(
        plan.get("semantic_future_pipeline_digest")
        or _semantic_future_pipeline_digest(plan)
    )
    impact_digest = digest_text(canonical_json.dumps(impact))
    request_digest = digest_text(canonical_json.dumps({
        "task_revision": task_revision,
        "impact_digest": impact_digest,
        "base_pipeline_digest": base_pipeline_digest,
        "target_occurrence": dict(target),
        "completed_source_occurrence": source_authority,
        "source_result_refs": source_result_refs,
    }))
    application_mode = (
        "replace_unexecuted_future_tail"
        if target_position > source_position
        else "append_rework_after_completed_occurrence"
    )
    retired_future = list(waves[prefix_end:])
    receipt = {
        "schema": "cortex/semantic-steer-occurrence-revision/v1",
        "classification": "semantic_steer_occurrence_revision",
        "task_revision": task_revision,
        "request_digest": request_digest,
        "impact_digest": impact_digest,
        "base_pipeline_digest": base_pipeline_digest,
        "application_mode": application_mode,
        "target_occurrence": dict(target_authority),
        "completed_source_occurrence": dict(source_authority),
        "source_attempt_ids": [str(item.get("attempt_id") or "") for item in source_attempts],
        "source_result_refs": source_result_refs,
        "retired_future_wave_refs": [
            str(wave.get("wave_ref") or "") for wave in retired_future
        ],
        "appended_wave_refs": [
            str(wave.get("wave_ref") or "") for wave in inserted
        ],
        "prior_attempt_audit_digest": old_attempt_audit_digest,
        "at": now(),
    }
    plan["waves"] = effective
    plan["chosen_pipeline"] = [
        str(gate) for wave in effective for gate in wave.get("gates") or []
    ]
    plan["chosen_parallel_groups"] = [
        list(wave.get("gates") or []) for wave in effective
    ]
    plan.setdefault("semantic_steer_revisions", []).append(receipt)
    plan["semantic_steer_revisions"] = plan["semantic_steer_revisions"][-32:]
    plan["semantic_future_pipeline_digest"] = _semantic_future_pipeline_digest(plan)
    state["current_pipeline"] = list(dict.fromkeys(plan["chosen_pipeline"]))
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
    for route in (state.get("product_rework_routes") or {}).values():
        if isinstance(route, dict) and route.get("status") in {"active", "awaiting_close"}:
            route.update({
                "status": "superseded",
                "superseded_at": now(),
                "superseded_by_task_revision": impact.get("task_revision"),
            })
    _sync_orchestration_wave_occurrences(state, plan)
    new_attempt_audit_digest = digest_text(canonical_json.dumps([
        {
            key: item.get(key)
            for key in (
                "attempt_id", "wave_ref", "phase_ref", "gate", "status",
                "attempt_result_ref", "continuation_consumed_at", "invalidated",
            )
        }
        for item in state.get("attempts") or [] if isinstance(item, Mapping)
    ]))
    if new_attempt_audit_digest != old_attempt_audit_digest:
        raise ValueError("semantic revision mutated immutable attempt history")
    state["status"] = "active"
    state.pop("blocked_reason", None)
    sync_current_wave(state)
    applied = {**impact, **receipt, "applied_at": now()}
    state.setdefault("applied_revision_impacts", []).append(applied)
    state["applied_revision_impacts"] = state["applied_revision_impacts"][-32:]
    state.pop("pending_revision_impact", None)
    root = _ledger_root_for_artifact(task_dir)
    with db_transaction(root):
        revision = ledger_db.append_plan_revision(
            root,
            str(state["task_id"]),
            task_revision=task_revision,
            impact=receipt,
            plan=plan,
            status="active",
        )
        plan["plan_revision"] = revision["plan_revision"]
        state["plan_revision"] = revision["plan_revision"]
        state["plan_digest"] = revision["plan_digest"]
        _write_orchestrate_plan(task_dir, plan, preserve_updated_at=True)
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "semantic_revision_occurrences_compiled",
            (
                f"compiled exact occurrence rework for task revision {task_revision}; "
                f"target={target_wave_ref}; source={source_wave_ref}"
            ),
        )
        if transaction_path is not None and isinstance(transaction, dict):
            # The occurrence compiler and continuation checkpoint share the
            # same SQLite transaction. A crash after plan replacement can
            # therefore resume from the completed source occurrence without
            # replaying its canonical result into the fresh frontier.
            _checkpoint_orchestrate_transaction(
                transaction_path,
                transaction,
                "gates_recorded",
                gates=list(completed_source_wave.get("gates") or []),
                semantic_revision_request_digest=request_digest,
                semantic_revision_wave_refs=[
                    str(wave.get("wave_ref") or "") for wave in inserted
                ],
            )
    return state, True


def _preflight_attempt_outcome(
    task_dir: Path,
    state: dict[str, Any],
    outcome: dict[str, Any],
) -> None:
    """Validate a canonical attempt outcome without mutating the task ledger.

    Native spawn/wait observations are lifecycle telemetry only. They may be
    absent or unknown and never authorize, attest, or block semantic
    continuation; the exact active attempt and its finalized canonical
    AttemptResult are the authoritative evidence.
    """
    attempt_id = safe_id(str(outcome.get("attempt_id", "")))
    attempt = _attempt(state, attempt_id)
    requested_status = str(outcome.get("status", "passed")).strip().lower()
    if requested_status not in TERMINAL_ATTEMPT_STATUSES:
        raise ValueError("outcome status must be passed, failed, blocked, cancelled, or superseded")
    _validate_retry_strategy(state, attempt, outcome)
    if attempt.get("status") in TERMINAL_ATTEMPT_STATUSES:
        if attempt.get("status") != requested_status:
            raise ValueError("outcome status does not match the terminal ledger attempt")
        return
    open_questions = _open_blocking_questions(task_dir, state, attempt_id)
    if open_questions:
        refs = ", ".join(str(item["question_id"]) for item in open_questions)
        raise ValueError(
            f"attempt has unanswered blocking worker question(s): {refs}; "
            "answer the question and resume the same worker before completion"
        )
    if requested_status == "passed":
        result_ref = str(outcome.get("attempt_result_ref") or "").strip()
        if not result_ref:
            raise ValueError("passed outcome requires attempt_result_ref from complete_attempt")
        if result_ref != str(attempt.get("attempt_result_ref") or ""):
            raise ValueError("passed outcome must select the canonical result for its exact attempt")
        result = attempt_protocol.get_attempt_result(
            _ledger_root_for_artifact(task_dir),
            task_id=state["task_id"], attempt_id=attempt_id,
        )
        if (
            result is None
            or str(result.get("result_ref") or "") != result_ref
            or str(result.get("lifecycle_status") or "") != attempt_protocol.LIFECYCLE_COMPLETED
        ):
            raise ValueError("passed outcome requires a finalized canonical attempt result")
    elif not str(outcome.get("reason", "")).strip():
        raise ValueError("non-success outcome requires an explicit reason")


def _apply_next_retry_strategies(
    wave: dict[str, Any],
    state: dict[str, Any],
    attempt_outcomes: list[dict[str, Any]],
) -> None:
    """Carry an explicitly revised strategy into only the matching retry slot."""
    by_key = {
        str(spec.get("orchestration_delegation_key") or ""): spec
        for spec in wave.get("delegations", [])
        if isinstance(spec, dict)
    }
    for outcome in attempt_outcomes:
        next_strategy = str(outcome.get("next_strategy") or "").strip()
        if not next_strategy:
            continue
        attempt = _attempt(state, safe_id(str(outcome.get("attempt_id", ""))))
        key = str(attempt.get("orchestration_delegation_key") or "")
        spec = by_key.get(key)
        if spec is None:
            raise ValueError("next_strategy cannot identify the matching retry slot")
        spec["strategy"] = next_strategy
        if not str(spec.get("logical_delegation_key") or "").strip():
            raise ValueError("revised retry assignment lacks its normalized logical delegation key")
        spec["assignment_lineage_digest"] = _assignment_lineage_digest(spec)
        spec["plan_assignment_lineage_digest"] = spec["assignment_lineage_digest"]


def _auto_handoff(params: dict[str, Any], task_dir: Path, state: dict[str, Any], next_action: str) -> dict[str, Any]:
    baseline = task_manifest_baseline(task_dir, state)
    current = capture_project_manifest(Path(baseline["project_root"]), policy=baseline.get("policy"))
    comparison = compare_manifests(baseline, current)
    plan = _load_orchestrate_plan(task_dir, state)
    completed_occurrences = bound_symbol(
        "orchestration_engine", "_completed_handoff_occurrences",
    )(
        state,
        artifact_root=_ledger_root_for_artifact(task_dir),
        plan=plan,
    )
    completed = [canonical_json.dumps(item) for item in completed_occurrences]
    if not completed:
        raise ValueError("automatic handoff requires completed assignment occurrences")
    # The lifecycle runner carries only the durable task record at this
    # internal seam; explicit coordinator capability authorization has already
    # completed at the public facade. Resolve the handoff seam at call time so
    # stable tests can replace the facade adapter without re-importing this
    # engine.
    return bound_symbol("orchestration_engine", "handoff")({
        **params,
        "task_id": state["task_id"],
        "principal": state.get("principal"),
        "expected_revision": state["revision"],
        "name": f"orchestrate-{primary_gate(state)}-{state['revision'] + 1}",
        "completed": completed,
        "files": comparison["changed_paths"],
        "decisions": ["Unified orchestrate facade reconciled the current wave."],
        "risks": [],
        "next_action": next_action,
    })


def _finalize_completed_lifecycle(
    params: dict[str, Any], task_dir: Path, state: dict[str, Any]
) -> dict[str, Any]:
    """Persist the canonical handoff and close receipt before projection."""
    if not state.get("handoff_created"):
        handed = _auto_handoff(params, task_dir, state, "Close the Cortex task.")
        if handed.get("recorded") is False:
            raise ValueError(str(handed.get("reason") or "automatic handoff was not recorded"))
        state = handed["state"]
    return close_audit({**params, "task_id": state["task_id"]})


def _consume_attempt_outcome(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    outcome: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    attempt_id = safe_id(str(outcome.get("attempt_id", "")))
    attempt = _attempt(state, attempt_id)
    requested_status = str(outcome.get("status", "passed")).strip().lower()
    if requested_status not in TERMINAL_ATTEMPT_STATUSES:
        raise ValueError("outcome status must be passed, failed, blocked, cancelled, or superseded")
    if attempt.get("status") in TERMINAL_ATTEMPT_STATUSES:
        if attempt.get("status") != requested_status:
            raise ValueError("outcome status does not match the terminal ledger attempt")
        replay_result_ref = str(attempt.get("attempt_result_ref") or "").strip()
        if replay_result_ref:
            persist_assignment_evaluation(
                _ledger_root_for_artifact(task_dir), state, attempt,
            )
            # Finalization and closure-route receipts are separate durable
            # writes.  Replaying a terminal continuation after a crash must
            # complete the same exact post-evaluation sequence; both helpers
            # are idempotent and bind the canonical result/occurrence.
            _record_product_rework_route_result(
                task_dir,
                state,
                attempt,
                replay_result_ref,
            )
            _record_server_corrective_receipts(
                _ledger_root_for_artifact(task_dir),
                state,
                attempt,
                replay_result_ref,
            )
            _resolve_origin_verified_findings(
                _ledger_root_for_artifact(task_dir),
                state,
                attempt,
                replay_result_ref,
            )
            attempt["continuation_consumed_at"] = attempt.get("continuation_consumed_at") or now()
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "assignment_acceptance_evaluated",
                f"{attempt_id}: {attempt.get('acceptance_status')}",
            )
        return state, None
    result_ref = str(outcome.get("attempt_result_ref") or "").strip()
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
        package = _delegation_package(task_dir, state["task_id"], attempt_id)
        package["lifecycle_status"] = canonical.get("lifecycle_status")
        package["attempt_status"] = finalized_attempt.get("status")
        _write_delegation_package(task_dir, state["task_id"], attempt_id, package)
        persist_assignment_evaluation(
            _ledger_root_for_artifact(task_dir), finalized_state, finalized_attempt,
        )
        _record_product_rework_route_result(
            task_dir,
            finalized_state,
            finalized_attempt,
            result_ref,
        )
        _record_server_corrective_receipts(
            _ledger_root_for_artifact(task_dir),
            finalized_state,
            finalized_attempt,
            result_ref,
        )
        _resolve_origin_verified_findings(
            _ledger_root_for_artifact(task_dir),
            finalized_state,
            finalized_attempt,
            result_ref,
        )
        finalized_attempt["continuation_consumed_at"] = (
            finalized_attempt.get("continuation_consumed_at") or now()
        )
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            finalized_state,
            "assignment_acceptance_evaluated",
            f"{attempt_id}: {finalized_attempt.get('acceptance_status')}",
        )
        return finalized_state, None
    if requested_status == "passed":
        raise ValueError("passed outcome requires attempt_result_ref from complete_attempt")
    finalized = finalize_attempt({
        **params,
        **outcome,
        "task_id": state["task_id"],
        "attempt_id": attempt_id,
        "status": requested_status,
        "reason": str(outcome.get("reason") or "canonical worker result reported terminal non-success"),
    })
    if finalized.get("recorded") is False:
        raise ValueError(str(finalized.get("reason") or "attempt finalization failed"))
    terminal_attempt = _attempt(finalized["state"], attempt_id)
    terminal_attempt["completion_transport_status"] = "not_recorded"
    terminal_attempt["gate_decision"] = requested_status
    if str(terminal_attempt.get("attempt_result_ref") or "").strip():
        persist_assignment_evaluation(
            _ledger_root_for_artifact(task_dir), finalized["state"], terminal_attempt,
        )
        terminal_attempt["continuation_consumed_at"] = (
            terminal_attempt.get("continuation_consumed_at") or now()
        )
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
    if attempt_gate == "governance_close":
        validate_governance_closure_authority(
            state,
            artifact_root=_ledger_root_for_artifact(task_dir),
            require_evidence=False,
            current_attempt=attempt,
        )
    governance_obligations = (
        list(_governance_obligations_for_gate(state, attempt_gate))
        if attempt_gate in {"governance_activation", "governance_close"}
        else []
    )
    if governance_obligations:
        # The projection binds the canonical result and reviewer identity but
        # preserves semantic verification as worker-attested. Server authority
        # comes only from the evaluator's manifest/revision/result/Stop facts.
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
    elif governance_obligations or command:
        evaluation = attempt.get("acceptance_evaluation")
        if not isinstance(evaluation, dict) or evaluation.get("acceptance_status") != "passed":
            raise ValueError(
                "verification evidence projection requires a passed canonical assignment evaluation"
            )
        result = record_evidence({
            **evidence_params,
            "kind": "verification_evidence",
        })
    else:
        result = record_evidence({**evidence_params, "kind": "result"})
    if result.get("recorded") is False:
        raise ValueError(str(result.get("reason") or "attempt evidence was not recorded"))
    return result["state"]


def _orchestrate_continue(params: dict[str, Any], transaction_path: Path, transaction: dict[str, Any]) -> dict[str, Any]:
    task_id = safe_id(str(params.get("task_id", "")))
    submitted_refs = params.get("result_refs")
    if not isinstance(submitted_refs, list) or not submitted_refs:
        raise ValueError("continue requires a non-empty result_refs array")
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(task_id, params)
        authorize(state, params)
        from cortex_runtime.native_lifecycle_observer import reconcile_native_stop_inbox

        reconcile_native_stop_inbox(root, task_dir, state)
        plan = _load_orchestrate_plan(task_dir, state)
        terminal_closure = _terminal_governance_closure_breaker(state)
        if terminal_closure is not None:
            return _orchestrate_response(
                "continue",
                state,
                wave_id=None,
                spawn_requests=[],
                result={
                    "terminal_governance_closure": {
                        "status": "blocked",
                        "accepted": True,
                        "retryable": False,
                        "state_mutated": False,
                        "fingerprint": str(terminal_closure.get("fingerprint") or ""),
                    },
                },
                plan=plan,
            )
        transaction_phase = str(transaction.get("phase") or "")
        if transaction_phase in {
            "technical_recovery_prepared", "technical_recovery_terminal",
        }:
            context = transaction.get("context")
            context = context if isinstance(context, Mapping) else {}
            batch_key = str(context.get("recovery_batch_key") or "")
            batches = [
                item for item in state.get("reliability_recovery_batches") or []
                if isinstance(item, Mapping)
                and str(item.get("batch_key") or "") == batch_key
            ]
            if not batch_key or len(batches) != 1:
                raise ValueError("technical recovery transaction has no exact batch receipt")
            batch = batches[0]
            expected_status = (
                "dispatched"
                if transaction_phase == "technical_recovery_prepared"
                else "terminal_blocked"
            )
            if str(batch.get("status") or "") != expected_status:
                raise ValueError("technical recovery batch receipt status does not match its transaction")
            if transaction_phase == "technical_recovery_terminal":
                return _orchestrate_response(
                    "continue",
                    state,
                    wave_id=str(batch.get("wave_id") or "") or None,
                    spawn_requests=[],
                    result={
                        "recovery": {
                            "schema": "cortex/recovery-contract/v1",
                            "mode": "terminal_reliability_batch",
                            "batch_key": batch_key,
                            "status": "terminal",
                            "retryable": False,
                            "state_mutated": False,
                            "failure_fingerprints": list(
                                batch.get("source_failure_fingerprints") or []
                            ),
                            "pending_product_rework_count": int(
                                batch.get("pending_product_rework_count") or 0
                            ),
                        },
                        "idempotent": True,
                    },
                    plan=plan,
                )
            current_wave, current_attempts = _effective_plan_frontier(plan, state)
            current_ids = [
                str(item.get("attempt_id") or "") for item in current_attempts
            ]
            receipt_ids = [str(item) for item in batch.get("attempt_ids") or []]
            context_ids = [str(item) for item in context.get("attempt_ids") or []]
            if (
                not isinstance(current_wave, Mapping)
                or str(current_wave.get("wave_id") or "")
                != str(batch.get("wave_id") or "")
                or current_ids != receipt_ids
                or receipt_ids != context_ids
            ):
                raise ValueError("technical recovery replay frontier does not match its exact batch")
            task = load_task_definition(task_dir, state)
            pending = [
                {
                    **_rehydrate_dispatch_spawn_request(task_dir, task, attempt),
                    "attempt_id": str(attempt.get("attempt_id") or ""),
                }
                for attempt in current_attempts
                if attempt.get("status") == AWAITING_HOST_SPAWN
                and attempt.get("dispatch_delivery_status") == "pending"
            ]
            return _orchestrate_response(
                "continue",
                state,
                wave_id=str(batch.get("wave_id") or "") or None,
                spawn_requests=pending,
                result={
                    "recovery": {
                        "schema": "cortex/recovery-contract/v1",
                        "mode": "exact_assignment_recovery_batch",
                        "batch_key": batch_key,
                        "status": "dispatched",
                        "retryable": True,
                        "state_mutated": False,
                        "pending_product_rework_count": int(
                            batch.get("pending_product_rework_count") or 0
                        ),
                    },
                    "idempotent": True,
                },
                plan=plan,
            )
        attempt_outcomes: list[dict[str, Any]] = []
        for index, submitted in enumerate(submitted_refs):
            if not isinstance(submitted, dict) or set(submitted) != {"attempt_id", "attempt_result_ref"}:
                raise ValueError(
                    f"result_refs[{index}] must contain only attempt_id and attempt_result_ref"
                )
            attempt_id = safe_id(str(submitted.get("attempt_id") or ""))
            result_ref = str(submitted.get("attempt_result_ref") or "").strip()
            attempt = _attempt(state, attempt_id)
            if not result_ref or result_ref != str(attempt.get("attempt_result_ref") or ""):
                raise ValueError("attempt_result_ref does not belong to the exact active attempt")
            canonical = attempt_protocol.get_attempt_result(
                root,
                task_id=state["task_id"],
                attempt_id=attempt_id,
            )
            if canonical is None or str(canonical.get("result_ref") or "") != result_ref:
                raise ValueError("attempt_result_ref is not a canonical AttemptResult")
            semantic_status = str(
                canonical.get("result_status") or canonical.get("status") or ""
            ).strip().lower()
            requested_status = {
                "completed": "passed",
                "passed": "passed",
                "failed": "failed",
                "blocked": "blocked",
                "cancelled": "cancelled",
                "canceled": "cancelled",
                "superseded": "superseded",
            }.get(semantic_status)
            if requested_status is None:
                raise ValueError("canonical AttemptResult has no supported terminal semantic status")
            lifecycle = str(canonical.get("lifecycle_status") or "")
            if lifecycle not in {
                attempt_protocol.LIFECYCLE_COMPLETED,
                attempt_protocol.LIFECYCLE_FAILED,
                attempt_protocol.LIFECYCLE_BLOCKED,
            }:
                raise ValueError("attempt_result_ref is not finalized")
            outcome = {
                "attempt_id": attempt_id,
                "attempt_result_ref": result_ref,
                "status": requested_status,
            }
            if requested_status != "passed":
                outcome["reason"] = str(
                    canonical.get("summary") or canonical.get("failure_class") or "semantic worker non-success"
                )
            attempt_outcomes.append(outcome)
        task = load_task_definition(task_dir, state)
        # Do this before completing the current worker or recording a gate.
        # A successor briefing is rendered later, but its task-domain
        # validation must never be the first operation that discovers an
        # invalid durable task after the source wave has been consumed.
        _preflight_dispatch_context(task, state)
        current_wave, current_frontier_attempts = _effective_plan_frontier(plan, state)
        executable_gates = list(current_wave.get("gates", [])) if current_wave else []
        requested_wave_id = safe_id(str(params.get("wave_id", "")))
        if current_wave is None or current_wave.get("wave_id") != requested_wave_id:
            prior_wave = next((wave for wave in plan.get("waves", []) if wave.get("wave_id") == requested_wave_id), None)
            transaction_phase = str(transaction.get("phase", ""))
            if (
                prior_wave is None
                or prior_wave.get("status") not in {"completed", "blocked"}
                or transaction_phase not in {"gates_recorded", "next_wave_prepared"}
            ):
                raise ValueError("continue wave_id does not match the active Cortex wave")
            # The prior call crossed the gate boundary but crashed before its
            # transaction receipt was committed. Continue only the remaining
            # post-gate phases; never replay its result refs into the new wave.
            if state.get("status") == "completed":
                audited = _finalize_completed_lifecycle(params, task_dir, state)
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
                            "continue",
                            recovered_state,
                            wave_id=prepared["wave_id"],
                            spawn_requests=prepared["spawn_requests"],
                            result={"recovery": receipt},
                            plan=recovered_plan,
                        )
                return _orchestrate_response("continue", audited["state"], wave_id=requested_wave_id, result={"result_count": audited["result_count"]}, plan=plan)
            if state.get("status") == "blocked":
                if state.get("user_stop_requested"):
                    return _orchestrate_response("continue", state, wave_id=requested_wave_id, plan=plan)
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
                        "continue",
                        recovered_state,
                        wave_id=prepared["wave_id"],
                        spawn_requests=prepared["spawn_requests"],
                        result={"recovery": receipt},
                        plan=recovered_plan,
                    )
                return _orchestrate_response(
                    "continue",
                    state,
                    wave_id=requested_wave_id,
                    result={"recovery": {"status": "reconciliation_pending", "automatic": True}},
                    plan=plan,
                )
            review = _hold_for_plan_approval(task_dir, state, plan)
            if review is not None:
                return _orchestrate_response(
                    "continue", state, wave_id=requested_wave_id,
                    result={"plan_review": review}, plan=plan,
                )
            prepared = _prepare_orchestrate_wave(params, task_dir, state, plan)
            _checkpoint_orchestrate_transaction(transaction_path, transaction, "next_wave_prepared", wave_id=prepared["wave_id"], attempt_ids=prepared["attempt_ids"])
            return _orchestrate_response(
                "continue",
                prepared["state"],
                wave_id=prepared["wave_id"],
                spawn_requests=prepared["spawn_requests"],
                plan=plan,
            )
        provided_attempt_ids = {safe_id(str(item.get("attempt_id", ""))) for item in attempt_outcomes if isinstance(item, dict)}
        if len(provided_attempt_ids) != len(attempt_outcomes):
            raise ValueError("continue server-derived active attempt ids must be unique")
        _validate_occurrence_frontier_submission(
            current_frontier_attempts,
            provided_attempt_ids,
        )
        for outcome in attempt_outcomes:
            if not isinstance(outcome, dict):
                raise ValueError("outcome entries must be objects")
            _preflight_attempt_outcome(task_dir, state, outcome)
        for outcome in attempt_outcomes:
            if not isinstance(outcome, dict):
                raise ValueError("outcome entries must be objects")
            state, _unused = _consume_attempt_outcome(params, task_dir, state, outcome)
        occurrence_authority = _compiled_wave_occurrence_authority(current_wave)
        occurrence_attempts = _wave_occurrence_reduction_attempts(
            state,
            current_wave,
            submitted_attempt_ids=provided_attempt_ids,
        )
        _occurrence_acceptance_decision(occurrence_attempts)
        recovery_queue = _materialize_wave_recovery_queue(
            task_dir,
            state,
            plan,
            current_wave,
            occurrence_attempts,
            event="continue_wave_recovery_queue_materialized",
        )
        technical_failure_attempts = list(
            recovery_queue["technical_failure_attempts"]
        )
        if technical_failure_attempts:
            dispatched = _dispatch_server_owned_recovery(
                params,
                task_dir,
                state,
                plan,
                reason=(
                    "Server-owned bounded recovery for every canonical technical "
                    f"assignment failure ({len(technical_failure_attempts)} exact slot(s))."
                ),
                transaction_path=transaction_path,
                transaction=transaction,
            )
            if dispatched is None:
                raise ValueError("canonical technical failure has no deterministic recovery route")
            state, plan, prepared, recovery_receipt = dispatched
            return _orchestrate_response(
                "continue",
                state,
                wave_id=prepared["wave_id"],
                spawn_requests=prepared["spawn_requests"],
                result={"recovery": recovery_receipt},
                plan=plan,
            )
        if state.get("pending_product_reworks"):
            receipt = state["pending_product_reworks"][0]
            state["status"] = "rework_preflight_required"
            current_wave["status"] = "needs_rework"
            with ledger_db.transaction(root):
                _write_orchestrate_plan(task_dir, plan)
                save_state(
                    task_dir,
                    task_dir / "state.sqlite",
                    state,
                    "product_rework_required",
                    str(receipt.get("source_attempt_id") or ""),
                )
            return _orchestrate_response(
                "continue",
                state,
                wave_id=requested_wave_id,
                result={"product_rework": receipt},
                plan=plan,
            )
        _apply_next_retry_strategies(current_wave, state, attempt_outcomes)
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
        completions_by_attempt = {
            safe_id(str(item.get("attempt_id") or "")): item
            for item in attempt_outcomes
            if isinstance(item, dict)
        }
        no_progress_pauses = _active_no_progress_pauses(state)
        newly_paused_occurrence_keys: list[str] = []
        for gate in list(executable_gates):
            # Re-resolve after each durable transition because record/evidence
            # helpers may reload state. Historical attempts with the same
            # semantic gate never participate in this occurrence's decision.
            occurrence_attempts = _wave_occurrence_reduction_attempts(
                state,
                current_wave,
                submitted_attempt_ids=provided_attempt_ids,
            )
            gate_attempts = [
                item for item in occurrence_attempts
                if str(item.get("gate") or "") == gate
            ]
            if not gate_attempts:
                raise ValueError("compiled gate occurrence has no exact assignment result")
            current_gate_attempts = [
                item for item in gate_attempts
                if item.get("attempt_id") in completions_by_attempt
            ]
            gate_acceptance = _occurrence_acceptance_decision(gate_attempts)
            gate_decision = {
                "blocked": "blocked",
                "failed": "fail",
                "needs_rework": "rework",
                "passed": "pass",
            }[gate_acceptance]
            default_outcome = "passed" if gate_acceptance == "passed" else "failed"
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
            outcome = default_outcome
            if outcome == "blocked" and gate == "governance_close":
                accepted: tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None = None
                for closure_attempt in reversed(current_gate_attempts):
                    result_ref = str(closure_attempt.get("attempt_result_ref") or "").strip()
                    if not result_ref:
                        continue
                    canonical = attempt_protocol.get_attempt_result(
                        root,
                        task_id=str(state["task_id"]),
                        attempt_id=str(closure_attempt.get("attempt_id") or ""),
                    )
                    metadata = (
                        canonical.get("metadata")
                        if isinstance(canonical, dict) and isinstance(canonical.get("metadata"), dict)
                        else {}
                    )
                    closure = (
                        metadata.get("governance_closure")
                        if isinstance(metadata.get("governance_closure"), dict)
                        else {}
                    )
                    if (
                        isinstance(canonical, dict)
                        and canonical.get("status") == "blocked"
                        and closure.get("closure_outcome") == "blocked"
                        and str(closure.get("closure_basis_digest") or "")
                    ):
                        accepted = (closure_attempt, canonical, closure)
                        break
                if accepted is not None:
                    closure_attempt, canonical, closure = accepted
                    logical_key = str(closure_attempt.get("logical_delegation_key") or "").strip()
                    basis_digest = str(closure.get("closure_basis_digest") or "").strip()
                    if not logical_key:
                        raise ValueError("accepted governance closure lacks logical delegation lineage")
                    fingerprint = digest_text(canonical_json.dumps({
                        "kind": "governance_closure_terminal_blocked",
                        "logical_delegation_key": logical_key,
                        "closure_basis_digest": basis_digest,
                        "attempt_result_ref": str(canonical.get("result_ref") or ""),
                    }))
                    receipts = [
                        item for item in state.get("terminal_recovery_breakers") or []
                        if isinstance(item, dict)
                    ]
                    receipt = next(
                        (item for item in receipts if item.get("fingerprint") == fingerprint),
                        None,
                    )
                    state_mutated = receipt is None
                    if receipt is None:
                        receipt = {
                            "schema": "cortex/terminal-recovery-breaker/v1",
                            "kind": "governance_closure_terminal_blocked",
                            "fingerprint": fingerprint,
                            "logical_delegation_key": logical_key,
                            "closure_basis_digest": basis_digest,
                            "attempt_result_ref": str(canonical.get("result_ref") or ""),
                            "created_at": now(),
                        }
                        state["terminal_recovery_breakers"] = [*receipts, receipt]
                        state["status"] = "terminal_blocked"
                        state["blocked_gate"] = "governance_close"
                        state["blocked_reason"] = "governance_closure_terminal_blocked"
                        save_state(
                            task_dir,
                            task_dir / "state.sqlite",
                            state,
                            "governance_closure_terminal_blocked",
                            "accepted terminal blocked governance closure; no replacement is authorized",
                        )
                    return _orchestrate_response(
                        "continue",
                        state,
                        wave_id=requested_wave_id,
                        result={
                            "terminal_governance_closure": {
                                "status": "blocked",
                                "accepted": True,
                                "retryable": False,
                                "state_mutated": state_mutated,
                                "fingerprint": fingerprint,
                            },
                        },
                        plan=plan,
                    )
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
            failure_count, failure_count_changed = _update_occurrence_failure_count(
                state,
                occurrence_authority,
                gate,
                outcome,
            )
            if failure_count_changed:
                save_state(
                    task_dir,
                    task_dir / "state.sqlite",
                    state,
                    "orchestrate_gate_recovery",
                    (
                        f"{occurrence_authority['wave_ref']}/{occurrence_authority['phase_ref']} "
                        f"{gate}: automatic failure count {failure_count}"
                    ),
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
            if outcome == "blocked":
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
                "occurrence_key": occurrence_authority["occurrence_key"],
                "wave_ref": occurrence_authority["wave_ref"],
                "phase_ref": occurrence_authority["phase_ref"],
                "assignment_lineage_digest": occurrence_authority["assignment_lineage_digest"],
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
                    if gate == "governance_close":
                        closure_lineages: list[tuple[str, str]] = []
                        for prior in state.get("attempts") or []:
                            if not isinstance(prior, dict) or prior.get("gate") != "governance_close":
                                continue
                            prior_result = attempt_protocol.get_attempt_result(
                                root,
                                task_id=str(state["task_id"]),
                                attempt_id=str(prior.get("attempt_id") or ""),
                            )
                            metadata = (
                                prior_result.get("metadata")
                                if isinstance(prior_result, dict)
                                and isinstance(prior_result.get("metadata"), dict)
                                else {}
                            )
                            closure = (
                                metadata.get("governance_closure")
                                if isinstance(metadata.get("governance_closure"), dict)
                                else {}
                            )
                            digest = str(closure.get("closure_basis_digest") or "")
                            if prior_result and prior_result.get("status") == "blocked" and digest:
                                closure_lineages.append((
                                    str(prior.get("logical_delegation_key") or ""),
                                    digest,
                                ))
                        exhausted_lineage = next(
                            (
                                lineage for lineage in closure_lineages
                                if lineage[0] and closure_lineages.count(lineage) > 1
                            ),
                            None,
                        )
                        if exhausted_lineage is not None:
                            logical_key, basis_digest = exhausted_lineage
                            fingerprint = digest_text(canonical_json.dumps({
                                "kind": "governance_closure_identical_basis_retry_exhausted",
                                "logical_delegation_key": logical_key,
                                "closure_basis_digest": basis_digest,
                            }))
                            receipts = [
                                item for item in state.get("terminal_recovery_breakers") or []
                                if isinstance(item, dict)
                            ]
                            receipt = next(
                                (item for item in receipts if item.get("fingerprint") == fingerprint),
                                None,
                            )
                            state_mutated = receipt is None
                            if receipt is None:
                                receipt = {
                                    "schema": "cortex/terminal-recovery-breaker/v1",
                                    "kind": "governance_closure_identical_basis_retry_exhausted",
                                    "fingerprint": fingerprint,
                                    "logical_delegation_key": logical_key,
                                    "closure_basis_digest": basis_digest,
                                    "failure_count": 2,
                                    "created_at": now(),
                                }
                                state["terminal_recovery_breakers"] = [*receipts, receipt]
                            state["status"] = "blocked"
                            state["blocked_reason"] = "governance_closure_identical_basis_retry_exhausted"
                            if state_mutated:
                                save_state(
                                    task_dir,
                                    task_dir / "state.sqlite",
                                    state,
                                    "governance_closure_retry_exhausted",
                                    "stopped after one identical-basis replacement",
                                )
                            return _orchestrate_response(
                                "continue",
                                state,
                                wave_id=requested_wave_id,
                                result={"recovery": {
                                    "mode": "governance_closure_identical_basis_retry_exhausted",
                                    "fingerprint": fingerprint,
                                    "retryable": False,
                                    "state_mutated": state_mutated,
                                }},
                                plan=plan,
                            )
                    appended = _append_closure_rework(
                        params,
                        source_result_refs=source_result_refs,
                    )
                    state = appended["state_record"]
                    plan = appended["plan_record"]
                    target_gate = str(appended["target_gate"])
                    corrective = {
                        "schema": "cortex/corrective-dispatch/v1",
                        "mode": "closure_unresolved",
                        "task_id": str(state["task_id"]),
                        "origin_gate": gate,
                        "target_gate": target_gate,
                        "source_attempt_ids": unresolved_attempt_ids,
                        "source_result_refs": source_result_refs,
                        "replacement_worker_authorized": False,
                        "rework_receipt": appended["rework_receipt"],
                    }
                    appended_attempt_ids = [
                        str(item.get("attempt_id") or "")
                        for item in state.get("attempts") or []
                        if isinstance(item, dict)
                        and not item.get("invalidated")
                        and str(item.get("wave_ref") or "") == str(appended.get("wave_id") or "")
                        and str(item.get("attempt_id") or "")
                    ]
                    _checkpoint_orchestrate_transaction(
                        transaction_path,
                        transaction,
                        "next_wave_prepared",
                        wave_id=appended.get("wave_id"),
                        attempt_ids=appended_attempt_ids,
                    )
                    return _orchestrate_response(
                        "continue",
                        state,
                        wave_id=appended.get("wave_id"),
                        spawn_requests=appended.get("spawn_requests") or [],
                        result={"corrective_dispatch": corrective},
                        plan=plan,
                    ) | {
                        "next_action": (
                            "Invoke only the returned native corrective dispatch request(s) from this "
                            "cortex/orchestration/v11 response for the same task, wait for those exact "
                            "workers, read each canonical AttemptResult, then call continue_orchestration "
                            "with only the server-returned result ref(s)."
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
                    occurrence_authority,
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
                    occurrence_gate_key = _occurrence_gate_key(occurrence_authority, gate)
                    no_progress_pauses[occurrence_gate_key] = pause
                    newly_paused_occurrence_keys.append(occurrence_gate_key)
        # Occurrence rework is compiled only after the exact current
        # occurrence has been reduced and durably completed below. Until that
        # boundary, the pending steer is evidence, never permission to mutate
        # current attempt identity.
        semantic_rework = False
        if newly_paused_occurrence_keys and not semantic_rework:
            # Retain every failed gate exactly as recorded, but never turn a
            # local retry circuit breaker into a task-wide stop. Repeated
            # failures are advisory evidence and the chosen gate is retried
            # automatically through the normal dispatch path. Do not leave a
            # live pause in active_gates: that would silently strand the
            # selected route until an unrelated resume call.
            paused_gates = sorted({
                str(no_progress_pauses[key].get("gate") or "")
                for key in newly_paused_occurrence_keys
                if key in no_progress_pauses
            })
            state.setdefault("pipeline_advice", []).append({
                "code": "no_progress_recovery_recommended",
                "severity": "warning",
                "gates": paused_gates,
                "message": "Repeated failure evidence was recorded; retry or delegate the selected gate.",
                "recommended_next": "retry_or_delegate_selected_gate",
            })
            for key in newly_paused_occurrence_keys:
                no_progress_pauses.pop(key, None)
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
                    "paused no-progress retries for gate(s) " + ", ".join(paused_gates)
                    + "; other executable gates remain active"
                ),
            )
        original_gates = list(current_wave.get("gates", []))
        occurrence_attempts = _wave_occurrence_reduction_attempts(
            state,
            current_wave,
            submitted_attempt_ids=provided_attempt_ids,
        )
        all_occurrence_gates_passed = all(
            _occurrence_gate_passed(
                state,
                occurrence_authority,
                gate,
                [item for item in occurrence_attempts if item.get("gate") == gate],
            )
            for gate in original_gates
        )
        current_wave["status"] = "completed" if all_occurrence_gates_passed else ("blocked" if state.get("status") == "blocked" else "active")
        if all_occurrence_gates_passed:
            _complete_orchestration_wave_occurrence(state, plan, current_wave)
        _write_orchestrate_plan(task_dir, plan)
        if all_occurrence_gates_passed:
            # The completed occurrence is continuation authority for final
            # audit and handoff. Persist it before either path reloads state;
            # otherwise an exact terminal result is mistaken for no closure.
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "orchestration_wave_occurrence_completed",
                occurrence_authority["occurrence_key"],
            )
            state, semantic_rework = _apply_pending_revision_impact(
                params,
                task_dir,
                state,
                plan,
                current_wave,
                transaction_path=transaction_path,
                transaction=transaction,
            )
        _checkpoint_orchestrate_transaction(transaction_path, transaction, "gates_recorded", gates=original_gates)
        if state.get("status") == "completed":
            audited = _finalize_completed_lifecycle(params, task_dir, state)
            return _orchestrate_response("continue", audited["state"], wave_id=requested_wave_id, result={"result_count": audited["result_count"]}, plan=plan)
        if state.get("status") == "blocked":
            # A terminal worker result is recoverable orchestration evidence,
            # not a terminal state for Cortex itself.  Derive the corrective
            # route on the server immediately; the coordinator must never
            # manufacture a successor pipeline or issue a second, replacement
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
                    "continue",
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
                "continue", state, wave_id=requested_wave_id,
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
                    "continue", state, wave_id=prepared["wave_id"],
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
                "continue", state, wave_id=requested_wave_id,
                result={"recovery": dict(state["diagnostic_recovery"])}, plan=plan,
            )
        review = _hold_for_plan_approval(task_dir, state, plan)
        if review is not None:
            return _orchestrate_response(
                "continue", state, wave_id=requested_wave_id,
                result={"plan_review": review}, plan=plan,
            )
        prepared = _prepare_orchestrate_wave(params, task_dir, state, plan)
        _checkpoint_orchestrate_transaction(transaction_path, transaction, "next_wave_prepared", wave_id=prepared["wave_id"], attempt_ids=prepared["attempt_ids"])
        return _orchestrate_response(
            "continue",
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
    wave, frontier_attempts = _effective_plan_frontier(plan, state)
    active = set(wave.get("gates") or []) if isinstance(wave, dict) else set()
    slots = {
        str(attempt.get("attempt_id") or ""): index
        for index, attempt in enumerate(frontier_attempts, 1)
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
        _record_technical_recovery_breaker(
            state,
            attempt,
            failure_kind="stopped_result_unusable",
        )
        db_put_worker_session(root, {
            "task_id": state["task_id"],
            "attempt_id": attempt_id,
            "host_agent_id": attempt.get("worker_host_thread_id"),
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
        if attempt.get("invalidated"):
            continue
        status_value = str(attempt.get("status") or "")
        stop = attempt.get("native_terminal_stop")
        awaiting_result_stop = (
            bool(str(attempt.get("attempt_result_ref") or ""))
            and status_value in {RESULT_READY, "blocked", "failed"}
            and not (isinstance(stop, Mapping) and stop.get("observed") is True)
        )
        if status_value not in {AWAITING_HOST_SPAWN, "running"} and not awaiting_result_stop:
            continue
        if attempt.get("lifecycle_status") == "paused_awaiting_user":
            continue
        if awaiting_result_stop:
            raw_expiry = str(
                attempt.get("result_ready_at")
                or attempt.get("finalized_at")
                or attempt.get("work_completed_at")
                or ""
            ).strip()
            grace = timedelta(seconds=_NATIVE_STOP_RESULT_GRACE_SECONDS)
        else:
            expiry_field = (
                "spawn_lease_expires_at" if status_value == AWAITING_HOST_SPAWN
                else "worker_lease_expires_at"
            )
            raw_expiry = str(attempt.get(expiry_field) or "").strip()
            grace = timedelta(0)
        if not raw_expiry:
            continue
        try:
            expiry = datetime.fromisoformat(raw_expiry).astimezone(timezone.utc) + grace
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
        result_ref = str(attempt.get("attempt_result_ref") or "")
        completion_observation_expired = bool(result_ref)
        attempt["status"] = "failed"
        attempt["lifecycle_status"] = "needs_recovery"
        attempt["orphaned_at"] = stopped_at
        attempt["host_stopped_at"] = stopped_at
        failure_kind = (
            "native_completion_observation_expired"
            if completion_observation_expired else "lifecycle_lease_expired"
        )
        attempt["host_stop_outcome"] = failure_kind
        attempt["finalization_reason"] = failure_kind
        attempt["host_resumable"] = False
        _record_technical_recovery_breaker(
            state,
            attempt,
            failure_kind=failure_kind,
            attempt_result_ref=result_ref,
        )
        if completion_observation_expired:
            logical_key = str(attempt.get("logical_delegation_key") or "")
            assignment_digest = str(attempt.get("assignment_lineage_digest") or "")
            plan_assignment_digest = str(attempt.get("plan_assignment_lineage_digest") or "")
            failure_fingerprint = digest_text(canonical_json.dumps({
                "logical_delegation_key": logical_key,
                "assignment_lineage_digest": assignment_digest,
                "plan_assignment_lineage_digest": plan_assignment_digest,
                "attempt_result_ref": result_ref,
                "reason": failure_kind,
            }))
            failures = [
                item for item in state.get("native_stop_failures") or []
                if isinstance(item, Mapping)
            ]
            if not any(str(item.get("failure_fingerprint") or "") == failure_fingerprint for item in failures):
                failures.append({
                    "attempt_id": attempt_id,
                    "failure_fingerprint": failure_fingerprint,
                    "logical_delegation_key": logical_key,
                    "assignment_lineage_digest": assignment_digest,
                    "plan_assignment_lineage_digest": plan_assignment_digest,
                    "wave_ref": str(attempt.get("wave_ref") or ""),
                    "phase_ref": str(attempt.get("phase_ref") or ""),
                    "reason": failure_kind,
                    "recorded_at": stopped_at,
                })
                state["native_stop_failures"] = failures[-32:]
        db_put_worker_session(root, {
            "task_id": state["task_id"],
            "attempt_id": attempt_id,
            "host_agent_id": attempt.get("worker_host_thread_id"),
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
            from cortex_runtime.native_lifecycle_observer import reconcile_native_stop_inbox

            reconcile_native_stop_inbox(root, task_dir, state)
            plan = _load_orchestrate_plan(task_dir, state)
            terminal_closure = _terminal_governance_closure_breaker(state)
            if terminal_closure is not None:
                return _orchestrate_response(
                    "inspect",
                    state,
                    wave_id=None,
                    spawn_requests=[],
                    result={
                        "terminal_governance_closure": {
                            "status": "blocked",
                            "accepted": True,
                            "retryable": False,
                            "state_mutated": False,
                            "fingerprint": str(terminal_closure.get("fingerprint") or ""),
                        },
                    },
                    plan=plan,
                )
            expired_attempt_ids = _expire_lifecycle_attempts(root, task_dir, state)
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
    current_wave, current_frontier_attempts = _effective_plan_frontier(plan, state)
    current_frontier_ids = {
        str(item.get("attempt_id") or "") for item in current_frontier_attempts
    }
    spawn_requests = [
        {**_rehydrate_dispatch_spawn_request(task_dir, task, item), "attempt_id": item["attempt_id"]}
        for item in state.get("attempts", [])
        if item.get("status") == AWAITING_HOST_SPAWN
        and str(item.get("attempt_id") or "") in current_frontier_ids
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


def _reliability_recovery_source(
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
    breaker: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return the exact current assignment bound to one technical breaker."""
    if str(breaker.get("kind") or "") in {
        "governance_closure_identical_basis_retry_exhausted",
        "governance_closure_terminal_blocked",
    }:
        return None
    try:
        _wave, current_attempts = _effective_plan_frontier(plan, state)
    except ValueError:
        return None
    matches = [
        attempt for attempt in current_attempts
        if isinstance(attempt, dict)
        and not attempt.get("invalidated")
        and str(attempt.get("logical_delegation_key") or "")
        == str(breaker.get("logical_delegation_key") or "")
        and str(attempt.get("assignment_lineage_digest") or "")
        == str(breaker.get("assignment_lineage_digest") or "")
        and str(attempt.get("plan_assignment_lineage_digest") or "")
        == str(breaker.get("plan_assignment_lineage_digest") or "")
    ]
    return matches[0] if len(matches) == 1 else None


def _reliability_recovery_is_exactly_exhausted(
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
    breaker: Mapping[str, Any],
) -> bool:
    """Return true only after Sol on the universal compatible profile failed."""
    source = _reliability_recovery_source(state, plan, breaker)
    if source is None:
        return False
    try:
        return reliability_recovery_target(
            source, PROFILES, MODEL_EFFORTS, MODEL_RECOMMENDED_EFFORTS,
        ) is None
    except ValueError:
        return False


def _current_product_rework_attempt(
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return one exact current canonical product defect, never prose inference."""
    try:
        _wave, current_attempts = _effective_plan_frontier(plan, state)
    except ValueError:
        return None
    return next((
        attempt for attempt in current_attempts
        if isinstance(attempt, dict)
        and isinstance(attempt.get("acceptance_evaluation"), Mapping)
        and requires_product_rework(attempt["acceptance_evaluation"])
        and str(attempt.get("attempt_result_ref") or "")
    ), None)


def _same_child_deficit_repair_target(
    task_dir: Path,
    state: Mapping[str, Any],
    source: Mapping[str, Any],
    breaker: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return the original route only for one supplementable stopped result."""
    evaluation = source.get("acceptance_evaluation")
    if not isinstance(evaluation, Mapping):
        return None
    reasons = {str(item) for item in evaluation.get("reasons") or []}
    supplementable = {
        "verification_evidence_required",
        "closure_verification_obligations_incomplete",
    }
    missing = {
        str(item) for item in evaluation.get("missing_verification_kinds") or []
        if str(item)
    }
    recovery_receipts = [
        item for item in state.get("reliability_recovery_receipts") or []
        if isinstance(item, Mapping)
    ]
    prior_replacement = next((
        item for item in reversed(recovery_receipts)
        if item.get("same_child_deficit_repair") is not True
        and str(item.get("replacement_plan_assignment_lineage_digest") or "")
        == str(source.get("plan_assignment_lineage_digest") or "")
    ), None)
    preferred = source
    if isinstance(prior_replacement, Mapping):
        original = next((
            item for item in state.get("attempts") or []
            if isinstance(item, Mapping)
            and str(item.get("logical_delegation_key") or "")
            == str(prior_replacement.get("logical_delegation_key") or "")
            and str(item.get("plan_assignment_lineage_digest") or "")
            == str(prior_replacement.get("plan_assignment_lineage_digest") or "")
        ), None)
        if isinstance(original, Mapping):
            preferred = original
    result_ref = str(preferred.get("attempt_result_ref") or "")
    stop = preferred.get("native_terminal_stop")
    model = str(preferred.get("selected_model") or preferred.get("model") or "")
    effort = str(
        preferred.get("selected_reasoning_effort") or preferred.get("reasoning_effort") or ""
    )
    profile = str(preferred.get("profile") or preferred.get("agent") or "")
    operation_kind = str(source.get("operation_kind") or "")
    spawn_request = preferred.get("spawn_request")
    host_thread_id = str(preferred.get("worker_host_thread_id") or "")
    host_task_name = str(
        spawn_request.get("task_name") if isinstance(spawn_request, Mapping) else ""
    )
    if (
        not result_ref
        or not reasons
        or not reasons.issubset(supplementable)
        or not missing
        or source.get("same_child_deficit_repair_consumed") is True
        or preferred.get("same_child_deficit_repair_consumed") is True
        or not isinstance(stop, Mapping)
        or stop.get("observed") is not True
        or str(stop.get("result_digest") or "") != digest_text(result_ref)
        or not host_thread_id
        or not host_task_name
        or model not in MODEL_EFFORTS
        or effort not in set(MODEL_EFFORTS.get(model) or [])
        or profile not in PROFILES
        or operation_kind not in set(PROFILES[profile].get("operation_kinds") or [])
    ):
        return None
    root = _ledger_root_for_artifact(task_dir)
    canonical = attempt_protocol.get_attempt_result(
        root,
        task_id=str(state.get("task_id") or ""),
        attempt_id=str(preferred.get("attempt_id") or ""),
    )
    if not isinstance(canonical, Mapping) or str(canonical.get("result_ref") or "") != result_ref:
        return None
    prior = [
        item for item in state.get("reliability_recovery_receipts") or []
        if isinstance(item, Mapping)
        and item.get("same_child_deficit_repair") is True
        and str(item.get("plan_assignment_lineage_digest") or "")
        in {
            str(source.get("plan_assignment_lineage_digest") or ""),
            str(preferred.get("plan_assignment_lineage_digest") or ""),
        }
    ]
    if prior:
        return None
    sessions = [
        item for item in ledger_db.list_worker_sessions(
            root, str(state.get("task_id") or "")
        )
        if str(item.get("attempt_id") or "") == str(preferred.get("attempt_id") or "")
        and str(item.get("host_agent_id") or "") == host_thread_id
        and str(item.get("host_task_name") or "") in {"", host_task_name}
    ]
    if len(sessions) != 1:
        return None
    return {
        "model": model,
        "reasoning_effort": effort,
        "profile": profile,
        "stage": "same_child_deficit_repair",
        "effort_resolution_reason": "preserved_for_same_child_deficit_repair",
        "same_child_source_attempt_id": str(preferred.get("attempt_id") or ""),
        "recovery_chain_result_refs": list(dict.fromkeys([
            str(preferred.get("attempt_result_ref") or ""),
            str(source.get("attempt_result_ref") or ""),
        ])),
    }


def _bind_prepared_same_child_deficit_repair(
    task_dir: Path,
    state: dict[str, Any],
    receipt: Mapping[str, Any],
    replacement: dict[str, Any],
    spawn_request: dict[str, Any],
) -> None:
    """Transfer one private native binding to an append-only repair attempt."""
    if receipt.get("same_child_deficit_repair") is not True:
        return
    source_id = str(
        receipt.get("same_child_source_attempt_id")
        or receipt.get("source_attempt_id")
        or ""
    )
    source = next((
        item for item in state.get("attempts") or []
        if isinstance(item, dict) and str(item.get("attempt_id") or "") == source_id
    ), None)
    if not isinstance(source, dict) or not source.get("invalidated"):
        raise ValueError("same-child deficit repair source is not durably retired")
    if (
        str(replacement.get("plan_assignment_lineage_digest") or "")
        != str(receipt.get("replacement_plan_assignment_lineage_digest") or "")
    ):
        raise ValueError("same-child deficit repair replacement lineage changed")
    host_thread_id = str(source.get("worker_host_thread_id") or "")
    source_spawn = source.get("spawn_request")
    host_task_name = str(
        source_spawn.get("task_name") if isinstance(source_spawn, Mapping) else ""
    )
    if not host_thread_id or not host_task_name:
        raise ValueError("same-child deficit repair lost its native target")
    root = _ledger_root_for_artifact(task_dir)
    source_sessions = [
        item for item in ledger_db.list_worker_sessions(root, str(state["task_id"]))
        if str(item.get("attempt_id") or "") == source_id
        and str(item.get("host_agent_id") or "") == host_thread_id
    ]
    if len(source_sessions) != 1:
        raise ValueError("same-child deficit repair source session is ambiguous")
    source_session = source_sessions[0]
    next_generation = max(
        int(source.get("worker_host_session_generation") or 1),
        int(source_session.get("generation") or 1),
    ) + 1
    source["same_child_deficit_repair_consumed"] = True
    source["retired_worker_host_thread_digest"] = digest_text(host_thread_id)
    source.pop("worker_host_thread_id", None)
    source["host_resumable"] = False
    ledger_db.put_worker_session(root, {
        **source_session,
        "host_agent_id": None,
        "status": "completed",
        "resumable": False,
        "terminated_at": source_session.get("terminated_at") or now(),
    })
    replacement.update({
        "worker_host_thread_id": host_thread_id,
        "worker_host_start_turn_id": str(source.get("worker_host_start_turn_id") or ""),
        "worker_host_start_observed": True,
        "worker_host_model_attested": True,
        "worker_host_session_generation": next_generation,
        "same_child_deficit_repair_consumed": True,
    })
    spawn_request.update({
        "native_call": "followup_task",
        "followup_target": host_task_name,
    })
    durable_spawn = replacement.get("spawn_request")
    if not isinstance(durable_spawn, dict):
        raise ValueError("same-child deficit repair spawn request is unavailable")
    durable_spawn.update({
        "native_call": "followup_task",
        "followup_target": host_task_name,
        "task_name": host_task_name,
    })
    package = _delegation_package(
        task_dir, str(state["task_id"]), str(replacement["attempt_id"]),
    )
    package["spawn_request"] = dict(durable_spawn)
    package["same_child_deficit_repair_consumed"] = True
    _write_delegation_package(
        task_dir, str(state["task_id"]), str(replacement["attempt_id"]), package,
    )
    ledger_db.put_worker_session(root, {
        "task_id": str(state["task_id"]),
        "attempt_id": str(replacement["attempt_id"]),
        "generation": next_generation,
        "host_agent_id": host_thread_id,
        "host_task_name": host_task_name,
        "host_tool": "followup_task",
        "status": "idle_resumable",
        "resumable": True,
        "started_at": source_session.get("started_at") or now(),
    })


def _consume_pending_host_epoch_session_retirements(
    root: Path,
    state: dict[str, Any],
    *,
    status: str,
    recovery_batch_key: str,
) -> None:
    """Finalize prior-host sessions inside the recovery commit transaction."""
    pending = list(state.pop("pending_native_host_epoch_session_retirements", []) or [])
    for item in pending:
        ledger_db.put_worker_session(root, {
            "session_id": str(item.get("session_id") or ""),
            "task_id": str(state["task_id"]),
            "attempt_id": str(item.get("attempt_id") or ""),
            "generation": int(item["generation"]),
            # Preserve the exact private binding so a delayed old-host event
            # can never reuse this child identity for a successor attempt.
            "host_agent_id": item.get("host_agent_id"),
            "host_task_name": str(item.get("host_task_name") or ""),
            "host_tool": str(item.get("host_tool") or "spawn_agent"),
            "status": "terminated_unavailable",
            "resumable": False,
            "started_at": item.get("started_at"),
            "terminated_at": now(),
        })
    handoffs = state.get("native_host_epoch_handoffs") or []
    if not handoffs or not isinstance(handoffs[-1], dict):
        raise ValueError("native host epoch handoff receipt is unavailable")
    handoffs[-1]["status"] = status
    handoffs[-1]["recovery_batch_key"] = recovery_batch_key
    _write_immutable_host_epoch_handoff_audit(
        root, state, handoffs[-1], stage="final-" + safe_id(status),
    )


def _write_immutable_host_epoch_handoff_audit(
    root: Path,
    state: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    stage: str,
) -> None:
    """Persist one immutable handoff audit event outside bounded task state."""
    digest = str(receipt.get("digest") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("native host epoch handoff digest is invalid")
    normalized_stage = safe_id(stage)
    key = f"native-host-epoch-handoff:{digest}:{normalized_stage}"
    payload = {**dict(receipt), "audit_stage": normalized_stage}
    task_id = str(state.get("task_id") or "")
    existing = ledger_db.get_task_document(root, task_id, key)
    if existing is not None:
        if canonical_json.dumps(existing) != canonical_json.dumps(payload):
            raise ValueError("native host epoch handoff audit replay conflicts")
        return
    ledger_db.put_task_document(root, task_id, key, payload)


def _append_host_epoch_handoff_projection(
    root: Path,
    state: dict[str, Any],
    receipt: dict[str, Any],
) -> None:
    """Keep bounded recent state while retaining an immutable normalized audit."""
    _write_immutable_host_epoch_handoff_audit(root, state, receipt, stage="prepared")
    recent = [
        item for item in state.get("native_host_epoch_handoffs") or []
        if isinstance(item, Mapping)
    ]
    state["native_host_epoch_handoffs"] = [*recent[-31:], receipt]


def _dispatch_server_owned_recovery(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    reason: str,
    transaction_path: Path | None = None,
    transaction: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Classify and replace every exact technical slot before one dispatch."""
    breakers = _active_terminal_recovery_breakers(state, plan)
    if breakers:
        terminal_kinds = {
            "governance_closure_identical_basis_retry_exhausted",
            "governance_closure_terminal_blocked",
        }
        terminal_governance = next((
            item for item in breakers
            if str(item.get("kind") or "") in terminal_kinds
        ), None)
        if terminal_governance is not None:
            return state, plan, {
                "wave_id": None, "spawn_requests": [], "attempt_ids": [], "state": state,
            }, {
                "schema": "cortex/recovery-contract/v1",
                "mode": "terminal_reliability_breaker",
                "status": "terminal",
                "retryable": False,
                "state_mutated": False,
                "fingerprint": str(terminal_governance.get("fingerprint") or ""),
            }

        batch_basis = [
            {
                "failure_fingerprint": str(item.get("fingerprint") or ""),
                "logical_delegation_key": str(item.get("logical_delegation_key") or ""),
                "assignment_lineage_digest": str(item.get("assignment_lineage_digest") or ""),
                "plan_assignment_lineage_digest": str(
                    item.get("plan_assignment_lineage_digest") or ""
                ),
            }
            for item in breakers
        ]
        if any(not all(item.values()) for item in batch_basis):
            raise ValueError("technical recovery batch lacks exact occurrence identity")
        batch_wave_refs = {
            str(item.get("wave_ref") or "") for item in breakers
            if str(item.get("wave_ref") or "")
        }
        if len(batch_wave_refs) != 1:
            raise ValueError("technical recovery batch crosses compiled wave identity")
        batch_wave_ref = next(iter(batch_wave_refs))
        batch_key = "recovery-batch-" + digest_text(
            canonical_json.dumps(batch_basis)
        )[:24]

        # Derive every source and every target before mutating even a working
        # copy.  This is the atomicity fence for parallel slots: one exhausted
        # or ambiguous occurrence prevents all replacements and all dispatch.
        classified: list[tuple[Mapping[str, Any], dict[str, Any], dict[str, Any] | None]] = []
        for breaker in breakers:
            source = _reliability_recovery_source(state, plan, breaker)
            if source is None:
                raise ValueError("technical recovery source occurrence is unavailable")
            try:
                target = _same_child_deficit_repair_target(
                    task_dir, state, source, breaker,
                ) or reliability_recovery_target(
                    source, PROFILES, MODEL_EFFORTS, MODEL_RECOMMENDED_EFFORTS,
                )
            except ValueError as exc:
                raise ValueError("technical recovery target is invalid") from exc
            classified.append((breaker, source, target))
        exhausted = [breaker for breaker, _source, target in classified if target is None]
        if exhausted:
            existing_batch = next((
                item for item in state.get("reliability_recovery_batches") or []
                if isinstance(item, Mapping)
                and str(item.get("batch_key") or "") == batch_key
                and str(item.get("status") or "") == "terminal_blocked"
            ), None)
            if isinstance(existing_batch, Mapping):
                return state, plan, {
                    "wave_id": None, "spawn_requests": [], "attempt_ids": [], "state": state,
                }, {
                    "schema": "cortex/recovery-contract/v1",
                    "mode": "terminal_reliability_batch",
                    "batch_key": batch_key,
                    "status": "terminal",
                    "retryable": False,
                    "state_mutated": False,
                    "failure_fingerprints": list(
                        existing_batch.get("source_failure_fingerprints") or []
                    ),
                    "pending_product_rework_count": int(
                        existing_batch.get("pending_product_rework_count") or 0
                    ),
                }
            state["status"] = "terminal_blocked"
            state["blocked_reason"] = "technical_reliability_terminal_blocked"
            terminal_receipts = [
                item for item in state.get("technical_reliability_terminal_receipts") or []
                if isinstance(item, Mapping)
            ]
            known = {
                str(item.get("failure_fingerprint") or "") for item in terminal_receipts
            }
            for breaker in exhausted:
                fingerprint = str(breaker.get("fingerprint") or "")
                if fingerprint in known:
                    continue
                terminal_receipts.append({
                    "schema": "cortex/reliability-recovery-terminal/v1",
                    "failure_fingerprint": fingerprint,
                    "logical_delegation_key": str(
                        breaker.get("logical_delegation_key") or ""
                    ),
                    "plan_assignment_lineage_digest": str(
                        breaker.get("plan_assignment_lineage_digest") or ""
                    ),
                    "status": "terminal_blocked",
                    "recorded_at": now(),
                })
                known.add(fingerprint)
            state["technical_reliability_terminal_receipts"] = terminal_receipts[-32:]
            batch_receipt = {
                "schema": "cortex/reliability-recovery-batch/v1",
                "batch_key": batch_key,
                "status": "terminal_blocked",
                "wave_id": batch_wave_ref,
                "source_failure_fingerprints": [
                    str(item.get("fingerprint") or "") for item in breakers
                ],
                "exhausted_failure_fingerprints": [
                    str(item.get("fingerprint") or "") for item in exhausted
                ],
                "withheld_recoverable_failure_fingerprints": [
                    str(breaker.get("fingerprint") or "")
                    for breaker, _source, target in classified
                    if target is not None
                ],
                "pending_product_rework_count": len(
                    [
                        item for item in state.get("pending_product_reworks") or []
                        if isinstance(item, Mapping)
                    ]
                ),
                "attempt_ids": [],
                "recorded_at": now(),
            }
            batches = [
                item for item in state.get("reliability_recovery_batches") or []
                if isinstance(item, Mapping)
                and str(item.get("batch_key") or "") != batch_key
            ]
            state["reliability_recovery_batches"] = [*batches[-31:], batch_receipt]
            root = _ledger_root_for_artifact(task_dir)
            with ledger_db.transaction(root):
                _consume_pending_host_epoch_session_retirements(
                    root, state, status="terminal_blocked", recovery_batch_key=batch_key,
                )
                if transaction_path is not None and isinstance(transaction, dict):
                    _checkpoint_orchestrate_transaction(
                        transaction_path,
                        transaction,
                        "technical_recovery_terminal",
                        recovery_batch_key=batch_key,
                        wave_id=batch_receipt["wave_id"],
                        attempt_ids=[],
                    )
                save_state(
                    task_dir, task_dir / "state.sqlite", state,
                    "technical_reliability_terminal_blocked",
                    "bounded recovery exhausted for one or more exact assignment occurrences",
                )
            return state, plan, {
                "wave_id": None, "spawn_requests": [], "attempt_ids": [], "state": state,
            }, {
                "schema": "cortex/recovery-contract/v1",
                "mode": "terminal_reliability_batch",
                "batch_key": batch_key,
                "status": "terminal",
                "retryable": False,
                "state_mutated": True,
                "failure_fingerprints": [
                    str(item.get("fingerprint") or "") for item in breakers
                ],
                "pending_product_rework_count": batch_receipt[
                    "pending_product_rework_count"
                ],
            }
        working_state = json.loads(json.dumps(state, ensure_ascii=False))
        working_plan = json.loads(json.dumps(plan, ensure_ascii=False))
        recovery_receipts: list[dict[str, Any]] = []
        expected_targets = {
            str(breaker.get("fingerprint") or ""): target
            for breaker, _source, target in classified
            if target is not None
        }
        for breaker, _source, _target in classified:
            replaced = _replace_reliability_recovery_assignment(
                params, task_dir, working_state, working_plan,
                breaker=breaker, reason=reason, defer_commit=True,
                recovery_target=_target,
            )
            if replaced is None:
                raise ValueError("technical recovery batch replacement is unavailable")
            working_state, working_plan, _deferred, receipt = replaced
            expected = expected_targets[str(breaker.get("fingerprint") or "")]
            if (
                str(receipt.get("model") or "") != str(expected.get("model") or "")
                or str(receipt.get("reasoning_effort") or "")
                != str(expected.get("reasoning_effort") or "")
                or str(receipt.get("resolved_profile") or "")
                != str(expected.get("profile") or "")
                or str(receipt.get("recovery_stage") or "")
                != str(expected.get("stage") or "")
            ):
                raise ValueError("technical recovery replacement target changed after preflight")
            recovery_receipts.append(receipt)
        if len(recovery_receipts) != len(classified):
            raise ValueError("technical recovery batch did not transform every failed slot")
        if _active_terminal_recovery_breakers(working_state, working_plan):
            raise ValueError("technical recovery batch left an active source occurrence")
        replacement_lineages = [
            str(item.get("replacement_plan_assignment_lineage_digest") or "")
            for item in recovery_receipts
        ]
        if (
            any(not item for item in replacement_lineages)
            or len(set(replacement_lineages)) != len(replacement_lineages)
        ):
            raise ValueError("technical recovery batch replacement lineages are not unique")
        _sync_orchestration_wave_occurrences(working_state, working_plan)
        root = _ledger_root_for_artifact(task_dir)
        with ledger_db.transaction(root):
            revision = ledger_db.append_plan_revision(
                root,
                str(working_state["task_id"]),
                task_revision=int(working_state.get("task_revision") or 1),
                impact={
                    "classification": "reliability_recovery_batch",
                    "batch_key": batch_key,
                    "recoveries": recovery_receipts,
                },
                plan=working_plan,
                status="active",
            )
            working_plan["plan_revision"] = revision["plan_revision"]
            working_state["plan_revision"] = revision["plan_revision"]
            working_state["plan_digest"] = revision["plan_digest"]
            _write_orchestrate_plan(task_dir, working_plan, preserve_updated_at=True)
            save_state(
                task_dir, task_dir / "state.sqlite", working_state,
                "reliability_recovery_batch_replaced", redact(reason, 1000),
            )
            prepared = _prepare_orchestrate_wave_transaction(
                {
                    **params,
                    "task_id": working_state["task_id"],
                    "principal": working_state.get("principal"),
                },
                task_dir, working_state, working_plan,
            )
            prepared_state = prepared.get("state")
            if not isinstance(prepared_state, dict):
                raise ValueError("reliability recovery batch did not return durable state")
            working_state = prepared_state
            prepared_attempts = [
                _attempt(working_state, str(attempt_id))
                for attempt_id in prepared.get("attempt_ids") or []
            ]
            for receipt in recovery_receipts:
                if receipt.get("same_child_deficit_repair") is not True:
                    continue
                replacement_lineage = str(
                    receipt.get("replacement_plan_assignment_lineage_digest") or ""
                )
                matching_attempts = [
                    item for item in prepared_attempts
                    if str(item.get("plan_assignment_lineage_digest") or "")
                    == replacement_lineage
                ]
                matching_requests = [
                    item for item in prepared.get("spawn_requests") or []
                    if str(item.get("attempt_id") or "")
                    == str((matching_attempts[0] if matching_attempts else {}).get("attempt_id") or "")
                ]
                if len(matching_attempts) != 1 or len(matching_requests) != 1:
                    raise ValueError("prepared same-child deficit repair is unavailable")
                _bind_prepared_same_child_deficit_repair(
                    task_dir,
                    working_state,
                    receipt,
                    matching_attempts[0],
                    matching_requests[0],
                )
            replacement_set = set(replacement_lineages)
            prepared_replacement_lineages = [
                str(attempt.get("plan_assignment_lineage_digest") or "")
                for attempt in prepared_attempts
                if str(attempt.get("plan_assignment_lineage_digest") or "")
                in replacement_set
            ]
            if prepared_replacement_lineages != replacement_lineages:
                raise ValueError(
                    "prepared technical recovery frontier does not contain every replacement in slot order"
                )
            for receipt in recovery_receipts:
                receipt["status"] = "dispatched"
                recovery_occurrence_key = str(
                    receipt.get("recovery_occurrence_key") or ""
                )
                matching_receipts = [
                    item for item in working_state.get("reliability_recovery_receipts") or []
                    if isinstance(item, dict)
                    and str(item.get("recovery_occurrence_key") or "")
                    == recovery_occurrence_key
                ]
                if len(matching_receipts) != 1:
                    raise ValueError(
                        "prepared reliability recovery batch receipt is unavailable"
                    )
                matching_receipts[0]["status"] = "dispatched"
            batch_receipt = {
                "schema": "cortex/reliability-recovery-batch/v1",
                "batch_key": batch_key,
                "status": "dispatched",
                "wave_id": str(prepared.get("wave_id") or ""),
                "plan_revision": working_plan["plan_revision"],
                "source_failure_fingerprints": [
                    str(item.get("fingerprint") or "") for item in breakers
                ],
                "recovery_occurrence_keys": [
                    str(item.get("recovery_occurrence_key") or "")
                    for item in recovery_receipts
                ],
                "replacement_plan_assignment_lineage_digests": [
                    str(item.get("replacement_plan_assignment_lineage_digest") or "")
                    for item in recovery_receipts
                ],
                "attempt_ids": [str(item) for item in prepared.get("attempt_ids") or []],
                "pending_product_rework_count": len(
                    [
                        item for item in working_state.get("pending_product_reworks") or []
                        if isinstance(item, Mapping)
                    ]
                ),
                "recorded_at": now(),
            }
            batches = [
                item for item in working_state.get("reliability_recovery_batches") or []
                if isinstance(item, Mapping)
                and str(item.get("batch_key") or "") != batch_key
            ]
            working_state["reliability_recovery_batches"] = [
                *batches[-31:], batch_receipt,
            ]
            _consume_pending_host_epoch_session_retirements(
                root,
                working_state,
                status="replacement_dispatched",
                recovery_batch_key=batch_key,
            )
            if transaction_path is not None and isinstance(transaction, dict):
                _checkpoint_orchestrate_transaction(
                    transaction_path,
                    transaction,
                    "technical_recovery_prepared",
                    recovery_batch_key=batch_key,
                    wave_id=batch_receipt["wave_id"],
                    attempt_ids=batch_receipt["attempt_ids"],
                )
            save_state(
                task_dir, task_dir / "state.sqlite", working_state,
                "reliability_recovery_batch_dispatched",
                ",".join(str(item.get("recovery_occurrence_key") or "") for item in recovery_receipts),
            )
        return working_state, working_plan, prepared, {
            "schema": "cortex/recovery-contract/v1",
            "mode": "exact_assignment_recovery_batch",
            "batch_key": batch_key,
            "status": "dispatched",
            "retryable": True,
            "state_mutated": True,
            "recoveries": recovery_receipts,
            "pending_product_rework_count": len(
                [
                    item for item in working_state.get("pending_product_reworks") or []
                    if isinstance(item, Mapping)
                ]
            ),
        }
    # A recovery call without an exact active breaker has no authority to
    # replay the selected route.  Every technical source must first bind its
    # precise assignment occurrence; otherwise fail closed and let the caller
    # derive or record that authoritative source rather than dispatching the
    # same model/profile indefinitely.
    return None


def recover_native_dispatch_attestation_failure(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    attempt_id: str,
    dispatch_ref: str,
) -> dict[str, Any]:
    """Replace one never-delivered Luna dispatch through canonical recovery.

    The native serializer can discover host-default drift only after the
    engine has durably prepared its dispatch. That is a technical transport
    failure, not a terminal task result. Bind it to the exact occurrence,
    reuse the ordinary reliability ladder, and return the durable replacement
    frontier. Replaying the same failure returns that frontier without
    advancing the ladder again.
    """
    source = next((
        item for item in state.get("attempts") or []
        if isinstance(item, dict)
        and str(item.get("attempt_id") or "") == str(attempt_id)
        and str(item.get("dispatch_ref") or "") == str(dispatch_ref)
    ), None)
    if not isinstance(source, dict):
        raise ValueError("native dispatch attestation failure source is unavailable")
    identity = {
        "kind": "native_dispatch_attestation_failure",
        "logical_delegation_key": str(source.get("logical_delegation_key") or ""),
        "assignment_lineage_digest": str(source.get("assignment_lineage_digest") or ""),
        "plan_assignment_lineage_digest": str(source.get("plan_assignment_lineage_digest") or ""),
        "wave_ref": str(source.get("wave_ref") or ""),
        "phase_ref": str(source.get("phase_ref") or ""),
        "failure_class": "native_luna_default_attestation_unavailable",
    }
    if any(not value for value in identity.values()):
        raise ValueError("native dispatch attestation failure lacks exact occurrence identity")
    fingerprint = digest_text(canonical_json.dumps(identity))
    prior_recoveries = [
        item for item in state.get("reliability_recovery_receipts") or []
        if isinstance(item, Mapping)
    ]
    existing = next((
        item for item in prior_recoveries
        if str(item.get("failure_fingerprint") or "") == fingerprint
    ), None)
    if isinstance(existing, Mapping):
        task = load_task_definition(task_dir, state)
        current_wave, current_attempts = _effective_plan_frontier(plan, state)
        pending = [
            {
                **_rehydrate_dispatch_spawn_request(task_dir, task, attempt),
                "attempt_id": str(attempt.get("attempt_id") or ""),
            }
            for attempt in current_attempts
            if isinstance(attempt, dict)
            and not attempt.get("invalidated")
            and attempt.get("status") == AWAITING_HOST_SPAWN
            and attempt.get("dispatch_delivery_status") == "pending"
        ]
        return _orchestrate_response(
            "continue",
            state,
            wave_id=(current_wave or {}).get("wave_id"),
            spawn_requests=pending,
            result={"recovery": dict(existing), "idempotent": True},
            plan=plan,
        )
    if (
        source.get("invalidated")
        or source.get("status") != AWAITING_HOST_SPAWN
        or source.get("dispatch_delivery_status") != "pending"
        or str(source.get("selected_model") or source.get("expected_model") or "")
        != "gpt-5.6-luna"
    ):
        raise ValueError("native dispatch attestation failure source is not a pending Luna dispatch")
    breaker = {
        "schema": "cortex/terminal-recovery-breaker/v1",
        **identity,
        "fingerprint": fingerprint,
        "created_at": now(),
    }
    receipts = [
        item for item in state.get("terminal_recovery_breakers") or []
        if isinstance(item, Mapping)
    ]
    state["terminal_recovery_breakers"] = [*receipts[-31:], breaker]
    recovered = _dispatch_server_owned_recovery(
        params,
        task_dir,
        state,
        plan,
        reason=(
            "The never-delivered Luna dispatch lost exact host-default attestation; "
            "advance the same occurrence through server-owned reliability recovery."
        ),
    )
    if recovered is None:
        return _lifecycle_error(
            "continue",
            "native_dispatch_recovery_route_unavailable",
            "The exact native dispatch recovery route is not yet executable",
            phase="native_dispatch_attestation_failure",
            recoverable=True,
            next_lifecycle="continue",
            task_id=str(state.get("task_id") or ""),
        )
    recovered_state, recovered_plan, prepared, receipt = recovered
    return _orchestrate_response(
        "continue",
        recovered_state,
        wave_id=prepared.get("wave_id"),
        spawn_requests=prepared.get("spawn_requests") or [],
        result={"recovery": receipt},
        plan=recovered_plan,
    )


def _record_technical_recovery_breaker(
    state: dict[str, Any],
    attempt: Mapping[str, Any],
    *,
    failure_kind: str,
    attempt_result_ref: str = "",
) -> dict[str, Any]:
    """Bind one technical failure to its exact assignment occurrence."""
    if not str(failure_kind or "").strip():
        raise ValueError("technical recovery requires a failure kind")
    identity = {
        "kind": str(failure_kind),
        "logical_delegation_key": str(attempt.get("logical_delegation_key") or ""),
        "assignment_lineage_digest": str(attempt.get("assignment_lineage_digest") or ""),
        "plan_assignment_lineage_digest": str(attempt.get("plan_assignment_lineage_digest") or ""),
        "wave_ref": str(attempt.get("wave_ref") or ""),
        "phase_ref": str(attempt.get("phase_ref") or ""),
        "failure_class": "technical",
    }
    if any(not str(value).strip() for value in identity.values()):
        raise ValueError("technical recovery lacks exact assignment identity")
    if attempt_result_ref:
        identity["attempt_result_ref"] = str(attempt_result_ref)
    fingerprint = digest_text(canonical_json.dumps(identity))
    receipts = [
        item for item in state.get("terminal_recovery_breakers") or []
        if isinstance(item, Mapping)
    ]
    existing = next(
        (item for item in receipts if str(item.get("fingerprint") or "") == fingerprint),
        None,
    )
    if isinstance(existing, Mapping):
        return dict(existing)
    receipt = {
        "schema": "cortex/terminal-recovery-breaker/v1",
        **identity,
        "fingerprint": fingerprint,
        "created_at": now(),
    }
    state["terminal_recovery_breakers"] = [*receipts[-31:], receipt]
    return receipt


def resumed_host_epoch_recovery_required(
    task_dir: Path,
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> bool:
    """Return whether an authenticated dead-host handoff awaits reconciliation."""
    root = _ledger_root_for_artifact(task_dir)
    session_id = str(state.get("coordinator_host_thread_id") or "")
    epoch = ledger_db.get_native_host_epoch(root, session_id) if session_id else None
    bound_epoch = state.get("coordinator_native_host_epoch")
    wave, attempts = _effective_plan_frontier(dict(plan), dict(state))
    delivered_nonterminal = [
        item for item in attempts
        if isinstance(item, dict)
        and not item.get("invalidated")
        and str(item.get("dispatch_delivery_status") or "") == "delivered"
        and str(item.get("status") or "") in {AWAITING_HOST_SPAWN, "running", RESULT_READY, "waiting_question"}
        and not (
            isinstance(item.get("native_terminal_stop"), Mapping)
            and item["native_terminal_stop"].get("observed") is True
        )
    ]
    if not isinstance(epoch, Mapping) or isinstance(bound_epoch, bool) or not isinstance(bound_epoch, int):
        if delivered_nonterminal:
            raise ValueError(
                "active delivered workers predate authenticated host-epoch ownership; recovery is unavailable"
            )
        return False
    current_epoch = int(epoch.get("epoch") or 0)
    if current_epoch < 1 or bound_epoch < 1 or not str(epoch.get("fingerprint") or ""):
        raise ValueError("native host epoch binding is invalid")
    bound_fingerprint = str(state.get("coordinator_native_host_epoch_fingerprint") or "")
    if not bound_fingerprint.startswith("hmac-sha256:"):
        raise ValueError("task-bound native host epoch fingerprint is unavailable")
    if current_epoch < bound_epoch:
        raise ValueError("native host epoch regressed below the task-bound epoch")
    if current_epoch == bound_epoch:
        if not hmac.compare_digest(
            bound_fingerprint, str(epoch.get("fingerprint") or ""),
        ):
            raise ValueError("native host epoch fingerprint conflicts with the task binding")
        retired_questions = _retired_epoch_question_frontiers(task_dir, state, plan)
        if any(str(question.get("status") or "") == "answered" for _source, question in retired_questions):
            # The durable answer may have committed immediately before the
            # coordinator process exited. Resume must finish the already
            # authorized new-child transition even without another epoch bump.
            return True
        return False
    if str(epoch.get("transition") or "") != "proven_dead_host_handoff":
        raise ValueError("native host epoch transition is not an authenticated dead-host handoff")
    return True


def resumed_host_epoch_orphans(
    task_dir: Path,
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Select exact current-frontier children lost at a proven host handoff."""
    if not resumed_host_epoch_recovery_required(task_dir, state, plan):
        return []
    root = _ledger_root_for_artifact(task_dir)
    session_id = str(state.get("coordinator_host_thread_id") or "")
    epoch = ledger_db.get_native_host_epoch(root, session_id)
    bound_epoch = state.get("coordinator_native_host_epoch")
    wave, attempts = _effective_plan_frontier(dict(plan), dict(state))
    delivered_nonterminal = [
        item for item in attempts
        if isinstance(item, dict)
        and not item.get("invalidated")
        and str(item.get("dispatch_delivery_status") or "") == "delivered"
        and str(item.get("status") or "") in {AWAITING_HOST_SPAWN, "running", RESULT_READY, "waiting_question"}
        and not (
            isinstance(item.get("native_terminal_stop"), Mapping)
            and item["native_terminal_stop"].get("observed") is True
        )
    ]
    if not isinstance(epoch, Mapping) or not isinstance(bound_epoch, int) or isinstance(bound_epoch, bool):
        raise ValueError("authenticated host-epoch handoff is unavailable")
    current_epoch = int(epoch.get("epoch") or 0)
    if not isinstance(wave, Mapping):
        raise ValueError("native host handoff has no exact current frontier")
    wave_ref = str(wave.get("wave_ref") or "")
    phase_ref = str(wave.get("phase_ref") or "")
    stale: list[dict[str, Any]] = []
    for attempt in delivered_nonterminal:
        if not isinstance(attempt, dict) or attempt.get("invalidated"):
            continue
        # This assignment was already retired and made childless by an older
        # authenticated handoff. Carry its durable question as task state, but
        # never session-preflight or retire the old child a second time. Other
        # live slots in the same frontier are still selected below.
        if (
            attempt.get("native_host_epoch_question_retired") is True
            and str(attempt.get("status") or "") == "waiting_question"
            and not str(attempt.get("worker_host_thread_id") or "")
        ):
            continue
        delivery = str(attempt.get("dispatch_delivery_status") or "")
        status = str(attempt.get("status") or "")
        if delivery != "delivered" or status not in {AWAITING_HOST_SPAWN, "running", RESULT_READY, "waiting_question"}:
            continue
        attempt_epoch = attempt.get("native_host_epoch")
        if isinstance(attempt_epoch, bool) or not isinstance(attempt_epoch, int):
            raise ValueError("delivered current-frontier attempt has no authenticated host epoch")
        if attempt_epoch >= current_epoch:
            continue
        if (
            attempt_epoch != bound_epoch
            or not hmac.compare_digest(
                str(attempt.get("native_host_epoch_fingerprint") or ""),
                str(state.get("coordinator_native_host_epoch_fingerprint") or ""),
            )
            or str(attempt.get("wave_ref") or attempt.get("orchestration_wave_id") or "") != wave_ref
            or str(attempt.get("phase_ref") or "") != phase_ref
            or not str(attempt.get("logical_delegation_key") or "")
            or not str(attempt.get("assignment_lineage_digest") or "")
            or not str(attempt.get("plan_assignment_lineage_digest") or "")
        ):
            raise ValueError("native host handoff attempt identity is ambiguous")
        stale.append(attempt)
    return stale


def _retired_epoch_question_frontiers(
    task_dir: Path,
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Resolve every already-childless durable-question frontier exactly."""
    wave, attempts = _effective_plan_frontier(dict(plan), dict(state))
    if not isinstance(wave, Mapping):
        return []
    candidates = [
        item for item in attempts
        if isinstance(item, dict)
        and not item.get("invalidated")
        and item.get("native_host_epoch_question_retired") is True
        and str(item.get("status") or "") == "waiting_question"
        and not str(item.get("worker_host_thread_id") or "")
    ]
    if not candidates:
        return []
    root = _ledger_root_for_artifact(task_dir)
    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for attempt in sorted(candidates, key=lambda item: str(item.get("attempt_id") or "")):
        if (
            str(attempt.get("wave_ref") or attempt.get("orchestration_wave_id") or "")
            != str(wave.get("wave_ref") or "")
            or str(attempt.get("phase_ref") or "") != str(wave.get("phase_ref") or "")
            or not str(attempt.get("logical_delegation_key") or "")
            or not str(attempt.get("assignment_lineage_digest") or "")
            or not str(attempt.get("plan_assignment_lineage_digest") or "")
        ):
            raise ValueError("retired host question occurrence identity is ambiguous")
        questions, has_more = ledger_db.page_durable_questions(
            root,
            str(state.get("task_id") or ""),
            attempt_id=str(attempt.get("attempt_id") or ""),
            offset=0,
            limit=2,
        )
        current = [
            item for item in questions
            if str(item.get("status") or "") in {"open", "answered"}
        ]
        if has_more or len(current) != 1:
            raise ValueError("retired host durable question is ambiguous")
        resolved.append((attempt, current[0]))
    return resolved


def recover_resumed_host_epoch(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Retire a proven-dead host frontier and dispatch exact new children once."""
    if not resumed_host_epoch_recovery_required(task_dir, state, plan):
        return None
    root = _ledger_root_for_artifact(task_dir)
    epoch = ledger_db.get_native_host_epoch(
        root, str(state.get("coordinator_host_thread_id") or ""),
    )
    if not isinstance(epoch, Mapping):
        raise ValueError("authenticated replacement host epoch is unavailable")
    current_epoch = int(epoch["epoch"])
    prior_epoch = int(state.get("coordinator_native_host_epoch") or 0)
    handoff_recorded_at = str(epoch.get("observed_at") or "")
    if not handoff_recorded_at:
        raise ValueError("authenticated replacement host epoch has no immutable observation time")
    retired_questions = _retired_epoch_question_frontiers(task_dir, state, plan)
    question_source_ids = {
        str(source.get("attempt_id") or "") for source, _question in retired_questions
    }
    answered_retired_questions = [
        (source, question) for source, question in retired_questions
        if str(question.get("status") or "") == "answered"
    ]
    _wave, _frontier_attempts = _effective_plan_frontier(plan, state)
    other_delivered = [
        item for item in _frontier_attempts
        if isinstance(item, Mapping)
        and not item.get("invalidated")
        and str(item.get("attempt_id") or "") not in question_source_ids
        and str(item.get("dispatch_delivery_status") or "") == "delivered"
        and str(item.get("status") or "")
        in {AWAITING_HOST_SPAWN, "running", RESULT_READY, "waiting_question"}
        and isinstance(item.get("native_host_epoch"), int)
        and not isinstance(item.get("native_host_epoch"), bool)
        and int(item.get("native_host_epoch") or 0) < current_epoch
        and not (
            isinstance(item.get("native_terminal_stop"), Mapping)
            and item["native_terminal_stop"].get("observed") is True
        )
    ]
    if retired_questions and not answered_retired_questions and not other_delivered:
        identities = [{
            "attempt_id": str(source.get("attempt_id") or ""),
            "wave_ref": str(source.get("wave_ref") or ""),
            "phase_ref": str(source.get("phase_ref") or ""),
            "logical_delegation_key": str(source.get("logical_delegation_key") or ""),
            "assignment_lineage_digest": str(source.get("assignment_lineage_digest") or ""),
            "plan_assignment_lineage_digest": str(source.get("plan_assignment_lineage_digest") or ""),
            "disposition": "durable_question_carried_forward",
        } for source, _question in retired_questions]
        identities.sort(key=lambda item: (
            item["wave_ref"], item["phase_ref"], item["logical_delegation_key"], item["attempt_id"],
        ))
        handoff_digest = digest_text(canonical_json.dumps({
            "task_id": str(state.get("task_id") or ""),
            "from_epoch": prior_epoch,
            "to_epoch": current_epoch,
            "retired_questions": identities,
        }))
        state["coordinator_native_host_epoch"] = current_epoch
        state["coordinator_native_host_epoch_fingerprint"] = str(epoch["fingerprint"])
        handoffs = [
            item for item in state.get("native_host_epoch_handoffs") or []
            if isinstance(item, Mapping)
        ]
        existing_handoff = next((
            item for item in handoffs
            if str(item.get("digest") or "") == handoff_digest
        ), None)
        if existing_handoff is None:
            receipt = {
                "schema": "cortex/native-host-epoch-handoff/v1",
                "digest": handoff_digest,
                "from_epoch": prior_epoch,
                "to_epoch": current_epoch,
                "assignments": identities,
                "status": "awaiting_question_answer",
                "recorded_at": handoff_recorded_at,
            }
            _append_host_epoch_handoff_projection(root, state, receipt)
            _write_immutable_host_epoch_handoff_audit(
                root, state, receipt, stage="final-awaiting_question_answer",
            )
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "native_host_epoch_retired_question_carried_forward",
            handoff_digest,
        )
        first_question = retired_questions[0][1]
        return state, plan, {"wave_id": identities[0]["wave_ref"] or None, "spawn_requests": []}, {
            "mode": "awaiting_question_answer",
            "question": {
                "question_ref": str(first_question.get("question_ref") or ""),
                "question_text": str(first_question.get("question_text") or ""),
            },
            "question_refs": [str(question.get("question_ref") or "") for _source, question in retired_questions],
        }
    stale = resumed_host_epoch_orphans(task_dir, state, plan)
    assignment_identities = [
        {
            "attempt_id": str(item.get("attempt_id") or ""),
            "wave_ref": str(item.get("wave_ref") or ""),
            "phase_ref": str(item.get("phase_ref") or ""),
            "logical_delegation_key": str(item.get("logical_delegation_key") or ""),
            "assignment_lineage_digest": str(item.get("assignment_lineage_digest") or ""),
            "plan_assignment_lineage_digest": str(item.get("plan_assignment_lineage_digest") or ""),
            "disposition": "retired_for_replacement",
        }
        for item in stale
    ]
    for question_source, question in retired_questions:
        question_answered = str(question.get("status") or "") == "answered"
        assignment_identities.append({
            "attempt_id": str(question_source.get("attempt_id") or ""),
            "wave_ref": str(question_source.get("wave_ref") or ""),
            "phase_ref": str(question_source.get("phase_ref") or ""),
            "logical_delegation_key": str(question_source.get("logical_delegation_key") or ""),
            "assignment_lineage_digest": str(question_source.get("assignment_lineage_digest") or ""),
            "plan_assignment_lineage_digest": str(question_source.get("plan_assignment_lineage_digest") or ""),
            "disposition": "retired_for_replacement"
            if question_answered else "durable_question_carried_forward",
        })
    assignment_identities.sort(key=lambda item: (
        item["wave_ref"], item["phase_ref"], item["logical_delegation_key"], item["attempt_id"],
    ))
    handoff_digest = digest_text(canonical_json.dumps({
        "task_id": str(state.get("task_id") or ""),
        "from_epoch": prior_epoch,
        "to_epoch": current_epoch,
        "assignments": assignment_identities,
    }))
    existing = next((
        item for item in state.get("native_host_epoch_handoffs") or []
        if isinstance(item, Mapping) and str(item.get("digest") or "") == handoff_digest
    ), None)
    if isinstance(existing, Mapping):
        raise ValueError("native host epoch handoff was already consumed")
    retired_sessions: list[dict[str, Any]] = []
    replacement_stale: list[dict[str, Any]] = []
    all_sessions = ledger_db.list_worker_sessions(root, str(state.get("task_id") or ""))
    for attempt in stale:
        old_child = str(attempt.get("worker_host_thread_id") or "")
        exact_sessions = [
            item for item in all_sessions
            if str(item.get("attempt_id") or "") == str(attempt.get("attempt_id") or "")
        ]
        active_sessions = [
            item for item in exact_sessions
            if str(item.get("status") or "")
            in {"awaiting_spawn", "running", "idle_resumable", "stopped_recoverable"}
            and bool(item.get("resumable"))
        ]
        completed_sessions = [
            item for item in exact_sessions
            if str(item.get("status") or "") == "completed"
            and not bool(item.get("resumable"))
        ]
        if str(attempt.get("status") or "") == RESULT_READY:
            if active_sessions:
                raise ValueError("result-ready host handoff has a contradictory active worker session")
            session_lineage = completed_sessions
        else:
            session_lineage = active_sessions
        if not session_lineage:
            raise ValueError("native host handoff attempt has no exact worker session lineage")
        expected_task_name = str((attempt.get("spawn_request") or {}).get("task_name") or "")
        if any(
            str(item.get("host_task_name") or "") not in {"", expected_task_name}
            or str(item.get("host_agent_id") or "") != old_child
            or not str(item.get("session_id") or "")
            or isinstance(item.get("generation"), bool)
            or not isinstance(item.get("generation"), int)
            or int(item.get("generation") or 0) < 1
            for item in session_lineage
        ):
            raise ValueError("native host handoff worker session identity is ambiguous")
        waiting_question = str(attempt.get("status") or "") == "waiting_question"
        incomplete_stop = (
            isinstance(attempt.get("native_incomplete_stop_evidence"), Mapping)
            and attempt["native_incomplete_stop_evidence"].get("observed") is True
        )
        if waiting_question:
            attempt["native_host_epoch_question_retired"] = True
            attempt["lifecycle_status"] = "paused_awaiting_user"
            attempt["host_stop_outcome"] = "awaiting_user_on_retired_host"
        else:
            attempt["status"] = "failed"
            attempt["lifecycle_status"] = "needs_recovery"
            if incomplete_stop:
                failure_kind = str(
                    attempt.get("host_stop_outcome")
                    or "native_worker_stopped_without_result_pending_recovery"
                )
                attempt["finalization_reason"] = failure_kind
            else:
                failure_kind = "native_host_epoch_ended_without_stop"
                attempt["host_stop_outcome"] = failure_kind
                attempt["finalization_reason"] = failure_kind
            replacement_stale.append(attempt)
        attempt["orphaned_at"] = now()
        attempt["host_resumable"] = False
        if old_child:
            attempt["retired_worker_host_thread_digest"] = digest_text(old_child)
            attempt.pop("worker_host_thread_id", None)
        if not waiting_question:
            _record_technical_recovery_breaker(
                state, attempt, failure_kind=failure_kind,
                attempt_result_ref=str(attempt.get("attempt_result_ref") or ""),
            )
        retired_sessions.extend({
            "session_id": str(item["session_id"]),
            "attempt_id": str(attempt.get("attempt_id") or ""),
            "generation": int(item["generation"]),
            "host_agent_id": item.get("host_agent_id"),
            "host_task_name": str(item.get("host_task_name") or expected_task_name),
            "host_tool": str(item.get("host_tool") or "spawn_agent"),
            "started_at": item.get("started_at"),
        } for item in active_sessions)
    for question_source, question in answered_retired_questions:
        question_source["status"] = "failed"
        question_source["lifecycle_status"] = "needs_recovery"
        question_source["host_resumable"] = False
        question_source["finalization_reason"] = "answered_question_worker_host_unavailable"
        question_source["epoch_question_recovery"] = {
            "question_ref": str(question.get("question_ref") or ""),
            "answered_sequence": int(question.get("answered_sequence") or 0),
            "answer_digest": "sha256:" + digest_text(str(question.get("answer") or "")),
        }
        _record_technical_recovery_breaker(
            state,
            question_source,
            failure_kind="answered_question_worker_host_unavailable",
        )
        replacement_stale.append(question_source)
    state["coordinator_native_host_epoch"] = current_epoch
    state["coordinator_native_host_epoch_fingerprint"] = str(epoch["fingerprint"])
    state["pending_native_host_epoch_session_retirements"] = retired_sessions
    handoff_receipt = {
        "schema": "cortex/native-host-epoch-handoff/v1",
        "digest": handoff_digest,
        "from_epoch": prior_epoch,
        "to_epoch": current_epoch,
        "assignments": assignment_identities,
        "status": "recovery_pending",
        "recorded_at": handoff_recorded_at,
    }
    _append_host_epoch_handoff_projection(root, state, handoff_receipt)
    if not stale and not replacement_stale:
        state["native_host_epoch_handoffs"][-1]["status"] = "epoch_advanced_no_delivered_assignments"
        _write_immutable_host_epoch_handoff_audit(
            root,
            state,
            state["native_host_epoch_handoffs"][-1],
            stage="final-epoch_advanced_no_delivered_assignments",
        )
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "native_host_epoch_advanced_before_dispatch_delivery",
            handoff_digest,
        )
        return None
    if not replacement_stale:
        questions = _open_blocking_questions(task_dir, state)
        matching_questions = [
            item for item in questions
            if str(item.get("attempt_id") or "")
            in {str(source.get("attempt_id") or "") for source in stale}
        ]
        if not matching_questions:
            raise ValueError("retired host question frontier is ambiguous")
        matching_questions.sort(key=lambda item: (
            int(item.get("published_sequence") or 0), str(item.get("question_ref") or ""),
        ))
        question = matching_questions[0]
        with ledger_db.transaction(root):
            _consume_pending_host_epoch_session_retirements(
                root,
                state,
                status="awaiting_question_answer",
                recovery_batch_key=handoff_digest,
            )
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "native_host_epoch_question_worker_retired",
                handoff_digest,
            )
        return state, plan, {
            "wave_id": str(stale[0].get("wave_ref") or "") or None,
            "spawn_requests": [],
        }, {
            "mode": "awaiting_question_answer",
            "question": {
                "question_ref": str(question.get("question_ref") or question.get("question_id") or ""),
                "question_text": str(question.get("question_text") or ""),
            },
            "question_refs": [str(item.get("question_ref") or "") for item in matching_questions],
        }
    recovered = _dispatch_server_owned_recovery(
        params,
        task_dir,
        state,
        plan,
        reason=(
            "The authenticated prior host epoch ended. Replace each unfinished exact assignment "
            "using its bound trusted Stop, canonical result, or no-Stop recovery evidence."
        ),
    )
    if recovered is None:
        raise ValueError("native host epoch handoff has no deterministic replacement route")
    return recovered


def recover_answered_epoch_question(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    question_ref: str,
) -> dict[str, Any] | None:
    """Replace a dead question worker only after its durable answer exists."""
    root = _ledger_root_for_artifact(task_dir)
    with state_lock(root):
        _, fresh_dir, fresh_state = load_state(str(state.get("task_id") or ""), params)
        if fresh_dir != task_dir:
            raise ValueError("answered host-epoch question task changed")
        authorize(fresh_state, params)
        record = ledger_db.get_durable_question(
            root, str(fresh_state.get("task_id") or ""), str(question_ref or ""),
        )
        if not isinstance(record, Mapping) or str(record.get("status") or "") != "answered":
            return None
        source_id = str(record.get("attempt_id") or "")
        sources = [
            item for item in fresh_state.get("attempts") or []
            if isinstance(item, dict)
            and str(item.get("attempt_id") or "") == source_id
            and item.get("native_host_epoch_question_retired") is True
            and not item.get("invalidated")
        ]
        if len(sources) != 1:
            return None
        source = sources[0]
        source["status"] = "failed"
        source["lifecycle_status"] = "needs_recovery"
        source["host_resumable"] = False
        source["finalization_reason"] = "answered_question_worker_host_unavailable"
        source["epoch_question_recovery"] = {
            "question_ref": str(record.get("question_ref") or ""),
            "answered_sequence": int(record.get("answered_sequence") or 0),
            "answer_digest": "sha256:" + digest_text(str(record.get("answer") or "")),
        }
        _record_technical_recovery_breaker(
            fresh_state,
            source,
            failure_kind="answered_question_worker_host_unavailable",
        )
        fresh_state["status"] = "active"
        plan = _load_orchestrate_plan(task_dir, fresh_state)
        recovered = _dispatch_server_owned_recovery(
            params,
            task_dir,
            fresh_state,
            plan,
            reason=(
                "The original question worker belonged to a proven-dead host epoch. Spawn a new "
                "worker for the same assignment and require it to read the exact durable answer."
            ),
        )
        if recovered is None:
            raise ValueError("answered host-epoch question has no deterministic replacement route")
        recovered_state, recovered_plan, prepared, receipt = recovered
        return _orchestrate_response(
            "resume",
            recovered_state,
            wave_id=prepared.get("wave_id"),
            spawn_requests=prepared.get("spawn_requests") or [],
            result={"recovery": receipt},
            plan=recovered_plan,
        )


def _is_terminal_governance_closure_attempt(
    root: Path,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> bool:
    """Keep an accepted blocked governance closure out of technical retry."""
    if (
        str(attempt.get("gate") or "") != "governance_close"
        or str(attempt.get("protocol_status") or "") != "blocked"
    ):
        return False
    canonical = attempt_protocol.get_attempt_result(
        root,
        task_id=str(state.get("task_id") or ""),
        attempt_id=str(attempt.get("attempt_id") or ""),
    )
    metadata = (
        canonical.get("metadata")
        if isinstance(canonical, Mapping) and isinstance(canonical.get("metadata"), Mapping)
        else {}
    )
    closure = (
        metadata.get("governance_closure")
        if isinstance(metadata.get("governance_closure"), Mapping)
        else {}
    )
    return bool(
        isinstance(canonical, Mapping)
        and str(canonical.get("status") or "") == "blocked"
        and str(closure.get("closure_outcome") or "") == "blocked"
        and str(closure.get("closure_basis_digest") or "")
    )


def _technical_recovery_context(
    task_dir: Path,
    state: Mapping[str, Any],
    source: Mapping[str, Any],
    breaker: Mapping[str, Any],
    *,
    recovery_stage: str,
    recovery_reason: str,
) -> tuple[dict[str, Any], str, str]:
    """Bind one replacement to its exact source result and evaluator deficit.

    The compact context is server-owned assignment authority, not a report
    projection.  Detailed source evidence remains available only through the
    exact paginated predecessor-result read capability.  Result-less failures
    carry an explicit absence reason and never fabricate a result reference.
    """
    evaluation = source.get("acceptance_evaluation")
    evaluation = dict(evaluation) if isinstance(evaluation, Mapping) else {}
    failure_kind = str(breaker.get("kind") or "technical_failure").strip()
    result_ref = str(
        source.get("attempt_result_ref") or breaker.get("attempt_result_ref") or ""
    ).strip()
    result_digest = ""
    if result_ref:
        root = _ledger_root_for_artifact(task_dir)
        canonical = attempt_protocol.get_attempt_result(
            root,
            task_id=str(state.get("task_id") or ""),
            attempt_id=str(source.get("attempt_id") or ""),
        )
        if (
            not isinstance(canonical, Mapping)
            or str(canonical.get("result_ref") or "") != result_ref
        ):
            raise ValueError("technical recovery source result is not canonical")
        result_digest = "sha256:" + digest_text(canonical_json.dumps(canonical))

    missing = sorted({
        str(item).strip()
        for item in evaluation.get("missing_verification_kinds") or []
        if str(item).strip()
    })
    evaluator_reasons = [
        str(item).strip()
        for item in evaluation.get("reasons") or []
        if str(item).strip()
    ]
    disposition = str(
        evaluation.get("acceptance_status")
        or source.get("acceptance_status")
        or ("technical_failure" if not result_ref else "needs_recovery")
    ).strip()
    failure_class = str(
        evaluation.get("failure_class")
        or breaker.get("failure_class")
        or "technical"
    ).strip()
    remaining_work = (
        "Produce and record the missing verification evidence: " + ", ".join(missing) + "."
        if missing else
        "Resolve the exact technical failure evidence and complete the original assignment obligations."
    )
    context = {
        "schema": "cortex/technical-recovery-context/v1",
        "source_result_status": "available" if result_ref else "absent",
        "source_result_absence_reason": "" if result_ref else failure_kind,
        "evaluator_disposition": disposition,
        "failure_class": failure_class,
        "evaluator_reasons": evaluator_reasons or [failure_kind],
        "recovery_stage": recovery_stage,
        "recovery_reason": redact(recovery_reason, 1000),
        "missing_obligations": missing,
        "failure_evidence": [failure_kind],
        "remaining_work": remaining_work,
    }
    question_recovery = source.get("epoch_question_recovery")
    if isinstance(question_recovery, Mapping):
        context["durable_question_ref"] = str(question_recovery.get("question_ref") or "")
        context["durable_answer_status"] = "available"
        context["remaining_work"] = (
            "Read the exact answered durable question through worker_question poll, then continue "
            "the original assignment with that canonical Unicode answer."
        )
    context_digest = "sha256:" + digest_text(canonical_json.dumps(context))
    return context, result_ref, result_digest


def _replace_reliability_recovery_assignment(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    breaker: Mapping[str, Any],
    reason: str,
    defer_commit: bool = False,
    recovery_target: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Replace one failed assignment without changing occurrence identity.

    Wave, phase, logical-slot, and every untouched assignment identity are
    immutable once compiled.  Reliability recovery changes only the failed
    slot's executable model/profile lineage inside that same occurrence.  The
    plan revision is append-only, while future corrective/verifier route
    references remain stable and resolvable.
    """
    fingerprint = str(breaker.get("fingerprint") or "").strip()
    logical_key = str(breaker.get("logical_delegation_key") or "").strip()
    plan_lineage = str(breaker.get("plan_assignment_lineage_digest") or "").strip()
    if not fingerprint or not logical_key or not plan_lineage:
        return None
    source = _reliability_recovery_source(state, plan, breaker)
    if not isinstance(source, dict):
        return None
    prior_receipts = [
        item for item in state.get("reliability_recovery_receipts") or []
        if isinstance(item, Mapping)
    ]
    waves = [item for item in plan.get("waves") or [] if isinstance(item, dict)]
    source_wave_ref = str(source.get("wave_ref") or "")
    source_index = next((
        index for index, wave in enumerate(waves)
        if str(wave.get("wave_ref") or "") == source_wave_ref
    ), -1)
    if source_index < 0:
        return None
    future_wave_refs = {
        str(wave.get("wave_ref") or "") for wave in waves[source_index + 1:]
    }
    if any(
        isinstance(item, Mapping)
        and not item.get("invalidated")
        and str(item.get("wave_ref") or "") in future_wave_refs
        for item in state.get("attempts") or []
    ):
        return None
    task = load_task_definition(task_dir, state)
    phase_kind = str(source.get("phase_kind") or source.get("gate") or "")
    operation_kind = str(source.get("operation_kind") or "")
    if not phase_kind or operation_kind not in {"inspect", "modify", "verify", "close"}:
        return None
    target = dict(recovery_target) if isinstance(recovery_target, Mapping) else reliability_recovery_target(
        source, PROFILES, MODEL_EFFORTS, MODEL_RECOMMENDED_EFFORTS,
    )
    if target is None:
        return None
    next_model = target["model"]
    resolved_profile = target["profile"]
    recovery_stage = target["stage"]
    recovery_context, recovery_source_result_ref, recovery_source_result_digest = (
        _technical_recovery_context(
            task_dir,
            state,
            source,
            breaker,
            recovery_stage=recovery_stage,
            recovery_reason=reason,
        )
    )
    recovery_chain_result_refs = [
        str(item) for item in target.get("recovery_chain_result_refs") or []
        if str(item)
    ]
    if recovery_source_result_ref and recovery_source_result_ref not in recovery_chain_result_refs:
        recovery_chain_result_refs.append(recovery_source_result_ref)
    recovery_chain_result_digests: dict[str, str] = {}
    if recovery_chain_result_refs:
        root = _ledger_root_for_artifact(task_dir)
        for chain_ref in recovery_chain_result_refs:
            chain_attempts = [
                item for item in state.get("attempts") or []
                if isinstance(item, Mapping)
                and str(item.get("attempt_result_ref") or "") == chain_ref
            ]
            if len(chain_attempts) != 1:
                raise ValueError("technical recovery chain source is ambiguous")
            canonical_chain = attempt_protocol.get_attempt_result(
                root,
                task_id=str(state.get("task_id") or ""),
                attempt_id=str(chain_attempts[0].get("attempt_id") or ""),
            )
            if (
                not isinstance(canonical_chain, Mapping)
                or str(canonical_chain.get("result_ref") or "") != chain_ref
            ):
                raise ValueError("technical recovery chain source is not canonical")
            recovery_chain_result_digests[chain_ref] = (
                "sha256:" + digest_text(canonical_json.dumps(canonical_chain))
            )
        recovery_context["source_result_chain"] = [
            {"attempt_result_ref": ref, "digest": recovery_chain_result_digests[ref]}
            for ref in recovery_chain_result_refs
        ]
    recovery_context_digest = "sha256:" + digest_text(
        canonical_json.dumps(recovery_context)
    )
    recovery_occurrence_key = "recovery-" + digest_text(canonical_json.dumps({
        "source_logical_delegation_key": logical_key,
        "source_plan_assignment_lineage_digest": plan_lineage,
        "source_result_ref": recovery_source_result_ref,
        "source_result_digest": recovery_source_result_digest,
        "recovery_context_digest": recovery_context_digest,
        "stage": recovery_stage,
        "model": next_model,
        "profile": resolved_profile,
    }))[:24]
    existing = next((
        item for item in prior_receipts
        if str(item.get("recovery_occurrence_key") or "") == recovery_occurrence_key
    ), None)
    if isinstance(existing, Mapping):
        return None
    source_requested_profile = str(
        source.get("requested_profile")
        or source.get("profile")
        or source.get("agent")
        or ""
    ).strip()
    source_resolved_profile = str(
        source.get("resolved_profile")
        or source.get("profile")
        or source.get("agent")
        or ""
    ).strip()
    if not source_requested_profile or not source_resolved_profile:
        return None
    resolution_reason = (
        "reliability_recovery_universal_fallback"
        if recovery_stage == "universal_profile_fallback"
        else str(source.get("resolution_reason") or "requested_profile_compatible")
    )
    raw = [{
        "wave_id": source_wave_ref,
        "delegations": [{
            "gate": phase_kind,
            "phase_kind": phase_kind,
            "wave_index": source.get("wave_index"),
            "operation_kind": operation_kind,
            "agent": resolved_profile,
            "objective": str(source.get("objective") or ""),
            "ownership": str(source.get("ownership") or "Execute the exact recovery assignment."),
            "acceptance_criteria": list(source.get("acceptance_criteria") or []),
            "verification": list(source.get("verification") or []),
            "model": next_model,
            "reasoning_effort": target["reasoning_effort"],
        }],
    }]
    inserted, _preview = _normalize_orchestrate_waves(
        raw, task, plan.get("host_capabilities") or {}, str(task["project_root"]),
    )
    if (
        len(inserted) != 1
        or len([item for item in inserted[0].get("delegations") or [] if isinstance(item, dict)]) != 1
    ):
        raise ValueError("reliability recovery must compile exactly one replacement assignment")
    effective = json.loads(json.dumps(waves, ensure_ascii=False))
    source_wave = effective[source_index]
    source_specs = [
        item for item in source_wave.get("delegations") or []
        if isinstance(item, dict)
        and str(item.get("logical_delegation_key") or "") == logical_key
        and str(item.get("plan_assignment_lineage_digest") or "") == plan_lineage
    ]
    if len(source_specs) != 1:
        raise ValueError("reliability recovery source assignment is ambiguous in the compiled plan")
    source_spec = source_specs[0]
    replacement = next(
        item for item in inserted[0]["delegations"] if isinstance(item, dict)
    )
    immutable_identity = {
        field: source_spec.get(field)
        for field in (
            "gate", "phase_kind", "phase_ref", "wave_ref", "wave_index",
            "orchestration_wave_id", "orchestration_delegation_key",
            "logical_delegation_key", "predecessor_wave_refs",
        )
    }
    replacement.update(immutable_identity)
    replacement.update({
        "requested_profile": source_requested_profile,
        "resolved_profile": resolved_profile,
        "resolution_reason": resolution_reason,
        "effort_resolution_reason": target["effort_resolution_reason"],
        "recovery_stage": recovery_stage,
        "recovery_occurrence_key": recovery_occurrence_key,
        "recovery_source_logical_delegation_key": logical_key,
        "recovery_source_plan_assignment_lineage_digest": plan_lineage,
        "recovery_source_result_ref": recovery_source_result_ref,
        "recovery_source_result_digest": recovery_source_result_digest,
        "recovery_chain_result_refs": recovery_chain_result_refs,
        "recovery_chain_result_digests": recovery_chain_result_digests,
        "recovery_context": recovery_context,
        "recovery_context_digest": recovery_context_digest,
    })
    question_recovery = source.get("epoch_question_recovery")
    if isinstance(question_recovery, Mapping):
        replacement.update({
            "recovery_question_ref": str(question_recovery.get("question_ref") or ""),
            "recovery_question_source_attempt_id": str(source.get("attempt_id") or ""),
            "recovery_question_source_dispatch_ref": str(source.get("dispatch_ref") or ""),
        })
    replacement["assignment_lineage_digest"] = _assignment_lineage_digest(replacement)
    replacement["plan_assignment_lineage_digest"] = replacement["assignment_lineage_digest"]
    replacement_lineage = str(replacement["plan_assignment_lineage_digest"])
    source_delegations = source_wave.get("delegations") or []
    source_slot = next(
        index for index, item in enumerate(source_delegations)
        if item is source_spec
    )
    untouched_before = [
        (
            str(wave.get("wave_ref") or ""),
            str(wave.get("phase_ref") or ""),
            str(spec.get("logical_delegation_key") or ""),
            str(spec.get("plan_assignment_lineage_digest") or ""),
        )
        for wave in waves
        for spec in wave.get("delegations") or []
        if isinstance(spec, Mapping)
        and not (
            str(spec.get("logical_delegation_key") or "") == logical_key
            and str(spec.get("plan_assignment_lineage_digest") or "") == plan_lineage
        )
    ]
    source_delegations[source_slot] = replacement
    untouched_after = [
        (
            str(wave.get("wave_ref") or ""),
            str(wave.get("phase_ref") or ""),
            str(spec.get("logical_delegation_key") or ""),
            str(spec.get("plan_assignment_lineage_digest") or ""),
        )
        for wave in effective
        for spec in wave.get("delegations") or []
        if isinstance(spec, Mapping) and spec is not replacement
    ]
    if untouched_after != untouched_before:
        raise ValueError("reliability recovery changed an untouched occurrence identity")
    # Validate every route binding before retiring the source. The helper is a
    # two-phase validate/apply operation, so any completed or mismatched route
    # fails with the source attempt, plan, and all route collections untouched.
    rebound_route_roles = _rebind_active_rework_route_assignment(
        state,
        source,
        replacement,
        recovery_occurrence_key=recovery_occurrence_key,
    )
    source["invalidated"] = True
    source["invalidated_at"] = now()
    source["invalidation_reason"] = "superseded_by_reliability_recovery"
    if (
        source.get("status") == AWAITING_HOST_SPAWN
        and source.get("dispatch_delivery_status") == "pending"
    ):
        source["status"] = "superseded"
        source["lifecycle_status"] = "superseded"
        source["dispatch_delivery_status"] = "superseded"
        source["host_resumable"] = False
        source["finalized_at"] = now()
    state["status"] = "active"
    state.pop("blocked_reason", None)
    plan["waves"] = effective
    # Historical attempts, breakers, recovery receipts, and plan revisions keep
    # the retired lineage. Active route capability state must instead move to
    # the replacement in the same transaction, for every route collection and
    # every exact corrective/verifier/close role.
    receipt = {
        "schema": "cortex/reliability-recovery/v1",
        "failure_fingerprint": fingerprint,
        "recovery_occurrence_key": recovery_occurrence_key,
        "recovery_stage": recovery_stage,
        "logical_delegation_key": logical_key,
        "plan_assignment_lineage_digest": plan_lineage,
        "source_wave_ref": source_wave_ref,
        "wave_ref": source_wave_ref,
        "recovery_source_result_ref": recovery_source_result_ref,
        "recovery_source_result_digest": recovery_source_result_digest,
        "recovery_chain_result_refs": recovery_chain_result_refs,
        "recovery_chain_result_digests": recovery_chain_result_digests,
        "recovery_context_digest": recovery_context_digest,
        "replacement_plan_assignment_lineage_digest": replacement_lineage,
        "model": next_model,
        "reasoning_effort": target["reasoning_effort"],
        "effort_resolution_reason": target["effort_resolution_reason"],
        "requested_profile": source_requested_profile,
        "source_resolved_profile": source_resolved_profile,
        "resolved_profile": resolved_profile,
        "operation_kind": operation_kind,
        "rebound_route_roles": rebound_route_roles,
        "same_child_deficit_repair": recovery_stage == "same_child_deficit_repair",
        "source_attempt_id": str(source.get("attempt_id") or ""),
        "same_child_source_attempt_id": str(
            target.get("same_child_source_attempt_id") or source.get("attempt_id") or ""
        ),
        "status": "prepared",
        "recorded_at": now(),
    }
    state["reliability_recovery_receipts"] = [*prior_receipts[-31:], receipt]
    if defer_commit:
        return state, plan, {
            "wave_id": source_wave_ref,
            "spawn_requests": [],
            "attempt_ids": [],
            "state": state,
        }, receipt
    _sync_orchestration_wave_occurrences(state, plan)
    root = _ledger_root_for_artifact(task_dir)
    with ledger_db.transaction(root):
        revision = ledger_db.append_plan_revision(
            root,
            str(state["task_id"]),
            task_revision=int(state.get("task_revision") or 1),
            impact={"classification": "reliability_recovery", **receipt},
            plan=plan,
            status="active",
        )
        plan["plan_revision"] = revision["plan_revision"]
        state["plan_revision"] = revision["plan_revision"]
        state["plan_digest"] = revision["plan_digest"]
        _write_orchestrate_plan(task_dir, plan, preserve_updated_at=True)
        save_state(
            task_dir, task_dir / "state.sqlite", state,
            "reliability_recovery_replaced", redact(reason, 1000),
        )
        prepared = _prepare_orchestrate_wave_transaction(
            {**params, "task_id": state["task_id"], "principal": state.get("principal")},
            task_dir, state, plan,
        )
        prepared_state = prepared.get("state")
        if not isinstance(prepared_state, dict):
            raise ValueError("reliability recovery preparation returned no durable state")
        state = prepared_state
        receipt["status"] = "dispatched"
        matching_receipts = [
            item for item in state.get("reliability_recovery_receipts") or []
            if isinstance(item, dict)
            and str(item.get("recovery_occurrence_key") or "")
            == recovery_occurrence_key
        ]
        if len(matching_receipts) != 1:
            raise ValueError("prepared reliability recovery receipt is unavailable")
        matching_receipts[0]["status"] = "dispatched"
        # Persist the final transition after successor preparation but before
        # commit so the returned recovery route and authoritative SQLite
        # ledger cannot disagree.
        save_state(
            task_dir, task_dir / "state.sqlite", state,
            "reliability_recovery_dispatched", recovery_occurrence_key,
        )
    return state, plan, prepared, receipt


def _terminal_governance_closure_breaker(
    state: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Return the immutable accepted blocked-closure receipt, if present.

    Unlike a technical blocked marker, this receipt is terminal task evidence.
    It intentionally does not depend on the mutable active-gate projection:
    replay, recovery, or a stale prior-wave view must never reopen it.
    """
    if (
        str(state.get("status") or "") != "terminal_blocked"
        or str(state.get("blocked_reason") or "") != "governance_closure_terminal_blocked"
    ):
        return None
    receipts = [
        item for item in state.get("terminal_recovery_breakers") or []
        if isinstance(item, Mapping)
        and str(item.get("kind") or "") == "governance_closure_terminal_blocked"
        and str(item.get("fingerprint") or "")
        and str(item.get("closure_basis_digest") or "")
        and str(item.get("attempt_result_ref") or "")
    ]
    return receipts[-1] if receipts else None


def _active_terminal_recovery_breakers(
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Return every exact active technical breaker in deterministic slot order."""
    terminal_closure = _terminal_governance_closure_breaker(state)
    if terminal_closure is not None:
        return [terminal_closure]
    current_wave, current_attempts = _effective_plan_frontier(
        dict(plan), dict(state),
    )
    current_wave_ref = str(
        current_wave.get("wave_ref") or ""
    ) if isinstance(current_wave, Mapping) else ""
    current_phase_ref = str(
        current_wave.get("phase_ref") or ""
    ) if isinstance(current_wave, Mapping) else ""
    current_plan_lineages = {
        (
            str(spec.get("logical_delegation_key") or ""),
            str(spec.get("plan_assignment_lineage_digest") or ""),
        )
        for spec in (current_wave.get("delegations") or [])
        if isinstance(current_wave, Mapping)
        if isinstance(spec, Mapping)
        and str(spec.get("wave_ref") or "") == current_wave_ref
        and str(spec.get("phase_ref") or "") == current_phase_ref
        and str(spec.get("logical_delegation_key") or "")
        and str(spec.get("plan_assignment_lineage_digest") or "")
    }
    current_slot_order = {
        (
            str(spec.get("logical_delegation_key") or ""),
            str(spec.get("plan_assignment_lineage_digest") or ""),
        ): index
        for index, spec in enumerate(current_wave.get("delegations") or [])
        if isinstance(current_wave, Mapping)
        if isinstance(spec, Mapping)
    }
    current_attempt_lineages = {
        (
            str(attempt.get("logical_delegation_key") or ""),
            str(attempt.get("assignment_lineage_digest") or ""),
            str(attempt.get("plan_assignment_lineage_digest") or ""),
        )
        for attempt in current_attempts
        if isinstance(attempt, Mapping)
        and not attempt.get("invalidated")
        and str(attempt.get("wave_ref") or "") == current_wave_ref
        and str(attempt.get("phase_ref") or "") == current_phase_ref
        and str(attempt.get("logical_delegation_key") or "")
        and str(attempt.get("assignment_lineage_digest") or "")
        and str(attempt.get("plan_assignment_lineage_digest") or "")
    }
    current_closure_lineages = {
        (
            str(attempt.get("logical_delegation_key") or ""),
            "sha256:" + digest_text(canonical_json.dumps(attempt.get("governance_closure_basis"))),
        )
        for attempt in current_attempts
        if isinstance(attempt, Mapping)
        and not attempt.get("invalidated")
        and str(attempt.get("wave_ref") or "") == current_wave_ref
        and str(attempt.get("phase_ref") or "") == current_phase_ref
        and str(attempt.get("gate") or "") == "governance_close"
        and str(attempt.get("logical_delegation_key") or "")
        and isinstance(attempt.get("governance_closure_basis"), Mapping)
    }
    active: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for receipt in state.get("terminal_recovery_breakers") or []:
        if not isinstance(receipt, Mapping):
            continue
        logical_key = str(receipt.get("logical_delegation_key") or "")
        if str(receipt.get("kind") or "") in {
            "governance_closure_identical_basis_retry_exhausted",
            "governance_closure_terminal_blocked",
        }:
            lineage = (logical_key, str(receipt.get("closure_basis_digest") or ""))
            if lineage in current_closure_lineages:
                key = (logical_key, str(receipt.get("closure_basis_digest") or ""), str(receipt.get("fingerprint") or ""))
                if key not in seen:
                    active.append(receipt)
                    seen.add(key)
            continue
        plan_lineage = str(receipt.get("plan_assignment_lineage_digest") or "")
        assignment_lineage = str(receipt.get("assignment_lineage_digest") or "")
        if (
            (logical_key, plan_lineage) in current_plan_lineages
            and (logical_key, assignment_lineage, plan_lineage) in current_attempt_lineages
        ):
            key = (logical_key, plan_lineage, assignment_lineage)
            if key not in seen:
                active.append(receipt)
                seen.add(key)
    return sorted(active, key=lambda item: (
        current_slot_order.get((
            str(item.get("logical_delegation_key") or ""),
            str(item.get("plan_assignment_lineage_digest") or ""),
        ), 2**31 - 1),
        str(item.get("logical_delegation_key") or ""),
        str(item.get("plan_assignment_lineage_digest") or ""),
        str(item.get("fingerprint") or ""),
    ))


def _terminalize_unroutable_technical_recovery(
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    lifecycle: str,
) -> dict[str, Any]:
    """Stop only an exactly exhausted universal-Sol technical occurrence.

    A generic missing dispatch is not exhaustion.  The active breaker must
    still bind the exact compiled assignment, the registry-owned recovery
    compiler must report no stage after universal Sol, and no canonical
    product defect may have an append-rework route.
    """
    breakers = _active_terminal_recovery_breakers(state, plan)
    if (
        not breakers
        or not all(
            _reliability_recovery_is_exactly_exhausted(state, plan, breaker)
            for breaker in breakers
        )
        or _current_product_rework_attempt(state, plan) is not None
        or state.get("pending_product_reworks")
    ):
        raise ValueError("technical recovery is not exactly exhausted")
    identity_valid = True
    try:
        current_wave, current_attempts = _effective_plan_frontier(plan, state)
    except ValueError:
        # The absence of a valid executable frontier is itself the terminal
        # condition.  Preserve only the durable occurrence keys already
        # recorded in state; never guess a replacement slot from phase prose.
        identity_valid = False
        completed = {
            str(item) for item in state.get("completed_orchestration_wave_ids") or []
        }
        skipped = {
            str(item) for item in state.get("skipped_orchestration_wave_ids") or []
        }
        occurrence = next((
            item for item in state.get("orchestration_wave_occurrences") or []
            if isinstance(item, Mapping)
            and str(item.get("wave_ref") or item.get("wave_id") or "") not in completed
            and str(item.get("wave_ref") or item.get("wave_id") or "") not in skipped
        ), None)
        current_wave = dict(occurrence) if isinstance(occurrence, Mapping) else None
        occurrence_wave_ref = str(
            current_wave.get("wave_ref") or current_wave.get("wave_id") or ""
        ) if isinstance(current_wave, Mapping) else ""
        occurrence_phase_ref = str(
            current_wave.get("phase_ref") or ""
        ) if isinstance(current_wave, Mapping) else ""
        current_attempts = [
            attempt for attempt in state.get("attempts") or []
            if isinstance(attempt, dict)
            and not attempt.get("invalidated")
            and str(attempt.get("wave_ref") or "") == occurrence_wave_ref
            and str(attempt.get("phase_ref") or "") == occurrence_phase_ref
        ]
    wave_ref = str(
        current_wave.get("wave_ref") or current_wave.get("wave_id") or ""
    ) if isinstance(current_wave, Mapping) else ""
    phase_ref = str(current_wave.get("phase_ref") or "") if isinstance(current_wave, Mapping) else ""
    assignment_occurrences = sorted(
        (
            {
                "logical_delegation_key": str(attempt.get("logical_delegation_key") or ""),
                "assignment_lineage_digest": str(attempt.get("assignment_lineage_digest") or ""),
                "plan_assignment_lineage_digest": str(attempt.get("plan_assignment_lineage_digest") or ""),
                "model": str(attempt.get("selected_model") or attempt.get("model") or ""),
            }
            for attempt in current_attempts
            if isinstance(attempt, Mapping) and not attempt.get("invalidated")
        ),
        key=lambda item: (
            item["logical_delegation_key"],
            item["assignment_lineage_digest"],
            item["plan_assignment_lineage_digest"],
        ),
    )
    identity = {
        "valid": identity_valid,
        "wave_ref": wave_ref,
        "phase_ref": phase_ref,
        "assignments": assignment_occurrences,
    }
    fingerprint = "sha256:" + digest_text(canonical_json.dumps(identity))
    existing = state.get("technical_forward_progress_terminal")
    if not (
        isinstance(existing, Mapping)
        and str(existing.get("failure_fingerprint") or "") == fingerprint
    ):
        state["technical_forward_progress_terminal"] = {
            "schema": "cortex/technical-forward-progress-terminal/v1",
            "status": "terminal_blocked",
            "reason_code": "technical_recovery_route_unavailable",
            "lifecycle": lifecycle,
            "failure_fingerprint": fingerprint,
            "occurrence": identity,
            "recorded_at": now(),
        }
    state["status"] = "terminal_blocked"
    state["blocked_reason"] = "technical_recovery_route_unavailable"
    save_state(
        task_dir,
        task_dir / "state.sqlite",
        state,
        "technical_recovery_route_unavailable",
        "stopped an active Cortex frontier that had no executable server-owned recovery route",
    )
    return _orchestrate_response(
        lifecycle,
        state,
        wave_id=wave_ref or None,
        spawn_requests=[],
        result={
            "recovery": {
                "schema": "cortex/recovery-contract/v1",
                "mode": "terminal_unroutable_frontier",
                "status": "terminal",
                "retryable": False,
                "state_mutated": True,
                "failure_fingerprint": fingerprint,
            },
        },
        plan=plan,
    )


def _route_pending_product_rework(
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    lifecycle: str,
) -> dict[str, Any] | None:
    """Expose one canonical product defect through the append-rework route."""
    queued = [
        item for item in state.get("pending_product_reworks") or []
        if isinstance(item, dict) and str(item.get("source_result_ref") or "")
    ]
    if not queued:
        return None
    if _active_terminal_recovery_breakers(state, plan):
        # Product receipts stay durable while every exact technical sibling is
        # replaced as one batch.  Exposing append here would let the source
        # wave be superseded before its technical occurrence is recovered.
        return None
    receipt = queued[0]
    state["pending_product_reworks"] = queued
    state["status"] = "rework_preflight_required"
    current_wave, _attempts = _effective_plan_frontier(plan, state)
    if isinstance(current_wave, dict):
        current_wave["status"] = "needs_rework"
    root = _ledger_root_for_artifact(task_dir)
    with ledger_db.transaction(root):
        _write_orchestrate_plan(task_dir, plan)
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "product_rework_required",
            str(receipt.get("source_attempt_id") or ""),
        )
    return _orchestrate_response(
        lifecycle,
        state,
        wave_id=str(receipt.get("wave_ref") or "") or None,
        spawn_requests=[],
        result={"product_rework": receipt},
        plan=plan,
    )


def _current_technical_recovery_attempts(
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return every exact current technical failure in stable slot order."""
    try:
        _wave, attempts = _effective_plan_frontier(plan, state)
    except ValueError:
        return []
    selected: list[dict[str, Any]] = []
    for attempt in attempts:
        if not isinstance(attempt, dict) or attempt.get("invalidated"):
            continue
        evaluation = attempt.get("acceptance_evaluation")
        if (
            isinstance(evaluation, Mapping)
            and assignment_recovery_class(evaluation) == "technical"
        ):
            selected.append(attempt)
            continue
        if not isinstance(evaluation, Mapping) and str(attempt.get("status") or "") in {
            "failed", "blocked", "cancelled", "superseded",
        }:
            selected.append(attempt)
    return sorted(selected, key=lambda item: (
        str(item.get("wave_ref") or ""),
        str(item.get("phase_ref") or ""),
        str(item.get("logical_delegation_key") or ""),
        str(item.get("plan_assignment_lineage_digest") or ""),
    ))


def _derive_current_technical_recovery(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    lifecycle: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Bind all unclassified technical stops and advance one atomic ladder."""
    sources = _current_technical_recovery_attempts(state, plan)
    if not sources:
        return None
    active_breakers = _active_terminal_recovery_breakers(state, plan)
    if not active_breakers:
        for source in sources:
            _record_technical_recovery_breaker(
                state,
                source,
                failure_kind="forward_progress_technical_failure",
                attempt_result_ref=str(source.get("attempt_result_ref") or ""),
            )
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "forward_progress_technical_failure",
            ",".join(str(source.get("attempt_id") or "") for source in sources),
        )
    return _dispatch_server_owned_recovery(
        params,
        task_dir,
        state,
        plan,
        reason=(
            "Bounded technical recovery for the exact canonical assignment occurrence "
            f"during {lifecycle}."
        ),
    )


def _ensure_technical_forward_progress(
    params: dict[str, Any],
    lifecycle: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Normalize every technical lifecycle stop into forward progress.

    This is the low-level safety net for all mutating orchestration calls. A
    stale projection, failed transport, missing canonical result, or retired
    worker may be recorded as internal JSONC evidence, but it is never a user
    decision. The server derives the same idempotent corrective route used by
    explicit recovery, preserving accepted results and issuing at most one
    selected-route dispatch. If no executable route exists after the bounded
    ladder, the exact durable frontier is stopped terminally instead of being
    returned as an active no-op.
    """
    if lifecycle not in {"continue", "resume", "recover_inspect", "recover_blocked"}:
        return result
    if str(result.get("state") or "") == "terminal_blocked":
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
            if _terminal_governance_closure_breaker(state) is not None:
                return _orchestrate_response(
                    lifecycle, state, wave_id=None, spawn_requests=[], plan=_load_orchestrate_plan(task_dir, state),
                )
            # Real task questions and explicit plan approval are the only
            # legitimate stops. Everything else is an internal recovery
            # condition and must remain invisible to the user.
            if (_plan_approval_is_pending(state) and _plan_approval_user_requested(state)) or _open_blocking_questions(task_dir, state):
                return result
            plan = _load_orchestrate_plan(task_dir, state)
            product_route = _route_pending_product_rework(
                task_dir, state, plan, lifecycle=lifecycle,
            )
            if product_route is not None:
                return product_route
            durable_route = _orchestrate_state_name(state)
            if durable_route == "ready_to_spawn":
                prepared = _prepare_orchestrate_wave(params, task_dir, state, plan)
                return _orchestrate_response(
                    lifecycle,
                    prepared["state"],
                    wave_id=prepared["wave_id"],
                    spawn_requests=prepared["spawn_requests"],
                    plan=plan,
                )
            if durable_route in {
                "waiting_workers", "completion_pending", "completed", "terminal_blocked",
            }:
                return _orchestrate_response(
                    lifecycle, state, wave_id=None, spawn_requests=[], plan=plan,
                )
            dispatched = _dispatch_server_owned_recovery(
                params,
                task_dir,
                state,
                plan,
                reason="Low-level technical lifecycle reconciliation; continue through the server-owned corrective route.",
            )
            if dispatched is not None:
                state, plan, prepared, receipt = dispatched
                response = _orchestrate_response(
                    lifecycle,
                    state,
                    wave_id=prepared["wave_id"],
                    spawn_requests=prepared["spawn_requests"],
                    result={"recovery": receipt},
                    plan=plan,
                )
                if str(response.get("state") or "") != "recovery_pending":
                    return response
            derived = _derive_current_technical_recovery(
                params, task_dir, state, plan, lifecycle=lifecycle,
            )
            if derived is not None:
                state, plan, prepared, receipt = derived
                return _orchestrate_response(
                    lifecycle,
                    state,
                    wave_id=prepared["wave_id"],
                    spawn_requests=prepared["spawn_requests"],
                    result={"recovery": receipt},
                    plan=plan,
                )
            product_route = _route_pending_product_rework(
                task_dir, state, plan, lifecycle=lifecycle,
            )
            if product_route is not None:
                return product_route
            if state.get("status") == "completed":
                return _orchestrate_response(
                    lifecycle, state, wave_id=None, spawn_requests=[], plan=plan,
                )
            breakers = _active_terminal_recovery_breakers(state, plan)
            if (
                breakers
                and all(
                    _reliability_recovery_is_exactly_exhausted(state, plan, breaker)
                    for breaker in breakers
                )
            ):
                return _terminalize_unroutable_technical_recovery(
                    task_dir, state, plan, lifecycle=lifecycle,
                )
            return _lifecycle_error(
                lifecycle,
                "technical_forward_progress_route_unavailable",
                "Cortex could not derive the next exact bounded route from the current durable frontier",
                phase="forward_progress",
                recoverable=True,
                next_lifecycle=lifecycle,
                task_id=str(state.get("task_id") or task_id),
                diagnostics=[{
                    "code": "technical_forward_progress_route_unavailable",
                    "phase": "forward_progress",
                    "message": "retry the same lifecycle after durable frontier reconciliation",
                }],
            )
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        # The original result remains the durable diagnostic if reconciliation
        # itself races with another writer. The next server call retries the
        # same idempotent route; never turn this guard into a new user block.
        return result
    return result


def _recover_internal_lifecycle_exception(
    params: dict[str, Any],
    lifecycle: str,
    mutating: bool,
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
    if not mutating or not task_id:
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
                lifecycle,
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
    root = ledger_root(params)
    # Resume may be the first server entry after a hook process captured Stop
    # and exited before evaluation. Consume that inbox under the same mutation
    # serialization used by continue/recovery, then reload the resulting exact
    # task snapshot for ordinary resume routing.
    with state_lock(root):
        _, replay_task_dir, replay_state = load_state(task_id, params)
        authorize(replay_state, params)
        from cortex_runtime.native_lifecycle_observer import (
            ensure_current_host_epoch,
            reconcile_native_stop_inbox,
        )

        if not isinstance(ensure_current_host_epoch(
            root,
            str(replay_state.get("coordinator_host_thread_id") or ""),
            source="resume_orchestration",
            allow_handoff=True,
            hook_owned=False,
        ), Mapping):
            raise ValueError("native host epoch is unavailable or concurrently owned")

        reconcile_native_stop_inbox(root, replay_task_dir, replay_state)
        replay_plan = _load_orchestrate_plan(replay_task_dir, replay_state)
        resumed_recovery = recover_resumed_host_epoch(
            params, replay_task_dir, replay_state, replay_plan,
        )
        if resumed_recovery is not None:
            recovered_state, recovered_plan, prepared, receipt = resumed_recovery
            if str(receipt.get("mode") or "") == "answered_question_recovery_required":
                question = receipt.get("question")
                if not isinstance(question, Mapping) or not str(question.get("question_ref") or ""):
                    raise ValueError("answered retired-host question recovery lost its exact reference")
                answered_recovery = recover_answered_epoch_question(
                    params,
                    replay_task_dir,
                    recovered_state,
                    str(question["question_ref"]),
                )
                if answered_recovery is None:
                    raise ValueError("answered retired-host question has no deterministic recovery")
                return answered_recovery
            lifecycle_result = (
                {
                    "question": dict(receipt["question"]),
                    "question_refs": [
                        str(item) for item in receipt.get("question_refs") or [] if str(item)
                    ],
                }
                if str(receipt.get("mode") or "") == "awaiting_question_answer"
                and isinstance(receipt.get("question"), Mapping)
                else {"recovery": receipt}
            )
            return _orchestrate_response(
                "resume",
                recovered_state,
                wave_id=prepared.get("wave_id"),
                spawn_requests=prepared.get("spawn_requests") or [],
                result=lifecycle_result,
                plan=recovered_plan,
            )
    _, task_dir, state = load_state(task_id, params)
    authorize(state, params)
    task = load_task_definition(task_dir, state)
    plan = _load_orchestrate_plan(task_dir, state)
    terminal_recovery_requested = bool(params.get("terminal_recovery"))
    breakers = _active_terminal_recovery_breakers(state, plan)
    governance_breaker = next((
        breaker for breaker in breakers
        if str(breaker.get("kind") or "") in {
            "governance_closure_identical_basis_retry_exhausted",
            "governance_closure_terminal_blocked",
        }
    ), None)
    if governance_breaker is not None:
        return _orchestrate_response(
            "resume",
            state,
            wave_id=None,
            result={
                "recovery": {
                    "schema": "cortex/recovery-contract/v1",
                    "mode": "terminal_reliability_breaker",
                    "status": "terminal",
                    "retryable": False,
                    "state_mutated": False,
                    "fingerprint": str(governance_breaker.get("fingerprint") or ""),
                },
            },
            plan=plan,
        )
    if breakers:
        dispatched = _dispatch_server_owned_recovery(
            params,
            task_dir,
            state,
            plan,
            reason="Resume every active exact technical occurrence through one atomic recovery batch.",
        )
        if dispatched is None:
            raise ValueError("active technical recovery batch has no deterministic route")
        state, plan, prepared, terminal_recovery = dispatched
        return _orchestrate_response(
            "resume",
            state,
            wave_id=prepared["wave_id"],
            spawn_requests=prepared["spawn_requests"],
            result={"recovery": terminal_recovery},
            plan=plan,
        )
    terminal_recovery: dict[str, Any] | None = None
    if terminal_recovery_requested:
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
                "Do not supply a replacement pipeline, spawn a replacement worker, or dispatch the origin gate directly."
            ),
        }
    # Resume invalidates blocked attempts and retries only the server-owned
    # durable pipeline. Validate dispatch context before any transition so a
    # compiler rejection preserves the exact recovery state for retry.
    _preflight_dispatch_context(task, state)
    original_status = str(state.get("status") or "")
    recovery_wave, _recovery_frontier = _effective_plan_frontier(plan, state)
    recovery_authority = (
        _compiled_wave_occurrence_authority(recovery_wave)
        if isinstance(recovery_wave, Mapping)
        else None
    )
    recovery_slots = {
        (
            str(item.get("logical_delegation_key") or ""),
            str(item.get("plan_assignment_lineage_digest") or ""),
        )
        for item in (recovery_authority or {}).get("assignment_lineages") or []
        if isinstance(item, Mapping)
    }
    recovery_attempts = [
        attempt for attempt in state.get("attempts") or []
        if isinstance(attempt, dict)
        and isinstance(recovery_authority, Mapping)
        and str(attempt.get("wave_ref") or "") == recovery_authority["wave_ref"]
        and str(attempt.get("orchestration_wave_id") or "") == recovery_authority["wave_ref"]
        and str(attempt.get("phase_ref") or "") == recovery_authority["phase_ref"]
        and (
            str(attempt.get("logical_delegation_key") or ""),
            str(attempt.get("plan_assignment_lineage_digest") or ""),
        ) in recovery_slots
    ]
    active_recovery = (
        original_status in {"active", "needs_input"}
        and isinstance(recovery_authority, Mapping)
        and not any(
            not attempt.get("invalidated")
            and attempt.get("status") in {AWAITING_HOST_SPAWN, "running", "waiting_question"}
            for attempt in recovery_attempts
        )
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
        resumed = reopen_blocked_lifecycle_state({
            **params,
            "task_id": task_id,
            "expected_revision": state["revision"],
            "reason": params.get("reason") or "Unified facade resumed the blocked task.",
        })
        resumed_state = resumed["state"]
    resumed_wave, _resumed_frontier = _effective_plan_frontier(plan, resumed_state)
    resumed_authority = (
        _compiled_wave_occurrence_authority(resumed_wave)
        if isinstance(resumed_wave, Mapping)
        else None
    )
    failure_counts = resumed_state.setdefault("orchestrate_occurrence_failure_counts", {})
    resume_state_changed = False
    if isinstance(resumed_authority, Mapping):
        for gate in resumed_authority["gates"]:
            resume_state_changed = (
                failure_counts.pop(_occurrence_gate_key(resumed_authority, gate), None) is not None
                or resume_state_changed
            )
    if not failure_counts:
        resumed_state.pop("orchestrate_occurrence_failure_counts", None)
    invalidated = False
    for attempt in resumed_state.get("attempts", []):
        exact_occurrence = bool(
            isinstance(resumed_authority, Mapping)
            and str(attempt.get("wave_ref") or "") == resumed_authority["wave_ref"]
            and str(attempt.get("orchestration_wave_id") or "") == resumed_authority["wave_ref"]
            and str(attempt.get("phase_ref") or "") == resumed_authority["phase_ref"]
            and (
                str(attempt.get("logical_delegation_key") or ""),
                str(attempt.get("plan_assignment_lineage_digest") or ""),
            ) in {
                (
                    str(item.get("logical_delegation_key") or ""),
                    str(item.get("plan_assignment_lineage_digest") or ""),
                )
                for item in resumed_authority.get("assignment_lineages") or []
                if isinstance(item, Mapping)
            }
        )
        if exact_occurrence and attempt.get("status") == "blocked" and not attempt.get("invalidated"):
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
        "schema": LIFECYCLE_RUNTIME_SCHEMA,
        "ok": True,
        "lifecycle": "lane",
        "transaction_id": None,
        "task_id": state.get("task_id") or params.get("task_id"),
        "wave_id": None,
        "state": "completed",
        "spawn_requests": [],
        "diagnostics": [],
        "result": result,
        "next_action": "continue the lane lifecycle with manage_orchestration intent lane when needed",
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
        for key in ("project_root", "task_id", "principal", "submission_id")
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
        }))[:24]
        call_payload["resume_context"] = {
            "source": "ordinary_chat_message",
            "interaction_ref": payload.get("question_id"),
            "user_language": str(state.get("user_language") or "en"),
        }
    result = handlers[command](call_payload)
    return _orchestrate_response("question", state, result=result)


def _run_v11_lifecycle(
    params: dict[str, Any],
    lifecycle: str,
    handler: Callable[[dict[str, Any], Path | None, dict[str, Any] | None], dict[str, Any]],
    *,
    mutating: bool,
) -> dict[str, Any]:
    """Execute one code-selected v11 lifecycle; request JSON cannot select another."""
    transaction_path: Path | None = None
    transaction: dict[str, Any] | None = None
    try:
        preflight_diagnostics = _collect_lifecycle_diagnostics(lifecycle, params)
        if preflight_diagnostics:
            return _lifecycle_error(
                lifecycle,
                "lifecycle_validation_failed",
                "request failed preflight validation",
                phase="preflight",
                recoverable=True,
                next_lifecycle=lifecycle,
                task_id=str(params.get("task_id") or (params.get("task") or {}).get("task_id") or "") or None,
                diagnostics=preflight_diagnostics,
            )
        select_project_root(params)
        if mutating:
            if not str(params.get("submission_id", "")).strip():
                raise ValueError(f"{lifecycle} requires submission_id")
            root = ledger_root(params)
            transaction_path, transaction, replay = _begin_orchestrate_transaction(
                root, params, lifecycle,
            )
            if replay is not None:
                return _materialize_response_result_projection(params, replay)
        result = handler(params, transaction_path, transaction)
        result = _ensure_technical_forward_progress(params, lifecycle, result)
        if mutating:
            committed = _commit_orchestrate_transaction(transaction_path, transaction, result)
            return _materialize_response_result_projection(params, committed)
        return _materialize_response_result_projection(
            params,
            {**result, "transaction_id": None, "idempotent": False},
        )
    except ReworkRequestIdempotent as exc:
        result = _orchestrate_response(
            lifecycle,
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
        if transaction_path is not None and transaction is not None:
            committed = _commit_orchestrate_transaction(transaction_path, transaction, result)
            return _materialize_response_result_projection(params, committed)
        return result
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        task_id = str(params.get("task_id") or (params.get("task") or {}).get("task_id") or "") or None
        recovered = _recover_internal_lifecycle_exception(
            params, lifecycle, mutating, task_id, transaction,
        )
        if recovered is not None:
            if transaction_path is not None and transaction is not None:
                return _commit_orchestrate_transaction(transaction_path, transaction, recovered)
            return recovered
        collected = getattr(exc, "diagnostics", None)
        error = _lifecycle_error(
            lifecycle,
            "lifecycle_validation_failed",
            exc,
            phase=(transaction or {}).get("phase", "preflight"),
            recoverable=True,
            next_lifecycle=lifecycle,
            task_id=task_id,
            diagnostics=[dict(item) for item in collected] if isinstance(collected, list) and collected else None,
        )
        if transaction_path is not None and transaction is not None:
            transaction.update({
                "status": "failed",
                "result": error,
                "updated_at": now(),
                "failed_at": now(),
            })
            db_put_operation(
                transaction_path,
                safe_id(str(transaction["submission_id"])),
                transaction,
            )
            error["transaction_id"] = transaction.get("transaction_id")
        return error


def start_lifecycle(params: dict[str, Any]) -> dict[str, Any]:
    return _run_v11_lifecycle(
        params,
        "start",
        lambda payload, path, receipt: _orchestrate_start(payload, path, receipt),
        mutating=True,
    )


def continue_lifecycle(params: dict[str, Any]) -> dict[str, Any]:
    return _run_v11_lifecycle(
        params,
        "continue",
        lambda payload, path, receipt: _orchestrate_continue(payload, path, receipt),
        mutating=True,
    )


def inspect_lifecycle(params: dict[str, Any]) -> dict[str, Any]:
    return _run_v11_lifecycle(
        params,
        "inspect",
        lambda payload, _path, _receipt: _orchestrate_inspect(payload),
        mutating=False,
    )


def manage_lifecycle(intent: str, params: dict[str, Any]) -> dict[str, Any]:
    """Execute one schema-validated management intent without a generic operation field."""
    if intent == "resume":
        handler = lambda payload, _path, _receipt: _orchestrate_resume(payload)
        mutating = True
    elif intent == "deactivate":
        def handler(
            payload: dict[str, Any],
            _path: Path | None,
            _receipt: dict[str, Any] | None,
        ) -> dict[str, Any]:
            return {
                "schema": LIFECYCLE_RUNTIME_SCHEMA,
                "ok": True,
                "lifecycle": "deactivate",
                "transaction_id": None,
                "task_id": payload.get("task_id"),
                "wave_id": None,
                "state": "completed",
                "spawn_requests": [],
                "diagnostics": [],
                "result": deactivate_orchestration({
                    **payload,
                    "user_command": NORMAL_COMMAND,
                }),
                "next_action": "Cortex orchestration is inactive for this coordinator",
            }
        mutating = True
    elif intent == "lane":
        handler = lambda payload, _path, _receipt: _orchestrate_lane(payload)
        command = str((params.get("payload") or {}).get("command") or "")
        mutating = command != "inspect"
    elif intent == "resource":
        handler = lambda payload, _path, _receipt: _orchestrate_resource(payload)
        mutating = True
    elif intent == "question":
        handler = lambda payload, _path, _receipt: _orchestrate_question(payload)
        command = str((params.get("payload") or {}).get("command") or "ask")
        mutating = command not in {"list", "updates"}
    elif intent == "plan_approval":
        handler = lambda payload, _path, _receipt: _orchestrate_plan_approval(payload)
        mutating = True
    else:
        raise ValueError("unsupported v11 management lifecycle")
    return _run_v11_lifecycle(params, intent, handler, mutating=mutating)
