"""Private schema-v1 storage for the Cortex V12 task-anchored ledger.

``create_task`` is the sole path-bearing operation.  Later calls decode one
project shard from a task ID and verify the task's persisted canonical root;
there is intentionally no project-wide task search and no V11 access.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from cortex_runtime.model_routing import validate_model_selection
from cortex_runtime.v12_contract import (
    CLOSURE_SUBJECTS, CLOSURE_VERDICTS, DECISION_ATTRIBUTION, DECISION_SUBJECTS,
    canonical_report_semantic_status,
    DECISION_TYPES, DEFAULT_PAGE_LIMIT, DIGEST_RE, GOVERNANCE_MODES,
    GOVERNANCE_SOURCES, IDEMPOTENCY_KEY_MAX_LENGTH, IDENTIFIER_RE,
    INITIATIVE_STATUSES, JSON_MAX_BYTES, JSON_MAX_DEPTH, LANGUAGE_TAG_MAX_LENGTH,
    LANGUAGE_TAG_RE,
    MAX_DECISION_IDS, MAX_LINKS,
    MAX_PAGE_LIMIT, MAX_REPORT_IDS, PROJECT_ROOT_MAX_LENGTH, REPORT_STATUSES,
    PLAN_REVIEW_POLICIES, REPORT_ASSEMBLING_MAX_BYTES_PER_TASK,
    REPORT_ASSEMBLING_MAX_PER_TASK, REPORT_ASSEMBLY_STATES, REPORT_CHUNK_MAX_BYTES,
    REPORT_MAX_BYTES, REPORT_MAX_CHUNKS, REPORT_MODES, REPORT_READ_MAX_BYTES,
    REPORT_RESPONSE_MAX_BYTES, REPORT_RETAINED_MAX_BYTES_PER_TASK,
    REPORT_SECTION_MAX_LENGTH, REPORT_SECTION_RE, REPORT_SINGLE_MAX_BYTES, REPORT_TYPES, ROLE_MAX_LENGTH, TASK_CONTRACT_ITEM_MAX_LENGTH,
    TASK_CONTRACT_MAX_ITEMS, TASK_CONTRACT_VERSION, TEXT_MAX_LENGTH, new_sharded_id,
    new_task_id, record_ref, record_ref_parts, record_shard_hash, task_ref, task_ref_parts, task_shard_hash,
)

SCHEMA_VERSION = 1
DATABASE_NAME = "cortex.db"
MIGRATION_NAME = "v12-initial"
_EXPANSION_MIGRATION_VERSION = 2
_EXPANSION_MIGRATION_NAME = "v12-schema-v1-human-views"
_PROFILE_BINDING_MIGRATION_VERSION = 3
_PROFILE_BINDING_MIGRATION_NAME = "v12-explicit-profile-binding"
_NATIVE_TASK_NAME_MIGRATION_VERSION = 4
_NATIVE_TASK_NAME_MIGRATION_NAME = "v12-durable-native-task-name"
_REPORT_CONSUMPTION_MIGRATION_VERSION = 5
_REPORT_CONSUMPTION_MIGRATION_NAME = "v12-report-consumption-receipts"
_GOVERNANCE_GATE_MIGRATION_VERSION = 6
_GOVERNANCE_GATE_MIGRATION_NAME = "v12-durable-governance-gate"
_APPROVAL_HANDLE_MIGRATION_VERSION = 7
_APPROVAL_HANDLE_MIGRATION_NAME = "v12-ready-approval-handles"
_ADVISORY_GOVERNANCE_MIGRATION_VERSION = 8
_ADVISORY_GOVERNANCE_MIGRATION_NAME = "v12-advisory-governance"
_REPORT_SEMANTICS_MIGRATION_VERSION = 9
_REPORT_SEMANTICS_MIGRATION_NAME = "v12-canonical-report-semantics"
_APPLICATION_ID = 0x43563132
_TIMELINE_BACKFILL_METADATA_KEY = "timeline_backfill_v1"
_TIMELINE_BACKFILL_VERSION = "cortex/v12-timeline-backfill/v1"
_TIMELINE_BACKFILL_REASON = "timeline_backfill"
_TIMELINE_REPAIR_CONFLICT_WARNING = "timeline_backfill_task_conflict"
_LINK_TYPES = {"parent", "dependency", "task", "delegation", "report", "decision"}
_LEGACY_UNSHARDED_TASK_ID_RE = re.compile(r"^task-[0-9a-f]{32}$")
_LEGACY_V12_MIGRATIONS = ((SCHEMA_VERSION, MIGRATION_NAME),)
# This is the only pre-human-views V12 layout that this runtime accepts for
# automatic migration.  It is deliberately an exact table/column/index
# fingerprint rather than a "close enough" base-table check: user_version=1
# alone is not a compatibility promise for unknown or future schemas.
_LEGACY_V12_COLUMNS = {
    "schema_migrations": ("version", "name", "applied_at"),
    "v12_metadata": ("key", "value"),
    "timeline": (
        "sequence", "occurred_at", "event_type", "entity_type", "entity_id", "task_id", "delegation_id",
        "report_id", "initiative_id", "assessment_id", "closure_id", "payload_json",
    ),
    "tasks": (
        "task_id", "project_hash", "objective", "context_json", "created_at", "updated_at", "created_sequence",
        "updated_sequence",
    ),
    "delegations": (
        "delegation_id", "project_hash", "task_id", "parent_delegation_id", "objective", "role", "scope",
        "instructions", "input_report_ids_json", "model", "reasoning_effort", "created_at", "created_sequence",
    ),
    "reports": (
        "report_id", "project_hash", "task_id", "delegation_id", "report_type", "status", "content_json",
        "created_at", "created_sequence",
    ),
    "idempotency": ("operation", "idempotency_key", "payload_digest", "result_json", "created_at"),
    "governance_assessments": (
        "assessment_id", "project_hash", "task_id", "initiative_id", "mode", "source", "rationale",
        "risk_factors_json", "created_at", "created_sequence",
    ),
    "initiatives": (
        "initiative_id", "project_hash", "goal", "risk", "status", "notes_json", "created_at", "updated_at",
        "latest_revision", "created_sequence", "updated_sequence",
    ),
    "initiative_revisions": (
        "revision_id", "initiative_id", "revision_number", "project_hash", "occurred_at", "sequence", "payload_json",
    ),
    "initiative_links": (
        "link_id", "initiative_id", "project_hash", "relationship", "target_id", "is_resolved", "warnings_json",
        "created_at",
    ),
    "governance_closures": (
        "closure_id", "project_hash", "subject_type", "subject_id", "verdict", "evidence_json",
        "unresolved_risks_json", "follow_ups_json", "initiative_status", "completion_notes_json", "created_at",
        "created_sequence",
    ),
}
_LEGACY_V12_INDEXES = {
    "timeline_task_sequence", "timeline_delegation_sequence", "timeline_initiative_sequence", "reports_task_created",
    "reports_delegation_created", "assessments_task_created", "initiative_links_source",
}
_LEGACY_V12_FOREIGN_KEYS = {
    "delegations": {("delegations", "parent_delegation_id", "delegation_id"), ("tasks", "task_id", "task_id")},
    "reports": {("delegations", "delegation_id", "delegation_id"), ("tasks", "task_id", "task_id")},
    "governance_assessments": {("tasks", "task_id", "task_id")},
    "initiative_revisions": {("initiatives", "initiative_id", "initiative_id")},
    "initiative_links": {("initiatives", "initiative_id", "initiative_id")},
}
T = TypeVar("T")


class V12StoreError(ValueError):
    """A sanitized, public-safe durable-storage failure."""

    def __init__(self, message: str, *, code: str = "v12_invalid", details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def _storage_error(error: BaseException) -> V12StoreError:
    """Classify transient SQLite contention without exposing local diagnostics."""
    sqlite_code = getattr(error, "sqlite_errorcode", None)
    try:
        primary_code = int(sqlite_code) & 0xFF
    except (TypeError, ValueError):
        primary_code = None
    busy_codes = {getattr(sqlite3, "SQLITE_BUSY", -1), getattr(sqlite3, "SQLITE_LOCKED", -1)}
    if primary_code in busy_codes:
        return V12StoreError(
            "V12 storage is busy",
            code="storage_busy",
            details={"retry_after_ms": 100, "retry_with": "same_idempotency_key"},
        )
    return V12StoreError("V12 storage is unavailable", code="storage_unavailable")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _depth(value: Any) -> int:
    if isinstance(value, Mapping):
        return 1 + max((_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(item) for item in value), default=0)
    return 0


def _strict_json(value: Any, *, label: str) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > JSON_MAX_BYTES or _depth(value) > JSON_MAX_DEPTH:
            raise ValueError("bounded JSON violation")
        return json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise V12StoreError(f"{label} is invalid", code="content_invalid", details={"field": label}) from exc


def _canonical_json(value: Any, *, label: str) -> str:
    return json.dumps(_strict_json(value, label=label), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _load_json(value: str, *, label: str) -> Any:
    try:
        return json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt", details={"field": label}) from exc


def _required_text(value: Any, *, label: str, maximum: int = TEXT_MAX_LENGTH) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > maximum:
        raise V12StoreError(f"{label} is required", code="invalid_argument", details={"field": label})
    return value.strip()


def _opaque_text(value: Any, *, label: str, maximum: int = TEXT_MAX_LENGTH) -> str:
    """Retain bounded free text exactly; content quality is coordinator-owned."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > maximum:
        raise V12StoreError(f"{label} is required", code="invalid_argument", details={"field": label})
    return value


def _optional_text(value: Any, *, label: str, maximum: int = TEXT_MAX_LENGTH) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum:
        raise V12StoreError(f"{label} is invalid", code="invalid_argument", details={"field": label})
    return value if value.strip() else None


def _identifier(value: Any, *, label: str) -> str:
    identifier = _required_text(value, label=label, maximum=160)
    if not IDENTIFIER_RE.fullmatch(identifier):
        raise V12StoreError(f"{label} is invalid", code="invalid_identifier", details={"field": label})
    return identifier


def _identifier_list(value: Any, *, label: str, maximum: int = MAX_LINKS, ordered: bool = False, minimum: int = 0, deduplicate: bool = False) -> list[str]:
    if value is None and minimum == 0:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise V12StoreError(f"{label} must be an array", code="invalid_argument", details={"field": label})
    result = [_identifier(item, label=label) for item in value]
    if deduplicate:
        result = list(dict.fromkeys(result))
    if len(result) < minimum or len(result) > maximum or (not deduplicate and len(set(result)) != len(result)):
        raise V12StoreError(f"{label} has an invalid length", code="invalid_argument", details={"field": label})
    return result if ordered else sorted(result)


def _text_list(value: Any, *, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_LINKS:
        raise V12StoreError(f"{label} is invalid", code="invalid_argument", details={"field": label})
    normalized = _strict_json(value, label=label)
    if not all(isinstance(item, str) for item in normalized):
        raise V12StoreError(f"{label} is invalid", code="invalid_argument", details={"field": label})
    return normalized


def _contract_text_list(value: Any, *, label: str) -> list[str]:
    """Normalize one non-empty English task-contract dimension.

    Every list is deliberately required.  An explicit ``No additional
    constraints.`` entry is meaningful for ``constraints``; an empty list is
    not a substitute for that boundary.
    """
    if not isinstance(value, list) or not value or len(value) > TASK_CONTRACT_MAX_ITEMS:
        raise V12StoreError(f"{label} is invalid", code="invalid_argument", details={"field": label})
    result = [
        _opaque_text(item, label=label, maximum=TASK_CONTRACT_ITEM_MAX_LENGTH)
        for item in value
    ]
    return result


def _task_language(value: Any) -> str:
    return _language_tag(value, label="user_language")


def _profile_name(value: Any) -> str:
    """Accept only an explicit profile packaged with this exact plugin build."""
    from cortex_runtime.worker_message import packaged_profile_names

    profile_name = _required_text(value, label="profile_name", maximum=ROLE_MAX_LENGTH)
    if profile_name not in packaged_profile_names():
        raise V12StoreError("profile_name is invalid", code="invalid_argument", details={"field": "profile_name"})
    return profile_name


def _instructions_text(value: Any) -> str:
    """Accept and preserve a bounded non-empty coordinator instruction string."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > TEXT_MAX_LENGTH:
        raise V12StoreError("instructions is required", code="invalid_argument", details={"field": "instructions"})
    return value


def _language_tag(value: Any, *, label: str) -> str:
    """Retain one asserted BCP-47-shaped tag without interpreting user prose."""
    language = _opaque_text(value, label=label, maximum=LANGUAGE_TAG_MAX_LENGTH)
    if LANGUAGE_TAG_RE.fullmatch(language) is None:
        raise V12StoreError(f"{label} is invalid", code="invalid_argument", details={"field": label})
    return language


def _language(value: Any, *, label: str = "user_language") -> str:
    return _language_tag(value, label=label)


def _digest(value: Any, *, label: str = "subject_digest", required: bool = False) -> str | None:
    if value is None:
        if required:
            raise V12StoreError(f"{label} is required", code="invalid_argument", details={"field": label})
        return None
    candidate = _required_text(value, label=label, maximum=71)
    if DIGEST_RE.fullmatch(candidate) is None:
        raise V12StoreError(f"{label} is invalid", code="invalid_argument", details={"field": label})
    return candidate


def _sha256_prefixed(value: Any, *, label: str) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value, label=label).encode("utf-8")).hexdigest()


def _canonical_json_bytes(value: Any, *, label: str) -> tuple[Any, str, int, str]:
    normalized = _strict_json(value, label=label)
    rendered = _canonical_json(normalized, label=label)
    encoded = rendered.encode("utf-8")
    return normalized, rendered, len(encoded), "sha256:" + hashlib.sha256(encoded).hexdigest()


def _report_manifest(chunks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "cortex/report-content/v1",
        "chunks": [
            {
                "index": int(chunk["chunk_index"]),
                "section": str(chunk["section"]),
                "content_digest": str(chunk["content_digest"]),
                "content_bytes": int(chunk["content_bytes"]),
            }
            for chunk in chunks
        ],
    }


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else {str(key): row[key] for key in row.keys()}


class V12Store:
    """One private V12 SQLite shard; all methods preserve schema version 1."""

    def __init__(self, project_root: str | os.PathLike[str]) -> None:
        raw = _required_text(os.fspath(project_root), label="project_root", maximum=PROJECT_ROOT_MAX_LENGTH)
        try:
            root = Path(raw).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise V12StoreError("project_root is unavailable", code="project_root_invalid") from exc
        if not root.is_dir():
            raise V12StoreError("project_root must be a directory", code="project_root_invalid")
        self.project_root: Path | None = root
        self.project_hash = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
        self._guard = threading.RLock()
        self._timeline_backfilled_tasks: set[str] = set()
        self._set_paths()
        self._bootstrap()

    @classmethod
    def for_task_id(cls, task_id: object) -> "V12Store":
        identifier = _identifier(task_id, label="task_id")
        shard = task_shard_hash(identifier)
        if shard is None:
            raise V12StoreError("task_id is invalid", code="invalid_identifier", details={"field": "task_id"})
        store = cls.__new__(cls)
        store.project_root = None
        store.project_hash = shard
        store._guard = threading.RLock()
        store._timeline_backfilled_tasks = set()
        store._set_paths()
        store._verify_known_task(identifier)
        return store

    @classmethod
    def for_task_ref(cls, value: object) -> tuple["V12Store", str]:
        """Resolve one exact compact task locator to its canonical task ID.

        Compact references are deliberately not persisted and never name a
        project root. Resolution scans only private V12 shard directories for
        one exact task UUID suffix. Any zero or ambiguous match fails closed;
        this routine never corrects, expands, or guesses an identifier.
        """
        task_suffix = task_ref_parts(value)
        if task_suffix is None:
            raise V12StoreError("task_ref is invalid", code="invalid_identifier", details={"field": "task_ref"})
        home = Path(os.environ.get("HOME") or str(Path.home())).expanduser()
        projects = home / ".codex" / "cortex" / "v12" / "projects"
        try:
            shards = [
                entry.name[2:]
                for entry in os.scandir(projects)
                if entry.name.startswith("p-")
                and re.fullmatch(r"p-[0-9a-f]{64}", entry.name)
                and entry.is_dir(follow_symlinks=False)
            ]
        except FileNotFoundError:
            shards = []
        except OSError as exc:
            raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc
        matches: list[tuple[V12Store, str]] = []
        for shard in shards:
            store = cls.__new__(cls)
            store.project_root = None
            store.project_hash = shard
            store._guard = threading.RLock()
            store._timeline_backfilled_tasks = set()
            store._set_paths()
            try:
                task_id = store._task_id_for_ref_suffix(task_suffix)
            except V12StoreError as exc:
                if exc.code == "task_not_found":
                    continue
                raise
            matches.append((store, task_id))
        if len(matches) == 0:
            raise V12StoreError("task was not found", code="task_not_found")
        if len(matches) != 1:
            raise V12StoreError("task_ref is ambiguous", code="task_ref_ambiguous", details={"field": "task_ref"})
        store, task_id = matches[0]
        store._verify_known_task(task_id)
        return store, task_id

    @classmethod
    def for_record_id(cls, record_id: object, *, label: str) -> "V12Store":
        """Open the one known shard encoded by a durable record identifier."""
        identifier = _identifier(record_id, label=label)
        shard = record_shard_hash(identifier)
        if shard is None:
            raise V12StoreError(f"{label} is invalid", code="invalid_identifier", details={"field": label})
        store = cls.__new__(cls)
        store.project_root = None
        store.project_hash = shard
        store._guard = threading.RLock()
        store._timeline_backfilled_tasks = set()
        store._set_paths()
        store._verify_known_record(identifier, label=label)
        return store

    @classmethod
    def for_record_ref(cls, value: object, *, label: str) -> tuple["V12Store", str]:
        """Resolve one typed public record ref across private V12 shards only."""
        suffix = record_ref_parts(value, label=label)
        if suffix is None:
            raise V12StoreError(f"{label} is invalid", code="invalid_identifier", details={"field": label})
        home = Path(os.environ.get("HOME") or str(Path.home())).expanduser()
        projects = home / ".codex" / "cortex" / "v12" / "projects"
        try:
            shards = [entry.name[2:] for entry in os.scandir(projects) if entry.name.startswith("p-") and re.fullmatch(r"p-[0-9a-f]{64}", entry.name) and entry.is_dir(follow_symlinks=False)]
        except FileNotFoundError:
            shards = []
        except OSError as exc:
            raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc
        matches: list[tuple[V12Store, str]] = []
        for shard in shards:
            store = cls.__new__(cls)
            store.project_root = None
            store.project_hash = shard
            store._guard = threading.RLock()
            store._timeline_backfilled_tasks = set()
            store._set_paths()
            try:
                identifier = store._record_id_for_ref_suffix(suffix, label=label)
            except V12StoreError as exc:
                if exc.code in {f"{label.removesuffix('_id')}_not_found", "delegation_not_found", "report_not_found", "initiative_not_found", "decision_not_found"}:
                    continue
                raise
            matches.append((store, identifier))
        if len(matches) == 0:
            raise V12StoreError(f"{label} was not found", code=f"{label.removesuffix('_id')}_not_found")
        if len(matches) != 1:
            raise V12StoreError(f"{label} is ambiguous", code="record_ref_ambiguous", details={"field": label})
        store, identifier = matches[0]
        store._verify_known_record(identifier, label=label)
        return store, identifier

    def _set_paths(self) -> None:
        self._home = Path(os.environ.get("HOME") or str(Path.home())).expanduser()
        self.root = self._home / ".codex" / "cortex" / "v12" / "projects" / f"p-{self.project_hash}"
        self.database_path = self.root / DATABASE_NAME
        # Human-readable views live beside the canonical database but never in
        # project_root.  Task IDs are generated/validated identifiers, so this
        # is a server-generated contained path rather than caller path input.
        self.tasks_root = self.root / "tasks"

    def _task_identifier(self, value: Any, *, label: str = "task_id") -> str:
        identifier = _identifier(value, label=label)
        shard = task_shard_hash(identifier)
        if shard is None:
            raise V12StoreError(f"{label} is invalid", code="invalid_identifier", details={"field": label})
        if shard != self.project_hash:
            raise V12StoreError("reference belongs to another project", code="cross_project_reference")
        return identifier

    def _task_id_for_ref_suffix(self, suffix: str) -> str:
        """Find exactly one canonical task in this already-selected shard."""
        try:
            self._check_open_paths(database_required=True)
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT task_id FROM tasks WHERE project_hash=? AND substr(task_id, -?)=? LIMIT 2",
                    (self.project_hash, len(suffix), suffix),
                ).fetchall()
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc
        if len(rows) == 0:
            raise V12StoreError("task was not found", code="task_not_found")
        if len(rows) != 1:
            raise V12StoreError("task_ref is ambiguous", code="task_ref_ambiguous", details={"field": "task_ref"})
        identifier = str(rows[0]["task_id"])
        if task_ref(identifier) is None or not identifier.endswith(suffix):
            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
        return identifier

    def _record_identifier(self, value: Any, *, label: str) -> str:
        identifier = _identifier(value, label=label)
        shard = record_shard_hash(identifier)
        if shard is not None and shard != self.project_hash:
            raise V12StoreError("reference belongs to another project", code="cross_project_reference")
        return identifier

    def _record_id_for_ref_suffix(self, suffix: str, *, label: str) -> str:
        table = {"delegation_id": ("delegations", "delegation_id"), "report_id": ("reports", "report_id"), "initiative_id": ("initiatives", "initiative_id"), "decision_id": ("user_decisions", "decision_id")}.get(label)
        if table is None:
            raise V12StoreError(f"{label} is invalid", code="invalid_identifier", details={"field": label})
        table_name, column = table
        try:
            self._check_open_paths(database_required=True)
            with self._connection() as connection:
                rows = connection.execute(f"SELECT {column} FROM {table_name} WHERE project_hash=? AND substr({column}, -?)=? LIMIT 2", (self.project_hash, len(suffix), suffix)).fetchall()
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc
        if len(rows) == 0:
            raise V12StoreError(f"{label} was not found", code=f"{label.removesuffix('_id')}_not_found")
        if len(rows) != 1:
            raise V12StoreError(f"{label} is ambiguous", code="record_ref_ambiguous", details={"field": label})
        identifier = str(rows[0][0])
        if record_ref(identifier) is None or not identifier.endswith(suffix):
            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
        return identifier

    def resolve_record_ref(self, value: Any, *, label: str) -> str:
        """Resolve one canonical public ref within this known shard."""
        suffix = record_ref_parts(value, label=label)
        if suffix is None:
            raise V12StoreError(f"{label} is invalid", code="invalid_identifier", details={"field": label})
        return self._record_id_for_ref_suffix(suffix, label=label)

    @staticmethod
    def _directory(path: Path, *, normalize: bool) -> None:
        try:
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise V12StoreError("V12 storage is unavailable", code="storage_unavailable")
            if normalize:
                os.chmod(path, 0o700)
        except V12StoreError:
            raise
        except OSError as exc:
            raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc

    @staticmethod
    def _regular(path: Path, *, required: bool) -> None:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            if required:
                raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from None
            return
        except OSError as exc:
            raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise V12StoreError("V12 storage is unavailable", code="storage_unavailable")

    def _ensure_root(self) -> None:
        # The general .codex directory is user-owned; only V12 directories are
        # permission-normalized.  All components are lstat-checked.
        components = [
            (self._home / ".codex", False), (self._home / ".codex" / "cortex", False),
            (self._home / ".codex" / "cortex" / "v12", True),
            (self._home / ".codex" / "cortex" / "v12" / "projects", True), (self.root, True),
        ]
        for directory, normalize in components:
            try:
                directory.mkdir(mode=0o700, exist_ok=True)
            except OSError as exc:
                raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc
            self._directory(directory, normalize=normalize)

    def _check_open_paths(self, *, database_required: bool) -> None:
        self._directory(self.root, normalize=True)
        self._regular(self.database_path, required=database_required)
        self._regular(Path(f"{self.database_path}-wal"), required=False)
        self._regular(Path(f"{self.database_path}-shm"), required=False)

    def _precreate_database(self) -> None:
        """Create the SQLite inode owner-only before SQLite can apply umask.

        The caller has already lstat-validated every state-directory ancestor.
        O_EXCL plus a second lstat protects against ordinary path races without
        making a host-native authority claim.
        """
        self._regular(self.database_path, required=False)
        if self.database_path.exists():
            self._regular(self.database_path, required=True)
            return
        descriptor = -1
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.database_path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        except FileExistsError:
            pass
        except OSError as exc:
            raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        self._regular(self.database_path, required=True)

    def _protect_files(self) -> None:
        for path in (self.database_path, Path(f"{self.database_path}-wal"), Path(f"{self.database_path}-shm")):
            self._regular(path, required=False)
            if path.exists():
                try:
                    os.chmod(path, 0o600)
                except OSError as exc:
                    raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc

    def _materialize_sidecars(self) -> None:
        """Leave owner-only sidecars in place between SQLite connections.

        SQLite may unlink empty WAL/SHM files when its last connection closes.
        Recreating zero-length regular placeholders after close keeps the
        documented V12 file set private; SQLite safely replaces them on the
        next WAL open.  Every creation is still preceded by an lstat check.
        """
        for path in (Path(f"{self.database_path}-wal"), Path(f"{self.database_path}-shm")):
            self._regular(path, required=False)
            if not path.exists():
                try:
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    os.close(descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc
            self._regular(path, required=True)
            try:
                os.chmod(path, 0o600)
            except OSError as exc:
                raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc

    def _bootstrap(self) -> None:
        try:
            self._ensure_root()
            with self._connection(database_required=False) as connection:
                connection.execute("BEGIN IMMEDIATE")
                tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if tables:
                    try:
                        self._migrate_schema_v1_expansion(connection)
                        self._migrate_explicit_profile_binding(connection)
                        self._migrate_durable_native_task_name(connection)
                        self._migrate_report_consumption_receipts(connection)
                        self._migrate_durable_governance_gate(connection)
                        self._migrate_ready_approval_handles(connection)
                        self._migrate_advisory_governance(connection)
                        self._migrate_canonical_report_semantics(connection)
                        self._validate_existing(connection)
                        self._timeline_backfilled_tasks = self._backfill_task_timelines(connection)
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                    connection.execute("COMMIT")
                else:
                    try:
                        self._create_schema(connection)
                        connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (SCHEMA_VERSION, MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_APPROVAL_HANDLE_MIGRATION_VERSION, _APPROVAL_HANDLE_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_ADVISORY_GOVERNANCE_MIGRATION_VERSION, _ADVISORY_GOVERNANCE_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_REPORT_SEMANTICS_MIGRATION_VERSION, _REPORT_SEMANTICS_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO v12_metadata(key,value) VALUES ('project_hash', ?)", (self.project_hash,))
                        connection.execute("INSERT INTO v12_metadata(key,value) VALUES ('project_root_digest', ?)", (hashlib.sha256(str(self.project_root).encode("utf-8")).hexdigest(),))
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                    connection.execute("COMMIT")
            self._protect_files()
            self._materialize_timeline_backfills()
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc

    @staticmethod
    def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _ordered_column_names(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
        return tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))

    def _is_known_legacy_v12_schema(self, connection: sqlite3.Connection) -> bool:
        """Recognize only the released pre-human-views V12 layout.

        Legacy task rows did not retain ``project_root``.  A path-bearing
        first open is therefore the sole safe migration entry: its canonical
        root must agree with the old shard metadata before that root is copied
        into the added column.  Task-ID-only opens cannot invert a project
        hash and must remain fail-closed until such a normal open occurs.
        """
        if self.project_root is None:
            return False
        if (
            int(connection.execute("PRAGMA application_id").fetchone()[0]) != _APPLICATION_ID
            or int(connection.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION
        ):
            return False
        migrations = tuple(
            (int(row[0]), str(row[1]))
            for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version")
        )
        if migrations != _LEGACY_V12_MIGRATIONS:
            return False
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }
        if tables != set(_LEGACY_V12_COLUMNS):
            return False
        if any(self._ordered_column_names(connection, table) != columns for table, columns in _LEGACY_V12_COLUMNS.items()):
            return False
        indexes = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        }
        triggers = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        if indexes != _LEGACY_V12_INDEXES or triggers:
            return False
        for table in _LEGACY_V12_COLUMNS:
            foreign_keys = {
                (str(row[2]), str(row[3]), str(row[4]))
                for row in connection.execute(f"PRAGMA foreign_key_list({table})")
            }
            if foreign_keys != _LEGACY_V12_FOREIGN_KEYS.get(table, set()):
                return False
        metadata = {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key,value FROM v12_metadata")
        }
        canonical = str(self.project_root)
        if metadata != {
            "project_hash": self.project_hash,
            "project_root_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }:
            return False
        for table in ("tasks", "delegations", "reports", "governance_assessments", "initiatives", "governance_closures"):
            if connection.execute(f"SELECT 1 FROM {table} WHERE project_hash<>? LIMIT 1", (self.project_hash,)).fetchone() is not None:
                return False
        return True

    def _migrate_schema_v1_expansion(self, connection: sqlite3.Connection) -> None:
        """Apply the one pre-release additive schema-v1 migration explicitly.

        Validation itself never edits schema.  Existing v12 rows receive
        conservative, immutable defaults so prior task IDs and objectives stay
        usable without reading or touching V11 state.
        """
        if int(connection.execute("PRAGMA application_id").fetchone()[0]) != _APPLICATION_ID or int(connection.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        migration = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_EXPANSION_MIGRATION_VERSION,)).fetchone()
        if migration is not None:
            if str(migration[0]) != _EXPANSION_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        if not self._is_known_legacy_v12_schema(connection):
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            for statement in (
                "ALTER TABLE timeline ADD COLUMN decision_id TEXT",
                "ALTER TABLE tasks ADD COLUMN project_root TEXT",
                "ALTER TABLE tasks ADD COLUMN user_request_original TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE tasks ADD COLUMN user_language TEXT NOT NULL DEFAULT 'und'",
                f"ALTER TABLE tasks ADD COLUMN task_contract_version TEXT NOT NULL DEFAULT '{TASK_CONTRACT_VERSION}'",
                "ALTER TABLE tasks ADD COLUMN requirements_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE tasks ADD COLUMN constraints_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE tasks ADD COLUMN acceptance_criteria_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE tasks ADD COLUMN verification_plan_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE delegations ADD COLUMN input_decision_ids_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE reports ADD COLUMN assembly_state TEXT NOT NULL DEFAULT 'finalized'",
                "ALTER TABLE reports ADD COLUMN next_chunk_index INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE reports ADD COLUMN total_chunks INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE reports ADD COLUMN total_bytes INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE reports ADD COLUMN content_digest TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE reports ADD COLUMN supersedes_report_id TEXT",
                "ALTER TABLE reports ADD COLUMN review_policy TEXT",
                "ALTER TABLE reports ADD COLUMN finalized_at TEXT",
                "ALTER TABLE reports ADD COLUMN finalized_sequence INTEGER",
                "ALTER TABLE reports ADD COLUMN aborted_at TEXT",
                "ALTER TABLE reports ADD COLUMN aborted_sequence INTEGER",
                "ALTER TABLE reports ADD COLUMN abort_reason_en TEXT",
            ):
                connection.execute(statement)
            connection.execute("UPDATE tasks SET project_root=? WHERE project_hash=?", (str(self.project_root), self.project_hash))
            connection.execute("UPDATE tasks SET user_request_original=objective WHERE user_request_original=''" )
            connection.execute("CREATE TABLE user_decisions(decision_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,subject_digest TEXT,decision_type TEXT NOT NULL,prompt_en TEXT NOT NULL,response_original TEXT NOT NULL,response_en TEXT NOT NULL,user_language TEXT NOT NULL,attribution TEXT NOT NULL,supersedes_decision_id TEXT REFERENCES user_decisions(decision_id),created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL)")
            connection.execute("CREATE TABLE report_chunks(report_id TEXT NOT NULL REFERENCES reports(report_id),chunk_index INTEGER NOT NULL,section TEXT NOT NULL,content_json TEXT NOT NULL,content_digest TEXT NOT NULL,content_bytes INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(report_id,chunk_index))")
            connection.execute("CREATE TABLE report_usage(task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),total_retained_bytes INTEGER NOT NULL,assembling_bytes INTEGER NOT NULL,assembling_reports INTEGER NOT NULL,updated_at TEXT NOT NULL)")
            connection.execute("CREATE TABLE projection_jobs(job_id INTEGER PRIMARY KEY AUTOINCREMENT,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),source_sequence INTEGER NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL,lease_token TEXT,lease_expires_at TEXT,last_error_code TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(task_id,source_sequence,reason))")
            connection.execute("CREATE TABLE projection_files(task_id TEXT NOT NULL REFERENCES tasks(task_id),relative_path TEXT NOT NULL,source_sequence INTEGER NOT NULL,renderer_version TEXT NOT NULL,content_digest TEXT NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(task_id,relative_path))")
            for statement in (
                "CREATE INDEX decisions_task_created ON user_decisions(task_id,created_sequence)",
                "CREATE INDEX report_chunks_report_order ON report_chunks(report_id,chunk_index)",
                "CREATE INDEX timeline_decision_sequence ON timeline(decision_id,sequence)",
                "CREATE INDEX projection_jobs_pending ON projection_jobs(status,lease_expires_at,job_id)",
                "CREATE TRIGGER reports_no_delete BEFORE DELETE ON reports BEGIN SELECT RAISE(ABORT,'reports are immutable'); END",
                "CREATE TRIGGER decisions_no_update BEFORE UPDATE ON user_decisions BEGIN SELECT RAISE(ABORT,'decisions are append-only'); END",
                "CREATE TRIGGER decisions_no_delete BEFORE DELETE ON user_decisions BEGIN SELECT RAISE(ABORT,'decisions are append-only'); END",
            ):
                connection.execute(statement)
            # Convert the pre-release one-column report bodies into verified single
            # finalized chunks.  V11 is never opened; this only upgrades an
            # existing V12 shard inside the same atomic migration.
            usage_by_task: dict[str, int] = {}
            for row in connection.execute("SELECT report_id,task_id,content_json,created_at,created_sequence FROM reports").fetchall():
                content = _load_json(str(row["content_json"]), label="legacy report content")
                _normalized, rendered, size, digest = _canonical_json_bytes(content, label="legacy report content")
                report_id, task_value = str(row["report_id"]), str(row["task_id"])
                connection.execute("INSERT INTO report_chunks(report_id,chunk_index,section,content_json,content_digest,content_bytes,created_at) VALUES (?, 0, 'body', ?, ?, ?, ?)", (report_id, rendered, digest, size, str(row["created_at"])))
                whole = _sha256_prefixed({"schema": "cortex/report-content/v1", "chunks": [{"index": 0, "section": "body", "content_digest": digest, "content_bytes": size}]}, label="legacy report manifest")
                connection.execute("UPDATE reports SET assembly_state='finalized',next_chunk_index=1,total_chunks=1,total_bytes=?,content_digest=?,finalized_at=COALESCE(finalized_at,created_at),finalized_sequence=COALESCE(finalized_sequence,created_sequence) WHERE report_id=?", (size, whole, report_id))
                usage_by_task[task_value] = usage_by_task.get(task_value, 0) + size
            for task_value, bytes_value in usage_by_task.items():
                connection.execute("INSERT INTO report_usage(task_id,total_retained_bytes,assembling_bytes,assembling_reports,updated_at) VALUES (?, ?, 0, 0, ?)", (task_value, bytes_value, _now()))
            for statement in (
                "CREATE TRIGGER reports_terminal_no_update BEFORE UPDATE ON reports WHEN OLD.assembly_state IN ('finalized','aborted') BEGIN SELECT RAISE(ABORT,'terminal reports are immutable'); END",
                "CREATE TRIGGER report_chunks_no_update BEFORE UPDATE ON report_chunks BEGIN SELECT RAISE(ABORT,'report chunks are immutable'); END",
                "CREATE TRIGGER report_chunks_no_delete BEFORE DELETE ON report_chunks BEGIN SELECT RAISE(ABORT,'report chunks are immutable'); END",
            ):
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_explicit_profile_binding(self, connection: sqlite3.Connection) -> None:
        """Bind every V12 delegation to one explicit packaged advisory profile.

        The former ``role`` field is an ordinary human-readable assignment
        label.  It must never silently select a profile.  Existing durable rows
        receive the explicit conservative ``general`` profile once so reads and
        re-rendering remain deterministic; all new delegations must supply a
        validated ``profile_name`` before the mutation path starts.
        """
        migration = connection.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (_PROFILE_BINDING_MIGRATION_VERSION,),
        ).fetchone()
        if migration is not None:
            if str(migration[0]) != _PROFILE_BINDING_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        migrations = [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()]
        if migrations != [(SCHEMA_VERSION, MIGRATION_NAME), (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME)]:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        if "profile_name" in self._column_names(connection, "delegations"):
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("ALTER TABLE delegations ADD COLUMN profile_name TEXT NOT NULL DEFAULT 'general'")
            connection.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)",
                (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME, _now()),
            )
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_durable_native_task_name(self, connection: sqlite3.Connection) -> None:
        """Persist the server-derived native worker handle for every delegation.

        The native host owns lifecycle semantics, so this column is evidence of
        the exact requested handle, not a receipt that the host created or can
        still resume it.  Existing V12 delegations are populated atomically
        from their immutable delegation IDs; a mismatch is corruption rather
        than a reason to invent or repair a handle in coordinator code.
        """
        migration = connection.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (_NATIVE_TASK_NAME_MIGRATION_VERSION,),
        ).fetchone()
        if migration is not None:
            if str(migration[0]) != _NATIVE_TASK_NAME_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        migrations = [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()]
        if migrations != [
            (SCHEMA_VERSION, MIGRATION_NAME),
            (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
        ] or "native_task_name" in self._column_names(connection, "delegations"):
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        from cortex_runtime.delegation import legacy_native_task_name

        try:
            connection.execute("ALTER TABLE delegations ADD COLUMN native_task_name TEXT")
            for row in connection.execute("SELECT delegation_id FROM delegations ORDER BY delegation_id").fetchall():
                delegation_id = str(row["delegation_id"])
                connection.execute(
                    "UPDATE delegations SET native_task_name=? WHERE delegation_id=?",
                    (legacy_native_task_name(delegation_id), delegation_id),
                )
            connection.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)",
                (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME, _now()),
            )
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_report_consumption_receipts(self, connection: sqlite3.Connection) -> None:
        """Add immutable, page-level evidence that a caller read report chunks.

        A receipt proves the ledger returned the identified chunks to the named
        caller.  It is deliberately not a claim that free-text reasoning used
        them or that a native host worker is still resumable.
        """
        migration = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_REPORT_CONSUMPTION_MIGRATION_VERSION,)).fetchone()
        if migration is not None:
            if str(migration[0]) != _REPORT_CONSUMPTION_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        migrations = [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()]
        if migrations != [
            (SCHEMA_VERSION, MIGRATION_NAME),
            (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
        ] or "report_consumption_receipts" in {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("CREATE TABLE report_consumption_receipts(receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),consumer_delegation_id TEXT REFERENCES delegations(delegation_id),reader_kind TEXT NOT NULL,report_id TEXT NOT NULL REFERENCES reports(report_id),observed_content_digest TEXT NOT NULL,sections_json TEXT NOT NULL,input_cursor TEXT,output_cursor TEXT,chunk_indexes_json TEXT NOT NULL,returned_content_bytes INTEGER NOT NULL,has_more INTEGER NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL)")
            connection.execute("CREATE INDEX consumption_task_sequence ON report_consumption_receipts(task_id,created_sequence)")
            connection.execute("CREATE INDEX consumption_delegation_report ON report_consumption_receipts(consumer_delegation_id,report_id,created_sequence)")
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_durable_governance_gate(self, connection: sqlite3.Connection) -> None:
        migration = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_GOVERNANCE_GATE_MIGRATION_VERSION,)).fetchone()
        if migration is not None:
            if str(migration[0]) != _GOVERNANCE_GATE_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        expected = [
            (SCHEMA_VERSION, MIGRATION_NAME),
            (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
            (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME),
        ]
        migrations = [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()]
        if migrations != expected or "governance_gates" in {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("CREATE TABLE governance_gates(task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),assessment_id TEXT NOT NULL REFERENCES governance_assessments(assessment_id),mode TEXT NOT NULL,plan_required INTEGER NOT NULL,user_approval_required INTEGER NOT NULL,allowed_preapproval_profiles_json TEXT NOT NULL,plan_report_id TEXT REFERENCES reports(report_id),plan_digest TEXT,approval_decision_id TEXT REFERENCES user_decisions(decision_id),created_sequence INTEGER NOT NULL,updated_sequence INTEGER NOT NULL)")
            connection.execute("CREATE INDEX governance_gates_assessment ON governance_gates(assessment_id)")
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_ready_approval_handles(self, connection: sqlite3.Connection) -> None:
        """Add opaque handles that prove one ready plan-review snapshot.

        This is deliberately a relation, not a host-authenticated user-turn
        receipt.  It can prove the server exposed a particular ready view before
        a later decision write, but ordinary-chat attribution remains the
        coordinator's honest assertion.
        """
        migration = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_APPROVAL_HANDLE_MIGRATION_VERSION,)).fetchone()
        if migration is not None:
            if str(migration[0]) != _APPROVAL_HANDLE_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        expected = [
            (SCHEMA_VERSION, MIGRATION_NAME),
            (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
            (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME),
            (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME),
        ]
        migrations = [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()]
        if migrations != expected or "approval_handles" in {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("CREATE TABLE approval_handles(approval_handle TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),report_id TEXT NOT NULL REFERENCES reports(report_id),report_content_digest TEXT NOT NULL,view_relative_path TEXT NOT NULL,view_content_digest TEXT NOT NULL,view_source_sequence INTEGER NOT NULL,request_digest TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,consumed_decision_id TEXT REFERENCES user_decisions(decision_id),UNIQUE(task_id,report_id,report_content_digest,view_content_digest,view_source_sequence))")
            connection.execute("CREATE INDEX approval_handles_task_report ON approval_handles(task_id,report_id,created_sequence)")
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_APPROVAL_HANDLE_MIGRATION_VERSION, _APPROVAL_HANDLE_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_advisory_governance(self, connection: sqlite3.Connection) -> None:
        """Retire the legacy gate projection without rewriting durable evidence.

        Assessments, reports, decisions, initiatives, and closures remain
        authoritative append-only evidence.  The former gate was only a
        derived workflow projection, so it is intentionally removed rather
        than migrated into another admission mechanism.
        """
        migration = connection.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (_ADVISORY_GOVERNANCE_MIGRATION_VERSION,),
        ).fetchone()
        if migration is not None:
            if str(migration[0]) != _ADVISORY_GOVERNANCE_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        expected = [
            (SCHEMA_VERSION, MIGRATION_NAME),
            (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
            (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME),
            (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME),
            (_APPROVAL_HANDLE_MIGRATION_VERSION, _APPROVAL_HANDLE_MIGRATION_NAME),
        ]
        migrations = [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()]
        if migrations != expected:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("DROP TABLE IF EXISTS governance_gates")
            connection.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)",
                (_ADVISORY_GOVERNANCE_MIGRATION_VERSION, _ADVISORY_GOVERNANCE_MIGRATION_NAME, _now()),
            )
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_canonical_report_semantics(self, connection: sqlite3.Connection) -> None:
        """Add non-gating semantic classification for canonical report data."""
        migration = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_REPORT_SEMANTICS_MIGRATION_VERSION,)).fetchone()
        if migration is not None:
            if str(migration[0]) != _REPORT_SEMANTICS_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        expected = [
            (SCHEMA_VERSION, MIGRATION_NAME),
            (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
            (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME),
            (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME),
            (_APPROVAL_HANDLE_MIGRATION_VERSION, _APPROVAL_HANDLE_MIGRATION_NAME),
            (_ADVISORY_GOVERNANCE_MIGRATION_VERSION, _ADVISORY_GOVERNANCE_MIGRATION_NAME),
        ]
        migrations = [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()]
        if migrations != expected or "semantic_status" in self._column_names(connection, "reports"):
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("DROP TRIGGER reports_terminal_no_update")
            connection.execute("ALTER TABLE reports ADD COLUMN semantic_status TEXT")
            connection.execute("UPDATE reports SET semantic_status=CASE WHEN assembly_state='assembling' THEN 'pending' ELSE 'legacy' END")
            connection.execute("CREATE TRIGGER reports_terminal_no_update BEFORE UPDATE ON reports WHEN OLD.assembly_state IN ('finalized','aborted') BEGIN SELECT RAISE(ABORT,'terminal reports are immutable'); END")
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_REPORT_SEMANTICS_MIGRATION_VERSION, _REPORT_SEMANTICS_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _validate_existing(self, connection: sqlite3.Connection) -> None:
        if int(connection.execute("PRAGMA application_id").fetchone()[0]) != _APPLICATION_ID or int(connection.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        migrations = [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()]
        metadata = connection.execute("SELECT value FROM v12_metadata WHERE key='project_hash'").fetchone()
        if migrations != [
            (SCHEMA_VERSION, MIGRATION_NAME),
            (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
            (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME),
            (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME),
            (_APPROVAL_HANDLE_MIGRATION_VERSION, _APPROVAL_HANDLE_MIGRATION_NAME),
            (_ADVISORY_GOVERNANCE_MIGRATION_VERSION, _ADVISORY_GOVERNANCE_MIGRATION_NAME),
            (_REPORT_SEMANTICS_MIGRATION_VERSION, _REPORT_SEMANTICS_MIGRATION_NAME),
        ] or metadata is None or str(metadata[0]) != self.project_hash:
            raise V12StoreError("reference belongs to another project", code="cross_project_reference")
        required_columns = {
            "tasks": {"task_id", "project_hash", "project_root", "objective", "user_request_original", "user_language", "task_contract_version", "requirements_json", "constraints_json", "acceptance_criteria_json", "verification_plan_json", "context_json"},
            "delegations": {"delegation_id", "task_id", "profile_name", "native_task_name", "input_report_ids_json", "input_decision_ids_json"},
            "reports": {"report_id", "task_id", "assembly_state", "next_chunk_index", "total_chunks", "total_bytes", "content_digest", "supersedes_report_id", "review_policy", "semantic_status"},
            "report_chunks": {"report_id", "chunk_index", "section", "content_json", "content_digest", "content_bytes"},
            "report_usage": {"task_id", "total_retained_bytes", "assembling_bytes", "assembling_reports"},
            "timeline": {"sequence", "task_id", "decision_id", "payload_json"},
            "user_decisions": {"decision_id", "task_id", "subject_type", "subject_id", "decision_type", "response_original", "response_en", "attribution"},
            "projection_jobs": {"job_id", "task_id", "source_sequence", "status"},
            "projection_files": {"task_id", "relative_path", "content_digest", "status"},
            "report_consumption_receipts": {"task_id", "consumer_delegation_id", "reader_kind", "report_id", "observed_content_digest", "sections_json", "input_cursor", "output_cursor", "chunk_indexes_json", "returned_content_bytes", "has_more", "created_sequence"},
            "approval_handles": {"approval_handle", "task_id", "report_id", "report_content_digest", "view_relative_path", "view_content_digest", "view_source_sequence", "request_digest", "created_sequence", "consumed_decision_id"},
        }
        for table, columns in required_columns.items():
            if not columns.issubset(self._column_names(connection, table)):
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        from cortex_runtime.delegation import is_profile_native_task_name, legacy_native_task_name
        seen_native_names: set[tuple[str, str]] = set()
        for row in connection.execute("SELECT task_id,delegation_id,profile_name,native_task_name FROM delegations").fetchall():
            native_name = str(row["native_task_name"])
            native_key = (str(row["task_id"]), native_name)
            if (
                native_key in seen_native_names
                or (
                    native_name != legacy_native_task_name(str(row["delegation_id"]))
                    and not is_profile_native_task_name(native_name, str(row["profile_name"]))
                )
            ):
                raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
            seen_native_names.add(native_key)
        objects = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('index','trigger')")}
        if not {"reports_terminal_no_update", "reports_no_delete", "report_chunks_no_update", "report_chunks_no_delete", "decisions_no_update", "decisions_no_delete", "decisions_task_created", "report_chunks_report_order", "timeline_decision_sequence", "projection_jobs_pending", "consumption_task_sequence", "consumption_delegation_report", "approval_handles_task_report"}.issubset(objects):
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        if self.project_root is not None:
            canonical = str(self.project_root)
            digest = connection.execute("SELECT value FROM v12_metadata WHERE key='project_root_digest'").fetchone()
            if digest is None or str(digest[0]) != hashlib.sha256(canonical.encode("utf-8")).hexdigest():
                raise V12StoreError("reference belongs to another project", code="cross_project_reference")
            connection.execute("UPDATE tasks SET project_root=? WHERE project_hash=? AND project_root IS NULL", (canonical, self.project_hash))

    def _materialize_timeline_backfills(self) -> None:
        """Refresh derived views after a committed, one-time timeline repair.

        The canonical repair is complete before this best-effort pass begins.
        A failed Markdown write therefore cannot roll back, suppress, or alter
        the repaired chronology.
        """
        task_ids = tuple(sorted(self._timeline_backfilled_tasks))
        self._timeline_backfilled_tasks.clear()
        for task_id in task_ids:
            self.materialize_human_views(task_id)

    def _backfill_task_timelines(self, connection: sqlite3.Connection) -> set[str]:
        """Append conservative task-scoped chronology for released V12 rows.

        The original V12 human-view expansion accidentally left initiative and
        initiative-closure events unscoped.  It also represented report chunk
        appends only in entity state.  This repair never rewrites either an
        existing timeline row or retained report content.  Every reconstructed
        row is appended with a bounded ``backfill`` marker, the canonical
        entity timestamp when it exists, and a deterministic source sequence
        hint.  SQLite's AUTOINCREMENT sequence remains the authoritative
        append order when historical interleaving cannot be proven.

        The metadata marker is written in the same ``BEGIN IMMEDIATE``
        transaction as every appended row and projection job.  A crash rolls
        all of it back; a later normal open retries from canonical metadata.
        """
        marker = connection.execute(
            "SELECT value FROM v12_metadata WHERE key=?", (_TIMELINE_BACKFILL_METADATA_KEY,),
        ).fetchone()
        if marker is not None:
            if str(marker[0]) != _TIMELINE_BACKFILL_VERSION:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return set()

        task_rows = connection.execute(
            "SELECT task_id,created_at,created_sequence FROM tasks WHERE project_hash=? ORDER BY task_id",
            (self.project_hash,),
        ).fetchall()
        known_tasks = {str(row["task_id"]) for row in task_rows}
        for task_id in known_tasks:
            # The first released V12 identifier format was an unsharded
            # ``task-<uuid4-hex>``.  The already-verified task row and this
            # project-scoped query bind it to this shard while a normal,
            # path-bearing open performs the transactional backfill.  New IDs
            # remain shard-bound; arbitrary old-looking values still fail
            # closed rather than being treated as a compatibility layout.
            if (
                task_shard_hash(task_id) != self.project_hash
                and _LEGACY_UNSHARDED_TASK_ID_RE.fullmatch(task_id) is None
            ):
                raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")

        def backfill_payload(source: str, source_sequence: int | None, values: Mapping[str, Any]) -> dict[str, Any]:
            payload = dict(values)
            marker_value: dict[str, Any] = {
                "schema": _TIMELINE_BACKFILL_VERSION,
                "derived": True,
                "source": source,
            }
            if source_sequence is not None:
                marker_value["source_sequence"] = source_sequence
            payload["backfill"] = marker_value
            return payload

        def direct_event(
            *, task_id: str, event_type: str, reference_column: str, reference_id: str,
            marker_field: str | None = None, marker_value: int | None = None,
        ) -> bool:
            rows = connection.execute(
                f"SELECT payload_json FROM timeline WHERE task_id=? AND event_type=? AND {reference_column}=? ORDER BY sequence",
                (task_id, event_type, reference_id),
            ).fetchall()
            if marker_field is None:
                return bool(rows)
            for row in rows:
                payload = _load_json(str(row["payload_json"]), label="timeline payload")
                if isinstance(payload, Mapping) and payload.get(marker_field) == marker_value:
                    return True
            return False

        candidates: list[dict[str, Any]] = []

        def append_candidate(
            *, task_id: str, event_type: str, entity_type: str, entity_id: str, payload: Mapping[str, Any],
            occurred_at: str, sequence_hint: int | None, delegation_id: str | None = None,
            report_id: str | None = None, initiative_id: str | None = None,
            assessment_id: str | None = None, closure_id: str | None = None,
            decision_id: str | None = None,
        ) -> None:
            candidates.append({
                "task_id": task_id,
                "event_type": event_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "payload": dict(payload),
                "occurred_at": occurred_at,
                "sequence_hint": sequence_hint,
                "delegation_id": delegation_id,
                "report_id": report_id,
                "initiative_id": initiative_id,
                "assessment_id": assessment_id,
                "closure_id": closure_id,
                "decision_id": decision_id,
            })

        for row in task_rows:
            task_id = str(row["task_id"])
            if not direct_event(task_id=task_id, event_type="task_created", reference_column="entity_id", reference_id=task_id):
                append_candidate(
                    task_id=task_id, event_type="task_created", entity_type="task", entity_id=task_id,
                    payload=backfill_payload("tasks", int(row["created_sequence"]), {"task_id": task_id}),
                    occurred_at=str(row["created_at"]), sequence_hint=int(row["created_sequence"]),
                )

        for row in connection.execute(
            "SELECT delegation_id,task_id,created_at,created_sequence FROM delegations WHERE project_hash=? ORDER BY task_id,created_sequence,delegation_id",
            (self.project_hash,),
        ).fetchall():
            task_id, delegation_id = str(row["task_id"]), str(row["delegation_id"])
            if task_id not in known_tasks:
                raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
            if not direct_event(task_id=task_id, event_type="delegation_created", reference_column="delegation_id", reference_id=delegation_id):
                append_candidate(
                    task_id=task_id, event_type="delegation_created", entity_type="delegation", entity_id=delegation_id,
                    payload=backfill_payload("delegations", int(row["created_sequence"]), {"delegation_id": delegation_id, "task_id": task_id}),
                    occurred_at=str(row["created_at"]), sequence_hint=int(row["created_sequence"]), delegation_id=delegation_id,
                )

        report_rows = connection.execute(
            "SELECT report_id,task_id,delegation_id,report_type,status,assembly_state,total_chunks,total_bytes,content_digest,created_at,created_sequence,finalized_at,finalized_sequence,aborted_at,aborted_sequence FROM reports WHERE project_hash=? ORDER BY task_id,created_sequence,report_id",
            (self.project_hash,),
        ).fetchall()
        for row in report_rows:
            task_id, report_id, delegation_id = str(row["task_id"]), str(row["report_id"]), str(row["delegation_id"])
            if task_id not in known_tasks:
                raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
            state = str(row["assembly_state"])
            created_sequence = int(row["created_sequence"])
            finalized_sequence = None if row["finalized_sequence"] is None else int(row["finalized_sequence"])
            aborted_sequence = None if row["aborted_sequence"] is None else int(row["aborted_sequence"])
            single = state == "finalized" and finalized_sequence == created_sequence

            def report_event(event_type: str, *, marker_field: str | None = None, marker_value: int | None = None) -> bool:
                return direct_event(
                    task_id=task_id, event_type=event_type, reference_column="report_id", reference_id=report_id,
                    marker_field=marker_field, marker_value=marker_value,
                )

            if single:
                if not report_event("report_submitted"):
                    append_candidate(
                        task_id=task_id, event_type="report_submitted", entity_type="report", entity_id=report_id,
                        payload=backfill_payload("reports", finalized_sequence, {
                            "report_id": report_id, "delegation_id": delegation_id, "report_type": str(row["report_type"]),
                            "status": row["status"], "total_chunks": int(row["total_chunks"]), "total_bytes": int(row["total_bytes"]),
                            "content_digest": str(row["content_digest"]),
                        }),
                        occurred_at=str(row["finalized_at"] or row["created_at"]), sequence_hint=finalized_sequence,
                        delegation_id=delegation_id, report_id=report_id,
                    )
                continue

            if not report_event("report_started"):
                append_candidate(
                    task_id=task_id, event_type="report_started", entity_type="report", entity_id=report_id,
                    payload=backfill_payload("reports", created_sequence, {
                        "report_id": report_id, "delegation_id": delegation_id, "report_type": str(row["report_type"]),
                    }),
                    occurred_at=str(row["created_at"]), sequence_hint=created_sequence,
                    delegation_id=delegation_id, report_id=report_id,
                )
            for chunk in connection.execute(
                "SELECT chunk_index,section,content_digest,content_bytes,created_at FROM report_chunks WHERE report_id=? ORDER BY chunk_index",
                (report_id,),
            ).fetchall():
                index = int(chunk["chunk_index"])
                if report_event("report_chunk_appended", marker_field="chunk_index", marker_value=index):
                    continue
                append_candidate(
                    task_id=task_id, event_type="report_chunk_appended", entity_type="report_chunk", entity_id=report_id,
                    payload=backfill_payload("report_chunks", None, {
                        "report_id": report_id, "delegation_id": delegation_id, "chunk_index": index,
                        "section": str(chunk["section"]), "content_digest": str(chunk["content_digest"]),
                        "content_bytes": int(chunk["content_bytes"]),
                    }),
                    occurred_at=str(chunk["created_at"]), sequence_hint=None,
                    delegation_id=delegation_id, report_id=report_id,
                )
            if state == "finalized" and not report_event("report_submitted"):
                append_candidate(
                    task_id=task_id, event_type="report_submitted", entity_type="report", entity_id=report_id,
                    payload=backfill_payload("reports", finalized_sequence, {
                        "report_id": report_id, "delegation_id": delegation_id, "report_type": str(row["report_type"]),
                        "status": row["status"], "total_chunks": int(row["total_chunks"]), "total_bytes": int(row["total_bytes"]),
                        "content_digest": str(row["content_digest"]),
                    }),
                    occurred_at=str(row["finalized_at"] or row["created_at"]), sequence_hint=finalized_sequence,
                    delegation_id=delegation_id, report_id=report_id,
                )
            elif state == "aborted" and not report_event("report_aborted"):
                append_candidate(
                    task_id=task_id, event_type="report_aborted", entity_type="report", entity_id=report_id,
                    payload=backfill_payload("reports", aborted_sequence, {
                        "report_id": report_id, "delegation_id": delegation_id, "total_chunks": int(row["total_chunks"]),
                        "total_bytes": int(row["total_bytes"]),
                    }),
                    occurred_at=str(row["aborted_at"] or row["created_at"]), sequence_hint=aborted_sequence,
                    delegation_id=delegation_id, report_id=report_id,
                )

        for row in connection.execute(
            "SELECT assessment_id,task_id,mode,created_at,created_sequence FROM governance_assessments WHERE project_hash=? ORDER BY task_id,created_sequence,assessment_id",
            (self.project_hash,),
        ).fetchall():
            task_id, assessment_id = str(row["task_id"]), str(row["assessment_id"])
            if task_id not in known_tasks:
                raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
            if not direct_event(task_id=task_id, event_type="governance_mode_set", reference_column="assessment_id", reference_id=assessment_id):
                append_candidate(
                    task_id=task_id, event_type="governance_mode_set", entity_type="governance_assessment", entity_id=assessment_id,
                    payload=backfill_payload("governance_assessments", int(row["created_sequence"]), {
                        "assessment_id": assessment_id, "task_id": task_id, "mode": str(row["mode"]),
                    }),
                    occurred_at=str(row["created_at"]), sequence_hint=int(row["created_sequence"]), assessment_id=assessment_id,
                )

        for row in connection.execute(
            "SELECT decision_id,task_id,subject_type,subject_id,subject_digest,decision_type,created_at,created_sequence FROM user_decisions WHERE project_hash=? ORDER BY task_id,created_sequence,decision_id",
            (self.project_hash,),
        ).fetchall():
            task_id, decision_id = str(row["task_id"]), str(row["decision_id"])
            if task_id not in known_tasks:
                raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
            if not direct_event(task_id=task_id, event_type="user_decision_recorded", reference_column="decision_id", reference_id=decision_id):
                append_candidate(
                    task_id=task_id, event_type="user_decision_recorded", entity_type="user_decision", entity_id=decision_id,
                    payload=backfill_payload("user_decisions", int(row["created_sequence"]), {
                        "decision_id": decision_id, "subject_type": str(row["subject_type"]), "subject_id": str(row["subject_id"]),
                        "subject_digest": row["subject_digest"], "decision_type": str(row["decision_type"]),
                    }),
                    occurred_at=str(row["created_at"]), sequence_hint=int(row["created_sequence"]), decision_id=decision_id,
                )

        def add_repair_warning(initiative_id: str) -> None:
            for link in connection.execute(
                "SELECT link_id,warnings_json FROM initiative_links WHERE initiative_id=? AND project_hash=? ORDER BY link_id",
                (initiative_id, self.project_hash),
            ).fetchall():
                warnings = _load_json(str(link["warnings_json"]), label="link warnings")
                if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
                    raise V12StoreError("idempotency key was already used for different arguments", code="idempotency_conflict")
                if _TIMELINE_REPAIR_CONFLICT_WARNING not in warnings:
                    connection.execute(
                        "UPDATE initiative_links SET warnings_json=? WHERE link_id=?",
                        (_canonical_json([*warnings, _TIMELINE_REPAIR_CONFLICT_WARNING], label="link warnings"), int(link["link_id"])),
                    )

        def initiative_report_task(initiative_id: str) -> tuple[str | None, bool, str | None]:
            """Resolve a conservative task scope from initiative links.

            A direct task link is already authoritative.  A report-only
            initiative is eligible for a *derived* task link only when every
            report link resolves to one and the same known task.  This helper
            deliberately does not mutate: callers must first validate every
            initiative revision so contradictory canonical evidence can never
            leave behind a guessed direct link.
            """
            links = connection.execute(
                "SELECT l.target_id,r.task_id FROM initiative_links l LEFT JOIN reports r ON r.report_id=l.target_id AND r.project_hash=l.project_hash WHERE l.initiative_id=? AND l.project_hash=? AND l.relationship='report' ORDER BY l.target_id",
                (initiative_id, self.project_hash),
            ).fetchall()
            direct = {
                str(row[0]) for row in connection.execute(
                    "SELECT target_id FROM initiative_links WHERE initiative_id=? AND project_hash=? AND relationship='task'",
                    (initiative_id, self.project_hash),
                ).fetchall()
            }
            if not links:
                if len(direct) == 1 and next(iter(direct)) in known_tasks:
                    return next(iter(direct)), False, None
                if direct:
                    add_repair_warning(initiative_id)
                    return None, False, "conflict"
                return None, False, None
            resolved = {str(row["task_id"]) for row in links if row["task_id"] is not None}
            if len(resolved) != 1 or any(row["task_id"] is None for row in links):
                add_repair_warning(initiative_id)
                return None, False, "ambiguous"
            candidate = next(iter(resolved))
            if candidate not in known_tasks or (direct and direct != {candidate}):
                add_repair_warning(initiative_id)
                return None, False, "conflict"
            return candidate, not direct, None

        initiative_scope: dict[str, str] = {}
        initiative_rows = connection.execute(
            "SELECT initiative_id FROM initiatives WHERE project_hash=? ORDER BY initiative_id", (self.project_hash,),
        ).fetchall()
        for item in initiative_rows:
            initiative_id = str(item["initiative_id"])
            task_id, needs_derived_link, conflict = initiative_report_task(initiative_id)
            if conflict is not None:
                continue
            revisions = connection.execute(
                "SELECT revision_number,occurred_at,sequence,payload_json FROM initiative_revisions WHERE initiative_id=? AND project_hash=? ORDER BY revision_number",
                (initiative_id, self.project_hash),
            ).fetchall()
            revision_tasks: set[str] = set()
            revision_payloads: list[tuple[sqlite3.Row, Mapping[str, Any], str | None]] = []
            for revision in revisions:
                payload = _load_json(str(revision["payload_json"]), label="initiative revision")
                if not isinstance(payload, Mapping):
                    raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
                revision_task = payload.get("task_id")
                if revision_task is not None and (not isinstance(revision_task, str) or revision_task not in known_tasks):
                    raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
                if isinstance(revision_task, str):
                    revision_tasks.add(revision_task)
                revision_payloads.append((revision, payload, revision_task if isinstance(revision_task, str) else None))

            # A uniquely report-resolved initiative may be linked to its task
            # only after every revision agrees.  Multiple revision anchors, a
            # revision anchor that contradicts report lineage, or an unknown
            # direct anchor is an explicit no-guess repair conflict.
            if len(revision_tasks) > 1 or (task_id is not None and revision_tasks and revision_tasks != {task_id}):
                add_repair_warning(initiative_id)
                continue
            if task_id is None and revision_tasks:
                task_id = next(iter(revision_tasks))
            if task_id is None:
                continue
            if needs_derived_link:
                connection.execute(
                    "INSERT INTO initiative_links(initiative_id,project_hash,relationship,target_id,is_resolved,warnings_json,created_at) VALUES (?, ?, 'task', ?, 1, '[]', ?)",
                    (initiative_id, self.project_hash, task_id, _now()),
                )
                append_candidate(
                    task_id=task_id, event_type="initiative_task_link_derived", entity_type="initiative_link", entity_id=initiative_id,
                    payload=backfill_payload("initiative_links", None, {"initiative_id": initiative_id, "task_id": task_id, "relationship": "task"}),
                    occurred_at=_now(), sequence_hint=None, initiative_id=initiative_id,
                )
            initiative_scope[initiative_id] = task_id
            for revision, _payload, revision_task in revision_payloads:
                if revision_task is None:
                    continue
                if revision_task != task_id:
                    # The full set was validated above.  Keep this guard near
                    # the write path to make a future query change fail closed.
                    add_repair_warning(initiative_id)
                    initiative_scope.pop(initiative_id, None)
                    break
                event_type = "initiative_created" if int(revision["revision_number"]) == 1 else "initiative_revised"
                if direct_event(task_id=revision_task, event_type=event_type, reference_column="initiative_id", reference_id=initiative_id, marker_field="revision_number", marker_value=int(revision["revision_number"])):
                    continue
                append_candidate(
                    task_id=revision_task, event_type=event_type, entity_type="initiative", entity_id=initiative_id,
                    payload=backfill_payload("initiative_revisions", int(revision["sequence"]), {
                        "initiative_id": initiative_id, "revision_number": int(revision["revision_number"]),
                    }),
                    occurred_at=str(revision["occurred_at"]), sequence_hint=int(revision["sequence"]), initiative_id=initiative_id,
                )

        for row in connection.execute(
            "SELECT closure_id,subject_type,subject_id,verdict,created_at,created_sequence,initiative_status FROM governance_closures WHERE project_hash=? ORDER BY created_sequence,closure_id",
            (self.project_hash,),
        ).fetchall():
            closure_id, subject_type, subject_id = str(row["closure_id"]), str(row["subject_type"]), str(row["subject_id"])
            if subject_type == "task":
                task_id = subject_id if subject_id in known_tasks else None
            elif subject_type == "initiative":
                task_id = initiative_scope.get(subject_id)
            else:
                raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
            if task_id is None:
                continue
            closure_event = direct_event(task_id=task_id, event_type="governance_closure_submitted", reference_column="closure_id", reference_id=closure_id)
            if not closure_event:
                append_candidate(
                    task_id=task_id, event_type="governance_closure_submitted", entity_type="governance_closure", entity_id=closure_id,
                    payload=backfill_payload("governance_closures", int(row["created_sequence"]), {
                        "closure_id": closure_id, "subject_type": subject_type, "subject_id": subject_id, "verdict": str(row["verdict"]),
                    }),
                    occurred_at=str(row["created_at"]), sequence_hint=int(row["created_sequence"]),
                    initiative_id=subject_id if subject_type == "initiative" else None, closure_id=closure_id,
                )
            if subject_type == "initiative" and row["initiative_status"] is not None and not direct_event(
                task_id=task_id, event_type="initiative_revised_by_closure", reference_column="closure_id", reference_id=closure_id,
            ):
                append_candidate(
                    task_id=task_id, event_type="initiative_revised_by_closure", entity_type="initiative", entity_id=subject_id,
                    payload=backfill_payload("governance_closures", int(row["created_sequence"]), {
                        "initiative_id": subject_id, "closure_id": closure_id, "status": str(row["initiative_status"]), "reason": "governance_closure",
                    }),
                    occurred_at=str(row["created_at"]), sequence_hint=int(row["created_sequence"]), initiative_id=subject_id,
                    closure_id=closure_id,
                )

        event_rank = {
            "task_created": 0,
            "delegation_created": 1,
            "report_started": 2,
            "report_chunk_appended": 3,
            "report_submitted": 4,
            "report_aborted": 4,
            "governance_mode_set": 5,
            "initiative_task_link_derived": 6,
            "initiative_created": 7,
            "initiative_revised": 8,
            "initiative_revised_by_closure": 9,
            "user_decision_recorded": 10,
            "governance_closure_submitted": 11,
        }
        changed: set[str] = set()
        for candidate in sorted(
            candidates,
            key=lambda item: (
                item["occurred_at"],
                event_rank.get(str(item["event_type"]), 99),
                item["task_id"],
                0 if item["sequence_hint"] is not None else 1,
                -1 if item["sequence_hint"] is None else int(item["sequence_hint"]),
                item["event_type"], item["entity_id"],
            ),
        ):
            self._timeline(
                connection,
                event_type=str(candidate["event_type"]), entity_type=str(candidate["entity_type"]), entity_id=str(candidate["entity_id"]),
                payload=candidate["payload"], task_id=str(candidate["task_id"]), occurred_at=str(candidate["occurred_at"]),
                delegation_id=candidate["delegation_id"], report_id=candidate["report_id"], initiative_id=candidate["initiative_id"],
                assessment_id=candidate["assessment_id"], closure_id=candidate["closure_id"], decision_id=candidate["decision_id"],
            )
            changed.add(str(candidate["task_id"]))

        for task_id in sorted(changed):
            latest = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM timeline WHERE task_id=?", (task_id,)).fetchone()[0]
            connection.execute(
                "INSERT INTO projection_jobs(project_hash,task_id,source_sequence,reason,status,created_at,updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?) ON CONFLICT(task_id,source_sequence,reason) DO UPDATE SET status='pending',last_error_code=NULL,updated_at=excluded.updated_at",
                (self.project_hash, task_id, int(latest), _TIMELINE_BACKFILL_REASON, _now(), _now()),
            )
        connection.execute(
            "INSERT INTO v12_metadata(key,value) VALUES (?, ?)",
            (_TIMELINE_BACKFILL_METADATA_KEY, _TIMELINE_BACKFILL_VERSION),
        )
        return changed

    def _verify_known_task(self, task_id: str) -> None:
        try:
            self._check_open_paths(database_required=True)
            with self._connection() as connection:
                # Existing shards may predate the one additive schema-v1
                # expansion.  Migration is explicit here, never hidden in
                # validation, and commits before the read-only integrity pass.
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self._migrate_schema_v1_expansion(connection)
                    self._migrate_explicit_profile_binding(connection)
                    self._migrate_durable_native_task_name(connection)
                    self._migrate_report_consumption_receipts(connection)
                    self._migrate_durable_governance_gate(connection)
                    self._migrate_ready_approval_handles(connection)
                    self._migrate_advisory_governance(connection)
                    self._migrate_canonical_report_semantics(connection)
                    self._validate_existing(connection)
                    self._timeline_backfilled_tasks = self._backfill_task_timelines(connection)
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
                connection.execute("COMMIT")
                found = _row(connection.execute("SELECT project_root FROM tasks WHERE task_id=? AND project_hash=?", (task_id, self.project_hash)).fetchone())
                if found is None or not isinstance(found.get("project_root"), str):
                    raise V12StoreError("task was not found", code="task_not_found")
                try:
                    root = Path(str(found["project_root"])).resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise V12StoreError("task was not found", code="task_not_found") from exc
                stored = str(found["project_root"])
                if not root.is_dir() or str(root) != stored or hashlib.sha256(stored.encode("utf-8")).hexdigest() != self.project_hash:
                    raise V12StoreError("reference belongs to another project", code="cross_project_reference")
                self.project_root = root
                self._materialize_timeline_backfills()
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc

    def _verify_known_record(self, record_id: str, *, label: str) -> None:
        """Resolve a sharded delegation/report ID to its owning task and root."""
        table_by_label = {
            "delegation_id": ("delegations", "delegation_id", "delegation"),
            "report_id": ("reports", "report_id", "report"),
        }
        selected = table_by_label.get(label)
        if selected is None:
            raise V12StoreError(f"{label} is invalid", code="invalid_identifier", details={"field": label})
        table, column, entity = selected
        try:
            self._check_open_paths(database_required=True)
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self._migrate_schema_v1_expansion(connection)
                    self._migrate_explicit_profile_binding(connection)
                    self._migrate_durable_native_task_name(connection)
                    self._migrate_report_consumption_receipts(connection)
                    self._migrate_durable_governance_gate(connection)
                    self._migrate_ready_approval_handles(connection)
                    self._migrate_advisory_governance(connection)
                    self._migrate_canonical_report_semantics(connection)
                    self._validate_existing(connection)
                    self._timeline_backfilled_tasks = self._backfill_task_timelines(connection)
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
                connection.execute("COMMIT")
                found = _row(connection.execute(
                    f"SELECT tasks.project_root FROM {table} JOIN tasks ON tasks.task_id={table}.task_id WHERE {table}.{column}=? AND {table}.project_hash=?",
                    (record_id, self.project_hash),
                ).fetchone())
                if found is None or not isinstance(found.get("project_root"), str):
                    raise V12StoreError(f"{entity} was not found", code=f"{entity}_not_found")
                try:
                    root = Path(str(found["project_root"])).resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise V12StoreError(f"{entity} was not found", code=f"{entity}_not_found") from exc
                stored = str(found["project_root"])
                if not root.is_dir() or str(root) != stored or hashlib.sha256(stored.encode("utf-8")).hexdigest() != self.project_hash:
                    raise V12StoreError("reference belongs to another project", code="cross_project_reference")
                self.project_root = root
                self._materialize_timeline_backfills()
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        statements = """
        CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL);
        CREATE TABLE v12_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE timeline(sequence INTEGER PRIMARY KEY AUTOINCREMENT,occurred_at TEXT NOT NULL,event_type TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,task_id TEXT,delegation_id TEXT,report_id TEXT,initiative_id TEXT,assessment_id TEXT,closure_id TEXT,decision_id TEXT,payload_json TEXT NOT NULL);
        CREATE TABLE tasks(task_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,project_root TEXT NOT NULL,objective TEXT NOT NULL,user_request_original TEXT NOT NULL,user_language TEXT NOT NULL,task_contract_version TEXT NOT NULL,requirements_json TEXT NOT NULL,constraints_json TEXT NOT NULL,acceptance_criteria_json TEXT NOT NULL,verification_plan_json TEXT NOT NULL,context_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,updated_sequence INTEGER NOT NULL);
        CREATE TABLE delegations(delegation_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),parent_delegation_id TEXT REFERENCES delegations(delegation_id),native_task_name TEXT NOT NULL,objective TEXT NOT NULL,role TEXT NOT NULL,profile_name TEXT NOT NULL,scope TEXT NOT NULL,instructions TEXT NOT NULL,input_report_ids_json TEXT NOT NULL,input_decision_ids_json TEXT NOT NULL,model TEXT NOT NULL,reasoning_effort TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE reports(report_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),delegation_id TEXT NOT NULL REFERENCES delegations(delegation_id),report_type TEXT NOT NULL,status TEXT,semantic_status TEXT,assembly_state TEXT NOT NULL,next_chunk_index INTEGER NOT NULL,total_chunks INTEGER NOT NULL,total_bytes INTEGER NOT NULL,content_digest TEXT NOT NULL,supersedes_report_id TEXT REFERENCES reports(report_id),review_policy TEXT,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,finalized_at TEXT,finalized_sequence INTEGER,aborted_at TEXT,aborted_sequence INTEGER,abort_reason_en TEXT);
        CREATE TABLE report_chunks(report_id TEXT NOT NULL REFERENCES reports(report_id),chunk_index INTEGER NOT NULL,section TEXT NOT NULL,content_json TEXT NOT NULL,content_digest TEXT NOT NULL,content_bytes INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(report_id,chunk_index));
        CREATE TABLE report_consumption_receipts(receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),consumer_delegation_id TEXT REFERENCES delegations(delegation_id),reader_kind TEXT NOT NULL,report_id TEXT NOT NULL REFERENCES reports(report_id),observed_content_digest TEXT NOT NULL,sections_json TEXT NOT NULL,input_cursor TEXT,output_cursor TEXT,chunk_indexes_json TEXT NOT NULL,returned_content_bytes INTEGER NOT NULL,has_more INTEGER NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE report_usage(task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),total_retained_bytes INTEGER NOT NULL,assembling_bytes INTEGER NOT NULL,assembling_reports INTEGER NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE governance_assessments(assessment_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),initiative_id TEXT,mode TEXT NOT NULL,source TEXT NOT NULL,rationale TEXT,risk_factors_json TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE initiatives(initiative_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,goal TEXT NOT NULL,risk TEXT,status TEXT NOT NULL,notes_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,latest_revision INTEGER NOT NULL,created_sequence INTEGER NOT NULL,updated_sequence INTEGER NOT NULL);
        CREATE TABLE initiative_revisions(revision_id INTEGER PRIMARY KEY AUTOINCREMENT,initiative_id TEXT NOT NULL REFERENCES initiatives(initiative_id),revision_number INTEGER NOT NULL,project_hash TEXT NOT NULL,occurred_at TEXT NOT NULL,sequence INTEGER NOT NULL,payload_json TEXT NOT NULL,UNIQUE(initiative_id,revision_number));
        CREATE TABLE initiative_links(link_id INTEGER PRIMARY KEY AUTOINCREMENT,initiative_id TEXT NOT NULL REFERENCES initiatives(initiative_id),project_hash TEXT NOT NULL,relationship TEXT NOT NULL,target_id TEXT NOT NULL,is_resolved INTEGER NOT NULL,warnings_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(initiative_id,relationship,target_id));
        CREATE TABLE governance_closures(closure_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,verdict TEXT NOT NULL,evidence_json TEXT NOT NULL,unresolved_risks_json TEXT NOT NULL,follow_ups_json TEXT NOT NULL,initiative_status TEXT,completion_notes_json TEXT,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE user_decisions(decision_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,subject_digest TEXT,decision_type TEXT NOT NULL,prompt_en TEXT NOT NULL,response_original TEXT NOT NULL,response_en TEXT NOT NULL,user_language TEXT NOT NULL,attribution TEXT NOT NULL,supersedes_decision_id TEXT REFERENCES user_decisions(decision_id),created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE approval_handles(approval_handle TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),report_id TEXT NOT NULL REFERENCES reports(report_id),report_content_digest TEXT NOT NULL,view_relative_path TEXT NOT NULL,view_content_digest TEXT NOT NULL,view_source_sequence INTEGER NOT NULL,request_digest TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,consumed_decision_id TEXT REFERENCES user_decisions(decision_id),UNIQUE(task_id,report_id,report_content_digest,view_content_digest,view_source_sequence));
        CREATE TABLE projection_jobs(job_id INTEGER PRIMARY KEY AUTOINCREMENT,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),source_sequence INTEGER NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL,lease_token TEXT,lease_expires_at TEXT,last_error_code TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(task_id,source_sequence,reason));
        CREATE TABLE projection_files(task_id TEXT NOT NULL REFERENCES tasks(task_id),relative_path TEXT NOT NULL,source_sequence INTEGER NOT NULL,renderer_version TEXT NOT NULL,content_digest TEXT NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(task_id,relative_path));
        CREATE TABLE idempotency(operation TEXT NOT NULL,idempotency_key TEXT NOT NULL,payload_digest TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(operation,idempotency_key));
        CREATE INDEX timeline_task_sequence ON timeline(task_id,sequence);
        CREATE INDEX timeline_delegation_sequence ON timeline(delegation_id,sequence);
        CREATE INDEX timeline_initiative_sequence ON timeline(initiative_id,sequence);
        CREATE INDEX reports_task_created ON reports(task_id,created_sequence);
        CREATE INDEX reports_delegation_created ON reports(delegation_id,created_sequence);
        CREATE INDEX report_chunks_report_order ON report_chunks(report_id,chunk_index);
        CREATE INDEX consumption_task_sequence ON report_consumption_receipts(task_id,created_sequence);
        CREATE INDEX consumption_delegation_report ON report_consumption_receipts(consumer_delegation_id,report_id,created_sequence);
        CREATE INDEX assessments_task_created ON governance_assessments(task_id,created_sequence);
        CREATE INDEX initiative_links_source ON initiative_links(initiative_id,relationship);
        CREATE INDEX decisions_task_created ON user_decisions(task_id,created_sequence);
        CREATE INDEX approval_handles_task_report ON approval_handles(task_id,report_id,created_sequence);
        CREATE INDEX timeline_decision_sequence ON timeline(decision_id,sequence);
        CREATE INDEX projection_jobs_pending ON projection_jobs(status,lease_expires_at,job_id);
        """
        for statement in statements.split(";"):
            if statement.strip():
                connection.execute(statement)
        for statement in (
            "CREATE TRIGGER reports_terminal_no_update BEFORE UPDATE ON reports WHEN OLD.assembly_state IN ('finalized','aborted') BEGIN SELECT RAISE(ABORT,'terminal reports are immutable'); END",
            "CREATE TRIGGER reports_no_delete BEFORE DELETE ON reports BEGIN SELECT RAISE(ABORT,'reports are immutable'); END",
            "CREATE TRIGGER report_chunks_no_update BEFORE UPDATE ON report_chunks BEGIN SELECT RAISE(ABORT,'report chunks are immutable'); END",
            "CREATE TRIGGER report_chunks_no_delete BEFORE DELETE ON report_chunks BEGIN SELECT RAISE(ABORT,'report chunks are immutable'); END",
            "CREATE TRIGGER decisions_no_update BEFORE UPDATE ON user_decisions BEGIN SELECT RAISE(ABORT,'decisions are append-only'); END",
            "CREATE TRIGGER decisions_no_delete BEFORE DELETE ON user_decisions BEGIN SELECT RAISE(ABORT,'decisions are append-only'); END",
        ):
            connection.execute(statement)

    @contextmanager
    def _connection(self, *, database_required: bool = True):
        connection: sqlite3.Connection | None = None
        try:
            if not database_required:
                self._precreate_database()
            self._check_open_paths(database_required=database_required)
            connection = sqlite3.connect(self.database_path, timeout=15, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 15000")
            # SQLite's journal-mode transition can return SQLITE_BUSY before
            # busy_timeout is honored when two processes perform their very
            # first open together.  Retry only that transient setup race
            # within the same bounded connection window; all later migration
            # and mutation work remains in explicit transactions.
            deadline = time.monotonic() + 15
            while True:
                try:
                    connection.execute("PRAGMA journal_mode = WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.025)
            connection.execute("PRAGMA synchronous = FULL")
            self._protect_files()
            yield connection
        finally:
            if connection is not None:
                connection.close()
                self._materialize_sidecars()

    def _read(self, call: Callable[[sqlite3.Connection], T]) -> T:
        try:
            with self._connection() as connection:
                # A deferred read transaction pins one consistent SQLite
                # snapshot across the several compact entity/timeline queries
                # in a public inspection response.
                connection.execute("BEGIN")
                try:
                    result = call(connection)
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
                connection.execute("COMMIT")
                return result
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc

    def _write(self, call: Callable[[sqlite3.Connection], T]) -> T:
        try:
            with self._guard, self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    result = call(connection)
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
                connection.execute("COMMIT")
                self._protect_files()
                return result
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc

    def _mutation(self, operation: str, payload: Mapping[str, Any], key: Any, call: Callable[[sqlite3.Connection], dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        normalized = _strict_json(dict(payload), label="mutation payload")
        digest = hashlib.sha256(_canonical_json(normalized, label="mutation payload").encode("utf-8")).hexdigest()
        client_key = None if key is None else _required_text(key, label="idempotency_key", maximum=IDEMPOTENCY_KEY_MAX_LENGTH)
        retry_handle = client_key or f"retry-{uuid.uuid4().hex}"
        idempotency = hashlib.sha256(_canonical_json({"operation": operation, "retry_handle": retry_handle}, label="idempotency operation key").encode("utf-8")).hexdigest()
        def transact(connection: sqlite3.Connection) -> tuple[dict[str, Any], bool]:
            previous = connection.execute("SELECT payload_digest,result_json FROM idempotency WHERE operation=? AND idempotency_key=?", (operation, idempotency)).fetchone()
            if previous is not None:
                if str(previous["payload_digest"]) != digest:
                    raise V12StoreError("idempotency key was already used for different arguments", code="idempotency_conflict")
                value = _load_json(str(previous["result_json"]), label="idempotency result")
                if not isinstance(value, dict):
                    raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
                return value, True
            value = _strict_json(call(connection), label="mutation result")
            if not isinstance(value, dict):
                raise V12StoreError("V12 storage is unavailable", code="storage_unavailable")
            projection_task = payload.get("task_id")
            if not isinstance(projection_task, str):
                candidate = value.get("task")
                projection_task = candidate.get("task_id") if isinstance(candidate, Mapping) else None
            if isinstance(projection_task, str):
                sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM timeline WHERE task_id=?", (projection_task,)).fetchone()[0]
                connection.execute("INSERT INTO projection_jobs(project_hash,task_id,source_sequence,reason,status,created_at,updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?) ON CONFLICT(task_id,source_sequence,reason) DO UPDATE SET status='pending',last_error_code=NULL,updated_at=excluded.updated_at", (self.project_hash, projection_task, int(sequence), operation, _now(), _now()))
            value = dict(value) | {"retry_handle": retry_handle}
            if client_key is not None:
                value["idempotency_key"] = client_key
            connection.execute("INSERT INTO idempotency(operation,idempotency_key,payload_digest,result_json,created_at) VALUES (?, ?, ?, ?, ?)", (operation, idempotency, digest, _canonical_json(value, label="mutation result"), _now()))
            return value, False
        result, replayed = self._write(transact)
        # Derived Markdown is never part of the transaction's success.  A
        # bounded best-effort pass happens only after canonical commit; later
        # reads retry it opportunistically.
        task_value = normalized.get("task_id")
        if not isinstance(task_value, str) and isinstance(result.get("task"), Mapping):
            task_value = result["task"].get("task_id")
        if isinstance(task_value, str):
            self.materialize_human_views(task_value)
        return result, replayed

    def materialize_human_views(self, task_id: str) -> None:
        """Best-effort post-commit materialization; intentionally non-failing."""
        try:
            from cortex_runtime.v12_projections import materialize_task
            token = uuid.uuid4().hex
            expires = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
            def claim(connection: sqlite3.Connection) -> int | None:
                now = _now()
                row = connection.execute("SELECT job_id FROM projection_jobs WHERE task_id=? AND (status='pending' OR (status='leased' AND lease_expires_at<?)) ORDER BY source_sequence,job_id LIMIT 1", (task_id, now)).fetchone()
                if row is None:
                    return None
                job_id = int(row[0])
                connection.execute("UPDATE projection_jobs SET status='leased',lease_token=?,lease_expires_at=?,attempt_count=attempt_count+1,updated_at=? WHERE job_id=?", (token, expires, now, job_id))
                return job_id
            job_id = self._write(claim)
            if job_id is None:
                return
            outcome = materialize_task(self, task_id)
            status = "completed" if outcome.get("status") == "ready" else "conflict" if outcome.get("status") == "conflict" else "failed"
            def mark(connection: sqlite3.Connection) -> None:
                if status == "completed":
                    connection.execute("UPDATE projection_jobs SET status='completed',lease_token=NULL,lease_expires_at=NULL,last_error_code=NULL,updated_at=? WHERE job_id=? AND lease_token=?", (_now(), job_id, token))
                else:
                    connection.execute("UPDATE projection_jobs SET status=?,lease_token=NULL,lease_expires_at=NULL,updated_at=?,last_error_code=? WHERE job_id=? AND lease_token=?", (status, _now(), "projection_unavailable", job_id, token))
            self._write(mark)
        except Exception:
            return

    def human_view(self, task_id: str, relative_path: str) -> dict[str, Any]:
        """Repair a bounded time, then expose only a freshly verified path.

        A read receipt can make a previously ready projection stale without
        adding a projection job.  After the normal queued-job attempt, perform
        one direct best-effort render for this known task before reporting that
        a view is stale.  Conflicts and I/O failures remain honest non-ready
        states and never alter canonical mutation success.
        """
        self.materialize_human_views(task_id)
        try:
            from cortex_runtime.v12_projections import human_view, materialize_task
            view = human_view(self, task_id, relative_path)
            if view.get("status") == "ready":
                return view
            materialize_task(self, task_id)
            return human_view(self, task_id, relative_path)
        except Exception:
            return {"status": "unavailable", "path": None}

    def ready_approval_handle(
        self,
        *,
        task_id: Any,
        report_id: Any,
        report_content_digest: Any,
        view_relative_path: str,
        view_content_digest: Any,
        view_source_sequence: Any,
    ) -> str:
        """Mint or recover one opaque relation for a verified ready plan view.

        The relation deliberately does not add a timeline event: doing so would
        make the very view it proves stale.  It is therefore not a user-turn
        receipt; it proves only that this exact ready snapshot existed before a
        later decision mutation cross-checks the handle.
        """
        anchor = self._task_identifier(task_id)
        report = self._record_identifier(report_id, label="report_id")
        report_digest = _digest(report_content_digest, label="report_content_digest", required=True)
        view_digest = _digest(view_content_digest, label="approval_view_content_digest", required=True)
        if not isinstance(view_source_sequence, int) or isinstance(view_source_sequence, bool) or view_source_sequence < 0:
            raise V12StoreError("approval view source sequence is invalid", code="invalid_argument", details={"field": "approval_view_source_sequence"})
        expected_relative = f"plans/revisions/{report}.md"
        if view_relative_path != expected_relative:
            raise V12StoreError("approval view is invalid", code="approval_view_mismatch")

        def write(connection: sqlite3.Connection) -> str:
            task = self._task(connection, anchor)
            item = self._report(connection, report, task_id=anchor)
            if item["report_type"] != "plan" or item["assembly_state"] != "finalized" or item["status"] != "completed" or item.get("semantic_status") != "semantic_valid" or item["content_digest"] != report_digest:
                raise V12StoreError("approval view plan is invalid", code="approval_view_mismatch")
            latest = int(connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM timeline WHERE task_id=?", (anchor,)).fetchone()[0])
            row = connection.execute("SELECT source_sequence,content_digest,status FROM projection_files WHERE task_id=? AND relative_path=?", (anchor, expected_relative)).fetchone()
            if row is None or str(row[2]) != "ready" or int(row[0]) != view_source_sequence or str(row[1]) != view_digest or latest != view_source_sequence:
                raise V12StoreError("approval view is not ready", code="approval_view_not_ready")
            request_digest = _sha256_prefixed(task["user_request_original"], label="user request original")
            existing = connection.execute("SELECT approval_handle FROM approval_handles WHERE task_id=? AND report_id=? AND report_content_digest=? AND view_content_digest=? AND view_source_sequence=?", (anchor, report, report_digest, view_digest, view_source_sequence)).fetchone()
            if existing is not None:
                return str(existing[0])
            handle = f"approval-{self.project_hash}-{uuid.uuid4().hex}"
            connection.execute("INSERT INTO approval_handles(approval_handle,project_hash,task_id,report_id,report_content_digest,view_relative_path,view_content_digest,view_source_sequence,request_digest,created_at,created_sequence,consumed_decision_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)", (handle, self.project_hash, anchor, report, report_digest, expected_relative, view_digest, view_source_sequence, request_digest, _now(), view_source_sequence))
            return handle
        return self._write(write)

    @staticmethod
    def _timeline(connection: sqlite3.Connection, *, event_type: str, entity_type: str, entity_id: str, payload: Mapping[str, Any], task_id: str, occurred_at: str | None = None, delegation_id: str | None = None, report_id: str | None = None, initiative_id: str | None = None, assessment_id: str | None = None, closure_id: str | None = None, decision_id: str | None = None) -> int:
        """Append one immutable, task-scoped event in the caller transaction.

        ``timeline.sequence`` is an SQLite AUTOINCREMENT key.  Combined with
        the caller's ``BEGIN IMMEDIATE`` write transaction it serializes WAL
        writers without a separate sequence allocator, and it never permits a
        mutation to commit without its chronology entry.
        """
        if not isinstance(task_id, str) or not task_id:
            raise V12StoreError("task-scoped timeline event is required", code="ledger_corrupt")
        timestamp = _now() if occurred_at is None else _required_text(occurred_at, label="timeline occurred_at", maximum=128)
        cursor = connection.execute("INSERT INTO timeline(occurred_at,event_type,entity_type,entity_id,task_id,delegation_id,report_id,initiative_id,assessment_id,closure_id,decision_id,payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (timestamp, event_type, entity_type, entity_id, task_id, delegation_id, report_id, initiative_id, assessment_id, closure_id, decision_id, _canonical_json(dict(payload), label="timeline payload")))
        return int(cursor.lastrowid)

    def _task(self, connection: sqlite3.Connection, task_id: Any) -> dict[str, Any]:
        identifier = self._task_identifier(task_id)
        found = _row(connection.execute("SELECT * FROM tasks WHERE task_id=? AND project_hash=?", (identifier, self.project_hash)).fetchone())
        if found is None:
            raise V12StoreError("task was not found", code="task_not_found")
        if found.get("project_root") != str(self.project_root):
            raise V12StoreError("reference belongs to another project", code="cross_project_reference")
        for key in ("requirements", "constraints", "acceptance_criteria", "verification_plan"):
            found[key] = _load_json(str(found.pop(f"{key}_json")), label=f"task {key}")
        found["context"] = _load_json(str(found.pop("context_json")), label="task context")
        compact_ref = task_ref(identifier)
        if compact_ref is None:
            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
        found["task_ref"] = compact_ref
        closure = self._task_closure(connection, identifier)
        found["closure_state"] = "task_closed" if closure is not None else "open"
        found["task_closure_ref"] = None if closure is None else self._closure_ref(str(closure["closure_id"]))
        found["task_closure_verdict"] = None if closure is None else str(closure["verdict"])
        found["task_closure_sequence"] = None if closure is None else int(closure["created_sequence"])
        return found

    @staticmethod
    def _closure_ref(closure_id: str) -> str:
        """Render an evidence-only compact closure reference, never a call input."""
        if not isinstance(closure_id, str) or not re.fullmatch(r"closure-[0-9a-f]{32}", closure_id):
            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
        return f"c_{closure_id[-12:]}"

    def _task_closure(self, connection: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
        return _row(connection.execute(
            "SELECT * FROM governance_closures WHERE project_hash=? AND subject_type='task' AND subject_id=? "
            "ORDER BY created_sequence DESC,closure_id DESC LIMIT 1",
            (self.project_hash, task_id),
        ).fetchone())

    def _delegation(self, connection: sqlite3.Connection, delegation_id: Any, *, task_id: str | None = None) -> dict[str, Any]:
        identifier = self._record_identifier(delegation_id, label="delegation_id")
        found = _row(connection.execute("SELECT * FROM delegations WHERE delegation_id=? AND project_hash=?", (identifier, self.project_hash)).fetchone())
        if found is None:
            raise V12StoreError("delegation was not found", code="delegation_not_found")
        if task_id is not None and found["task_id"] != task_id:
            raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
        from cortex_runtime.delegation import is_profile_native_task_name, legacy_native_task_name
        native_name = found.get("native_task_name")
        if (
            native_name != legacy_native_task_name(identifier)
            and not is_profile_native_task_name(native_name, found.get("profile_name"))
        ):
            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
        found["input_report_ids"] = _load_json(str(found.pop("input_report_ids_json")), label="delegation inputs")
        found["input_decision_ids"] = _load_json(str(found.pop("input_decision_ids_json")), label="delegation decision inputs")
        return found

    def _report(self, connection: sqlite3.Connection, report_id: Any, *, task_id: str | None = None) -> dict[str, Any]:
        identifier = self._record_identifier(report_id, label="report_id")
        found = _row(connection.execute("SELECT * FROM reports WHERE report_id=? AND project_hash=?", (identifier, self.project_hash)).fetchone())
        if found is None:
            raise V12StoreError("report was not found", code="report_not_found")
        if task_id is not None and found["task_id"] != task_id:
            raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
        # See insert_header: a legacy NOT NULL status column needs a durable
        # placeholder while a chunked report is assembling.  Do not let that
        # storage-only placeholder change the public assembling-report state.
        if found.get("content_json") == "null" and found.get("assembly_state") == "assembling" and found.get("status") == "partial":
            found["status"] = None
        return found

    def _report_chunks(self, connection: sqlite3.Connection, report_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in connection.execute("SELECT * FROM report_chunks WHERE report_id=? ORDER BY chunk_index", (report_id,)).fetchall():
            item = _row(row)
            assert item is not None
            content = _load_json(str(item.pop("content_json")), label="report chunk")
            normalized, _rendered, size, digest = _canonical_json_bytes(content, label="report chunk")
            if size != int(item["content_bytes"]) or digest != str(item["content_digest"]):
                raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
            item["content"] = normalized
            result.append(item)
        return result

    def _report_digest(self, connection: sqlite3.Connection, report_id: str) -> str:
        chunks = self._report_chunks(connection, report_id)
        return _sha256_prefixed(_report_manifest(chunks), label="report manifest")

    def _initiative(self, connection: sqlite3.Connection, initiative_id: Any) -> dict[str, Any]:
        identifier = self._record_identifier(initiative_id, label="initiative_id")
        found = _row(connection.execute("SELECT * FROM initiatives WHERE initiative_id=? AND project_hash=?", (identifier, self.project_hash)).fetchone())
        if found is None:
            raise V12StoreError("initiative was not found", code="initiative_not_found")
        found["notes"] = _load_json(str(found.pop("notes_json")), label="initiative notes")
        return found

    def _decision(self, connection: sqlite3.Connection, decision_id: Any, *, task_id: str | None = None) -> dict[str, Any]:
        identifier = self._record_identifier(decision_id, label="decision_id")
        found = _row(connection.execute("SELECT * FROM user_decisions WHERE decision_id=? AND project_hash=?", (identifier, self.project_hash)).fetchone())
        if found is None:
            raise V12StoreError("decision was not found", code="decision_not_found")
        if task_id is not None and found["task_id"] != task_id:
            raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
        return found

    def _assessment(self, connection: sqlite3.Connection, assessment_id: str) -> dict[str, Any]:
        found = _row(connection.execute("SELECT * FROM governance_assessments WHERE assessment_id=? AND project_hash=?", (assessment_id, self.project_hash)).fetchone())
        if found is None:
            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
        found["risk_factors"] = _load_json(str(found.pop("risk_factors_json")), label="assessment risks")
        return found

    def _closure(self, connection: sqlite3.Connection, closure_id: str) -> dict[str, Any]:
        found = _row(connection.execute("SELECT * FROM governance_closures WHERE closure_id=? AND project_hash=?", (closure_id, self.project_hash)).fetchone())
        if found is None:
            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
        found["evidence"] = _load_json(str(found.pop("evidence_json")), label="closure evidence")
        found["unresolved_risks"] = _load_json(str(found.pop("unresolved_risks_json")), label="closure risks")
        found["follow_ups"] = _load_json(str(found.pop("follow_ups_json")), label="closure follow ups")
        notes = found.pop("completion_notes_json")
        found["completion_notes"] = None if notes is None else _load_json(str(notes), label="closure notes")
        found["closure_ref"] = self._closure_ref(str(found["closure_id"]))
        found["closure_state"] = "task_closed" if found["subject_type"] == "task" else "initiative_closed"
        return found

    @staticmethod
    def _compact_report(report: Mapping[str, Any]) -> dict[str, Any]:
        keys = ("report_id", "project_hash", "task_id", "delegation_id", "report_type", "status", "semantic_status", "assembly_state", "next_chunk_index", "total_chunks", "total_bytes", "content_digest", "supersedes_report_id", "review_policy", "created_at", "created_sequence", "finalized_at", "finalized_sequence", "aborted_at", "aborted_sequence")
        compact = {key: report.get(key) for key in keys}
        compact["storage_status"] = "storage_valid"
        return compact

    @staticmethod
    def _compact_delegation(delegation: Mapping[str, Any]) -> dict[str, Any]:
        return {key: delegation[key] for key in ("delegation_id", "project_hash", "task_id", "parent_delegation_id", "native_task_name", "objective", "role", "profile_name", "scope", "model", "reasoning_effort", "created_at", "created_sequence")}

    @staticmethod
    def _next_native_task_name(connection: sqlite3.Connection, *, task_id: str, profile_name: str) -> str:
        """Allocate the first unused profile-derived native name in one task.

        The surrounding write transaction serializes same-profile siblings.
        Opaque legacy names remain reserved for their live workers, but do
        not consume one of the readable profile-name slots.
        """
        from cortex_runtime.delegation import native_task_name

        existing = {
            str(row[0])
            for row in connection.execute(
                "SELECT native_task_name FROM delegations WHERE task_id=?",
                (task_id,),
            ).fetchall()
        }
        instance = 1
        while True:
            candidate = native_task_name(profile_name, instance)
            if candidate not in existing:
                return candidate
            instance += 1

    @staticmethod
    def _compact_decision(decision: Mapping[str, Any]) -> dict[str, Any]:
        value = {key: decision.get(key) for key in ("decision_id", "task_id", "subject_type", "subject_id", "subject_digest", "decision_type", "user_language", "attribution", "supersedes_decision_id", "created_at", "created_sequence")}
        value["response_en_excerpt"] = str(decision.get("response_en") or "")[:512]
        return value

    def create_task(self, *, objective: Any, user_request_original: Any, user_language: Any, requirements: Any, constraints: Any, acceptance_criteria: Any, verification_plan: Any, context: Any = None, task_id: Any = None, idempotency_key: Any = None, task_contract_version: Any = TASK_CONTRACT_VERSION) -> tuple[dict[str, Any], bool]:
        english_objective = _opaque_text(objective, label="objective")
        payload = {
            "objective": english_objective,
            "user_request_original": _opaque_text(user_request_original, label="user_request_original"),
            "user_language": _task_language(user_language),
            "task_contract_version": _required_text(task_contract_version, label="task_contract_version", maximum=64),
            "requirements": _contract_text_list(requirements, label="requirements"),
            "constraints": _contract_text_list(constraints, label="constraints"),
            "acceptance_criteria": _contract_text_list(acceptance_criteria, label="acceptance_criteria"),
            "verification_plan": _contract_text_list(verification_plan, label="verification_plan"),
            "context": _strict_json(context, label="context"),
            "task_id": None if task_id is None else self._task_identifier(task_id),
        }
        if payload["task_contract_version"] != TASK_CONTRACT_VERSION:
            raise V12StoreError("task_contract_version is invalid", code="invalid_argument", details={"field": "task_contract_version"})
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            identifier = str(payload["task_id"] or new_task_id(self.project_hash))
            if connection.execute("SELECT 1 FROM tasks WHERE task_id=?", (identifier,)).fetchone() is not None:
                raise V12StoreError("task_id already exists", code="task_exists")
            sequence = self._timeline(connection, event_type="task_created", entity_type="task", entity_id=identifier, payload={"task_id": identifier}, task_id=identifier)
            timestamp = _now()
            connection.execute(
                "INSERT INTO tasks(task_id,project_hash,project_root,objective,user_request_original,user_language,task_contract_version,requirements_json,constraints_json,acceptance_criteria_json,verification_plan_json,context_json,created_at,updated_at,created_sequence,updated_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (identifier, self.project_hash, str(self.project_root), payload["objective"], payload["user_request_original"], payload["user_language"], payload["task_contract_version"], _canonical_json(payload["requirements"], label="requirements"), _canonical_json(payload["constraints"], label="constraints"), _canonical_json(payload["acceptance_criteria"], label="acceptance_criteria"), _canonical_json(payload["verification_plan"], label="verification_plan"), _canonical_json(payload["context"], label="context"), timestamp, timestamp, sequence, sequence),
            )
            return {"task": self._task(connection, identifier)}
        return self._mutation("create_task", payload, idempotency_key, write)

    def create_delegation(self, *, task_id: Any, objective: Any, role: Any, profile_name: Any, scope: Any, instructions: Any, delegation_id: Any = None, parent_delegation_id: Any = None, input_report_ids: Any = None, input_decision_ids: Any = None, model: Any = None, reasoning_effort: Any = None, idempotency_key: Any = None) -> tuple[dict[str, Any], bool]:
        try:
            selection = validate_model_selection(model, reasoning_effort)
        except ValueError as exc:
            raise V12StoreError("model selection is invalid", code="invalid_model_selection") from exc
        payload = {"task_id": self._task_identifier(task_id), "objective": _opaque_text(objective, label="objective"), "role": _opaque_text(role, label="role", maximum=ROLE_MAX_LENGTH), "profile_name": _profile_name(profile_name), "scope": _opaque_text(scope, label="scope"), "instructions": _instructions_text(instructions), "delegation_id": None if delegation_id is None else self._record_identifier(delegation_id, label="delegation_id"), "parent_delegation_id": None if parent_delegation_id is None else self._record_identifier(parent_delegation_id, label="parent_delegation_id"), "input_report_ids": _identifier_list(input_report_ids, label="input_report_ids", maximum=MAX_REPORT_IDS, deduplicate=True), "input_decision_ids": _identifier_list(input_decision_ids, label="input_decision_ids", maximum=MAX_DECISION_IDS, deduplicate=True), "model": selection.model, "reasoning_effort": selection.reasoning_effort}
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, payload["task_id"])
            if payload["parent_delegation_id"] is not None:
                self._delegation(connection, payload["parent_delegation_id"], task_id=task["task_id"])
            for report_id in payload["input_report_ids"]:
                report = self._report(connection, report_id, task_id=task["task_id"])
                if report["assembly_state"] != "finalized":
                    raise V12StoreError("input handoff report is not finalized", code="report_state_conflict")
            for decision_id in payload["input_decision_ids"]:
                self._decision(connection, decision_id, task_id=task["task_id"])
            identifier = str(payload["delegation_id"] or new_sharded_id("delegation", self.project_hash))
            if connection.execute("SELECT 1 FROM delegations WHERE delegation_id=?", (identifier,)).fetchone() is not None:
                raise V12StoreError("delegation_id already exists", code="delegation_exists")
            native_name = self._next_native_task_name(
                connection,
                task_id=str(task["task_id"]),
                profile_name=payload["profile_name"],
            )
            sequence = self._timeline(connection, event_type="delegation_created", entity_type="delegation", entity_id=identifier, payload={"delegation_id": identifier, "task_id": task["task_id"], "native_task_name": native_name}, task_id=task["task_id"], delegation_id=identifier)
            connection.execute("INSERT INTO delegations(delegation_id,project_hash,task_id,parent_delegation_id,native_task_name,objective,role,profile_name,scope,instructions,input_report_ids_json,input_decision_ids_json,model,reasoning_effort,created_at,created_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (identifier, self.project_hash, task["task_id"], payload["parent_delegation_id"], native_name, payload["objective"], payload["role"], payload["profile_name"], payload["scope"], payload["instructions"], _canonical_json(payload["input_report_ids"], label="input_report_ids"), _canonical_json(payload["input_decision_ids"], label="input_decision_ids"), payload["model"], payload["reasoning_effort"], _now(), sequence))
            delegation = self._delegation(connection, identifier, task_id=task["task_id"])
            # Creation must be immediately dispatchable, but must not echo the
            # full recovery brief as well as its native message.  Retain that
            # detailed durable brief for ``read_delegation``; the creation
            # receipt carries only the exact projection a host needs to spawn
            # this delegation and the proof that its selected profile loaded.
            worker_brief = self._worker_brief(connection, task, delegation)
            return {
                "delegation": delegation,
                "native_dispatch": worker_brief["native_dispatch"],
                "renderer": worker_brief["renderer"],
            }
        return self._mutation("create_delegation", payload, idempotency_key, write)

    def _worker_brief(self, connection: sqlite3.Connection, task: Mapping[str, Any], delegation: Mapping[str, Any]) -> dict[str, Any]:
        """Return the coordinator-authored brief without inventing knowledge routes.

        ``instructions`` is the canonical per-delegation semantic contract. It
        may carry selected knowledge paths, extracted constraints, and
        acceptance criteria compiled by the coordinator. The ledger preserves
        that text exactly and does not synthesize broad directory instructions.
        """
        from cortex_runtime.delegation import native_delegation_projection
        from cortex_runtime.worker_message import render_worker_message

        decisions = [self._decision(connection, item, task_id=str(task["task_id"])) for item in delegation["input_decision_ids"]]
        input_reports = [self._report(connection, item, task_id=str(task["task_id"])) for item in delegation["input_report_ids"]]
        if any(item["assembly_state"] != "finalized" for item in input_reports):
            raise V12StoreError("input handoff report is not finalized", code="report_state_conflict")
        report_refs = [
            {key: item[key] for key in ("report_id", "delegation_id", "report_type", "status", "assembly_state", "total_chunks", "content_digest")}
            for item in input_reports
        ]
        rendered = render_worker_message(task=task, delegation=dict(delegation) | {"input_reports": report_refs}, decisions=decisions)
        renderer = rendered.get("renderer")
        if (
            not isinstance(renderer, Mapping)
            or renderer.get("profile_state") != "loaded"
            or renderer.get("profile_name") != delegation["profile_name"]
            or not isinstance(renderer.get("profile_digest"), str)
        ):
            raise V12StoreError(
                "selected packaged profile is unavailable",
                code="profile_unavailable",
                details={"field": "profile_name", "expected": "loaded_packaged_profile"},
            )
        native_dispatch = native_delegation_projection(
            task_name=delegation["native_task_name"],
            message=rendered["message"],
            model=delegation["model"],
            reasoning_effort=delegation["reasoning_effort"],
        )
        return {
            "delegation_id": delegation["delegation_id"], "task_id": delegation["task_id"],
            "project_root": str(self.project_root), "objective": delegation["objective"],
            "role": delegation["role"], "profile_name": delegation["profile_name"], "scope": delegation["scope"],
            "native_task_name": delegation["native_task_name"],
            "instructions": delegation["instructions"], "input_report_ids": list(delegation["input_report_ids"]),
            "input_report_refs": report_refs,
            "input_decision_ids": list(delegation["input_decision_ids"]),
            "input_decisions": [
                {key: item[key] for key in ("decision_id", "subject_type", "subject_id", "subject_digest", "decision_type", "response_en", "user_language")}
                for item in decisions
            ],
            "model": delegation["model"], "reasoning_effort": delegation["reasoning_effort"],
            "worker_message": rendered["message"], "renderer": rendered["renderer"],
            "native_dispatch": native_dispatch,
        }

    def _task_for_delegation(self, delegation_id: Any, task_id: Any = None) -> tuple[str, str]:
        """Derive an owner task from its delegation and verify legacy anchors."""
        delegation = self._record_identifier(delegation_id, label="delegation_id")
        supplied = None if task_id is None else self._task_identifier(task_id)

        def read(connection: sqlite3.Connection) -> tuple[str, str]:
            owner = self._delegation(connection, delegation)
            anchor = str(owner["task_id"])
            self._task(connection, anchor)
            if supplied is not None and supplied != anchor:
                raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
            return anchor, delegation

        return self._read(read)

    def _task_for_reports(self, report_ids: list[str], task_id: Any = None, consumer_delegation_id: Any = None) -> str:
        """Derive one task from report evidence and reject mixed-owner reads."""
        supplied = None if task_id is None else self._task_identifier(task_id)
        consumer = None if consumer_delegation_id is None else self._record_identifier(consumer_delegation_id, label="consumer_delegation_id")

        def read(connection: sqlite3.Connection) -> str:
            reports = [self._report(connection, report_id) for report_id in report_ids]
            anchor = str(reports[0]["task_id"])
            self._task(connection, anchor)
            if any(str(report["task_id"]) != anchor for report in reports):
                raise V12StoreError("references do not belong to one task", code="cross_project_reference")
            if supplied is not None and supplied != anchor:
                raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
            if consumer is not None and self._delegation(connection, consumer)["task_id"] != anchor:
                raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
            return anchor

        return self._read(read)

    def submit_report(self, *, task_id: Any = None, delegation_id: Any = None, report_type: Any = None, status: Any = None, content: Any = None, report_id: Any = None, mode: Any = None, chunk_index: Any = None, section: Any = None, expected_chunk_count: Any = None, expected_content_digest: Any = None, abort_reason_en: Any = None, supersedes_report_id: Any = None, review_policy: Any = None, idempotency_key: Any = None) -> tuple[dict[str, Any], bool]:
        """Run the bounded immutable report upload state machine.

        This is intentionally data-only: an assembling/aborted/failed report
        is visible evidence, not a backend reason to stop unrelated work.
        """
        mode_value = _required_text(mode, label="mode", maximum=16).lower()
        if mode_value not in REPORT_MODES:
            raise V12StoreError("report operation is invalid", code="invalid_report_operation")
        anchor, delegation = self._task_for_delegation(delegation_id, task_id)
        identifier = None if report_id is None else self._record_identifier(report_id, label="report_id")

        def optional_type(value: Any) -> str | None:
            if value is None:
                return None
            candidate = _required_text(value, label="report_type", maximum=16).lower()
            if candidate not in REPORT_TYPES:
                raise V12StoreError("report type is invalid", code="invalid_report")
            return candidate

        def optional_status(value: Any) -> str | None:
            if value is None:
                return None
            candidate = _required_text(value, label="status", maximum=16).lower()
            if candidate not in REPORT_STATUSES:
                raise V12StoreError("report status is invalid", code="invalid_report")
            return candidate

        type_value, status_value = optional_type(report_type), optional_status(status)
        policy = None if review_policy is None else _required_text(review_policy, label="review_policy", maximum=16).lower()
        if policy is not None and policy not in PLAN_REVIEW_POLICIES:
            raise V12StoreError("review_policy is invalid", code="invalid_report")
        supersedes = None if supersedes_report_id is None else self._record_identifier(supersedes_report_id, label="supersedes_report_id")
        chunk = None
        if mode_value in {"single", "append"}:
            if content is None:
                raise V12StoreError("content is required", code="invalid_report_operation")
            chunk = _canonical_json_bytes(content, label="content")
            if chunk[2] > (REPORT_SINGLE_MAX_BYTES if mode_value == "single" else REPORT_CHUNK_MAX_BYTES):
                raise V12StoreError("report chunk is too large", code="report_chunk_too_large")
        if mode_value == "append":
            if not isinstance(chunk_index, int) or isinstance(chunk_index, bool) or chunk_index < 0:
                raise V12StoreError("chunk_index is invalid", code="invalid_report_operation")
            if not isinstance(section, str) or not section or len(section) > REPORT_SECTION_MAX_LENGTH:
                raise V12StoreError("section is invalid", code="invalid_report_operation")
        if mode_value == "finalize":
            if not isinstance(expected_chunk_count, int) or isinstance(expected_chunk_count, bool) or not 1 <= expected_chunk_count <= REPORT_MAX_CHUNKS:
                raise V12StoreError("expected_chunk_count is invalid", code="invalid_report_operation")
            _digest(expected_content_digest, required=True)
            if status_value is None:
                raise V12StoreError("status is required", code="invalid_report_operation")
        if mode_value == "abort" and _optional_text(abort_reason_en, label="abort_reason_en", maximum=4_096) is None:
            raise V12StoreError("abort_reason_en is required", code="invalid_report_operation")
        if mode_value in {"single", "begin"}:
            if type_value is None:
                raise V12StoreError("report_type is required", code="invalid_report_operation")
            if mode_value == "single" and status_value is None:
                raise V12StoreError("status is required", code="invalid_report_operation")
            if type_value != "plan" and (policy is not None or supersedes is not None):
                raise V12StoreError("plan metadata requires a plan report", code="invalid_report")
            if type_value == "plan" and policy is None:
                policy = "informational"
        else:
            if type_value is not None or policy is not None or supersedes is not None:
                raise V12StoreError("report metadata is fixed at begin", code="invalid_report_operation")
        payload = {
            "task_id": anchor, "delegation_id": delegation, "mode": mode_value,
            "report_type": type_value, "status": status_value, "content": None if chunk is None else chunk[0],
            "report_id": identifier, "chunk_index": chunk_index, "section": section,
            "expected_chunk_count": expected_chunk_count, "expected_content_digest": expected_content_digest,
            "abort_reason_en": abort_reason_en, "supersedes_report_id": supersedes, "review_policy": policy,
        }

        def usage(connection: sqlite3.Connection, task_value: str) -> dict[str, int]:
            row = _row(connection.execute("SELECT total_retained_bytes,assembling_bytes,assembling_reports FROM report_usage WHERE task_id=?", (task_value,)).fetchone())
            if row is None:
                connection.execute("INSERT INTO report_usage(task_id,total_retained_bytes,assembling_bytes,assembling_reports,updated_at) VALUES (?, 0, 0, 0, ?)", (task_value, _now()))
                return {"total_retained_bytes": 0, "assembling_bytes": 0, "assembling_reports": 0}
            return {key: int(row[key]) for key in ("total_retained_bytes", "assembling_bytes", "assembling_reports")}

        def update_usage(connection: sqlite3.Connection, task_value: str, state: Mapping[str, int]) -> None:
            connection.execute("UPDATE report_usage SET total_retained_bytes=?,assembling_bytes=?,assembling_reports=?,updated_at=? WHERE task_id=?", (state["total_retained_bytes"], state["assembling_bytes"], state["assembling_reports"], _now(), task_value))

        def insert_header(connection: sqlite3.Connection, task: Mapping[str, Any], owner: Mapping[str, Any], value: str, *, assembly_state: str, semantic_status: str, sequence: int) -> None:
            if supersedes is not None:
                prior = self._report(connection, supersedes, task_id=str(task["task_id"]))
                if prior["report_type"] != "plan":
                    raise V12StoreError("supersedes_report_id must name a plan", code="invalid_report")
            empty_digest = _sha256_prefixed(_report_manifest([]), label="report manifest")
            timestamp = _now()
            arguments = (
                value, self.project_hash, task["task_id"], owner["delegation_id"], type_value, semantic_status,
                assembly_state, empty_digest, supersedes, policy, timestamp, sequence,
                timestamp if assembly_state == "finalized" else None,
                sequence if assembly_state == "finalized" else None,
            )
            # The only supported predecessor retains its pre-chunking
            # ``content_json TEXT NOT NULL`` column.  SQLite cannot remove or
            # relax that column additively, so new headers retain a canonical
            # inert JSON placeholder there; report evidence remains solely in
            # immutable ``report_chunks`` and is never read from this legacy
            # compatibility column.
            if "content_json" in self._column_names(connection, "reports"):
                # The predecessor schema made ``status`` non-nullable, so an
                # assembling header needs the historical partial sentinel;
                # finalized single reports retain their requested status.
                legacy_arguments = (*arguments[:5], status_value or "partial", semantic_status, *arguments[6:])
                connection.execute("INSERT INTO reports(report_id,project_hash,task_id,delegation_id,report_type,status,semantic_status,content_json,assembly_state,next_chunk_index,total_chunks,total_bytes,content_digest,supersedes_report_id,review_policy,created_at,created_sequence,finalized_at,finalized_sequence,aborted_at,aborted_sequence,abort_reason_en) VALUES (?, ?, ?, ?, ?, ?, ?, 'null', ?, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)", legacy_arguments)
            else:
                arguments = (*arguments[:5], None, semantic_status, *arguments[6:])
                connection.execute("INSERT INTO reports(report_id,project_hash,task_id,delegation_id,report_type,status,semantic_status,assembly_state,next_chunk_index,total_chunks,total_bytes,content_digest,supersedes_report_id,review_policy,created_at,created_sequence,finalized_at,finalized_sequence,aborted_at,aborted_sequence,abort_reason_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)", arguments)

        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, anchor)
            owner = self._delegation(connection, delegation, task_id=task["task_id"])
            state = usage(connection, str(task["task_id"]))
            if mode_value in {"single", "begin"}:
                value = str(identifier or new_sharded_id("report", self.project_hash))
                if connection.execute("SELECT 1 FROM reports WHERE report_id=?", (value,)).fetchone() is not None:
                    raise V12StoreError("report_id already exists", code="report_exists")
                if mode_value == "begin":
                    if state["assembling_reports"] >= REPORT_ASSEMBLING_MAX_PER_TASK:
                        raise V12StoreError("report quota is exceeded", code="report_quota_exceeded")
                    sequence = self._timeline(connection, event_type="report_started", entity_type="report", entity_id=value, payload={"report_id": value, "delegation_id": owner["delegation_id"], "report_type": type_value}, task_id=task["task_id"], delegation_id=owner["delegation_id"], report_id=value)
                    insert_header(connection, task, owner, value, assembly_state="assembling", semantic_status="pending", sequence=sequence)
                    state["assembling_reports"] += 1
                    update_usage(connection, str(task["task_id"]), state)
                    return {"report": self._compact_report(self._report(connection, value, task_id=task["task_id"])), "assembly_state": "assembling", "next_chunk_index": 0}
                assert chunk is not None
                if chunk[2] > REPORT_MAX_BYTES or state["total_retained_bytes"] + chunk[2] > REPORT_RETAINED_MAX_BYTES_PER_TASK:
                    raise V12StoreError("report quota is exceeded", code="report_quota_exceeded")
                sequence = self._timeline(connection, event_type="report_submitted", entity_type="report", entity_id=value, payload={"report_id": value, "delegation_id": owner["delegation_id"], "report_type": type_value, "status": status_value, "total_chunks": 1, "total_bytes": chunk[2]}, task_id=task["task_id"], delegation_id=owner["delegation_id"], report_id=value)
                insert_header(connection, task, owner, value, assembly_state="assembling", semantic_status="pending", sequence=sequence)
                connection.execute("INSERT INTO report_chunks(report_id,chunk_index,section,content_json,content_digest,content_bytes,created_at) VALUES (?, 0, 'body', ?, ?, ?, ?)", (value, chunk[1], chunk[3], chunk[2], _now()))
                digest = self._report_digest(connection, value)
                semantic = canonical_report_semantic_status(str(type_value), chunk[0])
                connection.execute("UPDATE reports SET next_chunk_index=1,total_chunks=1,total_bytes=?,content_digest=?,assembly_state='finalized',status=?,semantic_status=?,finalized_at=?,finalized_sequence=? WHERE report_id=?", (chunk[2], digest, status_value, semantic, _now(), sequence, value))
                state["total_retained_bytes"] += chunk[2]
                update_usage(connection, str(task["task_id"]), state)
                return {"report": self._compact_report(self._report(connection, value, task_id=task["task_id"]))}

            if identifier is None:
                raise V12StoreError("report_id is required", code="invalid_report_operation")
            report = self._report(connection, identifier, task_id=task["task_id"])
            if report["delegation_id"] != owner["delegation_id"]:
                raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
            if mode_value == "append":
                if report["assembly_state"] != "assembling":
                    raise V12StoreError("report state conflicts with operation", code="report_state_conflict")
                assert chunk is not None and isinstance(chunk_index, int) and isinstance(section, str)
                if chunk_index < int(report["next_chunk_index"]):
                    existing = _row(connection.execute("SELECT section,content_digest,content_bytes FROM report_chunks WHERE report_id=? AND chunk_index=?", (identifier, chunk_index)).fetchone())
                    if existing is not None and existing["section"] == section and existing["content_digest"] == chunk[3] and int(existing["content_bytes"]) == chunk[2]:
                        return {"report": self._compact_report(report), "accepted_chunk_index": chunk_index, "next_chunk_index": report["next_chunk_index"], "chunk_digest": chunk[3], "chunk_bytes": chunk[2], "expected_chunk_count": report["total_chunks"], "expected_content_digest": report["content_digest"]}
                    raise V12StoreError("report chunk conflicts with existing chunk", code="report_chunk_conflict")
                if chunk_index != int(report["next_chunk_index"]):
                    raise V12StoreError("report chunk is out of order", code="report_chunk_out_of_order")
                if int(report["total_chunks"]) >= REPORT_MAX_CHUNKS or int(report["total_bytes"]) + chunk[2] > REPORT_MAX_BYTES or state["total_retained_bytes"] + chunk[2] > REPORT_RETAINED_MAX_BYTES_PER_TASK or state["assembling_bytes"] + chunk[2] > REPORT_ASSEMBLING_MAX_BYTES_PER_TASK:
                    raise V12StoreError("report quota is exceeded", code="report_quota_exceeded")
                connection.execute("INSERT INTO report_chunks(report_id,chunk_index,section,content_json,content_digest,content_bytes,created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (identifier, chunk_index, section, chunk[1], chunk[3], chunk[2], _now()))
                digest = self._report_digest(connection, identifier)
                self._timeline(
                    connection, event_type="report_chunk_appended", entity_type="report_chunk", entity_id=identifier,
                    payload={
                        "report_id": identifier, "delegation_id": owner["delegation_id"], "chunk_index": chunk_index,
                        "section": section, "content_digest": chunk[3], "content_bytes": chunk[2],
                    },
                    task_id=task["task_id"], delegation_id=owner["delegation_id"], report_id=identifier,
                )
                connection.execute("UPDATE reports SET next_chunk_index=?,total_chunks=?,total_bytes=?,content_digest=? WHERE report_id=?", (chunk_index + 1, int(report["total_chunks"]) + 1, int(report["total_bytes"]) + chunk[2], digest, identifier))
                state["total_retained_bytes"] += chunk[2]
                state["assembling_bytes"] += chunk[2]
                update_usage(connection, str(task["task_id"]), state)
                current = self._report(connection, identifier, task_id=task["task_id"])
                return {"report": self._compact_report(current), "accepted_chunk_index": chunk_index, "next_chunk_index": current["next_chunk_index"], "chunk_digest": chunk[3], "chunk_bytes": chunk[2], "expected_chunk_count": current["total_chunks"], "expected_content_digest": current["content_digest"]}
            if mode_value == "finalize":
                if report["assembly_state"] == "finalized" and int(report["total_chunks"]) == expected_chunk_count and report["content_digest"] == expected_content_digest and report["status"] == status_value:
                    return {"report": self._compact_report(report)}
                if report["assembly_state"] != "assembling":
                    raise V12StoreError("report state conflicts with operation", code="report_state_conflict")
                actual = self._report_digest(connection, identifier)
                if int(report["total_chunks"]) != expected_chunk_count or actual != expected_content_digest or report["content_digest"] != actual:
                    raise V12StoreError("report manifest does not match", code="report_manifest_mismatch")
                sequence = self._timeline(connection, event_type="report_submitted", entity_type="report", entity_id=identifier, payload={"report_id": identifier, "delegation_id": owner["delegation_id"], "report_type": report["report_type"], "status": status_value, "total_chunks": report["total_chunks"], "total_bytes": report["total_bytes"], "content_digest": actual}, task_id=task["task_id"], delegation_id=owner["delegation_id"], report_id=identifier)
                chunks = self._report_chunks(connection, identifier)
                canonical_content: object = chunks[0]["content"] if len(chunks) == 1 else None
                if len(chunks) > 1 and all(isinstance(item["content"], Mapping) for item in chunks):
                    merged: dict[str, Any] = {}
                    for item in chunks:
                        for key, item_value in item["content"].items():
                            if key in merged:
                                merged = {}
                                break
                            merged[key] = item_value
                        if not merged:
                            break
                    canonical_content = merged or None
                semantic = canonical_report_semantic_status(str(report["report_type"]), canonical_content)
                connection.execute("UPDATE reports SET assembly_state='finalized',status=?,semantic_status=?,finalized_at=?,finalized_sequence=? WHERE report_id=?", (status_value, semantic, _now(), sequence, identifier))
                state["assembling_bytes"] -= int(report["total_bytes"])
                state["assembling_reports"] -= 1
                update_usage(connection, str(task["task_id"]), state)
                return {"report": self._compact_report(self._report(connection, identifier, task_id=task["task_id"]))}
            # abort
            if report["assembly_state"] == "aborted" and report["abort_reason_en"] == abort_reason_en:
                return {"report": self._compact_report(report)}
            if report["assembly_state"] != "assembling":
                raise V12StoreError("report state conflicts with operation", code="report_state_conflict")
            sequence = self._timeline(connection, event_type="report_aborted", entity_type="report", entity_id=identifier, payload={"report_id": identifier, "delegation_id": owner["delegation_id"], "total_chunks": report["total_chunks"], "total_bytes": report["total_bytes"]}, task_id=task["task_id"], delegation_id=owner["delegation_id"], report_id=identifier)
            connection.execute("UPDATE reports SET assembly_state='aborted',aborted_at=?,aborted_sequence=?,abort_reason_en=? WHERE report_id=?", (_now(), sequence, abort_reason_en, identifier))
            state["assembling_bytes"] -= int(report["total_bytes"])
            state["assembling_reports"] -= 1
            update_usage(connection, str(task["task_id"]), state)
            return {"report": self._compact_report(self._report(connection, identifier, task_id=task["task_id"]))}
        return self._mutation("submit_report", payload, idempotency_key, write)

    def record_user_decision(self, *, task_id: Any, subject_type: Any, subject_id: Any, subject_digest: Any = None, decision_type: Any = None, prompt_en: Any = None, response_original: Any = None, response_en: Any = None, user_language: Any = None, approval_handle: Any = None, approval_view_content_digest: Any = None, approval_view_source_sequence: Any = None, supersedes_decision_id: Any = None, idempotency_key: Any = None) -> tuple[dict[str, Any], bool]:
        """Append a user-origin decision with exact immutable subject binding.

        Attribution is intentionally an honest coordinator assertion, never an
        authentication claim and never consulted by any admission path.
        """
        anchor = self._task_identifier(task_id)
        kind = _required_text(subject_type, label="subject_type", maximum=16).lower()
        if kind not in DECISION_SUBJECTS:
            raise V12StoreError("decision subject is invalid", code="invalid_decision_subject")
        decision = _required_text(decision_type, label="decision_type", maximum=32).lower()
        if decision not in DECISION_TYPES:
            raise V12StoreError("decision type is invalid", code="invalid_decision_type")
        subject = self._task_identifier(subject_id) if kind == "task" else self._record_identifier(subject_id, label="subject_id")
        payload = {
            "task_id": anchor, "subject_type": kind, "subject_id": subject,
            "subject_digest": _digest(subject_digest, required=kind in {"plan", "report"}),
            "decision_type": decision,
            "prompt_en": _optional_text(prompt_en, label="prompt_en") or "",
            "response_original": _optional_text(response_original, label="response_original") or "",
            "response_en": _optional_text(response_en, label="response_en") or "",
            "user_language": _language(user_language),
            "approval_handle": None if approval_handle is None else _required_text(approval_handle, label="approval_handle", maximum=160),
            "approval_view_content_digest": _digest(approval_view_content_digest, label="approval_view_content_digest"),
            "approval_view_source_sequence": approval_view_source_sequence,
            "supersedes_decision_id": None if supersedes_decision_id is None else self._record_identifier(supersedes_decision_id, label="supersedes_decision_id"),
        }
        requires_plan_approval_view = kind == "plan" and decision == "approve"
        if requires_plan_approval_view and payload["approval_handle"] is not None and IDENTIFIER_RE.fullmatch(payload["approval_handle"]) is None:
            raise V12StoreError("approval_handle is invalid", code="invalid_argument", details={"field": "approval_handle"})
        if payload["approval_view_source_sequence"] is not None and (not isinstance(payload["approval_view_source_sequence"], int) or isinstance(payload["approval_view_source_sequence"], bool) or payload["approval_view_source_sequence"] < 0):
            raise V12StoreError("approval_view_source_sequence is invalid", code="invalid_argument", details={"field": "approval_view_source_sequence"})
        if requires_plan_approval_view:
            # Receipts and unrelated chronology may advance timeline after the
            # handle was minted.  Approval still requires a real filesystem
            # and digest check, but not a projection newer than every event.
            from cortex_runtime.v12_projections import human_view as verified_human_view
            current_view = verified_human_view(self, anchor, f"plans/revisions/{subject}.md", require_fresh=False)
            if current_view.get("status") != "ready" or current_view.get("content_digest") != payload["approval_view_content_digest"]:
                raise V12StoreError("approval view is no longer ready", code="approval_view_not_ready")
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, anchor)
            bound_digest: str | None
            if kind == "task":
                if subject != task["task_id"]:
                    raise V12StoreError("task decision must use the anchored task", code="cross_project_reference")
                bound_digest = _sha256_prefixed({key: task[key] for key in ("task_id", "objective", "user_request_original", "user_language", "task_contract_version", "requirements", "constraints", "acceptance_criteria", "verification_plan")}, label="task decision subject")
            elif kind == "delegation":
                item = self._delegation(connection, subject, task_id=anchor)
                bound_digest = _sha256_prefixed({key: item[key] for key in ("delegation_id", "task_id", "objective", "role", "scope", "instructions", "input_report_ids", "input_decision_ids", "model", "reasoning_effort")}, label="delegation decision subject")
            elif kind in {"plan", "report"}:
                item = self._report(connection, subject, task_id=anchor)
                if kind == "plan" and item["report_type"] != "plan":
                    raise V12StoreError("plan decision must name a plan report", code="invalid_decision_subject")
                if kind == "plan" and (item["assembly_state"] != "finalized" or item["status"] != "completed"):
                    raise V12StoreError("plan is not finalized evidence", code="decision_subject_not_finalized")
                bound_digest = str(item["content_digest"])
            else:  # initiative: require a same-task relationship, not merely shard membership.
                item = self._initiative(connection, subject)
                if item["initiative_id"] not in self._task_initiative_ids(connection, anchor):
                    raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
                bound_digest = _sha256_prefixed({key: item[key] for key in ("initiative_id", "goal", "risk", "status", "notes", "latest_revision")}, label="initiative decision subject")
            if payload["subject_digest"] is not None and payload["subject_digest"] != bound_digest:
                raise V12StoreError("decision subject digest does not match", code="decision_subject_digest_mismatch")
            approval_handle: sqlite3.Row | None = None
            if kind == "plan":
                if not payload["response_original"]:
                    raise V12StoreError("plan decision requires a new user response", code="decision_response_required")
                if payload["response_original"] in {task["user_request_original"], task["objective"]}:
                    # Exact replay prevention only: arbitrary ordinary-chat
                    # prose is not semantically classified by the backend.
                    raise V12StoreError("plan decision response reuses the original task request", code="decision_response_reused_original")
            if requires_plan_approval_view:
                if payload["approval_handle"] is None or payload["approval_view_content_digest"] is None or payload["approval_view_source_sequence"] is None:
                    raise V12StoreError("plan approval requires a ready approval view", code="approval_view_required")
                approval_handle = connection.execute("SELECT * FROM approval_handles WHERE approval_handle=? AND project_hash=?", (payload["approval_handle"], self.project_hash)).fetchone()
                if approval_handle is None:
                    raise V12StoreError("approval handle was not found", code="approval_handle_not_found")
                expected_relative = f"plans/revisions/{subject}.md"
                if (
                    approval_handle["task_id"] != anchor
                    or approval_handle["report_id"] != subject
                    or approval_handle["report_content_digest"] != bound_digest
                    or approval_handle["view_relative_path"] != expected_relative
                    or approval_handle["view_content_digest"] != payload["approval_view_content_digest"]
                    or int(approval_handle["view_source_sequence"]) != payload["approval_view_source_sequence"]
                    or approval_handle["request_digest"] != _sha256_prefixed(task["user_request_original"], label="user request original")
                    or approval_handle["consumed_decision_id"] is not None
                ):
                    raise V12StoreError("approval handle does not match the ready plan view", code="approval_handle_mismatch")
                view = connection.execute("SELECT source_sequence,content_digest,status FROM projection_files WHERE task_id=? AND relative_path=?", (anchor, expected_relative)).fetchone()
                # The handle binds the immutable plan and the verified view
                # digest.  Later task-scoped chronology (including read
                # receipts and unrelated initiative events) must not revoke
                # that relation when the same view content remains ready.
                if view is None or str(view["status"]) != "ready" or str(view["content_digest"]) != payload["approval_view_content_digest"]:
                    raise V12StoreError("approval view is no longer ready", code="approval_view_not_ready")
            if payload["supersedes_decision_id"] is not None:
                prior = self._decision(connection, payload["supersedes_decision_id"], task_id=anchor)
                if prior["subject_type"] != kind or prior["subject_id"] != subject:
                    raise V12StoreError("superseded decision has a different subject", code="cross_project_reference")
            identifier = new_sharded_id("decision", self.project_hash)
            sequence = self._timeline(connection, event_type="user_decision_recorded", entity_type="user_decision", entity_id=identifier, payload={"decision_id": identifier, "subject_type": kind, "subject_id": subject, "subject_digest": bound_digest, "decision_type": decision}, task_id=anchor, decision_id=identifier)
            connection.execute("INSERT INTO user_decisions(decision_id,project_hash,task_id,subject_type,subject_id,subject_digest,decision_type,prompt_en,response_original,response_en,user_language,attribution,supersedes_decision_id,created_at,created_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (identifier, self.project_hash, anchor, kind, subject, bound_digest, decision, payload["prompt_en"], payload["response_original"], payload["response_en"], payload["user_language"], DECISION_ATTRIBUTION, payload["supersedes_decision_id"], _now(), sequence))
            if approval_handle is not None:
                cursor = connection.execute("UPDATE approval_handles SET consumed_decision_id=? WHERE approval_handle=? AND consumed_decision_id IS NULL", (identifier, payload["approval_handle"]))
                if cursor.rowcount != 1:
                    raise V12StoreError("approval handle has already been used", code="approval_handle_consumed")
            return {"decision": self._compact_decision(self._decision(connection, identifier, task_id=anchor))}
        return self._mutation("record_user_decision", payload, idempotency_key, write)

    def set_governance_mode(self, *, task_id: Any, mode: Any, rationale: Any, risk_factors: Any, source: Any, initiative_id: Any, idempotency_key: Any) -> tuple[dict[str, Any], bool]:
        mode_value, source_value = _required_text(mode, label="mode", maximum=16).lower(), _required_text(source, label="source", maximum=32).lower()
        if mode_value not in GOVERNANCE_MODES or source_value not in GOVERNANCE_SOURCES:
            raise V12StoreError("governance assessment is invalid", code="invalid_governance_mode")
        payload = {"task_id": self._task_identifier(task_id), "mode": mode_value, "rationale": _optional_text(rationale, label="rationale"), "risk_factors": _text_list(risk_factors, label="risk_factors"), "source": source_value, "initiative_id": None if initiative_id is None else self._record_identifier(initiative_id, label="initiative_id")}
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, payload["task_id"])
            if payload["initiative_id"] is not None:
                self._initiative(connection, payload["initiative_id"])
            identifier = f"assessment-{uuid.uuid4().hex}"
            sequence = self._timeline(connection, event_type="governance_mode_set", entity_type="governance_assessment", entity_id=identifier, payload={"assessment_id": identifier, "task_id": task["task_id"], "mode": mode_value}, task_id=task["task_id"], initiative_id=payload["initiative_id"], assessment_id=identifier)
            connection.execute("INSERT INTO governance_assessments(assessment_id,project_hash,task_id,initiative_id,mode,source,rationale,risk_factors_json,created_at,created_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (identifier, self.project_hash, task["task_id"], payload["initiative_id"], mode_value, source_value, payload["rationale"], _canonical_json(payload["risk_factors"], label="risk_factors"), _now(), sequence))
            return {"assessment": self._assessment(connection, identifier)}
        return self._mutation("set_governance_mode", payload, idempotency_key, write)

    def record_initiative(self, *, task_id: Any, goal: Any = None, initiative_id: Any = None, parent_initiative_id: Any = None, risk: Any = None, status: Any = None, dependencies: Any = None, linked_task_ids: Any = None, linked_delegation_ids: Any = None, linked_report_ids: Any = None, linked_decision_ids: Any = None, notes: Any = None, idempotency_key: Any = None) -> tuple[dict[str, Any], bool]:
        state = None if status is None else _required_text(status, label="status", maximum=16).lower()
        if state is not None and state not in INITIATIVE_STATUSES:
            raise V12StoreError("initiative status is invalid", code="invalid_initiative_status")
        payload = {"task_id": self._task_identifier(task_id), "goal": None if goal is None else _opaque_text(goal, label="goal"), "goal_present": goal is not None, "initiative_id": None if initiative_id is None else self._record_identifier(initiative_id, label="initiative_id"), "parent_initiative_id": None if parent_initiative_id is None else self._record_identifier(parent_initiative_id, label="parent_initiative_id"), "parent_present": parent_initiative_id is not None, "risk": _optional_text(risk, label="risk"), "risk_present": risk is not None, "status": state, "dependencies": None if dependencies is None else _identifier_list(dependencies, label="dependencies"), "linked_task_ids": None if linked_task_ids is None else _identifier_list(linked_task_ids, label="linked_task_ids"), "linked_delegation_ids": None if linked_delegation_ids is None else _identifier_list(linked_delegation_ids, label="linked_delegation_ids"), "linked_report_ids": None if linked_report_ids is None else _identifier_list(linked_report_ids, label="linked_report_ids"), "linked_decision_ids": None if linked_decision_ids is None else _identifier_list(linked_decision_ids, label="linked_decision_ids"), "notes": None if notes is None else _strict_json(notes, label="notes"), "notes_present": notes is not None}
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            self._task(connection, payload["task_id"])
            identifier = str(payload["initiative_id"] or new_sharded_id("initiative", self.project_hash))
            existing = None
            if payload["initiative_id"] is not None and connection.execute("SELECT 1 FROM initiatives WHERE initiative_id=? AND project_hash=?", (identifier, self.project_hash)).fetchone() is not None:
                existing = self._initiative(connection, identifier)
            if existing is None and not payload["goal_present"]:
                raise V12StoreError("goal is required when creating an initiative", code="invalid_argument", details={"field": "goal"})
            current_links = [] if existing is None else self._initiative_links(connection, [identifier])
            by_kind = {kind: sorted(link["target_id"] for link in current_links if link["relationship"] == kind) for kind in _LINK_TYPES}
            parent = payload["parent_initiative_id"] if payload["parent_present"] else (by_kind["parent"][0] if by_kind["parent"] else None)
            dependency_values = by_kind["dependency"] if payload["dependencies"] is None else payload["dependencies"]
            task_values = by_kind["task"] if payload["linked_task_ids"] is None else payload["linked_task_ids"]
            delegation_values = by_kind["delegation"] if payload["linked_delegation_ids"] is None else payload["linked_delegation_ids"]
            report_values = by_kind["report"] if payload["linked_report_ids"] is None else payload["linked_report_ids"]
            decision_values = by_kind["decision"] if payload["linked_decision_ids"] is None else payload["linked_decision_ids"]
            state_value = (existing["status"] if existing is not None else "proposed") if payload["status"] is None else payload["status"]
            risk_value = (existing["risk"] if existing is not None else None) if not payload["risk_present"] else payload["risk"]
            notes_value = (existing["notes"] if existing is not None else []) if not payload["notes_present"] else payload["notes"]
            goal_value = (existing["goal"] if existing is not None else None) if not payload["goal_present"] else payload["goal"]
            for candidate, label in [(parent, "parent_initiative_id"), *[(item, "dependencies") for item in dependency_values]]:
                if candidate is not None:
                    self._record_identifier(candidate, label=label)
            if parent is not None:
                if parent == identifier:
                    raise V12StoreError("initiative parent is invalid", code="invalid_initiative_parent")
                self._initiative(connection, parent)
                cursor, seen = parent, {identifier}
                while cursor is not None:
                    if cursor in seen:
                        raise V12StoreError("initiative parent is cyclic", code="invalid_initiative_parent")
                    seen.add(cursor)
                    row = connection.execute("SELECT target_id FROM initiative_links WHERE initiative_id=? AND relationship='parent'", (cursor,)).fetchone()
                    cursor = None if row is None else str(row[0])
            for item in task_values:
                self._task(connection, item)
            for item in report_values:
                self._report(connection, item)
            for item in delegation_values:
                self._delegation(connection, item, task_id=payload["task_id"])
            for item in decision_values:
                self._decision(connection, item, task_id=payload["task_id"])
            timestamp, revision = _now(), 1 if existing is None else int(existing["latest_revision"]) + 1
            sequence = self._timeline(connection, event_type="initiative_created" if existing is None else "initiative_revised", entity_type="initiative", entity_id=identifier, payload={"initiative_id": identifier, "revision_number": revision, "status": state_value}, task_id=payload["task_id"], initiative_id=identifier)
            if existing is None:
                connection.execute("INSERT INTO initiatives(initiative_id,project_hash,goal,risk,status,notes_json,created_at,updated_at,latest_revision,created_sequence,updated_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (identifier, self.project_hash, goal_value, risk_value, state_value, _canonical_json(notes_value, label="notes"), timestamp, timestamp, revision, sequence, sequence))
            else:
                connection.execute("UPDATE initiatives SET goal=?,risk=?,status=?,notes_json=?,updated_at=?,latest_revision=?,updated_sequence=? WHERE initiative_id=? AND project_hash=?", (goal_value, risk_value, state_value, _canonical_json(notes_value, label="notes"), timestamp, revision, sequence, identifier, self.project_hash))
            revision_payload = {"initiative_id": identifier, "revision_number": revision, "task_id": payload["task_id"], "goal": goal_value, "risk": risk_value, "status": state_value, "notes": notes_value, "parent_initiative_id": parent, "dependencies": dependency_values, "linked_task_ids": task_values, "linked_delegation_ids": delegation_values, "linked_report_ids": report_values, "linked_decision_ids": decision_values}
            connection.execute("INSERT INTO initiative_revisions(initiative_id,revision_number,project_hash,occurred_at,sequence,payload_json) VALUES (?, ?, ?, ?, ?, ?)", (identifier, revision, self.project_hash, timestamp, sequence, _canonical_json(revision_payload, label="initiative revision")))
            connection.execute("DELETE FROM initiative_links WHERE initiative_id=? AND project_hash=?", (identifier, self.project_hash))
            if parent is not None:
                self._insert_link(connection, identifier, "parent", parent, resolved=True)
            for dependency in dependency_values:
                self._insert_link(connection, identifier, "dependency", dependency)
            for item in task_values:
                self._insert_link(connection, identifier, "task", item, resolved=True)
            for item in delegation_values:
                self._insert_link(connection, identifier, "delegation", item, resolved=True)
            for item in report_values:
                self._insert_link(connection, identifier, "report", item, resolved=True)
            for item in decision_values:
                self._insert_link(connection, identifier, "decision", item, resolved=True)
            self._refresh_initiative_warnings(connection)
            links = self._initiative_links(connection, [identifier])
            return {"initiative": self._initiative(connection, identifier), "warnings": self._warning_values(links)}
        return self._mutation("record_initiative", payload, idempotency_key, write)

    def _insert_link(self, connection: sqlite3.Connection, initiative_id: str, relationship: str, target_id: str, *, resolved: bool = False) -> None:
        if relationship not in _LINK_TYPES:
            raise V12StoreError("initiative link is invalid", code="storage_unavailable")
        connection.execute("INSERT INTO initiative_links(initiative_id,project_hash,relationship,target_id,is_resolved,warnings_json,created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (initiative_id, self.project_hash, relationship, target_id, int(resolved), "[]", _now()))

    def _refresh_initiative_warnings(self, connection: sqlite3.Connection) -> None:
        existing = {str(row[0]) for row in connection.execute("SELECT initiative_id FROM initiatives WHERE project_hash=?", (self.project_hash,))}
        for relationship in ("dependency", "parent"):
            rows = connection.execute("SELECT link_id,initiative_id,target_id FROM initiative_links WHERE project_hash=? AND relationship=?", (self.project_hash, relationship)).fetchall()
            graph: dict[str, set[str]] = {item: set() for item in existing}
            for row in rows:
                if str(row["target_id"]) in existing:
                    graph.setdefault(str(row["initiative_id"]), set()).add(str(row["target_id"]))
            def path(start: str, target: str) -> bool:
                stack, seen = [start], set()
                while stack:
                    item = stack.pop()
                    if item == target:
                        return True
                    if item not in seen:
                        seen.add(item)
                        stack.extend(graph.get(item, set()) - seen)
                return False
            for row in rows:
                source, target = str(row["initiative_id"]), str(row["target_id"])
                resolved = target in existing
                warnings = [f"unresolved_{relationship}"] if not resolved else ([f"cyclic_{relationship}"] if path(target, source) else [])
                connection.execute("UPDATE initiative_links SET is_resolved=?,warnings_json=? WHERE link_id=?", (int(resolved), _canonical_json(warnings, label="link warnings"), int(row["link_id"])))

    def _require_documentation_impact_report(self, connection: sqlite3.Connection, task_id: str) -> None:
        """Require consumed worker-owned documentation evidence for governed closure.

        The relation check intentionally never reads a report body or closure
        evidence.  A light/full task needs a post-approval ``technical_writer``
        result that names the approved plan/decision and every earlier finalized
        result report.  The worker handoff and its durable report are sufficient;
        the coordinator must not reread opaque report prose merely to close the
        task.
        """
        gate = self._governance_gate(connection, task_id)
        if gate is None or gate["mode"] not in {"light", "full"}:
            return
        plan_id, approval_id = gate.get("plan_report_id"), gate.get("approval_decision_id")
        if not isinstance(plan_id, str) or not isinstance(approval_id, str):
            raise V12StoreError(
                "documentation impact assessment is required before closure",
                code="documentation_impact_required",
            )
        approval = self._decision(connection, approval_id, task_id=task_id)
        candidates = connection.execute(
            "SELECT delegation_id FROM delegations WHERE task_id=? AND project_hash=? "
            "AND profile_name='technical_writer' AND created_sequence>? ORDER BY created_sequence",
            (task_id, self.project_hash, int(approval["created_sequence"])),
        ).fetchall()
        if not candidates:
            raise V12StoreError(
                "documentation impact assessment is required before closure",
                code="documentation_impact_required",
            )
        for row in candidates:
            delegation = self._delegation(connection, str(row["delegation_id"]), task_id=task_id)
            inputs = set(delegation["input_report_ids"])
            decisions = set(delegation["input_decision_ids"])
            prior_results = {
                str(item["report_id"])
                for item in connection.execute(
                    "SELECT report_id FROM reports WHERE task_id=? AND project_hash=? "
                    "AND report_type='result' AND status='completed' AND assembly_state='finalized' "
                    "AND created_sequence<?",
                    (task_id, self.project_hash, int(delegation["created_sequence"])),
                ).fetchall()
            }
            if plan_id not in inputs or approval_id not in decisions or not prior_results.issubset(inputs):
                continue
            reports = connection.execute(
                "SELECT report_id,created_sequence FROM reports WHERE delegation_id=? AND task_id=? "
                "AND report_type='result' AND status='completed' AND assembly_state='finalized' "
                "ORDER BY created_sequence",
                (delegation["delegation_id"], task_id),
            ).fetchall()
            # The finalized worker-owned report is the durable handoff.  Its
            # body remains available to downstream workers through read_reports,
            # but coordinator consumption is deliberately not a closure
            # prerequisite.  Requiring a receipt here recreated the protocol
            # contradiction where the coordinator had to reread the report it
            # had already received as a Summary + exact Report ref.
            if reports:
                return
        raise V12StoreError(
            "documentation impact evidence is incomplete before closure",
            code="documentation_impact_evidence_missing",
        )

    def _require_initiative_closures(self, connection: sqlite3.Connection, task_id: str) -> None:
        """Keep initiative and task closure handoff explicit and ordered."""
        initiatives = self._task_initiative_ids(connection, task_id)
        if not initiatives:
            return
        closed = {
            str(row[0])
            for row in connection.execute(
                "SELECT subject_id FROM governance_closures WHERE project_hash=? AND subject_type='initiative' "
                "AND subject_id IN (%s)" % ",".join("?" for _ in initiatives),
                [self.project_hash, *initiatives],
            ).fetchall()
        }
        if set(initiatives) - closed:
            raise V12StoreError(
                "every initiative related to this task requires its own closure before task closure",
                code="initiative_closure_required",
            )

    def submit_governance_closure(self, *, task_id: Any, subject_type: Any, subject_id: Any, verdict: Any, evidence: Any, unresolved_risks: Any, follow_ups: Any, initiative_status: Any, completion_notes: Any, idempotency_key: Any) -> tuple[dict[str, Any], bool]:
        anchor = self._task_identifier(task_id)
        kind, decision = _required_text(subject_type, label="subject_type", maximum=16).lower(), _required_text(verdict, label="verdict", maximum=32).lower()
        if kind not in CLOSURE_SUBJECTS or decision not in CLOSURE_VERDICTS:
            raise V12StoreError("closure is invalid", code="invalid_closure_subject")
        subject = self._task_identifier(subject_id) if kind == "task" else self._record_identifier(subject_id, label="subject_id")
        if kind == "task" and subject != anchor:
            raise V12StoreError("task closure must use the anchored task", code="cross_project_reference")
        status_value = None if initiative_status is None else _required_text(initiative_status, label="initiative_status", maximum=16).lower()
        if status_value is not None and status_value not in INITIATIVE_STATUSES:
            raise V12StoreError("initiative status is invalid", code="invalid_initiative_status")
        if kind != "initiative" and status_value is not None:
            raise V12StoreError("initiative_status requires an initiative closure", code="invalid_closure_subject")
        payload = {"task_id": anchor, "subject_type": kind, "subject_id": subject, "verdict": decision, "evidence": _strict_json(evidence, label="evidence"), "unresolved_risks": _text_list(unresolved_risks, label="unresolved_risks"), "follow_ups": _text_list(follow_ups, label="follow_ups"), "initiative_status": status_value, "completion_notes": None if completion_notes is None else _strict_json(completion_notes, label="completion_notes")}
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            self._task(connection, anchor)
            if kind == "task":
                existing = self._task_closure(connection, anchor)
                if existing is not None:
                    return {
                        "closure": self._closure(connection, str(existing["closure_id"])),
                        "initiative": None,
                        "warnings": [],
                        "next_action": {"state": "task_closed", "task_ref": task_ref(anchor)},
                    }
            initiative: dict[str, Any] | None = None
            closure_task, closure_initiative = (anchor, None) if kind == "task" else (None, subject)
            closure_id = f"closure-{uuid.uuid4().hex}"
            if closure_initiative is not None:
                if closure_initiative not in self._task_initiative_ids(connection, anchor):
                    raise V12StoreError("initiative closure must be related to the anchored task", code="cross_project_reference")
                initiative = self._initiative(connection, closure_initiative)
                if status_value is not None or payload["completion_notes"] is not None:
                    sequence = self._timeline(connection, event_type="initiative_revised_by_closure", entity_type="initiative", entity_id=closure_initiative, payload={"initiative_id": closure_initiative, "closure_id": closure_id, "status": status_value or initiative["status"], "reason": "governance_closure"}, task_id=anchor, initiative_id=closure_initiative, closure_id=closure_id)
                    revision = int(initiative["latest_revision"]) + 1
                    timestamp, next_status = _now(), status_value or initiative["status"]
                    next_notes = payload["completion_notes"] if payload["completion_notes"] is not None else initiative["notes"]
                    connection.execute("UPDATE initiatives SET status=?,notes_json=?,updated_at=?,latest_revision=?,updated_sequence=? WHERE initiative_id=? AND project_hash=?", (next_status, _canonical_json(next_notes, label="completion_notes"), timestamp, revision, sequence, closure_initiative, self.project_hash))
                    history = {"initiative_id": closure_initiative, "revision_number": revision, "status": next_status, "notes": next_notes, "reason": "governance_closure"}
                    connection.execute("INSERT INTO initiative_revisions(initiative_id,revision_number,project_hash,occurred_at,sequence,payload_json) VALUES (?, ?, ?, ?, ?, ?)", (closure_initiative, revision, self.project_hash, timestamp, sequence, _canonical_json(history, label="initiative revision")))
                    initiative = self._initiative(connection, closure_initiative)
            sequence = self._timeline(connection, event_type="governance_closure_submitted", entity_type="governance_closure", entity_id=closure_id, payload={"closure_id": closure_id, "task_id": anchor, "subject_type": kind, "subject_id": subject, "verdict": decision}, task_id=anchor, initiative_id=closure_initiative, closure_id=closure_id)
            timestamp = _now()
            connection.execute("INSERT INTO governance_closures(closure_id,project_hash,subject_type,subject_id,verdict,evidence_json,unresolved_risks_json,follow_ups_json,initiative_status,completion_notes_json,created_at,created_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (closure_id, self.project_hash, kind, subject, decision, _canonical_json(payload["evidence"], label="evidence"), _canonical_json(payload["unresolved_risks"], label="unresolved_risks"), _canonical_json(payload["follow_ups"], label="follow_ups"), status_value, None if payload["completion_notes"] is None else _canonical_json(payload["completion_notes"], label="completion_notes"), timestamp, sequence))
            if kind == "task":
                connection.execute("UPDATE tasks SET updated_at=?,updated_sequence=? WHERE task_id=? AND project_hash=?", (timestamp, sequence, anchor, self.project_hash))
            links = [] if closure_initiative is None else self._initiative_links(connection, [closure_initiative])
            next_action = (
                {"tool": "submit_governance_closure", "suggested_subject": {"task_ref": task_ref(anchor), "subject_type": "task", "subject_ref": task_ref(anchor)}}
                if kind == "initiative"
                else {"state": "task_closed", "task_ref": task_ref(anchor)}
            )
            return {"closure": self._closure(connection, closure_id), "initiative": initiative, "warnings": self._warning_values(links), "next_action": next_action}
        return self._mutation("submit_governance_closure", payload, idempotency_key, write)

    def _initiative_links(self, connection: sqlite3.Connection, initiatives: Sequence[str] | None = None) -> list[dict[str, Any]]:
        clauses, values = ["project_hash=?"], [self.project_hash]
        if initiatives is not None:
            if not initiatives:
                return []
            clauses.append("initiative_id IN (%s)" % ",".join("?" for _ in initiatives))
            values.extend(initiatives)
        result: list[dict[str, Any]] = []
        for row in connection.execute(f"SELECT * FROM initiative_links WHERE {' AND '.join(clauses)} ORDER BY initiative_id,relationship,target_id", values).fetchall():
            item = _row(row)
            assert item is not None
            item["is_resolved"] = bool(item["is_resolved"])
            item["warnings"] = _load_json(str(item.pop("warnings_json")), label="link warnings")
            result.append(item)
        return result

    def _task_initiative_ids(self, connection: sqlite3.Connection, task_id: str) -> list[str]:
        """Return initiatives related to one task without crossing task scope.

        An initiative may be linked directly to a task or indirectly through a
        report set that canonically resolves to that task alone.  A shared,
        unresolved, or conflicting report set is deliberately not converted to
        task visibility: only an explicit matching task link may surface it.
        Timeline reads additionally filter ``task_id`` and therefore never
        pull another task's chronology through a shared initiative.
        """
        direct: dict[str, set[str]] = {}
        for row in connection.execute(
            "SELECT initiative_id,target_id FROM initiative_links WHERE project_hash=? AND relationship='task'",
            (self.project_hash,),
        ).fetchall():
            direct.setdefault(str(row["initiative_id"]), set()).add(str(row["target_id"]))
        visible = {initiative_id for initiative_id, targets in direct.items() if task_id in targets}
        report_tasks: dict[str, set[str]] = {}
        unresolved: set[str] = set()
        for row in connection.execute(
            "SELECT l.initiative_id,r.task_id FROM initiative_links l "
            "LEFT JOIN reports r ON r.report_id=l.target_id AND r.project_hash=l.project_hash "
            "WHERE l.project_hash=? AND l.relationship='report'",
            (self.project_hash,),
        ).fetchall():
            initiative_id = str(row["initiative_id"])
            if row["task_id"] is None:
                unresolved.add(initiative_id)
            else:
                report_tasks.setdefault(initiative_id, set()).add(str(row["task_id"]))
        for initiative_id, targets in report_tasks.items():
            if initiative_id in direct or initiative_id in unresolved:
                continue
            if targets == {task_id}:
                visible.add(initiative_id)
        return sorted(visible)

    @staticmethod
    def _warning_values(links: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [{"initiative_id": link["initiative_id"], "relationship": link["relationship"], "target_id": link["target_id"], "warning": warning} for link in links for warning in link.get("warnings", [])]

    @staticmethod
    def _sequence(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise V12StoreError("after_sequence is invalid", code="invalid_argument", details={"field": "after_sequence"})
        return value

    @staticmethod
    def _limit(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PAGE_LIMIT:
            raise V12StoreError("limit is invalid", code="invalid_argument", details={"field": "limit"})
        return value

    def _timeline_page(self, connection: sqlite3.Connection, *, after: int, limit: int, clause: str, values: Sequence[Any]) -> tuple[list[dict[str, Any]], int, bool]:
        rows = connection.execute(f"SELECT * FROM timeline WHERE sequence>? AND ({clause}) ORDER BY sequence LIMIT ?", [after, *values, limit + 1]).fetchall()
        has_more, rows = len(rows) > limit, rows[:limit]
        timeline = []
        for row in rows:
            item = _row(row)
            assert item is not None
            item["payload"] = _load_json(str(item.pop("payload_json")), label="timeline payload")
            timeline.append(item)
        return timeline, int(timeline[-1]["sequence"]) if timeline else after, has_more

    @staticmethod
    def _ids(timeline: Sequence[Mapping[str, Any]], field: str) -> list[str]:
        return sorted({str(item[field]) for item in timeline if isinstance(item.get(field), str) and item[field]})

    def _consumption_receipts(self, connection: sqlite3.Connection, *, task_id: str, sequences: Sequence[int]) -> list[dict[str, Any]]:
        if not sequences:
            return []
        query = "SELECT receipt_id,consumer_delegation_id,reader_kind,report_id,observed_content_digest,input_cursor,output_cursor,chunk_indexes_json,returned_content_bytes,has_more,created_sequence FROM report_consumption_receipts WHERE task_id=? AND created_sequence IN (%s) ORDER BY created_sequence,receipt_id" % ",".join("?" for _ in sequences)
        result = []
        for row in connection.execute(query, [task_id, *sequences]).fetchall():
            result.append({"receipt_id": int(row["receipt_id"]), "consumer_delegation_id": row["consumer_delegation_id"], "reader_kind": str(row["reader_kind"]), "report_id": str(row["report_id"]), "observed_content_digest": str(row["observed_content_digest"]), "input_cursor": row["input_cursor"], "output_cursor": row["output_cursor"], "chunk_indexes": _load_json(str(row["chunk_indexes_json"]), label="report receipt chunks"), "returned_content_bytes": int(row["returned_content_bytes"]), "has_more": bool(row["has_more"]), "created_sequence": int(row["created_sequence"])})
        return result

    def inspect_task(self, *, task_id: Any, after_sequence: Any, limit: Any = DEFAULT_PAGE_LIMIT) -> dict[str, Any]:
        anchor, after, page = self._task_identifier(task_id), self._sequence(after_sequence), self._limit(limit)
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, anchor)
            timeline, next_sequence, has_more = self._timeline_page(connection, after=after, limit=page, clause="task_id=?", values=[anchor])
            delegations = [self._compact_delegation(self._delegation(connection, item, task_id=anchor)) for item in self._ids(timeline, "delegation_id")]
            # Recovery is a read-only ledger projection.  It intentionally
            # says nothing about host lifecycle: the coordinator reconciles
            # the exact native name with the host, resumes/waits if present,
            # and may spawn only after absence is independently proven.
            continuation_rows = connection.execute(
                "SELECT delegation_id FROM delegations WHERE task_id=? AND project_hash=? ORDER BY created_sequence,delegation_id",
                (anchor, self.project_hash),
            ).fetchall()
            continuations = []
            for row in continuation_rows:
                delegation = self._delegation(connection, str(row["delegation_id"]), task_id=anchor)
                handoff_reports = [
                    self._compact_report(self._report(connection, str(report["report_id"]), task_id=anchor))
                    for report in connection.execute(
                        "SELECT report_id FROM reports WHERE delegation_id=? AND project_hash=? ORDER BY created_sequence,report_id",
                        (delegation["delegation_id"], self.project_hash),
                    ).fetchall()
                ]
                if any(item["assembly_state"] == "assembling" for item in handoff_reports):
                    handoff_state = "report_assembling"
                elif any(item["assembly_state"] == "finalized" and item.get("status") == "completed" for item in handoff_reports):
                    handoff_state = "report_finalized"
                elif any(item["assembly_state"] == "finalized" and item.get("status") in {"partial", "blocked", "failed"} for item in handoff_reports):
                    handoff_state = "explicit_handoff"
                else:
                    handoff_state = "report_required"
                continuations.append({
                    "delegation": self._compact_delegation(delegation),
                    "dispatch_state": "ledger_unknown",
                    "handoff_state": handoff_state,
                    "reports": handoff_reports,
                    "recovery_requirement": "finalized_report_or_explicit_handoff_or_parent_linked_replacement",
                    "continuation_sequence": int(delegation["created_sequence"]),
                })
            reports = [self._compact_report(self._report(connection, item, task_id=anchor)) for item in self._ids(timeline, "report_id")]
            decisions = [self._compact_decision(self._decision(connection, item, task_id=anchor)) for item in self._ids(timeline, "decision_id")]
            receipts = self._consumption_receipts(connection, task_id=anchor, sequences=[int(item["sequence"]) for item in timeline])
            return {"task": task, "delegations": delegations, "continuations": continuations, "reports": reports, "decisions": decisions, "consumption_receipts": receipts, "timeline": timeline, "next_sequence": next_sequence, "has_more": has_more}
        return self._read(read)

    def read_delegation(self, *, delegation_id: Any, after_sequence: Any, limit: Any = DEFAULT_PAGE_LIMIT, task_id: Any = None) -> dict[str, Any]:
        anchor, delegation_id = self._task_for_delegation(delegation_id, task_id)
        after, page = self._sequence(after_sequence), self._limit(limit)
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            self._task(connection, anchor)
            delegation = self._delegation(connection, delegation_id, task_id=anchor)
            timeline, next_sequence, has_more = self._timeline_page(connection, after=after, limit=page, clause="delegation_id=?", values=[delegation["delegation_id"]])
            reports = [self._compact_report(self._report(connection, item, task_id=anchor)) for item in self._ids(timeline, "report_id")]
            receipts = self._consumption_receipts(connection, task_id=anchor, sequences=[int(item["sequence"]) for item in timeline])
            return {"delegation": delegation, "worker_brief": self._worker_brief(connection, self._task(connection, anchor), delegation), "reports": reports, "consumption_receipts": receipts, "timeline": timeline, "next_sequence": next_sequence, "has_more": has_more}
        return self._read(read)

    def read_reports(self, *, report_ids: Any, sections: Any = None, cursor: Any = None, max_bytes: Any = REPORT_READ_MAX_BYTES, consumer_delegation_id: Any = None, reader_kind: Any = None, task_id: Any = None) -> dict[str, Any]:
        """Return bounded chunks and append structural evidence of that read.

        A worker handoff read names the exact consuming delegation.  A
        coordinator read is explicitly classified and cannot masquerade as
        downstream worker consumption.  Receipts contain only immutable IDs,
        digests, cursors, chunk indexes, and byte counts—never report bodies.
        """
        import base64

        requested = _identifier_list(report_ids, label="report_ids", maximum=MAX_REPORT_IDS, minimum=1, ordered=True)
        if sections is None:
            selected_sections: list[str] | None = None
        else:
            if not isinstance(sections, list) or not 1 <= len(sections) <= 32 or len(set(sections)) != len(sections):
                raise V12StoreError("sections are invalid", code="invalid_argument", details={"field": "sections"})
            if any(not isinstance(item, str) or not item or len(item) > REPORT_SECTION_MAX_LENGTH for item in sections):
                raise V12StoreError("sections are invalid", code="invalid_argument", details={"field": "sections"})
            selected_sections = list(sections)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 0 <= max_bytes <= REPORT_READ_MAX_BYTES:
            raise V12StoreError("max_bytes is invalid", code="invalid_argument", details={"field": "max_bytes"})
        kind = "coordinator" if reader_kind is None else _required_text(reader_kind, label="reader_kind", maximum=16).lower()
        if kind not in {"coordinator", "worker"}:
            raise V12StoreError("reader_kind is invalid", code="invalid_argument", details={"field": "reader_kind"})
        consumer = None if consumer_delegation_id is None else self._record_identifier(consumer_delegation_id, label="consumer_delegation_id")
        if (kind == "worker") != (consumer is not None):
            raise V12StoreError("worker report reads require consumer_delegation_id", code="invalid_argument", details={"field": "consumer_delegation_id"})
        anchor = self._task_for_reports(requested, task_id, consumer)
        scope = _sha256_prefixed({"task_id": anchor, "report_ids": requested, "sections": selected_sections}, label="report read scope")

        def decode_cursor(value: Any) -> tuple[int, int, str | None]:
            if value is None:
                return 0, 0, None
            if not isinstance(value, str) or not value:
                raise V12StoreError("report cursor is invalid", code="report_cursor_invalid")
            try:
                padded = value + "=" * (-len(value) % 4)
                raw = base64.urlsafe_b64decode(padded.encode("ascii"))
                decoded = _load_json(raw.decode("utf-8"), label="report cursor")
                checksum = decoded.pop("checksum")
                if checksum != _sha256_prefixed(decoded, label="report cursor"):
                    raise ValueError()
                if decoded.get("scope") != scope:
                    raise V12StoreError("report cursor scope does not match", code="report_cursor_scope_mismatch")
                position, chunk_at, snapshot = decoded.get("report_position"), decoded.get("chunk_index"), decoded.get("snapshot_digest")
                if (
                    not isinstance(position, int)
                    or not isinstance(chunk_at, int)
                    or position < 0
                    or chunk_at < 0
                    or not isinstance(snapshot, str)
                    or DIGEST_RE.fullmatch(snapshot) is None
                ):
                    raise ValueError()
                return position, chunk_at, snapshot
            except V12StoreError:
                raise
            except (ValueError, UnicodeDecodeError, TypeError, json.JSONDecodeError):
                raise V12StoreError("report cursor is invalid", code="report_cursor_invalid") from None

        def encode_cursor(position: int, chunk_at: int, snapshot: str) -> str:
            value: dict[str, Any] = {"v": 2, "scope": scope, "report_position": position, "chunk_index": chunk_at, "snapshot_digest": snapshot}
            value["checksum"] = _sha256_prefixed(value, label="report cursor")
            return base64.urlsafe_b64encode(_canonical_json(value, label="report cursor").encode("utf-8")).decode("ascii").rstrip("=")

        start_report, start_chunk, expected_snapshot = decode_cursor(cursor)
        if start_report > len(requested):
            raise V12StoreError("report cursor is invalid", code="report_cursor_invalid")
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            self._task(connection, anchor)
            consuming = None if consumer is None else self._delegation(connection, consumer, task_id=anchor)
            if consuming is not None and any(item not in consuming["input_report_ids"] for item in requested):
                raise V12StoreError("worker read names a report outside its declared handoff", code="cross_project_reference")
            report_rows = [self._report(connection, item, task_id=anchor) for item in requested]
            if consuming is not None and any(report["assembly_state"] != "finalized" for report in report_rows):
                raise V12StoreError("worker handoff report is not finalized", code="report_state_conflict")
            snapshot = _sha256_prefixed(
                {
                    "task_id": anchor,
                    "reports": [
                        {
                            key: report[key]
                            for key in ("report_id", "assembly_state", "next_chunk_index", "total_chunks", "total_bytes", "content_digest")
                        }
                        for report in report_rows
                    ],
                },
                label="report read snapshot",
            )
            if expected_snapshot is not None and expected_snapshot != snapshot:
                raise V12StoreError(
                    "report cursor is stale",
                    code="report_cursor_stale",
                    details={"field": "cursor", "expected": "restart_without_cursor"},
                )
            result_reports = [self._compact_report(item) | {"chunks": []} for item in report_rows]

            def receipt_result(returned_bytes: int, next_cursor: str | None, more: bool) -> dict[str, Any]:
                receipts: list[dict[str, Any]] = []
                for report, compact in zip(report_rows, result_reports):
                    chunks = compact["chunks"]
                    indexes = [int(chunk["chunk_index"]) for chunk in chunks]
                    bytes_value = sum(int(chunk["content_bytes"]) for chunk in chunks)
                    sequence = self._timeline(
                        connection,
                        event_type="report_read",
                        entity_type="report_consumption",
                        entity_id=str(report["report_id"]),
                        payload={
                            "report_id": report["report_id"],
                            "consumer_delegation_id": None if consuming is None else consuming["delegation_id"],
                            "reader_kind": kind,
                            "observed_content_digest": report["content_digest"],
                            "read_scope_digest": scope,
                            "chunk_indexes": indexes,
                            "returned_content_bytes": bytes_value,
                            "has_more": more,
                        },
                        task_id=anchor,
                        delegation_id=None if consuming is None else consuming["delegation_id"],
                        report_id=str(report["report_id"]),
                    )
                    cursor_value = connection.execute(
                        "INSERT INTO report_consumption_receipts(project_hash,task_id,consumer_delegation_id,reader_kind,report_id,observed_content_digest,sections_json,input_cursor,output_cursor,chunk_indexes_json,returned_content_bytes,has_more,created_at,created_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (self.project_hash, anchor, None if consuming is None else consuming["delegation_id"], kind, report["report_id"], report["content_digest"], _canonical_json(selected_sections, label="report read sections"), cursor, next_cursor, _canonical_json(indexes, label="report receipt chunks"), bytes_value, int(more), _now(), sequence),
                    )
                    receipts.append({"receipt_id": int(cursor_value.lastrowid), "report_id": report["report_id"], "consumer_delegation_id": None if consuming is None else consuming["delegation_id"], "reader_kind": kind, "observed_content_digest": report["content_digest"], "chunk_indexes": indexes, "input_cursor": cursor, "output_cursor": next_cursor, "returned_content_bytes": bytes_value, "has_more": more, "created_sequence": sequence})
                return {"reports": result_reports, "returned_content_bytes": returned_bytes, "next_cursor": next_cursor, "has_more": more, "consumption_receipts": receipts}
            if max_bytes == 0:
                return receipt_result(0, None, False)
            returned = 0
            position, current_index, more = start_report, start_chunk, False
            while position < len(report_rows):
                report = report_rows[position]
                chunks = self._report_chunks(connection, str(report["report_id"]))
                for chunk in chunks:
                    index = int(chunk["chunk_index"])
                    if position == start_report and index < current_index:
                        continue
                    if selected_sections is not None and chunk["section"] not in selected_sections:
                        continue
                    size = int(chunk["content_bytes"])
                    if returned + size > max_bytes:
                        more = True
                        break
                    candidate = {key: chunk[key] for key in ("chunk_index", "section", "content", "content_digest", "content_bytes")}
                    # Leave room below the transport physical frame even if a
                    # caller asks for the full allowed content budget.
                    trial = {"reports": result_reports, "returned_content_bytes": returned + size, "next_cursor": None, "has_more": True}
                    result_reports[position]["chunks"].append(candidate)
                    if len(_canonical_json(trial, label="report response").encode("utf-8")) > REPORT_RESPONSE_MAX_BYTES:
                        result_reports[position]["chunks"].pop()
                        more = True
                        break
                    returned += size
                    current_index = index + 1
                if more:
                    break
                position += 1
                current_index = 0
            if more:
                next_cursor = encode_cursor(position, current_index, snapshot)
            else:
                next_cursor = None
            # Legacy convenience only for the complete, small, one-chunk
            # response; all larger content remains chunk-addressable.
            if not more and cursor is None and selected_sections is None:
                for report, compact in zip(report_rows, result_reports):
                    if report["assembly_state"] == "finalized" and int(report["total_chunks"]) == 1 and len(compact["chunks"]) == 1:
                        compact["content"] = compact["chunks"][0]["content"]
            return receipt_result(returned, next_cursor, more)
        result = self._write(read)
        self.materialize_human_views(anchor)
        return result

    def _projection(self, connection: sqlite3.Connection, *, task_id: str | None, initiative_id: str | None) -> dict[str, Any]:
        clauses, values = ["project_hash=?"], [self.project_hash]
        if initiative_id is not None:
            clauses.append("initiative_id=?")
            values.append(initiative_id)
        elif task_id is not None:
            clauses.append("task_id=?")
            values.append(task_id)
        assessments = [self._assessment(connection, str(row[0])) for row in connection.execute(f"SELECT assessment_id FROM governance_assessments WHERE {' AND '.join(clauses)} ORDER BY created_sequence DESC", values).fetchall()]
        model = next((item for item in assessments if item["source"] == "model"), None)
        override = next((item for item in assessments if item["source"] == "user_override"), None)
        closure_clauses, closure_values = ["project_hash=?"], [self.project_hash]
        if initiative_id is not None:
            closure_clauses.extend(["subject_type='initiative'", "subject_id=?"])
            closure_values.append(initiative_id)
        else:
            closure_clauses.extend(["subject_type='task'", "subject_id=?"])
            closure_values.append(task_id)
        row = connection.execute(f"SELECT closure_id FROM governance_closures WHERE {' AND '.join(closure_clauses)} ORDER BY created_sequence DESC LIMIT 1", closure_values).fetchone()
        effective = override or model
        return {"effective_mode": None if effective is None else effective["mode"], "effective_assessment": effective, "override_active": override is not None, "latest_user_override": override, "latest_model_assessment": model, "latest_closure": None if row is None else self._closure(connection, str(row[0]))}

    def _revisions(self, connection: sqlite3.Connection, initiatives: Sequence[str], sequences: Sequence[int]) -> list[dict[str, Any]]:
        if not initiatives or not sequences:
            return []
        query = "SELECT * FROM initiative_revisions WHERE project_hash=? AND initiative_id IN (%s) AND sequence IN (%s) ORDER BY sequence" % (",".join("?" for _ in initiatives), ",".join("?" for _ in sequences))
        result = []
        for row in connection.execute(query, [self.project_hash, *initiatives, *sequences]).fetchall():
            item = _row(row)
            assert item is not None
            item["payload"] = _load_json(str(item.pop("payload_json")), label="initiative revision")
            result.append(item)
        return result

    @staticmethod
    def _timeline_revision_sequences(timeline: Sequence[Mapping[str, Any]]) -> list[int]:
        """Include source sequences carried by derived chronology events."""
        values = {int(item["sequence"]) for item in timeline if isinstance(item.get("sequence"), int)}
        for item in timeline:
            payload = item.get("payload")
            if not isinstance(payload, Mapping):
                continue
            backfill = payload.get("backfill")
            if isinstance(backfill, Mapping) and isinstance(backfill.get("source_sequence"), int):
                values.add(int(backfill["source_sequence"]))
        return sorted(values)

    def inspect_governance(self, *, task_id: Any, initiative_id: Any, after_sequence: Any, limit: Any = DEFAULT_PAGE_LIMIT) -> dict[str, Any]:
        anchor, after, page = self._task_identifier(task_id), self._sequence(after_sequence), self._limit(limit)
        selected = None if initiative_id is None else self._record_identifier(initiative_id, label="initiative_id")
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            self._task(connection, anchor)
            if selected is not None:
                if selected not in self._task_initiative_ids(connection, anchor):
                    raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
                initiatives, clause, values = [self._initiative(connection, selected)["initiative_id"]], "task_id=? AND initiative_id=?", [anchor, selected]
                projection = self._projection(connection, task_id=None, initiative_id=selected)
            else:
                ids = set(self._task_initiative_ids(connection, anchor))
                ids.update(str(row[0]) for row in connection.execute("SELECT DISTINCT initiative_id FROM governance_assessments WHERE project_hash=? AND task_id=? AND initiative_id IS NOT NULL", (self.project_hash, anchor)).fetchall())
                initiatives = sorted(ids)
                clause, values = "task_id=?", [anchor]
                projection = self._projection(connection, task_id=anchor, initiative_id=None)
            current = [self._initiative(connection, item) for item in initiatives]
            links = self._initiative_links(connection, initiatives)
            timeline, next_sequence, has_more = self._timeline_page(connection, after=after, limit=page, clause=clause, values=values)
            assessments = [self._assessment(connection, item) for item in self._ids(timeline, "assessment_id")]
            closures = [self._closure(connection, item) for item in self._ids(timeline, "closure_id")]
            revisions = self._revisions(connection, initiatives, self._timeline_revision_sequences(timeline))
            return {"initiatives": current, "assessments": assessments, "closures": closures, "initiative_revisions": revisions, "links": links, "warnings": self._warning_values(links), "projection": projection, "timeline": timeline, "next_sequence": next_sequence, "has_more": has_more}
        return self._read(read)


__all__ = ["DATABASE_NAME", "SCHEMA_VERSION", "V12Store", "V12StoreError"]
