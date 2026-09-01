from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/cortex/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS
from cortex_runtime.domain_api import (
    _resolve_task_context,
    assess_governance,
    open_assignment,
    open_steering,
    open_task,
    publish_result,
    read_task,
    record_steering,
)
from cortex_runtime.mcp_api import (
    MAX_PHYSICAL_JSONL_FRAME_BYTES,
    _SchemaError,
    _success_tool_result,
    _tool_error_result,
    _validate_schema,
    _validation_failure,
)
from cortex_runtime.v12_contract import MCP_OPERATION_MAX_BYTES
from cortex_runtime.v12_service import V12ServiceError


PROVENANCE = {
    name: "sha256:" + "a" * 64
    for name in ("build_digest", "candidate_digest", "source_digest", "catalogue_digest")
}
WORKER_REF = re.compile(r'"task_ref":"(t_[0-9a-f]{12}_[0-9a-f]{32})"')


class RuntimeContractRemediationTests(unittest.TestCase):
    def _task(self, root: str, outcomes: list[dict]) -> dict:
        task = open_task(
            project_root=root,
            request_original="Exercise the bounded runtime contract.",
            user_language="en",
            outcomes=outcomes,
            constraints=["Keep the test deterministic."],
        )
        assess_governance(
            task_ref=task["task_ref"], mode="minimal",
            rationale="Focused runtime contract fixture.",
        )
        return task

    def _assignment(
        self, task_ref: str, outcome_names: list[str], *, role: str,
        responsibility: str = "evidence", report_policy: str = "none",
        scope: str = "One exact semantic outcome.",
    ) -> tuple[dict, str]:
        assignment = open_assignment(
            task_ref=task_ref,
            role=role,
            profile_name="explorer",
            model="gpt-5.6-luna",
            reasoning_effort="high",
            responsibility=responsibility,
            goal="Exercise this exact outcome scope.",
            scope=scope,
            instructions="Consume the assignment and publish bounded evidence.",
            outcomes=outcome_names,
            report_policy=report_policy,
        )
        match = WORKER_REF.search(assignment["native_dispatch"]["message"])
        self.assertIsNotNone(match)
        return assignment, match.group(1)

    @staticmethod
    def _semantic_outcome(name: str) -> dict:
        return {
            "outcome": name,
            "acceptance": [f"{name} is accepted."],
            "constraints": [],
            "verification": [f"Verify {name}"],
        }

    def _publish(self, worker_ref: str, outcome: dict, summary: str) -> None:
        context: dict = {}
        read_task(
            task_ref=worker_ref, view="assignment",
            _connection_context=context,
        )
        published = publish_result(
            task_ref=worker_ref,
            summary=summary,
            outcome="The assigned outcome is complete.",
            changes=[{"path": "runtime", "summary": "Focused fixture change."}],
            verification_facts=[{"state": "executed", "summary": "Focused fixture check passed."}],
            outcome_coverage=[{
                "outcome": outcome["outcome"],
                "status": "complete",
                "verification": ["Focused fixture check passed."],
            }],
            documentation_impact="No documentation update is required for the fixture.",
            risks=[],
            unresolved=[],
            status="completed",
            _connection_context=context,
        )
        self.assertEqual(published["state"], "published")

    def test_independent_steering_additions_and_atomic_replacement(self) -> None:
        original = self._semantic_outcome("Original outcome.")
        first = self._semantic_outcome("Independent outcome A.")
        second = self._semantic_outcome("Independent outcome B.")
        replacement = self._semantic_outcome("Replacement outcome.")
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task = self._task(root, [original])
            open_steering(
                task_ref=task["task_ref"], prompt="Add both outcomes?",
                prompt_language="en",
            )
            record_steering(
                task_ref=task["task_ref"], response_original="Add both.",
                user_language="en", add=[first, second], retire=[],
            )
            state = read_task(task_ref=task["task_ref"], view="state")
            names = [item["outcome"] for item in state["data"]["effective_contract"]["items"]]
            self.assertEqual(names, [original["outcome"], first["outcome"], second["outcome"]])

            open_steering(
                task_ref=task["task_ref"], prompt="Replace the original?",
                prompt_language="en",
            )
            record_steering(
                task_ref=task["task_ref"], response_original="Replace it.",
                user_language="en", add=[replacement], retire=[original],
            )
            state = read_task(task_ref=task["task_ref"], view="state")
            names = [item["outcome"] for item in state["data"]["effective_contract"]["items"]]
            self.assertEqual(names, [replacement["outcome"], first["outcome"], second["outcome"]])
            with self.assertRaises(V12ServiceError) as stale:
                self._assignment(
                    task["task_ref"], [original["outcome"]], role="stale",
                )
            self.assertEqual(stale.exception.code, "outcome_item_not_found")
            self.assertEqual(stale.exception.details.get("path"), "$.outcomes[0]")
            self._assignment(
                task["task_ref"], [replacement["outcome"]], role="current",
            )

    def test_invalid_public_steering_outcome_reports_exact_path(self) -> None:
        original = self._semantic_outcome("Original outcome.")
        with tempfile.TemporaryDirectory() as root:
            task = self._task(root, [original])
            open_steering(
                task_ref=task["task_ref"], prompt="Add a malformed outcome?",
                prompt_language="en",
            )
            malformed = {
                "outcome": "Incomplete.", "acceptance": [], "constraints": [],
            }
            with self.assertRaises(V12ServiceError) as rejected:
                record_steering(
                    task_ref=task["task_ref"], response_original="Add it.",
                    user_language="en", add=[malformed], retire=[],
                )
            self.assertEqual(rejected.exception.details.get("path"), "$.add[0]")
            self.assertEqual(
                rejected.exception.details.get("expected"),
                "complete_outcome_object",
            )

    def test_exact_scope_replay_does_not_collapse_distinct_assignments(self) -> None:
        outcomes = [self._semantic_outcome("Outcome A."), self._semantic_outcome("Outcome B.")]
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task = self._task(root, outcomes)
            first, first_ref = self._assignment(
                task["task_ref"], [outcomes[0]["outcome"]],
                role="same specialist", scope="Same visible scope.",
            )
            second, second_ref = self._assignment(
                task["task_ref"], [outcomes[1]["outcome"]],
                role="same specialist", scope="Same visible scope.",
            )
            self.assertFalse(first["replayed"])
            self.assertFalse(second["replayed"])
            self.assertNotEqual(first_ref, second_ref)

            replay_first, replay_first_ref = self._assignment(
                task["task_ref"], [outcomes[0]["outcome"]],
                role="same specialist", scope="Same visible scope.",
            )
            replay_second, replay_second_ref = self._assignment(
                task["task_ref"], [outcomes[1]["outcome"]],
                role="same specialist", scope="Same visible scope.",
            )
            self.assertTrue(replay_first["replayed"])
            self.assertTrue(replay_second["replayed"])
            self.assertEqual(replay_first_ref, first_ref)
            self.assertEqual(replay_second_ref, second_ref)

    def test_parallel_owner_conflict_has_safe_scope_diagnostics(self) -> None:
        outcome = self._semantic_outcome("Exclusively owned outcome.")
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task = self._task(root, [outcome])

            def attempt(role: str) -> tuple[str, object]:
                try:
                    assignment, worker_ref = self._assignment(
                        task["task_ref"], [outcome["outcome"]], role=role,
                        responsibility="delivery", scope=f"Owner {role}.",
                    )
                    return "success", (assignment, worker_ref)
                except V12ServiceError as error:
                    return "error", error

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, ("owner a", "owner b")))
            successes = [value for state, value in results if state == "success"]
            failures = [value for state, value in results if state == "error"]
            self.assertEqual(len(successes), 1)
            self.assertEqual(len(failures), 1)
            failure = failures[0]
            self.assertEqual(failure.code, "outcome_assignment_conflict")
            self.assertEqual(failure.details, {
                "path": "$.outcomes",
                "expected": "non_overlapping_outcome_scope",
                "reason": "ownership_conflict",
            })

    def test_restarted_assignment_and_evidence_view_reuse_receipt(self) -> None:
        outcome = self._semantic_outcome("Produce predecessor evidence.")
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task = self._task(root, [outcome])
            _producer, producer_ref = self._assignment(
                task["task_ref"], [outcome["outcome"]], role="producer",
                responsibility="delivery",
            )
            self._publish(producer_ref, outcome, "Producer evidence.")
            _consumer, consumer_ref = self._assignment(
                task["task_ref"], [outcome["outcome"]], role="consumer",
                report_policy="all_finalized",
            )

            first_context: dict = {}
            first = read_task(
                task_ref=consumer_ref, view="assignment",
                _connection_context=first_context,
            )
            self.assertFalse(first["has_more"])
            store, _task_id, assignment_id, _coordinator_ref = _resolve_task_context(consumer_ref)

            def receipt_rows() -> list[tuple[int, int]]:
                return store._read(lambda connection: [
                    (int(row["receipt_id"]), int(row["created_sequence"]))
                    for row in connection.execute(
                        "SELECT receipt_id,created_sequence FROM report_consumption_receipts "
                        "WHERE consumer_delegation_id=? ORDER BY receipt_id",
                        (assignment_id,),
                    ).fetchall()
                ])

            initial_receipts = receipt_rows()
            self.assertEqual(len(initial_receipts), 1)
            evidence = read_task(
                task_ref=consumer_ref, view="evidence",
                report_policy="all_finalized",
                _connection_context=first_context,
            )
            self.assertIn("Producer evidence.", repr(evidence))
            self.assertEqual(receipt_rows(), initial_receipts)

            restarted = read_task(
                task_ref=consumer_ref, view="assignment",
                _connection_context={},
            )
            self.assertEqual(restarted["data"]["evidence"], first["data"]["evidence"])
            self.assertEqual(receipt_rows(), initial_receipts)

    def test_large_assignment_evidence_uses_server_owned_continuation(self) -> None:
        outcomes = [self._semantic_outcome("Large evidence A."), self._semantic_outcome("Large evidence B.")]
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task = self._task(root, outcomes)
            for index, outcome in enumerate(outcomes):
                _producer, producer_ref = self._assignment(
                    task["task_ref"], [outcome["outcome"]],
                    role=f"large producer {index}", responsibility="delivery",
                )
                self._publish(producer_ref, outcome, f"Evidence {index}: " + "x" * 45_000)
            _consumer, consumer_ref = self._assignment(
                task["task_ref"], [item["outcome"] for item in outcomes],
                role="large consumer", report_policy="all_finalized",
            )
            context: dict = {}
            first = read_task(
                task_ref=consumer_ref, view="assignment",
                _connection_context=context,
            )
            self.assertTrue(first["has_more"], repr(first["data"]["evidence"]))
            self.assertNotIn("content", first["data"]["evidence"]["reports"][0])
            second = read_task(
                task_ref=consumer_ref, view="assignment", continue_=True,
                _connection_context=context,
            )
            self.assertFalse(second["has_more"])
            with self.assertRaises(V12ServiceError) as exhausted:
                read_task(
                    task_ref=consumer_ref, view="assignment", continue_=True,
                    _connection_context=context,
                )
            self.assertEqual(exhausted.exception.code, "report_cursor_invalid")

    def test_large_assignment_retains_exact_publication_outcome_without_text_duplication(self) -> None:
        long_outcome = {
            "outcome": "Exact terminal publication outcome.",
            "acceptance": [f"Acceptance {index}: " + "a" * 500 for index in range(40)],
            "constraints": [f"Constraint {index}: " + "c" * 240 for index in range(20)],
            "verification": [f"Verification {index}: " + "v" * 300 for index in range(20)],
        }
        with tempfile.TemporaryDirectory() as root, patch(
            "cortex_runtime.domain_api._worker_capability_provenance",
            return_value=PROVENANCE,
        ):
            task = self._task(root, [long_outcome])
            _assignment, worker_ref = self._assignment(
                task["task_ref"], [long_outcome["outcome"]],
                role="large authority consumer",
            )
            read = read_task(
                task_ref=worker_ref, view="assignment",
                _connection_context={},
            )
            reconciliation = read["data"]["publication_reconciliation"]
            self.assertEqual(
                reconciliation["required_outcomes"],
                [long_outcome["outcome"]],
            )
            self.assertEqual(
                reconciliation["contract_coverage_template"],
                [{"outcome": long_outcome["outcome"]}],
            )
            rendered = _success_tool_result(read)
            self.assertEqual(
                rendered["content"][-1]["text"],
                "Complete Cortex result is available in structuredContent.",
            )
            self.assertEqual(
                rendered["structuredContent"]["data"]
                ["publication_reconciliation"]["required_outcomes"],
                [long_outcome["outcome"]],
            )

    def test_aggregate_byte_diagnostic_and_large_success_framing(self) -> None:
        contract = PUBLIC_TOOLS["publish_result"]
        schema = contract["inputSchema"]
        output_schema = contract["outputSchema"]
        self.assertIn("replayed", output_schema["required"])
        self.assertIn(
            "ends worker tool activity immediately",
            output_schema["description"],
        )
        self.assertIn(
            "no later tool call",
            output_schema["properties"]["state"]["description"],
        )
        self.assertEqual(schema["maxBytes"], MCP_OPERATION_MAX_BYTES)
        arguments = {
            "task_ref": "t_" + "a" * 12 + "_" + "b" * 32,
            "summary": "",
            "outcome": "Complete.",
            "changes": [],
            "verification_facts": [{"state": "executed", "summary": "Passed."}],
            "outcome_coverage": [{
                "outcome": "Bounded outcome.", "status": "complete",
                "verification": ["Passed."],
            }],
            "documentation_impact": "None.",
            "risks": [],
            "unresolved": [],
            "status": "completed",
        }

        def encoded_bytes(value: dict) -> int:
            return len(json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8"))

        overhead = encoded_bytes(arguments)
        at_limit = dict(arguments)
        at_limit["summary"] = "x" * (MCP_OPERATION_MAX_BYTES - overhead)
        self.assertEqual(encoded_bytes(at_limit), MCP_OPERATION_MAX_BYTES)
        below_limit = dict(at_limit)
        below_limit["summary"] = below_limit["summary"][:-1]
        self.assertEqual(encoded_bytes(below_limit), MCP_OPERATION_MAX_BYTES - 1)
        _validate_schema(schema, below_limit)
        _validate_schema(schema, at_limit)

        above = dict(at_limit)
        above["summary"] += "x"
        with self.assertRaises(_SchemaError) as caught:
            _validate_schema(schema, above)
        error = caught.exception
        self.assertEqual(error.path, "$")
        self.assertEqual(error.actual_bytes, MCP_OPERATION_MAX_BYTES + 1)
        self.assertEqual(error.max_bytes, MCP_OPERATION_MAX_BYTES)
        failure = _validation_failure(
            error, tool_name="publish_result", arguments=above,
            input_schema=schema,
        )
        self.assertEqual(failure["details"]["path"], "$")
        self.assertNotIn("field", failure["details"])
        self.assertEqual(failure["details"]["actual_bytes"], MCP_OPERATION_MAX_BYTES + 1)
        self.assertEqual(failure["details"]["max_bytes"], MCP_OPERATION_MAX_BYTES)
        self.assertEqual(failure["details"]["sections"][0]["section"], "summary")
        self.assertIn("exactly one", failure["action"])
        rendered_error = _tool_error_result(failure, mutation="publish_result")
        text = rendered_error["content"][0]["text"]
        self.assertNotIn("Field:", text)
        self.assertNotIn("Handle rule:", text)

        unicode_heavy = dict(arguments)
        unicode_heavy["summary"] = "🔒" * 16_384
        self.assertLess(len(unicode_heavy["summary"]), MCP_OPERATION_MAX_BYTES)
        with self.assertRaises(_SchemaError) as unicode_error:
            _validate_schema(schema, unicode_heavy)
        self.assertEqual(
            unicode_error.exception.actual_bytes,
            encoded_bytes(unicode_heavy),
        )
        self.assertGreater(
            unicode_error.exception.actual_bytes,
            MCP_OPERATION_MAX_BYTES,
        )

        large = _success_tool_result({"data": "z" * 100_000})
        self.assertEqual(
            large["content"][-1]["text"],
            "Complete Cortex result is available in structuredContent.",
        )
        self.assertEqual(len(large["structuredContent"]["data"]), 100_000)
        self.assertLess(
            len(json.dumps(large, separators=(",", ":")).encode("utf-8")),
            MAX_PHYSICAL_JSONL_FRAME_BYTES,
        )

    def test_governance_mode_is_structurally_required(self) -> None:
        schema = PUBLIC_TOOLS["assess_governance"]["inputSchema"]
        with self.assertRaises(_SchemaError) as caught:
            _validate_schema(schema, {"task_ref": "t_" + "a" * 12})
        self.assertEqual(caught.exception.path, "$.mode")
        self.assertEqual(caught.exception.missing_fields, ("mode",))


if __name__ == "__main__":
    unittest.main()
