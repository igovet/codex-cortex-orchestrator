"""Migration and storage invariants for the SQLite Cortex ledger."""
from __future__ import annotations

import json
import hashlib
import multiprocessing
import queue
import shutil
import sqlite3
import sys
import tempfile
import unittest
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from tests.cortex_test_support import HostPrivateControlStoreTestMixin

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))
import cortex
from cortex_runtime import ledger_db, v11_submission


def _first_boot_in_process(root_text: str, ready: multiprocessing.Queue, start: multiprocessing.synchronize.Event, results: multiprocessing.Queue) -> None:
    """Run the public first-boot path in a distinct interpreter process."""
    try:
        ready.put(True)
        if not start.wait(10):
            raise RuntimeError("parent did not release concurrent first boot")
        last_error: BaseException | None = None
        for _ in range(20):
            try:
                history = ledger_db.migration_history(Path(root_text))
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                last_error = exc
                time.sleep(0.05)
        else:
            raise last_error or RuntimeError("concurrent bootstrap did not converge")
        results.put(("ok", [item["version"] for item in history]))
    except BaseException as exc:  # pragma: no cover - asserted by the parent.
        results.put(("error", f"{type(exc).__name__}: {exc}"))


def _hold_state_lock_in_process(root_text: str, ready: multiprocessing.Event, release: multiprocessing.Event) -> None:
    """Hold the mutation lease so the parent can exercise bounded contention."""
    with cortex.state_lock(
        Path(root_text), timeout_seconds=2.0, operation="regression_hold", task_id="holder-task"
    ):
        ready.set()
        if not release.wait(10):
            raise RuntimeError("parent did not release state lock")


class LedgerDatabaseTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.set_up_host_private_control_store()

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()

    @staticmethod
    def create_task(root: Path, task_id: str = "artifact-task") -> None:
        definition = {"schema": cortex.SCHEMA, "task_id": task_id, "created_at": "2026-01-01T00:00:00+00:00"}
        state = {
            "schema": cortex.SCHEMA, "task_id": task_id, "task_number": 1,
            "status": "active", "revision": 1, "updated_at": "2026-01-01T00:00:00+00:00",
        }
        ledger_db.create_task(root, definition, state, f"tasks/0001-{task_id}")

    @staticmethod
    def repair_escrow_fixture(root: Path, task_id: str = "repair-task", attempt_id: str = "repair-attempt") -> dict[str, object]:
        ledger_db.create_task(
            root,
            {"schema": cortex.SCHEMA, "task_id": task_id, "created_at": "2026-01-01T00:00:00+00:00"},
            {
                "schema": cortex.SCHEMA, "task_id": task_id, "task_number": 1,
                "status": "active", "revision": 1, "updated_at": "2026-01-01T00:00:00+00:00",
                "attempts": [{"attempt_id": attempt_id, "status": "running"}],
            },
            f"tasks/0001-{task_id}",
        )
        payload = {"status": "completed", "summary": ""}
        diagnostics = [{
            "code": "validation_invalid", "json_pointer": "/outcome/summary",
            "repair_pointer": "/summary", "message": "must not be empty",
        }]
        return {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "task_ref_digest": v11_submission.canonical_digest("task-000000000001"),
            "assignment_ref_digest": v11_submission.canonical_digest("assignment-v1-" + "a" * 64),
            "kind": "outcome",
            "base_payload_digest": v11_submission.canonical_digest(payload),
            "payload": payload,
            "diagnostics": diagnostics,
            "allowed_paths": ["/summary"],
        }

    def test_repair_escrow_is_immutable_and_cascades_only_with_owning_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            fixture = self.repair_escrow_fixture(root)
            row = ledger_db.store_repair_escrow(root, **fixture)
            with ledger_db.connection(root, write=True) as connection:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "repair escrow rows are immutable"):
                    connection.execute(
                        "UPDATE repair_escrow SET allowed_paths_json='[]' WHERE handle_digest=?",
                        (row["handle_digest"],),
                    )
            self.assertIsNotNone(ledger_db.get_repair_escrow(root, handle_digest=row["handle_digest"]))
            self.assertEqual(ledger_db.delete_tasks(root, {str(fixture["task_id"])}), 1)
            self.assertIsNone(ledger_db.get_repair_escrow(root, handle_digest=row["handle_digest"]))

    def test_concurrent_identical_rejected_drafts_reuse_one_escrow_and_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            fixture = self.repair_escrow_fixture(root)
            with ThreadPoolExecutor(max_workers=8) as executor:
                rows = list(executor.map(
                    lambda _index: ledger_db.store_repair_escrow(root, **fixture),
                    range(16),
                ))
            self.assertEqual({row["handle_id"] for row in rows}, {rows[0]["handle_id"]})
            self.assertEqual({row["handle_digest"] for row in rows}, {rows[0]["handle_digest"]})
            with ledger_db.connection(root) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM repair_escrow").fetchone()[0], 1)

    @staticmethod
    def mark_as_exact_prior_canonical_v16(root: Path) -> None:
        """Rewrite only the canonical migration identity as the v16 release."""
        prior = ledger_db._prior_canonical_migration(16)
        with sqlite3.connect(root / ledger_db.DATABASE_NAME) as connection:
            connection.execute(
                "UPDATE schema_migrations SET version=?, name=?, checksum=?",
                (prior.version, prior.name, ledger_db._migration_checksum(prior)),
            )
            connection.execute("PRAGMA user_version = 16")
            connection.commit()
        ledger_db._forget_database_readiness(root)

    def test_fresh_ledger_records_only_current_canonical_schema_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            # This is the first normal server path after a Marketplace update:
            # no separate administrator command may be required to migrate a
            # developer's host-private project ledger before the first MCP
            # call.
            root = cortex.ledger_root({"project_root": str(project)})
            self.assertEqual(root, cortex.ledger_root_path({"project_root": str(project)}))
            first = ledger_db.migration_history(root)
            self.assertEqual(cortex.ledger_root({"project_root": str(project)}), root)
            second = ledger_db.migration_history(root)

            self.assertEqual(first, second)
            self.assertEqual(
                [item["version"] for item in first],
                [ledger_db.DATABASE_SCHEMA_VERSION],
            )
            self.assertEqual([item["name"] for item in first], ["canonical-current-ledger"])
            self.assertTrue((root / "cortex.db").is_file())
            self.assertFalse((project / ".codex" / "cortex" / "cortex.db").exists())
            self.assertFalse((root / "task-index.json").exists())

    def test_start_orchestration_quarantines_noncanonical_history_and_starts(self) -> None:
        """Start preflight recovers a 1..15 history without importing it."""
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            (project / "README.md").write_text("# fixture\n", encoding="utf-8")
            root = cortex.ledger_root_path({"project_root": str(project)}, create=True)
            root.mkdir(parents=True, mode=0o700)
            database = root / ledger_db.DATABASE_NAME
            ledger_db.ensure_database(root)
            (root / "tasks").mkdir(mode=0o700)
            (root / "lanes").mkdir(mode=0o700)
            (root / "tasks" / "precanonical-artifact.md").write_text("preserve task artifact", encoding="utf-8")
            (root / "lanes" / "precanonical-lane.json").write_text("{\"fixture\":true}", encoding="utf-8")
            old_key = ledger_db._governance_lifecycle_key_path(root)
            old_key.parent.mkdir(mode=0o700, exist_ok=True)
            old_key.write_bytes(b"o" * 32)
            old_key.chmod(0o600)
            with sqlite3.connect(database) as connection:
                connection.execute("DELETE FROM schema_migrations")
                connection.executemany(
                    "INSERT INTO schema_migrations VALUES(?,?,?,?)",
                    [
                        (version, f"historical-v{version}", "2026-01-01", "historical")
                        for version in sorted(ledger_db._PRECANONICAL_MIGRATION_NAMES)
                    ],
                )
                connection.execute("CREATE TABLE old_host_state(value TEXT NOT NULL)")
                connection.execute("INSERT INTO old_host_state VALUES('preserve')")
                connection.execute("PRAGMA user_version = 15")
                connection.commit()
            database.chmod(0o600)

            started = cortex.start_orchestration({
                "project_root": str(project),
                "task": {
                    "user_request": "Create a governed fixture result.",
                    "complexity": "C1",
                    "acceptance_criteria": ["The fixture result is produced."],
                    "verification": ["Inspect the fixture result."],
                },
                "waves": [{"phase": "discover", "workers": [{}]}],
            })

            self.assertTrue(started["ok"], started)
            self.assertEqual(started["outcome"], "ready_to_spawn")
            self.assertTrue((root / ledger_db.DATABASE_NAME).is_file())
            with sqlite3.connect(root / ledger_db.DATABASE_NAME) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    ledger_db.DATABASE_SCHEMA_VERSION,
                )
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='old_host_state'"
                ).fetchone())
            archives = list(root.glob("pre-canonical-ledger-*"))
            self.assertEqual(len(archives), 1)
            with sqlite3.connect(archives[0] / ledger_db.DATABASE_NAME) as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM old_host_state").fetchone()[0],
                    "preserve",
                )
            self.assertFalse((root / "tasks" / "precanonical-artifact.md").exists())
            self.assertFalse((root / "lanes" / "precanonical-lane.json").exists())
            self.assertEqual(
                (archives[0] / "tasks" / "precanonical-artifact.md").read_text(encoding="utf-8"),
                "preserve task artifact",
            )
            self.assertEqual(
                (archives[0] / "lanes" / "precanonical-lane.json").read_text(encoding="utf-8"),
                "{\"fixture\":true}",
            )
            self.assertEqual((archives[0] / "governance-lifecycle.key").read_bytes(), b"o" * 32)
            self.assertNotEqual(old_key.read_bytes(), b"o" * 32)

    def test_exact_v16_namespace_is_archived_once_before_fresh_v17_starts(self) -> None:
        """The hard namespace cutover archives, never adopts, canonical v16."""
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            (project / "README.md").write_text("# fixture\n", encoding="utf-8")
            root = cortex.ledger_root_path({"project_root": str(project)}, create=True)
            ledger_db.ensure_database(root)

            old_task_id = "legacy-v8-task"
            old_task_ref = "task-" + "8" * 64
            old_definition = {
                "schema": "cortex/v8",
                "task_id": old_task_id,
                "user_request": "Legacy fixture",
                "created_at": "2026-08-23T00:00:00+00:00",
            }
            old_state = {
                "schema": "cortex/v8",
                "task_id": old_task_id,
                "task_number": 1,
                "status": "active",
                "revision": 1,
                "updated_at": "2026-08-23T00:00:00+00:00",
            }
            old_artifact_dir = "tasks/0001-legacy-v8-task"
            ledger_db.create_task(root, old_definition, old_state, old_artifact_dir)
            artifact = root / old_artifact_dir / "legacy-marker.txt"
            artifact.parent.mkdir(parents=True, mode=0o700)
            artifact.write_text("preserved legacy namespace", encoding="utf-8")
            ledger_db.put_global(root, "operation_registry", {
                "schema": "cortex/orchestration/v5",
                "starts": {"legacy-start": {"task_ref": old_task_ref}},
                "tasks": {old_task_id: {"start": {"task_ref": old_task_ref}}},
                "updated_at": "2026-08-23T00:00:00+00:00",
            })
            old_key = ledger_db._governance_lifecycle_hmac_key(root, create=True)
            self.mark_as_exact_prior_canonical_v16(root)

            def start_request(label: str) -> dict[str, object]:
                return {
                    "project_root": str(project),
                    "task": {
                        "user_request": f"Create the {label} governed fixture result.",
                        "complexity": "C1",
                        "acceptance_criteria": [f"The {label} fixture result is produced."],
                        "verification": [f"Inspect the {label} fixture result."],
                    },
                    "waves": [{"phase": "discover", "workers": [{}]}],
                }

            started = cortex.start_orchestration(start_request("first"))

            self.assertTrue(started["ok"], started)
            self.assertEqual(started["outcome"], "ready_to_spawn")
            self.assertEqual([item.get("call") for item in started["dispatches"]], ["spawn_agent"])
            self.assertIsNone(ledger_db.load_task(root, old_task_id))
            self.assertIsNone(cortex._bound_project_root_for_task_ref(old_task_ref))
            active_registry = ledger_db.get_global(root, "operation_registry")
            self.assertEqual(active_registry["schema"], cortex.PUBLIC_ORCHESTRATION_SCHEMA)

            archives = list(root.glob("pre-canonical-ledger-*"))
            self.assertEqual(len(archives), 1)
            archive = archives[0]
            self.assertRegex(archive.name, r"^pre-canonical-ledger-[A-Za-z0-9.]+-[0-9a-f]{16}$")
            with sqlite3.connect(archive / ledger_db.DATABASE_NAME) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 16)
                archived_registry = json.loads(connection.execute(
                    "SELECT payload_json FROM global_documents WHERE name='operation_registry'"
                ).fetchone()[0])
                archived_task = connection.execute(
                    "SELECT definition_json,state_json FROM tasks WHERE task_id=?", (old_task_id,),
                ).fetchone()
            self.assertEqual(archived_registry["schema"], "cortex/orchestration/v5")
            self.assertEqual(json.loads(archived_task[0])["schema"], "cortex/v8")
            self.assertEqual(json.loads(archived_task[1])["schema"], "cortex/v8")
            self.assertEqual(
                (archive / old_artifact_dir / "legacy-marker.txt").read_text(encoding="utf-8"),
                "preserved legacy namespace",
            )
            self.assertEqual((archive / "governance-lifecycle.key").read_bytes(), old_key)

            second = cortex.start_orchestration(start_request("second"))
            self.assertTrue(second["ok"], second)
            self.assertEqual(second["outcome"], "ready_to_spawn")
            self.assertEqual([item.get("call") for item in second["dispatches"]], ["spawn_agent"])
            self.assertEqual(len(list(root.glob("pre-canonical-ledger-*"))), 1)

            before_reopen = ledger_db.migration_history(root)
            ledger_db.ensure_database(root)
            self.assertEqual(ledger_db.migration_history(root), before_reopen)
            self.assertEqual(before_reopen[0]["version"], 17)
            self.assertEqual(len(list(root.glob("pre-canonical-ledger-*"))), 1)

    def test_prior_canonical_v16_tampering_fails_closed_without_archive(self) -> None:
        """Unknown prior identities and malformed sidecar keys are never replaced."""
        cases = ("checksum", "name", "key")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / ".codex" / "cortex"
                ledger_db.ensure_database(root)
                ledger_db._governance_lifecycle_hmac_key(root, create=True)
                self.mark_as_exact_prior_canonical_v16(root)
                if case in {"checksum", "name"}:
                    with sqlite3.connect(root / ledger_db.DATABASE_NAME) as connection:
                        if case == "checksum":
                            connection.execute("UPDATE schema_migrations SET checksum='tampered'")
                        else:
                            connection.execute("UPDATE schema_migrations SET name='unknown-canonical-ledger'")
                        connection.commit()
                else:
                    ledger_db._governance_lifecycle_key_path(root).write_bytes(b"invalid")
                ledger_db._forget_database_readiness(root)
                database_before = (root / ledger_db.DATABASE_NAME).read_bytes()

                with self.assertRaisesRegex(ValueError, "unsupported pre-canonical ledger|host key is invalid"):
                    ledger_db.ensure_database(root)

                self.assertEqual((root / ledger_db.DATABASE_NAME).read_bytes(), database_before)
                self.assertEqual(list(root.glob("pre-canonical-ledger-*")), [])

    def test_single_canonical_v15_identity_is_unsupported_and_never_archived(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            current = ledger_db._migration_plan()[0]
            unsupported = ledger_db._Migration(15, current.name, current.statements)
            with sqlite3.connect(root / ledger_db.DATABASE_NAME) as connection:
                connection.execute(
                    "UPDATE schema_migrations SET version=?,name=?,checksum=?",
                    (unsupported.version, unsupported.name, ledger_db._migration_checksum(unsupported)),
                )
                connection.execute("PRAGMA user_version=15")
                connection.commit()
            ledger_db._forget_database_readiness(root)
            database_before = (root / ledger_db.DATABASE_NAME).read_bytes()
            with self.assertRaisesRegex(ValueError, "unsupported pre-canonical ledger"):
                ledger_db.ensure_database(root)
            self.assertEqual((root / ledger_db.DATABASE_NAME).read_bytes(), database_before)
            self.assertEqual(list(root.glob("pre-canonical-ledger-*")), [])

    def test_ready_ledger_uses_read_only_readiness_probe_without_migration_write_path(self) -> None:
        """A warm helper call must not serialize on bootstrap/migration work."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)

            # The first call filled the process-local readiness entry.  A
            # second call may read immutable migration facts, but must not
            # open BEGIN IMMEDIATE or sweep the schema.
            with mock.patch.object(
                ledger_db, "_connection", side_effect=AssertionError("warm readiness must not begin a write transaction")
            ), mock.patch.object(
                ledger_db, "_assert_migration_schema", side_effect=AssertionError("warm readiness must not sweep schema")
            ):
                ledger_db.ensure_database(root)

    def test_state_lock_is_bounded_and_busy_holder_metadata_excludes_owner_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            ready = multiprocessing.Event()
            release = multiprocessing.Event()
            holder = multiprocessing.Process(
                target=_hold_state_lock_in_process,
                args=(str(root), ready, release),
            )
            holder.start()
            try:
                self.assertTrue(ready.wait(5), "the child must acquire the state lock")
                with self.assertRaises(cortex.LedgerBusyError) as raised:
                    with cortex.state_lock(root, timeout_seconds=0.05, operation="regression_wait"):
                        pass
                error = raised.exception
                self.assertEqual(error.operation, "regression_wait")
                self.assertGreaterEqual(error.held_duration_ms, 0)
                self.assertIsInstance(error.holder, dict)
                self.assertEqual(error.holder.get("operation"), "regression_hold")
                self.assertEqual(error.holder.get("task_id"), "holder-task")
                self.assertNotIn("token", error.holder)
            finally:
                release.set()
                holder.join(5)
                if holder.is_alive():
                    holder.terminate()
                    holder.join(5)
            self.assertEqual(holder.exitcode, 0)

    def test_ready_ledger_cache_revalidates_history_user_version_and_schema_tampering(self) -> None:
        """Warm-cache hits must fail closed instead of trusting stale authority."""
        cases = (
            (
                "history",
                lambda connection: connection.execute(
                "UPDATE schema_migrations SET checksum='tampered' WHERE version=?",
                (ledger_db.DATABASE_SCHEMA_VERSION,),
                ),
                "unsupported pre-canonical ledger",
            ),
            (
                "user_version",
                lambda connection: connection.execute("PRAGMA user_version = 999"),
                "unsupported pre-canonical ledger",
            ),
            (
                "schema",
                lambda connection: connection.execute("DROP TABLE attempt_events"),
                "schema is inconsistent",
            ),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / ".codex" / "cortex"
                ledger_db.ensure_database(root)
                with sqlite3.connect(root / ledger_db.DATABASE_NAME) as connection:
                    mutate(connection)
                    connection.commit()

                with self.assertRaisesRegex(ValueError, expected):
                    ledger_db.ensure_database(root)

    def test_ready_ledger_cache_revalidates_database_replacement_before_reuse(self) -> None:
        """An inode change cannot silently inherit a previous ready-cache entry."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            database = root / ledger_db.DATABASE_NAME
            before = database.stat()
            replacement = root / "replacement-cortex.db"
            shutil.copyfile(database, replacement)
            replacement.chmod(0o600)
            replacement.replace(database)
            after = database.stat()
            self.assertNotEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))

            ledger_db.ensure_database(root)

    def test_pre_database_files_are_never_imported_or_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            task_dir = root / "tasks" / "0001-prior-state"
            task_dir.mkdir(parents=True)
            index = root / "task-index.json"
            index.write_text(json.dumps({"prior-task": {"number": 1, "directory": task_dir.name}}), encoding="utf-8")
            index.chmod(0o600)
            task_file = task_dir / "task.json"
            task_file.write_text(json.dumps({"task_id": "prior-task"}), encoding="utf-8")
            task_file.chmod(0o600)

            ledger_db.ensure_database(root)

            self.assertIsNone(ledger_db.load_task(root, "prior-task"))
            self.assertTrue(index.is_file())
            self.assertTrue(task_file.is_file())
            self.assertEqual(
                [item["name"] for item in ledger_db.migration_history(root)],
                [migration.name for migration in ledger_db._migration_plan()],
            )

    def test_invalid_migration_statement_rolls_back_schema_history_and_user_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            migration = ledger_db._migration_plan()[0]
            broken = ledger_db._Migration(
                migration.version, migration.name,
                migration.statements + ("CREATE TABLE invalid (",),
            )
            with mock.patch.object(ledger_db, "_migration_plan", return_value=(broken,)):
                with self.assertRaises(sqlite3.OperationalError):
                    ledger_db.ensure_database(root)
            with sqlite3.connect(root / "cortex.db") as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
                ).fetchone())

    @staticmethod
    def _migration_state(root: Path) -> tuple[int, tuple[tuple[object, ...], ...], tuple[tuple[str, str], ...]]:
        """Capture only durable migration facts, excluding SQLite implementation tables."""
        db_path = root / "cortex.db"
        if not db_path.exists():
            return (0, (), ())
        with sqlite3.connect(db_path) as connection:
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            has_history = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone() is not None
            history = tuple(connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            )) if has_history else ()
            schema = tuple(connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ))
        return (user_version, history, schema)

    def test_every_migration_statement_rolls_back_its_schema_history_and_user_version(self) -> None:
        """SQLite DDL must roll back even when a process stops between statements.

        Each statement of every immutable migration is fault-injected after
        all earlier statements of that migration have executed.  In
        particular, this exercises each canonical schema statement rather than just
        a synthetic malformed SQL tail.
        """
        migrations = ledger_db._migration_plan()
        self.assertEqual(len(migrations), 1)
        original_execute = ledger_db._execute_migration_statements
        for migration in migrations:
            executable_count = sum(bool(statement.strip()) for statement in migration.statements)
            for failed_statement in range(executable_count):
                with self.subTest(version=migration.version, statement=failed_statement):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory) / ".codex" / "cortex"
                        before = self._migration_state(root)

                        def fail_after_prefix(connection: sqlite3.Connection, statements: tuple[str, ...]) -> None:
                            if statements != migration.statements:
                                original_execute(connection, statements)
                                return
                            executed = 0
                            for statement in statements:
                                if not statement.strip():
                                    continue
                                if executed == failed_statement:
                                    raise sqlite3.OperationalError("deterministic injected migration interruption")
                                original_execute(connection, (statement,))
                                executed += 1
                            self.fail("migration fault injection did not run")

                        with mock.patch.object(ledger_db, "_execute_migration_statements", side_effect=fail_after_prefix):
                            with self.assertRaisesRegex(sqlite3.OperationalError, "deterministic injected"):
                                ledger_db.ensure_database(root)
                        self.assertEqual(self._migration_state(root), before)

    def test_migration_checksum_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            with sqlite3.connect(root / "cortex.db") as connection:
                connection.execute(
                    "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = ?",
                    (ledger_db.DATABASE_SCHEMA_VERSION,),
                )
                connection.commit()
            with self.assertRaisesRegex(ValueError, "unsupported pre-canonical ledger"):
                ledger_db.ensure_database(root)

    def test_executable_statement_change_is_detected_by_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            migrations = ledger_db._migration_plan()
            changed = ledger_db._Migration(
                ledger_db.DATABASE_SCHEMA_VERSION, migrations[0].name,
                migrations[0].statements + ("CREATE INDEX changed_migration_source ON artifacts(artifact_id)",),
            )
            with mock.patch.object(ledger_db, "_migration_plan", return_value=(changed,)):
                with self.assertRaisesRegex(ValueError, "unsupported pre-canonical ledger"):
                    ledger_db.ensure_database(root)

    def test_v7_content_mutation_is_detected_before_any_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            migrations = ledger_db._migration_plan()
            v7 = migrations[0]
            mutated = ledger_db._Migration(
                v7.version,
                v7.name,
                v7.statements[:-1] + (v7.statements[-1] + " /* source mutation */",),
            )
            with mock.patch.object(ledger_db, "_migration_plan", return_value=(mutated,)):
                with self.assertRaisesRegex(ValueError, "unsupported pre-canonical ledger"):
                    ledger_db.ensure_database(root)

    def test_database_with_removed_artifact_catalog_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            with sqlite3.connect(root / "cortex.db") as connection:
                connection.execute("DROP TABLE schema_migrations")
                connection.execute("CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY)")
                connection.execute("PRAGMA user_version = 0")
                connection.commit()
            ledger_db.ensure_database(root)
            with sqlite3.connect(root / ledger_db.DATABASE_NAME) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    ledger_db.DATABASE_SCHEMA_VERSION,
                )
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='artifacts'"
                ).fetchone())
            self.assertEqual(len(list(root.glob("pre-canonical-ledger-*"))), 1)

    def test_user_version_history_and_schema_disagreements_fail_closed(self) -> None:
        with self.subTest("user_version ahead of immutable history"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / ".codex" / "cortex"
                ledger_db.ensure_database(root)
                with sqlite3.connect(root / "cortex.db") as connection:
                    connection.execute("PRAGMA user_version = 999")
                    connection.commit()
                with self.assertRaisesRegex(ValueError, "unsupported pre-canonical ledger"):
                    ledger_db.ensure_database(root)
        with self.subTest("history asserts a migration whose table is absent"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / ".codex" / "cortex"
                ledger_db.ensure_database(root)
                with sqlite3.connect(root / "cortex.db") as connection:
                    connection.execute("DROP TABLE attempt_events")
                    connection.commit()
                with self.assertRaisesRegex(ValueError, "schema is inconsistent"):
                    ledger_db.ensure_database(root)
        with self.subTest("table name exists but immutable v1 layout was replaced"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / ".codex" / "cortex"
                ledger_db.ensure_database(root)
                with sqlite3.connect(root / "cortex.db") as connection:
                    connection.execute("PRAGMA foreign_keys = OFF")
                    connection.execute("DROP TABLE attempt_events")
                    connection.commit()
                with self.assertRaisesRegex(ValueError, "schema is inconsistent"):
                    ledger_db.ensure_database(root)

    def test_prior_name_checksum_is_rejected_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            with sqlite3.connect(root / "cortex.db") as connection:
                migration = ledger_db._migration_plan()[0]
                connection.execute(
                    "UPDATE schema_migrations SET checksum = ? WHERE version = ?",
                    (ledger_db._migration_checksum(migration.name), migration.version),
                )
                connection.commit()
            with self.assertRaisesRegex(ValueError, "unsupported pre-canonical ledger"):
                ledger_db.migration_history(root)

    def test_concurrent_first_boot_applies_migrations_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            with ThreadPoolExecutor(max_workers=8) as executor:
                histories = list(executor.map(lambda _: ledger_db.migration_history(root), range(8)))
            self.assertTrue(all(history == histories[0] for history in histories))
            self.assertEqual(
                [item["version"] for item in histories[0]],
                [ledger_db.DATABASE_SCHEMA_VERSION],
            )

    def test_two_processes_can_bootstrap_the_same_empty_ledger_once(self) -> None:
        if ledger_db.fcntl is None:
            self.skipTest("process-level advisory locks require fcntl")
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ready = context.Queue()
            start = context.Event()
            results = context.Queue()
            workers = [
                context.Process(target=_first_boot_in_process, args=(str(root), ready, start, results))
                for _ in range(2)
            ]
            for worker in workers:
                worker.start()
            for _ in workers:
                ready.get(timeout=10)
            start.set()
            for worker in workers:
                worker.join(timeout=20)
                self.assertFalse(worker.is_alive(), "concurrent first-boot worker did not finish")
                self.assertEqual(worker.exitcode, 0)
            try:
                observed = [results.get(timeout=5) for _ in workers]
            except queue.Empty as exc:  # pragma: no cover - assertion context below.
                self.fail(f"concurrent first boot produced no result: {exc}")
            self.assertEqual(
                observed,
                [("ok", [ledger_db.DATABASE_SCHEMA_VERSION])] * 2,
            )
            self.assertEqual(
                [item["version"] for item in ledger_db.migration_history(root)],
                [ledger_db.DATABASE_SCHEMA_VERSION],
            )

    def test_noncurrent_v6_state_is_quarantined_without_import(self) -> None:
        """An older ledger is archived and replaced with a fresh current one."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            root.mkdir(parents=True)
            with sqlite3.connect(root / "cortex.db") as connection:
                connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)")
                connection.execute("INSERT INTO schema_migrations VALUES(6,'historical-v6','2026-01-01','historical')")
                connection.execute("CREATE TABLE historical_v6_marker(value TEXT NOT NULL)")
                connection.execute("INSERT INTO historical_v6_marker VALUES('preserve')")
                connection.execute("PRAGMA user_version = 6")
                connection.commit()
            (root / "cortex.db").chmod(0o600)

            ledger_db.ensure_database(root)
            with sqlite3.connect(root / "cortex.db") as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], ledger_db.DATABASE_SCHEMA_VERSION)
                self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='historical_v6_marker'").fetchone())
            archives = list(root.glob("pre-canonical-ledger-*"))
            self.assertEqual(len(archives), 1)
            with sqlite3.connect(archives[0] / ledger_db.DATABASE_NAME) as connection:
                self.assertEqual(connection.execute("SELECT value FROM historical_v6_marker").fetchone()[0], "preserve")

    def test_noncurrent_v8_state_is_quarantined_without_import(self) -> None:
        """A live older ledger is not rewritten in place or imported."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            root.mkdir(parents=True)
            with sqlite3.connect(root / "cortex.db") as connection:
                connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)")
                connection.execute("INSERT INTO schema_migrations VALUES(8,'revision-aware-orchestration','2026-01-01','historical')")
                connection.execute("CREATE TABLE historical_v8_marker(task_id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO historical_v8_marker VALUES('active-v8-task')")
                connection.execute("PRAGMA user_version = 8")
                connection.commit()
            (root / "cortex.db").chmod(0o600)

            ledger_db.ensure_database(root)
            with sqlite3.connect(root / "cortex.db") as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], ledger_db.DATABASE_SCHEMA_VERSION)
                self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='historical_v8_marker'").fetchone())
            archives = list(root.glob("pre-canonical-ledger-*"))
            self.assertEqual(len(archives), 1)
            with sqlite3.connect(archives[0] / ledger_db.DATABASE_NAME) as connection:
                self.assertEqual(connection.execute("SELECT task_id FROM historical_v8_marker").fetchone()[0], "active-v8-task")

    def test_projection_ack_requires_the_current_nonexpired_lease_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            self.create_task(root, "projection-task")
            digest = "a" * 64
            job = ledger_db.enqueue_projection_job(
                root,
                task_id="projection-task",
                projection_type="briefing",
                expected_digest=digest,
                export_path="briefings/dispatch.md",
                artifact_id="artifact-projection",
            )
            claimed = ledger_db.claim_projection_jobs(root, "worker-a")
            self.assertEqual([item["projection_key"] for item in claimed], [job["projection_key"]])
            with self.assertRaisesRegex(ValueError, "non-expired lease"):
                ledger_db.ack_projection_job(
                    root, job["projection_key"], expected_digest=digest, lease_owner="worker-b",
                )
            with sqlite3.connect(root / "cortex.db") as connection:
                connection.execute(
                    "UPDATE projection_jobs SET lease_expires_at=? WHERE projection_key=?",
                    ("2000-01-01T00:00:00+00:00", job["projection_key"]),
                )
                connection.commit()
            with self.assertRaisesRegex(ValueError, "non-expired lease"):
                ledger_db.ack_projection_job(
                    root, job["projection_key"], expected_digest=digest, lease_owner="worker-a",
                )
            reclaimed = ledger_db.claim_projection_jobs(root, "worker-b")
            self.assertEqual([item["projection_key"] for item in reclaimed], [job["projection_key"]])
            acknowledged = ledger_db.ack_projection_job(
                root, job["projection_key"], expected_digest=digest, lease_owner="worker-b",
            )
            self.assertEqual(acknowledged["status"], "ready")

    def test_artifact_catalog_chunks_large_text_without_field_limit_and_signs_cursors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            self.create_task(root)
            content = ("# Harvest section\n\n" + ("verified behavior line\n" * 4000)).strip() + "\n"
            artifact = ledger_db.put_artifact(
                root, "artifact-task", kind="canonical_markdown", title="artifacts/markdown/item-0001.md",
                mime_type="text/markdown", content=content, export_path="artifacts/markdown/item-0001.md",
            )
            self.assertGreater(artifact["byte_size"], 64 * 1024)
            self.assertGreater(artifact["chunk_count"], 2)
            first = ledger_db.read_artifact_range(
                root, "artifact-task", artifact["artifact_ref"], max_bytes=8192,
            )
            self.assertFalse(first["complete"])
            self.assertLessEqual(first["returned_bytes"], 8192)
            self.assertTrue(first["content_part"].startswith("# Harvest section"))
            cursor = ledger_db.encode_artifact_cursor(root, {
                "type": "artifact_read", "task_id": "artifact-task", "artifact_ref": artifact["artifact_ref"],
                "digest_sha256": artifact["digest_sha256"], "byte_offset": first["next_byte_offset"], "audience": "coordinator",
            })
            decoded = ledger_db.decode_artifact_cursor(root, cursor)
            self.assertEqual(decoded["byte_offset"], first["next_byte_offset"])
            tampered_cursor = ("A" if cursor[0] != "A" else "B") + cursor[1:]
            with self.assertRaisesRegex(ValueError, "signature"):
                ledger_db.decode_artifact_cursor(root, tampered_cursor)
            listed, next_offset = ledger_db.list_artifacts(root, "artifact-task", kind="canonical_markdown", page_size=1)
            self.assertEqual(next_offset, None)
            self.assertEqual(listed, [artifact])
            with sqlite3.connect(root / "cortex.db") as connection:
                names = {row[1] for row in connection.execute("PRAGMA index_list('logical_artifacts')")}
            self.assertIn("logical_artifacts_task_kind_created_idx", names)
            self.assertIn("logical_artifacts_task_created_idx", names)

    def test_artifact_transport_makes_progress_for_a_multibyte_character(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            self.create_task(root)
            artifact = ledger_db.put_artifact(
                root, "artifact-task", kind="canonical_markdown", title="artifacts/markdown/unicode.md",
                mime_type="text/markdown", content="😀 verified", export_path="artifacts/markdown/unicode.md",
            )

            first = ledger_db.read_artifact_range(
                root, "artifact-task", artifact["artifact_ref"], max_bytes=1,
            )

            self.assertEqual(first["content_part"], "😀")
            self.assertEqual(first["returned_bytes"], len("😀".encode("utf-8")))
            self.assertEqual(first["next_byte_offset"], len("😀".encode("utf-8")))

    def test_artifact_transport_honors_optional_utf8_pages_and_cursor_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            self.create_task(root)
            # A caller-selected page may end at a multi-byte scalar. The
            # server preserves that complete scalar rather than enforcing a
            # hidden transport cap or emitting replacement text.
            content = "a" * 31 + "😀tail"
            artifact = ledger_db.put_artifact(
                root, "artifact-task", kind="canonical_markdown", title="artifacts/markdown/boundary.md",
                mime_type="text/markdown", content=content, export_path="artifacts/markdown/boundary.md",
            )
            first = ledger_db.read_artifact_range(
                root, "artifact-task", artifact["artifact_ref"], max_bytes=32,
            )
            self.assertEqual(first["returned_bytes"], 31)
            self.assertFalse(first["complete"])
            self.assertEqual(first["next_byte_offset"], first["returned_bytes"])
            self.assertNotIn("\ufffd", first["content_part"])

            offsets = [first["byte_offset"]]
            parts = [first["content_part"]]
            offset = first["next_byte_offset"]
            while offset is not None:
                part = ledger_db.read_artifact_range(
                    root, "artifact-task", artifact["artifact_ref"], byte_offset=offset, max_bytes=1,
                )
                self.assertGreaterEqual(part["returned_bytes"], ledger_db.ARTIFACT_TRANSPORT_MIN_BYTES)
                self.assertGreater(part["returned_bytes"], 0)
                self.assertEqual(part["byte_offset"], offset)
                self.assertNotIn("\ufffd", part["content_part"])
                offsets.append(offset)
                parts.append(part["content_part"])
                offset = part["next_byte_offset"]
            self.assertEqual("".join(parts), content)
            self.assertEqual(offsets, sorted(offsets))
            self.assertEqual(len(offsets), len(set(offsets)))

            # A signed cursor produced by an older reader may point inside a
            # UTF-8 scalar.  The server repairs that cursor at the scalar
            # boundary instead of turning a resumable worker into a blocked
            # replacement dispatch.
            repaired = ledger_db.read_artifact_range(
                root, "artifact-task", artifact["artifact_ref"],
                byte_offset=32,
            )
            self.assertTrue(repaired["cursor_normalized"])
            self.assertEqual(repaired["requested_byte_offset"], 32)
            self.assertEqual(repaired["byte_offset"], 31)
            self.assertEqual(repaired["content_part"], "😀tail")

    def test_artifact_transport_binary_eof_and_malformed_cursor_do_not_create_or_mutate_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            # Syntactically valid but untrusted cursor input cannot bootstrap
            # a SQLite ledger merely by being rejected.
            with self.assertRaisesRegex(ValueError, "cursor"):
                ledger_db.decode_artifact_cursor(root, "e30.AAAA")
            self.assertFalse((root / "cortex.db").exists())
            with self.assertRaisesRegex(ValueError, "cursor"):
                ledger_db.decode_artifact_cursor(root, "e30." + "A" * 43)
            self.assertFalse((root / "cortex.db").exists())
            with self.assertRaisesRegex(ValueError, "safe transport length"):
                ledger_db.encode_artifact_cursor(root, {"padding": "x" * 5000})
            self.assertFalse((root / "cortex.db").exists())

            ledger_db.ensure_database(root)
            self.create_task(root)
            artifact = ledger_db.put_artifact(
                root, "artifact-task", kind="canonical_binary", title="artifacts/binary/empty.bin",
                mime_type="application/octet-stream", content=b"", export_path="artifacts/binary/empty.bin",
            )
            eof = ledger_db.read_artifact_range(root, "artifact-task", artifact["artifact_ref"], max_bytes=1)
            self.assertTrue(eof["complete"])
            self.assertIsNone(eof["next_byte_offset"])
            self.assertEqual(eof["encoding"], "base64")
            self.assertEqual(eof["content_base64"], "")

    def test_normalized_artifacts_share_one_blob_but_keep_logical_identity_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            self.create_task(root)
            content = "# Same body\n\nThe bytes are intentionally identical.\n"
            first = ledger_db.put_artifact(
                root, "artifact-task", kind="canonical_markdown", title="artifacts/first.md",
                mime_type="text/markdown", content=content, export_path="artifacts/first.md",
                created_at="2026-01-01T00:00:00+00:00",
            )
            second = ledger_db.put_artifact(
                root, "artifact-task", kind="canonical_markdown", title="artifacts/second.md",
                mime_type="text/markdown", content=content, export_path="artifacts/second.md",
                created_at="2026-01-01T00:00:01+00:00",
            )

            self.assertNotEqual(first["artifact_ref"], second["artifact_ref"])
            self.assertEqual(first["digest_sha256"], second["digest_sha256"])
            self.assertEqual(
                ledger_db.get_artifact_blob_metadata(root, "artifact-task", first["artifact_ref"]),
                ledger_db.get_artifact_blob_metadata(root, "artifact-task", second["artifact_ref"]),
            )
            self.assertEqual(ledger_db.read_artifact_content(root, "artifact-task", first["artifact_ref"]), content)
            self.assertEqual(ledger_db.read_artifact_content(root, "artifact-task", second["artifact_ref"]), content)

            ledger_db.register_artifact_export(root, "artifact-task", first["artifact_ref"], "artifacts/archive/first.md")
            self.assertEqual(
                [item["export_path"] for item in ledger_db.list_artifact_exports(root, "artifact-task", first["artifact_ref"])],
                ["artifacts/archive/first.md", "artifacts/first.md"],
            )
            self.assertEqual(
                ledger_db.get_artifact_for_export_path(root, "artifact-task", "artifacts/archive/first.md")["artifact_ref"],
                first["artifact_ref"],
            )
            with sqlite3.connect(root / "cortex.db") as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM logical_artifacts").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifact_blobs").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifact_blob_chunks").fetchone()[0], 1)

    def test_fresh_schema_does_not_create_removed_artifact_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            with sqlite3.connect(root / "cortex.db") as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("artifacts", tables)
            self.assertNotIn("artifact_chunks", tables)
            self.assertIn("logical_artifacts", tables)
            self.assertIn("artifact_blobs", tables)

    def test_normalization_schema_loss_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            with sqlite3.connect(root / "cortex.db") as connection:
                connection.execute("DROP TABLE attempt_events")
                connection.commit()
            with self.assertRaisesRegex(ValueError, "schema is inconsistent"):
                ledger_db.ensure_database(root)

    def test_hook_snapshot_read_helpers_reuse_supplied_read_only_connection(self) -> None:
        """All hook readers use one bounded connection and cannot write through it."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            state = {
                "schema": cortex.SCHEMA, "task_id": "snapshot-task", "task_number": 1,
                "status": "active", "revision": 1, "attempts": [{
                    "attempt_id": "attempt-1", "status": "awaiting_host_spawn",
                    "spawn_request": {"task_name": "qa-engineer"},
                }],
            }
            definition = {"schema": cortex.SCHEMA, "task_id": "snapshot-task", "created_at": "now"}
            ledger_db.ensure_database(root)
            ledger_db.create_task(root, definition, state, "tasks/0001-snapshot-task")
            ledger_db.put_global(root, "operation_registry", {
                "tasks": {"snapshot-task": {"start": {"task_ref": "public-snapshot-ref"}}},
            })
            fingerprint = "a" * 64
            ledger_db.record_tool_observation(
                root, task_id="snapshot-task", attempt_id="attempt-1", context_epoch=3,
                fingerprint=fingerprint, tool_name="read", normalized_arguments="{}",
                workspace_generation="workspace-1", result_digest=None, coverage="full", status="success",
            )
            with ledger_db.hook_snapshot(root, timeout_ms=100) as snapshot:
                self.assertIsNotNone(snapshot)
                assert snapshot is not None
                with mock.patch.object(ledger_db, "_connection", side_effect=AssertionError("opened another connection")):
                    self.assertEqual(ledger_db.hook_snapshot_task_context(snapshot, "snapshot-task")[3], "tasks/0001-snapshot-task")
                    self.assertEqual(ledger_db.hook_snapshot_artifact_directory(snapshot, "snapshot-task"), "tasks/0001-snapshot-task")
                    self.assertEqual(ledger_db.hook_snapshot_task_ref(snapshot, "snapshot-task"), "public-snapshot-ref")
                    self.assertEqual(ledger_db.hook_snapshot_operation_registry(snapshot)["tasks"]["snapshot-task"]["start"]["task_ref"], "public-snapshot-ref")
                    self.assertEqual(ledger_db.hook_snapshot_pending_subagent(snapshot, "qa-engineer"), ["snapshot-task"])
                    self.assertEqual(ledger_db.hook_snapshot_tool_context_epoch(snapshot, "snapshot-task"), 0)
                    self.assertTrue(ledger_db.hook_snapshot_find_successful_tool_observation(
                        snapshot, "snapshot-task", "attempt-1", 3, fingerprint, "workspace-1",
                    ))
                with self.assertRaises(sqlite3.OperationalError):
                    snapshot.execute("DELETE FROM tasks WHERE task_id = 'snapshot-task'")

    def test_unicode_large_mutable_documents_round_trip_exactly(self) -> None:
        """Mutable documents have no backend content-size admission quota."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            self.create_task(root, "bounded-document-task")
            ledger_db.put_global(root, "bounded-global", {"value": "before"})
            ledger_db.put_task_document(root, "bounded-document-task", "bounded-task", {"value": "before"})
            oversized = "🙂" * 140_000
            ledger_db.put_global(root, "bounded-global", {"unicode": oversized})
            ledger_db.put_task_document(root, "bounded-document-task", "bounded-task", {"unicode": oversized})
            self.assertEqual(ledger_db.get_global(root, "bounded-global"), {"unicode": oversized})
            self.assertEqual(ledger_db.get_task_document(root, "bounded-document-task", "bounded-task"), {"unicode": oversized})

    def test_oversized_observation_metadata_round_trips_without_a_body_quota(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            ledger_db.ensure_database(root)
            self.create_task(root, "bounded-observation-task")
            tool_name = "Read-" + "🙂" * 2_000
            workspace_generation = "generation-" + "🙂" * 4_000
            normalized_arguments = json.dumps({"payload": "🙂" * 10_000}, ensure_ascii=False)
            ledger_db.record_tool_observation(
                root,
                task_id="bounded-observation-task",
                attempt_id="attempt-1",
                context_epoch=0,
                fingerprint="f" * 64,
                tool_name=tool_name,
                normalized_arguments=normalized_arguments,
                workspace_generation=workspace_generation,
                result_digest=None,
                coverage="full",
                status="success",
            )
            with ledger_db._connection(root) as connection:
                row = connection.execute(
                    "SELECT tool_name, normalized_arguments, workspace_generation FROM tool_observations WHERE task_id=? AND attempt_id=?",
                    ("bounded-observation-task", "attempt-1"),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["tool_name"], tool_name)
                self.assertEqual(row["normalized_arguments"], normalized_arguments)
                self.assertEqual(row["workspace_generation"], workspace_generation)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
