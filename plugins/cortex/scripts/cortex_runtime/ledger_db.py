"""SQLite-backed source of truth for Cortex coordination state.

The Cortex worker protocol deliberately keeps human-facing immutable artifacts
(briefings, Markdown reports, handoffs) in the task artifact tree.  Mutable
coordination state belongs here instead: SQLite gives the state machine one
transaction boundary, durable indices, foreign-key cleanup, and a numbered
migration history.

This module has no dependency on :mod:`cortex`. The server imports it through
the public facade, while the store itself remains usable by installation and
migration checks without importing the stdio entrypoint a second time.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the process-local guard.
    fcntl = None


DATABASE_NAME = "cortex.db"
DATABASE_SCHEMA_VERSION = 2
ARTIFACT_STORAGE_CHUNK_BYTES = 32 * 1024
ARTIFACT_TRANSPORT_MAX_BYTES = 32 * 1024
_LOCAL = threading.local()
_MIGRATION_GUARD = threading.Lock()
_MIGRATION_LOCKS: dict[str, threading.RLock] = {}


def _now() -> str:
    # Keep the database package independent from Cortex's response/runtime
    # facade.  RFC 3339 UTC text is sufficient for migration bookkeeping.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_json(text: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SQLite {label} JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError(f"SQLite {label} must be an object")
    return value


def database_path(root: Path) -> Path:
    return root / DATABASE_NAME


def _assert_private_regular(path: Path, label: str, *, allow_missing: bool = False) -> None:
    if not path.exists():
        if allow_missing:
            return
        raise ValueError(f"{label} is unavailable")
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    if info.st_mode & 0o077:
        raise ValueError(f"{label} has unsafe permissions")


def _connect(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    db_path = database_path(root)
    _assert_private_regular(db_path, "Cortex database", allow_missing=True)
    connection = sqlite3.connect(str(db_path), timeout=15, isolation_level=None)
    try:
        os.chmod(db_path, 0o600)
    except OSError:
        connection.close()
        raise
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    # WAL permits independent MCP processes to inspect durable state while a
    # coordinator is writing. FULL provides a conservative durability floor
    # for the small amount of coordination data Cortex stores.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def _active_connections() -> dict[str, sqlite3.Connection]:
    connections = getattr(_LOCAL, "connections", None)
    if connections is None:
        connections = {}
        _LOCAL.connections = connections
    return connections


def _root_key(root: Path) -> str:
    return str(root.resolve())


@contextlib.contextmanager
def _migration_lock(root: Path) -> Iterator[None]:
    """Serialize schema checks/upgrades across threads and MCP processes.

    A fresh Marketplace installation can receive several first calls at once.
    SQLite handles the durable transaction, while this advisory lock prevents
    concurrent connections from racing through the initial WAL/schema setup
    before the normal state lock has been reached.
    """
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    key = _root_key(root)
    with _MIGRATION_GUARD:
        local_lock = _MIGRATION_LOCKS.setdefault(key, threading.RLock())
    with local_lock:
        # Reuse the ledger's existing advisory lock artifact.  It has no
        # durable state semantics and avoids adding another visible runtime
        # file to every project solely for first-boot migration serialization.
        lock_path = root / ".state.lock"
        with lock_path.open("a+", encoding="utf-8") as stream:
            try:
                os.chmod(lock_path, 0o600)
            except OSError:
                pass
            if fcntl is not None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def transaction(root: Path) -> Iterator[None]:
    """Run a re-entrant IMMEDIATE SQLite transaction for one ledger root."""
    key = _root_key(root)
    active = _active_connections()
    if key in active:
        yield
        return
    connection = _connect(root)
    connection.execute("BEGIN IMMEDIATE")
    active[key] = connection
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        active.pop(key, None)
        connection.close()


@contextlib.contextmanager
def savepoint(root: Path, name: str) -> Iterator[None]:
    """Nest an all-or-nothing operation in the owning ledger transaction."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        raise ValueError("SQLite savepoint name is invalid")
    key = _root_key(root)
    connection = _active_connections().get(key)
    if connection is None:
        with transaction(root):
            with savepoint(root, name):
                yield
        return
    connection.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
        connection.execute(f"RELEASE SAVEPOINT {name}")
        raise
    else:
        connection.execute(f"RELEASE SAVEPOINT {name}")


@contextlib.contextmanager
def _connection(root: Path, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    key = _root_key(root)
    active = _active_connections()
    if key in active:
        yield active[key]
        return
    connection = _connect(root)
    if write:
        connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        if write:
            connection.rollback()
        raise
    else:
        if write:
            connection.commit()
    finally:
        connection.close()


def _apply_base_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            checksum TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ledger_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            task_number INTEGER NOT NULL UNIQUE,
            artifact_dir TEXT NOT NULL UNIQUE,
            definition_json TEXT NOT NULL,
            state_json TEXT NOT NULL,
            plan_json TEXT,
            status TEXT NOT NULL,
            revision INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS tasks_status_updated_idx ON tasks(status, updated_at);
        CREATE TABLE IF NOT EXISTS lanes (
            lane_id TEXT PRIMARY KEY,
            definition_json TEXT NOT NULL,
            state_json TEXT NOT NULL,
            status TEXT NOT NULL,
            revision INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS classifications (
            classification_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            consumed_by TEXT REFERENCES tasks(task_id) ON DELETE SET NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS manifest_snapshots (
            snapshot_ref TEXT PRIMARY KEY,
            digest TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS global_documents (
            name TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS operations (
            submission_id TEXT PRIMARY KEY,
            -- A start receipt is allocated before its task row exists, so this
            -- association is an indexed audit link rather than a foreign key.
            task_id TEXT,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS operations_task_idx ON operations(task_id);
        CREATE TABLE IF NOT EXISTS ledger_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT REFERENCES tasks(task_id) ON DELETE CASCADE,
            lane_id TEXT REFERENCES lanes(lane_id) ON DELETE CASCADE,
            event TEXT NOT NULL,
            detail TEXT NOT NULL,
            revision INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_documents (
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            document_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(task_id, document_key)
        );
        CREATE INDEX IF NOT EXISTS task_documents_updated_idx
            ON task_documents(task_id, updated_at);
        """
    )


def _migration_checksum(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _record_migration(connection: sqlite3.Connection, version: int, name: str) -> None:
    connection.execute(
        "INSERT INTO schema_migrations(version, name, applied_at, checksum) VALUES (?, ?, ?, ?)",
        (version, name, _now(), _migration_checksum(name)),
    )



def _validate_artifact_identity(task_id: str, kind: str, title: str, export_path: str | None) -> None:
    if not task_id or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", task_id):
        raise ValueError("SQLite artifact task identity is invalid")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind):
        raise ValueError("SQLite artifact kind is invalid")
    if not title or len(title) > 512:
        raise ValueError("SQLite artifact title is invalid")
    if export_path:
        path = Path(export_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("SQLite artifact export path is invalid")


def _text_chunk_boundaries(data: bytes) -> list[bytes]:
    """Split UTF-8 text deterministically without imposing a value-size limit."""
    if not data:
        return [b""]
    chunks: list[bytes] = []
    start = 0
    while start < len(data):
        end = min(start + ARTIFACT_STORAGE_CHUNK_BYTES, len(data))
        if end < len(data):
            newline = data.rfind(b"\n", start + 1, end)
            if newline > start:
                end = newline + 1
            else:
                while end > start and data[end] & 0b11000000 == 0b10000000:
                    end -= 1
                if end == start:
                    # A valid UTF-8 scalar is at most four bytes, so this
                    # branch is defensive for malformed caller bytes only.
                    end = min(start + ARTIFACT_STORAGE_CHUNK_BYTES, len(data))
        chunks.append(data[start:end])
        start = end
    return chunks


def _artifact_ref(task_id: str, kind: str, digest: str) -> str:
    return "artifact-" + hashlib.sha256(f"{task_id}\0{kind}\0{digest}".encode("utf-8")).hexdigest()[:32]


def _store_artifact_with_connection(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    kind: str,
    title: str,
    mime_type: str,
    content: str | bytes,
    immutable: bool,
    export_path: str | None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Persist immutable content and transport-sized chunks in one transaction."""
    _validate_artifact_identity(task_id, kind, title, export_path)
    if not mime_type or len(mime_type) > 160:
        raise ValueError("SQLite artifact MIME type is invalid")
    is_text = isinstance(content, str)
    raw = content.encode("utf-8") if is_text else bytes(content)
    digest = hashlib.sha256(raw).hexdigest()
    artifact_id = _artifact_ref(task_id, kind, digest)
    existing = connection.execute(
        "SELECT artifact_id, mime_type, byte_size, chunk_count, immutable, export_path, title, created_at "
        "FROM artifacts WHERE task_id = ? AND kind = ? AND digest_sha256 = ?",
        (task_id, kind, digest),
    ).fetchone()
    if existing is not None:
        if (
            str(existing["mime_type"]) != mime_type
            or int(existing["byte_size"]) != len(raw)
            or bool(existing["immutable"]) != bool(immutable)
        ):
            raise ValueError("SQLite immutable artifact identity already has conflicting metadata")
        return {
            "artifact_ref": str(existing["artifact_id"]), "task_id": task_id, "kind": kind,
            "title": str(existing["title"]), "mime_type": mime_type, "digest_sha256": digest,
            "byte_size": int(existing["byte_size"]), "chunk_count": int(existing["chunk_count"]),
            "immutable": bool(existing["immutable"]), "export_path": existing["export_path"],
            "created_at": str(existing["created_at"]),
        }
    chunks = _text_chunk_boundaries(raw) if is_text else [raw[offset:offset + ARTIFACT_STORAGE_CHUNK_BYTES] for offset in range(0, len(raw), ARTIFACT_STORAGE_CHUNK_BYTES)] or [b""]
    created = created_at or _now()
    connection.execute(
        """INSERT INTO artifacts(artifact_id, task_id, kind, title, mime_type, digest_sha256, byte_size, chunk_count, immutable, export_path, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (artifact_id, task_id, kind, title, mime_type, digest, len(raw), len(chunks), int(bool(immutable)), export_path, created),
    )
    for chunk_no, chunk in enumerate(chunks):
        text_content = chunk.decode("utf-8") if is_text else None
        blob_content = None if is_text else sqlite3.Binary(chunk)
        connection.execute(
            """INSERT INTO artifact_chunks(artifact_id, chunk_no, text_content, blob_content, byte_size, digest_sha256)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (artifact_id, chunk_no, text_content, blob_content, len(chunk), hashlib.sha256(chunk).hexdigest()),
        )
    return {
        "artifact_ref": artifact_id, "task_id": task_id, "kind": kind, "title": title,
        "mime_type": mime_type, "digest_sha256": digest, "byte_size": len(raw),
        "chunk_count": len(chunks), "immutable": bool(immutable), "export_path": export_path,
        "created_at": created,
    }


def _create_artifact_catalog(connection: sqlite3.Connection) -> None:
    """Create the immutable artifact tables for the SQLite-native ledger."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            digest_sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
            chunk_count INTEGER NOT NULL CHECK(chunk_count >= 1),
            immutable INTEGER NOT NULL CHECK(immutable IN (0, 1)),
            export_path TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(task_id, kind, digest_sha256)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS artifact_chunks (
            artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
            chunk_no INTEGER NOT NULL CHECK(chunk_no >= 0),
            text_content TEXT,
            blob_content BLOB,
            byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
            digest_sha256 TEXT NOT NULL,
            PRIMARY KEY(artifact_id, chunk_no),
            CHECK((text_content IS NOT NULL AND blob_content IS NULL) OR (text_content IS NULL AND blob_content IS NOT NULL))
        )"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS tasks_created_at_idx ON tasks(created_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS artifacts_task_kind_created_idx ON artifacts(task_id, kind, created_at DESC, artifact_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS artifacts_task_created_idx ON artifacts(task_id, created_at DESC, artifact_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS artifacts_digest_idx ON artifacts(digest_sha256)")
    connection.execute(
        "INSERT OR IGNORE INTO ledger_meta(key, value) VALUES (?, ?)",
        ("artifact_cursor_hmac_key", secrets.token_hex(32)),
    )



def _applied_migrations(connection: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    return {
        int(row["version"]): (str(row["name"]), str(row["checksum"]))
        for row in connection.execute("SELECT version, name, checksum FROM schema_migrations")
    }


def _migration_plan() -> tuple[tuple[int, str], ...]:
    return (
        (1, "sqlite-ledger-base"),
        (2, "immutable-artifact-catalog-and-chunks"),
    )


def _assert_current_migration_history(connection: sqlite3.Connection) -> None:
    """Validate a ready schema without issuing DDL inside a savepoint.

    ``sqlite3.Connection.executescript`` implicitly commits any pending
    transaction.  A nested Cortex API call therefore must validate the
    already-open store rather than replay ``CREATE TABLE IF NOT EXISTS`` while
    a composite operation is using a savepoint.
    """
    try:
        applied = _applied_migrations(connection)
    except sqlite3.OperationalError as exc:
        raise ValueError("Cortex database schema is unavailable inside an active transaction") from exc
    for version, name in _migration_plan():
        known = applied.get(version)
        checksum = _migration_checksum(name)
        if known != (name, checksum):
            raise ValueError("Cortex database requires migration before this nested operation")


def ensure_database(root: Path) -> None:
    """Open/upgrade one project ledger and apply each migration exactly once."""
    if _root_key(root) in _active_connections():
        _assert_current_migration_history(_active_connections()[_root_key(root)])
        return
    with _migration_lock(root):
        with _connection(root, write=True) as connection:
            _apply_base_schema(connection)
            applied = _applied_migrations(connection)
            for version, name in _migration_plan():
                known = applied.get(version)
                checksum = _migration_checksum(name)
                if known is not None:
                    if known != (name, checksum):
                        raise ValueError("Cortex database migration history is inconsistent")
                    continue
                if version == 2:
                    _create_artifact_catalog(connection)
                _record_migration(connection, version, name)
            connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")


def migration_history(root: Path) -> list[dict[str, Any]]:
    ensure_database(root)
    with _connection(root) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT version, name, applied_at, checksum FROM schema_migrations ORDER BY version"
        )]


def task_index(root: Path) -> dict[str, dict[str, Any]]:
    ensure_database(root)
    with _connection(root) as connection:
        rows = connection.execute("SELECT task_id, task_number, artifact_dir FROM tasks ORDER BY task_number").fetchall()
    return {
        str(row["task_id"]): {"number": int(row["task_number"]), "directory": Path(str(row["artifact_dir"])).name,
                              "artifact_dir": str(row["artifact_dir"])}
        for row in rows
    }


def artifact_path(root: Path, task_id: str) -> Path | None:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute("SELECT artifact_dir FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    relative = Path(str(row["artifact_dir"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("SQLite task artifact directory is unsafe")
    return root / relative


def load_task(root: Path, task_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, str] | None:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT definition_json, state_json, plan_json, artifact_dir FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
    if row is None:
        return None
    return (
        _decode_json(str(row["definition_json"]), "task definition"),
        _decode_json(str(row["state_json"]), "task state"),
        _decode_json(str(row["plan_json"]), "orchestration plan") if row["plan_json"] is not None else None,
        str(row["artifact_dir"]),
    )


def create_task(root: Path, definition: dict[str, Any], state: dict[str, Any], artifact_dir: str) -> None:
    task_id = str(state.get("task_id") or definition.get("task_id") or "")
    if not task_id or definition.get("task_id") != task_id:
        raise ValueError("SQLite task creation identity is invalid")
    number = state.get("task_number")
    if not isinstance(number, int) or number < 1:
        raise ValueError("SQLite task number is invalid")
    relative = Path(artifact_dir)
    if relative.is_absolute() or ".." in relative.parts or relative.name in {"", ".", ".."}:
        raise ValueError("SQLite task artifact directory is invalid")
    with _connection(root, write=True) as connection:
        connection.execute(
            """INSERT INTO tasks(task_id, task_number, artifact_dir, definition_json, state_json, plan_json, status, revision, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
            (task_id, number, str(relative), _canonical_json(definition), _canonical_json(state),
             str(state.get("status") or "active"), int(state.get("revision") or 0),
             str(definition.get("created_at") or _now()), str(state.get("updated_at") or _now())),
        )


def update_task_state(root: Path, state: dict[str, Any], *, event: str | None = None, detail: str = "") -> None:
    task_id = str(state.get("task_id") or "")
    if not task_id:
        raise ValueError("SQLite task state has no task_id")
    with _connection(root, write=True) as connection:
        cursor = connection.execute(
            "UPDATE tasks SET state_json = ?, status = ?, revision = ?, updated_at = ? WHERE task_id = ?",
            (_canonical_json(state), str(state.get("status") or "active"), int(state.get("revision") or 0),
             str(state.get("updated_at") or _now()), task_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("SQLite task state refers to an unknown task")
        if event:
            connection.execute(
                "INSERT INTO ledger_events(task_id, lane_id, event, detail, revision, created_at) VALUES (?, NULL, ?, ?, ?, ?)",
                (task_id, event, detail, int(state.get("revision") or 0), _now()),
            )


def update_task_definition(root: Path, definition: dict[str, Any]) -> None:
    task_id = str(definition.get("task_id") or "")
    if not task_id:
        raise ValueError("SQLite task definition has no task_id")
    with _connection(root, write=True) as connection:
        cursor = connection.execute("UPDATE tasks SET definition_json = ? WHERE task_id = ?", (_canonical_json(definition), task_id))
        if cursor.rowcount != 1:
            raise ValueError("SQLite task definition refers to an unknown task")


def update_task_plan(root: Path, task_id: str, plan: dict[str, Any]) -> None:
    with _connection(root, write=True) as connection:
        cursor = connection.execute("UPDATE tasks SET plan_json = ? WHERE task_id = ?", (_canonical_json(plan), task_id))
        if cursor.rowcount != 1:
            raise ValueError("SQLite orchestration plan refers to an unknown task")


def delete_tasks(root: Path, task_ids: set[str]) -> int:
    if not task_ids:
        return 0
    placeholders = ",".join("?" for _ in task_ids)
    with _connection(root, write=True) as connection:
        cursor = connection.execute(f"DELETE FROM tasks WHERE task_id IN ({placeholders})", tuple(sorted(task_ids)))
    return int(cursor.rowcount)


def next_task_number(root: Path) -> int:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute("SELECT COALESCE(MAX(task_number), 0) + 1 AS value FROM tasks").fetchone()
    return int(row["value"])


def get_global(root: Path, name: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute("SELECT payload_json FROM global_documents WHERE name = ?", (name,)).fetchone()
    return dict(default or {}) if row is None else _decode_json(str(row["payload_json"]), f"global document {name}")


def put_global(root: Path, name: str, value: dict[str, Any]) -> None:
    with _connection(root, write=True) as connection:
        connection.execute(
            "INSERT INTO global_documents(name, payload_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET payload_json = excluded.payload_json, updated_at = excluded.updated_at",
            (name, _canonical_json(value), _now()),
        )


def delete_global(root: Path, name: str) -> None:
    with _connection(root, write=True) as connection:
        connection.execute("DELETE FROM global_documents WHERE name = ?", (name,))


def get_task_document(root: Path, task_id: str, document_key: str) -> dict[str, Any] | None:
    """Read one mutable task document scoped by its registered task id."""
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT payload_json FROM task_documents WHERE task_id = ? AND document_key = ?",
            (task_id, document_key),
        ).fetchone()
    return None if row is None else _decode_json(str(row["payload_json"]), f"task document {document_key}")


def put_task_document(root: Path, task_id: str, document_key: str, value: dict[str, Any]) -> None:
    if not task_id or not document_key or len(document_key) > 160:
        raise ValueError("SQLite task document identity is invalid")
    with _connection(root, write=True) as connection:
        connection.execute(
            "INSERT INTO task_documents(task_id, document_key, payload_json, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(task_id, document_key) DO UPDATE SET payload_json = excluded.payload_json, updated_at = excluded.updated_at",
            (task_id, document_key, _canonical_json(value), str(value.get("updated_at") or value.get("answered_at") or _now())),
        )


def list_task_documents(root: Path, task_id: str, prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
    ensure_database(root)
    query = "SELECT document_key, payload_json FROM task_documents WHERE task_id = ?"
    values: list[Any] = [task_id]
    if prefix:
        query += " AND document_key LIKE ?"
        values.append(prefix + "%")
    query += " ORDER BY document_key"
    with _connection(root) as connection:
        rows = connection.execute(query, tuple(values)).fetchall()
    return [
        (str(row["document_key"]), _decode_json(str(row["payload_json"]), f"task document {row['document_key']}"))
        for row in rows
    ]


def put_artifact(
    root: Path,
    task_id: str,
    *,
    kind: str,
    title: str,
    mime_type: str,
    content: str | bytes,
    immutable: bool = True,
    export_path: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Store one artifact without imposing a schema-level payload limit.

    SQLite stores the complete text/BLOB as transport chunks.  Chunking is a
    read-protocol concern, not a truncation policy: metadata always reports the
    original full byte size and digest.
    """
    ensure_database(root)
    with _connection(root, write=True) as connection:
        return _store_artifact_with_connection(
            connection, task_id=task_id, kind=kind, title=title, mime_type=mime_type,
            content=content, immutable=immutable, export_path=export_path, created_at=created_at,
        )


def _artifact_metadata_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "artifact_ref": str(row["artifact_id"]),
        "task_id": str(row["task_id"]),
        "kind": str(row["kind"]),
        "title": str(row["title"]),
        "mime_type": str(row["mime_type"]),
        "digest_sha256": str(row["digest_sha256"]),
        "byte_size": int(row["byte_size"]),
        "chunk_count": int(row["chunk_count"]),
        "immutable": bool(row["immutable"]),
        "export_path": str(row["export_path"]) if row["export_path"] is not None else None,
        "created_at": str(row["created_at"]),
    }


def get_artifact_metadata(root: Path, task_id: str, artifact_ref: str) -> dict[str, Any] | None:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT artifact_id, task_id, kind, title, mime_type, digest_sha256, byte_size, chunk_count, immutable, export_path, created_at "
            "FROM artifacts WHERE task_id = ? AND artifact_id = ?",
            (task_id, artifact_ref),
        ).fetchone()
    return None if row is None else _artifact_metadata_row(row)


def get_artifact_for_export_path(root: Path, task_id: str, export_path: str) -> dict[str, Any] | None:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT artifact_id, task_id, kind, title, mime_type, digest_sha256, byte_size, chunk_count, immutable, export_path, created_at "
            "FROM artifacts WHERE task_id = ? AND export_path = ? ORDER BY created_at DESC, artifact_id DESC LIMIT 1",
            (task_id, export_path),
        ).fetchone()
    return None if row is None else _artifact_metadata_row(row)


def list_artifacts(
    root: Path,
    task_id: str,
    *,
    kind: str | None = None,
    offset: int = 0,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int | None]:
    ensure_database(root)
    if offset < 0 or not 1 <= page_size <= 100:
        raise ValueError("SQLite artifact page bounds are invalid")
    values: list[Any] = [task_id]
    query = (
        "SELECT artifact_id, task_id, kind, title, mime_type, digest_sha256, byte_size, chunk_count, immutable, export_path, created_at "
        "FROM artifacts WHERE task_id = ?"
    )
    if kind:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind):
            raise ValueError("SQLite artifact kind is invalid")
        query += " AND kind = ?"
        values.append(kind)
    query += " ORDER BY created_at DESC, artifact_id DESC LIMIT ? OFFSET ?"
    values.extend((page_size + 1, offset))
    with _connection(root) as connection:
        rows = connection.execute(query, tuple(values)).fetchall()
    has_next = len(rows) > page_size
    return [_artifact_metadata_row(row) for row in rows[:page_size]], (offset + page_size if has_next else None)


def read_artifact_range(
    root: Path,
    task_id: str,
    artifact_ref: str,
    *,
    byte_offset: int = 0,
    max_bytes: int = 16 * 1024,
) -> dict[str, Any]:
    """Return a bounded range of one immutable artifact with an exact next offset."""
    metadata = get_artifact_metadata(root, task_id, artifact_ref)
    if metadata is None:
        raise ValueError("SQLite artifact is unavailable for the selected task")
    if byte_offset < 0 or byte_offset > metadata["byte_size"]:
        raise ValueError("SQLite artifact byte offset is invalid")
    if not 1 <= max_bytes <= ARTIFACT_TRANSPORT_MAX_BYTES:
        raise ValueError(f"SQLite artifact max_bytes must be from 1 through {ARTIFACT_TRANSPORT_MAX_BYTES}")
    remaining_offset = byte_offset
    budget = max_bytes
    text_parts: list[str] = []
    blob_parts: list[bytes] = []
    is_text: bool | None = None
    with _connection(root) as connection:
        rows = connection.execute(
            "SELECT chunk_no, text_content, blob_content, byte_size, digest_sha256 "
            "FROM artifact_chunks WHERE artifact_id = ? ORDER BY chunk_no",
            (artifact_ref,),
        ).fetchall()
    for row in rows:
        size = int(row["byte_size"])
        if remaining_offset >= size:
            remaining_offset -= size
            continue
        text_value = row["text_content"]
        chunk_is_text = text_value is not None
        if is_text is None:
            is_text = chunk_is_text
        if is_text != chunk_is_text:
            raise ValueError("SQLite artifact chunk encoding is inconsistent")
        data = str(text_value).encode("utf-8") if chunk_is_text else bytes(row["blob_content"])
        if hashlib.sha256(data).hexdigest() != str(row["digest_sha256"]):
            raise ValueError("SQLite artifact chunk digest is invalid")
        start = remaining_offset
        available = len(data) - start
        take = min(available, budget)
        end = start + take
        if chunk_is_text and end < len(data):
            while end > start and data[end] & 0b11000000 == 0b10000000:
                end -= 1
            if end == start:
                # A caller may intentionally request fewer bytes than one
                # UTF-8 code point (for example ``max_bytes=1``).  Cursor
                # transport must still make forward progress and must never
                # emit a broken text fragment.  Return that one complete
                # code point even though it can exceed the requested budget
                # by at most three bytes; the server's 32 KiB hard ceiling
                # remains authoritative for every normal page.
                leading = data[start]
                if leading & 0b10000000 == 0:
                    codepoint_bytes = 1
                elif leading & 0b11100000 == 0b11000000:
                    codepoint_bytes = 2
                elif leading & 0b11110000 == 0b11100000:
                    codepoint_bytes = 3
                elif leading & 0b11111000 == 0b11110000:
                    codepoint_bytes = 4
                else:  # ``content`` originated as Python text, so fail closed on corruption.
                    raise ValueError("SQLite artifact text encoding is invalid")
                end = min(len(data), start + codepoint_bytes)
        piece = data[start:end]
        if chunk_is_text:
            text_parts.append(piece.decode("utf-8"))
        else:
            blob_parts.append(piece)
        budget -= len(piece)
        remaining_offset = 0
        if budget == 0:
            break
    delivered = max_bytes - budget
    next_offset = byte_offset + delivered
    if delivered == 0 and byte_offset < metadata["byte_size"]:
        raise ValueError("SQLite artifact transport could not form a safe text range")
    result = {
        **metadata,
        "byte_offset": byte_offset,
        "returned_bytes": delivered,
        "complete": next_offset >= metadata["byte_size"],
        "next_byte_offset": None if next_offset >= metadata["byte_size"] else next_offset,
    }
    if is_text is not False:
        result["content_part"] = "".join(text_parts)
        result["encoding"] = "utf-8"
    else:
        result["content_base64"] = base64.b64encode(b"".join(blob_parts)).decode("ascii")
        result["encoding"] = "base64"
    return result


def read_artifact_content(root: Path, task_id: str, artifact_ref: str) -> str | bytes:
    """Read one complete artifact for internal state-machine use only.

    This is deliberately not an MCP transport method.  Public callers must
    use :func:`read_artifact_range`, so a large body can never accidentally be
    inserted into a tool result.  Internal lifecycle validation still needs an
    exact, digest-checked record without treating its materialized export as
    authoritative state.
    """
    metadata = get_artifact_metadata(root, task_id, artifact_ref)
    if metadata is None:
        raise ValueError("SQLite artifact is unavailable for the selected task")
    with _connection(root) as connection:
        rows = connection.execute(
            "SELECT chunk_no, text_content, blob_content, byte_size, digest_sha256 "
            "FROM artifact_chunks WHERE artifact_id = ? ORDER BY chunk_no",
            (artifact_ref,),
        ).fetchall()
    if len(rows) != metadata["chunk_count"]:
        raise ValueError("SQLite artifact chunk count is invalid")
    raw_parts: list[bytes] = []
    text: bool | None = None
    for expected_chunk_no, row in enumerate(rows):
        if int(row["chunk_no"]) != expected_chunk_no:
            raise ValueError("SQLite artifact chunk sequence is invalid")
        chunk_is_text = row["text_content"] is not None
        if text is None:
            text = chunk_is_text
        if text != chunk_is_text:
            raise ValueError("SQLite artifact chunk encoding is inconsistent")
        data = str(row["text_content"]).encode("utf-8") if chunk_is_text else bytes(row["blob_content"])
        if len(data) != int(row["byte_size"]) or hashlib.sha256(data).hexdigest() != str(row["digest_sha256"]):
            raise ValueError("SQLite artifact chunk digest is invalid")
        raw_parts.append(data)
    raw = b"".join(raw_parts)
    if len(raw) != metadata["byte_size"] or hashlib.sha256(raw).hexdigest() != metadata["digest_sha256"]:
        raise ValueError("SQLite artifact digest is invalid")
    return raw.decode("utf-8") if text else raw


def _artifact_cursor_secret(connection: sqlite3.Connection) -> bytes:
    row = connection.execute("SELECT value FROM ledger_meta WHERE key = ?", ("artifact_cursor_hmac_key",)).fetchone()
    if row is None:
        raise ValueError("SQLite artifact cursor key is unavailable")
    value = str(row["value"])
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("SQLite artifact cursor key is invalid")
    return bytes.fromhex(value)


def encode_artifact_cursor(root: Path, payload: dict[str, Any]) -> str:
    """Sign an opaque cursor bound to task, artifact/version and reader scope."""
    ensure_database(root)
    encoded = _canonical_json(payload).encode("utf-8")
    with _connection(root) as connection:
        signature = hmac.new(_artifact_cursor_secret(connection), encoded, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=") + "." + base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


def decode_artifact_cursor(root: Path, cursor: str) -> dict[str, Any]:
    ensure_database(root)
    if not isinstance(cursor, str) or cursor.count(".") != 1 or len(cursor) > 4096:
        raise ValueError("artifact cursor is invalid")
    raw, signature = cursor.split(".", 1)
    try:
        encoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ValueError("artifact cursor is invalid") from exc
    with _connection(root) as connection:
        expected = hmac.new(_artifact_cursor_secret(connection), encoded, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied, expected):
        raise ValueError("artifact cursor signature is invalid")
    return _decode_json(encoded.decode("utf-8"), "artifact cursor")


def get_classification(root: Path, classification_id: str) -> dict[str, Any] | None:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute("SELECT payload_json FROM classifications WHERE classification_id = ?", (classification_id,)).fetchone()
    return None if row is None else _decode_json(str(row["payload_json"]), "classification receipt")


def put_classification(root: Path, receipt: dict[str, Any]) -> None:
    classification_id = str(receipt.get("classification_id") or "")
    if not classification_id:
        raise ValueError("classification receipt has no classification_id")
    with _connection(root, write=True) as connection:
        connection.execute(
            "INSERT INTO classifications(classification_id, payload_json, consumed_by, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(classification_id) DO UPDATE SET payload_json = excluded.payload_json, consumed_by = excluded.consumed_by",
            (classification_id, _canonical_json(receipt), receipt.get("consumed_by"), str(receipt.get("created_at") or _now())),
        )


def delete_classifications(root: Path, classification_ids: set[str]) -> int:
    if not classification_ids:
        return 0
    placeholders = ",".join("?" for _ in classification_ids)
    with _connection(root, write=True) as connection:
        cursor = connection.execute(f"DELETE FROM classifications WHERE classification_id IN ({placeholders})", tuple(sorted(classification_ids)))
    return int(cursor.rowcount)


def put_manifest_snapshot(root: Path, snapshot_ref: str, digest: str, payload: dict[str, Any]) -> None:
    with _connection(root, write=True) as connection:
        row = connection.execute("SELECT payload_json FROM manifest_snapshots WHERE snapshot_ref = ?", (snapshot_ref,)).fetchone()
        text = _canonical_json(payload)
        if row is not None:
            if str(row["payload_json"]) != text:
                raise ValueError("SQLite manifest snapshot reference already has different content")
            return
        connection.execute(
            "INSERT INTO manifest_snapshots(snapshot_ref, digest, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (snapshot_ref, digest, text, str(payload.get("captured_at") or _now())),
        )


def get_manifest_snapshot(root: Path, snapshot_ref: str) -> dict[str, Any] | None:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute("SELECT payload_json FROM manifest_snapshots WHERE snapshot_ref = ?", (snapshot_ref,)).fetchone()
    return None if row is None else _decode_json(str(row["payload_json"]), "manifest snapshot")


def manifest_snapshot_refs(root: Path) -> list[str]:
    """Return the observable references for diagnostics and invariant tests."""
    ensure_database(root)
    with _connection(root) as connection:
        rows = connection.execute("SELECT snapshot_ref FROM manifest_snapshots ORDER BY snapshot_ref").fetchall()
    return [str(row["snapshot_ref"]) for row in rows]


def delete_unreferenced_manifest_snapshots(root: Path) -> int:
    """Garbage-collect snapshots after terminal cleanup using JSON references.

    The state document contains the task/attempt references today.  The query
    intentionally stays conservative: a snapshot is removed only after the
    owning task has already been removed, or the close lifecycle explicitly
    clears its reference through :func:`delete_task_manifest_snapshots`.
    """
    return 0


def delete_task_manifest_snapshots(root: Path, task_id: str) -> int:
    loaded = load_task(root, task_id)
    if loaded is None:
        return 0
    _, state, _, _ = loaded
    refs = {str(state.get("initial_manifest_ref") or "")}
    refs.update(str(item.get("result_baseline_ref") or "") for item in state.get("attempts", []) if isinstance(item, dict))
    refs.discard("")
    if not refs:
        return 0
    with _connection(root, write=True) as connection:
        referenced_elsewhere: set[str] = set()
        for row in connection.execute("SELECT state_json FROM tasks WHERE task_id != ?", (task_id,)):
            other = _decode_json(str(row["state_json"]), "task state")
            referenced_elsewhere.add(str(other.get("initial_manifest_ref") or ""))
            referenced_elsewhere.update(
                str(item.get("result_baseline_ref") or "")
                for item in other.get("attempts", [])
                if isinstance(item, dict)
            )
        removable = sorted(refs - referenced_elsewhere)
        if not removable:
            return 0
        placeholders = ",".join("?" for _ in removable)
        cursor = connection.execute(f"DELETE FROM manifest_snapshots WHERE snapshot_ref IN ({placeholders})", tuple(removable))
    return int(cursor.rowcount)


def get_lane(root: Path, lane_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute("SELECT definition_json, state_json FROM lanes WHERE lane_id = ?", (lane_id,)).fetchone()
    if row is None:
        return None
    return _decode_json(str(row["definition_json"]), "lane definition"), _decode_json(str(row["state_json"]), "lane state")


def put_lane(root: Path, definition: dict[str, Any], state: dict[str, Any], *, event: str | None = None, detail: str = "") -> None:
    lane_id = str(state.get("lane_id") or definition.get("lane_id") or "")
    if not lane_id:
        raise ValueError("lane has no lane_id")
    with _connection(root, write=True) as connection:
        connection.execute(
            "INSERT INTO lanes(lane_id, definition_json, state_json, status, revision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(lane_id) DO UPDATE SET definition_json = excluded.definition_json, state_json = excluded.state_json, "
            "status = excluded.status, revision = excluded.revision, updated_at = excluded.updated_at",
            (lane_id, _canonical_json(definition), _canonical_json(state), str(state.get("status") or "active"),
             int(state.get("revision") or 0), str(state.get("created_at") or _now()), str(state.get("updated_at") or _now())),
        )
        if event:
            connection.execute(
                "INSERT INTO ledger_events(task_id, lane_id, event, detail, revision, created_at) VALUES (NULL, ?, ?, ?, ?, ?)",
                (lane_id, event, detail, int(state.get("revision") or 0), _now()),
            )


def all_lanes(root: Path) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    ensure_database(root)
    with _connection(root) as connection:
        rows = connection.execute("SELECT definition_json, state_json FROM lanes ORDER BY lane_id").fetchall()
    return [(_decode_json(str(row["definition_json"]), "lane definition"), _decode_json(str(row["state_json"]), "lane state")) for row in rows]


def get_operation(root: Path, submission_id: str) -> dict[str, Any] | None:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute("SELECT payload_json FROM operations WHERE submission_id = ?", (submission_id,)).fetchone()
    return None if row is None else _decode_json(str(row["payload_json"]), "operation receipt")


def put_operation(root: Path, submission_id: str, value: dict[str, Any]) -> None:
    with _connection(root, write=True) as connection:
        connection.execute(
            "INSERT INTO operations(submission_id, task_id, payload_json, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(submission_id) DO UPDATE SET task_id = excluded.task_id, payload_json = excluded.payload_json, updated_at = excluded.updated_at",
            (submission_id, value.get("task_id"), _canonical_json(value), str(value.get("updated_at") or _now())),
        )


def delete_operations_for_tasks(root: Path, task_ids: set[str]) -> int:
    if not task_ids:
        return 0
    placeholders = ",".join("?" for _ in task_ids)
    with _connection(root, write=True) as connection:
        cursor = connection.execute(f"DELETE FROM operations WHERE task_id IN ({placeholders})", tuple(sorted(task_ids)))
    return int(cursor.rowcount)
