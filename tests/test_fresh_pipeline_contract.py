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

    def test_corrupt_plan_recovery_restores_only_matching_wave_workers(self) -> None:
        def semantic_worker(
            profile: str,
            objective: str,
            paths: list[str],
            *,
            dependencies: list[str] | str = "all_verified_predecessors",
        ) -> dict[str, object]:
            return {
                "profile": profile,
                "objective": objective,
                "strategy": "bounded",
                "paths": paths,
                "dependencies": dependencies,
                "context_files": [],
                "acceptance_criteria": [],
                "verification": [],
            }

        semantic = [
            {
                "phase": "implementation",
                "workers": [
                    {
                        **semantic_worker(
                            "backend_dev", "Restore the backend implementation.",
                            ["src/backend.py"], dependencies=["plan"],
                        ),
                        "context_files": ["docs/project/index.md"],
                        "acceptance_criteria": ["Backend behavior is restored."],
                        "verification": ["Run the backend checks."],
                    },
                    semantic_worker(
                        "frontend_dev", "Restore the frontend implementation.",
                        ["src/frontend.ts"],
                    ),
                ],
            },
            {
                "phase": "qa",
                "workers": [semantic_worker(
                    "qa_engineer", "Verify the recovered implementation.", ["tests"],
                )],
            },
        ]
        corrupted_plan = {
            "waves": [],
            "history": [{"semantic_future_pipeline": semantic}],
        }
        state = {
            "chosen_pipeline": ["implementation", "qa"],
            "completed_gates": [],
            "skipped_gates": [],
            "attempts": [],
        }

        recovered = orchestration_engine._delivery_recovery_waves(
            Path("."), state, corrupted_plan,
        )

        self.assertEqual([wave["wave_id"] for wave in recovered], ["retry-implementation", "retry-qa"])
        implementation = recovered[0]["delegations"]
        qa = recovered[1]["delegations"]
        self.assertEqual(len(implementation), 2)
        self.assertEqual(
            [(item["agent"], item["objective"], item["allowed_paths"]) for item in implementation],
            [
                ("backend_dev", "Restore the backend implementation.", ["src/backend.py"]),
                ("frontend_dev", "Restore the frontend implementation.", ["src/frontend.ts"]),
            ],
        )
        self.assertEqual(implementation[0]["context_gates"], ["plan"])
        self.assertEqual(len(qa), 1)
        self.assertEqual(qa[0]["agent"], "qa_engineer")
        self.assertEqual(qa[0]["allowed_paths"], ["tests"])
        self.assertTrue(all(item["gate"] == "implementation" for item in implementation))
        self.assertTrue(all(item["gate"] == "qa" for item in qa))

        with self.assertRaisesRegex(ValueError, "inherit phase from their wave"):
            orchestration_engine._historical_recovery_specs({
                "waves": [],
                "history": [{"semantic_future_pipeline": [{
                    "phase": "implementation",
                    "workers": [{"phase": "implementation", "profile": "general"}],
                }]}],
            }, "implementation")

        with self.assertRaisesRegex(ValueError, "exactly phase and workers"):
            orchestration_engine._historical_recovery_specs({
                "waves": [],
                "history": [{"semantic_future_pipeline": [{
                    "workers": [{"phase": "implementation", "profile": "backend_dev"}],
                }]}],
            }, "implementation")

        malformed_qa = semantic_worker(
            "qa_engineer", "Malformed unrelated QA.", ["tests"],
        )
        malformed_qa["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "unsupported fields: unexpected"):
            orchestration_engine._historical_recovery_specs({
                "waves": [],
                "history": [{"semantic_future_pipeline": [
                    {"phase": "implementation", "workers": [semantic_worker(
                        "backend_dev", "Valid target worker.", ["src/backend.py"],
                    )]},
                    {"phase": "qa", "workers": [malformed_qa]},
                ]}],
            }, "implementation")


if __name__ == "__main__":
    unittest.main()
