"""Semantic public facade for the Cortex domain API.

The V12 ledger remains the durable historical substrate.  This module is the
only public-facing adapter: it turns semantic task/assignment/publication
operations into the existing immutable ledger records and never exports the
old storage-state-machine surface.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from cortex_runtime import v12_service as ledger
from cortex_runtime.v12_contract import record_ref, task_ref as compact_task_ref
from cortex_runtime.v12_service import V12ServiceError, _record_list_in_task, _task_store
from cortex_runtime.v12_store import V12Store, V12StoreError
from cortex_runtime.domain_kernel import DecisionAggregate


_WORKER_TASK_REF = re.compile(r"^(t_[0-9a-f]{12})_([0-9a-f]{32})$")


def _resolve_task_context(value: str) -> tuple[V12Store, str, str | None, str]:
    """Resolve a coordinator or assignment-scoped task_ref without guessing."""
    match = _WORKER_TASK_REF.fullmatch(value) if isinstance(value, str) else None
    public_ref = match.group(1) if match else value
    store, task_id = _task_store(public_ref)
    if match is None:
        return store, task_id, None, public_ref
    suffix = match.group(2)
    rows = store._read(lambda connection: connection.execute(
        "SELECT delegation_id FROM delegations WHERE task_id=? AND delegation_id LIKE ? ORDER BY delegation_id",
        (task_id, "%" + suffix),
    ).fetchall())
    if len(rows) != 1:
        raise V12ServiceError("worker task context is unavailable", code="task_not_found")
    return store, task_id, str(rows[0]["delegation_id"]), public_ref


def _semantic_outcome(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "outcome": str(item.get("text", "")),
        "acceptance": list(item.get("acceptance_criteria", [])) if isinstance(item.get("acceptance_criteria"), list) else [],
        "constraints": list(item.get("constraints", [])) if isinstance(item.get("constraints"), list) else [],
        "verification": list(item.get("verification_criteria", [])) if isinstance(item.get("verification_criteria"), list) else [],
    }


def _publicize(value: Any) -> Any:
    """Remove private ledger identity and continuation metadata recursively."""
    if isinstance(value, list):
        return [_publicize(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    if "item_ref" in value and isinstance(value.get("text"), str):
        return _semantic_outcome(value)
    result: dict[str, Any] = {}
    for key, item in value.items():
        lowered = str(key).lower()
        if lowered in {"recommendation", "project_hash"} or lowered.endswith("_hash"):
            continue
        if key != "task_ref" and (
            lowered == "handles" or lowered.endswith("_id") or lowered.endswith("_ids")
            or lowered.endswith("_ref") or lowered.endswith("_refs")
            or "digest" in lowered or "cursor" in lowered or lowered in {"continuation", "continuations"}
        ):
            continue
        public_key = "outcome" if key == "text" and "item_ref" in value else str(key)
        result[public_key] = "partial" if item == "rework" else _publicize(item)
    return result


def _reject_private_fields(value: Any, *, path: str = "evidence") -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_private_fields(item, path=f"{path}[{index}]")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if (lowered == "handles" or lowered.endswith("_id") or lowered.endswith("_ids")
                    or lowered.endswith("_ref") or lowered.endswith("_refs")
                    or "digest" in lowered or "cursor" in lowered or "continuation" in lowered):
                raise V12ServiceError("private identifier fields are not accepted", code="invalid_argument", details={"field": f"{path}.{key}"})
            _reject_private_fields(item, path=f"{path}.{key}")


def _internalize_publication_evidence(store: V12Store, assignment_id: str, evidence: object) -> object:
    _reject_private_fields(evidence)
    if not isinstance(evidence, Mapping):
        return evidence
    result = dict(evidence)
    coverage = result.get("contract_coverage")
    if not isinstance(coverage, list):
        return result
    projection = store.read_delegation(delegation_id=assignment_id, after_sequence=0, limit=1)
    worker = projection.get("worker_brief") if isinstance(projection, Mapping) else None
    effective = worker.get("effective_contract") if isinstance(worker, Mapping) else None
    items = (effective.get("planning_items") or effective.get("assigned_items")) if isinstance(effective, Mapping) else None
    typed_items = [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []
    internal: list[dict[str, Any]] = []
    for row in coverage:
        if not isinstance(row, Mapping) or not isinstance(row.get("outcome"), str):
            raise V12ServiceError("publication coverage requires a semantic outcome name", code="invalid_argument", details={"field": "evidence.contract_coverage"})
        matches = _match_outcomes(typed_items, [row["outcome"]])
        internal.append({"item_ref": matches[0], **{key: value for key, value in row.items() if key != "outcome"}})
    result["contract_coverage"] = internal
    return result


def _match_outcomes(current_items: list[Mapping[str, Any]], requested: object) -> list[str]:
    if not isinstance(requested, list) or not requested:
        raise V12ServiceError("assignment outcome scope is required", code="invalid_argument", details={"field": "mission.outcomes"})
    selected: list[str] = []
    for candidate in requested:
        if isinstance(candidate, str):
            normalized = {"outcome": candidate}
        elif isinstance(candidate, Mapping):
            normalized = {
                "outcome": candidate.get("outcome"),
                "acceptance": list(candidate.get("acceptance", [])),
                "constraints": list(candidate.get("constraints", [])),
                "verification": list(candidate.get("verification", [])),
            }
        else:
            raise V12ServiceError("assignment outcome scope is invalid", code="invalid_argument", details={"field": "mission.outcomes"})
        matches = [item for item in current_items if _semantic_outcome(item) == normalized] if len(normalized) > 1 else []
        if not matches and isinstance(normalized["outcome"], str):
            # Semantic child fields are useful evidence but are routinely
            # paraphrased by an LLM between reads.  A unique outcome title is
            # sufficient identity; only zero or multiple matches are unsafe.
            matches = [
                item for item in current_items
                if _semantic_outcome(item).get("outcome") == normalized["outcome"]
            ]
        if len(matches) != 1 or not isinstance(matches[0].get("item_ref"), str):
            raise V12ServiceError("assignment outcome is missing or ambiguous", code="outcome_item_not_found", details={"field": "mission.outcomes"})
        selected.append(str(matches[0]["item_ref"]))
    if len(set(selected)) != len(selected):
        raise V12ServiceError("assignment outcome scope is ambiguous", code="outcome_assignment_conflict", details={"field": "mission.outcomes"})
    return selected


def _select_report_inputs(store: V12Store, task_id: str, policy: object, item_refs: list[str]) -> list[str]:
    if policy == "none":
        return []
    def select(connection):
        if policy == "all_finalized":
            rows = connection.execute("SELECT report_id FROM reports WHERE task_id=? AND assembly_state='finalized' ORDER BY created_sequence", (task_id,)).fetchall()
        elif policy == "active_plan":
            rows = connection.execute(
                "SELECT r.report_id FROM reports r "
                "WHERE r.task_id=? AND r.report_type='plan' AND r.assembly_state='finalized' "
                "ORDER BY r.created_sequence DESC LIMIT 1", (task_id,),
            ).fetchall()
        elif policy == "latest_for_scope":
            # Coverage identity stays private. Choose the latest finalized
            # report touching any selected semantic outcome.
            internal = [store._outcome_item_id(connection, task_id, ref_value) for ref_value in item_refs]
            placeholders = ",".join("?" for _ in internal)
            rows = connection.execute(
                "SELECT DISTINCT r.report_id,r.created_sequence FROM reports r JOIN report_contract_coverage c ON c.report_id=r.report_id "
                f"WHERE r.task_id=? AND r.assembly_state='finalized' AND c.item_id IN ({placeholders}) ORDER BY r.created_sequence DESC LIMIT 1",
                (task_id, *internal),
            ).fetchall()
        else:
            raise V12StoreError("report policy is invalid", code="invalid_argument", details={"field": "mission.report_policy"})
        return [str(row["report_id"]) for row in rows]
    try:
        return store._read(select)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _admit_assignment(store: V12Store, task_id: str, assignment_policy: str) -> None:
    """Enforce the public admission invariant without choosing a schedule.

    Governance is deliberately advisory in the ledger, but an assignment is
    still not admissible until the coordinator has recorded its assessment.
    For light/full governance, owner (production) work additionally requires
    the latest finalized required-review plan and the latest decision for that
    exact plan to be an explicit approval.  Planning/review work remains the
    proportional path used to produce that evidence.
    """
    def read(connection):
        assessment = connection.execute(
            "SELECT mode FROM governance_assessments WHERE project_hash=? AND task_id=? "
            "ORDER BY CASE WHEN source='user_override' THEN 0 ELSE 1 END, "
            "created_sequence DESC LIMIT 1",
            (store.project_hash, task_id),
        ).fetchone()
        if assessment is None:
            raise V12ServiceError(
                "governance assessment is required before opening an assignment",
                code="governance_assessment_required",
            )
        mode = str(assessment["mode"])
        if assignment_policy != "owner" or mode not in {"light", "full"}:
            return
        plan = connection.execute(
            "SELECT report_id,content_digest,review_policy FROM reports "
            "WHERE project_hash=? AND task_id=? AND report_type='plan' "
            "AND assembly_state='finalized' ORDER BY created_sequence DESC LIMIT 1",
            (store.project_hash, task_id),
        ).fetchone()
        if plan is None or str(plan["review_policy"] or "") != "required":
            raise V12ServiceError(
                "a current finalized required-review plan is required before delivery",
                code="plan_approval_required",
            )
        decision = connection.execute(
            "SELECT decision_type,subject_id,subject_digest FROM user_decisions "
            "WHERE project_hash=? AND task_id=? AND subject_type='plan' "
            "ORDER BY created_sequence DESC LIMIT 1",
            (store.project_hash, task_id),
        ).fetchone()
        if (decision is None or str(decision["decision_type"]) != "approve"
                or str(decision["subject_id"]) != str(plan["report_id"])
                or str(decision["subject_digest"]) != str(plan["content_digest"])):
            raise V12ServiceError(
                "the current required-review plan has not been explicitly approved",
                code="plan_approval_required",
            )
    store._read(read)


def _worker_capability_provenance() -> dict[str, str]:
    """Return the running package identity used to bind worker bootstrap.

    Bootstrap capabilities are package-bound facts.  The public facade derives
    these values from the verified package and catalogue; callers can never
    select or override them.
    """
    from cortex_runtime.provenance import verify_runtime
    from cortex_runtime.public_contracts import build_public_contracts
    package_root = Path(__file__).resolve().parents[2]
    identity = verify_runtime(
        package_root,
        "1.13.2",
        allow_source_mode=os.environ.get("CORTEX_SOURCE_MODE") == "1",
    )
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
    """Private deterministic identity for the durable mutation layer."""
    encoded = json.dumps({"operation": operation, "payload": dict(payload)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "domain-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def open_task(*, project_root: str, request_original: str, user_language: str,
              outcomes: list[Mapping[str, object]], constraints: list[str],
              context: object | None = None) -> dict[str, Any]:
    """Create or exactly replay one task from its coherent public outcome contract."""
    user_request_original = request_original
    if not isinstance(outcomes, list):
        raise V12ServiceError("task outcomes are invalid", code="invalid_argument", details={"field": "task"})
    requirements: list[object] = []
    acceptance_criteria: list[object] = []
    outcome_contracts: list[dict[str, object]] = []
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            raise V12ServiceError("task outcomes are invalid", code="invalid_argument", details={"field": "task"})
        requirements.append(outcome.get("outcome"))
        acceptance = outcome.get("acceptance")
        if not isinstance(acceptance, list):
            raise V12ServiceError("task outcomes are invalid", code="invalid_argument", details={"field": "task"})
        acceptance_criteria.extend(acceptance)
        outcome_contracts.append({
            "requirement": outcome.get("outcome"),
            "acceptance": list(acceptance),
            "verification": list(outcome.get("verification", [])),
            "constraints": list(outcome.get("constraints", [])),
        })
    outcome_names = [item.get("requirement") for item in outcome_contracts]
    if len(set(outcome_names)) != len(outcome_names):
        raise V12ServiceError(
            "task outcome names must be unique",
            code="outcome_assignment_conflict",
            details={"field": "outcomes"},
        )
    # The exact original request is already the immutable task objective.
    # Requiring a second model-authored summary created a redundant first-call
    # failure mode and allowed the two values to drift.
    payload = {"project_root": project_root, "objective": user_request_original,
               "user_request_original": user_request_original, "user_language": user_language,
               "requirements": requirements, "constraints": constraints,
               "acceptance_criteria": acceptance_criteria,
               "outcome_contracts": outcome_contracts, "context": context}
    result = ledger.create_task(**payload)
    created = result.get("task") if isinstance(result, Mapping) else None
    canonical = created.get("task_id") if isinstance(created, Mapping) else None
    public_ref = compact_task_ref(str(canonical)) if isinstance(canonical, str) else None
    if public_ref is None:
        raise V12ServiceError("task reference is unavailable", code="ledger_error")
    return {"task_ref": public_ref, "replayed": bool(result.get("replayed"))}


def read_task(*, task_ref: str, view: str, continue_: bool = False,
              report_policy: str = "all_finalized",
              _connection_context: dict[str, Any] | None = None, **compat: Any) -> dict[str, Any]:
    """Read state, assignment bootstrap, or evidence with server-owned paging."""
    if "continue" in compat:
        continue_ = compat.pop("continue")
    if compat:
        raise V12ServiceError("task read shape is invalid", code="invalid_argument")
    context = _connection_context if isinstance(_connection_context, dict) else {}
    store, canonical, assignment_id, coordinator_ref = _resolve_task_context(task_ref)
    page_key = (task_ref, view, report_policy)
    if continue_:
        if context.get("read_key") != page_key or not context.get("has_more"):
            raise V12ServiceError("no bounded read is available to continue", code="report_cursor_invalid")
        cursor = context.get("cursor")
    else:
        cursor = None
        context.pop("cursor", None)
    if view == "state":
        raw = ledger.inspect_task(task_ref=coordinator_ref, after_sequence=0)
        data = _publicize(raw)
        result = {"task_ref": task_ref, "view": view, "data": data, "has_more": False}
    elif view == "assignment":
        if assignment_id is None:
            raise V12ServiceError("assignment view requires worker-scoped task_ref", code="capability_stale")
        raw = _read_assignment_page(store=store, assignment_id=assignment_id, cursor=cursor)
        context.update({"actor": "worker", "assignment_id": assignment_id,
                        "continuation_ref": raw.get("continuation_ref"), "task_id": canonical,
                        "worker_task_ref": task_ref})
        has_more = bool(raw.get("has_more"))
        context.update({"read_key": page_key, "cursor": raw.get("next_cursor"), "has_more": has_more})
        result = {"task_ref": task_ref, "view": view, "data": _publicize(raw), "has_more": has_more}
    elif view == "evidence":
        if assignment_id is not None:
            if context.get("assignment_id") != assignment_id:
                raise V12ServiceError("worker assignment must be read before evidence", code="capability_stale")
            assignment_page = _read_assignment_page(store=store, assignment_id=assignment_id, cursor=cursor)
            raw = assignment_page.get("evidence")
            if not isinstance(raw, Mapping):
                raise V12ServiceError("worker evidence is unavailable", code="ledger_error")
        else:
            report_ids = _select_report_inputs(store, canonical, report_policy, [])
            raw = store.read_reports(task_id=canonical, report_ids=report_ids, cursor=cursor, max_bytes=65_536, consumer_delegation_id=None) if report_ids else {"reports": [], "has_more": False, "next_cursor": None}
        has_more = bool(raw.get("has_more"))
        context.update({"read_key": page_key, "cursor": raw.get("next_cursor"), "has_more": has_more})
        result = {"task_ref": task_ref, "view": view, "data": _publicize(raw), "has_more": has_more}
    else:
        raise V12ServiceError("task view is invalid", code="invalid_argument", details={"field": "view"})
    return result


def open_assignment(*, task_ref: str, role: str, profile_name: str, model: str,
                    reasoning_effort: str,
                    responsibility: str, goal: str, scope: str,
                    instructions: str, outcomes: list[Mapping[str, object]],
                    report_policy: str) -> dict[str, Any]:
    """Atomically bind a worker assignment to its current effective contract."""
    from cortex_runtime.model_routing import validate_model_selection
    assignment_policy = {
        "delivery": "owner",
        "evidence": "review",
        "planning": "planning",
    }.get(responsibility)
    if assignment_policy is None:
        raise V12ServiceError("assignment responsibility is invalid", code="invalid_argument", details={"field": "mission.responsibility"})
    store, canonical = _task_store(task_ref)
    _admit_assignment(store, canonical, assignment_policy)
    current = ledger.inspect_task(task_ref=task_ref).get("effective_contract")
    current_items = current.get("items") if isinstance(current, Mapping) else None
    if not isinstance(current_items, list):
        raise V12ServiceError("task outcome scope is unavailable", code="ledger_error")
    typed_items = [item for item in current_items if isinstance(item, Mapping)]
    item_refs = _match_outcomes(typed_items, outcomes)
    outcome_assignments = {
        "owned": list(item_refs) if responsibility == "delivery" else [],
        "contributing": list(item_refs) if responsibility in {"evidence", "planning"} else [],
        "evidence_producing": list(item_refs) if responsibility == "evidence" else [],
    }
    try:
        selection = validate_model_selection(model, reasoning_effort)
    except ValueError as exc:
        raise V12ServiceError("model selection is invalid", code="invalid_model_selection") from exc
    input_report_ids = _select_report_inputs(store, canonical, report_policy, item_refs)
    input_report_refs = [record_ref(item) for item in input_report_ids]
    if any(item is None for item in input_report_refs):
        raise V12ServiceError("report policy resolution failed", code="ledger_error")
    input_decision_refs: list[str] = []
    payload = {"task_ref": task_ref, "objective": goal, "role": role,
               "profile_name": profile_name, "scope": scope, "instructions": instructions,
               "model": selection.model, "reasoning_effort": selection.reasoning_effort,
               "input_report_refs": input_report_refs, "input_decision_refs": input_decision_refs}
    provenance = _worker_capability_provenance()
    result = ledger.create_delegation(
        task_ref=task_ref, objective=payload["objective"], role=payload["role"], profile_name=payload["profile_name"],
        scope=payload["scope"], instructions=payload["instructions"], model=selection.model, reasoning_effort=selection.reasoning_effort,
        input_report_refs=list(input_report_refs), input_decision_refs=input_decision_refs,
        outcome_assignments=outcome_assignments,
        parent_delegation_ref=None,
        bootstrap_provenance=provenance,
        derive_assignment_scope=True,
        assignment_policy=assignment_policy,
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
        for key in ("model", "reasoning_effort"):
            if isinstance(native_args.get(key), str):
                native_dispatch[key] = native_args[key]
        if not all(isinstance(native_dispatch[key], str) and native_dispatch[key] for key in ("fork_turns", "message", "task_name")):
            raise V12ServiceError("worker dispatch is invalid", code="ledger_error")
        # Keep the server-issued correlation marker available to the MCP
        # observability boundary.  It is an internal observation aid, not a
        # callable handle, and the transport removes it before projecting the
        # public success envelope.  Without this bridge the open_assignment
        # event cannot be joined to the native-dispatch hook event.
        dispatch_marker = raw_brief.get("dispatch_correlation_marker")
        if not isinstance(dispatch_marker, str) or not dispatch_marker:
            raise V12ServiceError("worker dispatch correlation is invalid", code="ledger_error")
        # The coordinator owns only the opaque native dispatch. The private
        # one-time worker lease remains in the server ledger and is resolved by
        # the assignment locator when the worker consumes its evidence.
        return {
            "native_dispatch": native_dispatch,
            # Preserve the server's durable mutation outcome.  A coordinator
            # must be able to distinguish a first mint from reconciliation of
            # an already-committed dispatch after an interrupted host call;
            # otherwise it may incorrectly treat the same assignment as new.
            "replayed": bool(result.get("replayed")),
        }
    return result


def _read_assignment_page(*, store: V12Store, assignment_id: str,
                          cursor: str | None = None) -> dict[str, Any]:
    """Resolve and consume the exact server-bound worker assignment page."""
    try:
        assignment = store.read_delegation(delegation_id=assignment_id, after_sequence=0, limit=1)
        # ``read_delegation`` deliberately returns a projection envelope.  Do
        # not read task/dispatch identity from the envelope itself: doing so
        # made every freshly opened assignment fail with a misleading
        # ``ledger_error`` before the capability validator could run.
        delegation = assignment.get("delegation") if isinstance(assignment, Mapping) else None
        task_id = delegation.get("task_id") if isinstance(delegation, Mapping) else None
        dispatch_digest = delegation.get("dispatch_correlation_digest") if isinstance(delegation, Mapping) else None
        if not isinstance(task_id, str) or not isinstance(dispatch_digest, str):
            raise V12ServiceError("worker bootstrap capability is invalid", code="ledger_error")
        provenance = _worker_capability_provenance()
        continuation = store.consume_worker_bootstrap_for_assignment(
            task_id=task_id,
            # The store resolves the immutable assignment snapshot revision;
            # the revision is never supplied by the model.
            assignment_id=assignment_id, contract_revision=1,
            dispatch_digest=dispatch_digest, **provenance,
        )
        if not isinstance(continuation, Mapping):
            raise V12ServiceError("worker continuation is invalid", code="ledger_error")
        delegation_id = continuation.get("assignment_id")
        if not isinstance(delegation_id, str):
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
        if store.project_root is None:
            raise V12ServiceError("assignment project root is unavailable", code="ledger_error")
        assignment_context["project_root"] = str(store.project_root)
        assigned_items = effective_contract.get("assigned_items")
        assigned_roles = {
            item.get("assignment_role")
            for item in assigned_items
            if isinstance(item, Mapping)
        } if isinstance(assigned_items, list) else set()
        planning_items = effective_contract.get("planning_items")
        assignment_context["responsibility"] = (
            "planning"
            if isinstance(planning_items, list)
            else "delivery"
            if "owned" in assigned_roles
            else "evidence"
        )
        coverage_source = (
            "planning_items"
            if assignment_context["responsibility"] == "planning"
            else "assigned_items"
        )
        coverage_items = effective_contract.get(coverage_source)
        if not isinstance(coverage_items, list) or not coverage_items:
            raise V12ServiceError("assignment publication scope is unavailable", code="ledger_error")
        required_item_refs = [
            item.get("item_ref")
            for item in coverage_items
            if isinstance(item, Mapping) and isinstance(item.get("item_ref"), str)
        ]
        if len(required_item_refs) != len(coverage_items) or len(set(required_item_refs)) != len(required_item_refs):
            raise V12ServiceError("assignment publication scope is invalid", code="ledger_error")
        publication_reconciliation = {
            "coverage_source": coverage_source,
            "required_item_count": len(required_item_refs),
            "required_item_refs": required_item_refs,
            "contract_coverage_template": [
                {"item_ref": item_ref} for item_ref in required_item_refs
            ],
        }
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
            "publication_reconciliation": publication_reconciliation,
            "assignment_context": assignment_context,
            "predecessor_evidence": predecessor_evidence if isinstance(predecessor_evidence, list) else [],
            "decision_evidence": decision_evidence if isinstance(decision_evidence, list) else [],
        }
        # The task anchor is server-derived from the assignment and is the
        # callable context for the next worker operation on this connection.
        # Returning it lets the MCP transport bind the connection without
        # requiring the host to reconstruct or forward a task reference.
        bound_task_ref = compact_task_ref(task_id)
        if bound_task_ref is None:
            raise V12ServiceError("worker task context is invalid", code="ledger_error")
        report_ids = worker.get("input_report_ids") if isinstance(worker, Mapping) else None
        if not isinstance(report_ids, list) or not report_ids:
            return {"continuation_ref": continuation_ref, "task_ref": bound_task_ref, **authority, "evidence": {"state": "none", "reports": []}, "next_cursor": None, "has_more": False}
        result = store.read_reports(
            report_ids=report_ids, cursor=cursor, max_bytes=65_536,
            consumer_delegation_id=delegation_id,
        )
        return {"continuation_ref": continuation_ref, "task_ref": bound_task_ref, **authority, "evidence": {"state": "consumed", **result}}
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
    review_policy = report.get("review_policy")
    if review_policy not in {"informational", "required"}:
        raise V12ServiceError("plan publication relation is unavailable", code="ledger_error")
    compact_supersedes = record_ref(report.get("supersedes_report_id"))
    return {
        "report": {
            "report_ref": report_ref,
            "report_type": "plan",
            "status": str(report.get("status")),
            "semantic_status": str(report.get("semantic_status")),
            "review_policy": review_policy,
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
             status: str = "completed", review_policy: str | None = None) -> dict[str, Any]:
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
            publication_kind="synthesis" if kind == "documentation" else kind,
            content=evidence, status=status, review_policy=review_policy,
        )
        # ``publish_domain_report`` commits a plan's immutable rendered view
        # and approval relation in the same ledger transaction as its terminal
        # report operation.  Do not post-process it through the historical
        # best-effort projection path: that would create an observable
        # published-plan-without-ready-relation interval.
        return _public_plan_publication(published) if kind == "plan" else published
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _publish_from_task(*, task_ref: str, kind: str, evidence: Mapping[str, Any], status: str,
                       review_policy: str | None = None,
                       _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = _connection_context if isinstance(_connection_context, dict) else {}
    _store, _task_id, assignment_id, _coordinator_ref = _resolve_task_context(task_ref)
    if (assignment_id is None or context.get("actor") != "worker"
            or context.get("assignment_id") != assignment_id
            or context.get("worker_task_ref") != task_ref):
        raise V12ServiceError("publication requires this connection's consumed worker assignment", code="capability_stale")
    continuation_ref = context.get("continuation_ref")
    if not isinstance(continuation_ref, str):
        raise V12ServiceError("worker assignment evidence has not been consumed", code="capability_stale")
    store, _ = V12Store.for_record_ref(record_ref(assignment_id), label="delegation_id")
    internal_evidence = _internalize_publication_evidence(store, assignment_id, evidence)
    published = _publish(
        continuation_ref=continuation_ref, assignment_ref=record_ref(assignment_id),
        kind=kind, evidence=internal_evidence, status=status,
        review_policy=review_policy,
    )
    return {"task_ref": task_ref, "state": "published", "replayed": bool(published.get("replayed"))}


def _publication_evidence(*, schema_kind: str, summary: str,
                          verification_facts: list[Mapping[str, Any]],
                          outcome_coverage: list[Mapping[str, Any]], risks: list[str],
                          unresolved: list[str], **specific: Any) -> dict[str, Any]:
    schema = "synthesis" if schema_kind == "documentation" else schema_kind
    return {
        "schema": f"cortex/report/{schema}/v3",
        "summary": summary,
        "verification": [str(item.get("summary")) for item in verification_facts if isinstance(item, Mapping)],
        "risks": list(risks), "deviations": [], "unresolved": list(unresolved),
        "verification_facts": [dict(item) for item in verification_facts],
        "contract_coverage": [dict(item) for item in outcome_coverage],
        **specific,
    }


def publish_plan(*, task_ref: str, summary: str, scope: str, review_policy: str,
                 stages: list[Mapping[str, Any]], verification_facts: list[Mapping[str, Any]],
                 outcome_coverage: list[Mapping[str, Any]], risks: list[str], unresolved: list[str],
                 status: str, _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    evidence = _publication_evidence(schema_kind="plan", summary=summary, verification_facts=verification_facts,
                                     outcome_coverage=outcome_coverage, risks=risks, unresolved=unresolved,
                                     scope=scope, stages=[dict(item) for item in stages])
    return _publish_from_task(
        task_ref=task_ref, kind="plan", evidence=evidence, status=status,
        review_policy=review_policy, _connection_context=_connection_context,
    )


def publish_result(*, task_ref: str, summary: str, outcome: str,
                   changes: list[Mapping[str, Any]], verification_facts: list[Mapping[str, Any]],
                   outcome_coverage: list[Mapping[str, Any]], documentation_impact: str,
                   risks: list[str], unresolved: list[str], status: str,
                   _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    evidence = _publication_evidence(schema_kind="result", summary=summary, verification_facts=verification_facts,
                                     outcome_coverage=outcome_coverage, risks=risks, unresolved=unresolved,
                                     outcome=outcome, changes=[dict(item) for item in changes],
                                     documentation_impact=documentation_impact)
    return _publish_from_task(task_ref=task_ref, kind="result", evidence=evidence, status=status, _connection_context=_connection_context)


def publish_documentation(*, task_ref: str, summary: str,
                          findings: list[Mapping[str, Any]], recommendations: list[str],
                          verification_facts: list[Mapping[str, Any]], outcome_coverage: list[Mapping[str, Any]],
                          documentation_impact: str, risks: list[str], unresolved: list[str],
                          status: str, _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    evidence = _publication_evidence(schema_kind="documentation", summary=summary, verification_facts=verification_facts,
                                     outcome_coverage=outcome_coverage, risks=risks, unresolved=unresolved,
                                     findings=[dict(item) for item in findings], recommendations=list(recommendations),
                                     documentation_impact=documentation_impact)
    return _publish_from_task(task_ref=task_ref, kind="documentation", evidence=evidence, status=status, _connection_context=_connection_context)


def _pending_binding(store: V12Store, task_id: str, *, decision_type: str) -> str:
    rows = store._read(lambda connection: connection.execute(
        "SELECT clarification_binding FROM clarification_bindings "
        "WHERE task_id=? AND decision_type=? AND consumed_decision_id IS NULL ORDER BY issue_sequence",
        (task_id, decision_type),
    ).fetchall())
    if len(rows) != 1:
        raise V12ServiceError("exactly one matching user decision must be pending", code="clarification_binding_stale")
    return str(rows[0]["clarification_binding"])


def _decision_receipt(task_ref: str, state: str, issued: Mapping[str, Any]) -> dict[str, Any]:
    return {"task_ref": task_ref, "state": state, "replayed": bool(issued.get("replayed"))}


def open_clarification(*, task_ref: str, prompt: str, prompt_language: str) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        issued = DecisionAggregate(store).open_clarification(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type="task", subject_id=canonical, assignment_id=None,
        )
        return _decision_receipt(task_ref, "pending_clarification", issued)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def record_clarification(*, task_ref: str, response_original: str,
                         user_language: str) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        binding_ref = _pending_binding(store, canonical, decision_type="clarification")
        issued = DecisionAggregate(store).record_clarification(
            task_id=canonical, binding_ref=binding_ref,
            response_original=response_original, user_language=user_language,
            steering_delta=None,
        )
        return _decision_receipt(task_ref, "clarification_recorded", issued)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def open_plan_review(*, task_ref: str, prompt: str,
                     prompt_language: str) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        plans = store._read(lambda connection: connection.execute(
            "SELECT report_id FROM reports WHERE task_id=? AND report_type='plan' AND assembly_state='finalized' ORDER BY created_sequence DESC LIMIT 1",
            (canonical,),
        ).fetchall())
        if len(plans) != 1:
            raise V12ServiceError("an active finalized plan is required", code="approval_view_required")
        plan_id = str(plans[0]["report_id"])
        issued = DecisionAggregate(store).open_plan_review(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type="plan", subject_id=plan_id,
        )
        return _decision_receipt(task_ref, "pending_plan_review", issued)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def record_plan_review(*, task_ref: str, outcome: str,
                       response_original: str, user_language: str) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        binding_ref = _pending_binding(store, canonical, decision_type="plan_review")
        issued = DecisionAggregate(store).record_plan_review(
            task_id=canonical, binding_ref=binding_ref, outcome=outcome,
            response_original=response_original, user_language=user_language,
        )
        return _decision_receipt(task_ref, "plan_review_recorded", issued)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def open_steering(*, task_ref: str, prompt: str, prompt_language: str) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        issued = DecisionAggregate(store).open_steering(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type="task", subject_id=canonical, assignment_id=None,
        )
        return _decision_receipt(task_ref, "pending_steering", issued)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def record_steering(*, task_ref: str, response_original: str,
                    user_language: str, add: list[Mapping[str, Any]] | None = None,
                    retire: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        binding_ref = _pending_binding(store, canonical, decision_type="steer")
        current = ledger.inspect_task(task_ref=task_ref).get("effective_contract", {}).get("items", [])
        retire_refs = _match_outcomes([item for item in current if isinstance(item, Mapping)], retire or []) if retire else []
        additions: list[dict[str, Any]] = []
        paired_replacements = bool(add and retire_refs and len(add) == len(retire_refs))
        for index, item in enumerate(add or []):
            target = retire_refs[index] if paired_replacements else None
            fields = [
                ("requirement", item.get("outcome")),
                *(("acceptance", value) for value in item.get("acceptance", [])),
                *(("constraint", value) for value in item.get("constraints", [])),
                *(("verification", value) for value in item.get("verification", [])),
            ]
            additions.extend({"category": category, "text": text, **({"outcome_ref": target} if target else {})}
                             for category, text in fields if isinstance(text, str) and text.strip())
        steering_delta = {
            "add": additions,
            # An addition against an outcome atomically supersedes that row;
            # an unpaired retirement removes it without replacement.
            "retire_item_refs": [] if paired_replacements else retire_refs,
        }
        issued = DecisionAggregate(store).record_steering(
            task_id=canonical, binding_ref=binding_ref,
            response_original=response_original, user_language=user_language,
            steering_delta=steering_delta,
            supersedes_decision_id=None,
        )
        return _decision_receipt(task_ref, "steering_recorded", issued)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def assess_governance(*, task_ref: str, mode: str, rationale: str = "",
                      risk_factors: list[str] | None = None) -> dict[str, Any]:
    payload = {"task_ref": task_ref, "mode": mode, "rationale": rationale, "risk_factors": risk_factors}
    result = ledger.set_governance_mode(**payload, source="model")
    return {"task_ref": task_ref, "state": "governance_assessed", "replayed": bool(result.get("replayed"))}


def close_task(*, task_ref: str, verdict: str, evidence: object | None = None,
               unresolved_risks: object | None = None, follow_ups: object | None = None,
               completion_notes: object | None = None) -> dict[str, Any]:
    if evidence is None:
        inspected = ledger.inspect_task(task_ref=task_ref)
        evidence = {
            "source": "server_derived_task_state",
            "effective_contract": inspected.get("effective_contract"),
            "aggregate_coverage": inspected.get("aggregate_coverage"),
            "conformance_review": inspected.get("conformance_review"),
        }
    result = ledger.submit_governance_closure(
        task_ref=task_ref, subject_type="task", subject_ref=task_ref, verdict=verdict,
        evidence=evidence, unresolved_risks=unresolved_risks, follow_ups=follow_ups,
        completion_notes=completion_notes,
    )
    return {"task_ref": task_ref, "state": "closed", "replayed": bool(result.get("replayed"))}


__all__ = [
    "open_task", "read_task", "open_assignment",
    "publish_plan", "publish_result", "publish_documentation",
    "open_clarification", "record_clarification", "open_plan_review", "record_plan_review", "open_steering", "record_steering",
    "assess_governance", "close_task",
]
