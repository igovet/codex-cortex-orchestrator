from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex
from cortex_runtime import canonical_json, governance


class CanonicalJsonTests(unittest.TestCase):
    def test_canonical_normalization_preserves_json_scalar_types(self) -> None:
        value = {"count": 3, "enabled": True, "ratio": 1.5, "missing": None, "nested": ("1", 1)}
        normalized = canonical_json.normalize(value)

        self.assertEqual(normalized, {"count": 3, "enabled": True, "ratio": 1.5, "missing": None, "nested": ["1", 1]})
        self.assertIs(type(normalized["count"]), int)
        self.assertIs(type(normalized["nested"][1]), int)
        self.assertEqual(canonical_json.dumps(value), '{"count":3,"enabled":true,"missing":null,"nested":["1",1],"ratio":1.5}')


    def test_digest_is_type_sensitive_and_shared_by_governance(self) -> None:
        numeric = {"promotion_window_days": 90}
        textual = {"promotion_window_days": "90"}

        self.assertNotEqual(canonical_json.digest(numeric), canonical_json.digest(textual))
        self.assertEqual(governance._digest(numeric), canonical_json.digest(numeric))
        self.assertEqual(canonical_json.digest(numeric), hashlib.sha256(
            canonical_json.dumps(numeric).encode("utf-8")
        ).hexdigest())


    def test_sanitize_structured_redacts_strings_without_stringifying_safe_scalars(self) -> None:
        sanitized = cortex.sanitize_structured({
            "api_key": "secret-value",
            "count": 90,
            "enabled": False,
            "missing": None,
            "label": "kept",
        })

        self.assertEqual(sanitized, {
            "api_key": "<REDACTED>",
            "count": 90,
            "enabled": False,
            "missing": None,
            "label": "kept",
        })
        self.assertIs(type(sanitized["count"]), int)
        # The redacted projection remains canonical JSON and can be persisted.
        json.dumps(sanitized, ensure_ascii=False, sort_keys=True, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
