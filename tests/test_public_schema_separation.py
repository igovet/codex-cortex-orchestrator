"""Public MCP schema projection remains separate from runtime validation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS  # noqa: E402
from cortex_runtime.mcp_api import catalogue_identity, _validate_schema  # noqa: E402


def test_all_public_tools_have_compact_and_closed_runtime_schemas() -> None:
    assert len(PUBLIC_TOOLS) == 20
    for name, contract in PUBLIC_TOOLS.items():
        public = contract["outputSchema"]
        runtime = contract["runtimeOutputSchema"]
        # Static no-dispatch/non-publication variants are complete closed
        # objects, not an untyped catch-all. Sharing an immutable schema value
        # is harmless; validate its contract rather than Python object identity.
        for schema in (public, runtime):
            variants = schema.get("anyOf", [schema])
            for variant in variants:
                assert variant["type"] == "object" and variant["additionalProperties"] is False, name
                assert "handles" not in variant.get("properties", {}), name
                assert len(variant["properties"]) >= 2, name


def test_compact_catalogue_is_bounded_and_runtime_rejects_extra_fields() -> None:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {
            "tools": [{"name": name, "description": c["description"], "inputSchema": c["inputSchema"]}
                      for name, c in PUBLIC_TOOLS.items()],
        }},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    assert len(payload) < 65536
    assert 65536 - len(payload) >= 4096
    assert catalogue_identity(PUBLIC_TOOLS)["catalogue_count"] == 20
    runtime = PUBLIC_TOOLS["open_clarification"]["runtimeOutputSchema"]
    try:
        _validate_schema(runtime, {"unexpected": True})
    except ValueError:
        pass
    else:
        raise AssertionError("private runtime schema accepted an undeclared field")
