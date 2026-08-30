"""Persistence invariants for the v21 worker bootstrap capability ledger."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.v12_store import V12Store, V12StoreError  # noqa: E402


class V21WorkerCapabilityStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="cortex-v21-capability-")
        root = Path(self.tmp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.home = root / "home"
        self.home.mkdir()
        self.env = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}, clear=False)
        self.env.start()
        self.store = V12Store(self.project)
        task, _ = self.store.create_task(
            objective="Test worker bootstrap persistence.", user_request_original="Test worker bootstrap persistence.",
            user_language="en", requirements=["Persist capability."], constraints=["No reset."],
            acceptance_criteria=["Exact replay is stable."], verification_plan=["Read the capability."], context={},
        )
        self.task_id = task["task"]["task_id"]
        delegation, _ = self.store.create_delegation(
            task_id=self.task_id, objective="Test worker bootstrap persistence.", role="planner",
            profile_name="planner", scope="one assignment", instructions="Perform the test.",
            model="gpt-5.6-luna", reasoning_effort="high",
        )
        self.assignment = delegation["delegation"]
        self.args = {
            "task_id": self.task_id, "assignment_id": self.assignment["delegation_id"], "contract_revision": 1,
            "build_digest": "sha256:" + "1" * 64, "candidate_digest": "sha256:" + "2" * 64,
            "source_digest": "sha256:" + "3" * 64, "catalogue_digest": "sha256:" + "4" * 64,
            "dispatch_digest": self.assignment["dispatch_correlation_digest"],
        }

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_mint_and_consume_are_exactly_idempotent(self) -> None:
        first = self.store.mint_worker_bootstrap(**self.args)
        replay = self.store.mint_worker_bootstrap(**self.args)
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["capability"], replay["capability"])
        consumed = self.store.consume_worker_bootstrap(capability=first["capability"], **self.args)
        consumed_replay = self.store.consume_worker_bootstrap(capability=first["capability"], **self.args)
        self.assertFalse(consumed["replayed"])
        self.assertTrue(consumed_replay["replayed"])
        self.assertEqual(consumed["continuation"], consumed_replay["continuation"])
        self.assertEqual(
            self.store.validate_worker_continuation(
                continuation=consumed["continuation"], task_id=self.task_id,
                assignment_id=self.assignment["delegation_id"], contract_revision=1,
            )["state"], "consumed",
        )

    def test_assignment_locator_resolves_private_lease_once_under_concurrency(self) -> None:
        """Workers never receive a bearer capability; the server owns replay."""
        self.store.mint_worker_bootstrap(**self.args)

        def consume() -> dict:
            return self.store.consume_worker_bootstrap_for_assignment(
                task_id=self.task_id,
                assignment_id=self.assignment["delegation_id"],
                contract_revision=1,
                build_digest=self.args["build_digest"],
                candidate_digest=self.args["candidate_digest"],
                source_digest=self.args["source_digest"],
                catalogue_digest=self.args["catalogue_digest"],
                dispatch_digest=self.args["dispatch_digest"],
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: consume(), range(8)))
        self.assertEqual(sum(not result["replayed"] for result in results), 1)
        self.assertEqual({result["continuation"] for result in results}, {results[0]["continuation"]})
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT state,COUNT(*) AS count FROM worker_capabilities WHERE assignment_id=? GROUP BY state",
                (self.assignment["delegation_id"],),
            ).fetchall()
        self.assertEqual([(str(item["state"]), int(item["count"])) for item in rows], [("consumed", 1)])

    def test_changed_provenance_conflicts_and_wrong_scope_is_stale(self) -> None:
        first = self.store.mint_worker_bootstrap(**self.args)
        changed = dict(self.args, source_digest="sha256:" + "9" * 64)
        with self.assertRaisesRegex(V12StoreError, "conflicts"):
            self.store.mint_worker_bootstrap(**changed)
        with self.assertRaisesRegex(V12StoreError, "stale"):
            self.store.consume_worker_bootstrap(capability=first["capability"], **changed)

    def test_migration_is_forward_only_and_table_is_present(self) -> None:
        with self.store._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 23)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(worker_capabilities)")}
        self.assertIn("continuation_ref", columns)
        self.assertIn("dispatch_digest", columns)
        self.assertIn("lease_expires_at", columns)

    def test_active_dispatch_lease_rejects_parent_replacement_before_worker_consumes(self) -> None:
        capability = self.store.mint_worker_bootstrap(**self.args)
        self.assertEqual(capability["state"], "minted")
        with self.assertRaisesRegex(V12StoreError, "active dispatch lease"):
            self.store.create_delegation(
                task_id=self.task_id, parent_delegation_id=self.assignment["delegation_id"],
                objective="Replacement attempted before worker bootstrap.", role="worker",
                profile_name="general", scope="Inherited scope.", instructions="Continue the work.",
                model="gpt-5.6-luna", reasoning_effort="high", idempotency_key="blocked-parent-replacement",
            )
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT state FROM worker_capabilities WHERE capability_ref=?", (capability["capability"],)
            ).fetchone()
            self.assertEqual(row["state"], "minted")
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM delegations WHERE task_id=?", (self.task_id,)).fetchone()[0], 1
            )

    def test_consumed_dispatch_lease_allows_explicit_parent_replacement(self) -> None:
        capability = self.store.mint_worker_bootstrap(**self.args)
        self.store.consume_worker_bootstrap(capability=capability["capability"], **self.args)
        replacement, replayed = self.store.create_delegation(
            task_id=self.task_id, parent_delegation_id=self.assignment["delegation_id"],
            objective="Replacement after worker consumption.", role="worker",
            profile_name="general", scope="Inherited scope.", instructions="Continue the work.",
            model="gpt-5.6-luna", reasoning_effort="high", idempotency_key="allowed-parent-replacement",
        )
        self.assertFalse(replayed)
        self.assertNotEqual(replacement["delegation"]["delegation_id"], self.assignment["delegation_id"])

    def test_expired_dispatch_lease_is_reconciled_stale_before_replacement(self) -> None:
        capability = self.store.mint_worker_bootstrap(**self.args)
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE worker_capabilities SET lease_expires_at=? WHERE capability_ref=?",
                ("2000-01-01T00:00:00+00:00", capability["capability"]),
            )
        replacement, replayed = self.store.create_delegation(
            task_id=self.task_id, parent_delegation_id=self.assignment["delegation_id"],
            objective="Replacement after bounded lease expiry.", role="worker",
            profile_name="general", scope="Inherited scope.", instructions="Continue the work.",
            model="gpt-5.6-luna", reasoning_effort="high", idempotency_key="expired-parent-replacement",
        )
        self.assertFalse(replayed)
        self.assertNotEqual(replacement["delegation"]["delegation_id"], self.assignment["delegation_id"])
        with self.store._connection() as connection:
            state = connection.execute(
                "SELECT state FROM worker_capabilities WHERE capability_ref=?", (capability["capability"],)
            ).fetchone()["state"]
        self.assertEqual(state, "stale")


if __name__ == "__main__":
    unittest.main()
