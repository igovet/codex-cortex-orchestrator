"""Focused coverage for canonical public inputs and approval-view freshness."""
from __future__ import annotations

import io
import json
import os
import re
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
from cortex_runtime.v12_store import V12Store, V12StoreError  # noqa: E402


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
        self._mutation_counter = 0
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
            idempotency_key=f"plan-task-{objective}",
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
            idempotency_key=f"plan-delegation-{objective}",
        )[0]["delegation"]["delegation_id"]
        started = store.submit_report(
            task_id=task,
            delegation_id=delegation,
            mode="begin",
            report_type="plan",
            idempotency_key=f"plan-begin-{objective}",
        )[0]
        report_id = str(started["report"]["report_id"])
        appended = store.submit_report(
            task_id=task, delegation_id=delegation, mode="append", report_id=report_id,
            chunk_index=0, section="body",
            content={"schema": "cortex/report/plan/v1", "summary": "Compatibility plan", "scope": [], "stages": [], "verification": []},
            idempotency_key=f"plan-append-{objective}",
        )[0]
        store.submit_report(
            task_id=task, delegation_id=delegation, mode="finalize", report_id=report_id,
            status="completed", expected_chunk_count=appended["expected_chunk_count"],
            expected_content_digest=appended["expected_content_digest"],
            idempotency_key=f"plan-finalize-{objective}",
        )
        return task, report_id

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
        self.assertNotIn("error", reply, "public tools/call must not degrade into a JSON-RPC error")
        result = reply.get("result")
        self.assertIsInstance(result, dict)
        return result

    def _successful_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        if name in {"create_task", "create_delegation", "submit_report", "set_governance_mode", "record_initiative", "submit_governance_closure", "record_user_decision"}:
            arguments = dict(arguments)
            self._mutation_counter += 1
            arguments.setdefault("idempotency_key", f"test-{name}-{self._mutation_counter}")
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
        self.assertEqual(migrations[-1], (10, "v12-effective-outcome-coverage"))
        self.assertIsNone(retired_table)
        self.assertIsNone(retired_index)

    def test_store_submit_report_requires_explicit_mode(self) -> None:
        store = V12Store(self.project)
        task = store.create_task(
            objective="Require an explicit report operation.",
            user_request_original="Require an explicit report operation.",
            user_language="en",
            task_contract_version="cortex/task-contract/v1",
            requirements=["Use the explicit report operation."],
            constraints=["Do not retain mode-less compatibility."],
            acceptance_criteria=["Omitted mode is rejected before mutation."],
            verification_plan=["Run the focused negative test."],
            context={},
            idempotency_key="explicit-mode-task",
        )[0]["task"]["task_id"]
        delegation = store.create_delegation(
            task_id=task,
            objective="Produce one report.",
            role="writer",
            profile_name="general",
            scope="Own one bounded report.",
            instructions="Submit one explicit report operation.",
            model="gpt-5.6-luna",
            reasoning_effort="high",
            idempotency_key="explicit-mode-delegation",
        )[0]["delegation"]["delegation_id"]
        before = store.inspect_task(task_id=task, after_sequence=0)["timeline"]
        with self.assertRaises(V12StoreError) as rejected:
            store.submit_report(
                task_id=task,
                delegation_id=delegation,
                report_type="result",
                status="completed",
                content={"result": "must not be stored"},
            )
        self.assertEqual(rejected.exception.code, "invalid_argument")
        self.assertEqual(rejected.exception.details.get("field"), "mode")
        after = store.inspect_task(task_id=task, after_sequence=0)["timeline"]
        self.assertEqual(after, before)

    def test_direct_store_mints_a_retry_key_for_an_unkeyed_first_mutation(self) -> None:
        """A first mutation returns the only retry identity a caller may reuse."""
        store = V12Store(self.project)
        arguments = {
            "objective": "Reject an unkeyed direct mutation.",
            "user_request_original": "Reject an unkeyed direct mutation.",
            "user_language": "en",
            "task_contract_version": "cortex/task-contract/v1",
            "requirements": ["Require a caller-owned key."],
            "constraints": ["Do not commit an unkeyed mutation."],
            "acceptance_criteria": ["No token or timeline row is created."],
            "verification_plan": ["Inspect the write guard and durable state."],
            "context": {},
        }
        first, replayed = store.create_task(**arguments)
        minted_key = first["idempotency_key"]
        self.assertFalse(replayed)
        self.assertIsInstance(minted_key, str)
        self.assertTrue(minted_key)
        self.assertEqual(first["retry_handle"], minted_key)
        replay, replayed = store.create_task(**(arguments | {"idempotency_key": minted_key}))
        self.assertTrue(replayed)
        self.assertEqual(replay, first)
        before = store.inspect_task(task_id=first["task"]["task_id"], after_sequence=0)["timeline"]
        with self.assertRaises(V12StoreError) as conflict:
            store.create_task(**(arguments | {"objective": "Changed direct store payload.", "idempotency_key": minted_key}))
        self.assertEqual(conflict.exception.code, "idempotency_conflict")
        self.assertEqual(store.inspect_task(task_id=first["task"]["task_id"], after_sequence=0)["timeline"], before)

    def test_direct_store_idempotency_replays_exactly_and_conflicts_without_mutation(self) -> None:
        store = V12Store(self.project)
        arguments = {
            "objective": "Replay one direct store mutation.",
            "user_request_original": "Replay one direct store mutation.",
            "user_language": "en",
            "task_contract_version": "cortex/task-contract/v1",
            "requirements": ["Use one stable caller key."],
            "constraints": ["Preserve replay bytes."],
            "acceptance_criteria": ["Equal payloads replay; changed payloads conflict."],
            "verification_plan": ["Compare the returned values and timeline."],
            "context": {},
            "idempotency_key": "direct-replay-key",
        }
        first, first_replayed = store.create_task(**arguments)
        second, second_replayed = store.create_task(**arguments)
        self.assertFalse(first_replayed)
        self.assertTrue(second_replayed)
        self.assertEqual(second, first)
        before = store.inspect_task(task_id=first["task"]["task_id"], after_sequence=0)["timeline"]
        with self.assertRaises(V12StoreError) as conflict:
            store.create_task(**(arguments | {"objective": "Changed direct store payload."}))
        self.assertEqual(conflict.exception.code, "idempotency_conflict")
        after = store.inspect_task(task_id=first["task"]["task_id"], after_sequence=0)["timeline"]
        self.assertEqual(after, before)

    def test_advisory_closure_contract_does_not_require_initiative_order(self) -> None:
        contracts = build_public_contracts()
        closure_description = contracts["submit_governance_closure"]["description"]
        self.assertIn("automatically inspect", closure_description)
        self.assertIn("same-idempotency retry", closure_description)
        self.assertIn("remains intact", closure_description)
        self.assertIn("Governance remains nonblocking", closure_description)
        self.assertNotIn("first requires a finalized", closure_description)
        next_action = contracts["submit_governance_closure"]["outputSchema"]["properties"]["next_action"]
        self.assertIn("not a complete callable payload", next_action["description"])
        self.assertIn("closure=null must omit", next_action["description"])
        closure_relations = contracts["submit_governance_closure"]["outputSchema"]["oneOf"]
        self.assertEqual(closure_relations[0]["properties"]["closure"]["type"], "null")
        self.assertEqual(closure_relations[0]["properties"]["next_action"]["not"], {"required": ["advisory_status"]})
        self.assertEqual(closure_relations[1]["properties"]["closure"]["type"], "object")
        for tool in ("inspect_task", "submit_governance_closure"):
            execution_outcome = contracts[tool]["outputSchema"]["properties"]["execution_outcome"]
            self.assertEqual(
                set(execution_outcome["properties"]),
                {"evidence_status", "finalized_report_count", "completed_report_count", "outcome"},
            )
            self.assertEqual(execution_outcome["properties"]["outcome"]["type"], ["string", "null"])
            self.assertEqual(set(execution_outcome["properties"]["outcome"]["enum"]), {"completed", "incomplete", None})

    def test_schema_failures_name_output_only_and_approval_relation_fields(self) -> None:
        task = self._successful_tool("create_task", {
            "project_root": str(self.project),
            "objective": "Verify public validation diagnostics.",
            "user_request_original": "Verify public validation diagnostics.",
            "user_language": "en",
            "requirements": ["Use only advertised public fields."],
            "constraints": ["Do not mutate on schema rejection."],
            "acceptance_criteria": ["Failures name the repairable field."],
            "verification_plan": ["Exercise the MCP schema boundary."],
        })
        task_ref = task["handles"]["task_ref"]
        before = self._successful_tool("inspect_task", {"task_ref": task_ref})["timeline"]
        self.assertIsInstance(task_ref, str)
        before = self._successful_tool("inspect_task", {"task_ref": task_ref})

        rejected_gate = self._rejected_tool("set_governance_mode", {
            "task_ref": task_ref,
            "mode": "minimal",
            "governance_gate": {},
            "idempotency_key": "removed-gate",
        })
        gate_text = rejected_gate["content"][0]["text"]
        self.assertIn("governance_gate is a removed workflow projection", gate_text)
        self.assertIn("Field: governance_gate.", gate_text)
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
            "prompt": "Approve?",
            "response_original": "Approve.",
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
        native_dispatch = delegation["native_dispatch"]
        renderer = delegation["renderer"]
        self.assertEqual(native_dispatch["task_name"], delegation["delegation"]["native_task_name"])
        self.assertEqual(native_dispatch["selection"], {
            "model": "gpt-5.6-luna", "reasoning_effort": "high",
        })
        self.assertEqual(native_dispatch["native_arguments"]["reasoning_effort"], "high")
        self.assertEqual(native_dispatch["native_arguments"]["fork_turns"], "none")
        self.assertNotIn("model", native_dispatch["native_arguments"])
        self.assertEqual(renderer["profile_name"], "planner")
        self.assertEqual(renderer["profile_state"], "loaded")
        self.assertNotIn("worker_brief", delegation)
        self.assertNotIn("worker_message", delegation)
        self.assertNotIn("native_dispatch", delegation["delegation"])
        omitted_mode = self._rejected_tool("submit_report", {
            "delegation_ref": delegation_ref,
            "report_type": "plan",
            "status": "completed",
            "content": {"steps": ["Mode is required."]},
            "idempotency_key": "missing-mode",
        })
        self.assertIn("mode", omitted_mode["content"][0]["text"])
        started = self._successful_tool("submit_report", {
            "delegation_ref": delegation_ref, "mode": "begin", "report_type": "plan",
        })
        appended = self._successful_tool("submit_report", {
            "delegation_ref": delegation_ref, "mode": "append", "report_ref": started["handles"]["report_ref"],
            "chunk_index": started["handles"]["chunk_index"], "section": "body",
            "content": {"schema": "cortex/report/plan/v1", "summary": "Canonical public fields", "scope": [], "stages": [], "verification": []},
        })
        plan = self._successful_tool("submit_report", {
            "delegation_ref": delegation_ref, "mode": "finalize", "report_ref": started["handles"]["report_ref"],
            "status": "completed", "expected_chunk_count": appended["handles"]["expected_chunk_count"],
            "expected_content_digest": appended["handles"]["expected_content_digest"],
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
        binding = recovered["handles"].get("decision_binding")
        self.assertIsInstance(binding, dict)
        non_approval = {
            key: value for key, value in binding.items()
            if key not in {"approval_handle", "approval_view_content_digest", "approval_view_source_sequence"}
        }
        canonical_decision_refs = []
        for decision_type, original, normalized in (
            ("request_revision", "Please revise stage two.", "Please revise stage two."),
            ("cancel", "Cancel this plan.", "Cancel this plan."),
        ):
            first_attempt = dict(non_approval)
            first_attempt.update({
                "decision_type": decision_type,
                "prompt": "Record the user decision.",
                "response_original": original,
                "user_language": "en",
            })
            persisted = self._successful_tool("record_user_decision", first_attempt)
            self.assertEqual(persisted["decision"]["subject_digest"], first_attempt["subject_digest"])
            canonical_decision_refs.append(persisted["handles"]["decision_ref"])
        valid = dict(binding)
        valid.update({
            "decision_type": "approve",
            "prompt": "Approve this canonical plan?",
            "response_original": "Одобряю план.",
            "user_language": "ru",
        })
        accepted = self._successful_tool("record_user_decision", valid)
        canonical_decision_refs.append(accepted["handles"]["decision_ref"])
        self.assertEqual(accepted["decision"]["subject_digest"], valid["subject_digest"])
        self.assertNotIn("response_en_excerpt", accepted["decision"])
        self.assertNotIn("response_original", accepted["decision"])
        steering = self._successful_tool("record_user_decision", {
            "task_ref": task_ref, "subject_type": "task", "subject_ref": task_ref,
            "decision_type": "steer", "prompt": "Choose the next safe action.",
            "response_original": "Continue with focused verification.",
            "user_language": "en",
        })
        self.assertEqual(steering["decision"]["decision_type"], "steer")
        canonical_decision_refs.append(steering["handles"]["decision_ref"])

        successor = self._successful_tool("create_delegation", {
            "task_ref": task_ref,
            "objective": "Consume the finalized same-task plan.",
            "role": "qa",
            "profile_name": "qa_engineer",
            "scope": "Read only the declared plan evidence.",
            "instructions": "Read the exact predecessor report before verification.",
            "input_report_refs": [valid["subject_ref"]],
            "input_decision_refs": [accepted["handles"]["decision_ref"]],
            "model": "gpt-5.6-luna",
            "reasoning_effort": "high",
        })
        same_task_read = self._successful_tool("read_reports", {
            "report_refs": [valid["subject_ref"]],
            "consumer_delegation_ref": successor["handles"]["delegation_ref"],
        })
        self.assertEqual(same_task_read["consumption_receipts"][0]["reader_kind"], "worker")
        coordinator_read = self._successful_tool("read_reports", {"report_refs": [valid["subject_ref"]]})
        self.assertTrue(coordinator_read["reports"][0]["chunks"])
        self.assertEqual(coordinator_read["consumption_receipts"][0]["reader_kind"], "coordinator")

        before = self._successful_tool("inspect_task", {"task_ref": task_ref})
        before_count = len(before["decisions"])
        conformance_review = before.get("conformance_review")
        self.assertIsInstance(conformance_review, dict)
        decision_refs = conformance_review.get("decision_refs")
        self.assertEqual(
            decision_refs,
            canonical_decision_refs,
        )
        self.assertTrue(all(isinstance(ref, str) and re.fullmatch(r"u_[0-9a-f]{12}", ref) for ref in decision_refs))
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
            "requirements": ["Keep modes advisory."],
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
        started = self._successful_tool("submit_report", {
            "delegation_ref": delegation["handles"]["delegation_ref"], "mode": "begin", "report_type": "plan",
        })
        appended = self._successful_tool("submit_report", {
            "delegation_ref": delegation["handles"]["delegation_ref"], "mode": "append", "report_ref": started["handles"]["report_ref"],
            "chunk_index": 0, "section": "body", "content": {"advisory": True},
        })
        invalid = self._successful_tool("submit_report", {
            "delegation_ref": delegation["handles"]["delegation_ref"], "mode": "finalize", "report_ref": started["handles"]["report_ref"],
            "status": "completed", "expected_chunk_count": appended["handles"]["expected_chunk_count"],
            "expected_content_digest": appended["handles"]["expected_content_digest"],
        })
        self.assertEqual(invalid["report"]["storage_status"], "storage_valid")
        self.assertEqual(invalid["report"]["semantic_status"], "legacy")
        self.assertEqual(invalid["approval_view"]["status"], "unavailable")
        closure = self._successful_tool("submit_governance_closure", {
            "task_ref": task_ref_value, "subject_type": "task", "subject_ref": task_ref_value,
            "verdict": "ready", "evidence": {"checked": "advisory"}, "unresolved_risks": [], "follow_ups": [],
        })
        self.assertEqual(closure["closure"]["verdict"], "ready")

    def test_inspect_task_exposes_exact_recovery_dispatch_without_lifecycle_claim(self) -> None:
        task = self._successful_tool("create_task", {
            "project_root": str(self.project), "objective": "Recover an open delegation.",
            "user_request_original": "Recover an open delegation.", "user_language": "en",
            "requirements": ["Recover exact dispatch data."],
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
        self.assertEqual(recovered["worker_brief"]["native_dispatch"], created["native_dispatch"])

    def test_task_closure_is_schema_safe_and_confirmable(self) -> None:
        task = self._successful_tool("create_task", {
            "project_root": str(self.project), "objective": "Confirm completed-task closure.",
            "user_request_original": "Confirm completed-task closure.", "user_language": "en",
            "requirements": ["Attempt closure after completion evidence."],
            "constraints": ["Never claim an unverified closure."],
            "acceptance_criteria": ["A successful closure is inspectable."],
            "verification_plan": ["Exercise task and initiative closure branches."],
        })
        task_ref_value = task["handles"]["task_ref"]
        before = self._successful_tool("inspect_task", {"task_ref": task_ref_value})
        self.assertEqual(before["execution_outcome"]["evidence_status"], "no_finalized_reports")
        self.assertEqual(before["execution_outcome"]["finalized_report_count"], 0)
        self.assertEqual(before["execution_outcome"]["completed_report_count"], 0)
        self.assertIsNone(before["execution_outcome"]["outcome"])
        self.assertEqual(
            set(before["execution_outcome"]),
            {"evidence_status", "finalized_report_count", "completed_report_count", "outcome"},
        )
        self.assertEqual(before["advisory_closure"]["record_status"], "not_recorded")
        self.assertNotIn("closure_state", before["task"])
        invalid_ref = self._rejected_tool("submit_governance_closure", {
            "task_ref": task_ref_value, "subject_type": "task", "subject_ref": "i_000000000000",
            "verdict": "ready", "evidence": {"completed": True}, "idempotency_key": "invalid-closure-ref",
        })
        self.assertIn("subject_ref", invalid_ref["content"][0]["text"])
        invalid_status = self._rejected_tool("submit_governance_closure", {
            "task_ref": task_ref_value, "subject_type": "task", "subject_ref": task_ref_value,
            "verdict": "ready", "evidence": {"completed": True}, "initiative_status": "closed", "idempotency_key": "invalid-closure-status",
        })
        self.assertIn("initiative_status", invalid_status["content"][0]["text"])
        self.assertEqual(self._successful_tool("inspect_task", {"task_ref": task_ref_value})["timeline"], before["timeline"])

        initiative = self._successful_tool("record_initiative", {
            "task_ref": task_ref_value, "goal": "Close the completed task with evidence.", "linked_task_refs": [task_ref_value],
        })
        initiative_ref = initiative["handles"]["initiative_ref"]
        revised = self._successful_tool("record_initiative", {
            "task_ref": task_ref_value, "initiative_ref": initiative_ref, "status": "completed",
        })
        self.assertEqual(revised["initiative"]["goal"], "Close the completed task with evidence.")
        initiative_closure = self._successful_tool("submit_governance_closure", {
            "task_ref": task_ref_value, "subject_type": "initiative", "subject_ref": initiative_ref,
            "verdict": "ready", "evidence": {"completed": True}, "initiative_status": "closed",
        })
        suggested = initiative_closure["next_action"]["suggested_subject"]
        self.assertEqual(suggested["subject_ref"], task_ref_value)
        self.assertNotIn("arguments", initiative_closure["next_action"])
        task_closure = self._successful_tool("submit_governance_closure", {
            "task_ref": task_ref_value, "subject_type": "task", "subject_ref": task_ref_value,
            "verdict": "ready", "evidence": {"completed": True, "closure_attempt": "verified"},
        })
        self.assertEqual(task_closure["next_action"]["advisory_status"], "recorded")
        self.assertEqual(task_closure["closure_confirmation"]["inspection_status"], "confirmed")
        self.assertEqual(task_closure["closure_confirmation"]["reason"], "record_inspected")
        self.assertEqual(task_closure["execution_outcome"], before["execution_outcome"])
        self.assertNotIn("closure_state", task_closure["closure"])
        self.assertNotIn("task_closed", json.dumps(task_closure, sort_keys=True))
        inspected = self._successful_tool("inspect_governance", {"task_ref": task_ref_value})
        self.assertTrue(any(item["closure_id"] == task_closure["closure"]["closure_id"] for item in inspected["closures"]))

    def test_finalized_reports_expose_neutral_execution_evidence_independent_of_advice(self) -> None:
        def exercise(verdict: str) -> dict[str, object]:
            task = self._successful_tool("create_task", {
                "project_root": str(self.project), "objective": f"Separate {verdict} outcome.",
                "user_request_original": f"Separate {verdict} outcome.", "user_language": "en",
                "requirements": ["Keep execution and advice separate."],
                "constraints": ["Closure is nonblocking."],
                "acceptance_criteria": ["Finalized evidence survives advisory state."],
                "verification_plan": ["Inspect the public projections."],
            })
            task_ref_value = task["handles"]["task_ref"]
            before = self._successful_tool("inspect_task", {"task_ref": task_ref_value})
            self.assertEqual(before["advisory_closure"]["record_status"], "not_recorded")
            delegation = self._successful_tool("create_delegation", {
                "task_ref": task_ref_value, "objective": "Provide completed evidence.", "role": "worker",
                "profile_name": "general", "scope": "Own one completed result.",
                "instructions": "Submit the completed result evidence.", "model": "gpt-5.6-luna", "reasoning_effort": "high",
            })
            started = self._successful_tool("submit_report", {"delegation_ref": delegation["handles"]["delegation_ref"], "mode": "begin", "report_type": "result"})
            appended = self._successful_tool("submit_report", {
                "delegation_ref": delegation["handles"]["delegation_ref"], "mode": "append", "report_ref": started["handles"]["report_ref"],
                "chunk_index": 0, "section": "body", "content": {"schema": "cortex/report/result/v1", "summary": "Recorded evidence", "outcome": "evidence_recorded", "changes": [], "verification": [], "risks": []},
            })
            self._successful_tool("submit_report", {
                "delegation_ref": delegation["handles"]["delegation_ref"], "mode": "finalize", "report_ref": started["handles"]["report_ref"], "status": "completed",
                "expected_chunk_count": appended["handles"]["expected_chunk_count"], "expected_content_digest": appended["handles"]["expected_content_digest"],
            })
            completed = self._successful_tool("inspect_task", {"task_ref": task_ref_value})
            self.assertEqual(completed["execution_outcome"]["evidence_status"], "finalized_reports_present")
            self.assertEqual(completed["execution_outcome"]["finalized_report_count"], 1)
            self.assertEqual(completed["execution_outcome"]["completed_report_count"], 1)
            self.assertEqual(completed["execution_outcome"]["outcome"], "completed")
            self.assertEqual(
                set(completed["execution_outcome"]),
                {"evidence_status", "finalized_report_count", "completed_report_count", "outcome"},
            )
            closure = self._successful_tool("submit_governance_closure", {
                "task_ref": task_ref_value, "subject_type": "task", "subject_ref": task_ref_value,
                "verdict": verdict, "evidence": {"result": "completed"},
            })
            self.assertEqual(closure["execution_outcome"], completed["execution_outcome"])
            self.assertEqual(closure["closure_confirmation"]["inspection_status"], "confirmed")
            return closure

        self.assertEqual(exercise("ready")["closure"]["verdict"], "ready")
        self.assertEqual(exercise("ready_with_risks")["closure"]["verdict"], "ready_with_risks")
        self.assertEqual(exercise("not_ready")["closure"]["verdict"], "not_ready")

    def test_public_first_mutation_mints_callable_idempotency_key_and_schema_errors_are_safe(self) -> None:
        first_arguments = {
            "project_root": str(self.project), "objective": "Mint a first-call retry key.",
            "user_request_original": "Mint a first-call retry key.", "user_language": "en",
            "requirements": ["Permit omitted retry material."], "constraints": ["Keep replay exact."],
            "acceptance_criteria": ["Return a callable minted key."], "verification_plan": ["Replay and conflict."],
        }
        first = self._public_tool("create_task", first_arguments)
        self.assertIsNot(first.get("isError"), True, first)
        structured = first["structuredContent"]
        key = structured["handles"]["idempotency_key"]
        self.assertEqual(key, structured["idempotency_key"])
        self.assertEqual(structured["retry_handle"], key)
        self.assertNotIn("retry_handle", structured["handles"])
        replay = self._public_tool("create_task", first_arguments | {"idempotency_key": key})
        self.assertTrue(replay["structuredContent"]["replayed"])
        # A retry has the same durable result but its public replay marker changes.
        self.assertEqual(replay["structuredContent"]["task"], structured["task"])
        conflict = self._public_tool("create_task", first_arguments | {"objective": "Changed first-call payload.", "idempotency_key": key})
        self.assertIs(conflict.get("isError"), True)
        self.assertIn("idempotency_conflict", conflict["content"][0]["text"])

        contracts = build_public_contracts()
        submit_section = contracts["submit_report"]["inputSchema"]["properties"]["section"]
        read_section = contracts["read_reports"]["inputSchema"]["properties"]["sections"]["items"]
        self.assertEqual(submit_section["type"], read_section["type"])
        self.assertEqual(submit_section["maxLength"], read_section["maxLength"])
        self.assertEqual(submit_section["pattern"], read_section["pattern"])
        rejected = self._rejected_tool("submit_report", {
            "delegation_ref": "d_000000000000", "mode": "append", "report_ref": "r_000000000000",
            "chunk_index": 0, "section": "Unsafe Section", "content": {},
        })
        diagnostic = rejected["content"][0]["text"]
        self.assertIn("Field: section.", diagnostic)
        self.assertIn("Reason: pattern.", diagnostic)
        self.assertIn("Expected: lowercase_section_label.", diagnostic)
        self.assertNotIn("Unsafe Section", diagnostic)

    def test_schema_diagnostics_have_safe_field_specific_reason_classes(self) -> None:
        task = self._successful_tool("create_task", {
            "project_root": str(self.project), "objective": "Exercise safe schema diagnostics.",
            "user_request_original": "Exercise safe schema diagnostics.", "user_language": "en",
            "requirements": ["Use bounded diagnostics."], "constraints": ["Do not echo inputs."],
            "acceptance_criteria": ["Every requested reason is actionable."], "verification_plan": ["Call the public boundary."],
        })
        task_ref = task["handles"]["task_ref"]
        before = self._successful_tool("inspect_task", {"task_ref": task_ref})["timeline"]
        create_arguments = {
            "project_root": str(self.project), "objective": "Valid diagnostic baseline.",
            "user_request_original": "Valid diagnostic baseline.", "user_language": "en",
            "requirements": ["Use bounded diagnostics."], "constraints": ["No input echo."],
            "acceptance_criteria": ["Diagnose the exact public field."], "verification_plan": ["Call the boundary."],
        }
        cases = (
            ("create_task", create_arguments | {"objective": 7}, "type", "string", "objective"),
            ("set_governance_mode", {"task_ref": task_ref, "mode": "unbounded"}, "enum", "permitted_value", "mode"),
            ("create_task", create_arguments | {"user_language": "bad!tag"}, "pattern", "permitted_value", "user_language"),
            ("create_task", create_arguments | {"objective": ""}, "length", "bounded_length", "objective"),
            ("record_initiative", {"task_ref": task_ref, "goal": "Detect duplicates.", "linked_task_refs": [task_ref, task_ref]}, "unique_items", "unique_items", "linked_task_refs"),
            ("submit_report", {"delegation_ref": "d_000000000000", "mode": "begin", "report_type": "result", "content": {}}, "conditional_shape", "permitted_input_shape", "content"),
            ("submit_report", {"delegation_ref": "d_000000000000", "mode": "single", "report_type": "unsafe", "status": "completed", "content": {}}, "enum", "progress|result|synthesis|plan", "report_type"),
            ("read_reports", {"report_refs": ["r_000000000001", "r_000000000002", "r_000000000003", "r_000000000004", "unsafe"]}, "pattern", "r_[0-9a-f]{12}", "report_refs"),
        )
        for tool_name, arguments, reason, expected, field in cases:
            rejected = self._rejected_tool(tool_name, arguments)
            text = rejected["content"][0]["text"]
            self.assertIn(f"Field: {field}.", text)
            self.assertIn(f"Reason: {reason}.", text)
            self.assertIn(f"Expected: {expected}.", text)
            self.assertNotIn("advertised_input_schema", text)
            self.assertNotIn("Traceback", text)
            self.assertNotIn(str(self.project), text)
        after = self._successful_tool("inspect_task", {"task_ref": task_ref})["timeline"]
        self.assertEqual(after, before)

    def test_report_continuation_and_reader_mode_are_unambiguous(self) -> None:
        task = self._successful_tool("create_task", {
            "project_root": str(self.project), "objective": "Exercise report continuations.",
            "user_request_original": "Exercise report continuations.", "user_language": "en",
            "requirements": ["Return exact finalization inputs."], "constraints": ["Keep reads classified from the consumer."],
            "acceptance_criteria": ["Append result supplies finalize fields."], "verification_plan": ["Append then finalize a report."],
        })
        delegated = self._successful_tool("create_delegation", {
            "task_ref": task["handles"]["task_ref"], "objective": "Write a result report.",
            "role": "writer", "profile_name": "technical_writer", "scope": "One report.",
            "instructions": "Write a two-chunk result report.", "model": "gpt-5.6-luna", "reasoning_effort": "high",
        })
        started = self._successful_tool("submit_report", {"delegation_ref": delegated["handles"]["delegation_ref"], "mode": "begin", "report_type": "result"})
        appended = self._successful_tool("submit_report", {
            "delegation_ref": delegated["handles"]["delegation_ref"], "mode": "append", "report_ref": started["handles"]["report_ref"],
            "chunk_index": started["handles"]["chunk_index"], "section": "body", "content": {"evidence": True},
        })
        self.assertEqual(appended["handles"]["expected_chunk_count"], appended["expected_chunk_count"])
        self.assertEqual(appended["handles"]["expected_content_digest"], appended["expected_content_digest"])
        finalized = self._successful_tool("submit_report", {
            "delegation_ref": delegated["handles"]["delegation_ref"], "mode": "finalize", "report_ref": started["handles"]["report_ref"],
            "status": "completed", "expected_chunk_count": appended["handles"]["expected_chunk_count"],
            "expected_content_digest": appended["handles"]["expected_content_digest"],
        })
        consumer = self._successful_tool("create_delegation", {
            "task_ref": task["handles"]["task_ref"], "objective": "Consume the finalized result.",
            "role": "qa", "profile_name": "qa_engineer", "scope": "Read one declared report.",
            "instructions": "Read the declared report only.", "input_report_refs": [finalized["handles"]["report_ref"]],
            "model": "gpt-5.6-luna", "reasoning_effort": "high",
        })
        read = self._successful_tool("read_reports", {"report_refs": [finalized["handles"]["report_ref"]], "consumer_delegation_ref": consumer["handles"]["delegation_ref"]})
        self.assertEqual(read["consumption_receipts"][0]["reader_kind"], "worker")
        coordinator = self._successful_tool("read_reports", {"report_refs": [finalized["handles"]["report_ref"]]})
        self.assertGreater(coordinator["returned_content_bytes"], 0)
        self.assertEqual(coordinator["consumption_receipts"][0]["reader_kind"], "coordinator")
        reused = self._successful_tool("read_reports", {"report_refs": [finalized["handles"]["report_ref"]]})
        self.assertEqual(reused["consumption_receipts"][0]["receipt_id"], coordinator["consumption_receipts"][0]["receipt_id"])
        self.assertTrue(reused["consumption_receipts"][0]["reused"])

    def test_storage_unavailable_preserves_pending_decision_and_mutates_nothing(self) -> None:
        store = V12Store(self.project)
        task_id, report_id = self._plan(store, "Unavailable decision")
        report = store._read(lambda connection: store._report(connection, report_id, task_id=task_id))
        before = store.inspect_task(task_id=task_id, after_sequence=0)["decisions"]
        pending = {
            "task_ref": task_ref(task_id), "subject_type": "plan", "subject_ref": record_ref(report_id),
            "subject_digest": report["content_digest"], "decision_type": "approve", "prompt": "Approve?",
            "response_original": "Approve.", "user_language": "en",
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
        self.assertIn("oneOf", decision_schema)
        self.assertIn("prompt", decision_schema["properties"])
        self.assertNotIn("prompt_en", decision_schema["properties"])
        self.assertNotIn("response_en", decision_schema["properties"])
        self.assertNotIn("subject_digest", decision_schema["required"])
        self.assertNotIn("report_ref", decision_schema["properties"])
        self.assertNotIn("byte_budget", read_schema["properties"])
        self.assertEqual(read_schema["properties"]["max_bytes"]["type"], "integer")
        self.assertNotIn("reason", governance_schema["properties"])
        with self.assertRaises(_SchemaError):
            _validate_schema(read_schema, {"report_refs": ["r_000000000000"], "max_bytes": "0"})
        with self.assertRaises(_SchemaError):
            _validate_schema(read_schema, {"report_refs": ["r_000000000000"], "byte_budget": 0})

    def test_decision_preserves_original_unicode_without_translated_duplicate(self) -> None:
        task = self._successful_tool("create_task", {
            "project_root": str(self.project),
            "objective": "Verify exact decision response preservation.",
            "user_request_original": "Verify exact decision response preservation.",
            "user_language": "en",
            "requirements": ["Keep user decisions exact."],
            "constraints": ["Do not translate responses."],
            "acceptance_criteria": ["Russian and Japanese responses survive unchanged."],
            "verification_plan": ["Inspect the durable decision rows."],
        })
        task_ref = task["handles"]["task_ref"]
        russian = "Одобряю план без изменений."
        japanese = "次の安全な手順へ進めてください。"
        for response, language, key in ((russian, "ru", "unicode-ru"), (japanese, "ja", "unicode-ja")):
            result = self._successful_tool("record_user_decision", {
                "task_ref": task_ref,
                "subject_type": "task",
                "subject_ref": task_ref,
                "decision_type": "steer",
                "prompt": "Choose the next safe action.",
                "response_original": response,
                "user_language": language,
                "idempotency_key": key,
            })
            self.assertNotIn("response_en", result["decision"])
            self.assertNotIn("response_en_excerpt", result["decision"])
        store = V12Store.for_task_ref(task_ref)[0]
        rows = store._read(lambda connection: connection.execute(
            "SELECT prompt_en,response_original,response_en,user_language FROM user_decisions WHERE task_id=? ORDER BY created_sequence",
            (V12Store.for_task_ref(task_ref)[1],),
        ).fetchall())
        self.assertEqual([row["response_original"] for row in rows], [russian, japanese])
        self.assertEqual([row["user_language"] for row in rows], ["ru", "ja"])
        self.assertEqual([row["response_en"] for row in rows], ["", ""])
        self.assertEqual([row["prompt_en"] for row in rows], ["Choose the next safe action."] * 2)

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
                prompt="Approve this plan?",
                response_original="I approve this plan.",
                user_language="en",
                approval_handle=handle,
                approval_view_content_digest=view["content_digest"],
                approval_view_source_sequence=view["source_sequence"],
                idempotency_key="digest-mismatch",
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
            idempotency_key="approval-freshness-initiative",
        )
        approved, _ = store.record_user_decision(
            task_id=task_id,
            subject_type="plan",
            subject_id=report_id,
            subject_digest=report["content_digest"],
            decision_type="approve",
            prompt="Approve this plan?",
            response_original="I approve this plan.",
            user_language="en",
            approval_handle=handle,
            approval_view_content_digest=view["content_digest"],
            approval_view_source_sequence=view["source_sequence"],
            supersedes_decision_id=None,
            idempotency_key="approval-freshness-decision",
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
                prompt="Approve this plan?",
                response_original="I approve this plan.",
                user_language="en",
                approval_handle=handle2,
                approval_view_content_digest=view2["content_digest"],
                approval_view_source_sequence=view2["source_sequence"],
            )

    def test_public_stdio_hardening_keeps_single_sections_and_callable_handles(self) -> None:
        """Exercise the published contract through the JSON-RPC stdio boundary."""
        task = self._successful_tool("create_task", {
            "project_root": str(self.project), "objective": "Harden one public report.",
            "user_request_original": "Harden one public report.", "user_language": "en",
            "requirements": ["Keep the five report modes."], "constraints": ["Reject mixed task subjects."],
            "acceptance_criteria": ["A bounded single report is callable."], "verification_plan": ["Use the public stdio tool."],
        })
        task_ref_value = task["handles"]["task_ref"]
        self.assertEqual(task["handles"]["idempotency_key"], task["idempotency_key"])
        inspection = self._successful_tool("inspect_task", {"task_ref": task_ref_value})
        self.assertEqual(inspection["handles"]["after_sequence"], inspection["next_sequence"])
        self.assertNotIn("next_sequence", inspection["handles"])
        delegation = self._successful_tool("create_delegation", {
            "task_ref": task_ref_value, "objective": "Publish one report.", "role": "writer",
            "profile_name": "technical_writer", "scope": "One bounded report.", "instructions": "Submit evidence.",
            "model": "gpt-5.6-luna", "reasoning_effort": "high",
        })
        single = self._successful_tool("submit_report", {
            "delegation_ref": delegation["handles"]["delegation_ref"], "mode": "single", "report_type": "result",
            "status": "completed", "content": {"schema": "cortex/report/result/v1", "summary": "Bounded result."},
        })
        self.assertEqual(single["report"]["assembly_state"], "finalized")
        started = self._successful_tool("submit_report", {
            "delegation_ref": delegation["handles"]["delegation_ref"], "mode": "begin", "report_type": "result",
        })
        self.assertEqual(started["handles"]["chunk_index"], 0)
        self.assertNotIn("next_chunk_index", started["handles"])
        rejected_section = self._rejected_tool("submit_report", {
            "delegation_ref": delegation["handles"]["delegation_ref"], "mode": "append",
            "report_ref": started["handles"]["report_ref"], "chunk_index": started["handles"]["chunk_index"],
            "section": "Bad Section", "content": {"summary": "invalid"}, "idempotency_key": "bad-section",
        })
        self.assertIn("section", rejected_section["content"][0]["text"])
        other = self._successful_tool("create_task", {
            "project_root": str(self.project), "objective": "Other task.", "user_request_original": "Other task.", "user_language": "en",
            "requirements": ["Keep tasks isolated."], "constraints": ["No crossed subjects."],
            "acceptance_criteria": ["Crossed decision fails."], "verification_plan": ["Call decision API."],
        })
        crossed = self._rejected_tool("record_user_decision", {
            "task_ref": task_ref_value, "subject_type": "task", "subject_ref": other["handles"]["task_ref"],
            "decision_type": "steer", "prompt": "Next step?", "response_original": "Continue.",
            "user_language": "en", "idempotency_key": "crossed-task-subject",
        })
        self.assertIn("subject_ref", crossed["content"][0]["text"])

    def test_initialize_counteroffers_the_supported_protocol_version(self) -> None:
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2099-01-01", "capabilities": {}, "clientInfo": {"name": "counteroffer-test", "version": "1"},
        }}
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")), mock.patch("sys.stdout", output):
            serve_stdio(public_tools=PUBLIC_TOOLS, server_version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS)
        response = json.loads(output.getvalue())
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")


if __name__ == "__main__":
    unittest.main()
