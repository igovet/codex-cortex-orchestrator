"""Focused coverage for compatibility aliases and approval-view freshness."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.mcp_api import _SchemaError, _validate_schema  # noqa: E402
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
            acceptance_criteria=["Legacy aliases remain safe."],
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

    def test_legacy_decision_shape_and_byte_budget_alias_are_supported(self) -> None:
        store = V12Store(self.project)
        task_id, report_id = self._plan(store)
        report = store._read(lambda connection: store._report(connection, report_id, task_id=task_id))
        legacy = {
            "task_ref": task_ref(task_id),
            "report_ref": record_ref(report_id),
            "report_content_digest": report["content_digest"],
            "decision": "request_revision",
            "user_response_original": "Please clarify the verification step.",
            "english_normalization": "Please clarify the verification step.",
        }
        schema = build_public_contracts()["record_user_decision"]["inputSchema"]
        _validate_schema(schema, legacy)
        result = record_user_decision(**legacy)
        self.assertEqual(result["decision"]["decision_type"], "request_revision")
        self.assertEqual(result["decision"]["subject_digest"], report["content_digest"])

        # A legacy approve without the newer binding fields is upgraded only
        # through a fresh server-verified ready view and handle.
        approved = record_user_decision(**(legacy | {
            "decision": "approve",
            "user_response_original": "I approve the clarified plan.",
            "english_normalization": "I approve the clarified plan.",
        }))
        self.assertEqual(approved["decision"]["decision_type"], "approve")

        read_schema = build_public_contracts()["read_reports"]["inputSchema"]
        byte_budget = read_schema["properties"]["byte_budget"]
        self.assertEqual(byte_budget["type"], "integer")
        self.assertEqual(byte_budget["minimum"], 0)
        self.assertIn("deprecated", byte_budget["description"].lower())
        _validate_schema(read_schema, {"report_refs": [record_ref(report_id)], "byte_budget": 0})

        read = read_reports(report_refs=[record_ref(report_id)], byte_budget=0)
        self.assertEqual(read["returned_content_bytes"], 0)
        with self.assertRaises(V12ServiceError) as error:
            read_reports(report_refs=[record_ref(report_id)], max_bytes=1, byte_budget=2)
        self.assertEqual(error.exception.code, "invalid_argument")

    def test_legacy_aliases_reject_ambiguous_mixed_shape(self) -> None:
        schema = build_public_contracts()["record_user_decision"]["inputSchema"]
        with self.assertRaises(_SchemaError):
            _validate_schema(schema, {
                "task_ref": "t_000000000000",
                "report_ref": "r_000000000000",
                "report_content_digest": "sha256:" + "0" * 64,
                "decision": "cancel",
                "user_response_original": "Cancel this plan.",
                "english_normalization": "Cancel this plan.",
                "decision_type": "cancel",
            })

    def test_read_budget_accepts_legacy_decimal_string_only_within_bound(self) -> None:
        store = V12Store(self.project)
        _task_id, report_id = self._plan(store, "Read budget compatibility")
        schema = build_public_contracts()["read_reports"]["inputSchema"]

        # The advertised schema accepts the legacy wire form, while rejecting
        # whitespace, signs, leading zeroes, fractions, and booleans before the
        # handler is invoked.
        _validate_schema(schema, {"report_refs": [record_ref(report_id)], "max_bytes": "65536"})
        for invalid in ("065536", " 10", "+10", "-1", "1.5", True, 65_537):
            with self.subTest(invalid=invalid), self.assertRaises(_SchemaError):
                _validate_schema(schema, {"report_refs": [record_ref(report_id)], "max_bytes": invalid})

        read = read_reports(report_refs=[record_ref(report_id)], max_bytes="0")
        self.assertEqual(read["returned_content_bytes"], 0)
        self.assertEqual(read_reports(report_refs=[record_ref(report_id)], max_bytes="65536")["has_more"], False)

        for invalid in ("065536", " 10", "+10", "-1", "1.5", True, 65_537):
            with self.subTest(service_invalid=invalid), self.assertRaises(V12ServiceError) as error:
                read_reports(report_refs=[record_ref(report_id)], max_bytes=invalid)
            self.assertEqual(error.exception.code, "invalid_argument")

        with self.assertRaises(V12ServiceError):
            read_reports(report_refs=[record_ref(report_id)], max_bytes="1", byte_budget=2)

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
