import copy
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex as control
from cortex_runtime import attempt_protocol, questions


class BatchQuestionTests(unittest.TestCase):
    """Regression coverage for the ordinary-chat durable question boundary."""

    def test_question_records_use_only_current_schema(self):
        self.assertEqual(control.QUESTION_SCHEMA, "cortex/question/v3")
        self.assertEqual(questions.QUESTION_SCHEMA, "cortex/question/v3")
        with self.assertRaisesRegex(ValueError, "schema is not supported"):
            questions._question_record_view({"schema": "cortex/question/not-current"})
        self.assertEqual(
            questions._question_record_view({"schema": "cortex/question/v3", "question_id": "q-1"})["schema"],
            "cortex/question/v3",
        )

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
        self._worker_pairs: dict[str, dict[str, str]] = {}

    def tearDown(self):
        os.chdir(self.original_cwd)
        if self._previous_host_store is None:
            os.environ.pop(control.HOST_CONTROL_STORE_ENV, None)
        else:
            os.environ[control.HOST_CONTROL_STORE_ENV] = self._previous_host_store
        self.temp.cleanup()

    def _start(self, *, user_language="en"):
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Choose the safest implementation strategy and preserve the decision.",
                "user_language": user_language,
                "acceptance_criteria": ["The user's selected strategy is recorded."],
                "verification": ["Poll the same durable question reference."],
            },
            "waves": [{"workers": [{"phase": "plan"}]}],
        })
        self.assertTrue(started["ok"], started)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        bootstrap = str(started["dispatches"][0]["arguments"]["message"])
        match = re.search(r"read_dispatch_briefing\((\{[^\n]+?\})\)", bootstrap)
        self.assertIsNotNone(match, "native bootstrap must carry the worker capability pair")
        assert match is not None
        pair = json.loads(match.group(1))
        self.assertEqual(set(pair), {"task_ref", "assignment_ref"})
        self._worker_pairs[str(attempt["attempt_id"])] = pair
        return started, state, attempt

    def _worker_question(self, state, attempt, params):
        """Invoke through the exact capability pair carried by native bootstrap."""
        params = dict(params)
        if params.get("action") == "ask":
            params.setdefault("decision_scope", "task_decision")
        pair = self._worker_pairs.get(str(attempt["attempt_id"]))
        self.assertIsNotNone(pair, "only a native bootstrap may authorize a worker call")
        return control.worker_question({**pair, **params})

    @staticmethod
    def _batch():
        return {
            "batch_key": "implementation-decisions-v1",
            "questions": [
                {
                    "question_key": "storage_strategy",
                    "question": "Which storage strategy should the implementation use?",
                    "question_type": "single_select",
                    "decision_scope": "task_decision",
                    "header": "Storage migration strategy",
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
                    "question_type": "text",
                    "decision_scope": "task_decision",
                    "header": "Required migration constraint",
                    "recommended_answer": "Preserve all existing public API behavior.",
                    "recommendation": "Use this wording because it is concrete and directly verifiable.",
                },
            ],
        }

    def test_live_ask_missing_recommendation_has_exact_retry_and_creates_one_question(self):
        _, state, attempt = self._start()
        live_ask = {
            "action": "ask",
            "question_type": "single_select",
            "decision_scope": "task_decision",
            "question": "Which storage strategy should be used?",
            "header": "Storage strategy",
            "options": [
                {"option_id": "existing", "label_en": "Keep the existing schema"},
                {"option_id": "new", "label_en": "Create a new schema"},
            ],
            "recommended_option_ids": ["existing"],
        }
        rejected = self._worker_question(state, attempt, live_ask)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["schema"], "cortex/worker-question/v11")
        self.assertEqual(rejected["recovery"], {
            "kind": "same_operation",
            "operation": "worker_question",
            "retryable": True,
            "state_mutated": False,
            "allowed_changes": [{"json_pointer": "/recommendation", "allowed_ops": ["add"]}],
        })
        self.assertEqual(rejected["error"]["diagnostics"], [{
            "code": "worker_question_request_invalid",
            "json_pointer": "/recommendation",
            "message": "recommendation is required and must be a non-empty string",
            "field_schema": {"type": "string", "minLength": 1},
        }])
        self.assertNotIn("received", rejected["error"]["diagnostics"][0])
        self.assertNotIn("expected", rejected["error"]["diagnostics"][0])
        self.assertNotIn("validation", rejected)
        self.assertNotIn("repair", rejected)

        recorded = self._worker_question(state, attempt, {
            **live_ask,
            "recommendation": "Keep the existing schema because it preserves the deployed migration path and is fully reversible.",
        })
        self.assertEqual(set(recorded), {"schema", "ok", "outcome", "question_ref"})
        self.assertTrue(recorded["ok"])
        self.assertEqual(recorded["outcome"], "question_recorded")
        events = attempt_protocol.list_attempt_events(
            self.ledger,
            task_id=str(state["task_id"]),
            attempt_id=str(attempt["attempt_id"]),
        )
        self.assertEqual([event["event_type"] for event in events].count("question_created"), 1)

    def test_question_error_path_keeps_recommendation_fields_specific(self):
        self.assertEqual(
            questions._worker_question_error_path(
                "worker question recommendation is required and must explain why the suggested answer is safest or best"
            ),
            "recommendation",
        )
        self.assertEqual(
            questions._worker_question_error_path("choice questions require recommended_option_ids"),
            "recommended_option_ids",
        )
        self.assertEqual(
            questions._worker_question_error_path("text questions require a concrete recommended_answer"),
            "recommended_answer",
        )
        self.assertEqual(questions._worker_question_error_path("ask requires question"), "question")
        self.assertEqual(questions._worker_question_error_path("worker question action is unsupported"), "action")
        self.assertEqual(questions._worker_question_error_path("profile must be exact"), "profile")

    def test_question_diagnostic_converts_nested_batch_indexes_to_rfc6901(self):
        diagnostic = questions._question_diagnostic(
            "$.batch.questions[0].recommendation",
            "recommendation is required",
        )
        self.assertEqual(diagnostic["json_pointer"], "/batch/questions/0/recommendation")
        self.assertEqual(diagnostic["field_schema"], {"type": "string", "minLength": 1})

    def test_batch_recommendation_validation_has_exact_nested_retry_paths(self):
        cases = (
            (0, "recommendation", None, {"type": "string", "minLength": 1}, "add"),
            (0, "recommendation", "Безопасный выбор", {"type": "string", "minLength": 1}, "replace"),
            (0, "recommended_option_ids", None, {"type": "array", "minItems": 1, "uniqueItems": True}, "add"),
            (0, "recommended_option_ids", ["not-a-defined-option"], {"type": "array", "minItems": 1, "uniqueItems": True}, "replace"),
            (1, "recommended_answer", None, {"type": "string", "minLength": 1}, "add"),
            (1, "recommended_answer", "Сохранить API", {"type": "string", "minLength": 1}, "replace"),
        )
        for index, field, value, schema, operation in cases:
            with self.subTest(field=field, operation=operation):
                _, state, attempt = self._start()
                batch = copy.deepcopy(self._batch())
                if value is None:
                    del batch["questions"][index][field]
                else:
                    batch["questions"][index][field] = value
                rejected = self._worker_question(state, attempt, {
                    "action": "ask_batch",
                    "batch": batch,
                })
                pointer = f"/batch/questions/{index}/{field}"
                self.assertFalse(rejected["ok"])
                self.assertEqual(len(rejected["error"]["diagnostics"]), 1)
                diagnostic = rejected["error"]["diagnostics"][0]
                self.assertEqual(diagnostic["code"], "worker_question_request_invalid")
                self.assertEqual(diagnostic["json_pointer"], pointer)
                self.assertIn(field, diagnostic["message"])
                self.assertEqual(diagnostic["field_schema"], schema)
                self.assertEqual(rejected["recovery"], {
                    "kind": "same_operation",
                    "operation": "worker_question",
                    "retryable": True,
                    "state_mutated": False,
                    "allowed_changes": [{"json_pointer": pointer, "allowed_ops": [operation]}],
                })

    def test_batch_is_rendered_in_chat_with_explicit_recommendations_and_resumes(self):
        started, state, attempt = self._start()
        asked = self._worker_question(state, attempt, {
            "action": "ask_batch",
            "batch": self._batch(),
        })
        self.assertEqual(asked["outcome"], "batch_recorded")
        self.assertEqual(set(asked), {"schema", "ok", "outcome", "batch_ref"})
        batch_ref = asked["batch_ref"]
        surfaced = control.manage_orchestration({
            "task_ref": started["task_ref"],
            "coordinator_ref": started["coordinator_ref"],
            "intent": "question",
            "payload": {"question_ref": batch_ref},
        })
        self.assertEqual(surfaced["outcome"], "awaiting_user")
        self.assertEqual(surfaced["batch_ref"], batch_ref)
        self.assertEqual(surfaced["progress"], {
            "answered": 0, "total": 2, "next_question_key": "storage_strategy",
        })
        self.assertTrue(surfaced["question"]["prompt"])
        self.assertEqual([item["number"] for item in surfaced["question"]["options"]], [1, 2])
        self.assertNotIn("chat_interaction", surfaced)
        answered = control.manage_orchestration({
            "task_ref": started["task_ref"],
            "coordinator_ref": started["coordinator_ref"],
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
        self.assertEqual(answered["resume"], {
            "kind": "poll_batch",
            "batch_ref": batch_ref,
        })
        polled = self._worker_question(state, attempt, {"action": "poll_batch", "batch_ref": batch_ref})
        self.assertEqual(polled["outcome"], "batch_answered")
        self.assertEqual(polled["answers"]["storage_strategy"]["option_ids"], ["existing_schema"])
        self.assertEqual(set(polled), {"schema", "ok", "outcome", "batch_ref", "progress", "answers"})
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
        asked = self._worker_question(state, attempt, {
            "action": "ask",
            "question_type": "single_select",
            "decision_scope": "task_decision",
            "question": "Which rollout policy should the implementation follow?",
            "header": "Rollout policy",
            "options": [
                {"option_id": "gradual", "label_en": "Use a gradual rollout", "description": "Limits blast radius and keeps rollback available."},
                {"option_id": "immediate", "label_en": "Use an immediate rollout", "description": "Faster but exposes all users at once."},
            ],
            "recommended_option_ids": ["gradual"],
            "recommendation": "Use a gradual rollout because it minimizes the irreversible blast radius.",
        })
        self.assertEqual(set(asked), {"schema", "ok", "outcome", "question_ref"})
        question_ref = asked["question_ref"]
        surfaced = control.manage_orchestration({
            "task_ref": started["task_ref"],
            "coordinator_ref": started["coordinator_ref"],
            "intent": "question",
            "payload": {"question_ref": question_ref},
        })
        self.assertEqual(surfaced["outcome"], "awaiting_user")
        self.assertEqual(surfaced["question_ref"], question_ref)
        self.assertEqual([item["number"] for item in surfaced["question"]["options"]], [1, 2])
        self.assertNotIn("chat_interaction", surfaced)
        answered = control.manage_orchestration({
            "task_ref": started["task_ref"],
            "coordinator_ref": started["coordinator_ref"],
            "intent": "question",
            "payload": {
                "question_ref": question_ref,
                "answer": {"option_ids": ["gradual"], "custom_response": "Keep rollback under five minutes."},
            },
        })
        self.assertEqual(answered["outcome"], "question_answered")
        self.assertEqual(answered["resume"], {
            "kind": "poll",
            "question_ref": question_ref,
        })
        polled = self._worker_question(state, attempt, {"action": "poll", "question_ref": question_ref})
        self.assertEqual(polled["outcome"], "question_answered")
        self.assertEqual(polled["answer"]["option_ids"], ["gradual"])
        self.assertIn("five minutes", polled["answer"]["text"])
        self.assertEqual(set(polled), {"schema", "ok", "outcome", "question_ref", "answer"})
        self.assertNotIn("answer_text", polled)
        self.assertNotIn("answer_option_ids", polled)
        self.assertNotIn("resume_context", polled)
        self.assertNotIn("next_action", polled)
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

    def test_question_contract_returns_structured_scope_and_localization_corrections_then_resumes_once(self):
        """A Russian durable question exposes only correctable public states."""
        worker_schema = control.PUBLIC_SCHEMA_REGISTRY["worker_question"]
        self.assertEqual(
            worker_schema["properties"]["question_type"]["enum"],
            ["single_select", "multi_select", "text"],
        )
        self.assertIn("task_decision", worker_schema["properties"]["decision_scope"]["enum"])
        self.assertNotIn("context", worker_schema["properties"])
        self.assertNotIn("multiple", worker_schema["properties"])
        batch_item_union = worker_schema["properties"]["batch"]["properties"]["questions"]["items"]["oneOf"]
        batch_scope_schema = batch_item_union[0]["properties"]["decision_scope"]
        self.assertTrue(all(
            branch["properties"]["decision_scope"] == batch_scope_schema
            for branch in batch_item_union
        ))
        self.assertEqual(batch_scope_schema["type"], "string")
        self.assertTrue(all("question_type" in branch["required"] for branch in batch_item_union))
        self.assertTrue(all("type" not in branch["properties"] for branch in batch_item_union))
        self.assertIn(
            "option_id",
            worker_schema["properties"]["options"]["items"]["required"],
        )
        management = control.PUBLIC_SCHEMA_REGISTRY["manage_orchestration"]
        question_branch = next(
            branch for branch in management["oneOf"]
            if branch["properties"]["intent"]["const"] == "question"
        )
        localized_branch = next(
            branch for branch in question_branch["properties"]["payload"]["oneOf"]
            if "localized_question" in branch["properties"]
        )
        question_payload = localized_branch["properties"]
        self.assertTrue({
            "question_ref", "localized_question", "localized_header",
            "localized_options", "localized_custom_label",
        }.issubset(question_payload))

        started, state, attempt = self._start(user_language="ru")
        pair = self._worker_pairs[str(attempt["attempt_id"])]
        invalid_scope = control.worker_question({**pair,
            "action": "ask",
            "question_type": "single_select",
            "question": "Which safe mode should be used?",
            "header": "Safe mode",
            "options": [
                {"option_id": "safe_mode", "label_en": "Use safe mode"},
                {"option_id": "fast_mode", "label_en": "Use fast mode"},
            ],
            "recommended_option_ids": ["safe_mode"],
            "recommendation": "Safe mode limits the irreversible risk.",
        })
        self.assertFalse(invalid_scope["ok"])
        self.assertTrue(invalid_scope["recovery"]["retryable"])
        self.assertFalse(invalid_scope["recovery"]["state_mutated"])
        self.assertEqual(
            invalid_scope["error"]["diagnostics"][0]["json_pointer"],
            "/decision_scope",
        )

        unknown_scope = control.worker_question({**pair,
            "action": "ask",
            "question_type": "single_select",
            "decision_scope": "invented_scope",
            "question": "Which safe mode should be used?",
            "header": "Safe mode",
            "options": [
                {"option_id": "safe_mode", "label_en": "Use safe mode"},
                {"option_id": "fast_mode", "label_en": "Use fast mode"},
            ],
            "recommended_option_ids": ["safe_mode"],
            "recommendation": "Safe mode limits the irreversible risk.",
        })
        self.assertFalse(unknown_scope["ok"])
        self.assertTrue(unknown_scope["recovery"]["retryable"])
        self.assertFalse(unknown_scope["recovery"]["state_mutated"])
        self.assertEqual(
            unknown_scope["error"]["diagnostics"][0]["json_pointer"],
            "/decision_scope",
        )
        self.assertIn(
            "task_decision",
            unknown_scope["error"]["diagnostics"][0]["field_schema"]["enum"],
        )

        asked = self._worker_question(state, attempt, {
            "action": "ask",
            "question_type": "single_select",
            "decision_scope": "task_decision",
            "question": "Which safe mode should be used?",
            "header": "Safe mode",
            "options": [
                {"option_id": "safe_mode", "label_en": "Use safe mode"},
                {"option_id": "fast_mode", "label_en": "Use fast mode"},
            ],
            "recommended_option_ids": ["safe_mode"],
            "recommendation": "Safe mode limits the irreversible risk.",
        })
        self.assertEqual(asked["outcome"], "question_recorded")
        question_ref = asked["question_ref"]
        durable_questions = questions.list_worker_questions({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "principal": state["principal"],
            "attempt_id": attempt["attempt_id"],
        })["questions"]
        self.assertEqual(len(durable_questions), 1)
        self.assertEqual(durable_questions[0]["question_type"], "single_select")
        self.assertEqual(
            [item["option_id"] for item in durable_questions[0]["options"]],
            ["safe_mode", "fast_mode"],
        )
        self.assertEqual(durable_questions[0]["recommended_option_ids"], ["safe_mode"])

        misplaced = control.manage_orchestration({
            "task_ref": started["task_ref"],
            "coordinator_ref": started["coordinator_ref"],
            "intent": "question",
            "localized_question": "Какой безопасный режим использовать?",
            "payload": {"question_ref": question_ref},
        })
        self.assertFalse(misplaced["ok"])
        self.assertTrue(misplaced["recovery"]["retryable"])
        self.assertFalse(misplaced["recovery"]["state_mutated"])

        canonical_display = control.manage_orchestration({
            "task_ref": started["task_ref"],
            "coordinator_ref": started["coordinator_ref"],
            "intent": "question",
            "payload": {"question_ref": question_ref},
        })
        self.assertTrue(canonical_display["ok"])
        self.assertEqual(canonical_display["outcome"], "awaiting_user")
        self.assertEqual(len(canonical_display["question"]["options"]), 2)

        invalid_options = control.manage_orchestration({
            "task_ref": started["task_ref"],
            "coordinator_ref": started["coordinator_ref"],
            "intent": "question",
            "payload": {
                "question_ref": question_ref,
                "localized_question": "Какой безопасный режим использовать?",
                "localized_header": "Безопасный режим",
                "localized_options": [
                    {"option_id": "safe_mode", "label": "Выбрать безопасный режим"},
                ],
                "localized_custom_label": "Другой вариант",
            },
        })
        self.assertFalse(invalid_options["ok"])
        self.assertEqual(invalid_options["error"]["code"], "question_management_validation_failed")
        self.assertTrue(invalid_options["recovery"]["retryable"])
        self.assertFalse(invalid_options["recovery"]["state_mutated"])
        self.assertEqual(
            invalid_options["error"]["diagnostics"][0]["json_pointer"],
            "/payload/localized_options",
        )
        self.assertEqual(
            invalid_options["error"]["diagnostics"][0]["field_schema"]["type"],
            "array",
        )
        self.assertEqual(invalid_options["error"]["diagnostics"][0]["field_schema"]["minItems"], 2)
        self.assertEqual(invalid_options["recovery"]["allowed_changes"], [{
            "json_pointer": "/payload/localized_options", "allowed_ops": ["replace"],
        }])

        display_payload = {
            "question_ref": question_ref,
            "localized_question": "Какой безопасный режим использовать?",
            "localized_header": "Безопасный режим",
            "localized_options": [
                {"option_id": "safe_mode", "label": "Выбрать безопасный режим"},
                {"option_id": "fast_mode", "label": "Выбрать быстрый режим"},
            ],
            "localized_custom_label": "Другой вариант",
        }
        surfaced = control.manage_orchestration({
            "task_ref": started["task_ref"],
            "coordinator_ref": started["coordinator_ref"],
            "intent": "question",
            "payload": display_payload,
        })
        self.assertTrue(surfaced["ok"])
        self.assertEqual(surfaced["outcome"], "awaiting_user")
        self.assertEqual(surfaced["question_ref"], question_ref)
        self.assertEqual(surfaced["question"]["prompt"], display_payload["localized_question"])

        # Re-showing the same durable card is read-only: it neither creates a
        # second question nor consumes a second user decision.
        replay = control.manage_orchestration({
            "task_ref": started["task_ref"],
            "coordinator_ref": started["coordinator_ref"],
            "intent": "question",
            "payload": display_payload,
        })
        self.assertEqual(replay, surfaced)

        answered = control.manage_orchestration({
            "task_ref": started["task_ref"],
            "coordinator_ref": started["coordinator_ref"],
            "intent": "question",
            "payload": {"question_ref": question_ref, "answer": {"option_ids": ["safe_mode"]}},
        })
        self.assertEqual(answered["outcome"], "question_answered")
        self.assertEqual(answered["resume"], {"kind": "poll", "question_ref": question_ref})
        polled = self._worker_question(state, attempt, {"action": "poll", "question_ref": question_ref})
        self.assertEqual(polled["outcome"], "question_answered")
        events = attempt_protocol.list_attempt_events(
            self.ledger, task_id=str(state["task_id"]), attempt_id=str(attempt["attempt_id"]),
        )
        event_types = [event["event_type"] for event in events]
        self.assertEqual(event_types.count("question_created"), 1)
        self.assertEqual(event_types.count("question_answered"), 1)
        self.assertEqual(event_types.count("decision_resolved"), 1)

    def test_runtime_has_no_nested_elicitation_adapter(self):
        self.assertFalse(hasattr(control, "_request_mcp_elicitation"))


if __name__ == "__main__":
    unittest.main()
