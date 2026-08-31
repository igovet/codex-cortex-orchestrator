"""Fail-closed maintenance coverage for the v17 plan-review relation."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.v12_maintenance import (  # noqa: E402
    V12MaintenanceError,
    checkpoint,
    health,
)
from cortex_runtime.v12_store import V12Store  # noqa: E402


_PLAN_REVIEW_RELATION_COLUMNS = (
    "plan_content_digest",
    "plan_approval_handle",
    "plan_view_content_digest",
    "plan_view_source_sequence",
)


class V17MaintenanceSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cortex-v17-maintenance-")
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.home = self.root / "codex-home"
        self.project.mkdir()
        self.home.mkdir()
        self.environment = mock.patch.dict(
            os.environ, {"CODEX_HOME": str(self.home)}, clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def _healthy_v17_store(self) -> tuple[V12Store, str]:
        store = V12Store(self.project)
        task, _ = store.create_task(
            objective="Verify v17 maintenance schema.",
            user_request_original="Verify v17 maintenance schema.",
            user_language="en",
            task_contract_version="cortex/task-contract/v3-outcome-linked",
            requirements=["Retain v17 relation columns."],
            constraints=["Maintenance must fail closed."],
            acceptance_criteria=["Report missing immutable relation columns."],
            verification_plan=["Inspect maintenance schema health."],
            context={},
            idempotency_key="v17-maintenance-schema-task",
        )
        return store, str(task["task"]["task_id"])

    def test_healthy_v17_schema_passes_maintenance_health(self) -> None:
        _, task_id = self._healthy_v17_store()

        result = health(task_id=task_id)

        self.assertTrue(result["healthy"])
        self.assertTrue(result["checks"]["schema"])
        self.assertTrue(result["checks"]["migrations"])

    def test_each_missing_v17_plan_review_relation_column_fails_closed(self) -> None:
        for column in _PLAN_REVIEW_RELATION_COLUMNS:
            with self.subTest(column=column), tempfile.TemporaryDirectory(
                prefix=f"cortex-v17-missing-{column}-",
            ) as isolated:
                # Each subtest owns a complete nominal v17 shard whose
                # migration record remains intact while one required column is
                # absent. Direct SQLite mutation is fixture construction only.
                isolated_root = Path(isolated)
                project = isolated_root / "project"
                project.mkdir()
                store = V12Store(project)
                task, _ = store.create_task(
                    objective="Reject incomplete v17 relation.",
                    user_request_original="Reject incomplete v17 relation.",
                    user_language="en",
                    task_contract_version="cortex/task-contract/v3-outcome-linked",
                    requirements=["Validate all v17 relation columns."],
                    constraints=["Do not repair malformed schema."],
                    acceptance_criteria=["Fail closed with safe maintenance code."],
                    verification_plan=["Run read-only maintenance health."],
                    context={},
                    idempotency_key=f"v17-missing-column-{column}",
                )
                task_id = str(task["task"]["task_id"])
                with sqlite3.connect(store.database_path) as connection:
                    connection.execute(f"ALTER TABLE clarification_bindings DROP COLUMN {column}")

                reported = health(task_id=task_id)
                self.assertFalse(reported["healthy"])
                self.assertFalse(reported["checks"]["schema"])
                # A maintenance action must not attempt a write/repair once
                # health has detected the malformed nominal-v17 layout.
                with self.assertRaises(V12MaintenanceError) as failure:
                    checkpoint(task_id=task_id, confirm_action="CHECKPOINT")
                self.assertEqual(failure.exception.code, "maintenance_precondition_failed")


if __name__ == "__main__":
    unittest.main()
