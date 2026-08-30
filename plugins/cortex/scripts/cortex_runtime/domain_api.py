"""Semantic public facade for the Cortex domain API.

The V12 ledger remains the durable historical substrate.  This module is the
only public-facing adapter: it turns semantic task/assignment/publication
operations into the existing immutable ledger records and never exports the
old storage-state-machine surface.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from cortex_runtime import v12_service as legacy
from cortex_runtime.v12_contract import record_ref, task_ref as compact_task_ref
from cortex_runtime.v12_service import V12ServiceError, _task_store
from cortex_runtime.v12_store import V12Store, V12StoreError
from cortex_runtime.domain_kernel import DecisionAggregate


def _worker_capability_provenance() -> dict[str, str]:
    """Return the running package identity used to bind worker bootstrap.

    Bootstrap capabilities are package-bound facts.  The public facade derives
    these values from the verified package and catalogue; callers can never
    select or override them.
    """
    from cortex_runtime.provenance import verify_runtime
    from cortex_runtime.public_contracts import build_public_contracts
    package_root = Path(__file__).resolve().parents[2]
    identity = verify_runtime(package_root, "1.12.1", allow_source_mode=True)
    catalogue = tuple(
        {
            "name": name,
            "description": str(contract["description"]),
            "inputSchema": dict(contract["inputSchema"]),
        }
        for name, contract in build_public_contracts().items()
    )
    catalogue_digest = hashlib.sha256(json.dumps(
        catalogue, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")).hexdigest()
    return {
        "build_digest": identity["build_id"],
        "candidate_digest": "sha256:" + identity["source_digest"],
        "source_digest": "sha256:" + identity["source_digest"],
        "catalogue_digest": "sha256:" + catalogue_digest,
    }


def _operation_key(operation: str, payload: Mapping[str, object]) -> str:
    """Private deterministic identity for the legacy durable mutation layer."""
    encoded = json.dumps({"operation": operation, "payload": dict(payload)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "domain-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def open_task(*, task: Mapping[str, object]) -> dict[str, Any]:
    """Create or exactly replay one task from its coherent public outcome contract."""
    if not isinstance(task, Mapping):
        raise V12ServiceError("task opening contract is invalid", code="invalid_argument", details={"field": "task"})
    project_root = task.get("project_root")
    user_request_original = task.get("request_original")
    user_language = task.get("user_language")
    outcomes = task.get("outcomes")
    constraints = task.get("constraints")
    context = task.get("context")
    if not isinstance(outcomes, list):
        raise V12ServiceError("task outcomes are invalid", code="invalid_argument", details={"field": "task"})
    requirements: list[object] = []
    acceptance_criteria: list[object] = []
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            raise V12ServiceError("task outcomes are invalid", code="invalid_argument", details={"field": "task"})
        requirements.append(outcome.get("requirement"))
        acceptance = outcome.get("acceptance")
        if not isinstance(acceptance, list):
            raise V12ServiceError("task outcomes are invalid", code="invalid_argument", details={"field": "task"})
        acceptance_criteria.extend(acceptance)
    # The exact original request is already the immutable task objective.
    # Requiring a second model-authored summary created a redundant first-call
    # failure mode and allowed the two values to drift.
    payload = {"project_root": project_root, "objective": user_request_original,
               "user_request_original": user_request_original, "user_language": user_language,
               "requirements": requirements, "constraints": constraints,
               "acceptance_criteria": acceptance_criteria, "context": context}
    return legacy.create_task(**payload)


def read_task(*, task_ref: str, after_sequence: int = 0) -> dict[str, Any]:
    """Read bounded task state and aggregate evidence."""
    return legacy.inspect_task(task_ref=task_ref, after_sequence=after_sequence)


def _issue_clarification_legacy(*, task_ref: str, prompt: str, prompt_language: str,
                                subject_type: str = "task", subject_ref: str | None = None,
                                assignment_ref: str | None = None) -> dict[str, Any]:
    """Issue a server-owned opaque clarification binding."""
    store, canonical = _task_store(task_ref)
    result = store.issue_clarification_binding(
        task_id=canonical, prompt=prompt, prompt_language=prompt_language,
        subject_type=subject_type,
        subject_id=subject_ref, assignment_id=assignment_ref,
    )
    return result


def _open_decision_legacy(*, task_ref: str, prompt: str, prompt_language: str,
                          subject_type: str = "task", subject_ref: str | None = None,
                          assignment_ref: str | None = None,
                          decision_type: str = "clarification") -> dict[str, Any]:
    """Issue one public scalar binding handle and non-callable context."""
    store, canonical = _task_store(task_ref)
    try:
        issued = DecisionAggregate(store).open(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type=subject_type, subject_id=subject_ref,
            assignment_id=assignment_ref,
            decision_type=decision_type,
        )
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
    binding = issued.get("binding")
    if not isinstance(binding, Mapping) or not isinstance(binding.get("clarification_binding"), str):
        raise V12ServiceError("decision binding is unavailable", code="ledger_error")
    return {
        "task_ref": task_ref,
        "binding_ref": binding["clarification_binding"],
        "next_action": "record_clarification",
        "decision_context": {
            key: binding[key]
            for key in ("subject_type", "subject_ref", "decision_type", "prompt", "prompt_language", "effective_contract_revision", "consumed")
            if key in binding
        },
        "replayed": bool(issued.get("replayed")),
    }


def open_assignment(*, task_ref: str, mission: Mapping[str, object],
                    input_report_refs: list[str] | None = None,
                    input_decision_refs: list[str] | None = None) -> dict[str, Any]:
    """Atomically bind a worker assignment to its current effective contract."""
    from cortex_runtime.model_routing import profile_default_selection
    if not isinstance(mission, Mapping):
        raise V12ServiceError("assignment mission is invalid", code="invalid_argument", details={"field": "mission"})
    selection = profile_default_selection(mission.get("profile_name"))
    payload = {"task_ref": task_ref, "objective": mission.get("goal"), "role": mission.get("role"),
               "profile_name": mission.get("profile_name"), "scope": mission.get("constraints"), "instructions": mission.get("instructions"),
               "model": selection.model, "reasoning_effort": selection.reasoning_effort,
               "input_report_refs": input_report_refs, "input_decision_refs": input_decision_refs}
    provenance = _worker_capability_provenance()
    result = legacy.create_delegation(
        task_ref=task_ref, objective=payload["objective"], role=payload["role"], profile_name=payload["profile_name"],
        scope=payload["scope"], instructions=payload["instructions"], model=selection.model, reasoning_effort=selection.reasoning_effort,
        input_report_refs=input_report_refs, input_decision_refs=input_decision_refs,
        parent_delegation_ref=None,
        bootstrap_provenance=provenance,
        derive_assignment_scope=True,
    )
    delegation = result.get("delegation")
    if isinstance(delegation, Mapping) and isinstance(delegation.get("delegation_id"), str):
        result = dict(result)
        result["assignment_ref"] = record_ref(str(delegation["delegation_id"]))
        assignment_ref = result["assignment_ref"]
        if not isinstance(assignment_ref, str):
            raise V12ServiceError("assignment capability is unavailable", code="ledger_error")
        raw_brief = result.get("dispatch_brief")
        if not isinstance(raw_brief, Mapping):
            raise V12ServiceError("worker dispatch is invalid", code="ledger_error")
        raw_native = raw_brief.get("native_dispatch")
        if not isinstance(raw_native, Mapping):
            raise V12ServiceError("worker dispatch is invalid", code="ledger_error")
        native_args = raw_native.get("native_arguments")
        if not isinstance(native_args, Mapping):
            raise V12ServiceError("worker dispatch is invalid", code="ledger_error")
        # The public assignment result has one and only one host projection.
        # Keep the native spawn fields at that projection's top level so the
        # coordinator can forward the exact object without reconstructing a
        # semantic brief or selecting a parallel message representation.
        native_dispatch = {
            "fork_turns": native_args.get("fork_turns"),
            "message": native_args.get("message"),
            "task_name": native_args.get("task_name"),
        }
        if not all(isinstance(native_dispatch[key], str) and native_dispatch[key] for key in ("fork_turns", "message", "task_name")):
            raise V12ServiceError("worker dispatch is invalid", code="ledger_error")
        # The coordinator owns only the opaque native dispatch. The private
        # one-time worker lease remains in the server ledger and is resolved by
        # the assignment locator when the worker consumes its evidence.
        return {
            "assignment_ref": assignment_ref,
            "handles": {"assignment_ref": assignment_ref},
            "native_dispatch": native_dispatch,
            # Preserve the server's durable mutation outcome.  A coordinator
            # must be able to distinguish a first mint from reconciliation of
            # an already-committed dispatch after an interrupted host call;
            # otherwise it may incorrectly treat the same assignment as new.
            "replayed": bool(result.get("replayed")),
            "relations": {
                "parent_assignment_ref": parent
                for parent in [record_ref(delegation.get("parent_delegation_id"))]
                if parent is not None
            },
        }
    return result


def consume_assignment_evidence(*, assignment_ref: str, cursor: str | None = None) -> dict[str, Any]:
    """Consume only declared predecessor report evidence for one assignment.

    This is the worker's sole authoritative bootstrap boundary for effective
    scope, mission context, declared decisions, and predecessor reports. The
    worker supplies only the assignment locator; the bootstrap capability is
    private server state and is consumed atomically by assignment binding.
    """
    try:
        store, assignment_id = V12Store.for_record_ref(assignment_ref, label="delegation_id")
        assignment = store.read_delegation(delegation_id=assignment_id, after_sequence=0, limit=1)
        # ``read_delegation`` deliberately returns a projection envelope.  Do
        # not read task/dispatch identity from the envelope itself: doing so
        # made every freshly opened assignment fail with a misleading
        # ``ledger_error`` before the capability validator could run.
        delegation = assignment.get("delegation") if isinstance(assignment, Mapping) else None
        task_id = delegation.get("task_id") if isinstance(delegation, Mapping) else None
        dispatch_digest = delegation.get("dispatch_correlation_digest") if isinstance(delegation, Mapping) else None
        state = legacy.inspect_task(task_ref=compact_task_ref(str(task_id))) if isinstance(task_id, str) else {}
        if not isinstance(task_id, str) or not isinstance(dispatch_digest, str):
            raise V12ServiceError("worker bootstrap capability is invalid", code="ledger_error")
        provenance = _worker_capability_provenance()
        continuation = store.consume_worker_bootstrap_for_assignment(
            task_id=task_id,
            # The store resolves the immutable assignment snapshot revision;
            # this compatibility argument is no longer task-global authority.
            assignment_id=assignment_id, contract_revision=1,
            dispatch_digest=dispatch_digest, **provenance,
        )
        if not isinstance(continuation, Mapping):
            raise V12ServiceError("worker continuation is invalid", code="ledger_error")
        assignment_ref = record_ref(str(continuation.get("assignment_id")))
        delegation_id = continuation.get("assignment_id")
        if not isinstance(assignment_ref, str) or not isinstance(delegation_id, str):
            raise V12ServiceError("worker continuation is invalid", code="ledger_error")
        continuation_ref = continuation.get("continuation")
        if not isinstance(continuation_ref, str):
            raise V12ServiceError("worker continuation is invalid", code="ledger_error")
        brief = store.read_delegation(delegation_id=delegation_id, after_sequence=0, limit=1)
        worker = brief.get("worker_brief")
        delegation_view = brief.get("delegation")
        effective_contract = worker.get("effective_contract") if isinstance(worker, Mapping) else None
        if not isinstance(effective_contract, Mapping):
            raise V12ServiceError("assignment scope is unavailable", code="ledger_error")
        assignment_context = {}
        if isinstance(delegation_view, Mapping):
            for key in ("role", "profile_name", "objective", "scope", "instructions"):
                value = delegation_view.get(key)
                if isinstance(value, str):
                    assignment_context[key] = value
        predecessor_evidence = []
        for item in (worker.get("input_report_refs", []) if isinstance(worker, Mapping) else []):
            if not isinstance(item, Mapping):
                continue
            compact = record_ref(item.get("report_id"))
            if compact is not None:
                predecessor_evidence.append({"report_ref": compact, "report_type": item.get("report_type"), "status": item.get("status"), "assembly_state": item.get("assembly_state"), "content_digest": item.get("content_digest")})
        decision_evidence = []
        for item in (worker.get("input_decisions", []) if isinstance(worker, Mapping) else []):
            if not isinstance(item, Mapping):
                continue
            compact = record_ref(item.get("decision_id"))
            if compact is not None:
                decision_evidence.append({
                    "decision_ref": compact, "decision_type": item.get("decision_type"),
                    "subject_type": item.get("subject_type"), "subject_digest": item.get("subject_digest"),
                    "prompt": item.get("prompt_en"), "response_original": item.get("response_original"),
                    "user_language": item.get("user_language"),
                })
        authority = {
            "effective_contract": dict(effective_contract),
            "assignment_context": assignment_context,
            "predecessor_evidence": predecessor_evidence if isinstance(predecessor_evidence, list) else [],
            "decision_evidence": decision_evidence if isinstance(decision_evidence, list) else [],
        }
        report_ids = worker.get("input_report_ids") if isinstance(worker, Mapping) else None
        if not isinstance(report_ids, list) or not report_ids:
            return {"assignment_ref": assignment_ref, "continuation_ref": continuation_ref, **authority, "evidence": {"state": "none", "reports": []}, "next_cursor": None, "has_more": False}
        result = store.read_reports(
            report_ids=report_ids, cursor=cursor, max_bytes=65_536,
            consumer_delegation_id=delegation_id,
        )
        return {"assignment_ref": assignment_ref, "continuation_ref": continuation_ref, **authority, "evidence": {"state": "consumed", **result}}
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _public_plan_publication(published: Mapping[str, Any]) -> dict[str, Any]:
    """Return the closed compact plan capability emitted by public publication.

    The ledger report remains canonical evidence.  The public publication
    result intentionally contains only the immutable report capability and its
    already-bound review relation, so clients never receive canonical record,
    task, or delegation identifiers to copy into later calls.
    """
    report = published.get("report")
    approval = published.get("approval_view")
    if not isinstance(report, Mapping) or not isinstance(approval, Mapping):
        raise V12ServiceError("plan publication relation is unavailable", code="ledger_error")
    report_ref = record_ref(report.get("report_id"))
    delegation_ref = record_ref(report.get("delegation_id"))
    if report_ref is None or delegation_ref is None:
        raise V12ServiceError("plan publication relation is unavailable", code="ledger_error")
    report_digest = report.get("content_digest")
    relation_digest = approval.get("report_content_digest")
    if not isinstance(report_digest, str) or relation_digest != report_digest:
        raise V12ServiceError("plan publication relation is unavailable", code="ledger_error")
    view_digest = approval.get("content_digest")
    handle = approval.get("approval_handle")
    sequence = approval.get("source_sequence")
    if (not isinstance(view_digest, str) or not isinstance(handle, str)
            or not isinstance(sequence, int) or isinstance(sequence, bool)):
        raise V12ServiceError("plan publication relation is unavailable", code="ledger_error")
    compact_supersedes = record_ref(report.get("supersedes_report_id"))
    return {
        "report": {
            "report_ref": report_ref,
            "report_type": "plan",
            "status": str(report.get("status")),
            "semantic_status": str(report.get("semantic_status")),
            "content_digest": report_digest,
            **({"supersedes_report_ref": compact_supersedes} if compact_supersedes is not None else {}),
        },
        "approval_view": {
            "report_ref": report_ref,
            "delegation_ref": delegation_ref,
            "report_content_digest": report_digest,
            "status": "ready",
            "source_sequence": sequence,
            "content_digest": view_digest,
            "approval_handle": handle,
        },
        "replayed": bool(published.get("replayed")),
    }


def _publish(*, continuation_ref: str, assignment_ref: str, kind: str, evidence: object,
             status: str = "completed") -> dict[str, Any]:
    """Publish one complete immutable assignment outcome through the v14 store."""
    try:
        if not isinstance(continuation_ref, str) or not continuation_ref:
            raise V12ServiceError("worker continuation is invalid", code="invalid_argument", details={"field": "continuation_ref"})
        store, delegation_id = V12Store.for_record_ref(assignment_ref, label="delegation_id")
        delegation_projection = store.read_delegation(delegation_id=delegation_id, after_sequence=0, limit=1)
        delegation = delegation_projection.get("delegation") if isinstance(delegation_projection, Mapping) else None
        task_id = delegation.get("task_id") if isinstance(delegation, Mapping) else None
        if not isinstance(task_id, str):
            raise V12ServiceError("worker continuation is invalid", code="capability_stale")
        continuation = store.resolve_worker_continuation(continuation=continuation_ref)
        if continuation.get("task_id") != task_id or continuation.get("assignment_id") != delegation_id:
            raise V12ServiceError("worker continuation is invalid", code="capability_stale")
        revision = int(continuation["contract_revision"])
        published = store.publish_domain_report(
            delegation_id=delegation_id, continuation_ref=continuation_ref,
            contract_revision=revision,
            publication_kind="synthesis" if kind == "documentation" else kind, content=evidence, status=status,
        )
        # ``publish_domain_report`` commits a plan's immutable rendered view
        # and approval relation in the same ledger transaction as its terminal
        # report operation.  Do not post-process it through the historical
        # best-effort projection path: that would create an observable
        # published-plan-without-ready-relation interval.
        return _public_plan_publication(published) if kind == "plan" else published
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def publish_plan(*, continuation_ref: str, assignment_ref: str, evidence: object, status: str = "completed") -> dict[str, Any]:
    return _publish(continuation_ref=continuation_ref, assignment_ref=assignment_ref, kind="plan", evidence=evidence, status=status)


def publish_result(*, continuation_ref: str, assignment_ref: str, evidence: object, status: str = "completed") -> dict[str, Any]:
    return _publish(continuation_ref=continuation_ref, assignment_ref=assignment_ref, kind="result", evidence=evidence, status=status)


def publish_documentation(*, continuation_ref: str, assignment_ref: str, evidence: object, status: str = "completed") -> dict[str, Any]:
    return _publish(continuation_ref=continuation_ref, assignment_ref=assignment_ref, kind="documentation", evidence=evidence, status=status)


def _record_decision_legacy(*, task_ref: str, binding_ref: str, response_original: str,
                            user_language: str, subject_digest: str | None = None,
                            approval_handle: str | None = None,
                            approval_view_content_digest: str | None = None,
                            approval_view_source_sequence: int | None = None,
                            supersedes_decision_ref: str | None = None,
                            steering_delta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Record a decision only against a server-issued pending binding."""
    if not isinstance(binding_ref, str) or not binding_ref:
        raise V12ServiceError("decision binding is invalid", code="invalid_argument", details={"field": "binding_ref"})
    store, canonical = _task_store(task_ref)
    try:
        return DecisionAggregate(store).record(
            task_id=canonical, binding_ref=binding_ref,
            response_original=response_original, user_language=user_language,
            subject_digest=subject_digest, approval_handle=approval_handle,
            approval_view_content_digest=approval_view_content_digest,
            approval_view_source_sequence=approval_view_source_sequence,
            supersedes_decision_id=supersedes_decision_ref,
            steering_delta=steering_delta,
        )
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _family_result(
    *, task_ref: str, issued: Mapping[str, Any], response_original: str | None = None,
) -> dict[str, Any]:
    """Project a family command to its exact public scalar binding contract."""
    binding = issued.get("binding")
    if not isinstance(binding, Mapping) or not isinstance(binding.get("clarification_binding"), str):
        raise V12ServiceError("decision binding is unavailable", code="ledger_error")
    result: dict[str, Any] = {
        "task_ref": task_ref,
        "binding_ref": binding["clarification_binding"],
        "replayed": bool(issued.get("replayed")),
        "next_action": {"clarification": "record_clarification", "plan_review": "record_plan_review", "steer": "record_steering"}.get(str(binding.get("decision_type")), "record_clarification"),
    }
    hold = issued.get("clarification_hold")
    if isinstance(hold, Mapping):
        # The aggregate returns only compact lifecycle evidence here.  The
        # assignment-origin host continuation is added below by the
        # clarification facade after the renderer has built its trusted text.
        result["clarification_hold"] = dict(hold)
    # The scalar in ``binding_ref`` is the only next-call capability.  Retain
    # the bound family context separately for audit/display without requiring
    # clients to traverse it or reconstruct a durable identifier.
    result["decision_context"] = {
        key: binding[key]
        for key in (
            "task_ref", "subject_type", "subject_ref", "decision_type",
            "prompt", "prompt_language", "effective_contract_revision", "consumed",
            "decision_ref",
            "plan_review_relation",
        )
        if key in binding
    }
    decision = issued.get("decision")
    if isinstance(decision, Mapping):
        identifier = decision.get("decision_id")
        if isinstance(identifier, str):
            decision_ref = record_ref(identifier)
            if decision_ref is not None:
                result["decision_ref"] = decision_ref
        subject_type = decision.get("subject_type")
        subject_id = decision.get("subject_id")
        subject_ref: str | None = None
        if subject_type == "task" and isinstance(decision.get("task_id"), str):
            subject_ref = task_ref
        elif isinstance(subject_id, str):
            subject_ref = record_ref(subject_id)
        # Family responses intentionally project only compact capabilities and
        # bounded decision evidence.  Canonical IDs remain private ledger
        # implementation details; no caller needs them to compose another
        # public operation.
        context = {
            key: decision[key]
            for key in (
                "subject_type", "subject_digest", "decision_type", "user_language",
                "attribution", "created_at", "created_sequence",
            )
            if key in decision
        }
        if isinstance(result.get("decision_ref"), str):
            context["decision_ref"] = result["decision_ref"]
        if subject_ref is not None:
            context["subject_ref"] = subject_ref
        supersedes = decision.get("supersedes_decision_id")
        if isinstance(supersedes, str):
            compact_supersedes = record_ref(supersedes)
            if compact_supersedes is not None:
                context["relations"] = {"supersedes_decision_ref": compact_supersedes}
        if response_original is not None:
            context["response_original"] = response_original
        result["decision"] = context
    return result


def open_clarification(*, task_ref: str, prompt: str, prompt_language: str,
                       assignment_ref: str | None = None) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        assignment_id = None if assignment_ref is None else store.resolve_task_reference(
            task_id=canonical, value=assignment_ref, kinds=("assignment",)
        )[1]
        issued = DecisionAggregate(store).open_clarification(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type="task", subject_id=canonical, assignment_id=assignment_id,
        )
        return _family_result(task_ref=task_ref, issued=issued)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _clarification_host_delivery(
    *, store: V12Store, task_id: str, binding_ref: str,
) -> dict[str, Any] | None:
    """Render one exact-worker continuation without invoking a host action."""
    delivery = store.clarification_host_delivery_projection(
        task_id=task_id, binding_ref=binding_ref,
    )
    if delivery is None:
        return None
    assignment_ref = record_ref(str(delivery["assignment_id"]))
    decision_ref = record_ref(str(delivery["decision_id"]))
    if assignment_ref is None or decision_ref is None:
        raise V12ServiceError("clarification delivery relation is invalid", code="ledger_error")
    result: dict[str, Any] = {
        "state": delivery["state"],
        "assignment_ref": assignment_ref,
        "native_task_name": delivery["native_task_name"],
        "native_dispatch_digest": delivery["native_dispatch_digest"],
        "dispatch_correlation_marker": delivery["dispatch_correlation_marker"],
        "dispatch_correlation_fingerprint": delivery["dispatch_correlation_fingerprint"],
        "decision_ref": decision_ref,
    }
    if delivery["state"] == "pending_delivery":
        context = store.clarification_host_delivery_context(
            task_id=task_id, binding_ref=binding_ref,
        )
        if not isinstance(context, Mapping):
            raise V12ServiceError("clarification delivery renderer context is unavailable", code="ledger_error")
        try:
            from cortex_runtime.worker_message import render_clarification_continuation
            rendered = render_clarification_continuation(
                task=context["task"], delegation=context["delegation"], decision=context["decision"],
            )
        except (ImportError, AttributeError, KeyError, TypeError, ValueError) as exc:
            # Do not substitute an ad hoc message: an assignment answer is not
            # safe to deliver unless it carries the renderer's trusted boundary.
            raise V12ServiceError("clarification continuation renderer is unavailable", code="ledger_error") from exc
        if not isinstance(rendered, Mapping) or not isinstance(rendered.get("message"), str) or not isinstance(rendered.get("renderer"), Mapping):
            raise V12ServiceError("clarification continuation renderer is invalid", code="ledger_error")
        renderer = rendered["renderer"]
        if not isinstance(renderer.get("version"), str) or not isinstance(renderer.get("common_policy_digest"), str):
            raise V12ServiceError("clarification continuation renderer is invalid", code="ledger_error")
        result.update({
            "continuation_capability": delivery["continuation_capability"],
            "message": rendered["message"],
            "renderer": {
                "version": renderer["version"],
                "common_policy_digest": renderer["common_policy_digest"],
            },
        })
    elif delivery["state"] == "unavailable":
        result["unavailable_reason"] = delivery["unavailable_reason"]
    return result


def record_clarification(*, task_ref: str, binding_ref: str, response_original: str,
                         user_language: str,
                         add: list[Mapping[str, Any]] | None = None,
                         retire_item_refs: list[str] | None = None) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        delta = None if add is None and retire_item_refs is None else {"add": add or [], "retire_item_refs": retire_item_refs or []}
        issued = DecisionAggregate(store).record_clarification(
            task_id=canonical, binding_ref=binding_ref,
            response_original=response_original, user_language=user_language,
            steering_delta=delta,
        )
        result = _family_result(task_ref=task_ref, response_original=response_original, issued={
            **issued, "binding": {"clarification_binding": binding_ref, "decision_type": "clarification"},
        })
        delivery = _clarification_host_delivery(
            store=store, task_id=canonical, binding_ref=binding_ref,
        )
        if delivery is not None:
            result["host_delivery"] = delivery
        return result
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def open_plan_review(*, task_ref: str, plan_ref: str, prompt: str,
                     prompt_language: str) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        _, plan_id = store.resolve_task_reference(task_id=canonical, value=plan_ref, kinds=("plan",))
        issued = DecisionAggregate(store).open_plan_review(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type="plan", subject_id=plan_id,
        )
        result = _family_result(task_ref=task_ref, issued=issued)
        report = store._read(lambda connection: connection.execute(
            "SELECT report_id, delegation_id, task_id, content_digest, semantic_status FROM reports WHERE report_id=? AND project_hash=?",
            (plan_id, store.project_hash),
        ).fetchone())
        if report is not None:
            result["approval_view"] = legacy._ready_approval_view(store, dict(report))
        return result
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def record_plan_review(*, task_ref: str, binding_ref: str, outcome: str,
                       response_original: str, user_language: str) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        issued = DecisionAggregate(store).record_plan_review(
            task_id=canonical, binding_ref=binding_ref, outcome=outcome,
            response_original=response_original, user_language=user_language,
        )
        return _family_result(task_ref=task_ref, response_original=response_original, issued={
            **issued, "binding": {"clarification_binding": binding_ref, "decision_type": "plan_review"},
        })
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def open_steering(*, task_ref: str, prompt: str, prompt_language: str,
                  assignment_ref: str | None = None) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        assignment_id = None if assignment_ref is None else store.resolve_task_reference(
            task_id=canonical, value=assignment_ref, kinds=("assignment",)
        )[1]
        issued = DecisionAggregate(store).open_steering(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type="task", subject_id=canonical, assignment_id=assignment_id,
        )
        return _family_result(task_ref=task_ref, issued=issued)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def record_steering(*, task_ref: str, binding_ref: str, response_original: str,
                    user_language: str, steering_delta: Mapping[str, Any] | None = None,
                    supersedes_decision_ref: str | None = None,
                    add: list[Mapping[str, Any]] | None = None,
                    retire_item_refs: list[str] | None = None) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        if add is not None or retire_item_refs is not None:
            steering_delta = {"add": add or [], "retire_item_refs": retire_item_refs or []}
        if steering_delta is None:
            raise V12ServiceError("steering delta is required", code="invalid_argument", details={"field": "response"})
        supersedes = None if supersedes_decision_ref is None else store.resolve_task_reference(
            task_id=canonical, value=supersedes_decision_ref, kinds=("decision",)
        )[1]
        issued = DecisionAggregate(store).record_steering(
            task_id=canonical, binding_ref=binding_ref,
            response_original=response_original, user_language=user_language,
            steering_delta=steering_delta,
            supersedes_decision_id=supersedes,
        )
        return _family_result(task_ref=task_ref, response_original=response_original, issued={
            **issued, "binding": {"clarification_binding": binding_ref, "decision_type": "steer"},
        })
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def record_decision(*, task_ref: str, binding_ref: str, response: Mapping[str, Any]) -> dict[str, Any]:
    """Record a decision through one binding-driven public operation."""
    if not isinstance(response, Mapping):
        raise V12ServiceError("decision response is invalid", code="invalid_argument", details={"field": "response"})
    text = response.get("response_original")
    language = response.get("user_language")
    if not isinstance(text, str) or not isinstance(language, str):
        raise V12ServiceError("decision response is incomplete", code="invalid_argument", details={"field": "response"})
    store, canonical = _task_store(task_ref)
    row = store._read(lambda connection: connection.execute(
        "SELECT decision_type FROM clarification_bindings WHERE clarification_binding=? AND project_hash=?",
        (binding_ref, store.project_hash),
    ).fetchone())
    if row is None:
        raise V12ServiceError("decision binding was not found", code="clarification_binding_not_found")
    family = str(row["decision_type"])
    # The public operation is deliberately one flattened request.  Family is
    # server-owned by the binding, so reject fields that belong to another
    # family only after resolving that binding.  This keeps the advertised
    # schema concrete without allowing a caller to smuggle cross-family state.
    has_steering = "add" in response or "retire_item_refs" in response
    has_outcome = "outcome" in response
    has_supersession = "supersedes_decision_ref" in response
    if family == "clarification":
        if has_outcome or has_supersession:
            raise V12ServiceError("clarification response contains unsupported family fields", code="clarification_binding_mismatch", details={"field": "response"})
        delta = None
        if has_steering:
            delta = {"add": response.get("add", []), "retire_item_refs": response.get("retire_item_refs", [])}
        return record_clarification(task_ref=task_ref, binding_ref=binding_ref, response_original=text, user_language=language, steering_delta=delta)
    if family == "plan_review":
        if has_steering or has_supersession:
            raise V12ServiceError("plan review response contains unsupported family fields", code="clarification_binding_mismatch", details={"field": "response"})
        outcome = response.get("outcome")
        if not isinstance(outcome, str):
            raise V12ServiceError("plan review outcome is required", code="invalid_argument", details={"field": "response.outcome"})
        return record_plan_review(task_ref=task_ref, binding_ref=binding_ref, outcome=outcome, response_original=text, user_language=language)
    if family == "steer":
        if has_outcome:
            raise V12ServiceError("steering response contains unsupported family fields", code="clarification_binding_mismatch", details={"field": "response.outcome"})
        # These are the flattened public names.  The store/aggregate keeps
        # the domain value grouped as one delta so its validation and
        # supersession logic remain atomic.
        delta = {"add": response.get("add", []), "retire_item_refs": response.get("retire_item_refs", [])}
        if not any(delta.values()):
            raise V12ServiceError("steering delta is required", code="invalid_argument", details={"field": "response"})
        supersedes = response.get("supersedes_decision_ref")
        return record_steering(task_ref=task_ref, binding_ref=binding_ref, response_original=text, user_language=language, steering_delta=delta, supersedes_decision_ref=supersedes if isinstance(supersedes, str) else None)
    raise V12ServiceError("decision binding family is invalid", code="clarification_binding_mismatch")


def assess_governance(*, task_ref: str, mode: str, rationale: str = "",
                      risk_factors: list[str] | None = None) -> dict[str, Any]:
    payload = {"task_ref": task_ref, "mode": mode, "rationale": rationale, "risk_factors": risk_factors}
    return legacy.set_governance_mode(**payload, source="model")


def close_task(*, task_ref: str, verdict: str, evidence: object | None = None,
               unresolved_risks: object | None = None, follow_ups: object | None = None,
               completion_notes: object | None = None) -> dict[str, Any]:
    if evidence is None:
        inspected = legacy.inspect_task(task_ref=task_ref)
        evidence = {
            "source": "server_derived_task_state",
            "effective_contract": inspected.get("effective_contract"),
            "aggregate_coverage": inspected.get("aggregate_coverage"),
            "conformance_review": inspected.get("conformance_review"),
        }
    return legacy.submit_governance_closure(
        task_ref=task_ref, subject_type="task", subject_ref=task_ref, verdict=verdict,
        evidence=evidence, unresolved_risks=unresolved_risks, follow_ups=follow_ups,
        completion_notes=completion_notes,
    )


__all__ = [
    "open_task", "read_task", "open_assignment", "consume_assignment_evidence",
    "publish_plan", "publish_result", "publish_documentation",
    "open_clarification", "record_clarification", "open_plan_review", "record_plan_review", "open_steering", "record_steering",
    "assess_governance", "close_task",
]
