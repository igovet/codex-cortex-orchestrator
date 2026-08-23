"""Adversarial coverage for current canonical governance ledger invariants."""
from __future__ import annotations

import math
import json
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

from cortex_runtime import attempt_protocol, governance, ledger_db


class GovernanceIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / ".codex" / "cortex"
        ledger_db.ensure_database(self.root)
        # Current lifecycle authentication is host-private and must already
        # exist before a record write.  Initialize the legitimate sidecar key
        # for this isolated fixture; production still fails closed when it is
        # unavailable.
        ledger_db._governance_lifecycle_hmac_key(self.root, create=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_task(self, task_id: str) -> None:
        ledger_db.create_task(
            self.root,
            {"schema": "cortex/v3", "task_id": task_id, "user_request": "fixture", "created_at": "2026-01-01T00:00:00+00:00"},
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

    def test_v15_schema_has_non_null_scope_lifecycle_authority_and_public_uow_boundary(self) -> None:
        self.assertEqual(ledger_db.DATABASE_SCHEMA_VERSION, 15)
        history = ledger_db.migration_history(self.root)
        self.assertEqual(history[-1]["name"], "canonical-current-ledger")
        with ledger_db.connection(self.root) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(governance_records)")}
            indexes = {row[1] for row in connection.execute("PRAGMA index_list(governance_records)")}
            lifecycle_columns = {row[1] for row in connection.execute("PRAGMA table_info(governance_record_lifecycle)")}
            auth_columns = {row[1] for row in connection.execute("PRAGMA table_info(governance_record_lifecycle_auth)")}
        self.assertIn("scope_key", columns)
        self.assertTrue({"lifecycle_sequence", "lifecycle_binding"}.issubset(columns))
        self.assertIn("governance_records_scope_revision_unique", indexes)
        self.assertTrue({"record_ref", "lifecycle_sequence", "previous_binding", "binding"}.issubset(lifecycle_columns))
        self.assertEqual(auth_columns, {"lifecycle_ref", "envelope_hmac"})
        self.assertFalse(ledger_db.in_transaction(self.root))
        with ledger_db.transaction(self.root):
            self.assertTrue(ledger_db.in_transaction(self.root))

    def test_lifecycle_hmac_covers_fields_outside_the_v11_public_chain(self) -> None:
        initiative = self.initiative("lifecycle-envelope")
        record = governance.create_record(
            self.root,
            record_type="decision",
            content={"choice": "sealed envelope"},
            initiative_ref=initiative["initiative_ref"],
        )
        database = self.root / ledger_db.DATABASE_NAME
        immutable_trigger = next(
            statement
            for migration in ledger_db._migration_plan()
            if migration.version == 15
            for statement in migration.statements
            if statement.startswith("CREATE TRIGGER governance_record_lifecycle_immutable_update")
        )
        with sqlite3.connect(database) as connection:
            # actor_role is deliberately absent from v11's public SHA-256
            # binding.  It is included in v12's host-keyed full envelope.
            connection.execute("DROP TRIGGER governance_record_lifecycle_immutable_update")
            connection.execute(
                "UPDATE governance_record_lifecycle SET actor_role='worker' WHERE record_ref=?",
                (record["record_ref"],),
            )
            connection.execute(immutable_trigger)
            connection.commit()
        with self.assertRaisesRegex(governance.GovernanceError, "lifecycle authority is invalid") as raised:
            governance.inspect_record(self.root, record["record_ref"])
        self.assertEqual(raised.exception.code, "ledger_corrupt")

    def test_lifecycle_hmac_is_host_private_and_missing_authentication_fails_closed(self) -> None:
        initiative = self.initiative("lifecycle-auth")
        record = governance.create_record(
            self.root,
            record_type="decision",
            content={"choice": "host private key"},
            initiative_ref=initiative["initiative_ref"],
        )
        key_path = ledger_db._governance_lifecycle_key_path(self.root)
        self.assertTrue(key_path.is_file())
        self.assertNotEqual(key_path.parent, self.root)
        self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
        database = self.root / ledger_db.DATABASE_NAME
        with sqlite3.connect(database) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "authentication is append-only"):
                connection.execute(
                    "DELETE FROM governance_record_lifecycle_auth WHERE lifecycle_ref IN "
                    "(SELECT lifecycle_ref FROM governance_record_lifecycle WHERE record_ref=?)",
                    (record["record_ref"],),
                )
            connection.execute("DROP TRIGGER governance_record_lifecycle_auth_immutable_delete")
            connection.execute(
                "DELETE FROM governance_record_lifecycle_auth WHERE lifecycle_ref IN "
                "(SELECT lifecycle_ref FROM governance_record_lifecycle WHERE record_ref=?)",
                (record["record_ref"],),
            )
            auth_delete_trigger = next(
                statement
                for migration in ledger_db._migration_plan()
                if migration.version == 15
                for statement in migration.statements
                if statement.startswith("CREATE TRIGGER governance_record_lifecycle_auth_immutable_delete")
            )
            connection.execute(auth_delete_trigger)
            connection.commit()
        with self.assertRaisesRegex(governance.GovernanceError, "lifecycle authority is invalid"):
            governance.inspect_record(self.root, record["record_ref"])

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

    def test_immutable_artifact_is_the_record_body_and_cache_is_ignored(self) -> None:
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
        # ``content_json`` is retained only as a schema-era migration column;
        # current reads must come from the immutable artifact.
        inspected = governance.inspect_record(self.root, record["record_ref"])
        self.assertEqual(inspected["content_json"], {"choice": "artifact source"})

        # The artifact itself remains authoritative and must fail closed when
        # its immutable blob is tampered with.
        with sqlite3.connect(database) as connection:
            blob_id = connection.execute(
                "SELECT content_artifact_ref FROM governance_records WHERE record_ref=?",
                (record["record_ref"],),
            ).fetchone()[0]
            connection.execute(
                "UPDATE artifact_blob_chunks SET text_content=? WHERE blob_id=? AND chunk_no=0",
                ('{"choice":"artifact tamper"}', blob_id),
            )
            connection.commit()
        with self.assertRaisesRegex(governance.GovernanceError, "immutable artifact is invalid") as raised:
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

    def test_revise_submission_retry_recovers_a_lost_response_without_a_sibling(self) -> None:
        initiative = self.initiative("revise-retry")
        original = governance.create_record(
            self.root, record_type="decision", content={"choice": "first"}, initiative_ref=initiative["initiative_ref"],
        )
        first = governance.revise_record(
            self.root,
            record_ref=original["record_ref"],
            content={"choice": "revised"},
            submission_id="submission-revise-retry",
        )
        replay = governance.revise_record(
            self.root,
            record_ref=original["record_ref"],
            content={"choice": "revised"},
            submission_id="submission-revise-retry",
        )
        self.assertEqual(first["record_ref"], replay["record_ref"])
        self.assertEqual(first["supersedes"], original["record_ref"])
        with self.assertRaisesRegex(governance.GovernanceError, "submission_id replay conflicts"):
            governance.revise_record(
                self.root,
                record_ref=original["record_ref"],
                content={"choice": "different"},
                submission_id="submission-revise-retry",
            )

    def test_lifecycle_status_and_approval_basis_are_append_only_and_tamper_detected(self) -> None:
        initiative = self.initiative("lifecycle")
        record = governance.create_record(
            self.root,
            record_type="decision",
            content={"choice": "authority"},
            initiative_ref=initiative["initiative_ref"],
            approval_basis={"basis": "creation"},
        )
        database = self.root / ledger_db.DATABASE_NAME
        with sqlite3.connect(database) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "lifecycle authority"):
                connection.execute("UPDATE governance_records SET status='approved' WHERE record_ref=?", (record["record_ref"],))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "lifecycle authority"):
                connection.execute("UPDATE governance_records SET approval_basis_json='{}' WHERE record_ref=?", (record["record_ref"],))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute("UPDATE governance_record_lifecycle SET status='approved' WHERE record_ref=?", (record["record_ref"],))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute("DELETE FROM governance_record_lifecycle WHERE record_ref=?", (record["record_ref"],))
            authority_trigger = next(
                statement
                for migration in ledger_db._migration_plan()
                if migration.version == 15
                for statement in migration.statements
                if statement.startswith("CREATE TRIGGER governance_records_lifecycle_authority_update")
            )
            connection.execute("DROP TRIGGER governance_records_lifecycle_authority_update")
            connection.execute("UPDATE governance_records SET status='approved', approval_basis_json='{}' WHERE record_ref=?", (record["record_ref"],))
            connection.execute(authority_trigger)
            connection.commit()
        with self.assertRaisesRegex(governance.GovernanceError, "lifecycle authority is invalid") as raised:
            governance.inspect_record(self.root, record["record_ref"])
        self.assertEqual(raised.exception.code, "ledger_corrupt")

    def test_initiative_completion_requires_terminal_milestone_and_deliverable_success(self) -> None:
        self.add_task("task-8")
        initiative = self.initiative("task-success")
        governance.link_task(self.root, initiative_ref=initiative["initiative_ref"], task_id="task-8", relationship="deliverable")
        governance.transition_initiative(self.root, initiative_ref=initiative["initiative_ref"], status="active")
        advisory = governance.transition_initiative(self.root, initiative_ref=initiative["initiative_ref"], status="completed")
        self.assertFalse(advisory["applied"])
        self.assertTrue(any(item["code"] == "linked_task_unresolved" for item in advisory["advisories"]))
        loaded = ledger_db.load_task(self.root, "task-8")
        assert loaded is not None
        state = loaded[1]
        state["status"] = "completed"
        ledger_db.update_task_state(self.root, state)
        self.assertEqual(
            governance.transition_initiative(self.root, initiative_ref=initiative["initiative_ref"], status="completed")["status"],
            "completed",
        )
        with sqlite3.connect(self.root / ledger_db.DATABASE_NAME) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "terminal initiative"):
                connection.execute("UPDATE tasks SET status='active' WHERE task_id='task-8'")

    def test_governance_scoped_record_prevents_initiative_task_link_deletion(self) -> None:
        self.add_task("task-9")
        initiative = self.initiative("link-delete")
        governance.link_task(self.root, initiative_ref=initiative["initiative_ref"], task_id="task-9", relationship="deliverable")
        governance.create_record(
            self.root,
            record_type="decision",
            content={"choice": "retain link"},
            initiative_ref=initiative["initiative_ref"],
            task_id="task-9",
        )
        with sqlite3.connect(self.root / ledger_db.DATABASE_NAME) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "prevent initiative task link deletion"):
                connection.execute(
                    "DELETE FROM initiative_task_links WHERE initiative_ref=? AND task_id=? AND relationship='deliverable'",
                    (initiative["initiative_ref"], "task-9"),
                )
        self.add_task("task-10")
        projected = self.initiative("link-delete-projection")
        governance.link_task(self.root, initiative_ref=projected["initiative_ref"], task_id="task-10", relationship="milestone")
        record = governance.create_record(
            self.root,
            record_type="decision",
            content={"choice": "linked projection"},
            initiative_ref=projected["initiative_ref"],
        )
        governance.link_record(self.root, record_ref=record["record_ref"], relationship="task", task_id="task-10")
        with sqlite3.connect(self.root / ledger_db.DATABASE_NAME) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "prevent initiative task link deletion"):
                connection.execute(
                    "DELETE FROM initiative_task_links WHERE initiative_ref=? AND task_id=? AND relationship='milestone'",
                    (projected["initiative_ref"], "task-10"),
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
        advisory = governance.transition_initiative(self.root, initiative_ref=source["initiative_ref"], status="completed")
        self.assertFalse(advisory["applied"])
        self.assertTrue(any(item["code"] == "dependency_unresolved" for item in advisory["advisories"]))
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
        loaded = ledger_db.load_task(self.root, "task-7")
        assert loaded is not None
        state = loaded[1]
        state["status"] = "completed"
        ledger_db.update_task_state(self.root, state)
        governance.transition_initiative(self.root, initiative_ref=initiative["initiative_ref"], status="completed")
        artifact = ledger_db.put_artifact(
            self.root, "task-7", kind="evidence", title="reviewed-material.json", mime_type="application/json",
            content='{"reviewed":true}', immutable=True,
        )
        state["attempts"] = [{"attempt_id": "close-review", "gate": "governance_close", "agent": "code_reviewer", "dispatch_ref": "dispatch-close-review", "briefing_digest": "briefing-close-review", "briefing_artifact_ref": "artifact-close-review", "status": "passed", "invalidated": False, "result_baseline_ref": "baseline-close-review", "result_baseline_digest": "a" * 64}]
        ledger_db.update_task_state(self.root, state)
        attempt_protocol.acknowledge_briefing(
            self.root,
            task_id="task-7",
            attempt_id="close-review",
            dispatch_ref="dispatch-close-review",
            digest="briefing-close-review",
        )
        attempt_protocol.record_verification_observation(
            self.root, task_id="task-7", attempt_id="close-review",
            payload={"command": "python -m unittest tests.test_governance_integrity", "exit_code": 0},
        )
        completed_result = attempt_protocol.complete_attempt(
            self.root,
            task_id="task-7",
            attempt_id="close-review",
            status="completed",
            summary="Independent governance close review completed.",
            workspace_observation={
                "baseline_ref": "baseline-close-review",
                "baseline_digest_sha256": "a" * 64,
                "current_digest_sha256": "b" * 64,
                "complete": True,
                "safe_to_attribute": True,
                "changed_files": [],
            },
        )["result"]
        attempt_protocol.finalize_attempt(self.root, task_id="task-7", attempt_id="close-review")
        state["attempts"][0]["attempt_result_ref"] = completed_result["result_ref"]
        ledger_db.update_task_state(self.root, state)
        ledger_db.put_worker_session(self.root, {"task_id": "task-7", "attempt_id": "close-review", "host_agent_id": "reviewer-7", "host_task_name": "review", "host_tool": "spawn_agent", "status": "completed"})
        payload = {
            "task_id": "task-7", "attempt_id": "close-review", "attempt_result_ref": completed_result["result_ref"], "reviewer_identity": "reviewer-7",
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
