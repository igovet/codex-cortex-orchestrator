"""Public MCP schema projection remains separate from runtime validation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cortex import PUBLIC_TOOLS  # noqa: E402
from cortex_runtime.mcp_api import catalogue_identity, _validate_schema  # noqa: E402


def test_all_fifteen_tools_have_compact_public_and_closed_runtime_schemas() -> None:
    assert len(PUBLIC_TOOLS) == 15
    for name, contract in PUBLIC_TOOLS.items():
        public = contract["outputSchema"]
        runtime = contract["runtimeOutputSchema"]
        assert public is not runtime, name
        assert public["required"] == ["handles"], name
        assert set(public["properties"]) <= {
            "handles", "replayed", "status", "state", "assembly_state",
            "next_action", "has_more", "next_cursor", "next_sequence",
        }, name
        assert runtime["type"] == "object", name
        assert "properties" in runtime and len(runtime["properties"]) >= 2, name


def test_compact_catalogue_is_bounded_and_runtime_rejects_extra_fields() -> None:
    payload = json.dumps(
        {"tools": [{"name": name, "inputSchema": c["inputSchema"], "outputSchema": c["outputSchema"]}
                    for name, c in PUBLIC_TOOLS.items()]},
        separators=(",", ":"),
    ).encode()
    assert len(payload) < 65536
    assert catalogue_identity(PUBLIC_TOOLS)["catalogue_count"] == 15
    runtime = PUBLIC_TOOLS["open_clarification"]["runtimeOutputSchema"]
    try:
        _validate_schema(runtime, {"handles": {}, "unexpected": True})
    except ValueError:
        pass
    else:
        raise AssertionError("private runtime schema accepted an undeclared field")
