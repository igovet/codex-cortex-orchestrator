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
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the process-local guard.
    fcntl = None


DATABASE_NAME = "cortex.db"
DATABASE_SCHEMA_VERSION = 15
ARTIFACT_STORAGE_CHUNK_BYTES = 32 * 1024
ARTIFACT_TRANSPORT_MAX_BYTES = 32 * 1024
_LOCAL = threading.local()
_MIGRATION_GUARD = threading.Lock()
_MIGRATION_LOCKS: dict[str, threading.RLock] = {}
_DATABASE_READINESS_GUARD = threading.RLock()
_DATABASE_READINESS_CACHE_LIMIT = 128
_HOOK_METRICS_GUARD = threading.Lock()
_HOOK_METRICS: dict[str, int] = {
    "hook_snapshot_miss": 0,
    "telemetry_failure": 0,
}


def hook_metrics_snapshot() -> dict[str, int]:
    """Return content-free process-local hook counters."""
    with _HOOK_METRICS_GUARD:
        return dict(_HOOK_METRICS)


def _hook_metric(name: str) -> None:
    if name not in _HOOK_METRICS:
        return
    with _HOOK_METRICS_GUARD:
        _HOOK_METRICS[name] += 1


@dataclass(frozen=True)
class _DatabaseFileIdentity:
    """Filesystem facts that make one ready-ledger cache entry reusable.

    The cache never trusts this identity alone: every warm open also obtains
    a short read-only SQLite snapshot of the migration authority below.  The
    identity makes an atomic database replacement a cache miss even when the
    replacement happens to carry the same migration metadata.
    """

    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _DatabaseReadiness:
    """Validated immutable-schema facts for one process-local warm open."""

    identity: _DatabaseFileIdentity
    user_version: int
    schema_version: int
    history_fingerprint: str
    plan_fingerprint: str


# Keep this deliberately bounded: a long-lived host can inspect many project
# ledgers, but readiness is an optimization and must not become unbounded
# per-process state.  Entries are revalidated on every use and therefore are
# never an authority source.
_DATABASE_READINESS: OrderedDict[str, _DatabaseReadiness] = OrderedDict()


def _now() -> str:
    # Keep the database package independent from Cortex's response/runtime
    # facade.  RFC 3339 UTC text is sufficient for migration bookkeeping.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def governance_lifecycle_binding(
    *,
    record_ref: str,
    sequence: int,
    previous_binding: str | None,
    status: str,
    approval_basis_json: str | None,
) -> str:
    """Return the stable integrity binding for one governance lifecycle row.

    Governance records retain a small mutable projection (their current
    lifecycle status and approval basis) so active-snapshot queries remain
    cheap.  The canonical authority is an append-only lifecycle chain.  Keep
    this digest helper in the database package so both the v11 backfill and
    the governance service use *exactly* the same serialization.
    """
    return hashlib.sha256(_canonical_json({
        "schema": "cortex/governance-lifecycle/v1",
        "record_ref": str(record_ref),
        "sequence": int(sequence),
        "previous_binding": str(previous_binding or "") or None,
        "status": str(status),
        "approval_basis_json": approval_basis_json,
    }).encode("utf-8")).hexdigest()


def _governance_lifecycle_key_path(root: Path) -> Path:
    """Return the host-private sidecar key path for one project ledger.

    The ledger's project directory is movable (prior-release project-local state is
    migrated into ``<host>/projects/<opaque-id>``), while the host control
    store is not part of the project checkout.  Keep the HMAC key beside that
    control store rather than in the SQLite ledger it authenticates.  The
    opaque ledger directory name is stable for the normal host-private layout;
    a digest fallback keeps source-mode/prior-release fixtures collision-free.
    """
    resolved = root.resolve()
    host_root = resolved.parent.parent if resolved.parent.name == "projects" else resolved.parent
    key_id = resolved.name if re.fullmatch(r"p-[0-9a-f]{64}", resolved.name) else hashlib.sha256(
        str(resolved).encode("utf-8")
    ).hexdigest()
    return host_root / "governance-lifecycle-keys" / f"{key_id}.key"


def _governance_lifecycle_hmac_key(root: Path, *, create: bool) -> bytes:
    """Load a non-ledger key, creating it only during an authorized upgrade.

    The key is intentionally never represented in SQLite, lifecycle rows,
    responses, logs, or fixtures.  Losing it makes lifecycle authority
    unverifiable and therefore fails closed instead of silently resealing a
    tampered ledger with a replacement key.
    """
    path = _governance_lifecycle_key_path(root)
    parent = path.parent
    if not parent.exists():
        if not create:
            raise ValueError("Cortex governance lifecycle host key is unavailable")
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_info = parent.lstat()
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_mode & 0o077:
        raise ValueError("Cortex governance lifecycle host key directory is unsafe")
    if os.name != "nt" and hasattr(os, "getuid") and int(parent_info.st_uid) != int(os.getuid()):
        raise ValueError("Cortex governance lifecycle host key directory must be owned by the current user")
    try:
        os.chmod(parent, 0o700)
    except OSError:
        raise
    if not path.exists():
        if not create:
            raise ValueError("Cortex governance lifecycle host key is unavailable")
        raw = secrets.token_bytes(32)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            return raw
    _assert_private_regular(path, "Cortex governance lifecycle host key")
    path_info = path.lstat()
    if os.name != "nt" and hasattr(os, "getuid") and int(path_info.st_uid) != int(os.getuid()):
        raise ValueError("Cortex governance lifecycle host key must be owned by the current user")
    raw = path.read_bytes()
    if len(raw) != 32:
        raise ValueError("Cortex governance lifecycle host key is invalid")
    return raw


def governance_lifecycle_envelope_hmac(
    root: Path,
    *,
    lifecycle_ref: str,
    record_ref: str,
    lifecycle_sequence: int,
    previous_binding: str | None,
    status: str,
    approval_basis_json: str | None,
    binding: str,
    action: str,
    actor_role: str,
    created_at: str,
    create_key: bool = False,
) -> str:
    """Authenticate the complete immutable lifecycle event envelope.

    v11's public SHA-256 chain remains readable for historical verification.
    v12 seals every field omitted by that chain with an HMAC
    whose key lives outside the project ledger.
    """
    payload = _canonical_json({
        "schema": "cortex/governance-lifecycle-envelope/v2",
        "lifecycle_ref": str(lifecycle_ref),
        "record_ref": str(record_ref),
        "lifecycle_sequence": int(lifecycle_sequence),
        "previous_binding": str(previous_binding or "") or None,
        "status": str(status),
        "approval_basis_json": approval_basis_json,
        "binding": str(binding),
        "action": str(action),
        "actor_role": str(actor_role),
        "created_at": str(created_at),
    }).encode("utf-8")
    return hmac.new(
        _governance_lifecycle_hmac_key(root, create=create_key), payload, hashlib.sha256
    ).hexdigest()


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


def _database_file_identity(root: Path) -> _DatabaseFileIdentity | None:
    """Return safe non-content identity facts for the current database file.

    A missing database is a normal first-boot condition.  An existing file
    still goes through the same private-regular-file check as the write path;
    a warm cache must never turn an unsafe replacement into a trusted read.
    """
    path = database_path(root)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("Cortex database must be a regular non-symlink file")
    if info.st_mode & 0o077:
        raise ValueError("Cortex database has unsafe permissions")
    return _DatabaseFileIdentity(
        device=int(info.st_dev),
        inode=int(info.st_ino),
        size=int(info.st_size),
        modified_ns=int(info.st_mtime_ns),
        changed_ns=int(info.st_ctime_ns),
    )


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


@contextlib.contextmanager
def hook_snapshot(root: Path, *, timeout_ms: int = 100) -> Iterator[sqlite3.Connection | None]:
    """Open one bounded, read-only snapshot for a short-lived lifecycle hook.

    Hooks are observational and must never bootstrap a ledger, validate or
    apply migrations, acquire the filesystem state lock, or wait behind a
    result commit.  The database must already exist and advertise the current
    schema; any missing, busy, unreadable, or incompatible database is a
    fail-open ``None`` snapshot.  Callers may perform all of their reads while
    this single deferred transaction is open, then the connection is closed.
    """
    try:
        timeout = max(0, min(int(timeout_ms), 100))
    except (TypeError, ValueError):
        timeout = 100
    path = database_path(root)
    connection: sqlite3.Connection | None = None
    try:
        identity = _database_file_identity(root)
        if identity is None:
            _hook_metric("hook_snapshot_miss")
            yield None
            return
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=timeout / 1000.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute(f"PRAGMA busy_timeout = {timeout}")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != DATABASE_SCHEMA_VERSION:
            _hook_metric("hook_snapshot_miss")
            connection.close()
            connection = None
            yield None
            return
        # A single read transaction gives a consistent view across task,
        # activation, and deduplication queries without any write lock.
        connection.execute("BEGIN")
    except (OSError, sqlite3.Error, ValueError):
        _hook_metric("hook_snapshot_miss")
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        yield None
        return
    try:
        yield connection
    finally:
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            connection.close()


def hook_snapshot_global(connection: sqlite3.Connection, name: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read one global value from an already-open :func:`hook_snapshot`."""
    row = connection.execute("SELECT payload_json FROM global_documents WHERE name = ?", (name,)).fetchone()
    if row is None:
        return dict(default or {})
    return _decode_json(str(row["payload_json"]), f"global document {name}")


def hook_snapshot_operation_registry(
    connection: sqlite3.Connection,
    default: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the durable operation registry from an existing hook snapshot."""
    return hook_snapshot_global(connection, "operation_registry", default)


def hook_snapshot_task_ref(connection: sqlite3.Connection, task_id: str) -> str | None:
    """Read a task's opaque public reference without opening another snapshot."""
    registry = hook_snapshot_operation_registry(connection)
    tasks = registry.get("tasks")
    record = tasks.get(task_id) if isinstance(tasks, dict) else None
    start = record.get("start") if isinstance(record, dict) else None
    value = start.get("task_ref") if isinstance(start, dict) else None
    return str(value) if value is not None and str(value) else None


def hook_snapshot_task_index(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = connection.execute("SELECT task_id, task_number, artifact_dir FROM tasks ORDER BY task_number").fetchall()
    return {
        str(row["task_id"]): {
            "number": int(row["task_number"]),
            "directory": Path(str(row["artifact_dir"])).name,
            "artifact_dir": str(row["artifact_dir"]),
        }
        for row in rows
    }


def hook_snapshot_load_task(connection: sqlite3.Connection, task_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, str] | None:
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


def hook_snapshot_task_context(
    connection: sqlite3.Connection,
    task_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, str] | None:
    """Read one task's definition, state, plan, and registered artifact path."""
    return hook_snapshot_load_task(connection, task_id)


def hook_snapshot_artifact_directory(connection: sqlite3.Connection, task_id: str) -> str | None:
    """Read the registered (relative) artifact directory for one task."""
    row = connection.execute("SELECT artifact_dir FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return None if row is None else str(row["artifact_dir"])


def hook_snapshot_pending_subagent(
    connection: sqlite3.Connection,
    agent_type: str,
    model: str = "",
) -> list[str]:
    """Return task ids with exactly matching, still-pending host dispatches.

    This intentionally returns all matches; ambiguity is resolved by the hook
    caller.  Keeping the query on the supplied connection means the task scan
    and every state read share the same bounded snapshot.
    """
    matches: list[str] = []
    for row in connection.execute("SELECT task_id, state_json FROM tasks ORDER BY task_number").fetchall():
        try:
            state = _decode_json(str(row["state_json"]), "task state")
        except ValueError:
            continue
        if state.get("status") not in {"active", "blocked"}:
            continue
        pending = [
            attempt for attempt in state.get("attempts", [])
            if isinstance(attempt, dict)
            and not attempt.get("invalidated")
            and attempt.get("status") == "awaiting_host_spawn"
        ]
        if agent_type == "default":
            candidates = [
                attempt for attempt in pending
                if model and str(attempt.get("expected_model") or attempt.get("selected_model") or "") == model
            ]
        else:
            candidates = [
                attempt for attempt in pending
                if str((attempt.get("spawn_request") or {}).get("task_name") or "") == agent_type
            ]
        if len(candidates) == 1:
            matches.append(str(row["task_id"]))
    return matches


def hook_snapshot_find_successful_tool_observation(
    connection: sqlite3.Connection,
    task_id: str,
    attempt_id: str,
    context_epoch: int,
    fingerprint: str,
    workspace_generation: str,
) -> bool:
    row = connection.execute(
        "SELECT 1 FROM tool_observations WHERE task_id=? AND attempt_id=? AND context_epoch=? "
        "AND fingerprint=? AND workspace_generation=? AND coverage='full' AND status='success'",
        (task_id, attempt_id, context_epoch, fingerprint, workspace_generation),
    ).fetchone()
    return row is not None


def hook_snapshot_tool_context_epoch(connection: sqlite3.Connection, task_id: str) -> int:
    """Read the latest durable tool-context epoch from a hook snapshot."""
    rows = connection.execute(
        "SELECT metadata_json FROM orchestration_trace WHERE task_id=? AND event='tool_context_epoch' ORDER BY trace_id DESC",
        (task_id,),
    ).fetchall()
    for row in rows:
        try:
            value = json.loads(str(row["metadata_json"])).get("epoch")
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def hook_tool_context_epoch(root: Path, task_id: str, *, bump: bool = False) -> int:
    """Read or roll a hook context epoch without migration/state-lock work."""
    try:
        if not bump:
            with hook_snapshot(root) as snapshot:
                return int(snapshot and hook_snapshot_tool_context_epoch(snapshot, task_id) or 0)
        with _hook_write_connection(root) as connection:
            epoch = hook_snapshot_tool_context_epoch(connection, task_id) + 1
            connection.execute(
                "INSERT INTO orchestration_trace(task_id,attempt_id,event,occurred_at,metadata_json) VALUES(?,?,?,?,?)",
                (task_id, None, "tool_context_epoch", _now(), _canonical_json({"epoch": epoch})),
            )
            return epoch
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _hook_metric("telemetry_failure" if bump else "hook_snapshot_miss")
        return 0


@contextlib.contextmanager
def connection(root: Path, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    """Public repository boundary for small domain modules sharing the UoW.

    Callers should prefer dedicated ledger functions where one exists.  The
    governance module owns a compact set of transactional invariants that
    span records, artifacts and task links, so it receives this deliberately
    narrow, re-entrant connection boundary instead of importing private store
    internals or opening independent SQLite transactions.
    """
    with _connection(root, write=write) as active:
        yield active


def in_transaction(root: Path) -> bool:
    """Return whether ``root`` is already inside the public ledger UoW."""
    return _root_key(root) in _active_connections()


def content_addressed_blob_ref(digest: str, mime_type: str, byte_size: int) -> str:
    """Expose the stable v7 artifact identity without exposing implementation."""
    return _blob_ref(digest, mime_type, byte_size)


def text_chunk_boundaries(raw: bytes) -> list[bytes]:
    """Expose canonical UTF-8 chunking for immutable JSON artifacts."""
    return _text_chunk_boundaries(raw)


@dataclass(frozen=True)
class _Migration:
    version: int
    name: str
    statements: tuple[str, ...]


def _normalize_sql(statement: str) -> str:
    return " ".join(statement.split())


def _migration_checksum(migration: _Migration | str, statements: tuple[str, ...] = ()) -> str:
    """Hash the migration identity and ordered normalized content.

    The string form is retained solely to recognize databases written by the
    pre-atomic implementation, whose checksum covered only the migration
    name.
    """
    if isinstance(migration, str):
        return hashlib.sha256(migration.encode("utf-8")).hexdigest()
    payload = {
        "algorithm": "sha256",
        "version": migration.version,
        "name": migration.name,
        "statements": [_normalize_sql(item) for item in migration.statements],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _execute_migration_statements(connection: sqlite3.Connection, statements: tuple[str, ...]) -> None:
    """Execute exactly the immutable, checksummed statements in order.

    ``<runtime-token>`` is a stable source placeholder.  Its generated value
    is deliberately substituted only at execution time, so it cannot change
    the migration checksum while still allowing each installation its own
    cursor signing key.
    """
    for statement in statements:
        if not statement.strip():
            continue
        if "'<runtime-token>'" in statement:
            statement = statement.replace("'<runtime-token>'", repr(secrets.token_hex(32)))
        connection.execute(statement)


def _record_migration(connection: sqlite3.Connection, migration: _Migration) -> None:
    connection.execute(
        "INSERT INTO schema_migrations(version, name, applied_at, checksum) VALUES (?, ?, ?, ?)",
        (migration.version, migration.name, _now(), _migration_checksum(migration)),
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


def _artifact_ref(task_id: str, kind: str, title: str, digest: str) -> str:
    """Return an id for a *logical* artifact, not for its bytes alone.

    The v2 catalog used ``task/kind/digest`` as its identity.  That made two
    equally-sized reports with identical contents indistinguishable, even
    where they intentionally had different titles or export destinations.
    New ids include the logical title.  Existing v2 ids are retained verbatim
    by the v7 backfill.
    """
    return "artifact-" + hashlib.sha256(
        f"{task_id}\0{kind}\0{title}\0{digest}".encode("utf-8")
    ).hexdigest()[:32]


def _blob_ref(digest: str, mime_type: str, byte_size: int) -> str:
    """Return the content-addressed identifier for one canonical blob."""
    # Keep this exactly representable by the append-only SQLite migration,
    # which uses ``lower(hex(mime_type))`` to backfill pre-v7 rows without a
    # Python callback.  The actual identity remains the unique
    # digest/mime/size tuple, while this is its deterministic key.
    return f"blob-{digest}-{mime_type.encode('utf-8').hex()}-{byte_size}"


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
    """Persist one logical artifact and its shared canonical blob atomically."""
    _validate_artifact_identity(task_id, kind, title, export_path)
    if not mime_type or len(mime_type) > 160:
        raise ValueError("SQLite artifact MIME type is invalid")
    is_text = isinstance(content, str)
    raw = content.encode("utf-8") if is_text else bytes(content)
    digest = hashlib.sha256(raw).hexdigest()
    artifact_id = _artifact_ref(task_id, kind, title, digest)
    blob_id = _blob_ref(digest, mime_type, len(raw))
    existing = connection.execute(
        "SELECT artifact_id, task_id, kind, title, mime_type, digest_sha256, byte_size, chunk_count, "
        "immutable, export_path, created_at FROM logical_artifacts "
        "WHERE task_id = ? AND kind = ? AND title = ? AND digest_sha256 = ?",
        (task_id, kind, title, digest),
    ).fetchone()
    if existing is not None:
        if (
            str(existing["mime_type"]) != mime_type
            or int(existing["byte_size"]) != len(raw)
            or bool(existing["immutable"]) != bool(immutable)
        ):
            raise ValueError("SQLite immutable artifact identity already has conflicting metadata")
        if export_path:
            _register_artifact_export_with_connection(connection, task_id, str(existing["artifact_id"]), export_path)
        return _artifact_metadata_row(existing)
    chunks = _text_chunk_boundaries(raw) if is_text else [raw[offset:offset + ARTIFACT_STORAGE_CHUNK_BYTES] for offset in range(0, len(raw), ARTIFACT_STORAGE_CHUNK_BYTES)] or [b""]
    created = created_at or _now()
    connection.execute(
        """INSERT INTO artifact_blobs(blob_id, digest_sha256, mime_type, byte_size, chunk_count, encoding, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(digest_sha256, mime_type, byte_size) DO NOTHING""",
        (blob_id, digest, mime_type, len(raw), len(chunks), "utf-8" if is_text else "binary", created),
    )
    blob = connection.execute(
        "SELECT blob_id, chunk_count, encoding FROM artifact_blobs WHERE digest_sha256 = ? AND mime_type = ? AND byte_size = ?",
        (digest, mime_type, len(raw)),
    ).fetchone()
    if blob is None or str(blob["blob_id"]) != blob_id:
        raise ValueError("SQLite canonical artifact blob identity is inconsistent")
    if int(blob["chunk_count"]) != len(chunks) or str(blob["encoding"]) != ("utf-8" if is_text else "binary"):
        raise ValueError("SQLite canonical artifact blob metadata is inconsistent")
    existing_chunks = connection.execute(
        "SELECT COUNT(*) FROM artifact_blob_chunks WHERE blob_id = ?", (blob_id,)
    ).fetchone()[0]
    if existing_chunks == 0:
        for chunk_no, chunk in enumerate(chunks):
            text_content = chunk.decode("utf-8") if is_text else None
            blob_content = None if is_text else sqlite3.Binary(chunk)
            connection.execute(
                """INSERT INTO artifact_blob_chunks(blob_id, chunk_no, text_content, blob_content, byte_size, digest_sha256)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (blob_id, chunk_no, text_content, blob_content, len(chunk), hashlib.sha256(chunk).hexdigest()),
            )
    elif int(existing_chunks) != len(chunks):
        raise ValueError("SQLite canonical artifact blob chunks are inconsistent")
    connection.execute(
        """INSERT INTO logical_artifacts(artifact_id, task_id, kind, title, mime_type, digest_sha256, byte_size, chunk_count, immutable, blob_id, export_path, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (artifact_id, task_id, kind, title, mime_type, digest, len(raw), len(chunks), int(bool(immutable)), blob_id, export_path, created),
    )
    if export_path:
        _register_artifact_export_with_connection(connection, task_id, artifact_id, export_path, created_at=created)
    return {
        "artifact_ref": artifact_id, "task_id": task_id, "kind": kind, "title": title,
        "mime_type": mime_type, "digest_sha256": digest, "byte_size": len(raw),
        "chunk_count": len(chunks), "immutable": bool(immutable), "export_path": export_path,
        "created_at": created,
    }


_BASE_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ledger_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS tasks(task_id TEXT PRIMARY KEY, task_number INTEGER NOT NULL UNIQUE, artifact_dir TEXT NOT NULL UNIQUE, definition_json TEXT NOT NULL, state_json TEXT NOT NULL, plan_json TEXT, status TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS tasks_status_updated_idx ON tasks(status, updated_at)",
    "CREATE TABLE IF NOT EXISTS lanes(lane_id TEXT PRIMARY KEY, definition_json TEXT NOT NULL, state_json TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS classifications(classification_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, consumed_by TEXT REFERENCES tasks(task_id) ON DELETE SET NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS manifest_snapshots(snapshot_ref TEXT PRIMARY KEY, digest TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS global_documents(name TEXT PRIMARY KEY, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS operations(submission_id TEXT PRIMARY KEY, task_id TEXT, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS operations_task_idx ON operations(task_id)",
    "CREATE TABLE IF NOT EXISTS ledger_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT REFERENCES tasks(task_id) ON DELETE CASCADE, lane_id TEXT REFERENCES lanes(lane_id) ON DELETE CASCADE, event TEXT NOT NULL, detail TEXT NOT NULL, revision INTEGER, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS task_documents(task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, document_key TEXT NOT NULL, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(task_id, document_key))",
    "CREATE INDEX IF NOT EXISTS task_documents_updated_idx ON task_documents(task_id, updated_at)",
)
_ARTIFACT_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS artifacts(artifact_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, kind TEXT NOT NULL, title TEXT NOT NULL, mime_type TEXT NOT NULL, digest_sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL CHECK(byte_size >= 0), chunk_count INTEGER NOT NULL CHECK(chunk_count >= 1), immutable INTEGER NOT NULL CHECK(immutable IN (0, 1)), export_path TEXT, created_at TEXT NOT NULL, UNIQUE(task_id, kind, digest_sha256))",
    "CREATE TABLE IF NOT EXISTS artifact_chunks(artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE, chunk_no INTEGER NOT NULL CHECK(chunk_no >= 0), text_content TEXT, blob_content BLOB, byte_size INTEGER NOT NULL CHECK(byte_size >= 0), digest_sha256 TEXT NOT NULL, PRIMARY KEY(artifact_id, chunk_no), CHECK((text_content IS NOT NULL AND blob_content IS NULL) OR (text_content IS NULL AND blob_content IS NOT NULL)))",
    "CREATE INDEX IF NOT EXISTS tasks_created_at_idx ON tasks(created_at)",
    "CREATE INDEX IF NOT EXISTS artifacts_task_kind_created_idx ON artifacts(task_id, kind, created_at DESC, artifact_id)",
    "CREATE INDEX IF NOT EXISTS artifacts_task_created_idx ON artifacts(task_id, created_at DESC, artifact_id)",
    "CREATE INDEX IF NOT EXISTS artifacts_digest_idx ON artifacts(digest_sha256)",
    "INSERT OR IGNORE INTO ledger_meta(key, value) VALUES ('artifact_cursor_hmac_key', '<runtime-token>')",
)
_CLOSURE_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS task_findings(task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, fingerprint TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL, blocking INTEGER NOT NULL CHECK(blocking IN (0, 1)), summary TEXT NOT NULL, details TEXT, next_action_json TEXT, source_evidence_json TEXT NOT NULL, first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(task_id, fingerprint))",
    "CREATE INDEX IF NOT EXISTS task_findings_task_status_idx ON task_findings(task_id, status, severity, blocking)",
)
_FINDING_METADATA_SCHEMA_STATEMENTS = (
    "ALTER TABLE task_findings ADD COLUMN waiver_reason TEXT",
    "ALTER TABLE task_findings ADD COLUMN waived_by TEXT",
    "ALTER TABLE task_findings ADD COLUMN waived_at TEXT",
    "ALTER TABLE task_findings ADD COLUMN resolved_at TEXT",
)

# These definitions are intentionally append-only.  Projection state is an
# outbox: it is committed with canonical state, and filesystem materializers
# acknowledge it later in a short, independent transaction.
_PROJECTION_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS projection_jobs(projection_key TEXT PRIMARY KEY, task_id TEXT, artifact_id TEXT, projection_type TEXT NOT NULL, export_path TEXT, required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0, 1)), status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'materializing', 'ready', 'failed', 'deleting', 'deleted')), attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0), expected_digest TEXT NOT NULL, materialized_digest TEXT, last_error TEXT, lease_owner TEXT, lease_expires_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, materialized_at TEXT)",
    "CREATE INDEX IF NOT EXISTS projection_jobs_status_idx ON projection_jobs(status, lease_expires_at, updated_at)",
    "CREATE INDEX IF NOT EXISTS projection_jobs_task_idx ON projection_jobs(task_id, projection_type)",
)
_PRUNE_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS prune_tombstones(tombstone_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, artifact_dir TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned', 'claimed', 'filesystem_removed', 'finalized', 'failed')), lease_owner TEXT, lease_expires_at TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, filesystem_removed_at TEXT, finalized_at TEXT)",
    "CREATE UNIQUE INDEX IF NOT EXISTS prune_tombstones_active_task_idx ON prune_tombstones(task_id) WHERE status != 'finalized'",
    "CREATE INDEX IF NOT EXISTS prune_tombstones_status_idx ON prune_tombstones(status, lease_expires_at, updated_at)",
)

# v7 deliberately leaves the v2 ``artifacts`` and ``artifact_chunks`` tables
# intact.  They are migration evidence and preserve every historic artifact id
# while this migration backfills an explicitly split logical/blob model.  New
# writes and all reads use the tables below; a later, separately-versioned
# retention migration may reclaim prior-release duplicate chunks only after it has a
# safe export/projection retention policy.
_ARTIFACT_NORMALIZATION_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS artifact_blobs(blob_id TEXT PRIMARY KEY, digest_sha256 TEXT NOT NULL, mime_type TEXT NOT NULL, byte_size INTEGER NOT NULL CHECK(byte_size >= 0), chunk_count INTEGER NOT NULL CHECK(chunk_count >= 1), encoding TEXT NOT NULL CHECK(encoding IN ('utf-8', 'binary')), created_at TEXT NOT NULL, UNIQUE(digest_sha256, mime_type, byte_size))",
    "CREATE TABLE IF NOT EXISTS artifact_blob_chunks(blob_id TEXT NOT NULL REFERENCES artifact_blobs(blob_id) ON DELETE CASCADE, chunk_no INTEGER NOT NULL CHECK(chunk_no >= 0), text_content TEXT, blob_content BLOB, byte_size INTEGER NOT NULL CHECK(byte_size >= 0), digest_sha256 TEXT NOT NULL, PRIMARY KEY(blob_id, chunk_no), CHECK((text_content IS NOT NULL AND blob_content IS NULL) OR (text_content IS NULL AND blob_content IS NOT NULL)))",
    "CREATE TABLE IF NOT EXISTS logical_artifacts(artifact_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, kind TEXT NOT NULL, title TEXT NOT NULL, mime_type TEXT NOT NULL, digest_sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL CHECK(byte_size >= 0), chunk_count INTEGER NOT NULL CHECK(chunk_count >= 1), immutable INTEGER NOT NULL CHECK(immutable IN (0, 1)), blob_id TEXT NOT NULL REFERENCES artifact_blobs(blob_id), export_path TEXT, created_at TEXT NOT NULL, UNIQUE(task_id, kind, title, digest_sha256))",
    "CREATE TABLE IF NOT EXISTS artifact_exports(artifact_id TEXT NOT NULL REFERENCES logical_artifacts(artifact_id) ON DELETE CASCADE, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, export_path TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(artifact_id, export_path), UNIQUE(task_id, export_path))",
    "CREATE INDEX IF NOT EXISTS logical_artifacts_task_kind_created_idx ON logical_artifacts(task_id, kind, created_at DESC, artifact_id)",
    "CREATE INDEX IF NOT EXISTS logical_artifacts_task_created_idx ON logical_artifacts(task_id, created_at DESC, artifact_id)",
    "CREATE INDEX IF NOT EXISTS logical_artifacts_blob_idx ON logical_artifacts(blob_id)",
    "CREATE INDEX IF NOT EXISTS artifact_exports_task_path_idx ON artifact_exports(task_id, export_path)",
    "INSERT OR IGNORE INTO artifact_blobs(blob_id, digest_sha256, mime_type, byte_size, chunk_count, encoding, created_at) SELECT 'blob-' || digest_sha256 || '-' || lower(hex(mime_type)) || '-' || CAST(byte_size AS TEXT), digest_sha256, mime_type, byte_size, chunk_count, CASE WHEN EXISTS(SELECT 1 FROM artifact_chunks c WHERE c.artifact_id = artifacts.artifact_id AND c.text_content IS NOT NULL) THEN 'utf-8' ELSE 'binary' END, created_at FROM artifacts",
    "INSERT OR IGNORE INTO artifact_blob_chunks(blob_id, chunk_no, text_content, blob_content, byte_size, digest_sha256) SELECT 'blob-' || a.digest_sha256 || '-' || lower(hex(a.mime_type)) || '-' || CAST(a.byte_size AS TEXT), c.chunk_no, c.text_content, c.blob_content, c.byte_size, c.digest_sha256 FROM artifacts a JOIN artifact_chunks c ON c.artifact_id = a.artifact_id WHERE a.artifact_id = (SELECT MIN(source.artifact_id) FROM artifacts source WHERE source.digest_sha256 = a.digest_sha256 AND source.mime_type = a.mime_type AND source.byte_size = a.byte_size)",
    "INSERT OR IGNORE INTO logical_artifacts(artifact_id, task_id, kind, title, mime_type, digest_sha256, byte_size, chunk_count, immutable, blob_id, export_path, created_at) SELECT artifact_id, task_id, kind, title, mime_type, digest_sha256, byte_size, chunk_count, immutable, 'blob-' || digest_sha256 || '-' || lower(hex(mime_type)) || '-' || CAST(byte_size AS TEXT), export_path, created_at FROM artifacts",
    "INSERT OR IGNORE INTO artifact_exports(artifact_id, task_id, export_path, created_at) SELECT artifact_id, task_id, export_path, created_at FROM artifacts WHERE export_path IS NOT NULL",
)

_REVISION_AWARE_ORCHESTRATION_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS task_revisions(task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, task_revision INTEGER NOT NULL CHECK(task_revision >= 1), base_revision INTEGER, source TEXT NOT NULL CHECK(source IN ('initial','user_steer','recovery','system')), message_original TEXT NOT NULL, message_language TEXT NOT NULL, message_en TEXT, translation_status TEXT NOT NULL DEFAULT 'not_required' CHECK(translation_status IN ('not_required','pending','translated')), created_at TEXT NOT NULL, PRIMARY KEY(task_id, task_revision))",
    "CREATE TABLE IF NOT EXISTS plan_revisions(task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, plan_revision INTEGER NOT NULL CHECK(plan_revision >= 1), base_plan_revision INTEGER, task_revision INTEGER NOT NULL CHECK(task_revision >= 1), impact_json TEXT NOT NULL, plan_json TEXT, status TEXT NOT NULL CHECK(status IN ('active','superseded','approved','pending')), created_at TEXT NOT NULL, PRIMARY KEY(task_id, plan_revision))",
    "CREATE TABLE IF NOT EXISTS worker_sessions(session_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, host_agent_id TEXT, host_task_name TEXT NOT NULL, host_tool TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1 CHECK(generation >= 1), status TEXT NOT NULL CHECK(status IN ('awaiting_spawn','running','idle_resumable','stopped_recoverable','terminated_unavailable','completed')), resumable INTEGER NOT NULL DEFAULT 1 CHECK(resumable IN (0,1)), started_at TEXT, last_seen_at TEXT NOT NULL, terminated_at TEXT, UNIQUE(task_id, attempt_id, generation), UNIQUE(task_id, host_agent_id))",
    "CREATE TABLE IF NOT EXISTS attempt_messages(message_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, source TEXT NOT NULL CHECK(source IN ('user','coordinator','system')), kind TEXT NOT NULL CHECK(kind IN ('question_answer','steer','correction','recovery')), original_text TEXT NOT NULL, original_language TEXT NOT NULL, canonical_en TEXT NOT NULL, task_revision INTEGER NOT NULL CHECK(task_revision >= 1), created_at TEXT NOT NULL, delivered_at TEXT, acknowledged_at TEXT)",
    "CREATE TABLE IF NOT EXISTS question_batches(batch_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, batch_key TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','answered','superseded')), language TEXT NOT NULL, created_at TEXT NOT NULL, answered_at TEXT, UNIQUE(task_id, attempt_id, batch_key))",
    "CREATE TABLE IF NOT EXISTS question_items(batch_id TEXT NOT NULL REFERENCES question_batches(batch_id) ON DELETE CASCADE, question_key TEXT NOT NULL, question_type TEXT NOT NULL CHECK(question_type IN ('single_select','multi_select','text')), canonical_question TEXT NOT NULL, localized_question TEXT NOT NULL, options_json TEXT NOT NULL, ordinal INTEGER NOT NULL CHECK(ordinal >= 1), PRIMARY KEY(batch_id, question_key), UNIQUE(batch_id, ordinal))",
    "CREATE TABLE IF NOT EXISTS question_answers(batch_id TEXT NOT NULL REFERENCES question_batches(batch_id) ON DELETE CASCADE, question_key TEXT NOT NULL, answer_original TEXT NOT NULL, answer_original_language TEXT NOT NULL, answer_option_ids_json TEXT NOT NULL, answer_en TEXT, translation_status TEXT NOT NULL CHECK(translation_status IN ('not_required','awaiting_translation','translated')), translated_by TEXT, translated_at TEXT, PRIMARY KEY(batch_id, question_key), FOREIGN KEY(batch_id, question_key) REFERENCES question_items(batch_id, question_key) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS orchestration_trace(trace_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT, event TEXT NOT NULL, occurred_at TEXT NOT NULL, metadata_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS tool_observations(observation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, context_epoch INTEGER NOT NULL CHECK(context_epoch >= 0), fingerprint TEXT NOT NULL, tool_name TEXT NOT NULL, normalized_arguments TEXT NOT NULL, workspace_generation TEXT NOT NULL, result_digest TEXT, coverage TEXT, status TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, repeat_count INTEGER NOT NULL DEFAULT 0 CHECK(repeat_count >= 0), UNIQUE(task_id, attempt_id, context_epoch, fingerprint))",
    "CREATE INDEX IF NOT EXISTS task_revisions_created_idx ON task_revisions(task_id, created_at)",
    "CREATE INDEX IF NOT EXISTS plan_revisions_created_idx ON plan_revisions(task_id, created_at)",
    "CREATE INDEX IF NOT EXISTS worker_sessions_status_idx ON worker_sessions(task_id, status, last_seen_at)",
    "CREATE INDEX IF NOT EXISTS attempt_messages_delivery_idx ON attempt_messages(task_id, attempt_id, delivered_at, created_at)",
    "CREATE INDEX IF NOT EXISTS question_batches_status_idx ON question_batches(task_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS orchestration_trace_task_idx ON orchestration_trace(task_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS tool_observations_attempt_idx ON tool_observations(task_id, attempt_id, context_epoch, last_seen_at)",
)

# Governance is deliberately appended to the existing ledger rather than
# creating a second database or rewriting any v1-v8 tables.  Bodies are kept
# as canonical JSON plus a digest in the append-only record table; callers
# may additionally associate an immutable artifact from the normal artifact
# catalog when the record is task-scoped.  This keeps migration v9 safe for
# existing active tasks and preserves the v8 transaction/lock boundary.
_GOVERNANCE_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS initiatives(initiative_ref TEXT PRIMARY KEY, parent_ref TEXT REFERENCES initiatives(initiative_ref) ON DELETE RESTRICT, title TEXT NOT NULL, goal TEXT NOT NULL, owner TEXT NOT NULL, risk TEXT NOT NULL CHECK(risk IN ('low','moderate','high','critical')), acceptance_oracle_artifact_ref TEXT, status TEXT NOT NULL CHECK(status IN ('proposed','active','blocked','completed','closed','cancelled')), revision INTEGER NOT NULL CHECK(revision >= 1), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(parent_ref, title))",
    "CREATE INDEX IF NOT EXISTS initiatives_parent_idx ON initiatives(parent_ref, status, updated_at)",
    "CREATE INDEX IF NOT EXISTS initiatives_status_idx ON initiatives(status, updated_at)",
    "CREATE TABLE IF NOT EXISTS initiative_task_links(initiative_ref TEXT NOT NULL REFERENCES initiatives(initiative_ref) ON DELETE CASCADE, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, relationship TEXT NOT NULL CHECK(relationship IN ('milestone','deliverable','corrective')), milestone TEXT, deliverable TEXT, corrective INTEGER NOT NULL DEFAULT 0 CHECK(corrective IN (0,1)), expected_revision INTEGER NOT NULL DEFAULT 1 CHECK(expected_revision >= 1), created_at TEXT NOT NULL, PRIMARY KEY(initiative_ref, task_id, relationship))",
    "CREATE INDEX IF NOT EXISTS initiative_task_links_task_idx ON initiative_task_links(task_id, relationship)",
    "CREATE TABLE IF NOT EXISTS initiative_dependencies(dependency_ref TEXT PRIMARY KEY, source_type TEXT NOT NULL CHECK(source_type IN ('initiative','task')), source_ref TEXT NOT NULL, target_type TEXT NOT NULL CHECK(target_type IN ('initiative','task')), target_ref TEXT NOT NULL, dependency_type TEXT NOT NULL CHECK(dependency_type IN ('blocks','requires','relates_to','follows')), created_at TEXT NOT NULL, UNIQUE(source_type, source_ref, target_type, target_ref, dependency_type), CHECK(NOT(source_type = target_type AND source_ref = target_ref)))",
    "CREATE INDEX IF NOT EXISTS initiative_dependencies_source_idx ON initiative_dependencies(source_type, source_ref)",
    "CREATE INDEX IF NOT EXISTS initiative_dependencies_target_idx ON initiative_dependencies(target_type, target_ref)",
    "CREATE TABLE IF NOT EXISTS governance_records(record_ref TEXT PRIMARY KEY, initiative_ref TEXT REFERENCES initiatives(initiative_ref) ON DELETE SET NULL, task_id TEXT REFERENCES tasks(task_id) ON DELETE SET NULL, record_type TEXT NOT NULL CHECK(record_type IN ('policy','decision','ruling','preference','assumption','risk','learning','reflection','exception','promotion')), revision INTEGER NOT NULL CHECK(revision >= 1), supersedes TEXT REFERENCES governance_records(record_ref) ON DELETE RESTRICT, status TEXT NOT NULL CHECK(status IN ('pending','active','approved','rejected','superseded','expired')), content_json TEXT NOT NULL, content_digest TEXT NOT NULL, content_artifact_ref TEXT, approval_basis_json TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT, UNIQUE(initiative_ref, task_id, record_type, revision))",
    "CREATE INDEX IF NOT EXISTS governance_records_scope_idx ON governance_records(initiative_ref, task_id, record_type, revision DESC)",
    "CREATE INDEX IF NOT EXISTS governance_records_active_idx ON governance_records(status, record_type, expires_at)",
    "CREATE TABLE IF NOT EXISTS governance_links(link_ref TEXT PRIMARY KEY, record_ref TEXT NOT NULL REFERENCES governance_records(record_ref) ON DELETE CASCADE, initiative_ref TEXT REFERENCES initiatives(initiative_ref) ON DELETE CASCADE, task_id TEXT REFERENCES tasks(task_id) ON DELETE CASCADE, lane_id TEXT REFERENCES lanes(lane_id) ON DELETE CASCADE, finding_fingerprint TEXT, evidence_ref TEXT, relationship TEXT NOT NULL CHECK(relationship IN ('initiative','task','lane','finding','evidence')), created_at TEXT NOT NULL, CHECK(initiative_ref IS NOT NULL OR task_id IS NOT NULL OR lane_id IS NOT NULL OR finding_fingerprint IS NOT NULL OR evidence_ref IS NOT NULL))",
    "CREATE INDEX IF NOT EXISTS governance_links_record_idx ON governance_links(record_ref, relationship)",
    "CREATE INDEX IF NOT EXISTS governance_links_target_idx ON governance_links(initiative_ref, task_id, lane_id, finding_fingerprint)",
)

# v9 intentionally introduced governance without rewriting active task data.
# v10 is the follow-up integrity migration: a scope is made explicit and
# non-null, every revision chain is made linear, and database triggers prevent
# direct SQL from turning an initiative/task record into a broader record.  We
# retain the v9 table rather than rebuilding it so upgrades remain atomic and
# preserve record identifiers and artifact references.
_GOVERNANCE_INTEGRITY_SCHEMA_STATEMENTS = (
    "ALTER TABLE governance_records ADD COLUMN scope_key TEXT NOT NULL DEFAULT ''",
    "UPDATE governance_records SET scope_key = CASE "
    "WHEN initiative_ref IS NOT NULL AND task_id IS NOT NULL THEN 'initiative-task:' || length(initiative_ref) || ':' || initiative_ref || ':' || length(task_id) || ':' || task_id "
    "WHEN initiative_ref IS NOT NULL THEN 'initiative:' || length(initiative_ref) || ':' || initiative_ref "
    "WHEN task_id IS NOT NULL THEN 'task:' || length(task_id) || ':' || task_id "
    "ELSE 'project:' END",
    "CREATE TRIGGER governance_records_scope_integrity_insert BEFORE INSERT ON governance_records FOR EACH ROW "
    "WHEN NEW.scope_key != CASE "
    "WHEN NEW.initiative_ref IS NOT NULL AND NEW.task_id IS NOT NULL THEN 'initiative-task:' || length(NEW.initiative_ref) || ':' || NEW.initiative_ref || ':' || length(NEW.task_id) || ':' || NEW.task_id "
    "WHEN NEW.initiative_ref IS NOT NULL THEN 'initiative:' || length(NEW.initiative_ref) || ':' || NEW.initiative_ref "
    "WHEN NEW.task_id IS NOT NULL THEN 'task:' || length(NEW.task_id) || ':' || NEW.task_id "
    "ELSE 'project:' END "
    "OR (NEW.initiative_ref IS NOT NULL AND NEW.task_id IS NOT NULL AND NOT EXISTS "
    "(SELECT 1 FROM initiative_task_links WHERE initiative_ref=NEW.initiative_ref AND task_id=NEW.task_id)) "
    "BEGIN SELECT RAISE(ABORT, 'governance scope integrity violation'); END",
    "CREATE TRIGGER governance_records_scope_integrity_update BEFORE UPDATE OF initiative_ref, task_id, scope_key ON governance_records FOR EACH ROW "
    "WHEN NEW.scope_key != CASE "
    "WHEN NEW.initiative_ref IS NOT NULL AND NEW.task_id IS NOT NULL THEN 'initiative-task:' || length(NEW.initiative_ref) || ':' || NEW.initiative_ref || ':' || length(NEW.task_id) || ':' || NEW.task_id "
    "WHEN NEW.initiative_ref IS NOT NULL THEN 'initiative:' || length(NEW.initiative_ref) || ':' || NEW.initiative_ref "
    "WHEN NEW.task_id IS NOT NULL THEN 'task:' || length(NEW.task_id) || ':' || NEW.task_id "
    "ELSE 'project:' END "
    "OR (NEW.initiative_ref IS NOT NULL AND NEW.task_id IS NOT NULL AND NOT EXISTS "
    "(SELECT 1 FROM initiative_task_links WHERE initiative_ref=NEW.initiative_ref AND task_id=NEW.task_id)) "
    "BEGIN SELECT RAISE(ABORT, 'governance scope integrity violation'); END",
    # Re-evaluate prior-release v9 rows through the just-installed trigger.  A
    # prior-release record with a cross-initiative task link must fail closed instead
    # of silently becoming valid in a wider scope after upgrade.
    "UPDATE governance_records SET scope_key = scope_key",
    "CREATE UNIQUE INDEX governance_records_scope_revision_unique ON governance_records(scope_key, record_type, revision)",
    "CREATE UNIQUE INDEX governance_records_supersedes_unique ON governance_records(supersedes) WHERE supersedes IS NOT NULL",
    "CREATE TABLE governance_submissions(submission_id TEXT PRIMARY KEY, command_digest TEXT NOT NULL, record_ref TEXT NOT NULL REFERENCES governance_records(record_ref) ON DELETE RESTRICT, created_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX governance_submissions_record_idx ON governance_submissions(record_ref)",
    "CREATE TRIGGER governance_records_immutable_update BEFORE UPDATE ON governance_records FOR EACH ROW "
    "WHEN NEW.initiative_ref IS NOT OLD.initiative_ref OR NEW.task_id IS NOT OLD.task_id OR NEW.scope_key IS NOT OLD.scope_key "
    "OR NEW.record_type IS NOT OLD.record_type OR NEW.revision IS NOT OLD.revision OR NEW.supersedes IS NOT OLD.supersedes "
    "OR NEW.content_json IS NOT OLD.content_json OR NEW.content_digest IS NOT OLD.content_digest "
    "OR NEW.content_artifact_ref IS NOT OLD.content_artifact_ref OR NEW.created_by IS NOT OLD.created_by "
    "OR NEW.created_at IS NOT OLD.created_at OR NEW.expires_at IS NOT OLD.expires_at "
    "BEGIN SELECT RAISE(ABORT, 'governance record immutable fields cannot change'); END",
    "CREATE TRIGGER governance_records_task_delete_restrict BEFORE DELETE ON tasks FOR EACH ROW "
    "WHEN EXISTS (SELECT 1 FROM governance_records WHERE task_id=OLD.task_id) "
    "BEGIN SELECT RAISE(ABORT, 'governance records prevent task deletion'); END",
    "CREATE TRIGGER governance_records_initiative_delete_restrict BEFORE DELETE ON initiatives FOR EACH ROW "
    "WHEN EXISTS (SELECT 1 FROM governance_records WHERE initiative_ref=OLD.initiative_ref) "
    "BEGIN SELECT RAISE(ABORT, 'governance records prevent initiative deletion'); END",
)

# v11 binds the mutable lifecycle projection in ``governance_records`` to an
# append-only transition chain.  A record body was already immutable in v10,
# but a raw SQL writer could still rewrite its current status or approval
# basis.  The lifecycle row carries the exact predecessor binding, current
# status and approval basis, while the record keeps only the indexed current
# projection.  Reads verify the cryptographic chain in governance.py.
_GOVERNANCE_LIFECYCLE_INTEGRITY_SCHEMA_STATEMENTS = (
    "ALTER TABLE governance_records ADD COLUMN lifecycle_sequence INTEGER NOT NULL DEFAULT 0 CHECK(lifecycle_sequence >= 0)",
    "ALTER TABLE governance_records ADD COLUMN lifecycle_binding TEXT NOT NULL DEFAULT ''",
    "CREATE TABLE governance_record_lifecycle(lifecycle_ref TEXT PRIMARY KEY, record_ref TEXT NOT NULL REFERENCES governance_records(record_ref) ON DELETE RESTRICT, lifecycle_sequence INTEGER NOT NULL CHECK(lifecycle_sequence >= 0), previous_binding TEXT, status TEXT NOT NULL CHECK(status IN ('pending','active','approved','rejected','superseded','expired')), approval_basis_json TEXT, binding TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('created','transition','migration_baseline')), actor_role TEXT NOT NULL CHECK(actor_role IN ('coordinator','worker','reviewer','system')), created_at TEXT NOT NULL, UNIQUE(record_ref,lifecycle_sequence))",
    "CREATE INDEX governance_record_lifecycle_record_idx ON governance_record_lifecycle(record_ref,lifecycle_sequence)",
    "CREATE TRIGGER governance_record_lifecycle_insert_integrity BEFORE INSERT ON governance_record_lifecycle FOR EACH ROW "
    "WHEN NEW.lifecycle_sequence < 0 OR length(NEW.binding) != 64 "
    "OR NEW.status NOT IN ('pending','active','approved','rejected','superseded','expired') "
    "OR (NEW.lifecycle_sequence = 0 AND (NEW.previous_binding IS NOT NULL "
    "OR EXISTS (SELECT 1 FROM governance_record_lifecycle WHERE record_ref=NEW.record_ref) "
    "OR NOT EXISTS (SELECT 1 FROM governance_records WHERE record_ref=NEW.record_ref "
    "AND lifecycle_sequence=0 AND (lifecycle_binding='' OR lifecycle_binding=NEW.binding) "
    "AND status IS NEW.status AND approval_basis_json IS NEW.approval_basis_json))) "
    "OR (NEW.lifecycle_sequence > 0 AND (EXISTS (SELECT 1 FROM governance_record_lifecycle WHERE record_ref=NEW.record_ref AND lifecycle_sequence=NEW.lifecycle_sequence) "
    "OR NOT EXISTS (SELECT 1 FROM governance_records WHERE record_ref=NEW.record_ref "
    "AND lifecycle_sequence=NEW.lifecycle_sequence-1 AND lifecycle_binding=NEW.previous_binding))) "
    "BEGIN SELECT RAISE(ABORT, 'governance lifecycle append integrity violation'); END",
    "CREATE TRIGGER governance_records_lifecycle_authority_update BEFORE UPDATE OF status,approval_basis_json,lifecycle_sequence,lifecycle_binding ON governance_records FOR EACH ROW "
    "WHEN NOT ((OLD.lifecycle_binding='' AND OLD.lifecycle_sequence=0 "
    "AND NEW.lifecycle_sequence=0 AND NEW.status IS OLD.status AND NEW.approval_basis_json IS OLD.approval_basis_json "
    "AND EXISTS (SELECT 1 FROM governance_record_lifecycle WHERE record_ref=NEW.record_ref AND lifecycle_sequence=0 "
    "AND binding=NEW.lifecycle_binding AND status IS NEW.status AND approval_basis_json IS NEW.approval_basis_json)) "
    "OR (NEW.lifecycle_sequence=OLD.lifecycle_sequence+1 "
    "AND EXISTS (SELECT 1 FROM governance_record_lifecycle WHERE record_ref=NEW.record_ref "
    "AND lifecycle_sequence=NEW.lifecycle_sequence AND previous_binding=OLD.lifecycle_binding "
    "AND binding=NEW.lifecycle_binding AND status IS NEW.status AND approval_basis_json IS NEW.approval_basis_json))) "
    "BEGIN SELECT RAISE(ABORT, 'governance lifecycle authority violation'); END",
    "CREATE TRIGGER governance_record_lifecycle_immutable_update BEFORE UPDATE ON governance_record_lifecycle FOR EACH ROW "
    "BEGIN SELECT RAISE(ABORT, 'governance lifecycle events are append-only'); END",
    "CREATE TRIGGER governance_record_lifecycle_immutable_delete BEFORE DELETE ON governance_record_lifecycle FOR EACH ROW "
    "BEGIN SELECT RAISE(ABORT, 'governance lifecycle events are append-only'); END",
    "CREATE TRIGGER initiative_task_links_governance_delete_restrict BEFORE DELETE ON initiative_task_links FOR EACH ROW "
    "WHEN EXISTS (SELECT 1 FROM governance_records WHERE initiative_ref=OLD.initiative_ref AND task_id=OLD.task_id) "
    "OR EXISTS (SELECT 1 FROM governance_records AS records JOIN governance_links AS links ON links.record_ref=records.record_ref "
    "WHERE records.initiative_ref=OLD.initiative_ref AND links.relationship='task' AND links.task_id=OLD.task_id) "
    "OR EXISTS (SELECT 1 FROM governance_records AS records JOIN governance_links AS links ON links.record_ref=records.record_ref "
    "WHERE records.task_id=OLD.task_id AND links.relationship='initiative' AND links.initiative_ref=OLD.initiative_ref) "
    "OR EXISTS (SELECT 1 FROM governance_links AS initiative_links JOIN governance_links AS task_links "
    "ON task_links.record_ref=initiative_links.record_ref WHERE initiative_links.relationship='initiative' "
    "AND initiative_links.initiative_ref=OLD.initiative_ref AND task_links.relationship='task' AND task_links.task_id=OLD.task_id) "
    "BEGIN SELECT RAISE(ABORT, 'governance records prevent initiative task link deletion'); END",
    "CREATE TRIGGER initiative_task_links_terminal_success_insert BEFORE INSERT ON initiative_task_links FOR EACH ROW "
    "WHEN NEW.relationship IN ('milestone','deliverable') AND EXISTS (SELECT 1 FROM initiatives WHERE initiative_ref=NEW.initiative_ref AND status IN ('completed','closed')) "
    "AND NOT EXISTS (SELECT 1 FROM tasks WHERE task_id=NEW.task_id AND status='completed') "
    "BEGIN SELECT RAISE(ABORT, 'terminal initiative requires linked task terminal success'); END",
    "CREATE TRIGGER initiatives_terminal_linked_task_integrity_update BEFORE UPDATE OF status ON initiatives FOR EACH ROW "
    "WHEN NEW.status IN ('completed','closed') AND EXISTS (SELECT 1 FROM initiative_task_links AS links JOIN tasks ON tasks.task_id=links.task_id "
    "WHERE links.initiative_ref=NEW.initiative_ref AND links.relationship IN ('milestone','deliverable') AND tasks.status!='completed') "
    "BEGIN SELECT RAISE(ABORT, 'initiative completion requires linked task terminal success'); END",
    "CREATE TRIGGER tasks_terminal_linked_initiative_integrity_update BEFORE UPDATE OF status ON tasks FOR EACH ROW "
    "WHEN NEW.status!='completed' AND EXISTS (SELECT 1 FROM initiative_task_links AS links JOIN initiatives ON initiatives.initiative_ref=links.initiative_ref "
    "WHERE links.task_id=NEW.task_id AND links.relationship IN ('milestone','deliverable') AND initiatives.status IN ('completed','closed')) "
    "BEGIN SELECT RAISE(ABORT, 'terminal initiative requires linked task terminal success'); END",
)

# v12 seals the *complete* lifecycle event envelope with a host-private HMAC.
# The v11 SHA-256 chain remains intact and readable; a separate append-only
# authentication projection avoids rewriting a released migration checksum or
# mutating historical lifecycle rows during upgrade.
_GOVERNANCE_LIFECYCLE_ENVELOPE_AUTH_SCHEMA_STATEMENTS = (
    "CREATE TABLE governance_record_lifecycle_auth(lifecycle_ref TEXT PRIMARY KEY REFERENCES governance_record_lifecycle(lifecycle_ref) ON DELETE RESTRICT, envelope_hmac TEXT NOT NULL)",
    "CREATE TRIGGER governance_record_lifecycle_auth_insert_integrity BEFORE INSERT ON governance_record_lifecycle_auth FOR EACH ROW "
    "WHEN length(NEW.envelope_hmac) != 64 OR NOT EXISTS (SELECT 1 FROM governance_record_lifecycle WHERE lifecycle_ref=NEW.lifecycle_ref) "
    "OR EXISTS (SELECT 1 FROM governance_record_lifecycle_auth WHERE lifecycle_ref=NEW.lifecycle_ref) "
    "BEGIN SELECT RAISE(ABORT, 'governance lifecycle authentication integrity violation'); END",
    "CREATE TRIGGER governance_record_lifecycle_auth_immutable_update BEFORE UPDATE ON governance_record_lifecycle_auth FOR EACH ROW "
    "BEGIN SELECT RAISE(ABORT, 'governance lifecycle authentication is append-only'); END",
    "CREATE TRIGGER governance_record_lifecycle_auth_immutable_delete BEFORE DELETE ON governance_record_lifecycle_auth FOR EACH ROW "
    "BEGIN SELECT RAISE(ABORT, 'governance lifecycle authentication is append-only'); END",
)

# v13 separates the small semantic result supplied by a worker from the
# server-observed attempt metadata and from rebuildable result projections.
# ``attempt_results`` is deliberately one row per dispatched attempt: a
# completed worker must never be replaced merely because a later result
# materialization or other finalization step is unavailable.  The append-only
# ``attempt_events`` stream checkpoints useful facts while work is in flight.
_ATTEMPT_RESULT_EVENT_PROTOCOL_SCHEMA_STATEMENTS = (
    "CREATE TABLE attempt_results(result_ref TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, result_status TEXT NOT NULL CHECK(result_status IN ('completed','blocked','failed')), lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('WORK_COMPLETED','FINALIZING','COMPLETED','BLOCKED','FAILED')), summary TEXT NOT NULL, findings_json TEXT NOT NULL, decisions_needed_json TEXT NOT NULL, unresolved_json TEXT NOT NULL, claims_json TEXT NOT NULL, metadata_json TEXT NOT NULL, workspace_observation_json TEXT NOT NULL, changed_files_json TEXT NOT NULL, changed_files_status TEXT NOT NULL CHECK(changed_files_status IN ('server_observed','unavailable','incomplete','not_attributable')), content_digest TEXT NOT NULL, submission_id TEXT NOT NULL, work_completed_at TEXT, finalizing_at TEXT, completed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(task_id, attempt_id), UNIQUE(task_id, attempt_id, submission_id))",
    "CREATE INDEX attempt_results_task_lifecycle_idx ON attempt_results(task_id, lifecycle_status, updated_at)",
    "CREATE INDEX attempt_results_attempt_idx ON attempt_results(task_id, attempt_id)",
    "CREATE TABLE attempt_events(event_ref TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, event_key TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence >= 1), event_type TEXT NOT NULL CHECK(event_type IN ('finding_added','decision_evidence','blocker','verification_observed','progress','note','briefing_acknowledged','predecessor_read','work_completed','finalizing','finalization_failed','completed')), payload_json TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN ('worker','cortex','system')), occurred_at TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id, attempt_id, event_key), UNIQUE(task_id, attempt_id, sequence))",
    "CREATE INDEX attempt_events_task_attempt_sequence_idx ON attempt_events(task_id, attempt_id, sequence)",
    "CREATE INDEX attempt_events_task_type_idx ON attempt_events(task_id, event_type, occurred_at)",
)

# v14 makes verification authority explicit: workers can claim a check, but
# only Cortex may emit the observed verification event consumed by gates.
_ATTEMPT_VERIFICATION_AUTHORITY_SCHEMA_STATEMENTS = (
    "DROP INDEX attempt_events_task_type_idx",
    "DROP INDEX attempt_events_task_attempt_sequence_idx",
    "ALTER TABLE attempt_events RENAME TO attempt_events_v13",
    "CREATE TABLE attempt_events(event_ref TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, event_key TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence >= 1), event_type TEXT NOT NULL CHECK(event_type IN ('finding_added','decision_evidence','blocker','verification_claimed','verification_observed','progress','note','briefing_acknowledged','predecessor_read','work_completed','finalizing','finalization_failed','completed')), payload_json TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN ('worker','cortex','system')), occurred_at TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id, attempt_id, event_key), UNIQUE(task_id, attempt_id, sequence))",
    "INSERT INTO attempt_events(event_ref,task_id,attempt_id,event_key,sequence,event_type,payload_json,actor,occurred_at,created_at) SELECT event_ref,task_id,attempt_id,event_key,sequence,event_type,payload_json,actor,occurred_at,created_at FROM attempt_events_v13",
    "DROP TABLE attempt_events_v13",
    "CREATE INDEX attempt_events_task_attempt_sequence_idx ON attempt_events(task_id, attempt_id, sequence)",
    "CREATE INDEX attempt_events_task_type_idx ON attempt_events(task_id, event_type, occurred_at)",
)

# v15 makes durable question/decision transitions first-class AttemptEvents.
# The question documents remain the detailed interaction records; this table
# is the immutable attempt-local timeline consumed by canonical compilers.
_ATTEMPT_QUESTION_EVENT_SCHEMA_STATEMENTS = (
    "DROP INDEX attempt_events_task_type_idx",
    "DROP INDEX attempt_events_task_attempt_sequence_idx",
    "ALTER TABLE attempt_events RENAME TO attempt_events_v14",
    "CREATE TABLE attempt_events(event_ref TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, event_key TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence >= 1), event_type TEXT NOT NULL CHECK(event_type IN ('finding_added','decision_evidence','blocker','verification_claimed','verification_observed','progress','note','briefing_acknowledged','predecessor_read','question_created','question_answered','decision_resolved','work_completed','finalizing','finalization_failed','completed')), payload_json TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN ('worker','cortex','system')), occurred_at TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id, attempt_id, event_key), UNIQUE(task_id, attempt_id, sequence))",
    "INSERT INTO attempt_events(event_ref,task_id,attempt_id,event_key,sequence,event_type,payload_json,actor,occurred_at,created_at) SELECT event_ref,task_id,attempt_id,event_key,sequence,event_type,payload_json,actor,occurred_at,created_at FROM attempt_events_v14",
    "DROP TABLE attempt_events_v14",
    "CREATE INDEX attempt_events_task_attempt_sequence_idx ON attempt_events(task_id, attempt_id, sequence)",
    "CREATE INDEX attempt_events_task_type_idx ON attempt_events(task_id, event_type, occurred_at)",
)


def _v9_scope_key(initiative_ref: object, task_id: object) -> str:
    """Mirror v10's collision-free scope key before the v10 columns exist."""
    initiative = str(initiative_ref or "")
    task = str(task_id or "")
    if initiative and task:
        return f"initiative-task:{len(initiative)}:{initiative}:{len(task)}:{task}"
    if initiative:
        return f"initiative:{len(initiative)}:{initiative}"
    if task:
        return f"task:{len(task)}:{task}"
    return "project:"


def _v9_governance_upgrade_error(code: str) -> ValueError:
    """Return a bounded, non-content diagnostic for an unsafe v9 upgrade."""
    return ValueError(f"Cortex v9 governance migration blocked [{code}]; ledger maintenance is required")


def _prepare_v9_governance_integrity_upgrade(connection: sqlite3.Connection) -> None:
    """Reconcile only deterministic v9 conflicts before the v10 indexes exist.

    SQLite's old nullable scope uniqueness let project-/task-scoped records
    reuse a revision number, and it allowed more than one successor for a
    predecessor.  v10 intentionally rejects both states.  Rather than leave
    a partially explained ``CREATE UNIQUE INDEX`` failure, this preflight
    deterministically linearises *only* affected scope/type groups using
    created-at/record-ref order.  Missing scope links, dangling predecessors,
    cross-scope predecessors, and cycles are ambiguous integrity failures and
    fail closed before v10 applies.  The containing migration transaction
    rolls every preparatory change back on such a failure.
    """
    rows = [dict(row) for row in connection.execute(
        "SELECT record_ref,initiative_ref,task_id,record_type,revision,supersedes,created_at FROM governance_records ORDER BY created_at,record_ref"
    )]
    if not rows:
        return
    by_ref = {str(row["record_ref"]): row for row in rows}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        initiative = str(row["initiative_ref"] or "") or None
        task = str(row["task_id"] or "") or None
        if initiative and task and connection.execute(
            "SELECT 1 FROM initiative_task_links WHERE initiative_ref=? AND task_id=? LIMIT 1",
            (initiative, task),
        ).fetchone() is None:
            raise _v9_governance_upgrade_error("v9_scope_link_missing")
        row["_scope_key"] = _v9_scope_key(initiative, task)
        groups.setdefault((str(row["_scope_key"]), str(row["record_type"])), []).append(row)

    reconciled_groups = 0
    duplicate_groups = 0
    sibling_groups = 0
    for group_rows in groups.values():
        children: dict[str | None, list[dict[str, Any]]] = {}
        duplicate_revisions: set[int] = set()
        seen_revisions: set[int] = set()
        for row in group_rows:
            revision = int(row["revision"])
            if revision in seen_revisions:
                duplicate_revisions.add(revision)
            seen_revisions.add(revision)
            predecessor = str(row["supersedes"] or "") or None
            if predecessor:
                parent = by_ref.get(predecessor)
                if parent is None:
                    raise _v9_governance_upgrade_error("v9_supersedes_missing")
                if (
                    str(parent["record_type"]) != str(row["record_type"])
                    or str(parent["_scope_key"]) != str(row["_scope_key"])
                ):
                    raise _v9_governance_upgrade_error("v9_supersedes_scope_mismatch")
            children.setdefault(predecessor, []).append(row)
        # A predecessor chain must already be acyclic.  A deterministic sort
        # cannot safely infer a meaning for a cyclic historical graph.
        for row in group_rows:
            visited: set[str] = set()
            current = row
            while True:
                current_ref = str(current["record_ref"])
                if current_ref in visited:
                    raise _v9_governance_upgrade_error("v9_supersedes_cycle")
                visited.add(current_ref)
                parent_ref = str(current["supersedes"] or "") or None
                if not parent_ref:
                    break
                current = by_ref[parent_ref]
        sibling_parents = [parent for parent, items in children.items() if parent is not None and len(items) > 1]
        if not duplicate_revisions and not sibling_parents:
            continue
        duplicate_groups += int(bool(duplicate_revisions))
        sibling_groups += int(bool(sibling_parents))
        reconciled_groups += 1
        ordered: list[dict[str, Any]] = []

        def visit(item: dict[str, Any]) -> None:
            ordered.append(item)
            for child in sorted(children.get(str(item["record_ref"]), []), key=lambda value: (str(value["created_at"]), str(value["record_ref"]))):
                visit(child)

        roots = sorted(children.get(None, []), key=lambda value: (str(value["created_at"]), str(value["record_ref"])))
        for root in roots:
            visit(root)
        if len(ordered) != len(group_rows):
            raise _v9_governance_upgrade_error("v9_supersedes_graph_incomplete")
        # Move every revision out of the old range before assigning its
        # canonical number, avoiding transient uniqueness conflicts for
        # non-null prior-release scopes.
        offset = max([int(item["revision"]) for item in group_rows] + [0]) + len(group_rows) + 1
        for item in group_rows:
            connection.execute("UPDATE governance_records SET revision=revision+? WHERE record_ref=?", (offset, item["record_ref"]))
        for revision, item in enumerate(ordered, start=1):
            connection.execute(
                "UPDATE governance_records SET revision=? WHERE record_ref=?",
                (revision, item["record_ref"]),
            )
        # The depth-first order makes descendants immediately follow their
        # predecessor before a former sibling branch is appended.  Independent
        # roots remain independent, while each affected branch becomes linear.
        root_previous: str | None = None
        root_of: dict[str, str] = {}
        for root in roots:
            stack = [root]
            while stack:
                item = stack.pop()
                root_of[str(item["record_ref"])] = str(root["record_ref"])
                stack.extend(children.get(str(item["record_ref"]), []))
        last_by_root: dict[str, str | None] = {}
        for item in ordered:
            ref = str(item["record_ref"])
            root_ref = root_of.get(ref)
            predecessor = last_by_root.get(root_ref) if root_ref else None
            connection.execute("UPDATE governance_records SET supersedes=? WHERE record_ref=?", (predecessor, ref))
            if root_ref:
                last_by_root[root_ref] = ref
    if reconciled_groups:
        connection.execute(
            "INSERT INTO ledger_meta(key,value) VALUES('governance_v10_reconciliation',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_canonical_json({
                "schema": "cortex/governance-v10-reconciliation/v1",
                "reconciled_scope_type_groups": reconciled_groups,
                "duplicate_scope_revision_groups": duplicate_groups,
                "sibling_successor_groups": sibling_groups,
            }),),
        )


def _finalize_governance_lifecycle_migration(connection: sqlite3.Connection) -> None:
    """Attach one genesis lifecycle event to every pre-v11 governance row."""
    rows = connection.execute(
        "SELECT record_ref,status,approval_basis_json,lifecycle_sequence,lifecycle_binding FROM governance_records ORDER BY record_ref"
    ).fetchall()
    for row in rows:
        if int(row["lifecycle_sequence"]) != 0 or str(row["lifecycle_binding"] or ""):
            raise ValueError("Cortex governance lifecycle migration state is inconsistent")
        record_ref = str(row["record_ref"])
        status = str(row["status"])
        approval_basis_json = str(row["approval_basis_json"]) if row["approval_basis_json"] is not None else None
        binding = governance_lifecycle_binding(
            record_ref=record_ref,
            sequence=0,
            previous_binding=None,
            status=status,
            approval_basis_json=approval_basis_json,
        )
        lifecycle_ref = "lifecycle-" + hashlib.sha256(f"{record_ref}:0:{binding}".encode("utf-8")).hexdigest()[:32]
        connection.execute(
            "INSERT INTO governance_record_lifecycle(lifecycle_ref,record_ref,lifecycle_sequence,previous_binding,status,approval_basis_json,binding,action,actor_role,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (lifecycle_ref, record_ref, 0, None, status, approval_basis_json, binding, "migration_baseline", "system", _now()),
        )
        connection.execute(
            "UPDATE governance_records SET lifecycle_binding=? WHERE record_ref=?",
            (binding, record_ref),
        )


def insert_governance_lifecycle_auth(
    root: Path,
    connection: sqlite3.Connection,
    *,
    lifecycle_ref: str,
    record_ref: str,
    lifecycle_sequence: int,
    previous_binding: str | None,
    status: str,
    approval_basis_json: str | None,
    binding: str,
    action: str,
    actor_role: str,
    created_at: str,
    create_key: bool = False,
) -> None:
    """Append the v12 host-authentication projection for one lifecycle row."""
    envelope_hmac = governance_lifecycle_envelope_hmac(
        root,
        lifecycle_ref=lifecycle_ref,
        record_ref=record_ref,
        lifecycle_sequence=lifecycle_sequence,
        previous_binding=previous_binding,
        status=status,
        approval_basis_json=approval_basis_json,
        binding=binding,
        action=action,
        actor_role=actor_role,
        created_at=created_at,
        create_key=create_key,
    )
    connection.execute(
        "INSERT INTO governance_record_lifecycle_auth(lifecycle_ref,envelope_hmac) VALUES(?,?)",
        (lifecycle_ref, envelope_hmac),
    )


def _finalize_governance_lifecycle_envelope_auth_migration(root: Path, connection: sqlite3.Connection) -> None:
    """Seal each released v11 event without rewriting its public chain."""
    # Initialize the host key even for a fresh ledger with no governance
    # events.  Later runtime writes must never recreate a missing key, since
    # that would turn key deletion into an undetectable reseal opportunity.
    _governance_lifecycle_hmac_key(root, create=True)
    rows = connection.execute(
        "SELECT lifecycle_ref,record_ref,lifecycle_sequence,previous_binding,status,approval_basis_json,binding,action,actor_role,created_at "
        "FROM governance_record_lifecycle ORDER BY record_ref,lifecycle_sequence"
    ).fetchall()
    for row in rows:
        insert_governance_lifecycle_auth(
            root,
            connection,
            lifecycle_ref=str(row["lifecycle_ref"]),
            record_ref=str(row["record_ref"]),
            lifecycle_sequence=int(row["lifecycle_sequence"]),
            previous_binding=str(row["previous_binding"] or "") or None,
            status=str(row["status"]),
            approval_basis_json=(str(row["approval_basis_json"]) if row["approval_basis_json"] is not None else None),
            binding=str(row["binding"]),
            action=str(row["action"]),
            actor_role=str(row["actor_role"]),
            created_at=str(row["created_at"]),
        )


def _migration_plan() -> tuple[_Migration, ...]:
    return (
        _Migration(1, "sqlite-ledger-base", _BASE_SCHEMA_STATEMENTS),
        _Migration(2, "immutable-artifact-catalog-and-chunks", _ARTIFACT_SCHEMA_STATEMENTS),
        _Migration(3, "canonical-task-findings", _CLOSURE_SCHEMA_STATEMENTS),
        _Migration(4, "finding-waiver-and-resolution-metadata", _FINDING_METADATA_SCHEMA_STATEMENTS),
        _Migration(5, "projection-jobs", _PROJECTION_SCHEMA_STATEMENTS),
        _Migration(6, "crash-safe-prune-tombstones", _PRUNE_SCHEMA_STATEMENTS),
        _Migration(7, "canonical-content-blobs-and-logical-artifacts", _ARTIFACT_NORMALIZATION_SCHEMA_STATEMENTS),
        _Migration(8, "revision-aware-orchestration", _REVISION_AWARE_ORCHESTRATION_SCHEMA_STATEMENTS),
        _Migration(9, "governance-ledger", _GOVERNANCE_SCHEMA_STATEMENTS),
        _Migration(10, "governance-integrity-hardening", _GOVERNANCE_INTEGRITY_SCHEMA_STATEMENTS),
        _Migration(11, "governance-lifecycle-authority", _GOVERNANCE_LIFECYCLE_INTEGRITY_SCHEMA_STATEMENTS),
        _Migration(12, "governance-lifecycle-envelope-authentication", _GOVERNANCE_LIFECYCLE_ENVELOPE_AUTH_SCHEMA_STATEMENTS),
        _Migration(13, "attempt-result-event-protocol", _ATTEMPT_RESULT_EVENT_PROTOCOL_SCHEMA_STATEMENTS),
        _Migration(14, "attempt-verification-authority", _ATTEMPT_VERIFICATION_AUTHORITY_SCHEMA_STATEMENTS),
        _Migration(15, "attempt-question-decision-events", _ATTEMPT_QUESTION_EVENT_SCHEMA_STATEMENTS),
    )


def _assert_migration_schema(connection: sqlite3.Connection, version: int) -> None:
    required = {
        1: {
            "schema_migrations", "ledger_meta", "tasks", "lanes", "classifications",
            "manifest_snapshots", "global_documents", "operations", "ledger_events",
            "task_documents",
        },
        2: {"artifacts", "artifact_chunks"},
        3: {"task_findings"},
        4: {"task_findings"},
        5: {"projection_jobs", "projection_jobs_status_idx", "projection_jobs_task_idx"},
        6: {"prune_tombstones", "prune_tombstones_active_task_idx", "prune_tombstones_status_idx"},
        7: {
            "artifact_blobs", "artifact_blob_chunks", "logical_artifacts", "artifact_exports",
            "logical_artifacts_task_kind_created_idx", "logical_artifacts_task_created_idx",
            "logical_artifacts_blob_idx", "artifact_exports_task_path_idx",
        },
        8: {
            "task_revisions", "plan_revisions", "worker_sessions", "attempt_messages",
            "question_batches", "question_items", "question_answers", "orchestration_trace",
            "tool_observations", "task_revisions_created_idx", "plan_revisions_created_idx",
            "worker_sessions_status_idx", "attempt_messages_delivery_idx", "question_batches_status_idx",
            "orchestration_trace_task_idx", "tool_observations_attempt_idx",
        },
        9: {
            "initiatives", "initiatives_parent_idx", "initiatives_status_idx",
            "initiative_task_links", "initiative_task_links_task_idx",
            "initiative_dependencies", "initiative_dependencies_source_idx",
            "initiative_dependencies_target_idx", "governance_records",
            "governance_records_scope_idx", "governance_records_active_idx",
            "governance_links", "governance_links_record_idx",
            "governance_links_target_idx",
        },
        10: {
            "governance_records_scope_revision_unique", "governance_records_supersedes_unique",
            "governance_submissions", "governance_submissions_record_idx",
            "governance_records_scope_integrity_insert", "governance_records_scope_integrity_update",
            "governance_records_immutable_update", "governance_records_task_delete_restrict",
            "governance_records_initiative_delete_restrict",
        },
        11: {
            "governance_record_lifecycle", "governance_record_lifecycle_record_idx",
            "governance_record_lifecycle_insert_integrity",
            "governance_records_lifecycle_authority_update",
            "governance_record_lifecycle_immutable_update",
            "governance_record_lifecycle_immutable_delete",
            "initiative_task_links_governance_delete_restrict",
            "initiative_task_links_terminal_success_insert",
            "initiatives_terminal_linked_task_integrity_update",
            "tasks_terminal_linked_initiative_integrity_update",
        },
        12: {
            "governance_record_lifecycle_auth",
            "governance_record_lifecycle_auth_insert_integrity",
            "governance_record_lifecycle_auth_immutable_update",
            "governance_record_lifecycle_auth_immutable_delete",
        },
        13: {
            "attempt_results", "attempt_results_task_lifecycle_idx", "attempt_results_attempt_idx",
            "attempt_events", "attempt_events_task_attempt_sequence_idx", "attempt_events_task_type_idx",
        },
        14: {
            "attempt_events", "attempt_events_task_attempt_sequence_idx", "attempt_events_task_type_idx",
        },
        15: {
            "attempt_events", "attempt_events_task_attempt_sequence_idx", "attempt_events_task_type_idx",
        },
    }
    present = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'index', 'trigger')"
        )
    }
    if not required.get(version, set()).issubset(present):
        raise ValueError("Cortex database schema is inconsistent with migration history")
    column_requirements: dict[int, dict[str, set[str]]] = {
        1: {
            "schema_migrations": {"version", "name", "applied_at", "checksum"},
            "ledger_meta": {"key", "value"},
            "tasks": {"task_id", "task_number", "artifact_dir", "definition_json", "state_json", "plan_json", "status", "revision", "created_at", "updated_at"},
            "lanes": {"lane_id", "definition_json", "state_json", "status", "revision", "created_at", "updated_at"},
            "classifications": {"classification_id", "payload_json", "consumed_by", "created_at"},
            "manifest_snapshots": {"snapshot_ref", "digest", "payload_json", "created_at"},
            "global_documents": {"name", "payload_json", "updated_at"},
            "operations": {"submission_id", "task_id", "payload_json", "updated_at"},
            "ledger_events": {"event_id", "task_id", "lane_id", "event", "detail", "revision", "created_at"},
            "task_documents": {"task_id", "document_key", "payload_json", "updated_at"},
        },
        2: {
            "artifacts": {"artifact_id", "task_id", "kind", "title", "mime_type", "digest_sha256", "byte_size", "chunk_count", "immutable", "export_path", "created_at"},
            "artifact_chunks": {"artifact_id", "chunk_no", "text_content", "blob_content", "byte_size", "digest_sha256"},
        },
        3: {
            "task_findings": {"task_id", "fingerprint", "severity", "status", "blocking", "summary", "details", "next_action_json", "source_evidence_json", "first_seen_at", "updated_at"},
        },
        4: {"task_findings": {"waiver_reason", "waived_by", "waived_at", "resolved_at"}},
        5: {
            "projection_jobs": {"projection_key", "task_id", "artifact_id", "projection_type", "export_path", "required", "status", "attempts", "expected_digest", "materialized_digest", "last_error", "lease_owner", "lease_expires_at", "created_at", "updated_at", "materialized_at"},
        },
        6: {
            "prune_tombstones": {"tombstone_id", "task_id", "artifact_dir", "status", "lease_owner", "lease_expires_at", "error", "created_at", "updated_at", "filesystem_removed_at", "finalized_at"},
        },
        7: {
            "artifact_blobs": {"blob_id", "digest_sha256", "mime_type", "byte_size", "chunk_count", "encoding", "created_at"},
            "artifact_blob_chunks": {"blob_id", "chunk_no", "text_content", "blob_content", "byte_size", "digest_sha256"},
            "logical_artifacts": {"artifact_id", "task_id", "kind", "title", "mime_type", "digest_sha256", "byte_size", "chunk_count", "immutable", "blob_id", "export_path", "created_at"},
            "artifact_exports": {"artifact_id", "task_id", "export_path", "created_at"},
        },
        8: {
            "task_revisions": {"task_id", "task_revision", "base_revision", "source", "message_original", "message_language", "message_en", "translation_status", "created_at"},
            "plan_revisions": {"task_id", "plan_revision", "base_plan_revision", "task_revision", "impact_json", "plan_json", "status", "created_at"},
            "worker_sessions": {"session_id", "task_id", "attempt_id", "host_agent_id", "host_task_name", "host_tool", "generation", "status", "resumable", "started_at", "last_seen_at", "terminated_at"},
            "attempt_messages": {"message_id", "task_id", "attempt_id", "source", "kind", "original_text", "original_language", "canonical_en", "task_revision", "created_at", "delivered_at", "acknowledged_at"},
            "question_batches": {"batch_id", "task_id", "attempt_id", "batch_key", "status", "language", "created_at", "answered_at"},
            "question_items": {"batch_id", "question_key", "question_type", "canonical_question", "localized_question", "options_json", "ordinal"},
            "question_answers": {"batch_id", "question_key", "answer_original", "answer_original_language", "answer_option_ids_json", "answer_en", "translation_status", "translated_by", "translated_at"},
            "orchestration_trace": {"trace_id", "task_id", "attempt_id", "event", "occurred_at", "metadata_json"},
            "tool_observations": {"observation_id", "task_id", "attempt_id", "context_epoch", "fingerprint", "tool_name", "normalized_arguments", "workspace_generation", "result_digest", "coverage", "status", "first_seen_at", "last_seen_at", "repeat_count"},
        },
        9: {
            "initiatives": {"initiative_ref", "parent_ref", "title", "goal", "owner", "risk", "acceptance_oracle_artifact_ref", "status", "revision", "created_at", "updated_at"},
            "initiative_task_links": {"initiative_ref", "task_id", "relationship", "milestone", "deliverable", "corrective", "expected_revision", "created_at"},
            "initiative_dependencies": {"dependency_ref", "source_type", "source_ref", "target_type", "target_ref", "dependency_type", "created_at"},
            "governance_records": {"record_ref", "initiative_ref", "task_id", "record_type", "revision", "supersedes", "status", "content_json", "content_digest", "content_artifact_ref", "approval_basis_json", "created_by", "created_at", "expires_at"},
            "governance_links": {"link_ref", "record_ref", "initiative_ref", "task_id", "lane_id", "finding_fingerprint", "evidence_ref", "relationship", "created_at"},
        },
        10: {
            "governance_records": {"scope_key"},
            "governance_submissions": {"submission_id", "command_digest", "record_ref", "created_at"},
        },
        11: {
            "governance_records": {"lifecycle_sequence", "lifecycle_binding"},
            "governance_record_lifecycle": {"lifecycle_ref", "record_ref", "lifecycle_sequence", "previous_binding", "status", "approval_basis_json", "binding", "action", "actor_role", "created_at"},
        },
        12: {
            "governance_record_lifecycle_auth": {"lifecycle_ref", "envelope_hmac"},
        },
        13: {
            "attempt_results": {
                "result_ref", "task_id", "attempt_id", "result_status", "lifecycle_status", "summary",
                "findings_json", "decisions_needed_json", "unresolved_json", "claims_json", "metadata_json",
                "workspace_observation_json", "changed_files_json", "changed_files_status", "content_digest",
                "submission_id", "work_completed_at", "finalizing_at", "completed_at", "created_at", "updated_at",
            },
            "attempt_events": {
                "event_ref", "task_id", "attempt_id", "event_key", "sequence", "event_type", "payload_json",
                "actor", "occurred_at", "created_at",
            },
        },
        15: {
            "attempt_events": {
                "event_ref", "task_id", "attempt_id", "event_key", "sequence", "event_type", "payload_json",
                "actor", "occurred_at", "created_at",
            },
        },
    }
    for table, expected_columns in column_requirements.get(version, {}).items():
        columns = {str(row[0]) for row in connection.execute("SELECT name FROM pragma_table_info(?)", (table,))}
        if not expected_columns.issubset(columns):
            raise ValueError("Cortex database schema is inconsistent with migration history")



def _applied_migrations(connection: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    return {
        int(row["version"]): (str(row["name"]), str(row["checksum"]))
        for row in connection.execute("SELECT version, name, checksum FROM schema_migrations")
    }


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
    for migration in _migration_plan():
        known = applied.get(migration.version)
        checksum = _migration_checksum(migration)
        if known != (migration.name, checksum):
            raise ValueError("Cortex database requires migration before this nested operation")


def _migration_plan_fingerprint(migrations: tuple[_Migration, ...]) -> str:
    """Bind a warm-read cache entry to this exact immutable migration plan."""
    payload = [
        (migration.version, migration.name, _migration_checksum(migration))
        for migration in migrations
    ]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _migration_history_fingerprint(connection: sqlite3.Connection) -> str:
    """Return a compact fingerprint of the authority checked on every warm open."""
    rows = [
        (int(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        )
    ]
    return hashlib.sha256(_canonical_json(rows).encode("utf-8")).hexdigest()


def _read_database_readiness(
    root: Path,
    *,
    plan_fingerprint: str,
) -> _DatabaseReadiness | None:
    """Read the minimal authority needed to reuse one ready schema safely.

    This intentionally opens SQLite read-only: it does not acquire the
    migration advisory lock, issue ``BEGIN IMMEDIATE``, run DDL, or walk the
    full required-object schema.  It does, however, check the three mutation
    signals the ready cache relies on: SQLite's user/schema markers and the
    ordered migration history.  The filesystem identity is sampled on both
    sides of the SQLite read so a concurrent replacement becomes a miss.
    """
    before = _database_file_identity(root)
    if before is None:
        return None
    path = database_path(root).resolve()
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            timeout=15,
            isolation_level=None,
        )
    except sqlite3.Error:
        return None
    try:
        connection.execute("PRAGMA query_only = ON")
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        history_fingerprint = _migration_history_fingerprint(connection)
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    after = _database_file_identity(root)
    if after is None or after != before:
        return None
    return _DatabaseReadiness(
        identity=after,
        user_version=user_version,
        schema_version=schema_version,
        history_fingerprint=history_fingerprint,
        plan_fingerprint=plan_fingerprint,
    )


def _forget_database_readiness(root: Path) -> None:
    with _DATABASE_READINESS_GUARD:
        _DATABASE_READINESS.pop(_root_key(root), None)


def _cache_database_readiness(root: Path, migrations: tuple[_Migration, ...]) -> None:
    """Remember a just-validated steady-state schema, if it is still stable.

    The slow migration path is authoritative.  If the database changes after
    it releases its transaction, this helper simply declines to populate the
    optimization; the next caller runs the normal fail-closed path again.
    """
    plan_fingerprint = _migration_plan_fingerprint(migrations)
    readiness = _read_database_readiness(root, plan_fingerprint=plan_fingerprint)
    if readiness is None:
        _forget_database_readiness(root)
        return
    expected_history = hashlib.sha256(_canonical_json([
        (migration.version, migration.name, _migration_checksum(migration))
        for migration in migrations
    ]).encode("utf-8")).hexdigest()
    if (
        readiness.history_fingerprint != expected_history
        or readiness.user_version != (migrations[-1].version if migrations else 0)
    ):
        _forget_database_readiness(root)
        return
    key = _root_key(root)
    with _DATABASE_READINESS_GUARD:
        _DATABASE_READINESS[key] = readiness
        _DATABASE_READINESS.move_to_end(key)
        while len(_DATABASE_READINESS) > _DATABASE_READINESS_CACHE_LIMIT:
            _DATABASE_READINESS.popitem(last=False)


def _database_readiness_is_current(root: Path, migrations: tuple[_Migration, ...]) -> bool:
    """Return whether a read-only probe proves the warm schema is unchanged."""
    key = _root_key(root)
    with _DATABASE_READINESS_GUARD:
        cached = _DATABASE_READINESS.get(key)
        if cached is not None:
            _DATABASE_READINESS.move_to_end(key)
    if cached is None:
        return False
    if cached.plan_fingerprint != _migration_plan_fingerprint(migrations):
        _forget_database_readiness(root)
        return False
    observed = _read_database_readiness(root, plan_fingerprint=cached.plan_fingerprint)
    if observed == cached:
        return True
    _forget_database_readiness(root)
    return False


def ensure_database(root: Path) -> None:
    """Open/upgrade one project ledger and apply each migration exactly once."""
    if _root_key(root) in _active_connections():
        _assert_current_migration_history(_active_connections()[_root_key(root)])
        return
    migrations = _migration_plan()
    if _database_readiness_is_current(root, migrations):
        return
    with _migration_lock(root):
        with _connection(root, write=True) as connection:
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            has_history = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone() is not None
            applied = _applied_migrations(connection) if has_history else {}
            expected_versions = {migration.version for migration in migrations}
            if any(version not in expected_versions for version in applied):
                raise ValueError("Cortex database migration history is inconsistent")
            ordered_versions = sorted(applied)
            if ordered_versions != list(range(1, len(ordered_versions) + 1)):
                raise ValueError("Cortex database migration history is inconsistent")
            if user_version != max(applied, default=0):
                raise ValueError("Cortex database user_version is inconsistent")
            for version in applied:
                _assert_migration_schema(connection, version)
            for migration in migrations:
                known = applied.get(migration.version)
                checksum = _migration_checksum(migration)
                if known is not None:
                    if known == (migration.name, checksum):
                        continue
                    # Databases from the pre-atomic release used a prior-release
                    # name-only checksum. Upgrade it only after confirming
                    # the migration's known schema is actually present.
                    if known == (migration.name, _migration_checksum(migration.name)):
                        _assert_migration_schema(connection, migration.version)
                        connection.execute(
                            "UPDATE schema_migrations SET checksum = ? WHERE version = ?",
                            (checksum, migration.version),
                        )
                        continue
                    raise ValueError("Cortex database migration history is inconsistent")
                if migration.version != (max(applied, default=0) + 1):
                    raise ValueError("Cortex database migration history is inconsistent")
                if migration.version == 10 and max(applied, default=0) == 9:
                    _prepare_v9_governance_integrity_upgrade(connection)
                _execute_migration_statements(connection, migration.statements)
                if migration.version == 11:
                    _finalize_governance_lifecycle_migration(connection)
                if migration.version == 12:
                    _finalize_governance_lifecycle_envelope_auth_migration(root, connection)
                _record_migration(connection, migration)
                applied[migration.version] = (migration.name, checksum)
            # Keep SQLite's schema marker coupled to the immutable plan that
            # was actually validated/applied.  This matters both for an
            # interrupted upgrade and for deterministic migration tests that
            # intentionally stop at an earlier released plan.
            connection.execute(f"PRAGMA user_version = {migrations[-1].version if migrations else 0}")
    _cache_database_readiness(root, migrations)


def migration_history(root: Path) -> list[dict[str, Any]]:
    ensure_database(root)
    with _connection(root) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT version, name, applied_at, checksum FROM schema_migrations ORDER BY version"
        )]


def upsert_task_finding(root: Path, task_id: str, finding: dict[str, Any], *, source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge one closure finding, keyed by task and stable fingerprint."""
    ensure_database(root)
    fingerprint = str(finding["fingerprint"])
    source = source or {}
    severity_rank = {"info": 0, "P3": 1, "P2": 2, "P1": 3, "P0": 4}
    with _connection(root, write=True) as connection:
        row = connection.execute("SELECT * FROM task_findings WHERE task_id=? AND fingerprint=?", (task_id, fingerprint)).fetchone()
        now = _now()
        evidence = []
        if row:
            try: evidence = json.loads(str(row["source_evidence_json"]))
            except json.JSONDecodeError: evidence = []
        if source and source not in evidence: evidence.append(source)
        if row:
            status = str(finding["status"])
            if str(row["status"]) == "open" and status == "open":
                # While a finding remains open, repeated reports may only
                # retain or increase its severity/blocking state. Explicit
                # resolved/waived reports are lifecycle transitions and keep
                # their existing metadata contract below.
                severity = max(
                    (str(row["severity"]), str(finding["severity"])),
                    key=lambda value: severity_rank[value],
                )
                blocking = bool(row["blocking"]) or bool(finding["blocking"])
            else:
                severity = finding["severity"]
                blocking = bool(finding["blocking"])
            connection.execute("UPDATE task_findings SET severity=?, status=?, blocking=?, summary=?, details=?, next_action_json=?, source_evidence_json=?, waiver_reason=?, waived_by=?, waived_at=?, resolved_at=?, updated_at=? WHERE task_id=? AND fingerprint=?", (severity, status, int(blocking), finding["summary"], _canonical_json(finding.get("details")) if isinstance(finding.get("details"), (dict, list)) else finding.get("details"), None, _canonical_json(evidence), finding.get("waiver_reason"), finding.get("waived_by"), finding.get("waived_at"), finding.get("resolved_at"), now, task_id, fingerprint))
        else:
            connection.execute("INSERT INTO task_findings(task_id,fingerprint,severity,status,blocking,summary,details,next_action_json,source_evidence_json,waiver_reason,waived_by,waived_at,resolved_at,first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (task_id, fingerprint, finding["severity"], finding["status"], int(finding["blocking"]), finding["summary"], _canonical_json(finding.get("details")) if isinstance(finding.get("details"), (dict, list)) else finding.get("details"), None, _canonical_json(evidence), finding.get("waiver_reason"), finding.get("waived_by"), finding.get("waived_at"), finding.get("resolved_at"), now, now))
    return finding | {"task_id": task_id, "source_evidence": evidence}


def list_task_findings(root: Path, task_id: str, *, include_resolved: bool = True) -> list[dict[str, Any]]:
    ensure_database(root)
    query = "SELECT * FROM task_findings WHERE task_id=?" + ("" if include_resolved else " AND status != 'resolved'") + " ORDER BY fingerprint"
    with _connection(root) as connection:
        rows = connection.execute(query, (task_id,)).fetchall()
    result = []
    for row in rows:
        item = {"task_id": task_id, "fingerprint": row["fingerprint"], "severity": row["severity"], "status": row["status"], "blocking": bool(row["blocking"]), "summary": row["summary"], "details": row["details"]}
        for field in ("waiver_reason", "waived_by", "waived_at", "resolved_at"):
            if field in row.keys() and row[field] is not None:
                item[field] = row[field]
        item["source_evidence"] = json.loads(row["source_evidence_json"])
        item["first_seen_at"] = row["first_seen_at"]; item["updated_at"] = row["updated_at"]
        result.append(item)
    return result


def task_findings_blockers(root: Path, task_id: str) -> list[dict[str, Any]]:
    """Return only open findings that are authoritative transition blockers.

    P0/P1 are intrinsically blocking.  P2 is advisory unless the authoritative
    finding explicitly declares ``blocking=true``; treating every open P2 as
    a hard rework requirement made ordinary tracked risk indistinguishable
    from a closure blocker.
    """
    return [
        item
        for item in list_task_findings(root, task_id, include_resolved=True)
        if item.get("status") == "open"
        and (item["severity"] in {"P0", "P1"} or item["blocking"])
    ]


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
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_revisions'"
        ).fetchone() is not None:
            original = str(definition.get("user_request") or "")
            language = str(definition.get("user_language") or "en")
            connection.execute(
                """INSERT INTO task_revisions(task_id, task_revision, base_revision, source, message_original, message_language, message_en, translation_status, created_at)
                   VALUES (?, 1, NULL, 'initial', ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    original,
                    language,
                    original if language.lower().startswith("en") else None,
                    "not_required" if language.lower().startswith("en") else "pending",
                    str(definition.get("created_at") or _now()),
                ),
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
        has_revisions = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='plan_revisions'"
        ).fetchone() is not None
        existing = connection.execute(
            "SELECT 1 FROM plan_revisions WHERE task_id = ? LIMIT 1", (task_id,)
        ).fetchone() if has_revisions else None
        if has_revisions and existing is None:
            task_revision = connection.execute(
                "SELECT COALESCE(MAX(task_revision), 1) FROM task_revisions WHERE task_id = ?", (task_id,)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO plan_revisions(task_id, plan_revision, base_plan_revision, task_revision, impact_json, plan_json, status, created_at)
                   VALUES (?, 1, NULL, ?, ?, ?, 'active', ?)""",
                (task_id, int(task_revision), _canonical_json({"classification": "initial"}), _canonical_json(plan), _now()),
            )


def append_task_revision(
    root: Path,
    task_id: str,
    *,
    source: str,
    message_original: str,
    message_language: str,
    message_en: str | None,
) -> dict[str, Any]:
    """Append one immutable task revision and return its canonical row."""
    ensure_database(root)
    if source not in {"initial", "user_steer", "recovery", "system"}:
        raise ValueError("task revision source is invalid")
    original = str(message_original).strip()
    language = str(message_language or "en").strip().lower()
    canonical = str(message_en or "").strip() or None
    if not original:
        raise ValueError("task revision message_original is required")
    with _connection(root, write=True) as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(task_revision), 0) AS value FROM task_revisions WHERE task_id = ?", (task_id,)
        ).fetchone()
        next_revision = int(row["value"]) + 1
        if next_revision == 1:
            task = connection.execute("SELECT definition_json, created_at FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("task revision refers to an unknown task")
            definition = _decode_json(str(task["definition_json"]), "task definition")
            initial = str(definition.get("user_request") or "")
            initial_language = str(definition.get("user_language") or "en").lower()
            connection.execute(
                """INSERT INTO task_revisions(task_id, task_revision, base_revision, source, message_original, message_language, message_en, translation_status, created_at)
                   VALUES (?, 1, NULL, 'initial', ?, ?, ?, ?, ?)""",
                (
                    task_id, initial, initial_language,
                    initial if initial_language.startswith("en") else None,
                    "not_required" if initial_language.startswith("en") else "pending",
                    str(task["created_at"]),
                ),
            )
            next_revision = 2
        created_at = _now()
        connection.execute(
            """INSERT INTO task_revisions(task_id, task_revision, base_revision, source, message_original, message_language, message_en, translation_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, next_revision, next_revision - 1, source, original, language, canonical,
                "translated" if canonical and not language.startswith("en") else "not_required" if language.startswith("en") else "pending",
                created_at,
            ),
        )
    return {
        "task_id": task_id, "task_revision": next_revision, "base_revision": next_revision - 1,
        "source": source, "message_original": original, "message_language": language,
        "message_en": canonical, "created_at": created_at,
    }


def append_plan_revision(
    root: Path,
    task_id: str,
    *,
    task_revision: int,
    impact: dict[str, Any],
    plan: dict[str, Any] | None,
    status: str = "active",
) -> dict[str, Any]:
    ensure_database(root)
    if status not in {"active", "superseded", "approved", "pending"}:
        raise ValueError("plan revision status is invalid")
    with _connection(root, write=True) as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(plan_revision), 0) AS value FROM plan_revisions WHERE task_id = ?", (task_id,)
        ).fetchone()
        next_revision = int(row["value"]) + 1
        connection.execute("UPDATE plan_revisions SET status = 'superseded' WHERE task_id = ? AND status = 'active'", (task_id,))
        created_at = _now()
        connection.execute(
            """INSERT INTO plan_revisions(task_id, plan_revision, base_plan_revision, task_revision, impact_json, plan_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, next_revision, next_revision - 1 if next_revision > 1 else None, int(task_revision),
                _canonical_json(impact), _canonical_json(plan) if plan is not None else None, status, created_at,
            ),
        )
    return {"task_id": task_id, "plan_revision": next_revision, "task_revision": task_revision, "impact": impact, "status": status, "created_at": created_at}


def put_worker_session(root: Path, session: dict[str, Any]) -> dict[str, Any]:
    ensure_database(root)
    task_id = str(session.get("task_id") or "")
    attempt_id = str(session.get("attempt_id") or "")
    generation = int(session.get("generation") or 1)
    session_id = str(session.get("session_id") or f"session-{task_id}-{attempt_id}-{generation}")
    status = str(session.get("status") or "running")
    if status not in {"awaiting_spawn", "running", "idle_resumable", "stopped_recoverable", "terminated_unavailable", "completed"}:
        raise ValueError("worker session status is invalid")
    timestamp = _now()
    with _connection(root, write=True) as connection:
        connection.execute(
            """INSERT INTO worker_sessions(session_id,task_id,attempt_id,host_agent_id,host_task_name,host_tool,generation,status,resumable,started_at,last_seen_at,terminated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET host_agent_id=excluded.host_agent_id, host_task_name=excluded.host_task_name,
                 host_tool=excluded.host_tool, status=excluded.status, resumable=excluded.resumable, last_seen_at=excluded.last_seen_at,
                 terminated_at=excluded.terminated_at""",
            (
                session_id, task_id, attempt_id, session.get("host_agent_id"), str(session.get("host_task_name") or ""),
                str(session.get("host_tool") or "spawn_agent"), generation, status, int(bool(session.get("resumable", True))),
                session.get("started_at") or timestamp, timestamp, session.get("terminated_at"),
            ),
        )
    return {**session, "session_id": session_id, "status": status, "last_seen_at": timestamp}


def list_worker_sessions(root: Path, task_id: str) -> list[dict[str, Any]]:
    """Read server-owned native worker identities for one task.

    Compaction recovery must be able to rehydrate a native child even when an
    older task projection does not carry the nested ``host_spawn`` object.
    ``worker_sessions`` is the canonical server-observed identity table, so
    this read is deliberately scoped to one exact task and returns no other
    host/session metadata.
    """
    ensure_database(root)
    with _connection(root) as connection:
        rows = connection.execute(
            """SELECT session_id, task_id, attempt_id, host_agent_id, host_task_name,
                      host_tool, generation, status, resumable, started_at,
                      last_seen_at, terminated_at
               FROM worker_sessions
               WHERE task_id = ?
               ORDER BY attempt_id, generation DESC, last_seen_at DESC""",
            (str(task_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def append_attempt_message(root: Path, message: dict[str, Any]) -> dict[str, Any]:
    ensure_database(root)
    created_at = _now()
    message_id = str(message.get("message_id") or "message-" + secrets.token_hex(12))
    with _connection(root, write=True) as connection:
        connection.execute(
            """INSERT INTO attempt_messages(message_id,task_id,attempt_id,source,kind,original_text,original_language,canonical_en,task_revision,created_at,delivered_at,acknowledged_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                message_id, message["task_id"], message["attempt_id"], message.get("source", "coordinator"),
                message["kind"], message["original_text"], message.get("original_language", "en"),
                message["canonical_en"], int(message["task_revision"]), created_at,
                message.get("delivered_at"), message.get("acknowledged_at"),
            ),
        )
    return {**message, "message_id": message_id, "created_at": created_at}


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


def delete_task_document(root: Path, task_id: str, document_key: str) -> bool:
    """Delete one exact mutable task document without widening task scope."""
    if not task_id or not document_key or len(document_key) > 160:
        raise ValueError("SQLite task document identity is invalid")
    with _connection(root, write=True) as connection:
        cursor = connection.execute(
            "DELETE FROM task_documents WHERE task_id = ? AND document_key = ?",
            (task_id, document_key),
        )
    return bool(cursor.rowcount)


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


def _register_artifact_export_with_connection(
    connection: sqlite3.Connection,
    task_id: str,
    artifact_ref: str,
    export_path: str,
    *,
    created_at: str | None = None,
) -> None:
    _validate_artifact_identity(task_id, "artifact", "export", export_path)
    artifact = connection.execute(
        "SELECT task_id FROM logical_artifacts WHERE artifact_id = ?", (artifact_ref,)
    ).fetchone()
    if artifact is None or str(artifact["task_id"]) != task_id:
        raise ValueError("SQLite artifact is unavailable for the selected task")
    conflict = connection.execute(
        "SELECT artifact_id FROM artifact_exports WHERE task_id = ? AND export_path = ?",
        (task_id, export_path),
    ).fetchone()
    if conflict is not None and str(conflict["artifact_id"]) != artifact_ref:
        raise ValueError("SQLite artifact export path already belongs to another logical artifact")
    connection.execute(
        "INSERT OR IGNORE INTO artifact_exports(artifact_id, task_id, export_path, created_at) VALUES (?, ?, ?, ?)",
        (artifact_ref, task_id, export_path, created_at or _now()),
    )


def register_artifact_export(root: Path, task_id: str, artifact_ref: str, export_path: str) -> dict[str, Any]:
    """Register another filesystem projection for one immutable artifact.

    This changes only logical projection metadata; a projection worker must
    still materialize and acknowledge the requested path through the outbox.
    """
    ensure_database(root)
    with _connection(root, write=True) as connection:
        _register_artifact_export_with_connection(connection, task_id, artifact_ref, export_path)
    metadata = get_artifact_metadata(root, task_id, artifact_ref)
    if metadata is None:  # Defensive: the transaction above validated it.
        raise ValueError("SQLite artifact is unavailable for the selected task")
    return metadata


def list_artifact_exports(root: Path, task_id: str, artifact_ref: str) -> list[dict[str, str]]:
    """List stable logical export records for later projection scheduling."""
    ensure_database(root)
    with _connection(root) as connection:
        rows = connection.execute(
            "SELECT export_path, created_at FROM artifact_exports WHERE task_id = ? AND artifact_id = ? ORDER BY export_path",
            (task_id, artifact_ref),
        ).fetchall()
    return [{"export_path": str(row["export_path"]), "created_at": str(row["created_at"])} for row in rows]


def get_artifact_blob_metadata(root: Path, task_id: str, artifact_ref: str) -> dict[str, Any] | None:
    """Return the canonical blob identity behind a logical artifact."""
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            """SELECT b.blob_id, b.digest_sha256, b.mime_type, b.byte_size, b.chunk_count, b.encoding, b.created_at
               FROM logical_artifacts a JOIN artifact_blobs b ON b.blob_id = a.blob_id
               WHERE a.task_id = ? AND a.artifact_id = ?""",
            (task_id, artifact_ref),
        ).fetchone()
    if row is None:
        return None
    return {
        "blob_ref": str(row["blob_id"]), "digest_sha256": str(row["digest_sha256"]),
        "mime_type": str(row["mime_type"]), "byte_size": int(row["byte_size"]),
        "chunk_count": int(row["chunk_count"]), "encoding": str(row["encoding"]),
        "created_at": str(row["created_at"]),
    }


def get_artifact_metadata(root: Path, task_id: str, artifact_ref: str) -> dict[str, Any] | None:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT artifact_id, task_id, kind, title, mime_type, digest_sha256, byte_size, chunk_count, immutable, export_path, created_at "
            "FROM logical_artifacts WHERE task_id = ? AND artifact_id = ?",
            (task_id, artifact_ref),
        ).fetchone()
    return None if row is None else _artifact_metadata_row(row)


def get_artifact_for_export_path(root: Path, task_id: str, export_path: str) -> dict[str, Any] | None:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT a.artifact_id, a.task_id, a.kind, a.title, a.mime_type, a.digest_sha256, a.byte_size, a.chunk_count, a.immutable, e.export_path, a.created_at "
            "FROM artifact_exports e JOIN logical_artifacts a ON a.artifact_id = e.artifact_id "
            "WHERE e.task_id = ? AND e.export_path = ? ORDER BY a.created_at DESC, a.artifact_id DESC LIMIT 1",
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
        "FROM logical_artifacts WHERE task_id = ?"
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
            """SELECT c.chunk_no, c.text_content, c.blob_content, c.byte_size, c.digest_sha256
               FROM logical_artifacts a JOIN artifact_blob_chunks c ON c.blob_id = a.blob_id
               WHERE a.task_id = ? AND a.artifact_id = ? ORDER BY c.chunk_no""",
            (task_id, artifact_ref),
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
            """SELECT c.chunk_no, c.text_content, c.blob_content, c.byte_size, c.digest_sha256
               FROM logical_artifacts a JOIN artifact_blob_chunks c ON c.blob_id = a.blob_id
               WHERE a.task_id = ? AND a.artifact_id = ? ORDER BY c.chunk_no""",
            (task_id, artifact_ref),
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


# ---------------------------------------------------------------------------
# Projection outbox and crash-safe prune state machines

def _lease_until(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds)))).isoformat()


def _projection_row(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def enqueue_projection_job(
    root: Path, *, task_id: str | None, projection_type: str, expected_digest: str,
    export_path: str | None = None, artifact_id: str | None = None,
    required: bool = False, projection_key: str | None = None,
) -> dict[str, Any]:
    """Insert or return one deterministic outbox entry (filesystem-free)."""
    if not projection_type or not expected_digest:
        raise ValueError("projection type and expected digest are required")
    key = projection_key or hashlib.sha256(_canonical_json({
        "task_id": task_id, "projection_type": projection_type,
        "export_path": export_path, "artifact_id": artifact_id,
    }).encode()).hexdigest()
    now = _now()
    ensure_database(root)
    with _connection(root, write=True) as connection:
        connection.execute(
            "INSERT INTO projection_jobs(projection_key,task_id,artifact_id,projection_type,export_path,required,expected_digest,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(projection_key) DO NOTHING",
            (key, task_id, artifact_id, projection_type, export_path, int(required), expected_digest, now, now),
        )
        row = connection.execute("SELECT * FROM projection_jobs WHERE projection_key=?", (key,)).fetchone()
        if row is None or str(row["expected_digest"]) != expected_digest:
            raise ValueError("projection key already has conflicting content")
        return _projection_row(row)


def get_projection_job(root: Path, projection_key: str) -> dict[str, Any] | None:
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute("SELECT * FROM projection_jobs WHERE projection_key=?", (projection_key,)).fetchone()
    return None if row is None else _projection_row(row)


def list_projection_jobs(root: Path, *, task_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    ensure_database(root)
    if not 1 <= limit <= 1000: raise ValueError("projection page size is invalid")
    clauses, values = [], []
    if task_id is not None: clauses.append("task_id=?"); values.append(task_id)
    if status is not None: clauses.append("status=?"); values.append(status)
    query = "SELECT * FROM projection_jobs" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY created_at, projection_key LIMIT ?"
    values.append(limit)
    with _connection(root) as connection: rows = connection.execute(query, values).fetchall()
    return [_projection_row(row) for row in rows]


def claim_projection_jobs(root: Path, worker_id: str, *, limit: int = 1, lease_seconds: int = 300) -> list[dict[str, Any]]:
    ensure_database(root)
    now, expiry = _now(), _lease_until(lease_seconds)
    with _connection(root, write=True) as connection:
        rows = connection.execute("SELECT projection_key FROM projection_jobs WHERE (status IN ('pending','failed') OR (status='materializing' AND (lease_expires_at IS NULL OR lease_expires_at < ?))) ORDER BY created_at, projection_key LIMIT ?", (now, limit)).fetchall()
        result = []
        for row in rows:
            connection.execute("UPDATE projection_jobs SET status='materializing', lease_owner=?, lease_expires_at=?, attempts=attempts+1, updated_at=? WHERE projection_key=?", (worker_id, expiry, now, row[0]))
            current = connection.execute("SELECT * FROM projection_jobs WHERE projection_key=?", (row[0],)).fetchone()
            result.append(_projection_row(current))
    return result


def claim_projection_job(
    root: Path,
    projection_key: str,
    worker_id: str,
    *,
    lease_seconds: int = 300,
) -> dict[str, Any] | None:
    """Atomically lease exactly one pending, failed, or expired job.

    This is intentionally separate from :func:`claim_projection_jobs`.
    On-demand materialization must never lease unrelated outbox work merely
    because it happens to be pending in the same database transaction.
    """
    if not str(projection_key).strip() or not str(worker_id).strip():
        raise ValueError("projection key and worker id are required")
    ensure_database(root)
    now, expiry = _now(), _lease_until(lease_seconds)
    with _connection(root, write=True) as connection:
        cursor = connection.execute(
            "UPDATE projection_jobs SET status='materializing', lease_owner=?, "
            "lease_expires_at=?, attempts=attempts+1, updated_at=? "
            "WHERE projection_key=? AND (status IN ('pending','failed') "
            "OR (status='materializing' AND (lease_expires_at IS NULL OR lease_expires_at < ?)))",
            (worker_id, expiry, now, projection_key, now),
        )
        if cursor.rowcount != 1:
            return None
        row = connection.execute(
            "SELECT * FROM projection_jobs WHERE projection_key=?", (projection_key,)
        ).fetchone()
    return None if row is None else _projection_row(row)


def ack_projection_job(
    root: Path,
    projection_key: str,
    *,
    expected_digest: str,
    lease_owner: str,
    materialized_digest: str | None = None,
) -> dict[str, Any]:
    """Mark a projection ready only for its current, live lease holder.

    A materializer can finish after a competing worker has reclaimed an
    expired lease.  Checking the owner and expiry in the same write
    transaction makes that stale worker unable to acknowledge its bytes.
    """
    if not str(lease_owner).strip():
        raise ValueError("projection acknowledgement requires a lease owner")
    ensure_database(root)
    digest = materialized_digest or expected_digest
    with _connection(root, write=True) as connection:
        row = connection.execute("SELECT * FROM projection_jobs WHERE projection_key=?", (projection_key,)).fetchone()
        if row is None: raise ValueError("projection job is unavailable")
        if str(row["expected_digest"]) != expected_digest or digest != expected_digest: raise ValueError("projection digest mismatch")
        if row["status"] == "ready": return _projection_row(row)
        lease_expires_at = str(row["lease_expires_at"] or "")
        if (
            row["status"] != "materializing"
            or str(row["lease_owner"] or "") != str(lease_owner)
            or not lease_expires_at
            or lease_expires_at <= _now()
        ):
            raise ValueError("projection acknowledgement requires the caller's non-expired lease")
        cursor = connection.execute(
            "UPDATE projection_jobs SET status='ready', materialized_digest=?, materialized_at=?, "
            "lease_owner=NULL, lease_expires_at=NULL, last_error=NULL, updated_at=? "
            "WHERE projection_key=? AND status='materializing' AND lease_owner=? AND lease_expires_at > ?",
            (digest, _now(), _now(), projection_key, str(lease_owner), _now()),
        )
        if cursor.rowcount != 1:
            raise ValueError("projection acknowledgement lost its lease")
        return _projection_row(connection.execute("SELECT * FROM projection_jobs WHERE projection_key=?", (projection_key,)).fetchone())


def fail_projection_job(root: Path, projection_key: str, error: str) -> dict[str, Any]:
    ensure_database(root)
    with _connection(root, write=True) as connection:
        cur = connection.execute("UPDATE projection_jobs SET status='failed', last_error=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE projection_key=? AND status != 'ready'", (str(error)[:2000], _now(), projection_key))
        if not cur.rowcount: raise ValueError("projection job is unavailable or already ready")
        return _projection_row(connection.execute("SELECT * FROM projection_jobs WHERE projection_key=?", (projection_key,)).fetchone())


def retry_projection_job(root: Path, projection_key: str) -> dict[str, Any]:
    return _projection_retry(root, projection_key)


def _projection_retry(root: Path, key: str) -> dict[str, Any]:
    ensure_database(root)
    with _connection(root, write=True) as connection:
        connection.execute("UPDATE projection_jobs SET status='pending', lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE projection_key=? AND status IN ('failed','materializing')", (_now(), key))
        row = connection.execute("SELECT * FROM projection_jobs WHERE projection_key=?", (key,)).fetchone()
    if row is None: raise ValueError("projection job is unavailable")
    return _projection_row(row)


def reclaim_projection_jobs(root: Path) -> int:
    ensure_database(root)
    with _connection(root, write=True) as connection:
        cur = connection.execute("UPDATE projection_jobs SET status='pending', lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE status='materializing' AND lease_expires_at < ?", (_now(), _now()))
    return cur.rowcount


def plan_prune(root: Path, task_ids: set[str] | list[str]) -> list[dict[str, Any]]:
    ensure_database(root)
    result = []
    with _connection(root, write=True) as connection:
        for task_id in sorted(set(task_ids)):
            row = connection.execute("SELECT artifact_dir FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None: continue
            tombstone_id = "prune-" + hashlib.sha256(task_id.encode()).hexdigest()[:32]
            now = _now()
            connection.execute("INSERT INTO prune_tombstones(tombstone_id,task_id,artifact_dir,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(tombstone_id) DO NOTHING", (tombstone_id, task_id, row[0], now, now))
            result.append(dict(connection.execute("SELECT * FROM prune_tombstones WHERE tombstone_id=?", (tombstone_id,)).fetchone()))
    return result


def list_prune_tombstones(root: Path, *, status: str | None = None, task_id: str | None = None) -> list[dict[str, Any]]:
    ensure_database(root)
    clauses, values = [], []
    if status is not None: clauses.append("status=?"); values.append(status)
    if task_id is not None: clauses.append("task_id=?"); values.append(task_id)
    query = "SELECT * FROM prune_tombstones" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY created_at, tombstone_id"
    with _connection(root) as connection: rows = connection.execute(query, values).fetchall()
    return [dict(row) for row in rows]


def claim_prune_tombstone(
    root: Path,
    worker_id: str,
    *,
    tombstone_id: str | None = None,
    lease_seconds: int = 300,
) -> dict[str, Any] | None:
    """Claim one planned or failed prune tombstone for filesystem work."""
    ensure_database(root)
    now, expiry = _now(), _lease_until(lease_seconds)
    with _connection(root, write=True) as connection:
        clauses = ["(status IN ('planned','failed') OR (status='claimed' AND (lease_expires_at IS NULL OR lease_expires_at < ?)))"]
        values: list[Any] = [now]
        if tombstone_id is not None:
            clauses.append("tombstone_id=?")
            values.append(tombstone_id)
        row = connection.execute(
            "SELECT tombstone_id FROM prune_tombstones WHERE " + " AND ".join(clauses) + " ORDER BY created_at LIMIT 1",
            tuple(values),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            "UPDATE prune_tombstones SET status='claimed',lease_owner=?,lease_expires_at=?,error=NULL,updated_at=? WHERE tombstone_id=?",
            (worker_id, expiry, now, row[0]),
        )
        return dict(connection.execute("SELECT * FROM prune_tombstones WHERE tombstone_id=?", (row[0],)).fetchone())


def mark_prune_filesystem_removed(root: Path, tombstone_id: str, *, lease_owner: str) -> dict[str, Any]:
    """Acknowledge filesystem deletion for the exact live tombstone lease."""
    if not str(lease_owner).strip():
        raise ValueError("prune filesystem acknowledgement requires a lease owner")
    ensure_database(root)
    with _connection(root, write=True) as connection:
        stamp = _now()
        cursor = connection.execute(
            "UPDATE prune_tombstones SET status='filesystem_removed',filesystem_removed_at=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? "
            "WHERE tombstone_id=? AND status='claimed' AND lease_owner=? AND lease_expires_at > ?",
            (stamp, stamp, tombstone_id, lease_owner, stamp),
        )
        if cursor.rowcount != 1:
            raise ValueError("prune filesystem acknowledgement lost its lease")
        row = connection.execute("SELECT * FROM prune_tombstones WHERE tombstone_id=?", (tombstone_id,)).fetchone()
    return dict(row)


def _task_prune_references(connection: sqlite3.Connection, task_ids: set[str]) -> tuple[set[str], set[str]]:
    """Collect snapshot/classification references owned by the selected tasks."""
    snapshots: set[str] = set()
    classifications: set[str] = set()
    if not task_ids:
        return snapshots, classifications
    placeholders = ",".join("?" for _ in task_ids)
    for row in connection.execute(
        f"SELECT definition_json, state_json FROM tasks WHERE task_id IN ({placeholders})", tuple(sorted(task_ids))
    ):
        definition = _decode_json(str(row["definition_json"]), "task definition")
        state = _decode_json(str(row["state_json"]), "task state")
        snapshots.add(str(state.get("initial_manifest_ref") or ""))
        snapshots.update(
            str(item.get("result_baseline_ref") or "")
            for item in state.get("attempts", []) if isinstance(item, dict)
        )
        classifications.update({
            str(definition.get("classification_id") or ""),
            str(state.get("classification_receipt") or ""),
        })
    snapshots.discard("")
    classifications.discard("")
    return snapshots, classifications


def finalize_prunes(
    root: Path,
    tombstone_ids: set[str] | list[str],
    *,
    global_updates: dict[str, dict[str, Any] | None],
    lane_updates: list[tuple[dict[str, Any], dict[str, Any], str, str]],
) -> dict[str, Any]:
    """Atomically remove canonical task metadata after acknowledged deletion.

    The caller performs filesystem deletion without the process state lock,
    acknowledges each tombstone, then reacquires that lock to derive the
    current global and lane documents passed here.  This transaction is the
    only point at which canonical task rows and their associated metadata are
    removed.
    """
    ids = sorted({str(value) for value in tombstone_ids if str(value)})
    if not ids:
        return {"finalized": [], "removed_operations": 0, "removed_classifications": 0, "removed_manifest_snapshots": 0}
    ensure_database(root)
    with _connection(root, write=True) as connection:
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"SELECT * FROM prune_tombstones WHERE tombstone_id IN ({placeholders}) ORDER BY tombstone_id", tuple(ids)
        ).fetchall()
        if len(rows) != len(ids):
            raise ValueError("prune tombstone is unavailable")
        unfinished = [row for row in rows if row["status"] not in ("filesystem_removed", "finalized")]
        if unfinished:
            raise ValueError("filesystem removal must be acknowledged before finalize")
        active = [row for row in rows if row["status"] == "filesystem_removed"]
        task_ids = {str(row["task_id"]) for row in active}
        snapshots, classifications = _task_prune_references(connection, task_ids)

        # Preserve evidence shared by any task not in this atomic batch.
        remaining_snapshots, remaining_classifications = _task_prune_references(
            connection,
            {
                str(row["task_id"])
                for row in connection.execute("SELECT task_id FROM tasks")
                if str(row["task_id"]) not in task_ids
            },
        )
        removable_snapshots = sorted(snapshots - remaining_snapshots)
        removable_classifications = sorted(classifications - remaining_classifications)
        if task_ids:
            task_placeholders = ",".join("?" for _ in task_ids)
            removed_operations = connection.execute(
                f"DELETE FROM operations WHERE task_id IN ({task_placeholders})", tuple(sorted(task_ids))
            ).rowcount
            connection.execute(f"DELETE FROM projection_jobs WHERE task_id IN ({task_placeholders})", tuple(sorted(task_ids)))
        else:
            removed_operations = 0
        if removable_snapshots:
            snapshot_placeholders = ",".join("?" for _ in removable_snapshots)
            removed_snapshots = connection.execute(
                f"DELETE FROM manifest_snapshots WHERE snapshot_ref IN ({snapshot_placeholders})", tuple(removable_snapshots)
            ).rowcount
        else:
            removed_snapshots = 0
        if removable_classifications:
            classification_placeholders = ",".join("?" for _ in removable_classifications)
            removed_classifications = connection.execute(
                f"DELETE FROM classifications WHERE classification_id IN ({classification_placeholders})", tuple(removable_classifications)
            ).rowcount
        else:
            removed_classifications = 0
        for name, value in global_updates.items():
            if value is None:
                connection.execute("DELETE FROM global_documents WHERE name=?", (name,))
            else:
                connection.execute(
                    "INSERT INTO global_documents(name, payload_json, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                    (name, _canonical_json(value), _now()),
                )
        for definition, state, event, detail in lane_updates:
            lane_id = str(state.get("lane_id") or definition.get("lane_id") or "")
            if not lane_id:
                raise ValueError("lane has no lane_id")
            connection.execute(
                "UPDATE lanes SET definition_json=?,state_json=?,status=?,revision=?,updated_at=? WHERE lane_id=?",
                (_canonical_json(definition), _canonical_json(state), str(state.get("status") or "active"),
                 int(state.get("revision") or 0), str(state.get("updated_at") or _now()), lane_id),
            )
            connection.execute(
                "INSERT INTO ledger_events(task_id,lane_id,event,detail,revision,created_at) VALUES(NULL,?,?,?,?,?)",
                (lane_id, event, detail, int(state.get("revision") or 0), _now()),
            )
        if task_ids:
            connection.execute(f"DELETE FROM tasks WHERE task_id IN ({task_placeholders})", tuple(sorted(task_ids)))
        stamp = _now()
        connection.execute(
            f"UPDATE prune_tombstones SET status='finalized',finalized_at=?,updated_at=? "
            f"WHERE tombstone_id IN ({placeholders}) AND status='filesystem_removed'",
            (stamp, stamp, *ids),
        )
        finalized = [dict(row) for row in connection.execute(
            f"SELECT * FROM prune_tombstones WHERE tombstone_id IN ({placeholders}) ORDER BY tombstone_id", tuple(ids)
        )]
    return {
        "finalized": finalized,
        "removed_operations": int(removed_operations),
        "removed_classifications": int(removed_classifications),
        "removed_manifest_snapshots": int(removed_snapshots),
    }


def fail_prune(root: Path, tombstone_id: str, error: str) -> dict[str, Any]:
    ensure_database(root)
    with _connection(root, write=True) as connection:
        connection.execute("UPDATE prune_tombstones SET status='failed',error=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE tombstone_id=? AND status != 'finalized'", (str(error)[:2000], _now(), tombstone_id))
        row = connection.execute("SELECT * FROM prune_tombstones WHERE tombstone_id=?", (tombstone_id,)).fetchone()
    if row is None: raise ValueError("prune tombstone is unavailable")
    return dict(row)


# Tool observations are intentionally narrow hook telemetry.  They retain
# neither raw tool inputs nor response text: callers supply already-redacted
# digests and bounded argument summaries.
def tool_context_epoch(root: Path, task_id: str, *, bump: bool = False) -> int:
    """Return a task-scoped durable tool context epoch, optionally rolling it."""
    ensure_database(root)
    with _connection(root, write=bump) as connection:
        rows = connection.execute(
            "SELECT metadata_json FROM orchestration_trace WHERE task_id=? AND event='tool_context_epoch' ORDER BY trace_id DESC",
            (task_id,),
        ).fetchall()
        epoch = 0
        for row in rows:
            try:
                candidate = json.loads(str(row["metadata_json"])).get("epoch")
            except (json.JSONDecodeError, AttributeError):
                continue
            if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
                epoch = candidate
                break
        if bump:
            epoch += 1
            connection.execute(
                "INSERT INTO orchestration_trace(task_id,attempt_id,event,occurred_at,metadata_json) VALUES(?,?,?,?,?)",
                (task_id, None, "tool_context_epoch", _now(), _canonical_json({"epoch": epoch})),
            )
    return epoch


def find_successful_tool_observation(
    root: Path,
    task_id: str,
    attempt_id: str,
    context_epoch: int,
    fingerprint: str,
    workspace_generation: str,
) -> bool:
    """Return whether an identical full-file read completed successfully."""
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT 1 FROM tool_observations WHERE task_id=? AND attempt_id=? AND context_epoch=? "
            "AND fingerprint=? AND workspace_generation=? AND coverage='full' AND status='success'",
            (task_id, attempt_id, context_epoch, fingerprint, workspace_generation),
        ).fetchone()
    return row is not None


def hook_find_successful_tool_observation(
    root: Path,
    task_id: str,
    attempt_id: str,
    context_epoch: int,
    fingerprint: str,
    workspace_generation: str,
) -> bool:
    """Fail-open read-only dedupe lookup for lifecycle hooks."""
    try:
        with hook_snapshot(root) as snapshot:
            return bool(snapshot and hook_snapshot_find_successful_tool_observation(
                snapshot, task_id, attempt_id, context_epoch, fingerprint, workspace_generation,
            ))
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _hook_metric("hook_snapshot_miss")
        return False


def mark_tool_observation_duplicate(
    root: Path, task_id: str, attempt_id: str, context_epoch: int, fingerprint: str,
) -> bool:
    """Count a denied duplicate without replacing the successful observation."""
    ensure_database(root)
    with _connection(root, write=True) as connection:
        cursor = connection.execute(
            "UPDATE tool_observations SET repeat_count=repeat_count+1,last_seen_at=? WHERE task_id=? AND attempt_id=? "
            "AND context_epoch=? AND fingerprint=? AND coverage='full' AND status='success'",
            (_now(), task_id, attempt_id, context_epoch, fingerprint),
        )
    return cursor.rowcount == 1


def hook_mark_tool_observation_duplicate(
    root: Path, task_id: str, attempt_id: str, context_epoch: int, fingerprint: str,
) -> bool:
    """Bound the optional hook telemetry write and fail open on contention."""
    try:
        with _hook_write_connection(root) as connection:
            cursor = connection.execute(
                "UPDATE tool_observations SET repeat_count=repeat_count+1,last_seen_at=? WHERE task_id=? AND attempt_id=? "
                "AND context_epoch=? AND fingerprint=? AND coverage='full' AND status='success'",
                (_now(), task_id, attempt_id, context_epoch, fingerprint),
            )
            return cursor.rowcount == 1
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _hook_metric("telemetry_failure")
        return False


def record_tool_observation(
    root: Path,
    *,
    task_id: str,
    attempt_id: str,
    context_epoch: int,
    fingerprint: str,
    tool_name: str,
    normalized_arguments: str,
    workspace_generation: str,
    result_digest: str | None,
    coverage: str,
    status: str,
) -> None:
    """Upsert one bounded hook observation; a later success supersedes failure."""
    if not task_id or not attempt_id or context_epoch < 0:
        raise ValueError("tool observation identity is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("tool observation fingerprint is invalid")
    if len(normalized_arguments.encode("utf-8")) > 2048:
        raise ValueError("tool observation arguments are too large")
    try:
        parsed_arguments = json.loads(normalized_arguments)
    except json.JSONDecodeError as exc:
        raise ValueError("tool observation arguments are invalid") from exc
    if not isinstance(parsed_arguments, dict):
        raise ValueError("tool observation arguments are invalid")
    if status not in {"success", "failed"} or coverage not in {"full", "noncacheable"}:
        raise ValueError("tool observation status is invalid")
    if result_digest is not None and not re.fullmatch(r"[0-9a-f]{64}", result_digest):
        raise ValueError("tool observation result digest is invalid")
    observation_id = "tool-" + hashlib.sha256(
        f"{task_id}\0{attempt_id}\0{context_epoch}\0{fingerprint}".encode("utf-8")
    ).hexdigest()[:48]
    ensure_database(root)
    now = _now()
    with _connection(root, write=True) as connection:
        connection.execute(
            "INSERT INTO tool_observations(observation_id,task_id,attempt_id,context_epoch,fingerprint,tool_name,normalized_arguments,"
            "workspace_generation,result_digest,coverage,status,first_seen_at,last_seen_at,repeat_count) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0) "
            "ON CONFLICT(task_id,attempt_id,context_epoch,fingerprint) DO UPDATE SET "
            "tool_name=excluded.tool_name,normalized_arguments=excluded.normalized_arguments,workspace_generation=excluded.workspace_generation,"
            "result_digest=excluded.result_digest,coverage=excluded.coverage,status=excluded.status,last_seen_at=excluded.last_seen_at",
            (observation_id, task_id, attempt_id, context_epoch, fingerprint, tool_name, normalized_arguments,
             workspace_generation, result_digest, coverage, status, now, now),
        )


@contextlib.contextmanager
def _hook_write_connection(root: Path) -> Iterator[sqlite3.Connection]:
    """Open an existing ledger for at-most-100ms optional telemetry writes."""
    identity = _database_file_identity(root)
    if identity is None:
        raise FileNotFoundError("Cortex database is unavailable")
    path = database_path(root)
    connection = sqlite3.connect(str(path), timeout=0.1, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 100")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != DATABASE_SCHEMA_VERSION:
            raise ValueError("Cortex database schema is incompatible")
        connection.execute("BEGIN IMMEDIATE")
        yield connection
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        raise
    else:
        connection.commit()
    finally:
        connection.close()


def hook_record_tool_observation(
    root: Path,
    *,
    task_id: str,
    attempt_id: str,
    context_epoch: int,
    fingerprint: str,
    tool_name: str,
    normalized_arguments: str,
    workspace_generation: str,
    result_digest: str | None,
    coverage: str,
    status: str,
) -> bool:
    """Persist one sanitized observation without migration/state-lock work."""
    try:
        if not task_id or not attempt_id or context_epoch < 0:
            raise ValueError("tool observation identity is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("tool observation fingerprint is invalid")
        if len(normalized_arguments.encode("utf-8")) > 2048:
            raise ValueError("tool observation arguments are too large")
        parsed_arguments = json.loads(normalized_arguments)
        if not isinstance(parsed_arguments, dict):
            raise ValueError("tool observation arguments are invalid")
        if status not in {"success", "failed"} or coverage not in {"full", "noncacheable"}:
            raise ValueError("tool observation status is invalid")
        if result_digest is not None and not re.fullmatch(r"[0-9a-f]{64}", result_digest):
            raise ValueError("tool observation result digest is invalid")
        observation_id = "tool-" + hashlib.sha256(
            f"{task_id}\0{attempt_id}\0{context_epoch}\0{fingerprint}".encode("utf-8")
        ).hexdigest()[:48]
        stamp = _now()
        with _hook_write_connection(root) as connection:
            connection.execute(
                "INSERT INTO tool_observations(observation_id,task_id,attempt_id,context_epoch,fingerprint,tool_name,normalized_arguments,"
                "workspace_generation,result_digest,coverage,status,first_seen_at,last_seen_at,repeat_count) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0) "
                "ON CONFLICT(task_id,attempt_id,context_epoch,fingerprint) DO UPDATE SET "
                "tool_name=excluded.tool_name,normalized_arguments=excluded.normalized_arguments,workspace_generation=excluded.workspace_generation,"
                "result_digest=excluded.result_digest,coverage=excluded.coverage,status=excluded.status,last_seen_at=excluded.last_seen_at",
                (observation_id, task_id, attempt_id, context_epoch, fingerprint, tool_name, normalized_arguments,
                 workspace_generation, result_digest, coverage, status, stamp, stamp),
            )
        return True
    except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
        _hook_metric("telemetry_failure")
        return False
