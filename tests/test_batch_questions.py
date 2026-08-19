import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex as control


class BatchQuestionTests(unittest.TestCase):
    """Regression coverage for the ordinary-chat durable question boundary."""

    def setUp(self):
        self.original_cwd = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.ledger = self.project / ".codex" / "cortex"

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.temp.cleanup()

    def _start(self):
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Choose the safest implementation strategy and preserve the decision.",
                "user_language": "en",
                "acceptance_criteria": ["The user's selected strategy is recorded."],
                "verification": ["Poll the same durable question reference."],
            },
            "waves": [{"workers": [{"phase": "plan"}]}],
        })
        self.assertTrue(started["ok"], started)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        return started, state, state["attempts"][0]

    def _identity(self, state, attempt):
        return {
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
        }

    @staticmethod
    def _batch():
        return {
            "batch_key": "implementation-decisions-v1",
            "questions": [
                {
                    "question_key": "storage_strategy",
                    "question": "Which storage strategy should the implementation use?",
                    "type": "single_select",
                    "header": "Storage compatibility strategy",
                    "context": "A new schema increases migration risk; the existing schema preserves compatibility.",
                    "options": [
                        {"option_id": "existing_schema", "label_en": "Keep the existing schema", "description": "Lowest migration risk and preserves deployed readers."},
                        {"option_id": "new_schema", "label_en": "Create a new schema", "description": "Cleaner structure but requires a coordinated migration."},
                    ],
                    "recommended_option_ids": ["existing_schema"],
                    "recommendation": "Keep the existing schema because compatibility is the stated priority.",
                },
                {
                    "question_key": "compatibility_note",
                    "question": "What compatibility constraint should be treated as non-negotiable?",
                    "type": "text",
                    "header": "Required compatibility constraint",
                    "context": "The worker needs an explicit boundary before finalizing its plan.",
                    "recommended_answer": "Preserve all existing public API behavior.",
                    "recommendation": "Use this wording because it is concrete and directly verifiable.",
                },
            ],
        }

    def test_question_without_llm_recommendation_is_rejected_before_persistence(self):
        _, state, attempt = self._start()
        rejected = control.worker_question({
            **self._identity(state, attempt),
            "action": "ask",
            "question": "Which storage strategy should be used?",
            "header": "Storage strategy",
            "options": [
                {"option_id": "existing", "label_en": "Keep the existing schema"},
                {"option_id": "new", "label_en": "Create a new schema"},
            ],
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["outcome"], "needs_correction")
        self.assertIn("recommendation is required", rejected["diagnostics"][0]["message"])
        self.assertFalse(rejected["attempt_budget_consumed"])

    def test_batch_is_rendered_in_chat_with_explicit_recommendations_and_resumes(self):
        started, state, attempt = self._start()
        asked = control.worker_question({
            **self._identity(state, attempt),
            "action": "ask_batch",
            "batch": self._batch(),
        })
        self.assertEqual(asked["outcome"], "batch_recorded")
        batch_ref = asked["batch_ref"]
        surfaced = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "question",
            "payload": {"question_ref": batch_ref},
        })
        self.assertEqual(surfaced["outcome"], "awaiting_user")
        interaction = surfaced["chat_interaction"]
        self.assertEqual(interaction["schema"], "cortex/chat-interaction/v1")
        self.assertEqual(interaction["kind"], "worker_question")
        self.assertEqual(interaction["interaction_ref"], batch_ref)
        self.assertIn("End the turn immediately", interaction["coordinator_contract"])
        choice = interaction["questions"][0]["llm_recommendation"]
        self.assertEqual([item["option_id"] for item in choice["recommended_options"]], ["existing_schema"])
        self.assertTrue(choice["rationale"])
        self.assertEqual(interaction["questions"][1]["llm_recommendation"]["recommended_answer"], "Preserve all existing public API behavior.")
        answered = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "question",
            "payload": {
                "question_ref": batch_ref,
                "answers": {
                    "storage_strategy": "existing_schema",
                    "compatibility_note": "Preserve all existing public API behavior.",
                },
            },
        })
        self.assertEqual(answered["outcome"], "question_answered")
        polled = control.worker_question({**self._identity(state, attempt), "action": "poll_batch", "batch_ref": batch_ref})
        self.assertEqual(polled["outcome"], "batch_answered")
        self.assertEqual(polled["answers"]["storage_strategy"]["answer_option_ids"], ["existing_schema"])

    def test_single_question_next_chat_message_is_recorded_on_same_ref(self):
        started, state, attempt = self._start()
        asked = control.worker_question({
            **self._identity(state, attempt),
            "action": "ask",
            "question": "Which rollout policy should the implementation follow?",
            "header": "Rollout policy",
            "context": {"why": "The choice changes rollback risk."},
            "options": [
                {"option_id": "gradual", "label_en": "Use a gradual rollout", "description": "Limits blast radius and keeps rollback available."},
                {"option_id": "immediate", "label_en": "Use an immediate rollout", "description": "Faster but exposes all users at once."},
            ],
            "recommended_option_ids": ["gradual"],
            "recommendation": "Use a gradual rollout because it minimizes the irreversible blast radius.",
        })
        question_ref = asked["question_ref"]
        surfaced = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "question",
            "payload": {"question_ref": question_ref},
        })
        self.assertEqual(surfaced["outcome"], "awaiting_user")
        recommended = surfaced["chat_interaction"]["questions"][0]["llm_recommendation"]
        self.assertEqual(recommended["recommended_options"][0]["option_id"], "gradual")
        answered = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "question",
            "payload": {
                "question_ref": question_ref,
                "answer": {"option_ids": ["gradual"], "custom_response": "Keep rollback under five minutes."},
            },
        })
        self.assertEqual(answered["outcome"], "question_answered")
        polled = control.worker_question({**self._identity(state, attempt), "action": "poll", "question_ref": question_ref})
        self.assertEqual(polled["outcome"], "question_answered")
        self.assertEqual(polled["answer_option_ids"], ["gradual"])
        self.assertIn("five minutes", polled["answer_text"])

    def test_runtime_has_no_nested_elicitation_adapter(self):
        self.assertFalse(hasattr(control, "_request_mcp_elicitation"))


if __name__ == "__main__":
    unittest.main()
