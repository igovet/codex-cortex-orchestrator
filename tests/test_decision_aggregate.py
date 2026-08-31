"""Phase D black-box contract for the server-owned decision aggregate."""
from __future__ import annotations

import tempfile
import re
import os
import threading
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))

from cortex_runtime.domain_api import (  # noqa: E402
    consume_assignment_evidence,
    open_assignment as _open_assignment,
    open_task,
    publish_plan as _publish_plan,
    read_task,
)
from cortex_runtime.domain_kernel import DecisionAggregate  # noqa: E402
from cortex_runtime.v12_store import V12Store, V12StoreError  # noqa: E402


def open_assignment(*, task_ref: str, mission: dict, **kwargs: object) -> dict:
    return _open_assignment(task_ref=task_ref, mission={**mission, "responsibility": "planning"}, **kwargs)


def publish_plan(*, assignment_ref: str, evidence: dict, **kwargs: object) -> dict:
    store, assignment_id = V12Store.for_record_ref(assignment_ref, label="delegation_id")
    items = store.read_delegation(delegation_id=assignment_id, after_sequence=0, limit=1)["worker_brief"]["effective_contract"]["planning_items"]
    covered = {**evidence, "contract_coverage": [{"item_ref": item["item_ref"], "status": "planned", "verification": ["Fixture mapped this item."]} for item in items]}
    return _publish_plan(assignment_ref=assignment_ref, evidence=covered, **kwargs)


class DecisionAggregateTests(unittest.TestCase):
    def _ready_plan(self, root: str) -> tuple[dict, V12Store, DecisionAggregate, str]:
        """Create one finalized plan and materialize its approval view."""
        task = open_task(task={"project_root": root, "objective": "test", "request_original": "test", "user_language": "en", "outcomes": [{"requirement": "r", "acceptance": ["a"]}], "constraints": ["c"]})["task"]
        assignment = open_assignment(task_ref=task["task_ref"], mission={"role": "planner", "profile_name": "planner", "goal": "Plan the bounded test.", "constraints": "One plan.", "instructions": "Inspect only the assigned task."})
        consumed = consume_assignment_evidence(
            assignment_ref=assignment["assignment_ref"],
        )
        continuation = consumed["continuation_ref"]
        evidence = {
            "schema": "cortex/report/plan/v3", "summary": "Bounded plan.",
            "scope": "Test relation persistence.",
            "stages": [{"order": 1, "owner": "planner", "dependencies": [],
                        "work": ["Map the requirement."],
                        "verification": ["Inspect the relation."]}],
            "verification": ["Inspect the relation."], "risks": [],
            "deviations": [], "unresolved": [],
            "verification_facts": [{"state": "not_run", "summary": "Planning does not execute commands."}],
            "documentation_impact": "No files changed; no documentation impact.",
        }
        plan = publish_plan(continuation_ref=continuation, assignment_ref=assignment["assignment_ref"], evidence=evidence)
        # Publication owns the immutable ready relation; resolving the compact
        # report ref here is test-only access to the aggregate's private
        # canonical anchor.
        read_task(task_ref=task["task_ref"])
        store = V12Store(root)
        _, plan_id = V12Store.for_record_ref(plan["report"]["report_ref"], label="report_id")
        return task, store, DecisionAggregate(store), plan_id

    @staticmethod
    def _introduce_newer_plan_view(
        store: V12Store, *, task_id: str, plan_id: str, relation: dict,
        suffix: str,
    ) -> None:
        """Model a later ready view without changing the issued binding.

        The record command must continue to use ``relation``.  This fixture
        deliberately installs a newer view and distinct approval handle so a
        current/latest lookup would be observable.
        """
        def write(connection):
            relative = f"plans/revisions/{plan_id}.md"
            row = connection.execute(
                "SELECT source_sequence FROM projection_files WHERE task_id=? AND relative_path=?",
                (task_id, relative),
            ).fetchone()
            if row is None:
                raise AssertionError("expected a ready plan projection before forcing a newer view")
            next_sequence = int(row["source_sequence"]) + 1
            next_digest = "sha256:" + (suffix * 64)[:64]
            connection.execute(
                "UPDATE projection_files SET source_sequence=?,content_digest=?,status='ready' WHERE task_id=? AND relative_path=?",
                (next_sequence, next_digest, task_id, relative),
            )
            connection.execute(
                "INSERT INTO approval_handles(approval_handle,project_hash,task_id,report_id,report_content_digest,view_relative_path,view_content_digest,view_source_sequence,request_digest,created_at,created_sequence,consumed_decision_id) "
                "SELECT ?,project_hash,task_id,report_id,report_content_digest,view_relative_path,?,?,request_digest,created_at,?,NULL FROM approval_handles WHERE approval_handle=?",
                (f"approval-{store.project_hash}-new-{suffix}", next_digest, next_sequence,
                 next_sequence, relation["approval_handle"]),
            )
        store._write(write)

    @staticmethod
    def _append_unrelated_task_events(store: V12Store, *, task_id: str) -> None:
        """Add chronology that is intentionally unrelated to a plan snapshot."""
        def write(connection):
            store._timeline(
                connection, event_type="governance_advisory_recorded",
                entity_type="assessment", entity_id="assessment-unrelated",
                payload={"kind": "advisory"}, task_id=task_id,
            )
            store._timeline(
                connection, event_type="initiative_revision_noted",
                entity_type="initiative", entity_id="initiative-unrelated",
                payload={"kind": "unrelated"}, task_id=task_id,
            )
        store._write(write)

    def test_binding_is_server_owned_and_record_is_atomic_replay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-decision-aggregate-") as root, tempfile.TemporaryDirectory(prefix="cortex-decision-home-") as home:
            previous = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            self.addCleanup(lambda: os.environ.__setitem__("CODEX_HOME", previous) if previous is not None else os.environ.pop("CODEX_HOME", None))
            project = Path(root)
            task = open_task(task={"project_root": str(project), "objective": "test", "request_original": "test", "user_language": "en", "outcomes": [{"requirement": "r", "acceptance": ["a"]}], "constraints": ["c"]})["task"]
            store = V12Store(project)
            aggregate = DecisionAggregate(store)
            first = aggregate.open(task_id=task["task_id"], prompt="Choose", prompt_language="en")
            repeated = aggregate.open(task_id=task["task_id"], prompt="Choose", prompt_language="en")
            self.assertEqual(first["binding"]["clarification_binding"], repeated["binding"]["clarification_binding"])
            self.assertTrue(repeated["replayed"])
            recorded = aggregate.record(
                task_id=task["task_id"], binding_ref=first["binding"]["clarification_binding"],
                response_original="yes", user_language="en",
            )
            replay = aggregate.record(
                task_id=task["task_id"], binding_ref=first["binding"]["clarification_binding"],
                response_original="yes", user_language="en",
            )
            self.assertFalse(recorded["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(recorded["decision"]["decision_id"], replay["decision"]["decision_id"])
            self.assertEqual(aggregate.reconcile(task_id=task["task_id"], binding_ref=first["binding"]["clarification_binding"])["state"], "consumed")

    def test_open_identity_is_one_transaction_under_concurrency(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-decision-open-") as root, tempfile.TemporaryDirectory(prefix="cortex-decision-home-") as home:
            previous = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            self.addCleanup(lambda: os.environ.__setitem__("CODEX_HOME", previous) if previous is not None else os.environ.pop("CODEX_HOME", None))
            task = open_task(task={"project_root": root, "objective": "test", "request_original": "test", "user_language": "en", "outcomes": [{"requirement": "r", "acceptance": ["a"]}], "constraints": ["c"]})["task"]
            bindings: list[str] = []
            failures: list[BaseException] = []

            def issue() -> None:
                try:
                    result = DecisionAggregate(V12Store(root)).open_clarification(
                        task_id=task["task_id"], prompt="Concurrent?", prompt_language="en",
                    )
                    bindings.append(result["binding"]["clarification_binding"])
                except BaseException as exc:  # assertions below retain thread faults
                    failures.append(exc)

            workers = [threading.Thread(target=issue) for _ in range(4)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            self.assertEqual(failures, [])
            self.assertEqual(len(set(bindings)), 1)

    def test_steering_receipt_binds_delta_and_supersession(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cortex-decision-steering-") as root, tempfile.TemporaryDirectory(prefix="cortex-decision-home-") as home:
            previous = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            self.addCleanup(lambda: os.environ.__setitem__("CODEX_HOME", previous) if previous is not None else os.environ.pop("CODEX_HOME", None))
            task = open_task(task={"project_root": root, "objective": "test", "request_original": "test", "user_language": "en", "outcomes": [{"requirement": "r", "acceptance": ["a"]}], "constraints": ["c"]})["task"]
            aggregate = DecisionAggregate(V12Store(root))
            opened = aggregate.open_steering(task_id=task["task_id"], prompt="Steer", prompt_language="en")
            binding = opened["binding"]["clarification_binding"]
            delta = {"add": [{"category": "verification", "text": "Check the change."}]}
            first = aggregate.record_steering(task_id=task["task_id"], binding_ref=binding, response_original="yes", user_language="en", steering_delta=delta)
            replay = aggregate.record_steering(task_id=task["task_id"], binding_ref=binding, response_original="yes", user_language="en", steering_delta=delta)
            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            with self.assertRaises(V12StoreError) as conflict:
                aggregate.record_steering(task_id=task["task_id"], binding_ref=binding, response_original="yes", user_language="en", steering_delta={"add": [{"category": "verification", "text": "Changed intent."}]})
            self.assertEqual(conflict.exception.code, "command_conflict")

    def test_receipts_use_public_semantic_family_names(self) -> None:
        """Internal family/outcome aliases never become durable receipt names."""
        with tempfile.TemporaryDirectory(prefix="cortex-decision-command-names-") as root, tempfile.TemporaryDirectory(prefix="cortex-decision-home-") as home:
            previous = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            self.addCleanup(lambda: os.environ.__setitem__("CODEX_HOME", previous) if previous is not None else os.environ.pop("CODEX_HOME", None))
            task, store, aggregate, plan_id = self._ready_plan(root)
            steering = aggregate.open_steering(task_id=task["task_id"], prompt="Steer", prompt_language="en")
            aggregate.record_steering(
                task_id=task["task_id"], binding_ref=steering["binding"]["clarification_binding"],
                response_original="yes", user_language="en",
                steering_delta={"add": [{"category": "verification", "text": "Inspect receipts."}]},
            )
            # Each outcome is a semantic result of the same public record
            # operation; no outcome name may become a receipt command name.
            for outcome in ("approve", "request_revision", "cancel"):
                # Steering advanced the contract; make a fresh ready plan per
                # binding so no outcome competes for a consumed relation.
                child_root = Path(root) / outcome
                child_root.mkdir()
                child_task, child_store, child_aggregate, child_plan = self._ready_plan(str(child_root))
                opened = child_aggregate.open_plan_review(
                    task_id=child_task["task_id"], subject_type="plan", subject_id=child_plan,
                    prompt=f"Review {outcome}.", prompt_language="en",
                )
                self._introduce_newer_plan_view(
                    child_store, task_id=child_task["task_id"], plan_id=child_plan,
                    relation=dict(opened["binding"]["plan_review_relation"]),
                    suffix={"approve": "b", "request_revision": "c", "cancel": "d"}[outcome],
                )
                child_aggregate.record_plan_review(
                    task_id=child_task["task_id"], binding_ref=opened["binding"]["clarification_binding"],
                    outcome=outcome, response_original=f"{outcome}.", user_language="en",
                )
                names = child_store._read(lambda connection: [
                    str(row[0]) for row in connection.execute(
                        "SELECT command_name FROM command_receipts WHERE project_hash=?",
                        (child_store.project_hash,),
                    ).fetchall()
                ])
                self.assertIn("open_plan_review", names)
                self.assertIn("record_plan_review", names)
                self.assertNotIn(f"record_{outcome}", names)
            names = store._read(lambda connection: [
                str(row[0]) for row in connection.execute(
                    "SELECT command_name FROM command_receipts WHERE project_hash=?",
                    (store.project_hash,),
                ).fetchall()
            ])
            self.assertIn("open_steering", names)
            self.assertNotIn("record_decision", names)
            self.assertNotIn("open_steer", names)
            self.assertNotIn("record_steer", names)

    def test_plan_review_binding_survives_newer_view_concurrency_and_restart(self) -> None:
        """An issued review cannot drift to a newer view during lost-response recovery."""
        with tempfile.TemporaryDirectory(prefix="cortex-decision-plan-relation-") as root, tempfile.TemporaryDirectory(prefix="cortex-decision-home-") as home:
            previous = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            self.addCleanup(lambda: os.environ.__setitem__("CODEX_HOME", previous) if previous is not None else os.environ.pop("CODEX_HOME", None))
            task, store, aggregate, plan_id = self._ready_plan(root)
            opened = aggregate.open_plan_review(
                task_id=task["task_id"], subject_type="plan", subject_id=plan_id,
                prompt="Review immutable relation.", prompt_language="en",
            )
            relation = dict(opened["binding"]["plan_review_relation"])
            self._introduce_newer_plan_view(
                store, task_id=task["task_id"], plan_id=plan_id, relation=relation, suffix="a",
            )
            results: list[dict] = []
            failures: list[BaseException] = []

            def consume() -> None:
                try:
                    results.append(DecisionAggregate(V12Store(root)).record_plan_review(
                        task_id=task["task_id"], binding_ref=opened["binding"]["clarification_binding"],
                        outcome="approve", response_original="Approve the bound view.", user_language="en",
                    ))
                except BaseException as exc:
                    failures.append(exc)

            workers = [threading.Thread(target=consume) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            self.assertEqual(failures, [])
            self.assertEqual(len(results), 2)
            self.assertEqual({result["decision"]["decision_id"] for result in results},
                             {results[0]["decision"]["decision_id"]})
            self.assertEqual(sum(bool(result["replayed"]) for result in results), 1)
            # A new aggregate after the caller lost the response is an exact
            # receipt replay, not a replacement review binding or current-view lookup.
            replay = DecisionAggregate(V12Store(root)).record_plan_review(
                task_id=task["task_id"], binding_ref=opened["binding"]["clarification_binding"],
                outcome="approve", response_original="Approve the bound view.", user_language="en",
            )
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["decision"]["decision_id"], results[0]["decision"]["decision_id"])
            bound = store._read(lambda connection: connection.execute(
                "SELECT plan_content_digest,plan_approval_handle,plan_view_content_digest,plan_view_source_sequence FROM clarification_bindings WHERE clarification_binding=?",
                (opened["binding"]["clarification_binding"],),
            ).fetchone())
            self.assertEqual(str(bound["plan_content_digest"]), relation["plan_content_digest"])
            self.assertEqual(str(bound["plan_approval_handle"]), relation["approval_handle"])
            self.assertEqual(str(bound["plan_view_content_digest"]), relation["view_content_digest"])
            self.assertEqual(int(bound["plan_view_source_sequence"]), relation["view_source_sequence"])

    def test_plan_relation_ignores_later_unrelated_chronology_but_new_view_gets_new_relation(self) -> None:
        """Global MAX(timeline) is never a validity condition for a relation."""
        with tempfile.TemporaryDirectory(prefix="cortex-decision-plan-chronology-") as root, tempfile.TemporaryDirectory(prefix="cortex-decision-home-") as home:
            previous = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            self.addCleanup(lambda: os.environ.__setitem__("CODEX_HOME", previous) if previous is not None else os.environ.pop("CODEX_HOME", None))
            task, store, aggregate, plan_id = self._ready_plan(root)
            opened = aggregate.open_plan_review(
                task_id=task["task_id"], subject_type="plan", subject_id=plan_id,
                prompt="Review the stable snapshot.", prompt_language="en",
            )
            binding = opened["binding"]["clarification_binding"]
            relation = dict(opened["binding"]["plan_review_relation"])
            self._append_unrelated_task_events(store, task_id=task["task_id"])
            replayed_open = DecisionAggregate(V12Store(root)).open_plan_review(
                task_id=task["task_id"], subject_type="plan", subject_id=plan_id,
                prompt="Review the stable snapshot.", prompt_language="en",
            )
            self.assertTrue(replayed_open["replayed"])
            self.assertEqual(replayed_open["binding"]["plan_review_relation"], relation)
            recorded = DecisionAggregate(V12Store(root)).record_plan_review(
                task_id=task["task_id"], binding_ref=binding, outcome="approve",
                response_original="Approve the presented snapshot.", user_language="en",
            )
            self.assertFalse(recorded["replayed"])
            self.assertTrue(DecisionAggregate(V12Store(root)).record_plan_review(
                task_id=task["task_id"], binding_ref=binding, outcome="approve",
                response_original="Approve the presented snapshot.", user_language="en",
            )["replayed"])

            # A materially different rendered view gets a distinct relation
            # for a distinct server-issued review binding; it cannot redirect
            # the already consumed relation above.
            self._introduce_newer_plan_view(
                store, task_id=task["task_id"], plan_id=plan_id, relation=relation, suffix="b",
            )
            newer = DecisionAggregate(V12Store(root)).open_plan_review(
                task_id=task["task_id"], subject_type="plan", subject_id=plan_id,
                prompt="Review the newer snapshot.", prompt_language="en",
            )
            self.assertNotEqual(newer["binding"]["clarification_binding"], binding)
            self.assertNotEqual(newer["binding"]["plan_review_relation"]["approval_handle"], relation["approval_handle"])
            self.assertNotEqual(newer["binding"]["plan_review_relation"]["view_content_digest"], relation["view_content_digest"])


if __name__ == "__main__":
    unittest.main()
