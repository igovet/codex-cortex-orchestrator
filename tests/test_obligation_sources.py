"""The extraction review binds the actual registry, not model-resupplied data."""
import pytest

from cortex_runtime.obligation_sources import snapshot, ObligationSourceError
from cortex_runtime.domain_api import read_scope, record_steering
from test_obligation_integrity import task_store


def test_registry_contains_original_and_all_scoped_criteria(task_store):
    store, task_id, _, original = task_store
    result = store._read(lambda c: snapshot(c, task_id))
    assert result.sources[0].text == original
    assert result.sources[0].provenance == "recorded_original_not_host_attested"
    assert {s.role for s in result.subjects} == {"requirement", "acceptance", "verification", "task_constraint"}
    assert {s.text for s in result.subjects} == {"Product", "Works offline", "Test offline", "No network"}
    assert result == store._read(lambda c: snapshot(c, task_id))


def test_plan_state_changes_do_not_change_registry_binding(task_store):
    store, task_id, _, _ = task_store
    before = store._read(lambda c: snapshot(c, task_id))
    store._write(lambda c: c.execute("INSERT INTO execution_events(graph_id,event,details_json) VALUES ('fixture','replan','{}')"))
    assert before == store._read(lambda c: snapshot(c, task_id))


def test_user_change_adds_source_and_changes_registry_digest(task_store):
    store, task_id, ref, original = task_store
    before = store._read(lambda c: snapshot(c, task_id))
    context = {}
    read_scope(task_ref=ref, responsibility="delivery", _connection_context=context)
    message = "Also use keyboard input."
    record_steering(task_ref=ref, response_original=message, user_language="en", retire=[], add=[{
        "outcome": "Keyboard", "acceptance": ["Tab works"], "constraints": [], "verification": ["Tab works"],
    }], _connection_context=context)
    after = store._read(lambda c: snapshot(c, task_id))
    assert after.revision == before.revision + 1
    assert after.content_digest != before.content_digest
    assert [s.text for s in after.sources] == [original, message]
    repeated = [s for s in after.subjects if s.text == "Tab works"]
    assert len(repeated) == 2
    assert repeated[0].reference != repeated[1].reference


def test_snapshot_cannot_mix_reads_across_transactions(task_store):
    store, task_id, _, _ = task_store
    with store._connection() as connection:
        with pytest.raises(ObligationSourceError, match="transaction_required"):
            snapshot(connection, task_id)
