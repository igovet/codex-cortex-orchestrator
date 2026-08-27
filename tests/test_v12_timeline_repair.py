"""Regression coverage for the V12 task-scoped canonical chronology repair."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.v12_store import V12Store  # noqa: E402
from cortex_runtime.v12_projections import materialize_task  # noqa: E402
from cortex_runtime.v12_contract import task_ref  # noqa: E402


def _markdown_timeline_index(path: Path) -> tuple[int, list[dict[str, object]]]:
    """Read only the server-shaped fields from the human Markdown index."""
    text = path.read_text(encoding="utf-8")
    latest = re.search(r"^\s*- \*\*latest_sequence:\*\* (\d+)$", text, re.MULTILINE)
    if latest is None:
        raise AssertionError("Markdown timeline index has no latest sequence")
    pages: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for line in text.splitlines():
        path_match = re.search(r"\*\*path[^*]*\*\*\s+(pages/\d+)-(\d+)\.md", line)
        if path_match:
            current = {"path": f"pages/{path_match.group(1).split('/')[-1]}-{path_match.group(2)}.md"}
            pages.append(current)
            continue
        events_match = re.search(r"\*\*events[^*]*\*\*\s+(\d+)", line)
        if events_match and pages:
            pages[-1]["events"] = int(events_match.group(1))
    return int(latest.group(1)), pages


def _markdown_timeline_sequences(path: Path) -> list[int]:
    """Extract the repeated canonical sequence labels from a page."""
    return [int(value) for value in re.findall(r"^\s*- \*\*sequence:\*\* (\d+)$", path.read_text(encoding="utf-8"), re.MULTILINE)]


class V12TimelineRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cortex-v12-timeline-")
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.home_patch = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.home_patch.start()
        os.environ.pop("CORTEX_HOST_STATE_DIR", None)
        os.environ.pop("CODEX_HOME", None)

    def tearDown(self) -> None:
        self.home_patch.stop()
        self.temporary.cleanup()

    def _store(self) -> V12Store:
        return V12Store(self.project)

    @staticmethod
    def _task(store: V12Store, objective: str) -> str:
        return store.create_task(
            objective=objective,
            user_request_original=objective,
            user_language="en",
            task_contract_version="cortex/task-contract/v1",
            requirements=["Preserve the canonical timeline."],
            constraints=["No additional constraints."],
            acceptance_criteria=["The task timeline is ordered and complete."],
            verification_plan=["Inspect the bounded task timeline."],
            context={},
        )[0]["task"]["task_id"]

    @staticmethod
    def _delegation(store: V12Store, task_id: str, number: int) -> str:
        return store.create_delegation(
            task_id=task_id,
            objective=f"Timeline worker {number}",
            role="qa",
            profile_name="general",
            scope=f"Timeline worker {number} owns its bounded test evidence.",
            instructions=(
                "Knowledge contract:\n"
                "Documents to consume first: docs/project/index.md\n"
                "Applicable requirements: Preserve the canonical timeline.\n"
                "Verification contract: Inspect the bounded task timeline.\n"
                "Ownership constraints: Do not edit outside the delegated test surface.\n"
                "Known documentation state: No known documentation state\n"
                "Further documentation discovery: Not authorized\n\n"
                "Return only bounded test evidence."
            ),
            model="gpt-5.6-luna",
            reasoning_effort="high",
        )[0]["delegation"]["delegation_id"]

    def test_profile_names_are_used_for_native_workers_and_numbered_for_siblings(self) -> None:
        store = self._store()
        task_id = self._task(store, "Name same-profile native workers clearly.")

        def create(number: int) -> str:
            result, _ = store.create_delegation(
                task_id=task_id,
                objective=f"General worker {number}",
                role="general worker",
                profile_name="general",
                scope="Own the bounded worker-name evidence.",
                instructions="Return bounded worker-name evidence.",
                model="gpt-5.6-luna",
                reasoning_effort="high",
            )
            return str(result["delegation"]["native_task_name"])

        self.assertEqual(create(1), "general")
        self.assertEqual(create(2), "general_2")
        self.assertEqual(create(3), "general_3")

    def test_plan_revision_feedback_survives_intervening_timeline_activity(self) -> None:
        store = self._store()
        task_id = self._task(store, "Record a plan revision after unrelated activity.")
        delegation_id = store.create_delegation(
            task_id=task_id,
            objective="Produce the plan for the revision-feedback regression.",
            role="planner",
            profile_name="planner",
            scope="Own only the plan evidence for this regression test.",
            instructions="Return the initial plan evidence.",
            model="gpt-5.6-luna",
            reasoning_effort="high",
        )[0]["delegation"]["delegation_id"]
        report = store.submit_report(
            task_id=task_id,
            delegation_id=delegation_id,
            report_type="plan",
            status="completed",
            content={"steps": ["Keep the plan digest stable."]},
        )[0]["report"]
        plan_id, plan_digest = report["report_id"], report["content_digest"]

        # This advances the task chronology after the plan was materialized,
        # making any previously issued approval view stale by design.
        store.record_initiative(
            task_id=task_id,
            goal="Unrelated advisory activity between plan review turns.",
            initiative_id=None,
            parent_initiative_id=None,
            risk=None,
            status="active",
            dependencies=[],
            linked_task_ids=[task_id],
            linked_report_ids=[],
            notes=[],
            idempotency_key=None,
        )

        result, replayed = store.record_user_decision(
            task_id=task_id,
            subject_type="plan",
            subject_id=plan_id,
            subject_digest=plan_digest,
            decision_type="request_revision",
            prompt_en="What should be revised?",
            response_original="Please clarify the verification step.",
            response_en="Please clarify the verification step.",
            user_language="en",
            approval_handle=None,
            approval_view_content_digest=None,
            approval_view_source_sequence=None,
            supersedes_decision_id=None,
            idempotency_key=None,
        )
        self.assertFalse(replayed)
        decision = result["decision"]
        self.assertEqual(decision["subject_id"], plan_id)
        self.assertEqual(decision["subject_digest"], plan_digest)
        self.assertEqual(decision["decision_type"], "request_revision")

        with sqlite3.connect(store.database_path) as connection:
            row = connection.execute(
                "SELECT subject_digest,decision_type,response_original FROM user_decisions WHERE decision_id=?",
                (decision["decision_id"],),
            ).fetchone()
        self.assertEqual(row, (plan_digest, "request_revision", "Please clarify the verification step."))

    def _seed_live_shape_with_only_task_created_timeline(self) -> tuple[V12Store, str, str, str]:
        """Reproduce the reported shape in an isolated current-V12 shard.

        The fixture deliberately retains four delegations, four reports/four
        chunks, governance, report-only initiative lineage, a closure, and a
        decision while leaving exactly one canonical timeline row.  Direct
        SQLite editing is test-fixture setup only; production repair opens the
        user shard normally and never uses a maintenance command.
        """
        store = self._store()
        task_id = self._task(store, "Repair this canonical timeline.")
        delegations = [self._delegation(store, task_id, number) for number in range(1, 5)]
        reports = [
            store.submit_report(
                task_id=task_id,
                delegation_id=delegations[number],
                report_type="result",
                status="completed",
                content={"report": number + 1},
            )[0]["report"]["report_id"]
            for number in range(3)
        ]
        begun = store.submit_report(
            task_id=task_id,
            delegation_id=delegations[3],
            report_type="result",
            mode="begin",
        )[0]["report"]["report_id"]
        appended = store.submit_report(
            task_id=task_id,
            delegation_id=delegations[3],
            report_id=begun,
            mode="append",
            chunk_index=0,
            section="body",
            content={"report": 4},
        )[0]
        store.submit_report(
            task_id=task_id,
            delegation_id=delegations[3],
            report_id=begun,
            mode="finalize",
            status="completed",
            expected_chunk_count=1,
            expected_content_digest=appended["current_content_digest"],
        )
        reports.append(begun)
        store.set_governance_mode(
            task_id=task_id,
            mode="minimal",
            rationale=None,
            risk_factors=[],
            source="model",
            initiative_id=None,
            idempotency_key=None,
        )
        store.record_user_decision(
            task_id=task_id,
            subject_type="task",
            subject_id=task_id,
            subject_digest=None,
            decision_type="approve",
            prompt_en="Continue the bounded timeline test.",
            response_original="Continue.",
            response_en="Continue.",
            user_language="en",
            supersedes_decision_id=None,
            idempotency_key=None,
        )
        initiative_id = store.record_initiative(
            task_id=task_id,
            goal="Test report-only initiative lineage.",
            initiative_id=None,
            parent_initiative_id=None,
            risk=None,
            status="active",
            dependencies=[],
            linked_task_ids=[],
            linked_report_ids=reports[:3],
            notes=[],
            idempotency_key=None,
        )[0]["initiative"]["initiative_id"]
        store.submit_governance_closure(
            task_id=task_id,
            subject_type="initiative",
            subject_id=initiative_id,
            verdict="ready",
            evidence=[],
            unresolved_risks=[],
            follow_ups=[],
            initiative_status="closed",
            completion_notes=[],
            idempotency_key=None,
        )

        v11_sentinel = self.home / ".codex" / "cortex" / "v11" / "cortex.db"
        v11_sentinel.parent.mkdir(parents=True)
        v11_sentinel.write_bytes(b"v11-must-remain-untouched")
        v11_digest = hashlib.sha256(v11_sentinel.read_bytes()).hexdigest()

        with sqlite3.connect(store.database_path) as connection:
            counts = {
                "delegations": connection.execute("SELECT COUNT(*) FROM delegations WHERE task_id=?", (task_id,)).fetchone()[0],
                "reports": connection.execute("SELECT COUNT(*) FROM reports WHERE task_id=?", (task_id,)).fetchone()[0],
                "chunks": connection.execute(
                    "SELECT COUNT(*) FROM report_chunks c JOIN reports r ON r.report_id=c.report_id WHERE r.task_id=?",
                    (task_id,),
                ).fetchone()[0],
                "report_links": connection.execute(
                    "SELECT COUNT(*) FROM initiative_links WHERE initiative_id=? AND relationship='report'",
                    (initiative_id,),
                ).fetchone()[0],
                "task_links": connection.execute(
                    "SELECT COUNT(*) FROM initiative_links WHERE initiative_id=? AND relationship='task'",
                    (initiative_id,),
                ).fetchone()[0],
                "revisions": connection.execute(
                    "SELECT COUNT(*) FROM initiative_revisions WHERE initiative_id=?", (initiative_id,),
                ).fetchone()[0],
                "closures": connection.execute("SELECT COUNT(*) FROM governance_closures").fetchone()[0],
            }
            self.assertEqual(counts, {"delegations": 4, "reports": 4, "chunks": 4, "report_links": 3, "task_links": 0, "revisions": 2, "closures": 1})
            connection.execute("DELETE FROM timeline WHERE event_type <> 'task_created'")
            connection.execute("DELETE FROM v12_metadata WHERE key='timeline_backfill_v1'")
            # Earlier V12 preview builds used non-range timeline page names.
            # The repair must retire their registry metadata too, without
            # deleting a potentially user-altered private file.
            connection.execute(
                "INSERT INTO projection_files(task_id,relative_path,source_sequence,renderer_version,content_digest,status,updated_at) VALUES (?, 'timeline/0001.md', 1, 'legacy-preview', ?, 'ready', ?)",
                (task_id, "sha256:" + "0" * 64, "2000-01-01T00:00:00+00:00"),
            )
            connection.commit()
            remaining = connection.execute("SELECT event_type,task_id FROM timeline").fetchall()
        self.assertEqual(remaining, [("task_created", task_id)])
        return store, task_id, initiative_id, v11_digest

    def test_normal_open_backfills_live_shape_once_and_refreshes_views(self) -> None:
        original, task_id, initiative_id, v11_digest = self._seed_live_shape_with_only_task_created_timeline()
        database = original.database_path

        # This is the ordinary first open an installed V12 runtime performs.
        repaired = self._store()
        task = repaired.inspect_task(task_id=task_id, after_sequence=0, limit=100)
        governance = repaired.inspect_governance(task_id=task_id, initiative_id=None, after_sequence=0, limit=100)
        event_types = [str(item["event_type"]) for item in task["timeline"]]
        expected = [
            "task_created",
            *(["delegation_created"] * 4),
            *(["report_submitted"] * 3),
            "report_started",
            "report_chunk_appended",
            "report_submitted",
            "governance_mode_set",
            "user_decision_recorded",
            "initiative_created",
            "initiative_revised_by_closure",
            "governance_closure_submitted",
            "initiative_task_link_derived",
        ]
        self.assertEqual(event_types, expected)
        sequences = [int(item["sequence"]) for item in task["timeline"]]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(sequences), len(set(sequences)))
        self.assertTrue(all(item["task_id"] == task_id for item in task["timeline"]))
        self.assertEqual(task["timeline"][0]["event_type"], "task_created")
        self.assertTrue(all(item["payload"].get("backfill", {}).get("derived") is True for item in task["timeline"][1:]))
        self.assertEqual([item["initiative_id"] for item in governance["initiatives"]], [initiative_id])
        self.assertIn("governance_closure_submitted", [item["event_type"] for item in governance["timeline"]])

        delegation_id = task["delegations"][0]["delegation_id"]
        report_id = task["reports"][0]["report_id"]
        with sqlite3.connect(database) as connection:
            before_read_count = connection.execute("SELECT COUNT(*) FROM timeline WHERE task_id=?", (task_id,)).fetchone()[0]
        repaired.read_delegation(task_id=task_id, delegation_id=delegation_id, after_sequence=0, limit=10)
        repaired.read_reports(task_id=task_id, report_ids=[report_id], max_bytes=0)
        repaired.inspect_governance(task_id=task_id, initiative_id=initiative_id, after_sequence=0, limit=10)
        with sqlite3.connect(database) as connection:
            read_rows = connection.execute(
                "SELECT event_type,report_id FROM timeline WHERE task_id=? ORDER BY sequence",
                (task_id,),
            ).fetchall()
            receipt_rows = connection.execute(
                "SELECT report_id,reader_kind FROM report_consumption_receipts WHERE task_id=? ORDER BY created_sequence",
                (task_id,),
            ).fetchall()
            post_read_sequences = tuple(row[0] for row in connection.execute(
                "SELECT sequence FROM timeline WHERE task_id=? ORDER BY sequence", (task_id,)
            ).fetchall())
        self.assertEqual(len(read_rows), before_read_count + 1)
        self.assertEqual(read_rows[-1], ("report_read", report_id))
        self.assertEqual(receipt_rows, [(report_id, "coordinator")])

        with sqlite3.connect(database) as connection:
            marker = connection.execute("SELECT value FROM v12_metadata WHERE key='timeline_backfill_v1'").fetchone()
            task_link_count = connection.execute(
                "SELECT COUNT(*) FROM initiative_links WHERE initiative_id=? AND relationship='task' AND target_id=?",
                (initiative_id, task_id),
            ).fetchone()[0]
        self.assertEqual(marker, ("cortex/v12-timeline-backfill/v1",))
        self.assertEqual(task_link_count, 1)
        self.assertEqual(
            hashlib.sha256((self.home / ".codex" / "cortex" / "v11" / "cortex.db").read_bytes()).hexdigest(),
            v11_digest,
        )

        self.assertEqual(repaired.human_view(task_id, "timeline/index.md"), {"status": "disabled", "path": None})

        # The marker makes every following normal open idempotent.
        before = post_read_sequences
        reopened = self._store().inspect_task(task_id=task_id, after_sequence=0, limit=100)
        self.assertEqual(tuple(item["sequence"] for item in reopened["timeline"]), before)

    def test_wal_ordering_cross_task_isolation_read_only_and_rollback(self) -> None:
        store = self._store()
        task_a = self._task(store, "Concurrent timeline A.")
        task_b = self._task(store, "Concurrent timeline B.")

        def write(number: int) -> str:
            return self._delegation(self._store(), task_a if number % 2 == 0 else task_b, number)

        with ThreadPoolExecutor(max_workers=8) as executor:
            delegation_ids = list(executor.map(write, range(16)))
        self.assertEqual(len(set(delegation_ids)), 16)
        current = self._store()
        task_a_snapshot = current.inspect_task(task_id=task_a, after_sequence=0, limit=100)
        task_b_snapshot = current.inspect_task(task_id=task_b, after_sequence=0, limit=100)
        task_a_sequences = [int(item["sequence"]) for item in task_a_snapshot["timeline"]]
        task_b_sequences = [int(item["sequence"]) for item in task_b_snapshot["timeline"]]
        self.assertEqual(len(task_a_sequences), 9)
        self.assertEqual(len(task_b_sequences), 9)
        self.assertTrue(set(task_a_sequences).isdisjoint(task_b_sequences))
        self.assertTrue(all(item["task_id"] == task_a for item in task_a_snapshot["timeline"]))
        self.assertTrue(all(item["task_id"] == task_b for item in task_b_snapshot["timeline"]))

        with sqlite3.connect(current.database_path) as connection:
            before = connection.execute("SELECT COUNT(*) FROM timeline WHERE task_id=?", (task_a,)).fetchone()[0]

        def rollback(connection: sqlite3.Connection) -> None:
            current._timeline(
                connection,
                event_type="rollback_probe",
                entity_type="test",
                entity_id="rollback-probe",
                payload={"test": True},
                task_id=task_a,
            )
            raise RuntimeError("force SQLite rollback")

        with self.assertRaises(RuntimeError):
            current._write(rollback)
        current.inspect_task(task_id=task_a, after_sequence=0, limit=100)
        with sqlite3.connect(current.database_path) as connection:
            after = connection.execute("SELECT COUNT(*) FROM timeline WHERE task_id=?", (task_a,)).fetchone()[0]
        self.assertEqual(after, before)

    def test_current_mutations_append_one_task_scoped_event_per_transition(self) -> None:
        """New V12 mutations never need the compatibility backfill path."""
        store = self._store()
        task_id = self._task(store, "Record every current durable transition.")
        delegation_id = self._delegation(store, task_id, 1)
        report_id = store.submit_report(
            task_id=task_id, delegation_id=delegation_id, report_type="result", mode="begin",
        )[0]["report"]["report_id"]
        appended = store.submit_report(
            task_id=task_id,
            delegation_id=delegation_id,
            report_id=report_id,
            mode="append",
            chunk_index=0,
            section="body",
            content={"state": "final"},
        )[0]
        store.submit_report(
            task_id=task_id,
            delegation_id=delegation_id,
            report_id=report_id,
            mode="finalize",
            status="completed",
            expected_chunk_count=1,
            expected_content_digest=appended["current_content_digest"],
        )
        store.set_governance_mode(
            task_id=task_id, mode="minimal", rationale=None, risk_factors=[], source="model", initiative_id=None,
            idempotency_key=None,
        )
        store.record_user_decision(
            task_id=task_id,
            subject_type="task",
            subject_id=task_id,
            subject_digest=None,
            decision_type="approve",
            prompt_en="Proceed with the current task.",
            response_original="Proceed.",
            response_en="Proceed.",
            user_language="en",
            supersedes_decision_id=None,
            idempotency_key=None,
        )
        initiative_id = store.record_initiative(
            task_id=task_id,
            goal="Record current task-scoped initiative chronology.",
            initiative_id=None,
            parent_initiative_id=None,
            risk=None,
            status="active",
            dependencies=[],
            linked_task_ids=[],
            linked_report_ids=[report_id],
            notes=[],
            idempotency_key=None,
        )[0]["initiative"]["initiative_id"]
        store.submit_governance_closure(
            task_id=task_id,
            subject_type="initiative",
            subject_id=initiative_id,
            verdict="ready",
            evidence=[],
            unresolved_risks=[],
            follow_ups=[],
            initiative_status="closed",
            completion_notes=[],
            idempotency_key=None,
        )
        snapshot = store.inspect_task(task_id=task_id, after_sequence=0, limit=50)
        self.assertEqual(
            [item["event_type"] for item in snapshot["timeline"]],
            [
                "task_created",
                "delegation_created",
                "report_started",
                "report_chunk_appended",
                "report_submitted",
                "governance_mode_set",
                "user_decision_recorded",
                "initiative_created",
                "initiative_revised_by_closure",
                "governance_closure_submitted",
            ],
        )
        self.assertTrue(all(item["task_id"] == task_id for item in snapshot["timeline"]))
        self.assertTrue(all("backfill" not in item["payload"] for item in snapshot["timeline"]))
        closure_events = [item for item in snapshot["timeline"] if item["event_type"] == "governance_closure_submitted"]
        revision_events = [item for item in snapshot["timeline"] if item["event_type"] == "initiative_revised_by_closure"]
        self.assertEqual(len(closure_events), 1)
        self.assertEqual(len(revision_events), 1)
        self.assertEqual(revision_events[0]["closure_id"], closure_events[0]["closure_id"])
        self.assertEqual(revision_events[0]["payload"]["closure_id"], closure_events[0]["closure_id"])
        governance = store.inspect_governance(task_id=task_id, initiative_id=None, after_sequence=0, limit=50)
        self.assertEqual([item["initiative_id"] for item in governance["initiatives"]], [initiative_id])

    def test_conflicting_revision_anchor_never_leaves_a_derived_task_link(self) -> None:
        """Validate all legacy evidence before repairing report-only lineage."""
        store = self._store()
        task_a = self._task(store, "Conflicting revision lineage A.")
        task_b = self._task(store, "Conflicting revision lineage B.")
        delegation = self._delegation(store, task_a, 1)
        report_id = store.submit_report(
            task_id=task_a, delegation_id=delegation, report_type="result", status="completed", content={"task": "a"},
        )[0]["report"]["report_id"]
        initiative_id = store.record_initiative(
            task_id=task_a,
            goal="Do not link contradictory legacy evidence.",
            initiative_id=None,
            parent_initiative_id=None,
            risk=None,
            status="active",
            dependencies=[],
            linked_task_ids=[],
            linked_report_ids=[report_id],
            notes=[],
            idempotency_key=None,
        )[0]["initiative"]["initiative_id"]
        with sqlite3.connect(store.database_path) as connection:
            revision = json.loads(connection.execute(
                "SELECT payload_json FROM initiative_revisions WHERE initiative_id=? AND revision_number=1",
                (initiative_id,),
            ).fetchone()[0])
            revision["task_id"] = task_b
            connection.execute(
                "UPDATE initiative_revisions SET payload_json=? WHERE initiative_id=? AND revision_number=1",
                (json.dumps(revision, sort_keys=True, separators=(",", ":")), initiative_id),
            )
            connection.execute("DELETE FROM timeline WHERE event_type <> 'task_created'")
            connection.execute("DELETE FROM v12_metadata WHERE key='timeline_backfill_v1'")
            connection.commit()

        repaired = self._store()
        with sqlite3.connect(repaired.database_path) as connection:
            direct_links = connection.execute(
                "SELECT COUNT(*) FROM initiative_links WHERE initiative_id=? AND relationship='task'",
                (initiative_id,),
            ).fetchone()[0]
            warning_values = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT warnings_json FROM initiative_links WHERE initiative_id=? ORDER BY link_id",
                    (initiative_id,),
                ).fetchall()
            ]
        self.assertEqual(direct_links, 0)
        self.assertTrue(all("timeline_backfill_task_conflict" in values for values in warning_values))
        for task_id in (task_a, task_b):
            events = repaired.inspect_task(task_id=task_id, after_sequence=0, limit=50)["timeline"]
            self.assertFalse(any(item["initiative_id"] == initiative_id for item in events))

    def test_ambiguous_report_lineage_is_warned_and_never_guessed(self) -> None:
        store = self._store()
        task_a = self._task(store, "Ambiguous initiative lineage A.")
        task_b = self._task(store, "Ambiguous initiative lineage B.")
        delegation_a = self._delegation(store, task_a, 1)
        delegation_b = self._delegation(store, task_b, 2)
        report_a = store.submit_report(
            task_id=task_a, delegation_id=delegation_a, report_type="result", status="completed", content={"task": "a"},
        )[0]["report"]["report_id"]
        report_b = store.submit_report(
            task_id=task_b, delegation_id=delegation_b, report_type="result", status="completed", content={"task": "b"},
        )[0]["report"]["report_id"]
        initiative_id = store.record_initiative(
            task_id=task_a,
            goal="Keep this initiative unscoped during the repair.",
            initiative_id=None,
            parent_initiative_id=None,
            risk=None,
            status="active",
            dependencies=[],
            linked_task_ids=[],
            linked_report_ids=[report_a, report_b],
            notes=[],
            idempotency_key=None,
        )[0]["initiative"]["initiative_id"]
        with sqlite3.connect(store.database_path) as connection:
            connection.execute("DELETE FROM timeline WHERE event_type <> 'task_created'")
            connection.execute("DELETE FROM v12_metadata WHERE key='timeline_backfill_v1'")
            connection.commit()

        repaired = self._store()
        with sqlite3.connect(repaired.database_path) as connection:
            direct_links = connection.execute(
                "SELECT COUNT(*) FROM initiative_links WHERE initiative_id=? AND relationship='task'",
                (initiative_id,),
            ).fetchone()[0]
            warnings = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT warnings_json FROM initiative_links WHERE initiative_id=? AND relationship='report' ORDER BY link_id",
                    (initiative_id,),
                ).fetchall()
            ]
        self.assertEqual(direct_links, 0)
        self.assertTrue(all("timeline_backfill_task_conflict" in values for values in warnings))
        self.assertEqual(repaired.inspect_governance(task_id=task_a, initiative_id=None, after_sequence=0, limit=20)["initiatives"], [])
        self.assertEqual(repaired.inspect_governance(task_id=task_b, initiative_id=None, after_sequence=0, limit=20)["initiatives"], [])
        for task_id in (task_a, task_b):
            event_types = [item["event_type"] for item in repaired.inspect_task(task_id=task_id, after_sequence=0, limit=50)["timeline"]]
            self.assertNotIn("initiative_created", event_types)
            self.assertNotIn("initiative_task_link_derived", event_types)

    def test_timeline_events_remain_sqlite_only(self) -> None:
        store = self._store()
        task_id = self._task(store, "Paginate canonical chronology.")

        def seed_events(connection: sqlite3.Connection) -> None:
            for index in range(101):
                store._timeline(
                    connection,
                    event_type="projection_pagination_probe",
                    entity_type="test",
                    entity_id=f"probe-{index}",
                    payload={"index": index},
                    task_id=task_id,
                )

        store._write(seed_events)
        self.assertEqual(materialize_task(store, task_id)["status"], "ready")
        compact_task_ref = task_ref(task_id)
        self.assertIsInstance(compact_task_ref, str)
        view_root = store.root / "tasks" / str(compact_task_ref)
        self.assertFalse((view_root / "timeline").exists())
        self.assertEqual(store.human_view(task_id, "timeline/index.md"), {"status": "disabled", "path": None})


if __name__ == "__main__":
    unittest.main()
