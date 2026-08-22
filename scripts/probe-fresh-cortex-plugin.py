#!/usr/bin/env python3
"""Verify plugin registration using an isolated HOME/CODEX_HOME and fresh CLI processes."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
import cortex  # noqa: E402
SOURCE = ROOT / "plugins/cortex"
PROFILE_CONTRACT = json.loads((SOURCE / "profiles.json").read_text(encoding="utf-8"))
VERSION = json.loads((SOURCE / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
EXPECTED_AGENT_FILES = {item["filename"] for item in PROFILE_CONTRACT["profiles"]}
EXPECTED_SKILLS = {"adaptive-pipeline", "content-safety", "context-compaction", "orchestrator", "cortex-control", "documentation-sync", "find-skills", "knowledge-harvest", "output-validation", "progress-accounting"}


def command(argv: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=ROOT, env=environment, text=True, capture_output=True, timeout=30, check=False)


def mcp_tool(
    launcher: Path,
    entrypoint: Path,
    environment: dict[str, str],
    workspace: Path,
    name: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    """Call an installed MCP entry from its configured plugin-local cwd."""
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": {**arguments, "project_root": str(workspace)}},
    }
    rpc = subprocess.run(
        [str(launcher), str(entrypoint)], input=json.dumps(payload) + "\n",
        cwd=launcher.parents[1], env=environment, text=True, capture_output=True, timeout=30, check=False,
    )
    if rpc.returncode != 0:
        raise SystemExit(f"fresh plugin probe: configured Cortex launcher for {name} failed to start: {rpc.stderr.strip()}")
    try:
        response = json.loads(rpc.stdout)
        return response["result"]["structuredContent"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SystemExit(f"fresh plugin probe: configured Cortex launcher for {name} returned an invalid response: {rpc.stdout!r}") from exc


def main() -> int:
    codex = shutil.which("codex")
    if not codex:
        print("fresh plugin probe: SKIP (codex CLI is unavailable)")
        return 0
    with tempfile.TemporaryDirectory(prefix="codex-plugin-probe-") as directory:
        base = Path(directory)
        home = base / "home"
        checkout = base / "checkout"
        host_store = base / "host-private-cortex"
        home.mkdir()
        (home / ".codex").mkdir(mode=0o700)
        host_store.mkdir(mode=0o700)
        host_store.chmod(0o700)
        shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns(".git", ".codex", "__pycache__", "*.pyc", "*.pyo"))
        if not (checkout / ".agents/plugins/marketplace.json").is_file():
            raise SystemExit("fresh plugin probe: root marketplace manifest is missing from the checkout")
        if (checkout / "marketplace").exists() or (checkout / "marketplace").is_symlink():
            raise SystemExit("fresh plugin probe: retired nested marketplace artifact is present")
        environment = os.environ.copy()
        environment.update({
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            # The fresh plugin must prove the production host-private
            # control-store contract. This value is host configuration, never
            # an MCP argument, and remains outside the temporary workspace.
            "CORTEX_HOST_STATE_DIR": str(host_store),
        })
        environment.pop("CORTEX_ROOT", None)
        # This verifier imports the source control helpers only to derive the
        # opaque host mapping. Keep that local calculation on the exact same
        # temporary host store as the installed MCP subprocess.
        os.environ["CORTEX_HOST_STATE_DIR"] = str(host_store)
        os.environ.pop("CORTEX_ROOT", None)
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
        mcp_manifest = json.loads((cache / ".mcp.json").read_text(encoding="utf-8"))
        configured = mcp_manifest.get("mcpServers", {}).get("cortex", {})
        command_path = configured.get("command")
        configured_args = configured.get("args")
        if command_path != "./scripts/cortex-launcher" or configured_args != ["./scripts/cortex.py"] or configured.get("cwd") != ".":
            raise SystemExit("fresh plugin probe: installed MCP does not route through cortex-launcher")
        launcher = cache / command_path
        entrypoint = cache / configured_args[0]
        if not launcher.is_file() or not launcher.stat().st_mode & 0o111:
            raise SystemExit("fresh plugin probe: installed Cortex launcher is missing or not executable")
        hooks_manifest = json.loads((cache / "hooks/hooks.json").read_text(encoding="utf-8"))
        hook_commands = [
            hook.get("command")
            for registrations in hooks_manifest.get("hooks", {}).values()
            for registration in registrations
            for hook in registration.get("hooks", [])
        ]
        if len(hook_commands) != 6 or any(
            '"${PLUGIN_ROOT}/scripts/cortex-launcher"' not in command
            or '"${PLUGIN_ROOT}/scripts/cortex_hook.py"' not in command
            for command in hook_commands
        ):
            raise SystemExit("fresh plugin probe: lifecycle hooks do not use the installed Cortex launcher")
        rpc = subprocess.run(
            [str(launcher), str(entrypoint)],
            input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n',
            cwd=cache,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if rpc.returncode != 0:
            raise SystemExit("fresh plugin probe: configured Cortex launcher failed to start")
        rows = [json.loads(line) for line in rpc.stdout.splitlines() if line.strip()]
        tools = {item["name"]: item for item in rows[1]["result"]["tools"]}
        expected_tools = {
            "start_orchestration", "continue_orchestration", "manage_orchestration", "manage_governance", "worker_question",
            "record_attempt_event", "complete_attempt",
            "read_dispatch_briefing", "read_worker_result",
        }
        if set(tools) != expected_tools:
            raise SystemExit("fresh plugin probe: Cortex public tool set is incomplete")
        event_schema = tools["record_attempt_event"]["inputSchema"]
        result_schema = tools["complete_attempt"]["inputSchema"]
        if not {"event_type", "payload"}.issubset(event_schema["properties"]):
            raise SystemExit("fresh plugin probe: attempt-event schema lacks semantic event fields")
        if not {"status", "summary", "findings"}.issubset(result_schema["properties"]):
            raise SystemExit("fresh plugin probe: attempt-result schema lacks semantic result fields")
        workspace = base / "workspace"
        workspace.mkdir()
        rejected = mcp_tool(launcher, entrypoint, environment, workspace, "start_orchestration", {
            "task": {
                "user_request": "reject an installed task without an observable result contract",
                "complexity": "C1", "requirements": [],
            },
            "waves": [{"workers": [{"phase": "discover", "profile": "explorer"}]}],
        })
        if rejected.get("ok") is not False or rejected.get("outcome") != "needs_input":
            raise SystemExit("fresh plugin probe: installed MCP accepted a task without acceptance and verification")
        created = mcp_tool(launcher, entrypoint, environment, workspace, "start_orchestration", {
            "task": {
                "user_request": "verify the installed MCP pricing feature workspace binding",
                "complexity": "C1", "requirements": [],
                "acceptance_criteria": ["The installed MCP creates one canonical host-private task ledger bound to the workspace."],
                "verification": ["Inspect the generated task and orchestration schemas and dispatch identity."],
            },
            "waves": [{"workers": [{"phase": "discover", "profile": "explorer"}]}],
        })
        ledger = cortex.ledger_root_path({"project_root": str(workspace)}, create=False)
        task_dirs = list((ledger / "tasks").iterdir())
        if not created.get("ok") or created.get("outcome") != "ready_to_spawn" or len(task_dirs) != 1:
            raise SystemExit("fresh plugin probe: installed MCP did not create one host-private task ledger")
        expected_task = task_dirs[0]
        dispatch = created["dispatches"][0]
        if dispatch.get("display_name") != "Explorer Pricing":
            raise SystemExit(
                "fresh plugin probe: human worker display name did not select the explicit feature domain"
            )
        if re.search(r"\s\d+$", str(dispatch.get("display_name") or "")):
            raise SystemExit("fresh plugin probe: human worker display name still contains an identity suffix")
        if not re.fullmatch(r"explorer_[a-z0-9_]+_01_[0-9a-f]{8}", str(dispatch["arguments"].get("task_name") or "")):
            raise SystemExit("fresh plugin probe: native worker task name is not unique and host-safe")
        confirmed = mcp_tool(
            launcher,
            entrypoint,
            environment,
            workspace,
            "manage_orchestration",
            {"task_ref": created["task_ref"], "intent": "inspect"},
        )
        task = cortex.load_task_definition(expected_task)
        state = cortex.load_task_state_for_artifact(expected_task)
        loaded = cortex.db_load_task(ledger, str(state["task_id"]))
        plan = loaded[2] if loaded is not None else None
        files = [path.relative_to(ledger).as_posix() for path in ledger.rglob("*") if path.is_file()]
        banned = ("v3-operations", "active-tasks", "status-receipts", "reports/grants", "metrics.json", "task.json", "current.json", "task-index.json", "host-sessions.json")
        retired_snapshot = any(path.endswith("-snapshot.json") for path in files)
        retired_handoff_manifest = any(
            "/handoffs/" in f"/{path}" and path.endswith("-manifest.json")
            for path in files
        )
        if (
            not confirmed.get("ok")
            or task.get("project_root") != str(workspace)
            or task.get("schema") != "cortex/v8"
            or state.get("schema") != "cortex/v8"
            or not ledger.is_relative_to(host_store / "projects")
            or not (ledger / "cortex.db").is_file()
            or (workspace / ".codex/cortex/cortex.db").exists()
            or "current_gate" in state
            or not isinstance(plan, dict)
            or plan.get("schema") != "cortex/orchestration-plan/v1"
            or any(any(marker in path for marker in banned) for path in files)
            or retired_snapshot
            or retired_handoff_manifest
            or any(path.startswith("operations/v3-") for path in files)
        ):
            raise SystemExit("fresh plugin probe: created Cortex task was not immediately confirmed in the selected workspace")
        print(json.dumps({"status": "PASS", "plugin": record.get("pluginId"), "version": record.get("version"), "mcp": "cortex", "isolation": "temporary HOME and CODEX_HOME"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
