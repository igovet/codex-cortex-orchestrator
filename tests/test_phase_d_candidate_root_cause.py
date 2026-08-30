"""Source regressions for the Phase D candidate root-cause contracts."""
from __future__ import annotations

import sys
import re
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex_runtime.mcp_api import _handles, _project_public_views, _service_failure, _validate_schema  # noqa: E402
from cortex_runtime.domain_api import _family_result  # noqa: E402
from cortex_runtime.public_contracts import build_public_contracts  # noqa: E402
from cortex_runtime.semantic_registry import error_contract, exported_metadata, operation_specs, public_error_codes  # noqa: E402
from cortex_runtime.v12_service import V12ServiceError  # noqa: E402


class CandidateRootCauseTests(unittest.TestCase):
    def test_registry_owned_error_contract_preserves_semantic_classes(self) -> None:
        expected = {
            "command_conflict", "clarification_binding_stale",
            "cross_project_reference", "clarification_binding_mismatch",
            "storage_busy", "storage_unavailable",
            "clarification_binding_consumed", "outcome_item_not_found",
            "outcome_item_stale", "outcome_assignment_conflict",
        }
        self.assertTrue(expected.issubset(public_error_codes()))
        for code in expected:
            with self.subTest(code=code):
                projected = _service_failure(V12ServiceError("private text", code=code))
                self.assertEqual(projected["code"], code)
                self.assertEqual(projected["message"], error_contract(code).message)
                self.assertEqual(projected["action"], error_contract(code).action)
                self.assertEqual(projected["retryable"], error_contract(code).retryable)

    def test_every_declared_or_raised_public_code_has_one_canonical_spec(self) -> None:
        """The registry, not an MCP fallback, owns all intentional failures."""
        runtime = SCRIPTS / "cortex_runtime"
        raised: set[str] = set()
        for source in ("v12_store.py", "v12_service.py", "domain_kernel.py", "domain_api.py", "mcp_api.py"):
            raised.update(re.findall(r'code=["\']([a-z0-9_]+)["\']', (runtime / source).read_text(encoding="utf-8")))
        declared = {code for spec in operation_specs() for code in spec.safe_errors}
        codes = public_error_codes()
        self.assertTrue(raised.issubset(codes), sorted(raised - codes))
        self.assertTrue(declared.issubset(codes), sorted(declared - codes))
        metadata_codes = [item["code"] for item in exported_metadata()["errors"]]
        self.assertEqual(len(metadata_codes), len(set(metadata_codes)))
        for code in raised | declared:
            self.assertEqual(error_contract(code).code, code)

    def test_unknown_fault_is_the_only_generic_ledger_error(self) -> None:
        projected = _service_failure(V12ServiceError("private implementation detail", code="unregistered_fault"))
        self.assertEqual(projected["code"], "ledger_error")
        self.assertEqual(projected["message"], error_contract("ledger_error").message)
        self.assertNotIn("private", projected["message"].lower())

    def test_steering_supersession_is_a_closed_compact_relation(self) -> None:
        canonical_prior = "decision-" + "a" * 64 + "-" + "b" * 32
        result = _family_result(
            task_ref="t_" + "c" * 12,
            issued={
                "replayed": False,
                "binding": {"clarification_binding": "cb_" + "d" * 32, "decision_type": "steer"},
                "decision": {
                    "decision_id": "decision-" + "a" * 64 + "-" + "e" * 32,
                    "supersedes_decision_id": canonical_prior,
                    "decision_type": "steer",
                },
            },
            response_original="yes",
        )
        decision = result["decision"]
        self.assertNotIn("decision_id", decision)
        self.assertNotIn("task_id", decision)
        self.assertNotIn("subject_id", decision)
        self.assertNotIn("supersedes_decision_id", decision)
        self.assertEqual(decision["relations"], {"supersedes_decision_ref": "u_" + "b" * 12})
        projected = _project_public_views(result)
        projected["handles"] = _handles(projected)
        _validate_schema(
            build_public_contracts()["record_steering"]["outputSchema"],
            projected,
        )

    def test_family_and_plan_publication_schemas_exclude_canonical_ids(self) -> None:
        contracts = build_public_contracts()
        for name in (
            "open_clarification", "open_plan_review", "record_clarification", "record_plan_review", "open_steering", "record_steering",
        ):
            decision_properties = contracts[name]["runtimeOutputSchema"]["properties"]["decision"]["properties"]
            self.assertFalse({"decision_id", "task_id", "subject_id"} & set(decision_properties))
        publication = contracts["publish_plan"]["runtimeOutputSchema"]
        self.assertFalse(publication["additionalProperties"])
        self.assertFalse(publication["properties"]["report"]["additionalProperties"])
        self.assertFalse(publication["properties"]["handles"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
