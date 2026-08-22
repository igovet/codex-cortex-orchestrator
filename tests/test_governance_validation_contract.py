from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex


class GovernanceValidationContractTests(unittest.TestCase):
    def test_public_schema_exposes_action_enum_and_conditional_fields(self) -> None:
        schema = cortex.MANAGE_GOVERNANCE_SCHEMA
        action = schema["properties"]["action"]
        self.assertIn("create_record", action["enum"])
        self.assertIn("acknowledge_coordinator_recovery", action["enum"])
        self.assertTrue(schema["additionalProperties"] is False)
        self.assertTrue(any("required" in item.get("then", {}) for item in schema["allOf"]))

    def test_invalid_governance_form_aggregates_all_fields_without_mutation(self) -> None:
        response = cortex.manage_governance({
            "project_root": "",
            "action": "not_a_governance_action",
            "unexpected": True,
        })
        self.assertFalse(response["ok"])
        self.assertEqual(response["schema"], "cortex/validation-error/v1")
        self.assertEqual(response["outcome"], "needs_correction")
        paths = {item["path"] for item in response["diagnostics"]}
        self.assertEqual(paths, {"project_root", "action", "unexpected"})
        for item in response["diagnostics"]:
            self.assertIn("json_pointer", item)
            self.assertIn("received", item)
            self.assertIn("expected", item)
            self.assertIn("field_schema", item)
            self.assertIn("fix", item)
        self.assertNotIn("COORDINATOR LOCK", response["next_action"])
        self.assertIn("manage_governance", response["next_action"])
        self.assertTrue(response["validation"]["diagnostics_are_complete"])
        self.assertTrue(response["validation"]["apply_all_diagnostics_atomically"])

    def test_action_specific_missing_fields_are_returned_together(self) -> None:
        response = cortex.manage_governance({
            "project_root": "/tmp/cortex-validation-fixture",
            "action": "add_dependency",
        })
        self.assertFalse(response["ok"])
        paths = [item["path"] for item in response["diagnostics"]]
        self.assertEqual(paths, ["source_type", "source_ref", "target_type", "target_ref", "dependency_type"])
        self.assertEqual(len(paths), len(set(paths)))


if __name__ == "__main__":
    unittest.main()
