import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex as control
import cortex_hook


class OrchestrationInvariantTests(unittest.TestCase):
    GENERATED_START = "<!-- GENERATED:START -->"
    GENERATED_END = "<!-- GENERATED:END -->"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.ledger = self.project / ".codex" / "cortex"
        os.environ["CORTEX_PROJECT_ROOT"] = str(self.project)
        self._handlers = {}
        for handler, _ in control.TOOLS.values():
            name = handler.__name__
            if name in self._handlers:
                continue
            original = getattr(control, name)
            self._handlers[name] = original
            setattr(control, name, lambda params, original=original: original({**params, "project_root": str(self.project)}))
        control.activate_orchestration({"user_command": "/cortex", "principal": "owner", "thread_id": "owner"})

    def tearDown(self):
        for name, handler in self._handlers.items():
            setattr(control, name, handler)
        os.environ.pop("CORTEX_PROJECT_ROOT", None)
        self.temp.cleanup()

    def init(self, task_id="task", complexity="C1", requirements=None):
        requirements = requirements or []
        classified = control.classify_task({"complexity": complexity, "requirements": requirements, "principal": "owner"})
        return control.init_task({"task_id": task_id, "objective": "invariant test", "complexity": complexity, "classification_id": classified["classification_id"], "requirements": requirements, "principal": "owner", "thread_id": "owner"})

    def delegate(self, state, task_id, gate, agent="general"):
        observed = control.status({"task_id": task_id, "principal": "owner"})
        delegated = control.record_delegation({"task_id": task_id, "principal": "owner", "expected_revision": state["revision"], "status_receipt": observed["status_receipt"], "gate": gate, "agent": agent, "task_kind": gate, "risk": "moderate", "requested_model": "gpt-5.6-terra", "requested_reasoning_effort": "medium", "objective": "bounded work", "ownership": f"Own {gate}", "allowed_paths": ["."], "acceptance_criteria": [f"Complete {gate}"], "verification": ["Report evidence"]})
        confirmed = control.confirm_host_spawn({
            "task_id": task_id, "principal": "owner", "expected_revision": delegated["state"]["revision"],
            "attempt_id": delegated["attempt_id"], "host_agent_id": f"test-host-{delegated['attempt_id']}",
            "host_task_name": delegated["spawn_request"]["task_name"],
            "host_model": delegated["spawn_request"]["model"],
        })
        return {**delegated, "state": confirmed["state"], "host_spawn": confirmed["host_spawn"]}

    def report(self, task_id, attempt_id, submission_id="final"):
        return control.record_report({"task_id": task_id, "principal": "owner", "attempt_id": attempt_id, "submission_id": submission_id, "report": {"summary": "work complete", "findings": [], "questions": [], "changed_files": [], "tests": [], "evidence": ["focused evidence"], "uncertainty": [], "next_action": "advance"}})

    @classmethod
    def replace_generated_facts(cls, content, facts):
        start = content.index(cls.GENERATED_START) + len(cls.GENERATED_START)
        end = content.index(cls.GENERATED_END, start)
        return content[:start] + "\n" + facts.rstrip() + "\n" + content[end:]

    @staticmethod
    def cortex_routes(skill_text):
        routes = {}
        for line in skill_text.splitlines():
            if not line.startswith("| `"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) == 3 and cells[0].startswith("`") and cells[1].startswith("`"):
                routes[cells[0].strip("`")] = cells[1].strip("`")
        return routes

    def test_classification_is_required_and_bound_to_inputs(self):
        with self.assertRaisesRegex(ValueError, "prior classify_task"):
            control.init_task({"task_id": "missing", "objective": "x", "complexity": "C1", "principal": "owner"})
        classified = control.classify_task({"complexity": "C2", "requirements": ["docs"], "principal": "owner"})
        created = control.init_task({"task_id": "mismatch", "objective": "x", "complexity": "C3", "classification_id": classified["classification_id"], "requirements": ["security"], "principal": "owner"})
        self.assertEqual(created["state"]["complexity"], "C2")

    def test_status_authorization_and_stale_dispatch_inputs_are_recoverable(self):
        state = self.init()["state"]
        with self.assertRaisesRegex(ValueError, "different principal"):
            control.status({"task_id": "task", "principal": "intruder"})
        observed = control.status({"task_id": "task", "principal": "owner"})
        arguments = {"task_id": "task", "principal": "owner", "expected_revision": state["revision"], "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer", "task_kind": "discover", "risk": "low", "objective": "inspect", "ownership": "Read-only discovery", "allowed_paths": ["."], "acceptance_criteria": ["Report findings"], "verification": ["Cite inspected paths"]}
        delegated = control.record_delegation(arguments)
        recovered = control.record_delegation({**arguments, "expected_revision": delegated["state"]["revision"] + 2})
        self.assertTrue(recovered["receipt_correction"])
        self.assertEqual(recovered["revision_correction"], {
            "requested": delegated["state"]["revision"] + 2,
            "used": delegated["state"]["revision"],
        })

    def test_active_activation_can_infer_omitted_identity_but_unbound_task_cannot(self):
        self.init()
        observed = control.status({"task_id": "task"})
        self.assertTrue(observed["active"])
        # A correctly bound thread is a safe identity recovery path. It must
        # not be mistaken for the task owner/principal string.
        by_thread = control.status({"task_id": "task", "thread_id": "owner"})
        self.assertTrue(by_thread["active"])
        with self.assertRaisesRegex(ValueError, "different thread"):
            control.status({"task_id": "task", "thread_id": "other-thread"})
        with self.assertRaisesRegex(ValueError, "different principal"):
            control.status({"task_id": "task", "principal": "intruder"})

    def test_confirm_host_spawn_recovers_stale_revision(self):
        state = self.init()["state"]
        observed = control.status({"task_id": "task", "principal": "owner"})
        delegated = control.record_delegation({
            "task_id": "task", "principal": "owner",
            "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"],
            "gate": "plan", "agent": "planner", "task_kind": "planning", "risk": "low",
            "objective": "bounded work", "ownership": "Own plan",
            "allowed_paths": ["."], "acceptance_criteria": ["Complete plan"],
            "verification": ["Report evidence"],
        })
        confirmed = control.confirm_host_spawn({
            "task_id": "task",
            "attempt_id": delegated["attempt_id"],
            "expected_revision": state["revision"],
            "host_agent_id": "test-host-stale-revision",
            "host_task_name": delegated["spawn_request"]["task_name"],
            "host_model": delegated["spawn_request"]["model"],
        })
        self.assertEqual(confirmed["revision_correction"], {
            "requested": state["revision"],
            "used": confirmed["state"]["revision"] - 1,
        })
        self.assertEqual(confirmed["state"]["attempts"][-1]["status"], "running")

    def test_manifest_reconciles_every_change_without_truncation(self):
        (self.project / "modified.txt").write_text("before\n", encoding="utf-8")
        (self.project / "deleted.txt").write_text("delete\n", encoding="utf-8")
        (self.project / "old.txt").write_text("rename\n", encoding="utf-8")
        state = self.init()["state"]
        (self.project / "modified.txt").write_text("after\n", encoding="utf-8")
        (self.project / "deleted.txt").unlink()
        (self.project / "old.txt").rename(self.project / "new.txt")
        added = []
        for index in range(125):
            name = f"added-{index:03d}.txt"
            (self.project / name).write_text(str(index), encoding="utf-8")
            added.append(name)
        partial = control.reconcile_project_files({"task_id": "task", "principal": "owner", "expected_revision": state["revision"], "paths": ["modified.txt"]})
        self.assertFalse(partial["receipt"]["complete"])
        self.assertGreater(len(partial["receipt"]["unaccounted_paths"]), 100)
        complete_paths = ["modified.txt", "deleted.txt", "old.txt", "new.txt", *added]
        complete = control.reconcile_project_files({"task_id": "task", "principal": "owner", "expected_revision": partial["state"]["revision"], "paths": complete_paths})
        self.assertTrue(complete["receipt"]["complete"])
        self.assertEqual(complete["receipt"]["comparison"]["change_count"], 128)
        incomplete = control.handoff({"task_id": "task", "principal": "owner", "expected_revision": complete["state"]["revision"], "completed": ["changes"], "files": ["modified.txt"], "next_action": "continue"})
        self.assertFalse(incomplete["recorded"])
        self.assertTrue(incomplete["recoverable"])
        self.assertEqual(incomplete["next_action"], "retry_create_handoff_with_complete_files")
        self.assertEqual(incomplete["required_fields"], ["files"])
        self.assertGreater(len(incomplete["unaccounted_paths"]), 100)
        self.assertFalse(incomplete["state"]["handoff_created"])
        handed = control.handoff({"task_id": "task", "principal": "owner", "expected_revision": complete["state"]["revision"], "completed": ["changes"], "files": complete_paths, "next_action": "continue"})
        self.assertEqual(len(handed["file_manifest_receipt"]["reported_paths"]), len(complete_paths))

    def test_c2_pipeline_requires_documentation(self):
        created = self.init(complexity="C2")
        pipeline = created["state"]["current_pipeline"]
        self.assertIn("documentation", pipeline)
        self.assertLess(pipeline.index("documentation"), pipeline.index("close"))
        with self.assertRaisesRegex(ValueError, "retain documentation"):
            control.update_pipeline({"task_id": "task", "principal": "owner", "expected_revision": created["state"]["revision"], "pipeline": [gate for gate in pipeline if gate != "documentation"]})

    def test_completion_requires_terminal_attempts_with_reports_and_evidence(self):
        state = {
            "current_pipeline": ["documentation", "close"],
            "require_handoff": True,
            "completed_gates": ["documentation", "close"],
            "documentation_receipt": {"evidence_id": "evidence-doc"},
            "reassessment_receipts": [{"receipt_id": "reassessment-1"}],
            "handoff_created": True,
            "handoff_gate": "close",
            "attempts": [{"attempt_id": "close-01", "status": "passed"}],
            "evidence": [{
                "evidence_id": "evidence-close",
                "attempt_id": "close-01",
                "report_id": "report-0001",
                "report_receipt": "report-receipt-report-0001",
            }],
        }
        control.validate_completion_invariants(state)

        running = {**state, "attempts": [{"attempt_id": "close-01", "status": "running"}]}
        with self.assertRaisesRegex(ValueError, "every attempt to be terminal: close-01"):
            control.validate_completion_invariants(running)

        missing_evidence = {**state, "evidence": []}
        with self.assertRaisesRegex(ValueError, "evidence for every attempt: close-01"):
            control.validate_completion_invariants(missing_evidence)

        missing_report = {**state, "evidence": [{"evidence_id": "evidence-close", "attempt_id": "close-01"}]}
        with self.assertRaisesRegex(ValueError, "consumed report receipt for every attempt: close-01"):
            control.validate_completion_invariants(missing_report)

        terminal_failure_without_report = {
            **state,
            "attempts": [{"attempt_id": "close-01", "status": "failed", "finalization_reason": "worker exited"}],
            "evidence": [],
        }
        control.validate_completion_invariants(terminal_failure_without_report)

    def test_global_claims_collide_across_tasks_and_lanes(self):
        first = self.init(task_id="first")["state"]
        claimed = control.claim_resource({"task_id": "first", "principal": "owner", "expected_revision": first["revision"], "path": "port:8443", "owner": "worker-a", "expires_at": "2999-01-01T00:00:00+00:00"})
        control.activate_orchestration({"user_command": "/cortex", "principal": "owner", "thread_id": "owner"})
        second = self.init(task_id="second")["state"]
        with self.assertRaisesRegex(ValueError, "globally"):
            control.claim_resource({"task_id": "second", "principal": "owner", "expected_revision": second["revision"], "path": "port:8443", "owner": "worker-b", "expires_at": "2999-01-01T00:00:00+00:00"})
        control.activate_orchestration({"user_command": "/cortex", "principal": "owner", "thread_id": "owner"})
        control.release_resource({"task_id": "first", "principal": "owner", "expected_revision": claimed["state"]["revision"], "path": "port:8443", "owner": "worker-a"})
        lane = control.create_lane({"lane_id": "lane", "principal": "owner"})
        control.claim_lane_resource({"lane_id": "lane", "principal": "owner", "path": "port:8443", "owner": "worker-c", "expires_at": "2999-01-01T00:00:00+00:00"})

    def test_existing_worktree_attachment_is_never_retired(self):
        repository = self.base / "repository"
        worktree = self.base / "existing-worktree"
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.email", "codex@example.invalid"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.name", "Codex Test"], cwd=repository, check=True)
        (repository / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=repository, check=True)
        subprocess.run(["git", "worktree", "add", "-qb", "feature/existing", str(worktree)], cwd=repository, check=True)
        control.create_lane({"lane_id": "existing", "principal": "owner", "declarations": [{"repo_path": str(repository), "worktree_path": str(worktree), "branch": "feature/existing"}]})
        control.claim_lane({"lane_id": "existing", "principal": "owner", "run_id": "run", "expires_at": "2999-01-01T00:00:00+00:00"})
        materialized = control.materialize_lane({"lane_id": "existing", "principal": "owner", "run_id": "run", "confirm": True})
        self.assertFalse(materialized["materializations"][0]["managed"])
        control.release_lane({"lane_id": "existing", "principal": "owner", "run_id": "run"})
        control.retire_lane({"lane_id": "existing", "principal": "owner", "clean": True, "confirm": True})
        self.assertTrue(worktree.is_dir())

    def test_lane_reads_and_task_binding_require_lane_owner(self):
        control.activate_orchestration({"user_command": "/cortex", "principal": "other", "thread_id": "other"})
        control.create_lane({"lane_id": "other-lane", "principal": "other"})
        with self.assertRaisesRegex(ValueError, "different principal"):
            control.lane_status({"lane_id": "other-lane", "principal": "owner"})
        task = self.init(task_id="owned-task")["state"]
        with self.assertRaisesRegex(ValueError, "different principal"):
            control.bind_task_lane({"task_id": "owned-task", "lane_id": "other-lane", "principal": "owner", "expected_revision": task["revision"]})

    def test_structured_secrets_are_redacted(self):
        sanitized = control.sanitize_structured({"client_secret": "never-store-this", "nested": {"access_token": "also-secret"}, "safe": "visible"})
        self.assertEqual(sanitized["client_secret"], "<REDACTED>")
        self.assertEqual(sanitized["nested"]["access_token"], "<REDACTED>")
        self.assertEqual(sanitized["safe"], "visible")

    def test_verification_command_ids_reject_caller_execution_control(self):
        state = self.init()["state"]
        base = {"task_id": "task", "principal": "owner", "expected_revision": state["revision"], "gate": "discover", "summary": "negative", "verification_id": "benign_success"}
        for injected in (
            {"argv": ["/bin/sh", "-c", "id"]},
            {"executable": "/usr/bin/python3"},
            {"shell": "/bin/bash"},
            {"cwd": "../sibling"},
            {"env": {"HOME": "/private"}},
        ):
            with self.subTest(injected=injected), self.assertRaisesRegex(ValueError, "caller-selected"):
                control.execute_verification({**base, **injected})
        with self.assertRaisesRegex(ValueError, "unknown verification_id"):
            control.execute_verification({**base, "verification_id": "undeclared"})
        success = control.execute_verification(base)
        self.assertEqual(success["execution"]["exit_code"], 0)

    def test_malformed_sol_escalation_is_rejected_as_value_error(self):
        with self.assertRaisesRegex(ValueError, "sol_escalation.kind must be a string"):
            control.resolve_dispatch_route({
                "project_root": str(self.project),
                "agent": "general",
                "task_kind": "implementation",
                "risk": "moderate",
                "sol_escalation": {"kind": {}},
            })

    def test_record_gate_cannot_remove_mandatory_c2_gates(self):
        state = self.init(complexity="C2")["state"]
        delegation = self.delegate(state, "task", "plan", "planner")
        report = self.report("task", delegation["attempt_id"])
        evidence = control.record_evidence({"task_id": "task", "principal": "owner", "expected_revision": delegation["state"]["revision"], "gate": "plan", "attempt_id": delegation["attempt_id"], "report_receipt": report["receipt"]["receipt_id"], "summary": "planned"})
        with self.assertRaisesRegex(ValueError, "retain documentation"):
            control.record_gate({"task_id": "task", "principal": "owner", "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed", "pipeline_operations": [{"op": "remove", "gate": "documentation"}, {"op": "remove", "gate": "close"}]})

    def test_c2_rework_uses_only_current_attempt_evidence(self):
        state = self.init(complexity="C2")["state"]
        first = self.delegate(state, "task", "plan", "planner")
        first_report = self.report("task", first["attempt_id"], "first")
        evidence = control.record_evidence({"task_id": "task", "principal": "owner", "expected_revision": first["state"]["revision"], "gate": "plan", "attempt_id": first["attempt_id"], "report_receipt": first_report["receipt"]["receipt_id"], "summary": "first plan"})
        passed = control.record_gate({"task_id": "task", "principal": "owner", "expected_revision": evidence["state"]["revision"], "gate": "plan", "outcome": "passed"})
        reworked = control.reassess_pipeline({"task_id": "task", "principal": "owner", "expected_revision": passed["state"]["revision"], "signals": [], "intent": "rework_gate", "gate": "plan", "decision": "updated", "reason": "plan changed", "apply": True})
        second = self.delegate(reworked["state"], "task", "plan", "planner")
        second_report = self.report("task", second["attempt_id"], "second")
        second_evidence = control.record_evidence({"task_id": "task", "principal": "owner", "expected_revision": second["state"]["revision"], "gate": "plan", "attempt_id": second["attempt_id"], "report_receipt": second_report["receipt"]["receipt_id"], "summary": "replacement plan"})
        repassed = control.record_gate({"task_id": "task", "principal": "owner", "expected_revision": second_evidence["state"]["revision"], "gate": "plan", "outcome": "passed"})
        self.assertEqual(repassed["state"]["current_gate"], "discover")

    def test_stop_reassessment_requires_current_handoff(self):
        state = self.init(complexity="C2")["state"]
        params = {"task_id": "task", "principal": "owner", "expected_revision": state["revision"], "signals": ["blocked"], "intent": "stop", "decision": "stop", "reason": "external blocker"}
        with self.assertRaisesRegex(ValueError, "requires a current-gate handoff"):
            control.reassess_pipeline(params)
        handed = control.handoff({"task_id": "task", "principal": "owner", "expected_revision": state["revision"], "completed": ["investigation"], "files": [], "next_action": "wait"})
        stopped = control.reassess_pipeline({**params, "expected_revision": handed["state"]["revision"]})
        self.assertEqual(stopped["state"]["status"], "blocked")

    def test_materialization_preflight_prevents_partial_worktrees(self):
        repository = self.base / "preflight-repo"
        first_worktree = self.base / "first-worktree"
        missing_repo = self.base / "missing-repo"
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.email", "codex@example.invalid"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.name", "Codex Test"], cwd=repository, check=True)
        (repository / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=repository, check=True)
        control.create_lane({"lane_id": "preflight", "principal": "owner", "declarations": [
            {"repo_path": str(repository), "worktree_path": str(first_worktree), "branch": "feature/first", "sync_from": "HEAD"},
            {"repo_path": str(missing_repo), "worktree_path": str(self.base / "second-worktree"), "branch": "feature/second", "sync_from": "HEAD"},
        ]})
        control.claim_lane({"lane_id": "preflight", "principal": "owner", "run_id": "run", "expires_at": "2999-01-01T00:00:00+00:00"})
        with self.assertRaisesRegex(ValueError, "does not exist"):
            control.materialize_lane({"lane_id": "preflight", "principal": "owner", "run_id": "run", "confirm": True})
        self.assertFalse(first_worktree.exists())
        self.assertEqual(control.lane_status({"lane_id": "preflight", "principal": "owner"})["state"]["materializations"], [])

    def test_expired_lane_resource_does_not_block_retirement(self):
        control.create_lane({"lane_id": "expired-resource", "principal": "owner"})
        control.claim_lane_resource({"lane_id": "expired-resource", "principal": "owner", "path": "port:9000", "owner": "worker", "expires_at": "2999-01-01T00:00:00+00:00"})
        state_path = self.ledger / "lanes" / "expired-resource" / "current.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        next(iter(state["resources"].values()))["expires_at"] = "2000-01-01T00:00:00+00:00"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        retired = control.retire_lane({"lane_id": "expired-resource", "principal": "owner", "clean": True})
        self.assertEqual(retired["state"]["status"], "retired")

    def test_resource_identifiers_and_owners_never_persist_credentials(self):
        state = self.init()["state"]
        resource = "https://user:password@example.invalid/private"
        owner = "token=owner-secret"
        claimed = control.claim_resource({"task_id": "task", "principal": "owner", "expected_revision": state["revision"], "path": resource, "owner": owner, "expires_at": "2999-01-01T00:00:00+00:00"})
        serialized = json.dumps(claimed["state"])
        self.assertNotIn("password", serialized)
        self.assertNotIn("owner-secret", serialized)
        self.assertNotIn(resource, serialized)
        self.assertIn(control.lock_key(resource), claimed["state"]["locks"])

    def test_tool_schemas_require_runtime_auth_and_fields(self):
        for name in control.AUTHORIZED_TOOLS:
            self.assertIn("principal", control.TOOLS[name][1]["required"], name)
        required = {
            "activate_orchestration": {"user_command", "thread_id", "principal"},
            "deactivate_orchestration": {"user_command"},
            "record_delegation": set(),
            "confirm_host_spawn": {"attempt_id", "host_agent_id", "host_task_name"},
            "finalize_attempt": {"attempt_id", "status"},
            "cortex.question": {"task_id"},
            "publish_worker_question": {"attempt_id", "submission_id", "question"},
            "answer_worker_question": {"question_id", "submission_id", "answer", "resume_context"},
            "get_worker_question_updates": {"attempt_id"},
            "execute_verification_command": {"verification_id"},
            "create_handoff": {"completed", "next_action"},
            "claim_resource": {"expires_at"},
            "claim_lane": {"expires_at"},
            "claim_lane_resource": {"expires_at"},
            "retire_lane": {"confirm"},
        }
        for name, fields in required.items():
            self.assertTrue(fields.issubset(control.TOOLS[name][1]["required"]), name)
        verification_properties = control.TOOLS["execute_verification_command"][1]["properties"]
        self.assertFalse({"argv", "cwd", "env", "shell", "executable"} & set(verification_properties))

    def test_sync_detects_and_repairs_same_version_plugin_content_drift(self):
        if not shutil.which("codex"):
            self.skipTest("codex CLI is unavailable")
        isolated = self.base / "sync-home"
        codex_home = isolated / ".codex"
        codex_home.mkdir(parents=True)
        config = codex_home / "config.toml"
        config.write_text(
            '[plugins."cortex@cortex".mcp_servers.cortex]\n'
            'default_tools_approval_mode = "approve"\n',
            encoding="utf-8",
        )
        retired = codex_home / "agents" / "orchestrator.toml"
        retired.parent.mkdir()
        retired.write_bytes(base64.b64decode("bmFtZSA9ICJvcmNoZXN0cmF0b3IiCmRlc2NyaXB0aW9uID0gIkRlbGVnYXRpb24tb25seSBjb25kdWN0b3IgZm9yIHJvdXRpbmcgd29yayB0byBzcGVjaWFsaXN0IGFnZW50cyBhbmQgbWFuYWdpbmcgb3JjaGVzdHJhdGlvbiBzdGF0ZS4iCnNhbmRib3hfbW9kZSA9ICJyZWFkLW9ubHkiCmRldmVsb3Blcl9pbnN0cnVjdGlvbnMgPSAiIiIKWW91IGFyZSB0aGUgb3JjaGVzdHJhdGlvbiBjb25kdWN0b3IsIG5vdCBhbiBpbXBsZW1lbnRhdGlvbiBvciBpbnZlc3RpZ2F0aW9uIGFnZW50LgpEbyBub3QgaW5zcGVjdCwgc2VhcmNoLCByZWFkLCB0ZXN0LCBidWlsZCwgb3IgZWRpdCB0aGUgdGFyZ2V0IHByb2plY3QgeW91cnNlbGYuClVzZSBvbmx5IG9yY2hlc3RyYXRpb24gY29udHJvbCwgYWdlbnQgZGlzcGF0Y2gsIGFnZW50IG1lc3NhZ2luZywgdGFzayBzdGF0dXMsCmdhdGUsIGV2aWRlbmNlLCBhbmQgaGFuZG9mZiBvcGVyYXRpb25zLiBDb252ZXJ0IHRoZSB1c2VyJ3MgcmVxdWVzdCBpbnRvCmJvdW5kZWQgZGVsZWdhdGlvbnMgd2l0aCBleHBsaWNpdCBvd25lcnNoaXAsIGFsbG93ZWQgcGF0aHMsIGFjY2VwdGFuY2UKY3JpdGVyaWEsIGFuZCB2ZXJpZmljYXRpb24gcmVzcG9uc2liaWxpdGllcy4gV2FpdCBmb3Igd29ya2VyIHJlcG9ydHMsIHJvdXRlCmZvbGxvdy11cCB3b3JrLCBhZHZhbmNlIGdhdGVzIGZyb20gcmVjb3JkZWQgZXZpZGVuY2UsIGFuZCBzdXJmYWNlIGJsb2NrZXJzLgpOZXZlciBjb21wZW5zYXRlIGZvciBhIG1pc3Npbmcgd29ya2VyIHJlc3VsdCBieSBleGFtaW5pbmcgb3IgY2hhbmdpbmcgdGhlCnJlcG9zaXRvcnkgeW91cnNlbGYuIFRoZSBmaW5hbCByZXNwb25zZSBtdXN0IHN1bW1hcml6ZSB3b3JrZXIgZXZpZGVuY2UgYW5kCnJlbWFpbmluZyByaXNrLCBub3QgY2xhaW0gbG9jYWxseSBwZXJmb3JtZWQgd29yay4KIiIiCg=="))
        retired_cache = codex_home / "plugins/cache/personal/codex-orchestration-control/4.4.0"
        (retired_cache / ".codex-plugin").mkdir(parents=True)
        (retired_cache / "scripts").mkdir()
        (retired_cache / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "codex-orchestration-control", "version": "4.4.0"}), encoding="utf-8")
        (retired_cache / "scripts/orchestration_control.py").write_text('SERVER_VERSION = "4.4.0"\n', encoding="utf-8")
        environment = os.environ.copy()
        environment.update({"HOME": str(isolated), "CODEX_HOME": str(codex_home)})
        script = Path(__file__).parents[1] / "scripts/sync-cortex.sh"
        before_preview = config.read_text(encoding="utf-8")
        preview = subprocess.run(["bash", str(script), "--dry-run"], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn("would preserve Cortex MCP default_tools_approval_mode=approve", preview.stdout)
        self.assertIn("would set agents.default_subagent_model=gpt-5.6-luna", preview.stdout)
        self.assertEqual(config.read_text(encoding="utf-8"), before_preview)
        installed = subprocess.run(["bash", str(script)], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertIn('[plugins."cortex@cortex".mcp_servers.cortex]', config.read_text(encoding="utf-8"))
        self.assertIn('default_tools_approval_mode = "approve"', config.read_text(encoding="utf-8"))
        self.assertIn('[agents]', config.read_text(encoding="utf-8"))
        self.assertIn('default_subagent_model = "gpt-5.6-luna"', config.read_text(encoding="utf-8"))
        self.assertFalse(retired.exists())
        self.assertFalse(retired_cache.parent.exists())
        backup_root = codex_home / "backups/cortex-upgrade"
        self.assertTrue(backup_root.is_dir())
        for backup_path in [backup_root, *backup_root.rglob("*")]:
            self.assertEqual(backup_path.stat().st_mode & 0o077, 0, backup_path)
        cache = codex_home / "plugins/cache/cortex/cortex" / control.SERVER_VERSION / "scripts/cortex.py"
        cache.write_text(cache.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
        drift = subprocess.run(["bash", str(script), "--check"], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertNotEqual(drift.returncode, 0)
        self.assertIn("content drift", drift.stdout)
        repaired = subprocess.run(["bash", str(script)], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertIn('default_tools_approval_mode = "approve"', config.read_text(encoding="utf-8"))
        self.assertIn('default_subagent_model = "gpt-5.6-luna"', config.read_text(encoding="utf-8"))
        checked = subprocess.run(["bash", str(script), "--check"], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_sync_refuses_symlinked_codex_home_ancestry_without_touching_sentinel(self):
        isolated = self.base / "symlink-home"
        outside = self.base / "outside"
        isolated.mkdir()
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("unchanged\n", encoding="utf-8")
        (isolated / ".codex").symlink_to(outside, target_is_directory=True)
        environment = os.environ.copy()
        environment.update({"HOME": str(isolated), "CODEX_HOME": str(isolated / ".codex")})
        script = Path(__file__).parents[1] / "scripts/sync-cortex.sh"
        completed = subprocess.run(["bash", str(script), "--dry-run"], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("must not traverse symlinks", completed.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged\n")
        self.assertEqual(sorted(path.name for path in outside.iterdir()), ["sentinel.txt"])

    def test_sync_refuses_symlinked_global_config_without_touching_target(self):
        isolated = self.base / "symlink-config-home"
        codex_home = isolated / ".codex"
        codex_home.mkdir(parents=True)
        target = isolated / "outside-config.toml"
        target.write_text(
            '[agents]\n'
            'default_subagent_model = "gpt-5.6-terra"\n',
            encoding="utf-8",
        )
        (codex_home / "config.toml").symlink_to(target)
        environment = os.environ.copy()
        environment.update({"HOME": str(isolated), "CODEX_HOME": str(codex_home)})
        script = Path(__file__).parents[1] / "scripts/sync-cortex.sh"
        completed = subprocess.run(["bash", str(script), "--dry-run"], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("refusing to inspect non-regular Codex config", completed.stderr)
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            '[agents]\ndefault_subagent_model = "gpt-5.6-terra"\n',
        )

    def test_sync_backs_up_and_replaces_a_different_global_subagent_model(self):
        if not shutil.which("codex"):
            self.skipTest("codex CLI is unavailable")
        isolated = self.base / "explicit-model-home"
        codex_home = isolated / ".codex"
        codex_home.mkdir(parents=True)
        config = codex_home / "config.toml"
        config.write_text(
            '[agents]\n'
            'enabled = true\n'
            'default_subagent_model = "gpt-5.6-terra" # keep this comment\n',
            encoding="utf-8",
        )
        config.chmod(0o640)
        original = config.read_text(encoding="utf-8")
        environment = os.environ.copy()
        environment.update({"HOME": str(isolated), "CODEX_HOME": str(codex_home)})
        script = Path(__file__).parents[1] / "scripts/sync-cortex.sh"
        preview = subprocess.run(["bash", str(script), "--dry-run"], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn("would back up config and replace agents.default_subagent_model=gpt-5.6-terra with gpt-5.6-luna", preview.stdout)
        self.assertEqual(config.read_text(encoding="utf-8"), original)
        installed = subprocess.run(["bash", str(script)], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        updated = config.read_text(encoding="utf-8")
        self.assertIn('default_subagent_model = "gpt-5.6-luna" #keep this comment', updated)
        self.assertIn('enabled = true', updated)
        self.assertEqual(config.stat().st_mode & 0o777, 0o640)
        backups = list((codex_home / "backups/cortex-upgrade").rglob("config.toml"))
        self.assertTrue(backups)
        self.assertTrue(any('default_subagent_model = "gpt-5.6-terra"' in path.read_text(encoding="utf-8") for path in backups))
        for backup_path in [codex_home / "backups/cortex-upgrade", *(codex_home / "backups/cortex-upgrade").rglob("*")]:
            self.assertEqual(backup_path.stat().st_mode & 0o077, 0, backup_path)
        checked = subprocess.run(["bash", str(script), "--check"], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_sync_refuses_unauthenticated_retired_cache(self):
        isolated = self.base / "untrusted-cache-home"
        codex_home = isolated / ".codex"
        cache = codex_home / "plugins/cache/personal/codex-orchestration-control/4.4.0"
        (cache / ".codex-plugin").mkdir(parents=True)
        (cache / "scripts").mkdir()
        sentinel = cache / "sentinel.txt"
        sentinel.write_text("unchanged\n", encoding="utf-8")
        (cache / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "untrusted", "version": "4.4.0"}), encoding="utf-8")
        (cache / "scripts/orchestration_control.py").write_text('SERVER_VERSION = "4.4.0"\n', encoding="utf-8")
        environment = os.environ.copy()
        environment.update({"HOME": str(isolated), "CODEX_HOME": str(codex_home)})
        script = Path(__file__).parents[1] / "scripts/sync-cortex.sh"
        completed = subprocess.run(["bash", str(script), "--dry-run"], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("refusing unauthenticated retired plugin cache", completed.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged\n")

    def test_root_marketplace_validator_rejects_retired_nested_artifacts(self):
        repository = Path(__file__).parents[1]
        checkout = self.base / "checkout"
        shutil.copytree(repository, checkout, ignore=shutil.ignore_patterns(".git", ".codex", "__pycache__", "*.pyc", "*.pyo"))
        validator = repository / "scripts/validate-cortex-marketplace.py"
        valid = subprocess.run([sys.executable, str(validator), "--root", str(checkout)], text=True, capture_output=True, check=False)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        nested = checkout / "marketplace/.agents/plugins"
        nested.mkdir(parents=True)
        (nested / "marketplace.json").write_text("{}\n", encoding="utf-8")
        rejected = subprocess.run([sys.executable, str(validator), "--root", str(checkout)], text=True, capture_output=True, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("retired nested marketplace artifacts", rejected.stderr)

    def test_tracked_release_validation_rejects_runtime_state(self):
        repository = Path(__file__).parents[1]
        checkout = self.base / "release-checkout"
        shutil.copytree(repository, checkout, ignore=shutil.ignore_patterns(".git", ".codex", "__pycache__", "*.pyc", "*.pyo"))
        environment = os.environ.copy()
        environment.update({"GIT_AUTHOR_NAME": "Cortex Test", "GIT_AUTHOR_EMAIL": "cortex-test@example.invalid", "GIT_COMMITTER_NAME": "Cortex Test", "GIT_COMMITTER_EMAIL": "cortex-test@example.invalid"})
        for command in (["git", "init"], ["git", "add", "."], ["git", "commit", "-m", "release fixture"]):
            completed = subprocess.run(command, cwd=checkout, env=environment, text=True, capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        release = repository / "scripts/verify-cortex-release.py"
        valid = subprocess.run([sys.executable, str(release), "--root", str(checkout), "--require-tracked"], text=True, capture_output=True, check=False)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        runtime = checkout / ".codex/cortex/task.json"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("private runtime state\n", encoding="utf-8")
        subprocess.run(["git", "add", "-f", ".codex/cortex/task.json"], cwd=checkout, env=environment, check=True)
        subprocess.run(["git", "commit", "-m", "track runtime state"], cwd=checkout, env=environment, check=True)
        rejected = subprocess.run([sys.executable, str(release), "--root", str(checkout), "--require-tracked"], text=True, capture_output=True, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("runtime state is tracked", rejected.stderr)

    def test_tracked_release_validation_rejects_secret_prone_paths(self):
        repository = Path(__file__).parents[1]
        checkout = self.base / "secret-release-checkout"
        shutil.copytree(repository, checkout, ignore=shutil.ignore_patterns(".git", ".codex", "__pycache__", "*.pyc", "*.pyo"))
        environment = os.environ.copy()
        environment.update({"GIT_AUTHOR_NAME": "Cortex Test", "GIT_AUTHOR_EMAIL": "cortex-test@example.invalid", "GIT_COMMITTER_NAME": "Cortex Test", "GIT_COMMITTER_EMAIL": "cortex-test@example.invalid"})
        for command in (["git", "init"], ["git", "add", "."], ["git", "commit", "-m", "release fixture"]):
            completed = subprocess.run(command, cwd=checkout, env=environment, text=True, capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        release = repository / "scripts/verify-cortex-release.py"
        for index, relative in enumerate((
            "plugins/cortex/.env",
            "plugins/cortex/.env.production",
            "plugins/cortex/private-key.pem",
            "plugins/cortex/config/credentials.json",
            "plugins/cortex/.ssh/id_ed25519",
        )):
            with self.subTest(relative=relative):
                fixture = checkout / relative
                fixture.parent.mkdir(parents=True, exist_ok=True)
                fixture.write_text("non-secret regression fixture\n", encoding="utf-8")
                subprocess.run(["git", "add", "-f", relative], cwd=checkout, env=environment, check=True)
                subprocess.run(["git", "commit", "-m", f"secret path fixture {index}"], cwd=checkout, env=environment, check=True)
                rejected = subprocess.run([sys.executable, str(release), "--root", str(checkout), "--require-tracked"], text=True, capture_output=True, check=False)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn(f"secret-prone path is tracked: {relative}", rejected.stderr)
                subprocess.run(["git", "rm", "-f", relative], cwd=checkout, env=environment, check=True)
                subprocess.run(["git", "commit", "-m", f"remove secret path fixture {index}"], cwd=checkout, env=environment, check=True)
        safe_doc = checkout / "docs/credential-handling-example.md"
        safe_doc.write_text("Documentation may mention .env and private-key.pem without shipping those files.\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/credential-handling-example.md"], cwd=checkout, env=environment, check=True)
        subprocess.run(["git", "commit", "-m", "safe documentation fixture"], cwd=checkout, env=environment, check=True)
        accepted = subprocess.run([sys.executable, str(release), "--root", str(checkout), "--require-tracked"], text=True, capture_output=True, check=False)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_numbered_task_hook_resolution(self):
        created = self.init(task_id="hooked")
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        event = {"hook_event_name": "PostToolUse", "thread_id": "owner", "tool_name": "Agent"}
        completed = subprocess.run([sys.executable, str(hook)], input=json.dumps(event), text=True, capture_output=True, env=os.environ.copy(), check=True)
        self.assertEqual(completed.stdout.strip(), "{}")
        lifecycle = self.ledger / "tasks" / created["task_directory"] / "lifecycle-events.jsonl"
        self.assertTrue(lifecycle.exists())
        self.assertEqual(lifecycle.stat().st_mode & 0o777, 0o600)

    def test_hook_refuses_symlinked_lifecycle_event_file(self):
        created = self.init(task_id="hook-symlink")
        task_dir = self.ledger / "tasks" / created["task_directory"]
        victim = self.base / "victim.txt"
        victim.write_text("unchanged\n", encoding="utf-8")
        (task_dir / "lifecycle-events.jsonl").symlink_to(victim)
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        event = {"hook_event_name": "PostToolUse", "thread_id": "owner", "tool_name": "Agent"}
        completed = subprocess.run([sys.executable, str(hook)], input=json.dumps(event), text=True, capture_output=True, env=os.environ.copy(), check=True)
        self.assertEqual(completed.stdout.strip(), "{}")
        self.assertIn("warning: ValueError", completed.stderr)
        self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged\n")

    def test_worker_hook_forces_main_chat_return_route(self):
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        inactive_event = {"hook_event_name": "SubagentStart", "thread_id": "worker", "agent_type": "explorer"}
        inactive = subprocess.run([sys.executable, str(hook)], input=json.dumps(inactive_event), text=True, capture_output=True, env=os.environ.copy(), check=True)
        self.assertEqual(inactive.stdout.strip(), "{}")
        self.init(task_id="worker-context")
        active_event = {"hook_event_name": "SubagentStart", "thread_id": "owner", "agent_type": "explorer"}
        completed = subprocess.run([sys.executable, str(hook)], input=json.dumps(active_event), text=True, capture_output=True, env=os.environ.copy(), check=True)
        context = json.loads(completed.stdout)["additionalContext"]
        self.assertIn("internal worker, never user-facing", context)
        self.assertIn("native parent channel", context)
        self.assertIn("Return your final sanitized cortex/report/v1 directly to the parent", context)
        self.assertNotIn("record_report", context)
        self.assertNotIn("mcp__codebase_memory__", context)

    def test_hook_hashes_thread_and_allowlists_telemetry_fields(self):
        created = self.init(task_id="hook-privacy")
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        event = {"hook_event_name": "PostToolUse", "thread_id": "owner", "agent_type": "secret-agent", "tool_name": "bad tool\nsecret"}
        subprocess.run([sys.executable, str(hook)], input=json.dumps(event), text=True, capture_output=True, env=os.environ.copy(), check=True)
        task_dir = self.ledger / "tasks" / created["task_directory"]
        payload = json.loads((task_dir / "lifecycle-events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertNotIn("thread_id", payload)
        self.assertEqual(len(payload["thread_id_digest"]), 64)
        self.assertIsNone(payload["agent_type"])
        self.assertIsNone(payload["tool_name"])

    def test_lifecycle_telemetry_retains_bounded_tail_and_dropped_count(self):
        created = self.init(task_id="hook-retention")
        task_dir = self.ledger / "tasks" / created["task_directory"]
        original_events, original_bytes = cortex_hook.MAX_LIFECYCLE_EVENTS, cortex_hook.MAX_LIFECYCLE_BYTES
        try:
            cortex_hook.MAX_LIFECYCLE_EVENTS, cortex_hook.MAX_LIFECYCLE_BYTES = 3, 100000
            for index in range(6):
                cortex_hook.append_lifecycle_event(task_dir, {"index": index})
        finally:
            cortex_hook.MAX_LIFECYCLE_EVENTS, cortex_hook.MAX_LIFECYCLE_BYTES = original_events, original_bytes
        events = [json.loads(line) for line in (task_dir / "lifecycle-events.jsonl").read_text(encoding="utf-8").splitlines()]
        metadata = json.loads((task_dir / "lifecycle-events-meta.json").read_text(encoding="utf-8"))
        self.assertEqual([item["index"] for item in events], [3, 4, 5])
        self.assertEqual(metadata["dropped"], 3)

    def test_v7_rejects_older_task_schema(self):
        created = self.init(task_id="schema-check")
        task_dir = self.ledger / "tasks" / created["task_directory"]
        unsupported = "cortex/" + "v" + str(5)
        for name in ("task.json", "current.json"):
            path = task_dir / name
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["schema"] = unsupported
            path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "create a new v7 task"):
            control.status({"task_id": "schema-check", "principal": "owner"})

    def test_shipped_policy_and_plugin_have_no_retired_profile_contract(self):
        repository = Path(__file__).parents[1]
        self.assertFalse((repository / "agents" / "orchestrator.toml").exists())
        targets = [repository / "AGENTS.md"]
        targets.extend(path for path in (repository / "plugins/cortex").rglob("*") if path.is_file() and "__pycache__" not in path.parts)
        forbidden = ("@" + "orchestrator", "conductor" + "_only", "cortex/" + "v" + str(5), "4." + "4.0")
        for path in targets:
            content = path.read_text(encoding="utf-8", errors="ignore")
            for marker in forbidden:
                self.assertNotIn(marker, content, f"{marker!r} remains in {path}")

    def test_plugin_bundled_orchestrator_skill_is_the_only_source(self):
        repository = Path(__file__).parents[1]
        authoritative = repository / "plugins/cortex/skills/orchestrator/SKILL.md"
        self.assertTrue(authoritative.is_file())
        matches = [path for path in repository.rglob("SKILL.md") if path.parent.name == "orchestrator"]
        self.assertEqual(matches, [authoritative])

    def test_control_skill_requires_unified_host_dispatch_contract(self):
        skill = (Path(__file__).parents[1] / "plugins/cortex/skills/cortex-control/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Cortex v3 exposes three public MCP tools", skill)
        self.assertIn("`start_orchestration` and `continue_orchestration`", skill)
        self.assertIn("Invoke each returned dispatch", skill)
        self.assertIn("Expected routes are metadata, not proof", skill)
        self.assertIn("Workers do not call Cortex", skill)
        self.assertIn("question intent", skill)

    def test_control_skill_requires_ordered_one_call_per_wave_protocol(self):
        skill = (Path(__file__).parents[1] / "plugins/cortex/skills/cortex-control/SKILL.md").read_text(encoding="utf-8")
        markers = [
            "## Normal flow",
            "Call `start_orchestration` once",
            "Invoke each returned dispatch",
            "Workers do not call Cortex",
            "After all workers finish",
            "call `continue_orchestration` exactly once",
            "Repeat one continue per completed wave",
        ]
        positions = [skill.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))

    def test_all_installable_sources_are_plugin_bundled(self):
        repository = Path(__file__).parents[1]
        self.assertFalse((repository / "agents").exists())
        self.assertFalse((repository / "skills").exists())
        self.assertEqual(len(list((repository / "plugins/cortex/agents").glob("*.toml"))), 21)
        self.assertEqual(len(list((repository / "plugins/cortex/skills").glob("*/SKILL.md"))), 10)

    def test_default_cortex_ledger_is_excluded_from_manifest(self):
        ledger = control.ledger_root({"project_root": str(self.project)})
        (ledger / "generated.txt").write_text("runtime\n", encoding="utf-8")
        manifest = control.capture_project_manifest(self.project)
        self.assertEqual(ledger, self.project / ".codex" / "cortex")
        self.assertNotIn(".codex/cortex/generated.txt", manifest["entries"])
        self.assertIn(".codex/cortex", manifest["policy"]["effective_ignored_roots"])

    def test_server_and_manifest_versions_match(self):
        manifest = json.loads((Path(__file__).parents[1] / "plugins/cortex/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], control.SERVER_VERSION)

    def test_sol_escalation_schema_is_structured_and_auditable(self):
        for tool_name in ("resolve_dispatch_route", "record_delegation"):
            with self.subTest(tool_name=tool_name):
                schema = control.TOOLS[tool_name][1]
                escalation = schema["properties"]["sol_escalation"]
                self.assertFalse(escalation["additionalProperties"])
                self.assertEqual(escalation["properties"]["kind"]["enum"], ["auditable_extreme", "terra_failure"])
                self.assertIn("criterion", escalation["properties"])
                self.assertIn("audit_ref", escalation["properties"])
                self.assertIn("prior_terra_attempt_id", escalation["properties"])

    def test_cortex_help_route_is_deterministic_and_read_only(self):
        skill = (Path(__file__).parents[1] / "plugins/cortex/skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(self.cortex_routes(skill), {
            "empty": "orchestrate",
            "help": "help",
            "harvest": "harvest",
            "harvest-refresh": "harvest-refresh",
            "normal": "normal",
        })
        help_section = skill.split("## Invocation and routes", 1)[1].split("## Relative one-call-per-wave workflow", 1)[0]
        self.assertIn("`cortex:orchestrator`", help_section)
        self.assertIn("`$cortex:orchestrator`", help_section)
        self.assertIn("not registered native slash", help_section)
        self.assertIn("Help performs no activation", help_section)
        before = control.capture_project_manifest(self.project)
        after = control.capture_project_manifest(self.project)
        self.assertEqual(before["digest"], after["digest"])

    def test_incremental_harvest_fixture_changes_only_evidence_justified_docs(self):
        docs = self.project / "docs/project"
        docs.mkdir(parents=True)
        stale = docs / "verification.md"
        current = docs / "conventions.md"
        stale.write_text("Manual note.\n<!-- GENERATED:START -->\nold command\n<!-- GENERATED:END -->\n", encoding="utf-8")
        current.write_text("Keep this whole file.\n", encoding="utf-8")
        baseline = control.capture_project_manifest(self.project)
        stale.write_text(self.replace_generated_facts(stale.read_text(encoding="utf-8"), "`python3 -m unittest`\nSource: `tests/`."), encoding="utf-8")
        feature = self.project / "docs/features/widget/index.md"
        feature.parent.mkdir(parents=True)
        feature.write_text("<!-- GENERATED:START -->\nPurpose: verified by `src/widget.py`.\n<!-- GENERATED:END -->\n", encoding="utf-8")
        refreshed = control.capture_project_manifest(self.project)
        comparison = control.compare_manifests(baseline, refreshed)
        self.assertEqual(set(comparison["changed_paths"]), {"docs/features/widget/index.md", "docs/project/verification.md"})
        self.assertEqual(current.read_text(encoding="utf-8"), "Keep this whole file.\n")
        self.assertTrue(stale.read_text(encoding="utf-8").startswith("Manual note.\n"))

    def test_refresh_fixture_is_idempotent_and_preserves_manual_notes(self):
        docs = self.project / "docs/project"
        docs.mkdir(parents=True)
        index = docs / "index.md"
        index.write_text("# Project\n\nManual owner note.\n\n<!-- GENERATED:START -->\nstale facts\n<!-- GENERATED:END -->\n", encoding="utf-8")
        refreshed = self.replace_generated_facts(index.read_text(encoding="utf-8"), "Stack: Python.\nEvidence: `plugins/cortex/scripts/cortex.py`.")
        index.write_text(refreshed, encoding="utf-8")
        first_manifest = control.capture_project_manifest(self.project)
        second = self.replace_generated_facts(index.read_text(encoding="utf-8"), "Stack: Python.\nEvidence: `plugins/cortex/scripts/cortex.py`.")
        self.assertEqual(second, index.read_text(encoding="utf-8"))
        index.write_text(second, encoding="utf-8")
        second_manifest = control.capture_project_manifest(self.project)
        self.assertEqual(first_manifest["digest"], second_manifest["digest"])
        self.assertIn("Manual owner note.", second)

    def test_retired_release_version_is_absent_from_current_sources(self):
        repository = Path(__file__).parents[1]
        retired = "5." + "0.0"
        for root in ("AGENTS.md", "README.md", "docs", "marketplace", "plugins", "scripts", "tests"):
            target = repository / root
            paths = [target] if target.is_file() else [path for path in target.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
            for path in paths:
                self.assertNotIn(retired, path.read_text(encoding="utf-8", errors="ignore"), str(path))


if __name__ == "__main__":
    unittest.main()
