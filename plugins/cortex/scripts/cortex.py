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
from cortex_runtime.public_contracts import V12_TOOL_NAMES, build_public_contracts
from cortex_runtime.domain_api import (
    assess_governance,
    close_task,
    consume_assignment_evidence,
    open_assignment,
    open_task,
    publish_documentation,
    publish_plan,
    publish_result,
    read_task,
    open_decision,
    record_decision,
)


SERVER_VERSION = "12.1.1"
SERVER_INSTRUCTIONS = (
    "Cortex v12 is a durable coordination ledger. All participants receive the "
    "same eleven semantic tools for task, assignment, evidence, publication, decision, "
    "governance, and closure. The model owns delegation, model/effort selection, governance, "
    "rework, verification depth, and final-answer decisions. Governance records are "
    "advisory and never block safe coordination or a user-facing answer."
)


_HANDLERS: Mapping[str, Callable[..., Mapping[str, Any]]] = {
    "open_task": open_task,
    "read_task": read_task,
    "open_decision": open_decision,
    "open_assignment": open_assignment,
    "consume_assignment_evidence": consume_assignment_evidence,
    "publish_plan": publish_plan,
    "publish_result": publish_result,
    "publish_documentation": publish_documentation,
    "record_decision": record_decision,
    "assess_governance": assess_governance,
    "close_task": close_task,
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
