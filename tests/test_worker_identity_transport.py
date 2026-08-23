"""Focused checks for the server-bound worker transport contract."""
from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path
import unittest
from unittest import mock

SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cortex
from cortex_runtime import attempt_facade
from cortex_runtime.worker_identity import (
    WorkerBindingError,
    bind_semantic_params,
    set_binding_provider,
    worker_binding,
)


class WorkerIdentityTransportTests(unittest.TestCase):
    def test_worker_schemas_accept_semantic_fields_only(self) -> None:
        server_owned = {"project_root", "task_id", "task_ref", "attempt_id", "profile", "dispatch_ref", "briefing_digest"}
        for operation in ("record_attempt_event", "complete_attempt", "worker_question", "read_dispatch_briefing"):
            schema = cortex.PUBLIC_SCHEMA_REGISTRY[operation]
            self.assertTrue(server_owned.isdisjoint(schema.get("properties", {})), operation)
        self.assertEqual(
            set(cortex.WORKER_READ_WORKER_RESULT_SCHEMA["properties"]),
            {"attempt_result_ref", "worker_capability"},
        )

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

    def test_binding_provider_is_resolved_lazily_after_native_spawn(self) -> None:
        binding = {
            "project_root": "/tmp/project",
            "task_id": "task-1",
            "attempt_id": "attempt-1",
            "profile": "backend_dev",
            "dispatch_ref": "dispatch-1",
            "briefing_digest": "a" * 64,
        }
        calls = []
        set_binding_provider(lambda _capability: calls.append(True) or binding)
        try:
            with worker_binding(None):
                merged = bind_semantic_params({"worker_capability": "opaque-capability", "summary": "done"})
            self.assertEqual(merged["attempt_id"], "attempt-1")
            self.assertEqual(calls, [True])
        finally:
            set_binding_provider(None)

    def test_capability_resolver_matches_exact_parallel_attempt(self) -> None:
        """Parallel workers are selected by opaque capability, never thread id."""
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "host"
            project = Path(temporary) / "project"
            store = base / "projects" / ("p-" + "a" * 64)
            base.mkdir()
            (base / "projects").mkdir()
            store.mkdir()
            project.mkdir()
            import hashlib
            capability = "opaque-capability-for-worker-one-0123456789"
            capability_digest = hashlib.sha256(capability.encode()).hexdigest()
            state = {
                "status": "active",
                "thread_id": "orchestration-task-1",
                "attempts": [{
                    "attempt_id": "attempt-1",
                    "status": "awaiting_host_spawn",
                    "profile": "backend_dev",
                    "dispatch_ref": "dispatch-1",
                    "briefing_digest": "b" * 64,
                    "worker_capability_digest": capability_digest,
                }],
            }
            session = {
                "host_agent_id": "",
                "attempt_id": "attempt-1",
                "host_task_name": "explorer-race",
                "status": "awaiting_spawn",
            }
            with mock.patch.dict(os.environ, {
                cortex.HOST_CONTROL_STORE_ENV: str(base),
            }, clear=False), mock.patch.object(
                cortex, "db_task_index", return_value={"task-1": {}}
            ), mock.patch.object(
                cortex, "db_list_worker_sessions", return_value=[session]
            ), mock.patch.object(
                cortex, "db_load_task", return_value=({"project_root": str(project)}, state, None, "tasks/task-1")
            ):
                resolved = cortex._worker_binding_from_capability(capability)
            self.assertEqual(resolved["attempt_id"], "attempt-1")
            self.assertEqual(resolved["session_id"], "")


if __name__ == "__main__":
    unittest.main()
