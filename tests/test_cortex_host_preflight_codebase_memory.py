"""Host preflight must require the worker-owned Codebase Memory MCP."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "cortex_host_preflight_codebase_memory", ROOT / "scripts/cortex-host-preflight.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_requires_enabled_top_level_codebase_memory_server():
    inspect = _module().inspect_codebase_memory_config
    assert inspect({})["status"] == "FAIL"
    assert inspect({"mcp_servers": {"codebase_memory": {"enabled": False, "command": "memory"}}})["status"] == "FAIL"
    assert inspect({"mcp_servers": {"codebase_memory": {"enabled": True, "command": ""}}})["status"] == "FAIL"


def test_preflight_accepts_only_safe_local_stdio_metadata():
    inspect = _module().inspect_codebase_memory_config
    valid = {"mcp_servers": {"codebase_memory": {"enabled": True, "command": "codebase-memory", "args": ["serve"]}}}
    assert inspect(valid)["status"] == "PASS"
    remote = {"mcp_servers": {"codebase_memory": {"enabled": True, "url": "https://example.invalid"}}}
    assert inspect(remote)["status"] == "FAIL"
    secret = {"mcp_servers": {"codebase_memory": {"enabled": True, "command": "memory", "env": {"TOKEN": "redacted"}}}}
    result = inspect(secret)
    assert result["status"] == "FAIL"
    assert "redacted" not in result["detail"]
