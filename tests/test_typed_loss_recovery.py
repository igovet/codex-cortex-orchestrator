"""Signed native-loss observation and atomic public recovery, not live evidence."""
import re
import pytest

from cortex_runtime import native_observation as native, graph_ledger
from cortex_runtime.domain_api import assess_governance, open_assignment, read_scope, read_task, publish_result
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.v12_service import V12ServiceError
from test_domain_public_api_contract import PROVENANCE
from test_node_assignment_receipts import node_case
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content
from test_graph_ledger import observation


def test_recovery_mode_is_not_a_caller_parameter():
    import inspect
    from jsonschema import ValidationError, validate
    from cortex_runtime.public_contracts import build_public_contracts
    schema = build_public_contracts()["open_assignment"]["inputSchema"]
    assert "recover" not in schema["properties"]
    assert "recover" not in inspect.signature(open_assignment).parameters
    selection = {"task_ref": "t_0123456789ab", "profile_name": "general",
                 "model": "gpt-5.6-luna", "reasoning_effort": "high", "nodes": ["baseline"]}
    validate(selection, schema)
    with pytest.raises(ValidationError):
        validate({**selection, "recover": True}, schema)


@pytest.mark.parametrize("mutator", [False, True])
def test_confirmed_loss_reconciles_before_exact_replacement(node_case, monkeypatch, tmp_path, mutator):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    selected, responsibility = "baseline", "evidence"
    if mutator:
        assess_governance(task_ref=task, mode="minimal", execution_route="minimal", minimal_mode="mutating")
        publish_result(task_ref=baseline, _connection_context=worker, **baseline_content())
        selected, responsibility = "minimal-execution", "delivery"
        _, worker = dispatch_and_consume(task, nodes=[selected], responsibility=responsibility)
    lost = worker["assignment_id"]
    name = store._read(lambda c: c.execute("SELECT protected_task_name FROM execution_assignments WHERE assignment_id=?", (lost,)).fetchone()[0])
    plugin_data = tmp_path / "native"
    assert native.bind_task(plugin_data, task_digest=native.digest(task), session_digest=native.digest("coordinator"))
    context = {"_native_plugin_data": plugin_data}
    selection = dict(task_ref=task, profile_name="general", model="gpt-5.6-luna", reasoning_effort="high", nodes=[selected], _connection_context=context)
    def scope():
        return read_scope(task_ref=task, responsibility=responsibility, _connection_context=context)
    scope()
    with pytest.raises(V12ServiceError) as absent_evidence:
        open_assignment(**selection)
    assert absent_evidence.value.code == "assignment_not_ready"
    assert store._read(lambda c: c.execute("SELECT state FROM execution_assignments WHERE assignment_id=?", (lost,)).fetchone()[0]) == "active"
    def observe(children):
        assert native.record_projection(plugin_data, task_digest=native.digest(task), session_digest=native.digest("coordinator"),
            revision=1, barrier_epoch=0, response={"agents": [{"agent_name": "/root", "agent_status": "running"}, *children]}, arguments={})
    observe([{"agent_name": "/root/" + name, "agent_status": "running"}])
    scope()
    with pytest.raises(V12ServiceError) as present:
        open_assignment(**selection)
    assert present.value.details["reason"] == "native_worker_present"
    observe([])
    scope()
    dispatch = open_assignment(**selection)
    assert not dispatch["replayed"]
    assert open_assignment(**selection)["replayed"]
    assert store._read(lambda c: c.execute("SELECT state FROM execution_assignments WHERE assignment_id=?", (lost,)).fetchone()[0]) == "lost"
    recovered_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', dispatch["native_dispatch"]["message"]).group(1)
    recovery = {}
    page = read_task(task_ref=recovered_ref, _connection_context=recovery)
    while page["has_more"]:
        page = read_task(task_ref=recovered_ref, continue_=True, _connection_context=recovery)
    assigned = store._read(lambda c: graph_ledger.assignment_scope(c, recovery["assignment_id"]))
    assert assigned["artifact"]["reconciliation"]
    assert assigned["nodes"][0]["execution_mode"] == "read_only"
    with pytest.raises(V12ServiceError):
        dispatch_and_consume(task, nodes=[selected], responsibility=responsibility)
    content = baseline_content()
    content["artifact"] = observation("b" * 64, "b" * 64)
    if mutator:
        content["artifact"]["baseline_changes"] = observation("a" * 64, "b" * 64)["changes"]
    content["node_coverage"] = [{"node": assigned["nodes"][0]["key"], "coverage": [{"kind": "outcome", "name": "Product", "status": "complete", "verification": [{"check_key": "reconciliation", "state": "executed", "summary": "Unpublished effects observed without attribution; current baseline is stable."}]}]}]
    assert publish_result(task_ref=recovered_ref, _connection_context=recovery, **content)["published"]
    if mutator:
        _, replacement = dispatch_and_consume(task, nodes=[selected], responsibility=responsibility)
        parent = store._read(lambda c: c.execute("SELECT parent_delegation_id FROM delegations WHERE delegation_id=?", (replacement["assignment_id"],)).fetchone()[0])
        assert parent == lost
    else:
        assert store._read(lambda c: c.execute("SELECT state FROM execution_nodes WHERE node_key='baseline'").fetchone()[0]) == "resolved"
        dispatch_and_consume(task, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_publications WHERE assignment_id=?", (lost,)).fetchone()[0]) == 0


def test_parallel_lost_readers_share_reconciliation_without_worker_limit(node_case, monkeypatch, tmp_path):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    publish_result(task_ref=baseline, _connection_context=worker, **baseline_content())
    lost = []
    for question in ("Inspect frontend structure", "Inspect database structure"):
        _, reader = dispatch_and_consume(task, bootstrap={"kind": "discovery", "question": question})
        lost.append(reader["assignment_id"])
    plugin_data = tmp_path / "native"
    assert native.bind_task(plugin_data, task_digest=native.digest(task), session_digest=native.digest("coordinator"))
    assert native.record_projection(plugin_data, task_digest=native.digest(task), session_digest=native.digest("coordinator"),
        revision=1, barrier_epoch=0, response={"agents": [{"agent_name": "/root", "agent_status": "running"}]}, arguments={})
    context = {"_native_plugin_data": plugin_data}
    current = read_scope(task_ref=task, responsibility="evidence", _connection_context=context)
    selected = [item["node"] for item in current["data"]["nodes"] if item.get("loss_evidence", {}).get("confirmed")]
    assert selected == ["discovery-1", "discovery-2"]
    dispatch = open_assignment(task_ref=task, profile_name="explorer", model="gpt-5.6-luna", reasoning_effort="high",
        nodes=selected, _connection_context=context)
    ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', dispatch["native_dispatch"]["message"]).group(1)
    recovery = {}
    page = read_task(task_ref=ref, _connection_context=recovery)
    while page["has_more"]:
        page = read_task(task_ref=ref, continue_=True, _connection_context=recovery)
    assigned = store._read(lambda c: graph_ledger.assignment_scope(c, recovery["assignment_id"]))
    payload = baseline_content()
    payload["node_coverage"][0]["node"] = assigned["nodes"][0]["key"]
    payload["node_coverage"][0]["coverage"][0]["verification"][0]["check_key"] = "reconciliation"
    payload["artifact"]["baseline_changes"] = payload["artifact"]["changes"]
    assert publish_result(task_ref=ref, _connection_context=recovery, **payload)["published"]
    replacements = []
    for key in selected:
        _, replacement = dispatch_and_consume(task, nodes=[key])
        replacements.append(replacement["assignment_id"])
    assert len(set(replacements)) == 2
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments WHERE state='lost'").fetchone()[0]) == 2
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments WHERE state='active'").fetchone()[0]) == 2
    for replacement, original in zip(replacements, lost):
        parent, reports = store._read(lambda c: c.execute("SELECT parent_delegation_id,input_report_ids_json FROM delegations WHERE delegation_id=?", (replacement,)).fetchone())
        assert parent == original
        recovery_report = store._read(lambda c: c.execute("SELECT report_id FROM execution_publications WHERE assignment_id=?", (recovery["assignment_id"],)).fetchone()[0])
        assert recovery_report in reports


def test_repeated_confirmed_loss_exhausts_without_error_or_another_native_dispatch(node_case, monkeypatch, tmp_path):
    from jsonschema import validate
    from cortex_runtime.public_contracts import build_public_contracts
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    assess_governance(task_ref=task, mode="minimal", execution_route="minimal", minimal_mode="mutating")
    ref, worker = dispatch_and_consume(task, nodes=["baseline"])
    publish_result(task_ref=ref, _connection_context=worker, **baseline_content())
    plugin_data = tmp_path / "native"
    assert native.bind_task(plugin_data, task_digest=native.digest(task), session_digest=native.digest("coordinator"))
    context = {"_native_plugin_data": plugin_data}
    selection = dict(task_ref=task, profile_name="general", model="gpt-5.6-luna", reasoning_effort="high",
                     nodes=["minimal-execution"], _connection_context=context)
    for attempt in range(3):
        _, lost = dispatch_and_consume(task, nodes=["minimal-execution"], responsibility="delivery")
        epoch = store._read(lambda c: c.execute("SELECT barrier_epoch FROM project_integrity").fetchone()[0])
        assert native.record_projection(plugin_data, task_digest=native.digest(task), session_digest=native.digest("coordinator"),
            revision=1, barrier_epoch=epoch, response={"agents": [{"agent_name": "/root", "agent_status": "running"}]}, arguments={})
        scoped = read_scope(task_ref=task, responsibility="delivery", _connection_context=context)
        evidence = next(item["loss_evidence"] for item in scoped["data"]["nodes"] if item["node"] == "minimal-execution")
        assert evidence["confirmed"] and evidence["budget_exhausted"] is (attempt == 2)
        before = store._read(lambda c: c.execute("SELECT COUNT(*) FROM delegations").fetchone()[0])
        result = open_assignment(**selection)
        validate(result, build_public_contracts()["open_assignment"]["outputSchema"])
        if attempt == 2:
            assert result == {"state": "exhausted", "dispatched": False, "nodes": ["minimal-execution"], "replayed": False}
            assert open_assignment(**selection) == {**result, "replayed": True}
            assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM delegations").fetchone()[0]) == before
            assert store._read(lambda c: c.execute("SELECT state,quiescent FROM execution_assignments WHERE assignment_id=?", (lost["assignment_id"],)).fetchone())[:] == ("lost", 1)
            assert store._read(lambda c: c.execute("SELECT state FROM worker_capabilities WHERE assignment_id=?", (lost["assignment_id"],)).fetchone()[0]) == "stale"
            assert not store._read(lambda c: graph_ledger.closure_evidence(c, args["task_id"]))["ready"]
            break
        ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', result["native_dispatch"]["message"]).group(1)
        worker = {}
        page = read_task(task_ref=ref, _connection_context=worker)
        while page["has_more"]:
            page = read_task(task_ref=ref, continue_=True, _connection_context=worker)
        assigned = store._read(lambda c: graph_ledger.assignment_scope(c, worker["assignment_id"]))
        content = baseline_content()
        content["node_coverage"][0]["node"] = assigned["nodes"][0]["key"]
        content["node_coverage"][0]["coverage"][0]["verification"][0]["check_key"] = "reconciliation"
        content["artifact"]["baseline_changes"] = content["artifact"]["changes"]
        assert publish_result(task_ref=ref, _connection_context=worker, **content)["published"]
