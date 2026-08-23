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
from collections.abc import Callable, Mapping
from typing import Any

from cortex_runtime.communication import public_risks, render, render_lifecycle, render_plan
from cortex_runtime.worker_identity import worker_request


MCP_AUDIENCES = frozenset({"default", "coordinator", "worker"})
DEFAULT_MCP_AUDIENCE = "default"
CANONICAL_MODELS = ("gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra")


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


def _tool_unavailable_receipt(tool: str, audience: str) -> dict[str, Any]:
    """Return non-throwing advice when a fixed audience calls another surface.

    A worker can receive a stale or copied coordinator instruction.  This is a
    routing mismatch, not a lifecycle failure: it must not become a JSON-RPC
    exception that makes the worker appear blocked or eligible for replacement.
    Keep this receipt independent of request arguments so no private task
    identity or caller payload can cross the audience boundary.
    """
    normalized_tool = str(tool or "").strip() or "requested tool"
    normalized_audience = str(audience or "").strip().lower() or DEFAULT_MCP_AUDIENCE
    code = f"tool_not_available_for_{normalized_audience}_mcp_audience"
    coordinator_only = normalized_tool in {"manage_orchestration", "manage_governance"}
    if coordinator_only:
        next_action = (
            "Return this coordinator-only request to the host coordinator through the existing task handoff. "
            "Do not retry it from the worker channel, ask the user, replace the worker, or block the task; continue only with the worker operations in this channel."
        )
    else:
        next_action = "Use only the operations published for this MCP audience and continue the current task without replacing the worker."
    return {
        "schema": "cortex/tool-availability/v1",
        "ok": False,
        "outcome": "recovery_advice",
        "code": code,
        "retryable": False,
        "attempt_budget_consumed": False,
        "worker_replacement_authorized": False,
        "diagnostics": [{
            "code": code,
            "phase": "routing",
            "path": "tool",
            "json_pointer": "/tool",
            "message": f"{normalized_tool} is not published for the launch-time {normalized_audience} MCP audience",
            "received": None,
            "expected": "an operation published for this MCP audience",
            "field_schema": {"type": "string"},
            "fix": next_action,
        }],
        "next_action": next_action,
    }


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
    "prepare_delegations", "confirm_host_spawn", "finalize_attempt",
    "record_evidence", "execute_verification_command", "cortex.question",
    "publish_worker_question", "list_worker_questions", "answer_worker_question",
    "get_worker_question_updates", "record_gate_outcome", "commit_gate",
    "resume_task", "update_pipeline", "reassess_pipeline", "acquire_lock",
    "release_lock", "create_handoff", "claim_resource", "release_resource",
    "create_lane", "get_lane_status", "claim_lane", "release_lane",
    "retire_lane", "bind_task_lane", "claim_lane_resource",
    "release_lane_resource", "materialize_lane", "reconcile_lane",
    "orchestrate", "activate_orchestration", "deactivate_orchestration",
    "get_activation_status", "classify_task", "resolve_dispatch_route",
})


PUBLIC_TOOL_DESCRIPTIONS = {
    "start_orchestration": "Start a Cortex task from the exact user-authored request. Before the single call, every ordinary task needs non-empty task.acceptance_criteria and task.verification grounded in that request or verified authority; task.verification is the array of concrete authoritative checks, and verification_mode is not a task field. Use only fields advertised by this schema: unknown task fields are rejected before task creation. Ask the user if material intent is missing. Host activation context must already be established by the host before this call. Cortex preserves the intent boundary and returns native dispatches with canonical profile, capability, access, and selection rationale.",
    "continue_orchestration": "Verify continuation.task_id against the active task, then submit the server-derived continuation.step and continuation.results from read_worker_result verbatim for the active wave; never increment the step or substitute a projection_ref/formatted ref. Pass the exact task_ref returned by start_orchestration; Cortex resolves its host-bound project root from that opaque task_ref. Never submit an inline worker result body. A successful continue is a one-shot lifecycle receipt: if it returns dispatches, invoke only those exact dispatches; if a worker result is terminal non-success, Cortex records the error in the JSONC ledger and automatically derives one corrective owner/dispatch or a concrete user question—never a system block, wait loop, or replacement worker. If it returns waiting_workers, wait only for the exact persisted workers. Never call continue again with the same step/results, request artifacts, add future_waves, or spawn a replacement. A retryable=false task-identity or step-mismatch diagnostic is a server-owned reconciliation receipt; Cortex rehydrates the exact task and continues or surfaces only a real task question.",
    "manage_orchestration": "Inspect or recover one explicit task, create a linked corrective task for a completed source with intent=follow_up, prune stale tasks, run SQLite health/maintenance actions, surface one durable worker question at a time, or review a completed plan. Terminal worker failures are normally recovered automatically during continue_orchestration; intent=recover_blocked is the canonical server-owned retry for a lost recovery response and accepts only intent, task_ref, and optional reason—never payload or future_waves. intent=inspect is always read-only; when lifecycle recovery explicitly requires repair, use intent=recover_inspect and let Cortex derive the exact scope. Every task-scoped intent requires the exact task_ref returned by a successful lifecycle response, except the next ordinary-chat question answer: command=answer may omit both task_ref and question_ref, and the host resolves the unique idle/resumable open question from its private session ledger. Cortex derives the host-bound project root from that binding. Project-scoped prune/maintenance require project_root and omit task_ref. Question and plan-review responses include a localized user_view plus an internal receipt: render only user_view as the final ordinary assistant message, show one decision/question, visibly name the recommendation, and wait for the user's next message. Never call a UI/input/approval/elicitation tool or infer approval from silence. A successful durable question answer returns a server-derived resume_contract; copy its ref, attempt_id, profile, and poll_action verbatim when resuming the same existing worker, while retaining the original native target. Record the next message against the same interaction ref before resuming the exact worker or plan. Generic placeholders are rejected. When awaiting_translation, call the returned translation_request exactly; Cortex resolves all internal identity.",
    "worker_question": "Worker-only operation. Submit one self-contained task question or atomic batch with concrete outcome-based options; questions may cover only task requirements, never internal Cortex mechanics. The server-owned worker session supplies task, attempt, profile, and dispatch identity.",
    "record_attempt_event": "Worker-only incremental semantic event operation. Submit event_type, payload, and optional event_key; the immutable server-owned worker session supplies project/task/attempt/profile identity.",
    "complete_attempt": "Worker-only semantic completion operation. Submit AttemptResult fields only; the immutable server-owned worker session supplies task/attempt/profile identity. Planning repair uses only base_payload_digest plus diagnostic-scoped patches.",
    "read_dispatch_briefing": "Worker-only scoped read. The immutable server-owned worker session supplies project/task/attempt/profile, dispatch, and briefing digest. Submit only optional cursor/max_bytes.",
    "read_worker_result": "Read one canonical AttemptResult/AttemptEvent view. Coordinators supply only the exact task_ref and attempt_result_ref; Cortex resolves the immutable project root from the host-bound task. Bound successor workers supply only refs granted in their dispatch; the server-owned worker session supplies identity.",
    "manage_governance": "Host-bound coordinator governance surface: manage initiatives, typed dependencies, immutable governance records, active snapshots, constrained exceptions, and coordinator-approved policy-promotion proposals. project_root is optional when task_ref identifies the server-bound task; otherwise provide the exact project root for project-scoped administration. Normal calls are semantic forms only: Cortex derives coordinator identity and authorization from the active host/session binding, and caller-authored principal, thread_id, or capability fields are rejected. Only the explicitly discriminated recovery actions accept proof/identity fields, and only when a prior authorization response was actually lost. Every mutation names its initiative/task/record scope; worker proposals cannot approve or activate policy.",
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
            "phase": {
                "type": "string",
                "minLength": 1,
                "enum": sorted(canonical_gates),
                "description": (
                    "Canonical phase: scope, plan, discover, architecture, database_architecture, implementation, qa, "
                    "security, performance, accessibility, ux, review, documentation, governance_activation, "
                    "governance_close, or close. A canonical phase may "
                    "appear in only one wave, though one wave may contain multiple workers for that phase."
                ),
            },
            "profile": {
                "type": "string",
                "enum": public_profile_values,
                "description": "Optional canonical Cortex profile name; omit it to use the phase owner.",
            },
            "objective": {"type": "string"},
            "strategy": {
                "type": "string",
                "minLength": 1,
                "description": "Optional concise name for the worker approach; Cortex preserves it as rework evidence but never uses it to impose an attempt limit.",
            },
            "paths": {"type": "array", "items": {"type": "string"}},
            "allowed_paths": {
                "type": "array", "minItems": 1,
                "items": {
                    "type": "string", "minLength": 1,
                    "not": {"enum": [".", "*"]},
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
                "type": "string",
                "description": (
                    "Model explicitly requested by the user using its canonical model identifier. "
                    "Non-security Sol is rejected unless it is supplied through this field."
                ),
            },
            "effort": {"type": "string", "description": "Optional expert reasoning-effort override."},
            "visible": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Opt into a user-owned visible task only when the immutable task contract contains "
                    "visible_thread_requested=true; otherwise Cortex rejects this field and uses a hidden subagent."
                ),
            },
            "isolated_checkout": {
                "type": "boolean",
                "default": False,
                "description": "Optional worktree isolation for an explicitly authorized visible task.",
            },
        },
        "required": ["phase"],
    }
    V3_WAVE_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"workers": {"type": "array", "minItems": 1, "maxItems": 32, "items": V3_WORKER_SCHEMA}},
        "required": ["workers"],
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
                    "visible_thread_requested": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Explicit user authorization for visible task creation. Set only when the user asked "
                            "for a visible task; hidden subagents remain the default and visible worker fields are "
                            "rejected without this immutable opt-in."
                        ),
                    },
                    "complexity": {"type": ["string", "integer"], "enum": ["C1", "C2", "C3", 1, 2, 3], "description": "Optional canonical complexity; defaults to C2."},
                    "replan_limit": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Historical task metadata. It is not a lifetime execution cap; evidence-backed public replans are not blocked by it.",
                    },
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
                                "pattern": "(?:[Hh][Aa][Rr][Vv][Ee][Ss][Tt](?:-[Rr][Ee][Ff][Rr][Ee][Ss][Hh])?)",
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
            "task_ref": {"type": "string", "description": "Exact opaque task reference returned by start_orchestration; required for every continuation."},
            "step": {"type": "integer", "minimum": 1, "description": "Relative step returned by the preceding Cortex response; enables safe idempotent replay without a wave identifier."},
            "results": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "worker": {"type": "integer", "minimum": 1, "description": "Required only for a parallel wave."},
                        "attempt_result_ref": {"type": "string", "minLength": 1, "description": "Bare canonical result ref from read_worker_result.continuation.results; never a projection_ref or formatted attempt_result_ref=<id> string. Successful continuation never accepts an inline worker result body."},
                        "dispatch_ref": {"type": "string", "minLength": 1, "description": "Exact dispatch ref returned by Cortex; required only for a non-success result so stale failures cannot target a replacement attempt."},
                        "status": {"type": "string", "enum": ["failed", "blocked", "cancelled", "superseded"], "description": "Omit for success; use one canonical non-success status when a worker did not pass."},
                        "reason": {"type": "string", "description": "Required for a non-success result."},
                        "next_strategy": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Optional materially different approach when evidence supports it; never required merely to authorize another corrective attempt.",
                        },
                    },
                },
            },
            "future_waves": {"type": "array", "minItems": 1, "items": V3_WAVE_SCHEMA},
            "rework": {
                "type": "boolean",
                "default": False,
                "description": "Optional rework hint. Cortex automatically infers rework when future_waves reintroduces a current or completed phase.",
            },
            "reason": {"type": "string"},
        },
        "required": ["task_ref", "step", "results"],
    }
    WORKER_RECORD_ATTEMPT_EVENT_SCHEMA = {
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
    }
    WORKER_COMPLETE_ATTEMPT_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "Persist the minimal semantic AttemptResult, then let Cortex finalize server-owned receipts, "
            "workspace observations, and a regenerated non-authoritative result view on the same attempt. "
            "A rejected planner draft is repaired in a separate PATCH-only shape: send only the identity "
            "fields, base_payload_digest, and diagnostic-scoped patches; never resend the full planning object "
            "or semantic fields during repair."
        ),
        "properties": {
            "status": {"type": "string", "enum": ["completed", "blocked", "failed"]},
            "summary": {"type": "string", "minLength": 1},
            "findings": {"type": "array"},
            "decisions_needed": {"type": "array"},
            "unresolved": {"type": "array"},
            "claims": {
                "type": "array",
                "description": "Optional semantic criterion/evidence claims; Cortex maps them into generated acceptance projections without treating them as identity or telemetry.",
            },
            "base_payload_digest": {
                "type": "string",
                "pattern": "^sha256:[0-9a-f]{64}$",
                "description": "Digest returned with a rejected planner draft; required for same-attempt PATCH repair.",
            },
            "patches": {
                "type": "array",
                "minItems": 1,
                "description": "PATCH-only planner repair. Every path must be a returned diagnostic path or descendant; unrelated fields are preserved server-side.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "op": {"type": "string", "enum": ["replace", "add", "remove"]},
                        "path": {"type": "string", "pattern": "^/"},
                        "value": {},
                    },
                    "required": ["op", "path"],
                },
            },
            "planning": {
                "type": "object",
                "additionalProperties": False,
                "description": "Planner-only work breakdown. Accepted only for the planner profile on the plan gate; Cortex validates and persists it separately from AttemptResult.",
                "properties": V3_PLANNING_SCHEMA["properties"],
                "required": V3_PLANNING_SCHEMA["required"],
            },
        },
        # The semantic fields are required for a normal completion, but not
        # for a planner repair.  Keeping this distinction in the public JSON
        # Schema is important: the model must be able to emit only the
        # rejected-field patches after validation, rather than reconstructing
        # a complete AttemptResult and planning object.
        "required": [],
        "oneOf": [
            {
                "required": ["status", "summary", "findings", "decisions_needed", "unresolved"],
                "not": {
                    "anyOf": [
                        {"required": ["planning"]},
                        {"required": ["base_payload_digest"]},
                        {"required": ["patches"]},
                    ],
                },
            },
            {
                "required": ["status", "summary", "findings", "decisions_needed", "unresolved", "planning"],
                "not": {
                    "anyOf": [
                        {"required": ["base_payload_digest"]},
                        {"required": ["patches"]},
                    ],
                },
            },
            {
                "required": ["base_payload_digest", "patches"],
                "not": {
                    "anyOf": [
                        {"required": ["status"]},
                        {"required": ["summary"]},
                        {"required": ["findings"]},
                        {"required": ["decisions_needed"]},
                        {"required": ["unresolved"]},
                        {"required": ["claims"]},
                        {"required": ["planning"]},
                    ],
                },
            },
        ],
    }
    WORKER_QUESTION_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["ask", "poll", "ask_batch", "poll_batch"]},
            "question_ref": {"type": "string", "description": "Exact ref returned by ask; required for poll."},
            "batch_ref": {"type": "string", "description": "Exact ref returned by ask_batch; required for poll_batch."},
            "question": {"type": "string", "minLength": 1, "description": "Material user decision; required for ask."},
            "header": {"type": "string"},
            "options": {"type": "array", "items": question_option_schema},
            "multiple": {"type": "boolean"},
            "custom_label": {"type": "string"},
            "context": {},
            "recommendation": {"type": "string", "minLength": 1, "description": "Required LLM rationale for the recommended answer."},
            "recommended_option_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}, "description": "Required for choice questions; IDs must name the option(s) the LLM recommends."},
            "recommended_answer": {"type": "string", "minLength": 1, "description": "Required for text questions; concrete answer wording the LLM recommends."},
            "batch": {
                "type": "object",
                "additionalProperties": False,
                "description": "Durable material-question batch. question_key and option_id are stable canonical identifiers; the coordinator renders one ordinary-chat question per turn, stops, and checkpoints the user's next message before advancing.",
                "properties": {
                    "batch_key": {"type": "string", "minLength": 1},
                    "questions": {
                        "type": "array", "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "question_key": {"type": "string", "minLength": 1},
                                "question": {"type": "string", "minLength": 1},
                                "type": {"type": "string", "enum": ["single_select", "multi_select", "text"]},
                                "header": {"type": "string"},
                                "options": {"type": "array", "items": question_option_schema},
                                "custom_label": {"type": "string"},
                                "context": {"type": "string", "description": "Evidence or conflict that makes this user decision necessary."},
                                "recommendation": {"type": "string", "minLength": 1, "description": "Required LLM rationale for the recommended answer; neutrality is expressed in the rationale, never by omission."},
                                "recommended_option_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}, "description": "Required for single_select and multi_select; exact option IDs the LLM recommends."},
                                "recommended_answer": {"type": "string", "minLength": 1, "description": "Required for text; concrete answer wording the LLM recommends."},
                            },
                            "required": ["question_key", "question", "type", "recommendation"],
                        },
                    },
                },
                "required": ["batch_key", "questions"],
            },
        },
        "required": ["action"],
    }
    READ_WORKER_RESULT_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_ref": {"type": "string", "description": "Exact opaque task reference; required for every result read."},
            "attempt_result_ref": {"type": "string", "minLength": 1},
        },
        "required": ["task_ref", "attempt_result_ref"],
    }
    WORKER_READ_WORKER_RESULT_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "description": "Read one predecessor result using the immutable worker dispatch binding; the worker supplies only the canonical result ref.",
        "properties": {
            "attempt_result_ref": {"type": "string", "minLength": 1},
        },
        "required": ["attempt_result_ref"],
    }
    READ_DISPATCH_BRIEFING_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "cursor": {"type": "string", "description": "Opaque continuation cursor for the same large immutable briefing."},
            "max_bytes": {"type": "integer", "minimum": 1, "description": "Optional caller-selected UTF-8 briefing page size. Omit it to read the complete immutable briefing; Cortex does not clamp it."},
        },
        "required": [],
    }
    MANAGE_ORCHESTRATION_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "project_root": {"type": "string", "minLength": 1, "format": "absolute-path", "description": "Required for project-scoped prune/maintenance; optional for an implicit ordinary-chat question answer, where it only narrows the host-private search. Other task-scoped intents derive the project root from task_ref."},
            "intent": {
                "type": "string",
                "enum": [
                    "inspect", "recover_inspect", "recover_blocked", "resume", "deactivate", "lane", "resource",
                    "question", "plan_approval", "follow_up", "steer", "prune", "maintenance", "artifacts",
                ],
                "description": "Canonical management operation. Convenience aliases are not part of the public contract; use the enum value exactly.",
            },
            "task_ref": {"type": "string", "description": "Exact opaque task reference required for every task-scoped intent, including server-owned recover_blocked. Only prune and maintenance omit it."},
            "reason": {"type": "string"},
            "payload": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "future_waves": {
                        "type": "array",
                        "minItems": 1,
                        "description": "Resume/rework waves. Each item is a complete FutureWave object; invalid fields are reported by their exact nested path.",
                        "items": V3_WAVE_SCHEMA,
                    },
                    "rework": {"type": "boolean", "default": False},
                    "command": {"type": "string", "enum": ["ask", "answer", "list", "updates", "inspect"]},
                    "question_ref": {"type": "string", "minLength": 1},
                    "answer": {},
                    "answers": {"type": "object", "additionalProperties": {}},
                    "answer_en": {"type": "string", "minLength": 1},
                    "canonical_answers": {
                        "type": "object", "additionalProperties": {"type": "string", "minLength": 1},
                    },
                    "decision": {"type": "string", "enum": ["prompt", "approve", "approve_with_recommendations", "approve_without_recommendations", "cancel", "revise"]},
                    "approval_mode": {"type": "string", "enum": ["approve_with_recommendations", "approve_without_recommendations"]},
                    "feedback": {"type": "string"},
                    "request_id": {"type": "string", "minLength": 1},
                    "localized_prompt": {"type": "string"},
                    "localized_title": {"type": "string"},
                    "localized_approve": {"type": "string"},
                    "localized_cancel": {"type": "string"},
                    "localized_question": {"type": "string", "minLength": 1},
                    "localized_header": {"type": "string"},
                    "localized_options": {"type": "array", "items": question_option_schema},
                    "localized_custom_label": {"type": "string"},
                    "source_task_ref": {"type": "string", "minLength": 1},
                    "user_request": {"type": "string", "minLength": 1},
                    "requirements": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "constraints": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "acceptance_criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                    "scope": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "allowed_paths": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "verification": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                    "budget": {"type": "string"},
                    "pause_conditions": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "user_language": {"type": "string"},
                    "language": {"type": "string"},
                    "complexity": {"type": ["string", "integer"]},
                    "replan_limit": {"type": "integer", "minimum": 0},
                    "plan_approval": {
                        "type": "string",
                        "enum": ["auto", "required"],
                        "description": (
                            "Explicit plan-review intent for the task. Use required only when the user asked "
                            "to approve plans; internal recommendations remain advisory."
                        ),
                    },
                    "result_refs": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                    "question": {"type": "string", "minLength": 1},
                    "header": {"type": "string"},
                    "options": {"type": "array", "items": question_option_schema},
                    "multiple": {"type": "boolean"},
                    "custom_label": {"type": "string"},
                    "context": {},
                    "source_result_refs": {
                        "type": "array", "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "action": {"type": "string", "enum": ["health", "checkpoint", "backup", "verify_backup_restore", "optimize", "vacuum", "reconcile_projections"]},
                    "confirmation": {"type": "string"},
                    "full_confirmation": {"type": "string"},
                    "older_than_days": {"type": "integer", "minimum": 0},
                    "mode": {"type": "string", "enum": ["recover_lifecycle"]},
                    "artifacts": {"type": "array", "items": {"type": "object"}},
                    "cursor": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "description": (
                    "Rare-operation payload. For intent=plan_approval, decision=prompt returns a detailed "
                    "cortex/chat-interaction/v1 plan summary with approve, revise, and cancel meanings plus an explicit "
                    "LLM recommendation. Render it as the final ordinary chat message and end the turn. The user's next "
                    "message must be submitted with the exact request_id; preserve revision feedback verbatim. For intent=follow_up, use the completed source task_ref and an exact "
                    "corrective user_request; optional source_result_refs select canonical result context. For intent=question normal usage is exactly "
                    "{question_ref: '<worker ref>'} plus optional localized display labels. Cortex returns one detailed "
                    "ordinary-chat interaction containing context, consequences, options, and the mandatory LLM recommendation. The next ordinary-chat answer may omit both task_ref and question_ref: use command=answer with answer only, and the server resolves the unique host-bound open question and exact resumable child. For batch localization, "
                    "each ordered item uses localized_question, localized_header, localized_options, and optional "
                    "localized_custom_label. Every question "
                    "and option must be self-contained and outcome-specific; generic numbered or recommended/alternative "
                    "placeholders are rejected. If Cortex returns "
                    "awaiting_translation, submit its translation_request unchanged except for the English translation: "
                    "a single question uses {question_ref, answer, answer_en}; a batch uses {question_ref, canonical_answers}. "
                    "Cortex resolves task/principal/thread and never opens nested UI. For intent=resume after a closure-rework cycle, payload.future_waves is the coordinator's chosen corrective pipeline; Planner is one recommendation, not a required first wave. No-progress findings are recorded as routing evidence and do not pause other executable work; payload.rework may name the exact gate to update when several findings exist. Never add guessed identity fields. Artifacts accepts a bounded list, metadata, or read "
                    "action and opaque cursors; it never returns all bodies together. recover_blocked is server-owned: "
                    "send only intent and task_ref (plus optional reason); never send payload or future_waves. Prune requires confirmation='PRUNE' "
                    "and accepts older_than_days (default 7). Maintenance accepts action=health|checkpoint|backup|verify_backup_restore|optimize|vacuum|reconcile_projections. Every mutating maintenance action requires its exact action-specific confirmation; backup creates a private .cortex-backup DR bundle containing the SQLite ledger, governance lifecycle key, and fingerprint manifest, and verify_backup_restore validates that bundle on a fresh disposable host root through the governance layer. Normal wave progression never uses this field."
                ),
            },
        },
        "required": ["intent"],
        "allOf": [
            {
                "if": {
                    "properties": {
                        "intent": {"enum": [
                            "inspect", "recover_inspect", "recover_blocked", "resume", "deactivate", "lane", "resource",
                            "plan_approval", "follow_up", "steer", "artifacts",
                        ]},
                    },
                    "required": ["intent"],
                },
                "then": {"required": ["task_ref"]},
            },
            {
                "if": {
                    "properties": {"intent": {"const": "question"}, "payload": {
                        "properties": {"command": {"const": "answer"}},
                        "required": ["command", "answer"],
                    }},
                    "required": ["intent", "payload"],
                },
                "then": {},
            },
            {
                "if": {
                    "properties": {"intent": {"enum": [
                        "inspect", "recover_inspect", "recover_blocked", "resume", "deactivate", "lane", "resource",
                            "plan_approval", "follow_up", "steer", "artifacts",
                    ]}},
                    "required": ["intent"],
                },
                "then": {"not": {"required": ["project_root"]}},
            },
            {
                "if": {
                    "properties": {"intent": {"enum": ["prune", "maintenance"]}},
                    "required": ["intent"],
                },
                "then": {"not": {"required": ["task_ref"]}},
            },
            {
                "if": {
                    "properties": {"intent": {"enum": ["prune", "maintenance"]}},
                    "required": ["intent"],
                },
                "then": {"required": ["project_root"]},
            },
            {
                "if": {
                    "properties": {"intent": {"const": "recover_blocked"}},
                    "required": ["intent"],
                },
                "then": {"not": {"required": ["payload"]}},
            },
        ],
    }
    MANAGE_GOVERNANCE_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "description": "Dedicated governance surface for initiatives, dependency graph integrity, append-only records, snapshots, exceptions, and approval-only promotion proposals.",
        "properties": {
            "project_root": {"type": "string", "minLength": 1, "format": "absolute-path", "description": "Required for project-scoped governance when task_ref is absent. Task-bound governance derives the root from task_ref and does not accept this field."},
            "action": {"type": "string", "minLength": 1, "enum": [
                "create", "create_initiative", "inspect", "inspect_initiative",
                "link_task", "link", "link_record", "record_link",
                "add_dependency", "dependency", "transition", "transition_initiative",
                "create_record", "record_create", "revise_record", "record_revise", "revise",
                "inspect_record", "record_inspect", "history", "list_records", "snapshot", "snapshot_inspect",
                "request_exception", "exception_request", "evaluate_promotion", "promotion_evaluate", "promotion_inspect",
                "approve_promotion", "promotion_approve", "approve", "reject_promotion", "promotion_reject", "reject",
                "recover_coordinator_capability", "acknowledge_coordinator_recovery"
            ], "description": "Canonical governance action. Use only one enum value; aliases are accepted only where explicitly listed by this schema."},
            "task_ref": {"type": "string", "minLength": 1, "description": "Exact task reference. Recovery scope and authorization are resolved from the active host-bound coordinator session."},
            "capability_generation": {"type": "integer", "minimum": 1, "description": "Optional expected server-owned capability generation. Recovery expects the current generation; acknowledgement expects its next generation. A mismatch fails closed."},
            "submission_id": {"type": "string", "minLength": 1, "description": "Stable caller-generated identifier for durable create_record retry after a lost response. Reuse is accepted only for the exact same immutable command."},
            "entity": {"type": "string"},
            "initiative_ref": {"type": "string"},
            "parent_ref": {"type": "string"},
            "title": {"type": "string"},
            "goal": {"type": "string"},
            "owner": {"type": "string"},
            "risk": {"type": "string", "enum": ["low", "moderate", "high", "critical"]},
            "acceptance_oracle_artifact_ref": {"type": "string"},
            "task_id": {"type": "string"},
            "lane_id": {"type": "string"},
            "relationship": {"type": "string"},
            "milestone": {"type": "string"},
            "deliverable": {"type": "string"},
            "corrective": {"type": "boolean"},
            "expected_revision": {"type": "integer", "minimum": 1},
            "status": {"type": "string"},
            "evidence": {"type": "object"},
            "source_type": {"type": "string", "enum": ["initiative", "task"]},
            "source_ref": {"type": "string"},
            "target_type": {"type": "string", "enum": ["initiative", "task"]},
            "target_ref": {"type": "string"},
            "dependency_type": {"type": "string", "enum": ["blocks", "requires", "relates_to", "follows"]},
            "dependency_ref": {"type": "string"},
            "record_ref": {"type": "string"},
            "record_type": {"type": "string", "enum": ["policy", "decision", "ruling", "preference", "assumption", "risk", "learning", "reflection", "exception", "promotion"]},
            "content": {},
            "created_by": {"type": "string"},
            "supersedes": {"type": "string"},
            "expires_at": {"type": "string", "description": "Timezone-aware ISO-8601 expiry. Sensitive records derive it from the approved retention_days policy when omitted and may not exceed that bound."},
            "approval_basis": {},
            "content_artifact_ref": {"type": "string"},
            "link_ref": {"type": "string"},
            "finding_fingerprint": {"type": "string"},
            "evidence_ref": {"type": "string"},
            "fingerprint": {"type": "string"},
            "findings": {"type": "array", "items": {"type": "object"}},
            "threshold": {"type": "integer", "minimum": 1},
            "window_days": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 256, "description": "Bounded governance history/snapshot page size."},
            "offset": {"type": "integer", "minimum": 0, "description": "Bounded governance history/snapshot page offset."},
            "proposal_ref": {"type": "string"},
            "trigger": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["action"],
        "allOf": [
            {
                "anyOf": [{"required": ["project_root"]}, {"required": ["task_ref"]}],
                "description": "Supply project_root for project-scoped administration, or task_ref for task-bound administration; never send both.",
            },
            {
                "if": {"required": ["task_ref"]},
                "then": {"not": {"required": ["project_root"]}},
            },
            {
                "if": {"properties": {"action": {"enum": ["recover_coordinator_capability", "acknowledge_coordinator_recovery"]}}, "required": ["action"]},
                "then": {"required": ["task_ref"]}
            },
            {
                "if": {"properties": {"action": {"enum": ["create_initiative"]}}, "required": ["action"]},
                "then": {"required": ["title", "goal"]}
            },
            {
                "if": {"properties": {"action": {"enum": ["create_record", "record_create"]}}, "required": ["action"]},
                "then": {"required": ["record_type", "content"]}
            },
            {
                "if": {"properties": {"action": {"enum": ["add_dependency", "dependency"]}}, "required": ["action"]},
                "then": {"required": ["source_type", "source_ref", "target_type", "target_ref", "dependency_type"]}
            },
            {
                "if": {"properties": {"action": {"enum": ["request_exception", "exception_request"]}}, "required": ["action"]},
                "then": {"required": ["trigger", "reason"]}
            }
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
        "worker_read_worker_result": WORKER_READ_WORKER_RESULT_SCHEMA,
    }



def v3_response(
    old: dict[str, Any],
    task_ref: str,
    *,
    native_arguments: Callable[[dict[str, Any]], dict[str, Any]],
    public_schema: str,
    coordinator_lock: str,
    include_result: bool = False,
    start_replayed: bool | None = None,
) -> dict[str, Any]:
    state_summary = old.get("state_summary") if isinstance(old.get("state_summary"), dict) else {}
    communication_profile = old.get("communication_profile") or state_summary.get("communication_profile")
    user_language = old.get("user_language") or state_summary.get("user_language")
    communication_config = ({key: value for key, value in {
        "communication_profile": communication_profile,
        "user_language": user_language,
    }.items() if value} or None)
    wave_label = str(old.get("wave_id") or "")
    wave_match = re.search(r"(\d+)$", wave_label)
    step = int(wave_match.group(1)) if wave_match else None
    if not old.get("ok"):
        diagnostics = old.get("diagnostics") if isinstance(old.get("diagnostics"), list) else []
        operation = str(old.get("operation") or "")
        retry_tool = "start_orchestration" if operation == "start" else "continue_orchestration"
        raw_outcome = str(old.get("state", "needs_input"))
        public_outcome = "needs_input" if raw_outcome == "blocked" else raw_outcome
        old_result = old.get("result") if isinstance(old.get("result"), dict) else {}
        requires_user_decision = _is_user_decision_event(public_outcome, old_result)
        server_action = str(old.get("next_action") or "").strip()
        response = {
            "schema": public_schema,
            "ok": False,
            "outcome": public_outcome,
            "code": old.get("code", "orchestration_failed"),
            "step": step,
            "diagnostics": diagnostics,
            "dispatches": [],
            "recoverable": bool(old.get("recoverable", True)),
            "next_action": server_action or f"Correct every diagnostic and retry {retry_tool} with the same task; do not mutate unrelated fields.",
            "requires_user_decision": requires_user_decision,
        }
        if task_ref:
            response["task_ref"] = task_ref
        if include_result and "result" in old:
            response["result"] = old["result"]
        if isinstance(old.get("pipeline"), dict):
            response["pipeline"] = old["pipeline"]
        if isinstance(old.get("governance"), dict):
            response["governance"] = old["governance"]
            response["requested_mode"] = old["governance"].get("requested_mode")
            response["effective_mode"] = old["governance"].get("effective_mode")
            response["classification_reasons"] = old["governance"].get("reasons", [])
            response["trigger_evidence"] = old["governance"].get("trigger_evidence", [])
            response["initiative_ref"] = old["governance"].get("initiative_ref")
            response["policy_snapshot_digest"] = old["governance"].get("policy_snapshot_digest")
            response["close_obligations"] = old["governance"].get("close_obligations", [])
        # `blocked` remains an internal audit/recovery marker only.  The
        # public coordinator contract never stops Cortex on that state: it
        # asks for the exact recovery action or one concrete user decision.
        response["user_message"] = render_lifecycle(
            response.get("outcome"), ok=False, config=communication_config,
            metadata={"code": response.get("code"), "recoverable": response.get("recoverable")},
        )
        response["next_action"] = _public_next_action(response.get("next_action"))
        response["user_view"] = _public_user_view_with_decision(
            response["user_message"], requires_user_decision=requires_user_decision,
        )
        response["internal"] = _internal_protocol(response)
        return response
    requests = old.get("spawn_requests") if isinstance(old.get("spawn_requests"), list) else []
    prepared_dispatches = [
        {
            "worker": index,
            "dispatch_ref": request.get("dispatch_ref"),
            "phase": request.get("phase"),
            "profile": request.get("profile"),
            "display_name": request.get("display_name"),
            "capability": request.get("capability"),
            "sandbox": request.get("sandbox"),
            "selection_reason": request.get("selection_reason"),
            "briefing_path": request.get("briefing_path"),
            "briefing_digest": request.get("briefing_digest"),
            "call": request.get("host_tool") or "spawn_agent",
            "arguments": native_arguments(request),
        }
        for index, request in enumerate(requests, 1)
    ]
    # A replay is a lifecycle receipt, never a second host-dispatch grant. If
    # the original response was lost before any native call was made, inspect
    # can recover only the still-awaiting requests without making every exact
    # duplicate start capable of spawning a duplicate worker wave.
    dispatches = [] if start_replayed is True else prepared_dispatches
    raw_outcome = old.get("state")
    outcome = "needs_input" if raw_outcome == "blocked" else raw_outcome
    if start_replayed is True:
        next_action = (
            f"{coordinator_lock} start_orchestration was already completed for task_ref={task_ref}. "
            "Do not invoke or repeat any worker dispatch from this replay. If the original start response was "
            "lost before its native dispatches were invoked, call manage_orchestration with intent inspect once "
            "and invoke only the still-awaiting dispatches returned by that recovery call."
        )
    elif dispatches:
        start_transition = (
            f" start_orchestration is complete for task_ref={task_ref}; never call it again for this task."
            if start_replayed is not None else ""
        )
        next_action = (
            f"{coordinator_lock}{start_transition} NEXT REQUIRED ACTION: FIRST close every known completed child whose "
            "canonical AttemptResult was read or whose exact failed result Cortex already accepted; never close a running or "
            "question-paused child. If recovery may have missed one, use list_agents defensively. THEN call every "
            "dispatch.call exactly once with its exact dispatch.arguments. Until every returned dispatch has been "
            "invoked, do not call start_orchestration, continue_orchestration, manage_orchestration, inspect, or wait. "
            "A worker exists only after the native call returns a child target. Never claim it was sent or call wait "
            "without the returned child target. Do not substitute a generic collaboration spawn, self-authored task "
            "name, or replacement child: it cannot bind to or advance the issued Cortex attempt. Wait only for those "
            "returned targets. Each worker must publish through "
            "complete_attempt. For each terminal worker, read its exact returned attempt_result_ref with read_worker_result, "
            "then copy that server-returned read_worker_result.continuation.step and continuation.results verbatim into "
            "continue_orchestration; never increment its step or substitute a projection_ref/formatted reference. Only "
            "after that successful server continuation or terminal audit, close that exact completed native child with "
            "close_agent. Do not dispatch another worker before that close succeeds."
        )
    elif outcome == "awaiting_plan_approval":
        next_action = (
            f"{coordinator_lock} Read the Planner's attempt_result_ref with read_worker_result after it completes. "
            "Use its generated result view only as a non-authoritative display projection. Then call manage_orchestration with "
            "intent=plan_approval and payload.decision=prompt. Render its chat_interaction completely as one final "
            "ordinary user-language message: objective, work packages, paths, dependencies, verification, risks, "
            "remaining phases, all approve/revise/cancel meanings, and the visibly labelled LLM recommendation with "
            "rationale. Do not call a UI/input/approval/elicitation tool. End the turn and wait. Submit the user's "
            "next unambiguous response with the exact request_id; preserve requested changes verbatim."
        )
    elif outcome == "completion_pending":
        pending = (
            old.get("result", {}).get("pending_result_completions", [])
            if isinstance(old.get("result"), dict) else []
        )
        selections = []
        for item in pending:
            if not isinstance(item, dict):
                continue
            slot = item.get("worker")
            refs = [str(ref) for ref in item.get("candidate_attempt_result_refs", []) if str(ref).strip()]
            if slot and refs:
                selections.append(f"worker={slot}: " + ", ".join(refs))
        next_action = (
            f"{coordinator_lock} A native worker already stopped after recording a durable AttemptResult; it is not a live "
            "child. Never wait on, respawn, or resume it. Read the candidate AttemptResult refs, then explicitly select "
            "exactly one identity-validated attempt_result_ref for each listed worker slot and call continue_orchestration "
            f"once for task_ref={task_ref} and this step. Cortex rejects a superseded canonical result before any "
            "state mutation. Candidates: "
            + ("; ".join(selections) if selections else "none validated; inspect the canonical result mismatch and do not fabricate a result.")
        )
    elif outcome == "completed":
        next_action = f"{coordinator_lock} Orchestration is complete; use the verified handoff without additional project operations."
    elif raw_outcome == "blocked":
        stopped_result_recovery = (
            old.get("result", {}).get("stopped_result_recovery")
            if isinstance(old.get("result"), dict) else None
        )
        if isinstance(stopped_result_recovery, dict):
            next_action = (
                "The stopped worker has no usable canonical AttemptResult. Cortex records this transport failure "
                "and derives the server-owned corrective dispatch automatically. Use only the returned recovery "
                "dispatch; do not wait, call manage_orchestration, respawn, replace the child, or edit ledger state."
            )
        else:
            next_action = (
                "Cortex retained the condition as recoverable evidence and will reconcile it through the "
                "server-owned corrective route. Follow only the returned recovery or real task question; do not "
                "call manage_orchestration, wait, respawn, or create a replacement worker."
            )
    elif outcome == "needs_input":
        user_question = (
            old.get("result", {}).get("question")
            if isinstance(old.get("result"), dict) else None
        )
        if user_question:
            next_action = (
                f"{coordinator_lock} Ask the user this exact orchestration question: {user_question} "
                "Record the user's decision through manage_orchestration for the same task_ref, then let Cortex "
                "resume the server-owned route. Do not mark the task blocked, wait, respawn, or invent future_waves."
            )
        else:
            next_action = (
                str(old.get("next_action") or "").strip()
                or "Cortex will reconcile this technical diagnostic from the durable task state and return the "
                "server-owned recovery action; do not call manage_orchestration, wait, respawn, or create a "
                "replacement worker."
            )
    else:
        next_action = (
            f"{coordinator_lock} Wait idly for the active worker results, then call continue_orchestration "
            f"with task_ref={task_ref} and this step."
        )
    if old.get("operation") == "inspect" and isinstance(old.get("result"), dict) and isinstance(old["result"].get("context_handoff"), dict):
        handoff = old["result"]["context_handoff"]
        active_worker_ids = [
            str(item.get("host_agent_id") or "")
            for item in handoff.get("active_workers", [])
            if isinstance(item, dict) and str(item.get("host_agent_id") or "").strip()
        ]
        stopped_workers = [
            item for item in handoff.get("stopped_workers", []) if isinstance(item, dict)
        ]
        open_questions = [
            item for item in handoff.get("open_questions", []) if isinstance(item, dict)
        ]
        pending_dispatches = [
            item for item in handoff.get("pending_dispatches", []) if isinstance(item, dict)
        ]
        terminal_results = [
            item for item in handoff.get("completed_results", [])
            if isinstance(item, dict)
            and str(item.get("lifecycle_status") or "").strip().lower() in {"blocked", "failed"}
            and str(item.get("attempt_result_ref") or "").strip()
            and str(item.get("dispatch_ref") or "").strip()
        ]
        finalization_pending = [item for item in stopped_workers if item.get("finalization_pending")]
        terminal_failures = [
            item for item in stopped_workers
            if str(item.get("failure_status") or "").strip()
            and not item.get("awaiting_user")
            and str(item.get("dispatch_ref") or "").strip()
        ]
        question_pauses = [
            item for item in stopped_workers
            if item.get("awaiting_user")
            and str(item.get("dispatch_ref") or "").strip()
        ]
        if outcome == "completion_pending" or (
            raw_outcome == "blocked"
            and isinstance(old.get("result"), dict)
            and isinstance(old["result"].get("stopped_result_recovery"), dict)
        ):
            pass
        elif (
            raw_outcome in {"waiting_workers", "blocked"}
            and not active_worker_ids
            and not pending_dispatches
            and terminal_results
        ):
            refs = "; ".join(
                f"attempt_result_ref={item['attempt_result_ref']!r}, dispatch_ref={item['dispatch_ref']!r}, "
                f"status={str(item.get('lifecycle_status') or '').strip().lower()!r}"
                for item in terminal_results
            )
            next_action = (
                f"{coordinator_lock} No active or pending worker exists. Read each exact canonical result with "
                f"read_worker_result(task_ref={task_ref!r}, attempt_result_ref=...) for {refs}; then copy the returned "
                "terminal_continuation.task_id, step, and results verbatim into continue_orchestration. Do not wait, "
                "respawn, replace, or fabricate a success result."
            )
        elif outcome == "waiting_workers" and active_worker_ids:
            failed_result_clause = ""
            if terminal_failures:
                targets = "; ".join(
                    f"dispatch_ref={item['dispatch_ref']!r}, status='failed', reason={item['failure_reason']!r}"
                    for item in terminal_failures
                )
                failed_result_clause = " Include exactly one failed result for each stopped slot when continuing: " + targets + "."
            next_action = (
                f"{coordinator_lock} Rehydrate only from result.context_handoff. Do not restart, replay, or respawn "
                "running attempts. Wait only on these exact persisted native child ids: "
                + ", ".join(active_worker_ids)
                + ". After completion, read each canonical AttemptResult with read_worker_result; the server records the "
                "machine read receipt before you continue Cortex."
                + failed_result_clause
            )
        elif finalization_pending:
            next_action = (
                f"{coordinator_lock} A stopped worker already has a canonical AttemptResult but finalization remains pending. "
                "Do not wait on, respawn, or replace it. Inspect once, then retry complete_attempt only for that exact persisted attempt."
            )
        elif question_pauses or open_questions:
            question_targets = question_pauses or open_questions
            targets = "; ".join(
                f"question_ref={item.get('question_ref') or (item.get('question_refs') or [''])[0]!r}, "
                f"dispatch_ref={item.get('dispatch_ref')!r}"
                for item in question_targets
            )
            next_action = (
                f"{coordinator_lock} A worker is paused for a durable user question, not blocked and not running. "
                f"Surface the exact question and call manage_orchestration(intent='question', task_ref={task_ref!r}, "
                f"payload={{'question_ref': ...}}) for {targets}; render the returned chat interaction and end this turn. "
                "After the user's answer, call manage_orchestration with the same question_ref, resume the exact same child, "
                "then call continue_orchestration only after that attempt result is recorded. Never wait, respawn, replace, "
                "or invent future_waves."
            )
        elif terminal_failures:
            failure_targets = "; ".join(
                f"dispatch_ref={item['dispatch_ref']!r}, status='failed', reason={item['failure_reason']!r}"
                for item in terminal_failures
            )
            next_action = (
                f"{coordinator_lock} Recovery found a terminal stopped worker without an AttemptResult. Never wait on, "
                "follow up, or respawn the stopped child. Call continue_orchestration exactly once with the current "
                f"task_ref={task_ref!r}, current step, and results=[{{'status':'failed','reason':..., 'dispatch_ref':...}}] "
                "using: " + failure_targets + "; Cortex will route bounded corrective rework automatically and never "
                "create a replacement worker for this receipt."
            )
        elif outcome == "waiting_workers" and not active_worker_ids and not pending_dispatches:
            next_action = (
                "There are no active workers or pending dispatches. Cortex will reconcile the durable lifecycle "
                "state and emit the next recovery dispatch; do not wait, call manage_orchestration, respawn, or "
                "edit ledger state."
            )
        else:
            next_action = (
                f"{coordinator_lock} Rehydrate only from result.context_handoff before continuing. "
                "It is the durable post-compaction state and target-handoff snapshot; do not restart the task or replay completed dispatches. Then "
                + next_action
            )
    response = {
        "schema": public_schema,
        "ok": True,
        "outcome": outcome,
        "task_ref": task_ref,
        "step": step,
        "next_action": next_action,
        "dispatches": dispatches,
    }
    requires_user_decision = _is_user_decision_event(outcome, old.get("result"))
    # ``needs_input`` is also used by the durable ledger for technical
    # recovery.  Render that case as an error/progress update unless the
    # server explicitly attached a real question or plan approval.
    visible_outcome = outcome if requires_user_decision or outcome != "needs_input" else "error"
    response["requires_user_decision"] = requires_user_decision
    response["user_message"] = render_lifecycle(
        visible_outcome, config=communication_config,
        metadata={"outcome": outcome, "step": step},
    )
    if outcome == "waiting_workers":
        response.update({
            "output_policy": "silent",
            "allowed_visible_events": [],
        })
    if start_replayed is not None:
        response["replayed"] = start_replayed
    if isinstance(old.get("pipeline"), dict):
        response["pipeline"] = old["pipeline"]
    if isinstance(old.get("governance"), dict):
        response["governance"] = old["governance"]
        response["requested_mode"] = old["governance"].get("requested_mode")
        response["effective_mode"] = old["governance"].get("effective_mode")
        response["classification_reasons"] = old["governance"].get("reasons", [])
        response["trigger_evidence"] = old["governance"].get("trigger_evidence", [])
        response["initiative_ref"] = old["governance"].get("initiative_ref")
        response["policy_snapshot_digest"] = old["governance"].get("policy_snapshot_digest")
        response["close_obligations"] = old["governance"].get("close_obligations", [])
    if outcome == "completed":
        summary = old.get("state_summary") if isinstance(old.get("state_summary"), dict) else {}
        response["result"] = {
            "close_verified": bool(summary.get("close_verified")),
            "handoff_ready": bool(summary.get("handoff_created")),
        }
    if include_result and "result" in old:
        response["result"] = old["result"]
        if isinstance(old["result"], dict) and isinstance(old["result"].get("context_handoff"), dict):
            response["context_handoff"] = old["result"]["context_handoff"]
        if isinstance(old["result"], dict) and old["result"].get("decision") == "cancelled":
            response["output_policy"] = "silent"
            response["allowed_visible_events"] = ["user_message"]
            response["next_action"] = (
                f"{coordinator_lock} Stop now and wait for the user's next message. Keep the plan pending; do not "
                "dispatch, revise, or send approval/cancellation commentary."
            )
    response["next_action"] = _public_next_action(response.get("next_action"))
    if outcome == "awaiting_plan_approval":
        review = (old.get("result") or {}).get("plan_review") if isinstance(old.get("result"), dict) else None
        if isinstance(review, dict):
            response["plan_review"] = review
    response["user_view"] = (
        None if outcome == "waiting_workers" else
        _public_user_view_with_decision(
            response["user_message"], requires_user_decision=requires_user_decision,
        )
    )
    if outcome == "awaiting_plan_approval" and isinstance(response.get("plan_review"), dict):
        review = response["plan_review"]
        packages = review.get("work_packages") if isinstance(review.get("work_packages"), list) else []
        steps = [
            str(item.get("title") or item.get("summary") or "Review the planned work.").strip()
            for item in packages if isinstance(item, dict)
        ][:5]
        if not steps:
            steps = ["Review the proposed work and its verification."]
        review_recommendation = str(review.get("recommendation") or "approve").strip().lower()
        has_material_risk = bool(review.get("risks") or review.get("uncertainty") or review.get("findings"))
        recommended_decision = "revise" if review_recommendation == "revise" or has_material_risk else "approve"
        is_ru = str(user_language or "").lower().startswith("ru")
        if is_ru:
            recommendation = (
                "Рекомендация: доработать план — остаётся существенный риск или пробел в проверке."
                if recommended_decision == "revise" else
                "Рекомендация: утвердить план — требования покрыты, неопределённости закрыты, проверки конкретны."
            )
            approval_question = "Утвердить план, запросить доработку или отменить?"
        else:
            recommendation = (
                "Recommendation: revise — a material risk or verification gap remains."
                if recommended_decision == "revise" else
                "Recommendation: approve — the request is covered, uncertainties are closed, and checks are concrete."
            )
            approval_question = "Approve the plan, request a revision, or cancel?"
        response["user_view"] = render_plan(
            str(review.get("summary") or review.get("objective") or "Review the proposed plan."),
            steps,
            question=approval_question,
            recommendation=recommendation,
            config=communication_config,
        )
        recommendation_rendered = render(
            recommendation,
            kind="question",
            next_step=approval_question,
            config=communication_config,
        )
        why_rendered = render(
            "Решение определяет, можно ли перейти к выполнению плана." if is_ru else
            "Your decision determines whether the plan can move to implementation.",
            kind="question",
            next_step=approval_question,
            config=communication_config,
        )
        response["user_view"]["risks"] = public_risks(
            review.get("risks") or review.get("uncertainty") or review.get("findings"),
            config=communication_config,
            limit=4,
        )
        response["user_view"]["recommendation"] = recommendation_rendered["message"]
        response["user_view"]["requires_user_decision"] = True
        response["user_view"]["why_it_matters"] = why_rendered["message"]
        quality = dict(response["user_view"].get("quality") or {})
        quality["fallback_applied"] = bool(
            quality.get("fallback_applied")
            or recommendation_rendered["quality"].get("fallback_applied")
            or why_rendered["quality"].get("fallback_applied")
        )
        quality["ok"] = bool(
            quality.get("ok")
            and recommendation_rendered["quality"].get("ok")
            and why_rendered["quality"].get("ok")
        )
        response["user_view"]["quality"] = quality
    response["internal"] = _internal_protocol(response)
    return response

def configure_internal_schemas(tools: dict[str, tuple[Callable[..., Any], dict[str, Any]]]) -> set[str]:
    """Apply authorization requirements to internal handlers before projection."""
    tools["record_delegation"][1]["properties"]["dispatch_mode"]["description"] = (
        "visible_thread creates a user-owned Luna task only when explicitly requested; it is never a fallback."
    )
    tools["record_delegation"][1]["properties"]["luna_fallback"]["description"] = (
        "An unavailable hidden Luna dispatch falls back to an explicit hidden Terra spawn_agent request."
    )
    tools["record_delegation"][1]["properties"]["luna_fallback"]["default"] = "terra"
    authorized = {
        "init_task", "get_task_status", "record_delegation", "prepare_delegation", "prepare_delegations", "confirm_host_spawn", "finalize_attempt", "record_evidence", "execute_verification_command",
        "cortex.question", "publish_worker_question", "list_worker_questions", "answer_worker_question", "get_worker_question_updates",
        "record_gate_outcome", "commit_gate", "resume_task", "update_pipeline", "reassess_pipeline", "acquire_lock", "release_lock",
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
        "continue_orchestration", "read_worker_result", "worker_read_worker_result",
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
    worker_read_worker_result_schema: dict[str, Any] | None = None,
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
        **({"worker_read_worker_result": (read_worker_result, worker_read_worker_result_schema)} if worker_read_worker_result_schema else {}),
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
        name: (
            all_public_tools.get("worker_read_worker_result", all_public_tools[name])
            if selected == "worker" and name == "read_worker_result"
            else all_public_tools[name]
        )
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
    transport_session_id = secrets.token_hex(16)
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
                name = request.get("params", {}).get("name")
                if name not in public_tools:
                    if name in all_public_names:
                        value = _tool_unavailable_receipt(name, normalized_audience)
                        result = {
                            "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}],
                            "structuredContent": value,
                            "isError": True,
                        }
                        # This is a structured routing receipt, not an
                        # unhandled tool exception.  Do not log the request:
                        # the receipt contains all actionable advice and the
                        # transport must not retain caller payloads here.
                        if request_id is not None:
                            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False) + "\n")
                            sys.stdout.flush()
                        continue
                    if name in internal_handlers:
                        raise ValueError("tool_is_internal_use_cortex_orchestration_v4")
                    raise ValueError(f"unknown tool '{name}'")
                arguments = request.get("params", {}).get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
                if name == "start_orchestration" and ("id" not in request or request_id is None):
                    raise ValueError("start_orchestration requires a non-null JSON-RPC id for a mutating transport request")
                if name == "start_orchestration" and request_id is not None:
                    arguments = {
                        **arguments,
                        "_transport_request_id": (
                            f"{transport_session_id}:{_canonical_jsonrpc_request_id(request_id)}"
                        ),
                    }
                # The native host may bind one worker out-of-band at process
                # launch.  Capability-based resolution is deliberately
                # request-scoped so a reused stdio process cannot carry a
                # previous attempt's ContextVar into a corrective dispatch.
                with worker_request():
                    value = public_tools[name][0](arguments)
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}], "structuredContent": value}
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
                    error = {"code": -32602, "message": str(exc)}
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": error}, ensure_ascii=False) + "\n")
                sys.stdout.flush()
