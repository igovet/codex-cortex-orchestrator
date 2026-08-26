"""Gate-transition policy behind the public Cortex facade.

The public ``record_gate`` symbol intentionally remains in :mod:`cortex` for
MCP and existing integrations.  This module keeps the policy itself focused:
it resolves the active gate, validates evidence and C2/C3 obligations, then
applies one durable state transition.  Runtime references stay late-bound so
the stdio entrypoint remains the single composition root.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cortex_runtime.core.runtime_bindings import bind_symbols


bind_symbols(
    "gate_transitions",
    globals(),
    (
        "AWAITING_HOST_SPAWN",
        "TERMINAL_ATTEMPT_STATUSES",
        "_active_facade_attempts_missing_finalized_results",
        "_attempts_missing_result_validation",
        "_attempts_with_unresolved_canonical_results",
        "_governance_obligations_for_gate",
        "_validated_evidence_records",
        "active_gates",
        "append_pipeline_change",
        "apply_pipeline_operations",
        "authorize",
        "capture_project_manifest",
        "db_list_task_findings",
        "db_task_findings_blockers",
        "ledger_root",
        "load_state",
        "load_task_definition",
        "now",
        "reconcile_manifest",
        "redact",
        "remove_active_mapping",
        "save_state",
        "state_lock",
        "sync_current_wave",
        "task_manifest_baseline",
        "validate_completion_invariants",
        "validate_governance_obligation_evidence",
    ),
)


_OUTCOMES = {"passed", "failed", "blocked", "skipped"}
_CLOSURE_VERIFIER_GATES = frozenset({
    "review", "governance_activation", "governance_close", "close",
})


def _recoverable(
    state: dict[str, Any],
    revision_correction: dict[str, Any] | None,
    *,
    reason: str,
    gate: str,
    next_action: str,
    **extra: Any,
) -> dict[str, Any]:
    """Return the stable, non-mutating recovery shape used by the MCP API."""
    return {
        "recorded": False,
        "reason": reason,
        "gate": gate,
        "next_action": next_action,
        "recoverable": True,
        "revision_correction": revision_correction,
        "state": state,
        **extra,
    }


def _policy_advisory(
    state: dict[str, Any],
    revision_correction: dict[str, Any] | None,
    *,
    reason: str,
    gate: str,
    recommended_next: str,
    warning: str,
    **extra: Any,
) -> dict[str, Any]:
    """Return a governance recommendation without vetoing the coordinator."""
    return {
        "advisory": True,
        "policy_veto": False,
        "requires_user_decision": False,
        "severity": "warning",
        "recoverable": False,
        "recorded": True,
        "reason": reason,
        "gate": gate,
        "warning": warning,
        "recommended_next": recommended_next,
        "revision_correction": revision_correction,
        "state": state,
        **extra,
    }


def _resolve_active_gate(
    state: dict[str, Any], params: dict[str, Any]
) -> tuple[str, str, dict[str, Any] | None]:
    expected_revision = params.get("expected_revision")
    revision_correction = (
        {"requested": expected_revision, "used": state["revision"]}
        if expected_revision is not None and state["revision"] != expected_revision
        else None
    )
    requested_gate = str(params["gate"])
    current_wave = active_gates(state)
    # ``record_gate`` carries authority to advance a particular gate.  Never
    # silently substitute the first active gate for a caller-supplied inactive
    # one: doing so can commit a valid-looking transition to the wrong gate.
    # The caller turns the empty resolution into a non-mutating,
    # self-correcting ``gate_mismatch`` response below.
    gate = requested_gate if requested_gate in current_wave else ""
    occurrence = _active_occurrence_identity(state, gate) if gate else None
    if occurrence is not None:
        for field in (
            "occurrence_key", "wave_ref", "phase_ref", "assignment_lineage_digest",
        ):
            supplied = str(params.get(field) or "").strip()
            if supplied and supplied != str(occurrence.get(field) or ""):
                raise ValueError("gate transition identity does not match the active occurrence")
    return requested_gate, gate, revision_correction


def _active_occurrence_identity(state: dict[str, Any], gate: str) -> dict[str, Any] | None:
    """Return the exact current compiled occurrence for a semantic gate."""
    completed = set(state.get("completed_orchestration_wave_ids") or [])
    skipped = set(state.get("skipped_orchestration_wave_ids") or [])
    for occurrence in state.get("orchestration_wave_occurrences") or []:
        if not isinstance(occurrence, dict):
            continue
        wave_ref = str(occurrence.get("wave_ref") or occurrence.get("wave_id") or "")
        phase_ref = str(occurrence.get("phase_ref") or "")
        phase_kind = str(occurrence.get("phase_kind") or "")
        gates = [str(item) for item in occurrence.get("gates") or []]
        if wave_ref in completed or wave_ref in skipped:
            continue
        if gate in gates and wave_ref and phase_ref:
            occurrence_key = str(occurrence.get("occurrence_key") or "").strip()
            lineage_digest = str(occurrence.get("assignment_lineage_digest") or "").strip()
            lineages = occurrence.get("assignment_lineages")
            if (
                not occurrence_key
                or not lineage_digest
                or not isinstance(lineages, list)
                or not lineages
            ):
                raise ValueError("active occurrence lacks compiled assignment authority")
            return {
                **occurrence,
                "wave_ref": wave_ref,
                "phase_ref": phase_ref,
                "phase_kind": phase_kind,
                "occurrence_key": occurrence_key,
                "assignment_lineage_digest": lineage_digest,
            }
        break
    return None


def _gate_mismatch(
    state: dict[str, Any],
    revision_correction: dict[str, Any] | None,
    *,
    requested_gate: str,
) -> dict[str, Any]:
    """Return a precise no-op when a gate transition targets an inactive gate."""
    current_wave = active_gates(state)
    return _recoverable(
        state,
        revision_correction,
        reason="gate_mismatch",
        gate=requested_gate,
        next_action="retry_with_active_gate",
        requested_gate=requested_gate,
        active_gates=current_wave,
        state_changed=False,
        retryable=True,
    )


def _validate_skip(
    state: dict[str, Any],
    params: dict[str, Any],
    gate: str,
    outcome: str,
    revision_correction: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if outcome != "skipped":
        return None
    governance = state.get("governance") if isinstance(state.get("governance"), dict) else {}
    if gate == "close" or (gate == "documentation" and state.get("require_delegation")) or (
        governance.get("effective_mode") == "full" and gate in {"governance_activation", "governance_close"}
    ):
        return _policy_advisory(
            state,
            revision_correction,
            reason="governance_skip_recommended",
            gate=gate,
            recommended_next="continue_orchestration",
            warning="The selected gate is conventionally recommended by governance, but the coordinator chose to skip it.",
        )
    if state.get("require_delegation") and not str(params.get("skip_reason", "")).strip():
        return _policy_advisory(
            state,
            revision_correction,
            reason="skip_reason_missing",
            gate=gate,
            recommended_next="continue_orchestration",
            warning="C2/C3 normally records a reason for a skipped gate; the transition remains executable.",
        )
    return None


def _gate_inputs(
    task_dir: Path, state: dict[str, Any], gate: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    occurrence = _active_occurrence_identity(state, gate)
    occurrence_slots = {
        (
            str(item.get("logical_delegation_key") or ""),
            str(item.get("plan_assignment_lineage_digest") or ""),
        )
        for item in (occurrence or {}).get("assignment_lineages") or []
        if isinstance(item, dict)
    }

    def current_attempt(item: dict[str, Any]) -> bool:
        if item.get("gate") != gate or item.get("invalidated"):
            return False
        if occurrence is None:
            return not state.get("orchestration_wave_occurrences")
        return (
            str(item.get("wave_ref") or "") == occurrence["wave_ref"]
            and str(item.get("orchestration_wave_id") or "") == occurrence["wave_ref"]
            and str(item.get("phase_ref") or "") == occurrence["phase_ref"]
            and str(item.get("phase_kind") or "") == occurrence["phase_kind"]
            and (
                str(item.get("logical_delegation_key") or ""),
                str(item.get("plan_assignment_lineage_digest") or ""),
            ) in occurrence_slots
        )

    gate_evidence = [
        item for item in _validated_evidence_records(task_dir, state)
        if item.get("gate") == gate and not item.get("invalidated")
        and any(
            current_attempt(attempt)
            and str(attempt.get("attempt_id") or "") == str(item.get("attempt_id") or "")
            for attempt in state.get("attempts") or [] if isinstance(attempt, dict)
        )
    ]
    gate_attempts = [
        item for item in state.get("attempts", [])
        if isinstance(item, dict) and current_attempt(item)
    ]
    non_terminal_attempts = [
        item for item in gate_attempts
        if item.get("status") not in TERMINAL_ATTEMPT_STATUSES
    ]
    terminal_non_success_attempts = [
        item for item in gate_attempts
        if item.get("status") in TERMINAL_ATTEMPT_STATUSES - {"passed"}
    ]
    passed_attempts = [item for item in gate_attempts if item.get("status") == "passed"]
    return gate_evidence, gate_attempts, non_terminal_attempts, terminal_non_success_attempts, passed_attempts


def _documentation_recovery(
    state: dict[str, Any],
    revision_correction: dict[str, Any] | None,
    reason: str,
    gate: str,
    candidates: list[str],
) -> dict[str, Any]:
    return _recoverable(
        state,
        revision_correction,
        reason=reason,
        gate=gate,
        next_action="record_evidence",
        candidate_attempt_ids=candidates,
    )


def _validate_pass_evidence(
    task_dir: Path,
    state: dict[str, Any],
    params: dict[str, Any],
    *,
    requested_gate: str,
    gate: str,
    outcome: str,
    revision_correction: dict[str, Any] | None,
    gate_evidence: list[dict[str, Any]],
    gate_attempts: list[dict[str, Any]],
    non_terminal_attempts: list[dict[str, Any]],
    terminal_non_success_attempts: list[dict[str, Any]],
    passed_attempts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Validate the evidence branch, returning only evidence tied to this transition."""
    if outcome != "passed":
        return gate_evidence, None
    # A passed gate always needs positive, gate-scoped evidence.  In
    # particular, terminal failed/cancelled attempts are not evidence of
    # success: accepting that state would allow a gate to close after every
    # delegated worker failed (the old branch below treated that as an
    # implicit pass).
    if not gate_evidence:
        return gate_evidence, _policy_advisory(
            state,
            revision_correction,
            reason="evidence_recommended",
            gate=gate,
            recommended_next="continue_orchestration",
            warning="The gate has no positive evidence; the coordinator may continue, and the omission is recorded as a risk.",
            gate_correction=(
                {"requested": requested_gate, "used": gate}
                if requested_gate != gate else None
            ),
            candidate_attempt_ids=[
                item["attempt_id"] for item in non_terminal_attempts + passed_attempts
            ],
        )
    if any(
        item.get("kind") == "command"
        and item.get("exit_code") not in (0, None)
        for item in gate_evidence
    ):
        raise ValueError("cannot pass a gate with a worker-attested failed command")
    pending_attempt_ids = _active_facade_attempts_missing_finalized_results(
        task_dir, gate_attempts
    )
    if pending_attempt_ids:
        # Evidence can be recorded while a worker is running (for example a
        # server-observed verification command), but it cannot turn that live
        # worker into a pass. The native child must publish and finalize its
        # AttemptResult first; otherwise `_apply_transition` would coerce the
        # mutable attempt projection to `passed` without canonical proof.
        return [], _recoverable(
            state,
            revision_correction,
            reason="active_attempt_result_pending",
            gate=gate,
            next_action="wait_for_exact_worker_or_recover_attempt",
            candidate_attempt_ids=pending_attempt_ids,
        )
    # ``completed`` is scoped to a worker's assignment: ordinary implementation
    # and documentation results may intentionally carry unresolved work forward
    # to their successor.  A closure verifier is different.  Its own immutable
    # canonical result is the assertion that the remaining work is acceptably
    # closed, so it cannot pass while it still records unresolved items.
    #
    # Keep this check before every state mutation and restrict it to the exact
    # passed attempts for this gate.  In particular, do not reinterpret an
    # earlier implementation/documentation result as a closure failure.
    if gate in _CLOSURE_VERIFIER_GATES:
        unresolved = _attempts_with_unresolved_canonical_results(task_dir, passed_attempts)
        if unresolved:
            return gate_evidence, _recoverable(
                state,
                revision_correction,
                reason="closure_attempt_unresolved",
                gate=gate,
                next_action="rework_current_gate",
                candidate_attempt_ids=unresolved,
            )
    if not state.get("require_delegation"):
        return gate_evidence, None
    if not gate_attempts:
        if gate == "documentation":
            return gate_evidence, _policy_advisory(
                state,
                revision_correction,
                reason="documentation_attempt_required",
                gate=gate,
                recommended_next="continue_orchestration",
                warning="Documentation evidence is recommended for this governance level.",
            )
        return gate_evidence, _policy_advisory(
            state,
            revision_correction,
            reason="delegation_recommended",
            gate=gate,
            recommended_next="continue_orchestration",
            warning="Delegation is recommended for C2/C3, but it is not a backend authorization requirement.",
        )
    missing = [
        item["attempt_id"] for item in passed_attempts
        if not any(evidence.get("attempt_id") == item["attempt_id"] for evidence in gate_evidence)
    ]
    if missing:
        if gate == "documentation":
            return gate_evidence, _policy_advisory(
                state, revision_correction, reason="documentation_evidence_recommended", gate=gate,
                recommended_next="record_evidence",
                warning="Documentation evidence is recommended; the coordinator may continue.",
                candidate_attempt_ids=missing,
            )
        return gate_evidence, _policy_advisory(
            state, revision_correction, reason="attempt_evidence_recommended", gate=gate,
            recommended_next="record_evidence",
            warning="Some passed attempts have no linked evidence; the coordinator may continue with this risk recorded.",
            candidate_attempt_ids=missing,
        )
    missing_result_bindings = [
        item["attempt_id"] for item in passed_attempts
        if not any(
            evidence.get("attempt_id") == item["attempt_id"]
            and evidence.get("attempt_result_ref") == item.get("attempt_result_ref")
            for evidence in gate_evidence
        )
    ]
    if missing_result_bindings:
        if gate == "documentation":
            return gate_evidence, _policy_advisory(
                state, revision_correction, reason="documentation_result_evidence_recommended", gate=gate,
                recommended_next="record_evidence",
                warning="Documentation evidence should bind to its canonical result; the coordinator may continue.",
                candidate_attempt_ids=missing_result_bindings,
            )
        return gate_evidence, _policy_advisory(
            state, revision_correction, reason="canonical_result_evidence_recommended", gate=gate,
            recommended_next="record_evidence",
            warning="Some evidence is not bound to the canonical result; the coordinator may continue with this risk recorded.",
            candidate_attempt_ids=missing_result_bindings,
        )
    unvalidated_results = _attempts_missing_result_validation(task_dir, passed_attempts)
    if unvalidated_results:
        raise ValueError(
            "every passed facade attempt needs a server-validated result contract before the gate can pass: "
            + ", ".join(unvalidated_results)
        )
    evidence_attempt_ids = {item.get("attempt_id") for item in gate_evidence}
    unexplained = [
        item["attempt_id"] for item in non_terminal_attempts
        if item["attempt_id"] not in evidence_attempt_ids
    ]
    if unexplained:
        if gate == "documentation":
            return gate_evidence, _policy_advisory(
                state, revision_correction, reason="documentation_evidence_recommended", gate=gate,
                recommended_next="record_evidence",
                warning="Documentation evidence is recommended for active attempts; the coordinator may continue.",
                candidate_attempt_ids=unexplained,
            )
        return gate_evidence, _policy_advisory(
            state, revision_correction, reason="active_attempt_evidence_recommended", gate=gate,
            recommended_next="record_evidence",
            warning="Active delegated attempts lack linked evidence; the coordinator may continue and should reconcile them later.",
            candidate_attempt_ids=unexplained,
        )
    eligible_attempt_ids = {
        item["attempt_id"] for item in gate_attempts
        if item["attempt_id"] in evidence_attempt_ids
        and item.get("status") in {"running", "passed"}
    }
    if passed_attempts and not eligible_attempt_ids:
        if gate == "documentation":
            return gate_evidence, _policy_advisory(
                state, revision_correction, reason="documentation_evidence_recommended", gate=gate,
                recommended_next="record_evidence",
                warning="Documentation evidence is recommended before close; the coordinator may continue.",
                candidate_attempt_ids=[item["attempt_id"] for item in passed_attempts],
            )
        return gate_evidence, _policy_advisory(
            state, revision_correction, reason="delegated_evidence_recommended", gate=gate,
            recommended_next="record_evidence",
            warning="No delegated attempt is currently linked to evidence; the coordinator may continue.",
        )
    current_attempt_evidence = [
        item for item in gate_evidence if item.get("attempt_id") in eligible_attempt_ids
    ]
    if gate == "documentation":
        documentation = state.get("documentation_receipt")
        technical_writer_attempt_ids = {
            item["attempt_id"] for item in gate_attempts
            if item.get("agent") == "technical_writer"
        }
        if not documentation or documentation.get("attempt_id") not in technical_writer_attempt_ids:
            return gate_evidence, _policy_advisory(
                state, revision_correction, reason="documentation_receipt_recommended", gate=gate,
                recommended_next="record_evidence",
                warning="A documentation receipt is recommended; the coordinator may continue.",
                candidate_attempt_ids=[
                    item["attempt_id"] for item in gate_attempts
                    if item.get("agent") == "technical_writer"
                ],
            )
    return current_attempt_evidence, None


def _validate_handoff_and_close(
    task_dir: Path,
    state: dict[str, Any],
    *,
    gate: str,
    outcome: str,
    current_attempt_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    advisories: list[dict[str, Any]] = []
    if outcome == "blocked" and state.get("require_handoff") and (
        not state.get("handoff_created") or state.get("handoff_gate") != gate
    ):
        advisories.append({
            "reason": "handoff_recommended",
            "warning": "A current-gate handoff is recommended for this pause; the coordinator may continue.",
            "recommended_next": "record_handoff",
        })
    if outcome != "passed" or gate != "close" or not state.get("require_handoff"):
        return advisories
    if not state.get("handoff_created") or state.get("handoff_gate") != "close":
        advisories.append({"reason": "final_handoff_recommended", "warning": "A final handoff is recommended before close.", "recommended_next": "record_handoff"})
    if "documentation" not in state.get("completed_gates", []) or not state.get("documentation_receipt"):
        advisories.append({"reason": "documentation_decision_recommended", "warning": "Documentation decision evidence is recommended before close.", "recommended_next": "record_evidence"})
    if not state.get("reassessment_receipts"):
        advisories.append({"reason": "reassessment_recommended", "warning": "A reassessment receipt is recommended before close.", "recommended_next": "record_reassessment"})
    if not any(
        item.get("evidence_class") == "worker_attested"
        and str(item.get("attempt_result_ref") or "")
        for item in current_attempt_evidence
    ):
        advisories.append({"reason": "verification_recommended", "warning": "A canonical-result-bound worker verification attestation is recommended before close.", "recommended_next": "record_evidence"})
    manifest = state.get("final_manifest_receipt")
    if not manifest or not manifest.get("complete"):
        advisories.append({"reason": "manifest_capture_incomplete", "warning": "The handoff manifest is partial or incomplete; dispatch a corrective manifest recapture.", "recommended_next": "dispatch_manifest_recapture"})
        return advisories
    baseline_manifest = task_manifest_baseline(task_dir, state)
    baseline_partial = baseline_manifest.get("partial_manifest")
    if isinstance(baseline_partial, dict) and baseline_partial.get("partial"):
        advisories.append({
            "reason": "baseline_manifest_capture_incomplete",
            "warning": f"Baseline manifest capture was partial ({baseline_partial.get('reason') or 'capture limit'}); recapture it through a corrective worker.",
            "recommended_next": "dispatch_manifest_recapture",
        })
    current_manifest = capture_project_manifest(
        Path(load_task_definition(task_dir, state)["project_root"]),
        policy=baseline_manifest.get("policy"),
    )
    current_partial = current_manifest.get("partial_manifest")
    if isinstance(current_partial, dict) and current_partial.get("partial"):
        advisories.append({
            "reason": "current_manifest_capture_incomplete",
            "warning": f"Final manifest capture was partial ({current_partial.get('reason') or 'capture limit'}); recapture it through a corrective worker.",
            "recommended_next": "dispatch_manifest_recapture",
        })
    if current_manifest["digest"] != manifest.get("current_digest"):
        raise ValueError("project files changed after the final handoff; create a new complete handoff")
    return advisories


def _apply_transition(
    root: Path,
    task_dir: Path,
    state: dict[str, Any],
    params: dict[str, Any],
    *,
    gate: str,
    outcome: str,
    gate_evidence: list[dict[str, Any]],
    gate_attempts: list[dict[str, Any]],
) -> tuple[bool, list[dict[str, Any]]]:
    occurrence = _active_occurrence_identity(state, gate)
    receipt = {
        "outcome": outcome,
        "at": now(),
        "summary": redact(params.get("summary", ""), 2000),
        "skip_reason": redact(params.get("skip_reason", ""), 2000),
        "evidence_ids": [item["evidence_id"] for item in gate_evidence],
    }
    state["gates"][gate] = receipt
    if occurrence is not None:
        occurrence_gate_key = f"{occurrence['occurrence_key']}:{gate}"
        state.setdefault("gate_occurrences", {})[occurrence_gate_key] = {
            **receipt,
            "gate": gate,
            "phase_kind": occurrence["phase_kind"],
            "phase_ref": occurrence["phase_ref"],
            "wave_ref": occurrence["wave_ref"],
            "occurrence_key": occurrence["occurrence_key"],
            "assignment_lineage_digest": occurrence["assignment_lineage_digest"],
            "assignments": sorted(
                [
                    {
                        "attempt_id": str(item.get("attempt_id") or ""),
                        "attempt_result_ref": str(item.get("attempt_result_ref") or ""),
                        "logical_delegation_key": str(item.get("logical_delegation_key") or ""),
                        "plan_assignment_lineage_digest": str(item.get("plan_assignment_lineage_digest") or ""),
                        "protocol_status": str(item.get("protocol_status") or ""),
                        "acceptance_status": str(item.get("acceptance_status") or ""),
                    }
                    for item in gate_attempts
                ],
                key=lambda item: (
                    item["logical_delegation_key"],
                    item["plan_assignment_lineage_digest"],
                    item["attempt_id"],
                ),
            ),
        }

    def current_attempt(attempt: dict[str, Any]) -> bool:
        if attempt.get("gate") != gate or attempt.get("invalidated"):
            return False
        if occurrence is None:
            return not state.get("orchestration_wave_occurrences")
        return (
            str(attempt.get("wave_ref") or "") == occurrence["wave_ref"]
            and str(attempt.get("phase_ref") or "") == occurrence["phase_ref"]
            and any(
                str(attempt.get("logical_delegation_key") or "")
                == str(item.get("logical_delegation_key") or "")
                and str(attempt.get("plan_assignment_lineage_digest") or "")
                == str(item.get("plan_assignment_lineage_digest") or "")
                for item in occurrence["assignment_lineages"]
                if isinstance(item, dict)
            )
        )
    if outcome == "passed":
        if gate not in state["completed_gates"]:
            state["completed_gates"].append(gate)
        for attempt in state["attempts"]:
            if current_attempt(attempt) and attempt["status"] == "running":
                attempt["status"] = "passed"
    elif outcome == "skipped":
        if gate not in state["skipped_gates"]:
            state["skipped_gates"].append(gate)
    elif outcome == "blocked":
        # A worker's blocked outcome is an observation for corrective routing,
        # not permission for Cortex to stop the task. Only an explicit user
        # stop decision may place a task in blocked status.
        state.setdefault("policy_advice", []).append({
            "code": "worker_blocked_observation",
            "severity": "warning",
            "message": f"Worker reported gate {gate} as blocked; dispatch corrective work or resolve the task question.",
            "recommended_next": "dispatch_corrective_worker",
        })
        state["completed_gates"] = [
            item for item in state.get("completed_gates") or [] if item != gate
        ]
        state["skipped_gates"] = [
            item for item in state.get("skipped_gates") or [] if item != gate
        ]
    else:
        state["completed_gates"] = [
            item for item in state.get("completed_gates") or [] if item != gate
        ]
        state["skipped_gates"] = [
            item for item in state.get("skipped_gates") or [] if item != gate
        ]
        for attempt in state["attempts"]:
            # A gate can fail after a worker submitted a syntactically valid
            # canonical result: for example, when its inherited corrective
            # finding remains open. Retire that result with the failed gate so
            # the next bounded attempt is a new worker, never the same stale pass.
            if (
                current_attempt(attempt)
                and attempt["status"] in {"running", AWAITING_HOST_SPAWN, "passed"}
            ):
                attempt["status"] = "failed"
    operations = params.get("pipeline_operations", [])
    if operations:
        change = apply_pipeline_operations(
            state,
            operations=operations,
            allow_rework=bool(params.get("allow_rework", False)),
        )
        append_pipeline_change(
            state,
            change,
            str(params.get("pipeline_reason", "adaptive gate outcome")),
            params.get("signals", []),
        )
    if outcome in {"passed", "skipped"}:
        candidate_wave = sync_current_wave(state)
        if not candidate_wave:
            validate_completion_invariants(state, artifact_root=root)
            state["status"] = "completed"
    else:
        sync_current_wave(state)
    return state["status"] == "completed", operations


def _persist_transition(
    root: Path,
    task_dir: Path,
    state: dict[str, Any],
    *,
    gate: str,
    outcome: str,
    operations: list[dict[str, Any]],
    completed: bool,
) -> None:
    if completed:
        closed_receipt, _ = reconcile_manifest(task_dir, state, [])
        if not (closed_receipt.get("comparison") or {}).get("complete", False):
            # A bounded/partial capture is a recoverable observation, not a
            # Cortex policy veto. Keep the canonical receipt and continue the
            # lifecycle with an explicit corrective-worker recommendation.
            state.setdefault("policy_advice", []).append({
                "code": "final_manifest_capture_incomplete",
                "severity": "warning",
                "message": "Final manifest capture is partial; dispatch corrective manifest recapture.",
                "recommended_next": "dispatch_manifest_recapture",
            })
            state["status"] = "active"
            completed = False
        else:
            closed_paths = list(closed_receipt["comparison"]["changed_paths"])
            closed_receipt["observed_paths"] = closed_paths
            closed_receipt["unaccounted_paths"] = []
            closed_receipt["complete"] = True
            state["closed_manifest_receipt"] = closed_receipt
            state["manifest_snapshot_cleanup"] = {"status": "pending", "at": now()}
    save_state(
        task_dir,
        task_dir / "state.sqlite",
        state,
        "gate",
        f"{gate}: {outcome}" + ("; pipeline adapted" if operations else ""),
    )
    if not completed:
        return
    remove_active_mapping(root, state["task_id"])


def record_gate(params: dict[str, Any]) -> dict[str, Any]:
    """Validate and commit one gate outcome through focused policy phases."""
    root = ledger_root(params)
    with state_lock(root):
        root, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        requested_gate, gate, revision_correction = _resolve_active_gate(state, params)
        if not gate:
            return _gate_mismatch(
                state,
                revision_correction,
                requested_gate=requested_gate,
            )
        outcome = str(params["outcome"])
        if outcome not in _OUTCOMES:
            raise ValueError("outcome must be passed, failed, blocked, or skipped")
        if gate in {"governance_activation", "governance_close", "close"}:
            _governance_obligations_for_gate(state, gate)
        advisories: list[dict[str, Any]] = []
        skipped = _validate_skip(state, params, gate, outcome, revision_correction)
        if skipped is not None:
            advisories.append(skipped)
        inputs = _gate_inputs(task_dir, state, gate)
        current_attempt_evidence, recovery = _validate_pass_evidence(
            task_dir,
            state,
            params,
            requested_gate=requested_gate,
            gate=gate,
            outcome=outcome,
            revision_correction=revision_correction,
            gate_evidence=inputs[0],
            gate_attempts=inputs[1],
            non_terminal_attempts=inputs[2],
            terminal_non_success_attempts=inputs[3],
            passed_attempts=inputs[4],
        )
        if recovery is not None:
            if recovery.get("advisory"):
                advisories.append(recovery)
            else:
                return recovery
        if outcome == "passed":
            # Governance obligations are advisory.  When evidence is supplied,
            # still validate its immutable binding; a missing obligation is a
            # recommendation, while a malformed artifact remains integrity
            # evidence and is handled by the validator's hard error.
            supplied_evidence = None if gate == "close" else current_attempt_evidence
            if supplied_evidence:
                try:
                    validate_governance_obligation_evidence(
                        state, gate, supplied_evidence, artifact_root=root,
                    )
                except ValueError as exc:
                    if "requires typed governance obligation evidence" in str(exc):
                        advisories.append({
                            "reason": "governance_evidence_recommended",
                            "warning": str(exc),
                            "recommended_next": "record_evidence",
                        })
                    else:
                        raise
        advisories.extend(_validate_handoff_and_close(
            task_dir,
            state,
            gate=gate,
            outcome=outcome,
            current_attempt_evidence=current_attempt_evidence,
        ))
        if outcome == "passed" and gate in {"governance_close", "close"}:
            blockers = db_task_findings_blockers(root, state["task_id"])
            if blockers:
                raise ValueError("close_blocked_by_open_canonical_findings")
        if outcome in {"passed", "failed"} and (
            gate in {"review", "governance_activation", "governance_close", "close"}
            or bool(params.get("enforce_canonical_findings"))
        ):
            blockers = db_task_findings_blockers(root, state["task_id"])
            if blockers:
                advisories.append({
                    "reason": "canonical_findings_recommended",
                    "warning": "Canonical findings remain open; the coordinator may continue, but should schedule corrective work.",
                    "recommended_next": "resolve_findings_then_rerun_review_and_close",
                    "blockers": blockers,
                })
        completed, operations = _apply_transition(
            root,
            task_dir,
            state,
            params,
            gate=gate,
            outcome=outcome,
            gate_evidence=inputs[0],
            gate_attempts=inputs[1],
        )
        _persist_transition(
            root,
            task_dir,
            state,
            gate=gate,
            outcome=outcome,
            operations=operations,
            completed=completed,
        )
        result = {"state": state, "revision_correction": revision_correction}
        if advisories:
            result["advisories"] = advisories
            result["recommended_next"] = [
                item.get("recommended_next") for item in advisories
                if item.get("recommended_next")
            ]
        return result
