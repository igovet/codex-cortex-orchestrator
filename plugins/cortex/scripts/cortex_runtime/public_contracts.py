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
    "open_task", "read_task", "read_state", "read_scope", "read_outcome",
    "read_continuations", "read_evidence", "read_timeline",
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
        "outcome": _string(minimum=1, description="Exact semantic outcome text returned by the current-state or assignment read."),
        "acceptance": _texts(description="Complete acceptance conditions for this outcome; the property is required and an explicit empty array is valid only when none exist."),
        "constraints": _texts(description="Complete outcome-specific constraints; the property is required and an explicit empty array is valid only when none exist."),
        "verification": _texts(description="Complete verification expectations for this outcome; the property is required and an explicit empty array is valid only when none exist."),
    }, ("outcome", "acceptance", "constraints", "verification"), description="Copy this complete semantic outcome from the current-state read; Cortex resolves it atomically to private ledger identity and rejects zero or ambiguous matches.")
    outcomes = {"type": "array", "description": "Required non-empty complete semantic outcome definitions. Preserve every source requirement and do not substitute attachment references, shorthand, private identifiers, or invented fields.", "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": outcome}
    outcome_names = {
        "type": "array",
        "description": "Required non-empty complete ordered outcome selection. Copy exact unique semantic outcome names from current task evidence; do not paraphrase, duplicate, merge, invent, or pass private identifiers.",
        "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS,
        "items": _string(minimum=1, description="Exact unique outcome name from the current-state read."),
    }
    assignment_report_policy = _string(enum=("none", "active_plan", "latest_for_scope", "all_finalized"), maximum=32, description="Server-side policy for selecting finalized predecessor evidence for this assignment's exact selected outcomes. latest_for_scope is meaningful only here because open_assignment supplies that semantic scope. Cortex resolves private lineage; never add report identifiers or infer unavailable evidence.")
    evidence_report_policy = _string(enum=("none", "active_plan", "all_finalized"), maximum=32, description="Coordinator evidence-read policy. Use all_finalized to inspect finalized worker reports for the task, active_plan only for the latest finalized plan, or none only when an intentionally empty evidence result is required. The terminal coordinator evidence page includes verified human-view links for the selected finalized reports when ready; copy those links byte-for-byte in the relevant user-facing plan, decision, progress, or result message. This read has no caller-supplied outcome scope, so latest_for_scope is not a valid evidence-read policy.")
    state = _string(maximum=64, description="Semantic operation state. For a publication response, `published` is confirmed terminal success: the worker must perform no later tool call, must not repeat or reconcile the mutation, and immediately emits its compact native handoff.")
    replayed = {"type": "boolean", "description": "Whether the identical private mutation was reconciled. False on a new confirmed mutation. Replay is transport reconciliation only after an actually ambiguous result, never a post-success confirmation step."}
    receipt = _closed({"task_ref": task_ref, "state": state, "replayed": replayed}, ("task_ref", "state", "replayed"), description="Task-scoped terminal receipt without internal identity. A new publication receipt with state `published` and replayed false ends worker tool activity immediately.")
    closure_receipt = _closed({
        "task_ref": task_ref,
        "state": state,
        "replayed": replayed,
        "data": _read_data(),
    }, ("task_ref", "state", "replayed", "data"), description="Task closure receipt with verified finalized human-view links for the immediate final response.")
    plan_review_receipt = _closed({
        "task_ref": task_ref,
        "state": state,
        "replayed": replayed,
        "data": _read_data(),
    }, ("task_ref", "state", "replayed", "data"), description="Plan-review opening receipt with the exact verified active-plan Markdown link for the immediate user decision packet.")
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
            "description": "Legal only for purpose=closure_review, when it must contain exactly revise then close and those labels must be rendered to the user in the prompt language. For an ordinary clarification this property must be absent; never pass an empty array.",
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
    }, ("task_ref", "status", "summary", "documentation_impact", "findings", "recommendations", "outcome_coverage", "risks", "unresolved"), description="Flat closed documentation publication. verification_facts is optional; when omitted, Cortex derives one fact per outcome_coverage verification entry from its coverage status, preserving supplied evidence without duplication.")
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
        "read_task": _contract("Worker-only assignment consumption and the only operation that reads or consumes a worker assignment. A fresh worker calls it first with the exact server-rendered worker-scoped task_ref. Omitted or false continuation starts the read; continue=true is valid only after the immediately preceding call on this connection returned has_more=true. The top-level task_ref and has_more values are the sole callable identity and pagination markers. A fully consumed identical read reconciles the same assignment without minting another receipt. The server owns the assignment, evidence policy, and position; never supply a view, report policy, cursor, worker identity, or coordinator task reference.", _closed({
            "task_ref": task_ref,
            "continue": {"type": "boolean", "description": "Optional continuation flag. Use true only after the immediately preceding assignment page with the same task_ref returned has_more=true; otherwise omit or use false."},
        }, ("task_ref",)), _closed({"task_ref": task_ref, "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether continue=true is valid next."}}, ("task_ref", "data", "has_more"))),
        "read_state": _contract("Coordinator-only one-call bounded status summary for choosing the next kind of action. It returns only scalar current revision, coverage, conformance, execution, closure, and assignment-count facts. It never returns the original request, contract text, outcome lists, history, reports, or worker continuations. It is not worker-liveness polling: never call it merely because a bounded native wait timed out or returned no completion while the child remains active. Use it after a completion/attention event, user change, recovery/compaction, or another concrete need for current status.", _closed({
            "task_ref": task_ref,
        }, ("task_ref",)), _closed({"task_ref": task_ref, "data": _read_data()}, ("task_ref", "data"))),
        "read_scope": _contract("Coordinator-only assignment-scope read for one selected responsibility. The first page includes bounded current counts and only that responsibility's exact assignable outcome names plus their current coverage dispositions. A full-scope assignment may omit outcome selection and does not require older scope pages; continue only when an intentional partition needs additional exact names and the immediately preceding call with the same task_ref and responsibility returned has_more=true. This operation never returns contract details, reports, history, or continuations.", _closed({
            "task_ref": task_ref,
            "responsibility": _string(enum=("delivery", "evidence", "planning"), maximum=16, description="Required assignment responsibility whose current server-derived scope is needed."),
            "continue": {"type": "boolean", "description": "Optional continuation flag. Use true only to inspect more exact names for an intentional partition after the immediately preceding matching scope page returned has_more=true."},
        }, ("task_ref", "responsibility")), _closed({"task_ref": task_ref, "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether another older scope page is available."}}, ("task_ref", "data", "has_more"))),
        "read_outcome": _contract("Coordinator-only exact read of one current semantic outcome. Use it only when a point edit must preserve some existing acceptance, constraints, or verification. Supply the exact outcome name returned by read_scope; the result contains that one complete current outcome and nothing else from the task contract. Deletion alone does not require this read because record_steering accepts exact current outcome names.", _closed({
            "task_ref": task_ref,
            "outcome": _string(minimum=1, description="Exact current outcome name copied from read_scope; never paraphrase it."),
        }, ("task_ref", "outcome")), _closed({"task_ref": task_ref, "data": _read_data()}, ("task_ref", "data"))),
        "read_continuations": _contract("Coordinator-only bounded active-continuation read for recovery or lifecycle reconciliation. After recovery state shows unfinished delegated work, call this next; do not substitute read_timeline. It excludes task text, contract details, reports, and history. Continue only when the immediately preceding matching call returned has_more=true.", _closed({
            "task_ref": task_ref,
            "continue": {"type": "boolean", "description": "Optional continuation flag. Use true only after the immediately preceding continuation page with the same task_ref returned has_more=true."},
        }, ("task_ref",)), _closed({"task_ref": task_ref, "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether another continuation page is available."}}, ("task_ref", "data", "has_more"))),
        "read_evidence": _contract("Coordinator-only finalized evidence read. Select the advertised server-side report policy. Omitted or false continuation starts the read; continue=true is valid only after the immediately preceding call with the same task_ref and report_policy returned has_more=true. A terminal page returns verified human-view links for selected finalized plans/reports when ready. Copy every relevant returned link byte-for-byte into the corresponding user-facing message: use the complete markdown_link, including its literal square brackets, readable label, parentheses, and absolute destination. A bare absolute path, reconstructed Markdown, or omitted label is invalid. Never guess a path. The server owns report selection and position.", _closed({
            "task_ref": task_ref,
            "report_policy": evidence_report_policy,
            "continue": {"type": "boolean", "description": "Optional continuation flag. Use true only after the immediately preceding evidence page with the same task_ref and report_policy returned has_more=true; otherwise omit or use false."},
        }, ("task_ref", "report_policy")), _closed({"task_ref": task_ref, "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether continue=true is valid next."}}, ("task_ref", "data", "has_more"))),
        "read_timeline": _contract("Coordinator-only newest-first history for an explicit chronology or audit need. It is not a recovery lookup and must not replace read_continuations. Omitted or false continuation starts newest; continue=true follows only an immediately preceding matching page with has_more=true and moves older. The server owns position. It returns historical event projections without repeating current state.", _closed({
            "task_ref": task_ref,
            "continue": {"type": "boolean", "description": "Optional continuation flag. Use true only after the immediately preceding timeline page with the same task_ref returned has_more=true; otherwise omit or use false."},
        }, ("task_ref",)), _closed({"task_ref": task_ref, "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether continue=true is valid next."}}, ("task_ref", "data", "has_more"))),
        "open_clarification": _contract("Coordinator-only decision opening for one factual clarification whose possible answers leave every current outcome detail unchanged, or for mandatory final-result review. A question known to choose behavior, acceptance, constraints, verification, or scope must use open_steering instead. This call records the hold but does not display its prompt to the user. For ordinary clarification omit the options property entirely; never send an empty array. After success, render the complete localized question in the final answer with established context, safe choices, and material consequence of each. Before close_task, present the verified result and open closure_review with revise then close; never infer or reuse a choice.", clarification_open, receipt),
        "record_clarification": _contract("Coordinator-only records a direct user answer after clarification; preserve exact response and language, and omit outcome for the ordinary form. A choice of behavior not stated exactly in a current outcome is a semantic change: open steering next; do not read assignment scope or create an assignment first. Closure review records revise or close; only close authorizes close_task. Silence or unrelated prose never authorizes closure.", _closed({**answer, "outcome": _string(enum=("revise", "close"), maximum=16, description="Optional only because this flat host-compatible schema represents two server-bound forms. Omit outcome for an ordinary clarification. A pending closure review independently requires revise or close from the user's direct choice; never infer close from completion, silence, or unrelated text.")}, ("task_ref", "response_original", "user_language")), receipt),
        "open_plan_review": _contract("Coordinator-only opening of review after reading the current finalized active plan and choosing to ask the user. This call records the hold but does not display its prompt to the user. After success, its result returns data.human_view with the exact server-provided verified plan link. In the immediate final answer, copy that complete markdown_link byte-for-byte and separately show a localized decision-ready plan summary covering scope, ordered stages, intended changes, verification, stop conditions, and material risks or unresolved items. Preserve the link's literal square brackets, label, parentheses, and destination; a bare path is not a link and is invalid. If the ready link is unexpectedly unavailable to the model, disclose that limitation and provide enough detail inline for an informed decision instead of reconstructing a path. The final answer must present exactly the current plan choices in the user's language: approve it, request its revision, or cancel; a bare 'plan ready' question is invalid. Never render closure-review choices such as revise/close here. This opens one neutral question and does not approve the plan; Cortex derives plan identity and digest, so never supply plan references, handles, digests, or view fields.", decision_open, plan_review_receipt),
        "record_plan_review": _contract("Coordinator-only recording of the direct user decision after a successful plan-review opening. Approval requires explicit approval of the current plan; silence, unrelated text, or an old-plan decision is not approval. Cortex derives and validates the pending plan binding internally.", _closed({**answer, "outcome": _string(enum=("approve", "request_revision", "cancel"), maximum=32, description="Required explicit direct user decision for the pending current-plan review; never infer approval from silence or unrelated text.")}, ("task_ref", "response_original", "user_language", "outcome")), receipt),
        "open_steering": _contract("Coordinator-only decision opening for one task-scope or outcome change. If a question is known to choose previously unstated behavior or determine acceptance, constraints, verification, or scope, open steering before presenting it; the direct answer is the steering answer, so never ask for a second confirmation. Apply this before or after plan review, resume, or compaction. This call does not display its prompt to the user. After success, render the full localized context, choices, and material consequence of each in the final answer; never forward a context-free worker question. Do not apply the change or supply private identity.", decision_open, receipt),
        "record_steering": _contract("Coordinator-only atomic recording of a direct user steering answer after a successful steering opening. No task read is required when retire is empty: an unchanged decision or independent complete additions are bound safely by the pending decision. Retirement requires exact current names observed through read_scope on this same connection; a point replacement that preserves old details first uses read_outcome for that one name. Preserve the exact response, complete new outcomes, and exact names to retire, using empty arrays when unchanged. Independent additions remain independent; one unambiguous retire-plus-add replacement is committed atomically. Cortex resolves private identity internally.", _closed({**answer, "add": {**outcomes, "minItems": 0, "description": "Required complete new semantic outcomes to add; use an explicit empty array when none are added, and never infer additions from prose alone."}, "retire": {"type": "array", "description": "Required exact current outcome names to retire; copy each name from read_scope on this connection, use an explicit empty array when none retire, and never pass complete outcome objects or private identifiers.", "minItems": 0, "maxItems": TASK_CONTRACT_MAX_ITEMS, "uniqueItems": True, "items": _string(minimum=1, description="Exact current outcome name returned by read_scope.")}}, ("task_ref", "response_original", "user_language", "add", "retire")), receipt),
        "open_assignment": _contract("Coordinator-only creates exactly one private worker assignment; it never reads or consumes one, and a fresh worker must use read_task and must never call open_assignment. Before delivery or evidence, read that responsibility's scope. Omit outcomes for complete scope; use an exact non-empty subset only to partition. Planning omits outcomes and binds the current contract. Terminal rework and previously unstated concrete behavior selected by clarification require confirmed steering before every assignment, including planning; scope, goal, or instructions cannot substitute for that contract revision. Instructions are the sole task-specific instruction field. For confirmed loss recovery, omit outcomes when replacing the one complete server-advertised predecessor scope; Cortex derives it atomically. Supply an exact advertised recovery subset only when choosing among multiple recoverable predecessors. After stale-outcome rejection, permit one fresh scope read and one rebuilt request; never replay stale input. The coordinator owns routing, instructions, partitioning, and evidence policy. Never infer loss from timeout, reconnect, copied locator, or report reference. A planner publishes one terminal plan. Spawn a successful non-replayed dispatch exactly once; replay or ambiguity never spawns another worker.", _closed({
            "task_ref": task_ref,
            "role": _string(minimum=1, maximum=ROLE_MAX_LENGTH, description="Human-readable specialist label selected by the coordinator; it grants no coordinator authority, does not change responsibility, and cannot permit publication for another assignment."),
            "profile_name": _string(enum=packaged_profile_names(), maximum=ROLE_MAX_LENGTH, description="Packaged specialist profile."),
            "model": _string(enum=("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"), maximum=64, description="Exact model selected by the LLM coordinator."),
            "reasoning_effort": _string(enum=("low", "medium", "high", "xhigh", "max"), maximum=16, description="Exact reasoning effort selected by the LLM coordinator."),
            "responsibility": _string(enum=("delivery", "evidence", "planning"), maximum=16, description="Required coordinator-selected server-enforced responsibility. Delivery owns selected outcomes, evidence contributes evidence, and planning is restricted to one terminal plan; the role label cannot override it."),
            "goal": _string(minimum=1, description="Complete concrete assignment goal limited to selected semantic outcomes and immutable scope; do not broaden it from repository context, conversation, or another worker's evidence."),
            "scope": _string(minimum=1, description="Complete immutable project and evidence boundary. The worker must remain inside it and cover every selected outcome without omission, merging, invention, or ownership beyond scope."),
            "instructions": _string(minimum=1, description="Complete task-specific worker instructions containing every source-derived requirement, constraint, acceptance condition, verification expectation, stopping boundary, publication responsibility, and prohibition needed by this assignment. This is the sole instruction channel; no supplementary instruction, context, mission, attachment, or custom field exists."),
            "outcomes": {**outcome_names, "description": "Optional intentional partition selector. Omit when one assignment covers the complete current responsibility list: Cortex derives all latest assignment_scope.delivery_outcomes for delivery, all evidence_outcomes for evidence, the complete current contract for planning, and one unique complete predecessor scope for confirmed loss recovery. When partitioning or choosing among multiple recoverable predecessors, copy one non-empty exact subset from the matching latest advertised list. Never use terminal_outcomes for delivery."}, "report_policy": assignment_report_policy,
            "loss_recovery": _closed({
                "state": _string(enum=("blocked", "aborted"), maximum=16, description="Explicit terminal disposition for the confirmed lost nonterminal predecessor."),
                "reason": _string(minimum=1, description="Concrete reason the predecessor cannot continue; absence, silence, timeout, or reconnect alone is insufficient."),
                "evidence": _texts(minimum=1, description="Required non-empty bounded facts proving why the predecessor is irrecoverably blocked or aborted."),
            }, ("state", "reason", "evidence"), description="Optional explicit lost-worker replacement record. Omit for every ordinary assignment."),
        }, ("task_ref", "role", "profile_name", "model", "reasoning_effort", "responsibility", "goal", "scope", "instructions", "report_policy")), _closed({"native_dispatch": native_dispatch, "replayed": replayed}, ("native_dispatch", "replayed"))),
        "publish_plan": _contract("Worker-only atomic terminal plan publication for the current consumed assignment. Publish exactly one complete flat plan in one call with status, verification, full outcome coverage, and required risk and unresolved arrays, empty only when honestly none apply. Cortex derives review disposition from authoritative governance state. A planning assignment may publish only this plan and must stop after success; identical retry reconciles as replay, while changed payload conflicts and requires a new assignment.", plan_publication, receipt),
        "publish_result": _contract("Worker-only atomic terminal result publication for the current consumed non-planning assignment. The complete argument object must fit the advertised compact UTF-8 aggregate byte bound as well as every per-field limit. Construct one complete first call that preserves every required semantic section, exact outcome coverage, verification state, change, documentation conclusion, risk, unresolved item, and status; compact only redundant prose and formatting, never ellipsize, byte-slice, omit, or infer evidence. A root aggregate encoded-size rejection reports numeric actual and maximum bytes plus safe section attribution and permits exactly one materially smaller, schema-complete corrected attempt. An unchanged, incomplete, ellipsized, or still-oversize correction must stop. Publish only this worker's evidence and never a supplementary replacement after success; an identical post-dispatch retry reconciles as replay, while changed published content conflicts and requires a new assignment.", result_publication, receipt),
        "publish_documentation": _contract("Worker-only atomic terminal documentation-impact publication for the consumed non-planning assignment. Publish one complete flat assessment with findings, recommendations, full outcome coverage, documentation impact, risks, unresolved items, and status. verification_facts may add independent observations; when omitted, Cortex derives them without loss from outcome_coverage verification instead of requiring duplicate evidence. Never publish for another assignment or after success; identical retry reconciles, while changed content conflicts.", documentation_publication, receipt),
        "assess_governance": _contract("Coordinator-only advisory assessment exactly after task opening and before the first worker assignment. Hard precondition: open_task must already have succeeded on this exact coordinator connection and returned the task_ref used here. If no such returned value exists in current context, call open_task instead; never supply a placeholder such as invalid. Semantic ownership belongs only to the coordinator. A later reassessment is coordinator-only, requires material risk-changing evidence, and still requires a newly selected explicit advertised depth. Optional rationale and risk notes support but never replace that choice. This is not worker lifecycle, scheduling, or an authorization grant, and native workers, planners, replacements, and rework workers never call it.", _closed({"task_ref": task_ref, "mode": _string(enum=GOVERNANCE_MODES, maximum=16, description="Required explicit coordinator depth selection made before invoking the assessment; choose only an advertised value and never infer it from rationale or risk notes alone."), "rationale": _string(description="Optional supporting assessment rationale; it never replaces the required explicit depth."), "risk_factors": _texts(description="Optional complete supporting risk-factor list; omit when not supplied or use an explicit empty array when the assessment found none.")}, ("task_ref", "mode")), receipt),
        "close_task": _contract("Coordinator-only advisory closure after reconciling intended publications, verification facts, outcome coverage, documentation impact, and unresolved evidence from the ledger. Before every attempt, present that current result to the user, call the advertised open_clarification operation to open the mandatory closure_review, wait for the direct user choice, and call the advertised record_clarification operation to record exactly revise or close. Only a recorded current close choice authorizes this operation. A request made before the current result existed to close automatically afterward is not this required post-result review. Never call this operation as a readiness probe or before opening and recording the current review. Cortex rejects absent, revision-requested, pending, reused-after-work, or stale review evidence. Never infer permission from silence, worker completion, or an earlier review, and never open a new task when the user requests revision. On success, the result repeats verified links for every finalized plan and report. Copy each relevant returned markdown_link byte-for-byte into the immediate final answer; never reconstruct an earlier path from memory.", _closed({"task_ref": task_ref, "verdict": _string(enum=CLOSURE_VERDICTS, maximum=32, description="Required evidence-backed coordinator closure verdict chosen from advertised values; ready does not itself prove that unrun checks passed."), "unresolved_risks": _texts(description="Optional complete unresolved-risk list; omit when not supplied or use an explicit empty array only when none remain."), "follow_ups": _texts(description="Optional complete follow-up list; omit when not supplied or use an explicit empty array when none are needed."), "completion_notes": _texts(description="Optional complete closure notes; omit when not supplied or use an explicit empty array only when none exist.")}, ("task_ref", "verdict")), closure_receipt),
    }
    return {name: contracts[name] for name in V12_TOOL_NAMES}


def public_input_schemas(contracts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    catalogue = build_public_contracts() if contracts is None else contracts
    return {str(name): dict(value["inputSchema"]) for name, value in catalogue.items()}
