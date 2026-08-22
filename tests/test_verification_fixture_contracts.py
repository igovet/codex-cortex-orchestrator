"""Focused coverage for fresh release-fixture results and lazy-artifact checks."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RUNTIME = ROOT / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(RUNTIME))

import cortex  # noqa: E402
from cortex_runtime import prompt_compiler  # noqa: E402


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COLD_BOOT = load_script("cortex_cold_boot_smoke_fixture", "cortex-cold-boot-smoke.py")
LUNA_EVAL = load_script("cortex_luna_high_eval_fixture", "cortex-luna-high-eval.py")


class VerificationFixtureContractTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.set_up_host_private_control_store()

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()

    def test_fresh_contract_surface_contains_no_retired_result_transport_vocabulary(self) -> None:
        """Keep installable prompts, playbooks, and harnesses fresh-only."""
        retired = (
            "get_" + "rep" + "ort_template",
            "record_" + "rep" + "ort",
            "read_" + "worker_" + "rep" + "ort",
            "publish_" + "worker_" + "rep" + "ort",
            "rep" + "ort_" + "ref",
            "rep" + "ort_" + "markdown",
            "rep" + "ort_" + "bus",
            "context_" + "rep" + "ort_ids",
            "generated_" + "rep" + "ort_fields",
            "required_" + "rep" + "ort_fields",
            "worker" + "rep" + "ort",
            "rep" + "ort_" + "ids",
            "rep" + "ort_" + "id",
            "documentation_" + "rep" + "ort",
            "legacy-" + "v2",
            "run_prompt_" + "ab",
            "render_prompt_" + "ab",
            "rep" + "ort-first",
            "merge" + " patch",
            "seven" + "-field",
            "rep" + "ort-ready",
            "native_worker_stopped_without_" + "rep" + "ort",
            "gate" + "_result",
            "result" + "_envelope",
            "sanitize_" + "gate" + "_result_payload",
            "finding_rework_" + "documentation",
            "finding_rework_" + "documentation_full",
        )
        files = [
            ROOT / "plugins/cortex/profiles.json",
            ROOT / "plugins/cortex/prompt-contracts.json",
            ROOT / "plugins/cortex/hooks/hooks.json",
            ROOT / "plugins/cortex/scripts/cortex_hook.py",
            ROOT / "plugins/cortex/scripts/cortex_runtime/briefings.py",
            ROOT / "plugins/cortex/scripts/cortex_runtime/prompt_compiler.py",
            ROOT / "plugins/cortex/scripts/cortex_runtime/prompt_eval.py",
            ROOT / "plugins/cortex/scripts/cortex_runtime/prompt_live_eval.py",
            ROOT / "plugins/cortex/scripts/cortex.py",
            ROOT / "scripts/cortex-cold-boot-smoke.py",
            ROOT / "scripts/cortex-composite-benchmark.py",
            ROOT / "scripts/cortex-luna-high-eval.py",
            ROOT / "scripts/cortex-prompt-eval.py",
            ROOT / "scripts/cortex-prompt-lint.py",
            ROOT / "scripts/cortex-prompt-live-eval.py",
            ROOT / "scripts/validate-cortex-marketplace.py",
            ROOT / "README.md",
            ROOT / "tests/test_communication_live_smoke.py",
            ROOT / "tests/test_realtime_eval_harness.py",
        ]
        files.extend((ROOT / "plugins/cortex/agents").glob("*.toml"))
        files.extend((ROOT / "plugins/cortex/skills").rglob("*.md"))
        files.extend((ROOT / "docs/project").glob("*.md"))
        files.extend((ROOT / "docs/features").rglob("*.md"))
        for path in sorted(set(files)):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8").lower()
            found = [term for term in retired if term.lower() in text]
            self.assertEqual(found, [], f"retired vocabulary in {path}: {found}")

    def test_prompt_contract_requires_canonical_server_completion_audit(self) -> None:
        """A spawn/wait cannot be promoted to completion outside Cortex state."""
        contract_path = ROOT / "plugins/cortex/prompt-contracts.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        coordinator_completion = contract["attempt_result_contract"]["coordinator_completion"]
        self.assertIn("native spawn or wait is never completion evidence", coordinator_completion)
        self.assertIn("read_worker_result", coordinator_completion)
        self.assertIn("continue_orchestration", coordinator_completion)
        self.assertIn("successful server lifecycle outcome", coordinator_completion)
        self.assertEqual(prompt_compiler.load_prompt_contract(contract_path), contract)

        contract["attempt_result_contract"].pop("coordinator_completion")
        with tempfile.TemporaryDirectory() as temporary:
            invalid_path = Path(temporary) / "prompt-contracts.json"
            invalid_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "coordinator completion contract"):
                prompt_compiler.load_prompt_contract(invalid_path)

    def test_bundled_skills_require_server_audit_before_presentation_or_close(self) -> None:
        plugin = ROOT / "plugins/cortex"
        control = (plugin / "skills/cortex-control/SKILL.md").read_text(encoding="utf-8")
        orchestrator = (plugin / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        for marker in (
            "never completion evidence",
            "required server-derived\n   continuation/terminal audit",
            "do not\n   present completion",
            "Only then may the\n   coordinator present a final result to the user.",
        ):
            self.assertIn(marker, control)
        self.assertIn("server-derived `continue_orchestration` continuation/terminal audit", orchestrator)

    def test_fixture_attempt_results_are_complete_and_have_no_open_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            (project / "changed.txt").write_text("fixture\n", encoding="utf-8")

            for factory in (COLD_BOOT.passing_attempt_result, LUNA_EVAL.passing_attempt_result):
                attempt_result = factory(project, "close")
                self.assertEqual(attempt_result["status"], "completed")
                self.assertEqual(attempt_result["findings"], [])
                self.assertEqual(attempt_result["decisions_needed"], [])
                self.assertEqual(attempt_result["unresolved"], [])
                self.assertEqual(attempt_result["changed_files"], ["changed.txt"])

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
        self.assertGreaterEqual(cold_boot["replan_count"], cold_boot["dynamic_replan_count"])
        self.assertTrue(cold_boot["pending_implementation_drop_rejected"])
        self.assertTrue(cold_boot["implementation_phase_seen"])
        briefing_sizes = cold_boot["briefing_sizes"]
        self.assertTrue(briefing_sizes)
        self.assertEqual(cold_boot["briefing_size_target_bytes"], 14_500)
        self.assertEqual(cold_boot["briefing_size_max_bytes"], max(item["bytes"] for item in briefing_sizes))
        self.assertTrue(all(item["bytes"] <= 14_500 for item in briefing_sizes))
        self.assertEqual(len(briefing_sizes), cold_boot["worker_attempts"])
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
            # Exercise the same full-governance completion validator against
            # the server-projected evidence that the public lifecycle
            # produced.  This guards the boundary between automatic C3
            # governance evidence and the v10 ledger hardening contract.
            cortex.validate_governance_obligation_evidence(
                state,
                "governance_close",
                artifact_root=ledger,
            )
            records = LUNA_EVAL.canonical_attempt_result_records(ledger, state)

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
        self.assertEqual(len(records), len(state["attempts"]))
        self.assertTrue(LUNA_EVAL.canonical_results_are_strict(records))

    def test_live_prompt_uses_the_canonical_attempt_result_contract(self) -> None:
        prompt = LUNA_EVAL.live_prompt("automatic_sequential", Path("/workspace/cortex-live"))
        for field in ("summary", "findings", "decisions_needed", "unresolved"):
            self.assertIn(field, prompt)
        self.assertIn("AttemptResult", prompt)
        self.assertIn("server-provided continuation object", prompt)
        self.assertIn("copy its step and results verbatim", prompt)
        self.assertIn("For every review, governance review, or close dispatch", prompt)
        self.assertIn("Treat a native child as successful only", prompt)
        self.assertIn("status=failed, the exact dispatch_ref", prompt)
        self.assertIn("never submit an empty result or a resultless success", prompt)
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
        self.assertIn("sole question-only exception", prompt)
        self.assertIn("Deterministic decision policy authored for this fixture", prompt)
        self.assertIn("complete authorized facts and scope", prompt)
        self.assertIn("do not widen scope", prompt)
        self.assertIn("If the question requests any fact or decision outside that policy", prompt)
        self.assertIn("strict state machine for all five sequential server waves", prompt)
        self.assertIn(
            "read_worker_result -> continue_orchestration(existing project_root/task_ref plus server continuation step/results verbatim) -> close_agent(completed child)",
            prompt,
        )
        self.assertNotIn(
            "read_worker_result -> close_agent(completed child) -> continue_orchestration",
            prompt,
        )
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

    def test_bootstrap_missing_input_live_prompt_requires_one_question_and_same_worker_resume(self) -> None:
        prompt = LUNA_EVAL.live_prompt("bootstrap_missing_inputs", Path("/workspace/cortex-live"))
        self.assertIn("bootstrap_fixture_approval", prompt)
        self.assertIn("intentionally absent", prompt)
        self.assertIn("ask exactly one durable text worker_question", prompt)
        self.assertIn("rerun complete bootstrap validation", prompt)
        self.assertIn("command=answer", prompt)
        self.assertIn("outcome=question_answered", prompt)
        self.assertIn("server next_action explicitly requires the exact same native worker", prompt)
        self.assertIn("original child target to resume the same child", prompt)
        self.assertIn("do not execute any successor dispatch", prompt)

    def test_bootstrap_missing_input_lifecycle_requires_question_followup_and_result(self) -> None:
        events = [
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "question_recorded"},
            {
                "event": "cortex_mcp_call", "tool": "manage_orchestration", "status": "completed",
                "ok": True, "management_intent": "question", "outcome": "question_answered",
                "resume_contract": True,
            },
            {"event": "native_tool_call", "tool": "followup_task", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "attempt_result_recorded"},
            {"event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed", "ok": True},
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
        ]
        self.assertTrue(LUNA_EVAL.observed_question_resume_lifecycle(events))
        self.assertFalse(LUNA_EVAL.observed_question_resume_lifecycle(events[:-1]))

    def test_bootstrap_missing_input_lifecycle_allows_extra_waits_but_not_a_second_worker(self) -> None:
        events = [
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "question_recorded"},
            {
                "event": "cortex_mcp_call", "tool": "manage_orchestration", "status": "completed",
                "ok": True, "management_intent": "question", "outcome": "question_answered",
                "resume_contract": True,
            },
            {"event": "native_tool_call", "tool": "followup_task", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "question_recorded"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "attempt_result_recorded"},
            {"event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed", "ok": True},
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
        ]
        self.assertTrue(LUNA_EVAL.observed_question_resume_lifecycle(events))
        with_second_worker = [*events[:3], {
            "event": "native_tool_call", "tool": "spawn_agent", "status": "completed",
        }, *events[3:]]
        self.assertFalse(LUNA_EVAL.observed_question_resume_lifecycle(with_second_worker))


if __name__ == "__main__":
    unittest.main()
