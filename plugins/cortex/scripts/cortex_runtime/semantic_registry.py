"""Authoritative metadata for the public Cortex semantic boundary.

This module intentionally contains no persistence or orchestration policy.  It
describes the boundary once, while the existing contract builder remains the
implementation of the JSON Schema documents during the migration.  Keeping
the registry dependency-free avoids an import cycle and makes it safe for
validators, documentation tooling, and the MCP composition root to consume.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Any, Literal

OperationKind = Literal["command", "query"]


@dataclass(frozen=True)
class ErrorSpec:
    """One safe semantic failure contract shared by every public boundary."""
    code: str
    message: str
    action: str
    retryable: bool = False


_DEFAULT_ERROR_ACTION = "Review the advertised tool schema and use only values emitted by Cortex."
_ERROR_SPECS: tuple[ErrorSpec, ...] = (
    ErrorSpec("ledger_error", "The tool request could not be completed.", "Read current task state before the LLM chooses another action."),
    ErrorSpec("command_conflict", "The private mutation slot contains different semantic intent.", "Read current task state; the LLM decides how to proceed."),
    ErrorSpec("idempotency_conflict", "An identical private publication slot received different semantic content.", "Read current task state; do not replay changed content as the same mutation."),
    ErrorSpec("clarification_binding_conflict", "The pending decision was consumed with different input.", "Read current task state before the LLM chooses another action."),
    ErrorSpec("clarification_binding_mismatch", "The pending decision belongs to a different family or subject.", "Use the matching task_ref-only decision operation."),
    ErrorSpec("clarification_binding_stale", "The pending decision is stale after a task revision.", "Read current task state before opening a new decision."),
    ErrorSpec("fresh_state_read_required", "The pending steering answer requires a fresh current-state read on this connection.", "Read the current task state on this same coordinator connection, rebuild the steering answer from that result, and make one corrected record call."),
    ErrorSpec("cross_project_reference", "The task context belongs to another project.", "Use the exact task_ref supplied for this actor."),
    ErrorSpec("invalid_decision_subject", "The selected decision family or subject is invalid.", "Use the matching task_ref-only decision operation."),
    ErrorSpec("clarification_binding_not_found", "No matching pending decision exists.", "Read current task state before the LLM chooses another action."),
    ErrorSpec("clarification_binding_consumed", "The pending decision has already been consumed.", "Read current task state and reconcile the recorded result."),
    ErrorSpec("outcome_item_not_found", "The semantic outcome is not in the effective contract.", "Copy the complete semantic outcome from the current read_state result."),
    ErrorSpec("outcome_item_stale", "The semantic outcome is stale for the effective contract.", "Read current task state and use its current semantic outcome."),
    ErrorSpec("outcome_assignment_conflict", "The semantic outcome has incompatible active ownership.", "Read current task state; the LLM decides how to resolve ownership."),
    ErrorSpec("dispatch_lease_active", "The same assignment intent already has an active native dispatch.", "Use the server-returned native dispatch or read current task state."),
    ErrorSpec("assignment_loss_unrecorded", "A nonterminal predecessor cannot be replaced without explicit loss evidence.", "Read current task state and proceed only after confirming blocked or aborted status with concrete evidence."),
    ErrorSpec("assignment_loss_scope_conflict", "The selected outcomes do not identify one exact recoverable predecessor.", "Read current delivery scope and use confirmed loss evidence. Omit outcome selection when exactly one complete predecessor scope is recoverable; otherwise select one exact advertised recovery scope without adding or removing outcomes. Do not replay or guess assignment lineage."),
    ErrorSpec("assignment_loss_conflict", "The predecessor already has terminal evidence or a different recorded successor.", "Read current task state and reconcile the existing lineage; do not create another replacement."),
    ErrorSpec("assignment_not_consumed", "The worker assignment has not been completely consumed on this connection.", "Consume every assignment page on this same worker connection before publishing; do not retry publication first."),
    ErrorSpec("wrong_connection", "This operation is not available to the authenticated audience of this connection.", "Use the original host-attested worker connection for worker operations or the coordinator connection for coordinator operations; do not copy task references between connections."),
    ErrorSpec("connection_lost", "The assignment was consumed by a worker connection that is no longer available.", "Do not retry worker publication. The coordinator must record the predecessor as blocked or aborted and create one explicitly linked loss-recovery assignment."),
    ErrorSpec("assignment_stale", "The assignment or its connection-bound continuation is no longer current.", "Stop this worker route and read current task state from the coordinator connection before deciding whether explicit rework or loss recovery is required."),
    ErrorSpec("publication_conflict", "This assignment already has different terminal publication content.", "Do not overwrite or retry changed content. The coordinator must reconcile the existing publication and create a new rework assignment when further work is required."),
    ErrorSpec("dispatch_lease_expired", "The native dispatch lifecycle could not be bound.", "Read current task state before the LLM chooses another action."),
    ErrorSpec("storage_busy", "The V12 ledger is temporarily busy.", "Retry the identical request after the stated delay; do not change semantic input or create a replacement binding.", True),
    ErrorSpec("storage_unavailable", "The V12 ledger is temporarily unavailable.", "Preserve the exact request and retry only after storage is available.", True),
    ErrorSpec("ledger_corrupt", "The durable ledger cannot be safely read.", "Do not retry a mutation; obtain supported server-state diagnosis."),
    ErrorSpec("schema_unsupported", "The durable ledger schema is unsupported.", "Do not migrate, reset, or modify storage from this tool call."),
    ErrorSpec("validation_error", "The supplied arguments do not satisfy the advertised tool schema.", "Correct the named field and call the same tool again."),
    ErrorSpec("invalid_argument", "A supplied public argument is invalid.", "Correct the named field and call the same tool again."),
    ErrorSpec("invalid_identifier", "The supplied task_ref is invalid.", "Use the exact task_ref emitted by Cortex."),
    ErrorSpec("content_invalid", "A supplied JSON value is invalid.", "Supply finite JSON within the advertised bounds."),
    ErrorSpec("task_not_found", "The referenced task does not exist in the resolved ledger.", "Use the exact task_ref emitted by Cortex."),
    ErrorSpec("delegation_not_found", "The worker assignment does not exist for this task context.", "Use the exact worker-scoped task_ref from native dispatch."),
    ErrorSpec("report_not_found", "The selected server-side evidence does not exist.", "Read current task evidence before the LLM chooses another action."),
    ErrorSpec("decision_not_found", "The selected server-side decision does not exist.", "Read current task state before the LLM chooses another action."),
    ErrorSpec("approval_view_not_ready", "The active plan is not ready for review.", "Read current task state before the LLM chooses another action."),
    ErrorSpec("approval_view_required", "A finalized active plan is required for review.", "Read current task state before the LLM chooses another action."),
    ErrorSpec("approval_view_mismatch", "The private active-plan relation is stale.", "Read current task state before opening another plan review."),
    ErrorSpec("approval_handle_not_found", "The private active-plan relation is unavailable.", "Read current task state before the LLM chooses another action."),
    ErrorSpec("approval_handle_mismatch", "The private active-plan relation does not match.", "Read current task state before the LLM chooses another action."),
    ErrorSpec("approval_handle_consumed", "The private active-plan relation was already consumed.", "Read current task state and reconcile the recorded review."),
    ErrorSpec("decision_pending", "One user decision is pending for this task.", "Record the user's exact answer through the matching task_ref-only operation."),
    ErrorSpec("closure_review_required", "A current explicit user result review is required before task closure.", "Present the current verified result, use the advertised open_clarification operation to open closure_review, wait for the direct user choice, and use record_clarification to record revise or close before any close_task retry."),
    ErrorSpec("closure_revision_requested", "The user requested revision of the current task.", "Keep the same task open, obtain any needed revision detail, and dispatch bounded rework before presenting a new result review."),
    ErrorSpec("closure_review_stale", "The accepted result review is stale after later task activity.", "Present the updated verified result and record a new explicit closure-review choice before retrying task closure."),
)
_ERROR_BY_CODE = {spec.code: spec for spec in _ERROR_SPECS}
# These codes are emitted by long-lived storage/report paths outside the Phase
# D decision slice. They remain semantic public failures, even when they share
# the generic bounded recovery wording rather than a family-specific sentence.
_GENERIC_PUBLIC_ERROR_CODES = frozenset({
    "project_root_invalid", "task_ref_ambiguous", "record_ref_ambiguous",
    "task_exists", "delegation_exists", "report_exists", "invalid_model_selection",
    "profile_unavailable", "invalid_report", "invalid_report_operation",
    "report_chunk_too_large", "report_too_large", "report_quota_exceeded",
    "report_state_conflict", "report_incomplete", "report_operation_conflict",
    "report_chunk_conflict", "report_chunk_out_of_order", "report_manifest_mismatch",
    "input_evidence_unread", "result_report_exists", "report_cursor_invalid",
    "report_cursor_scope_mismatch", "report_cursor_stale", "invalid_governance_mode",
    "invalid_initiative_status", "invalid_initiative_parent", "initiative_not_found",
    "initiative_revision_not_material", "invalid_closure_subject", "invalid_decision_type",
    "decision_subject_not_finalized", "decision_subject_digest_mismatch",
    "decision_response_required", "decision_response_reused_original",
    "assignment_not_found", "evidence_cursor_invalid",
    "evidence_scope_mismatch", "closure_not_ready",
    "capability_conflict", "capability_not_found", "capability_stale",
})
for _code in _GENERIC_PUBLIC_ERROR_CODES:
    _ERROR_BY_CODE.setdefault(_code, ErrorSpec(
        _code, "The request could not be completed with the current durable state.",
        _DEFAULT_ERROR_ACTION,
    ))


def error_contract(code: object) -> ErrorSpec:
    """Resolve one registry-owned public error contract or safe internal fault."""
    return _ERROR_BY_CODE.get(code, _ERROR_BY_CODE["ledger_error"]) if isinstance(code, str) else _ERROR_BY_CODE["ledger_error"]


def public_error_codes() -> frozenset[str]:
    return frozenset(_ERROR_BY_CODE)


@dataclass(frozen=True)
class OperationSpec:
    name: str
    kind: OperationKind
    feature_ids: tuple[str, ...]
    input_schema_key: str
    output_schema_key: str
    produced_capabilities: tuple[str, ...] = ()
    consumed_capabilities: tuple[str, ...] = ()
    anchor: str = "task_ref"
    logical_slot: str = ""
    handler_name: str = ""
    safe_errors: tuple[str, ...] = ()


_SPECS: tuple[OperationSpec, ...] = (
    OperationSpec("open_task", "command", ("task-contract", "task-identity"), "open_task", "open_task", ("task_ref",), anchor="project_root", logical_slot="task.request", handler_name="open_task", safe_errors=("validation_error", "task_exists", "storage_unavailable")),
    OperationSpec("read_task", "query", ("worker-dispatch", "typed-evidence", "evidence-read-receipts"), "read_task", "read_task", ("assignment_evidence",), ("task_ref",), handler_name="read_task", safe_errors=("task_not_found", "report_cursor_invalid", "assignment_not_consumed", "wrong_connection", "connection_lost", "assignment_stale")),
    OperationSpec("read_state", "query", ("task-projection", "contract-revision"), "read_state", "read_state", ("task_state",), ("task_ref",), handler_name="read_state", safe_errors=("task_not_found", "wrong_connection")),
    OperationSpec("read_scope", "query", ("dag", "ownership", "contract-revision"), "read_scope", "read_scope", ("assignment_scope",), ("task_ref",), handler_name="read_scope", safe_errors=("task_not_found", "report_cursor_invalid", "wrong_connection")),
    OperationSpec("read_outcome", "query", ("contract-revision", "steering"), "read_outcome", "read_outcome", ("semantic_outcome",), ("task_ref",), handler_name="read_outcome", safe_errors=("task_not_found", "outcome_item_not_found", "wrong_connection")),
    OperationSpec("read_continuations", "query", ("worker-continuation", "recovery-rework"), "read_continuations", "read_continuations", ("continuation_state",), ("task_ref",), handler_name="read_continuations", safe_errors=("task_not_found", "report_cursor_invalid", "wrong_connection")),
    OperationSpec("read_evidence", "query", ("typed-evidence", "historical-evidence", "projections"), "read_evidence", "read_evidence", ("report_evidence",), ("task_ref",), handler_name="read_evidence", safe_errors=("task_not_found", "report_cursor_invalid", "wrong_connection")),
    OperationSpec("read_timeline", "query", ("timeline", "historical-evidence"), "read_timeline", "read_timeline", ("timeline_evidence",), ("task_ref",), handler_name="read_timeline", safe_errors=("task_not_found", "report_cursor_invalid", "wrong_connection")),
    OperationSpec("open_clarification", "command", ("clarification", "closure-review", "decision-identity"), "open_clarification", "open_clarification", ("pending_decision",), ("task_ref",), logical_slot="decision.pending.clarification", handler_name="open_clarification", safe_errors=("task_not_found", "invalid_argument", "command_conflict", "clarification_binding_stale")),
    OperationSpec("record_clarification", "command", ("clarification", "closure-review", "decision-transaction"), "record_clarification", "record_clarification", ("decision_recorded",), ("task_ref",), logical_slot="decision.consumed", handler_name="record_clarification", safe_errors=("invalid_argument", "clarification_binding_mismatch", "clarification_binding_not_found", "clarification_binding_stale", "clarification_binding_conflict", "clarification_binding_consumed", "command_conflict")),
    OperationSpec("open_plan_review", "command", ("plan-approval", "decision-identity"), "open_plan_review", "open_plan_review", ("pending_decision",), ("task_ref",), logical_slot="decision.pending.plan_review", handler_name="open_plan_review", safe_errors=("task_not_found", "approval_view_required", "approval_view_not_ready", "command_conflict")),
    OperationSpec("record_plan_review", "command", ("plan-approval", "decision-transaction"), "record_plan_review", "record_plan_review", ("decision_recorded",), ("task_ref",), logical_slot="decision.consumed", handler_name="record_plan_review", safe_errors=("clarification_binding_not_found", "clarification_binding_stale", "clarification_binding_conflict", "clarification_binding_consumed", "approval_handle_mismatch", "command_conflict")),
    OperationSpec("open_steering", "command", ("steering", "decision-identity"), "open_steering", "open_steering", ("pending_decision",), ("task_ref",), logical_slot="decision.pending.steering", handler_name="open_steering", safe_errors=("task_not_found", "command_conflict", "clarification_binding_stale")),
    OperationSpec("record_steering", "command", ("steering", "decision-transaction", "contract-delta"), "record_steering", "record_steering", ("decision_recorded",), ("task_ref",), logical_slot="decision.consumed", handler_name="record_steering", safe_errors=("fresh_state_read_required", "clarification_binding_not_found", "clarification_binding_stale", "clarification_binding_conflict", "clarification_binding_consumed", "command_conflict")),
    OperationSpec("open_assignment", "command", ("worker-dispatch", "server-owned-routing", "dispatch-correlation", "dag", "ownership", "model-routing", "assignment-loss-lineage"), "open_assignment", "open_assignment", ("native_dispatch",), ("task_ref", "semantic_outcomes", "report_policy"), logical_slot="assignment.intent", handler_name="open_assignment", safe_errors=("task_not_found", "profile_unavailable", "outcome_item_not_found", "outcome_item_stale", "outcome_assignment_conflict", "dispatch_lease_active", "assignment_loss_unrecorded", "assignment_loss_scope_conflict", "assignment_loss_conflict", "command_conflict")),
    OperationSpec("publish_plan", "command", ("planning", "publication-completeness", "contract-coverage"), "publish_plan", "publish_plan", ("publication_state",), ("task_ref", "semantic_evidence"), logical_slot="publication.plan", handler_name="publish_plan", safe_errors=("report_incomplete", "publication_conflict", "assignment_not_consumed", "wrong_connection", "connection_lost", "assignment_stale")),
    OperationSpec("publish_result", "command", ("implementation", "verification", "publication-completeness", "atomic-publication"), "publish_result", "publish_result", ("publication_state",), ("task_ref", "semantic_evidence"), logical_slot="publication.result", handler_name="publish_result", safe_errors=("report_incomplete", "publication_conflict", "assignment_not_consumed", "wrong_connection", "connection_lost", "assignment_stale")),
    OperationSpec("publish_documentation", "command", ("documentation-impact", "documentation-sync", "publication-completeness", "atomic-publication"), "publish_documentation", "publish_documentation", ("publication_state",), ("task_ref", "semantic_evidence"), logical_slot="publication.documentation", handler_name="publish_documentation", safe_errors=("report_incomplete", "publication_conflict", "assignment_not_consumed", "wrong_connection", "connection_lost", "assignment_stale")),
    OperationSpec("assess_governance", "command", ("governance", "risk"), "assess_governance", "assess_governance", ("governance_state",), ("task_ref",), logical_slot="governance.assessment", handler_name="assess_governance", safe_errors=("task_not_found",)),
    OperationSpec("close_task", "command", ("closure", "follow-ups", "unresolved-risks"), "close_task", "close_task", ("closure_state",), ("task_ref",), logical_slot="task.closure", handler_name="close_task", safe_errors=("closure_not_ready", "closure_review_required", "closure_revision_requested", "closure_review_stale", "task_not_found")),
)

OPERATION_NAMES: tuple[str, ...] = tuple(spec.name for spec in _SPECS)
# Capability IDs that are intentionally broader than callable tools.  These
# are the preservation vocabulary from the feature-parity contract; coordinator
# and worker capabilities are listed here even when their owner is not a
# backend operation.  This prevents an architectural registry from becoming a
# disguised feature-cut list during later vertical-slice cutovers.
_PARITY_FEATURE_IDS = (
    "explicit-opt-in", "english-worker-boundary", "worker-only-execution",
    "worker-required", "dynamic-dag", "dag-lifecycle", "parallel-waves",
    "recovery-rework", "worker-liveness", "planner-discovery",
    "immutable-plan", "plan-approval", "clarification", "clarification-hold", "worker-continuation", "dispatch-correlation", "same-task-steering",
    "typed-evidence", "evidence-read-receipts", "worker-report-publication",
    "role-complete-reports", "one-terminal-result", "historical-evidence",
    "model-routing", "profile-specialization", "governance-depth",
    "governance-nonblocking", "initiatives", "initiative-materiality",
    "documentation-impact", "documentation-sync", "knowledge-route",
    "content-safety", "context-recovery", "forward-migrations",
    "concurrency-replay", "lost-response-reconciliation", "server-owned-identity",
    "atomic-publication", "closure-readiness", "unresolved-risks",
    "projections", "worker-hidden-errors", "llm-live-dev",
    "candidate-provenance", "single-mcp-catalogue", "schema-authority",
    "package-validation", "task-locator-authority",
)
FEATURE_IDS: tuple[str, ...] = tuple(sorted(set(_PARITY_FEATURE_IDS) | {feature for spec in _SPECS for feature in spec.feature_ids}))
FEATURE_OWNERS: Mapping[str, str] = {
    feature: (next((spec.name for spec in _SPECS if feature in spec.feature_ids), "coordinator-policy"))
    for feature in FEATURE_IDS
}


def operation_specs() -> tuple[OperationSpec, ...]:
    """Return the stable, ordered operation metadata."""
    return _SPECS


def validate_receipt_metadata() -> tuple[str, ...]:
    """Return registry metadata violations (commands require slots; queries do not)."""
    errors: list[str] = []
    for spec in _SPECS:
        if spec.kind == "command" and not spec.logical_slot:
            errors.append(f"{spec.name}: command has no logical_slot")
        if spec.kind == "query" and spec.logical_slot:
            errors.append(f"{spec.name}: query declares logical_slot")
        if len(spec.safe_errors) != len(set(spec.safe_errors)):
            errors.append(f"{spec.name}: duplicate safe error code")
        for code in spec.safe_errors:
            if code not in _ERROR_BY_CODE:
                errors.append(f"{spec.name}: unknown safe error code {code}")
    return tuple(errors)


def spec_for(name: str) -> OperationSpec:
    for spec in _SPECS:
        if spec.name == name:
            return spec
    raise KeyError(name)


def producer_consumer_edges() -> tuple[tuple[str, str, str], ...]:
    """Return typed capability edges as ``producer, consumer, capability``."""
    produced = {cap: spec.name for spec in _SPECS for cap in spec.produced_capabilities}
    return tuple((producer, spec.name, cap) for spec in _SPECS for cap in spec.consumed_capabilities if (producer := produced.get(cap)) is not None)


def build_contracts() -> Mapping[str, Mapping[str, Any]]:
    """Derive public schemas from the existing contract builder during migration."""
    from cortex_runtime.public_contracts import build_public_contracts
    contracts = build_public_contracts()
    if tuple(contracts) != OPERATION_NAMES:
        raise RuntimeError("semantic registry and public contract catalogue diverged")
    return contracts


def bind_handlers(handlers: Mapping[str, Callable[..., Mapping[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Bind a complete handler map in registry order, rejecting drift."""
    missing = [name for name in OPERATION_NAMES if name not in handlers]
    extra = [name for name in handlers if name not in OPERATION_NAMES]
    if missing or extra:
        raise RuntimeError(f"semantic handler drift: missing={missing}, extra={extra}")
    contracts = build_contracts()
    return {name: {**dict(contracts[name]), "handler": handlers[name]} for name in OPERATION_NAMES}


def exported_metadata() -> dict[str, Any]:
    """JSON-compatible metadata for validators and architecture tooling."""
    return {
        "operations": [spec.__dict__ for spec in _SPECS],
        "errors": [spec.__dict__ for spec in _ERROR_BY_CODE.values()],
        "feature_ids": list(FEATURE_IDS),
        "feature_owners": dict(FEATURE_OWNERS),
        "edges": [list(edge) for edge in producer_consumer_edges()],
    }
