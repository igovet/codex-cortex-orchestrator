"""A current human choice cannot turn incomplete typed evidence into success."""
from plan_fixtures import ordinary_candidates
import pytest

from cortex_runtime.domain_api import open_clarification, record_clarification, close_task, read_state
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.v12_service import V12ServiceError
from cortex_runtime import graph_ledger
from test_node_assignment_receipts import node_case


def review(task, answer="close"):
    open_clarification(task_ref=task, prompt="Review the current result: revise or close?", prompt_language="en",
        purpose="closure_review", options=["revise", "close"])
    record_clarification(task_ref=task, response_original=answer, user_language="en", outcome=answer)


def test_close_requires_current_choice_and_incomplete_task_cannot_be_ready(node_case):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    with pytest.raises(V12ServiceError) as missing:
        close_task(task_ref=task, verdict="ready")
    assert missing.value.code == "closure_review_required"
    review(task, "revise")
    with pytest.raises(V12ServiceError) as revised:
        close_task(task_ref=task, verdict="ready")
    assert revised.value.code == "closure_revision_requested"
    review(task)
    with pytest.raises(V12ServiceError) as incomplete:
        close_task(task_ref=task, verdict="ready")
    assert incomplete.value.code == "closure_not_ready"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM governance_closures").fetchone()[0]) == 0
    with pytest.raises(V12ServiceError) as retired:
        close_task(task_ref=task, verdict="not_ready")
    assert retired.value.code == "invalid_argument"
    with pytest.raises(V12ServiceError) as incomplete_with_risks:
        close_task(task_ref=task, verdict="ready_with_risks", unresolved_risks=["Work is unfinished"])
    assert incomplete_with_risks.value.code == "closure_not_ready"
    assert read_state(task_ref=task)["data"]["closure_record_status"] == "not_recorded"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM governance_closures").fetchone()[0]) == 0


def test_snapshot_conflict_invalidates_review_even_without_report_or_timeline_mutation(node_case):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    review(task)
    store._write(lambda c: graph_ledger._raise_barrier(c, args["task_id"]))
    with pytest.raises(V12ServiceError) as stale:
        close_task(task_ref=task, verdict="ready")
    assert stale.value.code == "closure_review_stale"


def test_a_closed_revision_cannot_dispatch_or_reuse_its_review_after_steering(node_case, monkeypatch):
    from cortex_runtime.domain_api import open_assignment, read_scope, record_steering
    from test_domain_public_api_contract import PROVENANCE
    task = complete_graph(node_case, monkeypatch)
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    review(task)
    close_task(task_ref=task, verdict="ready")
    context = {}
    read_scope(task_ref=task, responsibility="evidence", _connection_context=context)
    with pytest.raises(V12ServiceError) as closed:
        open_assignment(task_ref=task, nodes=["baseline"], profile_name="explorer", model="gpt-5.6-luna", reasoning_effort="high", _connection_context=context)
    assert closed.value.code == "task_closed"
    record_steering(task_ref=task, response_original="Also check accessibility.", user_language="en", retire=[],
        add=[{"outcome": "Accessibility", "acceptance": [], "constraints": [], "verification": []}], _connection_context=context)
    with pytest.raises(V12ServiceError) as stale:
        close_task(task_ref=task, verdict="ready")
    assert stale.value.code == "closure_review_stale"
    assert read_state(task_ref=task)["data"]["closure_record_status"] == "not_recorded"


def complete_graph(node_case, monkeypatch):
    from cortex_runtime.domain_api import publish_result, publish_plan
    from test_typed_public_api import dispatch_and_consume
    from test_typed_publication_transaction import baseline_content
    from test_execution_graph_integrity import graph
    from test_graph_ledger import observation
    from test_domain_public_api_contract import PROVENANCE
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    publish_result(task_ref=baseline, _connection_context=worker, **baseline_content())
    planner, worker = dispatch_and_consume(task, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
    value = graph()
    value["nodes"] = value["nodes"][:1]
    value["outcomes"][0]["all_of"] = ["ui"]
    publish_plan(task_ref=planner, status="completed", summary="Bounded implementation", scope="Product", candidates=ordinary_candidates(value),
        artifact=observation(), risks=[], unresolved=[], _connection_context=worker)
    for key, responsibility, end in (("validate-candidate", "evidence", "a" * 64), ("frontend", "delivery", "b" * 64)):
        worker_ref, context = dispatch_and_consume(task, nodes=[key], responsibility=responsibility)
        node = store._read(lambda c: graph_ledger.assignment_scope(c, context["assignment_id"])["nodes"][0])
        content = baseline_content()
        content["artifact"] = observation("a" * 64, end)
        content["node_coverage"] = [{"node": key, "coverage": [{**subject, "status": "complete",
            "verification": [{"check_key": check["key"], "state": "executed", "summary": "Declared check completed"} for check in node["checks"]]}
            for subject in ([{"kind": "contribution", "name": name} for name in node["contributions"]] + node["verifies"])]}]
        publish_result(task_ref=worker_ref, _connection_context=context, **content)
    return task


def test_complete_typed_graph_closes_with_verified_publication_links(node_case, monkeypatch):
    task = complete_graph(node_case, monkeypatch)
    review(task)
    closed = close_task(task_ref=task, verdict="ready")
    assert closed["state"] == "closed" and not closed["replayed"]
    assert closed["data"]["human_views"]
    assert read_state(task_ref=task)["data"]["closure_verdict"] == "ready"
    assert close_task(task_ref=task, verdict="ready")["replayed"]
    with pytest.raises(V12ServiceError) as conflict:
        close_task(task_ref=task, verdict="ready_with_risks")
    assert conflict.value.code == "command_conflict"


def test_public_schema_cannot_request_incomplete_closure():
    from cortex import PUBLIC_TOOLS
    assert PUBLIC_TOOLS["close_task"]["inputSchema"]["properties"]["verdict"]["enum"] == ["ready", "ready_with_risks"]
