"""Focused parity checks for the public Cortex MCP contracts.

These tests intentionally use only the stdlib unittest runner.  The runtime
normalizers are the executable contract; the public schema must not reject a
value that those normalizers explicitly document and accept.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex


class PublicSchemaParityTests(unittest.TestCase):
    def test_worker_and_successor_schemas_advertise_only_canonical_profiles_and_phases(self) -> None:
        start_worker = cortex.PUBLIC_SCHEMA_REGISTRY["start_orchestration"]["properties"]["waves"]["items"]["properties"]["workers"]["items"]
        future_worker = cortex.PUBLIC_SCHEMA_REGISTRY["manage_orchestration"]["properties"]["payload"]["properties"]["future_waves"]["items"]["properties"]["workers"]["items"]
        expected = set(cortex.AGENTS)
        for worker in (start_worker, future_worker):
            self.assertEqual(expected, set(worker["properties"]["profile"]["enum"]))
            self.assertEqual(set(cortex.AVAILABLE_GATES), set(worker["properties"]["depends_on"]["items"]["enum"]))

        # Worker lifecycle operations receive profile through the server-bound
        # session; it is deliberately absent from their public semantic forms.
        for operation in ("worker_question", "record_attempt_event", "complete_attempt", "read_dispatch_briefing", "read_worker_result"):
            self.assertNotIn("profile", cortex.PUBLIC_SCHEMA_REGISTRY[operation].get("properties", {}), operation)

    def test_worker_scope_schema_matches_non_broad_runtime_validator(self) -> None:
        item_schema = (
            cortex.PUBLIC_SCHEMA_REGISTRY["start_orchestration"]["properties"]["waves"]
            ["items"]["properties"]["workers"]["items"]["properties"]["allowed_paths"]["items"]
        )
        self.assertEqual(item_schema["not"], {"enum": [".", "*"]})

        with self.assertRaisesRegex(ValueError, "explicit and non-broad"):
            cortex._v3_compact_waves(
                [{"workers": [{"phase": "discover", "allowed_paths": ["."]}]}],
                {"user_request": "scope parity", "complexity": "C1"},
            )

        microtask_item = (
            cortex.PUBLIC_SCHEMA_REGISTRY["complete_attempt"]["properties"]["planning"]
            ["properties"]["work_packages"]["items"]["properties"]["microtasks"]
            ["items"]["properties"]["allowed_paths"]["items"]
        )
        self.assertEqual(microtask_item["not"], {"enum": [".", "*"]})

    def test_task_scope_text_arrays_advertise_non_empty_items(self) -> None:
        task_properties = cortex.PUBLIC_SCHEMA_REGISTRY["start_orchestration"]["properties"]["task"]["properties"]
        for field in ("requirements", "constraints", "scope", "allowed_paths", "pause_conditions"):
            self.assertEqual(task_properties[field]["items"], {"type": "string", "minLength": 1}, field)

    def test_runtime_accepts_only_canonical_profile_and_phase_forms(self) -> None:
        result = cortex._v3_compact_waves(
            [{"workers": [{"phase": "qa", "profile": "qa_engineer"}]}],
            {"user_request": "canonical parity", "complexity": "C1"},
        )
        self.assertEqual(result[0]["delegations"][0]["agent"], "qa_engineer")
        for phase, profile in (("verification", "qa_engineer"), ("discover", "discovery"), ("plan", "planner_agent")):
            with self.subTest(phase=phase, profile=profile), self.assertRaises(ValueError):
                cortex._v3_compact_waves(
                    [{"workers": [{"phase": phase, "profile": profile}]}],
                    {"user_request": "canonical parity", "complexity": "C1"},
                )


if __name__ == "__main__":
    unittest.main()
