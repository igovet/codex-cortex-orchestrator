from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.cortex_test_support import HostPrivateControlStoreTestMixin

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex


class CoordinatorFormContractTests(unittest.TestCase):
    def test_task_scoped_forms_derive_project_root_after_task_ref(self) -> None:
        continue_schema = cortex.PUBLIC_SCHEMA_REGISTRY["continue_orchestration"]
        self.assertEqual(continue_schema["required"], ["task_ref", "step", "results"])
        self.assertNotIn("project_root", continue_schema["properties"])

        management_schema = cortex.PUBLIC_SCHEMA_REGISTRY["manage_orchestration"]
        self.assertEqual(management_schema["required"], ["intent"])
        self.assertEqual(
            cortex.PUBLIC_SCHEMA_REGISTRY["read_worker_result"]["required"],
            ["task_ref", "attempt_result_ref"],
        )
        self.assertNotIn("project_root", cortex.PUBLIC_SCHEMA_REGISTRY["read_worker_result"]["properties"])
        self.assertIn("task_ref", management_schema["allOf"][0]["then"]["required"])
        self.assertTrue(any("project_root" in item.get("then", {}).get("required", []) for item in management_schema["allOf"]))
        recover = management_schema["allOf"][-1]
        self.assertEqual(recover["if"]["properties"]["intent"]["const"], "recover_blocked")
        self.assertEqual(recover["then"]["not"]["required"], ["payload"])

    def test_continue_accepts_bound_task_without_repeated_project_root(self) -> None:
        bound_root = Path("/tmp/bound-cortex-project")
        with (
            mock.patch.object(cortex, "_bound_project_root_for_task_ref", return_value=bound_root),
            mock.patch.object(cortex, "_v3_completed_replay", return_value={"ok": True, "outcome": "replayed"}),
        ):
            response = cortex.continue_orchestration({
                "task_ref": "task-bound",
                "step": 1,
                "results": [{"attempt_result_ref": "attempt-result"}],
            })
        self.assertEqual(response["outcome"], "replayed")

    def test_governance_form_accepts_task_ref_as_root_binding(self) -> None:
        diagnostics = cortex._manage_governance_input_diagnostics({
            "task_ref": "task-bound",
            "action": "inspect",
        })
        self.assertNotIn("project_root", {item["path"] for item in diagnostics})

    def test_management_validation_contract_describes_server_bound_root(self) -> None:
        contract = cortex._validation_contract(
            "manage_orchestration",
            [{"path": "payload", "message": "invalid"}],
            task_ref="task-bound",
        )
        conditional = contract["request_schema"]["conditional_requirements"]
        self.assertIn("Cortex derives", conditional["task_scoped_intents"]["description"])
        self.assertEqual(conditional["project_scoped_intents"]["then"]["required"], ["project_root"])

    def test_recover_blocked_rejects_caller_recovery_payload(self) -> None:
        response = cortex.manage_orchestration({
            "intent": "recover_blocked",
            "task_ref": "task-bound",
            "payload": {"future_waves": []},
        })
        self.assertFalse(response["ok"])
        self.assertEqual(response["outcome"], "needs_correction")
        self.assertEqual(response["diagnostics"][0]["path"], "payload")
        self.assertIn("field_schema", response["diagnostics"][0])

    def test_management_alias_is_a_structured_correction(self) -> None:
        response = cortex.manage_orchestration({"intent": "recover-terminal", "task_ref": "task-bound"})
        self.assertFalse(response["ok"])
        self.assertEqual(response["outcome"], "needs_correction")
        self.assertEqual(response["diagnostics"][0]["path"], "intent")

    def test_management_missing_intent_is_not_defaulted_to_inspect(self) -> None:
        response = cortex.manage_orchestration({"task_ref": "task-bound"})
        self.assertFalse(response["ok"])
        self.assertEqual(response["outcome"], "needs_correction")
        self.assertEqual(response["diagnostics"][0]["path"], "intent")

    def test_continue_shape_errors_are_aggregated(self) -> None:
        response = cortex.continue_orchestration({
            "task_ref": "task-bound",
            "step": "one",
            "results": [
                {"status": "success", "worker": 0},
                {"unsupported": True},
            ],
            "rework": "yes",
        })
        self.assertFalse(response["ok"])
        self.assertEqual(response["outcome"], "needs_correction")
        paths = {item["path"] for item in response["diagnostics"]}
        self.assertTrue({"step", "results[0].status", "results[0].worker", "results[1].unsupported", "rework"}.issubset(paths))


class CoordinatorRootBindingTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.set_up_host_private_control_store()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        cortex.activate_orchestration({
            "user_command": "/cortex",
            "principal": "coordinator-form-test",
            "thread_id": "coordinator-form-test",
            "project_root": str(self.project),
        })

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()
        self.temp.cleanup()

    def test_task_ref_resolves_to_the_immutable_project_root(self) -> None:
        started = cortex.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Exercise coordinator task binding.",
                "acceptance_criteria": ["The task is bound."],
                "verification": ["Inspect the durable task binding."],
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        })
        self.assertTrue(started["ok"])
        self.assertEqual(
            cortex._bound_project_root_for_task_ref(started["task_ref"]),
            self.project.resolve(),
        )


if __name__ == "__main__":
    unittest.main()
