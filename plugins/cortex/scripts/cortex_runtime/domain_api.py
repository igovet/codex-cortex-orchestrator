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
_STATE_READ_PAGE_LIMIT = 16


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
        result[public_key] = _publicize(item)
    return result


def _public_read_data(value: Any) -> Any:
    """Project read data without duplicating the transport pagination marker."""
    def strip_markers(item: Any) -> Any:
        if isinstance(item, list):
            return [strip_markers(entry) for entry in item]
        if not isinstance(item, Mapping):
            return item
        return {
            str(key): strip_markers(entry)
            for key, entry in item.items()
            if str(key) not in {"has_more", "task_ref"}
        }

    return strip_markers(_publicize(value))


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
        if len(normalized) == 1 and isinstance(normalized["outcome"], str):
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


def _select_report_inputs(store: V12Store, task_id: str, policy: object) -> list[str]:
    if policy == "none":
        return []
    def select(connection):
        if policy == "all_finalized":
            rows = connection.execute("SELECT report_id FROM reports WHERE task_id=? AND assembly_state='finalized' ORDER BY created_sequence", (task_id,)).fetchall()
        elif policy == "active_plan":
            rows = connection.execute(
                "SELECT DISTINCT r.report_id FROM reports r "
                "JOIN execution_graphs g ON g.plan_report_id=r.report_id "
                "WHERE r.task_id=? AND r.report_type='plan' AND r.assembly_state='finalized' "
                "AND g.revision=(SELECT MAX(revision) FROM effective_contract_revisions WHERE task_id=?) "
                "ORDER BY g.rowid DESC LIMIT 1", (task_id, task_id),
            ).fetchall()
        else:
            raise V12StoreError("report policy is invalid", code="invalid_argument", details={"field": "mission.report_policy"})
        return [str(row["report_id"]) for row in rows]
    try:
        return store._read(select)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def _evidence_human_views(
    store: V12Store, task_id: str, report_ids: list[str],
) -> list[dict[str, Any]]:
    """Return verified user-facing links for one fully consumed evidence set.

    Report identity stays private.  The coordinator receives only a stable
    semantic label and the verified Markdown link produced by the projection
    layer.  Immutable report views deliberately ignore unrelated later task
    chronology while still failing closed on an absent, conflicting, or
    renderer-stale projection.
    """
    if not report_ids:
        return []

    def read(connection: Any) -> list[dict[str, Any]]:
        return [store._report(connection, report_id, task_id=task_id) for report_id in report_ids]

    reports = store._read(read)
    views: list[dict[str, Any]] = []
    for report in reports:
        report_type = str(report.get("report_type") or "report")
        report_id = report.get("report_id")
        if not isinstance(report_id, str) or report.get("assembly_state") != "finalized":
            continue
        relative = (
            f"plans/revisions/{report_id}.md"
            if report_type == "plan"
            else f"reports/{report_id}.md"
        )
        view = store.human_view(task_id, relative, require_fresh=False)
        public_view: dict[str, Any] = {
            "kind": "plan" if report_type == "plan" else "report",
            "report_type": report_type,
            "status": view.get("status"),
        }
        if isinstance(view.get("source_sequence"), int):
            public_view["source_sequence"] = view["source_sequence"]
        if (
            view.get("status") == "ready"
            and isinstance(view.get("markdown_link"), str)
            and view["markdown_link"]
        ):
            public_view["markdown_link"] = view["markdown_link"]
        views.append(public_view)
    return views


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
        "1.15.6",
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
    outcome_contracts: list[dict[str, object]] = []
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            raise V12ServiceError("task outcomes are invalid", code="invalid_argument", details={"field": "task"})
        requirements.append(outcome.get("outcome"))
        acceptance = outcome.get("acceptance")
        if not isinstance(acceptance, list):
            raise V12ServiceError("task outcomes are invalid", code="invalid_argument", details={"field": "task"})
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
               "acceptance_criteria": [],
               "outcome_contracts": outcome_contracts, "context": context}
    result = ledger.create_task(**payload)
    created = result.get("task") if isinstance(result, Mapping) else None
    canonical = created.get("task_id") if isinstance(created, Mapping) else None
    public_ref = compact_task_ref(str(canonical)) if isinstance(canonical, str) else None
    if public_ref is None:
        raise V12ServiceError("task reference is unavailable", code="ledger_error")
    return {"task_ref": public_ref, "replayed": bool(result.get("replayed"))}


def read_task(*, task_ref: str, continue_: bool = False,
              _connection_context: dict[str, Any] | None = None, **compat: Any) -> dict[str, Any]:
    """Consume one worker-owned assignment with server-owned paging."""
    if "continue" in compat:
        continue_ = compat.pop("continue")
    if compat:
        raise V12ServiceError("task read shape is invalid", code="invalid_argument")
    context = _connection_context if isinstance(_connection_context, dict) else {}
    store, canonical, assignment_id, _coordinator_ref = _resolve_task_context(task_ref)
    if assignment_id is None:
        raise V12ServiceError("assignment read requires worker-scoped task_ref", code="wrong_connection")
    if context.get("_role") == "coordinator":
        raise V12ServiceError(
            "coordinator connection cannot consume worker authority",
            code="wrong_connection",
        )
    page_key = ("read_task", task_ref)
    if continue_:
        if context.get("read_key") != page_key or not context.get("has_more"):
            raise V12ServiceError("no bounded read is available to continue", code="report_cursor_invalid")
        cursor = context.get("cursor")
    else:
        cursor = None
        context.pop("cursor", None)
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
        raise V12ServiceError("worker assignment context is stale", code="assignment_stale")
    bound_continuation = (
        context.get("continuation_ref")
        if context.get("bootstrap_assignment_id") == assignment_id
        else None
    )
    raw = _read_assignment_page(
        store=store, assignment_id=assignment_id, cursor=cursor,
        continuation_ref=bound_continuation if isinstance(bound_continuation, str) else None,
    )
    if terminal_reconciliation:
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
            context.setdefault("assignment_evidence_pages", []).append(dict(page_evidence))
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
    return {"task_ref": task_ref, "data": _public_read_data(raw), "has_more": has_more}


def _current_native_projection(connection, task_id, task_ref, context):
    """One signed host observation shared by current read projections."""
    root = context.get("_native_plugin_data")
    if root is None:
        return None
    from cortex_runtime import graph_ledger
    from cortex_runtime.native_observation import verified_projection, digest
    epoch = connection.execute("SELECT barrier_epoch FROM project_integrity WHERE singleton=1").fetchone()[0]
    return verified_projection(Path(root), task_digest=digest(task_ref),
        revision=graph_ledger._current_revision(connection, task_id), barrier_epoch=epoch)


def read_state(*, task_ref: str,
               _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return scalar current graph facts and the recovery-order boundary."""
    from cortex_runtime import graph_ledger
    context = _connection_context if isinstance(_connection_context, dict) else {}
    store, task_id, assignment, _ = _resolve_task_context(task_ref)
    if assignment is not None:
        raise V12ServiceError("coordinator state requires coordinator task_ref", code="wrong_connection")
    def read(c):
        state = graph_ledger.task_projection(c, task_id,
            native_observation=_current_native_projection(c, task_id, task_ref, context))
        reports = c.execute("SELECT COUNT(*),COALESCE(SUM(status='completed'),0) FROM reports WHERE task_id=? AND assembly_state='finalized'", (task_id,)).fetchone()
        closure = c.execute("SELECT verdict,evidence_json FROM governance_closures WHERE subject_type='task' AND subject_id=? ORDER BY created_sequence DESC LIMIT 1", (task_id,)).fetchone()
        current_closure = closure[0] if closure and json.loads(closure[1]).get("revision") == state["revision"] else None
        return state, tuple(reports), current_closure
    current, reports, closure = store._read(read)
    counts, node_counts = {}, {}
    for outcome in current["outcomes"]:
        counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1
    for node in current["nodes"]:
        node_counts[node["state"]] = node_counts.get(node["state"], 0) + 1
    unfinished = len(current["unfinished"])
    recovering = context.get("_role") == "unknown" or context.get("_recovery_required") is True
    if recovering and unfinished:
        context["_required_next_operation"] = ("read_continuations", task_ref)
    data = {
        "effective_revision": current["revision"], "outcome_count": len(current["outcomes"]),
        "coverage_status_counts": counts, "node_state_counts": node_counts,
        "coverage_status": "complete" if counts.get("complete", 0) == len(current["outcomes"]) else "incomplete",
        "finalized_report_count": reports[0], "completed_report_count": reports[1],
        "unfinished_assignment_count": unfinished, "recovery_required": bool(recovering and unfinished),
        "reconciliation_required": current["reconciliation_required"],
        "reconciliation_epoch": current["barrier_epoch"],
        "artifact_generation_present": current["generation_present"],
        "closure_record_status": "recorded" if closure is not None else "not_recorded",
        "closure_verdict": closure,
        "admissible_operations": ["read_continuations"] if recovering and unfinished else
            ["read_scope", "read_evidence", "read_outcome", "read_timeline", "read_continuations"],
    }
    context.update(read_key=("read_state", task_ref), cursor=None, has_more=False,
        steering_state_read_task_ref=task_ref)
    return {"task_ref": task_ref, "data": data}

def _bounded_page(values: list[Any], start: int, *, maximum: int = 49_152) -> tuple[list[Any], int]:
    """Pack whole semantic records without slicing or ellipsizing them."""
    page: list[Any] = []
    position = start
    while position < len(values):
        candidate = [*page, values[position]]
        if page and len(_encoded_bytes(candidate)) > maximum:
            break
        page = candidate
        position += 1
        if len(_encoded_bytes(page)) > maximum:
            break
    return page, position


def read_scope(*, task_ref: str, responsibility: str, continue_: bool = False,
               _connection_context: dict[str, Any] | None = None, **keywords: Any) -> dict[str, Any]:
    """Read revision-bound graph selectors, never reconstruct outcome owners."""
    if "continue" in keywords:
        continue_ = keywords.pop("continue")
    if keywords or responsibility not in {"delivery", "evidence", "planning"}:
        raise V12ServiceError("scope read shape is invalid", code="invalid_argument")
    from cortex_runtime import graph_ledger
    context = _connection_context if isinstance(_connection_context, dict) else {}
    store, task_id, assignment_id, _ = _resolve_task_context(task_ref)
    if assignment_id is not None:
        raise V12ServiceError("coordinator scope requires coordinator task_ref", code="wrong_connection")
    key = ("read_scope", task_ref, responsibility)
    if continue_:
        if context.get("read_key") != key or not context.get("has_more"):
            raise V12ServiceError("no scope page remains", code="report_cursor_invalid")
        snapshot = context["scope_snapshot"]
        start = context["cursor"]
        current_revision = store._read(lambda c: graph_ledger._current_revision(c, task_id))
        if current_revision != context["scope_revision"]:
            raise V12ServiceError("scope revision is stale", code="assignment_stale")
    else:
        def read(c):
            revision = graph_ledger._current_revision(c, task_id)
            contract = store._effective_contract(c, task_id)
            rows = c.execute("SELECT graph_id,graph_kind FROM execution_graphs WHERE task_id=? AND revision=? ORDER BY rowid DESC",
                (task_id, revision)).fetchall()
            chosen = []
            candidate_seen = False
            for row in rows:
                if row["graph_kind"] != "bootstrap":
                    if candidate_seen:
                        continue
                    candidate_seen = True
                chosen.append(row)
            snapshot, admissions, mapping = [], {}, {}
            integrity = c.execute("SELECT generation_key,barrier_epoch FROM project_integrity WHERE singleton=1").fetchone()
            generation = integrity[0]
            native = _current_native_projection(c, task_id, task_ref, context)
            bootstrap_id = None
            for row in chosen:
                graph_id = row["graph_id"]
                record, graph = graph_ledger._graph(c, graph_id)
                definitions = {item[0]: json.loads(item[1]) for item in c.execute(
                    "SELECT node_key,content_json FROM execution_nodes WHERE graph_id=?", (graph_id,))}
                admissions[graph_id] = {"graph": graph_id, "digest": graph.digest, "revision": revision,
                    "native_observation": native, "barrier_epoch": integrity["barrier_epoch"],
                    "generation": generation, "owners": {item[0]: item[1] for item in c.execute(
                        "SELECT node_key,assignment_id FROM execution_nodes WHERE graph_id=?", (graph_id,))}}
                if row["graph_kind"] == "bootstrap":
                    bootstrap_id = graph_id
                for projection in graph_ledger.state_projection(c, graph_id, native_observation=native):
                    node = definitions[projection["node"]]
                    if node["responsibility"] != responsibility:
                        continue
                    if projection["state"] == "active":
                        from cortex_runtime.execution_graph import GraphError
                        owner = admissions[graph_id]["owners"][node["key"]]
                        owned = [key for key, value in admissions[graph_id]["owners"].items() if value == owner]
                        try:
                            lost_owner = graph_ledger.recoverable_owner(c, graph_id=graph_id, node_keys=owned, observation=native)
                        except GraphError as exc:
                            projection["loss_evidence"] = {"confirmed": False, "reason": exc.reason}
                        else:
                            projection["loss_evidence"] = {"confirmed": True, "complete_scope_size": len(owned),
                                "budget_exhausted": lost_owner["budget_exhausted"]}
                    if node["key"] in mapping:
                        raise V12StoreError("current graph node selectors are ambiguous", code="ledger_error")
                    mapping[node["key"]] = graph_id
                    snapshot.append({**projection, "kind": node["kind"], "execution_mode": node["execution_mode"]})
            bootstrap_state = {"available": False, "reasons": []} if bootstrap_id is None else graph_ledger.bootstrap_readiness(
                c, bootstrap_id, "planning" if responsibility == "planning" else "discovery" if responsibility == "evidence" else "none")
            return revision, snapshot, admissions, mapping, bootstrap_id, [item["text"] for item in contract["items"]], bootstrap_state
        try:
            revision, snapshot, admissions, mapping, bootstrap_id, names, bootstrap_state = store._read(read)
        except V12StoreError as exc:
            raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
        context.update(scope_snapshot=snapshot, scope_revision=revision, scope_admissions=admissions,
            scope_node_graphs=mapping, scope_bootstrap=bootstrap_id, scope_observed_nodes=[],
            scope_outcome_names=names, scope_task=task_ref, scope_responsibility=responsibility,
            scope_bootstrap_state=bootstrap_state)
        start = 0
    page, position = _bounded_page(snapshot, start)
    has_more = position < len(snapshot)
    context.update(read_key=key, cursor=position if has_more else None, has_more=has_more)
    context["scope_observed_nodes"].extend(item["node"] for item in page)
    context["steering_state_read_task_ref"] = task_ref
    context["steering_observed_outcomes"] = list(context["scope_outcome_names"])
    return {"task_ref": task_ref, "data": {
        "effective_revision": context["scope_revision"], "responsibility": responsibility,
        "nodes": page, "outcomes": [{"outcome": name} for name in context["scope_outcome_names"]],
        "bootstrap_available": context["scope_bootstrap_state"]["available"],
        "bootstrap_reasons": context["scope_bootstrap_state"]["reasons"],
    }, "has_more": has_more}
def read_outcome(*, task_ref: str, outcome: str,
                 _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read exactly one current semantic outcome by its unique exact name."""
    context = _connection_context if isinstance(_connection_context, dict) else {}
    _store, _canonical, assignment_id, coordinator_ref = _resolve_task_context(task_ref)
    if assignment_id is not None:
        raise V12ServiceError("coordinator outcome requires coordinator task_ref", code="wrong_connection")
    raw = ledger.inspect_task(task_ref=coordinator_ref, after_sequence=0, limit=1)
    contract = raw.get("effective_contract") if isinstance(raw.get("effective_contract"), Mapping) else {}
    items = contract.get("items") if isinstance(contract.get("items"), list) else []
    matches = [
        item for item in items
        if isinstance(item, Mapping) and _semantic_outcome(item).get("outcome") == outcome
    ]
    if len(matches) != 1:
        raise V12ServiceError(
            "outcome is missing or ambiguous", code="outcome_item_not_found",
            details={
                "path": "$.outcome", "expected": "unique_current_semantic_outcome",
                "reason": "semantic_outcome_missing" if not matches else "semantic_outcome_ambiguous",
            },
        )
    context.update({
        "read_key": ("read_outcome", task_ref, outcome),
        "cursor": None,
        "has_more": False,
        "steering_state_read_task_ref": task_ref,
    })
    observed = context.setdefault("steering_observed_outcomes", [])
    if isinstance(observed, list) and outcome not in observed:
        observed.append(outcome)
    return {
        "task_ref": task_ref,
        "data": {
            "effective_revision": contract.get("revision"),
            "outcome": _semantic_outcome(matches[0]),
        },
    }


def read_continuations(*, task_ref: str, continue_: bool = False,
                       _connection_context: dict[str, Any] | None = None,
                       **compat: Any) -> dict[str, Any]:
    """Read only current worker continuation projections for recovery."""
    if "continue" in compat:
        continue_ = compat.pop("continue")
    if compat:
        raise V12ServiceError("continuation read shape is invalid", code="invalid_argument")
    context = _connection_context if isinstance(_connection_context, dict) else {}
    store, canonical, assignment_id, coordinator_ref = _resolve_task_context(task_ref)
    if assignment_id is not None:
        raise V12ServiceError("coordinator continuations require coordinator task_ref", code="wrong_connection")
    key = ("read_continuations", task_ref)
    if continue_:
        if context.get("read_key") != key or not context.get("has_more"):
            raise V12ServiceError("no bounded read is available to continue", code="report_cursor_invalid")
        snapshot = context.get("continuation_snapshot")
        start = context.get("cursor")
        if not isinstance(snapshot, list) or isinstance(start, bool) or not isinstance(start, int):
            raise V12ServiceError("continuation is unavailable", code="ledger_error")
        revision = store._read(
            lambda connection: store._effective_contract(connection, canonical)["revision"]
        )
        if context.get("continuation_revision") != revision:
            context.update({"cursor": None, "has_more": False})
            raise V12ServiceError(
                "continuation snapshot is stale after a contract revision",
                code="assignment_stale",
            )
    else:
        from cortex_runtime import graph_ledger
        revision, snapshot = store._read(lambda c: (
            graph_ledger._current_revision(c, canonical), graph_ledger.continuations(c, canonical,
                native_observation=_current_native_projection(c, canonical, task_ref, context))))
        start = 0
        context["continuation_snapshot"] = snapshot
        context["continuation_revision"] = revision
    page, position = _bounded_page(snapshot, start)
    has_more = position < len(snapshot)
    context.update({"read_key": key, "cursor": position if has_more else None, "has_more": has_more})
    if not has_more and context.get("_required_next_operation") == ("read_continuations", task_ref):
        context.pop("_required_next_operation", None)
        context.pop("_recovery_required", None)
    return {"task_ref": task_ref, "data": {"continuations": page}, "has_more": has_more}


def read_evidence(*, task_ref: str, report_policy: str,
                  continue_: bool = False,
                  _connection_context: dict[str, Any] | None = None,
                  **compat: Any) -> dict[str, Any]:
    """Read coordinator-selected finalized evidence with server-owned paging."""
    if "continue" in compat:
        continue_ = compat.pop("continue")
    if compat:
        raise V12ServiceError("evidence read shape is invalid", code="invalid_argument")
    context = _connection_context if isinstance(_connection_context, dict) else {}
    store, canonical, assignment_id, _coordinator_ref = _resolve_task_context(task_ref)
    if assignment_id is not None:
        raise V12ServiceError("coordinator evidence requires coordinator task_ref", code="wrong_connection")
    page_key = ("read_evidence", task_ref, report_policy)
    if continue_:
        if context.get("read_key") != page_key or not context.get("has_more"):
            raise V12ServiceError("no bounded read is available to continue", code="report_cursor_invalid")
        cursor = context.get("cursor")
        revision = store._read(
            lambda connection: store._effective_contract(connection, canonical)["revision"]
        )
        if context.get("evidence_revision") != revision:
            context.update({"cursor": None, "has_more": False})
            raise V12ServiceError(
                "evidence continuation is stale after a contract revision",
                code="assignment_stale",
            )
    else:
        cursor = None
        context["evidence_revision"] = store._read(
            lambda connection: store._effective_contract(connection, canonical)["revision"]
        )
    report_ids = _select_report_inputs(store, canonical, report_policy)
    raw = (
        store.read_reports(
            task_id=canonical, report_ids=report_ids, cursor=cursor,
            max_bytes=65_536, consumer_delegation_id=None,
        )
        if report_ids else {"reports": [], "has_more": False, "next_cursor": None}
    )
    has_more = bool(raw.get("has_more"))
    context.update({"read_key": page_key, "cursor": raw.get("next_cursor"), "has_more": has_more})
    public_raw = dict(raw)
    if not has_more:
        human_views = _evidence_human_views(store, canonical, report_ids)
        if human_views:
            public_raw["human_views"] = human_views
            if report_policy == "active_plan" and len(human_views) == 1:
                public_raw["human_view"] = human_views[0]
    return {"task_ref": task_ref, "data": _public_read_data(public_raw), "has_more": has_more}


def read_timeline(*, task_ref: str, continue_: bool = False,
                  _connection_context: dict[str, Any] | None = None,
                  **compat: Any) -> dict[str, Any]:
    """Read coordinator history newest-first with server-owned paging."""
    if "continue" in compat:
        continue_ = compat.pop("continue")
    if compat:
        raise V12ServiceError("timeline read shape is invalid", code="invalid_argument")
    context = _connection_context if isinstance(_connection_context, dict) else {}
    store, canonical, assignment_id, _coordinator_ref = _resolve_task_context(task_ref)
    if assignment_id is not None:
        raise V12ServiceError("coordinator timeline requires coordinator task_ref", code="wrong_connection")
    page_key = ("read_timeline", task_ref)
    if continue_:
        if context.get("read_key") != page_key or not context.get("has_more"):
            raise V12ServiceError("no bounded read is available to continue", code="report_cursor_invalid")
        before_sequence = context.get("cursor")
        if isinstance(before_sequence, bool) or not isinstance(before_sequence, int):
            raise V12ServiceError("timeline continuation is unavailable", code="ledger_error")
    else:
        before_sequence = None
    raw = store.inspect_task_timeline(
        task_id=canonical, before_sequence=before_sequence,
        limit=_STATE_READ_PAGE_LIMIT,
    )
    has_more = bool(raw.get("has_more"))
    next_sequence = raw.get("next_sequence")
    if has_more and (isinstance(next_sequence, bool) or not isinstance(next_sequence, int)):
        raise V12ServiceError("timeline continuation is unavailable", code="ledger_error")
    public_raw = dict(raw)
    public_raw.pop("has_more", None)
    public_raw.pop("next_sequence", None)
    context.update({
        "read_key": page_key,
        "cursor": next_sequence if has_more else None,
        "has_more": has_more,
    })
    return {"task_ref": task_ref, "data": _public_read_data(public_raw), "has_more": has_more}


def open_assignment(*, task_ref: str, profile_name: str, model: str,
                    reasoning_effort: str, nodes: list[str] | None = None,
                    bootstrap: Mapping[str, Any] | None = None,
                    _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Claim only selectors from the current connection's last graph scope."""
    context = _connection_context if isinstance(_connection_context, dict) else {}
    store, task_id = _task_store(task_ref)
    if context.get("scope_task") != task_ref or context.get("read_key") != (
            "read_scope", task_ref, context.get("scope_responsibility")):
        raise V12ServiceError("read the current graph scope before assigning", code="assignment_stale")
    if (nodes is None) == (bootstrap is None):
        raise V12ServiceError("select nodes or one bootstrap intent", code="invalid_argument")
    recover = False
    kind = question = None
    if bootstrap is not None:
        if not isinstance(bootstrap, Mapping) or set(bootstrap) - {"kind", "question"}:
            raise V12ServiceError("bootstrap intent is invalid", code="invalid_argument")
        kind, question = bootstrap.get("kind"), bootstrap.get("question")
        if kind not in {"discovery", "planning"}:
            raise V12ServiceError("bootstrap intent is invalid", code="invalid_argument")
        expected = "planning" if kind == "planning" else "evidence"
        if context["scope_responsibility"] != expected:
            raise V12ServiceError("bootstrap scope responsibility mismatch", code="assignment_stale")
        graph_id = context.get("scope_bootstrap")
        selected = []
    else:
        if not isinstance(nodes, list) or not nodes or len(set(nodes)) != len(nodes) or any(
                node not in context["scope_observed_nodes"] for node in nodes):
            raise V12ServiceError("node selection was not observed in current scope", code="assignment_stale")
        graph_ids = {context["scope_node_graphs"][node] for node in nodes}
        if len(graph_ids) != 1:
            raise V12ServiceError("selected nodes belong to different graphs", code="invalid_argument")
        graph_id = next(iter(graph_ids))
        selected = nodes
        # The observed owner state fixes the transition. A ready reconciliation
        # node is an ordinary claim; selecting an unpublished active owner asks
        # the store to verify loss and reconcile it atomically. The caller must
        # not guess a second mode for the same server-owned selection.
        recover = any(item["node"] in selected and item["state"] == "active"
                      for item in context["scope_snapshot"])
    admission = context["scope_admissions"].get(graph_id)
    if admission is None:
        raise V12ServiceError("current graph admission is unavailable", code="assignment_stale")
    try:
        result, replayed = store.open_node_assignment(task_id=task_id, graph_id=graph_id,
            recover=recover,
            graph_digest=admission["digest"], node_keys=selected, profile_name=profile_name,
            model=model, reasoning_effort=reasoning_effort, bootstrap_provenance=_worker_capability_provenance(),
            admission=admission, bootstrap_kind=kind, bootstrap_question=question,
            native_plugin_data=context.get("_native_plugin_data"), native_task_ref=task_ref)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
    return _native_assignment_response(dict(result, replayed=replayed))


def _native_assignment_response(result: Mapping[str, Any]) -> dict[str, Any]:
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
        contract_context = worker.get("contract_context") if isinstance(worker, Mapping) else None
        if not isinstance(contract_context, Mapping):
            raise V12ServiceError("assignment scope is unavailable", code="ledger_error")
        assignment_context = {}
        if isinstance(delegation_view, Mapping):
            for key in ("profile_name",):
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
        node_scope = worker.get("assignment")
        if not isinstance(node_scope, Mapping):
            raise V12ServiceError("typed assignment scope is unavailable", code="ledger_error")
        # The consumed graph is the only publication authority. Never infer
        # ownership or publication type from old outcome lists or profile text.
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
                    "prompt": item.get("prompt"), "response_original": item.get("response_original"),
                    "user_language": item.get("user_language"),
                })
        authority = {
            "assignment": dict(node_scope),
            "assignment_context": assignment_context,
            "contract_context": dict(contract_context),
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
            public_page = _public_read_data(rendered)
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


def _preflight_worker_publication(
    *, store: V12Store, assignment_id: str, continuation_ref: str,
) -> bool:
    """Recognize supersession before resolving public coverage names.

    Coverage is resolved against the immutable assignment snapshot.  If a
    later steering revision has already revoked that assignment, resolving
    the worker's submitted names first can report ``outcome_item_not_found``
    (or another coverage diagnostic) instead of the authoritative
    superseded non-publication state. Resolve the consumed continuation up front;
    ``publish_node_report``
    repeats the check atomically to cover a revision race after this read.
    """
    try:
        continuation = store.publication_authority(
            continuation=continuation_ref, assignment_id=assignment_id,
        )
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
    if continuation.get("assignment_id") != assignment_id:
        raise V12ServiceError(
            "worker continuation is invalid", code="assignment_stale",
        )
    return bool(continuation.get("superseded"))


def _publish_typed(*, task_ref: str, kind: str, content: Mapping[str, Any],
                   context: dict[str, Any] | None) -> dict[str, Any]:
    store, assignment, continuation = _worker_publication_binding(task_ref=task_ref, context=context or {})
    if _preflight_worker_publication(store=store, assignment_id=assignment, continuation_ref=continuation):
        return {"task_ref": task_ref, "state": "superseded", "published": False, "replayed": False}
    if len(_encoded_bytes({"task_ref": task_ref, **content})) > MCP_OPERATION_MAX_BYTES:
        raise V12ServiceError("publication exceeds aggregate size", code="validation_error",
            details={"path": "$", "reason": "encoded_size", "max_bytes": MCP_OPERATION_MAX_BYTES,
                     "actual_bytes": len(_encoded_bytes({"task_ref": task_ref, **content}))})
    try:
        published = store.publish_node_report(delegation_id=assignment, continuation_ref=continuation,
            kind=kind, content=content)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
    return {"task_ref": task_ref, "state": published["state"], "published": published["published"],
            "replayed": published["replayed"]}


def publish_plan(*, task_ref: str, summary: str, scope: str, candidates: list[Mapping[str, Any]],
                 artifact: Mapping[str, Any], risks: list[str], unresolved: list[str], status: str,
                 _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    return _publish_typed(task_ref=task_ref, kind="plan", context=_connection_context, content={
        "summary": summary, "scope": scope, "candidates": candidates, "artifact": artifact,
        "risks": risks, "unresolved": unresolved, "status": status})


def publish_result(*, task_ref: str, summary: str, outcome: str,
                   changes: list[Mapping[str, Any]], node_coverage: list[Mapping[str, Any]],
                   documentation_impact: str, risks: list[str], unresolved: list[str], status: str,
                   artifact: Mapping[str, Any] | None,
                   _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    return _publish_typed(task_ref=task_ref, kind="result", context=_connection_context, content={
        "summary": summary, "outcome": outcome, "changes": changes, "node_coverage": node_coverage,
        "documentation_impact": documentation_impact, "risks": risks, "unresolved": unresolved,
        "status": status, "artifact": artifact})


def publish_documentation(*, task_ref: str, summary: str, findings: list[Mapping[str, Any]],
                          recommendations: list[str], node_coverage: list[Mapping[str, Any]],
                          documentation_impact: str, risks: list[str], unresolved: list[str], status: str,
                          artifact: Mapping[str, Any] | None,
                          _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    return _publish_typed(task_ref=task_ref, kind="documentation", context=_connection_context, content={
        "summary": summary, "findings": findings, "recommendations": recommendations, "node_coverage": node_coverage,
        "documentation_impact": documentation_impact, "risks": risks, "unresolved": unresolved,
        "status": status, "artifact": artifact})


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
    rows = store._read(lambda connection: [row for row in store._pending_user_decisions(connection, task_id)
        if decision_type is None or row["decision_type"] == decision_type])
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
    return {"task_ref": task_ref, "state": state, "replayed": bool(issued.get("replayed")),
            **({"effect": issued["effect"]} if "effect" in issued else {})}


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
                subject_type="task", subject_id=canonical,
            )
            return _decision_receipt(task_ref, "pending_closure_review", issued)
        if purpose != "clarification" or options is not None:
            raise V12ServiceError(
                "ordinary clarification does not accept closure review options",
                code="invalid_argument", details={"field": "purpose" if purpose != "clarification" else "options"},
            )
        issued = DecisionAggregate(store).open_clarification(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type="task", subject_id=canonical,
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
        plans = _select_report_inputs(store, canonical, "active_plan")
        if len(plans) != 1:
            raise V12ServiceError("an active finalized plan is required", code="approval_view_required")
        plan_id = plans[0]
        issued = DecisionAggregate(store).open_plan_review(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type="plan", subject_id=plan_id,
        )
        receipt = _decision_receipt(task_ref, "pending_plan_review", issued)
        views = _evidence_human_views(store, canonical, [plan_id])
        if len(views) != 1 or views[0].get("status") != "ready":
            raise V12ServiceError(
                "the active plan view is unavailable",
                code="approval_view_required",
            )
        receipt["data"] = {"human_view": views[0]}
        from cortex_runtime.candidate_family import read_family
        def alternatives(connection):
            row = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND plan_report_id=? AND revision=(SELECT MAX(revision) FROM effective_contract_revisions WHERE task_id=?)", (canonical, plan_id, canonical)).fetchone()
            family = read_family(connection, row[0]) if row else None
            return [{"key": item["definition"]["key"], "consequences": item["definition"]["consequences"]} for item in family.data()["candidates"]] if family else []
        branches = store._read(alternatives)
        if branches:
            receipt["data"]["alternatives"] = branches
        return receipt
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def record_plan_review(*, task_ref: str, outcome: str,
                       response_original: str, user_language: str,
                       branch_key: str | None = None) -> dict[str, Any]:
    from cortex_runtime.execution_graph import GraphError
    store, canonical = _task_store(task_ref)
    replay_candidate = False
    try:
        binding_ref, _revision, replay_candidate = _pending_binding(
            store, canonical, decision_type="plan_review",
        )
        issued = DecisionAggregate(store).record_plan_review(
            task_id=canonical, binding_ref=binding_ref, outcome=outcome,
            response_original=response_original, user_language=user_language,
            branch_key=branch_key,
        )
        return _decision_receipt(task_ref, "plan_review_recorded", issued)
    except V12StoreError as exc:
        _stale_replay_conflict(exc, replay_candidate)
    except GraphError as exc:
        raise V12ServiceError("plan selection is not admissible", code="invalid_argument", details={"reason": exc.reason}) from None


def open_steering(*, task_ref: str, prompt: str, prompt_language: str,
                  _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    try:
        issued = DecisionAggregate(store).open_steering(
            task_id=canonical, prompt=prompt, prompt_language=prompt_language,
            subject_type="task", subject_id=canonical,
        )
        if isinstance(_connection_context, dict):
            _connection_context.pop("steering_state_read_task_ref", None)
            _connection_context.pop("steering_observed_outcomes", None)
        return _decision_receipt(task_ref, "pending_steering", issued)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None


def record_steering(*, task_ref: str, response_original: str,
                    user_language: str, add: list[Mapping[str, Any]] | None = None,
                    retire: list[str] | None = None,
                    _connection_context: dict[str, Any] | None = None) -> dict[str, Any]:
    store, canonical = _task_store(task_ref)
    replay_candidate = False
    try:
        if not isinstance(add, list) or not isinstance(retire, list):
            raise V12ServiceError(
                "steering outcome sets are invalid", code="invalid_argument",
                details={
                    "path": "$.add" if not isinstance(add, list) else "$.retire",
                    "expected": "array", "reason": "type",
                },
            )
        if not add and not retire:
            raise V12ServiceError(
                "steering requires a semantic outcome change",
                code="invalid_argument",
                details={
                    "path": "$",
                    "expected": "non_empty_add_or_retire",
                    "reason": "semantic_noop",
                },
            )
        typed_add = [
            _validated_public_outcome(item, path=f"$.add[{index}]")
            for index, item in enumerate(add)
        ]
        if any(not isinstance(item, str) or not item.strip() for item in retire):
            raise V12ServiceError(
                "steering retire names are invalid", code="invalid_argument",
                details={"path": "$.retire", "expected": "exact_current_outcome_names", "reason": "canonical_semantic_invalid"},
            )
        if len(set(retire)) != len(retire):
            raise V12ServiceError(
                "steering retire names are duplicated", code="invalid_argument",
                details={"path": "$.retire", "expected": "unique_items", "reason": "duplicate"},
            )

        def prepare_delta(*, revision: int | None, replay: bool) -> dict[str, Any]:
            current = (
                store._read(
                    lambda connection: store._effective_contract_at_revision(
                        connection, canonical, int(revision),
                    ).get("items", [])
                )
                if replay and revision is not None
                else ledger.inspect_task(task_ref=task_ref).get(
                    "effective_contract", {},
                ).get("items", [])
            )
            observed = (
                _connection_context.get("steering_observed_outcomes", [])
                if isinstance(_connection_context, dict) else retire
            )
            if not replay and any(name not in observed for name in retire):
                raise V12ServiceError(
                    "steering retirement requires a fresh observed outcome name",
                    code="fresh_state_read_required",
                    details={"path": "$.retire", "expected": "observed_current_semantic_outcome", "reason": "fresh_scope_required"},
                )
            retire_refs = _match_outcomes(
                [item for item in current if isinstance(item, Mapping)],
                retire, path="$.retire",
            ) if retire else []
            additions: list[dict[str, Any]] = []
            paired_replacement = len(typed_add) == 1 and len(retire_refs) == 1
            if paired_replacement:
                item = typed_add[0]
                additions.append({
                    "category": "outcome_replacement",
                    "outcome_ref": retire_refs[0],
                    "text": item["outcome"],
                    "acceptance": list(item["acceptance"]),
                    "constraints": list(item["constraints"]),
                    "verification": list(item["verification"]),
                })
            else:
                additions.extend({
                    "category": "outcome",
                    "text": item["outcome"],
                    "acceptance": list(item["acceptance"]),
                    "constraints": list(item["constraints"]),
                    "verification": list(item["verification"]),
                } for item in typed_add)
            return {
                "add": additions,
                "retire_item_refs": [] if paired_replacement else retire_refs,
            }

        aggregate = DecisionAggregate(store)

        def finish(issued: Mapping[str, Any]) -> dict[str, Any]:
            if isinstance(_connection_context, dict):
                _connection_context.pop("steering_state_read_task_ref", None)
                _connection_context.pop("steering_observed_outcomes", None)
            return _decision_receipt(task_ref, "steering_recorded", issued)

        def record_direct() -> dict[str, Any]:
            revision = store._read(lambda connection: store._effective_contract(connection, canonical)["revision"])
            delta = prepare_delta(revision=revision, replay=False)
            return aggregate.record_direct_steering(
                task_id=canonical, response_original=response_original, user_language=user_language,
                steering_delta=delta, expected_revision=revision,
            )
        try:
            binding_ref, binding_revision, replay_candidate = _pending_binding(
                store, canonical, decision_type="steer",
            )
        except V12ServiceError as exc:
            if exc.code != "clarification_binding_stale" or (not typed_add and not retire):
                raise
            # The user's current message can itself be the steering decision.
            # Open and consume its durable binding inside this public operation
            # instead of asking the user to confirm the same instruction again.
            return finish(record_direct())

        if replay_candidate and (typed_add or retire):
            # The most recent consumed steering binding is a valid replay
            # candidate after compaction, but it is not an authority for a
            # later direct user change.  Compare the semantic delta that the
            # bound revision would reconstruct with the immutable consumed
            # delta before deciding whether this is that exact replay.  When
            # the delta differs (including a target introduced by a newer
            # contract revision), create one fresh direct binding and record
            # the user's already-stated change without asking for a duplicate
            # confirmation.  A response-only mutation with the same delta is
            # still left to receipt conflict handling and remains stale.
            try:
                replay_delta = prepare_delta(
                    revision=binding_revision, replay=True,
                )
            except V12ServiceError:
                replay_delta = None
            persisted = store._read(lambda connection: connection.execute(
                "SELECT d.steering_delta_json FROM clarification_bindings b "
                "LEFT JOIN user_decisions d ON d.decision_id=b.consumed_decision_id "
                "WHERE b.clarification_binding=? AND b.project_hash=?",
                (binding_ref, store.project_hash),
            ).fetchone())
            persisted_delta: object = None
            if persisted is not None and persisted["steering_delta_json"] is not None:
                try:
                    persisted_delta = json.loads(str(persisted["steering_delta_json"]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    persisted_delta = None
            delta_matches = replay_delta is not None and json.dumps(
                replay_delta, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ) == json.dumps(
                persisted_delta, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            if not delta_matches:
                return finish(record_direct())

        steering_delta = prepare_delta(
            revision=binding_revision, replay=replay_candidate,
        )
        try:
            issued = aggregate.record_steering(
                task_id=canonical, binding_ref=binding_ref,
                response_original=response_original, user_language=user_language,
                steering_delta=steering_delta,
                supersedes_decision_id=None,
            )
        except V12StoreError as exc:
            direct_binding = store._read(lambda connection: connection.execute(
                "SELECT b.prompt,d.response_original FROM clarification_bindings b "
                "LEFT JOIN user_decisions d ON d.decision_id=b.consumed_decision_id "
                "WHERE b.clarification_binding=? AND b.project_hash=?",
                (binding_ref, store.project_hash),
            ).fetchone())
            prior_was_direct = (
                direct_binding is not None
                and str(direct_binding["prompt"]) == str(direct_binding["response_original"])
            )
            if not (
                replay_candidate and prior_was_direct
                and exc.code == "command_conflict" and (typed_add or retire)
            ):
                raise
            # A different explicit user change follows an earlier consumed
            # steering decision. Create a fresh binding once; exact retries
            # continue to reconcile through the consumed receipt above.
            return finish(record_direct())
        return finish(issued)
    except V12ServiceError:
        raise
    except V12StoreError as exc:
        _stale_replay_conflict(exc, replay_candidate)


def assess_governance(*, task_ref: str, mode: str, rationale: str = "",
                      risk_factors: list[str] | None = None, execution_route: str = "planned",
                      minimal_mode: str | None = None, user_review_requested: bool | None = None) -> dict[str, Any]:
    store, canonical, assignment_id, _ = _resolve_task_context(task_ref)
    if assignment_id is not None:
        raise V12ServiceError("governance requires coordinator task_ref", code="wrong_connection")
    try:
        _, replayed = store.assess_execution_governance(task_id=canonical, mode=mode, rationale=rationale,
            risk_factors=risk_factors, execution_route=execution_route, minimal_mode=minimal_mode,
            user_review_requested=user_review_requested)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
    return {"task_ref": task_ref, "state": "governance_assessed", "replayed": replayed}


def close_task(*, task_ref: str, verdict: str,
               unresolved_risks: object | None = None, follow_ups: object | None = None,
               completion_notes: object | None = None) -> dict[str, Any]:
    store, canonical, assignment_id, _ = _resolve_task_context(task_ref)
    if assignment_id is not None:
        raise V12ServiceError(
            "task closure requires coordinator task_ref",
            code="wrong_connection",
        )
    try:
        _, replayed = store.close_execution_task(task_id=canonical, verdict=verdict,
            unresolved_risks=unresolved_risks, follow_ups=follow_ups, completion_notes=completion_notes)
    except V12StoreError as exc:
        raise V12ServiceError(str(exc), code=exc.code, details=exc.details) from None
    report_ids = _select_report_inputs(store, canonical, "all_finalized")
    return {
        "task_ref": task_ref,
        "state": "closed",
        "replayed": replayed,
        "data": {"human_views": _evidence_human_views(store, canonical, report_ids)},
    }


__all__ = [
    "open_task", "read_task", "read_state", "read_scope", "read_outcome",
    "read_continuations", "read_evidence", "read_timeline", "open_assignment",
    "publish_plan", "publish_result", "publish_documentation",
    "open_clarification", "record_clarification", "open_plan_review", "record_plan_review", "open_steering", "record_steering",
    "assess_governance", "close_task",
]
