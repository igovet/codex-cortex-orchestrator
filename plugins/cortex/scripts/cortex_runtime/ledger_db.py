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
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex_runtime import canonical_json
from cortex_runtime.finding_severity import (
    CANONICAL_FINDING_SEVERITY_RANK,
    finding_severity_is_intrinsically_blocking,
    normalize_finding_severity,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the process-local guard.
    fcntl = None


DATABASE_NAME = "cortex.db"
NATIVE_HOST_START_SCHEMA = "cortex/native-host-subagent-start/v2"
NATIVE_HOST_START_KEY_PREFIX = "native-host-start:"
NATIVE_HOST_START_BOUNDARY_KEY_PREFIX = "native-host-start-boundary:"
NATIVE_HOST_STOP_SCHEMA = "cortex/native-host-subagent-stop/v1"
NATIVE_HOST_STOP_KEY_PREFIX = "native-host-stop:"
NATIVE_HOST_STOP_RECEIPT_SCHEMA = "cortex/native-host-subagent-stop-receipt/v1"
NATIVE_HOST_STOP_RECEIPT_KEY_PREFIX = "native-host-stop-receipt:"
NATIVE_HOST_EPOCH_SCHEMA = "cortex/native-host-epoch/v1"
NATIVE_HOST_EPOCH_KEY_PREFIX = "native-host-epoch:"
NATIVE_CONTEXT_BOUNDARY_SCHEMA = "cortex/native-context-boundary/v1"
NATIVE_CONTEXT_BOUNDARY_KEY_PREFIX = "native-context-boundary:"
NATIVE_CONTEXT_BOUNDARY_ACK_SCHEMA = "cortex/native-context-boundary-ack/v1"
NATIVE_CONTEXT_BOUNDARY_ACK_KEY_PREFIX = "native-context-boundary-ack:"
NATIVE_LIFECYCLE_FAILURE_SCHEMA = "cortex/native-lifecycle-observer-failure/v1"
NATIVE_LIFECYCLE_FAILURE_KEY_PREFIX = "native-lifecycle-observer:"
NATIVE_LIFECYCLE_PROJECT_FAILURE_DOCUMENT = "native_lifecycle_observer_failure"
NATIVE_LIFECYCLE_TASK_FAILURE_DOCUMENT = "native-lifecycle-observer:task"
PRIVATE_LIFECYCLE_AUDIT_DOCUMENT = "native-lifecycle-audit"
PRIVATE_LIFECYCLE_AUDIT_SCHEMA = "cortex/private-native-lifecycle-audit/v1"
PRIVATE_LIFECYCLE_AUDIT_RETENTION = 96
DATABASE_SCHEMA_VERSION = 19
# Exact signed storage histories emitted by supported prior installations.
# They are migration authority only; the current runtime never reads their
# retired protocol shapes. Previous v17/v18 ledgers are upgraded in place;
# the older v1..v8 namespace is preserved as a private quarantine before a
# fresh current ledger is created.
_SUPPORTED_PREVIOUS_V11_V8_HISTORY: tuple[tuple[int, str, str], ...] = (
    (1, "sqlite-ledger-base", "22987cef753fa59594fcb25794b0c29d63f9d9660879c15ae994cb13af354cbe"),
    (2, "immutable-artifact-catalog-and-chunks", "e9bb55107d182a08baa01826e8ff6e242956e5e7d32a730f097dcb49b07659ef"),
    (3, "canonical-task-findings", "0ee95845cf6d5f9aac5bc7aabd9f3774ca03f08e53168b3efba34b6562635295"),
    (4, "finding-waiver-and-resolution-metadata", "1e5886140fc287e3dcf12666398ddbbed3655298594350c9dec0b0988c961e6d"),
    (5, "projection-jobs", "fa1c55dd14ea965b59b069ed24bfe80d9b71c68792be5c9010eb0a6242dab853"),
    (6, "crash-safe-prune-tombstones", "a466fdfc92d3ada9e3956b06502cd8d771b1147732e7d0305857a589e660cabc"),
    (7, "canonical-content-blobs-and-logical-artifacts", "1cf51c69cab95e7a1d678522d562ca19347d8494e3e145f1e5c46f48ecf6219b"),
    (8, "revision-aware-orchestration", "081812ef70c27cbe647a7c6988ffa61c4a29a25859272e0169a87be48e7ff6f8"),
)
_SUPPORTED_PREVIOUS_V17_HISTORIES: tuple[tuple[tuple[int, str, str], ...], ...] = (
    ((17, "canonical-current-ledger", "8b63216b7cb574d2b5e66f2d6854dd282cffffca86e37d4e4976479fffade44b"),),
    ((17, "canonical-current-ledger", "fe628a4a38ba4462ba0a53f3602bc4d0809e8073e5dfb62bc0e50bd0cd2a4dbb"),),
    ((17, "canonical-current-ledger", "549cac1cbdfb1c019ee9de2c1f0fe7c151d5c3bb2faeeb34e539ae3d6e0f6973"),),
)
_SUPPORTED_PREVIOUS_V18_HISTORIES: tuple[tuple[tuple[int, str, str], ...], ...] = (
    ((18, "canonical-current-ledger", "cba5622afa2a0771165d05866c5170901a7b2704127d559a23b8f8dedbaa46ac"),),
    ((18, "canonical-current-ledger", "0ced9ba5442ca1df1eddc2c25d9e94be17c8a527e9a3d118407232cf45c7f20a"),),
    (
        (17, "canonical-current-ledger", "8b63216b7cb574d2b5e66f2d6854dd282cffffca86e37d4e4976479fffade44b"),
        (18, "remove-retired-question-batches", "a3a526b3a3354d4c6dd39f0aa456f4c2a90e1c85bc8b1699169c0ec2cfd6a6c4"),
    ),
)
_RELEASED_V18_HARD_CUT_ROW = (
    18,
    "remove-retired-question-batches",
    "a3a526b3a3354d4c6dd39f0aa456f4c2a90e1c85bc8b1699169c0ec2cfd6a6c4",
)
_MIGRATED_PRE_V19_QUESTION_CATEGORY = "requirement"
_CURRENT_OPERATION_REGISTRY_SCHEMA = "cortex/orchestration/v11"
ARTIFACT_STORAGE_CHUNK_BYTES = 32 * 1024
# Paging is optional caller framing, not a server-side content quota.  A
# UTF-8-safe page may exceed an extremely small caller request by one scalar.
ARTIFACT_TRANSPORT_MIN_BYTES = 1
ARTIFACT_TRANSPORT_MAX_BYTES = None
# Mutable documents are coordination metadata, never an artifact transport.
# Strict JSON and atomic writes preserve integrity without a size quota.
MAX_DURABLE_DOCUMENT_KEY_BYTES = 160
_LOCAL = threading.local()
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
_DATABASE_BOOTSTRAP_GUARD = threading.RLock()


def _now() -> str:
    # Keep the database package independent from Cortex's response/runtime
    # facade.  RFC 3339 UTC text is sufficient for migration bookkeeping.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return canonical_json.dumps(value)


def _bounded_document_json(value: Any, *, label: str) -> str:
    """Serialize one mutable document as strict JSON without a size quota."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be strict JSON") from exc
    del label
    return encoded.decode("utf-8")


def _bounded_document_key(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{label} is invalid")
    if len(value.encode("utf-8")) > MAX_DURABLE_DOCUMENT_KEY_BYTES:
        raise ValueError(f"{label} exceeds the {MAX_DURABLE_DOCUMENT_KEY_BYTES}-byte limit")
    return value


def _bounded_observation_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"tool observation {label} is invalid")
    return value


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
    this digest helper in the database package so the governance service uses
    *exactly* the same serialization.
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
    """Load a non-ledger key, creating it only during an authorized write.

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
        "schema": "cortex/governance-lifecycle-envelope/v3",
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


def private_lifecycle_audit_digest(root: Path, label: str, value: object) -> str:
    """Return one keyed, non-reversible private lifecycle identity digest."""
    normalized = str(value or "")
    if not normalized or len(normalized) > 1024 or "\x00" in normalized:
        raise ValueError("private lifecycle audit identity is invalid")
    payload = json.dumps([str(label), normalized], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "hmac-sha256:" + hmac.new(
        _governance_lifecycle_hmac_key(root, create=True), payload, hashlib.sha256,
    ).hexdigest()


def append_private_lifecycle_audit(
    root: Path, entry: dict[str, Any], *, task_id: str | None = None,
) -> bool:
    """Append one bounded metadata-only audit event at the trusted local boundary."""
    allowed = {"event", "tool", "outcome", "reason", "digests", "equality", "observed_at"}
    digest_names = {
        "mcp_thread", "hook_agent", "hook_session", "hook_turn", "hook_tool_use",
        "task", "attempt", "wave",
    }
    try:
        if set(entry) != allowed:
            raise ValueError("private lifecycle audit entry shape is invalid")
        if entry.get("event") not in {"mcp_call", "subagent_start", "subagent_stop", "invalid_hook"}:
            raise ValueError("private lifecycle audit event is invalid")
        if entry.get("tool") not in {"none", "spawn_agent", "wait_agent", "public_tool"}:
            raise ValueError("private lifecycle audit tool is invalid")
        if entry.get("outcome") not in {"received", "accepted", "rejected", "bound", "terminal", "confirmed", "retryable"}:
            raise ValueError("private lifecycle audit outcome is invalid")
        if entry.get("reason") not in {
            "strict_shape", "unsupported_shape", "metadata_valid", "metadata_mismatch",
            "candidate_missing", "candidate_ambiguous", "storage_retry", "exact_match",
            "incomplete_stop", "terminal_stop",
        }:
            raise ValueError("private lifecycle audit reason is invalid")
        digests = entry.get("digests")
        equality = entry.get("equality")
        if (
            not isinstance(digests, dict) or not set(digests).issubset(digest_names)
            or not all(re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", str(value)) for value in digests.values())
            or not isinstance(equality, dict)
            or not set(equality).issubset({"thread_matches", "session_matches", "task_matches", "attempt_matches", "wave_matches"})
            or not all(isinstance(value, bool) for value in equality.values())
        ):
            raise ValueError("private lifecycle audit protected fields are invalid")
        document_key = PRIVATE_LIFECYCLE_AUDIT_DOCUMENT
        with _hook_write_connection(root) as connection:
            if task_id is None:
                row = connection.execute(
                    "SELECT payload_json FROM global_documents WHERE name=?", (document_key,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT payload_json FROM task_documents WHERE task_id=? AND document_key=?",
                    (task_id, document_key),
                ).fetchone()
            prior = _decode_json(str(row["payload_json"]), "private lifecycle audit") if row else {}
            events = list(prior.get("events") or [])[-(PRIVATE_LIFECYCLE_AUDIT_RETENTION - 1):]
            events.append(dict(entry))
            document = {
                "schema": PRIVATE_LIFECYCLE_AUDIT_SCHEMA,
                "retention": PRIVATE_LIFECYCLE_AUDIT_RETENTION,
                "events": events,
                "updated_at": str(entry["observed_at"]),
            }
            payload_json = _bounded_document_json(document, label="private lifecycle audit")
            if task_id is None:
                connection.execute(
                    "INSERT INTO global_documents(name,payload_json,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                    (document_key, payload_json, str(entry["observed_at"])),
                )
            else:
                connection.execute(
                    "INSERT INTO task_documents(task_id,document_key,payload_json,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(task_id,document_key) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                    (task_id, document_key, payload_json, str(entry["observed_at"])),
                )
        return True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _hook_metric("telemetry_failure")
        return False


def _decode_json(text: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SQLite {label} JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError(f"SQLite {label} must be an object")
    return value


def _decode_json_list(text: str, label: str) -> list[Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SQLite {label} JSON is invalid") from exc
    if not isinstance(value, list):
        raise ValueError(f"SQLite {label} must be an array")
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


def _fsync_directory(path: Path) -> None:
    """Durably publish an atomic ledger rename on POSIX hosts."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _connect(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    db_path = database_path(root)
    # Create a fresh database inode with its private mode before another
    # thread/process can observe the path.  SQLite's transaction lock then
    # serializes the actual canonical bootstrap; no advisory migration lock
    # artifact is needed.
    try:
        fd = os.open(db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
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


@contextlib.contextmanager
def _database_bootstrap_lock(root: Path) -> Iterator[None]:
    """Serialize first-boot and stale-ledger isolation across host processes.

    A schema replacement is intentionally not a SQLite migration.  It is a
    filesystem operation (the old database is renamed out of the active
    path), so SQLite's database lock cannot protect the decision by itself.
    Keep a private lock beside the ledger while deciding whether to quarantine
    an incompatible file and while creating the replacement.
    """
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = root / ".cortex-bootstrap.lock"
    with _DATABASE_BOOTSTRAP_GUARD:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _active_connections() -> dict[str, sqlite3.Connection]:
    connections = getattr(_LOCAL, "connections", None)
    if connections is None:
        connections = {}
        _LOCAL.connections = connections
    return connections


def _root_key(root: Path) -> str:
    return str(root.resolve())


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
    bounded ``None`` snapshot. Lifecycle observers distinguish and retry that
    signal rather than collapsing it into an empty match set. Callers perform
    all reads while this single deferred transaction is open, then close it.
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


def hook_snapshot_worker_bindings(
    connection: sqlite3.Connection,
    host_agent_id: str,
    *,
    limit: int = 2,
) -> list[tuple[str, str]]:
    """Return a bounded exact private child binding without task JSON scans."""
    if not isinstance(host_agent_id, str) or not host_agent_id or "\x00" in host_agent_id:
        return []
    bounded = max(1, min(int(limit), 2))
    rows = connection.execute(
        "SELECT task_id,attempt_id FROM worker_sessions "
        "WHERE host_agent_id=? ORDER BY task_id,attempt_id LIMIT ?",
        (host_agent_id, bounded),
    ).fetchall()
    return [(str(row["task_id"]), str(row["attempt_id"])) for row in rows]


def hook_snapshot_awaiting_worker_sessions(
    connection: sqlite3.Connection,
    *,
    limit: int = 65,
) -> list[tuple[str, str]]:
    """Return a bounded candidate set for an unbound trusted Start."""
    bounded = max(1, min(int(limit), 65))
    rows = connection.execute(
        "SELECT task_id,attempt_id FROM worker_sessions "
        "WHERE status='awaiting_spawn' AND resumable=1 AND host_agent_id IS NULL "
        "ORDER BY last_seen_at,task_id,attempt_id LIMIT ?",
        (bounded,),
    ).fetchall()
    return [(str(row["task_id"]), str(row["attempt_id"])) for row in rows]


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

    Artifact identity is logical: the title is part of the identity, so two
    exports with identical bytes remain distinct canonical artifacts.
    """
    return "artifact-" + hashlib.sha256(
        f"{task_id}\0{kind}\0{title}\0{digest}".encode("utf-8")
    ).hexdigest()[:32]


def _blob_ref(digest: str, mime_type: str, byte_size: int) -> str:
    """Return the content-addressed identifier for one canonical blob."""
    # Keep this exactly representable by the append-only SQLite schema.  The
    # actual identity remains the unique digest/mime/size tuple, while this is
    # its deterministic key.
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
    "CREATE TABLE IF NOT EXISTS artifact_blobs(blob_id TEXT PRIMARY KEY, digest_sha256 TEXT NOT NULL, mime_type TEXT NOT NULL, byte_size INTEGER NOT NULL CHECK(byte_size >= 0), chunk_count INTEGER NOT NULL CHECK(chunk_count >= 1), encoding TEXT NOT NULL CHECK(encoding IN ('utf-8', 'binary')), created_at TEXT NOT NULL, UNIQUE(digest_sha256, mime_type, byte_size))",
    "CREATE TABLE IF NOT EXISTS artifact_blob_chunks(blob_id TEXT NOT NULL REFERENCES artifact_blobs(blob_id) ON DELETE CASCADE, chunk_no INTEGER NOT NULL CHECK(chunk_no >= 0), text_content TEXT, blob_content BLOB, byte_size INTEGER NOT NULL CHECK(byte_size >= 0), digest_sha256 TEXT NOT NULL, PRIMARY KEY(blob_id, chunk_no), CHECK((text_content IS NOT NULL AND blob_content IS NULL) OR (text_content IS NULL AND blob_content IS NOT NULL)))",
    "CREATE TABLE IF NOT EXISTS logical_artifacts(artifact_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, kind TEXT NOT NULL, title TEXT NOT NULL, mime_type TEXT NOT NULL, digest_sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL CHECK(byte_size >= 0), chunk_count INTEGER NOT NULL CHECK(chunk_count >= 1), immutable INTEGER NOT NULL CHECK(immutable IN (0, 1)), blob_id TEXT NOT NULL REFERENCES artifact_blobs(blob_id), export_path TEXT, created_at TEXT NOT NULL, UNIQUE(task_id, kind, title, digest_sha256))",
    "CREATE TABLE IF NOT EXISTS artifact_exports(artifact_id TEXT NOT NULL REFERENCES logical_artifacts(artifact_id) ON DELETE CASCADE, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, export_path TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(artifact_id, export_path), UNIQUE(task_id, export_path))",
    "CREATE INDEX IF NOT EXISTS tasks_created_at_idx ON tasks(created_at)",
    "CREATE INDEX IF NOT EXISTS logical_artifacts_task_kind_created_idx ON logical_artifacts(task_id, kind, created_at DESC, artifact_id)",
    "CREATE INDEX IF NOT EXISTS logical_artifacts_task_created_idx ON logical_artifacts(task_id, created_at DESC, artifact_id)",
    "CREATE INDEX IF NOT EXISTS logical_artifacts_blob_idx ON logical_artifacts(blob_id)",
    "CREATE INDEX IF NOT EXISTS artifact_exports_task_path_idx ON artifact_exports(task_id, export_path)",
)
_CLOSURE_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS task_findings(task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, fingerprint TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL, blocking INTEGER NOT NULL CHECK(blocking IN (0, 1)), summary TEXT NOT NULL, details TEXT, next_action_json TEXT, source_evidence_json TEXT NOT NULL, first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL, waiver_reason TEXT, waived_by TEXT, waived_at TEXT, resolved_at TEXT, PRIMARY KEY(task_id, fingerprint))",
    "CREATE INDEX IF NOT EXISTS task_findings_task_status_idx ON task_findings(task_id, status, severity, blocking)",
)
_FINDING_METADATA_SCHEMA_STATEMENTS: tuple[str, ...] = ()

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

# Kept as a numbered no-op so the current fresh schema remains monotonic.  It
# deliberately contains no upgrade logic: an existing incompatible database
# fails the immutable checksum/schema checks instead of being read.
_ARTIFACT_NORMALIZATION_SCHEMA_STATEMENTS: tuple[str, ...] = ()

_REVISION_AWARE_ORCHESTRATION_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS task_revisions(task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, task_revision INTEGER NOT NULL CHECK(task_revision >= 1), base_revision INTEGER, source TEXT NOT NULL CHECK(source IN ('initial','user_steer','recovery','system')), message_original TEXT NOT NULL, message_language TEXT NOT NULL, message_en TEXT, translation_status TEXT NOT NULL DEFAULT 'not_required' CHECK(translation_status = 'not_required'), created_at TEXT NOT NULL, PRIMARY KEY(task_id, task_revision))",
    "CREATE TABLE IF NOT EXISTS plan_revisions(task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, plan_revision INTEGER NOT NULL CHECK(plan_revision >= 1), base_plan_revision INTEGER, task_revision INTEGER NOT NULL CHECK(task_revision >= 1), impact_json TEXT NOT NULL, plan_json TEXT, status TEXT NOT NULL CHECK(status IN ('active','superseded','approved','pending')), created_at TEXT NOT NULL, PRIMARY KEY(task_id, plan_revision))",
    "CREATE TABLE IF NOT EXISTS worker_sessions(session_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, host_agent_id TEXT, host_task_name TEXT NOT NULL, host_tool TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1 CHECK(generation >= 1), status TEXT NOT NULL CHECK(status IN ('awaiting_spawn','running','idle_resumable','stopped_recoverable','terminated_unavailable','completed')), resumable INTEGER NOT NULL DEFAULT 1 CHECK(resumable IN (0,1)), started_at TEXT, last_seen_at TEXT NOT NULL, terminated_at TEXT, UNIQUE(task_id, attempt_id, generation), UNIQUE(task_id, host_agent_id))",
    "CREATE TABLE IF NOT EXISTS attempt_messages(message_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, source TEXT NOT NULL CHECK(source IN ('user','coordinator','system')), kind TEXT NOT NULL CHECK(kind IN ('question_answer','steer','correction','recovery')), original_text TEXT NOT NULL, original_language TEXT NOT NULL, canonical_en TEXT NOT NULL, task_revision INTEGER NOT NULL CHECK(task_revision >= 1), created_at TEXT NOT NULL, delivered_at TEXT, acknowledged_at TEXT)",
    "CREATE TABLE IF NOT EXISTS orchestration_trace(trace_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT, event TEXT NOT NULL, occurred_at TEXT NOT NULL, metadata_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS tool_observations(observation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, context_epoch INTEGER NOT NULL CHECK(context_epoch >= 0), fingerprint TEXT NOT NULL, tool_name TEXT NOT NULL, normalized_arguments TEXT NOT NULL, workspace_generation TEXT NOT NULL, result_digest TEXT, coverage TEXT, status TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, repeat_count INTEGER NOT NULL DEFAULT 0 CHECK(repeat_count >= 0), UNIQUE(task_id, attempt_id, context_epoch, fingerprint))",
    "CREATE INDEX IF NOT EXISTS task_revisions_created_idx ON task_revisions(task_id, created_at)",
    "CREATE INDEX IF NOT EXISTS plan_revisions_created_idx ON plan_revisions(task_id, created_at)",
    "CREATE INDEX IF NOT EXISTS worker_sessions_status_idx ON worker_sessions(task_id, status, last_seen_at)",
    "CREATE INDEX IF NOT EXISTS attempt_messages_delivery_idx ON attempt_messages(task_id, attempt_id, delivered_at, created_at)",
    "CREATE INDEX IF NOT EXISTS orchestration_trace_task_idx ON orchestration_trace(task_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS tool_observations_attempt_idx ON tool_observations(task_id, attempt_id, context_epoch, last_seen_at)",
)

# One durable record holds the human-visible question text and its optional
# answer. Structured options and localization state is intentionally not
# retained in the current ledger.
_DURABLE_QUESTION_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS durable_questions(question_ref TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, dispatch_ref TEXT NOT NULL, profile TEXT NOT NULL, task_revision INTEGER NOT NULL CHECK(task_revision >= 1), attempt_generation INTEGER NOT NULL CHECK(attempt_generation >= 1), submission_id TEXT NOT NULL, question_category TEXT, question_text TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','answered','superseded')), content_digest TEXT NOT NULL, published_sequence INTEGER NOT NULL CHECK(published_sequence >= 1), answer TEXT, answer_submission_id TEXT, answer_digest TEXT, answered_sequence INTEGER CHECK(answered_sequence >= 1), created_at TEXT NOT NULL, answered_at TEXT, superseded_at TEXT, UNIQUE(task_id,attempt_id,submission_id), UNIQUE(task_id,published_sequence))",
    "CREATE INDEX IF NOT EXISTS durable_questions_task_status_published_idx ON durable_questions(task_id,status,published_sequence)",
)

# Governance is part of the one current canonical ledger schema. Bodies are
# kept as canonical JSON plus a digest in the append-only record table; callers
# may additionally associate an immutable artifact from the normal artifact
# catalog when the record is task-scoped.
_GOVERNANCE_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS initiatives(initiative_ref TEXT PRIMARY KEY, parent_ref TEXT REFERENCES initiatives(initiative_ref) ON DELETE RESTRICT, title TEXT NOT NULL, goal TEXT NOT NULL, owner TEXT NOT NULL, risk TEXT NOT NULL CHECK(risk IN ('low','moderate','high','critical')), acceptance_oracle_artifact_ref TEXT, status TEXT NOT NULL CHECK(status IN ('proposed','active','blocked','completed','closed','cancelled')), revision INTEGER NOT NULL CHECK(revision >= 1), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(parent_ref, title))",
    "CREATE INDEX IF NOT EXISTS initiatives_parent_idx ON initiatives(parent_ref, status, updated_at)",
    "CREATE INDEX IF NOT EXISTS initiatives_status_idx ON initiatives(status, updated_at)",
    "CREATE TABLE IF NOT EXISTS initiative_task_links(initiative_ref TEXT NOT NULL REFERENCES initiatives(initiative_ref) ON DELETE CASCADE, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, relationship TEXT NOT NULL CHECK(relationship IN ('milestone','deliverable','corrective')), milestone TEXT, deliverable TEXT, corrective INTEGER NOT NULL DEFAULT 0 CHECK(corrective IN (0,1)), expected_revision INTEGER NOT NULL DEFAULT 1 CHECK(expected_revision >= 1), created_at TEXT NOT NULL, PRIMARY KEY(initiative_ref, task_id, relationship))",
    "CREATE INDEX IF NOT EXISTS initiative_task_links_task_idx ON initiative_task_links(task_id, relationship)",
    "CREATE TABLE IF NOT EXISTS initiative_dependencies(dependency_ref TEXT PRIMARY KEY, source_type TEXT NOT NULL CHECK(source_type IN ('initiative','task')), source_ref TEXT NOT NULL, target_type TEXT NOT NULL CHECK(target_type IN ('initiative','task')), target_ref TEXT NOT NULL, dependency_type TEXT NOT NULL CHECK(dependency_type IN ('blocks','requires','relates_to','follows')), created_at TEXT NOT NULL, UNIQUE(source_type, source_ref, target_type, target_ref, dependency_type), CHECK(NOT(source_type = target_type AND source_ref = target_ref)))",
    "CREATE INDEX IF NOT EXISTS initiative_dependencies_source_idx ON initiative_dependencies(source_type, source_ref)",
    "CREATE INDEX IF NOT EXISTS initiative_dependencies_target_idx ON initiative_dependencies(target_type, target_ref)",
    "CREATE TABLE IF NOT EXISTS governance_records(record_ref TEXT PRIMARY KEY, initiative_ref TEXT REFERENCES initiatives(initiative_ref) ON DELETE SET NULL, task_id TEXT REFERENCES tasks(task_id) ON DELETE SET NULL, record_type TEXT NOT NULL CHECK(record_type IN ('policy','decision','ruling','preference','assumption','risk','learning','reflection','exception','promotion')), revision INTEGER NOT NULL CHECK(revision >= 1), supersedes TEXT REFERENCES governance_records(record_ref) ON DELETE RESTRICT, status TEXT NOT NULL CHECK(status IN ('pending','active','approved','rejected','superseded','expired')), content_json TEXT NOT NULL, content_digest TEXT NOT NULL, content_artifact_ref TEXT, approval_basis_json TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT, scope_key TEXT NOT NULL, lifecycle_sequence INTEGER NOT NULL DEFAULT 0 CHECK(lifecycle_sequence >= 0), lifecycle_binding TEXT NOT NULL DEFAULT '', UNIQUE(initiative_ref, task_id, record_type, revision))",
    "CREATE INDEX IF NOT EXISTS governance_records_scope_idx ON governance_records(initiative_ref, task_id, record_type, revision DESC)",
    "CREATE INDEX IF NOT EXISTS governance_records_active_idx ON governance_records(status, record_type, expires_at)",
    "CREATE TABLE IF NOT EXISTS governance_links(link_ref TEXT PRIMARY KEY, record_ref TEXT NOT NULL REFERENCES governance_records(record_ref) ON DELETE CASCADE, initiative_ref TEXT REFERENCES initiatives(initiative_ref) ON DELETE CASCADE, task_id TEXT REFERENCES tasks(task_id) ON DELETE CASCADE, lane_id TEXT REFERENCES lanes(lane_id) ON DELETE CASCADE, finding_fingerprint TEXT, evidence_ref TEXT, relationship TEXT NOT NULL CHECK(relationship IN ('initiative','task','lane','finding','evidence')), created_at TEXT NOT NULL, CHECK(initiative_ref IS NOT NULL OR task_id IS NOT NULL OR lane_id IS NOT NULL OR finding_fingerprint IS NOT NULL OR evidence_ref IS NOT NULL))",
    "CREATE INDEX IF NOT EXISTS governance_links_record_idx ON governance_links(record_ref, relationship)",
    "CREATE INDEX IF NOT EXISTS governance_links_target_idx ON governance_links(initiative_ref, task_id, lane_id, finding_fingerprint)",
)

# Governance integrity is enforced directly on the current canonical tables.
_GOVERNANCE_INTEGRITY_SCHEMA_STATEMENTS = (
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

# Lifecycle integrity binds the mutable lifecycle projection in
# ``governance_records`` to an append-only transition chain. A record body is
# immutable,
# but a raw SQL writer could still rewrite its current status or approval
# basis.  The lifecycle row carries the exact predecessor binding, current
# status and approval basis, while the record keeps only the indexed current
# projection.  Reads verify the cryptographic chain in governance.py.
_GOVERNANCE_LIFECYCLE_INTEGRITY_SCHEMA_STATEMENTS = (
    "CREATE TABLE governance_record_lifecycle(lifecycle_ref TEXT PRIMARY KEY, record_ref TEXT NOT NULL REFERENCES governance_records(record_ref) ON DELETE RESTRICT, lifecycle_sequence INTEGER NOT NULL CHECK(lifecycle_sequence >= 0), previous_binding TEXT, status TEXT NOT NULL CHECK(status IN ('pending','active','approved','rejected','superseded','expired')), approval_basis_json TEXT, binding TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('created','transition')), actor_role TEXT NOT NULL CHECK(actor_role IN ('coordinator','worker','reviewer','system')), created_at TEXT NOT NULL, UNIQUE(record_ref,lifecycle_sequence))",
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

# Seal the complete lifecycle event envelope with a host-private HMAC. The
# public SHA-256 chain and its authentication projection are append-only.
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
    "CREATE TABLE attempt_events(event_ref TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, event_key TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence >= 1), event_type TEXT NOT NULL CHECK(event_type IN ('finding_recorded','decision_evidence','verification_observed','progress','note','briefing_acknowledged','predecessor_read','work_completed','finalizing','finalization_failed','completed')), payload_json TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN ('worker','cortex','system')), occurred_at TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id, attempt_id, event_key), UNIQUE(task_id, attempt_id, sequence))",
    "CREATE INDEX attempt_events_task_attempt_sequence_idx ON attempt_events(task_id, attempt_id, sequence)",
    "CREATE INDEX attempt_events_task_type_idx ON attempt_events(task_id, event_type, occurred_at)",
)

# v14 makes verification authority explicit: workers can claim a check, but
# only Cortex may emit the observed verification event consumed by gates.
_ATTEMPT_VERIFICATION_AUTHORITY_SCHEMA_STATEMENTS = (
    "DROP INDEX attempt_events_task_type_idx",
    "DROP INDEX attempt_events_task_attempt_sequence_idx",
    "ALTER TABLE attempt_events RENAME TO attempt_events_v13",
    "CREATE TABLE attempt_events(event_ref TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, event_key TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence >= 1), event_type TEXT NOT NULL CHECK(event_type IN ('finding_recorded','decision_evidence','verification_claimed','verification_observed','progress','note','briefing_acknowledged','predecessor_read','work_completed','finalizing','finalization_failed','completed')), payload_json TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN ('worker','cortex','system')), occurred_at TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id, attempt_id, event_key), UNIQUE(task_id, attempt_id, sequence))",
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
    "CREATE TABLE attempt_events(event_ref TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, event_key TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence >= 1), event_type TEXT NOT NULL CHECK(event_type IN ('finding_recorded','decision_evidence','verification_claimed','verification_observed','progress','note','briefing_acknowledged','predecessor_read','question_created','question_answered','decision_resolved','work_completed','finalizing','finalization_failed','completed')), payload_json TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN ('worker','cortex','system')), occurred_at TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id, attempt_id, event_key), UNIQUE(task_id, attempt_id, sequence))",
    "INSERT INTO attempt_events(event_ref,task_id,attempt_id,event_key,sequence,event_type,payload_json,actor,occurred_at,created_at) SELECT event_ref,task_id,attempt_id,event_key,sequence,event_type,payload_json,actor,occurred_at,created_at FROM attempt_events_v14",
    "DROP TABLE attempt_events_v14",
    "CREATE INDEX attempt_events_task_attempt_sequence_idx ON attempt_events(task_id, attempt_id, sequence)",
    "CREATE INDEX attempt_events_task_type_idx ON attempt_events(task_id, event_type, occurred_at)",
)

# Repair escrow is private transport state, not canonical attempt evidence.
# A rejected draft may be retained here without contradicting a public
# ``state_mutated=false`` response because no task revision, attempt, event,
# result, or workspace row is changed.  Rows live exactly as long as their
# owning task and are immutable after insertion; identical rejected drafts
# reuse one row and therefore one signed public handle.
_REPAIR_ESCROW_SCHEMA_STATEMENTS = (
    "CREATE TABLE repair_escrow(handle_digest TEXT PRIMARY KEY, handle_id TEXT NOT NULL UNIQUE, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, attempt_id TEXT NOT NULL, dispatch_ref_digest TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind = 'report'), base_payload_digest TEXT NOT NULL, payload_json TEXT NOT NULL, diagnostics_json TEXT NOT NULL, patch_paths_json TEXT NOT NULL, escrow_digest TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id, attempt_id, dispatch_ref_digest, escrow_digest))",
    "CREATE INDEX repair_escrow_task_attempt_idx ON repair_escrow(task_id, attempt_id, created_at)",
    "CREATE TRIGGER repair_escrow_immutable_update BEFORE UPDATE ON repair_escrow FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'repair escrow rows are immutable'); END",
)

def _current_migration_histories(
    migrations: tuple[_Migration, ...],
) -> tuple[tuple[tuple[int, str, str], ...], ...]:
    """Return exact append-only histories accepted by the current V19 runtime."""
    fresh = tuple(
        (migration.version, migration.name, _migration_checksum(migration))
        for migration in migrations
    )
    v19_row = fresh[-1]
    from_v17 = tuple(
        (*history, _RELEASED_V18_HARD_CUT_ROW, v19_row)
        for history in _SUPPORTED_PREVIOUS_V17_HISTORIES
    )
    from_v18 = tuple(
        (*history, v19_row) for history in _SUPPORTED_PREVIOUS_V18_HISTORIES
    )
    return (fresh, *from_v17, *from_v18)


def _is_current_migration_history(
    history: Sequence[tuple[object, ...]],
    user_version: int,
    migrations: tuple[_Migration, ...],
) -> bool:
    try:
        normalized = tuple((int(row[0]), str(row[1]), str(row[2])) for row in history)
    except (IndexError, TypeError, ValueError):
        return False
    return (
        user_version == DATABASE_SCHEMA_VERSION
        and normalized in _current_migration_histories(migrations)
    )


def _normalized_migration_history(
    history: Sequence[tuple[object, ...]],
) -> tuple[tuple[int, str, str], ...] | None:
    try:
        return tuple((int(row[0]), str(row[1]), str(row[2])) for row in history)
    except (IndexError, TypeError, ValueError):
        return None


def _matches_exact_history(
    history: Sequence[tuple[object, ...]],
    user_version: int,
    expected: tuple[tuple[int, str, str], ...],
) -> bool:
    return (
        bool(expected)
        and user_version == expected[-1][0]
        and _normalized_migration_history(history) == expected
    )


def _is_supported_previous_v18_history(
    history: Sequence[tuple[object, ...]], user_version: int,
) -> bool:
    return any(
        _matches_exact_history(history, user_version, expected)
        for expected in _SUPPORTED_PREVIOUS_V18_HISTORIES
    )


def _is_supported_previous_v17_history(
    history: Sequence[tuple[object, ...]], user_version: int,
) -> bool:
    return any(
        _matches_exact_history(history, user_version, expected)
        for expected in _SUPPORTED_PREVIOUS_V17_HISTORIES
    )


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute("SELECT * FROM pragma_table_info(?)", (table,))
    }


def _copy_v17_question_text(connection: sqlite3.Connection) -> None:
    """Preserve exact V17 question/answer text in the current durable store."""
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    retired = {"question_batches", "question_items", "question_answers"}
    if not (tables & retired):
        return
    if not retired <= tables:
        raise ValueError("Cortex V17 durable-question storage is incomplete")
    rows = connection.execute(
        """SELECT b.batch_id,b.task_id,b.attempt_id,b.status,b.created_at,b.answered_at,
                  i.question_key,i.canonical_question,i.ordinal,a.answer_original
             FROM question_batches b
             JOIN question_items i ON i.batch_id=b.batch_id
             LEFT JOIN question_answers a
               ON a.batch_id=i.batch_id AND a.question_key=i.question_key
             ORDER BY b.task_id,b.created_at,b.batch_id,i.ordinal"""
    ).fetchall()
    task_revisions: dict[str, int] = {}
    task_sequences: dict[str, int] = {}
    for row in rows:
        task_id = str(row["task_id"])
        if task_id not in task_revisions:
            revision = connection.execute(
                "SELECT COALESCE(MAX(task_revision),1) FROM task_revisions WHERE task_id=?",
                (task_id,),
            ).fetchone()[0]
            task_revisions[task_id] = max(1, int(revision or 1))
            sequence = connection.execute(
                "SELECT COALESCE(MAX(published_sequence),0) FROM durable_questions WHERE task_id=?",
                (task_id,),
            ).fetchone()[0]
            task_sequences[task_id] = int(sequence or 0)
        task_sequences[task_id] += 1
        question = str(row["canonical_question"])
        answer = None if row["answer_original"] is None else str(row["answer_original"])
        status = str(row["status"])
        if answer is not None and status == "open":
            status = "answered"
        suffix = hashlib.sha256(
            f'{row["batch_id"]}\0{row["question_key"]}'.encode("utf-8")
        ).hexdigest()[:24]
        question_ref = f"question-v17-{suffix}"
        answer_ref = f"answer-v17-{suffix}" if answer is not None else None
        connection.execute(
            """INSERT INTO durable_questions(
                   question_ref,task_id,attempt_id,dispatch_ref,profile,task_revision,
                   attempt_generation,submission_id,question_category,question_text,status,content_digest,
                   published_sequence,answer,answer_submission_id,answer_digest,
                   answered_sequence,created_at,answered_at,superseded_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                question_ref, task_id, str(row["attempt_id"]),
                f'v17:{row["batch_id"]}', "general", task_revisions[task_id], 1,
                question_ref, _MIGRATED_PRE_V19_QUESTION_CATEGORY, question, status,
                durable_question_content_digest(_MIGRATED_PRE_V19_QUESTION_CATEGORY, question),
                task_sequences[task_id], answer, answer_ref,
                None if answer is None else hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                task_sequences[task_id] if answer is not None else None,
                str(row["created_at"]), row["answered_at"] if answer is not None else None,
                row["answered_at"] if status == "superseded" else None,
            ),
        )
    connection.execute("DROP TABLE question_answers")
    connection.execute("DROP TABLE question_items")
    connection.execute("DROP TABLE question_batches")


def durable_question_content_digest(question_category: str, question_text: str) -> str:
    """Bind one durable question digest to its canonical semantic category."""
    if (
        not isinstance(question_category, str)
        or not question_category
        or "\x00" in question_category
        or not isinstance(question_text, str)
    ):
        raise ValueError("durable question digest input is invalid")
    return hashlib.sha256((question_category + "\x00" + question_text).encode("utf-8")).hexdigest()


def _migrate_pre_v19_durable_question_categories(connection: sqlite3.Connection) -> None:
    """Classify released user-facing questions without inspecting their text.

    Every row admitted by the signed V17/V18 schema was already a durable user
    decision.  ``requirement`` is therefore the conservative current category:
    it preserves that released meaning without guessing from language or
    content.  This runs only inside the predecessor-to-V19 transaction.  A
    NULL/internal row introduced after V19 never passes through this cutover
    and remains non-authorizing at runtime.
    """
    rows = connection.execute(
        "SELECT question_ref,question_category,question_text,status,answer FROM durable_questions "
        "ORDER BY task_id,published_sequence,question_ref"
    ).fetchall()
    for row in rows:
        if row["question_category"] is not None:
            if row["question_category"] != _MIGRATED_PRE_V19_QUESTION_CATEGORY:
                raise ValueError("Cortex predecessor durable question has unexpected category state")
            continue
        question_text = row["question_text"]
        if not isinstance(question_text, str):
            raise ValueError("Cortex predecessor durable question text is invalid")
        answer = row["answer"]
        if row["status"] == "answered" and (not isinstance(answer, str) or not answer):
            raise ValueError("Cortex predecessor answered question has no exact answer")
        if answer is not None and not isinstance(answer, str):
            raise ValueError("Cortex predecessor durable answer text is invalid")
        cursor = connection.execute(
            "UPDATE durable_questions SET question_category=?,content_digest=?,answer_digest=? "
            "WHERE question_ref=? AND question_category IS NULL",
            (
                _MIGRATED_PRE_V19_QUESTION_CATEGORY,
                durable_question_content_digest(_MIGRATED_PRE_V19_QUESTION_CATEGORY, question_text),
                None if answer is None else hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                str(row["question_ref"]),
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("Cortex predecessor durable question migration did not converge")


def _upgrade_supported_previous_ledger(
    connection: sqlite3.Connection,
    *,
    history: Sequence[tuple[object, ...]],
    user_version: int,
) -> None:
    """Atomically append the exact V18/V19 cutover to a signed predecessor."""
    if not (
        _is_supported_previous_v17_history(history, user_version)
        or _is_supported_previous_v18_history(history, user_version)
    ):
        raise ValueError("Cortex database history is not an authorized upgrade source")

    durable_columns = _table_columns(connection, "durable_questions")
    if durable_columns:
        if "answer_text" in durable_columns and "answer" not in durable_columns:
            connection.execute("ALTER TABLE durable_questions RENAME COLUMN answer_text TO answer")
        elif "answer" not in durable_columns:
            raise ValueError("Cortex prior durable-question schema is invalid")
    else:
        _execute_migration_statements(connection, _DURABLE_QUESTION_SCHEMA_STATEMENTS)
    durable_columns = _table_columns(connection, "durable_questions")
    if "question_category" not in durable_columns:
        connection.execute("ALTER TABLE durable_questions ADD COLUMN question_category TEXT")
    _copy_v17_question_text(connection)
    _migrate_pre_v19_durable_question_categories(connection)

    repair_columns = _table_columns(connection, "repair_escrow")
    if "dispatch_ref_digest" in repair_columns and "allowed_paths_json" in repair_columns:
        connection.execute("ALTER TABLE repair_escrow RENAME COLUMN allowed_paths_json TO patch_paths_json")
    elif "dispatch_ref_digest" in repair_columns and "patch_paths_json" in repair_columns:
        pass
    elif {"task_ref_digest", "assignment_ref_digest", "allowed_paths_json"} <= repair_columns:
        connection.execute("DROP TRIGGER repair_escrow_immutable_update")
        connection.execute("DROP INDEX repair_escrow_task_attempt_idx")
        connection.execute("ALTER TABLE repair_escrow RENAME TO repair_escrow_retired_v17")
        _execute_migration_statements(connection, _REPAIR_ESCROW_SCHEMA_STATEMENTS)
        connection.execute("DROP TABLE repair_escrow_retired_v17")
    else:
        raise ValueError("Cortex prior repair escrow schema is invalid")

    if user_version == 17:
        connection.execute(
            "INSERT INTO schema_migrations(version,name,applied_at,checksum) VALUES(?,?,?,?)",
            (
                _RELEASED_V18_HARD_CUT_ROW[0],
                _RELEASED_V18_HARD_CUT_ROW[1],
                _now(),
                _RELEASED_V18_HARD_CUT_ROW[2],
            ),
        )
    migration = _migration_plan()[0]
    _record_migration(connection, migration)
    connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
    _assert_migration_schema(connection, DATABASE_SCHEMA_VERSION)


def _migrate_v19_current_orchestration_data(connection: sqlite3.Connection) -> None:
    """Apply every deterministic current-V19 orchestration data hard cut."""
    rows = connection.execute(
        "SELECT task_id,artifact_dir,state_json,plan_json FROM tasks "
        "WHERE plan_json IS NOT NULL ORDER BY task_number"
    ).fetchall()
    if not rows:
        return
    try:
        from cortex_runtime.orchestration_engine import (
            _migrate_compiled_assignment_identity,
            _migrate_coordinator_planning_package_identity,
        )
    except RuntimeError as exc:
        if "before the composition root bound dependencies" not in str(exc):
            raise
        # Standalone migration/marketplace checks import the ledger directly.
        # Loading the normal composition root supplies the same canonical
        # compiler bindings used by the MCP server; it does not create a
        # second storage implementation.
        __import__("cortex")
        from cortex_runtime.orchestration_engine import (
            _migrate_compiled_assignment_identity,
            _migrate_coordinator_planning_package_identity,
        )
    for row in rows:
        state = _decode_json(str(row["state_json"]), "V19 task state")
        plan = _decode_json(str(row["plan_json"]), "V19 orchestration plan")
        if not isinstance(plan.get("waves"), list):
            continue
        artifact_dir = Path(str(row["artifact_dir"]))
        if artifact_dir.is_absolute() or ".." in artifact_dir.parts:
            raise ValueError("V19 task artifact directory is unsafe")
        root = Path(connection.execute("PRAGMA database_list").fetchone()[2]).parent
        _migrate_compiled_assignment_identity(root / artifact_dir, state, plan)
        _migrate_coordinator_planning_package_identity(root / artifact_dir, state, plan)


def _persist_v19_migrated_plan(root: Path, task_id: str, plan: Mapping[str, Any]) -> None:
    """Replace only the same active plan row during the owning V19 transaction."""
    connection = _active_connections().get(_root_key(root))
    if connection is None:
        raise ValueError("V19 plan migration requires the owning schema transaction")
    payload = _canonical_json(dict(plan))
    updated = connection.execute(
        "UPDATE plan_revisions SET plan_json=? "
        "WHERE task_id=? AND status='active'",
        (payload, task_id),
    )
    if updated.rowcount != 1:
        raise ValueError("V19 plan migration requires one exact active plan revision")
    task_updated = connection.execute(
        "UPDATE tasks SET plan_json=? WHERE task_id=?", (payload, task_id),
    )
    if task_updated.rowcount != 1:
        raise ValueError("V19 plan migration task is unavailable")


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


def _migration_plan() -> tuple[_Migration, ...]:
    # Fresh installations have one compact current V19 schema. Supported
    # predecessor ledgers retain every released migration row and append this
    # exact row after their transactional data cutover.
    return (_Migration(
        DATABASE_SCHEMA_VERSION,
        "canonical-current-ledger",
        _BASE_SCHEMA_STATEMENTS
        + _ARTIFACT_SCHEMA_STATEMENTS
        + _CLOSURE_SCHEMA_STATEMENTS
        + _PROJECTION_SCHEMA_STATEMENTS
        + _PRUNE_SCHEMA_STATEMENTS
        + _REVISION_AWARE_ORCHESTRATION_SCHEMA_STATEMENTS
        + _GOVERNANCE_SCHEMA_STATEMENTS
        + _GOVERNANCE_INTEGRITY_SCHEMA_STATEMENTS
        + _GOVERNANCE_LIFECYCLE_INTEGRITY_SCHEMA_STATEMENTS
        + _GOVERNANCE_LIFECYCLE_ENVELOPE_AUTH_SCHEMA_STATEMENTS
        + _ATTEMPT_RESULT_EVENT_PROTOCOL_SCHEMA_STATEMENTS
        + _ATTEMPT_VERIFICATION_AUTHORITY_SCHEMA_STATEMENTS
        + _ATTEMPT_QUESTION_EVENT_SCHEMA_STATEMENTS
        + _DURABLE_QUESTION_SCHEMA_STATEMENTS
        + _REPAIR_ESCROW_SCHEMA_STATEMENTS,
    ),)


def _expected_schema_objects() -> set[str]:
    """Derive final V19 object names from the one active migration plan."""
    objects: set[str] = set()
    for statement in _migration_plan()[0].statements:
        sql = " ".join(str(statement).split())
        create = re.match(r"CREATE(?: UNIQUE)? (?:TABLE|INDEX|TRIGGER) (?:IF NOT EXISTS )?([A-Za-z0-9_]+)", sql, re.IGNORECASE)
        if create:
            objects.add(create.group(1))
        drop = re.match(r"DROP (?:TABLE|INDEX|TRIGGER) (?:IF EXISTS )?([A-Za-z0-9_]+)", sql, re.IGNORECASE)
        if drop:
            objects.discard(drop.group(1))
        rename = re.match(r"ALTER TABLE ([A-Za-z0-9_]+) RENAME TO ([A-Za-z0-9_]+)", sql, re.IGNORECASE)
        if rename:
            objects.discard(rename.group(1))
            objects.add(rename.group(2))
    return objects


def _assert_migration_schema(connection: sqlite3.Connection, version: int) -> None:
    """Validate the complete current V19 schema without issuing DDL."""
    if version != DATABASE_SCHEMA_VERSION:
        raise ValueError("Cortex database schema version is unsupported")
    history = [tuple(row) for row in connection.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    )]
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if not _is_current_migration_history(history, user_version, _migration_plan()):
        raise ValueError("Cortex database requires the current migration")
    present = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'index', 'trigger')"
        )
    }
    if _expected_schema_objects() - present:
        raise ValueError("Cortex database schema is missing required objects")
    for table in ("schema_migrations", "tasks", "attempt_results", "attempt_events", "repair_escrow", "durable_questions"):
        columns = {str(row[1]) for row in connection.execute("SELECT * FROM pragma_table_info(?)", (table,))}
        if not columns:
            raise ValueError("Cortex database schema table is unavailable")


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
        history = [tuple(row) for row in connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        )]
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.OperationalError as exc:
        raise ValueError("Cortex database schema is unavailable inside an active transaction") from exc
    migrations = _migration_plan()
    if not _is_current_migration_history(history, user_version, migrations):
        raise ValueError("Cortex database requires migration before this nested operation")
    _assert_migration_schema(connection, DATABASE_SCHEMA_VERSION)


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
    expected_histories = {
        hashlib.sha256(_canonical_json(history).encode("utf-8")).hexdigest()
        for history in _current_migration_histories(migrations)
    }
    if (
        readiness.history_fingerprint not in expected_histories
        or readiness.user_version != DATABASE_SCHEMA_VERSION
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


def _requires_v11_quarantine(root: Path) -> bool:
    """Recognize only the complete exact signed V1..V8 storage lineage."""
    path = database_path(root)
    if not path.exists():
        return False
    _assert_private_regular(path, "Cortex database")
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        has_history = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone() is not None
        if not has_history:
            return False
        history = [tuple(row) for row in connection.execute(
            "SELECT version,name,checksum FROM schema_migrations ORDER BY version"
        )]
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error:
        return False
    finally:
        if "connection" in locals():
            connection.close()
    return _matches_exact_history(
        history, user_version, _SUPPORTED_PREVIOUS_V11_V8_HISTORY,
    )


def _quarantine_v11_namespace(root: Path) -> Path:
    """Preserve an authorized V11 namespace before current-only bootstrap."""
    archive = root / (
        "previous-v11-ledger-"
        + _now().replace("-", "").replace(":", "").replace(".", "")
        + "-"
        + secrets.token_hex(8)
    )
    archive.mkdir(mode=0o700)
    children = [item for item in root.iterdir() if item != archive]
    for item in children:
        if item.name == ".cortex-bootstrap.lock":
            continue
        os.replace(item, archive / item.name)
    lifecycle_key = _governance_lifecycle_key_path(root)
    if lifecycle_key.exists():
        _assert_private_regular(lifecycle_key, "Cortex governance lifecycle key")
        os.replace(lifecycle_key, archive / "governance-lifecycle.key")
        _fsync_directory(lifecycle_key.parent)
    _fsync_directory(archive)
    _fsync_directory(root)
    return archive


def ensure_database(root: Path) -> None:
    """Open current V19 or atomically append-upgrade an exact predecessor."""
    if _root_key(root) in _active_connections():
        _assert_current_migration_history(_active_connections()[_root_key(root)])
        return
    migrations = _migration_plan()
    if _database_readiness_is_current(root, migrations):
        return
    with _database_bootstrap_lock(root):
        if _requires_v11_quarantine(root):
            _forget_database_readiness(root)
            _quarantine_v11_namespace(root)
        with transaction(root):
            connection = _active_connections()[_root_key(root)]
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            has_history = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone() is not None
            applied = _applied_migrations(connection) if has_history else {}
            history = [tuple(row) for row in connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            )] if has_history else []
            user_objects = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table','index','trigger','view') "
                "AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone() is not None
            if user_objects and not has_history:
                raise ValueError("Cortex database has no current migration history")
            if applied:
                if (
                    _is_supported_previous_v17_history(history, user_version)
                    or _is_supported_previous_v18_history(history, user_version)
                ):
                    _upgrade_supported_previous_ledger(
                        connection, history=history, user_version=user_version,
                    )
                elif not _is_current_migration_history(history, user_version, migrations):
                    raise ValueError("Cortex database does not match the current v19 migration")
                else:
                    _assert_migration_schema(connection, DATABASE_SCHEMA_VERSION)
            else:
                migration = migrations[0]
                _execute_migration_statements(connection, migration.statements)
                _record_migration(connection, migration)
                connection.execute(f"PRAGMA user_version = {migration.version}")
            _assert_migration_schema(connection, DATABASE_SCHEMA_VERSION)
            _migrate_v19_current_orchestration_data(connection)
            _assert_migration_schema(connection, DATABASE_SCHEMA_VERSION)
    _cache_database_readiness(root, migrations)


def migration_history(root: Path) -> list[dict[str, Any]]:
    ensure_database(root)
    with _connection(root) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT version, name, applied_at, checksum FROM schema_migrations ORDER BY version"
        )]


def _repair_escrow_basis(
    *,
    task_id: str,
    attempt_id: str,
    dispatch_ref_digest: str,
    kind: str,
    base_payload_digest: str,
    payload: Mapping[str, Any],
    diagnostics: Sequence[Mapping[str, Any]],
    patch_paths: Sequence[str],
) -> dict[str, Any]:
    """Return the immutable content bound by one signed repair handle."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", task_id):
        raise ValueError("repair escrow task identity is invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", attempt_id):
        raise ValueError("repair escrow attempt identity is invalid")
    for label, value in (
        ("dispatch_ref_digest", dispatch_ref_digest),
        ("base_payload_digest", base_payload_digest),
    ):
        if re.fullmatch(r"sha256:[0-9a-f]{64}", str(value or "")) is None:
            raise ValueError(f"repair escrow {label} is invalid")
    # The public v11 contract has one semantic completion payload. Persist only
    # the current report target.
    if kind != "report":
        raise ValueError("repair escrow kind is invalid")
    if not isinstance(payload, Mapping):
        raise ValueError("repair escrow payload must be an object")
    if not isinstance(diagnostics, Sequence) or isinstance(diagnostics, (str, bytes)) or not diagnostics:
        raise ValueError("repair escrow diagnostics are invalid")
    if not isinstance(patch_paths, Sequence) or isinstance(patch_paths, (str, bytes)) or not patch_paths:
        raise ValueError("repair escrow patch paths are invalid")
    normalized_diagnostics = [dict(item) for item in diagnostics if isinstance(item, Mapping)]
    normalized_paths = [str(item) for item in patch_paths if isinstance(item, str) and item.startswith("/")]
    if len(normalized_diagnostics) != len(diagnostics) or len(normalized_paths) != len(patch_paths):
        raise ValueError("repair escrow diagnostics or patch paths are invalid")
    return {
        "schema": "cortex/private-repair-escrow/v1",
        "task_id": task_id,
        "attempt_id": attempt_id,
        "dispatch_ref_digest": dispatch_ref_digest,
        "kind": kind,
        "base_payload_digest": base_payload_digest,
        "payload": dict(payload),
        "diagnostics": normalized_diagnostics,
        "patch_paths": normalized_paths,
    }


def store_repair_escrow(
    root: Path,
    *,
    task_id: str,
    attempt_id: str,
    dispatch_ref_digest: str,
    kind: str,
    base_payload_digest: str,
    payload: Mapping[str, Any],
    diagnostics: Sequence[Mapping[str, Any]],
    patch_paths: Sequence[str],
) -> dict[str, Any]:
    """Create or reuse one task-lifetime private repair escrow row."""
    basis = _repair_escrow_basis(
        task_id=task_id,
        attempt_id=attempt_id,
        dispatch_ref_digest=dispatch_ref_digest,
        kind=kind,
        base_payload_digest=base_payload_digest,
        payload=payload,
        diagnostics=diagnostics,
        patch_paths=patch_paths,
    )
    payload_json = _canonical_json(basis["payload"])
    diagnostics_json = _canonical_json(basis["diagnostics"])
    patch_paths_json = _canonical_json(basis["patch_paths"])
    escrow_digest = hashlib.sha256(_canonical_json(basis).encode("utf-8")).hexdigest()
    ensure_database(root)
    with _connection(root, write=True) as connection:
        task_row = connection.execute(
            "SELECT state_json FROM tasks WHERE task_id=?", (task_id,),
        ).fetchone()
        if task_row is None:
            raise ValueError("repair escrow task is unavailable")
        state = _decode_json(str(task_row["state_json"]), "repair escrow task state")
        if not any(
            isinstance(item, Mapping) and str(item.get("attempt_id") or "") == attempt_id
            for item in state.get("attempts", [])
        ):
            raise ValueError("repair escrow attempt is unavailable")
        # One rejected draft becomes the immutable repair base for the whole
        # active attempt.  A later full resubmission (or a concurrent second
        # rejection) must not manufacture a new capsule and silently replace
        # the diagnostic scope already returned to the worker.
        existing = connection.execute(
            "SELECT * FROM repair_escrow WHERE task_id=? AND attempt_id=? ORDER BY created_at, handle_digest LIMIT 1",
            (task_id, attempt_id),
        ).fetchone()
        if existing is None:
            handle_id = secrets.token_urlsafe(16)
            if re.fullmatch(r"[A-Za-z0-9_-]{22}", handle_id) is None:
                raise RuntimeError("repair handle generation failed")
            handle_digest = hashlib.sha256(handle_id.encode("ascii")).hexdigest()
            created_at = _now()
            connection.execute(
                "INSERT INTO repair_escrow(handle_digest,handle_id,task_id,attempt_id,dispatch_ref_digest,kind,base_payload_digest,payload_json,diagnostics_json,patch_paths_json,escrow_digest,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    handle_digest, handle_id, task_id, attempt_id, dispatch_ref_digest,
                    kind, base_payload_digest, payload_json,
                    diagnostics_json, patch_paths_json, escrow_digest, created_at,
                ),
            )
            existing = connection.execute(
                "SELECT * FROM repair_escrow WHERE handle_digest=?", (handle_digest,),
            ).fetchone()
        if existing is None:
            raise ValueError("repair escrow could not be read after persistence")
        row = dict(existing)
    # A pre-existing row with different semantic content is the exact pending
    # repair, not a collision and not permission to replace the rejected base.
    # Its authenticated contents are returned so the caller can reissue the
    # same opaque contract.  Task/attempt ownership was checked above.
    return _validated_repair_escrow_row(row)


def _validated_repair_escrow_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Decode and authenticate every immutable escrow field before use."""
    value = dict(row)
    handle_id = str(value.get("handle_id") or "")
    handle_digest = str(value.get("handle_digest") or "")
    if not hmac.compare_digest(
        hashlib.sha256(handle_id.encode("ascii", errors="strict")).hexdigest(),
        handle_digest,
    ):
        raise ValueError("repair escrow integrity check failed")
    payload = _decode_json(str(value.get("payload_json") or ""), "repair escrow payload")
    diagnostics = _decode_json_list(str(value.get("diagnostics_json") or ""), "repair escrow diagnostics")
    patch_paths = _decode_json_list(str(value.get("patch_paths_json") or ""), "repair escrow patch paths")
    basis = _repair_escrow_basis(
        task_id=str(value.get("task_id") or ""),
        attempt_id=str(value.get("attempt_id") or ""),
        dispatch_ref_digest=str(value.get("dispatch_ref_digest") or ""),
        kind=str(value.get("kind") or ""),
        base_payload_digest=str(value.get("base_payload_digest") or ""),
        payload=payload,
        diagnostics=diagnostics,
        patch_paths=patch_paths,
    )
    observed = str(value.get("escrow_digest") or "")
    expected = hashlib.sha256(_canonical_json(basis).encode("utf-8")).hexdigest()
    if re.fullmatch(r"[0-9a-f]{64}", observed) is None or not hmac.compare_digest(expected, observed):
        raise ValueError("repair escrow integrity check failed")
    return {
        **value,
        "payload": payload,
        "diagnostics": diagnostics,
        "patch_paths": patch_paths,
    }


def get_repair_escrow(root: Path, *, handle_digest: str) -> dict[str, Any] | None:
    """Read one private repair escrow row by the digest of its random id."""
    if re.fullmatch(r"[0-9a-f]{64}", str(handle_digest or "")) is None:
        raise ValueError("repair handle digest is invalid")
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT * FROM repair_escrow WHERE handle_digest=?", (handle_digest,),
        ).fetchone()
    if row is None:
        return None
    return _validated_repair_escrow_row(dict(row))


def get_pending_repair_escrow(
    root: Path,
    *,
    task_id: str,
    attempt_id: str,
) -> dict[str, Any] | None:
    """Read the one immutable repair contract bound to an active attempt.

    More than one row indicates state produced outside the locked v11 state
    machine and fails closed as an integrity error.  Successful completion is
    tracked by the canonical AttemptResult, so no mutable "consumed" bit is
    required on the forensic escrow row.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", str(task_id or "")):
        raise ValueError("repair escrow task identity is invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", str(attempt_id or "")):
        raise ValueError("repair escrow attempt identity is invalid")
    ensure_database(root)
    with _connection(root) as connection:
        rows = connection.execute(
            "SELECT * FROM repair_escrow WHERE task_id=? AND attempt_id=? ORDER BY created_at, handle_digest LIMIT 2",
            (task_id, attempt_id),
        ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("repair escrow integrity check failed: multiple pending contracts")
    return _validated_repair_escrow_row(dict(rows[0]))


def _upsert_task_finding_connection(
    connection: Any,
    task_id: str,
    finding: dict[str, Any],
    *,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge one canonical finding using an existing transaction."""
    finding = dict(finding)
    finding["severity"] = normalize_finding_severity(finding.get("severity"))
    finding["blocking"] = bool(finding.get("blocking")) or finding_severity_is_intrinsically_blocking(
        finding["severity"]
    )
    if str(finding.get("status") or "") == "resolved":
        raise ValueError("canonical findings must be resolved through exact server evidence")
    fingerprint = str(finding["fingerprint"])
    source = source or {}
    row = connection.execute(
        "SELECT * FROM task_findings WHERE task_id=? AND fingerprint=?",
        (task_id, fingerprint),
    ).fetchone()
    now = _now()
    evidence = []
    if row:
        try:
            evidence = json.loads(str(row["source_evidence_json"]))
        except json.JSONDecodeError:
            evidence = []
    if source and source not in evidence:
        evidence.append(source)
    if row:
        status = str(finding["status"])
        if str(row["status"]) == "open" and status == "open":
            severity = max(
                (str(row["severity"]), str(finding["severity"])),
                key=lambda value: CANONICAL_FINDING_SEVERITY_RANK[value],
            )
            blocking = bool(row["blocking"]) or bool(finding["blocking"])
        else:
            severity = finding["severity"]
            blocking = bool(finding["blocking"])
        connection.execute(
            "UPDATE task_findings SET severity=?, status=?, blocking=?, summary=?, details=?, "
            "next_action_json=?, source_evidence_json=?, waiver_reason=?, waived_by=?, waived_at=?, "
            "resolved_at=?, updated_at=? WHERE task_id=? AND fingerprint=?",
            (
                severity, status, int(blocking), finding["summary"],
                _canonical_json(finding.get("details")) if isinstance(finding.get("details"), (dict, list)) else finding.get("details"),
                _canonical_json(finding.get("next_action")) if isinstance(finding.get("next_action"), (dict, list)) else finding.get("next_action"),
                _canonical_json(evidence), finding.get("waiver_reason"), finding.get("waived_by"),
                finding.get("waived_at"), finding.get("resolved_at"), now, task_id, fingerprint,
            ),
        )
    else:
        connection.execute(
            "INSERT INTO task_findings(task_id,fingerprint,severity,status,blocking,summary,details,"
            "next_action_json,source_evidence_json,waiver_reason,waived_by,waived_at,resolved_at,"
            "first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id, fingerprint, finding["severity"], finding["status"], int(finding["blocking"]),
                finding["summary"],
                _canonical_json(finding.get("details")) if isinstance(finding.get("details"), (dict, list)) else finding.get("details"),
                _canonical_json(finding.get("next_action")) if isinstance(finding.get("next_action"), (dict, list)) else finding.get("next_action"),
                _canonical_json(evidence), finding.get("waiver_reason"), finding.get("waived_by"),
                finding.get("waived_at"), finding.get("resolved_at"), now, now,
            ),
        )
    return finding | {"task_id": task_id, "source_evidence": evidence}


def upsert_task_finding(root: Path, task_id: str, finding: dict[str, Any], *, source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge one closure finding, keyed by task and stable fingerprint."""
    ensure_database(root)
    with _connection(root, write=True) as connection:
        return _upsert_task_finding_connection(connection, task_id, finding, source=source)


def materialize_attempt_findings(
    connection: Any,
    *,
    task_id: str,
    attempt_id: str,
    result_ref: str,
    gate: str | None,
    task_revision: int | None,
    findings: Sequence[Any],
) -> list[dict[str, Any]]:
    """Materialize AttemptResult findings in the completion transaction."""
    materialized: list[dict[str, Any]] = []
    for index, raw in enumerate(findings):
        value = dict(raw) if isinstance(raw, Mapping) else {"details": raw}
        fingerprint = str(value.get("fingerprint") or "").strip()
        if not fingerprint:
            identity = {
                key: item for key, item in value.items()
                if key not in {"source_evidence", "evidence", "evidence_refs"}
            }
            fingerprint = "finding-" + hashlib.sha256(
                _canonical_json(identity).encode("utf-8")
            ).hexdigest()[:32]
        severity = normalize_finding_severity(value.get("severity"), default="info")
        status = str(value.get("status") or "open").strip().lower()
        if status not in {"open", "resolved", "waived"}:
            status = "open"
        blocking_value = value.get("blocking")
        blocking = (
            bool(blocking_value) if blocking_value is not None else False
        ) or finding_severity_is_intrinsically_blocking(severity)
        summary = str(value.get("summary") or value.get("message") or "AttemptResult finding").strip()
        if not summary:
            summary = "AttemptResult finding"
        details = value.get("details")
        if details is None:
            details = {
                key: item for key, item in value.items()
                if key not in {
                    "fingerprint", "severity", "status", "blocking", "summary", "message",
                    "next_action", "waiver_reason", "waived_by", "waived_at", "resolved_at",
                }
            }
        finding = {
            "fingerprint": fingerprint, "severity": severity, "status": status,
            "blocking": blocking, "summary": summary, "details": details,
        }
        for key in ("next_action", "waiver_reason", "waived_by", "waived_at", "resolved_at"):
            if key in value:
                finding[key] = value[key]
        source = {
            "transition": "opened",
            "source_type": "attempt_result", "attempt_id": attempt_id,
            "attempt_result_ref": result_ref, "origin_result_ref": result_ref,
            "finding_index": index,
        }
        if gate:
            source["gate"] = gate
        if isinstance(task_revision, int) and not isinstance(task_revision, bool) and task_revision >= 1:
            source["task_revision"] = task_revision
        for key in ("evidence_ref", "evidence_refs", "source_ref", "source_evidence"):
            if key in value:
                source[key] = value[key]
        materialized.append(_upsert_task_finding_connection(connection, task_id, finding, source=source))
    return materialized


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

    P0/P1/P2 are intrinsically blocking. P3 and info remain advisory unless
    authoritative server evidence explicitly marks them blocking.
    """
    return [
        item
        for item in list_task_findings(root, task_id, include_resolved=True)
        if item.get("status") == "open"
        and (finding_severity_is_intrinsically_blocking(item["severity"]) or item["blocking"])
    ]


def require_no_task_finding_blockers(root: Path, task_id: str, *, operation: str) -> None:
    """Reject a terminal transition while authoritative findings stay open."""
    if operation not in {"close", "handoff"}:
        raise ValueError("canonical finding blocker guard operation is unsupported")
    if task_findings_blockers(root, task_id):
        raise ValueError(f"{operation}_blocked_by_open_canonical_findings")


def resolve_task_finding(
    root: Path,
    task_id: str,
    fingerprint: str,
    *,
    origin_result_refs: Sequence[str],
    resolving_attempt_result_ref: str,
    origin_gate: str,
    resolving_gate: str,
    task_revision: int,
) -> dict[str, Any] | None:
    """Resolve one finding only from its exact corrective/verifier lineage.

    The originating result(s), corrective result receipt(s), current verifier
    result, gate, and semantic task revision must all match durable rows. A
    verifier that repeats the same finding cannot resolve it by omission.
    """
    origins = tuple(dict.fromkeys(
        str(item).strip() for item in origin_result_refs if str(item).strip()
    ))
    resolver_ref = str(resolving_attempt_result_ref or "").strip()
    opened_gate = str(origin_gate or "").strip()
    verifier_gate = str(resolving_gate or "").strip()
    if not origins or not resolver_ref or not opened_gate or not verifier_gate or task_revision < 1:
        return None
    ensure_database(root)
    with _connection(root, write=True) as connection:
        row = connection.execute(
            "SELECT * FROM task_findings WHERE task_id=? AND fingerprint=?",
            (task_id, fingerprint),
        ).fetchone()
        if row is None:
            return None
        try:
            evidence = json.loads(str(row["source_evidence_json"]))
        except json.JSONDecodeError:
            return None
        if not isinstance(evidence, list):
            return None
        if str(row["status"]) == "resolved":
            exact_resolution = any(
                isinstance(item, Mapping)
                and item.get("transition") == "resolved"
                and str(item.get("origin_gate") or "") == opened_gate
                and str(item.get("gate") or "") == verifier_gate
                and list(item.get("origin_result_refs") or []) == list(origins)
                and str(item.get("attempt_result_ref") or "") == resolver_ref
                and int(item.get("task_revision") or 0) == task_revision
                for item in evidence
            )
            if not exact_resolution:
                return None
            return {"fingerprint": fingerprint, "status": "resolved", "idempotent": True}
        if str(row["status"]) != "open":
            return None
        opened = {
            str(item.get("origin_result_ref") or item.get("attempt_result_ref") or "")
            for item in evidence
            if isinstance(item, Mapping)
            and item.get("transition") == "opened"
            and str(item.get("gate") or "") == opened_gate
            and int(item.get("task_revision") or 0) == task_revision
        }
        if opened != set(origins):
            return None
        corrective_evidence = [
            item for item in evidence
            if isinstance(item, Mapping)
            and item.get("transition") == "corrective_reported"
            and int(item.get("task_revision") or 0) == task_revision
            and str(item.get("attempt_result_ref") or "")
        ]
        corrective_origins = {
            str(item.get("origin_result_ref") or "") for item in corrective_evidence
        }
        if corrective_origins != set(origins):
            return None
        for origin in origins:
            receipts = [
                item for item in corrective_evidence
                if str(item.get("origin_result_ref") or "") == origin
            ]
            receipt_verified = False
            for receipt in receipts:
                receipt_ref = str(receipt.get("attempt_result_ref") or "")
                receipt_gate = str(receipt.get("gate") or "")
                result_row = connection.execute(
                    "SELECT result_status,lifecycle_status,metadata_json FROM attempt_results "
                    "WHERE task_id=? AND result_ref=?",
                    (task_id, receipt_ref),
                ).fetchone()
                if (
                    result_row is None
                    or str(result_row["result_status"]) != "completed"
                    or str(result_row["lifecycle_status"]) != "COMPLETED"
                ):
                    continue
                try:
                    result_metadata = json.loads(str(result_row["metadata_json"]))
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(result_metadata, Mapping)
                    and receipt_gate
                    and str(result_metadata.get("phase") or "") == receipt_gate
                    and int(result_metadata.get("task_revision") or 0) == task_revision
                ):
                    receipt_verified = True
                    break
            if not receipt_verified:
                return None
        resolver = connection.execute(
            "SELECT result_status,lifecycle_status,metadata_json FROM attempt_results "
            "WHERE task_id=? AND result_ref=?",
            (task_id, resolver_ref),
        ).fetchone()
        if (
            resolver is None
            or str(resolver["result_status"]) != "completed"
            or str(resolver["lifecycle_status"]) != "COMPLETED"
        ):
            return None
        try:
            metadata = json.loads(str(resolver["metadata_json"]))
        except json.JSONDecodeError:
            return None
        if (
            not isinstance(metadata, Mapping)
            or str(metadata.get("phase") or "") != verifier_gate
            or int(metadata.get("task_revision") or 0) != task_revision
        ):
            return None
        if any(
            isinstance(item, Mapping)
            and item.get("transition") == "opened"
            and str(item.get("attempt_result_ref") or "") == resolver_ref
            for item in evidence
        ):
            return None
        stamp = _now()
        resolution = {
            "transition": "resolved",
            "source_type": "origin_verifier",
            "origin_gate": opened_gate,
            "gate": verifier_gate,
            "origin_result_refs": list(origins),
            "attempt_result_ref": resolver_ref,
            "task_revision": task_revision,
        }
        if resolution not in evidence:
            evidence.append(resolution)
        connection.execute(
            "UPDATE task_findings SET status='resolved',blocking=0,source_evidence_json=?,"
            "resolved_at=?,updated_at=? WHERE task_id=? AND fingerprint=? AND status='open'",
            (_canonical_json(evidence), stamp, stamp, task_id, fingerprint),
        )
    resolved = next((
        item for item in list_task_findings(root, task_id, include_resolved=True)
        if str(item.get("fingerprint") or "") == fingerprint
    ), None)
    return resolved if isinstance(resolved, dict) and resolved.get("status") == "resolved" else None


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
                    original,
                    "not_required",
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
        active = connection.execute(
            "SELECT plan_revision,plan_json FROM plan_revisions WHERE task_id = ? AND status = 'active'",
            (task_id,),
        ).fetchone()
        active_plan = (
            _decode_json(str(active["plan_json"]), "active plan revision")
            if active is not None and active["plan_json"] is not None else None
        )
        candidate = canonical_json.normalize(plan)
        if not isinstance(candidate, dict):
            raise ValueError("SQLite orchestration plan must be an object")
        if active_plan != candidate:
            row = connection.execute(
                "SELECT COALESCE(MAX(plan_revision), 0) AS value FROM plan_revisions WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            next_revision = int(row["value"]) + 1
            candidate["plan_revision"] = next_revision
            plan["plan_revision"] = next_revision
            task_revision = connection.execute(
                "SELECT COALESCE(MAX(task_revision), 1) FROM task_revisions WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE plan_revisions SET status = 'superseded' WHERE task_id = ? AND status = 'active'",
                (task_id,),
            )
            connection.execute(
                """INSERT INTO plan_revisions(task_id, plan_revision, base_plan_revision, task_revision, impact_json, plan_json, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
                (
                    task_id,
                    next_revision,
                    next_revision - 1 if next_revision > 1 else None,
                    int(task_revision),
                    _canonical_json({
                        "classification": "initial" if next_revision == 1 else "runtime_plan_update"
                    }),
                    _canonical_json(candidate),
                    _now(),
                ),
            )
        cursor = connection.execute(
            "UPDATE tasks SET plan_json = ? WHERE task_id = ?", (_canonical_json(candidate), task_id)
        )
        if cursor.rowcount != 1:
            raise ValueError("SQLite orchestration plan refers to an unknown task")


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
                    initial,
                    "not_required",
                    str(task["created_at"]),
                ),
            )
            next_revision = 2
        created_at = _now()
        connection.execute(
            """INSERT INTO task_revisions(task_id, task_revision, base_revision, source, message_original, message_language, message_en, translation_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, next_revision, next_revision - 1, source, original, language, canonical or original,
                "not_required",
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
        plan_snapshot = canonical_json.normalize(plan) if plan is not None else None
        if isinstance(plan_snapshot, dict):
            plan_snapshot["plan_revision"] = next_revision
            plan["plan_revision"] = next_revision
        plan_digest = canonical_json.digest(plan_snapshot) if plan_snapshot is not None else None
        connection.execute("UPDATE plan_revisions SET status = 'superseded' WHERE task_id = ? AND status = 'active'", (task_id,))
        created_at = _now()
        connection.execute(
            """INSERT INTO plan_revisions(task_id, plan_revision, base_plan_revision, task_revision, impact_json, plan_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, next_revision, next_revision - 1 if next_revision > 1 else None, int(task_revision),
                _canonical_json(impact), _canonical_json(plan_snapshot) if plan_snapshot is not None else None, status, created_at,
            ),
        )
    return {
        "task_id": task_id,
        "plan_revision": next_revision,
        "task_revision": task_revision,
        "impact": impact,
        "plan_digest": plan_digest,
        "status": status,
        "created_at": created_at,
    }


def find_plan_revision_by_impact(
    root: Path,
    task_id: str,
    *,
    classification: str,
    selectors: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return the latest immutable receipt matching exact server-owned impact facts."""
    ensure_database(root)
    with _connection(root, write=False) as connection:
        rows = connection.execute(
            "SELECT * FROM plan_revisions WHERE task_id = ? ORDER BY plan_revision DESC",
            (task_id,),
        ).fetchall()
    for row in rows:
        impact = _decode_json(str(row["impact_json"]), "plan revision impact")
        if impact.get("classification") != classification:
            continue
        if any(impact.get(key) != value for key, value in selectors.items()):
            continue
        plan = (
            _decode_json(str(row["plan_json"]), "plan revision plan")
            if row["plan_json"] is not None else None
        )
        return {
            "task_id": str(row["task_id"]),
            "plan_revision": int(row["plan_revision"]),
            "base_plan_revision": int(row["base_plan_revision"]) if row["base_plan_revision"] is not None else None,
            "task_revision": int(row["task_revision"]),
            "impact": impact,
            "plan": plan,
            "plan_digest": canonical_json.digest(plan) if plan is not None else None,
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
        }
    return None


def get_active_plan_revision(root: Path, task_id: str) -> dict[str, Any] | None:
    """Return the sole active immutable plan receipt and its canonical digest."""
    ensure_database(root)
    with _connection(root, write=False) as connection:
        rows = connection.execute(
            "SELECT * FROM plan_revisions WHERE task_id = ? AND status = 'active' ORDER BY plan_revision DESC LIMIT 2",
            (task_id,),
        ).fetchall()
        task_row = connection.execute(
            "SELECT plan_json FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("canonical task has multiple active plan revisions")
    row = rows[0]
    plan = (
        _decode_json(str(row["plan_json"]), "active plan revision")
        if row["plan_json"] is not None else None
    )
    impact = _decode_json(str(row["impact_json"]), "active plan revision impact")
    current_plan = (
        _decode_json(str(task_row["plan_json"]), "current orchestration plan")
        if task_row is not None and task_row["plan_json"] is not None else None
    )
    return {
        "task_id": str(row["task_id"]),
        "plan_revision": int(row["plan_revision"]),
        "base_plan_revision": int(row["base_plan_revision"]) if row["base_plan_revision"] is not None else None,
        "task_revision": int(row["task_revision"]),
        "impact": impact,
        "plan": plan,
        "plan_digest": canonical_json.digest(plan) if plan is not None else None,
        "current_plan": current_plan,
        "current_plan_digest": canonical_json.digest(current_plan) if current_plan is not None else None,
        "current_plan_matches": current_plan == plan,
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
    }


def get_plan_revision(root: Path, task_id: str, plan_revision: int) -> dict[str, Any] | None:
    """Return one exact immutable plan receipt by task and revision."""
    if isinstance(plan_revision, bool) or not isinstance(plan_revision, int) or plan_revision < 1:
        raise ValueError("plan_revision must be a positive integer")
    ensure_database(root)
    with _connection(root, write=False) as connection:
        row = connection.execute(
            "SELECT * FROM plan_revisions WHERE task_id = ? AND plan_revision = ?",
            (task_id, plan_revision),
        ).fetchone()
    if row is None:
        return None
    plan = (
        _decode_json(str(row["plan_json"]), "plan revision")
        if row["plan_json"] is not None else None
    )
    if not isinstance(plan, dict):
        return None
    return {
        "task_id": str(row["task_id"]),
        "plan_revision": int(row["plan_revision"]),
        "task_revision": int(row["task_revision"]),
        "plan": plan,
        "plan_digest": canonical_json.digest(plan),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
    }


def _close_assignment_revision_authority(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    attempt: Mapping[str, Any],
    result_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one close occurrence without trusting public task revision metadata."""
    assignment_revision = attempt.get("assignment_task_revision")
    plan_revision = attempt.get("plan_revision")
    identity = result_metadata.get("identity")
    if (
        str(attempt.get("gate") or "") != "governance_close"
        or str(attempt.get("operation_kind") or "") != "close"
        or isinstance(assignment_revision, bool)
        or not isinstance(assignment_revision, int)
        or assignment_revision < 1
        or isinstance(plan_revision, bool)
        or not isinstance(plan_revision, int)
        or plan_revision < 1
        or "task_revision" in result_metadata
        or not isinstance(identity, Mapping)
        or str(identity.get("attempt_id") or "") != str(attempt.get("attempt_id") or "")
        or str(identity.get("dispatch_ref") or "") != str(attempt.get("dispatch_ref") or "")
        or result_metadata.get("plan_revision") != plan_revision
    ):
        raise ValueError("governance close revision authority is not bound to the exact private assignment occurrence")

    exact_row = connection.execute(
        "SELECT task_revision,plan_json FROM plan_revisions "
        "WHERE task_id=? AND plan_revision=?",
        (task_id, plan_revision),
    ).fetchone()
    active_rows = connection.execute(
        "SELECT plan_json FROM plan_revisions WHERE task_id=? AND status='active' "
        "ORDER BY plan_revision DESC LIMIT 2",
        (task_id,),
    ).fetchall()
    task_row = connection.execute(
        "SELECT plan_json FROM tasks WHERE task_id=?", (task_id,),
    ).fetchone()
    current_revision_row = connection.execute(
        "SELECT task_revision FROM task_revisions WHERE task_id=? "
        "ORDER BY task_revision DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if exact_row is None or len(active_rows) != 1 or task_row is None or current_revision_row is None:
        raise ValueError("governance close revision authority is unavailable")
    exact_plan = _decode_json(str(exact_row["plan_json"]), "governance close plan receipt")
    active_plan = _decode_json(str(active_rows[0]["plan_json"]), "active governance close plan receipt")
    current_plan = _decode_json(str(task_row["plan_json"]), "current governance close plan")
    if not all(isinstance(item, dict) for item in (exact_plan, active_plan, current_plan)):
        raise ValueError("governance close plan authority is invalid")
    def digest_ref(value: object) -> str:
        digest = str(value or "").strip().lower()
        if digest.startswith("sha256:"):
            digest = digest[7:]
        return "sha256:" + digest if re.fullmatch(r"[0-9a-f]{64}", digest) else ""

    exact_digest = digest_ref(canonical_json.digest(exact_plan))

    if not (
        int(exact_row["task_revision"]) == assignment_revision
        and int(current_revision_row["task_revision"]) == assignment_revision
        and digest_ref(attempt.get("plan_digest")) == exact_digest
        and digest_ref(result_metadata.get("plan_digest")) == exact_digest
        and active_plan == current_plan
        and executable_plan_projection(exact_plan) == executable_plan_projection(current_plan)
    ):
        raise ValueError("governance close assignment is stale for the immutable plan or current task revision")
    return {
        "task_revision": assignment_revision,
        "plan_revision": plan_revision,
        "plan_digest": exact_digest,
    }


def validate_close_assignment_revision_authority_in_transaction(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    attempt: Mapping[str, Any],
    result_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate close occurrence revision authority inside an existing transaction."""
    return _close_assignment_revision_authority(
        connection,
        task_id=task_id,
        attempt=attempt,
        result_metadata=result_metadata,
    )


def validate_close_assignment_revision_authority(
    root: Path,
    *,
    task_id: str,
    attempt: Mapping[str, Any],
    result_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate close occurrence revision authority from the canonical ledger."""
    ensure_database(root)
    with _connection(root, write=False) as connection:
        return _close_assignment_revision_authority(
            connection,
            task_id=task_id,
            attempt=attempt,
            result_metadata=result_metadata,
        )


_OPERATIONAL_PLAN_KEYS = frozenset({
    "attempt_ids", "executable_gates", "plan_revision", "status", "updated_at",
})


def executable_plan_projection(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Project only executable plan semantics, excluding lifecycle bookkeeping."""
    def project(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): project(item)
                for key, item in value.items()
                if str(key) not in _OPERATIONAL_PLAN_KEYS
            }
        if isinstance(value, list):
            return [project(item) for item in value]
        return canonical_json.normalize(value)

    projected = project(plan)
    if not isinstance(projected, dict):
        raise ValueError("executable orchestration plan projection must be an object")
    return projected


def get_executable_plan_authority(root: Path, task_id: str) -> dict[str, Any] | None:
    """Return the latest semantic plan receipt and compare it to the live plan.

    Runtime wave status, assignment ids, and timestamps intentionally create
    operational receipts without changing this executable-plan authority.
    """
    ensure_database(root)
    with _connection(root, write=False) as connection:
        semantic_row = connection.execute(
            "WITH latest_semantic_plan AS ("
            " SELECT * FROM plan_revisions"
            " WHERE task_id = ?"
            " AND COALESCE(json_extract(impact_json, '$.classification'), '')"
            "     <> 'runtime_plan_update'"
            " ORDER BY plan_revision DESC LIMIT 1"
            "), latest_task_revision AS ("
            " SELECT task_revision FROM task_revisions"
            " WHERE task_id = ? ORDER BY task_revision DESC LIMIT 1"
            ")"
            " SELECT latest_semantic_plan.*,"
            " tasks.plan_json AS current_plan_json,"
            " latest_task_revision.task_revision AS current_task_revision"
            " FROM latest_semantic_plan"
            " JOIN tasks ON tasks.task_id = latest_semantic_plan.task_id"
            " LEFT JOIN latest_task_revision ON 1 = 1",
            (task_id, task_id),
        ).fetchone()
    if semantic_row is None:
        return None
    semantic_impact = _decode_json(
        str(semantic_row["impact_json"]), "semantic plan revision impact",
    )
    receipt_plan = (
        _decode_json(str(semantic_row["plan_json"]), "semantic plan revision")
        if semantic_row["plan_json"] is not None else None
    )
    current_plan = (
        _decode_json(str(semantic_row["current_plan_json"]), "current orchestration plan")
        if semantic_row["current_plan_json"] is not None else None
    )
    current_task_revision = (
        int(semantic_row["current_task_revision"])
        if semantic_row["current_task_revision"] is not None else None
    )
    if current_task_revision is not None and current_task_revision < 1:
        current_task_revision = None
    if not isinstance(receipt_plan, dict) or not isinstance(current_plan, dict):
        return None
    receipt_projection = executable_plan_projection(receipt_plan)
    current_projection = executable_plan_projection(current_plan)
    receipt_digest = canonical_json.digest(receipt_projection)
    current_digest = canonical_json.digest(current_projection)
    return {
        "task_id": str(semantic_row["task_id"]),
        "plan_revision": int(semantic_row["plan_revision"]),
        "task_revision": int(semantic_row["task_revision"]),
        "current_task_revision": current_task_revision,
        "impact": semantic_impact or {},
        "plan": receipt_plan,
        "plan_projection": receipt_projection,
        "plan_digest": receipt_digest,
        "current_plan": current_plan,
        "current_plan_projection": current_projection,
        "current_plan_digest": current_digest,
        "current_plan_matches": current_digest == receipt_digest,
        "created_at": str(semantic_row["created_at"]),
    }


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
                 host_tool=excluded.host_tool, generation=excluded.generation, status=excluded.status, resumable=excluded.resumable, last_seen_at=excluded.last_seen_at,
                 terminated_at=excluded.terminated_at""",
            (
                session_id, task_id, attempt_id, session.get("host_agent_id"), str(session.get("host_task_name") or ""),
                str(session.get("host_tool") or "spawn_agent"), generation, status, int(bool(session.get("resumable", True))),
                session.get("started_at") or timestamp, timestamp, session.get("terminated_at"),
            ),
        )
    return {**session, "session_id": session_id, "status": status, "last_seen_at": timestamp}


def reconcile_terminal_worker_session(
    root: Path,
    *,
    task_id: str,
    attempt_id: str,
    terminated_at: str | None = None,
) -> dict[str, Any]:
    """Close every persisted native session for one terminal AttemptResult.

    ``worker_sessions`` is the server-observed lifecycle authority.  Once an
    exact attempt has produced a terminal canonical result, no session for
    that attempt may remain presented as awaiting, running, or resumable.
    This is intentionally a named runtime transition rather than a caller
    issuing an ad-hoc SQL repair: retries are idempotent, preserve the native
    assignment row, and make the terminal timestamp durable before a coordinator
    can consume the result.

    A missing session is an authority violation, not an invitation to invent a
    assignment authority.  The caller must fail closed and retry/recover through the
    normal dispatched attempt instead.
    """
    ensure_database(root)
    normalized_task_id = str(task_id or "")
    normalized_attempt_id = str(attempt_id or "")
    if not normalized_task_id or not normalized_attempt_id:
        raise ValueError("terminal worker-session reconciliation requires task_id and attempt_id")
    stamp = str(terminated_at or _now())
    with _connection(root, write=True) as connection:
        rows = connection.execute(
            """SELECT session_id, status, resumable, terminated_at
                 FROM worker_sessions
                 WHERE task_id=? AND attempt_id=?
                 ORDER BY generation DESC, last_seen_at DESC""",
            (normalized_task_id, normalized_attempt_id),
        ).fetchall()
        if not rows:
            raise ValueError("terminal AttemptResult has no persisted worker session")
        changed = 0
        for row in rows:
            if (
                str(row["status"]) != "completed"
                or bool(row["resumable"])
                or not row["terminated_at"]
            ):
                changed += 1
        connection.execute(
            """UPDATE worker_sessions
                 SET status='completed', resumable=0,
                     last_seen_at=?, terminated_at=COALESCE(terminated_at, ?)
                 WHERE task_id=? AND attempt_id=?""",
            (stamp, stamp, normalized_task_id, normalized_attempt_id),
        )
    return {
        "task_id": normalized_task_id,
        "attempt_id": normalized_attempt_id,
        "session_count": len(rows),
        "reconciled": changed > 0,
        "idempotent": changed == 0,
    }


def list_worker_sessions(root: Path, task_id: str) -> list[dict[str, Any]]:
    """Read server-observed native worker lifecycle telemetry for one task.

    This table records spawn/wait/stop observations for native children. It is
    never an authorization source: worker authority is the exact native
    dispatch_ref carried by its dispatch. The read is deliberately scoped to
    one exact task and returns no unrelated transport telemetry.
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


def worker_sessions_for_host_agent(
    root: Path,
    host_agent_id: str,
    *,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Resolve a bounded private child binding without task enumeration."""
    if not isinstance(host_agent_id, str) or not host_agent_id or "\x00" in host_agent_id:
        return []
    bounded = max(1, min(int(limit), 2))
    ensure_database(root)
    with _connection(root) as connection:
        rows = connection.execute(
            "SELECT session_id,task_id,attempt_id,host_agent_id,host_task_name,host_tool,generation,status,resumable,started_at,last_seen_at,terminated_at "
            "FROM worker_sessions WHERE host_agent_id=? ORDER BY task_id,attempt_id,generation LIMIT ?",
            (host_agent_id, bounded),
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


def native_host_start_key(agent_id: str) -> str:
    """Return a private non-reversible key for one exact native child thread."""
    normalized = str(agent_id or "")
    if not normalized or len(normalized) > 256 or "\x00" in normalized:
        raise ValueError("native child host identity is invalid")
    return NATIVE_HOST_START_KEY_PREFIX + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def native_host_epoch_key(session_id: str) -> str:
    """Return the private document key for one coordinator host session."""
    normalized = str(session_id or "")
    if not normalized or len(normalized) > 256 or "\x00" in normalized:
        raise ValueError("native host session identity is invalid")
    return NATIVE_HOST_EPOCH_KEY_PREFIX + hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def native_context_boundary_key(session_id: str) -> str:
    normalized = str(session_id or "")
    if not normalized or len(normalized) > 256 or "\x00" in normalized:
        raise ValueError("native context session identity is invalid")
    return NATIVE_CONTEXT_BOUNDARY_KEY_PREFIX + hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def _native_context_boundary_binding(root: Path, value: Mapping[str, Any]) -> str:
    payload = _canonical_json({
        key: value.get(key)
        for key in (
            "schema", "session_digest", "epoch", "fingerprint", "source",
            "boundary_ref", "observed_at",
        )
    }).encode("utf-8")
    return "hmac-sha256:" + hmac.new(
        _governance_lifecycle_hmac_key(root, create=False), payload, hashlib.sha256,
    ).hexdigest()


def hook_record_native_context_boundary(
    root: Path,
    session_id: str,
    epoch: Mapping[str, Any],
    *,
    source: str,
) -> bool:
    """Record one same-incarnation compact/reset boundary without task mutation."""
    normalized_source = str(source or "").strip().lower()
    if normalized_source not in {"compact", "compaction", "clear", "reset"}:
        return False
    observed_at = _now()
    identity = _canonical_json({
        "session_digest": private_lifecycle_audit_digest(root, "host-session", session_id),
        "epoch": epoch.get("epoch"),
        "fingerprint": epoch.get("fingerprint"),
        "source": normalized_source,
        "observed_at": observed_at,
    })
    value = {
        "schema": NATIVE_CONTEXT_BOUNDARY_SCHEMA,
        "session_digest": private_lifecycle_audit_digest(root, "host-session", session_id),
        "epoch": int(epoch.get("epoch") or 0),
        "fingerprint": str(epoch.get("fingerprint") or ""),
        "source": normalized_source,
        "boundary_ref": "context-boundary-v1-" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "observed_at": observed_at,
    }
    if value["epoch"] < 1 or not value["fingerprint"].startswith("hmac-sha256:"):
        return False
    value["binding"] = _native_context_boundary_binding(root, value)
    return hook_put_global_document(root, native_context_boundary_key(session_id), value)


def get_native_context_boundary(root: Path, session_id: str) -> dict[str, Any] | None:
    value = get_global(root, native_context_boundary_key(session_id), {})
    if value.get("schema") != NATIVE_CONTEXT_BOUNDARY_SCHEMA:
        return None
    try:
        expected = _native_context_boundary_binding(root, value)
    except (OSError, TypeError, ValueError):
        return None
    binding = str(value.get("binding") or "")
    return dict(value) if binding and hmac.compare_digest(binding, expected) else None


def _native_context_boundary_ack_key(session_id: str, task_id: str) -> str:
    joined = _canonical_json([str(session_id), str(task_id)])
    return NATIVE_CONTEXT_BOUNDARY_ACK_KEY_PREFIX + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def context_boundary_pending(
    root: Path, session_id: str, task_id: str,
) -> dict[str, Any] | None:
    boundary = get_native_context_boundary(root, session_id)
    if boundary is None:
        return None
    ack = get_global(root, _native_context_boundary_ack_key(session_id, task_id), {})
    if (
        ack.get("schema") == NATIVE_CONTEXT_BOUNDARY_ACK_SCHEMA
        and str(ack.get("boundary_ref") or "") == str(boundary.get("boundary_ref") or "")
    ):
        return None
    return boundary


def acknowledge_context_boundary(
    root: Path, session_id: str, task_id: str, boundary: Mapping[str, Any],
) -> None:
    current = get_native_context_boundary(root, session_id)
    if (
        current is None
        or str(current.get("boundary_ref") or "") != str(boundary.get("boundary_ref") or "")
    ):
        raise ValueError("native context boundary changed during inspection")
    put_global(root, _native_context_boundary_ack_key(session_id, task_id), {
        "schema": NATIVE_CONTEXT_BOUNDARY_ACK_SCHEMA,
        "boundary_ref": str(current["boundary_ref"]),
        "acknowledged_at": _now(),
    })


def _native_host_epoch_binding(root: Path, value: Mapping[str, Any], *, create: bool) -> str:
    payload = _canonical_json({
        key: value.get(key)
        for key in (
            "schema", "session_digest", "epoch", "fingerprint", "host_uid", "host_pid",
            "host_start_ticks", "boot_digest", "process_started_at", "source",
            "observed_at", "transition",
        )
    }).encode("utf-8")
    return "hmac-sha256:" + hmac.new(
        _governance_lifecycle_hmac_key(root, create=create), payload, hashlib.sha256,
    ).hexdigest()


def get_native_host_epoch(root: Path, session_id: str) -> dict[str, Any] | None:
    """Read and authenticate the latest private host-process incarnation."""
    value = get_global(root, native_host_epoch_key(session_id), {})
    if value.get("schema") != NATIVE_HOST_EPOCH_SCHEMA:
        return None
    try:
        expected = _native_host_epoch_binding(root, value, create=False)
    except (OSError, TypeError, ValueError):
        return None
    binding = str(value.get("binding") or "")
    return value if binding and hmac.compare_digest(binding, expected) else None


def hook_get_native_host_epoch(
    root: Path, session_id: str, *, timeout_ms: int = 100,
) -> dict[str, Any] | None:
    """Read an authenticated epoch without bootstrapping or migrating a ledger."""
    try:
        key = native_host_epoch_key(session_id)
        with hook_snapshot(root, timeout_ms=timeout_ms) as connection:
            if connection is None:
                return None
            value = hook_snapshot_global(connection, key, {})
        if value.get("schema") != NATIVE_HOST_EPOCH_SCHEMA:
            return None
        expected = _native_host_epoch_binding(root, value, create=False)
        binding = str(value.get("binding") or "")
        return value if binding and hmac.compare_digest(binding, expected) else None
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _hook_metric("hook_snapshot_miss")
        return None


def hook_advance_native_host_epoch(
    root: Path,
    session_id: str,
    incarnation: Mapping[str, Any],
    *,
    source: str,
    prior_fingerprint: str | None,
    prior_provably_dead: bool,
    hook_owned: bool = True,
) -> dict[str, Any] | None:
    """Atomically bind one Codex process incarnation to a coordinator session.

    A changed process may advance the epoch only when the caller has proved
    that the previously authenticated PID/start-time pair is no longer live.
    The hook never infers liveness from model text or collaboration output.
    """
    try:
        key = native_host_epoch_key(session_id)
        fingerprint = str(incarnation.get("fingerprint") or "")
        if not fingerprint.startswith("hmac-sha256:"):
            raise ValueError("native host incarnation fingerprint is invalid")
        connection_scope = (
            _hook_write_connection(root, timeout_ms=4000)
            if hook_owned else _connection(root, write=True)
        )
        with connection_scope as connection:
            row = connection.execute(
                "SELECT payload_json FROM global_documents WHERE name=?", (key,),
            ).fetchone()
            existing = (
                _decode_json(str(row["payload_json"]), "native host epoch")
                if row is not None else None
            )
            if isinstance(existing, Mapping):
                existing_binding = str(existing.get("binding") or "")
                expected_binding = _native_host_epoch_binding(root, existing, create=False)
                if (
                    existing.get("schema") != NATIVE_HOST_EPOCH_SCHEMA
                    or not existing_binding
                    or not hmac.compare_digest(existing_binding, expected_binding)
                ):
                    return None
                existing_fingerprint = str(existing.get("fingerprint") or "")
                if hmac.compare_digest(existing_fingerprint, fingerprint):
                    return dict(existing)
                if (
                    not prior_provably_dead
                    or not prior_fingerprint
                    or not hmac.compare_digest(existing_fingerprint, prior_fingerprint)
                ):
                    return None
                epoch = int(existing.get("epoch") or 0) + 1
                transition = "proven_dead_host_handoff"
            else:
                epoch = 1
                transition = "initial_host_binding"
            value = {
                "schema": NATIVE_HOST_EPOCH_SCHEMA,
                "session_digest": private_lifecycle_audit_digest(
                    root, "host-session", session_id,
                ),
                "epoch": epoch,
                "fingerprint": fingerprint,
                "host_uid": int(incarnation["host_uid"]),
                "host_pid": int(incarnation["host_pid"]),
                "host_start_ticks": int(incarnation["host_start_ticks"]),
                "boot_digest": str(incarnation["boot_digest"]),
                "process_started_at": str(incarnation["process_started_at"]),
                "source": str(source),
                "observed_at": str(incarnation["observed_at"]),
                "transition": transition,
            }
            value["binding"] = _native_host_epoch_binding(root, value, create=True)
            payload_json = _bounded_document_json(value, label="native host epoch")
            connection.execute(
                "INSERT INTO global_documents(name,payload_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                (key, payload_json, value["observed_at"]),
            )
            return value
    except (KeyError, OSError, sqlite3.Error, TypeError, ValueError):
        _hook_metric("telemetry_failure")
        return None


def native_host_start_boundary_key(agent_id: str, turn_id: str) -> str:
    """Return a private immutable key for one exact child start boundary."""
    if not str(turn_id or "") or len(str(turn_id)) > 512 or "\x00" in str(turn_id):
        raise ValueError("native child start turn identity is invalid")
    joined = json.dumps([str(agent_id), str(turn_id)], ensure_ascii=False, separators=(",", ":"))
    return NATIVE_HOST_START_BOUNDARY_KEY_PREFIX + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def native_host_stop_key(agent_id: str, session_id: str, turn_id: str) -> str:
    """Return the immutable private inbox key for one exact native Stop."""
    values = (str(agent_id or ""), str(session_id or ""), str(turn_id or ""))
    if any(not value or len(value) > 512 or "\x00" in value for value in values):
        raise ValueError("native child stop identity is invalid")
    joined = json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))
    return NATIVE_HOST_STOP_KEY_PREFIX + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def native_host_stop_receipt_key(stop_key: str) -> str:
    """Return a private reconciliation key without copying host identity."""
    normalized = str(stop_key or "")
    if not normalized.startswith(NATIVE_HOST_STOP_KEY_PREFIX):
        raise ValueError("native child stop inbox identity is invalid")
    return NATIVE_HOST_STOP_RECEIPT_KEY_PREFIX + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def pending_native_host_stops(root: Path) -> list[tuple[str, dict[str, Any]]]:
    """List captured Stops without reconciliation receipts in durable order."""
    ensure_database(root)
    with _connection(root) as connection:
        rows = connection.execute(
            "SELECT name, payload_json, updated_at FROM global_documents "
            "WHERE name LIKE ? ORDER BY updated_at, name",
            (NATIVE_HOST_STOP_KEY_PREFIX + "%",),
        ).fetchall()
        receipt_names = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM global_documents WHERE name LIKE ?",
                (NATIVE_HOST_STOP_RECEIPT_KEY_PREFIX + "%",),
            ).fetchall()
        }
    pending: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        stop_key = str(row["name"])
        if native_host_stop_receipt_key(stop_key) in receipt_names:
            continue
        try:
            value = _decode_json(str(row["payload_json"]), "native host stop inbox")
        except (TypeError, ValueError):
            continue
        if value.get("schema") == NATIVE_HOST_STOP_SCHEMA:
            pending.append((stop_key, value))
    return pending


def put_native_host_stop_receipt(root: Path, stop_key: str, value: dict[str, Any]) -> str:
    """Insert one immutable reconciliation receipt in the caller's transaction."""
    if value.get("schema") != NATIVE_HOST_STOP_RECEIPT_SCHEMA:
        raise ValueError("native child stop receipt schema is invalid")
    key = native_host_stop_receipt_key(stop_key)
    payload_json = _bounded_document_json(value, label="native host stop receipt")
    with _connection(root, write=True) as connection:
        row = connection.execute(
            "SELECT payload_json FROM global_documents WHERE name=?", (key,),
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO global_documents(name,payload_json,updated_at) VALUES(?,?,?)",
                (key, payload_json, str(value.get("reconciled_at") or _now())),
            )
            return "inserted"
        existing = _decode_json(str(row["payload_json"]), "native host stop receipt")
        compare_fields = ("schema", "stop_key_digest", "task_id", "attempt_id", "outcome")
        return "same" if all(existing.get(field) == value.get(field) for field in compare_fields) else "conflict"


def get_native_host_start(root: Path, agent_id: str) -> dict[str, Any] | None:
    value = get_global(root, native_host_start_key(agent_id), {})
    return value if value.get("schema") == NATIVE_HOST_START_SCHEMA else None


def get_latest_native_host_start(root: Path, agent_id: str) -> dict[str, Any] | None:
    """Return the latest durable Start boundary for one native child.

    The primary child record returned by :func:`get_native_host_start` is
    intentionally immutable.  A resumed question turn creates another
    boundary record, so binding must resolve the newest exact boundary rather
    than reusing that primary record.  ``observed_at`` and the SQLite update
    timestamp provide the durable ordering; ``turn_id`` is a deterministic
    final tie-breaker for equal timestamps.
    """
    ensure_database(root)
    # Boundary keys are independently hashed (agent + turn); they do not
    # share the primary key prefix.  Filter the decoded payload as well as
    # the bounded key prefix so unrelated children cannot be selected.
    prefix = NATIVE_HOST_START_BOUNDARY_KEY_PREFIX
    candidates: list[tuple[tuple[str, str, str], dict[str, Any]]] = []
    with _connection(root) as connection:
        rows = connection.execute(
            "SELECT payload_json, updated_at FROM global_documents "
            "WHERE name LIKE ?",
            (prefix + "%",),
        ).fetchall()
    for row in rows:
        try:
            value = _decode_json(str(row["payload_json"]), "native host start boundary")
        except (TypeError, ValueError):
            continue
        if (
            value.get("schema") != NATIVE_HOST_START_SCHEMA
            or str(value.get("agent_id") or "") != str(agent_id)
            or not str(value.get("turn_id") or "")
        ):
            continue
        ordering = (
            str(value.get("observed_at") or ""),
            str(row["updated_at"] or ""),
            str(value.get("turn_id") or ""),
        )
        candidates.append((ordering, value))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def put_global(root: Path, name: str, value: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise ValueError("SQLite global document must be an object")
    name_value = _bounded_document_key(name, label="SQLite global document identity")
    payload_json = _bounded_document_json(value, label=f"SQLite global document {name_value!r}")
    with _connection(root, write=True) as connection:
        connection.execute(
            "INSERT INTO global_documents(name, payload_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET payload_json = excluded.payload_json, updated_at = excluded.updated_at",
            (name_value, payload_json, _now()),
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
    if not isinstance(task_id, str) or not task_id or "\x00" in task_id:
        raise ValueError("SQLite task document identity is invalid")
    if not isinstance(value, dict):
        raise ValueError("SQLite task document must be an object")
    key = _bounded_document_key(document_key, label="SQLite task document identity")
    payload_json = _bounded_document_json(value, label=f"SQLite task document {key!r}")
    with _connection(root, write=True) as connection:
        connection.execute(
            "INSERT INTO task_documents(task_id, document_key, payload_json, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(task_id, document_key) DO UPDATE SET payload_json = excluded.payload_json, updated_at = excluded.updated_at",
            (task_id, key, payload_json, str(value.get("updated_at") or value.get("answered_at") or _now())),
        )


def native_lifecycle_failure_key(attempt_id: str) -> str:
    normalized = str(attempt_id or "").strip()
    if not normalized or len(normalized) > 120 or "\x00" in normalized:
        raise ValueError("native lifecycle failure attempt identity is invalid")
    return NATIVE_LIFECYCLE_FAILURE_KEY_PREFIX + normalized


def hook_put_task_document(root: Path, task_id: str, document_key: str, value: dict[str, Any]) -> bool:
    """Attempt one bounded hook-owned task-document write without migration."""
    try:
        key = _bounded_document_key(document_key, label="hook task document identity")
        payload_json = _bounded_document_json(value, label=f"hook task document {key!r}")
        with _hook_write_connection(root) as connection:
            connection.execute(
                "INSERT INTO task_documents(task_id, document_key, payload_json, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(task_id, document_key) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                (task_id, key, payload_json, str(value.get("observed_at") or value.get("updated_at") or _now())),
            )
        return True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _hook_metric("telemetry_failure")
        return False


def hook_put_global_document(root: Path, name: str, value: dict[str, Any]) -> bool:
    """Attempt one bounded hook-owned project diagnostic write."""
    try:
        key = _bounded_document_key(name, label="hook global document identity")
        payload_json = _bounded_document_json(value, label=f"hook global document {key!r}")
        with _hook_write_connection(root) as connection:
            connection.execute(
                "INSERT INTO global_documents(name,payload_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                (key, payload_json, str(value.get("observed_at") or value.get("updated_at") or _now())),
            )
        return True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _hook_metric("telemetry_failure")
        return False


def hook_compare_insert_global_document(
    root: Path,
    name: str,
    value: dict[str, Any],
    *,
    compare_fields: Sequence[str],
) -> str:
    """Atomically insert immutable hook evidence or compare the exact row."""
    try:
        key = _bounded_document_key(name, label="hook immutable document identity")
        payload_json = _bounded_document_json(value, label=f"hook immutable document {key!r}")
        with _hook_write_connection(root) as connection:
            row = connection.execute(
                "SELECT payload_json FROM global_documents WHERE name=?", (key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO global_documents(name,payload_json,updated_at) VALUES(?,?,?)",
                    (key, payload_json, str(value.get("observed_at") or _now())),
                )
                return "inserted"
            existing = _decode_json(str(row["payload_json"]), f"hook immutable document {key!r}")
            return "same" if all(existing.get(field) == value.get(field) for field in compare_fields) else "conflict"
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _hook_metric("telemetry_failure")
        return "unavailable"


def hook_compare_insert_native_stop(root: Path, value: dict[str, Any]) -> str:
    """Durably capture one exact Stop before the hook acknowledges delivery.

    Unlike optional hook telemetry, terminal lifecycle capture gets most of
    the host hook's bounded runtime budget.  It deliberately does not acquire
    the task-state lock: a busy evaluator can delay reconciliation, but cannot
    make the sole terminal host event disappear.
    """
    try:
        if value.get("schema") != NATIVE_HOST_STOP_SCHEMA:
            raise ValueError("native child stop schema is invalid")
        if re.fullmatch(r"[0-9a-f]{64}", str(value.get("host_envelope_digest") or "")) is None:
            raise ValueError("native child stop envelope digest is invalid")
        key = native_host_stop_key(
            str(value.get("agent_id") or ""),
            str(value.get("session_id") or ""),
            str(value.get("turn_id") or ""),
        )
        payload_json = _bounded_document_json(value, label="native host stop inbox")
        with _hook_write_connection(root, timeout_ms=4000) as connection:
            row = connection.execute(
                "SELECT payload_json FROM global_documents WHERE name=?", (key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO global_documents(name,payload_json,updated_at) VALUES(?,?,?)",
                    (key, payload_json, str(value.get("observed_at") or _now())),
                )
                return "inserted"
            existing = _decode_json(str(row["payload_json"]), "native host stop inbox")
            compare_fields = (
                "schema", "agent_id", "session_id", "turn_id", "agent_type",
                "model", "permission_mode", "stop_hook_active", "host_envelope_digest",
                "host_epoch", "host_epoch_fingerprint",
            )
            return "same" if all(existing.get(field) == value.get(field) for field in compare_fields) else "conflict"
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _hook_metric("telemetry_failure")
        return "unavailable"


def hook_compare_insert_native_start(root: Path, value: dict[str, Any]) -> str:
    """Atomically bind one child thread and insert one exact Start boundary."""
    try:
        agent_id = str(value.get("agent_id") or "")
        turn_id = str(value.get("turn_id") or "")
        primary_key = native_host_start_key(agent_id)
        boundary_key = native_host_start_boundary_key(agent_id, turn_id)
        payload_json = _bounded_document_json(value, label="native host start evidence")
        with _hook_write_connection(root) as connection:
            primary = connection.execute(
                "SELECT payload_json FROM global_documents WHERE name=?", (primary_key,),
            ).fetchone()
            if primary is not None:
                existing = _decode_json(str(primary["payload_json"]), "native host start evidence")
                if not all(existing.get(field) == value.get(field) for field in (
                    "schema", "agent_id", "session_id", "agent_type",
                    "host_epoch", "host_epoch_fingerprint",
                )):
                    return "conflict"
            boundary = connection.execute(
                "SELECT payload_json FROM global_documents WHERE name=?", (boundary_key,),
            ).fetchone()
            if boundary is not None:
                existing = _decode_json(str(boundary["payload_json"]), "native host start boundary")
                return "same" if all(existing.get(field) == value.get(field) for field in (
                    "schema", "agent_id", "session_id", "turn_id", "agent_type", "model",
                    "host_epoch", "host_epoch_fingerprint",
                )) else "conflict"
            timestamp = str(value.get("observed_at") or _now())
            if primary is None:
                connection.execute(
                    "INSERT INTO global_documents(name,payload_json,updated_at) VALUES(?,?,?)",
                    (primary_key, payload_json, timestamp),
                )
            connection.execute(
                "INSERT INTO global_documents(name,payload_json,updated_at) VALUES(?,?,?)",
                (boundary_key, payload_json, timestamp),
            )
            return "inserted"
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _hook_metric("telemetry_failure")
        return "unavailable"


def native_child_binding_exists(
    root: Path,
    child_id: str,
    *,
    task_id: str,
    attempt_id: str,
) -> bool:
    """Check project-global child uniqueness inside the caller's transaction."""
    if not child_id or "\x00" in child_id:
        raise ValueError("native child identity is invalid")
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT 1 FROM worker_sessions WHERE host_agent_id=? "
            "AND NOT (task_id=? AND attempt_id=?) LIMIT 1",
            (child_id, task_id, attempt_id),
        ).fetchone()
    return row is not None


def native_lifecycle_observer_health(
    root: Path, *, task_id: str, attempt_id: str,
) -> str:
    """Return unavailable or healthy for the private native observer boundary."""
    failure = get_task_document(root, task_id, native_lifecycle_failure_key(attempt_id))
    if (
        isinstance(failure, dict)
        and failure.get("schema") == NATIVE_LIFECYCLE_FAILURE_SCHEMA
        and failure.get("retryable") is True
    ):
        return "unavailable"
    task_failure = get_task_document(root, task_id, NATIVE_LIFECYCLE_TASK_FAILURE_DOCUMENT)
    if (
        isinstance(task_failure, dict)
        and task_failure.get("schema") == NATIVE_LIFECYCLE_FAILURE_SCHEMA
        and task_failure.get("retryable") is True
    ):
        return "unavailable"
    shape_documents = list_task_documents(root, task_id, "native-hook-shape:")
    if any(
        key.endswith(":" + str(attempt_id))
        and isinstance(value, dict)
        and value.get("schema") in {
            "cortex/native-hook-shape-diagnostic/v1",
            "cortex/native-hook-shape-diagnostic/v2",
        }
        for key, value in shape_documents
    ):
        return "unavailable"
    return "healthy"


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


_DURABLE_QUESTION_COLUMNS = (
    "question_ref", "task_id", "attempt_id", "dispatch_ref", "profile", "task_revision",
    "attempt_generation", "submission_id", "question_category", "question_text", "status", "content_digest",
    "published_sequence", "answer", "answer_submission_id", "answer_digest",
    "answered_sequence", "created_at", "answered_at", "superseded_at",
)
_DURABLE_QUESTION_IMMUTABLE_COLUMNS = (
    "question_ref", "task_id", "attempt_id", "dispatch_ref", "profile", "task_revision",
    "attempt_generation", "submission_id", "question_category", "question_text", "content_digest",
    "published_sequence", "created_at",
)
_DURABLE_QUESTION_MUTABLE_COLUMNS = (
    "status", "answer", "answer_submission_id", "answer_digest", "answered_sequence",
    "answered_at", "superseded_at",
)


def _durable_question_record(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("durable question must be an object")
    missing = [column for column in _DURABLE_QUESTION_COLUMNS if column not in record]
    if missing:
        raise ValueError("durable question fields are incomplete")
    value = {column: record[column] for column in _DURABLE_QUESTION_COLUMNS}
    for column in (
        "question_ref", "task_id", "attempt_id", "dispatch_ref", "profile", "submission_id",
        "status", "content_digest", "created_at",
    ):
        if not isinstance(value[column], str) or not value[column] or "\x00" in value[column]:
            raise ValueError("durable question text or identity is invalid")
    # Question and answer bodies are semantic Unicode text blobs, not
    # identifiers or transport delimiters.  Preserve every scalar exactly,
    # including U+0000, rather than treating their contents as safe-text
    # tokens or normalizing them on the way into SQLite.
    if not isinstance(value["question_text"], str):
        raise ValueError("durable question text is invalid")
    if value["question_category"] is not None and (
        not isinstance(value["question_category"], str)
        or not value["question_category"]
        or "\x00" in value["question_category"]
    ):
        raise ValueError("durable question category is invalid")
    if value["status"] not in {"open", "answered", "superseded"}:
        raise ValueError("durable question status is invalid")
    for column in ("task_revision", "attempt_generation", "published_sequence"):
        if not isinstance(value[column], int) or isinstance(value[column], bool) or value[column] < 1:
            raise ValueError("durable question sequence is invalid")
    if value["answer"] is not None and not isinstance(value["answer"], str):
        raise ValueError("durable question answer text is invalid")
    for column in ("answer_submission_id", "answer_digest", "answered_at", "superseded_at"):
        if value[column] is not None and (not isinstance(value[column], str) or "\x00" in value[column]):
            raise ValueError("durable question answer field is invalid")
    if value["answered_sequence"] is not None and (
        not isinstance(value["answered_sequence"], int)
        or isinstance(value["answered_sequence"], bool)
        or value["answered_sequence"] < 1
    ):
        raise ValueError("durable question answer sequence is invalid")
    return value


def get_durable_question(root: Path, task_id: str, question_ref: str) -> dict[str, Any] | None:
    """Read one exact durable question without scanning the task collection."""
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT " + ",".join(_DURABLE_QUESTION_COLUMNS)
            + " FROM durable_questions WHERE task_id = ? AND question_ref = ?",
            (str(task_id), str(question_ref)),
        ).fetchone()
    return None if row is None else dict(row)


def get_durable_question_submission(
    root: Path, task_id: str, attempt_id: str, submission_id: str,
) -> dict[str, Any] | None:
    """Read one idempotent question submission through its unique index."""
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT " + ",".join(_DURABLE_QUESTION_COLUMNS)
            + " FROM durable_questions WHERE task_id = ? AND attempt_id = ? AND submission_id = ?",
            (str(task_id), str(attempt_id), str(submission_id)),
        ).fetchone()
    return None if row is None else dict(row)


def durable_question_sequence(root: Path, task_id: str) -> int:
    """Return the latest question/answer sequence without reading the collection."""
    ensure_database(root)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT MAX(MAX(published_sequence, COALESCE(answered_sequence, 0))) AS sequence "
            "FROM durable_questions WHERE task_id = ?",
            (str(task_id),),
        ).fetchone()
    return int(row["sequence"] or 0) if row is not None else 0


def count_durable_questions(
    root: Path, task_id: str, *, attempt_id: str = "", task_revision: int | None = None,
    attempt_generation: int | None = None, include_superseded: bool = True,
    categories: Sequence[str] | None = None,
) -> int:
    """Count questions using indexed predicates; never materialize rows."""
    ensure_database(root)
    where = ["task_id = ?"]
    values: list[Any] = [str(task_id)]
    if attempt_id:
        where.append("attempt_id = ?")
        values.append(str(attempt_id))
    if task_revision is not None:
        where.append("task_revision = ?")
        values.append(int(task_revision))
    if attempt_generation is not None:
        where.append("attempt_generation = ?")
        values.append(int(attempt_generation))
    if not include_superseded:
        where.append("status != 'superseded'")
    if categories is not None:
        category_values = tuple(categories)
        if not category_values or any(not isinstance(item, str) or not item for item in category_values):
            raise ValueError("question categories are invalid")
        where.append("question_category IN (" + ",".join("?" for _ in category_values) + ")")
        values.extend(category_values)
    with _connection(root) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM durable_questions WHERE " + " AND ".join(where),
            tuple(values),
        ).fetchone()
    return int(row["count"] or 0) if row is not None else 0


def page_durable_questions(
    root: Path,
    task_id: str,
    *,
    offset: int = 0,
    limit: int = 64,
    attempt_id: str = "",
    status: str = "",
    categories: Sequence[str] | None = None,
    dispatch_ref: str = "",
    attempt_generation: int | None = None,
    statuses: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Read a bounded question page directly from canonical SQLite rows."""
    ensure_database(root)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("question page offset is invalid")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 128:
        raise ValueError("question page limit is invalid")
    where = ["task_id = ?"]
    values: list[Any] = [str(task_id)]
    if attempt_id:
        where.append("attempt_id = ?")
        values.append(str(attempt_id))
    if status:
        where.append("status = ?")
        values.append(str(status))
    if statuses is not None:
        if status:
            raise ValueError("question status filters conflict")
        status_values = tuple(str(item) for item in statuses)
        if not status_values or any(item not in {"open", "answered", "superseded"} for item in status_values):
            raise ValueError("question statuses are invalid")
        where.append("status IN (" + ",".join("?" for _ in status_values) + ")")
        values.extend(status_values)
    if dispatch_ref:
        where.append("dispatch_ref = ?")
        values.append(str(dispatch_ref))
    if attempt_generation is not None:
        if isinstance(attempt_generation, bool) or not isinstance(attempt_generation, int) or attempt_generation < 1:
            raise ValueError("question attempt generation is invalid")
        where.append("attempt_generation = ?")
        values.append(attempt_generation)
    if categories is not None:
        category_values = tuple(categories)
        if not category_values or any(not isinstance(item, str) or not item for item in category_values):
            raise ValueError("question categories are invalid")
        where.append("question_category IN (" + ",".join("?" for _ in category_values) + ")")
        values.extend(category_values)
    values.extend((limit + 1, offset))
    with _connection(root) as connection:
        rows = connection.execute(
            "SELECT " + ",".join(_DURABLE_QUESTION_COLUMNS)
            + " FROM durable_questions WHERE " + " AND ".join(where)
            + " ORDER BY published_sequence LIMIT ? OFFSET ?",
            tuple(values),
        ).fetchall()
    has_more = len(rows) > limit
    return [dict(row) for row in rows[:limit]], has_more


def page_durable_question_updates(
    root: Path,
    task_id: str,
    attempt_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 64,
) -> tuple[list[dict[str, Any]], bool]:
    """Read a bounded question update page after one sequence watermark."""
    ensure_database(root)
    if not isinstance(after_sequence, int) or isinstance(after_sequence, bool) or after_sequence < 0:
        raise ValueError("question update sequence is invalid")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 128:
        raise ValueError("question update limit is invalid")
    with _connection(root) as connection:
        rows = connection.execute(
            "SELECT " + ",".join(_DURABLE_QUESTION_COLUMNS)
            + " FROM durable_questions WHERE task_id = ? AND attempt_id = ?"
            + " AND (published_sequence > ? OR COALESCE(answered_sequence, 0) > ?)"
            + " ORDER BY MIN(published_sequence, COALESCE(answered_sequence, published_sequence)), published_sequence"
            + " LIMIT ?",
            (str(task_id), str(attempt_id), after_sequence, after_sequence, limit + 1),
        ).fetchall()
    has_more = len(rows) > limit
    return [dict(row) for row in rows[:limit]], has_more


def put_durable_question(root: Path, record: Mapping[str, Any]) -> None:
    """Create a text pair or update only its mutable answer/lifecycle fields."""
    value = _durable_question_record(record)
    ensure_database(root)
    placeholders = ",".join("?" for _ in _DURABLE_QUESTION_COLUMNS)
    with _connection(root, write=True) as connection:
        existing = connection.execute(
            "SELECT " + ",".join(_DURABLE_QUESTION_COLUMNS)
            + " FROM durable_questions WHERE question_ref = ?",
            (value["question_ref"],),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO durable_questions(" + ",".join(_DURABLE_QUESTION_COLUMNS) + ") VALUES(" + placeholders + ")",
                tuple(value[column] for column in _DURABLE_QUESTION_COLUMNS),
            )
            return
        if any(existing[column] != value[column] for column in _DURABLE_QUESTION_IMMUTABLE_COLUMNS):
            raise ValueError("durable question immutable fields conflict")
        connection.execute(
            "UPDATE durable_questions SET "
            + ",".join(column + " = ?" for column in _DURABLE_QUESTION_MUTABLE_COLUMNS)
            + " WHERE question_ref = ?",
            tuple(value[column] for column in _DURABLE_QUESTION_MUTABLE_COLUMNS) + (value["question_ref"],),
        )


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


def delete_planning_revision_package_artifacts(
    root: Path, task_id: str, revision: str,
) -> int:
    """Remove one superseded coordinator-package projection atomically.

    Coordinator planning packages are rebuildable views of the canonical plan.
    This current-V19 data cutover removes only package artifacts for one exact
    coordinator revision; overview and unrelated planner revisions remain.
    """
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", str(task_id or ""))
        or not re.fullmatch(r"plan-coordinator-[0-9a-f]{24}", str(revision or ""))
    ):
        raise ValueError("planning package migration identity is invalid")
    title_prefix = f"{revision}:package:"
    path_prefix = f"planning/revisions/{revision}/packages/"
    with _connection(root, write=True) as connection:
        rows = connection.execute(
            "SELECT DISTINCT a.artifact_id,a.blob_id FROM logical_artifacts a "
            "LEFT JOIN artifact_exports e ON e.artifact_id=a.artifact_id "
            "WHERE a.task_id=? AND a.kind='planning_revision' "
            "AND (a.title LIKE ? OR e.export_path LIKE ?)",
            (task_id, title_prefix + "%", path_prefix + "%"),
        ).fetchall()
        artifact_ids = [str(row["artifact_id"]) for row in rows]
        blob_ids = [str(row["blob_id"]) for row in rows]
        connection.execute(
            "DELETE FROM projection_jobs WHERE task_id=? AND export_path LIKE ?",
            (task_id, path_prefix + "%"),
        )
        if artifact_ids:
            placeholders = ",".join("?" for _ in artifact_ids)
            connection.execute(
                f"DELETE FROM projection_jobs WHERE artifact_id IN ({placeholders})",
                tuple(artifact_ids),
            )
            connection.execute(
                f"DELETE FROM artifact_exports WHERE artifact_id IN ({placeholders})",
                tuple(artifact_ids),
            )
            connection.execute(
                f"DELETE FROM logical_artifacts WHERE artifact_id IN ({placeholders})",
                tuple(artifact_ids),
            )
        for blob_id in dict.fromkeys(blob_ids):
            connection.execute(
                "DELETE FROM artifact_blobs WHERE blob_id=? "
                "AND NOT EXISTS(SELECT 1 FROM logical_artifacts WHERE blob_id=?)",
                (blob_id, blob_id),
            )
    return len(artifact_ids)


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
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or offset < 0
        or not 1 <= page_size <= 100
    ):
        raise ValueError("SQLite artifact page bounds are invalid")
    ensure_database(root)
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
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Return one exact, UTF-8-safe range.

    The cursor offset is always a byte offset into the canonical immutable
    blob. Text pages never split a UTF-8 scalar. ``max_bytes`` is an optional
    caller-selected page size; omitting it reads every remaining exact byte.
    """
    if isinstance(byte_offset, bool) or not isinstance(byte_offset, int) or byte_offset < 0:
        raise ValueError("SQLite artifact byte offset is invalid")
    if max_bytes is not None and (
        isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1
    ):
        raise ValueError("SQLite artifact max_bytes must be a positive integer when supplied")
    metadata = get_artifact_metadata(root, task_id, artifact_ref)
    if metadata is None:
        raise ValueError("SQLite artifact is unavailable for the selected task")
    if byte_offset > metadata["byte_size"]:
        raise ValueError("SQLite artifact byte offset is invalid")
    requested_byte_offset = byte_offset
    remaining_offset = byte_offset
    effective_max_bytes = max_bytes if max_bytes is not None else int(metadata["byte_size"]) - byte_offset
    budget = effective_max_bytes
    text_parts: list[str] = []
    blob_parts: list[bytes] = []
    with _connection(root) as connection:
        rows = connection.execute(
            """SELECT b.encoding, c.chunk_no, c.text_content, c.blob_content, c.byte_size, c.digest_sha256
               FROM logical_artifacts a
               JOIN artifact_blobs b ON b.blob_id = a.blob_id
               JOIN artifact_blob_chunks c ON c.blob_id = a.blob_id
               WHERE a.task_id = ? AND a.artifact_id = ? ORDER BY c.chunk_no""",
            (task_id, artifact_ref),
        ).fetchall()
    if len(rows) != int(metadata["chunk_count"]):
        raise ValueError("SQLite artifact chunk count is invalid")
    if not rows:
        raise ValueError("SQLite artifact has no chunks")

    encoding = str(rows[0]["encoding"])
    if encoding not in {"utf-8", "binary"}:
        raise ValueError("SQLite artifact encoding is invalid")
    is_text = encoding == "utf-8"
    total_bytes = 0
    content_digest = hashlib.sha256()
    chunks: list[bytes] = []
    for expected_chunk_no, row in enumerate(rows):
        if int(row["chunk_no"]) != expected_chunk_no or str(row["encoding"]) != encoding:
            raise ValueError("SQLite artifact chunk sequence is invalid")
        size = int(row["byte_size"])
        text_value = row["text_content"]
        chunk_is_text = text_value is not None
        if is_text != chunk_is_text:
            raise ValueError("SQLite artifact chunk encoding is inconsistent")
        data = str(text_value).encode("utf-8") if chunk_is_text else bytes(row["blob_content"])
        if len(data) != size or hashlib.sha256(data).hexdigest() != str(row["digest_sha256"]):
            raise ValueError("SQLite artifact chunk digest is invalid")
        total_bytes += len(data)
        content_digest.update(data)
        chunks.append(data)
    if total_bytes != int(metadata["byte_size"]) or content_digest.hexdigest() != str(metadata["digest_sha256"]):
        raise ValueError("SQLite artifact digest is invalid")

    # Older briefing readers could issue a signed cursor in the middle of a
    # UTF-8 scalar (for example, after slicing a page at byte 32000).  The
    # cursor is still trusted for its task/artifact/digest scope, so rejecting
    # it here would unnecessarily terminate an otherwise resumable worker.
    # Rewind only to the beginning of that scalar.  This is lossless for a
    # failed continuation and keeps the returned cursor on a canonical UTF-8
    # boundary; tampered cursors are still rejected by cursor authentication
    # before reaching this function.
    normalized_byte_offset = byte_offset
    if is_text and byte_offset < total_bytes:
        combined = b"".join(chunks)
        while normalized_byte_offset > 0 and normalized_byte_offset < len(combined):
            if combined[normalized_byte_offset] & 0b11000000 != 0b10000000:
                break
            normalized_byte_offset -= 1
        if normalized_byte_offset != byte_offset:
            byte_offset = normalized_byte_offset
            remaining_offset = normalized_byte_offset
            if max_bytes is None:
                effective_max_bytes = int(metadata["byte_size"]) - normalized_byte_offset
            budget = effective_max_bytes

    for data in chunks:
        size = len(data)
        if remaining_offset >= size:
            remaining_offset -= size
            continue
        start = remaining_offset
        available = len(data) - start
        take = min(available, budget)
        end = start + take
        if is_text and end < len(data):
            while end > start and data[end] & 0b11000000 == 0b10000000:
                end -= 1
            if end == start:
                # A caller page preference is not a backend quota. Preserve
                # the first complete scalar rather than reject/stall.
                scalar_end = start + 1
                while scalar_end < len(data) and data[scalar_end] & 0b11000000 == 0b10000000:
                    scalar_end += 1
                end = scalar_end
        piece = data[start:end]
        if is_text:
            text_parts.append(piece.decode("utf-8"))
        else:
            blob_parts.append(piece)
        budget -= len(piece)
        remaining_offset = 0
        if budget <= 0:
            break
    delivered = effective_max_bytes - budget
    next_offset = byte_offset + delivered
    if delivered == 0 and byte_offset < metadata["byte_size"]:
        raise ValueError("SQLite artifact transport could not form a safe text range")
    result = {
        **metadata,
        "byte_offset": byte_offset,
        "requested_byte_offset": requested_byte_offset,
        "cursor_normalized": byte_offset != requested_byte_offset,
        "returned_bytes": delivered,
        "complete": next_offset >= metadata["byte_size"],
        "next_byte_offset": None if next_offset >= metadata["byte_size"] else next_offset,
    }
    if is_text:
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
    # Preflight every replacement before starting the transaction that removes
    # task rows.  A global-document overflow must leave the prune untouched.
    serialized_global_updates: dict[str, str | None] = {}
    for name, value in global_updates.items():
        name_value = _bounded_document_key(name, label="SQLite global document identity")
        if value is not None and not isinstance(value, dict):
            raise ValueError("SQLite global document must be an object")
        serialized_global_updates[name_value] = (
            None
            if value is None
            else _bounded_document_json(value, label=f"SQLite global document {name_value!r}")
        )
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

        # Preserve evidence shared by any task not in this atomic operation.
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
        for name, payload_json in serialized_global_updates.items():
            if payload_json is None:
                connection.execute("DELETE FROM global_documents WHERE name=?", (name,))
            else:
                connection.execute(
                    "INSERT INTO global_documents(name, payload_json, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                    (name, payload_json, _now()),
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
    try:
        parsed_arguments = json.loads(normalized_arguments)
    except json.JSONDecodeError as exc:
        raise ValueError("tool observation arguments are invalid") from exc
    if not isinstance(parsed_arguments, dict):
        raise ValueError("tool observation arguments are invalid")
    tool_name = _bounded_observation_text(tool_name, label="tool_name")
    workspace_generation = _bounded_observation_text(
        workspace_generation, label="workspace_generation"
    )
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
def _hook_write_connection(
    root: Path, *, timeout_ms: int = 100,
) -> Iterator[sqlite3.Connection]:
    """Open an existing ledger for one bounded hook-owned write."""
    identity = _database_file_identity(root)
    if identity is None:
        raise FileNotFoundError("Cortex database is unavailable")
    path = database_path(root)
    bounded_timeout_ms = max(1, min(int(timeout_ms), 4000))
    connection = sqlite3.connect(
        str(path), timeout=bounded_timeout_ms / 1000.0, isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {bounded_timeout_ms}")
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
        parsed_arguments = json.loads(normalized_arguments)
        if not isinstance(parsed_arguments, dict):
            raise ValueError("tool observation arguments are invalid")
        tool_name = _bounded_observation_text(tool_name, label="tool_name")
        workspace_generation = _bounded_observation_text(
            workspace_generation, label="workspace_generation"
        )
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
