from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

from cortex_runtime.revision_impact import classify_revision_impact


class RevisionImpactTests(unittest.TestCase):
    pipeline = ["plan", "architecture", "implementation", "qa", "security", "review", "documentation", "close"]

    def test_database_and_multitenant_steer_rewinds_to_architecture(self) -> None:
        impact = classify_revision_impact(
            "Add multi-tenant authorization and a separate database schema.",
            pipeline=self.pipeline,
            current_gates=["implementation"],
            active_attempt_ids=["attempt-implementation"],
        )
        self.assertEqual(impact["earliest_affected_gate"], "architecture")
        self.assertTrue(impact["requires_plan_revision"])
        self.assertEqual(impact["invalidate_gates"][0], "architecture")

    def test_documentation_only_change_does_not_rewind_implementation(self) -> None:
        impact = classify_revision_impact(
            "Documentation only: update the README wording.",
            pipeline=self.pipeline,
            current_gates=["implementation"],
        )
        self.assertEqual(impact["classification"], "documentation_only")
        self.assertEqual(impact["earliest_affected_gate"], "documentation")

    def test_unknown_material_change_conservatively_affects_active_gate(self) -> None:
        impact = classify_revision_impact(
            "Reconcile the newly requested behavior.",
            pipeline=self.pipeline,
            current_gates=["implementation"],
        )
        self.assertEqual(impact["earliest_affected_gate"], "implementation")


if __name__ == "__main__":
    unittest.main()
