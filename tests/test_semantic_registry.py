from __future__ import annotations

import unittest

from cortex_runtime.public_contracts import build_public_contracts
from cortex_runtime.semantic_registry import (
    FEATURE_IDS,
    FEATURE_OWNERS,
    OPERATION_NAMES,
    exported_metadata,
    operation_specs,
    producer_consumer_edges,
    validate_receipt_metadata,
)


class SemanticRegistryConformanceTests(unittest.TestCase):
    def test_registry_and_public_catalogue_have_one_ordered_surface(self) -> None:
        contracts = build_public_contracts()
        self.assertEqual(tuple(contracts), OPERATION_NAMES)
        self.assertEqual(len(OPERATION_NAMES), 14)
        self.assertEqual(len(set(OPERATION_NAMES)), 14)
        for name, contract in contracts.items():
            self.assertEqual(contract["inputSchema"].get("type"), "object", name)
            self.assertFalse(set(contract["inputSchema"]) - {"$schema", "type", "description", "additionalProperties", "properties", "required"}, name)
            self.assertIn("outputSchema", contract, name)

    def test_every_declared_feature_and_capability_is_mapped(self) -> None:
        specs = operation_specs()
        self.assertEqual(set(FEATURE_IDS), set(FEATURE_OWNERS))
        self.assertTrue({feature for spec in specs for feature in spec.feature_ids}.issubset(FEATURE_IDS))
        for spec in specs:
            self.assertTrue(spec.handler_name, spec.name)
            self.assertTrue(spec.safe_errors, spec.name)
        self.assertEqual(validate_receipt_metadata(), ())
        edges = producer_consumer_edges()
        self.assertTrue(edges)
        self.assertIn(("open_task", "read_task", "task_ref"), edges)
        self.assertEqual({field for _producer, _consumer, field in edges}, {"task_ref"})

    def test_storage_vocabulary_is_not_public(self) -> None:
        forbidden = {"begin_report", "append_report", "finalize_report", "abort_report", "read_reports", "create_task", "create_delegation", "open_decision", "record_user_decision"}
        self.assertFalse(forbidden.intersection(OPERATION_NAMES))

    def test_metadata_is_json_compatible(self) -> None:
        metadata = exported_metadata()
        self.assertEqual(tuple(item["name"] for item in metadata["operations"]), OPERATION_NAMES)
        self.assertEqual(len(metadata["feature_ids"]), len(FEATURE_IDS))

    def test_transport_catalogue_limit_is_derived_and_fits_one_frame(self) -> None:
        from cortex_runtime import mcp_api
        self.assertEqual(mcp_api._MAX_TOOLS, len(OPERATION_NAMES))
        self.assertLess(mcp_api._MAX_TOOLS, 64)


if __name__ == "__main__":
    unittest.main()
