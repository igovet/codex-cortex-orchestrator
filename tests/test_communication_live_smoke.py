"""Small end-to-end smoke for delegation, result persistence, and natural UX."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex as control
from cortex_runtime.communication import render_lifecycle, select_profile


class CommunicationLiveSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cortex-live-smoke-")
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.host_store = self.root / "host-private"
        self.host_store.mkdir(mode=0o700)
        self.previous_store = os.environ.get(control.HOST_CONTROL_STORE_ENV)
        os.environ[control.HOST_CONTROL_STORE_ENV] = str(self.host_store)

    def tearDown(self) -> None:
        if self.previous_store is None:
            os.environ.pop(control.HOST_CONTROL_STORE_ENV, None)
        else:
            os.environ[control.HOST_CONTROL_STORE_ENV] = self.previous_store
        self.temp.cleanup()

    def test_delegation_result_and_natural_profile_live_path(self) -> None:
        activation = control.activate_orchestration({
            "project_root": str(self.project),
            "user_command": "/cortex",
            "principal": "live-smoke",
            "thread_id": "live-smoke",
        })
        self.assertTrue(activation.get("active"), activation)
        started = control.start_orchestration({
            "project_root": str(self.project),
            "task": {
                "user_request": "Run a bounded orchestration smoke check.",
                "acceptance_criteria": ["The delegated smoke check is recorded successfully."],
                "verification": ["Run the focused smoke check and record its result."],
                "scope": ["the temporary project"],
                "allowed_paths": ["."],
                "complexity": "C1",
                "user_language": "ru",
                "communication_profile": "neutral",
                "plan_approval": "auto",
            },
            "waves": [{"workers": [{"phase": "discover"}]}],
        })
        self.assertTrue(started.get("ok"), started)
        dispatch = started["dispatches"][0]
        briefing = Path(dispatch["briefing_path"]).read_text(encoding="utf-8")
        assignment_match = re.search(r"```json\n(.*?)\n```", briefing, re.S)
        self.assertIsNotNone(assignment_match)
        assignment = json.loads(assignment_match.group(1))
        required_lists = (
            "scope", "allowed_paths", "task_acceptance_criteria",
            "gate_acceptance_criteria", "task_verification", "gate_verification",
        )
        for field in required_lists:
            with self.subTest(field=field):
                self.assertIsInstance(assignment.get(field), list)
                self.assertTrue(assignment[field])

        ledger = control.ledger_root_path({"project_root": str(self.project)})
        task_dir = next((ledger / "tasks").iterdir())
        state = control.load_task_state_for_artifact(task_dir)
        attempt = state["attempts"][0]
        task = control.load_task_definition(task_dir, state)
        briefing_read = control.read_dispatch_briefing({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "dispatch_ref": attempt["dispatch_ref"],
            "briefing_digest": attempt["briefing_digest"],
        })
        self.assertTrue(briefing_read.get("ok"), briefing_read)
        recorded = control.complete_worker_attempt({
            "project_root": str(self.project),
            "task_id": state["task_id"],
            "attempt_id": attempt["attempt_id"],
            "profile": attempt["profile"],
            "status": "completed",
            "summary": "Smoke check completed and persisted.",
            "findings": [],
            "decisions_needed": [],
            "unresolved": [],
        })
        self.assertTrue(recorded.get("ok"), recorded)
        self.assertEqual(recorded.get("outcome"), "attempt_completed")
        self.assertNotEqual(recorded.get("code"), "attempt_validation_failed")

        rendered = render_lifecycle(
            "completed",
            config={"communication_profile": "neutral", "user_language": "ru"},
        )
        self.assertEqual(select_profile({"communication_profile": "neutral"}), "natural")
        self.assertEqual(rendered["profile"], "natural")
        self.assertTrue(rendered["message"].startswith("Задача завершена"))
        self.assertTrue(rendered["quality"]["ok"], rendered)


if __name__ == "__main__":
    unittest.main()
