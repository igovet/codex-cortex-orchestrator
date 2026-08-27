"""Focused coverage for canonical public inputs and approval-view freshness."""
from __future__ import annotations

import io
import json
import os
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

    def _rejected_tool(self, name: str, arguments: dict[str, object]) -> None:
        result = self._public_tool(name, arguments)
        self.assertIs(result.get("isError"), True)
        self.assertNotIn("structuredContent", result)

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
        plan = self._successful_tool("submit_report", {
            "delegation_ref": delegation_ref,
            "report_type": "plan",
            "status": "completed",
            "content": {"steps": ["Use only canonical public fields."]},
        })
        approval_view = plan.get("approval_view")
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
