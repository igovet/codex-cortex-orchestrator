import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex

from cortex_runtime.validation import ValidationFailure, collect_validations


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

    def test_compact_resume_waves_aggregates_phase_profile_duplicate_and_dependency_errors(self):
        waves = [
            {"workers": [{"phase": "release"}]},
            {"workers": [{"phase": "implementation", "profile": "backend_dev"}]},
            {"workers": [{"phase": "implementation", "profile": "backend_dev"}]},
            {"workers": [{"phase": "governance_close", "profile": "devops_engineer", "depends_on": ["documentation"]}]},
        ]
        with self.assertRaises(ValidationFailure) as caught:
            cortex._v3_compact_waves(
                waves,
                {"user_request": "aggregate management errors", "complexity": "C3"},
                completed_gates={"scope", "plan", "discover", "architecture", "database_architecture", "implementation", "qa", "security", "performance", "accessibility", "ux", "review"},
            )
        diagnostics = caught.exception.diagnostics
        self.assertEqual(len(diagnostics), 4)
        self.assertEqual(
            [item["path"] for item in diagnostics],
            [
                "future_waves[0].workers[0].phase",
                "future_waves[2].workers[0].phase",
                "future_waves[3].workers[0].profile",
                "future_waves[3].workers[0].depends_on",
            ],
        )
        self.assertTrue(all(item.get("field_schema") for item in diagnostics))
        self.assertEqual(diagnostics[0]["received"], "release")
        self.assertIn("governance_close", diagnostics[2]["expected"])

    def test_compact_diagnostics_are_complete_field_errors_with_atomic_repairs(self):
        """Every independent wave error must be actionable without guessing.

        This is deliberately an API-contract test: a caller receives the bad
        path, submitted value, field schema and a same-request repair hint for
        every failure in one validation pass.  It must not receive the
        coordinator's internal routing lock.
        """
        waves = [
            {"workers": [{"phase": "release"}]},
            {"workers": [{"phase": "implementation", "profile": "not-a-profile"}]},
            {"workers": [{"phase": "implementation", "profile": "backend_dev", "depends_on": ["qa"]}]},
        ]
        with self.assertRaises(ValidationFailure) as caught:
            cortex._v3_compact_waves(
                waves,
                {"user_request": "aggregate management errors", "complexity": "C3"},
                completed_gates={"scope", "plan"},
            )

        diagnostics = caught.exception.diagnostics
        self.assertEqual(
            [item["path"] for item in diagnostics],
            [
                "future_waves[0].workers[0].phase",
                "future_waves[1].workers[0].profile",
                "future_waves[2].workers[0].phase",
                "future_waves[2].workers[0].depends_on",
            ],
        )
        for item in diagnostics:
            self.assertTrue(item["message"])
            self.assertTrue(item["expected"])
            self.assertIn("field_schema", item)
            self.assertEqual(item["fix"], "Correct this field in the same manage_orchestration request; do not resend unrelated fields.")

        next_action = cortex._validation_next_action(
            "manage_orchestration",
            diagnostics,
            task_ref="task-example",
            project_root="/tmp/project",
        )
        self.assertNotIn("COORDINATOR LOCK", next_action)
        self.assertIn("Correct every listed diagnostic", next_action)
        for item in diagnostics:
            self.assertIn(item["path"], next_action)
        self.assertIn("supported canonical phases", next_action)
        self.assertIn("project_root='/tmp/project'", next_action)

        contract = cortex._validation_contract(
            "manage_orchestration",
            diagnostics,
            task_ref="task-example",
            project_root="/tmp/project",
        )
        self.assertTrue(contract["diagnostics_are_complete"])
        self.assertTrue(contract["retry"]["preserve_valid_fields"])
        self.assertTrue(contract["repair"]["apply_all_diagnostics_atomically"])
        for item in diagnostics:
            self.assertIn(item["path"], contract["invalid_paths"])

    def test_management_validation_returns_schema_and_concrete_retry(self):
        diagnostics = [{
            "code": "management_failed",
            "phase": "payload",
            "path": "future_waves[0].workers[0].phase",
            "message": "unknown worker phase 'release'",
            "received": "release",
            "expected": "one of: qa, close",
            "field_schema": {"type": "string", "enum": ["qa", "close"]},
        }]
        response = cortex._v3_error(
            "management_failed",
            "management request validation failed",
            diagnostics=diagnostics,
        )
        response["next_action"] = cortex._validation_next_action(
            "manage_orchestration",
            diagnostics,
            task_ref="task-example",
            project_root="/tmp/project",
        )
        response["validation"] = cortex._validation_contract(
            "manage_orchestration",
            diagnostics,
            task_ref="task-example",
            project_root="/tmp/project",
        )
        self.assertNotIn("COORDINATOR LOCK", response["next_action"])
        self.assertIn("task-example", response["next_action"])
        self.assertIn("future_waves[0].workers[0].phase", response["next_action"])
        self.assertEqual(response["validation"]["schema"], "cortex/validation-error/v1")
        self.assertEqual(response["validation"]["request_schema"]["properties"]["payload.future_waves[].workers[].phase"]["enum"], [
            "accessibility", "architecture", "close", "database_architecture", "discover", "documentation",
            "governance_activation", "governance_close", "implementation", "performance", "plan", "qa",
            "review", "scope", "security", "ux",
        ])

    def test_public_management_form_advertises_nested_future_wave_fields(self):
        schema = cortex.PUBLIC_SCHEMA_REGISTRY["manage_orchestration"]
        payload = schema["properties"]["payload"]
        worker = payload["properties"]["future_waves"]["items"]["properties"]["workers"]["items"]
        self.assertIn("phase", worker["properties"])
        self.assertNotIn("release", worker["properties"]["phase"]["enum"])
        self.assertIn("planning", worker["properties"]["phase"]["enum"])
        self.assertIn("implementation", worker["properties"]["phase"]["enum"])
        self.assertIn("profile", worker["properties"])
        self.assertIn("depends_on", worker["properties"])
        self.assertTrue(set(worker["properties"]["depends_on"]["items"]["enum"]).issubset(
            set(worker["properties"]["phase"]["enum"])
        ))
        self.assertTrue(payload["additionalProperties"] is False)
        self.assertEqual(schema["required"], ["project_root", "intent"])
        self.assertIn("task_ref", schema["allOf"][0]["then"]["required"])


if __name__ == "__main__":
    unittest.main()
