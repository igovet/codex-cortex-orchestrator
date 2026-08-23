"""Lifecycle regressions for the black-box MCP test harness."""
from __future__ import annotations

import gc
import json
import os
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

from tests.jsonrpc_harness import JsonRpcHarness

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex  # noqa: E402


ROOT = Path(__file__).parents[1]
SERVER = ROOT / "plugins/cortex/scripts/cortex.py"


class JsonRpcHarnessLifecycleTests(unittest.TestCase):
    def test_real_worker_sequence_uses_launch_binding_without_model_identity(self) -> None:
        """A native-style process can read, checkpoint, and close semantically."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            project.mkdir()
            host_state_dir = base / "host-private-store"
            host_state_dir.mkdir(mode=0o700)
            host_state_dir.chmod(0o700)
            previous = os.environ.get(cortex.HOST_CONTROL_STORE_ENV)
            os.environ[cortex.HOST_CONTROL_STORE_ENV] = str(host_state_dir)
            try:
                started = cortex.start_orchestration({
                    "project_root": str(project),
                    "task": {
                        "user_request": "Exercise the real worker transport.",
                        "acceptance_criteria": ["The worker sequence is persisted."],
                        "verification": ["Run this focused integration test."],
                        "complexity": "C1",
                    },
                    "waves": [{"workers": [{"phase": "discover"}]}],
                })
                self.assertTrue(started["ok"], started)
                ledger = cortex.ledger_root_path({"project_root": str(project)})
                task_dir = next((ledger / "tasks").iterdir())
                state = cortex.load_task_state_for_artifact(task_dir)
                attempt = state["attempts"][0]
                binding = {
                    "project_root": str(project),
                    "task_id": state["task_id"],
                    "attempt_id": attempt["attempt_id"],
                    "profile": attempt["profile"],
                    "dispatch_ref": attempt["dispatch_ref"],
                    "briefing_digest": attempt["briefing_digest"],
                }
                # The desktop host commonly launches the default union
                # audience. Without launch binding, an unqualified request
                # remains fail-closed rather than selecting a dispatch.
                with JsonRpcHarness(SERVER, project, host_state_dir) as default_harness:
                    provider_read = default_harness.tool(
                        "read_worker_result", {"attempt_result_ref": "missing-result"},
                    )
                    self.assertFalse(provider_read["ok"])
                    self.assertNotIn("task_ref could not be resolved", json.dumps(provider_read))
                with JsonRpcHarness(
                    SERVER, project, host_state_dir,
                    audience="worker", worker_binding=binding,
                ) as harness:
                    listed = harness.request("tools/list", {})["tools"]
                    worker_schemas = {
                        item["name"]: item["inputSchema"]
                        for item in listed
                        if item["name"] in {"read_dispatch_briefing", "record_attempt_event", "complete_attempt"}
                    }
                    self.assertTrue(harness.tool("read_dispatch_briefing", {})["ok"])
                    self.assertTrue(harness.tool("record_attempt_event", {
                        "event_type": "progress", "payload": {"summary": "checkpoint"},
                    })["ok"])
                    completed = harness.tool("complete_attempt", {
                        "status": "completed", "summary": "Sequence completed.",
                        "findings": [], "decisions_needed": [], "unresolved": [],
                    })
                    self.assertTrue(completed["ok"], completed)
                    self.assertEqual(completed["outcome"], "attempt_completed")
            finally:
                if previous is None:
                    os.environ.pop(cortex.HOST_CONTROL_STORE_ENV, None)
                else:
                    os.environ[cortex.HOST_CONTROL_STORE_ENV] = previous

    def test_close_reaps_process_and_closes_every_pipe_without_resource_warning(self) -> None:
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always", ResourceWarning)
            with tempfile.TemporaryDirectory() as directory:
                project = Path(directory) / "project"
                project.mkdir()
                host_state_dir = Path(directory) / "host-private-store"
                host_state_dir.mkdir(mode=0o700)
                host_state_dir.chmod(0o700)
                harness = JsonRpcHarness(SERVER, project, host_state_dir)
                process = harness.process
                harness.close()
                harness.close()
                del harness
                gc.collect()

            resource_warnings = [item for item in observed if issubclass(item.category, ResourceWarning)]

        self.assertEqual(resource_warnings, [])
        self.assertIsNotNone(process.returncode)
        self.assertTrue(process.stdin is None or process.stdin.closed)
        self.assertTrue(process.stdout is None or process.stdout.closed)
        self.assertTrue(process.stderr is None or process.stderr.closed)

    def test_close_preserves_stderr_when_server_exits_nonzero(self) -> None:
        source = (
            "import json, sys\n"
            "request = json.loads(sys.stdin.readline())\n"
            "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': "
            "{'serverInfo': {'name': 'cortex'}}}), flush=True)\n"
            "sys.stdin.readline()\n"
            "print('deliberate server diagnostic', file=sys.stderr, flush=True)\n"
            "raise SystemExit(7)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            project.mkdir()
            host_state_dir = base / "host-private-store"
            host_state_dir.mkdir(mode=0o700)
            host_state_dir.chmod(0o700)
            server = base / "failing_server.py"
            server.write_text(source, encoding="utf-8")
            harness = JsonRpcHarness(server, project, host_state_dir)
            process = harness.process
            with self.assertRaisesRegex(RuntimeError, r"exited 7: deliberate server diagnostic"):
                harness.close()

        self.assertIsNotNone(process.returncode)
        self.assertTrue(process.stdout is None or process.stdout.closed)
        self.assertTrue(process.stderr is None or process.stderr.closed)


if __name__ == "__main__":
    unittest.main()
