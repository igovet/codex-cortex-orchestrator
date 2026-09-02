"""Closed native-dispatch projection and adapter-boundary regressions."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.delegation import (  # noqa: E402
    native_dispatch_projection,
    validate_native_dispatch_projection,
)


class NativeDispatchProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projection = native_dispatch_projection(
            assignment_ref="d_0123456789ab",
            task_name="implementation",
            message="trusted bootstrap\nopaque capability once",
            model="gpt-5.6-luna",
            reasoning_effort="high",
        )

    def test_adapter_returns_exact_server_arguments(self) -> None:
        args = validate_native_dispatch_projection(self.projection, assignment_ref="d_0123456789ab")
        self.assertEqual(args, self.projection["native_arguments"])
        self.assertNotIn("model", args)
        self.assertEqual(args["reasoning_effort"], "high")

    def test_coordinator_selected_routing_is_part_of_native_projection(self) -> None:
        self.assertEqual(
            set(self.projection["native_arguments"]),
            {"fork_turns", "message", "task_name", "reasoning_effort"},
        )
        self.assertEqual(
            list(self.projection["native_arguments"]),
            ["fork_turns", "task_name", "reasoning_effort", "message"],
        )

        terra = native_dispatch_projection(
            assignment_ref="d_0123456789ab",
            task_name="implementation",
            message="trusted bootstrap",
            model="gpt-5.6-terra",
            reasoning_effort="xhigh",
        )
        self.assertEqual(terra["native_arguments"]["model"], "gpt-5.6-terra")
        self.assertEqual(terra["native_arguments"]["reasoning_effort"], "xhigh")
        self.assertEqual(
            list(terra["native_arguments"]),
            ["fork_turns", "task_name", "reasoning_effort", "model", "message"],
        )

    def test_reconstruction_or_cross_assignment_fails_closed(self) -> None:
        changed = dict(self.projection)
        changed["native_arguments"] = dict(self.projection["native_arguments"])
        changed["native_arguments"]["message"] += " paraphrase"
        with self.assertRaisesRegex(ValueError, "digest"):
            validate_native_dispatch_projection(changed, assignment_ref="d_0123456789ab")
        with self.assertRaisesRegex(ValueError, "assignment"):
            validate_native_dispatch_projection(self.projection, assignment_ref="d_abcdefabcdef")

    def test_oversize_message_is_rejected_before_projection(self) -> None:
        with self.assertRaisesRegex(ValueError, "bound"):
            native_dispatch_projection(
                assignment_ref="d_0123456789ab",
                task_name="implementation",
                message="x" * (64 * 1024 + 1),
                model="gpt-5.6-luna",
                reasoning_effort="high",
            )


if __name__ == "__main__":
    unittest.main()
