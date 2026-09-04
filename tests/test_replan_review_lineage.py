"""Approval fulfills one risk boundary, never bypasses successor validation.

Public domain/API regression, not native-host live evidence.
"""
from plan_fixtures import ordinary_candidates
import pytest

from cortex_runtime import graph_ledger
from cortex_runtime.domain_api import (
    assess_governance, open_plan_review, publish_plan, publish_result,
    read_evidence, read_scope, record_plan_review, record_steering,
)
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.v12_service import V12ServiceError
from test_domain_public_api_contract import PROVENANCE
from test_execution_graph_integrity import graph
from test_graph_ledger import observation
from test_node_assignment_receipts import node_case
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content


def finish_observation(store, task, node):
    worker, context = dispatch_and_consume(task, nodes=[node])
    scope = store._read(lambda c: graph_ledger.assignment_scope(c, context["assignment_id"]))
    definition = scope["nodes"][0]
    payload = baseline_content()
    payload["node_coverage"] = [{"node": node, "coverage": [
        {**subject, "status": "complete", "verification": [
            {"check_key": check["key"], "state": "executed", "summary": "Independent fixture observation"}
            for check in definition["checks"]]}
        for subject in definition["verifies"]]}]
    if scope["artifact"].get("reconciliation"):
        payload["artifact"]["baseline_changes"] = observation()["changes"]
    result = publish_result(task_ref=worker, _connection_context=context, **payload)
    assert result["published"] and not result["replayed"]


def plan_and_validate(store, task):
    worker, context = dispatch_and_consume(task, bootstrap={"kind": "planning"},
        responsibility="planning", profile="planner")
    result = publish_plan(task_ref=worker, _connection_context=context, status="completed",
        summary="Bounded implementation of the current product contract", scope="Product",
        candidates=ordinary_candidates(graph()), artifact=observation(), risks=[], unresolved=[])
    assert result["published"] and not result["replayed"]
    assert frontend_state(task) == "waiting"
    finish_observation(store, task, "validate-candidate")


def frontend_state(task):
    return next(item["state"] for item in read_scope(task_ref=task, responsibility="delivery",
        _connection_context={})["data"]["nodes"] if item["node"] == "frontend")


def test_risk_change_invalidates_pending_packet_without_blocking_replanning(node_case, monkeypatch):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    assess_governance(task_ref=task, mode="full", rationale="Initial boundary")
    finish_observation(store, task, "baseline")
    plan_and_validate(store, task)
    open_plan_review(task_ref=task, prompt="Approve the validated product plan?", prompt_language="en")
    assess_governance(task_ref=task, mode="full", rationale="New credential authority discovered",
        risk_factors=["External credential authority"])
    with pytest.raises(V12ServiceError) as failure:
        record_plan_review(task_ref=task, outcome="approve", response_original="Approve the earlier packet.", user_language="en")
    assert failure.value.code == "clarification_binding_stale"
    assert frontend_state(task) == "waiting"
    plan_and_validate(store, task)
    opened = open_plan_review(task_ref=task, prompt="Approve the validated product plan?", prompt_language="en")
    assert not opened["replayed"]
    record_plan_review(task_ref=task, outcome="approve", response_original="Approve the updated credential boundary.", user_language="en")
    assert frontend_state(task) == "ready"


@pytest.mark.parametrize("change_before_review", [False, True])
def test_ordinary_plan_review_cannot_authorize_changed_artifact(node_case, monkeypatch, change_before_review):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    assess_governance(task_ref=task, mode="full", rationale="Initial boundary")
    finish_observation(store, task, "baseline")
    plan_and_validate(store, task)
    request = dict(task_ref=task, prompt="Approve the verified product plan?", prompt_language="en")
    if not change_before_review:
        first = open_plan_review(**request)
        assert not first["replayed"]
        assert open_plan_review(**request)["replayed"]
    store._write(lambda c: graph_ledger._seal(c, task_id=args["task_id"], revision=1,
        observation=observation("b" * 64, "b" * 64), source_assignment_id=None))
    before = store._read(lambda c: list(c.iterdump()))
    if change_before_review:
        with pytest.raises(V12ServiceError) as failure:
            open_plan_review(**request)
        assert failure.value.code == "approval_view_not_ready"
    else:
        with pytest.raises(V12ServiceError) as failure:
            record_plan_review(task_ref=task, outcome="approve", response_original="Approve the older packet.", user_language="en")
        assert failure.value.code == "clarification_binding_stale"
    assert store._read(lambda c: list(c.iterdump())) == before


@pytest.mark.parametrize("initial", ["full", "requested"])
@pytest.mark.parametrize("renewed", [None, "risk", "requested"])
def test_direct_steering_replan_only_reviews_new_decision_boundary(node_case, monkeypatch, initial, renewed):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    assess_governance(task_ref=task, mode="full" if initial == "full" else "light",
        user_review_requested=initial == "requested", rationale="Initial decision boundary")
    finish_observation(store, task, "baseline")
    plan_and_validate(store, task)
    assert frontend_state(task) == "waiting"
    open_plan_review(task_ref=task, prompt="Approve the validated product plan?", prompt_language="en")
    record_plan_review(task_ref=task, outcome="approve", response_original="Approve this plan.", user_language="en")
    assert frontend_state(task) == "ready"
    context = {}
    read_scope(task_ref=task, responsibility="delivery", _connection_context=context)
    record_steering(task_ref=task, response_original="Also require keyboard navigation for Product.",
        user_language="en", add=[{"outcome": "Product", "acceptance": ["Keyboard navigation works"],
        "constraints": [], "verification": []}], retire=["Product"], _connection_context=context)
    evidence = read_scope(task_ref=task, responsibility="evidence", _connection_context={})["data"]["nodes"]
    reconciliation = next(item["node"] for item in evidence if item["node"].startswith("reconcile-"))
    finish_observation(store, task, reconciliation)
    if renewed:
        assess_governance(task_ref=task, mode="full" if renewed == "risk" else "light",
            rationale="New authority boundary" if renewed == "risk" else "User explicitly requested renewed review",
            risk_factors=["New external authority"] if renewed == "risk" else [],
            user_review_requested=renewed == "requested")
    plan_and_validate(store, task)
    expected = "waiting" if renewed else "ready"
    assert frontend_state(task) == expected
    plan = read_evidence(task_ref=task, report_policy="active_plan")["data"]["reports"][0]
    assert plan["review_policy"] == ("required" if renewed else "informational")
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM user_decisions WHERE subject_type='plan' AND decision_type='approve'").fetchone()[0]) == 1
    if renewed:
        open_plan_review(task_ref=task, prompt="Approve the new decision boundary?", prompt_language="en")
        record_plan_review(task_ref=task, outcome="approve", response_original="Approve the revised boundary.", user_language="en")
        assert frontend_state(task) == "ready"


def test_direct_change_supersedes_unanswered_plan_without_second_question(node_case, monkeypatch):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    assess_governance(task_ref=task, mode="full", rationale="Initial boundary")
    finish_observation(store, task, "baseline")
    plan_and_validate(store, task)
    open_plan_review(task_ref=task, prompt="Approve the initial plan?", prompt_language="en")
    context = {}
    read_scope(task_ref=task, responsibility="delivery", _connection_context=context)
    before = store._read(lambda c: list(c.iterdump()))
    with pytest.raises(V12ServiceError):
        record_steering(task_ref=task, response_original="Keep the contract unchanged.", user_language="en",
            add=[{"outcome": "Product", "acceptance": [], "constraints": [], "verification": []}],
            retire=["Product"], _connection_context=context)
    assert store._read(lambda c: list(c.iterdump())) == before
    assert store._read(lambda c: len(store._pending_user_decisions(c, args["task_id"]))) == 1
    changed = record_steering(task_ref=task, response_original="Add keyboard navigation.", user_language="en",
        add=[{"outcome": "Product", "acceptance": ["Keyboard navigation"], "constraints": [], "verification": []}],
        retire=["Product"], _connection_context=context)
    assert changed["effect"]["effective_revision"] == 2
    evidence = read_scope(task_ref=task, responsibility="evidence", _connection_context={})["data"]["nodes"]
    finish_observation(store, task, next(item["node"] for item in evidence if item["node"].startswith("reconcile-")))
    plan_and_validate(store, task)
    assert store._read(lambda c: len(store._pending_user_decisions(c, args["task_id"]))) == 0
