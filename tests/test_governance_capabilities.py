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

    def _governance(self, action: str, bearer: str, **payload: object) -> dict[str, object]:
        return cortex.manage_governance(
            {
                "project_root": str(self.project),
                "action": action,
                "coordinator_capability": bearer,
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
            {"schema": "cortex/v3", "task_id": other_task, "objective": "other", "created_at": cortex.now()},
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

    def test_old_generation_is_revoked_and_project_admin_requires_explicit_server_grant(self) -> None:
        started, bearer, start = self._start()
        task_ref = str(started["task_ref"])
        principal = str(start["principal"])
        thread_id = str(start["thread_id"])
        recovery_proof = str(
            (started.get("authorization") or {}).get("coordinator_recovery_proof") or ""
        )
        self.assertRegex(recovery_proof, r"^[0-9a-f]{64}$")
        rotated = cortex.manage_governance(
            {
                "project_root": str(self.project),
                "action": "recover_coordinator_capability",
                "task_ref": task_ref,
                "principal": principal,
                "thread_id": thread_id,
                "coordinator_recovery_proof": recovery_proof,
                "capability_generation": 1,
            }
        )
        self.assertTrue(rotated["ok"], rotated)
        renewed = str((rotated.get("authorization_update") or {}).get("coordinator_capability") or "")
        renewed_recovery_proof = str(
            (rotated.get("authorization_update") or {}).get("coordinator_recovery_proof") or ""
        )
        self.assertRegex(renewed, r"^[0-9a-f]{64}$")
        self.assertRegex(renewed_recovery_proof, r"^[0-9a-f]{64}$")
        self.assertNotEqual(renewed, bearer)
        self.assertNotEqual(renewed_recovery_proof, recovery_proof)

        acknowledged = cortex.manage_governance(
            {
                "project_root": str(self.project),
                "action": "acknowledge_coordinator_recovery",
                "task_ref": task_ref,
                "principal": principal,
                "thread_id": thread_id,
                "coordinator_capability": renewed,
                "coordinator_recovery_proof": renewed_recovery_proof,
                "previous_coordinator_recovery_proof": recovery_proof,
                "capability_generation": 2,
            }
        )
        self.assertTrue(acknowledged["ok"], acknowledged)

        old_generation = self._governance("inspect", bearer, initiative_ref="initiative-bound")
        self.assertFalse(old_generation["ok"])
        self.assertEqual(old_generation["code"], "coordinator_capability_invalid")
        stale_recovery = cortex.manage_governance(
            {
                "project_root": str(self.project),
                "action": "recover_coordinator_capability",
                "task_ref": task_ref,
                "principal": principal,
                "thread_id": thread_id,
                "coordinator_recovery_proof": renewed_recovery_proof,
                "capability_generation": 1,
            }
        )
        self.assertFalse(stale_recovery["ok"])
        self.assertEqual(stale_recovery["code"], "coordinator_capability_stale")

        self.assertRaises(
            ValueError,
            cortex._issue_project_admin_coordinator_capability,
            self.ledger,
            task_id=str(start["task_id"]),
            principal=principal,
            thread_id=thread_id,
        )
        admin = cortex._issue_project_admin_coordinator_capability(
            self.ledger,
            task_id=str(start["task_id"]),
            principal=principal,
            thread_id=thread_id,
            explicit_server_grant=True,
        )
        project_policy = self._governance(
            "create_record",
            admin,
            record_type="policy",
            content={"mode": "explicit-server-admin"},
        )
        self.assertTrue(project_policy["ok"], project_policy)

    def test_lost_response_recovery_never_persists_plaintext_bearer(self) -> None:
        started, original, start = self._start("Recover a lost coordinator response.")
        recovery_proof = str(
            (started.get("authorization") or {}).get("coordinator_recovery_proof") or ""
        )
        self.assertRegex(recovery_proof, r"^[0-9a-f]{64}$")
        recovered = cortex.manage_governance(
            {
                "project_root": str(self.project),
                "action": "recover_coordinator_capability",
                "task_ref": str(started["task_ref"]),
                "principal": str(start["principal"]),
                "thread_id": str(start["thread_id"]),
                "coordinator_recovery_proof": recovery_proof,
            }
        )
        self.assertTrue(recovered["ok"], recovered)
        replacement = str((recovered.get("authorization_update") or {}).get("coordinator_capability") or "")
        self.assertRegex(replacement, r"^[0-9a-f]{64}$")
        registry_text = json.dumps(cortex._operation_registry(self.ledger), sort_keys=True)
        self.assertNotIn(original, registry_text)
        self.assertNotIn(replacement, registry_text)
        self.assertNotIn(recovery_proof, registry_text)
        self.assertNotIn('"coordinator_capability"', registry_text)
        self.assertNotIn('"coordinator_recovery_proof"', registry_text)
        self.assertIn('"coordinator_capability_digest"', registry_text)
        self.assertIn('"coordinator_recovery_proof_digest"', registry_text)
        self.assertIn('"rotation_audit"', registry_text)
        # Before acknowledgement both generations remain usable: a lost MCP
        # response can safely be retried with the original proof.
        self.assertTrue(self._governance("inspect", original, initiative_ref="initiative-bound")["ok"])
        acknowledged = cortex.manage_governance(
            {
                "project_root": str(self.project),
                "action": "acknowledge_coordinator_recovery",
                "task_ref": str(started["task_ref"]),
                "principal": str(start["principal"]),
                "thread_id": str(start["thread_id"]),
                "coordinator_capability": replacement,
                "coordinator_recovery_proof": str(
                    (recovered.get("authorization_update") or {}).get("coordinator_recovery_proof") or ""
                ),
                "previous_coordinator_recovery_proof": recovery_proof,
                "capability_generation": 2,
            }
        )
        self.assertTrue(acknowledged["ok"], acknowledged)
        self.assertFalse(self._governance("inspect", original, initiative_ref="initiative-bound")["ok"])
        self.assertTrue(
            self._governance("inspect", replacement, initiative_ref="initiative-bound")["ok"]
        )

    def test_lost_recovery_response_redelivers_same_pair_until_acknowledged(self) -> None:
        started, original, start = self._start("Retry a lost recovery response without changing authority.")
        request = {
            "project_root": str(self.project),
            "action": "recover_coordinator_capability",
            "task_ref": str(started["task_ref"]),
            "principal": str(start["principal"]),
            "thread_id": str(start["thread_id"]),
            "coordinator_recovery_proof": str(
                (started.get("authorization") or {}).get("coordinator_recovery_proof") or ""
            ),
            "capability_generation": 1,
        }
        first = cortex.manage_governance(request)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["outcome"], "coordinator_capability_recovery_pending")
        # Simulate a process restart: no raw replacement was kept in memory.
        cortex._PENDING_COORDINATOR_CAPABILITIES.clear()
        retry = cortex.manage_governance(request)
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(retry["outcome"], "coordinator_capability_recovery_redelivered")
        self.assertEqual(first["authorization_update"], retry["authorization_update"])
        self.assertTrue(self._governance("inspect", original, initiative_ref="initiative-bound")["ok"])
        update = first["authorization_update"]
        acknowledged = cortex.manage_governance(
            {
                "project_root": str(self.project),
                "action": "acknowledge_coordinator_recovery",
                "task_ref": request["task_ref"],
                "principal": request["principal"],
                "thread_id": request["thread_id"],
                "coordinator_capability": update["coordinator_capability"],
                "coordinator_recovery_proof": update["coordinator_recovery_proof"],
                "previous_coordinator_recovery_proof": request["coordinator_recovery_proof"],
                "capability_generation": 2,
            }
        )
        self.assertTrue(acknowledged["ok"], acknowledged)
        self.assertFalse(self._governance("inspect", original, initiative_ref="initiative-bound")["ok"])
        self.assertTrue(
            self._governance("inspect", update["coordinator_capability"], initiative_ref="initiative-bound")["ok"]
        )

    def test_recovery_acknowledgement_cannot_be_obtained_from_public_identifiers(self) -> None:
        started, original, start = self._start("Reject task-ref-only recovery acknowledgement.")
        denied = cortex.manage_governance(
            {
                "project_root": str(self.project),
                "action": "acknowledge_coordinator_recovery",
                "task_ref": str(started["task_ref"]),
                "principal": str(start["principal"]),
                "thread_id": str(start["thread_id"]),
            }
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["code"], "coordinator_recovery_delivery_unavailable")
        self.assertTrue(self._governance("inspect", original, initiative_ref="initiative-bound")["ok"])

    def test_recovery_rejects_malformed_or_wrong_proof_without_advancing_generation(self) -> None:
        """Recovery input is a verifier, never an identifier-only lookup.

        This is deliberately exercised before the valid rotation path: a
        transport retry, truncated proof, or proof copied from another task
        must not mutate the durable generation or replace its verifier.  The
        assertion is also a guard against accidentally making ``task_ref`` or
        the public principal/thread tuple a recovery capability (the P0
        regression that the default ``compat`` surface must not reintroduce).
        """
        started, _, start = self._start("Reject invalid recovery material.")
        task_ref = str(started["task_ref"])
        principal = str(start["principal"])
        thread_id = str(start["thread_id"])
        generation = int(start["coordinator_capability_claims"]["generation"])
        original_digest = str(start["coordinator_recovery_proof_digest"])

        invalid_proofs = [
            "",
            "not-a-proof",
            "0" * 63,
            "0" * 65,
            "g" * 64,
        ]
        for invalid in invalid_proofs:
            rejected = self._governance(
                "recover_coordinator_capability",
                "",
                task_ref=task_ref,
                principal=principal,
                thread_id=thread_id,
                coordinator_recovery_proof=invalid,
                capability_generation=generation,
            )
            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(rejected["code"], "coordinator_recovery_proof_required")

        wrong_identity = self._governance(
            "recover_coordinator_capability",
            "",
            task_ref=task_ref,
            principal=principal,
            thread_id="different-thread",
            coordinator_recovery_proof="0" * 64,
            capability_generation=generation,
        )
        self.assertFalse(wrong_identity["ok"])
        self.assertEqual(wrong_identity["code"], "coordinator_authorization_required")

        registry = cortex._operation_registry(self.ledger)
        current = next(iter(registry["tasks"].values()))["start"]
        self.assertEqual(current["coordinator_capability_claims"]["generation"], generation)
        self.assertEqual(current["coordinator_recovery_proof_digest"], original_digest)
        durable = json.dumps(registry, sort_keys=True)
        self.assertNotIn('"coordinator_recovery_proof"', durable)
        self.assertNotIn('"coordinator_capability"', durable)

    def test_recovery_proof_cannot_cross_task_boundary(self) -> None:
        """A valid proof is bound to its own task activation and identity."""
        first, _, first_start = self._start("Bind recovery proof to first task.")
        # The fixture normally creates one task per setUp; create a second
        # isolated project/ledger through the public setup so that the proof
        # is valid material but belongs to a different activation.
        second_project = Path(self.temp.name) / "second-project"
        second_project.mkdir()
        second_ledger = cortex.ledger_root({"project_root": str(second_project)})
        second_started = cortex.start_orchestration({
            "project_root": str(second_project),
            "task": {
                "user_request": "Second task for proof isolation.",
                "complexity": "C1",
                "governance_mode": "off",
                "risk_triggers": self._risk_assessment(),
                "acceptance_criteria": ["The second fixture is handled safely."],
                "verification": ["Verify the second fixture."],
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        })
        self.assertTrue(second_started["ok"], second_started)
        second_start = next(iter(cortex._operation_registry(second_ledger)["tasks"].values()))["start"]
        first_proof = str((first.get("authorization") or {}).get("coordinator_recovery_proof") or "")
        rejected = cortex.manage_governance({
            "project_root": str(second_project),
            "action": "recover_coordinator_capability",
            "task_ref": str(second_started["task_ref"]),
            "principal": str(second_start["principal"]),
            "thread_id": str(second_start["thread_id"]),
            "coordinator_recovery_proof": first_proof,
            "capability_generation": int(second_start["coordinator_capability_claims"]["generation"]),
        })
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "coordinator_recovery_proof_required")
        self.assertEqual(
            next(iter(cortex._operation_registry(second_ledger)["tasks"].values()))["start"]["coordinator_capability_claims"]["generation"],
            1,
        )

    def test_failed_start_clears_staged_authorization_and_revokes_its_verifiers(self) -> None:
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
        self.assertNotIn("coordinator_recovery_proof_digest", durable_start)
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
