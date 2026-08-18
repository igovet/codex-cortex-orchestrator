"""Focused regression coverage for on-demand report Markdown links."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex as control  # noqa: E402
from cortex_runtime import ledger_db, reports  # noqa: E402


class ReportLinkProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        project = Path(self.temp.name) / "project"
        project.mkdir()
        self.root = project / ".codex" / "cortex"
        self.task_id = "report-link-task"
        ledger_db.ensure_database(self.root)
        ledger_db.create_task(
            self.root,
            {"schema": "cortex/orchestration/v3", "task_id": self.task_id, "created_at": "2026-01-01T00:00:00+00:00"},
            {"schema": "cortex/orchestration/v3", "task_id": self.task_id, "task_number": 1, "status": "active", "revision": 1, "updated_at": "2026-01-01T00:00:00+00:00"},
            "tasks/0001-report-link-task",
        )
        self.task_dir = self.root / "tasks" / "0001-report-link-task"
        self.task_dir.mkdir(parents=True)
        self.report_ref = "report-0001"
        self.relative_path = f"reports/markdown/{self.report_ref}.md"
        self.artifact = ledger_db.put_artifact(
            self.root,
            self.task_id,
            kind="report_markdown",
            title=self.relative_path,
            mime_type="text/markdown",
            content="# Canonical report\n",
            export_path=self.relative_path,
        )
        self.state = {"task_id": self.task_id}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_link_path_is_materialized_and_verified_only_when_requested(self) -> None:
        self.assertFalse((self.task_dir / self.relative_path).exists())
        with mock.patch.object(reports, "state_lock", side_effect=AssertionError("must not lock state")):
            path = reports.ensure_report_markdown_path(self.task_dir, self.state, self.report_ref)

        self.assertEqual(path, self.task_dir / self.relative_path)
        self.assertEqual(path.read_text(encoding="utf-8"), "# Canonical report\n")
        jobs = ledger_db.list_projection_jobs(self.root, task_id=self.task_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "ready")
        self.assertEqual(jobs[0]["materialized_digest"], self.artifact["digest_sha256"])

    def test_missing_ready_markdown_projection_creates_a_durable_repair(self) -> None:
        path = reports.ensure_report_markdown_path(self.task_dir, self.state, self.report_ref)
        path.unlink()

        repaired = reports.ensure_report_markdown_path(self.task_dir, self.state, self.report_ref)

        self.assertEqual(repaired.read_text(encoding="utf-8"), "# Canonical report\n")
        jobs = ledger_db.list_projection_jobs(self.root, task_id=self.task_id)
        self.assertEqual({job["status"] for job in jobs}, {"ready"})
        self.assertEqual(len(jobs), 2)
        self.assertTrue(any(str(job["projection_key"]).startswith("repair-") for job in jobs))

    def test_materialization_failure_keeps_canonical_artifact_and_records_failed_job(self) -> None:
        with mock.patch.object(reports, "materialize_projection_job", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                reports.ensure_report_markdown_path(self.task_dir, self.state, self.report_ref)

        self.assertEqual(
            ledger_db.read_artifact_content(self.root, self.task_id, str(self.artifact["artifact_ref"])),
            "# Canonical report\n",
        )
        jobs = ledger_db.list_projection_jobs(self.root, task_id=self.task_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "failed")
        self.assertIn("disk unavailable", jobs[0]["last_error"])


if __name__ == "__main__":
    unittest.main()
