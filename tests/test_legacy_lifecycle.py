"""Safety tests for the opt-in pre-SQLite filesystem maintenance workflow."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from tests.cortex_test_support import HostPrivateControlStoreTestMixin


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex
from cortex_runtime import ledger_db


class LegacyLifecycleTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.set_up_host_private_control_store()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.root = self.project / ".codex" / "cortex"

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()
        self.temporary.cleanup()

    def manage(self, payload: dict[str, object]) -> dict[str, object]:
        return cortex.manage_orchestration({
            "project_root": str(self.project),
            "intent": "legacy",
            "payload": payload,
        })

    def make_legacy_task(self, name: str = "0001-legacy") -> Path:
        task = self.root / "tasks" / name
        (task / "reports" / "records").mkdir(parents=True)
        (task / "current.json").write_text(json.dumps({"task_id": "old-task"}), encoding="utf-8")
        (task / "task.json").write_text(json.dumps({"task_id": "old-task"}), encoding="utf-8")
        (task / "reports" / "records" / "report.json").write_text("{}", encoding="utf-8")
        return task

    def test_inventory_is_read_only_and_reports_known_patterns_count_and_size(self) -> None:
        self.root.mkdir(parents=True)
        index = self.root / "task-index.json"
        index.write_text("{}", encoding="utf-8")
        self.make_legacy_task()
        before = {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

        result = self.manage({"action": "inventory"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["outcome"], "legacy_inventory")
        self.assertTrue(result["read_only"])
        self.assertEqual(result["file_count"], 4)
        self.assertGreater(result["total_bytes"], 0)
        self.assertIn("root-file:task-index.json", result["recognized_patterns"])
        self.assertIn("task-ledger", result["recognized_patterns"])
        self.assertTrue(result["safe_to_archive"])
        after = {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)
        self.assertFalse((self.root / "cortex.db").exists())

    def test_archive_is_private_and_delete_needs_exact_confirmation(self) -> None:
        self.root.mkdir(parents=True)
        legacy_file = self.root / "task-index.json"
        legacy_file.write_text("{}", encoding="utf-8")

        archived = self.manage({"action": "archive"})

        self.assertTrue(archived["ok"])
        self.assertEqual(archived["outcome"], "legacy_archived")
        archive_path = Path(str(archived["archive_path"]))
        self.assertEqual(archive_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(archive_path.parent.stat().st_mode & 0o777, 0o700)
        archive_id = str(archived["archive_id"])

        rejected = self.manage({"action": "delete", "archive_id": archive_id, "confirmation": "DELETE_LEGACY_ARCHIVE:other"})
        self.assertFalse(rejected["ok"])
        self.assertTrue(legacy_file.exists())

        deleted = self.manage({
            "action": "delete",
            "archive_id": archive_id,
            "confirmation": f"DELETE_LEGACY_ARCHIVE:{archive_id}",
        })
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["deleted_file_count"], 1)
        self.assertFalse(legacy_file.exists())
        self.assertTrue(archive_path.exists())

    def test_unknown_legacy_task_structure_and_symlinks_block_archive(self) -> None:
        self.root.mkdir(parents=True)
        task = self.make_legacy_task()
        (task / "unknown.bin").write_bytes(b"not a supported legacy record")

        inventory = self.manage({"action": "inventory"})
        self.assertFalse(inventory["safe_to_archive"])
        self.assertTrue(any("unknown legacy task file" in issue for issue in inventory["issues"]))
        self.assertFalse(self.manage({"action": "archive"})["ok"])

        (task / "unknown.bin").unlink()
        legacy_records = self.root / "classification-receipts"
        legacy_records.mkdir()
        os.symlink(self.root / "task-index.json", legacy_records / "unsafe.json")
        inventory = self.manage({"action": "inventory"})
        self.assertFalse(inventory["safe_to_archive"])
        self.assertTrue(any("symlink" in issue for issue in inventory["issues"]))
        self.assertFalse(self.manage({"action": "archive"})["ok"])

    def test_current_sqlite_task_and_projection_paths_are_never_archived_or_deleted(self) -> None:
        ledger_db.ensure_database(self.root)
        current_task = self.root / "tasks" / "0002-current"
        (current_task / "delegations").mkdir(parents=True)
        current_marker = current_task / "current.json"
        current_marker.write_text("current SQLite projection", encoding="utf-8")
        (current_task / "task.json").write_text("current SQLite projection", encoding="utf-8")
        projection_marker = self.root / "projections" / "current.txt"
        projection_marker.parent.mkdir()
        projection_marker.write_text("current projection", encoding="utf-8")
        ledger_db.create_task(
            self.root,
            {"schema": cortex.SCHEMA, "task_id": "current-task", "task_number": 2, "created_at": "2026-01-01T00:00:00+00:00"},
            {"schema": cortex.SCHEMA, "task_id": "current-task", "task_number": 2, "status": "active", "revision": 0, "updated_at": "2026-01-01T00:00:00+00:00"},
            "tasks/0002-current",
        )
        legacy_file = self.root / "task-index.json"
        legacy_file.write_text("{}", encoding="utf-8")

        archived = self.manage({"action": "archive"})
        self.assertTrue(archived["ok"])
        self.assertEqual(archived["file_count"], 1)
        archive_id = str(archived["archive_id"])
        deleted = self.manage({
            "action": "delete",
            "archive_id": archive_id,
            "confirmation": f"DELETE_LEGACY_ARCHIVE:{archive_id}",
        })

        self.assertTrue(deleted["ok"])
        self.assertFalse(legacy_file.exists())
        self.assertEqual(current_marker.read_text(encoding="utf-8"), "current SQLite projection")
        self.assertEqual(projection_marker.read_text(encoding="utf-8"), "current projection")
        self.assertIsNotNone(ledger_db.load_task(self.root, "current-task"))

    def test_normal_ledger_initialization_refuses_unsupported_legacy_state(self) -> None:
        self.root.mkdir(parents=True)
        legacy_file = self.root / "task-index.json"
        legacy_file.write_text("{}", encoding="utf-8")
        private_root = cortex.ledger_root_path({"project_root": str(self.project)})

        with self.assertRaisesRegex(ValueError, "legacy project-local Cortex directory has no regular cortex.db"):
            cortex.ledger_root({"project_root": str(self.project)})

        self.assertTrue(legacy_file.exists())
        self.assertFalse((self.root / "legacy-archives").exists())
        self.assertFalse(private_root.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
