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
        "_attempts_missing_result_validation",
        "_validated_evidence_records",
        "active_gates",
        "append_pipeline_change",
        "apply_pipeline_operations",
        "authorize",
        "capture_project_manifest",
        "cleanup_completed_manifest_snapshots",
        "db_list_task_findings",
        "db_task_findings_blockers",
        "invalidate_reworked_report_receipts",
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
        return _recoverable(
            state,
            revision_correction,
            reason="mandatory_gate",
            gate=gate,
            next_action="record_delegation",
        )
    if state.get("require_delegation") and not str(params.get("skip_reason", "")).strip():
        raise ValueError("C2/C3 skipped gates require an explicit skip_reason")
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
    if not gate_evidence and not (
        state.get("require_delegation")
        and gate_attempts
        and len(terminal_non_success_attempts) == len(gate_attempts)
    ):
        return [], {
            "recorded": False,
            "reason": "evidence_required",
            "gate": gate,
            "gate_correction": (
                {"requested": requested_gate, "used": gate}
                if requested_gate != gate else None
            ),
            "candidate_attempt_ids": [
                item["attempt_id"] for item in non_terminal_attempts + passed_attempts
            ],
            "next_action": "record_delegation" if not gate_attempts else "record_evidence",
            "revision_correction": revision_correction,
            "state": state,
        }
    if any(
        item.get("kind") == "command"
        and (item.get("exit_code") != 0 or not item.get("verified_execution"))
        for item in gate_evidence
    ):
        raise ValueError("cannot pass a gate with failed or self-attested command evidence; use execute_verification_command")
    if not state.get("require_delegation"):
        return gate_evidence, None
    if not gate_attempts:
        if gate == "documentation":
            return [], _recoverable(
                state,
                revision_correction,
                reason="documentation_attempt_required",
                gate=gate,
                next_action="record_delegation",
            )
        raise ValueError("C2/C3 gates require at least one delegation attempt")
    missing = [
        item["attempt_id"] for item in passed_attempts
        if not any(evidence.get("attempt_id") == item["attempt_id"] for evidence in gate_evidence)
    ]
    if missing:
        if gate == "documentation":
            return [], _documentation_recovery(state, revision_correction, "documentation_evidence_required", gate, missing)
        raise ValueError("every passed attempt needs linked evidence before the gate can pass: " + ", ".join(missing))
    missing_reports = [
        item["attempt_id"] for item in passed_attempts
        if not any(
            evidence.get("attempt_id") == item["attempt_id"]
            and evidence.get("report_id") and evidence.get("report_receipt")
            for evidence in gate_evidence
        )
    ]
    if missing_reports:
        if gate == "documentation":
            return [], _documentation_recovery(state, revision_correction, "documentation_report_receipt_required", gate, missing_reports)
        raise ValueError("every passed attempt needs a consumed report receipt before the gate can pass: " + ", ".join(missing_reports))
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
            return [], _documentation_recovery(state, revision_correction, "documentation_evidence_required", gate, unexplained)
        raise ValueError("every active delegated attempt needs linked evidence before the gate can pass: " + ", ".join(unexplained))
    eligible_attempt_ids = {
        item["attempt_id"] for item in gate_attempts
        if item["attempt_id"] in evidence_attempt_ids
        and item.get("status") in {"running", "passed"}
    }
    if passed_attempts and not eligible_attempt_ids:
        if gate == "documentation":
            return [], _documentation_recovery(
                state,
                revision_correction,
                "documentation_evidence_required",
                gate,
                [item["attempt_id"] for item in passed_attempts],
            )
        raise ValueError("a passed gate requires linked evidence for at least one delegated attempt")
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
            return [], _documentation_recovery(
                state,
                revision_correction,
                "documentation_evidence_required",
                gate,
                [
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
) -> None:
    if outcome == "blocked" and state.get("require_handoff") and (
        not state.get("handoff_created") or state.get("handoff_gate") != gate
    ):
        raise ValueError("C2/C3 pause requires a current-gate handoff")
    if outcome != "passed" or gate != "close" or not state.get("require_handoff"):
        return
    if not state.get("handoff_created") or state.get("handoff_gate") != "close":
        raise ValueError("C2/C3 close requires a final handoff")
    if "documentation" not in state.get("completed_gates", []) or not state.get("documentation_receipt"):
        raise ValueError("C2/C3 close requires completed documentation decision evidence")
    if not state.get("reassessment_receipts"):
        raise ValueError("C2/C3 close requires a recorded reassessment decision")
    if not any(
        item.get("kind") == "command" and item.get("verified_execution")
        and item.get("exit_code") == 0
        for item in current_attempt_evidence
    ):
        raise ValueError("C2/C3 close requires successful server-observed command evidence")
    manifest = state.get("final_manifest_receipt")
    if not manifest or not manifest.get("complete"):
        raise ValueError("C2/C3 close requires a complete handoff file-manifest receipt")
    baseline_manifest = task_manifest_baseline(task_dir, state)
    baseline_partial = baseline_manifest.get("partial_manifest")
    if isinstance(baseline_partial, dict) and baseline_partial.get("partial"):
        raise ValueError(
            "C2/C3 close requires a complete baseline manifest; "
            f"capture stopped at {baseline_partial.get('reason') or 'a configured limit'}"
        )
    current_manifest = capture_project_manifest(
        Path(load_task_definition(task_dir, state)["project_root"]),
        policy=baseline_manifest.get("policy"),
    )
    current_partial = current_manifest.get("partial_manifest")
    if isinstance(current_partial, dict) and current_partial.get("partial"):
        raise ValueError(
            "C2/C3 close requires a complete final manifest; "
            f"capture stopped at {current_partial.get('reason') or 'a configured limit'}"
        )
    if current_manifest["digest"] != manifest.get("current_digest"):
        raise ValueError("project files changed after the final handoff; create a new complete handoff")


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
        state["status"] = "blocked"
    else:
        for attempt in state["attempts"]:
            # A gate can fail after a worker submitted a syntactically valid
            # pass report: for example, when its inherited corrective finding
            # remains open.  Retire that report with the failed gate so the
            # next bounded attempt is a new worker, never the same stale pass.
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
        invalidate_reworked_report_receipts(
            task_dir, state
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
            # A bounded/partial final capture cannot prove that the terminal
            # file set is accounted for.  Keep the task out of completed
            # state; the receipt remains available to the caller as bounded
            # diagnostic evidence through the reconciliation path.
            raise ValueError(
                "cannot complete task with an incomplete final manifest; "
                "recapture the project after the manifest limit is resolved"
            )
        closed_paths = list(closed_receipt["comparison"]["changed_paths"])
        closed_receipt["reported_paths"] = closed_paths
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
    remove_active_mapping(root, state["task_id"], str(task.get("thread_id", "")))
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

    Worker reports carry findings, not instructions.  The control plane owns
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
    if (
        affected_paths
        and all(path.startswith("docs/") for path in affected_paths)
        and "documentation" in pipeline
        and pipeline.index("documentation") <= gate_index
    ):
        return "documentation"
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
    task_dir: Path,
    state: dict[str, Any],
    *,
    gate: str,
    findings: list[dict[str, Any]],
    source_report_refs: list[str],
) -> str:
    """Make canonical closure debt an executable non-terminal rework chain.

    The current review/close attempt is deliberately not allowed to complete.
    Reordering the canonical pipeline places the corrective target ahead of a
    fresh originating verifier and every later close verifier, while ``rework``
    invalidates all stale evidence and attempts from that target onward.  The
    orchestration engine subsequently sees the target as the active wave and
    reuses its canonical wave contract to prepare a new delegation.
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
    # Preserve the corrective target's position so the rework operation makes
    # it the first incomplete gate. Move the originating closure gate and
    # every later closure verifier to the tail; moving the target itself behind
    # QA or documentation would leave the just-failed gate active and produce
    # a false ``needs_input`` state.  The previous review/close-only list
    # omitted governance_activation and governance_close, leaving a finding
    # raised by one of those gates without the fresh origin rerun required for
    # a server-bound resolution receipt.
    closure_gates = {"review", "governance_activation", "governance_close", "close"}
    origin_index = pipeline.index(gate) if gate in pipeline else len(pipeline)
    final_checks = [
        item for index, item in enumerate(pipeline)
        if index >= origin_index and item in closure_gates
    ]
    reordered = [item for item in pipeline if item not in final_checks] + final_checks
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
    invalidate_reworked_report_receipts(task_dir, state)
    state["status"] = "active"
    fingerprints = sorted({str(item["fingerprint"]) for item in findings})
    report_refs = list(dict.fromkeys(
        str(item).strip() for item in source_report_refs if str(item).strip()
    ))
    rework = state.setdefault("closure_rework", {})
    prior = rework.get(gate)
    iteration = int(prior.get("iteration") or 0) + 1 if isinstance(prior, dict) else 1
    if (
        not isinstance(prior, dict)
        or prior.get("finding_fingerprints") != fingerprints
        or prior.get("source_report_refs") != report_refs
    ):
        rework[gate] = {
            "status": "rework_required",
            "target_gate": target_gate,
            "rerun_gates": list(final_checks),
            "finding_fingerprints": fingerprints,
            # Rework invalidates the review/close receipt that raised the
            # finding.  Keep its immutable report reference in durable state
            # as historical provenance, rather than as current gate evidence,
            # so the corrective worker receives the exact defect rather than
            # a generic implementation assignment.  The semantic revision
            # binds this exceptional handoff to the task meaning it reviewed.
            "source_report_refs": report_refs,
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
        skipped = _validate_skip(state, params, gate, outcome, revision_correction)
        if skipped is not None:
            return skipped
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
            return recovery
        if outcome == "passed":
            validate_governance_obligation_evidence(
                state,
                gate,
                # Light-mode close obligations are intentionally accumulated
                # across documentation/review/verification gates.  Full
                # governance-close evidence is also safe to resolve from the
                # complete immutable receipt set at this boundary.
                None if gate == "close" else current_attempt_evidence,
                artifact_root=root,
            )
        _validate_handoff_and_close(
            task_dir,
            state,
            gate=gate,
            outcome=outcome,
            current_attempt_evidence=current_attempt_evidence,
        )
        if outcome in {"passed", "failed"} and (
            gate in {"review", "governance_activation", "governance_close", "close"}
            or bool(params.get("enforce_canonical_findings"))
        ):
            blockers = db_task_findings_blockers(root, state["task_id"])
            if blockers:
                actionable = blockers
                source_report_refs = list(dict.fromkeys(
                    str(report_ref)
                    for attempt in inputs[4]
                    for report_ref in attempt.get("report_ids", [])
                    if str(report_ref).strip()
                ))
                target_gate = _activate_closure_rework(
                    task_dir,
                    state,
                    gate=gate,
                    findings=actionable,
                    source_report_refs=source_report_refs,
                )
                save_state(task_dir, task_dir / "state.sqlite", state, "gate_rework", f"{gate}: canonical gate blockers require rework")
                # Returning a normal transition shape lets the v3 adapter
                # finish the current wave bookkeeping and prepare the active
                # remediation wave.  The gate itself is intentionally absent
                # from completed_gates, so this is non-terminal rework rather
                # than a worker-controlled pass.
                return {
                    "state": state,
                    "revision_correction": revision_correction,
                    "gate_rework": True,
                    "closure_rework": gate in {
                        "review", "governance_activation", "governance_close", "close",
                    },
                    "reason": "gate_blockers",
                    "gate": gate,
                    "target_gate": target_gate,
                    "next_action": "resolve_findings_then_rerun_review_and_close",
                    "blockers": actionable,
                }
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
        return {"state": state, "revision_correction": revision_correction}
