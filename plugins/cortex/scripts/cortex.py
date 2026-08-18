#!/usr/bin/env python3
"""Local stdio MCP server for a durable, evidence-driven orchestration ledger."""
from __future__ import annotations
import contextlib
import difflib
import fnmatch
import html
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import tomllib
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
# Some installer checks load this executable through ``importlib`` rather than
# by executing it from its own directory.  Keep the bundled runtime package
# resolvable in both modes; never depend on the caller's current directory.
_SCRIPTS_ROOT = str(Path(__file__).resolve().parent)
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)
# Extracted runtime modules consume the stable public ``cortex`` facade
# while the entrypoint is loaded through normal import, importlib, or direct
# stdio execution. Point that name at this exact module rather than allowing a
# second server instance to be imported under a different name.
if __name__ != "cortex" and __name__ in sys.modules:
    sys.modules["cortex"] = sys.modules[__name__]


def respond(payload: dict[str, Any]) -> None:
    """Write a JSON-RPC response, including a nested elicitation request."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


from cortex_runtime.delegation import (
    delegation_lists,
    dispatch_context,
    select_profile as select_delegation_profile,
    spawn_request as build_spawn_request,
    task_kind_and_risk,
)
from cortex_runtime.identity import (
    NATIVE_AGENT_NAME_RE,
    SAFE_ID_RE,
    native_worker_task_name,
    safe_id,
    worker_display_name,
    worker_module_label,
)
from cortex_runtime.mcp_api import (
    PUBLIC_TOOL_DESCRIPTIONS,
    build_public_schemas,
    configure_internal_schemas,
    public_tools as build_public_tools,
    serve_stdio,
    v3_response as render_v3_response,
)
from cortex_runtime.ledger_db import (
    ARTIFACT_TRANSPORT_MAX_BYTES,
    DATABASE_SCHEMA_VERSION,
    all_lanes as db_all_lanes,
    artifact_path as db_task_artifact_path,
    create_task as db_create_task,
    delete_classifications as db_delete_classifications,
    delete_global as db_delete_global,
    delete_operations_for_tasks as db_delete_operations_for_tasks,
    delete_task_manifest_snapshots as db_delete_task_manifest_snapshots,
    delete_tasks as db_delete_tasks,
    ensure_database as ensure_ledger_database,
    get_classification as db_get_classification,
    get_global as db_get_global,
    get_lane as db_get_lane,
    get_manifest_snapshot as db_get_manifest_snapshot,
    get_artifact_for_export_path as db_get_artifact_for_export_path,
    get_artifact_metadata as db_get_artifact_metadata,
    manifest_snapshot_refs as db_manifest_snapshot_refs,
    get_operation as db_get_operation,
    get_task_document as db_get_task_document,
    list_task_documents as db_list_task_documents,
    load_task as db_load_task,
    migration_history as db_migration_history,
    next_task_number as db_next_task_number,
    put_classification as db_put_classification,
    put_global as db_put_global,
    put_lane as db_put_lane,
    put_manifest_snapshot as db_put_manifest_snapshot,
    put_artifact as db_put_artifact,
    put_operation as db_put_operation,
    put_task_document as db_put_task_document,
    savepoint as db_savepoint,
    list_artifacts as db_list_artifacts,
    read_artifact_content as db_read_artifact_content,
    read_artifact_range as db_read_artifact_range,
    encode_artifact_cursor as db_encode_artifact_cursor,
    decode_artifact_cursor as db_decode_artifact_cursor,
    task_index as db_task_index,
    transaction as db_transaction,
    update_task_definition as db_update_task_definition,
    update_task_plan as db_update_task_plan,
    update_task_state as db_update_task_state,
    upsert_task_finding as db_upsert_task_finding,
    append_task_revision as db_append_task_revision,
    append_plan_revision as db_append_plan_revision,
    append_attempt_message as db_append_attempt_message,
    put_worker_session as db_put_worker_session,
    list_task_findings as db_list_task_findings,
    task_findings_blockers as db_task_findings_blockers,
    plan_prune as db_plan_prune,
    list_prune_tombstones as db_list_prune_tombstones,
    claim_prune_tombstone as db_claim_prune_tombstone,
    mark_prune_filesystem_removed as db_mark_prune_filesystem_removed,
    finalize_prunes as db_finalize_prunes,
    fail_prune as db_fail_prune,
)
from cortex_runtime.routing import (
    profile_can_own_gate as routing_profile_can_own_gate,
    profiles_for_gate as routing_profiles_for_gate,
    resolve_dispatch_route as routing_resolve_dispatch_route,
)
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback; atomic replace still applies.
    fcntl = None
SCHEMA = "cortex/v8"
REPORT_SCHEMA = "cortex/report/v1"
RESULT_VALIDATION_SCHEMA = "cortex/result-validation/v1"
PLANNING_SCHEMA = "cortex/planning/v1"
SCOPING_SCHEMA = "cortex/scoping/v1"
PIPELINE_CONTRACT_VERSION = 2
QUESTION_SCHEMA = "cortex/question/v2"
ACTIVATION_COMMAND = "/cortex"
NORMAL_COMMAND = "/normal"
SKILL_ROUTE_HINT = "select `cortex:orchestrator` in the Skills picker or mention `$cortex:orchestrator` in the main chat"
PROFILE_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "profiles.json"
# Desktop inserts this local Markdown link when a user selects the Cortex
# Orchestrator skill. It is host transport metadata, not user task content;
# retaining its absolute plugin-cache path in task labels or durable ledgers
# leaks a machine-local path and makes the label depend on the cache version.
DESKTOP_CORTEX_ORCHESTRATOR_LINK_RE = re.compile(
    r"\[\$cortex:orchestrator\]\((?:file://)?(?:[A-Za-z]:)?[\\/][^)\r\n]*?"
    r"[\\/]skills[\\/]orchestrator[\\/]SKILL\.md\)",
    re.IGNORECASE,
)
CODEX_SESSION_ENV_KEYS = ("CODEX_SESSION_ID", "CODEX_THREAD_ID")
HOST_SESSION_SCHEMA = "cortex/host-sessions/v1"
PLUGIN_ROOT = PROFILE_CONTRACT_PATH.parent
PLUGIN_MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
MCP_OPENAI_FORM = False
_STATE_LOCK_LOCAL = threading.local()
MCP_SERVER_INSTRUCTIONS = (
    "Cortex is opt-in. Root preserves task_ref, follows exact dispatches, and publishes every read_worker_report "
    "report_markdown_link before lifecycle. Internal workers emit English only and read scoped predecessor refs. "
    "Each dispatch has one immutable briefing+digest plus an exact scoped read fallback; bind native starts by exact task_name/dispatch identity. "
    "After resume, clear, or compaction, inspect once: spawn returned pending dispatches, wait persisted child ids, "
    "use context_handoff, never restart."
)

try:
    SERVER_VERSION = str(json.loads(PLUGIN_MANIFEST_PATH.read_text(encoding="utf-8"))["version"])
except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
    raise RuntimeError("bundled Cortex plugin manifest is unreadable") from exc


def load_profile_contract() -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, str]]:
    try:
        contract = json.loads(PROFILE_CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("bundled Cortex profile contract is unreadable") from exc
    if contract.get("schema") != "cortex/profile-contract/v1" or not isinstance(contract.get("profiles"), list):
        raise RuntimeError("bundled Cortex profile contract schema is invalid")
    profiles: dict[str, dict[str, Any]] = {}
    for item in contract["profiles"]:
        if not isinstance(item, dict) or not SAFE_ID_RE.fullmatch(str(item.get("name", ""))):
            raise RuntimeError("bundled Cortex profile contract contains an invalid profile")
        required_fields = {
            "name", "filename", "sandbox", "route_category", "gates",
            "description", "select_when", "avoid_when",
        }
        if not required_fields.issubset(item):
            raise RuntimeError("bundled Cortex profile contract contains incomplete routing metadata")
        name = str(item["name"])
        if name in profiles:
            raise RuntimeError("bundled Cortex profile contract contains duplicate profiles")
        if item.get("sandbox") not in {"read-only", "workspace-write"}:
            raise RuntimeError(f"bundled Cortex profile sandbox is invalid: {name}")
        if item.get("route_category") not in {"automatic", "manual"}:
            raise RuntimeError(f"bundled Cortex profile route category is invalid: {name}")
        if not isinstance(item.get("gates"), list) or not all(isinstance(gate, str) for gate in item["gates"]):
            raise RuntimeError(f"bundled Cortex profile gates are invalid: {name}")
        if not all(isinstance(item.get(field), str) and item[field].strip() for field in ("description", "select_when", "avoid_when")):
            raise RuntimeError(f"bundled Cortex profile routing text is invalid: {name}")
        profiles[name] = item
    instructions: dict[str, str] = {}
    agents_root = PROFILE_CONTRACT_PATH.parent / "agents"
    for name, profile in profiles.items():
        filename = Path(str(profile.get("filename", "")))
        if filename.is_absolute() or len(filename.parts) != 1 or filename.name in {"", ".", ".."}:
            raise RuntimeError(f"bundled Cortex agent profile path is invalid: {name}")
        profile_path = agents_root / filename
        if profile_path.is_symlink() or not profile_path.is_file():
            raise RuntimeError(f"bundled Cortex agent profile is not a regular file: {name}")
        try:
            profile_data = tomllib.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise RuntimeError(f"bundled Cortex agent profile is unreadable: {name}") from exc
        developer_instructions = str(profile_data.get("developer_instructions", "")).strip()
        if not developer_instructions:
            raise RuntimeError(f"bundled Cortex agent profile has no developer instructions: {name}")
        if profile_data.get("name") != name or profile_data.get("sandbox_mode") != profile.get("sandbox"):
            raise RuntimeError(f"bundled Cortex agent profile identity does not match its contract: {name}")
        if str(profile_data.get("description", "")).strip() != profile.get("description"):
            raise RuntimeError(f"bundled Cortex agent description does not match its contract: {name}")
        instructions[name] = developer_instructions
    execution_contracts = contract.get("profile_execution_contracts")
    if not isinstance(execution_contracts, dict) or set(execution_contracts) != set(profiles):
        raise RuntimeError("bundled Cortex execution contracts must cover every profile exactly once")
    for name, execution in execution_contracts.items():
        if not isinstance(execution, dict) or set(execution) != {"inputs", "project_artifacts", "completion"}:
            raise RuntimeError(f"bundled Cortex execution contract is invalid: {name}")
        if not all(isinstance(execution[field], str) and execution[field].strip() for field in execution):
            raise RuntimeError(f"bundled Cortex execution contract is incomplete: {name}")
    return contract, profiles, instructions


PROFILE_CONTRACT, PROFILES, PROFILE_INSTRUCTIONS = load_profile_contract()
AGENTS = set(PROFILES)
PROFILE_EXECUTION_CONTRACTS = PROFILE_CONTRACT["profile_execution_contracts"]
SHARED_WORKER_CONTRACT = PROFILE_CONTRACT.get("shared_worker_contract", {})
CODEBASE_MEMORY_REFRESH_PROFILES = set(SHARED_WORKER_CONTRACT.get("codebase_memory_refresh_profiles", []))
if (
    SHARED_WORKER_CONTRACT.get("repository_intelligence")
    != "codebase_memory_first_when_available_then_source_confirmed_with_bounded_fallback"
    or SHARED_WORKER_CONTRACT.get("codebase_memory_project_resolution")
    != "derive_canonical_path_key_then_single_exact_root_list_fallback"
    or SHARED_WORKER_CONTRACT.get("codebase_memory_project_key_algorithm")
    != "cbm_project_name_from_path_safe_ascii_utf8hex_fnv1a200"
    or SHARED_WORKER_CONTRACT.get("codebase_memory_fallback")
    != "one_bounded_attempt_then_repository_native_tools_without_looping"
    or SHARED_WORKER_CONTRACT.get("report_draft_lifecycle")
    != "template_private_file_direct_or_patch_validate_one_hour_consume"
    or SHARED_WORKER_CONTRACT.get("report_finalization")
    != "identity_draft_ref_validation_digest_same_file_then_delete"
    or SHARED_WORKER_CONTRACT.get("read_only_workspace_delta")
    != "ordinary_source_changes_are_concurrency_evidence_generated_or_ignored_side_effects_fail"
    or CODEBASE_MEMORY_REFRESH_PROFILES != {"planner", "explorer", "architect", "database_architect"}
    or not CODEBASE_MEMORY_REFRESH_PROFILES.issubset(AGENTS)
):
    raise RuntimeError("bundled Cortex shared worker contract is invalid")
AVAILABLE_GATES = {
    "scope", "plan", "discover", "architecture", "database_architecture", "implementation",
    "qa", "security", "performance", "accessibility", "ux", "review",
    "documentation", "close",
}
READ_ONLY_RESULT_GATES = {
    "scope", "plan", "discover", "architecture", "database_architecture", "security",
    "performance", "accessibility", "ux", "review", "close",
}
EXECUTED_CHECK_RESULT_GATES = {
    "implementation", "qa", "security", "performance", "accessibility", "ux",
    "review", "documentation", "close",
}
WRITE_REQUIRED_RESULT_GATES = {"implementation"}


def result_contract_is_read_only(attempt: dict[str, Any]) -> bool:
    """Return whether the gate or selected profile forbids project mutations."""
    profile_name = str(attempt.get("profile") or attempt.get("agent") or "")
    return attempt.get("gate") in READ_ONLY_RESULT_GATES or PROFILES.get(profile_name, {}).get("sandbox") == "read-only"


for _profile_name, _profile in PROFILES.items():
    _profile_gates = _profile.get("gates", [])
    if len(_profile_gates) != len(set(_profile_gates)) or not set(_profile_gates).issubset(AVAILABLE_GATES):
        raise RuntimeError(f"bundled Cortex profile has invalid gates: {_profile_name}")
    if _profile.get("route_category") == "automatic" and not _profile_gates:
        raise RuntimeError(f"bundled automatic Cortex profile has no gate: {_profile_name}")
    if _profile.get("route_category") == "manual" and _profile_gates:
        raise RuntimeError(f"bundled manual Cortex profile must be implementation-selected: {_profile_name}")
GATE_BRIEFINGS = PROFILE_CONTRACT.get("gate_briefings")
if not isinstance(GATE_BRIEFINGS, dict) or set(GATE_BRIEFINGS) != AVAILABLE_GATES:
    raise RuntimeError("bundled Cortex profile contract must define one briefing for every gate")
for _gate_name, _briefing in GATE_BRIEFINGS.items():
    if not isinstance(_briefing, dict) or set(_briefing) != {"objective", "ownership", "acceptance", "verification"}:
        raise RuntimeError(f"bundled Cortex gate briefing is invalid: {_gate_name}")
    if not all(isinstance(_briefing[key], str) and _briefing[key].strip() for key in ("objective", "ownership")):
        raise RuntimeError(f"bundled Cortex gate briefing text is invalid: {_gate_name}")
    if not all(
        isinstance(_briefing[key], list)
        and _briefing[key]
        and all(isinstance(item, str) and item.strip() for item in _briefing[key])
        for key in ("acceptance", "verification")
    ):
        raise RuntimeError(f"bundled Cortex gate briefing lists are invalid: {_gate_name}")

IMPLEMENTATION_ROUTING = PROFILE_CONTRACT.get("implementation_routing")
if not isinstance(IMPLEMENTATION_ROUTING, dict):
    raise RuntimeError("bundled Cortex profile contract lacks implementation routing")
_implementation_fallback = IMPLEMENTATION_ROUTING.get("fallback")
_implementation_rules = IMPLEMENTATION_ROUTING.get("rules")
if _implementation_fallback not in AGENTS or not isinstance(_implementation_rules, list) or not _implementation_rules:
    raise RuntimeError("bundled Cortex implementation routing is invalid")
if (
    PROFILES[_implementation_fallback].get("sandbox") != "workspace-write"
    or "implementation" not in PROFILES[_implementation_fallback].get("gates", [])
):
    raise RuntimeError("bundled Cortex implementation fallback must be an implementation writer")
_routed_implementation_profiles: set[str] = set()
for _rule in _implementation_rules:
    if not isinstance(_rule, dict) or set(_rule) - {"profile", "reason", "any", "all"}:
        raise RuntimeError("bundled Cortex implementation routing rule is invalid")
    _rule_profile = _rule.get("profile")
    if _rule_profile not in AGENTS or _rule_profile in _routed_implementation_profiles:
        raise RuntimeError("bundled Cortex implementation routing has an unknown or duplicate profile")
    if PROFILES[_rule_profile].get("sandbox") != "workspace-write":
        raise RuntimeError("bundled Cortex implementation routing may select only workspace-write profiles")
    if not isinstance(_rule.get("reason"), str) or not _rule["reason"].strip():
        raise RuntimeError("bundled Cortex implementation routing rule lacks a reason")
    any_signals = _rule.get("any", [])
    all_groups = _rule.get("all", [])
    if not isinstance(any_signals, list) or not all(isinstance(item, str) and item.strip() for item in any_signals):
        raise RuntimeError("bundled Cortex implementation routing any-signals are invalid")
    if not isinstance(all_groups, list) or not all(
        isinstance(group, list) and group and all(isinstance(item, str) and item.strip() for item in group)
        for group in all_groups
    ):
        raise RuntimeError("bundled Cortex implementation routing all-groups are invalid")
    if not any_signals and not all_groups:
        raise RuntimeError("bundled Cortex implementation routing rule has no signals")
    _routed_implementation_profiles.add(str(_rule_profile))
_manual_implementation_profiles = {
    name for name, profile in PROFILES.items()
    if profile.get("route_category") == "manual" and profile.get("sandbox") == "workspace-write"
}
if _routed_implementation_profiles != _manual_implementation_profiles:
    raise RuntimeError("bundled Cortex implementation routing must cover every manual writer exactly once")


def render_gate_briefing(gate: str, task_objective: object, profile: str) -> dict[str, Any]:
    """Render trusted gate defaults around the current task without interpreting user text."""
    template = GATE_BRIEFINGS[gate]
    values = {
        "task_objective": redact(task_objective, 4000),
        "profile": profile,
        "gate": gate,
    }
    return {
        "objective": template["objective"].format(**values),
        "ownership": template["ownership"].format(**values),
        "acceptance_criteria": [item.format(**values) for item in template["acceptance"]],
        "verification": [item.format(**values) for item in template["verification"]],
    }


def render_profile_catalog(*, markdown: bool = False, compact: bool = False) -> str:
    """Render the canonical team map from the machine-validated profile contract."""
    if markdown:
        lines = [
            "| Profile | Route | Access | Select when | Avoid when |",
            "| --- | --- | --- | --- | --- |",
        ]
        for name in sorted(AGENTS):
            profile = PROFILES[name]
            lines.append(
                f"| `{name}` | {profile['route_category']} | {profile['sandbox']} | "
                f"{profile['select_when']} | {profile['avoid_when']} |"
            )
        return "\n".join(lines)
    if compact:
        return "\n".join(
            f"- {name} [{PROFILES[name]['sandbox']}]"
            for name in sorted(AGENTS)
        )
    return "\n".join(
        f"- {name} [{PROFILES[name]['sandbox']}; {PROFILES[name]['route_category']}]: "
        f"{PROFILES[name]['description']} Select when: {PROFILES[name]['select_when']} "
        f"Avoid when: {PROFILES[name]['avoid_when']}"
        for name in sorted(AGENTS)
    )


def _task_routing_items(task: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for field in ("objective", "requirements", "acceptance_criteria", "scope", "allowed_paths", "verification"):
        value = task.get(field)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value is not None:
            values.append(str(value))
    return values


def _implementation_routing_text(task: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", " ".join(_task_routing_items(task)).lower().replace("ё", "е")).strip()


def _routing_signal_matches(corpus: str, signal: str) -> bool:
    normalized = re.sub(r"\s+", " ", signal.lower().replace("ё", "е")).strip()
    if re.fullmatch(r"[a-z0-9_]+", normalized):
        return re.search(rf"(?<![a-z0-9_]){re.escape(normalized)}(?![a-z0-9_])", corpus) is not None
    return normalized in corpus


def select_implementation_profile(task: dict[str, Any]) -> dict[str, Any]:
    """Conservatively select a writer from explicit task signals.

    This is an initial route, not a substitute for repository evidence. A
    planner or explorer may recommend a narrower replacement for future waves.
    """
    corpus = _implementation_routing_text(task)
    for rule in IMPLEMENTATION_ROUTING["rules"]:
        any_signals = [signal for signal in rule.get("any", []) if _routing_signal_matches(corpus, signal)]
        all_matches = [
            [signal for signal in group if _routing_signal_matches(corpus, signal)]
            for group in rule.get("all", [])
        ]
        all_satisfied = bool(all_matches) and all(group for group in all_matches)
        if any_signals or all_satisfied:
            matched = any_signals or [group[0] for group in all_matches]
            return {
                "profile": rule["profile"],
                "reason": rule["reason"],
                "matched_signals": matched,
                "source": "bounded_task_signals",
            }
    fallback = str(IMPLEMENTATION_ROUTING["fallback"])
    return {
        "profile": fallback,
        "reason": "No specialist implementation signal was strong enough; use the bounded general fallback until worker evidence justifies a narrower owner.",
        "matched_signals": [],
        "source": "conservative_fallback",
    }
# Gate IDs are part of the MCP contract.  The orchestrator sometimes emits
# human-facing labels (for example, ``planning``) even though the durable
# ledger uses the canonical IDs above. Keep input normalization explicit and
# bounded: unknown IDs must still fail closed instead of being guessed.
PIPELINE_GATE_ALIASES = {
    "scoping": "scope",
    "planning": "plan",
    "analysis": "discover",
    "investigation": "discover",
    "discovery": "discover",
    "research": "discover",
    "exploration": "discover",
    "architecture_design": "architecture",
    "database_design": "database_architecture",
    "implement": "implementation",
    "implementation_work": "implementation",
    "test": "qa",
    "testing": "qa",
    "verify": "qa",
    "verification": "qa",
    "quality_assurance": "qa",
    "code_review": "review",
    "reviewing": "review",
    "docs": "documentation",
    "documentation_sync": "documentation",
    # A worker/profile label is often used as a phase label by coordinators.
    # Treat explicit final build verification as the close gate.  The generic
    # `verification` denotes the QA phase; explicit final verification is close.
    "build_verification": "close",
    "final_verification": "close",
    "release_verification": "close",
    "finalization": "close",
    "closing": "close",
}
# Native host adapters and older coordinator prompts sometimes use the
# human-facing gate/profile label instead of the durable profile id.  Keep
# these aliases at the MCP boundary so a harmless naming variation cannot
# create a failed attempt (or, worse, leave a task half-dispatched).
PROFILE_ALIASES = {
    "discovery": "explorer",
    "exploration": "explorer",
    "researcher": "explorer",
    "planner_agent": "planner",
    "performance": "performance_engineer",
    "accessibility": "accessibility_engineer",
    "ux": "ux_designer",
    "code_reviewer": "code_reviewer",
    "security": "security_auditor",
    "qa": "qa_engineer",
    "build_verification": "build_verification",
    "technical_writer": "technical_writer",
}
V3_AUTOMATIC_IMPLEMENTATION_PROFILE_ALIASES = {
    "developer", "implementation", "implementer", "implementation_agent",
}
MANDATORY_PIPELINE_GATES = {
    "C1": ["documentation", "close"],
    "C2": ["documentation", "close"],
    "C3": ["documentation", "close"],
}
# These are the smallest auditable pipelines for each complexity. Specialist
# gates are selected from task requirements instead of being baked into C3.
BASE_PIPELINES = {
    "C1": ["discover", "implementation", "review", "close"],
    "C2": ["discover", "plan", "implementation", "qa", "review", "documentation", "close"],
    "C3": ["scope", "discover", "plan", "implementation", "qa", "review", "documentation", "close"],
}
GATE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
HOST_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
AUTHORIZATION_RE = re.compile(r"(?im)(\bauthorization[ \t]*[:=][ \t]*)([^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*)")
SENSITIVE_RE = re.compile(r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|token|password|passwd|secret|private[_ -]?key|authorization|bearer)\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)")
BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+|bearer\s+)([^\s,;]+)")
URI_CREDENTIAL_RE = re.compile(r"(?i)(://)([^/@\s]+):([^/@\s]+)(@)")
ENV_SECRET_RE = re.compile(r"(?i)(\b[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY)[A-Z0-9_]*\s*=\s*)(?:\"[^\"]*\"|'[^']*'|[^\s]+)")
MAX_TEXT = 4000
MAX_REPORT_BYTES = 65536
MAX_REPORT_ITEMS = 100
MAX_REPORTS_PER_ATTEMPT = 32
MAX_REPORTS_PER_TASK = 256
MAX_REPORT_AGGREGATE_BYTES = 1024 * 1024
MAX_PLANNING_BYTES = 128 * 1024
MAX_SCOPING_BYTES = 128 * 1024
MAX_DISCOVERY_DOMAINS = 8
MAX_BRIEFING_BYTES = 128 * 1024
MAX_WORK_PACKAGES = 32
MAX_MICROTASKS_PER_PACKAGE = 32
MAX_MICROTASKS_PER_PLAN = 128
MAX_TASK_STATE_BYTES = 8 * 1024 * 1024
# Every ordinary JSON artifact is bounded before it replaces an existing
# ledger file. Large manifests use the explicit, larger budget below instead
# of silently bypassing the guard.
MAX_JSON_BYTES = 8 * 1024 * 1024
# A project manifest is intentionally larger than an individual report or
# task-state document: it contains one bounded inventory record per project
# entry.  Keep the read cap finite while allowing ordinary repositories to
# complete handoff and reconciliation.
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_CONTEXT_REPORTS = 8
MAX_CONTEXT_REPORT_CHARS = 32000
MAX_GATE_RECOVERY_FAILURES = 3
MAX_ORCHESTRATE_GATE_FAILURES = 3
MAX_GATE_RECOVERY_EVENTS = 64
MAX_TOOL_ERROR_LOG_INPUT_BYTES = 16384
MAX_TOOL_ERROR_LOG_BYTES = 10 * 1024 * 1024
MAX_QUESTIONS_PER_ATTEMPT = 64
MAX_QUESTIONS_PER_TASK = 512
# These are Cortex policy models, not a claim about the native host catalog.
# The coordinator may supply the exact models exposed by its native
# ``spawn_agent`` tool for an individual dispatch.
SUPPORTED_MODELS = {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
REQUESTABLE_MODELS = SUPPORTED_MODELS
SUPPORTED_EFFORT_SEQUENCE = ("low", "medium", "high", "xhigh", "max")
SUPPORTED_EFFORTS = set(SUPPORTED_EFFORT_SEQUENCE)
MODEL_RESOLUTIONS = {"configured_default", "explicit_override", "visible_thread"}
MODEL_ROUTING = PROFILE_CONTRACT.get("model_routing")
if not isinstance(MODEL_ROUTING, dict) or MODEL_ROUTING.get("schema") != "cortex/model-routing/v1":
    raise RuntimeError("bundled Cortex model routing contract is invalid")
CONFIGURED_DEFAULT_MODEL = str(MODEL_ROUTING.get("configured_default_model", ""))
if CONFIGURED_DEFAULT_MODEL != "gpt-5.6-luna":
    raise RuntimeError("bundled Cortex model routing must use Luna as the configured default")


def _validated_effort_map(
    value: Any,
    keys: set[str],
    label: str,
    *,
    allow_max: bool = False,
) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RuntimeError(f"bundled Cortex {label} keys are invalid")
    normalized = {str(key): str(effort) for key, effort in value.items()}
    allowed_efforts = SUPPORTED_EFFORTS if allow_max else SUPPORTED_EFFORTS - {"max"}
    if not set(normalized.values()).issubset(allowed_efforts):
        raise RuntimeError(f"bundled Cortex {label} efforts are invalid")
    return normalized


_security_routing = MODEL_ROUTING.get("security")
_explorer_routing = MODEL_ROUTING.get("explorer")
if not isinstance(_security_routing, dict) or _security_routing.get("model") != "gpt-5.6-sol":
    raise RuntimeError("bundled Cortex security model routing is invalid")
if not isinstance(_explorer_routing, dict) or _explorer_routing.get("model") != CONFIGURED_DEFAULT_MODEL:
    raise RuntimeError("bundled Cortex explorer model routing is invalid")
SECURITY_MODEL = str(_security_routing["model"])
EXPLORER_MODEL = str(_explorer_routing["model"])
SECURITY_EFFORT_BY_COMPLEXITY = _validated_effort_map(
    _security_routing.get("effort_by_complexity"), {"C1", "C2", "C3"}, "security effort map"
)
EXPLORER_EFFORT_BY_RISK = _validated_effort_map(
    _explorer_routing.get("effort_by_risk"), {"low", "moderate", "high", "critical"}, "explorer effort map"
)
LUNA_BOUNDED_EFFORT_BY_COMPLEXITY = _validated_effort_map(
    MODEL_ROUTING.get("luna_bounded_effort_by_complexity"),
    {"C1", "C2", "C3"},
    "bounded Luna effort map",
    allow_max=True,
)
if LUNA_BOUNDED_EFFORT_BY_COMPLEXITY != {"C1": "high", "C2": "xhigh", "C3": "max"}:
    raise RuntimeError("bundled Cortex bounded Luna effort map must be high/xhigh/max")
LUNA_EFFICIENT_EFFORT_BY_COMPLEXITY = _validated_effort_map(
    MODEL_ROUTING.get("luna_efficient_effort_by_complexity"),
    {"C1", "C2", "C3"},
    "efficient Luna effort map",
)
TERRA_EFFORT_BY_COMPLEXITY = _validated_effort_map(
    MODEL_ROUTING.get("terra_effort_by_complexity"),
    {"C1", "C2", "C3"},
    "Terra effort map",
)
MODEL_EFFORT_FLOOR_BY_RISK = _validated_effort_map(
    MODEL_ROUTING.get("effort_floor_by_risk"), {"low", "moderate", "high", "critical"}, "model effort risk map"
)
if MODEL_ROUTING.get("max_policy") != "bounded_complex_work":
    raise RuntimeError("bundled Cortex max effort policy must be bounded_complex_work")

_profile_classes = MODEL_ROUTING.get("profile_classes")
if not isinstance(_profile_classes, dict) or set(_profile_classes) != {"efficient", "adaptive", "deep"}:
    raise RuntimeError("bundled Cortex model profile classes are invalid")
MODEL_PROFILE_CLASSES: dict[str, set[str]] = {}
_classified_profiles: set[str] = set()
for _class_name, _class_profiles in _profile_classes.items():
    if (
        not isinstance(_class_profiles, list)
        or not _class_profiles
        or not all(isinstance(name, str) and name in AGENTS for name in _class_profiles)
    ):
        raise RuntimeError(f"bundled Cortex model profile class is invalid: {_class_name}")
    _class_set = set(_class_profiles)
    if len(_class_set) != len(_class_profiles) or _classified_profiles.intersection(_class_set):
        raise RuntimeError("bundled Cortex model profile classes overlap or contain duplicates")
    MODEL_PROFILE_CLASSES[str(_class_name)] = _class_set
    _classified_profiles.update(_class_set)
if _classified_profiles != AGENTS - {"explorer", "security_auditor"}:
    raise RuntimeError("bundled Cortex model profile classes must cover every ordinary profile exactly once")

_terra_task_kinds = MODEL_ROUTING.get("terra_task_kinds")
if (
    not isinstance(_terra_task_kinds, list)
    or not _terra_task_kinds
    or len(set(_terra_task_kinds)) != len(_terra_task_kinds)
    or not all(isinstance(kind, str) and SAFE_ID_RE.fullmatch(kind) for kind in _terra_task_kinds)
):
    raise RuntimeError("bundled Cortex Terra task kinds are invalid")
TERRA_TASK_KINDS = set(_terra_task_kinds)
LIGHTWEIGHT_TASK_KINDS = {
    "read_only", "read-only", "reading", "discover", "discovery",
    "read_discovery", "read-discovery", "audit", "comparison",
    "comparative_audit", "comparative-audit",
    "data_gathering", "data-gathering", "crud", "crud_edit", "crud-edit",
    "small_fix", "small-fix",
}
# Analysis intent controls durable read-only metadata independently of model
# selection. Keep this list bounded and explicit because task_kind is
# model-supplied input at the MCP boundary.
ANALYSIS_TASK_KINDS = {
    "analysis", "analyze", "investigation", "investigate", "diagnosis",
    "diagnostic", "research", "fact_gathering", "fact_finding",
    "source_analysis", "code_analysis", "runtime_investigation",
    "root_cause_analysis", "code_review",
}
ANALYSIS_TASK_KIND_PREFIXES = (
    "analysis", "analy", "investigat", "diagnos", "research",
    "fact_gather", "fact_find", "source_analysis", "code_analysis",
    "runtime_investigat", "root_cause",
)
REASONING_EFFORT_ORDER = {name: index for index, name in enumerate(SUPPORTED_EFFORT_SEQUENCE)}
TERMINAL_ATTEMPT_STATUSES = {"passed", "failed", "blocked", "cancelled", "superseded"}
AWAITING_HOST_SPAWN = "awaiting_host_spawn"
CAPABILITY_SOURCE = "host_spawn_agent_contract_2026-08-13"
ROUTING_POLICY = {
    "supported_models": SUPPORTED_MODELS,
    "requestable_models": REQUESTABLE_MODELS,
    "supported_efforts": SUPPORTED_EFFORTS,
    "configured_default_model": CONFIGURED_DEFAULT_MODEL,
    "security_model": SECURITY_MODEL,
    "explorer_model": EXPLORER_MODEL,
    "security_effort_by_complexity": SECURITY_EFFORT_BY_COMPLEXITY,
    "explorer_effort_by_risk": EXPLORER_EFFORT_BY_RISK,
    "luna_bounded_effort_by_complexity": LUNA_BOUNDED_EFFORT_BY_COMPLEXITY,
    "luna_efficient_effort_by_complexity": LUNA_EFFICIENT_EFFORT_BY_COMPLEXITY,
    "terra_effort_by_complexity": TERRA_EFFORT_BY_COMPLEXITY,
    "model_effort_floor_by_risk": MODEL_EFFORT_FLOOR_BY_RISK,
    "profile_classes": MODEL_PROFILE_CLASSES,
    "terra_task_kinds": TERRA_TASK_KINDS,
    "lightweight_task_kinds": LIGHTWEIGHT_TASK_KINDS,
    "analysis_task_kinds": ANALYSIS_TASK_KINDS,
    "analysis_task_kind_prefixes": ANALYSIS_TASK_KIND_PREFIXES,
    "reasoning_effort_order": REASONING_EFFORT_ORDER,
    "capability_source": CAPABILITY_SOURCE,
}
# Documentation evidence is a decision receipt, not a free-form report label.
# Older workers used the two suffixed names below; accepting and canonicalizing
# them keeps an already valid technical-writer result from triggering a new
# worker solely because of a spelling mismatch.
DOCUMENTATION_EVIDENCE_KINDS = {
    "documentation",
    "documentation_report",
    "documentation_sync",
    "documentation_applicability",
    "verification",
    "report",
    "command",
}
TRACKER_POLICY = {
    "schema": "cortex/file-manifest/v1",
    "scope": "all non-directory entries below project_root after policy exclusions",
    "ignored_roots": [".git", ".codex/cortex"],
    # These are language-agnostic dependency, cache, test-output, and runtime
    # directories.  They are deliberately limited to names that conventionally
    # contain generated material rather than project source.
    "ignored_directory_names": [
        "__pycache__", ".build", ".cache", ".direnv", ".eggs", ".gradle",
        ".hypothesis", ".mypy_cache", ".next", ".nox", ".parcel-cache",
        ".pnpm-store", ".pytest_cache", ".ruff_cache", ".serverless",
        ".svelte-kit", ".terraform", ".tox", ".turbo", ".venv", "CMakeFiles",
        "DerivedData", "Pods", "_build", "coverage", "dist-newstyle", "htmlcov",
        "node_modules", "pip-wheel-metadata", "test-results",
    ],
    "ignored_relative_roots": [".yarn/cache", ".yarn/unplugged", "Carthage/Build"],
    # Generic output names require either an explicit .gitignore rule or a
    # recognizable build marker; source directories named build/dist/target
    # are therefore not hidden merely because of their name.
    "build_output_directory_names": ["build", "dist", "out", "target", "bin", "obj"],
    "virtual_environment_prefixes": [".venv"],
    "ignored_file_suffixes": [".pyc", ".pyo"],
    "symlinks": "record link target and never follow",
    "special_files": "record type and metadata without reading content",
    "gitignore": "honor directory and file patterns from .gitignore, including negation",
}
MANIFEST_SNAPSHOT_PREFIX = "manifest-"
MANIFEST_SNAPSHOT_REF_RE = re.compile(r"^manifest-([0-9a-f]{64})$")
VERIFICATION_COMMANDS = {
    "benign_success": {"argv": ["/usr/bin/true"], "cwd": "."},
    "benign_failure": {"argv": ["/usr/bin/false"], "cwd": "."},
}
SENSITIVE_KEY_RE = re.compile(r"(?i)(?:^|[_ -])(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|token|password|passwd|secret|private[_ -]?key|authorization)(?:$|[_ -])")
SENSITIVE_LOG_KEY_NAMES = {
    "apikey", "accesstoken", "refreshtoken", "clientsecret", "token",
    "password", "passwd", "secret", "privatekey", "authorization",
}
INTERNAL_NON_ENGLISH_SCRIPT_RE = re.compile(
    r"[\u0370-\u052f\u0530-\u058f\u0590-\u08ff\u0900-\u0fff"
    r"\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonicalize_desktop_cortex_request(value: object) -> str:
    """Remove only Desktop's local Cortex skill-link wrapper from task text.

    The replacement keeps the selected ``$cortex:orchestrator`` route and all
    following user-authored words, while ensuring an absolute plugin path is
    never persisted to the Cortex task ledger or incorporated into a task ID.
    Arbitrary Markdown links and user-provided paths are intentionally left
    untouched.
    """
    raw = str(value or "").strip()
    return DESKTOP_CORTEX_ORCHESTRATOR_LINK_RE.sub("$cortex:orchestrator", raw)


def v3_task_slug(value: object) -> str:
    """Build a concise durable-ID label without Desktop skill transport data."""
    canonical = canonicalize_desktop_cortex_request(value)
    without_route = re.sub(r"^\$cortex:orchestrator(?:\s+|$)", "", canonical, flags=re.IGNORECASE).strip()
    return re.sub(r"[^a-z0-9]+", "-", without_route.lower()).strip("-")[:48] or "task"


def normalize_routing_id(value: Any, field: str = "routing id") -> str:
    raw = str(value or "").strip().lower()
    normalized = re.sub(r"[\s-]+", "_", raw)
    if not normalized or not GATE_RE.fullmatch(normalized):
        raise ValueError(f"{field} must contain only letters, digits, spaces, hyphens, or underscores and start with a letter")
    return normalized


def redact(value: object, limit: int = MAX_TEXT) -> str:
    text = str(value or "")[:limit]
    text = AUTHORIZATION_RE.sub(lambda match: f"{match.group(1)}<REDACTED>", text)
    text = BEARER_RE.sub(lambda match: f"{match.group(1)}<REDACTED>", text)
    text = URI_CREDENTIAL_RE.sub(r"\1<REDACTED>@", text)
    text = ENV_SECRET_RE.sub(r"\1<REDACTED>", text)
    return SENSITIVE_RE.sub(lambda match: f"{match.group(1)}=<REDACTED>", text)


def require_internal_english(value: object, label: str) -> None:
    """Reject worker-authored durable text in a non-Latin script.

    Prompting establishes the full English-only rule. This narrow guard is a
    deterministic boundary for the common failure mode (for example Cyrillic
    worker reports/questions) without trying to classify quoted source data or
    file paths as natural language.
    """
    text = str(value or "")
    if INTERNAL_NON_ENGLISH_SCRIPT_RE.search(text):
        raise ValueError(
            f"{label} must be English-only; non-English user-facing content belongs to the main coordinator"
        )


def normalize_user_language(value: object, fallback_text: object = "") -> str:
    """Return a bounded language tag for user-facing coordinator messages."""
    requested = str(value or "").strip().lower().replace("_", "-")
    if requested:
        requested = {
            "english": "en", "russian": "ru", "русский": "ru",
            "romanian": "ro", "română": "ro", "german": "de",
            "french": "fr", "spanish": "es", "italian": "it",
            "portuguese": "pt", "ukrainian": "uk", "українська": "uk",
            "polish": "pl", "chinese": "zh", "japanese": "ja", "korean": "ko",
        }.get(requested, requested)
        if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2,4})?", requested):
            raise ValueError("user_language must be a BCP-47-like lowercase language tag")
        return requested
    sample = str(fallback_text or "")
    if re.search(r"[\u0400-\u04ff]", sample):
        return "ru"
    if re.search(r"[\u0370-\u03ff]", sample):
        return "el"
    return "en"


def digest_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _codex_host_session_id() -> str | None:
    """Return a validated session hint explicitly forwarded to the MCP process.

    Public v4 arguments intentionally do not accept caller-supplied durable
    identity.  Some hosts or operator configurations forward a session/thread
    environment value; the documented PostToolUse hook session_id is the
    primary lifecycle binding.  An absent or malformed value keeps the
    standalone-MCP fallback behavior until that hook runs.
    """
    for name in CODEX_SESSION_ENV_KEYS:
        candidate = str(os.environ.get(name, "")).strip().lower()
        if SAFE_ID_RE.fullmatch(candidate):
            return candidate
    return None


def _reject_symlink_ancestry(path: Path, label: str, allow_missing_leaf: bool = False) -> Path:
    """Return an absolute path after rejecting every existing symlink component."""
    candidate = path.expanduser().absolute()
    current = Path(candidate.anchor)
    parts = candidate.parts[1:] if candidate.anchor else candidate.parts
    for index, part in enumerate(parts):
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"{label} must not traverse symlinks")
    return candidate


def _contained_path(base: Path, child: Path, label: str) -> Path:
    base_absolute = base.absolute()
    child_absolute = child.absolute()
    try:
        child_absolute.relative_to(base_absolute)
    except ValueError as exc:
        raise ValueError(f"{label} escapes its allowed root") from exc
    _reject_symlink_ancestry(child_absolute, label, allow_missing_leaf=True)
    return child_absolute


def select_project_root(params: dict[str, Any] | None = None) -> Path:
    """Resolve one explicit absolute workspace supplied by the current tool call."""
    if os.environ.get("CORTEX_ROOT"):
        raise ValueError("CORTEX_ROOT is not supported; Cortex writes only below project_root/.codex/cortex")
    requested = str((params or {}).get("project_root") or "").strip()
    if not requested:
        raise ValueError("project_root is required for every Cortex tool call")
    requested_path = Path(requested).expanduser()
    if not requested_path.is_absolute():
        raise ValueError("project_root must be an absolute path")
    path = _reject_symlink_ancestry(requested_path, "project root")
    if not path.is_dir():
        raise ValueError(f"project root is not a directory: {path}")
    if path == PLUGIN_ROOT or PLUGIN_ROOT in path.parents:
        raise ValueError("project_root must not be the Cortex plugin directory")
    return path


def project_root(params: dict[str, Any] | None = None) -> Path:
    return select_project_root(params)


def _manifest_file(path: Path, info: os.stat_result) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError(f"project file changed while it was being inventoried: {path}")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (after.st_size, after.st_mtime_ns, after.st_ino) != (opened.st_size, opened.st_mtime_ns, opened.st_ino):
            raise ValueError(f"project file changed while it was being inventoried: {path}")
        return {"kind": "file", "sha256": digest.hexdigest(), "size": opened.st_size, "mode": stat.S_IMODE(opened.st_mode)}
    finally:
        os.close(descriptor)


def _load_manifest_gitignore_rules(directory: Path, relative: tuple[str, ...]) -> list[dict[str, Any]]:
    """Load relevant .gitignore rules without following symlinks.

    This is intentionally a small, deterministic matcher rather than a claim to
    implement every Git ignore edge case. It preserves directory-only markers,
    applies ordinary globs to both matching files and directories, and applies
    negations in source order.
    """
    path = directory / ".gitignore"
    if path.is_symlink() or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rules: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith((r"\#", r"\!")):
            line = line[1:]
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        if not line:
            continue
        anchored = line.startswith("/")
        if anchored:
            line = line[1:]
        directory_rule = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        rules.append({
            "base": list(relative),
            "pattern": line,
            "anchored": anchored,
            "directory_only": directory_rule,
            "negated": negated,
            "source": Path(*relative, ".gitignore").as_posix(),
        })
    return rules


def _manifest_gitignore_matches(parts: tuple[str, ...], rule: dict[str, Any], is_dir: bool) -> bool:
    if bool(rule.get("directory_only")) and not is_dir:
        return False
    base = tuple(str(item) for item in rule.get("base", []))
    if len(parts) <= len(base) or parts[: len(base)] != base:
        return False
    relative = Path(*parts[len(base) :]).as_posix()
    pattern = str(rule.get("pattern", ""))
    if not pattern:
        return False
    if pattern.startswith("**/"):
        pattern = pattern[3:]
    if "/" not in pattern:
        if bool(rule.get("anchored")):
            return fnmatch.fnmatchcase(relative, pattern)
        return fnmatch.fnmatchcase(parts[-1], pattern)
    return fnmatch.fnmatchcase(relative, pattern)


def _manifest_gitignored(parts: tuple[str, ...], rules: list[dict[str, Any]], is_dir: bool) -> tuple[bool, str | None]:
    ignored = False
    source: str | None = None
    for rule in rules:
        if not _manifest_gitignore_matches(parts, rule, is_dir):
            continue
        ignored = not bool(rule.get("negated"))
        source = str(rule.get("source") or "") or None
    return ignored, source


def _manifest_virtual_environment(path: Path, name: str, prefixes: tuple[str, ...]) -> bool:
    if any(name.startswith(prefix) for prefix in prefixes):
        return True
    if name.lower() not in {"venv", "env"}:
        return False
    markers = (
        "pyvenv.cfg",
        "bin/activate",
        "bin/python",
        "Scripts/activate",
        "Scripts/python.exe",
    )
    return any((path / marker).is_file() and not (path / marker).is_symlink() for marker in markers)


def _manifest_build_output(path: Path, name: str, build_names: set[str]) -> bool:
    if name.startswith("cmake-build-") or name.startswith("bazel-"):
        return True
    if name not in build_names:
        return False
    markers = {
        "build": ("CMakeFiles", ".ninja_deps", "intermediates", "classes", "reports"),
        "dist": ("*.whl", "*.tar.gz"),
        "out": ("Debug", "Release", "generated"),
        "target": (".rustc_info.json", "debug", "release", "classes", "test-classes", "maven-status"),
        "bin": ("Debug", "Release"),
        "obj": ("Debug", "Release"),
    }.get(name, ())
    for marker in markers:
        if "*" in marker:
            if any(match.exists() and not match.is_symlink() for match in path.glob(marker)):
                return True
        elif (path / marker).exists() and not (path / marker).is_symlink():
            return True
    return False


def _manifest_auto_ignore_reason(
    path: Path,
    parts: tuple[str, ...],
    policy: dict[str, Any],
) -> str | None:
    name = parts[-1]
    ignored_names = {str(item) for item in policy.get("ignored_directory_names", [])}
    if name in ignored_names:
        return f"conventional generated directory: {name}"
    relative = Path(*parts).as_posix()
    for root in policy.get("ignored_relative_roots", []):
        root_text = Path(str(root)).as_posix().strip("/")
        if relative == root_text or relative.startswith(root_text + "/"):
            return f"conventional generated root: {root_text}"
    prefixes = tuple(str(item) for item in policy.get("virtual_environment_prefixes", []))
    if _manifest_virtual_environment(path, name, prefixes):
        return f"virtual environment directory: {name}"
    build_names = {str(item) for item in policy.get("build_output_directory_names", [])}
    if _manifest_build_output(path, name, build_names):
        return f"recognized build output directory: {name}"
    return None


def capture_project_manifest(root: Path | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Capture every non-ignored project entry without following symlinks.

    A new baseline discovers project-owned `.gitignore` rules and records the
    resulting policy.  Reconciliation may pass that recorded policy back so an
    existing task remains stable even if `.gitignore` or generated directories
    change while the task is running.
    """
    if root is None:
        raise ValueError("project root is required for manifest capture")
    base = root
    base = _reject_symlink_ancestry(base, "project root")
    entries: dict[str, dict[str, Any]] = {}
    active_policy = dict(TRACKER_POLICY if policy is None else policy)
    ignored_roots = {tuple(Path(value).parts) for value in active_policy.get("ignored_roots", [])}
    ignored_suffixes = tuple(str(value) for value in active_policy.get("ignored_file_suffixes", []))
    discovered_rules: list[dict[str, Any]] = []
    discovered_gitignore_files: set[str] = set()
    detected_roots: dict[str, str] = {}
    detected_ignored_entries: dict[str, dict[str, Any]] = {}
    frozen_rules = list(active_policy.get("gitignore_rules", [])) if policy is not None else []
    if policy is not None:
        discovered_gitignore_files.update(str(item) for item in active_policy.get("gitignore_files", []))

    def ignored(parts: tuple[str, ...], path: Path, is_dir: bool, rules: list[dict[str, Any]]) -> tuple[bool, str | None]:
        if any(parts[: len(prefix)] == prefix for prefix in ignored_roots):
            return True, "ledger or VCS root"
        gitignored, source = _manifest_gitignored(parts, rules, is_dir)
        if gitignored:
            return True, f".gitignore:{source or 'rule'}"
        # An explicit negation is the project owner's opt-in to keep a path;
        # it takes precedence over the conventional fallback exclusions below.
        if source:
            return False, None
        if is_dir:
            reason = _manifest_auto_ignore_reason(path, parts, active_policy)
            if reason:
                return True, reason
        if not is_dir and parts and parts[-1].endswith(ignored_suffixes):
            return True, f"ignored file suffix: {parts[-1]}"
        return False, None

    def walk(directory: Path, relative: tuple[str, ...] = (), inherited_rules: list[dict[str, Any]] | None = None) -> None:
        rules = list(inherited_rules or [])
        if policy is None:
            local_gitignore = _load_manifest_gitignore_rules(directory, relative)
            if local_gitignore:
                rules.extend(local_gitignore)
                source = Path(*relative, ".gitignore").as_posix()
                discovered_gitignore_files.add(source)
                discovered_rules.extend(local_gitignore)
        elif not relative:
            rules.extend(frozen_rules)
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda item: item.name)
        for child in children:
            parts = (*relative, child.name)
            info = child.stat(follow_symlinks=False)
            mode = info.st_mode
            is_directory = stat.S_ISDIR(mode)
            path = Path(child.path)
            skip, reason = ignored(parts, path, is_directory, rules)
            if skip:
                if reason and reason != "ledger or VCS root":
                    ignored_path = Path(*parts).as_posix()
                    detected_ignored_entries[ignored_path] = {
                        "kind": "directory" if is_directory else "file",
                        "reason": reason,
                        "mode": stat.S_IMODE(mode),
                        "size": info.st_size,
                        "mtime_ns": info.st_mtime_ns,
                    }
                    if is_directory:
                        detected_roots[ignored_path] = reason
                continue
            rel = Path(*parts).as_posix()
            if stat.S_ISLNK(mode):
                entries[rel] = {"kind": "symlink", "target": os.readlink(path), "mode": stat.S_IMODE(mode)}
            elif is_directory:
                walk(path, parts, rules)
            elif stat.S_ISREG(mode):
                entries[rel] = _manifest_file(path, info)
            else:
                entries[rel] = {"kind": "special", "file_type": stat.S_IFMT(mode), "mode": stat.S_IMODE(mode), "size": info.st_size}
    walk(base)
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if policy is None:
        active_policy["gitignore_rules"] = discovered_rules
    active_policy["effective_ignored_roots"] = sorted(Path(*parts).as_posix() for parts in ignored_roots)
    active_policy["gitignore_files"] = sorted(discovered_gitignore_files)
    active_policy["detected_ignored_roots"] = dict(sorted(detected_roots.items()))
    active_policy["detected_ignored_entries"] = dict(sorted(detected_ignored_entries.items()))
    return {
        "schema": TRACKER_POLICY["schema"],
        "project_root": str(base),
        "policy": active_policy,
        "entries": entries,
        "entry_count": len(entries),
        "digest": digest_text(encoded),
        "captured_at": now(),
    }


def compare_manifests(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old, new = before.get("entries", {}), after.get("entries", {})
    deleted = sorted(set(old) - set(new))
    added = sorted(set(new) - set(old))
    modified = sorted(path for path in set(old) & set(new) if old[path] != new[path])
    renamed: list[dict[str, str]] = []
    remaining_added = set(added)
    remaining_deleted = set(deleted)
    by_fingerprint: dict[str, list[str]] = {}
    for path in added:
        by_fingerprint.setdefault(json.dumps(new[path], sort_keys=True), []).append(path)
    for old_path in deleted:
        matches = by_fingerprint.get(json.dumps(old[old_path], sort_keys=True), [])
        match = next((item for item in matches if item in remaining_added), None)
        if match is not None:
            renamed.append({"from": old_path, "to": match})
            remaining_added.remove(match)
            remaining_deleted.remove(old_path)
    changed_paths = sorted(set(modified) | remaining_added | remaining_deleted | {item for rename in renamed for item in rename.values()})
    return {"added": sorted(remaining_added), "modified": modified, "deleted": sorted(remaining_deleted), "renamed": renamed, "changed_paths": changed_paths, "change_count": len(modified) + len(remaining_added) + len(remaining_deleted) + len(renamed)}


def manifest_snapshot_ref(manifest: dict[str, Any]) -> str:
    """Return the canonical immutable reference for one captured manifest."""
    manifest_digest = str(manifest.get("digest") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_digest):
        raise ValueError("manifest digest must be a SHA-256 value")
    if not isinstance(manifest.get("policy"), dict) or not isinstance(manifest.get("entries"), dict):
        raise ValueError("manifest snapshot requires entries and policy")
    # ``manifest.digest`` deliberately fingerprints project entries only.  A
    # snapshot must also pin the frozen ignore policy and root, otherwise two
    # captures could have the same entries but different reconciliation scope.
    snapshot_digest = digest_text(json.dumps({
        "schema": manifest.get("schema"),
        "project_root": manifest.get("project_root"),
        "policy": manifest.get("policy"),
        "entries": manifest.get("entries"),
        "entry_count": manifest.get("entry_count"),
        "manifest_digest": manifest_digest,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return f"{MANIFEST_SNAPSHOT_PREFIX}{snapshot_digest}"


def _manifest_snapshot_path(task_dir: Path, reference: object, label: str) -> Path:
    match = MANIFEST_SNAPSHOT_REF_RE.fullmatch(str(reference or "").strip().lower())
    if not match:
        raise ValueError(f"{label} must be a canonical manifest snapshot reference")
    return _contained_path(
        task_dir,
        task_dir / "snapshots" / f"{match.group(1)}.json",
        label,
    )


def _ledger_root_for_artifact(task_dir: Path) -> Path:
    """Resolve the one database ledger that owns a task artifact directory."""
    for candidate in (task_dir, *task_dir.parents):
        if candidate.name == "cortex" and (candidate / "cortex.db").is_file():
            return candidate
    raise ValueError("task artifact directory is not owned by a Cortex SQLite ledger")


def _validate_manifest_snapshot(manifest: Any, reference: object, label: str) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ValueError(f"{label} is invalid")
    expected = manifest_snapshot_ref(manifest)
    if expected != str(reference or "").strip().lower():
        raise ValueError(f"{label} digest does not match its reference")
    if manifest.get("schema") != TRACKER_POLICY["schema"]:
        raise ValueError(f"{label} schema is not supported")
    if not isinstance(manifest.get("entries"), dict) or not isinstance(manifest.get("policy"), dict):
        raise ValueError(f"{label} is missing entries or policy")
    return manifest


def store_manifest_snapshot(task_dir: Path, manifest: dict[str, Any]) -> str:
    """Persist one content-addressed immutable manifest in SQLite and return its ref."""
    # Capture time is diagnostic metadata, not project state.  Omitting it is
    # what lets independent captures of the same state reuse one immutable
    # file instead of producing byte-different duplicates.
    snapshot = dict(manifest)
    snapshot.pop("captured_at", None)
    _json_text(snapshot, label="manifest snapshot", max_bytes=MAX_MANIFEST_BYTES)
    reference = manifest_snapshot_ref(manifest)
    db_put_manifest_snapshot(_ledger_root_for_artifact(task_dir), reference, str(manifest["digest"]), snapshot)
    return reference


def load_manifest_snapshot(task_dir: Path, reference: object, label: str) -> dict[str, Any]:
    if not MANIFEST_SNAPSHOT_REF_RE.fullmatch(str(reference or "").strip().lower()):
        raise ValueError(f"{label} must be a canonical manifest snapshot reference")
    manifest = db_get_manifest_snapshot(_ledger_root_for_artifact(task_dir), str(reference).strip().lower())
    if manifest is None:
        raise ValueError(f"{label} is unavailable")
    _json_text(manifest, label=label, max_bytes=MAX_MANIFEST_BYTES)
    return _validate_manifest_snapshot(manifest, reference, label)


def task_manifest_baseline(task_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Load the task start snapshot from the canonical SQLite ledger."""
    reference = state.get("initial_manifest_ref")
    if reference:
        return load_manifest_snapshot(task_dir, reference, "task baseline manifest")
    raise ValueError("task is missing its canonical baseline manifest")


def attempt_manifest_baseline(task_dir: Path, attempt: dict[str, Any]) -> dict[str, Any]:
    """Load an attempt start snapshot from the canonical SQLite ledger."""
    reference = attempt.get("result_baseline_ref")
    if reference:
        return load_manifest_snapshot(task_dir, reference, "attempt result baseline")
    raise ValueError("attempt is missing its canonical result baseline")


def establish_task_manifest_baseline(task_dir: Path, state: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Start or restart the active-task file-delta contract from current state."""
    baseline = capture_project_manifest(project_root)
    reference = store_manifest_snapshot(task_dir, baseline)
    state["initial_manifest_ref"] = reference
    state["initial_manifest_digest"] = baseline["digest"]
    state["manifest_snapshot_cleanup"] = {"status": "active", "at": now()}
    return baseline


def cleanup_completed_manifest_snapshots(task_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Delete unreferenced snapshots after a completed task's terminal state is durable."""
    if state.get("status") != "completed":
        raise ValueError("manifest snapshots may be cleaned only after task completion")
    removed_count = db_delete_task_manifest_snapshots(
        _ledger_root_for_artifact(task_dir), str(state.get("task_id") or "")
    )
    state["manifest_snapshot_cleanup"] = {
        "status": "completed",
        "removed_count": removed_count,
        "at": now(),
    }
    return dict(state["manifest_snapshot_cleanup"])


def reconcile_manifest(task_dir: Path, state: dict[str, Any], reported_paths: list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        state.get("status") == "completed"
        and (state.get("manifest_snapshot_cleanup") or {}).get("status") == "completed"
    ):
        checkpoint = state.get("closed_manifest_receipt") or state.get("final_manifest_receipt")
        if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("comparison"), dict):
            raise ValueError("completed task has no retained manifest receipt")
        task = load_task_definition(task_dir, state)
        current = capture_project_manifest(Path(task["project_root"]))
        if current.get("digest") != checkpoint.get("current_digest"):
            raise ValueError("project files changed after task completion; create a corrective task instead")
        supplied = {Path(str(item)).as_posix().removeprefix("./") for item in reported_paths}
        changed_paths = list((checkpoint.get("comparison") or {}).get("changed_paths") or [])
        missing = sorted(set(changed_paths) - supplied)
        return {
            **checkpoint,
            "reported_paths": sorted(supplied),
            "unaccounted_paths": missing,
            "complete": not missing,
            "created_at": now(),
        }, current
    baseline = task_manifest_baseline(task_dir, state)
    current = capture_project_manifest(Path(baseline["project_root"]), policy=baseline.get("policy"))
    comparison = compare_manifests(baseline, current)
    supplied = {Path(str(item)).as_posix().removeprefix("./") for item in reported_paths}
    missing = sorted(set(comparison["changed_paths"]) - supplied)
    receipt = {
        "schema": TRACKER_POLICY["schema"],
        "baseline_digest": baseline["digest"],
        "current_digest": current["digest"],
        "current_entry_count": current["entry_count"],
        "comparison": comparison,
        "reported_paths": sorted(supplied),
        "unaccounted_paths": missing,
        "complete": not missing,
        "created_at": now(),
    }
    return receipt, current


def remove_active_mapping(root: Path, task_id: str, thread_id: str) -> None:
    try:
        activations = _activation_records(root)
        changed = False
        for key, record in activations.items():
            if isinstance(record, dict) and record.get("task_id") == task_id:
                record["task_id"] = None
                record.pop("initialized_at", None)
                activations[key] = record
                changed = True
        if changed:
            _write_activation_records(root, activations)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    try:
        bindings = _host_session_bindings(root)
        if bindings["tasks"].pop(task_id, None) is not None:
            bindings["updated_at"] = now()
            if bindings["tasks"]:
                db_put_global(root, "host_sessions", bindings)
            else:
                db_delete_global(root, "host_sessions")
    except (OSError, ValueError, json.JSONDecodeError):
        pass


def authorize(state: dict[str, Any], params: dict[str, Any]) -> None:
    authorize_principal(state, params)
    activation_params = dict(params)
    if not activation_params.get("thread_id") and state.get("thread_id"):
        activation_params["thread_id"] = state["thread_id"]
    require_activation(activation_params, state.get("task_id"))


def _canonical_principal(value: object) -> str:
    """Normalize the host's two spellings for the main/root coordinator."""
    principal = str(value or "").strip()
    return "root" if principal == "/root" else principal


def authorize_principal(state: dict[str, Any], params: dict[str, Any]) -> None:
    expected = str(state.get("principal") or "local")
    supplied_principal = str(params.get("principal") or "").strip()
    supplied_thread = str(params.get("thread_id") or "").strip()
    bound_thread = str(state.get("thread_id") or "").strip()

    if supplied_principal:
        if expected != "local" and _canonical_principal(supplied_principal) != _canonical_principal(expected):
            raise ValueError("task is owned by a different principal")
        # A resumed root coordinator can be represented by `/root` after a
        # host turn while the durable task was initialized as `root`.  Keep
        # the durable owner authoritative for subsequent activation lookup
        # and receipts instead of treating the spelling change as a new
        # principal.
        if expected != "local" and supplied_principal != expected:
            params["principal"] = expected
        if supplied_thread and bound_thread and supplied_thread != bound_thread:
            raise ValueError("task is bound to a different thread")
        return

    # principal and thread_id are distinct identities. A caller that supplies
    # only the exact bound thread may recover the task principal, but an
    # arbitrary thread value must never be compared to the principal string.
    if supplied_thread:
        if bound_thread and supplied_thread == bound_thread:
            try:
                inferred = {"thread_id": supplied_thread, "principal": expected}
                if activation_record(ledger_root(params), inferred, state.get("task_id")):
                    return
            except (OSError, ValueError, TypeError):
                pass
        raise ValueError("task is bound to a different thread")

    if not supplied_principal and not supplied_thread:
        # Native model calls occasionally omit the repeated identity fields
        # after activation.  Recover only when this task is still bound to
        # the exact active activation recorded in the ledger; otherwise keep
        # the authentication boundary fail-closed.
        try:
            inferred = {
                "thread_id": str(state.get("thread_id") or ""),
                "principal": expected,
            }
            if state.get("thread_id") and activation_record(
                ledger_root(params), inferred, state.get("task_id")
            ):
                return
        except (OSError, ValueError, TypeError):
            pass
        raise ValueError("principal or thread_id is required for this task")


def ledger_root(params: dict[str, Any] | None = None) -> Path:
    base = select_project_root(params)
    path = _contained_path(base, base / ".codex" / "cortex", "Cortex root")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    ensure_ledger_database(path)
    # The remaining filesystem tree contains only worker-facing artifacts.
    # Mutable task/lane state is exclusively in cortex.db.
    (path / "tasks").mkdir(exist_ok=True, mode=0o700)
    (path / "tasks").chmod(0o700)
    (path / "lanes").mkdir(exist_ok=True, mode=0o700)
    (path / "lanes").chmod(0o700)
    return path


def activation_key(params: dict[str, Any]) -> str:
    principal = str(params.get("principal") or "").strip()
    # Public orchestration owns a server-generated principal for each task. Keep activation
    # records keyed by that unique identity even when the host session binding
    # is shared by more than one task in the same Codex thread.
    key = principal if principal.startswith("orchestration-task-") else str(params.get("thread_id") or principal).strip()
    if not key:
        raise ValueError("explicit orchestration activation requires thread_id or principal")
    return redact(key, 256)


def activation_path(root: Path) -> Path:
    """Compatibility label only; activations now reside in ``cortex.db``."""
    return root / "cortex.db"


def _activation_records(root: Path) -> dict[str, Any]:
    return db_get_global(root, "activations", {})


def _write_activation_records(root: Path, activations: dict[str, Any]) -> None:
    if activations:
        db_put_global(root, "activations", activations)
    else:
        db_delete_global(root, "activations")


def activation_record(root: Path, params: dict[str, Any], task_id: str | None = None) -> dict[str, Any] | None:
    key = activation_key(params)
    record = _activation_records(root).get(key)
    if not record or record.get("schema") != SCHEMA or record.get("coordinator") != "main" or record.get("mode") != "main-orchestrator":
        return None
    bound_task = record.get("task_id")
    if bound_task and task_id and bound_task != task_id:
        return None
    return record


def require_activation(params: dict[str, Any], task_id: str | None = None) -> dict[str, Any]:
    root = ledger_root(params)
    record = activation_record(root, params, task_id)
    if not record:
        raise ValueError(f"orchestration is inactive; {SKILL_ROUTE_HINT} first")
    return record


def activate_orchestration(params: dict[str, Any]) -> dict[str, Any]:
    if "agent" in params:
        raise ValueError("activation does not accept an agent profile")
    activation_token = str(
        params.get("user_command")
        or params.get("canonical_token")
        or params.get("command")
        or ""
    ).strip()
    if not activation_token:
        return {
            "active": False,
            "next_action": f"{SKILL_ROUTE_HINT}, then retry the Cortex activation route",
            "recoverable": True,
            "ledger_root": str(ledger_root(params)),
        }
    if activation_token != ACTIVATION_COMMAND:
        raise ValueError(f"explicit orchestration activation is owned by the Cortex skill route; {SKILL_ROUTE_HINT}")
    principal = str(params.get("principal", "")).strip()
    thread_id = str(params.get("thread_id", "")).strip()
    if not principal or not thread_id:
        return {
            "active": False,
            "next_action": "retry activate_orchestration with both principal and thread_id",
            "recoverable": True,
            "ledger_root": str(ledger_root(params)),
        }
    mode = "main-orchestrator"
    root = ledger_root(params)
    key = activation_key(params)
    with state_lock(root):
        activations = _activation_records(root)
        activations[key] = {
            "schema": SCHEMA,
            "thread_id": redact(thread_id, 256),
            "principal": redact(principal, 256),
            "coordinator": "main",
            "mode": mode,
            "parent_project_operations": "delegated",
            "worker_visibility": "hidden",
            "worker_return_route": "main_chat",
            "identity_assurance": "caller_asserted_principal_and_thread",
            "dispatch_attestation": "not_host_attested",
            "task_id": None,
            "activated_at": now(),
        }
        _write_activation_records(root, activations)
        return {"active": True, "key": key, "activation": activations[key], "ledger_root": str(root)}


def deactivate_orchestration(params: dict[str, Any]) -> dict[str, Any]:
    if str(params.get("user_command", "")).strip() != NORMAL_COMMAND:
        raise ValueError("explicit normal-mode transition is owned by the Cortex skill route; use `$cortex:orchestrator normal`")
    root = ledger_root(params)
    key = activation_key(params)
    with state_lock(root):
        activations = _activation_records(root)
        removed = activations.pop(key, None)
        _write_activation_records(root, activations)
        return {"active": False, "key": key, "removed": bool(removed)}


def activation_status(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    if not str(params.get("thread_id") or params.get("principal") or "").strip():
        activations = _activation_records(root)
        valid = [
            item for item in activations.values()
            if isinstance(item, dict) and item.get("schema") == SCHEMA
            and item.get("coordinator") == "main" and item.get("mode") == "main-orchestrator"
        ]
        if len(valid) == 1:
            return {"active": True, "activation": valid[0], "ledger_root": str(root), "identity_inferred": True}
        return {
            "active": False,
            "activation": None,
            "ledger_root": str(root),
            "identity_inferred": False,
            "reason": "activation_identity_ambiguous" if valid else "no_active_orchestration",
            "candidate_count": len(valid),
            "next_action": "provide_principal_or_thread_id" if valid else "activate_orchestration",
        }
    record = activation_record(root, params)
    return {"active": bool(record), "activation": record, "ledger_root": str(root), "identity_inferred": False}


def classify_task(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    require_activation(params)
    with state_lock(root):
        result = classify(params)
        receipt_id = f"classification-{secrets.token_hex(12)}"
        key = activation_key(params)
        payload = {
            "schema": SCHEMA,
            "classification_id": receipt_id,
            "activation_key": key,
            "complexity": result["complexity"],
            "requirements_digest": digest_text(json.dumps(params.get("requirements", []), sort_keys=True)),
            # The receipt is the authoritative classification contract.  Keep
            # the bounded task requirements here so init_task need not ask the
            # coordinator to reproduce a second, byte-identical copy.
            "requirements": [redact(item, 500) for item in params.get("requirements", [])][:100],
            "classification": result,
            "created_at": now(),
        }
        db_put_classification(root, payload)
        return {**result, "classification_id": receipt_id}


@contextlib.contextmanager
def state_lock(root: Path) -> Iterator[None]:
    """Serialize one filesystem+SQLite mutation and hold its DB transaction open."""
    # Composite tools call the existing mutation primitives while holding one
    # transaction lock.  Keep the lock re-entrant inside this MCP process while
    # retaining fcntl serialization between independent MCP processes.
    stack = getattr(_STATE_LOCK_LOCAL, "stack", None)
    if stack is None:
        stack = []
        _STATE_LOCK_LOCAL.stack = stack
    if stack and stack[-1][0] == root:
        stack.append((root, stack[-1][1]))
        try:
            yield
        finally:
            stack.pop()
        return
    lock_path = root / ".state.lock"
    with lock_path.open("a+", encoding="utf-8") as stream:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        if fcntl is not None:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stack.append((root, stream))
        try:
            with db_transaction(root):
                yield
        finally:
            stack.pop()
            if fcntl is not None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_text_atomic(path: Path, text: str) -> None:
    """Replace a private regular file without following links, then fsync its parent."""
    path = _reject_symlink_ancestry(path, "atomic output", allow_missing_leaf=True)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _reject_symlink_ancestry(path.parent, "atomic output parent")
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("atomic output must replace only a regular file")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json_text(value: Any, *, label: str, max_bytes: int) -> str:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError(f"{label} size limit is invalid")
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if len(text.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} is oversized")
    return text


def write_json(path: Path, value: Any) -> None:
    write_text_atomic(
        path,
        _json_text(value, label=f"JSON document '{path.name}'", max_bytes=MAX_JSON_BYTES),
    )


def write_text_exclusive(path: Path, text: str) -> None:
    path = _reject_symlink_ancestry(path, "exclusive output", allow_missing_leaf=True)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        payload = text.encode("utf-8")
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("exclusive output write made no progress")
            written += count
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def write_text_immutable(path: Path, text: str) -> str:
    """Create one immutable private briefing and return its SHA-256 digest.

    A retry may observe the exact already-written bytes after a later
    transaction step failed; different content for the same path fails closed.
    """
    payload = text.encode("utf-8")
    if len(payload) > MAX_BRIEFING_BYTES:
        raise ValueError("dispatch briefing is oversized")
    path = _reject_symlink_ancestry(path, "dispatch briefing", allow_missing_leaf=True)
    if path.exists():
        existing = _read_private_text(path, "dispatch briefing", max_bytes=MAX_BRIEFING_BYTES)
        if existing != text:
            raise ValueError("immutable dispatch briefing already exists with different content")
    else:
        write_text_exclusive(path, text)
    os.chmod(path, 0o400, follow_symlinks=False)
    _fsync_directory(path.parent)
    return hashlib.sha256(payload).hexdigest()


def write_json_exclusive(path: Path, value: Any) -> None:
    write_text_exclusive(
        path,
        _json_text(value, label=f"JSON document '{path.name}'", max_bytes=MAX_JSON_BYTES),
    )


def preflight_journal(directory: Path) -> Path:
    journal = _reject_symlink_ancestry(directory / "journal.md", "journal", allow_missing_leaf=True)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if journal.exists() and not stat.S_ISREG(journal.lstat().st_mode):
        raise ValueError("journal must be a regular file")
    return journal


def append_journal(task_dir: Path, event: str, detail: str) -> None:
    journal = preflight_journal(task_dir)
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(journal, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("journal must be a regular file")
        os.fchmod(descriptor, 0o600)
        payload = f"- {now()} — **{event}**: {detail}\n".encode("utf-8")
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("journal append made no progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_journal_best_effort(directory: Path, event: str, detail: str) -> None:
    """Keep journal output opt-in rather than a side effect of ledger writes.

    SQLite records the durable event stream.  The old journal was merely a
    convenience projection, but writing it from every mutation eagerly
    created task directories and made a filesystem failure part of otherwise
    valid business work.  Explicit projection/reconciliation flows may still
    call :func:`append_journal`; normal state transitions intentionally do
    not materialize it.
    """
    del directory, event, detail


def task_index_path(root: Path) -> Path:
    """Compatibility label only; task indexes now reside in ``cortex.db``."""
    return root / "cortex.db"


def read_task_index(root: Path) -> dict[str, Any]:
    return db_task_index(root)


def _host_session_bindings_path(root: Path) -> Path:
    """Compatibility label only; host session bindings now reside in SQLite."""
    return root / "cortex.db"


def _host_session_bindings(root: Path) -> dict[str, Any]:
    payload = db_get_global(root, "host_sessions", {"schema": HOST_SESSION_SCHEMA, "tasks": {}, "updated_at": now()})
    if payload.get("schema") != HOST_SESSION_SCHEMA or not isinstance(payload.get("tasks"), dict):
        raise ValueError("host session binding registry is invalid")
    return payload


def _active_host_session_task_ids(root: Path, bindings: dict[str, Any], session_id: str) -> list[str]:
    active: list[str] = []
    for raw_task_id, bound_session in bindings.get("tasks", {}).items():
        if bound_session != session_id:
            continue
        try:
            task_id = safe_id(str(raw_task_id))
            loaded = _v3_task_state(root, task_id)
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            continue
        if loaded is not None and loaded[1].get("status") in {"active", "blocked"}:
            active.append(task_id)
    return sorted(set(active))


def bind_host_session_from_hook(project_root_value: object, task_ref_value: object, session_id_value: object) -> dict[str, Any]:
    """Bind documented hook identity without changing task authorization."""
    project = select_project_root({"project_root": str(project_root_value or "")})
    task_ref_value = safe_id(str(task_ref_value or ""))
    session_id = safe_id(str(session_id_value or ""))
    root = ledger_root({"project_root": str(project)})
    with state_lock(root):
        registry = _operation_registry(root)
        matches = [
            str(task_id)
            for task_id, record in registry.get("tasks", {}).items()
            if isinstance(record, dict)
            and isinstance(record.get("start"), dict)
            and record["start"].get("task_ref") == task_ref_value
        ]
        if len(matches) != 1:
            return {"bound": False, "reason": "task_ref_unavailable"}
        task_id = safe_id(matches[0])
        loaded = _v3_task_state(root, task_id)
        if loaded is None or loaded[1].get("status") not in {"active", "blocked"}:
            return {"bound": False, "reason": "task_inactive"}
        bindings = _host_session_bindings(root)
        previous = str(bindings["tasks"].get(task_id) or "")
        if previous and previous != session_id:
            return {"bound": False, "reason": "session_conflict"}
        bindings["tasks"][task_id] = session_id
        bindings["updated_at"] = now()
        db_put_global(root, "host_sessions", bindings)
        active = _active_host_session_task_ids(root, bindings, session_id)
        return {"bound": True, "task_id": task_id, "ambiguous": len(active) != 1}


def bind_host_worker_from_hook(
    project_root_value: object,
    task_id_value: object,
    parent_session_id_value: object,
    host_task_name_value: object,
    host_agent_id_value: object,
    host_model_value: object,
) -> dict[str, Any]:
    """Durably bind a documented SubagentStart event to one pending attempt.

    Codex reports the parent session id, opaque native agent id, generic
    ``agent_type=default``, and actual model for dynamically named workers. The
    parent-session registry plus the required sequential spawn order assigns
    that event to the first still-awaiting issued dispatch. Hosts that expose
    the native task key are matched exactly instead. This lets compaction
    recovery distinguish a pending dispatch from a worker that already exists
    without reading a host transcript or trusting model-authored text.
    """
    project = select_project_root({"project_root": str(project_root_value or "")})
    task_id = safe_id(str(task_id_value or ""))
    parent_session_id = safe_id(str(parent_session_id_value or ""))
    raw_host_task_name = str(host_task_name_value or "").strip()
    host_task_name = safe_id(raw_host_task_name)
    host_agent_id = str(host_agent_id_value or "").strip()
    if not HOST_AGENT_ID_RE.fullmatch(host_agent_id):
        return {"bound": False, "reason": "host_agent_id_invalid"}
    host_model = _v3_model(host_model_value)
    if not host_model:
        return {"bound": False, "reason": "host_model_unavailable"}
    root = ledger_root({"project_root": str(project)})
    bindings = _host_session_bindings(root)
    if bindings.get("tasks", {}).get(task_id) != parent_session_id:
        return {"bound": False, "reason": "parent_session_mismatch"}
    loaded = _v3_task_state(root, task_id)
    if loaded is None:
        return {"bound": False, "reason": "task_unavailable"}
    _, state, _ = loaded
    if raw_host_task_name == "default":
        matches = [
            item for item in state.get("attempts", [])
            if not item.get("invalidated")
            and item.get("status") == AWAITING_HOST_SPAWN
            and str(item.get("expected_model") or item.get("selected_model") or "") == host_model
        ]
        if len(matches) == 1:
            host_task_name = str((matches[0].get("spawn_request") or {}).get("task_name") or "")
        elif len(matches) > 1:
            return {"bound": False, "reason": "exact_dispatch_identity_required"}
        else:
            matches = [
                item for item in state.get("attempts", [])
                if not item.get("invalidated")
                and item.get("status") == "running"
                and str((item.get("host_spawn") or {}).get("agent_id") or "") == host_agent_id
                and str((item.get("host_spawn") or {}).get("model") or "") == host_model
            ]
            if len(matches) == 1:
                host_task_name = str((matches[0].get("spawn_request") or {}).get("task_name") or "")
            elif len(matches) > 1:
                return {"bound": False, "reason": "exact_dispatch_identity_required"}
    else:
        matches = [
            item for item in state.get("attempts", [])
            if not item.get("invalidated")
            and str((item.get("spawn_request") or {}).get("task_name") or "") == host_task_name
            and item.get("status") in {AWAITING_HOST_SPAWN, "running"}
        ]
    if len(matches) != 1:
        return {"bound": False, "reason": "dispatch_identity_unavailable"}
    attempt = matches[0]
    existing = attempt.get("host_spawn") or {}
    if attempt.get("status") == "running":
        if existing.get("agent_id") == host_agent_id and existing.get("task_name") == host_task_name:
            if attempt.get("host_stop_outcome") == "awaiting_user":
                with state_lock(root):
                    resumed_loaded = _v3_task_state(root, task_id)
                    if resumed_loaded is None:
                        return {"bound": False, "reason": "task_unavailable"}
                    resumed_task_dir, resumed_state, _ = resumed_loaded
                    resumed_attempt = _attempt(resumed_state, str(attempt.get("attempt_id") or ""))
                    if resumed_attempt.get("host_stop_outcome") == "awaiting_user" and _open_blocking_questions(
                        resumed_task_dir, resumed_state, str(resumed_attempt.get("attempt_id") or "")
                    ):
                        return {"bound": False, "reason": "worker_question_still_open"}
                    for field in (
                        "host_stopped_at", "host_stop_outcome", "host_question_refs",
                    ):
                        resumed_attempt.pop(field, None)
                    resumed_attempt["host_resumed_at"] = now()
                    package = _delegation_package(resumed_task_dir, resumed_state["task_id"], str(resumed_attempt["attempt_id"]))
                    package["spawn_status"] = "resumed_existing_worker"
                    package["host_resumed_at"] = resumed_attempt["host_resumed_at"]
                    db_put_worker_session(root, {
                        "task_id": resumed_state["task_id"],
                        "attempt_id": str(resumed_attempt["attempt_id"]),
                        "host_agent_id": host_agent_id,
                        "host_task_name": host_task_name,
                        "host_tool": str((resumed_attempt.get("host_spawn") or {}).get("tool") or "spawn_agent"),
                        "status": "running",
                        "resumable": True,
                        "started_at": (resumed_attempt.get("host_spawn") or {}).get("confirmed_at"),
                    })
                    _write_delegation_package(resumed_task_dir, resumed_state["task_id"], str(resumed_attempt["attempt_id"]), package)
                    save_state(
                        resumed_task_dir,
                        resumed_task_dir / "state.sqlite",
                        resumed_state,
                        "host_resume_after_question",
                        str(resumed_attempt["attempt_id"]),
                    )
            return {
                "bound": True,
                "idempotent": True,
                "attempt_id": attempt.get("attempt_id"),
                "host_agent_id": host_agent_id,
            }
        return {"bound": False, "reason": "running_dispatch_mismatch"}
    result = confirm_host_spawn({
        "project_root": str(project),
        "task_id": task_id,
        "principal": state.get("principal"),
        "thread_id": state.get("thread_id"),
        "expected_revision": state.get("revision"),
        "attempt_id": attempt.get("attempt_id"),
        "host_tool": (attempt.get("spawn_request") or {}).get("host_tool") or "spawn_agent",
        "host_agent_id": host_agent_id,
        "host_task_name": host_task_name,
        "host_model": host_model,
        "host_reasoning_effort": (attempt.get("spawn_request") or {}).get("reasoning_effort"),
    })
    return {
        "bound": bool(result.get("confirmed")),
        "idempotent": bool(result.get("idempotent")),
        "attempt_id": attempt.get("attempt_id"),
        "host_agent_id": host_agent_id,
        **({"reason": result.get("reason") or "host_spawn_confirmation_failed"} if not result.get("confirmed") else {}),
    }


def finalize_host_worker_stop_from_hook(
    project_root_value: object,
    task_id_value: object,
    parent_session_id_value: object,
    host_agent_id_value: object,
) -> dict[str, Any]:
    """Persist a documented SubagentStop without trusting model-authored text.

    A worker that has already published a report remains eligible for the
    coordinator's normal ``continue_orchestration`` receipt. A worker paused
    on a durable question remains addressable through the exact persisted host
    identity. A worker stopped without either a report or question is terminal
    failed; the coordinator must submit that exact dispatch's failure receipt
    so the bounded gate retry policy can decide whether to re-dispatch.
    """
    project = select_project_root({"project_root": str(project_root_value or "")})
    task_id = safe_id(str(task_id_value or ""))
    parent_session_id = safe_id(str(parent_session_id_value or ""))
    host_agent_id = str(host_agent_id_value or "").strip()
    if not HOST_AGENT_ID_RE.fullmatch(host_agent_id):
        return {"updated": False, "reason": "host_agent_id_invalid"}
    root = ledger_root({"project_root": str(project)})
    with state_lock(root):
        bindings = _host_session_bindings(root)
        if bindings.get("tasks", {}).get(task_id) != parent_session_id:
            return {"updated": False, "reason": "parent_session_mismatch"}
        loaded = _v3_task_state(root, task_id)
        if loaded is None:
            return {"updated": False, "reason": "task_unavailable"}
        task_dir, state, _ = loaded
        matches = [
            item for item in state.get("attempts", [])
            if not item.get("invalidated")
            and str((item.get("host_spawn") or {}).get("agent_id") or "") == host_agent_id
        ]
        if len(matches) != 1:
            return {"updated": False, "reason": "host_worker_identity_unavailable"}
        attempt = matches[0]
        attempt_id = str(attempt.get("attempt_id") or "")
        if attempt.get("status") in TERMINAL_ATTEMPT_STATUSES:
            return {
                "updated": False,
                "idempotent": True,
                "reason": "attempt_already_terminal",
                "attempt_id": attempt_id,
                "status": attempt.get("status"),
            }
        if attempt.get("status") != "running":
            return {"updated": False, "reason": "attempt_not_running", "attempt_id": attempt_id}

        stopped_at = now()
        open_questions = _open_blocking_questions(task_dir, state, attempt_id)
        report_index = _report_index(report_bus_paths(task_dir), state["task_id"])
        report_refs = [
            str(item.get("report_id"))
            for item in report_index.get("reports", [])
            if isinstance(item, dict)
            and item.get("attempt_id") == attempt_id
            and str(item.get("report_id") or "").strip()
        ]
        attempt["host_stopped_at"] = stopped_at
        package = _delegation_package(task_dir, state["task_id"], attempt_id)
        if report_refs:
            attempt["host_stop_outcome"] = "report_recorded"
            # The report is the durable completion signal.  Do not retain the
            # provisional resumability flag written when the native worker
            # started; doing so would make compaction handoff advertise a
            # follow-up target instead of consuming the persisted report.
            attempt["host_resumable"] = False
            attempt["host_report_refs"] = report_refs
            package["spawn_status"] = "stopped_after_report"
            package["host_stopped_at"] = stopped_at
            package["resumable"] = False
            package["report_refs"] = report_refs
            event = "host_stop_after_report"
            detail = f"{attempt_id}: {', '.join(report_refs)}"
            outcome = "report_recorded"
        elif open_questions:
            question_refs = [str(item.get("question_id")) for item in open_questions]
            attempt["host_stop_outcome"] = "awaiting_user"
            attempt["host_resumable"] = True
            attempt["host_question_refs"] = question_refs
            package["spawn_status"] = "paused_for_question"
            package["host_stopped_at"] = stopped_at
            package["resumable"] = True
            package["question_refs"] = question_refs
            event = "host_stop_for_question"
            detail = f"{attempt_id}: {', '.join(question_refs)}"
            outcome = "awaiting_user"
        else:
            reason = "native_worker_stopped_without_report"
            attempt["status"] = "failed"
            attempt["finalized_at"] = stopped_at
            attempt["finalization_reason"] = reason
            attempt["host_stop_outcome"] = reason
            attempt["host_resumable"] = False
            package["spawn_status"] = "stopped_without_report"
            package["host_stopped_at"] = stopped_at
            package["resumable"] = False
            package["failure_reason"] = reason
            package["dispatch_ref"] = str(attempt.get("dispatch_ref") or "")
            event = "host_stop_without_report"
            detail = f"{attempt_id}: {reason}"
            outcome = reason
        session_status = (
            "completed" if report_refs else "idle_resumable" if open_questions else "terminated_unavailable"
        )
        db_put_worker_session(root, {
            "task_id": state["task_id"], "attempt_id": attempt_id,
            "host_agent_id": host_agent_id,
            "host_task_name": str((attempt.get("host_spawn") or {}).get("task_name") or ""),
            "host_tool": str((attempt.get("host_spawn") or {}).get("tool") or "spawn_agent"),
            "status": session_status, "resumable": bool(open_questions),
            "started_at": (attempt.get("host_spawn") or {}).get("confirmed_at"),
            **({"terminated_at": stopped_at} if report_refs or not open_questions else {}),
        })
        _write_delegation_package(task_dir, state["task_id"], attempt_id, package)
        save_state(task_dir, task_dir / "state.sqlite", state, event, detail)
        return {
            "updated": True,
            "attempt_id": attempt_id,
            "outcome": outcome,
            "report_refs": report_refs,
            "question_refs": [str(item.get("question_id")) for item in open_questions],
        }


def task_paths(task_id: str, params: dict[str, Any]) -> tuple[Path, Path, Path]:
    root = ledger_root(params)
    normalized = safe_id(task_id)
    tasks_dir = root / "tasks"
    task_dir = db_task_artifact_path(root, normalized)
    if task_dir is None:
        task_dir = _contained_path(tasks_dir, tasks_dir / f"missing-{normalized}", "task directory")
    if task_dir.exists():
        _reject_symlink_ancestry(task_dir, "task directory")
    return root, task_dir, task_dir / "state.sqlite"


def allocate_task_directory(root: Path, task_id: str) -> tuple[int, Path]:
    tasks_dir = root / "tasks"
    number = db_next_task_number(root)
    return number, tasks_dir / f"{number:04d}-{task_id}"


def lane_paths(lane_id: str, params: dict[str, Any]) -> tuple[Path, Path, Path]:
    root = ledger_root(params)
    normalized = safe_id(lane_id)
    lane_dir = root / "lanes" / normalized
    if lane_dir.exists() and lane_dir.is_symlink():
        raise ValueError("lane directory must not be a symlink")
    return root, lane_dir, lane_dir / "state.sqlite"


def load_lane(lane_id: str, params: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    root, lane_dir, state_path = lane_paths(lane_id, params)
    loaded = db_get_lane(root, safe_id(lane_id))
    if loaded is None:
        raise ValueError(f"lane '{lane_id}' does not exist")
    _, state = loaded
    if state.get("schema") != SCHEMA:
        raise ValueError("lane ledger schema is not supported; create a new lane")
    return root, lane_dir, state


def parse_expiry(value: object, label: str = "expires_at") -> datetime:
    if not value:
        raise ValueError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{label} must be timezone-aware RFC 3339") from exc
    return parsed


def sanitize_structured(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): "<REDACTED>" if SENSITIVE_KEY_RE.search(str(key)) else sanitize_structured(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_structured(item) for item in value[:100]]
    return redact(value, 2000)


def _is_sensitive_log_key(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(value).lower())
    return normalized in SENSITIVE_LOG_KEY_NAMES or any(
        normalized.endswith(suffix) for suffix in ("apikey", "token", "secret", "password", "privatekey")
    )


def _sanitize_tool_error_value(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> Any:
    """Bound and redact arbitrary tool input before it reaches the error log."""
    budget = budget if budget is not None else [512]
    if budget[0] <= 0 or depth > 6:
        return "<TRUNCATED>"
    budget[0] -= 1
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100:
                result["<TRUNCATED_KEYS>"] = "<TRUNCATED>"
                break
            key_text = redact(key, 256)
            result[key_text] = "<REDACTED>" if _is_sensitive_log_key(key) else _sanitize_tool_error_value(item, depth=depth + 1, budget=budget)
        return result
    if isinstance(value, (list, tuple)):
        items = [_sanitize_tool_error_value(item, depth=depth + 1, budget=budget) for item in value[:100]]
        if len(value) > 100:
            items.append("<TRUNCATED>")
        return items
    if value is None or isinstance(value, (bool, int, float)):
        return value if not isinstance(value, float) or math.isfinite(value) else "<NON_FINITE>"
    return redact(value, 2000)


def _bounded_error_input(value: Any) -> Any:
    sanitized = _sanitize_tool_error_value(value)
    try:
        encoded = json.dumps(sanitized, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        encoded = json.dumps(redact(repr(value), MAX_TOOL_ERROR_LOG_INPUT_BYTES), ensure_ascii=False)
    if len(encoded.encode("utf-8")) <= MAX_TOOL_ERROR_LOG_INPUT_BYTES:
        return sanitized
    return {
        "truncated": True,
        "preview": redact(encoded[:MAX_TOOL_ERROR_LOG_INPUT_BYTES], MAX_TOOL_ERROR_LOG_INPUT_BYTES),
    }


def _tool_error_log_path() -> Path:
    """Return the private per-user system log path for MCP tool errors."""
    return Path.home() / ".codex" / "logs" / "cortex-tool-errors.jsonl"


def _tool_error_context(request: Any, request_id: Any, raw_line: str) -> dict[str, Any]:
    request_dict = request if isinstance(request, dict) else {}
    params = request_dict.get("params") if isinstance(request_dict.get("params"), dict) else {}
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    request_meta = request_dict.get("_meta") if isinstance(request_dict.get("_meta"), dict) else {}
    params_meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    source = {**request_meta, **params_meta, **params, **arguments}
    ids: dict[str, Any] = {}
    for key in (
        "id", "call_id", "task_id", "attempt_id", "question_id", "submission_id",
        "status_receipt", "report_receipt", "verification_id", "lane_id", "run_id",
        "host_agent_id", "turn_id",
    ):
        value = request_id if key == "id" else source.get(key)
        if value not in (None, ""):
            ids[key] = redact(value, 256)
    thread_id = source.get("thread_id") or request_dict.get("thread_id")
    session_id = source.get("session_id") or source.get("chat_session_id") or request_dict.get("session_id") or thread_id
    return {
        "method": redact(request_dict.get("method", ""), 128) or None,
        "tool": redact(params.get("name", ""), 128) or None,
        "chat_session_id": redact(session_id, 256) if session_id else None,
        "thread_id": redact(thread_id, 256) if thread_id else None,
        "request_id": redact(request_id, 256) if request_id is not None else None,
        "ids": ids,
        "input": _bounded_error_input(arguments if arguments else params if params else raw_line),
    }


def log_tool_error(request: Any, request_id: Any, raw_line: str, error: BaseException) -> None:
    """Append a redacted MCP tool failure without masking the original error."""
    try:
        context = _tool_error_context(request, request_id, raw_line)
        record = {
            "timestamp": now(),
            "event": "tool_error",
            "server_version": SERVER_VERSION,
            "pid": os.getpid(),
            "error_type": type(error).__name__,
            "error": redact(str(error), 2000),
            **context,
        }
        encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        path = _tool_error_log_path()
        parent = _reject_symlink_ancestry(path.parent, "tool error log parent", allow_missing_leaf=True)
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(parent, 0o700)
        _reject_symlink_ancestry(path, "tool error log", allow_missing_leaf=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            size = os.fstat(descriptor).st_size
            if size + len(encoded) > MAX_TOOL_ERROR_LOG_BYTES:
                keep_budget = max(0, MAX_TOOL_ERROR_LOG_BYTES - len(encoded))
                tail = b""
                if keep_budget and size:
                    offset = max(0, size - keep_budget)
                    os.lseek(descriptor, offset, os.SEEK_SET)
                    remaining = min(size - offset, keep_budget)
                    chunks: list[bytes] = []
                    while remaining > 0:
                        chunk = os.read(descriptor, min(65536, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    tail = b"".join(chunks)
                    if offset:
                        newline = tail.find(b"\n")
                        tail = tail[newline + 1:] if newline >= 0 else b""
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.ftruncate(descriptor, 0)
                written = 0
                while written < len(tail):
                    count = os.write(descriptor, tail[written:])
                    if count <= 0:
                        raise OSError("tool error log compaction made no progress")
                    written += count
            os.lseek(descriptor, 0, os.SEEK_END)
            written = 0
            while written < len(encoded):
                count = os.write(descriptor, encoded[written:])
                if count <= 0:
                    raise OSError("tool error log write made no progress")
                written += count
            os.fsync(descriptor)
        finally:
            if fcntl is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
    except Exception:
        # Logging must never replace the MCP error or make a bad request hang.
        # Do not write raw input to stderr or another less controlled channel.
        pass


def profiles_for_gate(gate: str) -> list[str]:
    """Return only explicitly routed profiles; unknown gates never imply a writer."""
    return routing_profiles_for_gate(PROFILES, gate)


def profile_can_own_gate(profile_name: str, gate: str) -> bool:
    return routing_profile_can_own_gate(PROFILES, profile_name, gate)


def is_analysis_task_kind(task_kind: str) -> bool:
    return task_kind in ANALYSIS_TASK_KINDS or any(
        task_kind.startswith(prefix) for prefix in ANALYSIS_TASK_KIND_PREFIXES
    )


def model_profile_class(profile_name: str) -> str:
    for class_name, profiles in MODEL_PROFILE_CLASSES.items():
        if profile_name in profiles:
            return class_name
    raise RuntimeError(f"Cortex model routing has no class for profile {profile_name}")


def higher_effort(*efforts: str) -> str:
    return max(efforts, key=REASONING_EFFORT_ORDER.__getitem__)


def resolve_dispatch_route(params: dict[str, Any]) -> dict[str, Any]:
    """Public delegating entrypoint for the pure routing-policy evaluator."""
    return routing_resolve_dispatch_route(
        params,
        profiles=PROFILES,
        policy=ROUTING_POLICY,
        canonical_profile=canonical_profile,
        normalize_routing_id=normalize_routing_id,
        select_project_root=select_project_root,
    )

REPORT_FIELDS = tuple(PROFILE_CONTRACT.get("shared_worker_contract", {}).get("required_report_fields", []))
EXPECTED_REPORT_FIELDS = (
    "summary", "findings", "questions", "changed_files", "tests", "evidence", "uncertainty",
)
if REPORT_FIELDS != EXPECTED_REPORT_FIELDS:
    raise RuntimeError("bundled Cortex shared worker report contract is invalid")
REPORT_FIELD_SET = frozenset(REPORT_FIELDS)


INTENT_CLOSURE_GATES = AVAILABLE_GATES - {"scope", "discover"}
PRODUCT_SURFACE_PATTERNS = (
    r"\blanding(?:\s+page)?\b", r"\bweb\s*site\b", r"\bhomepage\b", r"\bpage\b",
    r"\bdashboard\b", r"\bapp(?:lication)?\b", r"\binterface\b", r"\bui\b", r"\bux\b",
    r"\bredesign\b", r"\bdesign\b", r"\bлендинг\w*\b", r"\bсайт\w*\b",
    r"\bстраниц\w*\b", r"\bдашборд\w*\b", r"\bприложен\w*\b", r"\bинтерфейс\w*\b",
    r"\bредизайн\w*\b", r"\bдизайн\w*\b",
)
PRODUCT_CREATION_PATTERNS = (
    r"\bcreate\w*\b", r"\bbuild\w*\b", r"\bmake\b", r"\bdesign\w*\b",
    r"\bredesign\w*\b", r"\bimplement\w*\b", r"\bdevelop\w*\b", r"\bсозда\w*\b",
    r"\bсдела\w*\b", r"\bразработ\w*\b", r"\bспроект\w*\b", r"\bпередел\w*\b",
)
INTENT_SPECIFICITY_PATTERNS = (
    (r"\baudience\b", r"\bcustomer\w*\b", r"\bpersona\w*\b", r"\busers?\b", r"\bаудитор\w*\b", r"\bклиент\w*\b", r"\bпользовател\w*\b", r"\bдля\s+кого\b"),
    (r"\bgoal\b", r"\bpurpose\b", r"\bconversion\w*\b", r"\bsales?\b", r"\bleads?\b", r"\bsignup\w*\b", r"\bцель\w*\b", r"\bконвер\w*\b", r"\bпродаж\w*\b", r"\bлид\w*\b", r"\bрегистрац\w*\b"),
    (r"\bbrand\w*\b", r"\bcopy\b", r"\bcontent\b", r"\bassets?\b", r"\bбренд\w*\b", r"\bтекст\w*\b", r"\bконтент\w*\b", r"\bассет\w*\b", r"\bматериал\w*\b"),
    (r"\bstyle\b", r"\breference\w*\b", r"\bvisual\w*\b", r"\bdesign\s+system\b", r"\bстил\w*\b", r"\bреференс\w*\b", r"\bвизуал\w*\b", r"\bдизайн[-\s]систем\w*\b"),
    (r"\bpreserv\w*\b", r"\breplac\w*\b", r"\bonly\b", r"\bscope\b", r"\bexisting\b", r"\bсохран\w*\b", r"\bзамен\w*\b", r"\bтолько\b", r"\bобъ[её]м\w*\b", r"\bсуществующ\w*\b"),
)


def _intent_clarification_preflight(user_request: object) -> tuple[bool, str | None]:
    """Conservatively identify product-surface requests that cannot define intent on their own."""
    text = str(user_request or "").strip().lower()
    if not text:
        return True, "the exact user-authored request is missing"
    classification_text = re.sub(
        r"\[\$?cortex:orchestrator\]\([^)]*\)", " ", text, flags=re.IGNORECASE
    )
    classification_text = re.sub(r"\$cortex:orchestrator\b", " ", classification_text, flags=re.IGNORECASE)
    classification_text = re.sub(r"\s+", " ", classification_text).strip()
    if not any(re.search(pattern, classification_text) for pattern in PRODUCT_SURFACE_PATTERNS):
        return False, None
    if not any(re.search(pattern, classification_text) for pattern in PRODUCT_CREATION_PATTERNS):
        return False, None
    words = re.findall(r"[^\W_]+", classification_text, flags=re.UNICODE)
    specificity = sum(
        any(re.search(pattern, classification_text) for pattern in group)
        for group in INTENT_SPECIFICITY_PATTERNS
    )
    if len(words) <= 6:
        return True, "a short product-surface creation request does not establish the intended outcome"
    if len(words) <= 18 and specificity < 2:
        return True, "the product-surface request lacks enough audience, outcome, content, visual, or scope context"
    return False, None


def _answered_blocking_questions(task_dir: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    # Worker questions are canonical SQLite task documents.  Their legacy
    # filesystem location is only a lazy projection and may legitimately be
    # absent after a fresh answer, so it cannot decide whether the worker has
    # received the material intent needed to resume.
    return [
        item for item in _question_records(question_bus_paths(task_dir), state)
        if item.get("status") == "answered" and bool(item.get("blocking", True))
    ]


def _validate_report_decision_closure(
    task_dir: Path,
    state: dict[str, Any],
    attempt: dict[str, Any],
    report: dict[str, Any],
) -> None:
    if report.get("questions"):
        raise ValueError(
            "final report questions must be empty; publish every material user decision through worker_question "
            "and wait for its answer, or move a genuinely non-blocking evidence limitation to uncertainty"
        )
    task = load_task_definition(task_dir, state)
    if (
        task.get("intent_clarification_required")
        and attempt.get("gate") in INTENT_CLOSURE_GATES
        and not _answered_blocking_questions(task_dir, state)
    ):
        reason = str(task.get("intent_clarification_reason") or "the user request is materially underspecified")
        raise ValueError(
            "intent clarification required before this phase can report completion: " + reason
            + "; call worker_question(action=ask), return its question_ref to the coordinator, and resume this "
            "same attempt only after the user's answer"
        )


def _safe_project_relative_path(value: Any) -> str:
    text = str(value).strip().replace("\\", "/")
    path = Path(text)
    if not text or path.is_absolute() or "\x00" in text or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("report changed_files must contain safe project-relative paths")
    normalized = path.as_posix()
    if normalized == ".codex/cortex" or normalized.startswith(".codex/cortex/"):
        raise ValueError("report changed_files must not include the Cortex report bus")
    return redact(normalized, 500)


def sanitize_report_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != REPORT_FIELD_SET:
        missing = sorted(REPORT_FIELD_SET - set(value) if isinstance(value, dict) else REPORT_FIELD_SET)
        unknown = sorted(set(value) - REPORT_FIELD_SET) if isinstance(value, dict) else []
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if unknown:
            detail.append("unknown: " + ", ".join(unknown))
        raise ValueError("report must contain exactly the cortex/report/v1 fields" + (" (" + "; ".join(detail) + ")" if detail else ""))
    summary = str(value["summary"]).strip()
    if not summary:
        raise ValueError("report summary is required")
    result: dict[str, Any] = {"summary": redact(summary, 4000)}
    for field in ("findings", "questions", "evidence", "uncertainty"):
        items = value[field]
        if not isinstance(items, list) or len(items) > MAX_REPORT_ITEMS:
            raise ValueError(f"report {field} must be an array with at most {MAX_REPORT_ITEMS} items")
        result[field] = [sanitize_structured(item) for item in items]
    tests = value["tests"]
    if not isinstance(tests, list) or len(tests) > MAX_REPORT_ITEMS:
        raise ValueError(f"report tests must be an array with at most {MAX_REPORT_ITEMS} items")
    result["tests"] = []
    for item in tests:
        sanitized_item = sanitize_structured(item)
        if isinstance(item, dict) and isinstance(sanitized_item, dict) and "exit_code" in item:
            sanitized_item["exit_code"] = item["exit_code"]
        result["tests"].append(sanitized_item)
    changed_files = value["changed_files"]
    if not isinstance(changed_files, list) or len(changed_files) > MAX_REPORT_ITEMS:
        raise ValueError(f"report changed_files must be an array with at most {MAX_REPORT_ITEMS} items")
    result["changed_files"] = [_safe_project_relative_path(item) for item in changed_files]
    for field in ("summary", "findings", "questions", "tests", "evidence", "uncertainty"):
        require_internal_english(result[field], f"report {field}")
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise ValueError(f"report exceeds the {MAX_REPORT_BYTES}-byte limit")
    return result


_CLOSURE_DECISIONS = {"pass", "rework", "fail"}
_GATE_RESULT_DECISIONS = _CLOSURE_DECISIONS | {"blocked"}
_GATE_FAILURE_CLASSES = {"product", "infrastructure", "environment", "policy", "worker"}
_CLOSURE_SEVERITIES = {"P0", "P1", "P2", "P3", "info"}
_CLOSURE_STATUSES = {"open", "resolved", "waived"}


def sanitize_closure_payload(value: Any, *, actor_ids: set[str] | None = None) -> dict[str, Any]:
    """Validate and canonicalize the optional closure sibling."""
    if not isinstance(value, dict) or set(value) != {"decision", "findings", "verification", "workspace"}:
        raise ValueError("closure must contain exactly decision, findings, verification, and workspace")
    decision = str(value["decision"]).strip().lower()
    if decision not in _CLOSURE_DECISIONS:
        raise ValueError("closure decision must be pass, rework, or fail")
    raw_findings = value["findings"]
    if not isinstance(raw_findings, list):
        raise ValueError("closure findings must be an array")
    findings = []
    for item in raw_findings:
        if not isinstance(item, dict):
            raise ValueError("closure finding must be an object")
        allowed = {"fingerprint", "severity", "status", "blocking", "summary", "details", "waiver_reason", "waived_by", "waived_at", "resolved_at"}
        if set(item) - allowed:
            raise ValueError("closure finding contains unknown fields")
        fingerprint = str(item.get("fingerprint") or "").strip()
        summary = str(item.get("summary") or "").strip()
        severity = str(item.get("severity") or "")
        status = str(item.get("status") or "")
        if not fingerprint or not summary or severity not in _CLOSURE_SEVERITIES or status not in _CLOSURE_STATUSES or not isinstance(item.get("blocking"), bool):
            raise ValueError("closure finding has invalid fingerprint, severity, status, blocking, or summary")
        details = item.get("details")
        if details is not None and not isinstance(details, (str, dict, list)):
            raise ValueError("closure finding details must be structured text")
        waiver_reason = str(item.get("waiver_reason") or "").strip()
        waived_by = str(item.get("waived_by") or "").strip()
        waived_at = str(item.get("waived_at") or "").strip()
        if status == "waived":
            if not waiver_reason or not waived_by or not waived_at:
                raise ValueError("waived closure findings require waiver_reason, waived_by, and waived_at metadata")
            if waived_by.lower() in {"worker", "self", fingerprint.lower()} or (actor_ids and waived_by.lower() in {item.lower() for item in actor_ids}):
                raise ValueError("workers cannot self-waive closure findings")
        elif any(item.get(field) is not None for field in ("waiver_reason", "waived_by", "waived_at")):
            raise ValueError("waiver metadata is only valid for waived closure findings")
        finding = {"fingerprint": fingerprint, "severity": severity, "status": status, "blocking": item["blocking"], "summary": redact(summary, 4000), **({"details": details} if details is not None else {})}
        if status == "waived":
            finding.update({"waiver_reason": redact(waiver_reason, 4000), "waived_by": redact(waived_by, 400), "waived_at": redact(waived_at, 200)})
        if item.get("resolved_at") is not None:
            finding["resolved_at"] = redact(str(item["resolved_at"]).strip(), 200)
        findings.append(finding)
    verification = value["verification"]
    workspace = value["workspace"]
    if not isinstance(verification, dict) or set(verification) != {"executed", "not_executed", "required_missing", "limitations"}:
        raise ValueError("closure verification must contain executed, not_executed, required_missing, and limitations")
    if not isinstance(workspace, dict) or set(workspace) != {"modified", "untracked", "staged", "committed"}:
        raise ValueError("closure workspace must contain modified, untracked, staged, and committed")
    if workspace["committed"] not in {True, False, "not_required"}:
        raise ValueError("closure workspace committed must be true, false, or not_required")
    for field in verification:
        if not isinstance(verification[field], list): raise ValueError("closure verification fields must be arrays")
    for field in ("modified", "untracked", "staged"):
        if not isinstance(workspace[field], list): raise ValueError("closure workspace file fields must be arrays")
    return {"decision": decision, "findings": findings, "verification": verification, "workspace": workspace}


def sanitize_gate_result_payload(value: Any, *, actor_ids: set[str] | None = None) -> dict[str, Any]:
    """Validate the universal gate envelope while reusing finding semantics."""
    if not isinstance(value, dict) or set(value) != {"decision", "failure_class", "findings", "verification", "workspace"}:
        raise ValueError(
            "gate_result must contain exactly decision, failure_class, findings, verification, and workspace"
        )
    decision = str(value.get("decision") or "").strip().lower()
    failure_class = str(value.get("failure_class") or "").strip().lower()
    if decision not in _GATE_RESULT_DECISIONS:
        raise ValueError("gate_result decision must be pass, rework, fail, or blocked")
    if failure_class not in _GATE_FAILURE_CLASSES:
        raise ValueError(
            "gate_result failure_class must be product, infrastructure, environment, policy, or worker"
        )
    closure = sanitize_closure_payload(
        {
            "decision": "fail" if decision == "blocked" else decision,
            "findings": value["findings"],
            "verification": value["verification"],
            "workspace": value["workspace"],
        },
        actor_ids=actor_ids,
    )
    if decision in {"rework", "fail", "blocked"} and not (
        any(
            item.get("status") == "open" and item.get("blocking")
            for item in closure["findings"]
        )
        or closure["verification"]["required_missing"]
    ):
        raise ValueError(
            "non-pass gate_result requires an open blocking finding or required_missing verification"
        )
    return {**closure, "decision": decision, "failure_class": failure_class}


def _planning_identifier(value: Any, label: str) -> str:
    identifier = str(value or "").strip()
    if not SAFE_ID_RE.fullmatch(identifier):
        raise ValueError(f"{label} must be a lowercase safe identifier")
    return identifier


def _planning_text(value: Any, label: str, *, maximum: int = 4000) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return redact(text, maximum)


def _planning_string_list(value: Any, label: str, *, maximum: int = 32) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be an array with at most {maximum} items")
    result = [_planning_text(item, f"{label} item", maximum=1000) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{label} items must be unique")
    return result


def _planning_paths_list(value: Any, label: str) -> list[str]:
    if value is None:
        return ["."]
    if not isinstance(value, list) or not value or len(value) > 50:
        raise ValueError(f"{label} must be a non-empty array with at most 50 paths")
    paths = ["." if str(item).strip() == "." else _safe_project_relative_path(item) for item in value]
    if len(paths) != len(set(paths)):
        raise ValueError(f"{label} paths must be unique")
    return paths


def _validate_planning_dependency_graph(nodes: set[str], dependencies: dict[str, list[str]], label: str) -> None:
    for node, items in dependencies.items():
        unknown = sorted(set(items) - nodes)
        if unknown:
            raise ValueError(f"{label} {node!r} depends on unknown item(s): " + ", ".join(unknown))
        if node in items:
            raise ValueError(f"{label} {node!r} cannot depend on itself")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError(f"{label} dependencies must be acyclic")
        if node in visited:
            return
        visiting.add(node)
        for dependency in dependencies.get(node, []):
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in nodes:
        visit(node)


def sanitize_scoping_payload(value: Any, *, persisted: bool = False) -> dict[str, Any]:
    """Validate the Planner Scope discovery brief without widening report/v1."""
    if persisted and isinstance(value, dict) and value.get("schema") == SCOPING_SCHEMA:
        value = {key: item for key, item in value.items() if key != "schema"}
    if not isinstance(value, dict) or set(value) != {"overview", "context_files", "discovery_domains"}:
        raise ValueError("scoping must contain exactly overview, context_files, and discovery_domains")
    overview = _planning_text(value.get("overview"), "scoping overview", maximum=8000)
    raw_context_files = value.get("context_files")
    if not isinstance(raw_context_files, list) or len(raw_context_files) > 50:
        raise ValueError("scoping context_files must be an array with at most 50 paths")
    context_files = [_safe_project_relative_path(item) for item in raw_context_files]
    if len(context_files) != len(set(context_files)):
        raise ValueError("scoping context_files must be unique")
    raw_domains = value.get("discovery_domains")
    if (
        not isinstance(raw_domains, list)
        or not raw_domains
        or len(raw_domains) > MAX_DISCOVERY_DOMAINS
    ):
        raise ValueError(f"scoping discovery_domains must contain 1..{MAX_DISCOVERY_DOMAINS} items")
    domains: list[dict[str, Any]] = []
    domain_ids: set[str] = set()
    dependency_graph: dict[str, list[str]] = {}
    required = {
        "id", "title", "objective", "paths", "context", "depends_on",
        "acceptance_criteria", "verification",
    }
    for index, raw_domain in enumerate(raw_domains, 1):
        if not isinstance(raw_domain, dict) or set(raw_domain) != required:
            unknown = sorted(set(raw_domain) - required) if isinstance(raw_domain, dict) else []
            missing = sorted(required - set(raw_domain)) if isinstance(raw_domain, dict) else sorted(required)
            details = ([] if not unknown else ["unknown: " + ", ".join(unknown)]) + (
                [] if not missing else ["missing: " + ", ".join(missing)]
            )
            raise ValueError(
                f"scoping discovery_domains[{index - 1}] is invalid (" + "; ".join(details) + ")"
            )
        domain_id = _planning_identifier(raw_domain.get("id"), "scoping discovery domain id")
        if domain_id in domain_ids:
            raise ValueError("scoping discovery domain ids must be unique")
        domain_ids.add(domain_id)
        raw_dependencies = raw_domain.get("depends_on")
        if not isinstance(raw_dependencies, list):
            raise ValueError(f"scoping discovery domain {domain_id!r} depends_on must be an array")
        dependencies = [_planning_identifier(item, "scoping discovery domain dependency") for item in raw_dependencies]
        if len(dependencies) != len(set(dependencies)):
            raise ValueError(f"scoping discovery domain {domain_id!r} dependencies must be unique")
        context = _planning_string_list(raw_domain.get("context"), "scoping discovery domain context")
        acceptance = _planning_string_list(
            raw_domain.get("acceptance_criteria"),
            "scoping discovery domain acceptance_criteria",
        )
        verification = _planning_string_list(
            raw_domain.get("verification"),
            "scoping discovery domain verification",
        )
        if not context:
            raise ValueError(f"scoping discovery domain {domain_id!r} requires non-empty context")
        if not acceptance or not verification:
            raise ValueError(
                f"scoping discovery domain {domain_id!r} requires non-empty acceptance_criteria and verification"
            )
        domain = {
            "id": domain_id,
            "title": _planning_text(raw_domain.get("title"), "scoping discovery domain title", maximum=500),
            "objective": _planning_text(raw_domain.get("objective"), "scoping discovery domain objective"),
            "paths": _planning_paths_list(raw_domain.get("paths"), "scoping discovery domain paths"),
            "context": context,
            "depends_on": dependencies,
            "acceptance_criteria": acceptance,
            "verification": verification,
        }
        domains.append(domain)
        dependency_graph[domain_id] = dependencies
    _validate_planning_dependency_graph(domain_ids, dependency_graph, "scoping discovery domain")
    result = {
        "schema": SCOPING_SCHEMA,
        "overview": overview,
        "context_files": context_files,
        "discovery_domains": domains,
    }
    if len(json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")) > MAX_SCOPING_BYTES:
        raise ValueError(f"scoping exceeds the {MAX_SCOPING_BYTES}-byte limit")
    return result


def sanitize_planning_payload(value: Any, *, persisted: bool = False) -> dict[str, Any]:
    """Validate the Planner-only work-breakdown artifact without widening report/v1."""
    if persisted and isinstance(value, dict) and value.get("schema") == PLANNING_SCHEMA:
        value = {key: item for key, item in value.items() if key != "schema"}
    if not isinstance(value, dict) or set(value) != {"overview", "work_packages"}:
        raise ValueError("planning must contain exactly overview and work_packages")
    overview = _planning_text(value.get("overview"), "planning overview", maximum=8000)
    raw_packages = value.get("work_packages")
    if not isinstance(raw_packages, list) or not raw_packages or len(raw_packages) > MAX_WORK_PACKAGES:
        raise ValueError(f"planning work_packages must contain 1..{MAX_WORK_PACKAGES} items")
    packages: list[dict[str, Any]] = []
    package_ids: set[str] = set()
    microtask_ids: set[str] = set()
    microtask_dependencies: dict[str, list[str]] = {}
    total_microtasks = 0
    for index, raw_package in enumerate(raw_packages, 1):
        if not isinstance(raw_package, dict):
            raise ValueError(f"planning work_packages[{index - 1}] must be an object")
        unknown = sorted(set(raw_package) - {"id", "title", "objective", "allowed_paths", "depends_on", "microtasks"})
        missing = sorted({"id", "title", "objective", "microtasks"} - set(raw_package))
        if unknown or missing:
            details = ([] if not unknown else ["unknown: " + ", ".join(unknown)]) + ([] if not missing else ["missing: " + ", ".join(missing)])
            raise ValueError(f"planning work_packages[{index - 1}] is invalid (" + "; ".join(details) + ")")
        package_id = _planning_identifier(raw_package.get("id"), "planning package id")
        if package_id in package_ids:
            raise ValueError("planning package ids must be unique")
        package_ids.add(package_id)
        raw_microtasks = raw_package.get("microtasks")
        if not isinstance(raw_microtasks, list) or not raw_microtasks or len(raw_microtasks) > MAX_MICROTASKS_PER_PACKAGE:
            raise ValueError(f"planning package {package_id!r} must contain 1..{MAX_MICROTASKS_PER_PACKAGE} microtasks")
        microtasks: list[dict[str, Any]] = []
        for micro_index, raw_microtask in enumerate(raw_microtasks, 1):
            if not isinstance(raw_microtask, dict):
                raise ValueError(f"planning package {package_id!r} microtask {micro_index} must be an object")
            allowed = {"id", "title", "objective", "profile", "allowed_paths", "depends_on", "acceptance_criteria", "verification"}
            unknown_micro = sorted(set(raw_microtask) - allowed)
            missing_micro = sorted({"id", "title", "objective"} - set(raw_microtask))
            if unknown_micro or missing_micro:
                details = ([] if not unknown_micro else ["unknown: " + ", ".join(unknown_micro)]) + ([] if not missing_micro else ["missing: " + ", ".join(missing_micro)])
                raise ValueError(f"planning package {package_id!r} microtask {micro_index} is invalid (" + "; ".join(details) + ")")
            microtask_id = _planning_identifier(raw_microtask.get("id"), "planning microtask id")
            if microtask_id in microtask_ids:
                raise ValueError("planning microtask ids must be unique across work packages")
            microtask_ids.add(microtask_id)
            profile = str(raw_microtask.get("profile") or "").strip()
            if profile:
                profile = canonical_profile(profile)
                if profile not in AGENTS:
                    raise ValueError(f"planning microtask {microtask_id!r} has an unknown profile")
            raw_dependencies = raw_microtask.get("depends_on", [])
            if not isinstance(raw_dependencies, list):
                raise ValueError(f"planning microtask {microtask_id!r} depends_on must be an array")
            dependencies = [_planning_identifier(item, "planning microtask dependency") for item in raw_dependencies]
            if len(dependencies) != len(set(dependencies)):
                raise ValueError(f"planning microtask {microtask_id!r} dependencies must be unique")
            microtask_dependencies[microtask_id] = dependencies
            microtask_acceptance = _planning_string_list(
                raw_microtask.get("acceptance_criteria"),
                "planning microtask acceptance_criteria",
            )
            microtask_verification = _planning_string_list(
                raw_microtask.get("verification"),
                "planning microtask verification",
            )
            if not microtask_acceptance or not microtask_verification:
                raise ValueError(
                    f"planning microtask {microtask_id!r} requires non-empty acceptance_criteria and verification"
                )
            microtasks.append({
                "id": microtask_id,
                "title": _planning_text(raw_microtask.get("title"), "planning microtask title", maximum=500),
                "objective": _planning_text(raw_microtask.get("objective"), "planning microtask objective"),
                "profile": profile or None,
                "allowed_paths": _planning_paths_list(raw_microtask.get("allowed_paths"), "planning microtask allowed_paths"),
                "depends_on": dependencies,
                "acceptance_criteria": microtask_acceptance,
                "verification": microtask_verification,
            })
        total_microtasks += len(microtasks)
        if total_microtasks > MAX_MICROTASKS_PER_PLAN:
            raise ValueError(f"planning may contain at most {MAX_MICROTASKS_PER_PLAN} microtasks")
        raw_dependencies = raw_package.get("depends_on", [])
        if not isinstance(raw_dependencies, list):
            raise ValueError(f"planning package {package_id!r} depends_on must be an array")
        dependencies = [_planning_identifier(item, "planning package dependency") for item in raw_dependencies]
        if len(dependencies) != len(set(dependencies)):
            raise ValueError(f"planning package {package_id!r} dependencies must be unique")
        packages.append({
            "id": package_id,
            "title": _planning_text(raw_package.get("title"), "planning package title", maximum=500),
            "objective": _planning_text(raw_package.get("objective"), "planning package objective"),
            "allowed_paths": _planning_paths_list(raw_package.get("allowed_paths"), "planning package allowed_paths"),
            "depends_on": dependencies,
            "microtasks": microtasks,
        })
    _validate_planning_dependency_graph(package_ids, {item["id"]: item["depends_on"] for item in packages}, "planning package")
    _validate_planning_dependency_graph(microtask_ids, microtask_dependencies, "planning microtask")
    result = {"schema": PLANNING_SCHEMA, "overview": overview, "work_packages": packages}
    if len(json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")) > MAX_PLANNING_BYTES:
        raise ValueError(f"planning exceeds the {MAX_PLANNING_BYTES}-byte limit")
    return result


def planning_paths(task_dir: Path) -> dict[str, Path]:
    root = _contained_path(task_dir, task_dir / "planning", "planning root")
    revisions = _contained_path(root, root / "revisions", "planning revisions")
    for label, path in (("planning root", root), ("planning revisions", revisions)):
        if path.exists():
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"{label} must be a real directory")
    return {"root": root, "revisions": revisions, "manifest": root / "manifest.json", "overview": root / "overview.md"}


def _planning_overview_markdown(manifest: dict[str, Any]) -> str:
    lines = ["# Work plan", "", str(manifest["overview"]), "", "## Work packages", ""]
    for package in manifest["work_packages"]:
        dependency_text = ", ".join(package["depends_on"]) if package["depends_on"] else "none"
        lines.extend((
            f"### {package['id']}: {package['title']}", "", package["objective"], "",
            f"Dependencies: {dependency_text}", "", "Microtasks:",
        ))
        for microtask in package["microtasks"]:
            profile = f" ({microtask['profile']})" if microtask.get("profile") else ""
            lines.append(f"- `{microtask['id']}`{profile}: {microtask['title']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def materialize_planning_artifacts(
    task_dir: Path,
    state: dict[str, Any],
    attempt: dict[str, Any],
    report_id: str,
    report: dict[str, Any],
    planning: dict[str, Any],
) -> dict[str, Any]:
    """Persist planning canonically and queue optional filesystem exports.

    This helper is called while a report transaction is active.  It therefore
    never creates a planning directory or writes a file.  Generic projection
    jobs make the familiar files available later through reconciliation or an
    explicit materialization request.
    """
    revision = safe_id(f"plan-{report_id}")
    packages = planning["work_packages"]
    manifest = {
        "schema": PLANNING_SCHEMA,
        "task_id": state["task_id"],
        "revision": revision,
        "source_report_ref": report_id,
        "source_attempt_id": attempt["attempt_id"],
        "summary": report["summary"],
        "overview": planning["overview"],
        "overview_artifact_path": f"planning/revisions/{revision}/overview.md",
        "work_packages": [
            {
                "id": package["id"], "title": package["title"], "objective": package["objective"],
                "allowed_paths": package["allowed_paths"], "depends_on": package["depends_on"],
                "microtask_count": len(package["microtasks"]),
                "artifact_path": f"planning/revisions/{revision}/packages/{package['id']}.json",
            }
            for package in packages
        ],
        "created_at": now(),
    }
    root = _task_document_root(task_dir, state["task_id"])
    existing = db_get_task_document(root, state["task_id"], "planning_current")
    if isinstance(existing, dict) and existing.get("source_report_ref") == report_id:
        return existing
    from cortex_runtime.projection_service import enqueue as enqueue_projection

    for package in packages:
        package_document = {
            "schema": PLANNING_SCHEMA,
            "task_id": state["task_id"],
            "revision": revision,
            "source_report_ref": report_id,
            "package": package,
            "created_at": manifest["created_at"],
        }
        artifact = store_immutable_artifact(
            task_dir, state["task_id"], kind="planning_revision",
            title=f"planning/revisions/{revision}/packages/{package['id']}.json",
            mime_type="application/json", content=_json_text(package_document, label="planning package artifact", max_bytes=MAX_JSON_BYTES),
            export_path=f"planning/revisions/{revision}/packages/{package['id']}.json",
        )
        enqueue_projection(
            root=root, task_id=state["task_id"], artifact_id=artifact["artifact_ref"],
            projection_type="planning_package",
            export_path=f"planning/revisions/{revision}/packages/{package['id']}.json",
        )
    manifest_artifact = store_immutable_artifact(
        task_dir, state["task_id"], kind="planning_revision",
        title=f"planning/revisions/{revision}/manifest.json", mime_type="application/json",
        content=_json_text(manifest, label="planning revision manifest", max_bytes=MAX_JSON_BYTES),
        export_path=f"planning/revisions/{revision}/manifest.json",
    )
    enqueue_projection(
        root=root, task_id=state["task_id"], artifact_id=manifest_artifact["artifact_ref"],
        projection_type="planning_manifest", export_path=f"planning/revisions/{revision}/manifest.json",
    )
    db_put_task_document(root, state["task_id"], "planning_current", manifest)
    overview = _planning_overview_markdown({**manifest, "work_packages": packages})
    overview_path = str(manifest["overview_artifact_path"])
    overview_artifact = store_immutable_artifact(
        task_dir, state["task_id"], kind="planning_overview", title=overview_path,
        mime_type="text/markdown", content=overview, export_path=overview_path,
    )
    enqueue_projection(
        root=root, task_id=state["task_id"], artifact_id=overview_artifact["artifact_ref"],
        projection_type="planning_overview", export_path=overview_path,
    )
    return manifest


def current_planning_manifest(task_dir: Path) -> dict[str, Any] | None:
    task = load_task_definition(task_dir)
    root = _task_document_root(task_dir, str(task["task_id"]))
    value = db_get_task_document(root, str(task["task_id"]), "planning_current")
    if value is None:
        return None
    if value.get("schema") != PLANNING_SCHEMA:
        raise ValueError("planning manifest schema is not supported")
    return value


def report_bus_paths(task_dir: Path) -> dict[str, Path]:
    reports = _contained_path(task_dir, task_dir / "reports", "report bus")
    paths = {
        "root": reports,
        "records": reports / "records",
        "markdown": reports / "markdown",
        "receipts": reports / "receipts",
        "consumptions": reports / "consumptions",
        "delegations": reports / "delegations",
    }
    for key in ("root", "records", "markdown", "receipts", "consumptions", "delegations"):
        path = _contained_path(task_dir, paths[key], f"report bus {key}")
        if path.exists():
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"report bus {key} must be a real directory")
    return paths


def report_markdown_path(task_dir: Path, report_ref: object) -> Path:
    """Return the Desktop-openable Markdown artifact for one persisted report."""
    report_id = safe_id(str(report_ref or ""))
    if not report_id:
        raise ValueError("report_ref is required")
    paths = report_bus_paths(task_dir)
    path = _contained_path(paths["markdown"], paths["markdown"] / f"{report_id}.md", "worker report Markdown")
    if not path.exists() or path.is_symlink() or not path.is_file():
        raise ValueError("worker report Markdown artifact is unavailable")
    return path


def report_markdown_link(task_dir: Path, report_ref: object, phase: object = "report") -> str:
    """Return the exact Markdown link the coordinator must publish in main chat."""
    report_id = safe_id(str(report_ref or ""))
    if not report_id:
        raise ValueError("report_ref is required")
    phase_label = str(phase or "report").strip() or "report"
    path = report_markdown_path(task_dir, report_id)
    return f"[Report {phase_label} — {report_id}](<{path}>)"


def question_bus_paths(task_dir: Path) -> dict[str, Path]:
    root = _contained_path(task_dir, task_dir / "questions", "question bus")
    records = _contained_path(root, root / "records", "question records")
    for label, path in (("question bus", root), ("question records", records)):
        if path.exists():
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"{label} must be a real directory")
    return {"root": root, "records": records}


def _question_options(value: object) -> list[dict[str, str]]:
    if value in (None, "", []):
        return []
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("question options must be a list with at most 32 choices")
    options: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            label = redact(item.strip(), 120)
            description = label
            option_id = "option_" + digest_text(label)[:12]
        elif isinstance(item, dict):
            label = redact(str(item.get("label_en") or item.get("label") or "").strip(), 120)
            description = redact(str(item.get("description", "")).strip(), 400) or label
            raw_option_id = str(item.get("option_id") or "").strip().lower()
            option_id = safe_id(raw_option_id) if raw_option_id else "option_" + digest_text(label)[:12]
        else:
            raise ValueError("question options must be strings or objects")
        if not label:
            raise ValueError("question options require a non-empty label")
        options.append({"option_id": option_id, "label": label, "label_en": label, "description": description})
    if len({item["option_id"] for item in options}) != len(options):
        raise ValueError("question option IDs must be unique")
    return options


def _question_config(params: dict[str, Any]) -> dict[str, Any]:
    options = _question_options(params.get("options"))
    multiple = bool(params.get("multiple", params.get("multi_select", False)))
    if multiple and not options:
        raise ValueError("multiple question selection requires options")
    header = redact(str(params.get("header") or "Question").strip(), 120) or "Question"
    custom_label = redact(
        str(params.get("custom_label") or "Your answer / additional context").strip(),
        160,
    ) or "Your answer / additional context"
    return {
        "header": header,
        "options": options,
        "multiple": multiple,
        "custom_label": custom_label,
        "custom_response": True,
    }


def _question_payload(params: dict[str, Any]) -> tuple[str, Any, bool, dict[str, Any], str]:
    question = str(params.get("question", "")).strip()
    if not question:
        raise ValueError("worker question text is required")
    sanitized_question = redact(question, 4000)
    context = sanitize_structured(params.get("context", {}))
    blocking = bool(params.get("blocking", True))
    config = _question_config(params)
    require_internal_english(sanitized_question, "worker question")
    require_internal_english(config["header"], "worker question header")
    require_internal_english(config["custom_label"], "worker question custom_label")
    require_internal_english(config["options"], "worker question options")
    digest = digest_text(json.dumps({"question": sanitized_question, "context": context, "blocking": blocking, "config": config}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return sanitized_question, context, blocking, config, digest


def _question_records(paths: dict[str, Path], state: dict[str, Any]) -> list[dict[str, Any]]:
    task_dir = paths["root"].parent
    root = _task_document_root(task_dir, str(state["task_id"]))
    records: list[dict[str, Any]] = []
    for document_key, record in db_list_task_documents(root, str(state["task_id"]), "question:"):
        question_id_from_key = document_key.removeprefix("question:")
        question_id = str(record.get("question_id", ""))
        if (
            record.get("schema") != QUESTION_SCHEMA
            or record.get("task_id") != state["task_id"]
            or question_id != question_id_from_key
            or not re.fullmatch(r"question-\d+", question_id)
        ):
            raise ValueError("question record failed validation")
        _attempt(state, safe_id(str(record.get("attempt_id", ""))))
        records.append(record)
    return records


def _write_question_record(task_dir: Path, state: dict[str, Any], record: dict[str, Any]) -> None:
    question_id = str(record.get("question_id") or "")
    if not re.fullmatch(r"question-\d+", question_id):
        raise ValueError("question record identity is invalid")
    root = _task_document_root(task_dir, str(state["task_id"]))
    db_put_task_document(root, str(state["task_id"]), f"question:{question_id}", record)


def _question_sequence(records: list[dict[str, Any]]) -> int:
    return max(
        (max(int(item.get("published_sequence", 0)), int(item.get("answered_sequence") or 0)) for item in records),
        default=0,
    )


def _open_blocking_questions(
    task_dir: Path,
    state: dict[str, Any],
    attempt_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return unanswered material questions that pause an attempt or wave."""
    # Question records are canonical SQLite task documents.  Their optional
    # filesystem projections may be absent on a fresh ledger or while an
    # outbox job is pending, so export layout must never decide whether a
    # material question blocks the task.
    records = _question_records(question_bus_paths(task_dir), state)
    blockers = [
        item for item in records
        if item.get("status") == "open"
        and bool(item.get("blocking", True))
        and (attempt_id is None or item.get("attempt_id") == attempt_id)
    ]
    # Localized batch questions use one SQLite task document with per-slide
    # checkpoints. Treat an unfinished sequence or translation exactly like an
    # unanswered single question: neither may allow a report or wave advance.
    document_root = _task_document_root(task_dir, str(state["task_id"]))
    for document_key, batch in db_list_task_documents(document_root, str(state["task_id"]), "question_batch:"):
        batch_id = str(batch.get("batch_id") or "")
        if (
            batch.get("schema") != "cortex/question-batch/v1"
            or document_key != "question_batch:" + batch_id
            or batch.get("task_id") != state["task_id"]
            or batch.get("status") not in {"open", "awaiting_translation"}
            or (attempt_id is not None and batch.get("attempt_id") != attempt_id)
        ):
            continue
        _attempt(state, safe_id(str(batch.get("attempt_id") or "")))
        blockers.append({
            "question_id": batch_id,
            "attempt_id": batch.get("attempt_id"),
            "header": "Question batch",
            "question": f"Batch {batch.get('batch_key') or batch_id} is {batch.get('status')}",
            "blocking": True,
            "status": batch.get("status"),
            "batch": True,
        })
    return blockers


def _read_private_text(path: Path, label: str, *, max_bytes: int) -> str:
    """Read one bounded private regular UTF-8 file without following links."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError(f"{label} size limit is invalid")
    path = _reject_symlink_ancestry(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if info.st_size > max_bytes:
            raise ValueError(f"{label} is oversized")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ValueError(f"{label} is oversized")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    finally:
        os.close(descriptor)


def _receipt_identity(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("schema"), value.get("receipt_id"), value.get("report_id"), value.get("task_id"),
        value.get("gate"), value.get("attempt_id"), value.get("content_digest"),
    )


def _report_markdown(record: dict[str, Any]) -> str:
    def prose(value: Any) -> str:
        """Escape HTML without corrupting ordinary Markdown punctuation."""
        return html.escape(str(value), quote=True)

    def scalar_item(value: Any) -> str:
        text = prose(value).replace("\r\n", "\n").replace("\r", "\n")
        text = "<br>".join(text.split("\n"))
        # Only a marker at the start of a list item can alter its structure.
        return re.sub(r"^(#{1,6}\s|[-+*]\s|>\s|\d+[.)]\s)", r"\\\1", text)

    report = record["report"]
    lines = [f"# Report {prose(record['report_id'])}", "", f"**Producer:** {prose(record['producer']['profile'])}", "", "## Summary", "", prose(report["summary"])]
    for field in ("findings", "questions", "changed_files", "tests", "evidence", "uncertainty"):
        lines.extend(["", f"## {field.replace('_', ' ').title()}", ""])
        items = report[field]
        if not items:
            lines.append("- None")
        else:
            for item in items:
                if isinstance(item, (dict, list)):
                    lines.extend(["```json", json.dumps(item, ensure_ascii=False, sort_keys=True, indent=2), "```"])
                else:
                    lines.append(f"- {scalar_item(item)}")
    validation = record.get("result_validation")
    if isinstance(validation, dict):
        lines.extend([
            "## Result Validation", "",
            f"- Status: {scalar_item(validation.get('status'))}",
            f"- Contract digest: {scalar_item(validation.get('contract_digest'))}",
            f"- Reported changed files: {scalar_item((validation.get('artifacts') or {}).get('reported_change_count'))}",
            "",
        ])
    scoping = record.get("scoping")
    if isinstance(scoping, dict):
        lines.extend(["", "## Scoping", "", "```json", json.dumps(scoping, ensure_ascii=False, sort_keys=True, indent=2), "```"])
    planning = record.get("planning")
    if isinstance(planning, dict):
        lines.extend(["", "## Planning", "", "```json", json.dumps(planning, ensure_ascii=False, sort_keys=True, indent=2), "```"])
    return "\n".join(lines)


def _report_metadata(record: dict[str, Any]) -> dict[str, Any]:
    report = record["report"]
    return {
        "report_id": record["report_id"],
        "attempt_id": record["attempt_id"],
        "gate": record["gate"],
        "producer": record["producer"],
        "summary": report["summary"],
        "changed_files": report["changed_files"],
        "content_digest": record["content_digest"],
        "result_validation": (
            {
                "schema": record["result_validation"].get("schema"),
                "status": record["result_validation"].get("status"),
                "contract_digest": record["result_validation"].get("contract_digest"),
            }
            if isinstance(record.get("result_validation"), dict) else None
        ),
        "created_at": record["created_at"],
    }


def _recover_report_receipt(
    paths: dict[str, Path], record: dict[str, Any], state: dict[str, Any], invalidated: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Load receipt state from SQLite and repair only its export projection.

    The immutable receipt artifact records the report binding. Its mutable
    consumption state is a task document in the same database transaction as
    evidence. Neither the receipt JSON export nor a tombstone file is ever
    consulted as state.
    """
    report_id = safe_id(str(record["report_id"]))
    receipt_path = paths["receipts"] / f"report-receipt-{report_id}.json"
    base = {
        "schema": REPORT_SCHEMA,
        "receipt_id": f"report-receipt-{report_id}",
        "report_id": report_id,
        "task_id": record["task_id"],
        "gate": record["gate"],
        "attempt_id": record["attempt_id"],
        "content_digest": record["content_digest"],
        "consumed_at": None,
        "consumed_by_evidence_id": None,
        "invalidated": bool(invalidated),
        "created_at": record.get("created_at") or now(),
    }
    root = _task_document_root(paths["root"].parent, state["task_id"])
    persisted, _ = read_immutable_json_artifact(
        paths["root"].parent,
        state["task_id"],
        f"reports/receipts/{base['receipt_id']}.json",
        kinds={"report_receipt"},
    )
    if _receipt_identity(persisted) != _receipt_identity(base):
        raise ValueError("SQLite report receipt identity is invalid")
    receipt_state = db_get_task_document(root, state["task_id"], f"receipt_state:{base['receipt_id']}")
    if receipt_state is not None:
        if _receipt_identity(receipt_state) != _receipt_identity(base):
            raise ValueError("SQLite report receipt state is invalid")
        base["consumed_at"] = receipt_state.get("consumed_at")
        base["consumed_by_evidence_id"] = receipt_state.get("consumed_by_evidence_id")
        base["invalidated"] = bool(receipt_state.get("invalidated") or invalidated)
        base["created_at"] = receipt_state.get("created_at") or base["created_at"]
    else:
        evidence = next((item for item in state.get("evidence", []) if item.get("report_id") == report_id), None)
        if evidence is not None:
            base["consumed_at"] = str(evidence.get("created_at") or now())
            base["consumed_by_evidence_id"] = safe_id(str(evidence["evidence_id"]))
            db_put_task_document(root, state["task_id"], f"receipt_state:{base['receipt_id']}", base)
    # This is an export only.  Do not write it while recovering durable state:
    # callers may be inside a SQLite transaction and a missing projection must
    # never invalidate a report or its evidence.  Reconciliation and explicit
    # materialization use the receipt artifact already registered above.
    del receipt_path
    return base, False


def _load_report_receipt(
    task_dir: Path,
    state: dict[str, Any],
    receipt_id: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve one report receipt through its SQLite artifact and task state."""
    normalized = safe_id(str(receipt_id or ""))
    if not normalized.startswith("report-receipt-report-"):
        raise ValueError("C2/C3 evidence requires an attempt-tied report receipt")
    report_id = normalized.removeprefix("report-receipt-")
    record, _ = read_immutable_json_artifact(
        task_dir,
        state["task_id"],
        f"reports/records/{report_id}.json",
        kinds={"worker_report", "report_record"},
    )
    if record.get("task_id") != state["task_id"] or record.get("report_id") != report_id:
        raise ValueError("report receipt does not belong to this task")
    receipt, _ = _recover_report_receipt(
        report_bus_paths(task_dir),
        record,
        state,
        bool(_attempt(state, safe_id(str(record["attempt_id"]))).get("invalidated")),
    )
    if receipt["receipt_id"] != normalized:
        raise ValueError("report receipt id does not match its report")
    return receipt, record


def _available_report_receipts(
    task_dir: Path,
    state: dict[str, Any],
    *,
    attempt_id: object,
    gate: object,
) -> list[dict[str, Any]]:
    """Return unconsumed valid receipts without enumerating file projections."""
    normalized_attempt = safe_id(str(attempt_id or ""))
    normalized_gate = str(gate or "")
    index = _report_index(report_bus_paths(task_dir), state["task_id"])
    receipts: list[dict[str, Any]] = []
    for metadata in index.get("reports", []):
        if not isinstance(metadata, dict) or metadata.get("attempt_id") != normalized_attempt:
            continue
        report_id = safe_id(str(metadata.get("report_id") or ""))
        if not report_id:
            continue
        receipt, record = _load_report_receipt(task_dir, state, f"report-receipt-{report_id}")
        if (
            record.get("gate") == normalized_gate
            and receipt.get("attempt_id") == normalized_attempt
            and receipt.get("gate") == normalized_gate
            and not receipt.get("consumed_at")
            and not receipt.get("invalidated")
        ):
            receipts.append(receipt)
    return receipts


def _task_document_root(task_dir: Path, task_id: str) -> Path:
    root = _ledger_root_for_artifact(task_dir)
    registered = db_task_artifact_path(root, task_id)
    if registered is None or registered.resolve() != task_dir.resolve():
        raise ValueError("task document scope does not match the SQLite task ledger")
    return root


def store_immutable_artifact(
    task_dir: Path,
    task_id: str,
    *,
    kind: str,
    title: str,
    mime_type: str,
    content: str | bytes,
    export_path: str | None = None,
) -> dict[str, Any]:
    """Persist canonical immutable content before/alongside its local export."""
    root = _task_document_root(task_dir, task_id)
    return db_put_artifact(
        root, task_id, kind=kind, title=title, mime_type=mime_type,
        content=content, immutable=True, export_path=export_path,
    )


def read_immutable_json_artifact(
    task_dir: Path,
    task_id: str,
    export_path: str,
    *,
    kinds: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read canonical JSON by its task-scoped export projection name.

    The path is an address retained for a convenient on-disk export, not the
    content source. State-machine callers therefore retain exact digest and
    chunk validation even when an export has not yet been rematerialized.
    """
    candidate = Path(export_path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError("immutable artifact export path is invalid")
    root = _task_document_root(task_dir, task_id)
    metadata = db_get_artifact_for_export_path(root, task_id, candidate.as_posix())
    if metadata is None or metadata.get("kind") not in kinds:
        raise ValueError("immutable artifact is unavailable for the selected task")
    content = db_read_artifact_content(root, task_id, str(metadata["artifact_ref"]))
    if not isinstance(content, str):
        raise ValueError("immutable JSON artifact is not UTF-8 text")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("immutable JSON artifact is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("immutable JSON artifact must be an object")
    return value, metadata


def _report_index(paths: dict[str, Path], task_id: str) -> dict[str, Any]:
    task_dir = paths["root"].parent
    root = _task_document_root(task_dir, task_id)
    value = db_get_task_document(root, task_id, "report_index")
    if value is None:
        return {"schema": REPORT_SCHEMA, "task_id": task_id, "reports": [], "submissions": {}, "updated_at": now()}
    if value.get("schema") != REPORT_SCHEMA or value.get("task_id") != task_id:
        raise ValueError("report index does not belong to this task")
    if len(value.get("reports", [])) > MAX_REPORTS_PER_TASK or len(value.get("submissions", {})) > MAX_REPORTS_PER_TASK:
        raise ValueError("report index exceeds its bounded capacity")
    return value


def _write_report_index(paths: dict[str, Path], task_id: str, value: dict[str, Any]) -> None:
    if value.get("schema") != REPORT_SCHEMA or value.get("task_id") != task_id:
        raise ValueError("report index does not belong to this task")
    _task_document_root(paths["root"].parent, task_id)
    db_put_task_document(_ledger_root_for_artifact(paths["root"].parent), task_id, "report_index", value)


def _compact_report_context(record: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded, receipt-free predecessor handoff for a worker prompt."""
    report = record.get("report") if isinstance(record.get("report"), dict) else {}

    def compact_list(field: str, *, items: int = 16, chars: int = 1200) -> list[str]:
        values = report.get(field)
        if not isinstance(values, list):
            return []
        return [redact(item, chars) for item in values[:items]]

    producer = record.get("producer") if isinstance(record.get("producer"), dict) else {}
    return {
        "report_id": redact(record.get("report_id", ""), 128),
        "phase": redact(record.get("gate", ""), 128),
        "profile": redact(producer.get("profile", ""), 128),
        "summary": redact(report.get("summary", ""), 2400),
        "findings": compact_list("findings"),
        "questions": compact_list("questions", items=8),
        "changed_files": compact_list("changed_files", chars=500),
        "tests": compact_list("tests"),
        "evidence": compact_list("evidence"),
        "uncertainty": compact_list("uncertainty", items=8),
    }


def _predecessor_review_marker(report_ids: list[str]) -> str:
    return "Predecessor review: " + ", ".join(report_ids)


KNOWLEDGE_INDEX_FILES = ("docs/project/index.md", "docs/features/index.md")
HARVEST_PROJECT_DOCS = (
    "docs/project/index.md",
    "docs/project/conventions.md",
    "docs/project/verification.md",
    "docs/project/decisions.md",
    "docs/project/gotchas.md",
)


def _project_knowledge_context(project_root: Path, explicit: object) -> tuple[list[str], list[str]]:
    """Add repository knowledge entry points without reading target contents."""
    indexes = [
        relative
        for relative in KNOWLEDGE_INDEX_FILES
        if (project_root / relative).is_file() and not (project_root / relative).is_symlink()
    ]
    supplied: list[str] = []
    for item in explicit if isinstance(explicit, list) else []:
        raw = str(item).strip()
        candidate = Path(raw)
        if not raw or candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise ValueError("context_files must contain existing project-relative regular files")
        resolved = _contained_path(project_root, project_root / candidate, "context file")
        if not resolved.is_file() or resolved.is_symlink():
            raise ValueError(f"context file is missing or not a regular file: {raw}")
        supplied.append(resolved.relative_to(project_root).as_posix())
    merged: list[str] = []
    for item in [*indexes, *supplied]:
        if item and item not in merged:
            merged.append(item)
    return merged[:50], indexes


def _validate_knowledge_review(report: dict[str, Any], knowledge_indexes: list[str]) -> None:
    """Require auditable consumption of every available knowledge entry point."""
    required = {str(item).lower() for item in knowledge_indexes}
    if not required:
        return
    reviewed: set[str] = set()
    for item in report.get("evidence", []):
        rendered = (
            json.dumps(item, ensure_ascii=False, sort_keys=True)
            if isinstance(item, (dict, list)) else str(item)
        ).lower()
        if "knowledge reviewed:" not in rendered:
            continue
        reviewed.update(path for path in required if path in rendered)
    missing = sorted(required - reviewed)
    if missing:
        raise ValueError(
            "report evidence must acknowledge every available repository knowledge index; add one entry like "
            + repr("Knowledge reviewed: " + ", ".join(sorted(required)))
        )


def _is_knowledge_harvest_task(task: dict[str, Any]) -> bool:
    routing_text = "\n".join(_task_routing_items(task)).lower()
    return (
        "harvest" in routing_text
        or "feature census" in routing_text
        or "repository knowledge" in routing_text
        or "knowledge documentation" in routing_text
    )


def _required_task_result_contract(task: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Require observable task success and verification before orchestration starts."""
    def items(field: str) -> list[str]:
        value = task.get(field)
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > 100:
            raise ValueError(f"task.{field} must be an array with at most 100 non-empty strings")
        cleaned = [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]
        if len(cleaned) != len(value):
            raise ValueError(f"task.{field} must contain only non-empty strings")
        return cleaned

    acceptance = items("acceptance_criteria")
    verification = items("verification")
    if _is_knowledge_harvest_task(task):
        acceptance = acceptance or [
            "Every in-scope feature-bearing surface is mapped with zero unexplained gaps.",
            "Each canonical feature page describes runtime ownership, behavior and scenarios, state and data, interfaces, failures, and recovery.",
            "An independent review finds no missing feature page or unsupported completeness claim.",
        ]
        verification = verification or [
            "Validate the coverage matrix, inventory totals, documentation links, exclusions, and known unknowns against the repository census.",
            "Run an independent post-write completeness review and repeat it after corrections until no factual documentation change is proposed.",
        ]
    if not acceptance or not verification:
        missing = []
        if not acceptance:
            missing.append("task.acceptance_criteria")
        if not verification:
            missing.append("task.verification")
        raise ValueError(
            "orchestration requires a decision-complete observable result contract before dispatch; missing: "
            + ", ".join(missing)
            + ". Derive only criteria established by the exact user request or verified authority; ask the user when material intent is missing."
        )
    return acceptance, verification


def _validate_harvest_coverage_manifest(project_root: Path, task: dict[str, Any], gate: str) -> None:
    """Delegate harvest completeness validation to its focused runtime module."""
    from cortex_runtime.harvest_validation import validate_harvest_coverage_manifest

    validate_harvest_coverage_manifest(project_root, task, gate)


def _result_contract_markers(attempt: dict[str, Any], task: dict[str, Any]) -> list[tuple[str, str]]:
    """Return the exact evidence prefixes required to prove one worker result."""
    markers: list[tuple[str, str]] = []
    for index, criterion in enumerate(attempt.get("acceptance_criteria") or [], 1):
        markers.append((f"Gate acceptance {index}: PASS - ", str(criterion)))
    for index, criterion in enumerate(attempt.get("verification") or [], 1):
        markers.append((f"Gate verification {index}: PASS - ", str(criterion)))
    if attempt.get("gate") == "close":
        for index, criterion in enumerate(task.get("acceptance_criteria") or [], 1):
            markers.append((f"Task acceptance {index}: PASS - ", str(criterion)))
        for index, criterion in enumerate(task.get("verification") or [], 1):
            markers.append((f"Task verification {index}: PASS - ", str(criterion)))
    return markers


def _result_path_is_allowed(path: str, allowed_paths: list[Any]) -> bool:
    normalized = Path(path).as_posix().removeprefix("./")
    for raw in allowed_paths:
        allowed = str(raw or "").strip().replace("\\", "/").rstrip("/")
        if allowed in {"", "."}:
            return True
        if allowed.startswith("./"):
            allowed = allowed[2:]
        if normalized == allowed or normalized.startswith(allowed + "/"):
            return True
    return False


def _validate_result_artifacts(
    task_dir: Path,
    attempt: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile claimed writes against the filesystem delta for this attempt."""
    gate = str(attempt.get("gate") or "")
    raw_reported = [str(item) for item in report.get("changed_files") or []]
    if len(raw_reported) != len(set(raw_reported)):
        raise ValueError("report changed_files must not contain duplicate paths")
    reported = sorted(raw_reported)
    read_only_result = result_contract_is_read_only(attempt)
    if read_only_result and reported:
        raise ValueError(f"{gate} is a read-only result gate and must not report changed_files")
    if gate in WRITE_REQUIRED_RESULT_GATES and not reported:
        raise ValueError(f"{gate} result requires at least one real changed file; report non-success when no change was produced")
    out_of_scope = [path for path in reported if not _result_path_is_allowed(path, list(attempt.get("allowed_paths") or []))]
    if out_of_scope:
        raise ValueError("report changed_files fall outside delegated allowed_paths: " + ", ".join(out_of_scope))

    baseline = attempt_manifest_baseline(task_dir, attempt)
    current = capture_project_manifest(Path(baseline["project_root"]), policy=baseline.get("policy"))
    comparison = compare_manifests(baseline, current)
    changed = set(comparison["changed_paths"])
    observed_in_scope = sorted(
        path for path in changed
        if _result_path_is_allowed(path, list(attempt.get("allowed_paths") or []))
    )
    # Read-only workers execute in a host-enforced read-only sandbox. In a
    # shared checkout, a manifest delta can therefore belong to another task,
    # the user, or a concurrent writer and cannot safely be attributed to this
    # worker. Preserve the delta as concurrency evidence instead of rejecting
    # an otherwise valid report with an impossible "fix the JSON" loop.
    # Generated/ignored artifacts remain a hard failure below because they are
    # the common observable side effect of a supposedly non-writing check.
    concurrent_read_only_paths = observed_in_scope if read_only_result else []
    baseline_ignored = (baseline.get("policy") or {}).get("detected_ignored_entries") or {}
    current_ignored = (current.get("policy") or {}).get("detected_ignored_entries") or {}
    ignored_side_effects = sorted(
        path for path in set(baseline_ignored) | set(current_ignored)
        if baseline_ignored.get(path) != current_ignored.get(path)
    )
    if read_only_result and ignored_side_effects:
        raise ValueError(
            f"generated or ignored project artifacts changed during read-only result gate {gate}: "
            + ", ".join(ignored_side_effects)
        )
    unsupported = sorted(set(reported) - changed)
    if unsupported:
        raise ValueError(
            "report changed_files are not changed relative to this worker attempt baseline: "
            + ", ".join(unsupported)
        )
    unreported = sorted(set(observed_in_scope) - set(reported))
    if not read_only_result and unreported:
        raise ValueError(
            "report changed_files omit observed changes inside delegated allowed_paths: " + ", ".join(unreported)
        )
    return {
        "baseline_digest": baseline.get("digest"),
        "current_digest": current.get("digest"),
        "observed_change_count": comparison.get("change_count", 0),
        "reported_change_count": len(reported),
        "reported_paths_digest": digest_text("\n".join(reported)),
        "concurrent_change_count": len(concurrent_read_only_paths),
        "concurrent_paths_digest": digest_text("\n".join(concurrent_read_only_paths)),
    }


def _validate_dispatch_briefing_review(
    task_dir: Path,
    state: dict[str, Any],
    attempt: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Verify the exact immutable briefing and its worker acknowledgement."""
    relative = str(attempt.get("briefing_file") or "").strip()
    expected_digest = str(attempt.get("briefing_digest") or "").strip().lower()
    dispatch_ref = str(attempt.get("dispatch_ref") or "").strip()
    if not relative or not dispatch_ref or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise ValueError("attempt is missing its immutable dispatch briefing contract")
    relative_path = Path(relative)
    if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
        raise ValueError("dispatch briefing path is outside its task scope")
    briefing_path = _contained_path(task_dir, task_dir / relative_path, "dispatch briefing")
    info = briefing_path.lstat()
    if not stat.S_ISREG(info.st_mode) or briefing_path.is_symlink():
        raise ValueError("dispatch briefing must remain a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) & 0o222:
        raise ValueError("dispatch briefing lost immutable read-only permissions")
    content = _read_private_text(briefing_path, "dispatch briefing", max_bytes=MAX_BRIEFING_BYTES)
    actual_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if actual_digest != expected_digest:
        raise ValueError("immutable dispatch briefing digest changed after dispatch")
    task_id = str(state.get("task_id") or "")
    root = _task_document_root(task_dir, task_id)
    artifact_ref = str(attempt.get("briefing_artifact_ref") or "")
    artifact = db_get_artifact_metadata(root, task_id, artifact_ref) if artifact_ref else None
    if artifact is None:
        artifact = db_get_artifact_for_export_path(root, task_id, relative)
    if artifact is None or artifact.get("kind") != "dispatch_briefing" or artifact.get("digest_sha256") != expected_digest:
        raise ValueError("dispatch briefing has no matching immutable artifact catalog entry")
    canonical = db_read_artifact_content(root, task_id, str(artifact["artifact_ref"]))
    if canonical != content:
        raise ValueError("dispatch briefing export differs from its immutable artifact")
    marker = dispatch_briefing_review_marker(expected_digest)
    if marker not in [item for item in report.get("evidence", []) if isinstance(item, str)]:
        raise ValueError(
            "report evidence must acknowledge the immutable dispatch briefing; add exactly " + repr(marker)
        )
    return {
        "dispatch_ref": dispatch_ref,
        "briefing_digest": expected_digest,
        "briefing_file": relative,
        "review_marker": marker,
    }


def _validate_gate_result_report(
    task_dir: Path,
    state: dict[str, Any],
    attempt: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed unless a worker proves its gate contract and any claimed writes."""
    gate = str(attempt.get("gate") or "")
    task = load_task_definition(task_dir, state)
    if not report.get("evidence"):
        raise ValueError(f"{gate} result requires observed evidence")
    briefing_receipt = _validate_dispatch_briefing_review(task_dir, state, attempt, report)
    tests = report.get("tests") or []
    if gate in EXECUTED_CHECK_RESULT_GATES and not tests:
        raise ValueError(f"{gate} result requires at least one executed check or inspection result")
    if tests:
        successful_checks = 0
        unsuccessful_checks: list[int] = []
        for index, check in enumerate(tests, 1):
            required_check_fields = {"command", "cwd", "exit_code", "evidence"}
            if not isinstance(check, dict) or set(check) != required_check_fields:
                raise ValueError(
                    f"{gate} result test {index} must contain exactly command, cwd, exit_code, and evidence"
                )
            command = str(check.get("command") or "").strip()
            cwd = str(check.get("cwd") or "").strip().replace("\\", "/")
            exit_code = check.get("exit_code")
            check_evidence = str(check.get("evidence") or "").strip()
            if len(command) < 2:
                raise ValueError(f"{gate} result test {index} must identify the exact executed command")
            if re.search(r"(?<!\S)\.\.\.(?!\S)", command):
                raise ValueError(
                    f"{gate} result test {index} command must be the exact reproducible invocation, not a placeholder with ..."
                )
            project_root = str(task.get("project_root") or "").replace("\\", "/")
            if cwd != project_root:
                relative_cwd = Path(cwd)
                if (
                    not cwd
                    or relative_cwd.is_absolute()
                    or "\x00" in cwd
                    or any(part in {"", ".."} for part in relative_cwd.parts)
                ):
                    raise ValueError(
                        f"{gate} result test {index} cwd must be the exact project root or a safe project-relative directory"
                    )
            if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                raise ValueError(f"{gate} result test {index} exit_code must be an integer")
            if not check_evidence:
                raise ValueError(f"{gate} result test {index} needs a concrete observed output summary")
            if exit_code == 0:
                successful_checks += 1
            else:
                unsuccessful_checks.append(index)
        if gate in EXECUTED_CHECK_RESULT_GATES and not successful_checks:
            raise ValueError(f"{gate} result requires at least one successful executed check")
        if unsuccessful_checks:
            raise ValueError(
                f"{gate} result contains unsuccessful executed check(s) at report.tests index(es): "
                + ", ".join(str(index) for index in unsuccessful_checks)
                + "; a completion report may contain only checks that passed. Preserve the failures and return "
                "the report-tool error so the coordinator can rework the gate"
            )

    evidence_items = [item for item in report.get("evidence", []) if isinstance(item, str)]
    started_revision = int(attempt.get("task_revision_started") or 1)
    latest_material_revision = int(attempt.get("latest_material_revision") or started_revision)
    if latest_material_revision > started_revision:
        revision_marker = f"Task revision reviewed: {latest_material_revision}"
        if revision_marker not in evidence_items:
            raise ValueError(
                "report is stale after a material steer; add exactly " + repr(revision_marker)
                + " after reconciling the same worker attempt"
            )
    missing_markers: list[str] = []
    invalid_markers: list[str] = []
    weak_detail = ("<specific", "todo", "tbd", "unverified", "not run", "not tested", "unknown")
    for prefix, _criterion in _result_contract_markers(attempt, task):
        matching = [item for item in evidence_items if item.startswith(prefix)]
        if not matching:
            missing_markers.append(prefix.rstrip())
            continue
        detail = matching[0][len(prefix):].strip()
        if not detail or any(marker in detail.lower() for marker in weak_detail):
            invalid_markers.append(prefix.rstrip())
    if missing_markers:
        raise ValueError("result evidence is missing required contract markers: " + "; ".join(missing_markers))
    if invalid_markers:
        raise ValueError("result evidence markers need concrete observed proof: " + "; ".join(invalid_markers))

    unresolved_text = "\n".join(str(item) for item in report.get("uncertainty", [])).lower()
    blocking_markers = ("blocking:", "missing required", "incomplete", "failed verification", "not run", "not tested")
    if gate in EXECUTED_CHECK_RESULT_GATES and any(marker in unresolved_text for marker in blocking_markers):
        raise ValueError(f"{gate} result contains unresolved completion evidence; return a non-success status or rework the gate")

    artifact_receipt = _validate_result_artifacts(task_dir, attempt, report)
    result_contract = {
        "gate_acceptance": list(attempt.get("acceptance_criteria") or []),
        "gate_verification": list(attempt.get("verification") or []),
        "task_acceptance": list(task.get("acceptance_criteria") or []) if gate == "close" else [],
        "task_verification": list(task.get("verification") or []) if gate == "close" else [],
    }
    return {
        "schema": RESULT_VALIDATION_SCHEMA,
        "status": "passed",
        "gate": gate,
        "attempt_id": attempt.get("attempt_id"),
        "gate_acceptance_count": len(attempt.get("acceptance_criteria") or []),
        "gate_verification_count": len(attempt.get("verification") or []),
        "task_acceptance_count": len(task.get("acceptance_criteria") or []) if gate == "close" else 0,
        "task_verification_count": len(task.get("verification") or []) if gate == "close" else 0,
        "contract_digest": digest_text(json.dumps(result_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        "dispatch_briefing": briefing_receipt,
        "artifacts": artifact_receipt,
        "validated_at": now(),
    }


def _validate_close_report(task_dir: Path, state: dict[str, Any], attempt: dict[str, Any], report: dict[str, Any]) -> None:
    """Reject a C2/C3 close report that cannot substantiate task completion."""
    if attempt.get("gate") != "close" or not state.get("require_handoff"):
        return
    if not report.get("tests"):
        raise ValueError("C2/C3 close report requires at least one executed verification command or test result")
    if not report.get("evidence"):
        raise ValueError("C2/C3 close report requires observed evidence, not only a completion assertion")
    task = load_task_definition(task_dir, state)
    combined = "\n".join(
        str(item) for field in ("summary", "findings", "evidence", "uncertainty")
        for item in (report.get(field) if isinstance(report.get(field), list) else [report.get(field)])
    ).lower()
    weak_markers = ("not run", "not tested", "unverified", "todo", "tbd", "blocked")
    present_weak_markers = [marker for marker in weak_markers if marker in combined]
    if present_weak_markers:
        raise ValueError("C2/C3 close report contains unresolved completion markers: " + ", ".join(present_weak_markers))
    if not task.get("acceptance_criteria") or not task.get("verification"):
        raise ValueError("C2/C3 close requires a non-empty task acceptance and verification contract")


def _validate_predecessor_review(report: dict[str, Any], report_ids: list[str]) -> None:
    """Require a worker-visible acknowledgement for every injected handoff."""
    required = {safe_id(str(report_id)) for report_id in report_ids}
    if not required:
        return
    acknowledged: set[str] = set()
    for item in report.get("evidence", []):
        rendered = (
            json.dumps(item, ensure_ascii=False, sort_keys=True)
            if isinstance(item, (dict, list)) else str(item)
        )
        if "predecessor review:" not in rendered.lower():
            continue
        acknowledged.update(re.findall(r"report-\d+", rendered.lower()))
    missing = sorted(required - acknowledged)
    if missing:
        raise ValueError(
            "report evidence must acknowledge every supplied predecessor handoff; add exactly one entry like "
            + repr(_predecessor_review_marker(sorted(required)))
        )


def _context_report_payloads(
    task_dir: Path,
    state: dict[str, Any],
    report_ids: list[str],
) -> list[dict[str, Any]]:
    """Load bounded predecessor reports that a facade worker cannot fetch itself."""
    if len(report_ids) > MAX_CONTEXT_REPORTS:
        raise ValueError(
            f"worker context requires {len(report_ids)} predecessor reports but the safe limit is "
            f"{MAX_CONTEXT_REPORTS}; set depends_on to the exact prerequisite phases"
        )
    payloads: list[dict[str, Any]] = []
    used_chars = 0
    for report_id in report_ids:
        normalized_report_id = safe_id(report_id)
        record, _ = read_immutable_json_artifact(
            task_dir,
            state["task_id"],
            f"reports/records/{normalized_report_id}.json",
            kinds={"worker_report", "report_record"},
        )
        if record.get("task_id") != state.get("task_id") or record.get("report_id") != normalized_report_id:
            raise ValueError("context report crosses task scope")
        payload = _compact_report_context(record)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(encoded) > MAX_CONTEXT_REPORT_CHARS:
            payload["findings"] = payload["findings"][:4]
            payload["tests"] = payload["tests"][:4]
            payload["evidence"] = payload["evidence"][:4]
            payload["uncertainty"] = payload["uncertainty"][:4]
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if used_chars + len(encoded) > MAX_CONTEXT_REPORT_CHARS:
            raise ValueError(
                "predecessor handoffs exceed the safe worker-context budget; "
                "set depends_on to the exact prerequisite phases instead of dropping reports implicitly"
            )
        payloads.append(payload)
        used_chars += len(encoded)
    return payloads


def _delegation_report_index(paths: dict[str, Path], task_id: str, attempt_id: str) -> tuple[str, dict[str, Any]]:
    attempt = safe_id(attempt_id)
    task_dir = paths["root"].parent
    root = _task_document_root(task_dir, task_id)
    document_key = f"report_delegation:{attempt}"
    value = db_get_task_document(root, task_id, document_key)
    if value is None:
        value = {"schema": REPORT_SCHEMA, "task_id": task_id, "attempt_id": attempt, "owned_report_ids": [], "context_report_ids": [], "updated_at": now()}
    if value.get("task_id") != task_id or value.get("attempt_id") != attempt:
        raise ValueError("delegation report index scope mismatch")
    if len(value.get("owned_report_ids", [])) > MAX_REPORTS_PER_ATTEMPT or len(value.get("context_report_ids", [])) > MAX_REPORTS_PER_TASK:
        raise ValueError("delegation report index exceeds its bounded capacity")
    return document_key, value


def _write_delegation_report_index(
    paths: dict[str, Path], task_id: str, attempt_id: str, value: dict[str, Any],
) -> None:
    attempt = safe_id(attempt_id)
    if value.get("schema") != REPORT_SCHEMA or value.get("task_id") != task_id or value.get("attempt_id") != attempt:
        raise ValueError("delegation report index scope mismatch")
    root = _task_document_root(paths["root"].parent, task_id)
    db_put_task_document(root, task_id, f"report_delegation:{attempt}", value)


def _delegation_package(task_dir: Path, task_id: str, attempt_id: str) -> dict[str, Any]:
    """Read mutable dispatch metadata from SQLite, never from worker artifacts."""
    attempt = safe_id(attempt_id)
    root = _task_document_root(task_dir, task_id)
    value = db_get_task_document(root, task_id, f"dispatch:{attempt}")
    if value is None or value.get("schema") != SCHEMA or value.get("task_id") != task_id or value.get("attempt_id") != attempt:
        raise ValueError("delegation package is unavailable or out of scope")
    return value


def _write_delegation_package(task_dir: Path, task_id: str, attempt_id: str, value: dict[str, Any]) -> None:
    attempt = safe_id(attempt_id)
    if value.get("schema") != SCHEMA or value.get("task_id") != task_id or value.get("attempt_id") != attempt:
        raise ValueError("delegation package scope mismatch")
    root = _task_document_root(task_dir, task_id)
    db_put_task_document(root, task_id, f"dispatch:{attempt}", value)


def safe_declared_path(value: object, label: str, must_exist: bool = False) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    path = _reject_symlink_ancestry(path, label, allow_missing_leaf=not must_exist)
    if must_exist and not path.exists():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    if any("\x00" in item for item in args):
        raise ValueError("git argument contains NUL")
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, timeout=30, check=False)


def require_lane_lease(state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    require_activation(params)
    lease = state.get("lease")
    principal = str(params.get("principal") or "local")
    if not lease or lease.get("owner") != principal:
        raise ValueError("lane requires a lease owned by this principal")
    if parse_expiry(lease.get("expires_at")) <= datetime.now(timezone.utc):
        raise ValueError("lane lease has expired")
    if params.get("run_id") and lease.get("run_id") != str(params["run_id"]):
        raise ValueError("lane lease belongs to a different run")
    return lease


def save_lane(lane_dir: Path, state_path: Path, state: dict[str, Any], event: str, detail: str) -> dict[str, Any]:
    state["revision"] += 1
    state["updated_at"] = now()
    root = _ledger_root_for_artifact(lane_dir)
    loaded = db_get_lane(root, str(state.get("lane_id") or ""))
    if loaded is None:
        raise ValueError("lane state refers to an unknown lane")
    definition, _ = loaded
    db_put_lane(root, definition, state, event=event, detail=detail)
    append_journal_best_effort(lane_dir, event, detail)
    return state


def load_state(task_id: str, params: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    root, task_dir, state_path = task_paths(task_id, params)
    loaded = db_load_task(root, safe_id(task_id))
    if loaded is None:
        raise ValueError(f"task '{task_id}' does not exist")
    task, state, _, artifact_dir = loaded
    resolved_artifacts = Path(artifact_dir)
    if resolved_artifacts.is_absolute() or ".." in resolved_artifacts.parts:
        raise ValueError("task artifact directory is unsafe")
    task_dir = root / resolved_artifacts
    if state.get("schema") != SCHEMA or task.get("schema") != SCHEMA:
        raise ValueError("task ledger schema is not supported; create a new task")
    return root, task_dir, state


def load_task_definition(task_dir: Path, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load a task's immutable definition from its owning SQLite ledger."""
    root = _ledger_root_for_artifact(task_dir)
    raw_task_id = str((state or {}).get("task_id") or "").strip()
    task_id = safe_id(raw_task_id) if raw_task_id else ""
    if not task_id:
        task_dir_resolved = task_dir.resolve()
        for candidate_id in db_task_index(root):
            candidate_path = db_task_artifact_path(root, candidate_id)
            if candidate_path is not None and candidate_path.resolve() == task_dir_resolved:
                task_id = candidate_id
                break
    loaded = db_load_task(root, task_id) if task_id else None
    if loaded is None:
        raise ValueError("task definition is unavailable")
    definition, _, _, _ = loaded
    if definition.get("schema") != SCHEMA or definition.get("task_id") != task_id:
        raise ValueError("task definition schema or identity is invalid")
    return definition


def load_task_state_for_artifact(task_dir: Path) -> dict[str, Any]:
    """Diagnostic/test helper: resolve an artifact directory to canonical SQLite state."""
    root = _ledger_root_for_artifact(task_dir)
    target = task_dir.resolve()
    for task_id in db_task_index(root):
        candidate = db_task_artifact_path(root, task_id)
        if candidate is not None and candidate.resolve() == target:
            loaded = db_load_task(root, task_id)
            if loaded is not None:
                return loaded[1]
    raise ValueError("task artifact directory is not registered in the SQLite ledger")


def guard_revision(state: dict[str, Any], expected: int | None) -> None:
    if expected is not None and state["revision"] != expected:
        raise ValueError(f"stale revision: expected {expected}, actual {state['revision']}")


def save_state(task_dir: Path, state_path: Path, state: dict[str, Any], event: str, detail: str) -> dict[str, Any]:
    state["revision"] += 1
    state["updated_at"] = now()
    db_update_task_state(_ledger_root_for_artifact(task_dir), state, event=event, detail=detail)
    append_journal_best_effort(task_dir, event, detail)
    return state


def next_gate(state: dict[str, Any]) -> str | None:
    done = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    return next((gate for gate in state["current_pipeline"] if gate not in done), None)


def active_gates(state: dict[str, Any]) -> list[str]:
    """Return the unfinished gates in the first executable parallel wave."""
    done = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    groups = state.get("parallel_groups") or [[gate] for gate in state["current_pipeline"]]
    for group in groups:
        pending = [gate for gate in group if gate not in done]
        if pending:
            return pending
    return []


def sync_current_wave(state: dict[str, Any]) -> list[str]:
    """Persist the explicit executable wave as the only current-gate state."""
    wave = active_gates(state)
    state["current_gates"] = wave
    return wave


def primary_gate(state: dict[str, Any]) -> str | None:
    """Return the first gate in the current wave without storing a scalar alias."""
    wave = active_gates(state)
    return wave[0] if wave else None


def _plan_approval(state: dict[str, Any]) -> dict[str, Any]:
    """Return the durable plan-review record, tolerating pre-feature ledgers."""
    value = state.get("plan_approval")
    if isinstance(value, dict):
        return value
    return {"policy": "auto", "status": "not_required", "history": []}


def _plan_approval_is_pending(state: dict[str, Any]) -> bool:
    approval = _plan_approval(state)
    return (
        approval.get("policy") == "required"
        and approval.get("status") == "awaiting_user"
    )


def normalize_parallel_groups(groups: Any, pipeline: list[str]) -> list[list[str]]:
    """Validate orchestrator-selected independent gate waves."""
    if groups is None:
        return [[gate] for gate in pipeline]
    if not isinstance(groups, list):
        raise ValueError("parallel_groups must be an array of gate arrays")
    normalized: list[list[str]] = []
    seen: set[str] = set()
    positions = {gate: index for index, gate in enumerate(pipeline)}
    for raw_group in groups:
        if not isinstance(raw_group, list) or not raw_group:
            raise ValueError("parallel_groups entries must be non-empty arrays")
        group = normalize_pipeline(raw_group)
        if any(gate not in positions for gate in group):
            raise ValueError("parallel_groups may contain only gates from pipeline")
        overlap = seen & set(group)
        if overlap:
            raise ValueError("parallel_groups may not repeat gates: " + ", ".join(sorted(overlap)))
        if any(gate in {"documentation", "close"} for gate in group) and len(group) > 1:
            raise ValueError("documentation and close gates cannot share a parallel group")
        seen.update(group)
        normalized.append(group)
    for gate in pipeline:
        if gate not in seen:
            normalized.append([gate])
    normalized.sort(key=lambda group: min(positions[gate] for gate in group))
    return normalized


def canonical_pipeline_gate(gate: Any) -> str:
    """Return the canonical ledger ID for a gate label.

    Only documented aliases are rewritten.  This is intentionally performed
    before the syntax and availability checks so aliases work consistently in
    both ``pipeline`` and ``parallel_groups`` without weakening validation.
    """
    value = str(gate).strip().lower()
    value = re.sub(r"[\s-]+", "_", value)
    return PIPELINE_GATE_ALIASES.get(value, value)


def canonical_profile(profile: Any) -> str:
    """Normalize a documented human-facing profile alias."""
    value = str(profile).strip().lower().replace("-", "_").replace(" ", "_")
    return PROFILE_ALIASES.get(value, value)


def normalize_pipeline(pipeline: list[Any]) -> list[str]:
    result = [canonical_pipeline_gate(gate) for gate in pipeline]
    if not result or len(result) != len(set(result)) or any(not GATE_RE.fullmatch(gate) for gate in result):
        raise ValueError("pipeline gates must be unique lowercase ids matching [a-z][a-z0-9_-]{0,63}")
    return result


def validate_pipeline_invariants(state: dict[str, Any], pipeline: list[str] | None = None) -> None:
    candidate = pipeline or state["current_pipeline"]
    if state.get("require_handoff"):
        missing = {"documentation", "close"} - set(candidate)
        if missing:
            raise ValueError("C2/C3 pipelines must retain documentation and close gates")
        if candidate.index("documentation") > candidate.index("close"):
            raise ValueError("documentation must run before close")
    normalize_parallel_groups(state.get("parallel_groups"), candidate)


def validate_completion_invariants(state: dict[str, Any]) -> None:
    validate_pipeline_invariants(state)
    if not state.get("require_handoff"):
        return
    attempts = [item for item in state.get("attempts", []) if not item.get("invalidated")]
    non_terminal = [item["attempt_id"] for item in attempts if item.get("status") not in TERMINAL_ATTEMPT_STATUSES]
    if non_terminal:
        raise ValueError("C2/C3 completion requires every attempt to be terminal: " + ", ".join(non_terminal))
    evidence = [item for item in state.get("evidence", []) if not item.get("invalidated")]
    missing_evidence = [
        item["attempt_id"]
        for item in attempts
        if item.get("status") == "passed"
        if not any(record.get("attempt_id") == item["attempt_id"] for record in evidence)
    ]
    if missing_evidence:
        raise ValueError("C2/C3 completion requires evidence for every attempt: " + ", ".join(missing_evidence))
    missing_reports = [
        item["attempt_id"]
        for item in attempts
        if item.get("status") == "passed"
        if not any(
            record.get("attempt_id") == item["attempt_id"]
            and record.get("report_id")
            and record.get("report_receipt")
            for record in evidence
        )
    ]
    if missing_reports:
        raise ValueError("C2/C3 completion requires a consumed report receipt for every attempt: " + ", ".join(missing_reports))
    if "documentation" not in state.get("completed_gates", []) or not state.get("documentation_receipt"):
        raise ValueError("C2/C3 completion requires documentation decision evidence")
    if "close" not in state.get("completed_gates", []):
        raise ValueError("C2/C3 completion requires the close gate")
    if not state.get("reassessment_receipts"):
        raise ValueError("C2/C3 completion requires a reassessment receipt")
    if not state.get("handoff_created") or state.get("handoff_gate") != "close":
        raise ValueError("C2/C3 completion requires a final close handoff")


def lock_key(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:32]


def _global_claims(root: Path) -> tuple[Path, dict[str, Any]]:
    path = root / "cortex.db"
    claims = db_get_global(root, "resource_claims", {})
    active: dict[str, Any] = {}
    for key, entry in claims.items():
        try:
            if parse_expiry(entry.get("expires_at")) > datetime.now(timezone.utc):
                active[key] = entry
        except ValueError:
            continue
    return path, active


def _claim_global_resource(root: Path, resource: str, owner: str, scope_kind: str, scope_id: str, expires_at: object, kind: str = "resource") -> dict[str, Any]:
    expiry = parse_expiry(expires_at)
    if expiry <= datetime.now(timezone.utc):
        raise ValueError("resource claim expires_at must be in the future")
    path, claims = _global_claims(root)
    key = digest_text(resource)
    existing = claims.get(key)
    owner_digest = digest_text(owner)
    if existing and (existing.get("owner_digest"), existing.get("scope_kind"), existing.get("scope_id")) != (owner_digest, scope_kind, scope_id):
        raise ValueError(f"resource already claimed globally: {redact(resource, 300)}")
    entry = {"resource": redact(resource, 500), "owner": redact(owner, 256), "owner_digest": owner_digest, "scope_kind": scope_kind, "scope_id": scope_id, "kind": redact(kind, 128), "expires_at": expiry.isoformat(), "claimed_at": existing.get("claimed_at") if existing else now()}
    claims[key] = entry
    db_put_global(root, "resource_claims", claims)
    return entry


def _release_global_resource(root: Path, resource: str, owner: str, scope_kind: str, scope_id: str) -> None:
    claims = db_get_global(root, "resource_claims", {})
    key = digest_text(resource)
    existing = claims.get(key)
    if not existing or (existing.get("owner_digest"), existing.get("scope_kind"), existing.get("scope_id")) != (digest_text(owner), scope_kind, scope_id):
        raise ValueError("global resource is not held by this owner and scope")
    del claims[key]
    if claims:
        db_put_global(root, "resource_claims", claims)
    else:
        db_delete_global(root, "resource_claims")


def _insert_gate(current: list[str], gate: str, operation: dict[str, Any]) -> None:
    if not GATE_RE.fullmatch(gate) or gate in current:
        raise ValueError(f"cannot add invalid or existing gate '{gate}'")
    if "index" in operation:
        index = max(0, min(int(operation["index"]), len(current)))
    elif operation.get("before"):
        target = str(operation["before"])
        if target not in current:
            raise ValueError(f"add.before gate '{target}' does not exist")
        index = current.index(target)
    elif operation.get("after"):
        target = str(operation["after"])
        if target not in current:
            raise ValueError(f"add.after gate '{target}' does not exist")
        index = current.index(target) + 1
    else:
        index = len(current)
    current.insert(index, gate)


def apply_pipeline_operations(state: dict[str, Any], pipeline: list[Any] | None = None, operations: list[dict[str, Any]] | None = None, allow_rework: bool = False, parallel_groups: Any = None) -> dict[str, Any]:
    previous = list(state["current_pipeline"])
    current = normalize_pipeline(pipeline if pipeline is not None else previous)
    previous_groups = normalize_parallel_groups(state.get("parallel_groups"), previous)
    explicit_groups = parallel_groups is not None
    groups = normalize_parallel_groups(parallel_groups if explicit_groups else (None if pipeline is not None else previous_groups), current)
    completed = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    reset: set[str] = set()
    removed_by_replacement = set(previous) - set(current) if pipeline is not None else set()
    if removed_by_replacement & completed and not allow_rework:
        raise ValueError("completed gates cannot be removed: " + ", ".join(sorted(removed_by_replacement & completed)))
    reset.update(removed_by_replacement)
    for operation in operations or []:
        op = str(operation.get("op", "")).lower()
        gate = str(operation.get("gate", "")).strip().lower()
        if op == "add":
            _insert_gate(current, gate, operation)
        elif op == "remove":
            if gate in completed and not allow_rework:
                raise ValueError(f"cannot remove completed gate '{gate}'")
            if gate not in current:
                raise ValueError(f"cannot remove unknown gate '{gate}'")
            current.remove(gate)
            reset.add(gate)
        elif op == "move":
            if gate not in current:
                raise ValueError(f"cannot move unknown gate '{gate}'")
            current.remove(gate)
            _insert_gate(current, gate, {**operation, "index": operation.get("index", len(current))} if not (operation.get("before") or operation.get("after")) else operation)
        elif op == "replace":
            replacement = normalize_pipeline(operation.get("with", []))
            if gate not in current:
                raise ValueError(f"cannot replace unknown gate '{gate}'")
            if gate in completed and not allow_rework:
                raise ValueError(f"cannot replace completed gate '{gate}'")
            current[current.index(gate):current.index(gate) + 1] = replacement
            reset.add(gate)
        elif op == "rework":
            if not allow_rework:
                raise ValueError("rework requires allow_rework=true")
            if gate not in current:
                raise ValueError(f"cannot rework unknown gate '{gate}'")
            reset.add(gate)
        else:
            raise ValueError(f"unsupported pipeline operation '{op}'")
        current = normalize_pipeline(current)
    missing = sorted(completed - set(current))
    if missing and not allow_rework:
        raise ValueError(f"completed gates cannot be removed: {', '.join(missing)}")
    if not explicit_groups and pipeline is None:
        # Operations preserve existing waves for surviving gates and place new
        # gates in their own singleton wave until the orchestrator groups them.
        surviving = {gate for group in previous_groups for gate in group if gate in current}
        groups = normalize_parallel_groups(
            [ [gate for gate in group if gate in current] for group in previous_groups if any(gate in current for gate in group) ]
            + [[gate] for gate in current if gate not in surviving], current,
        )
    validate_pipeline_invariants({**state, "parallel_groups": groups}, current)
    return {"pipeline": current, "parallel_groups": groups, "changed": current != state["current_pipeline"] or groups != previous_groups, "parallel_groups_changed": groups != previous_groups, "from": list(state["current_pipeline"]), "operations": operations or [], "reset_gates": sorted(reset)}


def append_pipeline_change(state: dict[str, Any], change: dict[str, Any], reason: str, signals: list[Any] | None = None) -> None:
    state["current_pipeline"] = change["pipeline"]
    state["parallel_groups"] = change.get("parallel_groups") or normalize_parallel_groups(None, state["current_pipeline"])
    state["pipeline_changes"].append({"at": now(), "reason": reason, "from": change["from"], "to": change["pipeline"], "parallel_groups": state["parallel_groups"], "operations": change["operations"], "signals": signals or []})
    state.setdefault("adaptive_events", []).append({"at": now(), "reason": reason, "signals": signals or [], "operations": change["operations"], "pipeline": change["pipeline"], "parallel_groups": state["parallel_groups"]})
    reset_gates = set(change.get("reset_gates", []))
    for gate in list(reset_gates):
        if gate in state["current_pipeline"]:
            reset_gates.update(state["current_pipeline"][state["current_pipeline"].index(gate):])
    for gate in sorted(reset_gates):
        state["completed_gates"] = [item for item in state.get("completed_gates", []) if item != gate]
        state["skipped_gates"] = [item for item in state.get("skipped_gates", []) if item != gate]
        state.get("gates", {}).pop(gate, None)
        for evidence in state.get("evidence", []):
            if evidence.get("gate") == gate:
                evidence["invalidated"] = True
        for attempt in state.get("attempts", []):
            if attempt.get("gate") == gate:
                attempt["invalidated"] = True


def invalidate_reworked_report_receipts(task_dir: Path, state: dict[str, Any]) -> None:
    invalidated_attempts = {item["attempt_id"] for item in state.get("attempts", []) if item.get("invalidated")}
    paths = report_bus_paths(task_dir)
    index = _report_index(paths, state["task_id"])
    root = _task_document_root(task_dir, state["task_id"])
    for metadata in index.get("reports", []):
        if not isinstance(metadata, dict):
            continue
        report_id = safe_id(str(metadata.get("report_id") or ""))
        if not report_id:
            continue
        receipt, record = _load_report_receipt(task_dir, state, f"report-receipt-{report_id}")
        if receipt.get("attempt_id") in invalidated_attempts and not receipt.get("invalidated"):
            receipt["invalidated"] = True
            receipt["invalidated_at"] = now()
            db_put_task_document(root, state["task_id"], f"receipt_state:{receipt['receipt_id']}", receipt)
            _recover_report_receipt(paths, record, state, invalidated=True)


def create_lane(params: dict[str, Any]) -> dict[str, Any]:
    lane_id = safe_id(str(params["lane_id"]))
    root = ledger_root(params)
    require_activation(params)
    with state_lock(root):
        _, lane_dir, state_path = lane_paths(lane_id, params)
        existing_lane = db_get_lane(root, lane_id)
        if existing_lane is not None:
            _, existing = existing_lane
            if existing.get("schema") != SCHEMA:
                raise ValueError("task ledger schema is not supported; create a new task")
            if existing.get("owner") != str(params.get("principal") or "local"):
                raise ValueError(f"lane '{lane_id}' already belongs to another principal")
            return {"created": False, "lane_id": lane_id, "state": existing}
        owner = redact(params.get("principal") or "local", 256)
        mode = str(params.get("mode", "ephemeral"))
        if mode not in {"ephemeral", "persistent"}:
            raise ValueError("lane mode must be ephemeral or persistent")
        declarations = params.get("declarations", [])
        if not isinstance(declarations, list):
            raise ValueError("lane declarations must be an array")
        lane_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        preflight_journal(lane_dir)
        state = {"schema": SCHEMA, "lane_id": lane_id, "status": "active", "owner": owner, "mode": mode, "purpose": redact(params.get("purpose", ""), 2000), "declarations": sanitize_structured(declarations), "lease": None, "resources": {}, "bound_tasks": [], "recovery_events": [], "materializations": [], "revision": 0, "created_at": now(), "updated_at": now()}
        definition = {"schema": SCHEMA, "lane_id": lane_id, "owner": owner, "mode": mode, "purpose": state["purpose"], "declarations": state["declarations"], "created_at": state["created_at"]}
        db_put_lane(root, definition, state, event="initialized", detail=f"{mode} lane")
        append_journal_best_effort(lane_dir, "initialized", f"{mode} lane")
        return {"created": True, "lane_id": lane_id, "state": state}


def lane_status(params: dict[str, Any]) -> dict[str, Any]:
    root, lane_dir, state = load_lane(str(params["lane_id"]), params)
    authorize_principal({"principal": state.get("owner")}, params)
    loaded = db_get_lane(root, str(state.get("lane_id") or ""))
    if loaded is None:
        raise ValueError("lane definition is unavailable")
    definition, _ = loaded
    return {"lane": definition, "state": state}


def claim_lane(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, lane_dir, state = load_lane(str(params["lane_id"]), params)
        authorize({"principal": state["owner"]}, params)
        if state["status"] != "active":
            raise ValueError("only active lanes can be claimed")
        expires_at = parse_expiry(params.get("expires_at"))
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("lane lease must expire in the future")
        existing = state.get("lease")
        if existing:
            existing_expiry = parse_expiry(existing.get("expires_at"))
            if existing_expiry > datetime.now(timezone.utc):
                if existing.get("owner") == str(params.get("principal") or "local") and existing.get("run_id") == str(params.get("run_id", "")):
                    return {"state": state, "reclaimed": False}
                raise ValueError("lane already has a live lease")
            if not params.get("reclaim"):
                raise ValueError("lane lease is expired; pass reclaim=true to recover it")
            state.setdefault("recovery_events", []).append({"at": now(), "kind": "expired_lease_reclaimed", "previous_owner": existing.get("owner"), "previous_run_id": existing.get("run_id")})
        state["lease"] = {"owner": str(params.get("principal") or "local"), "run_id": redact(params.get("run_id", ""), 256), "expires_at": expires_at.isoformat(), "acquired_at": now()}
        save_lane(lane_dir, lane_dir / "state.sqlite", state, "lease", f"claimed by {state['lease']['owner']}")
        return {"state": state, "reclaimed": bool(existing)}


def release_lane(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    require_activation(params)
    with state_lock(root):
        _, lane_dir, state = load_lane(str(params["lane_id"]), params)
        lease = state.get("lease")
        if not lease or lease.get("owner") != str(params.get("principal") or "local"):
            raise ValueError("lane is not leased by this principal")
        if params.get("run_id") and lease.get("run_id") != str(params["run_id"]):
            raise ValueError("lane lease belongs to a different run")
        state["lease"] = None
        save_lane(lane_dir, lane_dir / "state.sqlite", state, "release", "lane lease released")
        return {"state": state}


def materialize_lane(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, lane_dir, state = load_lane(str(params["lane_id"]), params)
        require_lane_lease(state, params)
        if not params.get("confirm"):
            raise ValueError("materialize_lane requires confirm=true")
        if state["status"] != "active":
            raise ValueError("cannot materialize a retired lane")
        prepared = []
        for declaration in state.get("declarations", []):
            if not isinstance(declaration, dict):
                raise ValueError("lane declarations must be objects for materialization")
            repo = safe_declared_path(declaration.get("repo_path"), "repo_path", must_exist=True)
            worktree = safe_declared_path(declaration.get("worktree_path"), "worktree_path")
            branch = str(declaration.get("branch", ""))
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,190}", branch) or ".." in branch:
                raise ValueError(f"invalid declared branch: {branch}")
            probe = run_git(repo, ["rev-parse", "--show-toplevel"])
            if probe.returncode != 0:
                raise ValueError(f"repo_path is not a Git repository: {repo}")
            if worktree.exists():
                branch_probe = run_git(worktree, ["branch", "--show-current"])
                if branch_probe.returncode != 0 or branch_probe.stdout.strip() != branch:
                    raise ValueError(f"existing worktree does not match declared branch: {worktree}")
                prepared.append({"repo": repo, "worktree": worktree, "branch": branch, "status": "existing", "command": None})
            else:
                if worktree.parent.exists() and worktree.parent.is_symlink():
                    raise ValueError("worktree parent must not be a symlink")
                sync_from = str(declaration.get("sync_from", ""))
                if sync_from:
                    command = ["worktree", "add", "-b", branch, str(worktree), sync_from]
                else:
                    source_probe = run_git(repo, ["show-ref", "--verify", f"refs/heads/{branch}"])
                    if source_probe.returncode != 0:
                        raise ValueError(f"new branch {branch} requires sync_from")
                    command = ["worktree", "add", str(worktree), branch]
                prepared.append({"repo": repo, "worktree": worktree, "branch": branch, "status": "created", "command": command})
        results, created = [], []
        try:
            for item in prepared:
                repo, worktree, branch, status, command = item["repo"], item["worktree"], item["branch"], item["status"], item["command"]
                if command is not None:
                    result = run_git(repo, command)
                    if result.returncode != 0:
                        raise ValueError(f"git worktree add failed: {redact(result.stderr, 1000)}")
                    created.append((repo, worktree))
                results.append({"repo_path": str(repo), "worktree_path": str(worktree), "branch": branch, "status": status, "managed": status == "created"})
        except Exception:
            for repo, worktree in reversed(created):
                run_git(repo, ["worktree", "remove", str(worktree)])
            raise
        state["materializations"] = results
        save_lane(lane_dir, lane_dir / "state.sqlite", state, "materialize", f"materialized {len(results)} declaration(s)")
        return {"state": state, "materializations": results}


def reconcile_lane(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, lane_dir, state = load_lane(str(params["lane_id"]), params)
        require_lane_lease(state, params)
        results = []
        for declaration in state.get("declarations", []):
            if not isinstance(declaration, dict):
                results.append({"status": "invalid_declaration"})
                continue
            worktree = safe_declared_path(declaration.get("worktree_path"), "worktree_path")
            expected_branch = str(declaration.get("branch", ""))
            if not worktree.exists():
                results.append({"worktree_path": str(worktree), "branch": expected_branch, "status": "missing"})
                continue
            branch_probe = run_git(worktree, ["branch", "--show-current"])
            dirty_probe = run_git(worktree, ["status", "--porcelain"])
            results.append({"worktree_path": str(worktree), "branch": branch_probe.stdout.strip(), "expected_branch": expected_branch, "dirty": bool(dirty_probe.stdout.strip()), "status": "ok" if branch_probe.returncode == 0 and branch_probe.stdout.strip() == expected_branch else "drift"})
        state["last_reconcile"] = {"at": now(), "results": results}
        save_lane(lane_dir, lane_dir / "state.sqlite", state, "reconcile", f"reconciled {len(results)} declaration(s)")
        return {"state": state, "results": results}


def retire_lane(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, lane_dir, state = load_lane(str(params["lane_id"]), params)
        authorize({"principal": state["owner"]}, params)
        if state.get("lease"):
            raise ValueError("cannot retire a leased lane")
        if not params.get("clean"):
            raise ValueError("lane retirement requires clean=true")
        for key, entry in list(state.get("resources", {}).items()):
            try:
                expired = parse_expiry(entry.get("expires_at")) <= datetime.now(timezone.utc)
            except ValueError:
                expired = True
            if expired:
                state["resources"].pop(key, None)
        if state.get("resources"):
            raise ValueError("cannot retire a lane with active resource claims")
        if state.get("materializations"):
            if not params.get("confirm"):
                raise ValueError("retiring materialized worktrees requires confirm=true")
            for item in state["materializations"]:
                if not item.get("managed", item.get("status") == "created"):
                    continue
                repo = safe_declared_path(item["repo_path"], "repo_path", must_exist=True)
                worktree = safe_declared_path(item["worktree_path"], "worktree_path", must_exist=True)
                dirty = run_git(worktree, ["status", "--porcelain"])
                if dirty.returncode != 0 or dirty.stdout.strip():
                    raise ValueError(f"refusing to retire dirty worktree: {worktree}")
                removed = run_git(repo, ["worktree", "remove", str(worktree)])
                if removed.returncode != 0:
                    raise ValueError(f"git worktree remove failed: {redact(removed.stderr, 1000)}")
            state["materializations"] = []
        state["status"] = "retired"
        save_lane(lane_dir, lane_dir / "state.sqlite", state, "retire", "clean retirement")
        return {"state": state}


def bind_task_lane(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        _, lane_dir, lane = load_lane(str(params["lane_id"]), params)
        authorize_principal({"principal": lane.get("owner")}, params)
        if lane["status"] != "active":
            raise ValueError("cannot bind a task to a retired lane")
        if state.get("lane_id") and state["lane_id"] != lane["lane_id"]:
            raise ValueError("task is already bound to another lane")
        state["lane_id"] = lane["lane_id"]
        if state["task_id"] not in lane.setdefault("bound_tasks", []):
            lane["bound_tasks"].append(state["task_id"])
        save_state(task_dir, task_dir / "state.sqlite", state, "lane", f"bound to {lane['lane_id']}")
        save_lane(lane_dir, lane_dir / "state.sqlite", lane, "bind", state["task_id"])
        return {"state": state, "lane": lane}


def claim_lane_resource(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, lane_dir, state = load_lane(str(params["lane_id"]), params)
        authorize({"principal": state["owner"]}, params)
        if state["status"] != "active":
            raise ValueError("cannot claim a resource on a retired lane")
        expiry = parse_expiry(params.get("expires_at"))
        path, owner = str(params["path"]), str(params["owner"])
        key = lock_key(path)
        existing = state["resources"].get(key)
        if existing and parse_expiry(existing.get("expires_at")) > datetime.now(timezone.utc) and existing.get("owner_digest") != digest_text(owner):
            raise ValueError(f"lane resource already claimed: {redact(path, 300)}")
        global_entry = _claim_global_resource(root, path, owner, "lane", state["lane_id"], expiry.isoformat(), str(params.get("kind", "resource")))
        state["resources"][key] = {"resource": redact(path, 500), "owner": redact(owner, 256), "owner_digest": digest_text(owner), "expires_at": expiry.isoformat(), "claimed_at": global_entry["claimed_at"], "kind": redact(params.get("kind", "resource"), 128), "global": True}
        save_lane(lane_dir, lane_dir / "state.sqlite", state, "resource", f"{redact(path, 300)} → {redact(owner, 128)}")
        return {"state": state, "advisory": False}


def release_lane_resource(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    require_activation(params)
    with state_lock(root):
        _, lane_dir, state = load_lane(str(params["lane_id"]), params)
        authorize({"principal": state["owner"]}, params)
        path = str(params["path"])
        key = lock_key(path)
        existing = state["resources"].get(key)
        if not existing or existing.get("owner_digest") != digest_text(str(params["owner"])):
            raise ValueError("lane resource is not held by this owner")
        _release_global_resource(root, path, str(params["owner"]), "lane", state["lane_id"])
        del state["resources"][key]
        save_lane(lane_dir, lane_dir / "state.sqlite", state, "resource_release", f"{redact(path, 300)} released")
        return {"state": state}


def classify(params: dict[str, Any]) -> dict[str, Any]:
    complexity = str(params.get("complexity", "C2")).upper()
    if complexity not in BASE_PIPELINES:
        raise ValueError("complexity must be C1, C2, or C3")
    requirements = " ".join(str(item).lower() for item in params.get("requirements", []))
    tokens = set(re.findall(r"[a-z0-9]+", requirements))
    proposed_pipeline = params.get("pipeline")
    pipeline_source = "heuristic"
    pipeline_corrections = []
    if proposed_pipeline is not None:
        pipeline = normalize_pipeline(proposed_pipeline)
        if isinstance(proposed_pipeline, list):
            for raw_gate, canonical_gate in zip(proposed_pipeline, pipeline):
                raw_label = str(raw_gate).strip().lower()
                normalized_label = re.sub(r"[\s-]+", "_", raw_label)
                if normalized_label != canonical_gate:
                    pipeline_corrections.append({
                        "from": str(raw_gate),
                        "to": canonical_gate,
                        "reason": "canonical gate alias",
                    })
        unknown = sorted(set(pipeline) - AVAILABLE_GATES)
        if unknown:
            raise ValueError("pipeline contains unsupported gate ids: " + ", ".join(unknown))
        pipeline_source = "orchestrator"
    else:
        pipeline = list(BASE_PIPELINES[complexity])
    additions, addition_reasons = [], {}

    def has(*words: str) -> bool:
        return any(
            (word in requirements)
            if (" " in word or "-" in word or not word.isascii())
            else (word in tokens)
            for word in words
        )

    def add_before(gate: str, target: str, reason: str) -> None:
        if gate not in pipeline:
            pipeline.insert(pipeline.index(target) if target in pipeline else len(pipeline), gate)
            additions.append(gate)
            addition_reasons.setdefault(gate, []).append(reason)

    def ensure_mandatory(gate: str, reason: str) -> None:
        if gate not in pipeline:
            # Mandatory gates are enforcement corrections, not optional
            # gates inferred from task signals. Keep them out of
            # ``conditional_gates`` so the response clearly distinguishes
            # the orchestrator's proposal from Cortex's audit invariants.
            target = "close" if gate in {"documentation", "review"} else None
            pipeline.insert(pipeline.index(target) if target in pipeline else len(pipeline), gate)
            pipeline_corrections.append({"gate": gate, "reason": reason})

    if proposed_pipeline is not None:
        for gate in MANDATORY_PIPELINE_GATES[complexity]:
            ensure_mandatory(gate, f"mandatory {complexity} audit gate")
        if pipeline.index("documentation") > pipeline.index("close"):
            pipeline.remove("documentation")
            pipeline.insert(pipeline.index("close"), "documentation")
            pipeline_corrections.append({"gate": "documentation", "reason": "documentation must precede close"})
        if pipeline[-1] != "close":
            pipeline.remove("close")
            pipeline.append("close")
            pipeline_corrections.append({"gate": "close", "reason": "close must be the final gate"})

    if proposed_pipeline is None and has(
        "security", "auth", "permission", "secret", "privacy",
        "безопасност", "авторизац", "аутентиф", "разрешен", "секрет", "приватност",
    ):
        add_before("security", "review", "security or authorization concerns")
    if proposed_pipeline is None and has(
        "architecture", "design", "contract", "refactor", "cross-cutting",
        "архитектур", "контракт", "рефактор", "сквозн",
    ):
        add_before(
            "architecture",
            "plan" if "plan" in pipeline else "implementation",
            "architecture, design, contract, or cross-cutting change",
        )

    # Do not treat generic words such as ``schema`` or ``migration`` as proof
    # of database work: manifests, JSON schemas, API contracts, and source
    # migrations use those words too. Require an explicit datastore/data-model
    # signal or a database-specific phrase before adding this specialist gate.
    database_signals = (
        (r"\b(?:database|databases|db|sql|nosql|postgres(?:ql)?|mysql|sqlite|mongodb|redis|orm|persistence|datastore|data\s+store)\b", "database or datastore terminology"),
        (r"\bdata\s+model\b", "data model"),
        (r"\b(?:database|data|schema)\s+migrations?\b", "database/data/schema migration"),
        (r"\bmigrations?\s+(?:of|for|to)\s+(?:the\s+)?(?:database|data|schema)\b", "database/data migration"),
        (r"\b(?:database|relational|sql|data)\s+schema\b", "database/data schema"),
        (r"\b(?:database|table)\s+(?:design|index|transaction)\b", "database table/index/transaction design"),
    )
    database_reason = next((reason for pattern, reason in database_signals if re.search(pattern, requirements)), None)
    if database_reason is None and has("база данных", "базы данных", "бд", "sql", "postgres", "mysql", "sqlite", "схема данных"):
        database_reason = "database or datastore terminology"
    if proposed_pipeline is None and database_reason:
        add_before("database_architecture", "plan" if "plan" in pipeline else "implementation", database_reason)
    if proposed_pipeline is None and has("performance", "latency", "load", "benchmark", "производительн", "задержк", "нагрузк", "бенчмарк"):
        add_before("performance", "review", "performance or load concern")
    if proposed_pipeline is None and has("accessibility", "a11y", "screen reader", "keyboard", "доступност", "скринридер", "клавиатур"):
        add_before("accessibility", "review", "accessibility requirement")
    if proposed_pipeline is None and has("frontend", "ui", "ux", "design system", "фронтенд", "интерфейс", "дизайн-систем"):
        add_before("ux", "plan" if "plan" in pipeline else "implementation", "UI/UX or design-system work")
    if proposed_pipeline is None and has("documentation", "docs", "runbook", "adr", "документац", "доки", "ранбук"):
        add_before("documentation", "close", "explicit documentation deliverable")
    parallel_groups = normalize_parallel_groups(params.get("parallel_groups"), pipeline)
    implementation_selection = select_implementation_profile({
        "objective": params.get("objective", ""),
        "requirements": params.get("requirements", []),
    })
    roles = {"scope": ["planner"], "plan": ["planner"], "discover": ["explorer"], "architecture": ["architect"], "database_architecture": ["database_architect"], "implementation": [implementation_selection["profile"]], "qa": ["qa_engineer", "build_verification"], "security": ["security_auditor"], "performance": ["performance_engineer"], "accessibility": ["accessibility_engineer"], "ux": ["ux_designer"], "review": ["code_reviewer"], "documentation": ["technical_writer"], "close": ["build_verification"]}
    return {"complexity": complexity, "base_pipeline": BASE_PIPELINES[complexity], "pipeline": pipeline, "parallel_groups": parallel_groups, "pipeline_source": pipeline_source, "pipeline_corrections": pipeline_corrections, "conditional_gates": additions, "conditional_gate_reasons": addition_reasons, "available_gates": sorted(AVAILABLE_GATES), "suggested_roles": {gate: roles.get(gate, profiles_for_gate(gate)) for gate in pipeline}, "implementation_selection": implementation_selection}


def init_task(params: dict[str, Any]) -> dict[str, Any]:
    task_id = safe_id(str(params["task_id"]))
    root = ledger_root(params)
    activation = require_activation(params, task_id)
    with state_lock(root):
        _, task_dir, state_path = task_paths(task_id, params)
        existing_task = db_load_task(root, task_id)
        if existing_task is not None:
            task_definition, existing, _, artifact_dir = existing_task
            task_dir = root / artifact_dir
            if existing.get("schema") != SCHEMA:
                raise ValueError("task ledger schema is not supported; create a new task")
            authorize_principal(existing, params)
            requested_objective = str(params.get("objective", "")).strip()
            stored_objective = str(task_definition.get("objective", ""))
            objective_correction = (
                {"requested": requested_objective, "used": stored_objective, "source": "immutable_task_definition"}
                if requested_objective and requested_objective != stored_objective else None
            )
            activations = _activation_records(root)
            activation_id = activation_key(params)
            current_activation = activations.get(activation_id)
            if not isinstance(current_activation, dict) or current_activation.get("schema") != SCHEMA:
                raise ValueError("orchestration activation disappeared while resuming task initialization")
            current_activation["task_id"] = task_id
            current_activation["initialized_at"] = current_activation.get("initialized_at") or now()
            current_activation["resumed_at"] = now()
            activations[activation_id] = current_activation
            _write_activation_records(root, activations)
            return {"created": False, "resumed": True, "task_id": task_id, "state": existing, "objective_correction": objective_correction, "ledger_root": str(root)}
        if not str(params.get("classification_id", "")).strip():
            raise ValueError("init_task requires a prior classify_task classification_id")
        classification_id = safe_id(str(params["classification_id"]))
        receipt = db_get_classification(root, classification_id)
        if receipt is None:
            raise ValueError("init_task requires a prior classify_task classification_id")
        if receipt.get("schema") != SCHEMA:
            raise ValueError("classification receipt schema is not supported; classify the task again")
        if receipt.get("consumed_by"):
            raise ValueError("classification receipt has already been consumed")
        mismatch = []
        if receipt.get("activation_key") != activation_key(params):
            mismatch.append("activation")
        if mismatch:
            raise ValueError("classification receipt does not match this " + ", ".join(mismatch))
        classification = receipt["classification"]
        receipt_requirements = receipt.get("requirements")
        if not isinstance(receipt_requirements, list) or any(not isinstance(item, str) for item in receipt_requirements):
            raise ValueError("classification receipt requirements are invalid; classify the task again")
        pipeline = normalize_pipeline(classification["pipeline"])
        parallel_groups = normalize_parallel_groups(classification.get("parallel_groups"), pipeline)
        requested_pipeline = params.get("pipeline")
        pipeline_correction = None
        if requested_pipeline is not None:
            try:
                normalized_requested_pipeline = normalize_pipeline(requested_pipeline)
            except ValueError:
                normalized_requested_pipeline = None
            if normalized_requested_pipeline != pipeline:
                pipeline_correction = {"requested": requested_pipeline, "used": pipeline, "source": "classification_receipt"}
        if classification["complexity"] in {"C2", "C3"} and not {"documentation", "close"}.issubset(pipeline):
            raise ValueError("C2/C3 pipelines must include documentation and close gates")
        if classification["complexity"] in {"C2", "C3"} and pipeline.index("documentation") > pipeline.index("close"):
            raise ValueError("documentation must run before close")
        task_number, task_dir = allocate_task_directory(root, task_id)
        state_path = task_dir / "state.sqlite"
        thread_id = str(params.get("thread_id", "")).strip()
        principal = redact(params.get("principal") or thread_id or "local", 256)
        baseline = capture_project_manifest(select_project_root(params))
        baseline_preflight = dict(baseline)
        baseline_preflight.pop("captured_at", None)
        _json_text(baseline_preflight, label="baseline manifest", max_bytes=MAX_MANIFEST_BYTES)
        user_language = normalize_user_language(params.get("user_language"), params.get("objective", ""))
        plan_approval_policy = str(params.get("plan_approval") or "auto")
        if plan_approval_policy not in {"auto", "required"}:
            raise ValueError("plan_approval must be auto or required")
        follow_up = params.get("follow_up") if isinstance(params.get("follow_up"), dict) else None
        baseline_ref = store_manifest_snapshot(task_dir, baseline)
        task = {"schema": SCHEMA, "pipeline_contract_version": PIPELINE_CONTRACT_VERSION, "task_id": task_id, "task_number": task_number, "user_request": redact(params.get("user_request") or params.get("objective", ""), 4000), "objective": redact(params.get("objective", "")), "intent_clarification_required": bool(params.get("intent_clarification_required", False)), "intent_clarification_reason": redact(params.get("intent_clarification_reason", ""), 500) or None, "complexity": classification["complexity"], "base_pipeline": classification["base_pipeline"], "initial_pipeline": pipeline, "parallel_groups": parallel_groups, "requirements": receipt_requirements, "acceptance_criteria": [redact(item, 1000) for item in params.get("acceptance_criteria", [])][:100], "scope": [redact(item, 500) for item in params.get("scope", [])][:100], "allowed_paths": [redact(item, 500) for item in params.get("allowed_paths", [])][:100], "verification": [redact(item, 1000) for item in params.get("verification", [])][:100], "budget": redact(params.get("budget", ""), 500), "pause_conditions": [redact(item, 1000) for item in params.get("pause_conditions", [])][:100], "plan_approval": plan_approval_policy, "thread_id": redact(thread_id, 256), "principal": principal, "user_language": user_language, "internal_language": "en", "classification_id": classification_id, "project_root": baseline["project_root"], "initial_manifest_ref": baseline_ref, "tracker_policy": TRACKER_POLICY, "created_at": now()}
        if follow_up is not None:
            task["follow_up"] = sanitize_structured(follow_up)
        state = {"schema": SCHEMA, "pipeline_contract_version": PIPELINE_CONTRACT_VERSION, "task_id": task_id, "task_number": task_number, "status": "active", "principal": principal, "thread_id": redact(thread_id, 256), "user_language": user_language, "internal_language": "en", "complexity": classification["complexity"], "current_pipeline": pipeline, "parallel_groups": parallel_groups, "current_gates": active_gates({"current_pipeline": pipeline, "parallel_groups": parallel_groups, "completed_gates": [], "skipped_gates": []}), "completed_gates": [], "skipped_gates": [], "gates": {}, "attempts": [], "evidence": [], "locks": {}, "pipeline_changes": [], "adaptive_events": [], "recovery_events": [], "resume_events": [], "reassessment_receipts": [], "documentation_receipt": None, "manifest_receipts": [], "initial_manifest_ref": baseline_ref, "initial_manifest_digest": baseline["digest"], "manifest_snapshot_cleanup": {"status": "active", "at": now()}, "classification_receipt": classification_id, "handoff_created": False, "replan_count": 0, "replan_limit": int(params.get("replan_limit", 2)), "require_delegation": classification["complexity"] in {"C2", "C3"}, "require_handoff": classification["complexity"] in {"C2", "C3"}, "plan_approval": {"policy": plan_approval_policy, "status": "not_required" if plan_approval_policy == "auto" else "pending_plan", "history": []}, "coordinator": activation["coordinator"], "parent_project_operations": activation["parent_project_operations"], "worker_visibility": activation["worker_visibility"], "worker_return_route": activation["worker_return_route"], "revision": 0, "updated_at": now()}
        artifact_relative = str(task_dir.relative_to(root))
        db_create_task(root, task, state, artifact_relative)
        if thread_id and (
            thread_id != principal
            or not principal.startswith("orchestration-task-")
        ):
            bindings = _host_session_bindings(root)
            bindings["tasks"][task_id] = thread_id
            bindings["updated_at"] = now()
            db_put_global(root, "host_sessions", bindings)
        activations = _activation_records(root)
        activation_id = activation_key(params)
        current_activation = activations.get(activation_id)
        if not isinstance(current_activation, dict) or current_activation.get("schema") != SCHEMA:
            raise ValueError("orchestration activation disappeared during task initialization")
        current_activation["task_id"] = task_id
        current_activation["initialized_at"] = now()
        activations[activation_id] = current_activation
        _write_activation_records(root, activations)
        receipt["consumed_by"] = task_id
        receipt["consumed_at"] = now()
        db_put_classification(root, receipt)
        return {"created": True, "task_id": task_id, "task_number": task_number, "task_directory": task_dir.name, "state": state, "classification": classification, "pipeline_correction": pipeline_correction, "ledger_root": str(root)}


def status(params: dict[str, Any]) -> dict[str, Any]:
    root, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    task = load_task_definition(task_dir, state)
    receipt_id = "status-" + digest_text(json.dumps({
        "task_id": state["task_id"],
        "principal": state.get("principal", "local"),
        "revision": state["revision"],
    }, sort_keys=True))[:24]
    active = activation_record(root, {"thread_id": state.get("thread_id"), "principal": state.get("principal")}, state["task_id"])
    return {"task": task, "state": state, "active": bool(active), "status_receipt": receipt_id, "ledger_root": str(root)}


def _attempt(state: dict[str, Any], attempt_id: str) -> dict[str, Any]:
    attempt = next((item for item in state.get("attempts", []) if item.get("attempt_id") == attempt_id), None)
    if not attempt:
        raise ValueError("attempt_id does not belong to this task")
    return attempt


def _attempt_identity_aliases(attempt: dict[str, Any]) -> set[str]:
    """Return identities a native worker may use for its own active attempt.

    The coordinator principal/thread remains the authorization boundary for
    task mutations.  A worker report is the one scoped exception: after the
    host confirms a spawn, the worker may identify itself by the returned
    child/thread id as well as the canonical profile/task labels.  Keeping the
    aliases derived from one attempt prevents a host id from being used to
    publish for a different attempt.
    """
    spawn_request = attempt.get("spawn_request") or {}
    host_spawn = attempt.get("host_spawn") or {}
    values = (
        attempt.get("agent"),
        attempt.get("profile"),
        attempt.get("display_name"),
        spawn_request.get("task_name"),
        host_spawn.get("agent_id"),
        host_spawn.get("task_name"),
    )
    return {str(value).strip() for value in values if str(value or "").strip()}





def reconcile_report_bus(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        preflight_journal(task_dir)
        paths = report_bus_paths(task_dir)
        records: list[dict[str, Any]] = []
        submissions: dict[str, str] = {}
        by_attempt: dict[str, list[str]] = {}
        repaired: list[str] = []
        aggregate_bytes = 0
        source_index = _report_index(paths, state["task_id"])
        for metadata in source_index.get("reports", []):
            if not isinstance(metadata, dict):
                raise ValueError("SQLite report index contains an invalid entry")
            report_id = safe_id(str(metadata.get("report_id", "")))
            record, _ = read_immutable_json_artifact(
                task_dir,
                state["task_id"],
                f"reports/records/{report_id}.json",
                kinds={"worker_report", "report_record"},
            )
            report_id = safe_id(str(record.get("report_id", "")))
            attempt = _attempt(state, safe_id(str(record.get("attempt_id", ""))))
            sanitized = sanitize_report_payload(record.get("report"))
            raw_scoping = record.get("scoping")
            if raw_scoping is not None:
                if attempt.get("gate") != "scope" or attempt.get("profile") != "planner":
                    raise ValueError(
                        f"report record {report_id!r} failed reconciliation: "
                        "scoping is allowed only for planner scope reports"
                    )
                scoping = sanitize_scoping_payload(raw_scoping, persisted=True)
            else:
                scoping = None
            raw_planning = record.get("planning")
            if raw_planning is not None:
                if attempt.get("gate") != "plan" or attempt.get("profile") != "planner":
                    raise ValueError(
                        f"report record {report_id!r} failed reconciliation: "
                        "planning is allowed only for planner plan reports"
                    )
                planning = sanitize_planning_payload(raw_planning, persisted=True)
                digest_input: Any = {"report": sanitized, "planning": planning}
            else:
                planning = None
                digest_input: Any = ({"report": sanitized, "planning": planning} if "planning" in record else sanitized)
            if scoping is not None:
                digest_input = {
                    **(digest_input if isinstance(digest_input, dict) and "report" in digest_input else {"report": sanitized, "planning": planning}),
                    "scoping": scoping,
                }
            if "gate_result" in record:
                digest_input = {
                    "report": sanitized,
                    "planning": planning,
                    **({"scoping": scoping} if scoping is not None else {}),
                    "gate_result": sanitize_gate_result_payload(record.get("gate_result")),
                }
            if "closure" in record:
                digest_input = {
                    **(digest_input if isinstance(digest_input, dict) else {"report": sanitized, "planning": planning}),
                    "closure": sanitize_closure_payload(record.get("closure")),
                }
            digest = digest_text(json.dumps(digest_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            if record.get("schema") != REPORT_SCHEMA or record.get("task_id") != state["task_id"] or not report_id or record.get("gate") != attempt.get("gate") or record.get("content_digest") != digest:
                raise ValueError("SQLite report record failed reconciliation")
            records.append(_report_metadata(record))
            submissions[f"{record['attempt_id']}:{safe_id(str(record['submission_id']))}"] = report_id
            by_attempt.setdefault(record["attempt_id"], []).append(report_id)
            if len(records) > MAX_REPORTS_PER_TASK or len(by_attempt[record["attempt_id"]]) > MAX_REPORTS_PER_ATTEMPT:
                raise ValueError("authoritative reports exceed count quota")
            aggregate_bytes += len(json.dumps(record.get("report", {}), ensure_ascii=False, sort_keys=True).encode("utf-8"))
            if aggregate_bytes > MAX_REPORT_AGGREGATE_BYTES:
                raise ValueError("authoritative reports exceed aggregate byte quota")
            markdown_path = paths["markdown"] / f"{report_id}.md"
            generated = _report_markdown(record)
            if markdown_path.is_symlink():
                raise ValueError("generated report Markdown must not be a symlink")
            if not markdown_path.exists() or markdown_path.read_text(encoding="utf-8") != generated:
                if markdown_path.exists():
                    write_text_atomic(markdown_path, generated)
                else:
                    write_text_exclusive(markdown_path, generated)
                repaired.append(markdown_path.relative_to(paths["root"]).as_posix())
            receipt, receipt_repaired = _recover_report_receipt(paths, record, state, bool(attempt.get("invalidated")))
            if receipt_repaired:
                repaired.append((paths["receipts"] / f"{receipt['receipt_id']}.json").relative_to(paths["root"]).as_posix())
        _write_report_index(paths, state["task_id"], {"schema": REPORT_SCHEMA, "task_id": state["task_id"], "reports": records, "submissions": submissions, "updated_at": now()})
        for attempt in state.get("attempts", []):
            _, delegation_index = _delegation_report_index(paths, state["task_id"], attempt["attempt_id"])
            delegation_index["owned_report_ids"] = sorted(by_attempt.get(attempt["attempt_id"], []))
            delegation_index["updated_at"] = now()
            _write_delegation_report_index(paths, state["task_id"], attempt["attempt_id"], delegation_index)
        append_journal_best_effort(task_dir, "report_reconcile", f"{len(records)} record(s); {len(repaired)} repair(s)")
        return {"schema": REPORT_SCHEMA, "task_id": state["task_id"], "report_count": len(records), "repaired": repaired, "state": state}


def record_delegation(params: dict[str, Any]) -> dict[str, Any]:
    """Public delegating entrypoint for the delegation persistence service."""
    return _record_delegation_service(params)



def prepare_delegation(params: dict[str, Any]) -> dict[str, Any]:
    """Prepare one delegation without retaining a facade transaction for I/O.

    ``record_delegation`` owns the authoritative prepare transaction and, once
    it has committed, materializes a required briefing projection.  Keeping a
    facade ``state_lock`` around that call used to make the projection's
    fsync/rename run while an outer re-entrant SQLite transaction was still
    active.  Capture the status receipt first, then let the service complete
    its own commit-before-materialize boundary.
    """
    root = ledger_root(params)
    with state_lock(root):
        spec = params.get("delegation") if isinstance(params.get("delegation"), dict) else {}
        # Do not leak the wrapper object into routing.  Besides being
        # redundant, nested delegation data has historically triggered
        # ``unhashable type: dict`` in downstream adapters that inspect the
        # flattened request.  The spec is the authoritative override.
        merged = {key: value for key, value in params.items() if key != "delegation"}
        merged.update(spec)
        observed = status(merged)
        merged["expected_revision"] = observed["state"]["revision"]
        merged["status_receipt"] = observed["status_receipt"]
    # Do not move this call into the state-lock scope.  The delegated service
    # deliberately exits its SQLite transaction before materializing the
    # required briefing outbox job.
    result = record_delegation(merged)
    return {"status": observed, "delegation": result, "state": result["state"], "atomic": True}


def prepare_delegations(params: dict[str, Any]) -> dict[str, Any]:
    """Stage a batch atomically, then materialize each committed projection.

    The batch admission checks are one transaction so a malformed item cannot
    leave preceding attempts behind.  Each subsequent service call owns and
    commits its own attempt/outbox transaction before it performs filesystem
    work; the facade never keeps an enclosing transaction during a projection
    materialization.
    """
    specs = params.get("delegations")
    if not isinstance(specs, list) or not specs or len(specs) > 32:
        raise ValueError("prepare_delegations requires 1..32 delegation specs")
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, current_state = load_state(str(params["task_id"]), params)
        authorize(current_state, params)
        current_wave = active_gates(current_state)
        staged: list[tuple[dict[str, Any], dict[str, Any]]] = []
        status_receipt = "status-" + digest_text(json.dumps({
            "task_id": current_state["task_id"],
            "principal": current_state.get("principal", "local"),
            "revision": current_state["revision"],
        }, sort_keys=True))[:24]
        observed = {
            "task": load_task_definition(task_dir, current_state),
            "state": current_state,
            "active": bool(activation_record(
                root,
                {"thread_id": current_state.get("thread_id"), "principal": current_state.get("principal")},
                current_state["task_id"],
            )),
            "status_receipt": status_receipt,
            "ledger_root": str(root),
        }
        report_paths = report_bus_paths(task_dir)
        available_reports = {
            item["report_id"]
            for item in _report_index(report_paths, current_state["task_id"]).get("reports", [])
        }
        for index, raw in enumerate(specs):
            if not isinstance(raw, dict):
                return {"recorded": False, "reason": "invalid_batch_spec", "index": index, "prepared": [], "recoverable": True}
            gate = str(raw.get("gate") or (current_wave[0] if current_wave else "")).strip()
            if gate not in current_wave:
                return {"recorded": False, "reason": "batch_requires_one_gate", "current_gates": current_wave, "index": index, "prepared": [], "recoverable": True}
            context_report_ids = [safe_id(str(item)) for item in raw.get("context_report_ids", [])]
            if (
                len(context_report_ids) != len(set(context_report_ids))
                or not set(context_report_ids).issubset(available_reports)
            ):
                return {
                    "recorded": False,
                    "atomic": True,
                    "reason": "partial_failure",
                    "index": index,
                    "error": "context_report_ids must be unique reports from this task",
                    "prepared": [],
                    "recoverable": True,
                    "next_action": "retry the batch after correcting the returned error; no batch attempts were committed",
                }
            # The batch endpoint itself is the explicit parallel declaration;
            # keep its canonical value while preserving the public wrapper
            # shape accepted by the single-delegation facade.
            merged = {key: value for key, value in params.items() if key != "delegations"}
            merged.update({**raw, "parallel": True})
            merged["expected_revision"] = current_state["revision"]
            merged["status_receipt"] = status_receipt
            staged.append((observed, merged))

    prepared: list[dict[str, Any]] = []
    try:
        for observed, merged in staged:
            # ``record_delegation`` commits its intent before it calls the
            # filesystem materializer.  In particular, do not call the
            # single facade here: it would create an unnecessary status read
            # and makes the transaction boundary less explicit.
            result = record_delegation(merged)
            prepared.append({"status": observed, "delegation": result, "state": result["state"], "atomic": True})
    except Exception as exc:
        # A required projection failure is persisted by the service as a
        # failed job/attempt.  Never surface a partial spawn list: callers
        # must recover from the durable ledger state instead.
        return {
            "recorded": False,
            "atomic": True,
            "reason": "partial_failure",
            "index": len(prepared),
            "error": redact(str(exc), 1000),
            "prepared": [],
            "recoverable": True,
            "next_action": "retry the batch after correcting the returned error; inspect durable delegation attempts before dispatching",
        }
    return {
        "recorded": True,
        "atomic": True,
        "gates": sorted({item["state"]["attempts"][-1]["gate"] for item in prepared}),
        "current_gates": current_wave,
        "attempts": [item["delegation"]["attempt_id"] for item in prepared],
        "spawn_requests": [item["delegation"]["spawn_request"] for item in prepared],
        "delegations": prepared,
        "state": prepared[-1]["state"],
    }


def confirm_host_spawn(params: dict[str, Any]) -> dict[str, Any]:
    """Bind a ledger attempt to a native Codex worker dispatch after confirmation."""
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        requested_revision = params.get("expected_revision")
        revision_correction = (
            {"requested": requested_revision, "used": state["revision"]}
            if requested_revision is not None and requested_revision != state["revision"] else None
        )
        attempt_id = safe_id(str(params.get("attempt_id", "")))
        attempt = _attempt(state, attempt_id)
        host_agent_id = redact(str(params.get("host_agent_id", "")).strip(), 256)
        host_task_name = redact(str(params.get("host_task_name", "")).strip(), 128)
        expected_host_tool = str((attempt.get("spawn_request") or {}).get("host_tool", "spawn_agent"))
        host_tool = redact(str(params.get("host_tool", "")).strip(), 64)
        if not host_tool and expected_host_tool == "spawn_agent":
            host_tool = "spawn_agent"
        if not host_agent_id or not host_task_name or not host_tool:
            return {
                "confirmed": False,
                "attempt_id": attempt_id,
                "next_action": "retry confirm_host_spawn with the native host_tool, host_agent_id, and host_task_name",
                "recoverable": True,
                "revision_correction": revision_correction,
                "state": state,
            }
        if host_tool != expected_host_tool:
            return {
                "confirmed": False,
                "attempt_id": attempt_id,
                "reason": "host_tool_mismatch",
                "next_action": f"retry confirm_host_spawn with host_tool={expected_host_tool}",
                "recoverable": True,
                "revision_correction": revision_correction,
                "state": state,
            }
        expected_task_name = str((attempt.get("spawn_request") or {}).get("task_name", ""))
        for existing in state.get("attempts", []):
            if existing.get("attempt_id") == attempt_id:
                continue
            existing_spawn = existing.get("host_spawn") or {}
            if (
                existing_spawn.get("tool") == host_tool
                and existing_spawn.get("agent_id") == host_agent_id
            ):
                return {
                    "confirmed": False,
                    "attempt_id": attempt_id,
                    "reason": "host_agent_id_reused",
                    "detail": (
                        f"host_agent_id {host_agent_id!r} is already bound to "
                        f"attempt {existing.get('attempt_id')!r}"
                    ),
                    "next_action": (
                        "retry confirm_host_spawn with the new native child id and "
                        "the exact task_name returned for this attempt"
                    ),
                    "recoverable": True,
                    "state": state,
                }
        task_name_correction = (
            {"requested": host_task_name, "used": expected_task_name}
            if host_task_name != expected_task_name else None
        )
        spawn_request = attempt.get("spawn_request") or {}
        expected_model = str(
            attempt.get("expected_model")
            or spawn_request.get("expected_model")
            or attempt.get("selected_model")
            or spawn_request.get("model")
            or ""
        ).strip()
        model_resolution = str(
            attempt.get("model_resolution")
            or spawn_request.get("model_resolution")
            or ("explicit" if spawn_request.get("model") else "policy")
        ).strip()
        # An omitted native model is intentional for configured-default
        # dispatch. Verify the actual host model against expected_model,
        # while retaining requested_model only for explicit native overrides.
        requested_model = str(spawn_request.get("model") or "").strip()
        host_model = redact(str(params.get("host_model", "")).strip(), 128) or None
        if expected_model and not host_model:
            return {
                "confirmed": False,
                "attempt_id": attempt_id,
                "reason": "host_model_required",
                "next_action": "retry confirm_host_spawn with the actual host_model returned by the native host",
                "required_fields": ["host_model"],
                "recoverable": True,
                "task_name_correction": task_name_correction,
                "revision_correction": revision_correction,
                "state": state,
            }
        host_spawn = {
            "tool": host_tool,
            "agent_id": host_agent_id,
            "task_name": expected_task_name,
            "model": host_model,
            "reasoning_effort": redact(str(params.get("host_reasoning_effort", "")).strip(), 64) or None,
            "requested_model": requested_model or None,
            "expected_model": expected_model or None,
            "model_resolution": model_resolution or None,
            "confirmed_at": now(),
        }
        model_verification = "not_requested"
        if expected_model:
            model_verification = "verified" if host_model == expected_model else "mismatch"
        host_spawn["model_verification"] = model_verification
        if attempt.get("status") == "running":
            existing = attempt.get("host_spawn") or {}
            if all(existing.get(key) == host_spawn.get(key) for key in ("tool", "agent_id", "task_name", "model", "reasoning_effort", "requested_model", "expected_model", "model_resolution", "model_verification")):
                return {"attempt_id": attempt_id, "idempotent": True, "revision_correction": revision_correction, "state": state}
            raise ValueError("running attempt already has a different host spawn binding")
        if attempt.get("status") != AWAITING_HOST_SPAWN or attempt.get("invalidated"):
            raise ValueError("only an active attempt awaiting host spawn can be confirmed")
        if model_verification == "mismatch":
            mismatch_reason = f"host_model_mismatch: expected {expected_model}, actual {host_model}"
            attempt["host_spawn"] = host_spawn
            attempt["model_verification"] = model_verification
            attempt["status"] = "failed"
            attempt["finalized_at"] = host_spawn["confirmed_at"]
            attempt["finalization_reason"] = mismatch_reason
            package = _delegation_package(task_dir, state["task_id"], attempt_id)
            package["spawn_status"] = "confirmed_model_mismatch"
            package["dispatch_correlation"] = "coordinator_recorded_host_spawn"
            package["host_spawn"] = host_spawn
            package["model_verification"] = model_verification
            _write_delegation_package(task_dir, state["task_id"], attempt_id, package)
            db_put_worker_session(root, {
                "task_id": state["task_id"], "attempt_id": attempt_id,
                "host_agent_id": host_agent_id, "host_task_name": expected_task_name, "host_tool": host_tool,
                "status": "terminated_unavailable", "resumable": False,
                "started_at": host_spawn["confirmed_at"], "terminated_at": host_spawn["confirmed_at"],
            })
            save_state(task_dir, task_dir / "state.sqlite", state, "host_spawn_model_mismatch", f"{attempt_id}: {mismatch_reason}")
            return {
                "confirmed": False,
                "failed": True,
                "attempt_id": attempt_id,
                "reason": "host_model_mismatch",
                "detail": mismatch_reason,
                "expected_model": expected_model,
                "actual_model": host_model,
                "host_spawn": host_spawn,
                "task_name_correction": task_name_correction,
                "revision_correction": revision_correction,
                "state": state,
            }
        attempt["host_spawn"] = host_spawn
        attempt["model_verification"] = model_verification
        attempt["dispatch_correlation"] = "coordinator_recorded_host_spawn"
        attempt["status"] = "running"
        attempt["started_at"] = host_spawn["confirmed_at"]
        package = _delegation_package(task_dir, state["task_id"], attempt_id)
        package["spawn_status"] = "confirmed"
        package["dispatch_correlation"] = "coordinator_recorded_host_spawn"
        package["host_spawn"] = host_spawn
        _write_delegation_package(task_dir, state["task_id"], attempt_id, package)
        db_put_worker_session(root, {
            "task_id": state["task_id"], "attempt_id": attempt_id,
            "host_agent_id": host_agent_id, "host_task_name": expected_task_name, "host_tool": host_tool,
            "status": "running", "resumable": True, "started_at": host_spawn["confirmed_at"],
        })
        save_state(task_dir, task_dir / "state.sqlite", state, "host_spawn", f"{attempt_id}: {expected_task_name}")
        return {"confirmed": True, "attempt_id": attempt_id, "idempotent": False, "host_spawn": host_spawn, "task_name_correction": task_name_correction, "revision_correction": revision_correction, "state": state}


def _attempt_evidence(state: dict[str, Any], attempt_id: str) -> list[dict[str, Any]]:
    return [
        item for item in state.get("evidence", [])
        if item.get("attempt_id") == attempt_id and not item.get("invalidated")
    ]


def _validated_evidence_records(task_dir: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify the immutable evidence records before a gate consumes them."""
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for state_record in state.get("evidence", []):
        evidence_id = safe_id(str(state_record.get("evidence_id") or ""))
        if evidence_id in seen:
            raise ValueError("task evidence index contains a duplicate evidence_id")
        seen.add(evidence_id)
        record, _ = read_immutable_json_artifact(
            task_dir,
            state["task_id"],
            f"evidence/{evidence_id}.json",
            kinds={"evidence"},
        )
        indexed = dict(state_record)
        invalidated = bool(indexed.pop("invalidated", False))
        if indexed != record:
            raise ValueError(f"SQLite evidence record failed reconciliation: {evidence_id}")
        validated.append({**record, **({"invalidated": True} if invalidated else {})})
    return validated


def finalize_attempt(params: dict[str, Any]) -> dict[str, Any]:
    """Explicitly close a host-completed attempt when it cannot publish a report."""
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        requested_revision = params.get("expected_revision")
        revision_correction = (
            {"requested": requested_revision, "used": state["revision"]}
            if requested_revision is not None and requested_revision != state["revision"] else None
        )
        attempt_id = safe_id(str(params.get("attempt_id", "")))
        attempt = _attempt(state, attempt_id)
        status = str(params.get("status", "")).strip().lower()
        if status not in TERMINAL_ATTEMPT_STATUSES:
            raise ValueError("status must be passed, failed, blocked, cancelled, or superseded")
        if attempt.get("status") in TERMINAL_ATTEMPT_STATUSES:
            if attempt.get("status") == status:
                return {"attempt_id": attempt_id, "status": status, "idempotent": True, "revision_correction": revision_correction, "state": state}
            raise ValueError("cannot change the status of a terminal attempt")
        if attempt.get("status") not in {"running", AWAITING_HOST_SPAWN}:
            raise ValueError("cannot finalize a non-running attempt")
        if attempt.get("status") == AWAITING_HOST_SPAWN and status == "passed":
            return {
                "recorded": False,
                "attempt_id": attempt_id,
                "reason": "host_spawn_confirmation_required",
                "next_action": "confirm_host_spawn",
                "recoverable": True,
                "revision_correction": revision_correction,
                "state": state,
            }
        if attempt.get("invalidated") and status != "superseded":
            raise ValueError("an invalidated running attempt may only be finalized as superseded")
        reason = str(params.get("reason", "")).strip()
        if status != "passed" and not reason:
            return {
                "recorded": False,
                "attempt_id": attempt_id,
                "status": status,
                "reason": "finalization_reason_required",
                "next_action": "retry_finalize_attempt_with_reason",
                "required_fields": ["reason"],
                "recoverable": True,
                "revision_correction": revision_correction,
                "state": state,
            }
        attempt["status"] = status
        attempt["finalized_at"] = now()
        if reason:
            attempt["finalization_reason"] = redact(reason, 2000)
        save_state(task_dir, task_dir / "state.sqlite", state, "attempt", f"{attempt_id}: {status}")
        return {"attempt_id": attempt_id, "status": status, "idempotent": False, "revision_correction": revision_correction, "state": state}


def _attempts_missing_result_validation(task_dir: Path, attempts: list[dict[str, Any]]) -> list[str]:
    """Return facade attempts that have no server-validated result receipt."""
    task = load_task_definition(task_dir)
    missing: list[str] = []
    for attempt in attempts:
        if not attempt.get("facade_managed"):
            continue
        valid = False
        for report_id in attempt.get("report_ids") or []:
            try:
                record, _ = read_immutable_json_artifact(
                    task_dir,
                    str(task["task_id"]),
                    f"reports/records/{safe_id(str(report_id))}.json",
                    kinds={"worker_report", "report_record"},
                )
            except ValueError:
                continue
            validation = record.get("result_validation")
            gate = str(attempt.get("gate") or "")
            result_contract = {
                "gate_acceptance": list(attempt.get("acceptance_criteria") or []),
                "gate_verification": list(attempt.get("verification") or []),
                "task_acceptance": list(task.get("acceptance_criteria") or []) if gate == "close" else [],
                "task_verification": list(task.get("verification") or []) if gate == "close" else [],
            }
            expected_contract_digest = digest_text(
                json.dumps(result_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
            if (
                isinstance(validation, dict)
                and validation.get("schema") == RESULT_VALIDATION_SCHEMA
                and validation.get("status") == "passed"
                and validation.get("attempt_id") == attempt.get("attempt_id")
                and validation.get("gate") == gate
                and validation.get("contract_digest") == expected_contract_digest
                and (validation.get("artifacts") or {}).get("baseline_digest") == attempt.get("result_baseline_digest")
            ):
                valid = True
                break
        if not valid:
            missing.append(str(attempt.get("attempt_id")))
    return missing


def complete_attempt(params: dict[str, Any]) -> dict[str, Any]:
    """Fast path for host confirmation, optional report publication, and terminalization."""
    root = ledger_root(params)
    with state_lock(root):
        task_id = str(params["task_id"])
        attempt_id = safe_id(str(params.get("attempt_id", "")))
        _, task_dir, initial_state = load_state(task_id, params)
        authorize(initial_state, params)
        status_value = str(params.get("status", "passed")).strip().lower()
        try:
            confirmed = None
            if params.get("host_agent_id") or params.get("host_task_name"):
                confirmed = confirm_host_spawn({**params, "attempt_id": attempt_id})
                if confirmed.get("confirmed") is False:
                    return {
                        "atomic": False,
                        "attempt_id": attempt_id,
                        "confirmed": confirmed,
                        "report": None,
                        "finalized": None,
                        "state": confirmed["state"],
                    }
            report_result = None
            if isinstance(params.get("report"), dict):
                report_result = record_report({**params, "attempt_id": attempt_id, "report": params["report"]})
            state = (report_result or confirmed or status({"task_id": task_id, **params}))["state"]
            final_params = {**params, "attempt_id": attempt_id, "expected_revision": state["revision"], "status": status_value}
            if status_value != "passed" and not str(final_params.get("reason", "")).strip():
                final_params["reason"] = "host adapter reported terminal non-success"
            finalized = finalize_attempt(final_params)
            return {"atomic": True, "attempt_id": attempt_id, "confirmed": confirmed, "report": report_result, "finalized": finalized, "state": finalized["state"]}
        except ValueError as exc:
            _, current_task_dir, current_state = load_state(task_id, params)
            attempt = _attempt(current_state, attempt_id)
            recovery = _record_commit_gate_recovery(
                current_task_dir,
                current_state,
                {**params, "gate": attempt.get("gate")},
                "complete_attempt",
                str(exc),
            )
            return {
                "atomic": False,
                "recorded": False,
                "attempt_id": attempt_id,
                "error": redact(str(exc), 1000),
                **recovery,
            }


def _record_evidence_locked(task_dir: Path, state: dict[str, Any], params: dict[str, Any], verified: bool = False, execution: dict[str, Any] | None = None) -> dict[str, Any]:
    gate = str(params["gate"])
    if gate not in active_gates(state):
        raise ValueError(f"cannot add evidence for non-active gate '{gate}'")
    attempt_id = params.get("attempt_id")
    if state.get("require_delegation") and not attempt_id:
        raise ValueError("C2/C3 evidence must be linked to a delegation attempt")
    if attempt_id and not any(item["attempt_id"] == attempt_id and item["gate"] == gate and item["status"] in {"running", "passed"} for item in state["attempts"]):
        raise ValueError("evidence attempt_id does not belong to a running or passed attempt for the current gate")
    # A technical-writer report is the documentation decision evidence even
    # when the coordinator labels the accompanying command/check as generic
    # verification.  Normalize that safe, gate-bound case so a read-only
    # documentation audit cannot fail at the gate transition merely because
    # the evidence kind was omitted by the host model.
    evidence_kind = str(params.get("kind", "")).strip()
    if gate == "documentation" and attempt_id:
        attempt = next((item for item in state.get("attempts", []) if item.get("attempt_id") == attempt_id), None)
        if attempt and attempt.get("agent") == "technical_writer" and evidence_kind in DOCUMENTATION_EVIDENCE_KINDS:
            params = {**params, "kind": "documentation"}
    report_receipt = None
    report_paths = None
    report_record = None
    if state.get("require_delegation"):
        receipt_id = safe_id(str(params.get("report_receipt", "")))
        report_paths = report_bus_paths(task_dir)
        report_receipt, report_record = _load_report_receipt(task_dir, state, receipt_id)
        if report_receipt.get("schema") != REPORT_SCHEMA or report_receipt.get("task_id") != state["task_id"] or report_receipt.get("gate") != gate or report_receipt.get("attempt_id") != attempt_id or report_receipt.get("invalidated") or report_receipt.get("consumed_at") or report_receipt.get("consumed_by_evidence_id"):
            raise ValueError("report receipt is invalid, consumed, or does not belong to this attempt")
    summary = str(params.get("summary", "")).strip()
    if not summary:
        raise ValueError("evidence summary is required")
    evidence_id = f"evidence-{len(state['evidence']) + 1:04d}"
    kind = redact(params.get("kind", "report"), 64)
    exit_code = params.get("exit_code")
    if kind == "command" and exit_code is None:
        raise ValueError("command evidence requires exit_code")
    decision = str(params.get("decision", "")).strip()
    justification = str(params.get("justification", "")).strip()
    if kind == "documentation":
        if gate != "documentation" or decision not in {"updated", "not_applicable"}:
            raise ValueError("documentation evidence requires decision updated or not_applicable at the documentation gate")
        if decision == "not_applicable" and not justification:
            raise ValueError("not_applicable documentation evidence requires justification")
    execution = execution or {}
    evidence = {
        "evidence_id": evidence_id,
        "task_id": state["task_id"],
        "gate": gate,
        "attempt_id": attempt_id,
        "report_id": report_receipt.get("report_id") if report_receipt else None,
        "report_receipt": report_receipt.get("receipt_id") if report_receipt else None,
        "kind": kind,
        "summary": redact(summary),
        "digest": redact(params.get("digest", ""), 256) or digest_text(json.dumps(execution, sort_keys=True) if execution else params.get("command", summary)),
        "command": redact(params.get("command", ""), 1000),
        "argv": [redact(item, 500) for item in execution.get("argv", [])],
        "cwd": execution.get("cwd"),
        "stdout": redact(execution.get("stdout", ""), 4000),
        "stderr": redact(execution.get("stderr", ""), 4000),
        "exit_code": int(exit_code) if exit_code is not None else None,
        "verified_execution": bool(verified),
        "decision": decision or None,
        "justification": redact(justification, 2000) or None,
        "paths": [redact(path, 500) for path in params.get("paths", [])],
        "created_at": now(),
    }
    if report_receipt is not None and report_paths is not None and report_record is not None:
        report_receipt["consumed_at"] = now()
        report_receipt["consumed_by_evidence_id"] = evidence_id
        receipt_root = _task_document_root(task_dir, state["task_id"])
        db_put_task_document(
            receipt_root,
            state["task_id"],
            f"receipt_state:{report_receipt['receipt_id']}",
            report_receipt,
        )
        _recover_report_receipt(report_paths, report_record, state)
    store_immutable_artifact(
        task_dir,
        state["task_id"],
        kind="evidence",
        title=f"evidence/{evidence_id}.json",
        mime_type="application/json",
        content=json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        export_path=f"evidence/{evidence_id}.json",
    )
    write_json(task_dir / "evidence" / f"{evidence_id}.json", evidence)
    state["evidence"].append(evidence)
    if kind == "documentation":
        state["documentation_receipt"] = {"evidence_id": evidence_id, "attempt_id": attempt_id, "decision": decision, "justification": evidence["justification"]}
    for attempt in state["attempts"]:
        if attempt["attempt_id"] == attempt_id:
            attempt["evidence_ids"].append(evidence_id)
            if evidence.get("report_id") and evidence["report_id"] not in attempt.setdefault("report_ids", []):
                attempt["report_ids"].append(evidence["report_id"])
    save_state(task_dir, task_dir / "state.sqlite", state, "evidence", f"{gate}: {evidence_id}")
    return {"evidence_id": evidence_id, "state": state, "evidence": evidence}


def record_evidence(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        wave = active_gates(state)
        requested_gate = str(params.get("gate") or (wave[0] if wave else ""))
        resolved = {**params, "gate": requested_gate if requested_gate in wave else (wave[0] if wave else requested_gate)}
        # A report receipt is itself an unambiguous attempt binding.  Prefer
        # that durable identity over forcing the coordinator to choose among
        # several parallel attempts when the attempt_id was omitted.
        if state.get("require_delegation") and not resolved.get("attempt_id") and resolved.get("report_receipt"):
            receipt_id = safe_id(str(resolved["report_receipt"]))
            try:
                receipt, _ = _load_report_receipt(task_dir, state, receipt_id)
            except ValueError:
                receipt = None
            if receipt and receipt.get("task_id") == state["task_id"] and receipt.get("gate") == resolved["gate"] and not receipt.get("consumed_at") and not receipt.get("invalidated"):
                resolved["attempt_id"] = receipt.get("attempt_id")
        if state.get("require_delegation") and not resolved.get("attempt_id"):
            eligible = [
                item for item in state.get("attempts", [])
                if item.get("gate") == resolved["gate"]
                and item.get("status") in {"running", "passed"}
                and not item.get("invalidated")
                and not _attempt_evidence(state, item["attempt_id"])
            ]
            if len(eligible) != 1:
                return {
                    "recorded": False,
                    "reason": "delegation_attempt_required",
                    "candidate_attempt_ids": [item["attempt_id"] for item in eligible],
                    "next_action": ("record_delegation" if not eligible else "select_attempt_id"),
                    "state": state,
                }
            resolved["attempt_id"] = eligible[0]["attempt_id"]
        if state.get("require_delegation") and not resolved.get("report_receipt"):
            receipts = _available_report_receipts(
                task_dir,
                state,
                attempt_id=resolved.get("attempt_id"),
                gate=resolved["gate"],
            )
            if len(receipts) != 1:
                return {
                    "recorded": False,
                    "reason": "report_receipt_required",
                    "attempt_id": resolved.get("attempt_id"),
                    "candidate_report_receipts": [item["receipt_id"] for item in receipts],
                    "next_action": ("record_report" if not receipts else "select_report_receipt"),
                    "state": state,
                }
            resolved["report_receipt"] = receipts[0]["receipt_id"]
        receipt_correction = None
        if state.get("require_delegation") and resolved.get("report_receipt"):
            resolved["report_receipt"] = safe_id(str(resolved["report_receipt"]))
        result = _record_evidence_locked(task_dir, state, resolved)
        result["inferred"] = {
            "gate": params.get("gate") != resolved.get("gate"),
            "attempt_id": not bool(params.get("attempt_id")),
            "report_receipt": not bool(params.get("report_receipt")),
        }
        result["receipt_correction"] = receipt_correction
        return result


def execute_verification(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        requested_revision = params.get("expected_revision")
        revision_correction = (
            {"requested": requested_revision, "used": state["revision"]}
            if requested_revision is not None and requested_revision != state["revision"] else None
        )
        wave = active_gates(state)
        requested_gate = str(params.get("gate") or (wave[0] if wave else ""))
        resolved = {**params, "gate": requested_gate if requested_gate in wave else (wave[0] if wave else requested_gate)}
        if state.get("require_delegation") and not resolved.get("attempt_id"):
            eligible = [
                item for item in state.get("attempts", [])
                if item.get("gate") == resolved["gate"]
                and item.get("status") in {"running", "passed"}
                and not item.get("invalidated")
                and not _attempt_evidence(state, item["attempt_id"])
            ]
            if len(eligible) == 1:
                resolved["attempt_id"] = eligible[0]["attempt_id"]
            else:
                return {
                    "recorded": False,
                    "reason": "delegation_attempt_required",
                    "candidate_attempt_ids": [item["attempt_id"] for item in eligible],
                    "next_action": ("record_delegation" if not eligible else "select_attempt_id"),
                    "recoverable": True,
                    "revision_correction": revision_correction,
                    "state": state,
                }
        if state.get("require_delegation") and not resolved.get("report_receipt"):
            receipts = _available_report_receipts(
                task_dir,
                state,
                attempt_id=resolved.get("attempt_id"),
                gate=resolved["gate"],
            )
            if len(receipts) == 1:
                resolved["report_receipt"] = receipts[0]["receipt_id"]
            else:
                return {
                    "recorded": False,
                    "reason": "report_receipt_required",
                    "attempt_id": resolved.get("attempt_id"),
                    "candidate_report_receipts": [item["receipt_id"] for item in receipts],
                    "next_action": ("record_report" if not receipts else "select_report_receipt"),
                    "recoverable": True,
                    "revision_correction": revision_correction,
                    "state": state,
                }
        receipt_correction = None
        if state.get("require_delegation") and resolved.get("report_receipt"):
            resolved["report_receipt"] = safe_id(str(resolved["report_receipt"]))
        forbidden = {"argv", "command", "cwd", "env", "environment", "executable", "shell", "args"} & set(params)
        if forbidden:
            raise ValueError("verification commands accept only a fixed verification_id; caller-selected command, cwd, or environment is forbidden")
        verification_id = str(params.get("verification_id", ""))
        selected = VERIFICATION_COMMANDS.get(verification_id)
        if not selected:
            raise ValueError("unknown verification_id")
        argv = selected["argv"]
        timeout = int(params.get("timeout_seconds", 60))
        if timeout < 1 or timeout > 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        task = load_task_definition(task_dir, state)
        base = select_project_root(params)
        if task.get("project_root") != str(base):
            raise ValueError("task project_root does not match the current tool call")
        relative_cwd = Path(selected["cwd"])
        cwd = _contained_path(base, (base / relative_cwd).absolute(), "verification cwd")
        if not cwd.is_dir():
            raise ValueError("verification cwd must be an existing directory")
        try:
            completed = subprocess.run(argv, cwd=cwd, env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}, text=True, capture_output=True, timeout=timeout, check=False)
            exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code, stdout, stderr = 124, exc.stdout or "", exc.stderr or "verification timed out"
        evidence_params = {**resolved, "kind": "command", "command": verification_id, "exit_code": exit_code}
        result = _record_evidence_locked(task_dir, state, evidence_params, verified=True, execution={"argv": argv, "cwd": str(cwd.relative_to(base)), "stdout": stdout, "stderr": stderr, "exit_code": exit_code})
        result["execution"] = {"exit_code": exit_code, "stdout": redact(stdout, 4000), "stderr": redact(stderr, 4000)}
        result["revision_correction"] = revision_correction
        result["receipt_correction"] = receipt_correction
        return result


def record_gate(params: dict[str, Any]) -> dict[str, Any]:
    """Public delegating entrypoint for the independently testable gate policy."""
    from cortex_runtime.gate_transitions import record_gate as _record_gate

    return _record_gate(params)


def _record_commit_gate_recovery(
    task_dir: Path,
    state: dict[str, Any],
    params: dict[str, Any],
    mode: str,
    error: str,
) -> dict[str, Any]:
    """Persist bounded fast-path failures so a bad adapter cannot hang a task."""
    gate = str(params.get("gate") or primary_gate(state) or "unknown")
    reason = redact(error, 1000)
    failure_key = digest_text(json.dumps({"gate": gate, "mode": mode, "error": reason}, sort_keys=True))
    events = state.setdefault("recovery_events", [])
    previous = [
        item for item in events
        if item.get("kind") == "commit_gate_failure" and item.get("failure_key") == failure_key
    ]
    gate_failures = [
        item for item in events
        if item.get("kind") == "commit_gate_failure"
        and item.get("gate") == gate
        and item.get("mode") == mode
    ]
    count = len(gate_failures) + 1
    same_error_count = len(previous) + 1
    terminal = count >= MAX_GATE_RECOVERY_FAILURES
    event = {
        "kind": "commit_gate_failure",
        "failure_key": failure_key,
        "gate": gate,
        "mode": mode,
        "count": count,
        "same_error_count": same_error_count,
        "error": reason,
        "at": now(),
        "terminal": terminal,
    }
    events.append(event)
    if len(events) > MAX_GATE_RECOVERY_EVENTS:
        del events[:-MAX_GATE_RECOVERY_EVENTS]
    if terminal:
        state["status"] = "blocked"
        state["blocked_gate"] = gate
        state["blocked_reason"] = f"commit_gate recovery budget exhausted: {reason}"
        next_action = "create_handoff_and_resume_after_gate_repair"
    elif "report receipt" in reason.lower() or "receipt" in reason.lower():
        next_action = "repair_report_receipt_then_retry_commit_gate_once"
    else:
        next_action = "inspect_commit_gate_error_then_retry_once"
    save_state(task_dir, task_dir / "state.sqlite", state, "gate_recovery", f"{gate}: {reason}")
    return {
        "failure": event,
        "recoverable": not terminal,
        "terminal": terminal,
        "retry_count": count,
        "retry_limit": MAX_GATE_RECOVERY_FAILURES,
        "next_action": next_action,
        "state": state,
    }


def commit_gate(params: dict[str, Any]) -> dict[str, Any]:
    """Fast path for server verification/evidence and the gate transition.

    Validation failures are returned as bounded recovery data instead of
    escaping as repeated MCP errors.  After three failures for the same
    gate/mode the task is durably blocked, giving the coordinator a terminal
    handoff path.
    """
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        mode = str(params.get("mode") or "verification").strip().lower()
        requested_gate = canonical_pipeline_gate(params.get("gate") or primary_gate(state) or "")
        # Host adapters may retry a completed composite call after a timeout.
        # Treat that exact gate transition as idempotent instead of trying to
        # consume a one-use report receipt a second time and opening a false
        # recovery event (the common source of "commit gate keeps failing").
        if requested_gate in set(state.get("completed_gates", [])) | set(state.get("skipped_gates", [])):
            existing_gate = state.get("gates", {}).get(requested_gate, {})
            return {
                "recorded": True,
                "atomic": True,
                "idempotent": True,
                "mode": mode,
                "outcome": existing_gate.get("outcome", "passed"),
                "evidence": None,
                "gate": existing_gate,
                "state": state,
            }
        evidence = None
        outcome = str(params.get("outcome") or "failed")
        try:
            if mode == "verification":
                evidence = execute_verification({**params, "gate": requested_gate})
                state = evidence["state"]
                if evidence.get("recorded") is False:
                    raise ValueError(str(evidence.get("reason") or "verification evidence was not recorded"))
                exit_code = evidence.get("execution", {}).get("exit_code")
                outcome = str(params.get("outcome") or ("passed" if exit_code == 0 else "failed"))
            elif mode == "documentation":
                evidence_params = {**params, "gate": requested_gate or "documentation", "kind": "documentation"}
                evidence = record_evidence(evidence_params)
                state = evidence["state"]
                if evidence.get("recorded") is False:
                    raise ValueError(str(evidence.get("reason") or "documentation evidence was not recorded"))
                outcome = str(params.get("outcome") or "passed")
            else:
                raise ValueError("commit_gate mode must be verification or documentation")
            gate_result = record_gate({
                **params,
                "gate": requested_gate or primary_gate(state),
                "expected_revision": state["revision"],
                "outcome": outcome,
            })
        except ValueError as exc:
            recovery = _record_commit_gate_recovery(task_dir, state, params, mode, str(exc))
            return {
                "recorded": False,
                "atomic": True,
                "mode": mode,
                "outcome": outcome,
                "error": redact(str(exc), 1000),
                "evidence": evidence,
                **recovery,
            }
        return {"recorded": True, "atomic": True, "mode": mode, "outcome": outcome, "evidence": evidence, "gate": gate_result, "state": gate_result["state"]}


def close_audit(params: dict[str, Any]) -> dict[str, Any]:
    """Reconcile and summarize the report bus in one close-time operation."""
    root = ledger_root(params)
    with state_lock(root):
        reports = list_task_reports(params)
        reconciled = reconcile_report_bus(params)
        return {"atomic": True, "reports": reports, "reconciled": reconciled, "report_count": reconciled["report_count"], "state": reconciled["state"]}


def resume_task(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        if state["status"] != "blocked":
            raise ValueError("only blocked tasks can be resumed")
        state["status"] = "active"
        state.setdefault("resume_events", []).append({"reason": redact(params.get("reason", ""), 2000), "at": now()})
        save_state(task_dir, task_dir / "state.sqlite", state, "resume", redact(params.get("reason", "task resumed")))
        return {"state": state}


def update_pipeline(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        was_completed = state["status"] == "completed"
        if was_completed and not params.get("allow_rework", False):
            raise ValueError("completed task cannot change pipeline without allow_rework=true")
        requested, operations = params.get("pipeline"), params.get("operations", [])
        if requested is None and not operations:
            raise ValueError("provide pipeline or operations")
        requested_gates = normalize_pipeline(requested) if requested is not None else list(state["current_pipeline"])
        change = apply_pipeline_operations(state, pipeline=requested, operations=operations, allow_rework=bool(params.get("allow_rework", False)), parallel_groups=params.get("parallel_groups"))
        append_pipeline_change(state, change, str(params.get("reason", "pipeline updated")), params.get("signals", []))
        invalidate_reworked_report_receipts(task_dir, state)
        sync_current_wave(state)
        if primary_gate(state) is not None and was_completed:
            task = load_task_definition(task_dir, state)
            establish_task_manifest_baseline(task_dir, state, Path(task["project_root"]))
            state["status"] = "active"
        save_state(task_dir, task_dir / "state.sqlite", state, "pipeline", str(params.get("reason", "pipeline updated")))
        return {"state": state, "change": change}


def reassess_pipeline(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        if state["status"] == "completed":
            raise ValueError("completed task cannot be reassessed")
        task = load_task_definition(task_dir, state)
        signals = [redact(item, 500) for item in params.get("signals", [])][:50]
        decision = str(params.get("decision", "")).strip()
        reason = redact(params.get("reason", ""), 2000)
        if state.get("require_delegation") and decision not in {"unchanged", "updated", "stop"}:
            raise ValueError("C2/C3 reassessment requires decision unchanged, updated, or stop")
        if state.get("require_delegation") and not reason:
            raise ValueError("C2/C3 reassessment requires an explicit reason")
        intent = str(params.get("intent", "add_specialist"))
        if intent not in {"add_specialist", "resequence", "rework_gate", "stop"}:
            raise ValueError("intent must be add_specialist, resequence, rework_gate, or stop")
        if int(state.get("replan_count", 0)) >= int(state.get("replan_limit", 2)):
            raise ValueError("replan limit exhausted")
        explicit_pipeline = params.get("pipeline")
        proposal_params = {"complexity": task["complexity"], "requirements": task.get("requirements", []) + signals}
        explicit_groups = params.get("parallel_groups")
        if explicit_pipeline is not None:
            proposal_params["pipeline"] = explicit_pipeline
        if explicit_groups is not None:
            proposal_params["parallel_groups"] = explicit_groups
        proposal = classify(proposal_params)
        current = list(state["current_pipeline"])
        proposed_groups = proposal["parallel_groups"]
        operations = []
        if explicit_pipeline is not None:
            current = list(proposal["pipeline"])
        else:
            for gate in proposal["pipeline"]:
                if gate not in current:
                    target = next((candidate for candidate in proposal["pipeline"][proposal["pipeline"].index(gate) + 1:] if candidate in current), None)
                    operations.append({"op": "add", "gate": gate, **({"before": target} if target else {})})
                    current.insert(current.index(target) if target else len(current), gate)
        if intent == "rework_gate":
            gate = str(params.get("gate") or primary_gate(state) or "")
            operations = [{"op": "rework", "gate": gate}]
        elif intent == "resequence":
            desired = [gate for gate in proposal["pipeline"] if gate in current]
            working = list(state["current_pipeline"])
            for index, gate in enumerate(desired):
                if gate not in working:
                    continue
                target = next((candidate for candidate in desired[index + 1:] if candidate in working), None)
                if target and working.index(gate) > working.index(target):
                    operations.append({"op": "move", "gate": gate, "before": target})
                    working.remove(gate)
                    working.insert(working.index(target), gate)
        elif intent == "stop":
            pass
        current_groups = normalize_parallel_groups(
            explicit_groups if explicit_groups is not None else (proposed_groups if explicit_pipeline is not None else state.get("parallel_groups")),
            current,
        )
        groups_changed = current_groups != normalize_parallel_groups(state.get("parallel_groups"), state["current_pipeline"]) if current == state["current_pipeline"] else explicit_groups is not None
        receipt = {"at": now(), "decision": decision or ("updated" if params.get("apply") and (operations or (explicit_pipeline is not None and current != state["current_pipeline"]) or groups_changed) else "unchanged"), "reason": reason, "intent": intent, "signals": signals, "pipeline": current, "parallel_groups": current_groups, "operations": operations}
        result = {"applied": False, "intent": intent, "signals": signals, "pipeline_source": proposal["pipeline_source"], "current_pipeline": state["current_pipeline"], "current_parallel_groups": state.get("parallel_groups"), "suggested_pipeline": current, "suggested_parallel_groups": current_groups, "inferred_gates": proposal["conditional_gates"], "operations": operations, "receipt": receipt}
        if intent == "stop":
            if state.get("require_delegation") and decision != "stop":
                raise ValueError("stop intent requires decision=stop")
            if state.get("require_handoff") and (not state.get("handoff_created") or state.get("handoff_gate") != primary_gate(state)):
                raise ValueError("C2/C3 stop reassessment requires a current-gate handoff")
            state["status"] = "blocked"
            state["replan_count"] += 1
            state.setdefault("reassessment_receipts", []).append(receipt)
            save_state(task_dir, task_dir / "state.sqlite", state, "reassess", "pipeline stopped by policy")
            result.update({"applied": True, "state": state})
            return result
        pipeline_changed = explicit_pipeline is not None and current != state["current_pipeline"]
        parallel_groups_changed = current_groups != normalize_parallel_groups(state.get("parallel_groups"), state["current_pipeline"])
        if params.get("apply") and (operations or pipeline_changed or parallel_groups_changed):
            if state.get("require_delegation") and decision != "updated":
                raise ValueError("applied reassessment operations require decision=updated")
            change = apply_pipeline_operations(
                state,
                pipeline=current if explicit_pipeline is not None else None,
                operations=operations,
                allow_rework=bool(params.get("allow_rework", False)) or intent == "rework_gate",
                parallel_groups=current_groups if (explicit_groups is not None or explicit_pipeline is not None) else None,
            )
            append_pipeline_change(state, change, str(params.get("reason", "reassessment")), signals)
            invalidate_reworked_report_receipts(task_dir, state)
            sync_current_wave(state)
            state["replan_count"] += 1
            state.setdefault("reassessment_receipts", []).append(receipt)
            save_state(task_dir, task_dir / "state.sqlite", state, "reassess", str(params.get("reason", "reassessment")))
            result.update({"applied": True, "state": state, "change": change})
        else:
            if state.get("require_delegation") and decision == "updated":
                raise ValueError("decision=updated requires applied pipeline operations")
            state.setdefault("reassessment_receipts", []).append(receipt)
            save_state(task_dir, task_dir / "state.sqlite", state, "reassess", reason or "pipeline unchanged")
            result["state"] = state
        return result


def acquire_lock(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        path, owner = str(params["path"]), str(params["owner"])
        key = lock_key(path)
        existing = state["locks"].get(key)
        if existing and existing.get("expires_at") and existing["expires_at"] < now():
            state["locks"].pop(key, None)
            existing = None
        if existing and existing["owner_digest"] != digest_text(owner):
            raise ValueError(f"advisory lock already held for '{redact(path, 300)}'")
        entry = {"path": redact(path, 500), "owner": redact(owner, 256), "owner_digest": digest_text(owner), "gate": redact(params.get("gate") or primary_gate(state) or "", 64), "acquired_at": now(), "expires_at": params.get("expires_at"), "advisory": bool(params.get("advisory", True))}
        state["locks"][key] = entry
        save_state(task_dir, task_dir / "state.sqlite", state, "lock", f"{redact(path, 300)} → {redact(owner, 128)}")
        return {"state": state, "advisory": entry["advisory"]}


def release_lock(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        path, owner = str(params["path"]), str(params["owner"])
        key = lock_key(path)
        existing = state["locks"].get(key)
        if not existing or existing.get("owner_digest") != digest_text(owner):
            raise ValueError("lock is not held by this owner")
        del state["locks"][key]
        save_state(task_dir, task_dir / "state.sqlite", state, "unlock", f"{redact(path, 300)} released by {redact(owner, 128)}")
        return {"state": state}


def handoff(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        completed = [redact(item, 1000) for item in params.get("completed", [])][:100]
        next_action = redact(params.get("next_action", ""), 4000)
        if not completed or not next_action:
            raise ValueError("handoff requires non-empty completed and next_action")
        files = params.get("files", [])
        if not isinstance(files, list) or any(not isinstance(item, str) or not item.strip() for item in files):
            raise ValueError("handoff files must be an array of non-empty project-relative paths")
        receipt, _current_manifest = reconcile_manifest(task_dir, state, files)
        if not receipt["complete"]:
            # An incomplete file list is a normal coordinator-repair case: the
            # manifest reconciler has already identified the exact paths that
            # must be added. Do not turn this recoverable omission into a
            # generic MCP -32602 exception or mutate handoff state.
            return {
                "recorded": False,
                "reason": "incomplete_file_manifest",
                "recoverable": True,
                "next_action": "retry_create_handoff_with_complete_files",
                "required_fields": ["files"],
                "unaccounted_paths": receipt["unaccounted_paths"],
                "file_manifest_receipt": receipt,
                "state": state,
            }
        name = safe_id(str(params.get("name", f"handoff-{state['revision'] + 1}")))
        payload = {"schema": SCHEMA, "task_id": state["task_id"], "created_at": now(), "source_revision": state["revision"], "gate": primary_gate(state), "completed": completed, "files": [redact(item, 500) for item in files], "file_manifest_receipt": receipt, "decisions": [redact(item, 2000) for item in params.get("decisions", [])][:100], "risks": [redact(item, 2000) for item in params.get("risks", [])][:100], "next_action": next_action}
        path = task_dir / "handoffs" / f"{name}.json"
        write_json(path, payload)
        artifact = store_immutable_artifact(
            task_dir, state["task_id"], kind="handoff", title=f"handoffs/{name}.json",
            mime_type="application/json", content=_json_text(payload, label="handoff artifact", max_bytes=MAX_JSON_BYTES),
            export_path=f"handoffs/{name}.json",
        )
        state["handoff_created"] = True
        state["handoff_gate"] = primary_gate(state)
        state["handoff_source_revision"] = payload["source_revision"]
        state["final_manifest_receipt"] = receipt
        state.setdefault("manifest_receipts", []).append({"handoff": path.name, **receipt})
        save_state(task_dir, task_dir / "state.sqlite", state, "handoff", path.name)
        return {"handoff_file": str(path), "artifact_ref": artifact["artifact_ref"], "file_manifest_receipt": receipt, "state": state}


def claim_resource(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        resource, owner = str(params["path"]), str(params["owner"])
        entry = _claim_global_resource(root, resource, owner, "task", state["task_id"], params.get("expires_at"), str(params.get("kind", "resource")))
        key = lock_key(resource)
        state["locks"][key] = {"path": redact(resource, 500), "owner": redact(owner, 256), "owner_digest": digest_text(owner), "gate": redact(params.get("gate") or primary_gate(state) or "", 64), "acquired_at": entry["claimed_at"], "expires_at": entry["expires_at"], "advisory": False, "global": True}
        save_state(task_dir, task_dir / "state.sqlite", state, "resource", f"{redact(resource, 300)} → {redact(owner, 128)}")
        return {"state": state, "advisory": False}


def release_resource(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        resource, owner = str(params["path"]), str(params["owner"])
        key = lock_key(resource)
        existing = state["locks"].get(key)
        if not existing or existing.get("owner_digest") != digest_text(owner) or not existing.get("global"):
            raise ValueError("global resource is not held by this task owner")
        _release_global_resource(root, resource, owner, "task", state["task_id"])
        del state["locks"][key]
        save_state(task_dir, task_dir / "state.sqlite", state, "resource_release", f"{redact(resource, 300)} released")
        return {"state": state}


ORCHESTRATE_SCHEMA = "cortex/orchestration-runtime/v1"
ORCHESTRATION_TRANSACTION_SCHEMA = "cortex/orchestration-transaction/v1"
ORCHESTRATION_PLAN_SCHEMA = "cortex/orchestration-plan/v1"
ORCHESTRATE_OPERATIONS = {"start", "advance", "inspect", "resume", "deactivate", "lane", "resource", "question", "plan_approval"}
ORCHESTRATE_MUTATING_OPERATIONS = {"start", "advance", "resume", "deactivate", "lane", "resource", "question", "plan_approval"}
PUBLIC_ORCHESTRATION_SCHEMA = "cortex/orchestration/v4"
COORDINATOR_LOCK = (
    "COORDINATOR LOCK: root is coordination-only. Never inspect, read, edit, build, test, or run the target project. "
    "Use only Cortex lifecycle, exact dispatches, waiting, report evaluation, user communication, and safe recovery. "
    "All project operations belong to workers; failure or delay never authorizes direct project work."
)


def _request_diagnostic(path: str, message: str, expected: str | None = None) -> dict[str, Any]:
    diagnostic = {
        "code": "invalid_request",
        "phase": "preflight",
        "path": path,
        "message": redact(message, 1000),
    }
    if expected:
        diagnostic["expected"] = redact(expected, 500)
    return diagnostic


def _collect_orchestrate_diagnostics(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the complete public facade envelope before any ledger write.

    The runtime handlers still perform authoritative checks, but this pass is
    intentionally non-throwing and aggregates independent request mistakes so
    a coordinator can repair one payload instead of discovering errors one at
    a time.
    """
    diagnostics: list[dict[str, Any]] = []
    allowed_top_level = {
        "operation", "submission_id", "project_root", "principal", "thread_id",
        "task", "task_id", "wave_id", "waves", "host_capabilities", "completions", "payload",
        "gate_outcomes", "future_waves", "allow_rework", "reason",
    }
    for key in sorted(set(params) - allowed_top_level):
        diagnostics.append(_request_diagnostic(key, "unsupported orchestrate parameter", "a documented orchestrate parameter"))
    operation = params.get("operation")
    if not isinstance(operation, str) or operation.strip() not in ORCHESTRATE_OPERATIONS:
        diagnostics.append(_request_diagnostic(
            "operation",
            "operation is required and must be one of start, advance, inspect, resume, deactivate, lane, resource, question, or plan_approval",
            "a supported orchestrate operation",
        ))
        operation = str(operation or "").strip()
    else:
        operation = operation.strip()

    root = params.get("project_root")
    if not isinstance(root, str) or not root.strip():
        diagnostics.append(_request_diagnostic("project_root", "project_root is required", "an existing absolute project directory"))
    elif not Path(root).expanduser().is_absolute():
        diagnostics.append(_request_diagnostic("project_root", "project_root must be an absolute path", "an existing absolute project directory"))

    if os.environ.get("CORTEX_ROOT"):
        diagnostics.append(_request_diagnostic("environment.CORTEX_ROOT", "CORTEX_ROOT is not supported; use project_root", "no CORTEX_ROOT override"))

    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    mutating = operation in ORCHESTRATE_MUTATING_OPERATIONS
    if operation == "lane" and str(payload.get("command", "")) == "inspect":
        mutating = False
    if operation == "question" and str(payload.get("command", "ask")) in {"list", "updates"}:
        mutating = False
    if mutating:
        submission_id = params.get("submission_id")
        if not isinstance(submission_id, str) or not submission_id.strip():
            diagnostics.append(_request_diagnostic("submission_id", f"{operation} requires submission_id", "a stable lowercase submission id"))
        else:
            require_submission = submission_id.strip()
            if "/" in require_submission or "\\" in require_submission or not SAFE_ID_RE.fullmatch(require_submission.lower()):
                diagnostics.append(_request_diagnostic("submission_id", "submission_id is not a valid identifier", "lowercase letters, numbers, hyphens, or underscores only"))

    if operation in {"start", "advance", "inspect", "resume", "lane", "resource", "question", "plan_approval"}:
        if not isinstance(params.get("principal"), str) or not str(params.get("principal")).strip():
            diagnostics.append(_request_diagnostic("principal", f"{operation} requires principal", "the bound coordinator principal"))
    if operation == "start":
        if not isinstance(params.get("thread_id"), str) or not str(params.get("thread_id")).strip():
            diagnostics.append(_request_diagnostic("thread_id", "start requires thread_id", "the bound coordinator thread id"))

    def require_identifier(path: str, value: Any) -> None:
        raw = str(value or "").strip()
        if not raw:
            diagnostics.append(_request_diagnostic(path, f"{path} is required", "a non-empty lowercase identifier using letters, numbers, hyphens, or underscores"))
        elif "/" in raw or "\\" in raw or not SAFE_ID_RE.fullmatch(raw.lower()):
            diagnostics.append(_request_diagnostic(path, f"{path} is not a valid identifier: {raw!r}", "lowercase letters, numbers, hyphens, or underscores only"))

    if operation == "start":
        task = params.get("task")
        if not isinstance(task, dict):
            diagnostics.append(_request_diagnostic("task", "start requires a task object", "an object containing task_id, objective, and complexity"))
        else:
            require_identifier("task.task_id", task.get("task_id"))
            if not isinstance(task.get("objective"), str) or not task.get("objective", "").strip():
                diagnostics.append(_request_diagnostic("task.objective", "task.objective is required", "a non-empty task objective"))
            complexity = str(task.get("complexity", "")).strip().upper()
            if complexity not in {"C1", "C2", "C3"}:
                diagnostics.append(_request_diagnostic("task.complexity", "task.complexity must be C1, C2, or C3", "C1, C2, or C3"))

        waves = params.get("waves")
        if not isinstance(waves, list) or not waves:
            diagnostics.append(_request_diagnostic("waves", "start requires a non-empty waves array", "an ordered array of {wave_id, delegations} objects"))
        else:
            allowed_wave_keys = {"wave_id", "delegations"}
            allowed_delegation_keys = {
                "gate", "agent", "task_kind", "risk", "requested_model", "configured_default_model",
                "available_models", "available_thread_models", "dispatch_mode", "thread_environment",
                "requested_reasoning_effort", "user_requested_model", "retry", "parallel",
                "objective", "ownership", "context_files", "context_report_ids", "context_gates", "allowed_paths",
                "acceptance_criteria", "verification", "selection_reason",
            }
            for index, wave in enumerate(waves, 1):
                wave_path = f"waves[{index - 1}]"
                if not isinstance(wave, dict):
                    diagnostics.append(_request_diagnostic(wave_path, "wave must be an object", "{wave_id, delegations}"))
                    continue
                if not str(wave.get("wave_id", "")).strip():
                    diagnostics.append(_request_diagnostic(f"{wave_path}.wave_id", "wave_id is required; do not use id", "a stable lowercase wave identifier"))
                else:
                    require_identifier(f"{wave_path}.wave_id", wave.get("wave_id"))
                if "id" in wave:
                    diagnostics.append(_request_diagnostic(f"{wave_path}.id", "id is deprecated; use wave_id", "wave_id"))
                if "gates" in wave:
                    diagnostics.append(_request_diagnostic(f"{wave_path}.gates", "gates is deprecated; use delegations", "delegations: [{gate, agent, ...}]"))
                for key in sorted(set(wave) - allowed_wave_keys):
                    if key not in {"id", "gates"}:
                        diagnostics.append(_request_diagnostic(f"{wave_path}.{key}", "unsupported wave parameter", "wave_id or delegations"))
                delegations = wave.get("delegations")
                if not isinstance(delegations, list) or not delegations:
                    diagnostics.append(_request_diagnostic(f"{wave_path}.delegations", "wave requires a non-empty delegations array", "an array of delegation objects"))
                    continue
                for delegation_index, delegation in enumerate(delegations, 1):
                    delegation_path = f"{wave_path}.delegations[{delegation_index - 1}]"
                    if not isinstance(delegation, dict):
                        diagnostics.append(_request_diagnostic(delegation_path, "delegation must be an object", "{gate, agent, objective, ownership, allowed_paths, acceptance_criteria, verification}"))
                        continue
                    if "id" in delegation:
                        diagnostics.append(_request_diagnostic(f"{delegation_path}.id", "id is not supported for delegations; use gate", "gate"))
                    if "owner" in delegation:
                        diagnostics.append(_request_diagnostic(f"{delegation_path}.owner", "owner is not supported; use agent and ownership", "agent, ownership"))
                    for key in sorted(set(delegation) - allowed_delegation_keys - {"id", "owner"}):
                        diagnostics.append(_request_diagnostic(f"{delegation_path}.{key}", "unsupported delegation parameter", "a documented delegation field"))
                    if not str(delegation.get("gate", "")).strip():
                        diagnostics.append(_request_diagnostic(f"{delegation_path}.gate", "delegation requires gate", "a supported pipeline gate"))

        host = params.get("host_capabilities")
        if not isinstance(host, dict):
            diagnostics.append(_request_diagnostic("host_capabilities", "start requires host_capabilities", "an object with spawn_agent_models and optional confirmed default"))
        else:
            models = host.get("spawn_agent_models")
            if not isinstance(models, list) or not models:
                diagnostics.append(_request_diagnostic("host_capabilities.spawn_agent_models", "host_capabilities requires a non-empty spawn_agent_models array; available_models is deprecated", "exact native spawn_agent model identifiers"))
            for key in ("spawn_agent_models", "available_models", "create_thread_models", "available_thread_models"):
                if key in host and not isinstance(host[key], list):
                    diagnostics.append(_request_diagnostic(f"host_capabilities.{key}", "model catalog must be an array", "an array of model identifiers"))
            if "spawn_agent_default_model" in host and not isinstance(host["spawn_agent_default_model"], str):
                diagnostics.append(_request_diagnostic("host_capabilities.spawn_agent_default_model", "configured default model must be a string", "a supported model identifier"))

    elif operation in {"advance", "inspect", "resume"}:
        require_identifier("task_id", params.get("task_id"))
        if operation == "advance":
            require_identifier("wave_id", params.get("wave_id"))
            completions = params.get("completions")
            if not isinstance(completions, list) or not completions:
                diagnostics.append(_request_diagnostic("completions", "advance requires a non-empty completions array", "one terminal completion object per active attempt"))
            elif any(not isinstance(item, dict) for item in completions):
                diagnostics.append(_request_diagnostic("completions", "every completion must be an object", "completion objects with attempt_id and report_ref"))

    elif operation in {"lane", "resource", "question", "plan_approval"} and not isinstance(params.get("payload"), dict):
        diagnostics.append(_request_diagnostic("payload", f"{operation} requires an operation-specific payload object", "an object with a supported command"))

    return diagnostics


def orchestrate(params: dict[str, Any]) -> dict[str, Any]:
    """Internal engine facade retained for v4 lifecycle composition."""
    from cortex_runtime.orchestration_engine import orchestrate as _orchestrate

    return _orchestrate(params)


# These helpers remain importable for the v4 projection and compaction module,
# but their implementation belongs exclusively to the orchestration engine.
# Keeping the late binding avoids a second partially initialized ``cortex``
# module when Codex loads the executable through importlib.
def _orchestrate_request_digest(params: dict[str, Any]) -> str:
    from cortex_runtime.orchestration_engine import _orchestrate_request_digest as _implementation

    return _implementation(params)


def _orchestrate_inspect(params: dict[str, Any]) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import _orchestrate_inspect as _implementation

    return _implementation(params)


def _load_orchestrate_plan(task_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import _load_orchestrate_plan as _implementation

    return _implementation(task_dir, state)


def _orchestrate_summary(state: dict[str, Any]) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import _orchestrate_summary as _implementation

    return _implementation(state)


def _orchestrate_pipeline_snapshot(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import _orchestrate_pipeline_snapshot as _implementation

    return _implementation(state, plan)


def _wave_for_gates(plan: dict[str, Any], gates: list[str]) -> dict[str, Any] | None:
    from cortex_runtime.orchestration_engine import _wave_for_gates as _implementation

    return _implementation(plan, gates)


def _default_profile_for_gate(gate: str) -> str:
    from cortex_runtime.orchestration_engine import _default_profile_for_gate as _implementation

    return _implementation(gate)


def _pre_recorded_report(
    task_dir: Path,
    state: dict[str, Any],
    attempt_id: str,
    report_ref: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Expose the engine's report ownership validator to v4 adapter code."""
    from cortex_runtime.orchestration_engine import _pre_recorded_report as _implementation

    return _implementation(task_dir, state, attempt_id, report_ref)


def _auto_handoff(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    next_action: str,
) -> dict[str, Any]:
    """Expose the engine's automatic handoff helper to existing v4 tests."""
    from cortex_runtime.orchestration_engine import _auto_handoff as _implementation

    return _implementation(params, task_dir, state, next_action)


V3_COMPLEXITY_ALIASES = {
    "1": "C1", "c1": "C1", "simple": "C1", "small": "C1", "tiny": "C1", "light": "C1", "lightweight": "C1",
    "2": "C2", "c2": "C2", "standard": "C2", "default": "C2", "normal": "C2",
    "3": "C3", "c3": "C3", "complex": "C3", "large": "C3", "critical": "C3", "high": "C3",
}
V3_PLAN_APPROVAL_ALIASES = {
    "auto": "auto", "none": "auto", "off": "auto", "skip": "auto",
    "required": "required", "require": "required", "always": "required", "on": "required",
}
V3_STATUS_ALIASES = {
    "pass": "passed", "passed": "passed", "success": "passed", "succeeded": "passed", "complete": "passed", "completed": "passed",
    "fail": "failed", "failed": "failed", "failure": "failed", "error": "failed",
    "block": "blocked", "blocked": "blocked", "waiting": "blocked", "needs_input": "blocked",
    "cancel": "cancelled", "canceled": "cancelled", "cancelled": "cancelled",
    "supersede": "superseded", "superseded": "superseded", "replaced": "superseded",
}


def _v3_error(code: str, message: object, *, outcome: str = "needs_input", candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "outcome": outcome,
        "code": code,
        "diagnostics": [{"code": code, "message": redact(message, 1000)}],
        "dispatches": [],
        "next_action": f"{COORDINATOR_LOCK} {redact(message, 1000)}",
    }
    if candidates is not None:
        result["candidates"] = candidates
    return result


def _v3_task_ref(task_id: str) -> str:
    return "task-" + digest_text(task_id)[:12]


def _operation_registry_path(root: Path) -> Path:
    return root / "cortex.db"


def _operation_registry(root: Path) -> dict[str, Any]:
    registry = db_get_global(
        root,
        "operation_registry",
        {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "starts": {}, "tasks": {}, "updated_at": now()},
    )
    if registry.get("schema") != PUBLIC_ORCHESTRATION_SCHEMA:
        raise ValueError("orchestration operation registry schema is not supported")
    if not isinstance(registry.get("starts"), dict) or not isinstance(registry.get("tasks"), dict):
        raise ValueError("orchestration operation registry is invalid")
    return registry


def _write_operation_registry(root: Path, registry: dict[str, Any]) -> None:
    """Persist only compact replay receipts in the canonical registry."""
    for record in registry.get("tasks", {}).values():
        last = record.get("last_continue") if isinstance(record, dict) else None
        if isinstance(last, dict) and isinstance(last.get("response"), dict):
            compact = _v3_compact_continue_replay(last["response"])
            if compact != last["response"]:
                raise ValueError("operation registry contains a non-canonical dispatch replay")
    registry["updated_at"] = now()
    db_put_global(root, "operation_registry", registry)


def _prune_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _write_or_remove_json(path: Path, value: dict[str, Any]) -> None:
    if value:
        write_json(path, value)
        return
    if not path.exists():
        return
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"prune target must be a regular file: {path.name}")
    path.unlink()


def _remove_prune_directory(path: Path) -> None:
    """Remove one validated task artifact tree without following symlinks."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is None:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("prune task artifact target is unsafe")
    for entry in os.scandir(path):
        child = Path(entry.path)
        child_info = child.lstat()
        if stat.S_ISLNK(child_info.st_mode):
            raise ValueError("prune task artifact tree contains a symlink")
        if stat.S_ISDIR(child_info.st_mode):
            _remove_prune_directory(child)
        elif stat.S_ISREG(child_info.st_mode):
            child.unlink()
        else:
            raise ValueError("prune task artifact tree contains an unsafe entry")
    path.rmdir()


def prune_orchestration_state(params: dict[str, Any]) -> dict[str, Any]:
    """Delete only completed task-scoped Cortex state older than a confirmed age floor."""
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    unknown = sorted(set(payload) - {"confirmation", "older_than_days", "period", "full_confirmation"})
    if unknown:
        raise ValueError("unsupported prune payload fields: " + ", ".join(unknown))
    period = str(payload.get("period") or "").strip().lower()
    if not period and "older_than_days" not in payload:
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "awaiting_prune_selection",
            "question": "How much terminal Cortex orchestration state should be retained?",
            "options": [
                {"option_id": "keep_1d", "label": "1 day"},
                {"option_id": "keep_7d", "label": "7 days"},
                {"option_id": "keep_30d", "label": "30 days"},
                {"option_id": "full_reset", "label": "Full reset"},
            ],
            "next_action": "Choose one stable option_id. full_reset requires a second exact confirmation.",
        }
    if period:
        period_days = {"keep_1d": 1, "keep_7d": 7, "keep_30d": 30}
        if period == "full_reset":
            if payload.get("full_confirmation") != "RESET CORTEX":
                return {
                    "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                    "ok": True,
                    "outcome": "awaiting_full_reset_confirmation",
                    "confirmation_required": "RESET CORTEX",
                    "next_action": "Confirm the destructive full reset exactly; active workers make reset fail closed.",
                }
            if str(params.get("task_ref") or "").strip():
                raise ValueError("full reset is project-scoped and must omit task_ref")
            project = select_project_root(params)
            root = _contained_path(project, project / ".codex" / "cortex", "Cortex root")
            if root.is_symlink() or (root.exists() and not root.is_dir()):
                raise ValueError("full reset refuses a symlinked or non-directory Cortex root")
            active = []
            if root.exists():
                for task_id in read_task_index(root):
                    loaded = db_load_task(root, task_id)
                    if loaded is not None and loaded[1].get("status") in {"active", "blocked"}:
                        active.append(_v3_task_ref(task_id))
            if active:
                return {
                    "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                    "ok": False,
                    "outcome": "active_workers_block_full_reset",
                    "active_task_refs": active,
                    "next_action": "Complete or explicitly cancel every active task before retrying full reset.",
                }
            operation_id = digest_text(str(project) + ":full-reset")[:16]
            journal = root.parent / f".cortex-reset-{operation_id}.json"
            quarantine = root.parent / f".cortex-quarantine-{operation_id}"
            if journal.is_symlink() or quarantine.is_symlink():
                raise ValueError("full reset journal or quarantine target is unsafe")
            journal_state = ""
            if journal.exists():
                info = journal.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
                    raise ValueError("full reset journal is unsafe")
                try:
                    receipt = json.loads(journal.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise ValueError("full reset journal is invalid") from exc
                if (
                    not isinstance(receipt, dict)
                    or receipt.get("schema") != "cortex/full-reset/v1"
                    or receipt.get("operation_id") != operation_id
                    or receipt.get("root") != str(root)
                    or receipt.get("quarantine") != str(quarantine)
                ):
                    raise ValueError("full reset journal identity is invalid")
                journal_state = str(receipt.get("state") or "")
                if journal_state not in {"selection_received", "root_quarantined"}:
                    raise ValueError("full reset journal state is invalid")
            elif quarantine.exists():
                raise ValueError("full reset quarantine exists without its recovery journal")

            if not root.exists() and not quarantine.exists() and not journal.exists():
                return {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "full_reset", "removed": False, "next_action": "Cortex state was already absent."}
            if not journal.exists():
                write_json(journal, {
                    "schema": "cortex/full-reset/v1", "operation_id": operation_id,
                    "state": "selection_received", "root": str(root), "quarantine": str(quarantine), "updated_at": now(),
                })
                journal_state = "selection_received"
            if root.exists():
                if quarantine.exists():
                    raise ValueError("full reset cannot quarantine two Cortex roots")
                os.replace(root, quarantine)
                _fsync_directory(root.parent)
                write_json(journal, {
                    "schema": "cortex/full-reset/v1", "operation_id": operation_id,
                    "state": "root_quarantined", "root": str(root), "quarantine": str(quarantine), "updated_at": now(),
                })
                journal_state = "root_quarantined"
            elif journal_state == "selection_received" and quarantine.exists():
                # A crash can land after the atomic rename but before the next
                # journal replacement. The unique quarantine path is enough to
                # prove which tree must be finished.
                write_json(journal, {
                    "schema": "cortex/full-reset/v1", "operation_id": operation_id,
                    "state": "root_quarantined", "root": str(root), "quarantine": str(quarantine), "updated_at": now(),
                })
            if not quarantine.exists():
                raise ValueError("full reset recovery journal has no matching quarantined tree")
            _remove_prune_directory(quarantine)
            journal.unlink()
            _fsync_directory(root.parent)
            return {
                "schema": PUBLIC_ORCHESTRATION_SCHEMA, "ok": True, "outcome": "full_reset", "removed": True,
                "next_action": "All project-scoped Cortex state was removed; a later orchestration starts from a fresh database.",
            }
        if period not in period_days:
            raise ValueError("prune period must be keep_1d, keep_7d, keep_30d, or full_reset")
        payload = {**payload, "older_than_days": period_days[period]}
    if payload.get("confirmation") != "PRUNE":
        raise ValueError("prune requires payload.confirmation='PRUNE'")
    days = payload.get("older_than_days", 7)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
        raise ValueError("prune older_than_days must be an integer from 1 through 3650")
    if str(params.get("task_ref") or "").strip():
        raise ValueError("prune is project-scoped and must omit task_ref")
    root = ledger_root(params)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    # The selection and final metadata transaction are protected by the
    # state lock.  The durable tombstone and all filesystem deletion run
    # between them, so a failed deletion cannot erase canonical rows.
    with state_lock(root):
        task_index = read_task_index(root)
        stale: dict[str, Path] = {}
        statuses: dict[str, str] = {}
        retained_nonterminal_count = 0
        for raw_task_id, entry in task_index.items():
            task_id = safe_id(str(raw_task_id))
            loaded = db_load_task(root, task_id)
            if loaded is None:
                raise ValueError(f"SQLite task index refers to a missing task: {task_id}")
            task, state, _, artifact_dir = loaded
            task_dir = root / artifact_dir
            relative = Path(artifact_dir)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"SQLite task artifact path is unsafe for {task_id}")
            if task_dir.exists() and (task_dir.is_symlink() or not task_dir.is_dir()):
                raise ValueError(f"prune task artifact target is unsafe: {task_id}")
            if state.get("task_id") != task_id or task.get("task_id") != task_id:
                raise ValueError(f"prune task identity mismatch: {task_id}")
            updated = (
                _prune_timestamp(state.get("updated_at"))
                or _prune_timestamp(task.get("created_at"))
                or datetime.now(timezone.utc)
            )
            if updated > cutoff or state.get("status") != "completed":
                if state.get("status") != "completed":
                    retained_nonterminal_count += 1
                continue
            stale[task_id] = task_dir
            statuses[task_id] = str(state.get("status") or "unknown")

        stale_ids = set(stale)
        if not stale_ids:
            return {
                "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": True,
                "outcome": "pruned",
                "older_than_days": days,
                "cutoff": cutoff.isoformat(),
                "pruned_count": 0,
                "pruned_task_refs": [],
                "retained_count": len(task_index),
                "retained_nonterminal_count": retained_nonterminal_count,
                "next_action": "No stale task-scoped Cortex state matched the age threshold.",
            }

        # This is the prepare record: it is committed before filesystem work
        # and leaves the entire canonical task graph intact.
        tombstones = db_plan_prune(root, stale_ids)

    # Filesystem work intentionally happens outside state_lock.  A failure
    # leaves every task/global/lane/operation record available for retry.
    worker_id = f"prune-{os.getpid()}"
    for tombstone in tombstones:
        tombstone_id = str(tombstone["tombstone_id"])
        if tombstone.get("status") == "finalized":
            continue
        if tombstone.get("status") != "filesystem_removed":
            claimed = db_claim_prune_tombstone(root, worker_id, tombstone_id=tombstone_id)
            if claimed is None:
                raise ValueError("prune tombstone could not be claimed")
            target = root / str(claimed["artifact_dir"])
            try:
                _remove_prune_directory(target)
                db_mark_prune_filesystem_removed(root, tombstone_id, lease_owner=worker_id)
            except Exception as exc:
                db_fail_prune(root, tombstone_id, str(exc))
                raise

    # Recompute mutable metadata under the lock only after every filesystem
    # deletion is durably acknowledged.  The ledger API commits the globals,
    # lanes, task rows, manifests, operation records, and tombstone together.
    with state_lock(root):
        host_bindings = _host_session_bindings(root)
        host_bindings["tasks"] = {
            key: value for key, value in host_bindings["tasks"].items() if key not in stale_ids
        }
        host_bindings["updated_at"] = now()

        activations = _activation_records(root)
        if not isinstance(activations, dict):
            raise ValueError("activation registry is invalid")
        activations = {
            key: value for key, value in activations.items()
            if not isinstance(value, dict) or value.get("task_id") not in stale_ids
        }

        registry = _operation_registry(root)
        registry["tasks"] = {
            key: value for key, value in registry["tasks"].items() if key not in stale_ids
        }
        registry["starts"] = {
            key: value for key, value in registry["starts"].items()
            if not isinstance(value, dict) or value.get("task_id") not in stale_ids
        }

        claims = db_get_global(root, "resource_claims", {})
        if not isinstance(claims, dict):
            raise ValueError("resource claims are invalid")
        claims = {
            key: value for key, value in claims.items()
            if not (
                isinstance(value, dict)
                and value.get("scope_kind") == "task"
                and value.get("scope_id") in stale_ids
            )
        }

        lane_updates = 0
        lane_mutations: list[tuple[dict[str, Any], dict[str, Any], str, str]] = []
        for lane_definition, lane in db_all_lanes(root):
            bound = lane.get("bound_tasks", [])
            if not isinstance(bound, list):
                raise ValueError("lane bound_tasks is invalid")
            filtered = [item for item in bound if item not in stale_ids]
            if filtered != bound:
                lane["bound_tasks"] = filtered
                lane["updated_at"] = now()
                lane_mutations.append((lane_definition, lane, "prune", f"removed {len(bound) - len(filtered)} stale task binding(s)"))
                lane_updates += 1

        global_updates = {
            "host_sessions": host_bindings if host_bindings["tasks"] else None,
            "activations": activations if activations else None,
            "resource_claims": claims if claims else None,
            "operation_registry": registry,
        }
        finalized = db_finalize_prunes(
            root,
            [str(item["tombstone_id"]) for item in tombstones],
            global_updates=global_updates,
            lane_updates=lane_mutations,
        )

        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "pruned",
            "older_than_days": days,
            "cutoff": cutoff.isoformat(),
            "pruned_count": len(stale_ids),
            "pruned_task_refs": [_v3_task_ref(task_id) for task_id in sorted(stale_ids)],
            "pruned_statuses": {status: list(statuses.values()).count(status) for status in sorted(set(statuses.values()))},
            "removed_operations": finalized["removed_operations"],
            "removed_classification_receipts": finalized["removed_classifications"],
            "removed_manifest_snapshots": finalized["removed_manifest_snapshots"],
            "updated_lanes": lane_updates,
            "retained_count": len(task_index) - len(stale_ids),
            "retained_nonterminal_count": retained_nonterminal_count,
            "next_action": "Prune completed; active/blocked tasks, recent tasks, and all project source/documentation were preserved.",
        }


def _v3_task_state(root: Path, task_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]] | None:
    loaded = db_load_task(root, task_id)
    if loaded is None:
        return None
    task, state, plan, artifact_dir = loaded
    artifact_relative = Path(artifact_dir)
    if artifact_relative.is_absolute() or ".." in artifact_relative.parts:
        raise ValueError("orchestration task artifact directory is unsafe")
    task_dir = root / artifact_relative
    if state.get("schema") != SCHEMA or task.get("schema") != SCHEMA or state.get("task_id") != task_id or task.get("task_id") != task_id:
        raise ValueError("orchestration task lookup found an unsupported or mismatched ledger")
    if plan is None:
        return None
    if plan.get("schema") != ORCHESTRATION_PLAN_SCHEMA or plan.get("task_id") != task_id:
        raise ValueError("orchestration task lookup found an unsupported plan")
    return task_dir, state, task


def _v3_task_candidates(params: dict[str, Any], *, include_completed: bool = False) -> list[dict[str, Any]]:
    root = ledger_root(params)
    candidates: list[dict[str, Any]] = []
    for task_id in sorted(read_task_index(root)):
        loaded = _v3_task_state(root, task_id)
        if loaded is None:
            continue
        _, state, task = loaded
        if not include_completed and state.get("status") not in {"active", "blocked"}:
            continue
        candidates.append({
            "task_id": task_id,
            "task_ref": _v3_task_ref(task_id),
            "objective": redact(task.get("objective", ""), 300),
            "status": state.get("status"),
            "created_at": task.get("created_at"),
        })
    return candidates


def _v3_resolve_task(params: dict[str, Any], *, include_completed: bool = False) -> tuple[Path, dict[str, Any], dict[str, Any], str] | dict[str, Any]:
    root = ledger_root(params)
    candidates = _v3_task_candidates(params, include_completed=include_completed)
    requested = str(params.get("task_ref") or "").strip()
    if requested:
        selected = next((item for item in candidates if item["task_ref"] == requested), None)
        if selected is None:
            return _v3_error("unknown_task_ref", "task_ref does not identify a selectable Cortex task")
    elif len(candidates) == 1:
        selected = candidates[0]
    elif not candidates:
        return _v3_error("no_active_task", "No active Cortex task exists in this project root.")
    else:
        public_candidates = [{key: item[key] for key in ("task_ref", "objective", "status")} for item in candidates]
        return _v3_error(
            "task_selection_required",
            "Several Cortex tasks are active; retry with one returned task_ref.",
            outcome="needs_selection",
            candidates=public_candidates,
        )
    loaded = _v3_task_state(root, str(selected["task_id"]))
    if loaded is None:
        return _v3_error("task_unavailable", "The selected Cortex task is unavailable.")
    task_dir, state, task = loaded
    return task_dir, state, task, str(selected["task_ref"])


def _v3_complexity(value: object) -> str:
    raw = str(value or "C2").strip().lower().replace("-", "_").replace(" ", "_")
    complexity = V3_COMPLEXITY_ALIASES.get(raw)
    if complexity:
        return complexity
    suggestions = difflib.get_close_matches(raw, sorted(V3_COMPLEXITY_ALIASES), n=3)
    suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
    raise ValueError("task.complexity is not recognized" + suffix)


def _v3_plan_approval(value: object, complexity: str) -> str:
    """Normalize the post-plan review policy selected for a public task."""
    if value in {None, ""}:
        return "required" if complexity in {"C2", "C3"} else "auto"
    raw = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    policy = V3_PLAN_APPROVAL_ALIASES.get(raw)
    if policy:
        return policy
    suggestions = difflib.get_close_matches(raw, sorted(V3_PLAN_APPROVAL_ALIASES), n=3)
    suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
    raise ValueError("task.plan_approval must be auto or required" + suffix)


def _v3_model(value: object) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    aliases = {"luna": "gpt-5.6-luna", "terra": "gpt-5.6-terra", "sol": "gpt-5.6-sol"}
    model = aliases.get(raw, raw)
    if model not in SUPPORTED_MODELS:
        suggestions = difflib.get_close_matches(model, sorted(SUPPORTED_MODELS | set(aliases)), n=3)
        suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
        raise ValueError("worker model is not supported" + suffix)
    return model


def _v3_compact_waves(
    raw_waves: object,
    task: dict[str, Any],
    *,
    completed_gates: set[str] | None = None,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw_waves, list) or not raw_waves:
        raise ValueError("waves must be a non-empty array when supplied")
    result: list[dict[str, Any]] = []
    allowed_worker_keys = {
        "phase", "profile", "objective", "paths", "acceptance", "verification",
        "model", "user_requested_model", "effort", "visible", "isolated_checkout", "depends_on", "context_files",
    }
    phase_waves: dict[str, tuple[int, str]] = {}
    available_context_gates = set(completed_gates or set())
    for wave_index, raw_wave in enumerate(raw_waves, 1):
        if not isinstance(raw_wave, dict) or set(raw_wave) != {"workers"}:
            raise ValueError(f"waves[{wave_index - 1}] must contain only workers")
        workers = raw_wave.get("workers")
        if not isinstance(workers, list) or not workers or len(workers) > 32:
            raise ValueError(f"waves[{wave_index - 1}].workers must contain 1..32 workers")
        delegations: list[dict[str, Any]] = []
        wave_gates: set[str] = set()
        for worker_index, worker in enumerate(workers, 1):
            if not isinstance(worker, dict):
                raise ValueError(f"waves[{wave_index - 1}].workers[{worker_index - 1}] must be an object")
            unknown = sorted(set(worker) - allowed_worker_keys)
            if unknown:
                raise ValueError(f"unsupported compact worker fields: {', '.join(unknown)}")
            raw_phase = str(worker.get("phase") or "").strip()
            if not raw_phase:
                raise ValueError(f"waves[{wave_index - 1}].workers[{worker_index - 1}].phase is required")
            gate = canonical_pipeline_gate(raw_phase)
            if gate not in AVAILABLE_GATES:
                suggestions = difflib.get_close_matches(gate, sorted(AVAILABLE_GATES | set(PIPELINE_GATE_ALIASES)), n=3)
                suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
                raise ValueError(f"unknown worker phase {raw_phase!r}" + suffix)
            prior_phase = phase_waves.get(gate)
            if prior_phase is not None and prior_phase[0] != wave_index:
                guidance = (
                    " Use phase 'close' with profile 'build_verification' for final build verification."
                    if gate == "qa" else " Put multiple owners of one phase in the same wave."
                )
                raise ValueError(
                    f"waves repeat canonical phase {gate!r}: {prior_phase[1]!r} and {raw_phase!r} "
                    f"normalize to the same phase.{guidance}"
                )
            phase_waves.setdefault(gate, (wave_index, raw_phase))
            raw_profile = str(worker.get("profile") or "").strip()
            normalized_profile = raw_profile.lower().replace("-", "_").replace(" ", "_")
            auto_implementation_profile = normalized_profile in V3_AUTOMATIC_IMPLEMENTATION_PROFILE_ALIASES
            if auto_implementation_profile and gate != "implementation":
                raise ValueError(
                    f"generic worker profile {raw_profile!r} is valid only for the implementation phase; "
                    "omit profile to use the canonical phase owner"
                )
            implementation_selection = select_implementation_profile(task) if auto_implementation_profile else None
            profile = (
                str(implementation_selection["profile"])
                if implementation_selection is not None else
                canonical_profile(raw_profile) if raw_profile else _default_profile_for_gate(gate)
            )
            if profile not in AGENTS:
                suggestions = difflib.get_close_matches(profile, sorted(AGENTS | set(PROFILE_ALIASES)), n=3)
                suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
                raise ValueError(f"unknown worker profile {raw_profile!r}" + suffix)
            if not profile_can_own_gate(profile, gate):
                supported = PROFILES[profile].get("gates", []) or ["implementation"]
                raise ValueError(
                    f"worker profile {profile!r} cannot own phase {gate!r}; "
                    f"supported phase(s): {', '.join(supported)}"
                )
            visible = bool(worker.get("visible", False))
            isolated = bool(worker.get("isolated_checkout", False))
            if isolated and not visible:
                raise ValueError("isolated_checkout requires visible=true")
            spec: dict[str, Any] = {
                "gate": gate,
                "agent": profile,
                "selection_reason": (
                    f"The coordinator requested a generic implementation worker; {implementation_selection['reason']}"
                    if implementation_selection is not None else
                    f"The coordinator explicitly selected `{profile}` for the `{gate}` phase."
                    if raw_profile else
                    f"`{profile}` is the canonical automatic owner for the `{gate}` phase."
                ),
            }
            if "depends_on" in worker:
                raw_dependencies = worker["depends_on"]
                if not isinstance(raw_dependencies, list) or len(raw_dependencies) > len(AVAILABLE_GATES):
                    raise ValueError("worker depends_on must be an array of prerequisite phases")
                dependencies = [canonical_pipeline_gate(item) for item in raw_dependencies]
                if len(dependencies) != len(set(dependencies)):
                    raise ValueError("worker depends_on phases must be unique")
                unknown_dependencies = sorted(set(dependencies) - AVAILABLE_GATES)
                if unknown_dependencies:
                    raise ValueError("worker depends_on contains unknown phases: " + ", ".join(unknown_dependencies))
                unavailable = sorted(set(dependencies) - available_context_gates)
                if unavailable:
                    raise ValueError(
                        "worker depends_on may reference only completed or earlier-wave phases: "
                        + ", ".join(unavailable)
                    )
                spec["context_gates"] = dependencies
            for source, target in (
                ("objective", "objective"), ("paths", "allowed_paths"),
                ("acceptance", "acceptance_criteria"), ("verification", "verification"),
                ("context_files", "context_files"),
            ):
                if source in worker:
                    if source == "context_files" and project_root is not None:
                        spec[target] = _project_knowledge_context(project_root, worker[source])[0]
                    else:
                        spec[target] = worker[source]
            model = _v3_model(worker.get("model"))
            if model:
                spec["requested_model"] = model
            user_requested_model = _v3_model(worker.get("user_requested_model"))
            if user_requested_model:
                if model and model != user_requested_model:
                    raise ValueError("worker model and user_requested_model must match")
                spec["requested_model"] = user_requested_model
                spec["user_requested_model"] = user_requested_model
            if str(worker.get("effort") or "").strip():
                spec["requested_reasoning_effort"] = str(worker["effort"]).strip().lower()
            if visible:
                spec["dispatch_mode"] = "visible_thread"
                spec["thread_environment"] = "worktree" if isolated else "local"
            delegations.append(spec)
            wave_gates.add(gate)
        result.append({"wave_id": f"wave-{wave_index:02d}", "delegations": delegations})
        available_context_gates.update(wave_gates)
    return result


def _v3_auto_waves(task: dict[str, Any]) -> list[dict[str, Any]]:
    classified = classify({"complexity": task["complexity"], "requirements": _task_routing_items(task)})
    implementation_selection = select_implementation_profile(task)

    def automatic_spec(gate: str) -> dict[str, Any]:
        if gate == "implementation":
            return {
                "gate": gate,
                "agent": implementation_selection["profile"],
                "selection_reason": implementation_selection["reason"],
            }
        profile = _default_profile_for_gate(gate)
        return {
            "gate": gate,
            "agent": profile,
            "selection_reason": f"`{profile}` is the canonical automatic owner for the `{gate}` phase.",
        }

    knowledge_harvest = _is_knowledge_harvest_task(task)
    groups = (
        [["scope"], ["discover"], ["architecture"], ["plan"], ["documentation"], ["review"], ["close"]]
        if knowledge_harvest else classified["parallel_groups"]
    )
    return [
        {
            "wave_id": f"wave-{index:02d}",
            "delegations": [automatic_spec(gate) for gate in group],
        }
        for index, group in enumerate(groups, 1)
    ]


def _v3_host_capabilities() -> dict[str, Any]:
    return {
        "spawn_agent_models": sorted(SUPPORTED_MODELS),
        "spawn_agent_default_model": CONFIGURED_DEFAULT_MODEL,
        "create_thread_models": ["gpt-5.6-luna"],
    }


def _v3_native_arguments(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("host_tool") == "create_thread":
        arguments: dict[str, Any] = {
            "prompt": request.get("prompt") or request.get("message"),
            "title": request.get("title") or request.get("task_name"),
            "target": {"environment": {"type": request.get("thread_environment") or "local"}},
        }
    else:
        arguments = {
            "task_name": request.get("task_name"),
            "message": request.get("message"),
            "reasoning_effort": request.get("reasoning_effort"),
            "fork_turns": request.get("fork_turns") or "none",
        }
    if request.get("model"):
        arguments["model"] = request["model"]
    return {key: value for key, value in arguments.items() if value is not None}


def _v3_response(
    old: dict[str, Any],
    task_ref: str,
    *,
    include_result: bool = False,
    start_replayed: bool | None = None,
) -> dict[str, Any]:
    """Public delegating entrypoint for the orchestration response adapter."""
    return render_v3_response(
        old,
        task_ref,
        native_arguments=_v3_native_arguments,
        public_schema=PUBLIC_ORCHESTRATION_SCHEMA,
        coordinator_lock=COORDINATOR_LOCK,
        include_result=include_result,
        start_replayed=start_replayed,
    )



def _v3_compact_continue_replay(response: dict[str, Any]) -> dict[str, Any]:
    """Turn a completed continue response into a non-dispatching receipt."""
    compact = dict(response)
    compact["dispatches"] = []
    compact["replayed"] = True
    task_ref = str(compact.get("task_ref") or "")
    compact["next_action"] = (
        f"{COORDINATOR_LOCK} continue_orchestration already completed for task_ref={task_ref}. Do not invoke or "
        "repeat a worker dispatch from this replay. If the original response was lost before its native dispatches "
        "were invoked, call manage_orchestration with intent inspect once and invoke only still-awaiting dispatches."
    )
    return compact


def _v3_start_reservation(
    params: dict[str, Any],
    task: dict[str, Any],
) -> tuple[str, str, str, str, str, bool]:
    root = ledger_root(params)
    # The canonical user-authored request is the active-task identity boundary.
    # Its only normalization removes Desktop's injected local Cortex skill-link
    # wrapper; coordinator-derived language metadata, waves, routing, or
    # verification refinements must not turn a retry into a second active task.
    start_digest = _orchestrate_request_digest({"user_request": task.get("user_request")})
    with state_lock(root):
        registry = _operation_registry(root)
        prior = registry["starts"].get(start_digest)
        if isinstance(prior, dict):
            task_id = str(prior.get("task_id") or "")
            loaded = _v3_task_state(root, task_id) if task_id else None
            # Reuse an in-flight reservation as well as a materialized active
            # task. Two MCP processes can observe the digest after reservation
            # but before orchestrate() creates the task ledger; allocating a
            # second task here would split one idempotent start across sessions.
            if loaded is None or loaded[1].get("status") in {"active", "blocked"}:
                return (
                    task_id,
                    str(prior["task_ref"]),
                    str(prior["principal"]),
                    str(prior.get("thread_id") or prior["principal"]),
                    str(prior["submission_id"]),
                    True,
                )
        objective_slug = v3_task_slug(task["objective"])
        task_id = safe_id(f"{objective_slug}-{secrets.token_hex(4)}")
        task_ref = _v3_task_ref(task_id)
        principal = safe_id("orchestration-" + task_ref)
        thread_id = _codex_host_session_id() or principal
        submission_id = safe_id("orchestration-start-" + secrets.token_hex(8))
        reservation = {
            "task_id": task_id,
            "task_ref": task_ref,
            "principal": principal,
            "thread_id": thread_id,
            "submission_id": submission_id,
            "created_at": now(),
        }
        registry["starts"][start_digest] = reservation
        registry["tasks"].setdefault(task_id, {})["start"] = {"digest": start_digest, **reservation}
        _write_operation_registry(root, registry)
        return task_id, task_ref, principal, thread_id, submission_id, False


def start_orchestration(params: dict[str, Any]) -> dict[str, Any]:
    """Start public Cortex orchestration without caller-managed lifecycle identifiers."""
    try:
        selected_project_root = select_project_root(params)
        if set(params) - {"project_root", "task", "waves", "_follow_up"}:
            raise ValueError("start_orchestration accepts only project_root, task, and optional waves")
        raw_task = params.get("task")
        if not isinstance(raw_task, dict):
            raise ValueError("task must be an object containing the exact user_request")
        allowed_task = {
            "user_request", "objective", "requirements", "acceptance_criteria", "scope", "allowed_paths",
            "verification", "budget", "pause_conditions", "user_language", "language",
            "complexity", "replan_limit", "plan_approval",
        }
        unknown_task = sorted(set(raw_task) - allowed_task)
        if unknown_task:
            raise ValueError("unsupported task fields: " + ", ".join(unknown_task))
        user_request = canonicalize_desktop_cortex_request(raw_task.get("user_request"))
        if not user_request:
            raise ValueError(
                "task.user_request is required and must preserve the exact user-authored task without coordinator expansion"
            )
        supplied_objective = canonicalize_desktop_cortex_request(raw_task.get("objective"))
        if supplied_objective and supplied_objective != user_request:
            raise ValueError(
                "task.objective must exactly match task.user_request when supplied; do not paraphrase, normalize, "
                "or add product requirements before a worker can ask the user"
            )
        objective = user_request
        intent_required, intent_reason = _intent_clarification_preflight(user_request)
        task = dict(raw_task)
        task["user_request"] = user_request
        task["objective"] = objective
        task["intent_clarification_required"] = intent_required
        task["intent_clarification_reason"] = intent_reason
        if "_follow_up" in params:
            if not isinstance(params["_follow_up"], dict):
                raise ValueError("internal follow_up context must be an object")
            task["follow_up"] = sanitize_structured(params["_follow_up"])
        task["complexity"] = _v3_complexity(raw_task.get("complexity"))
        task["acceptance_criteria"], task["verification"] = _required_task_result_contract(task)
        task["plan_approval"] = (
            "auto"
            if _is_knowledge_harvest_task(task)
            else _v3_plan_approval(raw_task.get("plan_approval"), task["complexity"])
        )
        language_alias = task.pop("language", None)
        task["user_language"] = normalize_user_language(
            task.get("user_language") or language_alias,
            objective,
        )
        waves = (
            _v3_compact_waves(params["waves"], task, project_root=selected_project_root)
            if params.get("waves") is not None else _v3_auto_waves(task)
        )
        task_id, task_ref, principal, thread_id, submission_id, replayed = _v3_start_reservation(params, task)
        if replayed:
            # A linked corrective task may be replayed after the coordinator
            # intentionally deactivated its prior session while recovering a
            # stale worker attempt.  The follow_up management operation is an
            # explicit Cortex route, so restore the server-owned activation
            # for this idempotent replay instead of leaking an internal
            # activation hint into the user-facing response.
            if isinstance(params.get("_follow_up"), dict):
                replay_params = {
                    "project_root": params["project_root"],
                    "principal": principal,
                    "thread_id": thread_id,
                }
                if not activation_record(ledger_root(params), replay_params, task_id):
                    activated = activate_orchestration({
                        **replay_params,
                        "user_command": ACTIVATION_COMMAND,
                    })
                    if not activated.get("active"):
                        raise ValueError("linked corrective-task replay could not restore its Cortex activation")
            loaded = _v3_task_state(ledger_root(params), task_id)
            if loaded is None:
                old = {
                    "ok": True,
                    "state": "waiting_workers",
                    "wave_id": None,
                    "spawn_requests": [],
                }
            else:
                old = _orchestrate_inspect({
                    "project_root": params["project_root"],
                    "principal": principal,
                    "thread_id": thread_id,
                    "task_id": task_id,
                })
            return _v3_response(old, task_ref, start_replayed=True)
        old = orchestrate({
            "operation": "start",
            "submission_id": submission_id,
            "project_root": params["project_root"],
            "principal": principal,
            "thread_id": thread_id,
            "task": {**task, "task_id": task_id},
            "waves": waves,
            "host_capabilities": _v3_host_capabilities(),
        })
        return _v3_response(old, task_ref, start_replayed=replayed)
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        return _v3_error("start_validation_failed", exc)


def _v3_status(value: object, *, has_report: bool) -> str:
    if value in {None, ""} and has_report:
        return "passed"
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    status_value = V3_STATUS_ALIASES.get(raw)
    if status_value:
        return status_value
    suggestions = difflib.get_close_matches(raw, sorted(V3_STATUS_ALIASES), n=3)
    suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
    raise ValueError("result status is not recognized" + suffix)


def _v3_active_wave_context(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
) -> tuple[dict[str, Any], list[str], int | None]:
    plan = _load_orchestrate_plan(task_dir, state)
    wave = _wave_for_gates(plan, active_gates(state))
    if wave is None:
        raise ValueError("The active Cortex task has no current wave.")
    wave_match = re.search(r"(\d+)$", str(wave.get("wave_id") or ""))
    expected_step = int(wave_match.group(1)) if wave_match else None
    if type(params.get("step")) is not int or params.get("step") != expected_step:
        raise ValueError(f"continue step must match the active relative step {expected_step}")
    active_attempt_ids = [
        item["attempt_id"] for item in state.get("attempts", [])
        if item.get("gate") in wave.get("gates", [])
        and item.get("status") in {AWAITING_HOST_SPAWN, "running"}
        and not item.get("invalidated")
    ]
    # A SubagentStop without a report is terminalized before recovery, but a
    # mixed wave still contains live workers.  Keep that exact failed slot in
    # the relative result contract; otherwise the handoff asks the
    # coordinator to submit a failed receipt that this adapter rejects as an
    # unknown slot.  Other terminal attempts (notably passed attempts kept
    # during bounded gate rework) remain omitted when a live retry exists.
    reportless_failure_ids = {
        str(item.get("attempt_id") or "")
        for item in state.get("attempts", [])
        if item.get("gate") in wave.get("gates", [])
        and item.get("status") == "failed"
        and item.get("host_stop_outcome") == "native_worker_stopped_without_report"
        and not item.get("invalidated")
    }
    wave_attempt_ids = [
        str(attempt_id)
        for attempt_id in (wave.get("attempt_ids") or [])
        if str(attempt_id or "").strip()
    ]
    if active_attempt_ids:
        eligible = set(active_attempt_ids) | reportless_failure_ids
        attempt_ids = [attempt_id for attempt_id in wave_attempt_ids if attempt_id in eligible]
        if not attempt_ids:
            attempt_ids = active_attempt_ids + [
                attempt_id for attempt_id in reportless_failure_ids
                if attempt_id not in active_attempt_ids
            ]
    else:
        attempt_ids = wave_attempt_ids
    if not attempt_ids:
        attempt_ids = active_attempt_ids
    return wave, attempt_ids, expected_step


def _v3_continue_context(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    task_ref: str,
) -> tuple[dict[str, Any], list[str], str, str, dict[str, Any] | None]:
    root = ledger_root(params)
    request_digest = _orchestrate_request_digest({key: value for key, value in params.items() if key != "task_ref"})
    with state_lock(root):
        registry = _operation_registry(root)
        task_record = registry["tasks"].setdefault(state["task_id"], {})
        last = task_record.get("last_continue")
        if isinstance(last, dict) and last.get("digest") == request_digest and isinstance(last.get("response"), dict):
            return {}, [], "", request_digest, dict(last["response"])
        inflight = task_record.get("inflight_continue")
        if isinstance(inflight, dict):
            if inflight.get("digest") != request_digest:
                raise ValueError("A different continue payload is already recovering this active wave; retry the original payload first.")
            return dict(inflight["old_params"]), list(inflight["attempt_ids"]), str(inflight["wave_id"]), request_digest, None
        wave, attempt_ids, _ = _v3_active_wave_context(params, task_dir, state)
        submission_id = safe_id("orchestration-continue-" + digest_text(state["task_id"] + ":" + str(wave["wave_id"]) + ":" + request_digest)[:20])
        old_params = {
            "operation": "advance",
            "submission_id": submission_id,
            "project_root": params["project_root"],
            "principal": state.get("principal"),
            "thread_id": state.get("thread_id"),
            "task_id": state["task_id"],
            "wave_id": wave["wave_id"],
        }
        task_record["inflight_continue"] = {
            "digest": request_digest,
            "wave_id": wave["wave_id"],
            "attempt_ids": attempt_ids,
            "old_params": old_params,
            "task_ref": task_ref,
            "created_at": now(),
        }
        _write_operation_registry(root, registry)
        return old_params, attempt_ids, str(wave["wave_id"]), request_digest, None


def _v3_store_continue(params: dict[str, Any], task_id: str, request_digest: str, response: dict[str, Any], *, clear_only: bool = False) -> None:
    root = ledger_root(params)
    with state_lock(root):
        registry = _operation_registry(root)
        task_record = registry["tasks"].setdefault(task_id, {})
        task_record.pop("inflight_continue", None)
        if not clear_only:
            task_record["last_continue"] = {
                "digest": request_digest,
                "response": _v3_compact_continue_replay(response),
                "completed_at": now(),
            }
        _write_operation_registry(root, registry)


def _v3_completed_replay(params: dict[str, Any]) -> dict[str, Any] | None:
    """Replay a final continue after task completion removed the active mapping."""
    if _v3_task_candidates(params):
        return None
    request_digest = _orchestrate_request_digest({key: value for key, value in params.items() if key != "task_ref"})
    requested_ref = str(params.get("task_ref") or "").strip()
    registry = _operation_registry(ledger_root(params))
    matches = []
    for task_id, record in registry.get("tasks", {}).items():
        if requested_ref and _v3_task_ref(str(task_id)) != requested_ref:
            continue
        last = record.get("last_continue") if isinstance(record, dict) else None
        if isinstance(last, dict) and last.get("digest") == request_digest and isinstance(last.get("response"), dict):
            matches.append(dict(last["response"]))
    return matches[0] if len(matches) == 1 else None


def _v3_active_replay(params: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    request_digest = _orchestrate_request_digest({key: value for key, value in params.items() if key != "task_ref"})
    record = _operation_registry(ledger_root(params)).get("tasks", {}).get(task_id, {})
    last = record.get("last_continue") if isinstance(record, dict) else None
    if isinstance(last, dict) and last.get("digest") == request_digest and isinstance(last.get("response"), dict):
        return dict(last["response"])
    return None


def continue_orchestration(params: dict[str, Any]) -> dict[str, Any]:
    """Advance exactly the active Cortex wave using relative worker slots."""
    resolved_task_ref = str(params.get("task_ref") or "").strip() or None
    try:
        select_project_root(params)
        allowed = {"project_root", "task_ref", "step", "results", "future_waves", "rework", "reason"}
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ValueError("unsupported continue fields: " + ", ".join(unknown))
        results = params.get("results")
        if not isinstance(results, list) or not results:
            raise ValueError("results must be a non-empty array")
        completed_replay = _v3_completed_replay(params)
        if completed_replay is not None:
            return completed_replay
        resolved = _v3_resolve_task(params)
        if isinstance(resolved, dict):
            return resolved
        task_dir, state, task, task_ref = resolved
        resolved_task_ref = task_ref
        active_replay = _v3_active_replay(params, state["task_id"])
        if active_replay is not None:
            return active_replay
        if _plan_approval_is_pending(state):
            raise ValueError(
                "the completed plan is awaiting explicit user approval; use manage_orchestration "
                "with intent=plan_approval before continuing"
            )
        _, attempt_ids, _ = _v3_active_wave_context(params, task_dir, state)
        open_questions = [
            item for item in _open_blocking_questions(task_dir, state)
            if item.get("attempt_id") in set(attempt_ids)
        ]
        if open_questions:
            refs = ", ".join(str(item["question_id"]) for item in open_questions)
            raise ValueError(
                f"active wave has unanswered blocking worker question(s): {refs}; "
                "surface each question to the user and resume the same worker before continue_orchestration"
            )
        if params.get("future_waves") is not None and not str(params.get("reason") or "").strip():
            raise ValueError(
                "future_waves requires a concise reason identifying the new evidence or coordinator decision"
            )
        if params.get("rework") and params.get("future_waves") is None:
            raise ValueError("rework=true requires explicit future_waves; Cortex never guesses a replacement pipeline")
        future_waves = (
            _v3_compact_waves(
                params["future_waves"],
                task,
                completed_gates=(
                    set(state.get("completed_gates", []))
                    | set(state.get("skipped_gates", []))
                    | set(active_gates(state))
                ),
                project_root=select_project_root(params),
            )
            if params.get("future_waves") is not None else None
        )
        if len(results) != len(attempt_ids):
            raise ValueError(f"active wave requires exactly {len(attempt_ids)} result(s)")
        slots: dict[int, dict[str, Any]] = {}
        multiple = len(attempt_ids) > 1
        for index, result in enumerate(results, 1):
            if not isinstance(result, dict):
                raise ValueError("every result must be an object")
            allowed_result = {"worker", "report_ref", "dispatch_ref", "status", "reason"}
            unknown_result = sorted(set(result) - allowed_result)
            if unknown_result:
                raise ValueError("unsupported result fields: " + ", ".join(unknown_result))
            if multiple:
                if type(result.get("worker")) is not int:
                    raise ValueError("parallel results require the integer worker slot returned by Cortex")
                slot = int(result["worker"])
            else:
                if "worker" in result and result["worker"] != 1:
                    raise ValueError("the single active worker slot is 1")
                slot = 1
            if slot < 1 or slot > len(attempt_ids) or slot in slots:
                raise ValueError("worker slots must be unique members of the active wave")
            report_ref = str(result.get("report_ref") or "").strip()
            has_report = bool(report_ref)
            status_value = _v3_status(result.get("status"), has_report=has_report)
            if status_value == "passed":
                if not report_ref:
                    raise ValueError("successful results require report_ref from record_report")
                if str(result.get("dispatch_ref") or "").strip():
                    raise ValueError("successful results use report_ref only; do not supply dispatch_ref")
                _pre_recorded_report(task_dir, state, attempt_ids[slot - 1], report_ref)
                if str(result.get("reason") or "").strip():
                    raise ValueError("successful results must not include reason")
            else:
                if report_ref:
                    raise ValueError("non-success results must omit report_ref")
                if not str(result.get("reason") or "").strip():
                    raise ValueError("non-success results require reason")
                dispatch_ref = str(result.get("dispatch_ref") or "").strip()
                if not dispatch_ref:
                    raise ValueError(
                        "non-success results require the exact dispatch_ref returned for that worker; "
                        "this prevents a stale failed result from being applied to a replacement attempt"
                    )
                attempt = next(
                    (item for item in state.get("attempts", []) if item.get("attempt_id") == attempt_ids[slot - 1]),
                    None,
                )
                if not isinstance(attempt, dict) or dispatch_ref != str(attempt.get("dispatch_ref") or ""):
                    raise ValueError("non-success dispatch_ref does not match the exact active worker attempt")
            slots[slot] = result
        # Reserve the server-owned transaction only after all slots, reports,
        # statuses, and future-wave overrides pass validation.
        old_params, reserved_attempt_ids, _, request_digest, replay = _v3_continue_context(params, task_dir, state, task_ref)
        if replay is not None:
            return replay
        if reserved_attempt_ids != attempt_ids:
            raise ValueError("the active wave changed while continue was being validated; retry with the latest step")
        completions: list[dict[str, Any]] = []
        for slot, attempt_id in enumerate(attempt_ids, 1):
            result = slots[slot]
            report_ref = str(result.get("report_ref") or "").strip()
            status_value = _v3_status(
                result.get("status"),
                has_report=bool(report_ref),
            )
            completion = {
                "attempt_id": attempt_id,
                "host_observation_source": "unattested_parent_result",
                "status": status_value,
            }
            if status_value == "passed":
                completion["report_ref"] = report_ref
            else:
                completion["reason"] = str(result["reason"])
            completions.append(completion)
        old_params["completions"] = completions
        if future_waves is not None:
            old_params["future_waves"] = future_waves
            old_params["allow_rework"] = bool(params.get("rework", False))
        if params.get("reason") is not None:
            old_params["reason"] = params["reason"]
        old = orchestrate(old_params)
        response = _v3_response(
            old,
            task_ref,
            include_result=old.get("state") == "awaiting_plan_approval",
        )
        if old.get("ok"):
            _v3_store_continue(params, state["task_id"], request_digest, response)
        elif str(old.get("phase")) in {"preflight", "started", "validation"}:
            _v3_store_continue(params, state["task_id"], request_digest, response, clear_only=True)
        return response
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        try:
            resolved = _v3_resolve_task(params)
            if not isinstance(resolved, dict):
                _, state, _, _ = resolved
                root = ledger_root(params)
                with state_lock(root):
                    registry = _operation_registry(root)
                    registry["tasks"].setdefault(state["task_id"], {}).pop("inflight_continue", None)
                    _write_operation_registry(root, registry)
        except Exception:
            pass
        error = _v3_error("continue_validation_failed", exc)
        if resolved_task_ref:
            error["task_ref"] = resolved_task_ref
        return error


def _v3_question_management_payload(value: object) -> dict[str, Any]:
    """Normalize the compact coordinator question contract.

    The public path accepts only a durable ``question_ref`` plus optional
    localization. Caller-supplied lifecycle identity is rejected rather than
    guessed or allowed to override the selected task.
    """
    if not isinstance(value, dict):
        raise ValueError("question management requires payload with question_ref")
    payload = dict(value)
    forbidden_identity = sorted(
        set(payload) & {"task_id", "principal", "thread_id", "attempt_id", "profile"}
    )
    if forbidden_identity:
        raise ValueError(
            "question management owns lifecycle identity; remove "
            + ", ".join(forbidden_identity)
            + " and pass only question_ref plus optional localization"
        )
    if "question_id" in payload:
        raise ValueError("question management accepts question_ref only")
    question_ref = str(payload.pop("question_ref", "") or "").strip()
    if question_ref:
        payload["question_id"] = safe_id(question_ref)
    command = str(payload.get("command") or "ask").strip().lower()
    # A batch translation resumes the existing durable batch through the same
    # coordinator UI route; it is not a legacy single-question answer call.
    if command == "answer" and "canonical_answers" in payload:
        command = "ask"
    payload["command"] = command
    if command == "ask" and not payload.get("question_id") and not str(payload.get("question") or "").strip():
        raise ValueError("question ask requires the worker's exact question_ref")
    if command == "answer" and not payload.get("question_id"):
        raise ValueError("question answer requires question_ref")
    return payload


def _v3_question_response(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    status_value = str(result.get("status") or "").strip()
    if status_value == "answered":
        response["outcome"] = "question_answered"
        poll_action = "poll_batch" if result.get("batch_ref") else "poll"
        poll_ref = "batch_ref" if result.get("batch_ref") else "question_ref"
        response["next_action"] = (
            f"{COORDINATOR_LOCK} Resume the exact same native worker with followup_task. Tell it the durable answer "
            f"is recorded, require worker_question(action={poll_action}) with the same {poll_ref} and attempt, and do not "
            "spawn a replacement worker or advance the wave before its report is recorded."
        )
    elif status_value == "elicitation_unavailable":
        response["ok"] = False
        response["outcome"] = "host_question_unavailable"
        response["code"] = "host_question_unavailable"
        response["next_action"] = (
            f"{COORDINATOR_LOCK} Keep the durable question open and stop. Do not ask it through commentary, a final "
            "message, or a worker-local UI; retry only in a main-chat host that supports MCP elicitation."
        )
    elif status_value == "awaiting_translation":
        response["outcome"] = "awaiting_translation"
        translation_field = "canonical_answers" if result.get("batch_ref") else "answer plus answer_en"
        response["next_action"] = (
            f"{COORDINATOR_LOCK} Translate only result.answer_original free text into canonical English, preserve "
            f"result.answer_option_ids, then answer the same question_ref with {translation_field}. Do not resume "
            "the worker until Cortex records both representations."
        )
    elif status_value == "superseded":
        response["outcome"] = "batch_superseded"
        response["next_action"] = (
            f"{COORDINATOR_LOCK} Do not resume the worker from this superseded batch. Keep the durable task revision "
            "as the source of truth and wait for its current dispatch or question batch."
        )
    elif status_value in {"decline", "cancel", "invalid_answer", "pending_user_input"}:
        response["outcome"] = "awaiting_user"
        response["next_action"] = (
            f"{COORDINATOR_LOCK} Keep the same worker and question or batch open. Accepted batch steps are durable, "
            "so retrying resumes at the next unanswered item. Translate the durable English question into "
            "the task's original user language only through localized_question, localized_header, localized_options, "
            "and localized_custom_label, then retry the native question UI when the user is ready; do not replace the "
            "worker, alter the durable worker record, fabricate an answer, or advance the wave."
        )
    return response


def _v3_plan_approval_payload(value: object) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("plan_approval requires a payload object")
    payload = dict(value)
    localization_fields = {
        "localized_prompt", "localized_title", "localized_approve", "localized_cancel",
    }
    unknown = sorted(set(payload) - {"decision", "feedback", *localization_fields})
    if unknown:
        raise ValueError("unsupported plan_approval payload fields: " + ", ".join(unknown))
    raw = str(payload.get("decision") or "prompt").strip().lower().replace("-", "_").replace(" ", "_")
    decision = {
        "prompt": "prompt", "ask": "prompt", "review": "prompt",
        "approve": "approve", "approved": "approve", "accept": "approve",
        "revise": "revise", "changes": "revise", "request_changes": "revise",
    }.get(raw)
    if not decision:
        raise ValueError("plan_approval decision must be prompt, approve, or revise")
    feedback = str(payload.get("feedback") or "").strip()
    if decision == "prompt" and feedback:
        raise ValueError("plan_approval prompt does not accept feedback")
    if decision == "revise" and not feedback:
        raise ValueError("plan_approval revise requires non-empty feedback")
    if decision != "prompt" and any(str(payload.get(field) or "").strip() for field in localization_fields):
        raise ValueError("plan approval localization fields are accepted only with decision=prompt")
    normalized = {"decision": decision, **({"feedback": feedback} if feedback else {})}
    for field in localization_fields:
        if str(payload.get(field) or "").strip():
            normalized[field] = redact(str(payload[field]).strip(), 300)
    return normalized


PLAN_APPROVAL_TRANSLATIONS: dict[str, tuple[str, str, str, str]] = {
    "en": ("Approve the completed plan?", "Plan review", "Approve", "Cancel"),
    "ru": ("Утвердить завершённый план?", "Проверка плана", "Утвердить", "Отмена"),
    "uk": ("Затвердити завершений план?", "Перевірка плану", "Затвердити", "Скасувати"),
    "ro": ("Aprobați planul finalizat?", "Revizuirea planului", "Aprobă", "Anulează"),
    "de": ("Den fertigen Plan genehmigen?", "Planprüfung", "Genehmigen", "Abbrechen"),
    "fr": ("Approuver le plan finalisé ?", "Examen du plan", "Approuver", "Annuler"),
    "es": ("¿Aprobar el plan finalizado?", "Revisión del plan", "Aprobar", "Cancelar"),
    "it": ("Approvare il piano completato?", "Revisione del piano", "Approva", "Annulla"),
    "pt": ("Aprovar o plano concluído?", "Revisão do plano", "Aprovar", "Cancelar"),
    "pl": ("Zatwierdzić ukończony plan?", "Przegląd planu", "Zatwierdź", "Anuluj"),
    "zh": ("批准已完成的计划？", "计划审核", "批准", "取消"),
    "ja": ("完成した計画を承認しますか？", "計画の確認", "承認", "キャンセル"),
    "ko": ("완료된 계획을 승인하시겠습니까?", "계획 검토", "승인", "취소"),
    "el": ("Έγκριση του ολοκληρωμένου σχεδίου;", "Έλεγχος σχεδίου", "Έγκριση", "Ακύρωση"),
}


def _v3_plan_approval_copy(state: dict[str, Any], localization: dict[str, Any]) -> tuple[str, str, str, str]:
    language = str(state.get("user_language") or "en").lower().split("-", 1)[0]
    translated = PLAN_APPROVAL_TRANSLATIONS.get(language)
    supplied = tuple(
        str(localization.get(field) or "").strip()
        for field in ("localized_prompt", "localized_title", "localized_approve", "localized_cancel")
    )
    if all(supplied):
        return supplied
    if language != "en" and translated is None:
        raise ValueError(
            "non-English plan approval requires localized_prompt, localized_title, localized_approve, and localized_cancel"
        )
    if any(supplied):
        raise ValueError("plan approval localization requires all four localized fields")
    return translated or PLAN_APPROVAL_TRANSLATIONS["en"]


def _v3_prompt_plan_approval(
    state: dict[str, Any],
    task_ref: str,
    localization: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Open the native Approve/Cancel UI without mutating a cancelled plan."""
    approval = _plan_approval(state)
    if approval.get("policy") != "required":
        raise ValueError("this task does not require post-plan approval")
    if approval.get("status") != "awaiting_user":
        raise ValueError("there is no pending plan approval for this task")
    review = dict(approval.get("review") or {})
    prompt, title, approve_label, cancel_label = _v3_plan_approval_copy(state, localization)
    requested_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decision": {
                "type": "string",
                "title": title,
                "oneOf": [
                    {"const": "approve", "title": approve_label},
                    {"const": "cancel", "title": cancel_label},
                ],
            },
        },
        "required": ["decision"],
    }
    try:
        action, content, elicitation_id = _request_mcp_elicitation(
            prompt,
            requested_schema,
            thread_id=str(state.get("thread_id") or ""),
        )
    except RuntimeError as exc:
        return None, {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": False,
            "outcome": "host_plan_approval_unavailable",
            "code": "host_plan_approval_unavailable",
            "task_ref": task_ref,
            "dispatches": [],
            "plan_review": review,
            "diagnostics": [{"message": redact(str(exc), 1000)}],
            "next_action": (
                f"{COORDINATOR_LOCK} Keep the plan pending and stop. Retry only in a main-chat host that can render "
                "native MCP elicitation; do not infer approval."
            ),
        }
    selected = ""
    if action == "accept" and isinstance(content, dict):
        selected = str(content.get("decision") or "").strip().lower()
    if action != "accept" or selected == "cancel":
        return None, {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "awaiting_plan_approval",
            "task_ref": task_ref,
            "dispatches": [],
            "plan_review": review,
            "result": {"decision": "cancelled", "elicitation_id": elicitation_id},
            "output_policy": "silent",
            "allowed_visible_events": ["user_message"],
            "next_action": (
                f"{COORDINATOR_LOCK} Stop now and wait for the user's next message. Keep the plan pending; do not "
                "dispatch, revise, or send approval/cancellation commentary."
            ),
        }
    if selected != "approve":
        raise ValueError("plan approval UI returned an invalid decision")
    return {"decision": "approve"}, None


def _v3_follow_up_payload(value: object) -> dict[str, Any]:
    """Normalize a user-authored corrective task without reopening its source."""
    if not isinstance(value, dict):
        raise ValueError("follow_up requires payload with the exact corrective user_request")
    allowed = {
        "user_request", "requirements", "acceptance_criteria", "scope", "allowed_paths",
        "verification", "budget", "pause_conditions", "user_language", "language",
        "complexity", "replan_limit", "plan_approval", "report_refs", "task_ref",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unsupported follow_up payload fields: " + ", ".join(unknown))
    user_request = canonicalize_desktop_cortex_request(value.get("user_request"))
    if not user_request:
        raise ValueError("follow_up payload.user_request must preserve the exact corrective user request")
    report_refs = value.get("report_refs", [])
    if not isinstance(report_refs, list) or len(report_refs) > 32:
        raise ValueError("follow_up payload.report_refs must be an array of at most 32 source report refs")
    normalized_refs = [safe_id(str(item)) for item in report_refs]
    if not all(normalized_refs) or len(normalized_refs) != len(set(normalized_refs)):
        raise ValueError("follow_up payload.report_refs must contain unique non-empty source report refs")
    task = {key: item for key, item in value.items() if key not in {"report_refs", "task_ref"}}
    task["user_request"] = user_request
    return {"task": task, "report_refs": normalized_refs}


def _v3_follow_up_context(
    source_dir: Path,
    source_state: dict[str, Any],
    source_task: dict[str, Any],
    source_task_ref: str,
    requested_report_refs: list[str],
) -> dict[str, Any]:
    """Build only source-derived, Desktop-openable corrective-task context."""
    if source_state.get("status") != "completed":
        raise ValueError("follow_up requires a completed source task; use rework while the original task is still active")
    paths = report_bus_paths(source_dir)
    index = _report_index(paths, str(source_state["task_id"]))
    available = [safe_id(str(item.get("report_id") or "")) for item in index.get("reports", []) if isinstance(item, dict)]
    available = [item for item in available if item]
    selected = requested_report_refs or available[-16:]
    unknown = sorted(set(selected) - set(available))
    if unknown:
        raise ValueError("follow_up report_refs do not belong to the completed source task: " + ", ".join(unknown))
    report_paths = [str(report_markdown_path(source_dir, report_ref)) for report_ref in selected]
    handoff_paths = sorted(
        path for path in (source_dir / "handoffs").glob("*.json")
        if path.is_file() and not path.is_symlink() and not path.name.endswith("-manifest.json")
    )
    return {
        "schema": "cortex/follow-up/v1",
        "source_task_ref": source_task_ref,
        "source_task_id": source_state["task_id"],
        "source_task_directory": str(source_dir),
        "source_objective": redact(source_task.get("objective", ""), 1000),
        "source_handoff_path": str(handoff_paths[-1]) if handoff_paths else None,
        "source_report_refs": selected,
        "source_report_markdown_paths": report_paths,
        "created_at": now(),
    }


def _v3_active_steer(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    task_definition: dict[str, Any],
    task_ref: str,
) -> dict[str, Any]:
    """Amend an active task and resume addressable native workers in place."""
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    unknown = sorted(set(payload) - {"user_message", "user_language", "message_en", "canonical_en", "received_at"})
    if unknown:
        raise ValueError("unsupported steer payload fields: " + ", ".join(unknown))
    original = str(payload.get("user_message") or "").strip()
    language = normalize_user_language(
        payload.get("user_language") or task_definition.get("user_language"), original
    )
    canonical = str(payload.get("message_en") or payload.get("canonical_en") or "").strip()
    if not original:
        raise ValueError("steer payload.user_message is required")
    if not canonical:
        if str(language).lower().startswith("en"):
            canonical = original
        else:
            raise ValueError("non-English steer requires payload.message_en with the canonical English worker message")
    if state.get("status") not in {"active", "blocked"}:
        raise ValueError("steer applies only to an active Cortex task; completed tasks use follow_up")

    root = ledger_root(params)
    with state_lock(root):
        loaded = _v3_task_state(root, state["task_id"])
        if loaded is None:
            raise ValueError("active steer task is unavailable")
        task_dir, state, task_definition = loaded
        task_revision = db_append_task_revision(
            root,
            state["task_id"],
            source="user_steer",
            message_original=original,
            message_language=str(language),
            message_en=canonical,
        )
        revision_number = int(task_revision["task_revision"])
        current_gates = active_gates(state)
        active_attempts = [
            item for item in state.get("attempts", [])
            if item.get("gate") in current_gates
            and item.get("status") in {AWAITING_HOST_SPAWN, "running"}
            and not item.get("invalidated")
        ]
        impact = {
            "classification": "affects_active_gate" if active_attempts else "affects_future_gate",
            "earliest_affected_gate": current_gates[0] if current_gates else None,
            "affected_work_packages": [str(item.get("attempt_id")) for item in active_attempts],
            "new_work_packages": [],
            "obsolete_work_packages": [],
            "dependency_changes": [],
            "active_attempt_actions": [
                {"attempt_id": str(item.get("attempt_id")), "action": "resume_worker"}
                for item in active_attempts if (item.get("host_spawn") or {}).get("agent_id")
            ],
        }
        plan = _load_orchestrate_plan(task_dir, state)
        plan_revision = None
        if "plan" in current_gates:
            impact["classification"] = "requires_plan_revision"
            plan_revision = db_append_plan_revision(
                root, state["task_id"], task_revision=revision_number,
                impact=impact, plan=plan, status="active",
            )
        task_definition["task_revision"] = revision_number
        task_definition.setdefault("active_steers", []).append({
            "task_revision": revision_number,
            "message_original": original,
            "message_language": language,
            "message_en": canonical,
            "received_at": payload.get("received_at") or now(),
        })
        db_update_task_definition(root, task_definition)

        dispatches = []
        for attempt in active_attempts:
            attempt.setdefault("task_revision_started", revision_number - 1)
            attempt["latest_material_revision"] = revision_number
            host_spawn = attempt.get("host_spawn") or {}
            host_agent_id = str(host_spawn.get("agent_id") or "")
            if not host_agent_id:
                continue
            message = (
                f"Cortex active steer for task revision {revision_number}: {canonical}\n\n"
                f"Continue the same attempt {attempt['attempt_id']}. Reconcile this steer with completed work and "
                f"include exactly `Task revision reviewed: {revision_number}` in report.evidence. Do not spawn or "
                "create a new attempt."
            )
            durable = db_append_attempt_message(root, {
                "task_id": state["task_id"], "attempt_id": attempt["attempt_id"],
                "source": "user", "kind": "steer", "original_text": original,
                "original_language": str(language), "canonical_en": canonical,
                "task_revision": revision_number,
            })
            dispatches.append({
                "worker": len(dispatches) + 1,
                "dispatch_kind": "resume_worker",
                "dispatch_ref": str(attempt.get("dispatch_ref") or ""),
                "attempt_id": attempt["attempt_id"],
                "host_agent_id": host_agent_id,
                "host_task_name": str(host_spawn.get("task_name") or ""),
                "message_id": durable["message_id"],
                "call": "followup_task",
                "arguments": {"target": host_agent_id, "message": message},
            })
        state["task_revision"] = revision_number
        state.setdefault("task_revision_history", []).append({
            "task_revision": revision_number, "source": "user_steer", "impact": impact, "at": now(),
        })
        save_state(task_dir, task_dir / "state.sqlite", state, "active_steer", f"task revision {revision_number}")
    return {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "outcome": "ready_to_resume" if dispatches else "steer_recorded",
        "task_ref": task_ref,
        "task_revision": revision_number,
        "plan_revision": plan_revision["plan_revision"] if plan_revision else None,
        "impact": impact,
        "dispatches": dispatches,
        "next_action": (
            "Call every returned followup_task once with its exact arguments; do not spawn a replacement worker."
            if dispatches else
            "The steer is durable. Inspect once to recover an unstarted dispatch or continue the revised pipeline."
        ),
    }


def manage_orchestration(params: dict[str, Any]) -> dict[str, Any]:
    """Keep recovery and rare control-plane capabilities outside the normal flow."""
    resolved_task_ref = str(params.get("task_ref") or "").strip() or None
    try:
        select_project_root(params)
        allowed = {"project_root", "intent", "task_ref", "reason", "payload"}
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ValueError("unsupported management fields: " + ", ".join(unknown))
        intent_raw = str(params.get("intent") or "inspect").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "status": "inspect", "inspect": "inspect", "show": "inspect",
            "resume": "resume", "retry": "resume", "continue_blocked": "resume",
            "deactivate": "deactivate", "normal": "deactivate", "stop_session": "deactivate",
            "lane": "lane", "resource": "resource", "question": "question",
            "plan_approval": "plan_approval", "approve_plan": "plan_approval", "plan_review": "plan_approval",
            "follow_up": "follow_up", "followup": "follow_up", "correct": "follow_up", "corrective_task": "follow_up",
            "steer": "steer", "amend": "steer", "revise_active_task": "steer",
            "prune": "prune", "cleanup": "prune",
            "legacy": "legacy", "legacy_lifecycle": "legacy", "legacy_cleanup": "legacy",
            "maintenance": "maintenance", "health": "maintenance", "sqlite_health": "maintenance",
            "artifacts": "artifacts", "artifact": "artifacts", "documents": "artifacts",
        }
        intent = aliases.get(intent_raw)
        if not intent:
            suggestions = difflib.get_close_matches(intent_raw, sorted(aliases), n=3)
            raise ValueError("management intent is not recognized" + (f"; try {', '.join(suggestions)}" if suggestions else ""))
        if intent == "prune":
            return prune_orchestration_state(params)
        if intent == "legacy":
            # This explicit maintenance route intentionally avoids ledger
            # initialization and all current task/projection reads.
            return manage_legacy_lifecycle(params, select_project_root(params))
        if intent == "maintenance":
            # Health inspection must not initialize or migrate a missing
            # ledger. The controlled module validates the existing root and
            # permits writes only through explicit, confirmed actions.
            project = select_project_root(params)
            root = _contained_path(project, project / ".codex" / "cortex", "Cortex root")
            return manage_health_maintenance(root, params.get("payload"))
        # Models frequently keep source identity beside the corrective request
        # in the rare-operation payload. Accept that equivalent compact form
        # only for follow_up, then normalize it to the canonical top-level ref
        # before task selection; all other intents remain strict.
        if intent == "follow_up" and not str(params.get("task_ref") or "").strip():
            raw_payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
            nested_ref = str(raw_payload.get("task_ref") or "").strip()
            if nested_ref:
                params = {**params, "task_ref": nested_ref}
                resolved_task_ref = nested_ref
        resolved = _v3_resolve_task(
            params,
            include_completed=bool(str(params.get("task_ref") or "").strip()) and intent in {"inspect", "deactivate", "follow_up"},
        )
        if isinstance(resolved, dict):
            return resolved
        task_dir, state, task_definition, task_ref = resolved
        resolved_task_ref = task_ref
        if intent == "artifacts":
            return manage_task_artifacts(params, task_dir, state, task_ref)
        if intent == "steer":
            return _v3_active_steer(params, task_dir, state, task_definition, task_ref)
        if intent == "follow_up":
            follow_up = _v3_follow_up_payload(params.get("payload"))
            source_context = _v3_follow_up_context(
                task_dir,
                state,
                task_definition,
                task_ref,
                follow_up["report_refs"],
            )
            follow_up_task = dict(follow_up["task"])
            follow_up_task["user_language"] = task_definition.get("user_language") or state.get("user_language") or "en"
            started = start_orchestration({
                "project_root": params["project_root"],
                "task": follow_up_task,
                "_follow_up": source_context,
            })
            if not started.get("ok"):
                return started
            started["follow_up"] = {
                "source_task_ref": task_ref,
                "source_handoff_path": source_context["source_handoff_path"],
                "source_report_markdown_paths": source_context["source_report_markdown_paths"],
                "new_task_ref": started["task_ref"],
            }
            if started.get("replayed"):
                started["next_action"] = (
                    f"{COORDINATOR_LOCK} The linked corrective task_ref={started['task_ref']} already exists; this is an idempotent replay. "
                    "Do not create or dispatch it again. Do not modify or reopen the source task. "
                    "If the original dispatch response was lost, inspect the corrective task once and invoke only its still-awaiting dispatches, "
                    "then follow its normal plan approval, verification, and close flow."
                )
            else:
                started["next_action"] = (
                    f"{COORDINATOR_LOCK} A new corrective task was created for completed source task_ref={task_ref}. "
                    "Do not modify or reopen the source task. Execute only the returned dispatches for the new task, "
                    "then follow its normal plan approval, verification, and close flow."
                )
            return started
        common = {
            "project_root": params["project_root"],
            "principal": state.get("principal"),
            "thread_id": state.get("thread_id"),
            "task_id": state["task_id"],
        }
        if intent == "inspect":
            return _v3_response(_orchestrate_inspect(common), task_ref, include_result=True)
        normalized_payload = None
        if intent == "question":
            normalized_payload = _v3_question_management_payload(params.get("payload"))
        elif intent == "plan_approval":
            normalized_payload = _v3_plan_approval_payload(params.get("payload"))
            if normalized_payload["decision"] == "prompt":
                normalized_payload, prompt_response = _v3_prompt_plan_approval(state, task_ref, normalized_payload)
                if prompt_response is not None:
                    return prompt_response
        operation_context: dict[str, Any] = {}
        if intent == "question" and str((normalized_payload or {}).get("question_id") or "").startswith("batch-"):
            batch_id = str(normalized_payload["question_id"])
            batch = db_get_task_document(
                _task_document_root(task_dir, str(state["task_id"])),
                str(state["task_id"]),
                "question_batch:" + batch_id,
            )
            if isinstance(batch, dict):
                operation_context["batch_progress"] = {
                    "status": batch.get("status"),
                    "answered_keys": sorted((batch.get("answers") or {}).keys()),
                    "translation_required_for": sorted(batch.get("translation_required_for") or []),
                }
        submission_id = safe_id("orchestration-manage-" + intent + "-" + digest_text(state["task_id"] + ":" + str(state.get("revision")) + ":" + json.dumps({**params, "payload": normalized_payload if normalized_payload is not None else params.get("payload"), **operation_context}, sort_keys=True, default=str))[:16])
        if intent in {"resume", "deactivate"}:
            old = orchestrate({
                **common,
                "operation": intent,
                "submission_id": submission_id,
                "reason": params.get("reason"),
            })
        else:
            payload = normalized_payload if normalized_payload is not None else params.get("payload")
            if not isinstance(payload, dict):
                raise ValueError(f"{intent} management requires payload")
            old = orchestrate({
                **common,
                "operation": intent,
                "submission_id": submission_id,
                "payload": payload,
            })
        response = _v3_response(old, task_ref, include_result=True)
        if intent == "question":
            return _v3_question_response(response)
        if intent == "plan_approval" and (old.get("result") or {}).get("decision") == "approved":
            response["approval_message"] = "Plan approved."
            response["next_action"] = (
                f"{COORDINATOR_LOCK} Tell the user in their language that the plan was approved, then execute every "
                "returned dispatch exactly once and continue the normal Cortex wave workflow."
            )
        return response
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        error = _v3_error("management_failed", exc)
        if resolved_task_ref:
            error["task_ref"] = resolved_task_ref
        return error


# ``sync-cortex.sh`` validates the server through ``importlib`` without
# pre-registering that transient module.  Legacy runtime adapters still rely
# on the public facade, so provide a snapshot only for that validation path.
# Extracted dependency-neutral slices use the explicit binding below instead.
if __name__ != "cortex" and __name__ not in sys.modules:
    _importlib_facade = types.ModuleType("cortex")
    _importlib_facade.__dict__.update(globals())
    sys.modules["cortex"] = _importlib_facade


# The executable is the sole composition root.  Runtime slices receive this
# explicit binding rather than importing the facade and creating a reverse
# dependency while the importlib validation path is still initializing.
from cortex_runtime.core.runtime_bindings import bind_runtime_dependencies
bind_runtime_dependencies(globals())


from cortex_runtime.briefings import (
    codebase_memory_project_key_from_root,
    dispatch_briefing_review_marker,
    host_spawn_bootstrap,
    host_spawn_prompt,
)
if __name__ != "cortex" and "_importlib_facade" in globals():
    _importlib_facade.__dict__.update(globals())
from cortex_runtime.delegation_service import record_delegation as _record_delegation_service
from cortex_runtime.context_handoff import _context_handoff as _context_handoff_service
from cortex_runtime.artifact_transport import manage_task_artifacts
from cortex_runtime.health_maintenance import manage_health_maintenance
from cortex_runtime.legacy_lifecycle import manage_legacy_lifecycle
from cortex_runtime.questions import (
    _localized_question_view,
    _normalize_question_answer,
    _question_answer_from_content,
    _question_form_schema,
    _question_record_for_main,
    _question_record_view,
    _request_mcp_elicitation,
    answer_worker_question,
    cortex_question,
    get_worker_question_updates,
    list_worker_questions,
    publish_worker_question,
    worker_question,
)
from cortex_runtime.reports import (
    get_report_template,
    get_delegation_reports,
    list_task_reports,
    publish_worker_report,
    read_dispatch_briefing,
    read_worker_report,
    record_report,
    validate_report_draft,
)


PIPELINE_OPERATION_SCHEMA = {"type": "object", "properties": {"op": {"type": "string", "enum": ["add", "remove", "move", "replace", "rework"]}, "gate": {"type": "string"}, "before": {"type": "string"}, "after": {"type": "string"}, "index": {"type": "integer"}, "with": {"type": "array", "items": {"type": "string"}}}, "required": ["op", "gate"]}
QUESTION_OPTION_SCHEMA = {
    "anyOf": [
        {"type": "string", "minLength": 1},
        {"type": "object", "additionalProperties": False, "properties": {"option_id": {"type": "string", "minLength": 1}, "label": {"type": "string", "minLength": 1}, "label_en": {"type": "string", "minLength": 1}, "label_localized": {"type": "string", "minLength": 1}, "description": {"type": "string"}}, "anyOf": [{"required": ["label"]}, {"required": ["label_en"]}]},
    ]
}
QUESTION_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "task_id": {"type": "string", "minLength": 1},
        "principal": {"type": "string", "minLength": 1},
        "thread_id": {"type": "string"},
        "turn_id": {"type": "string"},
        "question_id": {"type": "string", "description": "Existing worker question to surface and answer in the main chat."},
        "user_language": {"type": "string", "description": "Language requested by the user for the main-chat projection."},
        "localized_question": {"type": "string", "description": "Main-coordinator display translation into the task's original user language; durable worker content remains English and unchanged."},
        "localized_header": {"type": "string"},
        "localized_options": {"type": "array", "maxItems": 32, "items": QUESTION_OPTION_SCHEMA},
        "localized_custom_label": {"type": "string"},
        "localized_questions": {"type": "array", "maxItems": 32, "items": {"type": "object"}, "description": "Batch-only localized form projection. Each item identifies its stable question_key and may change titles/options display labels only."},
        "localized_batch": {"type": "object", "description": "Batch-only alias containing localized_questions under questions."},
        "answer_submission_id": {"type": "string", "description": "Stable id for an answer replay."},
        "canonical_answer": {"type": "string", "description": "Coordinator-supplied English translation of localized free text."},
        "canonical_answers": {"type": "object", "description": "Batch-only map of localized free-text question_key to its canonical English translation. Option-only answers derive English from stable option_id and must not be translated."},
        "translated_by": {"type": "string", "description": "Audit label for the coordinator that supplied batch free-text translations."},
        "attempt_id": {"type": "string", "description": "Worker attempt. Supplying it routes the question to the coordinator instead of opening a worker UI."},
        "submission_id": {"type": "string", "description": "Stable worker-question submission id."},
        "question": {"type": "string", "minLength": 1},
        "header": {"type": "string"},
        "options": {"type": "array", "maxItems": 32, "items": QUESTION_OPTION_SCHEMA},
        "multiple": {"type": "boolean", "default": False, "description": "Render options as checkboxes when true; otherwise render a single-select control."},
        "custom_label": {"type": "string", "description": "Label for the always-present final free-form response field."},
        "context": {},
        "blocking": {"type": "boolean", "default": True},
        "interactive": {"type": "boolean", "default": True},
    },
    "required": ["task_id", "principal"],
}
ORCHESTRATE_TASK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "task_id": {"type": "string", "minLength": 1, "description": "Required durable lowercase task identifier."},
        "objective": {"type": "string", "minLength": 1},
        "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "scope": {"type": "array", "items": {"type": "string"}},
        "allowed_paths": {"type": "array", "items": {"type": "string"}},
        "verification": {"type": "array", "items": {"type": "string"}},
        "budget": {"type": "string"},
        "pause_conditions": {"type": "array", "items": {"type": "string"}},
        "plan_approval": {"type": "string", "enum": ["auto", "required"]},
        "user_language": {"type": "string"},
        "replan_limit": {"type": "integer", "minimum": 0},
    },
    "required": ["task_id", "objective", "complexity"],
}
ORCHESTRATE_DELEGATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "gate": {"type": "string", "minLength": 1},
        "agent": {"type": "string", "enum": sorted(AGENTS)},
        "task_kind": {"type": "string"},
        "risk": {"type": "string", "enum": ["low", "moderate", "high", "critical"]},
        "requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS)},
        "user_requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS)},
        "configured_default_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS)},
        "available_models": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "available_thread_models": {"type": "array", "items": {"type": "string"}},
        "dispatch_mode": {"type": "string", "enum": ["hidden_subagent", "visible_thread"]},
        "thread_environment": {"type": "string", "enum": ["local", "worktree"]},
        "requested_reasoning_effort": {"type": "string"},
        "retry": {"type": "integer", "minimum": 0},
        "parallel": {"type": "boolean"},
        "objective": {"type": "string"},
        "ownership": {"type": "string", "minLength": 1},
        "context_files": {"type": "array", "items": {"type": "string"}},
        "context_report_ids": {"type": "array", "items": {"type": "string"}},
        "context_gates": {"type": "array", "items": {"type": "string"}},
        "allowed_paths": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "acceptance_criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "verification": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "selection_reason": {"type": "string", "minLength": 1},
    },
    "required": ["gate"],
}
ORCHESTRATE_WAVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "wave_id": {"type": "string", "minLength": 1, "description": "Stable wave identifier; do not use id."},
        "delegations": {"type": "array", "minItems": 1, "maxItems": 32, "items": ORCHESTRATE_DELEGATION_SCHEMA},
    },
    "required": ["wave_id", "delegations"],
}
ORCHESTRATE_HOST_CAPABILITIES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "spawn_agent_models": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "available_models": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "spawn_agent_default_model": {"type": "string", "enum": sorted(SUPPORTED_MODELS)},
        "create_thread_models": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "available_thread_models": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "required": ["spawn_agent_models"],
}
ORCHESTRATE_COMPLETION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "attempt_id": {"type": "string", "minLength": 1},
        "host_tool": {"type": "string", "enum": ["spawn_agent", "create_thread"]},
        "host_agent_id": {"type": "string", "minLength": 1},
        "host_task_name": {"type": "string", "minLength": 1},
        "host_model": {"type": "string", "minLength": 1},
        "host_reasoning_effort": {"type": "string", "minLength": 1},
        "status": {"type": "string", "enum": sorted(TERMINAL_ATTEMPT_STATUSES)},
        "reason": {"type": "string"},
        "report_ref": {"type": "string", "minLength": 1},
    },
    "required": ["attempt_id", "host_tool", "host_agent_id", "host_task_name", "host_model", "host_reasoning_effort", "status"],
}
ORCHESTRATE_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "operation": {"type": "string", "enum": sorted(ORCHESTRATE_OPERATIONS)},
        "submission_id": {"type": "string", "description": "Required for every mutating operation; identical retries are replayed exactly."},
        "project_root": {"type": "string", "minLength": 1, "description": "Absolute project workspace. Cortex state remains below project_root/.codex/cortex."},
        "principal": {"type": "string", "minLength": 1},
        "thread_id": {"type": "string"},
        "task_id": {"type": "string"},
        "wave_id": {"type": "string"},
        "task": {**ORCHESTRATE_TASK_SCHEMA, "description": "Start-only immutable task contract."},
        "waves": {"type": "array", "minItems": 1, "items": ORCHESTRATE_WAVE_SCHEMA, "description": "Start-only full ordered execution-wave plan."},
        "host_capabilities": {**ORCHESTRATE_HOST_CAPABILITIES_SCHEMA, "description": "Start-only native model catalogs plus the optional confirmed spawn_agent_default_model."},
        "completions": {"type": "array", "minItems": 1, "items": ORCHESTRATE_COMPLETION_SCHEMA, "description": "Advance-only host completions with report_ref values created by record_report."},
        "gate_outcomes": {"type": "object", "description": "Optional explicit gate outcome overrides for advance."},
        "future_waves": {"type": "array", "items": {"type": "object"}, "description": "Optional replacement for not-yet-started waves during advance."},
        "allow_rework": {"type": "boolean", "default": False},
        "reason": {"type": "string"},
        "payload": {"type": "object", "description": "Operation-specific lane, resource, or question command payload."},
    },
    "required": ["operation", "project_root"],
}
PUBLIC_SCHEMA_REGISTRY = build_public_schemas(
    agents=AGENTS,
    report_fields=REPORT_FIELDS,
    max_report_items=MAX_REPORT_ITEMS,
    max_work_packages=MAX_WORK_PACKAGES,
    max_microtasks_per_package=MAX_MICROTASKS_PER_PACKAGE,
    max_discovery_domains=MAX_DISCOVERY_DOMAINS,
    question_option_schema=QUESTION_OPTION_SCHEMA,
)
V3_REPORT_SCHEMA = PUBLIC_SCHEMA_REGISTRY["v3_report"]
V3_PLANNING_SCHEMA = PUBLIC_SCHEMA_REGISTRY["v3_planning"]
V3_SCOPING_SCHEMA = PUBLIC_SCHEMA_REGISTRY["v3_scoping"]
V3_WORKER_SCHEMA = PUBLIC_SCHEMA_REGISTRY["v3_worker"]
V3_WAVE_SCHEMA = PUBLIC_SCHEMA_REGISTRY["v3_wave"]
START_ORCHESTRATION_SCHEMA = PUBLIC_SCHEMA_REGISTRY["start_orchestration"]
CONTINUE_ORCHESTRATION_SCHEMA = PUBLIC_SCHEMA_REGISTRY["continue_orchestration"]
MANAGE_ORCHESTRATION_SCHEMA = PUBLIC_SCHEMA_REGISTRY["manage_orchestration"]
WORKER_QUESTION_SCHEMA = PUBLIC_SCHEMA_REGISTRY["worker_question"]
WORKER_GET_REPORT_TEMPLATE_SCHEMA = PUBLIC_SCHEMA_REGISTRY["get_report_template"]
WORKER_VALIDATE_REPORT_DRAFT_SCHEMA = PUBLIC_SCHEMA_REGISTRY["validate_report_draft"]
WORKER_RECORD_REPORT_SCHEMA = PUBLIC_SCHEMA_REGISTRY["record_report"]
READ_DISPATCH_BRIEFING_SCHEMA = PUBLIC_SCHEMA_REGISTRY["read_dispatch_briefing"]
READ_WORKER_REPORT_SCHEMA = PUBLIC_SCHEMA_REGISTRY["read_worker_report"]


TOOLS = {
    "start_orchestration": (start_orchestration, START_ORCHESTRATION_SCHEMA),
    "continue_orchestration": (continue_orchestration, CONTINUE_ORCHESTRATION_SCHEMA),
    "manage_orchestration": (manage_orchestration, MANAGE_ORCHESTRATION_SCHEMA),
    "orchestrate": (orchestrate, ORCHESTRATE_TOOL_SCHEMA),
    "activate_orchestration": (activate_orchestration, {"type": "object", "additionalProperties": False, "properties": {"user_command": {"type": "string", "const": "/cortex"}, "thread_id": {"type": "string", "minLength": 1}, "principal": {"type": "string", "minLength": 1}}, "required": ["user_command", "thread_id", "principal"]}),
    "deactivate_orchestration": (deactivate_orchestration, {"type": "object", "additionalProperties": False, "properties": {"user_command": {"type": "string", "const": "/normal"}, "thread_id": {"type": "string"}, "principal": {"type": "string"}}, "required": ["user_command"]}),
    "get_activation_status": (activation_status, {"type": "object", "properties": {"thread_id": {"type": "string"}, "principal": {"type": "string"}}, "required": []}),
    "classify_task": (classify_task, {"type": "object", "properties": {"complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "requirements": {"type": "array", "items": {"type": "string"}}, "pipeline": {"type": "array", "items": {"type": "string"}, "description": "Full gate proposal selected by the orchestrator; Cortex appends only documentation and close when missing."}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "Ordered executable waves selected by the orchestrator; gates in one wave may run concurrently."}, "thread_id": {"type": "string"}, "principal": {"type": "string"}}, "required": ["complexity"]}),
    "init_task": (init_task, {"type": "object", "properties": {"task_id": {"type": "string"}, "objective": {"type": "string"}, "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "classification_id": {"type": "string"}, "requirements": {"type": "array", "items": {"type": "string"}}, "acceptance_criteria": {"type": "array", "items": {"type": "string"}}, "scope": {"type": "array", "items": {"type": "string"}}, "allowed_paths": {"type": "array", "items": {"type": "string"}}, "verification": {"type": "array", "items": {"type": "string"}}, "budget": {"type": "string"}, "pause_conditions": {"type": "array", "items": {"type": "string"}}, "plan_approval": {"type": "string", "enum": ["auto", "required"]}, "pipeline": {"type": "array", "items": {"type": "string"}}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}, "thread_id": {"type": "string"}, "principal": {"type": "string"}, "user_language": {"type": "string"}, "replan_limit": {"type": "integer", "minimum": 0}}, "required": ["task_id", "objective", "classification_id"]}),
    "get_task_status": (status, {"type": "object", "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}}, "required": ["task_id", "principal"]}),
    "resolve_dispatch_route": (resolve_dispatch_route, {"type": "object", "additionalProperties": False, "properties": {"agent": {"type": "string", "enum": sorted(AGENTS)}, "task_kind": {"type": "string"}, "risk": {"type": "string", "enum": ["low", "moderate", "high", "critical"]}, "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS)}, "user_requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Exact model explicitly requested by the user; required for non-security Sol."}, "configured_default_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Host-configured agents.default_subagent_model used when native model is omitted."}, "available_models": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}, "description": "Exact model identifiers currently accepted by the native spawn_agent host tool."}, "requested_reasoning_effort": {"type": "string"}}, "required": ["agent", "task_kind", "risk"]}),
    "record_delegation": (record_delegation, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "status_receipt": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "gate": {"type": "string"}, "agent": {"type": "string", "enum": sorted(AGENTS)}, "task_kind": {"type": "string"}, "risk": {"type": "string", "enum": ["low", "moderate", "high", "critical"]}, "requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS)}, "user_requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Exact model explicitly requested by the user; required for non-security Sol."}, "configured_default_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Confirmed host agents.default_subagent_model used when native model is omitted."}, "available_models": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}, "description": "Exact model identifiers currently accepted by the native spawn_agent host tool."}, "dispatch_mode": {"type": "string", "enum": ["hidden_subagent", "visible_thread"], "description": "visible_thread is an explicit user-owned task request and is never an automatic fallback."}, "luna_fallback": {"type": "string", "enum": ["terra"], "description": "Unavailable Luna hidden dispatches fall back to an explicit hidden Terra spawn_agent request."}, "available_thread_models": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}, "description": "Exact model identifiers currently accepted by native create_thread; required only for an explicit visible_thread dispatch."}, "thread_environment": {"type": "string", "enum": ["local", "worktree"], "default": "local", "description": "Workspace for an explicitly requested visible_thread."}, "requested_reasoning_effort": {"type": "string"}, "retry": {"type": "integer"}, "parallel": {"type": "boolean"}, "objective": {"type": "string"}, "ownership": {"type": "string", "minLength": 1}, "context_files": {"type": "array", "items": {"type": "string"}}, "context_report_ids": {"type": "array", "items": {"type": "string"}}, "allowed_paths": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}, "acceptance_criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}, "verification": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}}, "required": ["task_id", "gate", "agent", "task_kind", "risk", "objective", "ownership", "allowed_paths", "acceptance_criteria", "verification"]}),
    "prepare_delegation": (prepare_delegation, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "delegation": {"type": "object"}}, "required": ["task_id", "principal", "delegation"]}),
    "prepare_delegations": (prepare_delegations, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "delegations": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "object"}}}, "required": ["task_id", "principal", "delegations"]}),
    "confirm_host_spawn": (confirm_host_spawn, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "host_tool": {"type": "string", "enum": ["spawn_agent", "create_thread"]}, "host_agent_id": {"type": "string", "minLength": 1, "description": "Native child id; for create_thread pass the returned threadId here."}, "host_task_name": {"type": "string", "minLength": 1}, "host_model": {"type": "string"}, "host_reasoning_effort": {"type": "string"}}, "required": ["task_id", "expected_revision", "attempt_id", "host_agent_id", "host_task_name"]}),
    "finalize_attempt": (finalize_attempt, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "status": {"type": "string", "enum": sorted(TERMINAL_ATTEMPT_STATUSES)}, "reason": {"type": "string"}}, "required": ["task_id", "expected_revision", "attempt_id", "status"]}),
    "complete_attempt": (complete_attempt, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "host_tool": {"type": "string", "enum": ["spawn_agent", "create_thread"]}, "host_agent_id": {"type": "string"}, "host_task_name": {"type": "string"}, "host_model": {"type": "string"}, "host_reasoning_effort": {"type": "string"}, "status": {"type": "string", "enum": sorted(TERMINAL_ATTEMPT_STATUSES)}, "reason": {"type": "string"}, "submission_id": {"type": "string"}, "report": {"type": "object"}}, "required": ["task_id", "principal", "attempt_id"]}),
    "record_report": (record_report, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string", "description": "Optional when the worker identity maps to exactly one active attempt; Cortex infers it."}, "submission_id": {"type": "string", "description": "Optional; Cortex derives a deterministic id from the attempt and report digest."}, "report": {"type": "object", "additionalProperties": False, "properties": {"summary": {"type": "string"}, "findings": {"type": "array"}, "questions": {"type": "array"}, "changed_files": {"type": "array", "items": {"type": "string"}}, "tests": {"type": "array"}, "evidence": {"type": "array"}, "uncertainty": {"type": "array"}}, "required": list(REPORT_FIELDS)}}, "required": ["task_id", "principal", "report"]}),
    "cortex.question": (cortex_question, QUESTION_TOOL_SCHEMA),
    "publish_worker_question": (publish_worker_question, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "submission_id": {"type": "string"}, "question": {"type": "string", "minLength": 1}, "header": {"type": "string"}, "options": {"type": "array", "maxItems": 32, "items": QUESTION_OPTION_SCHEMA}, "multiple": {"type": "boolean"}, "custom_label": {"type": "string"}, "context": {}, "blocking": {"type": "boolean"}}, "required": ["task_id", "principal", "attempt_id", "submission_id", "question"]}),
    "list_worker_questions": (list_worker_questions, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "status": {"type": "string", "enum": ["open", "answered"]}}, "required": ["task_id", "principal"]}),
    "answer_worker_question": (answer_worker_question, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "question_id": {"type": "string"}, "submission_id": {"type": "string"}, "answer": {"type": "string", "minLength": 1}, "resume_context": {}}, "required": ["task_id", "principal", "question_id", "submission_id", "answer", "resume_context"]}),
    "get_worker_question_updates": (get_worker_question_updates, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "after_sequence": {"type": "integer", "minimum": 0}}, "required": ["task_id", "principal", "attempt_id"]}),
    "list_task_reports": (list_task_reports, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}}, "required": ["task_id", "principal"]}),
    "get_delegation_reports": (get_delegation_reports, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "report_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["task_id", "principal", "attempt_id", "report_ids"]}),
    "reconcile_report_bus": (reconcile_report_bus, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}}, "required": ["task_id", "principal"]}),
    "close_audit": (close_audit, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}}, "required": ["task_id", "principal"]}),
    "record_evidence": (record_evidence, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "gate": {"type": "string"}, "attempt_id": {"type": "string"}, "report_receipt": {"type": "string"}, "kind": {"type": "string"}, "summary": {"type": "string"}, "digest": {"type": "string"}, "command": {"type": "string"}, "exit_code": {"type": "integer"}, "decision": {"type": "string", "enum": ["updated", "not_applicable"]}, "justification": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}}, "required": ["task_id", "expected_revision", "gate", "summary"]}),
    "execute_verification_command": (execute_verification, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "gate": {"type": "string"}, "attempt_id": {"type": "string"}, "report_receipt": {"type": "string"}, "summary": {"type": "string"}, "verification_id": {"type": "string", "enum": sorted(VERIFICATION_COMMANDS)}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120}, "paths": {"type": "array", "items": {"type": "string"}}}, "required": ["task_id", "expected_revision", "gate", "summary", "verification_id"]}),
    "record_gate_outcome": (record_gate, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "gate": {"type": "string"}, "outcome": {"type": "string", "enum": ["passed", "failed", "blocked", "skipped"]}, "summary": {"type": "string"}, "skip_reason": {"type": "string"}, "signals": {"type": "array", "items": {"type": "string"}}, "pipeline_reason": {"type": "string"}, "pipeline_operations": {"type": "array", "items": PIPELINE_OPERATION_SCHEMA}, "allow_rework": {"type": "boolean"}}, "required": ["task_id", "expected_revision", "gate", "outcome"]}),
    "commit_gate": (commit_gate, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "gate": {"type": "string"}, "mode": {"type": "string", "enum": ["verification", "documentation"]}, "attempt_id": {"type": "string"}, "report_receipt": {"type": "string"}, "summary": {"type": "string"}, "verification_id": {"type": "string", "enum": sorted(VERIFICATION_COMMANDS)}, "timeout_seconds": {"type": "integer"}, "decision": {"type": "string", "enum": ["updated", "not_applicable"]}, "justification": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "outcome": {"type": "string", "enum": ["passed", "failed", "blocked", "skipped"]}}, "required": ["task_id", "principal", "gate", "summary"]}),
    "resume_task": (resume_task, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "reason": {"type": "string"}}, "required": ["task_id", "expected_revision"]}),
    "update_pipeline": (update_pipeline, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "pipeline": {"type": "array", "items": {"type": "string"}}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}, "operations": {"type": "array", "items": PIPELINE_OPERATION_SCHEMA}, "signals": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}, "allow_rework": {"type": "boolean"}}, "required": ["task_id", "expected_revision"]}),
    "reassess_pipeline": (reassess_pipeline, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "signals": {"type": "array", "items": {"type": "string"}}, "pipeline": {"type": "array", "items": {"type": "string"}, "description": "Full replacement selected by the orchestrator; documentation and close are enforced."}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "Executable waves selected by the orchestrator."}, "intent": {"type": "string", "enum": ["add_specialist", "resequence", "rework_gate", "stop"]}, "decision": {"type": "string", "enum": ["unchanged", "updated", "stop"]}, "gate": {"type": "string"}, "reason": {"type": "string"}, "allow_rework": {"type": "boolean"}, "apply": {"type": "boolean"}}, "required": ["task_id", "expected_revision", "signals"]}),
    "acquire_lock": (acquire_lock, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "path": {"type": "string"}, "owner": {"type": "string"}, "gate": {"type": "string"}, "expires_at": {"type": "string"}, "advisory": {"type": "boolean"}}, "required": ["task_id", "expected_revision", "path", "owner"]}),
    "release_lock": (release_lock, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "path": {"type": "string"}, "owner": {"type": "string"}}, "required": ["task_id", "expected_revision", "path", "owner"]}),
    "create_handoff": (handoff, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "name": {"type": "string"}, "completed": {"type": "array", "items": {"type": "string"}}, "files": {"type": "array", "items": {"type": "string"}}, "decisions": {"type": "array", "items": {"type": "string"}}, "risks": {"type": "array", "items": {"type": "string"}}, "next_action": {"type": "string"}}, "required": ["task_id", "expected_revision"]}),
    "claim_resource": (claim_resource, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "path": {"type": "string"}, "owner": {"type": "string"}, "gate": {"type": "string"}, "expires_at": {"type": "string"}}, "required": ["task_id", "expected_revision", "path", "owner"]}),
    "release_resource": (release_resource, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "path": {"type": "string"}, "owner": {"type": "string"}}, "required": ["task_id", "expected_revision", "path", "owner"]}),
    "create_lane": (create_lane, {"type": "object", "properties": {"lane_id": {"type": "string"}, "principal": {"type": "string"}, "mode": {"type": "string", "enum": ["ephemeral", "persistent"]}, "purpose": {"type": "string"}, "declarations": {"type": "array"}}, "required": ["lane_id"]}),
    "get_lane_status": (lane_status, {"type": "object", "properties": {"lane_id": {"type": "string"}, "principal": {"type": "string"}}, "required": ["lane_id", "principal"]}),
    "claim_lane": (claim_lane, {"type": "object", "properties": {"lane_id": {"type": "string"}, "principal": {"type": "string"}, "run_id": {"type": "string"}, "expires_at": {"type": "string"}, "reclaim": {"type": "boolean"}}, "required": ["lane_id", "expires_at"]}),
    "release_lane": (release_lane, {"type": "object", "properties": {"lane_id": {"type": "string"}, "principal": {"type": "string"}, "run_id": {"type": "string"}}, "required": ["lane_id"]}),
    "retire_lane": (retire_lane, {"type": "object", "properties": {"lane_id": {"type": "string"}, "principal": {"type": "string"}, "clean": {"type": "boolean"}}, "required": ["lane_id", "clean"]}),
    "bind_task_lane": (bind_task_lane, {"type": "object", "properties": {"task_id": {"type": "string"}, "lane_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}}, "required": ["task_id", "lane_id", "expected_revision"]}),
    "claim_lane_resource": (claim_lane_resource, {"type": "object", "properties": {"lane_id": {"type": "string"}, "principal": {"type": "string"}, "path": {"type": "string"}, "owner": {"type": "string"}, "kind": {"type": "string"}, "expires_at": {"type": "string"}}, "required": ["lane_id", "path", "owner", "expires_at"]}),
    "release_lane_resource": (release_lane_resource, {"type": "object", "properties": {"lane_id": {"type": "string"}, "principal": {"type": "string"}, "path": {"type": "string"}, "owner": {"type": "string"}}, "required": ["lane_id", "path", "owner"]}),
    "materialize_lane": (materialize_lane, {"type": "object", "properties": {"lane_id": {"type": "string"}, "principal": {"type": "string"}, "run_id": {"type": "string"}, "confirm": {"type": "boolean"}}, "required": ["lane_id", "confirm"]}),
    "reconcile_lane": (reconcile_lane, {"type": "object", "properties": {"lane_id": {"type": "string"}, "principal": {"type": "string"}, "run_id": {"type": "string"}}, "required": ["lane_id"]}),
}

AUTHORIZED_TOOLS = configure_internal_schemas(TOOLS)
PUBLIC_TOOLS = build_public_tools(
    TOOLS,
    worker_question=worker_question,
    worker_question_schema=WORKER_QUESTION_SCHEMA,
    get_report_template=get_report_template,
    get_report_template_schema=WORKER_GET_REPORT_TEMPLATE_SCHEMA,
    validate_report_draft=validate_report_draft,
    validate_report_draft_schema=WORKER_VALIDATE_REPORT_DRAFT_SCHEMA,
    record_report=publish_worker_report,
    record_report_schema=WORKER_RECORD_REPORT_SCHEMA,
    read_dispatch_briefing=read_dispatch_briefing,
    read_dispatch_briefing_schema=READ_DISPATCH_BRIEFING_SCHEMA,
    read_worker_report=read_worker_report,
    read_worker_report_schema=READ_WORKER_REPORT_SCHEMA,
)


def _set_mcp_openai_form(value: bool) -> None:
    global MCP_OPENAI_FORM
    MCP_OPENAI_FORM = value


def main() -> None:
    """Keep the executable facade thin; transport lives in cortex_runtime.mcp_api."""
    # Load the complete runtime package before accepting requests. Installed
    # cache directories may be renamed during plugin replacement while this
    # already-running process still serves a host session.
    import cortex_runtime.orchestration_engine  # noqa: F401

    serve_stdio(
        public_tools=PUBLIC_TOOLS,
        internal_handlers=TOOLS,
        server_version=SERVER_VERSION,
        instructions=MCP_SERVER_INSTRUCTIONS,
        set_openai_form=_set_mcp_openai_form,
        log_tool_error=log_tool_error,
    )


if __name__ == "__main__":
    main()
