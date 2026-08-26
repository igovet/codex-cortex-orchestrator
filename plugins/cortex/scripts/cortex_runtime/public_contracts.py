"""Canonical model-facing MCP contracts.

Every object returned by :func:`build_public_contracts` is the exact object
advertised through ``tools/list``.  Runtime transport validation consumes that
same ``inputSchema`` object; there is no parallel required/allowed-field table.
Private routing metadata only selects an existing backend operation and any
server-owned discriminator that must be injected after validation.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from cortex_runtime.pagination import CURSOR_PATTERN
from cortex_runtime.finding_severity import PUBLIC_FINDING_SEVERITIES
from cortex_runtime.model_routing import (
    model_effort_pair_text,
    model_effort_registry,
    supported_effort_sequence,
)
from cortex_runtime.verification_contract import WORKER_VERIFICATION_KINDS
from cortex_runtime.v11_submission import (
    COORDINATOR_REF_PATTERN,
    DISPATCH_REF_PATTERN,
    MAX_DIAGNOSTICS,
    REPAIR_HANDLE_LENGTH,
    REPAIR_HANDLE_PATTERN,
)
from cortex_runtime.v11_responses import (
    NATIVE_DISPATCH_WAIT_INSTRUCTION,
    READ_AFTER_WAIT_INSTRUCTION,
    SAME_CHILD_WAIT_INSTRUCTION,
    WAIT_BEFORE_READ_INSTRUCTION,
)


PUBLIC_MAX_WORKERS_PER_WAVE = 8
PUBLIC_MAX_WAVES = 32
CANONICAL_COMPLEXITIES = ("C1", "C2", "C3")
PUBLIC_AUTHORITY_FORMATS = frozenset({
    "cortex-coordinator-ref",
    "cortex-dispatch-ref",
})
_INTERNAL_QUESTION_CATEGORIES = frozenset({
    "internal", "cortex", "cortex_internal", "model", "profile", "retry",
    "dependency", "schema", "runtime_recovery",
})


def is_internal_question_category(value: object) -> bool:
    """Classify known technical-recovery labels without inspecting question text."""
    return (
        isinstance(value, str)
        and value.strip().casefold().replace("-", "_") in _INTERNAL_QUESTION_CATEGORIES
    )

# This is the canonical model-facing RFC 6902 item schema.  The public
# contract deliberately stays flat and combinator-free; operation-specific
# value presence is enforced by the repair backend against the issued
# diagnostic rather than encoded as a oneOf branch here.
PUBLIC_PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "op": {
            "type": "string",
            "enum": ["add", "replace", "remove"],
            "description": (
                "Exact RFC6902 operation selected from the matching diagnostic's allowed_ops; "
                "the property name is op, never operation, patch, or patch_op."
            ),
        },
        "path": {
            "type": "string",
            "pattern": r"^(?:/(?:[^~/]|~[01])*)+$",
            "description": (
                "Exact RFC6901 semantic path copied from diagnostic repair_pointer; "
                "the property name is path, never repair_pointer."
            ),
        },
        "value": {
            "description": (
                "Required for add and replace and must satisfy the diagnostic field_schema; "
                "forbidden for remove. Semantic strings may use any language accepted by field_schema."
            ),
        },
    },
    "required": ["op", "path"],
}


def _string(
    *,
    enum: Sequence[str] | None = None,
    pattern: str | None = None,
    minimum: int = 1,
    maximum: int = 65_536,
    default: str | None = None,
    format_name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "string",
        "minLength": minimum,
        "maxLength": maximum,
    }
    if enum is not None:
        schema["enum"] = list(enum)
    if pattern is not None:
        schema["pattern"] = pattern
    if default is not None:
        schema["default"] = default
    if format_name is not None:
        schema["format"] = format_name
    if description is not None:
        schema["description"] = description
    return schema


def _strings(
    *,
    maximum_items: int = 128,
    item_maximum: int = 8_192,
    minimum_items: int = 0,
) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": minimum_items,
        "maxItems": maximum_items,
        "uniqueItems": True,
        "items": _string(maximum=item_maximum),
    }


def _closed(properties: Mapping[str, Any], required: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
    }


def _flat_patch_schema() -> dict[str, Any]:
    """Return the irreducible closed RFC6902 item without combinators."""
    return copy.deepcopy(PUBLIC_PATCH_SCHEMA)


def _contract(
    description: str,
    input_schema: dict[str, Any],
    base_operation: str,
    *,
    expose_flat_argument_names: bool = False,
    **injected_arguments: Any,
) -> dict[str, Any]:
    if expose_flat_argument_names:
        properties = input_schema.get("properties")
        required = input_schema.get("required")
        if (
            input_schema.get("type") != "object"
            or input_schema.get("additionalProperties") is not False
            or not isinstance(properties, Mapping)
            or not isinstance(required, list)
            or any(not isinstance(name, str) or name not in properties for name in required)
        ):
            raise ValueError("flat argument-name projection requires one closed object schema")
        authority_names = {
            name for name, field_schema in properties.items()
            if isinstance(field_schema, Mapping)
            and field_schema.get("format") in PUBLIC_AUTHORITY_FORMATS
        }
        required_names = [f"`{name}`" for name in required if name not in authority_names]
        optional_names = [
            f"`{name}`" for name in properties
            if name not in required and name not in authority_names
        ]
        description_parts = [description.rstrip()]
        if required_names:
            description_parts.append(
                "Exact required semantic inputSchema properties: "
                + ", ".join(required_names)
                + "."
            )
        if optional_names:
            description_parts.append(
                "Optional semantic inputSchema properties: "
                + ", ".join(optional_names)
                + "."
            )
        if required_names or optional_names:
            description_parts.append(
                "Use these exact schema-derived semantic names; shortened aliases are invalid."
            )
        if authority_names:
            description_parts.append(
                "Reuse the exact same server-issued worker authority input used for the successful "
                "read_dispatch_briefing call; its property name and wire shape remain exclusively "
                "in the required inputSchema."
            )
        description = " ".join(description_parts)
    audience = "coordinator" if base_operation in {
        "start_orchestration", "continue_orchestration",
        "manage_orchestration", "manage_governance",
    } else "worker"
    if base_operation == "read_worker_result" and injected_arguments.get("action") == "read_wave":
        audience = "coordinator"
    action = injected_arguments.get("action")
    prerequisite = "coordinator"
    if base_operation == "start_orchestration":
        prerequisite = "none"
    elif audience == "worker":
        prerequisite = "worker"
    if base_operation == "worker_question" and action == "poll":
        prerequisite = "question"
    elif base_operation == "complete_attempt" and action == "repair":
        prerequisite = "repair"
    elif base_operation == "read_worker_result" and action in {"list_reports", "read_predecessor"}:
        prerequisite = "predecessor"
    terminal = (
        (base_operation == "complete_attempt")
        or action in {"deactivate", "finalize_bootstrap_failure", "finalize_worker_failure"}
    )
    return {
        "description": description,
        "inputSchema": input_schema,
        "base_operation": base_operation,
        "injected_arguments": dict(injected_arguments),
        "audience": audience,
        "execution": {"prerequisite": prerequisite, "terminal": terminal},
    }


def build_public_contracts(
    *,
    agents: Mapping[str, Any],
    operation_kinds: Mapping[str, Any],
    model_routing: Mapping[str, Any],
    available_gates: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the single ordered registry used by discovery and execution."""
    gates = sorted(set(available_gates or {
        "scope", "plan", "discover", "architecture", "database_architecture",
        "implementation", "qa", "security", "performance", "accessibility",
        "ux", "review", "documentation", "close", "governance_activation",
        "governance_close",
    }))
    profiles = sorted(str(name) for name in agents)
    canonical_operation_kinds = tuple(
        str(operation_kind)
        for operation_kind in operation_kinds
        if isinstance(operation_kind, str) and operation_kind
    )
    if not canonical_operation_kinds:
        raise ValueError("profiles must publish at least one operation kind")
    if len(set(canonical_operation_kinds)) != len(canonical_operation_kinds):
        raise ValueError("profile operation kinds must be unique")
    model_efforts = model_effort_registry(model_routing)
    model_effort_guidance = (
        "The coordinator must select one exact supported model/effort pair: "
        + model_effort_pair_text(model_routing)
        + ". Luna uses the attested native default; Terra and Sol require an explicit native model override."
    )
    canonical_operation_kind_set = set(canonical_operation_kinds)
    for profile_name, profile in agents.items():
        profile_operation_kinds = profile.get("operation_kinds") if isinstance(profile, Mapping) else None
        if (
            not isinstance(profile_operation_kinds, list)
            or not profile_operation_kinds
            or any(
                not isinstance(operation_kind, str)
                or operation_kind not in canonical_operation_kind_set
                for operation_kind in profile_operation_kinds
            )
        ):
            raise ValueError(f"profile has invalid operation kinds: {profile_name}")

    task_ref = _string(pattern=r"^task-[0-9a-f]{12}$", maximum=17, format_name="cortex-task-ref")
    coordinator_ref = _string(
        pattern=COORDINATOR_REF_PATTERN,
        maximum=64,
        format_name="cortex-coordinator-ref",
    )
    dispatch_ref = _string(
        pattern=DISPATCH_REF_PATTERN,
        maximum=33,
        format_name="cortex-dispatch-ref",
    )
    cursor = _string(pattern=CURSOR_PATTERN, maximum=520, format_name="cortex-page-cursor")
    question_ref = _string(
        pattern=r"^question-[A-Za-z0-9._:-]{1,160}$",
        maximum=180,
        format_name="cortex-question-ref",
    )
    attempt_result_ref = _string(
        pattern=r"^attempt-result-[A-Za-z0-9._:-]{1,160}$",
        maximum=180,
        format_name="cortex-attempt-result-ref",
    )
    report_ref = _string(
        pattern=r"^report-v1-[0-9a-f]{64}$",
        maximum=74,
        format_name="cortex-worker-report-ref",
        description=(
            "Opaque server-issued report reference returned by list_worker_reports. "
            "It is valid only for the current authorized dispatch."
        ),
    )
    artifact_ref = _string(
        pattern=r"^artifact-[0-9a-f]{32}$",
        maximum=41,
        format_name="cortex-artifact-ref",
        description=(
            "Exact durable artifact reference returned by an artifact listing or explicitly by a "
            "canonical report. AttemptResult evidence references are not artifact references."
        ),
    )
    initiative_ref = _string(
        pattern=r"^initiative-[A-Za-z0-9_.:-]+$",
        maximum=180,
        format_name="cortex-initiative-ref",
    )
    record_ref = _string(
        pattern=r"^record-[A-Za-z0-9_.:-]+$",
        maximum=180,
        format_name="cortex-record-ref",
    )
    common_coordinator = {"task_ref": task_ref, "coordinator_ref": coordinator_ref}

    worker = _closed({
        "objective": _string(maximum=16_384, default="Complete the assigned bounded work."),
        "profile": _string(enum=profiles, maximum=160),
        "operation_kind": _string(
            enum=canonical_operation_kinds,
            maximum=16,
            description=(
                "Semantic operation selected by the coordinator. Cortex compiles it against "
                "the selected profile capability before creating any dispatch."
            ),
        ),
        "model": _string(
            enum=tuple(model_efforts), maximum=64,
            description=model_effort_guidance,
        ),
        "reasoning_effort": _string(
            enum=supported_effort_sequence(model_routing),
            maximum=16,
            description=model_effort_guidance,
        ),
    }, ["objective", "profile", "operation_kind", "model", "reasoning_effort"])
    wave = _closed({
        "phase_kind": _string(
            enum=gates,
            maximum=80,
            description=(
                "Repeatable semantic phase kind selected by the coordinator, never an identity. "
                "Cortex assigns every unique phase, wave, and global index reference."
            ),
        ),
        "workers": {
            "type": "array",
            "minItems": 1,
            "maxItems": PUBLIC_MAX_WORKERS_PER_WAVE,
            "items": worker,
        },
    }, ["phase_kind", "workers"])

    contracts: dict[str, dict[str, Any]] = {
        "start_orchestration": _contract(
            "Compile and start one durable task from a coordinator-authored semantic wave plan. Phase kinds may repeat and are never identifiers. The coordinator selects every worker profile, operation kind, model, and effort; Cortex assigns executable identities, derives dependency/context references, validates profile capability, and only then creates dispatches. Returns private coordinator authority and the first lifecycle action. "
            + NATIVE_DISPATCH_WAIT_INSTRUCTION
            + " An explicit Terra/Sol model override is mandatory, while Luna deliberately omits the override and uses the verified configured default.",
            _closed({
                "user_request": _string(maximum=65_536, default="Complete the requested work."),
                "acceptance_criteria": {
                    "type": "array",
                    "description": (
                        "Optional coordinator-authored acceptance items. Cortex preserves the "
                        "normalized non-empty strings in exact order as immutable task authority; "
                        "server baselines remain separate and never replace these requirements."
                    ),
                    "items": _string(minimum=1, maximum=65_536),
                },
                "verification": {
                    "type": "array",
                    "description": (
                        "Optional coordinator-authored verification items. Cortex preserves the "
                        "normalized non-empty strings in exact order as immutable task authority; "
                        "server baselines remain separate and never replace these checks."
                    ),
                    "items": _string(minimum=1, maximum=65_536),
                },
                "waves": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": PUBLIC_MAX_WAVES,
                    "items": wave,
                },
                "plan_approval": _string(enum=("auto", "required"), maximum=16),
                "governance_mode": _string(enum=("auto", "required", "minimal"), maximum=16),
                "complexity": _string(
                    enum=CANONICAL_COMPLEXITIES,
                    maximum=2,
                    description=(
                        "Canonical task complexity. Copy an explicit C1, C2, or C3 user choice; "
                        "omit only when no explicit choice exists, in which case Cortex defaults to C2."
                    ),
                ),
            }, ["user_request", "waves"]),
            "start_orchestration",
        ),
        "continue_orchestration": _contract(
            "Advance the active durable task after its current wave has reached terminal native completion, or exactly once when inspect_orchestration/read_worker_wave returns continue to reconcile an interrupted same-child response delivery. After a same-incarnation compaction boundary, inspect_orchestration must complete first. In that recovery case do not wait, reread, or create a child first. Cortex derives the active step and canonical result references from the task ledger. When it returns dispatches, "
            + NATIVE_DISPATCH_WAIT_INSTRUCTION
            + " When it returns read_worker_wave, " + READ_AFTER_WAIT_INSTRUCTION
            + " An explicit Terra/Sol model override is mandatory, while Luna deliberately omits the override and uses the verified configured default.",
            _closed({
                **common_coordinator,
            }, ["task_ref", "coordinator_ref"]),
            "continue_orchestration",
        ),
    }

    def add_management(
        name: str,
        description: str,
        action: str,
        properties: Mapping[str, Any] = {},
        required: Sequence[str] = (),
    ) -> None:
        contracts[name] = _contract(
            description,
            _closed({**common_coordinator, **properties}, ["task_ref", "coordinator_ref", *required]),
            "manage_orchestration",
            action=action,
        )

    add_management("inspect_orchestration", "Inspect current durable lifecycle state. After a same-incarnation compaction, clear, or reset, call this before any wait, replay, follow-up, continuation, resume, or child creation and consume every returned page until complete is true. Returns content-addressed compact frontier and report-catalog identities. A final action=continue means call continue_orchestration exactly once with the same authority. A final action=resume_orchestration means the authenticated Codex host epoch changed: call resume_orchestration exactly once before any wait, worker-wave read, continuation, or native child creation.", "inspect", {"cursor": cursor})
    add_management(
        "inspect_orchestration_recovery",
        "Observe one ambiguous delivered native dispatch without invoking it again. Consume every page until complete is true. If its exact child still has no trusted start observation, call this same bounded recovery tool again after the observer lease; never fabricate a child id or call wait_agent. Once Cortex proves recovery is required, it returns one exact replacement dispatch to invoke.",
        "recover_inspect", {"cursor": cursor},
    )
    add_management("recover_blocked_orchestration", "Apply coordinator recovery to a blocked task. Returns the resulting lifecycle action.", "recover_blocked", {"text": _string(maximum=65_536)})
    add_management("resume_orchestration", "Resume an eligible task or reconcile one authenticated dead-host epoch exactly once. Dead prior-epoch native children are never waited or reused; Cortex returns new server-owned replacement dispatches for their exact unfinished assignments.", "resume", {"text": _string(maximum=65_536)})
    add_management("stop_orchestration", "Deactivate the active task lifecycle. Returns a terminal acknowledgement.", "deactivate", {"text": _string(maximum=65_536)})
    add_management("show_orchestration_question", "Read one exact arbitrary-Unicode user question page. Returns a continuation cursor when more text remains.", "question_show", {"question_ref": question_ref, "cursor": cursor}, ["question_ref"])
    add_management(
        "answer_orchestration_question",
        "Record one exact arbitrary-Unicode user answer. On success, resume_bound_worker returns one exact native same-child followup_task call. "
        + SAME_CHILD_WAIT_INSTRUCTION
        + " Child binding and authorization remain server-side. Never call continue_orchestration before that canonical wave read.",
        "question_answer",
        {
            "question_ref": question_ref,
            "answer": _string(
                maximum=65_536,
                description="Exact arbitrary-Unicode response bound to the durable question.",
            ),
        },
        ["question_ref", "answer"],
    )
    add_management("read_plan_approval_prompt", "Read the current plan approval request. Returns a bounded prompt page.", "plan_prompt", {"cursor": cursor})
    add_management("decide_plan_approval", "Record the user's plan decision. Returns the resulting lifecycle action.", "plan", {
        "decision": _string(enum=("approve_with_recommendations", "approve_without_recommendations", "cancel"), maximum=32),
        "request_id": _string(maximum=180, format_name="cortex-request-ref"),
    }, ["decision", "request_id"])
    add_management("revise_plan", "Request a plan revision with exact user feedback. Returns the resulting lifecycle action.", "plan_revise", {
        "request_id": _string(maximum=180, format_name="cortex-request-ref"),
        "text": _string(maximum=65_536),
    }, ["request_id", "text"])
    add_management(
        "revise_future_pipeline",
        "Replace only unexecuted future work from completed canonical evidence. This operation never represents rework of completed work. Cortex derives the preserved frontier and assigns every executable phase, wave, dependency, and global index reference. Returns the durable revised route.",
        "future_pipeline_revise",
        {
            "evidence_result_refs": {
                "type": "array", "minItems": 1, "maxItems": 64,
                "uniqueItems": True, "items": attempt_result_ref,
            },
            "waves": {
                "type": "array", "minItems": 1, "maxItems": PUBLIC_MAX_WAVES,
                "items": wave,
            },
            "reason": _string(maximum=16_384),
        },
        ["evidence_result_refs", "waves", "reason"],
    )
    add_management(
        "append_rework_wave",
        "Append one evidence-grounded rework lifecycle after a completed canonical result. This is distinct from technical replacement and unexecuted future-tail revision. Cortex atomically assigns all executable identities and context, compiles one mutating implementation assignment, and appends independent verification. Returns the resulting lifecycle action.",
        "append_rework_wave",
        {
            "source_result_ref": attempt_result_ref,
            "objective": worker["properties"]["objective"],
            "acceptance": _string(
                maximum=65_536,
                description="Exact arbitrary-Unicode acceptance contract for the corrective work.",
            ),
            "profile": worker["properties"]["profile"],
            "model": worker["properties"]["model"],
            "reasoning_effort": worker["properties"]["reasoning_effort"],
        },
        [
            "source_result_ref", "objective", "acceptance", "profile", "model",
            "reasoning_effort",
        ],
    )
    add_management("start_follow_up", "Start one follow-up from the exact user request and a coordinator-authored wave plan. Cortex derives only source context and result references; it never creates worker waves.", "follow_up", {
        "user_request": _string(maximum=65_536, default="Complete the follow-up request."),
        "waves": {
            "type": "array", "minItems": 1, "maxItems": PUBLIC_MAX_WAVES,
            "items": wave,
        },
    }, ["user_request", "waves"])
    add_management("steer_orchestration", "Apply one exact user steering message to the active task. Returns the resulting lifecycle action.", "steer", {"text": _string(maximum=65_536)}, ["text"])
    add_management(
        "start_auxiliary_worker",
        "Start one distinct bounded worker while the current task is paused on one durable worker question. Cortex derives the active phase and preserves the question-bound worker.",
        "auxiliary_start",
        {
            key: worker["properties"][key]
            for key in (
                "objective", "profile", "operation_kind", "model", "reasoning_effort",
            )
        },
        ["objective", "profile", "operation_kind", "model", "reasoning_effort"],
    )
    add_management("list_task_artifacts", "List durable task artifacts. Returns one server-sized page.", "artifact_list", {"kind": _string(maximum=160), "cursor": cursor})
    add_management(
        "read_task_artifact_metadata",
        "Read metadata only for an exact artifact-* reference returned by an artifact listing or explicitly by a canonical report. Never pass an attempt-result-* evidence reference. Returns one server-sized page.",
        "artifact_metadata",
        {"artifact_ref": artifact_ref, "cursor": cursor},
        ["artifact_ref"],
    )
    add_management(
        "read_task_artifact",
        "Read content only for an exact artifact-* reference returned by an artifact listing or explicitly by a canonical report. Never pass an attempt-result-* evidence reference. Returns one server-sized page.",
        "artifact_read",
        {"artifact_ref": artifact_ref, "cursor": cursor},
        ["artifact_ref"],
    )
    add_management(
        "create_orchestration_lane",
        "Create an isolated orchestration lane from semantic intent. The server generates the lane identity and materialization coordinates.",
        "lane_create",
        {
            "mode": _string(enum=("ephemeral", "persistent"), maximum=16, default="ephemeral"),
            "text": _string(maximum=65_536, description="Optional human-readable lane purpose."),
        },
    )
    add_management("inspect_orchestration_lane", "Inspect one orchestration lane. Returns a bounded semantic report.", "lane_inspect", {"lane_id": _string(maximum=180), "cursor": cursor}, ["lane_id"])
    add_management("claim_orchestration_lane", "Claim one server-issued orchestration lane for a bounded run. The server generates the run identity and lease expiry.", "lane_claim", {
        "lane_id": _string(maximum=180), "reclaim": {"type": "boolean"},
    }, ["lane_id"])
    add_management("release_orchestration_lane", "Release one server-issued orchestration lane claim. Returns its durable state.", "lane_release", {"lane_id": _string(maximum=180)}, ["lane_id"])
    add_management("retire_orchestration_lane", "Retire one orchestration lane after explicit cleanup confirmation. Returns its terminal state.", "lane_retire", {"lane_id": _string(maximum=180), "clean": {"type": "boolean"}, "confirm": {"type": "boolean"}}, ["lane_id", "clean", "confirm"])
    add_management("bind_orchestration_lane", "Bind one orchestration lane to the current task. Returns the durable binding.", "lane_bind_task", {"lane_id": _string(maximum=180)}, ["lane_id"])
    add_management("materialize_orchestration_lane", "Materialize one server-described orchestration lane after explicit confirmation. Returns its durable state.", "lane_materialize", {"lane_id": _string(maximum=180), "confirm": {"type": "boolean"}}, ["lane_id", "confirm"])
    add_management("reconcile_orchestration_lane", "Reconcile one server-described orchestration lane. Returns one server-sized report page.", "lane_reconcile", {"lane_id": _string(maximum=180), "cursor": cursor}, ["lane_id"])
    for name, description, action in (
        ("claim_orchestration_resource", "Claim one task resource. Returns the durable claim.", "resource_claim"),
        ("release_orchestration_resource", "Release one task resource. Returns the durable state.", "resource_release"),
        ("lock_orchestration_resource", "Acquire one task resource lock. Returns the durable lock state.", "resource_lock"),
        ("unlock_orchestration_resource", "Release one task resource lock. Returns the durable state.", "resource_unlock"),
    ):
        add_management(name, description, action, {"path": _string(maximum=4_096)}, ["path"])
    add_management("read_orchestration_lifecycle", "Continue a lifecycle report from a server-issued cursor. Returns one server-sized page.", "read_lifecycle_page", {"cursor": cursor}, ["cursor"])
    add_management("finalize_bootstrap_failure", "Finalize a server-bound nonretryable bootstrap failure. Returns a terminal acknowledgement.", "finalize_bootstrap_failure", {"dispatch_ref": dispatch_ref}, ["dispatch_ref"])
    add_management("finalize_worker_failure", "Finalize a server-bound nonretryable worker failure. Returns a terminal acknowledgement.", "finalize_worker_failure", {"dispatch_ref": dispatch_ref}, ["dispatch_ref"])

    def add_governance(
        name: str,
        description: str,
        action: str,
        properties: Mapping[str, Any],
        required: Sequence[str],
        **injected: Any,
    ) -> None:
        contracts[name] = _contract(
            description,
            _closed({**common_coordinator, **properties}, ["task_ref", "coordinator_ref", *required]),
            "manage_governance",
            action=action,
            **injected,
        )

    add_governance("inspect_governance_initiative", "Inspect one governance initiative. Returns a bounded semantic report.", "inspect_initiative", {"initiative_ref": initiative_ref, "cursor": cursor}, ["initiative_ref"])
    add_governance("link_governance_task", "Link the current task to one governance initiative. Returns the durable relationship.", "link_task", {
        "initiative_ref": initiative_ref,
        "relationship": _string(enum=("milestone", "deliverable", "corrective"), maximum=32),
        "text": _string(maximum=65_536), "expected_revision": {"type": "integer", "minimum": 1},
    }, ["initiative_ref"])
    add_governance("add_governance_dependency", "Add one governance dependency. Returns the durable relationship.", "add_dependency", {
        "initiative_ref": initiative_ref,
        "source_type": _string(enum=("initiative", "task"), maximum=16), "source_ref": _string(maximum=180),
        "target_type": _string(enum=("initiative", "task"), maximum=16), "target_ref": _string(maximum=180),
        "dependency_type": _string(enum=("blocks", "requires", "relates_to", "follows"), maximum=32),
    }, ["initiative_ref", "source_type", "source_ref", "target_type", "target_ref", "dependency_type"])
    add_governance("transition_governance_initiative", "Transition one governance initiative. Returns its durable state.", "transition", {
        "initiative_ref": initiative_ref,
        "status": _string(enum=("pending", "active", "approved", "rejected", "superseded", "expired", "proposed", "blocked", "completed", "closed", "cancelled"), maximum=32),
        "expected_revision": {"type": "integer", "minimum": 1}, "text": _string(maximum=65_536),
    }, ["initiative_ref", "status"])

    record_properties = {
        "initiative_ref": initiative_ref,
        "record_type": _string(enum=("decision", "ruling", "preference", "assumption", "risk", "learning", "reflection", "policy", "exception", "promotion"), maximum=32),
        "text": _string(maximum=65_536), "supersedes": _string(maximum=180),
        "expires_at": _string(maximum=80), "content_artifact_ref": _string(maximum=180, format_name="cortex-artifact-ref"),
    }
    records_properties = {"initiative_ref": initiative_ref, "record_type": record_properties["record_type"], "cursor": cursor}
    for scope, label, needs_initiative in (
        ("task", "task", False),
        ("initiative", "initiative", True),
        ("initiative_task", "initiative/task", True),
    ):
        scoped_record = dict(record_properties)
        scoped_records = dict(records_properties)
        if not needs_initiative:
            scoped_record.pop("initiative_ref")
            scoped_records.pop("initiative_ref")
        initiative_required = ["initiative_ref"] if needs_initiative else []
        add_governance(
            f"create_{scope}_governance_record",
            f"Create one {label}-scoped governance record. Returns the durable record.",
            "create_record", scoped_record,
            [*initiative_required, "record_type", "text"], scope=scope,
        )
        add_governance(
            f"list_{scope}_governance_records",
            f"List {label}-scoped governance records. Returns one server-sized page.",
            "list_records", scoped_records, initiative_required, scope=scope,
        )
        add_governance(
            f"read_{scope}_governance_snapshot",
            f"Read the {label}-scoped governance snapshot. Returns one server-sized page.",
            "snapshot", scoped_records, initiative_required, scope=scope,
        )

    add_governance("evaluate_governance_promotion", "Evaluate one governance promotion candidate. Returns the durable evaluation.", "evaluate_promotion", {"initiative_ref": initiative_ref, "fingerprint": _string(maximum=512)}, ["initiative_ref", "fingerprint"])
    add_governance("inspect_governance_promotion", "Inspect governance promotion state. Returns one server-sized page.", "promotion_inspect", {"initiative_ref": initiative_ref, "record_ref": record_ref, "cursor": cursor}, ["initiative_ref"])

    contracts.update({
        "ask_worker_question": _contract(
            "Persist one exact arbitrary-Unicode question that requires a real user decision. Internal technical recovery is not a user question. Returns the durable reference.",
            _closed({
                "dispatch_ref": dispatch_ref,
                "question_category": _string(
                    enum=(
                        "product", "requirement", "scope", "acceptance",
                        "destructive_authorization", "external_authorization",
                    ),
                    maximum=32,
                    description=(
                        "Select the semantic user-decision boundary. Internal Cortex, model, profile, retry, "
                        "dependency, schema, and runtime-recovery conditions are forbidden here and must use "
                        "the server-owned technical recovery path."
                    ),
                ),
                "question_text": _string(maximum=65_536),
            }, ["dispatch_ref", "question_category", "question_text"]),
            "worker_question", action="ask",
        ),
        "poll_worker_question": _contract(
            "Poll one durable worker question. Returns the exact arbitrary-Unicode answer or a continuation action.",
            _closed({"dispatch_ref": dispatch_ref, "question_ref": question_ref, "cursor": cursor}, ["dispatch_ref", "question_ref"]),
            "worker_question", action="poll",
        ),
        "record_attempt_event": _contract(
            "Append one semantic checkpoint or worker-attested verification claim. Command, browser, console, network, accessibility, layout, and test facts remain worker-attested because Cortex does not observe those executions. Cortex validates the flat claim and returns only an identity/digest/storage receipt; that receipt never upgrades provenance. Manifest reconciliation is the only server-observed verification kind. Returns a durable acknowledgement.",
            _closed({
                "dispatch_ref": dispatch_ref,
                "event_type": _string(
                    enum=(
                        "decision_evidence", "verification_observation", "progress", "note",
                    ),
                    maximum=32,
                    description=(
                        "Select verification_observation only to attest a check the worker performed; "
                        "that branch also requires verification_kind and remains worker-attested."
                    ),
                ),
                "verification_kind": _string(
                    enum=tuple(sorted(WORKER_VERIFICATION_KINDS)),
                    maximum=64,
                    description=(
                        "Required only for verification_observation. manifest_reconciliation is server-only "
                        "and is never worker-submittable."
                    ),
                ),
                "text": _string(
                    maximum=65_536,
                    description=(
                        "Concise arbitrary-Unicode checkpoint text. For the worker-attested verification branch it must include "
                        "status=passed and the selected kind's machine fact: functional_browser uses "
                        "passed_tests=<n> with n>=1; responsive_layout uses viewports=<n> with n>=2; "
                        "keyboard_accessibility uses keyboard_checks=<n> with n>=1; console_clean uses "
                        "console_errors=0; local_only_network uses external_requests=0."
                    ),
                ),
            }, ["dispatch_ref", "event_type", "text"]),
            "record_attempt_event",
        ),
        "record_worker_finding": _contract(
            "Record one review or verification finding before terminal submission. Cortex generates and binds all identity, lineage, workspace, and fingerprint data. Medium, high, and critical findings require rework even when the worker later submits status=completed; low is advisory. An exact duplicate is idempotent. Returns a durable finding receipt.",
            _closed({
                "dispatch_ref": dispatch_ref,
                "severity": _string(enum=PUBLIC_FINDING_SEVERITIES, maximum=16),
                "summary": _string(
                    maximum=65_536,
                    description="Exact arbitrary-Unicode finding text. Describe one independently actionable issue.",
                ),
            }, ["dispatch_ref", "severity", "summary"]),
            "record_worker_finding",
        ),
        "submit_attempt": _contract(
            "Submit one ordinary terminal worker status and semantic report. Use completed whenever the worker completed its assignment protocol, including when product findings or unmet acceptance require rework; record those findings before submission. Use failed or blocked only when technical, infrastructure, environment, dependency, policy, or authorization conditions prevented assignment completion. Report text is arbitrary Unicode semantic evidence and never routing authority. Governance-close workers must use submit_governance_closure instead. Keep the opaque worker authority only in the dedicated inputSchema property; never copy it into the semantic report. An accidental copy of this worker's exact current authority returns a non-mutating same-operation correction limited to replacing the report; foreign authority remains rejected. Returns completion or a server-issued repair action.",
            _closed({
                "dispatch_ref": dispatch_ref,
                "status": _string(enum=("completed", "blocked", "failed"), maximum=16),
                "report": _string(maximum=65_536),
            }, ["dispatch_ref", "status", "report"]),
            "complete_attempt", action="submit",
        ),
        "submit_governance_closure": _contract(
            "Submit the governance-close verdict through its dedicated flat contract. blocking_gaps_text is plain arbitrary-Unicode text: verified requires exactly the empty string; blocked requires non-empty text. A verified verdict also requires a complete current server-derived closure basis. The worker supplies no task, plan, evidence, manifest, or provenance identifiers.",
            _closed({
                "dispatch_ref": dispatch_ref,
                "closure_outcome": _string(enum=("verified", "blocked"), maximum=16),
                "blocking_gaps_text": _string(
                    minimum=0,
                    maximum=65_536,
                    description=(
                        "Plain arbitrary-Unicode text. Use exactly the empty string when "
                        "closure_outcome is verified; use non-empty text when it is blocked."
                    ),
                ),
                "report": _string(maximum=65_536),
            }, ["dispatch_ref", "closure_outcome", "blocking_gaps_text", "report"]),
            "complete_attempt", action="governance_closure",
        ),
        "repair_attempt": _contract(
            "Apply one issued atomic patch-only repair. Opaque authority must be copied exactly; only issued paths may change.",
            _closed({
                "dispatch_ref": dispatch_ref,
                "repair_capsule": _string(
                    minimum=REPAIR_HANDLE_LENGTH,
                    maximum=REPAIR_HANDLE_LENGTH,
                    pattern=REPAIR_HANDLE_PATTERN,
                    format_name="cortex-repair-capsule",
                ),
                "base_payload_digest": _string(pattern=r"^sha256:[0-9a-f]{64}$", maximum=71, format_name="cortex-payload-digest"),
                "patches": {"type": "array", "minItems": 1, "maxItems": MAX_DIAGNOSTICS, "items": _flat_patch_schema()},
            }, ["dispatch_ref", "repair_capsule", "base_payload_digest", "patches"]),
            "complete_attempt", action="repair",
        ),
        "read_dispatch_briefing": _contract(
            "Read the authorized immutable dispatch briefing. On the first call supply only the required exact server-issued authority; add cursor only from a returned next_cursor. The public schema and runtime validate the call. Returns one server-sized exact-text page.",
            _closed({"dispatch_ref": dispatch_ref, "cursor": cursor}, ["dispatch_ref"]),
            "read_dispatch_briefing",
        ),
        "read_worker_wave": _contract(
            "Read the coordinator's current durable worker wave only after wait_agent has reported every exact bound child terminal. A premature read is forbidden and the backend keeps it non-mutating. Follow the returned action literally: wait_for_bound_workers means "
            + WAIT_BEFORE_READ_INSTRUCTION
            + " obtain_user_decision means surface the durable question; resume_bound_worker means "
            + SAME_CHILD_WAIT_INSTRUCTION
            + " invoke_dispatches means " + NATIVE_DISPATCH_WAIT_INSTRUCTION
            + " continue means call continue_orchestration immediately with the same exact coordinator authority; do not wait or reread first. inspect_orchestration means a same-incarnation compaction boundary requires a complete paginated inspection before any wait, reread, continuation, resume, follow-up, or child creation. resume_orchestration means the authenticated host process changed and every unfinished prior-epoch child is unavailable: call resume_orchestration exactly once before any wait, reread, continuation, or child creation. append_rework_wave means invoke that dedicated public operation from the returned canonical finding/result reference; revise_or_continue means the returned report is already complete canonical evidence, so call task-required revise_future_pipeline or continue_orchestration directly. Result references are evidence capabilities, never artifact references. Do not read an artifact unless the canonical report explicitly supplies a distinct artifact-* reference. A trusted result-less Stop may return wait_for_bound_workers only after its one same-child resume instruction was durably issued and that child's exact dispatch-authorized answer poll or result is pending; a follow-up turn need not emit another SubagentStart.",
            _closed({**common_coordinator, "cursor": cursor}, ["task_ref", "coordinator_ref"]),
            "read_worker_result", action="read_wave",
        ),
        "refresh_worker_context": _contract(
            "Refresh the current worker's server-owned context after compaction or a bounded context reset. "
            "The call has no identity or routing arguments: Cortex binds it to the authenticated native worker. "
            "If a continuation is returned, pass that exact cursor unchanged until complete is true.",
            _closed({"cursor": cursor}, []),
            "read_worker_context",
        ),
        "list_worker_reports": _contract(
            "List the canonical predecessor reports authorized for the current dispatch. Returns one "
            "server-sized text page containing opaque report references, plus next_cursor and complete; "
            "continue until complete is true before selecting a report.",
            _closed({"dispatch_ref": dispatch_ref, "cursor": cursor}, ["dispatch_ref"]),
            "read_worker_result", action="list_reports",
            expose_flat_argument_names=True,
        ),
        "read_predecessor_result": _contract(
            "Read one canonical predecessor report authorized for the current dispatch by an exact opaque "
            "report reference returned by list_worker_reports. Returns one server-sized text page, plus "
            "next_cursor and complete; continue until complete is true before using the report.",
            _closed({"dispatch_ref": dispatch_ref, "report_ref": report_ref, "cursor": cursor}, ["dispatch_ref", "report_ref"]),
            "read_worker_result", action="read_predecessor",
            expose_flat_argument_names=True,
        ),
    })
    return contracts


def public_input_schemas(contracts: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return the canonical schema objects without copying them."""
    return {
        str(name): contract["inputSchema"]
        for name, contract in contracts.items()
        if isinstance(contract.get("inputSchema"), dict)
    }


def backend_schema_for(
    contracts: Mapping[str, Mapping[str, Any]],
    base_operation: str,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive a private adapter schema from canonical public contracts."""
    matches = [
        contract for contract in contracts.values()
        if contract.get("base_operation") == base_operation
        and isinstance(contract.get("inputSchema"), Mapping)
    ]
    if arguments is not None:
        selected = [
            contract for contract in matches
            if all(
                arguments.get(str(name)) == value
                for name, value in dict(contract.get("injected_arguments") or {}).items()
            )
        ]
        if len(selected) == 1:
            matches = selected
    if not matches:
        return {"type": "object", "additionalProperties": False, "properties": {}, "required": []}

    materialized: list[dict[str, Any]] = []
    for contract in matches:
        schema = copy.deepcopy(contract["inputSchema"])
        properties = schema.setdefault("properties", {})
        required = schema.setdefault("required", [])
        for name, value in dict(contract.get("injected_arguments") or {}).items():
            properties[str(name)] = {
                "type": "boolean" if type(value) is bool else "integer" if type(value) is int else "string",
                "const": value,
            }
            if name not in required:
                required.append(str(name))
        materialized.append(schema)
    if len(materialized) == 1:
        return materialized[0]
    properties: dict[str, Any] = {}
    for schema in materialized:
        properties.update(schema.get("properties") or {})
    required_sets = [set(schema.get("required") or []) for schema in materialized]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": sorted(set.intersection(*required_sets)) if required_sets else [],
    }
