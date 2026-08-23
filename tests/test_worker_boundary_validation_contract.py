import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex  # noqa: F401  # initialize runtime bindings before facade imports
from cortex_runtime.dispatch_briefing import read_dispatch_briefing
from cortex_runtime.questions import worker_question


class WorkerBoundaryValidationContractTests(unittest.TestCase):
    def test_unbound_worker_question_fails_closed_without_model_identity_fields(self):
        response = worker_question({"action": "invalid", "profile": "nope", "question": ""})
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "worker_question_unavailable")
        paths = {item["path"] for item in response["diagnostics"]}
        self.assertEqual(paths, {"$"})
        self.assertTrue(all(item.get("field_schema") for item in response["diagnostics"]))
        self.assertNotIn("project_root", response["next_action"])
        self.assertNotIn("attempt_id", response["next_action"])
        self.assertIn("worker-session recovery", response["next_action"])
        self.assertFalse(response["worker_replacement_authorized"])

    def test_unbound_dispatch_briefing_fails_closed_without_model_identity_fields(self):
        response = read_dispatch_briefing({"max_bytes": 0})
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "dispatch_briefing_unavailable")
        paths = {item["path"] for item in response["diagnostics"]}
        self.assertEqual(paths, {"$"})
        self.assertNotIn("project_root", response["next_action"])
        self.assertNotIn("attempt_id", response["next_action"])
        self.assertIn("server-owned recovery", response["next_action"])


if __name__ == "__main__":
    unittest.main()
