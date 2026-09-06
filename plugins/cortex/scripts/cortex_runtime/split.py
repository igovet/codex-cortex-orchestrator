"""Explicit offline export of one project's format-11 metadata."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

from .contracts import StoreError
from .hook_storage import HOOK_SCHEMA
from .store import APPLICATION_ID, SCHEMA, regular, sync_directory


_MUTATIONS = {"create_task", "set_governance", "create_draft", "write_report"}

_SELECTIONS = {
    "tasks": "SELECT x.* FROM tasks x WHERE x.project_root=?",
    "thread_bindings": "SELECT x.* FROM thread_bindings x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "reports": "SELECT x.* FROM reports x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "source_cursors": "SELECT x.* FROM source_cursors x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "source_messages": "SELECT x.* FROM source_messages x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "editions": "SELECT x.* FROM editions x JOIN reports r ON r.id=x.report_id JOIN tasks t ON t.id=r.task_id WHERE t.project_root=?",
    "governance": "SELECT x.* FROM governance x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "drafts": "SELECT x.* FROM drafts x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "pending_source_deletions": "SELECT x.* FROM pending_source_deletions x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "pending_deletions": "SELECT x.* FROM pending_deletions x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "binding_receipts": "SELECT x.* FROM binding_receipts x JOIN thread_bindings b ON b.thread_id=x.thread_id JOIN tasks t ON t.id=b.task_id WHERE t.project_root=?",
    "source_resets": "SELECT x.* FROM source_resets x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "source_revisions": "SELECT x.* FROM source_revisions x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "report_provenance": "SELECT x.* FROM report_provenance x JOIN editions e ON e.sequence=x.edition_sequence JOIN reports r ON r.id=e.report_id JOIN tasks t ON t.id=r.task_id WHERE t.project_root=?",
    "draft_revisions": "SELECT x.* FROM draft_revisions x JOIN drafts d ON d.id=x.draft_id JOIN tasks t ON t.id=d.task_id WHERE t.project_root=?",
    "task_changes": "SELECT x.* FROM task_changes x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "source_turns": "SELECT x.* FROM source_turns x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "hook_pending_sources": "SELECT x.* FROM hook_pending_sources x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "hook_events": "SELECT x.* FROM hook_events x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "hook_hints": "SELECT x.* FROM hook_hints x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
    "hook_agent_bindings": "SELECT x.* FROM hook_agent_bindings x JOIN tasks t ON t.id=x.task_id WHERE t.project_root=?",
}

_COPY_ORDER = (
    "tasks", "thread_bindings", "reports", "source_cursors", "source_messages",
    "editions", "governance", "drafts", "pending_source_deletions",
    "pending_deletions", "binding_receipts", "source_resets", "source_revisions",
    "report_provenance", "draft_revisions", "task_changes", "source_turns",
    "hook_pending_sources", "hook_events", "hook_hints", "hook_agent_bindings",
    "deliveries",
)


def _identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _canonical_directory(value, code, *, private=False):
    path = Path(value)
    try:
        canonical = path.is_absolute() and path.is_dir() and path.resolve() == path
        info = path.lstat() if canonical else None
    except OSError:
        canonical = False; info = None
    if not canonical or info is None or not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise StoreError(code)
    if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise StoreError(code)
    return path


def _fresh_path(value, code, *, private_parent=True):
    path = Path(value)
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise StoreError(code)
    parent = _canonical_directory(path.parent, code, private=private_parent)
    candidate = parent / path.name
    if candidate.exists() or candidate.is_symlink():
        raise StoreError(code)
    return candidate


def _expected_layout():
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(SCHEMA + HOOK_SCHEMA)
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        columns = {
            table: tuple(tuple(row) for row in db.execute(f"PRAGMA table_xinfo({_identifier(table)})"))
            for table in tables
        }
        foreign_keys = {
            table: tuple(tuple(row) for row in db.execute(f"PRAGMA foreign_key_list({_identifier(table)})"))
            for table in tables
        }
        return tables, columns, foreign_keys
    finally:
        db.close()


def _validate_format(db):
    if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
        raise StoreError("unsupported_storage")
    if db.execute("PRAGMA user_version").fetchone()[0] != 11:
        raise StoreError("unsupported_storage")
    expected, columns, foreign_keys = _expected_layout()
    actual = {
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    views = db.execute("SELECT 1 FROM sqlite_master WHERE type='view' LIMIT 1").fetchone()
    if actual != expected or views is not None:
        raise StoreError("unsupported_storage")
    for table in sorted(expected):
        observed_columns = tuple(tuple(row) for row in db.execute(f"PRAGMA table_xinfo({_identifier(table)})"))
        observed_foreign_keys = tuple(tuple(row) for row in db.execute(f"PRAGMA foreign_key_list({_identifier(table)})"))
        if observed_columns != columns[table] or observed_foreign_keys != foreign_keys[table]:
            raise StoreError("unsupported_storage")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise StoreError("split_source_invalid")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise StoreError("split_source_invalid")


def _ensure_relations(db):
    checks = (
        """SELECT 1 FROM thread_bindings b LEFT JOIN thread_bindings p ON p.thread_id=b.parent_thread_id
             WHERE b.parent_thread_id IS NOT NULL AND (p.thread_id IS NULL OR p.task_id!=b.task_id) LIMIT 1""",
        """SELECT 1 FROM source_cursors x LEFT JOIN thread_bindings b ON b.thread_id=x.thread_id AND b.task_id=x.task_id
             WHERE b.thread_id IS NULL LIMIT 1""",
        """SELECT 1 FROM source_messages x LEFT JOIN thread_bindings b ON b.thread_id=x.thread_id AND b.task_id=x.task_id
             LEFT JOIN reports r ON r.id=x.report_id AND r.task_id=x.task_id
             WHERE b.thread_id IS NULL OR r.id IS NULL LIMIT 1""",
        """SELECT 1 FROM governance x LEFT JOIN reports r ON r.id=x.report_id AND r.task_id=x.task_id
             WHERE r.id IS NULL LIMIT 1""",
        """SELECT 1 FROM drafts x LEFT JOIN thread_bindings b ON b.thread_id=x.owner_thread_id AND b.task_id=x.task_id
             LEFT JOIN reports r ON r.id=x.published_report_id AND r.task_id=x.task_id
             WHERE b.thread_id IS NULL OR (x.published_report_id IS NOT NULL AND r.id IS NULL) LIMIT 1""",
        """SELECT 1 FROM pending_source_deletions x LEFT JOIN drafts d ON d.id=x.draft_id AND d.task_id=x.task_id
             WHERE d.id IS NULL LIMIT 1""",
        """SELECT 1 FROM source_resets x LEFT JOIN thread_bindings b ON b.thread_id=x.thread_id AND b.task_id=x.task_id
             WHERE b.thread_id IS NULL LIMIT 1""",
        """SELECT 1 FROM source_revisions x LEFT JOIN reports r ON r.id=x.report_id AND r.task_id=x.task_id
             WHERE r.id IS NULL OR (
                 EXISTS (SELECT 1 FROM source_messages m
                         WHERE m.thread_id=x.thread_id AND m.message_id=x.message_id)
                 AND NOT EXISTS (SELECT 1 FROM source_messages m
                         WHERE m.thread_id=x.thread_id AND m.message_id=x.message_id
                           AND m.task_id=x.task_id AND m.report_id=x.report_id)
             ) LIMIT 1""",
        """SELECT 1 FROM report_provenance x JOIN editions e ON e.sequence=x.edition_sequence
             JOIN reports r ON r.id=e.report_id
             WHERE x.source_revision!=0 AND NOT EXISTS (
                 SELECT 1 FROM source_revisions s WHERE s.task_id=r.task_id AND s.revision=x.source_revision
             ) LIMIT 1""",
        """SELECT 1 FROM draft_revisions x JOIN drafts d ON d.id=x.draft_id
             WHERE x.source_revision!=0 AND NOT EXISTS (
                 SELECT 1 FROM source_revisions s WHERE s.task_id=d.task_id AND s.revision=x.source_revision
             ) LIMIT 1""",
        """SELECT 1 FROM source_turns x LEFT JOIN reports r ON r.id=x.report_id AND r.task_id=x.task_id
             WHERE r.id IS NULL OR NOT EXISTS (
                 SELECT 1 FROM thread_bindings b WHERE b.thread_id=x.thread_id AND b.task_id=x.task_id
             ) LIMIT 1""",
        """SELECT 1 FROM hook_agent_bindings x LEFT JOIN thread_bindings p
                  ON p.thread_id=x.parent_thread_id AND p.task_id=x.task_id
             LEFT JOIN thread_bindings a ON a.thread_id=x.agent_id
             WHERE p.thread_id IS NULL OR (a.thread_id IS NOT NULL AND a.task_id!=x.task_id) LIMIT 1""",
        """SELECT 1 FROM hook_pending_sources x WHERE NOT EXISTS (
                 SELECT 1 FROM thread_bindings b WHERE b.thread_id=x.thread_id AND b.task_id=x.task_id
             ) AND NOT EXISTS (
                 SELECT 1 FROM hook_agent_bindings a WHERE a.agent_id=x.thread_id AND a.task_id=x.task_id
             ) LIMIT 1""",
        """SELECT 1 FROM hook_events x WHERE NOT EXISTS (
                 SELECT 1 FROM thread_bindings b WHERE b.thread_id=x.thread_id AND b.task_id=x.task_id
             ) AND NOT EXISTS (
                 SELECT 1 FROM hook_agent_bindings a WHERE a.agent_id=x.thread_id AND a.task_id=x.task_id
             ) LIMIT 1""",
        """SELECT 1 FROM hook_hints x WHERE NOT EXISTS (
                 SELECT 1 FROM thread_bindings b WHERE b.thread_id=x.thread_id AND b.task_id=x.task_id
             ) AND NOT EXISTS (
                 SELECT 1 FROM hook_agent_bindings a WHERE a.agent_id=x.thread_id AND a.task_id=x.task_id
             ) LIMIT 1""",
        """SELECT 1 FROM task_changes x WHERE x.kind NOT IN
                  ('source','report','hook','artifact','source_gap','source_pending') LIMIT 1""",
        """SELECT 1 FROM task_changes x WHERE x.kind IN ('source','report') AND NOT EXISTS (
                 SELECT 1 FROM reports r WHERE r.id=x.reference AND r.task_id=x.task_id
             ) LIMIT 1""",
        """SELECT 1 FROM task_changes x WHERE x.kind IN ('hook','artifact') AND NOT EXISTS (
                 SELECT 1 FROM hook_events h WHERE h.event_key=x.reference AND h.task_id=x.task_id
             ) LIMIT 1""",
        """SELECT 1 FROM task_changes x WHERE x.kind='source_pending' AND x.reference IS NOT NULL LIMIT 1""",
    )
    for query in checks:
        if db.execute(query).fetchone() is not None:
            raise StoreError("split_source_relations_invalid")


def _delivery_task(db, row):
    try:
        response = json.loads(row["response"])
    except (TypeError, ValueError):
        raise StoreError("split_source_relations_invalid") from None
    if row["operation"] not in _MUTATIONS or not isinstance(response, dict):
        raise StoreError("split_source_relations_invalid")
    operation = row["operation"]
    if operation == "create_task":
        task = response.get("task_id")
        if not isinstance(task, str) or not row["scope"].startswith("new-task:"):
            raise StoreError("split_source_relations_invalid")
        thread = row["scope"][len("new-task:"):]
        relation = db.execute(
            "SELECT 1 FROM thread_bindings WHERE thread_id=? AND task_id=? AND parent_thread_id IS NULL",
            (thread, task),
        ).fetchone()
    else:
        task = row["scope"]
        relation = db.execute("SELECT 1 FROM tasks WHERE id=?", (task,)).fetchone()
    if relation is None:
        raise StoreError("split_source_relations_invalid")
    if operation == "create_task":
        linked = db.execute(
            "SELECT 1 FROM reports WHERE id=? AND task_id=?", (response.get("original_report_id"), task)
        ).fetchone()
    elif operation == "set_governance":
        linked = db.execute(
            "SELECT 1 FROM governance WHERE id=? AND report_id=? AND task_id=?",
            (response.get("governance_id"), response.get("report_id"), task),
        ).fetchone()
    elif operation == "create_draft":
        linked = db.execute(
            "SELECT 1 FROM drafts WHERE id=? AND path=? AND task_id=?",
            (response.get("draft_id"), response.get("draft_path"), task),
        ).fetchone()
    else:
        linked = db.execute(
            "SELECT 1 FROM reports WHERE id=? AND task_id=?", (response.get("report_id"), task)
        ).fetchone()
    if linked is None:
        raise StoreError("split_source_relations_invalid")
    return task


def _selected_deliveries(db, project_root):
    for row in db.execute("SELECT * FROM deliveries ORDER BY scope,operation,delivery_key"):
        task = _delivery_task(db, row)
        selected = db.execute(
            "SELECT 1 FROM tasks WHERE id=? AND project_root=?", (task, project_root)
        ).fetchone()
        if selected is not None:
            yield tuple(row)


def _add_value(hashed, value):
    if value is None:
        hashed.update(b"n"); return
    if isinstance(value, bytes):
        body = value; tag = b"b"
    elif isinstance(value, str):
        body = value.encode("utf-8"); tag = b"s"
    elif isinstance(value, int):
        body = str(value).encode("ascii"); tag = b"i"
    elif isinstance(value, float):
        body = value.hex().encode("ascii"); tag = b"f"
    else:
        raise StoreError("unsupported_storage")
    hashed.update(tag); hashed.update(len(body).to_bytes(8, "big")); hashed.update(body)


def _rows_digest(rows):
    hashed = hashlib.sha256(); count = 0
    for row in rows:
        count += 1; hashed.update(b"r")
        for value in row:
            _add_value(hashed, value)
    return count, hashed.hexdigest()


def _primary_order(db, table, columns):
    info = db.execute(f"PRAGMA table_info({_identifier(table)})").fetchall()
    primary = [row[1] for row in sorted((row for row in info if row[5]), key=lambda row: row[5])]
    return primary or columns


def _database_digest(db):
    hashed = hashlib.sha256()
    tables = [
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    for table in tables:
        columns = [row[1] for row in db.execute(f"PRAGMA table_info({_identifier(table)})")]
        order = _primary_order(db, table, columns)
        query = "SELECT " + ",".join(map(_identifier, columns)) + " FROM " + _identifier(table)
        if order:
            query += " ORDER BY " + ",".join(map(_identifier, order))
        hashed.update(table.encode("utf-8")); hashed.update(b"\0")
        for row in db.execute(query):
            hashed.update(b"r")
            for value in row:
                _add_value(hashed, value)
    return hashed.hexdigest()


def _file_hashes(source):
    result = {}
    for suffix in ("", "-wal"):
        path = Path(str(source) + suffix)
        if not path.exists() and not path.is_symlink():
            continue
        regular(path)
        hashed = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            while block := stream.read(64 * 1024):
                hashed.update(block)
        result[suffix] = hashed.hexdigest()
    return result


def _create_backup(source_db, backup):
    fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    target = None
    try:
        target = sqlite3.connect(backup)
        source_db.backup(target)
        target.row_factory = sqlite3.Row
        _validate_format(target)
        if _database_digest(source_db) != _database_digest(target):
            raise StoreError("split_backup_invalid")
        target.close(); target = None
        os.chmod(backup, 0o600, follow_symlinks=False); regular(backup)
        with backup.open("rb") as stream:
            os.fsync(stream.fileno())
        sync_directory(backup.parent)
    except BaseException:
        if target is not None:
            target.close()
        try:
            backup.unlink(); sync_directory(backup.parent)
        except FileNotFoundError:
            pass
        raise


def _create_destination(source_db, temporary, project_root):
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    target = sqlite3.connect(temporary, isolation_level=None)
    target.row_factory = sqlite3.Row
    try:
        target.execute("PRAGMA foreign_keys=ON")
        target.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + HOOK_SCHEMA +
                             f"PRAGMA application_id={APPLICATION_ID};\nCOMMIT;")
        target.execute("BEGIN IMMEDIATE")
        expected = {}
        for table in _COPY_ORDER:
            columns = [row[1] for row in target.execute(f"PRAGMA table_info({_identifier(table)})")]
            placeholders = ",".join("?" for _ in columns)
            insert = ("INSERT INTO " + _identifier(table) + " (" +
                      ",".join(map(_identifier, columns)) + ") VALUES (" + placeholders + ")")
            if table == "deliveries":
                rows = _selected_deliveries(source_db, project_root)
            else:
                order = _primary_order(source_db, table, columns)
                query = _SELECTIONS[table] + " ORDER BY " + ",".join("x." + _identifier(column) for column in order)
                rows = source_db.execute(query, (project_root,))
            hashed = hashlib.sha256(); count = 0
            for row in rows:
                values = tuple(row); target.execute(insert, values); count += 1; hashed.update(b"r")
                for value in values:
                    _add_value(hashed, value)
            expected[table] = (count, hashed.hexdigest())
        if expected["tasks"][0] == 0:
            raise StoreError("split_project_not_found")
        target.commit()
        _validate_format(target)
        roots = target.execute("SELECT DISTINCT project_root FROM tasks").fetchall()
        if [row[0] for row in roots] != [project_root]:
            raise StoreError("split_destination_invalid")
        for table in _COPY_ORDER:
            columns = [row[1] for row in target.execute(f"PRAGMA table_info({_identifier(table)})")]
            order = _primary_order(target, table, columns)
            query = "SELECT " + ",".join(map(_identifier, columns)) + " FROM " + _identifier(table)
            query += " ORDER BY " + ",".join(map(_identifier, order))
            if _rows_digest(target.execute(query)) != expected[table]:
                raise StoreError("split_destination_invalid")
        target.close(); target = None
        os.chmod(temporary, 0o600, follow_symlinks=False); regular(temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        sync_directory(temporary.parent)
    finally:
        if target is not None:
            target.rollback(); target.close()


def _publish(temporary, destination):
    linked = False
    try:
        os.link(temporary, destination, follow_symlinks=False); linked = True
        sync_directory(destination.parent)
        temporary.unlink(); sync_directory(destination.parent)
        info = regular(destination)
        return info.st_dev, info.st_ino
    except FileExistsError:
        raise StoreError("split_destination_exists") from None
    except BaseException:
        if linked:
            try:
                destination.unlink(); sync_directory(destination.parent)
            except OSError:
                pass
        raise


def split_project(source_directory, project_root, backup, *, access_stopped=False):
    """Export only one canonical project's metadata into its fresh local database."""
    if not access_stopped:
        raise StoreError("split_requires_stopped_access")
    source_directory = _canonical_directory(source_directory, "unsafe_storage", private=True)
    project_root = _canonical_directory(project_root, "invalid_project")
    source = source_directory / "cortex.sqlite3"; regular(source)
    destination_directory = _canonical_directory(
        project_root / ".codex" / "cortex", "unsafe_destination", private=True
    )
    destination = destination_directory / "cortex.sqlite3"
    backup = _fresh_path(backup, "split_backup_exists")
    if backup == source or backup == destination:
        raise StoreError("unsafe_storage")

    source_lock = source_directory / ".access.lock"
    destination_lock = destination_directory / ".access.lock"
    source_lock_fd = None; destination_lock_fd = None
    source_db = None; probe = None; temporary = None
    published_identity = None; succeeded = False; destination_locked = False
    try:
        source_lock_fd = os.open(source_lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            destination_lock_fd = os.open(
                destination_lock, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            destination_lock_fd = os.open(destination_lock, os.O_RDWR | os.O_NOFOLLOW)
        regular(source_lock); regular(destination_lock)
        try:
            fcntl.flock(source_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(destination_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            destination_locked = True
        except BlockingIOError:
            raise StoreError("storage_busy") from None
        for candidate in (destination, Path(str(destination) + "-wal"),
                          Path(str(destination) + "-shm"), Path(str(destination) + "-journal")):
            if candidate.exists() or candidate.is_symlink():
                raise StoreError("split_destination_exists")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(source) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                raise StoreError("split_source_not_quiescent")
        before = _file_hashes(source)
        # Keep the no-op SQLite write transaction for the complete export. The
        # immutable reader can inspect the clean main database without being
        # blocked by our own lock, while independent SQLite writers are denied.
        probe = sqlite3.connect(source, timeout=0, isolation_level=None)
        probe.execute("PRAGMA locking_mode=EXCLUSIVE")
        probe.execute("BEGIN EXCLUSIVE")
        source_db = sqlite3.connect(source.resolve().as_uri() + "?mode=ro&immutable=1", uri=True,
                                    timeout=0, isolation_level=None)
        source_db.row_factory = sqlite3.Row
        source_db.execute("PRAGMA foreign_keys=ON")
        source_db.execute("BEGIN")
        _validate_format(source_db); _ensure_relations(source_db)
        selected = source_db.execute(
            "SELECT COUNT(*) FROM tasks WHERE project_root=?", (str(project_root),)
        ).fetchone()[0]
        if selected == 0:
            raise StoreError("split_project_not_found")
        for row in source_db.execute(
            "SELECT path FROM drafts JOIN tasks ON tasks.id=drafts.task_id WHERE tasks.project_root=?",
            (str(project_root),),
        ):
            path = Path(row[0])
            allowed = {
                project_root / ".cortex" / "draft-reports",
                project_root / ".cortex" / "pipeline-drafts",
            }
            if not path.is_absolute() or path.parent not in allowed:
                raise StoreError("split_source_relations_invalid")
        # Validate every delivery before the selected iterator is consumed.
        for row in source_db.execute("SELECT * FROM deliveries"):
            _delivery_task(source_db, row)
        _create_backup(source_db, backup)
        temporary = destination_directory / (".cortex.sqlite3.split-" + uuid.uuid4().hex + ".tmp")
        _create_destination(source_db, temporary, str(project_root))
        source_db.rollback(); source_db.close(); source_db = None
        published_identity = _publish(temporary, destination); temporary = None
        probe.rollback(); probe.close(); probe = None
        if any(Path(str(source) + suffix).exists() or Path(str(source) + suffix).is_symlink()
               for suffix in ("-wal", "-shm", "-journal")) or _file_hashes(source) != before:
            raise StoreError("split_source_changed")
        succeeded = True
        return dict(project_root=str(project_root), destination=str(destination), backup=str(backup),
                    tasks=selected, schema_version=11, application_id=APPLICATION_ID,
                    markdown_copied=False, source_modified=False)
    except StoreError:
        raise
    except (OSError, sqlite3.Error, UnicodeError, ValueError):
        raise StoreError("split_failed") from None
    finally:
        if source_db is not None:
            source_db.rollback(); source_db.close()
        if probe is not None:
            probe.rollback(); probe.close()
        if not succeeded and published_identity is not None:
            try:
                info = destination.lstat()
                if (info.st_dev, info.st_ino) == published_identity:
                    destination.unlink(); sync_directory(destination.parent)
            except FileNotFoundError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(); sync_directory(temporary.parent)
            except FileNotFoundError:
                pass
        if destination_locked and destination_lock_fd is not None:
            fcntl.flock(destination_lock_fd, fcntl.LOCK_UN)
        if destination_lock_fd is not None:
            os.close(destination_lock_fd)
        if source_lock_fd is not None:
            fcntl.flock(source_lock_fd, fcntl.LOCK_UN); os.close(source_lock_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage-dir", required=True,
                        help="Absolute owner-private directory containing the legacy global cortex.sqlite3.")
    parser.add_argument("--project-root", required=True,
                        help="Absolute canonical project whose metadata will be exported.")
    parser.add_argument("--backup", required=True,
                        help="New owner-private path for the verified global SQLite backup; never overwritten.")
    parser.add_argument("--access-stopped", action="store_true",
                        help="Acknowledge all CLI, Desktop, MCP, hook, and legacy database access is stopped.")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        result = split_project(args.source_storage_dir, args.project_root, args.backup,
                               access_stopped=args.access_stopped)
    except (StoreError, OSError, sqlite3.Error):
        parser.exit(1, "Cortex project split failed; no private details are displayed.\n")
    print("Project metadata split to " + result["destination"] +
          "; verified SQLite backup: " + result["backup"] +
          "; legacy SQLite and Markdown unchanged.")
    return 0
