"""Focused contracts for the pure v11 submission/repair validation layer."""
from __future__ import annotations

import copy
import hashlib
import hmac
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime import v11_submission as v11


def plan() -> dict[str, object]:
    return {
        "overview": "Implement the narrow, verifiable change.",
        "work_packages": [{
            "id": "core", "title": "Core", "objective": "Make the change.",
            "allowed_paths": ["plugins/cortex"],
            "microtasks": [{
                "id": "change", "title": "Change", "objective": "Implement it.", "profile": "backend_dev",
                "allowed_paths": ["plugins/cortex"], "acceptance_criteria": ["Contract passes."],
                "verification": ["Run focused tests."],
            }],
        }],
    }


def outcome() -> dict[str, object]:
    return {"status": "completed", "summary": "Implemented the bounded change.", "findings": [{"summary": "Verified."}]}


class V11SubmissionValidationTests(unittest.TestCase):
    _seal_key = b"v11-submission-test-signing-key-0123456789"
    _escrow_digest = "a" * 64
    _handle_id = "A" * 22

    def submission(self, **extra: object) -> dict[str, object]:
        return {
            "task_ref": "task-000000000001",
            "assignment_ref": "assignment-v1-" + "b" * 64,
            **extra,
        }

    def repair(self, escrow: dict[str, object], patches: list[dict[str, object]], *, digest: str | None = None) -> dict[str, object]:
        return self.submission(
            repair_capsule=v11.sign_repair_handle(self._handle_id, self._escrow_digest, self._seal_key),
            base_payload_digest=digest or str(escrow["base_payload_digest"]),
            patches=patches,
        )

    def test_closed_schema_aggregates_multiple_errors_and_projects_cards(self) -> None:
        bad = self.submission(task_ref="bad ref", plan={"overview": "", "unexpected": True})
        with self.assertRaises(v11.ValidationFailure) as raised:
            v11.validate_submission(bad)
        diagnostics = raised.exception.diagnostics
        self.assertEqual(
            [item["json_pointer"] for item in diagnostics],
            ["/task_ref", "/plan/work_packages", "/plan/unexpected", "/plan/overview"],
        )
        self.assertTrue(all("field_schema" in item for item in diagnostics))
        self.assertEqual(diagnostics[0]["field_schema"]["pattern"], v11.TASK_REF_PATTERN)

    def test_valid_full_forms_are_deep_copied_and_preserve_all_valid_fields(self) -> None:
        source = self.submission(plan=plan())
        checked = v11.validate_submission(source)
        self.assertEqual(checked["mode"], "full")
        self.assertEqual(checked["kind"], "plan")
        self.assertEqual(checked["plan"]["work_packages"][0]["microtasks"][0]["profile"], "backend_dev")
        checked["plan"]["overview"] = "changed"
        self.assertNotEqual(source["plan"]["overview"], "changed")
        self.assertEqual(v11.validate_submission(self.submission(outcome=outcome()))["kind"], "outcome")

    def test_repair_rejects_wrong_or_stale_digest_without_mutating_capsule(self) -> None:
        original = self.submission(plan=plan())
        escrow = v11.create_rejected_draft_escrow(original, [{"path": "$.plan.overview", "message": "needs detail"}])
        before = copy.deepcopy(escrow)
        for digest in ("sha256:" + "0" * 64, v11.canonical_digest({"overview": "other"})):
            with self.subTest(digest=digest), self.assertRaisesRegex(ValueError, "digest"):
                v11.apply_repair_escrow(escrow, self.repair(escrow, [{"op": "replace", "path": "/overview", "value": "More detail."}], digest=digest))
        self.assertEqual(escrow, before)

    def test_repair_rejects_out_of_scope_and_identity_paths_without_mutation(self) -> None:
        original = self.submission(plan=plan())
        escrow = v11.create_rejected_draft_escrow(original, [{"path": "$.plan.overview", "message": "needs detail"}])
        before = copy.deepcopy(escrow)
        repair = self.repair(escrow, [{"op": "replace", "path": "/work_packages/0/title", "value": "Forged"}])
        with self.assertRaisesRegex(ValueError, "outside"):
            v11.apply_repair_escrow(escrow, repair)
        identity_path = self.repair(escrow, [{"op": "replace", "path": "/task_ref", "value": "task-000000000002"}])
        with self.assertRaisesRegex(ValueError, "outside"):
            v11.apply_repair_escrow(escrow, identity_path)
        wrong_identity = {**repair, "assignment_ref": "assignment-v1-" + "c" * 64}
        with self.assertRaisesRegex(ValueError, "identity"):
            v11.apply_repair_escrow(escrow, wrong_identity)
        self.assertEqual(escrow, before)

    def test_same_repair_is_idempotent_and_postcheck_keeps_scope(self) -> None:
        original = self.submission(outcome=outcome())
        escrow = v11.create_rejected_draft_escrow(original, [{"path": "$.outcome.summary", "message": "be specific"}])
        repair = self.repair(escrow, [{"op": "replace", "path": "/summary", "value": "Implemented and ran focused validation."}])
        first = v11.apply_repair_escrow(escrow, repair)
        second = v11.apply_repair_escrow(escrow, repair)
        self.assertEqual(first, second)
        self.assertEqual(first["outcome"]["summary"], "Implemented and ran focused validation.")
        self.assertEqual(v11.changed_paths(escrow["payload"], first["outcome"]), ["/summary"])

    def test_repair_requires_patch_branch_and_revalidates_reconstructed_payload(self) -> None:
        original = self.submission(outcome=outcome())
        escrow = v11.create_rejected_draft_escrow(original, [{"path": "$.outcome.summary", "message": "be specific"}])
        full = self.submission(outcome=outcome())
        with self.assertRaisesRegex(ValueError, "digest-bound"):
            v11.apply_repair_escrow(escrow, full)
        invalid = self.repair(escrow, [{"op": "replace", "path": "/summary", "value": ""}])
        with self.assertRaises(v11.ValidationFailure) as raised:
            v11.apply_repair_escrow(escrow, invalid)
        self.assertEqual(raised.exception.diagnostics[0]["json_pointer"], "/outcome/summary")

    def test_rejected_invalid_plan_can_be_repaired_without_persisting_or_mutating_source(self) -> None:
        rejected = self.submission(plan={"overview": ""})
        with self.assertRaises(v11.ValidationFailure) as raised:
            v11.validate_submission(rejected)
        before = copy.deepcopy(rejected)
        escrow = v11.create_rejected_draft_escrow(rejected, raised.exception.diagnostics)
        repair = self.repair(escrow, [
            {"op": "replace", "path": "/overview", "value": "Implement the bounded change."},
            {"op": "add", "path": "/work_packages", "value": plan()["work_packages"]},
        ])
        repaired = v11.apply_repair_escrow(escrow, repair)
        self.assertEqual(repaired["kind"], "plan")
        self.assertEqual(rejected, before)

    def test_unknown_literal_key_pointer_is_rfc6901_and_executes_end_to_end(self) -> None:
        literal_key = "bad[0]/~key"
        rejected = self.submission(outcome={**outcome(), literal_key: True})
        with self.assertRaises(v11.ValidationFailure) as raised:
            v11.validate_submission(rejected)
        diagnostic = next(
            item for item in raised.exception.diagnostics
            if item["code"] == "validation_unknown"
        )
        self.assertEqual(
            diagnostic["json_pointer"],
            "/outcome/bad[0]~1~0key",
        )
        escrow = v11.create_rejected_draft_escrow(
            rejected,
            raised.exception.diagnostics,
        )
        repair_diagnostic = next(
            item for item in escrow["diagnostics"]
            if item["code"] == "validation_unknown"
        )
        self.assertEqual(repair_diagnostic["repair_pointer"], "/bad[0]~1~0key")
        repaired = v11.apply_repair_escrow(
            escrow,
            self.repair(escrow, [{
                "op": "remove",
                "path": repair_diagnostic["repair_pointer"],
            }]),
        )
        self.assertNotIn(literal_key, repaired["outcome"])
        self.assertEqual(repaired["outcome"], outcome())

    def test_patch_paths_reject_invalid_rfc6901_escape_sequences(self) -> None:
        original = self.submission(outcome=outcome())
        escrow = v11.create_rejected_draft_escrow(
            original,
            [{"path": "$.outcome.summary", "message": "be specific"}],
        )
        for path in ("/summary~", "/summary~2"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "RFC6901"):
                v11.apply_repair_escrow(
                    escrow,
                    self.repair(escrow, [{
                        "op": "replace", "path": path, "value": "Specific.",
                    }]),
                )

    def test_opaque_handle_is_fixed_length_tamper_evident_and_contains_no_assignment(self) -> None:
        token = v11.sign_repair_handle(self._handle_id, self._escrow_digest, self._seal_key)
        self.assertEqual(len(token), v11.REPAIR_HANDLE_LENGTH)
        self.assertLess(len(token), 94)
        self.assertNotIn("assignment-v1-" + "b" * 64, token)
        self.assertEqual(v11.verify_repair_handle(token, self._escrow_digest, self._seal_key), v11.repair_handle_digest(self._handle_id))
        with self.assertRaisesRegex(ValueError, "integrity"):
            v11.verify_repair_handle(token[:-1] + ("0" if token[-1] != "0" else "1"), self._escrow_digest, self._seal_key)

    def test_repair_handle_rejects_signature_without_its_domain_separator(self) -> None:
        undomained = hmac.new(
            self._seal_key,
            self._handle_id.encode("ascii") + b"\0" + self._escrow_digest.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()[:32]
        forged = f"v11rh1.{self._handle_id}.{undomained}"
        with self.assertRaisesRegex(ValueError, "integrity"):
            v11.verify_repair_handle(forged, self._escrow_digest, self._seal_key)


if __name__ == "__main__":
    unittest.main()
