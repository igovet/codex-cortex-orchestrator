#!/usr/bin/env python3
"""Verify plugin registration using an isolated HOME/CODEX_HOME and fresh CLI processes."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugins/cortex"
PROFILE_CONTRACT = json.loads((SOURCE / "profiles.json").read_text(encoding="utf-8"))
VERSION = json.loads((SOURCE / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
EXPECTED_AGENT_FILES = {item["filename"] for item in PROFILE_CONTRACT["profiles"]}
EXPECTED_SKILLS = {"adaptive-pipeline", "content-safety", "context-compaction", "orchestrator", "cortex-control", "documentation-sync", "find-skills", "knowledge-harvest", "output-validation", "token-monitoring"}


def command(argv: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=ROOT, env=environment, text=True, capture_output=True, timeout=30, check=False)


def mcp_tool(server: Path, environment: dict[str, str], workspace: Path, name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Call an installed MCP entry from its configured plugin-local cwd."""
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": {**arguments, "project_root": str(workspace)}},
    }
    rpc = subprocess.run(
        [os.environ.get("PYTHON", "python3"), str(server)], input=json.dumps(payload) + "\n",
        cwd=server.parents[1], env=environment, text=True, capture_output=True, timeout=30, check=False,
    )
    if rpc.returncode != 0:
        raise SystemExit(f"fresh plugin probe: cached Cortex MCP {name} failed to start: {rpc.stderr.strip()}")
    try:
        response = json.loads(rpc.stdout)
        return response["result"]["structuredContent"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SystemExit(f"fresh plugin probe: cached Cortex MCP {name} returned an invalid response: {rpc.stdout!r}") from exc


def main() -> int:
    codex = shutil.which("codex")
    if not codex:
        print("fresh plugin probe: SKIP (codex CLI is unavailable)")
        return 0
    with tempfile.TemporaryDirectory(prefix="codex-plugin-probe-") as directory:
        base = Path(directory)
        home = base / "home"
        checkout = base / "checkout"
        home.mkdir()
        (home / ".codex").mkdir()
        shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns(".git", ".codex", "__pycache__", "*.pyc", "*.pyo"))
        if not (checkout / ".agents/plugins/marketplace.json").is_file():
            raise SystemExit("fresh plugin probe: root marketplace manifest is missing from the checkout")
        if (checkout / "marketplace").exists() or (checkout / "marketplace").is_symlink():
            raise SystemExit("fresh plugin probe: retired nested marketplace artifact is present")
        environment = os.environ.copy()
        environment.update({"HOME": str(home), "CODEX_HOME": str(home / ".codex")})
        added_marketplace = command([codex, "plugin", "marketplace", "add", str(checkout), "--json"], environment)
        if added_marketplace.returncode != 0:
            raise SystemExit("fresh plugin probe: marketplace registration failed: " + added_marketplace.stderr.strip())
        installed = command([codex, "plugin", "add", f"{SOURCE.name}@cortex", "--json"], environment)
        if installed.returncode != 0:
            raise SystemExit("fresh plugin probe: plugin installation failed: " + installed.stderr.strip())
        listed = command([codex, "mcp", "list"], environment)
        if listed.returncode != 0 or "cortex" not in listed.stdout or "enabled" not in listed.stdout:
            raise SystemExit("fresh plugin probe: cortex was not exposed by a fresh Codex CLI process")
        if "${PLUGIN_ROOT}" in listed.stdout:
            raise SystemExit("fresh plugin probe: Cortex MCP still relies on an unexpanded PLUGIN_ROOT argument")
        plugins = command([codex, "plugin", "list", "--json"], environment)
        if plugins.returncode != 0:
            raise SystemExit("fresh plugin probe: plugin list failed")
        data = json.loads(plugins.stdout)
        items = data.get("installed", data) if isinstance(data, dict) else data
        record = next((item for item in items if item.get("name") == SOURCE.name and item.get("installed")), None)
        if not record:
            raise SystemExit("fresh plugin probe: installed plugin record is missing")
        if (home / ".codex/agents/orchestrator.toml").exists():
            raise SystemExit("fresh plugin probe: retired orchestrator profile was installed")
        cache = home / ".codex/plugins/cache/cortex/cortex" / VERSION
        installed_agents = {path.name for path in (cache / "agents").glob("*.toml")}
        installed_skills = {path.parent.name for path in (cache / "skills").glob("*/SKILL.md")}
        if installed_agents != EXPECTED_AGENT_FILES or installed_skills != EXPECTED_SKILLS:
            raise SystemExit("fresh plugin probe: installed agent profiles or skills are incomplete")
        cortex_skill = (cache / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        route_contract = (
            "| `empty` | `orchestrate` |",
            "| `help` | `help` |",
            "| `harvest` | `harvest` |",
            "| `harvest-refresh` | `harvest-refresh` |",
            "| `normal` | `normal` |",
        )
        skill_lines = cortex_skill.splitlines()
        if not all(any(line.startswith(route) for line in skill_lines) for route in route_contract):
            raise SystemExit("fresh plugin probe: installed Cortex route contract is incomplete")
        if "`cortex:orchestrator`" not in cortex_skill or "`$cortex:orchestrator`" not in cortex_skill:
            raise SystemExit("fresh plugin probe: installed Cortex native invocation help is incomplete")
        server = cache / "scripts/cortex.py"
        rpc = subprocess.run(
            [os.environ.get("PYTHON", "python3"), str(server)],
            input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n',
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if rpc.returncode != 0:
            raise SystemExit("fresh plugin probe: cached Cortex MCP failed to start")
        rows = [json.loads(line) for line in rpc.stdout.splitlines() if line.strip()]
        tools = {item["name"]: item for item in rows[1]["result"]["tools"]}
        activation = tools.get("activate_orchestration", {}).get("inputSchema", {})
        trigger = activation.get("properties", {}).get("user_command", {}).get("const")
        required = {"activate_orchestration", "deactivate_orchestration", "classify_task", "init_task", "record_delegation", "record_gate_outcome", "create_handoff"}
        if trigger != "/cortex" or not required.issubset(tools):
            raise SystemExit("fresh plugin probe: Cortex activation schema or required tools are missing")
        workspace = base / "workspace"
        workspace.mkdir()
        principal = "fresh-plugin-probe"
        activated = mcp_tool(server, environment, workspace, "activate_orchestration", {
            "user_command": "/cortex", "principal": principal, "thread_id": principal,
        })
        if not activated.get("active"):
            raise SystemExit("fresh plugin probe: installed Cortex MCP could not activate")
        classified = mcp_tool(server, environment, workspace, "classify_task", {
            "complexity": "C1", "requirements": [], "principal": principal,
        })
        created = mcp_tool(server, environment, workspace, "init_task", {
            "task_id": "fresh-plugin", "objective": "verify the installed MCP workspace binding",
            "complexity": "C1", "classification_id": classified["classification_id"], "requirements": [],
            "principal": principal, "thread_id": principal,
        })
        expected_task = workspace / ".codex/cortex/tasks" / str(created["task_directory"])
        if not created.get("created") or not expected_task.is_dir():
            raise SystemExit("fresh plugin probe: installed MCP did not create a project-local task ledger")
        confirmed = mcp_tool(server, environment, workspace, "get_task_status", {
            "task_id": "fresh-plugin", "principal": principal, "thread_id": principal,
        })
        if not confirmed.get("active") or confirmed.get("task", {}).get("project_root") != str(workspace):
            raise SystemExit("fresh plugin probe: created Cortex task was not immediately confirmed in the selected workspace")
        print(json.dumps({"status": "PASS", "plugin": record.get("pluginId"), "version": record.get("version"), "mcp": "cortex", "isolation": "temporary HOME and CODEX_HOME"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
