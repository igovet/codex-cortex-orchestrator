"""Production successor-handoff integration coverage.

The unit compiler tests deliberately pass hand-authored canonical objects.
This regression instead enters through the public orchestration lifecycle,
persists real AttemptResult/Event rows, and proves that the next
immutable briefing and compaction snapshot use a target-specific bounded
HandoffCompiler projection rather than a generic predecessor result body.
"""
from __future__ import annotations

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
from cortex_runtime import attempt_protocol, mcp_api  # noqa: E402


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

    def test_public_successor_briefing_and_compaction_use_bounded_target_handoffs(self) -> None:
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Implement and independently verify a bounded handoff seam.",
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
        self.assertEqual(backend_package["predecessor_selection"]["limit"], 16)
        self.assertNotIn("unexpected_fallback_refs", backend_package["predecessor_selection"])
        self.assertLess(backend_package["briefing_bytes"], 16 * 1024)
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
            self.assertLessEqual(package["briefing_bytes"], 14_500)
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
        self.assertLessEqual(max(map(len, segments)), 600)
        self.assertEqual("".join(segments), requirement)

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
