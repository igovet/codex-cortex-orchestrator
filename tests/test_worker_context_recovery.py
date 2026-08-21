import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex_hook


class WorkerContextRecoveryTests(unittest.TestCase):
    def test_compaction_rehydrates_only_exact_attempt_artifacts(self):
        task_dir = Path("/private/tasks/task-1")
        digest = "a" * 64
        state = {
            "attempts": [{
                "attempt_id": "attempt-1",
                "profile": "planner",
                "briefing_file": "briefings/attempt-1.md",
                "briefing_digest": digest,
                "plan_unit_file": "planning/unit.json",
                "plan_unit_digest": "b" * 64,
                "spawn_request": {"task_name": "planner_task_01"},
            }]
        }
        definition = {
            "user_intent_artifact_path": "intent/user-request.txt",
            "user_request_digest": "c" * 64,
        }
        context = cortex_hook.worker_context_recovery(
            {"agent_type": "planner_task_01", "source": "compact"},
            state,
            definition,
            task_dir,
        )
        self.assertIn(str(task_dir / "briefings/attempt-1.md"), context)
        self.assertIn(str(task_dir / "planning/unit.json"), context)
        self.assertIn("sha256=" + digest, context)
        self.assertIn("same attempt", context)
        self.assertNotIn("state.sqlite", context)

    def test_ambiguous_worker_identity_fails_closed(self):
        context = cortex_hook.worker_context_recovery(
            {"agent_type": "planner", "source": "compaction"},
            {"attempts": []},
            {},
            Path("/private/tasks/task-1"),
        )
        self.assertIn("exact worker attempt identity is unavailable", context)

    def test_unavailable_wait_requires_explicit_single_target_host_proof(self):
        state = {
            "attempts": [{
                "status": "running",
                "host_spawn": {"agent_id": "native-worker-1"},
            }]
        }
        base = {
            "hook_event_name": "PostToolUse",
            "tool_name": "wait",
            "tool_input": {"receiver_thread_ids": ["native-worker-1"]},
        }
        proven = cortex_hook.unavailable_wait_target_ids(
            {**base, "tool_response": {"is_error": True, "error": {"code": "agent_not_found"}}},
            state,
        )
        self.assertEqual(proven, ["native-worker-1"])

        # A transient/generic failure and an unscoped multi-target result do
        # not prove that this exact child ended, so both remain non-terminal.
        self.assertEqual(
            cortex_hook.unavailable_wait_target_ids(
                {**base, "tool_response": {"is_error": True, "error": {"code": "transport_error"}}},
                state,
            ),
            [],
        )
        self.assertEqual(
            cortex_hook.unavailable_wait_target_ids(
                {**base, "tool_response": {"is_error": True, "error": "agent temporarily unavailable"}},
                state,
            ),
            [],
        )
        self.assertEqual(
            cortex_hook.unavailable_wait_target_ids(
                {
                    **base,
                    "tool_input": {"receiver_thread_ids": ["native-worker-1", "native-worker-2"]},
                    "tool_response": {"is_error": True, "error": {"code": "agent_not_found"}},
                },
                state,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
