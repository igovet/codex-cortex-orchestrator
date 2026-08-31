"""Focused source coverage for the typed clarification-hold aggregate."""
from __future__ import annotations

import os
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "cortex" / "scripts"))

from cortex_runtime.domain_api import (  # noqa: E402
    consume_assignment_evidence, open_assignment as _open_assignment, open_clarification, open_task, record_clarification, publish_plan as _publish_plan,
)
from cortex_runtime.domain_kernel import DecisionAggregate  # noqa: E402
from cortex_runtime.v12_service import V12ServiceError  # noqa: E402
from cortex_runtime.v12_store import V12Store, V12StoreError  # noqa: E402


def open_assignment(*, task_ref: str, mission: dict, **kwargs: object) -> dict:
    responsibility = "planning" if mission.get("profile_name") == "planner" else "delivery"
    return _open_assignment(task_ref=task_ref, mission={**mission, "responsibility": responsibility}, **kwargs)


def publish_plan(*, assignment_ref: str, evidence: dict, **kwargs: object) -> dict:
    store, assignment_id = V12Store.for_record_ref(assignment_ref, label="delegation_id")
    items = store.read_delegation(delegation_id=assignment_id, after_sequence=0, limit=1)["worker_brief"]["effective_contract"]["planning_items"]
    covered = {**evidence, "contract_coverage": [{"item_ref": item["item_ref"], "status": "planned", "verification": ["Fixture mapped this item."]} for item in items]}
    return _publish_plan(assignment_ref=assignment_ref, evidence=covered, **kwargs)


class ClarificationHoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cortex-clarification-hold-")
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.home = self.root / "codex-home"
        self.project.mkdir()
        self.home.mkdir()
        self.previous_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.home)

    def tearDown(self) -> None:
        if self.previous_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.previous_home
        self.temporary.cleanup()

    def test_clarification_cannot_open_without_an_anchored_task(self) -> None:
        with self.assertRaises(V12ServiceError) as failure:
            open_clarification(
                task_ref="t_missing_anchor",
                prompt="Clarify the requirement.",
                prompt_language="en",
            )
        self.assertIn(failure.exception.code, {"invalid_identifier", "task_ref_invalid", "task_not_found", "cross_project_reference"})

    def _task(self) -> dict:
        return open_task(task={"project_root": str(self.project), "objective": "Exercise holds.", "request_original": "Exercise holds.", "user_language": "en", "outcomes": [{"requirement": "Keep the clarification lifecycle exact.", "acceptance": ["One answer creates one receipt."]}], "constraints": ["Never schedule a worker from the ledger."]})["task"]

    def _assignment(self, task_ref: str) -> dict:
        return open_assignment(task_ref=task_ref, mission={"role": "worker", "profile_name": "general", "goal": "Wait for an exact clarification.", "constraints": "Bounded test.", "instructions": "Use the active semantic registry."})

    def test_coordinator_only_hold_is_atomically_answered_without_host_delivery(self) -> None:
        task = self._task()
        store = V12Store(self.project)
        aggregate = DecisionAggregate(store)
        opened = aggregate.open_clarification(
            task_id=task["task_id"], prompt="Шрифт 😀?", prompt_language="ru",
        )
        binding = opened["binding"]["clarification_binding"]
        self.assertEqual(opened["clarification_hold"]["state"], "pending_question")
        self.assertIsNone(store.clarification_host_delivery_projection(task_id=task["task_id"], binding_ref=binding))

        recorded = aggregate.record_clarification(
            task_id=task["task_id"], binding_ref=binding,
            response_original="Оставить UTF-8 ✓", user_language="ru",
        )
        self.assertEqual(recorded["clarification_hold"]["state"], "coordinator_completed")
        replay = DecisionAggregate(V12Store(self.project)).record_clarification(
            task_id=task["task_id"], binding_ref=binding,
            response_original="Оставить UTF-8 ✓", user_language="ru",
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["decision"]["decision_id"], recorded["decision"]["decision_id"])

    def test_v19_migration_chain_is_forward_only_and_complete(self) -> None:
        store = V12Store(self.project)
        with sqlite3.connect(store.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT version,name FROM schema_migrations ORDER BY version DESC LIMIT 1"
                ).fetchone(),
                (24, "v24-outcome-linked-contract"),
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(clarification_holds)")}
        self.assertTrue({
            "clarification_binding", "assignment_id", "native_dispatch_digest",
            "continuation_capability", "response_decision_id", "delivery_claim_digest",
            "state", "opened_sequence", "answered_sequence", "delivery_sequence",
        }.issubset(columns))

    def test_assignment_hold_binds_one_exact_native_dispatch_and_host_delivery(self) -> None:
        task = self._task()
        assignment = self._assignment(task["task_ref"])
        store = V12Store(self.project)
        _, assignment_id = V12Store.for_record_ref(assignment["assignment_ref"], label="delegation_id")
        aggregate = DecisionAggregate(store)
        opened = aggregate.open_clarification(
            task_id=task["task_id"], prompt="Continue with blue?", prompt_language="en",
            assignment_id=assignment_id,
        )
        binding = opened["binding"]["clarification_binding"]
        first = aggregate.record_clarification(
            task_id=task["task_id"], binding_ref=binding,
            response_original="Yes, use blue.", user_language="en",
        )
        self.assertEqual(first["clarification_hold"]["state"], "pending_delivery")
        relation = store.clarification_host_delivery_projection(task_id=task["task_id"], binding_ref=binding)
        self.assertIsNotNone(relation)
        assert relation is not None
        self.assertEqual(relation["assignment_id"], assignment_id)
        self.assertEqual(relation["state"], "pending_delivery")
        self.assertTrue(relation["continuation_capability"].startswith("hc_"))
        with sqlite3.connect(store.database_path) as connection:
            marker = connection.execute("SELECT dispatch_correlation_marker FROM delegations WHERE delegation_id=?", (assignment_id,)).fetchone()[0]
        self.assertRegex(marker, r"^dc_[0-9a-f]{32}$")
        self.assertEqual(relation["dispatch_correlation_marker"], marker)
        self.assertEqual(relation["dispatch_correlation_fingerprint"], "sha256:" + hashlib.sha256(marker.encode("utf-8")).hexdigest())
        expected_dispatch_digest = "sha256:" + hashlib.sha256(json.dumps(
            {"assignment_id": assignment_id, "native_task_name": relation["native_task_name"]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        self.assertEqual(relation["native_dispatch_digest"], expected_dispatch_digest)
        claimed = store.host_clarification_delivery(
            binding_ref=binding, continuation_capability=relation["continuation_capability"],
            host_identity="native-test-worker",
        )
        self.assertFalse(claimed["replayed"])
        self.assertEqual(claimed["native_task_name"], relation["native_task_name"])
        self.assertEqual(claimed["response_original"], "Yes, use blue.")
        complete = store.complete_host_clarification_delivery(
            binding_ref=binding, continuation_capability=relation["continuation_capability"],
            host_identity="native-test-worker", outcome="delivered",
        )
        self.assertFalse(complete["replayed"])
        self.assertEqual(complete["state"], "delivered")
        self.assertTrue(store.complete_host_clarification_delivery(
            binding_ref=binding, continuation_capability=relation["continuation_capability"],
            host_identity="native-test-worker", outcome="delivered",
        )["replayed"])

    def test_native_dispatch_digest_tamper_fails_closed_without_delivery_claim(self) -> None:
        task = self._task()
        assignment = self._assignment(task["task_ref"])
        store = V12Store(self.project)
        _, assignment_id = V12Store.for_record_ref(assignment["assignment_ref"], label="delegation_id")
        opened = DecisionAggregate(store).open_clarification(
            task_id=task["task_id"], assignment_id=assignment_id,
            prompt="Continue?", prompt_language="en",
        )
        binding = opened["binding"]["clarification_binding"]
        DecisionAggregate(store).record_clarification(
            task_id=task["task_id"], binding_ref=binding,
            response_original="Yes.", user_language="en",
        )
        relation = store.clarification_host_delivery_projection(task_id=task["task_id"], binding_ref=binding)
        assert relation is not None
        # Fixture-only corruption: a consumer must reject a proof that no
        # longer matches the independently persisted assignment/native name.
        with sqlite3.connect(store.database_path) as connection:
            connection.execute(
                "UPDATE clarification_holds SET native_dispatch_digest=? WHERE clarification_binding=?",
                ("sha256:" + "0" * 64, binding),
            )
        with self.assertRaises(V12StoreError) as projected:
            store.clarification_host_delivery_projection(task_id=task["task_id"], binding_ref=binding)
        self.assertEqual(projected.exception.code, "ledger_corrupt")
        with self.assertRaises(V12StoreError) as claimed:
            store.host_clarification_delivery(
                binding_ref=binding, continuation_capability=relation["continuation_capability"],
                host_identity="tamper-test-worker",
            )
        self.assertEqual(claimed.exception.code, "ledger_corrupt")

    def test_dispatch_correlation_tamper_fails_closed_without_replacement(self) -> None:
        task = self._task()
        assignment = self._assignment(task["task_ref"])
        store = V12Store(self.project)
        _, assignment_id = V12Store.for_record_ref(assignment["assignment_ref"], label="delegation_id")
        opened = DecisionAggregate(store).open_clarification(
            task_id=task["task_id"], assignment_id=assignment_id,
            prompt="Continue safely?", prompt_language="en",
        )
        binding = opened["binding"]["clarification_binding"]
        DecisionAggregate(store).record_clarification(
            task_id=task["task_id"], binding_ref=binding,
            response_original="Continue.", user_language="en",
        )
        with sqlite3.connect(store.database_path) as connection:
            connection.execute(
                "UPDATE delegations SET dispatch_correlation_digest=? WHERE delegation_id=?",
                ("sha256:" + "0" * 64, assignment_id),
            )
        with self.assertRaises(V12StoreError) as projected:
            store.clarification_host_delivery_projection(task_id=task["task_id"], binding_ref=binding)
        self.assertEqual(projected.exception.code, "ledger_corrupt")

    def test_public_assignment_hold_returns_renderer_owned_delivery_or_unavailable_evidence(self) -> None:
        task = self._task()
        assignment = self._assignment(task["task_ref"])
        opened = open_clarification(
            task_ref=task["task_ref"], prompt="Use a blue accent?", prompt_language="en",
            assignment_ref=assignment["assignment_ref"],
        )
        self.assertEqual(opened["clarification_hold"]["state"], "pending_question")
        recorded = record_clarification(
            task_ref=task["task_ref"], binding_ref=opened["binding_ref"],
            response_original="Yes, use blue.", user_language="en",
        )
        delivery = recorded["host_delivery"]
        self.assertEqual(delivery["state"], "pending_delivery")
        self.assertEqual(delivery["assignment_ref"], assignment["assignment_ref"])
        self.assertIn("Yes, use blue.", delivery["message"])
        self.assertEqual(delivery["renderer"]["version"], "cortex/worker-continuation/v1")
        self.assertTrue(delivery["continuation_capability"].startswith("hc_"))
        self.assertRegex(delivery["native_dispatch_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(delivery["dispatch_correlation_marker"], r"^dc_[0-9a-f]{32}$")
        self.assertRegex(delivery["dispatch_correlation_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertIn(delivery["dispatch_correlation_marker"], delivery["message"])

        store = V12Store(self.project)
        claim = store.host_clarification_delivery(
            binding_ref=opened["binding_ref"], continuation_capability=delivery["continuation_capability"],
            host_identity="unavailable-test-worker",
        )
        self.assertFalse(claim["replayed"])
        unavailable = store.complete_host_clarification_delivery(
            binding_ref=opened["binding_ref"], continuation_capability=delivery["continuation_capability"],
            host_identity="unavailable-test-worker", outcome="unavailable",
            unavailable_reason="host did not retain the exact worker",
        )
        self.assertEqual(unavailable["state"], "unavailable")
        replay = record_clarification(
            task_ref=task["task_ref"], binding_ref=opened["binding_ref"],
            response_original="Yes, use blue.", user_language="en",
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["host_delivery"]["state"], "unavailable")
        self.assertEqual(replay["host_delivery"]["unavailable_reason"], "host did not retain the exact worker")
        self.assertEqual(replay["host_delivery"]["native_dispatch_digest"], delivery["native_dispatch_digest"])

    def test_first_accepted_exact_assignment_publication_reconciles_pending_delivery(self) -> None:
        """No host hook exists: only the exact worker's durable publication proves continuation."""
        task = self._task()
        assignment = open_assignment(task_ref=task["task_ref"], mission={"role": "planner", "profile_name": "planner", "goal": "Produce the plan after clarification.", "constraints": "Bounded planning test.", "instructions": "Use the active semantic registry."})
        opened = open_clarification(
            task_ref=task["task_ref"], assignment_ref=assignment["assignment_ref"],
            prompt="Continue after the answer?", prompt_language="en",
        )
        binding = opened["binding_ref"]
        recorded = record_clarification(
            task_ref=task["task_ref"], binding_ref=binding,
            response_original="Continue.", user_language="en",
        )
        self.assertEqual(recorded["clarification_hold"]["state"], "pending_delivery")
        consumed = consume_assignment_evidence(
            assignment_ref=assignment["assignment_ref"],
        )
        continuation = consumed["continuation_ref"]
        published = publish_plan(
            continuation_ref=continuation,
            assignment_ref=assignment["assignment_ref"],
            evidence={
                "schema": "cortex/report/plan/v3", "summary": "Complete plan.",
                "scope": "Complete contract.",
                "stages": [{"order": 1, "owner": "planner", "dependencies": [],
                            "work": ["Map every requirement."], "verification": ["Check every item."]}],
                "verification": ["Inspect every criterion."], "risks": [], "deviations": [], "unresolved": [],
                "verification_facts": [{"state": "not_run", "summary": "Planning does not execute project commands."}],
                "documentation_impact": "No documentation changed; no affected paths.",
            },
        )
        self.assertFalse(published["replayed"])
        store = V12Store(self.project)
        relation = store.clarification_host_delivery_projection(task_id=task["task_id"], binding_ref=binding)
        self.assertIsNotNone(relation)
        assert relation is not None
        self.assertEqual(relation["state"], "delivered")
        self.assertIsNone(relation["unavailable_reason"])
        # The replay does not emit another reconciliation event or mutate the hold.
        self.assertTrue(publish_plan(
            continuation_ref=continuation,
            assignment_ref=assignment["assignment_ref"],
            evidence={
                "schema": "cortex/report/plan/v3", "summary": "Complete plan.",
                "scope": "Complete contract.",
                "stages": [{"order": 1, "owner": "planner", "dependencies": [],
                            "work": ["Map every requirement."], "verification": ["Check every item."]}],
                "verification": ["Inspect every criterion."], "risks": [], "deviations": [], "unresolved": [],
                "verification_facts": [{"state": "not_run", "summary": "Planning does not execute project commands."}],
                "documentation_impact": "No documentation changed; no affected paths.",
            },
        )["replayed"])

    def test_concurrent_open_and_record_reconcile_one_hold_after_restart(self) -> None:
        task = self._task()
        bindings: list[str] = []
        failures: list[BaseException] = []

        def open_one() -> None:
            try:
                result = DecisionAggregate(V12Store(self.project)).open_clarification(
                    task_id=task["task_id"], prompt="One logical hold?", prompt_language="en",
                )
                bindings.append(result["binding"]["clarification_binding"])
            except BaseException as exc:  # retained for assertion
                failures.append(exc)

        workers = [threading.Thread(target=open_one) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(failures, [])
        self.assertEqual(len(set(bindings)), 1)
        binding = bindings[0]
        results: list[dict] = []

        def record_one() -> None:
            results.append(DecisionAggregate(V12Store(self.project)).record_clarification(
                task_id=task["task_id"], binding_ref=binding,
                response_original="Yes", user_language="en",
            ))

        workers = [threading.Thread(target=record_one) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual({result["decision"]["decision_id"] for result in results}, {results[0]["decision"]["decision_id"]})
        self.assertEqual(sum(bool(result["replayed"]) for result in results), 1)
        reconciled = DecisionAggregate(V12Store(self.project)).reconcile(task_id=task["task_id"], binding_ref=binding)
        self.assertEqual(reconciled["state"], "consumed")

    def test_cross_task_and_changed_answer_fail_without_replacement_hold(self) -> None:
        first_task = self._task()
        other = self.root / "other"
        other.mkdir()
        second = open_task(task={"project_root": str(other), "objective": "Other", "request_original": "Other", "user_language": "en", "outcomes": [{"requirement": "r", "acceptance": ["a"]}], "constraints": ["c"]})["task"]
        aggregate = DecisionAggregate(V12Store(self.project))
        opened = aggregate.open_clarification(task_id=first_task["task_id"], prompt="Exact?", prompt_language="en")
        binding = opened["binding"]["clarification_binding"]
        aggregate.record_clarification(task_id=first_task["task_id"], binding_ref=binding, response_original="Yes", user_language="en")
        with self.assertRaises(V12StoreError) as changed:
            aggregate.record_clarification(task_id=first_task["task_id"], binding_ref=binding, response_original="No", user_language="en")
        self.assertEqual(changed.exception.code, "command_conflict")
        with self.assertRaises(V12StoreError) as cross:
            DecisionAggregate(V12Store(other)).record_clarification(
                task_id=second["task_id"], binding_ref=binding, response_original="Yes", user_language="en",
            )
        self.assertIn(cross.exception.code, {"clarification_binding_not_found", "cross_project_reference"})


if __name__ == "__main__":
    unittest.main()
