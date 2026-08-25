"""Focused contract tests for the user-decision Question Firewall."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex  # noqa: F401  # initialize runtime bindings before facade imports
from cortex_runtime import questions


class QuestionFirewallTests(unittest.TestCase):
    def test_internal_cortex_condition_routes_to_coordinator_advice(self):
        decision = questions._question_firewall_scope({
            "question": "Which Cortex gate should be retried after the receipt failed?",
            "context": {"decision_scope": "recovery"},
        })
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["category"], "internal_cortex")

        response = questions.cortex_question({
            "task_id": "task-not-needed-for-firewall",
            "principal": "thread-a",
            "question": "Which Cortex gate should be retried after the receipt failed?",
            "context": {"decision_scope": "recovery"},
        })
        self.assertTrue(response["ok"])
        self.assertEqual(response["outcome"], "orchestrator_advice")
        self.assertFalse(response["requires_user_decision"])
        self.assertEqual(response["reason_category"], "internal_cortex")
        self.assertNotIn("chat_interaction", response)
        self.assertNotIn("question_firewall", response)
        self.assertNotIn("next_action", response)

    def test_task_requirement_question_is_allowed(self):
        decision = questions._question_firewall_scope({
            "question": "Which user-visible behavior should the feature preserve?",
            "context": {"decision_scope": "acceptance"},
        })
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["category"], "task_decision")

    def test_canonical_scope_allows_product_question(self):
        decision = questions._question_firewall_scope({
            "question": "Which retry behavior should the product expose to customers?",
            "context": {"decision_scope": "acceptance"},
        })
        self.assertTrue(decision["allowed"])

    def test_missing_decision_scope_is_not_inferred_from_text(self):
        decision = questions._question_firewall_scope({
            "question": "Which retry behavior should the product expose to customers?",
            "context": {},
        })
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["internal"][0]["scope"], "missing_decision_scope")

    def test_unknown_decision_scope_is_rejected_fail_closed(self):
        decision = questions._question_firewall_scope({
            "question": "Which release mode should be used?",
            "context": {"decision_scope": "invented_scope"},
        })
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["internal"][0]["scope"], "unknown_decision_scope")

    def test_internal_batch_does_not_become_a_user_question(self):
        decision = questions._question_firewall_scope({
            "action": "ask_batch",
            "batch": {
                "questions": [
                    {
                        "question": "Which worker profile should own this retry?",
                        "context": {"decision_scope": "worker"},
                    },
                    {
                        "question": "Which rollout behavior should customers see?",
                        "context": {"decision_scope": "acceptance"},
                    },
                ],
            },
        })
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["internal"][0]["path"], "batch.questions[0]")


if __name__ == "__main__":
    unittest.main()
