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
    value: dict[str, Any] = {"type": "string", "minLength": minimum, "maxLength": maximum}
    if description != "Bounded semantic text.":
        value["description"] = description
    if pattern is not None:
        value["pattern"] = pattern
    if enum is not None:
        value["enum"] = list(enum)
    return value


def _closed(properties: Mapping[str, Any], required: tuple[str, ...] = (), *, description: str = "Closed semantic object.") -> dict[str, Any]:
    value = {"type": "object", "properties": dict(properties), "required": list(required), "additionalProperties": False}
    if description != "Closed semantic object.":
        value["description"] = description
    return value


def _read_data() -> dict[str, Any]:
    return {"description": "Server-produced semantic task data; private ledger identity is removed recursively.", "type": ["object", "array", "string", "number", "integer", "boolean", "null"]}


def _texts(*, minimum: int = 0,
           description: str = "Bounded semantic text values.") -> dict[str, Any]:
    value = {"type": "array", "minItems": minimum, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": _string()}
    if description != "Bounded semantic text values.":
        value["description"] = description
    return value


def _contract(description: str, inputs: Mapping[str, Any], outputs: Mapping[str, Any]) -> dict[str, Any]:
    # Render, never independently maintain, the root required-property list.
    # Some native hosts render schema requiredness separately from the tool
    # description; this keeps the complete first-call checklist visible in both
    # views without introducing a second contract or changing validation.
    required = ", ".join(inputs.get("required", ()))
    exact = description.rstrip() + (f" Required properties: {required}." if required else "")
    input_schema = dict(inputs)
    # ``maxBytes`` is the runtime's advertised compact UTF-8 JSON bound. It
    # applies to the complete argument object independently from ordinary
    # per-field JSON-Schema limits.
    input_schema["maxBytes"] = MCP_OPERATION_MAX_BYTES
    return {"description": exact, "inputSchema": input_schema, "outputSchema": dict(outputs), "runtimeOutputSchema": dict(outputs)}


def semantic_outcome_schema() -> dict[str, Any]:
    """One closed outcome definition for task and proposed-contract schemas."""
    return _closed({
        "outcome": _string(minimum=1, description="Unique semantic outcome name; preserve an existing name exactly unless explicitly replacing it."),
        "acceptance": _texts(description="Complete acceptance conditions; empty only when none apply."),
        "constraints": _texts(description="Complete outcome-specific constraints; empty only when none apply."),
        "verification": _texts(description="Complete verification expectations; empty only when none apply."),
    }, ("outcome", "acceptance", "constraints", "verification"), description="One complete original or proposed outcome. Preserve every source requirement; never merge missing replacement details from retired outcomes.")


def build_public_contracts() -> dict[str, dict[str, Any]]:
    task_ref = _string(minimum=14, maximum=47, pattern=r"^t_[0-9a-f]{12}(?:_[0-9a-f]{32})?$", description="Exact server-issued task/worker ref; never infer.")
    outcome = semantic_outcome_schema()
    outcomes = {"type": "array", "description": "Required non-empty complete semantic outcome definitions. Preserve every source requirement and do not substitute attachment references, shorthand, private identifiers, or invented fields.", "minItems": 1, "maxItems": TASK_CONTRACT_MAX_ITEMS, "items": outcome}
    evidence_report_policy = _string(enum=("none", "active_plan", "all_finalized"), maximum=32, description="all_finalized: finalized task reports; active_plan: current finalized plan; none: intentionally empty evidence. No caller-supplied outcome scope or private report identity.")
    state = _string(maximum=64, description="Semantic operation state. For a publication response, `published` is confirmed terminal success: the worker must perform no later tool call, must not repeat or reconcile the mutation, and immediately emits its compact native handoff.")
    replayed = {"type": "boolean", "description": "Whether the identical private mutation was reconciled. False on a new confirmed mutation. Replay is transport reconciliation only after an actually ambiguous result, never a post-success confirmation step."}
    receipt = _closed({"task_ref": task_ref, "state": state, "replayed": replayed}, ("task_ref", "state", "replayed"), description="Task-scoped terminal receipt without internal identity. A new publication receipt with state `published` and replayed false ends worker tool activity immediately.")
    publication_receipt = _closed({
        "task_ref": task_ref,
        "state": _string(enum=("published", "superseded", "snapshot_conflict"), maximum=32,
            description="Published confirms a terminal report. Superseded and snapshot_conflict confirm no report and no consumed publication slot. All three states end worker activity; do not retry or continue project work after a non-publication result."),
        "published": {"type": "boolean", "description": "True only when terminal evidence was durably published."},
        "replayed": replayed,
    }, ("task_ref", "state", "published", "replayed"))
    publication_receipt["oneOf"] = [
        {"properties": {"state": {"const": "published"}, "published": {"const": True}}},
        {"properties": {"state": {"enum": ["superseded", "snapshot_conflict"]}, "published": {"const": False}, "replayed": {"const": False}}},
    ]
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
    from cortex_runtime.typed_publications import report_schema

    def publication_input(kind: str) -> dict[str, Any]:
        body = report_schema(kind)
        return _closed({"task_ref": task_ref, **body["properties"]},
            ("task_ref", *body["required"]))
    plan_publication = publication_input("plan")
    result_publication = publication_input("result")
    documentation_publication = publication_input("documentation")
    native_dispatch = _closed({
        "fork_turns": _string(enum=("none",), maximum=8),
        "task_name": _string(minimum=1, maximum=160, description="Exact native task name."),
        "model": _string(minimum=1, maximum=64, description="Explicit Terra or Sol native override. Absent for Luna, which uses the configured default subagent model."),
        "reasoning_effort": _string(minimum=1, maximum=16, description="Required explicit native reasoning effort, never above max."),
        "message": _string(minimum=1, maximum=131_072, description="Exact rendered worker message. It is emitted after every short routing discriminator so result compaction cannot hide a required routing field."),
    }, ("fork_turns", "message", "task_name", "reasoning_effort"), description="Exact server-rendered zero-history native spawn instruction. Effort is explicit. Luna uses the configured default model and therefore omits model; Terra and Sol include an explicit override. Forward the complete object immediately and exactly once after a successful non-replayed assignment; never omit, rewrite, supplement, select a latest assignment, or reconstruct identity. The host may protect the message as an opaque encrypted transport value before PreToolUse. A replayed assignment must not trigger a second spawn.")

    contracts = {
        "open_task": _contract("Coordinator-only first project operation. Supply the complete semantic outcome contract before inspection, governance, decisions or assignment. Make one complete direct call: never exec, batching, parallel or partial requests. Success opens or exactly reconciles this task. After ambiguous transport retry identically; never open a replacement.", _closed({
            "outcomes": outcomes,
            "project_root": _string(minimum=1, maximum=PROJECT_ROOT_MAX_LENGTH, description="Absolute existing canonical project directory supplied by the host or current workspace. This is the task's project root, not a planned output, package, artifact, or child directory: never append or create path segments for work that will be produced under it."),
            "request_original": _string(minimum=1, description="Exact original user request."),
            "user_language": _string(maximum=LANGUAGE_TAG_MAX_LENGTH, pattern=LANGUAGE_TAG_PATTERN, description="BCP-47 request language."),
            "constraints": _texts(minimum=1, description="Required non-empty complete task-wide constraint boundary. When no additional constraint exists, state that explicitly as one semantic list item; an empty array is invalid."),
            "context": _texts(description="Optional bounded factual context needed by fresh workers; omit it when none exists, and never use it as shorthand for missing outcome, acceptance, constraint, or verification requirements."),
        }, ("outcomes", "project_root", "request_original", "user_language", "constraints")), _closed({"task_ref": task_ref, "replayed": replayed}, ("task_ref", "replayed"))),
        "read_task": _contract("Worker-only first call with the exact server-rendered worker task_ref, never a coordinator ref. The returned assignment alone defines work, node-local check keys, artifact procedure and terminal kind. contract_context preserves scoped product requirements; its verification prose is not publication selectors or additional work for this node. Omit continue or use false to start; continue immediately only after a matching page says has_more=true. Recovery reconciles receipts, never new authority. No caller-supplied identity, cursors, views or report policy.", _closed({
            "task_ref": task_ref,
            "continue": {"type": "boolean", "description": "Optional continuation flag. Use true only after the immediately preceding assignment page with the same task_ref returned has_more=true; otherwise omit or use false."},
        }, ("task_ref",)), _closed({"task_ref": task_ref, "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether continue=true is valid next."}}, ("task_ref", "data", "has_more"))),
        "read_state": _contract("Coordinator-only scalar status: revision, coverage, conformance, execution, closure and assignment counts. No contract text, outcome list, reports, history or continuations. Use after completion/attention, user change, recovery or another concrete status need. This is not worker-liveness polling; never poll after a native timeout while the child is active.", _closed({
            "task_ref": task_ref,
        }, ("task_ref",)), _closed({"task_ref": task_ref, "data": _read_data()}, ("task_ref", "data"))),
        "read_scope": _contract("Coordinator-only current graph scope for one responsibility, immediately before assignment. Returns exact node keys, readiness, unmet prerequisites and outcome names for steering. bootstrap_available is admission readiness, not graph existence; when false, bootstrap_reasons identifies the missing baseline, evidence, budget or barrier. Read the indicated responsibility to find ready prerequisites. Only observed ready nodes may be assigned. Continue only after the matching page says has_more. Private admission binding stays on this connection.", _closed({
            "task_ref": task_ref,
            "responsibility": _string(enum=("delivery", "evidence", "planning"), maximum=16, description="Required assignment responsibility whose current server-derived scope is needed."),
            "continue": {"type": "boolean", "description": "Optional continuation flag. Use true only to inspect more exact names for an intentional partition after the immediately preceding matching scope page returned has_more=true."},
        }, ("task_ref", "responsibility")), _closed({"task_ref": task_ref, "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether another older scope page is available."}}, ("task_ref", "data", "has_more"))),
        "read_outcome": _contract("Coordinator-only complete current outcome read for a point replacement preserving acceptance, constraints or verification. Copy its exact name from read_scope. Returns only that outcome. Deletion alone needs no read_outcome.", _closed({
            "task_ref": task_ref,
            "outcome": _string(minimum=1, description="Exact current outcome name copied from read_scope; never paraphrase it."),
        }, ("task_ref", "outcome")), _closed({"task_ref": task_ref, "data": _read_data()}, ("task_ref", "data"))),
        "read_continuations": _contract("Coordinator-only bounded active-continuation read for recovery or lifecycle reconciliation. After recovery state shows unfinished delegated work, call this next; do not substitute read_timeline. It excludes task text, contract details, reports, and history. Continue only when the immediately preceding matching call returned has_more=true.", _closed({
            "task_ref": task_ref,
            "continue": {"type": "boolean", "description": "Optional continuation flag. Use true only after the immediately preceding continuation page with the same task_ref returned has_more=true."},
        }, ("task_ref",)), _closed({"task_ref": task_ref, "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether another continuation page is available."}}, ("task_ref", "data", "has_more"))),
        "read_evidence": _contract("Coordinator-only finalized evidence read using the selected report policy. The server owns selection and pagination. Omit continue or use false to start; use true only immediately after the same task_ref/report_policy returned has_more=true. The terminal page returns verified plan/report links when ready. Copy every relevant complete markdown_link byte-for-byte into its user-facing message, including brackets, label, parentheses and absolute destination. Bare paths, reconstructed Markdown and omitted labels are invalid; never guess a path.", _closed({
            "task_ref": task_ref,
            "report_policy": evidence_report_policy,
            "continue": {"type": "boolean", "description": "Optional continuation flag. Use true only after the immediately preceding evidence page with the same task_ref and report_policy returned has_more=true; otherwise omit or use false."},
        }, ("task_ref", "report_policy")), _closed({"task_ref": task_ref, "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether continue=true is valid next."}}, ("task_ref", "data", "has_more"))),
        "read_timeline": _contract("Coordinator-only newest-first history for an explicit chronology or audit need. It is not a recovery lookup and must not replace read_continuations. Omitted or false continuation starts newest; continue=true follows only an immediately preceding matching page with has_more=true and moves older. The server owns position. It returns historical event projections without repeating current state.", _closed({
            "task_ref": task_ref,
            "continue": {"type": "boolean", "description": "Optional continuation flag. Use true only after the immediately preceding timeline page with the same task_ref returned has_more=true; otherwise omit or use false."},
        }, ("task_ref",)), _closed({"task_ref": task_ref, "data": _read_data(), "has_more": {"type": "boolean", "description": "Whether continue=true is valid next."}}, ("task_ref", "data", "has_more"))),
        "open_clarification": _contract("Coordinator-only decision opening for one factual clarification whose possible answers leave every current outcome detail unchanged. A question known to choose behavior, acceptance, constraints, verification, or scope must use open_steering instead; closure review is the mandatory final user decision before close_task. This call records the hold but does not display its prompt to the user. For ordinary clarification omit the options property entirely; never send an empty array. After success, render the complete localized question in the final answer with established context, safe choices, and material consequence of each.", clarification_open, receipt),
        "record_clarification": _contract("Coordinator-only records a direct user answer after clarification or the mandatory final closure review; preserve exact response and language, and omit outcome for the ordinary form. If an ordinary answer itself states a semantic change, record the same direct message through record_steering next without asking for another confirmation; do not create an assignment against the old contract. A closure review must record exactly revise or close before close_task. Silence or unrelated prose never authorizes a semantic change or closure.", _closed({**answer, "outcome": _string(enum=("revise", "close"), maximum=16, description="Optional closure-review choice. Omit outcome for an ordinary clarification. Before close_task, record exactly revise or close from the current user-facing review.")}, ("task_ref", "response_original", "user_language")), receipt),
        "open_plan_review": _contract("Coordinator-only review of the independently validated current plan. Requires a verified ready human view; repair a missing/stale projection before opening review, never invent a link. After success, this records a hold, not approval; it does not display its prompt to the user. It returns data.human_view.markdown_link plus any exact alternatives. In the immediate final answer copy the complete markdown_link byte-for-byte, including brackets, label, parentheses and destination. Separately present a localized decision-ready summary of scope, ordered stages, changes, verification, stop conditions, risks and unresolved items. Show approve, request revision, or cancel; for alternatives, one answer can select a branch and approve it. Wait for the direct user decision. Never substitute closure choices. Identity and digests are server-derived.", decision_open, plan_review_receipt),
        "record_plan_review": _contract("Coordinator-only recording of the direct user decision after successful plan-review opening. Approval requires explicit current approval; silence, unrelated text, or an old-plan decision is not approval. For a decision-bearing family, one response selects one independently validated alternative and approves it; the same transaction applies its semantic delta, advances the revision and activates only its graph. No duplicate confirmation. Cortex resolves the pending binding internally.", _closed({**answer, "outcome": _string(enum=("approve", "request_revision", "cancel"), maximum=32, description="Required explicit direct user decision; never infer approval from silence or unrelated text."), "branch_key": _string(minimum=1, maximum=64, pattern="^[a-z][a-z0-9_-]{0,63}$", description="Required when approving a pending candidate family: copy the exact semantic key selected by the user from the opened review's alternatives. Never infer a branch by parsing prose. Omit for ordinary plans and for revision or cancellation.")}, ("task_ref", "response_original", "user_language", "outcome")), receipt),
        "open_steering": _contract("Coordinator-only decision opening for one task-scope or outcome change. If a question is known to choose previously unstated behavior or determine acceptance, constraints, verification, or scope, open steering before presenting it; the direct answer is the steering answer, so never ask for a second confirmation. Apply this before or after plan review, resume, or compaction. This call does not display its prompt to the user. After success, render the full localized context, choices, and material consequence of each in the final answer; never forward a context-free worker question. Do not apply the change or supply private identity.", decision_open, receipt),
        "record_steering": _contract("Coordinator-only atomic recording of a direct user-authored semantic change or an answer to an opened steering question. When the user already stated the concrete change, call this directly and never ask them to confirm the same instruction. Open steering first only when the coordinator genuinely needs the user to choose between material branches. No task read is required for independent complete additions. When an opened steering question has a retirement, first complete read_scope on this same connection (and read_outcome only when preserving one existing outcome); do not submit an unobserved name or guess from memory. Retirement requires exact current names observed through read_scope on this same connection. Preserve the exact response, complete new outcomes, and exact names to retire. Independent additions remain independent; one unambiguous retire-plus-add replacement is committed atomically. At least one of add or retire must be non-empty; an empty/empty delta is rejected.", _closed({**answer, "add": {**outcomes, "minItems": 0, "description": "Required complete new semantic outcomes to add; use an explicit empty array when none are added, and never infer additions from prose alone."}, "retire": {"type": "array", "description": "Required exact current outcome names to retire; copy each name from read_scope on this connection, use an explicit empty array when none retire, and never pass complete outcome objects or private identifiers.", "minItems": 0, "maxItems": TASK_CONTRACT_MAX_ITEMS, "uniqueItems": True, "items": _string(minimum=1, description="Exact current outcome name returned by read_scope.")}}, ("task_ref", "response_original", "user_language", "add", "retire")), receipt),
        "open_assignment": _contract(
            "Coordinator-only atomic node claim and native dispatch. First read matching current scope."
            " Supply exactly one of observed ready nodes or an available bootstrap intent: bounded read-only discovery, or planning from the full contract and finalized evidence."
            " The graph determines scope, terminal kind and predecessor reports; do not restate them."
            " Waiting prerequisites cannot be bypassed by model/effort changes."
            " The server derives ordinary claim versus loss reconciliation from the selected nodes' observed owner state."
            " A ready reconciliation node needs only its normal node selection."
            " For lost unpublished active owners, select their complete observed scopes in the same graph only after scope confirms loss evidence."
            " Fresh complete native observations must establish quiescence; live owners remain protected."
            " The atomic transition revokes lost authority and dispatches read-only reconciliation when budgets permit."
            " An exhausted result has no dispatch, grants no completion or closure authority, and must not be spawned or retried."
            " Forward successful non-replayed native_dispatch exactly once, immediately to native spawn. Never spawn a replay.", _closed({
            "task_ref": task_ref,
            "profile_name": _string(enum=packaged_profile_names(), maximum=ROLE_MAX_LENGTH, description="Coordinator-selected packaged specialist; publication kind comes from node purpose, not this profile."),
            "model": _string(enum=("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"), maximum=64, description="Luna is the default route and native spawn omits its override. Terra is for complex planning/architecture; Sol is for exceptional high-risk security work."),
            "reasoning_effort": _string(enum=("low", "medium", "high", "xhigh", "max"), maximum=16, description="Explicit bounded effort; ultra is forbidden."),
            "nodes": {"type": "array", "minItems": 1, "maxItems": 64, "uniqueItems": True,
                "items": _string(minimum=1, maximum=64, pattern="^[a-z][a-z0-9_-]{0,63}$"),
                "description": "Exact semantic node keys observed on this connection's current scope page. Omit only for a bootstrap intent."},
            "bootstrap": _closed({
                "kind": _string(enum=("discovery", "planning"), maximum=16),
                "question": _string(minimum=1, maximum=2048, description="Required only for discovery: one distinct evidence question. Omit for planning, whose scope is server-derived."),
            }, ("kind",), description="Only when matching scope has bootstrap_available=true. Append discovery/planning, never an already ready baseline. Mutually exclusive with nodes."),
        }, ("task_ref", "profile_name", "model", "reasoning_effort")), _closed({"native_dispatch": native_dispatch, "replayed": replayed}, ("native_dispatch", "replayed"))),
        "publish_plan": _contract("Worker-only atomic terminal plan publication. Publish one complete ordinary candidate or a complete family of alternatives for independent validation. Each alternative carries its full proposed semantic delta and execution graph; no branch gains authority on publication. Low-risk ordinary work may be informational. Material risk, a genuine product or authority choice, credentials or explicitly requested review needs a decision packet; incomplete work alone requires bounded discovery, not approval. The planner cannot choose or downgrade review policy. Publish once, then stop.", plan_publication, receipt),
        "publish_result": _contract("Worker-only atomic terminal result for the consumed result-kind assignment. Before calling, check every advertised required property, including empty arrays. Node coverage is the sole observed check-state ledger; do not duplicate it. Fit the complete compact UTF-8 payload within aggregate and field limits: shorten redundant prose only, never omit sections, truncate, slice or invent evidence. An aggregate-size rejection gives actual/maximum bytes and section sizes; permit one materially smaller complete correction, then stop if still invalid. Publish only this assignment. After success, stop; identical reconciliation is replay, changed content conflicts.", result_publication, receipt),
        "publish_documentation": _contract("Worker-only atomic terminal documentation assessment for the consumed documentation-kind assignment. Exploration, audit and verification use publish_result even when no files changed. Supply every required field, including empty arrays, with complete node coverage and documentation findings. Node coverage is the sole observed check-state ledger; never duplicate it. Publish only this assignment. After success stop; identical reconciliation is replay and changed content conflicts.", documentation_publication, receipt),
        "assess_governance": _contract("Coordinator-only assessment after open_task succeeds and before first assignment. Select an explicit advertised mode first; rationale never substitutes. Reassess only on material new risk evidence. Never invent task_ref. Native workers, planners, replacements and rework workers cannot assess. Advisory, not scheduling, lifecycle or authorization.", _closed({"task_ref": task_ref, "mode": _string(enum=GOVERNANCE_MODES, maximum=16, description="Required explicit coordinator depth selection made before invoking the assessment; choose only an advertised value and never infer it from rationale or risk notes alone."), "rationale": _string(description="Optional supporting assessment rationale; it never replaces the required explicit depth."), "risk_factors": _texts(description="Optional complete supporting risk-factor list; omit when not supplied or use an explicit empty array when the assessment found none.")}, ("task_ref", "mode")), receipt),
        "close_task": _contract("Coordinator-only closure after reconciling current verification, outcome coverage, documentation, risks and unresolved evidence. First present the current result, then open the mandatory closure_review, wait for the direct user choice and record exactly revise or close. Only a current recorded close permits this call; silence, worker completion, earlier approval or an advance auto-close request never does. Never call as a readiness probe. New work stales the review; revise continues the same task. The ledger independently checks closure evidence. On success copy every relevant returned markdown_link byte-for-byte into the immediate final answer. Governance is advisory, not permission.", _closed({"task_ref": task_ref, "verdict": _string(enum=CLOSURE_VERDICTS, maximum=32, description="Required evidence-backed coordinator closure verdict chosen from advertised values; ready does not itself prove that unrun checks passed."), "unresolved_risks": _texts(description="Optional complete unresolved-risk list; omit when not supplied or use an explicit empty array only when none remain."), "follow_ups": _texts(description="Optional complete follow-up list; omit when not supplied or use an explicit empty array when none are needed."), "completion_notes": _texts(description="Optional complete closure notes; omit when not supplied or use an explicit empty array only when none exist.")}, ("task_ref", "verdict")), closure_receipt),
    }
    assignment_receipt = {"type": "object", "anyOf": [
        contracts["open_assignment"]["outputSchema"],
        _closed({"state": {"const": "exhausted", "type": "string"},
                 "dispatched": {"const": False, "type": "boolean"},
                 "nodes": _texts(minimum=1), "replayed": replayed},
                ("state", "dispatched", "nodes", "replayed")),
    ]}
    contracts["open_assignment"]["outputSchema"] = assignment_receipt
    contracts["open_assignment"]["runtimeOutputSchema"] = assignment_receipt
    contracts["assess_governance"]["inputSchema"]["properties"].update({
        "execution_route": _string(enum=("planned", "minimal"), description="Defaults to planned. Select minimal only for one bounded risk-free complete-contract scope without dependent audits, multiple mutation owners, unresolved branches, or requested review. The server generates its single execution node after baseline evidence; planned uses a planner-authored independently validated graph."),
        "minimal_mode": _string(enum=("read_only", "mutating"), description="Required for minimal execution: whether the complete task changes project files. Omit for planned execution."),
        "user_review_requested": {"type": "boolean", "description": "Whether the user explicitly requested plan review. Omission preserves their recorded preference, initially false. Set false only when the user explicitly withdraws that request. True requires a planned route and exact plan approval regardless of risk depth. Approval fulfills that assessment's decision boundary: authorized replanning does not repeat it. A materially newer assessment or explicitly renewed review request establishes a fresh boundary."},
    })
    for name in ("publish_plan", "publish_result", "publish_documentation"):
        contracts[name]["outputSchema"] = publication_receipt
        contracts[name]["runtimeOutputSchema"] = publication_receipt
    steering_receipt = _closed({**receipt["properties"], "effect": _closed({
        "effective_revision": {"type": "integer", "minimum": 1},
        "reconciliation_required": {"type": "boolean"},
        "reconciliation_epoch": {"type": "integer", "minimum": 0},
        "invalidated_assignment_count": {"type": "integer", "minimum": 0},
        "stale_assignments": {"type": "array", "items": _closed({
            "nodes": {"type": "array", "items": _string(minimum=1, maximum=64)},
            "task_name": _string(minimum=1, maximum=160),
        }, ("nodes", "task_name"))},
    }, ("effective_revision", "reconciliation_required", "reconciliation_epoch", "invalidated_assignment_count", "stale_assignments"))},
        ("task_ref", "state", "replayed", "effect"))
    contracts["record_steering"]["outputSchema"] = steering_receipt
    contracts["record_steering"]["runtimeOutputSchema"] = steering_receipt
    selection_receipt = {"anyOf": [receipt, steering_receipt]}
    contracts["record_plan_review"]["outputSchema"] = selection_receipt
    contracts["record_plan_review"]["runtimeOutputSchema"] = selection_receipt
    return {name: contracts[name] for name in V12_TOOL_NAMES}


def public_input_schemas(contracts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    catalogue = build_public_contracts() if contracts is None else contracts
    return {str(name): dict(value["inputSchema"]) for name, value in catalogue.items()}
