import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex  # noqa: F401  # initialize runtime bindings before facade imports
from cortex_runtime.dispatch_briefing import read_dispatch_briefing
from cortex_runtime.questions import worker_question


class WorkerBoundaryValidationContractTests(unittest.TestCase):
    def test_worker_question_aggregates_form_errors_before_state_lookup(self):
        response = worker_question({"action": "invalid", "profile": "nope", "question": ""})
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "worker_question_request_invalid")
        paths = {item["path"] for item in response["diagnostics"]}
        self.assertTrue({"$.project_root", "$.task_id", "$.attempt_id", "$.action", "$.profile"}.issubset(paths))
        self.assertTrue(all(item.get("field_schema") for item in response["diagnostics"]))
        self.assertTrue(all(item.get("json_pointer") == item.get("path") for item in response["diagnostics"]))
        self.assertNotIn("COORDINATOR LOCK", response["next_action"])
        self.assertIn("worker_question", response["next_action"])
        self.assertFalse(response["validation"]["retry"]["replacement_worker_authorized"])

    def test_dispatch_briefing_reports_all_missing_identity_fields(self):
        response = read_dispatch_briefing({"max_bytes": 0})
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "dispatch_briefing_request_invalid")
        paths = {item["path"] for item in response["diagnostics"]}
        self.assertTrue({"project_root", "task_id", "attempt_id", "profile", "dispatch_ref", "briefing_digest"}.issubset(paths))
        self.assertTrue(all(item.get("field_schema") for item in response["diagnostics"]))
        self.assertTrue(all(item.get("json_pointer") == item.get("path") for item in response["diagnostics"]))
        self.assertNotIn("COORDINATOR LOCK", response["next_action"])
        self.assertIn("read_dispatch_briefing", response["next_action"])


if __name__ == "__main__":
    unittest.main()
