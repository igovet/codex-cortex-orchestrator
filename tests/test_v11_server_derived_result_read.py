"""Focused v11 coordinator result transport without child-carried refs."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cortex
from cortex_runtime import attempt_facade, attempt_protocol


class ServerDerivedCoordinatorResultReadTests(unittest.TestCase):
    def test_parallel_wave_is_derived_in_dispatch_order_without_child_refs(self) -> None:
        task_ref = "task-000000000001"
        result_refs = ["attempt-result-first", "attempt-result-second"]
        attempts = [
            {
                "attempt_id": "qa-01", "status": "running", "gate": "qa",
                "profile": "qa_engineer", "orchestration_wave_id": "wave-2",
                "dispatch_ref": "dispatch-first", "attempt_result_ref": result_refs[0],
            },
            {
                "attempt_id": "review-01", "status": "running", "gate": "review",
                "profile": "code_reviewer", "orchestration_wave_id": "wave-2",
                "dispatch_ref": "dispatch-second", "attempt_result_ref": result_refs[1],
            },
        ]
        state = {"task_id": "task-internal", "attempts": attempts}
        wave = {
            "wave_id": "wave-2", "gates": ["qa", "review"],
            "attempt_ids": ["qa-01", "review-01"],
        }

        def canonical(*_args, attempt_id: str, **_kwargs):
            index = wave["attempt_ids"].index(attempt_id)
            return {
                "attempt_id": attempt_id,
                "result_ref": result_refs[index],
                "lifecycle_status": attempt_protocol.LIFECYCLE_COMPLETED,
            }

        def view(*_args, attempt_id: str, **_kwargs):
            index = wave["attempt_ids"].index(attempt_id)
            return {
                "attempt_result_ref": result_refs[index],
                "lifecycle_status": attempt_protocol.LIFECYCLE_COMPLETED,
                "phase": attempts[index]["gate"],
                "producer": attempts[index]["profile"],
                "result": {
                    "status": "completed", "summary": f"result {index + 1}",
                    "findings": [], "decisions_needed": [], "unresolved": [], "claims": [],
                },
            }

        with mock.patch.object(
            attempt_facade._runtime, "authorize_coordinator_ref",
            return_value=(Path("/project"), Path("/task"), state, {}, task_ref),
        ), mock.patch.object(
            attempt_facade._runtime, "_task_document_root", return_value=Path("/ledger"),
        ), mock.patch.object(
            attempt_facade._runtime, "_load_orchestrate_plan", return_value={"waves": [wave]},
        ), mock.patch.object(
            attempt_facade._runtime, "active_gates", return_value=["qa", "review"],
        ), mock.patch.object(
            attempt_facade._runtime, "_wave_for_gates", return_value=wave,
        ), mock.patch.object(
            attempt_protocol, "get_attempt_result", side_effect=canonical,
        ), mock.patch.object(
            attempt_protocol, "build_attempt_result_view", side_effect=view,
        ):
            response = attempt_facade.read_worker_result({
                "task_ref": task_ref,
                "coordinator_ref": "a" * 64,
                "step": 2,
            })

        self.assertTrue(response["ok"])
        self.assertEqual([item["summary"] for item in response["results"]], ["result 1", "result 2"])
        self.assertEqual(response["continuation"], {
            "kind": "continue",
            "step": 2,
            "results": [
                {"attempt_result_ref": result_refs[0], "worker": 1},
                {"attempt_result_ref": result_refs[1], "worker": 2},
            ],
        })
        self.assertNotIn("attempt_result_ref", response)

    def test_coordinator_form_rejects_a_child_carried_result_ref(self) -> None:
        response = attempt_facade.read_worker_result({
            "task_ref": "task-000000000001",
            "coordinator_ref": "a" * 64,
            "step": 1,
            "attempt_result_ref": "attempt-result-child-output",
        })
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["diagnostics"][0]["json_pointer"], "/attempt_result_ref")


if __name__ == "__main__":
    unittest.main()
