"""Authoritative flat task-ref-only Cortex MCP contracts."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cortex_runtime.v12_contract import (
    CLOSURE_VERDICTS, GOVERNANCE_MODES, LANGUAGE_TAG_MAX_LENGTH,
    LANGUAGE_TAG_PATTERN, MCP_OPERATION_MAX_BYTES, PROJECT_ROOT_MAX_LENGTH, REPORT_STATUSES,
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
    required_names = set(required)
    advertised: dict[str, Any] = {}
    for name, schema in properties.items():
        value = dict(schema) if isinstance(schema, Mapping) else schema
        if name in required_names and isinstance(value, dict):
            detail = str(value.get("description", "")).strip()
            value["description"] = "Required property." + (f" {detail}" if detail else "")
        advertised[name] = value
    return {"type": "object", "description": description, "properties": advertised, "required": list(required), "additionalProperties": False}


def _read_data() -> dict[str, Any]:
    return {"description": "Server-produced semantic task data; private ledger identity is removed recursively.", "type": ["object", "array", "string", "number", "integer", "boolean", "null"]}


def _texts(*, minimum: int = 0,
           description: str = "Bounded semantic text values.") -> dict[str, Any]:
    return {"type": "array", "description": description, "minItems": minimum, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": _string()}


def _contract(description: str, inputs: Mapping[str, Any], outputs: Mapping[str, Any]) -> dict[str, Any]:
    required = inputs.get("required")
    required_names = tuple(
        name for name in required
        if isinstance(name, str)
    ) if isinstance(required, list) else ()
    required_clause = (
        " Required properties for this call: "
        + ", ".join(required_names)
        + ". Before invoking, verify every required property is present in one complete request."
    ) if required_names else ""
    exact = (
        description.rstrip()
        + required_clause
        + " Follow the advertised closed input schema exactly: include every required property, "
        "use only advertised properties, and never invent supplementary fields."
    )
    input_schema = dict(inputs)
    # ``maxBytes`` is the runtime's advertised compact UTF-8 JSON bound. It
    # applies to the complete argument object independently from ordinary
    # per-field JSON-Schema limits.
    input_schema["maxBytes"] = MCP_OPERATION_MAX_BYTES
    return {"description": exact, "inputSchema": input_schema, "outputSchema": dict(outputs), "runtimeOutputSchema": dict(outputs)}


def build_public_contracts() -> dict[str, dict[str, Any]]:
    task_ref = _string(minimum=14, maximum=47, pattern=r"^t_[0-9a-f]{12}(?:_[0-9a-f]{32})?$", description="The sole public identifier; copy the exact coordinator or worker-scoped task_ref supplied by Cortex.")
    outcome = _closed({
        "outcome": _string(minimum=1, description="Exact semantic outcome text returned by read_task."),
        "acceptance": _texts(description="Complete acceptance conditions for this outcome; the property is required and an explicit empty array is valid only when none exist."),
        "constraints": _texts(description="Complete outcome-specific constraints; the property is required and an explicit empty array is valid only when none exist."),
        "verification": _texts(description="Complete verification expectations for this outcome; the property is required and an explicit empty array is valid only when none exist."),
    }, ("outcome", "acceptance", "constraints", "verification"), description="Copy this complete semantic outcome from read_task; Cortex resolves it atomically to private ledger identity and rejects zero or ambiguous matches.")
    outcomes = {"type": "array", "description": "Required non-empty complete semantic outcome definitions. Preserve every source requirement and do not substitute attachment references, shorthand, private identifiers, or invented fields.", "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": outcome}
    outcome_names = {
        "type": "array",
        "description": "Required non-empty complete ordered outcome selection. Copy exact unique semantic outcome names from current task evidence; do not paraphrase, duplicate, merge, invent, or pass private identifiers.",
        "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS,
        "items": _string(minimum=1, description="Exact unique outcome name from read_task."),
    }
    assignment_report_policy = _string(enum=("none", "active_plan", "latest_for_scope", "all_finalized"), maximum=32, description="Server-side policy for selecting finalized predecessor evidence for this assignment's exact selected outcomes. latest_for_scope is meaningful only here because open_assignment supplies that semantic scope. Cortex resolves private lineage; never add report identifiers or infer unavailable evidence.")
    evidence_report_policy = _string(enum=("none", "active_plan", "all_finalized"), maximum=32, description="Coordinator evidence-read policy. Use all_finalized to inspect finalized worker reports for the task, active_plan only for the latest finalized plan, or none only when an intentionally empty evidence result is required. This read has no caller-supplied outcome scope, so latest_for_scope is not a valid evidence-read policy.")
    state = _string(maximum=64, description="Semantic operation state. For a publication response, `published` is confirmed terminal success: the worker must perform no later tool call, must not repeat or reconcile the mutation, and immediately emits its compact native handoff.")
    replayed = {"type": "boolean", "description": "Whether the identical private mutation was reconciled. False on a new confirmed mutation. Replay is transport reconciliation only after an actually ambiguous result, never a post-success confirmation step."}
    receipt = _closed({"task_ref": task_ref, "state": state, "replayed": replayed}, ("task_ref", "state", "replayed"), description="Task-scoped terminal receipt without internal identity. A new publication receipt with state `published` and replayed false ends worker tool activity immediately.")
    decision_open = _closed({
        "task_ref": task_ref,
        "prompt": _string(minimum=1, description="Required complete neutral user-facing question. This opens a decision but does not answer, approve, cancel, or apply it."),
        "prompt_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 prompt language."),
    }, ("task_ref", "prompt", "prompt_language"))
    clarification_open = _closed({
        "task_ref": task_ref,
        "prompt": _string(minimum=1, description="Required complete neutral user-facing question. This opens a decision but does not answer, approve, cancel, or apply it."),
        "prompt_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 prompt language."),
        "purpose": _string(enum=("clarification", "closure_review"), maximum=32, description="Optional question purpose; omitted means clarification. Use closure_review explicitly only after presenting the current verified result immediately before possible task closure."),
        "options": {
            "type": "array",
            "description": "Optional semantic choices; omitted ordinary clarification means answer. For closure_review supply exactly revise then close and render those labels to the user in the prompt language.",
            "minItems": 1,
            "maxItems": 2,
            "uniqueItems": True,
            "items": _string(enum=("revise", "close"), maximum=16),
        },
    }, ("task_ref", "prompt", "prompt_language"))
    answer = {
        "task_ref": task_ref,
        "response_original": _string(minimum=1, description="Required non-empty exact direct user response, preserved without translation, paraphrase, inference, or synthetic approval."),
        "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 response language."),
    }
    verification_fact = _closed({
        "state": _string(enum=("executed", "not_run", "failed"), maximum=16, description="Observed verification state."),
        "summary": _string(minimum=1, description="Concise observable verification fact."),
    }, ("state", "summary"), description="One observable verification fact.")
    verification_facts = {"type": "array", "description": "Required non-empty complete observable verification evidence. Record expected checks honestly as executed, not_run, or failed instead of omitting them or claiming unobserved success.", "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": verification_fact}
    coverage = _closed({
        "outcome": _string(minimum=1, description="Exact unique outcome name from the assignment read."),
        "status": _string(enum=("planned", "complete", "partial", "unverified", "blocked"), maximum=16, description="Evidence-backed disposition for this semantic outcome."),
        "verification": _texts(minimum=1),
    }, ("outcome", "status", "verification"), description="One semantic outcome disposition; private outcome identity is resolved by Cortex.")
    outcome_coverage = {"type": "array", "description": "Required complete ordered coverage of the immutable assignment scope: exactly one evidence-backed disposition for every assigned semantic outcome, with no omissions, duplicates, merges, renames, inventions, or private identifiers.", "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": coverage}
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
    publication_header = {
        "task_ref": task_ref,
        # Keep the short terminal discriminator at the front of both the
        # property map and required list. Long evidence arrays may be visually
        # compacted by the host; terminal status must remain visible before a
        # model constructs its first publication call.
        "status": _string(enum=REPORT_STATUSES, maximum=16, description="Required terminal semantic status matching observed evidence; it does not replace coverage, verification facts, risks, unresolved items, or documentation impact."),
        "summary": _string(minimum=1, description="Concise complete terminal evidence summary for this assignment; it never substitutes for any other required publication property."),
    }
    publication_evidence = {
        "verification_facts": verification_facts,
        "outcome_coverage": outcome_coverage,
        "risks": _texts(
            description="Required complete residual-risk list. This property must be present even when empty; use an explicit empty array only after checking the assigned boundary, never to conceal unknown or unresolved risk.",
        ),
        "unresolved": _texts(
            description="Required complete unresolved-item list. This property must be present even when empty; include every unresolved requirement, contradiction, failed or not-run check, scope limitation, and pending decision, and never use an empty array to conceal uncertainty.",
        ),
    }
    plan_publication = _closed({
        **publication_header,
        "scope": _string(minimum=1, description="Bounded plan scope."),
        "stages": {"type": "array", "description": "Required ordered plan stages covering all assigned outcomes, dependencies, ownership boundaries, acceptance, risks, stopping conditions, and observable verification.", "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": stage},
        **publication_evidence,
    }, ("task_ref", "status", "summary", "scope", "stages", "verification_facts", "outcome_coverage", "risks", "unresolved"), description="Flat closed plan publication derived from this connection's assignment. Cortex derives informational versus required review from the task's authoritative governance state; never supply a review-policy field. Every required property must be present; when there are no risks or unresolved items, supply the corresponding empty arrays.")
    result_publication = _closed({
        **publication_header,
        "outcome": _string(minimum=1, description="Observed execution outcome."),
        "documentation_impact": _string(minimum=1, description="Required complete documentation-impact assessment: identify affected documentation and follow-up, or explain from evidence why no update is needed; unavailable inspection is not proof of no impact."),
        "changes": {"type": "array", "description": "Required complete observed change list. Use an explicit empty array for read-only, verification-only, or no-change work; never omit it or use it to hide an unverified change.", "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": change},
        **publication_evidence,
    }, ("task_ref", "status", "summary", "outcome", "documentation_impact", "changes", "verification_facts", "outcome_coverage", "risks", "unresolved"), description="Flat closed result publication derived from this connection's assignment.")
    documentation_publication = _closed({
        **publication_header,
        "documentation_impact": _string(minimum=1, description="Required final documentation-impact conclusion grounded in findings and verification evidence; state affected areas or why no update is needed."),
        "findings": {"type": "array", "description": "Required complete documentation findings; use an explicit empty array when none exist, otherwise include each distinct evidence-backed affected area.", "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": finding},
        "recommendations": _texts(description="Required complete documentation recommendations or follow-ups; use an explicit empty array when none are needed."),
        **publication_evidence,
    }, ("task_ref", "status", "summary", "documentation_impact", "findings", "recommendations", "verification_facts", "outcome_coverage", "risks", "unresolved"), description="Flat closed documentation publication derived from this connection's assignment.")
    native_dispatch = _closed({
        "fork_turns": _string(enum=("none",), maximum=8),
        "task_name": _string(minimum=1, maximum=160, description="Exact native task name."),
        "model": _string(minimum=1, maximum=64, description="Optional native model override."),
        "reasoning_effort": _string(minimum=1, maximum=16, description="Optional native reasoning effort override."),
        "message": _string(minimum=1, maximum=131_072, description="Exact rendered worker message. It is emitted after every short routing discriminator so result compaction cannot hide a required routing field."),
    }, ("fork_turns", "message", "task_name", "reasoning_effort"), description="Exact server-rendered native spawn instruction. The selected effort is always explicit; the model property is omitted only for Luna so the configured default model is used, and is explicit for Terra or Sol. Forward the complete object immediately and exactly once after a successful non-replayed assignment; never omit, rewrite, supplement, select a latest assignment, or reconstruct identity. The host may protect the message as an opaque encrypted transport value before PreToolUse. A replayed assignment must not trigger a second spawn.")

    contracts = {
        "open_task": _contract("Coordinator-only first project execution operation. The required outcomes array is the primary semantic contract and must be supplied completely on the first call. Make exactly one direct MCP call with the complete request; never invoke open_task through programmatic tool calling, exec, a batch, parallel calls, or a speculative partial call. Build the entire closed argument object before calling. Open or exactly reconcile one task before project inspection, governance, decisions, or assignment. An ambiguous transport result permits only an identical direct retry, never a replacement task.", _closed({
            "outcomes": outcomes,
            "project_root": _string(minimum=1, maximum=PROJECT_ROOT_MAX_LENGTH, description="Absolute existing canonical project directory supplied by the host or current workspace. This is the task's project root, not a planned output, package, artifact, or child directory: never append or create path segments for work that will be produced under it."),
            "request_original": _string(minimum=1, description="Exact original user request."),
            "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 request language."),
            "constraints": _texts(minimum=1, description="Required non-empty complete task-wide constraint boundary. When no additional constraint exists, state that explicitly as one semantic list item; an empty array is invalid."),
            "context": _texts(description="Optional bounded factual context needed by fresh workers; omit it when none exists, and never use it as shorthand for missing outcome, acceptance, constraint, or verification requirements."),
        }, ("outcomes", "project_root", "request_original", "user_language", "constraints")), _closed({"task_ref": task_ref, "replayed": replayed}, ("task_ref", "replayed"))),
        "read_task": _contract("Single bounded coordinator or worker read and the only operation that reads or consumes a worker assignment. A fresh worker's first Cortex operation is read_task with its assignment view using the exact server-rendered worker-scoped task_ref; never use open_assignment, which is coordinator-only creation. Coordinator state exposes aggregate_coverage.assignment_scope as the canonical server-derived assignment selector. Omit outcomes when one assignment intentionally covers the complete advertised delivery_outcomes or evidence_outcomes list; Cortex binds that full current list atomically. Supply an exact copied non-empty subset only to partition work intentionally. Planning always omits outcomes. When terminal_rework is steering_revision_required, no delivery rework is valid for terminal_outcomes until an explicitly recorded steering revision produces new delivery_outcomes. Never infer assignability from row order or coverage prose. Omitted or false continuation starts a read; continue only the immediately preceding identical read when its top-level has_more value is true. The top-level task_ref and has_more values are the sole callable identity and pagination markers; data never contains another task_ref, has_more, or cursor marker. A fully consumed identical read is reconciled without minting another receipt. The server owns position, so never invent cursors, offsets, continuation handles, or identity fields.", _closed({
            "task_ref": task_ref,
            "view": _string(enum=("state", "assignment", "evidence"), maximum=16, description="Required semantic view. Fresh workers use assignment first; coordinators use state or evidence, and evidence reads may select a report policy."),
            "continue": {"type": "boolean", "description": "Optional continuation flag. Use true only after the immediately preceding read with the same task_ref, view, and report policy returned has_more=true; otherwise omit or use false."},
            "report_policy": evidence_report_policy,
        }, ("task_ref", "view")), _closed({"task_ref": task_ref, "view": _string(enum=("state", "assignment", "evidence"), maximum=16), "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether continue=true is valid next."}}, ("task_ref", "view", "data", "has_more"))),
        "open_clarification": _contract("Coordinator-only decision opening after choosing to ask one real clarification or the mandatory final result review. Before every close_task attempt, first present the current verified result and open closure_review with exactly the two advertised semantic options rendered in the user's language: revise the current task or close it. Never infer either choice, and never reuse a review after later work or evidence.", clarification_open, receipt),
        "record_clarification": _contract("Coordinator-only recording of a direct user answer after a successful clarification opening. Preserve the exact response and language. The server-owned pending binding identifies an ordinary clarification, so omit outcome for that form. For a pending closure review, record exactly revise or close; revise keeps the same task open for rework, while close is the only decision that can authorize close_task. Silence or unrelated prose never authorizes closure.", _closed({**answer, "outcome": _string(enum=("revise", "close"), maximum=16, description="Optional only because this flat host-compatible schema represents two server-bound forms. Omit outcome for an ordinary clarification. A pending closure review independently requires revise or close from the user's direct choice; never infer close from completion, silence, or unrelated text.")}, ("task_ref", "response_original", "user_language")), receipt),
        "open_plan_review": _contract("Coordinator-only opening of review after reading the current finalized active plan and choosing to ask the user. The user-facing prompt must present exactly the current plan choices in the user's language: approve it, request its revision, or cancel. Never render closure-review choices such as revise/close here. This opens one neutral question and does not approve the plan; Cortex derives plan identity and digest, so never supply plan references, handles, digests, or view fields.", decision_open, receipt),
        "record_plan_review": _contract("Coordinator-only recording of the direct user decision after a successful plan-review opening. Approval requires explicit approval of the current plan; silence, unrelated text, or an old-plan decision is not approval. Cortex derives and validates the pending plan binding internally.", _closed({**answer, "outcome": _string(enum=("approve", "request_revision", "cancel"), maximum=32, description="Required explicit direct user decision for the pending current-plan review; never infer approval from silence or unrelated text.")}, ("task_ref", "response_original", "user_language", "outcome")), receipt),
        "open_steering": _contract("Coordinator-only decision opening after choosing to ask for one task-scope or outcome change. Open the sole pending neutral steering question; do not apply changes yet or supply binding, handle, item, revision, or private identity fields.", decision_open, receipt),
        "record_steering": _contract("Coordinator-only atomic recording of a direct user steering answer after a successful steering opening and a fresh current-state read on the same coordinator connection. Perform that read and this recording as separate direct MCP calls; do not place either operation inside programmatic tool calling or an exec batch. The server invalidates earlier state-read evidence when steering opens and consumes the new evidence on one record attempt. Preserve the exact response and explicitly provide the complete added and retired semantic outcome sets, using empty arrays when unchanged. Independent additions remain independent top-level outcomes; one unambiguous retire-plus-add replacement is committed atomically. Cortex resolves current revisions and private identity internally. A deterministic public-argument rejection permits only one materially corrected request when its bounded path diagnostics and this live schema make the correction unambiguous; never repeat unchanged input or guess semantic identity.", _closed({**answer, "add": {**outcomes, "minItems": 0, "description": "Required complete new semantic outcomes to add; use an explicit empty array when none are added, and never infer additions from prose alone."}, "retire": {**outcomes, "minItems": 0, "description": "Required complete exact current semantic outcomes from read_task to retire; use an explicit empty array when none retire, and never pass item identifiers."}}, ("task_ref", "response_original", "user_language", "add", "retire")), receipt),
        "open_assignment": _contract("Coordinator-only creation of exactly one private worker assignment from the advertised flat fields. This operation never reads or consumes an existing worker assignment: a fresh worker must use read_task with its assignment view and must never call open_assignment. Immediately before the coordinator call, read current task state and use only aggregate_coverage.assignment_scope. Omit outcomes when this assignment intentionally covers the complete current list advertised for its responsibility: Cortex atomically binds all delivery_outcomes for delivery, all evidence_outcomes for evidence, and the complete effective contract for planning. Supply outcomes only to partition delivery or evidence intentionally, as a non-empty subset copied exactly from the matching advertised list. Never copy terminal_outcomes into delivery; terminal_rework=steering_revision_required means an explicit user-confirmed steering revision must create new delivery_outcomes before rework delivery can be assigned. Confirmed loss recovery always requires its complete exact advertised recovery scope. On an explicit stale-current-outcome rejection, at most one fresh-state read and one materially rebuilt complete request are permitted when the intended scope maps unambiguously; never replay the stale request or reconstruct retired identity. The coordinator owns routing, responsibility, scope, complete instructions, intentional delivery/evidence partitioning, and predecessor evidence policy. Goal or instructions never substitute for a requested subset. Instructions are the sole task-specific instruction field; never invent supplementary fields, nested mission objects, or caller-side lineage. For a confirmed lost nonterminal delivery worker only, loss_recovery must explicitly record blocked or aborted state, a concrete reason, and non-empty evidence; Cortex derives the unique predecessor from the exact current outcomes and atomically links the successor. Never infer loss from timeout, reconnect, a copied locator, report reference, or bare assignment reference. A planning assignment may publish only one terminal plan. Forward a successful non-replayed native dispatch immediately and exactly once; a replay or ambiguous mutation must not spawn another worker.", _closed({
            "task_ref": task_ref,
            "role": _string(minimum=1, maximum=ROLE_MAX_LENGTH, description="Human-readable specialist label selected by the coordinator; it grants no coordinator authority, does not change responsibility, and cannot permit publication for another assignment."),
            "profile_name": _string(enum=packaged_profile_names(), maximum=ROLE_MAX_LENGTH, description="Packaged specialist profile."),
            "model": _string(enum=("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"), maximum=64, description="Exact model selected by the LLM coordinator."),
            "reasoning_effort": _string(enum=("low", "medium", "high", "xhigh", "max"), maximum=16, description="Exact reasoning effort selected by the LLM coordinator."),
            "responsibility": _string(enum=("delivery", "evidence", "planning"), maximum=16, description="Required coordinator-selected server-enforced responsibility. Delivery owns selected outcomes, evidence contributes evidence, and planning is restricted to one terminal plan; the role label cannot override it."),
            "goal": _string(minimum=1, description="Complete concrete assignment goal limited to selected semantic outcomes and immutable scope; do not broaden it from repository context, conversation, or another worker's evidence."),
            "scope": _string(minimum=1, description="Complete immutable project and evidence boundary. The worker must remain inside it and cover every selected outcome without omission, merging, invention, or ownership beyond scope."),
            "instructions": _string(minimum=1, description="Complete task-specific worker instructions containing every source-derived requirement, constraint, acceptance condition, verification expectation, stopping boundary, publication responsibility, and prohibition needed by this assignment. This is the sole instruction channel; no supplementary instruction, context, mission, attachment, or custom field exists."),
            "outcomes": {**outcome_names, "description": "Optional intentional partition selector. Omit when one assignment covers the complete current responsibility list: Cortex derives all latest assignment_scope.delivery_outcomes for delivery, all evidence_outcomes for evidence, and the complete current contract for planning. When partitioning, copy one non-empty exact subset from the matching latest advertised list. Never use terminal_outcomes for delivery. Confirmed loss recovery requires the complete exact advertised recovery scope."}, "report_policy": assignment_report_policy,
            "loss_recovery": _closed({
                "state": _string(enum=("blocked", "aborted"), maximum=16, description="Explicit terminal disposition for the confirmed lost nonterminal predecessor."),
                "reason": _string(minimum=1, description="Concrete reason the predecessor cannot continue; absence, silence, timeout, or reconnect alone is insufficient."),
                "evidence": _texts(minimum=1, description="Required non-empty bounded facts proving why the predecessor is irrecoverably blocked or aborted."),
            }, ("state", "reason", "evidence"), description="Optional explicit lost-worker replacement record. Omit for every ordinary assignment."),
        }, ("task_ref", "role", "profile_name", "model", "reasoning_effort", "responsibility", "goal", "scope", "instructions", "report_policy")), _closed({"native_dispatch": native_dispatch, "replayed": replayed}, ("native_dispatch", "replayed"))),
        "publish_plan": _contract("Worker-only atomic terminal plan publication for the current consumed assignment. Publish exactly one complete flat plan in one call with status, verification, full outcome coverage, and required risk and unresolved arrays, empty only when honestly none apply. Cortex derives review disposition from authoritative governance state. A planning assignment may publish only this plan and must stop after success; identical retry reconciles as replay, while changed payload conflicts and requires a new assignment.", plan_publication, receipt),
        "publish_result": _contract("Worker-only atomic terminal result publication for the current consumed non-planning assignment. The complete argument object must fit the advertised compact UTF-8 aggregate byte bound as well as every per-field limit. Construct one complete first call that preserves every required semantic section, exact outcome coverage, verification state, change, documentation conclusion, risk, unresolved item, and status; compact only redundant prose and formatting, never ellipsize, byte-slice, omit, or infer evidence. A root aggregate encoded-size rejection reports numeric actual and maximum bytes plus safe section attribution and permits exactly one materially smaller, schema-complete corrected attempt. An unchanged, incomplete, ellipsized, or still-oversize correction must stop. Publish only this worker's evidence and never a supplementary replacement after success; an identical post-dispatch retry reconciles as replay, while changed published content conflicts and requires a new assignment.", result_publication, receipt),
        "publish_documentation": _contract("Worker-only atomic terminal documentation-impact publication for the current consumed non-planning assignment. Publish exactly one complete flat assessment in one call, using explicit empty findings or recommendations arrays when none apply, plus verification, full outcome coverage, final documentation impact, risks, unresolved items, and status. Never publish for another assignment or add a supplementary publication after success; identical retry reconciles as replay, while changed payload conflicts and requires a new assignment.", documentation_publication, receipt),
        "assess_governance": _contract("Coordinator-only advisory assessment exactly after task opening and before the first worker assignment. Hard precondition: open_task must already have succeeded on this exact coordinator connection and returned the task_ref used here. If no such returned value exists in current context, call open_task instead; never supply a placeholder such as invalid. Semantic ownership belongs only to the coordinator. A later reassessment is coordinator-only, requires material risk-changing evidence, and still requires a newly selected explicit advertised depth. Optional rationale and risk notes support but never replace that choice. This is not worker lifecycle, scheduling, or an authorization grant, and native workers, planners, replacements, and rework workers never call it.", _closed({"task_ref": task_ref, "mode": _string(enum=GOVERNANCE_MODES, maximum=16, description="Required explicit coordinator depth selection made before invoking the assessment; choose only an advertised value and never infer it from rationale or risk notes alone."), "rationale": _string(description="Optional supporting assessment rationale; it never replaces the required explicit depth."), "risk_factors": _texts(description="Optional complete supporting risk-factor list; omit when not supplied or use an explicit empty array when the assessment found none.")}, ("task_ref", "mode")), receipt),
        "close_task": _contract("Coordinator-only advisory closure after reconciling intended publications, verification facts, outcome coverage, documentation impact, and unresolved evidence from the ledger. Before every attempt, present that current result to the user, call the advertised open_clarification operation to open the mandatory closure_review, wait for the direct user choice, and call the advertised record_clarification operation to record exactly revise or close. Only a recorded current close choice authorizes this operation. A request made before the current result existed to close automatically afterward is not this required post-result review. Never call this operation as a readiness probe or before opening and recording the current review. Cortex rejects absent, revision-requested, pending, reused-after-work, or stale review evidence. Never infer permission from silence, worker completion, or an earlier review, and never open a new task when the user requests revision.", _closed({"task_ref": task_ref, "verdict": _string(enum=CLOSURE_VERDICTS, maximum=32, description="Required evidence-backed coordinator closure verdict chosen from advertised values; ready does not itself prove that unrun checks passed."), "unresolved_risks": _texts(description="Optional complete unresolved-risk list; omit when not supplied or use an explicit empty array only when none remain."), "follow_ups": _texts(description="Optional complete follow-up list; omit when not supplied or use an explicit empty array when none are needed."), "completion_notes": _texts(description="Optional complete closure notes; omit when not supplied or use an explicit empty array only when none exist.")}, ("task_ref", "verdict")), receipt),
    }
    return {name: contracts[name] for name in V12_TOOL_NAMES}


def public_input_schemas(contracts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    catalogue = build_public_contracts() if contracts is None else contracts
    return {str(name): dict(value["inputSchema"]) for name, value in catalogue.items()}
