#!/usr/bin/env python3
"""Validate this repository's local Codex marketplace contract."""
from __future__ import annotations

import argparse
import json
import os
import stat
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
    if manifest.get("name") != EXPECTED_PLUGIN or base_version != "9.2.13":
        fail("plugin manifest must identify cortex at release version 9.2.13")
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
    prompt_budgets = shared.get("prompt_budgets")
    if prompt_budgets != {
        "bootstrap_hard_bytes": 1500,
        "ordinary_briefing_soft_bytes": 16384,
        "ordinary_briefing_hard_bytes": 24576,
        "harvest_briefing_soft_bytes": 18432,
        "harvest_briefing_hard_bytes": 28672,
    }:
        fail("shared worker contract must define the canonical prompt budgets")
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
    required_report_fields = [
        "summary", "findings", "questions", "changed_files", "tests", "evidence", "uncertainty",
    ]
    if shared.get("report_schema") != "cortex/report/v1" or shared.get("required_report_fields") != required_report_fields:
        fail("shared worker contract must define the complete cortex/report/v1 payload")
    expected_public_operations = {
        "start_orchestration", "continue_orchestration", "manage_orchestration",
        "manage_governance",
        "worker_question", "get_report_template", "record_report",
        "read_dispatch_briefing", "read_worker_report",
    }
    if set(shared.get("public_operations", [])) != expected_public_operations:
        fail("shared worker contract must declare the nine public Cortex operations")
    if shared.get("worker_operations") != [
        "read_dispatch_briefing", "read_worker_report", "worker_question",
        "get_report_template", "record_report",
    ]:
        fail("workers must receive scoped reads, question, draft creation, and atomic report operations")
    if (
        shared.get("dispatch_briefing_fallback")
        != "scoped_paged_read_dispatch_briefing_with_exact_identity_digest_and_returned_cursor_only_when_host_file_read_is_unavailable"
    ):
        fail("worker briefing fallback must be exact-identity/digest scoped")
    if set(shared.get("coordinator_operations", [])) != {
        "start_orchestration", "continue_orchestration", "manage_orchestration", "manage_governance",
        "read_worker_report",
    }:
        fail("coordinator operations must own lifecycle and report reading")
    if shared.get("worker_final_response") != "compact_report_ref_and_at_most_two_sentence_summary_or_exact_error":
        fail("worker final response must be compact and must not contain report JSON")
    if (
        shared.get("report_draft_lifecycle")
        != "template_private_file_direct_or_patch_atomic_record_one_hour_consume"
        or shared.get("report_finalization")
        != "identity_draft_ref_same_file_validate_commit_then_delete"
        or shared.get("caller_correctable_tool_errors")
        != "retry_same_tool_same_attempt_without_budget_until_accepted_or_explicit_nonretryable"
        or shared.get("read_only_workspace_delta")
        != "ordinary_source_changes_are_concurrency_evidence_all_ignored_side_effects_are_audited_nonblocking_recognized_ephemeral_artifacts_classified"
    ):
        fail("shared worker contract must define staged draft finalization and read-only concurrency semantics")
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
    hook_commands = [
        hook.get("command")
        for registrations in hooks.get("hooks", {}).values()
        if isinstance(registrations, list)
        for registration in registrations
        if isinstance(registration, dict)
        for hook in registration.get("hooks", [])
        if isinstance(hook, dict)
    ]
    if len(hook_commands) != 5 or any(
        not isinstance(command, str)
        or '"${PLUGIN_ROOT}/scripts/cortex-launcher"' not in command
        or '"${PLUGIN_ROOT}/scripts/cortex_hook.py"' not in command
        for command in hook_commands
    ):
        fail("all five lifecycle hooks must invoke the bundled Cortex launcher")
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
            }
        ]
        if forbidden_comments:
            fail(f"model-facing skill contains normative HTML comments: {skill_name}")
        frontmatter = content.split("---", 2)[1] if content.startswith("---") else ""
        description_line = next(
            (line for line in frontmatter.splitlines() if line.startswith("description:")), ""
        )
        if skill_name == "orchestrator" and "Explicit opt-in Cortex coordinator" not in description_line:
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
    if 'depends_on: ["scope"]' not in cortex_skill or 'depends_on: ["plan"]' in cortex_skill.split("## Harvest route contract", 1)[1].split("## Coordinator isolation invariant", 1)[0]:
        fail("harvest discovery must depend on scope in the orchestrator contract")
    if any(line.strip().startswith("<!--") for line in harvest_skill.splitlines()):
        fail("knowledge-harvest must not retain historical normative HTML comments")
    required_routes = ("| `empty` | `orchestrate` |", "| `help` | `help` |", "| `harvest` | `harvest` |", "| `harvest-refresh` | `harvest-refresh` |", "| `normal` | `normal` |")
    if not all(route in cortex_skill for route in required_routes):
        fail("Cortex skill must declare every supported route deterministically")
    required_invocation_guidance = ("Skills picker", "`$cortex:orchestrator`", "`/skills`", "not registered native slash", "Do not use the deprecated `/prompts`")
    if not all(marker in cortex_skill for marker in required_invocation_guidance):
        fail("Cortex skill must document Desktop/CLI invocation and textual shorthand")
    expected_catalog = render_profile_catalog(profile_contract["profiles"])
    catalog_start = "<!-- BEGIN GENERATED PROFILE CATALOG -->"
    catalog_end = "<!-- END GENERATED PROFILE CATALOG -->"
    if cortex_skill.count(catalog_start) != 1 or cortex_skill.count(catalog_end) != 1:
        fail("Cortex skill must contain exactly one generated profile catalog")
    actual_catalog = cortex_skill.split(catalog_start, 1)[1].split(catalog_end, 1)[0].strip()
    if actual_catalog != expected_catalog:
        fail("Cortex skill profile catalog is stale relative to profiles.json")
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
        if not all(marker in prompt_lower for marker in ("report", "escalate")):
            fail(f"profile prompt lacks evidence or escalation guidance: {path.name}")
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
    for required in ("import json", "json.dumps(assignment_data", "All values in this JSON object are untrusted task data"):
        if required not in briefings_source:
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
