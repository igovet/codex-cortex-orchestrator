"""Focused coverage for fresh release-fixture results and lazy-artifact checks."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
            "noncurrent-" + "format",
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
        self.assertIn("Every ready_to_spawn response authorizes only its returned spawn_agent call", coordinator_completion)
        self.assertIn("unmodified arguments", coordinator_completion)
        self.assertIn("worker final must be exactly ATTEMPT_COMPLETED", coordinator_completion)
        self.assertIn("calls read_worker_result with task_ref plus coordinator_ref plus the exact server-derived step", coordinator_completion)
        self.assertIn("server returns all canonical results for the current wave and the continuation", coordinator_completion)
        self.assertIn("closes the completed child", coordinator_completion)
        self.assertIn("before any successor dispatch", coordinator_completion)
        self.assertLess(
            coordinator_completion.index("ATTEMPT_COMPLETED"),
            coordinator_completion.index("read_worker_result"),
        )
        self.assertLess(
            coordinator_completion.index("read_worker_result"),
            coordinator_completion.index("closes the completed child"),
        )
        self.assertIn("successful server lifecycle outcome", coordinator_completion)
        self.assertEqual(prompt_compiler.load_prompt_contract(contract_path), contract)

        contract["attempt_result_contract"].pop("coordinator_completion")
        with tempfile.TemporaryDirectory() as temporary:
            invalid_path = Path(temporary) / "prompt-contracts.json"
            invalid_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "coordinator completion contract"):
                prompt_compiler.load_prompt_contract(invalid_path)

    def test_worker_prompt_cards_are_nested_and_server_bound(self) -> None:
        contract = json.loads((ROOT / "plugins/cortex/prompt-contracts.json").read_text(encoding="utf-8"))
        profiles = json.loads((ROOT / "plugins/cortex/profiles.json").read_text(encoding="utf-8"))
        shared = profiles["shared_worker_contract"]
        cards = shared["operation_cards"]
        self.assertEqual(
            contract["lint"]["source_ownership"]["worker_operation_cards"],
            "profiles.json.shared_worker_contract.operation_cards",
        )
        self.assertIn("task_ref", cards["complete_attempt"]["input"])
        self.assertIn("assignment_ref", cards["complete_attempt"]["input"])
        self.assertIn("plan", cards["complete_attempt"]["input"])
        self.assertIn("compact plan/outcome", cards["complete_attempt"]["purpose"])
        self.assertIn("assignment_ref", cards["read_worker_result"]["input"])
        self.assertIn("coordinator_ref", cards["read_worker_result"]["input"])
        self.assertIn("verified worker assignment", cards["read_worker_result"]["purpose"])
        self.assertIn("attachment path as a placeholder", shared["attachment_preflight"])


    def test_fresh_prompt_surface_has_no_retired_tool_contract_routes(self) -> None:
        forbidden = (
            "activation_marker",
            "scoping",
            "question-schema/noncurrent",
            "obsolete aliases",
            "non-current public",
            "sol_escalation",
            "model/effort remapping",
            "latest aliases",
            "phase aliases",
            "private component API",
            "bare `/cortex`",
            "bare `/normal`",
        )
        targets = [
            ROOT / "plugins/cortex/profiles.json",
            ROOT / "plugins/cortex/prompt-contracts.json",
            ROOT / "plugins/cortex/scripts/cortex_runtime/briefings.py",
            *sorted((ROOT / "plugins/cortex/agents").glob("*.toml")),
            *sorted((ROOT / "plugins/cortex/skills").rglob("*.md")),
        ]
        for path in targets:
            content = path.read_text(encoding="utf-8").lower()
            found = [term for term in forbidden if term.lower() in content]
            self.assertEqual(found, [], f"retired tool-contract vocabulary in {path}: {found}")
            # retryable=true is not a general prompt protocol.  Its sole
            # installable use is the sanitized bootstrap-missing machine
            # marker, with one concrete missing field, the generic exact-pair
            # contract spelling, or the contract placeholder. Any remaining
            # occurrence revives retired prose.
            without_bootstrap_marker = re.sub(
                r"cortex_worker_bootstrap_missing\s*(?:\"\s*)*missing_fields=\[(?:task_ref|assignment_ref|task_ref,assignment_ref|\.\.\.)\] retryable=true",
                "",
                content,
            )
            if "retryable=true" in without_bootstrap_marker:
                for recovery_marker in (
                    "same_operation", "state_mutated=false", "allowed_changes",
                ):
                    self.assertIn(
                        recovery_marker,
                        without_bootstrap_marker,
                        f"retryable=true must be bounded by executable structured recovery in {path}",
                    )

    def test_bundled_skills_require_server_audit_before_presentation_or_close(self) -> None:
        plugin = ROOT / "plugins/cortex"
        control = (plugin / "skills/cortex-control/SKILL.md").read_text(encoding="utf-8")
        orchestrator = (plugin / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        for marker in (
            "never completion evidence",
            "server-derived step and result ref",
            "exact terminal outcome",
            "canonical result\nhas been read",
        ):
            self.assertIn(marker, control)
        self.assertIn("server returns a terminal outcome after canonical result processing", orchestrator)

    def test_luna_fixture_attempt_results_are_complete_and_have_no_open_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            (project / "changed.txt").write_text("fixture\n", encoding="utf-8")

            attempt_result = LUNA_EVAL.passing_attempt_result(project, "close")
            self.assertEqual(attempt_result["status"], "completed")
            self.assertEqual(attempt_result["findings"], [])
            self.assertEqual(attempt_result["decisions_needed"], [])
            self.assertEqual(attempt_result["unresolved"], [])
            self.assertEqual(attempt_result["changed_files"], ["changed.txt"])

    def test_v11_cold_boot_smoke_reports_capability_scoped_ledger_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cold_base = base / "cold-boot"
            cold_base.mkdir()
            cold_boot = COLD_BOOT.run(cold_base)

        self.assertEqual(cold_boot, {
            "status": "PASS",
            "task_schema": "cortex/v11",
            "state_schema": "cortex/v11",
            "ledger_schema": "v17",
        })

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
        self.assertIn("This automatic-sequential fixture is decision-complete", prompt)
        self.assertIn("Workers MUST NOT call worker_question", prompt)
        self.assertIn("do not invent an answer, guess, route, resume, replace the worker, or widen the scope", prompt)
        self.assertIn("stop the scenario transparently and let the evaluator mark it FAIL", prompt)
        self.assertIn("The evaluator rejects any question record for this scenario", prompt)

        # The worker receives only the immutable JSON task contract.  The
        # coordinator-level instruction after the closing tag cannot prevent a
        # worker from following the generic question rule, so the
        # decision-complete policy must be inside the contract itself.
        contract_text = prompt.rsplit("<cortex_task_contract>", 1)[1].split(
            "</cortex_task_contract>", 1
        )[0]
        contract = json.loads(contract_text)
        self.assertIn("must not call worker_question", contract["user_request"])
        self.assertTrue(any(
            "must not publish a question" in str(item)
            for item in contract["acceptance_criteria"]
        ))

    def test_automatic_sequential_question_audit_fails_closed_without_retaining_question_data(self) -> None:
        self.assertEqual(
            LUNA_EVAL.automatic_sequential_question_audit([]),
            {
                "question_state_available": True,
                "question_count": 0,
                "no_unexpected_questions": True,
            },
        )
        failed = LUNA_EVAL.automatic_sequential_question_audit([{"question": "sensitive text"}])
        self.assertFalse(failed["no_unexpected_questions"])
        self.assertEqual(failed["question_count"], 1)
        self.assertNotIn("sensitive text", str(failed))
        unavailable = LUNA_EVAL.automatic_sequential_question_audit(None)
        self.assertFalse(unavailable["question_state_available"])
        self.assertFalse(unavailable["no_unexpected_questions"])

    def test_planner_evaluator_treats_missing_manifest_as_safe_failed_evidence(self) -> None:
        """A missing planning projection is a failed check, never evaluator crash data."""
        with mock.patch.object(LUNA_EVAL.cortex, "current_planning_manifest", return_value=None):
            manifest = LUNA_EVAL.evaluation_planning_manifest(Path("/workspace/cortex-live"))
        self.assertEqual(manifest, {})
        package_artifacts = manifest.get("work_packages", [])
        self.assertEqual(package_artifacts, [])
        planning_check = (
            manifest.get("schema") == "cortex/planning/v1"
            and len(package_artifacts) >= 2
        )
        self.assertFalse(planning_check)

    def test_planner_live_prompt_uses_exact_schema_safe_work_breakdown(self) -> None:
        prompt = LUNA_EVAL.live_prompt("planner_work_breakdown", Path("/workspace/cortex-live"))
        self.assertIn('"plan_approval":"required"', prompt)
        self.assertIn('waves exactly [{"phase":"discover","workers":[{}]}', prompt)
        self.assertIn('{"phase":"plan","workers":[{}]}', prompt)
        self.assertIn('{"phase":"implementation","workers":[{}]}', prompt)
        self.assertIn('"id":"inspect_source"', prompt)
        self.assertIn('"id":"deliver_result"', prompt)
        self.assertIn('"depends_on":["inspect_source"]', prompt)
        self.assertIn("Do not add, remove, rename, or reorder packages or microtasks", prompt)
        self.assertIn("decision=prompt", prompt)
        self.assertIn("embedded Approve action arguments", prompt)
        self.assertIn("Follow the server-returned next_action and management contract verbatim", prompt)
        self.assertIn("do not call continue_orchestration for this plan wave", prompt)
        self.assertIn("server-provided request_id", prompt)
        self.assertNotIn("never call approval before that continue", prompt)

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
        self.assertIn("scenario-owned answer authorization", prompt)
        self.assertIn("it is not ordinary user authority", prompt)
        self.assertIn("exact two-call route", prompt)
        self.assertIn("expect outcome=awaiting_user", prompt)
        self.assertIn("UNATTENDED FIXTURE-RESUME RULE", prompt)
        self.assertIn("awaiting_user is an intermediate receipt", prompt)
        self.assertIn("The very next action in the same parent turn MUST be", prompt)
        self.assertIn("request input, or end the parent turn", prompt)
        self.assertIn("between those two receipts is a failed evaluator run", prompt)
        self.assertIn("command=answer, the exact same", prompt)
        self.assertIn("resume/read/continue receipt is missing", prompt)
        self.assertIn("<evaluator_question_authorization>", prompt)
        self.assertIn("strict state machine for all five sequential server waves", prompt)
        self.assertIn(
            "read_worker_result -> continue_orchestration(existing project_root/task_ref plus server continuation step/results verbatim) -> close_agent(completed child)",
            prompt,
        )
        self.assertNotIn(
            "read_worker_result -> close_agent(completed child) -> continue_orchestration",
            prompt,
        )
        self.assertIn(
            "the only legal next tool call is close_agent for the completed child whose exact result that continuation consumed",
            prompt,
        )
        self.assertIn(
            "This rule includes the final close wave: even when the continuation outcome=completed and no successor dispatch is returned, close_agent the completed child before stopping",
            prompt,
        )
        self.assertIn("The terminal close count must equal the native spawn count", prompt)
        self.assertIn("projection_ref: it is a generated view identifier, never a result lookup token", prompt)
        self.assertIn("copy only the bare value of the attempt_result_ref field into read_worker_result", prompt)
        self.assertIn("do not add gate-specific noncanonical envelopes", prompt)
        self.assertIn(
            "close_agent, or any management operation for a child from an earlier wave after a later wave has been dispatched",
            prompt,
        )
        self.assertNotIn(
            "close_agent, or any management operation after a later child terminal response",
            prompt,
        )
        self.assertIn(
            "Only after that close succeeds, when the continuation outcome=ready_to_spawn, the only legal next tool call is every returned dispatch.call",
            prompt,
        )
        self.assertNotIn(
            "After each successful continue_orchestration response with outcome=ready_to_spawn, the only legal next tool call is every returned dispatch.call",
            prompt,
        )
        self.assertIn("A native wait is legal only immediately after a successful native dispatch", prompt)
        self.assertIn("after an accepted continuation", prompt)
        self.assertIn("If Cortex returns retryable=false for task identity or step mismatch, stop the scenario immediately", prompt)

    def test_automatic_governance_question_authority_is_explicit_and_fail_closed(self) -> None:
        policy = LUNA_EVAL.live_question_policy("automatic_governance")
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertEqual(policy["maximum_questions"], 1)
        self.assertEqual(
            policy["preauthorized_answer"],
            "Proceed with the stated fixture contract and current repository evidence; do not widen scope.",
        )
        self.assertTrue(LUNA_EVAL.question_matches_pre_authorized_policy({
            "question": "May this fixture proceed within the stated scope?",
        }, policy))
        self.assertFalse(LUNA_EVAL.question_matches_pre_authorized_policy({
            "question": "Which external deployment should we use?",
        }, policy))

    def test_targeted_live_prompts_carry_complete_start_and_follow_up_contracts(self) -> None:
        compact = LUNA_EVAL.live_prompt("compact_parallel", Path("/workspace/cortex-live"))
        self.assertIn('<cortex_task_contract>{"user_request":', compact)
        self.assertIn('"complexity":"C1"', compact)
        self.assertIn('"plan_approval":"auto"', compact)
        self.assertIn('these exact waves: [{"phase":"discover","workers":[{', compact)
        self.assertEqual(compact.count('"phase":"discover"'), 1)

        follow_up = LUNA_EVAL.live_prompt(
            "follow_up_partial", Path("/workspace/cortex-live"), "task-source-ref",
        )
        self.assertIn('payload exactly {"user_request":', follow_up)
        self.assertIn('"acceptance_criteria"', follow_up)
        self.assertIn('"verification"', follow_up)
        self.assertIn('"plan_approval":"auto"', follow_up)

    def test_bootstrap_missing_pair_prompt_requires_zero_calls_and_one_same_child_repair(self) -> None:
        prompt = LUNA_EVAL.live_prompt("bootstrap_missing_inputs", Path("/workspace/cortex-live"))
        for marker in (
            "CORTEX_WORKER_BOOTSTRAP_MISSING",
            "omit assignment_ref only from the first native spawn message",
            "missing_fields=[assignment_ref] retryable=true",
            "make zero Cortex/project calls",
            "followup_task exactly once for that same native child",
            "exact original bootstrap_repair_message unchanged",
            "emit no gate-passed acknowledgement",
            "immediately calls read_dispatch_briefing",
            "continues the original assignment through complete_attempt",
            "exactly ATTEMPT_COMPLETED",
            "gate-passed prose final is a nonterminal protocol failure",
            "Never spawn a replacement",
            "session, environment, thread, path, database, hook",
            "make no second follow-up",
            "finalize_bootstrap_failure exactly once",
            "never read a worker result",
            "no resumable orphan remains",
        ):
            self.assertIn(marker, prompt)
        self.assertNotIn("bootstrap_fixture_approval", prompt)
        self.assertNotIn("worker_question(action=poll)", prompt)
        self.assertNotIn("command=answer", prompt)

    def test_repaired_gate_acknowledgement_is_not_a_terminal_success(self) -> None:
        self.assertEqual(
            LUNA_EVAL.classified_native_outcome({
                "child": {"message": "Cortex worker bootstrap gate passed; both required identifiers are present and valid."},
            }),
            "other_terminal_message",
        )

    def test_bootstrap_missing_pair_lifecycle_recovers_on_exactly_one_followup(self) -> None:
        events = [
            {"event": "cortex_mcp_call", "tool": "start_orchestration", "status": "completed", "ok": True,
             "bootstrap_repair_message_digests": ["a" * 64]},
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "bootstrap_missing"},
            {"event": "native_tool_call", "tool": "followup_task", "status": "completed", "bootstrap_repair_message_digest": "a" * 64},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "attempt_completed"},
            {"event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed", "ok": True},
            {"event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "completed", "ok": True},
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
        ]
        self.assertTrue(LUNA_EVAL.observed_bootstrap_repair_lifecycle(events, recovered=True))
        self.assertFalse(LUNA_EVAL.observed_bootstrap_repair_lifecycle(events[:-1], recovered=True))

    def test_bootstrap_missing_outcome_accepts_only_a_sanitized_single_field_marker(self) -> None:
        self.assertEqual(
            LUNA_EVAL.classified_native_outcome({
                "child": {
                    "message": "CORTEX_WORKER_BOOTSTRAP_MISSING "
                    "missing_fields=[assignment_ref] retryable=true",
                },
            }),
            "bootstrap_missing",
        )
        self.assertEqual(
            LUNA_EVAL.classified_native_outcome({
                "child": {
                    "message": "CORTEX_WORKER_BOOTSTRAP_MISSING "
                    "missing_fields=[task_ref,assignment_ref] retryable=true",
                },
            }),
            "bootstrap_missing",
        )
        self.assertNotEqual(
            LUNA_EVAL.classified_native_outcome({
                "child": {
                    "message": "CORTEX_WORKER_BOOTSTRAP_MISSING "
                    "missing_fields=[task_ref|assignment_ref] retryable=true leaked-capability",
                },
            }),
            "bootstrap_missing",
        )

    def test_bootstrap_missing_pair_second_failure_is_terminal_without_replacement_or_ambient_calls(self) -> None:
        terminal = [
            {"event": "cortex_mcp_call", "tool": "start_orchestration", "status": "completed", "ok": True,
             "bootstrap_repair_message_digests": ["a" * 64]},
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "bootstrap_missing"},
            {"event": "native_tool_call", "tool": "followup_task", "status": "completed", "bootstrap_repair_message_digest": "a" * 64},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "bootstrap_missing"},
            {"event": "cortex_mcp_call", "tool": "manage_orchestration", "status": "completed", "ok": True,
             "management_intent": "finalize_bootstrap_failure"},
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
        ]
        self.assertTrue(LUNA_EVAL.observed_bootstrap_repair_lifecycle(terminal, recovered=False))
        self.assertFalse(LUNA_EVAL.observed_bootstrap_repair_lifecycle([
            *terminal[:-1],
            {"event": "native_tool_call", "tool": "followup_task", "status": "completed"},
        ], recovered=False))
        self.assertFalse(LUNA_EVAL.observed_bootstrap_repair_lifecycle([
            *terminal[:2],
            {"event": "native_tool_call", "tool": "list_agents", "status": "completed"},
            *terminal[2:],
        ], recovered=False))
        self.assertFalse(LUNA_EVAL.observed_bootstrap_repair_lifecycle([
            *terminal[:2],
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            *terminal[2:],
        ], recovered=False))

    def test_gate_passed_repair_trace_finalizes_without_result_read_or_orphan(self) -> None:
        malformed = [
            {"event": "cortex_mcp_call", "tool": "start_orchestration", "status": "completed", "ok": True,
             "bootstrap_repair_message_digests": ["a" * 64]},
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "bootstrap_missing"},
            {"event": "native_tool_call", "tool": "followup_task", "status": "completed",
             "bootstrap_repair_message_digest": "a" * 64},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "other_terminal_message"},
            {"event": "cortex_mcp_call", "tool": "manage_orchestration", "status": "completed", "ok": True,
             "management_intent": "finalize_bootstrap_failure"},
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
        ]
        self.assertTrue(LUNA_EVAL.observed_bootstrap_repair_lifecycle(malformed, recovered=False))
        self.assertFalse(LUNA_EVAL.observed_bootstrap_repair_lifecycle([
            *malformed[:5],
            {"event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed", "ok": True},
            *malformed[5:],
        ], recovered=False))

    def test_nonretryable_worker_terminal_requires_cleanup_without_result_or_continue(self) -> None:
        self.assertEqual(LUNA_EVAL.classified_native_outcome({
            "child": {"message": "CORTEX_ATTEMPT_FAILED retryable=false"},
        }), "attempt_failed_nonretryable")
        lifecycle = [
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "attempt_failed_nonretryable"},
            {"event": "cortex_mcp_call", "tool": "manage_orchestration", "status": "completed", "ok": False,
             "management_intent": "finalize_worker_failure", "terminal_cleanup": True},
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
        ]
        self.assertTrue(LUNA_EVAL.observed_worker_failure_lifecycle(lifecycle))
        self.assertFalse(LUNA_EVAL.observed_worker_failure_lifecycle([
            *lifecycle[:2],
            {"event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed", "ok": False},
            *lifecycle[2:],
        ]))
        self.assertFalse(LUNA_EVAL.observed_worker_failure_lifecycle([
            *lifecycle[:3],
            {"event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "completed", "ok": False},
            lifecycle[3],
        ]))

    def test_authorized_question_resume_can_follow_prior_completed_waves(self) -> None:
        """A governance question is allowed late; only its local route is strict."""
        events = [
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "attempt_completed"},
            {"event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed", "ok": True},
            {"event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "completed", "ok": True},
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "question_recorded"},
            {
                "event": "cortex_mcp_call", "tool": "manage_orchestration", "status": "completed",
                "ok": True, "management_intent": "question", "outcome": "awaiting_user",
            },
            {
                "event": "cortex_mcp_call", "tool": "manage_orchestration", "status": "completed",
                "ok": True, "management_intent": "question", "outcome": "question_answered",
                "resume_contract": True,
            },
            {"event": "native_tool_call", "tool": "followup_task", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "attempt_completed"},
            {"event": "cortex_mcp_call", "tool": "read_worker_result", "status": "completed", "ok": True},
            {"event": "cortex_mcp_call", "tool": "continue_orchestration", "status": "completed", "ok": True},
            {"event": "native_tool_call", "tool": "close_agent", "status": "completed"},
        ]
        self.assertTrue(LUNA_EVAL.observed_question_resume_lifecycle(events))

    def test_authorized_awaiting_user_receipt_cannot_end_question_path(self) -> None:
        """Fixture authority makes the durable answer mandatory, never implicit."""
        events = [
            {"event": "native_tool_call", "tool": "spawn_agent", "status": "completed"},
            {"event": "native_tool_call", "tool": "wait", "status": "completed", "outcome": "question_recorded"},
            {
                "event": "cortex_mcp_call", "tool": "manage_orchestration", "status": "completed",
                "ok": True, "management_intent": "question", "outcome": "awaiting_user",
            },
        ]
        self.assertFalse(LUNA_EVAL.observed_question_resume_lifecycle(events))


if __name__ == "__main__":
    unittest.main()
