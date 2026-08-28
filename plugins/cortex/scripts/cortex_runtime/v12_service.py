"""Facade-ready public operations for the independent Cortex V12 ledger.

The functions in this module are intentionally action-specific and keyword
only.  A V12 facade can call an operation as ``function(**arguments)`` without
loading V11 contracts or importing the executable server entry point.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cortex_runtime.v12_contract import REPORT_READ_MAX_BYTES, record_ref_parts, task_ref as compact_task_ref
from cortex_runtime.v12_store import V12Store, V12StoreError


class V12ServiceError(ValueError):
    """A sanitized public error suitable for a facade response."""

    def __init__(self, message: str, *, code: str = "v12_invalid", details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def _normalize_execution_evidence(value: object) -> dict[str, Any]:
    """Keep every closure response on the canonical execution-outcome shape."""
    if isinstance(value, Mapping):
        evidence_status = value.get("evidence_status")
        finalized = value.get("finalized_report_count")
        completed = value.get("completed_report_count")
        outcome = value.get("outcome")
        if (
            evidence_status in {"finalized_reports_present", "no_finalized_reports"}
            and isinstance(finalized, int) and not isinstance(finalized, bool) and finalized >= 0
            and isinstance(completed, int) and not isinstance(completed, bool)
            and 0 <= completed <= finalized
            and outcome in {None, "completed", "incomplete"}
            and (finalized > 0) == (evidence_status == "finalized_reports_present")
            and (finalized > 0 or (completed == 0 and outcome is None))
            and (completed == 0 or outcome is not None)
            and (outcome != "completed" or completed > 0)
        ):
            return {
                "evidence_status": evidence_status,
                "finalized_report_count": finalized,
                "completed_report_count": completed,
                "outcome": outcome,
            }
    return {
        "evidence_status": "no_finalized_reports",
        "finalized_report_count": 0,
        "completed_report_count": 0,
        "outcome": None,
    }


def _validate_report_read_budget(value: object) -> int:
    """Validate the one canonical integer read budget at the service boundary."""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= REPORT_READ_MAX_BYTES:
        raise V12ServiceError("max_bytes is invalid", code="invalid_argument", details={"field": "max_bytes"})
    return value


def _task_store(task_ref: object) -> tuple[V12Store, str]:
    """Resolve one exact emitted compact task locator for a public operation."""
    try:
        return V12Store.for_task_ref(task_ref)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _record_store(record_ref: object, *, label: str) -> tuple[V12Store, str]:
    """Resolve one exact emitted compact record locator for a public operation."""
    try:
        return V12Store.for_record_ref(record_ref, label=label)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _record_in_task(store: V12Store, value: object | None, *, label: str) -> str | None:
    """Resolve one exact typed compact record locator within the anchored task."""
    if value is None:
        return None
    if record_ref_parts(value, label=label) is None:
        raise V12ServiceError(f"{label} is invalid", code="invalid_identifier", details={"field": label})
    try:
        return store.resolve_record_ref(value, label=label)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _record_list_in_task(store: V12Store, values: list[str] | None, *, label: str) -> list[str] | None:
    if values is None:
        return None
    if not isinstance(values, list):
        raise V12ServiceError(f"{label} is invalid", code="invalid_argument", details={"field": label})
    resolved = [_record_in_task(store, value, label=label) for value in values]
    if len(set(resolved)) != len(resolved):
        raise V12ServiceError(f"{label} must be unique", code="invalid_argument", details={"field": label})
    return [value for value in resolved if value is not None]


def _task_list_in_project(store: V12Store, values: list[str] | None) -> list[str] | None:
    """Resolve compact task locators and retain only same-shard canonical IDs."""
    if values is None:
        return None
    if not isinstance(values, list):
        raise V12ServiceError("linked_task_refs are invalid", code="invalid_argument", details={"field": "linked_task_refs"})
    resolved: list[str] = []
    for value in values:
        try:
            candidate, task_id = V12Store.for_task_ref(value)
        except V12StoreError as exc:
            raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
        if candidate.project_hash != store.project_hash:
            raise V12ServiceError("reference does not belong to the task project", code="cross_project_reference")
        resolved.append(task_id)
    if len(set(resolved)) != len(resolved):
        raise V12ServiceError("linked_task_refs must be unique", code="invalid_argument", details={"field": "linked_task_refs"})
    return resolved


def _create_store(project_root: object) -> V12Store:
    try:
        return V12Store(project_root)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _call_task(task_anchor: object, operation: str, *, store: V12Store | None = None, **arguments: Any) -> Any:
    store = _task_store(task_anchor) if store is None else store
    try:
        result = getattr(store, operation)(**arguments)
        result = _with_human_view(store, result)
        return result
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _mutation_store(store: V12Store, operation: str, **arguments: Any) -> dict[str, Any]:
    try:
        result, replayed = getattr(store, operation)(**arguments)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
    if not isinstance(result, dict):  # Defensive only: store results are JSON objects by contract.
        raise V12ServiceError("V12 storage returned an invalid result", code="storage_unavailable")
    # The public retry input is named idempotency_key.  Preserve that exact
    # callable name in every mutation receipt instead of requiring callers to
    # translate a server implementation detail back into an input.
    idempotency_key = arguments.get("idempotency_key")
    result_with_retry = dict(result) | {"replayed": bool(replayed)}
    if isinstance(idempotency_key, str):
        result_with_retry["idempotency_key"] = idempotency_key
    return _with_human_view(store, result_with_retry)


def _ready_approval_view(store: V12Store, report: Mapping[str, Any]) -> dict[str, Any]:
    """Return one server-issued plan-review snapshot, or an honest non-ready view.

    A report read appends its receipt before this response is assembled.  Retry a
    bounded two refreshes so a queued older projection cannot leave the first
    immediate read stale.  The handle is issued only after the exact path,
    report digest, view digest, and source sequence are cross-checked by the
    store; callers never construct a plan-review path.
    """
    task_id = str(report["task_id"])
    report_id = str(report["report_id"])
    relative = f"plans/revisions/{report_id}.md"
    view: dict[str, Any] = {"status": "unavailable", "path": None}
    for _attempt in range(2):
        candidate = store.human_view(task_id, relative)
        if candidate.get("status") == "ready":
            view = candidate
            break
        view = candidate
    approval: dict[str, Any] = {
        "report_id": report_id,
        "delegation_id": report["delegation_id"],
        "report_content_digest": report["content_digest"],
        "status": view.get("status"),
        "path": view.get("path"),
        "source_sequence": view.get("source_sequence"),
        "content_digest": view.get("content_digest"),
        "approval_handle": None,
        "semantic_status": report.get("semantic_status"),
    }
    if report.get("semantic_status") != "semantic_valid":
        approval.update({"status": "unavailable", "path": None, "source_sequence": None, "content_digest": None})
        return approval
    if view.get("status") == "ready":
        approval["markdown_link"] = view.get("markdown_link")
        try:
            approval["approval_handle"] = store.ready_approval_handle(
                task_id=task_id,
                report_id=report_id,
                report_content_digest=report["content_digest"],
                view_relative_path=relative,
                view_content_digest=view.get("content_digest"),
                view_source_sequence=view.get("source_sequence"),
            )
        except V12StoreError:
            # Never expose a ready path as approval evidence without its hard
            # relational handle.  The normal human view remains diagnostic.
            approval.update({"status": "stale", "path": None, "source_sequence": None, "content_digest": None})
    return approval


def _with_human_view(store: V12Store, result: Any) -> Any:
    """Attach volatile, verified view metadata after every canonical result.

    It is intentionally evaluated outside stored idempotency JSON: a replay
    can report a newly repaired view, while a later local edit is honestly
    stale/conflicted rather than frozen as "ready".
    """
    if not isinstance(result, dict):
        return result
    task: str | None = None
    relative: str | None = None
    value = result.get("report")
    if isinstance(value, Mapping) and isinstance(value.get("task_id"), str) and isinstance(value.get("report_id"), str):
        task = value["task_id"]
        relative = f"plans/revisions/{value['report_id']}.md" if value.get("report_type") == "plan" else f"reports/{value['report_id']}.md"
    approval_report: Mapping[str, Any] | None = None
    reports_value = result.get("reports")
    # Plan approval is a verified full-report read product.  Delegation reads
    # also expose compact report references, but must not manufacture an
    # approval view outside their advertised output contract.
    if (
        isinstance(reports_value, list)
        and "returned_content_bytes" in result
        and result.get("has_more") is False
        and result.get("next_cursor") is None
    ):
        plans = [
            item for item in reports_value
            if isinstance(item, Mapping)
            and item.get("report_type") == "plan"
            and item.get("assembly_state") == "finalized"
            and item.get("status") == "completed"
            and isinstance(item.get("task_id"), str)
            and isinstance(item.get("delegation_id"), str)
            and isinstance(item.get("report_id"), str)
            and isinstance(item.get("content_digest"), str)
        ]
        if len(plans) == 1:
            approval_report = plans[0]
            task = str(approval_report["task_id"])
            relative = f"plans/revisions/{approval_report['report_id']}.md"
    if task is None and isinstance(result.get("reports"), list):
        reports = result["reports"]
        if len(reports) == 1 and isinstance(reports[0], Mapping) and isinstance(reports[0].get("task_id"), str) and isinstance(reports[0].get("report_id"), str):
            task = reports[0]["task_id"]
            relative = f"plans/revisions/{reports[0]['report_id']}.md" if reports[0].get("report_type") == "plan" else f"reports/{reports[0]['report_id']}.md"
    # A finalized plan submission is already the authoritative report handoff.
    # Expose its verified approval binding in the same receipt so the
    # coordinator can progress from the worker acknowledgement without
    # re-reading the report body.  The binding is produced only by the same
    # server-side readiness checks used by read_reports.
    if (
        approval_report is None
        and isinstance(value, Mapping)
        and value.get("report_type") == "plan"
        and value.get("assembly_state") == "finalized"
        and value.get("status") == "completed"
        and isinstance(value.get("task_id"), str)
        and isinstance(value.get("delegation_id"), str)
        and isinstance(value.get("report_id"), str)
        and isinstance(value.get("content_digest"), str)
    ):
        approval_report = value
        task = str(value["task_id"])
        relative = f"plans/revisions/{value['report_id']}.md"
    if task is not None and relative is not None:
        result = dict(result)
        result["human_view"] = store.human_view(task, relative)
        if approval_report is not None:
            result["approval_view"] = _ready_approval_view(store, approval_report)
    return result


def create_task(
    *,
    project_root: object,
    objective: Any,
    user_request_original: Any,
    user_language: Any,
    requirements: Any,
    constraints: Any,
    acceptance_criteria: Any,
    verification_plan: Any,
    context: Any = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record one task in the caller's isolated V12 project ledger."""
    return _mutation_store(
        _create_store(project_root),
        "create_task",
        objective=objective,
        user_request_original=user_request_original,
        user_language=user_language,
        requirements=requirements,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        verification_plan=verification_plan,
        context=context,
        idempotency_key=idempotency_key,
    )


def inspect_task(*, task_ref: str, after_sequence: int = 0, limit: int = 50) -> dict[str, Any]:
    """Read a task, its delegations/reports, and its ordered local timeline."""
    store, canonical = _task_store(task_ref)
    return _call_task(canonical, "inspect_task", task_id=canonical, after_sequence=after_sequence, limit=limit, store=store)


def create_delegation(
    *,
    task_ref: str,
    objective: Any,
    role: Any,
    profile_name: Any,
    scope: Any,
    instructions: Any,
    model: str | None = None,
    reasoning_effort: str | None = None,
    idempotency_key: str | None = None,
    parent_delegation_ref: str | None = None,
    input_report_refs: list[str] | None = None,
    input_decision_refs: list[str] | None = None,
    outcome_assignments: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Persist a coordinator-supplied delegation without selecting a route."""
    store, canonical = _task_store(task_ref)
    input_report_ids = _record_list_in_task(store, input_report_refs, label="report_id")
    input_decision_ids = _record_list_in_task(store, input_decision_refs, label="decision_id")
    return _mutation_store(
        store,
        "create_delegation",
        task_id=canonical,
        objective=objective,
        role=role,
        profile_name=profile_name,
        scope=scope,
        instructions=instructions,
        parent_delegation_id=_record_in_task(store, parent_delegation_ref, label="delegation_id"),
        input_report_ids=input_report_ids,
        input_decision_ids=input_decision_ids,
        outcome_assignments=outcome_assignments,
        model=model,
        reasoning_effort=reasoning_effort,
        idempotency_key=idempotency_key,
    )


def read_delegation(*, delegation_ref: str, after_sequence: int = 0, limit: int = 50) -> dict[str, Any]:
    """Read one delegation; its durable ID resolves and verifies the owner task."""
    store, canonical = _record_store(delegation_ref, label="delegation_id")
    return _call_task(canonical, "read_delegation", delegation_id=canonical, after_sequence=after_sequence, limit=limit, store=store)


def submit_report(
    *,
    delegation_ref: str,
    report_type: Any = None,
    status: Any = None,
    content: Any = None,
    report_ref: str | None = None,
    mode: str | None = None,
    chunk_index: int | None = None,
    section: str | None = None,
    expected_chunk_count: int | None = None,
    expected_content_digest: str | None = None,
    abort_reason_en: Any = None,
    supersedes_report_ref: str | None = None,
    review_policy: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Append one report; the delegation ID resolves and verifies its task."""
    store, canonical_delegation = _record_store(delegation_ref, label="delegation_id")
    return _mutation_store(
        store,
        "submit_report",
        delegation_id=canonical_delegation,
        report_type=report_type,
        status=status,
        content=content,
        report_id=_record_in_task(store, report_ref, label="report_id"),
        mode=mode,
        chunk_index=chunk_index,
        section=section,
        expected_chunk_count=expected_chunk_count,
        expected_content_digest=expected_content_digest,
        abort_reason_en=abort_reason_en,
        supersedes_report_id=_record_in_task(store, supersedes_report_ref, label="report_id"),
        review_policy=review_policy,
        idempotency_key=idempotency_key,
    )


def read_reports(*, report_refs: list[str], sections: list[str] | None = None, cursor: str | None = None, max_bytes: int = REPORT_READ_MAX_BYTES, consumer_delegation_ref: str | None = None) -> dict[str, Any]:
    """Read bounded immutable report evidence for a worker or coordinator.

    A worker remains restricted to its declared predecessor reports.  A
    coordinator read is deliberately a separate, auditable reader kind: it
    can consume the authoritative body before a material orchestration
    decision, but it cannot impersonate a worker handoff.
    """
    if not isinstance(report_refs, list) or not report_refs:
        raise V12ServiceError("report_refs are invalid", code="invalid_argument", details={"field": "report_refs"})
    store, _canonical = _record_store(report_refs[0], label="report_id")
    canonical_reports = _record_list_in_task(store, report_refs, label="report_id")
    consumer_delegation_id = (
        None
        if consumer_delegation_ref is None
        else _record_in_task(store, consumer_delegation_ref, label="delegation_id")
    )
    return _call_task(canonical_reports[0], "read_reports", report_ids=canonical_reports, sections=sections, cursor=cursor, max_bytes=_validate_report_read_budget(max_bytes), consumer_delegation_id=consumer_delegation_id, reader_kind="worker" if consumer_delegation_id is not None else "coordinator", store=store)


def set_governance_mode(
    *,
    task_ref: str,
    mode: str,
    rationale: Any = None,
    risk_factors: Any = None,
    source: str = "model",
    idempotency_key: str | None = None,
    initiative_ref: str | None = None,
) -> dict[str, Any]:
    """Record an informational governance assessment for a local task."""
    store, canonical = _task_store(task_ref)
    return _mutation_store(
        store,
        "set_governance_mode",
        task_id=canonical,
        mode=mode,
        rationale=rationale,
        risk_factors=risk_factors,
        source=source,
        initiative_id=_record_in_task(store, initiative_ref, label="initiative_id"),
        idempotency_key=idempotency_key,
    )


def record_initiative(
    *,
    task_ref: str,
    goal: Any = None,
    risk: Any = None,
    status: str | None = None,
    notes: Any = None,
    idempotency_key: str | None = None,
    initiative_ref: str | None = None,
    parent_initiative_ref: str | None = None,
    dependency_refs: list[str] | None = None,
    linked_task_refs: list[str] | None = None,
    linked_delegation_refs: list[str] | None = None,
    linked_report_refs: list[str] | None = None,
    linked_decision_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Create or revise an informational initiative and its current links."""
    store, canonical = _task_store(task_ref)
    return _mutation_store(
        store,
        "record_initiative",
        task_id=canonical,
        goal=goal,
        initiative_id=_record_in_task(store, initiative_ref, label="initiative_id"),
        parent_initiative_id=_record_in_task(store, parent_initiative_ref, label="initiative_id"),
        risk=risk,
        status=status,
        dependencies=_record_list_in_task(store, dependency_refs, label="initiative_id"),
        linked_task_ids=_task_list_in_project(store, linked_task_refs),
        linked_delegation_ids=_record_list_in_task(store, linked_delegation_refs, label="delegation_id"),
        linked_report_ids=_record_list_in_task(store, linked_report_refs, label="report_id"),
        linked_decision_ids=_record_list_in_task(store, linked_decision_refs, label="decision_id"),
        notes=notes,
        idempotency_key=idempotency_key,
    )


def inspect_governance(*, task_ref: str, initiative_ref: str | None = None, after_sequence: int = 0, limit: int = 50) -> dict[str, Any]:
    """Read task-, initiative-, or project-scoped governance information."""
    store, canonical = _task_store(task_ref)
    return _call_task(
        canonical,
        "inspect_governance",
        task_id=canonical,
        initiative_id=_record_in_task(store, initiative_ref, label="initiative_id"),
        after_sequence=after_sequence,
        limit=limit,
        store=store,
    )


def submit_governance_closure(
    *,
    task_ref: str,
    subject_type: str,
    verdict: str,
    subject_ref: str,
    evidence: Any = None,
    unresolved_risks: Any = None,
    follow_ups: Any = None,
    initiative_status: str | None = None,
    completion_notes: Any = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Attempt advisory bookkeeping without changing neutral report evidence."""
    store, canonical = _task_store(task_ref)
    if subject_type == "task":
        if subject_ref != task_ref or subject_ref != compact_task_ref(canonical):
            raise V12ServiceError(
                "task closure must use the exact anchored task reference",
                code="cross_project_reference",
                details={"field": "subject_ref"},
            )
    subject_id = canonical if subject_type == "task" else _record_in_task(store, subject_ref, label="initiative_id")
    try:
        evidence = _normalize_execution_evidence(_call_task(canonical, "inspect_task", task_id=canonical, after_sequence=0, limit=1, store=store)["execution_outcome"])
    except V12ServiceError:
        # Do not fabricate success if both inspection and persistence are down.
        evidence = _normalize_execution_evidence(None)
    arguments = {
        "task_id": canonical, "subject_type": subject_type, "subject_id": subject_id,
        "verdict": verdict, "evidence": evidence, "unresolved_risks": unresolved_risks,
        "follow_ups": follow_ups, "initiative_status": initiative_status,
        "completion_notes": completion_notes, "idempotency_key": idempotency_key,
    }
    persisted: dict[str, Any] | None = None
    for attempt in (1, 2):
        try:
            persisted = _mutation_store(store, "submit_governance_closure", **arguments)
            break
        except V12ServiceError as error:
            if error.code not in {"storage_busy", "storage_unavailable"}:
                raise
            if attempt == 2:
                return {"closure": None, "initiative": None, "warnings": [], "next_action": {"task_ref": compact_task_ref(canonical)}, "execution_outcome": evidence, "closure_confirmation": {"inspection_status": "unconfirmed", "reason": "persistence_unavailable", "attempts": attempt}, "replayed": False}
    assert persisted is not None
    evidence = _normalize_execution_evidence(persisted.get("execution_outcome", evidence))
    closure = persisted.get("closure")
    closure_id = closure.get("closure_id") if isinstance(closure, Mapping) else None
    for attempt in (1, 2):
        try:
            task_inspection = _call_task(canonical, "inspect_task", task_id=canonical, after_sequence=0, limit=1, store=store)
            if subject_type == "task":
                advisory = task_inspection.get("advisory_closure")
                latest = advisory.get("latest_record") if isinstance(advisory, Mapping) else None
                observed = isinstance(latest, Mapping) and latest.get("closure_id") == closure_id
            else:
                governance = _call_task(canonical, "inspect_governance", task_id=canonical, initiative_id=subject_id, after_sequence=0, limit=200, store=store)
                observed = any(isinstance(item, Mapping) and item.get("closure_id") == closure_id for item in governance.get("closures", []))
            confirmation = "confirmed" if observed else "unconfirmed"
            reason = "record_inspected" if observed else "record_not_observed"
            return persisted | {
                "execution_outcome": _normalize_execution_evidence(task_inspection.get("execution_outcome", evidence)),
                "conformance_review": task_inspection.get("conformance_review"),
                "closure_confirmation": {"inspection_status": confirmation, "reason": reason, "attempts": attempt},
            }
        except V12ServiceError:
            if attempt == 2:
                return persisted | {"execution_outcome": evidence, "closure_confirmation": {"inspection_status": "unconfirmed", "reason": "inspection_unavailable", "attempts": attempt}}
    raise AssertionError("bounded advisory inspection did not return")


def record_user_decision(
    *,
    task_ref: str,
    subject_type: str,
    subject_ref: str,
    decision_type: str,
    prompt: Any,
    response_original: Any,
    user_language: Any,
    subject_digest: str | None = None,
    approval_handle: str | None = None,
    approval_view_content_digest: str | None = None,
    approval_view_source_sequence: int | None = None,
    idempotency_key: str | None = None,
    supersedes_decision_ref: str | None = None,
    steering_delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist an asserted ordinary-chat user decision as non-authoritative evidence."""
    store, canonical = _task_store(task_ref)
    if subject_type == "task" and subject_ref != task_ref:
        raise V12ServiceError(
            "task decision subject_ref must equal task_ref",
            code="invalid_decision_subject",
            details={"field": "subject_ref", "expected": "task_ref"},
        )
    approval_fields = (approval_handle, approval_view_content_digest, approval_view_source_sequence)
    if subject_type == "plan" and decision_type == "approve" and any(value is None for value in approval_fields):
        raise V12ServiceError(
            "approve requires the complete approval-view relation",
            code="approval_view_required",
            details={"field": "approval_handle"},
        )
    return _mutation_store(
        store,
        "record_user_decision",
        task_id=canonical,
        subject_type=subject_type,
        subject_id=canonical if subject_type == "task" else _record_in_task(store, subject_ref, label={"plan": "report_id", "report": "report_id", "delegation": "delegation_id", "initiative": "initiative_id"}.get(subject_type, "subject_id")),
        subject_digest=subject_digest,
        decision_type=decision_type,
        prompt=prompt,
        response_original=response_original,
        user_language=user_language,
        approval_handle=approval_handle,
        approval_view_content_digest=approval_view_content_digest,
        approval_view_source_sequence=approval_view_source_sequence,
        supersedes_decision_id=_record_in_task(store, supersedes_decision_ref, label="decision_id"),
        steering_delta=steering_delta,
        idempotency_key=idempotency_key,
    )


__all__ = [
    "V12ServiceError",
    "create_task",
    "inspect_task",
    "create_delegation",
    "read_delegation",
    "submit_report",
    "read_reports",
    "set_governance_mode",
    "record_initiative",
    "inspect_governance",
    "submit_governance_closure",
    "record_user_decision",
]
