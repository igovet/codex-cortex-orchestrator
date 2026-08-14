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
        expected_tools = {"start_orchestration", "continue_orchestration", "manage_orchestration", "worker_question", "record_report", "read_worker_report"}
        if set(tools) != expected_tools:
            raise SystemExit("fresh plugin probe: Cortex v3 public tool set is incomplete")
        workspace = base / "workspace"
        workspace.mkdir()
        created = mcp_tool(server, environment, workspace, "start_orchestration", {
            "task": {
                "objective": "verify the installed MCP workspace binding",
                "complexity": "C1", "requirements": [],
            },
            "waves": [{"workers": [{"phase": "discover", "profile": "explorer"}]}],
        })
        task_dirs = list((workspace / ".codex/cortex/tasks").iterdir())
        if not created.get("ok") or created.get("outcome") != "ready_to_spawn" or len(task_dirs) != 1:
            raise SystemExit("fresh plugin probe: installed MCP did not create a project-local task ledger")
        expected_task = task_dirs[0]
        confirmed = mcp_tool(server, environment, workspace, "manage_orchestration", {"intent": "inspect"})
        task = json.loads((expected_task / "task.json").read_text(encoding="utf-8"))
        if not confirmed.get("ok") or task.get("project_root") != str(workspace):
            raise SystemExit("fresh plugin probe: created Cortex task was not immediately confirmed in the selected workspace")
        print(json.dumps({"status": "PASS", "plugin": record.get("pluginId"), "version": record.get("version"), "mcp": "cortex", "isolation": "temporary HOME and CODEX_HOME"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
