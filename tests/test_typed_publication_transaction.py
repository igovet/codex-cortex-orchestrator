"""Typed report integration with real assignment consumption and store IO."""
import re
from copy import deepcopy
from unittest.mock import patch

import pytest

from cortex_runtime.domain_api import read_task
from cortex_runtime.v12_store import V12StoreError
from cortex_runtime import graph_ledger
from test_node_assignment_receipts import node_case
from test_domain_public_api_contract import PROVENANCE
from test_graph_ledger import observation


def consume(store, args, monkeypatch):
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    result, _ = store.open_node_assignment(**args)
    native = result["dispatch_brief"]["native_dispatch"]["native_arguments"]
    worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', native["message"]).group(1)
    context = {}
    page = read_task(task_ref=worker_ref, _connection_context=context)
    while page["has_more"]:
        page = read_task(task_ref=worker_ref, continue_=True, _connection_context=context)
    return result["delegation"]["delegation_id"], context["continuation_ref"]


def baseline_content():
    return {"status": "completed", "summary": "Baseline observed", "outcome": "Stable project baseline",
        "documentation_impact": "Read-only baseline, no documentation changes", "changes": [], "risks": [], "unresolved": [],
        "node_coverage": [{"node": "baseline", "coverage": [{"kind": "outcome", "name": "Product", "status": "complete",
            "verification": [{"check_key": "baseline", "state": "executed", "summary": "Matching start/end observations"}]}]}], "artifact": observation()}


@pytest.mark.parametrize("missing", [True, False])
def test_baseline_cannot_omit_observed_artifact(node_case, monkeypatch, missing):
    store, args = node_case
    assignment, continuation = consume(store, args, monkeypatch)
    payload = baseline_content()
    if missing:
        del payload["artifact"]
    else:
        payload["artifact"] = None
    before = store._read(lambda c: list(c.iterdump()))
    with pytest.raises(V12StoreError) as failure:
        store.publish_node_report(delegation_id=assignment, continuation_ref=continuation,
            kind="result", content=payload)
    assert failure.value.code == "report_incomplete"
    assert store._read(lambda c: list(c.iterdump())) == before


def test_graph_report_and_receipt_commit_once_then_enable_discovery(node_case, monkeypatch):
    store, args = node_case
    assignment, continuation = consume(store, args, monkeypatch)
    payload = baseline_content()
    first = store.publish_node_report(delegation_id=assignment, continuation_ref=continuation, kind="result", content=payload)
    replay = store.publish_node_report(delegation_id=assignment, continuation_ref=continuation, kind="result", content=payload)
    assert first["published"] and not first["replayed"] and replay["replayed"]
    for table in ("reports", "report_operations", "execution_publications", "artifact_generations"):
        assert store._read(lambda c: c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) == 1
    altered = deepcopy(payload)
    altered["summary"] = "Changed semantic conclusion"
    with pytest.raises(V12StoreError) as failure:
        store.publish_node_report(delegation_id=assignment, continuation_ref=continuation, kind="result", content=altered)
    assert failure.value.details["reason"] == "report_operation_conflict"
    admission = store.node_admission_snapshot(graph_id=args["graph_id"])
    discovery_args = dict(args, node_keys=[], admission=admission, bootstrap_kind="discovery",
        bootstrap_question="Which schema is currently implemented?")
    dispatched, replayed = store.open_node_assignment(**discovery_args)
    again, replayed_again = store.open_node_assignment(**discovery_args)
    assert not replayed and replayed_again and dispatched == again
    assert dispatched["delegation"]["input_report_ids"] == [first["report"]["report_id"]]


def test_graph_publication_rolls_back_if_report_insert_fails(node_case, monkeypatch):
    store, args = node_case
    assignment, continuation = consume(store, args, monkeypatch)
    def inject(c):
        c.execute("CREATE TRIGGER fail_report BEFORE INSERT ON reports BEGIN SELECT RAISE(ABORT,'fixture'); END")
    store._write(inject)
    with pytest.raises(V12StoreError):
        store.publish_node_report(delegation_id=assignment, continuation_ref=continuation, kind="result", content=baseline_content())
    for table in ("reports", "report_operations", "execution_publications", "artifact_generations"):
        assert store._read(lambda c: c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) == 0
    scope = store._read(lambda c: graph_ledger.state_projection(c, args["graph_id"]))
    assert scope[0]["state"] == "active"


def test_projection_failure_keeps_one_typed_report_and_repairs_on_retry(node_case, monkeypatch):
    store, args = node_case
    assignment, continuation = consume(store, args, monkeypatch)
    payload = baseline_content()
    with patch("cortex_runtime.v12_projections._safe_write", side_effect=OSError("fixture")):
        with pytest.raises(V12StoreError) as failure:
            store.publish_node_report(delegation_id=assignment, continuation_ref=continuation, kind="result", content=payload)
    assert failure.value.code == "storage_unavailable"
    repaired = store.publish_node_report(delegation_id=assignment, continuation_ref=continuation, kind="result", content=payload)
    assert repaired["published"] and repaired["replayed"]
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]) == 1
