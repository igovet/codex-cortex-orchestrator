"""Transactional DAG properties on the real project-sharded SQLite store."""
from plan_fixtures import ordinary_candidates
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from cortex_runtime import graph_ledger as ledger
from cortex_runtime.domain_api import open_task, _task_store
from cortex_runtime.execution_graph import GraphError
from test_execution_graph_integrity import graph


def observation(start="a" * 64, end="a" * 64):
    return {"method": "git_content_v1", "start": start, "end": end,
            "changes": {"count": int(start != end), "digest": "0" * 64,
                        "samples": ["src/changed"] if start != end else [], "within_domains": True}}


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    project = tmp_path / "project"
    project.mkdir()
    task_ref = open_task(project_root=str(project), request_original="Build Product", user_language="en",
                         outcomes=[{"outcome": "Product", "acceptance": ["Product works"], "constraints": [], "verification": []}],
                         constraints=["Fixture only"])["task_ref"]
    store, task_id = _task_store(task_ref)
    graph_id = store._write(lambda c: ledger.create_candidate(c, task_id=task_id, revision=1,
        plan_report_id="plan", planner_assignment_id="planner", graph=graph(), outcomes=["Product"], review_required=False))
    store._write(lambda c: ledger._seal(c, task_id=task_id, revision=1, observation=observation(), source_assignment_id=None))
    return store, task_id, graph_id


def claim(case, keys, assignment):
    store, task, graph_id = case
    digest = store._read(lambda c: ledger._graph(c, graph_id)[1].digest)
    return store._write(lambda c: ledger.claim_nodes(c, graph_id=graph_id, task_id=task,
        expected_digest=digest, node_keys=keys, assignment_id=assignment, protected_task_name=assignment))


def publication(case, assignment, nodes, *, start="a" * 64, end="a" * 64, fail=False, terminal_kind="result"):
    store, _, _ = case
    rows = []
    for node in nodes:
        subjects = [{"kind": "contribution", "name": key} for key in node["contributions"]] + node["verifies"]
        rows.append({"node": node["key"], "coverage": [{**subject, "status": "failed" if fail else "complete",
            "verification": [{"check_key": check["key"], "state": "failed" if fail else "executed", "summary": "Observed fixture evidence",
                              **({"classification": "defect_within_contract"} if fail else {})} for check in node["checks"]]} for subject in subjects]})
    return store._write(lambda c: ledger.publish_nodes(c, assignment_id=assignment, report_id=f"report-{assignment}",
        terminal_kind=terminal_kind, node_coverage=rows, artifact=observation(start, end)))


def states(case):
    store, _, graph_id = case
    return {row["node"]: row["state"] for row in store._read(lambda c: ledger.state_projection(c, graph_id))}


def activate(case):
    validation = claim(case, ["validate-candidate"], "validator")
    publication(case, "validator", validation["nodes"])


def implement(case):
    activate(case)
    previous = "a" * 64
    for key, fingerprint in (("frontend", "b" * 64), ("backend", "c" * 64), ("integration", "d" * 64)):
        scope = claim(case, [key], key)
        publication(case, key, scope["nodes"], start=previous, end=fingerprint)
        previous = fingerprint


def test_candidate_needs_independent_validation_not_just_structural_validity(case):
    assert states(case)["frontend"] == "waiting"
    assert states(case)["validate-candidate"] == "ready"
    with pytest.raises(GraphError, match="assignment_not_ready"):
        claim(case, ["frontend"], "early")
    with pytest.raises(GraphError, match="graph_validation_not_independent"):
        claim(case, ["validate-candidate"], "planner")
    activate(case)
    assert states(case)["frontend"] == "ready"
    assert states(case)["architecture"] == "waiting"


def test_new_candidate_revokes_old_graph_scope_even_without_semantic_revision(case):
    activate(case)
    store, task, _ = case
    store._write(lambda c: ledger.create_candidate(c, task_id=task, revision=1,
        plan_report_id="replacement-plan", planner_assignment_id="replacement-planner", graph=graph(), outcomes=["Product"], review_required=False))
    assert states(case)["frontend"] == "stale"
    with pytest.raises(GraphError, match="assignment_not_ready"):
        claim(case, ["frontend"], "old-scope-worker")


def test_failed_validation_keeps_candidate_inactive(case):
    scope = claim(case, ["validate-candidate"], "validator")
    publication(case, "validator", scope["nodes"], fail=True)
    assert states(case)["frontend"] == "waiting"
    assert states(case)["validate-candidate"] == "failed"


def test_concurrent_claims_have_exactly_one_owner(case):
    activate(case)
    barrier = Barrier(2)
    def compete(name):
        barrier.wait(timeout=5)
        try:
            claim(case, ["frontend"], name)
            return "claimed"
        except GraphError as exc:
            return exc.reason
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, ["worker-one", "worker-two"]))
    assert sorted(results) == ["assignment_not_ready", "claimed"]


def test_parallel_readers_only_after_complete_implementation(case):
    implement(case)
    assert states(case)["architecture"] == states(case)["database"] == "ready"
    architecture = claim(case, ["architecture"], "architecture")
    database = claim(case, ["database"], "database")
    assert architecture["terminal_kind"] == database["terminal_kind"] == "result"
    assert architecture["artifact_generation"] == database["artifact_generation"]
    assert architecture["predecessor_reports"] == ["report-integration"]
    assert publication(case, "architecture", architecture["nodes"], start="d" * 64, end="d" * 64)["published"]
    assert publication(case, "database", database["nodes"], start="d" * 64, end="d" * 64)["published"]


def test_candidate_cannot_orphan_active_execution_workers(case):
    implement(case)
    claim(case, ["architecture"], "architecture")
    store, task, _ = case
    with pytest.raises(GraphError, match="execution_evidence_pending"):
        store._write(lambda c: ledger.create_candidate(c, task_id=task, revision=1,
            plan_report_id="premature-replan", planner_assignment_id="another-planner",
            graph=graph(), outcomes=["Product"], review_required=False))
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_graphs WHERE graph_kind='candidate'").fetchone()[0]) == 1
    assert states(case)["architecture"] == "active"


def test_shared_checkout_has_one_mutator_not_one_worker(case):
    activate(case)
    frontend = claim(case, ["frontend"], "frontend")
    with pytest.raises(GraphError, match="assignment_not_ready"):
        claim(case, ["backend"], "backend")
    assert states(case)["backend"] == "waiting"
    store, _, graph_id = case
    projected = store._read(lambda c: ledger.state_projection(c, graph_id))
    assert {"kind": "project_mutation_barrier"} in next(item for item in projected if item["node"] == "backend")["reasons"]
    publication(case, "frontend", frontend["nodes"], start="a" * 64, end="b" * 64)
    assert states(case)["backend"] == "ready"


def test_native_quiescence_is_read_only_and_cannot_infer_active_worker_loss(case):
    from cortex_runtime.host_boundary import normalize_agent_projection as project_agents
    activate(case)
    claim(case, ["frontend"], "frontend")
    store, task, _ = case
    observed = {"revision": 1, "barrier_epoch": 0, "agents": project_agents(
        {"agents": [{"agent_name": "/root", "agent_status": "running"}]}, {})}
    result = store._read(lambda c: ledger.native_quiescence(c, task_id=task, observation=observed))
    assert not result["ready"]
    assert result["waiting"] == [{"nodes": ["frontend"], "reason": "active_assignment"}]
    assert result["confirmed"] == []
    assert states(case)["frontend"] == "active"


def test_revoked_worker_requires_current_native_projection_before_reconciliation(case):
    from cortex_runtime.host_boundary import normalize_agent_projection as project_agents
    activate(case)
    claim(case, ["frontend"], "frontend")
    store, task, _ = case
    store._write(lambda c: c.execute("UPDATE execution_assignments SET state='stale' WHERE assignment_id='frontend'"))
    def read(observed):
        return store._read(lambda c: ledger.native_quiescence(c, task_id=task, observation=observed))
    assert not read(None)["ready"]
    observation = {"revision": 1, "barrier_epoch": 0, "agents": project_agents(
        {"agents": [{"agent_name": "/root", "agent_status": "running"}]}, {})}
    assert read(observation)["confirmed"] == ["frontend"]
    assert read(observation)["ready"]
    assert not read({**observation, "revision": 2})["ready"]
    assert not read({**observation, "barrier_epoch": 1})["ready"]
    present = project_agents({"agents": [
        {"agent_name": "/root", "agent_status": "running"},
        {"agent_name": "/root/frontend", "agent_status": "running"}]}, {})
    assert not read({**observation, "agents": present})["ready"]
    assert store._read(lambda c: c.execute("SELECT quiescent FROM execution_assignments WHERE assignment_id='frontend'").fetchone()[0]) == 0


def test_parallel_snapshot_conflicts_share_one_pending_reconciliation(case):
    implement(case)
    architecture = claim(case, ["architecture"], "architecture")
    database = claim(case, ["database"], "database")
    assert publication(case, "architecture", architecture["nodes"], start="e" * 64, end="e" * 64)["state"] == "snapshot_conflict"
    assert publication(case, "database", database["nodes"], start="e" * 64, end="e" * 64)["state"] == "snapshot_conflict"
    store, task, _ = case
    assert store._read(lambda c: c.execute("SELECT barrier_epoch FROM project_integrity").fetchone()[0]) == 1
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_events WHERE event='reconciliation_created'").fetchone()[0]) == 1
    assert len(store._read(lambda c: ledger.continuations(c, task))) == 2


@pytest.mark.parametrize("completes", [True, False])
def test_reconciliation_partial_appends_once_then_completes_or_exhausts(case, completes):
    import json
    implement(case)
    store, task, _ = case
    store._write(lambda c: ledger._raise_barrier(c, task))
    bootstrap = store._read(lambda c: c.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND graph_kind='bootstrap'", (task,)).fetchone()[0])
    scoped = store, task, bootstrap
    initial = store._read(lambda c: ledger._reconciliation(c, bootstrap))["node"]
    previous_generation = store._read(lambda c: c.execute("SELECT generation_key FROM project_integrity").fetchone()[0])
    for attempt, succeeds in enumerate((False, completes)):
        current = store._read(lambda c: ledger._reconciliation(c, bootstrap))["node"]
        assigned = claim(scoped, [current], f"reconciler-{attempt}")
        if attempt:
            assert current != initial
            assert "reconciliation-report-0" in assigned["predecessor_reports"]
        fact = {"check_key": "reconciliation", "state": "executed" if succeeds else "not_run",
                "summary": "Stable baseline confirmed" if succeeds else f"Missing evidence, wording {attempt}"}
        if not succeeds:
            fact["classification"] = "inconclusive"
        coverage = [{"node": current, "coverage": [{"kind": "outcome", "name": "Product",
                     "status": "complete" if succeeds else "partial", "verification": [fact]}]}]
        artifact = observation("d" * 64, "d" * 64)
        artifact["baseline_changes"] = artifact["changes"]
        result = store._write(lambda c: ledger.publish_nodes(c, assignment_id=f"reconciler-{attempt}",
            report_id=f"reconciliation-report-{attempt}", terminal_kind="result",
            node_coverage=coverage, artifact=artifact))
        assert result["published"] and not result["replayed"]
    integrity = store._read(lambda c: c.execute("SELECT generation_key,reconciliation_required FROM project_integrity").fetchone())
    assert bool(integrity[1]) is not completes
    assert (integrity[0] != previous_generation) is completes
    assert states(scoped)[initial] == ("resolved" if completes else "exhausted")
    assert store._read(lambda c: c.execute("SELECT state FROM execution_nodes WHERE graph_id=? AND node_key=?", (bootstrap, initial)).fetchone()[0]) == "partial"
    if not completes:
        assert not any(value == "ready" for value in states(scoped).values())
        evidence = store._read(lambda c: json.loads(c.execute("SELECT details_json FROM execution_events WHERE graph_id=? AND event='reconciliation_exhausted' ORDER BY sequence DESC LIMIT 1", (bootstrap,)).fetchone()[0]))
        assert evidence["reason"] == "non_progress"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_events WHERE graph_id=? AND event='reconciliation_created'", (bootstrap,)).fetchone()[0]) == 2


@pytest.mark.parametrize("start,end", [("e" * 64, "e" * 64), ("d" * 64, "e" * 64)])
def test_snapshot_mismatch_publishes_nothing_and_consumes_no_slot(case, start, end):
    implement(case)
    scope = claim(case, ["architecture"], "audit")
    reply = publication(case, "audit", scope["nodes"], start=start, end=end)
    assert reply == {"state": "snapshot_conflict", "published": False, "replayed": False}
    store, _, _ = case
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_publications WHERE assignment_id='audit'").fetchone()[0]) == 0
    assert store._read(lambda c: c.execute("SELECT reconciliation_required FROM project_integrity").fetchone()[0]) == 1
    assert states(case)["database"] == "waiting"


def test_new_generation_reverification_is_ready_bounded_and_keeps_history(case):
    implement(case)
    store, task_id, _ = case
    current = "d" * 64
    for attempt, next_character in enumerate("efg"):
        scope = claim(case, ["architecture"], f"audit-{attempt}")
        publication(case, f"audit-{attempt}", scope["nodes"], start=current, end=current)
        assert states(case)["architecture"] == "complete"
        next_fingerprint = next_character * 64
        store._write(lambda c: ledger._seal(c, task_id=task_id, revision=1,
            observation=observation(current, next_fingerprint), source_assignment_id=None))
        current = next_fingerprint
        assert states(case)["architecture"] == ("ready" if attempt < 2 else "exhausted")
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_publications WHERE assignment_id LIKE 'audit-%'").fetchone()[0]) == 3
    with pytest.raises(GraphError, match="assignment_not_ready"):
        claim(case, ["architecture"], "unbounded-audit")


def test_wrong_kind_does_not_consume_slot_and_identical_retry_reconciles(case):
    activate(case)
    scope = claim(case, ["frontend"], "frontend")
    with pytest.raises(GraphError, match="publication_kind_not_permitted"):
        publication(case, "frontend", scope["nodes"], terminal_kind="documentation")
    first = publication(case, "frontend", scope["nodes"], end="b" * 64)
    second = publication(case, "frontend", scope["nodes"], end="b" * 64)
    assert first["replayed"] is False and second["replayed"] is True
    assert first["report_id"] == second["report_id"]
    with pytest.raises(GraphError, match="report_operation_conflict"):
        publication(case, "frontend", scope["nodes"], end="c" * 64)


def test_steering_invalidation_and_racing_publication_are_atomic(case):
    activate(case)
    scope = claim(case, ["frontend"], "frontend")
    store, task_id, _ = case
    from cortex_runtime.domain_api import record_steering
    from cortex_runtime.v12_contract import task_ref as public_task_ref
    # A real recorded change creates the revision; an unauthored SQL revision
    # is now rejected before it can invalidate any assignment.
    changed = record_steering(task_ref=public_task_ref(task_id),
        response_original="Also provide keyboard accessibility.", user_language="en",
        add=[{"outcome": "Keyboard accessibility", "acceptance": ["Keyboard controls work"],
              "constraints": [], "verification": ["Exercise the keyboard controls"]}], retire=[])
    effect = changed["effect"]["stale_assignments"]
    assert effect == [{"nodes": ["frontend"], "task_name": "frontend"}]
    reply = publication(case, "frontend", scope["nodes"], end="b" * 64)
    assert reply == {"state": "superseded", "published": False, "replayed": False}
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_publications WHERE assignment_id='frontend'").fetchone()[0]) == 0


def test_mutation_outside_transaction_is_rejected(case):
    store, task, _ = case
    with store._connection() as connection:
        with pytest.raises(GraphError, match="transaction_required"):
            ledger.invalidate_revision(connection, task)


def test_bootstrap_establishes_baseline_before_discovery_and_planning(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    task_ref = open_task(project_root=str(tmp_path), request_original="Build Product", user_language="en",
        outcomes=[{"outcome": "Product", "acceptance": [], "constraints": [], "verification": []}], constraints=["Fixture only"])["task_ref"]
    store, task = _task_store(task_ref)
    bootstrap = store._write(lambda c: ledger.ensure_bootstrap(c, task_id=task, outcomes=["Product"]))
    case = store, task, bootstrap
    assert store._write(lambda c: ledger.ensure_bootstrap(c, task_id=task, outcomes=["Product"])) == bootstrap
    with pytest.raises(GraphError, match="bootstrap_prerequisites_unsatisfied"):
        store._write(lambda c: ledger.append_bootstrap_node(c, graph_id=bootstrap, kind="discovery", key="inspect-schema", question="What database schema is present?"))
    with pytest.raises(GraphError, match="bootstrap_prerequisites_unsatisfied"):
        store._write(lambda c: ledger.append_bootstrap_node(c, graph_id=bootstrap, kind="planning", key="plan-initial", question="Plan Product from the inspected baseline and schema."))
    assert states(case) == {"baseline": "ready"}
    baseline = claim(case, ["baseline"], "baseline-worker")
    assert baseline["artifact_generation"] is None
    publication(case, "baseline-worker", baseline["nodes"])
    store._write(lambda c: ledger.append_bootstrap_node(c, graph_id=bootstrap, kind="discovery", key="inspect-schema", question="What database schema is present?"))
    assert states(case)["inspect-schema"] == "ready"
    with pytest.raises(GraphError, match="bootstrap_prerequisites_unsatisfied"):
        store._write(lambda c: ledger.append_bootstrap_node(c, graph_id=bootstrap, kind="planning", key="plan-initial", question="Plan Product from the inspected baseline and schema."))
    discovery = claim(case, ["inspect-schema"], "discovery-worker")
    publication(case, "discovery-worker", discovery["nodes"])
    store._write(lambda c: ledger.append_bootstrap_node(c, graph_id=bootstrap, kind="planning", key="plan-initial", question="Plan Product from the inspected baseline and schema."))
    assert states(case)["plan-initial"] == "ready"
    planner = claim(case, ["plan-initial"], "planner-worker")
    assert planner["terminal_kind"] == "plan"
    assert planner["predecessor_reports"] == ["report-baseline-worker", "report-discovery-worker"]
    content = {"status": "completed", "summary": "Product plan", "scope": "Complete contract", "risks": [], "unresolved": []}
    published = store._write(lambda c: ledger.publish_candidates(c, assignment_id="planner-worker", report_id="plan-report",
        candidates=ordinary_candidates(graph()), artifact=observation(), review_required=False, report_content=content))
    assert published["published"]
    expectations = store._read(lambda c: ledger.planned_coverage(ledger._graph(c, published["candidate"])[1]))
    assert expectations[0]["status"] == "planned"
    assert all("state" not in check for check in expectations[0]["expected_checks"])
    candidate_case = store, task, published["candidate"]
    assert states(candidate_case)["frontend"] == "waiting"
    assert states(candidate_case)["validate-candidate"] == "ready"
    replay = store._write(lambda c: ledger.publish_candidates(c, assignment_id="planner-worker", report_id="must-not-be-created",
        candidates=ordinary_candidates(graph()), artifact=observation(), review_required=False, report_content=content))
    assert replay["replayed"] and replay["report_id"] == "plan-report"
    with pytest.raises(GraphError, match="bootstrap_non_progress"):
        store._write(lambda c: ledger.append_bootstrap_node(c, graph_id=bootstrap, kind="discovery", key="duplicated-question", question="What database schema is present?"))
