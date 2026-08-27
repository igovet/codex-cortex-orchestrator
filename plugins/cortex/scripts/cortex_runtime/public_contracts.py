"""The complete uniform public contract for the Cortex V12 ledger."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cortex_runtime.model_routing import NATIVE_MODELS, NATIVE_REASONING_EFFORTS
from cortex_runtime.worker_message import packaged_profile_names
from cortex_runtime.v12_contract import (
    CLOSURE_SUBJECTS, DECISION_SUBJECTS, DECISION_TYPES, DIGEST_PATTERN,
    CLOSURE_VERDICTS,
    DEFAULT_PAGE_LIMIT,
    GOVERNANCE_MODES,
    GOVERNANCE_SOURCES,
    IDEMPOTENCY_KEY_MAX_LENGTH,
    IDENTIFIER_MAX_LENGTH,
    IDENTIFIER_PATTERN,
    INITIATIVE_STATUSES,
    JSON_MAX_BYTES,
    MAX_DECISION_IDS, MAX_LINKS,
    MAX_PAGE_LIMIT,
    MAX_REPORT_IDS,
    PROJECT_ROOT_MAX_LENGTH,
    PLAN_REVIEW_POLICIES, REPORT_MODES, REPORT_READ_MAX_BYTES,
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
    "create_task",
    "inspect_task",
    "create_delegation",
    "read_delegation",
    "submit_report",
    "read_reports",
    "set_governance_mode",
    "record_initiative",
    "inspect_governance",
    "submit_governance_closure",
    "record_user_decision",
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
    return _string(maximum=IDEMPOTENCY_KEY_MAX_LENGTH, description="Optional bounded opaque client retry token. Cortex does not parse or validate its meaning; omit it for a new mutation, then reuse only the returned retry_handle for an exact retry.")


def _closed(
    properties: Mapping[str, Any],
    required: tuple[str, ...],
    *,
    description: str = "Closed Cortex V12 public tool input.",
) -> dict[str, Any]:
    """Return one closed, advertised MCP input object schema."""
    return {
        "$schema": _JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "description": description,
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
    }


def _json_value() -> dict[str, Any]:
    """Advertise the exact encoded JSON bound also enforced by V12 storage."""
    return {
        "description": (
            "Opaque bounded JSON value. Cortex validates only finite JSON encoding, "
            f"depth, and the {JSON_MAX_BYTES}-byte bound; it never parses, classifies, "
            "or semantically validates report prose."
        ),
        "maxBytes": JSON_MAX_BYTES,
    }


def _page_arguments() -> dict[str, Any]:
    return {
        "after_sequence": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_LIMIT, "default": DEFAULT_PAGE_LIMIT},
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
    "requirements": "Required bounded opaque English requirements for workers; this is context, not backend workflow authority.",
    "constraints": "Required bounded opaque English constraints, including forbidden actions where applicable; this is context, not backend workflow authority.",
    "acceptance_criteria": "Required bounded opaque English acceptance criteria used by the coordinator when reviewing evidence.",
    "verification_plan": "Bounded English verification expectations for the coordinator and workers.",
    "context": "Bounded JSON task context retained with the task; do not include secrets or raw diagnostic logs.",
    "task_ref": "Preferred compact task locator emitted by create_task. Copy it byte-for-byte for every task-anchored tool; never use a UI-rendered task_id.",
    "idempotency_key": "Optional bounded opaque client retry token. Omit it for a new mutation; reuse only a returned retry_handle for an exact retry with byte-identical arguments.",
    "after_sequence": "Exclusive durable timeline cursor. Use the returned next_sequence unchanged for the next page.",
    "limit": "Maximum scoped timeline events to return in this response (1–200). Values above 200 are rejected; use next_sequence for another page.",
    "role": "Short advisory worker role label selected by the coordinator; it grants no host authority.",
    "profile_name": "Exact packaged advisory profile selected independently from the human role. It must be copied from this tool's enum and load successfully.",
    "scope": "Required concise textual boundary of the delegated worker's ownership.",
    "instructions": "Coordinator-authored bounded non-empty worker instructions, preserved unchanged for durable worker context. Recommended guidance covers: documents to consume first (exact paths and why), applicable requirements, verification contract, ownership constraints, known documentation state, and further documentation discovery. This helpful structure is advisory only: no heading, Markdown, order, language, or section content is parsed as a server admission rule.",
    "parent_delegation_ref": "Optional exact emitted same-task predecessor delegation reference; it is evidence linkage, never a lifecycle gate.",
    "model": "Exact logical model selected by the coordinator. Luna remains explicit in durable data; only native serialization omits its override.",
    "reasoning_effort": "Exact coordinator-selected reasoning effort paired atomically with model; the runtime never escalates it.",
    "mode": "Report upload operation. Use single for one bounded report; for a large report use begin, then sequential append, then finalize; abort only an assembling report.",
    "report_type": "Immutable report kind selected only for single or begin: progress, result, synthesis, or plan. It is fixed after begin and omitted for append, finalize, and abort.",
    "status": "Required only for single or finalize: partial, completed, blocked, or failed. A plan must be finalized with completed before it can receive an approval decision.",
    "content": "Required only for single or append. Supply one finite bounded JSON value (object, array, string, number, boolean, or null); Cortex never parses or semantically validates its report text. Content is returned publicly only by read_reports.",
    "chunk_index": "Zero-based next append index. Use the next_chunk_index acknowledged by the previous append.",
    "section": "Lowercase bounded chunk section label used to filter a report read.",
    "expected_chunk_count": "Final manifest chunk count observed after the final append.",
    "expected_content_digest": "Exact sha256: digest acknowledged by the assembled report; copy it byte-for-byte when finalizing.",
    "abort_reason_en": "English reason for intentionally ending an incomplete report assembly.",
    "supersedes_report_ref": "Optional exact emitted prior plan report replaced by this newly created plan; the referenced report must be a same-task plan.",
    "review_policy": "Optional plan-only review policy, set only on a new single/begin plan. Omit it for every non-plan and for append, finalize, or abort.",
    "sections": "Optional unique report section labels. The continuation cursor is valid only for the same ordered report refs and filters.",
    "cursor": "Opaque read_reports continuation cursor. Copy it byte-for-byte and reuse it only with the exact original report_refs and sections.",
    "max_bytes": "Maximum encoded report body bytes for this page. Zero returns metadata only and never consumes a cursor page.",
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
    "prompt_en": "English prompt or decision context shown by the coordinator to the user.",
    "response_original": "Exact arbitrary-Unicode user response. It is stored privately and is not copied into compact inspection evidence.",
    "response_en": "Coordinator-authored English normalization of the user response; it never replaces response_original.",
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
_COMPACT_REPORT_SCHEMA = _result_object("Compact immutable report record; body content is available only from read_reports.", {
    "report_id": _opaque_record_id("Opaque durable report handle."),
    "task_id": _opaque_task_id("Owning durable task handle."),
    "delegation_id": _opaque_record_id("Owning durable delegation handle."),
    "content_digest": _opaque_digest("SHA-256 digest of the current immutable report manifest.", nullable=True),
    "assembly_state": _string(enum=("assembling", "finalized", "aborted"), maximum=16, description="Current immutable report assembly state."),
    "next_chunk_index": {"type": "integer", "minimum": 0, "description": "Next permitted append position for an assembling report."},
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
        "retry_handle": _idempotency_key(),
        "cursor": _string(maximum=2_048, description="Exact opaque continuation cursor for read_reports only. " + HANDLE_COPY_RULE),
        "next_sequence": {"type": "integer", "minimum": 0, "description": "Exact durable timeline sequence for inspect_task, read_delegation, or inspect_governance. Copy it only into after_sequence. " + HANDLE_COPY_RULE},
        "next_chunk_index": {"type": "integer", "minimum": 0, "description": "Exact next append index for submit_report. " + HANDLE_COPY_RULE},
        "expected_chunk_count": {"type": "integer", "minimum": 1, "description": "Exact finalization chunk count for submit_report. " + HANDLE_COPY_RULE},
        "expected_content_digest": _opaque_digest("Exact finalization manifest digest for submit_report."),
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
_NATIVE_DISPATCH_SCHEMA = _result_object("Exact coordinator-selected native spawn projection; it is data, not a host receipt or lifecycle handle.", {
    "task_name": _string(maximum=64, pattern=r"^[a-z][a-z0-9_]*$", description="Exact server-derived host-safe task_name. Copy native_dispatch.task_name byte-for-byte into spawn_agent; never derive, sanitize, or replace it."),
    "selection": _result_object("Exact logical model and effort selected by the coordinator.", {
        "model": _string(enum=NATIVE_MODELS, maximum=64, description="Exact logical model selection; no fallback is inferred."),
        "reasoning_effort": _string(enum=NATIVE_REASONING_EFFORTS, maximum=16, description="Exact selected effort; it is never escalated by the ledger."),
    }),
    "native_arguments": _result_object("Byte-exact host spawn arguments; Luna omits only its native model override.", {
        "task_name": _string(maximum=64, pattern=r"^[a-z][a-z0-9_]*$", description="Exact server-derived host-safe task_name, identical to native_dispatch.task_name."),
        "message": _string(maximum=TEXT_MAX_LENGTH, description="Exact rendered worker message."),
        "reasoning_effort": _string(enum=NATIVE_REASONING_EFFORTS, maximum=16, description="Exact native effort argument."),
        "model": _string(enum=NATIVE_MODELS, maximum=64, description="Native override when supplied; omitted for configured-default Luna."),
        "fork_turns": _string(enum=("none",), maximum=8, description="Required isolated native spawn setting."),
    }),
})
_WORKER_BRIEF_SCHEMA = _result_object("Coordinator-owned recovery brief and renderer proof; it creates no host lifecycle record or dispatch handle.", {
    "delegation_id": _opaque_record_id("Durable delegation handle for this worker brief."),
    "task_id": _opaque_task_id("Durable task handle for this worker brief."),
    "native_task_name": _string(maximum=64, pattern=r"^[a-z][a-z0-9_]*$", description="Persisted exact server-derived native task name; it is not a lifecycle receipt."),
    "input_report_refs": _result_array("Exact finalized predecessor report references that the worker must read through read_reports before use.", _result_object("One immutable handoff report reference.", {
        "report_id": _opaque_record_id("Exact predecessor report ID."),
        "content_digest": _opaque_digest("Exact finalized predecessor report manifest digest."),
        "assembly_state": _string(enum=("finalized",), maximum=16),
    })),
    "renderer": _RENDERER_SCHEMA,
    "native_dispatch": _NATIVE_DISPATCH_SCHEMA,
})


_RESULT_PROPERTY_SCHEMAS: dict[str, dict[str, Any]] = {
    "task": _result_object("Durable task header, including canonical V12 project anchor and immutable task contract.", {
        "task_id": _opaque_task_id("Opaque durable task handle."),
        "project_hash": _string(maximum=64, pattern=r"^[0-9a-f]{64}$", description="V12 project-shard digest; it is not caller authority."),
    }),
    "delegations": _result_array("Compact delegation references selected by the returned task chronology page.", _result_object("Compact durable delegation reference.", {
        "delegation_id": _opaque_record_id("Opaque durable delegation handle."), "task_id": _opaque_task_id("Owning task handle."), "native_task_name": _string(maximum=64, pattern=r"^[a-z][a-z0-9_]*$", description="Persisted exact server-derived native task name; it is not a lifecycle receipt."),
    })),
    "delegation": _result_object("Durable delegation record; full instructions are returned only for the selected delegation.", {
        "delegation_id": _opaque_record_id("Opaque durable delegation handle."), "task_id": _opaque_task_id("Owning task handle."),
        "native_task_name": _string(maximum=64, pattern=r"^[a-z][a-z0-9_]*$", description="Persisted exact server-derived native task name; it is not a lifecycle receipt."),
        "profile_name": _string(maximum=ROLE_MAX_LENGTH, description="Exact packaged advisory profile distinct from role."),
        "model": _string(enum=NATIVE_MODELS, maximum=64, description="Exact coordinator-selected logical model."),
        "reasoning_effort": _string(enum=NATIVE_REASONING_EFFORTS, maximum=16, description="Exact coordinator-selected effort."),
    }),
    "worker_brief": _WORKER_BRIEF_SCHEMA,
    "renderer": _RENDERER_SCHEMA,
    "native_dispatch": _NATIVE_DISPATCH_SCHEMA,
    "report": _COMPACT_REPORT_SCHEMA,
    "reports": _result_array("Requested report records in caller order; report chunks and bodies appear only in read_reports.", _COMPACT_REPORT_SCHEMA),
    "consumption_receipts": _result_array("Structural report-read receipts created by this call.", _REPORT_CONSUMPTION_RECEIPT_SCHEMA),
    "decisions": _result_array("Compact user-decision evidence selected by the returned task chronology page.", _result_object("Compact immutable decision record.", {
        "decision_id": _opaque_record_id("Opaque durable decision handle."), "task_id": _opaque_task_id("Owning task handle."),
        "subject_id": _opaque_record_id("Opaque selected durable subject handle."), "subject_digest": _opaque_digest("Exact immutable subject digest.", nullable=True),
    })),
    "assessment": _result_object("New advisory governance assessment; it never blocks another operation.", {
        "assessment_id": _opaque_record_id("Opaque durable assessment handle."), "task_id": _opaque_task_id("Anchored task handle."),
    }),
    "continuations": _result_array("Compact persisted delegation recovery status. It is ledger evidence only: reconcile the exact native name with the host before any spawn, never infer lifecycle from this result, and do not spawn while host state is ambiguous. Read the selected delegation for its exact worker brief/native dispatch. Native commentary alone cannot advance a durable successor: recovery must end in a finalized report, explicit blocked/partial handoff, or a parent-linked replacement.", _result_object("One recoverable delegation status.", {
        "delegation": _result_object("Exact durable delegation selected for recovery.", {
            "delegation_id": _opaque_record_id("Opaque durable delegation handle."),
            "task_id": _opaque_task_id("Owning task handle."),
            "native_task_name": _string(maximum=64, pattern=r"^[a-z][a-z0-9_]*$"),
            "profile_name": _string(maximum=ROLE_MAX_LENGTH),
            "model": _string(enum=NATIVE_MODELS, maximum=64),
            "reasoning_effort": _string(enum=NATIVE_REASONING_EFFORTS, maximum=16),
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
    "closure": _result_object("Recorded advisory task or initiative closure.", {
        "closure_id": _opaque_record_id("Opaque durable closure handle."), "task_id": _opaque_task_id("Anchored task handle."),
        "subject_id": _opaque_record_id("Closed durable subject handle."),
    }),
    "next_action": _result_object("Optional exact closure follow-up. After an initiative closure, it may offer a compact suggested subject for a separate task closure; it is not a complete callable payload and never requires that closure or orders safe coordination.", {
        "tool": _string(enum=("submit_governance_closure",), maximum=32),
        "state": _string(enum=("task_closed",), maximum=16),
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
    "assembly_state": {"type": "string", "enum": ["assembling", "finalized", "aborted"], "description": "Current immutable report assembly state after this mutation."},
    "next_chunk_index": {"type": "integer", "minimum": 0, "description": "Next permitted append index; copy it unchanged into the next append."},
    "accepted_chunk_index": {"type": "integer", "minimum": 0, "description": "Append index accepted by this report receipt."},
    "chunk_digest": _opaque_digest("SHA-256 digest of the accepted append chunk."),
    "chunk_bytes": {"type": "integer", "minimum": 0, "description": "Encoded byte length of the accepted append chunk."},
    "expected_chunk_count": {"type": "integer", "minimum": 1, "description": "Exact finalization chunk count after the accepted append."},
    "expected_content_digest": _opaque_digest("Exact finalization manifest digest after the accepted append."),
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
    common = ("delegation_ref",)

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
    create_tail = ("chunk_index", "section", "expected_chunk_count", "expected_content_digest", "abort_reason_en")
    alternatives: list[dict[str, Any]] = []
    alternatives.extend((
        create_shape(mode="single", report_types=("plan",), required=("report_type", "status", "content"), forbidden=create_tail),
        create_shape(mode="single", report_types=("progress", "result", "synthesis"), required=("report_type", "status", "content"), forbidden=(*create_tail, *plan_fields)),
    ))
    for report_types, forbidden in (
        (("plan",), ("content", "status", *create_tail)),
        (("progress", "result", "synthesis"), ("content", "status", *create_tail, *plan_fields)),
    ):
        alternatives.append(create_shape(mode="begin", report_types=report_types, required=("report_type",), forbidden=forbidden))
    alternatives.extend((
        {
            "type": "object",
            "properties": {"mode": {"const": "append", "description": _PROPERTY_DESCRIPTIONS["mode"]}},
            "required": [*common, "mode", "report_ref", "chunk_index", "section", "content"],
            "allOf": _forbid_properties("report_type", "status", "expected_chunk_count", "expected_content_digest", "abort_reason_en", *plan_fields),
        },
        {
            "type": "object",
            "properties": {"mode": {"const": "finalize", "description": _PROPERTY_DESCRIPTIONS["mode"]}},
            "required": [*common, "mode", "report_ref", "expected_chunk_count", "expected_content_digest", "status"],
            "allOf": _forbid_properties("report_type", "content", "chunk_index", "section", "abort_reason_en", *plan_fields),
        },
        {
            "type": "object",
            "properties": {"mode": {"const": "abort", "description": _PROPERTY_DESCRIPTIONS["mode"]}},
            "required": [*common, "mode", "report_ref", "abort_reason_en"],
            "allOf": _forbid_properties("report_type", "status", "content", "chunk_index", "section", "expected_chunk_count", "expected_content_digest", *plan_fields),
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
            "description": "Initialize one durable task from its complete task/result contract. project_root is required only in this first call and must be the user-selected absolute canonical cwd/webhook root; Cortex stores the resolved root on the task. Copy the returned task_ref byte-for-byte for later task-anchored calls, and omit project_root from every later public tool call. Internal contract prose is English, while user_request_original stays exact and user_language is a BCP-47 tag such as ru. No undeclared wrapper, alias, or extra field is accepted.",
            "inputSchema": _closed(
                {
                    "project_root": _string(maximum=PROJECT_ROOT_MAX_LENGTH, description=_PROPERTY_DESCRIPTIONS["project_root"]),
                    "objective": _string(description=_PROPERTY_DESCRIPTIONS["objective"]),
                    "user_request_original": _string(description=_PROPERTY_DESCRIPTIONS["user_request_original"]),
                    "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description=_PROPERTY_DESCRIPTIONS["user_language"]),
                    "requirements": _text_array(minimum=1),
                    "constraints": _text_array(minimum=1),
                    "acceptance_criteria": _text_array(minimum=1),
                    "verification_plan": _text_array(minimum=1),
                    "context": _json_value(),
                    "idempotency_key": idempotency_key,
                },
                ("project_root", "objective", "user_request_original", "user_language", "requirements", "constraints", "acceptance_criteria", "verification_plan"),
            ),
            "outputSchema": _tool_output_schema("task", "replayed", optional_success_fields=("human_view",)),
        },
        "inspect_task": {
            "description": "Read one task header and a bounded task-scoped chronology using task_ref. Report entries are compact references; use read_reports for bodies. continuations return compact persisted recovery status only; use read_delegation for an exact worker brief/native dispatch after selecting its emitted delegation reference. Neither surface proves native lifecycle or authorizes a duplicate spawn.",
            "inputSchema": _closed({"task_ref": task_ref, **_page_arguments()}, ("task_ref",)),
            "outputSchema": _tool_output_schema("task", "delegations", "continuations", "reports", "decisions", "consumption_receipts", "timeline", "next_sequence", "has_more", optional_success_fields=("human_view",)),
        },
        "create_delegation": {
            "description": "Create one new model-authored delegation and return one compact, self-sufficient native-dispatch receipt plus loaded renderer proof. The complete rendered worker message occurs only in native_dispatch.native_arguments.message, so normal dispatch needs no read_delegation call. This creation-only tool never retrieves or replays an existing delegation: reuse the original complete payload only for an exact idempotent retry. read_delegation retains the verbose recovery brief; reconcile its persisted delegation_ref with the host, resume or wait if present, and spawn once only after absence is proven.",
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
                    "model": _string(enum=NATIVE_MODELS, maximum=64, description="Exact logical coordinator-selected model. Luna remains explicit in durable data."),
                    "reasoning_effort": _string(enum=NATIVE_REASONING_EFFORTS, maximum=16, description="Exact coordinator-selected effort, preserved unchanged by native serialization."),
                    "idempotency_key": idempotency_key,
                },
                ("task_ref", "objective", "role", "profile_name", "scope", "instructions", "model", "reasoning_effort"),
            ),
            "outputSchema": _tool_output_schema("delegation", "native_dispatch", "renderer", "replayed", optional_success_fields=("human_view",)),
        },
        "read_delegation": {
            "description": "Retrieve a read-only durable delegation, its trusted worker/native-dispatch payload, and bounded delegation-scoped chronology with its exact emitted delegation_ref.",
            "inputSchema": _closed({"delegation_ref": delegation_id, **_page_arguments()}, ("delegation_ref",)),
            "outputSchema": _tool_output_schema("delegation", "worker_brief", "reports", "consumption_receipts", "timeline", "next_sequence", "has_more", optional_success_fields=("human_view",)),
        },
        "submit_report": {
            "description": "Create, assemble, finalize, or abort one immutable bounded report using this single canonical field set. The worker alone calls this with its exact emitted delegation_ref, which resolves the authoritative task; never supply task_ref or a canonical ID. A one-call report requires delegation_ref + mode=single + report_type=plan + status=completed + one bounded opaque JSON content value. review_policy is optional. Submit these as top-level fields only: do not use wrappers or alternate names such as report, body, title, reportType, delegationId, or taskId. Large report: begin needs delegation_ref + mode=begin + report_type; each append needs the begin-returned report_ref + exact next_chunk_index + section + content; finalize needs that report_ref + status + exact expected_chunk_count and expected_content_digest returned by append. The schema checks references and operation fields; Cortex never semantically validates report prose.",
            "inputSchema": _closed(
                {
                    "delegation_ref": delegation_id,
                    "mode": _string(enum=REPORT_MODES, maximum=16),
                    "report_type": _string(enum=REPORT_TYPES, maximum=16),
                    "status": _string(enum=REPORT_STATUSES, maximum=16),
                    "content": _json_value(),
                    "report_ref": report_id,
                    "chunk_index": {"type": "integer", "minimum": 0, "maximum": 255},
                    "section": _string(maximum=REPORT_SECTION_MAX_LENGTH),
                    "expected_chunk_count": {"type": "integer", "minimum": 1, "maximum": 256},
                    "expected_content_digest": _string(maximum=71, pattern=DIGEST_PATTERN),
                    "abort_reason_en": _string(maximum=4_096),
                    "supersedes_report_ref": report_id,
                    "review_policy": _string(enum=PLAN_REVIEW_POLICIES, maximum=16),
                    "idempotency_key": idempotency_key,
                },
                ("delegation_ref",),
            ) | {"allOf": [{"oneOf": _report_operation_schema()}]},
            "outputSchema": _tool_output_schema("report", "replayed", optional_success_fields=("assembly_state", "next_chunk_index", "accepted_chunk_index", "chunk_digest", "chunk_bytes", "expected_chunk_count", "expected_content_digest", "human_view", "approval_view")),
        },
        "read_reports": {
            "description": "Read bounded complete report chunks in requested report order with the canonical report_refs and integer max_bytes fields; report_refs resolve and verify one authoritative task.",
            "inputSchema": _closed({
                "report_refs": _entity_ref_array("report", minimum=1, maximum=MAX_REPORT_IDS),
                "sections": {"type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": True, "items": _string(maximum=REPORT_SECTION_MAX_LENGTH, pattern=REPORT_SECTION_PATTERN)},
                "cursor": _string(maximum=2_048),
                    "max_bytes": {"type": "integer", "minimum": 0, "maximum": REPORT_READ_MAX_BYTES, "default": REPORT_READ_MAX_BYTES, "description": "Maximum encoded report body bytes for this page. Omit unless a smaller downstream-worker budget is genuinely needed."},
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
            "description": "Create or revise an advisory initiative. goal is required only for creation; an update with initiative_ref preserves the existing goal when goal is omitted.",
            "inputSchema": _closed(
                {"task_ref": task_ref, "initiative_ref": initiative_id, "goal": _string(), "parent_initiative_ref": initiative_id, "risk": _string(minimum=0), "status": _string(enum=INITIATIVE_STATUSES, maximum=16), "dependency_refs": _entity_ref_array("initiative", maximum=MAX_LINKS), "linked_task_refs": {"type": "array", "minItems": 0, "maxItems": MAX_LINKS, "uniqueItems": True, "items": _opaque_task_ref("Exact compact related task locator.")}, "linked_delegation_refs": _entity_ref_array("delegation", maximum=MAX_LINKS), "linked_report_refs": _entity_ref_array("report", maximum=MAX_LINKS), "linked_decision_refs": _entity_ref_array("decision", maximum=MAX_LINKS), "notes": _json_value(), "idempotency_key": idempotency_key},
                ("task_ref",),
            ) | {"oneOf": [{"type": "object", "required": ["task_ref", "goal"], "allOf": _forbid_properties("initiative_ref")}, {"type": "object", "required": ["task_ref", "initiative_ref"]}]},
            "outputSchema": _tool_output_schema("initiative", "warnings", "replayed"),
        },
        "inspect_governance": {
            "description": "Read task governance, or an initiative selected within the ledger anchored by task_ref, with a bounded scoped chronology and effective projection.",
            "inputSchema": _closed({"task_ref": task_ref, "initiative_ref": initiative_id, **_page_arguments()}, ("task_ref",)),
            "outputSchema": _tool_output_schema("initiatives", "assessments", "closures", "initiative_revisions", "links", "warnings", "projection", "timeline", "next_sequence", "has_more", optional_success_fields=("human_view",)),
        },
        "submit_governance_closure": {
            "description": "Append one advisory closure anchored by task_ref. A completed Cortex task must attempt closure and verify its intended inspection before it is described as closure-confirmed. If closure storage or inspection is unavailable, return an honest degraded result and never claim confirmation. Governance remains nonblocking: a closure failure does not prohibit safe work or an honest answer. Task and initiative subjects have separate validated shapes. An initiative closure may return a compact suggested subject for a separate task closure, never a complete callable payload.",
            "inputSchema": _closed(
                {"task_ref": task_ref, "subject_type": _string(enum=CLOSURE_SUBJECTS, maximum=16), "subject_ref": _string(maximum=14, pattern=r"^(?:t|i)_[0-9a-f]{12}$", description="Exact compact task or initiative subject selected by subject_type."), "verdict": _string(enum=CLOSURE_VERDICTS, maximum=32), "evidence": _json_value(), "unresolved_risks": {"type": "array", "items": _string(minimum=0), "maxItems": MAX_LINKS}, "follow_ups": {"type": "array", "items": _string(minimum=0), "maxItems": MAX_LINKS}, "initiative_status": _string(enum=INITIATIVE_STATUSES, maximum=16), "completion_notes": _json_value(), "idempotency_key": idempotency_key},
                ("task_ref", "subject_type", "subject_ref", "verdict", "evidence"),
            ) | {"oneOf": [{"type": "object", "properties": {"subject_type": {"const": "task"}, "subject_ref": task_ref}, "required": ["subject_type", "subject_ref"], "allOf": _forbid_properties("initiative_status")}, {"type": "object", "properties": {"subject_type": {"const": "initiative"}, "subject_ref": initiative_id}, "required": ["subject_type", "subject_ref"]}]},
            "outputSchema": _tool_output_schema("closure", "initiative", "warnings", "next_action", "replayed", success_property_overrides={"initiative": _RESULT_PROPERTY_SCHEMAS["initiative"] | {"type": ["object", "null"], "description": "Updated initiative for an initiative closure, or null for a task closure."}}),
        },
        "record_user_decision": {
            "description": "Append an ordinary-chat user decision asserted by the coordinator using one canonical field set. Plan and report decisions need their exact immutable subject digest; task, delegation, and initiative decisions do not. Only a plan approve decision additionally requires the complete returned ready approval_view relation: approval_handle, approval_view_content_digest, and approval_view_source_sequence. A revision or cancellation remains bound to the immutable plan digest but is not blocked by later unrelated chronology. Original task text, silence, inferred consent, wrappers, aliases, and mixed request shapes are invalid. The approval handle proves only the ready-view relation, not a host-authenticated user turn.",
            "inputSchema": _closed(
                {
                    "task_ref": task_ref,
                    "subject_type": _string(enum=DECISION_SUBJECTS, maximum=16),
                    "subject_ref": _string(maximum=14, pattern=r"^[tdriu]_[0-9a-f]{12}$", description="Exact compact reference for the selected subject; task decisions use the anchored task_ref."),
                    "subject_digest": _string(minimum=0, maximum=71, pattern=DIGEST_PATTERN, description="Required for plan and report subjects; binds the response to an immutable revision."),
                    "decision_type": _string(enum=DECISION_TYPES, maximum=32),
                    "prompt_en": _string(minimum=0, description="English prompt or decision context shown by the coordinator."),
                    "response_original": _string(minimum=0, description="Exact arbitrary-Unicode user response."),
                    "response_en": _string(minimum=0, description="Coordinator-authored English normalization; it never replaces response_original."),
                    "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH),
                    "approval_handle": _identifier(description="Exact opaque approval_view handle. Required only for decision_type=approve; never construct or reuse for a different plan/view."),
                    "approval_view_content_digest": _string(minimum=0, maximum=71, pattern=DIGEST_PATTERN, description="Exact ready approval_view.content_digest. Required only for decision_type=approve."),
                    "approval_view_source_sequence": {"type": "integer", "minimum": 0, "description": "Exact ready approval_view.source_sequence. Required only for decision_type=approve."},
                    "supersedes_decision_ref": decision_id,
                    "idempotency_key": idempotency_key,
                },
                ("task_ref", "subject_type", "subject_ref", "decision_type", "prompt_en", "response_original", "response_en", "user_language"),
            ) | {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "subject_type": {"enum": ["task", "delegation", "initiative"]},
                            "subject_ref": _string(maximum=14, pattern=r"^(?:t|d|i)_[0-9a-f]{12}$"),
                        },
                        "required": ["subject_type", "subject_ref"],
                        "allOf": _forbid_properties("subject_digest", "approval_handle", "approval_view_content_digest", "approval_view_source_sequence"),
                    },
                    {
                        "type": "object",
                        "properties": {
                            "subject_type": {"const": "report"},
                            "subject_ref": _string(maximum=14, pattern=r"^r_[0-9a-f]{12}$"),
                        },
                        "required": ["subject_type", "subject_ref", "subject_digest"],
                        "allOf": _forbid_properties("approval_handle", "approval_view_content_digest", "approval_view_source_sequence"),
                    },
                    {
                        "type": "object",
                        "properties": {
                            "subject_type": {"const": "plan"},
                            "subject_ref": _string(maximum=14, pattern=r"^r_[0-9a-f]{12}$"),
                            "decision_type": {"not": {"const": "approve"}},
                        },
                        "required": ["subject_type", "subject_ref", "subject_digest", "decision_type"],
                        "allOf": _forbid_properties("approval_handle", "approval_view_content_digest", "approval_view_source_sequence"),
                    },
                    {
                        "type": "object",
                        "properties": {
                            "subject_type": {"const": "plan"},
                            "subject_ref": _string(maximum=14, pattern=r"^r_[0-9a-f]{12}$"),
                            "decision_type": {"const": "approve"},
                        },
                        "required": [
                            "subject_type", "subject_ref", "subject_digest", "decision_type",
                            "approval_handle", "approval_view_content_digest", "approval_view_source_sequence",
                        ],
                    },
                ],
            },
            "outputSchema": _tool_output_schema("decision", "replayed", optional_success_fields=("human_view",)),
        },
    }
    if tuple(contracts) != V12_TOOL_NAMES:
        raise RuntimeError("Cortex v12 public catalogue must contain exactly the eleven canonical tools")
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
