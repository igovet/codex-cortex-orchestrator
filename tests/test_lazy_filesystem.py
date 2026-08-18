"""Regression coverage for SQLite-first task layout and lazy projections."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex as control
from cortex_runtime import projection_service
from cortex_runtime import reports as runtime_reports


class LazyFilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.ledger = self.project / ".codex/cortex"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _start(self, objective: str = "lazy filesystem projection") -> dict:
        return control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": objective,
                "acceptance_criteria": ["Persist the result canonically."],
                "verification": ["Run the focused regression."],
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        })

    @staticmethod
    def _report(attempt: dict) -> dict:
        evidence = [control.dispatch_briefing_review_marker(attempt["briefing_digest"])]
        for label in ("Gate acceptance", "Gate verification", "Task acceptance", "Task verification"):
            for index in range(1, 3):
                evidence.append(f"{label} {index}: PASS - observed focused regression output confirms the contract.")
        return {
            "summary": "The SQLite-backed report is complete.",
            "findings": [], "questions": [], "changed_files": [],
            "tests": [{
                "command": "python3 -m unittest tests.test_lazy_filesystem",
                "cwd": ".", "exit_code": 0,
                "evidence": "Observed output: focused regression passed with zero failures.",
            }],
            "evidence": evidence,
            "uncertainty": [], "next_action": "Advance the gate.",
        }

    def test_initialization_and_path_helpers_do_not_materialize_task_artifacts(self) -> None:
        control.activate_orchestration({
            "project_root": str(self.project), "user_command": "/cortex",
            "principal": "coordinator", "thread_id": "coordinator",
        })
        classified = control.classify_task({
            "project_root": str(self.project), "principal": "coordinator",
            "complexity": "C1", "requirements": [],
        })
        created = control.init_task({
            "project_root": str(self.project), "task_id": "pristine",
            "objective": "Keep task layout pristine.",
            "classification_id": classified["classification_id"], "principal": "coordinator",
        })
        task_dir = self.ledger / "tasks" / created["task_directory"]
        self.assertFalse(task_dir.exists())

        control.report_bus_paths(task_dir)
        control.question_bus_paths(task_dir)
        control.planning_paths(task_dir)
        self.assertFalse(task_dir.exists())

    def test_briefing_is_the_only_eager_projection_and_reports_are_on_demand(self) -> None:
        started = self._start()
        self.assertTrue(started["ok"], started)
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]

        files = [path.relative_to(task_dir).as_posix() for path in task_dir.rglob("*") if path.is_file()]
        self.assertEqual(files, [attempt["briefing_file"]])
        self.assertFalse((task_dir / "reports").exists())
        self.assertFalse((task_dir / "questions").exists())
        self.assertFalse((task_dir / "planning").exists())
        self.assertFalse((task_dir / "journal.md").exists())

        published = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": self._report(attempt),
        })
        self.assertTrue(published["ok"], published)
        self.assertFalse((task_dir / "reports").exists())

        read = control.read_worker_report({
            "project_root": str(self.project), "task_ref": started["task_ref"],
            "report_ref": published["report_ref"],
        })
        self.assertTrue(read["ok"], read)
        markdown = Path(read["report_markdown_path"])
        self.assertTrue(markdown.is_file())
        self.assertFalse((task_dir / "reports/records").exists())
        self.assertFalse((task_dir / "reports/receipts").exists())

    def test_optional_markdown_failure_keeps_report_and_reconcile_rebuilds_it(self) -> None:
        started = self._start("failed optional export")
        task_dir = next((self.ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        published = control.publish_worker_report({
            "project_root": str(self.project), "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"], "profile": attempt["profile"],
            "report": self._report(attempt),
        })
        self.assertTrue(published["ok"], published)

        with mock.patch.object(runtime_reports, "materialize_projection_job", side_effect=OSError("disk unavailable")):
            unavailable = control.read_worker_report({
                "project_root": str(self.project), "task_ref": started["task_ref"],
                "report_ref": published["report_ref"],
            })
        self.assertFalse(unavailable["ok"])
        self.assertEqual(unavailable["code"], "report_unavailable")
        self.assertFalse((task_dir / "reports/markdown/report-0001.md").exists())
        self.assertEqual(
            control.list_task_reports({
                "project_root": str(self.project), "task_id": state["task_id"],
                "principal": state["principal"],
            })["reports"][0]["report_id"],
            published["report_ref"],
        )

        repaired = projection_service.reconcile(self.ledger, worker_id="lazy-test")
        self.assertTrue(repaired)
        self.assertTrue((task_dir / "reports/markdown/report-0001.md").is_file())


if __name__ == "__main__":
    unittest.main()
