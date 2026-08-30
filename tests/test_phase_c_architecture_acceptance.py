"""Phase C parity, registry and handle-edge acceptance gates."""
from __future__ import annotations

import re
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "cortex" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS  # noqa: E402
from cortex_runtime.public_contracts import V12_TOOL_NAMES  # noqa: E402

EXPECTED_TOOLS = (
    "open_task", "read_task", "open_clarification", "record_clarification", "open_plan_review", "record_plan_review",
    "open_steering", "record_steering", "open_assignment",
    "consume_assignment_evidence", "publish_plan", "publish_result",
    "publish_documentation", "assess_governance", "close_task",
)


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


class PhaseCAcceptance(unittest.TestCase):
    def test_every_parity_capability_has_stable_id_owner_and_preservation(self) -> None:
        from cortex_runtime import semantic_registry
        ids = list(semantic_registry.FEATURE_IDS)
        self.assertGreater(len(ids), 40)
        self.assertEqual(len(ids), len(set(ids)))
        for capability_id in ids:
            self.assertRegex(capability_id, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
            self.assertIn(capability_id, semantic_registry.FEATURE_OWNERS)

    def test_single_server_keeps_complete_semantic_surface(self) -> None:
        self.assertEqual(tuple(V12_TOOL_NAMES), EXPECTED_TOOLS)
        self.assertEqual(tuple(PUBLIC_TOOLS), EXPECTED_TOOLS)
        for name in EXPECTED_TOOLS:
            contract = PUBLIC_TOOLS[name]
            self.assertIsInstance(contract.get("inputSchema"), dict, name)
            self.assertIsInstance(contract.get("outputSchema"), dict, name)
            self.assertTrue(callable(contract.get("handler")), name)

    def test_registry_is_authoritative_and_covers_operations(self) -> None:
        from cortex_runtime import semantic_registry  # type: ignore
        specs = semantic_registry.operation_specs()
        self.assertEqual(tuple(spec.name for spec in specs), EXPECTED_TOOLS)
        self.assertGreaterEqual(len(semantic_registry.FEATURE_IDS), 44)
        self.assertEqual(set(semantic_registry.FEATURE_IDS), set(semantic_registry.FEATURE_OWNERS))
        for spec in specs:
            self.assertTrue(spec.feature_ids, spec.name)
            self.assertTrue(spec.handler_name, spec.name)
            self.assertTrue(spec.input_schema_key, spec.name)
            self.assertTrue(spec.output_schema_key, spec.name)
        # These IDs are the stable preservation vocabulary, including
        # coordinator/worker capabilities that intentionally have no backend
        # operation.  Removing one is a feature cut, not a registry refactor.
        required = {
            "explicit-opt-in", "english-worker-boundary", "worker-only-execution", "worker-required",
            "dynamic-dag", "dag-lifecycle", "parallel-waves", "recovery-rework", "worker-liveness",
            "planner-discovery", "immutable-plan", "plan-approval", "clarification", "same-task-steering",
            "typed-evidence", "evidence-read-receipts", "worker-report-publication", "role-complete-reports",
            "one-terminal-result", "historical-evidence", "model-routing", "profile-specialization",
            "governance-depth", "governance-nonblocking", "initiatives", "initiative-materiality",
            "documentation-impact", "documentation-sync", "knowledge-route", "content-safety", "context-recovery",
            "forward-migrations", "concurrency-replay", "lost-response-reconciliation", "server-handles",
            "atomic-publication", "closure-readiness", "unresolved-risks", "projections", "worker-hidden-errors",
            "llm-live-dev", "candidate-provenance", "single-mcp-catalogue", "schema-authority", "package-validation",
        }
        self.assertTrue(required <= set(semantic_registry.FEATURE_IDS))

    def test_registry_declares_every_handle_edge_without_skips(self) -> None:
        from cortex_runtime import semantic_registry  # type: ignore
        edges = getattr(semantic_registry, "CAPABILITY_EDGES", None)
        if edges is None:
            edges = semantic_registry.producer_consumer_edges()
        self.assertIsInstance(edges, (list, tuple))
        self.assertTrue(edges)
        for edge in edges:
            self.assertEqual(len(edge), 3)
            producer, consumer, capability = edge
            self.assertIn(producer, EXPECTED_TOOLS)
            self.assertIn(consumer, EXPECTED_TOOLS)
            self.assertTrue(capability)

    def test_catalogue_and_registry_cannot_drift(self) -> None:
        from cortex_runtime import semantic_registry  # type: ignore
        registry = {spec.name: spec for spec in semantic_registry.operation_specs()}
        self.assertEqual(set(registry), set(PUBLIC_TOOLS))
        contracts = semantic_registry.build_contracts()
        for name in EXPECTED_TOOLS:
            self.assertEqual(PUBLIC_TOOLS[name]["inputSchema"], contracts[name]["inputSchema"], name)
            self.assertEqual(PUBLIC_TOOLS[name]["description"], contracts[name]["description"], name)
            self.assertEqual(registry[name].input_schema_key, name)


if __name__ == "__main__":
    unittest.main()
