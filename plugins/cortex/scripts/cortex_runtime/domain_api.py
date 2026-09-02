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
from cortex_runtime.v12_contract import (
    MCP_OPERATION_MAX_BYTES, REPORT_CHUNK_MAX_BYTES, REPORT_READ_MAX_BYTES,
    REPORT_RESPONSE_MAX_BYTES,
    record_ref, task_ref as compact_task_ref,
)
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


def worker_assignment_connection_code(value: object) -> str:
    """Classify a copied worker locator without consuming or mutating it."""
    if not isinstance(value, str):
        return "wrong_connection"
    try:
        store, task_id, assignment_id, _coordinator_ref = _resolve_task_context(value)
        if assignment_id is None:
            return "wrong_connection"
        state = store.worker_capability_state(
            task_id=task_id, assignment_id=assignment_id,
        )
    except (V12ServiceError, V12StoreError):
        return "wrong_connection"
    if state == "consumed":
        return "connection_lost"
    if state in {"stale", "conflict"}:
        return "assignment_stale"
    return "wrong_connection"


def _semantic_outcome(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "outcome": str(item.get("text", "")),
        "acceptance": list(item.get("acceptance_criteria", [])) if isinstance(item.get("acceptance_criteria"), list) else [],
        "constraints": list(item.get("constraints", [])) if isinstance(item.get("constraints"), list) else [],
        "verification": list(item.get("verification_criteria", [])) if isinstance(item.get("verification_criteria"), list) else [],
    }


def _validated_public_outcome(value: object, *, path: str) -> dict[str, Any]:
    """Validate one complete public semantic outcome with a safe path."""
    required = {"outcome", "acceptance", "constraints", "verification"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise V12ServiceError(
            "semantic outcome is incomplete",
            code="invalid_argument",
            details={
                "path": path,
                "expected": "complete_outcome_object",
                "reason": "canonical_semantic_invalid",
            },
        )
    outcome = value.get("outcome")
    if not isinstance(outcome, str) or not outcome.strip():
        raise V12ServiceError(
            "semantic outcome name is invalid", code="invalid_argument",
            details={
                "path": f"{path}.outcome", "expected": "current_semantic_outcome",
                "reason": "canonical_semantic_invalid",
            },
        )
    result: dict[str, Any] = {"outcome": outcome}
    for field in ("acceptance", "constraints", "verification"):
        entries = value.get(field)
        if (
            not isinstance(entries, list)
            or any(not isinstance(item, str) or not item.strip() for item in entries)
        ):
            raise V12ServiceError(
                "semantic outcome evidence is invalid", code="invalid_argument",
                details={
                    "path": f"{path}.{field}", "expected": "array",
                    "reason": "canonical_semantic_invalid",
                },
            )
        result[field] = list(entries)
    return result


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
    public_rows: list[Mapping[str, Any]] = []
    outcome_names: list[str] = []
    for row in coverage:
        if not isinstance(row, Mapping) or not isinstance(row.get("outcome"), str):
            raise V12ServiceError("publication coverage requires a semantic outcome name", code="invalid_argument", details={"field": "evidence.contract_coverage"})
        public_rows.append(row)
        outcome_names.append(str(row["outcome"]))
    # Resolve the complete ordered coverage in one call. Resolving each row as
    # a one-element list reset every diagnostic to index zero, so a mismatch in
    # the seventh planner outcome misleadingly reported
    # ``$.outcome_coverage[0]``. The single ordered match also applies duplicate
    # and overlap validation to the publication as a whole.
    matches = _match_outcomes(
        typed_items, outcome_names, path="$.outcome_coverage",
    )
    internal = [
        {
            "item_ref": item_ref,
            **{key: value for key, value in row.items() if key != "outcome"},
        }
        for row, item_ref in zip(public_rows, matches, strict=True)
    ]
    result["contract_coverage"] = internal
    return result


def _match_outcomes(
    current_items: list[Mapping[str, Any]], requested: object, *,
    path: str = "$.outcomes",
) -> list[str]:
    if not isinstance(requested, list) or not requested:
        raise V12ServiceError(
            "assignment outcome scope is required", code="invalid_argument",
            details={"path": path, "expected": "bounded_length", "reason": "length"},
        )
    selected: list[str] = []
    for index, candidate in enumerate(requested):
        if isinstance(candidate, str):
            normalized = {"outcome": candidate}
        elif isinstance(candidate, Mapping):
            normalized = _validated_public_outcome(candidate, path=f"{path}[{index}]")
        else:
            raise V12ServiceError(
                "assignment outcome scope is invalid", code="invalid_argument",
                details={
                    "path": f"{path}[{index}]", "expected": "current_semantic_outcome",
                    "reason": "canonical_semantic_invalid",
                },
            )
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
            raise V12ServiceError(
                "assignment outcome is missing or ambiguous",
                code="outcome_item_not_found",
                details={
                    "path": f"{path}[{index}]",
                    "expected": "unique_current_semantic_outcome",
                    "reason": "semantic_outcome_missing" if not matches else "semantic_outcome_ambiguous",
                },
            )
        selected.append(str(matches[0]["item_ref"]))
    if len(set(selected)) != len(selected):
        raise V12ServiceError(
            "assignment outcome scope is ambiguous",
            code="outcome_assignment_conflict",
            details={
                "path": path, "expected": "non_overlapping_outcome_scope",
                "reason": "scope_overlap",
            },
        )
    return selected


def _select_report_inputs(store: V12Store, task_id: str, policy: object, item_refs: list[str]) -> list[str]:
    if policy == "none":
        return []
    def select(connection):
        if policy == "all_finalized":
            rows = connection.execute("SELECT report_id FROM reports WHERE task_id=? AND assembly_state='finalized' ORDER BY created_sequence", (task_id,)).fetchall()
        elif policy == "active_plan":
            rows = connection.execute(
                "SELECT DISTINCT r.report_id FROM reports r "
                "JOIN assignment_scope_snapshots s ON s.assignment_id=r.delegation_id "
                "WHERE r.task_id=? AND r.report_type='plan' AND r.assembly_state='finalized' "
                "AND s.assignment_role='planning' "
                "AND s.contract_revision=(SELECT MAX(revision) FROM effective_contract_revisions WHERE task_id=?) "
                "ORDER BY r.created_sequence DESC LIMIT 1", (task_id, task_id),
            ).fetchall()
        elif policy == "latest_for_scope":
            # Coverage identity stays private. Choose the latest finalized
            # report touching any selected semantic outcome.
            if not item_refs:
                raise V12StoreError(
                    "latest-for-scope evidence requires an assignment outcome scope",
                    code="invalid_argument",
                    details={"field": "report_policy"},
                )
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
            "SELECT DISTINCT r.report_id,r.content_digest,r.review_policy FROM reports r "
            "JOIN assignment_scope_snapshots s ON s.assignment_id=r.delegation_id "
            "WHERE r.project_hash=? AND r.task_id=? AND r.report_type='plan' "
            "AND s.assignment_role='planning' "
            "AND s.contract_revision=(SELECT MAX(revision) FROM effective_contract_revisions WHERE task_id=r.task_id) "
            "AND r.assembly_state='finalized' ORDER BY r.created_sequence DESC LIMIT 1",
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
        "1.14.12",
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
    bootstrap_pending = (
        context.get("bootstrap_assignment_id") is not None
        and context.get("assignment_complete") is not True
    )
    if bootstrap_pending and view != "assignment":
        raise V12ServiceError(
            "the current assignment must be consumed through its terminal page",
            code="report_cursor_invalid",
        )
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
        context.update({
            "read_key": page_key,
            "cursor": None,
            "has_more": False,
            # Private, same-connection admission evidence.  A successful
            # steering opening clears this marker, so recording the user's
            # later answer requires a state read performed after that opening.
            # This remains effective when Codex invokes Cortex through an
            # outer programmatic-tool call that host hooks cannot decompose.
            "steering_state_read_task_ref": task_ref,
        })
        result = {"task_ref": task_ref, "view": view, "data": data, "has_more": False}
    elif view == "assignment":
        if assignment_id is None:
            raise V12ServiceError("assignment view requires worker-scoped task_ref", code="wrong_connection")
        if context.get("_role") == "coordinator":
            raise V12ServiceError(
                "coordinator connection cannot consume worker authority",
                code="wrong_connection",
            )
        terminal_reconciliation = (
            context.get("assignment_complete") is True
            and context.get("_role") == "worker"
            and context.get("actor") == "worker"
            and context.get("bootstrap_assignment_id") == assignment_id
            and context.get("assignment_id") == assignment_id
            and context.get("task_id") == canonical
            and context.get("worker_task_ref") == task_ref
            and isinstance(context.get("continuation_ref"), str)
        )
        if (
            (context.get("assignment_complete") is True and not terminal_reconciliation)
            or (
                context.get("bootstrap_assignment_id") is not None
                and context.get("bootstrap_assignment_id") != assignment_id
            )
        ):
            raise V12ServiceError(
                "worker assignment context is stale",
                code="assignment_stale",
            )
        bound_continuation = (
            context.get("continuation_ref")
            if context.get("bootstrap_assignment_id") == assignment_id
            else None
        )
        raw = _read_assignment_page(
            store=store, assignment_id=assignment_id, cursor=cursor,
            continuation_ref=bound_continuation
            if isinstance(bound_continuation, str) else None,
        )
        if terminal_reconciliation:
            # Context compaction can remove the model-visible exact assignment
            # projection while the authenticated MCP connection and its
            # publication authority remain live.  Re-reading that same
            # terminal assignment on the same bound connection is a read-only
            # reconciliation: it neither adopts a new worker identity nor
            # mints a new continuation.  Clear only the assembled page state
            # after the exact restart succeeds so a paginated reconciliation
            # must again reach its terminal page before publication.
            context.pop("assignment_evidence", None)
            context.pop("assignment_evidence_pages", None)
            context["assignment_complete"] = False
        has_more = bool(raw.get("has_more"))
        context.update({
            "bootstrap_assignment_id": assignment_id,
            "continuation_ref": raw.get("continuation_ref"),
            "task_id": canonical,
            "worker_task_ref": task_ref,
            "read_key": page_key,
            "cursor": raw.get("next_cursor"),
            "has_more": has_more,
        })
        evidence = raw.get("evidence")
        if isinstance(evidence, Mapping):
            context["assignment_evidence"] = evidence
        page = raw.get("assignment_page")
        if isinstance(page, Mapping) and page.get("phase") == "evidence":
            page_evidence = page.get("evidence")
            if isinstance(page_evidence, Mapping):
                context.setdefault("assignment_evidence_pages", []).append(
                    dict(page_evidence)
                )
        if not has_more:
            context.update({
                "actor": "worker",
                "assignment_id": assignment_id,
                "assignment_complete": True,
                "_role": "worker",
            })
            if "assignment_evidence" not in context:
                pages = context.get("assignment_evidence_pages")
                context["assignment_evidence"] = {
                    "state": "consumed",
                    "pages": list(pages) if isinstance(pages, list) else [],
                }
        result = {"task_ref": task_ref, "view": view, "data": _publicize(raw), "has_more": has_more}
    elif view == "evidence":
        if assignment_id is not None:
            if context.get("assignment_id") != assignment_id:
                raise V12ServiceError("worker assignment must be read before evidence", code="assignment_not_consumed")
            if context.get("has_more"):
                raise V12ServiceError(
                    "the current assignment page must be continued before another view",
                    code="report_cursor_invalid",
                )
            raw = context.get("assignment_evidence")
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
                    instructions: str, outcomes: list[str] | None = None,
                    report_policy: str = "none",
                    loss_recovery: Mapping[str, Any] | None = None) -> dict[str, Any]:
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
    task_state = ledger.inspect_task(task_ref=task_ref)
    current = task_state.get("effective_contract")
    current_items = current.get("items") if isinstance(current, Mapping) else None
    if not isinstance(current_items, list):
        raise V12ServiceError("task outcome scope is unavailable", code="ledger_error")
    typed_items = [item for item in current_items if isinstance(item, Mapping)]
    if responsibility == "planning":
        if outcomes is not None:
            raise V12ServiceError(
                "planning outcome scope is server-derived",
                code="invalid_argument",
                details={"path": "$.outcomes", "reason": "unsupported_property"},
            )
        item_refs = [
            str(item["item_ref"]) for item in typed_items
            if isinstance(item.get("item_ref"), str)
        ]
        if len(item_refs) != len(typed_items) or not item_refs:
            raise V12ServiceError("task outcome scope is unavailable", code="ledger_error")
    elif outcomes is None:
        if loss_recovery is not None:
            raise V12ServiceError(
                "loss recovery requires an exact predecessor outcome scope",
                code="assignment_loss_scope_conflict",
                details={"path": "$.outcomes", "expected": "exact_loss_recovery_scope", "reason": "required"},
            )
        aggregate = task_state.get("aggregate_coverage")
        assignment_scope = aggregate.get("assignment_scope") if isinstance(aggregate, Mapping) else None
        scope_key = "delivery_outcomes" if responsibility == "delivery" else "evidence_outcomes"
        advertised = assignment_scope.get(scope_key) if isinstance(assignment_scope, Mapping) else None
        if not isinstance(advertised, list) or not advertised:
            raise V12ServiceError(
                "no current outcomes are assignable for this responsibility",
                code="outcome_assignment_conflict",
                details={"path": "$.outcomes", "expected": f"non_empty_{scope_key}", "reason": "no_assignable_scope"},
            )
        item_refs = _match_outcomes(typed_items, advertised, path="$.outcomes")
    elif isinstance(outcomes, list):
        item_refs = _match_outcomes(typed_items, outcomes, path="$.outcomes")
    else:
        raise V12ServiceError(
            "assignment outcome scope is invalid",
            code="invalid_argument",
            details={"path": "$.outcomes", "expected": "array", "reason": "type"},
        )
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
    parent_delegation_ref = None
    if loss_recovery is not None:
        if responsibility != "delivery":
            raise V12ServiceError(
                "only a delivery owner can replace a lost assignment",
                code="assignment_loss_scope_conflict",
            )
        try:
            parent_id = store.active_owner_for_outcomes(
                task_id=canonical,
                outcome_items=item_refs,
            )
        except V12StoreError as exc:
            raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
        parent_delegation_ref = record_ref(parent_id)
        if parent_delegation_ref is None:
            raise V12ServiceError(
                "lost assignment lineage could not be resolved",
                code="ledger_error",
            )
    payload = {"task_ref": task_ref, "objective": goal, "role": role,
               "profile_name": profile_name, "scope": scope, "instructions": instructions,
               "model": selection.model, "reasoning_effort": selection.reasoning_effort,
               "input_report_refs": input_report_refs, "input_decision_refs": input_decision_refs}
    provenance = _worker_capability_provenance()
    try:
        result = ledger.create_delegation(
            task_ref=task_ref, objective=payload["objective"], role=payload["role"], profile_name=payload["profile_name"],
            scope=payload["scope"], instructions=payload["instructions"], model=selection.model, reasoning_effort=selection.reasoning_effort,
            input_report_refs=list(input_report_refs), input_decision_refs=input_decision_refs,
            outcome_assignments=outcome_assignments,
            parent_delegation_ref=parent_delegation_ref,
            bootstrap_provenance=provenance,
            derive_assignment_scope=True,
            assignment_policy=assignment_policy,
            loss_recovery=dict(loss_recovery) if loss_recovery is not None else None,
        )
    except V12ServiceError as exc:
        private_fields = {"input_report_refs", "input_decision_refs", "parent_assignment_ref"}
        field = exc.details.get("field") if isinstance(exc.details, Mapping) else None
        if field in private_fields:
            raise V12ServiceError(
                "server-owned assignment evidence could not be resolved",
                code="ledger_error",
            ) from None
        raise
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
            "task_name": native_args.get("task_name"),
        }
        for key in ("model", "reasoning_effort"):
            if isinstance(native_args.get(key), str):
                native_dispatch[key] = native_args[key]
        # Keep the long host-protected message last.  This preserves every
        # explicit routing discriminator at the front of a compacted tool
        # result so the first native call can remain complete and exact.
        native_dispatch["message"] = native_args.get("message")
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


_ASSIGNMENT_PAGE_MAX_BYTES = 144 * 1024
_ASSIGNMENT_STRING_FRAGMENT_BYTES = 32 * 1024


def _encoded_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _assignment_fragments(value: object) -> list[dict[str, Any]]:
    """Flatten one public assignment authority into ordered path fragments."""
    fragments: list[dict[str, Any]] = []

    def append(path: list[object], item: object) -> None:
        candidate = {"path": list(path), "value": item}
        if len(_encoded_bytes(candidate)) <= _ASSIGNMENT_STRING_FRAGMENT_BYTES:
            fragments.append(candidate)
            return
        if not isinstance(item, str):
            rendered_path = "$" + "".join(
                f"[{part}]" if isinstance(part, int) else f".{part}"
                for part in path
            )
            actual = len(_encoded_bytes(candidate))
            raise V12ServiceError(
                "an indivisible assignment section exceeds the bounded page",
                code="content_too_large",
                details={
                    "path": rendered_path,
                    "actual_bytes": actual,
                    "max_bytes": _ASSIGNMENT_STRING_FRAGMENT_BYTES,
                    "reason": "indivisible_section",
                },
            )
        encoded = item.encode("utf-8")
        parts: list[str] = []
        at = 0
        while at < len(encoded):
            end = min(at + _ASSIGNMENT_STRING_FRAGMENT_BYTES, len(encoded))
            while end > at:
                try:
                    part = encoded[at:end].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    end -= 1
            if end == at:
                raise V12ServiceError(
                    "assignment text cannot be represented safely",
                    code="content_too_large",
                    details={
                        "path": "$" + "".join(
                            f"[{part}]" if isinstance(part, int) else f".{part}"
                            for part in path
                        ),
                        "actual_bytes": len(encoded),
                        "max_bytes": _ASSIGNMENT_STRING_FRAGMENT_BYTES,
                        "reason": "indivisible_section",
                    },
                )
            parts.append(part)
            at = end
        for index, part in enumerate(parts):
            state = (
                "complete" if len(parts) == 1
                else "starts" if index == 0
                else "ends" if index == len(parts) - 1
                else "continues"
            )
            fragments.append({"path": list(path), "text": part, "string_state": state})

    def walk(item: object, path: list[object]) -> None:
        if isinstance(item, Mapping):
            if not item:
                append(path, {})
                return
            for key, child in item.items():
                walk(child, [*path, str(key)])
            return
        if isinstance(item, list):
            if not item:
                append(path, [])
                return
            for index, child in enumerate(item):
                walk(child, [*path, index])
            return
        append(path, item)

    walk(value, [])
    return fragments


def _pack_assignment_fragments(fragments: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    pages: list[list[dict[str, Any]]] = []
    page: list[dict[str, Any]] = []
    for fragment in fragments:
        trial = {
            "assignment_page": {
                "phase": "authority", "fragments": [*page, fragment],
                "terminal": False,
            }
        }
        if len(_encoded_bytes(trial)) <= _ASSIGNMENT_PAGE_MAX_BYTES:
            page.append(fragment)
            continue
        if not page:
            rendered_path = fragment.get("path")
            raise V12ServiceError(
                "an indivisible assignment section exceeds the bounded page",
                code="content_too_large",
                details={
                    "path": repr(rendered_path),
                    "actual_bytes": len(_encoded_bytes(trial)),
                    "max_bytes": _ASSIGNMENT_PAGE_MAX_BYTES,
                    "reason": "indivisible_section",
                },
            )
        pages.append(page)
        page = [fragment]
    if page:
        pages.append(page)
    return pages or [[]]


def _read_assignment_page(*, store: V12Store, assignment_id: str,
                          cursor: Mapping[str, Any] | None = None,
                          continuation_ref: str | None = None) -> dict[str, Any]:
    """Resolve and consume the exact server-bound worker assignment page."""
    try:
        from cortex_runtime.worker_message import assignment_worker_policy

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
        if continuation_ref is None:
            continuation = store.consume_worker_bootstrap_for_assignment(
                task_id=task_id,
                # The store resolves the immutable assignment snapshot
                # revision; the revision is never supplied by the model.
                assignment_id=assignment_id, contract_revision=1,
                dispatch_digest=dispatch_digest, **provenance,
            )
            # A consumed durable row is not bearer authority for a new MCP
            # process. Only the connection which holds the exact continuation
            # returned by the first successful consumption may advance or
            # reconcile its pages.
            if continuation.get("replayed") is True:
                raise V12ServiceError(
                    "worker assignment belongs to another connection",
                    code="connection_lost",
                )
        else:
            continuation = store.resolve_worker_continuation(
                continuation=continuation_ref,
            )
            if (
                continuation.get("task_id") != task_id
                or continuation.get("assignment_id") != assignment_id
            ):
                raise V12ServiceError(
                    "worker continuation is invalid", code="assignment_stale",
                )
        if not isinstance(continuation, Mapping):
            raise V12ServiceError("worker continuation is invalid", code="ledger_error")
        delegation_id = continuation.get("assignment_id")
        if not isinstance(delegation_id, str):
            raise V12ServiceError("worker continuation is invalid", code="ledger_error")
        continuation_ref = continuation.get("continuation")
        if not isinstance(continuation_ref, str):
            raise V12ServiceError("worker continuation is invalid", code="ledger_error")
        continuation = store.resolve_worker_continuation(
            continuation=continuation_ref,
        )
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
        policy = assignment_worker_policy(assignment_context.get("profile_name"))
        if policy is None:
            raise V12ServiceError("assignment worker policy is unavailable", code="profile_unavailable")
        assignment_context["common_policy"] = policy["common_policy"]
        assignment_context["profile_instructions"] = policy["profile_instructions"]
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
            # Keep the exact public outcome selectors in a compact block that
            # is rendered before the potentially large contract/policy body.
            # Workers can retain terminal-publication authority even when a
            # host abbreviates later diagnostic presentation. Private item
            # references remain server-owned and never cross the projection.
            "required_outcomes": [
                str(item.get("text"))
                for item in coverage_items
                if isinstance(item, Mapping) and isinstance(item.get("text"), str)
            ],
            "contract_coverage_template": [
                {"outcome": str(item.get("text"))}
                for item in coverage_items
                if isinstance(item, Mapping) and isinstance(item.get("text"), str)
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
            "publication_reconciliation": publication_reconciliation,
            "assignment_context": assignment_context,
            "effective_contract": dict(effective_contract),
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
        report_ids = report_ids if isinstance(report_ids, list) else []
        snapshot_digest = "sha256:" + hashlib.sha256(_encoded_bytes({
            "task_id": task_id,
            "assignment_id": assignment_id,
            "continuation_revision": continuation.get("contract_revision"),
            "authority": authority,
            "report_ids": report_ids,
            "report_refs": worker.get("input_report_refs") if isinstance(worker, Mapping) else [],
        })).hexdigest()

        def finish_page(
            page: dict[str, Any], *, phase: str, position: int,
            next_cursor: Mapping[str, Any] | None, has_more: bool,
        ) -> dict[str, Any]:
            rendered = {**page, "next_cursor": next_cursor, "has_more": has_more}
            public_page = _publicize(rendered)
            encoded = _encoded_bytes(public_page)
            if len(encoded) > REPORT_RESPONSE_MAX_BYTES:
                raise V12ServiceError(
                    "assignment page exceeds the bounded response",
                    code="content_too_large",
                    details={
                        "path": "$.assignment_page",
                        "actual_bytes": len(encoded),
                        "max_bytes": REPORT_RESPONSE_MAX_BYTES,
                        "reason": "indivisible_section",
                    },
                )
            store.record_assignment_page_receipt(
                task_id=task_id,
                assignment_id=assignment_id,
                snapshot_digest=snapshot_digest,
                phase=phase,
                private_position=position,
                page_digest="sha256:" + hashlib.sha256(encoded).hexdigest(),
                returned_content_bytes=len(encoded),
                has_more=has_more,
            )
            return rendered

        position = 0
        mode = "start"
        report_cursor = None
        if cursor is not None:
            mode = cursor.get("mode")
            position = cursor.get("position")
            report_cursor = cursor.get("report_cursor")
            if (
                mode not in {"complete", "authority", "evidence"}
                or not isinstance(position, int)
                or isinstance(position, bool)
                or position < 1
                or (report_cursor is not None and not isinstance(report_cursor, str))
            ):
                raise V12ServiceError(
                    "assignment continuation is invalid",
                    code="report_cursor_invalid",
                )

        complete_without_evidence = {
            "continuation_ref": continuation_ref,
            "task_ref": bound_task_ref,
            **authority,
            "evidence": {"state": "none", "reports": []},
        }
        authority_bytes = len(_encoded_bytes(_publicize(complete_without_evidence)))
        use_fragment_pages = authority_bytes > _ASSIGNMENT_PAGE_MAX_BYTES
        if not use_fragment_pages and report_ids:
            response_budget = REPORT_RESPONSE_MAX_BYTES - authority_bytes - 4_096
            use_fragment_pages = response_budget < REPORT_CHUNK_MAX_BYTES + 4_096

        if not use_fragment_pages:
            if mode not in {"start", "complete"}:
                raise V12ServiceError(
                    "assignment continuation does not match the current page",
                    code="report_cursor_invalid",
                )
            if not report_ids:
                if mode != "start":
                    raise V12ServiceError(
                        "assignment read is already terminal",
                        code="report_cursor_invalid",
                    )
                return finish_page(
                    complete_without_evidence,
                    phase="complete", position=0,
                    next_cursor=None, has_more=False,
                )
            response_budget = REPORT_RESPONSE_MAX_BYTES - authority_bytes - 4_096
            result = store.read_reports(
                report_ids=report_ids,
                cursor=report_cursor,
                max_bytes=min(REPORT_READ_MAX_BYTES, response_budget - 4_096),
                response_max_bytes=response_budget,
                consumer_delegation_id=delegation_id,
            )
            has_more = bool(result.get("has_more"))
            next_value = (
                {
                    "mode": "complete", "position": position + 1,
                    "report_cursor": result.get("next_cursor"),
                }
                if has_more else None
            )
            return finish_page(
                {
                    "continuation_ref": continuation_ref,
                    "task_ref": bound_task_ref,
                    **authority,
                    "evidence": {"state": "consumed", **result},
                },
                phase="complete", position=position,
                next_cursor=next_value, has_more=has_more,
            )

        public_authority = _publicize(authority)
        pages = _pack_assignment_fragments(_assignment_fragments(public_authority))
        if mode in {"start", "authority"}:
            authority_index = 0 if mode == "start" else cursor.get("authority_index")
            if (
                not isinstance(authority_index, int)
                or isinstance(authority_index, bool)
                or not 0 <= authority_index < len(pages)
            ):
                raise V12ServiceError(
                    "assignment continuation is invalid",
                    code="report_cursor_invalid",
                )
            final_authority = authority_index == len(pages) - 1
            has_more = not final_authority or bool(report_ids)
            if not final_authority:
                next_value: Mapping[str, Any] | None = {
                    "mode": "authority", "position": position + 1,
                    "authority_index": authority_index + 1,
                }
            elif report_ids:
                next_value = {
                    "mode": "evidence", "position": position + 1,
                    "report_cursor": None,
                }
            else:
                next_value = None
            return finish_page(
                {
                    "continuation_ref": continuation_ref,
                    "task_ref": bound_task_ref,
                    "assignment_page": {
                        "phase": "authority",
                        "fragments": pages[authority_index],
                        "terminal": not has_more,
                    },
                },
                phase="authority", position=position,
                next_cursor=next_value, has_more=has_more,
            )

        if mode != "evidence" or not report_ids:
            raise V12ServiceError(
                "assignment continuation does not match the current page",
                code="report_cursor_invalid",
            )
        result = store.read_reports(
            report_ids=report_ids,
            cursor=report_cursor,
            max_bytes=REPORT_READ_MAX_BYTES,
            response_max_bytes=REPORT_RESPONSE_MAX_BYTES - 16_384,
            consumer_delegation_id=delegation_id,
        )
        has_more = bool(result.get("has_more"))
        next_value = (
            {
                "mode": "evidence", "position": position + 1,
                "report_cursor": result.get("next_cursor"),
            }
            if has_more else None
        )
        return finish_page(
            {
                "continuation_ref": continuation_ref,
                "task_ref": bound_task_ref,
                "assignment_page": {
                    "phase": "evidence",
                    "evidence": _publicize({"state": "consumed", **result}),
                    "terminal": not has_more,
                },
            },
            phase="evidence", position=position,
            next_cursor=next_value, has_more=has_more,
        )
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
            raise V12ServiceError("worker continuation is invalid", code="assignment_stale")
        continuation = store.resolve_worker_continuation(continuation=continuation_ref)
        if continuation.get("task_id") != task_id or continuation.get("assignment_id") != delegation_id:
            raise V12ServiceError("worker continuation is invalid", code="assignment_stale")
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


_WORKER_PUBLICATION_BINDING_KEYS = frozenset({
    "actor", "task_id", "assignment_id", "worker_task_ref",
    "continuation_ref", "assignment_complete",
})


def _worker_publication_binding(
    *, task_ref: str, context: dict[str, Any],
) -> tuple[V12Store, str, str]:
    """Resolve only the exact terminal same-connection worker binding."""
    store, task_id, assignment_id, _coordinator_ref = _resolve_task_context(task_ref)
    if assignment_id is None:
        raise V12ServiceError(
            "publication requires a consumed worker assignment",
            code="wrong_connection",
        )
    present = _WORKER_PUBLICATION_BINDING_KEYS.intersection(context)
    continuation_ref = context.get("continuation_ref")
    if (
        present != _WORKER_PUBLICATION_BINDING_KEYS
        or context.get("_role") != "worker"
        or context.get("actor") != "worker"
        or context.get("task_id") != task_id
        or context.get("assignment_id") != assignment_id
        or context.get("worker_task_ref") != task_ref
        or context.get("assignment_complete") is not True
        or context.get("has_more") is not False
        or not isinstance(continuation_ref, str)
    ):
        raise V12ServiceError(
            "publication requires the exact consumed worker assignment",
            code=("assignment_not_consumed" if context.get("_role") != "worker" else "wrong_connection"),
        )
    return store, assignment_id, continuation_ref


def _publish_from_task(*, task_ref: str, kind: str, evidence: Mapping[str, Any], status: str,
                       review_policy: str | None = None,
                       _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = _connection_context if isinstance(_connection_context, dict) else {}
    store, assignment_id, continuation_ref = _worker_publication_binding(
        task_ref=task_ref, context=context,
    )
    if kind == "plan":
        # Review disposition is task-state, not a planner-authored semantic
        # choice.  Derive it from the latest effective governance assessment
        # so light/full plans cannot be accidentally published as
        # informational and minimal plans cannot fabricate an approval gate.
        mode = store._read(lambda connection: connection.execute(
            "SELECT g.mode FROM delegations d JOIN governance_assessments g "
            "ON g.task_id=d.task_id AND g.project_hash=d.project_hash "
            "WHERE d.delegation_id=? AND d.project_hash=? "
            "ORDER BY CASE WHEN g.source='user_override' THEN 0 ELSE 1 END, "
            "g.created_sequence DESC LIMIT 1",
            (assignment_id, store.project_hash),
        ).fetchone())
        if mode is None:
            raise V12ServiceError(
                "governance assessment is required before plan publication",
                code="governance_assessment_required",
            )
        review_policy = "required" if str(mode["mode"]) in {"light", "full"} else "informational"
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


def publish_plan(*, task_ref: str, summary: str, scope: str,
                 stages: list[Mapping[str, Any]], verification_facts: list[Mapping[str, Any]],
                 outcome_coverage: list[Mapping[str, Any]], risks: list[str], unresolved: list[str],
                 status: str, _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    evidence = _publication_evidence(schema_kind="plan", summary=summary, verification_facts=verification_facts,
                                     outcome_coverage=outcome_coverage, risks=risks, unresolved=unresolved,
                                     scope=scope, stages=[dict(item) for item in stages])
    return _publish_from_task(
        task_ref=task_ref, kind="plan", evidence=evidence, status=status,
        _connection_context=_connection_context,
    )


def publish_result(*, task_ref: str, summary: str, outcome: str,
                   changes: list[Mapping[str, Any]], verification_facts: list[Mapping[str, Any]],
                   outcome_coverage: list[Mapping[str, Any]], documentation_impact: str,
                   risks: list[str], unresolved: list[str], status: str,
                   _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    public_arguments = {
        "task_ref": task_ref, "summary": summary, "outcome": outcome,
        "changes": changes, "verification_facts": verification_facts,
        "outcome_coverage": outcome_coverage,
        "documentation_impact": documentation_impact,
        "risks": risks, "unresolved": unresolved, "status": status,
    }
    try:
        actual_bytes = len(json.dumps(
            public_arguments, ensure_ascii=False, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise V12ServiceError(
            "result publication is not finite JSON", code="content_invalid",
            details={"path": "$", "expected": "bounded_json_value"},
        ) from exc
    if actual_bytes > MCP_OPERATION_MAX_BYTES:
        raise V12ServiceError(
            "result publication exceeds the aggregate encoded size",
            code="validation_error",
            details={
                "path": "$", "expected": "bounded_json_value",
                "reason": "encoded_size", "actual_bytes": actual_bytes,
                "max_bytes": MCP_OPERATION_MAX_BYTES,
            },
        )
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


def _decision_binding(
    store: V12Store, task_id: str, *, decision_type: str | None = None,
) -> tuple[str, str, int, bool]:
    """Resolve one pending decision, or the latest consumed replay candidate.

    A compacted host can lose the successful tool result and repeat the exact
    record call.  The command receipt owns that ambiguity, so the public
    adapter must not discard its binding merely because it is already
    consumed.  Different input still conflicts with the receipt and is mapped
    back to the public stale-decision error by the record adapters.
    """
    predicate = " AND decision_type=?" if decision_type is not None else ""
    parameters: tuple[object, ...] = (task_id, decision_type) if decision_type is not None else (task_id,)
    rows = store._read(lambda connection: connection.execute(
        "SELECT clarification_binding,decision_type,effective_contract_revision "
        "FROM clarification_bindings WHERE task_id=?" + predicate
        + " AND consumed_decision_id IS NULL ORDER BY issue_sequence",
        parameters,
    ).fetchall())
    if len(rows) == 1:
        row = rows[0]
        return (
            str(row["clarification_binding"]), str(row["decision_type"]),
            int(row["effective_contract_revision"]), False,
        )
    if rows:
        raise V12ServiceError("exactly one matching user decision must be pending", code="clarification_binding_stale")
    consumed = store._read(lambda connection: connection.execute(
        "SELECT clarification_binding,decision_type,effective_contract_revision "
        "FROM clarification_bindings WHERE task_id=?" + predicate
        + " AND consumed_decision_id IS NOT NULL ORDER BY issue_sequence DESC LIMIT 1",
        parameters,
    ).fetchone())
    if consumed is None:
        raise V12ServiceError("exactly one matching user decision must be pending", code="clarification_binding_stale")
    return (
        str(consumed["clarification_binding"]), str(consumed["decision_type"]),
        int(consumed["effective_contract_revision"]), True,
    )


def _stale_replay_conflict(exc: V12StoreError, replay_candidate: bool) -> None:
    if replay_candidate and exc.code == "command_conflict":
        raise V12ServiceError(
            "the consumed decision does not match this request",
            code="clarification_binding_stale",
        ) from None
    raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _pending_binding(store: V12Store, task_id: str, *, decision_type: str) -> tuple[str, int, bool]:
    binding, _kind, revision, replay_candidate = _decision_binding(
        store, task_id, decision_type=decision_type,
    )
    return binding, revision, replay_candidate


def _sole_pending_binding(store: V12Store, task_id: str) -> tuple[str, str, bool]:
    binding, kind, _revision, replay_candidate = _decision_binding(store, task_id)
    return binding, kind, replay_candidate


def _decision_receipt(task_ref: str, state: str, issued: Mapping[str, Any]) -> dict[str, Any]:
    return {"task_ref": task_ref, "state": state, "replayed": bool(issued.get("replayed"))}


def open_clarification(*, task_ref: str, prompt: str, prompt_language: str,
                       purpose: str = "clarification",
                       options: list[str] | None = None) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        if purpose == "closure_review":
            if options != ["revise", "close"]:
                raise V12ServiceError(
                    "closure review requires exactly revise and close",
                    code="invalid_argument", details={"field": "options"},
                )
            issued = DecisionAggregate(store).open_closure_review(
                task_id=canonical, prompt=prompt, prompt_language=prompt_language,
                subject_type="task", subject_id=canonical, assignment_id=None,
            )
            return _decision_receipt(task_ref, "pending_closure_review", issued)
        if purpose != "clarification" or options is not None:
            raise V12ServiceError(
                "ordinary clarification does not accept closure review options",
                code="invalid_argument", details={"field": "purpose" if purpose != "clarification" else "options"},
            )
        issued = DecisionAggregate(store).open_clarification(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type="task", subject_id=canonical, assignment_id=None,
        )
        return _decision_receipt(task_ref, "pending_clarification", issued)
    except V12ServiceError:
        raise
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def record_clarification(*, task_ref: str, response_original: str,
                         user_language: str, outcome: str | None = None) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    replay_candidate = False
    try:
        binding_ref, decision_type, replay_candidate = _sole_pending_binding(store, canonical)
        if decision_type == "closure_review":
            if outcome not in {"revise", "close"}:
                raise V12ServiceError(
                    "closure review requires an explicit revise or close outcome",
                    code="invalid_argument", details={"field": "outcome"},
                )
            issued = DecisionAggregate(store).record_closure_review(
                task_id=canonical, binding_ref=binding_ref, outcome=outcome,
                response_original=response_original, user_language=user_language,
                steering_delta=None,
            )
            return _decision_receipt(task_ref, "closure_review_recorded", issued)
        if decision_type != "clarification" or outcome is not None:
            raise V12ServiceError(
                "ordinary clarification does not accept a closure outcome",
                code="clarification_binding_mismatch", details={"field": "outcome"},
            )
        issued = DecisionAggregate(store).record_clarification(
            task_id=canonical, binding_ref=binding_ref,
            response_original=response_original, user_language=user_language,
            steering_delta=None,
        )
        return _decision_receipt(task_ref, "clarification_recorded", issued)
    except V12ServiceError:
        raise
    except V12StoreError as exc:
        _stale_replay_conflict(exc, replay_candidate)


def open_plan_review(*, task_ref: str, prompt: str,
                     prompt_language: str) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        plans = store._read(lambda connection: connection.execute(
            "SELECT DISTINCT r.report_id FROM reports r "
            "JOIN assignment_scope_snapshots s ON s.assignment_id=r.delegation_id "
            "WHERE r.task_id=? AND r.report_type='plan' AND r.assembly_state='finalized' "
            "AND r.review_policy='required' "
            "AND s.assignment_role='planning' "
            "AND s.contract_revision=(SELECT MAX(revision) FROM effective_contract_revisions WHERE task_id=r.task_id) "
            "ORDER BY r.created_sequence DESC LIMIT 1",
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
    replay_candidate = False
    try:
        binding_ref, _revision, replay_candidate = _pending_binding(
            store, canonical, decision_type="plan_review",
        )
        issued = DecisionAggregate(store).record_plan_review(
            task_id=canonical, binding_ref=binding_ref, outcome=outcome,
            response_original=response_original, user_language=user_language,
        )
        return _decision_receipt(task_ref, "plan_review_recorded", issued)
    except V12StoreError as exc:
        _stale_replay_conflict(exc, replay_candidate)


def open_steering(*, task_ref: str, prompt: str, prompt_language: str,
                  _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        issued = DecisionAggregate(store).open_steering(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type="task", subject_id=canonical, assignment_id=None,
        )
        if isinstance(_connection_context, dict):
            _connection_context.pop("steering_state_read_task_ref", None)
        return _decision_receipt(task_ref, "pending_steering", issued)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def record_steering(*, task_ref: str, response_original: str,
                    user_language: str, add: list[Mapping[str, Any]] | None = None,
                    retire: list[Mapping[str, Any]] | None = None,
                    _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(_connection_context, dict):
        fresh_task_ref = _connection_context.pop(
            "steering_state_read_task_ref", None,
        )
        if fresh_task_ref != task_ref:
            raise V12ServiceError(
                "steering requires a fresh same-connection task-state read",
                code="fresh_state_read_required",
            )
    store, canonical = _task_store(task_ref)
    replay_candidate = False
    try:
        binding_ref, binding_revision, replay_candidate = _pending_binding(
            store, canonical, decision_type="steer",
        )
        if replay_candidate:
            current = store._read(
                lambda connection: store._effective_contract_at_revision(
                    connection, canonical, binding_revision,
                ).get("items", [])
            )
        else:
            current = ledger.inspect_task(task_ref=task_ref).get("effective_contract", {}).get("items", [])
        if not isinstance(add, list) or not isinstance(retire, list):
            raise V12ServiceError(
                "steering outcome sets are invalid", code="invalid_argument",
                details={
                    "path": "$.add" if not isinstance(add, list) else "$.retire",
                    "expected": "array", "reason": "type",
                },
            )
        typed_add = [
            _validated_public_outcome(item, path=f"$.add[{index}]")
            for index, item in enumerate(add)
        ]
        typed_retire = [
            _validated_public_outcome(item, path=f"$.retire[{index}]")
            for index, item in enumerate(retire)
        ]
        retire_refs = _match_outcomes(
            [item for item in current if isinstance(item, Mapping)],
            typed_retire, path="$.retire",
        ) if typed_retire else []
        additions: list[dict[str, Any]] = []
        paired_replacement = len(typed_add) == 1 and len(retire_refs) == 1
        if paired_replacement:
            item = typed_add[0]
            target = retire_refs[0]
            additions.append({
                "category": "outcome_replacement",
                "outcome_ref": target,
                "text": item["outcome"],
                "acceptance": list(item["acceptance"]),
                "constraints": list(item["constraints"]),
                "verification": list(item["verification"]),
            })
        else:
            # Unpaired public additions are complete independent outcomes.
            # Keep them grouped as private durable atoms so the store never
            # infers an existing target or merges unrelated additions.
            additions.extend({
                "category": "outcome",
                "text": item["outcome"],
                "acceptance": list(item["acceptance"]),
                "constraints": list(item["constraints"]),
                "verification": list(item["verification"]),
            } for item in typed_add)
        steering_delta = {
            "add": additions,
            # One exact replacement targets and supersedes its current row.
            # All other additions remain independent while explicit retires
            # are committed in the same transaction.
            "retire_item_refs": [] if paired_replacement else retire_refs,
        }
        issued = DecisionAggregate(store).record_steering(
            task_id=canonical, binding_ref=binding_ref,
            response_original=response_original, user_language=user_language,
            steering_delta=steering_delta,
            supersedes_decision_id=None,
        )
        return _decision_receipt(task_ref, "steering_recorded", issued)
    except V12ServiceError:
        raise
    except V12StoreError as exc:
        _stale_replay_conflict(exc, replay_candidate)


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
        completion_notes=completion_notes, require_closure_review=True,
    )
    return {"task_ref": task_ref, "state": "closed", "replayed": bool(result.get("replayed"))}


__all__ = [
    "open_task", "read_task", "open_assignment",
    "publish_plan", "publish_result", "publish_documentation",
    "open_clarification", "record_clarification", "open_plan_review", "record_plan_review", "open_steering", "record_steering",
    "assess_governance", "close_task",
]
