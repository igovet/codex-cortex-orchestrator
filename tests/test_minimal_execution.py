"""Typed minimal route and explicit review policy, not host/live evidence."""
import pytest

from cortex_runtime import graph_ledger
from cortex_runtime.domain_api import assess_governance, publish_result, read_scope
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.v12_service import V12ServiceError
from test_domain_public_api_contract import PROVENANCE
from test_node_assignment_receipts import node_case
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content


@pytest.mark.parametrize("mode", ["read_only", "mutating"])
def test_minimal_route_materializes_only_after_baseline(node_case, monkeypatch, mode):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    selection = dict(task_ref=task, mode="minimal", execution_route="minimal", minimal_mode=mode)
    assert not assess_governance(**selection)["replayed"]
    assert assess_governance(**selection)["replayed"]
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_graphs WHERE graph_kind='generated'").fetchone()[0]) == 0
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    publish_result(task_ref=baseline, _connection_context=worker, **baseline_content())
    responsibility = "delivery" if mode == "mutating" else "evidence"
    scope = read_scope(task_ref=task, responsibility=responsibility, _connection_context={})
    assert any(item["node"] == "minimal-execution" and item["state"] == "ready" for item in scope["data"]["nodes"])
    executor, worker = dispatch_and_consume(task, nodes=["minimal-execution"], responsibility=responsibility)
    content = baseline_content()
    content["node_coverage"] = [{"node": "minimal-execution", "coverage": [{"kind": "contribution", "name": "minimal-contribution-1", "status": "complete", "verification": [{"check_key": "acceptance", "state": "executed", "summary": "Complete original contract checked"}]}]}]
    assert publish_result(task_ref=executor, _connection_context=worker, **content)["published"]
    assert store._read(lambda c: graph_ledger.closure_evidence(c, args["task_id"]))["ready"]
    internal = store.inspect_task(task_id=args["task_id"], after_sequence=0, limit=1)
    assert internal["aggregate_coverage"]["status"] == "ready"
    assert internal["execution_outcome"]["outcome"] == "completed"
    assert internal["conformance_review"]["status"] == "ready"
    assert "assignment_scope" not in internal["aggregate_coverage"]
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports WHERE report_type='plan'").fetchone()[0]) == 0


@pytest.mark.parametrize("options", [
    {"mode": "full", "minimal_mode": "mutating"},
    {"mode": "minimal", "minimal_mode": "mutating", "risk_factors": ["Unresolved security risk"]},
    {"mode": "minimal", "minimal_mode": "read_only", "user_review_requested": True},
])
def test_ineligible_minimal_policy_rolls_back(node_case, options):
    store, args = node_case
    with pytest.raises(V12ServiceError) as failure:
        assess_governance(task_ref=public_task_ref(args["task_id"]), execution_route="minimal", **options)
    assert failure.value.code == "minimal_route_ineligible"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM governance_assessments").fetchone()[0]) == 1


def test_requested_review_persists_and_prevents_minimal_route(node_case):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    assess_governance(task_ref=task, mode="light", user_review_requested=True)
    assess_governance(task_ref=task, mode="minimal", rationale="Risk resolved; review preference unchanged")
    latest = store._read(lambda c: c.execute("SELECT p.user_review_requested FROM execution_policies p JOIN governance_assessments a ON a.assessment_id=p.assessment_id ORDER BY a.created_sequence DESC LIMIT 1").fetchone()[0])
    assert latest == 1
    with pytest.raises(V12ServiceError, match="without requested review"):
        assess_governance(task_ref=task, mode="minimal", execution_route="minimal", minimal_mode="read_only")


def test_identical_governance_selections_are_receipted_per_task(node_case):
    from cortex_runtime.domain_api import open_task
    store, _ = node_case
    another = open_task(project_root=str(store.project_root), request_original="Inspect a second product.", user_language="en",
        outcomes=[{"outcome": "Second product", "acceptance": [], "constraints": [], "verification": []}], constraints=["Read-only fixture"])["task_ref"]
    assert not assess_governance(task_ref=another, mode="minimal")["replayed"]
    assert assess_governance(task_ref=another, mode="minimal")["replayed"]
    assert store._read(lambda c: c.execute("SELECT COUNT(DISTINCT task_id) FROM governance_assessments").fetchone()[0]) == 2
