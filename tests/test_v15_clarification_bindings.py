"""Current aggregate binding tests; no alternate decision mutation ledger."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from cortex_runtime.domain_kernel import DecisionAggregate
from cortex_runtime.v12_store import V12StoreError
from test_node_assignment_receipts import node_case


def question(store, task):
    return DecisionAggregate(store).open_clarification(task_id=task,
        prompt="Confirm the observed fact?", prompt_language="en",
        subject_type="task", subject_id=task)["binding"]["clarification_binding"]


def answer(store, task, binding, response="Confirmed."):
    return DecisionAggregate(store).record_clarification(task_id=task,
        binding_ref=binding, response_original=response, user_language="en")


def steer(store, task, delta):
    return DecisionAggregate(store).record_direct_steering(task_id=task,
        response_original="Change the product contract.", user_language="en",
        steering_delta=delta, expected_revision=1)


def test_issue_consume_replay_and_conflict(node_case):
    store, args = node_case
    task = args["task_id"]
    binding = question(store, task)
    assert question(store, task) == binding
    first = answer(store, task, binding)
    repeated = answer(store, task, binding)
    assert not first["replayed"] and repeated["replayed"]
    assert first["decision"] == repeated["decision"]
    with pytest.raises(V12StoreError) as failure:
        answer(store, task, binding, "A different answer.")
    assert failure.value.code == "command_conflict"


def test_revision_invalidates_unanswered_binding(node_case):
    store, args = node_case
    task = args["task_id"]
    binding = question(store, task)
    steer(store, task, {"add": [{"category": "outcome", "text": "New feature",
        "acceptance": [], "constraints": [], "verification": []}]})
    with pytest.raises(V12StoreError) as failure:
        answer(store, task, binding)
    assert failure.value.code == "clarification_binding_stale"
    assert store._read(lambda c: store._pending_user_decisions(c, task)) == []


def test_complete_delta_rejections_are_atomic(node_case):
    store, args = node_case
    task = args["task_id"]
    item = store._read(lambda c: store._effective_contract(c, task))["items"][0]
    complete = {"text": "Product", "acceptance": [], "constraints": [], "verification": []}
    deltas = [
        {"add": [], "retire_item_refs": [item["item_ref"]]},
        {"add": [{"category": "outcome", **complete}]},
        {"add": [{"category": "outcome_replacement", "outcome_ref": item["item_ref"], **complete}]},
    ]
    before = store._read(lambda c: list(c.iterdump()))
    for delta in deltas:
        with pytest.raises(V12StoreError) as failure:
            steer(store, task, delta)
        assert failure.value.code == "invalid_argument"
        assert store._read(lambda c: list(c.iterdump())) == before


def test_concurrent_consumption_commits_one_decision(node_case):
    store, args = node_case
    task = args["task_id"]
    binding = question(store, task)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: answer(store, task, binding), range(2)))
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert results[0]["decision"] == results[1]["decision"]
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM user_decisions").fetchone()[0]) == 1


@pytest.mark.parametrize("category", ["acceptance", "verification", "constraint", "requirement"])
def test_obsolete_partial_field_steering_is_rejected(node_case, category):
    store, args = node_case
    before = store._read(lambda c: list(c.iterdump()))
    with pytest.raises(V12StoreError) as failure:
        steer(store, args["task_id"], {"add": [{"category": category,
            "text": "Incomplete replacement", "outcome_ref": "o_0123456789ab"}]})
    assert failure.value.code == "invalid_argument"
    assert store._read(lambda c: list(c.iterdump())) == before


def test_old_service_entry_points_are_absent():
    from cortex_runtime import v12_service
    from cortex_runtime.v12_store import V12Store
    assert not hasattr(v12_service, "record_user_decision")
    assert not hasattr(v12_service, "set_governance_mode")
    assert not hasattr(V12Store, "set_governance_mode")
