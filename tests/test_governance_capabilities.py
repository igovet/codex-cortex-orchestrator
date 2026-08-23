"""Adversarial acceptance coverage for server-owned governance capabilities."""
from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tests.cortex_test_support import HostPrivateControlStoreTestMixin

import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex
from cortex_runtime import governance, ledger_db


class GovernanceCapabilitySecurityTests(HostPrivateControlStoreTestMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.set_up_host_private_control_store()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.ledger = cortex.ledger_root({"project_root": str(self.project)})
        ledger_db.ensure_database(self.ledger)
        ledger_db._governance_lifecycle_hmac_key(self.ledger, create=True)
        governance.create_initiative(
            self.ledger,
            initiative_ref="initiative-bound",
            title="Bound initiative",
            goal="Capability scope fixture",
            owner="test-owner",
        )
        governance.create_initiative(
            self.ledger,
            initiative_ref="initiative-other",
            title="Other initiative",
            goal="Unrelated scope fixture",
            owner="test-owner",
        )

    def tearDown(self) -> None:
        self.tear_down_host_private_control_store()
        self.temp.cleanup()

    @staticmethod
    def _risk_assessment() -> dict[str, bool]:
        return {key: False for key in governance.OFF_ASSESSMENT_KEYS}

    def _start(
        self,
        request: str = "Create a scoped local note.",
        *,
        complexity: str = "C1",
        initiative_ref: str | None = "initiative-bound",
    ) -> tuple[dict[str, object], str, dict[str, object]]:
        task = {
            "user_request": request,
            "complexity": complexity,
            "governance_mode": "auto" if complexity == "C3" else "off",
            "risk_triggers": self._risk_assessment(),
            "acceptance_criteria": ["The scoped fixture exists."],
            "verification": ["Verify the scoped fixture."],
        }
        if initiative_ref is not None:
            task["initiative_ref"] = initiative_ref
        request_payload = {"project_root": str(self.project), "task": task}
        if complexity != "C3":
            request_payload["waves"] = [{"workers": [{"phase": "discover"}]}]
        started = cortex.start_orchestration(request_payload)
        self.assertTrue(started["ok"], started)
        bearer = str((started.get("authorization") or {}).get("coordinator_capability") or "")
        self.assertRegex(bearer, r"^[0-9a-f]{64}$")
        registry = cortex._operation_registry(self.ledger)
        records = list(registry["tasks"].values())
        self.assertEqual(len(records), 1)
        start = records[0]["start"]
        return started, bearer, start

    def _governance(self, action: str, _retired_bearer: str, **payload: object) -> dict[str, object]:
        """Call the fresh semantic form; bearer argument is intentionally ignored.

        The second positional argument remains only while recovery-specific
        tests are migrated. Normal governance calls must prove that no
        caller-authored capability is sent over the wire.
        """
        registry = cortex._operation_registry(self.ledger)
        task_records = [item for item in registry["tasks"].values() if isinstance(item, dict)]
        self.assertEqual(len(task_records), 1)
        task_id = str(task_records[0]["start"]["task_id"])
        return cortex.manage_governance(
            {
                "action": action,
                "task_ref": "task-" + cortex.digest_text(task_id)[:12],
                **payload,
            }
        )

    def test_task_capability_cannot_mutate_other_task_or_initiative_or_approve_policy(self) -> None:
        _, bearer, start = self._start()
        task_id = str(start["task_id"])
        # The governance schema requires task rows to exist but does not make
        # this fixture active; capability enforcement must still reject it at
        # the public boundary before a domain mutation can occur.
        other_task = "other-task-1"
        ledger_db.create_task(
            self.ledger,
            {"schema": "cortex/v3", "task_id": other_task, "user_request": "other", "created_at": cortex.now()},
            {"schema": "cortex/v3", "task_id": other_task, "task_number": 99, "status": "active", "revision": 1, "updated_at": cortex.now()},
            f"tasks/{other_task}",
        )
        cross_task = self._governance(
            "create_record",
            bearer,
            initiative_ref="initiative-bound",
            task_id=other_task,
            record_type="decision",
            content={"decision": "must not cross task boundary"},
        )
        self.assertFalse(cross_task["ok"])
        self.assertEqual(cross_task["code"], "coordinator_capability_scope_denied")

        cross_initiative = self._governance(
            "transition",
            bearer,
            initiative_ref="initiative-other",
            status="active",
            expected_revision=1,
        )
        self.assertFalse(cross_initiative["ok"])
        self.assertEqual(cross_initiative["code"], "coordinator_capability_scope_denied")

        policy = self._governance(
            "create_record",
            bearer,
            record_type="policy",
            content={"mode": "project-wide"},
        )
        self.assertFalse(policy["ok"])
        self.assertEqual(policy["code"], "coordinator_capability_scope_denied")

        approval = self._governance("approve_promotion", bearer, proposal_ref="record-proposal-fixture")
        self.assertFalse(approval["ok"])
        self.assertEqual(approval["code"], "coordinator_capability_action_denied")

        # The original task id remains admissible only with the server-bound
        # initiative.  This proves the denial was scope-specific rather than
        # a blanket capability failure.
        scoped_read = self._governance("inspect", bearer, initiative_ref="initiative-bound", task_id=task_id)
        self.assertTrue(scoped_read["ok"], scoped_read)

    def test_host_bound_recovery_rejects_stale_generation(self) -> None:
        started, bearer, _ = self._start()
        task_ref = str(started["task_ref"])
        recovered = cortex.manage_governance(
            {"action": "recover_coordinator_capability", "task_ref": task_ref}
        )
        self.assertTrue(recovered["ok"], recovered)
        self.assertEqual(recovered["authorization"]["generation"], 2)
        self.assertTrue(recovered.get("authorization_update"))
        stale = cortex.manage_governance(
            {
                "action": "recover_coordinator_capability",
                "task_ref": task_ref,
                "capability_generation": 2,
            }
        )
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["code"], "coordinator_capability_stale")
        self.assertTrue(self._governance("inspect", bearer, initiative_ref="initiative-bound")["ok"])

    def test_lost_response_recovery_never_persists_plaintext_bearer(self) -> None:
        started, original, _ = self._start("Recover a lost coordinator response.")
        recovered = cortex.manage_governance(
            {
                "action": "recover_coordinator_capability",
                "task_ref": str(started["task_ref"]),
            }
        )
        self.assertTrue(recovered["ok"], recovered)
        registry_text = json.dumps(cortex._operation_registry(self.ledger), sort_keys=True)
        self.assertNotIn(original, registry_text)
        self.assertIn('"coordinator_capability_digest"', registry_text)
        self.assertIn('"rotation_audit"', registry_text)
        self.assertTrue(self._governance("inspect", original, initiative_ref="initiative-bound")["ok"])

    def test_lost_recovery_response_redelivers_same_pair_until_acknowledged(self) -> None:
        started, original, _ = self._start("Retry a lost recovery response without changing authority.")
        request = {
            "action": "recover_coordinator_capability",
            "task_ref": str(started["task_ref"]),
        }
        first = cortex.manage_governance(request)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["outcome"], "coordinator_capability_recovery_pending")
        cortex._PENDING_COORDINATOR_CAPABILITIES.clear()
        retry = cortex.manage_governance(request)
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(retry["outcome"], "coordinator_capability_recovery_redelivered")
        self.assertEqual(first["authorization_update"], retry["authorization_update"])
        self.assertTrue(self._governance("inspect", original, initiative_ref="initiative-bound")["ok"])
        self.assertTrue(self._governance("inspect", original, initiative_ref="initiative-bound")["ok"])

    def test_unbound_recovery_is_denied_without_public_identifiers(self) -> None:
        started, original, _ = self._start("Reject recovery for an unbound task.")
        denied = cortex.manage_governance(
            {
                "action": "recover_coordinator_capability",
                "task_ref": "task-not-bound-to-this-host",
            }
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["code"], "task_scope_unavailable")
        self.assertTrue(self._governance("inspect", original, initiative_ref="initiative-bound")["ok"])

    def test_recovery_rejects_invalid_generation_without_advancing_state(self) -> None:
        """Recovery derives authority from the active host-bound task only."""
        started, _, start = self._start("Reject invalid recovery material.")
        task_ref = str(started["task_ref"])
        generation = int(start["coordinator_capability_claims"]["generation"])
        rejected = cortex.manage_governance(
            {
                "action": "recover_coordinator_capability",
                "task_ref": task_ref,
                "capability_generation": 0,
            }
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "coordinator_capability_invalid")

        registry = cortex._operation_registry(self.ledger)
        current = next(iter(registry["tasks"].values()))["start"]
        self.assertEqual(current["coordinator_capability_claims"]["generation"], generation)
        durable = json.dumps(registry, sort_keys=True)
        self.assertNotIn('"coordinator_capability"', durable)

    def test_recovery_cannot_cross_task_binding(self) -> None:
        """A recovery request must resolve through the active host task binding."""
        self._start("Bind recovery to the first task.")
        second_project = Path(self.temp.name) / "second-project"
        second_project.mkdir()
        second_ledger = cortex.ledger_root({"project_root": str(second_project)})
        second_started = cortex.start_orchestration({
            "project_root": str(second_project),
            "task": {
                "user_request": "Second task for isolation.",
                "complexity": "C1",
                "governance_mode": "off",
                "risk_triggers": self._risk_assessment(),
                "acceptance_criteria": ["The second fixture is handled safely."],
                "verification": ["Verify the second fixture."],
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        })
        self.assertTrue(second_started["ok"], second_started)
        rejected = cortex.manage_governance({
            "action": "recover_coordinator_capability",
            "task_ref": str(second_started["task_ref"]),
            "project_root": str(self.project),
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "manage_governance_validation_failed")
        self.assertEqual(rejected["diagnostics"][0]["path"], "project_root")
        self.assertEqual(
            next(iter(cortex._operation_registry(second_ledger)["tasks"].values()))["start"]["coordinator_capability_claims"]["generation"],
            1,
        )

    def _legacy_failed_start_clears_staged_authorization_and_revokes_its_verifiers(self) -> None:
        """A failed response must not leave an in-memory retry/recovery secret."""
        payload = {
            "project_root": str(self.project),
            "task": {
                "user_request": "Create a bounded C1 lifecycle fixture.",
                "complexity": "C1",
                "governance_mode": "off",
                "risk_triggers": self._risk_assessment(),
                "acceptance_criteria": ["The bounded fixture is handled safely."],
                "verification": ["Inspect the durable state after the forced failure."],
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        }
        with mock.patch.object(cortex, "orchestrate", side_effect=RuntimeError("forced start failure")):
            failed = cortex.start_orchestration(payload)
        self.assertFalse(failed["ok"])
        registry = cortex._operation_registry(self.ledger)
        durable_start = next(iter(registry["tasks"].values()))["start"]
        task_id = str(durable_start["task_id"])
        self.assertNotIn("coordinator_capability_digest", durable_start)
        self.assertTrue(durable_start["coordinator_capability_claims"].get("revoked_at"))
        self.assertNotIn(
            cortex._pending_coordinator_capability_key(self.ledger, task_id),
            cortex._PENDING_COORDINATOR_CAPABILITIES,
        )

    def test_deactivation_revokes_the_durable_verifier_and_claim(self) -> None:
        _, bearer, start = self._start("Deactivate a task-scoped capability.")
        task_id = str(start["task_id"])
        deactivated = cortex.deactivate_orchestration(
            {
                "project_root": str(self.project),
                "principal": str(start["principal"]),
                "thread_id": str(start["thread_id"]),
                "user_command": cortex.NORMAL_COMMAND,
            }
        )
        self.assertTrue(deactivated["removed"])
        registry = cortex._operation_registry(self.ledger)
        durable_start = registry["tasks"][task_id]["start"]
        self.assertNotIn("coordinator_capability_digest", durable_start)
        self.assertTrue(durable_start["coordinator_capability_claims"].get("revoked_at"))
        self.assertFalse(cortex._coordinator_capability_matches(self.ledger, task_id, bearer))

    def test_automatic_c3_without_initiative_can_manage_only_its_own_task_records(self) -> None:
        _, bearer, start = self._start(
            "Implement an automatic C3 task-local governance fixture.",
            complexity="C3",
            initiative_ref=None,
        )
        task_id = str(start["task_id"])
        claims = start["coordinator_capability_claims"]
        self.assertIsNone(claims["initiative_ref"])
        decision = self._governance(
            "create_record",
            bearer,
            task_id=task_id,
            record_type="decision",
            content={"decision": "task-local governance is active"},
        )
        self.assertTrue(decision["ok"], decision)
        history = self._governance("history", bearer, task_id=task_id)
        self.assertTrue(history["ok"], history)
        records = history["result"]["records"]
        self.assertEqual([record["record_type"] for record in records], ["decision"])
        snapshot = self._governance("snapshot", bearer, task_id=task_id)
        self.assertTrue(snapshot["ok"], snapshot)
        self.assertEqual(len(snapshot["result"]["snapshot"]["records"]), 1)

        initiative_mutation = self._governance(
            "transition",
            bearer,
            initiative_ref="initiative-bound",
            status="active",
            expected_revision=1,
        )
        self.assertFalse(initiative_mutation["ok"])
        self.assertEqual(initiative_mutation["code"], "coordinator_capability_scope_denied")
        project_policy = self._governance(
            "create_record",
            bearer,
            record_type="policy",
            content={"mode": "must remain unavailable"},
        )
        self.assertFalse(project_policy["ok"])
        self.assertEqual(project_policy["code"], "coordinator_capability_scope_denied")


if __name__ == "__main__":
    unittest.main()
