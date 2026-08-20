"""Deterministic coverage for explicit SQLite health maintenance."""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

from cortex_runtime import health_maintenance, ledger_db
import cortex


class HealthMaintenanceTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name) / ".codex" / "cortex"
        ledger_db.ensure_database(root)
        return directory, root

    def test_health_is_read_only_and_reports_schema_checks_and_projection_state(self) -> None:
        directory, root = self.make_root()
        self.addCleanup(directory.cleanup)
        database = ledger_db.database_path(root)
        before = database.stat().st_mtime_ns

        result = health_maintenance.manage_health_maintenance(root, {"action": "health"})

        self.assertEqual(result["operation"], "health")
        self.assertTrue(result["quick_check"]["ok"])
        self.assertTrue(result["foreign_key_check"]["ok"])
        self.assertTrue(result["schema"]["current"])
        self.assertEqual(result["schema"]["expected_version"], ledger_db.DATABASE_SCHEMA_VERSION)
        self.assertFalse(result["checkpoint"]["performed"])
        self.assertEqual(database.stat().st_mtime_ns, before)

    def test_sqlite_backup_and_restore_verification_are_consistent_and_private(self) -> None:
        directory, root = self.make_root()
        self.addCleanup(directory.cleanup)
        ledger_db.put_global(root, "health-backup-fixture", {"value": "canonical"})

        backup = health_maintenance.manage_health_maintenance(root, {
            "action": "backup", "confirmation": "BACKUP", "backup_name": "ledger-check.sqlite",
        })
        verified = health_maintenance.manage_health_maintenance(root, {
            "action": "verify_backup_restore", "backup_name": "ledger-check.sqlite",
        })
        target = root / "backups" / "ledger-check.sqlite"

        self.assertEqual(backup["operation"], "backup")
        self.assertTrue(target.is_file())
        self.assertEqual(target.stat().st_mode & 0o077, 0)
        self.assertTrue(verified["restored_with_sqlite_backup_api"])
        self.assertTrue(verified["quick_check"]["ok"])
        self.assertTrue(verified["foreign_key_check"]["ok"])
        self.assertTrue(verified["schema"]["current"])

    def test_mutating_actions_require_exact_confirmation_and_backup_names_are_contained(self) -> None:
        directory, root = self.make_root()
        self.addCleanup(directory.cleanup)

        with self.assertRaisesRegex(ValueError, "confirmation='CHECKPOINT'"):
            health_maintenance.manage_health_maintenance(root, {"action": "checkpoint"})
        with self.assertRaisesRegex(ValueError, "safe .sqlite filename"):
            health_maintenance.manage_health_maintenance(root, {
                "action": "backup", "confirmation": "BACKUP", "backup_name": "../escape.sqlite",
            })
        with self.assertRaisesRegex(ValueError, "confirmation='VACUUM'"):
            health_maintenance.manage_health_maintenance(root, {"action": "vacuum", "confirmation": "PRUNE"})
        with self.assertRaisesRegex(ValueError, "confirmation='RECONCILE'"):
            health_maintenance.manage_health_maintenance(root, {"action": "reconcile_projections"})

    def test_backup_uses_sqlite_backup_api_not_a_direct_database_file_copy(self) -> None:
        implementation = inspect.getsource(health_maintenance._backup_connection)
        self.assertIn("source_connection.backup(destination)", implementation)
        self.assertNotIn("shutil", implementation)
        self.assertNotIn("copyfile", implementation)

    def test_reconciliation_is_explicit_and_routes_through_projection_service(self) -> None:
        directory, root = self.make_root()
        self.addCleanup(directory.cleanup)
        result = health_maintenance.manage_health_maintenance(root, {
            "action": "reconcile_projections", "confirmation": "RECONCILE", "limit": 10,
        })
        self.assertTrue(result["scheduled"])
        self.assertEqual(result["processed"], [])
        self.assertIn("projection service", result["delegation"]["reason"])

    def test_health_does_not_create_a_missing_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            with self.assertRaisesRegex(ValueError, "Cortex root is unavailable"):
                health_maintenance.manage_health_maintenance(root, {"action": "health"})
            self.assertFalse(root.exists())

    def test_public_maintenance_intent_routes_to_health_without_task_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            host_store = base / "host-private-store"
            host_store.mkdir(mode=0o700)
            host_store.chmod(0o700)
            with mock.patch.dict(
                os.environ,
                {cortex.HOST_CONTROL_STORE_ENV: str(host_store), "CORTEX_ROOT": ""},
                clear=False,
            ):
                root = cortex.ledger_root({"project_root": str(project)})
                result = cortex.manage_orchestration({
                    "project_root": str(project),
                    "intent": "maintenance",
                    "payload": {"action": "health"},
                })
                self.assertEqual(result["operation"], "health")
                self.assertTrue(result["quick_check"]["ok"])
                self.assertTrue((root / "cortex.db").is_file())


if __name__ == "__main__":
    unittest.main()
