"""Synthetic coverage for explicit offline per-project metadata export."""
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from cortex_runtime.contracts import StoreError
from cortex_runtime.hook_storage import HookStorage
from cortex_runtime.host_source import NativeSource
from cortex_runtime.split import APPLICATION_ID, split_project
from cortex_runtime.store import Store


TABLES = (
    "tasks", "thread_bindings", "reports", "source_cursors", "source_messages",
    "editions", "governance", "deliveries", "drafts", "pending_source_deletions",
    "pending_deletions", "binding_receipts", "source_resets", "source_revisions",
    "report_provenance", "draft_revisions", "task_changes", "source_turns",
    "hook_pending_sources", "hook_events", "hook_hints", "hook_agent_bindings",
)


def _create(store, project, label):
    thread = "thread-" + label
    source = NativeSource(
        "request-" + label,
        {"offset": label},
        [dict(id="message-" + label, turn="turn-" + label,
              text="request-" + label, attachments=[{"name": label}])],
    )
    store.call("create_task", dict(project_root=str(project), request_key="create-" + label),
               thread, original_request=lambda: source)
    with store.connection() as db:
        task = db.execute("SELECT task_id FROM thread_bindings WHERE thread_id=?", (thread,)).fetchone()[0]
    return task, thread


def _populate(store, project, task, thread, label):
    governance = store.call(
        "set_governance",
        dict(mode="light", rationale="governance-" + label, request_key="govern-" + label),
        thread,
    )
    draft = store.call(
        "create_draft", dict(template="general", request_key="draft-" + label), thread
    )
    path = Path(draft["draft_path"])
    marker = path.read_bytes().split(b"\n\n", 1)[0] + b"\n\n"
    path.write_bytes(marker + ("evidence-" + label).encode())
    report = store.call(
        "write_report",
        dict(title="Evidence", summary="Checked evidence.", author="worker",
             draft_id=draft["draft_id"], source_revision=1,
             artifacts=[dict(reference="artifact-" + label, version="sha256:" + "a" * 64)],
             request_key="write-" + label),
        thread,
    )
    unfinished = store.call(
        "create_draft", dict(template="investigation", request_key="unfinished-" + label),
        "worker-" + label, thread,
    )
    hooks = HookStorage(store)
    context = hooks.context(thread, str(project))
    hooks.note_prompt(context, "pending-" + label)
    worker = hooks.register_agent(thread, str(project), "hook-worker-" + label)
    hooks.record(worker, "PostToolUse", "event-" + label,
                 dict(status="exited", exit_code=0, tool_name="exec_command",
                      actor_scope="actor", actor_thread_id="hook-worker-" + label))
    hooks.claim_hint(worker, "Stop", "state-" + label)
    store.call(
        "set_governance",
        dict(mode="minimal", rationale="pause-" + label, state="normal",
             request_key="pause-" + label),
        thread,
    )
    return dict(governance=governance["governance_id"], report=report["report_id"],
                unfinished=unfinished["draft_id"])


def _file_digests(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*.md")
    }


def _rows(db, table, where="", arguments=()):
    columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
    query = "SELECT " + ",".join('"' + column + '"' for column in columns) + f' FROM "{table}"'
    if where:
        query += " WHERE " + where
    return {tuple(row) for row in db.execute(query, arguments)}


def _expected_rows(source, table, task, project):
    direct = {
        "tasks": ("id=?", (task,)),
        "thread_bindings": ("task_id=?", (task,)),
        "reports": ("task_id=?", (task,)),
        "source_cursors": ("task_id=?", (task,)),
        "source_messages": ("task_id=?", (task,)),
        "governance": ("task_id=?", (task,)),
        "drafts": ("task_id=?", (task,)),
        "pending_source_deletions": ("task_id=?", (task,)),
        "pending_deletions": ("task_id=?", (task,)),
        "source_resets": ("task_id=?", (task,)),
        "source_revisions": ("task_id=?", (task,)),
        "task_changes": ("task_id=?", (task,)),
        "source_turns": ("task_id=?", (task,)),
        "hook_pending_sources": ("task_id=?", (task,)),
        "hook_events": ("task_id=?", (task,)),
        "hook_hints": ("task_id=?", (task,)),
        "hook_agent_bindings": ("task_id=?", (task,)),
    }
    if table in direct:
        return _rows(source, table, *direct[table])
    if table == "editions":
        return _rows(source, table, "report_id IN (SELECT id FROM reports WHERE task_id=?)", (task,))
    if table == "binding_receipts":
        return _rows(source, table,
                     "thread_id IN (SELECT thread_id FROM thread_bindings WHERE task_id=?)", (task,))
    if table == "report_provenance":
        return _rows(source, table, "edition_sequence IN (SELECT e.sequence FROM editions e JOIN reports r ON r.id=e.report_id WHERE r.task_id=?)", (task,))
    if table == "draft_revisions":
        return _rows(source, table, "draft_id IN (SELECT id FROM drafts WHERE task_id=?)", (task,))
    if table == "deliveries":
        result = set()
        columns = [row[1] for row in source.execute('PRAGMA table_info("deliveries")')]
        for row in source.execute("SELECT * FROM deliveries"):
            response = json.loads(row[columns.index("response")])
            if row[columns.index("scope")] == task or response.get("task_id") == task:
                result.add(tuple(row))
        return result
    raise AssertionError(table)


def test_split_preserves_all_selected_metadata_and_never_copies_markdown(tmp_path):
    source_dir = tmp_path / "global"; source_dir.mkdir(mode=0o700)
    first = tmp_path / "first"; second = tmp_path / "second"; first.mkdir(); second.mkdir()
    store = Store(source_dir)
    first_task, first_thread = _create(store, first, "first")
    second_task, second_thread = _create(store, second, "second")
    retained = _populate(store, first, first_task, first_thread, "first")
    _populate(store, second, second_task, second_thread, "second")
    markdown_before = _file_digests(tmp_path)
    source_before = (source_dir / "cortex.sqlite3").read_bytes()
    backup = tmp_path / "global-backup.sqlite3"

    result = split_project(source_dir, first, backup, access_stopped=True)

    destination = first / ".codex" / "cortex" / "cortex.sqlite3"
    assert result == dict(project_root=str(first), destination=str(destination), backup=str(backup),
                          tasks=1, schema_version=11, application_id=APPLICATION_ID,
                          markdown_copied=False, source_modified=False)
    assert (source_dir / "cortex.sqlite3").read_bytes() == source_before
    assert _file_digests(tmp_path) == markdown_before
    assert stat_mode(destination) == stat_mode(backup) == 0o600
    with closing(sqlite3.connect(source_dir / "cortex.sqlite3")) as source, \
         closing(sqlite3.connect(destination)) as selected, \
         closing(sqlite3.connect(backup)) as saved:
        assert selected.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert selected.execute("PRAGMA user_version").fetchone()[0] == 11
        assert selected.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert selected.execute("PRAGMA foreign_key_check").fetchall() == []
        assert selected.execute("SELECT id,project_root FROM tasks").fetchall() == [(first_task, str(first))]
        assert selected.execute("SELECT 1 FROM reports WHERE id=?", (retained["report"],)).fetchone()
        assert selected.execute("SELECT 1 FROM drafts WHERE id=?", (retained["unfinished"],)).fetchone()
        for table in TABLES:
            assert _rows(selected, table) == _expected_rows(source, table, first_task, first)
            assert _rows(saved, table) == _rows(source, table)
    local = Store(destination.parent, project_root=str(first))
    with local.connection() as db:
        assert [row[0] for row in db.execute("SELECT id FROM tasks")] == [first_task]


def stat_mode(path):
    return path.stat().st_mode & 0o777


def test_split_requires_acknowledgement_and_refuses_existing_outputs(tmp_path):
    source_dir = tmp_path / "global"; source_dir.mkdir(mode=0o700)
    project = tmp_path / "project"; project.mkdir(); store = Store(source_dir)
    _create(store, project, "only")
    backup = tmp_path / "backup.sqlite3"
    with pytest.raises(StoreError, match="split_requires_stopped_access"):
        split_project(source_dir, project, backup)
    destination = project / ".codex" / "cortex" / "cortex.sqlite3"
    destination.write_bytes(b"existing destination"); destination.chmod(0o600)
    with pytest.raises(StoreError, match="split_destination_exists"):
        split_project(source_dir, project, backup, access_stopped=True)
    assert destination.read_bytes() == b"existing destination" and not backup.exists()
    destination.unlink(); backup.write_bytes(b"existing backup")
    with pytest.raises(StoreError, match="split_backup_exists"):
        split_project(source_dir, project, backup, access_stopped=True)
    assert backup.read_bytes() == b"existing backup" and not destination.exists()


def test_split_accepts_legacy_format11_without_new_project_index(tmp_path):
    source_dir = tmp_path / "global"; source_dir.mkdir(mode=0o700)
    project = tmp_path / "project"; project.mkdir(); store = Store(source_dir)
    task, _ = _create(store, project, "only")
    with store.connection() as db:
        db.execute("DROP INDEX tasks_by_project")
    split_project(source_dir, project, tmp_path / "backup.sqlite3", access_stopped=True)
    destination = project / ".codex" / "cortex" / "cortex.sqlite3"
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT id FROM tasks").fetchall() == [(task,)]
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='tasks_by_project'"
        ).fetchone()


def test_split_exports_every_task_for_the_exact_project(tmp_path):
    source_dir = tmp_path / "global"; source_dir.mkdir(mode=0o700)
    project = tmp_path / "project"; project.mkdir(); store = Store(source_dir)
    first, _ = _create(store, project, "first")
    second, _ = _create(store, project, "second")
    other = tmp_path / "other"; other.mkdir(); foreign, _ = _create(store, other, "foreign")
    result = split_project(source_dir, project, tmp_path / "backup.sqlite3", access_stopped=True)
    with sqlite3.connect(project / ".codex" / "cortex" / "cortex.sqlite3") as db:
        assert result["tasks"] == 2
        assert {row[0] for row in db.execute("SELECT id FROM tasks")} == {first, second}
        assert db.execute("SELECT 1 FROM tasks WHERE id=?", (foreign,)).fetchone() is None


@pytest.mark.parametrize("alteration", [
    "CREATE TABLE private_future_data(secret TEXT)",
    "ALTER TABLE tasks ADD COLUMN future_private_value TEXT",
])
def test_split_fails_closed_on_unknown_format11_tables_or_columns(tmp_path, alteration):
    source_dir = tmp_path / "global"; source_dir.mkdir(mode=0o700)
    project = tmp_path / "project"; project.mkdir(); store = Store(source_dir)
    _create(store, project, "only")
    db = sqlite3.connect(source_dir / "cortex.sqlite3")
    db.execute(alteration); db.commit(); db.close()
    backup = tmp_path / "backup.sqlite3"
    with pytest.raises(StoreError, match="unsupported_storage"):
        split_project(source_dir, project, backup, access_stopped=True)
    assert not backup.exists()
    assert not (project / ".codex" / "cortex" / "cortex.sqlite3").exists()


def test_split_rejects_cross_project_logical_relation(tmp_path):
    source_dir = tmp_path / "global"; source_dir.mkdir(mode=0o700)
    first = tmp_path / "first"; second = tmp_path / "second"; first.mkdir(); second.mkdir()
    store = Store(source_dir)
    first_task, first_thread = _create(store, first, "first")
    second_task, _ = _create(store, second, "second")
    with store.connection() as db:
        db.execute("INSERT INTO hook_agent_bindings VALUES (?,?,?,?,?)",
                   ("cross-project-agent", first_thread, second_task, "cross-receipt", "now"))
    backup = tmp_path / "backup.sqlite3"
    with pytest.raises(StoreError, match="split_source_relations_invalid"):
        split_project(source_dir, second, backup, access_stopped=True)
    assert not backup.exists()


def test_split_rejects_active_cooperative_access(tmp_path):
    source_dir = tmp_path / "global"; source_dir.mkdir(mode=0o700)
    project = tmp_path / "project"; project.mkdir(); store = Store(source_dir)
    _create(store, project, "only")
    with store.connection():
        with pytest.raises(StoreError, match="storage_busy"):
            split_project(source_dir, project, tmp_path / "backup.sqlite3", access_stopped=True)


def test_split_holds_source_sqlite_and_destination_access_locks_through_publication(tmp_path, monkeypatch):
    import cortex_runtime.split as module
    source_dir = tmp_path / "global"; source_dir.mkdir(mode=0o700)
    project = tmp_path / "project"; project.mkdir(); store = Store(source_dir)
    _create(store, project, "only")
    original = module._publish
    checked = []

    def assert_locked(temporary, destination):
        contender = sqlite3.connect(source_dir / "cortex.sqlite3", timeout=0,
                                    isolation_level=None)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("BEGIN EXCLUSIVE")
        finally:
            contender.close()
        lock_fd = os.open(project / ".codex" / "cortex" / ".access.lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(lock_fd)
        checked.append(True)
        return original(temporary, destination)

    monkeypatch.setattr(module, "_publish", assert_locked)
    result = split_project(source_dir, project, tmp_path / "backup.sqlite3", access_stopped=True)
    assert checked == [True] and Path(result["destination"]).is_file()


def test_split_cleans_private_outputs_when_publication_fails(tmp_path, monkeypatch):
    import cortex_runtime.split as module
    source_dir = tmp_path / "global"; source_dir.mkdir(mode=0o700)
    project = tmp_path / "project"; project.mkdir(); store = Store(source_dir)
    _create(store, project, "only")
    backup = tmp_path / "backup.sqlite3"
    monkeypatch.setattr(module, "_publish", lambda *_: (_ for _ in ()).throw(OSError("private")))
    with pytest.raises(StoreError, match="split_failed"):
        split_project(source_dir, project, backup, access_stopped=True)
    destination_dir = project / ".codex" / "cortex"
    # A complete verified backup remains useful after a later publication
    # failure. Only the incomplete destination temporary is discarded.
    assert backup.is_file() and stat_mode(backup) == 0o600
    with sqlite3.connect(backup) as saved:
        assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not (destination_dir / "cortex.sqlite3").exists()
    assert not list(destination_dir.glob(".cortex.sqlite3.split-*.tmp"))
    assert (destination_dir / ".access.lock").is_file()


def test_split_rejects_unquiesced_sqlite_sidecars_without_removing_them(tmp_path):
    source_dir = tmp_path / "global"; source_dir.mkdir(mode=0o700)
    project = tmp_path / "project"; project.mkdir(); store = Store(source_dir)
    _create(store, project, "only")
    sidecar = Path(str(source_dir / "cortex.sqlite3") + "-wal")
    sidecar.write_bytes(b"pending"); sidecar.chmod(0o600)
    with pytest.raises(StoreError, match="split_source_not_quiescent"):
        split_project(source_dir, project, tmp_path / "backup.sqlite3", access_stopped=True)
    assert sidecar.read_bytes() == b"pending"
