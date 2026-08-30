"""Shared public and durable constraints for the Cortex V12 ledger.

The values in this module keep the MCP schema, service boundary, and SQLite
validation on the same finite set of identifiers, enums, page sizes, payload
limits, and versioned task-contract identifiers.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping


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
# One advertised aggregate UTF-8 limit for every public tool argument object.
# Store rows may add canonical identifiers, timeline metadata, and an
# idempotency receipt after this public envelope is admitted.  Those
# server-generated fields use the separate bounded result allowance below;
# they must not consume a caller's advertised operation budget.
MCP_OPERATION_MAX_BYTES = JSON_MAX_BYTES
# The only larger JSON objects are internally persisted mutation results.  The
# extra room is exclusively for the bounded server-generated result envelope,
# not a second public input allowance.
MUTATION_RESULT_MAX_BYTES = MCP_OPERATION_MAX_BYTES + 4_096
WORKER_MESSAGE_MAX_BYTES = 128 * 1024

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
MAX_LINKS = 100
MAX_REPORT_IDS = 20
MAX_DECISION_IDS = 20

REPORT_CONTENT_SCHEMA = "cortex/report-content/v1"
CANONICAL_REPORT_SCHEMAS = {
    "progress": "cortex/report/progress/v1",
    "result": "cortex/report/result/v1",
    "synthesis": "cortex/report/synthesis/v1",
    "plan": "cortex/report/plan/v1",
}
CANONICAL_REPORT_V2_SCHEMAS = {
    "result": "cortex/report/result/v2",
    "synthesis": "cortex/report/synthesis/v2",
    "plan": "cortex/report/plan/v2",
}
# V3 is the current specialist evidence envelope. V1/V2 evidence remains
# immutable and readable; only new V3 finalization receives completeness
# admission while its assembly can still be corrected.
CANONICAL_REPORT_EVIDENCE_SCHEMAS = {
    "result": "cortex/report/result/v3",
    "synthesis": "cortex/report/synthesis/v3",
    "plan": "cortex/report/plan/v3",
}
REPORT_SEMANTIC_STATUSES = ("pending", "semantic_valid", "semantic_invalid", "legacy")
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
# New report writes are always assembled. Historical finalized one-chunk rows
# remain readable, but no active write contract can create another such row.
REPORT_MODES = ("begin", "append", "finalize", "abort")
PLAN_REVIEW_POLICIES = ("informational", "required")

# The complete task/result contract is deliberately ordinary bounded text.  It
# is not an execution plan or backend workflow authority: it is durable
# context for a worker and a faithful source for the private human view.
# V2 records that verification entries are derived deterministically from the
# accepted criteria at the public boundary.  The durable version participates
# in idempotency and decision-subject digests; historical V1 rows remain
# immutable readable evidence.
TASK_CONTRACT_VERSION = "cortex/task-contract/v2-criteria-derived"
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
PROJECTION_RENDERER_VERSION = "cortex/v12-markdown/v8"
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
    "accept_risk", "override", "steer",
)
DECISION_ATTRIBUTION = "user_via_coordinator"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
DIGEST_RE = re.compile(DIGEST_PATTERN)


def canonical_report_semantic_status(report_type: object, content: object) -> str:
    """Classify fixed canonical report data without mutating stored evidence."""
    if not isinstance(report_type, str) or report_type not in CANONICAL_REPORT_SCHEMAS:
        return "legacy"
    if not isinstance(content, Mapping):
        return "legacy"
    schema = content.get("schema")
    # V2 is deliberately additive: v1/legacy evidence stays immutable and
    # readable, while the newer forms carry structured outcome coverage.
    if schema in {CANONICAL_REPORT_V2_SCHEMAS.get(report_type), CANONICAL_REPORT_EVIDENCE_SCHEMAS.get(report_type)}:
        base = {
            "result": ("summary", "outcome", "changes", "verification", "risks"),
            "synthesis": ("summary", "findings", "recommendations"),
            "plan": ("summary", "scope", "stages", "verification"),
        }[report_type]
        required = {*base, "deviations", "unresolved", "risks", "verification"}
        current_v3 = schema == CANONICAL_REPORT_EVIDENCE_SCHEMAS.get(report_type)
        # A planner can prove the proposed work breakdown and its observable
        # planning checks, but cannot truthfully issue the post-implementation
        # documentation-impact verdict.  That assessment belongs to result
        # and synthesis publication after implementation/verification.
        evidence_required = current_v3 and report_type in {"plan", "result", "synthesis"}
        if evidence_required:
            if report_type in {"plan", "result"}:
                required.add("verification_facts")
            if report_type in {"result", "synthesis"}:
                required.add("documentation_impact")
        elif not current_v3:
            # Historical v2 evidence already stored caller-authored coverage.
            # Keep those immutable rows readable, but do not admit the field in
            # the current v3 public contract or use it as current authority.
            required.add("contract_coverage")
        # Current v3 publication scope is owned by the immutable assignment
        # capability.  Accepting caller-authored coverage here would make the
        # model reconstruct server-owned item identities and would duplicate
        # an authority relation the store already has.  Historical v2 remains
        # readable through the branch above, but v3 deliberately has no
        # coverage input field.
        allowed_keys = {"schema", "source_text", *required}
        # A planner may optionally carry an early impact hypothesis, but it
        # cannot be required to know the post-implementation verdict. The
        # explicit authoritative assessment belongs to the later synthesis.
        if report_type == "plan":
            allowed_keys.add("documentation_impact")
        if any(not isinstance(key, str) or key not in allowed_keys for key in content):
            return "semantic_invalid"
        if not isinstance(content.get("summary"), str) or not content["summary"].strip():
            return "semantic_invalid"
        if "source_text" in content and not isinstance(content["source_text"], str):
            return "semantic_invalid"
        if report_type == "result" and not isinstance(content.get("outcome"), str):
            return "semantic_invalid"
        # ``outcome`` and plan ``scope`` are scalar narrative fields; the
        # remaining contract sections are arrays.  Keeping this distinction at
        # the canonical semantic boundary prevents valid v3 envelopes from
        # being rejected as incomplete after all of their evidence is present.
        scalar_fields = {"summary", "outcome"}
        if report_type == "plan":
            scalar_fields.add("scope")
        if any(not isinstance(content.get(key), list) for key in required - scalar_fields - {"documentation_impact"}):
            return "semantic_invalid"
        if report_type in {"result", "synthesis"} and evidence_required and (not isinstance(content.get("documentation_impact"), str) or not content["documentation_impact"].strip()):
            return "semantic_invalid"
        if not current_v3 and any(
            not isinstance(item, Mapping)
            or not isinstance(item.get("item_ref"), str)
            or not isinstance(item.get("status"), str)
            for item in content["contract_coverage"]
        ):
            return "semantic_invalid"
        if report_type == "plan" and evidence_required:
            stages = content["stages"]
            if not stages:
                return "semantic_invalid"
            expected_orders = list(range(1, len(stages) + 1))
            observed_orders: list[int] = []
            for stage in stages:
                if not isinstance(stage, Mapping) or set(stage) != {
                    "order", "owner", "dependencies", "work", "verification",
                }:
                    return "semantic_invalid"
                order = stage.get("order")
                if not isinstance(order, int) or isinstance(order, bool) or order < 1:
                    return "semantic_invalid"
                observed_orders.append(order)
                if not isinstance(stage.get("owner"), str) or not stage["owner"].strip():
                    return "semantic_invalid"
                dependencies = stage.get("dependencies")
                if (
                    not isinstance(dependencies, list)
                    or any(not isinstance(item, int) or isinstance(item, bool) or item < 1 or item >= order for item in dependencies)
                    or len(set(dependencies)) != len(dependencies)
                ):
                    return "semantic_invalid"
                for field in ("work", "verification"):
                    values = stage.get(field)
                    if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item.strip() for item in values):
                        return "semantic_invalid"
            if observed_orders != expected_orders:
                return "semantic_invalid"
        return "semantic_valid"
    if schema != CANONICAL_REPORT_SCHEMAS[report_type]:
        return "legacy" if schema is None else "semantic_invalid"
    fields: dict[str, tuple[str, ...]] = {
        "progress": ("summary", "completed", "active", "blocked", "next_steps"),
        "result": ("summary", "outcome", "changes", "verification", "risks"),
        "synthesis": ("summary", "findings", "recommendations"),
        "plan": ("summary", "scope", "stages", "verification"),
    }
    required = fields[report_type]
    # source_text is the only optional user-authored value. The closed field
    # set rejects language tags and original/translated or en/ru duplicates.
    if any(not isinstance(key, str) or key not in {"schema", "source_text", *required} for key in content):
        return "semantic_invalid"
    if not isinstance(content.get("summary"), str) or not content["summary"].strip():
        return "semantic_invalid"
    if "source_text" in content and not isinstance(content["source_text"], str):
        return "semantic_invalid"
    for field in required:
        if field == "summary":
            continue
        if report_type == "result" and field == "outcome":
            if not isinstance(content.get(field), str):
                return "semantic_invalid"
        elif not isinstance(content.get(field), list):
            return "semantic_invalid"
    return "semantic_valid"


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


def task_ref_parts(value: object) -> str | None:
    """Return the exact canonical public task-reference suffix."""
    if not isinstance(value, str):
        return None
    match = TASK_REF_RE.fullmatch(value)
    return None if match is None else match.group(1)


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
