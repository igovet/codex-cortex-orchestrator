"""Typed chronology is transactional and read-only; no historical backfill."""
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from cortex_runtime.domain_api import (
    open_task, open_assignment, open_plan_review, publish_result, read_scope,
    read_state, record_plan_review,
)
from cortex_runtime.v12_contract import task_ref
from cortex_runtime.v12_projections import _directory, materialize_task
from cortex_runtime.v12_service import V12ServiceError
from cortex_runtime.v12_store import V12Store
from test_domain_public_api_contract import PROVENANCE
from test_node_assignment_receipts import node_case
from test_typed_public_api import dispatch_and_consume
from test_typed_publication_transaction import baseline_content


def rows(store, task):
    return store._read(lambda c: [tuple(row) for row in c.execute(
        "SELECT sequence,task_id,event_type FROM timeline WHERE task_id=? ORDER BY sequence", (task,))])


def test_parallel_native_names_are_bound_to_distinct_current_assignments(node_case, monkeypatch):
    import test_domain_public_api_contract as api_fixture
    store, args = node_case
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    helper = api_fixture.DomainPublicApiContractTests()
    # A separate multi-outcome task is needed for independent ready nodes.
    outcomes = [{"outcome": name, "acceptance": [], "constraints": [], "verification": []} for name in ("A", "B", "C")]
    created, _ = helper._task(str(store.project_root), outcomes)
    dispatched = helper._parallel_assignments(created["task_ref"], outcomes)
    names = [item["native_dispatch"]["task_name"] for item in dispatched]
    assert len(set(names)) == 3
    assert all(re.fullmatch(r"explorer_d_[0-9a-f]{12}", name) for name in names)
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM execution_assignments WHERE state='active'").fetchone()[0]) == 3


def test_plan_revision_feedback_survives_unrelated_chronology(node_case, monkeypatch):
    import test_domain_public_api_contract as api_fixture
    store, args = node_case
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    helper = api_fixture.DomainPublicApiContractTests()
    task = task_ref(args["task_id"])
    outcome = {"outcome": "Product", "acceptance": [], "constraints": [], "verification": []}
    helper._prepared_plan(task, [outcome])
    worker, context = helper._consume_dispatch(helper._assignment(task, outcome, "validation"))
    helper._publish_result(worker, outcome, context)
    open_plan_review(task_ref=task, prompt="Review the current plan.", prompt_language="en")
    before = store._read(lambda c: tuple(c.execute("SELECT report_id,content_digest FROM reports WHERE report_type='plan'").fetchone()))
    store._write(lambda c: store._timeline(c, task_id=args["task_id"], event_type="diagnostic_observed",
        entity_type="test", entity_id="unrelated", payload={"changed_contract": False}))
    result = record_plan_review(task_ref=task, outcome="request_revision", response_original="Clarify the verification stages.", user_language="en")
    assert result["state"] == "plan_review_recorded" and not result["replayed"]
    assert store._read(lambda c: tuple(c.execute("SELECT report_id,content_digest FROM reports WHERE report_type='plan'").fetchone())) == before
    assert read_state(task_ref=task)["data"]["effective_revision"] == 1


def test_reopening_never_backfills_or_changes_existing_timeline(node_case, monkeypatch):
    store, args = node_case
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    task = task_ref(args["task_id"])
    worker, context = dispatch_and_consume(task, nodes=["baseline"])
    publish_result(task_ref=worker, _connection_context=context, **baseline_content())
    before = rows(store, args["task_id"])
    reopened = V12Store(store.project_root)
    read_state(task_ref=task)
    assert rows(reopened, args["task_id"]) == before
    assert not any("backfill" in name or name.startswith("_migrate_") for name in vars(V12Store))
    assert reopened._read(lambda c: c.execute("PRAGMA user_version").fetchone()[0]) == 2


def test_wal_ordering_cross_task_isolation_and_rollback(node_case):
    store, args = node_case
    second = open_task(project_root=str(store.project_root), request_original="Second timeline.", user_language="en",
        outcomes=[{"outcome": "Second", "acceptance": [], "constraints": [], "verification": []}], constraints=["Fixture only"])
    from cortex_runtime.domain_api import _task_store
    _, task_b = _task_store(second["task_ref"])
    task_a = args["task_id"]
    before = {task: len(rows(store, task)) for task in (task_a, task_b)}
    def write(index):
        owner = task_a if index % 2 == 0 else task_b
        return store._write(lambda c: store._timeline(c, task_id=owner, event_type="concurrent_observation",
            entity_type="test", entity_id=f"observation-{index}", payload={"index": index}))
    with ThreadPoolExecutor(max_workers=8) as pool:
        sequences = list(pool.map(write, range(16)))
    assert len(set(sequences)) == 16
    observed = {task: rows(store, task) for task in (task_a, task_b)}
    for task, events in observed.items():
        assert len(events) == before[task] + 8
        assert all(event[1] == task for event in events)
        assert [event[0] for event in events] == sorted(event[0] for event in events)
    assert {event[0] for event in observed[task_a]}.isdisjoint(event[0] for event in observed[task_b])
    def rollback(c):
        store._timeline(c, task_id=task_a, event_type="rolled_back", entity_type="test", entity_id="rollback", payload={})
        raise RuntimeError("Forced rollback")
    with pytest.raises(RuntimeError, match="Forced rollback"):
        store._write(rollback)
    assert rows(store, task_a) == observed[task_a]


def test_typed_publication_has_one_event_and_replay_adds_none(node_case, monkeypatch):
    store, args = node_case
    monkeypatch.setattr("cortex_runtime.domain_api._worker_capability_provenance", lambda: PROVENANCE)
    worker, context = dispatch_and_consume(task_ref(args["task_id"]), nodes=["baseline"])
    first = publish_result(task_ref=worker, _connection_context=context, **baseline_content())
    before = rows(store, args["task_id"])
    replay = publish_result(task_ref=worker, _connection_context=context, **baseline_content())
    assert first["published"] and not first["replayed"] and replay["replayed"]
    assert rows(store, args["task_id"]) == before
    kinds = [event[2] for event in before]
    assert kinds.count("delegation_created") == kinds.count("report_submitted") == 1
    assert "report_started" not in kinds and "report_chunk_appended" not in kinds


def test_foreign_task_cannot_reuse_scope_or_append_assignment_chronology(node_case):
    store, args = node_case
    coordinator = {}
    read_scope(task_ref=task_ref(args["task_id"]), responsibility="evidence", _connection_context=coordinator)
    second = open_task(project_root=str(store.project_root), request_original="Foreign scope.", user_language="en",
        outcomes=[{"outcome": "Second", "acceptance": [], "constraints": [], "verification": []}], constraints=["Fixture only"])
    before = rows(store, args["task_id"])
    with pytest.raises(V12ServiceError) as invalid:
        open_assignment(task_ref=second["task_ref"], nodes=["baseline"], profile_name="general",
            model="gpt-5.6-luna", reasoning_effort="high", _connection_context=coordinator)
    assert invalid.value.code == "assignment_stale"
    assert rows(store, args["task_id"]) == before
    assert store._read(lambda c: c.execute("SELECT COUNT(*) FROM delegations").fetchone()[0]) == 0


def test_timeline_events_remain_sqlite_only(node_case):
    store, args = node_case
    def seed(c):
        for index in range(101):
            store._timeline(c, task_id=args["task_id"], event_type="chronology_probe", entity_type="test",
                            entity_id=f"probe-{index}", payload={"index": index})
    store._write(seed)
    before = rows(store, args["task_id"])
    assert materialize_task(store, args["task_id"])["status"] == "ready"
    assert rows(store, args["task_id"]) == before
    assert not (store.root / "tasks" / task_ref(args["task_id"]) / "timeline").exists()
    assert store.human_view(args["task_id"], "timeline/index.md") == {"status": "disabled", "path": None}


def test_projection_directory_allows_outer_alias_not_managed_symlink(tmp_path):
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "system-alias"
    alias.symlink_to(physical, target_is_directory=True)
    root = alias / "project"
    root.mkdir()
    _directory(root / "tasks" / "safe", root=root)
    assert (physical / "project" / "tasks" / "safe").is_dir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "managed-alias").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="unsafe"):
        _directory(root / "managed-alias" / "child", root=root)
