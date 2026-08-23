"""Focused checks for the host-owned worker transport contract."""
from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path
import unittest

SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cortex
from cortex_runtime import attempt_facade
from cortex_runtime.worker_identity import (
    WorkerBindingError,
    bind_semantic_params,
    current_binding,
    worker_request,
    worker_binding,
)


class WorkerIdentityTransportTests(unittest.TestCase):
    def test_worker_schemas_accept_semantic_fields_only(self) -> None:
        server_owned = {"project_root", "task_id", "task_ref", "attempt_id", "profile", "dispatch_ref", "briefing_digest"}
        for operation in ("record_attempt_event", "complete_attempt", "worker_question", "read_dispatch_briefing"):
            schema = cortex.PUBLIC_SCHEMA_REGISTRY[operation]
            self.assertTrue(server_owned.isdisjoint(schema.get("properties", {})), operation)
        self.assertEqual(set(cortex.WORKER_READ_WORKER_RESULT_SCHEMA["properties"]), {"attempt_result_ref"})

    def test_unbound_semantic_worker_call_fails_closed(self) -> None:
        response = attempt_facade.record_attempt_event({
            "event_type": "progress",
            "payload": {"summary": "checkpoint"},
        })
        self.assertFalse(response["ok"])
        self.assertIn("server-bound worker session", response["diagnostics"][0]["message"])

    def test_bound_identity_is_injected_and_explicit_identity_is_rejected(self) -> None:
        binding = {
            "project_root": "/tmp/project",
            "task_id": "task-1",
            "attempt_id": "attempt-1",
            "profile": "backend_dev",
            "dispatch_ref": "dispatch-1",
            "briefing_digest": "a" * 64,
        }
        with worker_binding(binding):
            self.assertEqual(bind_semantic_params({"summary": "done"})["task_id"], "task-1")
            with self.assertRaises(WorkerBindingError):
                bind_semantic_params({"project_root": "/tmp/other", "summary": "done"})
            response = attempt_facade.record_attempt_event({
                "project_root": "/tmp/project",
                "event_type": "progress",
                "payload": {"summary": "checkpoint"},
            })
        self.assertFalse(response["ok"])
        self.assertIn("server-owned", response["diagnostics"][0]["message"])

    def test_request_scope_restores_host_launch_binding(self) -> None:
        binding = {
            "project_root": "/tmp/project",
            "task_id": "task-1",
            "attempt_id": "attempt-1",
            "profile": "backend_dev",
        }
        with worker_binding(binding):
            with worker_request():
                self.assertEqual(bind_semantic_params({"summary": "x"})["attempt_id"], "attempt-1")
            self.assertEqual(current_binding(), binding)

    def test_native_host_session_resolves_binding_without_env_json(self) -> None:
        """Default CLI child launches can recover the server binding from its host row."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            project.mkdir()
            host_store = base / "host-store"
            host_store.mkdir(mode=0o700)
            previous_store = os.environ.get(cortex.HOST_CONTROL_STORE_ENV)
            previous_thread = os.environ.get("CODEX_THREAD_ID")
            os.environ[cortex.HOST_CONTROL_STORE_ENV] = str(host_store)
            try:
                started = cortex.start_orchestration({
                    "project_root": str(project),
                    "task": {
                        "user_request": "Exercise host-owned native binding recovery.",
                        "acceptance_criteria": ["The child binding is recovered."],
                        "verification": ["Run the focused transport test."],
                        "complexity": "C1",
                    },
                    "waves": [{"workers": [{"phase": "discover"}]}],
                })
                self.assertTrue(started["ok"], started)
                ledger = cortex.ledger_root_path({"project_root": str(project)})
                task_id = next(iter(cortex.db_task_index(ledger)))
                loaded = cortex._v3_task_state(ledger, task_id)
                self.assertIsNotNone(loaded)
                _task_dir, state, _task = loaded
                attempt = state["attempts"][0]
                host_id = "native-child-session-123"
                cortex.db_put_worker_session(ledger, {
                    "task_id": task_id,
                    "attempt_id": attempt["attempt_id"],
                    "host_agent_id": host_id,
                    "host_task_name": attempt["spawn_request"]["task_name"],
                    "host_tool": "spawn_agent",
                    "status": "awaiting_spawn",
                })
                os.environ.pop("CORTEX_WORKER_BINDING_JSON", None)
                os.environ["CODEX_THREAD_ID"] = host_id
                binding = cortex._worker_binding_from_host_session()
                self.assertIsNotNone(binding)
                self.assertEqual(binding["task_id"], task_id)
                self.assertEqual(binding["attempt_id"], attempt["attempt_id"])
                self.assertEqual(binding["profile"], attempt["profile"])
            finally:
                if previous_store is None:
                    os.environ.pop(cortex.HOST_CONTROL_STORE_ENV, None)
                else:
                    os.environ[cortex.HOST_CONTROL_STORE_ENV] = previous_store
                if previous_thread is None:
                    os.environ.pop("CODEX_THREAD_ID", None)
                else:
                    os.environ["CODEX_THREAD_ID"] = previous_thread

    def test_native_host_session_resolves_one_fresh_child_without_host_ids(self) -> None:
        """Desktop native children may start with no child identity env vars."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            project.mkdir()
            host_store = base / "host-store"
            host_store.mkdir(mode=0o700)
            previous_store = os.environ.get(cortex.HOST_CONTROL_STORE_ENV)
            saved = {name: os.environ.get(name) for name in (
                "CORTEX_WORKER_BINDING_JSON", "CODEX_SESSION_ID", "CODEX_THREAD_ID",
                "CODEX_AGENT_ID", "CODEX_SUBAGENT_ID", "CODEX_TASK_ID",
            )}
            os.environ[cortex.HOST_CONTROL_STORE_ENV] = str(host_store)
            try:
                started = cortex.start_orchestration({
                    "project_root": str(project),
                    "task": {
                        "user_request": "Exercise an identity-free native child handoff.",
                        "acceptance_criteria": ["The child binding is recovered."],
                        "verification": ["Run the focused transport test."],
                        "complexity": "C1",
                    },
                    "waves": [{"workers": [{"phase": "discover"}]}],
                })
                self.assertTrue(started["ok"], started)
                ledger = cortex.ledger_root_path({"project_root": str(project)})
                task_id = next(iter(cortex.db_task_index(ledger)))
                loaded = cortex._v3_task_state(ledger, task_id)
                self.assertIsNotNone(loaded)
                _task_dir, state, task = loaded
                attempt = state["attempts"][0]
                cortex.db_put_worker_session(ledger, {
                    "task_id": task_id,
                    "attempt_id": attempt["attempt_id"],
                    "host_agent_id": "native-child-without-env",
                    "host_task_name": attempt["spawn_request"]["task_name"],
                    "host_tool": "spawn_agent",
                    "status": "running",
                })
                for name in saved:
                    os.environ.pop(name, None)
                binding = cortex._worker_binding_from_host_session()
                self.assertIsNotNone(binding)
                self.assertEqual(binding["task_id"], task_id)
                self.assertEqual(binding["attempt_id"], attempt["attempt_id"])
            finally:
                if previous_store is None:
                    os.environ.pop(cortex.HOST_CONTROL_STORE_ENV, None)
                else:
                    os.environ[cortex.HOST_CONTROL_STORE_ENV] = previous_store
                for name, value in saved.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
