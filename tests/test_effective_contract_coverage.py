"""Typed contribution coverage and immutable current-contract worker scope."""
from plan_fixtures import ordinary_candidates
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from cortex_runtime import graph_ledger
from cortex_runtime.domain_api import (
    open_assignment, publish_plan, publish_result, read_scope, read_state,
    record_steering,
)
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.v12_service import V12ServiceError
from test_domain_public_api_contract import PROVENANCE
from test_execution_graph_integrity import graph
from test_graph_ledger import observation
from test_node_assignment_receipts import node_case
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content


@pytest.fixture
def product(node_case, monkeypatch):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    worker, context = dispatch_and_consume(task, nodes=["baseline"])
    publish_result(task_ref=worker, _connection_context=context, **baseline_content())
    worker, context = dispatch_and_consume(task, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
    publish_plan(task_ref=worker, _connection_context=context, status="completed", summary="Composite Product plan",
                 scope="Product", candidates=ordinary_candidates(graph()), artifact=observation(), risks=[], unresolved=[])
    return store, task


def content(store, context, start="a" * 64, end="a" * 64):
    node = store._read(lambda c: graph_ledger.assignment_scope(c, context["assignment_id"])["nodes"][0])
    result = baseline_content()
    result["artifact"] = observation(start, end)
    subjects = [{"kind": "contribution", "name": name} for name in node["contributions"]] + node["verifies"]
    result["node_coverage"] = [{"node": node["key"], "coverage": [{**subject, "status": "complete",
        "verification": [{"check_key": check["key"], "state": "executed", "summary": "The assigned source check passed."}
                         for check in node["checks"]]} for subject in subjects]}]
    return result


def complete(product, key, *, profile="general", responsibility="evidence", start="a" * 64, end="a" * 64):
    store, task = product
    worker, context = dispatch_and_consume(task, nodes=[key], profile=profile, responsibility=responsibility)
    return publish_result(task_ref=worker, _connection_context=context, **content(store, context, start, end))


@pytest.mark.parametrize("profile", ["debugger", "backend_dev", "general"])
def test_contribution_ownership_does_not_depend_on_profile_name(product, profile):
    _, task = product
    complete(product, "validate-candidate")
    complete(product, "frontend", profile=profile, responsibility="delivery", end="b" * 64)
    state = read_state(task_ref=task)["data"]
    assert state["coverage_status"] == "incomplete"
    assert state["coverage_status_counts"] == {"partial": 1}


def test_composite_product_requires_every_contribution_and_both_dependent_audits(product):
    _, task = product
    complete(product, "validate-candidate")
    previous = "a" * 64
    for key, successor in (("frontend", "b" * 64), ("backend", "c" * 64), ("integration", "d" * 64)):
        complete(product, key, responsibility="delivery", start=previous, end=successor)
        assert read_state(task_ref=task)["data"]["coverage_status"] == "incomplete"
        previous = successor
    complete(product, "architecture", start=previous, end=previous)
    assert read_state(task_ref=task)["data"]["coverage_status"] == "incomplete"
    complete(product, "database", start=previous, end=previous)
    assert read_state(task_ref=task)["data"]["coverage_status_counts"] == {"complete": 1}


@pytest.mark.parametrize("defect", ["missing_subject", "invented_subject", "unverified_complete"])
def test_invalid_contribution_claim_rolls_back_without_consuming_publication_slot(product, defect):
    store, task = product
    complete(product, "validate-candidate")
    worker, context = dispatch_and_consume(task, nodes=["frontend"], responsibility="delivery")
    good = content(store, context)
    bad = deepcopy(good)
    row = bad["node_coverage"][0]
    if defect == "missing_subject":
        row["coverage"] = []
    elif defect == "invented_subject":
        row["coverage"][0]["name"] = "invented"
    else:
        row["coverage"][0]["verification"][0].update(state="not_run", classification="inconclusive")
    before = store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0])
    with pytest.raises(V12ServiceError):
        publish_result(task_ref=worker, _connection_context=context, **bad)
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]) == before
    # Deliberate negative contract test, not first-call/live qualification.
    accepted = publish_result(task_ref=worker, _connection_context=context, **good)
    assert accepted["published"] and not accepted["replayed"]


def test_steering_preserves_consumed_scope_but_cannot_publish_it_as_current(product):
    store, task = product
    complete(product, "validate-candidate")
    worker, context = dispatch_and_consume(task, nodes=["frontend"], responsibility="delivery")
    old = content(store, context)
    coordinator = {}
    read_scope(task_ref=task, responsibility="delivery", _connection_context=coordinator)
    record_steering(task_ref=task, response_original="Replace Product with Revised Product.", user_language="en",
        retire=["Product"], add=[{"outcome": "Revised Product", "acceptance": ["Use the revised requirements."],
                                 "constraints": [], "verification": []}], _connection_context=coordinator)
    assert content(store, context)["node_coverage"] == old["node_coverage"]
    before = store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0])
    result = publish_result(task_ref=worker, _connection_context=context, **old)
    assert result["state"] == "superseded" and not result["published"]
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]) == before
    assert read_state(task_ref=task)["data"]["coverage_status_counts"] == {"unverified": 1}


def test_parallel_public_admission_has_exactly_one_contribution_owner(product):
    store, task = product
    complete(product, "validate-candidate")
    barrier = Barrier(2)
    def claim(profile):
        context = {}
        read_scope(task_ref=task, responsibility="delivery", _connection_context=context)
        barrier.wait(timeout=5)
        try:
            result = open_assignment(task_ref=task, nodes=["frontend"], profile_name=profile,
                model="gpt-5.6-luna", reasoning_effort="high", _connection_context=context)
            return "replay" if result["replayed"] else "created"
        except V12ServiceError as error:
            assert error.code in {"assignment_stale", "assignment_not_ready", "command_conflict"}
            return "rejected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["frontend_dev", "fullstack_dev"]))
    assert sorted(results) == ["created", "rejected"]
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments WHERE state='active'").fetchone()[0]) == 1
