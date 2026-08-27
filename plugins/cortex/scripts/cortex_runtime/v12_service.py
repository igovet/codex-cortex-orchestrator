"""Facade-ready public operations for the independent Cortex V12 ledger.

The functions in this module are intentionally action-specific and keyword
only.  A V12 facade can call an operation as ``function(**arguments)`` without
loading V11 contracts or importing the executable server entry point.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from cortex_runtime.v12_contract import REPORT_READ_MAX_BYTES, REPORT_READ_MAX_BYTES_STRING_PATTERN, task_ref as compact_task_ref
from cortex_runtime.v12_store import V12Store, V12StoreError


class V12ServiceError(ValueError):
    """A sanitized public error suitable for a facade response."""

    def __init__(self, message: str, *, code: str = "v12_invalid", details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def _normalize_report_read_budget(value: object, *, field: str) -> int:
    """Normalize the bounded read budget at the public service boundary."""
    if isinstance(value, bool):
        raise V12ServiceError(f"{field} is invalid", code="invalid_argument", details={"field": field})
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str) and re.fullmatch(REPORT_READ_MAX_BYTES_STRING_PATTERN, value):
        normalized = int(value)
    else:
        raise V12ServiceError(f"{field} is invalid", code="invalid_argument", details={"field": field})
    if not 0 <= normalized <= REPORT_READ_MAX_BYTES:
        raise V12ServiceError(f"{field} is invalid", code="invalid_argument", details={"field": field})
    return normalized


def _create_store(project_root: object) -> V12Store:
    try:
        return V12Store(project_root)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _task_store(task_ref: object | None = None, *, task_id: object | None = None) -> tuple[V12Store, str]:
    """Resolve a public compact reference once, retaining only canonical IDs.

    ``task_id`` remains a direct-service compatibility locator for historical
    callers.  It is intentionally absent from newly advertised task-anchored
    MCP schemas, so public calls cannot offer ambiguous anchor alternatives.
    """
    try:
        if task_ref is not None:
            store, canonical = V12Store.for_task_ref(task_ref)
            if task_id is not None and store._task_identifier(task_id) != canonical:
                raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
            return store, canonical
        if task_id is not None:
            store = V12Store.for_task_id(task_id)
            return store, str(task_id)
        raise V12StoreError("task_ref is required", code="invalid_argument", details={"field": "task_ref"})
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _record_store(record_id: object, *, label: str) -> tuple[V12Store, str]:
    try:
        if isinstance(record_id, str) and record_id.startswith(("d_", "r_", "i_", "u_")):
            return V12Store.for_record_ref(record_id, label=label)
        store = V12Store.for_record_id(record_id, label=label)
        return store, str(record_id)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _record_in_task(store: V12Store, value: object | None, *, label: str) -> str | None:
    if value is None:
        return None
    try:
        return store.resolve_record_ref(value, label=label)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _record_list_in_task(store: V12Store, values: list[str] | None, *, label: str) -> list[str] | None:
    if values is None:
        return None
    if not isinstance(values, list):
        return values
    return [_record_in_task(store, value, label=label) for value in values]


def _approved_gate_evidence(store: V12Store, task_id: str) -> tuple[str, str] | None:
    """Return only a fully verified persisted plan/approval pair.

    This is intentionally a read-only convenience for the public creation
    boundary.  The store remains authoritative and repeats all governance
    checks inside the create transaction; an incomplete or inconsistent gate
    therefore cannot be turned into guessed evidence here.
    """
    def read(connection: Any) -> tuple[str, str] | None:
        gate = store._governance_gate(connection, task_id)
        if not gate or not gate.get("plan_required"):
            return None
        plan_id = gate.get("plan_report_id")
        decision_id = gate.get("approval_decision_id")
        digest = gate.get("plan_digest")
        if not all(isinstance(value, str) and value for value in (plan_id, decision_id, digest)):
            return None
        try:
            plan = store._report(connection, plan_id, task_id=task_id)
            decision = store._decision(connection, decision_id, task_id=task_id)
        except V12StoreError:
            return None
        if (
            plan.get("report_type") != "plan"
            or plan.get("assembly_state") != "finalized"
            or plan.get("status") != "completed"
            or plan.get("content_digest") != digest
            or decision.get("decision_type") != "approve"
            or decision.get("subject_type") != "plan"
            or decision.get("subject_id") != plan_id
            or decision.get("subject_digest") != digest
        ):
            return None
        return plan_id, decision_id

    return store._read(read)


def _task_list_in_project(store: V12Store, values: list[str] | None) -> list[str] | None:
    """Resolve compact task locators and retain only same-shard canonical IDs."""
    if values is None:
        return None
    if not isinstance(values, list):
        return values
    resolved: list[str] = []
    for value in values:
        try:
            candidate, task_id = V12Store.for_task_ref(value)
        except V12StoreError as exc:
            raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
        if candidate.project_hash != store.project_hash:
            raise V12ServiceError("reference does not belong to the task project", code="cross_project_reference")
        resolved.append(task_id)
    return _unique_canonical_records(resolved)


def _unique_canonical_records(values: list[str] | None) -> list[str] | None:
    """Preserve first-seen order while collapsing exact resolved record IDs."""
    if values is None or not isinstance(values, list):
        return values
    seen: set[str] = set()
    return [item for item in values if not (item in seen or seen.add(item))]


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
    return _with_human_view(store, dict(result)) | {"replayed": bool(replayed)}


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
    }
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
    task_contract_version: Any,
    requirements: Any,
    constraints: Any,
    acceptance_criteria: Any,
    verification_plan: Any,
    context: Any = None,
    task_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record one task in the caller's isolated V12 project ledger."""
    return _mutation_store(
        _create_store(project_root),
        "create_task",
        objective=objective,
        user_request_original=user_request_original,
        user_language=user_language,
        task_contract_version=task_contract_version,
        requirements=requirements,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        verification_plan=verification_plan,
        context=context,
        task_id=task_id,
        idempotency_key=idempotency_key,
    )


def inspect_task(*, task_ref: str | None = None, task_id: str | None = None, after_sequence: int = 0, limit: int = 50) -> dict[str, Any]:
    """Read a task, its delegations/reports, and its ordered local timeline."""
    store, canonical = _task_store(task_ref, task_id=task_id)
    return _call_task(canonical, "inspect_task", task_id=canonical, after_sequence=after_sequence, limit=limit, store=store)


def create_delegation(
    *,
    task_ref: str | None = None,
    task_id: str | None = None,
    objective: Any,
    role: Any,
    profile_name: Any,
    scope: Any,
    instructions: Any,
    delegation_id: str | None = None,
    parent_delegation_id: str | None = None,
    input_report_ids: list[str] | None = None,
    input_decision_ids: list[str] | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    idempotency_key: str | None = None,
    parent_delegation_ref: str | None = None,
    input_report_refs: list[str] | None = None,
    input_decision_refs: list[str] | None = None,
    approval_decision_ref: str | None = None,
) -> dict[str, Any]:
    """Persist a coordinator-supplied delegation without selecting a route."""
    store, canonical = _task_store(task_ref, task_id=task_id)
    parent_delegation_id = parent_delegation_ref if parent_delegation_ref is not None else parent_delegation_id
    input_report_ids = _unique_canonical_records(_record_list_in_task(
        store,
        input_report_refs if input_report_refs is not None else input_report_ids,
        label="report_id",
    ))
    input_decision_ids = _unique_canonical_records(_record_list_in_task(
        store,
        input_decision_refs if input_decision_refs is not None else input_decision_ids,
        label="decision_id",
    ))
    approval_decision_id = _record_in_task(store, approval_decision_ref, label="decision_id")
    # A coordinator may rely on the current task's already-approved gate.  In
    # that case derive only the exact persisted pair; never synthesize or
    # replace caller-provided references.  The store revalidates the pair in
    # the create transaction, including its profile-specific rules.
    gate_evidence = _approved_gate_evidence(store, canonical)
    if gate_evidence is not None:
        plan_id, gate_decision_id = gate_evidence
        if plan_id not in (input_report_ids or []):
            input_report_ids = [*(input_report_ids or []), plan_id]
        if approval_decision_id is None:
            approval_decision_id = gate_decision_id
    if approval_decision_id is not None and approval_decision_id not in (input_decision_ids or []):
        input_decision_ids = [*(input_decision_ids or []), approval_decision_id]
    return _mutation_store(
        store,
        "create_delegation",
        task_id=canonical,
        objective=objective,
        role=role,
        profile_name=profile_name,
        scope=scope,
        instructions=instructions,
        delegation_id=delegation_id,
        parent_delegation_id=_record_in_task(store, parent_delegation_id, label="delegation_id"),
        input_report_ids=input_report_ids,
        input_decision_ids=input_decision_ids,
        model=model,
        reasoning_effort=reasoning_effort,
        idempotency_key=idempotency_key,
    )


def read_delegation(*, delegation_id: str | None = None, delegation_ref: str | None = None, after_sequence: int = 0, limit: int = 50, task_id: str | None = None) -> dict[str, Any]:
    """Read one delegation; its durable ID resolves and verifies the owner task."""
    store, canonical = _record_store(delegation_ref if delegation_ref is not None else delegation_id, label="delegation_id")
    return _call_task(canonical, "read_delegation", task_id=task_id, delegation_id=canonical, after_sequence=after_sequence, limit=limit, store=store)


def submit_report(
    *,
    delegation_id: str | None = None,
    delegation_ref: str | None = None,
    report_type: Any = None,
    status: Any = None,
    content: Any = None,
    report_id: str | None = None,
    report_ref: str | None = None,
    mode: str | None = None,
    chunk_index: int | None = None,
    section: str | None = None,
    expected_chunk_count: int | None = None,
    expected_content_digest: str | None = None,
    abort_reason_en: Any = None,
    supersedes_report_id: str | None = None,
    supersedes_report_ref: str | None = None,
    review_policy: str | None = None,
    idempotency_key: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Append one report; the delegation ID resolves and verifies its task."""
    store, canonical_delegation = _record_store(delegation_ref if delegation_ref is not None else delegation_id, label="delegation_id")
    return _mutation_store(
        store,
        "submit_report",
        task_id=task_id,
        delegation_id=canonical_delegation,
        report_type=report_type,
        status=status,
        content=content,
        report_id=_record_in_task(store, report_ref if report_ref is not None else report_id, label="report_id"),
        mode=mode,
        chunk_index=chunk_index,
        section=section,
        expected_chunk_count=expected_chunk_count,
        expected_content_digest=expected_content_digest,
        abort_reason_en=abort_reason_en,
        supersedes_report_id=_record_in_task(store, supersedes_report_ref if supersedes_report_ref is not None else supersedes_report_id, label="report_id"),
        review_policy=review_policy,
        idempotency_key=idempotency_key,
    )


def read_reports(*, report_ids: list[str] | None = None, report_refs: list[str] | None = None, sections: list[str] | None = None, cursor: str | None = None, max_bytes: int | None = None, byte_budget: int | None = None, consumer_delegation_id: str | None = None, consumer_delegation_ref: str | None = None, reader_kind: str | None = None, task_id: str | None = None) -> dict[str, Any]:
    """Read ordered report chunks and append classified structural receipts."""
    normalized_max = _normalize_report_read_budget(max_bytes, field="max_bytes") if max_bytes is not None else None
    normalized_alias = _normalize_report_read_budget(byte_budget, field="byte_budget") if byte_budget is not None else None
    if normalized_alias is not None:
        if normalized_max is not None and normalized_max != normalized_alias:
            raise V12ServiceError("max_bytes and byte_budget conflict", code="invalid_argument", details={"field": "byte_budget"})
        normalized_max = normalized_alias
    if normalized_max is None:
        normalized_max = REPORT_READ_MAX_BYTES
    report_ids = report_refs if report_refs is not None else report_ids
    if not isinstance(report_ids, list) or not report_ids:
        raise V12ServiceError("report_ids are invalid", code="invalid_argument", details={"field": "report_ids"})
    store, _canonical = _record_store(report_ids[0], label="report_id")
    canonical_reports = _record_list_in_task(store, report_ids, label="report_id")
    consumer = consumer_delegation_ref if consumer_delegation_ref is not None else consumer_delegation_id
    return _call_task(canonical_reports[0], "read_reports", task_id=task_id, report_ids=canonical_reports, sections=sections, cursor=cursor, max_bytes=normalized_max, consumer_delegation_id=_record_in_task(store, consumer, label="delegation_id"), reader_kind=reader_kind, store=store)


def set_governance_mode(
    *,
    task_ref: str | None = None,
    task_id: str | None = None,
    mode: str,
    rationale: Any = None,
    reason: Any = None,
    risk_factors: Any = None,
    source: str = "model",
    initiative_id: str | None = None,
    idempotency_key: str | None = None,
    initiative_ref: str | None = None,
) -> dict[str, Any]:
    """Record an informational governance assessment for a local task."""
    store, canonical = _task_store(task_ref, task_id=task_id)
    return _mutation_store(
        store,
        "set_governance_mode",
        task_id=canonical,
        mode=mode,
        rationale=rationale if rationale is not None else reason,
        risk_factors=risk_factors,
        source=source,
        initiative_id=_record_in_task(store, initiative_ref if initiative_ref is not None else initiative_id, label="initiative_id"),
        idempotency_key=idempotency_key,
    )


def record_initiative(
    *,
    task_ref: str | None = None,
    task_id: str | None = None,
    goal: Any,
    initiative_id: str | None = None,
    parent_initiative_id: str | None = None,
    risk: Any = None,
    status: str | None = None,
    dependencies: list[str] | None = None,
    linked_task_ids: list[str] | None = None,
    linked_delegation_ids: list[str] | None = None,
    linked_report_ids: list[str] | None = None,
    linked_decision_ids: list[str] | None = None,
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
    store, canonical = _task_store(task_ref, task_id=task_id)
    return _mutation_store(
        store,
        "record_initiative",
        task_id=canonical,
        goal=goal,
        initiative_id=_record_in_task(store, initiative_ref if initiative_ref is not None else initiative_id, label="initiative_id"),
        parent_initiative_id=_record_in_task(store, parent_initiative_ref if parent_initiative_ref is not None else parent_initiative_id, label="initiative_id"),
        risk=risk,
        status=status,
        dependencies=_record_list_in_task(store, dependency_refs if dependency_refs is not None else dependencies, label="initiative_id"),
        linked_task_ids=_task_list_in_project(store, linked_task_refs) if linked_task_refs is not None else linked_task_ids,
        linked_delegation_ids=_unique_canonical_records(_record_list_in_task(store, linked_delegation_refs if linked_delegation_refs is not None else linked_delegation_ids, label="delegation_id")),
        linked_report_ids=_unique_canonical_records(_record_list_in_task(store, linked_report_refs if linked_report_refs is not None else linked_report_ids, label="report_id")),
        linked_decision_ids=_unique_canonical_records(_record_list_in_task(store, linked_decision_refs if linked_decision_refs is not None else linked_decision_ids, label="decision_id")),
        notes=notes,
        idempotency_key=idempotency_key,
    )


def inspect_governance(*, task_ref: str | None = None, task_id: str | None = None, initiative_id: str | None = None, initiative_ref: str | None = None, after_sequence: int = 0, limit: int = 50) -> dict[str, Any]:
    """Read task-, initiative-, or project-scoped governance information."""
    store, canonical = _task_store(task_ref, task_id=task_id)
    return _call_task(
        canonical,
        "inspect_governance",
        task_id=canonical,
        initiative_id=_record_in_task(store, initiative_ref if initiative_ref is not None else initiative_id, label="initiative_id"),
        after_sequence=after_sequence,
        limit=limit,
        store=store,
    )


def submit_governance_closure(
    *,
    task_ref: str | None = None,
    task_id: str | None = None,
    subject_type: str,
    verdict: str,
    subject_id: str | None = None,
    evidence: Any = None,
    unresolved_risks: Any = None,
    follow_ups: Any = None,
    initiative_status: str | None = None,
    completion_notes: Any = None,
    idempotency_key: str | None = None,
    subject_ref: str | None = None,
) -> dict[str, Any]:
    """Record a closure statement; it is informational and opens no gate."""
    store, canonical = _task_store(task_ref, task_id=task_id)
    if subject_type == "task":
        supplied = subject_ref if subject_ref is not None else subject_id
        if supplied not in {canonical, compact_task_ref(canonical)}:
            raise V12ServiceError(
                "task closure must use the exact anchored task reference",
                code="cross_project_reference",
                details={"field": "subject_ref"},
            )
    return _mutation_store(
        store,
        "submit_governance_closure",
        task_id=canonical,
        subject_type=subject_type,
        subject_id=canonical if subject_type == "task" else _record_in_task(store, subject_ref if subject_ref is not None else subject_id, label="initiative_id"),
        verdict=verdict,
        evidence=evidence,
        unresolved_risks=unresolved_risks,
        follow_ups=follow_ups,
        initiative_status=initiative_status,
        completion_notes=completion_notes,
        idempotency_key=idempotency_key,
    )


def record_user_decision(
    *,
    task_ref: str | None = None,
    task_id: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    decision_type: str | None = None,
    prompt_en: Any = None,
    response_original: Any = None,
    response_en: Any = None,
    user_language: Any = None,
    subject_digest: str | None = None,
    approval_handle: str | None = None,
    approval_view_content_digest: str | None = None,
    approval_view_source_sequence: int | None = None,
    supersedes_decision_id: str | None = None,
    idempotency_key: str | None = None,
    subject_ref: str | None = None,
    supersedes_decision_ref: str | None = None,
    # Compatibility aliases used by pre-V12 facades.  They are accepted only
    # as one complete legacy shape and are normalized before storage.
    report_ref: str | None = None,
    report_content_digest: str | None = None,
    decision: str | None = None,
    user_response_original: Any = None,
    english_normalization: Any = None,
) -> dict[str, Any]:
    """Persist an asserted ordinary-chat user decision as non-authoritative evidence."""
    store, canonical = _task_store(task_ref, task_id=task_id)
    legacy_values = (report_ref, report_content_digest, decision, user_response_original, english_normalization)
    legacy_present = any(value is not None for value in legacy_values)
    if legacy_present:
        if not all(value is not None for value in legacy_values) or any(
            value is not None
            for value in (subject_type, subject_id, subject_digest, decision_type, prompt_en, response_original, response_en, user_language)
        ):
            raise V12ServiceError(
                "legacy decision aliases must be complete and cannot be mixed with current fields",
                code="validation_error",
                details={"field": "record_user_decision"},
            )
        try:
            def decision_defaults(connection: Any) -> tuple[Any, Any]:
                task = store._task(connection, canonical)
                return task["objective"], task["user_language"]
            prompt_en, user_language = store._read(decision_defaults)
        except V12StoreError as exc:
            raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
        subject_type = "plan"
        subject_ref = _record_in_task(store, report_ref, label="report_id")
        subject_digest = report_content_digest
        decision_type = decision
        response_original = user_response_original
        response_en = english_normalization
        # Very old callers did not have approval-view fields.  For an approve
        # request, obtain a fresh server-verified view and opaque handle rather
        # than weakening the current approval contract or inventing values.
        if str(decision).lower() == "approve" and all(
            value is None for value in (approval_handle, approval_view_content_digest, approval_view_source_sequence)
        ):
            view = store.human_view(canonical, f"plans/revisions/{subject_ref}.md")
            if view.get("status") != "ready" or view.get("content_digest") is None or view.get("source_sequence") is None:
                raise V12ServiceError("approval view is not currently ready", code="approval_view_not_ready")
            try:
                approval_handle = store.ready_approval_handle(
                    task_id=canonical,
                    report_id=subject_ref,
                    report_content_digest=report_content_digest,
                    view_relative_path=f"plans/revisions/{subject_ref}.md",
                    view_content_digest=view["content_digest"],
                    view_source_sequence=view["source_sequence"],
                )
            except V12StoreError as exc:
                raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
            approval_view_content_digest = view["content_digest"]
            approval_view_source_sequence = view["source_sequence"]
    return _mutation_store(
        store,
        "record_user_decision",
        task_id=canonical,
        subject_type=subject_type,
        subject_id=canonical if subject_type == "task" else _record_in_task(store, subject_ref if subject_ref is not None else subject_id, label={"plan": "report_id", "report": "report_id", "delegation": "delegation_id", "initiative": "initiative_id"}.get(subject_type, "subject_id")),
        subject_digest=subject_digest,
        decision_type=decision_type,
        prompt_en=prompt_en,
        response_original=response_original,
        response_en=response_en,
        user_language=user_language,
        approval_handle=approval_handle,
        approval_view_content_digest=approval_view_content_digest,
        approval_view_source_sequence=approval_view_source_sequence,
        supersedes_decision_id=_record_in_task(store, supersedes_decision_ref if supersedes_decision_ref is not None else supersedes_decision_id, label="decision_id"),
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
