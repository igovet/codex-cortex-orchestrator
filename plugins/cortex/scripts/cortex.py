#!/usr/bin/env python3
"""Cortex v12 MCP entrypoint.

This composition root deliberately contains no orchestration state machine.
The v12 store owns durable data integrity while models own delegation, native
agent execution, governance decisions, recovery, and user-facing completion.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import sys

# The ordinary MCP command intentionally remains ``python3 ./scripts/cortex.py``.
# Disable bytecode before importing any bundled runtime module so an installed
# cache never receives generated ``__pycache__`` artifacts merely by launching
# the server.
sys.dont_write_bytecode = True

from collections.abc import Callable, Mapping
from typing import Any

from cortex_runtime.mcp_api import serve_stdio
from cortex_runtime.public_contracts import build_public_contracts
from cortex_runtime.semantic_registry import OPERATION_NAMES, bind_handlers
from cortex_runtime.domain_api import (
    assess_governance,
    close_task,
    open_assignment,
    open_task,
    publish_documentation,
    publish_plan,
    publish_result,
    read_task,
    open_clarification,
    open_plan_review,
    open_steering,
    record_clarification, record_plan_review, record_steering,
)


SERVER_VERSION = "1.14.12"
SERVER_INSTRUCTIONS = (
    "Cortex v12 is a durable coordination ledger with a complete "
    f"{len(OPERATION_NAMES)}-operation registry and immutable host-attested audience projections. "
    "Every Cortex operation is one separate direct MCP call and is never eligible for programmatic tool calling, exec, batching, parallelism, or speculative partial calls; this keeps each complete advertised input contract and result visible to the model. "
    "Use only the tools advertised to this connection. The model owns delegation, model/effort selection, governance, "
    "rework, verification depth, and final-answer decisions. Governance records are "
    "advisory and never block safe coordination or a user-facing answer."
)


_HANDLERS: Mapping[str, Callable[..., Mapping[str, Any]]] = {
    "open_task": open_task,
    "read_task": read_task,
    "open_clarification": open_clarification,
    "open_plan_review": open_plan_review,
    "open_steering": open_steering,
    "record_clarification": record_clarification,
    "record_plan_review": record_plan_review,
    "record_steering": record_steering,
    "open_assignment": open_assignment,
    "publish_plan": publish_plan,
    "publish_result": publish_result,
    "publish_documentation": publish_documentation,
    "assess_governance": assess_governance,
    "close_task": close_task,
}
_HANDLERS = {name: _HANDLERS[name] for name in OPERATION_NAMES}

def build_v12_public_tools() -> dict[str, dict[str, Any]]:
    """Bind the complete v12 registry directly to durable handlers."""
    contracts = build_public_contracts()
    if tuple(contracts) != OPERATION_NAMES or tuple(_HANDLERS) != OPERATION_NAMES:
        raise RuntimeError(f"Cortex v12 must expose exactly the canonical {len(OPERATION_NAMES)}-tool catalogue")
    return bind_handlers(_HANDLERS)


PUBLIC_TOOLS = build_v12_public_tools()


def main() -> None:
    """Serve audience projections of the complete V12 registry over stdio."""
    if sys.argv[1:]:
        raise SystemExit("usage: cortex.py")
    serve_stdio(
        public_tools=PUBLIC_TOOLS,
        server_version=SERVER_VERSION,
        instructions=SERVER_INSTRUCTIONS,
    )


if __name__ == "__main__":
    main()
