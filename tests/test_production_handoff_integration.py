"""Production successor-handoff integration coverage.

The unit compiler tests deliberately pass hand-authored canonical objects.
This regression instead enters through the public orchestration lifecycle,
persists real AttemptResult/Event rows, and proves that the next
immutable briefing and compaction snapshot use a target-specific bounded
HandoffCompiler projection rather than a generic predecessor result body.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from tests.cortex_test_support import HostPrivateControlStoreTestMixin

import cortex as control  # noqa: E402
from cortex_runtime import attempt_protocol, delegation_service, mcp_api  # noqa: E402


class ProductionHandoffIntegrationTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    """Exercise start → strict completion → continue → inspect end to end."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="cortex-production-handoff-")
        self.set_up_host_private_control_store()
        self.project = Path(self._temporary.name) / "project"
        self.project.mkdir()
        self.ledger = control.ledger_root_path({"project_root": str(self.project)})

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()
        self._temporary.cleanup()

    @staticmethod
    def _assignment(response: dict[str, object], index: int = 0) -> dict[str, object]:
        dispatch = response["dispatches"][index]
        assert isinstance(dispatch, dict)
        prompt = Path(str(dispatch["briefing_path"])).read_text(encoding="utf-8")
        matched = re.search(
            r"## Assignment data \(untrusted task data\)\n```json\n(.*?)\n```",
            prompt,
            flags=re.DOTALL,
        )
        if matched is None:
            raise AssertionError("immutable briefing has no assignment JSON")
        return json.loads(matched.group(1))

    def _task_state(self) -> tuple[Path, dict[str, object]]:
        task_dir = next((self.ledger / "tasks").iterdir())
        return task_dir, control.load_task_state_for_artifact(task_dir)

    def _active_attempt(self) -> tuple[Path, dict[str, object], dict[str, object]]:
        task_dir, state = self._task_state()
        active = next(
            item for item in state["attempts"]
            if isinstance(item, dict) and item.get("status") in {control.AWAITING_HOST_SPAWN, "running"}
        )
        return task_dir, state, active

    def _read_briefing(self, state: dict[str, object], attempt: dict[str, object]) -> None:
        result = control.read_dispatch_briefing({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "dispatch_ref": attempt["dispatch_ref"],
            "briefing_digest": attempt["briefing_digest"],
        })
        self.assertTrue(result["ok"], result)

    def _read_predecessors(
        self,
        state: dict[str, object],
        attempt: dict[str, object],
        task_ref: str,
    ) -> None:
        for result_ref in attempt.get("context_result_refs") or []:
            result = control.read_worker_result({
                "project_root": str(self.project),
                "task_ref": task_ref,
                "attempt_result_ref": result_ref,
                "attempt_id": attempt["attempt_id"],
                "profile": attempt["profile"],
            })
            self.assertTrue(result["ok"], result)

    def _complete_strict(
        self,
        state: dict[str, object],
        attempt: dict[str, object],
        summary: str,
    ) -> str:
        completion = {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "status": "completed",
            "summary": summary,
            "findings": [],
            "decisions_needed": [],
            "unresolved": [],
            "claims": [{
                "criterion": "The requested observable outcome is completed end to end.",
                "evidence": "The focused production handoff test observed this completed phase.",
            }],
        }
        if str(attempt.get("gate") or "") == "plan":
            completion["planning"] = {
                "overview": "The production handoff plan is bounded and directly verifiable.",
                "work_packages": [{
                    "id": "handoff_core",
                    "title": "Handoff core",
                    "objective": "Exercise the next canonical production wave.",
                    "allowed_paths": ["tests"],
                    "depends_on": [],
                    "microtasks": [{
                        "id": "handoff_core_task",
                        "title": "Verify the handoff",
                        "objective": "Verify the exact predecessor contract.",
                        "profile": "backend_dev",
                        "allowed_paths": ["tests"],
                        "depends_on": [],
                        "acceptance_criteria": ["The next wave receives the canonical result."],
                        "verification": ["Read the next worker briefing."],
                    }],
                }],
            }
        result = control.complete_worker_attempt(completion)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["outcome"], "attempt_completed")
        return str(result["attempt_result_ref"])

    @staticmethod
    def _continue(response: dict[str, object], result_ref: str, project: Path) -> dict[str, object]:
        result = control.continue_orchestration({
            "project_root": str(project),
            "task_ref": response["task_ref"],
            "step": response["step"],
            "results": [{"attempt_result_ref": result_ref}],
        })
        if not result.get("ok"):
            raise AssertionError(result)
        return result

    def _read_current_continuation(
        self,
        response: dict[str, object],
        result_ref: str,
    ) -> dict[str, object]:
        read = control.read_worker_result({
            "project_root": str(self.project),
            "task_ref": response["task_ref"],
            "attempt_result_ref": result_ref,
        })
        self.assertTrue(read["ok"], read)
        continuation = read.get("continuation")
        self.assertIsInstance(continuation, dict, read)
        assert isinstance(continuation, dict)
        self.assertEqual(
            set(continuation), {"task_id", "step", "results"},
        )
        self.assertEqual(continuation["task_id"], self._task_state()[1]["task_id"])
        self.assertEqual(continuation["results"], [{"attempt_result_ref": result_ref}])
        self.assertEqual(mcp_api._internal_protocol(read).get("continuation"), continuation)
        return read

    def _continue_from_server_continuation(
        self,
        response: dict[str, object],
        continuation: dict[str, object],
    ) -> dict[str, object]:
        result = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": response["task_ref"],
            "step": continuation["step"],
            "results": continuation["results"],
        })
        self.assertTrue(result["ok"], result)
        return result

    def test_parallel_wave_requires_the_complete_identity_bound_result_set(self) -> None:
        """Two simultaneous slots advance only as one exact, server-derived set.

        This is deliberately a public-lifecycle integration test rather than a
        hand-authored state fixture: each worker receives a separate immutable
        dispatch, acknowledges that exact dispatch, persists a result, and the
        coordinator may continue only after both canonical results exist.
        """
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Run independent discovery and QA workers in one parallel wave.",
                "complexity": "C1",
                "acceptance_criteria": ["Both independent workers finish before review starts."],
                "verification": ["Prove result and receipt identities remain bound to their worker slots."],
                "plan_approval": "auto",
            },
            "waves": [
                {"workers": [{"phase": "discover", "profile": "explorer"}, {"phase": "qa", "profile": "qa_engineer"}]},
                {"workers": [{"phase": "review", "profile": "code_reviewer"}]},
            ],
        })
        self.assertTrue(started["ok"], started)
        self.assertEqual(started["outcome"], "ready_to_spawn")
        dispatches = started["dispatches"]
        self.assertEqual(len(dispatches), 2)
        self.assertEqual(len({item["dispatch_ref"] for item in dispatches}), 2)

        task_dir, initial_state = self._task_state()
        attempts = {
            str(item["attempt_id"]): item
            for item in initial_state["attempts"]
            if isinstance(item, dict) and str(item.get("attempt_id") or "")
        }
        ordered_attempt_ids = [
            next(
                attempt_id
                for attempt_id, attempt in attempts.items()
                if attempt.get("dispatch_ref") == dispatch["dispatch_ref"]
            )
            for dispatch in dispatches
        ]
        self.assertEqual(len(set(ordered_attempt_ids)), 2)
        self.assertEqual(set(ordered_attempt_ids), set(attempts))
        for attempt_id in ordered_attempt_ids:
            self._read_briefing(initial_state, attempts[attempt_id])

        first_id, second_id = ordered_attempt_ids
        first_ref = self._complete_strict(initial_state, attempts[first_id], "Discovery completed its independent check.")
        first_read = control.read_worker_result({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "attempt_result_ref": first_ref,
        })
        self.assertTrue(first_read["ok"], first_read)
        self.assertNotIn("continuation", first_read)
        self.assertEqual(first_read.get("continuation_unavailable_reason"), "parallel_wave_results_pending")

        before_partial_continue = self._task_state()[1]
        partial_continue = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{"worker": 1, "attempt_result_ref": first_ref}],
        })
        self.assertFalse(partial_continue["ok"], partial_continue)
        self.assertEqual(partial_continue["code"], "continue_validation_failed")
        self.assertIn("exactly 2 result", partial_continue["diagnostics"][0]["message"])
        self.assertEqual(self._task_state()[1], before_partial_continue)

        second_ref = self._complete_strict(initial_state, attempts[second_id], "QA completed its independent check.")
        self.assertNotEqual(first_ref, second_ref)
        second_read = control.read_worker_result({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "attempt_result_ref": second_ref,
        })
        self.assertTrue(second_read["ok"], second_read)
        continuation = second_read.get("continuation")
        self.assertIsInstance(continuation, dict, second_read)
        assert isinstance(continuation, dict)
        expected_results = [
            {"worker": 1, "attempt_result_ref": first_ref},
            {"worker": 2, "attempt_result_ref": second_ref},
        ]
        self.assertEqual(continuation["results"], expected_results)

        # The continuation is derived from the complete wave, not from the
        # particular result just read. This avoids a last-reader association.
        first_after_complete = control.read_worker_result({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "attempt_result_ref": first_ref,
        })
        self.assertEqual(first_after_complete.get("continuation"), continuation)

        # A result may not be transferred to the sibling's server-owned slot.
        before_swapped_continue = self._task_state()[1]
        swapped = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": continuation["step"],
            "results": [
                {"worker": 1, "attempt_result_ref": second_ref},
                {"worker": 2, "attempt_result_ref": first_ref},
            ],
        })
        self.assertFalse(swapped["ok"], swapped)
        self.assertEqual(swapped["code"], "continue_validation_failed")
        self.assertIn("exact active attempt", swapped["diagnostics"][0]["message"])
        self.assertEqual(self._task_state()[1], before_swapped_continue)

        for attempt_id, dispatch in zip(ordered_attempt_ids, dispatches):
            receipts = attempt_protocol.attempt_receipts(
                self.ledger,
                task_id=str(initial_state["task_id"]),
                attempt_id=attempt_id,
            )
            receipt = receipts["briefing_receipt"]
            self.assertIsInstance(receipt, dict)
            assert isinstance(receipt, dict)
            self.assertEqual(receipt["payload"]["dispatch_ref"], dispatch["dispatch_ref"])
        receipt_refs = [
            attempt_protocol.attempt_receipts(
                self.ledger, task_id=str(initial_state["task_id"]), attempt_id=attempt_id,
            )["briefing_receipt"]["event_ref"]
            for attempt_id in ordered_attempt_ids
        ]
        self.assertEqual(len(set(receipt_refs)), 2)

        advanced = self._continue_from_server_continuation(started, continuation)
        self.assertEqual(advanced["outcome"], "ready_to_spawn")
        self.assertEqual([item["phase"] for item in advanced["dispatches"]], ["review"])

    def test_blocked_canonical_result_remains_a_terminal_receipt_not_success(self) -> None:
        """A blocked AttemptResult stays addressable without a fake continuation.

        The worker result is authoritative evidence that the current slot is
        blocked, but it cannot authorize a success continuation.  The parent
        must submit the exact dispatch identity as a non-success receipt so
        Cortex can record the blocked gate and expose its recovery path.
        """
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Exercise a canonical blocked planner handoff.",
                "acceptance_criteria": ["A blocked worker must stop the current pipeline."],
                "verification": ["Verify the blocked AttemptResult is durably consumed."],
                "plan_approval": "auto",
            },
            "waves": [
                {"workers": [{"phase": "discover", "profile": "explorer"}]},
                {"workers": [{"phase": "implementation", "profile": "backend_dev"}]},
            ],
        })
        self.assertTrue(started["ok"], started)
        task_dir, state, attempt = self._active_attempt()
        self._read_briefing(state, attempt)
        completion = control.complete_worker_attempt({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "status": "blocked",
            "summary": "Planner is blocked pending the required repository evidence.",
            "findings": [],
            "decisions_needed": ["Provide the required repository evidence."],
            "unresolved": ["The required repository evidence is unavailable."],
            "claims": [],
        })
        self.assertTrue(completion["ok"], completion)
        self.assertEqual(completion["outcome"], "attempt_blocked")
        result_ref = str(completion["attempt_result_ref"])
        blocked_state = control.load_task_state_for_artifact(task_dir)
        blocked_attempt = next(item for item in blocked_state["attempts"] if item["attempt_id"] == attempt["attempt_id"])
        self.assertEqual(blocked_attempt["status"], "blocked")
        self.assertEqual(blocked_attempt["lifecycle_status"], "blocked")
        self.assertEqual(blocked_attempt["attempt_result_ref"], result_ref)

        read = control.read_worker_result({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "attempt_result_ref": result_ref,
        })
        self.assertTrue(read["ok"], read)
        self.assertNotIn("continuation", read)
        self.assertEqual(read["continuation_unavailable_reason"], "attempt_result_not_finalized")

        fake_success = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{"attempt_result_ref": result_ref}],
        })
        self.assertFalse(fake_success["ok"], fake_success)
        self.assertIn("finalized canonical attempt result", fake_success["diagnostics"][0]["message"])

        terminal_receipt = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{
                "status": "blocked",
                "dispatch_ref": attempt["dispatch_ref"],
                "reason": "Planner is blocked pending the required repository evidence.",
            }],
        })
        self.assertTrue(terminal_receipt["ok"], terminal_receipt)
        self.assertEqual(terminal_receipt["outcome"], "blocked")
        final_state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(final_state["status"], "blocked")
        self.assertEqual(final_state["gates"]["discover"]["outcome"], "blocked")

    def test_unresolved_dispatch_cannot_complete_or_close_and_recovers_deterministically(self) -> None:
        """A dispatch without a canonical worker result remains recoverable.

        This is the production-shaped failure from a lost/unfinished native
        wait: the server has issued a dispatch, but the coordinator has no
        ``AttemptResult`` to submit.  A completion-shaped continuation must
        fail before reserving or mutating the active wave.  Inspection and
        lifecycle recovery must continue to expose the same pending dispatch,
        with documentation/close still pending and no synthetic handoff.
        """
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Keep an unfinished worker dispatch recoverable.",
                "acceptance_criteria": ["A canonical worker result is required before completion."],
                "verification": ["Repeat the pending-dispatch recovery check."],
                "plan_approval": "auto",
            },
            "waves": [
                {"workers": [{"phase": "discover", "profile": "explorer"}]},
                {"workers": [{"phase": "documentation", "profile": "technical_writer"}]},
                {"workers": [{"phase": "close", "profile": "build_verification"}]},
            ],
        })
        self.assertTrue(started["ok"], started)
        self.assertEqual(started["outcome"], "ready_to_spawn")
        self.assertEqual(len(started["dispatches"]), 1)

        task_dir, before, attempt = self._active_attempt()
        dispatch_ref = str(started["dispatches"][0]["dispatch_ref"])
        attempt_id = str(attempt["attempt_id"])
        self.assertEqual(attempt["status"], control.AWAITING_HOST_SPAWN)
        self.assertEqual(attempt["dispatch_ref"], dispatch_ref)
        self.assertFalse(attempt.get("attempt_result_ref"))

        # A coordinator cannot turn a missing worker result into success.  The
        # same request is retried to prove the validation path is deterministic
        # and does not consume the attempt or advance the pipeline.
        continuation_request = {
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{"status": "passed"}],
        }
        first_rejection = control.continue_orchestration(continuation_request)
        second_rejection = control.continue_orchestration(continuation_request)
        for rejection in (first_rejection, second_rejection):
            self.assertFalse(rejection["ok"], rejection)
            self.assertEqual(rejection["code"], "continue_validation_failed")
            self.assertEqual(rejection["outcome"], "needs_input")
            self.assertEqual(rejection["dispatches"], [])
            self.assertIn("attempt_result_ref", rejection["diagnostics"][0]["message"])
        self.assertEqual(
            first_rejection["diagnostics"], second_rejection["diagnostics"],
        )
        self.assertEqual(first_rejection["next_action"], second_rejection["next_action"])

        _same_task_dir, after, same_attempt = self._active_attempt()
        self.assertEqual(task_dir, _same_task_dir)
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["completed_gates"], [])
        self.assertFalse(after.get("handoff_created"))
        self.assertFalse(after.get("close_verified"))
        self.assertEqual(same_attempt["attempt_id"], attempt_id)
        self.assertEqual(same_attempt["status"], control.AWAITING_HOST_SPAWN)
        self.assertEqual(same_attempt["dispatch_ref"], dispatch_ref)
        self.assertFalse(same_attempt.get("attempt_result_ref"))

        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        self.assertTrue(inspected["ok"], inspected)
        inspection = inspected["result"]
        self.assertEqual(inspection["available_results"], [])
        self.assertEqual(
            [item["dispatch_ref"] for item in inspection["pending_dispatches"]],
            [dispatch_ref],
        )
        inspection_state = inspection["context_handoff"]["state"]
        self.assertEqual(inspection_state["completed_gates"], [])
        self.assertFalse(inspection_state["handoff_created"])
        self.assertFalse(inspection_state["close_verified"])
        self.assertEqual(inspection["plan"][1]["status"], "pending")
        self.assertEqual(inspection["plan"][2]["status"], "pending")

        recovered = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "recover_inspect",
        })
        recovered_again = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "recover_inspect",
        })
        for result in (recovered, recovered_again):
            self.assertTrue(result["ok"], result)
            recovery = result["result"]["lifecycle_recovery"]
            self.assertEqual(recovery["mode"], "recover_lifecycle")
            self.assertFalse(recovery["state_changed"])
            self.assertEqual(recovery["expired_attempt_ids"], [])
            self.assertEqual(recovery["unselectable_result_attempt_ids"], [])
            self.assertFalse(recovery["required"])
            self.assertEqual(
                [item["dispatch_ref"] for item in result["result"]["pending_dispatches"]],
                [dispatch_ref],
            )
        self.assertEqual(
            recovered["result"]["lifecycle_recovery"],
            recovered_again["result"]["lifecycle_recovery"],
        )

    def test_consumed_continuation_receipts_cannot_be_replanned_with_new_future_waves(self) -> None:
        """A completed worker result is a one-use continuation receipt.

        This mirrors the production failure mode where a coordinator received
        a successful step-2 response, then repeatedly retried that exact
        result with successively edited ``future_waves`` to reduce context.
        The second request must be a stable fail-closed stop and must leave
        both the pipeline and the next worker untouched.
        """
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Prove a consumed discovery result cannot be replanned.",
                "acceptance_criteria": ["A completed result advances exactly one wave."],
                "verification": ["Reject a changed replan that reuses the same result receipt."],
                "plan_approval": "auto",
            },
            "waves": [
                {"workers": [{"phase": "discover", "profile": "explorer"}]},
                {"workers": [{"phase": "implementation", "profile": "backend_dev", "depends_on": ["discover"]}]},
            ],
        })
        self.assertTrue(started["ok"], started)
        _task_dir, state, attempt = self._active_attempt()
        self._read_briefing(state, attempt)
        result_ref = self._complete_strict(state, attempt, "Discovery completed exactly once.")
        read = self._read_current_continuation(started, result_ref)
        continuation = read["continuation"]
        assert isinstance(continuation, dict)
        # Model the durable, post-commit receipt while deliberately retaining
        # the same active relative step.  This is the pathological recovery
        # shape from the stopped live thread: the old step is still presented
        # as active after a prior server acceptance, so a changed replan must
        # not consume its canonical result a second time.
        original = {
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": continuation["step"],
            "results": continuation["results"],
        }
        request_digest = control._orchestrate_request_digest({
            key: value for key, value in original.items() if key != "task_ref"
        })
        control._v3_store_continue(
            original,
            str(state["task_id"]),
            request_digest,
            {"ok": True, "outcome": "ready_to_spawn", "dispatches": []},
        )

        _before_dir, before = self._task_state()
        before_snapshot = json.loads(json.dumps(before))
        reused = {
            **original,
            "reason": "Try to reduce the next briefing after the result was already consumed.",
            "future_waves": [{
                "workers": [{
                    "phase": "review",
                    "profile": "code_reviewer",
                    "objective": "This proposal must never be applied from a consumed receipt.",
                    "paths": ["tests"],
                    "acceptance": ["The reused receipt is rejected before replan."],
                    "verification": ["Observe the stable fail-closed diagnostic."],
                    "depends_on": ["discover"],
                }],
            }],
        }
        rejected = control.continue_orchestration(reused)
        rejected_again = control.continue_orchestration(reused)
        for response in (rejected, rejected_again):
            self.assertFalse(response["ok"], response)
            self.assertEqual(response["code"], "continue_receipts_already_consumed")
            self.assertEqual(response["outcome"], "blocked")
            self.assertEqual(response["task_ref"], started["task_ref"])
            self.assertFalse(response["retryable"])
            self.assertEqual(response["dispatches"], [])
            self.assertIn("manage_orchestration intent=inspect", response["next_action"])
        self.assertEqual(rejected["diagnostics"], rejected_again["diagnostics"])
        self.assertEqual(rejected["next_action"], rejected_again["next_action"])
        self.assertEqual(self._task_state()[1], before_snapshot)

        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        self.assertTrue(inspected["ok"], inspected)
        handoff = inspected["result"]["context_handoff"]
        self.assertEqual(handoff["task_ref"], started["task_ref"])
        self.assertEqual(handoff["task_id"], continuation["task_id"])
        self.assertEqual(
            [item["dispatch_ref"] for item in handoff["pending_dispatches"]],
            [item["dispatch_ref"] for item in started["dispatches"]],
        )

    def test_public_successor_briefing_and_compaction_preserve_target_handoffs(self) -> None:
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Implement and independently verify a lossless handoff seam.",
                "acceptance_criteria": ["The requested observable outcome is completed end to end."],
                "verification": ["Run the production handoff integration test."],
                "plan_approval": "auto",
            },
            "waves": [
                {"workers": [{"phase": "discover", "profile": "explorer"}]},
                {"workers": [{"phase": "implementation", "profile": "backend_dev", "depends_on": ["discover"]}]},
                {"workers": [
                    {"phase": "qa", "profile": "qa_engineer", "depends_on": ["implementation"]},
                    {"phase": "review", "profile": "code_reviewer", "depends_on": ["implementation"]},
                ]},
            ],
        })
        self.assertTrue(started["ok"], started)

        _task_dir, state, discover = self._active_attempt()
        self._read_briefing(state, discover)
        discover_result = self._complete_strict(
            state, discover, "Discovery established the bounded successor contract."
        )
        backend_response = self._continue(started, discover_result, self.project)
        backend_assignment = self._assignment(backend_response)
        backend_handoff = backend_assignment["handoff"]
        self.assertEqual(backend_handoff["target"]["kind"], "implementation")
        self.assertIn("relevant_predecessor_conclusions", backend_handoff)
        self.assertNotIn("files_changed", backend_handoff)
        self.assertNotIn("findings", backend_handoff)
        self.assertNotIn("worker_body", json.dumps(backend_handoff).lower())

        task_dir, state, backend = self._active_attempt()
        backend_package = control._delegation_package(task_dir, str(state["task_id"]), str(backend["attempt_id"]))
        self.assertEqual(backend_package["predecessor_results"][0]["semantic_source"], "attempt_result")
        self.assertFalse(backend_package["predecessor_selection"].get("truncated", False))
        self.assertNotIn("unexpected_fallback_refs", backend_package["predecessor_selection"])
        self.assertGreater(backend_package["briefing_bytes"], 0)
        self._read_briefing(state, backend)
        self._read_predecessors(state, backend, str(started["task_ref"]))
        check = control.record_worker_attempt_event({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": backend["attempt_id"],
            "profile": backend["profile"],
            "event_type": "verification_claimed",
            "event_key": "backend-claim",
            "payload": {
                "command": "python3 -m unittest tests.test_production_handoff_integration",
                "cwd": ".",
                "exit_code": 0,
                "evidence": "Worker claims the focused check passed.",
            },
        })
        self.assertTrue(check["ok"], check)
        activation = control.activate_orchestration({
            "project_root": str(self.project),
            "user_command": "/cortex",
            "principal": state["principal"],
            "thread_id": state["thread_id"],
        })
        self.assertTrue(activation["active"], activation)
        observation = control.execute_verification({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "thread_id": state["thread_id"],
            "expected_revision": state["revision"],
            "gate": backend["gate"],
            "attempt_id": backend["attempt_id"],
            "summary": "Trusted Cortex verification completed before backend finalization.",
            "verification_id": "benign_success",
        })
        self.assertTrue(observation["recorded"], observation)
        self.assertEqual(observation["verification_observation"]["actor"], "cortex")
        receipt = observation["verification_observation"]["payload"]["server_execution_receipt"]
        self.assertEqual(receipt["task_id"], state["task_id"])
        self.assertEqual(receipt["attempt_id"], backend["attempt_id"])
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["path_set"], ["."])
        self.assertTrue(receipt["observed_at"])
        replay = control.execute_verification({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "thread_id": state["thread_id"],
            "expected_revision": state["revision"],
            "gate": backend["gate"],
            "attempt_id": backend["attempt_id"],
            "summary": "Trusted Cortex verification completed before backend finalization.",
            "verification_id": "benign_success",
        })
        self.assertTrue(replay["idempotent"], replay)
        self.assertEqual(
            replay["verification_observation"]["event_ref"],
            observation["verification_observation"]["event_ref"],
        )
        (self.project / "backend-owned.py").write_text("# observed backend change\n", encoding="utf-8")
        backend_result = self._complete_strict(
            state, backend, "Backend implementation completed the bounded handoff seam."
        )
        successor_response = self._continue(backend_response, backend_result, self.project)
        assignments = {
            str(dispatch["phase"]): self._assignment(successor_response, index)
            for index, dispatch in enumerate(successor_response["dispatches"])
        }
        qa_assignment = assignments["qa"]
        qa_handoff = qa_assignment["handoff"]
        self.assertEqual(qa_handoff["target"]["kind"], "qa")
        self.assertIn("implemented_behavior", qa_handoff)
        self.assertIn("backend-owned.py", qa_handoff["files_changed"])
        self.assertEqual(qa_handoff["verification_already_executed"], ["/usr/bin/true (exit 0)"])
        self.assertNotIn("findings", qa_handoff)
        self.assertNotIn("evidence", qa_handoff)

        review_assignment = assignments["review"]
        review_handoff = review_assignment["handoff"]
        self.assertEqual(review_handoff["target"]["kind"], "review")
        self.assertIn("change_inventory", review_handoff)
        self.assertIn("backend-owned.py", review_handoff["change_inventory"])
        self.assertNotIn("implemented_behavior", review_handoff)
        self.assertNotIn("relevant_predecessor_conclusions", review_handoff)
        self.assertNotIn("findings", review_handoff)

        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        pending = inspected["context_handoff"]["pending_dispatches"]
        self.assertEqual(len(pending), 2)
        recovery_handoff = next(
            item["target_handoff"] for item in pending if item["phase"] == "review"
        )
        self.assertEqual(recovery_handoff["target"]["profile"], "code_reviewer")
        self.assertEqual(recovery_handoff["target"]["kind"], "review")
        self.assertEqual(recovery_handoff["change_inventory"], review_handoff["change_inventory"])
        self.assertNotIn("worker_body", json.dumps(recovery_handoff).lower())

    def test_oversized_discover_successor_briefing_is_materialized_without_rejection(self) -> None:
        """A large fresh-v3 successor remains dispatchable after Discover.

        The host sees only the short bootstrap; the worker reads the immutable
        briefing.  Consequently an advisory prompt-size target must not turn a
        completed Discover receipt into a missing successor dispatch.  This
        uses real public continuation state rather than a hand-authored prompt
        and verifies the materialized bytes, digest, bootstrap capability,
        task contract, and predecessor handoff.
        """
        requirements = [
            f"Requirement {index}: " + ("lossless-successor-context-" * 35)
            for index in range(8)
        ]
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Continue a large Discover handoff without a backend prompt-size rejection.",
                "complexity": "C2",
                "requirements": requirements,
                "acceptance_criteria": ["The complete successor briefing is materialized."],
                "verification": ["Read its digest-bound dispatch artifact through the scoped worker protocol."],
                "plan_approval": "auto",
            },
            "waves": [
                {"workers": [{"phase": "discover", "profile": "explorer"}]},
                {"workers": [{"phase": "implementation", "profile": "backend_dev", "depends_on": ["discover"]}]},
            ],
        })
        self.assertTrue(started["ok"], started)
        _task_dir, state, discover = self._active_attempt()
        self._read_briefing(state, discover)
        discover_summary = "Discover completed before its large successor dispatch: " + ("predecessor-evidence-" * 1_200)
        discover_ref = self._complete_strict(state, discover, discover_summary)
        successor_response = self._continue(started, discover_ref, self.project)
        self.assertTrue(successor_response["ok"], successor_response)

        task_dir, state, successor = self._active_attempt()
        self.assertEqual(successor["gate"], "implementation")
        package = control._delegation_package(task_dir, str(state["task_id"]), str(successor["attempt_id"]))
        self.assertGreater(package["briefing_bytes"], 14_500)
        briefing_path = task_dir / str(successor["briefing_file"])
        materialized = briefing_path.read_text(encoding="utf-8")
        self.assertEqual(len(materialized.encode("utf-8")), package["briefing_bytes"])
        self.assertEqual(hashlib.sha256(materialized.encode("utf-8")).hexdigest(), successor["briefing_digest"])
        bootstrap = str(delegation_service.rehydrate_dispatch_spawn_request(
            task_dir, control.load_task_definition(task_dir, state), successor,
        )["message"])
        self.assertIn("read_dispatch_briefing", bootstrap)
        self.assertIn(str(successor["briefing_digest"]), bootstrap)
        self.assertIn(str(briefing_path), bootstrap)

        assignment = self._assignment(successor_response)
        self.assertIn(requirements[0], "".join(assignment["requirements"]))
        self.assertEqual(assignment["task_contract"]["digest_sha256"], package["task_contract"]["digest_sha256"])
        self.assertEqual(assignment["handoff"]["predecessor_result_refs"], [discover_ref])
        self.assertEqual(assignment["handoff"]["relevant_predecessor_conclusions"], [discover_summary])
        self._read_briefing(state, successor)
        self._read_predecessors(state, successor, str(started["task_ref"]))

    def test_full_c3_documentation_continuation_is_server_derived_and_strict(self) -> None:
        """The hidden governance waves cannot make a parent infer Documentation's step."""
        result_ref_schema = (
            control.PUBLIC_SCHEMA_REGISTRY["continue_orchestration"]
            ["properties"]["results"]["items"]["properties"]["attempt_result_ref"]
        )
        self.assertIn("read_worker_result.continuation.results", result_ref_schema["description"])
        self.assertIn("continuation={task_id,step,results}", mcp_api.PUBLIC_TOOL_DESCRIPTIONS["read_worker_result"])
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Create the C3 continuation fixture result after independent governance review.",
                "complexity": "C3",
                "acceptance_criteria": ["The full C3 sequence reaches governance close."],
                "verification": ["Observe the server-derived continuation for every completed worker."],
                "plan_approval": "auto",
            },
            "waves": [
                {"workers": [{"phase": "implementation", "profile": "backend_dev"}]},
                {"workers": [{"phase": "documentation", "profile": "technical_writer"}]},
                {"workers": [{"phase": "close", "profile": "build_verification"}]},
            ],
        })
        self.assertTrue(started["ok"], started)
        current = started
        prior_result_ref: str | None = None
        for expected_gate, expected_step in (("governance_activation", 1), ("implementation", 2)):
            if prior_result_ref:
                historical = control.read_worker_result({
                    "project_root": str(self.project),
                    "task_ref": current["task_ref"],
                    "attempt_result_ref": prior_result_ref,
                })
                self.assertTrue(historical["ok"], historical)
                self.assertNotIn("continuation", historical)
                self.assertEqual(
                    historical.get("continuation_unavailable_reason"), "attempt_result_not_current",
                )
            _task_dir, state, attempt = self._active_attempt()
            self.assertEqual(attempt["gate"], expected_gate)
            self._read_briefing(state, attempt)
            self._read_predecessors(state, attempt, str(current["task_ref"]))
            result_ref = self._complete_strict(state, attempt, f"{expected_gate} completed.")
            read = self._read_current_continuation(current, result_ref)
            continuation = read["continuation"]
            assert isinstance(continuation, dict)
            self.assertEqual(continuation["step"], expected_step)
            current = self._continue_from_server_continuation(current, continuation)
            prior_result_ref = result_ref

        _task_dir, state, documentation = self._active_attempt()
        self.assertEqual(documentation["gate"], "documentation")
        self._read_briefing(state, documentation)
        self._read_predecessors(state, documentation, str(current["task_ref"]))
        documentation_ref = self._complete_strict(
            state, documentation, "Documentation completed without reconstructing its continuation."
        )
        read = self._read_current_continuation(current, documentation_ref)
        continuation = read["continuation"]
        assert isinstance(continuation, dict)
        self.assertEqual(continuation["step"], 3)

        before_wrong = self._task_state()[1]
        wrong_step = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": current["task_ref"],
            "step": 4,
            "results": continuation["results"],
        })
        self.assertFalse(wrong_step["ok"])
        self.assertEqual(wrong_step["code"], "continue_validation_failed")
        self.assertIn("active relative step 3", wrong_step["diagnostics"][0]["message"])
        self.assertEqual(self._task_state()[1], before_wrong)

        wrong_projection = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": current["task_ref"],
            "step": continuation["step"],
            "results": [{"attempt_result_ref": read["result_view"]["projection_ref"]}],
        })
        self.assertFalse(wrong_projection["ok"])
        self.assertEqual(wrong_projection["code"], "continue_validation_failed")
        self.assertIn("exact active attempt", wrong_projection["diagnostics"][0]["message"])
        self.assertEqual(self._task_state()[1], before_wrong)

        governance_close = self._continue_from_server_continuation(current, continuation)
        self.assertEqual(governance_close["outcome"], "ready_to_spawn")
        self.assertEqual([item["phase"] for item in governance_close["dispatches"]], ["governance_close"])

        # A coordinator that lost the accepted response must not replay the
        # consumed step while trying to reconstruct the next wave.  The
        # public receipt is terminal and explicitly forbids artifact/rework
        # requests; otherwise a model can loop on continue(step=3).
        stale = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": current["task_ref"],
            "step": continuation["step"],
            "results": continuation["results"],
            "reason": "stale retry after accepted continuation",
        })
        self.assertFalse(stale["ok"], stale)
        self.assertEqual(stale["code"], "continue_validation_failed")
        self.assertFalse(stale["retryable"])
        self.assertEqual(stale["stop_reason"], "stale_relative_step")
        self.assertIn("Do not call continue_orchestration again", stale["next_action"])
        self.assertIn("do not request artifacts", stale["next_action"])

    def test_completed_child_recovery_set_survives_compaction_before_read(self) -> None:
        """A compacted coordinator can recover the exact data needed to continue.

        This deliberately drops the in-memory ``state``/``attempt`` variables after
        a worker has durably completed, then takes the normal inspect/recovery route.
        The assertion is scoped to the continuation contract: identity, canonical
        result reference, lifecycle, result digest/workspace facts, receipts, and
        the server-owned step/results object.  It does not require exposing every
        historical AttemptEvent to the coordinator.
        """
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Persist one completed child and recover its continuation after compaction.",
                "complexity": "C1",
                "acceptance_criteria": ["The completed child remains readable after inspect recovery."],
                "verification": ["Read the canonical result and continue from the server-owned continuation."],
                "plan_approval": "auto",
            },
            "waves": [
                {"workers": [{"phase": "implementation", "profile": "backend_dev"}]},
                {"workers": [{"phase": "documentation", "profile": "technical_writer"}]},
            ],
        })
        self.assertTrue(started["ok"], started)
        task_dir, state, attempt = self._active_attempt()
        self.assertEqual(attempt["gate"], "implementation")
        self._read_briefing(state, attempt)
        self._read_predecessors(state, attempt, str(started["task_ref"]))
        result_ref = self._complete_strict(state, attempt, "Implementation completed before coordinator compaction.")

        # Simulate the coordinator's post-summary boundary by discarding the
        # pre-compaction Python objects and asking the server for a fresh snapshot.
        del task_dir, state, attempt
        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        self.assertTrue(inspected["ok"], inspected)
        handoff = inspected["context_handoff"]
        recovered = next(
            item for item in handoff["completed_results"]
            if item.get("attempt_result_ref") == result_ref
        )
        self.assertEqual(recovered["phase"], "implementation")
        self.assertEqual(recovered["profile"], "backend_dev")
        # The state handoff records the pre-continuation server phase.  The
        # canonical result itself is COMPLETED; the attempt projection remains
        # result_finalized until the coordinator consumes its continuation.
        self.assertEqual(recovered["lifecycle_status"], "result_finalized")
        self.assertTrue(recovered["attempt_id"])
        self.assertTrue(recovered["dispatch_ref"])
        # Inspect may replay the still-issued dispatch envelope, but it must
        # replay the same identity rather than manufacture a replacement.
        self.assertEqual(len(inspected["dispatches"]), 1)
        self.assertEqual(inspected["dispatches"][0]["dispatch_ref"], recovered["dispatch_ref"])

        read = control.read_worker_result({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "attempt_result_ref": result_ref,
        })
        self.assertTrue(read["ok"], read)
        view = read["result_view"]
        self.assertEqual(view["attempt_result_ref"], result_ref)
        self.assertEqual(view["lifecycle_status"], "COMPLETED")
        self.assertEqual(view["result"]["status"], "completed")
        self.assertEqual(view["result"]["unresolved"], [])
        self.assertTrue(view["result"].get("workspace_observation"))
        self.assertIn("content_digest", view)
        receipts = attempt_protocol.attempt_receipts(
            self.ledger, task_id=handoff["task_id"], attempt_id=recovered["attempt_id"],
        )
        self.assertIsNotNone(receipts["briefing_receipt"])

        continuation = read.get("continuation")
        self.assertIsInstance(continuation, dict, read)
        assert isinstance(continuation, dict)
        self.assertEqual(continuation["task_id"], handoff["task_id"])
        self.assertEqual(continuation["results"], [{"attempt_result_ref": result_ref}])
        advanced = self._continue_from_server_continuation(started, continuation)
        self.assertEqual(advanced["outcome"], "ready_to_spawn")
        self.assertEqual([item["phase"] for item in advanced["dispatches"]], ["documentation"])

    def test_default_c2_chain_materializes_dynamic_briefings_through_documentation(self) -> None:
        """Every default C2 successor keeps the exact identity/handoff contract.

        The live failure occurred only after several successful successors, so
        a one-hop handoff test is not enough here.  This enters through the
        default C2 planner, acknowledges and completes every issued attempt,
        reads each predecessor through its public receipt boundary, and checks
        the newly materialized briefing before the next continuation is
        derived.  In particular, Documentation is reached with a real dynamic
        predecessor/handoff chain rather than a hand-authored fixture.
        """
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Complete the default C2 pipeline with an immutable documentation handoff.",
                "complexity": "C2",
                "acceptance_criteria": ["Every default gate reaches a canonical completed AttemptResult."],
                "verification": ["Each successor briefing is readable with its exact issued identity and digest."],
                "plan_approval": "auto",
            },
        })
        self.assertTrue(started["ok"], started)
        self.assertEqual(started["outcome"], "ready_to_spawn")

        expected_gates = (
            "discover", "plan", "implementation", "qa", "review", "documentation", "close",
        )
        current = started
        completed_refs: list[str] = []
        for index, expected_gate in enumerate(expected_gates):
            _task_dir, state, attempt = self._active_attempt()
            self.assertEqual(attempt["gate"], expected_gate)
            self.assertEqual(len(current["dispatches"]), 1, current)
            dispatch = current["dispatches"][0]
            self.assertEqual(dispatch["dispatch_ref"], attempt["dispatch_ref"])

            # This is the native worker boundary: the read must validate the
            # same task/attempt/profile/dispatch/digest tuple that was issued.
            self._read_briefing(state, attempt)
            package = control._delegation_package(
                self._task_state()[0], str(state["task_id"]), str(attempt["attempt_id"]),
            )
            self.assertGreater(package["briefing_bytes"], 14_500)
            assignment = self._assignment(current)
            identity = assignment["worker_identity"]
            self.assertEqual(identity["task_id"], state["task_id"])
            self.assertEqual(identity["attempt_id"], attempt["attempt_id"])
            self.assertEqual(identity["profile"], attempt["profile"])
            self.assertEqual(identity["dispatch_ref"], attempt["dispatch_ref"])
            self.assertEqual(identity["facade_managed"], bool(attempt["facade_managed"]))
            self.assertIn("compiled_context", assignment)
            self.assertIn("handoff", assignment)
            self.assertEqual(assignment["handoff"]["target"]["gate"], expected_gate)
            self.assertEqual(
                assignment["handoff"].get("predecessor_result_refs", []),
                list(attempt.get("context_result_refs") or []),
            )

            predecessor_refs = list(attempt.get("context_result_refs") or [])
            self.assertLessEqual(len(predecessor_refs), 16)
            self._read_predecessors(state, attempt, str(current["task_ref"]))
            result_ref = self._complete_strict(
                state, attempt, f"{expected_gate} completed in the default C2 chain.",
            )
            completed_refs.append(result_ref)

            if index < len(expected_gates) - 1:
                read = self._read_current_continuation(current, result_ref)
                continuation = read["continuation"]
                assert isinstance(continuation, dict)
                current = self._continue_from_server_continuation(current, continuation)
                self.assertEqual(current["outcome"], "ready_to_spawn", current)

        # The final close result still advances the server-owned lifecycle
        # marker; do not infer completion merely from the child result.
        final_read = self._read_current_continuation(current, completed_refs[-1])
        final_continuation = final_read["continuation"]
        assert isinstance(final_continuation, dict)
        completed = self._continue_from_server_continuation(current, final_continuation)
        self.assertEqual(completed["outcome"], "completed", completed)

        final_state = self._task_state()[1]
        self.assertEqual(final_state["status"], "completed")
        self.assertEqual(final_state["completed_gates"], list(expected_gates))
        self.assertEqual(len(completed_refs), len(expected_gates))

    def test_c3_oversized_requirement_continues_after_completed_attempt_without_loss(self) -> None:
        """A legacy-long requirement must not strand the ledger after worker completion.

        This enters through the public C3 lifecycle, so it covers the ingress
        normalization and the later ContextCompiler boundary that previously
        raised ``canonical requirements exceeds its bounded length`` only when
        the first result was continued.  The requirement is deliberately an
        unbroken token so the hard split path proves that every character is
        retained without depending on whitespace normalization at boundaries.
        """
        # Keep the requirement as one unbroken token after its short prefix.
        # This makes the assertion byte-exact even when the canonical domain
        # strips whitespace at preferred segment boundaries, and exercises the
        # hard split path where no word boundary is available.
        requirement = "semantic-requirement-" + ("z" * 900)
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Continue a C3 task whose existing requirement exceeds one context item.",
                "complexity": "C3",
                "requirements": [requirement],
                "acceptance_criteria": ["The complete requirement remains available to every successor."],
                "verification": ["Complete the first worker and continue from its canonical result."],
                "plan_approval": "auto",
            },
            "waves": [{"workers": [{"phase": "implementation"}]}],
        })
        self.assertTrue(started["ok"], started)

        task_dir, state, first = self._active_attempt()
        self.assertEqual(first["gate"], "governance_activation")
        self._read_briefing(state, first)
        completion = self._complete_strict(
            state, first, "The first C3 worker completed before continuation.",
        )
        replay = self._complete_strict(
            state, first, "The first C3 worker completed before continuation.",
        )
        self.assertEqual(replay, completion)

        # The coordinator reads the exact canonical result before advancing.
        read = control.read_worker_result({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "attempt_result_ref": completion,
        })
        self.assertTrue(read["ok"], read)
        continuation = read.get("continuation")
        self.assertIsInstance(continuation, dict, read)
        assert isinstance(continuation, dict)
        continued = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": continuation["step"],
            "results": continuation["results"],
        })
        self.assertTrue(continued["ok"], continued)
        self.assertNotIn("canonical requirements exceeds its bounded length", json.dumps(continued))

        state_after = control.load_task_state_for_artifact(task_dir)
        attempts = [item for item in state_after["attempts"] if isinstance(item, dict)]
        self.assertEqual(len(attempts), 2, state_after["attempts"])
        self.assertEqual(attempts[0]["attempt_id"], first["attempt_id"])
        self.assertEqual(attempts[0]["attempt_result_ref"], completion)
        self.assertEqual(attempts[1]["attempt_id"], "implementation-02")
        self.assertNotEqual(attempts[1]["attempt_id"], first["attempt_id"])

        successor = next(item for item in attempts if item["attempt_id"] == "implementation-02")
        package = control._delegation_package(
            task_dir, str(state_after["task_id"]), str(successor["attempt_id"]),
        )
        segments = package["task_requirements"]
        self.assertTrue(segments)
        self.assertEqual(segments, [requirement])

        # The first result is immutable and the successor can acknowledge its
        # briefing and predecessor independently without replacement work.
        self._read_briefing(state_after, successor)
        predecessor_read = control.read_worker_result({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "attempt_result_ref": completion,
            "attempt_id": successor["attempt_id"],
            "profile": successor["profile"],
        })
        self.assertTrue(predecessor_read["ok"], predecessor_read)
        self.assertEqual(predecessor_read["result_view"]["attempt_result_ref"], completion)

        stored = attempt_protocol.get_attempt_result(
            self.ledger,
            task_id=str(state_after["task_id"]),
            attempt_id=str(first["attempt_id"]),
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["result_ref"], completion)
        self.assertEqual(stored["lifecycle_status"], attempt_protocol.LIFECYCLE_COMPLETED)
        events = attempt_protocol.list_attempt_events(
            self.ledger,
            task_id=str(state_after["task_id"]),
            attempt_id=str(first["attempt_id"]),
        )
        self.assertEqual(
            [event["event_type"] for event in events],
            ["briefing_acknowledged", "work_completed", "finalizing", "completed"],
        )
        self.assertEqual(
            len({event["event_key"] for event in events}), len(events),
            "briefing receipt and completion retry must not duplicate event keys",
        )


if __name__ == "__main__":
    unittest.main()
