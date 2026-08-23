"""Focused checks for the host-owned worker transport contract."""
from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main()
