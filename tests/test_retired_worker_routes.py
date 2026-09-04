"""Retired alternate worker/initiative service routes have no compatibility alias."""
import pytest

from cortex_runtime import v12_service, worker_message
from cortex_runtime.v12_store import V12Store, V12StoreError
from cortex_runtime.v12_contract import record_ref, new_sharded_id, DECISION_SUBJECTS
from test_node_assignment_receipts import node_case


def test_only_current_worker_bootstrap_and_service_routes_exist():
    for name in ("render_clarification_continuation", "_continuation_subject_anchor",
                 "_compact_anchor", "_CLARIFICATION_CONTINUATION_POLICY", "CONTINUATION_RENDERER_VERSION"):
        assert not hasattr(worker_message, name)
    for name in ("record_initiative", "inspect_governance", "_task_list_in_project"):
        assert not hasattr(v12_service, name)
        assert name not in v12_service.__all__
    for name in ("record_initiative", "inspect_governance", "_initiative", "_initiative_links",
                 "_task_initiative_ids", "_refresh_initiative_warnings", "_timeline_revision_sequences"):
        assert not hasattr(V12Store, name)


def test_retired_initiative_storage_and_locators_are_absent(node_case):
    store, _ = node_case
    tables = store._read(lambda c: {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")})
    assert not {"initiatives", "initiative_revisions", "initiative_links"} & tables
    assert "initiative" not in DECISION_SUBJECTS
    assert record_ref("initiative-" + "a" * 16 + "-" + "b" * 32) is None
    with pytest.raises(ValueError):
        new_sharded_id("initiative", store.project_hash)


def test_current_schema_rejects_old_governance_shape_without_migration(node_case):
    store, _ = node_case
    def check(connection):
        connection.execute("ALTER TABLE governance_assessments ADD COLUMN initiative_id TEXT")
        before = list(connection.iterdump())
        with pytest.raises(V12StoreError) as failure:
            store._validate_existing(connection)
        assert failure.value.code == "schema_unsupported"
        assert list(connection.iterdump()) == before
    store._write(check)
