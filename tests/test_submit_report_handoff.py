"""A typed plan receipt leads to a validated, digest-bound review packet."""
from plan_fixtures import ordinary_candidates
import hashlib
from pathlib import Path
import pytest

from cortex_runtime.domain_api import (
    assess_governance, open_plan_review, publish_plan, publish_result,
)
from cortex_runtime.v12_contract import task_ref
from cortex_runtime.v12_service import V12ServiceError
from test_domain_public_api_contract import PROVENANCE
from test_node_assignment_receipts import node_case
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content
from test_publication_projection_repair import content_for, document_graph
from test_graph_ledger import observation


def test_plan_handoff_contains_verified_current_graph_view(node_case, monkeypatch):
    store, args = node_case
    task = task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    assess_governance(task_ref=task, mode="minimal", user_review_requested=True)
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    publish_result(task_ref=baseline, _connection_context=worker, **baseline_content())
    planner, worker = dispatch_and_consume(task, bootstrap={"kind": "planning"},
                                          responsibility="planning", profile="planner")
    receipt = publish_plan(task_ref=planner, _connection_context=worker, status="completed",
        summary="Complete product plan.", scope="Product only.", candidates=ordinary_candidates(document_graph()),
        artifact=observation(), risks=[], unresolved=[])
    assert receipt["published"] and not receipt["replayed"]
    before = store._read(lambda c: c.execute("SELECT COUNT(*) FROM clarification_bindings").fetchone()[0])
    with pytest.raises(V12ServiceError) as premature:
        open_plan_review(task_ref=task, prompt="Review the product plan.", prompt_language="en")
    assert premature.value.code == "approval_view_not_ready"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM clarification_bindings").fetchone()[0]) == before
    validator, worker = dispatch_and_consume(task, nodes=["validate-candidate"])
    publish_result(task_ref=validator, _connection_context=worker, **content_for(store, worker, "a" * 64))
    packet = open_plan_review(task_ref=task, prompt="Review the product plan.", prompt_language="en")
    view = packet["data"]["human_view"]
    assert view["status"] == "ready"
    link = view["markdown_link"]
    assert link.startswith("[Open plan revision](") and link.endswith(")")
    path = Path(link.split("](", 1)[1][:-1])
    body = path.read_bytes()
    assert b"## Execution dependencies" in body and b"## Node: implementation" in body
    assert b"## Node: documentation" in body
    rows = store._read(lambda c: c.execute(
        "SELECT content_digest FROM projection_files WHERE task_id=? AND relative_path LIKE 'plans/revisions/%'",
        (args["task_id"],)).fetchall())
    assert len(rows) == 1
    assert rows[0][0] == "sha256:" + hashlib.sha256(body).hexdigest()
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM approval_handles").fetchone()[0]) == 1
