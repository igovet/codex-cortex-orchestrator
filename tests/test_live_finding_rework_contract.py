"""Contract checks for the bounded real-host finding rework smoke scenario."""
from __future__ import annotations

import copy
import contextlib
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


ROOT = Path(__file__).parents[1]
EVALUATOR_PATH = ROOT / "scripts" / "cortex-luna-high-eval.py"
sys.path.insert(0, str(ROOT / "plugins" / "cortex" / "scripts"))
SPEC = importlib.util.spec_from_file_location("cortex_luna_high_eval", EVALUATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
EVALUATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATOR)


class LiveFindingReworkContractTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.set_up_host_private_control_store()

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()

    def test_trace_checks_require_exact_directional_handoff(self) -> None:
        fingerprint = EVALUATOR.FINDING_REWORK_FINGERPRINT
        opening_ref = "report-0001"
        correction_ref = "report-0002"
        state = {
            "closure_rework": {
                "review": {
                    "status": "resolved",
                    "target_gate": "documentation",
                    "finding_fingerprints": [fingerprint],
                },
            },
            "attempts": [
                {"attempt_id": "review-origin", "gate": "review", "status": "passed", "invalidated": True, "report_ids": [opening_ref]},
                {"attempt_id": "documentation-correction", "gate": "documentation", "status": "passed", "invalidated": False, "context_report_ids": [opening_ref], "report_ids": [correction_ref]},
                {"attempt_id": "review-rerun", "gate": "review", "status": "passed", "invalidated": False, "context_report_ids": [opening_ref, correction_ref], "report_ids": ["report-0003"]},
            ],
        }
        reports = [
            {"report_id": opening_ref, "attempt_id": "review-origin", "gate": "review", "gate_result": {"decision": "rework", "findings": [{"fingerprint": fingerprint, "status": "open", "blocking": True}]}},
            {"report_id": correction_ref, "attempt_id": "documentation-correction", "gate": "documentation", "report": {"changed_files": [EVALUATOR.FINDING_REWORK_DOCUMENTATION_PATH]}},
            {"report_id": "report-0003", "attempt_id": "review-rerun", "gate": "review", "gate_result": {"decision": "pass", "findings": [{"fingerprint": fingerprint, "status": "resolved", "blocking": False}]}},
        ]
        findings = [{
            "fingerprint": fingerprint,
            "status": "resolved",
            "source_evidence": [
                {"transition": "opened", "report_id": opening_ref},
                {
                    "transition": "resolved",
                    "origin_report_ref": opening_ref,
                    "correction_report_refs": [correction_ref],
                },
            ],
        }]

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            target = project / EVALUATOR.FINDING_REWORK_DOCUMENTATION_PATH
            target.parent.mkdir()
            target.write_text(EVALUATOR.FINDING_REWORK_DOCUMENTATION_CONTENT, encoding="utf-8")
            checks = EVALUATOR.finding_rework_trace_checks(
                state, reports, findings=findings, project=project,
            )
            self.assertTrue(all(checks.values()))

            wrong_handoff = copy.deepcopy(state)
            wrong_handoff["attempts"][2]["context_report_ids"] = [opening_ref]
            self.assertFalse(EVALUATOR.finding_rework_trace_checks(
                wrong_handoff, reports, findings=findings, project=project,
            )["fresh_review_received_correction"])

            target.write_text("wrong content\n", encoding="utf-8")
            self.assertFalse(EVALUATOR.finding_rework_trace_checks(
                state, reports, findings=findings, project=project,
            )["documentation_content_exact"])

    def test_live_prompt_and_timeout_are_intentionally_narrow(self) -> None:
        prompt = EVALUATOR.live_prompt(
            "finding_rework_documentation", Path("/tmp/cortex-finding-smoke"), "task-123",
        )
        self.assertIn(EVALUATOR.FINDING_REWORK_FINGERPRINT, prompt)
        self.assertIn("fresh review rerun", prompt)
        self.assertIn("do not start a task", prompt)
        self.assertIn("STOP there", prompt)
        self.assertNotIn("Call start_orchestration exactly once", prompt)
        self.assertEqual(EVALUATOR.FINDING_REWORK_LIVE_TIMEOUT_SECONDS, 300)

    def test_full_live_prompt_uses_a_real_c2_opening_and_close(self) -> None:
        prompt = EVALUATOR.live_prompt(
            "finding_rework_documentation_full", Path("/tmp/cortex-finding-full"),
        )
        self.assertIn('"complexity":"C2"', prompt)
        self.assertIn("Your FIRST action MUST be one Cortex MCP tool call: start_orchestration", prompt)
        self.assertIn("project_root=/tmp/cortex-finding-full; task=<cortex_task_contract>; waves=<cortex_initial_waves>", prompt)
        self.assertIn("Only after start_orchestration returns ready_to_spawn", prompt)
        self.assertIn("Call start_orchestration exactly once", prompt)
        self.assertIn("Open exactly one P2 finding", prompt)
        self.assertIn("decision=rework (never fail or blocked)", prompt)
        self.assertIn("spawn_agent -> wait -> read_worker_report -> close_agent", prompt)
        self.assertIn("trusted host binding is intentionally unavailable", prompt)
        self.assertNotIn("confirm_host_spawn", prompt)
        self.assertIn("required_missing=[]", prompt)
        self.assertIn("workspace has exactly modified/untracked/staged/committed", prompt)
        self.assertIn("Close must publish canonical gate_result decision=pass with findings=[]", prompt)
        self.assertIn("outcome=completed", prompt)
        self.assertNotIn("already-created", prompt)
        self.assertEqual(EVALUATOR.FINDING_REWORK_FULL_LIVE_TIMEOUT_SECONDS, 1800)

    def test_full_live_timeout_is_hard_and_does_not_widen_narrow_smoke(self) -> None:
        rejected = subprocess.run(
            [
                sys.executable, "-B", str(EVALUATOR_PATH), "--live",
                "--scenario", "finding_rework_documentation_full",
                "--live-timeout-seconds", "1801",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("hard 1800-second limit", rejected.stderr)
        self.assertIn("finding_rework_documentation_full", EVALUATOR.FINDING_REWORK_LIVE_SCENARIOS)
        self.assertIn("finding_rework_documentation", EVALUATOR.FINDING_REWORK_LIVE_SCENARIOS)

    def test_full_gate_result_contract_rejects_extra_open_or_missing_check(self) -> None:
        fingerprint = EVALUATOR.FINDING_REWORK_FINGERPRINT
        opening = {
            "gate": "review",
            "gate_result": {
                "decision": "rework", "failure_class": "product",
                "findings": [{
                    "fingerprint": fingerprint, "severity": "P2", "status": "open",
                    "blocking": True,
                }],
                "verification": {
                    "executed": [], "not_executed": [], "required_missing": [], "limitations": [],
                },
                "workspace": {
                    "modified": [], "untracked": [], "staged": [], "committed": "not_required",
                },
            },
        }
        fresh_review = {
            "gate": "review",
            "gate_result": {
                "decision": "pass", "failure_class": "product",
                "findings": [{
                    "fingerprint": fingerprint, "severity": "P2", "status": "resolved",
                    "blocking": False,
                }],
                "verification": {
                    "executed": [], "not_executed": [], "required_missing": [], "limitations": [],
                },
                "workspace": {
                    "modified": [], "untracked": [], "staged": [], "committed": "not_required",
                },
            },
        }
        close = {
            "gate": "close",
            "gate_result": {
                "decision": "pass", "failure_class": "product", "findings": [],
                "verification": {
                    "executed": [], "not_executed": [], "required_missing": [], "limitations": [],
                },
                "workspace": {
                    "modified": [], "untracked": [], "staged": [], "committed": "not_required",
                },
            },
        }
        records = [opening, fresh_review, close]
        self.assertTrue(EVALUATOR.full_finding_rework_gate_results_valid(records))

        extra_finding = copy.deepcopy(records)
        extra_finding[0]["gate_result"]["findings"].append({
            "fingerprint": "verification-required-missing", "severity": "P1",
            "status": "open", "blocking": True,
        })
        self.assertFalse(EVALUATOR.full_finding_rework_gate_results_valid(extra_finding))

        missing_check = copy.deepcopy(records)
        missing_check[0]["gate_result"]["verification"]["required_missing"] = ["fixture check"]
        self.assertFalse(EVALUATOR.full_finding_rework_gate_results_valid(missing_check))

    def test_source_mode_native_lifecycle_requires_ordered_recorded_cycles(self) -> None:
        one_cycle = [
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "report_recorded"},
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
        ]
        duplicated = [item.copy() for item in one_cycle for _ in range(2)]
        self.assertTrue(EVALUATOR.observed_native_lifecycle(duplicated * 4))

        wrong = [item.copy() for item in duplicated * 4]
        wrong[2]["outcome"] = "question_recorded"
        self.assertFalse(EVALUATOR.observed_native_lifecycle(wrong))

    def test_live_eval_returns_fail_without_task_state(self) -> None:
        streamed = {
            "events": [], "returncode": 0, "elapsed_seconds": 1,
            "termination": None, "dropped_stream_events": 0,
        }
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(EVALUATOR.shutil, "which", return_value="/usr/bin/codex"), \
             mock.patch.object(EVALUATOR, "isolated_codex_runtime", return_value=contextlib.nullcontext({})), \
             mock.patch.object(EVALUATOR, "run_live_command", return_value=streamed):
            result = EVALUATOR.live_eval(
                Path(directory), ("finding_rework_documentation_full",), timeout_seconds=10,
            )

        self.assertEqual(result[0]["status"], "FAIL")
        self.assertFalse(result[0]["checks"]["task_state_available"])
        self.assertEqual(result[0]["state_diagnostics"]["status"], "unavailable")

    def test_source_prelude_prepares_corrective_documentation_without_a_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            (project / "README.md").write_text("# finding fixture\n", encoding="utf-8")

            seeded = EVALUATOR.seed_finding_rework_documentation(project)

            task_dirs = EVALUATOR.canonical_task_directories(project)
            self.assertEqual(len(task_dirs), 1)
            state = EVALUATOR.cortex.load_task_state_for_artifact(task_dirs[0])
            documentation = next(
                item for item in state["attempts"]
                if item["gate"] == "documentation" and not item.get("invalidated")
            )
            findings = EVALUATOR.cortex.db_list_task_findings(
                EVALUATOR.cortex.ledger_root({"project_root": str(project)}), state["task_id"],
            )
            self.assertTrue(seeded["task_ref"])
            self.assertIn(seeded["opening_report_ref"], documentation["context_report_ids"])
            self.assertEqual(
                [(item["fingerprint"], item["status"]) for item in findings],
                [(EVALUATOR.FINDING_REWORK_FINGERPRINT, "open")],
            )

    def test_live_supervisor_enforces_timeout_on_unterminated_host_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            result = EVALUATOR.run_live_command(
                [
                    sys.executable,
                    "-c",
                    "import sys, time; sys.stdout.write('{'); sys.stdout.flush(); time.sleep(30)",
                ],
                project,
                "unterminated-output-timeout",
                timeout_seconds=1,
            )

        self.assertEqual(result["termination"], "timeout")
        self.assertLess(result["elapsed_seconds"], 6)


if __name__ == "__main__":
    unittest.main()
