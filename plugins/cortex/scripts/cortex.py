#!/usr/bin/env python3
"""Cortex v12 MCP entrypoint.

This composition root deliberately contains no orchestration state machine.
The v12 store owns durable data integrity while models own delegation, native
agent execution, governance decisions, recovery, and user-facing completion.
"""
from __future__ import annotations

import sys

# The ordinary MCP command intentionally remains ``python3 ./scripts/cortex.py``.
# Disable bytecode before importing any bundled runtime module so an installed
# cache never receives generated ``__pycache__`` artifacts merely by launching
# the server.
sys.dont_write_bytecode = True

from collections.abc import Callable, Mapping
from typing import Any

from cortex_runtime.mcp_api import serve_stdio
from cortex_runtime.model_routing import native_spawn_arguments
from cortex_runtime.public_contracts import V12_TOOL_NAMES, build_public_contracts
from cortex_runtime.v12_service import (
    create_delegation,
    create_task,
    inspect_governance,
    inspect_task,
    read_delegation,
    read_reports,
    record_user_decision,
    record_initiative,
    set_governance_mode,
    submit_governance_closure,
    submit_report,
)


SERVER_VERSION = "12.1.0"
SERVER_INSTRUCTIONS = (
    "Cortex v12 is a durable coordination ledger. All participants receive the "
    "same eleven tools. The model owns delegation, model/effort selection, governance, "
    "rework, verification depth, and final-answer decisions. Governance records are "
    "advisory and never block safe coordination or a user-facing answer."
)


_HANDLERS: Mapping[str, Callable[..., Mapping[str, Any]]] = {
    "create_task": create_task,
    "inspect_task": inspect_task,
    "create_delegation": create_delegation,
    "read_delegation": read_delegation,
    "submit_report": submit_report,
    "read_reports": read_reports,
    "set_governance_mode": set_governance_mode,
    "record_initiative": record_initiative,
    "inspect_governance": inspect_governance,
    "submit_governance_closure": submit_governance_closure,
    "record_user_decision": record_user_decision,
}


def build_v12_public_tools() -> dict[str, dict[str, Any]]:
    """Bind the uniform v12 contracts directly to their durable handlers."""
    contracts = build_public_contracts()
    if tuple(contracts) != V12_TOOL_NAMES or tuple(_HANDLERS) != V12_TOOL_NAMES:
        raise RuntimeError("Cortex v12 must expose exactly the canonical eleven-tool catalogue")
    return {
        name: {**dict(contracts[name]), "handler": _HANDLERS[name]}
        for name in V12_TOOL_NAMES
    }


# The coordinator owns logical model/effort selection.  This is intentionally
# only a pure serialization seam: it has no host attestation, lifecycle state,
# recovery ladder, or authority effect.  Re-exporting it keeps direct/native
# integrations on the same Luna-omission rule as the bundled runtime.
native_spawn_projection = native_spawn_arguments


PUBLIC_TOOLS = build_v12_public_tools()


def main() -> None:
    """Serve the single public V12 MCP catalogue over stdio."""
    if sys.argv[1:]:
        raise SystemExit("usage: cortex.py")
    serve_stdio(
        public_tools=PUBLIC_TOOLS,
        server_version=SERVER_VERSION,
        instructions=SERVER_INSTRUCTIONS,
    )


if __name__ == "__main__":
    main()
