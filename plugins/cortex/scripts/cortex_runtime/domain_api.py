"""Semantic public facade for the Cortex domain API.

The V12 ledger remains the durable historical substrate.  This module is the
only public-facing adapter: it turns semantic task/assignment/publication
operations into the existing immutable ledger records and never exports the
old storage-state-machine surface.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from cortex_runtime import v12_service as legacy
from cortex_runtime.v12_contract import record_ref
from cortex_runtime.v12_service import V12ServiceError, _task_store
from cortex_runtime.v12_store import V12Store, V12StoreError


def _operation_key(operation: str, payload: Mapping[str, object]) -> str:
    """Private deterministic identity for the legacy durable mutation layer."""
    encoded = json.dumps({"operation": operation, "payload": dict(payload)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "domain-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def open_task(*, project_root: str, objective: str, user_request_original: str,
              user_language: str, requirements: list[str], constraints: list[str],
              acceptance_criteria: list[str], context: object | None = None) -> dict[str, Any]:
    """Create or exactly replay one durable semantic task."""
    payload = {"project_root": project_root, "objective": objective,
               "user_request_original": user_request_original, "user_language": user_language,
               "requirements": requirements, "constraints": constraints,
               "acceptance_criteria": acceptance_criteria, "context": context}
    return legacy.create_task(**payload)


def read_task(*, task_ref: str, after_sequence: int = 0) -> dict[str, Any]:
    """Read bounded task state and aggregate evidence."""
    return legacy.inspect_task(task_ref=task_ref, after_sequence=after_sequence)


def issue_clarification(*, task_ref: str, prompt: str, prompt_language: str,
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


def open_decision(*, task_ref: str, prompt: str, prompt_language: str,
                  subject_type: str = "task", subject_ref: str | None = None,
                  assignment_ref: str | None = None) -> dict[str, Any]:
    """Issue one public scalar binding handle and non-callable context."""
    issued = issue_clarification(task_ref=task_ref, prompt=prompt,
                                 prompt_language=prompt_language,
                                 subject_type=subject_type,
                                 subject_ref=subject_ref,
                                 assignment_ref=assignment_ref)
    binding = issued.get("binding")
    if not isinstance(binding, Mapping) or not isinstance(binding.get("clarification_binding"), str):
        raise V12ServiceError("decision binding is unavailable", code="ledger_error")
    return {
        "task_ref": task_ref,
        "binding_ref": binding["clarification_binding"],
        "decision_context": {
            key: binding[key]
            for key in ("subject_type", "subject_ref", "decision_type", "prompt", "prompt_language", "effective_contract_revision", "consumed")
            if key in binding
        },
        "replayed": bool(issued.get("replayed")),
    }


def open_assignment(*, task_ref: str, objective: str, role: str, profile_name: str,
                    scope: str, instructions: str, model: str, reasoning_effort: str,
                    input_report_refs: list[str] | None = None,
                    input_decision_refs: list[str] | None = None,
                    parent_assignment_ref: str | None = None) -> dict[str, Any]:
    """Atomically bind a worker assignment to its current effective contract."""
    payload = {"task_ref": task_ref, "objective": objective, "role": role,
               "profile_name": profile_name, "scope": scope, "instructions": instructions,
               "model": model, "reasoning_effort": reasoning_effort,
               "input_report_refs": input_report_refs, "input_decision_refs": input_decision_refs,
               "parent_assignment_ref": parent_assignment_ref}
    result = legacy.create_delegation(
        task_ref=task_ref, objective=objective, role=role, profile_name=profile_name,
        scope=scope, instructions=instructions, model=model, reasoning_effort=reasoning_effort,
        input_report_refs=input_report_refs, input_decision_refs=input_decision_refs,
        parent_delegation_ref=parent_assignment_ref,
    )
    delegation = result.get("delegation")
    if isinstance(delegation, Mapping) and isinstance(delegation.get("delegation_id"), str):
        result = dict(result)
        result["assignment_ref"] = record_ref(str(delegation["delegation_id"]))
    return result


def consume_assignment_evidence(*, assignment_ref: str, cursor: str | None = None) -> dict[str, Any]:
    """Consume only declared predecessor report evidence for one assignment.

    Decision context is already resolved in the assignment brief and therefore
    cannot be accidentally routed through report-body storage.
    """
    try:
        store, delegation_id = V12Store.for_record_ref(assignment_ref, label="delegation_id")
        brief = store.read_delegation(delegation_id=delegation_id, after_sequence=0, limit=1)
        worker = brief.get("worker_brief")
        report_ids = worker.get("input_report_ids") if isinstance(worker, Mapping) else None
        if not isinstance(report_ids, list) or not report_ids:
            return {"assignment_ref": assignment_ref, "evidence": {"state": "none", "reports": []}, "next_cursor": None, "has_more": False}
        result = store.read_reports(
            report_ids=report_ids, cursor=cursor, max_bytes=65_536,
            consumer_delegation_id=delegation_id,
        )
        return {"assignment_ref": assignment_ref, "evidence": {"state": "consumed", **result}}
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _publish(*, assignment_ref: str, kind: str, evidence: object,
             status: str = "completed") -> dict[str, Any]:
    """Publish one complete immutable assignment outcome through the v14 store."""
    try:
        store, delegation_id = V12Store.for_record_ref(assignment_ref, label="delegation_id")
        return store.publish_domain_report(
            delegation_id=delegation_id, publication_kind="synthesis" if kind == "documentation" else kind, content=evidence, status=status,
        )
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def publish_plan(*, assignment_ref: str, evidence: object, status: str = "completed") -> dict[str, Any]:
    return _publish(assignment_ref=assignment_ref, kind="plan", evidence=evidence, status=status)


def publish_result(*, assignment_ref: str, evidence: object, status: str = "completed") -> dict[str, Any]:
    return _publish(assignment_ref=assignment_ref, kind="result", evidence=evidence, status=status)


def publish_documentation(*, assignment_ref: str, evidence: object, status: str = "completed") -> dict[str, Any]:
    return _publish(assignment_ref=assignment_ref, kind="documentation", evidence=evidence, status=status)


def record_decision(*, task_ref: str, binding_ref: str, response_original: str,
                    user_language: str) -> dict[str, Any]:
    """Record a decision only against a server-issued pending binding."""
    if not isinstance(binding_ref, str) or not binding_ref:
        raise V12ServiceError("decision binding is invalid", code="invalid_argument", details={"field": "binding_ref"})
    token = binding_ref
    store, canonical = _task_store(task_ref)
    resolved = store._read(lambda connection: connection.execute("SELECT subject_type,subject_id,decision_type,prompt,prompt_language FROM clarification_bindings WHERE clarification_binding=? AND project_hash=?", (token, store.project_hash)).fetchone())
    if resolved is None:
        raise V12ServiceError("decision binding was not found", code="clarification_binding_not_found")
    result, replayed = store.record_user_decision(
        task_id=canonical, subject_type=str(resolved["subject_type"]), subject_id=str(resolved["subject_id"]),
        subject_digest=None, decision_type=str(resolved["decision_type"]), prompt=str(resolved["prompt"]),
        response_original=response_original, user_language=str(resolved["prompt_language"]),
        clarification_binding=token,
        idempotency_key=_operation_key("record_decision", {"task_ref": task_ref, "binding_ref": token, "response_original": response_original, "user_language": user_language}),
    )
    return dict(result) | {"replayed": replayed}


def assess_governance(*, task_ref: str, mode: str, rationale: str = "",
                      risk_factors: list[str] | None = None) -> dict[str, Any]:
    payload = {"task_ref": task_ref, "mode": mode, "rationale": rationale, "risk_factors": risk_factors}
    return legacy.set_governance_mode(**payload, source="model")


def close_task(*, task_ref: str, verdict: str, evidence: object,
               unresolved_risks: object | None = None, follow_ups: object | None = None,
               completion_notes: object | None = None) -> dict[str, Any]:
    payload = {"task_ref": task_ref, "verdict": verdict, "evidence": evidence,
               "unresolved_risks": unresolved_risks, "follow_ups": follow_ups,
               "completion_notes": completion_notes}
    return legacy.submit_governance_closure(
        task_ref=task_ref, subject_type="task", subject_ref=task_ref, verdict=verdict,
        evidence=evidence, unresolved_risks=unresolved_risks, follow_ups=follow_ups,
        completion_notes=completion_notes,
    )


__all__ = [
    "open_task", "read_task", "issue_clarification", "open_decision", "open_assignment", "consume_assignment_evidence",
    "publish_plan", "publish_result", "publish_documentation", "record_decision",
    "assess_governance", "close_task",
]
