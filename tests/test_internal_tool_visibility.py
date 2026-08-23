"""Focused boundary checks for server-only control ports."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex
from cortex_runtime.mcp_api import (
    COORDINATOR_PUBLIC_TOOL_NAMES,
    SERVER_ONLY_TOOL_NAMES,
    WORKER_PUBLIC_TOOL_NAMES,
    public_tools_for_audience,
)


class InternalToolVisibilityTests(unittest.TestCase):
    def test_server_only_control_ports_are_not_in_any_model_projection(self) -> None:
        public_names = set(COORDINATOR_PUBLIC_TOOL_NAMES) | set(WORKER_PUBLIC_TOOL_NAMES)
        self.assertTrue(SERVER_ONLY_TOOL_NAMES)
        self.assertTrue(SERVER_ONLY_TOOL_NAMES.isdisjoint(public_names))
        self.assertNotIn("record_evidence", public_names)
        self.assertNotIn("execute_verification_command", public_names)
        self.assertNotIn("record_gate_outcome", public_names)
        self.assertNotIn("commit_gate", public_names)
        self.assertNotIn("update_pipeline", public_names)

    def test_audience_projection_never_resurrects_internal_handlers(self) -> None:
        projected = public_tools_for_audience(cortex.PUBLIC_TOOLS, "default")
        self.assertEqual(set(projected), public_names := {
            *COORDINATOR_PUBLIC_TOOL_NAMES,
            *WORKER_PUBLIC_TOOL_NAMES,
        })
        self.assertTrue(SERVER_ONLY_TOOL_NAMES.isdisjoint(projected))


if __name__ == "__main__":
    unittest.main()
