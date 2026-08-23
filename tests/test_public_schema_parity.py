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
    def test_worker_and_successor_schemas_advertise_runtime_profile_aliases(self) -> None:
        start_worker = cortex.PUBLIC_SCHEMA_REGISTRY["start_orchestration"]["properties"]["waves"]["items"]["properties"]["workers"]["items"]
        future_worker = cortex.PUBLIC_SCHEMA_REGISTRY["manage_orchestration"]["properties"]["payload"]["properties"]["future_waves"]["items"]["properties"]["workers"]["items"]
        expected = set(cortex.PROFILE_ALIASES) | set(cortex.V3_AUTOMATIC_IMPLEMENTATION_PROFILE_ALIASES)
        for worker in (start_worker, future_worker):
            self.assertTrue(expected.issubset(set(worker["properties"]["profile"]["enum"])))
            self.assertTrue({"planning", "verification", "build_verification"}.issubset(
                set(worker["properties"]["depends_on"]["items"]["enum"])
            ))

        for operation in (
            "worker_question", "record_attempt_event", "complete_attempt",
            "read_dispatch_briefing", "read_worker_result",
        ):
            profile_enum = set(cortex.PUBLIC_SCHEMA_REGISTRY[operation]["properties"]["profile"]["enum"])
            self.assertTrue(expected.issubset(profile_enum), operation)

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

    def test_runtime_accepts_the_aliases_advertised_by_worker_schema(self) -> None:
        cases = (("qa", "qa"), ("discover", "discovery"), ("plan", "planner_agent"), ("close", "build_verification"))
        for phase, profile in cases:
            with self.subTest(phase=phase, profile=profile):
                result = cortex._v3_compact_waves(
                    [{"workers": [{"phase": phase, "profile": profile}]}],
                    {"user_request": "alias parity", "complexity": "C1"},
                )
                self.assertTrue(result[0]["delegations"][0]["agent"])


if __name__ == "__main__":
    unittest.main()
