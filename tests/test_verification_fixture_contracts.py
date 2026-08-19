"""Focused coverage for release-fixture closure and lazy-artifact checks."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RUNTIME = ROOT / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(RUNTIME))

import cortex  # noqa: E402


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COLD_BOOT = load_script("cortex_cold_boot_smoke_fixture", "cortex-cold-boot-smoke.py")
LUNA_EVAL = load_script("cortex_luna_high_eval_fixture", "cortex-luna-high-eval.py")


class VerificationFixtureContractTests(unittest.TestCase):
    def test_fixture_closures_are_valid_and_have_no_open_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            (project / "changed.txt").write_text("fixture\n", encoding="utf-8")

            for factory in (COLD_BOOT.passing_gate_result, LUNA_EVAL.passing_closure):
                closure = factory(project, "close")
                self.assertEqual(closure["decision"], "pass")
                self.assertEqual(closure["findings"], [])
                self.assertEqual(closure["verification"]["required_missing"], [])
                self.assertEqual(closure["workspace"]["untracked"], ["changed.txt"])
                closure_compatible = {
                    key: value for key, value in closure.items() if key != "failure_class"
                }
                self.assertEqual(
                    cortex.sanitize_closure_payload(closure_compatible),
                    closure_compatible,
                )

    def test_deterministic_fixtures_complete_from_canonical_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cold_base = base / "cold-boot"
            luna_base = base / "luna"
            cold_base.mkdir()
            luna_base.mkdir()
            cold_boot = COLD_BOOT.run(cold_base)
            luna = LUNA_EVAL.fixture_eval(luna_base)

        self.assertEqual(cold_boot["status"], "PASS")
        self.assertTrue(cold_boot["dynamic_replan_applied"])
        self.assertGreaterEqual(cold_boot["dynamic_replan_count"], 3)
        self.assertGreater(cold_boot["replan_count"], cold_boot["legacy_replan_limit"])
        self.assertTrue(cold_boot["pending_implementation_drop_rejected"])
        self.assertTrue(cold_boot["implementation_phase_seen"])
        self.assertTrue(luna)
        self.assertTrue(all(item["outcome"] == "completed" for item in luna))

    def test_auto_c3_governance_completes_through_public_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "automatic-governance"
            project.mkdir()
            (project / "README.md").write_text("# governance fixture\n", encoding="utf-8")
            started = cortex.start_orchestration(
                {
                    "project_root": str(project),
                    "task": {
                        "user_request": "Complete a high-impact cross-system governance fixture.",
                        "complexity": "C3",
                        "acceptance_criteria": ["The governed fixture completes."],
                        "verification": ["Verify the governed lifecycle."],
                        "plan_approval": "auto",
                    },
                    "waves": [
                        {"workers": [{"phase": "implementation"}]},
                        {"workers": [{"phase": "documentation"}]},
                        {"workers": [{"phase": "close"}]},
                    ],
                }
            )
            completed = LUNA_EVAL.finish(project, started)
            ledger = cortex.ledger_root({"project_root": str(project)})
            registry = cortex._operation_registry(ledger)
            task_id = next(iter(registry["tasks"]))
            _task_dir, state, task = cortex._v3_task_state(ledger, task_id)

        self.assertEqual(started["requested_mode"], "auto")
        self.assertEqual(started["effective_mode"], "full")
        self.assertEqual(started["step"], 1)
        self.assertEqual(completed["outcome"], "completed")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(task["governance"]["reasons"], ["complexity:C3"])
        self.assertEqual(
            state["completed_gates"],
            [
                "governance_activation",
                "implementation",
                "documentation",
                "governance_close",
                "close",
            ],
        )
        governance_attempts = {
            item["gate"]: item
            for item in state["attempts"]
            if item.get("gate") in {"governance_activation", "governance_close"}
        }
        self.assertEqual(set(governance_attempts), {"governance_activation", "governance_close"})
        self.assertTrue(all(item["agent"] == "code_reviewer" for item in governance_attempts.values()))
        self.assertTrue(all(item["status"] == "passed" for item in governance_attempts.values()))
        governance_evidence = [
            item
            for item in state["evidence"]
            if item.get("gate") in {"governance_activation", "governance_close"}
        ]
        self.assertEqual(len(governance_evidence), 2)
        self.assertTrue(all(item["artifact_immutable"] for item in governance_evidence))
        self.assertTrue(all(item["artifact_verified"] for item in governance_evidence))
        self.assertTrue(all(item["verified_execution"] for item in governance_evidence))
        self.assertTrue(state["handoff_created"])

    def test_live_prompt_uses_the_ordered_profile_report_contract(self) -> None:
        fields = list(cortex.REPORT_FIELDS)
        prompt = LUNA_EVAL.live_prompt("automatic_sequential", Path("/workspace/cortex-live"))
        self.assertIn(
            f"exactly {len(fields)} report fields: {', '.join(fields)}",
            prompt,
        )
        self.assertIn("exactly one canonical top-level gate_result", prompt)
        self.assertIn("Do not add the legacy closure alias", prompt)
        self.assertIn("For every review, governance review, or close dispatch", prompt)
        self.assertIn(f"strict {len(fields)}-key report", prompt)
        self.assertIn("Treat a native child as successful only", prompt)
        self.assertIn("status=failed, the exact dispatch_ref", prompt)
        self.assertIn("never submit an empty result or a reportless success", prompt)
        self.assertIn("close the completed native child with close_agent", prompt)
        self.assertIn("Before every new spawn, FIRST close every known leftover completed child", prompt)
        self.assertIn("use list_agents defensively", prompt)
        self.assertIn('"complexity":"C2"', prompt)
        self.assertIn("Verified note: README heading is Luna high Cortex fixture.", prompt)

    def test_blocked_resume_live_prompt_forces_one_valid_future_wave_reassessment(self) -> None:
        prompt = LUNA_EVAL.live_prompt("blocked_resume", Path("/workspace/cortex-live"))
        self.assertIn("deterministic future-wave reassessment", prompt)
        self.assertIn('"complexity":"C2"', prompt)
        self.assertIn('these exact initial waves: [{"workers":[{"phase":"discover"}]}', prompt)
        self.assertIn(
            'future_waves exactly [{"workers":[{"phase":"implementation"}]}',
            prompt,
        )
        self.assertIn(
            "Discovery confirms result.md must be created, so add implementation before documentation.",
            prompt,
        )
        self.assertIn("Do not set rework", prompt)

    def test_planner_live_prompt_uses_exact_schema_safe_work_breakdown(self) -> None:
        prompt = LUNA_EVAL.live_prompt("planner_work_breakdown", Path("/workspace/cortex-live"))
        self.assertIn('"plan_approval":"required"', prompt)
        self.assertIn('waves exactly [{"workers":[{"phase":"discover"}]}', prompt)
        self.assertIn('{"workers":[{"phase":"plan"}]}', prompt)
        self.assertIn('{"workers":[{"phase":"implementation"}]}', prompt)
        self.assertIn('"id":"inspect_source"', prompt)
        self.assertIn('"id":"deliver_result"', prompt)
        self.assertIn('"depends_on":["inspect_source"]', prompt)
        self.assertIn("Do not add, remove, rename, or reorder packages or microtasks", prompt)
        self.assertIn("decision=prompt", prompt)
        self.assertIn("embedded Approve action arguments", prompt)
        self.assertIn("Only after it returns outcome=awaiting_plan_approval", prompt)
        self.assertIn("never call approval before that continue", prompt)

    def test_automatic_governance_live_prompt_does_not_force_governance(self) -> None:
        prompt = LUNA_EVAL.live_prompt(
            "automatic_governance", Path("/workspace/cortex-live")
        )
        contract = prompt.split("<cortex_task_contract>", 1)[1].split(
            "</cortex_task_contract>", 1
        )[0]
        self.assertIn('"complexity":"C3"', contract)
        self.assertIn("result.txt", contract)
        self.assertNotIn("result.md", contract)
        self.assertNotIn("governance_mode", contract)
        self.assertNotIn("governance_triggers", contract)
        self.assertNotIn("risk_triggers", contract)
        self.assertIn("server default auto mode must resolve solely from C3 complexity", prompt)
        self.assertIn("requested_mode=auto", prompt)
        self.assertIn("effective_mode=full", prompt)
        self.assertIn("governance_activation is first", prompt)
        self.assertIn("governance_close is immediately before close", prompt)
        self.assertIn("Do not call manage_governance", prompt)
        self.assertIn("do not call manage_orchestration", prompt)
        self.assertIn("strict state machine for all five sequential server waves", prompt)
        self.assertIn("the only legal next tool call is every returned dispatch.call", prompt)
        self.assertIn("A native wait is legal only immediately after a successful native dispatch", prompt)

    def test_targeted_live_prompts_carry_complete_start_and_follow_up_contracts(self) -> None:
        compact = LUNA_EVAL.live_prompt("compact_parallel", Path("/workspace/cortex-live"))
        self.assertIn('<cortex_task_contract>{"user_request":', compact)
        self.assertIn('"complexity":"C1"', compact)
        self.assertIn('"plan_approval":"auto"', compact)
        self.assertIn('these exact waves: [{"workers":[{"phase":"discover"', compact)
        self.assertEqual(compact.count('"phase":"discover"'), 2)

        follow_up = LUNA_EVAL.live_prompt(
            "follow_up_partial", Path("/workspace/cortex-live"), "task-source-ref",
        )
        self.assertIn('payload exactly {"user_request":', follow_up)
        self.assertIn('"acceptance_criteria"', follow_up)
        self.assertIn('"verification"', follow_up)
        self.assertIn('"plan_approval":"auto"', follow_up)


if __name__ == "__main__":
    unittest.main()
