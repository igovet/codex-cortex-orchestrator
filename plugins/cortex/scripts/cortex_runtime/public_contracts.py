"""Authoritative flat task-ref-only Cortex MCP contracts."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cortex_runtime.v12_contract import (
    CLOSURE_VERDICTS, GOVERNANCE_MODES, LANGUAGE_TAG_MAX_LENGTH,
    LANGUAGE_TAG_PATTERN, PROJECT_ROOT_MAX_LENGTH, REPORT_STATUSES,
    ROLE_MAX_LENGTH, TASK_CONTRACT_MAX_ITEMS,
)
from cortex_runtime.worker_message import packaged_profile_names

V12_TOOL_NAMES = (
    "open_task", "read_task",
    "open_clarification", "record_clarification",
    "open_plan_review", "record_plan_review",
    "open_steering", "record_steering",
    "open_assignment",
    "publish_plan", "publish_result", "publish_documentation",
    "assess_governance", "close_task",
)


def _string(*, minimum: int = 0, maximum: int = 65_536,
            pattern: str | None = None, enum: tuple[str, ...] | None = None,
            description: str = "Bounded semantic text.") -> dict[str, Any]:
    value: dict[str, Any] = {"type": "string", "minLength": minimum, "maxLength": maximum, "description": description}
    if pattern is not None:
        value["pattern"] = pattern
    if enum is not None:
        value["enum"] = list(enum)
    return value


def _closed(properties: Mapping[str, Any], required: tuple[str, ...] = (), *, description: str = "Closed semantic object.") -> dict[str, Any]:
    return {"type": "object", "description": description, "properties": dict(properties), "required": list(required), "additionalProperties": False}


def _read_data() -> dict[str, Any]:
    return {"description": "Server-produced semantic task data; private ledger identity is removed recursively.", "type": ["object", "array", "string", "number", "integer", "boolean", "null"]}


def _texts(*, minimum: int = 0) -> dict[str, Any]:
    return {"type": "array", "description": "Bounded semantic text values.", "minItems": minimum, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": _string()}


def _contract(description: str, inputs: Mapping[str, Any], outputs: Mapping[str, Any]) -> dict[str, Any]:
    return {"description": description, "inputSchema": dict(inputs), "outputSchema": dict(outputs), "runtimeOutputSchema": dict(outputs)}


def build_public_contracts() -> dict[str, dict[str, Any]]:
    task_ref = _string(minimum=14, maximum=47, pattern=r"^t_[0-9a-f]{12}(?:_[0-9a-f]{32})?$", description="The sole public identifier; copy the exact coordinator or worker-scoped task_ref supplied by Cortex.")
    outcome = _closed({
        "outcome": _string(minimum=1, description="Exact semantic outcome text returned by read_task."),
        "acceptance": _texts(), "constraints": _texts(), "verification": _texts(),
    }, ("outcome", "acceptance", "constraints", "verification"), description="Copy this complete semantic outcome from read_task; Cortex resolves it atomically to private ledger identity and rejects zero or ambiguous matches.")
    outcomes = {"type": "array", "description": "Exact semantic outcomes.", "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": outcome}
    report_policy = _string(enum=("none", "active_plan", "latest_for_scope", "all_finalized"), maximum=32, description="Server-side semantic evidence selection policy.")
    state = _string(maximum=64, description="Semantic operation state.")
    replayed = {"type": "boolean", "description": "Whether the identical private mutation was reconciled."}
    receipt = _closed({"task_ref": task_ref, "state": state, "replayed": replayed}, ("task_ref", "state"), description="Task-scoped receipt without internal identity.")
    decision_open = _closed({
        "task_ref": task_ref,
        "prompt": _string(minimum=1, description="Neutral user-facing question."),
        "prompt_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 prompt language."),
    }, ("task_ref", "prompt", "prompt_language"))
    answer = {
        "task_ref": task_ref,
        "response_original": _string(description="Exact user response."),
        "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 response language."),
    }
    verification_fact = _closed({
        "state": _string(enum=("executed", "not_run", "failed"), maximum=16, description="Observed verification state."),
        "summary": _string(minimum=1, description="Concise observable verification fact."),
    }, ("state", "summary"), description="One observable verification fact.")
    verification_facts = {"type": "array", "description": "Observable verification evidence.", "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": verification_fact}
    coverage = _closed({
        "outcome": outcome,
        "status": _string(enum=("planned", "complete", "partial", "unverified", "blocked"), maximum=16, description="Evidence-backed disposition for this semantic outcome."),
        "verification": _texts(minimum=1),
    }, ("outcome", "status", "verification"), description="One semantic outcome disposition; private outcome identity is resolved by Cortex.")
    outcome_coverage = {"type": "array", "description": "Complete ordered assignment outcome coverage.", "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": coverage}
    stage = _closed({
        "owner": _string(minimum=1, description="Stage owner role."),
        "work": _texts(minimum=1),
        "verification": _texts(minimum=1),
    }, ("owner", "work", "verification"), description="One concrete plan stage.")
    change = _closed({
        "path": _string(minimum=1, description="Changed project path or bounded surface."),
        "summary": _string(minimum=1, description="Observed change."),
    }, ("path", "summary"), description="One implemented change.")
    finding = _closed({
        "area": _string(minimum=1, description="Documentation area."),
        "summary": _string(minimum=1, description="Documentation finding."),
    }, ("area", "summary"), description="One documentation-impact finding.")
    publication_common = {
        "task_ref": task_ref,
        "summary": _string(minimum=1, description="Concise publication summary."),
        "verification_facts": verification_facts,
        "outcome_coverage": outcome_coverage,
        "risks": _texts(), "unresolved": _texts(),
        "status": _string(enum=REPORT_STATUSES, maximum=16, description="Terminal semantic status."),
    }
    plan_publication = _closed({
        **publication_common,
        "scope": _string(minimum=1, description="Bounded plan scope."),
        "stages": {"type": "array", "description": "Ordered implementation stages.", "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": stage},
    }, ("task_ref", "summary", "scope", "stages", "verification_facts", "outcome_coverage", "risks", "unresolved", "status"), description="Flat closed plan publication derived from this connection's assignment.")
    result_publication = _closed({
        **publication_common,
        "outcome": _string(minimum=1, description="Observed execution outcome."),
        "changes": {"type": "array", "description": "Implemented changes; empty is valid for read-only evidence work.", "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": change},
        "documentation_impact": _string(minimum=1, description="Complete documentation-impact assessment."),
    }, ("task_ref", "summary", "outcome", "changes", "verification_facts", "outcome_coverage", "documentation_impact", "risks", "unresolved", "status"), description="Flat closed result publication derived from this connection's assignment.")
    documentation_publication = _closed({
        **publication_common,
        "findings": {"type": "array", "description": "Documentation findings.", "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": finding},
        "recommendations": _texts(),
        "documentation_impact": _string(minimum=1, description="Final documentation-impact conclusion."),
    }, ("task_ref", "summary", "findings", "recommendations", "verification_facts", "outcome_coverage", "documentation_impact", "risks", "unresolved", "status"), description="Flat closed documentation publication derived from this connection's assignment.")
    native_dispatch = _closed({
        "fork_turns": _string(enum=("none",), maximum=8),
        "message": _string(minimum=1, maximum=131_072, description="Exact rendered worker message."),
        "task_name": _string(minimum=1, maximum=160, description="Exact native task name."),
        "model": _string(minimum=1, maximum=64, description="Optional native model override."),
        "reasoning_effort": _string(minimum=1, maximum=16, description="Optional native reasoning effort override."),
    }, ("fork_turns", "message", "task_name"), description="Forward this native dispatch without reconstructing identity.")

    contracts = {
        "open_task": _contract("Open or exactly reconcile one task. All fields are flat and task_ref is the only returned identifier.", _closed({
            "project_root": _string(minimum=1, maximum=PROJECT_ROOT_MAX_LENGTH, description="Absolute canonical project root."),
            "request_original": _string(minimum=1, description="Exact original user request."),
            "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 request language."),
            "outcomes": outcomes, "constraints": _texts(),
            "context": _texts(),
        }, ("project_root", "request_original", "user_language", "outcomes", "constraints")), _closed({"task_ref": task_ref, "replayed": replayed}, ("task_ref", "replayed"))),
        "read_task": _contract("Single bounded read. A worker's first call is view=assignment. Continue the same read only with continue=true; the server retains the continuation.", _closed({
            "task_ref": task_ref,
            "view": _string(enum=("state", "assignment", "evidence"), maximum=16, description="Requested semantic view."),
            "continue": {"type": "boolean", "description": "Continue the immediately preceding bounded read."},
            "report_policy": report_policy,
        }, ("task_ref", "view")), _closed({"task_ref": task_ref, "view": _string(enum=("state", "assignment", "evidence"), maximum=16), "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether continue=true is valid next."}}, ("task_ref", "view", "data", "has_more"))),
        "open_clarification": _contract("Open the task's sole pending clarification.", decision_open, receipt),
        "record_clarification": _contract("Record the sole pending clarification; its binding is derived internally.", _closed(answer, ("task_ref", "response_original", "user_language")), receipt),
        "open_plan_review": _contract("Open review for the current active plan; the plan relation is derived internally.", decision_open, receipt),
        "record_plan_review": _contract("Record the sole pending plan review; stale relations fail.", _closed({**answer, "outcome": _string(enum=("approve", "request_revision", "cancel"), maximum=32, description="Plan-review answer.")}, ("task_ref", "response_original", "user_language", "outcome")), receipt),
        "open_steering": _contract("Open the task's sole pending steering decision.", decision_open, receipt),
        "record_steering": _contract("Record steering and atomically apply semantic outcome changes.", _closed({**answer, "add": {**outcomes, "minItems": 0, "description": "Outcomes to add."}, "retire": {**outcomes, "minItems": 0, "description": "Exact current outcomes to retire."}}, ("task_ref", "response_original", "user_language")), receipt),
        "open_assignment": _contract("Create one private assignment from flat fields and return only its native dispatch.", _closed({
            "task_ref": task_ref,
            "role": _string(minimum=1, maximum=ROLE_MAX_LENGTH, description="Human-readable worker role."),
            "profile_name": _string(enum=packaged_profile_names(), maximum=ROLE_MAX_LENGTH, description="Packaged specialist profile."),
            "model": _string(enum=("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"), maximum=64, description="Exact model selected by the LLM coordinator."),
            "reasoning_effort": _string(enum=("low", "medium", "high", "xhigh", "max"), maximum=16, description="Exact reasoning effort selected by the LLM coordinator."),
            "responsibility": _string(enum=("delivery", "evidence", "planning"), maximum=16, description="Outcome ownership policy."),
            "goal": _string(minimum=1, description="Concrete worker goal."),
            "scope": _string(minimum=1, description="Worker scope boundary."),
            "instructions": _string(minimum=1, description="Task-specific worker instructions."),
            "outcomes": outcomes, "report_policy": report_policy,
        }, ("task_ref", "role", "profile_name", "model", "reasoning_effort", "responsibility", "goal", "scope", "instructions", "outcomes", "report_policy")), _closed({"native_dispatch": native_dispatch, "replayed": replayed}, ("native_dispatch", "replayed"))),
        "publish_plan": _contract("Worker-only atomic plan publication; lineage is private.", plan_publication, receipt),
        "publish_result": _contract("Worker-only atomic result publication; lineage is private.", result_publication, receipt),
        "publish_documentation": _contract("Worker-only atomic documentation publication; lineage is private.", documentation_publication, receipt),
        "assess_governance": _contract("Record advisory governance depth without a public record identifier.", _closed({"task_ref": task_ref, "mode": _string(enum=GOVERNANCE_MODES, maximum=16, description="Advisory governance mode."), "rationale": _string(description="Assessment rationale."), "risk_factors": _texts()}, ("task_ref", "mode")), receipt),
        "close_task": _contract("Close from ledger-derived evidence and coverage; no evidence identifiers are accepted.", _closed({"task_ref": task_ref, "verdict": _string(enum=CLOSURE_VERDICTS, maximum=32, description="Closure verdict."), "unresolved_risks": _texts(), "follow_ups": _texts(), "completion_notes": _texts()}, ("task_ref", "verdict")), receipt),
    }
    return {name: contracts[name] for name in V12_TOOL_NAMES}


def public_input_schemas(contracts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    catalogue = build_public_contracts() if contracts is None else contracts
    return {str(name): dict(value["inputSchema"]) for name, value in catalogue.items()}
