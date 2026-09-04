"""Public typed publication remains repairable after derived-view IO fails."""
import errno

from plan_fixtures import ordinary_candidates
from unittest.mock import patch

import pytest

from cortex_runtime import graph_ledger
from cortex_runtime.domain_api import publish_plan, publish_result, publish_documentation, read_evidence
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.v12_service import V12ServiceError
from cortex_runtime.v12_projections import _safe_write
from test_domain_public_api_contract import PROVENANCE
from test_execution_graph_integrity import graph, node
from test_graph_ledger import observation
from test_node_assignment_receipts import node_case
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content


def document_graph():
    value = graph()
    implementation = node("implementation", contribution="product", provides=["artifact"])
    documentation = node("documentation", audit=True, requires=["artifact"], provides=["documentation_checked"],
        dependencies=[("implementation", ["artifact"])])
    documentation["kind"] = "documentation"
    value["nodes"] = [implementation, documentation]
    value["outcomes"] = [{"outcome": "Product", "all_of": ["product"]}]
    return value


def content_for(store, worker, fingerprint):
    scope = store._read(lambda c: graph_ledger.assignment_scope(c, worker["assignment_id"]))
    content = baseline_content()
    content["artifact"] = observation(fingerprint, fingerprint)
    content["node_coverage"] = [{"node": item["key"], "coverage": [{**subject, "status": "complete",
        "verification": [{"check_key": check["key"], "state": "executed", "summary": "Assigned check observed"} for check in item["checks"]]}
        for subject in ([{"kind": "contribution", "name": key} for key in item["contributions"]] + item["verifies"])]}
        for item in scope["nodes"]]
    return content


@pytest.mark.parametrize("after_write", [False, True])
@pytest.mark.parametrize("kind", ["plan", "result", "documentation"])
@pytest.mark.parametrize("transient", [False, True])
def test_lost_view_is_repaired_after_commit_without_second_report(node_case, monkeypatch, after_write, kind, transient):
    store, arguments = node_case
    task = public_task_ref(arguments["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    worker_ref, worker = dispatch_and_consume(task, nodes=["baseline"])
    payload = baseline_content()
    if kind != "result":
        publish_result(task_ref=worker_ref, _connection_context=worker, **payload)
        worker_ref, worker = dispatch_and_consume(task, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
        payload = dict(summary="Complete fixture plan", scope="Complete contract", candidates=ordinary_candidates(document_graph()),
            artifact=observation(), risks=[], unresolved=[], status="completed")
    if kind == "documentation":
        publish_plan(task_ref=worker_ref, _connection_context=worker, **payload)
        validator, verifier = dispatch_and_consume(task, nodes=["validate-candidate"])
        publish_result(task_ref=validator, _connection_context=verifier, **content_for(store, verifier, "a" * 64))
        implementer, implementation = dispatch_and_consume(task, nodes=["implementation"], responsibility="delivery")
        completed = content_for(store, implementation, "a" * 64)
        completed["artifact"] = observation("a" * 64, "b" * 64)
        publish_result(task_ref=implementer, _connection_context=implementation, **completed)
        worker_ref, worker = dispatch_and_consume(task, nodes=["documentation"], profile="technical_writer")
        payload = content_for(store, worker, "b" * 64)
        payload.pop("outcome")
        payload.pop("changes")
        payload.update(findings=[], recommendations=[])
    publish = {"plan": publish_plan, "result": publish_result, "documentation": publish_documentation}[kind]
    args = dict(task_ref=worker_ref, _connection_context=worker, **payload)
    def count(table):
        assert table in {"reports", "report_operations", "approval_handles"}
        return store._read(lambda c: c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    original = count("reports")
    approvals = count("approval_handles")
    writes = []
    def fail_view(*args, **kwargs):
        assert count("reports") == count("report_operations") == original + 1
        writes.append(True)
        if transient and len(writes) > 1:
            return _safe_write(*args, **kwargs)
        if after_write:
            _safe_write(*args, **kwargs)
        if transient:
            raise OSError(errno.EIO, "injected transient derived-view failure")
        raise OSError("injected derived-view failure")
    with patch("cortex_runtime.v12_projections._safe_write", side_effect=fail_view):
        if transient:
            repaired = publish(**args)
            assert repaired["published"] and repaired["replayed"] is False
            assert len(writes) == 2
        else:
            with pytest.raises(V12ServiceError) as failure:
                publish(**args)
            assert failure.value.code == "storage_unavailable"
    assert count("reports") == count("report_operations") == original + 1
    if not transient:
        assert count("approval_handles") == approvals
        repaired = publish(**args)
        assert repaired["published"] and repaired["replayed"]
    assert count("reports") == count("report_operations") == original + 1
    assert count("approval_handles") == approvals + int(kind == "plan")
    context = {}
    pages = [read_evidence(task_ref=task, report_policy="all_finalized", _connection_context=context)]
    while pages[-1]["has_more"]:
        pages.append(read_evidence(task_ref=task, report_policy="all_finalized", continue_=True, _connection_context=context))
    views = pages[-1]["data"]["human_views"]
    assert len(views) == original + 1
    assert all(view["status"] == "ready" and view["markdown_link"].startswith("[Open ") for view in views)


@pytest.mark.parametrize("failure", [ValueError("unsafe path"), FileExistsError("external edit"),
    PermissionError(errno.EACCES, "denied"), OSError(errno.EIO, "persistent IO")])
def test_projection_repair_never_bypasses_integrity_or_retries_unboundedly(node_case, monkeypatch, failure):
    _, arguments = node_case
    task = public_task_ref(arguments["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    worker_ref, worker = dispatch_and_consume(task, nodes=["baseline"])
    with patch("cortex_runtime.v12_projections._safe_write", side_effect=failure) as write:
        with pytest.raises(V12ServiceError) as rejected:
            publish_result(task_ref=worker_ref, _connection_context=worker, **baseline_content())
    assert rejected.value.code == "storage_unavailable"
    assert write.call_count == (2 if getattr(failure, "errno", None) == errno.EIO else 1)
