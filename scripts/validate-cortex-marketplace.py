#!/usr/bin/env python3
"""Validate this repository's local Codex marketplace contract."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
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
    support_path = str(root / "scripts")
    if support_path not in sys.path:
        sys.path.insert(0, support_path)
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
    if profile_contract.get("schema") != "cortex/profile-contract/v1" or len(profile_contract.get("profiles", [])) != 22:
        fail("profile contract must define exactly 22 current Cortex profiles")
    operation_kinds = profile_contract.get("operation_kinds")
    if (
        not isinstance(operation_kinds, dict)
        or list(operation_kinds) != ["inspect", "modify", "verify", "close"]
        or not all(isinstance(value, str) and value.strip() for value in operation_kinds.values())
    ):
        fail("profile contract must define the canonical operation kinds once")
    profile_fields = {
        "name", "filename", "sandbox", "operation_kinds", "route_category", "gates",
        "description", "select_when", "avoid_when",
    }
    reliability_fallback_owners = {operation: [] for operation in operation_kinds}
    for item in profile_contract["profiles"]:
        if not isinstance(item, dict) or not profile_fields.issubset(item):
            fail("every profile must define complete identity and routing metadata")
        if item.get("sandbox") not in {"read-only", "workspace-write"}:
            fail(f"invalid profile sandbox: {item.get('name')}")
        profile_operations = item.get("operation_kinds")
        if (
            not isinstance(profile_operations, list)
            or not profile_operations
            or len(profile_operations) != len(set(profile_operations))
            or not set(profile_operations).issubset(operation_kinds)
            or "inspect" not in profile_operations
        ):
            fail(f"invalid profile operation kinds: {item.get('name')}")
        if ("modify" in profile_operations) != (item.get("sandbox") == "workspace-write"):
            fail(f"profile modify capability and sandbox disagree: {item.get('name')}")
        capability_family = item.get("capability_family")
        if capability_family is not None and (
            not isinstance(capability_family, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", capability_family)
        ):
            fail(f"invalid profile capability family: {item.get('name')}")
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
        fallback_operations = item.get("reliability_fallback_for", [])
        if (
            not isinstance(fallback_operations, list)
            or len(fallback_operations) != len(set(fallback_operations))
            or not set(fallback_operations).issubset(operation_kinds)
            or not set(fallback_operations).issubset(set(profile_operations))
        ):
            fail(f"invalid reliability fallback operations: {item.get('name')}")
        for operation in fallback_operations:
            reliability_fallback_owners[operation].append(str(item.get("name") or ""))
    if any(len(owners) != 1 for owners in reliability_fallback_owners.values()):
        fail("each canonical operation must have exactly one reliability fallback profile")
    profile_names = {item["name"] for item in profile_contract["profiles"]}
    model_routing = profile_contract.get("model_routing")
    if not isinstance(model_routing, dict) or model_routing.get("schema") != "cortex/model-routing/v1":
        fail("profile contract must define the Cortex model-routing policy")
    if model_routing.get("configured_default_model") != "gpt-5.6-luna":
        fail("model routing must retain Luna as a documented capability, not a worker default")
    capabilities = model_routing.get("model_capabilities")
    expected_capabilities = [
        {"model": "gpt-5.6-luna", "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"]},
        {"model": "gpt-5.6-terra", "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"]},
        {"model": "gpt-5.6-sol", "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"]},
    ]
    if capabilities != expected_capabilities:
        fail("model routing must publish the exact native model/effort capability registry")
    supported_models = [item["model"] for item in expected_capabilities]
    selection_policy = model_routing.get("selection_policy")
    if not isinstance(selection_policy, dict) or set(selection_policy) != {"governance_scope", "principle", "routes"}:
        fail("model routing must define one canonical per-worker selection policy")
    if not all(
        isinstance(selection_policy.get(field), str) and selection_policy[field].strip()
        for field in ("governance_scope", "principle")
    ):
        fail("model routing governance scope and selection principle must be explicit")
    routes = selection_policy.get("routes")
    if not isinstance(routes, list) or [route.get("model") for route in routes if isinstance(route, dict)] != supported_models:
        fail("model routing routes must cover every supported model in canonical order")
    if any(
        not isinstance(route, dict)
        or set(route) != {"model", "recommended_effort", "choose_for"}
        or not isinstance(route.get("choose_for"), str)
        or not route["choose_for"].strip()
        for route in routes
    ):
        fail("model routing route guidance is incomplete")
    runtime_path = str(plugin / "scripts")
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)
    try:
        from cortex_runtime.model_routing import model_effort_registry

        model_efforts = model_effort_registry(model_routing)
    except (ImportError, ValueError) as exc:
        fail(f"canonical model/effort registry is invalid: {exc}")
    if list(model_efforts) != supported_models:
        fail("runtime model/effort registry must preserve canonical model order")
    if model_efforts != {
        item["model"]: tuple(item["reasoning_efforts"])
        for item in expected_capabilities
    }:
        fail("runtime model/effort registry must exactly match native capabilities")
    if routes[0].get("model") != "gpt-5.6-luna" or "Default" not in routes[0].get("choose_for", ""):
        fail("Luna must remain the default worker recommendation")
    if "complex" not in routes[1].get("choose_for", "").lower() or "security" not in routes[1].get("choose_for", "").lower():
        fail("Terra recommendation must be limited to complex non-security work")
    if routes[2].get("model") != "gpt-5.6-sol" or "security" not in routes[2].get("choose_for", "").lower():
        fail("Sol recommendation must be security-only")
    non_security_recommendations = " ".join(
        str(route.get("choose_for") or "") for route in routes[:2]
    ).lower()
    if "sol" in non_security_recommendations:
        fail("non-security recommendations must not select Sol")
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
    if "retry_policy" in shared:
        fail("legacy shared retry policy must be absent; exact-occurrence recovery is compiler-owned")
    if (
        "refresh_worker_context" not in set(shared.get("worker_operations", []))
        or "refresh_worker_context" not in set(shared.get("recovery_operations", []))
        or "refresh_worker_context" in set(shared.get("normal_flow", []))
        or "refresh_worker_context" in set(shared.get("coordinator_operations", []))
    ):
        fail("refresh_worker_context must remain an established-worker recovery-only operation")
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
        from cortex_runtime.verification_contract import WORKER_VERIFICATION_KINDS
        from cortex_runtime.v11_responses import (
            FLAT_PUBLIC_RESPONSE_SCHEMA,
            NATIVE_DISPATCH_SCHEMA,
            NATIVE_DISPATCH_WAIT_INSTRUCTION,
            SAME_CHILD_WAIT_INSTRUCTION,
            WAIT_LOOP_INSTRUCTION,
            WAIT_POLICY_REPEAT_UNTIL_TERMINAL,
        )
        from render_cortex_tool_catalog import (
            expected_orchestrator_skill_text,
            expected_profile_capability_skill_text,
            expected_skill_text,
        )
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        fail(f"public MCP registry could not be loaded: {exc}")
    contracts = getattr(cortex_server, "PUBLIC_CONTRACTS", None)
    schemas = getattr(cortex_server, "PUBLIC_SCHEMA_REGISTRY", None)
    tools = getattr(cortex_server, "PUBLIC_TOOLS", None)
    if not isinstance(contracts, dict) or not contracts:
        fail("the canonical action-specific public contract registry is missing")
    if not isinstance(schemas, dict) or not isinstance(tools, dict):
        fail("the runtime public schema/tool registries are missing")
    native_dispatch_properties = NATIVE_DISPATCH_SCHEMA.get("properties", {})
    native_argument_properties = (
        native_dispatch_properties.get("arguments", {}).get("properties", {})
        if isinstance(native_dispatch_properties, dict) else {}
    )
    resolution_fields = {"requested_profile", "resolved_profile", "resolution_reason"}
    flat_response_properties = FLAT_PUBLIC_RESPONSE_SCHEMA.get("properties", {})
    if (
        not resolution_fields.issubset(native_dispatch_properties)
        or resolution_fields.intersection(native_argument_properties)
        or not isinstance(flat_response_properties.get("compiled_plan"), dict)
        or flat_response_properties["compiled_plan"].get("type") != "string"
        or set((flat_response_properties.get("next_native_action") or {}).get("enum", []))
        != {"wait_agent", "read_worker_wave"}
        or (flat_response_properties.get("read_worker_wave_allowed") or {}).get("type") != "boolean"
        or (flat_response_properties.get("wait_policy") or {}).get("enum")
        != [WAIT_POLICY_REPEAT_UNTIL_TERMINAL]
    ):
        fail("compiled plan, profile normalization, and native transition fields must remain public and flat")
    audiences = tuple(contract.get("audience") for contract in contracts.values())
    if (
        any(audience not in {"coordinator", "worker"} for audience in audiences)
        or set(audiences) != {"coordinator", "worker"}
    ):
        fail("the canonical inventory must contain coordinator and worker tools only")
    start_contract = contracts.get("start_orchestration")
    start_schema = start_contract.get("inputSchema") if isinstance(start_contract, dict) else None
    start_properties = start_schema.get("properties") if isinstance(start_schema, dict) else None
    waves_schema = start_properties.get("waves") if isinstance(start_properties, dict) else None
    wave_schema = waves_schema.get("items") if isinstance(waves_schema, dict) else None
    wave_properties = wave_schema.get("properties") if isinstance(wave_schema, dict) else None
    workers_schema = wave_properties.get("workers") if isinstance(wave_properties, dict) else None
    worker_schema = workers_schema.get("items") if isinstance(workers_schema, dict) else None
    worker_properties = worker_schema.get("properties") if isinstance(worker_schema, dict) else None
    if isinstance(start_properties, dict) and "project_root" in start_properties:
        fail("start_orchestration must not expose the host-owned project_root field")
    if isinstance(worker_properties, dict) and "allowed_paths" in worker_properties:
        fail("start_orchestration must not expose the removed allowed_paths worker field")
    if (
        not isinstance(wave_properties, dict)
        or set(wave_properties) != {"phase_kind", "workers"}
        or set(wave_schema.get("required", [])) != {"phase_kind", "workers"}
        or "phase" in wave_properties
    ):
        fail("wave contract must expose only repeatable semantic phase_kind plus workers")
    if not isinstance(worker_properties, dict) or set(worker_properties) != {
        "objective", "profile", "operation_kind", "model", "reasoning_effort",
    }:
        fail("worker contract must remain flat and backend-derived outside semantic assignment fields")
    worker_required = set(worker_schema.get("required", [])) if isinstance(worker_schema, dict) else set()
    if worker_required != {"objective", "profile", "operation_kind", "model", "reasoning_effort"}:
        fail("worker contract must require every coordinator-selected semantic assignment field")
    operation_kind = worker_properties.get("operation_kind")
    if (
        not isinstance(operation_kind, dict)
        or operation_kind.get("enum") != list(operation_kinds)
    ):
        fail("worker operation_kind must publish the canonical capability enum")
    if any(
        field in worker_properties
        for field in ("depends_on", "predecessor_wave_refs", "predecessor_result_refs")
    ):
        fail("worker dependency and predecessor context must remain backend-derived")
    if any(field in worker_properties for field in ("user_model", "user_effort")):
        fail("worker contract must use the canonical model and reasoning_effort names")
    pair_guidance = str((worker_properties.get("model") or {}).get("description") or "")
    effort_guidance = str((worker_properties.get("reasoning_effort") or {}).get("description") or "")
    for model, efforts in model_efforts.items():
        expected_pair = f"{model} -> {' or '.join(efforts)}"
        if expected_pair not in pair_guidance or expected_pair not in effort_guidance:
            fail("worker tool schema must derive every exact model/effort pair from profiles")
    governance_schema = start_properties.get("governance_mode") if isinstance(start_properties, dict) else None
    if not isinstance(governance_schema, dict) or set(governance_schema.get("enum", [])) != {"auto", "required", "minimal"}:
        fail("governance_mode must advertise the current auto/required/minimal choices")
    continue_contract = contracts.get("continue_orchestration") or {}
    continue_schema = continue_contract.get("inputSchema") if isinstance(continue_contract, dict) else None
    if not isinstance(continue_schema, dict) or set(continue_schema.get("required", [])) != {"task_ref", "coordinator_ref"} or set((continue_schema.get("properties") or {})) != {"task_ref", "coordinator_ref"}:
        fail("continue_orchestration must accept only task_ref and coordinator_ref")
    for name in ("start_orchestration", "continue_orchestration"):
        if NATIVE_DISPATCH_WAIT_INSTRUCTION not in str((contracts.get(name) or {}).get("description") or ""):
            fail(f"{name} must derive its dispatch-wait-read description from the canonical registry")
    if SAME_CHILD_WAIT_INSTRUCTION not in str((contracts.get("answer_orchestration_question") or {}).get("description") or ""):
        fail("answer_orchestration_question must derive its same-child wait sequence from the canonical registry")
    for marker in (
        '"No agents completed yet"', "timeout", "empty completion set", "NONTERMINAL",
        "pendingInit", "running", "interrupted", "completed", "errored", "shutdown", "notFound",
    ):
        if marker not in WAIT_LOOP_INSTRUCTION:
            fail(f"canonical wait loop does not classify native status marker: {marker}")
    revise_contract = contracts.get("revise_future_pipeline") or {}
    revise_schema = revise_contract.get("inputSchema") if isinstance(revise_contract, dict) else None
    if not isinstance(revise_schema, dict) or "current_step" in (revise_schema.get("properties") or {}):
        fail("revise_future_pipeline must derive the current frontier")
    if "wave:N" in str(revise_contract.get("description") or ""):
        fail("revise_future_pipeline must not advertise model-authored wave identities")
    rework_contract = contracts.get("append_rework_wave") or {}
    rework_schema = rework_contract.get("inputSchema") if isinstance(rework_contract, dict) else None
    rework_properties = rework_schema.get("properties") if isinstance(rework_schema, dict) else None
    expected_rework = {
        "task_ref", "coordinator_ref", "source_result_ref", "objective", "acceptance",
        "profile", "model", "reasoning_effort",
    }
    if (
        not isinstance(rework_properties, dict)
        or set(rework_properties) != expected_rework
        or set(rework_schema.get("required", [])) != expected_rework
        or rework_schema.get("additionalProperties") is not False
        or rework_contract.get("base_operation") != "manage_orchestration"
        or (rework_contract.get("injected_arguments") or {}).get("action")
        != "append_rework_wave"
    ):
        fail("append_rework_wave must expose one flat semantic rework contract")
    event_contract = contracts.get("record_attempt_event") or {}
    event_schema = event_contract.get("inputSchema") if isinstance(event_contract, dict) else None
    event_properties = event_schema.get("properties") if isinstance(event_schema, dict) else None
    event_type = event_properties.get("event_type") if isinstance(event_properties, dict) else None
    verification_kind = (
        event_properties.get("verification_kind") if isinstance(event_properties, dict) else None
    )
    text_schema = event_properties.get("text") if isinstance(event_properties, dict) else None
    verification_guidance = str((text_schema or {}).get("description") or "")
    if (
        not isinstance(event_properties, dict)
        or set(event_properties) != {"dispatch_ref", "event_type", "verification_kind", "text"}
        or set(event_schema.get("required", [])) != {"dispatch_ref", "event_type", "text"}
        or event_schema.get("additionalProperties") is not False
        or not isinstance(event_type, dict)
        or "verification_observation" not in set(event_type.get("enum", []))
        or "verification_claimed" in set(event_type.get("enum", []))
        or not isinstance(verification_kind, dict)
        or set(verification_kind.get("enum", [])) != set(WORKER_VERIFICATION_KINDS)
        or any(marker not in verification_guidance for marker in (
            "status=passed", "passed_tests=<n>", "viewports=<n>",
            "keyboard_checks=<n>", "console_errors=0", "external_requests=0",
        ))
    ):
        fail("record_attempt_event must expose the flat canonical verification observation branch")
    follow_up_contract = contracts.get("start_follow_up") or {}
    follow_up_schema = follow_up_contract.get("inputSchema") if isinstance(follow_up_contract, dict) else None
    if not isinstance(follow_up_schema, dict) or "result_refs" in (follow_up_schema.get("properties") or {}):
        fail("start_follow_up must derive canonical source results server-side")
    wave_contract = contracts.get("read_worker_wave") or {}
    wave_schema = wave_contract.get("inputSchema") if isinstance(wave_contract, dict) else None
    if not isinstance(wave_schema, dict) or "step" in (wave_schema.get("properties") or {}) or "step" in set(wave_schema.get("required", [])):
        fail("read_worker_wave must derive the active wave server-side")
    growing_read_names = {
        name for name, contract in contracts.items()
        if any(token in str(contract.get("description", "")).lower() for token in ("page", "pagination", "continuation cursor"))
    }
    growing_read_names.update(
        name for name in contracts
        if name.startswith(("read_", "list_", "inspect_", "show_", "poll_"))
    )
    for name in sorted(growing_read_names):
        schema = contracts[name].get("inputSchema") if isinstance(contracts[name], dict) else None
        if not isinstance(schema, dict) or "cursor" not in (schema.get("properties") or {}):
            fail(f"growing public read must expose cursor pagination: {name}")
    for name, contract in contracts.items():
        if "_lane" in name or "_resource" in name:
            schema = contract.get("inputSchema") if isinstance(contract, dict) else None
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            if any(isinstance(value, dict) and value.get("type") in {"object", "array"} for value in properties.values()):
                fail(f"lane/resource public inputs must remain flat: {name}")
    path_guidance_markers = (
        "an absolute path to the project root",
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
    answer_contract = contracts.get("answer_orchestration_question")
    answer_input = answer_contract.get("inputSchema") if isinstance(answer_contract, dict) else None
    answer_properties = answer_input.get("properties") if isinstance(answer_input, dict) else None
    answer_field = answer_properties.get("answer") if isinstance(answer_properties, dict) else None
    if (
        not isinstance(answer_field, dict)
        or "answer" not in answer_input.get("required", [])
        or "answer" not in answer_properties
        or answer_field.get("description")
        != "Exact arbitrary-Unicode response bound to the durable question."
    ):
        fail("answer_orchestration_question must expose only the canonical Unicode answer field")
    retired_public_completion_tools = {
        "completion_receipt", "acknowledge_worker_completion", "v18cr1",
    }
    if retired_public_completion_tools & set(contracts):
        fail("retired public completion receipt and acknowledgement surface must be absent")
    hook_contract = json.loads((plugin / "hooks/hooks.json").read_text(encoding="utf-8"))
    observer_text = (plugin / "scripts/cortex_runtime/native_lifecycle_observer.py").read_text(encoding="utf-8")
    ledger_text = (plugin / "scripts/cortex_runtime/ledger_db.py").read_text(encoding="utf-8")
    hook_text = (plugin / "scripts/cortex_hook.py").read_text(encoding="utf-8")
    response_text = (plugin / "scripts/cortex_runtime/v11_responses.py").read_text(encoding="utf-8")
    projector_text = (plugin / "scripts/cortex_runtime/mcp_api.py").read_text(encoding="utf-8")
    if (
        "native_lifecycle_observer import observe" not in hook_text
        or "START_FIELDS" not in observer_text
        or "STOP_FIELDS" not in observer_text
        or "native_terminal_stop" not in observer_text
        or "server_recovery" not in response_text
        or "confirm_native_completion_wait" in response_text
        or '"native_completion_observation_unavailable"' not in projector_text
        or '"worker_attestation_server_state_unavailable"' not in projector_text
        or "PRIVATE_LIFECYCLE_AUDIT_RETENTION" not in ledger_text
        or "private_lifecycle_audit_digest" not in ledger_text
    ):
        fail("private native completion observer contract is incomplete")
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
            if name == "refresh_worker_context":
                cursor_schema = properties.get("cursor")
                expected_cursor_schema = {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 520,
                    "pattern": r"^c11p\.[A-Za-z0-9_-]{16,512}$",
                    "format": "cortex-page-cursor",
                }
                registration = tools.get(name)
                registered_handler = (
                    registration.get("handler") if isinstance(registration, dict) else None
                )
                if (
                    base_operation != "read_worker_context"
                    or set(properties) != {"cursor"}
                    or cursor_schema != expected_cursor_schema
                    or required
                    or injected
                    or "no identity or routing arguments" not in description
                    or "authenticated native worker" not in description
                    or "read_worker_context" in contracts
                    or "read_worker_context" in schemas
                    or "read_worker_context" in tools
                    or registered_handler is not getattr(cortex_server, "read_worker_context", None)
                    or "_authorize_host_bound_worker_refresh"
                    not in set(getattr(getattr(registered_handler, "__code__", None), "co_names", ()))
                ):
                    fail(
                        "refresh_worker_context must remain a cursor-only trusted host-bound recovery read"
                    )
            else:
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
    if (
        "read_worker_wave` is forbidden until" not in control_skill_text
        or "exact bound child terminal" not in control_skill_text
        or "`No agents completed yet`" not in control_skill_text
        or not all(status in control_skill_text for status in (
            "`interrupted`", "`completed`", "`errored`", "`shutdown`", "`notFound`",
        ))
    ):
        fail("Cortex control must state the dispatch-wait-read lifecycle boundary")
    orchestrator_skill_path = plugin / "skills/orchestrator/SKILL.md"
    orchestrator_skill_text = orchestrator_skill_path.read_text(encoding="utf-8")
    try:
        expected_orchestrator_skill = expected_orchestrator_skill_text(
            orchestrator_skill_text,
            model_routing,
        )
        expected_orchestrator_skill = expected_profile_capability_skill_text(
            expected_orchestrator_skill,
            profile_contract,
        )
    except ValueError as exc:
        fail(f"Cortex orchestrator model-routing policy is invalid: {exc}")
    if orchestrator_skill_text != expected_orchestrator_skill:
        fail("Cortex orchestrator model-routing guidance differs from profiles.json")
    if (
        "Do not call `read_worker_wave` until `wait_agent`" not in orchestrator_skill_text
        or "`No agents completed yet`" not in orchestrator_skill_text
        or not all(status in orchestrator_skill_text for status in (
            "`interrupted`", "`completed`", "`errored`", "`shutdown`", "`notFound`",
        ))
    ):
        fail("Cortex orchestrator must state the dispatch-wait-read lifecycle boundary")
    expected_hook_events = {
        "SessionStart", "SubagentStart", "SubagentStop",
    }
    hook_registry = hooks.get("hooks", {})
    if not isinstance(hook_registry, dict) or set(hook_registry) != expected_hook_events:
        fail("hook registry must contain only the three native lifecycle events")
    hook_sync_policy = root / "scripts/sync-cortex-hook-trust.py"
    regular_file(hook_sync_policy, "Cortex hook trust synchronizer")
    try:
        hook_sync_tree = ast.parse(hook_sync_policy.read_text(encoding="utf-8"))
        hook_event_assignment = next(
            node for node in hook_sync_tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "V11_HOOK_EVENTS"
                for target in node.targets
            )
        )
        hook_event_value = hook_event_assignment.value
        if not (
            isinstance(hook_event_value, ast.Call)
            and isinstance(hook_event_value.func, ast.Name)
            and hook_event_value.func.id == "frozenset"
            and len(hook_event_value.args) == 1
            and not hook_event_value.keywords
        ):
            raise ValueError("V11_HOOK_EVENTS must be one literal frozenset")
        sync_hook_events = set(ast.literal_eval(hook_event_value.args[0]))
    except (OSError, StopIteration, SyntaxError, TypeError, ValueError) as exc:
        fail(f"Cortex hook trust synchronizer inventory is invalid: {exc}")
    if sync_hook_events != set(hook_registry) or len(sync_hook_events) != len(hook_registry):
        fail("Cortex sync hook policy must exactly match hooks.json events and count")
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
        fail("all native lifecycle hooks must invoke the bundled Cortex launcher")
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
                "<!-- BEGIN GENERATED CORTEX MODEL ROUTING -->",
                "<!-- END GENERATED CORTEX MODEL ROUTING -->",
                "<!-- BEGIN GENERATED CORTEX PROFILE CAPABILITIES -->",
                "<!-- END GENERATED CORTEX PROFILE CAPABILITIES -->",
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

    # Material active steers are occurrence compiler operations, never
    # semantic-name resets. Keep this as a source-level release invariant so
    # marketplace validation catches a regression without constructing or
    # mutating a user's runtime ledger.
    engine_path = plugin / "scripts/cortex_runtime/orchestration_engine.py"
    facade_path = plugin / "scripts/cortex.py"
    try:
        engine_source = engine_path.read_text(encoding="utf-8")
        facade_source = facade_path.read_text(encoding="utf-8")
        engine_tree = ast.parse(engine_source, filename=str(engine_path))
        facade_tree = ast.parse(facade_source, filename=str(facade_path))
    except (OSError, SyntaxError) as exc:
        fail(f"occurrence compiler source is invalid: {exc}")

    def function_source(tree: ast.AST, source: str, name: str) -> str:
        node = next(
            (
                item for item in ast.walk(tree)
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == name
            ),
            None,
        )
        if node is None:
            fail(f"required occurrence compiler function is missing: {name}")
        return ast.get_source_segment(source, node) or ""

    revision_source = function_source(
        engine_tree, engine_source, "_apply_pending_revision_impact",
    )
    if any(marker in revision_source for marker in (
        "apply_pipeline_operations", "append_pipeline_change",
        "invalidate_reworked_result_bindings",
    )):
        fail("material steer compiler must not reset or invalidate work by semantic phase")
    if not all(marker in revision_source for marker in (
        "target_occurrence", "frontier_occurrence_at_revision",
        "next_compiled_wave_index", "_normalize_orchestrate_waves",
        "completed_source_occurrence", "prior_attempt_audit_digest",
        "semantic_revision_request_digest", "with db_transaction(root)",
    )):
        fail("material steer compiler lacks exact occurrence, audit, or atomic replay authority")
    continue_source = function_source(
        engine_tree, engine_source, "_orchestrate_continue",
    )
    completion_position = continue_source.find(
        "orchestration_wave_occurrence_completed"
    )
    revision_position = continue_source.find("_apply_pending_revision_impact(")
    if (
        completion_position < 0
        or revision_position < 0
        or completion_position > revision_position
    ):
        fail("material steer compilation must follow durable source occurrence completion")
    steer_source = function_source(facade_tree, facade_source, "_v11_active_steer")
    if not all(marker in steer_source for marker in (
        "target_occurrence", "frontier_occurrence_at_revision",
        "frontier_position", "current_match", "future", "completed",
    )):
        fail("active steer must bind one exact current, future, or completed occurrence")
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
    expected_writers = {
        item["name"]
        for item in profile_contract["profiles"]
        if item.get("route_category") == "manual"
        and "modify" in item.get("operation_kinds", [])
    }
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
