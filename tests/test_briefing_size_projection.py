"""Adversarial UTF-8 transport tests for bounded dispatch projections."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "cortex" / "scripts"))

import cortex as control
from cortex_runtime.briefings import TARGET_V3_BRIEFING_BYTES


class BriefingSizeProjectionTests(unittest.TestCase):
    def test_unicode_heavy_dispatch_is_bounded_and_points_to_full_task_contract(self) -> None:
        huge = "Ж界" * 10_000
        package = {
            "task_id": "task-size", "task_ref": "task-size", "attempt_id": "attempt-size",
            "gate": "implementation", "profile": "backend_dev", "project_root": "/tmp/project",
            "objective": huge, "selection_reason": huge, "strategy": huge,
            "task_requirements": [huge] * 40, "task_constraints": [huge] * 40,
            "task_scope": [huge] * 40, "task_acceptance_criteria": [huge] * 40,
            "task_verification": [huge] * 40, "allowed_paths": ["src"] * 80,
            "acceptance_criteria": [huge] * 40, "verification": [huge] * 40,
            "user_intent": {
                "projection": huge, "artifact_ref": "artifact-intent", "artifact_path": "/tmp/intent",
                "digest_sha256": "b" * 64, "byte_size": len(huge.encode("utf-8")),
            },
            "task_contract": {
                "schema": "cortex/task-contract-ref/v1", "artifact_ref": "artifact-contract",
                "artifact_path": "/tmp/task-contract", "digest_sha256": "c" * 64,
                "byte_size": len(huge.encode("utf-8")), "read_required": True,
            },
            "predecessor_results": [], "resolved_user_decisions": [], "mode": "ordinary",
        }
        prompt = control.host_spawn_prompt("backend_dev", package)
        self.assertLessEqual(len(prompt.encode("utf-8")), TARGET_V3_BRIEFING_BYTES)
        self.assertIn("artifact-contract", prompt)
        self.assertIn("task-contract", prompt)
        self.assertNotIn(huge, prompt)

    def test_actual_remaining_utf8_budget_keeps_complete_fitting_item(self) -> None:
        fitting = "α" * 900
        omitted = "界" * 5_000
        package = {
            "task_id": "task-admission", "task_ref": "task-admission", "attempt_id": "attempt-admission",
            "gate": "implementation", "profile": "backend_dev", "project_root": "/tmp/project",
            "objective": "Bounded UTF-8 admission", "selection_reason": "size test", "strategy": "default",
            "task_requirements": [fitting, omitted], "task_constraints": [], "task_scope": ["src"],
            "task_acceptance_criteria": [], "task_verification": [], "allowed_paths": ["src"],
            "acceptance_criteria": [], "verification": [],
            "user_intent": {"projection": "small request", "artifact_ref": "artifact-intent", "artifact_path": "/tmp/intent", "digest_sha256": "b" * 64, "byte_size": 13},
            "task_contract": {"schema": "cortex/task-contract-ref/v1", "artifact_ref": "artifact-contract", "artifact_path": "/tmp/task-contract", "digest_sha256": "c" * 64, "byte_size": len((fitting + omitted).encode("utf-8")), "read_required": True},
            "predecessor_results": [], "resolved_user_decisions": [], "mode": "ordinary",
        }
        prompt = control.host_spawn_prompt("backend_dev", package)
        self.assertLessEqual(len(prompt.encode("utf-8")), TARGET_V3_BRIEFING_BYTES)
        self.assertIn(fitting, prompt)
        self.assertNotIn(omitted, prompt)
        self.assertIn("artifact-contract", prompt)


if __name__ == "__main__":
    unittest.main()
