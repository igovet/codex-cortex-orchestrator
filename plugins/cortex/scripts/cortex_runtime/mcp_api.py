"""Public MCP registry and stdio transport, independent of orchestration policy.

The stdio protocol does not carry a trustworthy per-call actor identity.  A
server process therefore receives one immutable audience at launch time.  The
ordinary Desktop launch uses the fresh public union.  A host that can
establish separate trusted channels may opt into coordinator and worker
projections.
"""
from __future__ import annotations

import json
import re
import secrets
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from cortex_runtime.v11_submission import (
    COORDINATOR_REF_PATTERN,
    PUBLIC_SUBMISSION_SCHEMA,
    json_pointer as _submission_json_pointer,
)
from cortex_runtime.v11_responses import (
    COORDINATOR_ACTION_SEMANTICS,
    TASK_REF_PATTERN,
    ResponseValidationError,
    validate_response,
)


MCP_AUDIENCES = frozenset({"default", "coordinator", "worker"})
DEFAULT_MCP_AUDIENCE = "default"
CANONICAL_MODELS = ("gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra")

# Raw host credentials must never cross the MCP/public response boundary.
# Keep this projection at the last point before JSON-RPC serialization so an
# individual lifecycle handler cannot re-introduce a bearer/proof through a
# success, recovery, error, transcript, or briefing.
_PUBLIC_SECRET_KEYS = frozenset({
    "assignment_ref",
    "coordinator_ref",
    "coordinator_capability",
    "coordinator_recovery_proof",
    "previous_coordinator_recovery_proof",
    "authorization_update",
})
_ASSIGNMENT_REF_VALUE_RE = re.compile(r"assignment-v1-[0-9a-f]{64}")


def _supplied_coordinator_refs(request: object) -> frozenset[str]:
    """Return only explicit coordinator bearers from one JSON-RPC request."""
    if not isinstance(request, Mapping):
        return frozenset()
    rpc_params = request.get("params")
    arguments = rpc_params.get("arguments") if isinstance(rpc_params, Mapping) else None
    candidate = str(arguments.get("coordinator_ref") or "").strip().lower() if isinstance(arguments, Mapping) else ""
    return frozenset({candidate}) if re.fullmatch(r"[0-9a-f]{64}", candidate) else frozenset()


def _scrub_public_response(
    value: object,
    *,
    allow_coordinator_ref: bool = False,
    supplied_coordinator_refs: frozenset[str] = frozenset(),
) -> object:
    """Remove authorization material except two exact top-level contracts."""
    allowed_messages: set[tuple[object, ...]] = set()
    if isinstance(value, Mapping) and isinstance(value.get("dispatches"), list):
        approved_argument_keys = {"task_name", "message", "reasoning_effort", "fork_turns", "model"}
        for index, dispatch in enumerate(value["dispatches"]):
            if not isinstance(dispatch, Mapping) or dispatch.get("call") != "spawn_agent":
                continue
            if not re.fullmatch(r"dispatch-[0-9a-f]{24}", str(dispatch.get("dispatch_ref") or "")):
                continue
            arguments = dispatch.get("arguments")
            if not isinstance(arguments, Mapping) or not set(arguments).issubset(approved_argument_keys):
                continue
            if not {"task_name", "message", "fork_turns"}.issubset(arguments) or arguments.get("fork_turns") != "none":
                continue
            message = arguments.get("message")
            if isinstance(message, str) and len(_ASSIGNMENT_REF_VALUE_RE.findall(message)) == 1:
                allowed_messages.add(("dispatches", index, "arguments", "message"))
            repair_message = dispatch.get("bootstrap_repair_message")
            if isinstance(repair_message, str) and len(_ASSIGNMENT_REF_VALUE_RE.findall(repair_message)) == 1:
                allowed_messages.add(("dispatches", index, "bootstrap_repair_message"))

    def scrub(node: object, path: tuple[object, ...]) -> object:
        if isinstance(node, Mapping):
            result: dict[str, object] = {}
            for key, item in node.items():
                normalized = str(key)
                child_path = (*path, normalized)
                if normalized == "coordinator_ref":
                    if (
                        allow_coordinator_ref and path == () and isinstance(item, str)
                        and re.fullmatch(r"[0-9a-f]{64}", item)
                    ):
                        result[normalized] = item
                    continue
                if normalized in _PUBLIC_SECRET_KEYS:
                    continue
                result[normalized] = scrub(item, child_path)
            return result
        if isinstance(node, list):
            return [scrub(item, (*path, index)) for index, item in enumerate(node)]
        if isinstance(node, tuple):
            return [scrub(item, (*path, index)) for index, item in enumerate(node)]
        if isinstance(node, str):
            if path in allowed_messages:
                return node
            scrubbed = _ASSIGNMENT_REF_VALUE_RE.sub("<redacted-assignment-ref>", node)
            for coordinator_ref in supplied_coordinator_refs:
                scrubbed = scrubbed.replace(coordinator_ref, "<redacted-coordinator-ref>")
            return scrubbed
        return node

    return scrub(value, ())


def _public_next_action(value: object) -> str:
    """Return the canonical server-provided next action."""
    if isinstance(value, Mapping):
        nested = value.get("next_action") or value.get("action")
        operation = value.get("operation") or value.get("tool")
        if nested:
            value = nested
        elif operation:
            value = f"Call {operation} with the server-returned arguments for this same task."
        else:
            value = "Inspect the same task and follow the server-returned recovery action."
    text = str(value or "").strip()
    return text or "Inspect the same task and follow the server-returned recovery action."


def _canonical_jsonrpc_request_id(value: object) -> str:
    """Encode a JSON-RPC id with its JSON type before lifecycle composition."""
    if value is None:
        return "null:null"
    if isinstance(value, bool):
        return "boolean:" + ("true" if value else "false")
    if isinstance(value, int):
        return f"number:integer:{value}"
    if isinstance(value, float):
        return "number:float:" + json.dumps(value, allow_nan=False, separators=(",", ":"))
    if isinstance(value, str):
        return "string:" + json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    # JSON-RPC ids should be string or number, but preserve a typed, stable
    # encoding for malformed/forward-compatible JSON values rather than
    # allowing their string representations to collide.
    return "json:" + json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _public_user_view(rendered: object) -> dict[str, Any]:
    """Return only presentation fields; transport metadata stays internal."""
    if not isinstance(rendered, dict):
        return {}
    allowed = (
        "message_type", "message", "next_step", "profile", "detail_level",
        "quality", "question", "requires_user_decision", "recommendation",
        "risks", "why_it_matters", "output_policy",
    )
    return {key: rendered[key] for key in allowed if key in rendered}


def _is_user_decision_event(outcome: object, result: object = None) -> bool:
    """Return whether this response is allowed to pause the visible chat.

    Technical validation/recovery states may use ``needs_input`` internally,
    but they are not questions.
    Only an explicit plan-approval state or a server-returned question may
    request a user decision at the public MCP boundary.
    """
    if str(outcome or "").strip().lower() == "awaiting_plan_approval":
        return True
    if not isinstance(result, Mapping):
        return False
    return bool(result.get("requires_user_decision") or result.get("question"))



def _public_user_view_with_decision(
    rendered: object,
    *,
    requires_user_decision: bool,
) -> dict[str, Any]:
    """Add the strict decision bit without leaking transport metadata."""
    view = _public_user_view(rendered)
    view["requires_user_decision"] = bool(requires_user_decision)
    view["message_type"] = "decision_required" if requires_user_decision else (
        "progress" if view.get("message_type") == "Question" else view.get("message_type", "Update")
    )
    return view


def _internal_protocol(response: Mapping[str, Any]) -> dict[str, Any]:
    """Nest a bounded machine receipt under one explicit boundary.

    The top-level response carries the native dispatch payload.  Repeating
    those large host messages inside ``internal``
    can exceed the host's response budget, so this boundary keeps identity,
    receipt references, and dispatch metadata while leaving the authoritative
    native payload at its existing top-level location.
    """
    keep = {
        "schema", "ok", "outcome", "task_ref", "step", "replayed",
        "attempt_result_ref", "receipt_ref", "continuation",
    }
    result = {key: response[key] for key in keep if key in response}
    dispatches = response.get("dispatches")
    if isinstance(dispatches, list):
        result["dispatches"] = [
            {
                key: item[key]
                for key in ("dispatch_ref", "phase", "profile", "display_name", "briefing_digest", "worker")
                if isinstance(item, dict) and key in item
            }
            for item in dispatches
            if isinstance(item, dict)
        ]
    if "next_action" in response:
        result["next_action_ref"] = "top-level next_action"
    if isinstance(response.get("diagnostics"), list):
        result["diagnostic_count"] = len(response["diagnostics"])
    return result

# ``read_worker_result`` is intentionally shared: the coordinator reads a
# completed canonical result, while a successor worker may read only refs
# granted in its dispatch.  Handler-level scope checks remain authoritative.
COORDINATOR_PUBLIC_TOOL_NAMES = (
    "start_orchestration",
    "continue_orchestration",
    "manage_orchestration",
    "manage_governance",
    "read_worker_result",
)
WORKER_PUBLIC_TOOL_NAMES = (
    "worker_question",
    "record_attempt_event",
    "complete_attempt",
    "read_dispatch_briefing",
    "read_worker_result",
)
# Desktop uses the same fresh-only nine-operation union as the explicit
# audience projections.
DEFAULT_PUBLIC_TOOL_NAMES = tuple(dict.fromkeys(
    (*COORDINATOR_PUBLIC_TOOL_NAMES, *WORKER_PUBLIC_TOOL_NAMES)
))

# These operations are implementation ports used by the orchestration engine,
# never model-facing MCP operations. Keep the boundary explicit: a new
# internal handler must not become callable merely because it was added to the
# handler registry. The public registry below is the only model contract.
# These names remain implementation ports for the SQLite engine only; they
# are deliberately not operation contracts and must never be projected to a
# model-facing MCP channel.
SERVER_ONLY_TOOL_NAMES = frozenset({
    "init_task", "get_task_status", "record_delegation", "prepare_delegation",
    "prepare_delegations", "finalize_attempt",
    "record_evidence", "execute_verification_command", "cortex.question",
    "publish_worker_question", "list_worker_questions", "answer_worker_question",
    "get_worker_question_updates", "commit_gate", "update_pipeline",
    "reassess_pipeline", "acquire_lock",
    "release_lock", "create_handoff", "claim_resource", "release_resource",
    "create_lane", "get_lane_status", "claim_lane", "release_lane",
    "retire_lane", "bind_task_lane", "claim_lane_resource",
    "release_lane_resource", "materialize_lane", "reconcile_lane",
    "activate_orchestration", "deactivate_orchestration",
    "classify_task", "resolve_dispatch_route",
})


_INVOKE_DISPATCHES_INSTRUCTION = COORDINATOR_ACTION_SEMANTICS["invoke_dispatches"]["instruction"]
_WAIT_FOR_BOUND_WORKERS_INSTRUCTION = COORDINATOR_ACTION_SEMANTICS["wait_for_bound_workers"]["instruction"]


PUBLIC_TOOL_DESCRIPTIONS = {
    "start_orchestration": "Start a Cortex task from the exact user-authored request. Before the single call, every ordinary task needs non-empty task.acceptance_criteria and task.verification grounded in that request or verified authority; task.verification is the array of concrete authoritative checks, and verification_mode is not a task field. Use only fields advertised by this schema: unknown task fields are rejected before task creation. Ask the user if material intent is missing. Host activation context must already be established by the host before this call. Each wave declares exactly one phase at waves[].phase; every worker in that wave inherits it and workers[].phase is unsupported. Put multiple workers for the same phase in one wave and use separate waves for different phases. Omit worker.profile for the canonical server-owned phase owner; an expert override must use the containing wave phase's closed conditional enum. Cortex preserves the intent boundary and returns native dispatches with canonical profile, capability, access, and selection rationale. action.kind=invoke_dispatches means: " + _INVOKE_DISPATCHES_INSTRUCTION + " It grants no wait permission. action.kind=wait_for_bound_workers means: " + _WAIT_FOR_BOUND_WORKERS_INSTRUCTION,
    "continue_orchestration": "Coordinator-only lifecycle operation. Preserve and submit the exact task_ref plus coordinator_ref returned by start_orchestration, together with the server-derived continuation step and result refs. Never infer authority from a host session, omit coordinator_ref, submit inline result bodies or replacement pipelines, or repeat a consumed dispatch. action.kind=invoke_dispatches means: " + _INVOKE_DISPATCHES_INSTRUCTION + " It grants no wait permission. action.kind=wait_for_bound_workers means: " + _WAIT_FOR_BOUND_WORKERS_INSTRUCTION,
    "manage_orchestration": "Coordinator-only bounded task management. Every call requires the exact task_ref plus coordinator_ref returned by start_orchestration; Cortex derives project scope from task_ref and rejects project_root. For a durable worker question, call intent=question with exactly payload={question_ref}; Cortex renders the stored canonical prompt and all stored canonical options, so the coordinator never invents option IDs or counts. Optional localization is display-only and may omit localized_options; when localized_options is supplied it must contain one ordered display label for every stored canonical option. Never put localization at the top level and never invent plan-approval field names. On awaiting_user, render question and end the turn. On the next user message, submit the answer with the same payload.question_ref; on question_answered, forward the returned resume object unchanged to exactly the paused child with followup_task. The resume object's kind is not a worker_question action object: the child maps resume.kind to the action string and preserves its own task_ref and assignment_ref. Bootstrap finalization is legal only after the exact bootstrap-missing sequence and is never a fallback for a child tool/protocol error. A child's bare terminal marker is status text, never failure authority: call finalize_worker_failure only for the original dispatch and let Cortex verify and consume its current server-bound terminal evidence. Missing, stale, wrong-dispatch, or replayed evidence must leave the task unchanged. No ambient-authority recovery or project-wide prune/maintenance authority is model-facing. If coordinator_ref is lost, fail closed with coordinator_capability_lost and start a fresh user-authorized task; never reconstruct or mint authority from runtime state.",
    "worker_question": "Worker-only closed action union. Preserve the exact task_ref and assignment_ref from the native dispatch on every call and use one tools/list branch without cross-branch fields. action=ask requires top-level question_type and decision_scope plus question and recommendation. question_type=single_select requires options with stable option_id values and exactly one recommended_option_ids item; multi_select requires options and one or more recommended_option_ids; text requires recommended_answer. Never send answer_mode, context, type, multiple, or infer a question type from field presence. ask_batch applies the same question_type/decision_scope contract to every batch.questions item. ask_batch requires only batch beyond authorization/action; poll_batch requires only the exact scalar batch_ref. For action=poll, make exactly {action:\"poll\",task_ref:<same string>,assignment_ref:<same string>,question_ref:<exact returned scalar string>}. The coordinator resume value {kind:\"poll\",question_ref:...} is an instruction for followup_task, not a value for action: copy its kind into the action string and its question_ref string unchanged. Do not omit worker authorization, turn action into an object, mix ask/poll/batch fields, rename a ref, or replace its value. Questions may cover only task requirements, scope, acceptance, or explicit external/destructive authorization; they may never ask the user to repair Cortex validation, lifecycle, routing, ledger, or worker conditions. A retryable non-mutating recovery names every independent JSON pointer to fix on this same worker attempt exactly once.",
    "record_attempt_event": "Worker-only pre-completion event operation. Preserve the exact task_ref and assignment_ref from the native dispatch; identity is never inferred from a session, hook, environment, or process. Never call it after complete_attempt succeeds or to report completion. A nonretryable recovery ends every task-scoped worker call.",
    "complete_attempt": "Worker-only compact v11 plan/outcome submission. Preserve exact task_ref and assignment_ref. Validation repair uses only the exact returned digest, directly copied opaque repair_capsule, and patches built from each self-contained repair diagnostic: use repair_pointer plus allowed_ops/code/message/field_schema; choose only an advertised patch operation. Never inspect Cortex source, installed plugin/cache, schemas, logs, ledger, session, environment, or hidden path after any error/recovery response. If the card is not executable, return exactly CORTEX_PROTOCOL_FAILURE retryable=false. A malformed capsule copy is retryable with the same repair; a structurally valid handle that fails integrity is terminal. ok=true with terminal=true ends every worker Cortex call: return exactly ATTEMPT_COMPLETED to the coordinator, with no reference, event write, or worker result read. A nonretryable recovery also ends every task-scoped worker call. Only recovery.terminal_failure with evidence=server_bound authorizes the status marker that asks the coordinator to attempt fixed cleanup; the marker itself is never authority.",
    "read_dispatch_briefing": "Worker-only scoped, bounded page read. Preserve the exact task_ref and assignment_ref from the native dispatch. The first call may be incomplete even without max_bytes: when complete=false, call this same tool again with the returned opaque next_cursor and the same authorization until complete=true. max_bytes is optional, bounded by the public schema, and never enables an unbounded read.",
    "read_worker_result": "Read canonical AttemptResult evidence. Immediately after every exact current child finishes, a coordinator sends task_ref+coordinator_ref+step; Cortex derives the complete current-wave result set in deterministic dispatch order and returns the only continuation accepted by continue_orchestration. Successor workers instead require task_ref+assignment_ref+attempt_result_ref and may read only predecessor refs granted in their dispatch before completion. The two closed forms cannot be mixed. A worker never reads its own completed result and makes no worker Cortex call after terminal=true.",
    "manage_governance": "Coordinator-only task-scoped governance. Every model-facing call requires the exact task_ref plus coordinator_ref returned by start_orchestration. Cortex derives project scope from task_ref, rejects project_root and ambient runtime authority, then enforces the signed action/scope claim before any mutation. Project-admin and lost-capability recovery actions are not MCP operations.",
}



def build_public_schemas(
    *,
    agents: Mapping[str, Any],
    max_work_packages: int,
    max_microtasks_per_package: int,
    max_discovery_domains: int,
    question_option_schema: dict[str, Any],
    available_gates: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the nine fresh public contracts independently of handlers."""
    # The runtime supplies these from its authoritative contract.  Keeping a
    # small fallback makes this registry independently importable in schema
    # tooling and tests, while the installed facade never relies on it.
    canonical_gates = set(available_gates or {
        "scope", "plan", "discover", "architecture", "database_architecture",
        "implementation", "qa", "security", "performance", "accessibility",
        "ux", "review", "documentation", "close", "governance_activation",
        "governance_close",
    })
    public_phase_values = sorted(canonical_gates)
    public_profile_values = sorted(set(agents))
    phase_profile_values: dict[str, list[str]] = {}
    for gate in public_phase_values:
        allowed: list[str] = []
        for name, raw_profile in agents.items():
            profile = raw_profile if isinstance(raw_profile, Mapping) else {}
            gates = profile.get("gates") if isinstance(profile.get("gates"), list) else []
            route_category = str(profile.get("route_category") or "")
            if gate in gates or (gate == "implementation" and route_category == "manual"):
                allowed.append(str(name))
            elif not gates and not route_category and gate == "implementation":
                # Pure schema tooling may supply only names/descriptions. Its
                # narrow fallback mirrors the runtime's manual implementation
                # rule without inventing ownership for any other phase.
                allowed.append(str(name))
        phase_profile_values[gate] = sorted(set(allowed))
    worker_ref_properties = {
        "task_ref": {"type": "string", "pattern": "^task-[0-9a-f]{12}$", "description": "Exact task_ref from the native dispatch."},
        "assignment_ref": {"type": "string", "pattern": "^assignment-v1-[0-9a-f]{64}$", "description": "Exact opaque assignment capability from the native dispatch."},
    }
    worker_question_type_schema = {
        "type": "string",
        "enum": ["single_select", "multi_select", "text"],
        "description": "Explicit answer shape. Never replace this discriminator with answer_mode, type, multiple, context, or field-presence inference.",
    }
    worker_question_scope_schema = {
        "type": "string",
        "enum": [
            "task", "task_decision", "requirement", "requirements", "scope", "product",
            "acceptance", "acceptance_criteria", "external_authorization",
            "destructive_authorization", "user_choice",
        ],
        "description": "Exact material user-decision boundary; Cortex-internal lifecycle, validation, routing, ledger, or worker conditions are forbidden.",
    }
    worker_question_option_schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "option_id": {
                "type": "string", "pattern": "^[a-z0-9][a-z0-9._:-]{0,159}$",
                "description": "Stable option identifier; recommended_option_ids must use this exact value.",
            },
            "label": {"type": "string", "minLength": 1},
            "label_en": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
        },
        "required": ["option_id"],
        "anyOf": [{"required": ["label"]}, {"required": ["label_en"]}],
    }

    def worker_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
        value = json.loads(json.dumps(dict(schema), ensure_ascii=False))
        value.setdefault("properties", {}).update(worker_ref_properties)
        value["required"] = list(dict.fromkeys(["task_ref", "assignment_ref", *(value.get("required") or [])]))
        return value
    EXECUTED_TEST_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "command": {
                "type": "string",
                "minLength": 2,
                "description": "Exact reproducible command that was executed; placeholders such as ... are forbidden.",
            },
            "cwd": {
                "type": "string",
                "minLength": 1,
                "description": "Exact project root or safe project-relative working directory used for the command.",
            },
            "exit_code": {"type": "integer", "const": 0},
            "evidence": {
                "type": "string",
                "minLength": 1,
                "description": "Decisive observed output or behavior from this executed command.",
            },
        },
        "required": ["command", "cwd", "exit_code", "evidence"],
    }
    PLANNING_STRING_LIST_SCHEMA = {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1},
    }
    PLANNING_PATHS_SCHEMA = {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1},
    }
    PLANNING_NARROW_PATHS_SCHEMA = {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {
            "type": "string",
            "minLength": 1,
            "not": {"enum": [".", "*"]},
        },
    }
    PLANNING_DEPENDENCIES_SCHEMA = {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string", "maxLength": 80, "pattern": "^[a-z0-9][a-z0-9_-]*$"},
    }
    PLANNING_COVERAGE_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "requirement": {"type": "string", "minLength": 1},
            "plan_refs": {
                "type": "array", "minItems": 1, "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "maxLength": 160},
            },
            "verification": PLANNING_STRING_LIST_SCHEMA,
            "status": {"type": "string", "enum": ["covered"]},
        },
        "required": ["requirement", "plan_refs", "verification", "status"],
    }
    REQUIRED_ARTIFACT_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "minLength": 1, "description": "Project-relative path that must exist when the owning gate completes."},
            "kind": {"type": "string", "enum": ["file", "test_suite", "fixture", "cli", "document", "report", "schema", "config", "other"], "description": "Artifact kind."},
            "owner_gate": {"type": "string", "enum": sorted(canonical_gates)},
            "verification": {"type": "string", "minLength": 1, "description": "Exact trusted verification id or command contract for this artifact."},
        },
        "required": ["path", "kind", "owner_gate", "verification"],
    }
    PLANNING_MICROTASK_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "maxLength": 80, "pattern": "^[a-z0-9][a-z0-9_-]*$"},
            "title": {"type": "string", "minLength": 1},
            "objective": {"type": "string", "minLength": 1},
            "profile": {
                "type": "string",
                "enum": public_profile_values,
                "description": "Optional canonical Cortex profile name; omit it to use the phase owner.",
            },
            "allowed_paths": PLANNING_NARROW_PATHS_SCHEMA,
            "depends_on": PLANNING_DEPENDENCIES_SCHEMA,
            "status": {"type": "string", "enum": ["pending", "ready", "running", "blocked", "completed", "skipped"]},
            "order": {"type": "integer", "minimum": 1},
            "gates": PLANNING_STRING_LIST_SCHEMA,
            "acceptance_criteria": PLANNING_STRING_LIST_SCHEMA,
            "verification": PLANNING_STRING_LIST_SCHEMA,
            "required_artifacts": {
                "type": "array", "uniqueItems": True, "items": REQUIRED_ARTIFACT_SCHEMA,
                "description": "Machine-readable deliverables checked against the project workspace at implementation/QA completion.",
            },
        },
        "required": ["id", "title", "objective", "profile", "allowed_paths", "acceptance_criteria", "verification"],
    }
    PLANNING_PACKAGE_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "Canonical package shape: id, title, objective, optional package routing/tracker fields, "
            "and microtasks. Package-level acceptance_criteria, verification, dependencies, profile, "
            "or other worker fields are not allowed; put those fields on each microtask."
        ),
        "properties": {
            "id": {"type": "string", "maxLength": 80, "pattern": "^[a-z0-9][a-z0-9_-]*$"},
            "title": {"type": "string", "minLength": 1},
            "objective": {"type": "string", "minLength": 1},
            "allowed_paths": PLANNING_PATHS_SCHEMA,
            "depends_on": PLANNING_DEPENDENCIES_SCHEMA,
            "status": {"type": "string", "enum": ["pending", "ready", "running", "blocked", "completed", "skipped"]},
            "order": {"type": "integer", "minimum": 1},
            "gates": PLANNING_STRING_LIST_SCHEMA,
            "required_artifacts": {
                "type": "array", "uniqueItems": True, "items": REQUIRED_ARTIFACT_SCHEMA,
                "description": "Package-level deliverables checked against the project workspace at their owner gate.",
            },
            "microtasks": {
                "type": "array",
                "minItems": 1,
                "items": PLANNING_MICROTASK_SCHEMA,
            },
        },
        "required": ["id", "title", "objective", "microtasks"],
    }
    V3_PLANNING_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "overview": {"type": "string", "minLength": 1},
            "requirement_coverage": {
                "type": "array", "uniqueItems": True,
                "items": PLANNING_COVERAGE_SCHEMA,
                "description": (
                    "Optional traceability map. When the user changes an active task, every latest requirement "
                    "must appear here exactly once with the plan items and verification that cover it."
                ),
            },
            "recommendation": {"type": "string", "enum": ["approve", "revise"]},
            "recommendation_rationale": {"type": "string", "minLength": 1},
            "recommendation_actions": {
                "type": "array",
                "description": "Concrete corrective actions required by the planner recommendation; required when recommendation=revise.",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "issue": {"type": "string", "minLength": 1},
                        "action": {"type": "string", "minLength": 1},
                        "plan_refs": {"type": "array", "items": {"type": "string", "minLength": 1}},
                        "verification": {"type": "string", "minLength": 1},
                    },
                    "required": ["issue", "action", "plan_refs", "verification"],
                },
            },
            "resolved_questions": {
                "type": "array", "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "risks": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "work_packages": {
                "type": "array", "minItems": 1,
                "description": (
                    "Planner-only task-local work breakdown. Runtime requires each package to have id, title, objective, "
                    "and non-empty microtasks, and writes the validated artifact to the host-private task projection store."
                ),
                "items": PLANNING_PACKAGE_SCHEMA,
            },
        },
        "required": ["overview", "work_packages"],
    }
    SCOPING_DOMAIN_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "maxLength": 80, "pattern": "^[a-z0-9][a-z0-9_-]*$"},
            "title": {"type": "string", "minLength": 1},
            "objective": {"type": "string", "minLength": 1},
            "paths": PLANNING_PATHS_SCHEMA,
            "context": PLANNING_STRING_LIST_SCHEMA,
            "depends_on": PLANNING_DEPENDENCIES_SCHEMA,
            "acceptance_criteria": PLANNING_STRING_LIST_SCHEMA,
            "verification": PLANNING_STRING_LIST_SCHEMA,
        },
        "required": [
            "id", "title", "objective", "paths", "context", "depends_on",
            "acceptance_criteria", "verification",
        ],
    }
    V3_SCOPING_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "overview": {"type": "string", "minLength": 1},
            "context_files": {
                "type": "array", "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "discovery_domains": {
                "type": "array", "minItems": 1,
                "items": SCOPING_DOMAIN_SCHEMA,
            },
        },
        "required": ["overview", "context_files", "discovery_domains"],
    }
    V3_WORKER_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "profile": {
                "type": "string",
                "description": (
                    "Optional expert override. Omit it to use the canonical server-owned phase owner; "
                    "when supplied, the containing wave phase branch is the complete allowed enum."
                ),
            },
            "objective": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
            "allowed_paths": {
                "type": "array", "minItems": 1,
                "items": {
                    "type": "string", "minLength": 1,
                    "pattern": r"^(?!\s)(?!.*\s$)(?!.*\x00)(?!/)(?![A-Za-z]:[\\/])(?!.*(?:^|/)\.\.(?:/|$))(?!.*[\\*?\[\]])(?!\.$).+$",
                    "format": "project-relative-path",
                },
                "description": (
                    "Canonical server-owned worker write scope. Paths must be narrow, project-relative, and "
                    "must not be `.` or `*`; Cortex validates and normalizes them before dispatch."
                ),
            },
            "acceptance": {"type": "array", "items": {"type": "string"}},
            "verification": {"type": "array", "items": {"type": "string"}},
            "context_files": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
                "description": (
                    "Task-relevant project/feature knowledge pages selected from the repository indexes. "
                    "Cortex also injects docs/project/index.md and docs/features/index.md when present."
                ),
            },
            "depends_on": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "enum": public_phase_values},
                "description": (
                    "Optional exact prerequisite phases whose verified AttemptResults this worker must receive. "
                    "Omit to receive every completed predecessor result; use an empty list only when the worker "
                    "is intentionally independent."
                ),
            },
            "context_result_refs": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
                "description": (
                    "Optional exact immutable AttemptResult refs that must be supplied to this worker. "
                    "Use for corrective handoffs that must retain an origin result; Cortex validates that "
                    "each result belongs to the current task before dispatch."
                ),
            },
            "model": {"type": "string", "enum": list(CANONICAL_MODELS), "description": "Optional expert model override using its canonical model identifier."},
            "user_requested_model": {
                "type": "string", "enum": list(CANONICAL_MODELS),
                "description": (
                    "Model explicitly requested by the user using its canonical model identifier. "
                    "Non-security Sol is rejected unless it is supplied through this field."
                ),
            },
            "effort": {"type": "string", "description": "Optional expert reasoning-effort override."},
        },
    }
    V3_WAVE_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "phase": {
                "type": "string", "minLength": 1, "enum": public_phase_values,
                "description": (
                    "The single canonical phase inherited by every worker in this wave. Multiple workers may share "
                    "this phase; workers for another phase belong in a separate wave."
                ),
            },
            "workers": {"type": "array", "minItems": 1, "maxItems": 32, "items": V3_WORKER_SCHEMA},
        },
        "required": ["phase", "workers"],
        "oneOf": [
            {
                "properties": {
                    "phase": {"const": gate},
                    "workers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "profile": {
                                    "type": "string",
                                    "enum": phase_profile_values[gate],
                                    "description": (
                                        f"Optional expert override for wave phase {gate}; omit it to use the canonical server owner."
                                    ),
                                },
                            },
                        },
                    },
                },
            }
            for gate in public_phase_values
        ],
    }
    START_ORCHESTRATION_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "project_root": {"type": "string", "minLength": 1, "format": "absolute-path", "description": "Exact absolute project workspace."},
            "task": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "user_request": {"type": "string", "minLength": 1, "description": "Exact user-authored task text. Do not paraphrase, normalize, or expand it."},
                    "requirements": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "constraints": {"type": "array", "items": {"type": "string", "minLength": 1}, "description": "Explicit non-negotiable task constraints compiled as first-class canonical context."},
                    "acceptance_criteria": {
                        "type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1},
                        "description": "Required observable outcomes, except harvest routes where Cortex supplies the exhaustive census contract.",
                    },
                    "scope": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "allowed_paths": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "verification": {
                        "type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1},
                        "description": "Required authoritative checks, except harvest routes where Cortex supplies the census checks.",
                    },
                    "budget": {"type": "string"},
                    "pause_conditions": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "plan_approval": {
                        "type": "string",
                        "enum": ["auto", "required"],
                        "description": (
                            "Post-plan user review policy. Defaults to auto for every complexity; "
                            "required is valid only when the user explicitly requested plan approval. "
                            "Governance, Planner, risk, and review recommendations never request approval "
                            "on the user's behalf."
                        ),
                    },
                    "initiative_ref": {"type": "string", "pattern": "^initiative-[A-Za-z0-9_.:-]+$", "description": "Optional existing initiative scope; the server verifies the reference before task creation."},
                    "governance_mode": {"type": "string", "enum": ["auto", "required", "off"], "description": "Requested governance floor. required always resolves to full. off is valid only for C1 after risk_triggers supplies every documented hard/topology trigger as an explicit boolean false; text and positive structured triggers still force full governance."},
                    "risk_triggers": {"type": ["array", "object"], "description": "Explicit stated governance trigger classes. governance_mode=off requires an exhaustive boolean object; auto/required may use an object or array. No numeric scope inference is applied."},
                    "governance_triggers": {"type": ["array", "object"], "description": "Explicit stated governance trigger classes; no numeric scope inference is applied."},
                    "multiple_repositories": {"type": "boolean"},
                    "related_tasks": {"type": "boolean"},
                    "long_lived_lanes": {"type": "boolean"},
                    "conflicting_resources": {"type": "boolean"},
                    "multi_session_handoff": {"type": "boolean"},
                    "user_language": {"type": "string"},
                    "communication_profile": {"type": "string", "enum": ["natural", "compact", "technical"], "default": "natural", "description": "User-facing message style."},
                    "complexity": {"type": "string", "enum": ["C1", "C2", "C3"], "description": "Optional canonical complexity; defaults to C2."},
                },
                "required": ["user_request"],
                "anyOf": [
                    {
                        "required": ["acceptance_criteria", "verification"],
                        "description": "Every ordinary task must provide a complete observable result contract before dispatch.",
                    },
                    {
                        "properties": {
                            "user_request": {
                                "pattern": "(?<![A-Za-z0-9])[Hh][Aa][Rr][Vv][Ee][Ss][Tt](?:-[Rr][Ee][Ff][Rr][Ee][Ss][Hh])?(?![A-Za-z0-9])",
                            }
                        },
                        "description": "Knowledge-harvest routes may omit either list because Cortex supplies the exhaustive census contract.",
                    },
                ],
            },
            "waves": {"type": "array", "minItems": 1, "items": V3_WAVE_SCHEMA},
        },
        "required": ["project_root", "task"],
    }
    CONTINUE_ORCHESTRATION_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_ref": {"type": "string", "pattern": "^task-[0-9a-f]{12}$", "description": "Exact opaque task reference returned by start_orchestration; required for every continuation."},
            "coordinator_ref": {"type": "string", "pattern": COORDINATOR_REF_PATTERN, "description": "Exact coordinator capability returned only by start_orchestration."},
            "step": {"type": "integer", "minimum": 1, "description": "Relative step returned by the preceding Cortex response; enables safe idempotent replay without a wave identifier."},
            "results": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "worker": {"type": "integer", "minimum": 1, "description": "Required only for a parallel wave."},
                        "attempt_result_ref": {"type": "string", "minLength": 1, "description": "Bare canonical result ref from read_worker_result. Cortex derives semantic status and evidence from the immutable AttemptResult; inline result bodies and caller-authored status are forbidden."},
                    },
                    "required": ["attempt_result_ref"],
                },
            },
        },
        "required": ["task_ref", "coordinator_ref", "step", "results"],
    }
    WORKER_RECORD_ATTEMPT_EVENT_SCHEMA = worker_schema({
        "type": "object",
        "additionalProperties": False,
        "description": "Append one lossless semantic checkpoint. Identity, timestamps, workspace state, read receipts, and projection status are server-owned; content volume is advisory in prompts only.",
        "properties": {
            "event_type": {
                "type": "string",
                "enum": ["finding_added", "decision_evidence", "blocker", "verification_claimed", "progress", "note"],
            },
            "payload": {
                "description": "Bounded worker semantic fact. Use verification_claimed for a worker assertion; only Cortex records verification_observed after a trusted server-side observation.",
            },
            "event_key": {
                "type": "string",
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
                "description": "Optional stable idempotency key for this fact; Cortex derives one from content when omitted.",
            },
        },
        "required": ["event_type", "payload"],
    })
    WORKER_COMPLETE_ATTEMPT_SCHEMA = json.loads(json.dumps(PUBLIC_SUBMISSION_SCHEMA, ensure_ascii=False))
    question_ref_schema = {
        "type": "string",
        "pattern": "^question-[0-9]+$",
        "description": "Exact scalar ref returned by ask. Copy it byte-for-byte; never wrap it in an object.",
    }
    batch_ref_schema = {
        "type": "string",
        "pattern": "^batch-[0-9a-f]{24}$",
        "description": "Exact scalar ref returned by ask_batch. Copy it byte-for-byte.",
    }
    question_key_schema = {
        "type": "string",
        "pattern": "^[a-z0-9][a-z0-9._:-]{0,159}$",
        "description": "Stable caller-defined identifier preserved across the durable question lifecycle.",
    }
    question_text_schema = {
        "type": "string", "minLength": 1,
        "description": "Material user decision; required when asking.",
    }
    question_recommendation_schema = {
        "type": "string", "minLength": 1,
        "description": "Required LLM rationale for the recommended answer.",
    }
    recommended_ids_schema = {
        "type": "array", "minItems": 1, "uniqueItems": True,
        "items": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._:-]{0,159}$"},
        "description": "Exact stable option IDs the LLM recommends.",
    }
    recommended_answer_schema = {
        "type": "string", "minLength": 1,
        "description": "Concrete answer wording the LLM recommends for a text question.",
    }

    def closed_question_form(
        action: str,
        properties: Mapping[str, Any],
        required: list[str],
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                **worker_ref_properties,
                "action": {"type": "string", "const": action},
                **properties,
            },
            "required": ["task_ref", "assignment_ref", "action", *required],
        }

    ask_common_properties = {
        "question": question_text_schema,
        "header": {"type": "string"},
        "custom_label": {"type": "string"},
        "decision_scope": worker_question_scope_schema,
        "recommendation": question_recommendation_schema,
    }
    single_choice_ask = closed_question_form(
        "ask",
        {
            **ask_common_properties,
            "question_type": {"type": "string", "const": "single_select"},
            "options": {
                "type": "array", "minItems": 1,
                "items": worker_question_option_schema,
                "description": "Stable choice options for a single-select question.",
            },
            "recommended_option_ids": {
                **recommended_ids_schema,
                "maxItems": 1,
                "description": "The one exact option_id recommended for this single-select question.",
            },
        },
        ["question", "question_type", "decision_scope", "recommendation", "options", "recommended_option_ids"],
    )
    multi_choice_ask = closed_question_form(
        "ask",
        {
            **ask_common_properties,
            "question_type": {"type": "string", "const": "multi_select"},
            "options": {
                "type": "array", "minItems": 1,
                "items": worker_question_option_schema,
                "description": "Stable choice options for a multi-select question.",
            },
            "recommended_option_ids": recommended_ids_schema,
        },
        ["question", "question_type", "decision_scope", "recommendation", "options", "recommended_option_ids"],
    )
    text_ask = closed_question_form(
        "ask",
        {
            **ask_common_properties,
            "question_type": {"type": "string", "const": "text"},
            "recommended_answer": recommended_answer_schema,
        },
        ["question", "question_type", "decision_scope", "recommendation", "recommended_answer"],
    )
    ask_branch = {
        "type": "object",
        "properties": {"action": {"const": "ask"}},
        "required": ["action"],
        "allOf": [{"oneOf": [single_choice_ask, multi_choice_ask, text_ask]}],
    }

    batch_common_properties = {
        "question_key": question_key_schema,
        "question": question_text_schema,
        "header": {"type": "string"},
        "custom_label": {"type": "string"},
        "decision_scope": worker_question_scope_schema,
        "recommendation": question_recommendation_schema,
    }

    def closed_batch_question(
        question_type: str,
        properties: Mapping[str, Any],
        required: list[str],
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                **batch_common_properties,
                "question_type": {"type": "string", "const": question_type},
                **properties,
            },
            "required": ["question_key", "question", "question_type", "decision_scope", "recommendation", *required],
        }

    batch_question_schema = {
        "type": "object",
        "oneOf": [
            closed_batch_question(
                "single_select",
                {
                    "options": {"type": "array", "minItems": 1, "items": worker_question_option_schema},
                    "recommended_option_ids": {**recommended_ids_schema, "maxItems": 1},
                },
                ["options", "recommended_option_ids"],
            ),
            closed_batch_question(
                "multi_select",
                {
                    "options": {"type": "array", "minItems": 1, "items": worker_question_option_schema},
                    "recommended_option_ids": recommended_ids_schema,
                },
                ["options", "recommended_option_ids"],
            ),
            closed_batch_question(
                "text",
                {"recommended_answer": recommended_answer_schema},
                ["recommended_answer"],
            ),
        ],
    }
    worker_question_batch_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "batch_key": question_key_schema,
            "questions": {
                "type": "array", "minItems": 1, "maxItems": 32,
                "items": batch_question_schema,
            },
        },
        "required": ["batch_key", "questions"],
    }

    poll_branch = closed_question_form("poll", {"question_ref": question_ref_schema}, ["question_ref"])
    ask_batch_branch = closed_question_form("ask_batch", {"batch": worker_question_batch_schema}, ["batch"])
    poll_batch_branch = closed_question_form("poll_batch", {"batch_ref": batch_ref_schema}, ["batch_ref"])

    WORKER_QUESTION_SCHEMA = worker_schema({
        "type": "object",
        "additionalProperties": False,
        "description": (
            "Closed action union; preserve task_ref and assignment_ref on every branch. ask requires explicit top-level "
            "question_type and decision_scope. single_select/multi_select use stable options plus recommended_option_ids; "
            "text uses recommended_answer. answer_mode, context, type, multiple, and implicit field-presence inference are forbidden. "
            "poll accepts only the exact scalar question_ref; action is the literal string 'poll', never a resume object. "
            "ask_batch accepts only batch and poll_batch accepts only the exact scalar batch_ref beyond worker authorization. "
            "Every validation response has top-level error and recovery; a retryable non-mutating recovery names the exact JSON pointer to correct on this same worker."
        ),
        "properties": {
            "action": {"type": "string", "enum": ["ask", "poll", "ask_batch", "poll_batch"]},
            "question_ref": question_ref_schema,
            "batch_ref": batch_ref_schema,
            "question": question_text_schema,
            "question_type": worker_question_type_schema,
            "decision_scope": worker_question_scope_schema,
            "header": {"type": "string"},
            "options": {"type": "array", "minItems": 1, "items": worker_question_option_schema, "description": "Choice options for ask. Each item needs stable option_id so recommended_option_ids is unambiguous."},
            "custom_label": {"type": "string"},
            "recommendation": question_recommendation_schema,
            "recommended_option_ids": recommended_ids_schema,
            "recommended_answer": recommended_answer_schema,
            "batch": worker_question_batch_schema,
        },
        "required": ["action"],
        "allOf": [
            {"oneOf": [ask_branch, poll_branch, ask_batch_branch, poll_batch_branch]},
        ],
    })
    READ_WORKER_RESULT_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_ref": {"type": "string", "pattern": "^task-[0-9a-f]{12}$", "description": "Exact opaque task reference; required for every result read."},
            "assignment_ref": worker_ref_properties["assignment_ref"],
            "coordinator_ref": {"type": "string", "pattern": COORDINATOR_REF_PATTERN},
            "attempt_result_ref": {"type": "string", "minLength": 1, "description": "Exact predecessor result capability granted to a worker; never transported from a completed child to the coordinator."},
            "step": {"type": "integer", "minimum": 1, "description": "Exact current step returned with the coordinator dispatch. Cortex derives every expected canonical result for this wave."},
        },
        "required": ["task_ref"],
        "oneOf": [
            {
                "required": ["coordinator_ref", "step"],
                "not": {"anyOf": [{"required": ["assignment_ref"]}, {"required": ["attempt_result_ref"]}]},
            },
            {
                "required": ["assignment_ref", "attempt_result_ref"],
                "not": {"anyOf": [{"required": ["coordinator_ref"]}, {"required": ["step"]}]},
            },
        ],
    }
    READ_DISPATCH_BRIEFING_SCHEMA = worker_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "cursor": {"type": "string", "minLength": 1, "description": "Opaque continuation cursor for the same bounded immutable briefing read."},
            "max_bytes": {"type": "integer", "minimum": 1, "maximum": 64 * 1024, "description": "Optional caller-selected UTF-8 briefing page size. Omit it for the server default; every page remains bounded."},
        },
        "required": [],
    })
    management_refs = {
        "task_ref": {
            "type": "string", "pattern": TASK_REF_PATTERN,
            "description": "Exact task reference returned by start_orchestration.",
        },
        "coordinator_ref": {
            "type": "string", "pattern": COORDINATOR_REF_PATTERN,
            "description": "Exact coordinator capability returned only by start_orchestration.",
        },
    }
    text_list = {"type": "array", "items": {"type": "string", "minLength": 1}}
    unique_refs = {
        "type": "array", "uniqueItems": True,
        "items": {"type": "string", "minLength": 1},
    }

    def closed_payload(
        properties: Mapping[str, Any],
        required: Sequence[str],
        *,
        description: str | None = None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "type": "object", "additionalProperties": False,
            "properties": dict(properties), "required": list(required),
        }
        if description:
            value["description"] = description
        return value

    def management_branch(
        intent: str,
        *,
        payload: Mapping[str, Any] | None = None,
        reason: bool = False,
    ) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "intent": {"type": "string", "const": intent},
            **management_refs,
        }
        required = ["intent", "task_ref", "coordinator_ref"]
        if payload is not None:
            properties["payload"] = dict(payload)
            required.append("payload")
        if reason:
            properties["reason"] = {"type": "string", "minLength": 1}
        return {
            "type": "object", "additionalProperties": False,
            "properties": properties, "required": required,
        }

    question_ref = {
        "type": "string", "minLength": 1,
        "description": "Exact durable question_ref or batch_ref returned by Cortex.",
    }
    question_payload = {
        "oneOf": [
            closed_payload({"question_ref": question_ref}, ["question_ref"], description="Display the stored canonical durable question and its stored canonical options. This form is sufficient in every user language and requires no coordinator-authored option projection."),
            closed_payload({
                "question_ref": question_ref,
                "localized_question": {"type": "string", "minLength": 1, "description": "Optional display translation of the stored canonical prompt."},
                "localized_header": {"type": "string", "minLength": 1},
                "localized_options": {
                    "type": "array", "minItems": 1, "items": question_option_schema,
                    "description": "Optional ordered display translations. If supplied, provide exactly one item per stored canonical option; omit this field when the canonical labels are acceptable.",
                },
                "localized_custom_label": {"type": "string", "minLength": 1},
            }, ["question_ref", "localized_question"], description="Optionally translate the durable question display. Header, option labels, and custom label are display-only and may be omitted; canonical option IDs and count remain server-owned."),
            closed_payload({"question_ref": question_ref, "answer": {}}, ["question_ref", "answer"], description="Submit the user's answer to one durable question."),
            closed_payload({
                "question_ref": question_ref, "answer": {},
                "answer_en": {"type": "string", "minLength": 1},
            }, ["question_ref", "answer", "answer_en"], description="Submit the original non-English answer and its canonical English translation."),
            closed_payload({
                "question_ref": question_ref,
                "answers": {"type": "object", "minProperties": 1, "additionalProperties": {}},
            }, ["question_ref", "answers"], description="Submit keyed answers for one durable question batch."),
            closed_payload({
                "question_ref": question_ref,
                "canonical_answers": {
                    "type": "object", "minProperties": 1,
                    "additionalProperties": {"type": "string", "minLength": 1},
                },
            }, ["question_ref", "canonical_answers"], description="Submit canonical English translations for batch free-text answers."),
        ],
    }

    plan_prompt = closed_payload({"decision": {"const": "prompt"}}, ["decision"])
    plan_prompt_localized = closed_payload({
        "decision": {"const": "prompt"},
        "localized_prompt": {"type": "string", "minLength": 1},
        "localized_title": {"type": "string", "minLength": 1},
        "localized_approve": {"type": "string", "minLength": 1},
        "localized_cancel": {"type": "string", "minLength": 1},
        "localized_custom_label": {"type": "string", "minLength": 1},
    }, ["decision", "localized_prompt", "localized_title", "localized_approve", "localized_cancel", "localized_custom_label"])
    plan_payload = {"oneOf": [
        plan_prompt,
        plan_prompt_localized,
        *[
            closed_payload({
                "decision": {"const": decision},
                "request_id": {"type": "string", "minLength": 1},
            }, ["decision", "request_id"])
            for decision in ("approve_with_recommendations", "approve_without_recommendations", "cancel")
        ],
        closed_payload({
            "decision": {"const": "revise"},
            "request_id": {"type": "string", "minLength": 1},
            "feedback": {"type": "string", "minLength": 1},
        }, ["decision", "request_id", "feedback"]),
    ]}

    follow_up_payload = closed_payload({
        "user_request": {"type": "string", "minLength": 1},
        "requirements": text_list,
        "constraints": text_list,
        "acceptance_criteria": {**text_list, "minItems": 1},
        "scope": text_list,
        "allowed_paths": text_list,
        "verification": {**text_list, "minItems": 1},
        "budget": {"type": "string"},
        "pause_conditions": text_list,
        "user_language": {"type": "string", "minLength": 1},
        "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]},
        "plan_approval": {"type": "string", "enum": ["auto", "required"]},
        "result_refs": unique_refs,
    }, ["user_request", "acceptance_criteria", "verification"])

    lane_declaration = closed_payload({
        "repo_path": {"type": "string", "minLength": 1},
        "worktree_path": {"type": "string", "minLength": 1},
        "branch": {"type": "string", "minLength": 1},
        "sync_from": {"type": "string", "minLength": 1},
    }, ["repo_path", "worktree_path", "branch"])
    lane_payload = {"oneOf": [
        closed_payload({
            "command": {"const": "create"}, "lane_id": {"type": "string", "minLength": 1},
            "mode": {"type": "string", "enum": ["ephemeral", "persistent"]},
            "purpose": {"type": "string"},
            "declarations": {"type": "array", "items": lane_declaration},
        }, ["command", "lane_id"]),
        closed_payload({"command": {"const": "inspect"}, "lane_id": {"type": "string", "minLength": 1}}, ["command", "lane_id"]),
        closed_payload({
            "command": {"const": "claim"}, "lane_id": {"type": "string", "minLength": 1},
            "run_id": {"type": "string", "minLength": 1}, "expires_at": {"type": "string", "minLength": 1},
            "reclaim": {"type": "boolean"},
        }, ["command", "lane_id", "expires_at"]),
        closed_payload({
            "command": {"const": "release"}, "lane_id": {"type": "string", "minLength": 1},
            "run_id": {"type": "string", "minLength": 1},
        }, ["command", "lane_id"]),
        closed_payload({
            "command": {"const": "retire"}, "lane_id": {"type": "string", "minLength": 1},
            "clean": {"const": True}, "confirm": {"const": True},
        }, ["command", "lane_id", "clean"]),
        closed_payload({"command": {"const": "bind_task"}, "lane_id": {"type": "string", "minLength": 1}}, ["command", "lane_id"]),
        closed_payload({
            "command": {"const": "materialize"}, "lane_id": {"type": "string", "minLength": 1},
            "run_id": {"type": "string", "minLength": 1}, "confirm": {"const": True},
        }, ["command", "lane_id", "confirm"]),
        closed_payload({
            "command": {"const": "reconcile"}, "lane_id": {"type": "string", "minLength": 1},
            "run_id": {"type": "string", "minLength": 1},
        }, ["command", "lane_id"]),
        closed_payload({
            "command": {"const": "claim_resource"}, "lane_id": {"type": "string", "minLength": 1},
            "path": {"type": "string", "minLength": 1}, "owner": {"type": "string", "minLength": 1},
            "kind": {"type": "string", "minLength": 1}, "expires_at": {"type": "string", "minLength": 1},
        }, ["command", "lane_id", "path", "owner", "expires_at"]),
        closed_payload({
            "command": {"const": "release_resource"}, "lane_id": {"type": "string", "minLength": 1},
            "path": {"type": "string", "minLength": 1}, "owner": {"type": "string", "minLength": 1},
        }, ["command", "lane_id", "path", "owner"]),
    ]}

    resource_payload = {"oneOf": [
        closed_payload({
            "command": {"const": "claim"}, "path": {"type": "string", "minLength": 1},
            "owner": {"type": "string", "minLength": 1}, "gate": {"type": "string", "minLength": 1},
            "expires_at": {"type": "string", "minLength": 1}, "kind": {"type": "string", "minLength": 1},
        }, ["command", "path", "owner"]),
        closed_payload({
            "command": {"const": "release"}, "path": {"type": "string", "minLength": 1},
            "owner": {"type": "string", "minLength": 1},
        }, ["command", "path", "owner"]),
        closed_payload({
            "command": {"const": "acquire_lock"}, "path": {"type": "string", "minLength": 1},
            "owner": {"type": "string", "minLength": 1}, "gate": {"type": "string", "minLength": 1},
            "expires_at": {"type": "string", "minLength": 1}, "advisory": {"type": "boolean"},
        }, ["command", "path", "owner"]),
        closed_payload({
            "command": {"const": "release_lock"}, "path": {"type": "string", "minLength": 1},
            "owner": {"type": "string", "minLength": 1},
        }, ["command", "path", "owner"]),
    ]}

    artifact_payload = {"oneOf": [
        closed_payload({
            "action": {"const": "list"}, "kind": {"type": "string", "minLength": 1},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 256},
            "cursor": {"type": "string", "minLength": 1},
        }, ["action"]),
        closed_payload({
            "action": {"const": "metadata"}, "artifact_ref": {"type": "string", "minLength": 1},
        }, ["action", "artifact_ref"]),
        closed_payload({
            "action": {"const": "read"}, "artifact_ref": {"type": "string", "minLength": 1},
            "cursor": {"type": "string", "minLength": 1},
            "max_bytes": {"type": "integer", "minimum": 1},
        }, ["action", "artifact_ref"]),
    ]}

    finalizer_payloads = {
        "finalize_bootstrap_failure": closed_payload({
            "dispatch_ref": {"type": "string", "pattern": "^dispatch-[0-9a-f]{24}$"},
            "reason_code": {"const": "bootstrap_missing_identity"},
        }, ["dispatch_ref", "reason_code"]),
        "finalize_worker_failure": closed_payload({
            "dispatch_ref": {"type": "string", "pattern": "^dispatch-[0-9a-f]{24}$"},
            "reason_code": {"const": "worker_nonretryable_terminal"},
        }, ["dispatch_ref", "reason_code"]),
    }

    MANAGE_ORCHESTRATION_SCHEMA = {
        "type": "object",
        "description": (
            "Closed coordinator management union. Select exactly one canonical intent branch and send only "
            "that branch's fields. Every call requires the exact task_ref and coordinator_ref returned by Cortex; "
            "project_root and caller-authored lifecycle identity are never accepted."
        ),
        "oneOf": [
            management_branch("inspect"),
            management_branch("recover_inspect"),
            management_branch("recover_blocked", reason=True),
            management_branch("resume", reason=True),
            management_branch("deactivate", reason=True),
            management_branch("lane", payload=lane_payload),
            management_branch("resource", payload=resource_payload),
            management_branch("question", payload=question_payload),
            management_branch("plan_approval", payload=plan_payload),
            management_branch("follow_up", payload=follow_up_payload),
            management_branch("steer", payload=closed_payload({
                "user_message": {"type": "string", "minLength": 1},
                "user_language": {"type": "string", "minLength": 1},
                "message_en": {"type": "string", "minLength": 1},
            }, ["user_message"])),
            management_branch("artifacts", payload=artifact_payload),
            management_branch("finalize_bootstrap_failure", payload=finalizer_payloads["finalize_bootstrap_failure"]),
            management_branch("finalize_worker_failure", payload=finalizer_payloads["finalize_worker_failure"]),
        ],
    }
    governance_refs = {
        "task_ref": {
            "type": "string", "pattern": TASK_REF_PATTERN,
            "description": "Exact task reference returned by start_orchestration.",
        },
        "coordinator_ref": {
            "type": "string", "pattern": COORDINATOR_REF_PATTERN,
            "description": "Exact coordinator capability returned only by start_orchestration.",
        },
    }

    def governance_branch(
        action: str,
        properties: Mapping[str, Any],
        required: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "type": "object", "additionalProperties": False,
            "properties": {
                "action": {"type": "string", "const": action},
                **governance_refs,
                **dict(properties),
            },
            "required": ["action", "task_ref", "coordinator_ref", *required],
        }

    initiative_ref_schema = {"type": "string", "pattern": "^initiative-[A-Za-z0-9_.:-]+$"}
    task_id_schema = {"type": "string", "minLength": 1}
    record_type_schema = {
        "type": "string",
        "enum": ["decision", "ruling", "preference", "assumption", "risk", "learning", "reflection"],
        "description": "Task-scoped coordinator capabilities cannot create policy, exception, or promotion records.",
    }
    paging_properties = {
        "limit": {"type": "integer", "minimum": 1, "maximum": 256},
        "offset": {"type": "integer", "minimum": 0},
    }
    create_record_properties = {
        "initiative_ref": initiative_ref_schema,
        "task_id": task_id_schema,
        "record_type": record_type_schema,
        "content": {},
        "status": {"type": "string", "enum": ["pending", "active", "approved", "rejected", "superseded", "expired"]},
        "supersedes": {"type": "string", "minLength": 1},
        "expires_at": {"type": "string", "minLength": 1},
        "approval_basis": {},
        "content_artifact_ref": {"type": "string", "minLength": 1},
        "record_ref": {"type": "string", "pattern": "^record-[A-Za-z0-9_.:-]+$"},
        "submission_id": {"type": "string", "minLength": 1},
    }
    list_properties = {
        "initiative_ref": initiative_ref_schema,
        "task_id": task_id_schema,
        "record_type": {
            "type": "string",
            "enum": ["policy", "decision", "ruling", "preference", "assumption", "risk", "learning", "reflection", "exception", "promotion"],
        },
        **paging_properties,
    }
    snapshot_properties = {
        "initiative_ref": initiative_ref_schema,
        "task_id": task_id_schema,
        **paging_properties,
    }

    def scoped(properties: Mapping[str, Any], scope: Sequence[str]) -> dict[str, Any]:
        selected = set(scope)
        return {
            key: value
            for key, value in properties.items()
            if key not in {"initiative_ref", "task_id"} or key in selected
        }

    MANAGE_GOVERNANCE_SCHEMA = {
        "type": "object",
        "description": (
            "Closed task-scoped governance union. Use one canonical action and only that action's fields. "
            "Every branch requires the exact task_ref and coordinator_ref; project_root, actor identity, "
            "capability-generation controls, and project-wide policy authority are never model-facing."
        ),
        "oneOf": [
            governance_branch("inspect_initiative", {
                "initiative_ref": initiative_ref_schema,
            }, ["initiative_ref"]),
            governance_branch("link_task", {
                "initiative_ref": initiative_ref_schema, "task_id": task_id_schema,
                "relationship": {"type": "string", "enum": ["milestone", "deliverable", "corrective"]},
                "milestone": {"type": "string", "minLength": 1},
                "deliverable": {"type": "string", "minLength": 1},
                "corrective": {"type": "boolean"},
                "expected_revision": {"type": "integer", "minimum": 1},
            }, ["initiative_ref", "task_id"]),
            governance_branch("add_dependency", {
                "initiative_ref": initiative_ref_schema,
                "source_type": {"type": "string", "enum": ["initiative", "task"]},
                "source_ref": {"type": "string", "minLength": 1},
                "target_type": {"type": "string", "enum": ["initiative", "task"]},
                "target_ref": {"type": "string", "minLength": 1},
                "dependency_type": {"type": "string", "enum": ["blocks", "requires", "relates_to", "follows"]},
                "dependency_ref": {"type": "string", "minLength": 1},
            }, ["initiative_ref", "source_type", "source_ref", "target_type", "target_ref", "dependency_type"]),
            governance_branch("transition_initiative", {
                "initiative_ref": initiative_ref_schema,
                "status": {"type": "string", "enum": ["proposed", "active", "blocked", "completed", "closed", "cancelled"]},
                "expected_revision": {"type": "integer", "minimum": 1},
                "evidence": {"type": "object"},
            }, ["initiative_ref", "status"]),
            *[
                governance_branch("create_record", scoped(create_record_properties, scope), [*scope, "record_type", "content"])
                for scope in (("task_id",), ("initiative_ref",), ("initiative_ref", "task_id"))
            ],
            *[
                governance_branch("list_records", scoped(list_properties, scope), list(scope))
                for scope in (("task_id",), ("initiative_ref",), ("initiative_ref", "task_id"))
            ],
            *[
                governance_branch("snapshot", scoped(snapshot_properties, scope), list(scope))
                for scope in (("task_id",), ("initiative_ref",), ("initiative_ref", "task_id"))
            ],
            governance_branch("evaluate_promotion", {
                "initiative_ref": initiative_ref_schema,
                "fingerprint": {"type": "string", "minLength": 1},
                "threshold": {"type": "integer", "minimum": 1},
                "window_days": {"type": "integer", "minimum": 1},
            }, ["initiative_ref", "fingerprint"]),
            governance_branch("promotion_inspect", {
                "initiative_ref": initiative_ref_schema,
                "record_ref": {"type": "string", "pattern": "^record-[A-Za-z0-9_.:-]+$"},
            }, ["initiative_ref"]),
        ],
    }

    return {
        "start_orchestration": START_ORCHESTRATION_SCHEMA,
        "continue_orchestration": CONTINUE_ORCHESTRATION_SCHEMA,
        "manage_orchestration": MANAGE_ORCHESTRATION_SCHEMA,
        "manage_governance": MANAGE_GOVERNANCE_SCHEMA,
        "worker_question": WORKER_QUESTION_SCHEMA,
        "record_attempt_event": WORKER_RECORD_ATTEMPT_EVENT_SCHEMA,
        "complete_attempt": WORKER_COMPLETE_ATTEMPT_SCHEMA,
        "read_dispatch_briefing": READ_DISPATCH_BRIEFING_SCHEMA,
        "read_worker_result": READ_WORKER_RESULT_SCHEMA,
    }



def _response_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    import hashlib
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _minimal_field_schema(raw_schema: Mapping[str, Any], *, depth: int = 2) -> dict[str, Any]:
    """Project only bounded, closed, model-actionable JSON Schema facets."""
    safe_schema: dict[str, Any] = {}
    raw_type = raw_schema.get("type")
    allowed_types = {"object", "array", "string", "integer", "boolean"}
    if isinstance(raw_type, str) and raw_type in allowed_types:
        safe_schema["type"] = raw_type
    elif isinstance(raw_type, list):
        safe_schema["type"] = next((value for value in raw_type if isinstance(value, str) and value in allowed_types), "object")
    for key in ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"):
        value = raw_schema.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            safe_schema[key] = value
    for key in ("minimum", "maximum"):
        value = raw_schema.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            safe_schema[key] = value
    pattern = raw_schema.get("pattern")
    if isinstance(pattern, str) and len(pattern) <= 1024:
        safe_schema["pattern"] = pattern
    format_name = raw_schema.get("format")
    if format_name == "project-relative-path":
        safe_schema["format"] = format_name
    enum = raw_schema.get("enum")
    if isinstance(enum, list):
        values = [
            value for value in enum[:64]
            if (
                isinstance(value, bool)
                or (isinstance(value, int) and not isinstance(value, bool))
                or (isinstance(value, str) and len(value) <= 256)
            )
        ]
        if values:
            safe_schema["enum"] = values
    const = raw_schema.get("const")
    if (
        isinstance(const, bool)
        or (isinstance(const, int) and not isinstance(const, bool))
        or (isinstance(const, str) and len(const) <= 256)
    ):
        safe_schema["const"] = const
    if isinstance(raw_schema.get("uniqueItems"), bool):
        safe_schema["uniqueItems"] = raw_schema["uniqueItems"]
    if isinstance(raw_schema.get("additionalProperties"), bool):
        safe_schema["additionalProperties"] = raw_schema["additionalProperties"]
    required = raw_schema.get("required")
    if isinstance(required, list):
        names = [str(name)[:256] for name in required[:64] if isinstance(name, str) and name]
        if names:
            safe_schema["required"] = names
    if depth > 0:
        properties = raw_schema.get("properties")
        if isinstance(properties, Mapping):
            projected = {
                str(name)[:256]: _minimal_field_schema(child, depth=depth - 1)
                for name, child in list(properties.items())[:64]
                if str(name) and isinstance(child, Mapping)
            }
            if projected:
                safe_schema["properties"] = projected
        raw_items = raw_schema.get("items")
        if isinstance(raw_items, Mapping):
            item_schema = _minimal_field_schema(raw_items, depth=depth - 1)
            if item_schema:
                safe_schema["items"] = item_schema
    if not safe_schema:
        safe_schema = {"type": "object"}
    return safe_schema


def _minimal_diagnostic(raw: object, *, default_code: str) -> dict[str, Any]:
    item = raw if isinstance(raw, Mapping) else {}
    pointer = str(item.get("json_pointer") or "").strip()
    if not pointer:
        path = str(item.get("path") or "").strip()
        if path:
            pointer = _submission_json_pointer(path)
    if pointer and not pointer.startswith("/"):
        pointer = "/" + pointer
    raw_schema = item.get("field_schema") if isinstance(item.get("field_schema"), Mapping) else {}
    diagnostic: dict[str, Any] = {
        "code": str(item.get("code") or default_code)[:160],
        "json_pointer": pointer[:2048],
        "message": str(item.get("message") or "The response could not be completed.")[:2000],
        "field_schema": _minimal_field_schema(raw_schema),
    }
    # These fields carry Cortex-issued references or opaque capabilities.  A
    # format card describes their wire shape, not a value a model is allowed
    # to invent.  Mark them without echoing the submitted or canonical value.
    field = pointer.rsplit("/", 1)[-1] if pointer else ""
    explicit_source = str(item.get("value_source") or "").strip()
    if explicit_source in {"model", "cortex"}:
        diagnostic["value_source"] = explicit_source
    elif field in {
        "task_ref", "assignment_ref", "coordinator_ref", "question_ref",
        "batch_ref", "cursor", "next_cursor", "dispatch_ref",
        "attempt_result_ref", "repair_capsule", "base_payload_digest",
        "request_id", "source_task_ref", "result_refs", "step",
    }:
        diagnostic["value_source"] = "cortex"
    for name in ("required_with", "forbidden_with"):
        values = item.get(name)
        if isinstance(values, list):
            pointers = [str(value)[:2048] for value in values[:32] if isinstance(value, str) and value.startswith("/")]
            if pointers:
                diagnostic[name] = pointers
    branch = str(item.get("branch") or "").strip()
    if branch:
        diagnostic["branch"] = branch[:160]
    return diagnostic


def _same_operation_change(diagnostic: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return one legal retry edit, or ``None`` when a retry would guess.

    Cortex-issued values are never reconstructed from a regex.  They are
    repairable here only when the legal change is to remove a forbidden
    field; adding or replacing one requires an exact value already delivered
    by a separate authoritative response and therefore cannot be authorized
    by this error card alone.
    """
    pointer = str(diagnostic.get("json_pointer") or "").strip()
    if not pointer.startswith("/"):
        return None
    message = str(diagnostic.get("message") or "").strip().lower()
    code = str(diagnostic.get("code") or "").strip().lower()
    removal = any(marker in message for marker in (
        "unsupported field", "unsupported worker_question field",
        "unsupported read_dispatch_briefing field",
        "is not allowed", "must omit", "accepts only",
        "remove this field", "forbidden",
    )) or (
        message.startswith("unsupported ") and " field" in message
    ) or code in {"validation_unknown", "unknown_field"}
    if removal:
        operations = ["remove"]
    elif diagnostic.get("value_source") == "cortex":
        return None
    elif any(marker in message for marker in ("is required", "are required", "requires ", "require ", "missing")):
        operations = ["add"]
    else:
        operations = ["replace"]
    return {"json_pointer": pointer, "allowed_ops": operations}


def _same_operation_changes(
    diagnostics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]] | None:
    """Return a complete de-duplicated legal edit set for one retry."""
    changes: list[dict[str, Any]] = []
    by_pointer: dict[str, tuple[str, ...]] = {}
    for diagnostic in diagnostics:
        change = _same_operation_change(diagnostic)
        if change is None:
            return None
        pointer = str(change["json_pointer"])
        operations = tuple(str(item) for item in change["allowed_ops"])
        prior = by_pointer.get(pointer)
        if prior is not None:
            if prior != operations:
                return None
            continue
        by_pointer[pointer] = operations
        changes.append(change)
    return changes or None


def _minimal_repair_diagnostic(raw: object) -> dict[str, Any]:
    """Project one diagnostic with the exact semantic patch pointer."""
    item = raw if isinstance(raw, Mapping) else {}
    diagnostic = _minimal_diagnostic(item, default_code="complete_attempt_validation_failed")
    if diagnostic["code"] == "validation_unknown":
        # Removal is the only valid operation for an unknown field. The full
        # parent-object schema is redundant and can be very large.
        diagnostic["field_schema"] = {"type": "object", "additionalProperties": False}
    pointer = str(item.get("repair_pointer") or "").strip()
    if pointer and not pointer.startswith("/"):
        pointer = "/" + pointer
    diagnostic["repair_pointer"] = pointer[:2048]
    raw_operations = item.get("allowed_ops")
    allowed = (
        [str(value) for value in raw_operations if str(value) in {"add", "replace", "remove"}]
        if isinstance(raw_operations, list) else []
    )
    if not allowed:
        # Unknown fields are removable only.  Every ordinary rejected value
        # may be supplied with RFC6902 add or replace; the escrow validator
        # remains the final authority for the exact operation/value pair.
        if diagnostic["code"] == "validation_unknown":
            allowed = ["remove"]
        elif diagnostic["code"] == "validation_required":
            allowed = ["add"]
        else:
            allowed = ["add", "replace"]
    diagnostic["allowed_ops"] = list(dict.fromkeys(allowed))
    return diagnostic


def _minimal_failure_card(
    source: Mapping[str, Any],
    *,
    default_code: str,
    retryable: bool,
    operation: str = "unknown_operation",
) -> dict[str, Any]:
    raw = source.get("diagnostics")
    diagnostics = (
        [_minimal_diagnostic(item, default_code=default_code) for item in raw[:64]]
        if isinstance(raw, list) and raw
        else [_minimal_diagnostic({"code": source.get("code") or default_code, "message": source.get("message") or source.get("next_action") or "The operation could not be completed."}, default_code=default_code)]
    )
    raw_retry = source.get("retry") if isinstance(source.get("retry"), Mapping) else {}
    requested_kind = str(raw_retry.get("kind") or "").strip()
    allowed_changes: list[dict[str, Any]] | None = None
    if requested_kind == "repair_patch_only":
        retry_kind = "repair_patch_only"
    elif not retryable:
        retry_kind = "terminal_stop"
    elif requested_kind == "inspect_server_state" or str(source.get("code") or "").endswith("_stale"):
        retry_kind = "inspect_server_state"
    else:
        allowed_changes = _same_operation_changes(diagnostics)
        retry_kind = "same_operation" if allowed_changes is not None else "terminal_stop"
    code = str(source.get("code") or default_code)[:160]
    category = (
        "integrity" if "integrity" in code else
        "authority" if any(token in code for token in ("identity", "capability", "authorization")) else
        "stale" if "stale" in code or retry_kind == "inspect_server_state" else
        "unavailable" if retry_kind == "terminal_stop" else
        "validation" if "validation" in code or retry_kind == "same_operation" else "internal"
    )
    message = str(source.get("message") or source.get("next_action") or "Cortex rejected this operation.").strip()[:512]
    effective_retryable = bool(retryable) and retry_kind != "terminal_stop"
    recovery: dict[str, Any] = {
        "kind": retry_kind,
        "operation": str(raw_retry.get("operation") or operation)[:64] or "unknown_operation",
        "retryable": effective_retryable,
        "state_mutated": False,
    }
    if retry_kind == "same_operation" and allowed_changes is not None:
        recovery["allowed_changes"] = allowed_changes
    return {
        "error": {"code": code, "category": category, "message": message or "Cortex rejected this operation.", "diagnostics": diagnostics},
        "recovery": recovery,
    }


def _lifecycle_step(source: Mapping[str, Any]) -> int:
    raw = source.get("step")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1:
        return raw
    match = re.search(r"(\d+)$", str(source.get("wave_id") or ""))
    return max(1, int(match.group(1))) if match else 1


def _real_question(source: Mapping[str, Any]) -> dict[str, Any] | None:
    result = source.get("result") if isinstance(source.get("result"), Mapping) else {}
    candidate = result.get("question")
    if not isinstance(candidate, Mapping):
        interaction = result.get("chat_interaction")
        candidate = interaction if isinstance(interaction, Mapping) else None
    if not isinstance(candidate, Mapping):
        return None
    question_ref = str(candidate.get("question_ref") or candidate.get("question_id") or candidate.get("interaction_ref") or "").strip()
    prompt = str(candidate.get("prompt") or candidate.get("question") or "").strip()
    if not re.fullmatch(r"question-[A-Za-z0-9._:-]{1,160}", question_ref) or not prompt:
        return None
    projected: dict[str, Any] = {"question_ref": question_ref, "prompt": prompt[:8000]}
    raw_options = candidate.get("options")
    if isinstance(raw_options, list):
        options: list[dict[str, Any]] = []
        for number, item in enumerate(raw_options[:16], 1):
            if isinstance(item, Mapping):
                label = str(item.get("label") or item.get("text") or item.get("id") or "").strip()[:1000]
                description = str(item.get("description") or "").strip()[:2000]
            else:
                label, description = str(item).strip()[:1000], ""
            if label:
                option: dict[str, Any] = {"number": number, "label": label}
                if description:
                    option["description"] = description
                options.append(option)
        if options:
            projected["options"] = options
    return projected


def _plan_decision(source: Mapping[str, Any]) -> dict[str, Any] | None:
    result = source.get("result") if isinstance(source.get("result"), Mapping) else {}
    review = result.get("plan_review") if isinstance(result.get("plan_review"), Mapping) else source.get("plan_review")
    review = review if isinstance(review, Mapping) else {}
    interaction = source.get("chat_interaction") if isinstance(source.get("chat_interaction"), Mapping) else {}
    request_id = str(
        review.get("request_id") or review.get("approval_request_id") or result.get("request_id")
        or interaction.get("interaction_ref") or ""
    ).strip()
    if request_id.startswith("plan-approval-"):
        request_id = "approval-" + request_id.removeprefix("plan-approval-")
    result_ref = str(review.get("result_ref") or review.get("plan_result_ref") or result.get("attempt_result_ref") or "").strip()
    if not re.fullmatch(r"approval-[A-Za-z0-9._:-]{1,160}", request_id):
        return None
    if not re.fullmatch(r"attempt-result-[A-Za-z0-9._:-]{1,160}", result_ref):
        return None
    digest_value = review.get("plan_digest") or review.get("digest") or review
    return {
        "request_id": request_id,
        "plan_result_ref": result_ref,
        "plan_digest": (
            str(digest_value)
            if isinstance(digest_value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest_value)
            else ("sha256:" + str(digest_value) if isinstance(digest_value, str) and re.fullmatch(r"[0-9a-f]{64}", digest_value) else _response_digest(digest_value))
        ),
        "choices": ["approve", "revise", "cancel"],
    }


def _completed_handoff(source: Mapping[str, Any]) -> dict[str, Any] | None:
    summary = source.get("state_summary") if isinstance(source.get("state_summary"), Mapping) else {}
    result = source.get("result") if isinstance(source.get("result"), Mapping) else {}
    handoff = result.get("context_handoff") if isinstance(result.get("context_handoff"), Mapping) else result.get("handoff")
    if not isinstance(handoff, Mapping):
        handoff = summary
    engine_completed = bool(source.get("ok")) and str(source.get("state") or "").strip() == "completed"
    if not engine_completed and not bool(summary.get("close_verified") or handoff.get("close_verified")):
        return None
    basis = handoff if handoff else {
        "state": "completed",
        "result": result,
    }
    digest = _response_digest(basis)
    return {
        "ref": "handoff-" + digest.removeprefix("sha256:")[:24],
        "digest": digest,
        "close_verified": True,
    }


def v11_response(
    old: dict[str, Any],
    task_ref: str,
    *,
    native_arguments: Callable[[dict[str, Any]], dict[str, Any]],
    public_schema: str,
    coordinator_lock: str,
    include_result: bool = False,
    start_replayed: bool | None = None,
) -> dict[str, Any]:
    """Project one engine receipt into the closed minimal lifecycle registry."""
    del public_schema, coordinator_lock, include_result
    source = old if isinstance(old, Mapping) else {}
    base = {
        "schema": "cortex/lifecycle-response/v11",
        "task_ref": str(task_ref or ""),
    }
    if start_replayed is True:
        response = {
            "schema": "cortex/lifecycle-response/v11",
            "ok": False,
            "outcome": "failed",
            "action": {"kind": "none"},
            **_minimal_failure_card(
                {"code": "coordinator_capability_lost", "message": "The successful start was already consumed and its coordinator capability cannot be reissued.", "diagnostics": []},
                default_code="coordinator_capability_lost",
                retryable=False,
                operation="start_orchestration",
            ),
        }
        return validate_response("coordinator.start", response)

    if not source.get("ok"):
        failure_card = _minimal_failure_card(
            source,
            default_code=str(source.get("code") or "orchestration_failed"),
            retryable=bool(source.get("retryable", source.get("recoverable", True))),
        )
        recovery_kind = str(failure_card["recovery"]["kind"])
        response = {
            "schema": "cortex/lifecycle-response/v11",
            "ok": False,
            "outcome": "needs_input" if recovery_kind == "same_operation" else "failed",
            "action": {"kind": (
                "retry_same_operation" if recovery_kind == "same_operation" else
                "inspect_or_retry" if recovery_kind == "inspect_server_state" else
                "none"
            )},
            **failure_card,
        }
        return validate_response("coordinator.lifecycle", response)

    requests = source.get("spawn_requests") if isinstance(source.get("spawn_requests"), list) else []
    dispatches = [
        {
            "call": "spawn_agent",
            "dispatch_ref": str(request.get("dispatch_ref") or ""),
            "arguments": native_arguments(request),
            "bootstrap_repair_message": str(request.get("bootstrap_repair_message") or ""),
        }
        for request in requests
        if isinstance(request, dict)
    ]
    step = _lifecycle_step(source)
    if dispatches:
        response = {
            **base,
            "ok": True,
            "outcome": "ready_to_spawn",
            "action": {"kind": "invoke_dispatches"},
            "step": step,
            "dispatches": dispatches,
        }
        return validate_response("coordinator.lifecycle", response)

    state = str(source.get("state") or "").strip()
    if state in {"waiting_workers", "waiting", "completion_pending"}:
        response = {
            **base,
            "ok": True,
            "outcome": "waiting",
            "action": {"kind": "wait_for_bound_workers"},
            "step": step,
        }
        return validate_response("coordinator.lifecycle", response)

    if state == "awaiting_plan_approval":
        decision = _plan_decision(source)
        if decision is not None:
            response = {
                **base,
                "ok": True,
                "outcome": "plan_approval",
                "action": {"kind": "obtain_plan_approval"},
                "decision": decision,
            }
            return validate_response("coordinator.lifecycle", response)

    if state == "needs_input":
        question = _real_question(source)
        if question is not None:
            response = {
                **base,
                "ok": True,
                "outcome": "needs_input",
                "action": {"kind": "obtain_user_decision"},
                "question": question,
            }
            return validate_response("coordinator.lifecycle", response)

    if state == "completed":
        handoff = _completed_handoff(source)
        if handoff is not None:
            response = {
                **base,
                "ok": True,
                "outcome": "completed",
                "action": {"kind": "deliver_handoff"},
                "handoff": handoff,
            }
            return validate_response("coordinator.lifecycle", response)

    if state == "bootstrap_terminal_failure":
        response = {
            "schema": "cortex/lifecycle-response/v11",
            "ok": False,
            "outcome": "failed",
            "action": {"kind": "none"},
            **_minimal_failure_card(
                source,
                default_code="bootstrap_terminal_failure",
                retryable=False,
                operation="manage_orchestration",
            ),
        }
        return validate_response("coordinator.lifecycle", response)

    failure_card = _minimal_failure_card(
        source,
        default_code=str(source.get("code") or "lifecycle_state_unavailable"),
        retryable=bool(source.get("recoverable", True)),
        operation="manage_orchestration",
    )
    response = {
        **base,
        "ok": False,
        "outcome": "failed",
        "action": {"kind": "none" if failure_card["recovery"]["kind"] == "terminal_stop" else "inspect_or_retry"},
        **failure_card,
    }
    return validate_response("coordinator.lifecycle", response)


_PUBLIC_RESPONSE_FAMILIES = {
    "start_orchestration": "coordinator.start",
    "continue_orchestration": "coordinator.lifecycle",
    "manage_orchestration": "coordinator.lifecycle",
    "manage_governance": "coordinator.governance",
    "read_worker_result": "result.read",
    "read_dispatch_briefing": "worker.briefing",
    "record_attempt_event": "worker.event",
    "worker_question": "worker.question",
    "complete_attempt": "worker.completion",
}


def _public_response_family(tool: str, arguments: Mapping[str, Any]) -> str:
    if tool == "manage_orchestration" and str(arguments.get("intent") or "").strip() == "question":
        return "coordinator.question_management"
    if tool == "manage_orchestration" and str(arguments.get("intent") or "").strip() == "follow_up":
        # Follow-up starts a distinct task and must deliver that task's
        # one-shot coordinator capability exactly as start_orchestration does.
        return "coordinator.start"
    return _PUBLIC_RESPONSE_FAMILIES[tool]


def _public_task_ref(source: Mapping[str, Any], arguments: Mapping[str, Any]) -> str | None:
    """Select only a syntactically valid explicit task reference for projection."""
    for candidate in (source.get("task_ref"), arguments.get("task_ref")):
        value = str(candidate or "").strip()
        if re.fullmatch(TASK_REF_PATTERN, value):
            return value
    return None


def _public_internal_failure(tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return a family-valid fail-closed receipt without reflecting raw state."""
    family = _public_response_family(tool, arguments)
    failure_card = {
        "error": {
            "code": "public_response_projection_failed", "category": "unavailable",
            "message": "Cortex could not safely project this operation response.",
            "diagnostics": [{
                "code": "public_response_projection_failed", "json_pointer": "",
                "message": "Cortex could not safely project this operation response.",
                "field_schema": {"type": "object"},
            }],
        },
        "recovery": {"kind": "terminal_stop", "operation": tool, "retryable": False, "state_mutated": False},
    }
    if family in {"coordinator.lifecycle", "coordinator.start"}:
        response: dict[str, Any] = {
            "schema": "cortex/lifecycle-response/v11",
            "ok": False,
            "outcome": "failed",
            "action": {"kind": "none"},
            **failure_card,
        }
    elif family == "coordinator.governance":
        response = {
            "schema": "cortex/governance-response/v11",
            "ok": False,
            "outcome": "failed",
            **failure_card,
        }
    elif family == "result.read":
        response = {
            "schema": "cortex/worker-result-read/v11",
            "ok": False,
            "outcome": "failed",
            **failure_card,
        }
    elif family == "coordinator.question_management":
        response = {
            "schema": "cortex/question-management/v11",
            "ok": False,
            "outcome": "needs_correction",
            **failure_card,
        }
    elif family == "worker.briefing":
        response = {
            "schema": "cortex/briefing-read/v11",
            "ok": False,
            "outcome": "failed",
            **failure_card,
        }
    else:
        schema = {
            "worker.event": "cortex/worker-event/v11",
            "worker.question": "cortex/worker-question/v11",
            "worker.completion": "cortex/worker-completion/v11",
        }[family]
        response = {"schema": schema, "ok": False, **failure_card}
    return validate_response(family, response)


def _public_argument_shape_failure(tool: str) -> dict[str, Any]:
    """Return a model-correctable tool error for non-object arguments.

    ``tools/call.params`` belongs to the JSON-RPC/MCP request envelope, but
    ``params.arguments`` is the selected tool's model-authored input.  Once a
    known public tool name has selected a public response family, a wrong
    arguments container is therefore a tool execution/input error rather than
    a raw JSON-RPC ``-32602``.  Keep the diagnostic at the wrapper leaf so the
    caller can replace only that value with the object advertised by
    ``tools/list``.
    """
    family = _public_response_family(tool, {})
    failure_card = {
        "error": {
            "code": "tool_arguments_invalid",
            "category": "validation",
            "message": "Tool arguments must be an object conforming to the advertised inputSchema.",
            "diagnostics": [{
                "code": "tool_arguments_invalid",
                "json_pointer": "/arguments",
                "message": "arguments must be an object conforming to the advertised inputSchema",
                "field_schema": {"type": "object"},
            }],
        },
        "recovery": {
            "kind": "same_operation",
            "operation": tool,
            "retryable": True,
            "state_mutated": False,
            "allowed_changes": [{
                "json_pointer": "/arguments",
                "allowed_ops": ["replace"],
            }],
        },
    }
    if family in {"coordinator.lifecycle", "coordinator.start"}:
        response: dict[str, Any] = {
            "schema": "cortex/lifecycle-response/v11",
            "ok": False,
            "outcome": "needs_input",
            "action": {"kind": "retry_same_operation"},
            **failure_card,
        }
    elif family == "coordinator.governance":
        response = {
            "schema": "cortex/governance-response/v11",
            "ok": False,
            "outcome": "failed",
            **failure_card,
        }
    elif family == "result.read":
        response = {
            "schema": "cortex/worker-result-read/v11",
            "ok": False,
            "outcome": "failed",
            **failure_card,
        }
    elif family == "worker.briefing":
        response = {
            "schema": "cortex/briefing-read/v11",
            "ok": False,
            "outcome": "failed",
            **failure_card,
        }
    else:
        schema = {
            "worker.event": "cortex/worker-event/v11",
            "worker.question": "cortex/worker-question/v11",
            "worker.completion": "cortex/worker-completion/v11",
        }[family]
        response = {"schema": schema, "ok": False, **failure_card}
    return validate_response(family, response)


def _project_failure(
    family: str,
    schema: str,
    source: Mapping[str, Any],
    arguments: Mapping[str, Any],
    *,
    outcome: bool = False,
) -> dict[str, Any]:
    operation_by_family = {
        "coordinator.governance": "manage_governance",
        "coordinator.question_management": "manage_orchestration",
        "result.read": "read_worker_result",
        "worker.briefing": "read_dispatch_briefing",
        "worker.event": "record_attempt_event",
        "worker.question": "worker_question",
        "worker.completion": "complete_attempt",
    }
    response: dict[str, Any] = {
        "schema": schema,
        "ok": False,
        **_minimal_failure_card(
            source,
            default_code=str(source.get("code") or "operation_failed"),
            retryable=bool(source.get("retryable", False)),
            operation=operation_by_family.get(family, family),
        ),
    }
    if family == "coordinator.question_management":
        response["outcome"] = "needs_correction"
    elif outcome:
        response["outcome"] = "failed"
    return validate_response(family, response)


def _project_lifecycle_response(
    source: Mapping[str, Any],
    arguments: Mapping[str, Any],
    *,
    start: bool,
    operation: str,
) -> dict[str, Any]:
    family = "coordinator.start" if start else "coordinator.lifecycle"
    task_ref = _public_task_ref(source, arguments)
    if not source.get("ok"):
        failure_card = _minimal_failure_card(
            source,
            default_code=str(source.get("code") or "orchestration_failed"),
            retryable=bool(source.get("retryable", source.get("recoverable", False))),
            operation=operation,
        )
        recovery_kind = str(failure_card["recovery"]["kind"])
        response: dict[str, Any] = {
            "schema": "cortex/lifecycle-response/v11",
            "ok": False,
            "outcome": "needs_input" if recovery_kind == "same_operation" else "failed",
            "action": {"kind": (
                "retry_same_operation" if recovery_kind == "same_operation" else
                "inspect_or_retry" if recovery_kind == "inspect_server_state" else
                "none"
            )},
            **failure_card,
        }
        return validate_response(family, response)
    if task_ref is None:
        raise ResponseValidationError([_minimal_diagnostic(
            {"code": "response_schema_invalid", "message": "successful lifecycle response omitted task_ref"},
            default_code="response_schema_invalid",
        )])
    coordinator_ref = str(source.get("coordinator_ref") or "").strip()
    base: dict[str, Any] = {
        "schema": "cortex/lifecycle-response/v11",
        "ok": True,
        "task_ref": task_ref,
    }
    if start:
        base["coordinator_ref"] = coordinator_ref
    dispatches = source.get("dispatches")
    if isinstance(dispatches, list) and dispatches:
        response = {
            **base,
            "outcome": "ready_to_spawn",
            "action": {"kind": "invoke_dispatches"},
            "step": _lifecycle_step(source),
            "dispatches": dispatches,
        }
        return validate_response(family, response)
    outcome = str(source.get("outcome") or "").strip()
    if outcome == "waiting":
        response = {
            **base,
            "outcome": "waiting",
            "action": {"kind": "wait_for_bound_workers"},
            "step": _lifecycle_step(source),
        }
        return validate_response(family, response)
    decision = source.get("decision") if isinstance(source.get("decision"), Mapping) else _plan_decision(source)
    if outcome in {"plan_approval", "awaiting_plan_approval"} and isinstance(decision, Mapping):
        return validate_response(family, {
            **base,
            "outcome": "plan_approval",
            "action": {"kind": "obtain_plan_approval"},
            "decision": dict(decision),
        })
    question = source.get("question")
    if outcome == "needs_input" and source.get("requires_user_decision") is True and isinstance(question, Mapping):
        return validate_response(family, {
            **base,
            "outcome": "needs_input",
            "action": {"kind": "obtain_user_decision"},
            "question": dict(question),
        })
    handoff = source.get("handoff")
    if outcome == "completed" and isinstance(handoff, Mapping):
        return validate_response(family, {
            **base,
            "outcome": "completed",
            "action": {"kind": "deliver_handoff"},
            "handoff": dict(handoff),
        })
    raise ResponseValidationError([_minimal_diagnostic(
        {"code": "response_schema_invalid", "message": "lifecycle outcome has no public v11 projection"},
        default_code="response_schema_invalid",
    )])


def _semantic_item(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        summary = str(
            value.get("summary") or value.get("message") or value.get("finding")
            or value.get("claim") or value.get("text") or ""
        ).strip()
        if not summary:
            summary = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        projected = {"summary": summary[:2000] or "Unspecified semantic item."}
        severity = str(value.get("severity") or "").strip().lower()
        if severity in {"low", "medium", "high", "critical"}:
            projected["severity"] = severity
        return projected
    summary = str(value or "").strip()
    return {"summary": summary[:2000] or "Unspecified semantic item."}


def _semantic_result(source: Mapping[str, Any]) -> dict[str, Any]:
    view = source.get("result_view") if isinstance(source.get("result_view"), Mapping) else {}
    result = view.get("result") if isinstance(view.get("result"), Mapping) else source.get("result")
    if not isinstance(result, Mapping):
        raise ResponseValidationError([_minimal_diagnostic(
            {"code": "response_schema_invalid", "message": "canonical AttemptResult is unavailable"},
            default_code="response_schema_invalid",
        )])
    status = str(result.get("result_status") or result.get("status") or "").strip().lower()
    projected: dict[str, Any] = {
        "status": status,
        "summary": str(result.get("summary") or "").strip()[:8000],
    }
    for key in ("findings", "decisions_needed", "unresolved", "claims"):
        values = result.get(key)
        projected[key] = [_semantic_item(item) for item in values[:32]] if isinstance(values, list) else []
    return projected


def _continuation(source: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = source.get("terminal_continuation")
    kind = "terminal_continue"
    if not isinstance(raw, Mapping):
        raw = source.get("continuation")
        kind = "continue"
    if not isinstance(raw, Mapping):
        return None
    results = raw.get("results")
    projected_results: list[dict[str, Any]] = []
    if isinstance(results, list):
        for item in results[:32]:
            if not isinstance(item, Mapping):
                continue
            result_ref = str(item.get("attempt_result_ref") or "").strip()
            if not result_ref:
                continue
            projected: dict[str, Any] = {"attempt_result_ref": result_ref}
            worker = item.get("worker")
            if type(worker) is int and worker >= 1:
                projected["worker"] = worker
            projected_results.append(projected)
    step = raw.get("step")
    if isinstance(step, bool) or not isinstance(step, int) or step < 1 or not projected_results:
        return None
    return {"kind": kind, "step": step, "results": projected_results}


def _project_result_read(source: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not source.get("ok"):
        return _project_failure(
            "result.read", "cortex/worker-result-read/v11", source, arguments, outcome=True,
        )
    audience = "worker" if str(arguments.get("assignment_ref") or "").strip() else "coordinator"
    if audience == "coordinator":
        views = source.get("result_views")
        if not isinstance(views, list) or not views:
            raise ResponseValidationError([_minimal_diagnostic(
                {"code": "response_schema_invalid", "message": "current wave canonical results are unavailable"},
                default_code="response_schema_invalid",
            )])
        response: dict[str, Any] = {
            "schema": "cortex/worker-result-read/v11",
            "ok": True,
            "results": [_semantic_result({"result_view": item}) for item in views if isinstance(item, Mapping)],
        }
        if len(response["results"]) != len(views):
            raise ResponseValidationError([_minimal_diagnostic(
                {"code": "response_schema_invalid", "message": "current wave result projection is incomplete"},
                default_code="response_schema_invalid",
            )])
        continuation = _continuation(source)
        if continuation is not None:
            response["continuation"] = continuation
        else:
            response["continuation_state"] = "unavailable"
    else:
        response = {
            "schema": "cortex/worker-result-read/v11",
            "ok": True,
            "result": _semantic_result(source),
        }
    return validate_response("result.read", response)


def _governance_kind(label: str) -> str:
    return {
        "initiative": "initiative", "record": "record", "records": "record",
        "link": "link", "dependency": "dependency", "exception": "exception",
        "proposal": "promotion", "proposals": "promotion", "promotion": "promotion",
    }.get(label, "record")


def _governance_receipt(label: str, value: object) -> dict[str, Any]:
    kind = _governance_kind(label)
    node = value if isinstance(value, Mapping) else {"value": value}
    digest = _response_digest(node)
    candidates = (
        f"{kind}_ref", "initiative_ref", "record_ref", "link_ref", "dependency_ref",
        "exception_ref", "proposal_ref", "resource_ref",
    )
    resource_ref = next((str(node.get(key) or "").strip() for key in candidates if str(node.get(key) or "").strip()), "")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{1,180}", resource_ref) is None:
        resource_ref = f"{kind}-receipt-{digest.removeprefix('sha256:')[:24]}"
    receipt: dict[str, Any] = {
        "resource_kind": kind,
        "resource_ref": resource_ref,
        "digest": digest,
    }
    revision = node.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
        receipt["revision"] = revision
    return receipt


def _governance_items(result: object) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(result, Mapping):
        for label, value in result.items():
            if isinstance(value, list):
                items.extend(_governance_receipt(str(label), item) for item in value[:64 - len(items)])
            else:
                items.append(_governance_receipt(str(label), value))
            if len(items) >= 64:
                break
    elif isinstance(result, list):
        items.extend(_governance_receipt("record", item) for item in result[:64])
    return items


def _project_governance(source: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not source.get("ok"):
        return _project_failure(
            "coordinator.governance", "cortex/governance-response/v11", source, arguments, outcome=True,
        )
    operation = str(source.get("action") or arguments.get("action") or "").strip()[:64]
    result = source.get("result")
    inspection_actions = {
        "inspect_initiative", "list_records", "snapshot", "promotion_inspect",
    }
    if operation in inspection_actions:
        digest = _response_digest(result)
        inspection: dict[str, Any] = {
            "ref": "governance-" + digest.removeprefix("sha256:")[:24],
            "digest": digest,
        }
        items = _governance_items(result)
        if items:
            inspection["items"] = items
        response = {
            "schema": "cortex/governance-response/v11",
            "ok": True,
            "outcome": "inspected",
            "inspection": inspection,
        }
    else:
        items = _governance_items(result)
        if len(items) != 1:
            raise ResponseValidationError([_minimal_diagnostic(
                {"code": "response_schema_invalid", "message": "governance mutation produced no unique resource receipt"},
                default_code="response_schema_invalid",
            )])
        response = {
            "schema": "cortex/governance-response/v11",
            "ok": True,
            "outcome": "updated",
            "receipt": items[0],
        }
    return validate_response("coordinator.governance", response)


def _project_briefing(source: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not source.get("ok"):
        response = _project_failure(
            "worker.briefing", "cortex/briefing-read/v11", source, arguments, outcome=True,
        )
        recovery = source.get("recovery")
        if isinstance(recovery, Mapping):
            path = str(recovery.get("path") or "")
            if (
                recovery.get("kind") == "read_exact_host_path_once"
                and recovery.get("max_reads") == 1
                and path.startswith("/")
            ):
                response = {
                    **response,
                    "recovery": {
                        "kind": "read_exact_host_path_once",
                        "path": path,
                        "max_reads": 1,
                    },
                }
                return validate_response("worker.briefing", response)
        return response
    response: dict[str, Any] = {
        "schema": "cortex/briefing-read/v11",
        "ok": True,
        "outcome": "briefing_read",
        "content": str(source.get("content") or ""),
        "encoding": str(source.get("encoding") or "utf-8"),
        "complete": bool(source.get("complete")),
    }
    if not response["complete"]:
        next_cursor = source.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor.strip():
            return _project_failure(
                "worker.briefing",
                "cortex/briefing-read/v11",
                {
                    "code": "dispatch_briefing_response_invalid",
                    "retryable": False,
                    "diagnostics": [{
                        "code": "dispatch_briefing_response_invalid",
                        "json_pointer": "/next_cursor",
                        "message": "incomplete briefing response requires a non-empty next_cursor",
                        "field_schema": {"type": "string", "minLength": 1, "maxLength": 1024},
                    }],
                },
                arguments,
                outcome=True,
            )
        response["next_cursor"] = next_cursor
    return validate_response("worker.briefing", response)


def _project_worker_event(source: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not source.get("ok"):
        return _project_failure("worker.event", "cortex/worker-event/v11", source, arguments)
    return validate_response("worker.event", {
        "schema": "cortex/worker-event/v11",
        "ok": True,
    })


def _project_worker_completion(source: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    repair = source.get("repair")
    if not source.get("ok") and isinstance(repair, Mapping):
        response: dict[str, Any] = {
            "schema": "cortex/worker-completion/v11",
            "ok": False,
            "error": {
                    "code": "complete_attempt_validation_failed", "category": "validation",
                    "message": "Cortex rejected the submitted attempt outcome.",
                    "diagnostics": [
                        _minimal_diagnostic(item, default_code="complete_attempt_validation_failed")
                        for item in list(repair.get("diagnostics") or [])[:64]
                    ],
                },
            "recovery": {
                    "kind": "repair_patch_only", "operation": "complete_attempt",
                    "retryable": True, "state_mutated": False,
                    "repair": {
                        "repair_capsule": str(repair.get("repair_capsule") or ""),
                        "base_payload_digest": str(repair.get("base_payload_digest") or ""),
                        "patch_paths": list(repair.get("patch_paths") or [])[:64],
                        "diagnostics": [
                            _minimal_repair_diagnostic(item)
                            for item in list(repair.get("diagnostics") or [])[:64]
                        ],
                    },
            },
        }
        return validate_response("worker.completion", response)
    if not source.get("ok"):
        return _project_failure("worker.completion", "cortex/worker-completion/v11", source, arguments)
    return validate_response("worker.completion", {
        "schema": "cortex/worker-completion/v11",
        "ok": True,
        "terminal": True,
    })


def _canonical_answer(value: object) -> object:
    if isinstance(value, str):
        return value[:8000]
    if not isinstance(value, Mapping):
        return str(value or "")[:8000]
    option_ids = value.get("option_ids")
    if not isinstance(option_ids, list):
        option_ids = value.get("answer_option_ids")
    if not isinstance(option_ids, list):
        option_ids = []
    text = str(
        value.get("text") or value.get("answer_en") or value.get("answer_en_text")
        or value.get("custom_response") or ""
    ).strip()
    if not text:
        selections = value.get("selections")
        if isinstance(selections, list):
            text = "; ".join(str(item) for item in selections if str(item).strip())
    return {
        "text": text[:8000],
        **({"option_ids": [str(item)[:256] for item in option_ids[:16]]} if option_ids else {}),
    }


def _batch_progress_value(value: object) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    answered = source.get("answered")
    total = source.get("total")
    progress: dict[str, Any] = {
        "answered": answered if isinstance(answered, int) and not isinstance(answered, bool) else 0,
        "total": total if isinstance(total, int) and not isinstance(total, bool) and total >= 1 else 0,
    }
    next_key = str(source.get("next_question_key") or "").strip()
    if next_key:
        progress["next_question_key"] = next_key[:160]
    return progress


def _project_worker_question(source: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not source.get("ok"):
        return _project_failure("worker.question", "cortex/worker-question/v11", source, arguments)
    outcome = str(source.get("outcome") or "").strip()
    response: dict[str, Any] = {
        "schema": "cortex/worker-question/v11",
        "ok": True,
        "outcome": outcome,
    }
    if outcome in {"question_recorded", "question_superseded", "question_answered"} or (
        outcome == "awaiting_user" and source.get("question_ref")
    ):
        response["question_ref"] = str(source.get("question_ref") or "").strip()
    if outcome in {"batch_recorded", "batch_superseded", "batch_answered"} or (
        outcome == "awaiting_user" and source.get("batch_ref")
    ):
        response["batch_ref"] = str(source.get("batch_ref") or "").strip()
    if outcome == "question_answered":
        response["answer"] = _canonical_answer(source.get("answer"))
    if source.get("progress") is not None and (outcome == "batch_answered" or (outcome == "awaiting_user" and source.get("batch_ref"))):
        response["progress"] = _batch_progress_value(source.get("progress"))
    if outcome == "batch_answered":
        answers = source.get("answers")
        response["answers"] = {
            str(key)[:160]: _canonical_answer(value)
            for key, value in list(answers.items())[:32]
        } if isinstance(answers, Mapping) else {}
    return validate_response("worker.question", response)


def _display_question(interaction: object) -> dict[str, Any] | None:
    """Keep only the single ordinary-chat question the coordinator must show."""
    source = interaction if isinstance(interaction, Mapping) else {}
    view = source.get("user_view") if isinstance(source.get("user_view"), Mapping) else source
    prompt = str(view.get("question") or view.get("message") or "").strip()[:8000]
    if not prompt:
        return None
    card: dict[str, Any] = {"prompt": prompt}
    if isinstance(view.get("options"), list):
        options: list[dict[str, Any]] = []
        for number, item in enumerate(view["options"][:16], 1):
            if isinstance(item, Mapping):
                label = str(item.get("label") or "").strip()[:1000]
                ordinal = item.get("number")
                description = str(item.get("description") or "").strip()[:2000]
            else:
                label, ordinal, description = str(item).strip()[:1000], number, ""
            if label:
                option = {"number": ordinal if isinstance(ordinal, int) and ordinal > 0 else number, "label": label}
                if description:
                    option["description"] = description
                options.append(option)
        if options:
            card["options"] = options
    return card


def _translation_text(value: object) -> str:
    return value[:8000] if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)[:8000]


def _project_coordinator_question_management(source: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Project durable question state without raw records, prose, or identity aliases."""
    if not source.get("ok"):
        response = _project_failure("coordinator.question_management", "cortex/question-management/v11", source, arguments)
        return response
    result = source.get("result") if isinstance(source.get("result"), Mapping) else {}
    outcome = str(source.get("outcome") or "").strip()
    base = {"schema": "cortex/question-management/v11", "ok": True}
    question_ref = str(result.get("question_id") or result.get("question_ref") or "").strip()
    batch_ref = str(result.get("batch_ref") or "").strip()
    if outcome == "awaiting_user":
        card = _display_question(source.get("chat_interaction") or result.get("chat_interaction"))
        if card is None:
            raise ResponseValidationError([_minimal_diagnostic({"code": "response_schema_invalid", "message": "awaiting user response has no display question"}, default_code="response_schema_invalid")])
        if batch_ref:
            progress = _batch_progress_value(result.get("progress"))
            if progress["total"] < 1:
                raise ResponseValidationError([_minimal_diagnostic({"code": "response_schema_invalid", "message": "batch question has no durable progress"}, default_code="response_schema_invalid")])
            return validate_response("coordinator.question_management", {**base, "outcome": outcome, "batch_ref": batch_ref, "progress": progress, "question": card})
        return validate_response("coordinator.question_management", {**base, "outcome": outcome, "question_ref": question_ref, "question": card})
    if outcome == "question_answered":
        resume_contract = source.get("resume_contract") if isinstance(source.get("resume_contract"), Mapping) else {}
        poll_action = str(resume_contract.get("poll_action") or "").strip()
        if poll_action == "poll":
            resume = {"kind": "poll", "question_ref": str(resume_contract.get("question_ref") or "").strip()}
        elif poll_action == "poll_batch":
            resume = {"kind": "poll_batch", "batch_ref": str(resume_contract.get("batch_ref") or "").strip()}
        else:
            raise ResponseValidationError([_minimal_diagnostic({"code": "response_schema_invalid", "message": "answered question has no resumable poll receipt"}, default_code="response_schema_invalid")])
        return validate_response("coordinator.question_management", {**base, "outcome": outcome, "resume": resume})
    if outcome == "question_answered_not_resumable":
        ref = {"batch_ref": batch_ref} if batch_ref else {"question_ref": question_ref}
        return validate_response("coordinator.question_management", {**base, "outcome": outcome, **ref})
    if outcome == "batch_superseded":
        return validate_response("coordinator.question_management", {**base, "outcome": outcome, "batch_ref": batch_ref})
    if outcome == "awaiting_translation":
        request = source.get("translation_request") if isinstance(source.get("translation_request"), Mapping) else {}
        payload = request.get("payload") if isinstance(request.get("payload"), Mapping) else {}
        ref = str(payload.get("question_ref") or batch_ref or question_ref).strip()
        if ref.startswith("batch-"):
            original = result.get("answer_original")
            values = original if isinstance(original, Mapping) else {}
            translation = {"batch_ref": ref, "source_text_by_question": {str(key)[:160]: _translation_text(value) for key, value in list(values.items())[:32]}}
        else:
            translation = {"question_ref": ref, "source_text": _translation_text(payload.get("answer", result.get("answer_original")))}
        return validate_response("coordinator.question_management", {**base, "outcome": outcome, "translation": translation})
    raise ResponseValidationError([_minimal_diagnostic({"code": "response_schema_invalid", "message": "question outcome has no public v11 projection"}, default_code="response_schema_invalid")])


def project_public_response(
    tool: str,
    value: object,
    *,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one handler value through the single closed public registry."""
    if tool not in _PUBLIC_RESPONSE_FAMILIES or not isinstance(value, Mapping):
        raise ResponseValidationError([_minimal_diagnostic(
            {"code": "response_schema_invalid", "message": "public handler returned an unsupported response"},
            default_code="response_schema_invalid",
        )])
    family = _public_response_family(tool, arguments)
    # Public facades and stdio share this boundary.  A direct facade may have
    # already returned the selected closed v11 union; validate/pass it through
    # rather than treating it as raw runtime state and rewriting its outcome.
    try:
        return validate_response(family, value)
    except ResponseValidationError:
        pass
    source = dict(value)
    if tool == "manage_orchestration" and _public_response_family(tool, arguments) == "coordinator.question_management":
        return _project_coordinator_question_management(source, arguments)
    if tool in {"start_orchestration", "continue_orchestration", "manage_orchestration"}:
        return _project_lifecycle_response(
            source, arguments, start=family == "coordinator.start", operation=tool,
        )
    if tool == "manage_governance":
        return _project_governance(source, arguments)
    if tool == "read_worker_result":
        return _project_result_read(source, arguments)
    if tool == "read_dispatch_briefing":
        return _project_briefing(source, arguments)
    if tool == "record_attempt_event":
        return _project_worker_event(source, arguments)
    if tool == "complete_attempt":
        return _project_worker_completion(source, arguments)
    return _project_worker_question(source, arguments)


def _safe_public_response(
    tool: str,
    value: object,
    *,
    arguments: Mapping[str, Any],
    supplied_coordinator_refs: frozenset[str],
) -> dict[str, Any]:
    """Project, scrub, and revalidate without exposing rejected handler state."""
    family = _public_response_family(tool, arguments)
    try:
        projected = project_public_response(tool, value, arguments=arguments)
    except (ResponseValidationError, ValueError, TypeError, KeyError):
        # A handler result can be semantically impossible for its advertised
        # public union (for example an answered question with no canonical
        # text).  This is a contained Cortex contract failure, not a JSON-RPC
        # transport failure.  Return the closed normal error union so a model
        # never receives a raw -32602 without recovery semantics.
        projected = _public_internal_failure(tool, arguments)
    scrubbed = _scrub_public_response(
        projected,
        allow_coordinator_ref=family == "coordinator.start",
        supplied_coordinator_refs=supplied_coordinator_refs,
    )
    try:
        return validate_response(family, scrubbed if isinstance(scrubbed, Mapping) else {})
    except ResponseValidationError:
        # The fallback is constructed from constants plus an already validated
        # task_ref only.  Never attach the rejected value or validator details.
        fallback = _public_internal_failure(tool, arguments)
        scrubbed_fallback = _scrub_public_response(
            fallback,
            allow_coordinator_ref=False,
            supplied_coordinator_refs=supplied_coordinator_refs,
        )
        return validate_response(
            family,
            scrubbed_fallback if isinstance(scrubbed_fallback, Mapping) else {},
        )


def configure_internal_schemas(tools: dict[str, tuple[Callable[..., Any], dict[str, Any]]]) -> set[str]:
    """Apply authorization requirements to internal handlers before projection."""
    tools["record_delegation"][1]["properties"]["dispatch_mode"].update({
        "enum": ["hidden_subagent"],
        "description": "Native hidden spawn_agent dispatch; no alternate worker transport is supported.",
    })
    tools["record_delegation"][1]["properties"]["luna_fallback"]["description"] = (
        "An unavailable hidden Luna dispatch falls back to an explicit hidden Terra spawn_agent request."
    )
    tools["record_delegation"][1]["properties"]["luna_fallback"]["default"] = "terra"
    authorized = {
        "init_task", "get_task_status", "record_delegation", "prepare_delegation", "prepare_delegations", "finalize_attempt", "record_evidence", "execute_verification_command",
        "cortex.question", "publish_worker_question", "list_worker_questions", "answer_worker_question", "get_worker_question_updates",
        "commit_gate", "update_pipeline", "reassess_pipeline", "acquire_lock", "release_lock",
        "create_handoff", "claim_resource", "release_resource",
        "create_lane", "get_lane_status", "claim_lane", "release_lane", "retire_lane", "bind_task_lane",
        "claim_lane_resource", "release_lane_resource", "materialize_lane", "reconcile_lane",
    }
    for name in authorized:
        schema = tools[name][1]
        schema.setdefault("properties", {}).setdefault("principal", {"type": "string", "minLength": 1})
        if "principal" not in schema.setdefault("required", []):
            schema["required"].append("principal")
    # These task-scoped coordinator/worker forms are server-bound by opaque
    # task/dispatch identity.  Do not inject the historical project_root
    # convenience field while decorating internal schemas: the public and
    # internal registries intentionally share schema objects.
    server_bound_without_root = {
        "continue_orchestration", "manage_orchestration", "read_worker_result",
    }
    for name, (_, schema) in tools.items():
        if name in server_bound_without_root:
            continue
        schema.setdefault("properties", {}).setdefault("project_root", {
            "type": "string",
            "minLength": 1,
            "description": "Absolute project workspace path. Cortex derives an opaque host-private control ledger from this path; callers cannot choose its storage location.",
        })
    if "project_root" not in tools["activate_orchestration"][1].setdefault("required", []):
        tools["activate_orchestration"][1]["required"].append("project_root")
    for name, fields in {
        "claim_resource": ["expires_at"], "claim_lane": ["expires_at"], "claim_lane_resource": ["expires_at"],
        "create_handoff": ["completed", "next_action"], "retire_lane": ["confirm"],
    }.items():
        for field in fields:
            if field not in tools[name][1]["required"]:
                tools[name][1]["required"].append(field)
    tools["retire_lane"][1]["properties"]["confirm"] = {"type": "boolean"}
    tools["record_delegation"][1]["required"] = [
        field for field in tools["record_delegation"][1]["required"]
        if field not in {"expected_revision", "status_receipt", "gate", "agent", "task_kind", "risk", "objective", "ownership", "allowed_paths", "acceptance_criteria", "verification"}
    ]
    for field in ("allowed_paths", "acceptance_criteria", "verification"):
        tools["record_delegation"][1]["properties"][field].pop("minItems", None)
    return authorized


def public_tools(
    internal_handlers: Mapping[str, tuple[Callable[..., Any], dict[str, Any]]],
    *,
    worker_question: Callable[..., Any],
    worker_question_schema: dict[str, Any],
    record_attempt_event: Callable[..., Any],
    record_attempt_event_schema: dict[str, Any],
    complete_attempt: Callable[..., Any],
    complete_attempt_schema: dict[str, Any],
    read_dispatch_briefing: Callable[..., Any],
    read_dispatch_briefing_schema: dict[str, Any],
    read_worker_result: Callable[..., Any],
    read_worker_result_schema: dict[str, Any],
    manage_governance: Callable[..., Any],
    manage_governance_schema: dict[str, Any],
) -> dict[str, tuple[Callable[..., Any], dict[str, Any]]]:
    """Return the fresh-only nine-operation public registry."""
    return {
        "start_orchestration": internal_handlers["start_orchestration"],
        "continue_orchestration": internal_handlers["continue_orchestration"],
        "manage_orchestration": internal_handlers["manage_orchestration"],
        "manage_governance": (manage_governance, manage_governance_schema),
        "worker_question": (worker_question, worker_question_schema),
        "record_attempt_event": (record_attempt_event, record_attempt_event_schema),
        "complete_attempt": (complete_attempt, complete_attempt_schema),
        "read_dispatch_briefing": (read_dispatch_briefing, read_dispatch_briefing_schema),
        "read_worker_result": (read_worker_result, read_worker_result_schema),
    }


def public_tools_for_audience(
    all_public_tools: Mapping[str, tuple[Callable[[dict[str, Any]], dict[str, Any]], dict[str, Any]]],
    audience: str,
) -> dict[str, tuple[Callable[[dict[str, Any]], dict[str, Any]], dict[str, Any]]]:
    """Project the public registry for one launch-time MCP audience.

    ``audience`` is intentionally not accepted from JSON-RPC initialization or
    individual tool arguments: those values are controlled by the caller and
    cannot establish a privilege boundary.  The host selects it before the
    process starts.  Unknown/missing audiences use the default fresh union;
    hosts that need role separation select ``worker`` or ``coordinator``.
    """
    selected = str(audience or "").strip().lower()
    if selected == "coordinator":
        names = COORDINATOR_PUBLIC_TOOL_NAMES
    elif selected == "worker":
        names = WORKER_PUBLIC_TOOL_NAMES
    else:
        names = DEFAULT_PUBLIC_TOOL_NAMES
    return {
        name: all_public_tools[name]
        for name in names
        if name in all_public_tools
    }


def serve_stdio(
    *,
    public_tools: Mapping[str, tuple[Callable[[dict[str, Any]], dict[str, Any]], dict[str, Any]]],
    internal_handlers: Mapping[str, tuple[Callable[..., Any], dict[str, Any]]],
    server_version: str,
    instructions: str,
    log_tool_error: Callable[[object, object, str, Exception], None],
    audience: str = DEFAULT_MCP_AUDIENCE,
) -> None:
    """Run the narrow JSON-RPC transport without importing orchestration internals.

    The selected tool mapping is fixed for the process lifetime.  This is the
    strongest boundary available to a plain stdio transport: it cannot trust a
    role supplied by the client after the process has started.
    """
    normalized_audience = str(audience or "").strip().lower()
    if normalized_audience not in MCP_AUDIENCES:
        normalized_audience = DEFAULT_MCP_AUDIENCE
    # ``serve_stdio`` is also imported by source-mode tests and embedding
    # hosts.  Enforce the projection here rather than relying exclusively on
    # the CLI entry point to pass an already-filtered mapping.
    public_tools = public_tools_for_audience(public_tools, normalized_audience)
    all_public_names = frozenset(PUBLIC_TOOL_DESCRIPTIONS)
    # JSON-RPC request ids are scoped to one MCP connection.  Include a fresh
    # connection nonce before handing the id to lifecycle code so a new Codex
    # thread that starts a fresh MCP process can never replay a prior thread's
    # numeric id.  A repeated id on this same transport remains a stable
    # request identity for server-side idempotency.
    transport_connection_nonce = secrets.token_hex(16)
    def tool_result(name: object, value: Mapping[str, Any]) -> dict[str, Any]:
        """Build one MCP tools/call result with model-visible tool errors."""
        failed = value.get("ok") is False
        if not failed:
            text_value = json.dumps(value, ensure_ascii=False, indent=2)
        else:
            error = value.get("error") if isinstance(value.get("error"), Mapping) else {}
            recovery = value.get("recovery") if isinstance(value.get("recovery"), Mapping) else {}
            lines = [
                f"operation: {str(name or 'unknown_operation')[:64]}",
                f"error: {str(error.get('code') or 'operation_failed')[:160]}: {str(error.get('message') or 'Cortex rejected this operation.')[:512]}",
            ]
            raw_diagnostics = error.get("diagnostics")
            if isinstance(raw_diagnostics, list):
                lines.append("diagnostics:")
                for raw in raw_diagnostics[:64]:
                    item = raw if isinstance(raw, Mapping) else {}
                    pointer = str(item.get("json_pointer") or "")[:2048]
                    message = str(item.get("message") or "invalid value")[:1000]
                    schema = item.get("field_schema") if isinstance(item.get("field_schema"), Mapping) else {"type": "object"}
                    lines.append(
                        f"- {pointer or '<request>'}: {message}; constraint="
                        + json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    )
            lines.append(
                "recovery: kind=" + str(recovery.get("kind") or "terminal_stop")[:32]
                + "; retryable=" + str(bool(recovery.get("retryable"))).lower()
                + "; state_mutated=" + str(bool(recovery.get("state_mutated"))).lower()
            )
            allowed = recovery.get("allowed_changes")
            if isinstance(allowed, list) and allowed:
                lines.append("allowed_changes:")
                for raw in allowed[:64]:
                    item = raw if isinstance(raw, Mapping) else {}
                    pointer = str(item.get("json_pointer") or "")[:2048]
                    operations = [
                        str(op) for op in item.get("allowed_ops") or []
                        if str(op) in {"add", "replace", "remove"}
                    ]
                    lines.append(f"- {pointer}: {','.join(operations)}")
            if (
                recovery.get("kind") == "same_operation"
                and recovery.get("retryable") is True
                and recovery.get("state_mutated") is False
                and isinstance(allowed, list) and bool(allowed)
            ):
                lines.append("instruction: apply only allowed_changes and retry the same operation; do not inspect Cortex implementation or private state.")
            elif isinstance(recovery.get("terminal_failure"), Mapping):
                lines.append(
                    "instruction: stop task-scoped worker calls and return exactly CORTEX_ATTEMPT_FAILED retryable=false; "
                    "the marker is status text only, and the coordinator must call the advertised finalize_worker_failure "
                    "action for the original dispatch so Cortex can verify and consume its private server-bound evidence."
                )
            else:
                lines.append("instruction: stop this operation; do not retry or inspect Cortex implementation or private state.")
            text_value = "\n".join(lines)
        return {
            "content": [{"type": "text", "text": text_value}],
            "structuredContent": dict(value),
            "isError": failed,
        }

    while True:
        line = sys.stdin.readline()
        if not line:
            return
        request_id: object = None
        request: object = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("JSON-RPC request must be an object")
            method, request_id = request.get("method"), request.get("id")
            if method == "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": request.get("params", {}).get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {}, "resources": {"subscribe": False, "listChanged": False}},
                    "serverInfo": {"name": "cortex", "version": server_version},
                    "instructions": instructions,
                }
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                result = {"tools": [
                    {"name": name, "description": PUBLIC_TOOL_DESCRIPTIONS[name], "inputSchema": schema}
                    for name, (_, schema) in public_tools.items()
                ]}
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "resources/templates/list":
                result = {"resourceTemplates": []}
            elif method == "tools/call":
                params = request.get("params", {})
                if not isinstance(params, dict):
                    # MCP CallToolRequest.params is an object.  Without that
                    # envelope the server cannot safely select a known tool or
                    # its public response family, so this remains a protocol
                    # invalid-params error rather than a tool execution error.
                    raise ValueError("tools/call params must be an object")
                name = params.get("name")
                coordinator_refs = _supplied_coordinator_refs(request)
                arguments = params.get("arguments", {})
                if name not in public_tools:
                    if name in all_public_names:
                        value = _scrub_public_response(
                            _public_internal_failure(
                                str(name), arguments if isinstance(arguments, dict) else {},
                            ),
                            supplied_coordinator_refs=coordinator_refs,
                        )
                        result = tool_result(name, value)
                        # This is a structured routing receipt, not an
                        # unhandled tool exception.  Do not log the request:
                        # the receipt contains all actionable advice and the
                        # transport must not retain caller payloads here.
                        if request_id is not None:
                            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False) + "\n")
                            sys.stdout.flush()
                        continue
                    if name in internal_handlers:
                        raise ValueError("tool_is_internal_and_not_model_callable")
                    raise ValueError(f"unknown tool '{name}'")
                if not isinstance(arguments, dict):
                    value = _scrub_public_response(
                        _public_argument_shape_failure(str(name)),
                        supplied_coordinator_refs=coordinator_refs,
                    )
                    result = tool_result(name, value)
                    if request_id is not None:
                        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False) + "\n")
                        sys.stdout.flush()
                    continue
                if name == "start_orchestration" and ("id" not in request or request_id is None):
                    raise ValueError("start_orchestration requires a non-null JSON-RPC id for a mutating transport request")
                if name == "start_orchestration" and request_id is not None:
                    arguments = {
                        **arguments,
                        "_transport_request_id": (
                            f"{transport_connection_nonce}:{_canonical_jsonrpc_request_id(request_id)}"
                        ),
                    }
                value = _safe_public_response(
                    name,
                    public_tools[name][0](arguments),
                    arguments=arguments,
                    supplied_coordinator_refs=coordinator_refs,
                )
                result = tool_result(name, value)
            elif method == "ping":
                result = {}
            else:
                raise ValueError(f"unsupported method '{method}'")
            if request_id is not None:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            is_ledger_busy = exc.__class__.__name__ == "LedgerBusyError"
            if not is_ledger_busy:
                log_tool_error(request, request_id, line.rstrip("\n"), exc)
            if request_id is not None:
                if is_ledger_busy:
                    holder = getattr(exc, "holder", None)
                    data = {
                        "schema": "cortex/ledger-busy/v1",
                        "code": "ledger_busy",
                        "retryable": True,
                        "retry_after_ms": 250,
                        "operation": str(getattr(exc, "operation", "mutation")),
                        "held_duration_ms": int(getattr(exc, "held_duration_ms", 0)),
                    }
                    if isinstance(holder, dict):
                        data["holder"] = holder
                    error = {
                        "code": -32009,
                        "message": "Cortex ledger is busy; retry the same operation without changing its input.",
                        "data": data,
                    }
                else:
                    error_message = _ASSIGNMENT_REF_VALUE_RE.sub("<redacted-assignment-ref>", str(exc))
                    for coordinator_ref in _supplied_coordinator_refs(request):
                        error_message = error_message.replace(coordinator_ref, "<redacted-coordinator-ref>")
                    error = {
                        "code": -32602,
                        "message": error_message,
                    }
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": error}, ensure_ascii=False) + "\n")
                sys.stdout.flush()
