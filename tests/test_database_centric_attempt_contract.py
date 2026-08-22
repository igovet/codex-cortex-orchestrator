"""Regression contract for the database-centric worker/attempt protocol.

This suite intentionally targets one small runtime seam instead of an
editable worker-authored transport document.  The implementation may choose where the service
lives, but it should expose a factory named ``build_contract`` in one of the
candidate modules below (or set ``CORTEX_ATTEMPT_CONTRACT_FACTORY`` to an
explicit ``module:function`` path while integrating).

The factory returns an object with the following deliberately small methods::

    start_attempt(...)
    record_event(attempt_id, kind, payload)
    acknowledge_briefing(attempt_id, dispatch_ref, digest)
    read_predecessor(attempt_id, predecessor_result_ref)
    complete_attempt(attempt_id, result)
    finalize_attempt(attempt_id, status="completed")
    result_view(attempt_id)
    attempt(attempt_id)
    events(attempt_id)
    attempt_count()
    fail_next_finalization(attempt_id)

``fail_next_finalization`` is a deterministic test seam for an infrastructure
failure after worker work has completed.  It stands in for a projection or
serialization error and must not model a second worker execution.

Until the new service is present, tests skip with an actionable message.  This
keeps the contract file importable while the implementation is being developed
in parallel; once the factory exists, no editable worker-authored fixture is needed.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Mapping


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime import attempt_protocol  # noqa: E402


_FACTORY_CANDIDATES = (
    "cortex_runtime.attempt_contract:build_contract",
    "cortex_runtime.attempts:build_contract",
    "cortex_runtime.attempt_service:build_contract",
    "cortex_runtime.ledger_db:build_attempt_contract",
)

_MINIMAL_RESULT_KEYS = frozenset({
    "status",
    "summary",
    "findings",
    "decisions_needed",
    "unresolved",
})

# These belong to the server-owned AttemptRecord, never to the worker result.
_SERVER_OWNED_KEYS = frozenset({
    "attempt_id",
    "task_id",
    "task_revision",
    "dispatch_ref",
    "profile",
    "phase",
    "predecessors",
    "briefing_digest",
    "briefing_receipt",
    "predecessor_receipts",
    "changed_files",
    "checks",
    "timestamps",
    "started_at",
    "completed_at",
    "finalized_at",
})


def _factory_path() -> str:
    """Return the configured implementation seam, useful for local bring-up."""
    return os.environ.get(
        "CORTEX_ATTEMPT_CONTRACT_FACTORY",
        ",".join(_FACTORY_CANDIDATES),
    )


def _load_factory() -> Callable[..., Any]:
    """Load the first available DB-centric contract factory.

    Import errors are swallowed only while probing candidates.  Once a module
    is found, errors raised by its factory are intentionally allowed through so
    implementation failures are visible instead of being mislabeled as skips.
    """
    candidates = tuple(
        item.strip() for item in _factory_path().split(",") if item.strip()
    )
    import_errors: list[str] = []
    for candidate in candidates:
        if ":" not in candidate:
            import_errors.append(f"{candidate!r} must use module:function syntax")
            continue
        module_name, function_name = candidate.split(":", 1)
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            # A dependency imported by an existing candidate is a real failure;
            # only the candidate module itself is absent during bring-up.
            if exc.name != module_name:
                raise
            import_errors.append(f"{candidate}: module not found")
            continue
        factory = getattr(module, function_name, None)
        if callable(factory):
            return factory
        import_errors.append(f"{candidate}: callable is missing")
    raise unittest.SkipTest(
        "database-centric AttemptResult contract factory is not available; "
        f"implement one of {_factory_path()!r} (probed: {'; '.join(import_errors)})"
    )


def _value(value: Any, key: str, default: Any = None) -> Any:
    """Read a field from either the dict-shaped or value-object-shaped seam."""
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


class DatabaseCentricAttemptContractTests(unittest.TestCase):
    """Executable invariants for AttemptResult and server-owned projections."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cortex-attempt-contract-")
        self.project_root = Path(self.temp.name)
        factory = _load_factory()
        try:
            self.service = factory(
                project_root=self.project_root,
                task_id="attempt-contract-task",
                principal="attempt-contract-test",
            )
        except TypeError as exc:
            raise AssertionError(
                "build_contract must accept project_root, task_id, and principal keyword arguments"
            ) from exc
        self.started = self.service.start_attempt(
            dispatch_ref="dispatch-contract-1",
            profile="backend",
            phase="implementation",
            task_revision=7,
            briefing_digest="briefing-sha-1",
            predecessor_result_refs=["predecessor-1"],
        )
        self.attempt_id = str(_value(self.started, "attempt_id"))
        self.assertTrue(self.attempt_id and self.attempt_id != "None")
        self.service.acknowledge_briefing(
            self.attempt_id,
            dispatch_ref="dispatch-contract-1",
            digest="briefing-sha-1",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _minimal_result(self, **overrides: Any) -> dict[str, Any]:
        result = {
            "status": "completed",
            "summary": "Implemented the change and observed the focused check pass.",
            "findings": [{"summary": "No blocking finding."}],
            "decisions_needed": [],
            "unresolved": [],
        }
        result.update(overrides)
        return result

    def _complete_work(self) -> Any:
        return self.service.complete_attempt(
            self.attempt_id,
            self._minimal_result(),
        )

    def test_attempt_result_accepts_only_semantic_worker_payload(self) -> None:
        """Worker completion is small; identity and telemetry stay server-owned."""
        result = self._complete_work()
        self.assertNotEqual(_value(result, "recorded", True), False)
        stored = self.service.attempt(self.attempt_id)
        worker_result = (
            _value(stored, "worker_result")
            or _value(stored, "attempt_result")
            or _value(stored, "result")
        )
        self.assertIsInstance(worker_result, Mapping)
        self.assertTrue(
            set(worker_result).issubset(_MINIMAL_RESULT_KEYS),
            f"worker result leaked non-semantic keys: {set(worker_result) - _MINIMAL_RESULT_KEYS}",
        )
        self.assertTrue(
            set(worker_result) & {"summary", "findings"},
            "a semantic completion must retain summary/findings rather than an unbounded worker dump",
        )

    def test_completed_result_preserves_successor_unresolved_handoff(self) -> None:
        """Completed describes scoped work; successor handoff items stay immutable."""
        unresolved = [{"summary": "Governance close must resolve the documented risk."}]
        self.service.complete_attempt(
            self.attempt_id,
            self._minimal_result(unresolved=unresolved),
        )
        stored = self.service.attempt(self.attempt_id)
        self.assertEqual(_value(stored, "worker_result")["status"], "completed")
        self.assertEqual(_value(stored, "worker_result")["unresolved"], unresolved)

    def test_blocked_result_preserves_concrete_blockers(self) -> None:
        """Blocked workers retain their concrete blockers for the corrective route."""
        unresolved = [{"summary": "Await the owner decision for the incompatible API."}]
        completed = self.service.complete_attempt(
            self.attempt_id,
            self._minimal_result(
                status="blocked",
                summary="The scoped work is blocked pending one owner decision.",
                unresolved=unresolved,
            ),
        )
        self.assertEqual(_value(completed, "status"), attempt_protocol.LIFECYCLE_BLOCKED)
        self.assertEqual(_value(self.service.attempt(self.attempt_id), "worker_result")["unresolved"], unresolved)

    def test_server_observed_fields_cannot_be_spoofed_by_worker(self) -> None:
        """Spoofed dispatch/profile/files are rejected or ignored, never authoritative."""
        spoof = self._minimal_result(
            dispatch_ref="worker-spoof",
            profile="worker-spoof",
            changed_files=["not-observed.txt"],
            task_revision=999,
            briefing_receipt="fake-receipt",
        )
        try:
            self.service.complete_attempt(self.attempt_id, spoof)
        except (TypeError, ValueError):
            # Rejecting server-owned fields is valid.  Finish with the clean
            # semantic payload so the assertions below still inspect durable state.
            self._complete_work()
        stored = self.service.attempt(self.attempt_id)
        for key in _SERVER_OWNED_KEYS:
            self.assertNotEqual(
                _value(stored, key),
                spoof.get(key),
                f"worker supplied {key!r} became authoritative",
            )

    def test_incremental_events_survive_failed_finalization(self) -> None:
        """A projection/finalization failure cannot erase already persisted events."""
        self.service.record_event(
            self.attempt_id,
            "finding_added",
            {"summary": "Observed an incremental fact before completion."},
        )
        self.service.record_event(
            self.attempt_id,
            "verification_claimed",
            {"command": "pytest -q", "exit_code": 0},
        )
        self._complete_work()
        self.service.fail_next_finalization(self.attempt_id)
        failed = self.service.finalize_attempt(self.attempt_id, status="completed")
        self.assertIn(_value(failed, "status"), {"failed", "retryable", "finalization_failed"})
        events = list(self.service.events(self.attempt_id))
        kinds = [_value(event, "kind") for event in events]
        self.assertIn("finding_added", kinds)
        self.assertIn("verification_claimed", kinds)
        retried = self.service.finalize_attempt(self.attempt_id, status="completed")
        self.assertNotEqual(_value(retried, "recorded", True), False)
        self.assertEqual(str(_value(self.service.attempt(self.attempt_id), "attempt_id")), self.attempt_id)

    def test_attempt_result_view_is_regenerable_from_canonical_rows(self) -> None:
        """The result view is derived from AttemptResult/Event rows only."""
        self.service.record_event(
            self.attempt_id,
            "verification_claimed",
            {"command": "pytest -q", "exit_code": 0, "evidence": "1 passed"},
        )
        self._complete_work()
        view = attempt_protocol.build_attempt_result_view(
            self.service.root,
            task_id=self.service.task_id,
            attempt_id=self.attempt_id,
        )
        self.assertIsInstance(view, Mapping)
        self.assertEqual(view["attempt_result_ref"], view["result"]["result_ref"])
        self.assertEqual(view["result"]["summary"], self._minimal_result()["summary"])
        self.assertIn("verification_claimed", [event["event_type"] for event in view["events"]])
        self.assertNotIn("worker-authored projection body", str(view))

    def test_briefing_and_predecessor_receipts_are_machine_side(self) -> None:
        """Completion needs durable reads, not prose pretending to be a server receipt."""
        self._complete_work()
        try:
            blocked = self.service.finalize_attempt(self.attempt_id, status="completed")
        except (PermissionError, ValueError):
            # A machine-side guard may reject the close with an exception.
            blocked = {"recorded": False}
        self.assertFalse(
            _value(blocked, "recorded", True),
            "finalization must not trust absent briefing/predecessor receipts",
        )
        self.service.acknowledge_briefing(
            self.attempt_id,
            dispatch_ref="dispatch-contract-1",
            digest="briefing-sha-1",
        )
        self.service.read_predecessor(self.attempt_id, "predecessor-1")
        # No evidence marker, template, or predecessor prose is sent.
        finalized = self.service.finalize_attempt(self.attempt_id, status="completed")
        self.assertNotEqual(_value(finalized, "recorded", True), False)
        stored = self.service.attempt(self.attempt_id)
        self.assertTrue(_value(stored, "briefing_receipt") or _value(stored, "briefing_acknowledged"))
        receipts = _value(stored, "predecessor_receipts")
        self.assertIn("predecessor-1", receipts or {})

    def test_finalization_failure_does_not_consume_new_worker_or_attempt(self) -> None:
        """Infrastructure retry reuses the completed attempt instead of respawning."""
        before = int(self.service.attempt_count())
        self._complete_work()
        self.service.fail_next_finalization(self.attempt_id)
        self.service.finalize_attempt(self.attempt_id, status="completed")
        after_failure = int(self.service.attempt_count())
        self.assertEqual(after_failure, before)
        self.service.finalize_attempt(self.attempt_id, status="completed")
        self.assertEqual(int(self.service.attempt_count()), before)
        self.assertEqual(str(_value(self.service.attempt(self.attempt_id), "attempt_id")), self.attempt_id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
