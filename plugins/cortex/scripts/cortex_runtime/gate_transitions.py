"""Gate-transition policy behind the public Cortex facade.

The public ``record_gate`` symbol intentionally remains in :mod:`cortex` for
MCP and existing integrations.  This module keeps the policy itself focused:
it resolves the active gate, validates evidence and C2/C3 obligations, then
applies one durable state transition.  Runtime references stay late-bound so
the stdio entrypoint remains the single composition root.
"""
from __future__ import annotations

import posixpath
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
        "_validated_evidence_records",
        "active_gates",
        "append_pipeline_change",
        "apply_pipeline_operations",
        "authorize",
        "capture_project_manifest",
        "cleanup_completed_manifest_snapshots",
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
    return requested_gate, gate, revision_correction


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
            recommended_next="record_delegation",
            warning="The selected gate is conventionally recommended by governance, but the coordinator chose to skip it.",
        )
    if state.get("require_delegation") and not str(params.get("skip_reason", "")).strip():
        return _policy_advisory(
            state,
            revision_correction,
            reason="skip_reason_missing",
            gate=gate,
            recommended_next="record_delegation_if_needed",
            warning="C2/C3 normally records a reason for a skipped gate; the transition remains executable.",
        )
    return None


def _gate_inputs(
    task_dir: Path, state: dict[str, Any], gate: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    gate_evidence = [
        item for item in _validated_evidence_records(task_dir, state)
        if item.get("gate") == gate and not item.get("invalidated")
    ]
    gate_attempts = [
        item for item in state.get("attempts", [])
        if item.get("gate") == gate and not item.get("invalidated")
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
            recommended_next="record_delegation" if not gate_attempts else "record_evidence",
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
        and (item.get("exit_code") != 0 or not item.get("verified_execution"))
        for item in gate_evidence
    ):
        raise ValueError("cannot pass a gate with failed or self-attested command evidence; use execute_verification_command")
    pending_attempt_ids = _active_facade_attempts_missing_finalized_results(
        task_dir, gate_attempts
    )
    if pending_attempt_ids:
        # Evidence can be recorded while a worker is running (for example a
        # server-observed verification command), but it cannot turn that live
        # worker into a pass. The exact child must publish and finalize its
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
            return gate_evidence, _policy_advisory(
                state,
                revision_correction,
                reason="closure_attempt_unresolved",
                gate=gate,
                recommended_next="rework_current_gate",
                warning="A closure result still reports unresolved work; the coordinator may continue, but should schedule corrective work.",
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
                recommended_next="record_delegation",
                warning="Documentation evidence is recommended for this governance level.",
            )
        return gate_evidence, _policy_advisory(
            state,
            revision_correction,
            reason="delegation_recommended",
            gate=gate,
            recommended_next="record_delegation",
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
        item.get("kind") == "command" and item.get("verified_execution")
        and item.get("exit_code") == 0
        for item in current_attempt_evidence
    ):
        advisories.append({"reason": "verification_recommended", "warning": "Server-observed verification evidence is recommended before close.", "recommended_next": "record_evidence"})
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
) -> tuple[bool, list[dict[str, Any]]]:
    state["gates"][gate] = {
        "outcome": outcome,
        "at": now(),
        "summary": redact(params.get("summary", ""), 2000),
        "skip_reason": redact(params.get("skip_reason", ""), 2000),
        "evidence_ids": [item["evidence_id"] for item in gate_evidence],
    }
    if outcome == "passed":
        if gate not in state["completed_gates"]:
            state["completed_gates"].append(gate)
        for attempt in state["attempts"]:
            if attempt["gate"] == gate and attempt["status"] == "running":
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
    else:
        for attempt in state["attempts"]:
            # A gate can fail after a worker submitted a syntactically valid
            # canonical result: for example, when its inherited corrective
            # finding remains open. Retire that result with the failed gate so
            # the next bounded attempt is a new worker, never the same stale pass.
            if (
                attempt["gate"] == gate
                and not attempt.get("invalidated")
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
    task = load_task_definition(task_dir, state)
    remove_active_mapping(root, state["task_id"])
    cleanup = cleanup_completed_manifest_snapshots(task_dir, state)
    save_state(
        task_dir,
        task_dir / "state.sqlite",
        state,
        "manifest_cleanup",
        f"removed {cleanup['removed_count']} immutable manifest snapshot artifact(s)",
    )


def _closure_rework_target(
    state: dict[str, Any],
    gate: str,
    findings: list[dict[str, Any]],
) -> str:
    """Choose the corrective gate from canonical state and observed impact.

    Worker AttemptResults carry findings, not instructions. The control plane owns
    wave selection: environment/policy conditions block upstream, documented
    impact returns to documentation, and product/review debt fails back to
    implementation when that gate exists.
    """
    pipeline = list(state.get("current_pipeline", []))
    gate_index = pipeline.index(gate) if gate in pipeline else len(pipeline) - 1
    obligations = {
        str(item) for item in state.get("pipeline_obligations", []) if str(item)
    }
    for change in state.get("pipeline_changes", []):
        if isinstance(change, dict):
            obligations.update(str(item) for item in change.get("from", []) if str(item))
    implementation_passed = any(
        attempt.get("gate") == "implementation"
        and attempt.get("status") == "passed"
        and not attempt.get("invalidated")
        for attempt in state.get("attempts", [])
    )
    approval_status = str((state.get("plan_approval") or {}).get("status") or "")
    if (
        gate in {"review", "governance_activation", "governance_close", "close"}
        and approval_status in {"approved", "not_required"}
        and "implementation" in obligations
        and not implementation_passed
    ):
        return "plan" if "plan" in pipeline else "implementation"
    affected_paths: list[str] = []
    for finding in findings:
        details = finding.get("details")
        if isinstance(details, dict) and isinstance(details.get("affected_paths"), list):
            affected_paths.extend(str(path).replace("\\", "/") for path in details["affected_paths"])
    def is_documentation_path(raw_path: str) -> bool:
        """Return true only for a normalized, relative docs path.

        Findings are untrusted input.  Normalize separators and dot segments
        before classification so ``docs/../src/file.py`` cannot be routed to
        the documentation wave.  Absolute paths and paths escaping through a
        parent segment are never documentation-only paths.
        """
        normalized_input = str(raw_path).replace("\\", "/")
        if normalized_input.startswith("/"):
            return False
        normalized = posixpath.normpath(normalized_input)
        return normalized == "docs" or normalized.startswith("docs/")

    if (
        affected_paths
        and all(is_documentation_path(path) for path in affected_paths)
        and "documentation" in pipeline
        and pipeline.index("documentation") <= gate_index
    ):
        return "documentation"
    # A generic governance review finding is not evidence that the completed
    # documentation decision is wrong.  Retrying it through the historical
    # fallback below would invalidate an otherwise-passed documentation
    # attempt and send the state machine back through an unrelated writer.
    # Keep the originating governance verifier active unless the canonical
    # finding explicitly scopes every affected path to documentation.
    if gate in {"governance_activation", "governance_close"}:
        return gate
    if gate == "documentation" and "documentation" in pipeline:
        return "documentation"
    if gate == "qa" and "implementation" in pipeline:
        return "implementation"
    if gate in {"review", "close", "security", "performance"} and "implementation" in pipeline:
        return "implementation"
    if "documentation" in pipeline:
        return "documentation"
    return gate


def _activate_closure_rework(
    state: dict[str, Any],
    *,
    gate: str,
    findings: list[dict[str, Any]],
    source_result_refs: list[str],
) -> str:
    """Make canonical closure debt an executable non-terminal rework chain.

    The current review/close attempt is deliberately not allowed to complete.
    The selected pipeline remains unchanged; ``rework`` invalidates stale
    evidence and attempts from the corrective target onward.  The
    orchestration engine subsequently sees the target as the first incomplete
    wave and reuses its canonical wave contract to prepare a new delegation.
    """
    target_gate = _closure_rework_target(state, gate, findings)
    pipeline = list(state.get("current_pipeline", []))
    if target_gate not in pipeline:
        recovery_order = [
            "plan", "implementation", "qa", "security", "performance",
            "review", "documentation", "close",
        ]
        target_index = recovery_order.index(target_gate) if target_gate in recovery_order else -1
        later = next(
            (
                candidate for candidate in recovery_order[target_index + 1:]
                if candidate in pipeline
            ),
            None,
        )
        pipeline.insert(pipeline.index(later) if later else len(pipeline), target_gate)
    # Rework resets evidence while preserving the orchestrator-selected route.
    # The backend must not reorder closure or governance phases as a side
    # effect of recording corrective work; active_gates will select the first
    # incomplete gate in this unchanged route.
    closure_gates = {"review", "governance_activation", "governance_close", "close"}
    origin_index = pipeline.index(gate) if gate in pipeline else len(pipeline)
    rerun_gates = [
        item for index, item in enumerate(pipeline)
        if index >= origin_index and item in closure_gates
    ]
    reordered = pipeline
    change = apply_pipeline_operations(
        state,
        pipeline=reordered,
        operations=[{"op": "rework", "gate": target_gate}],
        allow_rework=True,
        parallel_groups=[[item] for item in reordered],
    )
    append_pipeline_change(
        state,
        change,
        "Canonical closure finding requires corrective work followed by fresh review and close.",
        [f"closure finding blocked {gate}"],
    )
    state["status"] = "active"
    fingerprints = sorted({str(item["fingerprint"]) for item in findings})
    result_refs = list(dict.fromkeys(
        str(item).strip() for item in source_result_refs if str(item).strip()
    ))
    rework = state.setdefault("closure_rework", {})
    prior = rework.get(gate)
    iteration = int(prior.get("iteration") or 0) + 1 if isinstance(prior, dict) else 1
    if (
        not isinstance(prior, dict)
        or prior.get("finding_fingerprints") != fingerprints
        or prior.get("source_result_refs") != result_refs
    ):
        rework[gate] = {
            "status": "rework_required",
            "target_gate": target_gate,
            "rerun_gates": rerun_gates,
            "finding_fingerprints": fingerprints,
            # Rework invalidates the review/close result that raised the
            # finding. Keep its immutable AttemptResult reference in durable
            # state as historical provenance, rather than as current gate evidence,
            # so the corrective worker receives the exact defect rather than
            # a generic implementation assignment.  The semantic revision
            # binds this exceptional handoff to the task meaning it reviewed.
            "source_result_refs": result_refs,
            "task_revision": int(state.get("task_revision") or 1),
            "iteration": iteration,
            "at": now(),
        }
    elif isinstance(prior, dict):
        prior.update({
            "status": "rework_required",
            "target_gate": target_gate,
            "task_revision": int(state.get("task_revision") or 1),
            "iteration": iteration,
            "at": now(),
        })
    sync_current_wave(state)
    return target_gate


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
