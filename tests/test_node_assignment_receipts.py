"""Integrated graph claim, delegation and dispatch share one durable receipt."""
import pytest

from cortex_runtime import graph_ledger
from cortex_runtime.domain_api import open_task, assess_governance, _task_store
from cortex_runtime.v12_store import V12StoreError
from test_domain_public_api_contract import PROVENANCE


@pytest.fixture
def node_case(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    project = tmp_path / "project"
    project.mkdir()
    task_ref = open_task(project_root=str(project), request_original="Inspect Product", user_language="en",
        outcomes=[{"outcome": "Product", "acceptance": [], "constraints": [], "verification": []}],
        constraints=["Fixture only"])["task_ref"]
    assess_governance(task_ref=task_ref, mode="minimal")
    store, task = _task_store(task_ref)
    graph_id = store._read(lambda c: c.execute("SELECT graph_id FROM execution_graphs WHERE task_id=?", (task,)).fetchone()[0])
    admission = store.node_admission_snapshot(graph_id=graph_id)
    return store, dict(task_id=task, graph_id=graph_id, graph_digest=admission["digest"],
        node_keys=["baseline"], profile_name="explorer", model="gpt-5.6-luna", reasoning_effort="high",
        bootstrap_provenance=PROVENANCE, admission=admission)


def test_ambiguous_dispatch_retry_retains_exact_native_worker(node_case):
    store, args = node_case
    first, replayed = store.open_node_assignment(**args)
    assert not replayed
    retry, replayed = store.open_node_assignment(**args)
    assert replayed and retry == first
    for table in ("delegations", "execution_assignments", "worker_capabilities"):
        assert store._read(lambda c: c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) == 1
    with pytest.raises(V12StoreError) as changed:
        store.open_node_assignment(**dict(args, reasoning_effort="max"))
    assert changed.value.code == "command_conflict"


def test_fresh_read_of_active_node_cannot_spawn_second_owner(node_case):
    store, args = node_case
    store.open_node_assignment(**args)
    new_snapshot = store.node_admission_snapshot(graph_id=args["graph_id"])
    with pytest.raises(V12StoreError) as failure:
        store.open_node_assignment(**dict(args, admission=new_snapshot))
    assert failure.value.code == "assignment_not_ready"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM delegations").fetchone()[0]) == 1


def test_worker_read_receives_exact_node_kind_even_for_writer_profile(node_case, monkeypatch):
    import re
    from cortex_runtime.domain_api import read_task
    store, args = node_case
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    result, _ = store.open_node_assignment(**dict(args, profile_name="technical_writer"))
    message = result["dispatch_brief"]["native_dispatch"]["native_arguments"]["message"]
    worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', message).group(1)
    context = {}
    pages = [read_task(task_ref=worker_ref, _connection_context=context)]
    while pages[-1]["has_more"]:
        pages.append(read_task(task_ref=worker_ref, continue_=True, _connection_context=context))
    def walk(value):
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
    kinds = [value["terminal_publication_kind"] for page in pages for value in walk(page) if "terminal_publication_kind" in value]
    assert kinds and set(kinds) == {"result"}
    scopes = [value["assignment"] for page in pages for value in walk(page) if "assignment" in value]
    assert len(scopes) == 1 and scopes[0]["nodes"][0]["key"] == "baseline"
    assert scopes[0]["execution_mode"] == "read_only"
    assert not any({"contract_coverage_template", "required_outcomes", "assigned_items", "planning_items", "node_scope", "publication_reconciliation", "effective_contract"}.intersection(value)
                   for page in pages for value in walk(page))
    assert sum("nodes" in value for page in pages for value in walk(page)) == 1
    worker_context = pages[0]["data"]["assignment_context"]
    assert not {"instructions", "scope", "objective"}.intersection(worker_context)
    contract = pages[0]["data"]["contract_context"]
    assert contract["outcomes"][0]["outcome"] == "Product"
    assert "task_constraints" in contract and "context" in contract
    assert scopes[0]["artifact"]["background_mutators_permitted"] is False
    command = scopes[0]["artifact"]["worker_procedure"]["command"]
    assert command[0] == "python3"
    assert command[command.index("--project-root") + 1] == str(store.project_root)
    from cortex_runtime.artifact_fingerprint import archive_path
    assert command[command.index("--archive-root") + 1] == str(archive_path(store._codex_home, store.project_hash))
    assert not archive_path(store._codex_home, store.project_hash).is_relative_to(store.root)
    assert command[command.index("--method") + 1] == "auto"


def test_worker_read_refuses_missing_typed_scope_without_outcome_fallback(node_case, monkeypatch):
    import re
    from copy import deepcopy
    from cortex_runtime.domain_api import read_task
    from cortex_runtime.v12_service import V12ServiceError
    from cortex_runtime.v12_store import V12Store
    store, args = node_case
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    result, _ = store.open_node_assignment(**args)
    message = result["dispatch_brief"]["native_dispatch"]["native_arguments"]["message"]
    ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', message).group(1)
    original = V12Store.read_delegation
    def missing_scope(self, **kwargs):
        value = deepcopy(original(self, **kwargs))
        value["worker_brief"].pop("assignment")
        return value
    monkeypatch.setattr(V12Store, "read_delegation", missing_scope)
    context = {}
    with pytest.raises(V12ServiceError) as missing:
        read_task(task_ref=ref, _connection_context=context)
    assert missing.value.code == "ledger_error"
    assert context.get("assignment_complete") is not True
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_publications").fetchone()[0]) == 0


def test_bootstrap_append_and_claim_roll_back_together_before_baseline(node_case):
    store, args = node_case
    with pytest.raises(V12StoreError) as failure:
        store.open_node_assignment(**dict(args, node_keys=[], bootstrap_kind="discovery",
            bootstrap_question="Which schema is currently implemented?"))
    assert failure.value.code == "assignment_not_ready"
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_nodes").fetchone()[0]) == 1
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM delegations").fetchone()[0]) == 0


def test_bootstrap_append_claim_is_one_receipted_mutation(node_case):
    from test_graph_ledger import observation
    store, args = node_case
    # Establish baseline at the graph layer; this test is about the following
    # store transaction, not a claim of public API or host qualification.
    def baseline(c):
        scope = graph_ledger.claim_nodes(c, graph_id=args["graph_id"], task_id=args["task_id"],
            expected_digest=args["graph_digest"], node_keys=["baseline"], assignment_id="baseline-fixture", protected_task_name="baseline-fixture")
        coverage = [{"node": "baseline", "coverage": [{"kind": "outcome", "name": "Product", "status": "complete",
            "verification": [{"check_key": check["key"], "state": "executed", "summary": "Observed"} for check in scope["nodes"][0]["checks"]]}]}]
        graph_ledger.publish_nodes(c, assignment_id="baseline-fixture", report_id="baseline-evidence",
            terminal_kind="result", node_coverage=coverage, artifact=observation())
    store._write(baseline)
    # The real store requires finalized predecessor reports. Manufacture no
    # such report: missing evidence must roll back the appended node and claim.
    admission = store.node_admission_snapshot(graph_id=args["graph_id"])
    with pytest.raises(V12StoreError):
        store.open_node_assignment(**dict(args, node_keys=[], admission=admission,
            bootstrap_kind="discovery", bootstrap_question="Which schema is currently implemented?"))
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_nodes").fetchone()[0]) == 1
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments").fetchone()[0]) == 1


def test_unstable_initial_baseline_has_no_generation_or_publication(node_case):
    from test_graph_ledger import observation
    store, args = node_case
    result, _ = store.open_node_assignment(**args)
    assignment = result["delegation"]["delegation_id"]
    node = store._read(lambda c: graph_ledger._graph(c, args["graph_id"])[0])
    import json
    definition = store._read(lambda c: json.loads(c.execute("SELECT content_json FROM execution_nodes WHERE graph_id=? AND node_key='baseline'", (node["graph_id"],)).fetchone()[0]))
    coverage = [{"node": "baseline", "coverage": [{"kind": "outcome", "name": "Product", "status": "complete",
        "verification": [{"check_key": check["key"], "state": "executed", "summary": "Observed"} for check in definition["checks"]]}]}]
    outcome = store._write(lambda c: graph_ledger.publish_nodes(c, assignment_id=assignment,
        report_id="never-created", terminal_kind="result", node_coverage=coverage, artifact=observation(end="b" * 64)))
    assert outcome == {"state": "snapshot_conflict", "published": False, "replayed": False}
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM artifact_generations").fetchone()[0]) == 0
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_publications").fetchone()[0]) == 0
