"""Deterministic regressions for the Planner completion transport seam."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
import sys

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cortex
from cortex_runtime import attempt_facade, attempt_protocol, ledger_db


class PlannerCompletionPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cortex-planner-completion-")
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
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
        self.temporary.cleanup()

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
            response = attempt_facade.complete_attempt(params)
        self.assertFalse(response["ok"])
        self.assertIn("only for planner attempts", response["diagnostics"][0]["message"])
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
            response = attempt_facade.complete_attempt(params)
        self.assertTrue(response["ok"], response)
        self.assertIsNotNone(cortex.current_planning_manifest(self.task_dir))
        self.assertEqual(
            cortex.current_planning_manifest(self.task_dir)["source_result_ref"],
            "attempt-result-03",
        )


if __name__ == "__main__":
    unittest.main()
