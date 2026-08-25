from __future__ import annotations

import sys
import inspect
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex
from cortex_runtime import governance


class GovernanceValidationContractTests(unittest.TestCase):
    TASK_REF = "task-aaaaaaaaaaaa"
    COORDINATOR_REF = "b" * 64

    def test_public_schema_is_closed_canonical_action_union(self) -> None:
        schema = cortex.MANAGE_GOVERNANCE_SCHEMA
        actions = {
            branch["properties"]["action"]["const"]
            for branch in schema["oneOf"]
        }
        self.assertEqual(actions, {
            "inspect_initiative", "link_task", "add_dependency", "transition_initiative",
            "create_record", "list_records", "snapshot", "evaluate_promotion", "promotion_inspect",
        })
        retired_aliases = {
            "inspect", "link", "dependency", "transition", "record_create",
            "history", "snapshot_inspect", "promotion_evaluate",
        }
        self.assertTrue(actions.isdisjoint(retired_aliases))
        self.assertEqual(actions, set(cortex.TASK_COORDINATOR_CAPABILITY_ACTIONS))
        for branch in schema["oneOf"]:
            self.assertTrue(branch["additionalProperties"] is False)
            self.assertTrue({"action", "task_ref", "coordinator_ref"}.issubset(branch["required"]))
            self.assertNotIn("project_root", branch["properties"])
            self.assertNotIn("created_by", branch["properties"])
            self.assertNotIn("capability_generation", branch["properties"])

    def test_governance_schema_is_the_tools_list_runtime_authority(self) -> None:
        schema = cortex.PUBLIC_SCHEMA_REGISTRY["manage_governance"]
        self.assertIs(cortex.MANAGE_GOVERNANCE_SCHEMA, schema)
        self.assertIs(cortex.PUBLIC_TOOLS["manage_governance"][1], schema)
        self.assertIn('governance_schema.get("oneOf", [])', inspect.getsource(cortex._manage_governance_impl))

    def test_governance_discriminated_union_valid_matrix(self) -> None:
        envelope = {"task_ref": self.TASK_REF, "coordinator_ref": self.COORDINATOR_REF}
        valid = [
            {**envelope, "action": "inspect_initiative", "initiative_ref": "initiative-alpha"},
            {**envelope, "action": "link_task", "initiative_ref": "initiative-alpha", "task_id": "task-a"},
            {**envelope, "action": "add_dependency", "initiative_ref": "initiative-alpha", "source_type": "initiative", "source_ref": "initiative-alpha", "target_type": "task", "target_ref": "task-a", "dependency_type": "blocks"},
            {**envelope, "action": "transition_initiative", "initiative_ref": "initiative-alpha", "status": "active"},
            {**envelope, "action": "create_record", "task_id": "task-a", "record_type": "decision", "content": {"choice": "safe"}},
            {**envelope, "action": "create_record", "initiative_ref": "initiative-alpha", "record_type": "risk", "content": {"risk": "drift"}},
            {**envelope, "action": "create_record", "initiative_ref": "initiative-alpha", "task_id": "task-a", "record_type": "learning", "content": "Keep the strict boundary."},
            {**envelope, "action": "list_records", "task_id": "task-a"},
            {**envelope, "action": "list_records", "initiative_ref": "initiative-alpha"},
            {**envelope, "action": "snapshot", "task_id": "task-a", "limit": 32},
            {**envelope, "action": "snapshot", "initiative_ref": "initiative-alpha", "offset": 0},
            {**envelope, "action": "evaluate_promotion", "initiative_ref": "initiative-alpha", "fingerprint": "finding-a"},
            {**envelope, "action": "promotion_inspect", "initiative_ref": "initiative-alpha"},
        ]
        for request in valid:
            with self.subTest(action=request["action"], request=request):
                self.assertEqual(cortex._manage_governance_input_diagnostics(request), [])

    def test_governance_rejects_aliases_and_cross_branch_contamination(self) -> None:
        envelope = {"task_ref": self.TASK_REF, "coordinator_ref": self.COORDINATOR_REF}
        for alias in (
            "inspect", "link", "dependency", "transition", "record_create",
            "history", "snapshot_inspect", "promotion_evaluate",
        ):
            with self.subTest(alias=alias):
                diagnostics = cortex._manage_governance_input_diagnostics({**envelope, "action": alias})
                action_diagnostic = next(item for item in diagnostics if item["json_pointer"] == "/action")
                self.assertNotIn(alias, action_diagnostic["field_schema"]["enum"])

        contaminated = cortex._manage_governance_input_diagnostics({
            **envelope,
            "action": "inspect_initiative",
            "initiative_ref": "initiative-alpha",
            "record_type": "decision",
        })
        self.assertIn("/record_type", {item["json_pointer"] for item in contaminated})

    def test_governance_dispatcher_has_no_compatibility_alias_route(self) -> None:
        for alias in (
            "inspect", "link", "dependency", "transition", "record_create",
            "history", "snapshot_inspect", "promotion_evaluate",
        ):
            with self.subTest(alias=alias), self.assertRaisesRegex(
                governance.GovernanceError, "action is not recognized",
            ):
                governance.manage_governance(Path("/tmp/cortex-governance-alias-rejected"), {"action": alias})

    def test_governance_capability_safety_rejects_ambient_or_server_owned_fields(self) -> None:
        diagnostics = cortex._manage_governance_input_diagnostics({
            "action": "create_record",
            "task_ref": self.TASK_REF,
            "project_root": "/guessed/project",
            "task_id": "task-a",
            "record_type": "policy",
            "content": {},
            "created_by": "guessed-principal",
            "capability_generation": 9,
        })
        pointers = {item["json_pointer"] for item in diagnostics}
        self.assertTrue({
            "/coordinator_ref", "/project_root", "/created_by",
            "/capability_generation", "/record_type",
        }.issubset(pointers))

    def test_governance_validation_receipt_embeds_the_same_public_schema(self) -> None:
        response = cortex._manage_governance_validation_error({
            "action": "inspect_initiative", "task_ref": self.TASK_REF,
        })
        self.assertEqual(
            response["validation"]["request_schema"],
            {"tool": "manage_governance", **cortex.MANAGE_GOVERNANCE_SCHEMA},
        )

    def test_invalid_governance_form_aggregates_all_fields_without_mutation(self) -> None:
        response = cortex.manage_governance({
            "project_root": "",
            "action": "not_a_governance_action",
            "unexpected": True,
        })
        self.assertFalse(response["ok"])
        self.assertEqual(response["schema"], "cortex/governance-response/v11")
        self.assertEqual(response["outcome"], "failed")
        correction = {**response["error"], **response["recovery"]}
        # Without the previously issued task/coordinator capability pair,
        # there is no deterministic legal retry. The public terminal card
        # must not suggest an inspect/retry route or expose server state.
        self.assertEqual(correction["kind"], "terminal_stop")
        self.assertFalse(correction["retryable"])
        self.assertFalse(correction["state_mutated"])
        self.assertNotIn("allowed_changes", correction)
        paths = {item["json_pointer"] for item in correction["diagnostics"]}
        self.assertEqual(paths, {"/project_root", "/action", "/unexpected", "/task_ref", "/coordinator_ref"})
        for item in correction["diagnostics"]:
            self.assertIn("json_pointer", item)
            self.assertIn("field_schema", item)

    def test_action_specific_missing_fields_are_returned_together(self) -> None:
        response = cortex.manage_governance({
            "task_ref": "task-aaaaaaaaaaaa",
            "coordinator_ref": "b" * 64,
            "action": "add_dependency",
        })
        self.assertFalse(response["ok"])
        self.assertFalse(response["recovery"]["state_mutated"])
        paths = [item["json_pointer"] for item in response["error"]["diagnostics"]]
        self.assertEqual(paths, ["/initiative_ref", "/source_type", "/source_ref", "/target_type", "/target_ref", "/dependency_type"])
        self.assertEqual(len(paths), len(set(paths)))


if __name__ == "__main__":
    unittest.main()
