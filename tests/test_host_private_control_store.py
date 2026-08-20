"""Focused P2.2 coverage for host-private Cortex control-plane storage."""
from __future__ import annotations

import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"))

import cortex as control
import cortex_hook
from cortex_runtime import ledger_db


class HostPrivateControlStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.other_project = self.base / "other-project"
        self.other_project.mkdir()
        self.host_store = self.base / "host-private-store"
        self.host_store.mkdir(mode=0o700)
        self.host_store.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _environment(self):
        # CORTEX_HOST_STATE_DIR is process/operator configuration, not a tool
        # argument.  The temporary directory is already mode 0700, so these
        # tests also exercise the host-permission checks.
        return mock.patch.dict(
            os.environ,
            {control.HOST_CONTROL_STORE_ENV: str(self.host_store), "CORTEX_ROOT": ""},
            clear=False,
        )

    def _legacy_root(self) -> Path:
        root = self.project / ".codex" / "cortex"
        root.mkdir(parents=True, mode=0o700)
        root.parent.chmod(0o700)
        root.chmod(0o700)
        ledger_db.ensure_database(root)
        return root

    def test_mapping_is_opaque_private_and_never_creates_workspace_database(self) -> None:
        with self._environment():
            expected_id = control.stable_project_id(self.project)
            root = control.ledger_root({"project_root": str(self.project)})

            self.assertEqual(root, self.host_store / "projects" / expected_id)
            self.assertRegex(root.name, r"^p-[0-9a-f]{64}$")
            self.assertTrue((root / "cortex.db").is_file())
            self.assertEqual(root.stat().st_mode & 0o077, 0)
            self.assertEqual((root / "cortex.db").stat().st_mode & 0o077, 0)
            self.assertFalse((self.project / ".codex" / "cortex" / "cortex.db").exists())
            self.assertEqual(
                control.ledger_root_path({"project_root": str(self.project)}),
                root,
            )

    def test_projects_have_distinct_control_planes_and_hook_resolves_the_private_mapping(self) -> None:
        with self._environment():
            first = control.ledger_root({"project_root": str(self.project)})
            second = control.ledger_root({"project_root": str(self.other_project)})
            self.assertNotEqual(first, second)
            ledger_db.put_global(first, "isolation-sentinel", {"project": "first"})
            self.assertEqual(ledger_db.get_global(first, "isolation-sentinel", {}), {"project": "first"})
            self.assertEqual(ledger_db.get_global(second, "isolation-sentinel", {}), {})

            resolved = cortex_hook.root({"tool_input": {"project_root": str(self.project)}})
            self.assertEqual(resolved, first)
            self.assertFalse((self.project / ".codex" / "cortex" / "cortex.db").exists())
            self.assertFalse((self.other_project / ".codex" / "cortex" / "cortex.db").exists())

    def test_legacy_sqlite_ledger_moves_as_one_private_same_filesystem_tree(self) -> None:
        legacy = self._legacy_root()
        ledger_db.put_global(legacy, "migration-sentinel", {"preserved": True})

        with self._environment():
            private = control.ledger_root({"project_root": str(self.project)})

        self.assertFalse(legacy.exists())
        self.assertTrue((private / "cortex.db").is_file())
        self.assertEqual(ledger_db.get_global(private, "migration-sentinel", {}), {"preserved": True})
        self.assertFalse((self.project / ".codex" / "cortex" / "cortex.db").exists())

    def test_dual_legacy_and_private_databases_fail_closed_without_selecting_one(self) -> None:
        legacy = self._legacy_root()
        ledger_db.put_global(legacy, "legacy-only", {"value": "legacy"})
        with self._environment():
            target = control.ledger_root_path({"project_root": str(self.project)}, create=True)
            target.mkdir(mode=0o700)
            target.chmod(0o700)
            ledger_db.ensure_database(target)
            ledger_db.put_global(target, "private-only", {"value": "private"})
            with self.assertRaisesRegex(ValueError, "both legacy project-local and host-private Cortex ledgers"):
                control.ledger_root({"project_root": str(self.project)})

        self.assertTrue((legacy / "cortex.db").is_file())
        self.assertTrue((target / "cortex.db").is_file())
        self.assertEqual(ledger_db.get_global(legacy, "legacy-only", {}), {"value": "legacy"})
        self.assertEqual(ledger_db.get_global(target, "private-only", {}), {"value": "private"})

    def test_cross_filesystem_migration_failure_leaves_only_the_legacy_database(self) -> None:
        legacy = self._legacy_root()
        with self._environment():
            target = control.ledger_root_path({"project_root": str(self.project)}, create=True)
            with mock.patch.object(control.os, "replace", side_effect=OSError(errno.EXDEV, "cross-device link")):
                with self.assertRaisesRegex(ValueError, "cannot be migrated atomically"):
                    control.ledger_root({"project_root": str(self.project)})

        self.assertTrue((legacy / "cortex.db").is_file())
        self.assertFalse(target.exists())

    def test_legacy_symlink_fails_closed_without_creating_a_private_ledger(self) -> None:
        backing = self.base / "legacy-backing"
        backing.mkdir(mode=0o700)
        backing.chmod(0o700)
        ledger_db.ensure_database(backing)
        legacy_parent = self.project / ".codex"
        legacy_parent.mkdir(mode=0o700)
        legacy_parent.chmod(0o700)
        legacy = legacy_parent / "cortex"
        legacy.symlink_to(backing, target_is_directory=True)

        with self._environment():
            target = control.ledger_root_path({"project_root": str(self.project)})
            with self.assertRaisesRegex(ValueError, "legacy project-local Cortex root.*symlink"):
                control.ledger_root({"project_root": str(self.project)})

        self.assertTrue(legacy.is_symlink())
        self.assertTrue((backing / "cortex.db").is_file())
        self.assertFalse(target.exists())

    def test_host_store_configuration_inside_the_workspace_is_rejected_before_any_database_write(self) -> None:
        unsafe_store = self.project / ".codex" / "host-store"
        with mock.patch.dict(
            os.environ,
            {control.HOST_CONTROL_STORE_ENV: str(unsafe_store), "CORTEX_ROOT": ""},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "must be outside project_root"):
                control.ledger_root({"project_root": str(self.project)})
        self.assertFalse(unsafe_store.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
