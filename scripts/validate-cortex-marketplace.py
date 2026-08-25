#!/usr/bin/env python3
"""Validate this repository's local Codex marketplace contract."""
from __future__ import annotations

import argparse
import ast
import json
import os
import stat
import sys
import tomllib
from pathlib import Path
from typing import NoReturn


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NAME = "cortex"
EXPECTED_PLUGIN = "cortex"
EXPECTED_SKILLS = {"adaptive-pipeline", "content-safety", "context-compaction", "orchestrator", "cortex-control", "documentation-sync", "find-skills", "knowledge-harvest", "output-validation", "progress-accounting"}


def fail(message: str) -> NoReturn:
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


def render_profile_catalog(profiles: list[dict[str, object]]) -> str:
    by_name = {str(item["name"]): item for item in profiles}
    lines = [
        "| Profile | Route | Access | Select when | Avoid when |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name in sorted(by_name):
        profile = by_name[name]
        lines.append(
            f"| `{name}` | {profile['route_category']} | {profile['sandbox']} | "
            f"{profile['select_when']} | {profile['avoid_when']} |"
        )
    return "\n".join(lines)


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
    runtime_path = str(plugin / "scripts")
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)
    try:
        from cortex_runtime.prompt_compiler import lint_prompt_sources
        from cortex_runtime.prompt_eval import run_prompt_evals
        prompt_issues = lint_prompt_sources(root)
        if prompt_issues:
            fail("prompt contract lint failed: " + "; ".join(prompt_issues))
        run_prompt_evals(fixtures_path=plugin / "prompt-evals" / "fixtures.json")
    except (AssertionError, OSError, RuntimeError, ValueError) as exc:
        fail("prompt contract validation failed: " + str(exc))
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
        hooks = json.loads((plugin / "hooks/hooks.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid plugin companion file: {exc}")
    version = manifest.get("version")
    base_version = version.split("+", 1)[0] if isinstance(version, str) else ""
    if manifest.get("name") != EXPECTED_PLUGIN or base_version != "11.0.1":
        fail("plugin manifest must identify cortex at release version 11.0.1")
    if manifest.get("skills") != "./skills/" or manifest.get("mcpServers") != "./.mcp.json":
        fail("plugin manifest must declare its skills and MCP companion")
    launcher = plugin / "scripts/cortex-launcher"
    regular_file(launcher, "Cortex launcher")
    if not launcher.stat().st_mode & 0o111:
        fail("Cortex launcher must have executable permissions")
    launcher_source = launcher.read_text(encoding="utf-8")
    if "CORTEX_PYTHON" not in launcher_source or "exec" not in launcher_source:
        fail("Cortex launcher must resolve CORTEX_PYTHON and exec the selected runtime")
    try:
        profile_contract = json.loads((plugin / "profiles.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid profile contract: {exc}")
    if profile_contract.get("schema") != "cortex/profile-contract/v1" or len(profile_contract.get("profiles", [])) != 21:
        fail("profile contract must define exactly 21 Cortex profiles")
    profile_fields = {
        "name", "filename", "sandbox", "route_category", "gates",
        "description", "select_when", "avoid_when",
    }
    for item in profile_contract["profiles"]:
        if not isinstance(item, dict) or not profile_fields.issubset(item):
            fail("every profile must define complete identity and routing metadata")
        if item.get("sandbox") not in {"read-only", "workspace-write"}:
            fail(f"invalid profile sandbox: {item.get('name')}")
        if item.get("route_category") not in {"automatic", "manual"}:
            fail(f"invalid profile route category: {item.get('name')}")
        gates = item.get("gates")
        if not isinstance(gates, list) or len(gates) != len(set(gates)):
            fail(f"invalid or duplicate profile gates: {item.get('name')}")
        if item.get("route_category") == "automatic" and not gates:
            fail(f"automatic profile must own a gate: {item.get('name')}")
        if item.get("route_category") == "manual" and gates:
            fail(f"manual profile must be implementation-selected: {item.get('name')}")
        if not all(isinstance(item.get(field), str) and item[field].strip() for field in ("description", "select_when", "avoid_when")):
            fail(f"incomplete profile routing text: {item.get('name')}")
    profile_names = {item["name"] for item in profile_contract["profiles"]}
    model_routing = profile_contract.get("model_routing")
    if not isinstance(model_routing, dict) or model_routing.get("schema") != "cortex/model-routing/v1":
        fail("profile contract must define the Cortex model-routing policy")
    if model_routing.get("configured_default_model") != "gpt-5.6-luna":
        fail("model routing must keep Luna as the configured hidden-agent default")
    if model_routing.get("max_policy") != "complex_work_or_repeated_rework":
        fail("model routing must allow automatic max for complex work and repeated unresolved rework")
    if model_routing.get("security", {}).get("model") != "gpt-5.6-sol":
        fail("security model routing must select Sol")
    if model_routing.get("explorer", {}).get("model") != "gpt-5.6-luna":
        fail("explorer model routing must select Luna")
    profile_classes = model_routing.get("profile_classes")
    if not isinstance(profile_classes, dict) or set(profile_classes) != {"efficient", "adaptive", "deep"}:
        fail("model routing must define efficient, adaptive, and deep profile classes")
    if any(
        not isinstance(members, list)
        or not members
        or not all(isinstance(name, str) for name in members)
        for members in profile_classes.values()
    ):
        fail("model profile classes must contain non-empty profile-name lists")
    classified_profiles = [name for members in profile_classes.values() for name in members]
    if (
        len(classified_profiles) != len(set(classified_profiles))
        or set(classified_profiles) != profile_names - {"explorer", "security_auditor"}
    ):
        fail("model profile classes must cover every ordinary profile exactly once")
    supported_efforts = {"low", "medium", "high", "xhigh"}
    luna_bounded_effort = model_routing.get("luna_bounded_effort_by_complexity")
    luna_efficient_effort = model_routing.get("luna_efficient_effort_by_complexity")
    terra_effort = model_routing.get("terra_effort_by_complexity")
    effort_floor_by_risk = model_routing.get("effort_floor_by_risk")
    security_effort = model_routing.get("security", {}).get("effort_by_complexity")
    explorer_effort = model_routing.get("explorer", {}).get("effort_by_risk")
    if luna_bounded_effort != {"C1": "high", "C2": "xhigh", "C3": "max"}:
        fail("bounded Luna routing must define high/xhigh/max complexity effort")
    if not isinstance(luna_efficient_effort, dict) or set(luna_efficient_effort) != {"C1", "C2", "C3"}:
        fail("efficient Luna routing must define every complexity effort floor")
    if not isinstance(terra_effort, dict) or set(terra_effort) != {"C1", "C2", "C3"}:
        fail("Terra routing must define every complexity effort floor")
    if not isinstance(effort_floor_by_risk, dict) or set(effort_floor_by_risk) != {"low", "moderate", "high", "critical"}:
        fail("model routing must define every risk effort floor")
    if not isinstance(security_effort, dict) or set(security_effort) != {"C1", "C2", "C3"}:
        fail("security routing must define every complexity effort floor")
    if not isinstance(explorer_effort, dict) or set(explorer_effort) != {"low", "moderate", "high", "critical"}:
        fail("explorer routing must define every risk effort default")
    if any(
        not set(mapping.values()).issubset(supported_efforts)
        for mapping in (luna_efficient_effort, terra_effort, effort_floor_by_risk, security_effort, explorer_effort)
    ):
        fail("non-bounded automatic model-routing effort maps must not contain unsupported or max effort")
    terra_task_kinds = model_routing.get("terra_task_kinds")
    if (
        not isinstance(terra_task_kinds, list)
        or not terra_task_kinds
        or not all(isinstance(kind, str) and kind and kind.replace("_", "").isalnum() for kind in terra_task_kinds)
        or len(terra_task_kinds) != len(set(terra_task_kinds))
    ):
        fail("model routing must define unique Terra trigger task kinds")
    expected_gates = {
        "scope", "plan", "discover", "architecture", "database_architecture", "implementation",
        "qa", "security", "performance", "accessibility", "ux", "review", "documentation", "close",
        "governance_activation", "governance_close",
    }
    gate_briefings = profile_contract.get("gate_briefings")
    if not isinstance(gate_briefings, dict) or set(gate_briefings) != expected_gates:
        fail("profile contract must define exactly one briefing for every Cortex gate")
    for gate, briefing in gate_briefings.items():
        if not isinstance(briefing, dict) or set(briefing) != {"objective", "ownership", "acceptance", "verification"}:
            fail(f"invalid gate briefing shape: {gate}")
        if not all(isinstance(briefing.get(key), str) and briefing[key].strip() for key in ("objective", "ownership")):
            fail(f"gate briefing lacks objective or ownership: {gate}")
        if not all(isinstance(briefing.get(key), list) and briefing[key] for key in ("acceptance", "verification")):
            fail(f"gate briefing lacks acceptance or verification: {gate}")
    shared = profile_contract.get("shared_worker_contract", {})
    retry_policy = shared.get("retry_policy")
    if retry_policy != {
        "pipeline_rework": "unbounded_while_acceptance_or_findings_require_correction",
        "terra_after_failed_attempts": 2,
        "effort_by_prior_failures": {"1": "high", "2": "xhigh", "3+": "max"},
    }:
        fail("shared worker contract must define unbounded rework with model/effort escalation")
    prompt_compaction_guidance = shared.get("prompt_compaction_guidance")
    if prompt_compaction_guidance != {
        "bootstrap_target_bytes": 1024,
        "ordinary_briefing_target_bytes": 12288,
        "harvest_briefing_target_bytes": 18432,
        "semantics": "prompt_only_advisory; never a backend admission, storage, truncation, or rejection rule",
    }:
        fail("shared worker contract must define the canonical prompt compaction guidance")
    mode_overlays = profile_contract.get("mode_overlays")
    expected_harvest_profiles = {
        "planner", "explorer", "architect", "technical_writer", "code_reviewer", "build_verification",
    }
    if (
        not isinstance(mode_overlays, dict)
        or set(mode_overlays) != {"harvest"}
        or set(mode_overlays["harvest"]) != expected_harvest_profiles
        or not all(isinstance(value, str) and value.strip() for value in mode_overlays["harvest"].values())
    ):
        fail("harvest guidance must live in one conditional mode overlay")
    if (
        shared.get("repository_intelligence")
        != "codebase_memory_first_when_available_then_source_confirmed_with_bounded_fallback"
        or shared.get("codebase_memory_project_resolution")
        != "derive_canonical_path_key_then_single_exact_root_list_fallback"
        or shared.get("codebase_memory_project_key_algorithm")
        != "cbm_project_name_from_path_safe_ascii_utf8hex_fnv1a200"
        or set(shared.get("codebase_memory_refresh_profiles", []))
        != {"planner", "explorer", "architect", "database_architect"}
        or shared.get("codebase_memory_fallback")
        != "one_bounded_attempt_then_repository_native_tools_without_looping"
    ):
        fail("shared worker contract must define bounded Codebase Memory discovery and fallback")
    EXPECTED_AGENTS = {item.get("name") for item in profile_contract["profiles"]}
    execution_contracts = profile_contract.get("profile_execution_contracts")
    if not isinstance(execution_contracts, dict) or set(execution_contracts) != EXPECTED_AGENTS:
        fail("profile execution contracts must cover every supported agent exactly once")
    for name, execution in execution_contracts.items():
        if not isinstance(execution, dict) or set(execution) != {"inputs", "project_artifacts", "completion"}:
            fail(f"profile execution contract has an invalid shape: {name}")
        if not all(isinstance(value, str) and len(value.split()) >= 6 for value in execution.values()):
            fail(f"profile execution contract is too shallow: {name}")
    # MCP configuration supports a plugin-relative working directory.  Unlike
    # hook commands, ${PLUGIN_ROOT} is not expanded in stdio-MCP arguments by
    # the host, so the executable must remain plugin-relative.
    expected_server = {"command": "./scripts/cortex-launcher", "args": ["./scripts/cortex.py"], "cwd": "."}
    if mcp != {"mcpServers": {"cortex": expected_server}}:
        fail("MCP companion must expose only the cortex server")
    try:
        import cortex as cortex_server
        from cortex_runtime.mcp_api import public_tools_for_audience
        from render_cortex_tool_catalog import expected_skill_text
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        fail(f"public MCP registry could not be loaded: {exc}")
    contracts = getattr(cortex_server, "PUBLIC_CONTRACTS", None)
    schemas = getattr(cortex_server, "PUBLIC_SCHEMA_REGISTRY", None)
    tools = getattr(cortex_server, "PUBLIC_TOOLS", None)
    if not isinstance(contracts, dict) or not contracts:
        fail("the canonical action-specific public contract registry is missing")
    if not isinstance(schemas, dict) or not isinstance(tools, dict):
        fail("the runtime public schema/tool registries are missing")
    start_contract = contracts.get("start_orchestration")
    start_schema = start_contract.get("inputSchema") if isinstance(start_contract, dict) else None
    start_properties = start_schema.get("properties") if isinstance(start_schema, dict) else None
    project_root_schema = start_properties.get("project_root") if isinstance(start_properties, dict) else None
    waves_schema = start_properties.get("waves") if isinstance(start_properties, dict) else None
    wave_schema = waves_schema.get("items") if isinstance(waves_schema, dict) else None
    wave_properties = wave_schema.get("properties") if isinstance(wave_schema, dict) else None
    workers_schema = wave_properties.get("workers") if isinstance(wave_properties, dict) else None
    worker_schema = workers_schema.get("items") if isinstance(workers_schema, dict) else None
    worker_properties = worker_schema.get("properties") if isinstance(worker_schema, dict) else None
    allowed_paths_schema = worker_properties.get("allowed_paths") if isinstance(worker_properties, dict) else None
    allowed_path_item = allowed_paths_schema.get("items") if isinstance(allowed_paths_schema, dict) else None
    if (
        not isinstance(project_root_schema, dict)
        or project_root_schema.get("description") != "An absolute path to the project root."
        or not isinstance(allowed_paths_schema, dict)
        or allowed_paths_schema.get("description")
        != "Every entry is strictly project-relative to project_root, never absolute."
        or not isinstance(allowed_path_item, dict)
        or allowed_path_item.get("description")
        != "A project-relative path such as desktop-v11-smoke.txt; never an absolute path."
    ):
        fail("start_orchestration path guidance must be owned by its canonical inputSchema")
    path_guidance_markers = (
        "an absolute path to the project root",
        "strictly project-relative to project_root",
        "desktop-v11-smoke.txt",
    )
    if any(
        marker in str(start_contract.get("description") or "").lower()
        for marker in path_guidance_markers
    ):
        fail("start_orchestration path guidance must not be duplicated in its tool description")
    model_facing_sources = (
        *sorted((plugin / "skills").rglob("SKILL.md")),
        *sorted((plugin / "agents").glob("*.toml")),
        plugin / "profiles.json",
        plugin / "scripts/cortex_runtime/briefings.py",
        plugin / "scripts/cortex_runtime/prompt_compiler.py",
    )
    for source in model_facing_sources:
        source_text = source.read_text(encoding="utf-8").lower()
        if any(marker in source_text for marker in path_guidance_markers):
            fail(f"canonical inputSchema path guidance is duplicated in model-facing source: {source.name}")
    canonical_schemas: dict[str, object] = {}
    split_base_operations: set[str] = set()
    for name, contract in contracts.items():
        if not isinstance(name, str) or not name or not isinstance(contract, dict):
            fail("every public contract must have a non-empty name and object definition")
        if set(contract) != {
            "description", "inputSchema", "base_operation", "injected_arguments",
            "audience", "execution",
        }:
            fail(f"public contract has a non-canonical shape: {name}")
        description = contract.get("description")
        schema = contract.get("inputSchema")
        base_operation = contract.get("base_operation")
        injected = contract.get("injected_arguments")
        audience = contract.get("audience")
        execution = contract.get("execution")
        if (
            not isinstance(description, str)
            or not description.strip()
            or not isinstance(schema, dict)
            or not isinstance(base_operation, str)
            or not base_operation
            or not isinstance(injected, dict)
            or audience not in {"coordinator", "worker"}
            or not isinstance(execution, dict)
            or set(execution) != {"prerequisite", "terminal"}
            or not isinstance(execution.get("prerequisite"), str)
            or not isinstance(execution.get("terminal"), bool)
        ):
            fail(f"public contract metadata is incomplete: {name}")
        properties = schema.get("properties")
        required = schema.get("required")
        if (
            schema.get("type") != "object"
            or schema.get("additionalProperties") is not False
            or not isinstance(properties, dict)
            or not isinstance(required, list)
            or set(injected) & set(properties)
        ):
            fail(f"public contract does not keep injected routing outside its closed input schema: {name}")
        if audience == "worker":
            dispatch_schema = properties.get("dispatch_ref")
            if (
                "dispatch_ref" not in required
                or not isinstance(dispatch_schema, dict)
                or dispatch_schema.get("type") != "string"
                or dispatch_schema.get("format") != "cortex-dispatch-ref"
                or "dispatch_ref" in description
            ):
                fail(f"worker dispatch authority must live only in the required inputSchema: {name}")
        stack: list[object] = [schema]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if any(key in item for key in ("oneOf", "anyOf", "allOf", "not")):
                    fail(f"public contract contains a schema combinator: {name}")
                if item.get("type") == "object" and item.get("additionalProperties") is not False:
                    fail(f"public contract contains an open object schema: {name}")
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
        canonical_schemas[name] = schema
        if injected:
            split_base_operations.add(base_operation)
    if schemas != canonical_schemas:
        fail("runtime public schema registry diverges from canonical public contracts")
    if set(tools) != set(contracts):
        fail("runtime public tool registry diverges from canonical public contracts")
    for name, registration in tools.items():
        if (
            not isinstance(registration, dict)
            or set(registration) != set(contracts[name]) | {"handler"}
            or not callable(registration.get("handler"))
            or {key: value for key, value in registration.items() if key != "handler"} != contracts[name]
            or registration.get("inputSchema") is not contracts[name].get("inputSchema")
        ):
            fail(f"runtime public tool registration diverges from its canonical contract: {name}")
    if split_base_operations & set(tools):
        fail("retired multiplexed public aliases remain registered")
    coordinator_tools = public_tools_for_audience(tools, "coordinator")
    worker_tools = public_tools_for_audience(tools, "worker")
    default_tools = public_tools_for_audience(tools, "default")
    expected_coordinator = {name for name, contract in contracts.items() if contract.get("audience") == "coordinator"}
    expected_worker = {name for name, contract in contracts.items() if contract.get("audience") == "worker"}
    if (
        not coordinator_tools
        or not worker_tools
        or set(coordinator_tools) & set(worker_tools)
        or set(coordinator_tools) != expected_coordinator
        or set(worker_tools) != expected_worker
        or set(default_tools) != set(coordinator_tools) | set(worker_tools)
        or set(default_tools) != set(contracts)
    ):
        fail("public tool audience ownership is not a disjoint complete projection")
    control_skill_path = plugin / "skills/cortex-control/SKILL.md"
    control_skill_text = control_skill_path.read_text(encoding="utf-8")
    try:
        expected_control_skill = expected_skill_text(control_skill_text, contracts)
    except ValueError as exc:
        fail(f"Cortex control tool catalog is invalid: {exc}")
    if control_skill_text != expected_control_skill:
        fail("Cortex control tool catalog differs from the canonical public registry")
    expected_hook_events = {"SessionStart", "SubagentStart", "SubagentStop", "Stop", "PostToolUse"}
    hook_registry = hooks.get("hooks", {})
    if not isinstance(hook_registry, dict) or set(hook_registry) != expected_hook_events:
        fail("hook registry must contain only the five v11 telemetry lifecycle events")
    hook_commands = [
        hook.get("command")
        for registrations in hook_registry.values()
        if isinstance(registrations, list)
        for registration in registrations
        if isinstance(registration, dict)
        for hook in registration.get("hooks", [])
        if isinstance(hook, dict)
    ]
    if len(hook_commands) != len(expected_hook_events) or any(
        not isinstance(command, str)
        or '"${PLUGIN_ROOT}/scripts/cortex-launcher"' not in command
        or '"${PLUGIN_ROOT}/scripts/cortex_hook.py"' not in command
        for command in hook_commands
    ):
        fail("all five v11 telemetry hooks must invoke the bundled Cortex launcher")
    for skill_name in EXPECTED_SKILLS:
        skill = plugin / "skills" / skill_name / "SKILL.md"
        try:
            content = skill.read_text(encoding="utf-8")
        except OSError as exc:
            fail(f"missing skill {skill_name}: {exc}")
        if f"\nname: {skill_name}\n" not in content:
            fail(f"skill frontmatter must identify {skill_name}")
        forbidden_comments = [
            line for line in content.splitlines()
            if line.strip().startswith("<!--")
            and line.strip() not in {
                "<!-- BEGIN GENERATED PROFILE CATALOG -->",
                "<!-- END GENERATED PROFILE CATALOG -->",
                "<!-- BEGIN GENERATED CORTEX TOOL CATALOG -->",
                "<!-- END GENERATED CORTEX TOOL CATALOG -->",
            }
        ]
        if forbidden_comments:
            fail(f"model-facing skill contains normative HTML comments: {skill_name}")
        frontmatter = content.split("---", 2)[1] if content.startswith("---") else ""
        description_line = next(
            (line for line in frontmatter.splitlines() if line.startswith("description:")), ""
        )
        if skill_name == "orchestrator" and "Explicit opt-in Cortex v11 coordinator" not in description_line:
            fail("orchestrator frontmatter must make explicit opt-in authoritative")
        if skill_name in {
            "cortex-control", "adaptive-pipeline", "context-compaction", "documentation-sync",
            "output-validation", "knowledge-harvest",
        } and "Internal Cortex" not in description_line:
            fail(f"internal Cortex overlay must be marked internal in frontmatter: {skill_name}")
        if skill_name == "find-skills" and "Explicit skill-discovery helper" not in description_line:
            fail("find-skills must require explicit skill-discovery intent")
    cortex_skill = (plugin / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
    harvest_skill = (plugin / "skills/knowledge-harvest/SKILL.md").read_text(encoding="utf-8")
    if not all(marker in cortex_skill for marker in ("`harvest`", "`harvest-refresh`")):
        fail("orchestrator contract must expose both knowledge-harvest routes")
    if any(line.strip().startswith("<!--") for line in harvest_skill.splitlines()):
        fail("knowledge-harvest must not retain historical normative HTML comments")
    required_routes = (
        "Select `empty` to start an explicitly activated task",
        "guidance, `harvest` or `harvest-refresh`",
        "or `normal` to leave the route",
    )
    if not all(route in cortex_skill for route in required_routes):
        fail("Cortex skill must declare every supported route deterministically")
    required_invocation_guidance = (
        "Codex Desktop", "`$cortex:orchestrator`", "`/skills`",
        "`/cortex` is not a native slash command",
    )
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
        if parsed.get("name") != item.get("name") or parsed.get("sandbox_mode") != item.get("sandbox") or parsed.get("description") != item.get("description"):
            fail(f"profile contract does not match {path.name}")
        prompt = str(parsed.get("developer_instructions", ""))
        prompt_lower = prompt.lower()
        if "select this profile" in prompt_lower or "do not select" in prompt_lower:
            fail(f"selected worker prompt must not contain coordinator routing guidance: {path.name}")
        if "escalate" not in prompt_lower:
            fail(f"profile prompt lacks escalation guidance: {path.name}")
        if not all(marker in prompt_lower for marker in ("role and mission:", "operating workflow:", "quality bar:")):
            fail(f"profile prompt lacks the professional playbook structure: {path.name}")
        if "gpt-" in prompt or "model_reasoning_effort" in prompt:
            fail(f"profile prompt must not pin a model or effort: {path.name}")
        if "knowledge-harvest specialization:" in prompt_lower:
            fail(f"harvest guidance must not load in an ordinary profile prompt: {path.name}")
    prompt_paragraphs: dict[str, str] = {}
    for path in agent_files:
        prompt = str(tomllib.loads(path.read_text(encoding="utf-8")).get("developer_instructions", ""))
        for paragraph in prompt.split("\n\n"):
            normalized = " ".join(paragraph.lower().split())
            if len(normalized) < 200:
                continue
            prior = prompt_paragraphs.get(normalized)
            if prior:
                fail(f"duplicate normative profile paragraph in {prior} and {path.name}")
            prompt_paragraphs[normalized] = path.name
    briefings_source = (plugin / "scripts/cortex_runtime/briefings.py").read_text(encoding="utf-8")
    prompt_compiler_source = (plugin / "scripts/cortex_runtime/prompt_compiler.py").read_text(encoding="utf-8")
    if "from cortex_runtime.prompt_compiler import compile_v3_briefing" not in briefings_source:
        fail("worker assignment data must be rendered through the JSON encoder")
    try:
        briefings_tree = ast.parse(briefings_source, filename="briefings.py")
    except SyntaxError as exc:
        fail(f"briefings.py is not valid Python: {exc}")
    host_prompt = next(
        (
            node for node in ast.walk(briefings_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "host_spawn_prompt"
        ),
        None,
    )
    compiler_calls = [
        node for node in ast.walk(host_prompt) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "compile_v3_briefing"
    ] if host_prompt is not None else []
    if not any(
        any(
            keyword.arg == "assignment"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "assignment"
            for keyword in call.keywords
        )
        for call in compiler_calls
    ):
        fail("host_spawn_prompt must pass the encoded assignment object to compile_v3_briefing")
    if "assignment_json_block(assignment)" not in prompt_compiler_source or "json.dumps(assignment" not in prompt_compiler_source:
        fail("worker assignment data must be rendered through the JSON encoder")
    for forbidden in (
        "Exact user-authored request (authoritative intent boundary):",
        "Model route and reasoning effort:",
        "Attempt baseline ref:",
        "def prompt_list(",
    ):
        if forbidden in briefings_source:
            fail(f"briefing compiler retains forbidden policy/data interpolation: {forbidden}")
    routing = profile_contract.get("implementation_routing")
    if not isinstance(routing, dict) or routing.get("fallback") != "general" or not isinstance(routing.get("rules"), list):
        fail("implementation routing must define a general fallback and ordered specialist rules")
    rule_profiles = [item.get("profile") for item in routing["rules"] if isinstance(item, dict)]
    expected_writers = {"backend_dev", "data_engineer", "debugger", "devops_engineer", "frontend_dev", "fullstack_dev", "mobile_dev", "refactorer"}
    if set(rule_profiles) != expected_writers or len(rule_profiles) != len(set(rule_profiles)):
        fail("implementation routing must cover every specialist writer exactly once")
    if (root / "agents").exists() or (root / "skills").exists():
        fail("installable agent and skill sources must exist only inside plugins/cortex")
    if (root / "agents/orchestrator.toml").exists():
        fail("retired dedicated orchestrator profile must not ship")
    print(f"marketplace validation passed: {marketplace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
