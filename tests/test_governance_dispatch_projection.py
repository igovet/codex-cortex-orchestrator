"""Regression coverage for governed ordinary-gate dispatch context."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "cortex" / "scripts"))

import cortex  # noqa: E402,F401
from cortex_runtime import briefings, canonical_json, delegation_service  # noqa: E402


class GovernanceDispatchProjectionTests(unittest.TestCase):
    def _package(self, governance_context: dict[str, object]) -> dict[str, object]:
        return {
            "schema": "cortex/v3",
            "task_id": "task-plan-05",
            "task_ref": "task-ref-plan-05",
            "attempt_id": "plan-05",
            "dispatch_ref": "dispatch-plan-05",
            "gate": "plan",
            "profile": "planner",
            "project_root": "/workspace/plan-05",
            "objective": "Produce the approved governed plan.",
            "selection_reason": "planner owns the plan gate",
            "strategy": "default",
            "task_user_request": "Produce the approved governed plan.",
            "task_requirements": ["Preserve the server-owned governance basis."],
            "task_constraints": [],
            "task_scope": ["."],
            "task_acceptance_criteria": ["The plan is decision-complete."],
            "task_verification": ["Inspect the plan against the task contract."],
            "acceptance_criteria": ["The plan is decision-complete."],
            "verification": ["Inspect the plan against the task contract."],
            "allowed_paths": ["."],
            "context_files": [],
            "knowledge_index_files": [],
            "context_result_refs": [],
            "predecessor_results": [],
            "resolved_user_decisions": [],
            "pause_conditions": [],
            "intent_clarification_required": False,
            "intent_clarification_reason": None,
            "mode": "ordinary",
            "facade_managed": True,
            "user_owned_thread": False,
            "governance_context": governance_context,
            "user_intent": {
                "projection": "Produce the approved governed plan.",
                "artifact_ref": "artifact-intent",
                "artifact_path": "/workspace/plan-05/intent.txt",
                "digest_sha256": "a" * 64,
                "byte_size": 34,
            },
            "task_contract": {
                "schema": "cortex/task-contract-ref/v1",
                "artifact_ref": "artifact-contract",
                "artifact_path": "/workspace/plan-05/task-contract.json",
                "digest_sha256": "b" * 64,
                "byte_size": 512,
                "read_required": True,
            },
        }

    def _complete_governance(self) -> dict[str, object]:
        return {
            "schema": "cortex/governance/v1",
            "requested_mode": "auto",
            "effective_mode": "full",
            "complexity": "C3",
            "policy_snapshot": {
                "schema": "cortex/governance-policy/v1",
                "required_floor": "full",
                "promotion_window_days": 90,
            },
            "policy_snapshot_digest": canonical_json.digest({
                "schema": "cortex/governance-policy/v1",
                "required_floor": "full",
                "promotion_window_days": 90,
            }),
            "manifest_ref": "manifest-plan-05",
            "manifest_digest": "d" * 64,
            "current_pipeline": ["governance_activation", "plan", "implementation", "qa", "governance_close", "close"],
        }

    def test_plan_05_existing_governance_projection_is_decision_complete(self) -> None:
        package = self._package(self._complete_governance())
        prompt = briefings.host_spawn_prompt("planner", package)
        self.assertIn("SERVER-OWNED GOVERNANCE PROJECTION", prompt)
        self.assertIn("do not ask the user to choose or reconfirm them", prompt)
        self.assertNotIn("SERVER-OWNED GOVERNANCE PROJECTION IS INCOMPLETE", prompt)
        assignment = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(assignment["governance_context"]["manifest_ref"], "manifest-plan-05")
        self.assertEqual(
            assignment["governance_context"]["policy_snapshot_digest"],
            canonical_json.digest(assignment["governance_context"]["policy_snapshot"]),
        )

    def test_plan_05_missing_policy_snapshot_requires_a_durable_question(self) -> None:
        governance = self._complete_governance()
        governance.pop("policy_snapshot")
        prompt = briefings.host_spawn_prompt("planner", self._package(governance))
        self.assertIn("SERVER-OWNED GOVERNANCE PROJECTION IS INCOMPLETE", prompt)
        self.assertIn("record one durable worker_question", prompt)
        self.assertIn("Do not invent or infer the missing server fact", prompt)

    def test_every_governed_gate_carries_the_same_server_projection(self) -> None:
        """Ordinary and review gates must not lose governance between waves."""
        governed_gates = (
            "discover", "plan", "implementation", "qa", "review",
            "documentation", "governance_activation", "governance_close", "close",
        )
        governance = self._complete_governance()
        for gate in governed_gates:
            package = self._package(governance)
            package["gate"] = gate
            package["attempt_id"] = f"{gate}-05"
            prompt = briefings.host_spawn_prompt(
                "code_reviewer" if gate in {"review", "governance_activation", "governance_close"}
                else "general",
                package,
            )
            self.assertIn("SERVER-OWNED GOVERNANCE PROJECTION", prompt, gate)
            self.assertNotIn("SERVER-OWNED GOVERNANCE PROJECTION IS INCOMPLETE", prompt, gate)
            assignment = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
            projection = assignment["governance_context"]
            self.assertEqual(projection["effective_mode"], "full", gate)
            self.assertEqual(projection["manifest_ref"], "manifest-plan-05", gate)
            self.assertEqual(projection["manifest_digest"], "d" * 64, gate)
            self.assertEqual(
                projection["policy_snapshot_digest"],
                canonical_json.digest(projection["policy_snapshot"]),
                gate,
            )
            self.assertEqual(projection["current_pipeline"][-2:], ["governance_close", "close"], gate)

    def test_dispatch_projection_is_present_for_required_modes_only(self) -> None:
        state = {
            "complexity": "C2",
            "current_pipeline": ["plan", "implementation", "close"],
            "governance": {
                "effective_mode": "light",
                "policy_snapshot": {"required_floor": "full"},
                "policy_snapshot_digest": canonical_json.digest({"required_floor": "full"}),
            },
        }
        task = {"governance": state["governance"]}
        projection = delegation_service._governance_dispatch_projection(
            task, state, manifest_ref="manifest-light", manifest_digest="f" * 64,
        )
        self.assertIsNotNone(projection)
        self.assertEqual(projection["effective_mode"], "light")
        self.assertEqual(projection["current_pipeline"], state["current_pipeline"])
        self.assertEqual(projection["manifest_ref"], "manifest-light")
        minimal = delegation_service._governance_dispatch_projection(
            {"governance": {"effective_mode": "minimal"}}, state,
            manifest_ref="manifest-minimal", manifest_digest="0" * 64,
        )
        self.assertIsNone(minimal)

    def test_dispatch_projection_rejects_digest_for_different_json_scalar_types(self) -> None:
        governance = self._complete_governance()
        governance["policy_snapshot"]["promotion_window_days"] = "90"
        with pytest.raises(ValueError, match="policy_snapshot_digest"):
            delegation_service._governance_dispatch_projection(
                {"governance": governance},
                {"governance": governance, "current_pipeline": ["plan"]},
                manifest_ref="manifest-plan-05",
                manifest_digest="d" * 64,
            )


if __name__ == "__main__":
    unittest.main()
