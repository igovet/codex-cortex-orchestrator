"""Project-local routing and storage isolation regressions."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import multiprocessing
from pathlib import Path
import shutil
import sqlite3
import uuid

import pytest

from cortex_runtime.contracts import StoreError
from cortex_runtime.hooks import main as hook_main
from cortex_runtime.host_source import pending_requests
from cortex_runtime.project_storage import migrate_legacy_project_storage
from cortex_runtime.server import Server
from cortex_runtime.store import Store
from cortex_runtime.store import APPLICATION_ID


@pytest.mark.parametrize('kind', ['foreign', 'format10'])
def test_rejected_database_keeps_bytes_and_journal_mode(tmp_path, kind):
    directory=tmp_path/'.cortex'
    directory.mkdir(parents=True,mode=0o700)
    database=directory/'cortex.sqlite3'
    db=sqlite3.connect(database)
    try:
        db.execute('CREATE TABLE foreign_data(value TEXT)')
        db.execute("INSERT INTO foreign_data VALUES ('preserved')")
        if kind=='format10':
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute('PRAGMA user_version=10')
        db.commit()
    finally:db.close()
    database.chmod(0o600)
    before=database.read_bytes()
    with pytest.raises(StoreError,match='unsupported_storage'):
        Store(directory,project_root=str(tmp_path))
    assert database.read_bytes()==before
    assert all(not Path(str(database)+suffix).exists() for suffix in ('-wal','-shm','-journal'))


def identifier():
    return str(uuid.uuid4())


def metadata(thread, parent=None):
    return {"threadId": thread,
            "x-codex-turn-metadata": ({"parent_thread_id": parent} if parent else {})}


def dispatch(server, name, arguments, thread, parent=None):
    return server.dispatch("tools/call", {
        "name": name,
        "arguments": dict(arguments),
        "_meta": metadata(thread, parent),
    })


def successful(result):
    assert not result["isError"], result
    return result["structuredContent"]


def error_code(result):
    assert result["isError"], result
    return json.loads(result["content"][0]["text"])["error"]


class NativeIndex:
    """Small valid state_5.sqlite fixture with optional native child edges."""

    def __init__(self, root, monkeypatch):
        self.home = root / "codex-home"
        self.sessions = self.home / "sessions"
        self.sessions.mkdir(parents=True)
        self.database = self.home / "state_5.sqlite"
        monkeypatch.setenv("CODEX_HOME", str(self.home))
        monkeypatch.setenv("CORTEX_DATA_DIR", str(root / "ignored-global-store"))
        with sqlite3.connect(self.database) as db:
            db.execute("""CREATE TABLE _sqlx_migrations(
                version BIGINT PRIMARY KEY, description TEXT NOT NULL,
                installed_on TIMESTAMP NOT NULL, success BOOLEAN NOT NULL,
                checksum BLOB NOT NULL, execution_time BIGINT NOT NULL)""")
            db.execute("INSERT INTO _sqlx_migrations VALUES (1,'fixture','now',1,X'00',1)")
            db.execute("""CREATE TABLE threads(
                id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, cwd TEXT NOT NULL)""")
            db.execute("""CREATE TABLE thread_spawn_edges(
                parent_thread_id TEXT NOT NULL, child_thread_id TEXT PRIMARY KEY,
                status TEXT NOT NULL)""")

    def add(self, thread, project, parent=None, rollout=True):
        path = self.sessions / (thread + ".jsonl")
        if rollout:
            payload = {"id": thread, "cwd": str(project)}
            if parent:
                payload["parent_thread_id"] = parent
            path.write_text(json.dumps({"type": "session_meta", "payload": payload}) + "\n")
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO threads VALUES (?,?,?)",
                       (thread, str(path), str(project)))
            if parent:
                db.execute("INSERT INTO thread_spawn_edges VALUES (?,?,?)",
                           (parent, thread, "completed"))

    def set_cwd(self, thread, project):
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE threads SET cwd=? WHERE id=?", (str(project), thread))

    def remove_rollout(self, thread):
        with sqlite3.connect(self.database) as db:
            path = db.execute("SELECT rollout_path FROM threads WHERE id=?", (thread,)).fetchone()[0]
        Path(path).unlink()

    def remove_index(self):
        self.database.unlink()

    def corrupt_index(self):
        with sqlite3.connect(self.database) as db:
            db.execute("DROP TABLE _sqlx_migrations")


def configured_server(source="Original native request"):
    server = Server()
    server.request_source = lambda *_: source
    server.steering_source = lambda *_: type("Source", (), {"cursor": {}, "messages": []})()
    return server


def create_task(server, project, thread, key="create"):
    return successful(dispatch(server, "create_task", {
        "project_root": str(project), "request_key": key}, thread))


def create_report(server, thread, parent, body):
    draft = successful(dispatch(server, "create_draft", {
        "template": "general", "request_key": "draft-" + identifier()}, thread, parent))
    path = Path(draft["draft_path"])
    marker = path.read_text().split("\n\n", 1)[0] + "\n\n"
    path.write_text(marker + body)
    return successful(dispatch(server, "write_report", {
        "title": "Evidence", "summary": "Stored evidence.", "author": "worker",
        "draft_id": draft["draft_id"], "request_key": "report-" + identifier()}, thread, parent))


def test_fresh_project_keeps_all_cortex_persistence_under_one_root(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project = tmp_path / "project"; project.mkdir()
    coordinator, worker = identifier(), identifier()
    index.add(coordinator, project)
    index.add(worker, project, coordinator)
    server = configured_server("Original native request")

    created = create_task(server, project, coordinator)
    report_draft = successful(dispatch(server, "create_draft", {
        "template": "implementation", "request_key": "fresh-report-draft"},
        worker, coordinator))
    pipeline_draft = successful(dispatch(server, "create_draft", {
        "template": "pipeline", "request_key": "fresh-pipeline-draft"},
        coordinator))
    assert Path(report_draft["draft_path"]).parent == project / ".cortex/draft-reports"
    assert Path(pipeline_draft["draft_path"]).parent == project / ".cortex/pipeline-drafts"

    report_path = Path(report_draft["draft_path"])
    report_path.write_text(report_path.read_text().split("\n\n", 1)[0] +
                           "\n\n# Implementation evidence\n")
    report = successful(dispatch(server, "write_report", {
        "title": "Implementation evidence", "summary": "Stored implementation evidence.",
        "author": "backend developer", "draft_id": report_draft["draft_id"],
        "request_key": "fresh-report-publication"}, worker, coordinator))

    pipeline_path = Path(pipeline_draft["draft_path"])
    pipeline_path.write_text(pipeline_path.read_text().split("\n\n", 1)[0] +
                             "\n\n# Current pipeline\n")
    pipeline = successful(dispatch(server, "write_report", {
        "title": "Current pipeline", "summary": "Stored current pipeline.",
        "author": "coordinator", "draft_id": pipeline_draft["draft_id"],
        "request_key": "fresh-pipeline-publication"}, coordinator))

    root = project / ".cortex"
    with next(iter(server.stores.values())).connection() as database:
        task = database.execute("SELECT id FROM tasks").fetchone()[0]
    assert (root / "cortex.sqlite3").is_file()
    assert (root / task / (created["original_report_id"] + ".md")).read_text() == "Original native request"
    assert (root / task / (report["report_id"] + ".md")).is_file()
    assert pipeline["report_id"] and (root / task / "pipeline.md").is_file()
    assert not (project / ".codex" / "cortex").exists()


@pytest.mark.parametrize("contents", ["database", "reports", "complete"])
def test_one_way_legacy_storage_transfer_moves_only_absent_components(tmp_path, contents):
    project = tmp_path / "project"; project.mkdir()
    legacy = project / ".codex" / "cortex"; legacy.mkdir(parents=True, mode=0o700)
    task = "t_0123456789ab"
    if contents in {"database", "complete"}:
        database = legacy / "cortex.sqlite3"
        database.write_bytes(b"legacy database"); database.chmod(0o600)
    if contents in {"reports", "complete"}:
        reports = legacy / task; reports.mkdir(mode=0o700)
        report = reports / "r_0123456789ab.md"
        report.write_text("legacy report"); report.chmod(0o600)

    assert migrate_legacy_project_storage(project) is True
    destination = project / ".cortex"
    if contents in {"database", "complete"}:
        assert (destination / "cortex.sqlite3").read_bytes() == b"legacy database"
        assert not (legacy / "cortex.sqlite3").exists()
    if contents in {"reports", "complete"}:
        assert (destination / task / "r_0123456789ab.md").read_text() == "legacy report"
        assert not (legacy / task).exists()


def test_one_way_legacy_storage_transfer_is_noop_when_source_is_absent(tmp_path):
    project = tmp_path / "project"; project.mkdir()
    destination = project / ".cortex"; destination.mkdir(mode=0o700)
    database = destination / "cortex.sqlite3"
    database.write_bytes(b"current database"); database.chmod(0o600)
    assert migrate_legacy_project_storage(project) is False
    assert database.read_bytes() == b"current database"
    assert not (project / ".codex" / "cortex").exists()


@pytest.mark.parametrize("conflict", ["database", "reports"])
def test_one_way_legacy_storage_transfer_rejects_conflicts_without_moving(tmp_path, conflict):
    project = tmp_path / "project"; project.mkdir()
    legacy = project / ".codex" / "cortex"; legacy.mkdir(parents=True, mode=0o700)
    destination = project / ".cortex"; destination.mkdir(mode=0o700)
    task = "t_0123456789ab"
    if conflict == "database":
        source = legacy / "cortex.sqlite3"; target = destination / "cortex.sqlite3"
        source.write_bytes(b"legacy"); target.write_bytes(b"current")
        source.chmod(0o600); target.chmod(0o600)
    else:
        source = legacy / task; target = destination / task
        source.mkdir(mode=0o700); target.mkdir(mode=0o700)
        for root, body in ((source, "legacy"), (target, "current")):
            report = root / "pipeline.md"; report.write_text(body); report.chmod(0o600)
    with pytest.raises(StoreError, match="legacy_storage_conflict"):
        migrate_legacy_project_storage(project)
    assert source.exists() and target.exists()
    if conflict == "database":
        assert source.read_bytes() == b"legacy" and target.read_bytes() == b"current"
    else:
        assert (source / "pipeline.md").read_text() == "legacy"
        assert (target / "pipeline.md").read_text() == "current"


def test_server_reopens_a_complete_legacy_project_tree_after_transfer(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project = tmp_path / "project"; project.mkdir()
    thread = identifier(); index.add(thread, project)
    first = configured_server("Preserved legacy source")
    created = create_task(first, project, thread)
    with next(iter(first.stores.values())).connection() as database:
        task = database.execute("SELECT id FROM tasks").fetchone()[0]
    current = project / ".cortex"
    legacy = project / ".codex" / "cortex"; legacy.mkdir(parents=True, mode=0o700)
    for name in ("cortex.sqlite3", "cortex.sqlite3-wal", "cortex.sqlite3-shm"):
        path = current / name
        if path.exists(): path.replace(legacy / name)
    (current / task).replace(legacy / task)

    reopened = configured_server()
    result = successful(dispatch(reopened, "read_report", {
        "report_id": created["original_report_id"]}, thread))
    assert result["markdown"] == "Preserved legacy source"
    assert (current / "cortex.sqlite3").is_file()
    assert (current / task / (created["original_report_id"] + ".md")).is_file()
    assert not (legacy / "cortex.sqlite3").exists() and not (legacy / task).exists()


def test_one_server_routes_two_projects_to_independent_local_databases(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    project_a.mkdir(); project_b.mkdir()
    thread_a, thread_b = identifier(), identifier()
    index.add(thread_a, project_a); index.add(thread_b, project_b)
    server = configured_server()

    create_task(server, project_a, thread_a, "a-task")
    create_task(server, project_b, thread_b, "b-task")

    assert (project_a / ".cortex/cortex.sqlite3").is_file()
    assert (project_b / ".cortex/cortex.sqlite3").is_file()
    assert not (tmp_path / "ignored-global-store").exists()
    assert not (index.home / "cortex").exists()
    assert set(server.stores) == {str(project_a), str(project_b)}


def test_thread_report_and_draft_isolation_with_same_server(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    project_a.mkdir(); project_b.mkdir()
    root_a, root_b, child_a, child_b = [identifier() for _ in range(4)]
    index.add(root_a, project_a); index.add(root_b, project_b)
    index.add(child_a, project_a, root_a); index.add(child_b, project_b, root_b)
    server = configured_server()
    create_task(server, project_a, root_a, "a-task")
    create_task(server, project_b, root_b, "b-task")

    report = create_report(server, child_a, root_a, "Evidence owned by project A.")
    foreign_read = dispatch(server, "read_report", {"report_id": report["report_id"]}, child_b, root_b)
    assert error_code(foreign_read) == "not_found"
    draft = successful(dispatch(server, "create_draft", {
        "template": "general", "request_key": "private-draft"}, child_a, root_a))
    foreign_draft = dispatch(server, "read_draft", {"draft_id": draft["draft_id"]}, child_b, root_b)
    assert error_code(foreign_draft) == "draft_not_found"


def test_child_routes_through_parent_when_child_index_row_is_missing(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project = tmp_path / "project"; project.mkdir()
    parent, child = identifier(), identifier()
    index.add(parent, project)
    server = configured_server()
    create_task(server, project, parent)

    # The native child can reach MCP before its state index row is committed.
    draft = successful(dispatch(server, "create_draft", {
        "template": "general", "request_key": "child-before-index"}, child, parent))
    assert Path(draft["draft_path"]).is_file()


def test_child_requires_indexed_parent_and_rejects_cross_project_or_edge_conflicts(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    project_a.mkdir(); project_b.mkdir()
    parent_a, unknown_parent, child, child_b, wrong_parent = [identifier() for _ in range(5)]
    index.add(parent_a, project_a)
    index.add(child, project_a)
    index.add(child_b, project_b, parent_a)
    index.add(wrong_parent, project_a)
    server = configured_server()
    create_task(server, project_a, parent_a)

    missing = dispatch(server, "list_reports", {}, child, unknown_parent)
    assert error_code(missing) == "project_context_unavailable"
    cross_project = dispatch(server, "list_reports", {}, child_b, parent_a)
    assert error_code(cross_project) == "project_context_conflict"

    # Add an edge naming a different parent while _meta names parent_a.
    with sqlite3.connect(index.database) as db:
        db.execute("INSERT INTO thread_spawn_edges VALUES (?,?,?)",
                   (wrong_parent, child, "completed"))
    mismatched_edge = dispatch(server, "list_reports", {}, child, parent_a)
    assert error_code(mismatched_edge) == "project_context_conflict"
    assert not (project_b / ".cortex/cortex.sqlite3").exists()


def test_mismatched_create_project_is_rejected_before_any_local_store_creation(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    project_a.mkdir(); project_b.mkdir()
    thread = identifier(); index.add(thread, project_a)
    server = configured_server()

    result = dispatch(server, "create_task", {
        "project_root": str(project_b), "request_key": "wrong-project"}, thread)
    assert error_code(result) == "project_context_conflict"
    assert not (project_a / ".cortex").exists()
    assert not (project_b / ".cortex").exists()
    assert not server.stores and not server.projects.routes


def test_changed_native_cwd_never_reroutes_or_creates_a_second_database(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    project_a.mkdir(); project_b.mkdir()
    thread = identifier(); index.add(thread, project_a)
    server = configured_server()
    created = create_task(server, project_a, thread)
    index.set_cwd(thread, project_b)

    changed = dispatch(server, "read_report", {
        "report_id": created["original_report_id"]}, thread)
    assert error_code(changed) == "project_context_conflict"
    assert not (project_b / ".cortex/cortex.sqlite3").exists()

    restarted = configured_server()
    fresh = dispatch(restarted, "read_report", {
        "report_id": created["original_report_id"]}, thread)
    assert error_code(fresh) == "task_not_bound"
    assert not (project_b / ".cortex/cortex.sqlite3").exists()
    assert (project_a / ".cortex/cortex.sqlite3").is_file()


def test_restart_routes_from_index_without_rollout_and_preserves_archive(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project = tmp_path / "project"; project.mkdir()
    thread = identifier(); index.add(thread, project, rollout=True)
    server = configured_server("Original archive")
    created = create_task(server, project, thread)
    index.remove_rollout(thread)

    restarted = Server()
    result = successful(dispatch(restarted, "read_report", {
        "report_id": created["original_report_id"]}, thread))
    assert result["markdown"] == "Original archive"
    assert result["source_capture"]["status"] == "unavailable"
    assert result["source_capture"]["reason"] == "host_request_unavailable"


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_native_index_has_no_global_or_cwd_fallback(tmp_path, monkeypatch, corrupt):
    index = NativeIndex(tmp_path, monkeypatch)
    project = tmp_path / "project"; project.mkdir()
    thread = identifier(); index.add(thread, project)
    server = configured_server("Saved archive")
    created = create_task(server, project, thread)
    if corrupt:
        index.corrupt_index()
    else:
        index.remove_index()

    server.steering_source = pending_requests
    cached = successful(dispatch(server, "read_report", {
        "report_id": created["original_report_id"]}, thread))
    assert cached['markdown'] == 'Saved archive'
    assert cached['source_capture']['status'] == 'unavailable'

    restarted = Server()
    result = dispatch(restarted, "read_report", {
        "report_id": created["original_report_id"]}, thread)
    assert error_code(result) == "project_context_unavailable"
    assert not restarted.stores
    assert not (tmp_path / "ignored-global-store").exists()
    assert not (tmp_path / ".cortex/cortex.sqlite3").exists()
    assert (project / ".cortex/cortex.sqlite3").is_file()


def test_copied_foreign_project_database_is_rejected(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    project_a.mkdir(); project_b.mkdir()
    thread = identifier(); index.add(thread, project_a)
    server = configured_server()
    create_task(server, project_a, thread)
    shutil.copytree(project_a / ".cortex", project_b / ".cortex")

    with pytest.raises(StoreError, match="project_storage_mismatch"):
        Store(project_b / ".cortex", initialize=False, project_root=project_b)
    copied = project_b / '.cortex/cortex.sqlite3'
    with sqlite3.connect(copied) as db:
        assert db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
    before = copied.read_bytes()
    with pytest.raises(StoreError, match="project_storage_mismatch"):
        Store(project_b / '.cortex', project_root=project_b)
    assert copied.read_bytes() == before


def hook_event(name, session, cwd, **extra):
    return {"hook_event_name": name, "session_id": session, "cwd": str(cwd),
            "turn_id": "turn-hook", **extra}


def run_hook(payload):
    output, errors = io.StringIO(), io.StringIO()
    code = hook_main(io.StringIO(json.dumps(payload)), output, errors)
    return code, json.loads(output.getvalue()) if output.getvalue() else None


def test_inactive_hook_does_not_create_project_or_global_database(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project = tmp_path / "project"; project.mkdir()
    session = identifier(); index.add(session, project)

    code, output = run_hook(hook_event("UserPromptSubmit", session, project,
                                       prompt="ordinary inactive conversation"))
    assert code == 0 and output == {}
    assert not (project / ".codex").exists()
    assert not (tmp_path / "ignored-global-store").exists()


def test_active_hooks_use_project_store_and_reject_cwd_mismatch(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    project_a.mkdir(); project_b.mkdir()
    session_a, session_b = identifier(), identifier()
    index.add(session_a, project_a); index.add(session_b, project_b)
    server = configured_server()
    create_task(server, project_a, session_a)
    create_task(server, project_b, session_b)

    code, _ = run_hook(hook_event("UserPromptSubmit", session_a, project_a,
                                  prompt="defer this native source"))
    assert code == 0
    with sqlite3.connect(project_a / ".cortex/cortex.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM hook_pending_sources").fetchone()[0] == 1

    code, _ = run_hook(hook_event("UserPromptSubmit", session_a, project_b,
                                  prompt="wrong project"))
    assert code == 1
    with sqlite3.connect(project_b / ".cortex/cortex.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM hook_events").fetchone()[0] == 0


def test_subagent_hook_rejects_child_indexed_in_another_project(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    project_a.mkdir(); project_b.mkdir()
    parent, foreign_child = identifier(), identifier()
    index.add(parent, project_a)
    index.add(foreign_child, project_b, parent)
    server = configured_server()
    create_task(server, project_a, parent)

    code, _ = run_hook(hook_event("SubagentStart", parent, project_a,
                                  agent_id=foreign_child))
    assert code == 1
    with sqlite3.connect(project_a / ".cortex/cortex.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM hook_agent_bindings").fetchone()[0] == 0
    assert not (project_b / ".cortex/cortex.sqlite3").exists()


def test_nested_hook_parent_sessions_bind_only_after_native_mcp_binding(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project = tmp_path / "project"; project.mkdir()
    parent, child, nested = identifier(), identifier(), identifier()
    index.add(parent, project)
    index.add(child, project, parent)
    index.add(nested, project, child)
    server = configured_server()
    create_task(server, project, parent)
    # MCP establishes the child binding; hooks only retain lifecycle receipts.
    successful(dispatch(server, "list_reports", {}, child, parent))

    code, _ = run_hook(hook_event("SubagentStart", parent, project, agent_id=child))
    assert code == 0
    code, _ = run_hook(hook_event("SubagentStart", child, project, agent_id=nested))
    assert code == 0
    with sqlite3.connect(project / ".cortex/cortex.sqlite3") as db:
        agents = {row[0] for row in db.execute("SELECT agent_id FROM hook_agent_bindings")}
    assert agents == {child, nested}


def _store_writer(directory, project, thread, request_key, started, done, results):
    try:
        started.set()
        Store(directory, project_root=project).call(
            "create_draft", {"template": "general", "request_key": request_key}, thread)
        results.put((request_key, "ok"))
    except Exception as error:  # pragma: no cover - asserted through the queue
        results.put((request_key, type(error).__name__))
    finally:
        done.set()


def test_project_database_locks_are_independent_across_processes(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    project_a.mkdir(); project_b.mkdir()
    root_a, root_b = identifier(), identifier()
    index.add(root_a, project_a); index.add(root_b, project_b)
    server = configured_server()
    create_task(server, project_a, root_a)
    create_task(server, project_b, root_b)

    # Spawn avoids inheriting the holder's open SQLite/lock descriptors into a
    # worker process; the assertion therefore exercises the real inter-process
    # lock boundary rather than fork descriptor semantics.
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    b_started, a_started = context.Event(), context.Event()
    b_done, a_done = context.Event(), context.Event()
    worker_b = context.Process(target=_store_writer, args=(
        str(project_b / ".cortex"), str(project_b), root_b, "parallel-b",
        b_started, b_done, results))
    worker_a = context.Process(target=_store_writer, args=(
        str(project_a / ".cortex"), str(project_a), root_a, "parallel-a",
        a_started, a_done, results))

    holder = Store(project_a / ".cortex", project_root=project_a)
    try:
        with holder.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            worker_b.start(); worker_a.start()
            assert b_started.wait(2) and a_started.wait(2)
            assert results.get(timeout=3) == ("parallel-b", "ok")
            assert not a_done.is_set()
            assert db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
            db.commit()
        assert results.get(timeout=5) == ("parallel-a", "ok")
    finally:
        for process in (worker_a, worker_b):
            process.join(timeout=5)
            if process.is_alive():
                process.terminate(); process.join(timeout=2)
    assert worker_a.exitcode == 0 and worker_b.exitcode == 0


def test_multiple_tasks_in_one_project_remain_consistent(tmp_path, monkeypatch):
    index = NativeIndex(tmp_path, monkeypatch)
    project = tmp_path / "project"; project.mkdir()
    first, second = identifier(), identifier()
    index.add(first, project); index.add(second, project)
    server = configured_server()
    create_task(server, project, first, "first-task")
    create_task(server, project, second, "second-task")
    def publish(index):
        return create_report(configured_server(), (first, second)[index % 2], None,
                             'Task evidence ' + str(index))
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(publish, range(8)))
    assert len({report['report_id'] for report in reports}) == 8

    with sqlite3.connect(project / ".cortex/cortex.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 10
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
