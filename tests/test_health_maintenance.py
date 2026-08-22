"""Deterministic coverage for explicit SQLite health maintenance."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

from cortex_runtime import governance, health_maintenance, ledger_db
import cortex


class HealthMaintenanceTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name) / ".codex" / "cortex"
        ledger_db.ensure_database(root)
        return directory, root

    def test_health_is_read_only_and_exposes_schema_checks_and_projection_state(self) -> None:
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

    def test_health_exposes_physical_lock_state_without_conflating_task_blockers(self) -> None:
        directory, root = self.make_root()
        self.addCleanup(directory.cleanup)
        lock_path = root / ".state.lock"
        lock_path.touch(mode=0o600)

        result = health_maintenance.manage_health_maintenance(root, {"action": "health"})

        lock = result["availability"]["lock"]
        self.assertEqual(lock["scope"], "project")
        self.assertEqual(lock["probe"], "nonblocking")
        self.assertIn(lock["state"], {"free", "unsupported"})
        self.assertIn(lock["held"], {False, None})
        if lock["state"] == "free":
            self.assertFalse(lock["held"])
        self.assertIn("task/gate blockers", result["availability"]["meaning"])

    def test_health_lock_probe_exposes_busy_without_waiting(self) -> None:
        directory, root = self.make_root()
        self.addCleanup(directory.cleanup)
        lock_path = root / ".state.lock"
        lock_path.touch(mode=0o600)

        class BusyFcntl:
            LOCK_EX = 1
            LOCK_NB = 2
            LOCK_UN = 4

            @staticmethod
            def flock(_fd: int, operation: int) -> None:
                if operation == BusyFcntl.LOCK_EX | BusyFcntl.LOCK_NB:
                    raise BlockingIOError("busy")

        with mock.patch.object(health_maintenance, "fcntl", BusyFcntl):
            lock = health_maintenance._state_lock_status(root)

        self.assertEqual(lock["state"], "busy")
        self.assertTrue(lock["held"])

    def test_dr_bundle_restores_governance_authenticity_on_a_fresh_host_root(self) -> None:
        directory, root = self.make_root()
        self.addCleanup(directory.cleanup)
        ledger_db.put_global(root, "health-backup-fixture", {"value": "canonical"})
        governance.create_record(
            root,
            record_type="policy",
            content={"fixture": "backup-governance"},
            status="approved",
            created_by="coordinator",
        )

        backup = health_maintenance.manage_health_maintenance(root, {
            "action": "backup", "confirmation": "BACKUP", "backup_name": "ledger-check.cortex-backup",
        })
        verified = health_maintenance.manage_health_maintenance(root, {
            "action": "verify_backup_restore", "backup_name": "ledger-check.cortex-backup",
        })
        target = root / "backups" / "ledger-check.cortex-backup"

        self.assertEqual(backup["operation"], "backup")
        self.assertEqual(backup["bundle_schema"], "cortex/ledger-backup/v1")
        self.assertTrue(target.is_dir())
        self.assertEqual(target.stat().st_mode & 0o077, 0)
        for member in target.iterdir():
            self.assertEqual(member.stat().st_mode & 0o077, 0, member)
        self.assertTrue((target / "cortex.db").is_file())
        self.assertTrue((target / "governance-lifecycle.key").is_file())
        self.assertTrue((target / "manifest.json").is_file())
        self.assertNotIn("governance_lifecycle_key", backup)
        self.assertTrue(verified["restored_with_sqlite_backup_api"])
        self.assertTrue(verified["quick_check"]["ok"])
        self.assertTrue(verified["foreign_key_check"]["ok"])
        self.assertTrue(verified["schema"]["current"])
        self.assertTrue(verified["governance"]["fresh_host_root"])
        self.assertEqual(verified["governance"]["verified_records"], 1)

    def test_backup_verification_rejects_tampered_key_or_prior_sqlite_snapshot(self) -> None:
        directory, root = self.make_root()
        self.addCleanup(directory.cleanup)
        health_maintenance.manage_health_maintenance(root, {
            "action": "backup", "confirmation": "BACKUP", "backup_name": "tamper.cortex-backup",
        })
        key = root / "backups" / "tamper.cortex-backup" / "governance-lifecycle.key"
        key.write_bytes(b"x" * 32)
        key.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "key fingerprint"):
            health_maintenance.manage_health_maintenance(root, {
                "action": "verify_backup_restore", "backup_name": "tamper.cortex-backup",
            })
        with self.assertRaisesRegex(ValueError, "safe .cortex-backup bundle name"):
            health_maintenance.manage_health_maintenance(root, {
                "action": "backup", "confirmation": "BACKUP", "backup_name": "prior.sqlite",
            })

    def test_backup_verification_uses_governance_authority_not_only_manifest_hashes(self) -> None:
        directory, root = self.make_root()
        self.addCleanup(directory.cleanup)
        governance.create_record(
            root,
            record_type="policy",
            content={"fixture": "authority-check"},
            status="approved",
            created_by="coordinator",
        )
        health_maintenance.manage_health_maintenance(root, {
            "action": "backup", "confirmation": "BACKUP", "backup_name": "authority.cortex-backup",
        })
        bundle = root / "backups" / "authority.cortex-backup"
        key = bundle / "governance-lifecycle.key"
        # Model a bundle attacker who can also recompute the non-secret
        # manifest fingerprint.  The v12 lifecycle read must still reject the
        # mismatched key rather than treating fingerprint agreement as proof.
        key.write_bytes(b"y" * 32)
        key.chmod(0o600)
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["governance_lifecycle_key"]["sha256"] = hashlib.sha256(key.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        manifest_path.chmod(0o600)
        with self.assertRaisesRegex(governance.GovernanceError, "lifecycle authority"):
            health_maintenance.manage_health_maintenance(root, {
                "action": "verify_backup_restore", "backup_name": "authority.cortex-backup",
            })

    def test_mutating_actions_require_exact_confirmation_and_backup_names_are_contained(self) -> None:
        directory, root = self.make_root()
        self.addCleanup(directory.cleanup)

        with self.assertRaisesRegex(ValueError, "confirmation='CHECKPOINT'"):
            health_maintenance.manage_health_maintenance(root, {"action": "checkpoint"})
        with self.assertRaisesRegex(ValueError, "safe .cortex-backup bundle name"):
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
