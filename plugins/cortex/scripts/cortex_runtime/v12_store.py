"""Private typed-schema storage for the Cortex V12 task-anchored ledger.

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
import errno
import fcntl
from contextvars import ContextVar
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from cortex_runtime.model_routing import validate_model_selection
from cortex_runtime.v12_contract import (
    CLOSURE_VERDICTS, DECISION_ATTRIBUTION, DECISION_SUBJECTS,
    DECISION_TYPES, DEFAULT_PAGE_LIMIT, DIGEST_RE, GOVERNANCE_MODES,
    IDEMPOTENCY_KEY_MAX_LENGTH, IDENTIFIER_RE,
    JSON_MAX_BYTES, JSON_MAX_DEPTH, LANGUAGE_TAG_MAX_LENGTH,
    MUTATION_RESULT_MAX_BYTES,
    LANGUAGE_TAG_RE,
    MAX_DECISION_IDS, MAX_LINKS,
    MAX_PAGE_LIMIT, MAX_REPORT_IDS, PROJECT_ROOT_MAX_LENGTH, REPORT_STATUSES,
    PLAN_REVIEW_POLICIES, REPORT_ASSEMBLING_MAX_BYTES_PER_TASK,
    REPORT_ASSEMBLING_MAX_PER_TASK, REPORT_ASSEMBLY_STATES, REPORT_CHUNK_MAX_BYTES,
    REPORT_MAX_BYTES, REPORT_MAX_CHUNKS, REPORT_MODES, REPORT_READ_MAX_BYTES,
    REPORT_RESPONSE_MAX_BYTES, REPORT_RETAINED_MAX_BYTES_PER_TASK,
    REPORT_SECTION_MAX_LENGTH, REPORT_SECTION_RE, ROLE_MAX_LENGTH, TASK_CONTRACT_ITEM_MAX_LENGTH,
    TASK_CONTRACT_MAX_ITEMS, TASK_CONTRACT_VERSION, TEXT_MAX_LENGTH, PROJECTION_RENDERER_VERSION, new_sharded_id,
    new_task_id, record_ref, record_ref_parts, record_shard_hash, task_ref, task_ref_parts, task_shard_hash,
)

SCHEMA_VERSION = 2
DATABASE_NAME = "cortex.db"
# Cross-shard compact record references are resolved through this private,
# host-local index.  The ledger rows remain authoritative: the index only
# maps a typed suffix to the shard that must be verified before use.
_RECORD_LOCATOR_DATABASE_NAME = "record-locators.db"
_RECORD_LOCATOR_SCHEMA_VERSION = 1
# Compact task references cannot safely select a project shard by themselves.
# This private, derived index is the one routing accelerator for them.  The
# per-shard publication row and the task ledger row remain the authority.
_TASK_LOCATOR_DATABASE_NAME = "task-locators.db"
_TASK_LOCATOR_SCHEMA_VERSION = 1
MIGRATION_NAME = "typed-orchestration-integrity"
_DISPATCH_LEASE_SECONDS = 300
# Admission covers descriptor locking, WAL negotiation, schema readiness,
# canonical transaction work, and reconstructible locator convergence.  A
# sub-second budget is too small on loaded CI hosts and can surface a false
# terminal ``storage_busy`` even though the identical concurrent command is
# already converging. Keep the wait bounded, but long enough for the maximum
# supported local worker fan-out to serialize safely.
_STORAGE_ADMISSION_BUDGET_SECONDS = 5.0
_ADMISSION_DEADLINE: ContextVar[float | None] = ContextVar("cortex_v12_admission_deadline", default=None)
_SQLITE_ADMISSION_LOCKS: dict[str, threading.RLock] = {}
_SQLITE_ADMISSION_LOCKS_GUARD = threading.RLock()
_SQLITE_ADMISSION_LOCKS_PID = os.getpid()
_APPLICATION_ID = 0x43563132
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
    # ``SQLITE_PROTOCOL`` is SQLite's documented WAL locking-protocol race.
    # It is transient only when reported by the driver numeric code, never by
    # matching human exception wording.  It therefore shares the same bounded
    # admission/retry path as BUSY and LOCKED.
    busy_codes = {
        getattr(sqlite3, "SQLITE_BUSY", -1),
        getattr(sqlite3, "SQLITE_LOCKED", -1),
        getattr(sqlite3, "SQLITE_PROTOCOL", -1),
    }
    if primary_code in busy_codes:
        return V12StoreError(
            "V12 storage is busy",
            code="storage_busy",
            details={"retry_after_ms": 100, "retry_with": "same_idempotency_key"},
        )
    return V12StoreError("V12 storage is unavailable", code="storage_unavailable")

def _with_admission_budget(call: Callable[[], T]) -> T:
    """Apply one inherited monotonic deadline to nested storage admission."""
    inherited = _ADMISSION_DEADLINE.get()
    deadline = inherited if inherited is not None else time.monotonic() + _STORAGE_ADMISSION_BUDGET_SECONDS
    token = _ADMISSION_DEADLINE.set(deadline) if inherited is None else None
    delay = 0.01
    try:
        while True:
            try:
                return call()
            except V12StoreError as exc:
                remaining = deadline - time.monotonic()
                if exc.code != "storage_busy" or remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.08)
    finally:
        if token is not None:
            _ADMISSION_DEADLINE.reset(token)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _depth(value: Any) -> int:
    if isinstance(value, Mapping):
        return 1 + max((_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(item) for item in value), default=0)
    return 0

def _strict_json(value: Any, *, label: str, maximum_bytes: int = JSON_MAX_BYTES) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > maximum_bytes or _depth(value) > JSON_MAX_DEPTH:
            raise ValueError("bounded JSON violation")
        return json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise V12StoreError(f"{label} is invalid", code="content_invalid", details={"field": label}) from exc

def _canonical_json(value: Any, *, label: str, maximum_bytes: int = JSON_MAX_BYTES) -> str:
    return json.dumps(
        _strict_json(value, label=label, maximum_bytes=maximum_bytes),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

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

def _contract_optional_text_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > TASK_CONTRACT_MAX_ITEMS:
        raise V12StoreError(f"{label} is invalid", code="invalid_argument", details={"field": label})
    return [
        _opaque_text(item, label=label, maximum=TASK_CONTRACT_ITEM_MAX_LENGTH)
        for item in value
    ]

def _linked_outcome_contracts(
    value: Any, *, requirements: list[str],
) -> list[dict[str, Any]]:
    """Normalize outcome identity separately from linked acceptance evidence."""
    if not isinstance(value, list) or len(value) != len(requirements) or not value:
        raise V12StoreError("outcome_contracts is invalid", code="invalid_argument", details={"field": "outcome_contracts"})
    result: list[dict[str, Any]] = []
    for ordinal, outcome in enumerate(value):
        if not isinstance(outcome, Mapping) or set(outcome) - {"requirement", "acceptance", "verification", "constraints"}:
            raise V12StoreError("outcome_contracts is invalid", code="invalid_argument", details={"field": "outcome_contracts"})
        requirement = _opaque_text(outcome.get("requirement"), label="outcome_contracts", maximum=TASK_CONTRACT_ITEM_MAX_LENGTH)
        acceptance = _contract_optional_text_list(outcome.get("acceptance"), label="outcome_contracts.acceptance")
        raw_verification = outcome.get("verification", [])
        if not isinstance(raw_verification, list) or len(raw_verification) > TASK_CONTRACT_MAX_ITEMS:
            raise V12StoreError("outcome_contracts is invalid", code="invalid_argument", details={"field": "outcome_contracts"})
        verification = [
            _opaque_text(item, label="outcome_contracts.verification", maximum=TASK_CONTRACT_ITEM_MAX_LENGTH)
            for item in raw_verification
        ]
        if requirement != requirements[ordinal]:
            raise V12StoreError("outcome_contracts disagrees with requirements", code="invalid_argument", details={"field": "outcome_contracts"})
        constraints = _contract_optional_text_list(outcome.get("constraints", []), label="outcome_contracts.constraints")
        result.append({"requirement": requirement, "acceptance": acceptance, "verification": verification, "constraints": constraints})
    return result

def _initial_outcome_details(outcome: Mapping[str, Any], ordinal: int) -> dict[str, Any]:
    acceptance = list(outcome.get("acceptance", []))
    verification = list(outcome.get("verification", []))
    return {
        "acceptance_criteria": acceptance,
        "verification_criteria": verification,
        "constraints": list(outcome.get("constraints", [])),
    }

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

def _codex_home() -> Path:
    """Return the one state root used by storage and compact-ref resolution."""
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get("HOME") or str(Path.home())).expanduser() / ".codex"

class V12Store:
    """One private V12 SQLite shard; only the current typed schema is admitted."""

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
        self._contention_deadline: float | None = None
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
        store._contention_deadline = None
        store._set_paths()
        store._verify_known_task(identifier)
        return store

    @classmethod
    def for_task_ref(cls, value: object) -> tuple["V12Store", str]:
        return _with_admission_budget(lambda: cls._for_task_ref_once(value))

    @classmethod
    def _for_task_ref_once(cls, value: object) -> tuple["V12Store", str]:
        """Resolve one compact task ref through the non-authoritative index.

        Normal resolution opens exactly the indexed shard and proves the
        compact suffix, full-ID fingerprint, project hash, and canonical task
        row agree.  A missing, stale, malformed, or contended sidecar takes
        the explicit bounded recovery route below; it is never authority and
        never permits a guessed cross-project target.
        """
        task_suffix = task_ref_parts(value)
        if task_suffix is None:
            raise V12StoreError("task_ref is invalid", code="invalid_identifier", details={"field": "task_ref"})
        indexed = cls._task_locator_matches(task_suffix)
        if indexed is not None:
            if len(indexed) != 1:
                raise V12StoreError("task_ref is ambiguous", code="task_ref_ambiguous", details={"field": "task_ref"})
            shard, identifier, fingerprint = indexed[0]
            store = cls._store_for_shard(shard)
            try:
                if cls._task_locator_fingerprint(identifier) != fingerprint:
                    raise V12StoreError("task locator is stale", code="task_not_found")
                store._verify_known_task(identifier)
                if task_ref(identifier) is None or not identifier.endswith(task_suffix):
                    raise V12StoreError("task locator is stale", code="task_not_found")
            except V12StoreError as exc:
                if exc.code not in {"task_not_found", "cross_project_reference"}:
                    raise
            else:
                store._contention_deadline = _ADMISSION_DEADLINE.get()
                return store, identifier
        matches = cls._recover_task_locator_matches(task_suffix)
        if len(matches) == 1:
            store, identifier = matches[0]
            # A verified canonical recovery repairs only derived state.  A
            # repair failure cannot revoke the successful canonical lookup.
            store._repair_task_locator_entry_best_effort(identifier)
            store._contention_deadline = _ADMISSION_DEADLINE.get()
            return store, identifier
        if len(matches) == 0:
            raise V12StoreError("task was not found", code="task_not_found")
        raise V12StoreError("task_ref is ambiguous", code="task_ref_ambiguous", details={"field": "task_ref"})

    @classmethod
    def _recover_task_locator_matches(cls, task_suffix: str) -> list[tuple["V12Store", str]]:
        """Explicit bounded canonical recovery for an unusable task index."""
        projects = _codex_home() / "cortex" / "v12" / "projects"
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
            store._contention_deadline = None
            store._set_paths()
            try:
                task_id = store._open_shard_for_task_ref(task_suffix)
            except V12StoreError as exc:
                if exc.code == "task_not_found":
                    continue
                raise
            matches.append((store, task_id))
        return matches

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
        store._contention_deadline = None
        store._set_paths()
        store._verify_known_record(identifier, label=label)
        return store

    @classmethod
    def for_record_ref(cls, value: object, *, label: str) -> tuple["V12Store", str]:
        return _with_admission_budget(lambda: cls._for_record_ref_once(value, label=label))

    @classmethod
    def _for_record_ref_once(cls, value: object, *, label: str) -> tuple["V12Store", str]:
        """Resolve a compact record reference through the private locator index.

        New writes populate the indexed resolver.  A missing index entry is
        treated as a current-schema recovery path: it performs a bounded
        exact, fail-closed shard scan once and repairs the derived index.  A
        collision remains ambiguous; recovery never guesses a target.
        """
        suffix = record_ref_parts(value, label=label)
        if suffix is None:
            raise V12StoreError(f"{label} is invalid", code="invalid_identifier", details={"field": label})
        indexed = cls._record_locator_matches(suffix, label=label)
        if indexed is not None:
            if len(indexed) != 1:
                raise V12StoreError(f"{label} is ambiguous", code="record_ref_ambiguous", details={"field": label})
            shard, identifier = indexed[0]
            store = cls._store_for_shard(shard)
            try:
                store._verify_known_record(identifier, label=label)
            except V12StoreError as exc:
                if exc.code not in {f"{label.removesuffix('_id')}_not_found", "cross_project_reference"}:
                    raise
            else:
                store._contention_deadline = _ADMISSION_DEADLINE.get()
                return store, identifier
            # A stale derived entry is not an authority.  Fall through to the
            # one canonical recovery scan, which verifies the canonical shards.
        matches = cls._recover_record_locator_matches(suffix, label=label)
        if len(matches) == 1:
            store, identifier = matches[0]
            # The canonical shard scan above has already validated both the
            # record and its schema.  The locator is merely an accelerator for
            # later calls, so a bounded repair failure must not revoke this
            # canonical success or force a caller to invent a replacement
            # reference.
            store._repair_record_locators_best_effort()
            store._contention_deadline = _ADMISSION_DEADLINE.get()
            return store, identifier
        if len(matches) == 0:
            raise V12StoreError(f"{label} was not found", code=f"{label.removesuffix('_id')}_not_found")
        raise V12StoreError(f"{label} is ambiguous", code="record_ref_ambiguous", details={"field": label})

    @classmethod
    def _store_for_shard(cls, shard: str) -> "V12Store":
        store = cls.__new__(cls)
        store.project_root = None
        store.project_hash = shard
        store._guard = threading.RLock()
        store._contention_deadline = None
        store._set_paths()
        return store

    @classmethod
    def _recover_record_locator_matches(cls, suffix: str, *, label: str) -> list[tuple["V12Store", str]]:
        """One bounded current-schema rebuild of a damaged derived locator index."""
        projects = _codex_home() / "cortex" / "v12" / "projects"
        try:
            shards = [entry.name[2:] for entry in os.scandir(projects) if entry.name.startswith("p-") and re.fullmatch(r"p-[0-9a-f]{64}", entry.name) and entry.is_dir(follow_symlinks=False)]
        except FileNotFoundError:
            shards = []
        except OSError as exc:
            raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc
        matches: list[tuple[V12Store, str]] = []
        for shard in shards:
            store = cls._store_for_shard(shard)
            try:
                identifier = store._open_shard_for_record_ref(suffix, label=label)
            except V12StoreError as exc:
                if exc.code in {f"{label.removesuffix('_id')}_not_found", "delegation_not_found", "report_not_found", "decision_not_found"}:
                    continue
                raise
            matches.append((store, identifier))
        return matches

    def _set_paths(self) -> None:
        self._codex_home = _codex_home()
        self._v12_root = self._codex_home / "cortex" / "v12"
        self._record_locator_path = self._v12_root / _RECORD_LOCATOR_DATABASE_NAME
        self._task_locator_path = self._v12_root / _TASK_LOCATOR_DATABASE_NAME
        self.root = self._codex_home / "cortex" / "v12" / "projects" / f"p-{self.project_hash}"
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

    def _open_shard_for_task_ref(self, suffix: str) -> str:
        """Resolve, readiness-check, and anchor one compact task under one gate."""
        def admit() -> str:
            identifier = self._task_id_for_ref_suffix(suffix)
            self._verify_known_task(identifier)
            return identifier
        return self._with_storage_admission(admit)

    def _record_identifier(self, value: Any, *, label: str) -> str:
        identifier = _identifier(value, label=label)
        shard = record_shard_hash(identifier)
        if shard is not None and shard != self.project_hash:
            raise V12StoreError("reference belongs to another project", code="cross_project_reference")
        return identifier

    def _record_id_for_ref_suffix(self, suffix: str, *, label: str) -> str:
        table = {"delegation_id": ("delegations", "delegation_id"), "report_id": ("reports", "report_id"), "decision_id": ("user_decisions", "decision_id")}.get(label)
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

    def _open_shard_for_record_ref(self, suffix: str, *, label: str) -> str:
        """Resolve and schema-ready one compact record under one admission gate."""
        def admit() -> str:
            identifier = self._record_id_for_ref_suffix(suffix, label=label)
            self._verify_known_record(identifier, label=label)
            return identifier
        return self._with_storage_admission(admit)

    def resolve_record_ref(self, value: Any, *, label: str) -> str:
        """Resolve one canonical public ref within this known shard."""
        suffix = record_ref_parts(value, label=label)
        if suffix is None:
            raise V12StoreError(f"{label} is invalid", code="invalid_identifier", details={"field": label})
        return self._record_id_for_ref_suffix(suffix, label=label)

    def resolve_task_reference(
        self,
        *,
        task_id: Any,
        value: Any,
        kinds: tuple[str, ...],
    ) -> tuple[str, str]:
        """Resolve one compact public locator for a known task.

        This is the sole compact-to-canonical resolver used by semantic
        adapters.  It both identifies the durable type and verifies that the
        resolved record belongs to ``task_id`` in this project shard.  Callers
        receive only a canonical identifier and must never parse locators.
        """
        anchor = self._task_identifier(task_id)
        permitted = tuple(dict.fromkeys(kinds))
        labels = {
            "assignment": "delegation_id",
            "report": "report_id",
            "plan": "report_id",
            "decision": "decision_id",
        }
        if not permitted or any(kind not in {*labels, "task", "subject"} for kind in permitted):
            raise V12StoreError("reference type is invalid", code="invalid_identifier")

        def resolve(connection: sqlite3.Connection) -> tuple[str, str]:
            matches: list[tuple[str, str]] = []
            subject = "subject" in permitted
            if "task" in permitted or subject:
                suffix = task_ref_parts(value)
                if suffix is not None:
                    identifier = self._task_id_for_ref_suffix(suffix)
                    if identifier == anchor:
                        matches.append(("task", identifier))
                    else:
                        raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
            for kind, label in labels.items():
                if kind not in permitted and not (subject and kind in {"assignment", "report", "decision"}):
                    continue
                if subject and kind == "plan":
                    continue
                suffix = record_ref_parts(value, label=label)
                if suffix is None:
                    continue
                try:
                    identifier = self._record_id_for_ref_suffix(suffix, label=label)
                except V12StoreError as exc:
                    if exc.code in {f"{label.removesuffix('_id')}_not_found", "record_ref_ambiguous"}:
                        continue
                    raise
                if kind == "assignment":
                    self._delegation(connection, identifier, task_id=anchor)
                elif kind in {"report", "plan"}:
                    report = self._report(connection, identifier, task_id=anchor)
                    if kind == "plan" and str(report["report_type"]) != "plan":
                        raise V12StoreError("reference is not a plan", code="invalid_decision_subject")
                    if subject:
                        kind = "plan" if str(report["report_type"]) == "plan" else "report"
                else:
                    self._decision(connection, identifier, task_id=anchor)
                matches.append((kind, identifier))
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise V12StoreError("reference type is ambiguous", code="record_ref_ambiguous")
            # A syntactically valid locator that resolves in another known
            # project must not be downgraded to a local not-found result.
            if task_ref_parts(value) is not None:
                raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
            for label in set(labels.values()):
                if record_ref_parts(value, label=label) is None:
                    continue
                try:
                    foreign_store, _ = type(self).for_record_ref(value, label=label)
                except V12StoreError as exc:
                    if exc.code in {f"{label.removesuffix('_id')}_not_found", "record_ref_ambiguous"}:
                        continue
                    raise
                if foreign_store.project_hash != self.project_hash:
                    raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
            raise V12StoreError("reference is invalid", code="invalid_identifier")

        return self._read(resolve)

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
            (self._codex_home, False), (self._codex_home / "cortex", False),
            (self._codex_home / "cortex" / "v12", True),
            (self._codex_home / "cortex" / "v12" / "projects", True), (self.root, True),
        ]
        for directory, normalize in components:
            try:
                directory.mkdir(mode=0o700, exist_ok=True)
            except OSError as exc:
                raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc
            self._directory(directory, normalize=normalize)

    @classmethod
    def _record_locator_matches(cls, suffix: str, *, label: str) -> list[tuple[str, str]] | None:
        """Read at most two verified candidates without enumerating shards.

        ``None`` means no locator database exists yet, so only the explicit
        canonical recovery path may scan project shards.  An empty list is a
        valid indexed miss and follows the same one-time recovery path.
        """
        root = _codex_home() / "cortex" / "v12"
        path = root / _RECORD_LOCATOR_DATABASE_NAME
        if not root.exists():
            return None
        # The V12 root is canonical storage required by the fallback scan as
        # well.  Do not hide an unsafe root behind the sidecar policy.
        cls._directory(root, normalize=True)
        try:
            cls._regular(path, required=False)
            if not path.exists():
                return None
            deadline = _ADMISSION_DEADLINE.get() or (time.monotonic() + _STORAGE_ADMISSION_BUDGET_SECONDS)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise V12StoreError("V12 storage is busy", code="storage_busy")
            # This is an accelerator probe, not a canonical read.  Do not
            # spend the public admission budget waiting on its independent
            # writer; a busy probe immediately yields to canonical scanning,
            # which receives the same inherited deadline.
            with sqlite3.connect(path, timeout=0, isolation_level=None) as connection:
                connection.execute("PRAGMA busy_timeout = 1")
                if not cls._record_locator_schema_current(connection):
                    return None
                rows = connection.execute(
                    "SELECT project_hash,record_id FROM record_locators WHERE label=? AND suffix=? ORDER BY record_id LIMIT 2",
                    (label, suffix),
                ).fetchall()
        except V12StoreError:
            # The sidecar is derived and reconstructible.  Its path, schema,
            # lock, or read failure is never evidence that canonical shards
            # are unavailable; the bounded exact shard scan below remains the
            # authority.
            return None
        except (OSError, sqlite3.DatabaseError) as exc:
            # Do not expose a malformed/unreadable accelerator as a canonical
            # storage outage.  The fallback verifies the actual ledger row.
            return None
        return [(str(row[0]), str(row[1])) for row in rows]

    @staticmethod
    def _record_locator_schema_current(connection: sqlite3.Connection) -> bool:
        """Return whether one derived locator database has its exact schema."""
        try:
            version = connection.execute("PRAGMA user_version").fetchone()
            columns = tuple(
                str(row[1]) for row in connection.execute("PRAGMA table_info(record_locators)")
            )
        except sqlite3.DatabaseError:
            return False
        return (
            version is not None
            and int(version[0]) == _RECORD_LOCATOR_SCHEMA_VERSION
            and columns == ("label", "suffix", "project_hash", "record_id")
        )

    @staticmethod
    def _task_locator_fingerprint(task_id: str) -> str:
        """Return private evidence tying an index row to one canonical ID."""
        return hashlib.sha256(task_id.encode("utf-8")).hexdigest()

    @classmethod
    def _task_locator_matches(cls, suffix: str) -> list[tuple[str, str, str]] | None:
        """Probe the derived task locator without enumerating project shards."""
        root = _codex_home() / "cortex" / "v12"
        path = root / _TASK_LOCATOR_DATABASE_NAME
        if not root.exists():
            return None
        cls._directory(root, normalize=True)
        try:
            cls._regular(path, required=False)
            if not path.exists():
                return None
            with sqlite3.connect(path, timeout=0, isolation_level=None) as connection:
                connection.execute("PRAGMA busy_timeout = 1")
                if not cls._task_locator_schema_current(connection):
                    return None
                rows = connection.execute(
                    "SELECT project_hash,task_id,fingerprint FROM task_locators WHERE suffix=? ORDER BY task_id LIMIT 2",
                    (suffix,),
                ).fetchall()
        except (OSError, sqlite3.DatabaseError, V12StoreError):
            # A sidecar is reconstructible and cannot be canonical evidence.
            return None
        return [(str(row[0]), str(row[1]), str(row[2])) for row in rows]

    @staticmethod
    def _task_locator_schema_current(connection: sqlite3.Connection) -> bool:
        try:
            version = connection.execute("PRAGMA user_version").fetchone()
            columns = tuple(str(row[1]) for row in connection.execute("PRAGMA table_info(task_locators)"))
        except sqlite3.DatabaseError:
            return False
        return version is not None and int(version[0]) == _TASK_LOCATOR_SCHEMA_VERSION and columns == (
            "suffix", "fingerprint", "project_hash", "task_id",
        )

    def _canonical_task_locator_rows(self) -> list[tuple[str, str, str]]:
        """Read only canonical publication rows from this already known shard."""
        with self._connection() as ledger:
            rows = ledger.execute(
                "SELECT publication.task_id,publication.suffix,publication.fingerprint "
                "FROM task_locator_publications AS publication JOIN tasks ON tasks.task_id=publication.task_id "
                "WHERE publication.project_hash=? AND tasks.project_hash=? ORDER BY publication.task_id",
                (self.project_hash, self.project_hash),
            ).fetchall()
        result: list[tuple[str, str, str]] = []
        for row in rows:
            identifier, suffix, fingerprint = str(row[0]), str(row[1]), str(row[2])
            if task_ref(identifier) is None or not identifier.endswith(suffix) or fingerprint != self._task_locator_fingerprint(identifier):
                raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
            result.append((identifier, suffix, fingerprint))
        return result

    def _write_task_locator_rows(self, path: Path, rows: Sequence[tuple[str, str, str]]) -> None:
        deadline = self._contention_deadline or _ADMISSION_DEADLINE.get() or (time.monotonic() + _STORAGE_ADMISSION_BUDGET_SECONDS)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise V12StoreError("V12 storage is busy", code="storage_busy")
        with sqlite3.connect(path, timeout=remaining, isolation_level=None) as locator:
            locator.execute(f"PRAGMA busy_timeout = {max(1, int(remaining * 1000))}")
            locator.execute("BEGIN IMMEDIATE")
            exists = locator.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_locators'").fetchone() is not None
            if exists and not self._task_locator_schema_current(locator):
                raise V12StoreError("task locator sidecar is invalid", code="storage_unavailable")
            locator.execute("CREATE TABLE IF NOT EXISTS task_locators(suffix TEXT NOT NULL,fingerprint TEXT NOT NULL,project_hash TEXT NOT NULL,task_id TEXT NOT NULL,PRIMARY KEY(suffix,task_id))")
            locator.execute("CREATE INDEX IF NOT EXISTS task_locators_lookup ON task_locators(suffix,task_id)")
            locator.execute(f"PRAGMA user_version = {_TASK_LOCATOR_SCHEMA_VERSION}")
            locator.execute("DELETE FROM task_locators WHERE project_hash=?", (self.project_hash,))
            locator.executemany(
                "INSERT INTO task_locators(suffix,fingerprint,project_hash,task_id) VALUES (?, ?, ?, ?)",
                [(suffix, fingerprint, self.project_hash, identifier) for identifier, suffix, fingerprint in rows],
            )
            locator.execute("COMMIT")
        os.chmod(path, 0o600)

    def _replace_task_locator_sidecar(self, rows: Sequence[tuple[str, str, str]]) -> None:
        temporary = self._task_locator_path.with_name(f".{_TASK_LOCATOR_DATABASE_NAME}.{uuid.uuid4().hex}.rebuild")
        descriptor = -1
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            os.close(descriptor)
            descriptor = -1
            self._write_task_locator_rows(temporary, rows)
            self._regular(temporary, required=True)
            os.replace(temporary, self._task_locator_path)
            self._regular(self._task_locator_path, required=True)
            os.chmod(self._task_locator_path, 0o600)
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass

    def _sync_task_locators_once(self) -> None:
        rows = self._canonical_task_locator_rows()
        self._directory(self._v12_root, normalize=True)
        try:
            self._regular(self._task_locator_path, required=False)
            self._write_task_locator_rows(self._task_locator_path, rows)
        except V12StoreError as exc:
            if exc.code == "storage_busy":
                raise
            self._replace_task_locator_sidecar(rows)
        except (OSError, sqlite3.DatabaseError) as exc:
            classified = _storage_error(exc)
            if classified.code == "storage_busy":
                raise classified from exc
            self._replace_task_locator_sidecar(rows)

    def _sync_task_locators(self) -> None:
        return self._with_storage_admission(self._sync_task_locators_once)

    def _repair_task_locators_best_effort(self) -> None:
        try:
            self._sync_task_locators()
        except V12StoreError as exc:
            if exc.code in {"ledger_corrupt", "schema_unsupported"}:
                raise

    def _refresh_task_locators_after_commit(self) -> None:
        """Publish committed task-route evidence after its canonical commit.

        Lock order is target shard -> derived sidecar.  Creation commits the
        task and its publication in the same shard transaction first; a crash
        before this best-effort publication leaves only a recoverable locator
        miss, never a locator-only task.
        """
        self._repair_task_locators_best_effort()

    def _repair_task_locator_entry_best_effort(self, identifier: str) -> None:
        """Replace one recovered suffix mapping without trusting sidecar rows."""
        suffix = identifier[-12:]
        fingerprint = self._task_locator_fingerprint(identifier)
        invalid = False
        try:
            deadline = self._admission_deadline()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            with sqlite3.connect(self._task_locator_path, timeout=remaining, isolation_level=None) as locator:
                locator.execute(f"PRAGMA busy_timeout = {max(1, int(remaining * 1000))}")
                if not self._task_locator_schema_current(locator):
                    invalid = True
                else:
                    locator.execute("BEGIN IMMEDIATE")
                    # The recovery scan has proven this suffix has exactly one
                    # canonical match.  Remove every non-authoritative claim to
                    # it before publishing that exact verified relation.
                    locator.execute("DELETE FROM task_locators WHERE suffix=?", (suffix,))
                    locator.execute("INSERT INTO task_locators(suffix,fingerprint,project_hash,task_id) VALUES (?, ?, ?, ?)", (suffix, fingerprint, self.project_hash, identifier))
                    locator.execute("COMMIT")
        except (OSError, sqlite3.DatabaseError, V12StoreError):
            # Canonical recovery already succeeded; retryable sidecar repair
            # is intentionally non-authoritative.
            return
        if invalid:
            self._repair_task_locators_best_effort()

    def _sync_record_locators(self) -> None:
        """Rebuild this shard's derived locator rows without cross-shard reads."""
        return self._with_storage_admission(self._sync_record_locators_once)

    def _repair_record_locators_best_effort(self) -> None:
        """Attempt bounded derived-index repair without changing canonical truth.

        This is the only policy boundary for locator repair after canonical
        readiness, a successful command commit, or an exact fallback scan.
        The sidecar may be missing, malformed, unreadable, or contended; none
        of those states may turn a verified canonical result into an error.
        Canonical schema and corruption failures are still raised *before*
        this helper is reached by their normal shard verification paths.
        """
        try:
            self._sync_record_locators()
        except V12StoreError as exc:
            # Canonical corruption/schema compatibility is never downgraded
            # merely because this call also repairs derived state.  Every
            # other sidecar outcome is non-authoritative after canonical
            # readiness and may be reconciled by a later open.
            if exc.code in {"ledger_corrupt", "schema_unsupported"}:
                raise
            return

    def _refresh_record_locators_after_commit(self) -> None:
        """Best-effort repair of the reconstructible locator index.

        ``record-locators.db`` is a derived accelerator, never canonical
        receipt/command state: compact lookup verifies ledger rows and falls
        back to the fail-closed shard scan when the index is stale or absent.
        Therefore a refresh failure after a canonical commit must not turn a
        successful mutation into a misleading public failure or replay it.
        The next normal shard open attempts idempotent reconciliation.
        """
        self._repair_record_locators_best_effort()

    def _canonical_locator_records(self) -> list[tuple[str, str]]:
        """Read this shard's canonical records before touching derived state."""
        sources = {
            "delegation_id": ("delegations", "delegation_id"),
            "report_id": ("reports", "report_id"),
            "decision_id": ("user_decisions", "decision_id"),
        }
        with self._connection() as ledger:
            return [
                (label, str(row[0]))
                for label, (table, column) in sources.items()
                for row in ledger.execute(
                    f"SELECT {column} FROM {table} WHERE project_hash=?", (self.project_hash,)
                ).fetchall()
            ]

    def _write_record_locator_rows(self, path: Path, records: Sequence[tuple[str, str]]) -> None:
        """Write one exact derived sidecar image within the inherited budget."""
        deadline = self._contention_deadline or _ADMISSION_DEADLINE.get() or (
            time.monotonic() + _STORAGE_ADMISSION_BUDGET_SECONDS
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise V12StoreError("V12 storage is busy", code="storage_busy")
        with sqlite3.connect(path, timeout=remaining, isolation_level=None) as locator:
            locator.execute(f"PRAGMA busy_timeout = {max(1, int(remaining * 1000))}")
            locator.execute("BEGIN IMMEDIATE")
            has_table = locator.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='record_locators'"
            ).fetchone() is not None
            if has_table and not self._record_locator_schema_current(locator):
                raise V12StoreError("record locator sidecar is invalid", code="storage_unavailable")
            locator.execute(
                "CREATE TABLE IF NOT EXISTS record_locators(label TEXT NOT NULL,suffix TEXT NOT NULL,project_hash TEXT NOT NULL,record_id TEXT NOT NULL,PRIMARY KEY(label,record_id))"
            )
            locator.execute(
                "CREATE INDEX IF NOT EXISTS record_locators_lookup ON record_locators(label,suffix,record_id)"
            )
            locator.execute(f"PRAGMA user_version = {_RECORD_LOCATOR_SCHEMA_VERSION}")
            locator.execute("DELETE FROM record_locators WHERE project_hash=?", (self.project_hash,))
            locator.executemany(
                "INSERT INTO record_locators(label,suffix,project_hash,record_id) VALUES (?, ?, ?, ?)",
                [
                    (label, identifier.rsplit("-", 1)[-1][-12:], self.project_hash, identifier)
                    for label, identifier in records
                ],
            )
            locator.execute("COMMIT")
        os.chmod(path, 0o600)

    def _replace_record_locator_sidecar(self, records: Sequence[tuple[str, str]]) -> None:
        """Atomically replace only an invalid derived sidecar from canonical rows."""
        temporary = self._record_locator_path.with_name(
            f".{_RECORD_LOCATOR_DATABASE_NAME}.{uuid.uuid4().hex}.rebuild"
        )
        descriptor = -1
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            os.close(descriptor)
            descriptor = -1
            self._write_record_locator_rows(temporary, records)
            self._regular(temporary, required=True)
            os.replace(temporary, self._record_locator_path)
            self._regular(self._record_locator_path, required=True)
            os.chmod(self._record_locator_path, 0o600)
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass

    def _sync_record_locators_once(self) -> None:
        """Refresh locator rows inside the inherited shard-admission context."""
        records = self._canonical_locator_records()
        # This parent directory is also required for canonical shards.  A
        # defect here is not a sidecar failure and must remain fail-closed.
        self._directory(self._v12_root, normalize=True)
        try:
            self._regular(self._record_locator_path, required=False)
            self._write_record_locator_rows(self._record_locator_path, records)
        except V12StoreError as exc:
            # A busy valid sidecar must remain within normal admission retry.
            # A malformed/unreadable derived image is replaced atomically from
            # the already-read canonical shard rows.
            if exc.code == "storage_busy":
                raise
            try:
                self._replace_record_locator_sidecar(records)
            except V12StoreError:
                raise
        except (OSError, sqlite3.DatabaseError) as exc:
            classified = _storage_error(exc)
            if classified.code == "storage_busy":
                raise classified from exc
            self._replace_record_locator_sidecar(records)

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

    def _admission_deadline(self) -> float:
        """Return the one inherited deadline for storage and sidecar work."""
        return self._contention_deadline or _ADMISSION_DEADLINE.get() or (
            time.monotonic() + _STORAGE_ADMISSION_BUDGET_SECONDS
        )

    @staticmethod
    def _secure_regular_file(path: Path) -> None:
        """Apply owner-only mode to one already-present regular file safely."""
        descriptor = -1
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise V12StoreError("V12 storage is unavailable", code="storage_unavailable")
            os.fchmod(descriptor, 0o600)
        except V12StoreError:
            raise
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _protect_canonical_database(self) -> None:
        """Validate and normalize only the canonical SQLite database file."""
        self._regular(self.database_path, required=True)
        self._secure_regular_file(self.database_path)

    def _protect_admitted_sidecars(self) -> None:
        """Fail closed on unsafe live sidecars during serialized admission.

        Cortex deliberately performs no metadata mutation here.  Even
        descriptor ``fchmod`` is optional under the lifecycle contract, and
        omitting it removes the last Cortex filesystem operation against a
        sidecar that another SQLite process may have mapped after admission.
        Canonical database mode protection remains separate and strict.
        """
        for path in (Path(f"{self.database_path}-wal"), Path(f"{self.database_path}-shm")):
            self._regular(path, required=False)

    @contextmanager
    def _sqlite_admission_lock(self, deadline: float):
        """Serialize SQLite's journal-mode admission, never command meaning.

        SQLite serializes ordinary transactions itself, but concurrent
        ``PRAGMA journal_mode=WAL`` transitions are a distinct filesystem
        protocol.  A private, descriptor-validated lock file covers only the
        connect/WAL/safety transition, so independent public commands retain
        SQLite transaction concurrency after admission.  Its nonblocking
        retries consume the same inherited monotonic deadline as every other
        admission layer.
        """
        global _SQLITE_ADMISSION_LOCKS_PID, _SQLITE_ADMISSION_LOCKS
        path = self.root / ".sqlite-admission.lock"
        lock_key = os.fspath(path)
        with _SQLITE_ADMISSION_LOCKS_GUARD:
            # A fork inherits Python lock objects but not a safe ownership
            # relation for the child.  Rebuild this process-local registry;
            # the descriptor flock below remains the cross-process authority.
            if _SQLITE_ADMISSION_LOCKS_PID != os.getpid():
                _SQLITE_ADMISSION_LOCKS = {}
                _SQLITE_ADMISSION_LOCKS_PID = os.getpid()
            local_lock = _SQLITE_ADMISSION_LOCKS.setdefault(lock_key, threading.RLock())
        if getattr(local_lock, "_is_owned")():
            # Nested resolver/read/write calls in the same command must not
            # open a second descriptor and contend with their own flock.
            local_lock.acquire()
            try:
                yield
            finally:
                local_lock.release()
            return
        delay = 0.01
        while not local_lock.acquire(blocking=False):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise V12StoreError("V12 storage is busy", code="storage_busy")
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 0.08)
        descriptor = -1
        try:
            self._regular(path, required=False)
            flags = os.O_RDWR | os.O_CREAT
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise V12StoreError("V12 storage is unavailable", code="storage_unavailable")
            os.fchmod(descriptor, 0o600)
            delay = 0.01
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise V12StoreError("V12 storage is busy", code="storage_busy") from None
                    time.sleep(min(delay, remaining))
                    delay = min(delay * 2, 0.08)
            yield
        except V12StoreError:
            raise
        except OSError as exc:
            raise V12StoreError("V12 storage is unavailable", code="storage_unavailable") from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
            local_lock.release()

    def _bootstrap(self) -> None:
        self._with_storage_admission(self._bootstrap_once)

    def _bootstrap_once(self) -> None:
        try:
            self._ensure_root()
            with self._connection(database_required=False) as connection:
                connection.execute("BEGIN IMMEDIATE")
                tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if tables:
                    try:
                        self._validate_existing(connection)
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                    connection.execute("COMMIT")
                else:
                    try:
                        self._create_schema(connection)
                        from cortex_runtime.graph_ledger import create_tables
                        create_tables(connection)
                        connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (SCHEMA_VERSION, MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO v12_metadata(key,value) VALUES ('project_hash', ?)", (self.project_hash,))
                        connection.execute("INSERT INTO v12_metadata(key,value) VALUES ('project_root_digest', ?)", (hashlib.sha256(str(self.project_root).encode("utf-8")).hexdigest(),))
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                    connection.execute("COMMIT")
            self._protect_canonical_database()
            # Canonical schema readiness has succeeded.  Initial sidecar
            # construction is therefore a bounded, reconstructible repair,
            # never an additional condition for opening the canonical shard.
            self._repair_record_locators_best_effort()
            self._repair_task_locators_best_effort()
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc

    @staticmethod
    def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

    def _validate_existing(self, connection: sqlite3.Connection) -> None:
        if int(connection.execute("PRAGMA application_id").fetchone()[0]) != _APPLICATION_ID or int(connection.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        migrations = [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()]
        metadata = connection.execute("SELECT value FROM v12_metadata WHERE key='project_hash'").fetchone()
        if migrations != [(SCHEMA_VERSION, MIGRATION_NAME)] or metadata is None or str(metadata[0]) != self.project_hash:
            raise V12StoreError("reference belongs to another project", code="cross_project_reference")
        exact_columns = {
            "timeline": {"sequence", "occurred_at", "event_type", "entity_type", "entity_id", "task_id",
                "delegation_id", "report_id", "assessment_id", "closure_id", "decision_id", "payload_json"},
            "governance_assessments": {"assessment_id", "project_hash", "task_id", "mode", "source",
                "rationale", "risk_factors_json", "created_at", "created_sequence"},
            "governance_closures": {"closure_id", "project_hash", "subject_type", "subject_id", "verdict",
                "evidence_json", "unresolved_risks_json", "follow_ups_json", "completion_notes_json", "created_at", "created_sequence"},
        }
        if any(self._column_names(connection, table) != columns for table, columns in exact_columns.items()):
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        required_columns = {
            "execution_policies": {"assessment_id", "task_id", "revision", "execution_route", "user_review_requested", "minimal_mode"},
            "execution_graphs": {"graph_id", "graph_kind", "task_id", "revision", "content_digest", "content_json", "activation", "review_required", "approved"},
            "plan_candidate_families": {"graph_id", "content_digest", "content_json"},
            "plan_candidate_selections": {"graph_id", "family_graph_id", "branch_key", "validation_assignment_id"},
            "execution_nodes": {"graph_id", "node_key", "content_json", "state", "assignment_id", "artifact_generation", "facts_json"},
            "execution_assignments": {"assignment_id", "graph_id", "task_id", "revision", "nodes_json", "terminal_kind", "mode", "target_generation", "protected_task_name", "state", "quiescent"},
            "execution_publications": {"assignment_id", "report_id", "payload_digest", "payload_json", "artifact_generation"},
            "artifact_generations": {"generation_key", "task_id", "revision", "method", "fingerprint", "parent_key", "observation_json", "paths_json"},
            "project_integrity": {"singleton", "generation_key", "reconciliation_required", "barrier_epoch"},
            "tasks": {"task_id", "project_hash", "project_root", "objective", "user_request_original", "user_language", "task_contract_version", "requirements_json", "constraints_json", "acceptance_criteria_json", "verification_plan_json", "context_json"},
            "source_submissions": {"arrival", "source_ref", "session_digest", "turn_digest", "body", "signature"},
            "source_consumptions": {"source_ref", "task_id", "purpose"},
            "delegations": {"delegation_id", "task_id", "profile_name", "native_task_name", "input_report_ids_json", "input_decision_ids_json", "dispatch_correlation_marker", "dispatch_correlation_digest"},
            "reports": {"report_id", "task_id", "assembly_state", "next_chunk_index", "total_chunks", "total_bytes", "content_digest", "supersedes_report_id", "review_policy", "semantic_status", "coverage_diagnostics_json"},
            "report_operations": {"operation_id", "task_id", "delegation_id", "kind", "payload_digest", "report_id"},
            "report_chunks": {"report_id", "chunk_index", "section", "content_json", "content_digest", "content_bytes"},
            "report_usage": {"task_id", "total_retained_bytes", "assembling_bytes", "assembling_reports"},
            "timeline": {"sequence", "task_id", "decision_id", "payload_json"},
            "user_decisions": {"decision_id", "task_id", "subject_type", "subject_id", "decision_type", "response_original", "attribution", "steering_delta_json"},
            "projection_jobs": {"job_id", "task_id", "source_sequence", "status"},
            "projection_files": {"task_id", "relative_path", "content_digest", "status"},
            "report_consumption_receipts": {"task_id", "consumer_delegation_id", "reader_kind", "report_id", "observed_content_digest", "sections_json", "input_cursor", "output_cursor", "chunk_indexes_json", "returned_content_bytes", "has_more", "created_sequence"},
            "clarification_bindings": {"clarification_binding", "project_hash", "task_id", "subject_type", "subject_id", "decision_type", "prompt_digest", "prompt", "prompt_language", "effective_contract_revision", "issue_sequence", "request_digest", "response_digest", "consumed_decision_id", "plan_content_digest", "plan_approval_handle", "plan_view_content_digest", "plan_view_source_sequence"},
            "clarification_holds": {"clarification_binding", "project_hash", "task_id", "state", "response_decision_id", "opened_sequence", "answered_sequence", "created_at", "updated_at"},
            "worker_capabilities": {"capability_ref", "project_hash", "task_id", "assignment_id", "contract_revision", "build_digest", "candidate_digest", "source_digest", "catalogue_digest", "dispatch_digest", "capability_digest", "continuation_ref", "state", "created_sequence", "consumed_sequence", "created_at", "updated_at", "lease_expires_at"},
            "task_locator_publications": {"task_id", "project_hash", "suffix", "fingerprint", "created_at"},
            "approval_handles": {"approval_handle", "task_id", "report_id", "report_content_digest", "view_relative_path", "view_content_digest", "view_source_sequence", "request_digest", "created_sequence", "consumed_decision_id"},
            "effective_contract_revisions": {"task_id", "revision", "created_sequence"},
            "effective_contract_items": {"item_id", "task_id", "category", "ordinal", "text", "created_revision", "retired_revision"},
            "effective_contract_item_details": {"item_id", "details_json", "source_decision_id"},
            "assignment_scope_snapshots": {"assignment_id", "task_id", "item_id", "assignment_role", "contract_revision", "created_sequence"},
            "assignment_page_receipts": {"receipt_id", "project_hash", "task_id", "assignment_id", "snapshot_digest", "phase", "private_position", "page_digest", "returned_content_bytes", "has_more", "created_at", "created_sequence"},
            "assignment_losses": {"loss_id", "project_hash", "task_id", "assignment_id", "successor_assignment_id", "terminal_state", "reason", "evidence_json", "evidence_digest", "created_at", "created_sequence"},
        }
        for table, columns in required_columns.items():
            if not columns.issubset(self._column_names(connection, table)):
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        receipt_scopes = {
            tuple(str(column[2]) for column in connection.execute(
                "SELECT * FROM pragma_index_info(?)", (str(index[1]),)).fetchall())
            for index in connection.execute("PRAGMA index_list(command_receipts)").fetchall()
            if int(index[2])
        }
        if ("project_hash", "aggregate_type", "aggregate_id", "command_name", "logical_slot") not in receipt_scopes:
            raise V12StoreError("command receipt scope is unsupported", code="schema_unsupported")
        from cortex_runtime.delegation import is_profile_native_task_name
        seen_native_names: set[tuple[str, str]] = set()
        for row in connection.execute("SELECT task_id,delegation_id,profile_name,native_task_name FROM delegations").fetchall():
            native_name = str(row["native_task_name"])
            native_key = (str(row["task_id"]), native_name)
            if (
                native_key in seen_native_names
                or not is_profile_native_task_name(native_name, str(row["profile_name"]))
            ):
                raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
            seen_native_names.add(native_key)
        objects = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('index','trigger')")}
        from cortex_runtime.obligation_integrity import REQUIRED_TRIGGERS
        if not REQUIRED_TRIGGERS.issubset(objects):
            raise V12StoreError("obligation integrity schema is unsupported", code="schema_unsupported")
        from cortex_runtime.registry_draft import TABLES as DRAFT_TABLES, GUARDS as DRAFT_GUARDS
        if (not DRAFT_GUARDS.issubset(objects)
                or any(self._column_names(connection, table) != columns for table, columns in DRAFT_TABLES.items())):
            raise V12StoreError("registry draft schema is unsupported", code="schema_unsupported")
        from cortex_runtime.verification_journal import TABLES as FACT_TABLES, GUARDS as FACT_GUARDS
        if (not FACT_GUARDS.issubset(objects)
                or any(self._column_names(connection, table) != columns for table, columns in FACT_TABLES.items())):
            raise V12StoreError("verification journal schema is unsupported", code="schema_unsupported")
        if not {"source_submissions_no_update", "source_submissions_no_delete",
                "source_consumptions_no_update", "source_consumptions_no_delete",
                "source_initial_task"}.issubset(objects):
            raise V12StoreError("source inbox schema is unsupported", code="schema_unsupported")
        if not {"reports_terminal_no_update", "reports_no_delete", "report_chunks_no_update", "report_chunks_no_delete", "decisions_no_update", "decisions_no_delete", "decisions_task_created", "report_chunks_report_order", "timeline_decision_sequence", "projection_jobs_pending", "consumption_task_sequence", "consumption_delegation_report", "approval_handles_task_report", "clarification_bindings_task_pending", "clarification_holds_task_state", "task_locator_publications_suffix", "assignment_scope_task_revision", "assignment_scope_no_update", "assignment_scope_no_delete"}.issubset(objects):
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        if self.project_root is not None:
            canonical = str(self.project_root)
            digest = connection.execute("SELECT value FROM v12_metadata WHERE key='project_root_digest'").fetchone()
            if digest is None or str(digest[0]) != hashlib.sha256(canonical.encode("utf-8")).hexdigest():
                raise V12StoreError("reference belongs to another project", code="cross_project_reference")

    def _verify_known_task(self, task_id: str) -> None:
        self._with_storage_admission(lambda: self._verify_known_task_once(task_id))

    def _verify_known_task_once(self, task_id: str) -> None:
        try:
            self._check_open_paths(database_required=True)
            with self._connection() as connection:
                # Existing shards must already use the exact current schema.
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self._validate_existing(connection)
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
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc

    def _verify_known_record(self, record_id: str, *, label: str) -> None:
        self._with_storage_admission(lambda: self._verify_known_record_once(record_id, label=label))

    def _verify_known_record_once(self, record_id: str, *, label: str) -> None:
        """Resolve a sharded delegation/report ID to its owning task and root."""
        table_by_label = {
            "delegation_id": ("delegations", "delegation_id", "delegation"),
            "report_id": ("reports", "report_id", "report"),
            "decision_id": ("user_decisions", "decision_id", "decision"),
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
                    self._validate_existing(connection)
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
        except V12StoreError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise _storage_error(exc) from exc

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        statements = """
        CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL);
        CREATE TABLE v12_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE timeline(sequence INTEGER PRIMARY KEY AUTOINCREMENT,occurred_at TEXT NOT NULL,event_type TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,task_id TEXT,delegation_id TEXT,report_id TEXT,assessment_id TEXT,closure_id TEXT,decision_id TEXT,payload_json TEXT NOT NULL);
        CREATE TABLE tasks(task_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,project_root TEXT NOT NULL,objective TEXT NOT NULL,user_request_original TEXT NOT NULL,user_language TEXT NOT NULL,task_contract_version TEXT NOT NULL,requirements_json TEXT NOT NULL,constraints_json TEXT NOT NULL,acceptance_criteria_json TEXT NOT NULL,verification_plan_json TEXT NOT NULL,context_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,updated_sequence INTEGER NOT NULL);
        CREATE TABLE delegations(delegation_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),parent_delegation_id TEXT REFERENCES delegations(delegation_id),native_task_name TEXT NOT NULL,dispatch_correlation_marker TEXT,dispatch_correlation_digest TEXT,objective TEXT NOT NULL,role TEXT NOT NULL,profile_name TEXT NOT NULL,scope TEXT NOT NULL,instructions TEXT NOT NULL,input_report_ids_json TEXT NOT NULL,input_decision_ids_json TEXT NOT NULL,model TEXT NOT NULL,reasoning_effort TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE reports(report_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),delegation_id TEXT NOT NULL REFERENCES delegations(delegation_id),report_type TEXT NOT NULL,status TEXT,semantic_status TEXT,coverage_diagnostics_json TEXT NOT NULL DEFAULT '[]',assembly_state TEXT NOT NULL,next_chunk_index INTEGER NOT NULL,total_chunks INTEGER NOT NULL,total_bytes INTEGER NOT NULL,content_digest TEXT NOT NULL,supersedes_report_id TEXT REFERENCES reports(report_id),review_policy TEXT,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,finalized_at TEXT,finalized_sequence INTEGER,aborted_at TEXT,aborted_sequence INTEGER,abort_reason_en TEXT);
        CREATE TABLE report_chunks(report_id TEXT NOT NULL REFERENCES reports(report_id),chunk_index INTEGER NOT NULL,section TEXT NOT NULL,content_json TEXT NOT NULL,content_digest TEXT NOT NULL,content_bytes INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(report_id,chunk_index));
        CREATE TABLE report_consumption_receipts(receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),consumer_delegation_id TEXT REFERENCES delegations(delegation_id),reader_kind TEXT NOT NULL,report_id TEXT NOT NULL REFERENCES reports(report_id),observed_content_digest TEXT NOT NULL,sections_json TEXT NOT NULL,input_cursor TEXT,output_cursor TEXT,chunk_indexes_json TEXT NOT NULL,returned_content_bytes INTEGER NOT NULL,has_more INTEGER NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE report_usage(task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),total_retained_bytes INTEGER NOT NULL,assembling_bytes INTEGER NOT NULL,assembling_reports INTEGER NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE governance_assessments(assessment_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),mode TEXT NOT NULL,source TEXT NOT NULL,rationale TEXT,risk_factors_json TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE governance_closures(closure_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,verdict TEXT NOT NULL,evidence_json TEXT NOT NULL,unresolved_risks_json TEXT NOT NULL,follow_ups_json TEXT NOT NULL,completion_notes_json TEXT,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE user_decisions(decision_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,subject_digest TEXT,decision_type TEXT NOT NULL,prompt TEXT NOT NULL,response_original TEXT NOT NULL,user_language TEXT NOT NULL,attribution TEXT NOT NULL,supersedes_decision_id TEXT REFERENCES user_decisions(decision_id),created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,steering_delta_json TEXT);
        CREATE TABLE approval_handles(approval_handle TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),report_id TEXT NOT NULL REFERENCES reports(report_id),report_content_digest TEXT NOT NULL,view_relative_path TEXT NOT NULL,view_content_digest TEXT NOT NULL,view_source_sequence INTEGER NOT NULL,request_digest TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,consumed_decision_id TEXT REFERENCES user_decisions(decision_id),UNIQUE(task_id,report_id,report_content_digest,view_content_digest,view_source_sequence));
        CREATE TABLE clarification_bindings(clarification_binding TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,decision_type TEXT NOT NULL,prompt_digest TEXT NOT NULL,prompt TEXT NOT NULL,prompt_language TEXT NOT NULL,effective_contract_revision INTEGER NOT NULL,issue_sequence INTEGER NOT NULL,request_digest TEXT NOT NULL,response_digest TEXT,consumed_decision_id TEXT REFERENCES user_decisions(decision_id),created_at TEXT NOT NULL,plan_content_digest TEXT,plan_approval_handle TEXT REFERENCES approval_handles(approval_handle),plan_view_content_digest TEXT,plan_view_source_sequence INTEGER,UNIQUE(task_id,subject_type,subject_id,decision_type,prompt_digest,effective_contract_revision));
        CREATE TABLE clarification_holds(clarification_binding TEXT PRIMARY KEY REFERENCES clarification_bindings(clarification_binding),project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),state TEXT NOT NULL,response_decision_id TEXT REFERENCES user_decisions(decision_id),opened_sequence INTEGER NOT NULL,answered_sequence INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,CHECK(state IN ('pending_question','coordinator_completed','stale')));
        CREATE TABLE worker_capabilities(capability_ref TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),assignment_id TEXT NOT NULL REFERENCES delegations(delegation_id),contract_revision INTEGER NOT NULL,build_digest TEXT NOT NULL,candidate_digest TEXT NOT NULL,source_digest TEXT NOT NULL,catalogue_digest TEXT NOT NULL,dispatch_digest TEXT NOT NULL,capability_digest TEXT NOT NULL,continuation_ref TEXT UNIQUE,state TEXT NOT NULL,created_sequence INTEGER NOT NULL,consumed_sequence INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,lease_expires_at TEXT,CHECK(state IN ('minted','consumed','stale','conflict')),UNIQUE(assignment_id,contract_revision));
        CREATE TABLE task_locator_publications(task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),project_hash TEXT NOT NULL,suffix TEXT NOT NULL,fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(project_hash,task_id));
        CREATE TABLE command_receipts(command_ref TEXT PRIMARY KEY,project_hash TEXT NOT NULL,aggregate_type TEXT NOT NULL,aggregate_id TEXT NOT NULL,command_name TEXT NOT NULL,logical_slot TEXT NOT NULL,request_digest TEXT NOT NULL,status TEXT NOT NULL,result_json TEXT NOT NULL,build_id TEXT,created_sequence INTEGER NOT NULL,completed_sequence INTEGER,created_at TEXT NOT NULL,completed_at TEXT,UNIQUE(project_hash,aggregate_type,aggregate_id,command_name,logical_slot));
        CREATE TABLE projection_jobs(job_id INTEGER PRIMARY KEY AUTOINCREMENT,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),source_sequence INTEGER NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL,lease_token TEXT,lease_expires_at TEXT,last_error_code TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(task_id,source_sequence,reason));
        CREATE TABLE projection_files(task_id TEXT NOT NULL REFERENCES tasks(task_id),relative_path TEXT NOT NULL,source_sequence INTEGER NOT NULL,renderer_version TEXT NOT NULL,content_digest TEXT NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(task_id,relative_path));
        CREATE TABLE effective_contract_revisions(task_id TEXT NOT NULL REFERENCES tasks(task_id),revision INTEGER NOT NULL,decision_id TEXT REFERENCES user_decisions(decision_id),created_sequence INTEGER NOT NULL,PRIMARY KEY(task_id,revision));
        CREATE TABLE effective_contract_items(item_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),category TEXT NOT NULL,ordinal INTEGER NOT NULL,text TEXT NOT NULL,created_revision INTEGER NOT NULL,retired_revision INTEGER,UNIQUE(task_id,category,ordinal,created_revision));
        CREATE TABLE effective_contract_item_details(item_id TEXT PRIMARY KEY REFERENCES effective_contract_items(item_id),details_json TEXT NOT NULL,source_decision_id TEXT REFERENCES user_decisions(decision_id));
        CREATE TABLE assignment_scope_snapshots(assignment_id TEXT NOT NULL REFERENCES delegations(delegation_id),task_id TEXT NOT NULL REFERENCES tasks(task_id),item_id TEXT NOT NULL REFERENCES effective_contract_items(item_id),assignment_role TEXT NOT NULL,contract_revision INTEGER NOT NULL,created_sequence INTEGER NOT NULL,PRIMARY KEY(assignment_id,item_id,assignment_role));
        CREATE TABLE assignment_page_receipts(receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),assignment_id TEXT NOT NULL REFERENCES delegations(delegation_id),snapshot_digest TEXT NOT NULL,phase TEXT NOT NULL,private_position INTEGER NOT NULL,page_digest TEXT NOT NULL,returned_content_bytes INTEGER NOT NULL,has_more INTEGER NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,CHECK(phase IN ('complete','authority','evidence')),UNIQUE(assignment_id,snapshot_digest,phase,private_position));
        CREATE TABLE assignment_losses(loss_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),assignment_id TEXT NOT NULL REFERENCES delegations(delegation_id),successor_assignment_id TEXT NOT NULL,terminal_state TEXT NOT NULL,reason TEXT NOT NULL,evidence_json TEXT NOT NULL,evidence_digest TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,CHECK(terminal_state IN ('blocked','aborted')),UNIQUE(assignment_id),UNIQUE(successor_assignment_id));
        CREATE TABLE report_operations(operation_id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(task_id),delegation_id TEXT NOT NULL REFERENCES delegations(delegation_id),kind TEXT NOT NULL,payload_digest TEXT NOT NULL,report_id TEXT NOT NULL REFERENCES reports(report_id),created_at TEXT NOT NULL,UNIQUE(delegation_id,kind));
        CREATE TABLE idempotency(operation TEXT NOT NULL,idempotency_key TEXT NOT NULL,payload_digest TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(operation,idempotency_key));
        CREATE INDEX timeline_task_sequence ON timeline(task_id,sequence);
        CREATE INDEX timeline_delegation_sequence ON timeline(delegation_id,sequence);
        CREATE INDEX reports_task_created ON reports(task_id,created_sequence);
        CREATE INDEX reports_delegation_created ON reports(delegation_id,created_sequence);
        CREATE INDEX report_chunks_report_order ON report_chunks(report_id,chunk_index);
        CREATE INDEX report_operations_task ON report_operations(task_id,created_at);
        CREATE INDEX consumption_task_sequence ON report_consumption_receipts(task_id,created_sequence);
        CREATE INDEX consumption_delegation_report ON report_consumption_receipts(consumer_delegation_id,report_id,created_sequence);
        CREATE INDEX assessments_task_created ON governance_assessments(task_id,created_sequence);
        CREATE INDEX decisions_task_created ON user_decisions(task_id,created_sequence);
        CREATE INDEX approval_handles_task_report ON approval_handles(task_id,report_id,created_sequence);
        CREATE INDEX clarification_bindings_task_pending ON clarification_bindings(task_id,consumed_decision_id,issue_sequence);
        CREATE INDEX clarification_holds_task_state ON clarification_holds(task_id,state,opened_sequence);
        CREATE INDEX worker_capabilities_task_state ON worker_capabilities(task_id,state,created_sequence);
        CREATE INDEX worker_capabilities_assignment ON worker_capabilities(assignment_id,contract_revision);
        CREATE INDEX task_locator_publications_suffix ON task_locator_publications(suffix,task_id);
        CREATE INDEX command_receipts_aggregate ON command_receipts(project_hash,aggregate_type,aggregate_id,created_sequence);
        CREATE INDEX command_receipts_command ON command_receipts(project_hash,command_name,created_sequence);
        CREATE INDEX timeline_decision_sequence ON timeline(decision_id,sequence);
        CREATE INDEX projection_jobs_pending ON projection_jobs(status,lease_expires_at,job_id);
        CREATE INDEX assignment_scope_task_revision ON assignment_scope_snapshots(task_id,contract_revision,assignment_id);
        CREATE INDEX assignment_page_task_sequence ON assignment_page_receipts(task_id,created_sequence);
        CREATE INDEX assignment_page_assignment_position ON assignment_page_receipts(assignment_id,phase,private_position);
        CREATE INDEX assignment_loss_task_sequence ON assignment_losses(task_id,created_sequence);
        CREATE INDEX outcome_items_task_current ON effective_contract_items(task_id,retired_revision,category,ordinal);
        """
        for statement in statements.split(";"):
            if statement.strip():
                connection.execute(statement)
        for statement in (
            "CREATE TRIGGER reports_terminal_no_update BEFORE UPDATE ON reports WHEN OLD.assembly_state IN ('finalized','aborted') BEGIN SELECT RAISE(ABORT,'terminal reports are immutable'); END",
            "CREATE TRIGGER reports_no_delete BEFORE DELETE ON reports BEGIN SELECT RAISE(ABORT,'reports are immutable'); END",
            "CREATE TRIGGER report_chunks_no_update BEFORE UPDATE ON report_chunks BEGIN SELECT RAISE(ABORT,'report chunks are immutable'); END",
            "CREATE TRIGGER assignment_scope_no_update BEFORE UPDATE ON assignment_scope_snapshots BEGIN SELECT RAISE(ABORT,'assignment scope snapshots are immutable'); END",
            "CREATE TRIGGER assignment_scope_no_delete BEFORE DELETE ON assignment_scope_snapshots BEGIN SELECT RAISE(ABORT,'assignment scope snapshots are immutable'); END",
            "CREATE TRIGGER assignment_loss_no_update BEFORE UPDATE ON assignment_losses BEGIN SELECT RAISE(ABORT,'assignment loss records are immutable'); END",
            "CREATE TRIGGER assignment_loss_no_delete BEFORE DELETE ON assignment_losses BEGIN SELECT RAISE(ABORT,'assignment loss records are immutable'); END",
            "CREATE TRIGGER report_chunks_no_delete BEFORE DELETE ON report_chunks BEGIN SELECT RAISE(ABORT,'report chunks are immutable'); END",
            "CREATE TRIGGER decisions_no_update BEFORE UPDATE ON user_decisions BEGIN SELECT RAISE(ABORT,'decisions are append-only'); END",
            "CREATE TRIGGER decisions_no_delete BEFORE DELETE ON user_decisions BEGIN SELECT RAISE(ABORT,'decisions are append-only'); END",
        ):
            connection.execute(statement)

        from cortex_runtime.obligation_integrity import install_guards
        install_guards(connection)
        from cortex_runtime.submission_queue import create_tables as create_source_tables
        create_source_tables(connection)
        from cortex_runtime.registry_draft import create_tables as create_registry_drafts
        create_registry_drafts(connection)
        from cortex_runtime.verification_journal import create_tables as create_verification_journal
        create_verification_journal(connection)

    def _with_storage_admission(self, call: Callable[[], T]) -> T:
        """Run pre-receipt shard work under the same bounded busy policy.

        Compact-reference scans, WAL negotiation, schema readiness, and later
        receipt admission share this one monotonic deadline.  It is deliberately
        independent of a tool/family name and never manufactures a new command
        identity after an ambiguous write.
        """
        carried = self._contention_deadline
        token = _ADMISSION_DEADLINE.set(carried) if carried is not None and _ADMISSION_DEADLINE.get() is None else None
        def admitted() -> T:
            prior = self._contention_deadline
            self._contention_deadline = _ADMISSION_DEADLINE.get()
            try:
                return call()
            finally:
                self._contention_deadline = prior
        try:
            return _with_admission_budget(admitted)
        finally:
            if token is not None:
                _ADMISSION_DEADLINE.reset(token)

    @contextmanager
    def _connection(self, *, database_required: bool = True):
        connection: sqlite3.Connection | None = None
        try:
            if not database_required:
                self._precreate_database()
            self._check_open_paths(database_required=database_required)
            deadline = self._contention_deadline or _ADMISSION_DEADLINE.get() or (time.monotonic() + _STORAGE_ADMISSION_BUDGET_SECONDS)
            # The descriptor lock covers this connection through close.  The
            # source stress proved that releasing it immediately after WAL
            # setup still permits two Cortex processes to map divergent live
            # SQLite sidecar generations even without any Cortex sidecar
            # mutation.  This is a per-shard storage safety lock, not a
            # semantic-operation lock: it serializes generic SQLite access
            # and leaves receipts/identity entirely SQLite-authoritative.
            with self._sqlite_admission_lock(deadline):
                remaining = max(0.001, deadline - time.monotonic())
                connection = sqlite3.connect(self.database_path, timeout=remaining, isolation_level=None)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(f"PRAGMA busy_timeout = {max(1, int(remaining * 1000))}")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                self._protect_canonical_database()
                self._protect_admitted_sidecars()
                try:
                    yield connection
                finally:
                    connection.close()
                    connection = None
        finally:
            if connection is not None:
                connection.close()

    def _read(self, call: Callable[[sqlite3.Connection], T]) -> T:
        return self._with_storage_admission(lambda: self._read_once(call))

    def _read_once(self, call: Callable[[sqlite3.Connection], T]) -> T:
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
        return self._with_storage_admission(lambda: self._write_once(call))

    def _write_once(self, call: Callable[[sqlite3.Connection], T]) -> T:
        # Every durable command uses this single bounded acquisition policy.
        # Retrying a failed SQLite write transaction is safe because SQLite
        # rolls it back before control returns; semantic receipt admission is
        # still responsible for reconciling any ambiguous committed result.
        deadline = self._contention_deadline or (time.monotonic() + _STORAGE_ADMISSION_BUDGET_SECONDS)
        delay = 0.01
        while True:
            try:
                with self._guard, self._connection() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        result = call(connection)
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                    connection.execute("COMMIT")
                    self._protect_canonical_database()
                    return result
            except V12StoreError:
                raise
            except (OSError, sqlite3.DatabaseError) as exc:
                classified = _storage_error(exc)
                if classified.code != "storage_busy" or time.monotonic() >= deadline:
                    raise classified from exc
                time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
                delay = min(delay * 2, 0.08)

    def _reconcile_command_receipt_after_contention(
        self, *, aggregate_type: str, aggregate_id: str, command_name: str,
        logical_slot: str, request_digest: str,
    ) -> tuple[dict[str, Any], bool] | None:
        """Read-only receipt convergence after a bounded write-contention wait."""
        try:
            row = self.lookup_command_receipt(logical_slot, aggregate_type=aggregate_type,
                                              aggregate_id=aggregate_id, command_name=command_name)
        except V12StoreError:
            return None
        if row is None:
            return None
        if row.get("request_digest") != request_digest:
            raise V12StoreError("command slot already has a different request", code="command_conflict", details={"logical_slot": logical_slot})
        try:
            value = json.loads(str(row["result_json"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise V12StoreError("command receipt is corrupted", code="schema_unsupported") from exc
        if not isinstance(value, dict):
            raise V12StoreError("command receipt is corrupted", code="schema_unsupported")
        return value, True

    def lookup_command_receipt(self, logical_slot: object, *, aggregate_type: object,
                               aggregate_id: object, command_name: object) -> dict[str, Any] | None:
        """Return the server-owned receipt for one logical command slot."""
        slot = _required_text(logical_slot, label="logical_slot", maximum=TEXT_MAX_LENGTH)
        scope = tuple(_required_text(value, label=label, maximum=TEXT_MAX_LENGTH)
                      for label, value in (("aggregate_type", aggregate_type),
                                           ("aggregate_id", aggregate_id), ("command_name", command_name)))
        return self._read(lambda connection: _row(connection.execute(
            "SELECT command_ref,project_hash,aggregate_type,aggregate_id,command_name,logical_slot,request_digest,status,result_json,build_id,created_sequence,completed_sequence,created_at,completed_at FROM command_receipts WHERE project_hash=? AND aggregate_type=? AND aggregate_id=? AND command_name=? AND logical_slot=?",
            (self.project_hash, *scope, slot),
        ).fetchone()))

    def run_command_receipt(
        self,
        *,
        aggregate_type: object,
        aggregate_id: object,
        command_name: object,
        logical_slot: object,
        request: Mapping[str, Any],
        mutate: Callable[[sqlite3.Connection], Mapping[str, Any]],
        build_id: object | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Execute one semantic command and durably receipt its result.

        The lookup, admission, domain mutation, and receipt insert share one
        ``BEGIN IMMEDIATE`` transaction.  A matching slot/digest is an exact
        replay; a matching slot with a different digest is a conflict.  Any
        admission or mutation exception rolls back both domain state and the
        receipt, so incomplete commands cannot leave successful-looking state.
        """
        aggregate_type_text = _required_text(aggregate_type, label="aggregate_type", maximum=TEXT_MAX_LENGTH)
        aggregate_id_text = _required_text(aggregate_id, label="aggregate_id", maximum=TEXT_MAX_LENGTH)
        command_name_text = _required_text(command_name, label="command_name", maximum=TEXT_MAX_LENGTH)
        slot = _required_text(logical_slot, label="logical_slot", maximum=TEXT_MAX_LENGTH)
        normalized_request = _strict_json(dict(request), label="command request")
        request_digest = hashlib.sha256(_canonical_json(normalized_request, label="command request").encode("utf-8")).hexdigest()
        build = None if build_id is None else _required_text(build_id, label="build_id", maximum=TEXT_MAX_LENGTH)

        def transact(connection: sqlite3.Connection) -> tuple[dict[str, Any], bool]:
            existing = connection.execute(
                "SELECT request_digest,status,result_json FROM command_receipts WHERE project_hash=? AND aggregate_type=? AND aggregate_id=? AND command_name=? AND logical_slot=?",
                (self.project_hash, aggregate_type_text, aggregate_id_text, command_name_text, slot),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != request_digest:
                    raise V12StoreError("command slot already has a different request", code="command_conflict", details={"logical_slot": slot})
                try:
                    result = json.loads(str(existing[2]))
                except (TypeError, ValueError) as exc:
                    raise V12StoreError("command receipt is corrupted", code="schema_unsupported") from exc
                if not isinstance(result, dict):
                    raise V12StoreError("command receipt is corrupted", code="schema_unsupported")
                return result, True
            result = dict(mutate(connection))
            result = _strict_json(result, label="command result")
            sequence_row = connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM timeline").fetchone()
            sequence = int(sequence_row[0])
            now = _now()
            command_ref = f"cmd-{uuid.uuid4().hex}"
            encoded = _canonical_json(result, label="command result")
            connection.execute(
                "INSERT INTO command_receipts(command_ref,project_hash,aggregate_type,aggregate_id,command_name,logical_slot,request_digest,status,result_json,build_id,created_sequence,completed_sequence,created_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (command_ref, self.project_hash, aggregate_type_text, aggregate_id_text, command_name_text, slot, request_digest, "completed", encoded, build, sequence, sequence, now, now),
            )
            return result, False

        try:
            return self._write(transact)
        except V12StoreError as exc:
            if exc.code != "storage_busy":
                raise
            reconciled = self._reconcile_command_receipt_after_contention(
                aggregate_type=aggregate_type_text, aggregate_id=aggregate_id_text, command_name=command_name_text,
                logical_slot=slot, request_digest=request_digest,
            )
            if reconciled is not None:
                return reconciled
            raise

    # Explicit name for callers implementing the kernel protocol.
    execute_command_receipt = run_command_receipt

    def run_command_receipt_resolved(
        self,
        *,
        aggregate_type: object,
        aggregate_id: object,
        command_name: object,
        resolve: Callable[[sqlite3.Connection], tuple[object, Mapping[str, Any], Callable[[sqlite3.Connection], Mapping[str, Any]]]],
        build_id: object | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Run a receipt after resolving command identity in its write transaction.

        Some semantic commands derive their logical slot from a current
        effective-contract revision.  Resolving it before ``BEGIN IMMEDIATE``
        permits a concurrent steering write to split identity from mutation.
        This variant resolves the slot, normalized request, and mutation only
        after the same transaction has acquired its write lock.
        """
        aggregate_type_text = _required_text(aggregate_type, label="aggregate_type", maximum=TEXT_MAX_LENGTH)
        aggregate_id_text = _required_text(aggregate_id, label="aggregate_id", maximum=TEXT_MAX_LENGTH)
        command_name_text = _required_text(command_name, label="command_name", maximum=TEXT_MAX_LENGTH)
        build = None if build_id is None else _required_text(build_id, label="build_id", maximum=TEXT_MAX_LENGTH)

        resolved_identity: tuple[str, str] | None = None

        def transact(connection: sqlite3.Connection) -> tuple[dict[str, Any], bool]:
            nonlocal resolved_identity
            logical_slot, request, mutate = resolve(connection)
            slot = _required_text(logical_slot, label="logical_slot", maximum=TEXT_MAX_LENGTH)
            normalized_request = _strict_json(dict(request), label="command request")
            request_digest = hashlib.sha256(
                _canonical_json(normalized_request, label="command request").encode("utf-8")
            ).hexdigest()
            resolved_identity = (slot, request_digest)
            existing = connection.execute(
                "SELECT request_digest,result_json FROM command_receipts WHERE project_hash=? AND aggregate_type=? AND aggregate_id=? AND command_name=? AND logical_slot=?",
                (self.project_hash, aggregate_type_text, aggregate_id_text, command_name_text, slot),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != request_digest:
                    raise V12StoreError("command slot already has a different request", code="command_conflict", details={"logical_slot": slot})
                result = _load_json(str(existing[1]), label="command receipt")
                if not isinstance(result, dict):
                    raise V12StoreError("command receipt is corrupted", code="schema_unsupported")
                return result, True
            if not callable(mutate):
                raise V12StoreError("command mutation is invalid", code="schema_unsupported")
            result = _strict_json(dict(mutate(connection)), label="command result")
            sequence = int(connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM timeline").fetchone()[0])
            now = _now()
            connection.execute(
                "INSERT INTO command_receipts(command_ref,project_hash,aggregate_type,aggregate_id,command_name,logical_slot,request_digest,status,result_json,build_id,created_sequence,completed_sequence,created_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"cmd-{uuid.uuid4().hex}", self.project_hash, aggregate_type_text, aggregate_id_text,
                 command_name_text, slot, request_digest, "completed",
                 _canonical_json(result, label="command result"), build, sequence, sequence, now, now),
            )
            return result, False

        try:
            return self._write(transact)
        except V12StoreError as exc:
            if exc.code != "storage_busy" or resolved_identity is None:
                raise
            reconciled = self._reconcile_command_receipt_after_contention(
                aggregate_type=aggregate_type_text, aggregate_id=aggregate_id_text, command_name=command_name_text,
                logical_slot=resolved_identity[0], request_digest=resolved_identity[1],
            )
            if reconciled is not None:
                return reconciled
            raise

    def _mutation(self, operation: str, payload: Mapping[str, Any], key: Any, call: Callable[[sqlite3.Connection], dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        return self._with_storage_admission(
            lambda: self._mutation_once(operation, payload, key, call)
        )

    def _mutation_once(self, operation: str, payload: Mapping[str, Any], key: Any, call: Callable[[sqlite3.Connection], dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        normalized = _strict_json(dict(payload), label="mutation payload")
        # Operation identity is server-owned.  Public semantic callers do not
        # manufacture retry keys; identity is computed after normalizing the
        # fields whose order/presence has no domain meaning.  This makes
        # equivalent retries (None vs [], reordered refs) the same mutation
        # while preserving a digest conflict for genuinely different data.
        identity_payload = self._canonical_operation_payload(operation, normalized)
        digest = hashlib.sha256(_canonical_json(identity_payload, label="mutation payload").encode("utf-8")).hexdigest()
        caller_supplied_key = key is not None
        client_key = (_required_text(key, label="idempotency_key", maximum=IDEMPOTENCY_KEY_MAX_LENGTH)
                      if caller_supplied_key else "server-" + digest)
        retry_handle = client_key
        idempotency = hashlib.sha256(_canonical_json({"operation": operation, "retry_handle": retry_handle}, label="idempotency operation key").encode("utf-8")).hexdigest()
        def transact(connection: sqlite3.Connection) -> tuple[dict[str, Any], bool]:
            previous = connection.execute("SELECT payload_digest,result_json FROM idempotency WHERE operation=? AND idempotency_key=?", (operation, idempotency)).fetchone()
            if previous is not None:
                if str(previous["payload_digest"]) != digest:
                    raise V12StoreError("idempotency key was already used for different arguments", code="idempotency_conflict")
                value = _load_json(str(previous["result_json"]), label="idempotency result")
                if not isinstance(value, dict):
                    raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
                if not caller_supplied_key:
                    value.pop("idempotency_key", None)
                return value, True
            # The public operation envelope has already been admitted at the
            # MCP boundary.  Persisting its response additionally records
            # server-minted IDs, timestamps, and retry metadata, so validate
            # that result against the dedicated bounded result envelope.
            value = _strict_json(
                call(connection),
                label="mutation result",
                maximum_bytes=MUTATION_RESULT_MAX_BYTES,
            )
            if not isinstance(value, dict):
                raise V12StoreError("V12 storage is unavailable", code="storage_unavailable")
            projection_task = payload.get("task_id")
            if not isinstance(projection_task, str):
                candidate = value.get("task")
                projection_task = candidate.get("task_id") if isinstance(candidate, Mapping) else None
            if isinstance(projection_task, str):
                sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM timeline WHERE task_id=?", (projection_task,)).fetchone()[0]
                connection.execute("INSERT INTO projection_jobs(project_hash,task_id,source_sequence,reason,status,created_at,updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?) ON CONFLICT(task_id,source_sequence,reason) DO UPDATE SET status='pending',last_error_code=NULL,updated_at=excluded.updated_at", (self.project_hash, projection_task, int(sequence), operation, _now(), _now()))
            binding_replayed = bool(value.get("replayed"))
            value = dict(value) | {"retry_handle": retry_handle}
            if caller_supplied_key:
                value["idempotency_key"] = client_key
            connection.execute(
                "INSERT INTO idempotency(operation,idempotency_key,payload_digest,result_json,created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    operation,
                    idempotency,
                    digest,
                    _canonical_json(
                        value,
                        label="mutation result",
                        maximum_bytes=MUTATION_RESULT_MAX_BYTES,
                    ),
                    _now(),
                ),
            )
            return value, binding_replayed
        result, replayed = self._write(transact)
        # Record writes are committed before this derived cross-shard index is
        # refreshed.  Should the index be unavailable after a committed write,
        # later resolution uses its explicit canonical-recovery scan rather than
        # treating the cache as durable record authority.
        if not replayed:
            self._refresh_record_locators_after_commit()
            if operation == "create_task":
                self._refresh_task_locators_after_commit()
        # Derived Markdown is never part of the transaction's success.  A
        # bounded best-effort pass happens only after canonical commit; later
        # reads retry it opportunistically.
        task_value = normalized.get("task_id")
        if not isinstance(task_value, str) and isinstance(result.get("task"), Mapping):
            task_value = result["task"].get("task_id")
        if isinstance(task_value, str):
            self.materialize_human_views(task_value)
        return result, replayed

    @staticmethod
    def _canonical_operation_payload(operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Canonical identity payload for all durable mutations.

        The ledger stores the caller's validated representation, but mutation
        identity must be based on domain semantics: optional collection
        fields treat ``null`` and an empty collection alike, and relation
        collections are sets.  This is intentionally limited to identity;
        it never changes the durable evidence or public response.
        """
        value = dict(payload)
        collection_fields = {
            "input_report_ids", "input_decision_ids", "requirements",
            "constraints", "acceptance_criteria", "verification_plan",
            "risk_factors", "unresolved_risks", "follow_ups",
            "dependencies", "linked_task_ids", "linked_delegation_ids",
            "linked_report_ids", "linked_decision_ids",
        }
        for field in collection_fields:
            if field in value and (value[field] is None or value[field] == []):
                value[field] = []
            elif field in value and isinstance(value[field], list):
                # These are references or contract facts whose order is not a
                # relation.  Preserve duplicate rejection in input validation,
                # then sort only for the identity digest.
                value[field] = sorted(value[field], key=lambda item: _canonical_json(item, label=field))
        for field in ("context", "evidence", "completion_notes", "notes"):
            if field in value and value[field] is None:
                value[field] = {}
        return {"operation": operation, "payload": value}

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

    def human_view(self, task_id: str, relative_path: str, *, require_fresh: bool = True) -> dict[str, Any]:
        """Repair a bounded time, then expose only a freshly verified path.

        A read receipt can make a previously ready projection stale without
        adding a projection job.  After the normal queued-job attempt, perform
        one direct best-effort render for this known task before reporting that
        a view is stale. ``require_fresh=False`` is reserved for an immutable
        plan-review snapshot: it accepts a ready view for that exact report
        even if unrelated later task chronology exists. It never accepts an
        absent, mismatched, failed, or renderer-stale projection. Conflicts
        and I/O failures remain honest non-ready
        states and never alter canonical mutation success.
        """
        self.materialize_human_views(task_id)
        try:
            from cortex_runtime.v12_projections import human_view, materialize_task
            view = human_view(self, task_id, relative_path, require_fresh=require_fresh)
            if view.get("status") == "ready":
                return view
            materialize_task(self, task_id)
            return human_view(self, task_id, relative_path, require_fresh=require_fresh)
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

        return self._write(lambda connection: self._ready_plan_review_relation(
            connection, task_id=anchor, report_id=report,
            report_content_digest=report_digest, view_relative_path=expected_relative,
            view_content_digest=view_digest, view_source_sequence=view_source_sequence,
        )["approval_handle"])

    def _ready_plan_review_relation(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        report_id: str,
        report_content_digest: str | None = None,
        view_relative_path: str | None = None,
        view_content_digest: str | None = None,
        view_source_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Resolve one ready plan/view relation in the caller's transaction.

        The returned values are immutable binding evidence.  A record command
        later validates this persisted relation, rather than querying for the
        newest approval handle or projection view.
        """
        task = self._task(connection, task_id)
        plan = self._report(connection, report_id, task_id=task_id)
        if (plan["report_type"] != "plan" or plan["assembly_state"] != "finalized"
                or plan.get("semantic_status") != "semantic_valid"):
            raise V12StoreError("approval view plan is invalid", code="approval_view_mismatch")
        digest = str(plan["content_digest"])
        if report_content_digest is not None and report_content_digest != digest:
            raise V12StoreError("approval view plan is invalid", code="approval_view_mismatch")
        relative = f"plans/revisions/{report_id}.md"
        if view_relative_path is not None and view_relative_path != relative:
            raise V12StoreError("approval view is invalid", code="approval_view_mismatch")
        row = connection.execute(
            "SELECT source_sequence,content_digest,status FROM projection_files WHERE task_id=? AND relative_path=?",
            (task_id, relative),
        ).fetchone()
        # The approval relation proves one immutable report/view snapshot. It
        # is deliberately not a claim about the task's latest global event:
        # later governance or unrelated task chronology must not
        # invalidate a previously presented review capability. A changed
        # report or rendered view has different digest/sequence evidence and
        # therefore mints a distinct relation at the next open.
        if row is None or str(row["status"]) != "ready":
            raise V12StoreError("approval view is not ready", code="approval_view_not_ready")
        source_sequence = int(row["source_sequence"])
        view_digest = str(row["content_digest"])
        if ((view_content_digest is not None and view_content_digest != view_digest)
                or (view_source_sequence is not None and view_source_sequence != source_sequence)):
            raise V12StoreError("approval view is invalid", code="approval_view_mismatch")
        request_digest = _sha256_prefixed(task["user_request_original"], label="user request original")
        existing = connection.execute(
            "SELECT approval_handle FROM approval_handles WHERE task_id=? AND report_id=? AND report_content_digest=? AND view_content_digest=? AND view_source_sequence=?",
            (task_id, report_id, digest, view_digest, source_sequence),
        ).fetchone()
        handle = str(existing["approval_handle"]) if existing is not None else f"approval-{self.project_hash}-{uuid.uuid4().hex}"
        if existing is None:
            connection.execute(
                "INSERT INTO approval_handles(approval_handle,project_hash,task_id,report_id,report_content_digest,view_relative_path,view_content_digest,view_source_sequence,request_digest,created_at,created_sequence,consumed_decision_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (handle, self.project_hash, task_id, report_id, digest, relative, view_digest,
                 source_sequence, request_digest, _now(), source_sequence),
            )
        return {
            "plan_content_digest": digest,
            "approval_handle": handle,
            "view_content_digest": view_digest,
            "view_source_sequence": source_sequence,
        }

    @staticmethod
    def _timeline(connection: sqlite3.Connection, *, event_type: str, entity_type: str, entity_id: str, payload: Mapping[str, Any], task_id: str, occurred_at: str | None = None, delegation_id: str | None = None, report_id: str | None = None, assessment_id: str | None = None, closure_id: str | None = None, decision_id: str | None = None) -> int:
        """Append one immutable, task-scoped event in the caller transaction.

        ``timeline.sequence`` is an SQLite AUTOINCREMENT key.  Combined with
        the caller's ``BEGIN IMMEDIATE`` write transaction it serializes WAL
        writers without a separate sequence allocator, and it never permits a
        mutation to commit without its chronology entry.
        """
        if not isinstance(task_id, str) or not task_id:
            raise V12StoreError("task-scoped timeline event is required", code="ledger_corrupt")
        timestamp = _now() if occurred_at is None else _required_text(occurred_at, label="timeline occurred_at", maximum=128)
        cursor = connection.execute("INSERT INTO timeline(occurred_at,event_type,entity_type,entity_id,task_id,delegation_id,report_id,assessment_id,closure_id,decision_id,payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (timestamp, event_type, entity_type, entity_id, task_id, delegation_id, report_id, assessment_id, closure_id, decision_id, _canonical_json(dict(payload), label="timeline payload")))
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
        marker, marker_digest = found.get("dispatch_correlation_marker"), found.get("dispatch_correlation_digest")
        if (marker is None) != (marker_digest is None):
            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
        if marker is not None and (
            not isinstance(marker, str)
            or re.fullmatch(r"dc_[0-9a-f]{32}", marker) is None
            or marker_digest != "sha256:" + hashlib.sha256(marker.encode("utf-8")).hexdigest()
        ):
            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
        found["task_ref"] = compact_ref
        return found

    def _execution_evidence(self, connection: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        """Project current graph completion; report counts are diagnostics only."""
        finalized = int(connection.execute(
            "SELECT COUNT(*) FROM reports WHERE project_hash=? AND task_id=? AND assembly_state='finalized'",
            (self.project_hash, task_id),
        ).fetchone()[0])
        completed = int(connection.execute(
            "SELECT COUNT(*) FROM reports WHERE project_hash=? AND task_id=? AND assembly_state='finalized' AND report_type='result' AND semantic_status='semantic_valid' AND status='completed'",
            (self.project_hash, task_id),
        ).fetchone()[0])
        coverage = self._aggregate_coverage(connection, task_id)
        contract = self._effective_contract(connection, task_id)
        return {
            "evidence_status": "finalized_reports_present" if finalized else "no_finalized_reports",
            "finalized_report_count": finalized,
            "completed_report_count": completed,
            "effective_revision": contract["revision"],
            "coverage_status": coverage["status"],
            "outcome": "completed" if coverage["status"] == "ready" else "incomplete",
        }

    def _advisory_closure(self, connection: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        """Return task-relevant advisory bookkeeping separately from outcome."""
        closure = self._task_closure(connection, task_id)
        return {"record_status": "not_recorded" if closure is None else "recorded", "latest_record": None if closure is None else self._closure(connection, str(closure["closure_id"]))}

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
        from cortex_runtime.delegation import is_profile_native_task_name
        native_name = found.get("native_task_name")
        if (
            not is_profile_native_task_name(native_name, found.get("profile_name"))
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
        found["coverage_diagnostics"] = _load_json(
            str(found.pop("coverage_diagnostics_json", "[]")),
            label="report coverage diagnostics",
        )
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
        found["record_status"] = "recorded"
        return found

    @staticmethod
    def _compact_report(report: Mapping[str, Any]) -> dict[str, Any]:
        keys = ("report_id", "project_hash", "task_id", "delegation_id", "report_type", "status", "semantic_status", "coverage_diagnostics", "assembly_state", "next_chunk_index", "total_chunks", "total_bytes", "content_digest", "supersedes_report_id", "review_policy", "created_at", "created_sequence", "finalized_at", "finalized_sequence", "aborted_at", "aborted_sequence")
        compact = {key: report.get(key) for key in keys}
        compact["storage_status"] = "storage_valid"
        return compact

    @staticmethod
    def _compact_delegation(delegation: Mapping[str, Any]) -> dict[str, Any]:
        return {key: delegation[key] for key in ("delegation_id", "project_hash", "task_id", "parent_delegation_id", "native_task_name", "objective", "role", "profile_name", "scope", "model", "reasoning_effort", "created_at", "created_sequence")}

    @staticmethod
    def _outcome_ref(item_id: str) -> str:
        if not re.fullmatch(r"outcome-[0-9a-f]{32}", item_id):
            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
        return "o_" + item_id[-12:]

    def _contract_item_view(self, row: Mapping[str, Any], *, assignment_role: str | None = None) -> dict[str, Any]:
        details = _load_json(str(row.get("details_json") or "{}"), label="effective contract item details")
        if not isinstance(details, Mapping):
            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
        item = {
            "item_ref": self._outcome_ref(str(row["item_id"])),
            "category": str(row["category"]),
            "ordinal": int(row["ordinal"]),
            "text": str(row["text"]),
            "acceptance_criteria": list(details.get("acceptance_criteria", [])),
            "verification_criteria": list(details.get("verification_criteria", [])),
            "constraints": list(details.get("constraints", [])),
            "created_revision": int(row["created_revision"]),
        }
        # This is a derived coverage projection, not a second persisted copy
        # of every source sentence. The immutable row/decision supplies origin.
        origin = "user_steer" if row.get("source_decision_id") else "user_request"
        source = f"outcomes[{item['ordinal']}]"
        item["source_fragments"] = [{"source_type": origin, "path": source + ".outcome", "text": item["text"]}]
        for field, target in (("acceptance", "acceptance_criteria"), ("verification", "verification_criteria"),
                              ("constraints", "constraints")):
            item["source_fragments"].extend(
                {"source_type": origin, "path": f"{source}.{field}[{index}]", "text": text}
                for index, text in enumerate(item[target]))
        if row.get("source_decision_id") is not None:
            item["source_decision_ref"] = record_ref(str(row["source_decision_id"]))
        supersedes = details.get("supersedes_item_ref")
        if isinstance(supersedes, str):
            item["supersedes_item_ref"] = supersedes
        if assignment_role is not None:
            item["assignment_role"] = assignment_role
        return item

    @staticmethod
    def _task_constraint_view(connection: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
        row = connection.execute("SELECT constraints_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise V12StoreError("task was not found", code="task_not_found")
        constraints = _load_json(str(row["constraints_json"]), label="task constraints")
        return [
            {
                "text": str(text),
                "source_fragment": {
                    "source_type": "user_request",
                    "path": f"task.constraints[{ordinal}]",
                    "text": str(text),
                },
            }
            for ordinal, text in enumerate(constraints if isinstance(constraints, list) else [])
        ]

    def _effective_contract(self, connection: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        revision_row = connection.execute("SELECT revision FROM effective_contract_revisions WHERE task_id=? ORDER BY revision DESC LIMIT 1", (task_id,)).fetchone()
        revision = 0 if revision_row is None else int(revision_row[0])
        rows = connection.execute(
            "SELECT i.*,d.details_json,d.source_decision_id FROM effective_contract_items i "
            "JOIN effective_contract_item_details d ON d.item_id=i.item_id "
            "WHERE i.task_id=? AND (i.retired_revision IS NULL OR i.retired_revision>?) "
            "ORDER BY i.category,i.ordinal,i.item_id",
            (task_id, revision),
        ).fetchall()
        items = [self._contract_item_view(_row(row) or {}) for row in rows]
        return {"revision": revision, "items": items, "task_constraints": self._task_constraint_view(connection, task_id)}

    def _effective_contract_at_revision(self, connection: sqlite3.Connection, task_id: str, revision: int) -> dict[str, Any]:
        """Project the immutable contract snapshot owned at assignment time."""
        rows = connection.execute(
            "SELECT i.*,d.details_json,d.source_decision_id FROM effective_contract_items i "
            "JOIN effective_contract_item_details d ON d.item_id=i.item_id "
            "WHERE i.task_id=? AND i.created_revision<=? AND (i.retired_revision IS NULL OR i.retired_revision>?) ORDER BY i.category,i.ordinal,i.item_id",
            (task_id, revision, revision),
        ).fetchall()
        items = [self._contract_item_view(_row(row) or {}) for row in rows]
        return {"revision": revision, "items": items, "task_constraints": self._task_constraint_view(connection, task_id)}


    def _aggregate_coverage(self, connection: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        """One outcome projection, derived exclusively from the current graph."""
        from cortex_runtime.graph_ledger import task_projection
        state = task_projection(connection, task_id)
        return {
            "status": "ready" if state["outcomes"] and all(item["status"] == "complete" for item in state["outcomes"]) else "incomplete",
            "items": state["outcomes"],
        }

    def _conformance_review(self, connection: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        """Current typed readiness; no parallel owner-claim interpretation."""
        from cortex_runtime.graph_ledger import closure_evidence
        evidence = closure_evidence(connection, task_id)
        return {
            "effective_revision": evidence["revision"],
            "status": "ready" if evidence["ready"] else "not_ready",
            "outcomes": evidence["outcomes"],
            "nodes": evidence["nodes"],
            "unresolved_evidence": evidence["reasons"],
            "risks": evidence["risks"],
        }

    def _semantic_contract(self, connection: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
        from cortex_runtime.candidate_family import current_contract
        return current_contract(connection, task_id)

    def _commit_contract_delta(self, connection: sqlite3.Connection, *, task_id: str,
                               delta: Mapping[str, Any], decision_id: str, sequence: int,
                               selected_family: tuple[str, str] | None = None) -> dict[str, Any]:
        """One complete semantic replacement transaction, without field merging."""
        from cortex_runtime.candidate_family import proposed_contract
        from cortex_runtime.execution_graph import GraphError
        from cortex_runtime import graph_ledger
        graph_ledger._transaction(connection)
        if selected_family is not None:
            from cortex_runtime.candidate_family import selection_evidence
            selected = selection_evidence(connection, graph_id=selected_family[0], branch_key=selected_family[1])
            if delta != selected["selected"]["definition"]["delta"]:
                raise GraphError("candidate_selection_delta_mismatch")
        base = self._semantic_contract(connection, task_id)
        try:
            proposed = proposed_contract(base, delta)
        except GraphError as exc:
            raise V12StoreError("semantic contract change is invalid", code="invalid_argument",
                                details={"reason": exc.reason}) from None
        if proposed == base and selected_family is None:
            raise V12StoreError("steering requires a semantic change", code="invalid_argument",
                                details={"reason": "semantic_noop"})
        revision = graph_ledger._current_revision(connection, task_id) + 1
        # Establish the task-bound decision relation before inserting or retiring
        # any obligation. All writes still commit or roll back together.
        connection.execute("INSERT INTO effective_contract_revisions VALUES (?,?,?,?)",
                           (task_id, revision, decision_id, sequence))
        rows = {row["text"]: row for row in connection.execute(
            "SELECT item_id,text,ordinal FROM effective_contract_items WHERE task_id=? AND retired_revision IS NULL",
            (task_id,))}
        retired = delta["retire"]
        for name in retired:
            connection.execute("UPDATE effective_contract_items SET retired_revision=? WHERE item_id=?",
                               (revision, rows[name]["item_id"]))
        next_ordinal = connection.execute(
            "SELECT COALESCE(MAX(ordinal),-1)+1 FROM effective_contract_items WHERE task_id=?", (task_id,),
        ).fetchone()[0]
        point = len(retired) == len(delta["add"]) == 1
        for offset, item in enumerate(delta["add"]):
            ordinal = rows[retired[0]]["ordinal"] if point else next_ordinal + offset
            item_id = "outcome-" + uuid.uuid4().hex
            details = {"acceptance_criteria": item["acceptance"], "constraints": item["constraints"],
                       "verification_criteria": item["verification"]}
            if point:
                details["supersedes_item_ref"] = self._outcome_ref(rows[retired[0]]["item_id"])
            connection.execute(
                "INSERT INTO effective_contract_items(item_id,project_hash,task_id,category,ordinal,text,created_revision,retired_revision) VALUES (?,?,?,'outcome',?,?,?,NULL)",
                (item_id, self.project_hash, task_id, ordinal, item["outcome"], revision),
            )
            connection.execute("INSERT INTO effective_contract_item_details VALUES (?,?,?)",
                               (item_id, _canonical_json(details, label="outcome details"), decision_id))
        if selected_family is None:
            stale = graph_ledger.invalidate_revision(connection, task_id)
        else:
            # Selection was independently validated on the unchanged sealed
            # artifact and every old native route is terminal. No physical
            # reconciliation work or second user decision is manufactured.
            stale = []
            connection.execute("UPDATE execution_graphs SET activation='stale' WHERE task_id=? AND revision<?", (task_id, revision))
            connection.execute("UPDATE execution_nodes SET state='stale' WHERE graph_id IN (SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision<?) AND state IN ('waiting','ready','active')", (task_id, revision))
        graph_ledger.ensure_bootstrap(connection, task_id=task_id, outcomes=[item["outcome"] for item in proposed])
        connection.execute(
            "UPDATE worker_capabilities SET state='stale',updated_at=? WHERE task_id=? AND contract_revision<? "
            "AND state IN ('minted','consumed') AND NOT EXISTS (SELECT 1 FROM reports r "
            "WHERE r.delegation_id=worker_capabilities.assignment_id AND r.assembly_state='finalized')",
            (_now(), task_id, revision),
        )
        connection.execute(
            "UPDATE clarification_holds SET state='stale',updated_at=? WHERE task_id=? AND state='pending_question' "
            "AND clarification_binding IN (SELECT clarification_binding FROM clarification_bindings "
            "WHERE task_id=? AND effective_contract_revision<?)", (_now(), task_id, task_id, revision),
        )
        if selected_family is not None:
            from cortex_runtime.candidate_family import activate_selected
            activate_selected(connection, family_graph_id=selected_family[0], branch_key=selected_family[1], decision_id=decision_id)
        return {"effective_revision": revision, "reconciliation_required": selected_family is None,
                "reconciliation_epoch": connection.execute("SELECT barrier_epoch FROM project_integrity WHERE singleton=1").fetchone()[0],
                "invalidated_assignment_count": len(stale), "stale_assignments": stale}

    def _outcome_item_id(self, connection: sqlite3.Connection, task_id: str, value: Any) -> str:
        if not isinstance(value, str) or re.fullmatch(r"o_[0-9a-f]{12}", value) is None:
            raise V12StoreError("outcome item reference is invalid", code="invalid_argument", details={"field": "outcome_item_refs"})
        rows = connection.execute("SELECT item_id FROM effective_contract_items WHERE task_id=? AND item_id LIKE ?", (task_id, "%" + value[2:])).fetchall()
        if len(rows) != 1:
            raise V12StoreError("outcome item was not found", code="outcome_item_not_found")
        item_id = str(rows[0][0])
        current = self._effective_contract(connection, task_id)["revision"]
        row = connection.execute("SELECT retired_revision FROM effective_contract_items WHERE item_id=?", (item_id,)).fetchone()
        if row is None or (row[0] is not None and int(row[0]) <= current):
            raise V12StoreError("outcome item is stale", code="outcome_item_stale")
        return item_id

    @staticmethod
    def _next_native_task_name(connection: sqlite3.Connection, *, task_id: str, profile_name: str) -> str:
        """Allocate the first unused profile-derived native name in one task.

        The surrounding write transaction serializes same-profile siblings.
        Only the current profile-derived naming format is admitted.
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
        return value

    def create_task(self, *, objective: Any, user_request_original: Any, user_language: Any, requirements: Any, constraints: Any, acceptance_criteria: Any, verification_plan: Any, outcome_contracts: Any, context: Any = None, task_id: Any = None, idempotency_key: Any = None, task_contract_version: Any = TASK_CONTRACT_VERSION) -> tuple[dict[str, Any], bool]:
        english_objective = _opaque_text(objective, label="objective")
        normalized_requirements = _contract_text_list(requirements, label="requirements")
        normalized_acceptance = _contract_optional_text_list(acceptance_criteria, label="acceptance_criteria")
        normalized_verification = _contract_optional_text_list(verification_plan, label="verification_plan")
        payload = {
            "objective": english_objective,
            "user_request_original": _opaque_text(user_request_original, label="user_request_original"),
            "user_language": _task_language(user_language),
            "task_contract_version": _required_text(task_contract_version, label="task_contract_version", maximum=64),
            "requirements": normalized_requirements,
            "constraints": _contract_text_list(constraints, label="constraints"),
            "acceptance_criteria": normalized_acceptance,
            "verification_plan": normalized_verification,
            "outcome_contracts": _linked_outcome_contracts(
                outcome_contracts, requirements=normalized_requirements,
            ),
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
            # This is the canonical half of compact task routing and shares
            # the task creation transaction.  The root-local sidecar is only
            # published after commit, so a process crash can cause a bounded
            # recovery scan but can never leave a locator-only task.
            connection.execute(
                "INSERT INTO task_locator_publications(task_id,project_hash,suffix,fingerprint,created_at) VALUES (?, ?, ?, ?, ?)",
                (identifier, self.project_hash, identifier[-12:], self._task_locator_fingerprint(identifier), timestamp),
            )
            connection.execute("INSERT INTO effective_contract_revisions(task_id,revision,decision_id,created_sequence) VALUES (?, 1, NULL, ?)", (identifier, sequence))
            for ordinal, outcome in enumerate(payload["outcome_contracts"]):
                item_id = "outcome-" + uuid.uuid4().hex
                connection.execute(
                    "INSERT INTO effective_contract_items(item_id,project_hash,task_id,category,ordinal,text,created_revision,retired_revision) VALUES (?, ?, ?, 'outcome', ?, ?, 1, NULL)",
                    (item_id, self.project_hash, identifier, ordinal, outcome["requirement"]),
                )
                connection.execute(
                    "INSERT INTO effective_contract_item_details(item_id,details_json,source_decision_id) VALUES (?, ?, NULL)",
                    (item_id, _canonical_json(_initial_outcome_details(outcome, ordinal), label="effective contract item details")),
                )
            from cortex_runtime.graph_ledger import ensure_bootstrap
            ensure_bootstrap(connection, task_id=identifier, outcomes=payload["requirements"])
            return {"task": self._task(connection, identifier)}
        return self._mutation("create_task", payload, idempotency_key, write)

    def node_admission_snapshot(self, *, graph_id: str) -> dict[str, Any]:
        """Private connection-bound read authority; never an LLM-supplied token."""
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            from cortex_runtime import graph_ledger
            record, graph = graph_ledger._graph(connection, graph_id)
            return {"graph": graph_id, "digest": graph.digest, "revision": record["revision"],
                    "barrier_epoch": connection.execute("SELECT barrier_epoch FROM project_integrity WHERE singleton=1").fetchone()[0],
                    "native_observation": None,
                    "generation": connection.execute("SELECT generation_key FROM project_integrity WHERE singleton=1").fetchone()[0],
                    "owners": {row[0]: row[1] for row in connection.execute(
                        "SELECT node_key,assignment_id FROM execution_nodes WHERE graph_id=?", (graph_id,))}}
        return self._read(read)

    def open_node_assignment(self, *, task_id: str, graph_id: str, graph_digest: str,
                             node_keys: list[str], profile_name: str, model: str,
                             reasoning_effort: str, bootstrap_provenance: Mapping[str, str],
                             admission: Mapping[str, Any], bootstrap_kind: str | None = None,
                             bootstrap_question: str | None = None, native_plugin_data: Path | None = None,
                             native_task_ref: str | None = None, recover: bool = False) -> tuple[dict[str, Any], bool]:
        """Claim graph nodes, bind their derived scope, and mint one dispatch atomically."""
        from cortex_runtime import graph_ledger
        from cortex_runtime.execution_graph import GraphError
        profile = _profile_name(profile_name)
        try:
            selection = validate_model_selection(model, reasoning_effort)
        except ValueError as exc:
            raise V12StoreError("model selection is invalid", code="invalid_model_selection") from exc
        anchor = self._task_identifier(task_id)
        if not isinstance(recover, bool) or (recover and bootstrap_kind is not None):
            raise V12StoreError("loss recovery requires exact nodes", code="invalid_argument")
        if not isinstance(node_keys, list) or any(not isinstance(key, str) for key in node_keys):
            raise V12StoreError("node selection is invalid", code="invalid_argument")
        if bootstrap_kind is not None:
            if node_keys or bootstrap_kind not in {"planning", "discovery"}:
                raise V12StoreError("bootstrap selection is invalid", code="invalid_argument")
            if bootstrap_kind == "planning":
                if bootstrap_question is not None:
                    raise V12StoreError("planning scope is server-derived", code="invalid_argument")
                bootstrap_question = "Plan the complete current contract from finalized baseline and discovery evidence."
            elif not isinstance(bootstrap_question, str) or not bootstrap_question.strip():
                raise V12StoreError("a bounded evidence question is required", code="invalid_argument")
            existing = admission.get("owners", {})
            if not isinstance(existing, Mapping):
                raise V12StoreError("bootstrap scope read is required", code="assignment_stale")
            prefix = "plan" if bootstrap_kind == "planning" else "discovery"
            node_keys = [f"{prefix}-{1 + sum(key.startswith(prefix + '-') for key in existing)}"]
        elif not node_keys or bootstrap_question is not None:
            raise V12StoreError("node selection is invalid", code="invalid_argument")
        def admission_snapshot(connection: sqlite3.Connection) -> tuple[str | None, list[str | None]]:
            generation = connection.execute("SELECT generation_key FROM project_integrity WHERE singleton=1").fetchone()[0]
            previous = []
            for key in sorted(node_keys):
                row = connection.execute("SELECT assignment_id FROM execution_nodes WHERE graph_id=? AND node_key=?", (graph_id, key)).fetchone()
                previous.append(row[0] if row else None)
            return generation, previous
        # The immutable scope-read snapshot identifies the logical command.
        # Recomputing previous owners here makes our own successful claim alter
        # its retry slot, breaking reconciliation after a lost response.
        if admission.get("graph") != graph_id or admission.get("digest") != graph_digest or not isinstance(admission.get("owners"), Mapping):
            raise V12StoreError("node scope read is required", code="assignment_stale")
        if bootstrap_kind is None and any(key not in admission["owners"] for key in node_keys):
            raise V12StoreError("node is absent from scope read", code="assignment_stale")
        generation = admission["generation"]
        previous = [admission["owners"].get(key) for key in sorted(node_keys)]
        request = {"task": anchor, "graph": graph_id, "graph_digest": graph_digest, "nodes": sorted(node_keys),
                   "barrier_epoch": admission["barrier_epoch"], "native_observation": admission["native_observation"],
                   "profile": profile, "model": selection.model, "effort": selection.reasoning_effort,
                   "generation": generation, "bootstrap_kind": bootstrap_kind, "bootstrap_question": bootstrap_question,
                   "recover": recover}
        slot = "graph-assignment:" + hashlib.sha256(_canonical_json([graph_id, sorted(node_keys), generation, previous], label="node claim slot").encode()).hexdigest()
        def mutate(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, anchor)
            self._require_no_pending_user_decision(connection, task_id=anchor)
            closure = connection.execute("SELECT evidence_json FROM governance_closures WHERE subject_type='task' AND subject_id=? ORDER BY created_sequence DESC LIMIT 1", (anchor,)).fetchone()
            if closure is not None and json.loads(closure[0]).get("revision") == graph_ledger._current_revision(connection, anchor):
                raise V12StoreError("the current task revision is closed", code="task_closed")
            if connection.execute("SELECT 1 FROM governance_assessments WHERE task_id=? LIMIT 1", (anchor,)).fetchone() is None:
                raise V12StoreError("governance assessment is required", code="governance_assessment_required")
            if admission_snapshot(connection) != (generation, previous):
                raise V12StoreError("node selection changed before admission", code="assignment_stale")
            epoch = connection.execute("SELECT barrier_epoch FROM project_integrity WHERE singleton=1").fetchone()[0]
            if epoch != admission["barrier_epoch"]:
                raise V12StoreError("project barrier changed before admission", code="assignment_stale")
            native = None
            if native_plugin_data is not None and native_task_ref is not None:
                from cortex_runtime.native_observation import verified_projection, digest
                native = verified_projection(Path(native_plugin_data), task_digest=digest(native_task_ref),
                    revision=graph_ledger._current_revision(connection, anchor), barrier_epoch=epoch)
            if native != admission["native_observation"]:
                raise V12StoreError("native observation changed before admission", code="assignment_stale")
            claim_graph, claim_keys, claim_digest = graph_id, node_keys, graph_digest
            parent = None
            if recover:
                claim_graph, recovered_node, recovered_parents = graph_ledger.begin_loss_reconciliation(connection,
                    graph_id=graph_id, node_keys=node_keys, observation=native)
                for lost in recovered_parents:
                    connection.execute("UPDATE worker_capabilities SET state='stale',updated_at=? WHERE assignment_id=? AND state IN ('minted','consumed')", (_now(), lost))
                if recovered_node is None:
                    self._timeline(connection, task_id=anchor, entity_type="task", entity_id=anchor,
                                   event_type="recovery_exhausted", payload={"nodes": sorted(node_keys)})
                    return {"state": "exhausted", "dispatched": False, "nodes": sorted(node_keys)}
                claim_keys = [recovered_node]
                claim_digest = graph_ledger._graph(connection, claim_graph)[1].digest
                parent = recovered_parents[0] if len(recovered_parents) == 1 else None
                # The signed observation was checked before our own atomic
                # epoch change. Lost routes are now durably quiescent; no old
                # observation is relabelled as a new-epoch host observation.
                native = None
            if bootstrap_kind is not None:
                current_owners = {row[0]: row[1] for row in connection.execute(
                    "SELECT node_key,assignment_id FROM execution_nodes WHERE graph_id=?", (graph_id,))}
                if current_owners != admission["owners"]:
                    raise V12StoreError("bootstrap scope changed before admission", code="assignment_stale")
                graph_ledger.append_bootstrap_node(connection, graph_id=graph_id, kind=bootstrap_kind,
                    key=node_keys[0], question=bootstrap_question)
            identifier = new_sharded_id("delegation", self.project_hash)
            marker = "dc_" + uuid.uuid4().hex
            marker_digest = "sha256:" + hashlib.sha256(marker.encode()).hexdigest()
            native_name = self._next_native_task_name(connection, task_id=anchor, profile_name=profile) + "_d_" + marker_digest[7:19]
            scope = graph_ledger.claim_nodes(connection, graph_id=claim_graph, task_id=anchor,
                expected_digest=claim_digest, node_keys=claim_keys, assignment_id=identifier, protected_task_name=native_name,
                native_observation=native)
            nodes = scope["nodes"]
            responsibilities = {node["responsibility"] for node in nodes}
            if len(responsibilities) != 1:
                raise V12StoreError("node responsibilities conflict", code="invalid_argument")
            responsibility = next(iter(responsibilities))
            node_brief = {key: value for key, value in scope.items() if key != "predecessor_reports"}
            objective = " / ".join(node["owner"] for node in nodes)
            instructions = _canonical_json(node_brief, label="node assignment scope")
            reports = scope["predecessor_reports"]
            for report_id in reports:
                self._report(connection, report_id, task_id=anchor)
            sequence = self._timeline(connection, event_type="delegation_created", entity_type="delegation", entity_id=identifier,
                payload={"nodes": list(claim_keys), "native_task_name": native_name}, task_id=anchor, delegation_id=identifier)
            connection.execute("INSERT INTO delegations(delegation_id,project_hash,task_id,parent_delegation_id,native_task_name,dispatch_correlation_marker,dispatch_correlation_digest,objective,role,profile_name,scope,instructions,input_report_ids_json,input_decision_ids_json,model,reasoning_effort,created_at,created_sequence) VALUES (?,?,?,NULL,?,?,?,?,?,?,?,?,?,'[]',?,?,?,?)",
                (identifier, self.project_hash, anchor, native_name, marker, marker_digest, objective, responsibility, profile,
                 "Graph nodes: " + ", ".join(claim_keys), instructions, _canonical_json(reports, label="predecessor reports"),
                 selection.model, selection.reasoning_effort, _now(), sequence))
            if parent is None:
                lost_parents = {owner for owner in previous if owner is not None and connection.execute("SELECT 1 FROM execution_assignments WHERE assignment_id=? AND state='lost' AND quiescent=1", (owner,)).fetchone()}
                if len(lost_parents) == 1:
                    parent = next(iter(lost_parents))
            if parent is not None:
                connection.execute("UPDATE delegations SET parent_delegation_id=? WHERE delegation_id=?", (parent, identifier))
            names = {subject["name"] for node in nodes for subject in node["verifies"] if subject["kind"] == "outcome"}
            graph = graph_ledger._graph(connection, claim_graph)[1].data()
            produced = {contribution for node in nodes for contribution in node["contributions"]}
            produced.update(subject["name"] for node in nodes for subject in node["verifies"] if subject["kind"] == "contribution")
            names.update(item["outcome"] for item in graph["outcomes"] if produced.intersection(item["all_of"]))
            if responsibility == "planning":
                names = {item["text"] for item in self._effective_contract(connection, anchor)["items"]}
            revision = graph_ledger._current_revision(connection, anchor)
            for item in self._effective_contract(connection, anchor)["items"]:
                if item["text"] not in names:
                    continue
                item_id = self._outcome_item_id(connection, anchor, item["item_ref"])
                connection.execute("INSERT INTO assignment_scope_snapshots VALUES (?,?,?,?,?,?)",
                    (identifier, anchor, item_id, "planning" if responsibility == "planning" else "evidence", revision, sequence))
            self._mint_worker_bootstrap_in_transaction(connection, task_id=anchor, assignment_id=identifier,
                contract_revision=revision, dispatch_digest=marker_digest, **dict(bootstrap_provenance))
            delegation = self._delegation(connection, identifier, task_id=anchor)
            brief = self._worker_brief(connection, task, delegation)
            return {"delegation": delegation, "dispatch_brief": brief["dispatch_brief"], "renderer": brief["renderer"]}
        try:
            return self.run_command_receipt(aggregate_type="task", aggregate_id=anchor,
                command_name="open_assignment", logical_slot=slot, request=request, mutate=mutate)
        except GraphError as exc:
            raise V12StoreError("typed node assignment is not admissible", code="assignment_not_ready", details={"reason": exc.reason}) from exc

    @staticmethod
    def _worker_capability_ref(value: Any, *, label: str) -> str:
        candidate = _required_text(value, label=label, maximum=64)
        if re.fullmatch(r"w[bc]_[0-9a-f]{32}", candidate) is None:
            raise V12StoreError(f"{label} is invalid", code="invalid_argument", details={"field": label})
        return candidate

    def mint_worker_bootstrap(
        self, *, task_id: Any, assignment_id: Any, contract_revision: Any,
        build_digest: Any, candidate_digest: Any, source_digest: Any,
        catalogue_digest: Any, dispatch_digest: Any,
    ) -> dict[str, Any]:
        """Mint or exactly replay one assignment-bound bootstrap capability.

        This method is deliberately a single durable transaction.  The API
        layer must call it immediately after assignment creation; it must not
        manufacture or persist the opaque values itself.
        """
        task_key = self._task_identifier(task_id)
        assignment_key = self._record_identifier(assignment_id, label="assignment_id")
        try:
            revision = int(contract_revision)
        except (TypeError, ValueError) as exc:
            raise V12StoreError("contract_revision is invalid", code="invalid_argument", details={"field": "contract_revision"}) from exc
        if revision < 1:
            raise V12StoreError("contract_revision is invalid", code="invalid_argument", details={"field": "contract_revision"})
        digests = tuple(_digest(value, label=label, required=True) for label, value in (
            ("build_digest", build_digest), ("candidate_digest", candidate_digest),
            ("source_digest", source_digest), ("catalogue_digest", catalogue_digest),
            ("dispatch_digest", dispatch_digest),
        ))
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            return self._mint_worker_bootstrap_in_transaction(
                connection, task_id=task_key, assignment_id=assignment_key,
                contract_revision=revision, build_digest=digests[0],
                candidate_digest=digests[1], source_digest=digests[2],
                catalogue_digest=digests[3], dispatch_digest=digests[4],
            )
        return self._write(write)

    def _mint_worker_bootstrap_in_transaction(
        self, connection: sqlite3.Connection, *, task_id: str, assignment_id: str,
        contract_revision: int, build_digest: Any, candidate_digest: Any,
        source_digest: Any, catalogue_digest: Any, dispatch_digest: Any,
    ) -> dict[str, Any]:
        """Mint a capability on the caller's transaction after dispatch exists."""
        task = self._task(connection, task_id)
        assignment = self._delegation(connection, assignment_id, task_id=task["task_id"])
        digests = tuple(_digest(value, label=label, required=True) for label, value in (
            ("build_digest", build_digest), ("candidate_digest", candidate_digest),
            ("source_digest", source_digest), ("catalogue_digest", catalogue_digest),
            ("dispatch_digest", dispatch_digest),
        ))
        if str(assignment["dispatch_correlation_digest"] or "") != str(digests[4]):
            raise V12StoreError("dispatch binding is stale", code="capability_stale")
        existing = connection.execute("SELECT * FROM worker_capabilities WHERE assignment_id=? AND contract_revision=?", (assignment_id, contract_revision)).fetchone()
        values = (self.project_hash, task_id, assignment_id, contract_revision, *digests)
        if existing is not None:
            if tuple(existing[name] for name in ("project_hash", "task_id", "assignment_id", "contract_revision", "build_digest", "candidate_digest", "source_digest", "catalogue_digest", "dispatch_digest")) != values:
                raise V12StoreError("assignment capability conflicts with existing binding", code="capability_conflict")
            return {"capability": str(existing["capability_ref"]), "assignment_id": assignment_id, "task_id": task_id, "contract_revision": contract_revision, "state": str(existing["state"]), "replayed": True}
        capability = "wb_" + uuid.uuid4().hex
        capability_digest = "sha256:" + hashlib.sha256(capability.encode("utf-8")).hexdigest()
        sequence = self._timeline(connection, event_type="worker_bootstrap_minted", entity_type="delegation", entity_id=assignment_id, task_id=task_id, delegation_id=assignment_id, payload={"assignment_id": assignment_id, "contract_revision": contract_revision, "capability_digest": capability_digest})
        now = _now()
        lease_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=_DISPATCH_LEASE_SECONDS)).isoformat()
        connection.execute("INSERT INTO worker_capabilities(capability_ref,project_hash,task_id,assignment_id,contract_revision,build_digest,candidate_digest,source_digest,catalogue_digest,dispatch_digest,capability_digest,continuation_ref,state,created_sequence,created_at,updated_at,lease_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'minted', ?, ?, ?, ?)", (capability, self.project_hash, task_id, assignment_id, contract_revision, *digests, capability_digest, sequence, now, now, lease_expires_at))
        return {"capability": capability, "assignment_id": assignment_id, "task_id": task_id, "contract_revision": contract_revision, "state": "minted", "replayed": False}

    def consume_worker_bootstrap(
        self, *, capability: Any, task_id: Any, assignment_id: Any, contract_revision: Any,
        build_digest: Any, candidate_digest: Any, source_digest: Any,
        catalogue_digest: Any, dispatch_digest: Any,
    ) -> dict[str, Any]:
        """Consume one exact bootstrap and mint its scoped continuation once."""
        capability_key = self._worker_capability_ref(capability, label="capability")
        task_key = self._task_identifier(task_id)
        assignment_key = self._record_identifier(assignment_id, label="assignment_id")
        try:
            revision = int(contract_revision)
        except (TypeError, ValueError) as exc:
            raise V12StoreError("contract_revision is invalid", code="invalid_argument", details={"field": "contract_revision"}) from exc
        digests = tuple(_digest(value, label=label, required=True) for label, value in (("build_digest", build_digest), ("candidate_digest", candidate_digest), ("source_digest", source_digest), ("catalogue_digest", catalogue_digest), ("dispatch_digest", dispatch_digest)))
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute("SELECT * FROM worker_capabilities WHERE capability_ref=?", (capability_key,)).fetchone()
            return self._consume_worker_bootstrap_row(
                connection, row=row, capability_key=capability_key,
                task_key=task_key, assignment_key=assignment_key,
                revision=revision, digests=digests,
            )
        return self._write(write)

    def worker_capability_state(self, *, task_id: Any, assignment_id: Any) -> str | None:
        """Return the latest assignment capability state without consuming it."""
        task_key = self._task_identifier(task_id)
        assignment_key = self._record_identifier(assignment_id, label="assignment_id")
        row = self._read(lambda connection: connection.execute(
            "SELECT state FROM worker_capabilities WHERE task_id=? AND assignment_id=? "
            "ORDER BY created_sequence DESC LIMIT 1",
            (task_key, assignment_key),
        ).fetchone())
        return str(row["state"]) if row is not None else None

    def _consume_worker_bootstrap_row(
        self, connection: sqlite3.Connection, *, row: sqlite3.Row | None,
        capability_key: str, task_key: str, assignment_key: str,
        revision: int, digests: tuple[str, ...],
    ) -> dict[str, Any]:
        """Atomically consume a checked assignment capability row."""
        if row is None:
            raise V12StoreError("worker capability was not found", code="capability_not_found")
        expected = (self.project_hash, task_key, assignment_key, revision, *digests)
        actual = tuple(row[name] for name in ("project_hash", "task_id", "assignment_id", "contract_revision", "build_digest", "candidate_digest", "source_digest", "catalogue_digest", "dispatch_digest"))
        if actual != expected:
            raise V12StoreError("worker capability is stale or belongs to another scope", code="assignment_stale")
        if str(row["state"]) == "consumed":
            return {"continuation": str(row["continuation_ref"]), "assignment_id": assignment_key, "task_id": task_key, "state": "consumed", "replayed": True}
        if str(row["state"]) != "minted":
            raise V12StoreError("worker capability is not consumable", code="assignment_stale")
        continuation = "wc_" + uuid.uuid4().hex
        sequence = self._timeline(connection, event_type="worker_bootstrap_consumed", entity_type="delegation", entity_id=assignment_key, task_id=task_key, delegation_id=assignment_key, payload={"assignment_id": assignment_key, "capability_digest": str(row["capability_digest"])})
        now = _now()
        connection.execute("UPDATE worker_capabilities SET continuation_ref=?,state='consumed',consumed_sequence=?,updated_at=? WHERE capability_ref=? AND state='minted'", (continuation, sequence, now, capability_key))
        return {"continuation": continuation, "assignment_id": assignment_key, "task_id": task_key, "state": "consumed", "replayed": False}

    def consume_worker_bootstrap_for_assignment(
        self, *, task_id: Any, assignment_id: Any, contract_revision: Any,
        build_digest: Any, candidate_digest: Any, source_digest: Any,
        catalogue_digest: Any, dispatch_digest: Any,
    ) -> dict[str, Any]:
        """Consume the sole server-owned bootstrap for an assignment.

        The worker supplies only its non-secret assignment locator. The
        capability row remains private server state and is selected inside the
        same write transaction that transitions ``minted`` to ``consumed``;
        concurrent calls therefore produce one continuation and at most one
        first consumption.
        """
        task_key = self._task_identifier(task_id)
        assignment_key = self._record_identifier(assignment_id, label="assignment_id")
        # The capability is an assignment snapshot, not a task-global
        # contract lease.  A user steering revision may be committed while a
        # worker is in flight; selecting the row by assignment first lets a
        # replay receive the refreshed brief instead of manufacturing a
        # replacement assignment or failing against the newer task revision.
        digests = tuple(_digest(value, label=label, required=True) for label, value in (("build_digest", build_digest), ("candidate_digest", candidate_digest), ("source_digest", source_digest), ("catalogue_digest", catalogue_digest), ("dispatch_digest", dispatch_digest)))

        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute(
                "SELECT * FROM worker_capabilities WHERE task_id=? AND assignment_id=? ORDER BY created_sequence DESC LIMIT 1",
                (task_key, assignment_key),
            ).fetchone()
            capability_key = str(row["capability_ref"]) if row is not None else "server-owned-assignment-bootstrap"
            revision = int(row["contract_revision"]) if row is not None else 1
            current_revision = int(self._effective_contract(connection, task_key)["revision"])
            if revision != current_revision:
                raise V12StoreError(
                    "worker assignment is stale after a contract revision",
                    code="assignment_stale",
                )
            return self._consume_worker_bootstrap_row(
                connection, row=row, capability_key=capability_key,
                task_key=task_key, assignment_key=assignment_key,
                revision=revision, digests=digests,
            )
        return self._write(write)

    def resolve_worker_continuation(self, *, continuation: Any) -> dict[str, Any]:
        """Resolve a consumed continuation only for the current contract."""
        continuation_key = self._worker_capability_ref(continuation, label="continuation")
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute(
                "SELECT task_id,assignment_id,contract_revision,state FROM worker_capabilities WHERE continuation_ref=?",
                (continuation_key,),
            ).fetchone()
            if row is None or str(row["state"]) != "consumed":
                raise V12StoreError("worker continuation is invalid", code="assignment_stale")
            current_revision = int(self._effective_contract(connection, str(row["task_id"]))["revision"])
            if int(row["contract_revision"]) != current_revision:
                raise V12StoreError(
                    "worker continuation is stale after a contract revision",
                    code="assignment_stale",
                )
            return {"continuation": continuation_key, "task_id": str(row["task_id"]), "assignment_id": str(row["assignment_id"]), "contract_revision": int(row["contract_revision"]), "state": "consumed"}
        return self._read(read)

    def _publication_authority(self, connection: sqlite3.Connection, *, continuation: str,
                               assignment_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT task_id,assignment_id,contract_revision,state,consumed_sequence "
            "FROM worker_capabilities WHERE continuation_ref=?", (continuation,),
        ).fetchone()
        if row is None or str(row["assignment_id"]) != assignment_id or row["consumed_sequence"] is None:
            raise V12StoreError("publication capability does not belong to this consumed assignment", code="wrong_connection")
        revision = int(row["contract_revision"])
        current = int(self._effective_contract(connection, str(row["task_id"]))["revision"])
        superseded = str(row["state"]) == "stale" and revision < current
        if not superseded and (str(row["state"]) != "consumed" or revision != current):
            raise V12StoreError("publication capability is invalid", code="assignment_stale")
        return {
            "task_id": str(row["task_id"]), "assignment_id": assignment_id,
            "contract_revision": revision, "superseded": superseded,
        }

    def publication_authority(self, *, continuation: Any, assignment_id: Any) -> dict[str, Any]:
        key = self._worker_capability_ref(continuation, label="continuation")
        assignment = self._record_identifier(assignment_id, label="assignment_id")
        return self._read(lambda connection: self._publication_authority(
            connection, continuation=key, assignment_id=assignment,
        ))

    def record_assignment_page_receipt(
        self, *, task_id: Any, assignment_id: Any,
        snapshot_digest: Any, phase: Any, private_position: Any,
        page_digest: Any, returned_content_bytes: Any, has_more: Any,
    ) -> dict[str, Any]:
        """Record or reconcile one exact deterministic assignment page.

        The position is private server state.  It is accepted only in strict
        sequence for one immutable snapshot, and an exact repeated page is a
        read reconciliation rather than another authority grant or timeline
        mutation.
        """
        task_key = self._task_identifier(task_id)
        assignment_key = self._record_identifier(assignment_id, label="assignment_id")
        snapshot_key = _digest(snapshot_digest, label="snapshot_digest", required=True)
        page_key = _digest(page_digest, label="page_digest", required=True)
        if phase not in {"complete", "authority", "evidence"}:
            raise V12StoreError(
                "assignment page phase is invalid", code="invalid_argument",
                details={"field": "phase"},
            )
        if (
            not isinstance(private_position, int)
            or isinstance(private_position, bool)
            or private_position < 0
        ):
            raise V12StoreError(
                "assignment page position is invalid", code="invalid_argument",
                details={"field": "private_position"},
            )
        if (
            not isinstance(returned_content_bytes, int)
            or isinstance(returned_content_bytes, bool)
            or not 0 <= returned_content_bytes <= REPORT_RESPONSE_MAX_BYTES
        ):
            raise V12StoreError(
                "assignment page byte count is invalid", code="invalid_argument",
                details={"field": "returned_content_bytes"},
            )
        if not isinstance(has_more, bool):
            raise V12StoreError(
                "assignment page continuation state is invalid", code="invalid_argument",
                details={"field": "has_more"},
            )

        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            self._task(connection, task_key)
            self._delegation(connection, assignment_key, task_id=task_key)
            capability = connection.execute(
                "SELECT state,contract_revision FROM worker_capabilities "
                "WHERE task_id=? AND assignment_id=?",
                (task_key, assignment_key),
            ).fetchone()
            current_revision = int(self._effective_contract(connection, task_key)["revision"])
            if (
                capability is None
                or str(capability["state"]) != "consumed"
                or int(capability["contract_revision"]) != current_revision
            ):
                raise V12StoreError(
                    "assignment page requires consumed worker authority",
                    code="assignment_stale",
                )
            prior_snapshots = connection.execute(
                "SELECT DISTINCT snapshot_digest FROM assignment_page_receipts "
                "WHERE assignment_id=?",
                (assignment_key,),
            ).fetchall()
            if prior_snapshots and any(
                str(row["snapshot_digest"]) != snapshot_key for row in prior_snapshots
            ):
                raise V12StoreError(
                    "assignment page snapshot is stale",
                    code="report_cursor_stale",
                    details={"field": "continue", "expected": "same_assignment_snapshot"},
                )
            existing = connection.execute(
                "SELECT * FROM assignment_page_receipts "
                "WHERE assignment_id=? AND snapshot_digest=? AND private_position=?",
                (assignment_key, snapshot_key, private_position),
            ).fetchone()
            if existing is not None:
                expected = (
                    phase, page_key, returned_content_bytes, int(has_more),
                )
                actual = (
                    str(existing["phase"]), str(existing["page_digest"]),
                    int(existing["returned_content_bytes"]), int(existing["has_more"]),
                )
                if actual != expected:
                    raise V12StoreError(
                        "assignment page conflicts with its durable receipt",
                        code="report_cursor_invalid",
                        details={"field": "continue", "expected": "immediately_preceding_identical_read"},
                    )
                return {
                    "replayed": True,
                    "created_sequence": int(existing["created_sequence"]),
                }
            latest = connection.execute(
                "SELECT private_position,has_more FROM assignment_page_receipts "
                "WHERE assignment_id=? AND snapshot_digest=? "
                "ORDER BY private_position DESC LIMIT 1",
                (assignment_key, snapshot_key),
            ).fetchone()
            expected_position = 0 if latest is None else int(latest["private_position"]) + 1
            if (
                private_position != expected_position
                or (latest is not None and not bool(latest["has_more"]))
            ):
                raise V12StoreError(
                    "assignment page is out of sequence",
                    code="report_cursor_invalid",
                    details={"field": "continue", "expected": "immediately_preceding_identical_read"},
                )
            sequence = self._timeline(
                connection,
                event_type="assignment_page_read",
                entity_type="assignment_consumption",
                entity_id=assignment_key,
                task_id=task_key,
                delegation_id=assignment_key,
                payload={
                    "assignment_id": assignment_key,
                    "snapshot_digest": snapshot_key,
                    "phase": phase,
                    "private_position": private_position,
                    "page_digest": page_key,
                    "returned_content_bytes": returned_content_bytes,
                    "has_more": has_more,
                },
            )
            connection.execute(
                "INSERT INTO assignment_page_receipts("
                "project_hash,task_id,assignment_id,snapshot_digest,phase,"
                "private_position,page_digest,returned_content_bytes,has_more,"
                "created_at,created_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.project_hash, task_key, assignment_key, snapshot_key,
                    phase, private_position, page_key, returned_content_bytes,
                    int(has_more), _now(), sequence,
                ),
            )
            return {"replayed": False, "created_sequence": sequence}

        return self._write(write)

    def validate_worker_continuation(self, *, continuation: Any, task_id: Any, assignment_id: Any, contract_revision: Any) -> dict[str, Any]:
        """Read-only validation of a consumed continuation scope."""
        continuation_key = self._worker_capability_ref(continuation, label="continuation")
        task_key = self._task_identifier(task_id)
        assignment_key = self._record_identifier(assignment_id, label="assignment_id")
        try:
            revision = int(contract_revision)
        except (TypeError, ValueError) as exc:
            raise V12StoreError("contract_revision is invalid", code="invalid_argument", details={"field": "contract_revision"}) from exc
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute("SELECT task_id,assignment_id,contract_revision,state FROM worker_capabilities WHERE continuation_ref=?", (continuation_key,)).fetchone()
            current_revision = int(self._effective_contract(connection, task_key)["revision"])
            if (
                row is None
                or tuple(row) != (task_key, assignment_key, revision, "consumed")
                or revision != current_revision
            ):
                raise V12StoreError("worker continuation is invalid", code="assignment_stale")
            return {"continuation": continuation_key, "task_id": task_key, "assignment_id": assignment_key, "contract_revision": revision, "state": "consumed"}
        return self._read(read)

    def _worker_brief(self, connection: sqlite3.Connection, task: Mapping[str, Any], delegation: Mapping[str, Any]) -> dict[str, Any]:
        """Project one typed assignment and its complete scoped contract context."""
        from cortex_runtime.delegation import dispatch_brief_projection
        from cortex_runtime.worker_message import render_worker_message

        decisions = [self._decision(connection, item, task_id=str(task["task_id"])) for item in delegation["input_decision_ids"]]
        input_reports = [self._report(connection, item, task_id=str(task["task_id"])) for item in delegation["input_report_ids"]]
        if any(item["assembly_state"] != "finalized" for item in input_reports):
            raise V12StoreError("input handoff report is not finalized", code="report_state_conflict")
        snapshot_row = connection.execute(
            "SELECT revision FROM execution_assignments WHERE assignment_id=?",
            (str(delegation["delegation_id"]),),
        ).fetchone()
        if snapshot_row is None:
            raise V12StoreError("typed assignment is unavailable", code="ledger_corrupt")
        revision = int(snapshot_row["revision"])
        assignment_rows = connection.execute(
            "SELECT DISTINCT i.item_id,i.category,i.ordinal,i.text,i.created_revision,d.details_json,d.source_decision_id FROM assignment_scope_snapshots a "
            "JOIN effective_contract_items i ON i.item_id=a.item_id "
            "JOIN effective_contract_item_details d ON d.item_id=i.item_id "
            "WHERE a.assignment_id=? AND a.contract_revision=? "
            "AND (i.retired_revision IS NULL OR i.retired_revision>?) ORDER BY i.category,i.ordinal,i.item_id",
            (delegation["delegation_id"], revision, revision),
        ).fetchall()
        outcomes = []
        for row in assignment_rows:
            item = self._contract_item_view(_row(row) or {})
            outcomes.append({"outcome": item["text"], "acceptance": item["acceptance_criteria"],
                "constraints": item["constraints"], "verification": item["verification_criteria"]})
        contract_context = {"revision": revision, "outcomes": outcomes,
                            "task_constraints": task["constraints"], "context": task["context"]}
        from cortex_runtime.graph_ledger import assignment_scope
        node_scope = assignment_scope(connection, str(delegation["delegation_id"]))
        if node_scope["execution_mode"] != "artifact_independent":
            from cortex_runtime.artifact_fingerprint import archive_path
            archive = archive_path(self._codex_home, self.project_hash)
            artifact = node_scope["artifact"]
            command = ["python3", str(Path(__file__).with_name("artifact_fingerprint.py")),
                "--project-root", str(self.project_root),
                "--archive-root", str(archive),
                "--method", artifact["method"] or "auto"]
            for path in artifact["paths"]:
                command.extend(["--artifact-path", path])
            for domain in sorted({domain for node in node_scope["nodes"] for domain in node["mutation_domains"]}):
                command.extend(["--mutation-domain", domain])
            artifact["worker_procedure"] = {
                "command": command,
                "comparison_option": "--compare",
                "comparison_limit": 2,
                "instructions": "Execute this worker-owned procedure before work and immediately before publication. "
                    "For each comparison append the comparison option and the previously observed fingerprint as separate arguments. "
                    "Compare the sealed target at the beginning and your start observation at the end. "
                    "The procedure stores hash-only manifests outside the project. An unavailable observation is not evidence of success.",
            }
            if "boundary_target" in artifact:
                boundary = artifact["boundary_target"]
                boundary_command = ["python3", str(Path(__file__).with_name("artifact_fingerprint.py")),
                    "--project-root", str(self.project_root), "--archive-root", str(archive),
                    "--method", boundary["method"]]
                for path in boundary["paths"]:
                    boundary_command.extend(["--artifact-path", path])
                artifact["boundary_procedure"] = {"command": boundary_command,
                    "instructions": "Observe the old sealed target first with the ordinary procedure. Then run this new-boundary procedure twice, comparing the second observation only to its own first manifest. Finally observe the old boundary again and compare it to the sealed target. Do not compare fingerprints from different path boundaries or methods. Both enclosed observations and both outer observations must be stable; do not change files."}
        node_scope["terminal_publication_kind"] = node_scope.pop("terminal_kind")
        report_refs = [
            {key: item[key] for key in ("report_id", "delegation_id", "report_type", "status", "assembly_state", "total_chunks", "content_digest")}
            for item in input_reports
        ]
        # The capability remains a private server-side lease. It is never
        # rendered into the worker message; the worker consumes by the exact
        # assignment locator and the server resolves the one-time capability
        # inside its atomic transaction.
        rendered = render_worker_message(task=task, delegation=delegation)
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
        dispatch_brief = dispatch_brief_projection(
            task_name=delegation["native_task_name"],
            message=rendered["message"],
            model=delegation["model"],
            reasoning_effort=delegation["reasoning_effort"],
            delegation_ref=record_ref(str(delegation["delegation_id"])),
            task_ref=task_ref(str(task["task_id"])),
            project_root=str(self.project_root),
            semantic_objective=delegation["objective"],
            profile_proof=renderer,
            dispatch_correlation_marker=delegation.get("dispatch_correlation_marker"),
            dispatch_correlation_digest=delegation.get("dispatch_correlation_digest"),
        )
        return {
            "delegation_id": delegation["delegation_id"], "task_id": delegation["task_id"],
            "project_root": str(self.project_root), "objective": delegation["objective"],
            "role": delegation["role"], "profile_name": delegation["profile_name"], "scope": delegation["scope"],
            "native_task_name": delegation["native_task_name"],
            "instructions": delegation["instructions"], "input_report_ids": list(delegation["input_report_ids"]),
            "input_report_refs": report_refs,
            "report_inputs": {"state": "none" if not report_refs else "declared", "reports": report_refs},
            "input_decision_ids": list(delegation["input_decision_ids"]),
            "input_decisions": [
                {key: item[key] for key in ("decision_id", "subject_type", "subject_id", "subject_digest", "decision_type", "prompt", "response_original", "user_language")}
                for item in decisions
            ],
            "decision_inputs": {
                "state": "none" if not decisions else "declared",
                "decisions": [
                    {key: item[key] for key in ("decision_id", "subject_type", "subject_id", "subject_digest", "decision_type", "prompt", "response_original", "user_language")}
                    for item in decisions
                ],
            },
            "model": delegation["model"], "reasoning_effort": delegation["reasoning_effort"],
            "assignment": node_scope, "contract_context": contract_context,
            "worker_message": rendered["message"], "renderer": rendered["renderer"],
            "dispatch_brief": dispatch_brief,
        }

    def _task_for_delegation(self, delegation_id: Any, task_id: Any = None) -> tuple[str, str]:
        """Derive the exact owner task from its immutable assignment."""
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


    def publish_node_report(self, *, delegation_id: str, continuation_ref: str,
                            kind: str, content: Mapping[str, Any], review_required: bool = False) -> dict[str, Any]:
        """One transaction for authenticated graph transition, report and receipt."""
        from cortex_runtime import graph_ledger
        from cortex_runtime.execution_graph import GraphError
        from cortex_runtime.typed_publications import validate_report
        anchor, assignment = self._task_for_delegation(delegation_id)
        continuation = self._worker_capability_ref(continuation_ref, label="continuation")

        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            authority = self._publication_authority(connection, continuation=continuation, assignment_id=assignment)
            if authority["superseded"]:
                return {"state": "superseded", "published": False, "replayed": False}
            row = connection.execute("SELECT terminal_kind,state FROM execution_assignments WHERE assignment_id=?", (assignment,)).fetchone()
            if row is None:
                raise GraphError("assignment_missing")
            if row["state"] == "snapshot_conflict":
                return {"state": "snapshot_conflict", "published": False, "replayed": False}
            if kind != row["terminal_kind"]:
                raise GraphError("publication_kind_not_permitted")
            validate_report(kind, content)
            canonical = _canonical_json_bytes(content, label="typed report")
            if canonical[2] > REPORT_MAX_BYTES:
                raise V12StoreError("report is too large", code="report_too_large")
            report_id = new_sharded_id("report", self.project_hash)
            required_review = review_required
            if kind == "plan":
                assessment = connection.execute("SELECT a.mode,p.user_review_requested FROM governance_assessments a LEFT JOIN execution_policies p ON p.assessment_id=a.assessment_id WHERE a.task_id=? ORDER BY a.created_sequence DESC LIMIT 1", (anchor,)).fetchone()
                if assessment is None:
                    raise V12StoreError("governance assessment is required", code="governance_assessment_required")
                required_review = required_review or graph_ledger.pending_governance_review(connection, anchor)
                publication = graph_ledger.publish_candidates(connection, assignment_id=assignment, report_id=report_id,
                    candidates=content["candidates"], artifact=content["artifact"], review_required=required_review,
                    report_content={key: content[key] for key in ("status", "summary", "scope", "risks", "unresolved")})
                required_review = required_review or len(content["candidates"]) > 1 or any(
                    item["delta"]["add"] or item["delta"]["retire"] for item in content["candidates"])
            else:
                publication = graph_ledger.publish_nodes(connection, assignment_id=assignment, report_id=report_id,
                    terminal_kind=kind, node_coverage=content["node_coverage"], artifact=content["artifact"], report_content=content)
            if not publication["published"]:
                self._timeline(connection, event_type="assignment_snapshot_conflict", entity_type="delegation", entity_id=assignment,
                    payload={"state": publication["state"]}, task_id=anchor, delegation_id=assignment)
                return publication
            if publication["replayed"]:
                report = self._report(connection, publication["report_id"], task_id=anchor)
                return {"state": "published", "published": True, "replayed": True, "report": self._compact_report(report)}
            body = dict(content)
            # The graph is the canonical plan expectation source. Do not add
            # a second expanded copy of every node check to the stored body:
            # derived coverage may exceed the caller's admitted byte budget.
            canonical = _canonical_json_bytes(body, label="typed report")
            manifest = _sha256_prefixed(_report_manifest([{"chunk_index": 0, "section": "body", "content_digest": canonical[3], "content_bytes": canonical[2]}]), label="report manifest")
            sequence = self._timeline(connection, event_type="report_submitted", entity_type="report", entity_id=report_id,
                payload={"report_type": kind, "status": content["status"]}, task_id=anchor, delegation_id=assignment, report_id=report_id)
            timestamp = _now()
            policy = ("required" if required_review else "informational") if kind == "plan" else None
            connection.execute("INSERT INTO reports(report_id,project_hash,task_id,delegation_id,report_type,status,semantic_status,assembly_state,next_chunk_index,total_chunks,total_bytes,content_digest,review_policy,created_at,created_sequence,finalized_at,finalized_sequence) VALUES (?,?,?,?,?,?,?,'finalized',1,1,?,?,?,?,?,?,?)",
                (report_id, self.project_hash, anchor, assignment, kind, content["status"], "semantic_valid", canonical[2], manifest, policy, timestamp, sequence, timestamp, sequence))
            connection.execute("INSERT INTO report_chunks(report_id,chunk_index,section,content_json,content_digest,content_bytes,created_at) VALUES (?,0,'body',?,?,?,?)",
                (report_id, canonical[1], canonical[3], canonical[2], timestamp))
            payload_digest = connection.execute("SELECT payload_digest FROM execution_publications WHERE assignment_id=?", (assignment,)).fetchone()[0]
            connection.execute("INSERT INTO report_operations(operation_id,task_id,delegation_id,kind,payload_digest,report_id,created_at) VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, anchor, assignment, kind, payload_digest, report_id, timestamp))
            return {"state": "published", "published": True, "replayed": False,
                    "report": self._compact_report(self._report(connection, report_id, task_id=anchor))}
        try:
            result = self._write(write)
        except GraphError as exc:
            raise V12StoreError("typed publication violates its immutable assignment", code="report_incomplete", details={"reason": exc.reason}) from exc
        if result["published"]:
            view = self._materialize_publication_view(task_id=anchor, report_id=result["report"]["report_id"])
            if kind == "plan":
                result["approval_view"] = view
        return result


    def _materialize_publication_view(self, *, task_id: str, report_id: str) -> dict[str, Any]:
        from cortex_runtime.report_presenters import render_report
        from cortex_runtime.v12_projections import _safe_write

        def snapshot(connection: sqlite3.Connection) -> tuple[dict[str, Any], dict[str, Any], Any]:
            task = self._task(connection, task_id)
            report = self._compact_report(self._report(connection, report_id, task_id=task_id))
            chunks = self._report_chunks(connection, report_id)
            if len(chunks) != 1 or chunks[0].get("section") != "body":
                raise V12StoreError("report projection content is invalid", code="report_incomplete")
            return task, report, chunks[0]["content"]

        task, report, content = self._read(snapshot)
        sequence = int(report["created_sequence"])
        kind = str(report["report_type"])
        relative = f"plans/revisions/{report_id}.md" if kind == "plan" else f"reports/{report_id}.md"
        target = self.root / "tasks" / str(task["task_ref"]) / relative
        try:
            body = render_report(report_type=kind, content=content, report=report).encode("utf-8")
            expected = "sha256:" + hashlib.sha256(body).hexdigest()
            try:
                view_digest = _safe_write(target, body, expected_digest=expected, root=self.root)
            except OSError as exc:
                # Repair only one transient IO failure using the already durable
                # report and the same exact expected bytes. No second report,
                # host retry, unsafe-path override, or external-edit overwrite.
                if exc.errno not in {errno.EINTR, errno.EAGAIN, errno.EIO, errno.ESTALE}:
                    raise
                view_digest = _safe_write(target, body, expected_digest=expected, root=self.root)
        except (OSError, ValueError, UnicodeError) as exc:
            raise V12StoreError("durable report exists but its verified view is unavailable", code="storage_unavailable") from exc

        def bind(connection: sqlite3.Connection) -> dict[str, Any]:
            connection.execute(
                "INSERT INTO projection_files(task_id,relative_path,source_sequence,renderer_version,content_digest,status,updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'ready', ?) ON CONFLICT(task_id,relative_path) DO UPDATE SET "
                "source_sequence=excluded.source_sequence,renderer_version=excluded.renderer_version,"
                "content_digest=excluded.content_digest,status='ready',updated_at=excluded.updated_at",
                (task_id, relative, sequence, PROJECTION_RENDERER_VERSION, view_digest, _now()),
            )
            if kind != "plan":
                return {"status": "ready", "report_id": report_id, "content_digest": view_digest, "source_sequence": sequence}
            relation = self._ready_plan_review_relation(connection, task_id=task_id, report_id=report_id,
                report_content_digest=str(report["content_digest"]), view_relative_path=relative,
                view_content_digest=view_digest, view_source_sequence=sequence)
            return {"status": "ready", "report_id": report_id, "delegation_id": report["delegation_id"],
                    "report_content_digest": relation["plan_content_digest"], "approval_handle": relation["approval_handle"],
                    "content_digest": relation["view_content_digest"], "source_sequence": relation["view_source_sequence"]}

        return self._write(bind)

    def _decision_binding_projection(self, row: Mapping[str, Any], *, task_id: str) -> dict[str, Any]:
        """Project the current ledger state of one exact decision binding."""
        binding = {
                "clarification_binding": str(row["clarification_binding"]),
                "task_ref": task_ref(task_id),
                "subject_type": str(row["subject_type"]),
                "subject_ref": record_ref(str(row["subject_id"])) if str(row["subject_type"]) != "task" else task_ref(str(row["subject_id"])),
                "decision_type": str(row["decision_type"]),
                "prompt": str(row["prompt"]),
                "prompt_language": str(row["prompt_language"]),
                "effective_contract_revision": int(row["effective_contract_revision"]),
                "consumed": row["consumed_decision_id"] is not None,
            }
        if row["consumed_decision_id"] is not None:
            compact_decision = record_ref(str(row["consumed_decision_id"]))
            if compact_decision is None:
                raise V12StoreError("decision binding receipt is unavailable", code="ledger_corrupt")
            binding["decision_ref"] = compact_decision
        if str(row["decision_type"]) == "plan_review":
            if any(row[name] is None for name in ("plan_content_digest", "plan_approval_handle", "plan_view_content_digest", "plan_view_source_sequence")):
                raise V12StoreError("plan review binding relation is unavailable", code="approval_view_required")
            binding["plan_review_relation"] = {
                    "plan_content_digest": str(row["plan_content_digest"]),
                    "approval_handle": str(row["plan_approval_handle"]),
                    "view_content_digest": str(row["plan_view_content_digest"]),
                    "view_source_sequence": int(row["plan_view_source_sequence"]),
                }
        return binding

    def _decision_binding_current(self, connection: sqlite3.Connection, row: Mapping[str, Any]) -> bool:
        """Historical questions never retain authority over changed evidence."""
        from cortex_runtime import graph_ledger
        task_id = str(row["task_id"])
        if int(row["effective_contract_revision"]) != graph_ledger._current_revision(connection, task_id):
            return False
        if row["decision_type"] != "plan_review":
            return True
        snapshot = graph_ledger.plan_review_snapshot(connection, task_id, str(row["subject_id"]))
        for event in connection.execute(
                "SELECT e.details_json FROM execution_events e JOIN execution_graphs g ON g.graph_id=e.graph_id "
                "WHERE g.task_id=? AND e.event='plan_review_bound' ORDER BY e.sequence DESC", (task_id,)):
            bound = json.loads(event[0])
            if bound["binding"] == row["clarification_binding"]:
                return bound["snapshot"] == snapshot
        return False

    def _pending_user_decisions(self, connection: sqlite3.Connection, task_id: str) -> list[Any]:
        rows = connection.execute(
            "SELECT * FROM clarification_bindings WHERE project_hash=? AND task_id=? "
            "AND consumed_decision_id IS NULL ORDER BY issue_sequence", (self.project_hash, task_id))
        return [row for row in rows if self._decision_binding_current(connection, row)]

    def _require_no_pending_user_decision(
        self, connection: sqlite3.Connection, *, task_id: str,
    ) -> None:
        """Keep task-phase mutations behind the durable user-decision boundary.

        This runs inside the caller's mutation transaction. Persisting a
        binding without consulting it here used to let planning, dispatch, and
        closure advance while the matching answer existed only in chat. The
        binding remains the sole server-owned identity; this guard neither
        reconstructs it nor opens a recovery binding.
        """
        pending = self._pending_user_decisions(connection, task_id)
        if pending:
            raise V12StoreError(
                "a user decision must be recorded before the task can advance",
                code="decision_pending",
                details={"decision_type": str(pending[0]["decision_type"])},
            )

    def _require_current_closure_review(
        self, connection: sqlite3.Connection, *, task_id: str,
    ) -> None:
        """Require an explicit current user choice before task closure.

        The review is bound to the task timeline position at which the
        question was opened.  Reads and advisory governance do not invalidate
        it, while any later assignment, worker activity, publication, user
        decision, or decision opening means the result changed and must be
        shown again before another closure attempt.
        """
        review = connection.execute(
            "SELECT b.clarification_binding,b.issue_sequence,b.consumed_decision_id,d.decision_type AS outcome "
            "FROM clarification_bindings b "
            "LEFT JOIN user_decisions d ON d.decision_id=b.consumed_decision_id "
            "WHERE b.project_hash=? AND b.task_id=? AND b.decision_type='closure_review' "
            "ORDER BY b.issue_sequence DESC LIMIT 1",
            (self.project_hash, task_id),
        ).fetchone()
        if review is None or review["consumed_decision_id"] is None:
            raise V12StoreError(
                "a current user closure review is required",
                code="closure_review_required",
            )
        outcome = str(review["outcome"] or "")
        if outcome == "request_revision":
            raise V12StoreError(
                "the user requested revision of the current task",
                code="closure_revision_requested",
            )
        if outcome != "approve":
            raise V12StoreError(
                "the closure review decision is invalid",
                code="closure_review_required",
            )
        from cortex_runtime import graph_ledger
        snapshot = None
        for row in connection.execute("SELECT e.details_json FROM execution_events e JOIN execution_graphs g ON g.graph_id=e.graph_id WHERE g.task_id=? AND e.event='closure_review_bound' ORDER BY e.sequence DESC", (task_id,)):
            bound = json.loads(row[0])
            if bound["binding"] == review["clarification_binding"]:
                snapshot = bound["snapshot"]
                break
        if snapshot is None or snapshot != graph_ledger.closure_snapshot(connection, task_id):
            raise V12StoreError("the closure review does not cover the current graph and artifact evidence", code="closure_review_stale")
        invalidating_events = (
            "clarification_binding_issued",
            "delegation_created",
            "outcome_ownership_transferred",
            "worker_bootstrap_minted",
            "worker_bootstrap_consumed",
            "report_started",
            "report_chunk_appended",
            "report_submitted",
            "report_aborted",
            "user_decision_recorded",
        )
        placeholders = ",".join("?" for _ in invalidating_events)
        changed = connection.execute(
            f"SELECT sequence FROM timeline WHERE task_id=? AND sequence>? "
            f"AND event_type IN ({placeholders}) "
            "AND NOT (event_type='user_decision_recorded' AND decision_id=?) "
            "ORDER BY sequence LIMIT 1",
            (
                task_id, int(review["issue_sequence"]), *invalidating_events,
                str(review["consumed_decision_id"]),
            ),
        ).fetchone()
        if changed is not None:
            raise V12StoreError(
                "the accepted closure review is stale after later task activity",
                code="closure_review_stale",
            )

    def read_decision_binding(self, *, task_id: Any, binding_ref: Any) -> dict[str, Any]:
        """Read current state for an already-issued exact binding."""
        anchor = self._task_identifier(task_id)
        token = _required_text(binding_ref, label="binding_ref", maximum=64)
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            self._task(connection, anchor)
            row = connection.execute(
                "SELECT * FROM clarification_bindings WHERE clarification_binding=? AND project_hash=? AND task_id=?",
                (token, self.project_hash, anchor),
            ).fetchone()
            if row is None:
                raise V12StoreError("decision binding was not found", code="clarification_binding_not_found")
            return self._decision_binding_projection(row, task_id=anchor)
        return self._read(read)

    def issue_clarification_binding(self, *, task_id: Any, prompt: Any, prompt_language: Any, subject_type: Any = "task", subject_id: Any = None, decision_type: Any = "clarification", idempotency_key: Any = None, _connection: sqlite3.Connection | None = None, _direct_steering: bool = False) -> dict[str, Any]:
        """Issue or replay one exact, durable binding for a pending clarification."""
        anchor = self._task_identifier(task_id)
        text = _opaque_text(prompt, label="prompt")
        language = _language(prompt_language)
        kind = _required_text(subject_type, label="subject_type", maximum=16).lower()
        dtype = _required_text(decision_type, label="decision_type", maximum=32).lower()
        subject = anchor if subject_id is None and kind == "task" else self._record_identifier(subject_id, label="subject_id")
        if _direct_steering and (_connection is None or dtype != "steer" or kind != "task" or subject != anchor):
            raise V12StoreError("direct steering requires its atomic task transaction", code="invalid_argument")
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, anchor)
            revision = int(self._effective_contract(connection, anchor)["revision"])
            closure_generation = None
            if dtype == "closure_review":
                generation_row = connection.execute(
                    "SELECT COALESCE(MAX(sequence),0) AS sequence FROM timeline WHERE task_id=?",
                    (anchor,),
                ).fetchone()
                closure_generation = int(generation_row["sequence"] if generation_row is not None else 0)
            prompt_identity: object = text if closure_generation is None else {
                "prompt": text, "task_sequence": closure_generation,
            }
            plan_snapshot = None
            if dtype == "plan_review":
                from cortex_runtime import graph_ledger
                plan_snapshot = graph_ledger.plan_review_snapshot(connection, anchor, subject)
                prompt_identity = {"prompt": text, "plan_snapshot": plan_snapshot}
            request_identity = {
                "task_id": anchor, "subject_type": kind, "subject_id": subject,
                "decision_type": dtype, "prompt": text, "prompt_language": language,
                **({"task_sequence": closure_generation} if closure_generation is not None else {}),
                **({"plan_snapshot": plan_snapshot} if plan_snapshot is not None else {}),
            }
            request_digest = _sha256_prefixed(request_identity, label="clarification request")
            prompt_digest = _sha256_prefixed(prompt_identity, label="clarification prompt")
            existing = connection.execute("SELECT * FROM clarification_bindings WHERE task_id=? AND subject_type=? AND subject_id=? AND decision_type=? AND prompt_digest=? AND effective_contract_revision=?", (anchor, kind, subject, dtype, prompt_digest, revision)).fetchone()
            if existing is not None:
                return {"binding": self._decision_binding_projection(existing, task_id=anchor), "replayed": True}
            if not _direct_steering:
                self._require_no_pending_user_decision(connection, task_id=anchor)
            relation: dict[str, Any] | None = None
            if dtype == "plan_review":
                if kind != "plan":
                    raise V12StoreError("plan review must target a plan", code="invalid_decision_subject")
                candidate = connection.execute(
                    "SELECT g.graph_id,g.activation,n.state,n.artifact_generation FROM execution_graphs g "
                    "JOIN execution_nodes n ON n.graph_id=g.graph_id AND n.node_key='validate-candidate' "
                    "WHERE g.task_id=? AND g.revision=? AND g.plan_report_id=?",
                    (anchor, revision, subject),
                ).fetchone()
                if candidate is None or candidate["activation"] not in {"active", "validated"} or candidate["state"] not in {"complete", "resolved"}:
                    raise V12StoreError("independent current candidate validation is required before plan review", code="approval_view_not_ready")
                integrity = connection.execute("SELECT generation_key,reconciliation_required FROM project_integrity").fetchone()
                if integrity["reconciliation_required"] or integrity["generation_key"] != candidate["artifact_generation"]:
                    raise V12StoreError("current artifact validation is required before plan review", code="approval_view_not_ready")
                from cortex_runtime.candidate_family import read_family, selection_evidence
                from cortex_runtime.execution_graph import GraphError
                family = read_family(connection, candidate["graph_id"])
                if family is not None:
                    try:
                        selection_evidence(connection, graph_id=candidate["graph_id"], branch_key=family.data()["candidates"][0]["definition"]["key"])
                    except GraphError as exc:
                        raise V12StoreError("current family validation is required before review", code="approval_view_not_ready", details={"reason": exc.reason}) from None
                relation = self._ready_plan_review_relation(connection, task_id=anchor, report_id=subject)
            sequence = self._timeline(connection, event_type="clarification_binding_issued", entity_type="clarification_binding", entity_id="cb_" + uuid.uuid4().hex, payload={"task_id": anchor, "decision_type": dtype, "prompt_digest": prompt_digest}, task_id=anchor)
            token = "cb_" + uuid.uuid4().hex
            connection.execute("INSERT INTO clarification_bindings(clarification_binding,project_hash,task_id,subject_type,subject_id,decision_type,prompt_digest,prompt,prompt_language,effective_contract_revision,issue_sequence,request_digest,response_digest,consumed_decision_id,created_at,plan_content_digest,plan_approval_handle,plan_view_content_digest,plan_view_source_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)", (token, self.project_hash, anchor, kind, subject, dtype, prompt_digest, text, language, revision, sequence, request_digest, _now(), None if relation is None else relation["plan_content_digest"], None if relation is None else relation["approval_handle"], None if relation is None else relation["view_content_digest"], None if relation is None else relation["view_source_sequence"]))
            inserted = connection.execute("SELECT * FROM clarification_bindings WHERE clarification_binding=?", (token,)).fetchone()
            if inserted is None:
                raise V12StoreError("plan review binding was not stored", code="ledger_corrupt")
            if dtype == "plan_review":
                graph_ledger._event(connection, candidate["graph_id"], "plan_review_bound", {
                    "binding": token, "snapshot": plan_snapshot})
            if dtype == "closure_review":
                from cortex_runtime import graph_ledger
                bootstrap = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND revision=? AND graph_kind='bootstrap'", (anchor, revision)).fetchone()
                if bootstrap is None:
                    raise V12StoreError("closure bootstrap relation is unavailable", code="ledger_corrupt")
                graph_ledger._event(connection, bootstrap[0], "closure_review_bound", {
                    "binding": token, "snapshot": graph_ledger.closure_snapshot(connection, anchor)})
            return {"binding": self._decision_binding_projection(inserted, task_id=anchor), "replayed": False}
        # DomainKernel supplies the ambient transaction for semantic command
        # receipts; direct internal callers use the store transaction.
        return write(_connection) if _connection is not None else self._write(write)

    @staticmethod
    def _clarification_hold_projection(row: Mapping[str, Any]) -> dict[str, Any]:
        """Project one coordinator-owned question without a worker channel."""
        result = {
            "state": str(row["state"]),
            "opened_sequence": int(row["opened_sequence"]),
            "answered_sequence": None if row["answered_sequence"] is None else int(row["answered_sequence"]),
        }
        if row["response_decision_id"] is not None:
            result["decision_ref"] = record_ref(str(row["response_decision_id"]))
        return result

    def open_clarification_hold(
        self, *, task_id: str, binding_ref: str, connection: sqlite3.Connection,
    ) -> dict[str, Any]:
        """Create the coordinator hold in the same decision transaction."""
        binding = connection.execute(
            "SELECT task_id,issue_sequence,decision_type FROM clarification_bindings "
            "WHERE clarification_binding=? AND project_hash=?",
            (binding_ref, self.project_hash),
        ).fetchone()
        if (binding is None or binding["task_id"] != task_id
                or binding["decision_type"] not in {"clarification", "closure_review"}):
            raise V12StoreError("clarification binding was not found", code="clarification_binding_not_found")
        existing = connection.execute(
            "SELECT * FROM clarification_holds WHERE clarification_binding=? AND project_hash=?",
            (binding_ref, self.project_hash),
        ).fetchone()
        if existing is not None:
            return self._clarification_hold_projection(existing)
        now = _now()
        connection.execute(
            "INSERT INTO clarification_holds(clarification_binding,project_hash,task_id,state,"
            "response_decision_id,opened_sequence,answered_sequence,created_at,updated_at) "
            "VALUES (?, ?, ?, 'pending_question', NULL, ?, NULL, ?, ?)",
            (binding_ref, self.project_hash, task_id, int(binding["issue_sequence"]), now, now),
        )
        created = connection.execute(
            "SELECT * FROM clarification_holds WHERE clarification_binding=?", (binding_ref,),
        ).fetchone()
        if created is None:
            raise V12StoreError("clarification hold was not stored", code="ledger_corrupt")
        return self._clarification_hold_projection(created)


    def answer_clarification_hold(
        self, *, task_id: str, binding_ref: str, decision_id: str,
        connection: sqlite3.Connection,
    ) -> dict[str, Any]:
        """Atomically move one recorded hold into its next non-scheduling state."""
        row = connection.execute(
            "SELECT * FROM clarification_holds WHERE clarification_binding=? AND project_hash=? AND task_id=?",
            (binding_ref, self.project_hash, task_id),
        ).fetchone()
        if row is None:
            raise V12StoreError("clarification hold was not found", code="clarification_binding_not_found")
        if row["response_decision_id"] is not None:
            if str(row["response_decision_id"]) != decision_id:
                raise V12StoreError("clarification hold has a different response", code="clarification_binding_conflict")
            return self._clarification_hold_projection(row)
        if str(row["state"]) != "pending_question":
            raise V12StoreError("clarification hold cannot accept a response", code="clarification_binding_stale")
        next_state = "coordinator_completed"
        sequence = self._timeline(
            connection, event_type="clarification_hold_answered", entity_type="clarification_hold",
            entity_id=binding_ref, payload={"state": next_state}, task_id=task_id,
            decision_id=decision_id,
        )
        now = _now()
        cursor = connection.execute(
            "UPDATE clarification_holds SET state=?,response_decision_id=?,answered_sequence=?,updated_at=? "
            "WHERE clarification_binding=? AND response_decision_id IS NULL",
            (next_state, decision_id, sequence, now, binding_ref),
        )
        if cursor.rowcount != 1:
            raise V12StoreError("clarification hold has already been answered", code="clarification_binding_consumed")
        answered = connection.execute(
            "SELECT * FROM clarification_holds WHERE clarification_binding=?", (binding_ref,)
        ).fetchone()
        if answered is None:
            raise V12StoreError("clarification hold was not stored", code="ledger_corrupt")
        return self._clarification_hold_projection(answered)





    def record_user_decision(self, *, _connection: sqlite3.Connection, task_id: Any, subject_type: Any, subject_id: Any, subject_digest: Any = None, decision_type: Any = None, prompt: Any = None, response_original: Any = None, user_language: Any = None, approval_handle: Any = None, approval_view_content_digest: Any = None, approval_view_source_sequence: Any = None, supersedes_decision_id: Any = None, steering_delta: Any = None, clarification_binding: Any = None, branch_key: str | None = None) -> dict[str, Any]:
        """Append a user-origin decision with exact immutable subject binding.

        Only the decision aggregate's existing transaction may enter here;
        that transaction also owns the binding and command receipt. Attribution
        preserves the coordinator's assertion, not proof of user authentication.
        """
        if not _connection.in_transaction:
            raise V12StoreError("decision requires an aggregate transaction", code="transaction_required")
        if clarification_binding is None:
            raise V12StoreError("decision requires its exact binding", code="clarification_binding_not_found")
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
            "prompt": _optional_text(prompt, label="prompt") or "",
            "response_original": _optional_text(response_original, label="response_original") or "",
            "user_language": _language(user_language),
            "approval_handle": None if approval_handle is None else _required_text(approval_handle, label="approval_handle", maximum=160),
            "approval_view_content_digest": _digest(approval_view_content_digest, label="approval_view_content_digest"),
            "approval_view_source_sequence": approval_view_source_sequence,
            "supersedes_decision_id": None if supersedes_decision_id is None else self._record_identifier(supersedes_decision_id, label="supersedes_decision_id"),
            "steering_delta": steering_delta,
            "branch_key": None if branch_key is None else _required_text(branch_key, label="branch_key", maximum=64),
            "clarification_binding": None if clarification_binding is None else _required_text(clarification_binding, label="clarification_binding", maximum=160),
        }
        # A contract delta is a task-only mutation.  Clarification responses
        # may carry one because the user can answer a product question and
        # amend the contract atomically in the same decision transaction.
        # Validate its shape
        # before opening the write callback so malformed requests cannot leave
        # a user_decisions row or timeline event behind.
        has_contract_delta = decision == "steer" or (decision == "clarification" and steering_delta is not None)
        if has_contract_delta:
            if kind != "task" or subject != anchor:
                raise V12StoreError("steer decisions must target the anchored task", code="invalid_decision_subject", details={"field": "subject_ref", "expected": "task_ref"})
            delta = payload["steering_delta"]
            if not isinstance(delta, Mapping) or set(delta) - {"retire_item_refs", "add"}:
                raise V12StoreError("steering_delta is invalid", code="invalid_argument", details={"field": "steering_delta"})
            retired = delta.get("retire_item_refs", [])
            additions = delta.get("add", [])
            if not isinstance(retired, list) or not isinstance(additions, list):
                raise V12StoreError("steering_delta is invalid", code="invalid_argument", details={"field": "steering_delta"})
            if any(not isinstance(value, str) for value in retired) or len({value for value in retired if isinstance(value, str)}) != len(retired):
                raise V12StoreError("steering_delta is invalid", code="invalid_argument", details={"field": "steering_delta"})
            for addition in additions:
                if not isinstance(addition, Mapping):
                    raise V12StoreError("steering_delta is invalid", code="invalid_argument", details={"field": "steering_delta"})
                category = addition.get("category")
                if category == "outcome":
                    if (
                        set(addition) != {"category", "text", "acceptance", "constraints", "verification"}
                        or not isinstance(addition.get("text"), str)
                        or not addition["text"].strip()
                        or any(
                            not isinstance(addition.get(field), list)
                            or any(not isinstance(item, str) or not item.strip() for item in addition[field])
                            for field in ("acceptance", "constraints", "verification")
                        )
                    ):
                        raise V12StoreError("steering_delta is invalid", code="invalid_argument", details={"field": "steering_delta"})
                elif category == "outcome_replacement":
                    if (
                        set(addition) != {
                            "category", "outcome_ref", "text", "acceptance",
                            "constraints", "verification",
                        }
                        or not isinstance(addition.get("outcome_ref"), str)
                        or not isinstance(addition.get("text"), str)
                        or not addition["text"].strip()
                        or any(
                            not isinstance(addition.get(field), list)
                            or any(not isinstance(item, str) or not item.strip() for item in addition[field])
                            for field in ("acceptance", "constraints", "verification")
                        )
                    ):
                        raise V12StoreError("steering_delta is invalid", code="invalid_argument", details={"field": "steering_delta"})
                else:
                    raise V12StoreError("only complete outcome additions or replacements are permitted", code="invalid_argument", details={"field": "steering_delta"})
        elif steering_delta is not None:
            raise V12StoreError("steering_delta is only permitted for steer or clarification decisions", code="invalid_argument", details={"field": "steering_delta"})
        requires_plan_approval_view = kind == "plan" and decision == "approve"
        has_bound_plan_relation = kind == "plan" and any(
            payload[name] is not None
            for name in ("approval_handle", "approval_view_content_digest", "approval_view_source_sequence")
        )
        if requires_plan_approval_view and payload["approval_handle"] is not None and IDENTIFIER_RE.fullmatch(payload["approval_handle"]) is None:
            raise V12StoreError("approval_handle is invalid", code="invalid_argument", details={"field": "approval_handle"})
        if payload["approval_view_source_sequence"] is not None and (not isinstance(payload["approval_view_source_sequence"], int) or isinstance(payload["approval_view_source_sequence"], bool) or payload["approval_view_source_sequence"] < 0):
            raise V12StoreError("approval_view_source_sequence is invalid", code="invalid_argument", details={"field": "approval_view_source_sequence"})
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, anchor)
            clarification_row = None
            if payload["clarification_binding"] is not None:
                clarification_row = connection.execute("SELECT * FROM clarification_bindings WHERE clarification_binding=? AND project_hash=?", (payload["clarification_binding"], self.project_hash)).fetchone()
                if clarification_row is None:
                    raise V12StoreError("clarification binding was not found", code="clarification_binding_not_found")
                binding_decision_type = str(clarification_row["decision_type"])
                expected_prompt_digest = _sha256_prefixed(payload["prompt"], label="clarification prompt")
                prompt_matches = (
                    str(clarification_row["prompt"]) == payload["prompt"]
                    and (
                        binding_decision_type in {"closure_review", "plan_review"}
                        or str(clarification_row["prompt_digest"]) == expected_prompt_digest
                    )
                )
                # A plan-review binding represents a family; its consumed
                # outcome is one of the legal plan decisions.
                decision_matches = (
                    binding_decision_type == decision
                    or (binding_decision_type == "plan_review" and kind == "plan" and decision in {"approve", "request_revision", "cancel"})
                    or (binding_decision_type == "closure_review" and kind == "task" and decision in {"approve", "request_revision"})
                )
                if (str(clarification_row["task_id"]) != anchor or str(clarification_row["subject_type"]) != kind or str(clarification_row["subject_id"]) != subject or not decision_matches or not prompt_matches or str(clarification_row["prompt_language"]) != payload["user_language"]):
                    raise V12StoreError("clarification binding does not match the decision", code="clarification_binding_mismatch")
                if int(clarification_row["effective_contract_revision"]) != int(self._effective_contract(connection, anchor)["revision"]):
                    raise V12StoreError("clarification binding is stale", code="clarification_binding_stale")
                if clarification_row["consumed_decision_id"] is None and not self._decision_binding_current(connection, clarification_row):
                    raise V12StoreError("decision packet authority is stale", code="clarification_binding_stale")
                if clarification_row["consumed_decision_id"] is not None:
                    expected = _sha256_prefixed(payload["response_original"], label="clarification response")
                    if clarification_row["response_digest"] != expected:
                        raise V12StoreError("clarification binding was already consumed with a different response", code="clarification_binding_conflict")
                    return {"decision": self._compact_decision(self._decision(connection, str(clarification_row["consumed_decision_id"]), task_id=anchor)), "replayed": True}
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
                if kind == "plan" and item["assembly_state"] != "finalized":
                    raise V12StoreError("plan is not finalized evidence", code="decision_subject_not_finalized")
                bound_digest = str(item["content_digest"])
            else:
                raise V12StoreError("decision subject is invalid", code="invalid_decision_subject")
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
            if requires_plan_approval_view or has_bound_plan_relation:
                if payload["approval_handle"] is None or payload["approval_view_content_digest"] is None or payload["approval_view_source_sequence"] is None:
                    raise V12StoreError("plan approval requires a ready approval view", code="approval_view_required")
                relation_handle = connection.execute("SELECT * FROM approval_handles WHERE approval_handle=? AND project_hash=?", (payload["approval_handle"], self.project_hash)).fetchone()
                if relation_handle is None:
                    raise V12StoreError("approval handle was not found", code="approval_handle_not_found")
                expected_relative = f"plans/revisions/{subject}.md"
                if (
                    relation_handle["task_id"] != anchor
                    or relation_handle["report_id"] != subject
                    or relation_handle["report_content_digest"] != bound_digest
                    or relation_handle["view_relative_path"] != expected_relative
                    or relation_handle["view_content_digest"] != payload["approval_view_content_digest"]
                    or int(relation_handle["view_source_sequence"]) != payload["approval_view_source_sequence"]
                    or relation_handle["request_digest"] != _sha256_prefixed(task["user_request_original"], label="user request original")
                    or relation_handle["consumed_decision_id"] is not None
                ):
                    raise V12StoreError("approval handle does not match the ready plan view", code="approval_handle_mismatch")
                # The persisted relation was verified at open time.  Do not
                # query a mutable current projection here: a newer view must
                # neither redirect nor invalidate this one-shot review.
                approval_handle = relation_handle if requires_plan_approval_view else None
            if payload["supersedes_decision_id"] is not None:
                prior = self._decision(connection, payload["supersedes_decision_id"], task_id=anchor)
                if prior["subject_type"] != kind or prior["subject_id"] != subject:
                    raise V12StoreError("superseded decision has a different subject", code="cross_project_reference")
            steering_effect = None
            semantic_delta = None
            selected_family = None
            if kind == "plan":
                from cortex_runtime.candidate_family import read_family, selection_evidence
                row = connection.execute("SELECT graph_id FROM execution_graphs WHERE task_id=? AND plan_report_id=? AND revision=(SELECT MAX(revision) FROM effective_contract_revisions WHERE task_id=?)", (anchor, subject, anchor)).fetchone()
                family = read_family(connection, row[0]) if row is not None else None
                if family is not None and decision == "approve":
                    if payload["branch_key"] is None:
                        raise V12StoreError("choose one exact validated alternative", code="invalid_argument", details={"field": "branch_key"})
                    selected = selection_evidence(connection, graph_id=row[0], branch_key=payload["branch_key"])
                    selected_family = (row[0], payload["branch_key"])
                    semantic_delta = selected["selected"]["definition"]["delta"]
                elif payload["branch_key"] is not None:
                    raise V12StoreError("branch selection is only valid when approving a pending family", code="invalid_argument", details={"field": "branch_key"})
            elif payload["branch_key"] is not None:
                raise V12StoreError("branch selection requires plan review", code="invalid_argument")
            if has_contract_delta:
                current = self._effective_contract(connection, anchor)["items"]
                by_id = {self._outcome_item_id(connection, anchor, item["item_ref"]): item["text"] for item in current}
                source = payload["steering_delta"]
                retired = [by_id[self._outcome_item_id(connection, anchor, ref)] for ref in source.get("retire_item_refs", [])]
                additions = []
                replacements = [item for item in source.get("add", []) if item["category"] == "outcome_replacement"]
                if replacements and (len(replacements) != 1 or len(source["add"]) != 1 or retired):
                    raise V12StoreError("steering replacement is ambiguous", code="invalid_argument")
                for item in source.get("add", []):
                    if item["category"] == "outcome_replacement":
                        retired.append(by_id[self._outcome_item_id(connection, anchor, item["outcome_ref"])])
                    additions.append({"outcome": item["text"], "acceptance": item["acceptance"],
                                      "constraints": item["constraints"], "verification": item["verification"]})
                semantic_delta = {"add": additions, "retire": retired}
            identifier = new_sharded_id("decision", self.project_hash)
            sequence = self._timeline(connection, event_type="user_decision_recorded", entity_type="user_decision", entity_id=identifier, payload={"decision_id": identifier, "subject_type": kind, "subject_id": subject, "subject_digest": bound_digest, "decision_type": decision}, task_id=anchor, decision_id=identifier)
            # Store the neutral question and exact direct response once.
            connection.execute("INSERT INTO user_decisions(decision_id,project_hash,task_id,subject_type,subject_id,subject_digest,decision_type,prompt,response_original,user_language,attribution,supersedes_decision_id,created_at,created_sequence,steering_delta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (identifier, self.project_hash, anchor, kind, subject, bound_digest, decision, payload["prompt"], payload["response_original"], payload["user_language"], DECISION_ATTRIBUTION, payload["supersedes_decision_id"], _now(), sequence, _canonical_json(payload["steering_delta"], label="steering_delta") if has_contract_delta else None))
            if semantic_delta is not None:
                steering_effect = self._commit_contract_delta(connection, task_id=anchor,
                    delta=semantic_delta, decision_id=identifier, sequence=sequence, selected_family=selected_family)
            if kind == "plan":
                graph_row = connection.execute("SELECT graph_id,activation FROM execution_graphs WHERE task_id=? AND plan_report_id=? AND revision=(SELECT MAX(revision) FROM effective_contract_revisions WHERE task_id=?)",
                    (anchor, subject, anchor)).fetchone()
                if graph_row is None:
                    raise V12StoreError("current candidate graph is unavailable", code="plan_approval_required")
                if decision == "approve":
                    if graph_row["activation"] != "active":
                        raise V12StoreError("independent graph validation must finish before approval", code="plan_approval_required")
                    connection.execute("UPDATE execution_graphs SET approved=1 WHERE graph_id=?", (graph_row["graph_id"],))
                elif decision in {"request_revision", "cancel"}:
                    connection.execute("UPDATE execution_graphs SET activation='rejected',approved=0 WHERE graph_id=?", (graph_row["graph_id"],))
            if approval_handle is not None:
                cursor = connection.execute("UPDATE approval_handles SET consumed_decision_id=? WHERE approval_handle=? AND consumed_decision_id IS NULL", (identifier, payload["approval_handle"]))
                if cursor.rowcount != 1:
                    raise V12StoreError("approval handle has already been used", code="approval_handle_consumed")
            if clarification_row is not None:
                response_digest = _sha256_prefixed(payload["response_original"], label="clarification response")
                cursor = connection.execute("UPDATE clarification_bindings SET response_digest=?,consumed_decision_id=? WHERE clarification_binding=? AND consumed_decision_id IS NULL", (response_digest, identifier, payload["clarification_binding"]))
                if cursor.rowcount != 1:
                    raise V12StoreError("clarification binding has already been used", code="clarification_binding_consumed")
            return {"decision": self._compact_decision(self._decision(connection, identifier, task_id=anchor)),
                    **({"effect": steering_effect} if steering_effect is not None else {})}
        return write(_connection)

    def assess_execution_governance(self, *, task_id: str, mode: str, rationale: str,
                                    risk_factors: Any, execution_route: str,
                                    minimal_mode: str | None,
                                    user_review_requested: bool | None) -> tuple[dict[str, Any], bool]:
        """Append an atomic risk/route decision; identical evidence cannot churn it."""
        from cortex_runtime import graph_ledger
        anchor = self._task_identifier(task_id)
        if mode not in GOVERNANCE_MODES or execution_route not in {"planned", "minimal"}:
            raise V12StoreError("invalid governance selection", code="invalid_governance_mode")
        if user_review_requested is not None and not isinstance(user_review_requested, bool):
            raise V12StoreError("review selection must be boolean", code="invalid_argument")
        if (execution_route == "minimal" and minimal_mode not in {"read_only", "mutating"}) or (execution_route == "planned" and minimal_mode is not None):
            raise V12StoreError("execution mode must match the selected route", code="invalid_argument")
        payload = {"mode": mode, "rationale": _optional_text(rationale, label="rationale"),
            "risk_factors": sorted(set(_text_list(risk_factors, label="risk_factors"))),
            "execution_route": execution_route, "minimal_mode": minimal_mode,
            "user_review_requested": user_review_requested}
        def mutate(connection):
            self._task(connection, anchor)
            revision = graph_ledger._current_revision(connection, anchor)
            prior = connection.execute("SELECT p.user_review_requested FROM execution_policies p JOIN governance_assessments a ON a.assessment_id=p.assessment_id WHERE p.task_id=? ORDER BY a.created_sequence DESC LIMIT 1", (anchor,)).fetchone()
            requested = bool(prior[0]) if prior is not None and user_review_requested is None else bool(user_review_requested)
            if execution_route == "minimal" and (mode != "minimal" or requested or payload["risk_factors"]):
                raise V12StoreError("minimal execution requires bounded risk-free work without requested review", code="minimal_route_ineligible")
            identifier = "assessment-" + uuid.uuid4().hex
            sequence = self._timeline(connection, event_type="governance_mode_set", entity_type="governance_assessment", entity_id=identifier,
                payload={"mode": mode, "execution_route": execution_route, "user_review_requested": requested}, task_id=anchor, assessment_id=identifier)
            connection.execute("INSERT INTO governance_assessments VALUES (?,?,?,?,'model',?,?,?,?)",
                (identifier, self.project_hash, anchor, mode, payload["rationale"], _canonical_json(payload["risk_factors"], label="risk factors"), _now(), sequence))
            connection.execute("INSERT INTO execution_policies VALUES (?,?,?,?,?,?)", (identifier, anchor, revision, execution_route, int(requested), minimal_mode))
            graph_ledger.materialize_minimal(connection, anchor)
            return {"mode": mode, "execution_route": execution_route, "user_review_requested": requested}
        def resolve(connection):
            self._task(connection, anchor)
            revision = graph_ledger._current_revision(connection, anchor)
            digest = _sha256_prefixed(payload, label="governance selection")
            return f"governance:{anchor}:{revision}:{digest}", payload, mutate
        return self.run_command_receipt_resolved(aggregate_type="task", aggregate_id=anchor,
            command_name="assess_governance", resolve=resolve)



    def close_execution_task(self, *, task_id: str, verdict: str, unresolved_risks: Any = None,
                             follow_ups: Any = None, completion_notes: Any = None) -> tuple[dict[str, Any], bool]:
        """User-bound typed closure; incomplete evidence cannot become success."""
        from cortex_runtime import graph_ledger
        anchor = self._task_identifier(task_id)
        if verdict not in CLOSURE_VERDICTS:
            raise V12StoreError("closure verdict is invalid", code="invalid_argument")
        payload = {"verdict": verdict, "unresolved_risks": _text_list(unresolved_risks, label="unresolved_risks"),
            "follow_ups": _text_list(follow_ups, label="follow_ups"),
            "completion_notes": _text_list(completion_notes, label="completion_notes")}
        def mutate(connection):
            self._task(connection, anchor)
            self._require_current_closure_review(connection, task_id=anchor)
            self._require_no_pending_user_decision(connection, task_id=anchor)
            evidence = graph_ledger.closure_evidence(connection, anchor)
            if graph_ledger.continuations(connection, anchor):
                raise V12StoreError("native routes remain unfinished", code="closure_not_ready")
            if not evidence["ready"]:
                raise V12StoreError("current graph evidence is incomplete", code="closure_not_ready", details={"reasons": evidence["reasons"]})
            if verdict == "ready" and (evidence["risks"] or payload["unresolved_risks"]):
                raise V12StoreError("residual risks are incompatible with a risk-free verdict", code="closure_not_ready")
            identifier = "closure-" + uuid.uuid4().hex
            sequence = self._timeline(connection, event_type="governance_closure_submitted", entity_type="governance_closure", entity_id=identifier,
                payload={"verdict": verdict, "revision": evidence["revision"]}, task_id=anchor, closure_id=identifier)
            connection.execute("INSERT INTO governance_closures(closure_id,project_hash,subject_type,subject_id,verdict,evidence_json,unresolved_risks_json,follow_ups_json,completion_notes_json,created_at,created_sequence) VALUES (?,?,'task',?,?,?,?,?,?,?,?)",
                (identifier, self.project_hash, anchor, verdict, _canonical_json(evidence, label="closure evidence"),
                 _canonical_json(sorted(set([*payload["unresolved_risks"], *evidence["risks"]])), label="closure risks"),
                 _canonical_json(payload["follow_ups"], label="closure follow-ups"),
                 _canonical_json(payload["completion_notes"], label="closure notes"), _now(), sequence))
            return {"verdict": verdict, "revision": evidence["revision"]}
        def resolve(connection):
            self._require_current_closure_review(connection, task_id=anchor)
            review = connection.execute("SELECT clarification_binding FROM clarification_bindings WHERE task_id=? AND decision_type='closure_review' ORDER BY issue_sequence DESC LIMIT 1", (anchor,)).fetchone()
            return "closure:" + review[0], payload, mutate
        return self.run_command_receipt_resolved(aggregate_type="task", aggregate_id=anchor, command_name="close_task", resolve=resolve)



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

    def _timeline_page_reverse(
        self, connection: sqlite3.Connection, *, before: int | None,
        limit: int, clause: str, values: Sequence[Any],
    ) -> tuple[list[dict[str, Any]], int | None, bool]:
        """Return one newest-first page; the next page moves strictly older."""
        if before is None:
            rows = connection.execute(
                f"SELECT * FROM timeline WHERE ({clause}) ORDER BY sequence DESC LIMIT ?",
                [*values, limit + 1],
            ).fetchall()
        else:
            rows = connection.execute(
                f"SELECT * FROM timeline WHERE sequence<? AND ({clause}) ORDER BY sequence DESC LIMIT ?",
                [before, *values, limit + 1],
            ).fetchall()
        has_more, rows = len(rows) > limit, rows[:limit]
        timeline = []
        for row in rows:
            item = _row(row)
            assert item is not None
            item["payload"] = _load_json(str(item.pop("payload_json")), label="timeline payload")
            timeline.append(item)
        return timeline, int(timeline[-1]["sequence"]) if timeline else before, has_more

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
            current_revision = int(self._effective_contract(connection, anchor)["revision"])
            continuation_rows = connection.execute(
                "SELECT DISTINCT d.delegation_id FROM delegations d "
                "LEFT JOIN worker_capabilities c ON c.assignment_id=d.delegation_id "
                "WHERE d.task_id=? AND d.project_hash=? "
                "AND (c.assignment_id IS NULL OR (c.contract_revision=? AND c.state IN ('minted','consumed'))) "
                "ORDER BY d.created_sequence,d.delegation_id",
                (anchor, self.project_hash, current_revision),
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
            return {"task": task, "effective_contract": self._effective_contract(connection, anchor), "aggregate_coverage": self._aggregate_coverage(connection, anchor), "conformance_review": self._conformance_review(connection, anchor), "execution_outcome": self._execution_evidence(connection, anchor), "advisory_closure": self._advisory_closure(connection, anchor), "delegations": delegations, "continuations": continuations, "reports": reports, "decisions": decisions, "consumption_receipts": receipts, "timeline": timeline, "next_sequence": next_sequence, "has_more": has_more}
        return self._read(read)

    def inspect_task_timeline(
        self, *, task_id: Any, before_sequence: Any = None,
        limit: Any = DEFAULT_PAGE_LIMIT,
    ) -> dict[str, Any]:
        """Read complete historical event projections newest-first."""
        anchor, page = self._task_identifier(task_id), self._limit(limit)
        before = None if before_sequence is None else self._sequence(before_sequence)

        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            self._task(connection, anchor)
            timeline, next_sequence, has_more = self._timeline_page_reverse(
                connection, before=before, limit=page,
                clause="task_id=?", values=[anchor],
            )
            delegations = [
                self._compact_delegation(self._delegation(connection, item, task_id=anchor))
                for item in self._ids(timeline, "delegation_id")
            ]
            reports = [
                self._compact_report(self._report(connection, item, task_id=anchor))
                for item in self._ids(timeline, "report_id")
            ]
            decisions = [
                self._compact_decision(self._decision(connection, item, task_id=anchor))
                for item in self._ids(timeline, "decision_id")
            ]
            receipts = self._consumption_receipts(
                connection, task_id=anchor,
                sequences=[int(item["sequence"]) for item in timeline],
            )
            return {
                "delegations": delegations,
                "reports": reports,
                "decisions": decisions,
                "consumption_receipts": receipts,
                "timeline": timeline,
                "next_sequence": next_sequence,
                "has_more": has_more,
            }

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


    def read_reports(
        self, *, report_ids: Any, sections: Any = None, cursor: Any = None,
        max_bytes: Any = REPORT_READ_MAX_BYTES,
        response_max_bytes: Any = REPORT_RESPONSE_MAX_BYTES,
        consumer_delegation_id: Any = None, task_id: Any = None,
    ) -> dict[str, Any]:
        """Return bounded chunks and append structural evidence of that read.

        Report bodies require the exact consuming delegation.  Calls without a
        consumer return metadata only and cannot create receipts. Receipts
        contain only immutable IDs, digests, cursors, chunk indexes, and byte
        counts—never report bodies.
        """
        import base64

        requested = _identifier_list(report_ids, label="report_ids", maximum=MAX_REPORT_IDS, minimum=1, ordered=True)
        if sections is None:
            selected_sections: list[str] | None = None
        else:
            if not isinstance(sections, list) or not 1 <= len(sections) <= 32 or len(set(sections)) != len(sections):
                raise V12StoreError("sections are invalid", code="invalid_argument", details={"field": "sections"})
            if any(not isinstance(item, str) or REPORT_SECTION_RE.fullmatch(item) is None for item in sections):
                raise V12StoreError("sections are invalid", code="invalid_argument", details={"field": "sections"})
            selected_sections = list(sections)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 0 <= max_bytes <= REPORT_READ_MAX_BYTES:
            raise V12StoreError("max_bytes is invalid", code="invalid_argument", details={"field": "max_bytes"})
        if (
            not isinstance(response_max_bytes, int)
            or isinstance(response_max_bytes, bool)
            or not REPORT_CHUNK_MAX_BYTES + 4_096 <= response_max_bytes <= REPORT_RESPONSE_MAX_BYTES
        ):
            raise V12StoreError(
                "response_max_bytes is invalid", code="invalid_argument",
                details={"field": "response_max_bytes"},
            )
        consumer = None if consumer_delegation_id is None else self._record_identifier(consumer_delegation_id, label="consumer_delegation_id")
        if consumer is None and max_bytes != 0 and task_id is None:
            raise V12StoreError("coordinator report bodies require an anchored task", code="invalid_argument", details={"field": "task_id"})
        kind = "worker" if consumer is not None else "coordinator"
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
                consumer_id = None if consuming is None else consuming["delegation_id"]
                sections_json = _canonical_json(selected_sections, label="report read sections")
                candidates: list[tuple[Mapping[str, Any], list[int], int, str]] = []
                for report, compact in zip(report_rows, result_reports):
                    chunks = compact["chunks"]
                    indexes = [int(chunk["chunk_index"]) for chunk in chunks]
                    bytes_value = sum(int(chunk["content_bytes"]) for chunk in chunks)
                    indexes_json = _canonical_json(indexes, label="report receipt chunks")
                    candidates.append((report, indexes, bytes_value, indexes_json))

                # A restarted worker may repeat an already completed exact
                # assignment read without a model-owned cursor. Reconstruct
                # the deterministic page first, then reconcile every existing
                # receipt atomically. Never append another receipt or timeline
                # event for the same immutable report page.
                existing_rows: list[sqlite3.Row] = []
                for report, _indexes, bytes_value, indexes_json in candidates:
                    existing = connection.execute(
                        "SELECT * FROM report_consumption_receipts "
                        "WHERE project_hash=? AND task_id=? "
                        "AND consumer_delegation_id IS ? AND reader_kind=? "
                        "AND report_id=? AND observed_content_digest=? "
                        "AND sections_json=? AND input_cursor IS ? AND output_cursor IS ? "
                        "AND chunk_indexes_json=? AND returned_content_bytes=? AND has_more=? "
                        "ORDER BY receipt_id LIMIT 1",
                        (
                            self.project_hash, anchor, consumer_id, kind,
                            report["report_id"], report["content_digest"],
                            sections_json, cursor, next_cursor, indexes_json,
                            bytes_value, int(more),
                        ),
                    ).fetchone()
                    if existing is None:
                        existing_rows = []
                        break
                    existing_rows.append(existing)

                def project_receipt(
                    row: Mapping[str, Any], indexes: list[int],
                ) -> dict[str, Any]:
                    return {
                        "receipt_id": int(row["receipt_id"]),
                        "report_id": row["report_id"],
                        "consumer_delegation_id": row["consumer_delegation_id"],
                        "reader_kind": row["reader_kind"],
                        "observed_content_digest": row["observed_content_digest"],
                        "chunk_indexes": indexes,
                        "input_cursor": row["input_cursor"],
                        "output_cursor": row["output_cursor"],
                        "returned_content_bytes": int(row["returned_content_bytes"]),
                        "has_more": bool(row["has_more"]),
                        "created_sequence": int(row["created_sequence"]),
                    }

                if len(existing_rows) == len(candidates):
                    receipts = [
                        project_receipt(row, candidate[1])
                        for row, candidate in zip(existing_rows, candidates)
                    ]
                    return {
                        "reports": result_reports,
                        "returned_content_bytes": returned_bytes,
                        "next_cursor": next_cursor,
                        "has_more": more,
                        "consumption_receipts": receipts,
                    }

                receipts: list[dict[str, Any]] = []
                for report, indexes, bytes_value, indexes_json in candidates:
                    sequence = self._timeline(
                        connection,
                        event_type="report_read",
                        entity_type="report_consumption",
                        entity_id=str(report["report_id"]),
                        payload={
                            "report_id": report["report_id"],
                            "consumer_delegation_id": consumer_id,
                            "reader_kind": kind,
                            "observed_content_digest": report["content_digest"],
                            "read_scope_digest": scope,
                            "chunk_indexes": indexes,
                            "returned_content_bytes": bytes_value,
                            "has_more": more,
                        },
                        task_id=anchor,
                        delegation_id=consumer_id,
                        report_id=str(report["report_id"]),
                    )
                    cursor_value = connection.execute(
                        "INSERT INTO report_consumption_receipts(project_hash,task_id,consumer_delegation_id,reader_kind,report_id,observed_content_digest,sections_json,input_cursor,output_cursor,chunk_indexes_json,returned_content_bytes,has_more,created_at,created_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (self.project_hash, anchor, consumer_id, kind, report["report_id"], report["content_digest"], sections_json, cursor, next_cursor, indexes_json, bytes_value, int(more), _now(), sequence),
                    )
                    receipts.append({"receipt_id": int(cursor_value.lastrowid), "report_id": report["report_id"], "consumer_delegation_id": consumer_id, "reader_kind": kind, "observed_content_digest": report["content_digest"], "chunk_indexes": indexes, "input_cursor": cursor, "output_cursor": next_cursor, "returned_content_bytes": bytes_value, "has_more": more, "created_sequence": sequence})
                return {"reports": result_reports, "returned_content_bytes": returned_bytes, "next_cursor": next_cursor, "has_more": more, "consumption_receipts": receipts}
            if max_bytes == 0:
                # Metadata-only inspection is deliberately not a complete
                # authoritative body consumption and cannot satisfy or reuse
                # a material-decision receipt.
                return {"reports": result_reports, "returned_content_bytes": 0, "next_cursor": None, "has_more": False, "consumption_receipts": []}
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
                    # ``trial`` is a transport response assembled from
                    # individually validated report metadata and chunks.  Its
                    # allowed envelope is REPORT_RESPONSE_MAX_BYTES, not the
                    # smaller JSON_MAX_BYTES used for one stored JSON value.
                    # Applying the storage-value limit here made a valid
                    # multi-report worker handoff fail as ``content_invalid``
                    # before normal response pagination could take over.
                    if len(_canonical_json(
                        trial,
                        label="report response",
                        maximum_bytes=REPORT_RESPONSE_MAX_BYTES,
                    ).encode("utf-8")) > response_max_bytes:
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
            return receipt_result(returned, next_cursor, more)
        result = self._write(read)
        self.materialize_human_views(anchor)
        return result


__all__ = ["DATABASE_NAME", "SCHEMA_VERSION", "V12Store", "V12StoreError"]
