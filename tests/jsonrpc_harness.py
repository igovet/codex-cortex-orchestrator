"""Small black-box client for the orchestration stdio MCP server."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


_AUTO_REQUEST_ID = object()


class JsonRpcHarness:
    def __init__(
        self,
        server: Path,
        project_root: Path,
        host_state_dir: Path,
        *,
        audience: str | None = None,
        worker_binding: dict[str, Any] | None = None,
        elicitation_responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ):
        environment = os.environ.copy()
        environment.pop("CORTEX_ROOT", None)
        environment.pop("CORTEX_PROJECT_ROOT", None)
        self.project_root = project_root
        # The black-box server must use its host-private per-project mapping,
        # never a test/workspace-local ``.codex/cortex`` path.
        environment["CORTEX_HOST_STATE_DIR"] = str(host_state_dir)
        if worker_binding is not None:
            environment["CORTEX_WORKER_BINDING_JSON"] = json.dumps(worker_binding, sort_keys=True)
        command = [sys.executable, str(server)]
        if audience is not None:
            command.append(f"--mcp-audience={audience}")
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        self.elicitation_responder = elicitation_responder
        self.audience = audience
        self.next_id = 1
        self._closed = False
        initialized = self.request("initialize", {"protocolVersion": "2025-06-18"})
        if initialized["serverInfo"]["name"] != "cortex":
            raise RuntimeError("unexpected MCP server")
        self.notify("notifications/initialized", {})

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        request_id: object = _AUTO_REQUEST_ID,
    ) -> dict[str, Any]:
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("MCP process pipes are unavailable")
        if request_id is _AUTO_REQUEST_ID:
            request_id = self.next_id
            self.next_id += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"MCP server exited without a response: {stderr}")
        response = json.loads(line)
        while response.get("id") != request_id and response.get("method") == "elicitation/create":
            if self.elicitation_responder is None:
                raise RuntimeError(f"unexpected nested elicitation request: {response}")
            nested_id = response.get("id")
            nested_result = self.elicitation_responder(response)
            self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": nested_id, "result": nested_result}) + "\n")
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

    def tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        request_id: object = _AUTO_REQUEST_ID,
    ) -> dict[str, Any]:
        task_scoped = (
            name in {"continue_orchestration", "read_worker_result"}
            or (
                name == "manage_orchestration"
                and str(arguments.get("intent") or "") not in {"prune", "maintenance"}
                and bool(str(arguments.get("task_ref") or "").strip())
            )
        )
        # Worker and task-scoped coordinator forms derive identity from the
        # launch/session binding and reject caller-supplied project_root.
        payload = dict(arguments)
        if self.audience != "worker" and not task_scoped:
            payload["project_root"] = str(self.project_root)
        return self.request(
            "tools/call",
            {"name": name, "arguments": payload},
            request_id=request_id,
        )["structuredContent"]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        timed_out = False
        stderr = ""
        try:
            if self.process.stdin and not self.process.stdin.closed:
                try:
                    self.process.stdin.close()
                except BrokenPipeError:
                    pass
            self.process.stdin = None
            try:
                _, stderr = self.process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                timed_out = True
                self.process.terminate()
                try:
                    _, stderr = self.process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    _, stderr = self.process.communicate(timeout=2)
            return_code = self.process.returncode
        finally:
            for pipe in (self.process.stdout, self.process.stderr):
                if pipe and not pipe.closed:
                    pipe.close()
        if timed_out:
            raise RuntimeError(f"MCP server did not exit after stdin closed; terminated with {return_code}: {stderr}")
        if return_code != 0:
            raise RuntimeError(f"MCP server exited {return_code}: {stderr}")

    def __enter__(self) -> "JsonRpcHarness":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
