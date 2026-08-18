"""Explicit SQLite health and controlled-maintenance operations.

This module deliberately opens the ledger directly instead of using the
normal ``ledger_db`` connection helpers.  A health inspection must not create
or migrate a database merely because somebody asked whether it is healthy.
All writes are opt-in management operations and use SQLite's own primitives;
in particular, backups never copy a live database file.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import tempfile
from pathlib import Path
from typing import Any

from cortex_runtime.ledger_db import DATABASE_NAME, DATABASE_SCHEMA_VERSION


_BACKUP_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.sqlite")
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
        raise ValueError("backup_name must be a safe .sqlite filename")
    directory = _backup_directory(root, create=not require_existing)
    target = directory / name
    if target.parent != directory:
        raise ValueError("backup target escapes Cortex backups")
    if require_existing:
        return _private_file(target, "SQLite backup")
    if target.exists() or target.is_symlink():
        raise ValueError("SQLite backup target already exists")
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


def create_backup(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _require_confirmation("backup", payload)
    source, target = _database(root), _backup_target(root, payload)
    _backup_connection(source, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {"operation": "backup", "backup_name": target.name, "bytes": int(target.stat().st_size), "sha256": digest}


def verify_backup_restore(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Restore a backup via SQLite into a disposable DB, then inspect it."""
    source = _backup_target(root, payload, require_existing=True)
    with tempfile.TemporaryDirectory(prefix=".restore-check-", dir=_backup_directory(root, create=False)) as temporary:
        restored = Path(temporary) / "restored.sqlite"
        _backup_connection(source, restored)
        with _readonly_connection(restored) as connection:
            quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
            foreign_key_violations = _read_rows(connection, "PRAGMA foreign_key_check")
            schema = _schema_status(connection)
    return {
        "operation": "verify_backup_restore",
        "backup_name": source.name,
        "restored_with_sqlite_backup_api": True,
        "quick_check": {"ok": quick_check == ["ok"], "rows": quick_check},
        "foreign_key_check": {"ok": not foreign_key_violations, "violations": foreign_key_violations},
        "schema": schema,
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
