#!/usr/bin/env python3
"""Validate this repository's local Codex marketplace contract."""
from __future__ import annotations

import argparse
import json
import os
import stat
import tomllib
from pathlib import Path


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NAME = "cortex"
EXPECTED_PLUGIN = "cortex"
EXPECTED_SKILLS = {"adaptive-pipeline", "content-safety", "context-compaction", "orchestrator", "cortex-control", "documentation-sync", "find-skills", "knowledge-harvest", "output-validation", "token-monitoring"}


def fail(message: str) -> None:
    raise SystemExit(f"marketplace validation failed: {message}")


def regular_file(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        fail(f"{label} is missing or unreadable: {exc}")
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        fail(f"{label} must be a regular file, not a symlink or special file")


def regular_directory(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        fail(f"{label} is missing or unreadable: {exc}")
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        fail(f"{label} must be a directory, not a symlink or special file")


def reject_symlinks(root: Path, label: str) -> None:
    for directory, names, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in [*names, *files]:
            path = base / name
            if path.is_symlink():
                fail(f"{label} must not contain symlinks: {path.relative_to(root)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="repository tree to validate")
    return parser.parse_args()


def main() -> int:
    root = parse_args().root.resolve(strict=False)
    regular_directory(root, "repository root")
    marketplace = root / ".agents/plugins/marketplace.json"
    plugin = root / "plugins/cortex"
    retired_marketplace = root / "marketplace"
    regular_file(marketplace, "root marketplace manifest")
    regular_directory(plugin, "canonical plugin source")
    if retired_marketplace.exists() or retired_marketplace.is_symlink():
        fail("retired nested marketplace artifacts must not ship")
    reject_symlinks(root / ".agents", "root marketplace metadata")
    reject_symlinks(plugin, "canonical plugin source")
    try:
        payload = json.loads(marketplace.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(str(exc))
    if not isinstance(payload, dict) or payload.get("name") != EXPECTED_NAME:
        fail(f"marketplace name must be {EXPECTED_NAME!r}")
    interface = payload.get("interface")
    if not isinstance(interface, dict) or not isinstance(interface.get("displayName"), str) or not interface["displayName"].strip():
        fail("interface.displayName must be a non-empty string")
    plugins = payload.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1:
        fail("plugins must contain exactly the repository-managed plugin")
    expected = {
        "name": EXPECTED_PLUGIN,
        "source": {"source": "local", "path": "./plugins/cortex"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "DeveloperTools",
    }
    if plugins[0] != expected:
        fail("plugin entry does not match the repo-source installation policy")
    if (plugin / ".codex").exists():
        fail("plugin source must not contain plugin-local .codex runtime state")
    try:
        manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        mcp = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
        json.loads((plugin / "hooks/hooks.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid plugin companion file: {exc}")
    version = manifest.get("version")
    base_version = version.split("+", 1)[0] if isinstance(version, str) else ""
    if manifest.get("name") != EXPECTED_PLUGIN or base_version != "2.0.0":
        fail("plugin manifest must identify cortex at release version 2.0.0")
    if manifest.get("skills") != "./skills/" or manifest.get("mcpServers") != "./.mcp.json":
        fail("plugin manifest must declare its skills and MCP companion")
    try:
        profile_contract = json.loads((plugin / "profiles.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid profile contract: {exc}")
    if profile_contract.get("schema") != "cortex/profile-contract/v1" or len(profile_contract.get("profiles", [])) != 21:
        fail("profile contract must define exactly 21 Cortex profiles")
    shared = profile_contract.get("shared_worker_contract", {})
    required_report_fields = {"summary", "findings", "questions", "changed_files", "tests", "evidence", "uncertainty", "next_action"}
    if shared.get("report_schema") != "cortex/report/v1" or set(shared.get("required_report_fields", [])) != required_report_fields:
        fail("shared worker contract must define the complete cortex/report/v1 payload")
    EXPECTED_AGENTS = {item.get("name") for item in profile_contract["profiles"]}
    # MCP configuration supports a plugin-relative working directory.  Unlike
    # hook commands, ${PLUGIN_ROOT} is not expanded in stdio-MCP arguments by
    # the host, so it would launch a non-existent literal path.
    expected_server = {"command": "python3", "args": ["./scripts/cortex.py"], "cwd": "."}
    if mcp != {"mcpServers": {"cortex": expected_server}}:
        fail("MCP companion must expose only the cortex server")
    for skill_name in EXPECTED_SKILLS:
        skill = plugin / "skills" / skill_name / "SKILL.md"
        try:
            content = skill.read_text(encoding="utf-8")
        except OSError as exc:
            fail(f"missing skill {skill_name}: {exc}")
        if f"\nname: {skill_name}\n" not in content:
            fail(f"skill frontmatter must identify {skill_name}")
    cortex_skill = (plugin / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
    required_routes = ("| `empty` | `orchestrate` |", "| `help` | `help` |", "| `harvest` | `harvest` |", "| `harvest-refresh` | `harvest-refresh` |", "| `normal` | `normal` |")
    if not all(route in cortex_skill for route in required_routes):
        fail("Cortex skill must declare every supported route deterministically")
    required_invocation_guidance = ("Skills picker", "`$cortex:orchestrator`", "`/skills`", "not registered native slash", "Do not use the deprecated `/prompts`")
    if not all(marker in cortex_skill for marker in required_invocation_guidance):
        fail("Cortex skill must document Desktop/CLI invocation and textual shorthand")
    agent_files = sorted((plugin / "agents").glob("*.toml"))
    try:
        agent_names = {tomllib.loads(path.read_text(encoding="utf-8"))["name"] for path in agent_files}
    except (OSError, tomllib.TOMLDecodeError, KeyError) as exc:
        fail(f"invalid bundled agent profile: {exc}")
    if agent_names != EXPECTED_AGENTS or len(agent_files) != len(EXPECTED_AGENTS):
        fail("bundled agent profiles do not match the supported Cortex profile set")
    for item in profile_contract["profiles"]:
        path = plugin / "agents" / str(item.get("filename", ""))
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        if parsed.get("name") != item.get("name") or parsed.get("sandbox_mode") != item.get("sandbox"):
            fail(f"profile contract does not match {path.name}")
        prompt = str(parsed.get("developer_instructions", ""))
        prompt_lower = prompt.lower()
        if not all(marker in prompt_lower for marker in ("select this profile", "do not select", "report", "escalate")):
            fail(f"profile prompt lacks selection, exclusion, evidence, or escalation guidance: {path.name}")
        if "gpt-" in prompt or "model_reasoning_effort" in prompt:
            fail(f"profile prompt must not pin a model or effort: {path.name}")
    if (root / "agents").exists() or (root / "skills").exists():
        fail("installable agent and skill sources must exist only inside plugins/cortex")
    if (root / "agents/orchestrator.toml").exists():
        fail("retired dedicated orchestrator profile must not ship")
    print(f"marketplace validation passed: {marketplace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
