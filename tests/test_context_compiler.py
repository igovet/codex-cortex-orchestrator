"""Focused contracts for the database-centric context/handoff compiler seam."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "cortex" / "scripts"))

from cortex_runtime.context_compiler import (
    AcceptanceCriterion,
    Constraint,
    ContextCompiler,
    Decision,
    Finding,
    Requirement,
    TaskIntent,
    VerificationRequirement,
    context_domain_from_canonical,
)
from cortex_runtime.handoff_compiler import HandoffCompiler
from cortex_runtime.ledger_db import create_task, ensure_database, list_task_documents
from cortex_runtime import attempt_protocol


class ContextCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.canonical = {
            "task": {
                "user_request": "Refactor server handoff transport",
                "requirements": ["Use canonical state", "Keep briefing artifacts current"],
                "constraints": ["Do not allow worker-authored result projections."],
                "acceptance_criteria": ["QA receives changed files and acceptance coverage"],
                "verification": ["Run targeted tests"],
                "scope": ["plugins/cortex/scripts"],
            },
            "attempt": {"attempt_id": "attempt-qa", "gate": "qa", "profile": "qa_engineer"},
            "resolved_user_decisions": [{"question_en": "Migrate the handoff protocol now?", "answer_en": "Use canonical results"}],
            "read_receipts": {"briefing": {"kind": "briefing_read"}, "predecessors": ["result-impl"]},
            "predecessor_results": [{
                "attempt_result_ref": "result-impl", "attempt_id": "attempt-impl", "gate": "implementation",
                "profile": "backend_dev", "summary": "Implemented a server-owned receipt store.",
                "changed_files": ["plugins/cortex/scripts/cortex_runtime/read_receipts.py"],
                "checks": ["python -m unittest tests.test_context_compiler"],
                "findings": ["Full public handler integration remains pending."],
                "raw_worker_body": {"must_not": "be projected"},
            }],
        }

    def test_context_is_bounded_semantic_state_not_a_raw_worker_body(self) -> None:
        context = ContextCompiler().compile(self.canonical, target_profile="qa_engineer")
        self.assertEqual(context["schema"], "cortex/compiled-worker-context/v1")
        self.assertTrue(context["server_receipts"]["briefing_read"])
        self.assertEqual(context["predecessor_facts"][0]["attempt_result_ref"], "result-impl")
        self.assertNotIn("raw_worker_body", context["predecessor_facts"][0])
        self.assertEqual(context["task"]["constraints"], ["Do not allow worker-authored result projections."])

    def test_typed_domain_validates_and_preserves_canonical_records(self) -> None:
        domain = context_domain_from_canonical(self.canonical)
        self.assertIsInstance(domain.intent, TaskIntent)
        self.assertIsInstance(domain.requirements[0], Requirement)
        self.assertIsInstance(domain.constraints[0], Constraint)
        self.assertIsInstance(domain.decisions[0], Decision)
        self.assertIsInstance(domain.acceptance_criteria[0], AcceptanceCriterion)
        self.assertIsInstance(domain.verification_requirements[0], VerificationRequirement)
        self.assertIsInstance(domain.findings[0], Finding)
        malformed = {**self.canonical, "task": {**self.canonical["task"], "constraints": [{"text": "not a canonical string"}]}}
        with self.assertRaisesRegex(ValueError, "constraints"):
            context_domain_from_canonical(malformed)
        scalar_requirements = {**self.canonical, "task": {**self.canonical["task"], "requirements": "scalar requirement"}}
        with self.assertRaisesRegex(ValueError, "requirements"):
            context_domain_from_canonical(scalar_requirements)
        scalar_constraints = {**self.canonical, "task": {**self.canonical["task"], "constraints": "scalar constraint"}}
        with self.assertRaisesRegex(ValueError, "constraints"):
            context_domain_from_canonical(scalar_constraints)
        noncanonical_decision = {
            **self.canonical,
            "resolved_user_decisions": [{"question": "old field", "answer": "old field"}],
        }
        with self.assertRaisesRegex(ValueError, "decision question"):
            context_domain_from_canonical(noncanonical_decision)

    def test_oversized_persisted_requirement_round_trips_losslessly_for_context_and_handoff(self) -> None:
        # This simulates a task written before the per-item requirement bound.
        # Unicode and mixed whitespace ensure the recovery path is not an
        # ASCII-only slice or a silent truncation.
        oversized = "\n\t".join([
            "Сохранить канонический контекст между этапами проверки."
            for _ in range(24)
        ])
        self.assertGreater(len(oversized), 600)
        canonical = {
            **self.canonical,
            "task": {**self.canonical["task"], "requirements": [oversized]},
        }

        domain = context_domain_from_canonical(canonical)
        segments = [item.text for item in domain.requirements]
        self.assertEqual(segments, [oversized])

        context = ContextCompiler().compile(canonical, target_profile="backend_dev")
        self.assertEqual(context["task"]["requirements"], [oversized])
        self.assertIsNone(context["task"]["projection"])
        handoff = HandoffCompiler().build(
            canonical, target_profile="backend_dev", target_gate="implementation"
        )
        self.assertEqual(handoff["requirements"], [oversized])

    def test_requirements_still_reject_non_array_shape_after_length_recovery(self) -> None:
        malformed = {
            **self.canonical,
            "task": {**self.canonical["task"], "requirements": "scalar requirement"},
        }
        with self.assertRaisesRegex(ValueError, "requirements"):
            context_domain_from_canonical(malformed)

    def test_all_large_task_fields_compile_without_backend_projection_loss(self) -> None:
        """Valid durable facts never become a late dispatch validation error."""
        huge = "界" * 2_000
        contract = {
            "artifact_ref": "artifact-task-contract",
            "artifact_path": "/tmp/task-contract.json",
            "digest_sha256": "a" * 64,
            "byte_size": len(huge.encode("utf-8")),
        }
        canonical = {
            **self.canonical,
            "task": {
                **self.canonical["task"],
                "task_contract": contract,
                "user_request": huge,
                "requirements": [huge] * 24,
                "constraints": [huge] * 24,
                "acceptance_criteria": [huge] * 24,
                "verification": [huge] * 24,
            },
            "resolved_user_decisions": [
                {"question_en": huge, "answer_en": huge} for _ in range(16)
            ],
        }
        context = ContextCompiler().compile(canonical, target_profile="backend_dev")
        self.assertEqual(context["task"]["user_request"], huge)
        for field in ("requirements", "constraints", "acceptance_criteria", "verification_requirements"):
            self.assertEqual(context["task"][field], [huge] * 24)
        self.assertIsNone(context["task"]["projection"])
        self.assertEqual(context["decisions"], [{"question": huge, "answer": huge}] * 16)
        self.assertNotIn("decisions_projection", context)


    def test_server_question_decision_events_are_compiler_visible(self) -> None:
        canonical = {
            **self.canonical,
            "attempt_events": [
                {
                    "event_type": "question_created",
                    "actor": "cortex",
                    "payload": {"question_ref": "question-0001", "question": "Which rollout is safe?"},
                },
                {
                    "event_type": "question_answered",
                    "actor": "cortex",
                    "payload": {"question_ref": "question-0001", "answer": "Use gradual rollout."},
                },
                {
                    "event_type": "decision_resolved",
                    "actor": "cortex",
                    "payload": {
                        "question_ref": "question-0001",
                        "question": "Which rollout is safe?",
                        "answer": "Use gradual rollout.",
                    },
                },
            ],
        }
        context = ContextCompiler().compile(canonical, target_profile="qa_engineer")
        self.assertEqual(
            [item["event_type"] for item in context["event_transitions"]],
            ["question_created", "question_answered", "decision_resolved"],
        )
        self.assertIn(
            {"question": "Which rollout is safe?", "answer": "Use gradual rollout."},
            context["decisions"],
        )

    def test_handoffs_are_target_specific(self) -> None:
        compiler = HandoffCompiler()
        backend = compiler.build(self.canonical, target_profile="backend_dev", target_gate="implementation")
        qa = compiler.build(self.canonical, target_profile="qa_engineer", target_gate="qa")
        review = compiler.build(self.canonical, target_profile="code_reviewer", target_gate="review")
        self.assertIn("assigned_scope", backend)
        self.assertNotIn("files_changed", backend)
        self.assertIn("implemented_behavior", qa)
        self.assertIn("files_changed", qa)
        self.assertIn("change_inventory", review)
        self.assertNotIn("implemented_behavior", review)
        for projection in (backend, qa, review):
            self.assertNotIn("editable_worker_transport", projection)

    def test_handoff_preserves_canonical_result_facts_without_raw_body(self) -> None:
        self.canonical["predecessor_results"][0]["semantic_source"] = "attempt_result"
        self.canonical["predecessor_selection"] = {
            "available": 24,
        }
        context = ContextCompiler().compile(self.canonical, target_profile="qa_engineer")
        self.assertEqual(context["predecessor_selection"]["selected"], 1)
        self.assertFalse(context["predecessor_selection"]["truncated"])
        self.assertEqual(context["predecessor_facts"][0]["semantic_source"], "attempt_result")

        handoff = HandoffCompiler().build(
            self.canonical,
            target_profile="qa_engineer",
            target_gate="qa",
            compact=True,
        )
        self.assertEqual(handoff["predecessor_selection"]["selected"], 1)
        self.assertFalse(handoff["predecessor_selection"]["truncated"])
        self.assertIn("files_changed", handoff)
        self.assertNotIn("raw_worker_body", str(handoff))
        self.assertNotIn("worker_body", handoff)

    def test_server_receipts_are_durable_and_scoped_to_the_assigned_frontier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            ensure_database(root)
            create_task(
                root,
                {"task_id": "task-1", "created_at": "2026-01-01T00:00:00Z"},
                {"task_id": "task-1", "task_number": 1, "status": "active", "revision": 1},
                "tasks/task-1",
            )
            # Receipts are AttemptEvent rows, not task documents or exports
            # exports.  Use the fresh protocol directly so this test does not
            # accidentally preserve the old ServerReadReceiptStore surface.
            from cortex_runtime.ledger_db import update_task_state
            update_task_state(
                root,
                {
                    "task_id": "task-1", "task_number": 1, "status": "active", "revision": 1,
                    "attempts": [{
                        "attempt_id": "attempt-qa", "dispatch_ref": "dispatch-qa",
                        "profile": "qa_engineer", "briefing_digest": "a" * 64,
                        "context_result_refs": ["result-implementation"],
                    }],
                },
                event="receipt_test_setup",
            )
            attempt = {
                "attempt_id": "attempt-qa", "dispatch_ref": "dispatch-qa", "profile": "qa_engineer",
                "briefing_digest": "a" * 64, "context_result_refs": ["result-implementation"],
            }
            first = attempt_protocol.acknowledge_briefing(
                root, task_id="task-1", attempt_id="attempt-qa",
                dispatch_ref="dispatch-qa", digest="a" * 64,
            )
            repeated = attempt_protocol.acknowledge_briefing(
                root, task_id="task-1", attempt_id="attempt-qa",
                dispatch_ref="dispatch-qa", digest="a" * 64,
            )
            self.assertEqual(first["receipt"]["event_ref"], repeated["receipt"]["event_ref"])
            attempt_protocol.record_predecessor_read(
                root, task_id="task-1", attempt_id="attempt-qa",
                predecessor_result_ref="result-implementation",
            )
            receipts = attempt_protocol.attempt_receipts(
                root, task_id="task-1", attempt_id="attempt-qa",
            )
            self.assertEqual(
                sorted(receipts["predecessor_receipts"]),
                ["result-implementation"],
            )
            self.assertEqual(list_task_documents(root, "task-1", "server_receipts/"), [])
            with self.assertRaisesRegex(ValueError, "not authorized"):
                attempt_protocol.record_predecessor_read(
                    root, task_id="task-1", attempt_id="attempt-qa",
                    predecessor_result_ref="result-other",
                )


if __name__ == "__main__":
    unittest.main()
