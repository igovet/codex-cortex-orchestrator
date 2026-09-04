"""One explicit selection commits one validated graph, through public calls.

These use the domain adapter, not a native CLI/Desktop host.
"""
import pytest

from cortex_runtime import candidate_family, graph_ledger
from cortex_runtime.domain_api import (
    open_plan_review, publish_plan, publish_result, read_evidence, read_scope, record_plan_review,
)
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.v12_service import V12ServiceError
from test_candidate_family import alternative
from test_domain_public_api_contract import PROVENANCE
from test_graph_ledger import observation
from test_node_assignment_receipts import node_case
from test_replan_review_lineage import finish_observation, frontend_state
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content


def prepared(node_case, monkeypatch, *, empty_delta=False, changed_boundary=False):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    finish_observation(store, task, "baseline")
    worker, context = dispatch_and_consume(task, bootstrap={"kind": "planning"},
        responsibility="planning", profile="planner")
    candidates = [alternative("offline", acceptance="Works offline"), alternative("online", acceptance="Works online")]
    if empty_delta:
        candidates[0]["delta"] = {"add": [], "retire": []}
    if changed_boundary:
        candidates[0]["graph"]["artifact_paths"] = ["src"]
    result = publish_plan(task_ref=worker, _connection_context=context, status="completed",
        summary="Two complete product alternatives", scope="Product", candidates=candidates,
        artifact=observation(), risks=[], unresolved=[])
    assert result["published"] and not result["replayed"]
    assert read_scope(task_ref=task, responsibility="delivery", _connection_context={})["data"]["nodes"] == []
    with pytest.raises(V12ServiceError):
        open_plan_review(task_ref=task, prompt="Too early", prompt_language="en")
    finish_observation(store, task, "validate-candidate")
    review = open_plan_review(task_ref=task, prompt="Choose offline or online and approve, revise, or cancel.", prompt_language="en")
    assert review["data"]["alternatives"] == [{"key": item["key"], "consequences": item["consequences"]} for item in candidates]
    assert review["data"]["human_view"]["status"] == "ready"
    return store, task


@pytest.mark.parametrize("branch", ["offline", "online"])
@pytest.mark.parametrize("empty_delta", [False, True])
def test_one_response_selects_applies_and_approves_only_one_graph(node_case, monkeypatch, branch, empty_delta):
    store, task = prepared(node_case, monkeypatch, empty_delta=empty_delta)
    request = dict(task_ref=task, outcome="approve", branch_key=branch,
        response_original=f"I choose {branch} and approve it.", user_language="en")
    report_count = store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0])
    result = record_plan_review(**request)
    assert not result["replayed"]
    assert result["effect"]["effective_revision"] == 2
    assert result["effect"]["reconciliation_required"] is False
    assert record_plan_review(**request)["replayed"]
    assert frontend_state(task) == "ready"
    current = store._read(lambda c: candidate_family.current_contract(c, node_case[1]["task_id"]))
    expected = [] if branch == "offline" and empty_delta else ["Works " + branch]
    assert current[0]["acceptance"] == expected
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM user_decisions WHERE subject_type='plan'").fetchone()[0]) == 1
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]) == report_count
    selection = store._read(lambda c: dict(c.execute("SELECT * FROM plan_candidate_selections").fetchone()))
    assert selection["branch_key"] == branch
    proof = store._read(lambda c: dict(c.execute("SELECT * FROM execution_nodes WHERE graph_id=? AND node_key='validate-candidate'", (selection["graph_id"],)).fetchone()))
    assert proof["state"] == "resolved" and proof["facts_json"] == "[]" and proof["assignment_id"] is None
    assert read_evidence(task_ref=task, report_policy="active_plan")["data"]["reports"][0]["report_type"] == "plan"
    with pytest.raises(V12ServiceError):
        record_plan_review(**{**request, "branch_key": "online" if branch == "offline" else "offline"})


@pytest.mark.parametrize("branch", [None, "missing", "I approve offline"])
def test_invalid_selection_rolls_back_entire_decision(node_case, monkeypatch, branch):
    store, task = prepared(node_case, monkeypatch)
    before = store._read(lambda c: list(c.iterdump()))
    with pytest.raises(V12ServiceError):
        record_plan_review(task_ref=task, outcome="approve", branch_key=branch,
            response_original="Choose offline", user_language="en")
    assert store._read(lambda c: list(c.iterdump())) == before


def test_selection_storage_failure_leaves_no_new_revision_or_approval(node_case, monkeypatch):
    store, task = prepared(node_case, monkeypatch)
    store._write(lambda c: c.execute("CREATE TRIGGER fail_selection BEFORE INSERT ON plan_candidate_selections BEGIN SELECT RAISE(ABORT,'fixture'); END"))
    before = store._read(lambda c: list(c.iterdump()))
    with pytest.raises(V12ServiceError):
        record_plan_review(task_ref=task, outcome="approve", branch_key="offline",
            response_original="Approve offline", user_language="en")
    assert store._read(lambda c: list(c.iterdump())) == before


def test_artifact_change_after_review_cannot_consume_family_approval(node_case, monkeypatch):
    store, task = prepared(node_case, monkeypatch)
    store._write(lambda c: graph_ledger._seal(c, task_id=node_case[1]["task_id"], revision=1,
        observation=observation("b" * 64, "b" * 64), source_assignment_id=None))
    before = store._read(lambda c: list(c.iterdump()))
    with pytest.raises(V12ServiceError) as stale:
        record_plan_review(task_ref=task, outcome="approve", branch_key="offline",
            response_original="Approve offline", user_language="en")
    assert stale.value.code == "clarification_binding_stale"
    assert store._read(lambda c: list(c.iterdump())) == before


def test_selected_boundary_is_observed_before_implementation_without_revalidating_family(node_case, monkeypatch):
    store, task = prepared(node_case, monkeypatch, changed_boundary=True)
    record_plan_review(task_ref=task, outcome="approve", branch_key="offline",
        response_original="Approve offline with the source boundary", user_language="en")
    assert frontend_state(task) == "waiting"
    worker, context = dispatch_and_consume(task, nodes=["baseline-candidate"])
    payload = baseline_content()
    payload["node_coverage"][0]["node"] = "baseline-candidate"
    payload["node_coverage"][0]["coverage"][0]["verification"][0]["check_key"] = "boundary"
    payload["artifact"]["boundary"] = observation("b" * 64, "b" * 64)
    assert publish_result(task_ref=worker, _connection_context=context, **payload)["published"]
    assert frontend_state(task) == "ready"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments WHERE nodes_json='[\"validate-candidate\"]'").fetchone()[0]) == 1
