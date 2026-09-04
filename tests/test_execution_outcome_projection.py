"""Current graph evidence, not legacy report counters, determines completion."""
from copy import deepcopy

import pytest

from cortex_runtime.domain_api import publish_result, read_state, close_task
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.v12_service import V12ServiceError
from test_domain_public_api_contract import PROVENANCE
from test_node_assignment_receipts import node_case
from test_typed_closure import review
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content


def test_completed_baseline_is_not_completed_product_and_closure_retains_that_fact(node_case, monkeypatch):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    before = read_state(task_ref=task)["data"]
    assert before["coverage_status"] == "incomplete"
    worker_ref, context = dispatch_and_consume(task, nodes=["baseline"])
    result = publish_result(task_ref=worker_ref, _connection_context=context, **baseline_content())
    assert result["published"] and not result["replayed"]
    observed = read_state(task_ref=task)["data"]
    assert observed["coverage_status"] == "incomplete"
    assert observed["coverage_status_counts"] == {"unverified": 1}
    assert observed["node_state_counts"]["complete"] == 1
    review(task)
    with pytest.raises(V12ServiceError) as rejected:
        close_task(task_ref=task, verdict="ready")
    assert rejected.value.code == "closure_not_ready"
    with pytest.raises(V12ServiceError):
        close_task(task_ref=task, verdict="not_ready", completion_notes=["Only the baseline was observed."])
    after = read_state(task_ref=task)["data"]
    assert after["closure_record_status"] == "not_recorded"
    assert after["coverage_status_counts"] == observed["coverage_status_counts"]
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]) == 1


@pytest.mark.parametrize("defect", ["old_schema", "empty_verification", "contradictory_completion"])
def test_invalid_report_cannot_create_a_completion_signal(node_case, monkeypatch, defect):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    worker_ref, context = dispatch_and_consume(task, nodes=["baseline"])
    content = deepcopy(baseline_content())
    if defect == "old_schema":
        content["schema"] = "cortex/report/result/v1"
    elif defect == "empty_verification":
        content["node_coverage"][0]["coverage"][0]["verification"] = []
    else:
        content["node_coverage"][0]["coverage"][0]["status"] = "partial"
    with pytest.raises((V12ServiceError, TypeError)):
        publish_result(task_ref=worker_ref, _connection_context=context, **content)
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]) == 0
    assert read_state(task_ref=task)["data"]["coverage_status"] == "incomplete"
