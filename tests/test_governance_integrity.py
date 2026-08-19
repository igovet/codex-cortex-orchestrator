"""Adversarial v10 regression coverage for governance ledger invariants."""
from __future__ import annotations

import math
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

from cortex_runtime import governance, ledger_db


class GovernanceIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / ".codex" / "cortex"
        ledger_db.ensure_database(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_task(self, task_id: str) -> None:
        ledger_db.create_task(
            self.root,
            {"schema": "cortex/v3", "task_id": task_id, "objective": "fixture", "created_at": "2026-01-01T00:00:00+00:00"},
            {"schema": "cortex/v3", "task_id": task_id, "task_number": int(task_id.rsplit("-", 1)[-1]), "status": "active", "revision": 1, "updated_at": "2026-01-01T00:00:00+00:00"},
            f"tasks/{task_id}",
        )

    def initiative(self, suffix: str) -> dict:
        return governance.create_initiative(
            self.root,
            initiative_ref=f"initiative-{suffix}",
            title=f"Initiative {suffix}",
            goal="governance integrity fixture",
            owner="coordinator",
        )

    def test_v10_schema_has_non_null_scope_and_public_uow_boundary(self) -> None:
        self.assertEqual(ledger_db.DATABASE_SCHEMA_VERSION, 10)
        history = ledger_db.migration_history(self.root)
        self.assertEqual(history[-1]["name"], "governance-integrity-hardening")
        with ledger_db.connection(self.root) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(governance_records)")}
            indexes = {row[1] for row in connection.execute("PRAGMA index_list(governance_records)")}
        self.assertIn("scope_key", columns)
        self.assertIn("governance_records_scope_revision_unique", indexes)
        self.assertFalse(ledger_db.in_transaction(self.root))
        with ledger_db.transaction(self.root):
            self.assertTrue(ledger_db.in_transaction(self.root))

    def test_released_v9_database_upgrades_atomically_to_v10(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex" / "cortex"
            plan = ledger_db._migration_plan()
            with mock.patch.object(ledger_db, "_migration_plan", return_value=plan[:9]):
                ledger_db.ensure_database(root)
                self.assertEqual(ledger_db.migration_history(root)[-1]["version"], 9)
            ledger_db.ensure_database(root)
            self.assertEqual(ledger_db.migration_history(root)[-1]["version"], 10)
            with ledger_db.connection(root) as connection:
                self.assertIn(
                    "scope_key",
                    {row[1] for row in connection.execute("PRAGMA table_info(governance_records)")},
                )

    def test_scope_link_and_linear_revision_constraints_are_enforced(self) -> None:
        self.add_task("task-1")
        first = self.initiative("one")
        second = self.initiative("two")
        governance.link_task(self.root, initiative_ref=first["initiative_ref"], task_id="task-1", relationship="deliverable")
        original = governance.create_record(
            self.root, record_type="decision", content={"choice": "one"},
            initiative_ref=first["initiative_ref"], task_id="task-1",
        )
        with self.assertRaisesRegex(governance.GovernanceError, "not linked"):
            governance.create_record(
                self.root, record_type="decision", content={"choice": "wrong initiative"},
                initiative_ref=second["initiative_ref"], task_id="task-1",
            )
        successor = governance.revise_record(self.root, record_ref=original["record_ref"], content={"choice": "two"})
        self.assertEqual(successor["revision"], 2)
        with self.assertRaisesRegex(governance.GovernanceError, "already has a successor"):
            governance.create_record(
                self.root, record_type="decision", content={"choice": "fork"},
                initiative_ref=first["initiative_ref"], task_id="task-1", supersedes=original["record_ref"],
            )

    def test_immutable_artifact_is_the_record_body_and_cache_tamper_fails_closed(self) -> None:
        initiative = self.initiative("artifact")
        record = governance.create_record(
            self.root, record_type="decision", content={"choice": "artifact source"}, initiative_ref=initiative["initiative_ref"],
        )
        database = self.root / ledger_db.DATABASE_NAME
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER governance_records_immutable_update")
            connection.execute("UPDATE governance_records SET content_json=? WHERE record_ref=?", ('{"choice":"cache tamper"}', record["record_ref"]))
            connection.execute(
                "CREATE TRIGGER governance_records_immutable_update BEFORE UPDATE ON governance_records FOR EACH ROW "
                "WHEN NEW.content_json IS NOT OLD.content_json BEGIN SELECT RAISE(ABORT, 'governance record immutable fields cannot change'); END"
            )
            connection.commit()
        with self.assertRaisesRegex(governance.GovernanceError, "cache does not match") as raised:
            governance.inspect_record(self.root, record["record_ref"])
        self.assertEqual(raised.exception.code, "ledger_corrupt")

    def test_strict_json_and_nested_credential_default_deny(self) -> None:
        initiative = self.initiative("sensitive")
        with self.assertRaisesRegex(governance.GovernanceError, "strict JSON"):
            governance.create_record(self.root, record_type="learning", content=("tuple",), initiative_ref=initiative["initiative_ref"])
        with self.assertRaisesRegex(governance.GovernanceError, "non-finite"):
            governance.create_record(self.root, record_type="learning", content={"value": math.nan}, initiative_ref=initiative["initiative_ref"])
        with self.assertRaisesRegex(governance.GovernanceError, "require an approved policy"):
            governance.create_record(self.root, record_type="risk", content={"auth": {"password": "not-redacted"}}, initiative_ref=initiative["initiative_ref"])
        governance.create_record(
            self.root,
            record_type="policy",
            content={"record_types": ["risk"], "retention_days": 30, "allowed_roles": ["coordinator"], "allowed_fields": ["/auth/password"]},
            initiative_ref=initiative["initiative_ref"],
            status="approved",
        )
        with self.assertRaisesRegex(governance.GovernanceError, "must be redacted"):
            governance.create_record(self.root, record_type="risk", content={"auth": {"password": "not-redacted"}}, initiative_ref=initiative["initiative_ref"])
        redacted = governance.create_record(
            self.root, record_type="risk", content={"auth": {"password": "<REDACTED>"}}, initiative_ref=initiative["initiative_ref"],
        )
        self.assertEqual(redacted["content_json"]["auth"]["password"], "<REDACTED>")

    def test_submission_retry_is_durable_and_scope_replay_conflicts(self) -> None:
        initiative = self.initiative("submission")
        first = governance.create_record(
            self.root, record_type="decision", content={"choice": "retry"}, initiative_ref=initiative["initiative_ref"], submission_id="submission-retry",
        )
        replay = governance.create_record(
            self.root, record_type="decision", content={"choice": "retry"}, initiative_ref=initiative["initiative_ref"], submission_id="submission-retry",
        )
        self.assertEqual(first["record_ref"], replay["record_ref"])
        with self.assertRaisesRegex(governance.GovernanceError, "submission_id replay conflicts"):
            governance.create_record(
                self.root, record_type="decision", content={"choice": "different"}, initiative_ref=initiative["initiative_ref"], submission_id="submission-retry",
            )

    def test_promotion_approval_rolls_back_policy_when_transition_is_interrupted(self) -> None:
        initiative = self.initiative("promotion-atomic")
        proposal = governance.create_record(
            self.root,
            record_type="promotion",
            content={"fingerprint": "atomic", "finding_scopes": ["task-1"], "threshold": 1, "window_days": 1},
            initiative_ref=initiative["initiative_ref"],
            status="pending",
        )
        actual_create = governance.create_record

        def interrupt_after_policy(*args, **kwargs):
            actual_create(*args, **kwargs)
            raise RuntimeError("deterministic promotion interruption")

        with mock.patch.object(governance, "create_record", side_effect=interrupt_after_policy):
            with self.assertRaisesRegex(RuntimeError, "deterministic promotion interruption"):
                governance.approve_promotion(self.root, proposal_ref=proposal["record_ref"], actor_role="coordinator")
        stored = governance.inspect_record(self.root, proposal["record_ref"])
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["status"], "pending")
        self.assertFalse(
            governance.list_records(self.root, initiative_ref=initiative["initiative_ref"], record_type="policy", active_only=False)
        )

    def test_completion_blocks_unresolved_dependency_and_empty_exception(self) -> None:
        source = self.initiative("source")
        target = self.initiative("target")
        governance.transition_initiative(self.root, initiative_ref=source["initiative_ref"], status="active")
        governance.transition_initiative(self.root, initiative_ref=target["initiative_ref"], status="active")
        governance.add_dependency(
            self.root, source_type="initiative", source_ref=source["initiative_ref"], target_type="initiative", target_ref=target["initiative_ref"], dependency_type="requires",
        )
        with self.assertRaisesRegex(governance.GovernanceError, "unresolved blocks/requires"):
            governance.transition_initiative(self.root, initiative_ref=source["initiative_ref"], status="completed")
        governance.transition_initiative(self.root, initiative_ref=target["initiative_ref"], status="completed")
        self.assertEqual(
            governance.transition_initiative(self.root, initiative_ref=source["initiative_ref"], status="completed")["status"],
            "completed",
        )
        with self.assertRaisesRegex(governance.GovernanceError, "trigger is required"):
            governance.request_exception(self.root, trigger="", reason="not valid", initiative_ref=source["initiative_ref"])

    def test_independent_review_becomes_stale_after_linked_task_revision_changes(self) -> None:
        self.add_task("task-7")
        initiative = self.initiative("review")
        governance.link_task(self.root, initiative_ref=initiative["initiative_ref"], task_id="task-7", relationship="deliverable")
        governance.transition_initiative(self.root, initiative_ref=initiative["initiative_ref"], status="active")
        governance.transition_initiative(self.root, initiative_ref=initiative["initiative_ref"], status="completed")
        artifact = ledger_db.put_artifact(
            self.root, "task-7", kind="evidence", title="reviewed-material.json", mime_type="application/json",
            content='{"reviewed":true}', immutable=True,
        )
        loaded = ledger_db.load_task(self.root, "task-7")
        assert loaded is not None
        state = loaded[1]
        state["attempts"] = [{"attempt_id": "close-review", "gate": "governance_close", "agent": "code_reviewer", "status": "passed", "invalidated": False, "report_ids": ["report-7"]}]
        ledger_db.update_task_state(self.root, state)
        ledger_db.put_worker_session(self.root, {"task_id": "task-7", "attempt_id": "close-review", "host_agent_id": "reviewer-7", "host_task_name": "review", "host_tool": "spawn_agent", "status": "completed"})
        payload = {
            "task_id": "task-7", "attempt_id": "close-review", "report_id": "report-7", "reviewer_identity": "reviewer-7",
            "reviewed_initiative_revision": 4,
            "reviewed_task_revisions": {"task-7": 1},
            "reviewed_artifact_digests": {artifact["artifact_ref"]: artifact["digest_sha256"]},
        }
        with ledger_db.connection(self.root) as connection:
            governance._validate_independent_review_attestation(connection, initiative_ref=initiative["initiative_ref"], owner="coordinator", initiative_revision=4, metadata={"task_id": "task-7"}, payload=payload)
        state["revision"] = 2
        state["updated_at"] = "2026-01-02T00:00:00+00:00"
        ledger_db.update_task_state(self.root, state)
        with ledger_db.connection(self.root) as connection:
            with self.assertRaisesRegex(governance.GovernanceError, "stale for linked task revisions"):
                governance._validate_independent_review_attestation(connection, initiative_ref=initiative["initiative_ref"], owner="coordinator", initiative_revision=4, metadata={"task_id": "task-7"}, payload=payload)


if __name__ == "__main__":
    unittest.main()
