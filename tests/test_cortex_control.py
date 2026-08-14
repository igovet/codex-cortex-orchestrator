import io
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex as control


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.ledger = self.project / ".codex" / "cortex"
        self._handlers = {}
        for handler, _ in control.TOOLS.values():
            name = handler.__name__
            if name in self._handlers:
                continue
            original = getattr(control, name)
            self._handlers[name] = original
            setattr(control, name, lambda params, original=original: original({**params, "project_root": str(self.project)}))

    def tearDown(self):
        for name, handler in self._handlers.items():
            setattr(control, name, handler)
        self.temp.cleanup()

    def activate(self, principal="thread-a"):
        return control.activate_orchestration({"user_command": "/cortex", "principal": principal, "thread_id": principal})

    def init(self, task_id="demo", complexity="C1"):
        self.activate()
        classified = control.classify_task({"complexity": complexity, "requirements": [], "principal": "thread-a"})
        return control.init_task({"task_id": task_id, "objective": "test objective", "complexity": complexity, "classification_id": classified["classification_id"], "principal": "thread-a"})

    def delegate(self, state, task_id, gate, agent, **extra):
        observed = control.status({"task_id": task_id, "principal": state.get("principal", "thread-a")})
        contract = {"task_kind": gate, "risk": "moderate", "requested_model": "gpt-5.6-terra", "requested_reasoning_effort": "medium", "ownership": f"Own {gate}", "allowed_paths": ["."], "acceptance_criteria": [f"Complete {gate}"], "verification": ["Report evidence"]}
        delegated = control.record_delegation({"task_id": task_id, "principal": state.get("principal", "thread-a"), "expected_revision": state["revision"], "status_receipt": observed["status_receipt"], "gate": gate, "agent": agent, "objective": "test delegation", **contract, **extra})
        confirmed = control.confirm_host_spawn({
            "task_id": task_id,
            "principal": state.get("principal", "thread-a"),
            "expected_revision": delegated["state"]["revision"],
            "attempt_id": delegated["attempt_id"],
            "host_agent_id": f"test-host-{delegated['attempt_id']}",
            "host_task_name": delegated["spawn_request"]["task_name"],
            "host_model": delegated["spawn_request"]["model"],
            "host_reasoning_effort": delegated["spawn_request"]["reasoning_effort"],
        })
        return {**delegated, "state": confirmed["state"], "host_spawn": confirmed["host_spawn"]}

    def report(self, task_id, attempt_id, principal="thread-a", submission_id="final"):
        return control.record_report({"task_id": task_id, "principal": principal, "attempt_id": attempt_id, "submission_id": submission_id, "report": {"summary": "delegated work complete", "findings": [], "questions": [], "changed_files": [], "tests": [], "evidence": ["focused test evidence"], "uncertainty": [], "next_action": "advance the gate"}})

    def test_orchestration_is_inactive_until_main_chat_command(self):
        with self.assertRaisesRegex(ValueError, "inactive"):
            control.init_task({"task_id": "inactive", "objective": "nope", "complexity": "C1", "principal": "thread-a"})
        activated = self.activate()
        self.assertTrue(activated["active"])
        self.assertEqual(activated["activation"]["coordinator"], "main")
        self.assertEqual(activated["activation"]["identity_assurance"], "caller_asserted_principal_and_thread")
        self.assertEqual(activated["activation"]["dispatch_attestation"], "not_host_attested")
        self.assertTrue(control.activation_status({"principal": "thread-a"})["active"])

    def test_activation_requires_exact_command_and_rejects_agent_profile(self):
        with self.assertRaisesRegex(ValueError, "exact standalone /cortex text trigger"):
            control.activate_orchestration({"user_command": "please orchestrate", "principal": "thread-a", "thread_id": "thread-a"})
        with self.assertRaisesRegex(ValueError, "does not accept an agent profile"):
            control.activate_orchestration({"agent": "general", "user_command": "/cortex", "principal": "thread-a", "thread_id": "thread-a"})
        self.assertFalse((self.ledger / "activations.json").exists())
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))

    def test_activation_without_token_returns_recoverable_next_action(self):
        result = control.activate_orchestration({"principal": "thread-a", "thread_id": "thread-a"})
        self.assertFalse(result["active"])
        self.assertTrue(result["recoverable"])
        self.assertIn("user_command", result["next_action"])

    def test_default_ledger_is_project_local(self):
        with self.assertRaisesRegex(ValueError, "project_root is required"):
            control.ledger_root()
        root = control.ledger_root({"project_root": str(self.project)})
        self.assertEqual(root, self.ledger)
        self.assertTrue((root / "tasks").is_dir())

    def test_plugin_local_mcp_requires_and_honors_explicit_project_root(self):
        """The plugin's cwd must never become the durable task workspace."""
        previous_cwd = os.getcwd()
        try:
            os.chdir(control.PLUGIN_ROOT)
            with self.assertRaisesRegex(ValueError, "project_root is required"):
                self._handlers["activate_orchestration"]({"user_command": "/cortex", "principal": "thread-a", "thread_id": "thread-a"})
            with tempfile.TemporaryDirectory() as project:
                root = Path(project)
                arguments = {"project_root": project}
                activated = self._handlers["activate_orchestration"]({"user_command": "/cortex", "principal": "thread-a", "thread_id": "thread-a", **arguments})
                self.assertTrue(activated["active"])
                self.assertEqual(activated["ledger_root"], str(root / ".codex/cortex"))
                classified = self._handlers["classify_task"]({"complexity": "C1", "requirements": [], "principal": "thread-a", **arguments})
                created = self._handlers["init_task"]({"task_id": "plugin-cwd", "objective": "workspace binding", "complexity": "C1", "classification_id": classified["classification_id"], "principal": "thread-a", **arguments})
                self.assertEqual(created["ledger_root"], str(root / ".codex/cortex"))
                self.assertTrue((root / ".codex/cortex/tasks" / created["task_directory"] / "task.json").is_file())
                observed = self._handlers["status"]({"task_id": "plugin-cwd", "principal": "thread-a", **arguments})
                self.assertEqual(observed["task"]["project_root"], project)
                self.assertEqual(observed["ledger_root"], str(root / ".codex/cortex"))
        finally:
            os.chdir(previous_cwd)

    def test_tasks_receive_project_local_sequence_numbers(self):
        first = self.init(task_id="first-task")
        control.deactivate_orchestration({"user_command": "/normal", "principal": "thread-a"})
        self.activate()
        second = self.init(task_id="second-task")
        root = self.ledger
        self.assertEqual(first["task_number"], 1)
        self.assertEqual(second["task_number"], 2)
        self.assertTrue((root / "tasks" / "0001-first-task" / "task.json").exists())
        self.assertTrue((root / "tasks" / "0002-second-task" / "task.json").exists())
        self.assertEqual(control.status({"task_id": "first-task", "principal": "thread-a"})["task"]["task_number"], 1)

    def test_activation_persists_until_main_chat_returns_to_normal(self):
        self.activate()
        first_classification = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        control.init_task({"task_id": "first-task", "objective": "one task", "complexity": "C1", "classification_id": first_classification["classification_id"], "principal": "thread-a"})
        second_classification = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "inactive"):
            control.init_task({"task_id": "second-task", "objective": "second", "complexity": "C1", "classification_id": second_classification["classification_id"], "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "exact /normal"):
            control.deactivate_orchestration({"user_command": "normal", "principal": "thread-a"})
        control.deactivate_orchestration({"user_command": "/normal", "principal": "thread-a"})
        self.assertFalse(control.activation_status({"principal": "thread-a"})["active"])

    def test_activation_status_infers_the_only_bound_activation(self):
        self.activate()
        inferred = control.activation_status({})
        self.assertTrue(inferred["active"])
        self.assertTrue(inferred["identity_inferred"])
        self.assertEqual(inferred["activation"]["principal"], "thread-a")

    def test_init_consumes_classification_contract_without_duplicate_inputs(self):
        self.activate()
        requirements = ["implementation, verification, and documentation", "preserve the durable ledger"]
        classified = control.classify_task({"complexity": "C2", "requirements": requirements, "principal": "thread-a"})
        created = control.init_task({
            "task_id": "receipt-contract", "objective": "consume the immutable classification contract",
            "classification_id": classified["classification_id"], "principal": "thread-a",
        })
        self.assertEqual(created["state"]["complexity"], "C2")
        task = json.loads((self.ledger / "tasks" / created["task_directory"] / "task.json").read_text(encoding="utf-8"))
        self.assertEqual(task["requirements"], requirements)

    def test_init_ignores_duplicate_inputs_and_consumes_authoritative_receipt(self):
        self.activate()
        classified = control.classify_task({"complexity": "C2", "requirements": ["original"], "principal": "thread-a"})
        created = control.init_task({
            "task_id": "mismatched-contract", "objective": "consume authoritative receipt",
            "classification_id": classified["classification_id"], "complexity": "C3", "requirements": ["replacement"],
            "principal": "thread-a",
        })
        self.assertEqual(created["state"]["complexity"], "C2")
        task = json.loads((self.ledger / "tasks" / created["task_directory"] / "task.json").read_text(encoding="utf-8"))
        self.assertEqual(task["requirements"], ["original"])

    def test_init_ignores_truncated_c3_pipeline_and_uses_classification_receipt(self):
        self.activate()
        classified = control.classify_task({"complexity": "C3", "requirements": ["cross-system database security work"], "principal": "thread-a"})
        truncated = ["plan", "discover", "implementation", "qa", "review"]
        created = control.init_task({
            "task_id": "c3-truncated-pipeline", "objective": "consume authoritative C3 pipeline",
            "classification_id": classified["classification_id"], "pipeline": truncated,
            "principal": "thread-a",
        })
        self.assertEqual(created["state"]["current_pipeline"], classified["pipeline"])
        self.assertIn("documentation", created["state"]["current_pipeline"])
        self.assertIn("close", created["state"]["current_pipeline"])
        self.assertEqual(created["pipeline_correction"], {
            "requested": truncated, "used": classified["pipeline"], "source": "classification_receipt",
        })

    def test_init_resumes_existing_task_with_objective_correction_and_rebinds_activation(self):
        first = self.init(task_id="resume-existing", complexity="C2")
        control.activate_orchestration({"user_command": "/cortex", "principal": "thread-a", "thread_id": "thread-a"})
        classified = control.classify_task({"complexity": "C3", "requirements": ["repeat audit"], "principal": "thread-a"})
        resumed = control.init_task({
            "task_id": "resume-existing", "objective": "different generated wording",
            "classification_id": classified["classification_id"], "principal": "thread-a", "thread_id": "thread-a",
        })
        self.assertFalse(resumed["created"])
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["state"]["revision"], first["state"]["revision"])
        self.assertEqual(resumed["objective_correction"]["used"], "test objective")
        self.assertTrue(control.status({"task_id": "resume-existing", "principal": "thread-a"})["active"])

    def test_legacy_classification_receipt_requires_its_checked_requirements(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": ["preserve compatibility"], "principal": "thread-a"})
        receipt_path = self.ledger / "classification-receipts" / f"{classified['classification_id']}.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        del receipt["requirements"]
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "legacy classification receipt"):
            control.init_task({
                "task_id": "legacy-receipt", "objective": "require explicit legacy inputs",
                "classification_id": classified["classification_id"], "principal": "thread-a",
            })
        created = control.init_task({
            "task_id": "legacy-receipt", "objective": "require explicit legacy inputs",
            "classification_id": classified["classification_id"], "requirements": ["preserve compatibility"],
            "principal": "thread-a",
        })
        self.assertTrue(created["created"])

    def test_classifier_uses_boundaries(self):
        self.activate()
        self.assertNotIn("ux", control.classify_task({"complexity": "C2", "requirements": ["build verification"], "principal": "thread-a"})["pipeline"])
        self.assertIn("ux", control.classify_task({"complexity": "C2", "requirements": ["UI review"], "principal": "thread-a"})["pipeline"])

    def test_c3_pipeline_is_dynamic_and_does_not_assume_database_work(self):
        self.activate()
        plain = control.classify_task({
            "complexity": "C3",
            "requirements": ["Update the plugin manifest and CI packaging while preserving security invariants"],
            "principal": "thread-a",
        })
        self.assertNotIn("database_architecture", plain["pipeline"])
        self.assertNotIn("database_architecture", plain["conditional_gates"])
        self.assertIn("security", plain["pipeline"])

        database = control.classify_task({
            "complexity": "C3",
            "requirements": ["Perform a PostgreSQL schema migration and validate the transaction model"],
            "principal": "thread-a",
        })
        self.assertIn("database_architecture", database["pipeline"])
        self.assertEqual(database["conditional_gates"].count("database_architecture"), 1)
        self.assertIn("database_architecture", database["conditional_gate_reasons"])

    def test_generic_manifest_schema_does_not_trigger_database_gate(self):
        self.activate()
        classified = control.classify_task({
            "complexity": "C3",
            "requirements": ["Validate the JSON manifest schema and API contract"],
            "principal": "thread-a",
        })
        self.assertNotIn("database_architecture", classified["pipeline"])

    def test_explicit_orchestrator_pipeline_controls_all_optional_gates(self):
        self.activate()
        classified = control.classify_task({
            "complexity": "C3",
            "requirements": ["The orchestrator selected the gates from the task shape"],
            "pipeline": ["plan", "discover", "architecture", "implementation", "qa", "review"],
            "principal": "thread-a",
        })
        self.assertEqual(classified["pipeline_source"], "orchestrator")
        self.assertEqual(classified["pipeline"], [
            "plan", "discover", "architecture", "implementation", "qa", "review",
            "documentation", "close",
        ])
        self.assertEqual(classified["conditional_gates"], [])
        self.assertEqual(classified["pipeline_corrections"], [
            {"gate": "documentation", "reason": "mandatory C3 audit gate"},
            {"gate": "close", "reason": "mandatory C3 audit gate"},
        ])

    def test_reassessment_accepts_orchestrator_selected_full_replacement(self):
        state = self.init(task_id="explicit-reassessment", complexity="C2")["state"]
        revised = control.reassess_pipeline({
            "task_id": "explicit-reassessment",
            "principal": "thread-a",
            "expected_revision": state["revision"],
            "signals": ["discovery confirmed no database or security scope"],
            "pipeline": ["plan", "discover", "implementation", "review"],
            "intent": "resequence",
            "decision": "updated",
            "reason": "Remove unnecessary QA after discovery; keep the orchestrator-selected gates.",
            "apply": True,
        })
        self.assertTrue(revised["applied"])
        self.assertEqual(revised["pipeline_source"], "orchestrator")
        self.assertEqual(revised["state"]["current_pipeline"], [
            "plan", "discover", "implementation", "review", "documentation", "close",
        ])
        self.assertEqual(revised["state"]["parallel_groups"], [
            ["plan"], ["discover"], ["implementation"], ["review"],
            ["documentation"], ["close"],
        ])
        self.assertNotIn("database_architecture", revised["state"]["current_pipeline"])

    def test_security_always_routes_to_sol_at_every_risk(self):
        for risk in ("low", "moderate", "high", "critical"):
            with self.subTest(risk=risk):
                route = control.resolve_dispatch_route({"agent": "security_auditor", "task_kind": "security", "risk": risk, "complexity": "C1", "requested_model": "gpt-5.6-terra", "requested_reasoning_effort": "low"})
                self.assertEqual(route["policy_model"], "gpt-5.6-sol")
                self.assertEqual(route["selected_model"], "gpt-5.6-sol")
                self.assertEqual(route["selected_reasoning_effort"], "high")
                self.assertEqual(route["fallback_reason"], "policy_model_enforced")

    def test_security_profile_normalizes_contradictory_lightweight_kind_to_sol(self):
        route = control.resolve_dispatch_route({"agent": "security_auditor", "task_kind": "reading", "risk": "low", "complexity": "C1"})
        self.assertEqual(route["task_kind"], "security")
        self.assertEqual(route["policy_model"], "gpt-5.6-sol")
        self.assertEqual(route["selected_model"], "gpt-5.6-sol")

    def test_security_gate_normalizes_contradictory_lightweight_kind_to_sol_in_delegation(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": ["security"], "principal": "thread-a"})
        state = control.init_task({"task_id": "security-route", "objective": "security routing", "complexity": "C1", "pipeline": ["security", "close"], "classification_id": classified["classification_id"], "principal": "thread-a"})["state"]
        delegation = self.delegate(state, "security-route", "security", "security_auditor", task_kind="reading", risk="low")
        self.assertEqual(delegation["spawn_request"]["model"], "gpt-5.6-sol")
        self.assertEqual(delegation["state"]["attempts"][-1]["task_kind"], "security")

    def test_each_lightweight_dispatch_routes_independently_of_task_complexity(self):
        for complexity, risk, expected in (("C1", "low", "gpt-5.6-luna"), ("C2", "low", "gpt-5.6-luna"), ("C3", "moderate", "gpt-5.6-luna"), ("C1", "high", "gpt-5.6-terra"), ("C2", "critical", "gpt-5.6-terra")):
            with self.subTest(complexity=complexity, risk=risk):
                route = control.resolve_dispatch_route({"agent": "explorer", "task_kind": "reading", "risk": risk, "complexity": complexity})
                self.assertEqual(route["policy_model"], expected)
                self.assertEqual(route["selected_model"], expected)

    def test_record_gate_returns_revision_correction_instead_of_stale_revision_error(self):
        state = self.init(task_id="gate-revision", complexity="C1")["state"]
        result = control.record_gate({
            "task_id": "gate-revision",
            "principal": "thread-a",
            "expected_revision": state["revision"] + 7,
            "gate": "plan",
            "outcome": "skipped",
            "skip_reason": "read-only routing test",
        })
        self.assertTrue(result["state"])
        self.assertEqual(result["revision_correction"], {"requested": state["revision"] + 7, "used": state["revision"]})

    def test_c2_lightweight_agent_routes_to_luna_in_delegation(self):
        state = self.init(task_id="c2-lightweight", complexity="C2")["state"]
        delegation = self.delegate(state, "c2-lightweight", "plan", "planner", task_kind="reading", risk="low")
        self.assertEqual(delegation["spawn_request"]["model"], "gpt-5.6-luna")

    def test_lightweight_categories_route_to_luna_with_multi_agent_v2(self):
        for agent, task_kind in (("explorer", "reading"), ("explorer", "discover"), ("explorer", "read_discovery"), ("explorer", "read_only_audit"), ("explorer", "comparative_audit"), ("explorer", "comparative-audit"), ("general", "data_gathering"), ("general", "crud_edit"), ("general", "small_fix")):
            for effort in ("high", "xhigh"):
                with self.subTest(agent=agent, task_kind=task_kind, effort=effort):
                    route = control.resolve_dispatch_route({"agent": agent, "task_kind": task_kind, "risk": "low", "complexity": "C1", "requested_reasoning_effort": effort})
                    self.assertEqual(route["policy_model"], "gpt-5.6-luna")
                    self.assertEqual(route["selected_model"], "gpt-5.6-luna")
                    self.assertEqual(route["selected_reasoning_effort"], effort)
        for task_kind in ("implementation", "tests", "debugging", "architecture", "migration"):
            with self.subTest(non_lightweight=task_kind):
                route = control.resolve_dispatch_route({"agent": "general", "task_kind": task_kind, "risk": "low", "complexity": "C1"})
                self.assertEqual(route["selected_model"], "gpt-5.6-terra")

    def test_terra_style_task_kind_is_canonicalized_at_the_mcp_boundary(self):
        for supplied, expected, model in (("Code Review", "code_review", "gpt-5.6-terra"), ("READ-ONLY", "read_only", "gpt-5.6-luna"), ("data   gathering", "data_gathering", "gpt-5.6-luna")):
            with self.subTest(supplied=supplied):
                route = control.resolve_dispatch_route({"agent": "explorer", "task_kind": supplied, "risk": "low", "complexity": "C1"})
                self.assertEqual(route["task_kind"], expected)
                self.assertEqual(route["selected_model"], model)
        documentation_audit = control.resolve_dispatch_route({
            "agent": "technical_writer",
            "task_kind": "read_only_audit",
            "risk": "low",
            "complexity": "C3",
        })
        self.assertEqual(documentation_audit["selected_model"], "gpt-5.6-luna")
        self.assertTrue(documentation_audit["read_only"])
        with self.assertRaisesRegex(ValueError, "must contain only"):
            control.resolve_dispatch_route({"agent": "explorer", "task_kind": "review/execute", "risk": "low", "complexity": "C1"})

    def test_record_delegation_accepts_human_readable_task_kind(self):
        state = self.init(task_id="terra-task-kind")["state"]
        delegation = self.delegate(state, "terra-task-kind", "discover", "explorer", task_kind="Code Review")
        self.assertEqual(delegation["state"]["attempts"][-1]["task_kind"], "code_review")

    def test_luna_can_be_explicitly_requested_for_lightweight_dispatch(self):
        route = control.resolve_dispatch_route({"agent": "explorer", "task_kind": "reading", "risk": "low", "complexity": "C1", "requested_model": "gpt-5.6-luna"})
        self.assertEqual(route["selected_model"], "gpt-5.6-luna")

    def test_all_ordinary_non_security_complexities_route_to_terra(self):
        for complexity in ("C1", "C2", "C3"):
            for risk in ("low", "moderate", "high", "critical"):
                with self.subTest(complexity=complexity, risk=risk):
                    route = control.resolve_dispatch_route({"agent": "general", "task_kind": "implementation", "risk": risk, "complexity": complexity})
                    self.assertEqual(route["policy_model"], "gpt-5.6-terra")
                    self.assertEqual(route["selected_model"], "gpt-5.6-terra")

    def test_non_security_sol_requires_structured_authorization(self):
        for params in (
            {"requested_model": "gpt-5.6-sol", "escalation_reason": "operator preference"},
            {"requested_model": "gpt-5.6-sol", "sol_escalation": {"kind": "auditable_extreme", "criterion": "free form note", "audit_ref": "note-1"}},
            {"requested_model": "gpt-5.6-sol", "sol_escalation": {"kind": "terra_failure", "prior_terra_attempt_id": "missing-01"}},
        ):
            with self.subTest(params=params):
                with self.assertRaisesRegex(ValueError, "structured auditable_extreme|supported criterion|validated failed Terra"):
                    control.resolve_dispatch_route({"agent": "general", "task_kind": "implementation", "risk": "high", "complexity": "C3", **params})

    def test_auditable_extreme_criterion_permits_non_security_sol(self):
        route = control.resolve_dispatch_route({"agent": "general", "task_kind": "migration", "risk": "critical", "complexity": "C3", "requested_reasoning_effort": "xhigh", "sol_escalation": {"kind": "auditable_extreme", "criterion": "irreversible_multi_system_recovery", "audit_ref": "classification-001"}})
        self.assertEqual(route["policy_model"], "gpt-5.6-sol")
        self.assertEqual(route["selected_model"], "gpt-5.6-sol")
        self.assertEqual(route["selected_reasoning_effort"], "xhigh")
        self.assertEqual(route["sol_escalation"]["criterion"], "irreversible_multi_system_recovery")

    def test_failed_terra_attempt_in_ledger_permits_non_security_sol(self):
        state = self.init(task_id="sol-link")["state"]
        terra = self.delegate(state, "sol-link", "discover", "explorer", task_kind="implementation", requested_model="gpt-5.6-terra")
        failed = control.finalize_attempt({"task_id": "sol-link", "principal": "thread-a", "expected_revision": terra["state"]["revision"], "attempt_id": terra["attempt_id"], "status": "failed", "reason": "bounded Terra attempt could not resolve the defect"})
        observed = control.status({"task_id": "sol-link", "principal": "thread-a"})
        sol = control.record_delegation({"task_id": "sol-link", "principal": "thread-a", "expected_revision": failed["state"]["revision"], "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "general", "task_kind": "implementation", "risk": "high", "requested_model": "gpt-5.6-sol", "sol_escalation": {"kind": "terra_failure", "prior_terra_attempt_id": terra["attempt_id"]}, "objective": "retry after Terra failure", "ownership": "Own retry", "allowed_paths": ["."], "acceptance_criteria": ["Resolve the defect"], "verification": ["Report evidence"]})
        self.assertEqual(sol["spawn_request"]["model"], "gpt-5.6-sol")
        self.assertEqual(sol["state"]["attempts"][-1]["sol_escalation"], {"kind": "terra_failure", "prior_terra_attempt_id": terra["attempt_id"]})

    def test_evidence_is_required_to_pass(self):
        result = self.init()
        state = result["state"]
        delegation = self.delegate(state, "demo", "discover", "explorer")
        self.assertEqual(delegation["state"]["attempts"][-1]["display_name"], "explorer")
        delegation_file = json.loads(Path(delegation["delegation_file"]).read_text(encoding="utf-8"))
        self.assertEqual(delegation_file["display_name"], "explorer")
        self.assertEqual(delegation_file["profile"], "explorer")
        self.assertEqual(delegation_file["selected_model"], "gpt-5.6-luna")
        self.assertEqual(delegation_file["selected_reasoning_effort"], "medium")
        self.assertFalse(delegation_file["user_facing"])
        self.assertEqual(delegation_file["question_route"]["mode"], "pull")
        self.assertEqual(delegation_file["question_route"]["worker_tool"], "cortex.question")
        self.assertEqual(delegation_file["question_route"]["publish_tool"], "publish_worker_question")
        self.assertEqual(delegation_file["question_route"]["coordinator_ui_tool"], "cortex.question")
        self.assertEqual(delegation_file["escalation_route"], "main_chat")
        self.assertEqual(delegation_file["handoff_route"], "main_chat")
        pending = control.record_gate({"task_id": "demo", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "discover", "outcome": "passed"})
        self.assertFalse(pending["recorded"])
        self.assertEqual(pending["next_action"], "record_evidence")
        evidence = control.record_evidence({"task_id": "demo", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "discover", "attempt_id": delegation["attempt_id"], "kind": "report", "summary": "inspection completed", "command": "Authorization: Bearer <TOKEN>"})
        self.assertIn("<REDACTED>", evidence["state"]["evidence"][0]["command"])
        closed = control.record_gate({"task_id": "demo", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed"})
        self.assertEqual(closed["state"]["current_gate"], "implementation")

    def test_documentation_evidence_alias_is_canonicalized(self):
        created = self.init(task_id="documentation-alias", complexity="C2")
        narrowed = control.update_pipeline({
            "task_id": "documentation-alias",
            "principal": "thread-a",
            "expected_revision": created["state"]["revision"],
            "pipeline": ["documentation", "close"],
            "reason": "isolate documentation contract",
        })
        delegation = self.delegate(narrowed["state"], "documentation-alias", "documentation", "technical_writer")
        report = self.report("documentation-alias", delegation["attempt_id"])
        evidence = control.record_evidence({
            "task_id": "documentation-alias",
            "principal": "thread-a",
            "expected_revision": report["state"]["revision"],
            "gate": "documentation",
            "attempt_id": delegation["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "kind": "documentation_sync",
            "decision": "updated",
            "summary": "documentation is synchronized",
        })
        self.assertEqual(evidence["evidence"]["kind"], "documentation")
        self.assertEqual(evidence["state"]["documentation_receipt"]["attempt_id"], delegation["attempt_id"])
        passed = control.record_gate({
            "task_id": "documentation-alias",
            "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"],
            "gate": "documentation",
            "outcome": "passed",
        })
        self.assertIn("documentation", passed["state"]["completed_gates"])

    def test_documentation_gate_repairs_legacy_receipt_without_new_worker(self):
        created = self.init(task_id="documentation-legacy", complexity="C2")
        narrowed = control.update_pipeline({
            "task_id": "documentation-legacy",
            "principal": "thread-a",
            "expected_revision": created["state"]["revision"],
            "pipeline": ["documentation", "close"],
            "reason": "isolate legacy documentation receipt",
        })
        delegation = self.delegate(narrowed["state"], "documentation-legacy", "documentation", "technical_writer")
        report = self.report("documentation-legacy", delegation["attempt_id"])
        evidence = control.record_evidence({
            "task_id": "documentation-legacy",
            "principal": "thread-a",
            "expected_revision": report["state"]["revision"],
            "gate": "documentation",
            "attempt_id": delegation["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "kind": "documentation_sync",
            "decision": "updated",
            "summary": "legacy documentation evidence",
        })
        task_dir = self.ledger / "tasks" / "0001-documentation-legacy"
        current_path = task_dir / "current.json"
        current = json.loads(current_path.read_text(encoding="utf-8"))
        current["evidence"][-1]["kind"] = "documentation_sync"
        current["documentation_receipt"] = None
        current_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        evidence_path = task_dir / "evidence" / f"{evidence['evidence']['evidence_id']}.json"
        legacy_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        legacy_evidence["kind"] = "documentation_sync"
        evidence_path.write_text(json.dumps(legacy_evidence, indent=2) + "\n", encoding="utf-8")
        status = control.status({"task_id": "documentation-legacy", "principal": "thread-a"})
        passed = control.record_gate({
            "task_id": "documentation-legacy",
            "principal": "thread-a",
            "expected_revision": status["state"]["revision"],
            "gate": "documentation",
            "outcome": "passed",
        })
        self.assertIn("documentation", passed["state"]["completed_gates"])
        self.assertEqual(passed["state"]["documentation_receipt"]["evidence_id"], evidence["evidence"]["evidence_id"])

    def test_documentation_retry_cannot_spawn_duplicate_worker(self):
        created = self.init(task_id="documentation-no-loop", complexity="C2")
        narrowed = control.update_pipeline({
            "task_id": "documentation-no-loop",
            "principal": "thread-a",
            "expected_revision": created["state"]["revision"],
            "pipeline": ["documentation", "close"],
            "reason": "isolate duplicate-dispatch guard",
        })
        delegation = self.delegate(narrowed["state"], "documentation-no-loop", "documentation", "technical_writer")
        self.report("documentation-no-loop", delegation["attempt_id"])
        status = control.status({"task_id": "documentation-no-loop", "principal": "thread-a"})
        duplicate = control.record_delegation({
            "task_id": "documentation-no-loop",
            "principal": "thread-a",
            "expected_revision": status["state"]["revision"],
            "status_receipt": status["status_receipt"],
            "gate": "documentation",
            "agent": "technical_writer",
            "task_kind": "documentation",
            "risk": "low",
            "objective": "retry documentation",
            "ownership": "Own documentation",
            "allowed_paths": ["."],
            "acceptance_criteria": ["Publish documentation evidence"],
            "verification": ["Review docs"],
        })
        self.assertFalse(duplicate["recorded"])
        self.assertEqual(duplicate["reason"], "documentation_attempt_already_available")
        self.assertEqual(duplicate["next_action"], "record_evidence")
        self.assertEqual(len(duplicate["state"]["attempts"]), 1)

    def test_missing_report_attempt_can_be_finalized_and_remains_visible(self):
        state = self.init(task_id="missing-report", complexity="C2")["state"]
        delegation = self.delegate(state, "missing-report", "plan", "planner")
        missing_reason = control.finalize_attempt({
            "task_id": "missing-report",
            "principal": "thread-a",
            "expected_revision": delegation["state"]["revision"],
            "attempt_id": delegation["attempt_id"],
            "status": "failed",
        })
        self.assertFalse(missing_reason["recorded"])
        self.assertEqual(missing_reason["next_action"], "retry_finalize_attempt_with_reason")
        self.assertEqual(missing_reason["required_fields"], ["reason"])
        finalized = control.finalize_attempt({
            "task_id": "missing-report",
            "principal": "thread-a",
            "expected_revision": delegation["state"]["revision"],
            "attempt_id": delegation["attempt_id"],
            "status": "failed",
            "reason": "worker stopped before publishing a report",
        })
        self.assertEqual(finalized["status"], "failed")
        self.assertEqual(finalized["state"]["attempts"][0]["status"], "failed")
        self.assertEqual(finalized["state"]["attempts"][0]["finalization_reason"], "worker stopped before publishing a report")
        observed = control.status({"task_id": "missing-report", "principal": "thread-a"})
        self.assertEqual(observed["state"]["attempts"][0]["status"], "failed")

    def test_terminal_non_success_attempt_does_not_block_gate_completion(self):
        state = self.init(task_id="terminal-attempt", complexity="C2")["state"]
        abandoned = self.delegate(state, "terminal-attempt", "plan", "planner")
        finalized = control.finalize_attempt({
            "task_id": "terminal-attempt",
            "principal": "thread-a",
            "expected_revision": abandoned["state"]["revision"],
            "attempt_id": abandoned["attempt_id"],
            "status": "cancelled",
            "reason": "host worker timed out",
        })
        replacement = self.delegate(finalized["state"], "terminal-attempt", "plan", "planner")
        report = self.report("terminal-attempt", replacement["attempt_id"])
        evidence = control.record_evidence({
            "task_id": "terminal-attempt",
            "principal": "thread-a",
            "expected_revision": replacement["state"]["revision"],
            "gate": "plan",
            "attempt_id": replacement["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "summary": "replacement completed the gate",
        })
        closed = control.record_gate({
            "task_id": "terminal-attempt",
            "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"],
            "gate": "plan",
            "outcome": "passed",
        })
        statuses = {item["attempt_id"]: item["status"] for item in closed["state"]["attempts"]}
        self.assertEqual(statuses[abandoned["attempt_id"]], "cancelled")
        self.assertEqual(statuses[replacement["attempt_id"]], "passed")
        self.assertEqual(closed["state"]["current_gate"], "discover")

    def test_failed_terminal_attempt_without_evidence_does_not_block_gate_completion(self):
        state = self.init(task_id="failed-only-attempt", complexity="C2")["state"]
        failed = self.delegate(state, "failed-only-attempt", "plan", "planner")
        finalized = control.finalize_attempt({
            "task_id": "failed-only-attempt",
            "principal": "thread-a",
            "expected_revision": failed["state"]["revision"],
            "attempt_id": failed["attempt_id"],
            "status": "failed",
            "reason": "worker failed before producing a report",
        })
        closed = control.record_gate({
            "task_id": "failed-only-attempt",
            "principal": "thread-a",
            "expected_revision": finalized["state"]["revision"],
            "gate": "plan",
            "outcome": "passed",
        })
        self.assertEqual(closed["state"]["attempts"][0]["status"], "failed")
        self.assertEqual(closed["state"]["current_gate"], "discover")

    def test_active_running_attempt_without_evidence_still_blocks_gate(self):
        state = self.init(task_id="active-attempt", complexity="C2")["state"]
        delegation = self.delegate(state, "active-attempt", "plan", "planner")
        pending = control.record_gate({
                "task_id": "active-attempt",
                "principal": "thread-a",
                "expected_revision": delegation["state"]["revision"],
                "gate": "plan",
                "outcome": "passed",
            })
        self.assertFalse(pending["recorded"])
        self.assertEqual(pending["reason"], "evidence_required")

    def test_invalidated_running_attempt_can_be_superseded_and_no_longer_blocks_gate(self):
        state = self.init(task_id="invalidated-attempt", complexity="C2")["state"]
        original = self.delegate(state, "invalidated-attempt", "plan", "planner")
        reworked = control.update_pipeline({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": original["state"]["revision"],
            "operations": [{"op": "rework", "gate": "plan"}],
            "allow_rework": True,
        })
        missing_reason = control.finalize_attempt({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": reworked["state"]["revision"],
            "attempt_id": original["attempt_id"],
            "status": "superseded",
        })
        self.assertFalse(missing_reason["recorded"])
        self.assertEqual(missing_reason["next_action"], "retry_finalize_attempt_with_reason")
        with self.assertRaisesRegex(ValueError, "only be finalized as superseded"):
            control.finalize_attempt({
                "task_id": "invalidated-attempt",
                "principal": "thread-a",
                "expected_revision": reworked["state"]["revision"],
                "attempt_id": original["attempt_id"],
                "status": "cancelled",
                "reason": "rework replaced this attempt",
            })
        superseded = control.finalize_attempt({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": reworked["state"]["revision"],
            "attempt_id": original["attempt_id"],
            "status": "superseded",
            "reason": "rework replaced this attempt",
        })
        old_attempt = superseded["state"]["attempts"][0]
        self.assertTrue(old_attempt["invalidated"])
        self.assertEqual(old_attempt["status"], "superseded")

        replacement = self.delegate(superseded["state"], "invalidated-attempt", "plan", "planner")
        report = self.report("invalidated-attempt", replacement["attempt_id"])
        evidence = control.record_evidence({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": replacement["state"]["revision"],
            "gate": "plan",
            "attempt_id": replacement["attempt_id"],
            "report_receipt": report["receipt"]["receipt_id"],
            "summary": "replacement completed the reworked gate",
        })
        closed = control.record_gate({
            "task_id": "invalidated-attempt",
            "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"],
            "gate": "plan",
            "outcome": "passed",
        })
        statuses = {item["attempt_id"]: item["status"] for item in closed["state"]["attempts"]}
        self.assertEqual(statuses[original["attempt_id"]], "superseded")
        self.assertEqual(statuses[replacement["attempt_id"]], "passed")
        self.assertEqual(closed["state"]["current_gate"], "discover")

    def test_invalidated_terminal_attempt_remains_idempotent(self):
        state = self.init(task_id="invalidated-terminal", complexity="C2")["state"]
        original = self.delegate(state, "invalidated-terminal", "plan", "planner")
        failed = control.finalize_attempt({
            "task_id": "invalidated-terminal",
            "principal": "thread-a",
            "expected_revision": original["state"]["revision"],
            "attempt_id": original["attempt_id"],
            "status": "failed",
            "reason": "worker stopped",
        })
        reworked = control.update_pipeline({
            "task_id": "invalidated-terminal",
            "principal": "thread-a",
            "expected_revision": failed["state"]["revision"],
            "operations": [{"op": "rework", "gate": "plan"}],
            "allow_rework": True,
        })
        replay = control.finalize_attempt({
            "task_id": "invalidated-terminal",
            "principal": "thread-a",
            "expected_revision": reworked["state"]["revision"],
            "attempt_id": original["attempt_id"],
            "status": "failed",
        })
        self.assertTrue(replay["idempotent"])
        self.assertTrue(replay["state"]["attempts"][0]["invalidated"])

    def test_record_delegation_propagates_exact_spawn_request(self):
        state = self.init(task_id="spawn-contract")["state"]
        delegation = self.delegate(
            state,
            "spawn-contract",
            "discover",
            "general",
            requested_model="gpt-5.6-terra",
            requested_reasoning_effort="high",
            task_kind="implementation",
        )
        expected = {
            "host_tool": "spawn_agent",
            "profile": "general",
            "display_name": "general",
            "task_name": "general",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "high",
        }
        package = json.loads(Path(delegation["delegation_file"]).read_text(encoding="utf-8"))
        self.assertEqual({key: delegation["spawn_request"][key] for key in expected}, expected)
        self.assertEqual({key: package["spawn_request"][key] for key in expected}, expected)
        self.assertEqual({key: delegation["state"]["attempts"][-1]["spawn_request"][key] for key in expected}, expected)
        self.assertIn("internal Cortex worker with profile `general`", delegation["spawn_request"]["message"])
        self.assertEqual(delegation["state"]["attempts"][-1]["dispatch_correlation"], "coordinator_recorded_host_spawn")

    def test_host_spawn_confirmation_requires_the_exact_profile_task_name(self):
        state = self.init(task_id="host-name-contract")["state"]
        observed = control.status({"task_id": "host-name-contract", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-name-contract", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "inspect",
            "ownership": "Read-only discovery", "allowed_paths": ["."],
            "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        self.assertEqual(delegated["spawn_request"]["task_name"], "explorer")
        self.assertIn("Use attempt_id='discover-01' exactly", delegated["spawn_request"]["message"])
        self.assertIn("stable lowercase submission_id", delegated["spawn_request"]["message"])
        corrected = control.confirm_host_spawn({
                "task_id": "host-name-contract", "principal": "thread-a",
                "expected_revision": delegated["state"]["revision"],
                "attempt_id": delegated["attempt_id"], "host_agent_id": "desktop-child-123",
                "host_task_name": "cortex_discover_01",
            })
        self.assertEqual(corrected["task_name_correction"], {"requested": "cortex_discover_01", "used": "explorer"})
        self.assertEqual(corrected["host_spawn"]["task_name"], "explorer")

    def test_host_spawn_confirmation_without_host_fields_is_recoverable(self):
        state = self.init(task_id="host-fields")["state"]
        observed = control.status({"task_id": "host-fields", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-fields", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "plan", "agent": "planner",
            "task_kind": "planning", "risk": "low", "objective": "plan",
            "ownership": "Own plan", "allowed_paths": ["."],
            "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        recovered = control.confirm_host_spawn({
            "task_id": "host-fields", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
        })
        self.assertFalse(recovered["confirmed"])
        self.assertTrue(recovered["recoverable"])
        self.assertIn("host_agent_id", recovered["next_action"])

    def test_worker_profile_alias_can_publish_its_own_report_with_correction(self):
        state = self.init(task_id="worker-report-alias")["state"]
        observed = control.status({"task_id": "worker-report-alias", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "worker-report-alias", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "plan", "agent": "planner",
            "task_kind": "read_only_audit", "risk": "low", "objective": "plan",
            "ownership": "Own plan", "allowed_paths": ["."],
            "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        confirmed = control.confirm_host_spawn({
            "task_id": "worker-report-alias", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_agent_id": "planner", "host_task_name": "planner",
        })
        report = control.record_report({
            "task_id": "worker-report-alias", "principal": "planner", "attempt_id": delegated["attempt_id"],
            "submission_id": "worker-report", "report": {"summary": "done", "findings": [], "questions": [],
            "changed_files": [], "tests": [], "evidence": ["evidence"], "uncertainty": [], "next_action": "advance"},
        })
        self.assertEqual(report["principal_correction"], {"requested": "planner", "used": "thread-a"})

    def test_worker_report_infers_missing_attempt_and_submission_identifiers(self):
        state = self.init(task_id="worker-report-inference")["state"]
        delegated = self.delegate(state, "worker-report-inference", "plan", "planner", task_kind="planning", risk="low")
        report = control.record_report({
            "task_id": "worker-report-inference", "principal": "planner",
            "attempt_id": "", "submission_id": "",
            "report": {"summary": "done", "findings": [], "questions": [],
            "changed_files": [], "tests": [], "evidence": ["evidence"],
            "uncertainty": [], "next_action": "advance"},
        })
        self.assertFalse(report["idempotent"])
        self.assertEqual(report["report"]["attempt_id"], delegated["attempt_id"])
        self.assertTrue(report["report"]["submission_id"].startswith(f"submission-{delegated['attempt_id']}-report-"))
        self.assertEqual(report["principal_correction"], {"requested": "planner", "used": "thread-a"})

    def test_worker_report_requires_attempt_when_worker_identity_is_ambiguous(self):
        state = self.init(task_id="worker-report-ambiguous")["state"]
        first = self.delegate(state, "worker-report-ambiguous", "plan", "planner", task_kind="planning", risk="low", parallel=True)
        second = self.delegate(first["state"], "worker-report-ambiguous", "plan", "planner", task_kind="planning", risk="low", parallel=True)
        result = control.record_report({
            "task_id": "worker-report-ambiguous", "principal": "planner",
            "report": {"summary": "done", "findings": [], "questions": [],
            "changed_files": [], "tests": [], "evidence": ["evidence"],
            "uncertainty": [], "next_action": "advance"},
        })
        self.assertFalse(result["recorded"])
        self.assertEqual(result["reason"], "delegation_attempt_required")
        self.assertEqual(result["candidate_attempt_ids"], [first["attempt_id"], second["attempt_id"]])

    def test_delegation_requires_native_host_spawn_confirmation(self):
        state = self.init(task_id="host-spawn") ["state"]
        observed = control.status({"task_id": "host-spawn", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-spawn", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "inspect",
            "ownership": "Read-only discovery", "allowed_paths": ["."],
            "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        self.assertEqual(delegated["state"]["attempts"][-1]["status"], control.AWAITING_HOST_SPAWN)
        early_report = self.report("host-spawn", delegated["attempt_id"])
        self.assertTrue(early_report["host_confirmation_pending"])
        confirmed = control.confirm_host_spawn({
            "task_id": "host-spawn", "principal": "thread-a", "expected_revision": delegated["state"]["revision"],
            "attempt_id": delegated["attempt_id"], "host_agent_id": "desktop-child-123",
            "host_task_name": delegated["spawn_request"]["task_name"],
        })
        self.assertEqual(confirmed["state"]["attempts"][-1]["status"], "running")
        self.assertEqual(confirmed["host_spawn"]["agent_id"], "desktop-child-123")

    def test_host_can_finalize_passed_before_coordinator_links_report_evidence(self):
        state = self.init(task_id="finalize-before-evidence", complexity="C2")["state"]
        delegation = self.delegate(state, "finalize-before-evidence", "plan", "planner", task_kind="planning", risk="moderate")
        report = self.report("finalize-before-evidence", delegation["attempt_id"])
        finalized = control.finalize_attempt({
            "task_id": "finalize-before-evidence", "principal": "thread-a",
            "expected_revision": delegation["state"]["revision"],
            "attempt_id": delegation["attempt_id"], "status": "passed",
        })
        pending = control.record_gate({
                "task_id": "finalize-before-evidence", "principal": "thread-a",
                "expected_revision": finalized["state"]["revision"], "gate": "plan", "outcome": "passed",
            })
        self.assertFalse(pending["recorded"])
        self.assertEqual(pending["next_action"], "record_evidence")
        evidence = control.record_evidence({
            "task_id": "finalize-before-evidence", "principal": "thread-a",
            "expected_revision": finalized["state"]["revision"], "gate": "plan",
            "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"],
            "summary": "worker report reviewed",
        })
        advanced = control.record_gate({
            "task_id": "finalize-before-evidence", "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed",
        })
        self.assertEqual(advanced["state"]["current_gate"], "discover")

    def test_recoverable_model_sequence_is_corrected_without_contract_errors(self):
        state = self.init(task_id="recoverable-sequence", complexity="C2")["state"]
        observed = control.status({"task_id": "recoverable-sequence", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "recoverable-sequence", "principal": "thread-a",
            "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
            "gate": "discover", "agent": "planner", "task_kind": "planning", "risk": "moderate",
            "objective": "plan", "ownership": "", "allowed_paths": [],
            "acceptance_criteria": [], "verification": [],
        })
        self.assertEqual(delegated["gate_correction"], {"requested": "discover", "used": "plan"})
        package = json.loads(Path(delegated["delegation_file"]).read_text(encoding="utf-8"))
        self.assertEqual(package["ownership"], "Own the plan gate as planner")
        self.assertEqual(package["allowed_paths"], ["."])
        self.assertTrue(package["acceptance_criteria"])
        self.assertTrue(package["verification"])
        premature = control.record_gate({
            "task_id": "recoverable-sequence", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "gate": "discover", "outcome": "passed",
        })
        self.assertFalse(premature["recorded"])
        self.assertEqual(premature["next_action"], "record_evidence")
        self.assertEqual(premature["gate_correction"], {"requested": "discover", "used": "plan"})
        confirmed = control.confirm_host_spawn({
            "task_id": "recoverable-sequence", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_agent_id": "luna-medium-worker", "host_task_name": delegated["spawn_request"]["task_name"],
        })
        report = self.report("recoverable-sequence", delegated["attempt_id"])
        inferred = control.record_evidence({
            "task_id": "recoverable-sequence", "principal": "thread-a",
            "expected_revision": confirmed["state"]["revision"], "gate": "discover",
            "summary": "report reviewed",
        })
        self.assertEqual(inferred["evidence"]["attempt_id"], delegated["attempt_id"])
        self.assertEqual(inferred["evidence"]["report_receipt"], report["receipt"]["receipt_id"])
        self.assertEqual(inferred["inferred"], {"gate": True, "attempt_id": True, "report_receipt": True})

    def test_delegation_infers_missing_gate_profile_kind_and_risk(self):
        state = self.init(task_id="inferred-delegation", complexity="C2")["state"]
        delegated = control.record_delegation({
            "task_id": "inferred-delegation", "principal": "thread-a",
        })
        self.assertEqual(delegated["state"]["attempts"][-1]["gate"], "plan")
        self.assertEqual(delegated["spawn_request"]["profile"], "planner")
        self.assertEqual(delegated["state"]["attempts"][-1]["task_kind"], "planning")
        self.assertEqual(delegated["state"]["attempts"][-1]["risk"], "low")
        self.assertEqual(delegated["agent_correction"], {"requested": None, "used": "planner"})
        self.assertEqual(delegated["task_kind_correction"], {"requested": None, "used": "planning"})
        self.assertEqual(delegated["risk_correction"], {"requested": None, "used": "low"})

    def test_rework_releases_retry_budget_for_invalidated_attempts(self):
        state = self.init(task_id="retry-rework") ["state"]
        first = self.delegate(state, "retry-rework", "discover", "explorer")
        failed = control.finalize_attempt({
            "task_id": "retry-rework", "principal": "thread-a", "expected_revision": first["state"]["revision"],
            "attempt_id": first["attempt_id"], "status": "failed", "reason": "first failed",
        })
        second = self.delegate(failed["state"], "retry-rework", "discover", "explorer")
        failed = control.finalize_attempt({
            "task_id": "retry-rework", "principal": "thread-a", "expected_revision": second["state"]["revision"],
            "attempt_id": second["attempt_id"], "status": "failed", "reason": "second failed",
        })
        observed = control.status({"task_id": "retry-rework", "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "retry budget exhausted"):
            control.record_delegation({
                "task_id": "retry-rework", "principal": "thread-a", "expected_revision": failed["state"]["revision"],
                "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
                "task_kind": "discover", "risk": "low", "objective": "third try", "ownership": "Read-only discovery",
                "allowed_paths": ["."], "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
            })
        reworked = control.update_pipeline({
            "task_id": "retry-rework", "principal": "thread-a", "expected_revision": failed["state"]["revision"],
            "operations": [{"op": "rework", "gate": "discover"}], "allow_rework": True, "reason": "new evidence",
        })
        observed = control.status({"task_id": "retry-rework", "principal": "thread-a"})
        resumed = control.record_delegation({
            "task_id": "retry-rework", "principal": "thread-a", "expected_revision": reworked["state"]["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "reworked try", "ownership": "Read-only discovery",
            "allowed_paths": ["."], "acceptance_criteria": ["Report findings"], "verification": ["Cite paths"],
        })
        self.assertEqual(resumed["state"]["attempts"][-1]["status"], control.AWAITING_HOST_SPAWN)

    def test_worker_question_bus_is_scoped_idempotent_and_resumable(self):
        state = self.init(task_id="questions")["state"]
        first = self.delegate(state, "questions", "discover", "general", parallel=True)
        second = self.delegate(first["state"], "questions", "discover", "explorer", parallel=True)
        publish_args = {
            "task_id": "questions",
            "principal": "thread-a",
            "attempt_id": first["attempt_id"],
            "submission_id": "need-decision",
            "question": "Which compatibility mode should I preserve?",
            "context": {"choices": ["strict", "legacy"]},
            "blocking": True,
        }
        published = control.publish_worker_question(publish_args)
        replay = control.publish_worker_question(publish_args)
        self.assertFalse(published["idempotent"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["question"]["question_id"], published["question"]["question_id"])
        with self.assertRaisesRegex(ValueError, "different content"):
            control.publish_worker_question({**publish_args, "question": "A different question"})
        with self.assertRaisesRegex(ValueError, "different principal"):
            control.list_worker_questions({"task_id": "questions", "principal": "intruder"})

        open_questions = control.list_worker_questions({"task_id": "questions", "principal": "thread-a", "status": "open"})
        self.assertEqual([item["question_id"] for item in open_questions["questions"]], [published["question"]["question_id"]])
        self.assertEqual(
            control.get_worker_question_updates({"task_id": "questions", "principal": "thread-a", "attempt_id": second["attempt_id"]})["updates"],
            [],
        )

        answer_args = {
            "task_id": "questions",
            "principal": "thread-a",
            "question_id": published["question"]["question_id"],
            "submission_id": "decision-1",
            "answer": "Preserve strict mode.",
            "resume_context": {"instruction": "Continue with strict compatibility", "source": "coordinator"},
        }
        answered = control.answer_worker_question(answer_args)
        answer_replay = control.answer_worker_question(answer_args)
        self.assertFalse(answered["idempotent"])
        self.assertTrue(answer_replay["idempotent"])
        updates = control.get_worker_question_updates({
            "task_id": "questions",
            "principal": "thread-a",
            "attempt_id": first["attempt_id"],
            "after_sequence": published["cursor"],
        })
        self.assertEqual([item["kind"] for item in updates["updates"]], ["question_answered"])
        self.assertEqual(updates["updates"][0]["resume_context"], answer_args["resume_context"])
        with self.assertRaisesRegex(ValueError, "different content"):
            control.answer_worker_question({**answer_args, "answer": "Use legacy mode."})

    def test_cortex_question_routes_workers_to_main_chat_with_flexible_answers(self):
        state = self.init(task_id="question-ui")["state"]
        delegated = self.delegate(state, "question-ui", "discover", "explorer")
        pending = control.cortex_question({
            "task_id": "question-ui", "principal": "thread-a", "attempt_id": delegated["attempt_id"],
            "submission_id": "explorer-question-1", "question": "Which paths should be changed?",
            "options": [{"label": "src", "description": "Update source files"}, {"label": "tests", "description": "Update tests"}],
            "multiple": True, "custom_label": "Additional direction", "context": {"reason": "scope"},
        })
        self.assertEqual(pending["status"], "pending_user_input")
        self.assertEqual(pending["ui"]["custom_label"], "Additional direction")
        question_id = pending["question_id"]
        listed = control.list_worker_questions({"task_id": "question-ui", "principal": "thread-a", "status": "open"})
        question = listed["questions"][0]
        self.assertEqual(question["options"][0]["label"], "src")
        self.assertTrue(question["multiple"])
        with mock.patch.object(control, "_request_mcp_elicitation", return_value=("accept", {"selections": ["src", "tests"], "custom_response": {"image": {"path": "/tmp/shot.png"}}}, "elicitation-1")):
            answered = control.cortex_question({"task_id": "question-ui", "principal": "thread-a", "question_id": question_id})
        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["answer"]["selections"], ["src", "tests"])
        self.assertEqual(answered["answer"]["custom_response"]["image"]["path"], "/tmp/shot.png")
        updates = control.get_worker_question_updates({"task_id": "question-ui", "principal": "thread-a", "attempt_id": delegated["attempt_id"]})
        self.assertEqual(updates["updates"][-1]["kind"], "question_answered")
        self.assertEqual(updates["updates"][-1]["answer"]["selections"], ["src", "tests"])

    def test_cortex_question_form_always_puts_custom_field_last(self):
        single = control._question_form_schema(control._question_config({"header": "Pick one", "options": ["A", "B"]}))
        self.assertEqual(list(single["properties"]), ["selection", "custom_response"])
        multi = control._question_form_schema(control._question_config({"header": "Pick many", "options": ["A", "B"], "multiple": True}))
        self.assertEqual(multi["properties"]["selections"]["type"], "array")
        self.assertEqual(list(multi["properties"])[-1], "custom_response")

    def test_multiple_worker_questions_are_independent_and_ordered(self):
        state = self.init(task_id="question-concurrency")['state']
        first = self.delegate(state, "question-concurrency", "discover", "general", parallel=True)
        second = self.delegate(first["state"], "question-concurrency", "discover", "explorer", parallel=True)
        for attempt, number in ((first, "one"), (second, "two")):
            control.cortex_question({
                "task_id": "question-concurrency", "principal": "thread-a", "attempt_id": attempt["attempt_id"],
                "submission_id": f"question-{number}", "question": f"Choose for worker {number}",
                "options": ["keep", "change"],
            })
        listed = control.list_worker_questions({"task_id": "question-concurrency", "principal": "thread-a", "status": "open"})
        self.assertEqual(listed["open_count"], 2)
        self.assertEqual(listed["open_question_ids"], [item["question_id"] for item in listed["questions"]])
        self.assertNotEqual(listed["questions"][0]["attempt_id"], listed["questions"][1]["attempt_id"])
        for question in listed["questions"]:
            control.answer_worker_question({
                "task_id": "question-concurrency", "principal": "thread-a", "question_id": question["question_id"],
                "submission_id": f"answer-{question['question_id']}", "answer": {"selection": "keep"},
                "resume_context": {"source": "test"},
            })
        self.assertEqual(control.list_worker_questions({"task_id": "question-concurrency", "principal": "thread-a", "status": "open"})["open_count"], 0)

    def test_task_language_and_internal_english_worker_contract(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        created = control.init_task({"task_id": "language-contract", "objective": "Проверить язык", "classification_id": classified["classification_id"], "principal": "thread-a", "user_language": "ru"})
        self.assertEqual(created["state"]["user_language"], "ru")
        delegation = self.delegate(created["state"], "language-contract", "discover", "explorer")
        self.assertIn("Internal worker protocol: English only", delegation["spawn_request"]["message"])
        self.assertIn("User-facing language: ru", delegation["spawn_request"]["message"])
        self.assertIn("call mcp__codebase_memory__list_projects with {}", delegation["spawn_request"]["message"])
        self.assertIn("select the record whose root_path exactly matches the absolute project_root", delegation["spawn_request"]["message"])
        self.assertIn("pass that record's name as project", delegation["spawn_request"]["message"])
        self.assertIn("search_graph(project, query=...)", delegation["spawn_request"]["message"])
        self.assertIn("search_code(project, pattern, regex, mode=compact|full|files", delegation["spawn_request"]["message"])
        self.assertIn("trace_path(project, function_name=<qualified_name from search_graph>", delegation["spawn_request"]["message"])
        self.assertIn("get_code_snippet(project, qualified_name=<exact qualified_name from search_graph>", delegation["spawn_request"]["message"])
        self.assertIn("Do not call index_repository, ingest_traces, manage_adr, or delete_project", delegation["spawn_request"]["message"])
        self.assertIn("Do not start with grep, rg, glob", delegation["spawn_request"]["message"])
        self.assertIn("If list_projects fails", delegation["spawn_request"]["message"])
        self.assertIn("documented fallback", delegation["spawn_request"]["message"])

    def test_composite_delegation_and_completion_fast_paths(self):
        self.init(task_id="composites")
        prepared = control.prepare_delegation({
            "task_id": "composites", "principal": "thread-a", "delegation": {
                "gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low",
                "objective": "Inspect the repository", "ownership": "Own discovery", "parallel": True,
                "allowed_paths": ["."], "acceptance_criteria": ["Publish findings"], "verification": ["Cite paths"],
            },
        })
        attempt_id = prepared["delegation"]["attempt_id"]
        completed = control.complete_attempt({
            "task_id": "composites", "principal": "thread-a", "attempt_id": attempt_id,
            "host_agent_id": "host-composite", "host_task_name": "explorer", "host_model": "gpt-5.6-luna",
            "host_reasoning_effort": "low", "status": "passed", "report": {
                "summary": "discovery complete", "findings": [], "questions": [], "changed_files": [],
                "tests": [], "evidence": ["source paths"], "uncertainty": [], "next_action": "advance",
            },
        })
        self.assertTrue(completed["atomic"])
        self.assertEqual(completed["state"]["attempts"][-1]["status"], "passed")
        receipt = completed["report"]["receipt"]["receipt_id"]
        committed = control.commit_gate({
            "task_id": "composites", "principal": "thread-a", "gate": "discover", "mode": "verification",
            "attempt_id": attempt_id, "report_receipt": receipt, "summary": "verify discovery", "verification_id": "benign_success",
        })
        self.assertTrue(committed["recorded"])
        self.assertEqual(committed["state"]["current_gate"], "implementation")
        audited = control.close_audit({"task_id": "composites", "principal": "thread-a"})
        self.assertEqual(audited["report_count"], 1)

    def test_prepare_delegations_rejects_mixed_gates(self):
        self.init(task_id="composite-batch")
        result = control.prepare_delegations({
            "task_id": "composite-batch", "principal": "thread-a", "delegations": [
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "one", "ownership": "one", "allowed_paths": ["."], "acceptance_criteria": ["one"], "verification": ["one"]},
                {"gate": "implementation", "agent": "general", "task_kind": "implementation", "risk": "moderate", "parallel": True, "objective": "two", "ownership": "two", "allowed_paths": ["."], "acceptance_criteria": ["two"], "verification": ["two"]},
            ],
        })
        self.assertFalse(result["recorded"])
        self.assertEqual(result["reason"], "batch_requires_one_gate")

    def test_prepare_delegations_returns_independent_spawn_requests(self):
        self.init(task_id="composite-success")
        result = control.prepare_delegations({
            "task_id": "composite-success", "principal": "thread-a", "delegations": [
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "one", "ownership": "one", "allowed_paths": ["."], "acceptance_criteria": ["one"], "verification": ["one"]},
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "two", "ownership": "two", "allowed_paths": ["."], "acceptance_criteria": ["two"], "verification": ["two"]},
            ],
        })
        self.assertTrue(result["recorded"])
        self.assertEqual(len(result["spawn_requests"]), 2)
        self.assertEqual(len(set(result["attempts"])), 2)
        self.assertTrue(all(item["task_name"] == "explorer" for item in result["spawn_requests"]))

    def test_parallel_gate_wave_accepts_multiple_independent_gates(self):
        self.activate()
        classified = control.classify_task({
            "complexity": "C1",
            "requirements": ["independent discovery and implementation checks"],
            "pipeline": ["discover", "implementation", "review"],
            "parallel_groups": [["discover", "implementation"], ["review"]],
            "principal": "thread-a",
        })
        created = control.init_task({
            "task_id": "parallel-wave", "objective": "run independent gates concurrently",
            "classification_id": classified["classification_id"], "principal": "thread-a",
        })
        self.assertEqual(created["state"]["current_gates"], ["discover", "implementation"])
        prepared = control.prepare_delegations({
            "task_id": "parallel-wave", "principal": "thread-a", "delegations": [
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "discover", "ownership": "discover", "allowed_paths": ["."], "acceptance_criteria": ["discover"], "verification": ["discover"]},
                {"gate": "implementation", "agent": "general", "task_kind": "implementation", "risk": "moderate", "parallel": True, "objective": "implement", "ownership": "implement", "allowed_paths": ["."], "acceptance_criteria": ["implement"], "verification": ["implement"]},
            ],
        })
        self.assertTrue(prepared["recorded"])
        self.assertEqual(prepared["gates"], ["discover", "implementation"])
        self.assertEqual({item["gate"] for item in prepared["state"]["attempts"]}, {"discover", "implementation"})

        implementation_evidence = control.record_evidence({
            "task_id": "parallel-wave", "principal": "thread-a", "expected_revision": prepared["state"]["revision"],
            "gate": "implementation", "summary": "implementation wave evidence",
        })
        implementation_passed = control.record_gate({
            "task_id": "parallel-wave", "principal": "thread-a", "expected_revision": implementation_evidence["state"]["revision"],
            "gate": "implementation", "outcome": "passed",
        })
        self.assertEqual(implementation_passed["state"]["current_gates"], ["discover"])

        discover_evidence = control.record_evidence({
            "task_id": "parallel-wave", "principal": "thread-a", "expected_revision": implementation_passed["state"]["revision"],
            "gate": "discover", "summary": "discovery wave evidence",
        })
        discover_passed = control.record_gate({
            "task_id": "parallel-wave", "principal": "thread-a", "expected_revision": discover_evidence["state"]["revision"],
            "gate": "discover", "outcome": "passed",
        })
        self.assertEqual(discover_passed["state"]["current_gate"], "review")
        self.assertEqual(discover_passed["state"]["current_gates"], ["review"])

    def test_reassessment_can_split_parallel_wave(self):
        self.activate()
        classified = control.classify_task({
            "complexity": "C1", "requirements": ["independent checks"],
            "pipeline": ["discover", "implementation", "review"],
            "parallel_groups": [["discover", "implementation"], ["review"]],
            "principal": "thread-a",
        })
        created = control.init_task({"task_id": "split-wave", "objective": "reassess waves", "classification_id": classified["classification_id"], "principal": "thread-a"})
        revised = control.reassess_pipeline({
            "task_id": "split-wave", "principal": "thread-a", "expected_revision": created["state"]["revision"],
            "signals": ["implementation now depends on discovery"], "pipeline": ["discover", "implementation", "review"],
            "parallel_groups": [["discover"], ["implementation"], ["review"]],
            "intent": "resequence", "decision": "updated", "reason": "new dependency discovered", "apply": True,
        })
        self.assertTrue(revised["applied"])
        self.assertEqual(revised["state"]["parallel_groups"], [["discover"], ["implementation"], ["review"], ["documentation"], ["close"]])
        self.assertEqual(revised["state"]["current_gates"], ["discover"])

    def test_prepare_delegations_rolls_back_on_mid_batch_failure(self):
        self.init(task_id="composite-rollback")
        result = control.prepare_delegations({
            "task_id": "composite-rollback", "principal": "thread-a", "delegations": [
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "one", "ownership": "one", "allowed_paths": ["."], "acceptance_criteria": ["one"], "verification": ["one"]},
                {"gate": "discover", "agent": "not-a-profile", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "two", "ownership": "two", "allowed_paths": ["."], "acceptance_criteria": ["two"], "verification": ["two"]},
            ],
        })
        self.assertFalse(result["recorded"])
        self.assertTrue(result["atomic"])
        self.assertEqual(result["prepared"], [])
        state = control.status({"task_id": "composite-rollback", "principal": "thread-a"})["state"]
        self.assertEqual(state["attempts"], [])

    def test_mcp_elicitation_nested_exchange_is_json_rpc_safe(self):
        original_stdin, original_stdout = control.sys.stdin, control.sys.stdout
        try:
            control.sys.stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": "cortex-question-smoke", "result": {"action": "accept", "content": {"custom_response": "yes"}}}) + "\n")
            control.sys.stdout = io.StringIO()
            with mock.patch.object(control.secrets, "token_hex", return_value="smoke"):
                action, content, request_id = control._request_mcp_elicitation("Choose", {"type": "object", "properties": {"custom_response": {"type": "string"}}})
            self.assertEqual((action, content, request_id), ("accept", {"custom_response": "yes"}, "cortex-question-smoke"))
            outbound = json.loads(control.sys.stdout.getvalue())
            self.assertEqual(outbound["method"], "elicitation/create")
        finally:
            control.sys.stdin, control.sys.stdout = original_stdin, original_stdout

    def test_openai_form_extension_is_used_when_host_advertises_it(self):
        original_stdin, original_stdout = control.sys.stdin, control.sys.stdout
        try:
            control.sys.stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": "cortex-question-extension", "result": {"action": "cancel"}}) + "\n")
            control.sys.stdout = io.StringIO()
            with mock.patch.object(control.secrets, "token_hex", return_value="extension"), mock.patch.object(control, "MCP_OPENAI_FORM", True):
                action, _, _ = control._request_mcp_elicitation("Choose", {"type": "object", "properties": {"custom_response": {"type": "string"}}})
            self.assertEqual(action, "cancel")
            outbound = json.loads(control.sys.stdout.getvalue())
            self.assertEqual(outbound["params"]["mode"], "openai/form")
        finally:
            control.sys.stdin, control.sys.stdout = original_stdin, original_stdout

    def test_mcp_process_completes_cortex_question_after_host_response(self):
        self.init(task_id="nested-question")
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        proc = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            def call(payload):
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
                return json.loads(proc.stdout.readline())

            initialized = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
            self.assertEqual(initialized["result"]["serverInfo"]["name"], "cortex")
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "cortex.question", "arguments": {"project_root": str(self.project), "task_id": "nested-question", "principal": "thread-a", "question": "Continue?"}}}) + "\n")
            proc.stdin.flush()
            elicitation = json.loads(proc.stdout.readline())
            self.assertEqual(elicitation["method"], "elicitation/create")
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": elicitation["id"], "result": {"action": "accept", "content": {"custom_response": "yes"}}}) + "\n")
            proc.stdin.flush()
            completed = json.loads(proc.stdout.readline())
            self.assertEqual(completed["id"], 2)
            self.assertEqual(completed["result"]["structuredContent"]["status"], "answered")
        finally:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()

    def test_blocked_task_can_resume(self):
        result = self.init()
        state = result["state"]
        blocked = control.record_gate({"task_id": "demo", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "outcome": "blocked", "summary": "dependency unavailable"})
        self.assertEqual(blocked["state"]["status"], "blocked")
        resumed = control.resume_task({"task_id": "demo", "principal": "thread-a", "expected_revision": blocked["state"]["revision"], "reason": "dependency restored"})
        self.assertEqual(resumed["state"]["status"], "active")

    def test_rework_resets_completed_gate(self):
        result = self.init()
        state = result["state"]
        evidence = control.record_evidence({"task_id": "demo", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "summary": "done"})
        closed = control.record_gate({"task_id": "demo", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed"})
        changed = control.update_pipeline({"task_id": "demo", "principal": "thread-a", "expected_revision": closed["state"]["revision"], "operations": [{"op": "rework", "gate": "discover"}], "allow_rework": True, "reason": "rework"})
        self.assertIn("discover", changed["state"]["current_pipeline"])
        self.assertNotIn("discover", changed["state"]["completed_gates"])

    def test_principal_binding_and_replan_limit(self):
        result = self.init()
        state = result["state"]
        with self.assertRaisesRegex(ValueError, "different principal"):
            control.reassess_pipeline({"task_id": "demo", "principal": "other", "expected_revision": state["revision"], "signals": ["security"], "apply": False})
        first = control.reassess_pipeline({"task_id": "demo", "principal": "thread-a", "expected_revision": state["revision"], "signals": ["security"], "apply": True})
        second = control.reassess_pipeline({"task_id": "demo", "principal": "thread-a", "expected_revision": first["state"]["revision"], "signals": ["performance"], "apply": True})
        with self.assertRaisesRegex(ValueError, "replan limit"):
            control.reassess_pipeline({"task_id": "demo", "principal": "thread-a", "expected_revision": second["state"]["revision"], "signals": ["docs"], "apply": True})

    def test_c2_requires_linked_attempt_and_handoff(self):
        result = self.init(task_id="c2", complexity="C2")
        state = result["state"]
        pending = control.record_evidence({"task_id": "c2", "principal": "thread-a", "expected_revision": state["revision"], "gate": "plan", "summary": "unlinked"})
        self.assertFalse(pending["recorded"])
        self.assertEqual(pending["next_action"], "record_delegation")
        delegation = self.delegate(state, "c2", "plan", "planner")
        report = self.report("c2", delegation["attempt_id"])
        evidence = control.record_evidence({"task_id": "c2", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "plan complete"})
        control.record_gate({"task_id": "c2", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed"})
        self.assertFalse(control.status({"task_id": "c2", "principal": "thread-a"})["state"]["handoff_created"])

    def test_report_bus_scoping_receipts_reconciliation_and_router(self):
        state = self.init(task_id="reports", complexity="C2")["state"]
        delegation = self.delegate(state, "reports", "plan", "planner", risk="low", requested_model="gpt-5.6-terra", requested_reasoning_effort="none")
        package = json.loads(Path(delegation["delegation_file"]).read_text(encoding="utf-8"))
        self.assertEqual(package["requested_model"], "gpt-5.6-terra")
        self.assertEqual(package["selected_model"], "gpt-5.6-terra")
        self.assertIsNone(package["fallback_reason"])
        self.assertEqual(package["selected_reasoning_effort"], "low")
        report = control.record_report({"task_id": "reports", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": "stable", "report": {"summary": "client_secret: canary", "findings": ["Authorization: Bearer canary"], "questions": [], "changed_files": [], "tests": [], "evidence": ["<script>alert(1)</script>"], "uncertainty": [], "next_action": "advance"}})
        replay = control.record_report({"task_id": "reports", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": "stable", "report": {"summary": "client_secret: canary", "findings": ["Authorization: Bearer canary"], "questions": [], "changed_files": [], "tests": [], "evidence": ["<script>alert(1)</script>"], "uncertainty": [], "next_action": "advance"}})
        self.assertTrue(replay["idempotent"])
        task_dir = self.ledger / "tasks" / "0001-reports"
        artifacts = "\n".join(path.read_text(encoding="utf-8") for path in (task_dir / "reports").rglob("*") if path.is_file())
        self.assertNotIn("canary", artifacts)
        self.assertIn("&lt;script&gt;", (task_dir / "reports/markdown/report-0001.md").read_text(encoding="utf-8"))
        evidence = control.record_evidence({"task_id": "reports", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "report-backed evidence"})
        with self.assertRaisesRegex(ValueError, "consumed"):
            control.record_evidence({"task_id": "reports", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "receipt replay"})
        (task_dir / "reports/markdown/report-0001.md").unlink()
        reconciled = control.reconcile_report_bus({"task_id": "reports", "principal": "thread-a"})
        self.assertEqual(reconciled["report_count"], 1)
        self.assertTrue((task_dir / "reports/markdown/report-0001.md").exists())

    def test_report_context_is_explicit_and_report_shape_is_strict(self):
        state = self.init(task_id="context", complexity="C2")["state"]
        first = self.delegate(state, "context", "plan", "planner")
        report = self.report("context", first["attempt_id"])
        with self.assertRaisesRegex(ValueError, "exactly"):
            control.record_report({"task_id": "context", "principal": "thread-a", "attempt_id": first["attempt_id"], "submission_id": "bad", "report": {**report["report"]["report"], "unknown": True}})
        granted = control.grant_report_context({"task_id": "context", "principal": "thread-a", "attempt_id": first["attempt_id"], "report_ids": [report["report"]["report_id"]], "reason": "self-read fixture"})
        self.assertEqual(granted["grant"]["report_ids"], [report["report"]["report_id"]])
        bodies = control.get_delegation_reports({"task_id": "context", "principal": "thread-a", "attempt_id": first["attempt_id"], "report_ids": [report["report"]["report_id"]]})
        self.assertEqual(len(bodies["reports"]), 1)
        with self.assertRaisesRegex(ValueError, "not granted"):
            control.get_delegation_reports({"task_id": "context", "principal": "thread-a", "attempt_id": first["attempt_id"], "report_ids": ["report-9999"]})

    def test_concurrent_report_publishers_are_serialized(self):
        state = self.init(task_id="publishers", complexity="C2")["state"]
        delegation = self.delegate(state, "publishers", "plan", "planner")
        results, failures = [], []

        def publish(index):
            try:
                results.append(control.record_report({"task_id": "publishers", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": f"publisher-{index}", "report": {"summary": f"publisher {index}", "findings": [], "questions": [], "changed_files": [], "tests": [], "evidence": [f"evidence {index}"], "uncertainty": [], "next_action": "merge"}}))
            except Exception as exc:  # pragma: no cover - failure is asserted below.
                failures.append(exc)

        threads = [threading.Thread(target=publish, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(failures)
        self.assertEqual(len({item["report"]["report_id"] for item in results}), 8)
        self.assertEqual(len(control.list_task_reports({"task_id": "publishers", "principal": "thread-a"})["reports"]), 8)

    def test_stranded_report_recovery_never_overwrites_authoritative_record(self):
        state = self.init(task_id="crash", complexity="C2")["state"]
        delegation = self.delegate(state, "crash", "plan", "planner")
        first = self.report("crash", delegation["attempt_id"], submission_id="first")
        task_dir = self.ledger / "tasks/0001-crash/reports"
        original = (task_dir / "records/report-0001.json").read_bytes()
        (task_dir / "index.json").write_text(json.dumps({"schema": control.REPORT_SCHEMA, "task_id": "crash", "reports": [], "submissions": {}, "updated_at": control.now()}), encoding="utf-8")
        (task_dir / "markdown/report-0001.md").unlink()
        (task_dir / "receipts/report-receipt-report-0001.json").unlink()
        replay = self.report("crash", delegation["attempt_id"], submission_id="first")
        self.assertTrue(replay["idempotent"])
        second = self.report("crash", delegation["attempt_id"], submission_id="second")
        self.assertEqual(second["report"]["report_id"], "report-0002")
        self.assertEqual((task_dir / "records/report-0001.json").read_bytes(), original)
        reconciled = control.reconcile_report_bus({"task_id": "crash", "principal": "thread-a"})
        self.assertEqual(reconciled["report_count"], 2)

    def test_reconcile_preserves_orphan_receipt_consumption_as_tombstone(self):
        state = self.init(task_id="receipt-boundary", complexity="C2")["state"]
        delegation = self.delegate(state, "receipt-boundary", "plan", "planner")
        report = self.report("receipt-boundary", delegation["attempt_id"])
        receipt_path = self.ledger / "tasks/0001-receipt-boundary/reports/receipts/report-receipt-report-0001.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["consumed_at"] = control.now()
        receipt["consumed_by_evidence_id"] = "evidence-0001"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        reconciled = control.reconcile_report_bus({"task_id": "receipt-boundary", "principal": "thread-a"})
        repaired = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertNotIn("receipts/report-receipt-report-0001.json", reconciled["repaired"])
        self.assertIsNotNone(repaired["consumed_at"])
        self.assertEqual(repaired["consumed_by_evidence_id"], "evidence-0001")
        tombstone = receipt_path.parents[1] / "consumptions" / "report-receipt-report-0001.json"
        self.assertTrue(tombstone.is_file())
        with self.assertRaisesRegex(ValueError, "consumed"):
            control.record_evidence({"task_id": "receipt-boundary", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "recovered"})

    def test_consumed_report_replay_reconstructs_consumed_receipt(self):
        state = self.init(task_id="receipt-replay", complexity="C2")["state"]
        delegation = self.delegate(state, "receipt-replay", "plan", "planner")
        report = self.report("receipt-replay", delegation["attempt_id"], submission_id="stable")
        evidence = control.record_evidence({"task_id": "receipt-replay", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "consumed"})
        receipt_path = self.ledger / "tasks/0001-receipt-replay/reports/receipts/report-receipt-report-0001.json"
        receipt_path.unlink()
        replay = self.report("receipt-replay", delegation["attempt_id"], submission_id="stable")
        self.assertTrue(replay["idempotent"])
        self.assertIsNotNone(replay["receipt"]["consumed_at"])
        self.assertEqual(replay["receipt"]["consumed_by_evidence_id"], "evidence-0001")
        with self.assertRaisesRegex(ValueError, "consumed"):
            control.record_evidence({"task_id": "receipt-replay", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "replay"})

    def test_report_bus_rejects_symlinked_child_directories(self):
        for child in ("records", "markdown", "receipts", "consumptions", "delegations", "grants"):
            with self.subTest(child=child):
                task_id = f"symlink-{child}"
                created = self.init(task_id=task_id, complexity="C2")
                task_dir = self.ledger / "tasks" / created["task_directory"]
                target = self.base / f"sentinel-{child}"
                target.mkdir()
                bus_child = task_dir / "reports" / child
                bus_child.rmdir()
                bus_child.symlink_to(target, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symlink|real directory"):
                    control.report_bus_paths(task_dir)
                self.assertEqual(list(target.iterdir()), [])

    def test_report_crash_points_recover_deterministically(self):
        original_exclusive_json = control.write_json_exclusive
        original_exclusive_text = control.write_text_exclusive
        original_json = control.write_json
        phases = ("markdown", "receipt", "index", "delegation")
        for phase_index, phase in enumerate(phases, 1):
            with self.subTest(phase=phase):
                task_id = f"crash-{phase}"
                state = self.init(task_id=task_id, complexity="C2")["state"]
                delegation = self.delegate(state, task_id, "plan", "planner")
                fired = {"value": False}

                def exclusive_text(path, text):
                    if phase == "markdown" and path.parent.name == "markdown" and not fired["value"]:
                        fired["value"] = True
                        raise OSError("simulated crash after record")
                    return original_exclusive_text(path, text)

                def exclusive_json(path, value):
                    if phase == "receipt" and path.parent.name == "receipts" and not fired["value"]:
                        fired["value"] = True
                        raise OSError("simulated crash after markdown")
                    return original_exclusive_json(path, value)

                def write_json(path, value):
                    target = (phase == "index" and path.name == "index.json" and path.parent.name == "reports") or (phase == "delegation" and path.name == "index.json" and path.parent.parent.name == "delegations")
                    if target and not fired["value"]:
                        fired["value"] = True
                        raise OSError("simulated crash after authoritative artifact")
                    return original_json(path, value)

                control.write_text_exclusive, control.write_json_exclusive, control.write_json = exclusive_text, exclusive_json, write_json
                try:
                    with self.assertRaises(OSError):
                        self.report(task_id, delegation["attempt_id"], submission_id="stable")
                finally:
                    control.write_text_exclusive, control.write_json_exclusive, control.write_json = original_exclusive_text, original_exclusive_json, original_json
                recovered = self.report(task_id, delegation["attempt_id"], submission_id="stable")
                self.assertTrue(recovered["idempotent"])
                self.assertEqual(recovered["report"]["report_id"], "report-0001")
                self.assertEqual(control.reconcile_report_bus({"task_id": task_id, "principal": "thread-a"})["report_count"], 1)

    def test_report_quotas_and_terminal_attempt_are_rejected(self):
        state = self.init(task_id="quotas", complexity="C2")["state"]
        delegation = self.delegate(state, "quotas", "plan", "planner")
        original_attempt, original_task, original_bytes, original_grants = control.MAX_REPORTS_PER_ATTEMPT, control.MAX_REPORTS_PER_TASK, control.MAX_REPORT_AGGREGATE_BYTES, control.MAX_REPORT_GRANTS
        try:
            control.MAX_REPORTS_PER_ATTEMPT = 1
            self.report("quotas", delegation["attempt_id"], submission_id="one")
            with self.assertRaisesRegex(ValueError, "quota"):
                self.report("quotas", delegation["attempt_id"], submission_id="two")
            control.MAX_REPORTS_PER_ATTEMPT = 10
            control.MAX_REPORTS_PER_TASK = 1
            with self.assertRaisesRegex(ValueError, "quota"):
                self.report("quotas", delegation["attempt_id"], submission_id="task-limit")
            control.MAX_REPORTS_PER_TASK = original_task
            control.MAX_REPORT_AGGREGATE_BYTES = 1
            with self.assertRaisesRegex(ValueError, "byte quota"):
                self.report("quotas", delegation["attempt_id"], submission_id="byte-limit")
            control.MAX_REPORT_GRANTS = 0
            with self.assertRaisesRegex(ValueError, "grant quota"):
                control.grant_report_context({"task_id": "quotas", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "report_ids": ["report-0001"], "reason": "quota"})
        finally:
            control.MAX_REPORTS_PER_ATTEMPT, control.MAX_REPORTS_PER_TASK, control.MAX_REPORT_AGGREGATE_BYTES, control.MAX_REPORT_GRANTS = original_attempt, original_task, original_bytes, original_grants
        report = control.record_report({"task_id": "quotas", "principal": "thread-a", "attempt_id": delegation["attempt_id"], "submission_id": "one", "report": {"summary": "delegated work complete", "findings": [], "questions": [], "changed_files": [], "tests": [], "evidence": ["focused test evidence"], "uncertainty": [], "next_action": "advance the gate"}})
        evidence = control.record_evidence({"task_id": "quotas", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "done"})
        control.record_gate({"task_id": "quotas", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed"})
        with self.assertRaisesRegex(ValueError, "terminal"):
            self.report("quotas", delegation["attempt_id"], submission_id="late")

    def test_journal_symlinks_cannot_modify_task_or_lane_state(self):
        state = self.init(task_id="journal", complexity="C1")["state"]
        state = control.record_gate({"task_id": "journal", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "outcome": "blocked"})["state"]
        task_dir = self.ledger / "tasks/0001-journal"
        sentinel = self.base / "sentinel"
        sentinel.write_text("unchanged", encoding="utf-8")
        (task_dir / "journal.md").unlink()
        (task_dir / "journal.md").symlink_to(sentinel)
        resumed = control.resume_task({"task_id": "journal", "principal": "thread-a", "expected_revision": state["revision"]})
        self.assertEqual(resumed["state"]["status"], "active")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
        lane = control.create_lane({"lane_id": "journal-lane", "principal": "thread-a"})
        lane_dir = self.ledger / "lanes/journal-lane"
        (lane_dir / "journal.md").unlink()
        (lane_dir / "journal.md").symlink_to(sentinel)
        claimed = control.claim_lane({"lane_id": "journal-lane", "principal": "thread-a", "expires_at": "2999-01-01T00:00:00+00:00"})
        self.assertIsNotNone(claimed["state"]["lease"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

    def test_metrics_reject_nonfinite_negative_and_wrong_numeric_types(self):
        state = self.init(task_id="metric-validation")["state"]
        for field, value in (("estimated_cost", math.nan), ("estimated_cost", math.inf), ("estimated_cost", -1), ("input_tokens", -1), ("output_tokens", 1.5), ("elapsed_ms", True)):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "nonnegative|finite"):
                control.record_metrics({"task_id": "metric-validation", "principal": "thread-a", "expected_revision": state["revision"], field: value})

    def test_metrics_telemetry_retains_bounded_tail_and_dropped_count(self):
        state = self.init(task_id="metric-retention")["state"]
        original_events, original_bytes = control.MAX_METRIC_EVENTS, control.MAX_METRIC_BYTES
        try:
            control.MAX_METRIC_EVENTS, control.MAX_METRIC_BYTES = 3, 100000
            for index in range(6):
                result = control.record_metrics({"task_id": "metric-retention", "principal": "thread-a", "expected_revision": state["revision"], "input_tokens": index, "verdict": f"v{index}"})
                state = result["state"]
        finally:
            control.MAX_METRIC_EVENTS, control.MAX_METRIC_BYTES = original_events, original_bytes
        metrics = json.loads((self.ledger / "tasks/0001-metric-retention/metrics.json").read_text(encoding="utf-8"))
        self.assertEqual([item["verdict"] for item in metrics["telemetry"]], ["v3", "v4", "v5"])
        self.assertEqual(metrics["telemetry_dropped"], 3)

    def test_bearer_and_uri_redaction(self):
        text = control.redact("Authorization: Bearer abc123 trailing-canary\n continuation-canary\nSafe: visible https://user:pass@example.test API_KEY='quoted-secret'")
        self.assertNotIn("abc123", text)
        self.assertNotIn("trailing-canary", text)
        self.assertNotIn("continuation-canary", text)
        self.assertNotIn("user:pass", text)
        self.assertNotIn("quoted-secret", text)
        self.assertIn("Safe: visible", text)

    def test_rework_gate_reassessment_applies(self):
        result = self.init(task_id="rework", complexity="C2")
        state = result["state"]
        delegation = self.delegate(state, "rework", "plan", "planner")
        report = self.report("rework", delegation["attempt_id"])
        evidence = control.record_evidence({"task_id": "rework", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "done"})
        closed = control.record_gate({"task_id": "rework", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed"})
        changed = control.reassess_pipeline({"task_id": "rework", "principal": "thread-a", "expected_revision": closed["state"]["revision"], "signals": [], "intent": "rework_gate", "gate": "plan", "decision": "updated", "reason": "new evidence", "apply": True})
        self.assertIn("plan", changed["state"]["current_pipeline"])
        self.assertNotIn("plan", changed["state"]["completed_gates"])

    def test_completion_unbinds_task_but_keeps_activation_until_normal(self):
        self.activate(principal="thread-finish")
        classified = control.classify_task({"complexity": "C1", "requirements": [], "thread_id": "thread-finish", "principal": "thread-finish"})
        result = control.init_task({"task_id": "finish", "objective": "finish", "complexity": "C1", "classification_id": classified["classification_id"], "thread_id": "thread-finish", "principal": "thread-finish"})
        state = result["state"]
        for gate in ["discover", "implementation", "review", "close"]:
            evidence = control.record_evidence({"task_id": "finish", "principal": "thread-finish", "expected_revision": state["revision"], "gate": gate, "summary": gate})
            state = control.record_gate({"task_id": "finish", "principal": "thread-finish", "expected_revision": evidence["state"]["revision"], "gate": gate, "outcome": "passed"})["state"]
        index = self.ledger / "active-tasks.json"
        self.assertFalse(index.exists())
        self.assertEqual(state["status"], "completed")
        activation = control.activation_status({"thread_id": "thread-finish", "principal": "thread-finish"})
        self.assertTrue(activation["active"])
        self.assertIsNone(activation["activation"]["task_id"])
        self.assertNotIn("initialized_at", activation["activation"])

        next_classification = control.classify_task({"complexity": "C1", "requirements": [], "thread_id": "thread-finish", "principal": "thread-finish"})
        next_task = control.init_task({"task_id": "next", "objective": "next", "complexity": "C1", "classification_id": next_classification["classification_id"], "thread_id": "thread-finish", "principal": "thread-finish"})
        self.assertTrue(next_task["created"])
        self.assertEqual(json.loads(index.read_text(encoding="utf-8")), {"thread-finish": "next"})

        control.deactivate_orchestration({"user_command": "/normal", "thread_id": "thread-finish", "principal": "thread-finish"})
        self.assertFalse(control.activation_status({"thread_id": "thread-finish", "principal": "thread-finish"})["active"])

    def test_resource_claim_expiry_and_metrics(self):
        result = self.init(task_id="resources", complexity="C1")
        state = result["state"]
        claim = control.claim_resource({"task_id": "resources", "principal": "thread-a", "expected_revision": state["revision"], "path": "port:4000", "owner": "worker", "expires_at": "2999-01-01T00:00:00+00:00"})
        metric = control.record_metrics({"task_id": "resources", "principal": "thread-a", "expected_revision": claim["state"]["revision"], "model": "gpt-5.6-terra", "reasoning_effort": "medium", "input_tokens": 10, "output_tokens": 20, "elapsed_ms": 30, "estimated_cost": 0.01, "verdict": "PASS"})
        self.assertFalse(metric["state"]["locks"][control.lock_key("port:4000")]["advisory"])
        self.assertEqual(control.status({"task_id": "resources", "principal": "thread-a"})["task"]["task_id"], "resources")

    def test_completion_bypasses_are_rejected(self):
        result = self.init(task_id="skip", complexity="C2")
        state = result["state"]
        with self.assertRaisesRegex(ValueError, "skip_reason"):
            control.record_gate({"task_id": "skip", "principal": "thread-a", "expected_revision": state["revision"], "gate": "plan", "outcome": "skipped"})
        with self.assertRaisesRegex(ValueError, "close gate"):
            control.update_pipeline({"task_id": "skip", "principal": "thread-a", "expected_revision": state["revision"], "pipeline": ["plan", "discover"]})

    def test_nonzero_command_cannot_pass(self):
        result = self.init(task_id="nonzero", complexity="C1")
        state = result["state"]
        evidence = control.record_evidence({"task_id": "nonzero", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "kind": "command", "command": "false", "exit_code": 1, "summary": "expected failure"})
        with self.assertRaisesRegex(ValueError, "self-attested"):
            control.record_gate({"task_id": "nonzero", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed"})

    def test_rework_invalidates_downstream_gates(self):
        result = self.init(task_id="downstream", complexity="C1")
        state = result["state"]
        for gate in ["discover", "implementation"]:
            evidence = control.record_evidence({"task_id": "downstream", "principal": "thread-a", "expected_revision": state["revision"], "gate": gate, "summary": gate})
            state = control.record_gate({"task_id": "downstream", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": gate, "outcome": "passed"})["state"]
        changed = control.update_pipeline({"task_id": "downstream", "principal": "thread-a", "expected_revision": state["revision"], "operations": [{"op": "rework", "gate": "discover"}], "allow_rework": True})
        self.assertNotIn("discover", changed["state"]["completed_gates"])
        self.assertNotIn("implementation", changed["state"]["completed_gates"])

    def test_handoff_is_structured_and_gate_summary_is_redacted(self):
        result = self.init(task_id="handoff", complexity="C1")
        state = result["state"]
        handoff = control.handoff({"task_id": "handoff", "principal": "thread-a", "expected_revision": state["revision"], "completed": ["work"], "next_action": "none"})
        self.assertTrue(handoff["state"]["handoff_created"])
        evidence = control.record_evidence({"task_id": "handoff", "principal": "thread-a", "expected_revision": handoff["state"]["revision"], "gate": "discover", "summary": "done"})
        closed = control.record_gate({"task_id": "handoff", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed", "summary": "client_secret: hidden"})
        self.assertNotIn("hidden", closed["state"]["gates"]["discover"]["summary"])

    def test_optional_lane_lifecycle_and_task_binding(self):
        self.activate()
        created = control.create_lane({"lane_id": "feature-lane", "principal": "thread-a", "mode": "persistent", "purpose": "long-lived workstream", "declarations": [{"repo": "app", "branch": "feature/x", "worktree": "/tmp/app-x"}]})
        self.assertEqual(created["state"]["mode"], "persistent")
        claimed = control.claim_lane({"lane_id": "feature-lane", "principal": "thread-a", "run_id": "run-1", "expires_at": "2999-01-01T00:00:00+00:00"})
        with self.assertRaisesRegex(ValueError, "live lease"):
            control.claim_lane({"lane_id": "feature-lane", "principal": "thread-a", "run_id": "run-2", "expires_at": "2999-01-01T00:00:00+00:00"})
        task = self.init(task_id="lane-task", complexity="C1")
        bound = control.bind_task_lane({"task_id": "lane-task", "lane_id": "feature-lane", "principal": "thread-a", "expected_revision": task["state"]["revision"]})
        resource = control.claim_lane_resource({"lane_id": "feature-lane", "principal": "thread-a", "path": "port:4311", "owner": "run-1", "kind": "port", "expires_at": "2999-01-01T00:00:00+00:00"})
        self.assertEqual(bound["state"]["lane_id"], "feature-lane")
        self.assertFalse(resource["advisory"])
        control.release_lane_resource({"lane_id": "feature-lane", "principal": "thread-a", "path": "port:4311", "owner": "run-1"})
        control.release_lane({"lane_id": "feature-lane", "principal": "thread-a", "run_id": "run-1"})
        retired = control.retire_lane({"lane_id": "feature-lane", "principal": "thread-a", "clean": True})
        self.assertEqual(retired["state"]["status"], "retired")

    def test_lane_expired_lease_requires_explicit_reclaim(self):
        self.activate()
        control.create_lane({"lane_id": "recover-lane", "principal": "thread-a"})
        root = self.ledger
        lane_state = root / "lanes" / "recover-lane" / "current.json"
        state = json.loads(lane_state.read_text())
        state["lease"] = {"owner": "thread-a", "run_id": "old", "expires_at": "2000-01-01T00:00:00+00:00"}
        lane_state.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "reclaim=true"):
            control.claim_lane({"lane_id": "recover-lane", "principal": "thread-a", "run_id": "new", "expires_at": "2999-01-01T00:00:00+00:00"})
        recovered = control.claim_lane({"lane_id": "recover-lane", "principal": "thread-a", "run_id": "new", "expires_at": "2999-01-01T00:00:00+00:00", "reclaim": True})
        self.assertTrue(recovered["reclaimed"])

    def test_lane_materialize_reconcile_and_clean_retirement(self):
        self.activate()
        repo = self.base / "source"
        worktree = self.base / "worktree"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "codex@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Codex Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("fixture\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
        created = control.create_lane({"lane_id": "materialized", "principal": "thread-a", "declarations": [{"repo_path": str(repo), "worktree_path": str(worktree), "branch": "feature/lane", "sync_from": "HEAD"}]})
        claimed = control.claim_lane({"lane_id": "materialized", "principal": "thread-a", "run_id": "run-1", "expires_at": "2999-01-01T00:00:00+00:00"})
        materialized = control.materialize_lane({"lane_id": "materialized", "principal": "thread-a", "run_id": "run-1", "confirm": True})
        self.assertTrue(worktree.exists())
        self.assertEqual(materialized["materializations"][0]["status"], "created")
        reconciled = control.reconcile_lane({"lane_id": "materialized", "principal": "thread-a", "run_id": "run-1"})
        self.assertEqual(reconciled["results"][0]["status"], "ok")
        control.release_lane({"lane_id": "materialized", "principal": "thread-a", "run_id": "run-1"})
        retired = control.retire_lane({"lane_id": "materialized", "principal": "thread-a", "clean": True, "confirm": True})
        self.assertEqual(retired["state"]["status"], "retired")
        self.assertFalse(worktree.exists())

    def test_concurrent_mutations_are_serialized(self):
        result = self.init()
        errors = []

        def add_evidence(index):
            try:
                control.record_evidence({"task_id": "demo", "principal": "thread-a", "gate": "discover", "summary": f"worker {index}"})
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=add_evidence, args=(index,)) for index in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(errors, [])
        state = control.status({"task_id": "demo", "principal": "thread-a"})["state"]
        self.assertEqual(len(state["evidence"]), 8)
        self.assertEqual(state["revision"], result["state"]["revision"] + 8)

    def test_symlink_root_and_invalid_id_are_rejected(self):
        self.activate()
        with self.assertRaisesRegex(ValueError, "identifier"):
            control.init_task({"task_id": "../escape", "objective": "bad", "complexity": "C1"})
        linked = self.base / "linked"
        linked.symlink_to(self.project, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "must not traverse symlinks"):
            control.ledger_root({"project_root": str(linked)})
        os.environ["CORTEX_ROOT"] = str(self.base / "external-ledger")
        try:
            with self.assertRaisesRegex(ValueError, "CORTEX_ROOT is not supported"):
                control.ledger_root({"project_root": str(self.project)})
        finally:
            os.environ.pop("CORTEX_ROOT", None)

    def test_mcp_smoke_exposes_new_tools(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        proc = subprocess.run([sys.executable, str(script)], input='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n', text=True, capture_output=True, check=True)
        tools = json.loads(proc.stdout)["result"]["tools"]
        names = {item["name"] for item in tools}
        self.assertTrue({
            "record_evidence",
            "execute_verification_command",
            "reconcile_project_files",
            "resume_task",
            "cortex.question",
            "publish_worker_question",
            "list_worker_questions",
            "answer_worker_question",
            "get_worker_question_updates",
            "prepare_delegation",
            "prepare_delegations",
            "complete_attempt",
            "commit_gate",
            "close_audit",
        }.issubset(names))
        self.assertEqual(len(tools), 48)
        self.assertTrue(all("project_root" in item["inputSchema"]["properties"] for item in tools))
        activation = next(item for item in tools if item["name"] == "activate_orchestration")
        self.assertIn("project_root", activation["inputSchema"]["required"])
        report_tool = next(item for item in tools if item["name"] == "record_report")
        self.assertNotIn("attempt_id", report_tool["inputSchema"]["required"])
        self.assertNotIn("submission_id", report_tool["inputSchema"]["required"])

    def test_mcp_rejects_root_fallbacks_and_reports_canonical_ledger(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"

        def call(arguments, environment=None):
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "activate_orchestration", "arguments": arguments}}
            completed = subprocess.run([sys.executable, str(script)], input=json.dumps(request) + "\n", text=True, capture_output=True, env=environment, check=True)
            return json.loads(completed.stdout)

        common = {"user_command": "/cortex", "principal": "mcp", "thread_id": "mcp"}
        self.assertIn("project_root is required", call(common)["error"]["message"])
        self.assertIn("absolute path", call({**common, "project_root": "relative"})["error"]["message"])
        external = self.base / "external-ledger"
        environment = os.environ.copy()
        environment["CORTEX_ROOT"] = str(external)
        rejected = call({**common, "project_root": str(self.project)}, environment)
        self.assertIn("CORTEX_ROOT is not supported", rejected["error"]["message"])
        self.assertFalse(external.exists())
        accepted = call({**common, "project_root": str(self.project)})["result"]["structuredContent"]
        self.assertEqual(accepted["ledger_root"], str(self.ledger))

    def test_mcp_process_rejects_project_root_switch(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        other = self.base / "other-project"
        other.mkdir()
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_activation_status", "arguments": {"project_root": str(self.project), "principal": "mcp"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_activation_status", "arguments": {"principal": "mcp"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_activation_status", "arguments": {"project_root": str(other), "principal": "mcp"}}},
        ]
        completed = subprocess.run([sys.executable, str(script)], input="".join(json.dumps(item) + "\n" for item in requests), text=True, capture_output=True, check=True)
        first, omitted, second = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(first["result"]["structuredContent"]["ledger_root"], str(self.ledger))
        self.assertEqual(omitted["result"]["structuredContent"]["ledger_root"], str(self.ledger))
        self.assertIn("already bound", second["error"]["message"])
        self.assertFalse((other / ".codex/cortex").exists())


if __name__ == "__main__":
    unittest.main()
