"""Shared public and durable constraints for the Cortex V12 ledger.

The values in this module keep the MCP schema, service boundary, and SQLite
validation on the same finite set of identifiers, enums, page sizes, payload
limits, and versioned task-contract identifiers.
"""
from __future__ import annotations

import re
import uuid


IDENTIFIER_MAX_LENGTH = 160
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$"
IDENTIFIER_RE = re.compile(IDENTIFIER_PATTERN)

PROJECT_HASH_LENGTH = 64
TASK_RANDOM_LENGTH = 32
TASK_ID_PATTERN = rf"^task-([0-9a-f]{{{PROJECT_HASH_LENGTH}}})-([0-9a-f]{{{TASK_RANDOM_LENGTH}}})$"
TASK_ID_RE = re.compile(TASK_ID_PATTERN)
# Public task references are short derived views of canonical task IDs, never
# durable database keys. Exact cross-shard matching is still required, so the
# reference is a locator rather than an authority or fuzzy recovery token.
TASK_REF_SUFFIX_LENGTH = 12
TASK_REF_PATTERN = rf"^t_([0-9a-f]{{{TASK_REF_SUFFIX_LENGTH}}})$"
TASK_REF_RE = re.compile(TASK_REF_PATTERN)
# Retain direct-service recovery for older stored callers only. The public MCP
# schema advertises and accepts only TASK_REF_PATTERN above.
LEGACY_TASK_REF_SUFFIX_LENGTH = 20
LEGACY_TASK_REF_RE = re.compile(rf"^t_([0-9a-f]{{{LEGACY_TASK_REF_SUFFIX_LENGTH}}})$")
SHARDED_RECORD_PATTERN = rf"^(delegation|report|initiative|decision)-([0-9a-f]{{{PROJECT_HASH_LENGTH}}})-([0-9a-f]{{{TASK_RANDOM_LENGTH}}})$"
SHARDED_RECORD_RE = re.compile(SHARDED_RECORD_PATTERN)
# Public entity references deliberately carry only a type discriminator and
# the final UUID suffix.  They are locators, never persisted keys.  Resolving
# them always scans private V12 shards for one *exact* match and fails closed
# on zero or multiple matches.
RECORD_REF_SUFFIX_LENGTH = 12
RECORD_REF_PREFIXES = {
    "delegation": "d",
    "report": "r",
    "initiative": "i",
    "decision": "u",
}
RECORD_REF_PATTERNS = {
    kind: rf"^{prefix}_([0-9a-f]{{{RECORD_REF_SUFFIX_LENGTH}}})$"
    for kind, prefix in RECORD_REF_PREFIXES.items()
}
RECORD_REF_RES = {kind: re.compile(pattern) for kind, pattern in RECORD_REF_PATTERNS.items()}

TEXT_MAX_LENGTH = 65_536
ROLE_MAX_LENGTH = 160
PROJECT_ROOT_MAX_LENGTH = 16_384
IDEMPOTENCY_KEY_MAX_LENGTH = 256
JSON_MAX_BYTES = 65_536
JSON_MAX_DEPTH = 32

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
MAX_LINKS = 100
MAX_REPORT_IDS = 20
MAX_DECISION_IDS = 20

REPORT_CONTENT_SCHEMA = "cortex/report-content/v1"
REPORT_SINGLE_MAX_BYTES = 65_536
REPORT_CHUNK_MAX_BYTES = 32_768
REPORT_MAX_CHUNKS = 256
REPORT_MAX_BYTES = 8 * 1024 * 1024
REPORT_ASSEMBLING_MAX_PER_TASK = 8
REPORT_ASSEMBLING_MAX_BYTES_PER_TASK = 16 * 1024 * 1024
REPORT_RETAINED_MAX_BYTES_PER_TASK = 128 * 1024 * 1024
REPORT_READ_MAX_BYTES = 65_536
REPORT_RESPONSE_MAX_BYTES = 224 * 1024
REPORT_SECTION_MAX_LENGTH = 128
REPORT_SECTION_PATTERN = r"^[a-z][a-z0-9_.-]{0,127}$"
REPORT_SECTION_RE = re.compile(REPORT_SECTION_PATTERN)
REPORT_ASSEMBLY_STATES = ("assembling", "finalized", "aborted")
REPORT_MODES = ("single", "begin", "append", "finalize", "abort")
PLAN_REVIEW_POLICIES = ("informational", "required")

# The complete task/result contract is deliberately ordinary bounded text.  It
# is not an execution plan or backend workflow authority: it is durable
# context for a worker and a faithful source for the private human view.
TASK_CONTRACT_VERSION = "cortex/task-contract/v1"
LANGUAGE_TAG_MAX_LENGTH = 64
# A deliberately conservative BCP-47-shaped tag.  Require the usual ISO
# 639 two- or three-letter primary language subtag so a language name such as
# ``Russian`` cannot be retained accidentally.  This is syntax validation
# only; it never classifies or interprets user prose.
LANGUAGE_TAG_PATTERN = r"^(?![Uu][Nn][Dd]$)[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*$"
LANGUAGE_TAG_RE = re.compile(LANGUAGE_TAG_PATTERN)
TASK_CONTRACT_ITEM_MAX_LENGTH = 4_096
TASK_CONTRACT_MAX_ITEMS = 100

# Human views are entirely host-private derived files.  The renderer version
# belongs in their metadata so a verified view can be invalidated when the
# safe, inert rendering implementation changes.
PROJECTION_RENDERER_VERSION = "cortex/v12-markdown/v2"
HUMAN_VIEW_STATUSES = ("ready", "stale", "conflict", "unavailable", "disabled")

REPORT_TYPES = ("progress", "result", "synthesis", "plan")
REPORT_STATUSES = ("partial", "completed", "blocked", "failed")
GOVERNANCE_MODES = ("minimal", "light", "full")
GOVERNANCE_SOURCES = ("model", "user_override")
INITIATIVE_STATUSES = ("proposed", "active", "paused", "completed", "closed", "cancelled")
CLOSURE_VERDICTS = ("ready", "ready_with_risks", "not_ready")
CLOSURE_SUBJECTS = ("task", "initiative")

DECISION_SUBJECTS = ("task", "plan", "initiative", "delegation", "report")
DECISION_TYPES = (
    "approve", "reject", "request_revision", "clarification", "cancel",
    "accept_risk", "override",
)
DECISION_ATTRIBUTION = "user_via_coordinator"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
DIGEST_RE = re.compile(DIGEST_PATTERN)


def new_task_id(project_hash: str) -> str:
    """Mint one opaque task ID that carries exactly one project-shard hash."""
    if not isinstance(project_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", project_hash):
        raise ValueError("project hash is invalid")
    return f"task-{project_hash}-{uuid.uuid4().hex}"


def task_shard_hash(task_id: object) -> str | None:
    """Return the embedded V12 shard hash without searching any ledger."""
    if not isinstance(task_id, str):
        return None
    match = TASK_ID_RE.fullmatch(task_id)
    return None if match is None else match.group(1)


def task_ref(task_id: object) -> str | None:
    """Return the compact public locator derived from one canonical task ID."""
    if not isinstance(task_id, str):
        return None
    match = TASK_ID_RE.fullmatch(task_id)
    if match is None:
        return None
    return f"t_{match.group(2)[-TASK_REF_SUFFIX_LENGTH:]}"


def task_ref_parts(value: object, *, allow_legacy: bool = True) -> str | None:
    """Return an exact public or direct-legacy suffix without fuzzy matching."""
    if not isinstance(value, str):
        return None
    match = TASK_REF_RE.fullmatch(value)
    if match is not None:
        return match.group(1)
    if allow_legacy:
        legacy = LEGACY_TASK_REF_RE.fullmatch(value)
        if legacy is not None:
            return legacy.group(1)
    return None


def new_sharded_id(prefix: str, project_hash: str) -> str:
    """Mint an opaque local record ID that can reject foreign-shard references."""
    if prefix not in {"delegation", "report", "initiative", "decision"}:
        raise ValueError("record prefix is invalid")
    if not isinstance(project_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", project_hash):
        raise ValueError("project hash is invalid")
    return f"{prefix}-{project_hash}-{uuid.uuid4().hex}"


def record_shard_hash(value: object) -> str | None:
    """Return an embedded optional record shard without any database search."""
    if not isinstance(value, str):
        return None
    match = SHARDED_RECORD_RE.fullmatch(value)
    return None if match is None else match.group(2)


def record_ref(value: object) -> str | None:
    """Return the public compact locator for a canonical durable entity."""
    if not isinstance(value, str):
        return None
    match = SHARDED_RECORD_RE.fullmatch(value)
    if match is None:
        return None
    prefix = RECORD_REF_PREFIXES.get(match.group(1))
    if prefix is None:
        return None
    return f"{prefix}_{match.group(3)[-RECORD_REF_SUFFIX_LENGTH:]}"


def record_ref_parts(value: object, *, label: str) -> str | None:
    """Validate one typed public entity ref and return its exact UUID suffix."""
    kind = label.removesuffix("_id").removesuffix("_ref")
    matcher = RECORD_REF_RES.get(kind)
    if matcher is None or not isinstance(value, str):
        return None
    match = matcher.fullmatch(value)
    return None if match is None else match.group(1)
