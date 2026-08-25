"""Direct contract checks for the model-facing worker_question union."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))

import cortex
from cortex_runtime import mcp_api


TASK_REF = "task-" + "a" * 12
ASSIGNMENT_REF = "assignment-v1-" + "b" * 64
AUTHORITY = {"task_ref": TASK_REF, "assignment_ref": ASSIGNMENT_REF}


def choice_ask(*, question_type: str = "single_select") -> dict[str, object]:
    value: dict[str, object] = {
        **AUTHORITY,
        "action": "ask",
        "question_type": question_type,
        "decision_scope": "task_decision",
        "question": "Which storage strategy should be used?",
        "recommendation": "Keep the existing schema because it minimizes migration risk.",
        "options": [
            {"option_id": "existing", "label_en": "Keep the existing schema"},
            {"option_id": "new", "label_en": "Create a new schema"},
        ],
        "recommended_option_ids": ["existing"],
    }
    return value


def text_ask() -> dict[str, object]:
    return {
        **AUTHORITY,
        "action": "ask",
        "question_type": "text",
        "decision_scope": "acceptance_criteria",
        "question": "What migration guarantee is required?",
        "recommendation": "Use an explicit compatibility guarantee.",
        "recommended_answer": "Preserve all existing public API behavior.",
    }


def ask_batch() -> dict[str, object]:
    return {
        **AUTHORITY,
        "action": "ask_batch",
        "batch": {
            "batch_key": "migration",
            "questions": [
                {
                    "question_key": "strategy",
                    "question": "Which migration strategy should be used?",
                    "question_type": "single_select",
                    "decision_scope": "task_decision",
                    "recommendation": "Use the compatible strategy.",
                    "options": [
                        {"option_id": "compatible", "label_en": "Compatible migration"},
                        {"option_id": "breaking", "label_en": "Breaking migration"},
                    ],
                    "recommended_option_ids": ["compatible"],
                },
                {
                    "question_key": "guarantee",
                    "question": "What guarantee is mandatory?",
                    "question_type": "text",
                    "decision_scope": "acceptance",
                    "recommendation": "State compatibility explicitly.",
                    "recommended_answer": "Preserve existing public behavior.",
                },
            ],
        },
    }


class WorkerQuestionPublicContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = cortex.PUBLIC_SCHEMA_REGISTRY["worker_question"]
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema)

    def assertSchemaValid(self, value: dict[str, object]) -> None:
        errors = sorted(self.validator.iter_errors(value), key=lambda item: list(item.path))
        self.assertEqual(errors, [], [item.message for item in errors])

    def assertSchemaInvalid(self, value: dict[str, object]) -> None:
        self.assertTrue(list(self.validator.iter_errors(value)), value)

    def test_every_closed_branch_has_a_direct_valid_instance(self) -> None:
        valid = {
            "single_choice_ask": choice_ask(),
            "multi_choice_ask": {**choice_ask(question_type="multi_select"), "recommended_option_ids": ["existing", "new"]},
            "text_ask": text_ask(),
            "poll": {**AUTHORITY, "action": "poll", "question_ref": "question-0001"},
            "ask_batch": ask_batch(),
            "poll_batch": {**AUTHORITY, "action": "poll_batch", "batch_ref": "batch-" + "c" * 24},
        }
        for name, value in valid.items():
            with self.subTest(name=name):
                self.assertSchemaValid(value)

    def test_cross_branch_and_ambiguous_answer_forms_are_schema_invalid(self) -> None:
        invalid = {
            "missing_authority": {"action": "poll", "question_ref": "question-0001"},
            "poll_with_ask_field": {**AUTHORITY, "action": "poll", "question_ref": "question-0001", "question": "extra"},
            "poll_object_ref": {**AUTHORITY, "action": "poll", "question_ref": {"value": "question-0001"}},
            "poll_batch_with_batch": {**AUTHORITY, "action": "poll_batch", "batch_ref": "batch-" + "c" * 24, "batch": {}},
            "ask_with_poll_ref": {**text_ask(), "question_ref": "question-0001"},
            "ask_without_question_type": {key: value for key, value in text_ask().items() if key != "question_type"},
            "ask_without_decision_scope": {key: value for key, value in text_ask().items() if key != "decision_scope"},
            "legacy_context": {**text_ask(), "context": {"decision_scope": "acceptance"}},
            "legacy_answer_mode": {**text_ask(), "answer_mode": "text"},
            "legacy_multiple": {**choice_ask(), "multiple": False},
            "ask_without_recommendation": {key: value for key, value in text_ask().items() if key != "recommendation"},
            "ask_without_answer_mode": {key: value for key, value in text_ask().items() if key != "recommended_answer"},
            "choice_plus_text": {**choice_ask(), "recommended_answer": "Also text"},
            "text_plus_empty_options": {**text_ask(), "options": []},
            "choice_without_ids": {key: value for key, value in choice_ask().items() if key != "recommended_option_ids"},
            "single_with_multiple_ids": {**choice_ask(), "recommended_option_ids": ["existing", "new"]},
            "multi_without_ids": {key: value for key, value in choice_ask(question_type="multi_select").items() if key != "recommended_option_ids"},
            "choice_string_option": {**choice_ask(), "options": ["unstable option"]},
            "ask_batch_with_single_field": {**ask_batch(), "question": "extra"},
            "batch_text_with_choice_field": {
                **ask_batch(),
                "batch": {
                    **ask_batch()["batch"],
                    "questions": [
                        {**ask_batch()["batch"]["questions"][1], "options": []},
                    ],
                },
            },
            "batch_legacy_type": {
                **ask_batch(),
                "batch": {
                    **ask_batch()["batch"],
                    "questions": [
                        {
                            **{key: value for key, value in ask_batch()["batch"]["questions"][0].items() if key != "question_type"},
                            "type": "single_select",
                        },
                    ],
                },
            },
        }
        for name, value in invalid.items():
            with self.subTest(name=name):
                self.assertSchemaInvalid(value)

    def test_runtime_returns_exact_structured_corrections_for_cross_branch_fields(self) -> None:
        invalid = {
            "/question": {**AUTHORITY, "action": "poll", "question_ref": "question-0001", "question": "extra"},
            "/question_ref": {**text_ask(), "question_ref": "question-0001"},
            "/recommended_answer": {**choice_ask(), "recommended_answer": "mixed mode"},
            "/batch": {**AUTHORITY, "action": "poll_batch", "batch_ref": "batch-" + "c" * 24, "batch": {}},
        }
        for pointer, value in invalid.items():
            with self.subTest(pointer=pointer):
                response = cortex.worker_question(value)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "worker_question_request_invalid")
                self.assertIn(pointer, [item["json_pointer"] for item in response["error"]["diagnostics"]])
                self.assertEqual(response["recovery"]["kind"], "same_operation")
                self.assertFalse(response["recovery"]["state_mutated"])

    def test_model_prompt_card_and_tool_description_name_the_same_closed_union(self) -> None:
        profiles = json.loads((ROOT / "plugins/cortex/profiles.json").read_text(encoding="utf-8"))
        card = profiles["shared_worker_contract"]["operation_cards"]["worker_question"]
        for literal in ("ask single_select=", "ask multi_select=", "ask text=", "poll=", "ask_batch=", "poll_batch="):
            self.assertIn(literal, card["input"])
        self.assertIn("question_type", card["purpose"])
        self.assertIn("decision_scope", card["purpose"])
        description = mcp_api.PUBLIC_TOOL_DESCRIPTIONS["worker_question"]
        self.assertIn("closed action union", description.lower())
        self.assertIn("question_type=single_select", description)
        self.assertIn("Never send answer_mode", description)
        self.assertNotIn("project_root", card["input"])
        self.assertNotIn("profile", card["input"])


if __name__ == "__main__":
    unittest.main()
