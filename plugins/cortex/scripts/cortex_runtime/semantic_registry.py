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
    ErrorSpec("ledger_error", "The tool request could not be completed.", "Do not blindly repeat this mutation. Preserve the exact last successful handles and pending intent, then obtain supported server-state diagnosis."),
    ErrorSpec("command_conflict", "The command slot already contains different semantic intent.", "Preserve the original binding and receipt. Changed intent requires a new server-owned decision flow."),
    ErrorSpec("idempotency_conflict", "The idempotency key was already used for different arguments.", "Reuse the exact retry handle only with byte-identical arguments."),
    ErrorSpec("clarification_binding_conflict", "The decision binding was already consumed with different input.", "Reconcile the original binding; do not create a replacement binding."),
    ErrorSpec("clarification_binding_mismatch", "The supplied binding belongs to a different decision family or subject.", "Use only the exact binding returned by the matching open operation."),
    ErrorSpec("clarification_binding_stale", "The decision binding is stale after an effective-contract revision.", "Read current task state and obtain a new server-owned decision binding."),
    ErrorSpec("cross_project_reference", "A supplied reference is outside the task's project scope.", "Use only references emitted for the supplied task."),
    ErrorSpec("invalid_decision_subject", "The supplied decision subject or family is invalid.", "Use the matching family operation and an emitted same-task subject reference."),
    ErrorSpec("clarification_binding_not_found", "The supplied decision binding was not found.", "Use the exact binding returned by the matching open operation."),
    ErrorSpec("clarification_binding_consumed", "The supplied decision binding has already been consumed.", "Reconcile the original decision receipt; do not open a replacement binding."),
    ErrorSpec("outcome_item_not_found", "The supplied outcome item is not in the effective contract.", "Use only the item reference emitted by the current effective contract."),
    ErrorSpec("outcome_item_stale", "The supplied outcome item is stale for the current effective contract.", "Read the current task state and use a current contract item reference."),
    ErrorSpec("outcome_assignment_conflict", "The outcome item already has an incompatible assignment.", "Preserve the existing assignment and reconcile its original receipt."),
    ErrorSpec("dispatch_lease_active", "The parent assignment still owns an active worker dispatch lease.", "Continue the existing server-issued dispatch; do not create a replacement until terminal, absent, stale, or expiry evidence is available."),
    ErrorSpec("dispatch_lease_expired", "The worker dispatch lease has expired before bootstrap consumption.", "Reconcile the exact assignment state read-only, then obtain a new server-issued dispatch only when the prior lease is proven stale."),
    ErrorSpec("storage_busy", "The V12 ledger is temporarily busy.", "Retry the identical request after the stated delay; do not change semantic input or create a replacement binding.", True),
    ErrorSpec("storage_unavailable", "The V12 ledger is temporarily unavailable.", "Preserve the exact request and retry only after storage is available.", True),
    ErrorSpec("ledger_corrupt", "The durable ledger cannot be safely read.", "Do not retry a mutation; obtain supported server-state diagnosis."),
    ErrorSpec("schema_unsupported", "The durable ledger schema is unsupported.", "Do not migrate, reset, or modify storage from this tool call."),
    ErrorSpec("validation_error", "The supplied arguments do not satisfy the advertised tool schema.", "Correct the named field and call the same tool again."),
    ErrorSpec("invalid_argument", "A supplied public argument is invalid.", "Correct the named field and call the same tool again."),
    ErrorSpec("invalid_identifier", "A supplied Cortex identifier is invalid.", "Reuse the exact emitted compact reference; do not reconstruct it."),
    ErrorSpec("content_invalid", "A supplied JSON value is invalid.", "Supply finite JSON within the advertised bounds."),
    ErrorSpec("task_not_found", "The referenced task does not exist in the resolved ledger.", "Use the exact task_ref emitted by Cortex."),
    ErrorSpec("delegation_not_found", "The referenced assignment does not exist in the anchored task.", "Use the exact assignment_ref emitted by Cortex."),
    ErrorSpec("report_not_found", "The referenced report does not exist in the anchored task.", "Use the exact report_ref emitted by Cortex."),
    ErrorSpec("decision_not_found", "The referenced decision does not exist in the anchored task.", "Use the exact decision_ref emitted by Cortex."),
    ErrorSpec("approval_view_not_ready", "The plan approval view is not ready.", "Use the ready relation returned by plan publication; do not construct or select a view."),
    ErrorSpec("approval_view_required", "The plan review requires a server-issued ready relation.", "Use the plan publication relation returned by Cortex."),
    ErrorSpec("approval_view_mismatch", "The supplied approval relation does not match the plan.", "Use the exact server-issued relation without modification."),
    ErrorSpec("approval_handle_not_found", "The supplied approval handle was not found.", "Use the opaque handle returned by the plan publication relation."),
    ErrorSpec("approval_handle_mismatch", "The supplied approval handle does not match the bound plan relation.", "Use the exact relation captured by the review binding."),
    ErrorSpec("approval_handle_consumed", "The supplied approval handle has already been used.", "Reconcile the original review binding; do not create a replacement relation."),
    ErrorSpec("decision_pending", "A server-issued user decision is still pending for this task.", "Record the next exact user response with the previously returned pending binding before any other task mutation."),
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
    "publication_conflict", "assignment_not_found", "evidence_cursor_invalid",
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
    OperationSpec("read_task", "query", ("task-projection", "timeline", "contract-revision"), "read_task", "read_task", ("task_state",), ("task_ref",), handler_name="read_task", safe_errors=("task_not_found", "report_cursor_invalid")),
    OperationSpec("open_clarification", "command", ("clarification", "clarification-hold", "decision-identity"), "open_clarification", "open_clarification", ("clarification_binding_ref", "clarification_hold"), ("task_ref",), logical_slot="decision.pending.clarification", handler_name="open_clarification", safe_errors=("task_not_found", "command_conflict", "clarification_binding_stale")),
    OperationSpec("record_clarification", "command", ("clarification", "clarification-hold", "decision-transaction"), "record_clarification", "record_clarification", ("decision_ref",), ("task_ref", "clarification_binding_ref", "clarification_hold"), logical_slot="decision.consumed", handler_name="record_clarification", safe_errors=("clarification_binding_not_found", "clarification_binding_stale", "clarification_binding_conflict", "clarification_binding_consumed", "command_conflict")),
    OperationSpec("open_plan_review", "command", ("plan-approval", "decision-identity"), "open_plan_review", "open_plan_review", ("plan_review_binding_ref",), ("task_ref", "plan_ref"), logical_slot="decision.pending.plan_review", handler_name="open_plan_review", safe_errors=("task_not_found", "approval_view_required", "approval_view_not_ready", "command_conflict")),
    OperationSpec("record_plan_review", "command", ("plan-approval", "decision-transaction"), "record_plan_review", "record_plan_review", ("decision_ref",), ("task_ref", "plan_review_binding_ref"), logical_slot="decision.consumed", handler_name="record_plan_review", safe_errors=("clarification_binding_not_found", "clarification_binding_stale", "clarification_binding_conflict", "clarification_binding_consumed", "approval_handle_mismatch", "command_conflict")),
    OperationSpec("open_steering", "command", ("steering", "decision-identity"), "open_steering", "open_steering", ("steering_binding_ref",), ("task_ref",), logical_slot="decision.pending.steering", handler_name="open_steering", safe_errors=("task_not_found", "command_conflict", "clarification_binding_stale")),
    OperationSpec("record_steering", "command", ("steering", "decision-transaction", "contract-delta"), "record_steering", "record_steering", ("decision_ref",), ("task_ref", "steering_binding_ref"), logical_slot="decision.consumed", handler_name="record_steering", safe_errors=("clarification_binding_not_found", "clarification_binding_stale", "clarification_binding_conflict", "clarification_binding_consumed", "command_conflict")),
    OperationSpec("open_assignment", "command", ("worker-dispatch", "assignment-mission", "server-owned-routing", "dispatch-correlation", "typed-anchor-continuation", "publication-completeness", "dag", "ownership", "model-routing"), "open_assignment", "open_assignment", ("assignment_ref", "dispatch_correlation"), ("task_ref", "decision_ref", "report_ref"), logical_slot="assignment.intent", handler_name="open_assignment", safe_errors=("task_not_found", "profile_unavailable", "outcome_item_not_found", "outcome_item_stale", "outcome_assignment_conflict", "dispatch_lease_active", "command_conflict", "decision_pending")),
    OperationSpec("consume_assignment_evidence", "query", ("evidence-handoff", "read-receipts", "worker-hidden-events"), "consume_assignment_evidence", "consume_assignment_evidence", ("evidence_cursor",), ("assignment_ref",), anchor="assignment_ref", handler_name="consume_assignment_evidence", safe_errors=("assignment_not_found", "evidence_cursor_invalid", "evidence_scope_mismatch", "dispatch_lease_expired")),
    OperationSpec("publish_plan", "command", ("planning", "plan-approval", "publication-completeness", "contract-coverage"), "publish_plan", "publish_plan", ("plan_publication_ref",), ("assignment_ref", "report_evidence"), anchor="assignment_ref", logical_slot="publication.plan", handler_name="publish_plan", safe_errors=("report_incomplete", "input_evidence_unread", "publication_conflict")),
    OperationSpec("publish_result", "command", ("implementation", "verification", "publication-completeness", "atomic-publication"), "publish_result", "publish_result", ("result_publication_ref",), ("assignment_ref", "report_evidence"), anchor="assignment_ref", logical_slot="publication.result", handler_name="publish_result", safe_errors=("report_incomplete", "input_evidence_unread", "publication_conflict")),
    OperationSpec("publish_documentation", "command", ("documentation-impact", "documentation-sync", "publication-completeness", "atomic-publication"), "publish_documentation", "publish_documentation", ("documentation_publication_ref",), ("assignment_ref", "report_evidence"), anchor="assignment_ref", logical_slot="publication.documentation", handler_name="publish_documentation", safe_errors=("report_incomplete", "input_evidence_unread", "publication_conflict")),
    OperationSpec("assess_governance", "command", ("governance", "initiative-materiality", "risk"), "assess_governance", "assess_governance", ("governance_assessment_ref",), ("task_ref",), logical_slot="governance.assessment", handler_name="assess_governance", safe_errors=("task_not_found", "initiative_revision_not_material", "decision_pending")),
    OperationSpec("close_task", "command", ("closure", "follow-ups", "unresolved-risks"), "close_task", "close_task", ("closure_ref",), ("task_ref", "plan_publication_ref", "result_publication_ref", "documentation_publication_ref"), logical_slot="task.closure", handler_name="close_task", safe_errors=("closure_not_ready", "task_not_found", "decision_pending")),
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
    "concurrency-replay", "lost-response-reconciliation", "server-handles",
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
