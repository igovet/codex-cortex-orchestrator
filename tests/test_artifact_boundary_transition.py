"""Candidate path/method changes require a stable, explicit successor baseline."""
from plan_fixtures import ordinary_candidates
import json
import pytest

from cortex_runtime import graph_ledger
from cortex_runtime.domain_api import publish_plan, publish_result, read_scope
from cortex_runtime.v12_contract import task_ref as public_task_ref
from cortex_runtime.v12_service import V12ServiceError
from test_domain_public_api_contract import PROVENANCE
from test_execution_graph_integrity import graph
from test_graph_ledger import observation
from test_node_assignment_receipts import node_case
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content


@pytest.mark.parametrize("method", ["git_content_v1", "path_manifest_v1"])
@pytest.mark.parametrize("complete", [False, True])
def test_changed_boundary_precedes_candidate_validation(node_case, monkeypatch, method, complete):
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    publish_result(task_ref=baseline, _connection_context=worker, **baseline_content())
    planner, worker = dispatch_and_consume(task, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
    value = graph()
    value.update(artifact_paths=["src"], fingerprint_method=method)
    assert publish_plan(task_ref=planner, status="completed", summary="Product plan with declared source boundary", scope="Product",
        candidates=ordinary_candidates(value), artifact=observation(), risks=[], unresolved=[], _connection_context=worker)["published"]
    nodes = read_scope(task_ref=task, responsibility="evidence", _connection_context={})["data"]["nodes"]
    assert next(item for item in nodes if item["node"] == "baseline-candidate")["state"] == "ready"
    assert next(item for item in nodes if item["node"] == "validate-candidate")["state"] == "waiting"
    with pytest.raises(V12ServiceError):
        dispatch_and_consume(task, nodes=["validate-candidate"])
    observer, worker = dispatch_and_consume(task, nodes=["baseline-candidate"])
    scope = store._read(lambda c: graph_ledger.assignment_scope(c, worker["assignment_id"]))
    assert scope["artifact"]["paths"] == ["."]
    assert scope["artifact"]["method"] == "git_content_v1"
    assert scope["artifact"]["boundary_target"] == {"method": method, "paths": ["src"]}
    content = baseline_content()
    content["node_coverage"][0]["node"] = "baseline-candidate"
    content["node_coverage"][0]["coverage"][0]["verification"][0]["check_key"] = "boundary"
    content["artifact"]["boundary"] = dict(observation("b" * 64, "b" * 64), method=method)
    if not complete:
        content.update(status="partial", unresolved=["Semantic boundary check is incomplete."])
    assert publish_result(task_ref=observer, _connection_context=worker, **content)["published"]
    count, current = store._read(lambda c: (c.execute("SELECT COUNT(*) FROM artifact_generations").fetchone()[0],
        dict(c.execute("SELECT * FROM artifact_generations WHERE generation_key=(SELECT generation_key FROM project_integrity)").fetchone())))
    assert count == (2 if complete else 1)
    assert json.loads(current["paths_json"]) == (["src"] if complete else ["."])
    if not complete:
        with pytest.raises(V12ServiceError):
            dispatch_and_consume(task, nodes=["validate-candidate"])
        return
    assert current["method"] == method and current["fingerprint"] == "b" * 64
    validator, worker = dispatch_and_consume(task, nodes=["validate-candidate"])
    scope = store._read(lambda c: graph_ledger.assignment_scope(c, worker["assignment_id"]))
    assert scope["artifact"]["paths"] == ["src"]
    assert scope["artifact"]["method"] == method
    assert scope["artifact"]["target_fingerprint"] == "b" * 64


def test_ordinary_worker_cannot_change_boundary(node_case, monkeypatch):
    store, args = node_case
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    baseline, worker = dispatch_and_consume(public_task_ref(args["task_id"]), nodes=["baseline"])
    content = baseline_content()
    content["artifact"]["boundary"] = observation("b" * 64, "b" * 64)
    with pytest.raises(V12ServiceError):
        publish_result(task_ref=baseline, _connection_context=worker, **content)
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]) == 0


def test_boundary_worker_procedures_use_real_persisted_file_manifests(node_case, monkeypatch):
    import subprocess
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    source = store.project_root / "src"
    source.mkdir()
    (source / "product.txt").write_text("Product fixture\n", encoding="utf-8")
    def procedures(worker):
        return store.read_delegation(delegation_id=worker["assignment_id"], after_sequence=0, limit=1)["worker_brief"]["assignment"]["artifact"]
    def observe(command, baseline=None):
        result = subprocess.run([*command, *(["--compare", baseline] if baseline else [])], check=True, capture_output=True, text=True)
        assert not result.stderr
        data = json.loads(result.stdout)
        assert data["state"] == "observed"
        return data
    def pair(command):
        start = observe(command)
        end = observe(command, start["fingerprint"])
        return {"method": start["method"], "start": start["fingerprint"], "end": end["fingerprint"], "changes": end["comparisons"][0]["changes"]}
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    first = pair(procedures(worker)["worker_procedure"]["command"])
    content = baseline_content()
    content["artifact"] = first
    publish_result(task_ref=baseline, _connection_context=worker, **content)
    planner, worker = dispatch_and_consume(task, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
    value = graph()
    value.update(fingerprint_method="path_manifest_v1", artifact_paths=["src"])
    planning_observation = pair(procedures(worker)["worker_procedure"]["command"])
    publish_plan(task_ref=planner, status="completed", summary="Source-only product plan", scope="Product",
        candidates=ordinary_candidates(value), artifact=planning_observation, risks=[], unresolved=[], _connection_context=worker)
    boundary, worker = dispatch_and_consume(task, nodes=["baseline-candidate"])
    commands = procedures(worker)
    old_start = observe(commands["worker_procedure"]["command"], first["end"])
    new_pair = pair(commands["boundary_procedure"]["command"])
    old_end = observe(commands["worker_procedure"]["command"], old_start["fingerprint"])
    content["node_coverage"][0]["node"] = "baseline-candidate"
    content["node_coverage"][0]["coverage"][0]["verification"][0]["check_key"] = "boundary"
    content["artifact"] = {"method": old_start["method"], "start": old_start["fingerprint"], "end": old_end["fingerprint"],
        "changes": old_end["comparisons"][0]["changes"], "boundary": new_pair}
    assert content["artifact"]["start"] != new_pair["start"]
    assert publish_result(task_ref=boundary, _connection_context=worker, **content)["published"]
    _, verifier = dispatch_and_consume(task, nodes=["validate-candidate"])
    verified = observe(procedures(verifier)["worker_procedure"]["command"], new_pair["end"])
    assert verified["fingerprint"] == new_pair["end"]
    assert verified["comparisons"][0]["changes"]["count"] == 0


def test_new_task_baseline_restores_project_root_and_old_graph_rebases(node_case, monkeypatch):
    from cortex_runtime.domain_api import open_task, assess_governance
    store, args = node_case
    task = public_task_ref(args["task_id"])
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    baseline, worker = dispatch_and_consume(task, nodes=["baseline"])
    publish_result(task_ref=baseline, _connection_context=worker, **baseline_content())
    planner, worker = dispatch_and_consume(task, bootstrap={"kind": "planning"}, responsibility="planning", profile="planner")
    value = graph()
    value["artifact_paths"] = ["src"]
    publish_plan(task_ref=planner, status="completed", summary="Source boundary plan", scope="Product", candidates=ordinary_candidates(value),
        artifact=observation(), risks=[], unresolved=[], _connection_context=worker)
    def transition(task_ref, node, previous, successor):
        ref, context = dispatch_and_consume(task_ref, nodes=[node])
        content = baseline_content()
        content["node_coverage"][0]["node"] = node
        content["node_coverage"][0]["coverage"][0]["verification"][0]["check_key"] = "baseline" if node == "baseline" else "boundary"
        content["artifact"] = observation(previous * 64, previous * 64)
        content["artifact"]["boundary"] = observation(successor * 64, successor * 64)
        assert publish_result(task_ref=ref, _connection_context=context, **content)["published"]
    transition(task, "baseline-candidate", "a", "b")
    another = open_task(project_root=str(store.project_root), request_original="Inspect another product scope.", user_language="en",
        outcomes=[{"outcome": "Product", "acceptance": [], "constraints": [], "verification": []}], constraints=["Read-only fixture"])["task_ref"]
    assess_governance(task_ref=another, mode="minimal")
    transition(another, "baseline", "b", "c")
    assert store._read(lambda c: c.execute("SELECT paths_json FROM artifact_generations WHERE generation_key=(SELECT generation_key FROM project_integrity)").fetchone()[0]) == '["."]'
    scope = read_scope(task_ref=task, responsibility="evidence", _connection_context={})["data"]["nodes"]
    assert next(item for item in scope if item["node"] == "baseline-candidate")["state"] == "ready"
    assert next(item for item in scope if item["node"] == "validate-candidate")["state"] == "waiting"
    transition(task, "baseline-candidate", "c", "d")
    dispatch_and_consume(task, nodes=["validate-candidate"])
