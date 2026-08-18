import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex as control
from cortex_runtime import questions as runtime_questions


class BatchQuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.ledger = self.project / ".codex" / "cortex"

    def tearDown(self):
        self.temp.cleanup()

    def test_worker_question_distinguishes_caller_correction_from_durable_integrity_failure(self):
        with mock.patch.object(
            runtime_questions,
            "_worker_question_impl",
            side_effect=ValueError("question batch record failed validation"),
        ):
            blocked = control.worker_question({})
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["outcome"], "blocked")
        self.assertFalse(blocked["retryable"])
        self.assertFalse(blocked["attempt_budget_consumed"])

    def _start(self, *, user_language="ru"):
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Choose the implementation strategy",
                "user_language": user_language,
                "acceptance_criteria": ["The selected implementation strategy is recorded."],
                "verification": ["Run focused tests."],
            },
            "waves": [{"workers": [{"phase": "plan"}]}],
        })
        self.assertTrue(started["ok"], started)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        return started, task_dir, state, attempt

    def _batch(self, key="planner-clarifications-1"):
        return {
            "batch_key": key,
            "questions": [
                {
                    "question_key": "database_strategy",
                    "question": "Which database strategy should the implementation use?",
                    "type": "single_select",
                    "options": [
                        {"option_id": "use_existing_schema", "label_en": "Use the existing schema"},
                        {"option_id": "new_schema", "label_en": "Create a new schema"},
                    ],
                },
                {
                    "question_key": "migration_scope",
                    "question": "Which migration scopes are required?",
                    "type": "multi_select",
                    "options": [
                        {"option_id": "data", "label_en": "Data migration"},
                        {"option_id": "code", "label_en": "Application code"},
                    ],
                },
                {
                    "question_key": "extra_context",
                    "question": "What additional implementation context should the planner use?",
                    "type": "text",
                },
            ],
        }

    @staticmethod
    def _identity(state, attempt, project):
        return {
            "project_root": str(project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
        }

    def _ask(self, state, attempt, *, key="planner-clarifications-1"):
        return control.worker_question({
            **self._identity(state, attempt, self.project),
            "action": "ask_batch",
            "batch": self._batch(key),
        })

    def _record(self, state, batch_ref):
        record = control.db_get_task_document(
            self.ledger,
            state["task_id"],
            "question_batch:" + batch_ref,
        )
        self.assertIsNotNone(record)
        return record

    @staticmethod
    def _report(attempt):
        evidence = [control.dispatch_briefing_review_marker(attempt["briefing_digest"])]
        for label in ("Gate acceptance", "Gate verification", "Task acceptance", "Task verification"):
            for index in range(1, 9):
                evidence.append(
                    f"{label} {index}: PASS - focused test evidence confirms this criterion"
                )
        return {
            "summary": "Planner completed after the batch answer.",
            "findings": [],
            "questions": [],
            "changed_files": [],
            "tests": [{
                "command": "python3 -m unittest tests.test_batch_questions",
                "cwd": ".",
                "exit_code": 0,
                "evidence": "Focused batch-question tests passed.",
            }],
            "evidence": evidence,
            "uncertainty": [],
        }

    @staticmethod
    def _planning():
        return {
            "overview": "Use the recorded user decisions in the implementation plan.",
            "work_packages": [{
                "id": "core",
                "title": "Core delivery",
                "objective": "Implement the selected strategy.",
                "allowed_paths": ["src"],
                "microtasks": [{
                    "id": "core_change",
                    "title": "Implement the selected strategy",
                    "objective": "Deliver the agreed behavior.",
                    "profile": "backend_dev",
                    "allowed_paths": ["src"],
                    "acceptance_criteria": ["The selected strategy is implemented."],
                    "verification": ["Run focused tests."],
                }],
            }],
        }

    def test_localized_batch_uses_stable_ids_and_persists_translation_audit(self):
        started, _, state, attempt = self._start()
        asked = self._ask(state, attempt)
        self.assertEqual(asked["outcome"], "batch_recorded")
        batch_ref = asked["batch_ref"]
        localized = [
            {
                "question_key": "database_strategy",
                "question": "Какую стратегию базы данных использовать?",
                "options": [
                    {"option_id": "use_existing_schema", "label": "Использовать существующую схему"},
                    {"option_id": "new_schema", "label": "Создать новую схему"},
                ],
            },
            {
                "question_key": "migration_scope",
                "question": "Какие области миграции нужны?",
                "options": [
                    {"option_id": "data", "label": "Данные"},
                    {"option_id": "code", "label": "Код приложения"},
                ],
            },
            {
                "question_key": "extra_context",
                "question": "Какой дополнительный контекст нужен?",
            },
        ]
        with mock.patch.object(
            control,
            "_request_mcp_elicitation",
            side_effect=[
                ("accept", {"database_strategy": "use_existing_schema"}, "batch-form-1"),
                ("accept", {"migration_scope": ["data", "code"]}, "batch-form-2"),
                ("accept", {"extra_context": "Нужно сохранить обратную совместимость."}, "batch-form-3"),
            ],
        ) as elicitation:
            pending = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {
                    "question_ref": batch_ref,
                    "user_language": "ru",
                    "localized_questions": localized,
                },
            })
        self.assertTrue(pending["ok"], pending)
        self.assertEqual(pending["outcome"], "awaiting_translation")
        self.assertEqual(elicitation.call_count, 3)
        self.assertEqual([call.args[0] for call in elicitation.call_args_list], [
            "1 / 3", "2 / 3", "3 / 3",
        ])
        form = elicitation.call_args_list[0].args[1]
        self.assertEqual(set(form["properties"]), {"database_strategy"})
        self.assertEqual(form["properties"]["database_strategy"]["oneOf"][0], {
            "const": "use_existing_schema", "title": "Использовать существующую схему",
        })
        self.assertEqual(set(elicitation.call_args_list[1].args[1]["properties"]), {"migration_scope"})
        self.assertEqual(set(elicitation.call_args_list[2].args[1]["properties"]), {"extra_context"})

        durable = self._record(state, batch_ref)
        self.assertEqual(durable["status"], "awaiting_translation")
        self.assertEqual(durable["questions"][0]["canonical_question"], "Which database strategy should the implementation use?")
        self.assertEqual(durable["questions"][0]["options"][0]["label_en"], "Use the existing schema")
        self.assertEqual(durable["answers"]["database_strategy"]["answer_option_ids"], ["use_existing_schema"])
        self.assertEqual(durable["answers"]["extra_context"]["answer_original"], "Нужно сохранить обратную совместимость.")
        self.assertEqual(durable["answers"]["extra_context"]["translation_status"], "awaiting_translation")

        completed = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "question",
            "payload": {
                "question_ref": batch_ref,
                "canonical_answers": {
                    "extra_context": "Preserve backwards compatibility.",
                },
                "translated_by": "coordinator",
            },
        })
        self.assertTrue(completed["ok"], completed)
        self.assertEqual(completed["outcome"], "question_answered")
        durable = self._record(state, batch_ref)
        self.assertEqual(durable["status"], "answered")
        self.assertEqual(durable["answers"]["extra_context"]["answer_en"], "Preserve backwards compatibility.")
        self.assertEqual(durable["answers"]["extra_context"]["translated_by"], "coordinator")

        polled = control.worker_question({
            **self._identity(state, attempt, self.project),
            "action": "poll_batch",
            "batch_ref": batch_ref,
        })
        self.assertEqual(polled["outcome"], "batch_answered")
        self.assertEqual(polled["answers"]["database_strategy"], {
            "answer_en": "Use the existing schema",
            "answer_option_ids": ["use_existing_schema"],
        })
        self.assertEqual(polled["answers"]["extra_context"]["answer_en"], "Preserve backwards compatibility.")
        self.assertNotIn("Нужно", json.dumps(polled, ensure_ascii=False))

    def test_localized_batch_uses_canonical_positions_when_display_ids_change(self):
        started, _, state, attempt = self._start()
        asked = self._ask(state, attempt)
        localized = [
            {
                "question_key": "translated-question-one",
                "question": "Какую стратегию базы данных использовать?",
                "options": [
                    {"option_id": "translated-option-one", "label": "Текущая схема"},
                    {"option_id": "translated-option-two", "label": "Новая схема"},
                ],
            },
            {
                "question_key": "translated-question-one",
                "question": "Какие области миграции нужны?",
                "options": [
                    {"option_id": "display-a", "label": "Данные"},
                    {"option_id": "display-b", "label": "Код"},
                ],
            },
            {"question": "Какой дополнительный контекст нужен?"},
        ]
        with mock.patch.object(
            control,
            "_request_mcp_elicitation",
            side_effect=[
                ("accept", {"database_strategy": "use_existing_schema"}, "batch-position-1"),
                ("accept", {"migration_scope": ["data"]}, "batch-position-2"),
                ("accept", {"extra_context": "Сохранить совместимость."}, "batch-position-3"),
            ],
        ) as elicitation:
            pending = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {
                    "question_ref": asked["batch_ref"],
                    "user_language": "ru",
                    "localized_questions": localized,
                },
            })
        self.assertTrue(pending["ok"], pending)
        self.assertEqual(pending["outcome"], "awaiting_translation")
        first_form = elicitation.call_args_list[0].args[1]
        self.assertEqual(set(first_form["properties"]), {"database_strategy"})
        self.assertEqual(first_form["properties"]["database_strategy"]["oneOf"][0], {
            "const": "use_existing_schema", "title": "Текущая схема",
        })
        self.assertEqual(set(elicitation.call_args_list[1].args[1]["properties"]), {"migration_scope"})
        self.assertEqual(set(elicitation.call_args_list[2].args[1]["properties"]), {"extra_context"})

    def test_batch_persistence_is_atomic_and_poll_rejects_superseded_revision(self):
        started, _, state, attempt = self._start()
        invalid = self._batch()
        invalid["questions"].append(dict(invalid["questions"][0]))
        rejected = control.worker_question({
            **self._identity(state, attempt, self.project),
            "action": "ask_batch",
            "batch": invalid,
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["outcome"], "needs_correction")
        self.assertTrue(rejected["retryable"])
        self.assertFalse(rejected["attempt_budget_consumed"])
        self.assertIn("question_key values must be unique", rejected["diagnostics"][0]["message"])
        documents = control.db_list_task_documents(self.ledger, state["task_id"], "question_batch:")
        self.assertEqual(documents, [])

        asked = self._ask(state, attempt)
        batch_ref = asked["batch_ref"]
        durable = self._record(state, batch_ref)
        self.assertEqual(len(durable["questions"]), 3)
        self.assertEqual(durable["status"], "open")

        steered = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "steer",
            "payload": {
                "user_message": "Also require a compatibility audit.",
                "user_language": "en",
            },
        })
        self.assertTrue(steered["ok"], steered)
        superseded = control.worker_question({
            **self._identity(state, attempt, self.project),
            "action": "poll_batch",
            "batch_ref": batch_ref,
        })
        self.assertEqual(superseded["outcome"], "batch_superseded")
        self.assertFalse(superseded["resume"])
        self.assertEqual(self._record(state, batch_ref)["status"], "superseded")

    def test_localized_option_only_batch_derives_english_without_translation(self):
        started, _, state, attempt = self._start()
        asked = control.worker_question({
            **self._identity(state, attempt, self.project),
            "action": "ask_batch",
            "batch": {
                "batch_key": "planner-clarifications-options-only",
                "questions": [{
                    "question_key": "database_strategy",
                    "question": "Which database strategy should the implementation use?",
                    "type": "single_select",
                    "options": [
                        {"option_id": "use_existing_schema", "label_en": "Use the existing schema"},
                        {"option_id": "new_schema", "label_en": "Create a new schema"},
                    ],
                }],
            },
        })
        with mock.patch.object(
            control,
            "_request_mcp_elicitation",
            return_value=("accept", {"database_strategy": "use_existing_schema"}, "batch-form-options"),
        ):
            answered = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {
                    "question_ref": asked["batch_ref"],
                    "user_language": "ru",
                    "localized_questions": [{
                        "question_key": "database_strategy",
                        "question": "Какую стратегию базы данных использовать?",
                        "options": [
                            {"option_id": "use_existing_schema", "label": "Использовать существующую схему"},
                            {"option_id": "new_schema", "label": "Создать новую схему"},
                        ],
                    }],
                },
            })
        self.assertTrue(answered["ok"], answered)
        self.assertEqual(answered["outcome"], "question_answered")
        durable = self._record(state, asked["batch_ref"])
        self.assertEqual(durable["translation_status"], "not_required")
        self.assertEqual(durable["answers"]["database_strategy"]["answer_en"], "Use the existing schema")
        polled = control.worker_question({
            **self._identity(state, attempt, self.project),
            "action": "poll_batch",
            "batch_ref": asked["batch_ref"],
        })
        self.assertEqual(polled["answers"]["database_strategy"]["answer_option_ids"], ["use_existing_schema"])

    def test_open_batch_blocks_report_until_every_item_is_answered(self):
        started, _, state, attempt = self._start(user_language="en")
        asked = self._ask(state, attempt)
        report_payload = {
            **self._identity(state, attempt, self.project),
            "report": self._report(attempt),
            "planning": self._planning(),
        }
        blocked = control.publish_worker_report(report_payload)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["code"], "blocking_question_open")
        self.assertIn(asked["batch_ref"], blocked["diagnostics"][0]["message"])

        with mock.patch.object(
            control,
            "_request_mcp_elicitation",
            side_effect=[
                ("accept", {"database_strategy": "use_existing_schema"}, "batch-form-report-1"),
                ("accept", {"migration_scope": ["data"]}, "batch-form-report-2"),
                ("accept", {"extra_context": "Preserve compatibility during rollout."}, "batch-form-report-3"),
            ],
        ):
            answered = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {"question_ref": asked["batch_ref"], "user_language": "en"},
            })
        self.assertTrue(answered["ok"], answered)
        self.assertEqual(answered["outcome"], "question_answered")
        accepted = control.publish_worker_report(report_payload)
        self.assertTrue(accepted["ok"], accepted)

    def test_cancelled_batch_resumes_at_the_next_unanswered_question(self):
        started, _, state, attempt = self._start(user_language="en")
        asked = self._ask(state, attempt)
        batch_ref = asked["batch_ref"]

        with mock.patch.object(
            control,
            "_request_mcp_elicitation",
            side_effect=[
                ("accept", {"database_strategy": "use_existing_schema"}, "batch-step-1"),
                ("cancel", None, "batch-step-2-cancel"),
            ],
        ) as first_run:
            cancelled = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {"question_ref": batch_ref, "user_language": "en"},
            })
        self.assertTrue(cancelled["ok"], cancelled)
        self.assertEqual(cancelled["outcome"], "awaiting_user")
        self.assertEqual(first_run.call_count, 2)
        durable = self._record(state, batch_ref)
        self.assertEqual(durable["status"], "open")
        self.assertEqual(durable["answered_count"], 1)
        self.assertEqual(durable["next_question_key"], "migration_scope")
        self.assertEqual(set(durable["answers"]), {"database_strategy"})

        with mock.patch.object(
            control,
            "_request_mcp_elicitation",
            side_effect=[
                ("accept", {"migration_scope": ["data"]}, "batch-step-2"),
                ("accept", {"extra_context": "Preserve compatibility."}, "batch-step-3"),
            ],
        ) as resumed_run:
            answered = control.manage_orchestration({
                "project_root": str(self.project),
                "task_ref": started["task_ref"],
                "intent": "question",
                "payload": {"question_ref": batch_ref, "user_language": "en"},
            })
        self.assertTrue(answered["ok"], answered)
        self.assertEqual(answered["outcome"], "question_answered")
        self.assertEqual(resumed_run.call_count, 2)
        self.assertEqual(set(resumed_run.call_args_list[0].args[1]["properties"]), {"migration_scope"})
        self.assertEqual(set(resumed_run.call_args_list[1].args[1]["properties"]), {"extra_context"})
        durable = self._record(state, batch_ref)
        self.assertEqual(durable["status"], "answered")
        self.assertEqual(durable["answered_count"], 3)
        self.assertIsNone(durable["next_question_key"])


if __name__ == "__main__":
    unittest.main()
