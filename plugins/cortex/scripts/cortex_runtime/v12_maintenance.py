"""Host-private, task-scoped maintenance for the Cortex V12 ledger.

This is intentionally a local administrator surface, not an MCP tool.  Every
operation starts with a V12 task identifier and derives its only filesystem
targets from the identifier's embedded shard hash.  It never accepts a project
root or arbitrary backup path, and it deliberately knows nothing about V11.

The canonical SQLite ledger is never retained or pruned by this module.
Because one V12 shard can contain multiple tasks, a backup is explicitly a
project-shard backup that is merely anchored to one requested task.  Retention
applies only to completed, manifest-bound maintenance backups.  The separate
projection prune operation removes only derived Markdown files that are
registered for the requested task.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from cortex_runtime.v12_contract import TASK_ID_RE, task_ref, task_shard_hash


_DATABASE_NAME = "cortex.db"
_APPLICATION_ID = 0x43563132
_SCHEMA_VERSION = 1
_MIGRATIONS = (
    (1, "v12-initial"),
    (2, "v12-schema-v1-human-views"),
    (3, "v12-explicit-profile-binding"),
    (4, "v12-durable-native-task-name"),
    (5, "v12-report-consumption-receipts"),
    (6, "v12-durable-governance-gate"),
    (7, "v12-ready-approval-handles"),
    (8, "v12-advisory-governance"),
    (9, "v12-canonical-report-semantics"),
    (10, "v12-effective-outcome-coverage"),
    (11, "v12-report-coverage-diagnostics"),
    (12, "v12-revisioned-outcome-assignments"),
    (13, "v12-persisted-steering-delta"),
    (14, "v14-atomic-report-operations"),
    (15, "v15-durable-clarification-bindings"),
    (16, "v16-transactional-command-receipts"),
    (17, "v17-plan-review-bound-relations"),
    (18, "v18-clarification-holds"),
    (19, "v19-derived-task-locators"),
    (20, "v20-dispatch-correlation-marker"),
    (21, "v21-worker-bootstrap-capabilities"),
    (22, "v22-dispatch-lease-expiry"),
    (23, "v23-immutable-assignment-scope"),
    (24, "v24-outcome-linked-contract"),
    (25, "v25-assignment-page-receipts"),
    (26, "v26-explicit-assignment-loss-lineage"),
)
_BACKUP_FORMAT = "cortex/v12-maintenance-backup/v1"
_BACKUP_ID_PREFIX = "backup-"
_BACKUP_DATABASE_SIDECARS = ("cortex.db-wal", "cortex.db-shm")
_MAX_BACKUP_ID_LENGTH = 112
_MAX_RETENTION_BACKUPS = 20
_MAX_PROJECTION_ENTRIES = 10_000
_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_CHECKPOINT_MODES = frozenset({"PASSIVE", "FULL", "RESTART", "TRUNCATE"})
_REQUIRED_TABLES = frozenset(
    {
        "schema_migrations",
        "v12_metadata",
        "timeline",
        "tasks",
        "delegations",
        "reports",
        "report_chunks",
        "report_operations",
        "report_consumption_receipts",
        "report_usage",
        "governance_assessments",
        "initiatives",
        "initiative_revisions",
        "initiative_links",
        "governance_closures",
        "user_decisions",
        "projection_jobs",
        "projection_files",
        "effective_contract_item_details",
        "idempotency",
        "approval_handles",
        "clarification_bindings",
        "clarification_holds",
        "task_locator_publications",
        "effective_contract_revisions",
        "effective_contract_items",
        "delegation_outcome_assignments",
        "assignment_scope_snapshots",
        "assignment_page_receipts",
        "assignment_losses",
        "report_contract_coverage",
        "command_receipts",
        "worker_capabilities",
    }
)
_REQUIRED_SCHEMA_OBJECTS = frozenset(
    {
        "reports_terminal_no_update",
        "reports_no_delete",
        "report_chunks_no_update",
        "report_chunks_no_delete",
        "decisions_no_update",
        "decisions_no_delete",
        "decisions_task_created",
        "report_chunks_report_order",
        "consumption_task_sequence",
        "consumption_delegation_report",
        "timeline_decision_sequence",
        "projection_jobs_pending",
        "approval_handles_task_report",
        "clarification_bindings_task_pending",
        "clarification_holds_assignment_state",
        "clarification_holds_task_state",
        "task_locator_publications_suffix",
        "outcome_owned_current",
        "outcome_assignment_current",
        "outcome_items_task_current",
        "assignment_scope_task_revision",
        "assignment_scope_no_update",
        "assignment_scope_no_delete",
        "assignment_page_task_sequence",
        "assignment_page_assignment_position",
        "assignment_loss_task_sequence",
        "assignment_loss_no_update",
        "assignment_loss_no_delete",
    }
)
_REQUIRED_COLUMNS = {
    "tasks": frozenset({"task_id", "project_hash", "project_root", "objective", "context_json"}),
    "delegations": frozenset({"delegation_id", "project_hash", "task_id", "native_task_name", "dispatch_correlation_marker", "dispatch_correlation_digest", "input_report_ids_json", "input_decision_ids_json"}),
    "reports": frozenset({"report_id", "project_hash", "task_id", "delegation_id", "assembly_state", "content_digest", "semantic_status", "coverage_diagnostics_json"}),
    "report_chunks": frozenset({"report_id", "chunk_index", "content_json", "content_digest"}),
    "report_consumption_receipts": frozenset({"receipt_id", "project_hash", "task_id", "consumer_delegation_id", "reader_kind", "report_id", "observed_content_digest", "sections_json", "input_cursor", "output_cursor", "chunk_indexes_json", "returned_content_bytes", "has_more", "created_sequence"}),
    "report_usage": frozenset({"task_id", "total_retained_bytes", "assembling_bytes", "assembling_reports"}),
    "timeline": frozenset({"sequence", "task_id", "decision_id", "payload_json"}),
    "user_decisions": frozenset({"decision_id", "task_id", "subject_type", "subject_id", "decision_type", "prompt_en", "response_original", "response_en", "steering_delta_json"}),
    "effective_contract_revisions": frozenset({"task_id", "revision", "decision_id", "created_sequence"}),
    "effective_contract_items": frozenset({"item_id", "project_hash", "task_id", "category", "ordinal", "text", "created_revision", "retired_revision"}),
    "effective_contract_item_details": frozenset({"item_id", "details_json", "source_decision_id"}),
    "delegation_outcome_assignments": frozenset({"delegation_id", "item_id", "assignment_role", "revision", "superseded_by_delegation_id", "superseded_sequence"}),
    "assignment_scope_snapshots": frozenset({"assignment_id", "task_id", "item_id", "assignment_role", "contract_revision", "created_sequence"}),
    "assignment_page_receipts": frozenset({"receipt_id", "project_hash", "task_id", "assignment_id", "snapshot_digest", "phase", "private_position", "page_digest", "returned_content_bytes", "has_more", "created_at", "created_sequence"}),
    "assignment_losses": frozenset({"loss_id", "project_hash", "task_id", "assignment_id", "successor_assignment_id", "terminal_state", "reason", "evidence_json", "evidence_digest", "created_at", "created_sequence"}),
    "report_contract_coverage": frozenset({"report_id", "item_id", "status", "verification_json"}),
    # v17 makes these four fields the authoritative immutable plan-review
    # relation.  Maintenance must reject a shard that claims v17 while any
    # one is absent; it must never repair, infer, or downgrade that relation.
    "clarification_bindings": frozenset({"clarification_binding", "project_hash", "task_id", "subject_type", "subject_id", "decision_type", "prompt_digest", "prompt", "prompt_language", "effective_contract_revision", "issue_sequence", "request_digest", "response_digest", "consumed_decision_id", "plan_content_digest", "plan_approval_handle", "plan_view_content_digest", "plan_view_source_sequence"}),
    "clarification_holds": frozenset({"clarification_binding", "project_hash", "task_id", "assignment_id", "native_dispatch_digest", "continuation_capability", "state", "response_decision_id", "delivery_claim_digest", "opened_sequence", "answered_sequence", "delivery_sequence", "unavailable_reason", "created_at", "updated_at"}),
    "task_locator_publications": frozenset({"task_id", "project_hash", "suffix", "fingerprint", "created_at"}),
    "projection_jobs": frozenset({"job_id", "task_id", "source_sequence", "status"}),
    "projection_files": frozenset({"task_id", "relative_path", "content_digest", "status"}),
    "worker_capabilities": frozenset({"capability_ref", "project_hash", "task_id", "assignment_id", "contract_revision", "build_digest", "candidate_digest", "source_digest", "catalogue_digest", "dispatch_digest", "capability_digest", "continuation_ref", "state", "created_sequence", "consumed_sequence", "created_at", "updated_at", "lease_expires_at"}),
}


def _codex_home() -> Path:
    """Use the explicit Codex state root, with the documented HOME fallback."""
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get("HOME") or str(Path.home())).expanduser() / ".codex"

class V12MaintenanceError(RuntimeError):
    """A bounded error that is safe to render from the local CLI."""

    def __init__(self, *, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _Target:
    task_id: str
    project_hash: str
    root: Path
    database: Path
    projections: Path
    backups: Path


def _failure(code: str) -> V12MaintenanceError:
    return V12MaintenanceError(code=code)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise _failure("maintenance_storage_unavailable") from exc
    return "sha256:" + digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _private_mode(path: Path, expected: int, *, code: str = "maintenance_storage_unsafe") -> None:
    try:
        metadata = os.lstat(path)
        mode = stat.S_IMODE(metadata.st_mode)
    except OSError as exc:
        raise _failure("maintenance_storage_unavailable") from exc
    if mode != expected or metadata.st_uid != os.getuid():
        raise _failure(code)


def _regular(path: Path, *, required: bool = True) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        if required:
            raise _failure("maintenance_storage_unavailable") from None
        return False
    except OSError as exc:
        raise _failure("maintenance_storage_unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise _failure("maintenance_storage_unsafe")
    _private_mode(path, _PRIVATE_FILE_MODE)
    return True


def _directory(path: Path, *, private: bool) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        raise _failure("maintenance_storage_unavailable") from None
    except OSError as exc:
        raise _failure("maintenance_storage_unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _failure("maintenance_storage_unsafe")
    if private:
        _private_mode(path, _PRIVATE_DIRECTORY_MODE)


def _target(task_id: object) -> _Target:
    if not isinstance(task_id, str) or TASK_ID_RE.fullmatch(task_id) is None:
        raise _failure("maintenance_task_id_invalid")
    project_hash = task_shard_hash(task_id)
    compact_ref = task_ref(task_id)
    if project_hash is None or compact_ref is None:
        raise _failure("maintenance_task_id_invalid")
    codex = _codex_home()
    cortex = codex / "cortex"
    v12 = cortex / "v12"
    projects = v12 / "projects"
    root = projects / f"p-{project_hash}"
    # Never resolve these paths: resolving would follow a symlink before the
    # lstat checks below can reject it.
    _directory(codex, private=False)
    _directory(cortex, private=False)
    _directory(v12, private=True)
    _directory(projects, private=True)
    _directory(root, private=True)
    database = root / _DATABASE_NAME
    _regular(database)
    for sidecar in (Path(f"{database}-wal"), Path(f"{database}-shm")):
        _regular(sidecar, required=False)
    return _Target(
        task_id=task_id,
        project_hash=project_hash,
        root=root,
        database=database,
        projections=root / "tasks" / compact_ref,
        backups=root / "backups" / task_id,
    )


def _backup_id(value: object) -> str:
    if not isinstance(value, str) or not value.startswith(_BACKUP_ID_PREFIX):
        raise _failure("maintenance_backup_id_invalid")
    if len(value) > _MAX_BACKUP_ID_LENGTH or len(value) <= len(_BACKUP_ID_PREFIX):
        raise _failure("maintenance_backup_id_invalid")
    suffix = value[len(_BACKUP_ID_PREFIX):]
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in suffix):
        raise _failure("maintenance_backup_id_invalid")
    if suffix.startswith("-") or suffix.endswith("-"):
        raise _failure("maintenance_backup_id_invalid")
    return value


def _new_backup_id() -> str:
    return _BACKUP_ID_PREFIX + uuid.uuid4().hex


def _backup_paths(target: _Target, backup_id: object) -> tuple[str, Path, Path, Path]:
    identifier = _backup_id(backup_id)
    bundle = target.backups / identifier
    database = bundle / "cortex.db"
    manifest = bundle / "manifest.json"
    return identifier, bundle, database, manifest


def _validated_backup_bundle_files(bundle: Path, database: Path, manifest_path: Path) -> tuple[Path, ...]:
    """Return the exact closed allowlist of safe bundle members.

    A sealed manifest binds the independent backup database.  SQLite can still
    leave its owner-private WAL/SHM support files beside that database after a
    local validation or restore operation.  They are not caller-selected
    artifacts, so retention may remove them only when they match this fixed
    allowlist and each one remains a regular owner-private file.  No recursive
    traversal, glob, or arbitrary bundle member is ever accepted.
    """
    _directory(bundle, private=True)
    names = set(os.listdir(bundle))
    required = {database.name, manifest_path.name}
    allowed = required | set(_BACKUP_DATABASE_SIDECARS)
    if not required.issubset(names) or not names.issubset(allowed):
        raise _failure("maintenance_backup_invalid")
    members: list[Path] = []
    # Remove support sidecars before the canonical manifest/database.  Should
    # a sidecar unlink fail, the sealed backup itself remains intact.
    for name in (*_BACKUP_DATABASE_SIDECARS, manifest_path.name, database.name):
        if name in names:
            member = bundle / name
            _regular(member)
            # A non-empty WAL could contain data that is not represented by
            # the sealed database digest in the manifest.  A recovery-created
            # zero-byte WAL is harmless metadata, but anything else makes the
            # bundle unavailable for retention rather than expanding what may
            # be deleted.
            if name == "cortex.db-wal" and _safe_file_size(member) != 0:
                raise _failure("maintenance_backup_invalid")
            members.append(member)
    return tuple(members)


def _remove_validated_backup_bundle(bundle: Path, database: Path, manifest_path: Path) -> None:
    """Remove only an already-validated sealed bundle's fixed members.

    The caller has performed the operator confirmation and exact-ID validation.
    Re-list and re-lstat immediately before unlinking so a local alteration is
    rejected rather than broadened into a recursive cleanup.
    """
    members = _validated_backup_bundle_files(bundle, database, manifest_path)
    try:
        for member in members:
            _regular(member)
            if member.name == "cortex.db-wal" and _safe_file_size(member) != 0:
                raise _failure("maintenance_backup_invalid")
            os.unlink(member)
        _directory(bundle, private=True)
        os.rmdir(bundle)
        _fsync_directory(bundle.parent)
    except V12MaintenanceError:
        raise
    except OSError as exc:
        raise _failure("maintenance_retention_failed") from exc


def _contained(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise _failure("maintenance_storage_unsafe") from exc


def _ensure_private_directory(path: Path, *, root: Path) -> None:
    _contained(path, root)
    relative = path.relative_to(root)
    _directory(root, private=True)
    current = root
    for part in relative.parts:
        current /= part
        try:
            os.mkdir(current, _PRIVATE_DIRECTORY_MODE)
        except FileExistsError:
            pass
        except OSError as exc:
            raise _failure("maintenance_storage_unavailable") from exc
        _directory(current, private=True)


def _create_private_directory(path: Path, *, root: Path) -> None:
    _contained(path, root)
    try:
        os.mkdir(path, _PRIVATE_DIRECTORY_MODE)
    except FileExistsError:
        raise _failure("maintenance_target_exists") from None
    except OSError as exc:
        raise _failure("maintenance_storage_unavailable") from exc
    _directory(path, private=True)


def _create_private_file(path: Path, *, root: Path) -> None:
    _contained(path, root)
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, _PRIVATE_FILE_MODE)
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        os.fsync(descriptor)
    except FileExistsError:
        raise _failure("maintenance_target_exists") from None
    except OSError as exc:
        raise _failure("maintenance_storage_unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _regular(path)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        raise _failure("maintenance_storage_unavailable") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise _failure("maintenance_storage_unavailable") from exc
    finally:
        os.close(descriptor)


def _atomic_private_json(path: Path, value: Mapping[str, Any], *, root: Path) -> None:
    """Write one new maintenance manifest without following a caller path."""
    _contained(path, root)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = -1
    try:
        _regular(path, required=False)
        if path.exists():
            raise _failure("maintenance_target_exists")
        _create_private_file(temporary, root=root)
        descriptor = os.open(temporary, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
        written = 0
        view = memoryview(payload)
        while written < len(view):
            written += os.write(descriptor, view[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _regular(temporary)
        # A hard-link commit refuses to overwrite a path installed by another
        # process after our initial lstat.  os.replace would be atomic but
        # could silently replace an unexpected target.
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        _regular(path)
        _fsync_directory(path.parent)
    except V12MaintenanceError:
        raise
    except OSError as exc:
        raise _failure("maintenance_storage_unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            try:
                _regular(temporary)
                os.unlink(temporary)
            except (OSError, V12MaintenanceError):
                pass


def _readonly_uri(path: Path) -> str:
    return path.as_uri() + "?mode=ro"


@contextmanager
def _connection(target: _Target, *, writable: bool) -> Iterator[sqlite3.Connection]:
    connection: sqlite3.Connection | None = None
    try:
        _regular(target.database)
        if writable:
            connection = sqlite3.connect(target.database, timeout=15, isolation_level=None)
        else:
            connection = sqlite3.connect(_readonly_uri(target.database), uri=True, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        if writable:
            journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
            connection.execute("PRAGMA synchronous = FULL")
            if journal_mode != "wal" or int(connection.execute("PRAGMA synchronous").fetchone()[0]) != 2:
                raise _failure("maintenance_configuration_invalid")
        yield connection
    except V12MaintenanceError:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise _failure("maintenance_storage_unavailable") from exc
    finally:
        if connection is not None:
            connection.close()
        if writable:
            # SQLite may have created sidecars.  Check them immediately rather
            # than accepting a non-regular or non-private target later.
            for sidecar in (Path(f"{target.database}-wal"), Path(f"{target.database}-shm")):
                _regular(sidecar, required=False)


def _schema_health(connection: sqlite3.Connection, target: _Target, *, database: Path) -> dict[str, Any]:
    """Check V12 shape without running migrations or materializing anything."""
    checks = {
        "integrity": False,
        "foreign_keys": False,
        "schema": False,
        "migrations": False,
        "metadata": False,
        "task_binding": False,
        "configuration": False,
    }
    foreign_key_violations = 0
    affected_task_count = 0
    try:
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check(1)")]
        checks["integrity"] = integrity == ["ok"]
        for _row in connection.execute("PRAGMA foreign_key_check"):
            foreign_key_violations += 1
            if foreign_key_violations > 100:
                break
        checks["foreign_keys"] = foreign_key_violations == 0
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        table_names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }
        object_names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('index','trigger')")
        }
        checks["schema"] = (
            application_id == _APPLICATION_ID
            and user_version == _SCHEMA_VERSION
            and _REQUIRED_TABLES.issubset(table_names)
            and _REQUIRED_SCHEMA_OBJECTS.issubset(object_names)
        )
        migrations = tuple(
            (int(row[0]), str(row[1]))
            for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version")
        )
        checks["migrations"] = migrations == _MIGRATIONS
        metadata = {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key,value FROM v12_metadata WHERE key IN ('project_hash','project_root_digest')")
        }
        checks["metadata"] = metadata.get("project_hash") == target.project_hash
        task = connection.execute(
            "SELECT project_root FROM tasks WHERE task_id=? AND project_hash=?",
            (target.task_id, target.project_hash),
        ).fetchone()
        if task is not None and isinstance(task[0], str):
            stored_root = str(task[0])
            try:
                canonical_root = Path(stored_root).resolve(strict=True)
                checks["task_binding"] = (
                    canonical_root.is_dir()
                    and str(canonical_root) == stored_root
                    and hashlib.sha256(stored_root.encode("utf-8")).hexdigest() == target.project_hash
                    and metadata.get("project_root_digest") == hashlib.sha256(stored_root.encode("utf-8")).hexdigest()
                )
            except (OSError, RuntimeError):
                checks["task_binding"] = False
        affected_task_count = min(
            int(connection.execute("SELECT COUNT(*) FROM tasks WHERE project_hash=?", (target.project_hash,)).fetchone()[0]),
            100_000,
        )
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        checks["configuration"] = journal_mode == "wal" and int(connection.execute("PRAGMA synchronous").fetchone()[0]) == 2
        for table, required_columns in _REQUIRED_COLUMNS.items():
            columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
            if not required_columns.issubset(columns):
                checks["schema"] = False
    except sqlite3.DatabaseError:
        pass
    return {
        "healthy": all(checks.values()),
        "checks": checks,
        "foreign_key_violations": min(foreign_key_violations, 101),
        "database_bytes": _safe_file_size(database),
        "affected_task_count": affected_task_count,
    }


def _safe_file_size(path: Path) -> int:
    try:
        return int(os.lstat(path).st_size)
    except OSError as exc:
        raise _failure("maintenance_storage_unavailable") from exc


def _health_target(target: _Target, *, database: Path | None = None) -> dict[str, Any]:
    """Run direct read-only health validation; it never initializes a store."""
    selected = target.database if database is None else database
    _regular(selected)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(_readonly_uri(selected), uri=True, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        try:
            status = _schema_health(connection, target, database=selected)
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
        return status
    except V12MaintenanceError:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise _failure("maintenance_storage_unavailable") from exc
    finally:
        if connection is not None:
            connection.close()


def health(*, task_id: str) -> dict[str, Any]:
    """Read-only integrity, FK, schema, migration, and task-binding health."""
    target = _target(task_id)
    status = _health_target(target)
    return {
        "ok": True,
        "operation": "health",
        "task_id": target.task_id,
        **status,
    }


def _require_healthy(target: _Target) -> dict[str, Any]:
    status = _health_target(target)
    if not status["healthy"]:
        raise _failure("maintenance_precondition_failed")
    return status


def _require_action(value: object, expected: str) -> None:
    if value != expected:
        raise _failure("maintenance_confirmation_required")


def _create_backup(target: _Target, *, backup_id: object | None = None) -> dict[str, Any]:
    """Create one sealed backup bundle using SQLite's online backup API."""
    _require_healthy(target)
    _ensure_private_directory(target.backups, root=target.root)
    identifier = _new_backup_id() if backup_id is None else _backup_id(backup_id)
    identifier, bundle, destination, manifest_path = _backup_paths(target, identifier)
    _create_private_directory(bundle, root=target.root)
    _create_private_file(destination, root=target.root)
    source: sqlite3.Connection | None = None
    backup: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(_readonly_uri(target.database), uri=True, timeout=15, isolation_level=None)
        backup = sqlite3.connect(destination, timeout=15, isolation_level=None)
        source.backup(backup)
        backup.commit()
    except (OSError, sqlite3.DatabaseError) as exc:
        try:
            _regular(destination)
            os.unlink(destination)
            os.rmdir(bundle)
        except (OSError, V12MaintenanceError):
            pass
        raise _failure("maintenance_backup_failed") from exc
    finally:
        if backup is not None:
            backup.close()
        if source is not None:
            source.close()
    _regular(destination)
    validation = _health_target(target, database=destination)
    if not validation["healthy"]:
        try:
            os.unlink(destination)
            os.rmdir(bundle)
        except OSError:
            pass
        raise _failure("maintenance_backup_failed")
    manifest = {
        "format": _BACKUP_FORMAT,
        "state": "complete",
        "backup_scope": "project_shard",
        "backup_id": identifier,
        "anchor_task_id": target.task_id,
        "project_hash": target.project_hash,
        "affected_task_count": validation["affected_task_count"],
        "database_sha256": _sha256_file(destination),
        "database_bytes": _safe_file_size(destination),
        "created_at": _now(),
    }
    try:
        _atomic_private_json(manifest_path, manifest, root=target.root)
        _fsync_directory(bundle.parent)
    except V12MaintenanceError:
        # A database without its manifest is deliberately not restorable or
        # retention-eligible.  Remove only the just-created exact target.
        try:
            _regular(destination)
            os.unlink(destination)
            os.rmdir(bundle)
        except (OSError, V12MaintenanceError):
            pass
        raise
    return {
        "backup_scope": "project_shard",
        "anchor_task_id": target.task_id,
        "affected_task_count": validation["affected_task_count"],
        "backup_id": identifier,
        "database_sha256": manifest["database_sha256"],
        "database_bytes": manifest["database_bytes"],
    }


def backup(*, task_id: str, confirm_action: str, backup_id: str | None = None) -> dict[str, Any]:
    """Create a project-shard backup anchored to one exact V12 task."""
    _require_action(confirm_action, "BACKUP")
    target = _target(task_id)
    result = _create_backup(target, backup_id=backup_id)
    return {"ok": True, "operation": "backup", "task_id": target.task_id, **result}


def _validate_backup(target: _Target, backup_id: object) -> tuple[str, Path, Path, Path, dict[str, Any]]:
    identifier, bundle, database, manifest_path = _backup_paths(target, backup_id)
    _validated_backup_bundle_files(bundle, database, manifest_path)
    try:
        raw = manifest_path.read_bytes()
        if len(raw) > 16 * 1024:
            raise ValueError()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise _failure("maintenance_backup_invalid") from exc
    required = {
        "format",
        "state",
        "backup_id",
        "backup_scope",
        "anchor_task_id",
        "project_hash",
        "affected_task_count",
        "database_sha256",
        "database_bytes",
        "created_at",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise _failure("maintenance_backup_invalid")
    if (
        manifest.get("format") != _BACKUP_FORMAT
        or manifest.get("state") != "complete"
        or manifest.get("backup_scope") != "project_shard"
        or manifest.get("backup_id") != identifier
        or manifest.get("anchor_task_id") != target.task_id
        or manifest.get("project_hash") != target.project_hash
        or not isinstance(manifest.get("affected_task_count"), int)
        or isinstance(manifest.get("affected_task_count"), bool)
        or not 1 <= int(manifest["affected_task_count"]) <= 100_000
        or manifest.get("database_sha256") != _sha256_file(database)
        or manifest.get("database_bytes") != _safe_file_size(database)
        or not isinstance(manifest.get("created_at"), str)
    ):
        raise _failure("maintenance_backup_invalid")
    status = _health_target(target, database=database)
    if not status["healthy"] or int(manifest["affected_task_count"]) != int(status["affected_task_count"]):
        raise _failure("maintenance_backup_invalid")
    return identifier, bundle, database, manifest_path, manifest


def _configure_live_database(target: _Target) -> None:
    with _connection(target, writable=True) as connection:
        connection.execute("SELECT 1 FROM tasks WHERE task_id=? AND project_hash=?", (target.task_id, target.project_hash)).fetchone()
        if int(connection.execute("PRAGMA synchronous").fetchone()[0]) != 2:
            raise _failure("maintenance_configuration_invalid")
        if str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            raise _failure("maintenance_configuration_invalid")


def _backup_into_live(target: _Target, source_database: Path) -> None:
    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(_readonly_uri(source_database), uri=True, timeout=15, isolation_level=None)
        with _connection(target, writable=True) as destination:
            source.backup(destination)
            destination.commit()
    except V12MaintenanceError:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise _failure("maintenance_restore_failed") from exc
    finally:
        if source is not None:
            source.close()


def restore(
    *,
    task_id: str,
    backup_id: str,
    confirm_action: str,
    confirm_task_id: str,
    confirm_shard: str,
    confirm_service_stopped: str,
) -> dict[str, Any]:
    """Restore a shard backup after the operator stops normal MCP access.

    This module cannot make a maintenance-only lock compatible with ordinary
    V12Store access.  The explicit quiescence acknowledgement is therefore a
    hard precondition, not a claim that this is a live/online restore.
    """
    _require_action(confirm_action, "RESTORE")
    _require_action(confirm_service_stopped, "MCP_STOPPED")
    target = _target(task_id)
    if confirm_task_id != target.task_id or confirm_shard != f"p-{target.project_hash}":
        raise _failure("maintenance_confirmation_mismatch")
    _require_healthy(target)
    identifier, _source_bundle, source_database, _manifest, _metadata = _validate_backup(target, backup_id)
    pre_restore = _create_backup(target)
    try:
        _backup_into_live(target, source_database)
        _configure_live_database(target)
        status = _require_healthy(target)
    except V12MaintenanceError as original:
        try:
            _recovery_id, _recovery_bundle, recovery_database, _recovery_manifest, _recovery_metadata = _validate_backup(target, pre_restore["backup_id"])
            _backup_into_live(target, recovery_database)
            _configure_live_database(target)
            _require_healthy(target)
        except V12MaintenanceError:
            raise _failure("maintenance_restore_recovery_failed") from original
        raise _failure("maintenance_restore_failed_recovered") from original
    return {
        "ok": True,
        "operation": "restore",
        "task_id": target.task_id,
        "backup_scope": "project_shard",
        "anchor_task_id": target.task_id,
        "affected_task_count": int(_metadata["affected_task_count"]),
        "restored_backup_id": identifier,
        "recovery_backup_id": pre_restore["backup_id"],
        "healthy": status["healthy"],
    }


def _maintenance_write(target: _Target, statement: str) -> tuple[Any, ...] | None:
    _require_healthy(target)
    with _connection(target, writable=True) as connection:
        task = connection.execute(
            "SELECT 1 FROM tasks WHERE task_id=? AND project_hash=?", (target.task_id, target.project_hash)
        ).fetchone()
        if task is None:
            raise _failure("maintenance_task_not_found")
        row = connection.execute(statement).fetchone()
        return None if row is None else tuple(row)


def checkpoint(*, task_id: str, confirm_action: str, mode: str = "PASSIVE") -> dict[str, Any]:
    """Run one explicit WAL checkpoint for the exact task's V12 shard."""
    _require_action(confirm_action, "CHECKPOINT")
    selected = mode.upper() if isinstance(mode, str) else ""
    if selected not in _CHECKPOINT_MODES:
        raise _failure("maintenance_checkpoint_mode_invalid")
    target = _target(task_id)
    row = _maintenance_write(target, f"PRAGMA wal_checkpoint({selected})")
    values = (0, 0, 0) if row is None else row
    return {
        "ok": True,
        "operation": "checkpoint",
        "task_id": target.task_id,
        "mode": selected,
        "busy": bool(int(values[0])),
        "log_frames": int(values[1]),
        "checkpointed_frames": int(values[2]),
    }


def optimize(*, task_id: str, confirm_action: str) -> dict[str, Any]:
    """Run SQLite's explicit optimizer for one validated V12 shard."""
    _require_action(confirm_action, "OPTIMIZE")
    target = _target(task_id)
    _maintenance_write(target, "PRAGMA optimize")
    return {"ok": True, "operation": "optimize", "task_id": target.task_id}


def vacuum(*, task_id: str, confirm_action: str) -> dict[str, Any]:
    """Run an explicit SQLite VACUUM only after health validation."""
    _require_action(confirm_action, "VACUUM")
    target = _target(task_id)
    _maintenance_write(target, "VACUUM")
    status = _require_healthy(target)
    return {
        "ok": True,
        "operation": "vacuum",
        "task_id": target.task_id,
        "database_bytes": status["database_bytes"],
    }


def _projection_candidates(target: _Target) -> tuple[list[tuple[str, Path]], int]:
    """Return registered exact-task Markdown targets, never a directory walk."""
    _require_healthy(target)
    entries: list[tuple[str, str, str]] = []
    with _connection(target, writable=False) as connection:
        rows = connection.execute(
            "SELECT relative_path,content_digest,status FROM projection_files WHERE task_id=? ORDER BY relative_path LIMIT ?",
            (target.task_id, _MAX_PROJECTION_ENTRIES + 1),
        ).fetchall()
        if len(rows) > _MAX_PROJECTION_ENTRIES:
            raise _failure("maintenance_projection_limit_exceeded")
        entries = [(str(row[0]), str(row[1]), str(row[2])) for row in rows]
    candidates: list[tuple[str, Path]] = []
    unsafe = 0
    for relative, expected_digest, status in entries:
        # A ready current view is not an obsolete projection.  Never turn an
        # explicit cleanup command into a task-history erasure mechanism.
        if status == "ready":
            continue
        if status not in {"stale", "unavailable", "disabled"}:
            unsafe += 1
            continue
        fragment = Path(relative)
        if fragment.is_absolute() or ".." in fragment.parts or fragment.suffix != ".md":
            unsafe += 1
            continue
        path = target.projections / fragment
        try:
            _contained(path, target.projections)
            current = target.projections
            if target.projections.exists():
                _directory(target.projections, private=True)
                for part in fragment.parts[:-1]:
                    current /= part
                    _directory(current, private=True)
            if path.exists():
                _regular(path)
                if _sha256_file(path) != expected_digest:
                    unsafe += 1
                    continue
            candidates.append((relative, path))
        except V12MaintenanceError:
            unsafe += 1
    return candidates, unsafe


def prune_projections(
    *,
    task_id: str,
    dry_run: bool = True,
    confirm_action: str | None = None,
) -> dict[str, Any]:
    """Prune only registered derived Markdown for one task; default is dry-run."""
    if not isinstance(dry_run, bool):
        raise _failure("maintenance_dry_run_invalid")
    target = _target(task_id)
    candidates, unsafe = _projection_candidates(target)
    if unsafe:
        return {
            "ok": False,
            "operation": "projection-prune",
            "task_id": target.task_id,
            "dry_run": dry_run,
            "candidate_count": len(candidates),
            "unsafe_count": unsafe,
            "applied": False,
        }
    if dry_run:
        return {
            "ok": True,
            "operation": "projection-prune",
            "task_id": target.task_id,
            "dry_run": True,
            "candidate_count": len(candidates),
            "unsafe_count": 0,
            "applied": False,
        }
    _require_action(confirm_action, "PRUNE_PROJECTIONS")
    # Validate all target paths before deleting even one.  We only unlink
    # exact rows recorded by the renderer; unmanaged files are left alone.
    for _relative, path in candidates:
        if path.exists():
            _regular(path)
    removed = 0
    for _relative, path in candidates:
        if path.exists():
            try:
                os.unlink(path)
                removed += 1
            except OSError as exc:
                raise _failure("maintenance_projection_prune_failed") from exc
    with _connection(target, writable=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if candidates:
                placeholders = ",".join("?" for _ in candidates)
                connection.execute(
                    f"DELETE FROM projection_files WHERE task_id=? AND relative_path IN ({placeholders})",
                    [target.task_id, *(relative for relative, _path in candidates)],
                )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return {
        "ok": True,
        "operation": "projection-prune",
        "task_id": target.task_id,
        "dry_run": False,
        "candidate_count": len(candidates),
        "removed_count": removed,
        "unsafe_count": 0,
        "applied": True,
    }


def regenerate_projections(*, task_id: str, confirm_action: str) -> dict[str, Any]:
    """Regenerate only the requested task's derived host-private Markdown."""
    _require_action(confirm_action, "REGENERATE_PROJECTIONS")
    target = _target(task_id)
    _require_healthy(target)
    try:
        from cortex_runtime.v12_projections import materialize_task
        from cortex_runtime.v12_store import V12Store

        outcome = materialize_task(V12Store.for_task_id(target.task_id), target.task_id)
    except Exception as exc:  # renderer failure must not disclose a path/detail
        raise _failure("maintenance_projection_regeneration_failed") from exc
    files = outcome.get("files") if isinstance(outcome, Mapping) else {}
    ready = sum(1 for value in files.values() if value == "ready") if isinstance(files, Mapping) else 0
    return {
        "ok": outcome.get("status") == "ready" if isinstance(outcome, Mapping) else False,
        "operation": "projection-regenerate",
        "task_id": target.task_id,
        "rendered_count": ready,
        "status": str(outcome.get("status", "unavailable")) if isinstance(outcome, Mapping) else "unavailable",
    }


def _backup_ids(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_RETENTION_BACKUPS:
        raise _failure("maintenance_retention_backup_ids_invalid")
    result = [_backup_id(item) for item in value]
    if len(set(result)) != len(result):
        raise _failure("maintenance_retention_backup_ids_invalid")
    return result


def retention(
    *,
    task_id: str,
    backup_ids: list[str],
    dry_run: bool = True,
    confirm_action: str | None = None,
) -> dict[str, Any]:
    """Retain canonical ledger rows; remove only explicit valid backup bundles."""
    if not isinstance(dry_run, bool):
        raise _failure("maintenance_dry_run_invalid")
    target = _target(task_id)
    _require_healthy(target)
    identifiers = _backup_ids(backup_ids)
    valid: list[tuple[str, Path, Path, Path]] = []
    unsafe = 0
    for identifier in identifiers:
        try:
            item, bundle, database, manifest, _data = _validate_backup(target, identifier)
            valid.append((item, bundle, database, manifest))
        except V12MaintenanceError:
            unsafe += 1
    if unsafe:
        return {
            "ok": False,
            "operation": "retention",
            "task_id": target.task_id,
            "backup_scope": "project_shard",
            "dry_run": dry_run,
            "requested_count": len(identifiers),
            "eligible_count": len(valid),
            "unsafe_count": unsafe,
            "applied": False,
            "canonical_data_retained": True,
        }
    if dry_run:
        return {
            "ok": True,
            "operation": "retention",
            "task_id": target.task_id,
            "backup_scope": "project_shard",
            "dry_run": True,
            "requested_count": len(identifiers),
            "eligible_count": len(valid),
            "unsafe_count": 0,
            "applied": False,
            "canonical_data_retained": True,
        }
    _require_action(confirm_action, "RETENTION")
    # Revalidate after the explicit confirmation, then remove only the
    # server-generated manifest/database/SQLite-sidecar allowlist.  No
    # directory recursion or caller-selected file path is permitted.
    valid = []
    for identifier in identifiers:
        item, bundle, database, manifest, _data = _validate_backup(target, identifier)
        valid.append((item, bundle, database, manifest))
    removed = 0
    for _identifier, bundle, database, manifest in valid:
        _remove_validated_backup_bundle(bundle, database, manifest)
        removed += 1
    return {
        "ok": True,
        "operation": "retention",
        "task_id": target.task_id,
        "backup_scope": "project_shard",
        "dry_run": False,
        "requested_count": len(identifiers),
        "removed_count": removed,
        "unsafe_count": 0,
        "applied": True,
        "canonical_data_retained": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cortex-v12-maintenance")
    actions = parser.add_subparsers(dest="command", required=True)

    def task(command: str) -> argparse.ArgumentParser:
        item = actions.add_parser(command)
        item.add_argument("--task-id", required=True)
        return item

    task("health")
    command = task("backup")
    command.add_argument("--confirm-action", required=True, choices=["BACKUP"])
    command.add_argument("--backup-id")
    command = task("checkpoint")
    command.add_argument("--confirm-action", required=True, choices=["CHECKPOINT"])
    command.add_argument("--mode", default="PASSIVE", choices=sorted(_CHECKPOINT_MODES))
    command = task("optimize")
    command.add_argument("--confirm-action", required=True, choices=["OPTIMIZE"])
    command = task("vacuum")
    command.add_argument("--confirm-action", required=True, choices=["VACUUM"])
    command = task("restore")
    command.add_argument("--backup-id", required=True)
    command.add_argument("--confirm-action", required=True, choices=["RESTORE"])
    command.add_argument("--confirm-task-id", required=True)
    command.add_argument("--confirm-shard", required=True)
    command.add_argument("--confirm-service-stopped", required=True, choices=["MCP_STOPPED"])
    command = task("projection-prune")
    group = command.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    command.add_argument("--confirm-action", choices=["PRUNE_PROJECTIONS"])
    command = task("projection-regenerate")
    command.add_argument("--confirm-action", required=True, choices=["REGENERATE_PROJECTIONS"])
    command = task("retention")
    group = command.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    command.add_argument("--backup-id", action="append", dest="backup_ids", required=True)
    command.add_argument("--confirm-action", choices=["RETENTION"])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = vars(_parser().parse_args(argv))
    command = arguments.pop("command")
    try:
        if command == "health":
            result = health(**arguments)
        elif command == "backup":
            result = backup(**arguments)
        elif command == "checkpoint":
            result = checkpoint(**arguments)
        elif command == "optimize":
            result = optimize(**arguments)
        elif command == "vacuum":
            result = vacuum(**arguments)
        elif command == "restore":
            result = restore(**arguments)
        elif command == "projection-prune":
            apply = bool(arguments.pop("apply"))
            arguments.pop("dry_run")
            result = prune_projections(dry_run=not apply, **arguments)
        elif command == "projection-regenerate":
            result = regenerate_projections(**arguments)
        elif command == "retention":
            apply = bool(arguments.pop("apply"))
            arguments.pop("dry_run")
            result = retention(dry_run=not apply, **arguments)
        else:  # argparse's closed subcommand set makes this defensive only.
            raise _failure("maintenance_command_invalid")
    except V12MaintenanceError as error:
        result = {"ok": False, "code": error.code}
    except Exception:
        result = {"ok": False, "code": "maintenance_unavailable"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "V12MaintenanceError",
    "health",
    "backup",
    "checkpoint",
    "optimize",
    "vacuum",
    "restore",
    "prune_projections",
    "regenerate_projections",
    "retention",
    "main",
]
