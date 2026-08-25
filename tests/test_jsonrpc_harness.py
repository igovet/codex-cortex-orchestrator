"""Lifecycle regressions for the black-box MCP test harness."""
from __future__ import annotations

import gc
import os
import re
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
    def test_real_scalar_worker_question_poll_returns_canonical_answer_after_resume(self) -> None:
        """The exact Desktop poll shape must never escape as MCP -32602.

        This crosses the real stdio declaration, worker facade, durable answer
        route, and response projection.  It deliberately uses the scalar
        ``question-0001`` form seen in Desktop rather than a test alias.
        """
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            project.mkdir()
            host_state_dir = base / "host-private-store"
            host_state_dir.mkdir(mode=0o700)
            host_state_dir.chmod(0o700)
            with JsonRpcHarness(SERVER, project, host_state_dir) as coordinator:
                started = coordinator.tool("start_orchestration", {
                    "task": {
                        "user_request": "Choose a safe rollout mode and record the answer.",
                        "acceptance_criteria": ["The selected safe mode is returned to the same worker."],
                        "verification": ["Poll the durable question with the original scalar references."],
                        "complexity": "C1",
                    },
                    "waves": [{"phase": "implementation", "workers": [{"profile": "general", "allowed_paths": ["result.txt"]}]}],
                })
                self.assertTrue(started["ok"], started)
                # Public JSON projection preserves the native message bytes;
                # depending on the host JSON layer its embedded call example
                # can retain escaped quote characters.
                bootstrap = str(started["dispatches"][0]["arguments"]["message"]).replace('\\\\"', '"')
                task_match = re.search(r"task-[0-9a-f]{12}", bootstrap)
                assignment_match = re.search(r"assignment-v1-[0-9a-f]{64}", bootstrap)
                self.assertIsNotNone(task_match)
                self.assertIsNotNone(assignment_match)
                assert task_match is not None and assignment_match is not None
                worker_authority = {
                    "task_ref": task_match.group(0),
                    "assignment_ref": assignment_match.group(0),
                }
                self.assertEqual(worker_authority["task_ref"], started["task_ref"])

                with JsonRpcHarness(SERVER, project, host_state_dir, audience="worker") as worker:
                    briefing = worker.tool("read_dispatch_briefing", worker_authority)
                    self.assertTrue(briefing["ok"], briefing)
                    asked = worker.tool("worker_question", {
                        **worker_authority,
                        "action": "ask",
                        "question_type": "single_select",
                        "decision_scope": "task_decision",
                        "question": "Which rollout mode should be used?",
                        "header": "Rollout mode",
                        "options": [
                            {"option_id": "safe_mode", "label_en": "Use safe mode", "description": "Preserves rollback."},
                            {"option_id": "fast_mode", "label_en": "Use fast mode", "description": "Prioritizes speed."},
                        ],
                        "recommended_option_ids": ["safe_mode"],
                        "recommendation": "Use safe mode because rollback remains available.",
                    })
                    self.assertTrue(asked["ok"], asked)
                    self.assertEqual(asked["question_ref"], "question-0001")
                    # The briefing/question calls are server-recorded
                    # post-bootstrap evidence.  A coordinator cannot erase
                    # that live assignment by falsely claiming bootstrap loss.
                    misclassified = coordinator.tool("manage_orchestration", {
                        "task_ref": started["task_ref"],
                        "coordinator_ref": started["coordinator_ref"],
                        "intent": "finalize_bootstrap_failure",
                        "payload": {
                            "dispatch_ref": started["dispatches"][0]["dispatch_ref"],
                            "reason_code": "bootstrap_missing_identity",
                        },
                    })
                    self.assertFalse(misclassified["ok"], misclassified)
                    self.assertEqual(misclassified["action"], {"kind": "none"})
                    self.assertEqual(misclassified["recovery"]["kind"], "terminal_stop")
                    self.assertEqual(misclassified["recovery"]["operation"], "manage_orchestration")
                    self.assertFalse(misclassified["recovery"]["retryable"], misclassified)
                    self.assertFalse(misclassified["recovery"]["state_mutated"], misclassified)
                    answered = coordinator.tool("manage_orchestration", {
                        "task_ref": started["task_ref"],
                        "coordinator_ref": started["coordinator_ref"],
                        "intent": "question",
                        # A normal chat reply arrives as a scalar rather than
                        # an option-id object.  This used to persist an empty
                        # answer_en projection and made the later poll escape
                        # through MCP with a misleading type error.
                        "payload": {"question_ref": asked["question_ref"], "answer": "Use safe mode."},
                    })
                    self.assertTrue(answered["ok"], answered)
                    self.assertEqual(answered["resume"], {"kind": "poll", "question_ref": "question-0001"})

                    # Exact live scalar argument shape: no aliases, objects,
                    # inferred session identity, or substituted identifier.
                    polled = worker.tool("worker_question", {
                        "action": "poll",
                        "task_ref": worker_authority["task_ref"],
                        "assignment_ref": worker_authority["assignment_ref"],
                        "question_ref": "question-0001",
                    })
                    self.assertTrue(polled["ok"], polled)
                    self.assertEqual(polled["outcome"], "question_answered")
                    self.assertEqual(polled["question_ref"], "question-0001")
                    self.assertEqual(polled["answer"], {"text": "Use safe mode."})

    def test_real_worker_sequence_uses_explicit_v11_assignment_capability(self) -> None:
        """A worker process must carry the exact bootstrap capability in each call."""
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
                    "waves": [{"phase": "discover", "workers": [{}]}],
                })
                self.assertTrue(started["ok"], started)
                ledger = cortex.ledger_root_path({"project_root": str(project)})
                bootstrap = str(started["dispatches"][0]["arguments"]["message"])
                match = re.search(
                    r'read_dispatch_briefing\(\{"task_ref":"([^"]+)","assignment_ref":"([^"]+)"\}\)',
                    bootstrap,
                )
                self.assertIsNotNone(match)
                assert match is not None
                assignment_authority = {
                    "task_ref": match.group(1),
                    "assignment_ref": match.group(2),
                }
                self.assertEqual(assignment_authority["task_ref"], started["task_ref"])
                # The default union cannot infer a worker assignment from its
                # process, audience, or host environment.
                with JsonRpcHarness(SERVER, project, host_state_dir) as default_harness:
                    provider_read = default_harness.tool(
                        "read_worker_result", {
                            "task_ref": started["task_ref"],
                            "attempt_result_ref": "missing-result",
                        },
                    )
                    self.assertFalse(provider_read["ok"])
                with JsonRpcHarness(
                    SERVER, project, host_state_dir,
                    audience="worker",
                ) as harness:
                    listed = harness.request("tools/list", {})["tools"]
                    worker_schemas = {
                        item["name"]: item["inputSchema"]
                        for item in listed
                        if item["name"] in {"read_dispatch_briefing", "record_attempt_event", "complete_attempt"}
                    }
                    self.assertTrue(harness.tool("read_dispatch_briefing", assignment_authority)["ok"])
                    self.assertTrue(harness.tool("record_attempt_event", {
                        **assignment_authority,
                        "event_type": "progress", "payload": {"summary": "checkpoint"},
                    })["ok"])
                    completed = harness.tool("complete_attempt", {
                        **assignment_authority,
                        "outcome": {
                            "status": "completed", "summary": "Sequence completed.",
                            "findings": [], "decisions_needed": [], "unresolved": [], "claims": [],
                        },
                    })
                    self.assertTrue(completed["ok"], completed)
                    self.assertEqual(completed["schema"], "cortex/worker-completion/v11")
                    self.assertTrue(completed["terminal"])
                    self.assertEqual(
                        set(completed),
                        {"schema", "ok", "terminal"},
                    )
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
