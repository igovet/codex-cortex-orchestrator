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

    def test_noncanonical_profiles_fall_back_and_human_message_types(self):
        self.assertEqual(communication.select_profile({"communication_profile": "unsupported_profile"}), "natural")
        self.assertEqual(communication.select_profile({"communication_profile": "other_profile"}), "natural")
        self.assertEqual(communication.select_profile({"profile": "technical"}), "natural")
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

    def test_render_keeps_metadata_out_of_prose_and_preserves_quality(self):
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
            # Technical lifecycle labels are presented as silent progress
            # while recovery continues. Only an explicit task question or
            # user-requested plan approval opts into a decision view.
            "approval": ("awaiting_plan_approval", "progress"),
            "question": ("needs_input", "progress"),
            "problem": ("error", "progress"),
            "blocker": ("blocked", "progress"),
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

    def test_blocked_internal_state_is_presented_as_recovery_not_a_user_block(self):
        for language in ("en", "ru"):
            with self.subTest(language=language):
                result = communication.render_lifecycle(
                    "blocked", config={"communication_profile": "natural", "user_language": language}
                )
                visible = f"{result['message']} {result['next_step']}".lower()
                self.assertEqual(result["message_type"], communication.message_type("progress"))
                self.assertNotIn("blocked", visible)
                self.assertNotIn("blocker", visible)
                self.assertTrue(result["quality"]["ok"], result)

    def test_profiles_localize_and_have_distinct_detail_levels(self):
        natural = communication.render_lifecycle(
            "completed", config={"communication_profile": "natural", "user_language": "ru"}
        )
        compact = communication.render_lifecycle(
            "completed", config={"communication_profile": "compact", "user_language": "en"}
        )
        technical = communication.render_lifecycle(
            "completed", config={"communication_profile": "technical", "user_language": "en"}
        )
        self.assertIn("Задача завершена", natural["message"])
        self.assertEqual({natural["detail_level"], compact["detail_level"], technical["detail_level"]},
                         {"plain", "minimal", "diagnostic"})
        self.assertIn("technical_context", technical)

    def test_natural_and_compact_hide_internal_values_and_waiting_is_silent(self):
        for profile in ("natural", "compact"):
            rendered = communication.render(
                "Task abc1234567890abcdef is complete.",
                config={"communication_profile": profile},
                next_step="Review the result.",
            )
            self.assertNotIn("abc1234567890abcdef", rendered["message"])
        waiting = communication.render_lifecycle("waiting_workers")
        self.assertEqual(waiting["output_policy"], "silent")

    def test_technical_profile_keeps_diagnostics_but_redacts_values_and_paths(self):
        rendered = communication.render(
            "Inspect the ledger pipeline and record the diagnostic result.",
            config={"communication_profile": "technical"},
            next_step="Review the diagnostic result.",
        )
        self.assertEqual(rendered["profile"], "technical")
        self.assertIn("ledger", rendered["message"])
        self.assertTrue(rendered["quality"]["ok"], rendered)
        redacted = communication.render(
            "Inspect task_ref=task-123 in plugins/cortex/private.py through the ledger pipeline.",
            config={"communication_profile": "technical"},
            next_step="Review the diagnostic result.",
        )
        self.assertNotIn("task-123", redacted["message"])
        self.assertNotIn("plugins/cortex/private.py", redacted["message"])

    def test_raw_quality_failure_cannot_become_a_fragment_after_redaction(self):
        rendered = communication.render(
            "Use plugins/cortex/private.py after task_ref=task-123.",
            kind="question",
            next_step="Choose the safe option.",
            config={"communication_profile": "natural"},
        )
        self.assertTrue(rendered["quality"]["fallback_applied"])
        self.assertTrue(rendered["quality"]["ok"], rendered)
        self.assertNotIn("Use from", rendered["message"])

    def test_plan_summary_is_bounded_and_contains_one_question(self):
        result = communication.render_plan(
            "Deliver the requested change.",
            ["Discover", "Design", "Implement", "Verify", "Close", "Extra"],
            question="Approve this plan?",
        )
        self.assertEqual(result["question"], "Approve this plan?")
        self.assertEqual(len([line for line in result["message"].splitlines() if line[:2].rstrip(".").isdigit()]), 5)

    def test_russian_plan_projection_is_localized_path_free_and_has_bounded_steps(self):
        result = communication.render_plan(
            "Изменить plugins/cortex/secrets.py для task_ref=abc1234567890abcdef",
            ["Изменить plugins/cortex/secrets.py для task_ref=abc1234567890abcdef"],
            question="Утвердить план?",
            config={"communication_profile": "natural", "user_language": "ru"},
        )
        self.assertTrue(result["message"].startswith("План:"))
        self.assertNotIn("plugins/cortex", result["message"])
        self.assertNotIn("task_ref", result["message"])
        self.assertIn("План готов к проверке.", result["message"])
        self.assertGreaterEqual(
            len([line for line in result["message"].splitlines() if line[:2].rstrip(".").isdigit()]),
            3,
        )
        self.assertEqual(result["next_step"], "Ответьте на вопрос ниже.")

    def test_renderer_falls_back_for_low_quality_messages_across_lifecycle_types(self):
        # A malformed model sentence must never be exposed merely because the
        # caller inspected (or ignored) quality.ok.  The deterministic fallback
        # remains user-facing and itself passes the same quality gate.
        for kind in ("launch", "plan", "steer", "question", "blocked", "completion"):
            with self.subTest(kind=kind):
                result = communication.render(
                    "The pipeline worker task_ref abc is complete. The pipeline worker task_ref abc is complete.",
                    kind=kind,
                    next_step="",
                    config={"communication_profile": "natural"},
                )
                self.assertTrue(result["quality"]["fallback_applied"])
                self.assertTrue(result["quality"]["ok"], result)
                self.assertNotRegex(result["message"], communication._INTERNAL_RE)
                self.assertTrue(result["next_step"])

    def test_quality_gate_supports_plain_russian_user_text(self):
        result = communication.render(
            "Задача завершена.",
            kind="completed",
            next_step="Проверьте результат.",
            config={"communication_profile": "natural"},
        )
        self.assertTrue(result["quality"]["ok"], result)
        self.assertFalse(result["quality"]["fallback_applied"])


if __name__ == "__main__":
    unittest.main()
