"""Authoritative assignment acceptance evaluation for Cortex orchestration.

Native worker completion is a protocol fact, not proof that an assignment's
acceptance contract passed. This module preserves worker-attested semantic
checks while deriving identity, revision, manifest, result, and native Stop
facts exclusively from server-owned evidence.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from cortex_runtime import attempt_protocol, ledger_db
from cortex_runtime.assignment_compiler import (
    acceptance_contract_digest,
    state_occurrence_execution_ranks,
)
from cortex_runtime.verification_contract import (
    required_verification_kinds,
    validated_bound_evidence,
)


PROTOCOL_STATUSES = frozenset({"completed", "failed", "blocked"})
ACCEPTANCE_STATUSES = frozenset({"passed", "failed", "blocked", "needs_rework"})
PRODUCT_REWORK_REASONS = frozenset({
    "canonical_unresolved_items",
    "canonical_blocking_findings",
    "required_project_mutation_absent",
})


def acceptance_contract_binding_complete(
    definition: Mapping[str, Any],
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
    result: Mapping[str, Any],
) -> bool:
    """Require one exact immutable task-result contract through completion.

    Worker prose is an attestation against its compiled assignment, not a
    substitute for coordinator-authored acceptance or verification items.
    Every persisted boundary therefore carries the same digest and the
    attempt also carries the exact ordered caller/server lists.
    """
    acceptance = definition.get("acceptance_criteria")
    verification = definition.get("verification")
    server_acceptance = definition.get("server_acceptance_obligations")
    server_verification = definition.get("server_verification_obligations")
    exact_lists = (acceptance, verification, server_acceptance, server_verification)
    if any(
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        for value in exact_lists
    ):
        return False
    try:
        expected = acceptance_contract_digest(
            acceptance,
            verification,
            server_acceptance_obligations=server_acceptance,
            server_verification_obligations=server_verification,
        )
    except (TypeError, ValueError):
        return False
    metadata = result.get("metadata")
    return bool(
        isinstance(metadata, Mapping)
        and str(definition.get("acceptance_contract_digest") or "") == expected
        and str(state.get("acceptance_contract_digest") or "") == expected
        and str(attempt.get("acceptance_contract_digest") or "") == expected
        and str(metadata.get("acceptance_contract_digest") or "") == expected
        and attempt.get("task_acceptance_criteria") == acceptance
        and attempt.get("task_verification") == verification
        and attempt.get("server_acceptance_obligations") == server_acceptance
        and attempt.get("server_verification_obligations") == server_verification
    )


def assignment_recovery_class(evaluation: Mapping[str, Any]) -> str:
    """Return the one canonical recovery class for an evaluator receipt.

    Every lifecycle surface consumes this classifier instead of reinterpreting
    status/reason combinations. Product recovery is deliberately narrow:
    canonical findings, unresolved product work, or an absent required
    mutation. Missing receipts, bindings, evidence, native Stop observations,
    infrastructure failures, and every failed/blocked protocol result are
    technical and use the bounded exact-assignment replacement ladder.
    """
    if str(evaluation.get("schema") or "") != "cortex/assignment-evaluation/v2":
        raise ValueError("assignment recovery classification requires a canonical evaluator receipt")
    protocol_status = str(evaluation.get("protocol_status") or "")
    acceptance_status = str(evaluation.get("acceptance_status") or "")
    reasons = evaluation.get("reasons")
    if (
        protocol_status not in PROTOCOL_STATUSES
        or acceptance_status not in ACCEPTANCE_STATUSES
        or not isinstance(reasons, Sequence)
        or isinstance(reasons, (str, bytes, bytearray))
    ):
        raise ValueError("assignment recovery classification received an invalid evaluator receipt")
    failure_class = classify_assignment_failure(
        protocol_status, acceptance_status, [str(item) for item in reasons],
    )
    declared = str(evaluation.get("failure_class") or "")
    expected = failure_class or ""
    if declared != expected:
        raise ValueError("assignment evaluator failure_class disagrees with canonical reasons")
    return failure_class or "none"


def requires_product_rework(evaluation: Mapping[str, Any]) -> bool:
    """Return whether a consumed result needs semantic product rework."""
    return (
        str(evaluation.get("acceptance_status") or "") == "needs_rework"
        and assignment_recovery_class(evaluation) == "product"
    )


def classify_assignment_failure(
    protocol_status: str,
    acceptance_status: str,
    reasons: Sequence[str],
) -> str | None:
    """Classify recovery from one closed registry of evaluator reason codes."""
    if protocol_status in {"failed", "blocked"}:
        return "technical"
    if acceptance_status == "passed":
        return None
    reason_set = {str(item) for item in reasons}
    if reason_set.intersection(PRODUCT_REWORK_REASONS):
        return "product"
    return "technical"


def _protocol_status(result: Mapping[str, Any]) -> str:
    status = str(result.get("status") or "").strip().lower()
    lifecycle = str(result.get("lifecycle_status") or "").strip().upper()
    if status == "blocked" or lifecycle == attempt_protocol.LIFECYCLE_BLOCKED:
        return "blocked"
    if status == "failed" or lifecycle == attempt_protocol.LIFECYCLE_FAILED:
        return "failed"
    if status == "completed" and lifecycle == attempt_protocol.LIFECYCLE_COMPLETED:
        return "completed"
    raise ValueError("canonical AttemptResult is not terminal for acceptance evaluation")


def _current_result_blockers(
    root: Path,
    task_id: str,
    result_ref: str,
) -> list[str]:
    fingerprints: list[str] = []
    for finding in ledger_db.task_findings_blockers(root, task_id):
        sources = finding.get("source_evidence")
        if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes, bytearray)):
            continue
        if any(
            isinstance(source, Mapping)
            and str(source.get("attempt_result_ref") or source.get("origin_result_ref") or "") == result_ref
            for source in sources
        ):
            fingerprint = str(finding.get("fingerprint") or "").strip()
            if fingerprint:
                fingerprints.append(fingerprint)
    return list(dict.fromkeys(fingerprints))


def _bound_verification_evidence(
    root: Path,
    task_id: str,
    attempt: Mapping[str, Any],
    result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return validated_bound_evidence(
        attempt_protocol.list_attempt_events(
            root, task_id=task_id, attempt_id=str(attempt.get("attempt_id") or ""),
        ),
        task_id=task_id,
        attempt=attempt,
        result=result,
    )


def _verification_obligation_set(
    root: Path,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
    result: Mapping[str, Any],
) -> tuple[set[str], set[str], set[str], int, int]:
    """Return required kinds and their honest server/worker evidence classes."""
    task_id = str(state.get("task_id") or "")
    operation_kind = str(attempt.get("operation_kind") or "")
    expected_own = set(required_verification_kinds(
        attempt.get("phase_kind"), operation_kind,
    ))
    compiled_own = set(attempt.get("required_verification_kinds") or [])
    if compiled_own != expected_own:
        raise ValueError("compiled verification obligations differ from server policy")
    own = _bound_verification_evidence(root, task_id, attempt, result)
    required = set(compiled_own)
    server_observed = {
        item["verification_kind"] for item in own
        if item.get("evidence_class") == "server_observed"
    }
    worker_attested = {
        item["verification_kind"] for item in own
        if item.get("evidence_class") == "worker_attested"
    }
    server_count = sum(item.get("evidence_class") == "server_observed" for item in own)
    worker_count = sum(item.get("evidence_class") == "worker_attested" for item in own)
    if operation_kind != "close":
        return required, server_observed, worker_attested, server_count, worker_count

    execution_ranks = state_occurrence_execution_ranks(state)
    close_rank = execution_ranks.get(str(attempt.get("wave_ref") or ""))
    if close_rank is None:
        raise ValueError("close assignment is outside canonical occurrence execution order")
    for predecessor in state.get("attempts") or []:
        predecessor_rank = (
            execution_ranks.get(str(predecessor.get("wave_ref") or ""))
            if isinstance(predecessor, Mapping) else None
        )
        if (
            not isinstance(predecessor, Mapping)
            or predecessor.get("invalidated")
            or str(predecessor.get("operation_kind") or "") != "verify"
            or predecessor_rank is None
            or predecessor_rank >= close_rank
            or str(predecessor.get("acceptance_status") or "") != "passed"
        ):
            continue
        predecessor_ref = str(predecessor.get("attempt_result_ref") or "")
        predecessor_id = str(predecessor.get("attempt_id") or "")
        if not predecessor_ref or not predecessor_id:
            raise ValueError("mandatory predecessor verification has no canonical identity")
        predecessor_result = attempt_protocol.get_attempt_result(
            root, task_id=task_id, attempt_id=predecessor_id,
        )
        if (
            not isinstance(predecessor_result, Mapping)
            or str(predecessor_result.get("result_ref") or "") != predecessor_ref
            or predecessor_result.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
        ):
            raise ValueError("mandatory predecessor verification result is unavailable")
        expected = set(required_verification_kinds(
            predecessor.get("phase_kind"), predecessor.get("operation_kind"),
        ))
        if set(predecessor.get("required_verification_kinds") or []) != expected:
            raise ValueError("predecessor verification obligations differ from server policy")
        predecessor_evidence = _bound_verification_evidence(
            root, task_id, predecessor, predecessor_result,
        )
        required.update(expected)
        server_observed.update(
            item["verification_kind"] for item in predecessor_evidence
            if item.get("evidence_class") == "server_observed"
        )
        worker_attested.update(
            item["verification_kind"] for item in predecessor_evidence
            if item.get("evidence_class") == "worker_attested"
        )
        server_count += sum(item.get("evidence_class") == "server_observed" for item in predecessor_evidence)
        worker_count += sum(item.get("evidence_class") == "worker_attested" for item in predecessor_evidence)
    return required, server_observed, worker_attested, server_count, worker_count


def _immutable_verifier_binding_complete(
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
    result: Mapping[str, Any],
) -> bool:
    """Require exact occurrence identity, compiled plan, and workspace manifest."""
    metadata = result.get("metadata")
    identity = metadata.get("identity") if isinstance(metadata, Mapping) else None
    workspace = result.get("workspace_observation")
    assignment_task_revision = attempt.get("assignment_task_revision")
    operation_kind = str(attempt.get("operation_kind") or "")
    task_revision_bound = (
        "task_revision" not in metadata
        if isinstance(metadata, Mapping) and operation_kind == "close"
        else isinstance(metadata, Mapping)
        and metadata.get("task_revision") == assignment_task_revision
    )
    return bool(
        isinstance(metadata, Mapping)
        and isinstance(identity, Mapping)
        and isinstance(workspace, Mapping)
        and not isinstance(assignment_task_revision, bool)
        and isinstance(assignment_task_revision, int)
        and assignment_task_revision >= 1
        and workspace.get("complete") is True
        and str(workspace.get("current_digest_sha256") or "")
        and str(identity.get("attempt_id") or "") == str(attempt.get("attempt_id") or "")
        and str(identity.get("dispatch_ref") or "") == str(attempt.get("dispatch_ref") or "")
        and metadata.get("plan_revision") == attempt.get("plan_revision")
        and str(metadata.get("plan_digest") or "") == str(attempt.get("plan_digest") or "")
        and task_revision_bound
    )


def evaluate_assignment(
    root: Path,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one deterministic protocol/acceptance verdict.

    ``operation_kind`` and executable identities are compiler output.  Missing
    compiler facts are integrity failures, not values inferred from old gate
    names or worker prose.
    """
    task_id = str(state.get("task_id") or "").strip()
    attempt_id = str(attempt.get("attempt_id") or "").strip()
    result_ref = str(attempt.get("attempt_result_ref") or "").strip()
    if not task_id or not attempt_id or not result_ref:
        raise ValueError("assignment acceptance evaluation requires exact canonical identity")
    for field in ("phase_kind", "phase_ref", "wave_ref", "operation_kind"):
        if not str(attempt.get(field) or "").strip():
            raise ValueError(f"compiled assignment is missing {field}")
    operation_kind = str(attempt["operation_kind"])
    if operation_kind not in {"inspect", "modify", "verify", "close"}:
        raise ValueError("compiled assignment operation_kind is unsupported")
    canonical = dict(result) if isinstance(result, Mapping) else attempt_protocol.get_attempt_result(
        root, task_id=task_id, attempt_id=attempt_id,
    )
    if not isinstance(canonical, dict) or str(canonical.get("result_ref") or "") != result_ref:
        raise ValueError("assignment acceptance evaluation requires its exact canonical result")
    loaded_task = ledger_db.load_task(root, task_id)
    if loaded_task is None:
        raise ValueError("assignment acceptance evaluation requires its immutable task definition")
    task_definition = loaded_task[0]
    contract_bound = acceptance_contract_binding_complete(
        task_definition, state, attempt, canonical,
    )

    protocol_status = _protocol_status(canonical)
    required_verification: set[str] = set()
    server_observed_verification: set[str] = set()
    worker_attested_verification: set[str] = set()
    server_observed_count = 0
    worker_attested_count = 0
    if operation_kind in {"verify", "close"}:
        (
            required_verification,
            server_observed_verification,
            worker_attested_verification,
            server_observed_count,
            worker_attested_count,
        ) = (
            _verification_obligation_set(root, state, attempt, canonical)
        )
    reasons: list[str] = []
    blocker_fingerprints: list[str] = []
    if protocol_status == "blocked":
        acceptance_status = "blocked"
        reasons.append("canonical_result_blocked")
    elif protocol_status == "failed":
        acceptance_status = "failed"
        reasons.append("canonical_result_failed")
    else:
        decisions = list(canonical.get("decisions_needed") or [])
        unresolved = list(canonical.get("unresolved") or [])
        if decisions:
            acceptance_status = "blocked"
            reasons.append("decision_required")
        elif unresolved:
            acceptance_status = "needs_rework"
            reasons.append("canonical_unresolved_items")
        else:
            blocker_fingerprints = _current_result_blockers(root, task_id, result_ref)
            if blocker_fingerprints:
                acceptance_status = "needs_rework"
                reasons.append("canonical_blocking_findings")
            else:
                acceptance_status = "passed"

        if acceptance_status == "passed" and operation_kind in {"verify", "close"}:
            if not _immutable_verifier_binding_complete(state, attempt, canonical):
                acceptance_status = "blocked" if operation_kind == "close" else "needs_rework"
                reasons.append("immutable_verifier_binding_incomplete")
            else:
                missing_server = {
                    kind for kind in required_verification
                    if kind == "manifest_reconciliation" and kind not in server_observed_verification
                }
                missing_worker = {
                    kind for kind in required_verification
                    if kind != "manifest_reconciliation" and kind not in worker_attested_verification
                }
            if acceptance_status == "passed" and (missing_server or missing_worker):
                acceptance_status = "blocked" if operation_kind == "close" else "needs_rework"
                reasons.append(
                    "closure_verification_obligations_incomplete"
                    if operation_kind == "close"
                    else "verification_evidence_required"
                )
            elif acceptance_status == "passed":
                native_stop = attempt.get("native_terminal_stop")
                expected_stop_digest = hashlib.sha256(result_ref.encode("utf-8")).hexdigest()
                if (
                    not isinstance(native_stop, Mapping)
                    or native_stop.get("observed") is not True
                    or str(native_stop.get("result_digest") or "") != expected_stop_digest
                ):
                    acceptance_status = "blocked" if operation_kind == "close" else "needs_rework"
                    reasons.append("exact_native_terminal_stop_required")

        if acceptance_status == "passed" and not contract_bound:
            acceptance_status = "blocked" if operation_kind == "close" else "needs_rework"
            reasons.append("acceptance_contract_binding_incomplete")

        if acceptance_status == "passed" and operation_kind == "modify":
            observation = canonical.get("workspace_observation")
            if not isinstance(observation, Mapping) or observation.get("complete") is not True:
                acceptance_status = "needs_rework"
                reasons.append("mutation_receipt_incomplete")
            else:
                baseline_digest = str(observation.get("baseline_digest_sha256") or "").strip()
                current_digest = str(observation.get("current_digest_sha256") or "").strip()
                if not baseline_digest or not current_digest:
                    acceptance_status = "needs_rework"
                    reasons.append("mutation_receipt_incomplete")
                elif baseline_digest == current_digest:
                    acceptance_status = "needs_rework"
                    reasons.append("required_project_mutation_absent")
                elif (
                    observation.get("safe_to_attribute") is not True
                    or str(canonical.get("changed_files_status") or "") != "server_observed"
                    or not list(canonical.get("changed_files") or [])
                ):
                    acceptance_status = "needs_rework"
                    reasons.append("relevant_mutation_not_attributable")

    if protocol_status not in PROTOCOL_STATUSES or acceptance_status not in ACCEPTANCE_STATUSES:
        raise ValueError("assignment evaluator produced an unsupported status")
    receipt = {
        "schema": "cortex/assignment-evaluation/v2",
        "attempt_id": attempt_id,
        "attempt_result_ref": result_ref,
        "phase_ref": str(attempt["phase_ref"]),
        "wave_ref": str(attempt["wave_ref"]),
        "operation_kind": operation_kind,
        "acceptance_contract_digest": str(
            task_definition.get("acceptance_contract_digest") or ""
        ),
        "protocol_status": protocol_status,
        "acceptance_status": acceptance_status,
        "reasons": reasons,
        "blocking_finding_fingerprints": blocker_fingerprints,
        "server_observed_verification_count": server_observed_count,
        "worker_attested_verification_count": worker_attested_count,
        "required_verification_kinds": sorted(required_verification),
        "server_observed_verification_kinds": sorted(server_observed_verification),
        "worker_attested_verification_kinds": sorted(worker_attested_verification),
        "missing_verification_kinds": sorted(
            {
                kind for kind in required_verification
                if (
                    kind == "manifest_reconciliation"
                    and kind not in server_observed_verification
                ) or (
                    kind != "manifest_reconciliation"
                    and kind not in worker_attested_verification
                )
            }
        ),
    }
    failure_class = classify_assignment_failure(
        protocol_status, acceptance_status, reasons,
    )
    if failure_class is not None:
        receipt["failure_class"] = failure_class
    return receipt


def persist_assignment_evaluation(
    root: Path,
    state: dict[str, Any],
    attempt: dict[str, Any],
) -> dict[str, Any]:
    """Bind the current canonical evaluator receipt to one assignment."""
    receipt = evaluate_assignment(root, state, attempt)
    attempt["protocol_status"] = receipt["protocol_status"]
    attempt["acceptance_status"] = receipt["acceptance_status"]
    attempt["acceptance_evaluation"] = receipt
    return receipt
