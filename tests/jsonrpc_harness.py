"""Small black-box client for the orchestration stdio MCP server."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class JsonRpcHarness:
    def __init__(self, server: Path, project_root: Path, ledger_root: Path):
        environment = os.environ.copy()
        environment.pop("CORTEX_ROOT", None)
        environment.pop("CORTEX_PROJECT_ROOT", None)
        self.project_root = project_root
        expected_ledger = project_root / ".codex" / "cortex"
        if ledger_root != expected_ledger:
            raise ValueError(f"ledger_root must be {expected_ledger}")
        self.process = subprocess.Popen(
            [sys.executable, str(server)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        self.next_id = 1
        initialized = self.request("initialize", {"protocolVersion": "2025-06-18"})
        if initialized["serverInfo"]["name"] != "cortex":
            raise RuntimeError("unexpected MCP server")
        self.notify("notifications/initialized", {})

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("MCP process pipes are unavailable")
        request_id = self.next_id
        self.next_id += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"MCP server exited without a response: {stderr}")
        response = json.loads(line)
        if response.get("id") != request_id:
            raise RuntimeError(f"unexpected response id: {response}")
        if "error" in response:
            raise RuntimeError(response["error"]["message"])
        return response["result"]

    def notify(self, method: str, params: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise RuntimeError("MCP process stdin is unavailable")
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
        self.process.stdin.flush()

    def tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": {**arguments, "project_root": str(self.project_root)}})["structuredContent"]

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        return_code = self.process.wait(timeout=5)
        if return_code != 0:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"MCP server exited {return_code}: {stderr}")

    def __enter__(self) -> "JsonRpcHarness":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
