"""The complete uniform public contract for the Cortex V12 ledger."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cortex_runtime.model_routing import NATIVE_MODELS, NATIVE_REASONING_EFFORTS
from cortex_runtime.worker_message import packaged_profile_names
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


V12_TOOL_NAMES = (
    "open_task",
    "read_task",
    "open_decision",
    "open_assignment",
    "consume_assignment_evidence",
    "publish_plan",
    "publish_result",
    "publish_documentation",
    "record_decision",
    "assess_governance",
    "close_task",
)


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
    return {
        "$schema": _JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "description": description,
        "additionalProperties": False,
        "properties": dict(properties),
        "required": required_fields,
    }


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


def _text_array(*, maximum: int = TASK_CONTRACT_MAX_ITEMS, minimum: int = 0) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": minimum,
        "maxItems": maximum,
        "items": _string(
            maximum=TASK_CONTRACT_ITEM_MAX_LENGTH,
            description="One bounded English task-contract item.",
        ),
    }


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
    "task_ref": "Preferred compact task locator emitted by create_task. Copy it byte-for-byte for every task-anchored tool; never use a UI-rendered task_id.",
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
    "content": "Required only for append. Storage accepts one finite bounded JSON value and worker read_reports returns its canonical JSON unchanged. New specialist plan and result evidence uses cortex/report/{plan,result}/v3: include exact contract coverage, observable evidence facts (executed command/cwd/exit/result or not_run reason), residual risks/deviations/unresolved, and documentation impact status/rationale/surfaces. A planner's v3 plan maps every current token in its returned planning_items catalogue exactly once, independent of delivery assignments, and has ordered stages with an owner, earlier-stage dependencies, work, and verification. Finalization rejects incomplete v3 evidence while the same report remains assembling for correction. Historical v1/v2 evidence remains readable. Canonical source material uses one optional unchanged source_text string with no language tag or translated/original companion.",
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
    "prompt": "Language-neutral prompt or decision context shown by the coordinator to the user.",
    "response_original": "Exact arbitrary-Unicode user response. It is stored privately and is not copied into compact inspection evidence.",
    "supersedes_decision_ref": "Optional exact emitted prior decision for the same subject that this decision supersedes.",
}


def _describe_schema_properties(schema: Mapping[str, Any]) -> None:
    """Populate every advertised property, including conditional branches."""
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, property_schema in properties.items():
            if not isinstance(property_schema, dict):
                continue
            if not isinstance(property_schema.get("description"), str):
                property_schema["description"] = _PROPERTY_DESCRIPTIONS.get(
                    str(name),
                    "Bounded public field. Use only durable values emitted by Cortex or values stated in this contract.",
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
        for key, kind in (("inputSchema", "request"), ("outputSchema", "successful structuredContent")):
            schema = contract.get(key)
            if not isinstance(schema, dict):
                continue
            schema.setdefault("$schema", _JSON_SCHEMA_DRAFT_2020_12)
            schema["description"] = f"{name} {kind}. {tool_description}"
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
_HUMAN_VIEW_SCHEMA = _result_object("Volatile host-private derived-view status; it never changes canonical ledger evidence.", {
    "status": _string(enum=("ready", "stale", "conflict", "unavailable", "disabled"), maximum=16, description="Current derived-view availability state."),
    "path": {"type": ["string", "null"], "description": "Verified absolute host-private view path only when status is ready; otherwise null."},
    "markdown_link": {"type": "string", "description": "Server-formatted exact Markdown link, present only when status=ready and path exists. Copy byte-for-byte; never reconstruct it from compact refs."},
    "source_sequence": {"type": "integer", "minimum": 0, "description": "Timeline sequence used to verify a ready derived view."},
    "content_digest": _opaque_digest("SHA-256 digest of verified ready view content.", nullable=True),
})
_APPROVAL_VIEW_SCHEMA = _result_object("Exact server-verified plan-review view from a completed finalized plan read. Only status=ready with the returned opaque approval_handle can support a later plan approval. Copy every handle, path, and digest byte-for-byte; never construct, concatenate, shorten, substitute native_task_name, or infer a path.", {
    "report_ref": _entity_ref("report"),
    "delegation_ref": _entity_ref("delegation"),
    "report_content_digest": _opaque_digest("Exact immutable plan report manifest digest required by record_user_decision."),
    "status": _string(enum=("ready", "stale", "conflict", "unavailable", "disabled"), maximum=16, description="Only ready permits presenting the returned path for approval."),
    "path": {"type": ["string", "null"], "description": "Exact verified host-private plan path when status is ready; otherwise null. Never construct this value."},
    "markdown_link": {"type": "string", "description": "Server-formatted exact Markdown plan link, present only when status=ready and path exists. Copy byte-for-byte; never reconstruct it from compact refs."},
    "source_sequence": {"type": ["integer", "null"], "minimum": 0, "description": "Exact ledger sequence used to verify the returned path, or null when not ready."},
    "content_digest": _opaque_digest("Exact verified derived-view digest when status is ready; otherwise null.", nullable=True),
    "approval_handle": _string(maximum=IDENTIFIER_MAX_LENGTH, pattern=IDENTIFIER_PATTERN, description="Server-issued opaque relation for this exact ready report/view/request snapshot; null unless status is ready.") | {"type": ["string", "null"]},
})
_DECISION_BINDING_SCHEMA = _result_object("Ready plan-approval arguments already named for record_user_decision. Copy this object field-for-field; it is a convenience projection of the verified approval relation, not a new authority token.", {
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
        "delegation_ref": _entity_ref("delegation"),
        "report_ref": _entity_ref("report"),
        "report_refs": _entity_ref_array("report", maximum=MAX_REPORT_IDS),
        "initiative_ref": _entity_ref("initiative"),
        "decision_ref": _entity_ref("decision"),
        "idempotency_key": _idempotency_key(),
        "cursor": _string(maximum=2_048, description="Exact opaque continuation cursor for read_reports only. " + HANDLE_COPY_RULE),
        "after_sequence": {"type": "integer", "minimum": 0, "description": "Exact inspection continuation input value. " + HANDLE_COPY_RULE},
        "human_view": _HUMAN_VIEW_SCHEMA,
        "approval_view": _APPROVAL_VIEW_SCHEMA,
        "decision_binding": _DECISION_BINDING_SCHEMA,
    },
}
_RENDERER_SCHEMA = _result_object("Packaged-profile renderer proof.", {
    "version": _string(maximum=160, description="Exact packaged renderer version."),
    "profile_name": _string(maximum=ROLE_MAX_LENGTH, description="Exact selected packaged profile name."),
    "profile_state": _string(maximum=32, description="Renderer profile availability state."),
    "profile_digest": _opaque_digest("SHA-256 digest of the exact loaded packaged profile.", nullable=True),
    "common_policy_digest": _opaque_digest("SHA-256 digest of the common trusted renderer policy."),
})
_DISPATCH_BRIEF_SCHEMA = _result_object("Host-neutral coordinator dispatch brief; it is semantic context, not a host call, receipt, or lifecycle handle.", {
    "task_name": _string(description="Stable task name for the active host to map to its spawn operation."),
    "rendered_message": _string(maximum=TEXT_MAX_LENGTH, description="Complete renderer-produced worker message."),
    "semantic_objective": _string(description="Delegation objective preserved for host-neutral dispatch reasoning."),
    "recommended_model": _string(description="Coordinator recommendation; active host availability remains authoritative."),
    "recommended_reasoning_effort": _string(description="Coordinator recommendation; active host effort values remain authoritative."),
    "delegation_ref": _entity_ref("delegation", description="Exact delegation anchor for this brief."),
    "project_root": _string(maximum=PROJECT_ROOT_MAX_LENGTH, description="Saved project-root context for the dispatched worker."),
    "profile_proof": _RENDERER_SCHEMA,
    "effective_contract": _result_object("Bounded effective-contract projection. assigned_items are this delegation's delivery assignments; a planner additionally receives planning_items, the exact full current contract token catalogue it must map once in a v3 plan before finalization.", {"revision": {"type": "integer", "minimum": 1}, "assigned_items": {"type": "array"}, "planning_items": {"type": "array", "description": "Planner-only full current requirement, constraint, acceptance, and derived-verification token catalogue for exact plan mapping."}, "decisions": {"type": "array"}}),
})
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
    "replayed": {"type": "boolean", "description": "Whether a matching idempotency key returned its original durable result."},
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
    task_ref = _task_ref(description="Preferred compact task locator emitted by create_task. Copy handles.task_ref byte-for-byte for task-anchored calls; never use a UI-rendered task_id.")
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
    compact_output = {
        "$schema": _JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "properties": {"handles": {"type": "object", "additionalProperties": True}},
        "required": ["handles"],
        "additionalProperties": True,
    }
    publication = lambda kind: {
        "description": f"Atomically publish one complete immutable {kind} outcome for an assignment. The server validates role-specific completeness, consumes declared evidence, chunks internally, and records its manifest before the terminal slot is consumed. An exact retry replays; changed evidence conflicts; corrections require a recovery assignment.",
        "inputSchema": _closed({"assignment_ref": assignment_ref, "evidence": _json_value(), "status": _string(enum=("completed", "partial", "blocked"), maximum=16)}, ("assignment_ref", "evidence")),
        "outputSchema": compact_output,
    }
    contracts = {
        "open_task": {
            "description": "Open exactly one durable task with its complete meaningful contract. The server derives verification evidence from acceptance criteria and returns the task reference.",
            "inputSchema": _closed({"project_root": _string(maximum=PROJECT_ROOT_MAX_LENGTH), "objective": _string(), "user_request_original": _string(), "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN), "requirements": _text_array(minimum=1), "constraints": _text_array(minimum=1), "acceptance_criteria": _text_array(minimum=1), "context": _json_value()}, ("project_root", "objective", "user_request_original", "user_language", "requirements", "constraints", "acceptance_criteria")),
            "outputSchema": compact_output,
        },
        "read_task": {"description": "Read bounded task state, effective contract, aggregate evidence, and chronology.", "inputSchema": _closed({"task_ref": task_ref, "after_sequence": {"type": "integer", "minimum": 0}}, ("task_ref",)), "outputSchema": compact_output},
        "open_decision": {"description": "Issue or replay one server-owned pending decision binding for the current task. Copy the returned binding_ref scalar byte-for-byte into record_decision; decision_context is explanatory only and is never callable.", "inputSchema": _closed({"task_ref": task_ref, "prompt": _string(minimum=1), "prompt_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN), "subject_type": _string(enum=DECISION_SUBJECTS, maximum=16), "subject_ref": _string(maximum=14, pattern=r'^[tdriu]_[0-9a-f]{12}$'), "assignment_ref": assignment_ref}, ("task_ref", "prompt", "prompt_language")), "outputSchema": compact_output},
        "open_assignment": {
            "description": "Open one worker assignment and atomically bind its effective contract plus declared typed report and decision evidence. Decision evidence is resolved in the assignment; report evidence is consumed only through the assignment evidence operation.",
            "inputSchema": _closed({"task_ref": task_ref, "objective": _string(), "role": _string(maximum=ROLE_MAX_LENGTH), "profile_name": _string(enum=profile_names, maximum=ROLE_MAX_LENGTH), "scope": _string(), "instructions": _string(), "model": _string(maximum=ROLE_MAX_LENGTH), "reasoning_effort": _string(maximum=ROLE_MAX_LENGTH), "input_report_refs": _entity_ref_array("report", maximum=MAX_REPORT_IDS), "input_decision_refs": _entity_ref_array("decision", maximum=MAX_DECISION_IDS), "parent_assignment_ref": assignment_ref}, ("task_ref", "objective", "role", "profile_name", "scope", "instructions", "model", "reasoning_effort")),
            "outputSchema": compact_output,
        },
        "consume_assignment_evidence": {"description": "Consume only the report evidence declared by one assignment. When the assignment declares no reports, it returns state=none. Decision evidence is already typed in the assignment brief and is never report-body input.", "inputSchema": _closed({"assignment_ref": assignment_ref, "cursor": _string(maximum=2048)}, ("assignment_ref",)), "outputSchema": compact_output},
        "publish_plan": publication("plan"),
        "publish_result": publication("result"),
        "publish_documentation": publication("documentation"),
        "record_decision": {"description": "Record an explicit user response only against the server-issued pending binding_ref from open_decision. Copy that scalar byte-for-byte; decision_context is not an input and reconstructed bindings are rejected.", "inputSchema": _closed({"task_ref": task_ref, "binding_ref": _string(minimum=35, maximum=35, pattern=r"^cb_[0-9a-f]{32}$"), "response_original": _string(minimum=0), "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN)}, ("task_ref", "binding_ref", "response_original", "user_language")), "outputSchema": compact_output},
        "assess_governance": {"description": "Record a material advisory task assessment. Ordinary worker stage or rework notes belong to the task timeline, not initiative revision history.", "inputSchema": _closed({"task_ref": task_ref, "mode": _string(enum=GOVERNANCE_MODES, maximum=16), "rationale": _string(minimum=0), "risk_factors": {"type": "array", "items": _string(), "maxItems": MAX_LINKS}}, ("task_ref", "mode")), "outputSchema": compact_output},
        "close_task": {"description": "Record the final task closure aggregate from immutable published evidence and unresolved risks.", "inputSchema": _closed({"task_ref": task_ref, "verdict": _string(enum=CLOSURE_VERDICTS, maximum=32), "evidence": _json_value(), "unresolved_risks": {"type": "array", "items": _string(minimum=0), "maxItems": MAX_LINKS}, "follow_ups": {"type": "array", "items": _string(minimum=0), "maxItems": MAX_LINKS}, "completion_notes": _json_value()}, ("task_ref", "verdict", "evidence")), "outputSchema": compact_output},
    }
    if tuple(contracts) != V12_TOOL_NAMES:
        raise RuntimeError("Cortex v12 public catalogue must contain exactly the eleven semantic tools")
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
