"""Declarative boundary for the database-centric attempt protocol.

The stdio/MCP facade uses this module to expose strict worker operations
without importing internal tables or editable worker-authored documents.
Keeping the wiring here also gives adapters one explicit failure seam: after
``WORK_COMPLETED`` a handoff/materialization failure is finalization work, never a new
worker execution attempt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from cortex_runtime import attempt_protocol


CONTRACT_SCHEMA = "cortex/attempt-completion-contract/v1"


def _definition(root: Path, *, task_id: str, principal: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a small real-ledger task for an embedded strict-protocol client."""
    ledger_db = attempt_protocol.ledger_db
    ledger_db.ensure_database(root)
    loaded = ledger_db.load_task(root, task_id)
    if loaded is not None:
        definition, state, _plan, _artifact_dir = loaded
        return definition, state
    definition = {
        "schema": "cortex/attempt-contract-task/v1",
        "task_id": task_id,
        "project_root": str(root.parent),
        "principal": principal,
    }
    state = {
        "schema": "cortex/attempt-contract-task/v1",
        "task_id": task_id,
        "task_number": 1,
        "status": "active",
        "principal": principal,
        "revision": 0,
        "attempts": [],
    }
    ledger_db.create_task(root, definition, state, f"tasks/0001-{task_id}")
    return definition, state


@dataclass
class AttemptContract:
    """Small stateful adapter for strict transports and embedded callers.

    It stores state in the same SQLite tables as production adapters.  The
    ``fail_next_finalization`` method is deterministic fault injection for a
    materialization boundary: it deliberately exercises the durable retry
    path without pretending that a second worker executed the assignment.
    """

    root: Path
    task_id: str
    principal: str
    _finalization_faults: set[str] = field(default_factory=set)

    def _state(self) -> tuple[dict[str, Any], dict[str, Any]]:
        loaded = attempt_protocol.ledger_db.load_task(self.root, self.task_id)
        if loaded is None:
            raise ValueError("attempt contract task is unavailable")
        definition, state, _plan, _artifact_dir = loaded
        return definition, state

    def start_attempt(
        self,
        *,
        dispatch_ref: str,
        profile: str,
        phase: str,
        task_revision: int,
        briefing_digest: str,
        predecessor_result_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create one delegated attempt with server-owned dispatch metadata."""
        if not all(isinstance(value, str) and value.strip() for value in (dispatch_ref, profile, phase, briefing_digest)):
            raise ValueError("attempt contract dispatch metadata is invalid")
        if isinstance(task_revision, bool) or not isinstance(task_revision, int) or task_revision < 1:
            raise ValueError("attempt contract task_revision is invalid")
        _definition, state = self._state()
        next_number = len(state.get("attempts") or []) + 1
        attempt_id = f"attempt-{next_number:04d}"
        if any(str(item.get("dispatch_ref") or "") == dispatch_ref for item in state.get("attempts") or [] if isinstance(item, dict)):
            raise ValueError("attempt contract dispatch_ref already exists")
        refs = [str(item) for item in predecessor_result_refs or [] if isinstance(item, str) and item]
        if len(refs) != len(set(refs)):
            raise ValueError("attempt contract predecessor refs must be unique")
        state["task_revision"] = task_revision
        state["revision"] = max(int(state.get("revision") or 0), task_revision)
        state.setdefault("attempts", []).append({
            "attempt_id": attempt_id,
            "gate": phase,
            "profile": profile,
            "agent": profile,
            "dispatch_ref": dispatch_ref,
            "briefing_digest": briefing_digest,
            "result_baseline_ref": None,
            "result_baseline_digest": None,
            "predecessor_result_refs": refs,
            "status": "running",
        })
        attempt_protocol.ledger_db.update_task_state(self.root, state, event="attempt_contract_started", detail=attempt_id)
        return {
            "attempt_id": attempt_id,
            "task_id": self.task_id,
            "dispatch_ref": dispatch_ref,
            "profile": profile,
            "phase": phase,
            "task_revision": task_revision,
        }

    def record_event(self, attempt_id: str, kind: str, payload: Any) -> dict[str, Any]:
        result = attempt_protocol.record_attempt_event(
            self.root,
            task_id=self.task_id,
            attempt_id=attempt_id,
            event_type=kind,
            payload=payload,
        )
        return {"recorded": True, "kind": result["event"]["event_type"], **result}

    def acknowledge_briefing(self, attempt_id: str, *, dispatch_ref: str, digest: str) -> dict[str, Any]:
        result = attempt_protocol.acknowledge_briefing(
            self.root,
            task_id=self.task_id,
            attempt_id=attempt_id,
            dispatch_ref=dispatch_ref,
            digest=digest,
        )
        return {"recorded": True, **result}

    def read_predecessor(self, attempt_id: str, predecessor_result_ref: str) -> dict[str, Any]:
        result = attempt_protocol.record_predecessor_read(
            self.root,
            task_id=self.task_id,
            attempt_id=attempt_id,
            predecessor_result_ref=predecessor_result_ref,
        )
        return {"recorded": True, **result}

    def complete_attempt(self, attempt_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
        outcome = attempt_protocol.complete_attempt(
            self.root,
            task_id=self.task_id,
            attempt_id=attempt_id,
            result=result,
        )
        return {"recorded": True, "status": outcome["result"]["lifecycle_status"], **outcome}

    def finalize_attempt(self, attempt_id: str, *, status: str = "completed") -> dict[str, Any]:
        if status != "completed":
            raise ValueError("attempt contract finalization status must be completed")
        if attempt_id in self._finalization_faults:
            self._finalization_faults.remove(attempt_id)
            failure = attempt_protocol.record_finalization_failure(
                self.root,
                task_id=self.task_id,
                attempt_id=attempt_id,
                reason_code="injected_projection_failure",
            )
            return {"recorded": False, "status": "retryable", "failure": failure}
        receipts = attempt_protocol.attempt_receipts(
            self.root, task_id=self.task_id, attempt_id=attempt_id,
        )
        _definition, state = self._state()
        attempt = next(
            (item for item in state.get("attempts") or [] if isinstance(item, dict) and item.get("attempt_id") == attempt_id),
            None,
        )
        if attempt is None:
            raise ValueError("attempt contract attempt is unavailable")
        required_predecessors = set(str(item) for item in attempt.get("predecessor_result_refs") or [])
        received_predecessors = set((receipts.get("predecessor_receipts") or {}).keys())
        prior_finalization_failure = any(
            event.get("kind") == "finalization_failed" for event in self.events(attempt_id)
        )
        # A recorded finalization failure is emitted only after the normal
        # production adapter has accepted its receipt preconditions.  The
        # deterministic local fault seam models that already-past point, so
        # retrying it must not turn a projection outage into a worker retry.
        if (
            not prior_finalization_failure
            and (receipts.get("briefing_receipt") is None or not required_predecessors.issubset(received_predecessors))
        ):
            return {"recorded": False, "status": "blocked", "reason": "required_receipts_missing"}
        outcome = attempt_protocol.finalize_attempt(
            self.root, task_id=self.task_id, attempt_id=attempt_id,
        )
        return {"recorded": True, "status": "completed", **outcome}

    def result_view(self, attempt_id: str) -> dict[str, Any]:
        return attempt_protocol.build_attempt_result_view(
            self.root, task_id=self.task_id, attempt_id=attempt_id,
        )

    def attempt(self, attempt_id: str) -> dict[str, Any]:
        result = attempt_protocol.get_attempt_result(self.root, task_id=self.task_id, attempt_id=attempt_id)
        _definition, state = self._state()
        attempt = next(
            (item for item in state.get("attempts") or [] if isinstance(item, dict) and item.get("attempt_id") == attempt_id),
            None,
        )
        if attempt is None:
            raise ValueError("attempt contract attempt is unavailable")
        if result is None:
            return {"attempt_id": attempt_id, "status": "RUNNING", "phase": attempt.get("gate")}
        receipts = attempt_protocol.attempt_receipts(self.root, task_id=self.task_id, attempt_id=attempt_id)
        worker_result = {
            key: result[key]
            for key in ("status", "summary", "findings", "decisions_needed", "unresolved")
        }
        return {
            "attempt_id": attempt_id,
            "task_id": self.task_id,
            "worker_result": worker_result,
            "attempt_result": worker_result,
            "status": result["lifecycle_status"],
            "phase": result["metadata"].get("phase"),
            "profile": (result["metadata"].get("identity") or {}).get("profile"),
            "dispatch_ref": (result["metadata"].get("identity") or {}).get("dispatch_ref"),
            "task_revision": result["metadata"].get("task_revision"),
            "predecessors": result["metadata"].get("predecessor_result_refs"),
            "briefing_digest": (result["metadata"].get("briefing") or {}).get("digest_sha256"),
            "briefing_receipt": receipts.get("briefing_receipt"),
            "predecessor_receipts": receipts.get("predecessor_receipts"),
            "changed_files": result["changed_files"],
            "checks": [item for item in self.events(attempt_id) if item.get("kind") == "verification_observed"],
            "started_at": attempt.get("started_at") or result["created_at"],
            "completed_at": result.get("work_completed_at") or "",
            "finalized_at": result.get("completed_at") or "",
            "timestamps": {
                "started_at": attempt.get("started_at") or result["created_at"],
                "completed_at": result.get("work_completed_at") or "",
                "finalized_at": result.get("completed_at") or "",
            },
        }

    def events(self, attempt_id: str) -> list[dict[str, Any]]:
        return [
            {"kind": event["event_type"], **event}
            for event in attempt_protocol.list_attempt_events(
                self.root, task_id=self.task_id, attempt_id=attempt_id,
            )
        ]

    def attempt_count(self) -> int:
        _definition, state = self._state()
        return sum(1 for item in state.get("attempts") or [] if isinstance(item, dict))

    def fail_next_finalization(self, attempt_id: str) -> None:
        if attempt_protocol.get_attempt_result(self.root, task_id=self.task_id, attempt_id=attempt_id) is None:
            raise ValueError("attempt contract result is unavailable for finalization fault")
        self._finalization_faults.add(attempt_id)


def build_contract(*, project_root: Path | str, task_id: str, principal: str) -> AttemptContract:
    """Build a small durable adapter around the strict attempt primitives.

    The adapter is used by transport integration and local health/evaluation
    harnesses.  It creates no editable worker documents and all state is persisted in the
    normal SQLite ledger below the supplied isolated project root.
    """
    project = Path(project_root)
    if not task_id or not principal:
        raise ValueError("attempt contract task_id and principal are required")
    root = project / ".codex" / "attempt-contract"
    _definition(root, task_id=task_id, principal=principal)
    return AttemptContract(root=root, task_id=task_id, principal=principal)


def contract_description() -> dict[str, Any]:
    """Return static strict-operation metadata for MCP schema builders."""
    return {
        "schema": CONTRACT_SCHEMA,
        "service": {
            "record_attempt_event": attempt_protocol.record_attempt_event,
            "complete_attempt": attempt_protocol.complete_attempt,
            "begin_attempt_finalization": attempt_protocol.begin_attempt_finalization,
            "record_finalization_failure": attempt_protocol.record_finalization_failure,
            "finalize_attempt": attempt_protocol.finalize_attempt,
            "build_attempt_result_view": attempt_protocol.build_attempt_result_view,
            "get_attempt_result": attempt_protocol.get_attempt_result,
            "list_attempt_events": attempt_protocol.list_attempt_events,
        },
        "operations": {
            "record_attempt_event": {
                "required": ["task_id", "attempt_id", "event_type", "payload"],
                "optional": ["event_key"],
                "event_types": sorted(attempt_protocol.WORKER_EVENT_TYPES),
                "persistence": "append_only_attempt_events",
            },
            "complete_attempt": {
                "required": ["task_id", "attempt_id", "status", "summary"],
                "optional": [
                    "findings", "decisions_needed", "unresolved", "claims", "submission_id",
                ],
                "server_only": ["workspace_observation"],
                "status_values": sorted(attempt_protocol.RESULT_STATUSES),
                "persistence": "canonical_attempt_result",
            },
        },
        "lifecycle": {
            "running": "RUNNING",
            "successful_completion": [
                attempt_protocol.LIFECYCLE_WORK_COMPLETED,
                attempt_protocol.LIFECYCLE_FINALIZING,
                attempt_protocol.LIFECYCLE_COMPLETED,
            ],
            "worker_terminal_results": {
                "blocked": attempt_protocol.LIFECYCLE_BLOCKED,
                "failed": attempt_protocol.LIFECYCLE_FAILED,
            },
        },
        "failure_seam": {
            "after": attempt_protocol.LIFECYCLE_WORK_COMPLETED,
            "projection_failure": {
                "service": "record_finalization_failure",
                "retry": "finalize_attempt",
                "replacement_worker": False,
            },
            "source_of_truth": "attempt_results_and_attempt_events",
            "result_view": "build_attempt_result_view",
        },
    }
