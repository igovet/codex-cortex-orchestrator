"""No implicit migration or reconstructed authority from obsolete databases."""
import pytest

from cortex_runtime.v12_store import V12Store, V12StoreError, SCHEMA_VERSION, MIGRATION_NAME


def test_fresh_schema_is_created_directly_and_reopens_without_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    project = tmp_path / "project"
    project.mkdir()
    store = V12Store(project)
    expected = [(SCHEMA_VERSION, MIGRATION_NAME)]
    assert store._read(lambda c: [tuple(row) for row in c.execute("SELECT version,name FROM schema_migrations")]) == expected
    assert store._read(lambda c: c.execute("PRAGMA user_version").fetchone()[0]) == 2
    reopened = V12Store(project)
    assert reopened._read(lambda c: [tuple(row) for row in c.execute("SELECT version,name FROM schema_migrations")]) == expected
    assert not any(name.startswith("_migrate_") or "backfill" in name for name in vars(V12Store))
    forbidden_hold_columns = {
        "assignment_id", "native_dispatch_digest", "continuation_capability",
        "delivery_claim_digest", "delivery_sequence", "unavailable_reason",
    }
    for table in ("clarification_holds", "clarification_bindings"):
        columns = store._read(lambda c: {row[1] for row in c.execute(f"PRAGMA table_info({table})")})
        assert not (columns & forbidden_hold_columns)
    decision_columns = store._read(lambda c: {row[1] for row in c.execute("PRAGMA table_info(user_decisions)")})
    assert "prompt" in decision_columns
    assert not {"prompt_en", "response_en"} & decision_columns
    tables = store._read(lambda c: {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")})
    assert not {"delegation_outcome_assignments", "report_contract_coverage"} & tables


@pytest.mark.parametrize("version", [0, 1, 3])
def test_obsolete_or_future_schema_is_rejected_without_conversion(tmp_path, monkeypatch, version):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    project = tmp_path / "project"
    project.mkdir()
    store = V12Store(project)
    store._write(lambda c: c.execute(f"PRAGMA user_version={version}"))
    before = store._read(lambda c: list(c.iterdump()))
    with pytest.raises(V12StoreError) as failure:
        V12Store(project)
    assert failure.value.code == "schema_unsupported"
    assert store._read(lambda c: list(c.iterdump())) == before


def test_old_native_name_adapter_is_absent():
    from cortex_runtime import delegation
    assert not hasattr(delegation, "legacy_native_task_name")
    assert not delegation.is_profile_native_task_name("cortex_" + "a" * 32, "general")


def test_task_creation_never_infers_outcome_grouping_from_legacy_lists():
    import inspect
    from cortex_runtime import v12_store, v12_service
    for create in (V12Store.create_task, v12_service.create_task):
        assert inspect.signature(create).parameters["outcome_contracts"].default is inspect.Parameter.empty
    with pytest.raises(V12StoreError) as invalid:
        v12_store._linked_outcome_contracts(None, requirements=["Product"])
    assert invalid.value.code == "invalid_argument"


def test_obsolete_assignment_and_chunked_publication_apis_are_absent():
    from cortex_runtime import v12_service
    for name in ("create_delegation", "submit_report", "publish_domain_report", "admit_result_report", "submit_governance_closure"):
        assert not hasattr(V12Store, name)
        assert not hasattr(v12_service, name)


def test_prose_plan_and_inferred_outcome_assignment_routes_are_absent():
    for name in (
        "_report_digest", "_inferred_assignment_predecessor",
        "_approved_plan_rework_parent", "active_owner_for_outcomes",
        "_current_plan_graph", "_enforce_plan_readiness", "plan_readiness",
        "_lease_expired", "_reconcile_dispatch_lease_in_transaction",
        "_reconcile_assignment_clarification_publication",
        "clarification_host_delivery_projection", "clarification_host_delivery_context",
        "host_clarification_delivery", "complete_host_clarification_delivery",
        "_clarification_native_dispatch_digest",
        "_approved_current_plan_allows_rework",
    ):
        assert not hasattr(V12Store, name)


def test_projection_uses_current_locator_without_moving_old_directory(tmp_path):
    from types import SimpleNamespace
    from cortex_runtime import v12_projections
    root = tmp_path / "shard"
    old = root / "tasks" / "old-task-identity"
    old.mkdir(parents=True)
    marker = old / "untouched.md"
    marker.write_text("Old data is not a current projection.")
    current = v12_projections._task_directory(SimpleNamespace(root=root), "t_0123456789ab")
    assert current == root / "tasks" / "t_0123456789ab"
    assert current.is_dir()
    assert list(current.iterdir()) == []
    assert marker.read_text() == "Old data is not a current projection."
    assert not hasattr(v12_projections, "_migrate_legacy_task_directory")
    assert not hasattr(v12_projections, "_rename_directory_noreplace")


def test_projection_current_directory_refuses_symlink(tmp_path):
    from types import SimpleNamespace
    from cortex_runtime import v12_projections
    root = tmp_path / "shard"
    (root / "tasks").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "tasks" / "t_0123456789ab").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="unsafe"):
        v12_projections._task_directory(SimpleNamespace(root=root), "t_0123456789ab")
    assert list(outside.iterdir()) == []
