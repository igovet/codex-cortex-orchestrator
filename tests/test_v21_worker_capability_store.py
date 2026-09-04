"""Persistence invariants for current typed worker bootstrap capabilities."""
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
            outcome_contracts=[{'requirement': 'Persist capability.', 'acceptance': ['Exact replay is stable.'], 'verification': ['Read the capability.'], 'constraints': []}],
            user_language="en", requirements=["Persist capability."], constraints=["No reset."],
            acceptance_criteria=["Exact replay is stable."], verification_plan=["Read the capability."], context={},
        )
        self.task_id = task["task"]["task_id"]
        from cortex_runtime.domain_api import assess_governance
        from test_domain_public_api_contract import PROVENANCE
        assess_governance(task_ref=task["task"]["task_ref"], mode="minimal")
        graph_id = self.store._read(lambda c: c.execute("SELECT graph_id FROM execution_graphs WHERE task_id=?", (self.task_id,)).fetchone()[0])
        admission = self.store.node_admission_snapshot(graph_id=graph_id)
        self.dispatch_args = dict(task_id=self.task_id, graph_id=graph_id, graph_digest=admission["digest"],
            admission=admission, node_keys=["baseline"], profile_name="explorer",
            model="gpt-5.6-luna", reasoning_effort="high", bootstrap_provenance=PROVENANCE)
        delegation, replayed = self.store.open_node_assignment(**self.dispatch_args)
        self.assertFalse(replayed)
        self.assignment = delegation["delegation"]
        self.args = {
            "task_id": self.task_id, "assignment_id": self.assignment["delegation_id"], "contract_revision": 1,
            **PROVENANCE, "dispatch_digest": self.assignment["dispatch_correlation_digest"],
        }

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_mint_and_consume_are_exactly_idempotent(self) -> None:
        first = self.store.mint_worker_bootstrap(**self.args)
        replay = self.store.mint_worker_bootstrap(**self.args)
        self.assertTrue(first["replayed"])
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

    def test_consumed_publication_has_no_locator_recovery_surface(self) -> None:
        minted = self.store.mint_worker_bootstrap(**self.args)
        consumed = self.store.consume_worker_bootstrap(
            capability=minted["capability"], **self.args,
        )
        self.assertTrue(consumed["continuation"])
        self.assertFalse(
            hasattr(self.store, "recover_consumed_worker_publication")
        )

    def test_assignment_page_receipts_reconcile_exactly_and_reject_skips(self) -> None:
        minted = self.store.mint_worker_bootstrap(**self.args)
        self.store.consume_worker_bootstrap(
            capability=minted["capability"], **self.args,
        )
        common = {
            "task_id": self.task_id,
            "assignment_id": self.assignment["delegation_id"],
            "snapshot_digest": "sha256:" + "5" * 64,
            "phase": "authority",
            "page_digest": "sha256:" + "6" * 64,
            "returned_content_bytes": 1234,
            "has_more": True,
        }
        first = self.store.record_assignment_page_receipt(
            private_position=0, **common,
        )
        replay = self.store.record_assignment_page_receipt(
            private_position=0, **common,
        )
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["created_sequence"], replay["created_sequence"])
        with self.assertRaises(V12StoreError) as skipped:
            self.store.record_assignment_page_receipt(
                private_position=2,
                **(common | {"page_digest": "sha256:" + "7" * 64}),
            )
        self.assertEqual(skipped.exception.code, "report_cursor_invalid")
        terminal = self.store.record_assignment_page_receipt(
            private_position=1,
            **(
                common
                | {
                    "phase": "evidence",
                    "page_digest": "sha256:" + "8" * 64,
                    "has_more": False,
                }
            ),
        )
        self.assertFalse(terminal["replayed"])
        with self.assertRaises(V12StoreError) as post_terminal:
            self.store.record_assignment_page_receipt(
                private_position=2,
                **(common | {"page_digest": "sha256:" + "9" * 64}),
            )
        self.assertEqual(post_terminal.exception.code, "report_cursor_invalid")
        with self.store._connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM assignment_page_receipts"
                ).fetchone()[0],
                2,
            )

    def test_changed_provenance_conflicts_and_wrong_scope_is_stale(self) -> None:
        first = self.store.mint_worker_bootstrap(**self.args)
        changed = dict(self.args, source_digest="sha256:" + "9" * 64)
        with self.assertRaisesRegex(V12StoreError, "conflicts"):
            self.store.mint_worker_bootstrap(**changed)
        with self.assertRaisesRegex(V12StoreError, "stale"):
            self.store.consume_worker_bootstrap(capability=first["capability"], **changed)

    def test_current_capability_and_receipt_tables_are_present(self) -> None:
        with self.store._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 2)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(worker_capabilities)")}
            receipt_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(assignment_page_receipts)"
                )
            }
            loss_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(assignment_losses)"
                )
            }
        self.assertIn("continuation_ref", columns)
        self.assertIn("dispatch_digest", columns)
        self.assertIn("lease_expires_at", columns)
        self.assertIn("snapshot_digest", receipt_columns)
        self.assertIn("private_position", receipt_columns)
        self.assertIn("successor_assignment_id", loss_columns)
        self.assertIn("evidence_digest", loss_columns)


    def test_live_or_expired_unpublished_route_never_infers_native_loss(self):
        for consumed in (False, True):
            if consumed:
                self.store.consume_worker_bootstrap_for_assignment(**self.args)
            for deadline in ("2999-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"):
                self.store._write(lambda c: c.execute(
                    "UPDATE worker_capabilities SET lease_expires_at=? WHERE assignment_id=?",
                    (deadline, self.assignment["delegation_id"])))
                snapshot = self.store.node_admission_snapshot(graph_id=self.dispatch_args["graph_id"])
                for recover in (False, True):
                    with self.subTest(consumed=consumed, deadline=deadline, recover=recover), self.assertRaises(V12StoreError):
                        self.store.open_node_assignment(**dict(self.dispatch_args, admission=snapshot, recover=recover))
                self.assertEqual(self.store._read(lambda c: c.execute("SELECT COUNT(*) FROM delegations").fetchone()[0]), 1)
                self.assertEqual(self.store._read(lambda c: c.execute("SELECT state FROM worker_capabilities").fetchone()[0]),
                                 "consumed" if consumed else "minted")


if __name__ == "__main__":
    unittest.main()
