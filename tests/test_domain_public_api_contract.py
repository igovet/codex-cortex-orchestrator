from __future__ import annotations

import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from cortex import PUBLIC_TOOLS, SERVER_VERSION
from cortex_runtime.domain_api import open_assignment, open_task, publish_result, read_task


PROVENANCE = {name: "sha256:" + "a" * 64 for name in ("build_digest", "candidate_digest", "source_digest", "catalogue_digest")}


class DomainPublicApiContractTests(unittest.TestCase):
    def _task(self, root: str, outcomes: list[dict] | None = None) -> tuple[dict, list[dict]]:
        outcomes = outcomes or [{"outcome": "Build the artifact.", "acceptance": ["The artifact works."], "constraints": [], "verification": []}]
        task = open_task(project_root=root, request_original="Build it.", user_language="en", outcomes=outcomes, constraints=["Keep public identity minimal."])
        return task, outcomes

    def _assignment(self, task_ref: str, outcome: dict, role: str) -> dict:
        return open_assignment(task_ref=task_ref, role=role, profile_name="explorer", model="gpt-5.6-luna", reasoning_effort="high", responsibility="evidence",
                               goal=f"Verify {role}.", scope="Read-only bounded scope.", instructions="Inspect and report.",
                               outcomes=[outcome], report_policy="none")

    def test_flat_task_open_and_state_read_expose_only_task_ref_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task, outcomes = self._task(root)
            self.assertEqual(set(task), {"task_ref", "replayed"})
            state = read_task(task_ref=task["task_ref"], view="state")
            self.assertEqual(state["data"]["effective_contract"]["items"], outcomes)
            rendered = repr(state)
            for name in ("item_ref", "report_ref", "decision_ref", "digest", "cursor", "handles"):
                self.assertNotIn(name, rendered)

    def test_open_assignment_returns_only_native_dispatch_and_replay_state(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            result = self._assignment(task["task_ref"], outcomes[0], "audit")
            self.assertEqual(set(result), {"native_dispatch", "replayed"})
            self.assertNotIn("assignment_ref", repr(result))
            self.assertNotIn("continuation_ref", repr(result))
            self.assertRegex(result["native_dispatch"]["message"], r'"task_ref":"t_[0-9a-f]{12}_[0-9a-f]{32}"')

    def test_parallel_workers_bind_distinct_assignments_even_when_read_in_reverse_order(self) -> None:
        outcomes = [
            {"outcome": "Audit A.", "acceptance": ["A verified."], "constraints": [], "verification": []},
            {"outcome": "Audit B.", "acceptance": ["B verified."], "constraints": [], "verification": []},
        ]
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, outcomes)
            with ThreadPoolExecutor(max_workers=2) as pool:
                assignments = list(pool.map(lambda pair: self._assignment(task["task_ref"], pair[1], pair[0]), (("a", outcomes[0]), ("b", outcomes[1]))))
            worker_refs = [re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', item["native_dispatch"]["message"]).group(1) for item in assignments]
            self.assertEqual(len(set(worker_refs)), 2)
            contexts = [{}, {}]
            second = read_task(task_ref=worker_refs[1], view="assignment", _connection_context=contexts[1])
            first = read_task(task_ref=worker_refs[0], view="assignment", _connection_context=contexts[0])
            self.assertNotEqual(contexts[0]["assignment_id"], contexts[1]["assignment_id"])
            self.assertIn("Audit A.", repr(first))
            self.assertNotIn("Audit B.", repr(first["data"]["effective_contract"]))
            self.assertIn("Audit B.", repr(second))
            self.assertNotIn("Audit A.", repr(second["data"]["effective_contract"]))

    def test_restart_reconciles_consumed_assignment_without_model_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, outcomes = self._task(root)
            assignment = self._assignment(task["task_ref"], outcomes[0], "restart")
            worker_ref = re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', assignment["native_dispatch"]["message"]).group(1)
            first = read_task(task_ref=worker_ref, view="assignment", _connection_context={})
            restarted = read_task(task_ref=worker_ref, view="assignment", _connection_context={})
            self.assertEqual(first["data"]["effective_contract"], restarted["data"]["effective_contract"])
            self.assertNotIn("cursor", repr(restarted))

    def test_parallel_workers_publish_to_their_exact_assignments_in_reverse_order(self) -> None:
        outcomes = [
            {"outcome": "Implement A.", "acceptance": ["A works."], "constraints": [], "verification": []},
            {"outcome": "Implement B.", "acceptance": ["B works."], "constraints": [], "verification": []},
        ]
        with tempfile.TemporaryDirectory() as root, patch("cortex_runtime.domain_api._worker_capability_provenance", return_value=PROVENANCE):
            task, _ = self._task(root, outcomes)
            assignments = [self._assignment(task["task_ref"], outcome, label) for label, outcome in zip(("a", "b"), outcomes)]
            worker_refs = [re.search(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"', item["native_dispatch"]["message"]).group(1) for item in assignments]
            contexts = [{}, {}]
            read_task(task_ref=worker_refs[0], view="assignment", _connection_context=contexts[0])
            read_task(task_ref=worker_refs[1], view="assignment", _connection_context=contexts[1])

            def publish(index: int) -> dict:
                return publish_result(
                    task_ref=worker_refs[index], summary=f"Result {index}.", outcome="Verified.", changes=[],
                    verification_facts=[{"state": "executed", "summary": f"Check {index} passed."}],
                    outcome_coverage=[{"outcome": outcomes[index], "status": "complete", "verification": [f"Check {index} passed."]}],
                    documentation_impact="No documentation change.", risks=[], unresolved=[], status="completed",
                    _connection_context=contexts[index],
                )

            second, first = publish(1), publish(0)
            self.assertEqual(second["task_ref"], worker_refs[1])
            self.assertEqual(first["task_ref"], worker_refs[0])
            self.assertNotEqual(contexts[0]["assignment_id"], contexts[1]["assignment_id"])

    def test_version_and_catalogue_remain_current(self) -> None:
        self.assertEqual(SERVER_VERSION, "1.12.3")
        self.assertEqual(len(PUBLIC_TOOLS), 14)


if __name__ == "__main__":
    unittest.main()
