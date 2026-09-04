"""Typed public calls; no legacy assignment fields or report envelopes."""
from plan_fixtures import ordinary_candidates
import json
import re

import pytest

from cortex_runtime.domain_api import (
    open_assignment, read_scope, read_task, publish_result, _task_store,
    publish_plan, open_plan_review,
    record_steering, read_state, read_continuations, assess_governance, record_plan_review,
)
from cortex_runtime.public_contracts import build_public_contracts
from cortex_runtime.v12_service import V12ServiceError
from test_node_assignment_receipts import node_case
from test_domain_public_api_contract import PROVENANCE
from test_typed_publication_transaction import baseline_content
from cortex_runtime.v12_contract import task_ref as public_task_ref


def test_public_baseline_to_discovery_uses_nodes_and_purpose(node_case, monkeypatch):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    coordinator = {}
    scope = read_scope(task_ref=task, responsibility="evidence", _connection_context=coordinator)
    assert scope["data"]["nodes"][0]["node"] == "baseline"
    selected = dict(task_ref=task, profile_name="technical_writer", model="gpt-5.6-luna", reasoning_effort="high",
        nodes=["baseline"], _connection_context=coordinator)
    dispatch = open_assignment(**selected)
    assert not dispatch["replayed"] and "model" not in dispatch["native_dispatch"]
    assert open_assignment(**selected)["replayed"]
    worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', dispatch["native_dispatch"]["message"]).group(1)
    worker = {}
    page = read_task(task_ref=worker_ref, _connection_context=worker)
    observed = [page]
    while page["has_more"]:
        page = read_task(task_ref=worker_ref, continue_=True, _connection_context=worker)
        observed.append(page)
    def checks(value):
        if isinstance(value, dict):
            if {"key", "description", "required"}.issubset(value):
                yield value
            for item in value.values():
                yield from checks(item)
        elif isinstance(value, list):
            for item in value:
                yield from checks(item)
    assigned_checks = list(checks(observed))
    assert assigned_checks and {item["key"] for item in assigned_checks} == {"baseline"}
    assert all(item["description"] != item["key"] for item in assigned_checks)
    result = publish_result(task_ref=worker_ref, _connection_context=worker, **baseline_content())
    assert result["published"] and not result["replayed"]
    read_scope(task_ref=task, responsibility="evidence", _connection_context=coordinator)
    discovery = open_assignment(task_ref=task, profile_name="explorer", model="gpt-5.6-luna", reasoning_effort="medium",
        bootstrap={"kind": "discovery", "question": "Which schema exists?"}, _connection_context=coordinator)
    assert not discovery["replayed"]
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]) == 1


def test_public_assignment_requires_same_connection_observed_scope(node_case):
    _, args = node_case
    with pytest.raises(V12ServiceError) as failure:
        open_assignment(task_ref=public_task_ref(args["task_id"]), profile_name="general", model="gpt-5.6-luna",
            reasoning_effort="high", nodes=["baseline"], _connection_context={})
    assert failure.value.code == "assignment_stale"


def test_advertised_public_contract_has_no_old_assignment_or_publication_route():
    contracts = build_public_contracts()
    assignment = contracts["open_assignment"]["inputSchema"]["properties"]
    assert {"goal", "scope", "instructions", "outcomes", "responsibility", "report_policy", "loss_recovery"}.isdisjoint(assignment)
    assert {"nodes", "bootstrap"}.issubset(assignment)
    for name in ("publish_plan", "publish_result", "publish_documentation"):
        properties = contracts[name]["inputSchema"]["properties"]
        assert {"verification_facts", "outcome_coverage", "stages"}.isdisjoint(properties)
        assert "graph" not in properties
        assert ("candidates" in properties) == (name == "publish_plan")
        assert ("node_coverage" in properties) == (name != "publish_plan")


def dispatch_and_consume(task, *, nodes=None, bootstrap=None, responsibility="evidence", profile="general"):
    context = {}
    read_scope(task_ref=task, responsibility=responsibility, _connection_context=context)
    dispatch = open_assignment(task_ref=task, profile_name=profile, model="gpt-5.6-luna", reasoning_effort="high",
        _connection_context=context, **({"nodes": nodes} if nodes is not None else {"bootstrap": bootstrap}))
    assert not dispatch["replayed"]
    worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', dispatch["native_dispatch"]["message"]).group(1)
    worker = {}
    page = read_task(task_ref=worker_ref, _connection_context=worker)
    while page["has_more"]:
        page = read_task(task_ref=worker_ref, continue_=True, _connection_context=worker)
    return worker_ref, worker


@pytest.mark.parametrize("governance", ["minimal", "full", "requested"])
def test_public_plan_candidate_blocks_delivery_until_independent_validation(node_case, monkeypatch, governance):
    from test_execution_graph_integrity import graph
    from test_graph_ledger import observation
    from cortex_runtime import graph_ledger
    store, args = node_case
    task = public_task_ref(args["task_id"])
    if governance == "full":
        assess_governance(task_ref=task, mode="full", rationale="High-risk security fixture")
    elif governance == "requested":
        assess_governance(task_ref=task, mode="light", user_review_requested=True)
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    publish_result(task_ref=baseline, _connection_context=worker, **baseline_content())
    planner, worker = dispatch_and_consume(task, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
    plan = publish_plan(task_ref=planner, summary="Product plan", scope="Complete contract", candidates=ordinary_candidates(graph()),
        artifact=observation(), risks=[], unresolved=[], status="completed", _connection_context=worker)
    assert plan["published"]
    stored_plan = store._read(lambda c: json.loads(c.execute("SELECT content_json FROM report_chunks rc JOIN reports r ON r.report_id=rc.report_id WHERE r.report_type='plan'").fetchone()[0]))
    assert "candidates" in stored_plan and "graph" not in stored_plan and "planned_coverage" not in stored_plan
    with pytest.raises(V12ServiceError) as early:
        dispatch_and_consume(task, nodes=["frontend"], responsibility="delivery")
    assert early.value.code == "assignment_not_ready"
    validator, worker = dispatch_and_consume(task, nodes=["validate-candidate"])
    assignment = worker["assignment_id"]
    node = store._read(lambda c: graph_ledger.assignment_scope(c, assignment)["nodes"][0])
    payload = baseline_content()
    payload["node_coverage"] = [{"node": node["key"], "coverage": [{"kind": "outcome", "name": "Product", "status": "complete",
        "verification": [{"check_key": check["key"], "state": "executed", "summary": "Candidate checked"} for check in node["checks"]]}]}]
    publish_result(task_ref=validator, _connection_context=worker, **payload)
    readback = read_scope(task_ref=task, responsibility="delivery", _connection_context={})
    assert {item["node"]: item["state"] for item in readback["data"]["nodes"]}["frontend"] == ("ready" if governance == "minimal" else "waiting")
    view = open_plan_review(task_ref=task, prompt="Review this plan", prompt_language="en")
    assert view["data"]["human_view"]["markdown_link"].startswith("[Open plan revision](")
    record_plan_review(task_ref=task, outcome="approve", response_original="Approve this plan", user_language="en")
    approved = read_scope(task_ref=task, responsibility="delivery", _connection_context={})
    assert {item["node"]: item["state"] for item in approved["data"]["nodes"]}["frontend"] == "ready"
    if governance == "minimal":
        assess_governance(task_ref=task, mode="full", rationale="Newly observed security boundary", risk_factors=["Sensitive authority boundary"])
        changed_risk = read_scope(task_ref=task, responsibility="delivery", _connection_context={})
        assert {item["node"]: item["state"] for item in changed_risk["data"]["nodes"]}["frontend"] == "waiting"


def test_steering_effect_retains_stale_native_route_for_reconciliation(node_case, monkeypatch):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    addition = {"outcome": "Accessibility", "acceptance": ["Keyboard interaction works"], "constraints": [], "verification": []}
    reply = record_steering(task_ref=task, response_original="Also add keyboard interaction.", user_language="en", add=[addition], retire=[], _connection_context={})
    assert reply["effect"]["effective_revision"] == 2
    assert reply["effect"]["reconciliation_required"]
    assert reply["effect"]["invalidated_assignment_count"] == 1
    assert reply["effect"]["stale_assignments"][0]["nodes"] == ["baseline"]
    stale = publish_result(task_ref=baseline, _connection_context=worker, **baseline_content())
    assert stale["state"] == "superseded" and stale["published"] is False
    recovery = {"_role": "unknown"}
    state = read_state(task_ref=task, _connection_context=recovery)
    assert state["data"]["unfinished_assignment_count"] == 1
    assert state["data"]["recovery_required"]
    pending = read_continuations(task_ref=task, _connection_context=recovery)
    assert pending["data"]["continuations"][0]["state"] == "stale"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]) == 0


@pytest.mark.parametrize("changed", [False, True])
def test_replanning_consumes_rejected_candidate_without_new_user_decision(node_case, monkeypatch, changed):
    from cortex_runtime import graph_ledger
    from test_execution_graph_integrity import graph
    from test_graph_ledger import observation
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    publish_result(task_ref=baseline, _connection_context=worker, **baseline_content())
    planner, worker = dispatch_and_consume(task, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
    value = graph()
    plan = dict(status="completed", summary="Candidate", scope="Product", candidates=ordinary_candidates(value), artifact=observation(), risks=[], unresolved=[])
    publish_plan(task_ref=planner, _connection_context=worker, **plan)
    validator, worker = dispatch_and_consume(task, nodes=["validate-candidate"])
    node = store._read(lambda c: graph_ledger.assignment_scope(c, worker["assignment_id"])["nodes"][0])
    payload = baseline_content()
    payload["status"] = "failed"
    payload["node_coverage"] = [{"node": node["key"], "coverage": [{"kind": "outcome", "name": "Product", "status": "failed",
        "verification": [{"check_key": check["key"], "state": "failed", "summary": "Candidate needs an additional check", "classification": "defect_within_contract"} for check in node["checks"]]}]}]
    publish_result(task_ref=validator, _connection_context=worker, **payload)
    next_planner, next_worker = dispatch_and_consume(task, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
    predecessor_reports = store._read(lambda c: store._delegation(c, next_worker["assignment_id"])["input_report_ids"])
    assert len(predecessor_reports) == 3  # baseline, rejected plan, independent validation
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM clarification_bindings").fetchone()[0]) == 0
    if changed:
        value["nodes"][0]["checks"].append({"key": "regression", "description": "Check the newly identified boundary", "required": True})
        assert publish_plan(task_ref=next_planner, _connection_context=next_worker, **plan)["published"]
    else:
        plan["summary"] = "Different summary does not repair the identical candidate."
        with pytest.raises(V12ServiceError) as repeated:
            publish_plan(task_ref=next_planner, _connection_context=next_worker, **plan)
        assert repeated.value.details["reason"] == "candidate_non_progress"


@pytest.mark.parametrize("sealed", [False, True])
@pytest.mark.parametrize("complete", [False, True])
def test_reconciliation_requires_native_quiescence_then_seals_revised_baseline(node_case, monkeypatch, tmp_path, sealed, complete):
    from cortex_runtime import native_observation as native, graph_ledger
    from test_graph_ledger import observation
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    if sealed:
        publish_result(task_ref=baseline, _connection_context=worker, **baseline_content())
        dispatch_and_consume(task, bootstrap={"kind": "discovery", "question": "Which database exists?"})
    addition = {"outcome": "Accessibility", "acceptance": [], "constraints": [], "verification": []}
    effect = record_steering(task_ref=task, response_original="Add accessibility.", user_language="en",
        add=[addition], retire=[], _connection_context={})["effect"]
    plugin_data = tmp_path / "native-observations"
    context = {"_native_plugin_data": plugin_data}
    native.bind_task(plugin_data, task_digest=native.digest(task), session_digest=native.digest("coordinator"))
    def scope():
        return read_scope(task_ref=task, responsibility="evidence", _connection_context=context)["data"]["nodes"]
    before = scope()
    reconciliation = next(item["node"] for item in before if item["node"].startswith("reconcile-"))
    assert {item["node"]: item["state"] for item in before}[reconciliation] == "waiting"
    def observe(children):
        assert native.record_projection(plugin_data, task_digest=native.digest(task), session_digest=native.digest("coordinator"),
            revision=2, barrier_epoch=effect["reconciliation_epoch"], response={"agents": [
                {"agent_name": "/root", "agent_status": "running"}, *children]}, arguments={})
    observe([{"agent_name": "/root/" + effect["stale_assignments"][0]["task_name"], "agent_status": "running"}])
    assert {item["node"]: item["state"] for item in scope()}[reconciliation] == "waiting"
    observe([])
    assert {item["node"]: item["state"] for item in scope()}[reconciliation] == "ready"
    observe([{"agent_name": "/root/" + effect["stale_assignments"][0]["task_name"], "agent_status": "running"}])
    with pytest.raises(V12ServiceError) as changed_observation:
        open_assignment(task_ref=task, profile_name="explorer", model="gpt-5.6-luna", reasoning_effort="high",
            nodes=[reconciliation], _connection_context=context)
    assert changed_observation.value.code == "assignment_stale"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments WHERE state='stale' AND quiescent=1").fetchone()[0]) == 0
    observe([{"agent_name": "/root/" + stale["task_name"], "agent_status": "interrupted"}
             for stale in effect["stale_assignments"]])
    state = read_state(task_ref=task, _connection_context=context)["data"]
    assert state["node_state_counts"]["ready"] >= 1
    continuations = read_continuations(task_ref=task, _connection_context=context)["data"]["continuations"]
    assert continuations and all(item["quiescent"] for item in continuations)
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments WHERE state='stale' AND quiescent=1").fetchone()[0]) == 0
    assert {item["node"]: item["state"] for item in scope()}[reconciliation] == "ready"
    dispatch = open_assignment(task_ref=task, profile_name="explorer", model="gpt-5.6-luna", reasoning_effort="high",
        nodes=[reconciliation], _connection_context=context)
    worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', dispatch["native_dispatch"]["message"]).group(1)
    worker = {}
    page = read_task(task_ref=worker_ref, _connection_context=worker)
    while page["has_more"]:
        page = read_task(task_ref=worker_ref, continue_=True, _connection_context=worker)
    assigned = store._read(lambda c: graph_ledger.assignment_scope(c, worker["assignment_id"]))
    assert assigned["artifact"]["reconciliation"]
    content = baseline_content()
    content["artifact"] = observation("b" * 64, "b" * 64)
    if sealed:
        content["artifact"]["baseline_changes"] = observation("a" * 64, "b" * 64)["changes"]
    content["node_coverage"] = [{"node": reconciliation, "coverage": [{**subject, "status": "complete",
        "verification": [{"check_key": "reconciliation", "state": "executed", "summary": "Saved baseline compared and stable current observations confirmed."}]} for subject in assigned["nodes"][0]["verifies"]]}]
    if not complete:
        content["status"] = "partial"
        content["unresolved"] = ["A required semantic comparison is still incomplete."]
    result = publish_result(task_ref=worker_ref, _connection_context=worker, **content)
    assert result["published"] and not result["replayed"]
    state = read_state(task_ref=task, _connection_context=context)["data"]
    if not complete:
        assert state["reconciliation_required"]
        assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM artifact_generations").fetchone()[0]) == int(sealed)
        return
    assert not state["reconciliation_required"]
    assert state["unfinished_assignment_count"] == 0
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM artifact_generations").fetchone()[0]) == (2 if sealed else 1)
    planner, _ = dispatch_and_consume(task, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
    assert planner
