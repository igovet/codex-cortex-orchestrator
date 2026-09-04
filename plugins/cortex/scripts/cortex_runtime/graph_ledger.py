"""Transactional graph integrity primitives; no host or project filesystem IO.

Every mutator requires the caller's existing SQLite transaction. Public
operations retain responsibility for actor authentication and command receipts.
No primitive chooses a profile, schedules work, or interprets user prose.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from cortex_runtime.execution_graph import (
    GraphError, KEY_RE, ValidatedGraph, assignment_compatible, project_readiness,
    publication_kind, validate_coverage, validate_graph,
)


DDL = (
    """CREATE TABLE execution_policies(
        assessment_id TEXT PRIMARY KEY REFERENCES governance_assessments(assessment_id),
        task_id TEXT NOT NULL REFERENCES tasks(task_id), revision INTEGER NOT NULL,
        execution_route TEXT NOT NULL CHECK(execution_route IN ('planned','minimal')),
        user_review_requested INTEGER NOT NULL CHECK(user_review_requested IN (0,1)),
        minimal_mode TEXT CHECK(minimal_mode IN ('read_only','mutating')))""",
    """CREATE TABLE execution_graphs(
        graph_id TEXT PRIMARY KEY, graph_kind TEXT NOT NULL CHECK(graph_kind IN ('bootstrap','candidate','generated')),
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        revision INTEGER NOT NULL, plan_report_id TEXT NOT NULL,
        planner_assignment_id TEXT NOT NULL, content_digest TEXT NOT NULL,
        content_json TEXT NOT NULL, order_json TEXT NOT NULL,
        activation TEXT NOT NULL CHECK(activation IN ('candidate','validated','active','rejected','stale')),
        review_required INTEGER NOT NULL CHECK(review_required IN (0,1)),
        approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1)),
        UNIQUE(task_id,revision,plan_report_id))""",
    """CREATE TABLE plan_candidate_families(
        graph_id TEXT PRIMARY KEY REFERENCES execution_graphs(graph_id),
        content_digest TEXT NOT NULL, content_json TEXT NOT NULL)""",
    """CREATE TABLE plan_candidate_selections(
        graph_id TEXT PRIMARY KEY REFERENCES execution_graphs(graph_id),
        family_graph_id TEXT NOT NULL UNIQUE REFERENCES plan_candidate_families(graph_id),
        branch_key TEXT NOT NULL,
        validation_assignment_id TEXT NOT NULL REFERENCES execution_assignments(assignment_id))""",
    """CREATE TABLE execution_nodes(
        graph_id TEXT NOT NULL REFERENCES execution_graphs(graph_id),
        node_key TEXT NOT NULL, content_json TEXT NOT NULL, state TEXT NOT NULL,
        assignment_id TEXT, artifact_generation TEXT, facts_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(graph_id,node_key))""",
    """CREATE TABLE execution_assignments(
        assignment_id TEXT PRIMARY KEY, graph_id TEXT NOT NULL REFERENCES execution_graphs(graph_id),
        task_id TEXT NOT NULL REFERENCES tasks(task_id), revision INTEGER NOT NULL,
        nodes_json TEXT NOT NULL, mode TEXT NOT NULL, terminal_kind TEXT NOT NULL,
        target_generation TEXT, protected_task_name TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('active','published','stale','snapshot_conflict','lost')),
        quiescent INTEGER NOT NULL DEFAULT 0 CHECK(quiescent IN (0,1)))""",
    """CREATE TABLE execution_publications(
        assignment_id TEXT PRIMARY KEY REFERENCES execution_assignments(assignment_id),
        report_id TEXT NOT NULL UNIQUE, payload_digest TEXT NOT NULL,
        payload_json TEXT NOT NULL, artifact_generation TEXT)""",
    """CREATE TABLE artifact_generations(
        ordinal INTEGER PRIMARY KEY AUTOINCREMENT, generation_key TEXT NOT NULL UNIQUE,
        task_id TEXT NOT NULL REFERENCES tasks(task_id), revision INTEGER NOT NULL,
        method TEXT NOT NULL, fingerprint TEXT NOT NULL, parent_key TEXT,
        source_assignment_id TEXT, observation_json TEXT NOT NULL, paths_json TEXT NOT NULL)""",
    """CREATE TABLE project_integrity(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation_key TEXT,
        reconciliation_required INTEGER NOT NULL CHECK(reconciliation_required IN (0,1)),
        barrier_epoch INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE execution_events(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, graph_id TEXT NOT NULL,
        event TEXT NOT NULL, details_json TEXT NOT NULL)""",
)


def create_tables(connection: sqlite3.Connection) -> None:
    _transaction(connection)
    for statement in DDL:
        connection.execute(statement)
    connection.execute("INSERT INTO project_integrity VALUES (1,NULL,0,0)")


def _transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise GraphError("transaction_required")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _event(connection: sqlite3.Connection, graph_id: str, event: str, details: Any) -> None:
    connection.execute("INSERT INTO execution_events(graph_id,event,details_json) VALUES (?,?,?)",
                       (graph_id, event, _json(details)))


def _current_revision(connection: sqlite3.Connection, task_id: str) -> int:
    row = connection.execute("SELECT MAX(revision) FROM effective_contract_revisions WHERE task_id=?", (task_id,)).fetchone()
    if row is None or row[0] is None:
        raise GraphError("task_missing")
    return int(row[0])


def _graph(connection: sqlite3.Connection, graph_id: str) -> tuple[dict[str, Any], ValidatedGraph]:
    row = connection.execute("SELECT * FROM execution_graphs WHERE graph_id=?", (graph_id,)).fetchone()
    if row is None:
        raise GraphError("graph_missing")
    value = dict(row)
    canonical = value["content_json"]
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != value["content_digest"]:
        raise GraphError("graph_corrupted")
    return value, ValidatedGraph(canonical, value["content_digest"], tuple(json.loads(value["order_json"])))


def create_candidate(connection: sqlite3.Connection, *, task_id: str, revision: int,
                     plan_report_id: str, planner_assignment_id: str,
                     graph: Mapping[str, Any], outcomes: Sequence[str], review_required: bool) -> str:
    _transaction(connection)
    if revision != _current_revision(connection, task_id):
        raise GraphError("graph_revision_stale")
    validated = validate_graph(graph, outcomes)
    graph_id = _digest([task_id, revision, plan_report_id])
    prior = connection.execute("SELECT content_digest,planner_assignment_id,review_required FROM execution_graphs WHERE graph_id=?", (graph_id,)).fetchone()
    if prior is not None:
        if tuple(prior) != (validated.digest, planner_assignment_id, int(review_required)):
            raise GraphError("graph_publication_conflict")
        return graph_id
    if connection.execute("SELECT 1 FROM execution_assignments a JOIN execution_graphs g ON g.graph_id=a.graph_id WHERE a.task_id=? AND a.revision=? AND a.state='active' AND g.graph_kind!='bootstrap' LIMIT 1", (task_id, revision)).fetchone():
        raise GraphError("execution_evidence_pending")
    connection.execute(
        "INSERT INTO execution_graphs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (graph_id, "candidate", task_id, revision, plan_report_id, planner_assignment_id,
         validated.digest, validated.canonical_json, _json(validated.order), "candidate", int(review_required), 0),
    )
    for node in validated.data()["nodes"]:
        connection.execute("INSERT INTO execution_nodes(graph_id,node_key,content_json,state) VALUES (?,?,?,'waiting')",
                           (graph_id, node["key"], _json(node)))
    _create_validation_nodes(connection, graph_id=graph_id, graph=graph, outcomes=outcomes)
    _event(connection, graph_id, "candidate_created", {"revision": revision})
    return graph_id


def _create_validation_nodes(connection, *, graph_id, graph, outcomes):
    """System-owned validation intent, separate from authored execution nodes."""
    _transaction(connection)
    target = connection.execute("SELECT method,paths_json FROM artifact_generations WHERE generation_key=(SELECT generation_key FROM project_integrity)").fetchone()
    changed_boundary = target is not None and (target["method"] != graph["fingerprint_method"] or json.loads(target["paths_json"]) != sorted(set(graph["artifact_paths"])))
    boundary = _bootstrap_node("baseline-candidate", "discovery", outcomes,
        "Establish the candidate's declared artifact boundary without changing files. Observe the old sealed boundary before and after the new boundary's stable pair of observations; preserve both manifests.",
        checks=[{"key": "boundary", "description": "The old boundary remains unchanged while the new method and path boundary are observed twice with identical fingerprints.", "required": True}])
    connection.execute("INSERT INTO execution_nodes(graph_id,node_key,content_json,state) VALUES (?,?,?,?)", (graph_id, boundary["key"], _json(boundary), "waiting" if changed_boundary else "skipped"))
    validation = {
        "key": "validate-candidate", "kind": "graph_validation", "responsibility": "evidence",
        "execution_mode": "read_only", "owner": "Independent candidate validation",
        "contributions": [], "verifies": [{"kind": "outcome", "name": name} for name in outcomes],
        "mutation_domains": [], "requires": [], "provides": [], "dependencies": [],
        "work": ["Independently validate the candidate against the complete contract and finalized discovery evidence."],
        "acceptance": ["Every source requirement, artifact dependency, ownership boundary, and remediation policy is represented correctly."],
        "checks": [{"key": key, "description": description, "required": True} for key, description in (
            ("coverage", "Check complete source-to-contract and candidate coverage."),
            ("dependencies", "Check prerequisites match the artifacts each node inspects."),
            ("independence", "Check contribution ownership and verification independence."),
            ("remediation", "Check finite remediation policies cover every defect-producing audit."),
            ("boundaries", "Check responsibilities and mutation domains are coherent."),
        )], "activation": "always",
    }
    connection.execute("INSERT INTO execution_nodes(graph_id,node_key,content_json,state) VALUES (?,?,?,'waiting')",
                       (graph_id, validation["key"], _json(validation)))


def pending_governance_review(connection: sqlite3.Connection, task_id: str) -> bool:
    """A fulfilled decision does not become a perpetual replan question.

    New governance evidence opens a fresh boundary. An explicit revision or
    cancellation of the latest reviewed plan cannot reuse its old approval.
    Contract steering alone neither invents risk nor copies graph approval.
    Every successor graph still needs its own independent validation.
    """
    assessment = connection.execute("SELECT a.mode,a.created_sequence,p.user_review_requested FROM governance_assessments a LEFT JOIN execution_policies p ON p.assessment_id=a.assessment_id WHERE a.task_id=? ORDER BY a.created_sequence DESC LIMIT 1", (task_id,)).fetchone()
    if assessment is None or not (assessment["mode"] == "full" or assessment["user_review_requested"]):
        return False
    decision = connection.execute("SELECT decision_type,created_sequence FROM user_decisions WHERE task_id=? AND subject_type='plan' AND decision_type IN ('approve','request_revision','cancel') ORDER BY created_sequence DESC LIMIT 1", (task_id,)).fetchone()
    return decision is None or decision["decision_type"] != "approve" or decision["created_sequence"] < assessment["created_sequence"]


def _review_state(connection: sqlite3.Connection, record: Mapping[str, Any]) -> tuple[bool, bool]:
    assessment = connection.execute("SELECT a.mode,a.created_sequence,p.user_review_requested FROM governance_assessments a LEFT JOIN execution_policies p ON p.assessment_id=a.assessment_id WHERE a.task_id=? ORDER BY a.created_sequence DESC LIMIT 1", (record["task_id"],)).fetchone()
    required = bool(record["review_required"]) or pending_governance_review(connection, record["task_id"])
    approved = bool(record["approved"])
    if required and approved and assessment is not None:
        approval = connection.execute("SELECT MAX(created_sequence) FROM user_decisions WHERE task_id=? AND subject_type='plan' AND subject_id=? AND decision_type='approve'", (record["task_id"], record["plan_report_id"])).fetchone()[0]
        approved = approval is not None and approval >= assessment["created_sequence"]
    return required, approved


def _project_ownership(connection, graph_id, projected, *, native_observation=None):
    """Expose the same physical ownership barrier used by atomic admission."""
    definitions = {row[0]: json.loads(row[1]) for row in connection.execute(
        "SELECT node_key,content_json FROM execution_nodes WHERE graph_id=?", (graph_id,))}
    record, graph = _graph(connection, graph_id)
    current = connection.execute("SELECT method,paths_json FROM artifact_generations WHERE generation_key=(SELECT generation_key FROM project_integrity)").fetchone()
    mismatch = current is not None and (current["method"] != graph.data()["fingerprint_method"] or json.loads(current["paths_json"]) != sorted(set(graph.data()["artifact_paths"])))
    owners = list(connection.execute("SELECT assignment_id,mode FROM execution_assignments WHERE state='active' OR (state IN ('stale','snapshot_conflict','lost') AND quiescent=0)"))
    reconciliation = _reconciliation(connection, graph_id)
    confirmed = set()
    if reconciliation:
        task = _graph(connection, graph_id)[0]["task_id"]
        evidence = native_quiescence(connection, task_id=task, observation=native_observation)
        if evidence["ready"]:
            confirmed.update(evidence["confirmed"])
    for item in projected:
        if (record["graph_kind"] != "bootstrap" and mismatch and item["node"] != "baseline-candidate"
                and item["state"] in {"ready", "complete", "resolved"} and definitions[item["node"]]["execution_mode"] != "artifact_independent"):
            item["state"] = "waiting"
            item["reasons"].append({"kind": "artifact_boundary_mismatch"})
        if item["state"] != "ready":
            continue
        selected_reconciliation = reconciliation is not None and item["node"] == reconciliation["node"]
        modes = [owner["mode"] for owner in owners if not selected_reconciliation or owner["assignment_id"] not in confirmed]
        try:
            assignment_compatible([definitions[item["node"]]], modes)
        except GraphError as exc:
            if exc.reason != "project_mutation_barrier":
                raise
            item["state"] = "waiting"
            item["reasons"].append({"kind": "project_mutation_barrier"})
    return projected


def state_projection(connection: sqlite3.Connection, graph_id: str, *, native_observation: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    record, graph = _graph(connection, graph_id)
    integrity = connection.execute("SELECT generation_key,reconciliation_required,barrier_epoch FROM project_integrity WHERE singleton=1").fetchone()
    observations = {}
    for row in connection.execute("SELECT * FROM execution_nodes WHERE graph_id=?", (graph_id,)):
        facts = json.loads(row["facts_json"])
        observations[row["node_key"]] = {
            "state": row["state"], "artifact_generation": row["artifact_generation"],
            "has_not_run": any(fact["state"] == "not_run" for fact in facts),
            "has_failed": any(fact["state"] == "failed" for fact in facts),
        }
    if record["revision"] != _current_revision(connection, record["task_id"]):
        return [{"node": key, "state": "stale", "reasons": [{"kind": "graph_revision_stale"}]} for key in observations]
    if record["graph_kind"] == "bootstrap":
        projected = _bootstrap_projection(connection, graph_id, integrity, observations, native_observation=native_observation)
        return _project_ownership(connection, graph_id, projected, native_observation=native_observation)
    latest = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind!='bootstrap' ORDER BY rowid DESC LIMIT 1", (record["task_id"], record["revision"])).fetchone()
    if latest is not None and latest[0] != graph_id:
        return [{"node": key, "state": "stale", "reasons": [{"kind": "graph_replaced"}]} for key in observations]
    from cortex_runtime.remediation import projections
    review_required, approved = _review_state(connection, record)
    resolved, generated = projections(connection, graph_id, current_generation=integrity["generation_key"],
        admitted=record["activation"] == "active" and (not review_required or approved) and not integrity["reconciliation_required"])
    observations.update(resolved)
    projected = project_readiness(graph, observations, activated=record["activation"] == "active",
                             approved=approved, review_required=review_required,
                             current_generation=integrity["generation_key"],
                             reconciliation_required=bool(integrity["reconciliation_required"]))
    if record["graph_kind"] == "generated":
        projected.extend(generated)
        return _project_ownership(connection, graph_id, projected, native_observation=native_observation)
    validation_state = observations["validate-candidate"]["state"]
    reasons = []
    if record["activation"] == "stale":
        validation_state = "stale"
    elif validation_state == "waiting":
        if integrity["reconciliation_required"]:
            reasons.append({"kind": "reconciliation_required"})
        if integrity["generation_key"] is None:
            reasons.append({"kind": "artifact_generation_missing"})
        if "baseline-candidate" in observations and observations["baseline-candidate"]["state"] not in {"complete", "skipped"}:
            reasons.append({"kind": "predecessor_unsatisfied", "node": "baseline-candidate"})
        validation_state = "waiting" if reasons else "ready"
    if "baseline-candidate" in observations:
        state = observations["baseline-candidate"]["state"]
        boundary_reasons = []
        current = connection.execute("SELECT method,paths_json FROM artifact_generations WHERE generation_key=?", (integrity["generation_key"],)).fetchone()
        changed = current is not None and (current["method"] != graph.data()["fingerprint_method"] or json.loads(current["paths_json"]) != sorted(set(graph.data()["artifact_paths"])))
        if changed and state in {"complete", "skipped"}:
            state = "waiting"
            attempts = connection.execute("SELECT COUNT(*) FROM execution_assignments WHERE graph_id=? AND nodes_json=?", (graph_id, _json(["baseline-candidate"]))).fetchone()[0]
            if attempts >= 1 + graph.data()["budgets"]["additional_evidence"]:
                state = "exhausted"
                boundary_reasons.append({"kind": "boundary_reconciliation_budget_exhausted"})
        if state == "waiting":
            if record["activation"] in {"rejected", "stale"}:
                boundary_reasons.append({"kind": "candidate_inactive"})
            if integrity["reconciliation_required"]:
                boundary_reasons.append({"kind": "reconciliation_required"})
            if integrity["generation_key"] is None:
                boundary_reasons.append({"kind": "artifact_generation_missing"})
            state = "waiting" if boundary_reasons else "ready"
        projected.append({"node": "baseline-candidate", "state": state, "reasons": boundary_reasons,
            "contributions": [], "verifies": [{"kind": "outcome", "name": item["outcome"]} for item in graph.data()["outcomes"]],
            "artifact_generation": integrity["generation_key"]})
    projected.append({"node": "validate-candidate", "state": validation_state,
                      "reasons": reasons, "contributions": [],
                      "verifies": [{"kind": "outcome", "name": item["outcome"]} for item in graph.data()["outcomes"]],
                      "artifact_generation": integrity["generation_key"]})
    projected.extend(generated)
    for item in projected:
        if item["state"] == "ready" and any(reason["kind"] == "artifact_generation_stale" for reason in item["reasons"]):
            attempts = sum(item["node"] in json.loads(row[0]) for row in connection.execute(
                "SELECT nodes_json FROM execution_assignments WHERE graph_id=?", (graph_id,)))
            if attempts >= 1 + graph.data()["budgets"]["additional_evidence"]:
                item["state"] = "exhausted"
                item["reasons"].append({"kind": "additional_evidence_budget_exhausted"})
    return _project_ownership(connection, graph_id, projected, native_observation=native_observation)


def claim_nodes(connection: sqlite3.Connection, *, graph_id: str, task_id: str,
                expected_digest: str, node_keys: Sequence[str], assignment_id: str,
                protected_task_name: str, native_observation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _transaction(connection)
    record, graph = _graph(connection, graph_id)
    if record["task_id"] != task_id or record["revision"] != _current_revision(connection, task_id) or graph.digest != expected_digest:
        raise GraphError("graph_revision_stale")
    if not node_keys or len(set(node_keys)) != len(node_keys):
        raise GraphError("assignment_nodes_invalid")
    projection = {row["node"]: row for row in state_projection(connection, graph_id, native_observation=native_observation)}
    if any(key not in projection or projection[key]["state"] != "ready" for key in node_keys):
        raise GraphError("assignment_not_ready")
    definitions = {row["node_key"]: json.loads(row["content_json"]) for row in connection.execute(
        "SELECT node_key,content_json FROM execution_nodes WHERE graph_id=?", (graph_id,))}
    nodes = [definitions[key] for key in node_keys]
    reconciliation = _reconciliation(connection, graph_id)
    if reconciliation is not None and reconciliation["node"] in node_keys:
        if len(node_keys) != 1:
            raise GraphError("reconciliation_assignment_must_be_separate")
        native = native_quiescence(connection, task_id=task_id, observation=native_observation)
        if not native["ready"]:
            raise GraphError("native_quiescence_required")
        for revoked in native["confirmed"]:
            connection.execute("UPDATE execution_assignments SET quiescent=1 WHERE assignment_id=? AND state IN ('stale','snapshot_conflict','lost')", (revoked,))
        _event(connection, graph_id, "native_quiescence_confirmed", {
            "barrier_epoch": native["barrier_epoch"], "assignments": native["confirmed"]})
    if "validate-candidate" in node_keys and (len(node_keys) != 1 or assignment_id == record["planner_assignment_id"]):
        raise GraphError("graph_validation_not_independent")
    # The project shard represents exactly one canonical root, including
    # assignments from other tasks. Stale nonquiescent workers still own the
    # physical mutation hazard even though they no longer own publication.
    active_modes = [row[0] for row in connection.execute(
        "SELECT mode FROM execution_assignments WHERE state='active' OR (state IN ('stale','snapshot_conflict','lost') AND quiescent=0)")]
    assignment_compatible(nodes, active_modes)
    generations = {projection[key]["artifact_generation"] for key in node_keys}
    if len(generations) != 1:
        raise GraphError("assignment_generation_conflict")
    target = next(iter(generations))
    kind, mode = publication_kind(nodes[0]), nodes[0]["execution_mode"]
    replaced_owners = {row[0] for key in node_keys for row in connection.execute(
        "SELECT n.assignment_id FROM execution_nodes n JOIN execution_assignments a ON a.assignment_id=n.assignment_id WHERE n.graph_id=? AND n.node_key=? AND a.state='lost' AND a.quiescent=1", (graph_id, key))}
    connection.execute("INSERT INTO execution_assignments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (assignment_id, graph_id, task_id, record["revision"], _json(list(node_keys)), mode, kind, target, protected_task_name, "active", 0))
    for key in node_keys:
        updated = connection.execute("UPDATE execution_nodes SET state='active',assignment_id=?,artifact_generation=? WHERE graph_id=? AND node_key=? AND state!='active' AND (assignment_id IS NULL OR assignment_id IN (SELECT assignment_id FROM execution_assignments WHERE state='published' OR (state='lost' AND quiescent=1)))",
                                     (assignment_id, target, graph_id, key))
        if updated.rowcount != 1:
            raise GraphError("node_ownership_conflict", node=key)
    _event(connection, graph_id, "nodes_claimed", {"nodes": list(node_keys), "assignment": assignment_id})
    predecessor_keys = sorted({edge["node"] for node in nodes for edge in node["dependencies"]})
    reports = []
    if reconciliation is not None and reconciliation["node"] in node_keys:
        for row in connection.execute("SELECT details_json FROM execution_events WHERE graph_id=? AND event='reconciliation_retry'", (graph_id,)):
            retry = json.loads(row[0])
            if retry["node"] == reconciliation["node"]:
                reports.append(retry["predecessor_report"])
    if replaced_owners:
        for row in connection.execute("SELECT graph_id,details_json FROM execution_events WHERE event='loss_reconciliation'"):
            recovery = json.loads(row[1])
            if recovery["source_assignment"] in replaced_owners:
                evidence = connection.execute("SELECT p.report_id FROM execution_nodes n JOIN execution_publications p ON p.assignment_id=n.assignment_id WHERE n.graph_id=? AND n.node_key=? AND n.state='complete'", (row[0], recovery["node"])).fetchone()
                if evidence is not None:
                    reports.append(evidence[0])
    for key in predecessor_keys:
        row = connection.execute("SELECT p.report_id FROM execution_nodes n JOIN execution_publications p ON p.assignment_id=n.assignment_id WHERE n.graph_id=? AND n.node_key=?", (graph_id, key)).fetchone()
        if row is not None:
            reports.append(row[0])
    if "validate-candidate" in node_keys:
        reports.append(record["plan_report_id"])
        boundary = connection.execute("SELECT p.report_id FROM execution_nodes n JOIN execution_publications p ON p.assignment_id=n.assignment_id WHERE n.graph_id=? AND n.node_key='baseline-candidate' AND n.state='complete'", (graph_id,)).fetchone()
        if boundary is not None:
            reports.append(boundary[0])
    elif "baseline-candidate" in node_keys:
        reports.append(record["plan_report_id"])
    if any(node["kind"] == "planning" for node in nodes):
        rejected = connection.execute("SELECT graph_id,plan_report_id FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind='candidate' ORDER BY rowid DESC LIMIT 1", (task_id, record["revision"])).fetchone()
        if rejected is not None:
            reports.append(rejected["plan_report_id"])
            reports.extend(row[0] for row in connection.execute("SELECT p.report_id FROM execution_nodes n JOIN execution_publications p ON p.assignment_id=n.assignment_id WHERE n.graph_id=? AND n.node_key='validate-candidate'", (rejected["graph_id"],)))
    from cortex_runtime.remediation import chains, reviews, strategy_reviews
    for chain in chains(connection, graph_id):
        if set(node_keys).intersection({chain["repair"], chain["regression"]}):
            reports.append(chain["source_report"])
            if chain.get("original_report"):
                reports.append(chain["original_report"])
            if chain["classification_report"]:
                reports.append(chain["classification_report"])
            reports.extend(chain.get("strategy_reports", []))
            if chain["regression"] in node_keys:
                attempted = connection.execute("SELECT p.report_id FROM execution_nodes n JOIN execution_publications p ON p.assignment_id=n.assignment_id WHERE n.graph_id=? AND n.node_key=?", (graph_id, chain["repair"])).fetchone()
                if attempted is not None:
                    reports.append(attempted[0])
    for review in reviews(connection, graph_id):
        if review["node"] in node_keys:
            reports.append(review["source_report"])
            if review["predecessor_report"]:
                reports.append(review["predecessor_report"])
    for review in strategy_reviews(connection, graph_id):
        if review["node"] in node_keys:
            reports.append(review["source_report"])
            reports.append(review["chain"].get("original_report", review["chain"]["source_report"]))
            if review["diagnostic_report"]:
                reports.append(review["diagnostic_report"])
    target_method = connection.execute("SELECT method,paths_json FROM artifact_generations WHERE generation_key=?", (target,)).fetchone()
    return {"nodes": nodes, "terminal_kind": kind, "artifact_generation": target,
            "predecessor_reports": sorted(set(reports)), "fingerprint_method": target_method[0] if target_method else None,
            "artifact_paths": json.loads(target_method["paths_json"]) if target_method else graph.data()["artifact_paths"]}


def _observation(value: Any, *, mutating: bool, reconciliation: bool = False, boundary: bool = False) -> dict[str, Any]:
    if (not isinstance(value, Mapping) or not {"method", "start", "end", "changes"}.issubset(value)
            or set(value) - {"method", "start", "end", "changes", "baseline_changes", "boundary"}
            or ("baseline_changes" in value and not reconciliation)
            or ("boundary" in value) != boundary):
        raise GraphError("artifact_observation_invalid")
    if value["method"] not in {"git_content_v1", "path_manifest_v1"}:
        raise GraphError("fingerprint_method_invalid")
    for key in ("start", "end"):
        text = value[key]
        if not isinstance(text, str) or len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
            raise GraphError("fingerprint_invalid")
    _change_commitment(value["changes"])
    if "baseline_changes" in value:
        _change_commitment(value["baseline_changes"])
    if boundary:
        _observation(value["boundary"], mutating=False)
    return dict(value)


def _change_commitment(changes: Any) -> None:
    if not isinstance(changes, Mapping) or set(changes) != {"count", "digest", "samples", "within_domains"}:
        raise GraphError("change_commitment_invalid")
    if type(changes["count"]) is not int or not 0 <= changes["count"] <= 100_000 or type(changes["within_domains"]) is not bool:
        raise GraphError("change_commitment_invalid")
    digest = changes["digest"]
    if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise GraphError("change_commitment_invalid")
    if not isinstance(changes["samples"], list) or len(changes["samples"]) > min(changes["count"], 16) or any(not isinstance(path, str) or not path or len(path) > 2048 for path in changes["samples"]):
        raise GraphError("change_commitment_invalid")


def assignment_scope(connection: sqlite3.Connection, assignment_id: str) -> dict[str, Any]:
    """Render immutable node intent and its original target, not current state."""
    row = connection.execute("SELECT * FROM execution_assignments WHERE assignment_id=?", (assignment_id,)).fetchone()
    if row is None:
        raise GraphError("assignment_missing")
    record, graph = _graph(connection, row["graph_id"])
    nodes = [json.loads(connection.execute(
        "SELECT content_json FROM execution_nodes WHERE graph_id=? AND node_key=?",
        (row["graph_id"], key)).fetchone()[0]) for key in json.loads(row["nodes_json"])]
    target = connection.execute("SELECT method,fingerprint,paths_json FROM artifact_generations WHERE generation_key=?",
        (row["target_generation"],)).fetchone()
    reconciliation = _reconciliation(connection, row["graph_id"], node=nodes[0]["key"] if len(nodes) == 1 else "")
    boundary = ([node["key"] for node in nodes] == ["baseline-candidate"] or
        ([node["key"] for node in nodes] == ["baseline"] and target is not None
         and json.loads(target["paths_json"]) != sorted(set(graph.data()["artifact_paths"]))))
    candidate_evidence = {}
    if any(node["kind"] == "graph_validation" for node in nodes):
        from cortex_runtime.candidate_family import read_family
        family = read_family(connection, row["graph_id"])
        candidate_evidence = {"candidate_family": family.data()} if family else {"candidate_graph": graph.data()}
    return {"nodes": nodes, "terminal_kind": row["terminal_kind"], "execution_mode": row["mode"],
            "responsibility": nodes[0]["responsibility"],
            "artifact": {"method": target["method"] if target else None,
                         "target_fingerprint": target["fingerprint"] if target else None,
                         "paths": json.loads(target["paths_json"]) if target else graph.data()["artifact_paths"],
                         "worker_procedure": "cortex_runtime.artifact_fingerprint.observe",
                         "background_mutators_permitted": False,
                         "reconciliation": reconciliation is not None,
                         **({"boundary_target": {"method": target["method"] if nodes[0]["key"] == "baseline" else graph.data()["fingerprint_method"], "paths": graph.data()["artifact_paths"]}}
                            if boundary else {})},
            **candidate_evidence}


def continuations(connection: sqlite3.Connection, task_id: str, *,
                  native_observation: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Unfinished native routes, including revoked but not quiescent workers."""
    confirmed = set(native_quiescence(connection, task_id=task_id, observation=native_observation)["confirmed"])
    return [{"nodes": json.loads(row["nodes_json"]), "task_name": row["protected_task_name"],
             "state": row["state"], "quiescent": bool(row["quiescent"]) or row["assignment_id"] in confirmed} for row in connection.execute(
        "SELECT assignment_id,nodes_json,protected_task_name,state,quiescent FROM execution_assignments "
        "WHERE task_id=? AND (state='active' OR (state IN ('stale','snapshot_conflict','lost') AND quiescent=0)) "
        "ORDER BY rowid", (task_id,))]


def native_quiescence(connection: sqlite3.Connection, *, task_id: str,
                      observation: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project reconciliation hazards without changing lifecycle or inferring loss.

    The caller verifies the private hook signature before supplying an
    observation. Revision/epoch checks remain local so a newer steering or
    snapshot conflict cannot reuse a previously admissible native projection.
    A different task's root tree can never prove absence of our workers.
    """
    from cortex_runtime.native_observation import quiescent
    integrity = connection.execute("SELECT barrier_epoch FROM project_integrity WHERE singleton=1").fetchone()
    bound = (isinstance(observation, Mapping)
             and observation.get("revision") == _current_revision(connection, task_id)
             and observation.get("barrier_epoch") == integrity[0])
    confirmed, waiting = [], []
    rows = connection.execute(
        "SELECT assignment_id,task_id,state,mode,protected_task_name,nodes_json FROM execution_assignments "
        "WHERE quiescent=0 AND state IN ('active','stale','snapshot_conflict','lost') ORDER BY rowid")
    for row in rows:
        # Live artifact-independent research does not own the project, but
        # steering revokes all old scopes and still requires their quiescence.
        if row["state"] == "active" and row["mode"] == "artifact_independent":
            continue
        if row["state"] == "active":
            reason = "active_assignment"
        elif row["task_id"] != task_id:
            reason = "foreign_task_native_evidence_required"
        elif not bound:
            reason = "native_observation_required"
        elif not quiescent(observation, row["protected_task_name"]):
            reason = "native_worker_present"
        else:
            confirmed.append(row["assignment_id"])
            continue
        waiting.append({"nodes": json.loads(row["nodes_json"]),
            "reason": reason})
    return {"ready": not waiting, "confirmed": confirmed, "waiting": waiting,
            "barrier_epoch": integrity[0]}


def recoverable_owner(connection, *, graph_id, node_keys, observation):
    """Resolve exactly one unpublished owner against fresh signed host facts."""
    from datetime import datetime
    from cortex_runtime.native_observation import quiescent
    record, graph = _graph(connection, graph_id)
    epoch = connection.execute("SELECT barrier_epoch FROM project_integrity").fetchone()[0]
    if record["graph_kind"] != "bootstrap":
        latest = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind!='bootstrap' ORDER BY rowid DESC LIMIT 1", (record["task_id"], record["revision"])).fetchone()
        if latest is None or latest[0] != graph_id:
            raise GraphError("graph_revision_stale")
    if (not isinstance(observation, Mapping) or observation.get("revision") != record["revision"]
            or record["revision"] != _current_revision(connection, record["task_id"])
            or observation.get("barrier_epoch") != epoch):
        raise GraphError("native_loss_evidence_required")
    rows = [connection.execute("SELECT assignment_id FROM execution_nodes WHERE graph_id=? AND node_key=?", (graph_id, key)).fetchone() for key in node_keys]
    owners = {row[0] for row in rows if row is not None}
    if not rows or any(row is None for row in rows) or len(owners) != 1 or None in owners:
        raise GraphError("recovery_scope_invalid")
    owner = connection.execute("SELECT * FROM execution_assignments WHERE assignment_id=?", (next(iter(owners)),)).fetchone()
    if owner is None or owner["state"] != "active" or set(json.loads(owner["nodes_json"])) != set(node_keys):
        raise GraphError("recovery_scope_invalid")
    if connection.execute("SELECT 1 FROM execution_publications WHERE assignment_id=?", (owner["assignment_id"],)).fetchone():
        raise GraphError("recovery_publication_exists")
    if not quiescent(observation, owner["protected_task_name"]):
        raise GraphError("native_worker_present")
    created = connection.execute("SELECT created_at FROM delegations WHERE delegation_id=?", (owner["assignment_id"],)).fetchone()
    if created is None or type(observation.get("observed_at")) is not int or observation["observed_at"] <= int(datetime.fromisoformat(created[0].replace("Z", "+00:00")).timestamp() * 1_000_000_000):
        raise GraphError("native_loss_evidence_predates_assignment")
    attempts = [json.loads(row[0]) for row in connection.execute("SELECT details_json FROM execution_events WHERE graph_id=? AND event='native_loss_confirmed'", (graph_id,))]
    return {**dict(owner), "budget_exhausted":
        sum(set(item["nodes"]) == set(node_keys) for item in attempts) >= graph.data()["budgets"]["recovery"]}


def begin_loss_reconciliation(connection, *, graph_id, node_keys, observation):
    """Revoke exact lost routes together before one reconciliation prerequisite."""
    _transaction(connection)
    selected = {}
    for key in node_keys:
        row = connection.execute("SELECT assignment_id FROM execution_nodes WHERE graph_id=? AND node_key=?", (graph_id, key)).fetchone()
        if row is None or row[0] is None:
            raise GraphError("recovery_scope_invalid")
        selected.setdefault(row[0], []).append(key)
    owners = [recoverable_owner(connection, graph_id=graph_id, node_keys=keys, observation=observation)
        for keys in selected.values()]
    if not owners:
        raise GraphError("recovery_scope_invalid")
    for owner in owners:
        connection.execute("UPDATE execution_assignments SET state='lost',quiescent=1 WHERE assignment_id=?", (owner["assignment_id"],))
        connection.execute("UPDATE execution_nodes SET state=? WHERE assignment_id=?",
                           ("exhausted" if owner["budget_exhausted"] else "blocked", owner["assignment_id"]))
        _event(connection, graph_id, "native_loss_confirmed", {"assignment": owner["assignment_id"], "nodes": selected[owner["assignment_id"]], "observation": observation})
        if owner["budget_exhausted"]:
            _event(connection, graph_id, "recovery_exhausted", {"assignment": owner["assignment_id"], "nodes": selected[owner["assignment_id"]], "reason": "recovery_budget"})
    if all(owner["budget_exhausted"] for owner in owners):
        connection.execute("UPDATE project_integrity SET reconciliation_required=1,barrier_epoch=barrier_epoch+1 WHERE singleton=1")
        return graph_id, None, [owner["assignment_id"] for owner in owners]
    _raise_barrier(connection, owners[0]["task_id"])
    bootstrap = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind='bootstrap'", (owners[0]["task_id"], owners[0]["revision"])).fetchone()[0]
    reconciliation = _reconciliation(connection, bootstrap)
    epoch = connection.execute("SELECT barrier_epoch FROM project_integrity WHERE singleton=1").fetchone()[0]
    if reconciliation is None or reconciliation["barrier_epoch"] != epoch:
        for owner in owners:
            connection.execute("UPDATE execution_nodes SET state='exhausted' WHERE assignment_id=?", (owner["assignment_id"],))
            _event(connection, graph_id, "recovery_exhausted", {"assignment": owner["assignment_id"], "nodes": selected[owner["assignment_id"]], "reason": "reconciliation_budget"})
        return graph_id, None, [owner["assignment_id"] for owner in owners]
    for owner in owners:
        if owner["budget_exhausted"]:
            continue
        _event(connection, bootstrap, "loss_reconciliation", {"node": reconciliation["node"], "source_graph": graph_id, "source_assignment": owner["assignment_id"], "source_nodes": selected[owner["assignment_id"]]})
    return bootstrap, reconciliation["node"], [owner["assignment_id"] for owner in owners]


def _release_recovered_nodes(connection, *, graph_id, reconciliation_node):
    for row in connection.execute("SELECT details_json FROM execution_events WHERE graph_id=? AND event='loss_reconciliation'", (graph_id,)):
        link = json.loads(row[0])
        if link["node"] != reconciliation_node:
            continue
        for key in link["source_nodes"]:
            # Reconciliation itself replaces a lost initial baseline; rerunning
            # that same observation would create redundant evidence. Other
            # lost work becomes eligible only after this baseline is sealed.
            state = "resolved" if key == "baseline" and link["source_graph"] == graph_id else "waiting"
            connection.execute("UPDATE execution_nodes SET state=? WHERE graph_id=? AND node_key=? AND assignment_id=? AND state='blocked'", (state, link["source_graph"], key, link["source_assignment"]))
        _event(connection, link["source_graph"], "recovery_baseline_sealed", {"assignment": link["source_assignment"], "nodes": link["source_nodes"]})


def task_projection(connection: sqlite3.Connection, task_id: str, *,
                    native_observation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Current execution facts from the typed graph, never legacy ownership."""
    revision = _current_revision(connection, task_id)
    integrity = connection.execute("SELECT generation_key,reconciliation_required,barrier_epoch FROM project_integrity WHERE singleton=1").fetchone()
    candidate = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind!='bootstrap' ORDER BY rowid DESC LIMIT 1",
        (task_id, revision)).fetchone()
    names = [row[0] for row in connection.execute("SELECT text FROM effective_contract_items WHERE task_id=? AND (retired_revision IS NULL OR retired_revision>?) ORDER BY ordinal",
        (task_id, revision))]
    outcomes = [{"outcome": name, "status": "unverified"} for name in names]
    nodes = []
    if candidate is not None:
        record, graph = _graph(connection, candidate[0])
        nodes = state_projection(connection, candidate[0], native_observation=native_observation)
        states = {node["node"]: node["state"] for node in nodes}
        definitions = graph.data()["nodes"]
        review_required, approved = _review_state(connection, record)
        pending_family = connection.execute("SELECT 1 FROM plan_candidate_families WHERE graph_id=?", (candidate[0],)).fetchone() is not None
        by_name = {}
        for expression in graph.data()["outcomes"]:
            related = [node["key"] for node in definitions if set(node["contributions"]) & set(expression["all_of"])
                or {"kind": "outcome", "name": expression["outcome"]} in node["verifies"]
                or any(subject["kind"] == "contribution" and subject["name"] in expression["all_of"] for subject in node["verifies"])]
            acceptable = (not pending_family and record["activation"] == "active" and (not review_required or approved)
                and not integrity["reconciliation_required"] and all(states[key] in {"complete", "resolved"} for key in related))
            observed = {states[key] for key in related}
            by_name[expression["outcome"]] = "complete" if acceptable else "failed" if observed & {"failed", "exhausted"} else "partial" if observed & {"complete", "partial", "resolved"} else "unverified"
        outcomes = [{"outcome": name, "status": by_name.get(name, "unverified")} for name in names]
    for row in connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind='bootstrap'", (task_id, revision)):
        nodes.extend(state_projection(connection, row[0], native_observation=native_observation))
    return {"revision": revision, "outcomes": outcomes, "nodes": nodes,
            "reconciliation_required": bool(integrity["reconciliation_required"]),
            "barrier_epoch": integrity["barrier_epoch"],
            "generation_present": integrity["generation_key"] is not None,
            "unfinished": continuations(connection, task_id, native_observation=native_observation)}


def closure_evidence(connection: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    """Reconcile current required nodes and evidence, never a caller verdict."""
    state = task_projection(connection, task_id)
    candidate = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind!='bootstrap' ORDER BY rowid DESC LIMIT 1",
        (task_id, state["revision"])).fetchone()
    reasons, risks = [], []
    if candidate is None:
        reasons.append({"kind": "execution_graph_missing"})
        nodes = []
    else:
        nodes = state_projection(connection, candidate[0])
        for node in nodes:
            if node["state"] not in {"complete", "resolved", "skipped"}:
                reasons.append({"kind": "required_node_incomplete", "node": node["node"], "state": node["state"]})
        dispositions = {node["node"]: node["state"] for node in nodes}
        for row in connection.execute("SELECT n.node_key,p.payload_json FROM execution_nodes n JOIN execution_publications p ON p.assignment_id=n.assignment_id WHERE n.graph_id=?", (candidate[0],)):
            if dispositions[row[0]] == "resolved":
                continue
            content = json.loads(row[1]).get("report_content")
            if isinstance(content, dict):
                risks.extend(content.get("risks", []))
                if content.get("unresolved"):
                    reasons.append({"kind": "unresolved_current_evidence", "node": row[0]})
                if not content.get("documentation_impact"):
                    reasons.append({"kind": "documentation_impact_missing", "node": row[0]})
            else:
                reasons.append({"kind": "publication_content_missing", "node": row[0]})
    if state["reconciliation_required"]:
        reasons.append({"kind": "project_reconciliation_required"})
    if state["unfinished"]:
        reasons.append({"kind": "unfinished_native_routes"})
    if any(item["status"] != "complete" for item in state["outcomes"]):
        reasons.append({"kind": "outcome_expression_incomplete"})
    return {"revision": state["revision"], "ready": not reasons, "reasons": reasons,
            "outcomes": state["outcomes"], "nodes": nodes, "risks": sorted(set(risks))}


def plan_review_snapshot(connection: sqlite3.Connection, task_id: str, report_id: str) -> str:
    """Freeze the authority shown in a plan packet, independently of prose."""
    revision = _current_revision(connection, task_id)
    candidate = connection.execute(
        "SELECT graph_id,content_digest,activation FROM execution_graphs "
        "WHERE task_id=? AND revision=? AND plan_report_id=?",
        (task_id, revision, report_id)).fetchone()
    latest = connection.execute(
        "SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? "
        "AND graph_kind='candidate' ORDER BY rowid DESC LIMIT 1", (task_id, revision)).fetchone()
    validation = None if candidate is None else connection.execute(
        "SELECT state,artifact_generation,facts_json FROM execution_nodes "
        "WHERE graph_id=? AND node_key='validate-candidate'", (candidate[0],)).fetchone()
    integrity = connection.execute("SELECT generation_key,reconciliation_required,barrier_epoch FROM project_integrity").fetchone()
    assessment = connection.execute("SELECT MAX(created_sequence) FROM governance_assessments WHERE task_id=?", (task_id,)).fetchone()[0]
    return _digest({"revision": revision, "candidate": None if candidate is None else tuple(candidate),
        "latest": None if latest is None else latest[0], "assessment": assessment,
        "validation": None if validation is None else tuple(validation), "artifact": tuple(integrity)})


def closure_snapshot(connection: sqlite3.Connection, task_id: str) -> str:
    """Bind review to graph facts, artifact epoch and publications, not prose."""
    state = task_projection(connection, task_id)
    graphs = [tuple(row) for row in connection.execute("SELECT graph_id,content_digest,activation,approved FROM execution_graphs WHERE task_id=? AND revision=? ORDER BY graph_id", (task_id, state["revision"]))]
    publications = [tuple(row) for row in connection.execute("SELECT p.assignment_id,p.report_id,p.payload_digest,p.artifact_generation FROM execution_publications p JOIN execution_assignments a ON a.assignment_id=p.assignment_id WHERE a.task_id=? ORDER BY p.report_id", (task_id,))]
    assessment = connection.execute("SELECT mode,risk_factors_json,created_sequence FROM governance_assessments WHERE task_id=? ORDER BY created_sequence DESC LIMIT 1", (task_id,)).fetchone()
    return _digest({"state": state, "graphs": graphs, "publications": publications,
        "assessment": tuple(assessment) if assessment is not None else None})


def _seal(connection: sqlite3.Connection, *, task_id: str, revision: int,
          observation: Mapping[str, Any], source_assignment_id: str | None,
          artifact_paths: Sequence[str] | None = None) -> str:
    parent = connection.execute("SELECT generation_key FROM project_integrity WHERE singleton=1").fetchone()[0]
    if artifact_paths is None:
        if parent is not None:
            artifact_paths = json.loads(connection.execute("SELECT paths_json FROM artifact_generations WHERE generation_key=?", (parent,)).fetchone()[0])
        else:
            owner = connection.execute("SELECT graph_id FROM execution_assignments WHERE assignment_id=?", (source_assignment_id,)).fetchone()
            if owner is None:
                owner = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? ORDER BY rowid DESC LIMIT 1", (task_id, revision)).fetchone()
            artifact_paths = _graph(connection, owner[0])[1].data()["artifact_paths"] if owner else ["."]
    paths = sorted(set(artifact_paths))
    ordinal = int(connection.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM artifact_generations").fetchone()[0])
    generation = _digest([task_id, revision, source_assignment_id, parent, ordinal, observation, paths])
    connection.execute("INSERT INTO artifact_generations VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (ordinal, generation, task_id, revision, observation["method"], observation["end"], parent,
                        source_assignment_id, _json(observation), _json(paths)))
    connection.execute("UPDATE project_integrity SET generation_key=? WHERE singleton=1", (generation,))
    return generation


def _reconciliation(connection: sqlite3.Connection, graph_id: str, *, node: str | None = None) -> dict[str, Any] | None:
    for row in connection.execute("SELECT details_json FROM execution_events WHERE graph_id=? AND event='reconciliation_created' ORDER BY sequence DESC", (graph_id,)):
        value = json.loads(row[0])
        if node is None or value["node"] == node:
            return value
    return None


def _raise_barrier(connection: sqlite3.Connection, task_id: str, *, force_new: bool = False) -> None:
    """Generate bounded reconciliation intent, never dispatch or execute it."""
    _transaction(connection)
    revision = _current_revision(connection, task_id)
    names = [row[0] for row in connection.execute("SELECT text FROM effective_contract_items WHERE task_id=? AND (retired_revision IS NULL OR retired_revision>?) ORDER BY ordinal", (task_id, revision))]
    graph_id = ensure_bootstrap(connection, task_id=task_id, outcomes=names)
    integrity = connection.execute("SELECT generation_key,barrier_epoch,reconciliation_required FROM project_integrity WHERE singleton=1").fetchone()
    previous = _reconciliation(connection, graph_id)
    if not force_new and integrity["reconciliation_required"] and previous is not None and previous["barrier_epoch"] == integrity["barrier_epoch"]:
        pending = connection.execute("SELECT state FROM execution_nodes WHERE graph_id=? AND node_key=?", (graph_id, previous["node"])).fetchone()
        if pending[0] == "waiting":
            # Concurrent audits observing the same invalidated generation add
            # hazards to one barrier; they do not spend one recovery attempt
            # per auditor before reconciliation has even been dispatched.
            return
    connection.execute("UPDATE project_integrity SET reconciliation_required=1,barrier_epoch=barrier_epoch+1 WHERE singleton=1")
    integrity = connection.execute("SELECT generation_key,barrier_epoch FROM project_integrity WHERE singleton=1").fetchone()
    graph = _graph(connection, graph_id)[1].data()
    attempts = connection.execute("SELECT COUNT(*) FROM execution_events WHERE graph_id=? AND event='reconciliation_created'", (graph_id,)).fetchone()[0]
    if attempts >= graph["budgets"]["reconciliation"]:
        _event(connection, graph_id, "reconciliation_exhausted", {"barrier_epoch": integrity["barrier_epoch"]})
        return
    connection.execute("UPDATE execution_nodes SET state='stale' WHERE graph_id=? AND state='waiting'", (graph_id,))
    key = f"reconcile-{integrity['barrier_epoch']}"
    node = _bootstrap_node(key, "discovery", names,
        "Observe the current project after revoked workers are quiescent. Compare the saved sealed baseline manifest "
        "with the current project and record pre-existing changes without claiming authorship or reversal. "
        "Observe again before publication to prove this read-only inspection remained stable.",
        checks=[{"key": "reconciliation", "description": "Verify the saved predecessor commitment, record the complete changed-path commitment, and prove matching current start/end observations.", "required": True}])
    connection.execute("INSERT INTO execution_nodes(graph_id,node_key,content_json,state) VALUES (?,?,?,'waiting')", (graph_id, key, _json(node)))
    _event(connection, graph_id, "reconciliation_created", {"node": key, "barrier_epoch": integrity["barrier_epoch"], "predecessor_generation": integrity["generation_key"]})


def _after_reconciliation(connection, *, assignment, node, report_id, facts, artifact):
    """Append bounded follow-up evidence; never reopen the failed publication."""
    graph_id = assignment["graph_id"]
    row = connection.execute("SELECT state FROM execution_nodes WHERE graph_id=? AND node_key=?", (graph_id, node)).fetchone()
    if row[0] == "complete":
        return
    progress = _digest([assignment["revision"], assignment["target_generation"], artifact,
                       sorted((fact["subject"]["kind"], fact["subject"]["name"], fact["check_key"],
                               fact["state"], fact.get("classification")) for fact in facts)])
    previous = [json.loads(row[0]) for row in connection.execute(
        "SELECT details_json FROM execution_events WHERE graph_id=? AND event='reconciliation_retry'", (graph_id,))]
    attempts = connection.execute("SELECT COUNT(*) FROM execution_events WHERE graph_id=? AND event='reconciliation_created'", (graph_id,)).fetchone()[0]
    exhausted = "non_progress" if any(item["progress"] == progress for item in previous) else (
        "reconciliation_budget" if attempts >= _graph(connection, graph_id)[1].data()["budgets"]["reconciliation"] else None)
    if exhausted:
        _event(connection, graph_id, "reconciliation_exhausted", {
            "node": node, "report": report_id, "reason": exhausted})
        return
    _raise_barrier(connection, assignment["task_id"], force_new=True)
    successor = _reconciliation(connection, graph_id)
    if successor is None or successor["node"] == node:
        return
    _event(connection, graph_id, "reconciliation_retry", {
        "node": successor["node"], "predecessor_node": node,
        "predecessor_report": report_id, "progress": progress})
    links = [json.loads(row[0]) for row in connection.execute(
        "SELECT details_json FROM execution_events WHERE graph_id=? AND event='loss_reconciliation'", (graph_id,))]
    for link in links:
        if link["node"] == node:
            _event(connection, graph_id, "loss_reconciliation", {**link, "node": successor["node"]})


def publish_nodes(connection: sqlite3.Connection, *, assignment_id: str, report_id: str,
                  terminal_kind: str, node_coverage: Sequence[Mapping[str, Any]],
                  artifact: Mapping[str, Any] | None, report_content: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _transaction(connection)
    row = connection.execute("SELECT * FROM execution_assignments WHERE assignment_id=?", (assignment_id,)).fetchone()
    if row is None:
        raise GraphError("assignment_missing")
    assignment = dict(row)
    if assignment["state"] == "stale" or assignment["revision"] != _current_revision(connection, assignment["task_id"]):
        return {"state": "superseded", "published": False, "replayed": False}
    if assignment["state"] == "snapshot_conflict":
        return {"state": "snapshot_conflict", "published": False, "replayed": False}
    if terminal_kind != assignment["terminal_kind"]:
        raise GraphError("publication_kind_not_permitted")
    payload = {"terminal_kind": terminal_kind, "node_coverage": node_coverage, "artifact": artifact,
               "report_content": report_content}
    digest = _digest(payload)
    prior = connection.execute("SELECT payload_digest,report_id FROM execution_publications WHERE assignment_id=?", (assignment_id,)).fetchone()
    if prior is not None:
        if prior["payload_digest"] != digest:
            raise GraphError("report_operation_conflict")
        return {"state": "published", "published": True, "replayed": True, "report_id": prior["report_id"]}
    if assignment["state"] != "active":
        raise GraphError("assignment_not_active")
    keys = json.loads(assignment["nodes_json"])
    coverage = {}
    for entry in node_coverage:
        if not isinstance(entry, Mapping) or set(entry) != {"node", "coverage"} or entry["node"] in coverage:
            raise GraphError("node_coverage_invalid")
        coverage[entry["node"]] = entry["coverage"]
    if set(coverage) != set(keys):
        raise GraphError("node_coverage_invalid")
    facts_by_node = {}
    for key in keys:
        node = json.loads(connection.execute("SELECT content_json FROM execution_nodes WHERE graph_id=? AND node_key=?", (assignment["graph_id"], key)).fetchone()[0])
        facts_by_node[key] = validate_coverage(node, coverage[key])
    generation = assignment["target_generation"]
    if assignment["mode"] == "artifact_independent":
        if artifact is not None:
            raise GraphError("independent_artifact_claim")
    else:
        reconciliation = _reconciliation(connection, assignment["graph_id"], node=keys[0] if len(keys) == 1 else "")
        reconciling = reconciliation is not None and keys == [reconciliation["node"]]
        target_boundary = connection.execute("SELECT paths_json FROM artifact_generations WHERE generation_key=?", (generation,)).fetchone()
        declared = _graph(connection, assignment["graph_id"])[1].data()
        rebaselining = keys == ["baseline-candidate"] or (keys == ["baseline"] and target_boundary is not None
            and json.loads(target_boundary[0]) != sorted(set(declared["artifact_paths"])))
        observation = _observation(artifact, mutating=assignment["mode"] == "mutating", reconciliation=reconciling, boundary=rebaselining)
        target = connection.execute("SELECT * FROM artifact_generations WHERE generation_key=?", (generation,)).fetchone()
        integrity = connection.execute("SELECT generation_key,reconciliation_required,barrier_epoch FROM project_integrity WHERE singleton=1").fetchone()
        if reconciling:
            if not integrity["reconciliation_required"] or integrity["barrier_epoch"] != reconciliation["barrier_epoch"]:
                return {"state": "superseded", "published": False, "replayed": False}
            if (target is not None) != ("baseline_changes" in observation):
                raise GraphError("reconciliation_baseline_commitment_required")
            if target is not None and target["method"] != observation["method"]:
                raise GraphError("artifact_target_invalid")
            if target is not None and bool(observation["baseline_changes"]["count"]) != (target["fingerprint"] != observation["start"]):
                raise GraphError("reconciliation_baseline_commitment_conflict")
            stable = (integrity[0] == generation and observation["start"] == observation["end"]
                and observation["changes"]["count"] == 0 and observation["changes"]["within_domains"])
            complete = (all(item["status"] == "complete" for items in coverage.values() for item in items)
                and (report_content is None or (report_content.get("status") == "completed" and not report_content.get("unresolved"))))
            if not stable:
                connection.execute("UPDATE execution_assignments SET state='snapshot_conflict' WHERE assignment_id=?", (assignment_id,))
                connection.execute("UPDATE execution_nodes SET state='blocked' WHERE assignment_id=?", (assignment_id,))
                _raise_barrier(connection, assignment["task_id"])
                return {"state": "snapshot_conflict", "published": False, "replayed": False}
            if complete:
                generation = _seal(connection, task_id=assignment["task_id"], revision=assignment["revision"], observation=observation, source_assignment_id=assignment_id)
                connection.execute("UPDATE project_integrity SET reconciliation_required=0 WHERE singleton=1")
                _release_recovered_nodes(connection, graph_id=assignment["graph_id"], reconciliation_node=keys[0])
            # Ordinary target-match logic below must not mistake pre-existing
            # external changes for mutations by this read-only reconciliation.
            target = {"fingerprint": observation["start"], "method": observation["method"]}
            integrity = (generation,)
        bootstrap_baseline = keys == ["baseline"] and generation is None
        baseline_conflict = bootstrap_baseline and (observation["start"] != observation["end"]
            or observation["changes"]["count"] != 0 or not observation["changes"]["within_domains"])
        if bootstrap_baseline and not baseline_conflict:
            generation = _seal(connection, task_id=assignment["task_id"], revision=assignment["revision"],
                               observation=observation, source_assignment_id=assignment_id)
            target = connection.execute("SELECT * FROM artifact_generations WHERE generation_key=?", (generation,)).fetchone()
            integrity = (generation,)
        if not baseline_conflict and (target is None or target["method"] != observation["method"]):
            raise GraphError("artifact_target_invalid")
        conflict = baseline_conflict or (integrity[0] != generation or observation["start"] != target["fingerprint"]
                    or not observation["changes"]["within_domains"]
                    or (assignment["mode"] == "read_only" and (observation["end"] != target["fingerprint"] or observation["changes"]["count"] != 0)))
        if conflict:
            connection.execute("UPDATE execution_assignments SET state='snapshot_conflict' WHERE assignment_id=?", (assignment_id,))
            connection.execute("UPDATE execution_nodes SET state='blocked' WHERE assignment_id=?", (assignment_id,))
            _raise_barrier(connection, assignment["task_id"])
            _event(connection, assignment["graph_id"], "snapshot_conflict", {"assignment": assignment_id})
            return {"state": "snapshot_conflict", "published": False, "replayed": False}
        if rebaselining:
            boundary = observation["boundary"]
            declared = _graph(connection, assignment["graph_id"])[1].data()
            expected_method = target["method"] if keys == ["baseline"] else declared["fingerprint_method"]
            if boundary["method"] != expected_method:
                raise GraphError("artifact_boundary_method_invalid")
            if boundary["start"] != boundary["end"] or boundary["changes"]["count"] or not boundary["changes"]["within_domains"]:
                connection.execute("UPDATE execution_assignments SET state='snapshot_conflict' WHERE assignment_id=?", (assignment_id,))
                connection.execute("UPDATE execution_nodes SET state='blocked' WHERE assignment_id=?", (assignment_id,))
                _raise_barrier(connection, assignment["task_id"])
                return {"state": "snapshot_conflict", "published": False, "replayed": False}
            complete = all(item["status"] == "complete" for items in coverage.values() for item in items) and (report_content is None or (report_content.get("status") == "completed" and not report_content.get("unresolved")))
            if complete:
                generation = _seal(connection, task_id=assignment["task_id"], revision=assignment["revision"], observation=boundary,
                    source_assignment_id=assignment_id, artifact_paths=declared["artifact_paths"])
        if assignment["mode"] == "mutating":
            generation = _seal(connection, task_id=assignment["task_id"], revision=assignment["revision"],
                               observation=observation, source_assignment_id=assignment_id)
    connection.execute("INSERT INTO execution_publications VALUES (?,?,?,?,?)", (assignment_id, report_id, digest, _json(payload), generation))
    connection.execute("UPDATE execution_assignments SET state='published',quiescent=1 WHERE assignment_id=?", (assignment_id,))
    for key in keys:
        facts = facts_by_node[key]
        statuses = {item["status"] for item in coverage[key]}
        state = "complete" if statuses == {"complete"} else "failed" if any(fact["state"] == "failed" for fact in facts) else "blocked" if "blocked" in statuses else "partial"
        if state == "complete" and report_content is not None and (report_content.get("status") != "completed" or report_content.get("unresolved")):
            state = "partial"
        connection.execute("UPDATE execution_nodes SET state=?,facts_json=?,artifact_generation=? WHERE graph_id=? AND node_key=?",
                           (state, _json(facts), generation, assignment["graph_id"], key))
        if key == "validate-candidate":
            family = connection.execute("SELECT 1 FROM plan_candidate_families WHERE graph_id=?", (assignment["graph_id"],)).fetchone()
            connection.execute("UPDATE execution_graphs SET activation=? WHERE graph_id=? AND activation='candidate'",
                               (("validated" if family else "active") if state == "complete" else "rejected", assignment["graph_id"]))
    _event(connection, assignment["graph_id"], "nodes_published", {"assignment": assignment_id, "report": report_id})
    if len(keys) == 1 and _reconciliation(connection, assignment["graph_id"], node=keys[0]) is not None:
        _after_reconciliation(connection, assignment=assignment, node=keys[0], report_id=report_id,
                              facts=facts_by_node[keys[0]], artifact=artifact)
    materialize_minimal(connection, assignment["task_id"])
    from cortex_runtime.remediation import expand
    expand(connection, assignment_id=assignment_id, report_id=report_id)
    return {"state": "published", "published": True, "replayed": False, "report_id": report_id}


def invalidate_revision(connection: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    _transaction(connection)
    revision = _current_revision(connection, task_id)
    stale = [dict(row) for row in connection.execute(
        "SELECT assignment_id,nodes_json,protected_task_name FROM execution_assignments WHERE task_id=? AND revision<? AND state='active'",
        (task_id, revision))]
    connection.execute("UPDATE execution_graphs SET activation='stale' WHERE task_id=? AND revision<?", (task_id, revision))
    connection.execute("UPDATE execution_assignments SET state='stale' WHERE task_id=? AND revision<? AND state='active'", (task_id, revision))
    connection.execute("UPDATE execution_nodes SET state='stale' WHERE graph_id IN (SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision<?) AND state IN ('waiting','ready','active')", (task_id, revision))
    _raise_barrier(connection, task_id, force_new=True)
    return [{"nodes": json.loads(row["nodes_json"]), "task_name": row["protected_task_name"]} for row in stale]


def ensure_bootstrap(connection: sqlite3.Connection, *, task_id: str, outcomes: Sequence[str]) -> str:
    """Create the revision's immutable bootstrap root and initial baseline node."""
    _transaction(connection)
    revision = _current_revision(connection, task_id)
    graph_id = _digest([task_id, revision, "bootstrap"])
    if connection.execute("SELECT 1 FROM execution_graphs WHERE graph_id=?", (graph_id,)).fetchone():
        return graph_id
    value = {"nodes": [], "outcomes": [{"outcome": name, "all_of": [], "non_execution": "Bootstrap evidence only"} for name in outcomes],
             "fingerprint_method": "path_manifest_v1", "artifact_paths": ["."],
             "budgets": {"planning": 4, "additional_evidence": 4, "reconciliation": 4, "recovery": 4}}
    connection.execute("INSERT INTO execution_graphs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (graph_id, "bootstrap", task_id, revision, "bootstrap", "", _digest(value), _json(value), "[]", "active", 0, 0))
    baseline = _bootstrap_node("baseline", "discovery", outcomes,
        "Observe the project baseline without changing files; establish repository capability and a stable artifact fingerprint.",
        checks=[{"key": "baseline", "description": "Establish capability and matching start/end observations without mutations.", "required": True}])
    connection.execute("INSERT INTO execution_nodes(graph_id,node_key,content_json,state) VALUES (?,?,?,'waiting')",
        (graph_id, "baseline", _json(baseline)))
    _event(connection, graph_id, "bootstrap_created", {"revision": revision})
    return graph_id


def materialize_minimal(connection: sqlite3.Connection, task_id: str) -> None:
    """Materialize the selected route only after a verified baseline exists."""
    revision = _current_revision(connection, task_id)
    policy = connection.execute("SELECT p.execution_route,p.minimal_mode FROM execution_policies p JOIN governance_assessments a ON a.assessment_id=p.assessment_id WHERE p.task_id=? ORDER BY a.created_sequence DESC LIMIT 1", (task_id,)).fetchone()
    if policy is None or policy["execution_route"] != "minimal":
        return
    if connection.execute("SELECT 1 FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind!='bootstrap'", (task_id, revision)).fetchone():
        return
    integrity = connection.execute("SELECT generation_key,reconciliation_required FROM project_integrity").fetchone()
    if integrity["generation_key"] is None or integrity["reconciliation_required"]:
        return
    baseline = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind='bootstrap'", (task_id, revision)).fetchone()
    if baseline is None:
        return
    reconciliation = _reconciliation(connection, baseline[0])
    node = reconciliation["node"] if reconciliation else "baseline"
    observed = connection.execute("SELECT state,artifact_generation FROM execution_nodes WHERE graph_id=? AND node_key=?", (baseline[0], node)).fetchone()
    if observed is None or observed["state"] != "complete" or observed["artifact_generation"] != integrity["generation_key"]:
        return
    method = connection.execute("SELECT method FROM artifact_generations WHERE generation_key=?", (integrity["generation_key"],)).fetchone()[0]
    create_minimal_graph(connection, task_id=task_id, mode=policy["minimal_mode"], method=method)


def create_minimal_graph(connection: sqlite3.Connection, *, task_id: str, mode: str, method: str) -> str:
    """A deterministic complete-contract node, not a graph-free fast path."""
    _transaction(connection)
    revision = _current_revision(connection, task_id)
    if mode not in {"read_only", "mutating"}:
        raise GraphError("minimal_execution_mode_invalid")
    if connection.execute("SELECT 1 FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind!='bootstrap'", (task_id, revision)).fetchone():
        raise GraphError("execution_graph_already_exists")
    outcomes = [row[0] for row in connection.execute("SELECT text FROM effective_contract_items WHERE task_id=? AND (retired_revision IS NULL OR retired_revision>?) ORDER BY ordinal", (task_id, revision))]
    contributions = [f"minimal-contribution-{index + 1}" for index in range(len(outcomes))]
    checks = [{"key": "acceptance", "description": "Verify every assigned outcome against all of its original acceptance, constraints, and verification obligations.", "required": True}]
    node = {"key": "minimal-execution", "kind": "implementation" if mode == "mutating" else "discovery",
        "responsibility": "delivery" if mode == "mutating" else "evidence", "execution_mode": mode,
        "owner": "Complete the bounded minimal task", "contributions": contributions, "verifies": [],
        "mutation_domains": ["."] if mode == "mutating" else [], "requires": [], "provides": ["artifact"], "dependencies": [],
        "work": ["Complete the entire current task contract within this single bounded scope. Preserve every supplied requirement and stop before any action requiring new authority."],
        "acceptance": ["Every original outcome and its checks are complete; documentation impact is assessed."],
        "checks": checks, "activation": "always"}
    if mode == "mutating":
        node["remediation"] = {"generation_budget": 2, "strategy_budget": 1,
            "strategies": [{"key": "focused-repair", "work": ["Repair the demonstrated in-contract defect."], "diagnostic_checks": checks}],
            "mutation_domains": ["."], "restores": ["artifact"], "regression_checks": checks, "classification_verification": False}
    graph = {"nodes": [node], "outcomes": [{"outcome": name, "all_of": [contribution]} for name, contribution in zip(outcomes, contributions)],
        "fingerprint_method": method, "artifact_paths": ["."],
        "budgets": {"planning": 2, "additional_evidence": 2, "reconciliation": 2, "recovery": 2}}
    validated = validate_graph(graph, outcomes)
    identifier = _digest([task_id, revision, "minimal"])
    connection.execute("INSERT INTO execution_graphs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (identifier, "generated", task_id, revision,
        "minimal", "", validated.digest, validated.canonical_json, _json(validated.order), "active", 0, 0))
    connection.execute("INSERT INTO execution_nodes(graph_id,node_key,content_json,state) VALUES (?,?,?,'waiting')", (identifier, node["key"], _json(node)))
    _event(connection, identifier, "minimal_graph_created", {"mode": mode})
    return identifier


def _bootstrap_node(key: str, kind: str, outcomes: Sequence[str], question: str,
                    *, checks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"key": key, "kind": kind, "responsibility": "planning" if kind == "planning" else "evidence",
            "execution_mode": "read_only", "owner": "Planning" if kind == "planning" else "Read-only evidence",
            "contributions": [], "verifies": [{"kind": "outcome", "name": name} for name in outcomes],
            "mutation_domains": [], "requires": [], "provides": [f"evidence-{key}"], "dependencies": [],
            "work": [question], "acceptance": ["Complete only this node's question and checks. Product requirements are context; downstream implementation and checks are not assigned here."],
            "checks": list(checks), "activation": "always"}


def bootstrap_readiness(connection: sqlite3.Connection, graph_id: str, kind: str) -> dict[str, Any]:
    """Read-only prerequisites shared by scope projection and append admission."""
    record, graph = _graph(connection, graph_id)
    reasons = []
    if kind not in {"planning", "discovery"}:
        return {"available": False, "reasons": []}
    if record["graph_kind"] != "bootstrap" or record["revision"] != _current_revision(connection, record["task_id"]):
        return {"available": False, "reasons": [{"kind": "graph_revision_stale"}]}
    integrity = connection.execute("SELECT generation_key,reconciliation_required FROM project_integrity").fetchone()
    if integrity["generation_key"] is None:
        reasons.append({"kind": "artifact_generation_missing"})
    if integrity["reconciliation_required"]:
        reasons.append({"kind": "reconciliation_required"})
    reconciliation = _reconciliation(connection, graph_id)
    baseline = reconciliation["node"] if reconciliation else "baseline"
    rows = list(connection.execute("SELECT node_key,content_json,state FROM execution_nodes WHERE graph_id=? ORDER BY rowid", (graph_id,)))
    after_baseline = False
    for row in rows:
        node = json.loads(row["content_json"])
        after_baseline = after_baseline or row["node_key"] == baseline
        if after_baseline and (row["node_key"] == baseline or (kind == "planning" and node["kind"] == "discovery")):
            if row["state"] not in {"complete", "resolved"}:
                reasons.append({"kind": "predecessor_unsatisfied", "node": row["node_key"], "responsibility": "evidence"})
    budget = graph.data()["budgets"]["planning" if kind == "planning" else "additional_evidence"]
    candidate = connection.execute("SELECT g.activation,g.content_json,r.created_sequence FROM execution_graphs g LEFT JOIN reports r ON r.report_id=g.plan_report_id WHERE g.task_id=? AND g.revision=? AND g.graph_kind='candidate' ORDER BY g.rowid DESC LIMIT 1", (record["task_id"], record["revision"])).fetchone()
    if candidate is not None:
        budget = min(budget, json.loads(candidate["content_json"])["budgets"]["planning" if kind == "planning" else "additional_evidence"])
    count = sum(json.loads(row["content_json"])["kind"] == kind and row["node_key"] != "baseline" and not row["node_key"].startswith("reconcile-") for row in rows)
    if count >= budget:
        reasons.append({"kind": "bootstrap_budget_exhausted"})
    if kind == "planning":
        if connection.execute("SELECT 1 FROM execution_assignments a JOIN execution_graphs g ON g.graph_id=a.graph_id WHERE a.task_id=? AND a.revision=? AND a.state='active' AND g.graph_kind!='bootstrap' LIMIT 1", (record["task_id"], record["revision"])).fetchone():
            reasons.append({"kind": "execution_evidence_pending"})
        assessment = connection.execute("SELECT MAX(created_sequence) FROM governance_assessments WHERE task_id=?", (record["task_id"],)).fetchone()[0]
        risk_changed = candidate is not None and candidate["created_sequence"] is not None and assessment is not None and assessment > candidate["created_sequence"]
        if count and (candidate is None or (candidate["activation"] != "rejected" and not risk_changed)):
            reasons.append({"kind": "bootstrap_non_progress"})
    modes = [row[0] for row in connection.execute("SELECT mode FROM execution_assignments WHERE state='active' OR (state IN ('stale','snapshot_conflict','lost') AND quiescent=0)")]
    try:
        assignment_compatible([{"kind": kind, "execution_mode": "read_only"}], modes)
    except GraphError as exc:
        reasons.append({"kind": exc.reason})
    return {"available": not reasons, "reasons": reasons}


def append_bootstrap_node(connection: sqlite3.Connection, *, graph_id: str,
                          kind: str, key: str, question: str) -> None:
    _transaction(connection)
    record, graph = _graph(connection, graph_id)
    if record["graph_kind"] != "bootstrap" or record["revision"] != _current_revision(connection, record["task_id"]):
        raise GraphError("graph_revision_stale")
    if kind not in {"planning", "discovery"} or not KEY_RE.fullmatch(key) or key in {"baseline", "validate-candidate"}:
        raise GraphError("bootstrap_node_invalid")
    if not isinstance(question, str) or not question.strip() or len(question) > 2048:
        raise GraphError("bootstrap_question_invalid")
    if connection.execute("SELECT 1 FROM execution_nodes WHERE graph_id=? AND node_key=?", (graph_id, key)).fetchone():
        raise GraphError("bootstrap_node_exists")
    previous = [json.loads(row[0]) for row in connection.execute("SELECT content_json FROM execution_nodes WHERE graph_id=?", (graph_id,))]
    budget = graph.data()["budgets"]["planning" if kind == "planning" else "additional_evidence"]
    candidate = connection.execute("SELECT g.activation,g.content_json,r.created_sequence FROM execution_graphs g LEFT JOIN reports r ON r.report_id=g.plan_report_id WHERE g.task_id=? AND g.revision=? AND g.graph_kind='candidate' ORDER BY g.rowid DESC LIMIT 1", (record["task_id"], record["revision"])).fetchone()
    if candidate is not None:
        budget = min(budget, json.loads(candidate["content_json"])["budgets"]["planning" if kind == "planning" else "additional_evidence"])
    if sum(node["kind"] == kind and node["key"] != "baseline" and not node["key"].startswith("reconcile-") for node in previous) >= budget:
        raise GraphError("bootstrap_budget_exhausted")
    if any(node["work"] == [question] for node in previous):
        if kind == "planning":
            assessment = connection.execute("SELECT MAX(created_sequence) FROM governance_assessments WHERE task_id=?", (record["task_id"],)).fetchone()[0]
            risk_changed = (candidate is not None and candidate["created_sequence"] is not None and assessment is not None and assessment > candidate["created_sequence"])
            if candidate is None or (candidate["activation"] != "rejected" and not risk_changed):
                raise GraphError("bootstrap_non_progress")
        else:
            generation = connection.execute("SELECT generation_key FROM project_integrity WHERE singleton=1").fetchone()[0]
            repeated = [node["key"] for node in previous if node["work"] == [question]]
            if any(connection.execute("SELECT artifact_generation FROM execution_nodes WHERE graph_id=? AND node_key=?", (graph_id, name)).fetchone()[0] in {None, generation} for name in repeated):
                raise GraphError("bootstrap_non_progress")
    if not bootstrap_readiness(connection, graph_id, kind)["available"]:
        raise GraphError("bootstrap_prerequisites_unsatisfied")
    outcomes = [item["outcome"] for item in graph.data()["outcomes"]]
    node = _bootstrap_node(key, kind, outcomes, question,
        checks=[{"key": "contract", "description": "Cover the complete current contract with a bounded candidate graph." if kind == "planning" else "Answer the declared evidence question.", "required": True}])
    reconciliation = _reconciliation(connection, graph_id)
    baseline_key = reconciliation["node"] if reconciliation else "baseline"
    after_baseline = previous[next(index for index, item in enumerate(previous) if item["key"] == baseline_key):]
    predecessors = [item for item in after_baseline if item["key"] == baseline_key or (kind == "planning" and item["kind"] == "discovery")]
    node["dependencies"] = [{"node": parent["key"], "capabilities": parent["provides"], "optional": False, "allow_not_applicable": False} for parent in predecessors]
    node["requires"] = [capability for edge in node["dependencies"] for capability in edge["capabilities"]]
    connection.execute("INSERT INTO execution_nodes(graph_id,node_key,content_json,state) VALUES (?,?,?,'waiting')",
                       (graph_id, key, _json(node)))
    _event(connection, graph_id, "bootstrap_node_appended", {"node": key, "kind": kind})


def _bootstrap_projection(connection: sqlite3.Connection, graph_id: str, integrity: Mapping[str, Any],
                          observations: Mapping[str, Mapping[str, Any]], *, native_observation: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    result = []
    definitions = [json.loads(row[0]) for row in connection.execute("SELECT content_json FROM execution_nodes WHERE graph_id=? ORDER BY rowid", (graph_id,))]
    reconciliation = _reconciliation(connection, graph_id)
    exhausted = connection.execute("SELECT 1 FROM execution_events WHERE graph_id=? AND event='reconciliation_exhausted' LIMIT 1", (graph_id,)).fetchone() is not None
    reconciliation_keys = {json.loads(row[0])["node"] for row in connection.execute(
        "SELECT details_json FROM execution_events WHERE graph_id=? AND event='reconciliation_created'", (graph_id,))}
    reconciled = reconciliation is not None and observations[reconciliation["node"]]["state"] == "complete"
    for node in definitions:
        state = observations[node["key"]]["state"]
        reasons = []
        if node["key"] in reconciliation_keys and state in {"partial", "failed", "blocked"}:
            if exhausted:
                state, reasons = "exhausted", [{"kind": "reconciliation_exhausted"}]
            elif reconciled:
                state = "resolved"
        if state == "waiting":
            is_reconciliation = reconciliation is not None and node["key"] == reconciliation["node"]
            if node["kind"] == "planning":
                record, _ = _graph(connection, graph_id)
                if connection.execute("SELECT 1 FROM execution_assignments a JOIN execution_graphs g ON g.graph_id=a.graph_id WHERE a.task_id=? AND a.revision=? AND a.state='active' AND g.graph_kind!='bootstrap' LIMIT 1", (record["task_id"], record["revision"])).fetchone():
                    reasons.append({"kind": "execution_evidence_pending"})
            if is_reconciliation:
                if not integrity["reconciliation_required"] or reconciliation["barrier_epoch"] != integrity["barrier_epoch"]:
                    reasons.append({"kind": "reconciliation_superseded"})
                hazards = native_quiescence(connection, task_id=_graph(connection, graph_id)[0]["task_id"], observation=native_observation)
                reasons.extend({"kind": item["reason"]} for item in hazards["waiting"])
            elif integrity["reconciliation_required"] or (reconciliation and node["key"] == "baseline"):
                reasons.append({"kind": "reconciliation_required"})
            if node["key"] != "baseline" and not is_reconciliation and integrity["generation_key"] is None:
                reasons.append({"kind": "artifact_generation_missing"})
            for edge in node["dependencies"]:
                if observations.get(edge["node"], {}).get("state") not in {"complete", "resolved"}:
                    reasons.append({"kind": "predecessor_unsatisfied", "node": edge["node"]})
            state = "waiting" if reasons else "ready"
        result.append({"node": node["key"], "state": state, "reasons": reasons,
                       "contributions": [], "verifies": node["verifies"], "artifact_generation": integrity["generation_key"]})
    return result


def planned_coverage(graph: ValidatedGraph) -> list[dict[str, Any]]:
    """Derive plan expectations without synthesizing observed fact states."""
    value = graph.data()
    result = []
    for expression in value["outcomes"]:
        related = [node for node in value["nodes"] if set(node["contributions"]) & set(expression["all_of"])
                   or {"kind": "outcome", "name": expression["outcome"]} in node["verifies"]
                   or any(subject["kind"] == "contribution" and subject["name"] in expression["all_of"] for subject in node["verifies"])]
        result.append({"outcome": expression["outcome"], "status": "planned",
            "expected_checks": [{"node": node["key"], **check} for node in related for check in node["checks"]],
            **({"non_execution": expression["non_execution"]} if "non_execution" in expression else {})})
    return result


def publish_candidates(connection: sqlite3.Connection, *, assignment_id: str, report_id: str,
                      candidates: Sequence[Mapping[str, Any]], artifact: Mapping[str, Any],
                      review_required: bool, report_content: Mapping[str, Any]) -> dict[str, Any]:
    """Commit one immutable plan, ordinary or decision-bearing, atomically."""
    from cortex_runtime.candidate_family import current_contract, validate_family, create_family
    _transaction(connection)
    row = connection.execute("SELECT * FROM execution_assignments WHERE assignment_id=?", (assignment_id,)).fetchone()
    if row is None:
        raise GraphError("assignment_missing")
    assignment = dict(row)
    if assignment["state"] == "stale" or assignment["revision"] != _current_revision(connection, assignment["task_id"]):
        return {"state": "superseded", "published": False, "replayed": False}
    if assignment["state"] == "snapshot_conflict":
        return {"state": "snapshot_conflict", "published": False, "replayed": False}
    if assignment["terminal_kind"] != "plan":
        raise GraphError("publication_kind_not_permitted")
    if not isinstance(report_content, Mapping) or set(report_content) != {"status", "summary", "scope", "risks", "unresolved"}:
        raise GraphError("plan_content_invalid")
    if report_content["status"] not in {"completed", "partial", "blocked", "failed"}:
        raise GraphError("plan_content_invalid")
    if any(not isinstance(report_content[field], str) or not report_content[field].strip() for field in ("summary", "scope")):
        raise GraphError("plan_content_invalid")
    if any(not isinstance(report_content[field], list) or any(not isinstance(value, str) or not value.strip() for value in report_content[field]) for field in ("risks", "unresolved")):
        raise GraphError("plan_content_invalid")
    # Review policy is server authority, not a caller-authored publication
    # field. A later assessment must not change an identical retry's digest.
    payload = {"candidates": candidates, "artifact": artifact, "content": report_content}
    digest = _digest(payload)
    prior = connection.execute("SELECT payload_digest,report_id FROM execution_publications WHERE assignment_id=?", (assignment_id,)).fetchone()
    if prior is not None:
        if prior["payload_digest"] != digest:
            raise GraphError("report_operation_conflict")
        return {"state": "published", "published": True, "replayed": True, "report_id": prior["report_id"]}
    if assignment["state"] != "active":
        raise GraphError("assignment_not_active")
    bootstrap_record, bootstrap = _graph(connection, assignment["graph_id"])
    if bootstrap_record["graph_kind"] != "bootstrap":
        raise GraphError("planning_outside_bootstrap")
    names = [item["outcome"] for item in bootstrap.data()["outcomes"]]
    family = validate_family(candidates, current_contract(connection, assignment["task_id"]), revision=assignment["revision"])
    decision = len(candidates) > 1 or any(item["delta"]["add"] or item["delta"]["retire"] for item in candidates)
    graph = candidates[0]["graph"]
    validated = validate_graph(graph, [item["outcome"] for item in family.data()["candidates"][0]["contract"]])
    if decision:
        def progress(value):
            return sorted(_digest({"contract": item["contract"], "graph_digest": item["graph_digest"]}) for item in value["candidates"])
        predecessors = connection.execute("SELECT f.content_json FROM plan_candidate_families f JOIN execution_graphs g ON g.graph_id=f.graph_id JOIN execution_assignments a ON a.assignment_id=g.planner_assignment_id WHERE g.task_id=? AND g.revision=? AND g.activation='rejected' AND a.target_generation IS ?",
            (assignment["task_id"], assignment["revision"], assignment["target_generation"]))
        if any(progress(json.loads(row[0])) == progress(family.data()) for row in predecessors):
            raise GraphError("candidate_non_progress")
    elif connection.execute("SELECT 1 FROM execution_graphs g JOIN execution_assignments a ON a.assignment_id=g.planner_assignment_id WHERE g.task_id=? AND g.revision=? AND g.activation='rejected' AND g.content_digest=? AND a.target_generation IS ? LIMIT 1",
        (assignment["task_id"], assignment["revision"], validated.digest, assignment["target_generation"])).fetchone():
        raise GraphError("candidate_non_progress")
    observation = _observation(artifact, mutating=False)
    target = connection.execute("SELECT * FROM artifact_generations WHERE generation_key=?", (assignment["target_generation"],)).fetchone()
    current = connection.execute("SELECT generation_key FROM project_integrity WHERE singleton=1").fetchone()[0]
    if target is None or target["method"] != observation["method"]:
        raise GraphError("artifact_target_invalid")
    if current != assignment["target_generation"] or observation["start"] != target["fingerprint"] or observation["end"] != target["fingerprint"] or observation["changes"]["count"] or not observation["changes"]["within_domains"]:
        connection.execute("UPDATE execution_assignments SET state='snapshot_conflict' WHERE assignment_id=?", (assignment_id,))
        connection.execute("UPDATE execution_nodes SET state='blocked' WHERE assignment_id=?", (assignment_id,))
        _raise_barrier(connection, assignment["task_id"])
        _event(connection, assignment["graph_id"], "snapshot_conflict", {"assignment": assignment_id})
        return {"state": "snapshot_conflict", "published": False, "replayed": False}
    candidate = (create_family(connection, task_id=assignment["task_id"], plan_report_id=report_id,
        planner_assignment_id=assignment_id, candidates=candidates) if decision else
        create_candidate(connection, task_id=assignment["task_id"], revision=assignment["revision"],
            plan_report_id=report_id, planner_assignment_id=assignment_id, graph=graph, outcomes=names,
            review_required=review_required))
    complete = report_content["status"] == "completed" and not report_content["unresolved"]
    if not complete:
        connection.execute("UPDATE execution_graphs SET activation='rejected' WHERE graph_id=?", (candidate,))
        connection.execute("UPDATE execution_nodes SET state='blocked' WHERE graph_id=? AND node_key='validate-candidate'", (candidate,))
    connection.execute("INSERT INTO execution_publications VALUES (?,?,?,?,?)",
        (assignment_id, report_id, digest, _json(payload), assignment["target_generation"]))
    connection.execute("UPDATE execution_assignments SET state='published',quiescent=1 WHERE assignment_id=?", (assignment_id,))
    connection.execute("UPDATE execution_nodes SET state=? WHERE assignment_id=?", ("complete" if complete else "partial", assignment_id))
    _event(connection, assignment["graph_id"], "candidate_published", {"assignment": assignment_id, "candidate": candidate})
    return {"state": "published", "published": True, "replayed": False, "report_id": report_id,
            "candidate": candidate}
