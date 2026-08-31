#!/usr/bin/env python3
"""Validate the repository-local Cortex V12 plugin package."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, NoReturn

import yaml

from cortex_payload_manifest import (
    RuntimePayloadError,
    runtime_payload_closure,
    validate_directory_topology,
    validated_managed_directory,
)


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PLUGIN = "cortex"
EXPECTED_BASE_VERSION = "1.13.2"
VERSION_PATTERN = re.compile(r"^1\.13\.2\+codex\.sha256\.[0-9a-f]{16}$")
EXPECTED_SKILLS = (
    "adaptive-pipeline",
    "content-safety",
    "context-compaction",
    "coordinator-communication",
    "cortex-control",
    "documentation-sync",
    "find-skills",
    "knowledge-harvest",
    "orchestrator",
    "output-validation",
    "progress-accounting",
)
EXPECTED_MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
EXPECTED_EFFORTS = ("low", "medium", "high", "xhigh", "max")
TASK_ANCHORED_TOOLS = {
    "read_task", "open_clarification", "record_clarification", "open_plan_review", "record_plan_review",
    "open_steering", "record_steering", "open_assignment", "assess_governance", "close_task",
}
EXPECTED_SKILL_RESOURCES = {
    Path("cortex-control/agents/openai.yaml"),
    Path("knowledge-harvest/references/feature-census.md"),
}
RETIRED_PLUGIN_PATHS = {
    Path("scripts/cortex_hook.py"),
    Path("scripts/cortex-launcher"),
    Path("scripts/cortex_runtime/core"),
    Path("scripts/cortex_runtime/record_report"),
}
RETIRED_RUNTIME_MARKERS = (
    "reliability_recovery_target",
    "Luna-to-Terra-to-Sol",
    "SubagentStop",
    "read_worker_wave",
    "wait_agent",
)


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
            if (base / name).is_symlink():
                fail(f"{label} must not contain symlinks: {(base / name).relative_to(root)}")


def reject_plugin_residue(plugin: Path) -> None:
    """Reject bytecode and retired V11 control-plane payloads before packaging."""
    for directory, names, files in os.walk(plugin, followlinks=False):
        base = Path(directory)
        for name in [*names, *files]:
            path = base / name
            relative = path.relative_to(plugin)
            if any(part == "__pycache__" for part in relative.parts):
                fail(f"plugin source contains Python bytecode state: {relative}")
            if path.suffix in {".pyc", ".pyo"}:
                fail(f"plugin source contains Python bytecode: {relative}")
            if relative in RETIRED_PLUGIN_PATHS or any(retired in relative.parents for retired in RETIRED_PLUGIN_PATHS):
                fail(f"plugin source retains retired V11 hook/control-plane residue: {relative}")


def load_json(path: Path, label: str) -> dict[str, Any]:
    regular_file(path, label)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"{label} is invalid: {exc}")
    if not isinstance(payload, dict):
        fail(f"{label} must contain an object")
    return payload


def validate_hooks(plugin: Path) -> None:
    """Validate the official plugin-bundled command-hook surface."""
    hooks = load_json(plugin / "hooks/hooks.json", "plugin hooks")
    if set(hooks) - {"description", "hooks"} or not isinstance(hooks.get("hooks"), dict):
        fail("plugin hooks must use the official hooks.json object shape")
    allowed_events = {"PreToolUse", "PostToolUse", "Stop", "SessionStart", "SessionEnd", "SubagentStart", "SubagentStop", "PreCompact", "PostCompact"}
    if set(hooks["hooks"]) != allowed_events:
        fail("plugin hooks must declare exactly the activation events")
    activation_events = {"PreToolUse", "PostToolUse", "Stop"}
    lifecycle_command = '/usr/bin/python3 -B "$PLUGIN_ROOT/hooks/cortex_lifecycle_observer.py"'
    expected_command = '/usr/bin/python3 "$PLUGIN_ROOT/hooks/cortex_activation.py"'
    for event_name, groups in hooks["hooks"].items():
        if not isinstance(groups, list) or not groups:
            fail(f"plugin hook {event_name} must contain a non-empty matcher list")
        for group in groups:
            if not isinstance(group, dict) or set(group) - {"matcher", "hooks"} or not isinstance(group.get("hooks"), list):
                fail(f"plugin hook {event_name} matcher group has an invalid shape")
            for handler in group["hooks"]:
                if not isinstance(handler, dict) or set(handler) - {"type", "command", "timeout", "additionalContextLimit"}:
                    fail(f"plugin hook {event_name} handler has an invalid shape")
                expected = expected_command if event_name in activation_events and group.get("matcher") != "^Agent$" else lifecycle_command
                allowed = {expected}
                if event_name == "SubagentStart":
                    allowed.add(expected_command)
                if handler.get("type") != "command" or handler.get("command") not in allowed:
                    fail(f"plugin hook {event_name} must use the trusted in-plugin command")
                if not isinstance(handler.get("timeout"), int) or handler["timeout"] < 1:
                    fail(f"plugin hook {event_name} must declare a positive timeout")
                if event_name == "SessionEnd" and handler["timeout"] > 3:
                    fail("plugin SessionEnd hook timeout exceeds the Codex 3-second limit")
    regular_file(plugin / "hooks/cortex_activation.py", "activation hook script")
    regular_file(plugin / "hooks/cortex_lifecycle_observer.py", "lifecycle observer hook script")
    source = (plugin / "hooks/cortex_activation.py").read_text(encoding="utf-8")
    if any(token in source for token in ("$(", "../", "subprocess", "os.system")):
        fail("activation hook command must remain trust-safe and in-plugin")
    lifecycle = (plugin / "hooks/cortex_lifecycle_observer.py").read_text(encoding="utf-8")
    if any(token in lifecycle for token in ("$((", "../", "subprocess", "os.system")):
        fail("lifecycle observer hook must remain trust-safe and in-plugin")


def validate_openai_metadata(plugin: Path) -> None:
    """Validate the official skill/plugin MCP dependency metadata surface."""
    path = plugin / "agents/openai.yaml"
    regular_file(path, "agents/openai.yaml")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        fail(f"agents/openai.yaml is invalid: {exc}")
    if not isinstance(payload, dict) or set(payload) != {"interface", "policy", "dependencies"}:
        fail("agents/openai.yaml must use the official interface/policy/dependencies shape")
    interface = payload["interface"]
    if not isinstance(interface, dict) or set(interface) != {"display_name", "short_description"}:
        fail("agents/openai.yaml interface metadata is invalid")
    if not all(isinstance(interface.get(key), str) and interface[key].strip() for key in interface):
        fail("agents/openai.yaml interface metadata must be non-empty strings")
    policy = payload["policy"]
    if not isinstance(policy, dict) or policy != {"allow_implicit_invocation": False}:
        fail("Cortex must opt out of implicit invocation")
    tools = payload["dependencies"].get("tools") if isinstance(payload["dependencies"], dict) else None
    if not isinstance(tools, list) or len(tools) != 1 or not isinstance(tools[0], dict):
        fail("agents/openai.yaml must declare one MCP dependency")
    dependency = tools[0]
    if set(dependency) != {"type", "value", "description"} or dependency.get("type") != "mcp" or dependency.get("value") != "cortex":
        fail("agents/openai.yaml must declare the Cortex MCP dependency")
    if not isinstance(dependency.get("description"), str) or not dependency["description"].strip():
        fail("Cortex MCP dependency description must be non-empty")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="repository tree to validate")
    parser.add_argument("--candidate", action="store_true", help="require a stamped content-addressed candidate manifest")
    return parser.parse_args()


def validate_marketplace(root: Path, plugin: Path) -> None:
    marketplace = load_json(root / ".agents/plugins/marketplace.json", "root marketplace manifest")
    if marketplace.get("name") != EXPECTED_PLUGIN:
        fail("marketplace name must be 'cortex'")
    interface = marketplace.get("interface")
    if not isinstance(interface, dict) or not isinstance(interface.get("displayName"), str) or not interface["displayName"].strip():
        fail("marketplace interface.displayName must be a non-empty string")
    expected = {
        "name": EXPECTED_PLUGIN,
        "source": {"source": "local", "path": "./plugins/cortex"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "DeveloperTools",
    }
    if marketplace.get("plugins") != [expected]:
        fail("marketplace must contain exactly the repository-managed Cortex plugin entry")
    if (root / "marketplace").exists() or (root / "marketplace").is_symlink():
        fail("retired nested marketplace artifacts must not ship")
    if (plugin / ".codex").exists():
        fail("plugin source must not contain plugin-local runtime state")


def validate_manifest(plugin: Path, *, candidate: bool = False) -> None:
    manifest = load_json(plugin / ".codex-plugin/plugin.json", "plugin manifest")
    version = manifest.get("version")
    valid_version = VERSION_PATTERN.fullmatch(version) if isinstance(version, str) else None
    if manifest.get("name") != EXPECTED_PLUGIN or not isinstance(version, str) or not valid_version:
        fail("installable plugin manifest must use a content-addressed 1.13.2 version")
    if version.split("+", 1)[0] != EXPECTED_BASE_VERSION:
        fail("plugin manifest semantic version must be 1.13.2")
    provenance_path = plugin / "scripts/cortex_runtime/provenance.py"
    spec = importlib.util.spec_from_file_location("cortex_marketplace_provenance", provenance_path)
    if spec is None or spec.loader is None:
        fail("canonical package provenance module cannot be loaded")
    provenance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provenance)
    digest = provenance.package_digest(plugin)
    if not digest.startswith(version.rsplit(".", 1)[-1]):
        fail("plugin manifest content-addressed suffix does not match package content")
    if manifest.get("skills") != "./skills/" or manifest.get("mcpServers") != "./.mcp.json":
        fail("plugin manifest must declare the bundled skills and MCP companion")
    interface = manifest.get("interface")
    if not isinstance(interface, dict) or not all(
        isinstance(interface.get(field), str) and interface[field].strip()
        for field in ("displayName", "shortDescription", "longDescription", "logo", "defaultPrompt")
    ):
        fail("plugin interface metadata is incomplete")
    if len(interface["defaultPrompt"].encode("utf-8")) > 128:
        fail("plugin interface defaultPrompt exceeds the Codex 128-byte limit")
    regular_file(plugin / str(interface["logo"]).removeprefix("./"), "plugin logo")

    mcp = load_json(plugin / ".mcp.json", "MCP companion")
    expected_server = {
        "command": "python3",
        "args": ["-B", "./scripts/cortex.py"],
        "cwd": ".",
        "env_vars": ["CODEX_HOME", "CORTEX_SESSION_NONCE", "CORTEX_RAW_DIAGNOSTIC"],
    }
    if mcp != {"mcpServers": {"cortex": expected_server}}:
        fail("MCP companion must expose only the direct Python Cortex V12 server")
    if manifest.get("hooks") != "./hooks/hooks.json":
        fail("plugin manifest must declare the official bundled hooks path")


def validate_profiles(plugin: Path) -> None:
    contract = load_json(plugin / "profiles.json", "advisory profile contract")
    if set(contract) != {"schema", "model_routing", "dynamic_pipeline", "profiles"} or contract.get("schema") != "cortex/advisory-profiles/v1":
        fail("profiles.json must be the compact V12 advisory profile contract")
    routing = contract.get("model_routing")
    if not isinstance(routing, dict) or set(routing) != {"schema", "native_default_model", "ownership", "recommendations"}:
        fail("advisory model routing metadata has an invalid shape")
    if routing.get("schema") != "cortex/advisory-model-selection/v1" or routing.get("native_default_model") != "gpt-5.6-luna":
        fail("advisory routing must retain Luna as the configured native default")
    if not isinstance(routing.get("ownership"), str) or not routing["ownership"].strip():
        fail("advisory routing must explain coordinator-owned selection")
    recommendations = routing.get("recommendations")
    if not isinstance(recommendations, list) or len(recommendations) != len(EXPECTED_MODELS):
        fail("advisory routing must contain one recommendation for every native model")
    for expected_model, recommendation in zip(EXPECTED_MODELS, recommendations, strict=True):
        if not isinstance(recommendation, dict) or set(recommendation) != {"model", "recommended_effort", "choose_for"}:
            fail("advisory model recommendation has an invalid shape")
        if recommendation.get("model") != expected_model or recommendation.get("recommended_effort") not in EXPECTED_EFFORTS:
            fail("advisory model recommendation does not preserve native model/effort support")
        if not isinstance(recommendation.get("choose_for"), str) or not recommendation["choose_for"].strip():
            fail("advisory model recommendation lacks selection guidance")

    pipeline = contract.get("dynamic_pipeline")
    if not isinstance(pipeline, dict) or set(pipeline) != {
        "schema", "ownership", "complexity_baselines", "governance_baselines", "stages", "rules",
    } or pipeline.get("schema") != "cortex/model-owned-pipeline/v1":
        fail("advisory dynamic-pipeline metadata has an invalid shape")
    if not isinstance(pipeline.get("ownership"), str) or not pipeline["ownership"].strip():
        fail("advisory dynamic-pipeline metadata lacks coordinator ownership guidance")
    if pipeline.get("governance_baselines") != {"C1": "minimal", "C2": "light", "C3": "full"}:
        fail("advisory dynamic-pipeline metadata must preserve C1/C2/C3 governance baselines")
    if not isinstance(pipeline.get("complexity_baselines"), dict) or not isinstance(pipeline.get("stages"), list) or not isinstance(pipeline.get("rules"), list):
        fail("advisory dynamic-pipeline metadata must contain bounded baselines, stages, and rules")

    profiles = contract.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != 22:
        fail("advisory profile contract must define exactly 22 profiles")
    expected_names: set[str] = set()
    for item in profiles:
        if not isinstance(item, dict) or set(item) != {"name", "filename", "description", "select_when", "avoid_when"}:
            fail("each advisory profile must contain only prompt-template metadata")
        name = item.get("name")
        filename = item.get("filename")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            fail("advisory profile has an invalid name")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".toml"):
            fail(f"advisory profile filename is invalid: {name}")
        if name in expected_names:
            fail(f"duplicate advisory profile: {name}")
        expected_names.add(name)
        if not all(isinstance(item.get(field), str) and item[field].strip() for field in ("description", "select_when", "avoid_when")):
            fail(f"advisory profile text is incomplete: {name}")
        path = plugin / "agents" / filename
        regular_file(path, f"advisory profile {name}")
        try:
            prompt = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            fail(f"advisory profile {name} is invalid TOML: {exc}")
        if (
            set(prompt) != {"name", "description", "developer_instructions"}
            or prompt.get("name") != name
            or not isinstance(prompt.get("description"), str)
            or not prompt["description"].strip()
        ):
            fail(f"advisory profile {name} diverges from profiles.json")
        instructions = prompt.get("developer_instructions")
        if not isinstance(instructions, str) or not instructions.strip() or "gpt-" in instructions or "reasoning_effort" in instructions:
            fail(f"advisory profile {name} must remain a model-neutral prompt template")
    agent_files = {path.name for path in (plugin / "agents").glob("*.toml")}
    if agent_files != {str(item["filename"]) for item in profiles}:
        fail("bundled agent files must match the advisory profile contract exactly")


def validate_skills(plugin: Path) -> None:
    folders = {path.name for path in (plugin / "skills").iterdir() if path.is_dir() and not path.is_symlink()}
    if folders != set(EXPECTED_SKILLS):
        fail("bundled skills must be exactly the eleven supported V12 skills")
    resources = {
        path.relative_to(plugin / "skills")
        for path in (plugin / "skills").rglob("*")
        if path.is_file() and path.name != "SKILL.md"
    }
    if resources != EXPECTED_SKILL_RESOURCES:
        fail("bundled skill resources must match the explicit installable V12 resource set")
    for relative in sorted(resources):
        regular_file(plugin / "skills" / relative, f"skill resource {relative}")
    for name in EXPECTED_SKILLS:
        path = plugin / "skills" / name / "SKILL.md"
        regular_file(path, f"skill {name}")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            fail(f"skill {name} is unreadable: {exc}")
        if f"\nname: {name}\n" not in content:
            fail(f"skill frontmatter must identify {name}")
    orchestrator = (plugin / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
    # Concrete host arguments and lifecycle fields belong to the active Codex
    # schema. The package retains a semantic brief only.
    orchestrator_semantics = " ".join(orchestrator.split()).lower()
    lifecycle_markers = (
        "the live mcp catalogue is authoritative",
        "forward it exactly to native spawn",
        "pretooluse/subagentstart correlates the actual child session",
        "never choose a “latest assignment”",
        "no workflow or governance admission rule may block that worker",
    )
    if any(marker not in orchestrator_semantics for marker in lifecycle_markers):
        fail("orchestrator guidance must describe host-schema-owned native lifecycle semantics")
    required_safety_markers = (
        "coordinator communication follows the latest meaningful user-message language",
        "the first execution operation is `open_task`",
        "the coordinator stores only `task_ref`",
        "never an imperative workflow command",
    )
    missing_safety_markers = [marker for marker in required_safety_markers if marker.lower() not in orchestrator.lower()]
    if missing_safety_markers:
        fail("orchestrator guidance lacks terminal task-opening/language safeguards: " + ", ".join(missing_safety_markers))
    communication = (plugin / "skills/coordinator-communication/SKILL.md").read_text(encoding="utf-8")
    required_communication_markers = (
        "result, then its user impact, then the next step",
        "latest meaningful user message",
        "Suppress an update",
        "raw task/delegation/report/decision IDs",
        "Humor is optional",
        "does not add a runtime loader",
    )
    missing_communication_markers = [marker for marker in required_communication_markers if marker not in communication]
    if missing_communication_markers:
        fail("coordinator communication skill lacks required policy safeguards: " + ", ".join(missing_communication_markers))


def validate_prompt_contract(root: Path) -> None:
    """Run the self-contained V12 skill/profile contract lint as a source gate."""
    lint = root / "scripts/cortex-prompt-lint.py"
    regular_file(lint, "V12 skill/profile contract lint")
    checked = subprocess.run(
        [sys.executable, "-B", str(lint)],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if checked.returncode != 0:
        detail = checked.stdout.strip() or checked.stderr.strip() or "no diagnostic output"
        fail("V12 skill/profile contract lint failed: " + detail)


def validate_runtime(plugin: Path) -> None:
    runtime = plugin / "scripts" / "cortex_runtime"
    regular_directory(runtime, "Cortex runtime package")
    try:
        closure = runtime_payload_closure(plugin.parents[1])
    except RuntimePayloadError as exc:
        fail(str(exc))
    plugin_prefix = Path("plugins/cortex").parts
    runtime_files = [
        plugin / Path(*relative.parts[len(plugin_prefix):])
        for relative in closure.files
        if relative.parts[:len(plugin_prefix)] == plugin_prefix
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)
    retired = [marker for marker in RETIRED_RUNTIME_MARKERS if marker in source]
    if retired:
        fail("active V12 runtime retains retired control-plane markers: " + ", ".join(retired))

    runtime_path = str(plugin / "scripts")
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)
    try:
        from cortex_runtime import model_routing, v12_projections
        from cortex_runtime.v12_contract import task_ref
        from cortex_runtime.public_contracts import build_public_contracts
        from cortex_runtime.semantic_registry import OPERATION_NAMES, operation_specs
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        fail(f"V12 runtime cannot be imported: {exc}")
    composition_source = (plugin / "scripts" / "cortex.py").read_text(encoding="utf-8")
    if "bind_handlers(_HANDLERS)" not in composition_source:
        fail("composition root must bind handlers through semantic_registry.bind_handlers")
    compact_probe = "task-" + ("a" * 64) + "-" + ("b" * 32)
    compact_ref = task_ref(compact_probe)
    if (
        compact_ref != "t_" + ("b" * 12)
        or v12_projections._task_relative(compact_ref, "task.md") != Path("tasks") / compact_ref / "task.md"
    ):
        fail("V12 projections must use the canonical compact task_ref directory")
    contracts = build_public_contracts()
    catalogue = tuple(OPERATION_NAMES)
    if tuple(contracts) != catalogue:
        fail("Cortex public-contract catalogue must match semantic registry order")
    for name in catalogue:
        contract = contracts[name]
        registration = {"handler": next(spec.handler_name for spec in operation_specs() if spec.name == name)}
        schema = contract.get("inputSchema") if isinstance(contract, dict) else None
        expected_fields = set(schema.get("properties") or {}) if isinstance(schema, dict) else None
        expected_required = set(schema.get("required") or ()) if isinstance(schema, dict) else None
        if (
            not isinstance(schema, dict)
            or schema.get("type") != "object"
            or schema.get("additionalProperties") is not False
            or "audience" in contract
            or not isinstance(registration, dict)
            or not registration.get("handler")
        ):
            fail(f"V12 public contract is invalid: {name}")
        properties = schema["properties"]
        required = set(schema.get("required") or ())
        if expected_fields is not None and set(properties) != expected_fields:
            fail(f"V12 public contract fields drifted: {name}")
        if expected_required is not None and required != expected_required:
            fail(f"V12 public contract required fields drifted: {name}")
        forbidden = {"assignment_ref", "continuation_ref", "binding_ref", "report_ref", "plan_ref", "decision_ref", "item_ref", "cursor", "digest", "handles"}
        if set(properties) & forbidden:
            fail(f"{name} exposes private identifier or continuation fields")
        if name != "open_task" and "task_ref" not in properties:
            fail(f"{name} must use task_ref as its public task identity")
        if name == "open_task" and not {"project_root", "request_original", "user_language", "outcomes", "constraints"}.issubset(required):
            fail("open_task must expose one flat coherent task contract")
        if name == "open_assignment" and not {"profile_name", "model", "reasoning_effort", "responsibility", "outcomes", "report_policy"}.issubset(required):
            fail("open_assignment must expose one flat LLM-owned mission contract")
    if hasattr(__import__("cortex_runtime.mcp_api", fromlist=["public_tools_for_audience"]), "public_tools_for_audience"):
        fail("V12 MCP transport must not project tools by audience")

    registry = model_routing.model_effort_registry()
    if tuple(registry) != EXPECTED_MODELS or registry != {model: EXPECTED_EFFORTS for model in EXPECTED_MODELS}:
        fail("packaged model/effort recommendations must remain internally consistent")
    for model in EXPECTED_MODELS:
        for effort in EXPECTED_EFFORTS:
            try:
                recommendation = model_routing.validate_model_selection(model, effort)
            except ValueError as exc:
                fail(f"model/effort recommendation rejected {model}/{effort}: {exc}")
            if recommendation.model != model or recommendation.reasoning_effort != effort:
                fail(f"model/effort recommendation rewrote {model}/{effort}")
    try:
        model_routing.validate_model_selection("host-selected-model", "host-selected-effort")
    except ValueError:
        pass
    else:
        fail("model routing must reject values outside the advertised catalogue")


def main() -> int:
    requested_root = parse_args().root
    try:
        root = validated_managed_directory(requested_root, "repository root")
        plugin = validated_managed_directory(root / "plugins" / EXPECTED_PLUGIN, "canonical plugin source")
    except RuntimePayloadError as exc:
        fail(str(exc))
    reject_symlinks(root / ".agents", "root marketplace metadata")
    reject_symlinks(plugin, "canonical plugin source")
    reject_plugin_residue(plugin)
    try:
        plugin_files = {
            path.relative_to(plugin)
            for path in plugin.rglob("*")
            if path.is_file()
        }
        validate_directory_topology(plugin, plugin_files, "canonical plugin")
    except (OSError, RuntimePayloadError) as exc:
        fail(str(exc))
    validate_marketplace(root, plugin)
    args = parse_args()
    validate_manifest(plugin, candidate=args.candidate)
    validate_hooks(plugin)
    validate_openai_metadata(plugin)
    validate_profiles(plugin)
    validate_skills(plugin)
    validate_prompt_contract(root)
    validate_runtime(plugin)
    print(f"marketplace validation passed: {root / '.agents/plugins/marketplace.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
