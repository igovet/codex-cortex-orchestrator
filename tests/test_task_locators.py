"""Derived compact-task locator safety and first-call topology regressions."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from multiprocessing import get_context
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.v12_store import V12Store, V12StoreError, SCHEMA_VERSION, MIGRATION_NAME
from cortex_runtime.v12_maintenance import health


def _resolve_first_call(home: str, compact_ref: str, ready: object, start: object, results: object) -> None:
    """One fresh-process first compact-task resolution under shared state."""
    os.environ["CODEX_HOME"] = home
    try:
        ready.put(True)
        start.wait(15)
        store, identifier = V12Store.for_task_ref(compact_ref)
        results.put(("ok", store.project_hash, identifier))
    except V12StoreError as exc:
        results.put(("error", exc.code, None))
    except BaseException as exc:
        results.put(("exception", type(exc).__name__, None))


class TaskLocatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cortex-task-locator-")
        self.home = Path(self.temp.name) / "state"
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.previous_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.home)
        self.store = V12Store(self.project)

    def tearDown(self) -> None:
        if self.previous_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.previous_home
        self.temp.cleanup()

    def _create(self, suffix: str = "one") -> dict:
        result, replayed = self.store.create_task(
            objective=f"Task locator {suffix}.", user_request_original=f"Task locator {suffix}.", user_language="en",
            outcome_contracts=[{'requirement': 'Route exactly one compact task reference.', 'acceptance': ['Verify the canonical task row.'], 'verification': ['Resolve the compact task reference.'], 'constraints': []}],
            requirements=["Route exactly one compact task reference."], constraints=["Locator is derived only."],
            acceptance_criteria=["Verify the canonical task row."], verification_plan=["Resolve the compact task reference."],
            context={}, idempotency_key=f"task-locator-{suffix}",
        )
        self.assertFalse(replayed)
        return result["task"]

    def test_normal_lookup_uses_one_indexed_shard_without_recovery_scan(self) -> None:
        task = self._create()
        with patch.object(V12Store, "_recover_task_locator_matches", side_effect=AssertionError("unexpected shard scan")):
            resolved, canonical = V12Store.for_task_ref(task["task_ref"])
        self.assertEqual(canonical, task["task_id"])
        self.assertEqual(resolved.project_hash, self.store.project_hash)

    def test_missing_stale_and_tampered_locators_recover_only_after_canonical_proof(self) -> None:
        task = self._create()
        locator = self.store._task_locator_path
        for state in ("missing", "tampered", "wrong"):
            with self.subTest(state=state):
                if state == "missing":
                    locator.unlink()
                elif state == "tampered":
                    locator.write_bytes(b"not sqlite")
                else:
                    with sqlite3.connect(locator) as connection:
                        connection.execute("UPDATE task_locators SET fingerprint='wrong' WHERE task_id=?", (task["task_id"],))
                        connection.commit()
                resolved, canonical = V12Store.for_task_ref(task["task_ref"])
                self.assertEqual((resolved.project_hash, canonical), (self.store.project_hash, task["task_id"]))
                # The successful recovery must repair an independently usable
                # sidecar rather than leave scans on the normal hot path.
                with patch.object(V12Store, "_recover_task_locator_matches", side_effect=AssertionError("unrepaired")):
                    self.assertEqual(V12Store.for_task_ref(task["task_ref"])[1], task["task_id"])

    def test_wrong_project_locator_and_cross_project_reference_fail_closed(self) -> None:
        task = self._create()
        other = Path(self.temp.name) / "other"
        other.mkdir()
        other_store = V12Store(other)
        other_task_result, _ = other_store.create_task(
            objective="Other task.", user_request_original="Other task.", user_language="en",
            outcome_contracts=[{'requirement': 'Keep project identity.', 'acceptance': ['Reject wrong locator.'], 'verification': ['Verify canonical shard.'], 'constraints': []}],
            requirements=["Keep project identity."], constraints=["No cross-project routing."],
            acceptance_criteria=["Reject wrong locator."], verification_plan=["Verify canonical shard."], context={},
        )
        other_task = other_task_result["task"]
        with sqlite3.connect(self.store._task_locator_path) as connection:
            connection.execute("UPDATE task_locators SET project_hash=?,task_id=? WHERE task_id=?", (other_store.project_hash, other_task["task_id"], task["task_id"]))
            connection.commit()
        # The stale map is not authority: recovery returns the original task.
        self.assertEqual(V12Store.for_task_ref(task["task_ref"])[1], task["task_id"])
        self.assertNotEqual(task["task_ref"], other_task["task_ref"])

    def test_current_publication_is_transactional_and_maintenance_shape_is_present(self) -> None:
        task = self._create()
        with sqlite3.connect(self.store.database_path) as connection:
            row = connection.execute(
                "SELECT project_hash,suffix,fingerprint FROM task_locator_publications WHERE task_id=?", (task["task_id"],)
            ).fetchone()
            self.assertEqual(row[0], self.store.project_hash)
            self.assertEqual(row[1], task["task_id"][-12:])
            self.assertEqual(row[2], V12Store._task_locator_fingerprint(task["task_id"]))
            self.assertEqual(connection.execute("SELECT version,name FROM schema_migrations").fetchall(), [(SCHEMA_VERSION, MIGRATION_NAME)])
        self.assertTrue(health(task_id=task["task_id"])["healthy"])

    def test_eighty_shards_one_hundred_sixty_first_calls_do_not_scan_or_busy(self) -> None:
        """The installed-candidate topology, exercised against source storage.

        All 160 fresh processes share one state root while each pair resolves
        one of 80 independent project shards.  The normal resolver is patched
        nowhere: success proves its indexed path did not consume every shard's
        per-shard admission lock before opening the target.
        """
        pair_count = 80
        references: list[tuple[str, str, str]] = []
        for index in range(pair_count):
            project = Path(self.temp.name) / f"stress-{index}"
            project.mkdir()
            store = V12Store(project)
            created, _ = store.create_task(
                objective=f"Stress {index}.", user_request_original=f"Stress {index}.", user_language="en",
                outcome_contracts=[{'requirement': 'Resolve one target shard.', 'acceptance': ['No storage busy.'], 'verification': ['Resolve concurrently.'], 'constraints': []}],
                requirements=["Resolve one target shard."], constraints=["No all-shard scan."],
                acceptance_criteria=["No storage busy."], verification_plan=["Resolve concurrently."], context={},
            )
            task = created["task"]
            references.extend([(task["task_ref"], task["task_id"], store.project_hash)] * 2)
        context = get_context("fork")
        ready, start, results = context.Queue(), context.Event(), context.Queue()
        workers = [context.Process(target=_resolve_first_call, args=(str(self.home), reference, ready, start, results)) for reference, _identifier, _shard in references]
        for worker in workers:
            worker.start()
        self.assertEqual([ready.get(timeout=30) for _ in workers], [True] * len(workers))
        start.set()
        observed = [results.get(timeout=30) for _ in workers]
        for worker in workers:
            worker.join(timeout=30)
            self.assertEqual(worker.exitcode, 0)
        self.assertTrue(all(result[0] == "ok" for result in observed), observed)
        self.assertEqual(sorted((result[1], result[2]) for result in observed), sorted((shard, identifier) for _ref, identifier, shard in references))
