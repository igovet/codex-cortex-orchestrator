"""Focused v11 install-gate contracts.

These tests intentionally avoid importing the full ``cortex`` facade so the
public schema and pure repair contracts remain testable while the bundled
profile/runtime registry is being migrated.  They cover the model-facing
boundary and the side-effect-free parts of the worker contract.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime import delegation
from cortex_runtime import mcp_api
from cortex_runtime import v11_submission as v11


def _schemas() -> dict[str, dict[str, object]]:
    return mcp_api.build_public_schemas(
        agents={"backend_dev": {"description": "Backend", "sandbox": "workspace-write"}},
        max_work_packages=32,
        max_microtasks_per_package=32,
        max_discovery_domains=8,
        question_option_schema={"type": "string"},
    )


def _plan() -> dict[str, object]:
    return {
        "overview": "Implement the bounded change.",
        "work_packages": [{
            "id": "core",
            "title": "Core",
            "objective": "Make the change.",
            "allowed_paths": ["plugins/cortex"],
            "microtasks": [{
                "id": "change",
                "title": "Change",
                "objective": "Implement it.",
                "profile": "backend_dev",
                "allowed_paths": ["plugins/cortex"],
                "acceptance_criteria": ["The contract passes."],
                "verification": ["Run focused tests."],
            }],
        }],
    }


def _outcome() -> dict[str, object]:
    return {
        "status": "completed",
        "summary": "Implemented and verified the bounded change.",
        "findings": [{"summary": "Verified."}],
    }


class V11InstallSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = _schemas()

    def test_all_five_worker_handlers_require_explicit_task_and_assignment_refs(self) -> None:
        worker_handlers = (
            "worker_question",
            "record_attempt_event",
            "complete_attempt",
            "read_dispatch_briefing",
        )
        for name in worker_handlers:
            with self.subTest(operation=name):
                schema = self.schemas[name]
                self.assertIn("task_ref", schema["required"])
                self.assertIn("assignment_ref", schema["required"])
                self.assertEqual(schema["properties"]["task_ref"]["pattern"], v11.TASK_REF_PATTERN)
                self.assertEqual(schema["properties"]["assignment_ref"]["pattern"], v11.ASSIGNMENT_REF_PATTERN)

        result_schema = self.schemas["read_worker_result"]
        self.assertEqual(result_schema["required"], ["task_ref"])
        self.assertEqual(
            result_schema["oneOf"],
            [
                {
                    "required": ["coordinator_ref", "step"],
                    "not": {"anyOf": [{"required": ["assignment_ref"]}, {"required": ["attempt_result_ref"]}]},
                },
                {
                    "required": ["assignment_ref", "attempt_result_ref"],
                    "not": {"anyOf": [{"required": ["coordinator_ref"]}, {"required": ["step"]}]},
                },
            ],
        )

    def test_complete_attempt_advertises_exact_plan_outcome_and_locked_repair_branches(self) -> None:
        schema = self.schemas["complete_attempt"]
        self.assertEqual(
            [branch["title"] for branch in schema["oneOf"]],
            [
                "Planner assignment: plan branch",
                "Non-planner assignment: outcome branch",
                "Pending repair: patch-only branch",
            ],
        )
        plan = schema["properties"]["plan"]["properties"]
        self.assertEqual(plan["recommendation"]["enum"], ["approve", "revise"])
        self.assertEqual(plan["recommendation_actions"]["items"]["type"], "object")
        self.assertEqual(plan["risks"]["items"]["type"], "string")
        self.assertEqual(plan["resolved_questions"]["items"]["type"], "string")
        self.assertEqual(schema["properties"]["patches"]["minItems"], 1)
        self.assertIn("pending repair unchanged", schema["properties"]["patches"]["description"])

    def test_coordinator_handlers_cannot_downgrade_to_task_only(self) -> None:
        for name in ("continue_orchestration", "manage_orchestration", "manage_governance"):
            with self.subTest(operation=name):
                schema = self.schemas[name]
                branches = schema.get("oneOf", [schema])
                self.assertTrue(branches)
                for branch in branches:
                    required = set(branch["required"])
                    self.assertIn("task_ref", required)
                    self.assertIn("coordinator_ref", required)

        result_schema = self.schemas["read_worker_result"]
        self.assertEqual(result_schema["oneOf"][0]["required"], ["coordinator_ref", "step"])
        self.assertEqual(result_schema["oneOf"][1]["required"], ["assignment_ref", "attempt_result_ref"])
        self.assertNotIn("coordinator_ref", self.schemas["start_orchestration"].get("required", []))

    def test_public_audiences_exclude_create_thread_and_use_only_native_worker_names(self) -> None:
        fake_tools = {
            name: (lambda _params: {"ok": True}, schema)
            for name, schema in self.schemas.items()
        }
        fake_tools["create_thread"] = (lambda _params: {"ok": True}, {"type": "object"})
        for audience, expected in {
            "default": set(mcp_api.DEFAULT_PUBLIC_TOOL_NAMES),
            "coordinator": set(mcp_api.COORDINATOR_PUBLIC_TOOL_NAMES),
            "worker": set(mcp_api.WORKER_PUBLIC_TOOL_NAMES),
        }.items():
            with self.subTest(audience=audience):
                projected = mcp_api.public_tools_for_audience(fake_tools, audience)
                self.assertNotIn("create_thread", projected)
                self.assertEqual(set(projected), expected & set(fake_tools))

    def test_continue_accepts_only_canonical_attempt_result_refs(self) -> None:
        schema = self.schemas["continue_orchestration"]
        self.assertNotIn("completions", schema["properties"])
        self.assertNotIn("gate_outcomes", schema["properties"])
        self.assertNotIn("future_waves", schema["properties"])
        self.assertNotIn("host_agent_id", schema["properties"])
        result = schema["properties"]["results"]["items"]
        self.assertEqual(result["required"], ["attempt_result_ref"])
        self.assertNotIn("status", result["properties"])
        self.assertNotIn("reason", result["properties"])
        self.assertNotIn("dispatch_ref", result["properties"])

    def test_installable_runtime_contains_no_removed_protocol_artifacts(self) -> None:
        plugin_root = SCRIPTS.parent
        retired_operation_names = (
            "".join(("resume", "_task")),
            "".join(("record_gate", "_outcome")),
            "".join(("cortex/tool-", "availability/v1")),
            "".join(("v11", "r1.")),
            "".join(("seal_rejected", "_draft_capsule")),
            "".join(("open_rejected", "_draft_capsule")),
            "".join(("apply_repair", "_capsule")),
            "".join(("allowed_wave", "_keys")),
            "".join(("allowed_delegation", "_keys")),
            "".join(("allowed", "_probe")),
            "".join(("allowed", "_task")),
            "".join(("_V11_PUBLIC", "_WORKER_FIELDS")),
            "".join(("replan", "_limit")),
        )
        retired_assignment_term = re.compile("worker" + r"[ _-]?" + "identity", re.IGNORECASE)
        allowed_suffixes = {".py", ".json", ".toml", ".md"}
        violations: list[str] = []
        for path in sorted(plugin_root.rglob("*")):
            if not path.is_file() or path.suffix not in allowed_suffixes:
                continue
            text = path.read_text(encoding="utf-8")
            if any(name in text for name in retired_operation_names) or retired_assignment_term.search(text):
                violations.append(str(path.relative_to(plugin_root)))
        self.assertEqual(violations, [])
        self.assertTrue(set(retired_operation_names[:2]).isdisjoint(mcp_api.SERVER_ONLY_TOOL_NAMES))
        intents = {
            branch["properties"]["intent"]["const"]
            for branch in self.schemas["manage_orchestration"]["oneOf"]
        }
        self.assertIn("resume", intents)
        self.assertIn("recover_blocked", intents)
        self.assertIn("finalize_worker_failure", intents)
        worker_schema = (
            self.schemas["start_orchestration"]["properties"]["waves"]["items"]
            ["properties"]["workers"]["items"]["properties"]
        )
        self.assertNotIn("strategy", worker_schema)
        submission_source = (SCRIPTS / "cortex_runtime" / "v11_submission.py").read_text(encoding="utf-8")
        self.assertNotIn("base" + "64", submission_source)


class V11BearerSerializationTests(unittest.TestCase):
    def test_bearers_are_retained_only_at_the_exact_allowed_response_paths(self) -> None:
        assignment_ref = "assignment-v1-" + "a" * 64
        coordinator_ref = "b" * 64
        response = {
            "coordinator_ref": coordinator_ref,
            "assignment_ref": assignment_ref,
            "error": f"failed assignment={assignment_ref} coordinator={coordinator_ref}",
            "nested": {
                "coordinator_ref": coordinator_ref,
                "assignment_ref": assignment_ref,
                "result": {"assignment_ref": assignment_ref},
            },
            "dispatches": [{
                "call": "spawn_agent",
                "dispatch_ref": "dispatch-" + "a" * 24,
                "arguments": {
                    "task_name": "backend_dev_change_01_ab12cd34",
                    "fork_turns": "none",
                    "message": f"assignment_ref={assignment_ref}",
                },
                "bootstrap_repair_message": f"assignment_ref={assignment_ref}",
            }],
        }
        projected = mcp_api._scrub_public_response(
            response,
            allow_coordinator_ref=True,
            supplied_coordinator_refs=frozenset({coordinator_ref}),
        )
        self.assertEqual(projected["coordinator_ref"], coordinator_ref)
        self.assertEqual(projected["dispatches"][0]["arguments"]["message"], response["dispatches"][0]["arguments"]["message"])
        self.assertNotIn("assignment_ref", projected)
        self.assertNotIn("coordinator_ref", projected["nested"])
        self.assertNotIn("assignment_ref", projected["nested"])
        serialized = json.dumps(projected, ensure_ascii=False)
        self.assertNotIn(assignment_ref, serialized.replace(response["dispatches"][0]["arguments"]["message"], ""))
        self.assertNotIn(coordinator_ref, json.dumps({key: value for key, value in projected.items() if key != "coordinator_ref"}))

    def test_opaque_repair_capsule_never_serializes_raw_assignment_or_coordinator_bearers(self) -> None:
        assignment_ref = "assignment-v1-" + "c" * 64
        coordinator_ref = "d" * 64
        token = v11.sign_repair_handle(
            "A" * 22, "b" * 64, b"v11-install-gate-signing-key-0123456789",
        )
        self.assertEqual(len(token), v11.REPAIR_HANDLE_LENGTH)
        self.assertNotIn(assignment_ref, token)
        self.assertNotIn(coordinator_ref, token)


class V11RepairGateTests(unittest.TestCase):
    _key = b"v11-install-gate-signing-key-0123456789"
    _escrow_digest = "c" * 64

    @staticmethod
    def _submission(**fields: object) -> dict[str, object]:
        return {
            "task_ref": "task-000000000001",
            "assignment_ref": "assignment-v1-" + "b" * 64,
            **fields,
        }

    def _repair(self, escrow: dict[str, object], patches: list[dict[str, object]], **overrides: object) -> dict[str, object]:
        payload = {
            "repair_capsule": v11.sign_repair_handle("B" * 22, self._escrow_digest, self._key),
            "base_payload_digest": escrow["base_payload_digest"],
            "patches": patches,
        }
        payload.update(overrides)
        return self._submission(
            **payload,
        )

    def test_missing_or_malformed_worker_refs_fail_before_semantic_mutation(self) -> None:
        valid = self._submission(outcome=_outcome())
        for label, candidate in {
            "missing_task_ref": {key: value for key, value in valid.items() if key != "task_ref"},
            "missing_assignment_ref": {key: value for key, value in valid.items() if key != "assignment_ref"},
            "wrong_task_ref": {**valid, "task_ref": "other task"},
            "wrong_assignment_ref": {**valid, "assignment_ref": "other assignment"},
        }.items():
            with self.subTest(case=label):
                before = copy.deepcopy(candidate)
                with self.assertRaises(v11.ValidationFailure):
                    v11.validate_submission(candidate)
                self.assertEqual(candidate, before)

    def test_invalid_plan_aggregates_errors_then_repairs_only_diagnostic_paths(self) -> None:
        rejected = self._submission(plan={"overview": "", "unexpected": True, "work_packages": []})
        before = copy.deepcopy(rejected)
        with self.assertRaises(v11.ValidationFailure) as raised:
            v11.validate_submission(rejected)
        escrow = v11.create_rejected_draft_escrow(rejected, raised.exception.diagnostics)
        repaired = v11.apply_repair_escrow(escrow, self._repair(escrow, [
            {"op": "replace", "path": "/overview", "value": "A complete plan."},
            {"op": "remove", "path": "/unexpected"},
            {"op": "replace", "path": "/work_packages", "value": _plan()["work_packages"]},
        ]))
        self.assertEqual(repaired["kind"], "plan")
        self.assertEqual(repaired["plan"]["overview"], "A complete plan.")
        self.assertEqual(rejected, before)

    def test_invalid_outcome_aggregates_errors_then_repairs_without_losing_valid_fields(self) -> None:
        rejected = self._submission(outcome={"status": "completed", "summary": "", "findings": [{"summary": "keep"}], "unexpected": True})
        before = copy.deepcopy(rejected)
        with self.assertRaises(v11.ValidationFailure) as raised:
            v11.validate_submission(rejected)
        escrow = v11.create_rejected_draft_escrow(rejected, raised.exception.diagnostics)
        repaired = v11.apply_repair_escrow(escrow, self._repair(escrow, [
            {"op": "replace", "path": "/summary", "value": "A complete outcome."},
            {"op": "remove", "path": "/unexpected"},
        ]))
        self.assertEqual(repaired["outcome"]["status"], "completed")
        self.assertEqual(repaired["outcome"]["summary"], "A complete outcome.")
        self.assertEqual(repaired["outcome"]["findings"], [{"summary": "keep"}])
        self.assertEqual(rejected, before)

    def test_wrong_cross_task_stale_and_out_of_scope_repairs_fail_without_mutation(self) -> None:
        rejected = self._submission(outcome={"status": "completed", "summary": ""})
        with self.assertRaises(v11.ValidationFailure) as raised:
            v11.validate_submission(rejected)
        escrow = v11.create_rejected_draft_escrow(rejected, raised.exception.diagnostics)
        snapshot = copy.deepcopy(escrow)
        valid_patch = [{"op": "replace", "path": "/summary", "value": "fixed"}]

        wrong_task = self._repair(escrow, valid_patch, task_ref="task-000000000002")
        with self.assertRaisesRegex(ValueError, "identity"):
            v11.apply_repair_escrow(escrow, wrong_task)

        stale = self._repair(escrow, valid_patch, base_payload_digest="sha256:" + "0" * 64)
        with self.assertRaisesRegex(ValueError, "digest"):
            v11.apply_repair_escrow(escrow, stale)

        out_of_scope = self._repair(escrow, [{"op": "replace", "path": "/status", "value": "failed"}])
        with self.assertRaisesRegex(ValueError, "outside"):
            v11.apply_repair_escrow(escrow, out_of_scope)
        self.assertEqual(escrow, snapshot)


class V11SpawnRehydrateTests(unittest.TestCase):
    def test_rehydrate_preserves_exact_ids_and_requires_spawn_agent(self) -> None:
        assignment_ref = "assignment-v1-" + "e" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir = root / "task"
            task_dir.mkdir()
            task_definition = {
                "task_id": "task-000000000001",
                "project_root": str(root / "project"),
                "user_intent_artifact_path": "intent/user-request.txt",
                "user_request_digest": "sha256:" + "1" * 64,
            }
            attempt = {
                "task_id": "task-000000000001",
                "attempt_id": "attempt-1",
                "dispatch_ref": "dispatch-1",
                "profile": "backend_dev",
                "briefing_file": "briefings/attempt-1.json",
                "briefing_digest": "sha256:" + "2" * 64,
                "plan_unit_file": "plans/attempt-1.json",
                "plan_unit_digest": "sha256:" + "3" * 64,
                "worker_assignment": {"task_ref": "task-000000000001", "assignment_ref": assignment_ref},
                "spawn_request": {"host_tool": "spawn_agent", "task_name": "backend_dev_change_01_ab12cd34"},
            }

            # delegation_service is composition-root bound at import time.  Run
            # this functional probe in a child interpreter so its test doubles
            # cannot leak into the parent process or mask import-order bugs.
            names = (
                "AGENTS", "AWAITING_HOST_SPAWN", "DOCUMENTATION_EVIDENCE_KINDS", "PROFILES",
                "QUESTION_SCHEMA", "REWORK_EFFORT_BY_PRIOR_FAILURES", "REWORK_TERRA_AFTER_FAILURES",
                "SCHEMA", "_contained_path", "_is_knowledge_harvest_task", "_project_knowledge_context",
                "_resolved_user_decisions", "_v11_task_ref", "_write_delegation_package", "active_gates",
                "authorize", "canonical_profile", "capture_project_manifest", "digest_text",
                "db_put_worker_session", "host_spawn_bootstrap", "host_spawn_prompt", "issue_worker_assignment",
                "host_bootstrap_repair_message",
                "ledger_root", "load_state", "load_task_definition", "native_worker_task_name", "now",
                "primary_gate", "profiles_for_gate", "redact", "render_gate_briefing", "resolve_dispatch_route",
                "safe_id", "sanitize_structured", "save_state", "select_implementation_profile",
                "select_project_root", "state_lock", "store_immutable_artifact", "store_manifest_snapshot",
                "worker_display_name", "worker_module_label", "worker_assignment_ref",
            )
            bad = copy.deepcopy(attempt)
            bad["spawn_request"]["host_tool"] = "create_thread"
            script = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPTS)!r})
from cortex_runtime.core.runtime_bindings import bind_runtime_dependencies
names = {names!r}
bindings = {{name: (lambda *args, **kwargs: None) for name in names}}
bindings.update({{
    "_contained_path": lambda _root, path, _label: path,
    "worker_assignment_ref": lambda _project, claim, create_key=False: claim["assignment_ref"],
    "host_spawn_bootstrap": lambda *args, **kwargs: "compact native bootstrap",
    "host_bootstrap_repair_message": lambda **kwargs: "exact bootstrap repair",
}})
bind_runtime_dependencies(bindings)
from cortex_runtime import delegation_service
task_dir = Path({str(task_dir)!r})
task_definition = {task_definition!r}
attempt = {attempt!r}
restored = delegation_service.rehydrate_dispatch_spawn_request(task_dir, task_definition, attempt)
assert restored["host_tool"] == "spawn_agent"
assert restored["task_name"] == "backend_dev_change_01_ab12cd34"
assert restored["dispatch_ref"] == "dispatch-1"
assert restored["message"] == "compact native bootstrap"
assert restored["bootstrap_repair_message"] == "exact bootstrap repair"
assert restored["briefing_file"] == "briefings/attempt-1.json"
assert restored["briefing_digest"] == "sha256:{'2' * 64}"
bad = {bad!r}
try:
    delegation_service.rehydrate_dispatch_spawn_request(task_dir, task_definition, bad)
except ValueError as error:
    assert str(error) == "native_spawn_agent_transport_required"
else:
    raise AssertionError("create_thread transport was accepted")
"""
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_native_hidden_spawn_shape_has_no_thread_environment_or_create_thread(self) -> None:
        request = delegation.spawn_request(
            dispatch_mode="hidden_subagent",
            gate="implementation",
            agent="backend_dev",
            display_name="Backend worker",
            task_name="backend_dev_change_01_ab12cd34",
            profiles={"backend_dev": {"description": "Backend", "sandbox": "workspace-write", "route_category": "implementation"}},
            selection_reason="bounded test",
            route={"selected_model": "gpt-5.6-luna", "selected_reasoning_effort": "medium"},
            thread_environment=None,
        )
        self.assertEqual(request["host_tool"], "spawn_agent")
        self.assertEqual(request["fork_turns"], "none")
        self.assertNotIn("thread_environment", request)

    def test_visible_thread_route_is_rejected_by_the_v11_spawn_builder(self) -> None:
        with self.assertRaisesRegex(ValueError, "native spawn_agent"):
            delegation.spawn_request(
                dispatch_mode="visible_thread",
                gate="implementation",
                agent="backend_dev",
                display_name="Backend worker",
                task_name="backend_dev_change_01_ab12cd34",
                profiles={"backend_dev": {"description": "Backend", "sandbox": "workspace-write", "route_category": "implementation"}},
                selection_reason="bounded test",
                route={"selected_model": "gpt-5.6-luna", "selected_reasoning_effort": "medium"},
                thread_environment="worktree",
            )


if __name__ == "__main__":
    unittest.main()
