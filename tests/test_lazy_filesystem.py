"""Regression coverage for SQLite-first task layout and lazy projections."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex as control
from cortex_runtime import projection_service


class LazyFilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.host_store = Path(self.temp.name) / "host-private-store"
        self.host_store.mkdir(mode=0o700)
        self.host_store.chmod(0o700)
        self._previous_host_store = os.environ.get(control.HOST_CONTROL_STORE_ENV)
        os.environ[control.HOST_CONTROL_STORE_ENV] = str(self.host_store)
        self.ledger = control.ledger_root_path({"project_root": str(self.project)})

    def tearDown(self) -> None:
        if self._previous_host_store is None:
            os.environ.pop(control.HOST_CONTROL_STORE_ENV, None)
        else:
            os.environ[control.HOST_CONTROL_STORE_ENV] = self._previous_host_store
        self.temp.cleanup()

    def _start(self, objective: str = "lazy filesystem projection") -> dict:
        return control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": objective,
                "plan_approval": "auto",
                "acceptance_criteria": ["Persist the result canonically."],
                "verification": ["Run the focused regression."],
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        })

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
            "user_request": "Keep task layout pristine.",
            "classification_id": classified["classification_id"], "principal": "coordinator",
        })
        task_dir = self.ledger / "tasks" / created["task_directory"]
        self.assertFalse(task_dir.exists())
        control.question_bus_paths(task_dir)
        control.planning_paths(task_dir)
        self.assertFalse(task_dir.exists())

if __name__ == "__main__":
    unittest.main()
