import io
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex as control
import cortex_hook
from cortex_runtime import orchestration_engine
from cortex_runtime import gate_transitions
from cortex_runtime import mcp_api
from cortex_runtime import delegation_service
from cortex_runtime import briefings
from cortex_runtime import attempt_protocol
from cortex_runtime import ledger_db


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.host_store = self.base / "host-private-store"
        self.host_store.mkdir(mode=0o700)
        self.host_store.chmod(0o700)
        self._previous_host_store = os.environ.get(control.HOST_CONTROL_STORE_ENV)
        os.environ[control.HOST_CONTROL_STORE_ENV] = str(self.host_store)
        self.ledger = control.ledger_root_path({"project_root": str(self.project)})
        # Current V5 governance authenticity is host-owned.  Provision the
        # test host's private lifecycle key before any start/classification
        # path attempts to verify a signed governance record.
        ledger_db._governance_lifecycle_hmac_key(self.ledger, create=True)
        self._handlers = {}
        def with_project(name, original, params):
            prepared = {**params, "project_root": str(self.project)}
            # Task-scoped public operations derive their workspace from the
            # canonical task_ref.  project_root is reserved for project-
            # scoped maintenance/prune calls and must not be injected here.
            if name in {"manage_orchestration", "continue_orchestration"} and prepared.get("task_ref"):
                prepared.pop("project_root", None)
            if name in {"worker_question", "cortex_question", "publish_worker_question"}:
                action = str(prepared.get("action") or "ask")
                if action == "ask" and str(prepared.get("question") or "").strip():
                    prepared.setdefault("recommendation", "Use the first listed option because it is the safest bounded default.")
                    options = control._question_options(prepared.get("options"))
                    if options:
                        prepared.setdefault("recommended_option_ids", [options[0]["option_id"]])
                    else:
                        prepared.setdefault("recommended_answer", "Provide the smallest concrete constraint that unblocks the worker.")
                batch = prepared.get("batch")
                if action == "ask_batch" and isinstance(batch, dict):
                    batch = {**batch, "questions": [dict(item) for item in batch.get("questions", [])]}
                    for item in batch["questions"]:
                        item.setdefault("recommendation", "Use the first listed option because it is the safest bounded default.")
                        options = control._question_options(item.get("options"))
                        if options:
                            item.setdefault("recommended_option_ids", [options[0]["option_id"]])
                        else:
                            item.setdefault("recommended_answer", "Provide the smallest concrete constraint that unblocks the worker.")
                    prepared["batch"] = batch
            return original(prepared)
        for handler, _ in control.TOOLS.values():
            name = handler.__name__
            if name in self._handlers:
                continue
            original = getattr(control, name)
            self._handlers[name] = original
            setattr(control, name, lambda params, original=original, name=name: with_project(name, original, params))
        # Public worker helpers are also called directly by contract tests and
        # are not necessarily registered under their Python function name.
        for name in ("worker_question", "cortex_question"):
            if name in self._handlers or not hasattr(control, name):
                continue
            original = getattr(control, name)
            self._handlers[name] = original
            setattr(control, name, lambda params, original=original, name=name: with_project(name, original, params))

    def tearDown(self):
        for name, handler in self._handlers.items():
            setattr(control, name, handler)
        if self._previous_host_store is None:
            os.environ.pop(control.HOST_CONTROL_STORE_ENV, None)
        else:
            os.environ[control.HOST_CONTROL_STORE_ENV] = self._previous_host_store
        self.temp.cleanup()

    def activate(self, principal="thread-a"):
        return control.activate_orchestration({"user_command": "/cortex", "principal": principal, "thread_id": principal})

    def task_state(self, task_dir: Path) -> dict:
        return control.load_task_state_for_artifact(task_dir)

    def task_definition(self, task_dir: Path) -> dict:
        return control.load_task_definition(task_dir)

    def write_task_state(self, state: dict) -> None:
        control.db_update_task_state(self.ledger, state)

    def task_document(self, task_dir: Path, key: str) -> dict:
        state = self.task_state(task_dir)
        document = control.db_get_task_document(self.ledger, state["task_id"], key)
        self.assertIsNotNone(document)
        return document

    def reconcile_projections(self, *, worker_id="control-plane-test"):
        """Explicitly materialize optional exports before filesystem assertions."""
        from cortex_runtime.projection_service import reconcile
        return reconcile(self.ledger, worker_id=worker_id)

    @staticmethod
    def briefing_from_request(request):
        path = Path(request["briefing_path"])
        return path.read_text(encoding="utf-8")

    @classmethod
    def briefing_from_response(cls, response, index=0):
        return cls.briefing_from_request(response["dispatches"][index])

    def init(self, task_id="demo", complexity="C1"):
        self.activate()
        classified = control.classify_task({"complexity": complexity, "requirements": [], "principal": "thread-a"})
        return control.init_task({"task_id": task_id, "user_request": "test objective", "complexity": complexity, "classification_id": classified["classification_id"], "principal": "thread-a"})

    def test_init_task_requires_canonical_text_arrays(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "scope must be a current canonical text array"):
            control.init_task({
                "task_id": "scalar-scope",
                "user_request": "scalar scope",
                "complexity": "C1",
                "classification_id": classified["classification_id"],
                "principal": "thread-a",
                "scope": "Текущий репозиторий",
            })
        created = control.init_task({
            "task_id": "array-scope",
            "user_request": "array scope",
            "complexity": "C1",
            "classification_id": classified["classification_id"],
            "principal": "thread-a",
            "scope": ["Текущий репозиторий"],
            "acceptance_criteria": ["one acceptance criterion"],
            "allowed_paths": ["plugins/cortex"],
            "verification": ["run focused tests"],
            "pause_conditions": ["ask before widening scope"],
        })
        task = control.db_load_task(self.ledger, created["task_id"])[0]
        self.assertEqual(task["scope"], ["Текущий репозиторий"])
        self.assertEqual(task["acceptance_criteria"], ["one acceptance criterion"])
        self.assertEqual(task["allowed_paths"], ["plugins/cortex"])
        self.assertEqual(task["verification"], ["run focused tests"])
        self.assertEqual(task["pause_conditions"], ["ask before widening scope"])
        self.assertEqual(briefings._briefing_scope(task["scope"]), ["Текущий репозиторий"])
        delegation_lists = delegation_service.delegation_lists(
            {},
            {"allowed_paths": ["plugins/cortex"]},
            {"acceptance_criteria": ["fallback acceptance"], "verification": ["fallback verification"]},
        )
        self.assertEqual(delegation_lists["allowed_paths"], ["plugins/cortex"])


    def test_init_task_preserves_governance_snapshot_types_and_digest(self):
        """Persisted governance must hash the exact server-owned JSON snapshot."""
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        snapshot = {
            "schema": "cortex/governance-policy/v1",
            "required_floor": "full",
            "promotion_window_days": 90,
            "promotion_threshold_scopes": 3,
        }
        governance_context = {
            "schema": "cortex/governance/v1",
            "requested_mode": "auto",
            "effective_mode": "full",
            "policy_snapshot": snapshot,
            "policy_snapshot_digest": hashlib.sha256(
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        created = control.init_task({
            "task_id": "governance-digest-types",
            "user_request": "preserve governance digest types",
            "complexity": "C1",
            "classification_id": classified["classification_id"],
            "principal": "thread-a",
            "governance": governance_context,
        })
        persisted = control.db_load_task(self.ledger, created["task_id"])[0]["governance"]
        persisted_snapshot = persisted["policy_snapshot"]
        self.assertIs(type(persisted_snapshot["promotion_window_days"]), int)
        self.assertIs(type(persisted_snapshot["promotion_threshold_scopes"]), int)
        self.assertEqual(
            persisted["policy_snapshot_digest"],
            hashlib.sha256(
                json.dumps(persisted_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        )

    def test_oversized_requirements_round_trip_before_classify_and_init_persistence(self):
        self.activate()
        requirement = " ".join(
            f"requirement-{index}: preserve the complete durable requirement text across classification and task initialization"
            for index in range(32)
        )
        self.assertGreater(len(requirement), 600)

        classified = control.classify_task({
            "complexity": "C2",
            "requirements": [requirement],
            "principal": "thread-a",
        })
        receipt = control.db_get_classification(self.ledger, classified["classification_id"])
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["requirements"], [requirement])
        self.assertEqual(
            control.normalize_task_requirements(receipt["requirements"]),
            receipt["requirements"],
        )

        created = control.init_task({
            "task_id": "lossless-requirements",
            "user_request": "persist lossless requirements without blocking continuation",
            "classification_id": classified["classification_id"],
            "principal": "thread-a",
        })
        task = self.task_definition(self.ledger / "tasks" / created["task_directory"])
        self.assertEqual(task["requirements"], receipt["requirements"])
        self.assertEqual(task["requirements"], [requirement])

    def test_v3_start_preserves_oversized_requirements_before_dispatch(self):
        requirement = " ".join(
            f"dispatch requirement {index} must remain complete and bounded in the canonical ledger domain"
            for index in range(36)
        )
        self.assertGreater(len(requirement), 600)

        started = self.v3_start(
            "start a task with an oversized requirement",
            requirements=[requirement],
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        self.assertTrue(started["ok"])
        self.assertTrue(started["dispatches"])
        task_dir = next((self.ledger / "tasks").iterdir())
        task = self.task_definition(task_dir)
        self.assertEqual(task["requirements"], [requirement])

    def test_init_task_preserves_an_oversized_preexisting_receipt_before_persistence(self):
        self.activate()
        requirement = " ".join(
            f"receipt requirement {index} remains a complete bounded task-domain value"
            for index in range(34)
        )
        classified = control.classify_task({
            "complexity": "C1",
            "requirements": [requirement],
            "principal": "thread-a",
        })
        receipt = control.db_get_classification(self.ledger, classified["classification_id"])
        self.assertIsNotNone(receipt)
        # Model a persisted classification receipt. Init must preserve it,
        # not rewrite it into backend-sized atoms.
        receipt["requirements"] = [requirement]
        control.db_put_classification(self.ledger, receipt)

        created = control.init_task({
            "task_id": "repair-oversized-receipt",
            "user_request": "repair a preexisting classification receipt at task ingress",
            "classification_id": classified["classification_id"],
            "principal": "thread-a",
        })
        task = self.task_definition(self.ledger / "tasks" / created["task_directory"])
        self.assertEqual(task["requirements"], [requirement])

    def test_many_oversized_requirements_are_classified_without_a_backend_quota(self):
        self.activate()
        classified = control.classify_task({
            "complexity": "C1",
            "requirements": ["x" * 601 for _ in range(51)],
            "principal": "thread-a",
        })
        receipt = control.db_get_classification(self.ledger, classified["classification_id"])
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["requirements"], ["x" * 601 for _ in range(51)])
        self.assertEqual(list((self.ledger / "tasks").iterdir()), [])

    def test_init_task_rejects_non_string_items_in_text_lists(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "scope must be a current canonical text array"):
            control.init_task({
                "task_id": "invalid-scope",
                "user_request": "invalid scope",
                "complexity": "C1",
                "classification_id": classified["classification_id"],
                "principal": "thread-a",
                "scope": ["valid", 123],
            })

    def write_canonical_harvest_project_docs(self):
        project_docs = self.project / "docs/project"
        project_docs.mkdir(parents=True, exist_ok=True)
        (project_docs / "index.md").write_text(
            "# Project knowledge\n\n"
            "This index records verified repository behavior, operating boundaries, evidence, and durable project guidance.\n\n"
            "- [Conventions](conventions.md)\n"
            "- [Verification](verification.md)\n"
            "- [Decisions](decisions.md)\n"
            "- [Gotchas](gotchas.md)\n",
            encoding="utf-8",
        )
        canonical_content = {
            "conventions.md": "# Conventions\n\nThe repository follows verified naming, structure, configuration, and implementation conventions recorded from current source evidence.\n",
            "verification.md": "# Verification\n\nThe repository uses focused automated checks and source inspection to verify behavior, failure paths, and integration boundaries.\n",
            "decisions.md": "# Decisions\n\nNo explicit architectural decision record was found; this verified evidence boundary is retained for future investigation.\n",
            "gotchas.md": "# Gotchas\n\nNo confirmed project-specific gotcha was found; this verified absence must be reconsidered when repository behavior changes.\n",
        }
        for name, content in canonical_content.items():
            (project_docs / name).write_text(content, encoding="utf-8")
        return project_docs

    def delegate(self, state, task_id, gate, agent, **extra):
        observed = control.status({"task_id": task_id, "principal": state.get("principal", "thread-a")})
        default_model = (
            "gpt-5.6-luna" if agent == "explorer"
            else "gpt-5.6-sol" if agent == "security_auditor" or gate == "security"
            else "gpt-5.6-terra"
        )
        contract = {"task_kind": gate, "risk": "moderate", "requested_model": default_model, "requested_reasoning_effort": "medium", "ownership": f"Own {gate}", "allowed_paths": ["."], "acceptance_criteria": [f"Complete {gate}"], "verification": ["Record evidence"]}
        delegated = control.record_delegation({"task_id": task_id, "principal": state.get("principal", "thread-a"), "expected_revision": state["revision"], "status_receipt": observed["status_receipt"], "gate": gate, "agent": agent, "objective": "test delegation", **contract, **extra})
        confirmed = control.confirm_host_spawn({
            "task_id": task_id,
            "principal": state.get("principal", "thread-a"),
            "expected_revision": delegated["state"]["revision"],
            "attempt_id": delegated["attempt_id"],
            "host_tool": delegated["spawn_request"]["host_tool"],
            "host_agent_id": f"test-host-{delegated['attempt_id']}",
            "host_task_name": delegated["spawn_request"]["task_name"],
            "host_model": delegated["spawn_request"]["model"],
            "host_reasoning_effort": delegated["spawn_request"]["reasoning_effort"],
        })
        return {**delegated, "state": confirmed["state"], "host_spawn": confirmed["host_spawn"]}










    @staticmethod
    def v3_planning():
        return {
            "overview": "Split the approved outcome into independently verifiable work packages.",
            "work_packages": [{
                "id": "core",
                "title": "Core delivery",
                "objective": "Implement the bounded core change.",
                "allowed_paths": ["src"],
                "microtasks": [{
                    "id": "core_change",
                    "title": "Implement the core change",
                    "objective": "Make the requested behavior work.",
                    "profile": "backend_dev",
                    "allowed_paths": ["src"],
                    "acceptance_criteria": ["Requested behavior is implemented."],
                    "verification": ["Run focused tests."],
                }],
            }],
        }

    @staticmethod
    def v3_scoping():
        return {
            "overview": "Map the repository evidence needed before solution design.",
            "context_files": [],
            "discovery_domains": [{
                "id": "runtime",
                "title": "Runtime contract",
                "objective": "Trace the current behavior and its executable boundaries.",
                "paths": ["."],
                "context": ["Current source, tests, schemas, and executable configuration are authoritative."],
                "depends_on": [],
                "acceptance_criteria": ["The discovery result identifies the current behavior and relevant ownership boundaries."],
                "verification": ["Confirm consequential claims in current source or tests."],
            }],
        }

    def v3_start(self, objective="v3 task", waves=None, **task_overrides):
        if (
            waves is not None
            and "plan_approval" not in task_overrides
            and not any(
                str(worker.get("phase") or "").strip().lower() in {"plan", "planning"}
                for wave in waves
                for worker in wave.get("workers", [])
            )
        ):
            task_overrides["plan_approval"] = "auto"
        task = {
            "user_request": objective,
            "acceptance_criteria": ["The requested observable outcome is completed end to end."],
            "verification": ["Run and record an authoritative check for the requested outcome."],
            **task_overrides,
        }
        arguments = {
            "project_root": str(self.project),
            "task": task,
        }
        if waves is not None:
            arguments["waves"] = waves
        return control.start_orchestration(arguments)

    def test_v3_start_binds_codex_session_for_documented_compact_hook(self):
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "bind the host session for recovery",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
            self.assertTrue(started["ok"])
            task_dir = next((self.ledger / "tasks").iterdir())
            state = control.load_task_state_for_artifact(task_dir)
            self.assertEqual(state["thread_id"], state["principal"])
            bound = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps({
                    "hook_event_name": "PostToolUse",
                    "session_id": "host-session-42",
                    "cwd": str(self.project),
                    "tool_name": "mcp__cortex__start_orchestration",
                    "tool_input": {"project_root": str(self.project)},
                    "tool_response": {"structuredContent": started},
                }),
                text=True,
                capture_output=True,
                env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
                check=True,
            )
            bound_payload = json.loads(bound.stdout)
            self.assertEqual(bound_payload["hookSpecificOutput"]["hookEventName"], "PostToolUse")
            self.assertIn(
                "CORTEX DISPATCH REQUIRED NOW",
                bound_payload["hookSpecificOutput"]["additionalContext"],
            )
            bindings = control._host_session_bindings(self.ledger)
            self.assertEqual(bindings["tasks"][state["task_id"]], "host-session-42")
            self.assertEqual(cortex_hook.active_task(self.ledger, "host-session-42"), state["task_id"])
            self.assertFalse((self.ledger / "active-tasks.json").exists())
            subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps({
                    "hook_event_name": "PostToolUse",
                    "session_id": "different-host-session",
                    "cwd": str(self.project),
                    "tool_name": "mcp__cortex__start_orchestration",
                    "tool_input": {"project_root": str(self.project)},
                    "tool_response": {"structuredContent": started},
                }),
                text=True,
                capture_output=True,
                env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
                check=True,
            )
            bindings = control._host_session_bindings(self.ledger)
            self.assertEqual(bindings["tasks"][state["task_id"]], "host-session-42")
            self.assertIsNone(cortex_hook.active_task(self.ledger, "different-host-session"))
            completed = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps({
                    "hook_event_name": "SessionStart",
                    "session_id": "host-session-42",
                    "cwd": str(self.project),
                    "source": "clear",
                }),
                text=True,
                capture_output=True,
                env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
                check=True,
            )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("CONTEXT RECOVERY", context)
        self.assertIn(started["task_ref"], context)
        self.assertIn("manage_orchestration(intent='inspect'", context)

    def test_subagent_start_persists_native_child_for_compaction_recovery(self):
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "persist a native worker across context compaction",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        parent_session = "host-parent-session"
        subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "PostToolUse",
                "session_id": parent_session,
                "cwd": str(self.project),
                "tool_name": "mcp__cortex__start_orchestration",
                "tool_input": {"project_root": str(self.project)},
                "tool_response": {"structuredContent": started},
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        bindings = control._host_session_bindings(self.ledger)
        self.assertEqual(bindings["tasks"][state["task_id"]], parent_session)
        self.assertEqual(cortex_hook.active_task(self.ledger, parent_session), state["task_id"])
        launched = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "SubagentStart",
                "session_id": parent_session,
                "agent_id": "native.Child:01",
                "agent_type": "default",
                "model": attempt["expected_model"],
                "turn_id": "turn-native-child-01",
                "permission_mode": "default",
                "cwd": str(self.project),
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        payload = json.loads(launched.stdout)
        self.assertIn("hookSpecificOutput", payload, launched.stderr)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        self.assertNotIn("HOST BINDING BLOCKER", payload["hookSpecificOutput"]["additionalContext"])
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        self.assertEqual(attempt["status"], "running")
        self.assertEqual(attempt["host_spawn"]["agent_id"], "native.Child:01")
        self.assertEqual(attempt["host_spawn"]["task_name"], attempt["spawn_request"]["task_name"])
        self.assertEqual(attempt["host_spawn"]["model"], attempt["expected_model"])

        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        self.assertTrue(inspected["ok"], inspected)
        self.assertEqual(inspected["outcome"], "waiting_workers")
        self.assertEqual(inspected["dispatches"], [])
        self.assertEqual(inspected["result"]["pending_dispatches"], [])
        active = inspected["context_handoff"]["active_workers"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["host_agent_id"], "native.Child:01")
        self.assertEqual(active[0]["dispatch_ref"], attempt["dispatch_ref"])
        self.assertIn("native.Child:01", inspected["next_action"])
        self.assertIn("Do not restart, replay, or respawn", inspected["next_action"])

        wait_any = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "PreToolUse",
                "session_id": parent_session,
                "cwd": str(self.project),
                "tool_name": "wait",
                "tool_input": {"action": "wait", "receiver_thread_ids": []},
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        self.assertEqual(wait_any.stdout.strip(), "{}")

    def test_stop_hook_blocks_early_coordinator_final_while_worker_is_running(self):
        """A live child must keep the coordinator turn available to wait."""
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "do not end the coordinator turn while its native worker is active",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        parent_session = "host-stop-guard-parent"
        worker_id = "native.StopGuard:01"
        self.assertTrue(
            control.bind_host_session_from_hook(
                str(self.project), started["task_ref"], parent_session,
            )["bound"]
        )
        self.assertTrue(
            control.bind_host_worker_from_hook(
                str(self.project), state["task_id"], parent_session, "default",
                worker_id, attempt["expected_model"],
            )["bound"]
        )

        stop = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "Stop",
                "session_id": parent_session,
                "cwd": str(self.project),
                "model": attempt["expected_model"],
                "permission_mode": "default",
                "turn_id": "turn-stop-guard-01",
                "last_assistant_message": "The final validation is still running.",
                "transcript_path": None,
                "stop_hook_active": False,
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        payload = json.loads(stop.stdout)
        self.assertEqual(payload, {})

        loop_escape = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "Stop",
                "session_id": parent_session,
                "cwd": str(self.project),
                "model": attempt["expected_model"],
                "permission_mode": "default",
                "turn_id": "turn-stop-guard-01",
                "last_assistant_message": "The final validation is still running.",
                "transcript_path": None,
                "stop_hook_active": True,
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        self.assertEqual(loop_escape.stdout.strip(), "{}", loop_escape.stderr)

        state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(state["attempts"][0]["status"], "running")
        self.assertEqual(state["attempts"][0]["host_spawn"]["agent_id"], worker_id)

    def test_subagent_start_recovers_when_post_tool_session_hook_was_untrusted(self):
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "recover one exact pending native worker",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        self.assertNotIn(state["task_id"], control._host_session_bindings(self.ledger)["tasks"])

        launched = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "SubagentStart",
                "session_id": "host-recovered-session",
                "agent_id": "native.Recovered:01",
                "agent_type": "default",
                "model": attempt["expected_model"],
                "cwd": str(self.project),
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        payload = json.loads(launched.stdout)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        self.assertNotIn("HOST BINDING BLOCKER", payload["hookSpecificOutput"]["additionalContext"])
        state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(state["attempts"][0]["status"], "running")
        self.assertEqual(state["attempts"][0]["host_spawn"]["agent_id"], "native.Recovered:01")
        bindings = control._host_session_bindings(self.ledger)
        self.assertEqual(bindings["tasks"][state["task_id"]], "host-recovered-session")

    def test_post_wait_unavailable_exact_child_becomes_terminal_recovery(self):
        """A proven absent native target must not pin the task for its lease."""
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "recover a native worker that the host can no longer address",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        parent_session = "host-unavailable-session"
        worker_id = "native.Unavailable:01"
        self.assertTrue(
            control.bind_host_session_from_hook(
                str(self.project), started["task_ref"], parent_session,
            )["bound"]
        )
        bound = control.bind_host_worker_from_hook(
            str(self.project), state["task_id"], parent_session, "default",
            worker_id, attempt["expected_model"],
        )
        self.assertTrue(bound["bound"], bound)

        failed_wait = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "PostToolUse",
                "session_id": parent_session,
                "cwd": str(self.project),
                "tool_name": "wait",
                "tool_input": {"receiver_thread_ids": [worker_id]},
                "tool_response": {"is_error": True, "error": {"code": "agent_not_found"}},
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        hook_payload = json.loads(failed_wait.stdout)
        context = hook_payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Internal lifecycle receipt", context)
        self.assertIn("server recorded", context)
        self.assertIn("recover_inspect", context)
        self.assertNotIn("Submit exactly one result", context)
        self.assertNotIn("agent_not_found", context)

        after_wait = control.load_task_state_for_artifact(task_dir)
        stopped = after_wait["attempts"][0]
        self.assertEqual(stopped["status"], "failed")
        self.assertEqual(stopped["lifecycle_status"], "needs_recovery")
        self.assertEqual(stopped["host_stop_outcome"], "native_worker_stopped_without_result")
        self.assertFalse(stopped["host_resumable"])

        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        self.assertTrue(inspected["ok"], inspected)
        self.assertEqual(inspected["context_handoff"]["active_workers"], [])
        stopped_worker = inspected["context_handoff"]["stopped_workers"][0]
        self.assertEqual(stopped_worker["dispatch_ref"], attempt["dispatch_ref"])
        self.assertEqual(stopped_worker["failure_reason"], "native_worker_stopped_without_result")

    def test_subagent_start_recovery_fails_closed_for_ambiguous_generic_workers(self):
        first = self.v3_start(
            "first pending worker for ambiguity",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        second = self.v3_start(
            "second pending worker for ambiguity",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        self.assertNotEqual(first["task_ref"], second["task_ref"])
        states = [
            control.load_task_state_for_artifact(path)
            for path in (self.ledger / "tasks").iterdir()
        ]
        models = {state["attempts"][0]["expected_model"] for state in states}
        self.assertEqual(len(models), 1)
        recovered = cortex_hook.pending_task_from_subagent_start(self.ledger, {
            "hook_event_name": "SubagentStart",
            "agent_type": "default",
            "model": next(iter(models)),
        })
        self.assertIsNone(recovered)

    def test_subagent_recovery_readers_use_preopened_snapshot_without_factory(self):
        """A caller-owned snapshot remains usable if opening another one is unavailable."""
        started = self.v3_start(
            "exercise preopened recovery snapshot",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        # Standalone MCP starts intentionally do not invent a host-session
        # binding when the host has not supplied CODEX_SESSION_ID.  Simulate
        # the documented SessionStart hook explicitly so this test exercises
        # the caller-owned snapshot path with a deterministic identity in CI.
        control.bind_host_session_from_hook(
            str(self.project), started["task_ref"], "preopened-recovery-session"
        )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        event = {
            "hook_event_name": "SubagentStart",
            "agent_type": "default",
            "model": state["attempts"][0]["expected_model"],
        }
        with cortex_hook.db_hook_snapshot(self.ledger) as snapshot:
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            with mock.patch.object(cortex_hook, "db_hook_snapshot", None):
                self.assertEqual(
                    cortex_hook.pending_task_from_subagent_start(self.ledger, event, snapshot),
                    state["task_id"],
                )

        session_id = control._host_session_bindings(self.ledger)["tasks"].get(state["task_id"])
        self.assertTrue(session_id)
        with cortex_hook.db_hook_snapshot(self.ledger) as snapshot:
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            with mock.patch.object(cortex_hook, "db_hook_snapshot", None):
                context = cortex_hook._active_task_context(self.ledger, session_id, snapshot)
                self.assertIsNotNone(context)
                self.assertEqual(context["task_id"], state["task_id"])

    def test_subagent_stop_without_result_is_terminal_and_bounded(self):
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            started = self.v3_start(
                "stop a native worker without a result",
                waves=[{"workers": [{"phase": "discover"}]}],
            )
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        parent_session = "host-stop-parent"
        control.bind_host_session_from_hook(str(self.project), started["task_ref"], parent_session)
        bound = control.bind_host_worker_from_hook(
            str(self.project), state["task_id"], parent_session, "default",
            "native.Stop:01", attempt["expected_model"],
        )
        self.assertTrue(bound["bound"], bound)
        stopped = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "SubagentStop",
                "session_id": parent_session,
                "agent_id": "native.Stop:01",
                "agent_type": "default",
                "cwd": str(self.project),
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        self.assertEqual(stopped.stdout.strip(), "{}", stopped.stderr)
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        self.assertEqual(attempt["host_stop_outcome"], "native_worker_stopped_without_result")
        self.assertEqual(attempt["status"], "failed")
        self.assertFalse(attempt["host_resumable"])
        self.assertEqual(attempt["finalization_reason"], "native_worker_stopped_without_result")
        waited = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "PostToolUse",
                "session_id": parent_session,
                "cwd": str(self.project),
                "tool_name": "wait",
                "tool_input": {"receiver_thread_ids": ["native.Stop:01"]},
                "tool_response": {"status": "completed"},
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
            check=True,
        )
        wait_output = json.loads(waited.stdout)
        wait_context = wait_output["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(wait_output["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("server recorded", wait_context)
        self.assertIn("recoverable", wait_context)
        self.assertIn("Do not submit a synthetic result", wait_context)
        self.assertIn("recover_inspect", wait_context)
        self.assertNotIn("Submit exactly one result", wait_context)
        self.assertIn("native_worker_stopped_without_result", wait_context)
        self.assertNotIn("followup_task", wait_context)
        self.assertIn(started["task_ref"], wait_context)
        inspected = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "inspect",
        })
        self.assertEqual(inspected["context_handoff"]["active_workers"], [])
        self.assertFalse(inspected["context_handoff"]["stopped_workers"][0]["resumable"])
        self.assertIn("continue_orchestration", inspected["next_action"])
        self.assertNotIn("followup_task", inspected["next_action"])
        self.assertNotIn("Wait only on", inspected["next_action"])
        failed = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{
                "status": "failed",
                "reason": "native_worker_stopped_without_result",
                "dispatch_ref": attempt["dispatch_ref"],
            }],
        })
        # The hook and attempt state use the canonical
        # ``native_worker_stopped_without_result`` marker.  The failed slot
        # must remain addressable in the active wave so this exact recovery
        # receipt is accepted instead of failing cardinality validation.
        self.assertTrue(failed["ok"], failed)
        self.assertEqual(failed["outcome"], "ready_to_spawn")
        self.assertEqual(len(failed["dispatches"]), 1)




    def test_recover_blocked_repairs_stale_failed_attempt_without_canonical_result(self):
        """A stale/lease-expired attempt must replay one Planner recovery, never block the task."""
        started = self.v3_start("recover stale failed attempt without result", waves=[
            {"workers": [{"phase": "discover"}]},
            {"workers": [{"phase": "implementation"}]},
        ])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        attempt = state["attempts"][0]
        lease_field = (
            "spawn_lease_expires_at"
            if attempt["status"] == control.AWAITING_HOST_SPAWN
            else "worker_lease_expires_at"
        )
        attempt[lease_field] = "2000-01-01T00:00:00+00:00"
        self.write_task_state(state)

        # The lifecycle repair first proves that the native lease is stale and
        # records the failed attempt without inventing a canonical result.
        repaired = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "recover_inspect",
        })
        self.assertTrue(repaired["ok"], repaired)

        recovered = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "recover_blocked",
        })
        self.assertTrue(recovered["ok"], recovered)
        self.assertEqual(recovered["outcome"], "ready_to_spawn", recovered)
        self.assertNotEqual(recovered.get("state"), "blocked", recovered)
        self.assertFalse(recovered.get("requires_user_decision", False), recovered)
        self.assertFalse(
            (recovered.get("user_view") or {}).get("requires_user_decision", False),
            recovered,
        )
        self.assertEqual(len(recovered.get("dispatches") or []), 1, recovered)
        self.assertEqual(recovered["dispatches"][0].get("phase"), "discover", recovered)
        first_dispatch_ref = recovered["dispatches"][0]["dispatch_ref"]

        replay = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "recover_blocked",
        })
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["outcome"], "ready_to_spawn", replay)
        self.assertEqual(len(replay.get("dispatches") or []), 1, replay)
        self.assertEqual(replay["dispatches"][0]["dispatch_ref"], first_dispatch_ref)
        self.assertFalse(replay.get("requires_user_decision", False))


    def test_technical_active_gate_always_gets_server_recovery_not_user_block(self):
        """All stale technical terminal markers route to corrective work automatically."""
        for index, (attempt_status, lifecycle_status) in enumerate((
            ("failed", "FAILED"),
            ("blocked", "BLOCKED"),
            ("failed", "needs_recovery"),
        ), start=1):
            with self.subTest(attempt_status=attempt_status, lifecycle_status=lifecycle_status):
                started = self.v3_start(
                    f"technical recovery invariant {index}",
                    waves=[{"workers": [{"phase": "discover"}]}],
                )
                task_dir, _, _, _ = control._v3_resolve_task({
                    "project_root": str(self.project),
                    "task_ref": started["task_ref"],
                }, require_task_ref=True)
                state = self.task_state(task_dir)
                attempt = state["attempts"][0]
                attempt.update({
                    "status": attempt_status,
                    "lifecycle_status": lifecycle_status,
                    "attempt_result_ref": None,
                    "invalidated": False,
                })
                state["status"] = "active"
                self.write_task_state(state)

                recovered = control.manage_orchestration({
                    "project_root": str(self.project),
                    "task_ref": started["task_ref"],
                    "intent": "recover_inspect",
                })
                self.assertTrue(recovered["ok"], recovered)
                self.assertNotIn(recovered.get("outcome"), {"needs_input", "error"}, recovered)
                self.assertNotEqual(recovered.get("state"), "blocked", recovered)
                self.assertFalse(recovered.get("requires_user_decision", False), recovered)
                self.assertFalse(
                    (recovered.get("user_view") or {}).get("requires_user_decision", False),
                    recovered,
                )
                dispatches = recovered.get("dispatches") or recovered.get("spawn_requests") or []
                self.assertTrue(dispatches, recovered)
                self.assertEqual(dispatches[0].get("phase"), "discover", recovered)
                self.assertNotIn("CORTEX ACTIVE WORKER", str(recovered.get("next_action") or ""))



    def test_compaction_inspect_recovers_only_still_pending_immutable_dispatch(self):
        started = self.v3_start(
            "recover one unstarted immutable dispatch",
            waves=[{"workers": [{"phase": "discover"}]}],
        )
        inspected = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "inspect",
        })
        self.assertTrue(inspected["ok"], inspected)
        self.assertEqual(inspected["outcome"], "ready_to_spawn")
        self.assertEqual(len(inspected["dispatches"]), 1)
        recovered = inspected["dispatches"][0]
        original = started["dispatches"][0]
        for key in ("dispatch_ref", "briefing_digest"):
            self.assertEqual(recovered[key], original[key])
        pending = inspected["context_handoff"]["pending_dispatches"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["dispatch_ref"], original["dispatch_ref"])
        self.assertEqual(inspected["context_handoff"]["active_workers"], [])

    def test_v3_shared_host_session_hook_binding_fails_closed_until_unambiguous(self):
        hook = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex_hook.py"
        with mock.patch.dict(
            os.environ,
            {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CORTEX_ROOT": ""},
            clear=False,
        ):
            starts = [
                self.v3_start(
                    f"shared host task {index}",
                    waves=[{"workers": [{"phase": "discover"}]}],
                )
                for index in range(1, 4)
            ]
            self.assertTrue(all(item["ok"] for item in starts))
            for started in starts:
                subprocess.run(
                    [sys.executable, str(hook)],
                    input=json.dumps({
                        "hook_event_name": "PostToolUse",
                        "session_id": "shared-host",
                        "cwd": str(self.project),
                        "tool_name": "mcp__cortex__start_orchestration",
                        "tool_input": {"project_root": str(self.project)},
                        "tool_response": {"structuredContent": started},
                    }),
                    text=True,
                    capture_output=True,
                    env={**os.environ, "CORTEX_PROJECT_ROOT": ""},
                    check=True,
                )
            self.assertFalse((self.ledger / "active-tasks.json").exists())
            self.assertIsNone(cortex_hook.active_task(self.ledger, "shared-host"))
            completed = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps({"hook_event_name": "SessionStart", "session_id": "shared-host", "cwd": str(self.project), "source": "compact"}),
                text=True,
                capture_output=True,
                env={**os.environ, "CORTEX_ROOT": "", "CORTEX_PROJECT_ROOT": str(self.project)},
                check=True,
            )
            self.assertEqual(completed.stdout.strip(), "{}")

            index = control.read_task_index(self.ledger)
            bindings = control._host_session_bindings(self.ledger)
            task_ids = sorted(task_id for task_id, session in bindings["tasks"].items() if session == "shared-host")
            self.assertEqual(len(task_ids), 3)
            for task_id in task_ids[:2]:
                task_dir = self.ledger / "tasks" / index[task_id]["directory"]
                state = self.task_state(task_dir)
                state["status"] = "completed"
                self.write_task_state(state)
                control.remove_active_mapping(self.ledger, task_id, str(state.get("thread_id") or ""))
            self.assertEqual(cortex_hook.active_task(self.ledger, "shared-host"), task_ids[2])

    def test_orchestration_is_inactive_until_main_chat_command(self):
        with self.assertRaisesRegex(ValueError, "inactive"):
            control.init_task({"task_id": "inactive", "user_request": "nope", "complexity": "C1", "principal": "thread-a"})
        activated = self.activate()
        self.assertTrue(activated["active"])
        self.assertEqual(activated["activation"]["coordinator"], "main")
        self.assertEqual(activated["activation"]["identity_assurance"], "caller_asserted_principal_and_thread")
        self.assertEqual(activated["activation"]["dispatch_attestation"], "not_host_attested")
        self.assertTrue(control.activation_status({"principal": "thread-a"})["active"])

    def test_activation_requires_exact_command_and_rejects_agent_profile(self):
        with self.assertRaisesRegex(ValueError, "Cortex skill route"):
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
        self.assertIn("cortex:orchestrator", result["next_action"])
        self.assertNotIn("/cortex", result["next_action"])

    def test_default_ledger_is_host_private_and_project_bound(self):
        with self.assertRaisesRegex(ValueError, "project_root is required"):
            control.ledger_root()
        root = control.ledger_root({"project_root": str(self.project)})
        self.assertEqual(root, self.ledger)
        self.assertTrue((root / "tasks").is_dir())

    def test_project_root_rejects_system_and_home_roots_before_manifest_capture(self):
        task = {
            "user_request": "Do not scan the host filesystem",
            "complexity": "C1",
            "acceptance_criteria": ["The request is rejected before a manifest is captured."],
            "verification": ["Observe the public validation response."],
        }
        with mock.patch.object(control, "capture_project_manifest") as manifest:
            for unsafe_root in (Path("/"), Path.home().absolute(), Path("/tmp")):
                response = self._handlers["start_orchestration"]({
                    "project_root": str(unsafe_root),
                    "task": task,
                })
                self.assertFalse(response["ok"])
                self.assertIn("specific repository or worktree", response["diagnostics"][0]["message"])
        manifest.assert_not_called()

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
                private_ledger = control.ledger_root_path(arguments)
                self.assertEqual(activated["ledger_root"], str(private_ledger))
                classified = self._handlers["classify_task"]({"complexity": "C1", "requirements": [], "principal": "thread-a", **arguments})
                created = self._handlers["init_task"]({"task_id": "plugin-cwd", "user_request": "workspace binding", "complexity": "C1", "classification_id": classified["classification_id"], "principal": "thread-a", **arguments})
                self.assertEqual(created["ledger_root"], str(private_ledger))
                self.assertTrue((private_ledger / "cortex.db").is_file())
                self.assertFalse((private_ledger / "tasks" / created["task_directory"]).exists())
                self.assertFalse((root / ".codex/cortex/cortex.db").exists())
                observed = self._handlers["status"]({"task_id": "plugin-cwd", "principal": "thread-a", **arguments})
                self.assertEqual(observed["task"]["project_root"], project)
                self.assertEqual(observed["ledger_root"], str(private_ledger))
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
        self.assertEqual(control.db_task_artifact_path(root, "first-task"), root / "tasks" / "0001-first-task")
        self.assertEqual(control.db_task_artifact_path(root, "second-task"), root / "tasks" / "0002-second-task")
        self.assertFalse((root / "tasks" / "0001-first-task").exists())
        self.assertFalse((root / "tasks" / "0002-second-task").exists())
        self.assertEqual(control.status({"task_id": "first-task", "principal": "thread-a"})["task"]["task_number"], 1)

    def test_activation_persists_until_main_chat_returns_to_normal(self):
        self.activate()
        first_classification = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        control.init_task({"task_id": "first-task", "user_request": "one task", "complexity": "C1", "classification_id": first_classification["classification_id"], "principal": "thread-a"})
        second_classification = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "inactive"):
            control.init_task({"task_id": "second-task", "user_request": "second", "complexity": "C1", "classification_id": second_classification["classification_id"], "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "Cortex skill route"):
            control.deactivate_orchestration({"user_command": "normal", "principal": "thread-a"})
        control.deactivate_orchestration({"user_command": "/normal", "principal": "thread-a"})
        self.assertFalse(control.activation_status({"principal": "thread-a"})["active"])

    def test_activation_status_infers_the_only_bound_activation(self):
        self.activate()
        inferred = control.activation_status({})
        self.assertTrue(inferred["active"])
        self.assertTrue(inferred["identity_inferred"])
        self.assertEqual(inferred["activation"]["principal"], "thread-a")

    def test_resumed_root_coordinator_alias_does_not_lose_task_ownership(self):
        self.activate(principal="root")
        classified = control.classify_task({
            "complexity": "C1", "requirements": [], "principal": "root", "thread_id": "root",
        })
        control.init_task({
            "task_id": "root-alias", "user_request": "resume root task",
            "classification_id": classified["classification_id"],
            "principal": "root", "thread_id": "root",
        })
        resumed = control.status({"task_id": "root-alias", "principal": "/root"})
        self.assertTrue(resumed["active"])
        self.assertEqual(resumed["state"]["principal"], "root")

    def test_init_consumes_classification_contract_without_duplicate_inputs(self):
        self.activate()
        requirements = ["implementation, verification, and documentation", "preserve the durable ledger"]
        classified = control.classify_task({"complexity": "C2", "requirements": requirements, "principal": "thread-a"})
        created = control.init_task({
            "task_id": "receipt-contract", "user_request": "consume the immutable classification contract",
            "classification_id": classified["classification_id"], "principal": "thread-a",
        })
        self.assertEqual(created["state"]["complexity"], "C2")
        task = self.task_definition(self.ledger / "tasks" / created["task_directory"])
        self.assertEqual(task["requirements"], requirements)

    def test_init_ignores_duplicate_inputs_and_consumes_authoritative_receipt(self):
        self.activate()
        classified = control.classify_task({"complexity": "C2", "requirements": ["original"], "principal": "thread-a"})
        created = control.init_task({
            "task_id": "mismatched-contract", "user_request": "consume authoritative receipt",
            "classification_id": classified["classification_id"], "complexity": "C3", "requirements": ["replacement"],
            "principal": "thread-a",
        })
        self.assertEqual(created["state"]["complexity"], "C2")
        task = self.task_definition(self.ledger / "tasks" / created["task_directory"])
        self.assertEqual(task["requirements"], ["original"])

    def test_init_ignores_truncated_c3_pipeline_and_uses_classification_receipt(self):
        self.activate()
        classified = control.classify_task({"complexity": "C3", "requirements": ["cross-system database security work"], "principal": "thread-a"})
        truncated = ["plan", "discover", "implementation", "qa", "review"]
        created = control.init_task({
            "task_id": "c3-truncated-pipeline", "user_request": "consume authoritative C3 pipeline",
            "classification_id": classified["classification_id"], "pipeline": truncated,
            "principal": "thread-a",
        })
        self.assertEqual(created["state"]["current_pipeline"], classified["pipeline"])
        self.assertIn("documentation", created["state"]["current_pipeline"])
        self.assertIn("close", created["state"]["current_pipeline"])
        self.assertEqual(created["pipeline_correction"], {
            "requested": truncated, "used": classified["pipeline"], "source": "classification_receipt",
        })

    def test_init_rejects_a_different_user_request_for_an_existing_task(self):
        first = self.init(task_id="resume-existing", complexity="C2")
        control.activate_orchestration({"user_command": "/cortex", "principal": "thread-a", "thread_id": "thread-a"})
        classified = control.classify_task({"complexity": "C3", "requirements": ["repeat audit"], "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "different user_request"):
            control.init_task({
                "task_id": "resume-existing", "user_request": "different generated wording",
                "classification_id": classified["classification_id"], "principal": "thread-a", "thread_id": "thread-a",
            })
        self.assertTrue(control.status({"task_id": "resume-existing", "principal": "thread-a"})["active"])

    def test_incomplete_classification_receipt_is_never_repaired_from_caller_input(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": ["preserve current behavior"], "principal": "thread-a"})
        receipt = control.db_get_classification(self.ledger, classified["classification_id"])
        del receipt["requirements"]
        control.db_put_classification(self.ledger, receipt)
        with self.assertRaisesRegex(ValueError, "classification receipt requirements are invalid"):
            control.init_task({
                "task_id": "result-receipt", "user_request": "require explicit current inputs",
                "classification_id": classified["classification_id"], "principal": "thread-a",
            })
        with self.assertRaisesRegex(ValueError, "classification receipt requirements are invalid"):
            control.init_task({
                "task_id": "result-receipt", "user_request": "require explicit current inputs",
                "classification_id": classified["classification_id"], "requirements": ["preserve current behavior"],
                "principal": "thread-a",
            })

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
        ])
        self.assertEqual(classified["conditional_gates"], [])
        self.assertEqual(
            classified["pipeline_corrections"],
            [
                {"gate": "documentation", "reason": "mandatory C3 audit gate", "advisory": True},
                {"gate": "close", "reason": "mandatory C3 audit gate", "advisory": True},
            ],
        )


    def test_classify_still_rejects_unknown_pipeline_gate_ids(self):
        self.activate()
        with self.assertRaisesRegex(ValueError, "pipeline contains unsupported gate ids: mystery"):
            control.classify_task({
                "complexity": "C2",
                "requirements": ["unknown gate should fail closed"],
                "pipeline": ["mystery"],
                "principal": "thread-a",
            })

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
            "plan", "discover", "implementation", "review",
        ])
        self.assertEqual(revised["state"]["parallel_groups"], [
            ["plan"], ["discover"], ["implementation"], ["review"],
        ])
        self.assertNotIn("database_architecture", revised["state"]["current_pipeline"])

    def test_security_sol_route_uses_complexity_effort_floor(self):
        for complexity, expected_effort in (("C1", "medium"), ("C2", "high"), ("C3", "xhigh")):
            with self.subTest(complexity=complexity):
                route = control.resolve_dispatch_route({
                    "agent": "security_auditor", "task_kind": "security", "risk": "low",
                    "complexity": complexity, "requested_model": "gpt-5.6-sol",
                    "requested_reasoning_effort": "low",
                })
                self.assertEqual(route["policy_model"], "gpt-5.6-sol")
                self.assertEqual(route["selected_model"], "gpt-5.6-sol")
                self.assertEqual(route["selected_reasoning_effort"], expected_effort)
                self.assertEqual(route["model_choice_reason"], "security_policy")

        with self.assertRaisesRegex(ValueError, "security work always uses"):
            control.resolve_dispatch_route({
                "agent": "security_auditor", "task_kind": "security", "risk": "low",
                "complexity": "C1", "requested_model": "gpt-5.6-terra",
            })
        with self.assertRaisesRegex(ValueError, "security work always uses"):
            control.resolve_dispatch_route({
                "agent": "security_auditor", "task_kind": "security", "risk": "low",
                "complexity": "C1", "user_requested_model": "gpt-5.6-terra",
            })

    def test_security_profile_normalizes_contradictory_lightweight_kind_to_sol(self):
        route = control.resolve_dispatch_route({"agent": "security_auditor", "task_kind": "reading", "risk": "low", "complexity": "C1"})
        self.assertEqual(route["task_kind"], "security")
        self.assertEqual(route["policy_model"], "gpt-5.6-sol")
        self.assertEqual(route["selected_model"], "gpt-5.6-sol")

    def test_security_gate_normalizes_contradictory_lightweight_kind_to_sol_in_delegation(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": ["security"], "principal": "thread-a"})
        state = control.init_task({"task_id": "security-route", "user_request": "security routing", "complexity": "C1", "pipeline": ["security", "close"], "classification_id": classified["classification_id"], "principal": "thread-a"})["state"]
        delegation = self.delegate(state, "security-route", "security", "security_auditor", task_kind="reading", risk="low", requested_reasoning_effort="high")
        self.assertEqual(delegation["spawn_request"]["model"], "gpt-5.6-sol")
        self.assertEqual(delegation["state"]["attempts"][-1]["task_kind"], "security")

    def test_each_lightweight_dispatch_routes_independently_of_task_complexity(self):
        for complexity, risk, expected_model, expected_effort in (
            ("C1", "low", "gpt-5.6-luna", "medium"),
            ("C2", "low", "gpt-5.6-luna", "medium"),
            ("C3", "moderate", "gpt-5.6-luna", "medium"),
            ("C1", "high", "gpt-5.6-luna", "high"),
            ("C2", "critical", "gpt-5.6-luna", "xhigh"),
        ):
            with self.subTest(complexity=complexity, risk=risk):
                route = control.resolve_dispatch_route({"agent": "explorer", "task_kind": "reading", "risk": risk, "complexity": complexity})
                self.assertEqual(route["policy_model"], expected_model)
                self.assertEqual(route["selected_model"], expected_model)
                self.assertEqual(route["selected_reasoning_effort"], expected_effort)

    def test_explorer_keeps_luna_and_coordinator_selected_effort(self):
        route = control.resolve_dispatch_route({
            "agent": "explorer",
            "task_kind": "runtime_investigation",
            "risk": "moderate",
            "complexity": "C3",
            "requested_reasoning_effort": "medium",
        })
        self.assertEqual(route["policy_model"], "gpt-5.6-luna")
        self.assertEqual(route["selected_model"], "gpt-5.6-luna")
        self.assertEqual(route["selected_reasoning_effort"], "medium")
        self.assertEqual(route["model_choice_reason"], "explorer_policy")
        self.assertTrue(route["read_only"])
        with self.assertRaisesRegex(ValueError, "explorer always uses"):
            control.resolve_dispatch_route({
                "agent": "explorer", "task_kind": "runtime_investigation", "risk": "moderate",
                "complexity": "C3", "requested_model": "gpt-5.6-terra",
            })

    def test_configured_default_luna_omits_native_model_and_separates_expectation(self):
        route = control.resolve_dispatch_route({
            "agent": "explorer",
            "task_kind": "reading",
            "risk": "low",
            "complexity": "C1",
            "configured_default_model": "gpt-5.6-luna",
            "available_models": ["gpt-5.6-sol", "gpt-5.6-terra"],
            "requested_reasoning_effort": "high",
        })
        self.assertEqual(route["expected_model"], "gpt-5.6-luna")
        self.assertEqual(route["selected_model"], "gpt-5.6-luna")
        self.assertEqual(route["model_resolution"], "configured_default")
        self.assertEqual(route["selected_reasoning_effort"], "high")

    def test_configured_default_delegation_omits_model_key_but_confirms_actual_luna(self):
        state = self.init(task_id="configured-default-luna") ["state"]
        observed = control.status({"task_id": "configured-default-luna", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "configured-default-luna", "principal": "thread-a",
            "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
            "gate": "discover", "agent": "explorer", "task_kind": "reading", "risk": "low",
            "configured_default_model": "gpt-5.6-luna",
            "available_models": ["gpt-5.6-sol", "gpt-5.6-terra"],
            "objective": "configured Luna", "ownership": "Read-only discovery",
            "allowed_paths": ["."], "acceptance_criteria": ["Record findings"],
            "verification": ["Cite paths"],
        })
        request = delegated["spawn_request"]
        self.assertEqual(request["host_tool"], "spawn_agent")
        self.assertNotIn("model", request)
        self.assertEqual(request["expected_model"], "gpt-5.6-luna")
        self.assertEqual(request["model_resolution"], "configured_default")
        self.assertEqual(request["reasoning_effort"], "medium")
        self.assertEqual(request["fork_turns"], "none")
        confirmed = control.confirm_host_spawn({
            "task_id": "configured-default-luna", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_tool": "spawn_agent", "host_agent_id": "luna-child",
            "host_task_name": request["task_name"], "host_model": "gpt-5.6-luna",
            "host_reasoning_effort": request["reasoning_effort"],
        })
        self.assertTrue(confirmed["confirmed"])
        self.assertEqual(confirmed["host_spawn"]["model_verification"], "verified")

    def test_configured_default_keeps_explicit_terra_override(self):
        route = control.resolve_dispatch_route({
            "agent": "general", "task_kind": "implementation", "risk": "moderate", "complexity": "C2",
            "configured_default_model": "gpt-5.6-luna", "requested_model": "gpt-5.6-terra",
            "requested_reasoning_effort": "high",
        })
        self.assertEqual(route["selected_model"], "gpt-5.6-terra")
        self.assertEqual(route["model_resolution"], "explicit_override")




    def test_explicit_luna_and_terra_overrides_keep_adaptive_effort_floor(self):
        for agent in ("planner", "general", "code_reviewer"):
            for requested_model, requested_effort, expected_effort in (
                ("gpt-5.6-luna", "low", "xhigh"),
                ("gpt-5.6-luna", "high", "xhigh"),
                ("gpt-5.6-luna", "max", "max"),
                ("gpt-5.6-terra", "low", "high"),
                ("gpt-5.6-terra", "high", "high"),
                ("gpt-5.6-terra", "max", "max"),
            ):
                with self.subTest(agent=agent, model=requested_model, effort=requested_effort):
                    route = control.resolve_dispatch_route({
                        "agent": agent, "task_kind": "implementation", "risk": "moderate",
                        "complexity": "C2", "requested_model": requested_model,
                        "requested_reasoning_effort": requested_effort,
                    })
                    self.assertEqual(route["selected_model"], requested_model)
                    self.assertEqual(route["selected_reasoning_effort"], expected_effort)

    def test_effort_above_max_is_rejected_for_every_model_route(self):
        cases = (
            {"agent": "explorer", "task_kind": "discovery", "requested_model": "gpt-5.6-luna"},
            {"agent": "planner", "task_kind": "planning", "requested_model": "gpt-5.6-luna"},
            {"agent": "general", "task_kind": "implementation", "requested_model": "gpt-5.6-terra"},
            {
                "agent": "general", "task_kind": "implementation",
                "requested_model": "gpt-5.6-sol", "user_requested_model": "gpt-5.6-sol",
            },
            {"agent": "security_auditor", "task_kind": "security", "requested_model": "gpt-5.6-sol"},
        )
        for case in cases:
            with self.subTest(agent=case["agent"], model=case["requested_model"]):
                with self.assertRaisesRegex(ValueError, "supported effort"):
                    control.resolve_dispatch_route({
                        **case, "risk": "high", "complexity": "C3",
                        "requested_reasoning_effort": "ultra",
                    })

    def test_explorer_model_is_profile_bound_not_task_kind_bound(self):
        route = control.resolve_dispatch_route({
            "agent": "explorer",
            "task_kind": "implementation",
            "risk": "high",
            "complexity": "C3",
        })
        self.assertEqual(route["policy_model"], "gpt-5.6-luna")
        self.assertEqual(route["selected_model"], "gpt-5.6-luna")
        self.assertEqual(route["selected_reasoning_effort"], "high")

    def test_terra_task_kinds_route_uncertain_work_independently_of_risk(self):
        for task_kind in ("diagnosis", "research", "runtime_investigation", "root_cause_analysis", "code_review", "long_context", "integration_conflict"):
            with self.subTest(task_kind=task_kind):
                route = control.resolve_dispatch_route({
                    "agent": "general",
                    "task_kind": task_kind,
                    "risk": "low",
                    "complexity": "C1",
                })
                self.assertEqual(route["policy_model"], "gpt-5.6-terra")
                self.assertEqual(route["selected_model"], "gpt-5.6-terra")
                self.assertEqual(route["selected_reasoning_effort"], "high")
                self.assertEqual(route["policy_reason"], "terra_task_kind")
        bounded_analysis = control.resolve_dispatch_route({
            "agent": "general", "task_kind": "analysis", "risk": "low", "complexity": "C1",
        })
        self.assertEqual(bounded_analysis["selected_model"], "gpt-5.6-luna")
        self.assertEqual(bounded_analysis["selected_reasoning_effort"], "high")

    def test_record_gate_returns_revision_correction_instead_of_stale_revision_error(self):
        state = self.init(task_id="gate-revision", complexity="C1")["state"]
        result = control.record_gate({
            "task_id": "gate-revision",
            "principal": "thread-a",
            "expected_revision": state["revision"] + 7,
            "gate": "discover",
            "outcome": "skipped",
            "skip_reason": "read-only routing test",
        })
        self.assertTrue(result["state"])
        self.assertEqual(result["revision_correction"], {"requested": state["revision"] + 7, "used": state["revision"]})

    def test_record_gate_rejects_inactive_gate_without_mutating_state(self):
        state = self.init(task_id="gate-mismatch", complexity="C1")["state"]
        result = control.record_gate({
            "task_id": "gate-mismatch",
            "principal": "thread-a",
            "expected_revision": state["revision"] + 7,
            "gate": "plan",
            "outcome": "passed",
        })
        self.assertFalse(result["recorded"])
        self.assertTrue(result["recoverable"])
        self.assertTrue(result["retryable"])
        self.assertFalse(result["state_changed"])
        self.assertEqual(result["reason"], "gate_mismatch")
        self.assertEqual(result["requested_gate"], "plan")
        self.assertEqual(result["active_gates"], ["discover"])
        self.assertEqual(result["next_action"], "retry_with_active_gate")
        self.assertEqual(result["revision_correction"], {"requested": state["revision"] + 7, "used": state["revision"]})
        persisted = control.status({"task_id": "gate-mismatch", "principal": "thread-a"})["state"]
        self.assertEqual(persisted["revision"], state["revision"])
        self.assertEqual(persisted["current_gates"], ["discover"])
        self.assertEqual(persisted["gates"], {})

    def test_planner_defaults_follow_complexity(self):
        simple = control.resolve_dispatch_route({
            "agent": "planner", "task_kind": "planning", "risk": "low", "complexity": "C1",
        })
        self.assertEqual(simple["selected_model"], "gpt-5.6-luna")
        self.assertEqual(simple["selected_reasoning_effort"], "high")
        route = control.resolve_dispatch_route({
            "agent": "planner", "task_kind": "reading", "risk": "low", "complexity": "C2",
        })
        self.assertEqual(route["selected_model"], "gpt-5.6-terra")
        self.assertEqual(route["selected_reasoning_effort"], "high")

    def test_every_ordinary_profile_follows_its_canonical_model_class(self):
        for agent in control.MODEL_PROFILE_CLASSES["efficient"]:
            with self.subTest(profile_class="efficient", agent=agent):
                route = control.resolve_dispatch_route({
                    "agent": agent, "task_kind": "documentation", "risk": "moderate", "complexity": "C3",
                })
                self.assertEqual(route["selected_model"], "gpt-5.6-luna")
                self.assertEqual(route["selected_reasoning_effort"], "xhigh")
                self.assertEqual(route["policy_reason"], "efficient_profile")
        for agent in control.MODEL_PROFILE_CLASSES["deep"]:
            with self.subTest(profile_class="deep", agent=agent):
                route = control.resolve_dispatch_route({
                    "agent": agent, "task_kind": "analysis", "risk": "low", "complexity": "C1",
                })
                self.assertEqual(route["selected_model"], "gpt-5.6-terra")
                self.assertEqual(route["selected_reasoning_effort"], "high")
                self.assertEqual(route["policy_reason"], "deep_profile")
        for agent in control.MODEL_PROFILE_CLASSES["adaptive"]:
            with self.subTest(profile_class="adaptive", agent=agent, complexity="C1"):
                simple = control.resolve_dispatch_route({
                    "agent": agent, "task_kind": "implementation", "risk": "low", "complexity": "C1",
                })
                self.assertEqual(simple["selected_model"], "gpt-5.6-luna")
                self.assertEqual(simple["selected_reasoning_effort"], "high")
                self.assertEqual(simple["policy_reason"], "bounded_adaptive_work")
            with self.subTest(profile_class="adaptive", agent=agent, complexity="C2"):
                consequential = control.resolve_dispatch_route({
                    "agent": agent, "task_kind": "implementation", "risk": "moderate", "complexity": "C2",
                })
                expected_model = "gpt-5.6-terra" if agent == "planner" else "gpt-5.6-luna"
                expected_effort = "high" if agent == "planner" else "xhigh"
                expected_reason = "complex_planning" if agent == "planner" else "bounded_adaptive_work"
                self.assertEqual(consequential["selected_model"], expected_model)
                self.assertEqual(consequential["selected_reasoning_effort"], expected_effort)
                self.assertEqual(consequential["policy_reason"], expected_reason)


    def test_terra_style_task_kind_is_canonicalized_at_the_mcp_boundary(self):
        for supplied, expected, model in (("Code Review", "code_review", "gpt-5.6-luna"), ("READ-ONLY", "read_only", "gpt-5.6-luna"), ("data   gathering", "data_gathering", "gpt-5.6-luna")):
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







    def test_thread_environment_is_rejected_for_hidden_subagents(self):
        state = self.init(task_id="hidden-thread-environment")["state"]
        observed = control.status({"task_id": "hidden-thread-environment", "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "applies only to visible_thread"):
            control.record_delegation({
                "task_id": "hidden-thread-environment", "principal": "thread-a",
                "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
                "gate": "discover", "agent": "explorer", "task_kind": "reading", "risk": "low",
                "thread_environment": "local", "objective": "Inspect", "ownership": "Read-only",
                "allowed_paths": ["."], "acceptance_criteria": ["Record"], "verification": ["Cite"],
            })

    def test_visible_thread_requires_its_own_host_model_catalog(self):
        state = self.init(task_id="visible-thread-catalog")["state"]
        observed = control.status({"task_id": "visible-thread-catalog", "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "visible_thread requires exact available_thread_models"):
            control.record_delegation({
                "task_id": "visible-thread-catalog", "principal": "thread-a",
                "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
                "gate": "discover", "agent": "explorer", "task_kind": "reading", "risk": "low",
                "dispatch_mode": "visible_thread", "objective": "Inspect", "ownership": "Read-only",
                "allowed_paths": ["."], "acceptance_criteria": ["Record"], "verification": ["Cite"],
            })

    def test_visible_thread_is_rejected_as_a_luna_fallback(self):
        state = self.init(task_id="luna-thread-fallback")["state"]
        observed = control.status({"task_id": "luna-thread-fallback", "principal": "thread-a"})
        with self.assertRaisesRegex(ValueError, "luna_fallback must be terra"):
            control.record_delegation({
                "task_id": "luna-thread-fallback", "principal": "thread-a",
                "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
                "gate": "discover", "agent": "explorer", "task_kind": "discover", "risk": "low",
                "available_models": ["gpt-5.6-sol", "gpt-5.6-terra"],
                "available_thread_models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
                "luna_fallback": "visible_thread",
                "objective": "Inspect a narrow question", "ownership": "Read-only discovery",
                "allowed_paths": ["."], "acceptance_criteria": ["Record findings"],
                "verification": ["Cite paths"],
            })

    def test_luna_thread_fallback_keeps_hidden_luna_when_spawn_agent_supports_it(self):
        state = self.init(task_id="luna-fallback-not-needed")["state"]
        delegated = self.delegate(
            state,
            "luna-fallback-not-needed",
            "discover",
            "explorer",
            task_kind="discover",
            available_models=["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
            available_thread_models=["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
            luna_fallback="terra",
        )
        self.assertEqual(delegated["spawn_request"]["host_tool"], "spawn_agent")
        self.assertEqual(delegated["spawn_request"]["model"], "gpt-5.6-luna")

    def test_adaptive_profile_matrix_uses_bounded_luna_and_high_cost_terra(self):
        for complexity in ("C1", "C2", "C3"):
            for risk in ("low", "moderate", "high", "critical"):
                with self.subTest(complexity=complexity, risk=risk):
                    route = control.resolve_dispatch_route({"agent": "general", "task_kind": "implementation", "risk": risk, "complexity": complexity})
                    expected_model = "gpt-5.6-luna" if risk in {"low", "moderate"} else "gpt-5.6-terra"
                    effort_map = (
                        control.LUNA_BOUNDED_EFFORT_BY_COMPLEXITY
                        if expected_model == "gpt-5.6-luna"
                        else control.TERRA_EFFORT_BY_COMPLEXITY
                    )
                    expected_effort = control.higher_effort(
                        effort_map[complexity],
                        control.MODEL_EFFORT_FLOOR_BY_RISK[risk],
                    )
                    self.assertEqual(route["policy_model"], expected_model)
                    self.assertEqual(route["selected_model"], expected_model)
                    self.assertEqual(route["selected_reasoning_effort"], expected_effort)
                    self.assertEqual(
                        route["selected_reasoning_effort"] == "max",
                        expected_model == "gpt-5.6-luna" and complexity == "C3",
                    )

    def test_non_security_sol_requires_explicit_user_model_request(self):
        with self.assertRaisesRegex(ValueError, "requires user_requested_model"):
            control.resolve_dispatch_route({
                "agent": "general", "task_kind": "implementation", "risk": "high", "complexity": "C3",
                "requested_model": "gpt-5.6-sol",
            })
        with self.assertRaisesRegex(ValueError, "must match requested_model"):
            control.resolve_dispatch_route({
                "agent": "general", "task_kind": "implementation", "risk": "high", "complexity": "C3",
                "requested_model": "gpt-5.6-terra", "user_requested_model": "gpt-5.6-sol",
            })

    def test_explicit_user_request_permits_non_security_sol(self):
        route = control.resolve_dispatch_route({
            "agent": "general", "task_kind": "migration", "risk": "critical", "complexity": "C3",
            "user_requested_model": "gpt-5.6-sol",
            "requested_reasoning_effort": "xhigh",
        })
        self.assertEqual(route["policy_model"], "gpt-5.6-terra")
        self.assertEqual(route["selected_model"], "gpt-5.6-sol")
        self.assertEqual(route["selected_reasoning_effort"], "xhigh")
        self.assertEqual(route["model_choice_reason"], "explicit_user_request")
        self.assertEqual(route["user_requested_model"], "gpt-5.6-sol")


    def test_explorer_rejects_user_requested_sol(self):
        with self.assertRaisesRegex(ValueError, "explorer always uses"):
            control.resolve_dispatch_route({
                "agent": "explorer", "task_kind": "discovery", "risk": "high", "complexity": "C3",
                "requested_model": "gpt-5.6-sol", "user_requested_model": "gpt-5.6-sol",
            })

    def test_evidence_is_required_to_pass(self):
        result = self.init()
        state = result["state"]
        delegation = self.delegate(state, "demo", "discover", "explorer")
        self.assertEqual(delegation["state"]["attempts"][-1]["display_name"], "Explorer Objective")
        delegation_file = self.task_document(control.db_task_artifact_path(self.ledger, "demo"), f"dispatch:{delegation['attempt_id']}")
        self.assertEqual(delegation_file["display_name"], "Explorer Objective")
        self.assertEqual(delegation_file["profile"], "explorer")
        self.assertEqual(delegation_file["selected_model"], "gpt-5.6-luna")
        self.assertEqual(delegation_file["selected_reasoning_effort"], "medium")
        self.assertFalse(delegation_file["user_facing"])
        self.assertEqual(delegation_file["question_route"]["mode"], "pull")
        self.assertEqual(delegation_file["question_route"]["worker_tool"], "cortex.question")
        self.assertEqual(delegation_file["question_route"]["publish_tool"], "publish_worker_question")
        self.assertEqual(delegation_file["question_route"]["coordinator_surface"], "ordinary_final_chat_message")
        self.assertEqual(delegation_file["question_route"]["pause_until"], "next_user_message")
        self.assertEqual(delegation_file["escalation_route"], "main_chat")
        self.assertEqual(delegation_file["handoff_route"], "main_chat")
        evidence = control.record_evidence({"task_id": "demo", "principal": "thread-a", "expected_revision": delegation["state"]["revision"], "gate": "discover", "attempt_id": delegation["attempt_id"], "kind": "result", "summary": "inspection completed", "command": "Authorization: Bearer <TOKEN>"})
        self.assertIn("<REDACTED>", evidence["state"]["evidence"][0]["command"])
        evidence_record = evidence["evidence"]
        artifact = control.db_get_artifact_for_export_path(
            self.ledger, "demo", "evidence/evidence-0001.json",
        )
        self.assertIsNotNone(artifact)
        export_path = control.db_task_artifact_path(self.ledger, "demo") / "evidence/evidence-0001.json"
        canonical_content = control.db_read_artifact_content(
            self.ledger, "demo", str(artifact["artifact_ref"]),
        )
        self.assertEqual(export_path.read_text(encoding="utf-8"), canonical_content)
        self.assertEqual(
            hashlib.sha256(canonical_content.encode("utf-8")).hexdigest(),
            artifact["digest_sha256"],
        )
        self.assertNotIn("artifact_ref", json.loads(canonical_content))
        self.assertEqual(evidence_record["artifact_ref"], artifact["artifact_ref"])
        self.assertEqual(evidence_record["artifact_digest"], artifact["digest_sha256"])
        closed = control.record_gate({"task_id": "demo", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed"})
        self.assertEqual(closed["state"]["current_gates"], ["implementation"])



    def test_gate_ignores_tampered_evidence_projection(self):
        created = self.init(task_id="evidence-reconciliation", complexity="C1")
        state = created["state"]
        evidence = control.record_evidence({
            "task_id": "evidence-reconciliation", "principal": "thread-a",
            "expected_revision": state["revision"], "gate": "discover", "summary": "Observed repository evidence.",
        })
        task_dir = self.ledger / "tasks/0001-evidence-reconciliation"
        canonical = control.db_get_artifact_for_export_path(
            self.ledger, "evidence-reconciliation", "evidence/evidence-0001.json",
        )
        self.assertIsNotNone(canonical)
        self.reconcile_projections(worker_id="evidence-projection-test")
        path = task_dir / "evidence/evidence-0001.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["summary"] = "tampered"
        path.write_text(json.dumps(record), encoding="utf-8")
        passed = control.record_gate({
            "task_id": "evidence-reconciliation", "principal": "thread-a",
            "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed",
        })
        self.assertIn("discover", passed["state"]["completed_gates"])


    def test_failed_terminal_attempt_without_evidence_cannot_pass_gate(self):
        state = self.init(task_id="failed-only-attempt", complexity="C2")["state"]
        failed = self.delegate(state, "failed-only-attempt", "discover", "explorer")
        finalized = control.finalize_attempt({
            "task_id": "failed-only-attempt",
            "principal": "thread-a",
            "expected_revision": failed["state"]["revision"],
            "attempt_id": failed["attempt_id"],
            "status": "failed",
            "reason": "worker failed before producing a result",
        })
        closed = control.record_gate({
            "task_id": "failed-only-attempt",
            "principal": "thread-a",
            "expected_revision": finalized["state"]["revision"],
            "gate": "discover",
            "outcome": "passed",
        })
        self.assertEqual(closed["state"]["attempts"][0]["status"], "failed")
        self.assertIn("advisories", closed)
        self.assertEqual(closed["advisories"][0]["reason"], "evidence_recommended")

    def test_active_running_attempt_without_evidence_still_blocks_gate(self):
        state = self.init(task_id="active-attempt", complexity="C2")["state"]
        delegation = self.delegate(state, "active-attempt", "discover", "explorer")
        pending = control.record_gate({
                "task_id": "active-attempt",
                "principal": "thread-a",
                "expected_revision": delegation["state"]["revision"],
                "gate": "discover",
                "outcome": "passed",
            })
        self.assertIn("advisories", pending)
        self.assertEqual(pending["advisories"][0]["reason"], "evidence_recommended")


    def test_invalidated_terminal_attempt_remains_idempotent(self):
        state = self.init(task_id="invalidated-terminal", complexity="C2")["state"]
        original = self.delegate(state, "invalidated-terminal", "discover", "explorer")
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
            "operations": [{"op": "rework", "gate": "discover"}],
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
            "display_name": "General Objective",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "high",
        }
        package = self.task_document(control.db_task_artifact_path(self.ledger, "spawn-contract"), f"dispatch:{delegation['attempt_id']}")
        self.assertEqual(package["lifecycle_status"], "running_acknowledged")
        self.assertEqual(package["attempt_status"], "running")
        self.assertEqual({key: delegation["spawn_request"][key] for key in expected}, expected)
        self.assertRegex(
            delegation["spawn_request"]["task_name"],
            r"^general_objective_01_[0-9a-f]{8}$",
        )
        self.assertNotEqual(delegation["spawn_request"]["task_name"], delegation["spawn_request"]["display_name"])
        self.assertEqual({key: package["spawn_request"][key] for key in expected}, expected)
        self.assertEqual({key: delegation["state"]["attempts"][-1]["spawn_request"][key] for key in expected}, expected)
        briefing = self.briefing_from_request(delegation["spawn_request"])
        self.assertIn("# Cortex Worker Briefing v3", briefing)
        assignment = json.loads(briefing.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(assignment["profile"], "general")
        self.assertEqual(delegation["state"]["attempts"][-1]["dispatch_correlation"], "coordinator_recorded_host_spawn")

    def test_delegation_ignores_orphan_briefing_export_when_allocating_attempt(self):
        state = self.init(task_id="orphan-briefing-recovery")["state"]
        task_dir = control.db_task_artifact_path(self.ledger, "orphan-briefing-recovery")
        delegations = task_dir / "delegations"
        delegations.mkdir(parents=True, exist_ok=True)
        (delegations / "discover-07.dispatch-orphan123.briefing.md").write_text(
            "orphaned immutable briefing\n", encoding="utf-8"
        )
        delegation = self.delegate(state, "orphan-briefing-recovery", "discover", "explorer")
        self.assertEqual(delegation["attempt_id"], "discover-01")
        self.assertNotEqual(Path(delegation["spawn_request"]["briefing_path"]), delegations / "discover-07.dispatch-orphan123.briefing.md")
        self.assertEqual(self.task_state(task_dir)["attempts"][0]["attempt_id"], "discover-01")

    def test_native_worker_task_name_compacts_long_request_derived_task_ids(self):
        long_task_id = "cortex-orchestrator-local-plugin-cache-ca-bd8a9ad4"
        task_name = control.native_worker_task_name("planner", long_task_id, "plan-01")
        self.assertRegex(task_name, r"^planner_worker_01_[0-9a-f]{8}$")
        self.assertNotIn("plugin", task_name)
        self.assertNotIn("cache", task_name)
        self.assertNotIn("-", task_name)
        self.assertLessEqual(len(task_name), 80)
        self.assertNotEqual(
            task_name,
            control.native_worker_task_name(
                "planner", "cortex-orchestrator-local-plugin-cache-7f3e2a19", "plan-01"
            ),
        )

    def test_native_worker_task_names_obey_host_contract_for_every_profile(self):
        names = {
            control.native_worker_task_name(profile, "harvest-refresh", "plan-01")
            for profile in control.PROFILES
        }
        self.assertEqual(len(names), len(control.PROFILES))
        for task_name in names:
            self.assertRegex(task_name, r"^[a-z0-9_]{1,80}$")
            self.assertNotIn("-", task_name)

    def test_worker_display_name_humanizes_multiword_profiles_without_identity_suffix(self):
        self.assertEqual(control.worker_display_name("security_auditor", "Auth"), "Security Auditor Auth")
        self.assertEqual(control.worker_display_name("explorer", "Trading"), "Explorer Trading")

    def test_worker_module_label_prefers_explicit_feature_domain_over_command_words(self):
        request = (
            "$cortex:orchestrator harvest Run a source-backed full knowledge harvest. "
            "The authentication feature documentation must cover failures and recovery."
        )
        self.assertEqual(control.worker_module_label(request, ["."], "plan"), "Authentication")

        live_request = (
            "$cortex:orchestrator harvest Perform a complete source-backed Cortex harvest. "
            "Do not request post-plan user approval. Document the actual pricing behavior, "
            "positive and negative scenarios, state, interfaces, and recovery."
        )
        self.assertEqual(control.worker_module_label(live_request, ["."], "plan"), "Pricing")
        self.assertEqual(
            control.worker_display_name("planner", control.worker_module_label(live_request, ["."], "plan")),
            "Planner Pricing",
        )

        repository_request = (
            "$cortex:orchestrator harvest Perform a complete source-backed harvest. "
            "Complete every canonical phase through independent review and close verification."
        )
        self.assertEqual(control.worker_module_label(repository_request, ["."], "plan"), "Repository")
        self.assertEqual(
            control.worker_display_name(
                "planner", control.worker_module_label(repository_request, ["."], "plan")
            ),
            "Planner Repository",
        )

        refresh_request = (
            "$cortex:orchestrator harvest-refresh Refresh the repository knowledge exhaustively "
            "from current source, tests, configuration, and existing documentation."
        )
        self.assertEqual(control.worker_module_label(refresh_request, ["."], "plan"), "Repository")
        self.assertEqual(
            control.worker_display_name(
                "planner", control.worker_module_label(refresh_request, ["."], "plan")
            ),
            "Planner Repository",
        )

    def test_native_worker_task_name_remains_unique_after_hyphen_normalization(self):
        dashed = control.native_worker_task_name("planner", "harvest-refresh", "plan-01")
        underscored = control.native_worker_task_name("planner", "harvest_refresh", "plan_01")
        self.assertNotEqual(dashed, underscored)
        self.assertRegex(dashed, r"^[a-z0-9_]{1,80}$")
        self.assertRegex(underscored, r"^[a-z0-9_]{1,80}$")

    def test_host_spawn_confirmation_requires_the_exact_native_task_name(self):
        state = self.init(task_id="host-name-contract")["state"]
        observed = control.status({"task_id": "host-name-contract", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-name-contract", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "inspect",
            "ownership": "Read-only discovery", "allowed_paths": ["."],
            "acceptance_criteria": ["Record findings"], "verification": ["Cite paths"],
        })
        expected_task_name = delegated["spawn_request"]["task_name"]
        self.assertRegex(expected_task_name, r"^explorer_objective_01_[0-9a-f]{8}$")
        briefing = self.briefing_from_request(delegated["spawn_request"])
        self.assertIn('"attempt_id": "discover-01"', briefing)
        self.assertIn("Finish with complete_attempt", briefing)
        self.assertIn("Finish with complete_attempt", briefing)
        self.assertIn("do not submit changed_files", briefing)
        self.assertIn("Answer=>followup_task same child", briefing)
        self.assertIn("poll same ref/attempt first", briefing)
        self.assertIn("Do not activate or initialize Cortex", briefing)
        confirmed = control.confirm_host_spawn({
                "task_id": "host-name-contract", "principal": "thread-a",
                "expected_revision": delegated["state"]["revision"],
                "attempt_id": delegated["attempt_id"], "host_agent_id": "desktop-child-123",
                "host_task_name": expected_task_name, "host_model": delegated["spawn_request"]["model"],
            })
        self.assertTrue(confirmed["confirmed"])
        self.assertIsNone(confirmed["task_name_correction"])
        self.assertEqual(confirmed["host_spawn"]["task_name"], expected_task_name)

    def test_generic_host_spawn_cannot_bind_or_advance_an_issued_cortex_attempt(self):
        """A self-authored host child must never become a Cortex substitute."""
        state = self.init(task_id="generic-spawn-cannot-advance")["state"]
        observed = control.status({"task_id": "generic-spawn-cannot-advance", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "generic-spawn-cannot-advance", "principal": "thread-a",
            "expected_revision": state["revision"], "status_receipt": observed["status_receipt"],
            "gate": "discover", "agent": "explorer", "task_kind": "discover", "risk": "low",
            "objective": "inspect", "ownership": "Read-only discovery", "allowed_paths": ["."],
            "acceptance_criteria": ["Record findings"], "verification": ["Cite paths"],
        })
        generic = control.confirm_host_spawn({
            "task_id": "generic-spawn-cannot-advance", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_agent_id": "generic-collaboration-child", "host_task_name": "security-audit",
            "host_model": delegated["spawn_request"]["model"],
        })
        self.assertFalse(generic["confirmed"])
        self.assertTrue(generic["recoverable"])
        self.assertEqual(generic["reason"], "host_task_name_mismatch")
        self.assertEqual(generic["state"]["attempts"][-1]["status"], control.AWAITING_HOST_SPAWN)
        self.assertEqual(generic["state"]["attempts"][-1].get("host_spawn"), None)

        # Without the exact dispatched host target, the generic child cannot
        # turn the pending attempt into a successful gate receipt either.
        finalized = control.finalize_attempt({
            "task_id": "generic-spawn-cannot-advance", "principal": "thread-a",
            "expected_revision": generic["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "status": "passed",
        })
        self.assertFalse(finalized["recorded"])
        self.assertEqual(finalized["reason"], "host_spawn_confirmation_required")
        self.assertEqual(finalized["state"]["attempts"][-1]["status"], control.AWAITING_HOST_SPAWN)

    def test_host_spawn_confirmation_rejects_reused_native_child_id(self):
        self.init(task_id="host-id-reuse")
        result = control.prepare_delegations({
            "task_id": "host-id-reuse", "principal": "thread-a", "delegations": [
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "one", "ownership": "one", "allowed_paths": ["."], "acceptance_criteria": ["one"], "verification": ["one"]},
                {"gate": "discover", "agent": "explorer", "task_kind": "discovery", "risk": "low", "parallel": True, "objective": "two", "ownership": "two", "allowed_paths": ["."], "acceptance_criteria": ["two"], "verification": ["two"]},
            ],
        })
        first, second = result["spawn_requests"]
        first_attempt_id, second_attempt_id = result["attempts"]
        first_confirmed = control.confirm_host_spawn({
            "task_id": "host-id-reuse", "principal": "thread-a", "expected_revision": result["state"]["revision"],
            "attempt_id": first_attempt_id, "host_tool": "spawn_agent", "host_agent_id": "same-native-child",
            "host_task_name": first["task_name"], "host_model": first.get("model") or first["expected_model"],
            "host_reasoning_effort": first["reasoning_effort"],
        })
        self.assertTrue(first_confirmed["confirmed"])
        reused = control.confirm_host_spawn({
            "task_id": "host-id-reuse", "principal": "thread-a", "expected_revision": first_confirmed["state"]["revision"],
            "attempt_id": second_attempt_id, "host_tool": "spawn_agent", "host_agent_id": "same-native-child",
            "host_task_name": second["task_name"], "host_model": second.get("model") or second["expected_model"],
            "host_reasoning_effort": second["reasoning_effort"],
        })
        self.assertFalse(reused["confirmed"])
        self.assertTrue(reused["recoverable"])
        self.assertEqual(reused["reason"], "host_agent_id_reused")
        self.assertEqual(reused["state"]["attempts"][1]["status"], control.AWAITING_HOST_SPAWN)

    def test_host_spawn_confirmation_without_host_fields_is_recoverable(self):
        state = self.init(task_id="host-fields")["state"]
        observed = control.status({"task_id": "host-fields", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-fields", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "plan", "agent": "planner",
            "task_kind": "planning", "risk": "low", "objective": "plan",
            "ownership": "Own plan", "allowed_paths": ["."],
            "acceptance_criteria": ["Record findings"], "verification": ["Cite paths"],
        })
        recovered = control.confirm_host_spawn({
            "task_id": "host-fields", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
        })
        self.assertFalse(recovered["confirmed"])
        self.assertTrue(recovered["recoverable"])
        self.assertIn("host_agent_id", recovered["next_action"])

    def test_host_spawn_confirmation_requires_the_actual_host_model(self):
        state = self.init(task_id="host-model-required")["state"]
        observed = control.status({"task_id": "host-model-required", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-model-required", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "inspect",
            "ownership": "Read-only discovery", "allowed_paths": ["."],
            "acceptance_criteria": ["Record findings"], "verification": ["Cite paths"],
        })
        recovered = control.confirm_host_spawn({
            "task_id": "host-model-required", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_agent_id": "desktop-child-model-required",
            "host_task_name": delegated["spawn_request"]["task_name"],
        })
        self.assertFalse(recovered["confirmed"])
        self.assertTrue(recovered["recoverable"])
        self.assertEqual(recovered["reason"], "host_model_required")
        self.assertEqual(recovered["required_fields"], ["host_model"])
        self.assertEqual(recovered["state"]["attempts"][-1]["status"], control.AWAITING_HOST_SPAWN)

    def test_host_model_mismatch_fails_attempt_without_accepting_result(self):
        state = self.init(task_id="host-model-mismatch")["state"]
        observed = control.status({"task_id": "host-model-mismatch", "principal": "thread-a"})
        delegated = control.record_delegation({
            "task_id": "host-model-mismatch", "principal": "thread-a", "expected_revision": state["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "inspect",
            "ownership": "Read-only discovery", "allowed_paths": ["."],
            "acceptance_criteria": ["Record findings"], "verification": ["Cite paths"],
        })
        self.assertEqual(delegated["spawn_request"]["model"], "gpt-5.6-luna")
        mismatch = control.confirm_host_spawn({
            "task_id": "host-model-mismatch", "principal": "thread-a",
            "expected_revision": delegated["state"]["revision"], "attempt_id": delegated["attempt_id"],
            "host_agent_id": "desktop-child-terra-fallback",
            "host_task_name": delegated["spawn_request"]["task_name"],
            "host_model": "gpt-5.6-terra",
        })
        self.assertFalse(mismatch["confirmed"])
        self.assertTrue(mismatch["failed"])
        self.assertEqual(mismatch["reason"], "host_model_mismatch")
        self.assertEqual(mismatch["expected_model"], "gpt-5.6-luna")
        self.assertEqual(mismatch["actual_model"], "gpt-5.6-terra")
        self.assertEqual(mismatch["state"]["attempts"][-1]["status"], "failed")
        self.assertEqual(mismatch["state"]["attempts"][-1]["model_verification"], "mismatch")








    def test_delegation_infers_missing_gate_profile_kind_and_risk(self):
        state = self.init(task_id="inferred-delegation", complexity="C2")["state"]
        delegated = control.record_delegation({
            "task_id": "inferred-delegation", "principal": "thread-a",
        })
        self.assertEqual(delegated["state"]["attempts"][-1]["gate"], "discover")
        self.assertEqual(delegated["spawn_request"]["profile"], "explorer")
        self.assertEqual(delegated["state"]["attempts"][-1]["task_kind"], "discovery")
        self.assertEqual(delegated["state"]["attempts"][-1]["risk"], "low")
        self.assertEqual(delegated["agent_correction"], {"requested": None, "used": "explorer"})
        self.assertEqual(delegated["task_kind_correction"], {"requested": None, "used": "discovery"})
        self.assertEqual(delegated["risk_correction"], {"requested": None, "used": "low"})

    def test_rework_is_unbounded_and_escalates_effort_across_invalidated_attempts(self):
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
        third = control.record_delegation({
            "task_id": "retry-rework", "principal": "thread-a", "expected_revision": failed["state"]["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "third try", "ownership": "Read-only discovery",
            "allowed_paths": ["."], "acceptance_criteria": ["Record findings"], "verification": ["Cite paths"],
        })
        self.assertEqual(third["spawn_request"]["reasoning_effort"], "xhigh")
        failed = control.finalize_attempt({
            "task_id": "retry-rework", "principal": "thread-a", "expected_revision": third["state"]["revision"],
            "attempt_id": third["attempt_id"], "status": "failed", "reason": "third failed",
        })
        observed = control.status({"task_id": "retry-rework", "principal": "thread-a"})
        fourth = control.record_delegation({
            "task_id": "retry-rework", "principal": "thread-a", "expected_revision": failed["state"]["revision"],
            "status_receipt": observed["status_receipt"], "gate": "discover", "agent": "explorer",
            "task_kind": "discover", "risk": "low", "objective": "fourth try", "ownership": "Read-only discovery",
            "allowed_paths": ["."], "acceptance_criteria": ["Record findings"], "verification": ["Cite paths"],
        })
        self.assertEqual(fourth["spawn_request"]["reasoning_effort"], "max")
        self.assertEqual(fourth["state"]["attempts"][-1]["status"], control.AWAITING_HOST_SPAWN)

    def test_worker_question_bus_is_scoped_idempotent_and_resumable(self):
        state = self.init(task_id="questions")["state"]
        first = self.delegate(state, "questions", "discover", "general", parallel=True)
        second = self.delegate(first["state"], "questions", "discover", "explorer", parallel=True)
        publish_args = {
            "task_id": "questions",
            "principal": "thread-a",
            "attempt_id": first["attempt_id"],
            "submission_id": "need-decision",
            "question": "Which current behavior should I preserve?",
            "context": {"choices": ["strict", "current"]},
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
            "resume_context": {"instruction": "Continue with the strict current behavior", "source": "coordinator"},
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
            control.answer_worker_question({**answer_args, "answer": "Use the current mode."})


    def test_cortex_question_form_always_puts_custom_field_last(self):
        option_ids = [item["option_id"] for item in control._question_options(["A", "B"])]
        single = control._question_form_schema(control._question_config({"header": "Pick one", "options": ["A", "B"], "recommendation": "Choose A for the bounded default.", "recommended_option_ids": [option_ids[0]]}))
        self.assertEqual(list(single["properties"]), ["selection", "custom_response"])
        multi = control._question_form_schema(control._question_config({"header": "Pick many", "options": ["A", "B"], "multiple": True, "recommendation": "Choose A for the bounded default.", "recommended_option_ids": [option_ids[0]]}))
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
        # Gate/routing questions are internal Cortex concerns.  Question
        # Firewall routes them to the orchestrator as advice and never opens
        # a user-facing durable question.
        self.assertEqual(listed["open_count"], 0)
        self.assertEqual(listed["questions"], [])


    def test_codebase_memory_project_key_matches_upstream_path_rule(self):
        self.assertEqual(
            control.codebase_memory_project_key_from_root("/Users/dev/my-project"),
            "Users-dev-my-project",
        )
        self.assertEqual(
            control.codebase_memory_project_key_from_root(r"C:\Users\dev\project"),
            "C-Users-dev-project",
        )
        self.assertEqual(
            control.codebase_memory_project_key_from_root("/home///user/my project"),
            "home-user-my-project",
        )
        self.assertEqual(
            control.codebase_memory_project_key_from_root("/Users/yunxin/Desktop/开发/后端/信租风控通后端"),
            "Users-yunxin-Desktop-e5bc80e58f91-e5908ee7abaf-"
            "e4bfa1e7a79fe9a38ee68ea7e9809ae5908ee7abaf",
        )
        long_key = control.codebase_memory_project_key_from_root("/Users/dev/" + "开" * 60 + "/alpha")
        self.assertEqual(len(long_key), 200)
        self.assertRegex(long_key, r"-[0-9a-f]{8}$")


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
        task_names = [item["task_name"] for item in result["spawn_requests"]]
        self.assertEqual(len(set(task_names)), 2)
        self.assertTrue(all(item.startswith("explorer_") for item in task_names))
        self.assertTrue(all(re.fullmatch(r"[a-z0-9_]{1,80}", item) for item in task_names))
        self.assertTrue(all(item["profile"] == "explorer" for item in result["spawn_requests"]))

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
            "task_id": "parallel-wave", "user_request": "run independent gates concurrently",
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
        self.assertEqual(discover_passed["state"]["current_gates"], ["review"])
        self.assertEqual(discover_passed["state"]["current_gates"], ["review"])

    def test_reassessment_can_split_parallel_wave(self):
        self.activate()
        classified = control.classify_task({
            "complexity": "C1", "requirements": ["independent checks"],
            "pipeline": ["discover", "implementation", "review"],
            "parallel_groups": [["discover", "implementation"], ["review"]],
            "principal": "thread-a",
        })
        created = control.init_task({"task_id": "split-wave", "user_request": "reassess waves", "classification_id": classified["classification_id"], "principal": "thread-a"})
        revised = control.reassess_pipeline({
            "task_id": "split-wave", "principal": "thread-a", "expected_revision": created["state"]["revision"],
            "signals": ["implementation now depends on discovery"], "pipeline": ["discover", "implementation", "review"],
            "parallel_groups": [["discover"], ["implementation"], ["review"]],
            "intent": "resequence", "decision": "updated", "reason": "new dependency discovered", "apply": True,
        })
        self.assertTrue(revised["applied"])
        self.assertEqual(revised["state"]["parallel_groups"], [["discover"], ["implementation"], ["review"]])
        self.assertEqual(revised["state"]["current_gates"], ["discover"])


    def test_v3_minimal_start_defaults_to_c2_and_hides_durable_ids(self):
        started = self.v3_start("minimal relative orchestration")
        self.assertTrue(started["ok"])
        self.assertEqual(started["schema"], control.PUBLIC_ORCHESTRATION_SCHEMA)
        self.assertEqual(started["step"], 1)
        self.assertEqual(len(started["dispatches"]), 1)
        self.assertEqual(set(started["dispatches"][0]), {
            "worker", "phase", "profile", "display_name", "capability", "sandbox",
            "selection_reason", "call", "arguments", "dispatch_ref", "briefing_path",
            "briefing_digest",
        })
        self.assertEqual(started["dispatches"][0]["phase"], "discover")
        self.assertEqual(started["dispatches"][0]["profile"], "explorer")
        self.assertEqual(started["dispatches"][0]["sandbox"], "read-only")
        self.assertIn("canonical automatic owner", started["dispatches"][0]["selection_reason"])
        self.assertNotIn("COORDINATOR LOCK", started["next_action"])
        self.assertIn("Until every returned dispatch has been invoked", started["next_action"])
        self.assertNotIn("All project operations belong to workers", started["next_action"])
        self.assertEqual(started["dispatches"][0]["arguments"].get("model"), None)
        self.assertEqual(started["dispatches"][0]["arguments"]["reasoning_effort"], "medium")
        self.assertNotIn("task_id", started)
        self.assertNotIn("wave_id", started)
        tasks = list((self.ledger / "tasks").iterdir())
        definition = self.task_definition(tasks[0])
        self.assertEqual(definition["complexity"], "C2")
        self.assertEqual(definition["plan_approval"], "auto")




    def test_implementation_router_prefers_narrow_specialists_and_conservative_fallback(self):
        cases = {
            "Build a React frontend and an API backend for this workflow": "fullstack_dev",
            "Implement an Android screen in Jetpack Compose": "mobile_dev",
            "Update Docker and GitHub Actions deployment": "devops_engineer",
            "Implement an ETL backfill into the data warehouse": "data_engineer",
            "Build a data pipeline for warehouse imports": "data_engineer",
            "Reproduce the failing test, prove root cause, and fix it": "debugger",
            "Refactor the module without changing behavior": "refactorer",
            "Implement a browser component and CSS states": "frontend_dev",
            "Add a server API endpoint and business logic": "backend_dev",
            "Исправь почему сервис падает и найди корневую причину": "debugger",
            "Implement the bounded requested change": "general",
        }
        for objective, expected in cases.items():
            with self.subTest(objective=objective):
                selected = control.select_implementation_profile({"user_request": objective})
                self.assertEqual(selected["profile"], expected)
                self.assertTrue(selected["reason"])
                self.assertIn(selected["source"], {"bounded_task_signals", "conservative_fallback"})

    def test_automatic_waves_embed_specialist_implementation_rationale(self):
        waves = control._v3_auto_waves({
            "user_request": "Add a browser UI backed by a server API",
            "requirements": [],
            "complexity": "C2",
        })
        implementation = next(
            spec
            for wave in waves
            for spec in wave["delegations"]
            if spec["gate"] == "implementation"
        )
        self.assertEqual(implementation["agent"], "fullstack_dev")
        self.assertIn("both browser-facing and server-facing", implementation["selection_reason"])

    def test_external_ledger_continuation_omits_write_required_implementation_gate(self):
        """A Codex-only lifecycle request must not demand a fictitious project delta."""
        objective = (
            "Продолжи выполнять задачу codex://threads/01a01fed-4ccc-7321-bd2b-939d1adee101 "
            "в этом треде. Создай отдельную новую задачу в леджере."
        )
        task = {"user_request": objective, "requirements": [], "complexity": "C2"}
        self.assertTrue(control._is_external_lifecycle_only_task(task))
        waves = control._v3_auto_waves(task)
        gates = [spec["gate"] for wave in waves for spec in wave["delegations"]]
        self.assertEqual(gates, ["discover", "plan", "review", "documentation", "close"])
        self.assertNotIn("implementation", gates)
        self.assertNotIn("qa", gates)

        started = self.v3_start(objective)
        self.assertTrue(started["ok"])
        state = self.task_state(next((self.ledger / "tasks").iterdir()))
        self.assertEqual(
            state["current_pipeline"],
            ["discover", "plan", "review", "documentation", "close"],
        )

    def test_external_lifecycle_reference_with_project_mutation_keeps_implementation_gate(self):
        task = {
            "user_request": (
                "Continue codex://threads/01a01fed-4ccc-7321-bd2b-939d1adee101 in a new ledger task "
                "and fix the repository source code."
            ),
            "requirements": [],
            "complexity": "C2",
        }
        self.assertFalse(control._is_external_lifecycle_only_task(task))
        gates = [spec["gate"] for wave in control._v3_auto_waves(task) for spec in wave["delegations"]]
        self.assertIn("implementation", gates)

    def test_automatic_pipeline_reads_the_full_task_and_multilingual_specialist_signals(self):
        cases = {
            "Audit authorization security before changing the API": "security",
            "Проверь доступность интерфейса с клавиатуры": "accessibility",
            "Оптимизируй производительность и задержку сервиса": "performance",
            "Спроектируй миграцию схемы базы данных": "database_architecture",
        }
        for objective, expected_gate in cases.items():
            with self.subTest(objective=objective):
                waves = control._v3_auto_waves({
                    "user_request": objective,
                    "requirements": [],
                    "complexity": "C2",
                })
                gates = {spec["gate"] for wave in waves for spec in wave["delegations"]}
                self.assertIn(expected_gate, gates)

    def test_v3_harvest_routes_use_the_complete_census_pipeline(self):
        expected = ["scope", "discover", "architecture", "plan", "documentation", "review", "close"]
        for objective in (
            "Harvest exhaustive repository knowledge documentation",
            "Run harvest-refresh with a complete feature census",
        ):
            with self.subTest(objective=objective):
                waves = control._v3_auto_waves({
                    "user_request": objective,
                    "requirements": [],
                    "complexity": "C2",
                })
                self.assertEqual(
                    [spec["gate"] for wave in waves for spec in wave["delegations"]],
                    expected,
                )
                started = self.v3_start(objective)
                self.assertTrue(started["ok"])
                for dispatch in started["dispatches"]:
                    self.assertEqual(dispatch["call"], "spawn_agent")
                    self.assertRegex(dispatch["arguments"]["task_name"], r"^[a-z0-9_]{1,80}$")


    def test_v3_harvest_never_requires_post_plan_user_approval(self):
        started = self.v3_start("$cortex:orchestrator harvest", complexity="C3")
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        task = control.load_task_definition(task_dir)
        self.assertEqual(task["plan_approval"], "auto")

    def test_v3_profile_schema_exposes_exact_roster_and_rejects_wrong_gate_owner(self):
        self.assertTrue(control.AGENTS)
        rejected = self.v3_start(
            "invalid planner owner",
            waves=[{"workers": [{"phase": "plan", "profile": "backend_dev"}]}],
        )
        self.assertFalse(rejected["ok"])
        self.assertIn("cannot own phase", rejected["diagnostics"][0]["message"])
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))

    def test_v3_requires_an_observable_task_result_contract_before_writing_ledger(self):
        rejected = control.start_orchestration({
            "project_root": str(self.project),
            "task": {"user_request": "implement the requested behavior", "complexity": "C1"},
            "waves": [{"workers": [{"phase": "implementation"}]}],
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "start_validation_failed")
        self.assertIn("task.acceptance_criteria", rejected["diagnostics"][0]["message"])
        self.assertIn("task.verification", rejected["diagnostics"][0]["message"])
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))

    def test_v3_start_reports_nested_scope_schema_error_before_normalization(self):
        rejected = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "preserve the scope boundary",
                "acceptance_criteria": ["The requested outcome is observed."],
                "verification": ["Run an authoritative outcome check."],
                "scope": "Текущий репозиторий",
            },
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "start_validation_failed")
        diagnostic = next(item for item in rejected["diagnostics"] if item.get("path") == "task.scope")
        self.assertEqual(diagnostic["message"], "must be an array of non-empty strings")
        self.assertEqual(diagnostic["received"], {"type": "str"})
        self.assertEqual(diagnostic["field_schema"], {"type": "array", "items": {"type": "string", "minLength": 1}})
        self.assertIn("task.scope", rejected["next_action"])
        self.assertIn("task.scope", rejected["validation"]["invalid_paths"])
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))








    def test_v3_repeated_exact_start_is_idempotent_but_changed_work_creates_a_new_task(self):
        first = self.v3_start("stable objective", waves=[{"workers": [{"phase": "discover"}]}])
        replay = self.v3_start("stable objective", waves=[{"workers": [{"phase": "discover"}]}])
        changed = self.v3_start("stable objective complete end to end", waves=[{"workers": [{"phase": "discover"}]}])
        self.assertTrue(replay["ok"])
        self.assertEqual(replay["task_ref"], first["task_ref"])
        self.assertTrue(changed["ok"])
        self.assertNotEqual(changed["task_ref"], first["task_ref"])
        self.assertEqual(len(list((self.ledger / "tasks").iterdir())), 2)
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["dispatches"], [])
        self.assertIn("Do not invoke or repeat any worker dispatch", replay["next_action"])









    def test_v3_exact_user_request_rejects_coordinator_expansion_before_ledger_write(self):
        rejected = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "создай лендинг",
                "objective": "Create a polished responsive sales landing page with an accessible CTA.",
                "complexity": "C2",
            },
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "start_validation_failed")
        self.assertIn("unsupported task fields: objective", rejected["diagnostics"][0]["message"])
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))


    def test_v3_start_uses_host_activation_and_preserves_exact_request(self):
        activation = control.activate_orchestration({
            "project_root": str(self.project),
            "user_command": control.ACTIVATION_COMMAND,
            "principal": "thread-a",
            "thread_id": "thread-a",
        })
        self.assertTrue(activation["active"])
        request = "создай лендинг"
        started = self.v3_start(request, waves=[{"workers": [{"phase": "plan"}]}])
        self.assertTrue(started["ok"], started)
        task_dir = next((self.ledger / "tasks").iterdir())
        task = control.load_task_definition(task_dir)
        self.assertEqual(task["user_request"], request)
        self.assertNotIn("objective", task)
        self.assertRegex(task_dir.name, r"^0001-task-[0-9a-f]{8}$")
        self.assertNotIn("home", task_dir.name)
        self.assertNotIn("plugins", task_dir.name)
        self.assertNotIn("SKILL.md", json.dumps(task))
        briefing = self.briefing_from_response(started)
        self.assertNotIn("plugins/cache", briefing)
        self.assertTrue(task["intent_clarification_required"])
        self.assertIn("Cortex intent preflight: BLOCKING", briefing)

    def test_v3_detailed_product_surface_request_does_not_force_a_preflight_question(self):
        request = (
            "Create a landing page for B2B sales teams with lead generation as the primary goal; preserve the "
            "existing brand and copy, and use the current design system."
        )
        started = self.v3_start(request, waves=[{"workers": [{"phase": "plan"}]}])
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        task = control.load_task_definition(task_dir)
        self.assertFalse(task["intent_clarification_required"])
        self.assertEqual(control._intent_clarification_preflight("Implement mapping retry logic"), (False, None))
        self.assertEqual(control._intent_clarification_preflight("Fix the application crash"), (False, None))



    def test_v3_prune_removes_only_confirmed_stale_task_state_and_is_idempotent(self):
        old = self.v3_start("abandoned old task", waves=[{"workers": [{"phase": "discover"}]}])
        recent = self.v3_start("recent active task", waves=[{"workers": [{"phase": "discover"}]}])
        index = control.read_task_index(self.ledger)
        old_task_id = next(task_id for task_id in index if control._v3_task_ref(task_id) == old["task_ref"])
        old_dir = self.ledger / "tasks" / index[old_task_id]["directory"]
        old_state = self.task_state(old_dir)
        old_state["status"] = "completed"
        old_state["updated_at"] = "2000-01-01T00:00:00+00:00"
        self.write_task_state(old_state)
        old_task = control.load_task_definition(old_dir)
        self.assertIsNotNone(control.db_get_classification(self.ledger, old_task["classification_id"]))
        control.db_put_global(self.ledger, "resource_claims", {
            "old": {"scope_kind": "task", "scope_id": old_task_id},
            "lane": {"scope_kind": "lane", "scope_id": "shared-lane"},
        })
        lane_dir = self.ledger / "lanes" / "shared-lane"
        lane_dir.mkdir(parents=True)
        lane_definition = {"schema": control.SCHEMA, "lane_id": "shared-lane"}
        lane_state = {
            "schema": control.SCHEMA,
            "lane_id": "shared-lane",
            "bound_tasks": [old_task_id, next(task_id for task_id in index if task_id != old_task_id)],
        }
        control.db_put_lane(self.ledger, lane_definition, lane_state)
        keep = self.project / "keep.txt"
        keep.write_text("project data", encoding="utf-8")

        rejected = control.manage_orchestration({
            "project_root": str(self.project), "intent": "prune", "payload": {"older_than_days": 7},
        })
        self.assertFalse(rejected["ok"])
        self.assertTrue(old_dir.exists())
        pruned = control.manage_orchestration({
            "project_root": str(self.project),
            "intent": "prune",
            "payload": {"confirmation": "PRUNE", "older_than_days": 7},
        })
        self.assertTrue(pruned["ok"])
        self.assertEqual(pruned["pruned_task_refs"], [old["task_ref"]])
        self.assertFalse(old_dir.exists())
        self.assertTrue(keep.is_file())
        remaining_index = control.read_task_index(self.ledger)
        self.assertEqual(len(remaining_index), 1)
        recent_task_id = next(iter(remaining_index))
        self.assertEqual(control._v3_task_ref(recent_task_id), recent["task_ref"])
        registry = control._operation_registry(self.ledger)
        self.assertNotIn(old_task_id, registry["tasks"])
        self.assertTrue(all(item.get("task_id") != old_task_id for item in registry["starts"].values()))
        self.assertIsNone(control.db_get_classification(self.ledger, old_task["classification_id"]))
        claims = control.db_get_global(self.ledger, "resource_claims", {})
        self.assertEqual(set(claims), {"lane"})
        _, lane_state = control.db_get_lane(self.ledger, "shared-lane")
        self.assertEqual(lane_state["bound_tasks"], [recent_task_id])
        self.assertFalse((self.ledger / "active-tasks.json").exists())
        activations = control.db_get_global(self.ledger, "activations", {})
        self.assertTrue(all(item.get("task_id") != old_task_id for item in activations.values()))
        replay = control.manage_orchestration({
            "project_root": str(self.project),
            "intent": "prune",
            "payload": {"confirmation": "PRUNE", "older_than_days": 7},
        })
        self.assertEqual(replay["pruned_count"], 0)

    def test_v3_prune_failure_preserves_canonical_metadata_until_retry(self):
        started = self.v3_start("prune retry must retain canonical state", waves=[{"workers": [{"phase": "discover"}]}])
        index = control.read_task_index(self.ledger)
        task_id = next(task_id for task_id in index if control._v3_task_ref(task_id) == started["task_ref"])
        task_dir = self.ledger / "tasks" / index[task_id]["directory"]
        state = self.task_state(task_dir)
        state["status"] = "completed"
        state["updated_at"] = "2000-01-01T00:00:00+00:00"
        self.write_task_state(state)
        task = control.load_task_definition(task_dir)
        control.db_put_global(self.ledger, "resource_claims", {
            "task-claim": {"scope_kind": "task", "scope_id": task_id},
        })
        before_registry = control._operation_registry(self.ledger)

        with mock.patch.object(control, "_remove_prune_directory", side_effect=OSError("simulated filesystem outage")):
            failed = control.manage_orchestration({
                "project_root": str(self.project),
                "intent": "prune",
                "payload": {"confirmation": "PRUNE", "older_than_days": 7},
            })
        self.assertFalse(failed["ok"])
        self.assertTrue(task_dir.exists())
        self.assertIn(task_id, control.read_task_index(self.ledger))
        self.assertEqual(control._operation_registry(self.ledger), before_registry)
        self.assertEqual(control.db_get_global(self.ledger, "resource_claims", {}), {
            "task-claim": {"scope_kind": "task", "scope_id": task_id},
        })
        self.assertIsNotNone(control.db_get_classification(self.ledger, task["classification_id"]))
        tombstones = control.db_list_prune_tombstones(self.ledger, task_id=task_id)
        self.assertEqual([row["status"] for row in tombstones], ["failed"])

        retried = control.manage_orchestration({
            "project_root": str(self.project),
            "intent": "prune",
            "payload": {"confirmation": "PRUNE", "older_than_days": 7},
        })
        self.assertTrue(retried["ok"])
        self.assertFalse(task_dir.exists())
        self.assertNotIn(task_id, control.read_task_index(self.ledger))
        self.assertNotIn(task_id, control._operation_registry(self.ledger)["tasks"])
        self.assertEqual(control.db_get_global(self.ledger, "resource_claims", {}), {})
        self.assertIsNone(control.db_get_classification(self.ledger, task["classification_id"]))
        self.assertEqual(
            [row["status"] for row in control.db_list_prune_tombstones(self.ledger, task_id=task_id)],
            ["finalized"],
        )

    def test_v3_prune_retains_old_active_task_and_shared_classification_receipt(self):
        active = self.v3_start("long-running task must survive prune", waves=[{"workers": [{"phase": "discover"}]}])
        completed = self.v3_start("completed task can be pruned", waves=[{"workers": [{"phase": "discover"}]}])
        index = control.read_task_index(self.ledger)
        active_id = next(task_id for task_id in index if control._v3_task_ref(task_id) == active["task_ref"])
        completed_id = next(task_id for task_id in index if control._v3_task_ref(task_id) == completed["task_ref"])
        active_dir = self.ledger / "tasks" / index[active_id]["directory"]
        completed_dir = self.ledger / "tasks" / index[completed_id]["directory"]
        active_state = self.task_state(active_dir)
        completed_state = self.task_state(completed_dir)
        completed_task = control.load_task_definition(completed_dir)
        active_state["updated_at"] = "2000-01-01T00:00:00+00:00"
        completed_state["status"] = "completed"
        completed_state["updated_at"] = "2000-01-01T00:00:00+00:00"
        # Simulate an old shared receipt reference: pruning one task must not
        # delete evidence still owned by the retained active contract.
        active_state["classification_receipt"] = completed_task["classification_id"]
        self.write_task_state(active_state)
        self.write_task_state(completed_state)
        self.assertIsNotNone(control.db_get_classification(self.ledger, completed_task["classification_id"]))

        pruned = control.manage_orchestration({
            "project_root": str(self.project),
            "intent": "prune",
            "payload": {"confirmation": "PRUNE", "older_than_days": 7},
        })

        self.assertTrue(pruned["ok"])
        self.assertEqual(pruned["pruned_task_refs"], [completed["task_ref"]])
        self.assertTrue(active_dir.exists())
        self.assertFalse(completed_dir.exists())
        self.assertGreaterEqual(pruned["retained_nonterminal_count"], 1)
        self.assertIsNotNone(control.db_get_classification(self.ledger, completed_task["classification_id"]))


    def test_v3_validation_errors_preserve_the_selected_task_ref(self):
        started = self.v3_start("preserve task ref", waves=[{"workers": [{"phase": "discover"}]}])
        continued = control.continue_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [],
        })
        self.assertFalse(continued["ok"])
        self.assertEqual(continued["task_ref"], started["task_ref"])
        managed = control.manage_orchestration({
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "intent": "unsupported recovery intent",
        })
        self.assertFalse(managed["ok"])
        self.assertEqual(managed["task_ref"], started["task_ref"])

    def test_v3_concurrent_process_starts_preserve_one_registry(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        responses = []
        for index in range(1, 4):
            request = {
                    "jsonrpc": "2.0", "id": index, "method": "tools/call",
                    "params": {"name": "start_orchestration", "arguments": {
                        "project_root": str(self.project),
                        "task": {
                            "user_request": f"concurrent task {index}", "complexity": "C1",
                            "acceptance_criteria": ["The requested outcome is observed."],
                            "verification": ["Run an authoritative outcome check."],
                        },
                        "waves": [{"workers": [{"phase": "discover"}]}],
                    }},
            }
            completed = subprocess.run(
                [sys.executable, str(script), "--mcp-audience=coordinator"],
                input=json.dumps(request) + "\n", text=True,
                capture_output=True, check=True,
            )
            payload = json.loads(completed.stdout)
            self.assertIn("result", payload, completed.stderr)
            responses.append(payload["result"]["structuredContent"])
        self.assertTrue(all(item["ok"] for item in responses))
        self.assertEqual(len({item["task_ref"] for item in responses}), 3)
        registry = control._operation_registry(self.ledger)
        self.assertEqual(len(registry["tasks"]), 3)

    def test_v3_concurrent_identical_starts_share_the_pending_reservation(self):
        task = {
            "user_request": "one concurrent idempotent task", "complexity": "C1",
            "acceptance_criteria": ["The requested outcome is observed."],
            "verification": ["Run an authoritative outcome check."],
        }
        params = {
            "project_root": str(self.project),
            "task": task,
            "waves": [{"workers": [{"phase": "discover"}]}],
        }
        # Source-mode tests share one SQLite connection boundary; exercise
        # the same reservation invariant with serialized callers. Native host
        # concurrency is covered by the server integration contract.
        responses = [control.start_orchestration(params) for _ in range(3)]
        self.assertTrue(all(item["ok"] for item in responses))
        self.assertEqual(len({item["task_ref"] for item in responses}), 1)
        registry = control._operation_registry(self.ledger)
        self.assertEqual(len(registry["starts"]), 1)
        self.assertEqual(len(registry["tasks"]), 1)



    def test_v3_start_accepts_server_owned_allowed_paths_worker_field(self):
        started = self.v3_start(
            "explicit worker scope",
            waves=[{"workers": [{
                "phase": "discover",
                "profile": "explorer",
                "allowed_paths": ["README.md"],
            }]}],
        )
        self.assertTrue(started["ok"])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(state["attempts"][0]["allowed_paths"], ["README.md"])

    def test_v3_worker_allowed_paths_rejects_broad_scope(self):
        with self.assertRaisesRegex(ValueError, "explicit and non-broad"):
            control._v3_compact_waves([
                {"workers": [{"phase": "discover", "allowed_paths": ["."]}]},
            ], {"user_request": "narrow worker scope", "complexity": "C1"})

    def test_v3_visible_worker_requires_immutable_user_opt_in(self):
        with self.assertRaisesRegex(ValueError, "visible thread requires an explicit user-authorized task"):
            control._v3_compact_waves([
                {"workers": [{"phase": "review", "visible": True}]},
            ], {"user_request": "visible review", "complexity": "C1"})
        compact = control._v3_compact_waves([
            {"workers": [{"phase": "review", "visible": True}]},
        ], {
            "user_request": "visible review",
            "complexity": "C1",
            "visible_thread_requested": True,
        }, allow_visible_threads=True)
        self.assertEqual(compact[0]["delegations"][0]["dispatch_mode"], "visible_thread")


    def test_v3_planner_dispatch_preserves_full_briefing_and_advisory_volume_guidance(self):
        request = (
            "$cortex:orchestrator harvest\nRun a source-backed full knowledge harvest for this small repository as a "
            "C1 task. Use the normal harvest pipeline and do not request plan approval because this is a harvest "
            "command. Acceptance: every feature-bearing surface is mapped or explicitly excluded; the identity "
            "feature documentation explains actors, entry points, main and negative scenarios, state/data, interfaces, "
            "configuration, failure/recovery, verification, and exact source evidence; zero unexplained unmapped "
            "surfaces remain. Verification: run authoritative tests, validate links and source paths, and independently "
            "review completeness before closing."
        )
        started = self.v3_start(request, waves=[{"workers": [{"phase": "plan"}]}])
        prompt = self.briefing_from_response(started)
        bootstrap = started["dispatches"][0]["arguments"]["message"]
        serialized = json.dumps(started, ensure_ascii=False, separators=(",", ":"))
        # Prompt volume is worker guidance only.  The backend must preserve
        # and materialize the complete immutable briefing rather than reject
        # a valid assignment at an arbitrary transport threshold.
        self.assertGreater(len(prompt.encode("utf-8")), 15_000)
        self.assertIn("Prompt volume targets are advisory worker guidance only", prompt)
        self.assertLess(len(bootstrap.encode("utf-8")), 1_500)
        self.assertLess(len(serialized.encode("utf-8")), 8_000)
        self.assertLess(serialized.index("NEXT REQUIRED ACTION"), serialized.index("Cortex worker"))
        self.assertIn("Never claim it was sent or call wait without the returned child target", started["next_action"])
        self.assertIn("NEXT REQUIRED ACTION: FIRST", started["next_action"])
        self.assertIn("exact failed result Cortex already accepted", started["next_action"])
        self.assertIn("use list_agents defensively", started["next_action"])
        self.assertIn("THEN call every dispatch.call", started["next_action"])
        self.assertIn(
            "read its exact returned attempt_result_ref with read_worker_result, then copy that server-returned",
            started["next_action"],
        )
        self.assertIn(
            "Only after that successful server continuation or terminal audit, close that exact completed native child with close_agent",
            started["next_action"],
        )
        self.assertIn("Do not dispatch another worker before that close succeeds", started["next_action"])
        self.assertNotIn(
            "Read every returned attempt_result_ref with read_worker_result, then close that exact completed native child with close_agent. Only after every result",
            started["next_action"],
        )
        self.assertIn("close that exact completed native child with close_agent", started["next_action"])
        assignment = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(assignment["user_intent"]["projection"], request)
        self.assertRegex(assignment["user_intent"]["digest_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(prompt.count(request), 0)
        self.assertNotIn(request, serialized)
        self.assertIn("Direct reads are the issued briefing capability", bootstrap)
        self.assertIn("server receipt", bootstrap)
        self.assertIn("Never add prose to simulate a server receipt", bootstrap)
        self.assertRegex(started["dispatches"][0]["dispatch_ref"], r"^dispatch-[0-9a-f]{24}$")
        self.assertRegex(started["dispatches"][0]["briefing_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(started["dispatches"][0]["display_name"], "Planner Repository")
        self.assertRegex(started["dispatches"][0]["arguments"]["task_name"], r"^planner_repository_01_[0-9a-f]{8}$")
        self.assertIn("## Role contract", prompt)
        self.assertIn("Every microtask requires", prompt)


    def test_closure_gate_rejects_its_own_finalized_unresolved_result_before_mutation(self):
        """A closure verifier cannot pass from its own unresolved canonical row."""
        evidence = [{"evidence_id": "evidence-01", "attempt_id": "closure-01", "kind": "note"}]
        attempt = {
            "attempt_id": "closure-01",
            "status": "passed",
            "facade_managed": True,
            "attempt_result_ref": "attempt-result-closure-01",
        }
        for gate in ("governance_close", "close"):
            with self.subTest(gate=gate), \
                 mock.patch.object(gate_transitions, "_attempts_with_unresolved_canonical_results", return_value=["closure-01"]) as unresolved:
                current, recovery = gate_transitions._validate_pass_evidence(
                    self.project,
                    {"require_delegation": False},
                    {},
                    requested_gate=gate,
                    gate=gate,
                    outcome="passed",
                    revision_correction=None,
                    gate_evidence=evidence,
                    gate_attempts=[attempt],
                    non_terminal_attempts=[],
                    terminal_non_success_attempts=[],
                    passed_attempts=[attempt],
                )
            self.assertEqual(current, evidence)
            self.assertEqual(recovery["reason"], "closure_attempt_unresolved")
            self.assertEqual(recovery["recommended_next"], "rework_current_gate")
            self.assertTrue(recovery["advisory"])
            self.assertEqual(recovery["candidate_attempt_ids"], ["closure-01"])
            unresolved.assert_called_once_with(self.project, [attempt])

    def test_terminal_backstop_scans_only_current_closure_attempts(self):
        """Historical implementation/docs handoffs do not invalidate terminal acceptance."""
        state = {
            "task_id": "closure-backstop",
            "require_handoff": True,
            "current_pipeline": [
                "governance_activation", "implementation", "documentation",
                "governance_close", "close",
            ],
            "parallel_groups": [
                ["governance_activation"], ["implementation"], ["documentation"],
                ["governance_close"], ["close"],
            ],
            "governance": {"effective_mode": "full"},
            "completed_gates": [
                "governance_activation", "implementation", "documentation",
                "governance_close", "close",
            ],
            "skipped_gates": [],
            "documentation_receipt": {"attempt_id": "docs-01"},
            "reassessment_receipts": [{"receipt_id": "reassess-01"}],
            "handoff_created": True,
            "handoff_gate": "close",
            "attempts": [
                {"attempt_id": "implementation-01", "gate": "implementation", "status": "passed", "facade_managed": True, "attempt_result_ref": "attempt-result-implementation-01"},
                {"attempt_id": "docs-01", "gate": "documentation", "status": "passed", "facade_managed": True, "attempt_result_ref": "attempt-result-docs-01"},
                {"attempt_id": "governance-close-01", "gate": "governance_close", "status": "passed", "facade_managed": True, "attempt_result_ref": "attempt-result-governance-close-01"},
                {"attempt_id": "close-01", "gate": "close", "status": "passed", "facade_managed": True, "attempt_result_ref": "attempt-result-close-01"},
            ],
            "evidence": [
                {"attempt_id": attempt_id, "attempt_result_ref": result_ref}
                for attempt_id, result_ref in (
                    ("implementation-01", "attempt-result-implementation-01"),
                    ("docs-01", "attempt-result-docs-01"),
                    ("governance-close-01", "attempt-result-governance-close-01"),
                    ("close-01", "attempt-result-close-01"),
                )
            ],
        }
        # This fixture deliberately supplies a projected task directory rather
        # than creating a complete task/result ledger.  The terminal-result
        # backstop is covered separately; keep this test focused on whether
        # the unresolved-result scan is limited to the closure verifier rows.
        with mock.patch.object(control, "db_task_artifact_path", return_value=self.project), \
             mock.patch.object(control, "_attempts_missing_result_validation", return_value=[]) as finalized, \
             mock.patch.object(control, "_terminal_facade_attempts_with_live_sessions", return_value=[]), \
             mock.patch.object(control, "_attempts_with_unresolved_canonical_results", return_value=["governance-close-01"]) as unresolved:
            control.validate_completion_invariants(state, artifact_root=self.ledger)
        finalized.assert_called_once_with(self.project, state["attempts"])
        scanned = unresolved.call_args.args[1]
        self.assertEqual([item["gate"] for item in scanned], ["governance_close", "close"])
        self.assertNotIn("documentation", [item["gate"] for item in scanned])

    def test_terminal_backstop_allows_clean_closure_with_prior_unresolved_handoff(self):
        """A clean closure passes even when an earlier scoped result handed work on."""
        state = {
            "task_id": "closure-backstop-clean",
            "require_handoff": True,
            "current_pipeline": [
                "governance_activation", "implementation", "documentation",
                "governance_close", "close",
            ],
            "parallel_groups": [
                ["governance_activation"], ["implementation"], ["documentation"],
                ["governance_close"], ["close"],
            ],
            "governance": {"effective_mode": "full"},
            "completed_gates": [
                "governance_activation", "implementation", "documentation",
                "governance_close", "close",
            ],
            "skipped_gates": [],
            "documentation_receipt": {"attempt_id": "docs-01"},
            "reassessment_receipts": [{"receipt_id": "reassess-01"}],
            "handoff_created": True,
            "handoff_gate": "close",
            "attempts": [
                {"attempt_id": "docs-01", "gate": "documentation", "status": "passed", "facade_managed": True, "attempt_result_ref": "attempt-result-docs-01"},
                {"attempt_id": "governance-close-01", "gate": "governance_close", "status": "passed", "facade_managed": True, "attempt_result_ref": "attempt-result-governance-close-01"},
                {"attempt_id": "close-01", "gate": "close", "status": "passed", "facade_managed": True, "attempt_result_ref": "attempt-result-close-01"},
            ],
            "evidence": [
                {"attempt_id": attempt_id, "attempt_result_ref": result_ref}
                for attempt_id, result_ref in (
                    ("docs-01", "attempt-result-docs-01"),
                    ("governance-close-01", "attempt-result-governance-close-01"),
                    ("close-01", "attempt-result-close-01"),
                )
            ],
        }
        with mock.patch.object(control, "db_task_artifact_path", return_value=self.project), \
             mock.patch.object(control, "_attempts_missing_result_validation", return_value=[]), \
             mock.patch.object(control, "_terminal_facade_attempts_with_live_sessions", return_value=[]), \
             mock.patch.object(control, "_attempts_with_unresolved_canonical_results", return_value=[]) as unresolved, \
             mock.patch.object(control, "validate_governance_obligation_evidence"):
            control.validate_completion_invariants(state, artifact_root=self.ledger)
        self.assertEqual([item["gate"] for item in unresolved.call_args.args[1]], ["governance_close", "close"])


    def test_immutable_dispatch_briefing_has_no_hidden_size_rejection(self):
        """Cursor paging, not a file quota, bounds large briefing transport."""
        briefing_path = self.base / "large-dispatch.briefing.md"
        content = "x" * (control.MAX_BRIEFING_BYTES + 1)
        digest = control.write_text_immutable(briefing_path, content)
        self.assertEqual(hashlib.sha256(content.encode("utf-8")).hexdigest(), digest)
        self.assertEqual(
            control._read_private_text(briefing_path, "dispatch briefing", max_bytes=None),
            content,
        )










    def test_missing_planned_implementation_routes_closure_finding_back_to_plan(self):
        state = {
            "current_pipeline": ["plan", "documentation", "close"],
            "pipeline_obligations": [
                "plan", "implementation", "qa", "security", "performance",
                "review", "documentation", "close",
            ],
            "plan_approval": {"status": "approved"},
            "pipeline_changes": [],
            "attempts": [{"gate": "plan", "status": "passed"}],
        }
        target = gate_transitions._closure_rework_target(
            state,
            "close",
            [{"fingerprint": "implementation-absent", "details": {"affected_paths": ["plugins/cortex"]}}],
        )
        self.assertEqual(target, "plan")

    def test_closure_rework_does_not_classify_parent_traversal_as_documentation(self):
        state = {
            "current_pipeline": ["implementation", "documentation", "close"],
            "pipeline_obligations": ["implementation", "documentation", "close"],
            "pipeline_changes": [],
            "plan_approval": {"status": "not_required"},
            "attempts": [{"gate": "implementation", "status": "passed"}],
        }
        target = gate_transitions._closure_rework_target(
            state,
            "close",
            [{"details": {"affected_paths": ["docs/../src/main.py"]}}],
        )
        self.assertEqual(target, "implementation")

    def test_closure_rework_normalizes_documentation_path(self):
        state = {
            "current_pipeline": ["implementation", "documentation", "close"],
            "pipeline_obligations": ["implementation", "documentation", "close"],
            "pipeline_changes": [],
            "plan_approval": {"status": "not_required"},
            "attempts": [{"gate": "implementation", "status": "passed"}],
        }
        target = gate_transitions._closure_rework_target(
            state,
            "close",
            [{"details": {"affected_paths": ["docs/./features/index.md"]}}],
        )
        self.assertEqual(target, "documentation")

    def test_generic_governance_close_finding_preserves_passed_documentation(self):
        state = {
            "current_pipeline": [
                "implementation", "documentation", "governance_close", "close",
            ],
            "pipeline_obligations": [
                "implementation", "documentation", "governance_close", "close",
            ],
            "pipeline_changes": [],
            "plan_approval": {"status": "not_required"},
            "attempts": [
                {"gate": "implementation", "status": "passed"},
                {"gate": "documentation", "status": "passed"},
            ],
        }
        target = gate_transitions._closure_rework_target(
            state,
            "governance_close",
            [{"fingerprint": "generic-governance-finding", "details": {}}],
        )
        self.assertEqual(target, "governance_close")

    def test_docs_only_governance_close_finding_still_routes_to_documentation(self):
        state = {
            "current_pipeline": [
                "implementation", "documentation", "governance_close", "close",
            ],
            "pipeline_obligations": [
                "implementation", "documentation", "governance_close", "close",
            ],
            "pipeline_changes": [],
            "plan_approval": {"status": "not_required"},
            "attempts": [{"gate": "implementation", "status": "passed"}],
        }
        target = gate_transitions._closure_rework_target(
            state,
            "governance_close",
            [{"fingerprint": "docs-governance-finding", "details": {"affected_paths": ["docs/a.md"]}}],
        )
        self.assertEqual(target, "documentation")

    def test_generic_governance_close_rework_keeps_documentation_result_current(self):
        state = {
            "task_id": "governance-rework",
            "status": "active",
            "task_revision": 1,
            "current_pipeline": [
                "implementation", "documentation", "governance_close", "close",
            ],
            "parallel_groups": [
                ["implementation"], ["documentation"], ["governance_close"], ["close"],
            ],
            "pipeline_obligations": [
                "implementation", "documentation", "governance_close", "close",
            ],
            "pipeline_changes": [],
            "adaptive_events": [],
            "completed_gates": ["implementation", "documentation"],
            "skipped_gates": [],
            "gates": {},
            "evidence": [{"gate": "documentation", "invalidated": False}],
            "attempts": [
                {"attempt_id": "implementation-01", "gate": "implementation", "status": "passed", "invalidated": False},
                {"attempt_id": "documentation-01", "gate": "documentation", "status": "passed", "invalidated": False},
                {"attempt_id": "governance-close-01", "gate": "governance_close", "status": "passed", "invalidated": False},
            ],
        }
        target = gate_transitions._activate_closure_rework(
            state,
            gate="governance_close",
            findings=[{"fingerprint": "generic-governance-finding", "details": {}}],
            source_result_refs=["attempt-result-governance-close"],
        )
        self.assertEqual(target, "governance_close")
        self.assertEqual(state["completed_gates"], ["implementation", "documentation"])
        self.assertFalse(state["attempts"][1]["invalidated"])
        self.assertFalse(state["evidence"][0]["invalidated"])
        self.assertEqual(control.active_gates(state), ["governance_close"])

    def test_full_governance_future_rework_preserves_orchestrator_choice(self):
        """Full governance records advice without rewriting the chosen route."""
        state = {
            "task_id": "full-governance-future-rework",
            "revision": 17,
            "status": "active",
            "governance": {"effective_mode": "full"},
            "current_pipeline": [
                "governance_activation", "implementation", "documentation",
                "governance_close", "close",
            ],
            "parallel_groups": [
                ["governance_activation"], ["implementation"], ["documentation"],
                ["governance_close"], ["close"],
            ],
            "completed_gates": ["governance_activation", "implementation", "documentation"],
            "skipped_gates": [],
            "gates": {},
            "pipeline_changes": [],
            "adaptive_events": [],
            "evidence": [
                {"gate": "governance_activation", "invalidated": False},
                {"gate": "implementation", "invalidated": False},
                {"gate": "documentation", "invalidated": False},
            ],
            "attempts": [
                {"attempt_id": "ga-01", "gate": "governance_activation", "status": "passed", "invalidated": False},
                {"attempt_id": "implementation-01", "gate": "implementation", "status": "passed", "invalidated": False},
                {"attempt_id": "documentation-01", "gate": "documentation", "status": "passed", "invalidated": False},
            ],
        }
        # This is an intentionally non-standard replacement assembled from a
        # retained implementation prefix plus caller future waves GA, GC,
        # docs, close.  The backend records the deviation but preserves the
        # orchestrator-selected route.
        change = control.apply_pipeline_operations(
            state,
            pipeline=[
                "implementation", "governance_activation", "governance_close",
                "documentation", "close",
            ],
            parallel_groups=[
                ["implementation"], ["governance_activation"], ["governance_close"],
                ["documentation"], ["close"],
            ],
            operations=[
                {"op": "rework", "gate": "documentation"},
                {"op": "rework", "gate": "governance_activation"},
            ],
            allow_rework=True,
        )
        self.assertEqual(change["pipeline"], [
            "implementation", "governance_activation", "governance_close",
            "documentation", "close",
        ])
        control.append_pipeline_change(state, change, "regression future replacement")
        self.assertEqual(state["revision"], 17)
        self.assertEqual(state["current_pipeline"], change["pipeline"])
        # Only the explicitly reworked gates and their downstream evidence
        # are invalidated; the already-passed implementation remains valid.
        self.assertEqual(state["completed_gates"], ["implementation"])
        self.assertTrue(all(
            item["invalidated"]
            for item in state["attempts"]
            if item["gate"] != "implementation"
        ))
        self.assertFalse(next(item for item in state["attempts"] if item["gate"] == "implementation")["invalidated"])
        self.assertTrue(all(
            item["invalidated"]
            for item in state["evidence"]
            if item["gate"] != "implementation"
        ))
        self.assertFalse(next(item for item in state["evidence"] if item["gate"] == "implementation")["invalidated"])
        self.assertFalse(any(item["attempt_id"] == "ga-04" for item in state["attempts"]))
        self.assertEqual(control.active_gates(state), ["governance_activation"])

    def test_governance_activation_closure_rework_preserves_activation_first(self):
        state = {
            "task_id": "activation-closure-rework",
            "status": "active",
            "task_revision": 1,
            "governance": {"effective_mode": "full"},
            "current_pipeline": [
                "governance_activation", "implementation", "documentation",
                "governance_close", "close",
            ],
            "parallel_groups": [
                ["governance_activation"], ["implementation"], ["documentation"],
                ["governance_close"], ["close"],
            ],
            "pipeline_obligations": [
                "governance_activation", "implementation", "documentation",
                "governance_close", "close",
            ],
            "pipeline_changes": [],
            "adaptive_events": [],
            "completed_gates": ["governance_activation", "implementation", "documentation"],
            "skipped_gates": [],
            "gates": {},
            "evidence": [],
            "attempts": [
                {"attempt_id": "ga-01", "gate": "governance_activation", "status": "passed", "invalidated": False},
                {"attempt_id": "implementation-01", "gate": "implementation", "status": "passed", "invalidated": False},
                {"attempt_id": "documentation-01", "gate": "documentation", "status": "passed", "invalidated": False},
            ],
        }
        target = gate_transitions._activate_closure_rework(
            state,
            gate="governance_activation",
            findings=[{"fingerprint": "activation-review", "details": {}}],
            source_result_refs=["attempt-result-ga-01"],
        )
        self.assertEqual(target, "governance_activation")
        self.assertEqual(state["current_pipeline"], [
            "governance_activation", "implementation", "documentation",
            "governance_close", "close",
        ])
        self.assertEqual(state["parallel_groups"][0], ["governance_activation"])
        self.assertEqual(state["parallel_groups"][-2:], [["governance_close"], ["close"]])
        self.assertEqual(state["completed_gates"], [])
        self.assertTrue(all(item["invalidated"] for item in state["attempts"]))

    def test_pending_revision_activation_rework_canonicalizes_without_partial_failure(self):
        """The post-gate revision path cannot preserve a malformed prefix."""
        state = {
            "task_id": "pending-full-governance",
            "revision": 9,
            "status": "active",
            "governance": {"effective_mode": "full"},
            # Simulates a historical malformed future replacement immediately
            # before the pending semantic impact is consumed at the gate
            # boundary.
            "current_pipeline": [
                "implementation", "governance_activation", "governance_close",
                "documentation", "close",
            ],
            "parallel_groups": [
                ["implementation"], ["governance_activation"], ["governance_close"],
                ["documentation"], ["close"],
            ],
            "completed_gates": ["implementation", "governance_activation", "documentation"],
            "skipped_gates": [],
            "gates": {},
            "pipeline_changes": [],
            "adaptive_events": [],
            "attempts": [
                {"attempt_id": "implementation-01", "gate": "implementation", "status": "passed", "invalidated": False},
                {"attempt_id": "ga-01", "gate": "governance_activation", "status": "passed", "invalidated": False},
                {"attempt_id": "documentation-01", "gate": "documentation", "status": "passed", "invalidated": False},
            ],
            "evidence": [],
            "pending_revision_impact": {
                "task_revision": 3,
                "earliest_affected_gate": "governance_activation",
                "categories": ["policy"],
            },
        }
        with mock.patch.object(orchestration_engine, "save_state") as saved:
            updated, semantic_rework = orchestration_engine._apply_pending_revision_impact(
                {"project_root": str(self.project)}, self.ledger, state, {"waves": []},
            )
        self.assertTrue(semantic_rework)
        self.assertEqual(updated["current_pipeline"], [
            "implementation", "governance_activation", "governance_close",
            "documentation", "close",
        ])
        self.assertEqual(updated["completed_gates"], ["implementation"])
        self.assertTrue(all(
            item["invalidated"] for item in updated["attempts"]
            if item["gate"] != "implementation"
        ))
        self.assertNotIn("pending_revision_impact", updated)
        self.assertEqual(updated["status"], "active")
        saved.assert_called_once()

    def test_failed_advance_rejects_altered_future_recovery_identity(self):
        """A failed gates_recorded receipt is immutable, including recovery waves."""
        self.activate()
        original = {
            "operation": "advance",
            "submission_id": "full-governance-recovery",
            "task_id": "full-governance-future-rework",
            "future_waves": [{"wave_id": "wave-04", "delegations": [{"gate": "documentation"}]}],
            "allow_rework": True,
            "reason": "First bounded recovery contract.",
        }
        _path, receipt, _replay = orchestration_engine._begin_orchestrate_transaction(self.ledger, original)
        receipt.update({"status": "failed", "phase": "gates_recorded"})
        control.db_put_operation(self.ledger, original["submission_id"], receipt)
        altered = {
            **original,
            "future_waves": [{"wave_id": "wave-04", "delegations": [{"gate": "governance_activation"}]}],
            "reason": "Altered recovery contract.",
        }
        with self.assertRaisesRegex(ValueError, "reused with different content"):
            orchestration_engine._begin_orchestrate_transaction(self.ledger, altered)
        persisted = control.db_get_operation(self.ledger, original["submission_id"])
        self.assertEqual(persisted["request_digest"], orchestration_engine._orchestrate_request_digest(original))
        self.assertEqual(persisted["status"], "failed")
        self.assertEqual(persisted["phase"], "gates_recorded")

    def test_inflight_continue_rejects_altered_future_reason_or_rework(self):
        self.activate()
        state = {"task_id": "immutable-inflight", "current_pipeline": ["documentation", "close"]}
        original = {
            "project_root": str(self.project), "task_ref": "task-immutable-inflight",
            "step": 1, "results": [{"attempt_result_ref": "attempt-result-01"}],
            "future_waves": [{"workers": [{"phase": "documentation"}]}],
            "reason": "Documented recovery.", "rework": True,
        }
        digest = control._orchestrate_request_digest({key: value for key, value in original.items() if key != "task_ref"})
        registry = control._operation_registry(self.ledger)
        registry["tasks"][state["task_id"]] = {"inflight_continue": {
            "digest": digest,
            "wave_id": "wave-01",
            "attempt_ids": ["attempt-01"],
            "old_params": {"submission_id": "immutable-inflight-continue"},
            "task_ref": original["task_ref"],
        }}
        control._write_operation_registry(self.ledger, registry)
        altered = {**original, "reason": "Different recovery reason."}
        with self.assertRaisesRegex(ValueError, "different continue payload"):
            control._v3_continue_context(altered, self.ledger, state, original["task_ref"])
        persisted = control._operation_registry(self.ledger)
        self.assertEqual(persisted["tasks"][state["task_id"]]["inflight_continue"]["digest"], digest)
















    def test_unhashable_governance_exit_code_returns_validation_error(self):
        """Governance evidence validation must also reject JSON arrays safely."""
        task_id = "governance-unhashable"
        evidence_id = "evidence-1"
        artifact_digest = "a" * 64
        artifact_ref = "artifact-" + hashlib.sha256(
            f"{task_id}\0evidence\0evidence/{evidence_id}.json\0{artifact_digest}".encode("utf-8")
        ).hexdigest()[:32]
        state = {
            "task_id": task_id,
            "governance": {
                "effective_mode": "light",
                "autonomous_scope_ref": "governance-scope-autonomous",
                "close_obligations": ["verification_evidence"],
            },
        }
        evidence = [{
            "evidence_id": evidence_id,
            "digest": "server-recorded-evidence-digest",
            "governance_scope_ref": "governance-scope-autonomous",
            "governance_obligations": ["verification_evidence"],
            "kind": "verification_evidence",
            "artifact_ref": artifact_ref,
            "artifact_digest": artifact_digest,
            "artifact_immutable": True,
            "artifact_verified": True,
            "verified_execution": True,
            "exit_code": [],
        }]
        with self.assertRaisesRegex(ValueError, "server-verified successful execution"):
            control.validate_governance_obligation_evidence(
                state, "governance_close", gate_evidence=evidence
            )



















    def test_v3_follow_up_rejects_an_active_source_task(self):
        source = self.v3_start("active source task", waves=[{"workers": [{"phase": "discover"}]}])
        rejected = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": source["task_ref"], "intent": "follow_up",
            "payload": {"user_request": "Correct an active task instead of reopening it."},
        })
        self.assertFalse(rejected["ok"])
        self.assertIn("requires a completed source task", rejected["diagnostics"][0]["message"])




    def test_planner_microtasks_allow_cross_package_dependencies_but_keep_one_global_dag(self):
        planning = self.v3_planning()
        planning["work_packages"].append({
            "id": "integration",
            "title": "Integration",
            "objective": "Verify the core delivery through the public boundary.",
            "depends_on": ["core"],
            "allowed_paths": ["tests"],
            "microtasks": [{
                "id": "integration_test",
                "title": "Exercise the integration",
                "objective": "Verify the core microtask from another package.",
                "profile": "qa_engineer",
                "allowed_paths": ["tests"],
                "depends_on": ["core_change"],
                "acceptance_criteria": ["The integration observes the core behavior."],
                "verification": ["Run the focused integration test."],
            }],
        })
        sanitized = control.sanitize_planning_payload(planning)
        self.assertEqual(
            sanitized["work_packages"][1]["microtasks"][0]["depends_on"],
            ["core_change"],
        )

        sanitized["work_packages"][0]["microtasks"][0]["depends_on"] = ["integration_test"]
        with self.assertRaisesRegex(ValueError, "planning microtask dependencies must be acyclic"):
            control.sanitize_planning_payload({
                "overview": sanitized["overview"],
                "work_packages": sanitized["work_packages"],
            }, persisted=True)

        planning["work_packages"][1]["microtasks"][0]["depends_on"] = ["missing_microtask"]
        with self.assertRaisesRegex(ValueError, "depends on unknown item"):
            control.sanitize_planning_payload(planning)

    def test_planning_package_prose_gate_projection_uses_microtask_gates(self):
        planning = self.v3_planning()
        planning["work_packages"][0]["gates"] = [
            "a_missing", "timed_out", "inaccessible", "ambiguous",
            "production_fact", "release_blocker", "autonomous_averaging",
            "no_production_mutation", "restart", "deployment", "order",
        ]
        sanitized = control.sanitize_planning_payload(planning)
        self.assertEqual(sanitized["work_packages"][0]["gates"], ["implementation"])

    def test_planning_release_safety_prose_gate_projection_preserves_microtask_gates(self):
        planning = self.v3_planning()
        package = planning["work_packages"][0]
        package["id"] = "release_and_safety"
        package["gates"] = ["enable", "release", "warmup"]
        package["microtasks"][0]["gates"] = ["implementation", "qa"]

        sanitized = control.sanitize_planning_payload(planning)
        self.assertEqual(
            sanitized["work_packages"][0]["gates"],
            ["implementation", "qa"],
        )

    def test_planning_package_unknown_gate_still_fails_closed(self):
        planning = self.v3_planning()
        planning["work_packages"][0]["gates"] = ["not_a_real_gate"]
        with self.assertRaisesRegex(ValueError, "references unknown gates"):
            control.sanitize_planning_payload(planning)

    def test_planner_recommendation_actions_are_concrete_and_reference_plan_items(self):
        planning = self.v3_planning()
        planning["recommendation"] = "revise"
        planning["recommendation_actions"] = [{
            "issue": "The plan leaves the regression risk unresolved.",
            "action": "Add a focused regression test before implementation closes.",
            "plan_refs": ["core_change"],
            "verification": "Run the focused regression test.",
        }]
        sanitized = control.sanitize_planning_payload(planning)
        self.assertEqual(sanitized["recommendation_actions"][0]["plan_refs"], ["core_change"])
        planning["recommendation_actions"][0]["plan_refs"] = ["missing_plan_item"]
        with self.assertRaisesRegex(ValueError, "recommendation_actions\[0\].*unknown plan items"):
            control.sanitize_planning_payload(planning)




    def test_scoping_domains_reject_duplicates_cycles_and_incomplete_criteria_but_preserve_large_sets(self):
        base = self.v3_scoping()
        self.assertEqual(control.sanitize_scoping_payload(base)["schema"], control.SCOPING_SCHEMA)

        duplicate = dict(base)
        duplicate["discovery_domains"] = [dict(base["discovery_domains"][0]), dict(base["discovery_domains"][0])]
        with self.assertRaisesRegex(ValueError, "unique"):
            control.sanitize_scoping_payload(duplicate)

        large_set = dict(base)
        large_set["discovery_domains"] = [
            {**dict(base["discovery_domains"][0]), "id": f"domain_{index}", "title": f"Domain {index}"}
            for index in range(24)
        ]
        self.assertEqual(len(control.sanitize_scoping_payload(large_set)["discovery_domains"]), 24)

        cycle = dict(base)
        first = {**dict(base["discovery_domains"][0]), "id": "first", "depends_on": ["second"]}
        second = {**dict(base["discovery_domains"][0]), "id": "second", "title": "Second", "depends_on": ["first"]}
        cycle["discovery_domains"] = [first, second]
        with self.assertRaisesRegex(ValueError, "acyclic"):
            control.sanitize_scoping_payload(cycle)

        incomplete = json.loads(json.dumps(base))
        incomplete["discovery_domains"][0]["verification"] = []
        with self.assertRaisesRegex(ValueError, "verification"):
            control.sanitize_scoping_payload(incomplete)

    def test_planning_and_scoping_preserve_oversized_unicode_without_lossy_normalization(self):
        """Planning/scoping payloads remain lossless; prompt volume is advisory."""
        oversized = "🙂" * (control.MAX_JSON_BYTES // len("🙂".encode("utf-8")) + 1)
        planning = self.v3_planning()
        planning["overview"] = oversized
        self.assertEqual(control.sanitize_planning_payload(planning)["overview"], oversized)
        scoping = self.v3_scoping()
        scoping["overview"] = oversized
        self.assertEqual(control.sanitize_scoping_payload(scoping)["overview"], oversized)




    def test_v3_artifact_management_pages_metadata_and_streams_large_markdown(self):
        started = self.v3_start("stream task artifacts", waves=[{"workers": [{"phase": "discover"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        content = "# Evidence\n\n" + ("source-backed behavior\n" * 5000)
        artifact = control.store_immutable_artifact(
            task_dir,
            state["task_id"],
            kind="canonical_markdown",
            title="artifacts/markdown/large.md",
            mime_type="text/markdown",
            content=content,
            export_path="artifacts/markdown/large.md",
        )
        page = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "artifacts",
            "payload": {"action": "list", "kind": "canonical_markdown", "page_size": 1},
        })
        self.assertTrue(page["ok"])
        self.assertEqual(page["artifacts"], [artifact])
        metadata = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "artifacts",
            "payload": {"action": "metadata", "artifact_ref": artifact["artifact_ref"]},
        })
        first = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "artifacts",
            "payload": {"action": "read", "artifact_ref": artifact["artifact_ref"], "cursor": metadata["read_cursor"], "max_bytes": 4096},
        })
        self.assertTrue(first["ok"])
        self.assertFalse(first["complete"])
        self.assertLessEqual(first["returned_bytes"], 4096)
        self.assertTrue(first["content_part"].startswith("# Evidence"))
        tiny = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "artifacts",
            "payload": {"action": "read", "artifact_ref": artifact["artifact_ref"], "cursor": metadata["read_cursor"], "max_bytes": 1},
        })
        self.assertTrue(tiny["ok"], tiny)
        self.assertEqual(tiny["requested_max_bytes"], 1)
        self.assertEqual(tiny["effective_max_bytes"], 1)
        self.assertFalse(tiny["max_bytes_normalized"])
        self.assertLessEqual(tiny["returned_bytes"], tiny["effective_max_bytes"])
        oversized = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "artifacts",
            "payload": {
                "action": "read", "artifact_ref": artifact["artifact_ref"],
                "cursor": metadata["read_cursor"], "max_bytes": control.MAX_BRIEFING_BYTES,
            },
        })
        self.assertTrue(oversized["ok"], oversized)
        self.assertFalse(oversized["max_bytes_normalized"])
        self.assertEqual(oversized["requested_max_bytes"], control.MAX_BRIEFING_BYTES)
        self.assertEqual(oversized["effective_max_bytes"], control.MAX_BRIEFING_BYTES)
        denied = control.manage_orchestration({
            "project_root": str(self.project), "task_ref": started["task_ref"], "intent": "artifacts",
            "payload": {"action": "read", "artifact_ref": artifact["artifact_ref"], "cursor": first["next_cursor"] + "x"},
        })
        self.assertFalse(denied["ok"])
        self.assertIn("cursor", denied["diagnostics"][0]["message"])





    def test_large_baseline_manifest_is_readable_during_handoff_and_reconciliation(self):
        started = self.v3_start("large baseline handoff", waves=[{"workers": [{"phase": "discover"}]}])
        task_dir = next((self.ledger / "tasks").iterdir())
        state = self.task_state(task_dir)
        baseline = control.load_manifest_snapshot(
            task_dir, state["initial_manifest_ref"], "test task baseline"
        )
        baseline["policy"]["test_padding"] = "x" * (64 * 1024 * 5)
        padded_ref = control.manifest_snapshot_ref(baseline)
        control.db_put_manifest_snapshot(self.ledger, padded_ref, baseline["digest"], baseline)
        state["initial_manifest_ref"] = padded_ref
        self.write_task_state(state)

        receipt, _ = control.reconcile_manifest(task_dir, state, [])
        self.assertEqual(receipt["baseline_digest"], baseline["digest"])

        with mock.patch.object(control, "handoff", return_value={"recorded": True}) as handoff:
            result = control._auto_handoff(
                {"project_root": str(self.project), "principal": "thread-a"},
                task_dir,
                state,
                "Close the Cortex task.",
            )
        self.assertTrue(result["recorded"])
        handoff.assert_called_once()

    def test_json_write_preserves_large_content_before_replacing_existing_file(self):
        path = self.base / "bounded.json"
        path.write_text("sentinel\n", encoding="utf-8")
        original_limit = control.MAX_JSON_BYTES
        try:
            control.MAX_JSON_BYTES = 128
            control.write_json(path, {"padding": "x" * 512})
        finally:
            control.MAX_JSON_BYTES = original_limit
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["padding"], "x" * 512)

    def test_oversized_baseline_is_materialized_before_task_directory_creation(self):
        self.activate()
        classified = control.classify_task({"complexity": "C1", "requirements": [], "principal": "thread-a"})
        baseline = control.capture_project_manifest(self.project)
        baseline["padding"] = "x" * 512
        original_limit = control.MAX_MANIFEST_BYTES
        try:
            control.MAX_MANIFEST_BYTES = 128
            with mock.patch.object(control, "capture_project_manifest", return_value=baseline):
                result = control.init_task({
                    "task_id": "oversized-baseline",
                    "user_request": "accept oversized baseline",
                    "complexity": "C1",
                    "classification_id": classified["classification_id"],
                    "principal": "thread-a",
                })
        finally:
            control.MAX_MANIFEST_BYTES = original_limit
        self.assertTrue(result["created"])
        self.assertEqual(control.db_load_task(self.ledger, "oversized-baseline")[1]["task_id"], "oversized-baseline")





    def test_harvest_coverage_matrix_requires_complete_structured_rows(self):
        self.write_canonical_harvest_project_docs()
        feature_docs = self.project / "docs/features"
        feature_docs.mkdir(parents=True)
        (feature_docs / "index.md").write_text(
            "# Features\n\n## Inventory totals\n\nTotal: 1.\n\n"
            "## Coverage matrix\n\n"
            "| Feature | Runtime owner | Entry points | Source evidence | Documentation | Verification | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| Trading | engine | command | service.py | no canonical link | test.py | complete-ish |\n\n"
            "## Unmapped surfaces\n\nNone.\n\n## Exclusions\n\nNone.\n\n"
            "## Known unknowns\n\nNone.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ValueError,
            "status must be covered, documented, verified, or excluded.*Documentation must link",
        ):
            control._validate_harvest_coverage_manifest(
                self.project,
                {"user_request": "harvest exhaustive repository knowledge"},
                "documentation",
            )

    def test_harvest_rejects_placeholder_unknowns_and_duplicate_features(self):
        self.write_canonical_harvest_project_docs()
        feature_docs = self.project / "docs/features"
        feature_docs.mkdir(parents=True)
        (feature_docs / "trading").mkdir(parents=True)
        (feature_docs / "trading/index.md").write_text(
            "# Trading\n\n## Runtime owner\n\nThe engine owns trading.\n\n"
            "## Behavior and workflow\n\nThe workflow handles orders.\n\n"
            "## State and data\n\nOrder state is persisted.\n\n"
            "## Interfaces\n\nThe command is an entry point.\n\n"
            "## Failure and recovery\n\nErrors recover safely.\n\n"
            "## Verification\n\nTests verify behavior.\n",
            encoding="utf-8",
        )
        (feature_docs / "index.md").write_text(
            "# Features\n\n## Inventory totals\n\nTotal: 1.\n\n## Coverage matrix\n\n"
            "| Feature | Runtime owner | Entry points | Source evidence | Documentation | Verification | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| Trading | engine | command | service.py | [Trading](trading/index.md) | test.py | documented |\n"
            "| Trading | engine | command | service.py | [Trading](trading/index.md) | test.py | documented |\n\n"
            "## Unmapped surfaces\n\nNone.\n\n## Exclusions\n\nNone.\n\n"
            "## Known unknowns\n\nNone.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "duplicates feature"):
            control._validate_harvest_coverage_manifest(
                self.project, {"user_request": "harvest exhaustive repository knowledge"}, "documentation"
            )

    def test_harvest_rejects_empty_known_unknowns_and_empty_feature_sections(self):
        self.write_canonical_harvest_project_docs()
        feature_docs = self.project / "docs/features"
        feature_docs.mkdir(parents=True)
        (feature_docs / "index.md").write_text(
            "# Features\n\n## Inventory totals\n\nTotal: 1.\n\n## Coverage matrix\n\n"
            "| Feature | Runtime owner | Entry points | Source evidence | Documentation | Verification | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| Trading | engine | command | service.py | [Trading](trading/index.md) | test.py | documented |\n\n"
            "## Unmapped surfaces\n\nNone.\n\n## Exclusions\n\nNone.\n\n## Known unknowns\n\n## Notes\n\nReviewed.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "Known unknowns section"):
            control._validate_harvest_coverage_manifest(
                self.project, {"user_request": "harvest exhaustive repository knowledge"}, "documentation"
            )



    def test_v3_context_files_reject_escape_missing_and_symlink_paths(self):
        docs = self.project / "docs"
        docs.mkdir()
        (docs / "relevant.md").write_text("# Relevant\n", encoding="utf-8")
        outside = self.base / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (docs / "linked.md").symlink_to(outside)
        for context_file in ("../outside.md", str(outside), "docs/missing.md", "docs/linked.md"):
            with self.subTest(context_file=context_file):
                rejected = self.v3_start(
                    f"reject unsafe context {context_file}",
                    waves=[{"workers": [{"phase": "discover", "context_files": [context_file]}]}],
                )
                self.assertFalse(rejected["ok"])
                self.assertEqual(rejected["code"], "start_validation_failed")
                self.assertIn("context file", rejected["diagnostics"][0]["message"].replace("_", " "))
        tasks = self.ledger / "tasks"
        self.assertTrue(not tasks.exists() or not any(tasks.iterdir()))










    def test_v3_automatic_qa_rework_is_unbounded_and_escalates_model_effort(self):
        current = self.v3_start("unbounded QA correction", waves=[{"workers": [{"phase": "qa"}]}])
        # Repeated equivalent failures correctly trigger the no-progress
        # circuit breaker.  These intentionally distinct observed failure
        # classes prove that genuine corrective progress still remains
        # unbounded and receives the expected escalation.
        failures = [
            "worker process stopped before it could run the QA checks",
            "network timeout prevented the QA dependency download",
            "missing dependency blocked the QA toolchain",
            "governance policy validation rejected the QA evidence",
            "product assertion failure remains in the QA result",
        ]
        for failure_number, failure_reason in enumerate(failures, start=1):
            result = {
                "status": "failed",
                "reason": failure_reason,
                "dispatch_ref": current["dispatches"][0]["dispatch_ref"],
            }
            current = control.continue_orchestration({
                "project_root": str(self.project),
                "task_ref": current["task_ref"],
                "step": current["step"],
                "results": [result],
            })
            self.assertTrue(current["ok"], current)
            self.assertEqual(current["outcome"], "ready_to_spawn")
            self.assertEqual(len(current["dispatches"]), 1)
            arguments = current["dispatches"][0]["arguments"]
            if failure_number == 1:
                # QA already starts above the first-failure floor; escalation
                # never lowers the profile's stronger base route.
                self.assertEqual(arguments["reasoning_effort"], "xhigh")
            elif failure_number == 2:
                self.assertEqual(arguments["model"], "gpt-5.6-terra")
                self.assertEqual(arguments["reasoning_effort"], "xhigh")
            else:
                self.assertEqual(arguments["model"], "gpt-5.6-terra")
                self.assertEqual(arguments["reasoning_effort"], "max")
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        self.assertEqual(state["orchestrate_gate_failure_counts"]["qa"], 5)
        self.assertNotEqual(state["status"], "blocked")
        self.assertNotIn("rework budget exhausted", str(state.get("blocked_reason") or ""))


    def test_v3_failed_result_is_bound_to_the_dispatched_attempt(self):
        started = self.v3_start("identical failed retries", waves=[{"workers": [{"phase": "discover"}]}])
        first_payload = {
            "project_root": str(self.project),
            "task_ref": started["task_ref"],
            "step": started["step"],
            "results": [{
                "status": "failed",
                "reason": "native_worker_stopped_without_result",
                "dispatch_ref": started["dispatches"][0]["dispatch_ref"],
            }],
        }
        first = control.continue_orchestration(first_payload)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["outcome"], "ready_to_spawn")
        first_replay = control.continue_orchestration(first_payload)
        self.assertTrue(first_replay["replayed"])
        self.assertEqual(first_replay["dispatches"], [])

        unchanged_strategy_payload = {
            **first_payload,
            "results": [{
                "status": "failed",
                "reason": "native_worker_stopped_without_result",
                "dispatch_ref": first["dispatches"][0]["dispatch_ref"],
            }],
        }
        unchanged_strategy = control.continue_orchestration(unchanged_strategy_payload)
        self.assertTrue(unchanged_strategy["ok"], unchanged_strategy)
        self.assertEqual(unchanged_strategy["outcome"], "ready_to_spawn")
        unchanged_replay = control.continue_orchestration(unchanged_strategy_payload)
        self.assertTrue(unchanged_replay["replayed"])
        self.assertEqual(unchanged_replay["dispatches"], [])

        second_payload = {
            **unchanged_strategy_payload,
            "results": [{
                "status": "failed",
                "reason": "native_worker_stopped_without_result",
                "dispatch_ref": unchanged_strategy["dispatches"][0]["dispatch_ref"],
                "next_strategy": "inspect an alternate repository evidence path",
            }],
        }
        second = control.continue_orchestration(second_payload)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["outcome"], "ready_to_spawn")
        self.assertEqual(len(second["dispatches"]), 1)
        retry_assignment = json.loads(
            self.briefing_from_response(second).split("```json\n", 1)[1].split("\n```", 1)[0]
        )
        self.assertEqual(retry_assignment["strategy"], "inspect an alternate repository evidence path")
        replay = control.continue_orchestration(second_payload)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["dispatches"], [])



























    def test_mcp_resource_discovery_returns_an_empty_catalogue(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        requests = "\n".join((
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "resources/templates/list", "params": {}}),
        )) + "\n"
        result = subprocess.run(
            [sys.executable, str(script)], input=requests, text=True,
            capture_output=True, check=True,
        )
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["capabilities"]["resources"], {"subscribe": False, "listChanged": False})
        instructions = responses[0]["result"]["instructions"]
        self.assertLessEqual(len(instructions), 512)
        self.assertIn("attempt_result_ref", instructions)
        self.assertIn("Internal workers emit English only", instructions)
        self.assertIn("After resume, clear, or compaction", instructions)
        self.assertEqual(responses[1]["result"], {"resources": []})
        self.assertEqual(responses[2]["result"], {"resourceTemplates": []})




    def test_blocked_task_can_resume(self):
        result = self.init()
        state = result["state"]
        blocked = control.record_gate({"task_id": "demo", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "outcome": "blocked", "summary": "dependency unavailable"})
        self.assertEqual(blocked["state"]["status"], "active")

    def test_rework_resets_completed_gate(self):
        result = self.init()
        state = result["state"]
        evidence = control.record_evidence({"task_id": "demo", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "summary": "done"})
        closed = control.record_gate({"task_id": "demo", "principal": "thread-a", "expected_revision": evidence["state"]["revision"], "gate": "discover", "outcome": "passed"})
        changed = control.update_pipeline({"task_id": "demo", "principal": "thread-a", "expected_revision": closed["state"]["revision"], "operations": [{"op": "rework", "gate": "discover"}], "allow_rework": True, "reason": "rework"})
        self.assertIn("discover", changed["state"]["current_pipeline"])
        self.assertNotIn("discover", changed["state"]["completed_gates"])

















    def test_journal_symlinks_cannot_modify_task_or_lane_state(self):
        state = self.init(task_id="journal", complexity="C1")["state"]
        state = control.record_gate({"task_id": "journal", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "outcome": "blocked"})["state"]
        task_dir = self.ledger / "tasks/0001-journal"
        sentinel = self.base / "sentinel"
        sentinel.write_text("unchanged", encoding="utf-8")
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "journal.md").symlink_to(sentinel)
        self.assertEqual(state["status"], "active")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
        lane = control.create_lane({"lane_id": "journal-lane", "principal": "thread-a"})
        lane_dir = self.ledger / "lanes/journal-lane"
        lane_dir.mkdir(parents=True, exist_ok=True)
        (lane_dir / "journal.md").symlink_to(sentinel)
        claimed = control.claim_lane({"lane_id": "journal-lane", "principal": "thread-a", "expires_at": "2999-01-01T00:00:00+00:00"})
        self.assertIsNotNone(claimed["state"]["lease"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

    def test_bearer_and_uri_redaction(self):
        text = control.redact("Authorization: Bearer abc123 trailing-canary\n continuation-canary\nSafe: visible https://user:pass@example.test API_KEY='quoted-secret'")
        self.assertNotIn("abc123", text)
        self.assertNotIn("trailing-canary", text)
        self.assertNotIn("continuation-canary", text)
        self.assertNotIn("user:pass", text)
        self.assertNotIn("quoted-secret", text)
        self.assertIn("Safe: visible", text)

    def test_completion_unbinds_task_but_keeps_activation_until_normal(self):
        self.activate(principal="thread-finish")
        classified = control.classify_task({"complexity": "C1", "requirements": [], "thread_id": "thread-finish", "principal": "thread-finish"})
        result = control.init_task({"task_id": "finish", "user_request": "finish", "complexity": "C1", "classification_id": classified["classification_id"], "thread_id": "thread-finish", "principal": "thread-finish"})
        state = result["state"]
        for gate in ["discover", "implementation", "review", "close"]:
            evidence = control.record_evidence({"task_id": "finish", "principal": "thread-finish", "expected_revision": state["revision"], "gate": gate, "summary": gate})
            state = control.record_gate({"task_id": "finish", "principal": "thread-finish", "expected_revision": evidence["state"]["revision"], "gate": gate, "outcome": "passed"})["state"]
        self.assertFalse((self.ledger / "active-tasks.json").exists())
        self.assertEqual(state["status"], "completed")
        activation = control.activation_status({"thread_id": "thread-finish", "principal": "thread-finish"})
        self.assertTrue(activation["active"])
        self.assertIsNone(activation["activation"]["task_id"])
        self.assertNotIn("initialized_at", activation["activation"])

        next_classification = control.classify_task({"complexity": "C1", "requirements": [], "thread_id": "thread-finish", "principal": "thread-finish"})
        next_task = control.init_task({"task_id": "next", "user_request": "next", "complexity": "C1", "classification_id": next_classification["classification_id"], "thread_id": "thread-finish", "principal": "thread-finish"})
        self.assertTrue(next_task["created"])
        self.assertFalse((self.ledger / "active-tasks.json").exists())

        control.deactivate_orchestration({"user_command": "/normal", "thread_id": "thread-finish", "principal": "thread-finish"})
        self.assertFalse(control.activation_status({"thread_id": "thread-finish", "principal": "thread-finish"})["active"])

    def test_resource_claim_expiry(self):
        result = self.init(task_id="resources", complexity="C1")
        state = result["state"]
        claim = control.claim_resource({"task_id": "resources", "principal": "thread-a", "expected_revision": state["revision"], "path": "port:4000", "owner": "worker", "expires_at": "2999-01-01T00:00:00+00:00"})
        self.assertFalse(claim["state"]["locks"][control.lock_key("port:4000")]["advisory"])
        self.assertEqual(control.status({"task_id": "resources", "principal": "thread-a"})["task"]["task_id"], "resources")

    def test_completion_bypasses_are_rejected(self):
        result = self.init(task_id="skip", complexity="C2")
        state = result["state"]
        skipped = control.record_gate({"task_id": "skip", "principal": "thread-a", "expected_revision": state["revision"], "gate": "discover", "outcome": "skipped"})
        self.assertIn("discover", skipped["state"]["skipped_gates"])
        self.assertEqual(skipped["advisories"][0]["reason"], "skip_reason_missing")
        mismatch = control.record_gate({"task_id": "skip", "principal": "thread-a", "expected_revision": skipped["state"]["revision"], "gate": "qa", "outcome": "skipped"})
        self.assertEqual(mismatch["reason"], "gate_mismatch")
        self.assertFalse(mismatch["state_changed"])
        repaired = control.update_pipeline({"task_id": "skip", "principal": "thread-a", "expected_revision": skipped["state"]["revision"], "pipeline": ["plan", "discover"]})
        self.assertEqual(repaired["state"]["current_pipeline"], ["plan", "discover"])

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
        definition, state = control.db_get_lane(self.ledger, "recover-lane")
        state["lease"] = {"owner": "thread-a", "run_id": "old", "expires_at": "2000-01-01T00:00:00+00:00"}
        control.db_put_lane(self.ledger, definition, state)
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
            control.init_task({"task_id": "../escape", "user_request": "bad", "complexity": "C1"})
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


    def test_mcp_logs_invalid_tool_input_with_session_and_call_ids(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        with tempfile.TemporaryDirectory() as home:
            environment = os.environ.copy()
            environment["HOME"] = home
            environment.pop("CORTEX_ROOT", None)
            request = {
                "jsonrpc": "2.0",
                "id": "call-17",
                "method": "tools/call",
                "params": {
                    "name": "get_task_status",
                    "arguments": {
                        "task_id": "missing-log-task",
                        "attempt_id": "attempt-9",
                        "thread_id": "chat-session-42",
                        "principal": "owner",
                        "api_key": "do-not-persist",
                        "project_root": str(self.project),
                    },
                },
            }
            completed = subprocess.run(
                [sys.executable, str(script), "--mcp-audience=coordinator"],
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                env=environment,
                check=True,
            )
            response = json.loads(completed.stdout)
            self.assertIn("error", response)
            log_path = Path(home) / ".codex" / "logs" / "cortex-tool-errors.jsonl"
            self.assertTrue(log_path.is_file())
            record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(record["event"], "tool_error")
            self.assertEqual(record["tool"], "get_task_status")
            self.assertEqual(record["chat_session_id"], "chat-session-42")
            self.assertEqual(record["request_id"], "call-17")
            self.assertEqual(record["ids"]["task_id"], "missing-log-task")
            self.assertEqual(record["ids"]["attempt_id"], "attempt-9")
            self.assertNotIn("input", record)
            self.assertEqual(record["input_summary"]["source"], "arguments")
            self.assertEqual(record["input_summary"]["field_count"], len(request["params"]["arguments"]))
            self.assertEqual(record["input_summary"]["sensitive_field_count"], 1)
            self.assertIn("<sensitive-field>", record["input_summary"]["fields"])
            self.assertNotIn("do-not-persist", log_path.read_text(encoding="utf-8"))
            self.assertEqual(log_path.stat().st_mode & 0o777, 0o600)

    def test_tool_error_log_keeps_complete_newest_lines_within_ten_megabyte_cap(self):
        self.assertEqual(control.MAX_TOOL_ERROR_LOG_BYTES, 10 * 1024 * 1024)
        log_path = self.base / "private-logs" / "cortex-tool-errors.jsonl"
        with (
            mock.patch.object(control, "_tool_error_log_path", return_value=log_path),
            mock.patch.object(control, "MAX_TOOL_ERROR_LOG_BYTES", 4096),
        ):
            for index in range(40):
                control.log_tool_error(
                    {
                        "method": "tools/call",
                        "params": {
                            "name": "bounded-log-test",
                            "arguments": {"task_id": f"task-{index}", "payload": "x" * 240},
                        },
                    },
                    f"call-{index}",
                    "",
                    RuntimeError(f"bounded failure {index}"),
                )
        content = log_path.read_bytes()
        self.assertLessEqual(len(content), 4096)
        self.assertTrue(content.endswith(b"\n"))
        records = [json.loads(line) for line in content.decode("utf-8").splitlines()]
        self.assertGreater(len(records), 1)
        self.assertEqual(records[-1]["request_id"], "call-39")
        self.assertNotEqual(records[0]["request_id"], "call-0")
        self.assertTrue(all(record["event"] == "tool_error" for record in records))




    def test_mcp_rejects_root_fallbacks_and_starts_in_the_canonical_ledger(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"

        def call(arguments, environment=None):
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "start_orchestration", "arguments": arguments}}
            completed = subprocess.run([sys.executable, str(script), "--mcp-audience=coordinator"], input=json.dumps(request) + "\n", text=True, capture_output=True, env=environment, check=True)
            return json.loads(completed.stdout)

        common = {"task": {
            "user_request": "root test", "complexity": "C1",
            "acceptance_criteria": ["The requested outcome is observed."],
            "verification": ["Run an authoritative outcome check."],
        }}
        missing_root = call(common)["result"]["structuredContent"]
        self.assertFalse(missing_root["ok"])
        self.assertIn("project_root is required", missing_root["diagnostics"][0]["message"])
        relative_root = call({**common, "project_root": "relative"})["result"]["structuredContent"]
        self.assertFalse(relative_root["ok"])
        self.assertIn("absolute path", relative_root["diagnostics"][0]["message"])
        external = self.base / "external-ledger"
        environment = os.environ.copy()
        environment["CORTEX_ROOT"] = str(external)
        rejected = call({**common, "project_root": str(self.project)}, environment)
        rejected_result = rejected["result"]["structuredContent"]
        self.assertFalse(rejected_result["ok"])
        self.assertIn("CORTEX_ROOT is not supported", rejected_result["diagnostics"][0]["message"])
        self.assertFalse(external.exists())
        accepted = call({**common, "project_root": str(self.project)})["result"]["structuredContent"]
        self.assertTrue(accepted["ok"])
        self.assertNotIn("task_id", accepted)
        self.assertTrue((self.ledger / "tasks").is_dir())

    def test_mcp_process_supports_multiple_project_roots(self):
        script = Path(__file__).parents[1] / "plugins/cortex/scripts/cortex.py"
        other = self.base / "other-project"
        other.mkdir()
        def start(root, task_id, submission_id):
            return {"jsonrpc": "2.0", "id": submission_id, "method": "tools/call", "params": {"name": "start_orchestration", "arguments": {"project_root": str(root), "task": {"user_request": task_id, "complexity": "C1", "acceptance_criteria": ["The requested outcome is observed."], "verification": ["Run an authoritative outcome check."]}}}}
        requests = [start(self.project, "first-root", "first-root-start"), start(other, "second-root", "second-root-start")]
        completed = subprocess.run([sys.executable, str(script), "--mcp-audience=coordinator"], input="".join(json.dumps(item) + "\n" for item in requests), text=True, capture_output=True, check=True)
        first, second = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertTrue(first["result"]["structuredContent"]["ok"])
        self.assertTrue(second["result"]["structuredContent"]["ok"])
        self.assertTrue(control.ledger_root_path({"project_root": str(self.project)}).is_dir())
        self.assertTrue(control.ledger_root_path({"project_root": str(other)}).is_dir())
        self.assertFalse((self.project / ".codex/cortex/cortex.db").exists())
        self.assertFalse((other / ".codex/cortex/cortex.db").exists())

    def test_mcp_profile_cache_survives_source_directory_rename(self):
        source = Path(__file__).parents[1] / "plugins/cortex"
        cached = self.base / "cached-cortex"
        renamed = self.base / "retired-cache-entry"
        shutil.copytree(source, cached, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        script = cached / "scripts/cortex.py"
        proc = subprocess.Popen(
            [sys.executable, str(script), "--mcp-audience=coordinator"], cwd=self.project,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            def call(payload):
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                if not line:
                    self.fail(proc.stderr.read())
                return json.loads(line)

            initialized = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(initialized["result"]["serverInfo"]["version"].split("+", 1)[0], "10.0.7")
            cached.rename(renamed)
            request = {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "start_orchestration", "arguments": {
                    "project_root": str(self.project),
                    "task": {
                        "user_request": "use in-memory profiles", "complexity": "C1",
                        "acceptance_criteria": ["The requested outcome is observed."],
                        "verification": ["Run an authoritative outcome check."],
                    },
                    "waves": [{"workers": [{"phase": "discover", "profile": "explorer"}]}],
                }},
            }
            started = call(request)["result"]["structuredContent"]
            self.assertTrue(started["ok"])
            briefing = self.briefing_from_response(started)
            self.assertIn("## Role contract", briefing)
            self.assertIn("Read supplied knowledge pages first", briefing)
            self.assertNotIn("Select this profile", briefing)
        finally:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
