"""Domain Kernel foundation for Cortex semantic commands and queries.

The kernel is deliberately an intent boundary in this migration step.  It
validates coordinator intent and defines receipt integration points, but does
not select workers/models, construct a DAG, ask users questions, or decide
governance/rework.  Existing domain_api handlers remain authoritative until a
vertical slice explicitly moves behind this interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
import hashlib
import json
from typing import Any, Callable, Generic, Protocol, TypeVar

from cortex_runtime.semantic_registry import OperationSpec, spec_for
from cortex_runtime.v12_store import V12StoreError

T = TypeVar("T")


@dataclass(frozen=True)
class CommandContext:
    operation: str
    task_ref: str | None = None
    project_root: str | None = None
    request_digest: str | None = None
    build_id: str | None = None
    candidate_parity_verified: bool = False


@dataclass(frozen=True)
class QueryContext(CommandContext):
    after_sequence: int = 0


@dataclass(frozen=True)
class AggregateState:
    aggregate_type: str
    aggregate_id: str
    state: str
    revision: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DomainError:
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KernelResult(Generic[T]):
    value: T | None = None
    error: DomainError | None = None
    receipt_ref: str | None = None
    replayed: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


class ReceiptStore(Protocol):
    """Persistence seam implemented by the command-receipt store."""

    def lookup_command_receipt(self, logical_slot: object) -> Mapping[str, Any] | None: ...

    def run_command_receipt(
        self, *, aggregate_type: object, aggregate_id: object, command_name: object,
        logical_slot: object, request: Mapping[str, Any],
        mutate: Callable[[Any], Mapping[str, Any]], build_id: object | None = None,
    ) -> tuple[dict[str, Any], bool]: ...


def validate_intent(spec: OperationSpec, payload: Mapping[str, Any]) -> DomainError | None:
    """Validate only boundary-level intent; semantic state stays in handlers."""
    if spec.kind not in {"command", "query"}:
        return DomainError("registry_error", f"Unsupported operation kind: {spec.kind}")
    if not isinstance(payload, Mapping):
        return DomainError("validation_error", "Coordinator intent must be an object")
    if spec.anchor and spec.anchor != "project_root" and not payload.get(spec.anchor):
        return DomainError("validation_error", f"Missing task-scoped anchor for {spec.name}")
    return None


def validate_coordinator_intent(operation: str, payload: Mapping[str, Any]) -> DomainError | None:
    """Resolve registry metadata and validate a coordinator-supplied intent."""
    try:
        spec = spec_for(operation)
    except KeyError:
        return DomainError("unknown_operation", f"Unknown Cortex semantic operation: {operation}")
    return validate_intent(spec, payload)


class DomainKernel:
    """The single adapter between coordinator intent and durable commands.

    This is intentionally a thin adapter: it performs common admission and
    identity work, while a vertical slice supplies the aggregate identifiers,
    logical-slot policy, and transactional mutation.  It does not select
    workers, answer users, or make orchestration decisions.
    """

    def __init__(self, receipt_store: ReceiptStore | None = None) -> None:
        self.receipt_store = receipt_store

    def preflight(self, operation: str, payload: Mapping[str, Any]) -> DomainError | None:
        return validate_coordinator_intent(operation, payload)

    def operation(self, operation: str) -> OperationSpec:
        return spec_for(operation)

    @staticmethod
    def canonical_request(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Return a JSON-safe, deterministically ordered request envelope."""
        if not isinstance(payload, Mapping):
            raise TypeError("coordinator intent must be an object")
        value = json.loads(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False))
        if not isinstance(value, dict):  # defensive: json round-trip above
            raise TypeError("coordinator intent must be an object")
        return value

    @classmethod
    def request_digest(cls, payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(cls.canonical_request(payload), ensure_ascii=False,
                              sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def execute_command(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        aggregate_type: str,
        aggregate_id: str,
        logical_slot: str | Callable[[str, Mapping[str, Any]], str],
        mutate: Callable[[Any], Mapping[str, Any]],
        context: CommandContext | None = None,
    ) -> KernelResult[dict[str, Any]]:
        """Admit and atomically execute a command through the receipt store.

        ``logical_slot`` is deliberately supplied by the vertical-slice
        policy.  The kernel owns normalization, digesting, and receipt wiring;
        it must not guess a slot from arbitrary model payload fields.
        """
        spec = self.operation(operation)
        normalized: dict[str, Any]
        try:
            normalized = self.canonical_request(payload)
        except (TypeError, ValueError, OverflowError) as exc:
            return KernelResult(error=DomainError("validation_error", str(exc)))
        admission = validate_intent(spec, normalized)
        if admission is not None:
            return KernelResult(error=admission)
        if spec.kind != "command":
            return KernelResult(error=DomainError("registry_error", f"{operation} is not a command"))
        if self.receipt_store is None:
            return KernelResult(error=DomainError("receipt_store_unavailable", "Command receipt store is unavailable"))
        try:
            slot = logical_slot(operation, normalized) if callable(logical_slot) else logical_slot
            if not isinstance(slot, str) or not slot.strip():
                return KernelResult(error=DomainError("validation_error", "logical slot policy returned an empty slot"))
            build_id = context.build_id if context is not None else None
            value, replayed = self.receipt_store.run_command_receipt(
                aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                command_name=operation, logical_slot=slot,
                request=normalized, mutate=mutate, build_id=build_id,
            )
            receipt = self.receipt_store.lookup_command_receipt(slot)
            receipt_ref = str(receipt["command_ref"]) if receipt and receipt.get("command_ref") else None
            return KernelResult(value=value, receipt_ref=receipt_ref, replayed=replayed)
        except Exception as exc:  # store normalizes operational failures to typed errors
            code = str(getattr(exc, "code", "command_failed"))
            details = getattr(exc, "details", {})
            if not isinstance(details, dict):
                details = {}
            return KernelResult(error=DomainError(code, str(exc), retryable=False, details=details))

    # Explicit aliases keep the adapter discoverable without creating another
    # protocol or changing any existing semantic operation.
    run_command = execute_command

    def execute_query(
        self, operation: str, payload: Mapping[str, Any], *,
        query: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> KernelResult[dict[str, Any]]:
        """Execute a read-only query without command receipts."""
        spec = self.operation(operation)
        try:
            normalized = self.canonical_request(payload)
        except (TypeError, ValueError, OverflowError) as exc:
            return KernelResult(error=DomainError("validation_error", str(exc)))
        admission = validate_intent(spec, normalized)
        if admission is not None:
            return KernelResult(error=admission)
        if spec.kind != "query":
            return KernelResult(error=DomainError("registry_error", f"{operation} is not a query"))
        try:
            return KernelResult(value=dict(query(normalized)))
        except Exception as exc:
            return KernelResult(error=DomainError(str(getattr(exc, "code", "query_failed")), str(exc), details=getattr(exc, "details", {}) if isinstance(getattr(exc, "details", {}), dict) else {}))

    run_query = execute_query


class DecisionAggregate:
    """Transactional decision aggregate over the historical V12 ledger.

    The aggregate is the semantic cut-over point: callers provide intent and
    the kernel resolves the durable binding.  The legacy tables remain the
    source of historical evidence, but decision commands and their receipts
    now share one ``BEGIN IMMEDIATE`` transaction.
    """

    def __init__(self, store: Any) -> None:
        self.store = store

    _FAMILY_OPERATIONS = {
        "clarification": ("open_clarification", "record_clarification", "clarification"),
        "closure_review": ("open_clarification", "record_clarification", "closure_review"),
        "plan_review": ("open_plan_review", "record_plan_review", "plan_review"),
        "steering": ("open_steering", "record_steering", "steer"),
    }

    @classmethod
    def _family_operation(cls, family: str, index: int) -> str:
        try:
            return cls._FAMILY_OPERATIONS[family][index]
        except KeyError as exc:
            raise V12StoreError("decision family is invalid", code="invalid_decision_subject") from exc

    @staticmethod
    def _slot(prefix: str, values: Mapping[str, Any]) -> str:
        encoded = json.dumps(dict(values), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return prefix + "/" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def open(
        self, *, task_id: str, prompt: str, prompt_language: str,
        subject_type: str = "task", subject_id: str | None = None,
        assignment_id: str | None = None, decision_type: str = "clarification",
        family: str = "clarification",
    ) -> dict[str, Any]:
        def resolve(connection: Any) -> tuple[str, Mapping[str, Any], Callable[[Any], Mapping[str, Any]]]:
            # The revision is intentionally read only after the receipt
            # transaction's BEGIN IMMEDIATE.  Slot identity, binding issue,
            # and the persisted binding revision therefore share one snapshot.
            revision = int(self.store._effective_contract(connection, task_id)["revision"])
            closure_generation = None
            if family == "closure_review":
                row = connection.execute(
                    "SELECT COALESCE(MAX(sequence),0) AS sequence FROM timeline WHERE task_id=?",
                    (task_id,),
                ).fetchone()
                closure_generation = int(row["sequence"] if row is not None else 0)
            request = {
                "task_id": task_id, "prompt": prompt, "prompt_language": prompt_language,
                "subject_type": subject_type, "subject_id": subject_id,
                "assignment_id": assignment_id, "decision_type": decision_type,
                "contract_revision": revision,
                **({"closure_generation": closure_generation} if closure_generation is not None else {}),
            }
            slot = self._slot("decision/pending", {
                "task_id": task_id, "subject_type": subject_type,
                "subject_id": subject_id or task_id, "assignment_id": assignment_id,
                "decision_type": decision_type, "prompt": prompt,
                "prompt_language": prompt_language, "contract_revision": revision,
                **({"closure_generation": closure_generation} if closure_generation is not None else {}),
            })
            def mutate(active: Any) -> Mapping[str, Any]:
                issued = self.store.issue_clarification_binding(
                    task_id=task_id, prompt=prompt, prompt_language=prompt_language,
                    subject_type=subject_type, subject_id=subject_id,
                    assignment_id=assignment_id, decision_type=decision_type,
                    _connection=active,
                )
                # A clarification hold is a typed lifecycle relation, not a
                # second decision family.  It must be created/replayed in the
                # same transaction as the binding, before a coordinator may
                # render the corresponding product question.
                if family in {"clarification", "closure_review"}:
                    binding = issued.get("binding")
                    if not isinstance(binding, Mapping) or not isinstance(binding.get("clarification_binding"), str):
                        raise V12StoreError("clarification binding is unavailable", code="ledger_error")
                    value = dict(issued)
                    value["clarification_hold"] = self.store.open_clarification_hold(
                        task_id=task_id, binding_ref=str(binding["clarification_binding"]),
                        assignment_id=assignment_id, connection=active,
                    )
                    return value
                return issued
            return slot, request, mutate

        result, replayed = self.store.run_command_receipt_resolved(
            aggregate_type="task", aggregate_id=task_id,
            command_name=self._family_operation(family, 0),
            resolve=resolve,
        )
        if replayed:
            binding = result.get("binding") if isinstance(result, Mapping) else None
            binding_ref = binding.get("clarification_binding") if isinstance(binding, Mapping) else None
            if isinstance(binding_ref, str):
                result = dict(result)
                result["binding"] = self.store.read_decision_binding(
                    task_id=task_id, binding_ref=binding_ref,
                )
        value = dict(result)
        value["replayed"] = replayed
        return value

    # Family-specific entry points deliberately share the aggregate
    # transaction implementation but expose distinct semantic commands to
    # the MCP registry.  Keeping these names here prevents a new overloaded
    # discriminator API from being reintroduced at the adapter layer.
    def open_clarification(self, **kwargs: Any) -> dict[str, Any]:
        kwargs["family"] = "clarification"
        kwargs["decision_type"] = self._family_operation("clarification", 2)
        return self.open(**kwargs)

    def open_closure_review(self, **kwargs: Any) -> dict[str, Any]:
        kwargs["family"] = "closure_review"
        kwargs["decision_type"] = self._family_operation("closure_review", 2)
        return self.open(**kwargs)

    def open_plan_review(self, **kwargs: Any) -> dict[str, Any]:
        kwargs["family"] = "plan_review"
        kwargs["decision_type"] = self._family_operation("plan_review", 2)
        return self.open(**kwargs)

    def open_steering(self, **kwargs: Any) -> dict[str, Any]:
        kwargs["family"] = "steering"
        kwargs["decision_type"] = self._family_operation("steering", 2)
        return self.open(**kwargs)

    def record(
        self, *, task_id: str, binding_ref: str, response_original: str,
        user_language: str, subject_digest: str | None = None,
        decision_type_override: str | None = None,
        approval_handle: str | None = None,
        approval_view_content_digest: str | None = None,
        approval_view_source_sequence: int | None = None,
        supersedes_decision_id: str | None = None,
        steering_delta: Mapping[str, Any] | None = None,
        family: str = "clarification",
    ) -> dict[str, Any]:
        slot = "decision/consumed/" + binding_ref
        request = {
            "task_id": task_id, "binding_ref": binding_ref,
            "response_original": response_original, "user_language": user_language,
            "decision_type": decision_type_override,
            "subject_digest": subject_digest,
            "approval_handle": approval_handle,
            "approval_view_content_digest": approval_view_content_digest,
            "approval_view_source_sequence": approval_view_source_sequence,
            "supersedes_decision_id": supersedes_decision_id,
            # Canonical request normalization performed by the receipt store
            # covers the complete semantic steering intent, including nested
            # additions and the supersession relation.
            "steering_delta": steering_delta,
        }
        def mutate(connection: Any) -> Mapping[str, Any]:
            # Resolve the binding inside the same BEGIN IMMEDIATE transaction
            # that consumes it.  No preflight read may race the decision write.
            row = connection.execute(
                "SELECT subject_type,subject_id,decision_type,prompt,prompt_language,plan_content_digest,plan_approval_handle,plan_view_content_digest,plan_view_source_sequence "
                "FROM clarification_bindings WHERE clarification_binding=? AND project_hash=?",
                (binding_ref, self.store.project_hash),
            ).fetchone()
            if row is None:
                raise V12StoreError("decision binding was not found", code="clarification_binding_not_found")
            # Plan review relation is fixed at binding issuance.  Record-time
            # code only reads these persisted values; it never selects a newer
            # approval handle or current projection view.
            resolved_subject_digest = subject_digest
            resolved_approval_handle = approval_handle
            resolved_view_digest = approval_view_content_digest
            resolved_view_sequence = approval_view_source_sequence
            if str(row["subject_type"]) == "plan":
                if str(row["decision_type"]) == "plan_review":
                    if any(row[name] is None for name in ("plan_content_digest", "plan_approval_handle", "plan_view_content_digest", "plan_view_source_sequence")):
                        raise V12StoreError("plan review binding relation is unavailable", code="approval_view_required")
                    resolved_subject_digest = str(row["plan_content_digest"])
                    resolved_approval_handle = str(row["plan_approval_handle"])
                    resolved_view_digest = str(row["plan_view_content_digest"])
                    resolved_view_sequence = int(row["plan_view_source_sequence"])
                else:
                    # Ordinary plan clarification is still digest-bound, but
                    # it has no approval-view relation and must not inherit
                    # plan-review validation semantics.
                    resolved_subject_digest = str(self.store._report(
                        connection, str(row["subject_id"]), task_id=task_id,
                    )["content_digest"])
            value, _ = self.store.record_user_decision(
                task_id=task_id, subject_type=str(row["subject_type"]),
                subject_id=str(row["subject_id"]), decision_type=decision_type_override or str(row["decision_type"]),
                prompt=str(row["prompt"]), response_original=response_original,
                user_language=str(row["prompt_language"]), clarification_binding=binding_ref,
                subject_digest=resolved_subject_digest,
                approval_handle=resolved_approval_handle,
                approval_view_content_digest=resolved_view_digest,
                approval_view_source_sequence=resolved_view_sequence,
                supersedes_decision_id=supersedes_decision_id,
                steering_delta=steering_delta,
                _connection=connection,
            )
            if family in {"clarification", "closure_review"}:
                decision = value.get("decision")
                if not isinstance(decision, Mapping) or not isinstance(decision.get("decision_id"), str):
                    raise V12StoreError("clarification decision is unavailable", code="ledger_error")
                result = dict(value)
                result["clarification_hold"] = self.store.answer_clarification_hold(
                    task_id=task_id, binding_ref=binding_ref,
                    decision_id=str(decision["decision_id"]), connection=connection,
                )
                return result
            return value
        result, replayed = self.store.run_command_receipt(
            aggregate_type="task", aggregate_id=task_id,
            command_name=self._family_operation(family, 1),
            logical_slot=slot, request=request, mutate=mutate,
        )
        value = dict(result)
        value["replayed"] = replayed
        return value

    def record_clarification(self, **kwargs: Any) -> dict[str, Any]:
        return self.record(family="clarification", **kwargs)

    def record_closure_review(self, *, outcome: str, **kwargs: Any) -> dict[str, Any]:
        if outcome not in {"revise", "close"}:
            raise V12StoreError("closure review outcome is invalid", code="invalid_argument")
        kwargs["decision_type_override"] = "request_revision" if outcome == "revise" else "approve"
        return self.record(family="closure_review", **kwargs)

    def record_plan_review(self, *, outcome: str, **kwargs: Any) -> dict[str, Any]:
        if outcome not in {"approve", "request_revision", "cancel"}:
            raise V12StoreError("plan review outcome is invalid", code="invalid_argument")
        # The binding's decision type is the durable family marker.  The
        # outcome is included in the receipt request so a changed semantic
        # assertion cannot replay an earlier result.
        kwargs["response_original"] = kwargs.get("response_original", "")
        kwargs["decision_type_override"] = outcome
        return self.record(family="plan_review", **kwargs)

    def record_steering(self, *, steering_delta: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self.record(family="steering", steering_delta=steering_delta, **kwargs)

    def reconcile(self, *, task_id: str, binding_ref: str) -> dict[str, Any]:
        """Read-only recovery projection; it never opens a new binding."""
        row = self.store._read(lambda c: c.execute(
            "SELECT clarification_binding,consumed_decision_id,response_digest,effective_contract_revision "
            "FROM clarification_bindings WHERE clarification_binding=? AND project_hash=? AND task_id=?",
            (binding_ref, self.store.project_hash, task_id),
        ).fetchone())
        if row is None:
            return {"state": "not_found", "binding_ref": binding_ref}
        state = "consumed" if row["consumed_decision_id"] else "pending"
        return {"state": state, "binding_ref": binding_ref,
                "decision_ref": row["consumed_decision_id"],
                "effective_contract_revision": int(row["effective_contract_revision"])}
