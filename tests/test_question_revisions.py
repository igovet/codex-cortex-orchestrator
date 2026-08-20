"""Regression coverage for revision-bound legacy single questions."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex as control
from cortex_runtime import questions


class QuestionRevisionTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.set_up_host_private_control_store()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()
        self.temp.cleanup()

    def _start(self) -> tuple[dict, dict, dict]:
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Choose one compatible rollout decision.",
                "complexity": "C1",
                "acceptance_criteria": ["The selected decision is durable."],
                "verification": ["Poll the same durable question reference."],
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        })
        ledger = control.ledger_root_path({"project_root": str(self.project)})
        task_dir = next((ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        return started, state, state["attempts"][0]

    def _identity(self, state: dict, attempt: dict) -> dict:
        return {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
        }

    @staticmethod
    def _question(*, audit: bool = False) -> dict:
        return {
            "action": "ask",
            "question": (
                "Which compatible audit format should the implementation use?"
                if audit else "Which compatible rollout policy should the implementation use?"
            ),
            "header": "Compatible audit format" if audit else "Compatible rollout policy",
            "options": [{
                "option_id": "structured_json" if audit else "gradual",
                "label_en": "Use structured JSON audit records" if audit else "Use a gradual rollout",
                "description": "Keeps a stable machine-readable contract." if audit else "Preserves rollback and limits the blast radius.",
            }],
            "recommended_option_ids": ["structured_json" if audit else "gradual"],
            "recommendation": (
                "Use structured JSON records because the schema remains stable for verification."
                if audit else "Use a gradual rollout because it preserves a bounded rollback path."
            ),
        }

    def test_single_question_stales_on_material_task_revision_and_never_delivers_old_answer(self) -> None:
        started, state, attempt = self._start()
        asked = control.worker_question({**self._identity(state, attempt), **self._question()})
        self.assertEqual(asked["status"], "open")
        original = questions.list_worker_questions({
            "project_root": str(self.project), "task_id": state["task_id"],
            "principal": state["principal"], "thread_id": state["thread_id"],
        })["questions"][0]
        self.assertEqual(original["task_revision"], 1)
        self.assertIn("plan_revision", original)
        self.assertEqual(original["attempt_generation"], 1)

        steered = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "steer",
            "payload": {"user_message": "Require durable audit logging in the result path.", "user_language": "en"},
        })
        self.assertEqual(steered["task_revision"], 2)

        listed = questions.list_worker_questions({
            "project_root": str(self.project), "task_id": state["task_id"],
            "principal": state["principal"], "thread_id": state["thread_id"],
        })
        stale = listed["questions"][0]
        self.assertEqual(stale["status"], "superseded")
        self.assertIn("task revision", stale["superseded_reason"])

        polled = control.worker_question({
            **self._identity(state, attempt), "action": "poll", "question_ref": asked["question_ref"],
        })
        self.assertEqual(polled["outcome"], "question_superseded")
        answered = questions.answer_worker_question({
            "project_root": str(self.project), "task_id": state["task_id"],
            "principal": state["principal"], "thread_id": state["thread_id"],
            "question_id": asked["question_ref"], "submission_id": "answer-stale-question",
            "answer": {"option_ids": ["gradual"]}, "resume_context": {"user_language": "en"},
        })
        self.assertEqual(answered["status"], "superseded")
        self.assertFalse(answered["resume"])

    def test_task_question_quota_is_scoped_to_the_current_revision_not_lifetime_history(self) -> None:
        started, state, attempt = self._start()
        with mock.patch.object(questions, "MAX_QUESTIONS_PER_TASK", 1):
            asked = control.worker_question({**self._identity(state, attempt), **self._question()})
            self.assertEqual(asked["status"], "open")
            control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "steer",
                "payload": {"user_message": "Add a durable audit requirement.", "user_language": "en"},
            })
            # The first question belongs to the superseded revision.  The
            # current revision retains a fresh bounded decision budget.
            replacement = control.worker_question({**self._identity(state, attempt), **self._question(audit=True)})
            self.assertEqual(replacement["status"], "open")


if __name__ == "__main__":
    unittest.main()
