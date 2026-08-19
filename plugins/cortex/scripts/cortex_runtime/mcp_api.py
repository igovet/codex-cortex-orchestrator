"""Public MCP registry and stdio transport, independent of orchestration policy."""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any


PUBLIC_TOOL_DESCRIPTIONS = {
    "start_orchestration": "Start a Cortex task from the exact user-authored request. Before the single call, every ordinary task needs non-empty task.acceptance_criteria and task.verification grounded in that request or verified authority; ask the user if material intent is missing. Exact knowledge-harvest routes are the sole server-supplied exception. Cortex preserves the intent boundary and returns native dispatches with canonical profile, capability, access, and selection rationale.",
    "continue_orchestration": "Submit compact report_ref receipts for the active wave and receive the next relative wave with canonical profile-selection metadata. Pass the exact task_ref returned by start_orchestration; Cortex never selects a task by project-wide fallback. Never submit an inline worker report body.",
    "manage_orchestration": "Inspect or recover one explicit task, create a linked corrective task for a completed source with intent=follow_up, prune stale tasks, run explicit legacy lifecycle or SQLite health/maintenance actions, surface a worker's durable question as an ordinary detailed chat message, or review a completed plan in chat. Every task-scoped intent requires the exact task_ref returned by a successful lifecycle response. Question and plan-review responses return cortex/chat-interaction/v1: render it completely in the user's language as the final assistant message, visibly name the LLM-recommended response and rationale, end the turn, and wait for the user's next ordinary message. Never call a UI/input/approval/elicitation tool and never infer approval from silence. Record the next message against the same interaction ref before resuming the exact worker or plan. Generic placeholders are rejected. When the response is awaiting_translation, call the returned translation_request exactly; for one question it uses answer plus answer_en, for a batch canonical_answers. Cortex resolves all internal identity.",
    "worker_question": "Worker-only operation: persist one self-contained material question or atomic batch with concrete outcome-based options, finish into resumable idle, then poll its canonical answer after the coordinator resumes the same worker. After recording, return the ref plus a complete decision handoff with context, trade-offs, and recommendation; generic placeholder questions/options are rejected. Caller/schema diagnostics are corrected and retried on the same attempt without consuming its budget; only explicit non-retryable blockers end the worker.",
    "get_report_template": "Worker-only draft operation: create one private task-scoped temporary JSON file already filled with the exact report structure, generated evidence markers, and gate-specific placeholders. Return only draft_ref, draft_path, expiry, and required sections. Caller mistakes are corrected on the same attempt; no final report is persisted and no worker attempt is consumed.",
    "record_report": "Worker-only atomic report operation: pass worker identity and draft_ref after editing the private temporary file, or include a complete replacement or small JSON Merge Patch when the sandbox cannot edit that file. Cortex validates the exact current draft and state, atomically persists it only when valid, and deletes the draft only after success. Invalid drafts remain editable and consume no worker retry budget; do not paste the report into the parent channel.",
    "read_dispatch_briefing": "Worker-only fallback: read exactly the immutable briefing identified by the complete task, attempt, profile, dispatch, and SHA-256 capability tuple from the native bootstrap. Oversized chunk requests are safely bounded; caller/schema diagnostics are corrected and retried on the same attempt, while only explicit integrity or storage blockers end the worker. It cannot list or read any other Cortex state.",
    "read_worker_report": "Read one persisted worker report by report_ref and the exact task_ref from the successful lifecycle response. Oversized chunk requests are safely bounded and caller/schema diagnostics are corrected on the same attempt without consuming its budget. Coordinators omit worker identity; successor workers include their exact attempt_id/profile and may read only refs supplied in their dispatch.",
    "manage_governance": "Manage initiatives, typed dependencies, immutable governance records, active snapshots, constrained exceptions, and coordinator-approved policy-promotion proposals. Every mutation names its initiative/task/record scope; worker proposals cannot approve or activate policy.",
}



def build_public_schemas(
    *,
    agents: Mapping[str, Any],
    report_fields: Sequence[str],
    max_report_items: int,
    max_work_packages: int,
    max_microtasks_per_package: int,
    max_discovery_domains: int,
    question_option_schema: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build the nine public contracts independently of internal handlers."""
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
    V3_REPORT_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "description": f"Strict cortex/report/v1 object with exactly {len(report_fields)} fields. Review and close closure belongs beside this object, never inside it.",
        "properties": {
            "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
            "findings": {"type": "array", "maxItems": max_report_items},
            "questions": {
                "type": "array",
                "maxItems": 0,
                "description": "Final reports must use the durable worker_question lifecycle and therefore contain questions=[].",
            },
            "changed_files": {
                "type": "array",
                "maxItems": max_report_items,
                "items": {"type": "string", "minLength": 1, "maxLength": 500},
                "description": "Safe project-relative paths only; put prose in findings or evidence.",
            },
            "tests": {
                "type": "array",
                "maxItems": max_report_items,
                "description": (
                    "For implementation, QA, specialist checks, review, documentation, and close, each item must contain "
                    "exactly command, cwd, exit_code, and evidence from an executed check."
                ),
                "items": EXECUTED_TEST_SCHEMA,
            },
            "evidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": max_report_items,
                "description": (
                    "Evidence plus every exact generated Predecessor review:, Knowledge reviewed:, Gate acceptance:, "
                    "Gate verification:, and close-level Task acceptance:/Task verification: marker from the briefing."
                ),
            },
            "uncertainty": {"type": "array", "maxItems": max_report_items},
        },
        "required": list(report_fields),
    }
    CLOSURE_FINDING_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "fingerprint": {"type": "string", "minLength": 1},
            "severity": {"type": "string", "enum": ["P0", "P1", "P2", "P3", "info"]},
            "status": {"type": "string", "enum": ["open", "resolved", "waived"]},
            "blocking": {"type": "boolean"},
            "summary": {"type": "string", "minLength": 1},
            "details": {},
            "waiver_reason": {"type": "string"},
            "waived_by": {"type": "string"},
            "waived_at": {"type": "string"},
            "resolved_at": {"type": "string"},
        },
        "required": ["severity", "status", "blocking", "summary"],
    }
    CLOSURE_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "description": "Legacy review/close input alias. New workers use only gate_result; Cortex never persists both.",
        "properties": {
            "decision": {"type": "string", "enum": ["pass", "rework", "fail"]},
            "findings": {"type": "array", "items": CLOSURE_FINDING_SCHEMA},
            "verification": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "executed": {"type": "array", "items": {"type": "string"}},
                    "not_executed": {"type": "array", "items": {"type": "string"}},
                    "required_missing": {"type": "array", "items": {"type": "string"}},
                    "limitations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["executed", "not_executed", "required_missing", "limitations"],
            },
            "workspace": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "modified": {"type": "array", "items": {"type": "string"}},
                    "untracked": {"type": "array", "items": {"type": "string"}},
                    "staged": {"type": "array", "items": {"type": "string"}},
                    "committed": {"type": ["boolean", "string"], "enum": [True, False, "not_required"]},
                },
                "required": ["modified", "untracked", "staged", "committed"],
            },
        },
        "required": ["decision", "findings", "verification", "workspace"],
    }
    GATE_RESULT_SCHEMA = {
        **CLOSURE_SCHEMA,
        "description": (
            "Canonical result envelope for every gate. The legacy closure sibling remains an alias for "
            "review/close during the compatibility window."
        ),
        "properties": {
            **CLOSURE_SCHEMA["properties"],
            "decision": {"type": "string", "enum": ["pass", "rework", "fail", "blocked"]},
            "failure_class": {
                "type": "string",
                "enum": ["product", "infrastructure", "environment", "policy", "worker"],
            },
        },
        "required": ["decision", "failure_class", "findings", "verification", "workspace"],
    }
    PLANNING_STRING_LIST_SCHEMA = {
        "type": "array",
        "minItems": 1,
        "maxItems": 32,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": 1000},
    }
    PLANNING_PATHS_SCHEMA = {
        "type": "array",
        "minItems": 1,
        "maxItems": 50,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1},
    }
    PLANNING_DEPENDENCIES_SCHEMA = {
        "type": "array",
        "maxItems": 32,
        "uniqueItems": True,
        "items": {"type": "string", "maxLength": 80, "pattern": "^[a-z0-9][a-z0-9_-]*$"},
    }
    PLANNING_MICROTASK_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "maxLength": 80, "pattern": "^[a-z0-9][a-z0-9_-]*$"},
            "title": {"type": "string", "minLength": 1, "maxLength": 500},
            "objective": {"type": "string", "minLength": 1, "maxLength": 4000},
            "profile": {"type": "string", "enum": sorted(agents)},
            "allowed_paths": PLANNING_PATHS_SCHEMA,
            "depends_on": PLANNING_DEPENDENCIES_SCHEMA,
            "acceptance_criteria": PLANNING_STRING_LIST_SCHEMA,
            "verification": PLANNING_STRING_LIST_SCHEMA,
        },
        "required": ["id", "title", "objective", "profile", "allowed_paths", "acceptance_criteria", "verification"],
    }
    PLANNING_PACKAGE_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "maxLength": 80, "pattern": "^[a-z0-9][a-z0-9_-]*$"},
            "title": {"type": "string", "minLength": 1, "maxLength": 500},
            "objective": {"type": "string", "minLength": 1, "maxLength": 4000},
            "allowed_paths": PLANNING_PATHS_SCHEMA,
            "depends_on": PLANNING_DEPENDENCIES_SCHEMA,
            "microtasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": max_microtasks_per_package,
                "items": PLANNING_MICROTASK_SCHEMA,
            },
        },
        "required": ["id", "title", "objective", "microtasks"],
    }
    V3_PLANNING_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "overview": {"type": "string", "minLength": 1, "maxLength": 8000},
            "work_packages": {
                "type": "array", "minItems": 1, "maxItems": max_work_packages,
                "description": (
                    "Planner-only task-local work breakdown. Runtime requires each package to have id, title, objective, "
                    "and non-empty microtasks, and writes the validated artifact under .codex/cortex/tasks/<task>/planning/."
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
            "title": {"type": "string", "minLength": 1, "maxLength": 500},
            "objective": {"type": "string", "minLength": 1, "maxLength": 4000},
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
            "overview": {"type": "string", "minLength": 1, "maxLength": 8000},
            "context_files": {
                "type": "array", "maxItems": 50, "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "discovery_domains": {
                "type": "array", "minItems": 1, "maxItems": max_discovery_domains,
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
                "description": (
                    "Canonical phase: scope, plan, discover, architecture, database_architecture, implementation, qa, "
                    "security, performance, accessibility, ux, review, documentation, governance_activation, "
                    "governance_close, or close. Common aliases "
                    "are normalized; build_verification/final_verification map to close. A canonical phase may "
                    "appear in only one wave, though one wave may contain multiple workers for that phase."
                ),
            },
            "profile": {
                "type": "string",
                "enum": sorted(agents),
                "description": "Optional canonical Cortex profile name; omit it to use the phase owner. Accepted convenience aliases are normalized before persistence.",
            },
            "objective": {"type": "string"},
            "strategy": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1000,
                "description": "Optional concise name for the worker approach; Cortex preserves it as rework evidence but never uses it to impose an attempt limit.",
            },
            "paths": {"type": "array", "items": {"type": "string"}},
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
                "items": {"type": "string", "minLength": 1},
                "description": (
                    "Optional exact prerequisite phases whose verified reports this worker must receive. "
                    "Omit to receive every completed predecessor report; use an empty list only when the worker "
                    "is intentionally independent."
                ),
            },
            "model": {"type": "string", "description": "Optional expert override; luna, terra, and sol aliases are accepted."},
            "user_requested_model": {
                "type": "string",
                "description": (
                    "Model explicitly requested by the user; luna, terra, and sol aliases are accepted. "
                    "Non-security Sol is rejected unless it is supplied through this field."
                ),
            },
            "effort": {"type": "string", "description": "Optional expert reasoning-effort override."},
            "visible": {"type": "boolean", "default": False},
            "isolated_checkout": {"type": "boolean", "default": False},
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
            "project_root": {"type": "string", "minLength": 1, "description": "Exact absolute project workspace."},
            "task": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "user_request": {"type": "string", "minLength": 1, "description": "Exact user-authored task text. Do not paraphrase, normalize, or expand it."},
                    "objective": {"type": "string", "minLength": 1, "description": "Deprecated exact mirror of user_request. Omit it; when supplied it must match user_request byte-for-byte after trimming."},
                    "requirements": {"type": "array", "items": {"type": "string"}},
                    "acceptance_criteria": {
                        "type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "string", "minLength": 1},
                        "description": "Required observable outcomes, except harvest routes where Cortex supplies the exhaustive census contract.",
                    },
                    "scope": {"type": "array", "items": {"type": "string"}},
                    "allowed_paths": {"type": "array", "items": {"type": "string"}},
                    "verification": {
                        "type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "string", "minLength": 1},
                        "description": "Required authoritative checks, except harvest routes where Cortex supplies the census checks.",
                    },
                    "budget": {"type": "string"},
                    "pause_conditions": {"type": "array", "items": {"type": "string"}},
                    "plan_approval": {"type": "string", "enum": ["auto", "required"], "description": "Post-plan user review policy. Defaults to required for C2/C3 and auto for C1."},
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
                    "language": {"type": "string"},
                    "complexity": {"type": ["string", "integer"], "description": "Optional C1/C2/C3 or human alias; defaults to C2."},
                    "replan_limit": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Deprecated compatibility field retained in persisted tasks. It is audit metadata, not a lifetime execution cap; evidence-backed public replans are not blocked by it.",
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
            "project_root": {"type": "string", "minLength": 1, "description": "Exact absolute project workspace."},
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
                        "report_ref": {"type": "string", "minLength": 1, "description": "Compact ref returned by the worker's record_report call. Successful public continuation uses this field, never an inline report body."},
                        "dispatch_ref": {"type": "string", "minLength": 1, "description": "Exact dispatch ref returned by Cortex; required only for a non-success result so stale failures cannot target a replacement attempt."},
                        "status": {"type": "string", "description": "Omit for success; human aliases are accepted for non-success."},
                        "reason": {"type": "string", "description": "Required for a non-success result."},
                        "next_strategy": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 1000,
                            "description": "Optional materially different approach when evidence supports it; never required merely to authorize another corrective attempt.",
                        },
                    },
                },
            },
            "future_waves": {"type": "array", "minItems": 1, "items": V3_WAVE_SCHEMA},
            "rework": {
                "type": "boolean",
                "default": False,
                "description": "Optional compatibility hint. Cortex automatically infers rework when future_waves reintroduces a current or completed phase.",
            },
            "reason": {"type": "string"},
        },
        "required": ["project_root", "step", "results"],
    }
    WORKER_RECORD_REPORT_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "description": f"Worker report request. Normal finalization sends draft_ref after editing the private file returned by get_report_template. A complete {len(report_fields)}-field replacement or a small JSON Merge Patch may accompany draft_ref when the worker cannot edit draft_path. Cortex validates and persists in one atomic operation; invalid drafts remain editable.",
        "properties": {
            "project_root": {"type": "string", "minLength": 1, "description": "Exact absolute project_root from this worker's Cortex briefing."},
            "task_id": {"type": "string", "minLength": 1, "description": "Exact task_id from this worker's Cortex briefing; never omit or guess it."},
            "attempt_id": {"type": "string", "minLength": 1, "description": "Exact attempt_id from this worker's Cortex briefing; never substitute a phase or profile."},
            "profile": {"type": "string", "enum": sorted(agents), "description": "Exact canonical profile from this worker's Cortex briefing."},
            "draft_ref": {
                "type": "string",
                "pattern": "^draft-[0-9a-f]{32}$",
                "description": "Short reference for the task-scoped temporary report file created by get_report_template.",
            },
            "patch": {
                "type": "object",
                "description": "Optional RFC 7396 JSON Merge Patch limited to report, scoping, planning, gate_result, and closure. Do not combine with a complete report replacement.",
            },
            "report": V3_REPORT_SCHEMA,
            "gate_result": GATE_RESULT_SCHEMA,
            "closure": CLOSURE_SCHEMA,
            "scoping": V3_SCOPING_SCHEMA,
            "planning": V3_PLANNING_SCHEMA,
        },
        "required": ["project_root", "task_id", "attempt_id", "profile"],
        "anyOf": [
            {"required": ["draft_ref"]},
            {"required": ["report"]},
        ],
    }
    WORKER_GET_REPORT_TEMPLATE_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "project_root": {"type": "string", "minLength": 1},
            "task_id": {"type": "string", "minLength": 1},
            "attempt_id": {"type": "string", "minLength": 1},
            "profile": {"type": "string", "enum": sorted(agents)},
        },
        "required": ["project_root", "task_id", "attempt_id", "profile"],
    }
    WORKER_QUESTION_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "project_root": {"type": "string", "minLength": 1},
            "task_id": {"type": "string", "minLength": 1},
            "attempt_id": {"type": "string", "minLength": 1},
            "profile": {"type": "string", "enum": sorted(agents)},
            "action": {"type": "string", "enum": ["ask", "poll", "ask_batch", "poll_batch"]},
            "question_ref": {"type": "string", "description": "Exact ref returned by ask; required for poll."},
            "batch_ref": {"type": "string", "description": "Exact ref returned by ask_batch; required for poll_batch."},
            "question": {"type": "string", "minLength": 1, "description": "Material user decision; required for ask."},
            "header": {"type": "string"},
            "options": {"type": "array", "maxItems": 32, "items": question_option_schema},
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
                        "type": "array", "minItems": 1, "maxItems": 32,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "question_key": {"type": "string", "minLength": 1},
                                "question": {"type": "string", "minLength": 1},
                                "type": {"type": "string", "enum": ["single_select", "multi_select", "text"]},
                                "header": {"type": "string"},
                                "options": {"type": "array", "maxItems": 32, "items": question_option_schema},
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
        "required": ["project_root", "task_id", "attempt_id", "profile", "action"],
    }
    READ_WORKER_REPORT_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "project_root": {"type": "string", "minLength": 1},
            "task_ref": {"type": "string", "description": "Exact opaque task reference; required for every report read."},
            "report_ref": {"type": "string", "minLength": 1},
            "attempt_id": {"type": "string", "minLength": 1, "description": "Successor workers copy the exact attempt id from their dispatch; coordinators omit it."},
            "profile": {"type": "string", "enum": sorted(agents), "description": "Successor workers copy the exact profile from their dispatch; coordinators omit it."},
            "cursor": {"type": "string", "description": "Opaque cursor returned for a large scoped report. It is bound to the report digest, task, and reader scope."},
            "max_bytes": {"type": "integer", "minimum": 1, "description": "Requested UTF-8 report-part size. Values above the safe 32768-byte transport bound are normalized to 32768 and continued with next_cursor."},
        },
        "required": ["project_root", "report_ref"],
    }
    READ_DISPATCH_BRIEFING_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "project_root": {"type": "string", "minLength": 1},
            "task_id": {"type": "string", "minLength": 1},
            "attempt_id": {"type": "string", "minLength": 1},
            "profile": {"type": "string", "enum": sorted(agents)},
            "dispatch_ref": {"type": "string", "minLength": 1},
            "briefing_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "cursor": {"type": "string", "description": "Opaque continuation cursor for the same large immutable briefing; task, worker identity, dispatch and digest remain required on every call."},
            "max_bytes": {"type": "integer", "minimum": 1, "description": "Requested UTF-8 briefing-part size. Values above the safe 32768-byte transport bound are normalized to 32768 and continued with next_cursor."},
        },
        "required": [
            "project_root", "task_id", "attempt_id", "profile", "dispatch_ref", "briefing_digest",
        ],
    }
    MANAGE_ORCHESTRATION_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "project_root": {"type": "string", "minLength": 1, "description": "Exact absolute project workspace."},
            "intent": {"type": "string", "description": "Recovery or maintenance intent such as inspect, resume, deactivate, follow_up, artifacts, lane, resource, question, prune, legacy, or maintenance; common aliases are normalized."},
            "task_ref": {"type": "string", "description": "Exact opaque task reference required for every task-scoped intent. Only prune, legacy, and maintenance omit it."},
            "reason": {"type": "string"},
            "payload": {
                "type": "object",
                "description": (
                    "Rare-operation payload. For intent=plan_approval, decision=prompt returns a detailed "
                    "cortex/chat-interaction/v1 plan summary with approve, revise, and cancel meanings plus an explicit "
                    "LLM recommendation. Render it as the final ordinary chat message and end the turn. The user's next "
                    "message must be submitted with the exact request_id; preserve revision feedback verbatim. For intent=follow_up, use the completed source task_ref and an exact "
                    "corrective user_request; optional report_refs select source report context. For intent=question normal usage is exactly "
                    "{question_ref: '<worker ref>'} plus optional localized display labels. Cortex returns one detailed "
                    "ordinary-chat interaction containing context, consequences, options, and the mandatory LLM recommendation. For batch localization, "
                    "each ordered item uses localized_question, localized_header, localized_options, and optional "
                    "localized_custom_label; question/header/options/custom_label remain compatibility aliases. Every question "
                    "and option must be self-contained and outcome-specific; generic numbered or recommended/alternative "
                    "placeholders are rejected. If Cortex returns "
                    "awaiting_translation, submit its translation_request unchanged except for the English translation: "
                    "a single question uses {question_ref, answer, answer_en}; a batch uses {question_ref, canonical_answers}. "
                    "Cortex resolves task/principal/thread and never opens nested UI. For intent=resume after an exhausted closure-rework cycle, payload.future_waves must begin with a Planner recovery wave; Cortex infers rework and records the replacement before dispatch. Never add guessed identity fields. Artifacts accepts a bounded list, metadata, or read "
                    "action and opaque cursors; it never returns all bodies together. Prune requires confirmation='PRUNE' "
                    "and accepts older_than_days (default 7). Legacy accepts action=inventory|archive|delete; delete "
                    "requires the exact archive-specific confirmation returned by archive. Maintenance accepts action=health|checkpoint|backup|verify_backup_restore|optimize|vacuum|reconcile_projections. Every mutating maintenance action requires its exact action-specific confirmation; backup targets use only safe backup_name values. Normal wave progression never uses this field."
                ),
            },
        },
        "required": ["project_root"],
    }
    MANAGE_GOVERNANCE_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "description": "Dedicated governance surface for initiatives, dependency graph integrity, append-only records, snapshots, exceptions, and approval-only promotion proposals.",
        "properties": {
            "project_root": {"type": "string", "minLength": 1, "description": "Exact absolute project workspace."},
            "action": {"type": "string", "minLength": 1, "description": "Explicit governance action such as create_initiative, add_dependency, create_record, snapshot, or approve_promotion."},
            "principal": {"type": "string", "minLength": 1, "description": "Optional server-bound coordinator principal; when omitted, Cortex derives it from the capability."},
            "thread_id": {"type": "string", "minLength": 1, "description": "Optional server-bound coordinator thread/session identity; provide it together with principal or omit both."},
            "coordinator_capability": {"type": "string", "pattern": "^[0-9a-f]{64}$", "description": "Opaque one-response server-issued capability returned by the original successful start_orchestration call. Only its SHA-256 digest is durable; retries do not reissue it. Never persist it or include it in worker briefings."},
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
        "required": ["project_root", "action", "coordinator_capability"],
    }

    return {
        "v3_report": V3_REPORT_SCHEMA,
        "v3_scoping": V3_SCOPING_SCHEMA,
        "v3_planning": V3_PLANNING_SCHEMA,
        "v3_worker": V3_WORKER_SCHEMA,
        "v3_wave": V3_WAVE_SCHEMA,
        "start_orchestration": START_ORCHESTRATION_SCHEMA,
        "continue_orchestration": CONTINUE_ORCHESTRATION_SCHEMA,
        "manage_orchestration": MANAGE_ORCHESTRATION_SCHEMA,
        "manage_governance": MANAGE_GOVERNANCE_SCHEMA,
        "worker_question": WORKER_QUESTION_SCHEMA,
        "get_report_template": WORKER_GET_REPORT_TEMPLATE_SCHEMA,
        "record_report": WORKER_RECORD_REPORT_SCHEMA,
        "read_dispatch_briefing": READ_DISPATCH_BRIEFING_SCHEMA,
        "read_worker_report": READ_WORKER_REPORT_SCHEMA,
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
    wave_label = str(old.get("wave_id") or "")
    wave_match = re.search(r"(\d+)$", wave_label)
    step = int(wave_match.group(1)) if wave_match else None
    if not old.get("ok"):
        diagnostics = old.get("diagnostics") if isinstance(old.get("diagnostics"), list) else []
        operation = str(old.get("operation") or "")
        retry_tool = "start_orchestration" if operation == "start" else "continue_orchestration"
        response = {
            "schema": public_schema,
            "ok": False,
            "outcome": old.get("state", "needs_input"),
            "code": old.get("code", "orchestration_failed"),
            "step": step,
            "diagnostics": diagnostics,
            "dispatches": [],
            "recoverable": bool(old.get("recoverable", True)),
            "next_action": f"{coordinator_lock} Correct every diagnostic and retry {retry_tool} without touching the target project.",
        }
        if old.get("code") == "plan_reapproval_required":
            response["outcome"] = "plan_reapproval_required"
            response["next_action"] = f"{coordinator_lock} {old.get('next_action')}"
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
    outcome = old.get("state")
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
            f"{coordinator_lock}{start_transition} NEXT REQUIRED ACTION: FIRST, with close_agent when available, close "
            "every known completed child whose durable report was read or whose exact failed result Cortex already "
            "accepted; never close a running or question-paused child. If recovery may have missed a terminal child, "
            "use list_agents defensively and apply the same eligibility rule. THEN call "
            "every dispatch.call once with its exact dispatch.arguments in one model turn when the host supports "
            "parallel tool calls. Exact task_name and dispatch identity bind out-of-order SubagentStart events; "
            "ordinal correlation is forbidden. "
            "A worker is dispatched only after that native call returns a child id. Never claim "
            "it was sent or call wait without the returned child target; if the native call is unavailable or fails, "
            "stop and report the blocker. Keep the returned child targets, then remain idle and wait only for them. Do not repeat a "
            "completed lifecycle call while dispatching. Each worker publishes through record_report and returns only "
            "a report_ref plus a short summary. After the native worker completes, read every ref with "
            "read_worker_report. Only publication_required=true authorizes one main-chat link, and that same message "
            "must briefly explain completion_update.summary and what happens next; rereads never republish. After the durable report was read and no "
            "question or follow-up remains, close that exact completed native child with close_agent when available; "
            "the Cortex report remains authoritative after native cleanup. Reassess the pipeline, then call "
            f"continue_orchestration with task_ref={task_ref}, the report_ref values, and this step."
        )
    elif outcome == "awaiting_plan_approval":
        next_action = (
            f"{coordinator_lock} Read plan_review.report_ref with read_worker_report after the Planner completes. "
            "Publish its link only if publication_required=true and include the completion summary and next step in "
            "that same main-chat message; never republish on reread. Then call manage_orchestration with "
            "intent=plan_approval and payload.decision=prompt. Render its chat_interaction completely as one final "
            "ordinary user-language message: objective, work packages, paths, dependencies, verification, risks, "
            "remaining phases, all approve/revise/cancel meanings, and the visibly labelled LLM recommendation with "
            "rationale. Do not call a UI/input/approval/elicitation tool. End the turn and wait. Submit the user's "
            "next unambiguous response with the exact request_id; preserve requested changes verbatim."
        )
    elif outcome == "completed":
        next_action = f"{coordinator_lock} Orchestration is complete; use the verified handoff without additional project operations."
    elif outcome == "blocked":
        next_action = f"{coordinator_lock} Resolve the blocker without direct project work, then use manage_orchestration with intent resume."
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
        stopped_report_refs = [
            str(report_ref)
            for item in stopped_workers
            for report_ref in item.get("report_refs", [])
            if str(report_ref or "").strip()
        ]
        resumable_workers = [
            item for item in stopped_workers
            if item.get("resumable") and str(item.get("host_agent_id") or "").strip()
        ]
        terminal_failures = [
            item for item in stopped_workers
            if item.get("failure_status") == "failed"
            and str(item.get("dispatch_ref") or "").strip()
        ]
        if outcome == "waiting_workers":
            if active_worker_ids:
                terminal_failure_targets = "; ".join(
                    f"dispatch_ref={item['dispatch_ref']!r}, status='failed', reason={item['failure_reason']!r}"
                    for item in terminal_failures
                )
                stopped_report_clause = (
                    " Also read and publish these persisted report refs before continuing: "
                    + ", ".join(stopped_report_refs)
                    + "."
                    if stopped_report_refs
                    else ""
                )
                failed_result_clause = (
                    " Include exactly one failed result for each stopped slot when you continue: "
                    + terminal_failure_targets
                    + "."
                    if terminal_failure_targets
                    else ""
                )
                next_action = (
                    f"{coordinator_lock} Rehydrate from result.context_handoff. Do not restart, replay, or respawn "
                    "the running attempts. Wait only on these exact persisted native child ids: "
                    + ", ".join(active_worker_ids)
                    + ". After completion, read and validate their report refs before continuing Cortex."
                    + stopped_report_clause
                    + failed_result_clause
                )
            elif any(item.get("question_refs") for item in stopped_workers):
                waiting_questions = [
                    str(question_ref)
                    for item in stopped_workers
                    for question_ref in item.get("question_refs", [])
                    if str(question_ref or "").strip()
                ]
                next_action = (
                    f"{coordinator_lock} The worker is paused on a durable question, not running. Never wait on or "
                    "respawn it. First publish the worker's complete decision handoff as a detailed user-language "
                    "commentary preamble, then surface the self-contained question through "
                    "manage_orchestration(intent=question): "
                    + ", ".join(waiting_questions) + "."
                )
            elif stopped_workers and all(item.get("report_refs") for item in stopped_workers):
                next_action = (
                    f"{coordinator_lock} Recovery found stopped workers with persisted reports, not running "
                    "children. Never wait on or respawn them. Read and publish these report refs, then call "
                    "continue_orchestration for the current step: " + ", ".join(stopped_report_refs) + "."
                )
            elif terminal_failures:
                failure_targets = "; ".join(
                    f"dispatch_ref={item['dispatch_ref']!r}, status='failed', reason={item['failure_reason']!r}"
                    for item in terminal_failures
                )
                report_clause = (
                    " Read and publish these persisted report refs before continuing: "
                    + ", ".join(stopped_report_refs)
                    + "."
                    if stopped_report_refs
                    else ""
                )
                next_action = (
                    f"{coordinator_lock} Recovery found a terminal stopped worker without a report. Never wait on, "
                    "follow up, or respawn the stopped child. Submit exactly one failed result for the current "
                    "step using: " + failure_targets + "; Cortex will continue unbounded corrective rework while "
                    "raising reasoning effort after repeated failures."
                    + report_clause
                )
            elif resumable_workers:
                resume_targets = [str(item["host_agent_id"]) for item in resumable_workers]
                next_action = (
                    f"{coordinator_lock} Recovery found a stopped but addressable worker. Do not spawn a replacement "
                    "and do not wait on the stopped child. Resume the exact persisted worker with followup_task "
                    "targeting: " + ", ".join(resume_targets) + "."
                )
            else:
                next_action = (
                    f"{coordinator_lock} Recovery found a running attempt without a persisted native child id. "
                    "Fail closed: do not respawn, do not call an empty wait, and report the host-binding blocker."
                )
        elif stopped_workers:
            waiting_questions = [
                str(question_ref)
                for item in stopped_workers
                for question_ref in item.get("question_refs", [])
                if str(question_ref or "").strip()
            ]
            if waiting_questions:
                next_action = (
                    f"{coordinator_lock} Never wait on or respawn the paused worker. First publish the worker's "
                    "complete decision handoff as a detailed user-language commentary preamble, then surface and "
                    "answer the durable question through manage_orchestration(intent=question): "
                    + ", ".join(waiting_questions)
                    + ". Then resume the same persisted host_agent_id with followup_task."
                )
            elif all(item.get("report_refs") for item in stopped_workers):
                next_action = (
                    f"{coordinator_lock} Never wait on or respawn the stopped worker. Read and publish these "
                    "persisted report refs, then continue the current step: " + ", ".join(stopped_report_refs) + "."
                )
            elif terminal_failures:
                failure_targets = "; ".join(
                    f"dispatch_ref={item['dispatch_ref']!r}, status='failed', reason={item['failure_reason']!r}"
                    for item in terminal_failures
                )
                report_clause = (
                    " Read and publish these persisted report refs before continuing: "
                    + ", ".join(stopped_report_refs)
                    + "."
                    if stopped_report_refs
                    else ""
                )
                next_action = (
                    f"{coordinator_lock} Recovery found a terminal stopped worker without a report. Never wait on, "
                    "follow up, or respawn the stopped child. Submit exactly one failed result for the current "
                    "step using: " + failure_targets + "; Cortex will continue unbounded corrective rework while "
                    "raising reasoning effort after repeated failures."
                    + report_clause
                )
            elif resumable_workers:
                resume_targets = [str(item["host_agent_id"]) for item in resumable_workers]
                next_action = (
                    f"{coordinator_lock} The native worker stopped before recording a report but remains addressable. "
                    "Do not mark the attempt failed and do not spawn a replacement. Resume it with followup_task "
                    "using the exact persisted target: " + ", ".join(resume_targets) + "."
                )
            else:
                next_action = (
                    f"{coordinator_lock} Recovery found stopped worker state without a report or an addressable host "
                    "identity. Fail closed: do not respawn or fabricate a failed receipt; report the host-binding blocker."
                )
        else:
            next_action = (
                f"{coordinator_lock} Rehydrate from result.context_handoff before continuing. "
                "It is the durable post-compaction state and protocol snapshot; do not restart the task or replay "
                "completed dispatches. Then " + next_action
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
    if outcome == "waiting_workers":
        response.update({
            "output_policy": "silent",
            "allowed_visible_events": [
                "user_message", "worker_question", "worker_completed", "worker_failed", "blocking_error",
            ],
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
    if outcome == "awaiting_plan_approval":
        review = (old.get("result") or {}).get("plan_review") if isinstance(old.get("result"), dict) else None
        if isinstance(review, dict):
            response["plan_review"] = review
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
        "init_task", "get_task_status", "record_delegation", "prepare_delegation", "prepare_delegations", "confirm_host_spawn", "finalize_attempt", "complete_attempt", "record_evidence", "execute_verification_command",
        "record_report", "cortex.question", "publish_worker_question", "list_worker_questions", "answer_worker_question", "get_worker_question_updates",
        "list_task_reports", "get_delegation_reports", "reconcile_report_bus", "close_audit",
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
    for _, schema in tools.values():
        schema.setdefault("properties", {}).setdefault("project_root", {
            "type": "string",
            "minLength": 1,
            "description": "Absolute project workspace path. Cortex writes only to project_root/.codex/cortex.",
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
    get_report_template: Callable[..., Any],
    get_report_template_schema: dict[str, Any],
    record_report: Callable[..., Any],
    record_report_schema: dict[str, Any],
    read_dispatch_briefing: Callable[..., Any],
    read_dispatch_briefing_schema: dict[str, Any],
    read_worker_report: Callable[..., Any],
    read_worker_report_schema: dict[str, Any],
    manage_governance: Callable[..., Any],
    manage_governance_schema: dict[str, Any],
) -> dict[str, tuple[Callable[..., Any], dict[str, Any]]]:
    """Return the only nine MCP operations exposed to hosts and workers."""
    return {
        "start_orchestration": internal_handlers["start_orchestration"],
        "continue_orchestration": internal_handlers["continue_orchestration"],
        "manage_orchestration": internal_handlers["manage_orchestration"],
        "manage_governance": (manage_governance, manage_governance_schema),
        "worker_question": (worker_question, worker_question_schema),
        "get_report_template": (get_report_template, get_report_template_schema),
        "record_report": (record_report, record_report_schema),
        "read_dispatch_briefing": (read_dispatch_briefing, read_dispatch_briefing_schema),
        "read_worker_report": (read_worker_report, read_worker_report_schema),
    }


def serve_stdio(
    *,
    public_tools: Mapping[str, tuple[Callable[[dict[str, Any]], dict[str, Any]], dict[str, Any]]],
    internal_handlers: Mapping[str, tuple[Callable[..., Any], dict[str, Any]]],
    server_version: str,
    instructions: str,
    log_tool_error: Callable[[object, object, str, Exception], None],
) -> None:
    """Run the narrow JSON-RPC transport without importing orchestration internals."""
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
                    if name in internal_handlers:
                        raise ValueError("tool_is_internal_use_cortex_orchestration_v4")
                    raise ValueError(f"unknown tool '{name}'")
                arguments = request.get("params", {}).get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
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
            log_tool_error(request, request_id, line.rstrip("\n"), exc)
            if request_id is not None:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}}, ensure_ascii=False) + "\n")
                sys.stdout.flush()
