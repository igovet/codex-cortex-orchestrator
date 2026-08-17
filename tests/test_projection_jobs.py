from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
from cortex_runtime.projections import (  # noqa: E402
    ProjectionDigestError,
    ProjectionPathError,
    materialize_projection,
    remove_optional_projection,
)
from cortex_runtime import ledger_db, projection_service  # noqa: E402


class ProjectionMaterializerTests(unittest.TestCase):
    def test_materializes_private_file_and_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            task.mkdir(mode=0o700)
            body = b"canonical report\n"
            digest = hashlib.sha256(body).hexdigest()
            result = materialize_projection(task, "reports/record.json", body, digest)
            self.assertTrue(result.materialized)
            self.assertEqual(result.path.read_bytes(), body)
            self.assertEqual(stat_mode(result.path), 0o600)
            self.assertEqual(stat_mode(result.path.parent), 0o700)

    def test_matching_file_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            task.mkdir()
            body = b"same"
            digest = hashlib.sha256(body).hexdigest()
            first = materialize_projection(task, "projection.json", body, digest)
            first.path.write_bytes(body)
            second = materialize_projection(task, "projection.json", body, digest)
            self.assertTrue(first.materialized)
            self.assertFalse(second.materialized)

    def test_accepts_an_existing_safe_task_relative_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            task.mkdir()
            (task / "delegations").mkdir()
            body = b"dispatch briefing"
            result = materialize_projection(
                task, "delegations/dispatch.briefing.md", body, hashlib.sha256(body).hexdigest(),
            )
            self.assertEqual(result.path.read_bytes(), body)
            self.assertEqual(stat_mode(task / "delegations"), 0o700)

    def test_rejects_traversal_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            task.mkdir()
            body = b"x"
            digest = hashlib.sha256(body).hexdigest()
            for path in ("../outside", "/tmp/outside"):
                with self.assertRaises(ProjectionPathError):
                    materialize_projection(task, path, body, digest)
            outside = Path(directory) / "outside"
            outside.mkdir()
            (task / "linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ProjectionPathError):
                materialize_projection(task, "linked/file", body, digest)

    def test_detects_wrong_digest_and_tampered_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            task.mkdir()
            body = b"canonical"
            digest = hashlib.sha256(body).hexdigest()
            with self.assertRaises(ProjectionDigestError):
                materialize_projection(task, "report", body + b"changed", digest)
            materialize_projection(task, "report", body, digest)
            (task / "report").write_bytes(b"tampered")
            with self.assertRaises(ProjectionDigestError):
                materialize_projection(task, "report", body, digest)

    def test_recovers_after_preexisting_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            task.mkdir()
            (task / ".report.tmp-old").write_bytes(b"stale")
            body = b"recover"
            digest = hashlib.sha256(body).hexdigest()
            result = materialize_projection(task, "report", body, digest)
            self.assertEqual(result.path.read_bytes(), body)
            self.assertEqual((task / ".report.tmp-old").read_bytes(), b"stale")
            self.assertEqual([item.name for item in task.glob(".report.tmp-*")], [".report.tmp-old"])

    def test_remove_optional_is_safe_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            task.mkdir()
            result = remove_optional_projection(task, "optional.md")
            self.assertFalse(result.removed)
            (task / "optional.md").write_text("projection", encoding="utf-8")
            self.assertTrue(remove_optional_projection(task, "optional.md").removed)
            self.assertFalse(remove_optional_projection(task, "optional.md").removed)
            outside = Path(directory) / "outside"
            outside.write_text("keep", encoding="utf-8")
            (task / "link").symlink_to(outside)
            with self.assertRaises(ProjectionPathError):
                remove_optional_projection(task, "link")
            self.assertTrue(outside.exists())


class ProjectionServiceTests(unittest.TestCase):
    def _root_with_artifact(self, directory: str) -> tuple[Path, Path, dict[str, object]]:
        project = Path(directory) / "project"
        project.mkdir()
        root = project / ".codex" / "cortex"
        task_id = "projection-task"
        ledger_db.ensure_database(root)
        ledger_db.create_task(
            root,
            {"schema": "cortex/orchestration/v3", "task_id": task_id, "created_at": "2026-01-01T00:00:00+00:00"},
            {"schema": "cortex/orchestration/v3", "task_id": task_id, "task_number": 1, "status": "active", "revision": 1, "updated_at": "2026-01-01T00:00:00+00:00"},
            "tasks/0001-projection-task",
        )
        task_dir = root / "tasks" / "0001-projection-task"
        task_dir.mkdir(parents=True)
        artifact = ledger_db.put_artifact(
            root, task_id, kind="report_markdown", title="reports/canonical.md",
            mime_type="text/markdown", content="# Canonical\n",
        )
        return root, task_dir, artifact

    def test_one_logical_artifact_can_materialize_multiple_authorized_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, task_dir, artifact = self._root_with_artifact(directory)
            first = projection_service.enqueue(
                root=root, task_id="projection-task", artifact_id=str(artifact["artifact_ref"]),
                projection_type="report_markdown", export_path="reports/current.md",
            )
            second = projection_service.enqueue(
                root=root, task_id="projection-task", artifact_id=str(artifact["artifact_ref"]),
                projection_type="report_markdown", export_path="reports/archive/current.md",
            )
            self.assertNotEqual(first["projection_key"], second["projection_key"])
            self.assertEqual(
                [row["export_path"] for row in ledger_db.list_artifact_exports(root, "projection-task", str(artifact["artifact_ref"]))],
                ["reports/archive/current.md", "reports/current.md"],
            )
            completed = projection_service.reconcile(root, worker_id="multi-export")
            self.assertEqual({row["status"] for row in completed}, {"ready"})
            self.assertEqual((task_dir / "reports/current.md").read_text(encoding="utf-8"), "# Canonical\n")
            self.assertEqual((task_dir / "reports/archive/current.md").read_text(encoding="utf-8"), "# Canonical\n")

    def test_on_demand_materialization_claims_only_its_requested_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, task_dir, artifact = self._root_with_artifact(directory)
            briefing = projection_service.enqueue(
                root=root, task_id="projection-task", artifact_id=str(artifact["artifact_ref"]),
                projection_type="dispatch_briefing", export_path="delegations/dispatch.briefing.md",
            )
            report = projection_service.enqueue(
                root=root, task_id="projection-task", artifact_id=str(artifact["artifact_ref"]),
                projection_type="report_markdown", export_path="reports/current.md",
            )

            completed_briefing = projection_service.materialize(
                root, str(briefing["projection_key"]), worker_id="dispatch-owner",
            )
            untouched_report = ledger_db.get_projection_job(root, str(report["projection_key"]))

            self.assertEqual(completed_briefing["status"], "ready")
            self.assertEqual(completed_briefing["attempts"], 1)
            self.assertIsNotNone(untouched_report)
            self.assertEqual(untouched_report["status"], "pending")
            self.assertEqual(untouched_report["attempts"], 0)
            self.assertIsNone(untouched_report["lease_owner"])
            self.assertIsNone(untouched_report["lease_expires_at"])

            completed_report = projection_service.materialize(
                root, str(report["projection_key"]), worker_id="report-owner",
            )
            self.assertEqual(completed_report["status"], "ready")
            self.assertEqual(completed_report["lease_owner"], None)
            self.assertEqual((task_dir / "delegations/dispatch.briefing.md").read_text(encoding="utf-8"), "# Canonical\n")
            self.assertEqual((task_dir / "reports/current.md").read_text(encoding="utf-8"), "# Canonical\n")

    def test_deleted_optional_export_is_rebuilt_by_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, task_dir, artifact = self._root_with_artifact(directory)
            job = projection_service.enqueue(
                root=root, task_id="projection-task", artifact_id=str(artifact["artifact_ref"]),
                projection_type="report_markdown", export_path="reports/optional.md",
            )
            projection_service.materialize_job(root, job, worker_id="first-write")
            removed = projection_service.remove_optional(root, job)
            self.assertTrue(removed["removed"])
            self.assertFalse(projection_service.verify_job(root, job).present)
            repaired = projection_service.reconcile(root, worker_id="restore-optional")
            self.assertEqual(repaired[-1]["status"], "ready")
            self.assertEqual((task_dir / "reports/optional.md").read_text(encoding="utf-8"), "# Canonical\n")
            self.assertTrue(projection_service.verify_job(root, job).valid)

    def test_tampered_export_is_detected_and_repair_records_a_failed_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, task_dir, artifact = self._root_with_artifact(directory)
            job = projection_service.enqueue(
                root=root, task_id="projection-task", artifact_id=str(artifact["artifact_ref"]),
                projection_type="report_markdown", export_path="reports/tampered.md",
            )
            projection_service.materialize_job(root, job, worker_id="first-write")
            (task_dir / "reports/tampered.md").write_text("not canonical", encoding="utf-8")
            verification = projection_service.verify_job(root, job)
            self.assertTrue(verification.present)
            self.assertFalse(verification.valid)
            with self.assertRaises(ProjectionDigestError):
                projection_service.repair(root, job, worker_id="tamper-repair")
            self.assertEqual(len(projection_service.list_failed(root, task_id="projection-task")), 1)

    def test_unsafe_export_path_is_rejected_before_an_outbox_row_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, _task_dir, artifact = self._root_with_artifact(directory)
            with self.assertRaises(ValueError):
                projection_service.enqueue(
                    root=root, task_id="projection-task", artifact_id=str(artifact["artifact_ref"]),
                    projection_type="report_markdown", export_path="../outside.md",
                )
            self.assertEqual(projection_service.list_pending(root, task_id="projection-task"), [])


def stat_mode(path: Path) -> int:
    return os.stat(path, follow_symlinks=False).st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
