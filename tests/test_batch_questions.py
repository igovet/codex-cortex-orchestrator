import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex as control
from cortex_runtime import attempt_protocol


class BatchQuestionTests(unittest.TestCase):
    """Regression coverage for the ordinary-chat durable question boundary."""

    def setUp(self):
        self.original_cwd = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.host_store = Path(self.temp.name) / "host-private-store"
        self.host_store.mkdir(mode=0o700)
        self.host_store.chmod(0o700)
        self._previous_host_store = os.environ.get(control.HOST_CONTROL_STORE_ENV)
        os.environ[control.HOST_CONTROL_STORE_ENV] = str(self.host_store)
        self.ledger = control.ledger_root_path({"project_root": str(self.project)})

    def tearDown(self):
        os.chdir(self.original_cwd)
        if self._previous_host_store is None:
            os.environ.pop(control.HOST_CONTROL_STORE_ENV, None)
        else:
            os.environ[control.HOST_CONTROL_STORE_ENV] = self._previous_host_store
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
                    "header": "Storage migration strategy",
                    "context": "A new schema increases migration risk; the existing schema preserves required behavior.",
                    "options": [
                        {"option_id": "existing_schema", "label_en": "Keep the existing schema", "description": "Lowest migration risk and preserves deployed readers."},
                        {"option_id": "new_schema", "label_en": "Create a new schema", "description": "Cleaner structure but requires a coordinated migration."},
                    ],
                    "recommended_option_ids": ["existing_schema"],
                    "recommendation": "Keep the existing schema because preserving required behavior is the stated priority.",
                },
                {
                    "question_key": "migration_note",
                    "question": "What migration constraint should be treated as non-negotiable?",
                    "type": "text",
                    "header": "Required migration constraint",
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

    def test_large_question_and_answer_are_stored_losslessly(self):
        """Prompt compactness guidance never becomes a durable size gate."""
        _, state, attempt = self._start()
        identity = self._identity(state, attempt)
        valid = {
            **identity,
            "action": "ask",
            "question": "Which rollback boundary should the implementation preserve?",
            "header": "Rollback boundary",
            "recommendation": "Keep the rollback path because the implementation can then be safely reversed.",
            "recommended_answer": "Preserve a tested rollback path for the deployment.",
        }
        complete_question = {
            **valid,
            "question": "🙂" * 4_001,
            "context": {str(index): "🙂" * 2_000 for index in range(10)},
        }
        asked = control.worker_question(complete_question)
        self.assertTrue(asked["ok"], asked)
        question_ref = asked["question_ref"]
        answered_large = control.answer_worker_question({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "thread_id": state["thread_id"],
            "question_id": question_ref,
            "submission_id": "large-answer",
            "answer": "🙂" * 8_001,
            "resume_context": {"source": "main_chat", "detail": "🙂" * 4_000},
        })
        self.assertEqual(answered_large["status"], "answered", answered_large)
        open_questions = control.list_worker_questions({
            "project_root": str(self.project), "task_id": state["task_id"],
            "principal": state["principal"], "thread_id": state["thread_id"], "status": "open",
        })
        self.assertEqual(open_questions["questions"], [])
        self.assertEqual(answered_large["question"]["attempt_id"], attempt["attempt_id"])

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
        self.assertNotIn("resume_contract", surfaced)
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
                "migration_note": "Preserve all required public API behavior.",
                },
            },
        })
        self.assertEqual(answered["outcome"], "question_answered")
        self.assertEqual(answered["resume_contract"], {
            "batch_ref": batch_ref,
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "poll_action": "poll_batch",
        })
        self.assertNotIn("target", answered["resume_contract"])
        polled = control.worker_question({**self._identity(state, attempt), "action": "poll_batch", "batch_ref": batch_ref})
        self.assertEqual(polled["outcome"], "batch_answered")
        self.assertEqual(polled["answers"]["storage_strategy"]["answer_option_ids"], ["existing_schema"])
        events = attempt_protocol.list_attempt_events(
            self.ledger,
            task_id=str(state["task_id"]),
            attempt_id=str(attempt["attempt_id"]),
        )
        event_types = [event["event_type"] for event in events]
        self.assertEqual(event_types.count("question_created"), 2)
        self.assertEqual(event_types.count("question_answered"), 2)
        self.assertEqual(event_types.count("decision_resolved"), 2)

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
        self.assertNotIn("resume_contract", surfaced)
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
        self.assertEqual(answered["resume_contract"], {
            "question_ref": question_ref,
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "poll_action": "poll",
        })
        polled = control.worker_question({**self._identity(state, attempt), "action": "poll", "question_ref": question_ref})
        self.assertEqual(polled["outcome"], "question_answered")
        self.assertEqual(polled["answer_option_ids"], ["gradual"])
        self.assertIn("five minutes", polled["answer_text"])
        events = attempt_protocol.list_attempt_events(
            self.ledger,
            task_id=str(state["task_id"]),
            attempt_id=str(attempt["attempt_id"]),
        )
        event_types = [event["event_type"] for event in events]
        self.assertEqual(
            event_types[-3:],
            ["question_created", "question_answered", "decision_resolved"],
        )
        self.assertTrue(all(event["actor"] == "cortex" for event in events[-3:]))
        resolved = events[-1]["payload"]
        self.assertEqual(resolved["question_ref"], question_ref)
        self.assertIn("gradual", resolved["answer"])

    def test_runtime_has_no_nested_elicitation_adapter(self):
        self.assertFalse(hasattr(control, "_request_mcp_elicitation"))


if __name__ == "__main__":
    unittest.main()
