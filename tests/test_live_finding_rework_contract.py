"""Contract checks for the bounded real-host finding rework smoke scenario."""
from __future__ import annotations

import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
EVALUATOR_PATH = ROOT / "scripts" / "cortex-luna-high-eval.py"
sys.path.insert(0, str(ROOT / "plugins" / "cortex" / "scripts"))
SPEC = importlib.util.spec_from_file_location("cortex_luna_high_eval", EVALUATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
EVALUATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATOR)


class LiveFindingReworkContractTests(unittest.TestCase):
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
            "finding_rework_documentation", Path("/tmp/cortex-finding-smoke"),
        )
        self.assertIn(EVALUATOR.FINDING_REWORK_FINGERPRINT, prompt)
        self.assertIn("fresh review rerun", prompt)
        self.assertEqual(EVALUATOR.FINDING_REWORK_LIVE_TIMEOUT_SECONDS, 300)


if __name__ == "__main__":
    unittest.main()
