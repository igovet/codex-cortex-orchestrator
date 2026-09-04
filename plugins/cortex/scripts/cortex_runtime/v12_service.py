"""Facade-ready public operations for the independent Cortex V12 ledger.

The functions in this module are intentionally action-specific and keyword
only.  A V12 facade can call an operation as ``function(**arguments)`` without
loading V11 contracts or importing the executable server entry point.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cortex_runtime.v12_contract import REPORT_READ_MAX_BYTES, record_ref, record_ref_parts, task_ref as compact_task_ref
from cortex_runtime.v12_store import V12Store, V12StoreError


class V12ServiceError(ValueError):
    """A sanitized public error suitable for a facade response."""

    def __init__(self, message: str, *, code: str = "v12_invalid", details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})




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
        # An approval relation is anchored to this immutable report/view
        # snapshot. Global task chronology is deliberately irrelevant: later
        # governance events cannot make the already-issued
        # relation stale.
        candidate = store.human_view(task_id, relative, require_fresh=False)
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
    outcome_contracts: Any,
    context: Any = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record one task in the caller's isolated V12 project ledger."""
    # The public task contract has no separate verification input. Acceptance
    # remains linked to its outcome and must not be copied into a second task
    # dimension or coverage obligation.
    verification_plan: list[Any] = []
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
        outcome_contracts=outcome_contracts,
        context=context,
        idempotency_key=idempotency_key,
    )


def inspect_task(*, task_ref: str, after_sequence: int = 0,
                 limit: int = 50) -> dict[str, Any]:
    """Read a task, its delegations/reports, and its ordered local timeline."""
    store, canonical = _task_store(task_ref)
    return _call_task(
        canonical, "inspect_task", task_id=canonical,
        after_sequence=after_sequence, limit=limit, store=store,
    )


def read_delegation(*, delegation_ref: str, after_sequence: int = 0) -> dict[str, Any]:
    """Read one delegation; its durable ID resolves and verifies the owner task."""
    store, canonical = _record_store(delegation_ref, label="delegation_id")
    return _call_task(canonical, "read_delegation", delegation_id=canonical, after_sequence=after_sequence, limit=50, store=store)


def read_reports(*, report_refs: list[str], sections: list[str] | None = None, cursor: str | None = None, consumer_delegation_ref: str | None = None) -> dict[str, Any]:
    """Read report metadata, or declared worker-owned report bodies.

    Calls without a consuming delegation are metadata-only and never create a
    receipt.  A body read is attributable only to a delegation that declared
    the exact finalized inputs.
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
    # The public operation deliberately has no caller-selected byte budget.
    # Metadata reads omit the consumer; worker reads receive one server-bounded
    # body page and continue only with the returned opaque cursor.
    page_bytes = REPORT_READ_MAX_BYTES if consumer_delegation_id is not None else 0
    return _call_task(canonical_reports[0], "read_reports", report_ids=canonical_reports, sections=sections, cursor=cursor, max_bytes=page_bytes, consumer_delegation_id=consumer_delegation_id, store=store)




__all__ = [
    "V12ServiceError",
    "create_task",
    "inspect_task",
    "read_delegation",
    "read_reports",
]
