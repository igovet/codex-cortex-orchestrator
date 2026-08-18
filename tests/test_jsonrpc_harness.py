"""Lifecycle regressions for the black-box MCP test harness."""
from __future__ import annotations

import gc
import tempfile
import unittest
import warnings
from pathlib import Path

from tests.jsonrpc_harness import JsonRpcHarness


ROOT = Path(__file__).parents[1]
SERVER = ROOT / "plugins/cortex/scripts/cortex.py"


class JsonRpcHarnessLifecycleTests(unittest.TestCase):
    def test_close_reaps_process_and_closes_every_pipe_without_resource_warning(self) -> None:
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always", ResourceWarning)
            with tempfile.TemporaryDirectory() as directory:
                project = Path(directory) / "project"
                project.mkdir()
                harness = JsonRpcHarness(SERVER, project, project / ".codex/cortex")
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
            server = base / "failing_server.py"
            server.write_text(source, encoding="utf-8")
            harness = JsonRpcHarness(server, project, project / ".codex/cortex")
            process = harness.process
            with self.assertRaisesRegex(RuntimeError, r"exited 7: deliberate server diagnostic"):
                harness.close()

        self.assertIsNotNone(process.returncode)
        self.assertTrue(process.stdout is None or process.stdout.closed)
        self.assertTrue(process.stderr is None or process.stderr.closed)


if __name__ == "__main__":
    unittest.main()
