import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex_hook
from cortex_runtime import ledger_db


class ToolDedupeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.ledger = self.project / ".codex" / "cortex"
        self.path = self.project / "small.txt"
        self.path.write_text("first version\n", encoding="utf-8")
        ledger_db.ensure_database(self.ledger)
        ledger_db.create_task(
            self.ledger,
            {"task_id": "task-a", "created_at": "2026-01-01T00:00:00+00:00"},
            {"task_id": "task-a", "task_number": 1, "status": "active", "revision": 0},
            "tasks/task-a",
        )

    def tearDown(self):
        self.temp.cleanup()

    def event(self, *, hook="PostToolUse", **overrides):
        return {
            "hook_event_name": hook,
            "tool_name": "Read",
            "tool_input": {"file_path": str(self.path)},
            "tool_response": {"content": "first version\n"},
            **overrides,
        }

    def observation(self, **overrides):
        return cortex_hook.tool_observation(self.event(**overrides), self.project, "task-a", "attempt-a", 0)

    def test_fingerprint_tracks_file_generation_context_and_range(self):
        initial = self.observation(workspace_generation=1)
        self.assertTrue(initial["cacheable"])
        self.assertEqual(initial["coverage"], "full")
        self.assertNotIn("small.txt", initial["normalized_arguments"])

        different_generation = self.observation(workspace_generation=2)
        different_epoch = cortex_hook.tool_observation(self.event(workspace_generation=1), self.project, "task-a", "attempt-a", 1)
        ranged = self.observation(tool_input={"file_path": str(self.path), "offset": 0})
        self.path.write_text("changed version\n", encoding="utf-8")
        changed_file = self.observation(workspace_generation=1)

        self.assertNotEqual(initial["fingerprint"], different_generation["fingerprint"])
        self.assertNotEqual(initial["fingerprint"], different_epoch["fingerprint"])
        self.assertNotEqual(initial["fingerprint"], ranged["fingerprint"])
        self.assertFalse(ranged["cacheable"])
        self.assertNotEqual(initial["fingerprint"], changed_file["fingerprint"])

    def test_only_successful_full_reads_are_deduplicated_and_counted(self):
        state = {"attempts": [{"attempt_id": "attempt-a", "host_spawn": {"agent_id": "agent-a"}}]}
        failed = self.event(agent_id="agent-a", tool_response={"is_error": True})
        self.assertIsNone(cortex_hook.apply_tool_deduplication(failed, self.project, self.ledger, "task-a", state))
        pre = self.event(hook="PreToolUse", agent_id="agent-a")
        self.assertIsNone(cortex_hook.apply_tool_deduplication(pre, self.project, self.ledger, "task-a", state))

        succeeded = self.event(agent_id="agent-a", tool_response={"content": "first version\n"})
        self.assertIsNone(cortex_hook.apply_tool_deduplication(succeeded, self.project, self.ledger, "task-a", state))
        duplicate = cortex_hook.apply_tool_deduplication(pre, self.project, self.ledger, "task-a", state)
        self.assertEqual(duplicate, {"duplicate": True, "tool_name": "Read"})

        observation = self.observation(agent_id="agent-a")
        with ledger_db._connection(self.ledger) as connection:
            row = connection.execute(
                "SELECT status, repeat_count, normalized_arguments FROM tool_observations WHERE fingerprint=?",
                (observation["fingerprint"],),
            ).fetchone()
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["repeat_count"], 1)
        self.assertNotIn("first version", row["normalized_arguments"])

        advisory = cortex_hook.duplicate_read_advisory()
        output = advisory["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", output)
        self.assertIn("remains allowed", output["additionalContext"])

    def test_search_observations_hash_query_and_are_never_cacheable(self):
        secret = "not-for-ledger-token"
        event = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Grep",
            "tool_input": {"path": str(self.path), "query": secret},
            "tool_response": {"content": "match"},
        }
        observation = cortex_hook.tool_observation(event, self.project, "task-a", "attempt-a", 0)
        self.assertFalse(observation["cacheable"])
        self.assertNotIn(secret, observation["normalized_arguments"])
        self.assertNotIn(secret, json.dumps(observation))

    def test_compaction_rolls_the_durable_epoch(self):
        self.assertEqual(ledger_db.tool_context_epoch(self.ledger, "task-a"), 0)
        self.assertEqual(ledger_db.tool_context_epoch(self.ledger, "task-a", bump=True), 1)
        self.assertEqual(ledger_db.tool_context_epoch(self.ledger, "task-a"), 1)


if __name__ == "__main__":
    unittest.main()
