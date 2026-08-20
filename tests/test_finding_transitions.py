"""Regression coverage for canonical task-finding lifecycle merges."""
from __future__ import annotations

import concurrent.futures
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

from cortex_runtime import ledger_db  # noqa: E402


class FindingTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / ".codex" / "cortex"
        ledger_db.ensure_database(self.root)
        self._add_task("task-1")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _add_task(self, task_id: str) -> None:
        definition = {
            "schema": "cortex/v3",
            "task_id": task_id,
            "objective": "finding transition fixture",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        state = {
            "schema": "cortex/v3",
            "task_id": task_id,
            "task_number": 1,
            "status": "active",
            "revision": 1,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        ledger_db.create_task(self.root, definition, state, f"tasks/{task_id}")

    @staticmethod
    def _finding(
        fingerprint: str = "same-finding",
        *,
        severity: str = "P2",
        status: str = "open",
        blocking: bool = False,
        **metadata: str,
    ) -> dict[str, object]:
        return {
            "fingerprint": fingerprint,
            "severity": severity,
            "status": status,
            "blocking": blocking,
            "summary": "A canonical finding",
            **metadata,
        }

    def _stored(self, fingerprint: str = "same-finding") -> dict[str, object]:
        return ledger_db.list_task_findings(self.root, "task-1")[0]

    def test_open_findings_merge_conservatively_in_both_orders(self) -> None:
        for first, second in (
            (self._finding(severity="P3", blocking=False), self._finding(severity="P1", blocking=True)),
            (self._finding(severity="P1", blocking=True), self._finding(severity="P3", blocking=False)),
        ):
            with self.subTest(first=first["severity"], second=second["severity"]):
                ledger_db.upsert_task_finding(self.root, "task-1", first)
                ledger_db.upsert_task_finding(self.root, "task-1", second)
                stored = self._stored()
                self.assertEqual(stored["status"], "open")
                self.assertEqual(stored["severity"], "P1")
                self.assertTrue(stored["blocking"])
            with ledger_db._connection(self.root, write=True) as connection:
                connection.execute("DELETE FROM task_findings WHERE task_id = ?", ("task-1",))

    def test_open_blocking_finding_cannot_be_downgraded(self) -> None:
        ledger_db.upsert_task_finding(
            self.root,
            "task-1",
            self._finding(severity="P2", blocking=True),
        )
        ledger_db.upsert_task_finding(
            self.root,
            "task-1",
            self._finding(severity="P2", blocking=False),
        )
        stored = self._stored()
        self.assertTrue(stored["blocking"])
        self.assertEqual(stored["severity"], "P2")

    def test_open_p2_is_advisory_unless_explicitly_marked_blocking(self) -> None:
        ledger_db.upsert_task_finding(
            self.root,
            "task-1",
            self._finding("advisory-p2", severity="P2", blocking=False),
        )
        self.assertEqual(ledger_db.task_findings_blockers(self.root, "task-1"), [])

        ledger_db.upsert_task_finding(
            self.root,
            "task-1",
            self._finding("blocking-p2", severity="P2", blocking=True),
        )
        blockers = ledger_db.task_findings_blockers(self.root, "task-1")
        self.assertEqual([item["fingerprint"] for item in blockers], ["blocking-p2"])

    def test_open_p0_and_p1_remain_blockers_without_explicit_flag(self) -> None:
        ledger_db.upsert_task_finding(
            self.root,
            "task-1",
            self._finding("intrinsic-p0", severity="P0", blocking=False),
        )
        ledger_db.upsert_task_finding(
            self.root,
            "task-1",
            self._finding("intrinsic-p1", severity="P1", blocking=False),
        )
        blockers = ledger_db.task_findings_blockers(self.root, "task-1")
        self.assertEqual(
            [item["fingerprint"] for item in blockers],
            ["intrinsic-p0", "intrinsic-p1"],
        )

    def test_explicit_resolve_and_waive_transitions_keep_metadata_contract(self) -> None:
        ledger_db.upsert_task_finding(
            self.root,
            "task-1",
            self._finding(severity="P1", blocking=True),
        )
        ledger_db.upsert_task_finding(
            self.root,
            "task-1",
            self._finding(
                severity="P3",
                status="resolved",
                blocking=False,
                resolved_at="2026-08-19T00:00:00+00:00",
            ),
        )
        resolved = self._stored()
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["severity"], "P3")
        self.assertFalse(resolved["blocking"])
        self.assertEqual(resolved["resolved_at"], "2026-08-19T00:00:00+00:00")

        ledger_db.upsert_task_finding(
            self.root,
            "task-1",
            self._finding(
                severity="P2",
                status="waived",
                blocking=True,
                waiver_reason="Accepted for the next release",
                waived_by="release-owner",
                waived_at="2026-08-19T00:01:00+00:00",
            ),
        )
        waived = self._stored()
        self.assertEqual(waived["status"], "waived")
        self.assertEqual(waived["severity"], "P2")
        self.assertTrue(waived["blocking"])
        self.assertEqual(waived["waiver_reason"], "Accepted for the next release")
        self.assertEqual(waived["waived_by"], "release-owner")
        self.assertEqual(waived["waived_at"], "2026-08-19T00:01:00+00:00")

    def test_exact_fingerprint_resolution_keeps_a_similar_sibling_open(self) -> None:
        first = self._finding(
            "documentation-link-a", severity="P2", blocking=True,
            summary="A documentation link needs correction.",
        )
        second = self._finding(
            "documentation-link-b", severity="P2", blocking=True,
            summary="A documentation link needs correction.",
        )
        ledger_db.upsert_task_finding(self.root, "task-1", first)
        ledger_db.upsert_task_finding(self.root, "task-1", second)
        ledger_db.upsert_task_finding(
            self.root,
            "task-1",
            self._finding(
                "documentation-link-a", severity="P2", status="resolved", blocking=False,
                summary="The first documentation link was rechecked.",
                resolved_at="2026-08-20T00:00:00+00:00",
            ),
        )
        states = {
            item["fingerprint"]: item["status"]
            for item in ledger_db.list_task_findings(self.root, "task-1")
        }
        self.assertEqual(states, {
            "documentation-link-a": "resolved",
            "documentation-link-b": "open",
        })

    def test_concurrent_open_reports_retain_maximum_severity_and_blocking(self) -> None:
        severities = ("info", "P3", "P2", "P1", "P0", "P3", "P2", "P1")

        def submit(index: int) -> None:
            ledger_db.upsert_task_finding(
                self.root,
                "task-1",
                self._finding(
                    severity=severities[index],
                    blocking=index % 3 == 0,
                ),
                source={"report_id": f"report-{index}"},
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(severities)) as pool:
            list(pool.map(submit, range(len(severities))))

        stored = self._stored()
        self.assertEqual(stored["severity"], "P0")
        self.assertTrue(stored["blocking"])
        self.assertEqual(len(stored["source_evidence"]), len(severities))


if __name__ == "__main__":
    unittest.main()
