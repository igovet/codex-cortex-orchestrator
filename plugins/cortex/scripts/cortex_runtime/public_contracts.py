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
from cortex_runtime.v11_submission import (
    COORDINATOR_REF_PATTERN,
    DISPATCH_REF_PATTERN,
    MAX_DIAGNOSTICS,
    REPAIR_HANDLE_LENGTH,
    REPAIR_HANDLE_PATTERN,
)


PUBLIC_MAX_WORKERS_PER_WAVE = 8
PUBLIC_MAX_WAVES = 32
CANONICAL_MODELS = ("gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra")

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
    **injected_arguments: Any,
) -> dict[str, Any]:
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
    elif base_operation == "read_worker_result" and action == "read_predecessor":
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
        "allowed_paths": {
            "type": "array",
            "minItems": 1,
            "maxItems": 64,
            "uniqueItems": True,
            "description": "Every entry is strictly project-relative to project_root, never absolute.",
            "items": _string(
                pattern=r"^(?!\s)(?!.*\s$)(?!.*\x00)(?!/)(?![A-Za-z]:[\\/])(?!.*(?:^|/)\.\.(?:/|$))(?!.*[\\*?\[\]])(?!\.$).+$",
                maximum=2_048,
                description="A project-relative path such as desktop-v11-smoke.txt; never an absolute path.",
            ),
        },
        "profile": _string(enum=profiles, maximum=160),
        "depends_on": _strings(maximum_items=32, item_maximum=160),
        "context_result_refs": _strings(maximum_items=64, item_maximum=180),
        "user_model": _string(enum=CANONICAL_MODELS, maximum=64),
        "user_effort": _string(
            enum=("low", "medium", "high", "xhigh", "max", "ultra"),
            maximum=16,
        ),
    }, ["objective"])
    wave = _closed({
        "phase": _string(enum=gates, maximum=80),
        "workers": {
            "type": "array",
            "minItems": 1,
            "maxItems": PUBLIC_MAX_WORKERS_PER_WAVE,
            "items": worker,
        },
    }, ["phase", "workers"])

    contracts: dict[str, dict[str, Any]] = {
        "start_orchestration": _contract(
            "Start one durable task from a coordinator-authored wave plan. Returns private coordinator authority and the first lifecycle action.",
            _closed({
                "project_root": _string(
                    maximum=4_096,
                    format_name="cortex-project-root",
                    description="An absolute path to the project root.",
                ),
                "user_request": _string(maximum=65_536, default="Complete the requested work."),
                "waves": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": PUBLIC_MAX_WAVES,
                    "items": wave,
                },
                "plan_approval": _string(enum=("auto", "required"), maximum=16),
                "governance_mode": _string(enum=("auto", "required", "off"), maximum=16),
            }, ["project_root", "user_request", "waves"]),
            "start_orchestration",
        ),
        "continue_orchestration": _contract(
            "Advance one durable task step after the current wave has reached a terminal result. Returns the next lifecycle action.",
            _closed({
                **common_coordinator,
                "step": {"type": "integer", "minimum": 1},
                "result_refs": _strings(maximum_items=64, item_maximum=180, minimum_items=1),
            }, ["task_ref", "coordinator_ref", "step", "result_refs"]),
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

    add_management("inspect_orchestration", "Inspect current durable lifecycle state. Returns a bounded semantic report.", "inspect", {"cursor": cursor})
    add_management("inspect_orchestration_recovery", "Inspect recoverable lifecycle state. Returns a bounded recovery report.", "recover_inspect", {"cursor": cursor})
    add_management("recover_blocked_orchestration", "Apply coordinator recovery to a blocked task. Returns the resulting lifecycle action.", "recover_blocked", {"text": _string(maximum=65_536)})
    add_management("resume_orchestration", "Resume an eligible task. Returns the resulting lifecycle action.", "resume", {"text": _string(maximum=65_536)})
    add_management("stop_orchestration", "Deactivate the active task lifecycle. Returns a terminal acknowledgement.", "deactivate", {"text": _string(maximum=65_536)})
    add_management("show_orchestration_question", "Read one exact arbitrary-Unicode user question page. Returns a continuation cursor when more text remains.", "question_show", {"question_ref": question_ref, "cursor": cursor}, ["question_ref"])
    add_management(
        "answer_orchestration_question",
        "Record one exact arbitrary-Unicode user answer. Returns the resumed lifecycle action.",
        "question_answer",
        {
            "question_ref": question_ref,
            "answer_text": _string(
                maximum=65_536,
                description="Exact arbitrary-Unicode answer text bound to the durable question.",
            ),
        },
        ["question_ref", "answer_text"],
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
    add_management("start_follow_up", "Start one follow-up from the exact user request and optional durable context. Returns the first lifecycle action.", "follow_up", {
        "user_request": _string(maximum=65_536, default="Complete the follow-up request."),
        "result_refs": _strings(maximum_items=64, item_maximum=180, minimum_items=1),
    }, ["user_request"])
    add_management("steer_orchestration", "Apply one exact user steering message to the active task. Returns the resulting lifecycle action.", "steer", {"text": _string(maximum=65_536)}, ["text"])
    add_management("list_task_artifacts", "List durable task artifacts. Returns one server-sized page.", "artifact_list", {"kind": _string(maximum=160), "cursor": cursor})
    add_management("read_task_artifact_metadata", "Read durable artifact metadata. Returns one server-sized page.", "artifact_metadata", {"artifact_ref": _string(maximum=180, format_name="cortex-artifact-ref"), "cursor": cursor}, ["artifact_ref"])
    add_management("read_task_artifact", "Read durable artifact content. Returns one server-sized page.", "artifact_read", {"artifact_ref": _string(maximum=180, format_name="cortex-artifact-ref"), "cursor": cursor}, ["artifact_ref"])
    add_management("create_orchestration_lane", "Create an isolated orchestration lane. Returns its durable state.", "lane_create", {
        "lane_id": _string(maximum=180), "mode": _string(enum=("ephemeral", "persistent"), maximum=16),
        "text": _string(maximum=65_536), "repo_path": _string(maximum=4_096),
        "worktree_path": _string(maximum=4_096), "branch": _string(maximum=512),
        "sync_from": _string(maximum=512),
    }, ["lane_id"])
    add_management("inspect_orchestration_lane", "Inspect one orchestration lane. Returns a bounded semantic report.", "lane_inspect", {"lane_id": _string(maximum=180), "cursor": cursor}, ["lane_id"])
    add_management("claim_orchestration_lane", "Claim one orchestration lane for a bounded run. Returns its durable state.", "lane_claim", {
        "lane_id": _string(maximum=180), "run_id": _string(maximum=180),
        "expires_at": _string(maximum=80), "reclaim": {"type": "boolean"},
    }, ["lane_id", "expires_at"])
    add_management("release_orchestration_lane", "Release one orchestration lane claim. Returns its durable state.", "lane_release", {"lane_id": _string(maximum=180), "run_id": _string(maximum=180)}, ["lane_id"])
    add_management("retire_orchestration_lane", "Retire one orchestration lane after explicit cleanup confirmation. Returns its terminal state.", "lane_retire", {"lane_id": _string(maximum=180), "clean": {"type": "boolean"}, "confirm": {"type": "boolean"}}, ["lane_id", "clean", "confirm"])
    add_management("bind_orchestration_lane", "Bind one orchestration lane to the current task. Returns the durable binding.", "lane_bind_task", {"lane_id": _string(maximum=180)}, ["lane_id"])
    add_management("materialize_orchestration_lane", "Materialize one orchestration lane after explicit confirmation. Returns its durable state.", "lane_materialize", {"lane_id": _string(maximum=180), "run_id": _string(maximum=180), "confirm": {"type": "boolean"}}, ["lane_id", "confirm"])
    add_management("reconcile_orchestration_lane", "Reconcile one orchestration lane. Returns one server-sized report page.", "lane_reconcile", {"lane_id": _string(maximum=180), "run_id": _string(maximum=180), "cursor": cursor}, ["lane_id"])
    for name, description, action in (
        ("claim_orchestration_resource", "Claim one task resource. Returns the durable claim.", "resource_claim"),
        ("release_orchestration_resource", "Release one task resource. Returns the durable state.", "resource_release"),
        ("lock_orchestration_resource", "Acquire one task resource lock. Returns the durable lock state.", "resource_lock"),
        ("unlock_orchestration_resource", "Release one task resource lock. Returns the durable state.", "resource_unlock"),
    ):
        add_management(name, description, action, {"path": _string(maximum=4_096), "kind": _string(maximum=160)}, ["path"])
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
            "Persist one exact arbitrary-Unicode worker question. Returns its durable reference.",
            _closed({"dispatch_ref": dispatch_ref, "question_text": _string(maximum=65_536)}, ["dispatch_ref", "question_text"]),
            "worker_question", action="ask",
        ),
        "poll_worker_question": _contract(
            "Poll one durable worker question. Returns the exact arbitrary-Unicode answer or a continuation action.",
            _closed({"dispatch_ref": dispatch_ref, "question_ref": question_ref, "cursor": cursor}, ["dispatch_ref", "question_ref"]),
            "worker_question", action="poll",
        ),
        "record_attempt_event": _contract(
            "Append one semantic attempt checkpoint. Returns a durable acknowledgement.",
            _closed({
                "dispatch_ref": dispatch_ref,
                "event_type": _string(enum=("finding_added", "decision_evidence", "blocker", "verification_claimed", "progress", "note"), maximum=32),
                "text": _string(maximum=65_536),
            }, ["dispatch_ref", "event_type", "text"]),
            "record_attempt_event",
        ),
        "submit_attempt": _contract(
            "Submit one terminal worker status and semantic report. Returns completion or a server-issued repair action.",
            _closed({
                "dispatch_ref": dispatch_ref,
                "status": _string(enum=("completed", "blocked", "failed"), maximum=16),
                "report": _string(maximum=65_536),
            }, ["dispatch_ref", "status", "report"]),
            "complete_attempt", action="submit",
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
            "Read the authorized immutable dispatch briefing. Returns one server-sized exact-text page.",
            _closed({"dispatch_ref": dispatch_ref, "cursor": cursor}, ["dispatch_ref"]),
            "read_dispatch_briefing",
        ),
        "read_worker_wave": _contract(
            "Read the coordinator's current durable worker wave. Returns one server-sized result page.",
            _closed({**common_coordinator, "step": {"type": "integer", "minimum": 1}, "cursor": cursor}, ["task_ref", "coordinator_ref", "step"]),
            "read_worker_result", action="read_wave",
        ),
        "read_predecessor_result": _contract(
            "Read one predecessor result authorized by the current dispatch. Returns one server-sized result page.",
            _closed({"dispatch_ref": dispatch_ref, "attempt_result_ref": attempt_result_ref, "cursor": cursor}, ["dispatch_ref", "attempt_result_ref"]),
            "read_worker_result", action="read_predecessor",
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
