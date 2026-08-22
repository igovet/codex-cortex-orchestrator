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
        result = control.complete_worker_attempt({
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
        })
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


if __name__ == "__main__":
    unittest.main()
