"""Public worker-tool validation is path-aware and actionable."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex  # noqa: F401  # bind the runtime before importing its facade
from cortex_runtime import attempt_facade


class AttemptFacadeValidationContractTests(unittest.TestCase):
    def test_complete_attempt_reports_all_unknown_fields_with_schema(self):
        response = attempt_facade.complete_attempt({"bogus_a": 1, "bogus_b": "x"})

        self.assertFalse(response["ok"])
        self.assertEqual([item["path"] for item in response["diagnostics"]], ["$.bogus_a", "$.bogus_b"])
        self.assertTrue(all(item["received"] is not None for item in response["diagnostics"]))
        self.assertTrue(all(item["field_schema"]["type"] == "object" for item in response["diagnostics"]))
        self.assertIn("complete_attempt", response["next_action"])
        self.assertNotIn("COORDINATOR LOCK", response["next_action"])
        self.assertEqual(response["validation"]["schema"], "cortex/validation-error/v1")

    def test_each_worker_tool_exposes_its_documented_properties(self):
        calls = (
            (attempt_facade.record_attempt_event, "record_attempt_event", "wrong"),
            (attempt_facade.repair_planning, "repair_planning", "wrong"),
            (attempt_facade.read_worker_result, "read_worker_result", "wrong"),
        )
        for tool, operation, field in calls:
            with self.subTest(operation=operation):
                response = tool({field: True})
                self.assertFalse(response["ok"])
                diagnostic = response["diagnostics"][0]
                self.assertEqual(diagnostic["path"], f"$.{field}")
                self.assertEqual(diagnostic["received"], True)
                self.assertIn("properties", diagnostic["field_schema"])
                self.assertIn(operation, response["next_action"])
                self.assertNotIn("COORDINATOR LOCK", response["next_action"])

    def test_complete_attempt_exposes_full_nested_planning_schema(self):
        schema = attempt_facade._facade_schema("complete_attempt")
        planning = schema["properties"]["planning"]
        self.assertEqual(
            set(planning["properties"]),
            {
                "overview", "requirement_coverage", "recommendation",
                "recommendation_rationale", "recommendation_actions", "resolved_questions", "risks",
                "work_packages",
            },
        )
        self.assertEqual(
            set(planning["properties"]["work_packages"]["items"]["properties"]),
            {"id", "title", "objective", "allowed_paths", "depends_on", "gates", "microtasks", "order", "status", "required_artifacts"},
        )
        self.assertIn(
            "profile",
            planning["properties"]["work_packages"]["items"]["properties"]["microtasks"]["items"]["properties"],
        )

    def test_missing_identity_fields_are_returned_in_one_validation_response(self):
        response = attempt_facade.record_attempt_event({})

        self.assertFalse(response["ok"])
        paths = [item["path"] for item in response["diagnostics"]]
        self.assertEqual(paths, ["$.project_root", "$.task_id", "$.attempt_id", "$.profile", "$.event_type", "$.payload"])
        self.assertTrue(response["validation"]["diagnostics_are_complete"])
        self.assertIn("Correct every listed diagnostic", response["next_action"])

    def test_planning_failure_requires_same_attempt_patch_and_forbids_coordinator_repair(self):
        response = attempt_facade._planning_repair_failure(
            {"schema": "cortex/orchestration/v5", "ok": False, "diagnostics": [{"path": "planning.future_waves"}]},
            {"attempt_id": "plan-01", "base_payload_digest": "sha256:abc"},
        )

        self.assertEqual(response["planning_repair"]["mode"], "same_attempt_patch")
        self.assertEqual(response["planning_repair"]["patch_paths"], ["/future_waves"])
        self.assertIn("regenerate or resend the full planning object", response["planning_repair"]["coordinator_must_not"])
        self.assertIn("repair_planning", response["next_action"])
        self.assertIn("same planner attempt", response["next_action"])
        self.assertIn("replacement worker", response["next_action"])

    def test_full_planning_retry_replays_original_diagnostics_and_patch_paths(self):
        response = attempt_facade._planning_repair_failure(
            {
                "schema": "cortex/orchestration/v5",
                "ok": False,
                "diagnostics": [{
                    "code": "complete_attempt_invalid",
                    "path": "$.planning",
                    "message": "planner rejected draft requires PATCH-only repair",
                }],
            },
            {
                "attempt_id": "plan-01",
                "base_payload_digest": "sha256:abc",
                "diagnostics": [{
                    "code": "planning_coverage_invalid",
                    "path": "planning.requirement_coverage[4].plan_refs",
                    "message": "unknown plan item",
                }],
            },
        )

        self.assertEqual(
            [item["path"] for item in response["diagnostics"]],
            ["planning.requirement_coverage[4].plan_refs"],
        )
        self.assertEqual(response["planning_repair"]["patch_paths"], ["/requirement_coverage/4/plan_refs"])
        self.assertEqual(response["base_payload_digest"], "sha256:abc")
        self.assertIn("/requirement_coverage/4/plan_refs", response["next_action"])


if __name__ == "__main__":
    unittest.main()
