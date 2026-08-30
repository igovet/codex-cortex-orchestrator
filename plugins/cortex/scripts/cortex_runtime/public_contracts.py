"""The complete uniform public contract for the Cortex V12 ledger."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cortex_runtime.model_routing import NATIVE_MODELS, NATIVE_REASONING_EFFORTS
from cortex_runtime.worker_message import packaged_profile_names
from cortex_runtime.semantic_registry import OPERATION_NAMES
from cortex_runtime.v12_contract import (
    CLOSURE_SUBJECTS, DECISION_SUBJECTS, DECISION_TYPES, DIGEST_PATTERN,
    CLOSURE_VERDICTS,
    GOVERNANCE_MODES,
    GOVERNANCE_SOURCES,
    IDEMPOTENCY_KEY_MAX_LENGTH,
    IDENTIFIER_MAX_LENGTH,
    IDENTIFIER_PATTERN,
    INITIATIVE_STATUSES,
    JSON_MAX_BYTES,
    MAX_DECISION_IDS, MAX_LINKS,
    MAX_REPORT_IDS,
    PROJECT_ROOT_MAX_LENGTH,
    PLAN_REVIEW_POLICIES, REPORT_MODES,
    REPORT_SECTION_MAX_LENGTH, REPORT_SECTION_PATTERN, REPORT_STATUSES,
    REPORT_TYPES,
    ROLE_MAX_LENGTH, LANGUAGE_TAG_MAX_LENGTH, LANGUAGE_TAG_PATTERN, TASK_CONTRACT_ITEM_MAX_LENGTH,
    TASK_CONTRACT_MAX_ITEMS,
    TASK_ID_PATTERN,
    TASK_REF_PATTERN,
    RECORD_REF_PATTERNS,
    TEXT_MAX_LENGTH,
)


V12_TOOL_NAMES = OPERATION_NAMES


# MCP 2025-06-18 transports ordinary JSON Schema documents for both tool
# inputs and ``structuredContent`` outputs.  Keep the declared dialect on
# every top-level schema so tools/list consumers do not need to infer it from
# a transport detail or from a sibling tool.
_JSON_SCHEMA_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
_PACKAGED_PROFILE_COUNT = 22
HANDLE_COPY_RULE = "Copy only compact typed refs and server-issued opaque tokens from structuredContent.handles byte-for-byte. Canonical durable IDs in rendered evidence are non-callable. Never use UI ellipsis, prose, Markdown parsing, inferred IDs, or constructed paths."


def _string(
    *,
    minimum: int = 1,
    maximum: int = TEXT_MAX_LENGTH,
    enum: tuple[str, ...] | None = None,
    pattern: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "string", "minLength": minimum, "maxLength": maximum}
    if enum is not None:
        value["enum"] = list(enum)
    if pattern is not None:
        value["pattern"] = pattern
    if description is not None:
        value["description"] = description
    return value


def _identifier(*, task: bool = False, description: str | None = None) -> dict[str, Any]:
    return _string(
        maximum=IDENTIFIER_MAX_LENGTH,
        pattern=TASK_ID_PATTERN if task else IDENTIFIER_PATTERN,
        description=description,
    )


def _task_ref(*, description: str | None = None) -> dict[str, Any]:
    return _string(maximum=14, pattern=TASK_REF_PATTERN, description=description)


def _entity_ref(kind: str, *, description: str | None = None) -> dict[str, Any]:
    return _string(maximum=14, pattern=RECORD_REF_PATTERNS[kind], description=description or f"Compact {kind} locator emitted in handles. Copy byte-for-byte; never reconstruct it.")


def _idempotency_key() -> dict[str, Any]:
    return _string(maximum=IDEMPOTENCY_KEY_MAX_LENGTH, description="Required caller-generated opaque idempotency key for this mutation. Reuse the exact key only for an exact retry; changed arguments return idempotency_conflict without mutation.")


def _closed(
    properties: Mapping[str, Any],
    required: tuple[str, ...],
    *,
    description: str = "Closed Cortex V12 public tool input.",
) -> dict[str, Any]:
    """Return one closed, advertised MCP input object schema."""
    required_fields = list(required)
    if "idempotency_key" in properties and "idempotency_key" not in required_fields:
        required_fields.append("idempotency_key")
    schema = {
        "$schema": _JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "description": description,
        "additionalProperties": False,
        "properties": dict(properties),
    }
    # Some Codex host adapters reject an explicitly empty ``required`` array
    # and omit the entire tool from the model-visible catalogue.  Omission is
    # the JSON Schema representation of an object with no required properties.
    if required_fields:
        schema["required"] = required_fields
    return schema


def _json_value() -> dict[str, Any]:
    """Describe the server-enforced encoded JSON bound without a non-standard keyword.

    ``maxBytes`` is not a JSON Schema keyword.  Advertising it made clients
    treat a backend implementation limit as a caller-selectable schema
    feature, and inflated the tool catalogue.  The limit remains enforced by
    the storage/runtime validator and is documented in the property text.
    """
    return {
        "description": (
            "Opaque bounded JSON value. Cortex validates only finite JSON encoding, "
            f"depth, and the {JSON_MAX_BYTES}-byte bound; it never parses, classifies, "
            "or semantically validates report prose."
        ),
    }


def _identifier_array(*, maximum: int, minimum: int = 0) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": minimum,
        "maxItems": maximum,
        "uniqueItems": True,
        "items": _identifier(
            description="Opaque durable ID. Copy an emitted value byte-for-byte; do not parse, construct, or normalize it."
        ),
    }


def _entity_ref_array(kind: str, *, maximum: int, minimum: int = 0, unique: bool = True) -> dict[str, Any]:
    return {"type": "array", "minItems": minimum, "maxItems": maximum, "uniqueItems": unique, "items": _entity_ref(kind)}


_CALLABLE_DURABLE_NAMES = frozenset({
    "task_id", "task_ids", "delegation_id", "delegation_ids", "report_id", "report_ids",
    "decision_id", "decision_ids", "initiative_id", "initiative_ids", "closure_id", "closure_ids",
    "native_task_name",
})


def _assert_no_callable_durable_properties(schema: Mapping[str, Any], *, path: str) -> None:
    """Fail catalogue construction if an input/handle can invite ID copying."""
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        forbidden = sorted(str(key) for key in properties if str(key) in _CALLABLE_DURABLE_NAMES)
        if forbidden:
            raise RuntimeError(f"Cortex public callable schema leaks durable fields at {path}: {', '.join(forbidden)}")
        for key, value in properties.items():
            if isinstance(value, Mapping):
                _assert_no_callable_durable_properties(value, path=f"{path}.properties.{key}")
    for keyword in ("items", "allOf", "anyOf", "oneOf", "not"):
        value = schema.get(keyword)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, Mapping):
                _assert_no_callable_durable_properties(item, path=f"{path}.{keyword}")


def _text_array(*, maximum: int = TASK_CONTRACT_MAX_ITEMS, minimum: int = 0, description: str | None = None) -> dict[str, Any]:
    schema = {
        "type": "array",
        "minItems": minimum,
        "maxItems": maximum,
        "items": _string(
            maximum=TASK_CONTRACT_ITEM_MAX_LENGTH,
            description="One bounded English task-contract item.",
        ),
    }
    if description is not None:
        schema["description"] = description
    return schema


_PROPERTY_DESCRIPTIONS: dict[str, str] = {
    "project_root": "Required only to initialize create_task: the user-selected absolute canonical cwd/webhook root. Cortex resolves and stores it on the task; omit project_root from every later public tool call.",
    "objective": "Coordinator-authored English-normalized objective for durable internal worker context; it does not replace the exact original user request.",
    "user_request_original": "Required exact immutable user text in its original language; preserve it verbatim and do not replace it with the English objective.",
    "user_language": "Required asserted BCP-47 language tag for user_request_original, for example ru (never a language name such as Russian).",
    "requirements": "Required non-empty English requirements dimension. On the first create_task call, provide it together with the non-empty constraints and acceptance_criteria dimensions.",
    "constraints": "Required non-empty English constraints dimension, including forbidden actions where applicable. On the first create_task call, provide it together with the non-empty requirements and acceptance_criteria dimensions.",
    "acceptance_criteria": "Required non-empty English acceptance-criteria dimension used to review evidence. Cortex deterministically derives the complete persisted verification plan from these entries before storage.",
    "context": "Bounded JSON task context retained with the task; do not include secrets or raw diagnostic logs.",
    "outcome_assignments": "Optional assignment of current effective-contract items. Normally omit this entire property. Supply it only after inspect_task has returned the exact current effective_contract.items item_ref token catalogue; copy those emitted tokens byte-for-byte. Never supply human prose, requirement text, labels, ordinals, summaries, inferred values, or reconstructed tokens. If no exact server-returned token is available, omit this property. Group exact item_ref values under owned, contributing, or evidence_producing; owned responsibility is exclusive within one task revision.",
    "task_ref": "Preferred compact task locator emitted by open_task. Copy it byte-for-byte for every task-anchored tool; never use a UI-rendered task_id.",
    "idempotency_key": "Required caller-generated opaque idempotency key for this mutation. Reuse it only for an exact retry; a changed payload returns idempotency_conflict without mutation.",
    "after_sequence": "Exclusive durable timeline cursor. Use the returned next_sequence unchanged for the next page.",
    "role": "Short advisory worker role label selected by the coordinator; it grants no host authority.",
    "profile_name": "Exact packaged advisory profile selected independently from the human role. It must be copied from this tool's enum and load successfully.",
    "scope": "Required concise textual boundary of the delegated worker's ownership.",
    "instructions": "Coordinator-authored bounded non-empty worker instructions, preserved unchanged for durable worker context. Recommended guidance covers: documents to consume first (exact paths and why), applicable requirements, verification contract, ownership constraints, known documentation state, and further documentation discovery. This helpful structure is advisory only: no heading, Markdown, order, language, or section content is parsed as a server admission rule.",
    "parent_delegation_ref": "Optional exact emitted same-task predecessor delegation reference; it is evidence linkage, never a lifecycle gate.",
    "model": "Exact logical model selected by the coordinator. Luna remains explicit in durable data; only native serialization omits its override.",
    "reasoning_effort": "Exact coordinator-selected reasoning effort paired atomically with model; the runtime never escalates it.",
    "mode": "Required explicit report-upload phase on every submit_report call, including every continuation: begin, append, finalize, or abort. Phase map: begin selects report_type; append accepts report_ref, section, and content; finalize accepts report_ref and status; abort accepts report_ref and abort_reason_en. Cortex atomically assigns append order and computes the immutable final manifest. review_policy and supersedes_report_ref are legal only for begin with report_type=plan, never for append, finalize, or abort. Handles identify records only; they never encode, infer, or replace this required phase. New reports use begin, sequential append, then finalize; abort only an assembling report. Historical one-chunk rows are read-only compatibility evidence.",
    "report_type": "Immutable report kind selected only for begin: progress, result, synthesis, or plan. Only begin with report_type=plan may additionally include review_policy or supersedes_report_ref. It is fixed after begin and omitted for append, finalize, and abort.",
    "status": "Required only for finalize: partial, completed, blocked, or failed. A plan must be finalized with completed before it can receive an approval decision.",
    "content": "Required only for append. Storage accepts one finite bounded JSON value and worker read_reports returns its canonical JSON unchanged. New specialist plan and result evidence uses cortex/report/{plan,result}/v3 with observable evidence facts, residual risks, deviations, unresolved work, and documentation impact. The server derives authoritative contract coverage from the bound assignment and assigns plan-stage order from array position. Finalization rejects incomplete v3 evidence while the same report remains assembling for correction. Historical v1/v2 evidence remains readable. Canonical source material uses one optional unchanged source_text string with no language tag or translated/original companion.",
    "section": "Lowercase bounded chunk section label used to filter a report read.",
    "abort_reason_en": "English reason for intentionally ending an incomplete report assembly.",
    "supersedes_report_ref": "Optional exact emitted prior plan report replaced by this newly created plan; the referenced report must be a same-task plan.",
    "review_policy": "Optional plan-only review policy, set only on a new begin plan. Omit it for every non-plan and for append, finalize, or abort.",
    "sections": "Optional unique report section labels. The continuation cursor is valid only for the same ordered report refs and filters.",
    "cursor": "Opaque read_reports continuation cursor. Copy it byte-for-byte and reuse it only with the exact original report_refs and sections.",
    "rationale": "Optional bounded advisory governance rationale. It records model or user evidence and never blocks coordination.",
    "risk_factors": "Optional bounded advisory risk labels for this governance assessment.",
    "source": "Author of the advisory assessment: model evidence or an explicit user override asserted by the coordinator. The assessment is evidence only and does not create or retain a backend plan or approval obligation.",
    "goal": "Required bounded initiative goal when creating a new initiative; omit it only when revising an existing initiative to preserve its recorded goal.",
    "risk": "Optional bounded initiative risk summary retained in its next revision.",
    "status": "Semantic status for the selected record type. Use only one of this tool's advertised enum values.",
    "notes": "Optional bounded JSON initiative notes recorded in immutable revision history.",
    "subject_type": "Existing durable subject kind. Select the matching subject_ref and supply only fields permitted for that kind.",
    "verdict": "Advisory closure verdict. It records evidence and does not permit or prevent later coordination.",
    "evidence": "Required bounded opaque JSON closure evidence. Cortex validates only JSON encoding and size; do not include raw diagnostic logs or secrets.",
    "unresolved_risks": "Optional bounded opaque advisory-risk strings. Omit to store an empty list.",
    "follow_ups": "Optional bounded opaque advisory next-action strings. Omit to store an empty list.",
    "initiative_status": "Optional next initiative status, permitted only for an initiative closure.",
    "completion_notes": "Optional bounded opaque JSON completion notes for either a task or initiative closure. Cortex stores them without semantic or Markdown validation.",
    "subject_digest": "sha256: digest binding a plan or report decision to the exact immutable revision. It is required only for plan and report subjects; copy it byte-for-byte from the selected subject.",
    "decision_type": "Coordinator-asserted ordinary-chat user decision type; it is durable evidence, never backend authority.",
    "prompt": "Language-neutral prompt or decision context. The coordinator renders the user-facing question in the declared prompt_language.",
    "response_original": "Exact arbitrary-Unicode user response. It is stored privately and is not copied into compact inspection evidence.",
    "supersedes_decision_ref": "Optional exact emitted prior decision for the same subject that this decision supersedes.",
}


def _describe_schema_properties(schema: Mapping[str, Any]) -> None:
    """Populate every advertised property without borrowing another tool's semantics."""
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, property_schema in properties.items():
            if not isinstance(property_schema, dict):
                continue
            if not isinstance(property_schema.get("description"), str):
                # Reusing a description solely by property name is unsafe:
                # names such as ``mode`` and ``cursor`` have different
                # meanings in different operations.  Operation-specific
                # semantics belong at the schema declaration; this fallback
                # deliberately adds no semantics that the local schema does
                # not advertise.
                property_schema["description"] = (
                    "Bounded public field for this operation. Follow this property's "
                    "advertised type, enum, pattern, and required marker."
                )
            _describe_schema_properties(property_schema)
    items = schema.get("items")
    if isinstance(items, Mapping):
        _describe_schema_properties(items)
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, Mapping):
                    _describe_schema_properties(branch)


def _describe_contract_schemas(contracts: Mapping[str, Mapping[str, Any]]) -> None:
    """Make tools/list self-describing for both input and structured output."""
    for name, contract in contracts.items():
        tool_description = str(contract.get("description") or "Cortex V12 public operation.")
        # Make the boundary explicit at the exact point where the model sees
        # the tool contract. The schema remains authoritative: only properties
        # advertised by this operation may be supplied.
        contract_guidance = (
            " The request object itself is the canonical closed request for this operation. "
            "Send exactly the properties advertised by this input schema. The displayed "
            "properties and required markers are the complete caller contract; all other "
            "bookkeeping is internal to the server."
        )
        for key, kind in (("inputSchema", "request"), ("outputSchema", "successful structuredContent")):
            schema = contract.get(key)
            if not isinstance(schema, dict):
                continue
            schema.setdefault("$schema", _JSON_SCHEMA_DRAFT_2020_12)
            schema["description"] = f"{name} {kind}. {tool_description}{contract_guidance if key == 'inputSchema' else ''}"
            _describe_schema_properties(schema)


def _result_object(description: str, properties: Mapping[str, Any]) -> dict[str, Any]:
    """Describe stable nested fields without closing evolving ledger records."""
    return {"type": "object", "description": description, "properties": dict(properties)}


def _result_array(description: str, items: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "array", "description": description, "items": dict(items)}


def _opaque_task_id(description: str) -> dict[str, Any]:
    return _identifier(task=True, description=description + " Non-callable durable evidence only; public MCP calls use task_ref. " + HANDLE_COPY_RULE)


def _opaque_task_ref(description: str) -> dict[str, Any]:
    return _task_ref(description=description + " " + HANDLE_COPY_RULE)


def _opaque_record_id(description: str) -> dict[str, Any]:
    return _identifier(description=description + " Non-callable durable evidence only; public MCP calls use typed compact refs. " + HANDLE_COPY_RULE)


def _opaque_digest(description: str, *, nullable: bool = False) -> dict[str, Any]:
    value = _string(maximum=71, pattern=DIGEST_PATTERN, description=description + " " + HANDLE_COPY_RULE)
    if nullable:
        value["type"] = ["string", "null"]
    return value


_CHUNK_SCHEMA = _result_object("One complete immutable report chunk returned by a bounded report read.", {
    "chunk_index": {"type": "integer", "minimum": 0, "description": "Zero-based immutable chunk position."},
    "section": _string(maximum=REPORT_SECTION_MAX_LENGTH, pattern=REPORT_SECTION_PATTERN, description="Bounded lowercase report section label."),
    "content_digest": _opaque_digest("SHA-256 digest of this exact chunk content."),
    "content_bytes": {"type": "integer", "minimum": 0, "description": "UTF-8 JSON byte count for this chunk."},
    "content": {"description": "Complete JSON content for this chunk when it fits the selected read budget."},
})
_COMPACT_REPORT_SCHEMA = _result_object("Compact immutable report record; body content is available only from read_reports. For finalized or aborted reports, the record is terminal evidence, not a report-state continuation. A finalized result consumes its delegation's normal result slot.", {
    "report_id": _opaque_record_id("Opaque durable report handle."),
    "task_id": _opaque_task_id("Owning durable task handle."),
    "delegation_id": _opaque_record_id("Owning durable delegation handle."),
    "report_type": _string(enum=REPORT_TYPES, maximum=16, description="Immutable report kind."),
    "status": {"type": ["string", "null"], "enum": [*REPORT_STATUSES, None], "maxLength": 16, "description": "Finalized report status, or null while assembling."},
    "semantic_status": _string(enum=("pending", "semantic_valid", "semantic_invalid", "legacy"), maximum=32, description="Concrete canonical-content review state. semantic_valid is not inferred from finalization alone."),
    "storage_status": _string(enum=("storage_valid",), maximum=32, description="The immutable report was durably stored."),
    "coverage_diagnostics": _result_array("Concrete non-gating semantic/coverage diagnostics for coordinator rework reasoning.", _result_object("One concrete diagnostic.", {
        "code": _string(maximum=64), "message": _string(maximum=1_024),
    })),
    "content_digest": _opaque_digest("SHA-256 digest of the current immutable report manifest.", nullable=True),
    "assembly_state": _string(enum=("assembling", "finalized", "aborted"), maximum=16, description="Current immutable report assembly state."),
    "next_chunk_index": {"type": "integer", "minimum": 0, "description": "Next permitted append position only while assembly_state=assembling. On a finalized or aborted record this retained count is historical metadata, never a callable continuation."},
    "chunks": _result_array("Complete returned chunks within the requested bounded report page.", _CHUNK_SCHEMA),
})
_REPORT_CONSUMPTION_RECEIPT_SCHEMA = _result_object("Immutable structural evidence that this read returned identified report chunks to a classified caller; it is not evidence of free-text reasoning or native-worker lifecycle.", {
    "receipt_id": {"type": "integer", "minimum": 1, "description": "Durable receipt sequence within the private V12 shard."},
    "report_id": _opaque_record_id("Exact report read by this receipt."),
    "consumer_delegation_id": _string(maximum=IDENTIFIER_MAX_LENGTH, pattern=IDENTIFIER_PATTERN, description="Exact worker delegation that consumed the report, or null for a coordinator-classified read.") | {"type": ["string", "null"]},
    "reader_kind": _string(enum=("worker", "coordinator"), maximum=16, description="Classified reader; only worker receipts prove a declared downstream handoff read."),
    "observed_content_digest": _opaque_digest("Exact immutable report manifest digest observed for this read."),
    "chunk_indexes": _result_array("Exact chunk indexes returned in this page.", {"type": "integer", "minimum": 0}),
    "input_cursor": {"type": ["string", "null"], "maxLength": 2_048, "description": "Exact prior cursor, or null for the first page."},
    "output_cursor": {"type": ["string", "null"], "maxLength": 2_048, "description": "Exact returned continuation cursor, or null when this response has no further page."},
    "returned_content_bytes": {"type": "integer", "minimum": 0},
    "has_more": {"type": "boolean"},
    "created_sequence": {"type": "integer", "minimum": 0},
})
_HUMAN_VIEW_SCHEMA = _result_object("Volatile derived-view status; it never changes canonical ledger evidence. A ready view exposes one server-formatted Markdown link and never exposes its host-private filesystem path.", {
    "status": _string(enum=("ready", "stale", "conflict", "unavailable", "disabled"), maximum=16, description="Current derived-view availability state."),
    "markdown_link": {"type": "string", "description": "The sole user-renderable representation of a ready view. Copy this server-formatted exact Markdown link byte-for-byte; never reconstruct a path or link from compact refs."},
    "source_sequence": {"type": "integer", "minimum": 0, "description": "Timeline sequence used to verify a ready derived view."},
    "content_digest": _opaque_digest("SHA-256 digest of verified ready view content.", nullable=True),
})
_APPROVAL_VIEW_SCHEMA = _result_object("Exact server-verified plan-review view from a completed finalized plan read. Only status=ready with the returned opaque approval_handle can support a later plan approval. A ready view exposes one server-formatted Markdown link and never exposes its host-private filesystem path.", {
    "report_ref": _entity_ref("report"),
    "delegation_ref": _entity_ref("delegation"),
    "report_content_digest": _opaque_digest("Exact immutable plan report manifest required by the later record_plan_review call."),
    "status": _string(enum=("ready", "stale", "conflict", "unavailable", "disabled"), maximum=16, description="Only ready permits presenting the returned Markdown link for approval."),
    "markdown_link": {"type": "string", "description": "The sole user-renderable representation of a ready plan. Copy this server-formatted exact Markdown link byte-for-byte; never reconstruct a path or link from compact refs."},
    "source_sequence": {"type": ["integer", "null"], "minimum": 0, "description": "Exact ledger sequence used to verify the returned view, or null when not ready."},
    "content_digest": _opaque_digest("Exact verified derived-view digest when status is ready; otherwise null.", nullable=True),
    "approval_handle": _string(maximum=IDENTIFIER_MAX_LENGTH, pattern=IDENTIFIER_PATTERN, description="Server-issued opaque relation for this exact ready report/view/request snapshot; null unless status is ready.") | {"type": ["string", "null"]},
})
_DECISION_BINDING_SCHEMA = _result_object("Ready plan-approval relation returned for the later record_decision call. This is a convenience projection of the verified approval relation, not a new authority token.", {
    "task_ref": _opaque_task_ref("Exact anchored task locator."),
    "subject_type": _string(enum=("plan",), maximum=16),
    "subject_ref": _entity_ref("report"),
    "subject_digest": _opaque_digest("Exact immutable plan digest."),
    "approval_handle": _string(maximum=IDENTIFIER_MAX_LENGTH, pattern=IDENTIFIER_PATTERN),
    "approval_view_content_digest": _opaque_digest("Exact ready view digest."),
    "approval_view_source_sequence": {"type": "integer", "minimum": 0},
})
_HANDLES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "description": "Exact authoritative next-call values selected from this success result. " + HANDLE_COPY_RULE,
    "properties": {
        "task_ref": _opaque_task_ref("Exact preferred compact task locator."),
        "assignment_ref": _entity_ref("delegation", description="Exact assignment locator. It is distinct from task_ref and is accepted only by assignment-scoped operations."),
        "delegation_ref": _entity_ref("delegation"),
        "report_ref": _entity_ref("report"),
        "report_refs": _entity_ref_array("report", maximum=MAX_REPORT_IDS),
        "initiative_ref": _entity_ref("initiative"),
        "decision_ref": _entity_ref("decision"),
        "binding_ref": _string(minimum=35, maximum=35, pattern=r"^cb_[0-9a-f]{32}$", description="Exact family binding returned by an open operation. Copy it byte-for-byte into record_decision."),
        "cursor": _string(maximum=2_048, description="Exact opaque continuation cursor for read_reports only. " + HANDLE_COPY_RULE),
        "after_sequence": {"type": "integer", "minimum": 0, "description": "Exact inspection continuation input value. " + HANDLE_COPY_RULE},
        "human_view": _HUMAN_VIEW_SCHEMA,
        "approval_view": _APPROVAL_VIEW_SCHEMA,
        "decision_binding": _DECISION_BINDING_SCHEMA,
        "continuation_ref": _string(maximum=160, description="Exact worker-scoped continuation emitted after bootstrap consumption."),
    },
}
_RENDERER_SCHEMA = _closed({
    "version": _string(maximum=160, description="Exact packaged renderer version."),
    "profile_name": _string(maximum=ROLE_MAX_LENGTH, description="Exact selected packaged profile name."),
    "profile_state": _string(maximum=32, description="Renderer profile availability state."),
    "profile_digest": _opaque_digest("SHA-256 digest of the exact loaded packaged profile.", nullable=True),
    "common_policy_digest": _opaque_digest("SHA-256 digest of the common trusted renderer policy."),
}, ("version", "profile_name", "profile_state", "profile_digest", "common_policy_digest"), description="Closed packaged-profile renderer proof.")
_NATIVE_DISPATCH_SCHEMA = _closed({
    "assignment_ref": _entity_ref("delegation", description="Exact assignment bound to this server-issued native dispatch."),
    "dispatch_digest": _opaque_digest("Digest binding the exact assignment and native arguments. The host must reject any changed projection."),
    "native_arguments": _closed({
        "fork_turns": _string(enum=("none",), maximum=8, description="Server-issued no-history isolation mode."),
        "message": _string(maximum=TEXT_MAX_LENGTH, description="Complete server-rendered child bootstrap message. Pass byte-for-byte exactly once."),
        "task_name": _string(maximum=128, description="Safe server-issued native child task name."),
    }, ("fork_turns", "message", "task_name"), description="Closed native child arguments. Pass this object unchanged to the active host spawn operation; model and reasoning remain host/coordinator policy, not server defaults."),
}, ("assignment_ref", "dispatch_digest", "native_arguments"), description="Closed server-issued native dispatch projection bound to exactly one assignment. Any mutation, substitution, stale projection, or cross-assignment use must fail before spawn.")
_NATIVE_HOST_PROJECTION_SCHEMA = _closed({
    "fork_turns": _string(enum=("none",), maximum=8, description="Server-issued no-history isolation mode. Forward this projection unchanged to the active host spawn operation."),
    "message": _string(maximum=TEXT_MAX_LENGTH, description="Complete server-rendered child bootstrap message. Forward it byte-for-byte exactly once."),
    "task_name": _string(maximum=128, description="Safe server-issued native child task name. Forward it unchanged."),
}, ("fork_turns", "message", "task_name"), description="The sole literal host projection for this assignment. These three fields are the complete native spawn arguments; forward this object unchanged and do not reconstruct a message from semantic evidence. Assignment identity and the private binding digest are kept outside the host call.")
_DISPATCH_BRIEF_SCHEMA = _closed({
    "mode": _string(enum=("assignment_worker",), maximum=32, description="Server-issued child execution mode. Output-only: never caller input, user route activation, native identity, or lifecycle proof."),
    "task_name": _string(description="Stable task name for the active host to map to its spawn operation."),
    "task_ref": _opaque_task_ref("Exact task anchor paired with this assignment. This typed task reference, not delegation_ref, is the only task-read relation in the brief."),
    "semantic_objective": _string(description="Delegation objective preserved for host-neutral dispatch reasoning."),
    "recommended_model": _string(description="Server-owned output-only routing recommendation; active host availability remains authoritative. Never provide this field as caller input."),
    "recommended_reasoning_effort": _string(description="Server-owned output-only routing recommendation; active host effort values remain authoritative. Never provide this field as caller input."),
    "delegation_ref": _entity_ref("delegation", description="Exact delegation anchor for this brief."),
    "project_root": _string(maximum=PROJECT_ROOT_MAX_LENGTH, description="Saved project-root context for the dispatched worker."),
    "profile_proof": _RENDERER_SCHEMA,
    "dispatch_correlation_marker": _string(pattern=r"^dc_[0-9a-f]{32}$", maximum=35, description="Server-issued observational marker embedded unchanged in the trusted worker brief. It is non-authorizing evidence, never an MCP call input or host capability."),
    "dispatch_correlation_fingerprint": _opaque_digest("One-way fingerprint for observational event correlation; it cannot authorize dispatch or continuation."),
    "native_dispatch": _NATIVE_DISPATCH_SCHEMA,
    "publication_next_action": _closed({
        "operation": _string(enum=("publish_plan", "publish_result", "publish_documentation"), maximum=32),
        "assignment_ref": _entity_ref("delegation", description="Exact assignment anchor for the named publication operation."),
        "task_ref": _opaque_task_ref("Exact task anchor for task reads associated with this assignment; never substitute assignment_ref."),
        "coverage_source": _string(enum=("planning_items", "assigned_items"), maximum=32, description="The exact server-issued effective-contract item collection to cover in the advertised publication evidence."),
    }, ("operation", "assignment_ref", "task_ref", "coverage_source"), description="Closed server-owned publication continuation for this exact assignment. The named operation's advertised evidence schema is authoritative; this object provides only exact typed relations and never a host command."),
}, ("mode", "task_name", "task_ref", "semantic_objective", "recommended_model", "recommended_reasoning_effort", "delegation_ref", "project_root", "profile_proof", "dispatch_correlation_marker", "dispatch_correlation_fingerprint", "native_dispatch", "publication_next_action"), description="Closed server-issued assignment brief. The nested native_dispatch is the sole host spawn projection; semantic fields are durable evidence and must not be mapped into a reconstructed host call.")
_WORKER_BRIEF_SCHEMA = _result_object("Coordinator-owned recovery brief and renderer proof; it creates no host lifecycle record or dispatch handle.", {
    "delegation_id": _opaque_record_id("Durable delegation handle for this worker brief."),
    "task_id": _opaque_task_id("Durable task handle for this worker brief."),
    "native_task_name": _string(description="Persisted stable task name for host reconciliation; it is not a lifecycle receipt."),
    "input_report_refs": _result_array("Exact finalized predecessor report references that the worker must read through read_reports before use.", _result_object("One immutable handoff report reference.", {
        "report_id": _opaque_record_id("Exact predecessor report ID."),
        "content_digest": _opaque_digest("Exact finalized predecessor report manifest digest."),
        "assembly_state": _string(enum=("finalized",), maximum=16),
    })),
    "renderer": _RENDERER_SCHEMA,
    "effective_contract": _result_object("Bounded effective-contract projection. assigned_items are this delegation's delivery assignments; a planner additionally receives planning_items, the exact full current contract token catalogue it must map once in a v3 plan before finalization.", {"revision": {"type": "integer", "minimum": 1}, "assigned_items": {"type": "array"}, "planning_items": {"type": "array", "description": "Planner-only full current requirement, constraint, acceptance, and derived-verification token catalogue for exact plan mapping."}, "decisions": {"type": "array"}}),
    "dispatch_brief": _DISPATCH_BRIEF_SCHEMA,
})

_RESULT_SLOT_SCHEMA = _result_object("Terminal normal-result slot receipt for one delegation. state=consumed means this delegation must not begin another normal result report. Corrected or replacement result evidence requires a distinct recovery/rework delegation; this receipt does not authorize a second report.", {
    "state": _string(enum=("consumed",), maximum=16, description="A finalized normal result has consumed this delegation's one normal-result slot."),
    "report_ref": _entity_ref("report", description="Exact finalized immutable result evidence."),
    "semantic_status": _string(enum=("semantic_valid", "semantic_invalid", "legacy"), maximum=32, description="Concrete review state for the consumed result evidence."),
    "coverage_diagnostics": _result_array("Concrete diagnostics from finalization; use them to scope one recovery/rework delegation when correction is needed.", _result_object("One concrete semantic or coverage diagnostic.", {
        "code": _string(maximum=64), "message": _string(maximum=1_024),
    })),
    "replacement_requirement": _string(enum=("distinct_recovery_or_rework_delegation",), maximum=64, description="Any corrected or replacement result belongs to a distinct recovery/rework delegation, never another result begin on this delegation."),
})


_RESULT_PROPERTY_SCHEMAS: dict[str, dict[str, Any]] = {
    "effective_contract": _result_object("Current revisioned effective task outcome contract. Its emitted item_ref values are the only permissible tokens for optional create_delegation.outcome_assignments; copy them byte-for-byte, and otherwise omit that property.", {"revision": {"type": "integer", "minimum": 1}, "items": _result_array("Current stable outcome items.", _result_object("Outcome item.", {"item_ref": _string(pattern=r"^o_[0-9a-f]{12}$", maximum=14), "category": _string(enum=("requirement", "constraint", "acceptance", "verification"), maximum=16), "ordinal": {"type": "integer", "minimum": 0}, "text": _string(), "created_revision": {"type": "integer", "minimum": 1}}))}),
    "aggregate_coverage": _result_object("Advisory aggregate effective-contract coverage; it never gates safe work.", {"status": _string(enum=("ready", "ready_with_risks", "rework"), maximum=24), "items": _result_array("Per-item aggregate evidence.", _result_object("Coverage item.", {"item_ref": _string(pattern=r"^o_[0-9a-f]{12}$", maximum=14), "status": _string(enum=("complete", "missing", "partial", "unverified", "stale", "contradictory"), maximum=16), "reason": _string(maximum=64), "report_refs": _result_array("Current-owner report refs.", _string(pattern=r"^r_[0-9a-f]{12}$", maximum=14)), "superseded_report_refs": _result_array("Historical or superseded report refs that do not affect current coverage.", _string(pattern=r"^r_[0-9a-f]{12}$", maximum=14))}))}),
    "conformance_review": _result_object("Advisory effective-contract conformance evidence for rework and closure reasoning; it is not a backend gate.", {"effective_revision": {"type": "integer", "minimum": 1}, "status": _string(enum=("ready", "ready_with_risks", "not_ready"), maximum=24), "recommendation": _string(enum=("ready", "ready_with_risks", "rework"), maximum=24), "decision_refs": _result_array("Relevant steering and approval decisions.", _string(pattern=r"^u_[0-9a-f]{12}$", maximum=14)), "aggregate_coverage": {"type": "object"}, "report_manifests": _result_array("Finalized report manifest bindings.", _result_object("Report manifest.", {"report_ref": _string(pattern=r"^r_[0-9a-f]{12}$", maximum=14), "content_digest": _opaque_digest("Immutable finalized report manifest digest.")})), "consumed_report_digests": _result_array("Coordinator-consumed immutable report digests.", _opaque_digest("Consumed report manifest digest."))}),
    "task": _result_object("Durable task header, including canonical V12 project anchor and immutable task contract.", {
        "task_id": _opaque_task_id("Opaque durable task handle."),
        "project_hash": _string(maximum=64, pattern=r"^[0-9a-f]{64}$", description="V12 project-shard digest; it is not caller authority."),
    }),
    "execution_outcome": _result_object("Neutral report diagnostics plus deterministic current-coverage outcome, independent of advisory closure bookkeeping and report arrival order.", {
        "evidence_status": _string(enum=("finalized_reports_present", "no_finalized_reports"), maximum=32),
        "finalized_report_count": {"type": "integer", "minimum": 0},
        "completed_report_count": {"type": "integer", "minimum": 0},
        "effective_revision": {"type": "integer", "minimum": 1},
        "coverage_status": _string(enum=("ready", "ready_with_risks", "rework"), maximum=24),
        "outcome": _string(enum=("completed", "incomplete"), maximum=16),
    }),
    "advisory_closure": _result_object("Task-relevant advisory closure-record projection. It never changes the user-work outcome.", {
        "record_status": _string(enum=("not_recorded", "recorded"), maximum=16),
        "latest_record": {"type": ["object", "null"]},
    }),
    "closure_confirmation": _result_object("Automatic advisory bookkeeping after a closure attempt. Confirmation means the intended record was found by bounded inspection; it never changes execution outcome.", {
        "inspection_status": _string(enum=("confirmed", "unconfirmed"), maximum=16),
        "reason": _string(enum=("record_inspected", "persistence_unavailable", "inspection_unavailable", "record_not_observed"), maximum=32),
        "attempts": {"type": "integer", "minimum": 1, "maximum": 2},
    }),
    "delegations": _result_array("Compact delegation references selected by the returned task chronology page.", _result_object("Compact durable delegation reference.", {
        "delegation_id": _opaque_record_id("Opaque durable delegation handle."), "task_id": _opaque_task_id("Owning task handle."), "native_task_name": _string(description="Persisted stable task name; it is not a lifecycle receipt."),
    })),
    "delegation": _result_object("Durable delegation record; full instructions are returned only for the selected delegation.", {
        "delegation_id": _opaque_record_id("Opaque durable delegation handle."), "task_id": _opaque_task_id("Owning task handle."),
        "native_task_name": _string(description="Persisted stable task name; it is not a lifecycle receipt."),
        "profile_name": _string(maximum=ROLE_MAX_LENGTH, description="Exact packaged advisory profile distinct from role."),
        "model": _string(maximum=ROLE_MAX_LENGTH, description="Coordinator model recommendation; it is not host capability evidence."),
        "reasoning_effort": _string(maximum=ROLE_MAX_LENGTH, description="Coordinator effort recommendation; it is not host capability evidence."),
    }),
    "worker_brief": _WORKER_BRIEF_SCHEMA,
    "renderer": _RENDERER_SCHEMA,
    "dispatch_brief": _DISPATCH_BRIEF_SCHEMA,
    "report": _COMPACT_REPORT_SCHEMA,
    "result_slot": _RESULT_SLOT_SCHEMA,
    "reports": _result_array("Requested report records in caller order; report chunks and bodies appear only in read_reports.", _COMPACT_REPORT_SCHEMA),
    "consumption_receipts": _result_array("Structural report-read receipts created by this call.", _REPORT_CONSUMPTION_RECEIPT_SCHEMA),
    "decisions": _result_array("Compact user-decision evidence selected by the returned task chronology page.", _result_object("Compact immutable decision record.", {
        "decision_id": _opaque_record_id("Opaque durable decision handle."), "task_id": _opaque_task_id("Owning task handle."),
        "subject_id": _opaque_record_id("Opaque selected durable subject handle."), "subject_digest": _opaque_digest("Exact immutable subject digest.", nullable=True),
    })),
    "assessment": _result_object("New advisory governance assessment; it never blocks another operation.", {
        "assessment_id": _opaque_record_id("Opaque durable assessment handle."), "task_id": _opaque_task_id("Anchored task handle."),
    }),
    "continuations": _result_array("Compact persisted delegation recovery status. It is ledger evidence only: dispatch_state remains ledger_unknown and native commentary alone cannot advance a durable successor. Recovery must end in a finalized report, explicit blocked/partial handoff, or a parent-linked replacement.", _result_object("One recoverable delegation status.", {
        "delegation": _result_object("Exact durable delegation selected for recovery.", {
            "delegation_id": _opaque_record_id("Opaque durable delegation handle."),
            "task_id": _opaque_task_id("Owning task handle."),
            "native_task_name": _string(),
            "profile_name": _string(maximum=ROLE_MAX_LENGTH),
            "model": _string(maximum=ROLE_MAX_LENGTH),
            "reasoning_effort": _string(maximum=ROLE_MAX_LENGTH),
        }),
        "dispatch_state": _string(enum=("ledger_unknown",), maximum=32),
        "handoff_state": _string(enum=("report_required", "report_assembling", "report_finalized", "explicit_handoff"), maximum=32),
        "reports": _result_array("Immutable report evidence currently owned by this delegation.", _COMPACT_REPORT_SCHEMA),
        "recovery_requirement": _string(enum=("finalized_report_or_explicit_handoff_or_parent_linked_replacement",), maximum=96),
        "continuation_sequence": {"type": "integer", "minimum": 0},
    })),
    "assessments": _result_array("Advisory governance assessments selected by the returned chronology page.", _result_object("Advisory governance assessment record.", {
        "assessment_id": _opaque_record_id("Opaque durable assessment handle."), "task_id": _opaque_task_id("Anchored task handle."),
    })),
    "initiative": _result_object("Created or revised immutable-current initiative projection.", {
        "initiative_id": _opaque_record_id("Opaque durable initiative handle."),
    }),
    "initiatives": _result_array("Initiatives related to the selected task or selected initiative.", _result_object("Current initiative projection.", {
        "initiative_id": _opaque_record_id("Opaque durable initiative handle."),
    })),
    "closures": _result_array("Advisory governance closures selected by the returned chronology page.", _result_object("Advisory task or initiative closure record.", {
        "closure_id": _opaque_record_id("Opaque durable closure handle."), "task_id": _opaque_task_id("Anchored task handle."),
        "subject_id": _opaque_record_id("Closed durable subject handle."),
    })),
    "closure": _result_object("Recorded advisory task or initiative closure. It is a recommendation record, not an execution lifecycle receipt.", {
        "closure_id": _opaque_record_id("Opaque durable closure handle."), "task_id": _opaque_task_id("Anchored task handle."),
        "subject_id": _opaque_record_id("Closed durable subject handle."),
    }),
    "next_action": _result_object("Optional advisory closure follow-up. advisory_status is present only when a closure record exists; an exhausted persistence failure with closure=null must omit it. After an initiative closure, this object may offer a compact suggested subject for a separate task closure; it is not a complete callable payload and never requires that closure or orders safe coordination.", {
        "tool": _string(enum=("submit_governance_closure",), maximum=32),
        "advisory_status": _string(enum=("recorded",), maximum=16, description="Present only when the returned closure is non-null and recorded."),
        "task_ref": _opaque_task_ref("Exact anchored task reference echoed after closure or offered for an optional task closure."),
        "suggested_subject": _result_object("Exact compact suggested task-closure subject relation, not complete submit_governance_closure arguments.", {
            "task_ref": _opaque_task_ref("Exact anchored task reference."),
            "subject_type": _string(enum=("task",), maximum=16),
            "subject_ref": _opaque_task_ref("Exact task subject reference."),
        }),
    }),
    "decision": _result_object("Compact immutable user-decision receipt; the original response is not repeated.", {
        "decision_id": _opaque_record_id("Opaque durable decision handle."), "task_id": _opaque_task_id("Owning task handle."),
        "subject_digest": _opaque_digest("Exact immutable subject digest.", nullable=True),
    }),
    "warnings": _result_array("Advisory unresolved or cyclic initiative-link warnings; these do not gate work.", {"description": "One advisory warning value."}),
    "links": _result_array("Current initiative links within the selected governance scope.", _result_object("Current initiative relationship.", {
        "initiative_id": _opaque_record_id("Source initiative handle."), "target_id": _opaque_record_id("Related durable target handle."),
    })),
    "initiative_revisions": _result_array("Immutable initiative revisions whose timeline events are in this page.", _result_object("Immutable initiative revision event.", {
        "initiative_id": _opaque_record_id("Revised initiative handle."), "sequence": {"type": "integer", "minimum": 0, "description": "Immutable revision sequence."},
    })),
    "projection": _result_object("Effective advisory governance projection retaining latest model, user override, and closure evidence.", {
        "effective_mode": {"type": ["string", "null"], "enum": [*GOVERNANCE_MODES, None], "description": "Current effective advisory governance mode, or null when no assessment exists."},
        "override_active": {"type": "boolean", "description": "Whether a user override currently supersedes model assessment evidence."},
    }),
    "timeline": _result_array("Chronological cursor-scoped durable event metadata.", _result_object("One immutable timeline event.", {
        "sequence": {"type": "integer", "minimum": 0, "description": "Opaque chronological sequence cursor; retain it exactly for the next page."},
        "entity_id": _opaque_record_id("Affected durable record handle."),
        "task_id": _opaque_task_id("Owning task handle."),
    })),
    "next_sequence": {"type": "integer", "minimum": 0, "description": "Next chronology cursor. Copy it unchanged into after_sequence for the next bounded page."},
    "has_more": {"type": "boolean", "description": "Whether another chronology or report-content page is available for the same scope."},
    "returned_content_bytes": {"type": "integer", "minimum": 0, "description": "Encoded byte count of report body content returned in this response."},
    "next_cursor": {"type": ["string", "null"], "minLength": 1, "maxLength": 2_048, "description": "Opaque read_reports continuation cursor, or null when complete. Copy it byte-for-byte for the identical requested report scope."},
    "replayed": {"type": "boolean", "description": "Whether this successful command returned its already-persisted result."},
    # Legacy storage-contract construction still consumes these private
    # receipt schemas before the semantic catalogue projection replaces that
    # surface. They must never appear in the final 15-tool output schemas.
    "idempotency_key": _idempotency_key(),
    "retry_handle": _idempotency_key(),
    "human_view": _HUMAN_VIEW_SCHEMA,
    "approval_view": _APPROVAL_VIEW_SCHEMA,
    "handles": _HANDLES_SCHEMA,
    "assembly_state": {"type": "string", "enum": ["assembling", "finalized", "aborted"], "description": "Current immutable report assembly state after this mutation. finalized and aborted are terminal: stop assembly and use the report only as immutable evidence."},
    "next_chunk_index": {"type": "integer", "minimum": 0, "description": "Next permitted append index only for an assembling report. Begin and append receipts identify append as the next semantic operation, but the caller must issue a fresh schema-complete request with explicit mode=append; this value never supplies that phase. It is not emitted as a handle after finalization or abort."},
    "accepted_chunk_index": {"type": "integer", "minimum": 0, "description": "Append index accepted by this report receipt."},
    "chunk_digest": _opaque_digest("SHA-256 digest of the accepted append chunk."),
    "chunk_bytes": {"type": "integer", "minimum": 0, "description": "Encoded byte length of the accepted append chunk."},
    "expected_chunk_count": {"type": "integer", "minimum": 1, "description": "Output-only immutable receipt count after the accepted append; Cortex computes finalization transactionally and this value is never a callable input."},
    "expected_content_digest": _opaque_digest("Output-only immutable manifest receipt after the accepted append; Cortex computes finalization transactionally and this value is never a callable input."),
}


def _tool_output_schema(
    *required_success_fields: str,
    optional_success_fields: tuple[str, ...] = (),
    success_property_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Describe the structured success receipt for one public tool.

    Correctable failures deliberately use only actionable MCP ``TextContent``
    with ``isError: true``.  They therefore are not a second pseudo-success
    structured shape and do not need to satisfy this success schema.
    """
    return {
        "$schema": _JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "description": "Closed successful Cortex V12 structuredContent receipt. " + HANDLE_COPY_RULE + " Correctable failures use MCP TextContent and do not match this schema.",
        "additionalProperties": False,
        "properties": {
            field: dict((success_property_overrides or {}).get(field, _RESULT_PROPERTY_SCHEMAS[field]))
            for field in dict.fromkeys((*required_success_fields, *optional_success_fields, "idempotency_key", "retry_handle", "handles"))
        },
        "required": [*required_success_fields, "handles"],
    }


def _forbid_properties(*names: str) -> list[dict[str, Any]]:
    """Return portable draft-2020-12 guards for mutually exclusive inputs."""
    return [{"not": {"required": [name]}} for name in names]


def _report_operation_schema() -> list[dict[str, Any]]:
    """Describe the complete report upload state machine before storage opens.

    The root schema still lists every known property so its closed-object
    boundary remains one source of truth.  These alternatives make a caller
    select exactly one valid operation and reject fields belonging to another
    operation at the public MCP boundary.
    """
    common = ("delegation_ref", "idempotency_key")

    def create_shape(*, mode: str, report_types: tuple[str, ...], required: tuple[str, ...], forbidden: tuple[str, ...]) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "report_type": {
                "enum": list(report_types),
                "description": _PROPERTY_DESCRIPTIONS["report_type"],
            },
        }
        requirements = [*common, *required]
        guards = _forbid_properties(*forbidden)
        properties["mode"] = {"const": mode, "description": _PROPERTY_DESCRIPTIONS["mode"]}
        requirements.append("mode")
        return {
            "type": "object",
            "properties": properties,
            "required": requirements,
            "allOf": guards,
        }

    plan_fields = ("review_policy", "supersedes_report_ref")
    create_tail = ("section", "abort_reason_en")
    alternatives: list[dict[str, Any]] = []
    for report_types, forbidden in (
        (("plan",), ("content", "status", *create_tail)),
        (("progress", "result", "synthesis"), ("content", "status", *create_tail, *plan_fields)),
    ):
        alternatives.append(create_shape(mode="begin", report_types=report_types, required=("report_type",), forbidden=forbidden))
    alternatives.extend((
        {
            "type": "object",
            "properties": {"mode": {"const": "append", "description": _PROPERTY_DESCRIPTIONS["mode"]}},
            "required": [*common, "mode", "report_ref", "section", "content"],
            "allOf": _forbid_properties("report_type", "status", "abort_reason_en", *plan_fields),
        },
        {
            "type": "object",
            "properties": {"mode": {"const": "finalize", "description": _PROPERTY_DESCRIPTIONS["mode"]}},
            "required": [*common, "mode", "report_ref", "status"],
            "allOf": _forbid_properties("report_type", "content", "section", "abort_reason_en", *plan_fields),
        },
        {
            "type": "object",
            "properties": {"mode": {"const": "abort", "description": _PROPERTY_DESCRIPTIONS["mode"]}},
            "required": [*common, "mode", "report_ref", "abort_reason_en"],
            "allOf": _forbid_properties("report_type", "status", "content", "section", *plan_fields),
        },
    ))
    return alternatives


def build_public_contracts() -> dict[str, dict[str, Any]]:
    """Return the one authoritative V12 MCP catalogue for all callers."""
    task_ref = _task_ref(description="Preferred compact task locator emitted by open_task. Copy handles.task_ref byte-for-byte for task-anchored calls; never use a UI-rendered task_id.")
    delegation_id = _entity_ref("delegation")
    report_id = _entity_ref("report")
    initiative_id = _entity_ref("initiative")
    decision_id = _entity_ref("decision")
    idempotency_key = _idempotency_key()
    profile_names = packaged_profile_names()
    if (
        len(profile_names) != _PACKAGED_PROFILE_COUNT
        or len(set(profile_names)) != _PACKAGED_PROFILE_COUNT
        or tuple(sorted(profile_names)) != profile_names
    ):
        raise RuntimeError("Cortex v12 packaged profile catalogue is unavailable")

    contracts: dict[str, dict[str, Any]] = {
        "create_task": {
            "description": "First call requires non-empty requirements, constraints, and acceptance_criteria arrays. Cortex deterministically derives the complete non-empty persisted verification plan from acceptance_criteria before storage. Initialize exactly one durable task for this orchestration. After any successful result, including replayed=true, reuse the exact returned task_ref for later task-anchored calls and never call create_task again for this orchestration. Replay is only for an explicitly ambiguous transport outcome: retry the exact original payload with the same idempotency_key; do not blindly repeat a successful call. project_root is required only in this first call and must be the user-selected absolute canonical cwd/webhook root; Cortex stores the resolved root on the task. Omit project_root from every later public tool call. Internal contract prose is English, while user_request_original stays exact and user_language is a BCP-47 tag such as ru. No undeclared wrapper, alias, or extra field is accepted.",
            "inputSchema": _closed(
                {
                    "requirements": _text_array(minimum=1),
                    "constraints": _text_array(minimum=1),
                    "acceptance_criteria": _text_array(minimum=1),
                    "project_root": _string(maximum=PROJECT_ROOT_MAX_LENGTH, description=_PROPERTY_DESCRIPTIONS["project_root"]),
                    "objective": _string(description=_PROPERTY_DESCRIPTIONS["objective"]),
                    "user_request_original": _string(description=_PROPERTY_DESCRIPTIONS["user_request_original"]),
                    "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description=_PROPERTY_DESCRIPTIONS["user_language"]),
                    "idempotency_key": idempotency_key,
                    "context": _json_value(),
                },
                ("project_root", "objective", "user_request_original", "user_language", "requirements", "constraints", "acceptance_criteria"),
            ),
            "outputSchema": _tool_output_schema(
                "task",
                "replayed",
                optional_success_fields=("human_view",),
                success_property_overrides={
                    "task": _RESULT_PROPERTY_SCHEMAS["task"] | {
                        "description": "Created or exact-replayed durable task header. After this successful receipt, reuse handles.task_ref for this orchestration and never call create_task again. This receipt does not expose outcome-assignment tokens: normally omit create_delegation.outcome_assignments; if needed, first read inspect_task.effective_contract.items and copy only its emitted item_ref values byte-for-byte.",
                    },
                    "replayed": _RESULT_PROPERTY_SCHEMAS["replayed"] | {
                        "description": "True only when an explicitly ambiguous transport outcome was retried with the exact original payload and idempotency_key. A successful receipt, whether true or false, means reuse handles.task_ref and do not call create_task again for this orchestration.",
                    },
                },
            ),
        },
        "inspect_task": {
            "description": "Read one task header, neutral finalized-worker-report evidence, a separate task-relevant advisory closure record, and a bounded task-scoped chronology using task_ref. Neither evidence nor advisory record proves native lifecycle or authorizes a duplicate spawn.",
            "inputSchema": _closed({"task_ref": task_ref, "after_sequence": {"type": "integer", "minimum": 0}}, ("task_ref",)),
            "outputSchema": _tool_output_schema("task", "effective_contract", "aggregate_coverage", "conformance_review", "execution_outcome", "advisory_closure", "delegations", "continuations", "reports", "decisions", "consumption_receipts", "timeline", "next_sequence", "has_more", optional_success_fields=("human_view",)),
        },
        "create_delegation": {
            "description": "Create one new model-authored delegation and return one host-neutral dispatch brief plus loaded renderer proof. Normally omit the optional outcome_assignments property. Supply it only when inspect_task.effective_contract.items has returned the exact current item_ref tokens; copy those tokens byte-for-byte and never substitute human prose, inferred labels, or reconstructed values. Without an exact server-returned token, omit the property. The brief carries the rendered message, stable task name, semantic objective, recommended model/effort, delegation anchor, project context, and profile proof. The coordinator maps it to the active host spawn schema; it is never a byte-exact host argument object or lifecycle receipt. read_delegation retains the verbose recovery brief for ledger reconciliation only.",
            "inputSchema": _closed(
                {
                    "task_ref": task_ref,
                    "objective": _string(),
                    "role": _string(maximum=ROLE_MAX_LENGTH),
                    "profile_name": _string(enum=profile_names, maximum=ROLE_MAX_LENGTH, description="Exact packaged profile. This is distinct from role and must load before a worker brief is returned."),
                    "scope": _string(description="Required concise textual boundary of the delegated worker's ownership."),
                    "instructions": _string(description=_PROPERTY_DESCRIPTIONS["instructions"]),
                    "parent_delegation_ref": delegation_id,
                    "input_report_refs": _entity_ref_array("report", maximum=MAX_REPORT_IDS) | {"description": "Optional unique predecessor report refs. Every value must be copied byte-for-byte from an emitted handle."},
                    "input_decision_refs": _entity_ref_array("decision", maximum=MAX_DECISION_IDS) | {"description": "Optional unique predecessor decision refs. Every value must be copied byte-for-byte from an emitted handle."},
                    "outcome_assignments": _closed({"owned": {"type": "array", "uniqueItems": True, "items": _string(pattern=r"^o_[0-9a-f]{12}$", maximum=14, description="Exact current item_ref copied from inspect_task.effective_contract.items; never item text.")}, "contributing": {"type": "array", "uniqueItems": True, "items": _string(pattern=r"^o_[0-9a-f]{12}$", maximum=14, description="Exact current item_ref copied from inspect_task.effective_contract.items; never item text.")}, "evidence_producing": {"type": "array", "uniqueItems": True, "items": _string(pattern=r"^o_[0-9a-f]{12}$", maximum=14, description="Exact current item_ref copied from inspect_task.effective_contract.items; never item text.")}}, ()) | {"description": _PROPERTY_DESCRIPTIONS["outcome_assignments"]},
                    "model": _string(maximum=ROLE_MAX_LENGTH, description="Coordinator-recommended model; host availability is resolved only by the active Codex integration."),
                    "reasoning_effort": _string(maximum=ROLE_MAX_LENGTH, description="Coordinator-recommended effort; host support is resolved only by the active Codex integration."),
                    "idempotency_key": idempotency_key,
                },
                ("task_ref", "objective", "role", "profile_name", "scope", "instructions", "model", "reasoning_effort"),
            ),
            "outputSchema": _tool_output_schema("delegation", "dispatch_brief", "renderer", "replayed", optional_success_fields=("human_view",)),
        },
        "read_delegation": {
            "description": "Retrieve a read-only durable delegation, its trusted worker dispatch brief, and bounded delegation-scoped chronology with its exact emitted delegation_ref.",
            "inputSchema": _closed({"delegation_ref": delegation_id, "after_sequence": {"type": "integer", "minimum": 0}}, ("delegation_ref",)),
            "outputSchema": _tool_output_schema("delegation", "worker_brief", "reports", "consumption_receipts", "timeline", "next_sequence", "has_more", optional_success_fields=("human_view",)),
        },
        "submit_report": {
            "description": "Create, assemble, finalize, or abort one immutable bounded report. Phase map: begin selects report_type; append carries one chunk; finalize carries report_ref and status; abort carries its reason. Cortex atomically assigns chunk order and computes the final immutable manifest, so callers never supply an index, count, or digest. The plan-revision relation (review_policy and supersedes_report_ref) is legal only on a fresh begin with report_type=plan; omit both from every append, finalize, and abort request, even when continuing a plan revision. Before beginning a result report for a delegation with declared input evidence, consume every declared finalized input through read_reports using that delegation. A successful finalized result receipt includes result_slot.state=consumed: the delegation's normal result slot is spent even when semantic diagnostics recommend correction; create a distinct recovery/rework delegation before submitting any replacement result. The advertised flat object is the complete field catalogue; every call, including a continuation, must explicitly select mode. Returned report_ref never encodes, infers, or replaces that required phase; every next operation is a fresh schema-complete request. begin requires delegation_ref, mode, report_type, and idempotency_key and forbids content/status/assembly fields; its next semantic operation is append with a new explicit mode=append request. append requires delegation_ref, mode, report_ref, section, content, and idempotency_key; its next semantic operation is append again or finalize, each with its own explicit mode. finalize requires delegation_ref, mode, report_ref, status, and idempotency_key. abort requires delegation_ref, mode, report_ref, abort_reason_en, and idempotency_key. Fields belonging to another mode are invalid. New reports always use begin, sequential append, then finalize (or abort). A successful finalized or aborted receipt is terminal: stop assembly immediately; never append, finalize, or abort that manifest again, and use only its finalized immutable report evidence. The worker alone calls this with its exact emitted delegation_ref, which resolves the authoritative task; never supply task_ref or a canonical ID. Append content is one JSON value; canonical semantic envelopes use cortex/report/{progress,result,synthesis,plan}/v1 and may contain one optional unchanged source_text value inside that content object, with no language tag or translated/original duplicate fields. Storage-valid legacy or semantic-invalid content remains immutable evidence; only a finalized completed semantic-valid canonical plan can receive a ready approval relation.",
            "inputSchema": _closed(
                {
                    "delegation_ref": delegation_id,
                    "mode": _string(enum=REPORT_MODES, maximum=16, description=_PROPERTY_DESCRIPTIONS["mode"]),
                    "report_type": _string(enum=REPORT_TYPES, maximum=16, description=_PROPERTY_DESCRIPTIONS["report_type"]),
                    "status": _string(enum=REPORT_STATUSES, maximum=16),
                    "content": _json_value(),
                    "report_ref": report_id,
                    "section": _string(maximum=REPORT_SECTION_MAX_LENGTH, pattern=REPORT_SECTION_PATTERN, description="Bounded lowercase report section label."),
                    "abort_reason_en": _string(maximum=4_096),
                    "supersedes_report_ref": report_id | {"description": "Optional exact prior plan reference for a plan revision. Legal only with mode=begin and report_type=plan. Omit it from append, finalize, and abort; a later phase never carries, repeats, or updates this relation."},
                    "review_policy": _string(enum=PLAN_REVIEW_POLICIES, maximum=16, description="Optional review policy for a fresh plan begin only. Legal only with mode=begin and report_type=plan; omit it from append, finalize, and abort."),
                    "idempotency_key": idempotency_key,
                },
                ("delegation_ref", "mode"),
            ),
            "outputSchema": _tool_output_schema("report", "replayed", optional_success_fields=("assembly_state", "next_chunk_index", "accepted_chunk_index", "chunk_digest", "chunk_bytes", "expected_chunk_count", "expected_content_digest", "result_slot", "human_view", "approval_view")),
        },
        "read_reports": {
            "description": "Read immutable report metadata in requested order, or one server-bounded body page for a declared consuming worker. report_refs is always required and must contain at least one exact server-returned report_ref; never call with an empty selection. Omit consumer_delegation_ref for metadata-only behavior. Supply the exact declared consumer_delegation_ref for a body read; Cortex then returns one fixed safe page and, when more remains, one opaque cursor for the next identical selection. The public operation has no caller-selected byte budget. A body read requires only exact finalized server-returned report_ref values that the delegation declared in input_report_refs; each returned body page records a worker-consumption receipt.",
            "inputSchema": _closed({
                "report_refs": _entity_ref_array("report", minimum=1, maximum=MAX_REPORT_IDS) | {"description": "Required non-empty ordered report selection. Use only exact server-returned report_ref values; never pass an empty array, human text, inferred values, or reconstructed references. Body reads require finalized reports declared by consumer_delegation_ref; metadata-only reads may inspect the selected report state."},
                "sections": {"type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": True, "items": _string(maximum=REPORT_SECTION_MAX_LENGTH, pattern=REPORT_SECTION_PATTERN), "description": "Optional non-empty body-section filter. Omit it entirely for metadata-only reads and when no section filter is needed; never pass an empty array as a placeholder."},
                "cursor": _string(maximum=2_048),
                    "consumer_delegation_ref": delegation_id,
                }, ("report_refs",)),
            "outputSchema": _tool_output_schema("reports", "returned_content_bytes", "next_cursor", "has_more", "consumption_receipts", optional_success_fields=("human_view", "approval_view")),
        },
        "set_governance_mode": {
            "description": "Append an advisory governance assessment. Mode, planning, readiness, documentation evidence, and closure inform coordinator reasoning but never block another safe ledger operation.",
            "inputSchema": _closed(
                {
                    "task_ref": task_ref,
                    "mode": _string(enum=GOVERNANCE_MODES, maximum=16),
                    "rationale": _string(minimum=0),
                    "risk_factors": {"type": "array", "items": _string(), "maxItems": MAX_LINKS},
                    "source": _string(enum=GOVERNANCE_SOURCES, maximum=32),
                    "initiative_ref": initiative_id,
                    "idempotency_key": idempotency_key,
                },
                ("task_ref", "mode"),
            ),
            "outputSchema": _tool_output_schema("assessment", "replayed"),
        },
        "record_initiative": {
            "description": "Create an advisory initiative only for cross-task or long-lived governance. goal is required only for creation. An update with initiative_ref is admitted only for a material goal, dependency graph, risk, status, parent, or cross-task change; ordinary delegation stage/rework/proposed-next-step, report, decision, and notes churn belongs in the durable task timeline and must not create an initiative revision.",
            "inputSchema": _closed(
                {"task_ref": task_ref, "initiative_ref": initiative_id, "goal": _string(), "parent_initiative_ref": initiative_id, "risk": _string(minimum=0), "status": _string(enum=INITIATIVE_STATUSES, maximum=16), "dependency_refs": _entity_ref_array("initiative", maximum=MAX_LINKS), "linked_task_refs": {"type": "array", "minItems": 0, "maxItems": MAX_LINKS, "uniqueItems": True, "items": _opaque_task_ref("Exact compact related task locator.")}, "linked_delegation_refs": _entity_ref_array("delegation", maximum=MAX_LINKS), "linked_report_refs": _entity_ref_array("report", maximum=MAX_LINKS), "linked_decision_refs": _entity_ref_array("decision", maximum=MAX_LINKS), "notes": _json_value(), "idempotency_key": idempotency_key},
                ("task_ref",),
            ),
            "outputSchema": _tool_output_schema("initiative", "warnings", "replayed"),
        },
        "inspect_governance": {
            "description": "Read task governance, or an initiative selected within the ledger anchored by task_ref, with a bounded scoped chronology and effective projection.",
            "inputSchema": _closed({"task_ref": task_ref, "initiative_ref": initiative_id, "after_sequence": {"type": "integer", "minimum": 0}}, ("task_ref",)),
            "outputSchema": _tool_output_schema("initiatives", "assessments", "closures", "initiative_revisions", "links", "warnings", "projection", "timeline", "next_sequence", "has_more", optional_success_fields=("human_view",)),
        },
        "submit_governance_closure": {
            "description": "Attempt one advisory closure anchored by task_ref, then automatically inspect the intended record and return current advisory conformance evidence when inspection succeeds. The neutral four-field execution_outcome remains intact if advisory persistence or inspection is unavailable. Cortex makes at most one same-idempotency retry for a verified transient persistence or inspection outage; remaining uncertainty is returned as closure_confirmation.inspection_status=unconfirmed. Governance remains nonblocking.",
            "inputSchema": _closed(
                {"task_ref": task_ref, "subject_type": _string(enum=CLOSURE_SUBJECTS, maximum=16), "subject_ref": _string(maximum=14, pattern=r"^(?:t|i)_[0-9a-f]{12}$", description="Exact compact task or initiative subject selected by subject_type."), "verdict": _string(enum=CLOSURE_VERDICTS, maximum=32), "evidence": _json_value(), "unresolved_risks": {"type": "array", "items": _string(minimum=0), "maxItems": MAX_LINKS}, "follow_ups": {"type": "array", "items": _string(minimum=0), "maxItems": MAX_LINKS}, "initiative_status": _string(enum=INITIATIVE_STATUSES, maximum=16), "completion_notes": _json_value(), "idempotency_key": idempotency_key},
                ("task_ref", "subject_type", "subject_ref", "verdict", "evidence"),
            ),
            "outputSchema": _tool_output_schema("closure", "closure_confirmation", "execution_outcome", "initiative", "warnings", "next_action", "replayed", optional_success_fields=("conformance_review",), success_property_overrides={"closure": _RESULT_PROPERTY_SCHEMAS["closure"] | {"type": ["object", "null"], "description": "Recorded advisory closure, or null when transient persistence remains unavailable."}, "initiative": _RESULT_PROPERTY_SCHEMAS["initiative"] | {"type": ["object", "null"], "description": "Updated initiative for an initiative closure, or null for a task closure."}}) | {
                "oneOf": [
                    {"properties": {"closure": {"type": "null"}, "next_action": {"not": {"required": ["advisory_status"]}}}, "required": ["closure", "next_action"]},
                    {"properties": {"closure": {"type": "object"}}, "required": ["closure"]},
                ],
            },
        },
        "record_user_decision": {
            "description": "Append an ordinary-chat user decision asserted by the coordinator using this one flat advertised field catalogue. task, delegation, and initiative subjects require the common decision fields, including the anchored task reference, and forbid subject_digest and approval-view fields. report and plan subjects additionally require subject_digest; their successful receipt returns the exact selected report_ref together with task_ref and decision_ref, so any immediate report follow-up remains in that same task scope. Only a plan approve decision additionally requires approval_handle, approval_view_content_digest, and approval_view_source_sequence copied from one ready approval_view. steering_delta is required only for a steer decision, which must target the anchored task, and must contain at least one non-empty add or retire operation; every other decision forbids steering_delta. A revision or cancellation remains bound to the immutable plan digest but is not blocked by later unrelated chronology. Original task text, silence, inferred consent, wrappers, aliases, and mixed request shapes are invalid. The approval handle proves only the ready-view relation, not a host-authenticated user turn.",
            "inputSchema": _closed(
                {
                    "task_ref": task_ref,
                    "subject_type": _string(enum=DECISION_SUBJECTS, maximum=16),
                    "subject_ref": _string(maximum=14, pattern=r"^[tdriu]_[0-9a-f]{12}$", description="Exact compact reference for the selected subject; task decisions use the anchored task_ref."),
                    "subject_digest": _string(minimum=0, maximum=71, pattern=DIGEST_PATTERN, description="Required for plan and report subjects; binds the response to an immutable revision."),
                    "decision_type": _string(enum=DECISION_TYPES, maximum=32),
                    "prompt": _string(minimum=0, description="Language-neutral prompt or decision context shown by the coordinator."),
                    "response_original": _string(minimum=0, description="Exact arbitrary-Unicode user response."),
                    "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH),
                    "approval_handle": _identifier(description="Exact opaque approval_view handle. Required only for decision_type=approve; never construct or reuse for a different plan/view."),
                    "approval_view_content_digest": _string(minimum=0, maximum=71, pattern=DIGEST_PATTERN, description="Exact ready approval_view.content_digest. Required only for decision_type=approve."),
                    "approval_view_source_sequence": {"type": "integer", "minimum": 0, "description": "Exact ready approval_view.source_sequence. Required only for decision_type=approve."},
                    "supersedes_decision_ref": decision_id,
                    "steering_delta": _closed({"retire_item_refs": {"type": "array", "uniqueItems": True, "items": _string(pattern=r"^o_[0-9a-f]{12}$", maximum=14)}, "add": {"type": "array", "items": _closed({"category": _string(enum=("requirement", "constraint", "acceptance", "verification"), maximum=16), "text": _string(minimum=1)}, ("category", "text"))}}, ()) | {"description": "Required for a steer decision; creates a new effective-contract revision and invalidates only retired item coverage."},
                    "idempotency_key": idempotency_key,
                },
                ("task_ref", "subject_type", "subject_ref", "decision_type", "prompt", "response_original", "user_language"),
            ),
            "outputSchema": _tool_output_schema("decision", "replayed", optional_success_fields=("human_view",)),
        },
    }
    # The storage-oriented names above are implementation vocabulary only.  The
    # wire catalogue is deliberately a smaller semantic surface: callers open
    # and read tasks/assignments, consume declared evidence, publish each report
    # kind, record decisions, assess governance, and close a task.  Keep this
    # projection here so no legacy spelling can accidentally become callable.
    implementation_contracts = contracts
    report_contract = implementation_contracts["submit_report"]
    report_variants: dict[str, dict[str, Any]] = {}
    for semantic_name, report_type in (
        ("publish_plan", "plan"),
        ("publish_result", "result"),
        ("publish_documentation", "synthesis"),
    ):
        variant = {**report_contract}
        variant["description"] = (
            f"Publish one immutable {report_type} assignment report. "
            "The server assigns assembly order and computes the final manifest; "
            "the caller never supplies an index, count, or digest."
        )
        schema = {**report_contract["inputSchema"]}
        properties = dict(schema["properties"])
        properties["report_type"] = {**properties["report_type"], "enum": [report_type]}
        schema["properties"] = properties
        variant["inputSchema"] = schema
        report_variants[semantic_name] = variant

    # Do not expose an alias-shaped view of the old storage API.  These closed
    # objects are the domain boundary: report chunks, manifests, report IDs,
    # caller idempotency keys, and mutable initiative bookkeeping are internal.
    assignment_ref = _entity_ref("delegation", description="Exact assignment reference emitted by open_assignment.")

    # Publication is a semantic command, not an opaque report-storage write.
    # Keep the complete envelope at the advertised MCP boundary: a worker must
    # be able to form its first valid publication from tools/list plus the
    # server-issued assignment contract, rather than discover hidden admission
    # rules from a report_incomplete rejection.
    evidence_fact = _closed({
        "state": _string(enum=("executed", "not_run"), maximum=16, description="Whether this observable check was executed or honestly not run."),
        "summary": _string(description="One concise complete observation. For execution, name the checked command and outcome; for non-execution, state why it was not run."),
    }, ("state", "summary"), description="One uniform complete observable evidence fact. This deliberately avoids branch-specific field shapes so a first publication has one unambiguous evidence form.")
    documentation_impact = _string(minimum=1, description="Complete documentation-impact assessment. Name affected paths when impact exists; otherwise explain why there is no impact.")
    def publication_evidence(kind: str) -> dict[str, Any]:
        common = {
            "schema": _string(enum=("cortex/report/plan/v3" if kind == "plan" else "cortex/report/result/v3" if kind == "result" else "cortex/report/synthesis/v3",), maximum=64, description="Exact advertised semantic publication format for this operation."),
            "summary": _string(description="Concise complete outcome summary for this publication."),
            "verification": _text_array(minimum=0, description="Complete overall verification and acceptance observations. For a plan, place cross-stage acceptance checks here and stage-specific checks inside each ordered stage."),
            "risks": _text_array(minimum=0, description="Residual risks supported by this worker's evidence; use an empty array when none remain."),
            "deviations": _text_array(minimum=0, description="Observed deviations from the assigned outcome; use an empty array when none occurred."),
            "unresolved": _text_array(minimum=0, description="Unresolved work or decisions; use an empty array when the outcome is complete."),
            "source_text": _string(minimum=0, description="Optional unchanged source prose when preservation is materially useful; omit it for ordinary structured evidence."),
        }
        # Keep tools/list identical to the canonical semantic admission
        # boundary.  A property that is required by canonical validation must
        # never be discoverable only after a failed first call.
        required = ["schema", "summary", "verification", "risks", "deviations", "unresolved"]
        if kind == "plan":
            common.update({
                "scope": _string(description="Complete implementation boundary covered by this plan, including relevant constraints and assumptions in prose."),
                "stages": {"type": "array", "minItems": 1, "items": _closed({
                    "order": {"type": "integer", "description": "Non-authoritative stage-order bookkeeping accepted for lossless report round trips. The server always recomputes canonical order from array position."},
                    "owner": _string(description="Worker role that owns this stage."),
                    "dependencies": {"type": "array", "items": {"type": "integer"}, "description": "Non-authoritative predecessor bookkeeping accepted for lossless report round trips. The server always recomputes canonical predecessors from array position."},
                    "work": _text_array(minimum=1, description="Concrete work owned by this stage."),
                    "verification": _text_array(minimum=1, description="Acceptance checks owned by this stage."),
                }, ("owner", "work", "verification"), description="One ordered plan stage. Array order is the authoritative dependency order, and the server derives order and predecessor bookkeeping. The same canonical stage shape is accepted on first publication and when immutable consumed evidence is republished.")},
                "verification_facts": {"type": "array", "minItems": 1, "description": "Observable facts supporting the plan publication itself, such as bounded discovery performed or an honest statement that no project command was run.", "items": evidence_fact},
                "documentation_impact": documentation_impact,
            })
            # Documentation impact is assessed after implementation and
            # verification by the documentation worker, not by the planner.
            # Keeping it out of the plan's required set prevents a future
            # phase verdict from becoming a first-call planner prerequisite.
            required += ["scope", "stages", "verification_facts"]
        elif kind == "result":
            common.update({"outcome": _string(description="Complete assignment-level result outcome."), "changes": _text_array(minimum=0, description="Concrete project changes made by this worker; use an empty array for a read-only result."), "verification_facts": {"type": "array", "minItems": 1, "description": "Observable execution or verification facts supporting this result.", "items": evidence_fact}, "documentation_impact": documentation_impact})
            required += ["outcome", "verification_facts", "documentation_impact"]
        else:
            common.update({"findings": _text_array(minimum=0, description="Concrete documentation-impact findings."), "recommendations": _text_array(minimum=0, description="Bounded documentation follow-up recommendations, if any."), "documentation_impact": documentation_impact})
            required += ["findings", "recommendations", "documentation_impact"]
        return _closed(common, tuple(required), description=f"Closed current canonical {kind} publication evidence. This is the complete first-pass semantic admission contract. The immutable continuation already binds the authoritative assignment scope, so the caller never repeats server-owned coverage identities; the server persists that scope atomically with the publication.")
    compact_output = {
        "$schema": _JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "properties": {
            # Every public operation replaces this placeholder with an
            # operation-specific closed handle schema below. Keeping the
            # fallback closed prevents a future operation from accidentally
            # advertising a generic cross-operation handle union.
            "handles": _closed({}, (), description="No callable handles are implied by this output family; the operation-specific schema selects them explicitly."),
            "effective_contract": _result_object("Authoritative server-owned scope for this worker's publication. A planner receives the full planning catalogue; other workers receive their server-derived assigned catalogue. Publication coverage is derived by the server from this bound scope.", {"revision": {"type": "integer", "minimum": 1}, "assigned_items": {"type": "array"}, "planning_items": {"type": "array"}, "decisions": {"type": "array"}}),
            "assignment_context": _result_object("Bounded semantic context for the exact assignment; it does not replace the effective contract or server-issued evidence.", {"role": _string(), "profile_name": _string(), "objective": _string(), "scope": _string(), "instructions": _string()}),
            "predecessor_evidence": {"type": "array", "description": "Exact declared predecessor report manifests returned for this assignment."},
            "decision_evidence": {"type": "array", "description": "Exact declared decision evidence returned for this assignment."},
            "human_view": _HUMAN_VIEW_SCHEMA,
            "approval_view": _APPROVAL_VIEW_SCHEMA,
        },
        "required": ["handles"],
        "additionalProperties": True,
    }
    def operation_handles(*names: str, required: tuple[str, ...] = ()) -> dict[str, Any]:
        """Return one closed operation-specific callable handle contract."""
        unknown = [name for name in names if name not in _HANDLES_SCHEMA["properties"]]
        if unknown:
            raise RuntimeError(f"unknown semantic handle fields: {unknown}")
        return _closed(
            {name: _HANDLES_SCHEMA["properties"][name] for name in names},
            required,
            description="Exact callable values emitted by this operation only. No other handle kind is valid or implied.",
        )

    def compact_with_handles(*names: str, required: tuple[str, ...] = ()) -> dict[str, Any]:
        properties = dict(compact_output["properties"])
        properties["handles"] = operation_handles(*names, required=required)
        return {**compact_output, "properties": properties}

    read_task_output = {
        **compact_output,
        "properties": {
            **compact_output["properties"],
            "handles": _closed({
                "task_ref": task_ref,
                "report_refs": _entity_ref_array("report", maximum=MAX_REPORT_IDS),
                "after_sequence": {"type": "integer", "minimum": 0, "description": "Exact next task chronology position for a later bounded read."},
            }, ("task_ref",), description="Task-read handles only. When a ready plan view is presented to the user, copy its server-formatted markdown_link byte-for-byte; its host-private path is evidence only and must not be rendered or reconstructed."),
        },
    }
    def decision_output(binding_name: str, *, recorded: bool = False, report_relation: bool = False) -> dict[str, Any]:
        record_operation = {"clarification": "record_clarification", "plan-review": "record_plan_review", "steering": "record_steering"}.get(binding_name, "record_decision")
        """Closed family result with one directly consumable binding handle."""
        clarification_hold = {
            "type": "object", "additionalProperties": False,
            "description": "Closed clarification-hold lifecycle evidence. A successful open_clarification creates or replays this hold before the coordinator renders its one matching product question. A successful record_decision consumes the same hold exactly once; assignment-origin holds then require host delivery or explicit unavailable recovery evidence.",
            "properties": {
                "state": _string(enum=("pending_question", "pending_delivery", "delivery_claimed", "delivered", "coordinator_completed", "unavailable", "stale", "superseded"), maximum=32),
                "assignment_ref": _entity_ref("delegation", description="Exact originating assignment when this hold needs worker continuation."),
                "decision_ref": _entity_ref("decision", description="Exact recorded response receipt after the hold is answered."),
                "opened_sequence": {"type": "integer", "minimum": 0},
                "answered_sequence": {"type": ["integer", "null"], "minimum": 0},
                "delivery_sequence": {"type": ["integer", "null"], "minimum": 0},
                "unavailable_reason": {"type": ["string", "null"], "maxLength": 1024},
            },
            "required": ["state", "opened_sequence", "answered_sequence", "delivery_sequence"],
        }
        host_delivery = {
            "type": "object", "additionalProperties": False,
                "description": "Closed exact-worker continuation projection emitted only for an answered assignment-origin clarification. The coordinator may use its returned trusted message unchanged with a genuinely supported host follow-up facility. The current Codex plugin exposes no such callback, so it remains durable coordinator recovery evidence until the exact assignment publishes. It never reconstructs an assignment, worker identity, response, or continuation capability, and is not authority to schedule replacement work.",
            "properties": {
                "state": _string(enum=("pending_delivery", "delivery_claimed", "delivered", "unavailable"), maximum=32),
                "continuation_capability": _string(pattern=r"^hc_[0-9a-f]{32}$", maximum=35, description="Opaque server-issued exact-worker continuation capability. Pass only unchanged to the host follow-up adapter; it is not an MCP tool argument."),
                "assignment_ref": _entity_ref("delegation", description="Exact assignment that originated the clarification hold."),
                "native_task_name": _string(maximum=128, description="Exact server-owned native host task name for the existing assignment; do not infer or substitute another worker."),
                "native_dispatch_digest": _opaque_digest("Immutable server-derived digest of the exact saved assignment/native dispatch identity. It is evidence only, never a host capability or MCP call input."),
                "dispatch_correlation_marker": _string(pattern=r"^dc_[0-9a-f]{32}$", maximum=35, description="Exact non-authorizing server-issued dispatch observation marker embedded in the original trusted worker brief. It is not an MCP call input or continuation capability."),
                "dispatch_correlation_fingerprint": _opaque_digest("One-way fingerprint of the exact dispatch observation marker; evidence only."),
                "decision_ref": _entity_ref("decision", description="Immutable response evidence delivered to the existing worker."),
                "message": _string(description="Trusted renderer-owned continuation message. Pass byte-for-byte to the host; do not construct a replacement message."),
                "renderer": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "version": _string(maximum=128),
                        "common_policy_digest": _string(pattern=DIGEST_PATTERN),
                    },
                    "required": ["version", "common_policy_digest"],
                },
                "unavailable_reason": {"type": ["string", "null"], "maxLength": 1024},
            },
            "required": ["state", "assignment_ref", "native_task_name", "native_dispatch_digest", "dispatch_correlation_marker", "dispatch_correlation_fingerprint", "decision_ref"],
        }
        return {
            "$schema": _JSON_SCHEMA_DRAFT_2020_12,
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "task_ref": task_ref,
                "binding_ref": {"type": "string", "minLength": 35, "maxLength": 35, "pattern": r"^cb_[0-9a-f]{32}$", "description": f"Server-issued {binding_name} binding. Copy byte-for-byte into record_decision."},
                "next_action": {"type": "string", "enum": [record_operation], "description": "The server-issued next operation for this decision binding."},
                "decision_ref": {"type": "string", "pattern": r"^u_[0-9a-f]{12}$"},
                "decision": {
                    "type": "object",
                    "additionalProperties": False,
                    "description": "Server-returned decision evidence only. This object is output, not a request template; its fields must never be copied into a record operation.",
                    "properties": {
                        "decision_ref": _entity_ref("decision", description="Exact compact decision reference emitted after recording."),
                        "subject_type": _string(maximum=16),
                        "subject_ref": {"type": "string", "maxLength": 14, "pattern": r"^[tdriu]_[0-9a-f]{12}$", "description": "Bounded compact subject relation; canonical IDs are never public."},
                        "subject_digest": {"type": ["string", "null"]},
                        "decision_type": _string(maximum=32, description="Server-resolved decision family; output evidence only, never a record-operation input."),
                        "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN),
                        "attribution": _string(maximum=64),
                        "relations": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "supersedes_decision_ref": _entity_ref(
                                    "decision",
                                    description="Exact same-task decision relation persisted by a steering record.",
                                ),
                            },
                        },
                        "created_at": _string(maximum=64),
                        "created_sequence": {"type": "integer", "minimum": 0},
                        "response_original": _string(minimum=0),
                    },
                },
                "replayed": {"type": "boolean"},
                "decision_context": {
                    "type": "object",
                    "additionalProperties": False,
                    "description": "Server-returned binding context only. The matching record operation derives its decision family from the binding and does not accept this context as input.",
                    "properties": {
                        "task_ref": task_ref,
                        "subject_type": _string(maximum=16),
                        "subject_ref": {"type": "string", "maxLength": 14},
                        "decision_type": _string(maximum=32),
                        "prompt": _string(minimum=0),
                        "prompt_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="Language of the user-facing clarification rendered by the coordinator; internal prompt context may be language-neutral."),
                        "effective_contract_revision": {"type": "integer", "minimum": 1},
                        "consumed": {"type": "boolean"},
                        "decision_ref": _entity_ref("decision", description="Existing immutable decision recorded for this binding when consumed is true."),
                        "plan_review_relation": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "plan_content_digest": _string(pattern=DIGEST_PATTERN),
                                "approval_handle": _string(maximum=160),
                                "view_content_digest": _string(pattern=DIGEST_PATTERN),
                                "view_source_sequence": {"type": "integer", "minimum": 0},
                            },
                            "required": [
                                "plan_content_digest", "approval_handle",
                                "view_content_digest", "view_source_sequence",
                            ],
                            "description": "Immutable server-resolved plan and ready-view relation captured when this review binding was issued.",
                        },
                    },
                },
                "approval_view": _APPROVAL_VIEW_SCHEMA,
                "clarification_hold": clarification_hold,
                "host_delivery": host_delivery,
                "handles": operation_handles(
                    "task_ref", "binding_ref",
                    *(("decision_ref",) if recorded else ()),
                    *(("report_ref",) if report_relation else ()),
                    required=("task_ref", "binding_ref", *(("decision_ref",) if recorded else ())),
                ),
            },
            "required": ["task_ref", "binding_ref", "next_action", "handles"],
        }
    steering_delta = _closed(
        {
            "retire_item_refs": {
                "type": "array", "minItems": 0, "uniqueItems": True,
                "items": _string(pattern=r"^o_[0-9a-f]{12}$", maximum=14),
                "description": "Optional operation: retire one or more exact outcome references returned by Cortex.",
            },
            "add": {
                "type": "array", "minItems": 0,
                "items": _closed(
                    {
                        "category": _string(enum=("requirement", "constraint", "acceptance", "verification"), maximum=16),
                        "text": _string(minimum=1),
                    },
                    ("category", "text"),
                    description="One closed effective-contract addition.",
                ),
                },
                "description": "Optional operation named exactly add: append one or more contract items stated by the user.",
            },
        (),
        description="Required closed steering delta for this response. Include at least one advertised operation: use add for new contract items and/or retire_item_refs for exact outcome references. These are the only operation names.",
    )
    continuation_ref = _string(maximum=160, description="Exact server-issued worker continuation returned by assignment evidence consumption. Copy byte-for-byte; assignment and task locators alone are not publication authority.")
    publication = lambda kind: {
        "description": f"Atomically publish one immutable {kind} outcome for an assignment. The worker-owned status is the assignment-level semantic outcome: completed is the only complete outcome; partial or blocked remain non-complete. The server derives the exact active assignment scope and stores its coverage in the same transaction; any optional coverage annotation is non-authoritative. The server consumes declared evidence, chunks internally, and records its manifest before the terminal slot is consumed. An exact retry replays; changed evidence conflicts; corrections require a recovery assignment.",
        "inputSchema": _closed({"continuation_ref": continuation_ref, "assignment_ref": assignment_ref, "evidence": publication_evidence(kind), "status": _string(enum=("completed", "partial", "blocked"), maximum=16, description="Optional assignment-level semantic outcome; omission means completed.")}, ("continuation_ref", "assignment_ref", "evidence")),
        "outputSchema": compact_with_handles("report_ref", required=("report_ref",)),
    }
    plan_publication_output = {
        "$schema": _JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
            "properties": {
            "report": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "report_ref": _entity_ref("report", description="Exact immutable published plan reference."),
                    "report_type": _string(enum=["plan"], maximum=16),
                    "status": _string(enum=["completed"], maximum=16),
                    "semantic_status": _string(enum=["semantic_valid"], maximum=32),
                    "content_digest": _string(pattern=DIGEST_PATTERN),
                    "supersedes_report_ref": _entity_ref("report", description="Server-derived exact prior plan replaced by this immutable revision. Omitted only for the first plan."),
                },
                "required": ["report_ref", "report_type", "status", "semantic_status", "content_digest"],
                "description": "Closed compact immutable plan evidence; canonical report/task/delegation IDs are internal.",
            },
            "replayed": {"type": "boolean"},
            "approval_view": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "report_content_digest": _string(pattern=DIGEST_PATTERN),
                    "status": _string(enum=["ready"], maximum=16),
                    "source_sequence": {"type": "integer", "minimum": 0},
                    "content_digest": _string(pattern=DIGEST_PATTERN),
                    "approval_handle": _string(maximum=160),
                    "report_ref": _entity_ref("report", description="Exact immutable plan relation."),
                    "delegation_ref": _entity_ref("delegation", description="Assignment that published the exact plan."),
                },
                "required": ["report_content_digest", "status", "source_sequence", "content_digest", "approval_handle", "report_ref", "delegation_ref"],
                "description": "Closed server-issued immutable approval relation. Keep its opaque approval_handle and compact refs unchanged.",
            },
            "handles": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "report_ref": _entity_ref("report", description="Exact published plan reference."),
                },
                "required": ["report_ref"],
                "description": "Exact callable plan reference emitted by publication. The separate approval_view is presentation evidence, not a handle container.",
            },
        },
        "required": ["report", "replayed", "approval_view", "handles"],
    }
    assignment_output = _closed({
        "assignment_ref": assignment_ref,
        "handles": operation_handles("assignment_ref", required=("assignment_ref",)),
        "native_dispatch": _NATIVE_HOST_PROJECTION_SCHEMA,
        "replayed": {"type": "boolean", "description": "Whether this response reconciles the same server-committed assignment request."},
        "relations": _closed({
            "parent_assignment_ref": _entity_ref("delegation", description="Server-derived predecessor assignment for replacement or rework. Omitted for an assignment with no predecessor."),
        }, (), description="Server-owned assignment graph relations derived from immutable predecessor evidence."),
    }, ("assignment_ref", "handles", "native_dispatch", "replayed", "relations"), description="Successful compact assignment opening with one server-issued native host projection. Forward native_dispatch literally as the host spawn input; do not reconstruct a message from the semantic task or assignment evidence. The server derives an unambiguous replacement/rework predecessor from declared immutable evidence and current ownership, then returns that relation; callers never restate it. No worker bootstrap authority is exposed outside that single message. Full worker scope, mission, decisions, predecessor evidence, and continuation authority are returned only to the spawned worker by assignment evidence consumption.")
    contracts = {
        "open_task": {
            "description": "Open exactly one durable task from one coherent outcome contract. The request has one task object; put its advertised project, original request, language, outcomes, constraints, and optional context directly inside that object, never inside a second nested task object and never under unadvertised parallel requirement or acceptance properties. Each required outcome carries its own measurable acceptance statements, so no required outcome can be separated from how it will be accepted. The server preserves the original request as the immutable task objective, normalizes the contract into durable requirements and acceptance criteria, derives verification evidence, and returns the task reference.",
            "inputSchema": _closed({"task": _closed({
                "project_root": _string(maximum=PROJECT_ROOT_MAX_LENGTH, description="Canonical project identity for this task."),
                "request_original": _string(description="Exact original user request preserved as durable source context."),
                "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 language tag for the exact original request, for example en or ru."),
                "outcomes": {"type": "array", "minItems": 1, "items": _closed({
                    "requirement": _string(description="One required outcome."),
                    "acceptance": _text_array(minimum=1, description="One or more measurable statements that accept this required outcome."),
                }, ("requirement", "acceptance"), description="One required outcome paired with its measurable acceptance statements."), "description": "Non-empty list of required outcomes. Each item contains its requirement and acceptance directly."},
                "constraints": _text_array(minimum=1, description="Non-empty task constraints, including forbidden actions where applicable."),
                "context": _json_value(),
            }, ("project_root", "request_original", "user_language", "outcomes", "constraints"), description="The sole task object. Place every advertised child property directly here; do not add another task wrapper. The exact original request is also the durable objective, and outcomes pair each requirement with its acceptance statements." )}, ("task",)),
            "outputSchema": compact_with_handles("task_ref", required=("task_ref",)),
        },
        "read_task": {"description": "Read bounded task state, effective contract, aggregate evidence, and chronology. When presenting a ready plan view, use only the exact returned Markdown link; never render or reconstruct its host-private path.", "inputSchema": _closed({"task_ref": task_ref, "after_sequence": {"type": "integer", "minimum": 0}}, ("task_ref",)), "outputSchema": read_task_output},
        "open_clarification": {"description": "Create or replay one task-scoped clarification hold and its server-owned binding before rendering the corresponding product question. The server derives the task subject; an optional exact assignment relation may be supplied only when the question originates from that assignment. After the next user answer, perform the result's advertised next action before any governance, planning, assignment, or closure mutation; the server will not advance the task while this binding is pending. The result states the hold lifecycle and, for an assignment-origin question, the exact assignment relation that must later receive the recorded answer. Render no question when this command fails.", "inputSchema": _closed({"task_ref": task_ref, "prompt": _string(minimum=1), "prompt_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN), "assignment_ref": assignment_ref}, ("task_ref", "prompt", "prompt_language")), "outputSchema": decision_output("clarification")},
        "open_plan_review": {"description": "Issue or replay one server-owned plan-review binding for the selected plan. When decision_context.consumed is false, copy the returned binding_ref byte-for-byte into record_decision. When consumed is true, the same logical review was already recorded: use decision_context.decision_ref and do not record the binding again.", "inputSchema": _closed({"task_ref": task_ref, "plan_ref": _string(maximum=14, pattern=r'^r_[0-9a-f]{12}$'), "prompt": _string(minimum=1), "prompt_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN)}, ("task_ref", "plan_ref", "prompt", "prompt_language")), "outputSchema": decision_output("plan-review")},
        "open_steering": {"description": "Issue or replay one server-owned steering binding for the anchored task. Copy the returned binding_ref byte-for-byte into record_decision; the server derives the steering family from that binding.", "inputSchema": _closed({"task_ref": task_ref, "prompt": _string(minimum=1), "prompt_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN), "assignment_ref": assignment_ref}, ("task_ref", "prompt", "prompt_language")), "outputSchema": decision_output("steering")},
        "record_clarification": {"description": "Record one exact clarification response against its server-issued clarification binding. The operation accepts only the clarification answer and common language information; contract changes use the dedicated steering flow.", "inputSchema": _closed({"task_ref": task_ref, "binding_ref": _string(minimum=35, maximum=35, pattern=r"^cb_[0-9a-f]{32}$"), "response_original": _string(maximum=65536), "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 language tag for the exact response, for example en or ru.")}, ("task_ref", "binding_ref", "response_original", "user_language")), "outputSchema": decision_output("clarification", recorded=True)},
        "record_plan_review": {"description": "Record one exact plan-review response against its server-issued plan-review binding. The response outcome is restricted to the plan-review choices and is validated against the immutable approval relation.", "inputSchema": _closed({"task_ref": task_ref, "binding_ref": _string(minimum=35, maximum=35, pattern=r"^cb_[0-9a-f]{32}$"), "response_original": _string(maximum=65536), "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 language tag for the exact response, for example en or ru."), "outcome": _string(enum=("approve", "request_revision", "cancel"), maximum=32)}, ("task_ref", "binding_ref", "response_original", "user_language", "outcome")), "outputSchema": decision_output("plan-review", recorded=True)},
        "record_steering": {"description": "Record one exact steering response against its server-issued steering binding. The closed contract delta and optional supersession are applied atomically to the effective contract.", "inputSchema": _closed({"task_ref": task_ref, "binding_ref": _string(minimum=35, maximum=35, pattern=r"^cb_[0-9a-f]{32}$"), "response_original": _string(maximum=65536), "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 language tag for the exact response, for example en or ru."), "add": steering_delta["properties"]["add"], "retire_item_refs": steering_delta["properties"]["retire_item_refs"], "supersedes_decision_ref": _entity_ref("decision")}, ("task_ref", "binding_ref", "response_original", "user_language", "add", "retire_item_refs")), "outputSchema": decision_output("steering", recorded=True)},
        "open_assignment": {
            "description": "Open one worker assignment from one coherent mission contract and atomically bind its effective contract plus declared typed report and decision evidence. This task-phase mutation is available only when no server-issued user decision remains pending; first perform the pending decision result's advertised next action. Immutable input reports and current server-owned outcome ownership determine any replacement/rework predecessor; callers never restate assignment lineage. Owner-profile rework must declare the relevant finalized predecessor evidence, and an ambiguous set of current owners is rejected before mutation. Review and documentation profiles consume evidence without taking ownership. The explicit profile expresses worker intent; Cortex selects the packaged model and reasoning recommendation from its server-owned routing table. Routing recommendations are output-only and never caller input. Decision evidence is resolved in the assignment; report evidence is consumed only through the assignment evidence operation.",
            "inputSchema": _closed({"task_ref": task_ref, "mission": _closed({
                "role": _string(maximum=ROLE_MAX_LENGTH),
                "profile_name": _string(enum=profile_names, maximum=ROLE_MAX_LENGTH, description="Explicit packaged worker-profile intent."),
                "goal": _string(description="One concrete worker mission outcome."),
                "constraints": _string(description="Execution boundary and scope constraints for this mission."),
                "instructions": _string(description="Concrete task-specific worker instructions within the declared mission boundary."),
            }, ("role", "profile_name", "goal", "constraints", "instructions"), description="Closed assignment mission. It combines role, intended outcome, execution boundary, and instructions without duplicated objective/scope fields."), "input_report_refs": _entity_ref_array("report", maximum=MAX_REPORT_IDS), "input_decision_refs": _entity_ref_array("decision", maximum=MAX_DECISION_IDS)}, ("task_ref", "mission")),
            "outputSchema": assignment_output,
        },
        "consume_assignment_evidence": {"description": "Worker-only bootstrap operation. The spawned native worker consumes the one-time server-owned assignment evidence for the exact assignment locator delivered inside its server-rendered dispatch; a coordinator never calls this operation for a worker. The server resolves the private bootstrap lease atomically; no caller-supplied bootstrap capability is accepted. On success, return the worker-scoped continuation plus the authoritative server-owned effective scope, assignment context, declared predecessor report evidence/manifests, and decision evidence required for that worker's publication. A planner receives the full planning catalogue; other workers receive their server-derived assigned catalogue. The server derives publication coverage from this scope and the worker's explicit assignment-level outcome.", "inputSchema": _closed({"assignment_ref": assignment_ref, "cursor": _string(maximum=2048, description="Optional opaque continuation cursor previously emitted by this operation; copy it byte-for-byte only when continuing the same assignment evidence read.")}, ("assignment_ref",)), "outputSchema": compact_with_handles("assignment_ref", "continuation_ref", required=("assignment_ref", "continuation_ref"))},
        "publish_plan": {**publication("plan"), "outputSchema": plan_publication_output},
        "publish_result": publication("result"),
        "publish_documentation": publication("documentation"),
        "assess_governance": {"description": "Record a material advisory task assessment only when no server-issued user decision remains pending; first perform the pending decision result's advertised next action. Ordinary worker stage or rework notes belong to the task timeline, not initiative revision history.", "inputSchema": _closed({"task_ref": task_ref, "mode": _string(enum=GOVERNANCE_MODES, maximum=16, description="Advisory governance depth selected from this property's advertised enum."), "rationale": _string(minimum=0), "risk_factors": {"type": "array", "items": _string(), "maxItems": MAX_LINKS}}, ("task_ref", "mode")), "outputSchema": compact_with_handles("task_ref", required=("task_ref",))},
        "close_task": {"description": "Record the final task closure aggregate. The server derives authoritative closure evidence from the current effective contract, immutable report manifests, decisions, consumption receipts, and aggregate conformance state; the caller supplies only its advisory verdict and optional unresolved risks, follow-ups, or notes.", "inputSchema": _closed({"task_ref": task_ref, "verdict": _string(enum=CLOSURE_VERDICTS, maximum=32), "unresolved_risks": {"type": "array", "items": _string(minimum=0), "maxItems": MAX_LINKS}, "follow_ups": {"type": "array", "items": _string(minimum=0), "maxItems": MAX_LINKS}, "completion_notes": _json_value()}, ("task_ref", "verdict")), "outputSchema": compact_with_handles("task_ref", required=("task_ref",))},
    }
    contracts = {name: contracts[name] for name in V12_TOOL_NAMES}
    if tuple(contracts) != V12_TOOL_NAMES:
        raise RuntimeError(f"Cortex v12 public catalogue must contain exactly {len(V12_TOOL_NAMES)} semantic tools")
    # Task identity becomes server-owned connection context after one exact
    # task has been established on the current MCP transport. Keep task_ref as
    # an accepted selector for a fresh/resumed connection or intentional task
    # switch, but do not force the model to retranscribe the same opaque
    # locator on every subsequent task-scoped operation. The transport rejects
    # omission before a binding exists and never guesses from ledger recency.
    for name, contract in contracts.items():
        schema = contract["inputSchema"]
        properties = schema.get("properties")
        required = schema.get("required")
        if (
            name != "open_task"
            and isinstance(properties, dict)
            and "task_ref" in properties
            and isinstance(required, list)
            and "task_ref" in required
        ):
            schema["required"] = [field for field in required if field != "task_ref"]
            contract["description"] += (
                " The exact task_ref may establish or replace this MCP connection's task context; "
                "after one successful exact task binding on the same connection, omit it to use that "
                "server-owned context. A fresh or restarted connection never guesses a task from ledger recency."
            )
    for name, contract in contracts.items():
        _assert_no_callable_durable_properties(contract["inputSchema"], path=f"{name}.inputSchema")
    _assert_no_callable_durable_properties(_HANDLES_SCHEMA, path="handles")
    _assert_no_callable_durable_properties(_APPROVAL_VIEW_SCHEMA, path="approval_view")
    _describe_contract_schemas(contracts)
    return contracts


def public_input_schemas(contracts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Expose the same schemas advertised by the MCP transport."""
    catalogue = build_public_contracts() if contracts is None else contracts
    return {str(name): dict(value["inputSchema"]) for name, value in catalogue.items()}
