"""Closure decision boundaries on the typed public protocol."""
import pytest

from cortex import PUBLIC_TOOLS
from cortex_runtime.domain_api import (
    close_task, open_clarification, publish_result, record_clarification,
)
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.v12_service import V12ServiceError
from test_domain_public_api_contract import PROVENANCE
from test_node_assignment_receipts import node_case
from test_typed_closure import review
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content


@pytest.mark.parametrize("event", ["assignment", "publication"])
def test_accepted_closure_review_is_invalidated_by_typed_execution(node_case, monkeypatch, event):
    _, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    if event == "publication":
        worker_ref, worker = dispatch_and_consume(task, nodes=["baseline"])
    review(task)
    if event == "assignment":
        dispatch_and_consume(task, nodes=["baseline"])
    else:
        publish_result(task_ref=worker_ref, _connection_context=worker, **baseline_content())
    with pytest.raises(V12ServiceError) as stale:
        close_task(task_ref=task, verdict="ready")
    assert stale.value.code == "closure_review_stale"


def test_closure_choices_do_not_turn_factual_answer_into_permission(node_case):
    _, args = node_case
    task = public_task_ref(args["task_id"])
    schema = PUBLIC_TOOLS["open_clarification"]["inputSchema"]
    assert schema["properties"]["options"]["items"]["enum"] == ["revise", "close"]
    record_schema = PUBLIC_TOOLS["record_clarification"]["inputSchema"]
    assert "outcome" not in record_schema["required"]
    assert record_schema["properties"]["outcome"]["enum"] == ["revise", "close"]
    with pytest.raises(V12ServiceError, match="exactly revise and close"):
        open_clarification(task_ref=task, prompt="Review this result.", prompt_language="en",
            purpose="closure_review", options=["close", "revise"])
    opened = open_clarification(task_ref=task, prompt="Was the supplied fixture captured before or after the reported event?",
        prompt_language="en", purpose="clarification")
    assert opened["state"] == "pending_clarification"
    with pytest.raises(V12ServiceError, match="ordinary clarification"):
        record_clarification(task_ref=task, response_original="Before the event.", user_language="en", outcome="close")
    answered = record_clarification(task_ref=task, response_original="Before the event.", user_language="en")
    assert answered["state"] == "clarification_recorded"
    open_clarification(task_ref=task, prompt="Check the current result: revise or close?", prompt_language="en",
        purpose="closure_review", options=["revise", "close"])
    with pytest.raises(V12ServiceError, match="closure review requires"):
        record_clarification(task_ref=task, response_original="Maybe.", user_language="en")
    with pytest.raises(V12ServiceError):
        close_task(task_ref=task, verdict="ready")
