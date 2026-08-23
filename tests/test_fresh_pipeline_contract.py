"""Focused checks for the single current orchestration pipeline contract."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cortex as control
from cortex_runtime import orchestration_engine


class FreshPipelineContractTests(unittest.TestCase):
    def test_current_contract_is_accepted_without_retired_dispatch(self) -> None:
        state = {"pipeline_contract_version": control.PIPELINE_CONTRACT_VERSION}
        self.assertEqual(
            orchestration_engine._pipeline_contract_version(state),
            control.PIPELINE_CONTRACT_VERSION,
        )
        self.assertFalse(hasattr(orchestration_engine, "_validate_wave_contract"))

    def test_missing_or_invalid_contract_is_not_inferred(self) -> None:
        for state in ({}, {"pipeline_contract_version": 0}, {"pipeline_contract_version": "unsupported"}):
            with self.assertRaisesRegex(ValueError, "unsupported pipeline_contract_version"):
                orchestration_engine._pipeline_contract_version(state)

    def test_retired_pause_and_replacement_shapes_are_not_runtime_protocols(self) -> None:
        self.assertFalse(hasattr(orchestration_engine, "_normalize_no_progress_pause_state"))
        source = Path(orchestration_engine.__file__).read_text(encoding="utf-8")
        self.assertNotIn("replacement_waves", source)
        self.assertNotIn("planner_recovery_pending", source)


if __name__ == "__main__":
    unittest.main()
