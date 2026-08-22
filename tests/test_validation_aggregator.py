import unittest

from plugins.cortex.scripts.cortex_runtime.validation import ValidationFailure, collect_validations


class ValidationAggregatorTests(unittest.TestCase):
    def test_collects_independent_failures_in_declared_path_order(self):
        with self.assertRaises(ValidationFailure) as caught:
            collect_validations(
                (
                    ("task_id", lambda: "task_id is required"),
                    ("attempt_id", lambda: "attempt_id is required"),
                    ("profile", lambda: None),
                ),
                code="request_invalid",
            )
        self.assertEqual([item["path"] for item in caught.exception.diagnostics], ["task_id", "attempt_id"])

    def test_success_does_not_run_or_write_anything(self):
        calls = []
        collect_validations((("field", lambda: calls.append("checked") or None),), code="request_invalid")
        self.assertEqual(calls, ["checked"])


if __name__ == "__main__":
    unittest.main()
