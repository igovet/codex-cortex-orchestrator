"""Deterministic regressions for the Planner completion transport seam."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
import sys

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cortex
from cortex_runtime import attempt_facade, attempt_protocol, ledger_db, worker_identity


class PlannerCompletionPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cortex-planner-completion-")
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.host_store = Path(self.temporary.name) / "host-store"
        self.host_store.mkdir(mode=0o700)
        # ``mkdir(mode=...)`` is subject to the process umask and can also be
        # affected by a preconfigured test runner/container policy.  The
        # runtime deliberately fails closed for host state that is writable
        # by group/other users, so make the fixture's contract explicit before
        # the first ledger operation rather than relying on ambient modes.
        self.host_store.chmod(0o700)
        self._host_store_env = mock.patch.dict(
            os.environ, {"CORTEX_HOST_STATE_DIR": str(self.host_store)}, clear=False,
        )
        self._host_store_env.start()
        self.root = self.project / "cortex"
        ledger_db.ensure_database(self.root)
        self.task_id = "planner-completion-task"
        self.task_dir = self.root / "tasks" / "0001-planner-completion-task"
        self.task_dir.mkdir(parents=True)
        self.state = {
            "schema": cortex.SCHEMA,
            "task_id": self.task_id,
            "task_number": 1,
            "task_revision": 1,
            "revision": 1,
            "attempts": [],
        }
        ledger_db.create_task(
            self.root,
            {"schema": cortex.SCHEMA, "task_id": self.task_id, "project_root": str(self.project)},
            self.state,
            "tasks/0001-planner-completion-task",
        )
        self.attempt = {"attempt_id": "plan-01", "gate": "plan", "profile": "planner"}

    def tearDown(self) -> None:
        self._host_store_env.stop()
        self.temporary.cleanup()

    def _complete(self, params: dict[str, object], attempt: dict[str, object] | None = None) -> dict:
        """Call the worker facade through its server-bound semantic channel."""
        source = attempt or self.attempt
        binding = {
            "project_root": str(self.project),
            "task_id": self.task_id,
            "attempt_id": str(params.get("attempt_id") or source.get("attempt_id") or "plan-01"),
            "profile": str(params.get("profile") or source.get("profile") or "planner"),
        }
        semantic = {key: value for key, value in params.items() if key not in worker_identity.SERVER_OWNED_FIELDS}
        with worker_identity.worker_binding(binding):
            return attempt_facade.complete_attempt(semantic)

    @staticmethod
    def planning() -> dict[str, object]:
        return {
            "overview": "Implement the bounded change and verify its public contract.",
            "requirement_coverage": [],
            "recommendation": "approve",
            "recommendation_rationale": "The work is bounded and directly verifiable.",
            "resolved_questions": [],
            "risks": [],
            "work_packages": [{
                "id": "core",
                "title": "Core change",
                "objective": "Implement the server-owned completion seam.",
                "allowed_paths": ["plugins/cortex"],
                "depends_on": [],
                "status": "pending",
                "order": 1,
                "gates": ["implementation"],
                "microtasks": [{
                    "id": "core_change",
                    "title": "Persist the plan",
                    "objective": "Write and verify the immutable plan revision.",
                    "profile": "backend_dev",
                    "allowed_paths": ["plugins/cortex"],
                    "depends_on": [],
                    "status": "pending",
                    "order": 1,
                    "gates": ["implementation"],
                    "acceptance_criteria": ["The current planning pointer is readable."],
                    "verification": ["Read the immutable package artifact."],
                }],
            }],
        }

    def test_schema_advertises_planner_only_planning_sibling(self) -> None:
        schema = cortex.PUBLIC_SCHEMA_REGISTRY["complete_attempt"]
        planning = schema["properties"]["planning"]
        self.assertEqual(planning["type"], "object")
        self.assertEqual(planning["required"], ["overview", "work_packages"])
        self.assertFalse(planning["additionalProperties"])

    def test_schema_has_a_patch_only_repair_variant(self) -> None:
        schema = cortex.PUBLIC_SCHEMA_REGISTRY["complete_attempt"]
        # Worker identity and bearer transport are server-owned; the repair
        # branch contains only the semantic patch proof.
        self.assertEqual(schema["required"], [])
        repair = next(
            branch for branch in schema["oneOf"]
            if set(branch["required"]) == {"base_payload_digest", "patches"}
        )
        self.assertIn({"required": ["planning"]}, repair["not"]["anyOf"])
        for field in ("status", "summary", "findings", "decisions_needed", "unresolved", "claims"):
            self.assertIn({"required": [field]}, repair["not"]["anyOf"])
        self.assertEqual(schema["properties"]["patches"]["minItems"], 1)
        self.assertIn("diagnostic", schema["properties"]["patches"]["description"])

    def test_materialization_is_atomic_and_idempotent(self) -> None:
        first = cortex.materialize_planning_payload(
            self.task_dir, self.state, self.attempt, "attempt-result-01", self.planning(),
        )
        second = cortex.materialize_planning_payload(
            self.task_dir, self.state, self.attempt, "attempt-result-01", self.planning(),
        )
        self.assertEqual(second, first)
        self.assertEqual(
            len(ledger_db.list_artifacts(self.root, self.task_id, kind="planning_revision")[0]),
            2,
        )
        self.assertEqual(
            cortex.current_planning_manifest(self.task_dir)["source_result_ref"],
            "attempt-result-01",
        )
        package_path = first["work_packages"][0]["artifact_path"]
        package, metadata = cortex.read_immutable_json_artifact(
            self.task_dir, self.task_id, package_path, kinds={"planning_revision"},
        )
        self.assertEqual(package["package"]["id"], "core")
        self.assertEqual(metadata["artifact_ref"], first["work_packages"][0]["artifact_ref"])

    def test_malformed_plan_leaves_no_pointer_or_artifacts(self) -> None:
        malformed = self.planning()
        malformed["work_packages"][0]["microtasks"][0]["verification"] = []
        with self.assertRaisesRegex(ValueError, "verification"):
            cortex.materialize_planning_payload(
                self.task_dir, self.state, self.attempt, "attempt-result-02", malformed,
            )
        self.assertIsNone(cortex.current_planning_manifest(self.task_dir))
        self.assertEqual(ledger_db.list_artifacts(self.root, self.task_id, kind="planning_revision")[0], [])

    def test_non_planner_planning_is_rejected_before_canonical_completion(self) -> None:
        params = {
            "project_root": str(self.project),
            "task_id": self.task_id,
            "attempt_id": "implementation-01",
            "profile": "backend_dev",
            "status": "completed",
            "summary": "A normal completion.",
            "findings": [],
            "decisions_needed": [],
            "unresolved": [],
            "planning": self.planning(),
        }
        attempt = {"attempt_id": "implementation-01", "gate": "implementation", "profile": "backend_dev", "status": "running"}
        with mock.patch.object(attempt_facade, "_worker_context", return_value=(self.project, self.task_dir, self.state, attempt, "backend_dev")), \
             mock.patch.object(attempt_protocol, "complete_attempt") as complete:
            response = self._complete(params)
        self.assertFalse(response["ok"])
        self.assertIn("only for planner attempts", response["diagnostics"][0]["message"])
        complete.assert_not_called()

    def test_planner_missing_payload_is_rejected_before_canonical_completion(self) -> None:
        attempt = {
            "attempt_id": "plan-01", "gate": "plan", "profile": "planner",
            "status": "running", "dispatch_ref": "dispatch-plan-01",
        }
        params = {
            "project_root": str(self.project), "task_id": self.task_id,
            "attempt_id": "plan-01", "profile": "planner", "status": "completed",
            "summary": "The plan is ready.", "findings": [],
            "decisions_needed": [], "unresolved": [],
        }
        with mock.patch.object(attempt_facade, "_worker_context", return_value=(self.project, self.task_dir, self.state, attempt, "planner")), \
             mock.patch.object(attempt_protocol, "complete_attempt") as complete:
            response = self._complete(params)
        self.assertFalse(response["ok"])
        self.assertIn("planning payload", response["diagnostics"][0]["message"])
        complete.assert_not_called()

    def test_planner_invalid_payload_is_rejected_before_canonical_completion(self) -> None:
        attempt = {
            "attempt_id": "plan-01", "gate": "plan", "profile": "planner",
            "status": "running", "dispatch_ref": "dispatch-plan-01",
        }
        malformed = self.planning()
        malformed["work_packages"][0]["microtasks"][0]["verification"] = []
        params = {
            "project_root": str(self.project), "task_id": self.task_id,
            "attempt_id": "plan-01", "profile": "planner", "status": "completed",
            "summary": "The plan is ready.", "findings": [],
            "decisions_needed": [], "unresolved": [], "planning": malformed,
        }
        with mock.patch.object(attempt_facade, "_worker_context", return_value=(self.project, self.task_dir, self.state, attempt, "planner")), \
             mock.patch.object(attempt_protocol, "complete_attempt") as complete:
            response = self._complete(params)
        self.assertFalse(response["ok"])
        self.assertIn("verification", response["diagnostics"][0]["message"])
        complete.assert_not_called()

    def test_planner_reports_independent_shape_errors_in_one_response(self) -> None:
        attempt = {"attempt_id": "plan-01", "gate": "plan", "profile": "planner", "status": "running"}
        malformed = {
            "overview": 42,
            "recommendation": "later",
            "resolved_questions": "not-an-array",
            "work_packages": [
                {"id": "one", "title": "x", "objective": "y", "microtasks": []},
                {"id": "two", "title": "x", "objective": "y", "microtasks": "not-an-array"},
            ],
        }
        params = {"status": "completed", "summary": "ready", "findings": [], "decisions_needed": [], "unresolved": [], "planning": malformed}
        binding = {"project_root": str(self.project), "task_id": self.task_id, "attempt_id": "plan-01", "profile": "planner"}
        with worker_identity.worker_binding(binding), \
             mock.patch.object(attempt_facade, "_worker_context", return_value=(self.project, self.task_dir, self.state, attempt, "planner")), \
             mock.patch.object(attempt_protocol, "complete_attempt") as complete:
            response = self._complete(params)
        self.assertFalse(response["ok"])
        self.assertGreaterEqual(len(response["diagnostics"]), 4)
        self.assertTrue(any("recommendation" in item["message"] for item in response["diagnostics"]))
        self.assertRegex(response.get("base_payload_digest", ""), r"^sha256:[0-9a-f]{64}$")
        self.assertTrue(response["planning_repair"]["preserve_other_fields"])
        self.assertEqual(response["planning_repair"]["mode"], "same_attempt_patch")
        self.assertIn("/overview", response["planning_repair"]["patch_paths"])
        self.assertIn("base_payload_digest", response["next_action"])
        complete.assert_not_called()
        self.assertEqual(ledger_db.list_artifacts(self.root, self.task_id)[0], [])
        rejected = ledger_db.get_task_document(
            self.root, self.task_id, "planning_rejected_draft:plan-01",
        )
        self.assertIsNotNone(rejected)
        self.assertEqual(rejected["planning"]["work_packages"][0]["id"], "one")
        self.assertEqual(rejected["planning"]["work_packages"][1]["id"], "two")
        self.assertEqual(
            rejected["base_payload_digest"], response["base_payload_digest"],
        )
        self.assertIsNone(attempt_protocol.get_attempt_result(
            self.root, task_id=self.task_id, attempt_id="plan-01",
        ))

    def test_top_level_planning_fields_are_rejected_without_alias_recovery(self) -> None:
        """Planning is accepted only under the canonical nested sibling."""
        attempt = {
            "attempt_id": "plan-01", "gate": "plan", "profile": "planner",
            "status": "running", "dispatch_ref": "dispatch-plan-01",
        }
        raw = self.planning()
        # This is the exact planner mistake seen in the runtime receipt: the
        # planning siblings are emitted at complete_attempt's root.
        params = {
            "status": "completed", "summary": "ready", "findings": [],
            "decisions_needed": [], "unresolved": [], "claims": [], **raw,
        }
        binding = {
            "project_root": str(self.project), "task_id": self.task_id,
            "attempt_id": "plan-01", "profile": "planner",
        }
        with worker_identity.worker_binding(binding), \
             mock.patch.object(attempt_facade, "_worker_context", return_value=(self.project, self.task_dir, self.state, attempt, "planner")), \
             mock.patch.object(attempt_protocol, "complete_attempt") as complete:
            response = self._complete(params)
        self.assertFalse(response["ok"], response)
        self.assertTrue(any(item.get("path") == "$.overview" for item in response["diagnostics"]))
        self.assertFalse(any("canonical_path" in item for item in response["diagnostics"]))
        self.assertNotIn("planning_repair", response)
        complete.assert_not_called()
        self.assertIsNone(attempt_protocol.get_attempt_result(
            self.root, task_id=self.task_id, attempt_id="plan-01",
        ))

    def test_planner_collects_prose_gate_and_coverage_errors_without_rebuilding_valid_plan(self) -> None:
        malformed = self.planning()
        malformed["work_packages"][0]["id"] = "release_safety"
        malformed["work_packages"][0]["gates"] = ["release", "warmup"]
        malformed["requirement_coverage"] = [{
            "requirement": "preserve the requested scope",
            "plan_refs": ["missing_item"],
            "verification": ["inspect the resulting plan"],
            "status": "covered",
        }]
        with self.assertRaises(attempt_facade._runtime.PlanningValidationError) as raised:
            attempt_facade._runtime.sanitize_planning_payload(malformed)
        diagnostics = raised.exception.diagnostics
        self.assertTrue(any(item["code"] == "planning_coverage_invalid" for item in diagnostics))
        coverage = next(item for item in diagnostics if item["code"] == "planning_coverage_invalid")
        self.assertEqual(coverage["path"], "planning.requirement_coverage[0].plan_refs")
        self.assertEqual(
            cortex.planning_diagnostic_patch_paths(diagnostics),
            ["/requirement_coverage/0/plan_refs"],
        )
        self.assertFalse(any(item["code"] == "planning_gates_invalid" for item in diagnostics))

    def test_planner_repair_rejects_patch_outside_diagnostic_scope_atomically(self) -> None:
        attempt = {"attempt_id": "plan-01", "gate": "plan", "profile": "planner", "status": "running"}
        state = {**self.state, "attempts": [attempt]}
        draft = cortex.planning_rejected_draft_document(
            self.task_dir,
            state,
            attempt,
            self.planning(),
            [{"code": "planning_gates_invalid", "path": "planning.work_packages[0].gates", "message": "bad gate"}],
            {"status": "completed", "summary": "draft", "findings": [], "decisions_needed": [], "unresolved": [], "claims": []},
        )
        params = {
            "project_root": str(self.project), "task_id": self.task_id,
            "attempt_id": "plan-01", "profile": "planner",
            "status": "completed", "summary": "draft", "findings": [],
            "decisions_needed": [], "unresolved": [], "claims": [],
            "base_payload_digest": draft["base_payload_digest"],
            "patches": [{"op": "replace", "path": "/overview", "value": "rewritten"}],
        }
        with mock.patch.object(attempt_facade, "_worker_context", return_value=(self.project, self.task_dir, state, attempt, "planner")), \
             mock.patch.object(attempt_protocol, "complete_attempt") as complete:
            response = self._complete(params)
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "repair_planning_invalid")
        self.assertIn("patches", response["next_action"])
        complete.assert_not_called()
        persisted = ledger_db.get_task_document(self.root, self.task_id, "planning_rejected_draft:plan-01")
        self.assertEqual(persisted["schema"], "cortex/planning-rejected-draft/v1")
        self.assertEqual(persisted["attempt_id"], "plan-01")
        self.assertEqual(persisted["base_payload_digest"], draft["base_payload_digest"])
        self.assertEqual(persisted["planning"]["overview"], self.planning()["overview"])

    def test_planner_repair_applies_patch_then_finalizes_same_attempt(self) -> None:
        attempt = {
            "attempt_id": "plan-01", "gate": "plan", "profile": "planner",
            "status": "running", "dispatch_ref": "dispatch-plan-01",
        }
        state = {**self.state, "attempts": [attempt]}
        draft = cortex.planning_rejected_draft_document(
            self.task_dir,
            state,
            attempt,
            self.planning(),
            [{"code": "planning_gates_invalid", "path": "planning.work_packages[0].gates", "message": "bad gate"}],
            {"status": "completed", "summary": "draft", "findings": [], "decisions_needed": [], "unresolved": [], "claims": []},
        )
        params = {
            "base_payload_digest": draft["base_payload_digest"],
            "patches": [{"op": "replace", "path": "/work_packages/0/gates", "value": ["implementation"]}],
        }
        binding = {
            "project_root": str(self.project), "task_id": self.task_id,
            "attempt_id": "plan-01", "profile": "planner",
        }
        canonical = {
            "result_ref": "attempt-result-repaired", "status": "completed",
            "result_status": "completed", "summary": "draft",
            "lifecycle_status": attempt_protocol.LIFECYCLE_WORK_COMPLETED,
        }
        finalized = {**canonical, "lifecycle_status": attempt_protocol.LIFECYCLE_COMPLETED}
        with worker_identity.worker_binding(binding), \
             mock.patch.object(attempt_facade, "_worker_context", return_value=(self.project, self.task_dir, state, attempt, "planner")), \
             mock.patch.object(attempt_facade, "_receipt_guard", return_value={}), \
             mock.patch.object(attempt_facade, "_workspace_observation", return_value={}), \
             mock.patch.object(attempt_facade, "_mark_attempt"), \
             mock.patch.object(attempt_protocol, "get_attempt_result", return_value=None), \
             mock.patch.object(attempt_protocol, "complete_attempt", return_value={"result": canonical}), \
             mock.patch.object(attempt_protocol, "begin_attempt_finalization"), \
             mock.patch.object(attempt_protocol, "build_attempt_result_view", return_value={"projection_ref": "view-repaired"}), \
             mock.patch.object(attempt_protocol, "finalize_attempt", return_value={"result": finalized}):
            response = self._complete(params)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["attempt_result_ref"], "attempt-result-repaired")
        manifest = cortex.current_planning_manifest(self.task_dir)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["source_result_ref"], "attempt-result-repaired")
        self.assertEqual(manifest["repair"]["mode"], "same_attempt_patch")
        self.assertEqual(manifest["repair"]["patch_paths"], ["/work_packages/0/gates"])

    def test_planner_rejects_full_regeneration_after_rejected_draft(self) -> None:
        attempt = {"attempt_id": "plan-01", "gate": "plan", "profile": "planner", "status": "running"}
        state = {**self.state, "attempts": [attempt]}
        draft = cortex.planning_rejected_draft_document(
            self.task_dir,
            state,
            attempt,
            self.planning(),
            [{"code": "planning_coverage_invalid", "path": "planning.requirement_coverage[0].plan_refs", "message": "bad ref"}],
            {"status": "completed", "summary": "draft", "findings": [], "decisions_needed": [], "unresolved": [], "claims": []},
        )
        regenerated = self.planning()
        regenerated["overview"] = "the model regenerated everything"
        params = {
            "status": "completed", "summary": "draft", "findings": [],
            "decisions_needed": [], "unresolved": [], "claims": [],
            "planning": regenerated,
        }
        binding = {"project_root": str(self.project), "task_id": self.task_id, "attempt_id": "plan-01", "profile": "planner"}
        with worker_identity.worker_binding(binding), \
             mock.patch.object(attempt_facade, "_worker_context", return_value=(self.project, self.task_dir, state, attempt, "planner")), \
             mock.patch.object(attempt_protocol, "complete_attempt") as complete:
            response = self._complete(params)
        self.assertFalse(response["ok"])
        self.assertEqual(response["base_payload_digest"], draft["base_payload_digest"])
        self.assertEqual(response["planning_repair"]["mode"], "same_attempt_patch")
        self.assertEqual(response["planning_repair"]["patch_paths"], ["/requirement_coverage/0/plan_refs"])
        self.assertIn("/requirement_coverage/0/plan_refs", response["next_action"])
        self.assertIn("patches", response["planning_repair"]["instruction"])
        complete.assert_not_called()
        persisted = ledger_db.get_task_document(self.root, self.task_id, "planning_rejected_draft:plan-01")
        self.assertEqual(persisted["planning"]["overview"], self.planning()["overview"])

    def test_attempt_result_reports_independent_envelope_errors_together(self) -> None:
        attempt = {"attempt_id": "implementation-01", "gate": "implementation", "profile": "backend_dev", "status": "running"}
        params = {
            "project_root": str(self.project), "task_id": self.task_id,
            "attempt_id": "implementation-01", "profile": "backend_dev",
            "status": "not-a-status", "summary": "", "findings": "bad",
            "decisions_needed": 7, "unresolved": {}, "claims": "bad",
        }
        with self.assertRaises(attempt_protocol.AttemptValidationError) as raised:
            attempt_protocol._normalise_result({
                "status": params["status"], "summary": params["summary"],
                "findings": params["findings"], "decisions_needed": params["decisions_needed"],
                "unresolved": params["unresolved"], "claims": params["claims"],
            }, status=None, summary=None, findings=None, decisions_needed=None, unresolved=None, claims=None)
        paths = {item.get("path") for item in raised.exception.diagnostics}
        self.assertTrue({"status", "summary", "findings", "decisions_needed", "unresolved", "claims"}.issubset(paths))
        self.assertEqual(ledger_db.list_artifacts(self.root, self.task_id)[0], [])

    def test_planner_dependency_cycles_are_reported_as_cross_field_diagnostic(self) -> None:
        malformed = self.planning()
        micro = malformed["work_packages"][0]["microtasks"][0]
        micro["depends_on"] = ["core_change"]
        with self.assertRaisesRegex(ValueError, "acyclic"):
            cortex.sanitize_planning_payload(malformed)

    def test_planning_repair_scope_normalizes_indexed_diagnostic_paths(self) -> None:
        diagnostics = [{
            "code": "planning_validation_failed",
            "path": "planning.work_packages[0].microtasks[0].verification",
            "message": "verification is required",
        }]
        self.assertTrue(cortex.planning_diagnostic_scope_allows(
            diagnostics,
            ["/work_packages/0/microtasks/0/verification/0"],
        ))
        self.assertFalse(cortex.planning_diagnostic_scope_allows(
            diagnostics,
            ["/work_packages/1/microtasks/0/verification"],
        ))
        self.assertEqual(
            cortex.planning_diagnostic_patch_paths([
                {"path": "$.planning.work_packages[0].microtasks[0].verification"},
                {"patch_path": "/planning/overview"},
            ]),
            ["/work_packages/0/microtasks/0/verification", "/overview"],
        )

    def test_planner_recovery_rejects_changed_semantic_envelope_without_mutation(self) -> None:
        """A materialization retry cannot replace the immutable planner result."""
        attempt = {
            "attempt_id": "plan-01", "gate": "plan", "profile": "planner",
            "status": "running", "dispatch_ref": "dispatch-plan-01",
        }
        state = {**self.state, "attempts": [attempt]}
        params = {
            "project_root": str(self.project), "task_id": self.task_id,
            "attempt_id": "plan-01", "profile": "planner", "status": "completed",
            # Deliberately differs from the already committed canonical result.
            "summary": "A changed retry envelope must not replace the result.",
            "findings": [{"retry": "changed"}], "decisions_needed": [],
            "unresolved": [], "claims": [], "planning": self.planning(),
        }
        canonical = {
            "result_ref": "attempt-result-existing",
            "status": "completed", "result_status": "completed",
            "summary": "The immutable canonical planner result.",
            "findings": [], "decisions_needed": [], "unresolved": [], "claims": [],
            "workspace_observation": {}, "changed_files": [],
            "lifecycle_status": attempt_protocol.LIFECYCLE_WORK_COMPLETED,
            "submission_id": "completion-existing",
        }
        finalized = {**canonical, "lifecycle_status": attempt_protocol.LIFECYCLE_COMPLETED}
        with mock.patch.object(attempt_facade, "_worker_context", return_value=(self.project, self.task_dir, state, attempt, "planner")), \
             mock.patch.object(attempt_facade._runtime, "ledger_root", return_value=self.root), \
             mock.patch.object(attempt_facade, "_receipt_guard", return_value={}), \
             mock.patch.object(attempt_facade, "_workspace_observation", return_value={}), \
             mock.patch.object(attempt_facade, "_mark_attempt"), \
             mock.patch.object(attempt_facade._runtime, "materialize_planning_payload") as materialize, \
             mock.patch.object(attempt_protocol, "get_attempt_result", return_value=canonical), \
             mock.patch.object(attempt_protocol, "complete_attempt", side_effect=attempt_protocol.CanonicalResultConflict(result_ref="attempt-result-existing")) as complete, \
             mock.patch.object(attempt_protocol, "begin_attempt_finalization"), \
             mock.patch.object(attempt_protocol, "build_attempt_result_view", return_value={"projection_ref": "view-existing"}), \
             mock.patch.object(attempt_protocol, "finalize_attempt", return_value={"result": finalized}):
            response = self._complete(params)
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["code"], "attempt_canonical_result_conflict")
        self.assertFalse(response["retryable"])
        complete.assert_called_once()
        materialize.assert_not_called()

    def test_planner_recovery_invalid_payload_does_not_touch_existing_result(self) -> None:
        attempt = {
            "attempt_id": "plan-01", "gate": "plan", "profile": "planner",
            "status": "running", "dispatch_ref": "dispatch-plan-01",
        }
        malformed = self.planning()
        malformed["work_packages"][0]["microtasks"][0]["verification"] = []
        params = {
            "project_root": str(self.project), "task_id": self.task_id,
            "attempt_id": "plan-01", "profile": "planner", "status": "completed",
            "summary": "A conflicting retry.", "findings": [],
            "decisions_needed": [], "unresolved": [], "planning": malformed,
        }
        with mock.patch.object(attempt_facade, "_worker_context", return_value=(self.project, self.task_dir, self.state, attempt, "planner")), \
             mock.patch.object(attempt_protocol, "complete_attempt") as complete:
            response = self._complete(params)
        self.assertFalse(response["ok"], response)
        self.assertIn("verification", response["diagnostics"][0]["message"])
        complete.assert_not_called()

    def test_planner_facade_persists_plan_after_canonical_completion(self) -> None:
        attempt = {
            "attempt_id": "plan-01",
            "gate": "plan",
            "profile": "planner",
            "status": "running",
            "dispatch_ref": "dispatch-plan-01",
        }
        state = {**self.state, "attempts": [attempt]}
        params = {
            "project_root": str(self.project),
            "task_id": self.task_id,
            "attempt_id": "plan-01",
            "profile": "planner",
            "status": "completed",
            "summary": "The plan is ready for review.",
            "findings": [],
            "decisions_needed": [],
            "unresolved": [],
            "planning": self.planning(),
        }
        canonical = {
            "result_ref": "attempt-result-03",
            "status": "completed",
            "result_status": "completed",
            "summary": params["summary"],
            "lifecycle_status": attempt_protocol.LIFECYCLE_WORK_COMPLETED,
        }
        finalized = {**canonical, "lifecycle_status": attempt_protocol.LIFECYCLE_COMPLETED}
        with mock.patch.object(attempt_facade, "_worker_context", return_value=(self.project, self.task_dir, state, attempt, "planner")), \
             mock.patch.object(attempt_facade._runtime, "ledger_root", return_value=self.root), \
             mock.patch.object(attempt_facade, "_receipt_guard", return_value={}), \
             mock.patch.object(attempt_facade, "_workspace_observation", return_value={}), \
             mock.patch.object(attempt_facade, "_mark_attempt"), \
             mock.patch.object(attempt_protocol, "get_attempt_result", return_value=None), \
             mock.patch.object(attempt_protocol, "complete_attempt", return_value={"result": canonical}), \
             mock.patch.object(attempt_protocol, "begin_attempt_finalization"), \
             mock.patch.object(attempt_protocol, "build_attempt_result_view", return_value={"projection_ref": "view-03"}), \
             mock.patch.object(attempt_protocol, "finalize_attempt", return_value={"result": finalized}):
            response = self._complete(params)
        self.assertTrue(response["ok"], response)
        self.assertIsNotNone(cortex.current_planning_manifest(self.task_dir))
        self.assertEqual(
            cortex.current_planning_manifest(self.task_dir)["source_result_ref"],
            "attempt-result-03",
        )


if __name__ == "__main__":
    unittest.main()
