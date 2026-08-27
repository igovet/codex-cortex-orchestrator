"""Focused coverage for canonical public inputs and approval-view freshness."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS, SERVER_INSTRUCTIONS, SERVER_VERSION  # noqa: E402
from cortex_runtime.mcp_api import _SchemaError, _validate_schema, serve_stdio  # noqa: E402
from cortex_runtime.public_contracts import build_public_contracts  # noqa: E402
from cortex_runtime.v12_contract import record_ref, task_ref  # noqa: E402
from cortex_runtime.v12_service import V12ServiceError, read_reports, record_user_decision  # noqa: E402
from cortex_runtime.v12_store import V12Store  # noqa: E402


class V12CompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cortex-v12-compatibility-")
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

    def _plan(self, store: V12Store, objective: str = "Compatibility plan") -> tuple[str, str]:
        task = store.create_task(
            objective=objective,
            user_request_original=objective,
            user_language="en",
            task_contract_version="cortex/task-contract/v1",
            requirements=["Keep compatibility bounded."],
            constraints=["Do not weaken approval binding."],
            acceptance_criteria=["Only canonical public fields are accepted."],
            verification_plan=["Run focused tests."],
            context={},
        )[0]["task"]["task_id"]
        delegation = store.create_delegation(
            task_id=task,
            objective="Produce a compatibility plan.",
            role="planner",
            profile_name="planner",
            scope="Own only the compatibility plan.",
            instructions="Return the bounded plan.",
            model="gpt-5.6-luna",
            reasoning_effort="high",
        )[0]["delegation"]["delegation_id"]
        report = store.submit_report(
            task_id=task,
            delegation_id=delegation,
            report_type="plan",
            status="completed",
            content={"steps": ["Preserve immutable plan digest."]},
        )[0]["report"]
        return task, str(report["report_id"])

    def _public_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        """Invoke one actual public MCP call through its schema gate."""
        request_lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "canonical-input-test", "version": "1"},
            }},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        ]
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("\n".join(json.dumps(item) for item in request_lines) + "\n")), mock.patch("sys.stdout", output):
            serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        reply = next(item for item in replies if item.get("id") == 2)
        result = reply.get("result")
        self.assertIsInstance(result, dict)
        return result

    def _successful_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = self._public_tool(name, arguments)
        self.assertIsNot(result.get("isError"), True, result)
        structured = result.get("structuredContent")
        self.assertIsInstance(structured, dict)
        return structured

    def _rejected_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = self._public_tool(name, arguments)
        self.assertIs(result.get("isError"), True)
        self.assertNotIn("structuredContent", result)
        return result

    def test_fresh_schema_records_advisory_migration_without_retired_gate_table(self) -> None:
        store = V12Store(self.project)
        with sqlite3.connect(store.database_path) as connection:
            migrations = connection.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall()
            retired_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='governance_gates'"
            ).fetchone()
            retired_index = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='governance_gates_assessment'"
            ).fetchone()
        self.assertEqual(migrations[-1], (8, "v12-advisory-governance"))
        self.assertIsNone(retired_table)
        self.assertIsNone(retired_index)

    def test_advisory_closure_contract_does_not_require_initiative_order(self) -> None:
        contracts = build_public_contracts()
        closure_description = contracts["submit_governance_closure"]["description"]
        self.assertIn("never block a safe operation", closure_description)
        self.assertIn("neither that follow-up nor any initiative-first order is required", closure_description)
        self.assertNotIn("first requires a finalized", closure_description)
        next_action = contracts["submit_governance_closure"]["outputSchema"]["properties"]["next_action"]
        self.assertIn("never requires that closure", next_action["description"])

    def test_schema_failures_name_output_only_and_approval_relation_fields(self) -> None:
        task = self._successful_tool("create_task", {
            "project_root": str(self.project),
            "objective": "Verify public validation diagnostics.",
            "user_request_original": "Verify public validation diagnostics.",
            "user_language": "en",
            "task_contract_version": "cortex/task-contract/v1",
            "requirements": ["Use only advertised public fields."],
            "constraints": ["Do not mutate on schema rejection."],
            "acceptance_criteria": ["Failures name the repairable field."],
            "verification_plan": ["Exercise the MCP schema boundary."],
        })
        task_ref = task["handles"]["task_ref"]
        self.assertIsInstance(task_ref, str)
        before = self._successful_tool("inspect_task", {"task_ref": task_ref})

        rejected_gate = self._rejected_tool("set_governance_mode", {
            "task_ref": task_ref,
            "mode": "minimal",
            "governance_gate": {},
        })
        gate_text = rejected_gate["content"][0]["text"]
        self.assertIn("governance_gate is a removed workflow projection", gate_text)
        self.assertIn("Location: $.", gate_text)
        after_rejection = self._successful_tool("inspect_task", {"task_ref": task_ref})
        self.assertEqual(after_rejection["timeline"], before["timeline"])

        accepted = self._successful_tool("set_governance_mode", {
            "task_ref": task_ref,
            "mode": "minimal",
        })
        self.assertNotIn("governance_gate", accepted)

        rejected_approval = self._rejected_tool("record_user_decision", {
            "task_ref": task_ref,
            "subject_type": "plan",
            "subject_ref": "r_000000000000",
            "subject_digest": "sha256:" + ("0" * 64),
            "decision_type": "approve",
            "prompt_en": "Approve?",
            "response_original": "Approve.",
            "response_en": "I approve.",
            "user_language": "en",
        })
        approval_text = rejected_approval["content"][0]["text"]
        self.assertIn("approval_handle, approval_view_content_digest, approval_view_source_sequence", approval_text)

    def test_canonical_decision_contract_rejects_aliases_before_mutation(self) -> None:
        task = self._successful_tool("create_task", {
            "project_root": str(self.project),
            "objective": "Verify one canonical decision request.",
            "user_request_original": "Verify one canonical decision request.",
            "user_language": "en",
            "task_contract_version": "cortex/task-contract/v1",
            "requirements": ["Use the public schema."],
            "constraints": ["Do not infer request fields."],
            "acceptance_criteria": ["Only canonical decision inputs mutate."],
            "verification_plan": ["Exercise the MCP schema boundary."],
        })
        task_ref = task["handles"]["task_ref"]
        self.assertIsInstance(task_ref, str)
        delegation = self._successful_tool("create_delegation", {
            "task_ref": task_ref,
            "objective": "Produce the plan evidence.",
            "role": "planner",
            "profile_name": "planner",
            "scope": "Own the canonical plan evidence.",
            "instructions": "Return one bounded plan report.",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "high",
        })
        delegation_ref = delegation["handles"]["delegation_ref"]
        self.assertIsInstance(delegation_ref, str)
        worker_brief = delegation["worker_brief"]
        native_dispatch = worker_brief["native_dispatch"]
        self.assertEqual(native_dispatch["task_name"], worker_brief["native_task_name"])
        self.assertEqual(native_dispatch["native_arguments"]["message"], worker_brief["worker_message"])
        self.assertEqual(native_dispatch["native_arguments"]["reasoning_effort"], "high")
        self.assertEqual(native_dispatch["native_arguments"]["fork_turns"], "none")
        self.assertNotIn("model", native_dispatch["native_arguments"])
        plan = self._successful_tool("submit_report", {
            "delegation_ref": delegation_ref,
            "report_type": "plan",
            "status": "completed",
            "content": {"steps": ["Use only canonical public fields."]},
        })
        # Context recovery must accept the same complete, server-issued
        # relation shape returned from a bounded plan read, not only the
        # original submission receipt.
        recovered = self._successful_tool("read_reports", {
            "report_refs": [plan["handles"]["report_ref"]],
        })
        approval_view = recovered.get("approval_view")
        self.assertIsInstance(approval_view, dict)
        self.assertEqual(approval_view.get("status"), "ready")
        valid = {
            "task_ref": task_ref,
            "subject_type": "plan",
            "subject_ref": approval_view["report_ref"],
            "subject_digest": approval_view["report_content_digest"],
            "decision_type": "approve",
            "prompt_en": "Approve this canonical plan?",
            "response_original": "Approve.",
            "response_en": "I approve the plan.",
            "user_language": "en",
            "approval_handle": approval_view["approval_handle"],
            "approval_view_content_digest": approval_view["content_digest"],
            "approval_view_source_sequence": approval_view["source_sequence"],
        }
        accepted = self._successful_tool("record_user_decision", valid)
        self.assertEqual(accepted["decision"]["subject_digest"], valid["subject_digest"])

        successor = self._successful_tool("create_delegation", {
            "task_ref": task_ref,
            "objective": "Consume the finalized same-task plan.",
            "role": "qa",
            "profile_name": "qa_engineer",
            "scope": "Read only the declared plan evidence.",
            "instructions": "Read the exact predecessor report before verification.",
            "input_report_refs": [valid["subject_ref"]],
            "input_decision_refs": [accepted["handles"]["decision_ref"]],
            "approval_decision_ref": accepted["handles"]["decision_ref"],
            "model": "gpt-5.6-luna",
            "reasoning_effort": "high",
        })
        same_task_read = self._successful_tool("read_reports", {
            "report_refs": [valid["subject_ref"]],
            "reader_kind": "worker",
            "consumer_delegation_ref": successor["handles"]["delegation_ref"],
        })
        self.assertEqual(same_task_read["consumption_receipts"][0]["reader_kind"], "worker")

        before = self._successful_tool("inspect_task", {"task_ref": task_ref})
        before_count = len(before["decisions"])
        invalid_inputs = (
            {name: value for name, value in valid.items() if name != "task_ref"},
            valid | {"decision": "approve"},
            {
                "task_ref": task_ref,
                "report_ref": valid["subject_ref"],
                "report_content_digest": valid["subject_digest"],
                "decision": "approve",
                "user_response_original": "Approve.",
                "english_normalization": "I approve the plan.",
            },
            {name: value for name, value in valid.items() if name != "approval_handle"},
        )
        for arguments in invalid_inputs:
            self._rejected_tool("record_user_decision", arguments)
        after = self._successful_tool("inspect_task", {"task_ref": task_ref})
        self.assertEqual(len(after["decisions"]), before_count)

    def test_advisory_governance_never_gates_dispatch_reports_or_closure(self) -> None:
        task = self._successful_tool("create_task", {
            "project_root": str(self.project), "objective": "Advisory governance only.",
            "user_request_original": "Advisory governance only.", "user_language": "en",
            "task_contract_version": "cortex/task-contract/v1", "requirements": ["Keep modes advisory."],
            "constraints": ["Do not weaken reference validation."],
            "acceptance_criteria": ["Safe work continues without a workflow gate."],
            "verification_plan": ["Exercise a light-mode non-planner delegation."],
        })
        task_ref_value = task["handles"]["task_ref"]
        self._successful_tool("set_governance_mode", {"task_ref": task_ref_value, "mode": "full"})
        delegation = self._successful_tool("create_delegation", {
            "task_ref": task_ref_value, "objective": "Write non-planner plan evidence.",
            "role": "writer", "profile_name": "technical_writer", "scope": "One advisory report.",
            "instructions": "Submit a bounded plan report.", "model": "gpt-5.6-luna", "reasoning_effort": "high",
        })
        self._successful_tool("submit_report", {
            "delegation_ref": delegation["handles"]["delegation_ref"], "report_type": "plan",
            "status": "completed", "content": {"advisory": True},
        })
        closure = self._successful_tool("submit_governance_closure", {
            "task_ref": task_ref_value, "subject_type": "task", "subject_ref": task_ref_value,
            "verdict": "ready", "evidence": {"checked": "advisory"}, "unresolved_risks": [], "follow_ups": [],
        })
        self.assertEqual(closure["closure"]["verdict"], "ready")

    def test_inspect_task_exposes_exact_recovery_dispatch_without_lifecycle_claim(self) -> None:
        task = self._successful_tool("create_task", {
            "project_root": str(self.project), "objective": "Recover an open delegation.",
            "user_request_original": "Recover an open delegation.", "user_language": "en",
            "task_contract_version": "cortex/task-contract/v1", "requirements": ["Recover exact dispatch data."],
            "constraints": ["Never duplicate host work."],
            "acceptance_criteria": ["Recovered data is byte-identical."],
            "verification_plan": ["Compare create and inspect receipts."],
        })
        created = self._successful_tool("create_delegation", {
            "task_ref": task["handles"]["task_ref"], "objective": "Remediate PII logging.",
            "role": "implementer", "profile_name": "backend_dev", "scope": "PII remediation only.",
            "instructions": "Preserve exact recovery identity.", "model": "gpt-5.6-luna", "reasoning_effort": "high",
        })
        inspected = self._successful_tool("inspect_task", {"task_ref": task["handles"]["task_ref"]})
        continuation = next(item for item in inspected["continuations"] if item["delegation"]["delegation_id"] == created["delegation"]["delegation_id"])
        self.assertEqual(continuation["dispatch_state"], "ledger_unknown")
        self.assertEqual(continuation["handoff_state"], "report_required")
        self.assertEqual(continuation["recovery_requirement"], "finalized_report_or_explicit_handoff_or_parent_linked_replacement")
        recovered = self._successful_tool("read_delegation", {
            "delegation_ref": created["handles"]["delegation_ref"], "after_sequence": 0,
        })
        self.assertEqual(recovered["worker_brief"]["native_dispatch"], created["worker_brief"]["native_dispatch"])

    def test_storage_unavailable_preserves_pending_decision_and_mutates_nothing(self) -> None:
        store = V12Store(self.project)
        task_id, report_id = self._plan(store, "Unavailable decision")
        report = store._read(lambda connection: store._report(connection, report_id, task_id=task_id))
        before = store.inspect_task(task_id=task_id, after_sequence=0)["decisions"]
        pending = {
            "task_ref": task_ref(task_id), "subject_type": "plan", "subject_ref": record_ref(report_id),
            "subject_digest": report["content_digest"], "decision_type": "approve", "prompt_en": "Approve?",
            "response_original": "Approve.", "response_en": "I approve.", "user_language": "en",
            "approval_handle": "approval-unavailable", "approval_view_content_digest": "sha256:" + "0" * 64,
            "approval_view_source_sequence": 0,
        }
        with mock.patch("cortex_runtime.v12_service._task_store", side_effect=V12ServiceError("V12 storage is unavailable", code="storage_unavailable")):
            with self.assertRaises(V12ServiceError) as unavailable:
                record_user_decision(**pending)
        self.assertEqual(unavailable.exception.code, "storage_unavailable")
        self.assertEqual(pending["response_original"], "Approve.")
        self.assertEqual(store.inspect_task(task_id=task_id, after_sequence=0)["decisions"], before)

    def test_canonical_schemas_reject_removed_aliases_and_string_budget(self) -> None:
        contracts = build_public_contracts()
        decision_schema = contracts["record_user_decision"]["inputSchema"]
        read_schema = contracts["read_reports"]["inputSchema"]
        governance_schema = contracts["set_governance_mode"]["inputSchema"]
        self.assertNotIn("oneOf", decision_schema)
        self.assertNotIn("report_ref", decision_schema["properties"])
        self.assertNotIn("byte_budget", read_schema["properties"])
        self.assertEqual(read_schema["properties"]["max_bytes"]["type"], "integer")
        self.assertNotIn("reason", governance_schema["properties"])
        with self.assertRaises(_SchemaError):
            _validate_schema(read_schema, {"report_refs": ["r_000000000000"], "max_bytes": "0"})
        with self.assertRaises(_SchemaError):
            _validate_schema(read_schema, {"report_refs": ["r_000000000000"], "byte_budget": 0})

    def test_approval_survives_unrelated_timeline_but_not_changed_file(self) -> None:
        store = V12Store(self.project)
        task_id, report_id = self._plan(store, "Approval freshness plan")
        relative = f"plans/revisions/{report_id}.md"
        view = store.human_view(task_id, relative)
        self.assertEqual(view["status"], "ready")
        handle = store.ready_approval_handle(
            task_id=task_id,
            report_id=report_id,
            report_content_digest=store._read(lambda connection: store._report(connection, report_id, task_id=task_id))["content_digest"],
            view_relative_path=relative,
            view_content_digest=view["content_digest"],
            view_source_sequence=view["source_sequence"],
        )
        report = store._read(lambda connection: store._report(connection, report_id, task_id=task_id))
        # This is the release-gate sequence: consuming the report records a
        # task-scoped receipt and advances chronology, while a wrong plan
        # digest must still fail at the immutable subject binding gate.
        read_reports(report_refs=[record_ref(report_id)], max_bytes=0)
        with self.assertRaises(V12ServiceError) as mismatch:
            record_user_decision(
                task_ref=task_ref(task_id),
                subject_type="plan",
                subject_ref=record_ref(report_id),
                subject_digest="sha256:" + "0" * 64,
                decision_type="approve",
                prompt_en="Approve this plan?",
                response_original="I approve this plan.",
                response_en="I approve this plan.",
                user_language="en",
                approval_handle=handle,
                approval_view_content_digest=view["content_digest"],
                approval_view_source_sequence=view["source_sequence"],
            )
        self.assertEqual(mismatch.exception.code, "decision_subject_digest_mismatch")
        store.record_initiative(
            task_id=task_id,
            goal="Unrelated chronology event.",
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
        approved, _ = store.record_user_decision(
            task_id=task_id,
            subject_type="plan",
            subject_id=report_id,
            subject_digest=report["content_digest"],
            decision_type="approve",
            prompt_en="Approve this plan?",
            response_original="I approve this plan.",
            response_en="I approve this plan.",
            user_language="en",
            approval_handle=handle,
            approval_view_content_digest=view["content_digest"],
            approval_view_source_sequence=view["source_sequence"],
            supersedes_decision_id=None,
            idempotency_key=None,
        )
        self.assertEqual(approved["decision"]["decision_type"], "approve")

        # A changed projection must not be accepted merely because its SQLite
        # registry row still says ready.
        task2, report2 = self._plan(store, "Changed projection plan")
        relative2 = f"plans/revisions/{report2}.md"
        view2 = store.human_view(task2, relative2)
        report2_row = store._read(lambda connection: store._report(connection, report2, task_id=task2))
        handle2 = store.ready_approval_handle(
            task_id=task2,
            report_id=report2,
            report_content_digest=report2_row["content_digest"],
            view_relative_path=relative2,
            view_content_digest=view2["content_digest"],
            view_source_sequence=view2["source_sequence"],
        )
        projection = Path(view2["path"])
        projection.write_text(projection.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
        with self.assertRaises(V12ServiceError):
            # Use the service to exercise the same public operation path.
            record_user_decision(
                task_ref=task_ref(task2),
                subject_type="plan",
                subject_ref=record_ref(report2),
                subject_digest=report2_row["content_digest"],
                decision_type="approve",
                prompt_en="Approve this plan?",
                response_original="I approve this plan.",
                response_en="I approve this plan.",
                user_language="en",
                approval_handle=handle2,
                approval_view_content_digest=view2["content_digest"],
                approval_view_source_sequence=view2["source_sequence"],
            )


if __name__ == "__main__":
    unittest.main()
