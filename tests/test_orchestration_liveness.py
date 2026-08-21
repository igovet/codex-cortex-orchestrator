"""Regression tests for evidence-based corrective-work liveness."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex as control


class OrchestrationLivenessTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.set_up_host_private_control_store()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.ledger = control.ledger_root_path({"project_root": str(self.project)})

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()
        self.temp.cleanup()

    def _start(self) -> dict:
        return control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Investigate the repeated failure without claiming a false result.",
                "complexity": "C1",
                "acceptance_criteria": ["A verified result is produced or a user decision is requested."],
                "verification": ["Exercise the corrective retry lifecycle."],
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        })

    def _state(self) -> dict:
        task_dir = next((self.ledger / "tasks").iterdir())
        return control.load_task_state_for_artifact(task_dir)

    def _start_parallel(self) -> dict:
        return control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Exercise independent parallel corrective routes.",
                "complexity": "C1",
                "acceptance_criteria": ["Independent work is not stranded by a sibling retry."],
                "verification": ["Exercise a partial parallel wave."],
            },
            "waves": [
                {"workers": [{"phase": "qa"}, {"phase": "security"}]},
                {"workers": [{"phase": "review"}]},
            ],
        })

    def _record_success_report(self, attempt: dict, *, submission_id: str = "parallel-success") -> str:
        recorded = control.record_report({
            "project_root": str(self.project),
            "task_id": self._state()["task_id"],
            "principal": self._state()["principal"],
            "attempt_id": attempt["attempt_id"],
            "submission_id": submission_id,
            "report": {
                "summary": "Independent gate completed.", "findings": [], "questions": [],
                "changed_files": [], "tests": [], "evidence": ["focused evidence"], "uncertainty": [],
            },
        })
        return recorded["report"]["report_id"]

    def _fail_parallel_qa_and_complete_security(self, current: dict) -> dict:
        attempts = {item["gate"]: item for item in self._state()["attempts"] if not item.get("invalidated")}
        security_report = self._record_success_report(attempts["security"])
        return control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": current["task_ref"], "step": current["step"],
            "results": [
                {
                    "worker": 1, "status": "failed", "reason": "network transport unavailable before any project change",
                    "dispatch_ref": current["dispatches"][0]["dispatch_ref"],
                },
                {"worker": 2, "report_ref": security_report},
            ],
        })

    def _failed_continue(self, current: dict, *, reason: str, next_strategy: str | None = None) -> dict:
        result = {
            "status": "failed",
            "reason": reason,
            "dispatch_ref": current["dispatches"][0]["dispatch_ref"],
        }
        if next_strategy:
            result["next_strategy"] = next_strategy
        return control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": current["task_ref"],
            "step": current["step"],
            "results": [result],
        })

    @staticmethod
    def _planner_recovery_waves(
        *,
        planner_objective: str | None = None,
        discovery_strategy: str | None = None,
    ) -> list[dict]:
        planner: dict[str, str] = {"phase": "plan"}
        if planner_objective:
            planner["objective"] = planner_objective
        discovery: dict[str, str] = {"phase": "discover"}
        if discovery_strategy:
            discovery["strategy"] = discovery_strategy
        return [{"workers": [planner]}, {"workers": [discovery]}]

    def test_three_identical_infrastructure_failures_pause_without_false_terminal_result(self) -> None:
        current = self._start()
        reason = "network transport unavailable before any project change"
        for expected in (1, 2):
            current = self._failed_continue(current, reason=reason)
            self.assertTrue(current["ok"], current)
            self.assertEqual(current["outcome"], "ready_to_spawn")
            self.assertEqual(len(current["dispatches"]), 1)
            progress = self._state()["rework_progress"]["discover"]
            self.assertEqual(progress["consecutive_identical_iterations"], expected)

        paused = self._failed_continue(current, reason=reason)
        self.assertTrue(paused["ok"], paused)
        self.assertEqual(paused["outcome"], "blocked")
        self.assertEqual(paused["dispatches"], [])
        state = self._state()
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["rework_pause"]["status"], "needs_user_decision")
        self.assertEqual(state["rework_pause"]["failure_class"], "infrastructure")
        self.assertEqual(state["rework_pause"]["consecutive_identical_iterations"], 3)
        self.assertEqual(set(state["rework_pauses"]), {"discover"})
        self.assertNotIn("discover", state["completed_gates"])
        self.assertNotEqual(state["status"], "completed")

        rejected_resume = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": paused["task_ref"],
            "intent": "resume",
            "reason": "Retry the identical failed path.",
        })
        self.assertFalse(rejected_resume["ok"])
        self.assertIn("Planner-first recovery plan", rejected_resume["diagnostics"][0]["message"])

    def test_material_strategy_change_resets_the_no_progress_streak(self) -> None:
        current = self._start()
        reason = "network transport unavailable before any project change"
        current = self._failed_continue(current, reason=reason)
        current = self._failed_continue(current, reason=reason)
        changed = self._failed_continue(
            current,
            reason=reason,
            next_strategy="Use the cached offline source and verify transport only after the local analysis completes.",
        )
        self.assertTrue(changed["ok"], changed)
        self.assertEqual(changed["outcome"], "ready_to_spawn")
        state = self._state()
        self.assertEqual(state["status"], "active")
        self.assertNotIn("rework_pause", state)
        self.assertEqual(state["rework_progress"]["discover"]["consecutive_identical_iterations"], 1)

    def test_paraphrased_equivalent_failure_reasons_still_trip_the_circuit_breaker(self) -> None:
        current = self._start()
        reasons = (
            "network transport unavailable before any project change",
            "The remote connection failed because the network path is unavailable; source remains unchanged.",
            "No source changes occurred because the transport service cannot be reached over the network.",
        )
        for expected, reason in enumerate(reasons, 1):
            current = self._failed_continue(current, reason=reason)
            self.assertTrue(current["ok"], current)
            if expected < 3:
                self.assertEqual(current["outcome"], "ready_to_spawn")
            else:
                self.assertEqual(current["outcome"], "blocked")

        progress = self._state()["rework_progress"]["discover"]
        self.assertEqual(progress["consecutive_identical_iterations"], 3)
        self.assertEqual(progress["failure_classes"], ["infrastructure"])

    def test_paused_resume_rejects_planner_wrapper_around_the_same_failed_route(self) -> None:
        current = self._start()
        for _ in range(3):
            current = self._failed_continue(
                current,
                reason="network transport unavailable before any project change",
            )
        self.assertEqual(current["outcome"], "blocked")

        rejected = control.manage_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": current["task_ref"],
                "intent": "resume",
                "reason": "Ask Planner to restate the same discovery route.",
                "payload": {"future_waves": self._planner_recovery_waves()},
            }
        )
        self.assertFalse(rejected["ok"])
        self.assertIn("materially change the failed strategy, pipeline, or verification", rejected["diagnostics"][0]["message"])
        self.assertEqual(self._state()["status"], "blocked")
        self.assertEqual(self._state()["rework_pause"]["status"], "needs_user_decision")

    def test_paused_resume_accepts_a_material_strategy_change(self) -> None:
        current = self._start()
        for _ in range(3):
            current = self._failed_continue(
                current,
                reason="network transport unavailable before any project change",
            )
        resumed = control.manage_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": current["task_ref"],
                "intent": "resume",
                "reason": "Use a new offline discovery strategy before any remote transport verification.",
                "payload": {
                    "future_waves": self._planner_recovery_waves(
                        discovery_strategy=(
                            "Use the cached local source for discovery and defer remote transport verification "
                            "until the local analysis is complete."
                        ),
                    ),
                },
            }
        )
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["outcome"], "ready_to_spawn")
        self.assertEqual([item["phase"] for item in resumed["dispatches"]], ["plan"])
        self.assertEqual(self._state()["status"], "active")
        self.assertNotIn("rework_pause", self._state())

    def test_paused_resume_accepts_matching_infrastructure_remediation(self) -> None:
        current = self._start()
        for _ in range(3):
            current = self._failed_continue(
                current,
                reason="network transport unavailable before any project change",
            )
        resumed = control.manage_orchestration(
            {
                "project_root": str(self.project),
                "task_ref": current["task_ref"],
                "intent": "resume",
                "reason": "Repair the unavailable transport before returning to discovery.",
                "payload": {
                    "future_waves": self._planner_recovery_waves(
                        planner_objective=(
                            "Repair the network transport configuration and verify the connection before "
                            "retrying the discovery route."
                        ),
                    ),
                },
            }
        )
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["outcome"], "ready_to_spawn")
        self.assertEqual([item["phase"] for item in resumed["dispatches"]], ["plan"])
        self.assertNotIn("rework_pause", self._state())

    def test_parallel_sibling_completes_while_no_progress_pauses_only_failed_gate(self) -> None:
        current = self._start_parallel()
        current = self._fail_parallel_qa_and_complete_security(current)
        self.assertTrue(current["ok"], current)
        self.assertEqual([item["phase"] for item in current["dispatches"]], ["qa"])
        for _ in range(2):
            current = self._failed_continue(
                current,
                reason="network transport unavailable before any project change",
            )
        self.assertTrue(current["ok"], current)
        # QA is locally paused only after its independent security sibling
        # completed. A later wave cannot leapfrog that unresolved dependency.
        self.assertEqual(current["outcome"], "blocked")
        state = self._state()
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["completed_gates"], ["security"])
        self.assertEqual(set(state["rework_pauses"]), {"qa"})
        self.assertEqual(state["current_gates"], [])

    def test_unrelated_finding_does_not_change_failed_gate_repeat_signature(self) -> None:
        current = self._start_parallel()
        current = self._fail_parallel_qa_and_complete_security(current)
        state = self._state()
        control.db_upsert_task_finding(
            self.ledger,
            state["task_id"],
            {
                "fingerprint": "security-unrelated", "severity": "P1", "status": "open",
                "blocking": True, "summary": "Independent security finding", "details": {},
            },
            source={
                "transition": "opened", "report_id": "report-security", "receipt_ref": "receipt-security",
                "report_artifact_ref": "artifact-security", "report_content_digest": "digest-security",
                "attempt_id": "security-02", "gate": "security", "task_revision": 1,
            },
        )
        current = self._failed_continue(
            current,
            reason="network transport unavailable before any project change",
        )
        progress = self._state()["rework_progress"]["qa"]
        self.assertEqual(progress["consecutive_identical_iterations"], 2)
        self.assertEqual(progress["finding_fingerprints"], [])

    def test_multi_gate_recovery_requires_and_unpauses_only_named_gate(self) -> None:
        current = self._start_parallel()
        for _ in range(3):
            current = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": current["task_ref"], "step": current["step"],
                "results": [
                    {
                        "worker": 1, "status": "failed", "reason": "network transport unavailable",
                        "dispatch_ref": current["dispatches"][0]["dispatch_ref"],
                    },
                    {
                        "worker": 2, "status": "failed", "reason": "network transport unavailable",
                        "dispatch_ref": current["dispatches"][1]["dispatch_ref"],
                    },
                ],
            })
        self.assertEqual(current["outcome"], "blocked")
        recovery_waves = [
            {"workers": [{"phase": "plan", "objective": "Repair network transport configuration before retry."}]},
            {"workers": [{"phase": "qa"}, {"phase": "security"}]},
        ]
        ambiguous = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": current["task_ref"], "intent": "resume",
            "reason": "Recover the failed route.", "payload": {"future_waves": recovery_waves},
        })
        self.assertFalse(ambiguous["ok"])
        self.assertIn("name the intended rework gate", ambiguous["diagnostics"][0]["message"])
        resumed = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": current["task_ref"], "intent": "resume",
            "reason": "Repair network transport before the QA retry.",
            "payload": {"rework": "qa", "future_waves": recovery_waves},
        })
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual([item["phase"] for item in resumed["dispatches"]], ["plan"])
        state = self._state()
        self.assertEqual(state["status"], "active")
        self.assertEqual(set(state["rework_pauses"]), {"security"})


if __name__ == "__main__":
    unittest.main()
