"""Token-budget and security regressions for the v11 native dispatch boundary."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cortex
from cortex_runtime import mcp_api
from cortex_runtime.briefings import host_bootstrap_repair_message, host_spawn_bootstrap
from cortex_runtime.dispatch_briefing import _dispatch_briefing_failure


TASK_REF = "task-" + "a" * 12
ASSIGNMENT_REF = "assignment-v1-" + "b" * 64
DISPATCH_REF = "dispatch-" + "d" * 24


def representative_package() -> dict[str, object]:
    request = "Create the requested file with exact content."
    return {
        "task_id": "task-size",
        "task_ref": TASK_REF,
        "attempt_id": "implementation-01",
        "dispatch_ref": DISPATCH_REF,
        "gate": "implementation",
        "profile": "general",
        "facade_managed": True,
        "user_request": request,
        "task_user_request": request,
        "objective": "Create compact.txt with exact content.",
        "selection_reason": "canonical phase owner",
        "strategy": "default",
        "task_requirements": ["Create compact.txt."],
        "task_scope": ["compact.txt"],
        "allowed_paths": ["compact.txt"],
        "task_acceptance_criteria": ["compact.txt has exact content."],
        "acceptance_criteria": ["compact.txt has exact content."],
        "task_verification": ["Verify exact UTF-8 bytes and one final newline."],
        "verification": ["Verify exact UTF-8 bytes and one final newline."],
        "context_files": [],
        "knowledge_index_files": [],
        "context_result_refs": [],
        "predecessor_results": [],
        "resolved_user_decisions": [],
        "mode": "ordinary",
        # These host-private descriptors may still exist in durable state,
        # but a successful complete briefing must not ask the worker to read
        # either artifact again.
        "user_intent": {
            "projection": request,
            "artifact_ref": "artifact-intent",
            "artifact_path": "/private/intent.txt",
            "digest_sha256": "c" * 64,
            "byte_size": len(request.encode("utf-8")),
        },
        "task_contract": {
            "schema": "cortex/task-contract-ref/v1",
            "artifact_ref": "artifact-contract",
            "artifact_path": "/private/task-contract.json",
            "digest_sha256": "e" * 64,
            "byte_size": 900,
            "read_required": True,
        },
    }


class V11BootstrapCompactionTests(unittest.TestCase):
    def bootstrap(self) -> str:
        return host_spawn_bootstrap(
            profile="general",
            briefing_path=Path("/private/briefing.md"),
            briefing_digest="f" * 64,
            dispatch_ref=DISPATCH_REF,
            task_id="task-internal",
            attempt_id="attempt-internal",
            project_root=Path("/private/project"),
            task_ref=TASK_REF,
            assignment_ref=ASSIGNMENT_REF,
        )

    def test_native_bootstrap_and_repair_stay_below_one_kib(self) -> None:
        bootstrap = self.bootstrap()
        repair = host_bootstrap_repair_message(
            task_ref=TASK_REF,
            assignment_ref=ASSIGNMENT_REF,
        )
        self.assertLessEqual(len(bootstrap.encode("utf-8")), 1024)
        self.assertLessEqual(len(repair.encode("utf-8")), 1024)
        self.assertEqual(len(bootstrap.encode("utf-8")), 706)
        self.assertEqual(len(repair.encode("utf-8")), 638)

    def test_bootstrap_contains_no_dispatch_or_private_fallback_metadata(self) -> None:
        bootstrap = self.bootstrap()
        for forbidden in (
            DISPATCH_REF,
            "/private/briefing.md",
            "/private/project",
            "briefing_digest",
            "Fallback path:",
            "repair_capsule",
            "attempt_result_ref",
        ):
            self.assertNotIn(forbidden, bootstrap)
        self.assertIn("read_dispatch_briefing", bootstrap)
        self.assertIn("complete=true", bootstrap)

    def test_repair_positive_branch_cannot_stop_at_gate_acknowledgement(self) -> None:
        repair = host_bootstrap_repair_message(
            task_ref=TASK_REF,
            assignment_ref=ASSIGNMENT_REF,
        )
        self.assertIn("no gate-passed acknowledgement", repair)
        self.assertLess(repair.index("no gate-passed"), repair.index("read_dispatch_briefing"))
        self.assertLess(repair.index("read_dispatch_briefing"), repair.index("complete_attempt"))
        self.assertTrue(repair.endswith("final exactly ATTEMPT_COMPLETED."))

    def test_complete_assignment_delta_has_no_duplicate_intent_or_contract_reads(self) -> None:
        package = representative_package()
        briefing = cortex.host_spawn_prompt("general", package)
        request = str(package["user_request"])
        self.assertLessEqual(len(briefing.encode("utf-8")), 12 * 1024)
        # The exact prompt size changes legitimately when static protocol
        # wording changes. Keep a narrow semantic budget assertion instead of
        # pinning an incidental byte count.
        self.assertGreater(len(briefing.encode("utf-8")), 8 * 1024)
        self.assertIn("top-level error/recovery", briefing)
        self.assertIn("allowed_ops", briefing)
        self.assertIn("never inspect Cortex source", briefing)
        self.assertLessEqual(briefing.count(request), 1)
        for forbidden in (
            '"user_intent"',
            '"task_contract"',
            '"gate_acceptance_criteria"',
            '"gate_verification"',
            '"assignment_context"',
            '"user_request":',
            "/private/intent.txt",
            "/private/task-contract.json",
            DISPATCH_REF,
        ):
            self.assertNotIn(forbidden, briefing)
        self.assertIn('"mission": "Create compact.txt with exact content."', briefing)
        self.assertIn('"allowed_paths":', briefing)
        self.assertIn('"acceptance_criteria":', briefing)
        self.assertIn('"verification":', briefing)

    def test_dispatch_ref_is_structured_and_fork_turns_remains_none(self) -> None:
        response = mcp_api.v11_response(
            {
                "ok": True,
                "state": "ready_to_spawn",
                "spawn_requests": [{
                    "dispatch_ref": DISPATCH_REF,
                    "task_name": "general_compaction",
                    "message": self.bootstrap(),
                    "reasoning_effort": "xhigh",
                    "fork_turns": "none",
                    "bootstrap_repair_message": host_bootstrap_repair_message(
                        task_ref=TASK_REF,
                        assignment_ref=ASSIGNMENT_REF,
                    ),
                }],
            },
            TASK_REF,
            native_arguments=lambda request: {
                key: request[key]
                for key in ("task_name", "message", "reasoning_effort", "fork_turns")
            },
            public_schema="unused",
            coordinator_lock="unused",
        )
        dispatch = response["dispatches"][0]
        self.assertEqual(dispatch["dispatch_ref"], DISPATCH_REF)
        self.assertEqual(dispatch["arguments"]["fork_turns"], "none")
        self.assertNotIn(DISPATCH_REF, dispatch["arguments"]["message"])

    def test_private_host_path_is_returned_only_for_actual_file_unavailability(self) -> None:
        private_path = Path("/private/briefing.md")
        missing = _dispatch_briefing_failure(
            FileNotFoundError("unavailable"), recovery_path=private_path,
        )
        self.assertEqual(missing["recovery"], {
            "kind": "read_exact_host_path_once",
            "path": str(private_path),
            "max_reads": 1,
        })
        self.assertNotIn(
            "recovery",
            _dispatch_briefing_failure(OSError("permission denied"), recovery_path=private_path),
        )
        self.assertNotIn(
            "recovery",
            _dispatch_briefing_failure(ValueError("invalid cursor"), recovery_path=private_path),
        )

    def test_public_event_success_has_no_unused_internal_event_ref(self) -> None:
        projected = mcp_api.project_public_response(
            "record_attempt_event",
            {"ok": True, "event_ref": "attempt-event-internal", "event_type": "progress"},
            arguments={"task_ref": TASK_REF, "assignment_ref": ASSIGNMENT_REF},
        )
        self.assertEqual(projected, {"schema": "cortex/worker-event/v11", "ok": True})
        self.assertNotIn("attempt-event-internal", json.dumps(projected))


if __name__ == "__main__":
    unittest.main()
