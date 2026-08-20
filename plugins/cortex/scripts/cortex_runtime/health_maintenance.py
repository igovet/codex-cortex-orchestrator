"""Explicit SQLite health and controlled-maintenance operations.

This module deliberately opens the ledger directly instead of using the
normal ``ledger_db`` connection helpers.  A health inspection must not create
or migrate a database merely because somebody asked whether it is healthy.
All writes are opt-in management operations and use SQLite's own primitives;
in particular, backups never copy a live database file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex_runtime import ledger_db
from cortex_runtime.ledger_db import DATABASE_NAME, DATABASE_SCHEMA_VERSION

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None


# A backup is deliberately a directory rather than a bare SQLite file: v12
# governance authenticity also depends on a host-private key which SQLite
# alone cannot recover.  A distinct suffix prevents old DB-only snapshots
# from being mistaken for a recoverable Cortex backup.
_BACKUP_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.cortex-backup")
_BACKUP_MANIFEST_NAME = "manifest.json"
_BACKUP_DATABASE_NAME = DATABASE_NAME
_BACKUP_GOVERNANCE_KEY_NAME = "governance-lifecycle.key"
_BACKUP_SCHEMA = "cortex/ledger-backup/v1"
_WRITE_CONFIRMATIONS = {
    "checkpoint": "CHECKPOINT",
    "backup": "BACKUP",
    "optimize": "OPTIMIZE",
    "vacuum": "VACUUM",
    "reconcile_projections": "RECONCILE",
}


def _private_directory(path: Path, label: str, *, create: bool = False) -> Path:
    """Validate one real private directory without following symlinks."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise ValueError(f"{label} is unavailable")
        path.mkdir(parents=True, mode=0o700)
        info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"{label} must be a real directory")
    if os.name != "nt" and hasattr(os, "getuid") and int(info.st_uid) != int(os.getuid()):
        raise ValueError(f"{label} must be owned by the current user")
    if info.st_mode & 0o077:
        path.chmod(0o700)
    return path


def _private_file(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    if os.name != "nt" and hasattr(os, "getuid") and int(info.st_uid) != int(os.getuid()):
        raise ValueError(f"{label} must be owned by the current user")
    if info.st_mode & 0o077:
        raise ValueError(f"{label} has unsafe permissions")
    return path


def _database(root: Path) -> Path:
    _private_directory(root, "Cortex root")
    return _private_file(root / DATABASE_NAME, "Cortex database")


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection


def _read_rows(connection: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query)]


def _wal_status(path: Path) -> dict[str, Any]:
    wal_path = path.with_name(path.name + "-wal")
    try:
        info = wal_path.lstat()
    except FileNotFoundError:
        return {"path": wal_path.name, "present": False, "bytes": 0}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("SQLite WAL must be a regular non-symlink file")
    if info.st_mode & 0o077:
        raise ValueError("SQLite WAL has unsafe permissions")
    return {"path": wal_path.name, "present": True, "bytes": int(info.st_size)}


def _state_lock_status(root: Path) -> dict[str, Any]:
    """Probe the project lock without waiting, creating, or mutating it.

    This is deliberately separate from the normal mutation boundary.  It only
    tells callers whether the project-wide coordination lock is currently
    observable as held; it does not identify a holder and does not infer a
    logical task blocker from a busy lock.
    """
    lock_path = root / ".state.lock"
    base = {"path": lock_path.name, "scope": "project", "probe": "nonblocking"}
    try:
        info = lock_path.lstat()
    except FileNotFoundError:
        return {**base, "state": "not_present", "held": False}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return {**base, "state": "invalid", "held": None}
    if fcntl is None:
        return {**base, "state": "unsupported", "held": None}
    try:
        with lock_path.open("r", encoding="utf-8") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {**base, "state": "busy", "held": True}
            finally:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    except OSError as exc:
        return {**base, "state": "unavailable", "held": None, "error": type(exc).__name__}
    return {**base, "state": "free", "held": False}


def _schema_status(connection: sqlite3.Connection) -> dict[str, Any]:
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    try:
        history = _read_rows(
            connection,
            "SELECT version, name, applied_at, checksum FROM schema_migrations ORDER BY version",
        )
    except sqlite3.OperationalError as exc:
        raise ValueError("SQLite migration history is unavailable") from exc
    versions = [int(item["version"]) for item in history]
    contiguous = versions == list(range(1, len(versions) + 1))
    current = versions[-1] if versions else 0
    return {
        "expected_version": DATABASE_SCHEMA_VERSION,
        "user_version": user_version,
        "migration_versions": versions,
        "history": history,
        "contiguous": contiguous,
        "current": current == DATABASE_SCHEMA_VERSION and user_version == DATABASE_SCHEMA_VERSION and contiguous,
    }


def _projection_status(connection: sqlite3.Connection) -> dict[str, Any]:
    try:
        rows = _read_rows(
            connection,
            "SELECT status, COUNT(*) AS count FROM projection_jobs GROUP BY status ORDER BY status",
        )
        recent = _read_rows(
            connection,
            "SELECT projection_key, task_id, projection_type, export_path, status, attempts, last_error "
            "FROM projection_jobs WHERE status IN ('pending', 'failed', 'materializing') "
            "ORDER BY updated_at, projection_key LIMIT 100",
        )
    except sqlite3.OperationalError:
        return {"available": False, "counts": {}, "actionable": []}
    counts = {str(item["status"]): int(item["count"]) for item in rows}
    return {"available": True, "counts": counts, "actionable": recent}


def inspect_health(root: Path) -> dict[str, Any]:
    """Return a strictly read-only integrity snapshot for an existing ledger."""
    db_path = _database(root)
    with _readonly_connection(db_path) as connection:
        quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
        foreign_key_violations = _read_rows(connection, "PRAGMA foreign_key_check")
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        schema = _schema_status(connection)
        projections = _projection_status(connection)
    return {
        "operation": "health",
        "availability": {
            "lock": _state_lock_status(root),
            "meaning": "Physical project lock state only; task/gate blockers are reported by orchestration inspection.",
        },
        "database": {"name": db_path.name, "bytes": int(db_path.stat().st_size), "journal_mode": journal_mode},
        "quick_check": {"ok": quick_check == ["ok"], "rows": quick_check},
        "foreign_key_check": {"ok": not foreign_key_violations, "violations": foreign_key_violations},
        "schema": schema,
        "projections": projections,
        "wal": _wal_status(db_path),
        # A checkpoint pragma can flush pages, so inspection never invokes it.
        "checkpoint": {"performed": False, "reason": "inspection is read-only"},
    }


def _require_confirmation(action: str, payload: dict[str, Any]) -> None:
    expected = _WRITE_CONFIRMATIONS[action]
    if str(payload.get("confirmation") or "") != expected:
        raise ValueError(f"{action} requires confirmation='{expected}'")


def _backup_directory(root: Path, *, create: bool) -> Path:
    path = root / "backups"
    return _private_directory(path, "Cortex backup directory", create=create)


def _backup_target(root: Path, payload: dict[str, Any], *, require_existing: bool = False) -> Path:
    name = str(payload.get("backup_name") or "")
    if not _BACKUP_NAME_RE.fullmatch(name):
        raise ValueError("backup_name must be a safe .cortex-backup bundle name")
    directory = _backup_directory(root, create=not require_existing)
    target = directory / name
    if target.parent != directory:
        raise ValueError("backup target escapes Cortex backups")
    if require_existing:
        return _private_directory(target, "Cortex backup bundle")
    if target.exists() or target.is_symlink():
        raise ValueError("Cortex backup bundle target already exists")
    return target


def _backup_connection(source: Path, target: Path) -> None:
    """Make a consistent copy through SQLite's backup API, never file copy."""
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    destination: sqlite3.Connection | None = None
    try:
        with _readonly_connection(source) as source_connection:
            destination = sqlite3.connect(str(target), timeout=15)
            source_connection.backup(destination)
            destination.commit()
        target.chmod(0o600)
    except BaseException:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        if destination is not None:
            destination.close()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_private_bytes(path: Path, value: bytes, label: str) -> None:
    """Create one private regular file and persist it before publication."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    _private_file(path, label)


def _fsync_directory(path: Path) -> None:
    """Persist a directory entry where the platform permits it."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _bundle_manifest(*, database: Path, governance_key: Path) -> dict[str, Any]:
    return {
        "schema": _BACKUP_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": {
            "name": _BACKUP_DATABASE_NAME,
            "bytes": int(database.stat().st_size),
            "sha256": _sha256_file(database),
            "schema_version": DATABASE_SCHEMA_VERSION,
        },
        # This is only a fingerprint.  The key bytes are present exclusively
        # in the 0600 bundle member and never returned in a maintenance result.
        "governance_lifecycle_key": {
            "name": _BACKUP_GOVERNANCE_KEY_NAME,
            "sha256": _sha256_file(governance_key),
        },
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    _write_private_bytes(
        path,
        (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8"),
        "Cortex backup manifest",
    )


def _read_bundle_manifest(bundle: Path) -> dict[str, Any]:
    path = _private_file(bundle / _BACKUP_MANIFEST_NAME, "Cortex backup manifest")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Cortex backup manifest is invalid") from exc
    if not isinstance(value, dict) or value.get("schema") != _BACKUP_SCHEMA:
        raise ValueError("Cortex backup manifest schema is invalid")
    database = value.get("database")
    key = value.get("governance_lifecycle_key")
    if not isinstance(database, dict) or not isinstance(key, dict):
        raise ValueError("Cortex backup manifest is incomplete")
    if database.get("name") != _BACKUP_DATABASE_NAME or key.get("name") != _BACKUP_GOVERNANCE_KEY_NAME:
        raise ValueError("Cortex backup manifest members are invalid")
    for item in (database, key):
        if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])):
            raise ValueError("Cortex backup manifest fingerprint is invalid")
    if not isinstance(database.get("bytes"), int) or database["bytes"] < 0:
        raise ValueError("Cortex backup manifest database size is invalid")
    return value


def _bundle_members(bundle: Path, manifest: dict[str, Any]) -> tuple[Path, Path]:
    database = _private_file(bundle / _BACKUP_DATABASE_NAME, "Cortex backup database")
    governance_key = _private_file(bundle / _BACKUP_GOVERNANCE_KEY_NAME, "Cortex backup governance key")
    expected_database = manifest["database"]
    expected_key = manifest["governance_lifecycle_key"]
    if int(database.stat().st_size) != int(expected_database["bytes"]):
        raise ValueError("Cortex backup database size does not match manifest")
    if _sha256_file(database) != expected_database["sha256"]:
        raise ValueError("Cortex backup database fingerprint does not match manifest")
    if _sha256_file(governance_key) != expected_key["sha256"]:
        raise ValueError("Cortex backup governance key fingerprint does not match manifest")
    return database, governance_key


def _governance_records_verified(root: Path) -> int:
    """Read every record through the real v12 validation layer."""
    from cortex_runtime import governance

    offset, total = 0, 0
    while True:
        page = governance.list_records(root, limit=256, offset=offset)
        total += len(page)
        if len(page) < 256:
            return total
        offset += len(page)


def create_backup(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _require_confirmation("backup", payload)
    source, target = _database(root), _backup_target(root, payload)
    # Use the ledger helper rather than merely opening the file: it also
    # checks current-user ownership, expected key length, and fail-closed key
    # availability semantics.
    key_material = ledger_db._governance_lifecycle_hmac_key(root, create=False)
    directory = _backup_directory(root, create=True)
    temporary = Path(tempfile.mkdtemp(prefix=".creating-", dir=directory))
    try:
        temporary.chmod(0o700)
        database = temporary / _BACKUP_DATABASE_NAME
        copied_key = temporary / _BACKUP_GOVERNANCE_KEY_NAME
        _backup_connection(source, database)
        _write_private_bytes(copied_key, key_material, "Cortex backup governance key")
        manifest = _bundle_manifest(database=database, governance_key=copied_key)
        _write_manifest(temporary / _BACKUP_MANIFEST_NAME, manifest)
        _fsync_directory(temporary)
        os.replace(temporary, target)
        _fsync_directory(directory)
    except BaseException:
        # The private temporary directory never becomes a valid bundle and is
        # safe to remove only because it was created by this call underneath a
        # validated backup directory.
        if temporary.exists():
            for member in temporary.iterdir():
                member.unlink()
            temporary.rmdir()
        raise
    return {
        "operation": "backup",
        "backup_name": target.name,
        "bundle_schema": _BACKUP_SCHEMA,
        "database_bytes": int(manifest["database"]["bytes"]),
        "database_sha256": manifest["database"]["sha256"],
        "governance_lifecycle_key_fingerprint": manifest["governance_lifecycle_key"]["sha256"],
    }


def verify_backup_restore(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Verify a portable bundle in a fresh host/root through governance v12."""
    bundle = _backup_target(root, payload, require_existing=True)
    manifest = _read_bundle_manifest(bundle)
    source, source_key = _bundle_members(bundle, manifest)
    with tempfile.TemporaryDirectory(prefix=".restore-check-", dir=_backup_directory(root, create=False)) as temporary:
        # Deliberately choose a fresh host-private layout.  This proves the
        # key is rehomed by the restore contract instead of accidentally using
        # the source host's sidecar path.
        # Keep the opaque ledger ID construction explicit and stable for
        # ledger_db's host-key lookup.
        restored_root = Path(temporary) / "fresh-host" / "projects" / ("p-" + "0" * 64)
        restored_root.mkdir(parents=True, mode=0o700)
        restored = restored_root / DATABASE_NAME
        _backup_connection(source, restored)
        restored_key = ledger_db._governance_lifecycle_key_path(restored_root)
        restored_key.parent.mkdir(parents=True, mode=0o700)
        restored_key.parent.chmod(0o700)
        _write_private_bytes(restored_key, source_key.read_bytes(), "Cortex restored governance lifecycle host key")
        with _readonly_connection(restored) as connection:
            quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
            foreign_key_violations = _read_rows(connection, "PRAGMA foreign_key_check")
            schema = _schema_status(connection)
        governance_records_verified = _governance_records_verified(restored_root)
    return {
        "operation": "verify_backup_restore",
        "backup_name": bundle.name,
        "bundle_schema": _BACKUP_SCHEMA,
        "restored_with_sqlite_backup_api": True,
        "quick_check": {"ok": quick_check == ["ok"], "rows": quick_check},
        "foreign_key_check": {"ok": not foreign_key_violations, "violations": foreign_key_violations},
        "schema": schema,
        "governance": {"verified_records": governance_records_verified, "fresh_host_root": True},
    }


def _write_pragma(root: Path, action: str, statement: str, payload: dict[str, Any]) -> dict[str, Any]:
    _require_confirmation(action, payload)
    db_path = _database(root)
    with sqlite3.connect(str(db_path), timeout=15, isolation_level=None) as connection:
        connection.execute("PRAGMA busy_timeout = 15000")
        rows = [tuple(row) for row in connection.execute(statement)]
    return {"operation": action, "result": rows, "database_bytes": int(db_path.stat().st_size)}


def reconcile_projections(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Explicitly route projection repair through the dedicated service.

    Maintenance never writes an export itself.  The projection service claims
    durable jobs and enforces its artifact/path/lease checks before it does.
    """
    _require_confirmation("reconcile_projections", payload)
    limit = payload.get("limit", 100)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("projection reconciliation limit must be an integer from 1 to 100")
    from cortex_runtime.projection_service import reconcile

    processed = reconcile(root, worker_id="maintenance-reconciler", limit=limit)
    task_ids = sorted({str(item.get("task_id") or "") for item in processed if item.get("task_id")})
    return {
        "operation": "reconcile_projections",
        "scheduled": True,
        "delegation": {
            "required": bool(processed),
            "task_ids": task_ids,
            "projection_keys": [str(item["projection_key"]) for item in processed],
            "reason": "projection service owns durable claims and materialization",
        },
        "processed": processed,
    }


def manage_health_maintenance(root: Path, payload: object) -> dict[str, Any]:
    """Route the explicit ``manage_orchestration(intent=maintenance)`` payload."""
    if not isinstance(payload, dict):
        raise ValueError("maintenance management requires payload")
    action = str(payload.get("action") or "health").strip().lower().replace("-", "_")
    aliases = {"inspect": "health", "check": "health", "wal_checkpoint": "checkpoint", "verify_backup": "verify_backup_restore", "reconcile": "reconcile_projections"}
    action = aliases.get(action, action)
    if action == "health":
        return inspect_health(root)
    if action == "checkpoint":
        return _write_pragma(root, action, "PRAGMA wal_checkpoint(TRUNCATE)", payload)
    if action == "backup":
        return create_backup(root, payload)
    if action == "verify_backup_restore":
        return verify_backup_restore(root, payload)
    if action == "optimize":
        return _write_pragma(root, action, "PRAGMA optimize", payload)
    if action == "vacuum":
        return _write_pragma(root, action, "VACUUM", payload)
    if action == "reconcile_projections":
        return reconcile_projections(root, payload)
    raise ValueError("maintenance action is not recognized")
