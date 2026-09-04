"""Obligations cannot be rewritten by ordinary storage operations."""
import sqlite3

import pytest

from cortex_runtime.domain_api import open_task, _task_store, record_steering, read_scope


@pytest.fixture
def task_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    project = tmp_path / "project"
    project.mkdir()
    original = "  Keep ALL requirements.\nНе ослаблять критерии.\n"
    ref = open_task(project_root=str(project), request_original=original,
                    user_language="en", constraints=["No network"], outcomes=[{
                        "outcome": "Product", "acceptance": ["Works offline"],
                        "constraints": [], "verification": ["Test offline"],
                    }])["task_ref"]
    store, task_id = _task_store(ref)
    return store, task_id, ref, original


@pytest.mark.parametrize("sql", [
    "UPDATE tasks SET user_request_original='shorter request'",
    "UPDATE tasks SET constraints_json='[]'",
    "UPDATE tasks SET verification_plan_json='[]'",
    "DELETE FROM tasks",
    "UPDATE effective_contract_items SET text='weaker requirement'",
    "DELETE FROM effective_contract_items",
    "UPDATE effective_contract_item_details SET details_json='{}'",
    "DELETE FROM effective_contract_item_details",
    "UPDATE effective_contract_revisions SET revision=99",
    "DELETE FROM effective_contract_revisions",
])
def test_history_mutation_rejected_atomically(task_store, sql):
    store, _, _, _ = task_store
    before = store._read(lambda c: list(c.iterdump()))
    def attempt(c):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute(sql)
    store._write(attempt)
    assert store._read(lambda c: list(c.iterdump())) == before


def test_original_bytes_and_prior_criteria_survive_user_replacement(task_store):
    store, task_id, ref, original = task_store
    context = {}
    read_scope(task_ref=ref, responsibility="delivery", _connection_context=context)
    record_steering(task_ref=ref, response_original="Also support keyboard input.",
                    user_language="en", retire=["Product"], add=[{
                        "outcome": "Product", "acceptance": ["Works offline", "Keyboard input"],
                        "constraints": [], "verification": ["Test offline", "Test keyboard"],
                    }], _connection_context=context)
    stored = store._read(lambda c: c.execute(
        "SELECT user_request_original FROM tasks WHERE task_id=?", (task_id,)).fetchone()[0])
    assert stored.encode() == original.encode()
    rows = store._read(lambda c: c.execute(
        "SELECT retired_revision FROM effective_contract_items ORDER BY created_revision").fetchall())
    assert [r[0] for r in rows] == [2, None]
    def revive(c):
        with pytest.raises(sqlite3.IntegrityError, match="irreversible|recorded decision"):
            c.execute("UPDATE effective_contract_items SET retired_revision=NULL WHERE retired_revision=2")
        decision = c.execute("SELECT decision_id FROM effective_contract_revisions WHERE revision=2").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="already changed"):
            c.execute("INSERT INTO effective_contract_revisions VALUES (?,3,?,999)", (task_id, decision))
    store._write(revive)


def test_internal_retry_cannot_create_revision_or_retire_obligation_without_decision(task_store):
    store, task_id, _, _ = task_store
    before = store._read(lambda c: list(c.iterdump()))
    def unauthorized(c):
        with pytest.raises(sqlite3.IntegrityError, match="task-bound decision"):
            c.execute("INSERT INTO effective_contract_revisions VALUES (?,2,NULL,999)", (task_id,))
        with pytest.raises(sqlite3.IntegrityError, match="recorded decision"):
            c.execute("UPDATE effective_contract_items SET retired_revision=2")
    store._write(unauthorized)
    assert store._read(lambda c: list(c.iterdump())) == before
