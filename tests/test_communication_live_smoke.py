"""Small end-to-end smoke for delegation, result persistence, and natural UX."""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex as control
from cortex_runtime.communication import render_lifecycle, select_profile


_BOOTSTRAP_AUTHORITY = re.compile(
    r'read_dispatch_briefing\(\{"task_ref":"([^"]+)","assignment_ref":"([^"]+)"\}\)'
)


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
                "communication_profile": "natural",
                "plan_approval": "auto",
            },
            "waves": [{"phase": "discover", "workers": [{}]}],
        })
        self.assertTrue(started.get("ok"), started)
        dispatch = started["dispatches"][0]
        message = str(dispatch["arguments"]["message"])
        assignment_match = _BOOTSTRAP_AUTHORITY.search(message)
        self.assertIsNotNone(assignment_match)
        assert assignment_match is not None
        assignment_authority = {
            "task_ref": assignment_match.group(1),
            "assignment_ref": assignment_match.group(2),
        }
        self.assertEqual(assignment_authority["task_ref"], started["task_ref"])

        briefing_read = control.read_dispatch_briefing(assignment_authority)
        self.assertTrue(briefing_read.get("ok"), briefing_read)
        recorded = control.complete_worker_attempt({
            **assignment_authority,
            "outcome": {
                "status": "completed",
                "summary": "Smoke check completed and persisted.",
                "findings": [],
                "decisions_needed": [],
                "unresolved": [],
                "claims": [],
            },
        })
        self.assertTrue(recorded.get("ok"), recorded)
        self.assertEqual(recorded, {
            "schema": "cortex/worker-completion/v11",
            "ok": True,
            "terminal": True,
        })

        rendered = render_lifecycle(
            "completed",
            config={"communication_profile": "natural", "user_language": "ru"},
        )
        self.assertEqual(select_profile({"communication_profile": "natural"}), "natural")
        self.assertEqual(rendered["profile"], "natural")
        self.assertTrue(rendered["message"].startswith("Задача завершена"))
        self.assertTrue(rendered["quality"]["ok"], rendered)


if __name__ == "__main__":
    unittest.main()
