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
    CANONICAL_REPORT_EVIDENCE_SCHEMAS, CANONICAL_REPORT_V2_SCHEMAS, CLOSURE_SUBJECTS, CLOSURE_VERDICTS, DECISION_ATTRIBUTION, DECISION_SUBJECTS,
    canonical_report_semantic_status,
    DECISION_TYPES, DEFAULT_PAGE_LIMIT, DIGEST_RE, GOVERNANCE_MODES,
    GOVERNANCE_SOURCES, IDEMPOTENCY_KEY_MAX_LENGTH, IDENTIFIER_RE,
    INITIATIVE_STATUSES, JSON_MAX_BYTES, JSON_MAX_DEPTH, LANGUAGE_TAG_MAX_LENGTH,
    MUTATION_RESULT_MAX_BYTES,
    LANGUAGE_TAG_RE,
    MAX_DECISION_IDS, MAX_LINKS,
    MAX_PAGE_LIMIT, MAX_REPORT_IDS, PROJECT_ROOT_MAX_LENGTH, REPORT_STATUSES,
    PLAN_REVIEW_POLICIES, REPORT_ASSEMBLING_MAX_BYTES_PER_TASK,
    REPORT_ASSEMBLING_MAX_PER_TASK, REPORT_ASSEMBLY_STATES, REPORT_CHUNK_MAX_BYTES,
    REPORT_MAX_BYTES, REPORT_MAX_CHUNKS, REPORT_MODES, REPORT_READ_MAX_BYTES,
    REPORT_RESPONSE_MAX_BYTES, REPORT_RETAINED_MAX_BYTES_PER_TASK,
    REPORT_SECTION_MAX_LENGTH, REPORT_SECTION_RE, REPORT_TYPES, ROLE_MAX_LENGTH, TASK_CONTRACT_ITEM_MAX_LENGTH,
    TASK_CONTRACT_MAX_ITEMS, TASK_CONTRACT_VERSION, TEXT_MAX_LENGTH, PROJECTION_RENDERER_VERSION, new_sharded_id,
    new_task_id, record_ref, record_ref_parts, record_shard_hash, task_ref, task_ref_parts, task_shard_hash,
)

SCHEMA_VERSION = 1
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
_OUTCOME_COVERAGE_MIGRATION_VERSION = 10
_OUTCOME_COVERAGE_MIGRATION_NAME = "v12-effective-outcome-coverage"
_REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_VERSION = 11
_REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_NAME = "v12-report-coverage-diagnostics"
_REVISIONED_ASSIGNMENTS_MIGRATION_VERSION = 12
_REVISIONED_ASSIGNMENTS_MIGRATION_NAME = "v12-revisioned-outcome-assignments"
_STEERING_DELTA_MIGRATION_VERSION = 13
_STEERING_DELTA_MIGRATION_NAME = "v12-persisted-steering-delta"
_REPORT_OPERATIONS_MIGRATION_VERSION = 14
_REPORT_OPERATIONS_MIGRATION_NAME = "v14-atomic-report-operations"
_CLARIFICATION_BINDING_MIGRATION_VERSION = 15
_CLARIFICATION_BINDING_MIGRATION_NAME = "v15-durable-clarification-bindings"
_COMMAND_RECEIPTS_MIGRATION_VERSION = 16
_COMMAND_RECEIPTS_MIGRATION_NAME = "v16-transactional-command-receipts"
_PLAN_REVIEW_RELATION_MIGRATION_VERSION = 17
_PLAN_REVIEW_RELATION_MIGRATION_NAME = "v17-plan-review-bound-relations"
_CLARIFICATION_HOLD_MIGRATION_VERSION = 18
_CLARIFICATION_HOLD_MIGRATION_NAME = "v18-clarification-holds"
_TASK_LOCATOR_MIGRATION_VERSION = 19
_TASK_LOCATOR_MIGRATION_NAME = "v19-derived-task-locators"
_DISPATCH_CORRELATION_MIGRATION_VERSION = 20
_DISPATCH_CORRELATION_MIGRATION_NAME = "v20-dispatch-correlation-marker"
_WORKER_CAPABILITY_MIGRATION_VERSION = 21
_WORKER_CAPABILITY_MIGRATION_NAME = "v21-worker-bootstrap-capabilities"
_DISPATCH_LEASE_MIGRATION_VERSION = 22
_DISPATCH_LEASE_MIGRATION_NAME = "v22-dispatch-lease-expiry"
_ASSIGNMENT_SCOPE_SNAPSHOT_MIGRATION_VERSION = 23
_ASSIGNMENT_SCOPE_SNAPSHOT_MIGRATION_NAME = "v23-immutable-assignment-scope"
_OUTCOME_LINKAGE_MIGRATION_VERSION = 24
_OUTCOME_LINKAGE_MIGRATION_NAME = "v24-outcome-linked-contract"
_DISPATCH_LEASE_SECONDS = 300
_STORAGE_ADMISSION_BUDGET_SECONDS = 0.8
_ADMISSION_DEADLINE: ContextVar[float | None] = ContextVar("cortex_v12_admission_deadline", default=None)
_SQLITE_ADMISSION_LOCKS: dict[str, threading.RLock] = {}
_SQLITE_ADMISSION_LOCKS_GUARD = threading.RLock()
_SQLITE_ADMISSION_LOCKS_PID = os.getpid()
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
    value: Any, *, requirements: list[str], acceptance_criteria: list[str],
    verification_plan: list[str],
) -> list[dict[str, Any]]:
    """Normalize outcome identity separately from linked acceptance evidence."""
    if value is None:
        # Private callers predating the semantic facade still receive the new
        # outcome-oriented shape. Exact public outcome grouping is supplied by
        # the facade; this deterministic fallback never creates extra items.
        result = []
        for ordinal, requirement in enumerate(requirements):
            if len(requirements) == 1:
                acceptance = list(acceptance_criteria)
                verification = [item for item in verification_plan if item not in acceptance]
            else:
                acceptance = [acceptance_criteria[ordinal]] if len(acceptance_criteria) == len(requirements) else []
                verification = [verification_plan[ordinal]] if len(verification_plan) == len(requirements) and verification_plan[ordinal] not in acceptance else []
            result.append({"requirement": requirement, "acceptance": acceptance, "verification": verification})
        return result
    if not isinstance(value, list) or len(value) != len(requirements) or not value:
        raise V12StoreError("outcome_contracts is invalid", code="invalid_argument", details={"field": "outcome_contracts"})
    result: list[dict[str, Any]] = []
    for ordinal, outcome in enumerate(value):
        if not isinstance(outcome, Mapping) or set(outcome) - {"requirement", "acceptance", "verification", "constraints"}:
            raise V12StoreError("outcome_contracts is invalid", code="invalid_argument", details={"field": "outcome_contracts"})
        requirement = _opaque_text(outcome.get("requirement"), label="outcome_contracts", maximum=TASK_CONTRACT_ITEM_MAX_LENGTH)
        acceptance = _contract_text_list(outcome.get("acceptance"), label="outcome_contracts.acceptance")
        raw_verification = outcome.get("verification", [])
        if not isinstance(raw_verification, list) or len(raw_verification) > TASK_CONTRACT_MAX_ITEMS:
            raise V12StoreError("outcome_contracts is invalid", code="invalid_argument", details={"field": "outcome_contracts"})
        verification = [
            _opaque_text(item, label="outcome_contracts.verification", maximum=TASK_CONTRACT_ITEM_MAX_LENGTH)
            for item in raw_verification
        ]
        verification = [item for item in verification if item not in acceptance]
        if requirement != requirements[ordinal]:
            raise V12StoreError("outcome_contracts disagrees with requirements", code="invalid_argument", details={"field": "outcome_contracts"})
        constraints = _contract_optional_text_list(outcome.get("constraints", []), label="outcome_contracts.constraints")
        result.append({"requirement": requirement, "acceptance": acceptance, "verification": verification, "constraints": constraints})
    return result


def _initial_outcome_details(outcome: Mapping[str, Any], ordinal: int) -> dict[str, Any]:
    acceptance = list(outcome.get("acceptance", []))
    verification = [item for item in outcome.get("verification", []) if item not in acceptance]
    fragments = [{
        "source_type": "user_request",
        "path": f"task.outcomes[{ordinal}].requirement",
        "text": str(outcome["requirement"]),
    }]
    fragments.extend({
        "source_type": "user_request",
        "path": f"task.outcomes[{ordinal}].acceptance[{index}]",
        "text": text,
    } for index, text in enumerate(acceptance))
    fragments.extend({
        "source_type": "user_request",
        "path": f"task.outcomes[{ordinal}].verification[{index}]",
        "text": text,
    } for index, text in enumerate(verification))
    return {
        "acceptance_criteria": acceptance,
        "verification_criteria": verification,
        "constraints": list(outcome.get("constraints", [])),
        "requirement_extensions": [],
        "source_fragments": fragments,
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


def _coalesce_compatible_contract_coverage(value: Any) -> Any:
    """Preserve compatible repeated evidence without weakening dispositions.

    A model can occasionally expand evidence for one assigned item as a second
    coverage row.  Treating that mechanical repetition as a failed mutation
    makes an otherwise complete first publication retry, while silently taking
    either row would lose evidence.  Coalesce only rows whose exact item and
    disposition agree, retain every unique verification string in encounter
    order, and leave malformed or conflicting rows untouched for the strict
    admission checks below to reject.
    """
    if not isinstance(value, Mapping) or not isinstance(value.get("contract_coverage"), list):
        return value
    normalized = dict(value)
    coverage: list[Any] = []
    positions: dict[str, int] = {}
    for candidate in value["contract_coverage"]:
        if not isinstance(candidate, Mapping) or set(candidate) != {"item_ref", "status", "verification"}:
            coverage.append(candidate)
            continue
        item_ref = candidate.get("item_ref")
        status = candidate.get("status")
        verification = candidate.get("verification")
        if (
            not isinstance(item_ref, str)
            or not isinstance(status, str)
            or not isinstance(verification, list)
            or any(not isinstance(item, str) for item in verification)
        ):
            coverage.append(candidate)
            continue
        prior_position = positions.get(item_ref)
        if prior_position is None:
            positions[item_ref] = len(coverage)
            coverage.append(dict(candidate))
            continue
        prior = coverage[prior_position]
        if not isinstance(prior, Mapping) or prior.get("status") != status:
            coverage.append(candidate)
            continue
        prior_verification = prior.get("verification")
        if not isinstance(prior_verification, list) or any(not isinstance(item, str) for item in prior_verification):
            coverage.append(candidate)
            continue
        merged = list(prior_verification)
        merged.extend(item for item in verification if item not in merged)
        coverage[prior_position] = {"item_ref": item_ref, "status": status, "verification": merged}
    normalized["contract_coverage"] = coverage
    return normalized


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
        store._timeline_backfilled_tasks = set()
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
        matches = cls._legacy_task_ref_matches(task_suffix)
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
    def _legacy_task_ref_matches(cls, task_suffix: str) -> list[tuple["V12Store", str]]:
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
            store._timeline_backfilled_tasks = set()
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
        store._timeline_backfilled_tasks = set()
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
        treated as an explicitly legacy recovery path: it performs the former
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
            # one legacy recovery scan, which verifies the canonical shards.
        matches = cls._legacy_record_ref_matches(suffix, label=label)
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
        store._timeline_backfilled_tasks = set()
        store._contention_deadline = None
        store._set_paths()
        return store

    @classmethod
    def _legacy_record_ref_matches(cls, suffix: str, *, label: str) -> list[tuple["V12Store", str]]:
        """One bounded compatibility scan for pre-index record locators."""
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
                if exc.code in {f"{label.removesuffix('_id')}_not_found", "delegation_not_found", "report_not_found", "initiative_not_found", "decision_not_found"}:
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
            "initiative": "initiative_id",
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
                if kind not in permitted and not (subject and kind in {"assignment", "report", "initiative", "decision"}):
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
                elif kind == "initiative":
                    initiative = self._initiative(connection, identifier)
                    if str(initiative["task_id"]) != anchor:
                        raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
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
        legacy recovery path may scan project shards.  An empty list is a
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
            "initiative_id": ("initiatives", "initiative_id"),
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
                        self._migrate_schema_v1_expansion(connection)
                        self._migrate_explicit_profile_binding(connection)
                        self._migrate_durable_native_task_name(connection)
                        self._migrate_report_consumption_receipts(connection)
                        self._migrate_durable_governance_gate(connection)
                        self._migrate_ready_approval_handles(connection)
                        self._migrate_advisory_governance(connection)
                        self._migrate_canonical_report_semantics(connection)
                        self._migrate_effective_outcome_coverage(connection)
                        self._migrate_report_coverage_diagnostics(connection)
                        self._migrate_revisioned_outcome_assignments(connection)
                        self._migrate_persisted_steering_delta(connection)
                        self._migrate_report_operations(connection)
                        self._migrate_clarification_bindings(connection)
                        self._migrate_command_receipts(connection)
                        self._migrate_plan_review_relations(connection)
                        self._migrate_clarification_holds(connection)
                        self._migrate_task_locator_publications(connection)
                        self._migrate_dispatch_correlation_marker(connection)
                        self._migrate_worker_capabilities(connection)
                        self._migrate_dispatch_lease(connection)
                        self._migrate_assignment_scope_snapshots(connection)
                        self._migrate_outcome_linkage(connection)
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
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_OUTCOME_COVERAGE_MIGRATION_VERSION, _OUTCOME_COVERAGE_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_VERSION, _REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_REVISIONED_ASSIGNMENTS_MIGRATION_VERSION, _REVISIONED_ASSIGNMENTS_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_STEERING_DELTA_MIGRATION_VERSION, _STEERING_DELTA_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_REPORT_OPERATIONS_MIGRATION_VERSION, _REPORT_OPERATIONS_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_CLARIFICATION_BINDING_MIGRATION_VERSION, _CLARIFICATION_BINDING_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_COMMAND_RECEIPTS_MIGRATION_VERSION, _COMMAND_RECEIPTS_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_PLAN_REVIEW_RELATION_MIGRATION_VERSION, _PLAN_REVIEW_RELATION_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_CLARIFICATION_HOLD_MIGRATION_VERSION, _CLARIFICATION_HOLD_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_TASK_LOCATOR_MIGRATION_VERSION, _TASK_LOCATOR_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_DISPATCH_CORRELATION_MIGRATION_VERSION, _DISPATCH_CORRELATION_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_WORKER_CAPABILITY_MIGRATION_VERSION, _WORKER_CAPABILITY_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_DISPATCH_LEASE_MIGRATION_VERSION, _DISPATCH_LEASE_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_ASSIGNMENT_SCOPE_SNAPSHOT_MIGRATION_VERSION, _ASSIGNMENT_SCOPE_SNAPSHOT_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_OUTCOME_LINKAGE_MIGRATION_VERSION, _OUTCOME_LINKAGE_MIGRATION_NAME, _now()))
                        connection.execute("INSERT INTO v12_metadata(key,value) VALUES ('project_hash', ?)", (self.project_hash,))
                        connection.execute("INSERT INTO v12_metadata(key,value) VALUES ('project_root_digest', ?)", (hashlib.sha256(str(self.project_root).encode("utf-8")).hexdigest(),))
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                    connection.execute("COMMIT")
            self._protect_canonical_database()
            self._materialize_timeline_backfills()
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
            connection.execute("CREATE TABLE user_decisions(decision_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,subject_digest TEXT,decision_type TEXT NOT NULL,prompt_en TEXT NOT NULL,response_original TEXT NOT NULL,response_en TEXT NOT NULL,user_language TEXT NOT NULL,attribution TEXT NOT NULL,supersedes_decision_id TEXT REFERENCES user_decisions(decision_id),created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,steering_delta_json TEXT)")
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

    def _migrate_effective_outcome_coverage(self, connection: sqlite3.Connection) -> None:
        """Materialize revisioned outcome items without changing task history."""
        migration = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_OUTCOME_COVERAGE_MIGRATION_VERSION,)).fetchone()
        if migration is not None:
            if str(migration[0]) != _OUTCOME_COVERAGE_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        expected = [
            (SCHEMA_VERSION, MIGRATION_NAME), (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
            (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME),
            (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME),
            (_APPROVAL_HANDLE_MIGRATION_VERSION, _APPROVAL_HANDLE_MIGRATION_NAME),
            (_ADVISORY_GOVERNANCE_MIGRATION_VERSION, _ADVISORY_GOVERNANCE_MIGRATION_NAME),
            (_REPORT_SEMANTICS_MIGRATION_VERSION, _REPORT_SEMANTICS_MIGRATION_NAME),
        ]
        if [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()] != expected:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("CREATE TABLE effective_contract_revisions(task_id TEXT NOT NULL REFERENCES tasks(task_id),revision INTEGER NOT NULL,decision_id TEXT REFERENCES user_decisions(decision_id),created_sequence INTEGER NOT NULL,PRIMARY KEY(task_id,revision))")
            connection.execute("CREATE TABLE effective_contract_items(item_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),category TEXT NOT NULL,ordinal INTEGER NOT NULL,text TEXT NOT NULL,created_revision INTEGER NOT NULL,retired_revision INTEGER,UNIQUE(task_id,category,ordinal,created_revision))")
            connection.execute("CREATE TABLE delegation_outcome_assignments(delegation_id TEXT NOT NULL REFERENCES delegations(delegation_id),item_id TEXT NOT NULL REFERENCES effective_contract_items(item_id),assignment_role TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(delegation_id,item_id,assignment_role))")
            connection.execute("CREATE UNIQUE INDEX outcome_owned_current ON delegation_outcome_assignments(item_id) WHERE assignment_role='owned'")
            connection.execute("CREATE TABLE report_contract_coverage(report_id TEXT NOT NULL REFERENCES reports(report_id),item_id TEXT NOT NULL REFERENCES effective_contract_items(item_id),status TEXT NOT NULL,verification_json TEXT NOT NULL,PRIMARY KEY(report_id,item_id))")
            connection.execute("CREATE INDEX outcome_items_task_current ON effective_contract_items(task_id,retired_revision,category,ordinal)")
            for task in connection.execute("SELECT task_id,project_hash,requirements_json,constraints_json,acceptance_criteria_json,verification_plan_json,created_sequence FROM tasks").fetchall():
                task_id = str(task["task_id"])
                connection.execute("INSERT INTO effective_contract_revisions(task_id,revision,decision_id,created_sequence) VALUES (?, 1, NULL, ?)", (task_id, int(task["created_sequence"])))
                for category, column in (("requirement", "requirements_json"), ("constraint", "constraints_json"), ("acceptance", "acceptance_criteria_json"), ("verification", "verification_plan_json")):
                    values = _load_json(str(task[column]), label="task contract")
                    for ordinal, text in enumerate(values if isinstance(values, list) else []):
                        connection.execute("INSERT INTO effective_contract_items(item_id,project_hash,task_id,category,ordinal,text,created_revision,retired_revision) VALUES (?, ?, ?, ?, ?, ?, 1, NULL)", ("outcome-" + uuid.uuid4().hex, str(task["project_hash"]), task_id, category, ordinal, str(text)))
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_OUTCOME_COVERAGE_MIGRATION_VERSION, _OUTCOME_COVERAGE_MIGRATION_NAME, _now()))
            expected_post_migration = [*expected, (_OUTCOME_COVERAGE_MIGRATION_VERSION, _OUTCOME_COVERAGE_MIGRATION_NAME)]
            if [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()] != expected_post_migration:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_report_coverage_diagnostics(self, connection: sqlite3.Connection) -> None:
        """Persist coverage diagnostics without rejecting immutable report transport."""
        migration = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_VERSION,)).fetchone()
        if migration is not None:
            if str(migration[0]) != _REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        expected = [
            (SCHEMA_VERSION, MIGRATION_NAME), (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
            (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME),
            (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME),
            (_APPROVAL_HANDLE_MIGRATION_VERSION, _APPROVAL_HANDLE_MIGRATION_NAME),
            (_ADVISORY_GOVERNANCE_MIGRATION_VERSION, _ADVISORY_GOVERNANCE_MIGRATION_NAME),
            (_REPORT_SEMANTICS_MIGRATION_VERSION, _REPORT_SEMANTICS_MIGRATION_NAME),
            (_OUTCOME_COVERAGE_MIGRATION_VERSION, _OUTCOME_COVERAGE_MIGRATION_NAME),
        ]
        if [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()] != expected or "coverage_diagnostics_json" in self._column_names(connection, "reports"):
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("DROP TRIGGER reports_terminal_no_update")
            connection.execute("ALTER TABLE reports ADD COLUMN coverage_diagnostics_json TEXT NOT NULL DEFAULT '[]'")
            connection.execute("CREATE TRIGGER reports_terminal_no_update BEFORE UPDATE ON reports WHEN OLD.assembly_state IN ('finalized','aborted') BEGIN SELECT RAISE(ABORT,'terminal reports are immutable'); END")
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_VERSION, _REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_revisioned_outcome_assignments(self, connection: sqlite3.Connection) -> None:
        """Keep ownership history while making only one assignment current.

        The v10 assignment table deliberately retained every row, but its
        partial unique index treated a replaced parent owner as permanently
        current.  This additive migration records explicit supersession and
        makes the active owner relation queryable without rewriting reports.
        """
        migration = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_REVISIONED_ASSIGNMENTS_MIGRATION_VERSION,)).fetchone()
        if migration is not None:
            if str(migration[0]) != _REVISIONED_ASSIGNMENTS_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        expected = [
            (SCHEMA_VERSION, MIGRATION_NAME), (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
            (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME),
            (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME),
            (_APPROVAL_HANDLE_MIGRATION_VERSION, _APPROVAL_HANDLE_MIGRATION_NAME),
            (_ADVISORY_GOVERNANCE_MIGRATION_VERSION, _ADVISORY_GOVERNANCE_MIGRATION_NAME),
            (_REPORT_SEMANTICS_MIGRATION_VERSION, _REPORT_SEMANTICS_MIGRATION_NAME),
            (_OUTCOME_COVERAGE_MIGRATION_VERSION, _OUTCOME_COVERAGE_MIGRATION_NAME),
            (_REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_VERSION, _REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_NAME),
        ]
        if [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()] != expected:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("DROP INDEX outcome_owned_current")
            connection.execute("ALTER TABLE delegation_outcome_assignments ADD COLUMN superseded_by_delegation_id TEXT REFERENCES delegations(delegation_id)")
            connection.execute("ALTER TABLE delegation_outcome_assignments ADD COLUMN superseded_sequence INTEGER")
            connection.execute("CREATE UNIQUE INDEX outcome_owned_current ON delegation_outcome_assignments(item_id) WHERE assignment_role='owned' AND superseded_by_delegation_id IS NULL")
            connection.execute("CREATE INDEX outcome_assignment_current ON delegation_outcome_assignments(item_id,assignment_role,superseded_by_delegation_id)")
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_REVISIONED_ASSIGNMENTS_MIGRATION_VERSION, _REVISIONED_ASSIGNMENTS_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_persisted_steering_delta(self, connection: sqlite3.Connection) -> None:
        """Retain the narrow steering effect needed by later worker briefs."""
        migration = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_STEERING_DELTA_MIGRATION_VERSION,)).fetchone()
        if migration is not None:
            if str(migration[0]) != _STEERING_DELTA_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        expected = [
            (SCHEMA_VERSION, MIGRATION_NAME), (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
            (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME),
            (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME),
            (_APPROVAL_HANDLE_MIGRATION_VERSION, _APPROVAL_HANDLE_MIGRATION_NAME),
            (_ADVISORY_GOVERNANCE_MIGRATION_VERSION, _ADVISORY_GOVERNANCE_MIGRATION_NAME),
            (_REPORT_SEMANTICS_MIGRATION_VERSION, _REPORT_SEMANTICS_MIGRATION_NAME),
            (_OUTCOME_COVERAGE_MIGRATION_VERSION, _OUTCOME_COVERAGE_MIGRATION_NAME),
            (_REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_VERSION, _REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_NAME),
            (_REVISIONED_ASSIGNMENTS_MIGRATION_VERSION, _REVISIONED_ASSIGNMENTS_MIGRATION_NAME),
        ]
        if [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()] != expected:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            if "steering_delta_json" not in self._column_names(connection, "user_decisions"):
                connection.execute("ALTER TABLE user_decisions ADD COLUMN steering_delta_json TEXT")
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_STEERING_DELTA_MIGRATION_VERSION, _STEERING_DELTA_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_report_operations(self, connection: sqlite3.Connection) -> None:
        """Add the durable ledger for atomic semantic report publication.

        No historical rows are inferred: this table records only operations
        written by the new domain publication path.
        """
        migration = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_REPORT_OPERATIONS_MIGRATION_VERSION,)).fetchone()
        if migration is not None:
            if str(migration[0]) != _REPORT_OPERATIONS_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        expected = [
            (SCHEMA_VERSION, MIGRATION_NAME), (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
            (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME),
            (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME),
            (_APPROVAL_HANDLE_MIGRATION_VERSION, _APPROVAL_HANDLE_MIGRATION_NAME),
            (_ADVISORY_GOVERNANCE_MIGRATION_VERSION, _ADVISORY_GOVERNANCE_MIGRATION_NAME),
            (_REPORT_SEMANTICS_MIGRATION_VERSION, _REPORT_SEMANTICS_MIGRATION_NAME),
            (_OUTCOME_COVERAGE_MIGRATION_VERSION, _OUTCOME_COVERAGE_MIGRATION_NAME),
            (_REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_VERSION, _REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_NAME),
            (_REVISIONED_ASSIGNMENTS_MIGRATION_VERSION, _REVISIONED_ASSIGNMENTS_MIGRATION_NAME),
            (_STEERING_DELTA_MIGRATION_VERSION, _STEERING_DELTA_MIGRATION_NAME),
        ]
        if [tuple(row) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()] != expected:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "report_operations" in tables:
                expected_columns = {"operation_id", "task_id", "delegation_id", "kind", "payload_digest", "report_id", "created_at"}
                if self._column_names(connection, "report_operations") != expected_columns:
                    raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            else:
                connection.execute("CREATE TABLE report_operations(operation_id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(task_id),delegation_id TEXT NOT NULL REFERENCES delegations(delegation_id),kind TEXT NOT NULL,payload_digest TEXT NOT NULL,report_id TEXT NOT NULL REFERENCES reports(report_id),created_at TEXT NOT NULL,UNIQUE(delegation_id,kind))")
                connection.execute("CREATE INDEX report_operations_task ON report_operations(task_id,created_at)")
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_REPORT_OPERATIONS_MIGRATION_VERSION, _REPORT_OPERATIONS_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_clarification_bindings(self, connection: sqlite3.Connection) -> None:
        """Add the durable server-issued one-shot clarification relation."""
        row = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_CLARIFICATION_BINDING_MIGRATION_VERSION,)).fetchone()
        if row is not None:
            if str(row[0]) != _CLARIFICATION_BINDING_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        previous = connection.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        if previous is None or int(previous[0]) != _REPORT_OPERATIONS_MIGRATION_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        connection.execute("CREATE TABLE clarification_bindings(clarification_binding TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,assignment_id TEXT,decision_type TEXT NOT NULL,prompt_digest TEXT NOT NULL,prompt TEXT NOT NULL,prompt_language TEXT NOT NULL,effective_contract_revision INTEGER NOT NULL,issue_sequence INTEGER NOT NULL,request_digest TEXT NOT NULL,response_digest TEXT,consumed_decision_id TEXT REFERENCES user_decisions(decision_id),created_at TEXT NOT NULL,UNIQUE(task_id,subject_type,subject_id,decision_type,prompt_digest,effective_contract_revision))")
        connection.execute("CREATE INDEX clarification_bindings_task_pending ON clarification_bindings(task_id,consumed_decision_id,issue_sequence)")
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_CLARIFICATION_BINDING_MIGRATION_VERSION, _CLARIFICATION_BINDING_MIGRATION_NAME, _now()))

    def _migrate_command_receipts(self, connection: sqlite3.Connection) -> None:
        """Add the domain-level receipt table without rewriting historical data.

        Receipts are deliberately separate from the legacy ``idempotency``
        table.  The latter records transport-era operations; this table records
        semantic command slots and is the authority for migrated commands.
        """
        row = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_COMMAND_RECEIPTS_MIGRATION_VERSION,)).fetchone()
        if row is not None:
            if str(row[0]) != _COMMAND_RECEIPTS_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        previous = connection.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        if previous is None or int(previous[0]) != _CLARIFICATION_BINDING_MIGRATION_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        tables = {str(r[0]) for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "command_receipts" in tables:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("CREATE TABLE command_receipts(command_ref TEXT PRIMARY KEY,project_hash TEXT NOT NULL,aggregate_type TEXT NOT NULL,aggregate_id TEXT NOT NULL,command_name TEXT NOT NULL,logical_slot TEXT NOT NULL,request_digest TEXT NOT NULL,status TEXT NOT NULL,result_json TEXT NOT NULL,build_id TEXT,created_sequence INTEGER NOT NULL,completed_sequence INTEGER,created_at TEXT NOT NULL,completed_at TEXT,UNIQUE(project_hash,logical_slot))")
            connection.execute("CREATE INDEX command_receipts_aggregate ON command_receipts(project_hash,aggregate_type,aggregate_id,created_sequence)")
            connection.execute("CREATE INDEX command_receipts_command ON command_receipts(project_hash,command_name,created_sequence)")
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_COMMAND_RECEIPTS_MIGRATION_VERSION, _COMMAND_RECEIPTS_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_plan_review_relations(self, connection: sqlite3.Connection) -> None:
        """Persist immutable plan/view evidence on each new review binding.

        Existing rows remain historical evidence.  Only bindings issued after
        this forward-only migration can carry the server-verified relation.
        """
        row = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_PLAN_REVIEW_RELATION_MIGRATION_VERSION,)).fetchone()
        if row is not None:
            if str(row[0]) != _PLAN_REVIEW_RELATION_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        previous = connection.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        if previous is None or int(previous[0]) != _COMMAND_RECEIPTS_MIGRATION_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            columns = self._column_names(connection, "clarification_bindings")
            additions = {
                "plan_content_digest": "TEXT",
                "plan_approval_handle": "TEXT REFERENCES approval_handles(approval_handle)",
                "plan_view_content_digest": "TEXT",
                "plan_view_source_sequence": "INTEGER",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE clarification_bindings ADD COLUMN {name} {declaration}")
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_PLAN_REVIEW_RELATION_MIGRATION_VERSION, _PLAN_REVIEW_RELATION_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_clarification_holds(self, connection: sqlite3.Connection) -> None:
        """Add the typed clarification-to-worker continuation relation.

        A hold is deliberately separate from the historical decision binding:
        the binding proves the user decision identity while this row proves
        whether an originating assignment needs one exact post-answer delivery.
        The generated continuation capability is private host material.  It is
        never rendered into an MCP result and cannot be reconstructed from a
        durable ID, assignment name, or prompt text.
        """
        row = connection.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (_CLARIFICATION_HOLD_MIGRATION_VERSION,),
        ).fetchone()
        if row is not None:
            if str(row[0]) != _CLARIFICATION_HOLD_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        previous = connection.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        if previous is None or int(previous[0]) != _PLAN_REVIEW_RELATION_MIGRATION_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        tables = {str(value[0]) for value in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "clarification_holds" in tables:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute(
                "CREATE TABLE clarification_holds("
                "clarification_binding TEXT PRIMARY KEY REFERENCES clarification_bindings(clarification_binding),"
                "project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),"
                "assignment_id TEXT REFERENCES delegations(delegation_id),"
                "native_dispatch_digest TEXT,continuation_capability TEXT UNIQUE,"
                "state TEXT NOT NULL,response_decision_id TEXT REFERENCES user_decisions(decision_id),"
                "delivery_claim_digest TEXT,opened_sequence INTEGER NOT NULL,answered_sequence INTEGER,delivery_sequence INTEGER,"
                "unavailable_reason TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
                "CHECK(state IN ('pending_question','pending_delivery','delivery_claimed','delivered','coordinator_completed','unavailable','stale','superseded'))"
                ")"
            )
            connection.execute("CREATE INDEX clarification_holds_assignment_state ON clarification_holds(assignment_id,state,opened_sequence)")
            connection.execute("CREATE INDEX clarification_holds_task_state ON clarification_holds(task_id,state,opened_sequence)")
            connection.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)",
                (_CLARIFICATION_HOLD_MIGRATION_VERSION, _CLARIFICATION_HOLD_MIGRATION_NAME, _now()),
            )
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_task_locator_publications(self, connection: sqlite3.Connection) -> None:
        """Add canonical route-publication evidence for the derived task index.

        This is intentionally not a second authority.  It is transactionally
        coupled to each shard's task row, and is used solely to reconstruct
        the root-local accelerator after a crash, tamper, or version upgrade.
        """
        row = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_TASK_LOCATOR_MIGRATION_VERSION,)).fetchone()
        if row is not None:
            if str(row[0]) != _TASK_LOCATOR_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        previous = connection.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        if previous is None or int(previous[0]) != _CLARIFICATION_HOLD_MIGRATION_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        if "task_locator_publications" in {str(value[0]) for value in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("CREATE TABLE task_locator_publications(task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),project_hash TEXT NOT NULL,suffix TEXT NOT NULL,fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(project_hash,task_id))")
            connection.execute("CREATE INDEX task_locator_publications_suffix ON task_locator_publications(suffix,task_id)")
            for task in connection.execute("SELECT task_id,project_hash,created_at FROM tasks").fetchall():
                identifier = str(task["task_id"])
                compact = task_ref(identifier)
                if compact is None:
                    raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
                connection.execute(
                    "INSERT INTO task_locator_publications(task_id,project_hash,suffix,fingerprint,created_at) VALUES (?, ?, ?, ?, ?)",
                    (identifier, str(task["project_hash"]), identifier[-12:], self._task_locator_fingerprint(identifier), str(task["created_at"])),
                )
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_TASK_LOCATOR_MIGRATION_VERSION, _TASK_LOCATOR_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_dispatch_correlation_marker(self, connection: sqlite3.Connection) -> None:
        """Add a random, non-authorizing host-observation marker per assignment."""
        row = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_DISPATCH_CORRELATION_MIGRATION_VERSION,)).fetchone()
        if row is not None:
            if str(row[0]) != _DISPATCH_CORRELATION_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        previous = connection.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        if previous is None or int(previous[0]) != _TASK_LOCATOR_MIGRATION_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        columns = self._column_names(connection, "delegations")
        if "dispatch_correlation_marker" in columns or "dispatch_correlation_digest" in columns:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute("ALTER TABLE delegations ADD COLUMN dispatch_correlation_marker TEXT")
            connection.execute("ALTER TABLE delegations ADD COLUMN dispatch_correlation_digest TEXT")
            # Historical assignments cannot be safely assigned a marker after
            # their worker may already have started. They remain explicitly
            # uncorrelated rather than receiving reconstructed evidence.
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_DISPATCH_CORRELATION_MIGRATION_VERSION, _DISPATCH_CORRELATION_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_worker_capabilities(self, connection: sqlite3.Connection) -> None:
        """Add the server-owned, one-shot worker bootstrap capability ledger.

        This is intentionally separate from native hook identity.  The row
        binds the exact Cortex assignment and build provenance, while the
        opaque capability is consumed once by a worker and yields one scoped
        continuation capability.  All transitions are append-only timeline
        facts plus a constrained state update in one SQLite transaction.
        """
        row = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_WORKER_CAPABILITY_MIGRATION_VERSION,)).fetchone()
        if row is not None:
            if str(row[0]) != _WORKER_CAPABILITY_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        previous = connection.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        if previous is None or int(previous[0]) != _DISPATCH_CORRELATION_MIGRATION_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        tables = {str(value[0]) for value in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "worker_capabilities" in tables:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute(
                "CREATE TABLE worker_capabilities("
                "capability_ref TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),"
                "assignment_id TEXT NOT NULL REFERENCES delegations(delegation_id),contract_revision INTEGER NOT NULL,"
                "build_digest TEXT NOT NULL,candidate_digest TEXT NOT NULL,source_digest TEXT NOT NULL,catalogue_digest TEXT NOT NULL,"
                "dispatch_digest TEXT NOT NULL,capability_digest TEXT NOT NULL,continuation_ref TEXT UNIQUE,"
                "state TEXT NOT NULL,created_sequence INTEGER NOT NULL,consumed_sequence INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
                "CHECK(state IN ('minted','consumed','stale','conflict')),UNIQUE(assignment_id,contract_revision),"
                "FOREIGN KEY(task_id) REFERENCES tasks(task_id))"
            )
            connection.execute("CREATE INDEX worker_capabilities_task_state ON worker_capabilities(task_id,state,created_sequence)")
            connection.execute("CREATE INDEX worker_capabilities_assignment ON worker_capabilities(assignment_id,contract_revision)")
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_WORKER_CAPABILITY_MIGRATION_VERSION, _WORKER_CAPABILITY_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_dispatch_lease(self, connection: sqlite3.Connection) -> None:
        """Add the bounded server-owned lease for an unconsumed dispatch.

        ``worker_capabilities.state == 'minted'`` is the authoritative active
        lease.  The expiry is deliberately stored beside that capability so a
        parent-linked replacement cannot infer that a host dispatch happened
        merely because the host API returned without correlation telemetry.
        Expiry is only a bounded recovery boundary: callers must still observe
        the current row and transition it to ``stale`` before replacement.
        """
        row = connection.execute("SELECT name FROM schema_migrations WHERE version=?", (_DISPATCH_LEASE_MIGRATION_VERSION,)).fetchone()
        if row is not None:
            if str(row[0]) != _DISPATCH_LEASE_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        expected = [
            (SCHEMA_VERSION, MIGRATION_NAME), (_EXPANSION_MIGRATION_VERSION, _EXPANSION_MIGRATION_NAME),
            (_PROFILE_BINDING_MIGRATION_VERSION, _PROFILE_BINDING_MIGRATION_NAME),
            (_NATIVE_TASK_NAME_MIGRATION_VERSION, _NATIVE_TASK_NAME_MIGRATION_NAME),
            (_REPORT_CONSUMPTION_MIGRATION_VERSION, _REPORT_CONSUMPTION_MIGRATION_NAME),
            (_GOVERNANCE_GATE_MIGRATION_VERSION, _GOVERNANCE_GATE_MIGRATION_NAME),
            (_APPROVAL_HANDLE_MIGRATION_VERSION, _APPROVAL_HANDLE_MIGRATION_NAME),
            (_ADVISORY_GOVERNANCE_MIGRATION_VERSION, _ADVISORY_GOVERNANCE_MIGRATION_NAME),
            (_REPORT_SEMANTICS_MIGRATION_VERSION, _REPORT_SEMANTICS_MIGRATION_NAME),
            (_OUTCOME_COVERAGE_MIGRATION_VERSION, _OUTCOME_COVERAGE_MIGRATION_NAME),
            (_REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_VERSION, _REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_NAME),
            (_REVISIONED_ASSIGNMENTS_MIGRATION_VERSION, _REVISIONED_ASSIGNMENTS_MIGRATION_NAME),
            (_STEERING_DELTA_MIGRATION_VERSION, _STEERING_DELTA_MIGRATION_NAME),
            (_REPORT_OPERATIONS_MIGRATION_VERSION, _REPORT_OPERATIONS_MIGRATION_NAME),
            (_CLARIFICATION_BINDING_MIGRATION_VERSION, _CLARIFICATION_BINDING_MIGRATION_NAME),
            (_COMMAND_RECEIPTS_MIGRATION_VERSION, _COMMAND_RECEIPTS_MIGRATION_NAME),
            (_PLAN_REVIEW_RELATION_MIGRATION_VERSION, _PLAN_REVIEW_RELATION_MIGRATION_NAME),
            (_CLARIFICATION_HOLD_MIGRATION_VERSION, _CLARIFICATION_HOLD_MIGRATION_NAME),
            (_TASK_LOCATOR_MIGRATION_VERSION, _TASK_LOCATOR_MIGRATION_NAME),
            (_DISPATCH_CORRELATION_MIGRATION_VERSION, _DISPATCH_CORRELATION_MIGRATION_NAME),
            (_WORKER_CAPABILITY_MIGRATION_VERSION, _WORKER_CAPABILITY_MIGRATION_NAME),
        ]
        if [tuple(item) for item in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()] != expected:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            columns = self._column_names(connection, "worker_capabilities")
            if "lease_expires_at" in columns:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            connection.execute("ALTER TABLE worker_capabilities ADD COLUMN lease_expires_at TEXT")
            expiry = (datetime.now(timezone.utc) + timedelta(seconds=_DISPATCH_LEASE_SECONDS)).isoformat()
            # Existing minted rows are conservatively granted one bounded
            # reconciliation window; consumed/stale rows remain closed.
            connection.execute("UPDATE worker_capabilities SET lease_expires_at=? WHERE state='minted'", (expiry,))
            connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)", (_DISPATCH_LEASE_MIGRATION_VERSION, _DISPATCH_LEASE_MIGRATION_NAME, _now()))
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_assignment_scope_snapshots(self, connection: sqlite3.Connection) -> None:
        """Separate immutable assignment responsibility from mutable ownership.

        Historical rows are backfilled from the assignment relation without
        consulting its current supersession state.  From this migration on,
        publication and continuation history use only this immutable table;
        ``delegation_outcome_assignments`` remains the scheduling projection.
        """
        row = connection.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (_ASSIGNMENT_SCOPE_SNAPSHOT_MIGRATION_VERSION,),
        ).fetchone()
        if row is not None:
            if str(row[0]) != _ASSIGNMENT_SCOPE_SNAPSHOT_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        previous = connection.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        if previous is None or int(previous[0]) != _DISPATCH_LEASE_MIGRATION_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        tables = {str(value[0]) for value in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "assignment_scope_snapshots" in tables:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute(
                "CREATE TABLE assignment_scope_snapshots("
                "assignment_id TEXT NOT NULL REFERENCES delegations(delegation_id),"
                "task_id TEXT NOT NULL REFERENCES tasks(task_id),"
                "item_id TEXT NOT NULL REFERENCES effective_contract_items(item_id),"
                "assignment_role TEXT NOT NULL,contract_revision INTEGER NOT NULL,"
                "created_sequence INTEGER NOT NULL,"
                "PRIMARY KEY(assignment_id,item_id,assignment_role))"
            )
            connection.execute(
                "INSERT INTO assignment_scope_snapshots(assignment_id,task_id,item_id,assignment_role,contract_revision,created_sequence) "
                "SELECT a.delegation_id,d.task_id,a.item_id,a.assignment_role,a.revision,d.created_sequence "
                "FROM delegation_outcome_assignments a JOIN delegations d ON d.delegation_id=a.delegation_id"
            )
            planners = connection.execute(
                "SELECT d.delegation_id,d.task_id,d.created_sequence,"
                "COALESCE(w.contract_revision,(SELECT MAX(r.revision) FROM effective_contract_revisions r WHERE r.task_id=d.task_id AND r.created_sequence<=d.created_sequence),1) AS contract_revision "
                "FROM delegations d LEFT JOIN worker_capabilities w ON w.assignment_id=d.delegation_id "
                "WHERE d.profile_name='planner'"
            ).fetchall()
            for planner in planners:
                connection.execute(
                    "INSERT OR IGNORE INTO assignment_scope_snapshots(assignment_id,task_id,item_id,assignment_role,contract_revision,created_sequence) "
                    "SELECT ?,?,i.item_id,'planning',?,? FROM effective_contract_items i "
                    "WHERE i.task_id=? AND i.created_revision<=? AND (i.retired_revision IS NULL OR i.retired_revision>?)",
                    (planner["delegation_id"], planner["task_id"], planner["contract_revision"], planner["created_sequence"], planner["task_id"], planner["contract_revision"], planner["contract_revision"]),
                )
            connection.execute("CREATE INDEX assignment_scope_task_revision ON assignment_scope_snapshots(task_id,contract_revision,assignment_id)")
            connection.execute("CREATE TRIGGER assignment_scope_no_update BEFORE UPDATE ON assignment_scope_snapshots BEGIN SELECT RAISE(ABORT,'assignment scope snapshots are immutable'); END")
            connection.execute("CREATE TRIGGER assignment_scope_no_delete BEFORE DELETE ON assignment_scope_snapshots BEGIN SELECT RAISE(ABORT,'assignment scope snapshots are immutable'); END")
            connection.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)",
                (_ASSIGNMENT_SCOPE_SNAPSHOT_MIGRATION_VERSION, _ASSIGNMENT_SCOPE_SNAPSHOT_MIGRATION_NAME, _now()),
            )
        except sqlite3.DatabaseError as exc:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported") from exc

    def _migrate_outcome_linkage(self, connection: sqlite3.Connection) -> None:
        """Attach source-grounded criteria to coverage outcomes.

        Historical item identity remains immutable. New task writes use only
        outcome rows; this side table keeps linked criteria and provenance out
        of the coverage-item namespace.
        """
        row = connection.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (_OUTCOME_LINKAGE_MIGRATION_VERSION,),
        ).fetchone()
        if row is not None:
            if str(row[0]) != _OUTCOME_LINKAGE_MIGRATION_NAME:
                raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
            return
        previous = connection.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        if previous is None or int(previous[0]) != _ASSIGNMENT_SCOPE_SNAPSHOT_MIGRATION_VERSION:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        tables = {str(value[0]) for value in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "effective_contract_item_details" in tables:
            raise V12StoreError("V12 database schema is unsupported", code="schema_unsupported")
        try:
            connection.execute(
                "CREATE TABLE effective_contract_item_details("
                "item_id TEXT PRIMARY KEY REFERENCES effective_contract_items(item_id),"
                "details_json TEXT NOT NULL,source_decision_id TEXT REFERENCES user_decisions(decision_id))"
            )
            for item in connection.execute(
                "SELECT item_id,category,ordinal,text FROM effective_contract_items ORDER BY task_id,category,ordinal,item_id"
            ).fetchall():
                category = str(item["category"])
                details = {
                    "acceptance_criteria": [], "verification_criteria": [],
                    "constraints": [], "requirement_extensions": [],
                    "source_fragments": [{
                        "source_type": "legacy_task_contract",
                        "path": f"task.{category}[{int(item['ordinal'])}]",
                        "text": str(item["text"]),
                    }],
                }
                connection.execute(
                    "INSERT INTO effective_contract_item_details(item_id,details_json,source_decision_id) VALUES (?, ?, NULL)",
                    (item["item_id"], _canonical_json(details, label="effective contract item details")),
                )
            connection.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES (?, ?, ?)",
                (_OUTCOME_LINKAGE_MIGRATION_VERSION, _OUTCOME_LINKAGE_MIGRATION_NAME, _now()),
            )
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
            (_OUTCOME_COVERAGE_MIGRATION_VERSION, _OUTCOME_COVERAGE_MIGRATION_NAME),
            (_REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_VERSION, _REPORT_COVERAGE_DIAGNOSTICS_MIGRATION_NAME),
            (_REVISIONED_ASSIGNMENTS_MIGRATION_VERSION, _REVISIONED_ASSIGNMENTS_MIGRATION_NAME),
            (_STEERING_DELTA_MIGRATION_VERSION, _STEERING_DELTA_MIGRATION_NAME),
            (_REPORT_OPERATIONS_MIGRATION_VERSION, _REPORT_OPERATIONS_MIGRATION_NAME),
            (_CLARIFICATION_BINDING_MIGRATION_VERSION, _CLARIFICATION_BINDING_MIGRATION_NAME),
            (_COMMAND_RECEIPTS_MIGRATION_VERSION, _COMMAND_RECEIPTS_MIGRATION_NAME),
            (_PLAN_REVIEW_RELATION_MIGRATION_VERSION, _PLAN_REVIEW_RELATION_MIGRATION_NAME),
            (_CLARIFICATION_HOLD_MIGRATION_VERSION, _CLARIFICATION_HOLD_MIGRATION_NAME),
            (_TASK_LOCATOR_MIGRATION_VERSION, _TASK_LOCATOR_MIGRATION_NAME),
            (_DISPATCH_CORRELATION_MIGRATION_VERSION, _DISPATCH_CORRELATION_MIGRATION_NAME),
            (_WORKER_CAPABILITY_MIGRATION_VERSION, _WORKER_CAPABILITY_MIGRATION_NAME),
            (_DISPATCH_LEASE_MIGRATION_VERSION, _DISPATCH_LEASE_MIGRATION_NAME),
            (_ASSIGNMENT_SCOPE_SNAPSHOT_MIGRATION_VERSION, _ASSIGNMENT_SCOPE_SNAPSHOT_MIGRATION_NAME),
            (_OUTCOME_LINKAGE_MIGRATION_VERSION, _OUTCOME_LINKAGE_MIGRATION_NAME),
        ] or metadata is None or str(metadata[0]) != self.project_hash:
            raise V12StoreError("reference belongs to another project", code="cross_project_reference")
        required_columns = {
            "tasks": {"task_id", "project_hash", "project_root", "objective", "user_request_original", "user_language", "task_contract_version", "requirements_json", "constraints_json", "acceptance_criteria_json", "verification_plan_json", "context_json"},
            "delegations": {"delegation_id", "task_id", "profile_name", "native_task_name", "input_report_ids_json", "input_decision_ids_json", "dispatch_correlation_marker", "dispatch_correlation_digest"},
            "reports": {"report_id", "task_id", "assembly_state", "next_chunk_index", "total_chunks", "total_bytes", "content_digest", "supersedes_report_id", "review_policy", "semantic_status", "coverage_diagnostics_json"},
            "report_operations": {"operation_id", "task_id", "delegation_id", "kind", "payload_digest", "report_id"},
            "report_chunks": {"report_id", "chunk_index", "section", "content_json", "content_digest", "content_bytes"},
            "report_usage": {"task_id", "total_retained_bytes", "assembling_bytes", "assembling_reports"},
            "timeline": {"sequence", "task_id", "decision_id", "payload_json"},
            "user_decisions": {"decision_id", "task_id", "subject_type", "subject_id", "decision_type", "response_original", "response_en", "attribution", "steering_delta_json"},
            "projection_jobs": {"job_id", "task_id", "source_sequence", "status"},
            "projection_files": {"task_id", "relative_path", "content_digest", "status"},
            "report_consumption_receipts": {"task_id", "consumer_delegation_id", "reader_kind", "report_id", "observed_content_digest", "sections_json", "input_cursor", "output_cursor", "chunk_indexes_json", "returned_content_bytes", "has_more", "created_sequence"},
            "clarification_bindings": {"clarification_binding", "project_hash", "task_id", "subject_type", "subject_id", "decision_type", "prompt_digest", "prompt", "prompt_language", "effective_contract_revision", "issue_sequence", "request_digest", "response_digest", "consumed_decision_id", "plan_content_digest", "plan_approval_handle", "plan_view_content_digest", "plan_view_source_sequence"},
            "clarification_holds": {"clarification_binding", "project_hash", "task_id", "assignment_id", "native_dispatch_digest", "continuation_capability", "state", "response_decision_id", "delivery_claim_digest", "opened_sequence", "answered_sequence", "delivery_sequence", "unavailable_reason", "created_at", "updated_at"},
            "worker_capabilities": {"capability_ref", "project_hash", "task_id", "assignment_id", "contract_revision", "build_digest", "candidate_digest", "source_digest", "catalogue_digest", "dispatch_digest", "capability_digest", "continuation_ref", "state", "created_sequence", "consumed_sequence", "created_at", "updated_at", "lease_expires_at"},
            "task_locator_publications": {"task_id", "project_hash", "suffix", "fingerprint", "created_at"},
            "approval_handles": {"approval_handle", "task_id", "report_id", "report_content_digest", "view_relative_path", "view_content_digest", "view_source_sequence", "request_digest", "created_sequence", "consumed_decision_id"},
            "effective_contract_revisions": {"task_id", "revision", "created_sequence"},
            "effective_contract_items": {"item_id", "task_id", "category", "ordinal", "text", "created_revision", "retired_revision"},
            "effective_contract_item_details": {"item_id", "details_json", "source_decision_id"},
            "delegation_outcome_assignments": {"delegation_id", "item_id", "assignment_role", "revision", "superseded_by_delegation_id", "superseded_sequence"},
            "assignment_scope_snapshots": {"assignment_id", "task_id", "item_id", "assignment_role", "contract_revision", "created_sequence"},
            "report_contract_coverage": {"report_id", "item_id", "status", "verification_json"},
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
        if not {"reports_terminal_no_update", "reports_no_delete", "report_chunks_no_update", "report_chunks_no_delete", "decisions_no_update", "decisions_no_delete", "decisions_task_created", "report_chunks_report_order", "timeline_decision_sequence", "projection_jobs_pending", "consumption_task_sequence", "consumption_delegation_report", "approval_handles_task_report", "clarification_bindings_task_pending", "clarification_holds_assignment_state", "clarification_holds_task_state", "task_locator_publications_suffix", "assignment_scope_task_revision", "assignment_scope_no_update", "assignment_scope_no_delete"}.issubset(objects):
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
        self._with_storage_admission(lambda: self._verify_known_task_once(task_id))

    def _verify_known_task_once(self, task_id: str) -> None:
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
                    self._migrate_effective_outcome_coverage(connection)
                    self._migrate_report_coverage_diagnostics(connection)
                    self._migrate_revisioned_outcome_assignments(connection)
                    self._migrate_persisted_steering_delta(connection)
                    self._migrate_report_operations(connection)
                    self._migrate_clarification_bindings(connection)
                    self._migrate_command_receipts(connection)
                    self._migrate_plan_review_relations(connection)
                    self._migrate_clarification_holds(connection)
                    self._migrate_task_locator_publications(connection)
                    self._migrate_dispatch_correlation_marker(connection)
                    self._migrate_worker_capabilities(connection)
                    self._migrate_dispatch_lease(connection)
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
                    self._migrate_schema_v1_expansion(connection)
                    self._migrate_explicit_profile_binding(connection)
                    self._migrate_durable_native_task_name(connection)
                    self._migrate_report_consumption_receipts(connection)
                    self._migrate_durable_governance_gate(connection)
                    self._migrate_ready_approval_handles(connection)
                    self._migrate_advisory_governance(connection)
                    self._migrate_canonical_report_semantics(connection)
                    self._migrate_effective_outcome_coverage(connection)
                    self._migrate_report_coverage_diagnostics(connection)
                    self._migrate_revisioned_outcome_assignments(connection)
                    self._migrate_persisted_steering_delta(connection)
                    self._migrate_report_operations(connection)
                    self._migrate_clarification_bindings(connection)
                    self._migrate_command_receipts(connection)
                    self._migrate_plan_review_relations(connection)
                    self._migrate_clarification_holds(connection)
                    self._migrate_task_locator_publications(connection)
                    self._migrate_dispatch_correlation_marker(connection)
                    self._migrate_worker_capabilities(connection)
                    self._migrate_dispatch_lease(connection)
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
        CREATE TABLE delegations(delegation_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),parent_delegation_id TEXT REFERENCES delegations(delegation_id),native_task_name TEXT NOT NULL,dispatch_correlation_marker TEXT,dispatch_correlation_digest TEXT,objective TEXT NOT NULL,role TEXT NOT NULL,profile_name TEXT NOT NULL,scope TEXT NOT NULL,instructions TEXT NOT NULL,input_report_ids_json TEXT NOT NULL,input_decision_ids_json TEXT NOT NULL,model TEXT NOT NULL,reasoning_effort TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE reports(report_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),delegation_id TEXT NOT NULL REFERENCES delegations(delegation_id),report_type TEXT NOT NULL,status TEXT,semantic_status TEXT,coverage_diagnostics_json TEXT NOT NULL DEFAULT '[]',assembly_state TEXT NOT NULL,next_chunk_index INTEGER NOT NULL,total_chunks INTEGER NOT NULL,total_bytes INTEGER NOT NULL,content_digest TEXT NOT NULL,supersedes_report_id TEXT REFERENCES reports(report_id),review_policy TEXT,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,finalized_at TEXT,finalized_sequence INTEGER,aborted_at TEXT,aborted_sequence INTEGER,abort_reason_en TEXT);
        CREATE TABLE report_chunks(report_id TEXT NOT NULL REFERENCES reports(report_id),chunk_index INTEGER NOT NULL,section TEXT NOT NULL,content_json TEXT NOT NULL,content_digest TEXT NOT NULL,content_bytes INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(report_id,chunk_index));
        CREATE TABLE report_consumption_receipts(receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),consumer_delegation_id TEXT REFERENCES delegations(delegation_id),reader_kind TEXT NOT NULL,report_id TEXT NOT NULL REFERENCES reports(report_id),observed_content_digest TEXT NOT NULL,sections_json TEXT NOT NULL,input_cursor TEXT,output_cursor TEXT,chunk_indexes_json TEXT NOT NULL,returned_content_bytes INTEGER NOT NULL,has_more INTEGER NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE report_usage(task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),total_retained_bytes INTEGER NOT NULL,assembling_bytes INTEGER NOT NULL,assembling_reports INTEGER NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE governance_assessments(assessment_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),initiative_id TEXT,mode TEXT NOT NULL,source TEXT NOT NULL,rationale TEXT,risk_factors_json TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE initiatives(initiative_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,goal TEXT NOT NULL,risk TEXT,status TEXT NOT NULL,notes_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,latest_revision INTEGER NOT NULL,created_sequence INTEGER NOT NULL,updated_sequence INTEGER NOT NULL);
        CREATE TABLE initiative_revisions(revision_id INTEGER PRIMARY KEY AUTOINCREMENT,initiative_id TEXT NOT NULL REFERENCES initiatives(initiative_id),revision_number INTEGER NOT NULL,project_hash TEXT NOT NULL,occurred_at TEXT NOT NULL,sequence INTEGER NOT NULL,payload_json TEXT NOT NULL,UNIQUE(initiative_id,revision_number));
        CREATE TABLE initiative_links(link_id INTEGER PRIMARY KEY AUTOINCREMENT,initiative_id TEXT NOT NULL REFERENCES initiatives(initiative_id),project_hash TEXT NOT NULL,relationship TEXT NOT NULL,target_id TEXT NOT NULL,is_resolved INTEGER NOT NULL,warnings_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(initiative_id,relationship,target_id));
        CREATE TABLE governance_closures(closure_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,verdict TEXT NOT NULL,evidence_json TEXT NOT NULL,unresolved_risks_json TEXT NOT NULL,follow_ups_json TEXT NOT NULL,initiative_status TEXT,completion_notes_json TEXT,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL);
        CREATE TABLE user_decisions(decision_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,subject_digest TEXT,decision_type TEXT NOT NULL,prompt_en TEXT NOT NULL,response_original TEXT NOT NULL,response_en TEXT NOT NULL,user_language TEXT NOT NULL,attribution TEXT NOT NULL,supersedes_decision_id TEXT REFERENCES user_decisions(decision_id),created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,steering_delta_json TEXT);
        CREATE TABLE approval_handles(approval_handle TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),report_id TEXT NOT NULL REFERENCES reports(report_id),report_content_digest TEXT NOT NULL,view_relative_path TEXT NOT NULL,view_content_digest TEXT NOT NULL,view_source_sequence INTEGER NOT NULL,request_digest TEXT NOT NULL,created_at TEXT NOT NULL,created_sequence INTEGER NOT NULL,consumed_decision_id TEXT REFERENCES user_decisions(decision_id),UNIQUE(task_id,report_id,report_content_digest,view_content_digest,view_source_sequence));
        CREATE TABLE clarification_bindings(clarification_binding TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,assignment_id TEXT,decision_type TEXT NOT NULL,prompt_digest TEXT NOT NULL,prompt TEXT NOT NULL,prompt_language TEXT NOT NULL,effective_contract_revision INTEGER NOT NULL,issue_sequence INTEGER NOT NULL,request_digest TEXT NOT NULL,response_digest TEXT,consumed_decision_id TEXT REFERENCES user_decisions(decision_id),created_at TEXT NOT NULL,plan_content_digest TEXT,plan_approval_handle TEXT REFERENCES approval_handles(approval_handle),plan_view_content_digest TEXT,plan_view_source_sequence INTEGER,UNIQUE(task_id,subject_type,subject_id,decision_type,prompt_digest,effective_contract_revision));
        CREATE TABLE clarification_holds(clarification_binding TEXT PRIMARY KEY REFERENCES clarification_bindings(clarification_binding),project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),assignment_id TEXT REFERENCES delegations(delegation_id),native_dispatch_digest TEXT,continuation_capability TEXT UNIQUE,state TEXT NOT NULL,response_decision_id TEXT REFERENCES user_decisions(decision_id),delivery_claim_digest TEXT,opened_sequence INTEGER NOT NULL,answered_sequence INTEGER,delivery_sequence INTEGER,unavailable_reason TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,CHECK(state IN ('pending_question','pending_delivery','delivery_claimed','delivered','coordinator_completed','unavailable','stale','superseded')));
        CREATE TABLE worker_capabilities(capability_ref TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),assignment_id TEXT NOT NULL REFERENCES delegations(delegation_id),contract_revision INTEGER NOT NULL,build_digest TEXT NOT NULL,candidate_digest TEXT NOT NULL,source_digest TEXT NOT NULL,catalogue_digest TEXT NOT NULL,dispatch_digest TEXT NOT NULL,capability_digest TEXT NOT NULL,continuation_ref TEXT UNIQUE,state TEXT NOT NULL,created_sequence INTEGER NOT NULL,consumed_sequence INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,lease_expires_at TEXT,CHECK(state IN ('minted','consumed','stale','conflict')),UNIQUE(assignment_id,contract_revision));
        CREATE TABLE task_locator_publications(task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),project_hash TEXT NOT NULL,suffix TEXT NOT NULL,fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(project_hash,task_id));
        CREATE TABLE command_receipts(command_ref TEXT PRIMARY KEY,project_hash TEXT NOT NULL,aggregate_type TEXT NOT NULL,aggregate_id TEXT NOT NULL,command_name TEXT NOT NULL,logical_slot TEXT NOT NULL,request_digest TEXT NOT NULL,status TEXT NOT NULL,result_json TEXT NOT NULL,build_id TEXT,created_sequence INTEGER NOT NULL,completed_sequence INTEGER,created_at TEXT NOT NULL,completed_at TEXT,UNIQUE(project_hash,logical_slot));
        CREATE TABLE projection_jobs(job_id INTEGER PRIMARY KEY AUTOINCREMENT,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),source_sequence INTEGER NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL,lease_token TEXT,lease_expires_at TEXT,last_error_code TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(task_id,source_sequence,reason));
        CREATE TABLE projection_files(task_id TEXT NOT NULL REFERENCES tasks(task_id),relative_path TEXT NOT NULL,source_sequence INTEGER NOT NULL,renderer_version TEXT NOT NULL,content_digest TEXT NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(task_id,relative_path));
        CREATE TABLE effective_contract_revisions(task_id TEXT NOT NULL REFERENCES tasks(task_id),revision INTEGER NOT NULL,decision_id TEXT REFERENCES user_decisions(decision_id),created_sequence INTEGER NOT NULL,PRIMARY KEY(task_id,revision));
        CREATE TABLE effective_contract_items(item_id TEXT PRIMARY KEY,project_hash TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),category TEXT NOT NULL,ordinal INTEGER NOT NULL,text TEXT NOT NULL,created_revision INTEGER NOT NULL,retired_revision INTEGER,UNIQUE(task_id,category,ordinal,created_revision));
        CREATE TABLE effective_contract_item_details(item_id TEXT PRIMARY KEY REFERENCES effective_contract_items(item_id),details_json TEXT NOT NULL,source_decision_id TEXT REFERENCES user_decisions(decision_id));
        CREATE TABLE delegation_outcome_assignments(delegation_id TEXT NOT NULL REFERENCES delegations(delegation_id),item_id TEXT NOT NULL REFERENCES effective_contract_items(item_id),assignment_role TEXT NOT NULL,revision INTEGER NOT NULL,superseded_by_delegation_id TEXT REFERENCES delegations(delegation_id),superseded_sequence INTEGER,PRIMARY KEY(delegation_id,item_id,assignment_role));
        CREATE TABLE assignment_scope_snapshots(assignment_id TEXT NOT NULL REFERENCES delegations(delegation_id),task_id TEXT NOT NULL REFERENCES tasks(task_id),item_id TEXT NOT NULL REFERENCES effective_contract_items(item_id),assignment_role TEXT NOT NULL,contract_revision INTEGER NOT NULL,created_sequence INTEGER NOT NULL,PRIMARY KEY(assignment_id,item_id,assignment_role));
        CREATE TABLE report_contract_coverage(report_id TEXT NOT NULL REFERENCES reports(report_id),item_id TEXT NOT NULL REFERENCES effective_contract_items(item_id),status TEXT NOT NULL,verification_json TEXT NOT NULL,PRIMARY KEY(report_id,item_id));
        CREATE TABLE report_operations(operation_id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(task_id),delegation_id TEXT NOT NULL REFERENCES delegations(delegation_id),kind TEXT NOT NULL,payload_digest TEXT NOT NULL,report_id TEXT NOT NULL REFERENCES reports(report_id),created_at TEXT NOT NULL,UNIQUE(delegation_id,kind));
        CREATE TABLE idempotency(operation TEXT NOT NULL,idempotency_key TEXT NOT NULL,payload_digest TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(operation,idempotency_key));
        CREATE INDEX timeline_task_sequence ON timeline(task_id,sequence);
        CREATE INDEX timeline_delegation_sequence ON timeline(delegation_id,sequence);
        CREATE INDEX timeline_initiative_sequence ON timeline(initiative_id,sequence);
        CREATE INDEX reports_task_created ON reports(task_id,created_sequence);
        CREATE INDEX reports_delegation_created ON reports(delegation_id,created_sequence);
        CREATE INDEX report_chunks_report_order ON report_chunks(report_id,chunk_index);
        CREATE INDEX report_operations_task ON report_operations(task_id,created_at);
        CREATE INDEX consumption_task_sequence ON report_consumption_receipts(task_id,created_sequence);
        CREATE INDEX consumption_delegation_report ON report_consumption_receipts(consumer_delegation_id,report_id,created_sequence);
        CREATE INDEX assessments_task_created ON governance_assessments(task_id,created_sequence);
        CREATE INDEX initiative_links_source ON initiative_links(initiative_id,relationship);
        CREATE INDEX decisions_task_created ON user_decisions(task_id,created_sequence);
        CREATE INDEX approval_handles_task_report ON approval_handles(task_id,report_id,created_sequence);
        CREATE INDEX clarification_bindings_task_pending ON clarification_bindings(task_id,consumed_decision_id,issue_sequence);
        CREATE INDEX clarification_holds_assignment_state ON clarification_holds(assignment_id,state,opened_sequence);
        CREATE INDEX clarification_holds_task_state ON clarification_holds(task_id,state,opened_sequence);
        CREATE INDEX worker_capabilities_task_state ON worker_capabilities(task_id,state,created_sequence);
        CREATE INDEX worker_capabilities_assignment ON worker_capabilities(assignment_id,contract_revision);
        CREATE INDEX task_locator_publications_suffix ON task_locator_publications(suffix,task_id);
        CREATE INDEX command_receipts_aggregate ON command_receipts(project_hash,aggregate_type,aggregate_id,created_sequence);
        CREATE INDEX command_receipts_command ON command_receipts(project_hash,command_name,created_sequence);
        CREATE INDEX timeline_decision_sequence ON timeline(decision_id,sequence);
        CREATE INDEX projection_jobs_pending ON projection_jobs(status,lease_expires_at,job_id);
        CREATE UNIQUE INDEX outcome_owned_current ON delegation_outcome_assignments(item_id) WHERE assignment_role='owned' AND superseded_by_delegation_id IS NULL;
        CREATE INDEX outcome_assignment_current ON delegation_outcome_assignments(item_id,assignment_role,superseded_by_delegation_id);
        CREATE INDEX assignment_scope_task_revision ON assignment_scope_snapshots(task_id,contract_revision,assignment_id);
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
            "CREATE TRIGGER report_chunks_no_delete BEFORE DELETE ON report_chunks BEGIN SELECT RAISE(ABORT,'report chunks are immutable'); END",
            "CREATE TRIGGER decisions_no_update BEFORE UPDATE ON user_decisions BEGIN SELECT RAISE(ABORT,'decisions are append-only'); END",
            "CREATE TRIGGER decisions_no_delete BEFORE DELETE ON user_decisions BEGIN SELECT RAISE(ABORT,'decisions are append-only'); END",
        ):
            connection.execute(statement)

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
        self, *, logical_slot: str, request_digest: str,
    ) -> tuple[dict[str, Any], bool] | None:
        """Read-only receipt convergence after a bounded write-contention wait."""
        try:
            row = self.lookup_command_receipt(logical_slot)
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

    def lookup_command_receipt(self, logical_slot: object) -> dict[str, Any] | None:
        """Return the server-owned receipt for one logical command slot."""
        slot = _required_text(logical_slot, label="logical_slot", maximum=TEXT_MAX_LENGTH)
        return self._read(lambda connection: _row(connection.execute(
            "SELECT command_ref,project_hash,aggregate_type,aggregate_id,command_name,logical_slot,request_digest,status,result_json,build_id,created_sequence,completed_sequence,created_at,completed_at FROM command_receipts WHERE project_hash=? AND logical_slot=?",
            (self.project_hash, slot),
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
                "SELECT request_digest,status,result_json FROM command_receipts WHERE project_hash=? AND logical_slot=?",
                (self.project_hash, slot),
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
                "SELECT request_digest,result_json FROM command_receipts WHERE project_hash=? AND logical_slot=?",
                (self.project_hash, slot),
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
        # later resolution uses its explicit legacy-recovery scan rather than
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
                or plan["status"] != "completed" or plan.get("semantic_status") != "semantic_valid"):
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
        # later governance, initiative, or unrelated task chronology must not
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
        """Project outcome from current required coverage, never report order.

        Finalized report counts remain neutral diagnostics.  The outcome itself
        is intentionally derived only from the active effective contract and
        its current-owner claims, so historical replacement attempts and
        optional reports cannot turn a completed contract back into failure.
        """
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

    def _inferred_assignment_predecessor(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        profile_name: str,
        input_report_ids: list[str],
        input_decision_ids: list[str],
        explicit_parent_delegation_id: str | None,
        assignment_policy: str | None = None,
    ) -> dict[str, Any] | None:
        """Derive one predecessor from immutable server-owned evidence.

        A plan-review revision decision already names the exact immutable plan,
        and every input report already names its publishing assignment.  Making
        the model repeat either relation is redundant and allowed live calls to
        preserve the content while silently losing the revision DAG.  The
        server therefore derives the relation and treats a contradictory
        caller-supplied parent as a conflict.
        """
        from cortex_runtime.worker_message import packaged_profile_assignment_policy
        if profile_name != "planner":
            policy = assignment_policy or packaged_profile_assignment_policy(profile_name)
            # Delivery rework inherits the unique current owner represented by
            # its immutable input reports. Evidence rechecks inherit only from
            # a non-owning evidence author, allowing the new report to
            # explicitly supersede a failed/partial finding without confusing
            # an implementation report consumed for first-pass review.
            active_authors: dict[str, dict[str, Any]] = {}
            for report_id in input_report_ids:
                report = self._report(connection, report_id, task_id=task_id)
                author = report.get("delegation_id")
                if not isinstance(author, str):
                    continue
                roles = {
                    str(row[0]) for row in connection.execute(
                        "SELECT DISTINCT assignment_role FROM delegation_outcome_assignments "
                        "WHERE delegation_id=? AND superseded_by_delegation_id IS NULL",
                        (author,),
                    ).fetchall()
                }
                if policy == "owner":
                    matches = "owned" in roles
                elif policy == "review":
                    matches = bool(roles & {"evidence", "contributing"}) and "owned" not in roles
                else:
                    matches = False
                if matches:
                    active_authors[author] = report
            if len(active_authors) > 1:
                raise V12StoreError(
                    "assignment predecessor evidence is ambiguous",
                    code="invalid_argument",
                    details={"field": "input_report_refs"},
                )
            predecessor = next(iter(active_authors.values()), None)
            if predecessor is not None and explicit_parent_delegation_id is not None:
                if predecessor.get("delegation_id") != explicit_parent_delegation_id:
                    raise V12StoreError(
                        "assignment parent conflicts with predecessor evidence",
                        code="invalid_argument",
                        details={"field": "parent_assignment_ref"},
                    )
            return predecessor
        plan_inputs = {
            str(report["report_id"]): report
            for report in (
                self._report(connection, report_id, task_id=task_id)
                for report_id in input_report_ids
            )
            if report.get("report_type") == "plan"
        }
        decision_targets: set[str] = set()
        for decision_id in input_decision_ids:
            decision = self._decision(connection, decision_id, task_id=task_id)
            if decision.get("decision_type") != "request_revision":
                continue
            if decision.get("subject_type") != "plan" or not isinstance(decision.get("subject_id"), str):
                raise V12StoreError(
                    "plan revision decision has an invalid subject",
                    code="invalid_argument",
                    details={"field": "input_decision_refs"},
                )
            target = str(decision["subject_id"])
            if target not in plan_inputs:
                raise V12StoreError(
                    "plan revision decision requires its plan evidence",
                    code="invalid_argument",
                    details={"field": "input_report_refs"},
                )
            decision_targets.add(target)
        if len(decision_targets) > 1:
            raise V12StoreError(
                "plan revision evidence is ambiguous",
                code="invalid_argument",
                details={"field": "input_decision_refs"},
            )
        predecessor: dict[str, Any] | None = None
        if decision_targets:
            predecessor = plan_inputs[next(iter(decision_targets))]
        elif len(plan_inputs) == 1:
            predecessor = next(iter(plan_inputs.values()))
        elif explicit_parent_delegation_id is not None:
            authored = [
                report for report in plan_inputs.values()
                if report.get("delegation_id") == explicit_parent_delegation_id
            ]
            if len(authored) == 1:
                predecessor = authored[0]
        if predecessor is not None and explicit_parent_delegation_id is not None:
            if predecessor.get("delegation_id") != explicit_parent_delegation_id:
                raise V12StoreError(
                    "plan revision parent conflicts with predecessor evidence",
                    code="invalid_argument",
                    details={"field": "parent_assignment_ref"},
                )
        return predecessor

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
            "requirement_extensions": list(details.get("requirement_extensions", [])),
            "source_fragments": list(details.get("source_fragments", [])),
            "created_revision": int(row["created_revision"]),
        }
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
        contract = self._effective_contract(connection, task_id)
        rows = []
        for item in contract["items"]:
            suffix = str(item["item_ref"])[2:]
            item_id = str(connection.execute("SELECT item_id FROM effective_contract_items WHERE task_id=? AND item_id LIKE ?", (task_id, "%" + suffix)).fetchone()[0])
            owner = connection.execute(
                "SELECT delegation_id FROM delegation_outcome_assignments "
                "WHERE item_id=? AND assignment_role='owned' AND superseded_by_delegation_id IS NULL",
                (item_id,),
            ).fetchone()
            all_claims = connection.execute(
                "SELECT c.status AS claim_status,c.verification_json,r.report_id,r.status AS report_status,r.delegation_id "
                "FROM report_contract_coverage c JOIN reports r ON r.report_id=c.report_id "
                "WHERE c.item_id=? AND r.assembly_state='finalized' "
                "ORDER BY r.finalized_sequence,r.report_id",
                (item_id,),
            ).fetchall()
            claims = [] if owner is None else [row for row in all_claims if str(row["delegation_id"]) == str(owner["delegation_id"])]
            statuses = {str(row["claim_status"]) for row in claims}
            if owner is None:
                status, reason = "missing", "no_owned_delegation"
            elif not claims:
                status, reason = "missing", "no_finalized_coverage"
            elif len(statuses) > 1:
                status, reason = "contradictory", "conflicting_claims"
            elif "not_applicable" in statuses:
                status, reason = "stale", "not_applicable_claim"
            elif any(str(row["report_status"]) != "completed" for row in claims):
                status, reason = "partial", "non_completed_report"
            elif "partial" in statuses:
                status, reason = "partial", "partial_claim"
            elif "unverified" in statuses or any(not _load_json(str(row["verification_json"]), label="coverage verification") for row in claims):
                status, reason = "unverified", "unverified_claim"
            else:
                status, reason = "complete", "current_verified_claim"
            current_ids = {str(row["report_id"]) for row in claims}
            rows.append({
                "item_ref": item["item_ref"], "status": status, "reason": reason,
                "report_refs": [record_ref(str(row["report_id"])) for row in claims],
                "superseded_report_refs": [record_ref(str(row["report_id"])) for row in all_claims if str(row["report_id"]) not in current_ids],
            })
        statuses = {str(row["status"]) for row in rows}
        if rows and statuses == {"complete"}:
            overall = "ready"
        elif rows and statuses <= {"complete", "unverified"}:
            overall = "ready_with_risks"
        else:
            overall = "rework"
        return {"status": overall, "items": rows}

    def _conformance_review(self, connection: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        """Build advisory closure evidence from immutable current-ledger records.

        This is deliberately a projection, not a backend gate: callers see why
        current evidence is ready, risky, or requires rework without changing
        report/delegation lifecycle or mutating historic v1 evidence.
        """
        contract = self._effective_contract(connection, task_id)
        coverage = self._aggregate_coverage(connection, task_id)
        decisions = [record_ref(str(row[0])) for row in connection.execute(
            "SELECT decision_id FROM user_decisions WHERE task_id=? ORDER BY created_sequence,decision_id", (task_id,)
        ).fetchall()]
        report_records = connection.execute(
            "SELECT report_id,content_digest,total_chunks FROM reports "
            "WHERE task_id=? AND assembly_state='finalized' ORDER BY finalized_sequence,report_id",
            (task_id,),
        ).fetchall()
        reports = [
            {"report_ref": record_ref(str(row["report_id"])), "content_digest": str(row["content_digest"])}
            for row in report_records
        ]
        manifest_by_ref = {str(item["report_ref"]): str(item["content_digest"]) for item in reports}
        coverage_refs = {
            str(report_ref)
            for item in coverage["items"]
            for report_ref in item.get("report_refs", [])
            if isinstance(report_ref, str)
        }
        active_refs = {
            str(record_ref(str(row["report_id"])))
            for row in connection.execute(
                "SELECT DISTINCT r.report_id FROM reports r "
                "JOIN assignment_scope_snapshots s ON s.assignment_id=r.delegation_id "
                "LEFT JOIN reports replacement ON replacement.supersedes_report_id=r.report_id "
                "AND replacement.assembly_state='finalized' "
                "WHERE r.task_id=? AND r.assembly_state='finalized' "
                "AND replacement.report_id IS NULL AND s.contract_revision=?",
                (task_id, int(contract["revision"])),
            ).fetchall()
        }
        supporting_refs = sorted(coverage_refs | active_refs)
        required_manifests = [
            {"report_ref": ref, "content_digest": manifest_by_ref.get(ref)}
            for ref in supporting_refs
        ]
        consumed_refs: set[str] = set()
        consumed_digests: set[str] = set()
        records_by_ref = {str(record_ref(str(row["report_id"]))): row for row in report_records}
        for ref in supporting_refs:
            report_row = records_by_ref.get(ref)
            if report_row is None:
                continue
            report_id = str(report_row["report_id"])
            digest = str(report_row["content_digest"])
            observed: set[int] = set()
            for receipt in connection.execute(
                "SELECT observed_content_digest,sections_json,chunk_indexes_json "
                "FROM report_consumption_receipts WHERE task_id=? AND report_id=? "
                "AND reader_kind='coordinator' ORDER BY created_sequence",
                (task_id, report_id),
            ).fetchall():
                if str(receipt["observed_content_digest"]) != digest:
                    continue
                if _load_json(str(receipt["sections_json"]), label="report read sections") is not None:
                    continue
                indexes = _load_json(str(receipt["chunk_indexes_json"]), label="report receipt chunks")
                if isinstance(indexes, list):
                    observed.update(index for index in indexes if isinstance(index, int))
            if observed == set(range(int(report_row["total_chunks"]))):
                consumed_refs.add(ref)
                consumed_digests.add(digest)
        unconsumed_refs = [ref for ref in supporting_refs if ref not in consumed_refs]
        evidence_defects = [
            {
                "report_ref": record_ref(str(row["report_id"])),
                "report_status": str(row["report_status"]),
                "item_ref": self._outcome_ref(str(row["item_id"])),
                "disposition": str(row["claim_status"]),
            }
            for row in connection.execute(
                "SELECT DISTINCT r.report_id,r.status AS report_status,c.item_id,c.status AS claim_status "
                "FROM reports r JOIN report_contract_coverage c ON c.report_id=r.report_id "
                "JOIN assignment_scope_snapshots s ON s.assignment_id=r.delegation_id AND s.item_id=c.item_id "
                "LEFT JOIN reports replacement ON replacement.supersedes_report_id=r.report_id AND replacement.assembly_state='finalized' "
                "WHERE r.task_id=? AND r.assembly_state='finalized' AND replacement.report_id IS NULL "
                "AND s.contract_revision=? AND s.assignment_role IN ('evidence','contributing') "
                "AND (r.status!='completed' OR c.status!='complete') "
                "ORDER BY r.finalized_sequence,r.report_id,c.item_id",
                (task_id, int(contract["revision"])),
            ).fetchall()
        ]
        status = {"ready": "ready", "ready_with_risks": "ready_with_risks"}.get(coverage["status"], "not_ready")
        if unconsumed_refs or evidence_defects:
            status = "not_ready"
        return {
            "effective_revision": contract["revision"],
            "status": status,
            "recommendation": "ready" if status == "ready" else "ready_with_risks" if status == "ready_with_risks" else "rework",
            "decision_refs": decisions,
            "aggregate_coverage": coverage,
            "report_manifests": reports,
            "consumed_report_digests": sorted(consumed_digests),
            "required_report_manifests": required_manifests,
            "unconsumed_report_refs": unconsumed_refs,
            "unresolved_evidence": evidence_defects,
        }

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
        return value

    def create_task(self, *, objective: Any, user_request_original: Any, user_language: Any, requirements: Any, constraints: Any, acceptance_criteria: Any, verification_plan: Any, outcome_contracts: Any = None, context: Any = None, task_id: Any = None, idempotency_key: Any = None, task_contract_version: Any = TASK_CONTRACT_VERSION) -> tuple[dict[str, Any], bool]:
        english_objective = _opaque_text(objective, label="objective")
        normalized_requirements = _contract_text_list(requirements, label="requirements")
        normalized_acceptance = _contract_text_list(acceptance_criteria, label="acceptance_criteria")
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
                acceptance_criteria=normalized_acceptance,
                verification_plan=normalized_verification,
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
            return {"task": self._task(connection, identifier)}
        return self._mutation("create_task", payload, idempotency_key, write)

    def create_delegation(self, *, task_id: Any, objective: Any, role: Any, profile_name: Any, scope: Any, instructions: Any, delegation_id: Any = None, parent_delegation_id: Any = None, input_report_ids: Any = None, input_decision_ids: Any = None, outcome_assignments: Any = None, model: Any = None, reasoning_effort: Any = None, idempotency_key: Any = None, bootstrap_provenance: Mapping[str, Any] | None = None, derive_assignment_scope: bool = False, assignment_policy: Any = None) -> tuple[dict[str, Any], bool]:
        try:
            selection = validate_model_selection(model, reasoning_effort)
        except ValueError as exc:
            raise V12StoreError("model selection is invalid", code="invalid_model_selection") from exc
        if outcome_assignments is None:
            assignments: dict[str, list[str]] = {"owned": [], "contributing": [], "evidence_producing": []}
        elif isinstance(outcome_assignments, Mapping) and set(outcome_assignments).issubset({"owned", "contributing", "evidence_producing"}):
            assignments = {}
            for kind in ("owned", "contributing", "evidence_producing"):
                values = outcome_assignments.get(kind, [])
                if not isinstance(values, list) or len(values) > TASK_CONTRACT_MAX_ITEMS or len(set(values)) != len(values) or any(not isinstance(item, str) for item in values):
                    raise V12StoreError("outcome assignments are invalid", code="invalid_argument", details={"field": "outcome_assignments"})
                assignments[kind] = list(values)
        else:
            raise V12StoreError("outcome assignments are invalid", code="invalid_argument", details={"field": "outcome_assignments"})
        if not isinstance(derive_assignment_scope, bool):
            raise V12StoreError("assignment scope policy is invalid", code="invalid_argument")
        from cortex_runtime.worker_message import packaged_profile_assignment_policy
        resolved_policy = packaged_profile_assignment_policy(profile_name) if assignment_policy is None else assignment_policy
        if resolved_policy not in {"owner", "review", "planning"}:
            raise V12StoreError("assignment responsibility is invalid", code="invalid_argument", details={"field": "assignment_policy"})
        if (resolved_policy == "planning") != (profile_name == "planner"):
            raise V12StoreError("planning responsibility and planner profile must agree", code="invalid_argument", details={"field": "assignment_policy"})
        payload = {"task_id": self._task_identifier(task_id), "objective": _opaque_text(objective, label="objective"), "role": _opaque_text(role, label="role", maximum=ROLE_MAX_LENGTH), "profile_name": _profile_name(profile_name), "scope": _opaque_text(scope, label="scope"), "instructions": _instructions_text(instructions), "delegation_id": None if delegation_id is None else self._record_identifier(delegation_id, label="delegation_id"), "parent_delegation_id": None if parent_delegation_id is None else self._record_identifier(parent_delegation_id, label="parent_delegation_id"), "input_report_ids": _identifier_list(input_report_ids, label="input_report_ids", maximum=MAX_REPORT_IDS, deduplicate=True), "input_decision_ids": _identifier_list(input_decision_ids, label="input_decision_ids", maximum=MAX_DECISION_IDS, deduplicate=True), "outcome_assignments": assignments, "model": selection.model, "reasoning_effort": selection.reasoning_effort, "derive_assignment_scope": derive_assignment_scope, "assignment_policy": resolved_policy}
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, payload["task_id"])
            self._require_no_pending_user_decision(connection, task_id=str(task["task_id"]))
            input_reports: list[dict[str, Any]] = []
            for report_id in payload["input_report_ids"]:
                report = self._report(connection, report_id, task_id=task["task_id"])
                if report["assembly_state"] != "finalized":
                    raise V12StoreError("input handoff report is not finalized", code="report_state_conflict")
                input_reports.append(report)
            for decision_id in payload["input_decision_ids"]:
                self._decision(connection, decision_id, task_id=task["task_id"])
            predecessor = self._inferred_assignment_predecessor(
                connection,
                task_id=str(task["task_id"]),
                profile_name=str(payload["profile_name"]),
                input_report_ids=list(payload["input_report_ids"]),
                input_decision_ids=list(payload["input_decision_ids"]),
                explicit_parent_delegation_id=payload["parent_delegation_id"],
                assignment_policy=str(payload["assignment_policy"]),
            )
            if predecessor is not None and payload["parent_delegation_id"] is None:
                payload["parent_delegation_id"] = str(predecessor["delegation_id"])
            if payload["parent_delegation_id"] is not None:
                self._delegation(connection, payload["parent_delegation_id"], task_id=task["task_id"])
                # A successful server-side assignment mint owns a dispatch
                # lease until the worker consumes it.  Host-side absence of a
                # SubagentStart correlation event is not evidence that this
                # lease ended, so parent-linked rework is rejected atomically
                # while the lease remains active.
                self._reconcile_dispatch_lease_in_transaction(
                    connection, task_id=str(task["task_id"]),
                    assignment_id=str(payload["parent_delegation_id"]),
                )
            # A host dispatch can commit and then lose its response before
            # SubagentStart is observed.  Mutation-digest replay covers
            # byte-identical requests, but a resend may contain incidental
            # wording changes.  Reconcile by stable execution boundary before
            # deriving ownership or minting another active assignment.
            current_revision = self._effective_contract(connection, str(task["task_id"]))["revision"]
            logical_match = connection.execute(
                "SELECT d.* FROM delegations d JOIN worker_capabilities c ON c.assignment_id=d.delegation_id "
                "WHERE d.task_id=? AND d.project_hash=? AND c.contract_revision=? AND c.state='minted' "
                "AND d.role=? AND d.profile_name=? AND d.scope=? AND d.parent_delegation_id IS ? "
                "AND d.input_report_ids_json=? AND d.input_decision_ids_json=? "
                "ORDER BY d.created_sequence,d.delegation_id LIMIT 1",
                (task["task_id"], self.project_hash, current_revision, payload["role"],
                 payload["profile_name"], payload["scope"], payload["parent_delegation_id"],
                 _canonical_json(payload["input_report_ids"], label="input_report_ids"),
                 _canonical_json(payload["input_decision_ids"], label="input_decision_ids")),
            ).fetchone()
            if logical_match is not None:
                existing_roles = {
                    str(row[0]) for row in connection.execute(
                        "SELECT DISTINCT assignment_role FROM delegation_outcome_assignments WHERE delegation_id=?",
                        (str(logical_match["delegation_id"]),),
                    ).fetchall()
                }
                existing_policy = "owner" if "owned" in existing_roles else "review" if existing_roles & {"contributing", "evidence"} else "planning" if logical_match["profile_name"] == "planner" else None
                if existing_policy != payload["assignment_policy"]:
                    logical_match = None
            if logical_match is not None:
                existing = self._delegation(connection, str(logical_match["delegation_id"]), task_id=task["task_id"])
                brief = self._worker_brief(connection, task, existing)
                return {"delegation": existing, "dispatch_brief": brief["dispatch_brief"], "renderer": brief["renderer"], "replayed": True}
            assignment_ids = {kind: [self._outcome_item_id(connection, str(task["task_id"]), item) for item in values] for kind, values in payload["outcome_assignments"].items()}
            parent_owned: set[str] = set()
            if payload["parent_delegation_id"] is not None:
                parent_owned = {
                    str(row[0]) for row in connection.execute(
                        "SELECT item_id FROM delegation_outcome_assignments WHERE delegation_id=? AND assignment_role='owned' AND superseded_by_delegation_id IS NULL",
                        (payload["parent_delegation_id"],),
                    ).fetchall()
                }
            assignment_policy = str(payload["assignment_policy"])
            if (
                not any(assignment_ids.values())
                and assignment_policy in {"owner", "review"}
                and (
                    payload["derive_assignment_scope"]
                    or assignment_policy == "review"
                    or payload["parent_delegation_id"] is not None
                    or bool(payload["input_report_ids"])
                )
            ):
                # Public assignment opening does not accept caller-selected
                # outcome item routing. Derive scope from typed predecessor
                # coverage when available. An initial owner without predecessor
                # evidence still owns the current effective catalogue: simple
                # bounded execution is valid without a planner, and leaving
                # that first owner unscoped makes every successful publication
                # appear as ``no_owned_delegation`` at closure.
                scoped_items: set[str] = set(parent_owned)
                if assignment_policy == "owner" and payload["parent_delegation_id"] is not None:
                    # A parent-linked owner continues the predecessor's
                    # immutable ownership and must also pick up contract items
                    # introduced after that predecessor snapshot which do not
                    # yet have a current owner. Otherwise steering can create
                    # permanently unowned requirement/acceptance/verification
                    # items even though the follow-on exists specifically to
                    # implement that revision.
                    parent_revision_row = connection.execute(
                        "SELECT MAX(contract_revision) FROM assignment_scope_snapshots WHERE assignment_id=?",
                        (payload["parent_delegation_id"],),
                    ).fetchone()
                    parent_revision = int(parent_revision_row[0]) if parent_revision_row and parent_revision_row[0] is not None else current_revision
                    scoped_items.update(
                        str(row[0]) for row in connection.execute(
                            "SELECT i.item_id FROM effective_contract_items i "
                            "LEFT JOIN delegation_outcome_assignments a ON a.item_id=i.item_id "
                            "AND a.assignment_role='owned' AND a.superseded_by_delegation_id IS NULL "
                            "WHERE i.task_id=? AND i.retired_revision IS NULL AND i.created_revision>? AND a.item_id IS NULL "
                            "ORDER BY i.category,i.ordinal,i.item_id",
                            (task["task_id"], parent_revision),
                        ).fetchall()
                    )
                if not scoped_items:
                    for report_id in payload["input_report_ids"]:
                        for chunk in self._report_chunks(connection, report_id):
                            content = chunk.get("content")
                            claims = content.get("contract_coverage") if isinstance(content, Mapping) else None
                            if isinstance(claims, list):
                                for claim in claims:
                                    if isinstance(claim, Mapping) and isinstance(claim.get("item_ref"), str):
                                        try:
                                            scoped_items.add(self._outcome_item_id(connection, str(task["task_id"]), claim["item_ref"]))
                                        except V12StoreError:
                                            continue
                if not scoped_items:
                    scoped_items = {
                        str(row[0]) for row in connection.execute(
                            "SELECT item_id FROM effective_contract_items WHERE task_id=? AND retired_revision IS NULL ORDER BY category,ordinal,item_id",
                            (task["task_id"],),
                        ).fetchall()
                    }
                assignment_ids["owned"] = sorted(scoped_items) if assignment_policy == "owner" else []
                assignment_ids["contributing"] = [] if assignment_policy == "owner" else sorted(scoped_items)
                assignment_ids["evidence_producing"] = sorted(scoped_items) if assignment_policy == "review" else []
            for item_id in assignment_ids["owned"]:
                current_owner = connection.execute(
                    "SELECT delegation_id FROM delegation_outcome_assignments WHERE item_id=? AND assignment_role='owned' AND superseded_by_delegation_id IS NULL",
                    (item_id,),
                ).fetchone()
                if current_owner is not None and (
                    payload["parent_delegation_id"] is None or str(current_owner["delegation_id"]) != str(payload["parent_delegation_id"])
                ):
                    raise V12StoreError("outcome item already has an active owner", code="outcome_assignment_conflict")
            identifier = str(payload["delegation_id"] or new_sharded_id("delegation", self.project_hash))
            if connection.execute("SELECT 1 FROM delegations WHERE delegation_id=?", (identifier,)).fetchone() is not None:
                raise V12StoreError("delegation_id already exists", code="delegation_exists")
            dispatch_marker = "dc_" + uuid.uuid4().hex
            dispatch_marker_digest = "sha256:" + hashlib.sha256(dispatch_marker.encode("utf-8")).hexdigest()
            base_native_name = self._next_native_task_name(
                connection,
                task_id=str(task["task_id"]),
                profile_name=payload["profile_name"],
            )
            # Native task names are host-visible correlation, not authority.
            # Bind a bounded non-secret assignment/dispatch suffix so the host
            # can carry the server-selected correlation through its spawn
            # schema without receiving the private capability or full digest.
            native_name = f"{base_native_name}_d_{hashlib.sha256(dispatch_marker.encode('utf-8')).hexdigest()[:12]}"
            sequence = self._timeline(connection, event_type="delegation_created", entity_type="delegation", entity_id=identifier, payload={"delegation_id": identifier, "task_id": task["task_id"], "native_task_name": native_name}, task_id=task["task_id"], delegation_id=identifier)
            connection.execute("INSERT INTO delegations(delegation_id,project_hash,task_id,parent_delegation_id,native_task_name,dispatch_correlation_marker,dispatch_correlation_digest,objective,role,profile_name,scope,instructions,input_report_ids_json,input_decision_ids_json,model,reasoning_effort,created_at,created_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (identifier, self.project_hash, task["task_id"], payload["parent_delegation_id"], native_name, dispatch_marker, dispatch_marker_digest, payload["objective"], payload["role"], payload["profile_name"], payload["scope"], payload["instructions"], _canonical_json(payload["input_report_ids"], label="input_report_ids"), _canonical_json(payload["input_decision_ids"], label="input_decision_ids"), payload["model"], payload["reasoning_effort"], _now(), sequence))
            transferred = [item_id for item_id in assignment_ids["owned"] if item_id in parent_owned]
            if transferred:
                transfer_sequence = self._timeline(
                    connection, event_type="outcome_ownership_transferred", entity_type="delegation", entity_id=identifier,
                    payload={"delegation_id": identifier, "parent_delegation_id": payload["parent_delegation_id"], "item_count": len(transferred)},
                    task_id=task["task_id"], delegation_id=identifier,
                )
                placeholders = ",".join("?" for _ in transferred)
                connection.execute(
                    "UPDATE delegation_outcome_assignments SET superseded_by_delegation_id=?, superseded_sequence=? "
                    f"WHERE delegation_id=? AND assignment_role='owned' AND superseded_by_delegation_id IS NULL AND item_id IN ({placeholders})",
                    (identifier, transfer_sequence, payload["parent_delegation_id"], *transferred),
                )
            revision = self._effective_contract(connection, str(task["task_id"]))["revision"]
            for kind, items in assignment_ids.items():
                role_name = "evidence" if kind == "evidence_producing" else kind
                for item_id in items:
                    try:
                        connection.execute("INSERT INTO delegation_outcome_assignments(delegation_id,item_id,assignment_role,revision) VALUES (?, ?, ?, ?)", (identifier, item_id, role_name, revision))
                    except sqlite3.IntegrityError as exc:
                        raise V12StoreError("outcome item already has an owner", code="outcome_assignment_conflict") from exc
            if payload["profile_name"] == "planner":
                snapshot_items = connection.execute(
                    "SELECT item_id FROM effective_contract_items WHERE task_id=? AND created_revision<=? AND (retired_revision IS NULL OR retired_revision>?) ORDER BY category,ordinal,item_id",
                    (task["task_id"], revision, revision),
                ).fetchall()
                for item in snapshot_items:
                    connection.execute(
                        "INSERT INTO assignment_scope_snapshots(assignment_id,task_id,item_id,assignment_role,contract_revision,created_sequence) VALUES (?, ?, ?, 'planning', ?, ?)",
                        (identifier, task["task_id"], item["item_id"], revision, sequence),
                    )
            else:
                connection.execute(
                    "INSERT INTO assignment_scope_snapshots(assignment_id,task_id,item_id,assignment_role,contract_revision,created_sequence) "
                    "SELECT delegation_id,?,item_id,assignment_role,revision,? FROM delegation_outcome_assignments WHERE delegation_id=?",
                    (task["task_id"], sequence, identifier),
                )
            delegation = self._delegation(connection, identifier, task_id=task["task_id"])
            # Mint the private one-time lease before rendering the brief.  The
            # capability reference remains an internal ledger locator only;
            # it must never cross the public assignment/native-dispatch
            # boundary.  Workers resolve this row by their assignment locator
            # through ``consume_worker_bootstrap_for_assignment``.
            capability = None
            if bootstrap_provenance is not None:
                revision = self._effective_contract(connection, str(task["task_id"]))["revision"]
                required = ("build_digest", "candidate_digest", "source_digest", "catalogue_digest")
                if any(key not in bootstrap_provenance for key in required):
                    raise V12StoreError("worker bootstrap provenance is invalid", code="invalid_argument")
                capability = self._mint_worker_bootstrap_in_transaction(
                    connection, task_id=str(task["task_id"]), assignment_id=identifier,
                    contract_revision=revision, dispatch_digest=dispatch_marker_digest,
                    **{key: bootstrap_provenance[key] for key in required},
                )
            # Creation returns the semantic brief required by a coordinator.
            # The active host maps that brief to its own agent API; the ledger
            # never serializes or authorizes a host-native spawn request.
            worker_brief = self._worker_brief(connection, task, delegation, bootstrap_capability=capability)
            result = {
                "delegation": delegation,
                "dispatch_brief": worker_brief["dispatch_brief"],
                "renderer": worker_brief["renderer"],
            }
            return result
        return self._mutation("create_delegation", payload, idempotency_key, write)

    @staticmethod
    def _worker_capability_ref(value: Any, *, label: str) -> str:
        candidate = _required_text(value, label=label, maximum=64)
        if re.fullmatch(r"w[bc]_[0-9a-f]{32}", candidate) is None:
            raise V12StoreError(f"{label} is invalid", code="invalid_argument", details={"field": label})
        return candidate

    @staticmethod
    def _lease_expired(value: Any) -> bool:
        if not isinstance(value, str) or not value:
            # A minted capability from a pre-lease schema is not safe to
            # treat as expired by inference.  Migration fills this field for
            # known rows; a missing value therefore fails closed as active.
            return False
        try:
            expiry = datetime.fromisoformat(value)
        except ValueError:
            # Corrupt or non-canonical expiry metadata must never open a
            # second owner.  Treat it as active and require explicit server
            # reconciliation rather than failing open.
            return False
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= datetime.now(timezone.utc)

    def _reconcile_dispatch_lease_in_transaction(
        self, connection: sqlite3.Connection, *, task_id: str, assignment_id: str,
    ) -> str | None:
        """Reconcile one parent lease before allowing a replacement.

        The row read is the authoritative, read-only reconciliation point:
        ``consumed`` means the worker has started and replacement is allowed;
        ``minted`` means the dispatch is still owned and replacement is
        denied; an expired ``minted`` row is explicitly marked ``stale`` and
        only then may a replacement proceed.  No host telemetry is consulted.
        """
        rows = connection.execute(
            "SELECT capability_ref,state,lease_expires_at FROM worker_capabilities "
            "WHERE task_id=? AND assignment_id=? ORDER BY created_sequence DESC",
            (task_id, assignment_id),
        ).fetchall()
        if not rows:
            return None
        expired = []
        for row in rows:
            if str(row["state"]) != "minted":
                continue
            if not self._lease_expired(row["lease_expires_at"]):
                raise V12StoreError(
                    "parent assignment still owns an active dispatch lease",
                    code="dispatch_lease_active",
                    details={"assignment_id": assignment_id},
                )
            expired.append(row)
        for row in expired:
            self._timeline(
                connection, event_type="dispatch_lease_expired", entity_type="delegation",
                entity_id=assignment_id, task_id=task_id, delegation_id=assignment_id,
                payload={"assignment_id": assignment_id, "capability_ref": str(row["capability_ref"])},
            )
            connection.execute(
                "UPDATE worker_capabilities SET state='stale',updated_at=? "
                "WHERE capability_ref=? AND state='minted'",
                (_now(), str(row["capability_ref"])),
            )
        return "stale" if expired else str(rows[0]["state"])

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
            raise V12StoreError("worker capability is stale or belongs to another scope", code="capability_stale")
        if str(row["state"]) == "consumed":
            return {"continuation": str(row["continuation_ref"]), "assignment_id": assignment_key, "task_id": task_key, "state": "consumed", "replayed": True}
        if str(row["state"]) != "minted":
            raise V12StoreError("worker capability is not consumable", code="capability_conflict")
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
            return self._consume_worker_bootstrap_row(
                connection, row=row, capability_key=capability_key,
                task_key=task_key, assignment_key=assignment_key,
                revision=revision, digests=digests,
            )
        return self._write(write)

    def resolve_worker_continuation(self, *, continuation: Any) -> dict[str, Any]:
        """Resolve the immutable assignment revision for a consumed continuation.

        The task's effective revision is intentionally not consulted here:
        steering can advance that revision while an owned worker is still
        completing its assignment.  Publication remains bound to the
        continuation's assignment snapshot and one terminal assignment slot.
        """
        continuation_key = self._worker_capability_ref(continuation, label="continuation")
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute(
                "SELECT task_id,assignment_id,contract_revision,state FROM worker_capabilities WHERE continuation_ref=?",
                (continuation_key,),
            ).fetchone()
            if row is None or str(row["state"]) != "consumed":
                raise V12StoreError("worker continuation is invalid", code="capability_stale")
            return {"continuation": continuation_key, "task_id": str(row["task_id"]), "assignment_id": str(row["assignment_id"]), "contract_revision": int(row["contract_revision"]), "state": "consumed"}
        return self._read(read)

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
            if row is None or tuple(row) != (task_key, assignment_key, revision, "consumed"):
                raise V12StoreError("worker continuation is invalid", code="capability_stale")
            return {"continuation": continuation_key, "task_id": task_key, "assignment_id": assignment_key, "contract_revision": revision, "state": "consumed"}
        return self._read(read)

    def _worker_brief(self, connection: sqlite3.Connection, task: Mapping[str, Any], delegation: Mapping[str, Any], *, bootstrap_capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return the coordinator-authored brief without inventing knowledge routes.

        ``instructions`` is the canonical per-delegation semantic contract. It
        may carry selected knowledge paths, extracted constraints, and
        acceptance criteria compiled by the coordinator. The ledger preserves
        that text exactly and does not synthesize broad directory instructions.
        """
        from cortex_runtime.delegation import dispatch_brief_projection
        from cortex_runtime.worker_message import render_worker_message

        decisions = [self._decision(connection, item, task_id=str(task["task_id"])) for item in delegation["input_decision_ids"]]
        input_reports = [self._report(connection, item, task_id=str(task["task_id"])) for item in delegation["input_report_ids"]]
        if any(item["assembly_state"] != "finalized" for item in input_reports):
            raise V12StoreError("input handoff report is not finalized", code="report_state_conflict")
        # A worker brief is an immutable assignment snapshot.  Never rebuild
        # it from the task's latest revision: steering may advance the task
        # while this assignment is still in flight.  The private capability
        # row is authoritative once minted; the assignment rows provide the
        # creation-time fallback while the brief is being constructed before
        # that row exists.
        snapshot_row = connection.execute(
            "SELECT contract_revision FROM worker_capabilities "
            "WHERE assignment_id=? ORDER BY created_sequence LIMIT 1",
            (str(delegation["delegation_id"]),),
        ).fetchone()
        if snapshot_row is not None:
            revision = int(snapshot_row["contract_revision"])
        else:
            revision_row = connection.execute(
                "SELECT revision FROM delegation_outcome_assignments "
                "WHERE delegation_id=? ORDER BY revision LIMIT 1",
                (str(delegation["delegation_id"]),),
            ).fetchone()
            revision = int(revision_row["revision"]) if revision_row is not None else int(self._effective_contract(connection, str(task["task_id"]))["revision"])
        assignment_rows = connection.execute(
            "SELECT a.assignment_role,i.item_id,i.category,i.ordinal,i.text,i.created_revision,d.details_json,d.source_decision_id FROM assignment_scope_snapshots a "
            "JOIN effective_contract_items i ON i.item_id=a.item_id "
            "JOIN effective_contract_item_details d ON d.item_id=i.item_id "
            "WHERE a.assignment_id=? "
            "AND (i.retired_revision IS NULL OR i.retired_revision>?) ORDER BY i.category,i.ordinal,i.item_id,a.assignment_role",
            (delegation["delegation_id"], revision),
        ).fetchall()
        relevant_decisions = []
        for item in decisions:
            if item["decision_type"] != "steer":
                continue
            changed = connection.execute(
                "SELECT 1 FROM effective_contract_revisions WHERE task_id=? AND decision_id=?",
                (task["task_id"], item["decision_id"]),
            ).fetchone() is not None
            relevant_decisions.append({
                "decision_ref": record_ref(str(item["decision_id"])), "type": "steer",
                "effect_summary": "effective_contract_updated" if changed else "no_effective_contract_change",
                "steering_delta": _load_json(str(item["steering_delta_json"]), label="steering_delta") if item.get("steering_delta_json") else None,
            })
        full_contract = self._effective_contract_at_revision(connection, str(task["task_id"]), revision)
        # A review assignment can legitimately be both contributing and
        # evidence-producing for the same contract item.  The trusted worker
        # scope is a set of semantic items, not a list of assignment roles;
        # project one canonical entry per compact item reference before it
        # crosses the renderer boundary.
        canonical_assigned: dict[str, dict[str, Any]] = {}
        for row in assignment_rows:
            item_id = str(row["item_id"])
            candidate = self._contract_item_view(
                _row(row) or {}, assignment_role=str(row["assignment_role"]),
            )
            current = canonical_assigned.get(item_id)
            if current is None or (current["assignment_role"] == "contributing" and candidate["assignment_role"] == "evidence"):
                canonical_assigned[item_id] = candidate
        effective_brief = {
            "revision": revision,
            "assigned_items": sorted(canonical_assigned.values(), key=lambda item: (item["category"], item["ordinal"], item["item_ref"])),
            "decisions": relevant_decisions,
        }
        if delegation["profile_name"] == "planner":
            # A planner maps the whole current contract before any delivery
            # ownership exists.  Its stable tokens are not outcome assignments
            # and must therefore be available independently of that empty set.
            effective_brief["planning_items"] = full_contract["items"]
        report_refs = [
            {key: item[key] for key in ("report_id", "delegation_id", "report_type", "status", "assembly_state", "total_chunks", "content_digest")}
            for item in input_reports
        ]
        scope = {"planning_items": effective_brief["planning_items"]} if delegation["profile_name"] == "planner" else {"assigned_items": effective_brief["assigned_items"]}
        # The capability remains a private server-side lease. It is never
        # rendered into the worker message; the worker consumes by the exact
        # assignment locator and the server resolves the one-time capability
        # inside its atomic transaction.
        rendered = render_worker_message(task=task, delegation=dict(delegation) | {"input_reports": report_refs}, decisions=decisions, bootstrap_capability=None, effective_scope=scope)
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
            effective_contract=effective_brief,
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
                {key: item[key] for key in ("decision_id", "subject_type", "subject_id", "subject_digest", "decision_type", "prompt_en", "response_original", "user_language")}
                for item in decisions
            ],
            "decision_inputs": {
                "state": "none" if not decisions else "declared",
                "decisions": [
                    {key: item[key] for key in ("decision_id", "subject_type", "subject_id", "subject_digest", "decision_type", "prompt_en", "response_original", "user_language")}
                    for item in decisions
                ],
            },
            "model": delegation["model"], "reasoning_effort": delegation["reasoning_effort"],
            "effective_contract": effective_brief,
            "worker_message": rendered["message"], "renderer": rendered["renderer"],
            "dispatch_brief": dispatch_brief,
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

    def submit_report(self, *, task_id: Any = None, delegation_id: Any = None, report_type: Any = None, status: Any = None, content: Any = None, report_id: Any = None, mode: Any = None, section: Any = None, abort_reason_en: Any = None, supersedes_report_id: Any = None, review_policy: Any = None, idempotency_key: Any = None) -> tuple[dict[str, Any], bool]:
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
        if mode_value == "append":
            if content is None:
                raise V12StoreError("content is required", code="invalid_report_operation")
            chunk = _canonical_json_bytes(content, label="content")
            if chunk[2] > REPORT_CHUNK_MAX_BYTES:
                raise V12StoreError("report chunk is too large", code="report_chunk_too_large")
        if mode_value == "append":
            if not isinstance(section, str) or REPORT_SECTION_RE.fullmatch(section) is None:
                raise V12StoreError("section is invalid", code="invalid_report_operation", details={"field": "section"})
        if mode_value == "finalize":
            if status_value is None:
                raise V12StoreError("status is required", code="invalid_report_operation")
        if mode_value == "abort" and _optional_text(abort_reason_en, label="abort_reason_en", maximum=4_096) is None:
            raise V12StoreError("abort_reason_en is required", code="invalid_report_operation")
        if mode_value == "begin":
            if type_value is None:
                raise V12StoreError("report_type is required", code="invalid_report_operation")
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
            "report_id": identifier, "section": section,
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

        def record_coverage(connection: sqlite3.Connection, task: Mapping[str, Any], owner: Mapping[str, Any], report_value: str, semantic: str, canonical_content: object) -> list[dict[str, str]]:
            if semantic != "semantic_valid" or not isinstance(canonical_content, Mapping):
                return []
            if owner.get("profile_name") == "planner" and canonical_content.get("schema") == CANONICAL_REPORT_EVIDENCE_SCHEMAS.get("plan"):
                return []
            claims = canonical_content.get("contract_coverage")
            if not isinstance(claims, list):
                return [{"code": "coverage_missing", "message": "canonical coverage is absent"}]
            validated: list[tuple[str, str, object]] = []
            for claim in claims:
                if not isinstance(claim, Mapping):
                    return [{"code": "coverage_invalid", "message": "coverage claim is invalid"}]
                try:
                    item_id = self._outcome_item_id(connection, str(task["task_id"]), claim.get("item_ref"))
                except V12StoreError:
                    return [{"code": "coverage_unassigned", "message": "coverage item is unavailable"}]
                allowed = connection.execute("SELECT 1 FROM delegation_outcome_assignments WHERE delegation_id=? AND item_id=? AND superseded_by_delegation_id IS NULL", (owner["delegation_id"], item_id)).fetchone()
                if allowed is None:
                    return [{"code": "coverage_unassigned", "message": "coverage item is not assigned to this delegation"}]
                status = claim.get("status")
                if status not in {"complete", "partial", "unverified", "not_applicable"}:
                    return [{"code": "coverage_invalid", "message": "coverage status is invalid"}]
                validated.append((item_id, status, claim.get("verification", [])))
            for item_id, status, verification in validated:
                connection.execute("INSERT INTO report_contract_coverage(report_id,item_id,status,verification_json) VALUES (?, ?, ?, ?)", (report_value, item_id, status, _canonical_json(verification, label="coverage verification")))
            return []

        def completeness_diagnostics(connection: sqlite3.Connection, task: Mapping[str, Any], owner: Mapping[str, Any], report_type: str, canonical_content: object) -> list[dict[str, str]]:
            """Admission-check new specialist evidence before terminal finalization.

            Historical and ordinary general-purpose rows remain readable.  New
            specialist v2 plan/result evidence must be complete while its
            assembly is still amendable; this is deliberately separate from
            the non-gating semantic classifier used for immutable history.
            """
            enforced_profiles = {
                "planner", "backend_dev", "frontend_dev", "fullstack_dev", "mobile_dev",
                "accessibility_fixer", "qa_engineer", "build_verification", "technical_writer",
                "code_reviewer", "security_auditor", "accessibility_auditor", "database_architect",
                "data_engineer", "devops_engineer", "debugger",
            }
            if owner.get("profile_name") not in enforced_profiles or report_type not in {"plan", "result", "synthesis"}:
                return []
            # Existing v1/v2 rows are immutable evidence and intentionally
            # retain their historical finalization semantics.  The current v3
            # envelope is the only shape admitted through this new gate.
            if not isinstance(canonical_content, Mapping) or canonical_content.get("schema") != CANONICAL_REPORT_EVIDENCE_SCHEMAS.get(report_type):
                return []
            diagnostics: list[dict[str, str]] = []
            if canonical_report_semantic_status(report_type, canonical_content) != "semantic_valid":
                diagnostics.append({"code": "canonical_semantic_invalid", "message": "the v3 canonical evidence envelope is structurally incomplete"})
            evidence = canonical_content.get("verification_facts")
            if report_type in {"plan", "result"} and (not isinstance(evidence, list) or not evidence):
                diagnostics.append({"code": "evidence_missing", "message": "at least one observable evidence fact is required"})
            elif report_type in {"plan", "result"}:
                valid_facts = 0
                for fact in evidence:
                    if not isinstance(fact, Mapping):
                        diagnostics.append({"code": "evidence_invalid", "message": "each evidence entry must be an object"})
                        continue
                    state = fact.get("state")
                    if state not in {"executed", "not_run"}:
                        diagnostics.append({"code": "evidence_state_invalid", "message": "evidence state must be executed or not_run"})
                        continue
                    if isinstance(fact.get("summary"), str) and fact["summary"].strip():
                        valid_facts += 1
                        continue
                    # Historical/direct domain evidence remains readable and
                    # admissible; the advertised first-call contract uses the
                    # uniform summary form above.
                    if state == "executed" and all(isinstance(fact.get(field), str) and fact[field].strip() for field in ("command", "cwd", "result")) and isinstance(fact.get("exit_code"), int) and not isinstance(fact.get("exit_code"), bool):
                        valid_facts += 1
                        continue
                    if state == "not_run" and isinstance(fact.get("reason"), str) and fact["reason"].strip():
                        valid_facts += 1
                        continue
                    diagnostics.append({"code": "evidence_invalid", "message": "every evidence fact requires one non-empty summary"})
                    continue
                if not valid_facts:
                    diagnostics.append({"code": "evidence_missing", "message": "no complete observable evidence fact was supplied"})
            impact = canonical_content.get("documentation_impact")
            if report_type in {"result", "synthesis"} and (not isinstance(impact, str) or not impact.strip()):
                diagnostics.append({"code": "documentation_impact_incomplete", "message": "documentation impact requires a non-empty assessment"})
            revision_row = connection.execute("SELECT contract_revision FROM worker_capabilities WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (owner["delegation_id"],)).fetchone()
            assignment_revision = int(revision_row[0]) if revision_row is not None else int(self._effective_contract(connection, str(task["task_id"]))["revision"])
            if owner.get("profile_name") == "planner" and report_type == "plan":
                # Planning is a task-level contract mapping, not an outcome
                # assignment.  Require every current requirement, constraint,
                # acceptance criterion, and derived verification item exactly
                # once even when the planner owns no delivery item.
                assigned = [
                    self._outcome_item_id(connection, str(task["task_id"]), item["item_ref"])
                    for item in self._effective_contract_at_revision(connection, str(task["task_id"]), assignment_revision)["items"]
                ]
            else:
                assigned = [str(row[0]) for row in connection.execute("SELECT DISTINCT item_id FROM delegation_outcome_assignments WHERE delegation_id=? AND superseded_by_delegation_id IS NULL ORDER BY item_id", (owner["delegation_id"],)).fetchall()]
            claims = canonical_content.get("contract_coverage")
            if not isinstance(claims, list):
                diagnostics.append({"code": "contract_coverage_missing", "message": "complete assigned contract coverage is required"})
            else:
                seen: set[str] = set()
                by_ref: dict[str, Mapping[str, Any]] = {}
                for claim in claims:
                    if not isinstance(claim, Mapping) or not isinstance(claim.get("item_ref"), str):
                        diagnostics.append({"code": "contract_coverage_invalid", "message": "each coverage claim requires an exact item reference"})
                        continue
                    try:
                        item_id = self._outcome_item_id(connection, str(task["task_id"]), claim["item_ref"])
                    except V12StoreError:
                        diagnostics.append({"code": "contract_coverage_extra", "message": "coverage includes an unavailable item"})
                        continue
                    if item_id in seen:
                        diagnostics.append({"code": "contract_coverage_duplicate", "message": "each assigned item must have exactly one coverage claim"})
                    seen.add(item_id); by_ref[item_id] = claim
                if set(assigned) != seen:
                    diagnostics.append({"code": "contract_coverage_incomplete", "message": "coverage must contain exactly every current assigned item"})
                for item_id, claim in by_ref.items():
                    verification = claim.get("verification")
                    if claim.get("status") in {"complete", "partial"} and (not isinstance(verification, list) or not verification):
                        diagnostics.append({"code": "coverage_evidence_missing", "message": "complete or partial coverage requires non-empty verification evidence"})
            input_row = connection.execute("SELECT input_report_ids_json FROM delegations WHERE delegation_id=?", (owner["delegation_id"],)).fetchone()
            inputs = [] if input_row is None else _load_json(str(input_row[0]), label="delegation inputs")
            if inputs:
                placeholders = ",".join("?" for _ in inputs)
                observed = {str(row[0]) for row in connection.execute("SELECT DISTINCT report_id FROM report_consumption_receipts WHERE consumer_delegation_id=? AND has_more=0 AND report_id IN (" + placeholders + ")", [owner["delegation_id"], *inputs]).fetchall()}
                if any(item not in observed for item in inputs):
                    diagnostics.append({"code": "predecessor_unread", "message": "every declared predecessor report must have a completed worker read receipt"})
            return diagnostics

        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, anchor)
            owner = self._delegation(connection, delegation, task_id=task["task_id"])
            state = usage(connection, str(task["task_id"]))
            if mode_value == "begin":
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

            if identifier is None:
                raise V12StoreError("report_id is required", code="invalid_report_operation")
            report = self._report(connection, identifier, task_id=task["task_id"])
            if report["delegation_id"] != owner["delegation_id"]:
                raise V12StoreError("reference does not belong to the task", code="cross_project_reference")
            if mode_value == "append":
                if report["assembly_state"] != "assembling":
                    raise V12StoreError("report state conflicts with operation", code="report_state_conflict")
                assert chunk is not None and isinstance(section, str)
                chunk_index = int(report["next_chunk_index"])
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
                # These are immutable receipt evidence only.  They are not
                # advertised handles or legal finalize inputs: the server
                # computes the final manifest transactionally.
                return {"report": self._compact_report(current), "accepted_chunk_index": chunk_index, "next_chunk_index": current["next_chunk_index"], "chunk_digest": chunk[3], "chunk_bytes": chunk[2], "expected_chunk_count": current["total_chunks"], "expected_content_digest": current["content_digest"]}
            if mode_value == "finalize":
                if report["assembly_state"] == "finalized" and report["status"] == status_value:
                    return {"report": self._compact_report(report)}
                if report["assembly_state"] != "assembling":
                    raise V12StoreError("report state conflicts with operation", code="report_state_conflict")
                actual = self._report_digest(connection, identifier)
                if int(report["total_chunks"]) < 1 or report["content_digest"] != actual:
                    raise V12StoreError("report manifest does not match", code="report_manifest_mismatch")
                sequence = self._timeline(connection, event_type="report_submitted", entity_type="report", entity_id=identifier, payload={"report_id": identifier, "delegation_id": owner["delegation_id"], "report_type": report["report_type"], "status": status_value, "total_chunks": report["total_chunks"], "total_bytes": report["total_bytes"], "content_digest": actual}, task_id=task["task_id"], delegation_id=owner["delegation_id"], report_id=identifier)
                chunks = self._report_chunks(connection, identifier)
                canonical_content: object = chunks[0]["content"] if len(chunks) == 1 else None
                if len(chunks) > 1 and all(isinstance(item["content"], Mapping) for item in chunks):
                    merged: dict[str, Any] = {}
                    first_content = chunks[0]["content"]
                    allow_coverage_amendment = (
                        isinstance(first_content, Mapping)
                        and first_content.get("schema") == CANONICAL_REPORT_EVIDENCE_SCHEMAS.get(str(report["report_type"]))
                    )
                    for item in chunks:
                        for key, item_value in item["content"].items():
                            if key in merged and not (key == "contract_coverage" and allow_coverage_amendment):
                                merged = {}
                                break
                            merged[key] = item_value
                        if not merged:
                            break
                    canonical_content = merged or None
                semantic = canonical_report_semantic_status(str(report["report_type"]), canonical_content)
                diagnostics = completeness_diagnostics(connection, task, owner, str(report["report_type"]), canonical_content)
                if diagnostics:
                    raise V12StoreError("report evidence is incomplete", code="report_incomplete", details={"field": "content", "expected": "complete_evidence_envelope", "reason": diagnostics[0]["code"]})
                diagnostics = record_coverage(connection, task, owner, identifier, semantic, canonical_content)
                connection.execute("UPDATE reports SET assembly_state='finalized',status=?,semantic_status=?,coverage_diagnostics_json=?,finalized_at=?,finalized_sequence=? WHERE report_id=?", (status_value, semantic, _canonical_json(diagnostics, label="coverage diagnostics"), _now(), sequence, identifier))
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

    def publish_domain_report(self, *, delegation_id: Any, continuation_ref: Any,
                              contract_revision: Any, publication_kind: Any,
                              content: Any, status: Any) -> dict[str, Any]:
        """Atomically publish one terminal semantic report for an assignment.

        ``report_operations`` is deliberately separate from caller-facing
        idempotency.  The assignment/kind unique slot makes ambiguous retries
        safe: an equal canonical payload replays the existing report, while a
        different payload is an explicit conflict requiring a new assignment.
        """
        anchor, delegation = self._task_for_delegation(delegation_id, None)
        report_kind = _required_text(publication_kind, label="publication_kind", maximum=16).lower()
        if report_kind not in REPORT_TYPES:
            raise V12StoreError("report type is invalid", code="invalid_report")
        report_status = _required_text(status, label="status", maximum=16).lower()
        if report_status not in REPORT_STATUSES:
            raise V12StoreError("report status is invalid", code="invalid_report")
        continuation_key = self._worker_capability_ref(continuation_ref, label="continuation")
        try:
            revision = int(contract_revision)
        except (TypeError, ValueError) as exc:
            raise V12StoreError("contract_revision is invalid", code="invalid_argument", details={"field": "contract_revision"}) from exc
        if revision < 1:
            raise V12StoreError("contract_revision is invalid", code="invalid_argument", details={"field": "contract_revision"})
        # Normalize the intentionally minimal v3 public envelope before its
        # digest is computed. Defaults and stage order are server-owned, so
        # equivalent omitted/explicit bookkeeping has one canonical payload.
        if isinstance(content, Mapping) and content.get("schema") in CANONICAL_REPORT_EVIDENCE_SCHEMAS.values():
            normalized = dict(content)
            for key in ("risks", "deviations", "unresolved"):
                normalized.setdefault(key, [])
            if report_kind == "result":
                normalized.setdefault("changes", [])
            if report_kind == "synthesis":
                normalized.setdefault("findings", [])
                normalized.setdefault("recommendations", [])
            if report_kind == "plan" and isinstance(normalized.get("stages"), list):
                stages = []
                for index, stage in enumerate(normalized["stages"], 1):
                    if isinstance(stage, Mapping):
                        item = dict(stage)
                        item["order"] = index
                        item["dependencies"] = [] if index == 1 else [index - 1]
                        stages.append(item)
                    else:
                        stages.append(stage)
                normalized["stages"] = stages
            content = _coalesce_compatible_contract_coverage(normalized)
        canonical = _canonical_json_bytes(content, label="content")
        if canonical[2] > REPORT_MAX_BYTES:
            raise V12StoreError("report is too large", code="report_too_large")
        operation_payload = {"task_id": anchor, "delegation_id": delegation, "kind": report_kind, "status": report_status, "content": canonical[0]}
        payload_digest = hashlib.sha256(_canonical_json(operation_payload, label="report operation").encode("utf-8")).hexdigest()

        def write(connection: sqlite3.Connection) -> tuple[dict[str, Any], bool]:
            task = self._task(connection, anchor)
            owner = self._delegation(connection, delegation, task_id=task["task_id"])
            assignment_roles = {
                str(row[0]) for row in connection.execute(
                    "SELECT DISTINCT assignment_role FROM delegation_outcome_assignments WHERE delegation_id=?",
                    (owner["delegation_id"],),
                ).fetchall()
            }
            owner_policy = "planning" if owner.get("profile_name") == "planner" else "owner" if "owned" in assignment_roles else "review"
            predecessor = self._inferred_assignment_predecessor(
                connection,
                task_id=str(task["task_id"]),
                profile_name=str(owner["profile_name"]),
                input_report_ids=list(owner["input_report_ids"]),
                input_decision_ids=list(owner["input_decision_ids"]),
                explicit_parent_delegation_id=owner.get("parent_delegation_id"),
                assignment_policy=owner_policy,
            )
            supersedes_report_id = None if predecessor is None else str(predecessor["report_id"])
            # Continuation admission is part of the same write transaction as
            # the assignment/kind uniqueness check and report insert.  A
            # read-then-publish split permits a stale or cross-assignment
            # continuation to race an effective-contract change.
            continuation = connection.execute(
                "SELECT task_id,assignment_id,contract_revision,state FROM worker_capabilities WHERE continuation_ref=?",
                (continuation_key,),
            ).fetchone()
            if continuation is None or tuple(continuation) != (str(task["task_id"]), str(owner["delegation_id"]), revision, "consumed"):
                raise V12StoreError("worker continuation is invalid", code="capability_stale")
            existing = connection.execute("SELECT payload_digest,report_id FROM report_operations WHERE delegation_id=? AND kind=?", (owner["delegation_id"], report_kind)).fetchone()
            if existing is not None:
                if str(existing["payload_digest"]) != payload_digest:
                    raise V12StoreError("assignment already has a different terminal report", code="report_operation_conflict")
                report = self._report(connection, str(existing["report_id"]), task_id=task["task_id"])
                replayed: dict[str, Any] = {"report": self._compact_report(report), "operation_id": str(existing["report_id"]), "replayed": True}
                if report_kind == "plan":
                    relation = self._ready_plan_review_relation(
                        connection, task_id=str(task["task_id"]), report_id=str(report["report_id"]),
                    )
                    replayed["approval_view"] = {
                        "status": "ready", "report_id": str(report["report_id"]),
                        "delegation_id": str(owner["delegation_id"]),
                        "report_content_digest": relation["plan_content_digest"],
                        "approval_handle": relation["approval_handle"],
                        "content_digest": relation["view_content_digest"],
                        "source_sequence": relation["view_source_sequence"],
                    }
                return replayed
            # The domain API is deliberately stricter than the historical
            # assembled-report transport: only the exact current envelope for
            # this publication kind may consume a terminal logical slot.
            expected_schema = CANONICAL_REPORT_EVIDENCE_SCHEMAS.get(report_kind)
            if expected_schema is None:
                expected_schema = CANONICAL_REPORT_V2_SCHEMAS.get(report_kind)
            if not isinstance(content, Mapping) or content.get("schema") != expected_schema:
                raise V12StoreError("report evidence is incomplete", code="report_incomplete", details={"reason": "canonical_semantic_invalid"})
            if canonical_report_semantic_status(report_kind, content) != "semantic_valid":
                    raise V12StoreError("report evidence is incomplete", code="report_incomplete", details={"reason": "canonical_semantic_invalid"})
            if report_kind in {"plan", "result"}:
                diagnostics: list[str] = []
                evidence = content.get("verification_facts")
                if not isinstance(evidence, list) or not evidence:
                    diagnostics.append("evidence_missing")
                else:
                    for fact in evidence:
                        if not isinstance(fact, Mapping):
                            diagnostics.append("evidence_invalid")
                            break
                        if fact.get("state") not in {"executed", "not_run", "failed"}:
                            diagnostics.append("evidence_state_invalid")
                            break
                        if isinstance(fact.get("summary"), str) and fact["summary"].strip():
                            continue
                        if fact.get("state") == "executed" and all(isinstance(fact.get(field), str) and fact[field].strip() for field in ("command", "cwd", "result")) and isinstance(fact.get("exit_code"), int) and not isinstance(fact.get("exit_code"), bool):
                            continue
                        if fact.get("state") == "not_run" and isinstance(fact.get("reason"), str) and fact["reason"].strip():
                            continue
                        diagnostics.append("evidence_invalid")
                        break
                if report_kind == "result":
                    impact = content.get("documentation_impact")
                    if not isinstance(impact, str) or not impact.strip():
                        diagnostics.append("documentation_impact_incomplete")
                if diagnostics:
                    raise V12StoreError("report evidence is incomplete", code="report_incomplete", details={"reason": diagnostics[0]})
            continuation_row = connection.execute("SELECT contract_revision FROM worker_capabilities WHERE continuation_ref=?", (continuation_key,)).fetchone()
            assignment_revision = int(continuation_row[0]) if continuation_row is not None else int(self._effective_contract(connection, str(task["task_id"]))["revision"])
            # The immutable assignment snapshot remains the sole routing
            # authority. The publication must nevertheless reconcile every
            # emitted scope item exactly once, otherwise a worker summary can
            # silently drop findings while still consuming its terminal slot.
            scope_rows = connection.execute(
                "SELECT DISTINCT item_id,assignment_role FROM assignment_scope_snapshots "
                "WHERE assignment_id=? AND contract_revision=? ORDER BY item_id,assignment_role",
                (owner["delegation_id"], assignment_revision),
            ).fetchall()
            expected_items = {str(row["item_id"]) for row in scope_rows}
            expected_by_ref = {self._outcome_ref(item_id): item_id for item_id in expected_items}
            claims = content.get("contract_coverage")
            if not isinstance(claims, list):
                raise V12StoreError("report evidence is incomplete", code="report_incomplete", details={"reason": "contract_coverage_missing"})
            dispositions: dict[str, tuple[str, list[Any]]] = {}
            allowed_statuses = {"planned"} if report_kind == "plan" else {"complete", "partial", "unverified", "blocked", "not_applicable"}
            for claim in claims:
                if not isinstance(claim, Mapping) or not isinstance(claim.get("item_ref"), str):
                    raise V12StoreError("report evidence is incomplete", code="report_incomplete", details={"reason": "contract_coverage_invalid"})
                # Resolve against the immutable assignment snapshot, not the
                # task's latest contract. A steering decision may retire an
                # in-flight item after the assignment was consumed; that
                # worker must still publish an explicit disposition for the
                # exact scope it received.
                item_id = expected_by_ref.get(str(claim["item_ref"]))
                if item_id is None:
                    raise V12StoreError("report evidence is incomplete", code="report_incomplete", details={"reason": "contract_coverage_invalid"})
                if item_id in dispositions:
                    raise V12StoreError("report evidence is incomplete", code="report_incomplete", details={"reason": "contract_coverage_duplicate"})
                claim_status = claim.get("status")
                verification = claim.get("verification")
                if claim_status not in allowed_statuses or not isinstance(verification, list) or not verification or any(not isinstance(value, str) or not value.strip() for value in verification):
                    raise V12StoreError("report evidence is incomplete", code="report_incomplete", details={"reason": "contract_coverage_invalid"})
                dispositions[item_id] = (str(claim_status), list(verification))
            if set(dispositions) != expected_items:
                raise V12StoreError("report evidence is incomplete", code="report_incomplete", details={"reason": "contract_coverage_incomplete"})
            report_id = new_sharded_id("report", self.project_hash)
            manifest = _sha256_prefixed(_report_manifest([{"chunk_index": 0, "section": "body", "content_digest": canonical[3], "content_bytes": canonical[2]}]), label="report manifest")
            sequence = self._timeline(connection, event_type="report_submitted", entity_type="report", entity_id=report_id, payload={"report_id": report_id, "delegation_id": owner["delegation_id"], "report_type": report_kind, "status": report_status, "total_chunks": 1, "total_bytes": canonical[2], "content_digest": manifest, "supersedes_report_id": supersedes_report_id}, task_id=task["task_id"], delegation_id=owner["delegation_id"], report_id=report_id)
            timestamp = _now()
            arguments = (report_id, self.project_hash, task["task_id"], owner["delegation_id"], report_kind, report_status, "semantic_valid", manifest, timestamp, sequence)
            if "content_json" in self._column_names(connection, "reports"):
                connection.execute("INSERT INTO reports(report_id,project_hash,task_id,delegation_id,report_type,status,semantic_status,content_json,assembly_state,next_chunk_index,total_chunks,total_bytes,content_digest,supersedes_report_id,review_policy,created_at,created_sequence,finalized_at,finalized_sequence,aborted_at,aborted_sequence,abort_reason_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'finalized', 1, 1, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, NULL)", (report_id, self.project_hash, task["task_id"], owner["delegation_id"], report_kind, report_status, "semantic_valid", "null", canonical[2], manifest, supersedes_report_id, timestamp, sequence, timestamp, sequence))
            else:
                connection.execute("INSERT INTO reports(report_id,project_hash,task_id,delegation_id,report_type,status,semantic_status,assembly_state,next_chunk_index,total_chunks,total_bytes,content_digest,supersedes_report_id,review_policy,created_at,created_sequence,finalized_at,finalized_sequence,aborted_at,aborted_sequence,abort_reason_en) VALUES (?, ?, ?, ?, ?, ?, ?, 'finalized', 1, 1, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, NULL)", (report_id, self.project_hash, task["task_id"], owner["delegation_id"], report_kind, report_status, "semantic_valid", canonical[2], manifest, supersedes_report_id, timestamp, sequence, timestamp, sequence))
            connection.execute("INSERT INTO report_chunks(report_id,chunk_index,section,content_json,content_digest,content_bytes,created_at) VALUES (?, 0, 'body', ?, ?, ?, ?)", (report_id, canonical[1], canonical[3], canonical[2], timestamp))
            # Persist the exact validated dispositions. Server scope controls
            # which rows may exist; worker evidence controls their semantic
            # status and verification narrative.
            for item_id, (claim_status, verification) in dispositions.items():
                connection.execute(
                    "INSERT INTO report_contract_coverage(report_id,item_id,status,verification_json) VALUES (?, ?, ?, ?)",
                    (report_id, item_id, claim_status, _canonical_json(verification, label="coverage verification")),
                )
            connection.execute("INSERT INTO report_operations(operation_id,task_id,delegation_id,kind,payload_digest,report_id,created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex, task["task_id"], owner["delegation_id"], report_kind, payload_digest, report_id, timestamp))
            # Codex exposes no supported plugin hook capable of observing the
            # exact native-worker follow-up result.  It would be dishonest to
            # claim host delivery from a renderer projection.  The one safe
            # reconciliation signal is an accepted publication authored by
            # the same saved assignment after the answer was recorded.  It is
            # persisted inside this publication transaction, never schedules
            # a worker, and cannot turn an unavailable/no-publication hold
            # into a false success.
            publication_count = int(connection.execute(
                "SELECT COUNT(*) FROM report_operations WHERE delegation_id=?",
                (owner["delegation_id"],),
            ).fetchone()[0])
            if publication_count == 1:
                self._reconcile_assignment_clarification_publication(
                    connection, task_id=str(task["task_id"]),
                    assignment_id=str(owner["delegation_id"]), report_id=report_id,
                )
            compact = self._compact_report(self._report(connection, report_id, task_id=task["task_id"]))
            published: dict[str, Any] = {"report": compact, "operation_id": report_id, "replayed": False}
            if report_kind == "plan":
                # A plan is not canonical-public until its exact approval view
                # and opaque relation exist.  Render the immutable revision
                # before this transaction commits, then record its digest and
                # mint the relation on this same connection.  The write is
                # atomic at the file boundary; a failed DB transaction can at
                # worst leave an unreferenced immutable file, never a ledger
                # plan advertised without its relation.
                try:
                    from cortex_runtime.report_presenters import render_report
                    from cortex_runtime.v12_projections import _migrate_legacy_task_directory, _safe_write
                    view_body = render_report(
                        report_type="plan", content=json.loads(canonical[1]), report=compact,
                    ).encode("utf-8")
                    task_directory = _migrate_legacy_task_directory(
                        self, str(task["task_id"]), str(task["task_ref"]),
                    )
                    relative = f"plans/revisions/{report_id}.md"
                    view_digest = _safe_write(
                        task_directory / relative, view_body, expected_digest=None, root=self.root,
                    )
                except (OSError, ValueError, UnicodeError) as exc:
                    raise V12StoreError(
                        "plan approval view cannot be materialized", code="storage_unavailable",
                    ) from exc
                connection.execute(
                    "INSERT INTO projection_files(task_id,relative_path,source_sequence,renderer_version,content_digest,status,updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'ready', ?) "
                    "ON CONFLICT(task_id,relative_path) DO UPDATE SET "
                    "source_sequence=excluded.source_sequence,renderer_version=excluded.renderer_version,"
                    "content_digest=excluded.content_digest,status='ready',updated_at=excluded.updated_at",
                    (task["task_id"], relative, sequence, PROJECTION_RENDERER_VERSION, view_digest, timestamp),
                )
                relation = self._ready_plan_review_relation(
                    connection, task_id=str(task["task_id"]), report_id=report_id,
                    report_content_digest=str(compact["content_digest"]), view_relative_path=relative,
                    view_content_digest=view_digest, view_source_sequence=sequence,
                )
                published["approval_view"] = {
                    "status": "ready", "report_id": report_id,
                    "delegation_id": str(owner["delegation_id"]),
                    "report_content_digest": relation["plan_content_digest"],
                    "approval_handle": relation["approval_handle"],
                    "content_digest": relation["view_content_digest"],
                    "source_sequence": relation["view_source_sequence"],
                }
            return published
        return self._write(write)

    def _reconcile_assignment_clarification_publication(
        self, connection: sqlite3.Connection, *, task_id: str, assignment_id: str,
        report_id: str,
    ) -> None:
        """Close only pending holds evidenced by this assignment's first report.

        This is a private reconciliation path, not a host adapter.  The exact
        assignment and answered hold were already server-bound in one prior
        decision transaction; the first accepted worker-owned publication is
        durable evidence that the worker continued.  A pending hold with no
        such publication remains pending for coordinator-owned recovery.
        """
        pending = connection.execute(
            "SELECT clarification_binding,response_decision_id FROM clarification_holds "
            "WHERE project_hash=? AND task_id=? AND assignment_id=? "
            "AND state='pending_delivery' AND response_decision_id IS NOT NULL "
            "ORDER BY opened_sequence ASC",
            (self.project_hash, task_id, assignment_id),
        ).fetchall()
        for row in pending:
            binding_ref = str(row["clarification_binding"])
            decision_id = str(row["response_decision_id"])
            sequence = self._timeline(
                connection, event_type="clarification_delivery_reconciled_publication",
                entity_type="clarification_hold", entity_id=binding_ref,
                payload={"state": "delivered", "evidence": "first_assignment_publication"},
                task_id=task_id, delegation_id=assignment_id,
                report_id=report_id, decision_id=decision_id,
            )
            cursor = connection.execute(
                "UPDATE clarification_holds SET state='delivered',delivery_sequence=?,updated_at=? "
                "WHERE clarification_binding=? AND state='pending_delivery'",
                (sequence, _now(), binding_ref),
            )
            if cursor.rowcount != 1:
                raise V12StoreError("clarification delivery reconciliation conflicted", code="command_conflict")

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
        pending = connection.execute(
            "SELECT decision_type FROM clarification_bindings "
            "WHERE project_hash=? AND task_id=? AND consumed_decision_id IS NULL "
            "ORDER BY issue_sequence ASC LIMIT 1",
            (self.project_hash, task_id),
        ).fetchone()
        if pending is not None:
            raise V12StoreError(
                "a user decision must be recorded before the task can advance",
                code="decision_pending",
                details={"decision_type": str(pending["decision_type"])},
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

    def issue_clarification_binding(self, *, task_id: Any, prompt: Any, prompt_language: Any, subject_type: Any = "task", subject_id: Any = None, assignment_id: Any = None, decision_type: Any = "clarification", idempotency_key: Any = None, _connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        """Issue or replay one exact, durable binding for a pending clarification."""
        anchor = self._task_identifier(task_id)
        text = _opaque_text(prompt, label="prompt")
        language = _language(prompt_language)
        kind = _required_text(subject_type, label="subject_type", maximum=16).lower()
        dtype = _required_text(decision_type, label="decision_type", maximum=32).lower()
        subject = anchor if subject_id is None and kind == "task" else self._record_identifier(subject_id, label="subject_id")
        assignment = None if assignment_id is None else self._record_identifier(assignment_id, label="assignment_id")
        request_digest = _sha256_prefixed({"task_id": anchor, "subject_type": kind, "subject_id": subject, "decision_type": dtype, "prompt": text, "prompt_language": language}, label="clarification request")
        prompt_digest = _sha256_prefixed(text, label="clarification prompt")

        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, anchor)
            revision = int(self._effective_contract(connection, anchor)["revision"])
            existing = connection.execute("SELECT * FROM clarification_bindings WHERE task_id=? AND subject_type=? AND subject_id=? AND decision_type=? AND prompt_digest=? AND effective_contract_revision=?", (anchor, kind, subject, dtype, prompt_digest, revision)).fetchone()
            if existing is not None:
                return {"binding": self._decision_binding_projection(existing, task_id=anchor), "replayed": True}
            self._require_no_pending_user_decision(connection, task_id=anchor)
            relation: dict[str, Any] | None = None
            if dtype == "plan_review":
                if kind != "plan":
                    raise V12StoreError("plan review must target a plan", code="invalid_decision_subject")
                relation = self._ready_plan_review_relation(connection, task_id=anchor, report_id=subject)
            sequence = self._timeline(connection, event_type="clarification_binding_issued", entity_type="clarification_binding", entity_id="cb_" + uuid.uuid4().hex, payload={"task_id": anchor, "decision_type": dtype, "prompt_digest": prompt_digest}, task_id=anchor)
            token = "cb_" + uuid.uuid4().hex
            connection.execute("INSERT INTO clarification_bindings(clarification_binding,project_hash,task_id,subject_type,subject_id,assignment_id,decision_type,prompt_digest,prompt,prompt_language,effective_contract_revision,issue_sequence,request_digest,response_digest,consumed_decision_id,created_at,plan_content_digest,plan_approval_handle,plan_view_content_digest,plan_view_source_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)", (token, self.project_hash, anchor, kind, subject, assignment, dtype, prompt_digest, text, language, revision, sequence, request_digest, _now(), None if relation is None else relation["plan_content_digest"], None if relation is None else relation["approval_handle"], None if relation is None else relation["view_content_digest"], None if relation is None else relation["view_source_sequence"]))
            inserted = connection.execute("SELECT * FROM clarification_bindings WHERE clarification_binding=?", (token,)).fetchone()
            if inserted is None:
                raise V12StoreError("plan review binding was not stored", code="ledger_corrupt")
            return {"binding": self._decision_binding_projection(inserted, task_id=anchor), "replayed": False}
        # DomainKernel supplies the ambient transaction for semantic command
        # receipts; direct internal callers use the store transaction.
        return write(_connection) if _connection is not None else self._write(write)

    @staticmethod
    def _clarification_hold_projection(connection: sqlite3.Connection, row: Mapping[str, Any]) -> dict[str, Any]:
        """Return compact public evidence for one hold, never canonical IDs."""
        assignment_id = row["assignment_id"]
        result: dict[str, Any] = {
            "state": str(row["state"]),
            "opened_sequence": int(row["opened_sequence"]),
            "answered_sequence": None if row["answered_sequence"] is None else int(row["answered_sequence"]),
            "delivery_sequence": None if row["delivery_sequence"] is None else int(row["delivery_sequence"]),
        }
        if assignment_id is not None:
            result["assignment_ref"] = record_ref(str(assignment_id))
        if row["response_decision_id"] is not None:
            result["decision_ref"] = record_ref(str(row["response_decision_id"]))
        if row["unavailable_reason"] is not None:
            result["unavailable_reason"] = str(row["unavailable_reason"])
        return result

    def open_clarification_hold(
        self, *, task_id: str, binding_ref: str, assignment_id: str | None,
        connection: sqlite3.Connection,
    ) -> dict[str, Any]:
        """Create/replay the hold in the decision command transaction.

        The server obtains the native dispatch identity from the already saved
        assignment, never from MCP input.  Its digest and random continuation
        capability bind a later host action to that immutable assignment while
        leaving canonical IDs outside the public boundary.
        """
        binding = connection.execute(
            "SELECT task_id,assignment_id,issue_sequence,decision_type FROM clarification_bindings "
            "WHERE clarification_binding=? AND project_hash=?",
            (binding_ref, self.project_hash),
        ).fetchone()
        if binding is None or str(binding["task_id"]) != task_id or str(binding["decision_type"]) != "clarification":
            raise V12StoreError("clarification binding was not found", code="clarification_binding_not_found")
        bound_assignment = None if binding["assignment_id"] is None else str(binding["assignment_id"])
        if bound_assignment != assignment_id:
            raise V12StoreError("clarification assignment does not match the binding", code="clarification_binding_mismatch")
        existing = connection.execute(
            "SELECT * FROM clarification_holds WHERE clarification_binding=? AND project_hash=?",
            (binding_ref, self.project_hash),
        ).fetchone()
        if existing is not None:
            if (None if existing["assignment_id"] is None else str(existing["assignment_id"])) != assignment_id:
                raise V12StoreError("clarification hold is inconsistent", code="ledger_corrupt")
            return self._clarification_hold_projection(connection, existing)
        native_digest: str | None = None
        capability: str | None = None
        if assignment_id is not None:
            assignment = self._delegation(connection, assignment_id, task_id=task_id)
            native_name = str(assignment["native_task_name"])
            native_digest = self._clarification_native_dispatch_digest(assignment_id, native_name)
            capability = "hc_" + uuid.uuid4().hex
        now = _now()
        connection.execute(
            "INSERT INTO clarification_holds(clarification_binding,project_hash,task_id,assignment_id,"
            "native_dispatch_digest,continuation_capability,state,response_decision_id,delivery_claim_digest,"
            "opened_sequence,answered_sequence,delivery_sequence,unavailable_reason,created_at,updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending_question', NULL, NULL, ?, NULL, NULL, NULL, ?, ?)",
            (binding_ref, self.project_hash, task_id, assignment_id, native_digest, capability,
             int(binding["issue_sequence"]), now, now),
        )
        created = connection.execute(
            "SELECT * FROM clarification_holds WHERE clarification_binding=?", (binding_ref,)
        ).fetchone()
        if created is None:
            raise V12StoreError("clarification hold was not stored", code="ledger_corrupt")
        return self._clarification_hold_projection(connection, created)

    @staticmethod
    def _clarification_native_dispatch_digest(assignment_id: str, native_task_name: str) -> str:
        """Bind a hold to its immutable saved assignment/native identity."""
        return _sha256_prefixed(
            {"assignment_id": assignment_id, "native_task_name": native_task_name},
            label="clarification native dispatch",
        )

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
            return self._clarification_hold_projection(connection, row)
        if str(row["state"]) != "pending_question":
            raise V12StoreError("clarification hold cannot accept a response", code="clarification_binding_stale")
        next_state = "coordinator_completed" if row["assignment_id"] is None else "pending_delivery"
        sequence = self._timeline(
            connection, event_type="clarification_hold_answered", entity_type="clarification_hold",
            entity_id=binding_ref, payload={"state": next_state}, task_id=task_id,
            delegation_id=row["assignment_id"], decision_id=decision_id,
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
        return self._clarification_hold_projection(connection, answered)

    def clarification_host_delivery_projection(self, *, task_id: str, binding_ref: str) -> dict[str, Any] | None:
        """Read the private exact-worker delivery relation for the API adapter.

        This is not an MCP handler.  The public adapter uses it only to render
        one trusted continuation message after the decision transaction has
        committed.  It never calls a host operation or changes hold state.
        """
        def read(connection: sqlite3.Connection) -> dict[str, Any] | None:
            row = connection.execute(
                "SELECT h.*,d.native_task_name,d.dispatch_correlation_marker,d.dispatch_correlation_digest,u.response_original,u.user_language,u.decision_id "
                "FROM clarification_holds h JOIN delegations d ON d.delegation_id=h.assignment_id "
                "JOIN user_decisions u ON u.decision_id=h.response_decision_id "
                "WHERE h.clarification_binding=? AND h.project_hash=? AND h.task_id=?",
                (binding_ref, self.project_hash, task_id),
            ).fetchone()
            if row is None:
                return None
            if str(row["state"]) not in {"pending_delivery", "delivery_claimed", "delivered", "unavailable"}:
                raise V12StoreError("clarification hold has invalid delivery state", code="ledger_corrupt")
            expected_dispatch_digest = self._clarification_native_dispatch_digest(
                str(row["assignment_id"]), str(row["native_task_name"]),
            )
            if str(row["native_dispatch_digest"] or "") != expected_dispatch_digest:
                raise V12StoreError("clarification dispatch proof is inconsistent", code="ledger_corrupt")
            marker = row["dispatch_correlation_marker"]
            digest = row["dispatch_correlation_digest"]
            if not isinstance(marker, str) or re.fullmatch(r"dc_[0-9a-f]{32}", marker) is None or digest != "sha256:" + hashlib.sha256(marker.encode("utf-8")).hexdigest():
                raise V12StoreError("clarification dispatch correlation is unavailable", code="ledger_corrupt")
            return {
                "state": str(row["state"]), "binding_ref": binding_ref,
                "assignment_id": str(row["assignment_id"]),
                "native_task_name": str(row["native_task_name"]),
                "native_dispatch_digest": str(row["native_dispatch_digest"]),
                "dispatch_correlation_marker": marker,
                "dispatch_correlation_fingerprint": str(digest),
                "continuation_capability": str(row["continuation_capability"]),
                "decision_id": str(row["decision_id"]),
                "response_original": str(row["response_original"]),
                "user_language": str(row["user_language"]),
                "unavailable_reason": None if row["unavailable_reason"] is None else str(row["unavailable_reason"]),
            }
        return self._read(read)

    def clarification_host_delivery_context(self, *, task_id: str, binding_ref: str) -> dict[str, Any] | None:
        """Return the renderer-only context for a pending worker continuation."""
        def read(connection: sqlite3.Connection) -> dict[str, Any] | None:
            row = connection.execute(
                "SELECT h.assignment_id,h.response_decision_id,h.state FROM clarification_holds h "
                "WHERE h.clarification_binding=? AND h.project_hash=? AND h.task_id=?",
                (binding_ref, self.project_hash, task_id),
            ).fetchone()
            if row is None or row["assignment_id"] is None or row["response_decision_id"] is None:
                return None
            return {
                "task": self._task(connection, task_id),
                "delegation": self._delegation(connection, str(row["assignment_id"]), task_id=task_id),
                "decision": self._decision(connection, str(row["response_decision_id"]), task_id=task_id),
                "state": str(row["state"]),
            }
        return self._read(read)

    def host_clarification_delivery(
        self, *, binding_ref: str, continuation_capability: str, host_identity: str,
    ) -> dict[str, Any]:
        """Atomically claim a pending exact-worker delivery for a host adapter.

        This private method is intentionally not registered as an MCP tool.
        It returns the exact persisted native name and response only after the
        opaque server-issued capability is supplied unchanged.
        """
        if not isinstance(continuation_capability, str) or not continuation_capability:
            raise V12StoreError("continuation capability is invalid", code="invalid_argument")
        identity = _required_text(host_identity, label="host_identity", maximum=512)
        claim_digest = _sha256_prefixed(
            {"continuation_capability": continuation_capability, "host_identity": identity},
            label="clarification host claim",
        )
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute(
                "SELECT h.*,d.native_task_name,u.response_original,u.user_language "
                "FROM clarification_holds h JOIN delegations d ON d.delegation_id=h.assignment_id "
                "JOIN user_decisions u ON u.decision_id=h.response_decision_id "
                "WHERE h.clarification_binding=? AND h.project_hash=?",
                (binding_ref, self.project_hash),
            ).fetchone()
            if row is None or row["assignment_id"] is None:
                raise V12StoreError("worker clarification delivery was not found", code="clarification_binding_not_found")
            if str(row["continuation_capability"] or "") != continuation_capability:
                raise V12StoreError("continuation capability does not match the hold", code="clarification_binding_mismatch")
            if str(row["native_dispatch_digest"] or "") != self._clarification_native_dispatch_digest(
                str(row["assignment_id"]), str(row["native_task_name"]),
            ):
                raise V12StoreError("clarification dispatch proof is inconsistent", code="ledger_corrupt")
            state = str(row["state"])
            if state == "pending_delivery":
                sequence = self._timeline(
                    connection, event_type="clarification_delivery_claimed", entity_type="clarification_hold",
                    entity_id=binding_ref, payload={"state": "delivery_claimed"}, task_id=str(row["task_id"]),
                    delegation_id=str(row["assignment_id"]), decision_id=str(row["response_decision_id"]),
                )
                connection.execute(
                    "UPDATE clarification_holds SET state='delivery_claimed',delivery_claim_digest=?,delivery_sequence=?,updated_at=? WHERE clarification_binding=?",
                    (claim_digest, sequence, _now(), binding_ref),
                )
                replayed = False
            elif state == "delivery_claimed" and str(row["delivery_claim_digest"] or "") == claim_digest:
                replayed = True
            elif state == "delivered" and str(row["delivery_claim_digest"] or "") == claim_digest:
                replayed = True
            else:
                raise V12StoreError("clarification delivery is not available for this host", code="command_conflict")
            return {
                "binding_ref": binding_ref, "assignment_id": str(row["assignment_id"]),
                "native_task_name": str(row["native_task_name"]),
                "decision_id": str(row["response_decision_id"]),
                "response_original": str(row["response_original"]), "user_language": str(row["user_language"]),
                "replayed": replayed,
            }
        return self._write(write)

    def complete_host_clarification_delivery(
        self, *, binding_ref: str, continuation_capability: str, host_identity: str,
        outcome: str, unavailable_reason: str | None = None,
    ) -> dict[str, Any]:
        """Persist a host delivery outcome without choosing replacement work."""
        if outcome not in {"delivered", "unavailable"}:
            raise V12StoreError("clarification delivery outcome is invalid", code="invalid_argument")
        identity = _required_text(host_identity, label="host_identity", maximum=512)
        claim_digest = _sha256_prefixed(
            {"continuation_capability": continuation_capability, "host_identity": identity},
            label="clarification host claim",
        )
        reason = None if unavailable_reason is None else _required_text(unavailable_reason, label="unavailable_reason", maximum=1024)
        if outcome == "unavailable" and not reason:
            raise V12StoreError("unavailable delivery requires a reason", code="invalid_argument")
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute(
                "SELECT * FROM clarification_holds WHERE clarification_binding=? AND project_hash=?",
                (binding_ref, self.project_hash),
            ).fetchone()
            if row is None or row["assignment_id"] is None or str(row["continuation_capability"] or "") != continuation_capability:
                raise V12StoreError("worker clarification delivery was not found", code="clarification_binding_not_found")
            state = str(row["state"])
            if state in {"delivered", "unavailable"}:
                if state == outcome and str(row["delivery_claim_digest"] or "") == claim_digest and (outcome != "unavailable" or str(row["unavailable_reason"] or "") == reason):
                    return {"state": state, "replayed": True}
                raise V12StoreError("clarification delivery has a different outcome", code="command_conflict")
            if state != "delivery_claimed" or str(row["delivery_claim_digest"] or "") != claim_digest:
                raise V12StoreError("clarification delivery was not claimed by this host", code="command_conflict")
            sequence = self._timeline(
                connection, event_type="clarification_delivery_" + outcome, entity_type="clarification_hold",
                entity_id=binding_ref, payload={"state": outcome}, task_id=str(row["task_id"]),
                delegation_id=str(row["assignment_id"]), decision_id=str(row["response_decision_id"]),
            )
            connection.execute(
                "UPDATE clarification_holds SET state=?,delivery_sequence=?,unavailable_reason=?,updated_at=? WHERE clarification_binding=?",
                (outcome, sequence, reason, _now(), binding_ref),
            )
            return {"state": outcome, "replayed": False}
        return self._write(write)

    def record_user_decision(self, *, task_id: Any, subject_type: Any, subject_id: Any, subject_digest: Any = None, decision_type: Any = None, prompt: Any = None, response_original: Any = None, user_language: Any = None, approval_handle: Any = None, approval_view_content_digest: Any = None, approval_view_source_sequence: Any = None, supersedes_decision_id: Any = None, steering_delta: Any = None, clarification_binding: Any = None, idempotency_key: Any = None, _connection: sqlite3.Connection | None = None) -> tuple[dict[str, Any], bool]:
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
            "prompt": _optional_text(prompt, label="prompt") or "",
            "response_original": _optional_text(response_original, label="response_original") or "",
            "user_language": _language(user_language),
            "approval_handle": None if approval_handle is None else _required_text(approval_handle, label="approval_handle", maximum=160),
            "approval_view_content_digest": _digest(approval_view_content_digest, label="approval_view_content_digest"),
            "approval_view_source_sequence": approval_view_source_sequence,
            "supersedes_decision_id": None if supersedes_decision_id is None else self._record_identifier(supersedes_decision_id, label="supersedes_decision_id"),
            "steering_delta": steering_delta,
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
            if not isinstance(retired, list) or not isinstance(additions, list) or not retired and not additions:
                raise V12StoreError("steering_delta must contain at least one operation", code="invalid_argument", details={"field": "steering_delta"})
            if any(not isinstance(value, str) for value in retired) or len({value for value in retired if isinstance(value, str)}) != len(retired):
                raise V12StoreError("steering_delta is invalid", code="invalid_argument", details={"field": "steering_delta"})
            for addition in additions:
                if (
                    not isinstance(addition, Mapping)
                    or set(addition) - {"outcome_ref", "category", "text"}
                    or not {"category", "text"}.issubset(addition)
                    or addition.get("category") not in {"requirement", "constraint", "acceptance", "verification"}
                    or not isinstance(addition.get("text"), str)
                    or not addition["text"].strip()
                    or ("outcome_ref" in addition and not isinstance(addition.get("outcome_ref"), str))
                ):
                    raise V12StoreError("steering_delta is invalid", code="invalid_argument", details={"field": "steering_delta"})
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
                expected_prompt_digest = _sha256_prefixed(payload["prompt"], label="clarification prompt")
                binding_decision_type = str(clarification_row["decision_type"])
                # A plan-review binding represents a family; its consumed
                # outcome is one of the legal plan decisions.
                decision_matches = binding_decision_type == decision or (binding_decision_type == "plan_review" and kind == "plan" and decision in {"approve", "request_revision", "cancel"})
                if (str(clarification_row["task_id"]) != anchor or str(clarification_row["subject_type"]) != kind or str(clarification_row["subject_id"]) != subject or not decision_matches or str(clarification_row["prompt_digest"]) != expected_prompt_digest or str(clarification_row["prompt"]) != payload["prompt"] or str(clarification_row["prompt_language"]) != payload["user_language"]):
                    raise V12StoreError("clarification binding does not match the decision", code="clarification_binding_mismatch")
                if int(clarification_row["effective_contract_revision"]) != int(self._effective_contract(connection, anchor)["revision"]):
                    raise V12StoreError("clarification binding is stale", code="clarification_binding_stale")
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
            retired_item_ids: list[str] = []
            steering_targets: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
            active_contract_rows: dict[str, dict[str, Any]] = {}
            if has_contract_delta:
                delta = payload["steering_delta"]
                retired_item_ids = [self._outcome_item_id(connection, anchor, value) for value in delta.get("retire_item_refs", [])]
                current_revision = self._effective_contract(connection, anchor)["revision"]
                active_rows = connection.execute(
                    "SELECT i.*,d.details_json,d.source_decision_id FROM effective_contract_items i "
                    "JOIN effective_contract_item_details d ON d.item_id=i.item_id "
                    "WHERE i.task_id=? AND (i.retired_revision IS NULL OR i.retired_revision>?)",
                    (anchor, current_revision),
                ).fetchall()
                active_contract_rows = {
                    str(row["item_id"]): (_row(row) or {}) for row in active_rows
                }
                retired_set = set(retired_item_ids)
                available = [item_id for item_id in active_contract_rows if item_id not in retired_set]
                for addition_index, addition in enumerate(delta.get("add", [])):
                    supplied_ref = addition.get("outcome_ref")
                    if supplied_ref is None:
                        if len(available) != 1:
                            raise V12StoreError("steering addition requires an exact outcome_ref", code="invalid_argument", details={"field": "steering_delta.add.outcome_ref"})
                        target_id = available[0]
                    else:
                        target_id = self._outcome_item_id(connection, anchor, supplied_ref)
                    if target_id not in active_contract_rows or target_id in retired_set:
                        raise V12StoreError("steering addition target is not active", code="outcome_item_not_found", details={"field": "steering_delta.add.outcome_ref"})
                    steering_targets.setdefault(target_id, []).append((addition_index, addition))
            identifier = new_sharded_id("decision", self.project_hash)
            sequence = self._timeline(connection, event_type="user_decision_recorded", entity_type="user_decision", entity_id=identifier, payload={"decision_id": identifier, "subject_type": kind, "subject_id": subject, "subject_digest": bound_digest, "decision_type": decision}, task_id=anchor, decision_id=identifier)
            # Retain the historical columns for opening immutable pre-rework
            # rows. New writes map the neutral prompt into the old storage
            # slot and leave the retired English normalization empty; neither
            # field is part of the public contract or compact projections.
            connection.execute("INSERT INTO user_decisions(decision_id,project_hash,task_id,subject_type,subject_id,subject_digest,decision_type,prompt_en,response_original,response_en,user_language,attribution,supersedes_decision_id,created_at,created_sequence,steering_delta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (identifier, self.project_hash, anchor, kind, subject, bound_digest, decision, payload["prompt"], payload["response_original"], "", payload["user_language"], DECISION_ATTRIBUTION, payload["supersedes_decision_id"], _now(), sequence, _canonical_json(payload["steering_delta"], label="steering_delta") if has_contract_delta else None))
            if has_contract_delta:
                if retired_item_ids or steering_targets:
                    revision = self._effective_contract(connection, anchor)["revision"] + 1
                    for item_id in {*retired_item_ids, *steering_targets}:
                        connection.execute("UPDATE effective_contract_items SET retired_revision=? WHERE item_id=? AND retired_revision IS NULL", (revision, item_id))
                    for prior_item_id, additions in steering_targets.items():
                        prior = active_contract_rows[prior_item_id]
                        details = _load_json(str(prior["details_json"]), label="effective contract item details")
                        if not isinstance(details, Mapping):
                            raise V12StoreError("stored V12 data is invalid", code="ledger_corrupt")
                        merged = {
                            "acceptance_criteria": list(details.get("acceptance_criteria", [])),
                            "verification_criteria": list(details.get("verification_criteria", [])),
                            "constraints": list(details.get("constraints", [])),
                            "requirement_extensions": list(details.get("requirement_extensions", [])),
                            "source_fragments": list(details.get("source_fragments", [])),
                            "supersedes_item_ref": self._outcome_ref(prior_item_id),
                        }
                        for addition_index, addition in additions:
                            category, text = str(addition["category"]), str(addition["text"])
                            if category == "acceptance":
                                if text in merged["verification_criteria"]:
                                    merged["verification_criteria"].remove(text)
                                if text not in merged["acceptance_criteria"]:
                                    merged["acceptance_criteria"].append(text)
                            elif category == "verification":
                                if text not in merged["acceptance_criteria"] and text not in merged["verification_criteria"]:
                                    merged["verification_criteria"].append(text)
                            elif category == "constraint":
                                if text not in merged["constraints"]:
                                    merged["constraints"].append(text)
                            elif text != str(prior["text"]) and text not in merged["requirement_extensions"]:
                                merged["requirement_extensions"].append(text)
                            merged["source_fragments"].append({
                                "source_type": "user_steer",
                                "path": f"steer.add[{addition_index}].text",
                                "text": text,
                                "decision_ref": record_ref(identifier),
                            })
                        replacement_id = "outcome-" + uuid.uuid4().hex
                        connection.execute(
                            "INSERT INTO effective_contract_items(item_id,project_hash,task_id,category,ordinal,text,created_revision,retired_revision) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                            (replacement_id, self.project_hash, anchor, prior["category"], prior["ordinal"], prior["text"], revision),
                        )
                        connection.execute(
                            "INSERT INTO effective_contract_item_details(item_id,details_json,source_decision_id) VALUES (?, ?, ?)",
                            (replacement_id, _canonical_json(merged, label="effective contract item details"), identifier),
                        )
                    connection.execute("INSERT INTO effective_contract_revisions(task_id,revision,decision_id,created_sequence) VALUES (?, ?, ?, ?)", (anchor, revision, identifier, sequence))
            if approval_handle is not None:
                cursor = connection.execute("UPDATE approval_handles SET consumed_decision_id=? WHERE approval_handle=? AND consumed_decision_id IS NULL", (identifier, payload["approval_handle"]))
                if cursor.rowcount != 1:
                    raise V12StoreError("approval handle has already been used", code="approval_handle_consumed")
            if clarification_row is not None:
                response_digest = _sha256_prefixed(payload["response_original"], label="clarification response")
                cursor = connection.execute("UPDATE clarification_bindings SET response_digest=?,consumed_decision_id=? WHERE clarification_binding=? AND consumed_decision_id IS NULL", (response_digest, identifier, payload["clarification_binding"]))
                if cursor.rowcount != 1:
                    raise V12StoreError("clarification binding has already been used", code="clarification_binding_consumed")
            return {"decision": self._compact_decision(self._decision(connection, identifier, task_id=anchor))}
        if _connection is not None:
            # The aggregate receipt owns idempotency for semantic calls.  The
            # old mutation ledger remains available to legacy callers only.
            return write(_connection), False
        return self._mutation("record_user_decision", payload, idempotency_key, write)

    def set_governance_mode(self, *, task_id: Any, mode: Any, rationale: Any, risk_factors: Any, source: Any, initiative_id: Any, idempotency_key: Any) -> tuple[dict[str, Any], bool]:
        mode_value, source_value = _required_text(mode, label="mode", maximum=16).lower(), _required_text(source, label="source", maximum=32).lower()
        if mode_value not in GOVERNANCE_MODES or source_value not in GOVERNANCE_SOURCES:
            raise V12StoreError("governance assessment is invalid", code="invalid_governance_mode")
        payload = {"task_id": self._task_identifier(task_id), "mode": mode_value, "rationale": _optional_text(rationale, label="rationale"), "risk_factors": _text_list(risk_factors, label="risk_factors"), "source": source_value, "initiative_id": None if initiative_id is None else self._record_identifier(initiative_id, label="initiative_id")}
        def write(connection: sqlite3.Connection) -> dict[str, Any]:
            task = self._task(connection, payload["task_id"])
            self._require_no_pending_user_decision(connection, task_id=str(task["task_id"]))
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
            if existing is not None:
                # Initiatives describe cross-task or long-lived governance
                # topology.  Ordinary worker-stage, report-link, decision-link
                # and notes churn is already durable in the task timeline and
                # must not manufacture a fresh initiative revision.
                current_parent = by_kind["parent"][0] if by_kind["parent"] else None
                material = (
                    goal_value != existing["goal"]
                    or risk_value != existing["risk"]
                    or state_value != existing["status"]
                    or parent != current_parent
                    or dependency_values != by_kind["dependency"]
                    or task_values != by_kind["task"]
                )
                if not material:
                    raise V12StoreError(
                        "initiative revision has no material governance change",
                        code="initiative_revision_not_material",
                        details={"field": "initiative_ref", "expected": "goal_dependency_graph_risk_status_or_cross_task_change"},
                    )
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
            self._require_no_pending_user_decision(connection, task_id=anchor)
            if kind == "task":
                existing = self._task_closure(connection, anchor)
                if existing is not None:
                    recorded = str(existing["verdict"])
                    return {
                        "closure": self._closure(connection, str(existing["closure_id"])),
                        "initiative": None,
                        "warnings": [],
                        "advisory_status": "recorded",
                        "execution_outcome": self._execution_evidence(connection, anchor),
                        "conformance_review": self._conformance_review(connection, anchor),
                        "verdict_adjustment": {"requested": decision, "recorded": recorded},
                    }
            initiative: dict[str, Any] | None = None
            conformance = self._conformance_review(connection, anchor)
            conformance_verdict = {
                "ready": "ready",
                "ready_with_risks": "ready_with_risks",
                "not_ready": "not_ready",
            }[str(conformance["status"])]
            verdict_rank = {"not_ready": 0, "ready_with_risks": 1, "ready": 2}
            effective_decision = min((decision, conformance_verdict), key=lambda value: verdict_rank[value])
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
            sequence = self._timeline(connection, event_type="governance_closure_submitted", entity_type="governance_closure", entity_id=closure_id, payload={"closure_id": closure_id, "task_id": anchor, "subject_type": kind, "subject_id": subject, "requested_verdict": decision, "verdict": effective_decision}, task_id=anchor, initiative_id=closure_initiative, closure_id=closure_id)
            timestamp = _now()
            connection.execute("INSERT INTO governance_closures(closure_id,project_hash,subject_type,subject_id,verdict,evidence_json,unresolved_risks_json,follow_ups_json,initiative_status,completion_notes_json,created_at,created_sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (closure_id, self.project_hash, kind, subject, effective_decision, _canonical_json(payload["evidence"], label="evidence"), _canonical_json(payload["unresolved_risks"], label="unresolved_risks"), _canonical_json(payload["follow_ups"], label="follow_ups"), status_value, None if payload["completion_notes"] is None else _canonical_json(payload["completion_notes"], label="completion_notes"), timestamp, sequence))
            if kind == "task":
                connection.execute("UPDATE tasks SET updated_at=?,updated_sequence=? WHERE task_id=? AND project_hash=?", (timestamp, sequence, anchor, self.project_hash))
            links = [] if closure_initiative is None else self._initiative_links(connection, [closure_initiative])
            return {
                "closure": self._closure(connection, closure_id),
                "initiative": initiative,
                "warnings": self._warning_values(links),
                "advisory_status": "recorded",
                "execution_outcome": self._execution_evidence(connection, anchor),
                # Closure is advisory bookkeeping; expose the same immutable
                # conformance projection in the mutation response so callers
                # cannot mistake a recorded verdict for evidence readiness.
                "conformance_review": self._conformance_review(connection, anchor),
                "verdict_adjustment": {"requested": decision, "recorded": effective_decision},
            }
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
            return {"task": task, "effective_contract": self._effective_contract(connection, anchor), "aggregate_coverage": self._aggregate_coverage(connection, anchor), "conformance_review": self._conformance_review(connection, anchor), "execution_outcome": self._execution_evidence(connection, anchor), "advisory_closure": self._advisory_closure(connection, anchor), "delegations": delegations, "continuations": continuations, "reports": reports, "decisions": decisions, "consumption_receipts": receipts, "timeline": timeline, "next_sequence": next_sequence, "has_more": has_more}
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

    def admit_result_report(self, *, delegation_id: Any, idempotency_key: Any) -> None:
        """Fail closed before a result assembly when required input evidence is unread."""
        anchor, identifier = self._task_for_delegation(delegation_id)
        client_key = _required_text(idempotency_key, label="idempotency_key", maximum=IDEMPOTENCY_KEY_MAX_LENGTH)
        idempotency = hashlib.sha256(
            _canonical_json(
                {"operation": "submit_report", "retry_handle": client_key},
                label="idempotency operation key",
            ).encode("utf-8")
        ).hexdigest()

        def read(connection: sqlite3.Connection) -> None:
            replay = connection.execute(
                "SELECT 1 FROM idempotency WHERE operation='submit_report' AND idempotency_key=? LIMIT 1",
                (idempotency,),
            ).fetchone()
            if replay is not None:
                return
            delegation = self._delegation(connection, identifier, task_id=anchor)
            inputs = delegation["input_report_ids"]
            if inputs:
                placeholders = ",".join("?" for _ in inputs)
                observed = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT DISTINCT report_id FROM report_consumption_receipts "
                        f"WHERE consumer_delegation_id=? AND has_more=0 AND report_id IN ({placeholders})",
                        [identifier, *inputs],
                    ).fetchall()
                }
                if any(item not in observed for item in inputs):
                    raise V12StoreError("declared input evidence must be read before a result report", code="input_evidence_unread")
            existing = connection.execute(
                "SELECT 1 FROM reports WHERE delegation_id=? AND report_type='result' AND assembly_state='finalized' LIMIT 1",
                (identifier,),
            ).fetchone()
            if existing is not None:
                raise V12StoreError("delegation already has a finalized result report", code="result_report_exists")

        self._read(read)

    def read_reports(self, *, report_ids: Any, sections: Any = None, cursor: Any = None, max_bytes: Any = REPORT_READ_MAX_BYTES, consumer_delegation_id: Any = None, task_id: Any = None) -> dict[str, Any]:
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
