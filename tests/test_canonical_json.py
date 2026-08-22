from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "plugins/cortex/scripts"))

import cortex
from cortex_runtime import canonical_json, governance


def test_canonical_normalization_preserves_json_scalar_types() -> None:
    value = {"count": 3, "enabled": True, "ratio": 1.5, "missing": None, "nested": ("1", 1)}
    normalized = canonical_json.normalize(value)

    assert normalized == {"count": 3, "enabled": True, "ratio": 1.5, "missing": None, "nested": ["1", 1]}
    assert type(normalized["count"]) is int
    assert type(normalized["nested"][1]) is int
    assert canonical_json.dumps(value) == '{"count":3,"enabled":true,"missing":null,"nested":["1",1],"ratio":1.5}'


def test_digest_is_type_sensitive_and_shared_by_governance() -> None:
    numeric = {"promotion_window_days": 90}
    textual = {"promotion_window_days": "90"}

    assert canonical_json.digest(numeric) != canonical_json.digest(textual)
    assert governance._digest(numeric) == canonical_json.digest(numeric)
    assert canonical_json.digest(numeric) == hashlib.sha256(
        canonical_json.dumps(numeric).encode("utf-8")
    ).hexdigest()


def test_sanitize_structured_redacts_strings_without_stringifying_safe_scalars() -> None:
    sanitized = cortex.sanitize_structured({
        "api_key": "secret-value",
        "count": 90,
        "enabled": False,
        "missing": None,
        "label": "kept",
    })

    assert sanitized == {
        "api_key": "<REDACTED>",
        "count": 90,
        "enabled": False,
        "missing": None,
        "label": "kept",
    }
    assert type(sanitized["count"]) is int
    # The redacted projection remains canonical JSON and can be persisted.
    json.dumps(sanitized, ensure_ascii=False, sort_keys=True, allow_nan=False)
