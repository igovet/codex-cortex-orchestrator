"""Public worker-tool validation is path-aware and actionable."""
from __future__ import annotations

import sys
import json
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex  # noqa: F401  # bind the runtime before importing its facade
from cortex_runtime import attempt_facade
from cortex_runtime.worker_identity import worker_binding


class AttemptFacadeValidationContractTests(unittest.TestCase):
    _binding = {
        "project_root": "/tmp/project",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "profile": "backend_dev",
    }

    def test_complete_attempt_reports_all_unknown_fields_with_schema(self):
        with worker_binding(self._binding):
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
                with worker_binding(self._binding):
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

    def test_unbound_semantic_form_fails_closed_and_caller_identity_is_rejected(self):
        response = attempt_facade.record_attempt_event({
            "event_type": "progress", "payload": {"summary": "checkpoint"},
        })

        self.assertFalse(response["ok"])
        self.assertIn("server-bound worker session", response["diagnostics"][0]["message"])

        with worker_binding(self._binding):
            rejected = attempt_facade.record_attempt_event({
                "project_root": "/tmp/forged",
                "event_type": "progress",
                "payload": {"summary": "checkpoint"},
            })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["diagnostics"][0]["path"], "$")
        self.assertIn("project_root", rejected["diagnostics"][0]["message"])
        self.assertIn("server-owned", rejected["diagnostics"][0]["message"])

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

    def test_planning_receipt_is_bounded_and_projects_only_invalid_subschemas(self):
        response = attempt_facade._planning_repair_failure(
            {
                "schema": "cortex/orchestration/v5",
                "ok": False,
                "diagnostics": [
                    {"code": "planning_validation_failed", "path": "planning.overview", "message": "bad overview"},
                    {"code": "planning_validation_failed", "path": "planning.work_packages[0].microtasks[0].verification", "message": "bad verification"},
                ],
                "validation": {
                    "schema": "cortex/validation-error/v1",
                    "operation": "complete_attempt",
                    "diagnostics_are_complete": True,
                },
            },
            {"attempt_id": "plan-01", "base_payload_digest": "sha256:abc"},
        )

        diagnostics = response["diagnostics"]
        self.assertEqual(
            [item["json_pointer"] for item in diagnostics],
            ["/overview", "/work_packages/0/microtasks/0/verification"],
        )
        self.assertEqual(
            response["planning_repair"]["patch_paths"],
            ["/overview", "/work_packages/0/microtasks/0/verification"],
        )
        self.assertEqual(
            response["validation"]["invalid_json_pointers"],
            ["/overview", "/work_packages/0/microtasks/0/verification"],
        )
        self.assertEqual(response["base_payload_digest"], "sha256:abc")
        serialized = json.dumps(response, ensure_ascii=False)
        self.assertLess(len(serialized), 20_000)
        self.assertNotIn("bearer", serialized.lower())
        self.assertNotIn("coordinator_capability", serialized)
        self.assertEqual(
            set(diagnostics[0]["field_schema"]),
            {"type", "minLength"},
        )
        self.assertEqual(diagnostics[0]["field_schema"]["type"], "string")


if __name__ == "__main__":
    unittest.main()
