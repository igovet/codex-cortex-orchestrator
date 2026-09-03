from __future__ import annotations

import tempfile
import unittest

from cortex_runtime.domain_api import open_clarification, open_steering, open_task, record_clarification, record_steering
from cortex_runtime.v12_service import V12ServiceError


class ClarificationHoldTests(unittest.TestCase):
    def _task(self, root: str) -> tuple[str, dict]:
        outcome = {"outcome": "Build.", "acceptance": ["Works."], "constraints": [], "verification": []}
        result = open_task(
            project_root=root,
            request_original="Build.",
            user_language="en",
            outcomes=[outcome],
            constraints=["No additional constraints."],
        )
        return result["task_ref"], outcome

    def test_one_pending_decision_is_resolved_without_public_binding(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task_ref, _ = self._task(root)
            opened = open_clarification(task_ref=task_ref, prompt="Which color?", prompt_language="en")
            self.assertEqual(set(opened), {"task_ref", "state", "replayed"})
            recorded = record_clarification(task_ref=task_ref, response_original="Blue.", user_language="en")
            self.assertEqual(recorded["state"], "clarification_recorded")

    def test_second_decision_cannot_open_while_one_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task_ref, _ = self._task(root)
            open_clarification(task_ref=task_ref, prompt="Which color?", prompt_language="en")
            with self.assertRaises(V12ServiceError):
                open_steering(task_ref=task_ref, prompt="Change scope?", prompt_language="en")

    def test_steering_uses_semantic_outcomes_not_item_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task_ref, current = self._task(root)
            open_steering(task_ref=task_ref, prompt="Replace outcome?", prompt_language="en")
            replacement = {"outcome": "Build safely.", "acceptance": ["Safe."], "constraints": [], "verification": []}
            result = record_steering(task_ref=task_ref, response_original="Replace it.", user_language="en", add=[replacement], retire=[current["outcome"]])
            self.assertEqual(result["state"], "steering_recorded")


if __name__ == "__main__":
    unittest.main()
