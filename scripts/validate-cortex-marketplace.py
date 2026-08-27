#!/usr/bin/env python3
"""Validate the repository-local Cortex V12 plugin package."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, NoReturn


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PLUGIN = "cortex"
EXPECTED_BASE_VERSION = "12.0.0"
VERSION_PATTERN = re.compile(r"^12\.0\.0\+codex\.\d{14}$")
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
BASE_V12_TOOLS = (
    "create_task",
    "inspect_task",
    "create_delegation",
    "read_delegation",
    "submit_report",
    "read_reports",
    "set_governance_mode",
    "record_initiative",
    "inspect_governance",
    "submit_governance_closure",
    "record_user_decision",
)
SUPPORTED_V12_CATALOGUES = (BASE_V12_TOOLS,)
TASK_ANCHORED_TOOLS = {
    "inspect_task", "create_delegation", "set_governance_mode", "record_initiative",
    "inspect_governance", "submit_governance_closure", "record_user_decision",
}
EXPECTED_TOOL_FIELDS = {
    "create_task": {
        "project_root", "objective", "user_request_original", "user_language", "task_contract_version",
        "requirements", "constraints", "acceptance_criteria", "verification_plan", "context",
        "idempotency_key",
    },
    "inspect_task": {"task_ref", "after_sequence", "limit"},
    "create_delegation": {
        "task_ref", "objective", "role", "profile_name", "scope", "instructions",
        "parent_delegation_ref", "input_report_refs", "input_decision_refs", "approval_decision_ref", "model", "reasoning_effort",
        "idempotency_key",
    },
    "read_delegation": {"delegation_ref", "after_sequence", "limit"},
    "submit_report": {
        "delegation_ref", "mode", "report_type", "status", "content", "report_ref",
        "chunk_index", "section", "expected_chunk_count", "expected_content_digest", "abort_reason_en",
        "supersedes_report_ref", "review_policy", "idempotency_key",
    },
    "read_reports": {
        "report_refs", "sections", "cursor", "max_bytes",
        "consumer_delegation_ref", "reader_kind",
    },
    "set_governance_mode": {
        "task_ref", "mode", "rationale", "risk_factors", "source", "initiative_ref", "idempotency_key",
    },
    "record_initiative": {
        "task_ref", "goal", "initiative_ref", "parent_initiative_ref", "risk", "status", "dependency_refs",
        "linked_task_refs", "linked_delegation_refs", "linked_report_refs", "linked_decision_refs", "notes", "idempotency_key",
    },
    "inspect_governance": {"task_ref", "initiative_ref", "after_sequence", "limit"},
    "submit_governance_closure": {
        "task_ref", "subject_type", "subject_ref", "verdict", "evidence", "unresolved_risks", "follow_ups",
        "initiative_status", "completion_notes", "idempotency_key",
    },
}
EXPECTED_TOOL_REQUIRED = {
    "create_task": {
        "project_root", "objective", "user_request_original", "user_language",
        "task_contract_version", "requirements", "constraints",
        "acceptance_criteria", "verification_plan",
    },
    "inspect_task": {"task_ref"},
    "create_delegation": {"task_ref", "objective", "role", "profile_name", "scope", "instructions", "model", "reasoning_effort"},
    "read_delegation": {"delegation_ref", "after_sequence"},
    "submit_report": {"delegation_ref"},
    "read_reports": {"report_refs"},
    "set_governance_mode": {"task_ref", "mode"},
    "record_initiative": {"task_ref", "goal"},
    "inspect_governance": {"task_ref"},
    "submit_governance_closure": {"task_ref", "subject_type", "subject_ref", "verdict", "evidence"},
    "record_user_decision": {
        "task_ref", "subject_type", "subject_ref", "subject_digest", "decision_type",
        "prompt_en", "response_original", "response_en", "user_language",
    },
}
EXPECTED_DECISION_FIELDS = {
    "task_ref", "subject_type", "subject_ref", "subject_digest", "decision_type", "prompt_en",
    "response_original", "response_en", "user_language", "approval_handle",
    "approval_view_content_digest", "approval_view_source_sequence", "supersedes_decision_ref",
    "idempotency_key",
}
ACTIVE_RUNTIME_FILES = {
    "__init__.py",
    "canonical_json.py",
    "delegation.py",
    "markdown_document.py",
    "mcp_api.py",
    "model_routing.py",
    "public_contracts.py",
    "report_presenters.py",
    "routing.py",
    "v12_contract.py",
    "v12_maintenance.py",
    "v12_projections.py",
    "v12_service.py",
    "v12_store.py",
    "worker_message.py",
}
EXPECTED_SKILL_RESOURCES = {
    Path("cortex-control/agents/openai.yaml"),
    Path("knowledge-harvest/references/feature-census.md"),
}
RETIRED_PLUGIN_PATHS = {
    Path("hooks"),
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="repository tree to validate")
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


def validate_manifest(plugin: Path) -> None:
    manifest = load_json(plugin / ".codex-plugin/plugin.json", "plugin manifest")
    version = manifest.get("version")
    if manifest.get("name") != EXPECTED_PLUGIN or not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        fail("plugin manifest must use Cortex 12.0.0 with a codex timestamp cachebuster")
    if version.split("+", 1)[0] != EXPECTED_BASE_VERSION:
        fail("plugin manifest semantic version must be 12.0.0")
    if manifest.get("skills") != "./skills/" or manifest.get("mcpServers") != "./.mcp.json":
        fail("plugin manifest must declare the bundled skills and MCP companion")
    interface = manifest.get("interface")
    if not isinstance(interface, dict) or not all(
        isinstance(interface.get(field), str) and interface[field].strip()
        for field in ("displayName", "shortDescription", "longDescription", "logo", "defaultPrompt")
    ):
        fail("plugin interface metadata is incomplete")
    regular_file(plugin / str(interface["logo"]).removeprefix("./"), "plugin logo")

    mcp = load_json(plugin / ".mcp.json", "MCP companion")
    expected_server = {"command": "python3", "args": ["./scripts/cortex.py"], "cwd": "."}
    if mcp != {"mcpServers": {"cortex": expected_server}}:
        fail("MCP companion must expose only the direct Python Cortex V12 server")
    if (plugin / "hooks/hooks.json").exists() or (plugin / "scripts/cortex_hook.py").exists() or (plugin / "scripts/cortex-launcher").exists():
        fail("Cortex V12 package must not ship hook or launcher lifecycle assets")


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
        fail("bundled skills must be exactly the eleven V12 skills")
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
    if "gpt-5.6-luna" not in orchestrator or "fork_turns=\"none\"" not in orchestrator:
        fail("orchestrator guidance must preserve the native Luna/default dispatch rule")
    required_safety_markers = (
        "deterministically matches the actual user message",
        "must not inject a contradictory target language",
        "`create_task` is the terminal task-anchoring boundary",
        "Do not start degraded project work, use a fallback",
    )
    missing_safety_markers = [marker for marker in required_safety_markers if marker not in orchestrator]
    if missing_safety_markers:
        fail("orchestrator guidance lacks terminal create_task/language safeguards: " + ", ".join(missing_safety_markers))
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
    files = {path.name for path in runtime.glob("*.py")}
    if files != ACTIVE_RUNTIME_FILES:
        fail("package must contain only the active V12 runtime modules")
    source = "\n".join((runtime / name).read_text(encoding="utf-8") for name in sorted(files))
    retired = [marker for marker in RETIRED_RUNTIME_MARKERS if marker in source]
    if retired:
        fail("active V12 runtime retains retired control-plane markers: " + ", ".join(retired))

    runtime_path = str(plugin / "scripts")
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)
    try:
        import cortex
        from cortex_runtime import model_routing, v12_projections
        from cortex_runtime.v12_contract import task_ref
        from cortex_runtime.public_contracts import V12_TOOL_NAMES, build_public_contracts
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        fail(f"V12 runtime cannot be imported: {exc}")
    if getattr(cortex, "SERVER_VERSION", None) != EXPECTED_BASE_VERSION:
        fail("Cortex server must publish the 12.0.0 semantic version")
    compact_probe = "task-" + ("a" * 64) + "-" + ("b" * 32)
    compact_ref = task_ref(compact_probe)
    if (
        compact_ref != "t_" + ("b" * 12)
        or v12_projections._task_relative(compact_ref, "task.md") != Path("tasks") / compact_ref / "task.md"
    ):
        fail("V12 projections must use the canonical compact task_ref directory")
    tools = getattr(cortex, "PUBLIC_TOOLS", None)
    contracts = build_public_contracts()
    catalogue = tuple(V12_TOOL_NAMES)
    if catalogue not in SUPPORTED_V12_CATALOGUES:
        fail("Cortex runtime exposes an unsupported V12 catalogue; expected the canonical base or approved decision extension")
    if not isinstance(tools, dict) or tuple(tools) != catalogue or tuple(contracts) != catalogue:
        fail("Cortex runtime/public-contract catalogues must be identical and ordered")
    for name in catalogue:
        contract = contracts[name]
        registration = tools[name]
        schema = contract.get("inputSchema") if isinstance(contract, dict) else None
        expected_fields = EXPECTED_TOOL_FIELDS.get(name)
        expected_required = EXPECTED_TOOL_REQUIRED.get(name)
        if (
            not isinstance(schema, dict)
            or schema.get("type") != "object"
            or schema.get("additionalProperties") is not False
            or "audience" in contract
            or not isinstance(registration, dict)
            or not callable(registration.get("handler"))
            or registration.get("inputSchema") != schema
        ):
            fail(f"V12 public contract is invalid: {name}")
        properties = schema["properties"]
        required = set(schema.get("required") or ())
        if expected_fields is not None and set(properties) != expected_fields:
            fail(f"V12 public contract fields drifted: {name}")
        if expected_required is not None and required != expected_required:
            fail(f"V12 public contract required fields drifted: {name}")
        if name == "create_task":
            project_root = properties.get("project_root")
            if not isinstance(project_root, dict) or project_root.get("type") != "string":
                fail("create_task.project_root must remain the explicit V12 shard anchor")
        elif name in {"read_delegation", "submit_report", "read_reports"}:
            derived_required = {
                "read_delegation": {"delegation_ref", "after_sequence"},
                "submit_report": {"delegation_ref"},
                "read_reports": {"report_refs"},
            }[name]
            if (
                "project_root" in properties
                or "task_id" in properties
                or "task_ref" in properties
                or not derived_required.issubset(required)
            ):
                fail(f"{name} must resolve through its required opaque record references without a public task anchor")
        elif name in TASK_ANCHORED_TOOLS:
            task_ref = properties.get("task_ref")
            if (
                "project_root" in properties
                or "task_id" in properties
                or "task_ref" not in required
                or not isinstance(task_ref, dict)
                or task_ref.get("type") != "string"
                or task_ref.get("maxLength") != 14
            ):
                fail(f"{name} must resolve through required compact task_ref without a public task_id alternative")
        if name == "read_reports":
            max_bytes = properties.get("max_bytes")
            if (
                not isinstance(max_bytes, dict)
                or max_bytes.get("type") != "integer"
                or max_bytes.get("minimum") != 0
                or not isinstance(max_bytes.get("maximum"), int)
                or "byte_budget" in properties
            ):
                fail("read_reports must expose only the canonical integer max_bytes field")
        if name == "create_delegation":
            scope = properties.get("scope")
            if (
                not isinstance(scope, dict)
                or scope.get("type") != "string"
                or scope.get("minLength") != 1
                or not isinstance(scope.get("maxLength"), int)
                or scope["maxLength"] < 1
            ):
                fail("create_delegation.scope must be a bounded non-empty string")
        if name == "submit_governance_closure":
            subject_id = properties.get("subject_ref")
            if (
                not isinstance(subject_id, dict)
                or subject_id.get("type") != "string"
                or subject_id.get("minLength") != 1
                or subject_id.get("maxLength") != 14
            ):
                fail("submit_governance_closure.subject_ref must be a bounded compact reference")
        if name == "record_user_decision":
            if set(properties) != EXPECTED_DECISION_FIELDS:
                fail("record_user_decision fields drifted from the canonical V12 user-decision contract")
            if "oneOf" in schema:
                fail("record_user_decision must expose one canonical field set, not legacy request shapes")
            conditionals = schema.get("allOf")
            decision_shapes = (
                conditionals[0].get("anyOf")
                if isinstance(conditionals, list)
                and len(conditionals) == 1
                and isinstance(conditionals[0], dict)
                else None
            )
            approval_required = {"approval_handle", "approval_view_content_digest", "approval_view_source_sequence"}
            if (
                not isinstance(decision_shapes, list)
                or not any(
                    isinstance(shape, dict)
                    and isinstance(shape.get("properties"), dict)
                    and isinstance(shape["properties"].get("decision_type"), dict)
                    and shape["properties"]["decision_type"].get("const") == "approve"
                    and approval_required.issubset(set(shape.get("required") or ()))
                    for shape in decision_shapes
                )
            ):
                fail("record_user_decision must require the complete approval relation for approve")
            subject_type = properties.get("subject_type")
            decision_type = properties.get("decision_type")
            approval_handle = properties.get("approval_handle")
            approval_view_digest = properties.get("approval_view_content_digest")
            approval_view_sequence = properties.get("approval_view_source_sequence")
            if (
                not isinstance(subject_type, dict)
                or subject_type.get("type") != "string"
                or subject_type.get("enum") != ["task", "plan", "initiative", "delegation", "report"]
                or not isinstance(decision_type, dict)
                or decision_type.get("type") != "string"
                or decision_type.get("enum") != [
                    "approve", "reject", "request_revision", "clarification", "cancel", "accept_risk", "override",
                ]
            ):
                fail("record_user_decision must preserve the canonical V12 subject and decision enums")
            if (
                approval_required & required
                or not isinstance(approval_handle, dict)
                or approval_handle.get("type") != "string"
                or approval_handle.get("minLength") != 1
                or approval_handle.get("maxLength") != 160
                or not isinstance(approval_view_digest, dict)
                or approval_view_digest.get("type") != "string"
                or approval_view_digest.get("minLength") != 0
                or approval_view_digest.get("maxLength") != 71
                or not isinstance(approval_view_digest.get("pattern"), str)
                or not isinstance(approval_view_sequence, dict)
                or approval_view_sequence.get("type") != "integer"
                or approval_view_sequence.get("minimum") != 0
            ):
                fail("record_user_decision must preserve the canonical approval relation fields")
    if hasattr(__import__("cortex_runtime.mcp_api", fromlist=["public_tools_for_audience"]), "public_tools_for_audience"):
        fail("V12 MCP transport must not project tools by audience")

    registry = model_routing.model_effort_registry()
    if tuple(registry) != EXPECTED_MODELS or registry != {model: EXPECTED_EFFORTS for model in EXPECTED_MODELS}:
        fail("native model/effort registry must preserve all exact supported selections")
    for model in EXPECTED_MODELS:
        for effort in EXPECTED_EFFORTS:
            try:
                native = model_routing.native_spawn_arguments(
                    model=model,
                    reasoning_effort=effort,
                    task_name="cortex-v12-validator",
                    message="Preserve the selected native model and effort exactly.",
                )
            except ValueError as exc:
                fail(f"native model/effort transport rejected {model}/{effort}: {exc}")
            if native.get("reasoning_effort") != effort or native.get("fork_turns") != "none":
                fail(f"native model/effort transport rewrote {model}/{effort}")
            if model == "gpt-5.6-luna":
                if "model" in native:
                    fail("native Luna transport must omit the model override")
            elif native.get("model") != model:
                fail(f"native {model} transport must retain the explicit model override")


def main() -> int:
    root = parse_args().root.resolve(strict=False)
    regular_directory(root, "repository root")
    plugin = root / "plugins" / EXPECTED_PLUGIN
    regular_directory(plugin, "canonical plugin source")
    reject_symlinks(root / ".agents", "root marketplace metadata")
    reject_symlinks(plugin, "canonical plugin source")
    reject_plugin_residue(plugin)
    validate_marketplace(root, plugin)
    validate_manifest(plugin)
    validate_profiles(plugin)
    validate_skills(plugin)
    validate_prompt_contract(root)
    validate_runtime(plugin)
    print(f"marketplace validation passed: {root / '.agents/plugins/marketplace.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
