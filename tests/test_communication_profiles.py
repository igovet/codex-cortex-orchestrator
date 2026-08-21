import importlib.util
from pathlib import Path
import unittest


MODULE = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_runtime/communication.py"
SPEC = importlib.util.spec_from_file_location("cortex_communication", MODULE)
communication = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(communication)


class CommunicationProfileTests(unittest.TestCase):
    def test_default_and_invalid_configuration_fall_back_to_natural(self):
        self.assertEqual(communication.select_profile({}), "natural")
        self.assertEqual(communication.select_profile({"communication_profile": "unknown"}), "natural")
        self.assertEqual(communication.select_profile({}, env={communication.PROFILE_ENV: "technical"}), "technical")

    def test_aliases_and_human_message_types(self):
        self.assertEqual(communication.select_profile({"communication_profile": "detailed"}), "technical")
        self.assertEqual(communication.message_type("blocked"), "Action needed")
        self.assertEqual(communication.message_type("private_internal_value"), "Update")

    def test_internal_metadata_is_separate_from_user_message(self):
        separated = communication.separate_metadata({"message": "Done", "metadata": {"task_ref": "opaque"}})
        self.assertEqual(separated["message"]["message"], "Done")
        self.assertEqual(separated["metadata"]["task_ref"], "opaque")

    def test_quality_checks_cover_plain_language_repetition_next_step_and_profile(self):
        failures = communication.quality_checks(
            "The task_ref abc is complete. The task_ref abc is complete. The task_ref abc is complete. The task_ref abc is complete.",
            profile="compact", previous="The task_ref abc is complete. The task_ref abc is complete. The task_ref abc is complete. The task_ref abc is complete.", next_step=None,
        )
        self.assertIn("message exposes internal metadata", failures)
        self.assertIn("message repeats the previous update", failures)
        self.assertIn("message must include a next step", failures)
        self.assertIn("compact profile is too verbose", failures)

    def test_render_keeps_metadata_out_of_prose_and_reports_quality(self):
        result = communication.render("The work is complete.", kind="completed", next_step="Review the result.", metadata={"attempt_id": "a1"})
        self.assertEqual(result["message_type"], "Task completed")
        self.assertEqual(result["metadata"]["attempt_id"], "a1")
        self.assertTrue(result["quality"]["ok"])

    def test_lifecycle_projection_covers_all_required_user_update_types(self):
        # These are the public lifecycle meanings required by the UX contract;
        # transport-level outcome names remain confined to metadata/receipts.
        required = {
            "start": ("ready_to_spawn", "started"),
            "progress": ("waiting_workers", "progress"),
            "approval": ("awaiting_plan_approval", "question"),
            "question": ("needs_input", "question"),
            "problem": ("error", "error"),
            "blocker": ("blocked", "blocked"),
            "completion": ("completed", "completed"),
        }
        for label, (outcome, expected_kind) in required.items():
            with self.subTest(label=label):
                result = communication.render_lifecycle(
                    outcome,
                    ok=outcome not in {"problem"},
                    config={"communication_profile": "natural"},
                    metadata={"task_ref": "opaque", "outcome": outcome},
                )
                self.assertEqual(result["message_type"], communication.message_type(expected_kind))
                self.assertTrue(result["message"])
                self.assertTrue(result["next_step"])
                self.assertTrue(result["quality"]["ok"], result)
                self.assertNotRegex(result["message"], communication._INTERNAL_RE)


if __name__ == "__main__":
    unittest.main()
