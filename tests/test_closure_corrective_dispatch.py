"""Focused server-owned recovery tests for unresolved closure results."""
from __future__ import annotations

import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex  # noqa: F401  # composition root binds runtime dependencies
from cortex_runtime import orchestration_engine


class ClosureCorrectiveDispatchTests(unittest.TestCase):
    def test_advance_returns_ready_to_spawn_typed_closure_correction(self) -> None:
        state = {
            "task_id": "task-close",
            "status": "active",
            "revision": 3,
            "task_revision": 3,
            "attempts": [{
                "attempt_id": "close-01",
                "gate": "close",
                "agent": "code_reviewer",
                "status": "passed",
                "invalidated": False,
                "attempt_result_ref": "attempt-result-close-01",
            }],
            "completed_gates": [],
            "skipped_gates": [],
            "current_pipeline": ["implementation", "close"],
        }
        plan = {"waves": [{
            "wave_id": "wave-close",
            "gates": ["close"],
            "attempt_ids": ["close-01"],
            "delegations": [{"gate": "close", "agent": "code_reviewer"}],
        }]}
        prepared = {
            "state": {**state, "status": "active", "attempts": [{
                **state["attempts"][0], "invalidated": True,
            }, {
                "attempt_id": "implementation-02", "gate": "implementation",
                "status": "awaiting_host_spawn", "invalidated": False,
            }]},
            "wave_id": "wave-implementation",
            "spawn_requests": [{
                "task_id": "task-close", "attempt_id": "implementation-02",
                "dispatch_ref": "dispatch-implementation-02",
            }],
        }
        request = {
            "operation": "advance", "submission_id": "advance-close-01",
            "project_root": "/project", "principal": "thread-a",
            "task_id": "task-close", "wave_id": "wave-close",
            "completions": [{
                "attempt_id": "close-01", "status": "passed",
                "attempt_result_ref": "attempt-result-close-01",
            }],
        }
        recovery = {
            "recorded": False,
            "reason": "closure_attempt_unresolved",
            "candidate_attempt_ids": ["close-01"],
        }
        response = {
            "ok": True, "state": "ready_to_spawn", "spawn_requests": prepared["spawn_requests"],
            "result": {"corrective_dispatch": {"schema": "cortex/corrective-dispatch/v1"}},
        }
        with (
            mock.patch.object(orchestration_engine, "ledger_root", return_value=Path("/ledger")),
            mock.patch.object(orchestration_engine, "state_lock", return_value=nullcontext()),
            mock.patch.object(orchestration_engine, "load_state", return_value=(Path("/ledger"), Path("/ledger/tasks/task-close"), state)),
            mock.patch.object(orchestration_engine, "authorize"),
            mock.patch.object(orchestration_engine, "_load_orchestrate_plan", return_value=plan),
            mock.patch.object(orchestration_engine, "load_task_definition", return_value={"user_request": "close"}),
            mock.patch.object(orchestration_engine, "_preflight_dispatch_context"),
            mock.patch.object(orchestration_engine, "_wave_for_gates", return_value=plan["waves"][0]),
            mock.patch.object(orchestration_engine, "_preflight_orchestrate_completion"),
            mock.patch.object(orchestration_engine, "_complete_orchestrate_attempt", return_value=(state, None)),
            mock.patch.object(orchestration_engine, "_ensure_attempt_evidence", return_value=state),
            mock.patch.object(orchestration_engine, "_checkpoint_orchestrate_transaction"),
            mock.patch.object(orchestration_engine, "db_list_task_findings", return_value=[]),
            mock.patch.object(orchestration_engine, "record_gate", return_value=recovery),
            mock.patch.object(orchestration_engine, "_closure_unresolved_corrective_findings", return_value=([{"fingerprint": "f1"}], ["attempt-result-close-01"])),
            mock.patch.object(orchestration_engine, "_activate_closure_rework", return_value="implementation"),
            mock.patch.object(orchestration_engine, "_prepare_orchestrate_wave", return_value=prepared),
            mock.patch.object(orchestration_engine, "save_state"),
            mock.patch.object(orchestration_engine, "_orchestrate_response", return_value=response),
        ):
            result = orchestration_engine._orchestrate_advance(request, Path("/transaction"), {"phase": "started"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "ready_to_spawn")
        self.assertEqual(result["spawn_requests"][0]["task_id"], "task-close")
        self.assertEqual(result["result"]["corrective_dispatch"]["schema"], "cortex/corrective-dispatch/v1")
        self.assertIn("orchestrate(operation=advance)", result["next_action"])
        self.assertNotIn("resume", result["next_action"])
        self.assertNotIn("future_waves", result["next_action"])

    def test_unresolved_items_become_stable_blocking_findings(self) -> None:
        state = {
            "task_id": "task-close",
            "task_revision": 4,
            "attempts": [{
                "attempt_id": "close-01",
                "gate": "close",
                "status": "passed",
                "attempt_result_ref": "attempt-result-close-01",
            }],
        }
        canonical = {
            "result_ref": "attempt-result-close-01",
            "lifecycle_status": "COMPLETED",
            "unresolved": [{
                "summary": "The close verifier found an uncovered requirement.",
                "details": {"affected_paths": ["src/service.py"]},
            }],
        }
        with mock.patch.object(
            orchestration_engine.attempt_protocol,
            "get_attempt_result",
            return_value=canonical,
        ), mock.patch.object(orchestration_engine, "db_upsert_task_finding") as upsert:
            findings, refs = orchestration_engine._closure_unresolved_corrective_findings(
                Path("/ledger"), state, ["close-01"],
            )

        self.assertEqual(refs, ["attempt-result-close-01"])
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0]["blocking"])
        self.assertEqual(findings[0]["severity"], "P1")
        self.assertTrue(findings[0]["fingerprint"].startswith("closure-unresolved-"))
        upsert.assert_called_once()
        source = upsert.call_args.kwargs["source"]
        self.assertEqual(source["transition"], "opened")
        self.assertEqual(source["origin_result_ref"], "attempt-result-close-01")

    def test_passed_corrective_result_records_origin_bound_receipts(self) -> None:
        state = {
            "task_id": "task-close",
            "task_revision": 4,
            "closure_rework": {
                "close": {
                    "status": "rework_required",
                    "target_gate": "implementation",
                    "task_revision": 4,
                    "finding_fingerprints": ["closure-unresolved-finding"],
                    "source_result_refs": ["attempt-result-close-01"],
                },
            },
        }
        finding = {
            "task_id": "task-close",
            "fingerprint": "closure-unresolved-finding",
            "severity": "P1",
            "status": "open",
            "blocking": True,
            "summary": "Unresolved close item",
            "details": {},
        }
        attempt = {"attempt_id": "implementation-02", "gate": "implementation"}
        with mock.patch.object(
            orchestration_engine,
            "db_list_task_findings",
            return_value=[finding],
        ), mock.patch.object(orchestration_engine, "db_upsert_task_finding") as upsert:
            orchestration_engine._record_server_corrective_receipts(
                Path("/ledger"), state, attempt, "attempt-result-implementation-02",
            )

        upsert.assert_called_once()
        source = upsert.call_args.kwargs["source"]
        self.assertEqual(source["transition"], "corrective_reported")
        self.assertEqual(source["origin_gate"], "close")
        self.assertEqual(source["origin_result_ref"], "attempt-result-close-01")
        self.assertEqual(source["attempt_result_ref"], "attempt-result-implementation-02")


if __name__ == "__main__":
    unittest.main()
