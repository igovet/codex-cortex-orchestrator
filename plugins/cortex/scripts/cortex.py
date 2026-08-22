#!/usr/bin/env python3
"""Local stdio MCP server for a durable, evidence-driven orchestration ledger."""
from __future__ import annotations
import contextlib
import difflib
import errno
import fnmatch
import html
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
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
    """Write one JSON-RPC response."""
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
    DEFAULT_MCP_AUDIENCE,
    MCP_AUDIENCES,
    PUBLIC_TOOL_DESCRIPTIONS,
    build_public_schemas,
    configure_internal_schemas,
    public_tools as build_public_tools,
    public_tools_for_audience,
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
    list_worker_sessions as db_list_worker_sessions,
    put_worker_session as db_put_worker_session,
    reconcile_terminal_worker_session as db_reconcile_terminal_worker_session,
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
from cortex_runtime.revision_impact import classify_revision_impact
from cortex_runtime.communication import (
    public_risks,
    quality_checks,
    render,
    render_lifecycle,
    render_plan,
    select_profile as select_communication_profile,
)
from cortex_runtime.governance import (
    GovernanceError,
    manage_governance as manage_governance_service,
    resolve_governance,
)
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback; atomic replace still applies.
    fcntl = None
SCHEMA = "cortex/v8"
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
# This is intentionally a host process setting, not a public Cortex tool
# argument.  It selects the parent directory for per-project private stores;
# it never receives a workspace-controlled ``project_root/.codex`` value.
HOST_CONTROL_STORE_ENV = "CORTEX_HOST_STATE_DIR"
HOST_CONTROL_STORE_SCHEMA = "cortex/host-control-store/v1"
PLUGIN_ROOT = PROFILE_CONTRACT_PATH.parent
PLUGIN_MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
# Cortex captures a complete content-addressed manifest before it creates a
# task. These are host-wide roots, not useful project workspaces: walking one
# can make the synchronous MCP request appear stuck for a long time. Keep this
# an exact-root denylist so projects below conventional development parents
# such as /home, /opt, or /tmp remain supported.
SYSTEM_PROJECT_ROOTS = frozenset(
    Path(item) for item in (
        "/", "/Applications", "/Library", "/System", "/Users", "/bin", "/boot",
        "/dev", "/etc", "/home", "/lib", "/lib64", "/media", "/mnt", "/opt",
        "/private", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp",
        "/usr", "/var",
    )
)
_STATE_LOCK_LOCAL = threading.local()
STATE_LOCK_TIMEOUT_SECONDS = 5.0
_STATE_LOCK_OWNER_FILE = ".state.lock.owner.json"


class LedgerBusyError(RuntimeError):
    """Raised when a bounded mutation lease cannot be acquired in time."""

    def __init__(self, operation: str, held_duration_ms: int, *, holder: dict[str, Any] | None = None) -> None:
        super().__init__(f"Cortex ledger is busy during {operation}")
        self.operation = re.sub(r"[^a-z0-9_.-]", "_", str(operation).lower())[:64] or "mutation"
        self.held_duration_ms = max(0, min(int(held_duration_ms), 600000))
        self.holder = dict(holder) if isinstance(holder, dict) else None


def _state_lock_owner_path(root: Path) -> Path:
    return root / _STATE_LOCK_OWNER_FILE


def _read_state_lock_holder(root: Path) -> dict[str, Any] | None:
    """Read bounded, non-secret holder metadata for a busy-lock diagnostic."""
    path = _state_lock_owner_path(root)
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return {
        "pid": int(value["pid"]) if isinstance(value.get("pid"), int) else None,
        "operation": redact(value.get("operation", ""), 128) or None,
        "task_id": redact(value.get("task_id", ""), 128) or None,
        "acquired_at": redact(value.get("acquired_at", ""), 64) or None,
    }


def _write_state_lock_holder(root: Path, payload: dict[str, Any]) -> None:
    """Best-effort atomic owner marker; lock correctness never depends on it."""
    path = _state_lock_owner_path(root)
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".state-lock-owner.", dir=str(root))
        os.fchmod(descriptor, 0o600)
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        os.write(descriptor, encoded)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        temporary = None
    except OSError:
        pass
    finally:
        if 'descriptor' in locals() and descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if temporary:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


def _clear_state_lock_holder(root: Path, token: str) -> None:
    path = _state_lock_owner_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("token") == token:
            path.unlink()
    except (OSError, ValueError, json.JSONDecodeError):
        pass
MCP_SERVER_INSTRUCTIONS = (
    "Cortex opt-in. Read canonical attempt results by attempt_result_ref before continuation. "
    "Internal workers emit English only. Lifecycle requires task_ref; no unscoped recovery. "
    "After resume, clear, or compaction inspect once with task_ref; use context_handoff and never restart."
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
MODE_OVERLAYS = PROFILE_CONTRACT.get("mode_overlays", {})
SHARED_WORKER_CONTRACT = PROFILE_CONTRACT.get("shared_worker_contract", {})
CODEBASE_MEMORY_REFRESH_PROFILES = set(SHARED_WORKER_CONTRACT.get("codebase_memory_refresh_profiles", []))
RETRY_POLICY = SHARED_WORKER_CONTRACT.get("retry_policy", {})
PROMPT_COMPACTION_GUIDANCE = SHARED_WORKER_CONTRACT.get("prompt_compaction_guidance", {})
if (
    SHARED_WORKER_CONTRACT.get("repository_intelligence")
    != "codebase_memory_first_when_available_then_source_confirmed_with_bounded_fallback"
    or SHARED_WORKER_CONTRACT.get("codebase_memory_project_resolution")
    != "derive_canonical_path_key_then_single_exact_root_list_fallback"
    or SHARED_WORKER_CONTRACT.get("codebase_memory_project_key_algorithm")
    != "cbm_project_name_from_path_safe_ascii_utf8hex_fnv1a200"
    or SHARED_WORKER_CONTRACT.get("codebase_memory_fallback")
    != "one_bounded_attempt_then_repository_native_tools_without_looping"
    or SHARED_WORKER_CONTRACT.get("attempt_result_lifecycle")
    != "read_dispatch_briefing_receipt_then_record_attempt_event_checkpoints_then_complete_attempt_closes_one_attempt; missing briefing receipt is retryable and cannot mutate the attempt; finalization_or_projection_failures_retry_server_side_without_respawning_the_worker"
    or SHARED_WORKER_CONTRACT.get("worker_result_fields")
    != ["status", "summary", "findings", "decisions_needed", "unresolved"]
    or SHARED_WORKER_CONTRACT.get("worker_operations")
    != ["worker_question", "record_attempt_event", "complete_attempt", "read_dispatch_briefing", "read_worker_result"]
    or SHARED_WORKER_CONTRACT.get("caller_correctable_tool_errors")
    != "retry_same_tool_same_attempt_without_budget_until_accepted_or_explicit_nonretryable"
    or SHARED_WORKER_CONTRACT.get("read_only_workspace_delta")
    != "ordinary_source_changes_are_concurrency_evidence_all_ignored_side_effects_are_audited_nonblocking_recognized_ephemeral_artifacts_classified"
    or CODEBASE_MEMORY_REFRESH_PROFILES != {"planner", "explorer", "architect", "database_architect"}
    or not CODEBASE_MEMORY_REFRESH_PROFILES.issubset(AGENTS)
    or RETRY_POLICY != {
        "pipeline_rework": "unbounded_while_acceptance_or_findings_require_correction",
        "terra_after_failed_attempts": 2,
        "effort_by_prior_failures": {"1": "high", "2": "xhigh", "3+": "max"},
    }
    or set(MODE_OVERLAYS) != {"harvest"}
    or set(MODE_OVERLAYS["harvest"]) != {
        "planner", "explorer", "architect", "technical_writer", "code_reviewer", "build_verification"
    }
    or not all(isinstance(value, str) and value.strip() for value in MODE_OVERLAYS["harvest"].values())
    or PROMPT_COMPACTION_GUIDANCE != {
        "bootstrap_target_bytes": 1500,
        "ordinary_briefing_target_bytes": 16 * 1024,
        "harvest_briefing_target_bytes": 18 * 1024,
    }
):
    raise RuntimeError("bundled Cortex shared worker contract is invalid")
AVAILABLE_GATES = {
    "scope", "plan", "discover", "architecture", "database_architecture", "implementation",
    "qa", "security", "performance", "accessibility", "ux", "review",
    "documentation", "close", "governance_activation", "governance_close",
}
READ_ONLY_RESULT_GATES = {
    "scope", "plan", "discover", "architecture", "database_architecture", "security",
    "performance", "accessibility", "ux", "review", "close", "governance_activation", "governance_close",
}
EXECUTED_CHECK_RESULT_GATES = {
    "implementation", "qa", "security", "performance", "accessibility", "ux",
    "review", "documentation", "close", "governance_activation", "governance_close",
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


def render_gate_briefing(gate: str, task_user_request: object, profile: str) -> dict[str, Any]:
    """Render trusted gate defaults around the current task without interpreting user text."""
    template = GATE_BRIEFINGS[gate]
    values = {
        "task_user_request": redact(task_user_request, 4000),
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
    for field in ("user_request", "requirements", "acceptance_criteria", "scope", "allowed_paths", "verification"):
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
    mobile_signals = (
        "react native", "expo", "android", "ios", "swiftui", "uikit", "jetpack compose", "flutter",
        "mobile app", "мобильн", "андроид",
    )
    matched_mobile = [signal for signal in mobile_signals if _routing_signal_matches(corpus, signal)]
    if matched_mobile:
        return {
            "profile": "mobile_dev",
            "reason": "Mobile platform/framework evidence takes precedence over generic React/frontend or API signals.",
            "matched_signals": matched_mobile,
            "source": "bounded_task_signals",
        }
    path_values = [str(item).lower().replace("\\", "/") for item in task.get("allowed_paths", [])]
    python_path_evidence = any(
        path.endswith(".py")
        or path.startswith(("plugins/cortex/scripts", "scripts/", "src/")) and "python" in corpus
        for path in path_values
    )
    python_runtime = python_path_evidence and any(
        signal in corpus for signal in ("runtime", "plugin", "service", "server", "cortex", "python")
    )
    if python_runtime:
        return {
            "profile": "backend_dev",
            "reason": "Python runtime/service paths indicate backend implementation rather than a generic full-stack owner.",
            "matched_signals": [path for path in path_values if path.endswith(".py")][:8],
            "source": "bounded_task_signals",
        }
    application_paths = [
        path for path in path_values
        if not path.startswith((".github/", "infra/", "deploy/", "ops/"))
        and not path.endswith((".yml", ".yaml", ".tf"))
    ]
    application_change_signal = any(
        _routing_signal_matches(corpus, signal)
        for signal in (
            "fix", "bug", "feature", "implement", "application change", "app change",
            "исправ", "ошибк", "функц", "реализ",
        )
    )
    for rule in IMPLEMENTATION_ROUTING["rules"]:
        if rule.get("profile") == "devops_engineer" and (application_paths or application_change_signal):
            # Deployment is a later operational concern when the same task also
            # changes application code. Planner microtasks decide the primary
            # code owner; a deployment keyword alone must not steal the fix.
            continue
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
GOVERNANCE_FULL_GATES = ("governance_activation", "governance_close")
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
# ``ContextCompiler`` is deliberately strict about the durable task-domain
# shape: it accepts at most 100 requirements and each requirement must be no
# longer than 600 characters.  Requirements enter the ledger through several
# public/control-plane paths, so keep the ingress invariant beside the common
# text-array normalizer instead of relying on a later briefing compilation to
# discover it.
MAX_CANONICAL_REQUIREMENTS = 100
MAX_CANONICAL_REQUIREMENT_LENGTH = 600
# The result itself is an immutable, cursor-paged SQLite artifact; it must not
# have a second product-sized quota.  Keep the same hard bound as one atomic
# JSON document so a malformed or hostile local draft cannot exhaust process
# memory or disk in a single operation.  The 32 KiB transport page is a read
# concern only and never truncates canonical result content.
MAX_JSON_BYTES = 8 * 1024 * 1024
# Planner and Scope payloads are immutable result siblings.  Keep their
# complete content on the same technical artifact boundary rather than
# rejecting evidence because it will later be represented by an artifact ref
# in a compact worker briefing.
MAX_PLANNING_BYTES = MAX_JSON_BYTES
MAX_SCOPING_BYTES = MAX_JSON_BYTES
MAX_DISCOVERY_DOMAINS = 8
MAX_BRIEFING_BYTES = 128 * 1024
MAX_WORK_PACKAGES = 32
MAX_MICROTASKS_PER_PACKAGE = 32
MAX_MICROTASKS_PER_PLAN = 128
MAX_TASK_STATE_BYTES = 8 * 1024 * 1024
# Every ordinary JSON artifact is bounded before it replaces an existing
# ledger file. Large manifests use the explicit, larger budget below instead
# of silently bypassing the guard.
# ``MAX_JSON_BYTES`` is defined above because result payloads share this
# technical atomic-persistence boundary.
# A project manifest is intentionally larger than an individual result or
# task-state document: it contains one bounded inventory record per project
# entry.  Keep the read cap finite while allowing ordinary repositories to
# complete handoff and reconciliation.
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
# Compact inspect/recovery handoffs retain only the newest summaries.  Worker
# dispatches use scoped result refs instead of embedding these summaries, so
# predecessor grants are bounded by the task's result inventory instead.
MAX_CONTEXT_RESULTS = 8
# This bounds only repeated failures inside the private atomic commit adapter;
# it is not a pipeline, QA, gate, worker, or rework-attempt budget.
MAX_GATE_RECOVERY_FAILURES = 3
REWORK_TERRA_AFTER_FAILURES = int(RETRY_POLICY["terra_after_failed_attempts"])
REWORK_EFFORT_BY_PRIOR_FAILURES = dict(RETRY_POLICY["effort_by_prior_failures"])
MAX_GATE_RECOVERY_EVENTS = 64
MAX_TOOL_ERROR_LOG_FIELDS = 64
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
if MODEL_ROUTING.get("max_policy") != "complex_work_or_repeated_rework":
    raise RuntimeError("bundled Cortex max effort policy must cover complex work and repeated rework")

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
    "data_gathering", "data-gathering",
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
# Documentation evidence is a decision receipt, not a free-form result label.
# Kinds are current protocol values and are validated without format aliases.
DOCUMENTATION_EVIDENCE_KINDS = {
    "documentation",
    "documentation_sync",
    "documentation_applicability",
    "verification",
    "command",
}
TRACKER_POLICY = {
    "schema": "cortex/file-manifest/v1",
    "scope": "all non-directory entries below project_root after policy exclusions",
    "ignored_roots": [".git", ".codex/cortex"],
    # These are language-agnostic dependency, cache, test-output, and runtime
    # directories. They cover common test/build stacks across languages while
    # remaining limited to names that conventionally contain generated material
    # rather than project source.
    "ignored_directory_names": [
        "__pycache__", ".build", ".bundle", ".cache", ".dart_tool", ".direnv",
        ".eggs", ".expo", ".gradle", ".hypothesis", ".mypy_cache", ".next", ".nox",
        ".nyc_output", ".parcel-cache", ".phpunit.cache", ".pnpm-store",
        ".pub-cache", ".pytest_cache", ".ruff_cache", ".serverless", ".stack-work",
        ".svelte-kit", ".terraform", ".tox", ".turbo", ".venv", "BenchmarkDotNet.Artifacts",
        "CMakeFiles", "DerivedData", "Pods", "TestResults", "_build", "allure-report",
        "allure-results", "blob-report", "coverage", "dist-newstyle", "htmlcov",
        "node_modules", "pip-wheel-metadata", "playwright-report", "test-results",
    ],
    "ignored_relative_roots": [
        ".yarn/cache", ".yarn/unplugged", "Carthage/Build", "cypress/screenshots", "cypress/videos",
    ],
    # Generic output names require either an explicit .gitignore rule or a
    # recognizable build marker; source directories named build/dist/target
    # are therefore not hidden merely because of their name.
    "build_output_directory_names": ["build", "dist", "out", "target", "bin", "obj"],
    "virtual_environment_prefixes": [".venv"],
    "ignored_file_suffixes": [".pyc", ".pyo"],
    "ignored_file_patterns": [
        ".coverage", ".rspec_status", "TEST-*.xml", "clover.xml", "coverage-*", "coverage.*",
        "jacoco.exec", "junit.xml", "lcov.info", "*.lcov", "*.trx",
    ],
    "symlinks": "record link target and never follow",
    "special_files": "record type and metadata without reading content",
    "gitignore": "honor directory and file patterns from .gitignore, including negation",
    "manifest_limits": {
        "max_entries": 100000,
        "max_hashed_bytes": 2147483648,
        "max_seconds": 30,
    },
}
READ_ONLY_EPHEMERAL_REASON_PREFIXES = (
    "conventional generated directory: ",
    "conventional generated root: ",
    "conventional generated file: ",
    "virtual environment directory: ",
    "recognized build output directory: ",
    "ignored file suffix: ",
)
MANIFEST_SNAPSHOT_PREFIX = "manifest-"
MANIFEST_SNAPSHOT_REF_RE = re.compile(r"^manifest-([0-9a-f]{64})$")
VERIFICATION_COMMANDS = {
    "benign_success": {"argv": ["/usr/bin/true"], "cwd": "."},
    "benign_failure": {"argv": ["/usr/bin/false"], "cwd": "."},
}
SENSITIVE_KEY_RE = re.compile(r"(?i)(?:^|[_ -])(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|token|password|passwd|secret|private[_ -]?key|authorization|coordinator[_ -]?capability|coordinator[_ -]?recovery[_ -]?proof)(?:$|[_ -])")
SENSITIVE_LOG_KEY_NAMES = {
    "apikey", "accesstoken", "refreshtoken", "clientsecret", "token",
    "password", "passwd", "secret", "privatekey", "authorization",
    "coordinatorcapability", "coordinatorrecoveryproof",
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


def redact(value: object, limit: int | None = MAX_TEXT) -> str:
    text = str(value or "")
    if limit is not None:
        text = text[:limit]
    text = AUTHORIZATION_RE.sub(lambda match: f"{match.group(1)}<REDACTED>", text)
    text = BEARER_RE.sub(lambda match: f"{match.group(1)}<REDACTED>", text)
    text = URI_CREDENTIAL_RE.sub(r"\1<REDACTED>@", text)
    text = ENV_SECRET_RE.sub(r"\1<REDACTED>", text)
    return SENSITIVE_RE.sub(lambda match: f"{match.group(1)}=<REDACTED>", text)


def normalize_init_text_list(value: object, field: str, *, item_limit: int = 100) -> list[str]:
    """Validate one current canonical init-task text-array field."""
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a current canonical text array")
    return [item.strip() for item in value][:item_limit]


def _atomize_requirement_text(value: str) -> list[str]:
    """Split one redacted requirement without discarding any of its text.

    A task requirement is a typed ContextDomain record, not a display field.
    Its compiler boundary is 600 characters.  Prefer a whitespace boundary so
    natural-language clauses stay intact where possible.  Keep a selected
    whitespace boundary in the preceding atom and preserve leading/trailing
    whitespace too: ``ContextCompiler`` applies its own display-time strip,
    but durable normalization must be byte-stable when a receipt is consumed
    again.  No user text is discarded and the resulting atoms are idempotent.
    A single unbroken token is split at the hard boundary because there is no
    meaning-preserving word boundary available.
    """
    text = value
    atoms: list[str] = []
    while len(text) > MAX_CANONICAL_REQUIREMENT_LENGTH:
        window = text[:MAX_CANONICAL_REQUIREMENT_LENGTH]
        split_at = max(window.rfind(" "), window.rfind("\t"), window.rfind("\n"), window.rfind("\r"))
        # Include the selected separator in the prior atom.  That makes
        # ``''.join(atoms)`` reconstruct the complete redacted input and,
        # unlike lstrip/rstrip normalization, remains stable when init_task
        # revalidates a persisted classification receipt.
        end = split_at + 1 if split_at > 0 else MAX_CANONICAL_REQUIREMENT_LENGTH
        atom = text[:end]
        if not atom.strip():
            # Defensive fallback for pathological whitespace-only windows.
            end = MAX_CANONICAL_REQUIREMENT_LENGTH
            atom = text[:end]
        atoms.append(atom)
        text = text[end:]
    if text.strip():
        atoms.append(text)
    return atoms


def normalize_task_requirements(value: object) -> list[str]:
    """Return the only persistable representation of ``task.requirements``.

    Oversized requirements are atomized *before* they reach classification,
    receipts, or task definitions.  No text is truncated: explicit secret
    redaction remains the existing safety policy, while all non-sensitive
    normalized text is retained in order.  If the complete content cannot fit
    in ContextCompiler's fixed 100x600 domain, reject it at ingress rather
    than persisting a task which will later become uncontinuable.
    """
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("requirements must be a current canonical text array")
    atoms: list[str] = []
    for item in value:
        # Do not use redact's ordinary display limit here.  Atomization is the
        # bounded storage policy and must see the entire user-supplied value.
        # In particular, do not strip an existing atom: init_task revalidates
        # immutable receipts and must preserve the split separator exactly.
        atoms.extend(_atomize_requirement_text(redact(item, limit=None)))
        if len(atoms) > MAX_CANONICAL_REQUIREMENTS:
            raise ValueError(
                "requirements exceed the bounded canonical task domain "
                f"({MAX_CANONICAL_REQUIREMENTS} items of {MAX_CANONICAL_REQUIREMENT_LENGTH} characters); "
                "split the work into separate tasks"
            )
    return atoms


def require_internal_english(value: object, label: str) -> None:
    """Reject worker-authored durable text in a non-Latin script.

    Prompting establishes the full English-only rule. This narrow guard is a
    deterministic boundary for the common failure mode (for example Cyrillic
    worker results/questions) without trying to classify quoted source data or
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

    Public v5 arguments intentionally do not accept caller-supplied durable
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
        raise ValueError(
            "CORTEX_ROOT is not supported; Cortex control state is derived from project_root "
            "and stored in the host-private control store"
        )
    requested = str((params or {}).get("project_root") or "").strip()
    if not requested:
        raise ValueError("project_root is required for every Cortex tool call")
    requested_path = Path(os.path.normpath(str(Path(requested).expanduser())))
    if not requested_path.is_absolute():
        raise ValueError("project_root must be an absolute path")
    path = _reject_symlink_ancestry(requested_path, "project root")
    if not path.is_dir():
        raise ValueError(f"project root is not a directory: {path}")
    if path == PLUGIN_ROOT or PLUGIN_ROOT in path.parents:
        raise ValueError("project_root must not be the Cortex plugin directory")
    if path == Path.home().absolute() or path in SYSTEM_PROJECT_ROOTS:
        raise ValueError(
            "project_root must be a specific repository or worktree, not a system or home directory; "
            "Cortex recursively captures a content-addressed manifest before orchestration starts"
        )
    return path


def project_root(params: dict[str, Any] | None = None) -> Path:
    return select_project_root(params)


def _lstat_or_none(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _assert_owned_by_current_user(info: os.stat_result, label: str) -> None:
    """Reject host-store paths owned by another local account when available."""
    if os.name == "nt" or not hasattr(os, "getuid"):
        return
    if int(info.st_uid) != int(os.getuid()):
        raise ValueError(f"{label} must be owned by the current host user")


def _assert_private_directory(path: Path, label: str) -> Path:
    """Validate one existing non-symlink directory used for private state."""
    candidate = _reject_symlink_ancestry(path, label, allow_missing_leaf=True)
    info = _lstat_or_none(candidate)
    if info is None:
        raise ValueError(f"{label} is unavailable")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"{label} must be a real non-symlink directory")
    _assert_owned_by_current_user(info, label)
    if info.st_mode & 0o077:
        raise ValueError(f"{label} has unsafe permissions; require mode 0700")
    return candidate


def _assert_host_store_parent(path: Path, label: str) -> Path:
    """Validate a parent that names a host-private directory.

    The parent does not need to be unreadable (a home directory is commonly
    searchable by a primary group), but another user/group must not be able to
    replace the child ledger entry beneath it.  The child itself is always
    required to be mode 0700.
    """
    candidate = _reject_symlink_ancestry(path, label, allow_missing_leaf=False)
    info = _lstat_or_none(candidate)
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"{label} must be a real existing directory")
    _assert_owned_by_current_user(info, label)
    if info.st_mode & 0o022:
        raise ValueError(
            f"{label} is writable by group or other users; configure a mode-0700 host-private "
            f"directory through {HOST_CONTROL_STORE_ENV}"
        )
    return candidate


def _ensure_private_directory(path: Path, label: str) -> Path:
    """Create one leaf private directory after validating its host parent."""
    candidate = _reject_symlink_ancestry(path, label, allow_missing_leaf=True)
    existing = _lstat_or_none(candidate)
    if existing is not None:
        return _assert_private_directory(candidate, label)
    _assert_host_store_parent(candidate.parent, f"{label} parent")
    try:
        os.mkdir(candidate, 0o700)
    except FileExistsError:
        # A concurrent host process may have created the same project store.
        # Revalidate it rather than inheriting its permissions or type.
        return _assert_private_directory(candidate, label)
    try:
        os.chmod(candidate, 0o700)
    except OSError:
        # Do not continue with a control root whose privacy could not be
        # established.  The caller receives a recoverable storage diagnostic.
        raise
    return _assert_private_directory(candidate, label)


def _project_path_is_within(path: Path, candidate_parent: Path) -> bool:
    try:
        path.absolute().relative_to(candidate_parent.absolute())
    except ValueError:
        return False
    return True


def _host_control_store_base(project: Path, *, create: bool) -> Path:
    """Resolve the trusted host-side parent for opaque project ledgers.

    ``CORTEX_HOST_STATE_DIR`` is an operator/process configuration only.  It
    is intentionally absent from every public schema and is rejected when it
    points into the caller-selected workspace.  ``CORTEX_ROOT`` remains
    unsupported, so a tool caller cannot redirect storage through an argument.
    """
    configured = str(os.environ.get(HOST_CONTROL_STORE_ENV) or "").strip()
    if configured:
        requested = Path(os.path.normpath(str(Path(configured).expanduser())))
        if not requested.is_absolute():
            raise ValueError(f"{HOST_CONTROL_STORE_ENV} must be an absolute host-private directory")
        base = _reject_symlink_ancestry(requested, "host control-store root", allow_missing_leaf=True)
        if _project_path_is_within(base, project) or _project_path_is_within(project, base):
            raise ValueError(
                f"{HOST_CONTROL_STORE_ENV} must be outside project_root; Cortex refuses workspace-controlled state"
            )
        if create:
            return _ensure_private_directory(base, "host control-store root")
        if _lstat_or_none(base) is not None:
            return _assert_private_directory(base, "host control-store root")
        return base

    home = _reject_symlink_ancestry(Path.home().absolute(), "host home", allow_missing_leaf=False)
    codex_home = home / ".codex"
    if create:
        if _lstat_or_none(codex_home) is None:
            _ensure_private_directory(codex_home, "Codex host directory")
        else:
            _assert_private_directory(codex_home, "Codex host directory")
        return _ensure_private_directory(codex_home / "cortex", "Cortex host control-store root")
    if _lstat_or_none(codex_home) is None:
        return codex_home / "cortex"
    _assert_private_directory(codex_home, "Codex host directory")
    base = codex_home / "cortex"
    if _lstat_or_none(base) is not None:
        return _assert_private_directory(base, "Cortex host control-store root")
    return base


def stable_project_id(project: Path) -> str:
    """Return an opaque, stable-per-worktree host-store namespace identifier."""
    info = project.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("project root is not a directory")
    identity = {
        "schema": HOST_CONTROL_STORE_SCHEMA,
        # Preserve stable mapping for the same directory while making a fresh
        # checkout at a reused spelling a different private control plane.
        "path": str(project.absolute()),
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
    }
    return "p-" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


_HOST_PROJECT_IDENTITY_KEY = "host_project_identity"
_HOST_PROJECT_IDENTITY_SCHEMA = "cortex/host-project-identity/v1"


def _host_project_identity(project: Path) -> dict[str, Any]:
    """Return the non-secret filesystem identity used for rename recovery.

    The opaque namespace intentionally includes the spelling of ``project`` so
    a new checkout at the same pathname cannot inherit a prior ledger.  A
    rename of the *same* directory, however, preserves its device/inode pair.
    Keep that pair in the private ledger so the host can relocate the opaque
    namespace atomically without retaining any task artifact absolute paths.
    """
    info = project.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("project root is not a directory")
    return {
        "schema": _HOST_PROJECT_IDENTITY_SCHEMA,
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
        "path": str(project.absolute()),
    }


def _record_host_project_identity(root: Path, project: Path, *, rebound_from: str | None = None) -> bool:
    """Persist host-project identity only when identity or rebind history changed.

    ``ledger_root()`` is reached by ordinary read and lifecycle calls.  Its
    rename-recovery identity must remain durable, but refreshing
    ``updated_at`` on every open turns that otherwise read-oriented path into
    an avoidable SQLite writer.  Keep the existing timestamp and payload when
    this is the same directory identity with no new rebind provenance.

    Return whether this call wrote the document so focused callers/tests can
    distinguish an idempotent open from an actual provenance transition.
    """
    identity = _host_project_identity(project)
    existing = db_get_global(root, _HOST_PROJECT_IDENTITY_KEY, {})
    existing_history = existing.get("history") if isinstance(existing, dict) else None
    history = list(existing_history) if isinstance(existing_history, list) else []
    if rebound_from and rebound_from != identity["path"]:
        history.append({"from": rebound_from, "to": identity["path"], "at": now()})
    bounded_history = history[-16:]

    # Do not use a whole-document equality test here: ``updated_at`` is
    # intentionally operational metadata, not a reason to acquire a writer
    # lock.  A malformed/non-list history is normalized on the next genuine
    # write instead of being treated as an equivalent provenance record.
    if (
        isinstance(existing, dict)
        and all(existing.get(key) == value for key, value in identity.items())
        and isinstance(existing_history, list)
        and existing_history == bounded_history
    ):
        return False
    db_put_global(root, _HOST_PROJECT_IDENTITY_KEY, {
        **identity,
        "history": bounded_history,
        "updated_at": now(),
    })
    return True


def _find_renamed_project_ledger(project: Path, target: Path) -> Path | None:
    """Find one private ledger whose recorded directory was renamed in place.

    This is deliberately narrow: the desired namespace must not exist, every
    candidate must be a validated private ledger, and there must be exactly
    one matching device/inode record.  Ambiguity is a hard error rather than a
    best-effort attempt to choose another project's control plane.
    """
    projects = target.parent
    if not projects.exists():
        return None
    desired = _host_project_identity(project)
    matches: list[Path] = []
    for candidate in sorted(projects.iterdir(), key=lambda item: item.name):
        if candidate == target or not candidate.name.startswith("p-"):
            continue
        try:
            _assert_private_directory(candidate, "Cortex host project ledger")
            identity = db_get_global(candidate, _HOST_PROJECT_IDENTITY_KEY, {})
        except (OSError, ValueError, sqlite3.Error):
            # An unrelated stale/corrupt namespace must not become a rename
            # candidate. Normal maintenance remains responsible for it.
            continue
        if not isinstance(identity, dict) or identity.get("schema") != _HOST_PROJECT_IDENTITY_SCHEMA:
            continue
        if int(identity.get("device", -1)) == desired["device"] and int(identity.get("inode", -1)) == desired["inode"]:
            matches.append(candidate)
    if len(matches) > 1:
        raise ValueError("multiple host-private Cortex ledgers match this renamed project; refuse to choose state")
    return matches[0] if matches else None


def _maybe_rebind_renamed_project_ledger(project: Path, target: Path) -> None:
    """Atomically move an exact private ledger after a workspace rename."""
    if _lstat_or_none(target) is not None:
        return
    source = _find_renamed_project_ledger(project, target)
    if source is None:
        return
    # Active task definitions and manifest snapshots deliberately bind the
    # workspace spelling.  Moving only the control root would leave those
    # immutable baselines pointing at the old workspace and turn a rename
    # into a misleading verification failure.  Completed ledgers are safe to
    # rebind automatically; active work needs an explicit future maintenance
    # protocol that invalidates/re-baselines every live attempt atomically.
    active = []
    for task_id in db_task_index(source):
        loaded = db_load_task(source, task_id)
        if loaded is not None and str(loaded[1].get("status") or "") in {"active", "blocked"}:
            active.append(task_id)
    if active:
        raise ValueError(
            "renamed project has active Cortex tasks; explicit rebind maintenance must re-baseline live task state"
        )
    source_identity = db_get_global(source, _HOST_PROJECT_IDENTITY_KEY, {})
    old_path = str(source_identity.get("path") or "") if isinstance(source_identity, dict) else ""
    try:
        os.replace(source, target)
        _fsync_directory(source.parent)
    except OSError as exc:
        raise ValueError("renamed project Cortex ledger could not be rebound atomically") from exc
    _record_host_project_identity(target, project, rebound_from=old_path)


def ledger_root_path(params: dict[str, Any] | None = None, *, create: bool = False) -> Path:
    """Return the host-private ledger path for one workspace without opening SQLite.

    This helper is intentionally deterministic and side-effect free when
    ``create`` is false.  It lets hooks and maintenance inspect only the
    selected project mapping without falling back to a workspace-local
    ``.codex/cortex`` directory.
    """
    project = select_project_root(params)
    base = _host_control_store_base(project, create=create)
    projects = base / "projects"
    if create:
        _ensure_private_directory(projects, "Cortex host projects directory")
    elif _lstat_or_none(projects) is not None:
        _assert_private_directory(projects, "Cortex host projects directory")
    return _reject_symlink_ancestry(
        projects / stable_project_id(project), "Cortex host project ledger", allow_missing_leaf=True
    )


def existing_ledger_root(params: dict[str, Any] | None = None) -> Path:
    """Resolve the current host-private ledger without creating a database."""
    project = select_project_root(params)
    target = ledger_root_path({"project_root": str(project)}, create=False)
    if _lstat_or_none(target) is None:
        _maybe_rebind_renamed_project_ledger(project, target)
    if _lstat_or_none(target) is not None:
        _assert_private_directory(target, "Cortex host project ledger")
    return target


_MANIFEST_DIGEST_CACHE: dict[tuple[str, int, int, int, int, int, int], dict[str, Any]] = {}
_MANIFEST_DIGEST_CACHE_LOCK = threading.Lock()
_MANIFEST_DIGEST_CACHE_MAX = 200000


def _manifest_file(path: Path, info: os.stat_result) -> dict[str, Any]:
    cache_key = (
        str(path), int(info.st_dev), int(info.st_ino), int(info.st_size),
        int(info.st_mtime_ns), int(getattr(info, "st_ctime_ns", 0)), stat.S_IMODE(info.st_mode),
    )
    with _MANIFEST_DIGEST_CACHE_LOCK:
        cached = _MANIFEST_DIGEST_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached) | {"digest_cache_hit": True}
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
        result = {"kind": "file", "sha256": digest.hexdigest(), "size": opened.st_size, "mode": stat.S_IMODE(opened.st_mode)}
        with _MANIFEST_DIGEST_CACHE_LOCK:
            if len(_MANIFEST_DIGEST_CACHE) >= _MANIFEST_DIGEST_CACHE_MAX:
                # The cache is an optimization only. A bounded clear is safer
                # than allowing a daemon process to retain every historic path.
                _MANIFEST_DIGEST_CACHE.clear()
            _MANIFEST_DIGEST_CACHE[cache_key] = dict(result)
        return result
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
        "build": ("CMakeFiles", ".ninja_deps", "intermediates", "classes", "AttemptResults"),
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


def _manifest_ephemeral_file_reason(parts: tuple[str, ...], policy: dict[str, Any]) -> str | None:
    """Return a bounded conventional generated-file reason, if one applies."""
    name = parts[-1]
    patterns = tuple(str(item) for item in policy.get("ignored_file_patterns", []))
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
        return f"conventional generated file: {name}"
    return None


def _read_only_ephemeral_ignored_entry(path: str, entry: object, policy: dict[str, Any]) -> bool:
    """Whether one ignored-manifest entry is safe test/build residue for read-only gates.

    A project may list a conventional cache or coverage path in ``.gitignore``.
    The manifest records the user's rule first, so recognize the same bounded
    conventions here rather than treating that spelling detail as a failed
    read-only worker result. Arbitrary gitignored paths intentionally remain
    observable failures.
    """
    if not isinstance(entry, dict):
        return False
    reason = str(entry.get("reason") or "")
    if reason.startswith(READ_ONLY_EPHEMERAL_REASON_PREFIXES):
        return True
    if not reason.startswith(".gitignore:"):
        return False

    # Older active attempts retain a frozen v1 policy. Merge defaults so a
    # runtime upgrade still recognizes the stable cross-language conventions
    # below without rewriting that attempt's baseline policy.
    effective_policy = dict(TRACKER_POLICY)
    effective_policy.update(policy)
    parts = tuple(Path(path).parts)
    if not parts:
        return False
    name = parts[-1]
    kind = str(entry.get("kind") or "")
    if kind == "directory":
        if name in {str(item) for item in effective_policy.get("ignored_directory_names", [])}:
            return True
        relative = Path(*parts).as_posix()
        for root in effective_policy.get("ignored_relative_roots", []):
            root_text = Path(str(root)).as_posix().strip("/")
            if relative == root_text or relative.startswith(root_text + "/"):
                return True
        prefixes = tuple(str(item) for item in effective_policy.get("virtual_environment_prefixes", []))
        if any(name.startswith(prefix) for prefix in prefixes):
            return True
        # Maven, Gradle, Rust, .NET, C/C++, and similar projects commonly
        # ignore these roots directly, which prevents marker inspection.
        if name in {str(item) for item in effective_policy.get("build_output_directory_names", [])}:
            return True
        return False
    if kind == "file":
        if name.endswith(tuple(str(item) for item in effective_policy.get("ignored_file_suffixes", []))):
            return True
        return _manifest_ephemeral_file_reason(parts, effective_policy) is not None
    return False


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
    started_at = time.monotonic()
    limit_values = active_policy.get("manifest_limits") if isinstance(active_policy.get("manifest_limits"), dict) else {}
    max_entries = max(1, int(limit_values.get("max_entries", 100000)))
    max_hashed_bytes = max(1, int(limit_values.get("max_hashed_bytes", 2147483648)))
    max_seconds = max(1, int(limit_values.get("max_seconds", 30)))
    budget = {"hashed_bytes": 0, "cache_hits": 0, "partial": False, "reason": None, "at": None}
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
        if not is_dir and parts:
            conventional_file = _manifest_ephemeral_file_reason(parts, active_policy)
            if conventional_file:
                return True, conventional_file
            if parts[-1].endswith(ignored_suffixes):
                return True, f"ignored file suffix: {parts[-1]}"
        return False, None

    def walk(directory: Path, relative: tuple[str, ...] = (), inherited_rules: list[dict[str, Any]] | None = None) -> None:
        if budget["partial"]:
            return
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
            if budget["partial"]:
                return
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
            if len(entries) >= max_entries:
                budget.update({"partial": True, "reason": "entry_limit", "at": rel})
                return
            if time.monotonic() - started_at >= max_seconds:
                budget.update({"partial": True, "reason": "time_limit", "at": rel})
                return
            if stat.S_ISLNK(mode):
                entries[rel] = {"kind": "symlink", "target": os.readlink(path), "mode": stat.S_IMODE(mode)}
            elif is_directory:
                walk(path, parts, rules)
            elif stat.S_ISREG(mode):
                if budget["hashed_bytes"] + int(info.st_size) > max_hashed_bytes:
                    budget.update({"partial": True, "reason": "hashed_byte_limit", "at": rel})
                    return
                record = _manifest_file(path, info)
                if record.pop("digest_cache_hit", False):
                    budget["cache_hits"] += 1
                else:
                    budget["hashed_bytes"] += int(info.st_size)
                entries[rel] = record
            else:
                entries[rel] = {"kind": "special", "file_type": stat.S_IFMT(mode), "mode": stat.S_IMODE(mode), "size": info.st_size}
    walk(base)
    partial_descriptor = {
        "partial": bool(budget["partial"]),
        "reason": budget["reason"],
        "at": budget["at"],
    }
    digest_payload: Any = {"entries": entries, "partial_manifest": partial_descriptor} if budget["partial"] else entries
    encoded = json.dumps(digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
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
        "partial_manifest": partial_descriptor,
        "capture_metrics": {
            "hashed_bytes": int(budget["hashed_bytes"]),
            "digest_cache_hits": int(budget["cache_hits"]),
            "elapsed_ms": int((time.monotonic() - started_at) * 1000),
            "limits": {
                "max_entries": max_entries,
                "max_hashed_bytes": max_hashed_bytes,
                "max_seconds": max_seconds,
            },
        },
        "captured_at": now(),
    }


def _manifest_partial_descriptor(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded capture status used by integrity-critical callers.

    Partial manifests remain valid diagnostic artifacts.  They must not be
    treated as a complete baseline or final view, however, because entries
    beyond the cutoff were never observed and may contain unreported changes.
    """
    raw = manifest.get("partial_manifest")
    if not isinstance(raw, dict):
        return {"partial": False, "reason": None, "at": None}
    return {
        "partial": bool(raw.get("partial")),
        "reason": raw.get("reason"),
        "at": raw.get("at"),
    }


def compare_manifests(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_partial = _manifest_partial_descriptor(before)
    after_partial = _manifest_partial_descriptor(after)
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
    return {
        "added": sorted(remaining_added),
        "modified": modified,
        "deleted": sorted(remaining_deleted),
        "renamed": renamed,
        "changed_paths": changed_paths,
        "change_count": len(modified) + len(remaining_added) + len(remaining_deleted) + len(renamed),
        # A bounded capture is useful diagnostic evidence, but it is not a
        # complete view of either side of an integrity comparison.  Keep both
        # descriptors in the comparison so receipts explain why a seemingly
        # unchanged/unscanned path cannot authorize reconciliation.
        "complete": not before_partial["partial"] and not after_partial["partial"],
        "partial_manifest": {
            "baseline": before_partial,
            "current": after_partial,
        },
    }


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
        database = candidate / "cortex.db"
        info = _lstat_or_none(database)
        # Pre-P2.2 ledgers were named ``cortex`` below a workspace.  New
        # host-private ledgers use an opaque per-project id below the trusted
        # ``projects`` parent.  Do not accept an arbitrary nested SQLite file
        # as an artifact owner.
        private_project_ledger = (
            candidate.parent.name == "projects"
            and re.fullmatch(r"p-[0-9a-f]{64}", candidate.name) is not None
        )
        if (
            (candidate.name == "cortex" or private_project_ledger)
            and info is not None
            and stat.S_ISREG(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
        ):
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
    # Capture time and performance counters are diagnostic metadata, not
    # project state. Omitting them lets a cold capture and a digest-cache hit
    # reuse the same immutable content-addressed snapshot.
    snapshot = dict(manifest)
    snapshot.pop("captured_at", None)
    snapshot.pop("capture_metrics", None)
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
        current_partial = _manifest_partial_descriptor(current)
        checkpoint_partial = (checkpoint.get("comparison") or {}).get("partial_manifest")
        if not isinstance(checkpoint_partial, dict):
            checkpoint_partial = {}
        checkpoint_baseline_partial = checkpoint_partial.get("baseline")
        checkpoint_current_partial = checkpoint_partial.get("current")
        checkpoint_has_partial = any(
            isinstance(item, dict) and item.get("partial")
            for item in (checkpoint_baseline_partial, checkpoint_current_partial)
        )
        checkpoint_complete = (
            bool(checkpoint.get("complete"))
            and not checkpoint_has_partial
            and not current_partial["partial"]
        )
        return {
            **checkpoint,
            "reported_paths": sorted(supplied),
            "unaccounted_paths": missing,
            "complete": checkpoint_complete and not missing,
            "partial_manifest": {
                "baseline": checkpoint_partial.get("baseline"),
                "current": current_partial,
            },
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
        # A path list can be exhaustive only when both captures were complete.
        # Keep the partial descriptors in the receipt for diagnostics, while
        # making the integrity decision fail closed.
        "complete": bool(comparison.get("complete")) and not missing,
        "partial_manifest": comparison.get("partial_manifest"),
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
        _revoke_coordinator_capability(root, task_id, reason="task_terminal")
    except (OSError, ValueError, json.JSONDecodeError):
        # Completion is already durable.  The activation removal above still
        # makes public governance mutations fail closed if the compact audit
        # receipt cannot be updated.
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
    """Open the one private control-plane ledger for an explicit workspace.

    No caller-controlled workspace path is used as the durable ledger root.
    """
    project = select_project_root(params)
    path = ledger_root_path({"project_root": str(project)}, create=True)
    _maybe_rebind_renamed_project_ledger(project, path)
    _ensure_private_directory(path, "Cortex host project ledger")
    ensure_ledger_database(path)
    _record_host_project_identity(path, project)
    # Task capability files and optional exports remain beneath the same
    # private host store.  A worker receives only an exact capability path or
    # the scoped public artifact fallback; it never receives a browsable
    # workspace-local control directory.
    _ensure_private_directory(path / "tasks", "Cortex host task-artifact directory")
    _ensure_private_directory(path / "lanes", "Cortex host lane directory")
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
    """Stable label only; activations now reside in ``cortex.db``."""
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
        task_id = str(removed.get("task_id") or "") if isinstance(removed, dict) else ""
        if task_id:
            _revoke_coordinator_capability(root, task_id, reason="orchestration_deactivated")
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
        classification_params = dict(params)
        normalized_requirements = normalize_task_requirements(params.get("requirements"))
        classification_params["requirements"] = normalized_requirements
        result = classify(classification_params)
        receipt_id = f"classification-{secrets.token_hex(12)}"
        key = activation_key(params)
        payload = {
            "schema": SCHEMA,
            "classification_id": receipt_id,
            "activation_key": key,
            "complexity": result["complexity"],
            "requirements_digest": digest_text(json.dumps(normalized_requirements, sort_keys=True)),
            # The receipt is the authoritative classification contract.  Keep
            # the bounded task requirements here so init_task need not ask the
            # coordinator to reproduce a second, byte-identical copy.
            "requirements": normalized_requirements,
            "classification": result,
            "created_at": now(),
        }
        db_put_classification(root, payload)
        return {**result, "classification_id": receipt_id}


@contextlib.contextmanager
def state_lock(
    root: Path,
    *,
    timeout_seconds: float | None = STATE_LOCK_TIMEOUT_SECONDS,
    operation: str = "mutation",
    task_id: str | None = None,
) -> Iterator[None]:
    """Serialize one filesystem+SQLite mutation with a bounded acquisition.

    Every mutation now fails fast with a retryable ``ledger_busy`` signal by
    default.  A caller may explicitly pass ``timeout_seconds=None`` only for a
    narrowly controlled maintenance path that already owns the process-level
    coordination boundary.  The optional owner marker is diagnostic only and
    never participates in lock correctness.
    """
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
    started = time.monotonic()
    with lock_path.open("a+", encoding="utf-8") as stream:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        if fcntl is not None:
            if timeout_seconds is None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            else:
                deadline = started + max(0.0, float(timeout_seconds))
                while True:
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except (BlockingIOError, OSError) as exc:
                        if isinstance(exc, OSError) and exc.errno not in {errno.EACCES, errno.EAGAIN}:
                            raise
                        if time.monotonic() >= deadline:
                            raise LedgerBusyError(
                                operation,
                                int((time.monotonic() - started) * 1000),
                                holder=_read_state_lock_holder(root),
                            ) from None
                        time.sleep(min(0.01, max(0.001, deadline - time.monotonic())))
        owner_token = secrets.token_hex(12)
        _write_state_lock_holder(root, {
            "pid": os.getpid(),
            "operation": re.sub(r"[^a-z0-9_.-]", "_", str(operation).lower())[:64] or "mutation",
            "task_id": safe_id(str(task_id)) if task_id else None,
            "acquired_at": now(),
            "token": owner_token,
        })
        stack.append((root, stream))
        try:
            with db_transaction(root):
                yield
        finally:
            stack.pop()
            _clear_state_lock_holder(root, owner_token)
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
    path = _reject_symlink_ancestry(path, "dispatch briefing", allow_missing_leaf=True)
    if path.exists():
        existing = _read_private_text(path, "dispatch briefing", max_bytes=None)
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
    """Stable label only; task indexes now reside in ``cortex.db``."""
    return root / "cortex.db"


def read_task_index(root: Path) -> dict[str, Any]:
    return db_task_index(root)


def _host_session_bindings_path(root: Path) -> Path:
    """Stable label only; host session bindings now reside in SQLite."""
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

    Codex AttemptResults the parent session id, opaque native agent id, generic
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
            with state_lock(root):
                heartbeat_loaded = _v3_task_state(root, task_id)
                if heartbeat_loaded is None:
                    return {"bound": False, "reason": "task_unavailable"}
                heartbeat_task_dir, heartbeat_state, _ = heartbeat_loaded
                heartbeat_attempt = _attempt(heartbeat_state, str(attempt.get("attempt_id") or ""))
                if heartbeat_attempt.get("status") != "running":
                    return {"bound": False, "reason": "worker_no_longer_running"}
                heartbeat_at = now()
                lease_expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
                heartbeat_attempt["last_heartbeat_at"] = heartbeat_at
                heartbeat_attempt["worker_lease_expires_at"] = lease_expires_at
                heartbeat_attempt["lifecycle_status"] = "running_acknowledged"
                if isinstance(heartbeat_attempt.get("host_spawn"), dict):
                    heartbeat_attempt["host_spawn"]["lease_expires_at"] = lease_expires_at
                db_put_worker_session(root, {
                    "task_id": heartbeat_state["task_id"],
                    "attempt_id": str(heartbeat_attempt["attempt_id"]),
                    "host_agent_id": host_agent_id,
                    "host_task_name": host_task_name,
                    "host_tool": str((heartbeat_attempt.get("host_spawn") or {}).get("tool") or "spawn_agent"),
                    "status": "running",
                    "resumable": True,
                    "started_at": (heartbeat_attempt.get("host_spawn") or {}).get("confirmed_at"),
                })
                save_state(
                    heartbeat_task_dir,
                    heartbeat_task_dir / "state.sqlite",
                    heartbeat_state,
                    "host_heartbeat",
                    str(heartbeat_attempt["attempt_id"]),
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

    A worker that has already committed its canonical AttemptResult is never
    reclassified as failed merely because projection finalization is pending.
    A published result remains eligible for normal progression, and a worker
    paused on a durable question remains addressable through its persisted host
    identity. Only a stop with no result or question is terminal.
    """
    project = select_project_root({"project_root": str(project_root_value or "")})
    task_id = safe_id(str(task_id_value or ""))
    parent_session_id = safe_id(str(parent_session_id_value or ""))
    host_agent_id = str(host_agent_id_value or "").strip()
    if not HOST_AGENT_ID_RE.fullmatch(host_agent_id):
        return {"updated": False, "outcome": "host_agent_id_invalid", "reason": "host_agent_id_invalid"}
    root = ledger_root({"project_root": str(project)})
    with state_lock(root):
        bindings = _host_session_bindings(root)
        loaded = _v3_task_state(root, task_id)
        if loaded is None:
            return {"updated": False, "outcome": "task_unavailable", "reason": "task_unavailable"}
        task_dir, state, _ = loaded
        bound_parent_session = str(bindings.get("tasks", {}).get(task_id) or "").strip()
        state_parent_session = str(state.get("thread_id") or "").strip()
        if bound_parent_session != parent_session_id:
            # The task state is the durable, task-scoped authority.  A missing
            # global binding can be left behind by an interrupted host start or
            # compaction; restore it only when the hook supplies that exact
            # persisted parent session.  Never accept a different session.
            if not bound_parent_session and state_parent_session == parent_session_id:
                bindings["tasks"][task_id] = parent_session_id
                bindings["updated_at"] = now()
                db_put_global(root, "host_sessions", bindings)
            else:
                return {
                    "updated": False,
                    "outcome": "parent_session_mismatch",
                    "reason": "parent_session_mismatch",
                }
        matches = [
            item for item in state.get("attempts", [])
            if not item.get("invalidated")
            and str((item.get("host_spawn") or {}).get("agent_id") or "") == host_agent_id
        ]
        if len(matches) != 1:
            return {
                "updated": False,
                "outcome": "host_worker_identity_unavailable",
                "reason": "host_worker_identity_unavailable",
            }
        attempt = matches[0]
        attempt_id = str(attempt.get("attempt_id") or "")
        if attempt.get("status") in TERMINAL_ATTEMPT_STATUSES:
            return {
                "updated": False,
                "idempotent": True,
                "outcome": str(attempt.get("host_stop_outcome") or "attempt_already_terminal"),
                "reason": "attempt_already_terminal",
                "attempt_id": attempt_id,
                "status": attempt.get("status"),
            }
        if attempt.get("status") != "running":
            return {
                "updated": False,
                "outcome": "attempt_not_running",
                "reason": "attempt_not_running",
                "attempt_id": attempt_id,
            }

        stopped_at = now()
        open_questions = _open_blocking_questions(task_dir, state, attempt_id)
        canonical_result = attempt_protocol.get_attempt_result(
            root,
            task_id=state["task_id"],
            attempt_id=attempt_id,
        )
        finalization_pending = bool(
            canonical_result
            and canonical_result.get("status") == "completed"
            and canonical_result.get("lifecycle_status")
            in {attempt_protocol.LIFECYCLE_WORK_COMPLETED, attempt_protocol.LIFECYCLE_FINALIZING}
        )
        result_finalized = bool(
            canonical_result
            and canonical_result.get("lifecycle_status") == attempt_protocol.LIFECYCLE_COMPLETED
            and str(canonical_result.get("result_ref") or "")
        )
        attempt["host_stopped_at"] = stopped_at
        package = _delegation_package(task_dir, state["task_id"], attempt_id)
        if result_finalized:
            result_ref = str(canonical_result["result_ref"])
            attempt["lifecycle_status"] = attempt_protocol.LIFECYCLE_COMPLETED
            attempt.pop("worker_lease_expires_at", None)
            attempt["host_stop_outcome"] = "result_finalized"
            attempt["host_resumable"] = False
            attempt["attempt_result_ref"] = result_ref
            package["spawn_status"] = "stopped_after_result"
            package["host_stopped_at"] = stopped_at
            package["resumable"] = False
            package["attempt_result_ref"] = result_ref
            event = "host_stop_after_result"
            detail = f"{attempt_id}: {result_ref}"
            outcome = "result_finalized"
        elif open_questions:
            attempt["lifecycle_status"] = "paused_awaiting_user"
            attempt.pop("worker_lease_expires_at", None)
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
        elif finalization_pending:
            lifecycle = str(canonical_result["lifecycle_status"]).lower()
            attempt["lifecycle_status"] = lifecycle
            attempt.pop("worker_lease_expires_at", None)
            attempt["host_stop_outcome"] = "work_completed_finalization_pending"
            attempt["host_resumable"] = False
            attempt["attempt_result_ref"] = canonical_result.get("result_ref")
            package["spawn_status"] = "stopped_finalization_pending"
            package["host_stopped_at"] = stopped_at
            package["resumable"] = False
            package["attempt_result_ref"] = canonical_result.get("result_ref")
            event = "host_stop_finalization_pending"
            detail = f"{attempt_id}: {lifecycle}"
            outcome = "work_completed_finalization_pending"
        else:
            reason = "native_worker_stopped_without_result"
            attempt["status"] = "failed"
            attempt["finalized_at"] = stopped_at
            attempt["finalization_reason"] = reason
            attempt["host_stop_outcome"] = reason
            attempt["host_resumable"] = False
            attempt["lifecycle_status"] = "needs_recovery"
            attempt.pop("worker_lease_expires_at", None)
            package["spawn_status"] = "stopped_without_result"
            package["host_stopped_at"] = stopped_at
            package["resumable"] = False
            package["failure_reason"] = reason
            package["dispatch_ref"] = str(attempt.get("dispatch_ref") or "")
            event = "host_stop_without_result"
            detail = f"{attempt_id}: {reason}"
            outcome = reason
        session_status = (
            "completed" if result_finalized
            else "idle_resumable" if open_questions
            # The native child has stopped but finalization must be retried on
            # the exact persisted AttemptResult.  ``completion_pending`` is a
            # public lifecycle label, not a valid worker_sessions status;
            # storing it here previously failed the SQLite status boundary and
            # could leave a stale running row behind.
            else "stopped_recoverable" if finalization_pending
            else "terminated_unavailable"
        )
        db_put_worker_session(root, {
            "task_id": state["task_id"], "attempt_id": attempt_id,
            "host_agent_id": host_agent_id,
            "host_task_name": str((attempt.get("host_spawn") or {}).get("task_name") or ""),
            "host_tool": str((attempt.get("host_spawn") or {}).get("tool") or "spawn_agent"),
            "status": session_status, "resumable": bool(open_questions),
            "started_at": (attempt.get("host_spawn") or {}).get("confirmed_at"),
            **({"terminated_at": stopped_at} if result_finalized or not open_questions else {}),
        })
        _write_delegation_package(task_dir, state["task_id"], attempt_id, package)
        save_state(task_dir, task_dir / "state.sqlite", state, event, detail)
        return {
            "updated": True,
            "attempt_id": attempt_id,
            "outcome": outcome,
            "attempt_result_ref": str(canonical_result.get("result_ref") or "") if canonical_result else None,
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


def _tool_error_input_summary(value: Any, *, source: str) -> dict[str, Any]:
    """Describe the input shape without retaining any caller-supplied values."""
    try:
        byte_size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        byte_size = len(str(value).encode("utf-8", errors="replace"))
    if not isinstance(value, dict):
        return {"source": source, "kind": type(value).__name__, "byte_size": byte_size}
    fields = sorted(
        "<sensitive-field>" if _is_sensitive_log_key(str(key)) else redact(str(key), 128)
        for key in value
    )
    return {
        "source": source,
        "kind": "object",
        "field_count": len(fields),
        "fields": fields[:MAX_TOOL_ERROR_LOG_FIELDS],
        "fields_truncated": len(fields) > MAX_TOOL_ERROR_LOG_FIELDS,
        "sensitive_field_count": sum(1 for key in value if _is_sensitive_log_key(str(key))),
        "byte_size": byte_size,
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
        "status_receipt", "result_binding", "verification_id", "lane_id", "run_id",
        "host_agent_id", "turn_id",
    ):
        value = request_id if key == "id" else source.get(key)
        if value not in (None, ""):
            ids[key] = redact(value, 256)
    thread_id = source.get("thread_id") or request_dict.get("thread_id")
    session_id = source.get("session_id") or source.get("chat_session_id") or request_dict.get("session_id") or thread_id
    if arguments:
        input_value, input_source = arguments, "arguments"
    elif params:
        input_value, input_source = params, "params"
    else:
        input_value, input_source = raw_line, "raw_line"
    return {
        "method": redact(request_dict.get("method", ""), 128) or None,
        "tool": redact(params.get("name", ""), 128) or None,
        "chat_session_id": redact(session_id, 256) if session_id else None,
        "thread_id": redact(thread_id, 256) if thread_id else None,
        "request_id": redact(request_id, 256) if request_id is not None else None,
        "ids": ids,
        "input_summary": _tool_error_input_summary(input_value, source=input_source),
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

# AttemptResult shape and lifecycle are validated exclusively by
# cortex_runtime.attempt_protocol.


def _safe_project_relative_path(value: Any) -> str:
    """Validate a task-scoped project-relative path."""
    text = str(value).strip().replace("\\", "/")
    path = Path(text)
    if not text or path.is_absolute() or "\x00" in text or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path must be a safe project-relative path")
    normalized = path.as_posix()
    if normalized == ".codex/cortex" or normalized.startswith(".codex/cortex/"):
        raise ValueError("path must not include Cortex runtime state")
    return redact(normalized, MAX_JSON_BYTES)

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
    # Worker questions are canonical SQLite task documents.  Their optional
    # filesystem projection may legitimately be
    # absent after a fresh answer, so it cannot decide whether the worker has
    # received the material intent needed to resume.
    return [
        item for item in _question_records(question_bus_paths(task_dir), state)
        if item.get("status") == "answered" and bool(item.get("blocking", True))
    ]



def _planning_identifier(value: Any, label: str) -> str:
    identifier = str(value or "").strip()
    if not SAFE_ID_RE.fullmatch(identifier):
        raise ValueError(f"{label} must be a lowercase safe identifier")
    return identifier


def _planning_text(value: Any, label: str, *, maximum: int = MAX_JSON_BYTES) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    # Do not use ``redact(..., maximum)`` here: its character-count truncation
    # could make a hostile Unicode payload appear acceptable only after a
    # planner/scope worker had already produced it.  Planning and scoping are
    # durable JSON ingress, so enforce the advertised bound in UTF-8 bytes
    # without silently dropping semantic content.
    text = redact(value, None).strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text.encode("utf-8")) > maximum:
        raise ValueError(f"{label} exceeds the {maximum}-byte limit")
    return text


def _planning_string_list(value: Any, label: str, *, maximum: int | None = None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or (maximum is not None and len(value) > maximum):
        suffix = f" with at most {maximum} items" if maximum is not None else ""
        raise ValueError(f"{label} must be an array{suffix}")
    result = [_planning_text(item, f"{label} item") for item in value]
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
    """Validate the Planner Scope discovery brief without widening AttemptResult."""
    if persisted and isinstance(value, dict) and value.get("schema") == SCOPING_SCHEMA:
        value = {key: item for key, item in value.items() if key != "schema"}
    if not isinstance(value, dict) or set(value) != {"overview", "context_files", "discovery_domains"}:
        raise ValueError("scoping must contain exactly overview, context_files, and discovery_domains")
    overview = _planning_text(value.get("overview"), "scoping overview")
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
            "title": _planning_text(raw_domain.get("title"), "scoping discovery domain title"),
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


def _validate_latest_intent_coverage(
    task_definition: dict[str, Any],
    planning: dict[str, Any],
) -> None:
    """Require one coverage row for every current canonical requirement."""
    if not isinstance(task_definition, dict) or not isinstance(planning, dict):
        raise ValueError("latest-intent coverage requires task and planning objects")
    raw_requirements = task_definition.get("requirements", [])
    if not isinstance(raw_requirements, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_requirements
    ):
        raise ValueError("task requirements must be a current canonical text array")
    requirements = [item.strip() for item in raw_requirements]
    current_intent = str(task_definition.get("current_user_intent") or "").strip()
    if current_intent and current_intent not in requirements:
        requirements.append(current_intent)
    required = {" ".join(item.casefold().split()) for item in requirements}
    coverage = planning.get("requirement_coverage")
    if not required or not isinstance(coverage, list):
        return
    covered = {
        " ".join(str(item.get("requirement") or "").casefold().split())
        for item in coverage
        if isinstance(item, dict)
    }
    missing = sorted(required - covered)
    if missing:
        raise ValueError("planning requirement_coverage omits latest intent: " + "; ".join(missing))


def sanitize_planning_payload(value: Any, *, persisted: bool = False) -> dict[str, Any]:
    """Validate the Planner-only work-breakdown artifact without widening AttemptResult."""
    if persisted and isinstance(value, dict) and value.get("schema") == PLANNING_SCHEMA:
        value = {key: item for key, item in value.items() if key != "schema"}
    allowed_top_level = {
        "overview", "work_packages", "requirement_coverage", "recommendation",
        "recommendation_rationale", "resolved_questions", "risks",
    }
    if not isinstance(value, dict) or not {"overview", "work_packages"}.issubset(value) or set(value) - allowed_top_level:
        raise ValueError("planning must contain overview and work_packages, with only documented traceability fields")
    overview = _planning_text(value.get("overview"), "planning overview")
    recommendation = str(value.get("recommendation") or "approve").strip().lower()
    if recommendation not in {"approve", "revise"}:
        raise ValueError("planning recommendation must be approve or revise")
    recommendation_rationale = str(value.get("recommendation_rationale") or "").strip()
    if recommendation_rationale and len(recommendation_rationale.encode("utf-8")) > 4000:
        raise ValueError("planning recommendation_rationale is too long")
    raw_resolved_questions = value.get("resolved_questions", [])
    if not isinstance(raw_resolved_questions, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_resolved_questions
    ) or len(raw_resolved_questions) > 32:
        raise ValueError("planning resolved_questions must be an array of non-empty strings")
    resolved_questions = list(dict.fromkeys(str(item).strip() for item in raw_resolved_questions))
    raw_risks = value.get("risks", [])
    if not isinstance(raw_risks, list) or any(not isinstance(item, str) or not item.strip() for item in raw_risks):
        raise ValueError("planning risks must be an array of non-empty strings")
    risks = list(dict.fromkeys(str(item).strip() for item in raw_risks))
    if len(risks) > 64 or any(len(item.encode("utf-8")) > 2000 for item in risks):
        raise ValueError("planning risks exceed the supported bound")
    raw_coverage = value.get("requirement_coverage", [])
    if not isinstance(raw_coverage, list) or len(raw_coverage) > 100:
        raise ValueError("planning requirement_coverage must be an array with at most 100 items")
    coverage: list[dict[str, Any]] = []
    coverage_keys: set[str] = set()
    for index, item in enumerate(raw_coverage, 1):
        if not isinstance(item, dict):
            raise ValueError(f"planning requirement_coverage[{index - 1}] must be an object")
        unknown = sorted(set(item) - {"requirement", "plan_refs", "verification", "status"})
        missing = sorted({"requirement", "plan_refs", "verification", "status"} - set(item))
        if unknown or missing:
            details = ([] if not unknown else ["unknown: " + ", ".join(unknown)]) + ([] if not missing else ["missing: " + ", ".join(missing)])
            raise ValueError(f"planning requirement_coverage[{index - 1}] is invalid (" + "; ".join(details) + ")")
        requirement = _planning_text(item.get("requirement"), "planning coverage requirement")
        coverage_key = " ".join(requirement.casefold().split())
        if coverage_key in coverage_keys:
            raise ValueError("planning requirement_coverage requirements must be unique")
        coverage_keys.add(coverage_key)
        refs = item.get("plan_refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise ValueError(f"planning requirement_coverage[{index - 1}] plan_refs must be a non-empty array")
        refs_normalized = list(dict.fromkeys(str(ref).strip() for ref in refs))
        verification = _planning_string_list(item.get("verification"), "planning coverage verification")
        status = str(item.get("status") or "").strip().lower()
        if status != "covered":
            raise ValueError(f"planning requirement_coverage[{index - 1}] status must be covered")
        coverage.append({
            "requirement": requirement,
            "plan_refs": refs_normalized,
            "verification": verification,
            "status": "covered",
        })
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
        unknown = sorted(set(raw_package) - {"id", "title", "objective", "allowed_paths", "depends_on", "status", "order", "gates", "microtasks"})
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
            allowed = {"id", "title", "objective", "profile", "allowed_paths", "depends_on", "status", "order", "gates", "acceptance_criteria", "verification"}
            unknown_micro = sorted(set(raw_microtask) - allowed)
            missing_micro = sorted({"id", "title", "objective", "profile", "allowed_paths", "acceptance_criteria", "verification"} - set(raw_microtask))
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
            status = str(raw_microtask.get("status") or "pending").strip().lower()
            if status not in {"pending", "ready", "running", "blocked", "completed", "skipped"}:
                raise ValueError(f"planning microtask {microtask_id!r} has an invalid status")
            order = raw_microtask.get("order", micro_index)
            if isinstance(order, bool) or not isinstance(order, int) or order < 1:
                raise ValueError(f"planning microtask {microtask_id!r} order must be a positive integer")
            raw_gates = raw_microtask.get("gates", ["implementation"])
            if not isinstance(raw_gates, list) or not raw_gates or any(not str(item).strip() for item in raw_gates):
                raise ValueError(f"planning microtask {microtask_id!r} gates must be a non-empty array")
            gates = list(dict.fromkeys(canonical_pipeline_gate(item) for item in raw_gates))
            unknown_gates = sorted(set(gates) - AVAILABLE_GATES)
            if unknown_gates:
                raise ValueError(
                    f"planning microtask {microtask_id!r} references unknown gates: "
                    + ", ".join(unknown_gates)
                )
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
            microtask_paths = _planning_paths_list(raw_microtask.get("allowed_paths"), "planning microtask allowed_paths")
            if any(path in {".", "*"} for path in microtask_paths):
                raise ValueError(
                    f"planning microtask {microtask_id!r} allowed_paths must be explicit and non-broad"
                )
            microtasks.append({
                "id": microtask_id,
                "title": _planning_text(raw_microtask.get("title"), "planning microtask title"),
                "objective": _planning_text(raw_microtask.get("objective"), "planning microtask objective"),
                "profile": profile or None,
                "status": status,
                "order": order,
                "gates": gates,
                "allowed_paths": microtask_paths,
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
        package_status = str(raw_package.get("status") or "pending").strip().lower()
        if package_status not in {"pending", "ready", "running", "blocked", "completed", "skipped"}:
            raise ValueError(f"planning package {package_id!r} has an invalid status")
        package_order = raw_package.get("order", index)
        if isinstance(package_order, bool) or not isinstance(package_order, int) or package_order < 1:
            raise ValueError(f"planning package {package_id!r} order must be a positive integer")
        raw_package_gates = raw_package.get("gates", ["implementation"])
        if not isinstance(raw_package_gates, list) or not raw_package_gates or any(not str(item).strip() for item in raw_package_gates):
            raise ValueError(f"planning package {package_id!r} gates must be a non-empty array")
        package_gates = list(dict.fromkeys(canonical_pipeline_gate(item) for item in raw_package_gates))
        unknown_package_gates = sorted(set(package_gates) - AVAILABLE_GATES)
        if unknown_package_gates:
            raise ValueError(
                f"planning package {package_id!r} references unknown gates: "
                + ", ".join(unknown_package_gates)
            )
        packages.append({
            "id": package_id,
            "title": _planning_text(raw_package.get("title"), "planning package title"),
            "objective": _planning_text(raw_package.get("objective"), "planning package objective"),
            "status": package_status,
            "order": package_order,
            "gates": package_gates,
            "allowed_paths": _planning_paths_list(raw_package.get("allowed_paths"), "planning package allowed_paths"),
            "depends_on": dependencies,
            "microtasks": microtasks,
        })
    _validate_planning_dependency_graph(package_ids, {item["id"]: item["depends_on"] for item in packages}, "planning package")
    _validate_planning_dependency_graph(microtask_ids, microtask_dependencies, "planning microtask")
    valid_plan_refs = package_ids | microtask_ids
    for coverage_index, item in enumerate(coverage, 1):
        unknown_refs = sorted(set(item["plan_refs"]) - valid_plan_refs)
        if unknown_refs:
            raise ValueError(
                f"planning requirement_coverage[{coverage_index - 1}] references unknown plan items: "
                + ", ".join(unknown_refs)
            )
    result = {
        "schema": PLANNING_SCHEMA,
        "overview": overview,
        "work_packages": packages,
        "requirement_coverage": coverage,
        "recommendation": recommendation,
        "recommendation_rationale": recommendation_rationale,
        "resolved_questions": resolved_questions,
        "risks": risks,
    }
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
            f"Status: {package.get('status', 'pending')} | Order: {package.get('order', 1)} | Gates: {', '.join(package.get('gates') or ['implementation'])}",
            f"Dependencies: {dependency_text}", "", "Microtasks:",
        ))
        for microtask in package["microtasks"]:
            profile = f" ({microtask['profile']})" if microtask.get("profile") else ""
            lines.append(
                f"- `{microtask['id']}`{profile} [{microtask.get('status', 'pending')}; order {microtask.get('order', 1)}; gates {', '.join(microtask.get('gates') or ['implementation'])}]: {microtask['title']}"
            )
        lines.append("")
    coverage = manifest.get("requirement_coverage") or []
    if coverage:
        lines.extend(("## Requirement coverage", ""))
        for item in coverage:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('requirement')}: {', '.join(str(ref) for ref in item.get('plan_refs') or [])}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


PLAN_TRACKER_SCHEMA = "cortex/plan-tracker/v1"


def _plan_tracker_document(
    state: dict[str, Any],
    attempt: dict[str, Any],
    result_id: str,
    planning: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Build the one mutable task-local execution tracker.

    Immutable planner/package artifacts remain evidence. This document is the
    live coordination surface: every package and microtask has one row with
    ordering, dependencies, gates, acceptance, verification, and a lifecycle
    status that save_state advances after each gate transition.
    """
    rows: list[dict[str, Any]] = []
    for package in planning.get("work_packages", []):
        if not isinstance(package, dict):
            continue
        package_id = str(package.get("id") or "")
        rows.append({
            "kind": "package",
            "id": package_id,
            "title": package.get("title"),
            "objective": package.get("objective"),
            "status": package.get("status") or "pending",
            "order": package.get("order", 1),
            "gates": list(package.get("gates") or ["implementation"]),
            "depends_on": list(package.get("depends_on") or []),
            "allowed_paths": list(package.get("allowed_paths") or []),
            "acceptance_criteria": [],
            "verification": [],
            "package_id": package_id,
        })
        for microtask in package.get("microtasks", []):
            if not isinstance(microtask, dict):
                continue
            rows.append({
                "kind": "microtask",
                "id": str(microtask.get("id") or ""),
                "title": microtask.get("title"),
                "objective": microtask.get("objective"),
                "status": microtask.get("status") or "pending",
                "order": microtask.get("order", 1),
                "gates": list(microtask.get("gates") or ["implementation"]),
                "depends_on": list(microtask.get("depends_on") or []),
                "allowed_paths": list(microtask.get("allowed_paths") or package.get("allowed_paths") or []),
                "acceptance_criteria": list(microtask.get("acceptance_criteria") or []),
                "verification": list(microtask.get("verification") or []),
                "profile": microtask.get("profile"),
                "package_id": package_id,
            })
    rows.sort(key=lambda item: (int(item.get("order") or 1), 0 if item.get("kind") == "package" else 1, str(item.get("id") or "")))
    return {
        "schema": PLAN_TRACKER_SCHEMA,
        "task_id": state["task_id"],
        "revision": manifest.get("revision"),
        "source_result_ref": result_id,
        "source_attempt_id": attempt.get("attempt_id"),
        "task_revision": int(state.get("task_revision") or 1),
        "recommendation": manifest.get("recommendation", "approve"),
        "recommendation_rationale": manifest.get("recommendation_rationale", ""),
        "requirement_coverage": list(manifest.get("requirement_coverage") or []),
        "resolved_questions": list(manifest.get("resolved_questions") or []),
        "risks": list(manifest.get("risks") or []),
        "items": rows,
        "updated_at": now(),
        "last_event": "plan_created",
    }


def _sync_plan_tracker_document(task_dir: Path, state: dict[str, Any], *, event: str, detail: str) -> None:
    """Advance live tracker statuses from the durable gate state.

    Projection failures are intentionally non-fatal to the lifecycle commit;
    the immutable plan and state remain authoritative and the next save retries
    the same task-scoped document update.
    """
    root = _task_document_root(task_dir, str(state["task_id"]))
    tracker = db_get_task_document(root, str(state["task_id"]), "plan_tracker_current")
    if not isinstance(tracker, dict) or tracker.get("schema") != PLAN_TRACKER_SCHEMA:
        return
    done = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    active = set(active_gates(state))
    paused = {
        str(gate) for gate, value in (state.get("rework_pauses") or {}).items()
        if isinstance(value, dict) and value.get("status") == "needs_user_decision"
    }
    for item in tracker.get("items", []):
        if not isinstance(item, dict):
            continue
        gates = {canonical_pipeline_gate(gate) for gate in item.get("gates") or []}
        if gates and gates.issubset(done):
            item["status"] = "completed"
        elif gates.intersection(paused):
            item["status"] = "blocked"
        elif gates.intersection(active):
            item["status"] = "running"
        elif item.get("status") not in {"completed", "skipped"}:
            item["status"] = "pending"
        item["updated_at"] = now()
    tracker["task_revision"] = int(state.get("task_revision") or tracker.get("task_revision") or 1)
    tracker["state_revision"] = int(state.get("revision") or 0)
    tracker["last_event"] = event
    tracker["last_detail"] = redact(detail, 1000)
    tracker["updated_at"] = now()
    db_put_task_document(root, str(state["task_id"]), "plan_tracker_current", tracker)


def materialize_planning_payload(
    task_dir: Path,
    state: dict[str, Any],
    attempt: dict[str, Any],
    result_id: str,
    value: Any,
) -> dict[str, Any]:
    """Validate and atomically publish one finalized Planner work breakdown.

    The canonical AttemptResult remains the worker's semantic completion.  A
    plan is a separate, planner-only artifact family: immutable revision and
    package records are written in the same SQLite unit of work as the two
    mutable task documents that point at them.  A retry for the same result is
    therefore a no-op, while a failed validation or artifact write leaves no
    visible planning pointer or orphaned artifact.
    """
    planning = sanitize_planning_payload(value, persisted=True)
    result_ref = safe_id(str(result_id or ""))
    if not result_ref:
        raise ValueError("planning requires a canonical attempt result reference")
    task_id = safe_id(str(state.get("task_id") or ""))
    if not task_id:
        raise ValueError("planning requires a canonical task identity")
    root = _task_document_root(task_dir, task_id)
    existing = db_get_task_document(root, task_id, "planning_current")
    if isinstance(existing, dict) and existing.get("source_result_ref") == result_ref:
        return existing

    revision = f"plan-{result_ref}"
    revision_root = f"planning/revisions/{revision}"
    overview_path = f"{revision_root}/overview.md"
    summaries: list[dict[str, Any]] = []
    package_artifacts: list[tuple[dict[str, Any], str, str]] = []
    for package in planning["work_packages"]:
        package_id = str(package["id"])
        package_path = f"{revision_root}/packages/{package_id}.json"
        package_record = {
            "schema": PLANNING_SCHEMA,
            "revision": revision,
            "source_result_ref": result_ref,
            "package": package,
        }
        package_artifacts.append((package_record, package_id, package_path))
        summaries.append({
            "id": package_id,
            "title": package["title"],
            "depends_on": list(package.get("depends_on") or []),
            "microtask_count": len(package.get("microtasks") or []),
            "artifact_path": package_path,
        })

    manifest: dict[str, Any] = {
        "schema": PLANNING_SCHEMA,
        "revision": revision,
        "source_result_ref": result_ref,
        "source_attempt_id": str(attempt.get("attempt_id") or ""),
        "overview": planning["overview"],
        "overview_artifact_path": overview_path,
        "overview_artifact_ref": None,
        "work_packages": summaries,
        "requirement_coverage": list(planning.get("requirement_coverage") or []),
        "recommendation": planning.get("recommendation", "approve"),
        "recommendation_rationale": planning.get("recommendation_rationale", ""),
        "resolved_questions": list(planning.get("resolved_questions") or []),
        "risks": list(planning.get("risks") or []),
        "created_at": now(),
        "updated_at": now(),
    }
    overview = _planning_overview_markdown(planning)
    with db_transaction(root):
        overview_metadata = store_immutable_artifact(
            task_dir,
            task_id,
            kind="planning_revision",
            title=f"{revision}:overview",
            mime_type="text/markdown; charset=utf-8",
            content=overview,
            export_path=overview_path,
        )
        manifest["overview_artifact_ref"] = overview_metadata["artifact_ref"]
        for package_record, package_id, package_path in package_artifacts:
            metadata = store_immutable_artifact(
                task_dir,
                task_id,
                kind="planning_revision",
                title=f"{revision}:package:{package_id}",
                mime_type="application/json",
                content=json.dumps(package_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                export_path=package_path,
            )
            next(summary for summary in summaries if summary["id"] == package_id)["artifact_ref"] = metadata["artifact_ref"]
        tracker = _plan_tracker_document(state, attempt, result_ref, planning, manifest)
        db_put_task_document(root, task_id, "planning_current", manifest)
        db_put_task_document(root, task_id, "plan_tracker_current", tracker)
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


def current_plan_tracker(task_dir: Path, state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return the single mutable task-local plan tracker, if one exists."""
    task = load_task_definition(task_dir, state)
    root = _task_document_root(task_dir, str(task["task_id"]))
    value = db_get_task_document(root, str(task["task_id"]), "plan_tracker_current")
    if value is None:
        return None
    if value.get("schema") != PLAN_TRACKER_SCHEMA:
        raise ValueError("plan tracker schema is not supported")
    return value


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
    recommendation = redact(str(params.get("recommendation") or "").strip(), 1200)
    if not recommendation:
        raise ValueError(
            "worker question recommendation is required and must explain why the suggested answer is safest or best"
        )
    raw_recommended_ids = params.get("recommended_option_ids")
    if isinstance(raw_recommended_ids, str):
        raw_recommended_ids = [raw_recommended_ids]
    recommended_option_ids = [safe_id(str(value)) for value in (raw_recommended_ids or [])]
    recommended_answer = redact(str(params.get("recommended_answer") or "").strip(), 1200)
    option_ids = {item["option_id"] for item in options}
    if options:
        if not recommended_option_ids:
            raise ValueError("choice questions require recommended_option_ids")
        if any(value not in option_ids for value in recommended_option_ids):
            raise ValueError("recommended_option_ids must reference defined question options")
        if not multiple and len(recommended_option_ids) != 1:
            raise ValueError("single-select questions require exactly one recommended option")
        if len(recommended_option_ids) != len(set(recommended_option_ids)):
            raise ValueError("recommended_option_ids must be unique")
        if recommended_answer:
            raise ValueError("choice questions use recommended_option_ids, not recommended_answer")
    else:
        if recommended_option_ids:
            raise ValueError("text questions use recommended_answer, not recommended_option_ids")
        if not recommended_answer:
            raise ValueError("text questions require a concrete recommended_answer")
    return {
        "header": header,
        "options": options,
        "multiple": multiple,
        "custom_label": custom_label,
        "custom_response": True,
        "recommendation": recommendation,
        "recommended_option_ids": recommended_option_ids,
        "recommended_answer": recommended_answer,
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
    require_internal_english(config["recommendation"], "worker question recommendation")
    require_internal_english(config["recommended_answer"], "worker question recommended_answer")
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
    # unanswered single question: neither may allow a result or wave advance.
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


def _resolved_user_decisions(task_dir: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every canonical answered user decision for automatic result carry-forward.

    Question storage is attempt-scoped for resumability, but user authority is
    task-scoped. Results and replacement dispatches consume this projection so
    a new worker cannot lose an answer merely because it uses a new attempt or
    invents a different question key.
    """
    decisions: list[dict[str, Any]] = []
    for record in _question_records(question_bus_paths(task_dir), state):
        if record.get("status") != "answered":
            continue
        question = redact(str(record.get("question") or "").strip(), 2000)
        answer_en = str(record.get("answer_en_text") or "").strip()
        if not answer_en and str(record.get("answer_original_language") or "en").lower().startswith("en"):
            answer_en = str(record.get("answer_text") or "").strip()
        answer_en = redact(answer_en, 4000)
        if not question or not answer_en:
            continue
        source_ref = safe_id(str(record.get("question_id") or ""))
        decision = {
            "source_type": "question",
            "source_ref": source_ref,
            "question_key": source_ref,
            "question_en": question,
            "answer_en": answer_en,
            "answer_option_ids": [safe_id(str(item)) for item in record.get("answer_option_ids") or []],
            "answered_at": record.get("answered_at"),
        }
        decision["decision_digest"] = digest_text(json.dumps(
            {key: decision[key] for key in ("source_type", "source_ref", "question_key", "question_en", "answer_en", "answer_option_ids")},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ))
        decisions.append(decision)

    document_root = _task_document_root(task_dir, str(state["task_id"]))
    for document_key, batch in db_list_task_documents(document_root, str(state["task_id"]), "question_batch:"):
        batch_id = str(batch.get("batch_id") or "")
        if (
            batch.get("schema") != "cortex/question-batch/v1"
            or document_key != "question_batch:" + batch_id
            or batch.get("task_id") != state["task_id"]
            or batch.get("status") != "answered"
        ):
            continue
        answers = batch.get("answers") if isinstance(batch.get("answers"), dict) else {}
        for question in batch.get("questions") or []:
            if not isinstance(question, dict):
                continue
            question_key = safe_id(str(question.get("question_key") or ""))
            answer = answers.get(question_key)
            question_en = redact(str(question.get("canonical_question") or "").strip(), 2000)
            answer_en = redact(str((answer or {}).get("answer_en") or "").strip(), 4000)
            if not question_key or not isinstance(answer, dict) or not question_en or not answer_en:
                continue
            source_ref = safe_id(batch_id)
            decision = {
                "source_type": "question_batch",
                "source_ref": source_ref,
                "question_key": question_key,
                "question_en": question_en,
                "answer_en": answer_en,
                "answer_option_ids": [safe_id(str(item)) for item in answer.get("answer_option_ids") or []],
                "answered_at": answer.get("answered_at") or batch.get("answered_at"),
            }
            decision["decision_digest"] = digest_text(json.dumps(
                {key: decision[key] for key in ("source_type", "source_ref", "question_key", "question_en", "answer_en", "answer_option_ids")},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ))
            decisions.append(decision)
    decisions.sort(key=lambda item: (str(item.get("answered_at") or ""), item["source_ref"], item["question_key"]))
    return decisions


def _read_private_text(path: Path, label: str, *, max_bytes: int | None) -> str:
    """Read one private regular UTF-8 file without following links.

    ``None`` is reserved for immutable dispatch briefings. Their public
    transport is cursor-paged, so they must not acquire a second hidden
    persistence/read quota after they have been accepted for dispatch.
    """
    if max_bytes is not None and (
        isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0
    ):
        raise ValueError(f"{label} size limit is invalid")
    path = _reject_symlink_ancestry(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if max_bytes is not None and info.st_size > max_bytes:
            raise ValueError(f"{label} is oversized")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise ValueError(f"{label} is oversized")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    finally:
        os.close(descriptor)


def _receipt_identity(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("schema"), value.get("receipt_id"), value.get("result_id"), value.get("task_id"),
        value.get("gate"), value.get("attempt_id"), value.get("content_digest"),
    )



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



def _is_knowledge_harvest_task(task: dict[str, Any]) -> bool:
    routing_text = "\n".join(_task_routing_items(task)).lower()
    return re.search(r"(?<![a-z0-9])harvest(?:-refresh)?(?![a-z0-9])", routing_text) is not None


def _is_external_lifecycle_only_task(task: dict[str, Any]) -> bool:
    """Recognize the narrow no-project-change Cortex continuation workflow.

    A task that merely asks Cortex to continue a referenced Codex task in a
    newly-created ledger record is an external control-plane operation.  It
    still needs discovery, planning, review, documentation, and closure
    evidence, but an implementation writer cannot truthfully satisfy its
    required project-file delta.  Keep this detection deliberately narrow:
    an explicit Codex thread reference and ledger/lifecycle-task creation are
    both required, and any explicit project mutation keeps the normal route.
    """
    routing_text = _implementation_routing_text(task)
    if not re.search(r"\bcodex://threads/[a-z0-9-]+\b", routing_text):
        return False
    has_lifecycle_operation = bool(re.search(
        r"\b(?:ledger|lifecycle|orchestration)\b|\b(?:леджер|леджере|оркестрац\w*)\b",
        routing_text,
    ))
    has_task_creation = bool(re.search(
        r"\b(?:create|continue|resume)\b.{0,120}\b(?:task|thread)\b"
        r"|\b(?:созда\w*|продолж\w*)\b.{0,120}\b(?:задач\w*|тред\w*)\b",
        routing_text,
    ))
    has_project_mutation = bool(re.search(
        r"\b(?:code|source|repository|repo|file|implement\w*|fix\w*|patch\w*|"
        r"test\w*|build\w*|deploy\w*|config\w*)\b"
        r"|\b(?:код\w*|исходник\w*|репозитори\w*|файл\w*|исправ\w*|"
        r"реализ\w*|тест\w*|сборк\w*|конфиг\w*)\b",
        routing_text,
    ))
    return has_lifecycle_operation and has_task_creation and not has_project_mutation


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
    try:
        _sync_plan_tracker_document(task_dir, state, event=event, detail=detail)
    except (OSError, ValueError, TypeError, sqlite3.Error):
        # The state transition is canonical; a mutable tracker projection is
        # retried on the next lifecycle save and must not make a completed
        # gate disappear because SQLite briefly rejected the secondary write.
        pass
    append_journal_best_effort(task_dir, event, detail)
    return state


def next_gate(state: dict[str, Any]) -> str | None:
    done = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    return next((gate for gate in state["current_pipeline"] if gate not in done), None)


def active_gates(state: dict[str, Any]) -> list[str]:
    """Return the unfinished, unpaused gates in the first executable wave.

    A no-progress pause belongs to one corrective gate, not to the task.  A
    sibling in the same parallel wave (and later independent waves) must keep
    running while that gate awaits a Planner-backed recovery decision.
    """
    done = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    pauses = state.get("rework_pauses")
    paused = {
        str(gate)
        for gate, pause in pauses.items()
        if isinstance(pause, dict) and pause.get("status") == "needs_user_decision"
    } if isinstance(pauses, dict) else set()
    groups = state.get("parallel_groups") or [[gate] for gate in state["current_pipeline"]]
    for group in groups:
        unfinished = [gate for gate in group if gate not in done]
        if not unfinished:
            continue
        pending = [gate for gate in unfinished if gate not in paused]
        # Waves are sequential dependency boundaries. A paused gate may not
        # let a later wave leapfrog it, but its unpaused siblings are still
        # independently executable in this wave.
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


def canonicalize_full_governance_pipeline(
    state: dict[str, Any],
    pipeline: list[Any],
) -> list[str]:
    """Return the one executable ordering for a full-governance pipeline.

    Governance phases are server-owned lifecycle boundaries, rather than
    coordinator-selected ordinary work.  Keeping that rule in one helper is
    essential because normal starts, future-wave replacements, semantic
    revisions, and corrective rework all eventually change the same pipeline.
    In particular, a retained completed implementation must never be allowed
    to precede a reintroduced activation review.
    """
    current = normalize_pipeline(pipeline)
    governance = state.get("governance") if isinstance(state.get("governance"), dict) else {}
    if governance.get("effective_mode") != "full":
        return current
    missing = set(GOVERNANCE_FULL_GATES) - set(current)
    if missing:
        raise ValueError(
            "full governance pipelines must retain activation and close review gates"
        )
    if "close" not in current:
        raise ValueError("full governance pipelines must retain the final close gate")

    # Preserve the relative order of ordinary work, but make the server-owned
    # envelope unambiguous.  This is deliberately a normalization rather than
    # a late validation failure: replacement/revision requests are allowed to
    # describe ordinary work, but cannot place lifecycle authority around it.
    ordinary = [
        gate for gate in current
        if gate not in {*GOVERNANCE_FULL_GATES, "close"}
    ]
    return ["governance_activation", *ordinary, "governance_close", "close"]


def canonicalize_full_governance_parallel_groups(
    state: dict[str, Any],
    groups: Any,
    pipeline: list[str],
) -> list[list[str]]:
    """Keep full-governance boundaries singleton waves around ordinary work."""
    normalized = normalize_parallel_groups(groups, pipeline)
    governance = state.get("governance") if isinstance(state.get("governance"), dict) else {}
    if governance.get("effective_mode") != "full":
        return normalized
    ordinary_groups = [
        [gate for gate in group if gate not in {*GOVERNANCE_FULL_GATES, "close"}]
        for group in normalized
    ]
    return [
        ["governance_activation"],
        *[group for group in ordinary_groups if group],
        ["governance_close"],
        ["close"],
    ]


def validate_pipeline_invariants(state: dict[str, Any], pipeline: list[str] | None = None) -> None:
    candidate = pipeline or state["current_pipeline"]
    governance = state.get("governance") if isinstance(state.get("governance"), dict) else {}
    if governance.get("effective_mode") == "full":
        missing_governance = set(GOVERNANCE_FULL_GATES) - set(candidate)
        if missing_governance:
            raise ValueError("full governance pipelines must retain activation and close review gates")
        if "close" not in candidate:
            raise ValueError("full governance pipelines must retain the final close gate")
        if candidate[0] != "governance_activation":
            raise ValueError("full governance activation must be the first pipeline gate")
        if candidate[-2:] != ["governance_close", "close"]:
            raise ValueError("full governance close must immediately precede the final close gate")
    if state.get("require_handoff"):
        missing = {"documentation", "close"} - set(candidate)
        if missing:
            raise ValueError("C2/C3 pipelines must retain documentation and close gates")
        if candidate.index("documentation") > candidate.index("close"):
            raise ValueError("documentation must run before close")
    normalize_parallel_groups(state.get("parallel_groups"), candidate)


_GOVERNANCE_OBLIGATION_DEFAULTS = {
    "minimal": ("verification_evidence", "audit_receipt"),
    "light": (
        "policy_snapshot",
        "decision_assumption_risk_evidence",
        "process_reflection",
        "verification_evidence",
    ),
    "full": (
        "acceptance_oracle_evidence",
        "risk_register",
        "falsification_strategy",
        "independent_governance_review",
        "retrospective",
        "verification_evidence",
        "audit_receipt",
    ),
}


def _governance_obligations_for_gate(state: dict[str, Any], gate: str) -> tuple[str, ...]:
    governance = state.get("governance") if isinstance(state.get("governance"), dict) else {}
    mode = str(governance.get("effective_mode") or "minimal").strip().lower()
    if mode not in _GOVERNANCE_OBLIGATION_DEFAULTS:
        return ()
    if gate == "governance_activation":
        return _GOVERNANCE_OBLIGATION_DEFAULTS["full"][:4] if mode == "full" else ()
    if gate not in {"governance_close", "close"}:
        return ()
    if mode == "minimal":
        return ()
    configured = governance.get("close_obligations")
    if isinstance(configured, (list, tuple)) and all(str(item).strip() for item in configured):
        return tuple(dict.fromkeys(str(item).strip() for item in configured))
    return _GOVERNANCE_OBLIGATION_DEFAULTS[mode]


def validate_governance_obligation_evidence(
    state: dict[str, Any],
    gate: str,
    gate_evidence: list[dict[str, Any]] | None = None,
    *,
    artifact_root: Path | None = None,
) -> None:
    """Require typed, scoped, immutable evidence for governance gates.

    ``close_obligations`` is a policy declaration, not completion evidence.
    Each obligation must be attached to a durable evidence id, digest, and
    governance scope.  The evidence itself must also carry the server-created
    immutable artifact binding.  When ``artifact_root`` is available (the
    normal gate path), the binding is resolved against SQLite and the stored
    JSON is checked for the same task/gate/attempt identity.  The independent
    review obligation additionally carries a distinct reviewer identity so a
    worker cannot self-approve governance.
    """
    required = _governance_obligations_for_gate(state, gate)
    if not required:
        return
    governance = state.get("governance") if isinstance(state.get("governance"), dict) else {}
    expected_scope = str(
        governance.get("initiative_ref")
        or governance.get("autonomous_scope_ref")
        or "governance-scope-autonomous"
    ).strip()
    evidence = gate_evidence if gate_evidence is not None else state.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []
    seen: dict[str, dict[str, Any]] = {}
    for item in evidence:
        if not isinstance(item, dict) or item.get("invalidated"):
            continue
        obligations = item.get("governance_obligations")
        if isinstance(obligations, str):
            obligations = [obligations]
        elif not isinstance(obligations, (list, tuple, set)):
            obligations = []
        obligations = {str(value).strip() for value in obligations if str(value).strip()}
        kind = str(item.get("kind") or "").strip()
        if kind in required:
            obligations.add(kind)
        scope = str(item.get("governance_scope_ref") or item.get("scope_ref") or "").strip()
        if scope != expected_scope:
            continue
        artifact_ref = str(item.get("artifact_ref") or "").strip()
        artifact_digest = str(item.get("artifact_digest") or "").strip().lower()
        artifact_bound = (
            bool(re.fullmatch(r"artifact-[0-9a-f]{32}", artifact_ref))
            and bool(re.fullmatch(r"[0-9a-f]{64}", artifact_digest))
            and item.get("artifact_immutable") is True
            and item.get("artifact_verified") is True
        )
        task_id = str(state.get("task_id") or "").strip()
        evidence_id = str(item.get("evidence_id") or "").strip()
        if artifact_bound and task_id and evidence_id:
            expected_artifact_ref = "artifact-" + hashlib.sha256(
                f"{task_id}\0evidence\0evidence/{evidence_id}.json\0{artifact_digest}".encode("utf-8")
            ).hexdigest()[:32]
            artifact_bound = artifact_ref == expected_artifact_ref
        if artifact_bound and artifact_root is not None:
            try:
                metadata = db_get_artifact_metadata(artifact_root, task_id, artifact_ref) if task_id else None
                canonical = db_read_artifact_content(artifact_root, task_id, artifact_ref) if metadata else None
                if not metadata or not metadata.get("immutable") or metadata.get("digest_sha256") != artifact_digest:
                    artifact_bound = False
                elif isinstance(canonical, bytes):
                    canonical = canonical.decode("utf-8")
                parsed = json.loads(canonical) if isinstance(canonical, str) else None
                artifact_bound = artifact_bound and isinstance(parsed, dict) and all(
                    parsed.get(key) == item.get(key)
                    for key in ("task_id", "gate", "attempt_id", "evidence_id", "kind")
                )
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                artifact_bound = False
        if (
            not item.get("evidence_id")
            or not str(item.get("digest") or "").strip()
            or not artifact_bound
        ):
            continue
        for obligation in obligations:
            if obligation in required:
                seen.setdefault(obligation, item)
    missing = [item for item in required if item not in seen]
    if missing:
        raise ValueError(
            f"{gate} requires typed governance obligation evidence: " + ", ".join(missing)
        )
    review = seen.get("independent_governance_review")
    if review is not None:
        reviewer = str(
            review.get("reviewer_identity")
            or review.get("reviewer_id")
            or review.get("reviewer")
            or ""
        ).strip()
        reviewer_role = str(review.get("reviewer_role") or review.get("reviewer_role_name") or "").strip().lower()
        owner = str(governance.get("owner") or state.get("principal") or "").strip()
        if (
            not reviewer
            or reviewer == owner
            or review.get("independent_reviewer") is False
            or reviewer_role not in {"code_reviewer", "reviewer"}
        ):
            raise ValueError("independent_governance_review requires a distinct code_reviewer identity")
    verification = seen.get("verification_evidence")
    if verification is not None and not (
        verification.get("verified_execution") is True
        # Evidence is caller-controlled JSON; avoid set membership so a
        # malformed array becomes a normal validation error rather than an
        # unhashable-value exception at the boundary.
        and verification.get("exit_code") in (0, None)
    ):
        raise ValueError("verification_evidence requires server-verified successful execution")
    receipt = seen.get("audit_receipt")
    if receipt is not None and not str(receipt.get("attempt_result_ref") or "").strip():
        raise ValueError("audit_receipt requires a canonical attempt_result_ref")


def validate_completion_invariants(
    state: dict[str, Any],
    *,
    artifact_root: Path | None = None,
) -> None:
    validate_pipeline_invariants(state)
    # A task cannot become terminal merely because a gate projection says so.
    # A facade-managed worker is authoritative only after its exact canonical
    # AttemptResult has finished finalization.  Keep this check ahead of the
    # governance-only return below: C1/C2 terminal transitions are equally
    # unsafe if a live host child has not produced a durable result yet.
    active_facade_attempts = [
        item for item in state.get("attempts", [])
        if isinstance(item, dict)
        and item.get("facade_managed")
        and not item.get("invalidated")
        and item.get("status") not in TERMINAL_ATTEMPT_STATUSES
    ]
    if active_facade_attempts:
        if artifact_root is None:
            raise ValueError(
                "active_attempt_result_pending: canonical ledger root is required"
            )
        task_dir = db_task_artifact_path(artifact_root, str(state.get("task_id") or ""))
        if task_dir is None:
            raise ValueError(
                "active_attempt_result_pending: task artifact directory is unavailable"
            )
        pending_attempts = _active_facade_attempts_missing_finalized_results(
            task_dir, active_facade_attempts
        )
        if pending_attempts:
            raise ValueError(
                "active_attempt_result_pending: " + ", ".join(pending_attempts)
            )
    # A finalized/blocked/failed canonical result also ends the exact native
    # worker lifecycle, even while the task projection is still active pending
    # coordinator consumption.  Prevent an old ``awaiting_spawn`` or
    # ``running`` worker-session row from being rehydrated as a second live
    # worker, or from silently surviving a terminal close.
    terminal_result_attempts = [
        item for item in state.get("attempts", [])
        if isinstance(item, dict)
        and item.get("facade_managed")
        and not item.get("invalidated")
    ]
    if terminal_result_attempts:
        if artifact_root is None:
            raise ValueError(
                "terminal_attempt_session_unreconciled: canonical ledger root is required"
            )
        task_dir = db_task_artifact_path(artifact_root, str(state.get("task_id") or ""))
        if task_dir is None:
            raise ValueError(
                "terminal_attempt_session_unreconciled: task artifact directory is unavailable"
            )
        live_terminal_sessions = _terminal_facade_attempts_with_live_sessions(
            task_dir, terminal_result_attempts
        )
        if live_terminal_sessions:
            raise ValueError(
                "terminal_attempt_session_unreconciled: " + ", ".join(live_terminal_sessions)
            )
    # The active-attempt guard intentionally excludes terminal rows, but a
    # malformed or historical state can already contain a facade row projected
    # as ``passed`` without its canonical result ever completing. Do not let
    # that projection survive a recovery/replay path and become terminal merely
    # because it no longer counts as active. Non-success terminal attempts
    # legitimately have no successful AttemptResult, so this backstop is
    # scoped to successful facade rows only.
    passed_facade_attempts = [
        item for item in state.get("attempts", [])
        if isinstance(item, dict)
        and item.get("facade_managed")
        and not item.get("invalidated")
        and item.get("status") == "passed"
    ]
    if passed_facade_attempts:
        if artifact_root is None:
            raise ValueError(
                "passed_attempt_result_unfinalized: canonical ledger root is required"
            )
        task_dir = db_task_artifact_path(artifact_root, str(state.get("task_id") or ""))
        if task_dir is None:
            raise ValueError(
                "passed_attempt_result_unfinalized: task artifact directory is unavailable"
            )
        missing_passed_results = _attempts_missing_result_validation(
            task_dir, passed_facade_attempts
        )
        if missing_passed_results:
            raise ValueError(
                "passed_attempt_result_unfinalized: " + ", ".join(missing_passed_results)
            )
    governance = state.get("governance") if isinstance(state.get("governance"), dict) else {}
    governance_full = governance.get("effective_mode") == "full"
    if not state.get("require_handoff") and not governance_full:
        return
    attempts = [item for item in state.get("attempts", []) if not item.get("invalidated")]
    non_terminal = [item["attempt_id"] for item in attempts if item.get("status") not in TERMINAL_ATTEMPT_STATUSES]
    if non_terminal:
        raise ValueError("governed completion requires every attempt to be terminal: " + ", ".join(non_terminal))
    evidence = [item for item in state.get("evidence", []) if not item.get("invalidated")]
    missing_evidence = [
        item["attempt_id"]
        for item in attempts
        if item.get("status") == "passed"
        if not any(record.get("attempt_id") == item["attempt_id"] for record in evidence)
    ]
    if missing_evidence:
        raise ValueError("governed completion requires evidence for every attempt: " + ", ".join(missing_evidence))
    missing_results = [
        item["attempt_id"]
        for item in attempts
        if item.get("status") == "passed"
        if not any(
            record.get("attempt_id") == item["attempt_id"]
            and record.get("attempt_result_ref") == item.get("attempt_result_ref")
            for record in evidence
        )
    ]
    if missing_results:
        raise ValueError("governed completion requires canonical result-bound evidence for every attempt: " + ", ".join(missing_results))
    # An intermediate worker may truthfully complete its narrow assignment
    # while handing unresolved work to a successor.  Terminal acceptance must
    # not retroactively reject those ordinary results.  It does, however, need
    # a final fail-closed backstop for current closure-verifier rows in case a
    # recovery path encounters an immutable canonical result that escaped the
    # gate-time check.
    closure_gates = {"governance_close", "close"}
    if "review" in state.get("current_pipeline", []):
        closure_gates.add("review")
    closure_attempts = [
        item for item in attempts
        if item.get("facade_managed")
        and item.get("status") == "passed"
        and item.get("gate") in closure_gates
    ]
    if closure_attempts:
        if artifact_root is None:
            raise ValueError("closure_attempt_unresolved: canonical ledger root is required")
        task_dir = db_task_artifact_path(artifact_root, str(state.get("task_id") or ""))
        if task_dir is None:
            raise ValueError("closure_attempt_unresolved: task artifact directory is unavailable")
        unresolved_closure_attempts = _attempts_with_unresolved_canonical_results(
            task_dir, closure_attempts
        )
        if unresolved_closure_attempts:
            raise ValueError(
                "closure_attempt_unresolved: " + ", ".join(unresolved_closure_attempts)
            )
    if state.get("require_handoff"):
        if "documentation" not in state.get("completed_gates", []) or not state.get("documentation_receipt"):
            raise ValueError("C2/C3 completion requires documentation decision evidence")
        if "close" not in state.get("completed_gates", []):
            raise ValueError("C2/C3 completion requires the close gate")
        if not state.get("reassessment_receipts"):
            raise ValueError("C2/C3 completion requires a reassessment receipt")
        if not state.get("handoff_created") or state.get("handoff_gate") != "close":
            raise ValueError("C2/C3 completion requires a final close handoff")
    if governance_full:
        completed = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
        missing_governance = sorted(set(GOVERNANCE_FULL_GATES) - completed)
        if missing_governance:
            raise ValueError("full governance completion requires activation and close review gates: " + ", ".join(missing_governance))
    # Full and light governance obligations are enforced from actual evidence
    # receipts, not merely from the resolver's metadata list.
    if governance_full:
        validate_governance_obligation_evidence(
            state, "governance_close", evidence, artifact_root=artifact_root
        )
    elif governance.get("effective_mode") == "light":
        validate_governance_obligation_evidence(
            state, "close", evidence, artifact_root=artifact_root
        )


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
    current = canonicalize_full_governance_pipeline(
        state,
        pipeline if pipeline is not None else previous,
    )
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
        current = canonicalize_full_governance_pipeline(state, current)
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
    groups = canonicalize_full_governance_parallel_groups(
        state,
        groups,
        current,
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
    if "plan" in reset_gates:
        invalidate_plan_approval_for_reopened_plan(
            state,
            reason=reason,
            reset_gates=sorted(reset_gates),
        )


def invalidate_plan_approval_for_reopened_plan(
    state: dict[str, Any],
    *,
    reason: str,
    reset_gates: list[str] | None = None,
    event: str = "plan_reopened",
) -> bool:
    """Retire a required approval when pipeline work reopens ``plan``.

    Pipeline mutations and their approval invalidation must share the caller's
    durable state transaction.  Keeping this state-only helper beside
    ``append_pipeline_change`` makes every generic and internal rework path
    use one rule, while callers still emit their ordinary single state event.
    Historical approval evidence is retained under ``history`` only; none of
    its request or basis fields remain eligible for the replacement plan.
    """
    approval = _plan_approval(state)
    if approval.get("policy") != "required":
        return False

    previous = {
        key: json.loads(json.dumps(approval[key]))
        for key in (
            "status", "review", "plan_result_ref", "pending_basis",
            "approved_basis", "request_id", "requested_at", "approved_at",
            "feedback",
        )
        if key in approval
    }
    approval.setdefault("history", []).append({
        "event": event,
        "at": now(),
        "reason": redact(reason, 2000),
        "reset_gates": list(reset_gates or ["plan"]),
        "previous": previous,
    })
    for key in (
        "review", "plan_result_ref", "pending_basis", "approved_basis",
        "request_id", "requested_at", "approved_at",
    ):
        approval.pop(key, None)
    approval.update({"policy": "required", "status": "pending_plan", "feedback": None})
    state["plan_approval"] = approval
    return True


def invalidate_reworked_result_bindings(task_dir: Path, state: dict[str, Any]) -> None:
    """Rework invalidates state attempts; canonical result rows remain immutable."""
    del task_dir, state


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
    requirements_values = normalize_task_requirements(params.get("requirements"))
    requirements = " ".join(item.lower() for item in requirements_values)
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
        "user_request": params.get("user_request", ""),
        "requirements": params.get("requirements", []),
    })
    roles = {"scope": ["planner"], "plan": ["planner"], "discover": ["explorer"], "architecture": ["architect"], "database_architecture": ["database_architect"], "implementation": [implementation_selection["profile"]], "qa": ["qa_engineer", "build_verification"], "security": ["security_auditor"], "performance": ["performance_engineer"], "accessibility": ["accessibility_engineer"], "ux": ["ux_designer"], "review": ["code_reviewer"], "documentation": ["technical_writer"], "close": ["build_verification"]}
    return {"complexity": complexity, "base_pipeline": BASE_PIPELINES[complexity], "pipeline": pipeline, "parallel_groups": parallel_groups, "pipeline_source": pipeline_source, "pipeline_corrections": pipeline_corrections, "conditional_gates": additions, "conditional_gate_reasons": addition_reasons, "available_gates": sorted(AVAILABLE_GATES), "suggested_roles": {gate: roles.get(gate, profiles_for_gate(gate)) for gate in pipeline}, "implementation_selection": implementation_selection}


def init_task(params: dict[str, Any]) -> dict[str, Any]:
    if "objective" in params:
        raise ValueError("init_task does not accept objective; use user_request")
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
            requested_user_request = str(params.get("user_request", "")).strip()
            stored_user_request = str(task_definition.get("user_request", "")).strip()
            if not requested_user_request:
                raise ValueError("init_task requires the exact non-empty user request")
            if requested_user_request != stored_user_request:
                raise ValueError("existing task_id belongs to a different user_request")
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
            return {"created": False, "resumed": True, "task_id": task_id, "state": existing, "ledger_root": str(root)}
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
        if "requirements" not in receipt:
            raise ValueError("classification receipt requirements are invalid; classify the task again")
        try:
            receipt_requirements = normalize_task_requirements(receipt.get("requirements"))
        except ValueError as exc:
            raise ValueError("classification receipt requirements are invalid; classify the task again") from exc
        # Receipts created before the ingress invariant can be safely
        # atomized here: classification consumes the same text in the same
        # order, while task persistence must never reintroduce an oversized
        # canonical requirement.  Keep the receipt digest aligned with its
        # authoritative stored representation before consumption.
        receipt["requirements"] = receipt_requirements
        receipt["requirements_digest"] = digest_text(
            json.dumps(receipt_requirements, sort_keys=True)
        )
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
        user_language = normalize_user_language(params.get("user_language"), params.get("user_request", ""))
        visible_thread_requested = params.get("visible_thread_requested", False)
        if not isinstance(visible_thread_requested, bool):
            raise ValueError("visible_thread_requested must be a boolean")
        plan_approval_policy = str(params.get("plan_approval") or "auto")
        if plan_approval_policy not in {"auto", "required"}:
            raise ValueError("plan_approval must be auto or required")
        init_acceptance_criteria = normalize_init_text_list(params.get("acceptance_criteria"), "acceptance_criteria")
        init_scope = normalize_init_text_list(params.get("scope"), "scope")
        init_constraints = normalize_init_text_list(params.get("constraints"), "constraints")
        init_allowed_paths = normalize_init_text_list(params.get("allowed_paths"), "allowed_paths")
        init_verification = normalize_init_text_list(params.get("verification"), "verification")
        init_pause_conditions = normalize_init_text_list(params.get("pause_conditions"), "pause_conditions")
        follow_up = params.get("follow_up") if isinstance(params.get("follow_up"), dict) else None
        baseline_ref = store_manifest_snapshot(task_dir, baseline)
        exact_user_request = str(params.get("user_request") or "").strip()
        if not exact_user_request:
            raise ValueError("init_task requires the exact non-empty user request")
        exact_user_request_digest = digest_text(exact_user_request)
        task = {"schema": SCHEMA, "pipeline_contract_version": PIPELINE_CONTRACT_VERSION, "task_id": task_id, "task_number": task_number, "user_request": exact_user_request, "user_request_digest": exact_user_request_digest, "user_request_projection": redact(exact_user_request, 4000), "intent_clarification_required": bool(params.get("intent_clarification_required", False)), "intent_clarification_reason": redact(params.get("intent_clarification_reason", ""), 500) or None, "complexity": classification["complexity"], "base_pipeline": classification["base_pipeline"], "initial_pipeline": pipeline, "parallel_groups": parallel_groups, "requirements": receipt_requirements, "constraints": [redact(item, 1000) for item in init_constraints], "acceptance_criteria": [redact(item, 1000) for item in init_acceptance_criteria], "scope": [redact(item, 500) for item in init_scope], "allowed_paths": [redact(item, 500) for item in init_allowed_paths], "verification": [redact(item, 1000) for item in init_verification], "budget": redact(params.get("budget", ""), 500), "pause_conditions": [redact(item, 1000) for item in init_pause_conditions], "plan_approval": plan_approval_policy, "initiative_ref": redact(params.get("initiative_ref", ""), 200) or None, "governance_mode": str(params.get("governance_mode") or "auto"), "governance": sanitize_structured(params.get("governance")) if isinstance(params.get("governance"), dict) else None, "thread_id": redact(thread_id, 256), "principal": principal, "user_language": user_language, "communication_profile": select_communication_profile(params), "visible_thread_requested": visible_thread_requested, "internal_language": "en", "classification_id": classification_id, "project_root": baseline["project_root"], "initial_manifest_ref": baseline_ref, "tracker_policy": TRACKER_POLICY, "created_at": now()}
        if follow_up is not None:
            task["follow_up"] = sanitize_structured(follow_up)
        state = {"schema": SCHEMA, "pipeline_contract_version": PIPELINE_CONTRACT_VERSION, "task_id": task_id, "task_number": task_number, "status": "active", "principal": principal, "thread_id": redact(thread_id, 256), "user_language": user_language, "communication_profile": select_communication_profile(params), "visible_thread_requested": visible_thread_requested, "internal_language": "en", "complexity": classification["complexity"], "initiative_ref": redact(params.get("initiative_ref", ""), 200) or None, "governance_mode": str(params.get("governance_mode") or "auto"), "governance": sanitize_structured(params.get("governance")) if isinstance(params.get("governance"), dict) else None, "current_pipeline": pipeline, "pipeline_obligations": list(pipeline), "parallel_groups": parallel_groups, "current_gates": active_gates({"current_pipeline": pipeline, "parallel_groups": parallel_groups, "completed_gates": [], "skipped_gates": []}), "completed_gates": [], "skipped_gates": [], "gates": {}, "attempts": [], "evidence": [], "locks": {}, "pipeline_changes": [], "adaptive_events": [], "recovery_events": [], "resume_events": [], "reassessment_receipts": [], "documentation_receipt": None, "manifest_receipts": [], "initial_manifest_ref": baseline_ref, "initial_manifest_digest": baseline["digest"], "manifest_snapshot_cleanup": {"status": "active", "at": now()}, "classification_receipt": classification_id, "handoff_created": False, "replan_count": 0, "replan_limit": int(params.get("replan_limit", 2)), "require_delegation": classification["complexity"] in {"C2", "C3"}, "require_handoff": classification["complexity"] in {"C2", "C3"}, "plan_approval": {"policy": plan_approval_policy, "status": "not_required" if plan_approval_policy == "auto" else "pending_plan", "history": []}, "coordinator": activation["coordinator"], "parent_project_operations": activation["parent_project_operations"], "worker_visibility": activation["worker_visibility"], "worker_return_route": activation["worker_return_route"], "revision": 0, "updated_at": now()}
        artifact_relative = str(task_dir.relative_to(root))
        db_create_task(root, task, state, artifact_relative)
        intent_artifact = store_immutable_artifact(
            task_dir,
            task_id,
            kind="user_intent",
            title="intent/user-request.txt",
            mime_type="text/plain; charset=utf-8",
            content=exact_user_request,
            export_path="intent/user-request.txt",
        )
        task["user_intent_artifact_ref"] = intent_artifact["artifact_ref"]
        task["user_intent_artifact_path"] = "intent/user-request.txt"
        task["user_intent_byte_size"] = intent_artifact["byte_size"]
        if intent_artifact["digest_sha256"] != exact_user_request_digest:
            raise ValueError("immutable user-intent artifact digest does not match the exact request")
        db_update_task_definition(root, task)
        from cortex_runtime.projection_service import enqueue as enqueue_projection
        intent_projection = enqueue_projection(
            root=root,
            task_id=task_id,
            artifact_id=intent_artifact["artifact_ref"],
            projection_type="user_intent",
            export_path="intent/user-request.txt",
            required=False,
        )
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
    task mutations.  A worker result is the one scoped exception: after the
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
        available_result_refs = {
            safe_id(str(item.get("attempt_result_ref") or ""))
            for item in current_state.get("attempts", [])
            if isinstance(item, dict) and str(item.get("attempt_result_ref") or "").strip()
        }
        for index, raw in enumerate(specs):
            if not isinstance(raw, dict):
                return {"recorded": False, "reason": "invalid_batch_spec", "index": index, "prepared": [], "recoverable": True}
            gate = str(raw.get("gate") or (current_wave[0] if current_wave else "")).strip()
            if gate not in current_wave:
                return {"recorded": False, "reason": "batch_requires_one_gate", "current_gates": current_wave, "index": index, "prepared": [], "recoverable": True}
            context_result_refs = [safe_id(str(item)) for item in raw.get("context_result_refs", [])]
            if (
                len(context_result_refs) != len(set(context_result_refs))
                or not set(context_result_refs).issubset(available_result_refs)
            ):
                return {
                    "recorded": False,
                    "atomic": True,
                    "reason": "partial_failure",
                    "index": index,
                    "error": "context_result_refs must be unique canonical AttemptResult references from this task",
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
        # A host child can satisfy this attempt only when it was started from
        # this exact server-issued dispatch.  Quietly correcting an arbitrary
        # host task name here would let a coordinator-created generic child be
        # attached to the pending Cortex attempt after the fact.  Keep the
        # attempt awaiting the one durable dispatch instead.
        if host_task_name != expected_task_name:
            return {
                "confirmed": False,
                "attempt_id": attempt_id,
                "reason": "host_task_name_mismatch",
                "next_action": (
                    "invoke the exact returned Cortex dispatch.call with its unmodified dispatch.arguments; "
                    "a generic host spawn cannot satisfy this attempt"
                ),
                "recoverable": True,
                "expected_task_name": expected_task_name,
                "revision_correction": revision_correction,
                "state": state,
            }
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
        task_name_correction = None
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
            "lease_expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
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
            attempt["host_agent_id"] = host_agent_id
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
        # Retain the server-observed native identity directly on the attempt
        # projection so compaction recovery never loses its exact wait target.
        attempt["host_agent_id"] = host_agent_id
        attempt["model_verification"] = model_verification
        attempt["dispatch_correlation"] = "coordinator_recorded_host_spawn"
        attempt["status"] = "running"
        attempt["lifecycle_status"] = "running_acknowledged"
        attempt["last_heartbeat_at"] = host_spawn["confirmed_at"]
        attempt["worker_lease_expires_at"] = host_spawn["lease_expires_at"]
        attempt.pop("spawn_lease_expires_at", None)
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
        # The canonical artifact is written before its catalog binding is
        # attached to the task state.  Keep that binding server-owned while
        # reconciling the immutable evidence body; it is intentionally not a
        # self-referential field inside the content-addressed JSON.
        for binding_key in (
            "artifact_ref", "artifact_digest", "artifact_immutable", "artifact_verified"
        ):
            if binding_key in indexed and binding_key not in record:
                record[binding_key] = indexed[binding_key]
        if indexed != record:
            raise ValueError(f"SQLite evidence record failed reconciliation: {evidence_id}")
        validated.append({**record, **({"invalidated": True} if invalidated else {})})
    return validated


def finalize_attempt(params: dict[str, Any]) -> dict[str, Any]:
    """Explicitly close a host-completed attempt when it cannot publish a canonical result.

    A facade-managed worker may use this escape hatch only for a terminal
    non-success. A successful worker must first complete and finalize its
    canonical AttemptResult through ``complete_attempt``; otherwise a mutable
    ``status=passed`` projection could bypass the active-attempt guard after
    it became terminal.
    """
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
        if status == "passed" and attempt.get("facade_managed"):
            missing_result = _attempts_missing_result_validation(task_dir, [attempt])
            if missing_result:
                return {
                    "recorded": False,
                    "attempt_id": attempt_id,
                    "status": status,
                    "reason": "passed_attempt_result_required",
                    "next_action": "complete_attempt",
                    "required_fields": ["attempt_result_ref"],
                    "recoverable": True,
                    "revision_correction": revision_correction,
                    "state": state,
                }
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
    """Return facade attempts without a finalized canonical AttemptResult."""
    task = load_task_definition(task_dir)
    missing: list[str] = []
    for attempt in attempts:
        if not attempt.get("facade_managed"):
            continue
        result_ref = str(attempt.get("attempt_result_ref") or "")
        result = attempt_protocol.get_attempt_result(
            _task_document_root(task_dir, str(task["task_id"])),
            task_id=str(task["task_id"]), attempt_id=str(attempt.get("attempt_id") or ""),
        )
        if (
            not result_ref
            or result is None
            or result.get("result_ref") != result_ref
            or result.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
        ):
            missing.append(str(attempt.get("attempt_id")))
    return missing


def _active_facade_attempts_missing_finalized_results(
    task_dir: Path,
    attempts: list[dict[str, Any]],
) -> list[str]:
    """Return live public-worker attempts without a finalized canonical result.

    A native child binding is a recovery checkpoint, not successful work.  It
    lets the host wait for the exact child after compaction, but it must never
    authorize a handoff or terminal state in place of the child's finalized
    ``AttemptResult``.  Keep the lookup against SQLite rather than trusting
    the mutable state projection or an exported result view.
    """
    active_attempts = [
        attempt for attempt in attempts
        if isinstance(attempt, dict)
        and attempt.get("facade_managed")
        and not attempt.get("invalidated")
        and attempt.get("status") not in TERMINAL_ATTEMPT_STATUSES
    ]
    if not active_attempts:
        return []
    task = load_task_definition(task_dir)
    pending: list[str] = []
    for attempt in active_attempts:
        attempt_id = str(attempt.get("attempt_id") or "")
        result_ref = str(attempt.get("attempt_result_ref") or "")
        result = attempt_protocol.get_attempt_result(
            _task_document_root(task_dir, str(task["task_id"])),
            task_id=str(task["task_id"]),
            attempt_id=attempt_id,
        )
        if (
            not result_ref
            or result is None
            or result.get("result_ref") != result_ref
            or result.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
        ):
            pending.append(attempt_id)
    return pending


def _terminal_facade_attempts_with_live_sessions(
    task_dir: Path,
    attempts: list[dict[str, Any]],
) -> list[str]:
    """Return terminal-result facade attempts whose exact session is still live.

    A task attempt can remain active until its coordinator consumes a result;
    that does *not* keep its native worker live.  The canonical result and the
    server-observed ``worker_sessions`` row must agree before any gate or
    terminal transition proceeds.  Missing session rows are violations too:
    recovery must never invent a native identity after the fact.
    """
    relevant = [
        attempt for attempt in attempts
        if isinstance(attempt, dict)
        and attempt.get("facade_managed")
        and not attempt.get("invalidated")
    ]
    if not relevant:
        return []
    task = load_task_definition(task_dir)
    task_id = str(task["task_id"])
    root = _task_document_root(task_dir, task_id)
    sessions_by_attempt: dict[str, list[dict[str, Any]]] = {}
    for session in db_list_worker_sessions(root, task_id):
        sessions_by_attempt.setdefault(str(session.get("attempt_id") or ""), []).append(session)
    violations: list[str] = []
    for attempt in relevant:
        attempt_id = str(attempt.get("attempt_id") or "")
        result_ref = str(attempt.get("attempt_result_ref") or "")
        if not attempt_id or not result_ref:
            continue
        result = attempt_protocol.get_attempt_result(root, task_id=task_id, attempt_id=attempt_id)
        if (
            result is None
            or str(result.get("result_ref") or "") != result_ref
            or str(result.get("lifecycle_status") or "")
            not in attempt_protocol.TERMINAL_LIFECYCLES
        ):
            continue
        sessions = sessions_by_attempt.get(attempt_id, [])
        if not sessions or any(
            str(session.get("status") or "") != "completed"
            or bool(session.get("resumable"))
            or not session.get("terminated_at")
            for session in sessions
        ):
            violations.append(attempt_id)
    return violations


def _attempts_with_unresolved_canonical_results(task_dir: Path, attempts: list[dict[str, Any]]) -> list[str]:
    """Return supplied facade attempts with an unresolved finalized result.

    Callers deliberately supply only closure-verifier attempts.  A
    ``completed`` semantic result is scoped to one worker assignment and may
    retain successor handoff items; this helper must never be used as a global
    historical-result scan.
    """
    task = load_task_definition(task_dir)
    unresolved_attempts: list[str] = []
    for attempt in attempts:
        if not attempt.get("facade_managed"):
            continue
        attempt_id = str(attempt.get("attempt_id") or "")
        result_ref = str(attempt.get("attempt_result_ref") or "")
        result = attempt_protocol.get_attempt_result(
            _task_document_root(task_dir, str(task["task_id"])),
            task_id=str(task["task_id"]), attempt_id=attempt_id,
        )
        if (
            result_ref
            and result is not None
            and result.get("result_ref") == result_ref
            and result.get("lifecycle_status") == attempt_protocol.LIFECYCLE_COMPLETED
            and bool(result.get("unresolved"))
        ):
            unresolved_attempts.append(attempt_id)
    return unresolved_attempts



def _record_evidence_locked(task_dir: Path, state: dict[str, Any], params: dict[str, Any], verified: bool = False, execution: dict[str, Any] | None = None) -> dict[str, Any]:
    gate = str(params["gate"])
    if gate not in active_gates(state):
        raise ValueError(f"cannot add evidence for non-active gate '{gate}'")
    attempt_id = params.get("attempt_id")
    if state.get("require_delegation") and not attempt_id:
        raise ValueError("C2/C3 evidence must be linked to a delegation attempt")
    if attempt_id and not any(item["attempt_id"] == attempt_id and item["gate"] == gate and item["status"] in {"running", "passed"} for item in state["attempts"]):
        raise ValueError("evidence attempt_id does not belong to a running or passed attempt for the current gate")
    # A technical-writer result is the documentation decision evidence even
    # when the coordinator labels the accompanying command/check as generic
    # verification.  Normalize that safe, gate-bound case so a read-only
    # documentation audit cannot fail at the gate transition merely because
    # the evidence kind was omitted by the host model.
    evidence_kind = str(params.get("kind", "")).strip()
    if gate == "documentation" and attempt_id:
        attempt = next((item for item in state.get("attempts", []) if item.get("attempt_id") == attempt_id), None)
        if attempt and attempt.get("agent") == "technical_writer" and evidence_kind in DOCUMENTATION_EVIDENCE_KINDS:
            params = {**params, "kind": "documentation"}
    attempt_result_ref = None
    if state.get("require_delegation"):
        delegated_attempt = _attempt(state, safe_id(str(attempt_id or "")))
        attempt_result_ref = str(delegated_attempt.get("attempt_result_ref") or "").strip() or None
        if not attempt_result_ref:
            raise ValueError("canonical attempt_result_ref is required for delegated evidence")
    summary = str(params.get("summary", "")).strip()
    if not summary:
        raise ValueError("evidence summary is required")
    evidence_id = f"evidence-{len(state['evidence']) + 1:04d}"
    kind = redact(params.get("kind", "result"), 64)
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
    raw_obligations = params.get("governance_obligations")
    if isinstance(raw_obligations, str):
        governance_obligations = [raw_obligations.strip()] if raw_obligations.strip() else []
    elif isinstance(raw_obligations, (list, tuple, set)):
        governance_obligations = [str(item).strip() for item in raw_obligations if str(item).strip()]
    else:
        governance_obligations = []
    if kind in {
        "acceptance_oracle_evidence", "risk_register", "falsification_strategy",
        "independent_governance_review", "retrospective", "verification_evidence",
        "audit_receipt", "policy_snapshot", "decision_assumption_risk_evidence", "process_reflection",
    } and kind not in governance_obligations:
        governance_obligations.append(kind)
    state_governance = state.get("governance") if isinstance(state.get("governance"), dict) else {}
    # Preserve the normal C2 evidence workflow while making the light-mode
    # obligations explicit in the durable receipt.  These are only safe
    # aliases for the corresponding server-observed gate evidence; callers
    # cannot claim them by setting arbitrary completion metadata.
    if state_governance.get("effective_mode") == "light":
        if gate == "documentation" or kind == "documentation":
            if "policy_snapshot" not in governance_obligations:
                governance_obligations.append("policy_snapshot")
        if kind not in {"command", "documentation"}:
            for obligation in ("decision_assumption_risk_evidence", "process_reflection"):
                if obligation not in governance_obligations:
                    governance_obligations.append(obligation)
        if kind == "command" and verified and (exit_code is None or int(exit_code) == 0):
            if "verification_evidence" not in governance_obligations:
                governance_obligations.append("verification_evidence")
    governance_scope_ref = str(
        params.get("governance_scope_ref")
        or params.get("scope_ref")
        or state_governance.get("initiative_ref")
        or state_governance.get("autonomous_scope_ref")
        or "governance-scope-autonomous"
    ).strip()
    evidence = {
        "evidence_id": evidence_id,
        "task_id": state["task_id"],
        "gate": gate,
        "attempt_id": attempt_id,
        "attempt_result_ref": attempt_result_ref,
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
        "governance_obligations": governance_obligations,
        "governance_scope_ref": governance_scope_ref,
        "reviewer_identity": redact(params.get("reviewer_identity", ""), 256) or None,
        "reviewer_role": redact(params.get("reviewer_role", ""), 128) or None,
        "independent_reviewer": bool(params.get("independent_reviewer")) if "independent_reviewer" in params else None,
        "created_at": now(),
    }
    artifact = store_immutable_artifact(
        task_dir,
        state["task_id"],
        kind="evidence",
        title=f"evidence/{evidence_id}.json",
        mime_type="application/json",
        content=json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        export_path=f"evidence/{evidence_id}.json",
    )
    # Governance close/activation validation must be able to resolve the
    # evidence back to canonical SQLite content.  These fields are produced
    # only after the immutable artifact write and are never accepted from the
    # public evidence payload.
    evidence["artifact_ref"] = artifact["artifact_ref"]
    evidence["artifact_digest"] = artifact["digest_sha256"]
    evidence["artifact_immutable"] = bool(artifact["immutable"])
    evidence["artifact_verified"] = True
    write_json(task_dir / "evidence" / f"{evidence_id}.json", evidence)
    state["evidence"].append(evidence)
    if kind == "documentation":
        state["documentation_receipt"] = {"evidence_id": evidence_id, "attempt_id": attempt_id, "decision": decision, "justification": evidence["justification"]}
    for attempt in state["attempts"]:
        if attempt["attempt_id"] == attempt_id:
            attempt["evidence_ids"].append(evidence_id)
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
        result = _record_evidence_locked(task_dir, state, resolved)
        result["inferred"] = {
            "gate": params.get("gate") != resolved.get("gate"),
            "attempt_id": not bool(params.get("attempt_id")),
            "attempt_result_ref": not bool(params.get("attempt_result_ref")),
        }
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
        attempt_id = str(resolved.get("attempt_id") or "").strip()
        protocol_root = ledger_root({"project_root": str(base)})
        existing_result = (
            attempt_protocol.get_attempt_result(
                protocol_root,
                task_id=str(state["task_id"]),
                attempt_id=attempt_id,
            )
            if attempt_id else None
        )
        observation_key = (
            f"verification_observed:{attempt_id}:{verification_id}"
            if attempt_id else None
        )
        # A trusted verification command is an idempotent operation for one
        # dispatched attempt and command id.  On a retry after the durable
        # observation was committed, do not run an extra command or mint a
        # second fact.  This also makes a retry after a response loss safe.
        if observation_key:
            prior = next(
                (
                    event for event in attempt_protocol.list_attempt_events(
                        protocol_root,
                        task_id=str(state["task_id"]),
                        attempt_id=attempt_id,
                    )
                    if event.get("event_key") == observation_key
                ),
                None,
            )
            if prior is not None:
                payload = prior.get("payload") if isinstance(prior.get("payload"), dict) else {}
                return {
                    "recorded": True,
                    "evidence_id": str(payload.get("evidence_id") or "") or None,
                    "evidence": payload.get("server_execution_receipt"),
                    "verification_observation": prior,
                    "execution": {
                        "exit_code": payload.get("exit_code"),
                        "stdout": "",
                        "stderr": "",
                    },
                    "idempotent": True,
                    "revision_correction": revision_correction,
                }
        try:
            completed = subprocess.run(argv, cwd=cwd, env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}, text=True, capture_output=True, timeout=timeout, check=False)
            exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code, stdout, stderr = 124, exc.stdout or "", exc.stderr or "verification timed out"
        # A live delegated attempt has no AttemptResult yet.  Its immutable
        # server observation is therefore the durable execution receipt; the
        # successor later resolves the same task+attempt to its finalized
        # AttemptResult.  Requiring a result ref here would make the protocol
        # impossible because completion closes the event stream.
        if attempt_id and existing_result is None:
            if exit_code != 0:
                raise ValueError("trusted verification command failed; no verification_observed event was recorded")
            relative_cwd = str(cwd.relative_to(base)) or "."
            observed_at = now()
            receipt_id = "verification-execution-" + digest_text(
                json.dumps(
                    {
                        "task_id": state["task_id"],
                        "attempt_id": attempt_id,
                        "verification_id": verification_id,
                        "argv": argv,
                        "cwd": relative_cwd,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )[:24]
            observation_payload = {
                "schema": "cortex/verification-observation/v1",
                "verification_id": verification_id,
                "server_execution_receipt": {
                    "receipt_id": receipt_id,
                    "task_id": state["task_id"],
                    "attempt_id": attempt_id,
                    "command": list(argv),
                    "cwd": relative_cwd,
                    "exit_code": exit_code,
                    "path_set": [relative_cwd],
                    "observed_at": observed_at,
                    "stdout_digest": digest_text(stdout),
                    "stderr_digest": digest_text(stderr),
                },
                # ``tests`` remains the bounded HandoffCompiler input.  It
                # contains only server-observed command/exit facts, never a
                # worker claim or raw command output.
                "tests": [{"command": " ".join(argv), "cwd": relative_cwd, "exit_code": exit_code}],
                "command": " ".join(argv),
                "cwd": relative_cwd,
                "exit_code": exit_code,
                "paths": [relative_cwd],
                "observed_at": observed_at,
            }
            observed = attempt_protocol.record_verification_observation(
                protocol_root,
                task_id=str(state["task_id"]),
                attempt_id=attempt_id,
                payload=observation_payload,
                event_key=observation_key,
            )
            return {
                "recorded": True,
                "verification_observation": observed["event"],
                "execution": {
                    "exit_code": exit_code,
                    "stdout": redact(stdout, 4000),
                    "stderr": redact(stderr, 4000),
                },
                "idempotent": bool(observed.get("idempotent")),
                "revision_correction": revision_correction,
            }
        evidence_params = {**resolved, "kind": "command", "command": verification_id, "exit_code": exit_code}
        result = _record_evidence_locked(task_dir, state, evidence_params, verified=True, execution={"argv": argv, "cwd": str(cwd.relative_to(base)), "stdout": stdout, "stderr": stderr, "exit_code": exit_code})
        result["execution"] = {"exit_code": exit_code, "stdout": redact(stdout, 4000), "stderr": redact(stderr, 4000)}
        result["revision_correction"] = revision_correction
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
    elif "result binding" in reason.lower():
        next_action = "repair_result_binding_then_retry_commit_gate_once"
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

    Validation failures inside this private atomic adapter are returned as
    bounded recovery data instead of escaping as repeated MCP errors. After
    three failures for the same gate/mode the adapter is durably blocked,
    giving the coordinator a terminal handoff path. This limit is intentionally
    unrelated to pipeline, QA, review, worker, finding-remediation, or closure
    rework attempts.
    """
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        mode = str(params.get("mode") or "verification").strip().lower()
        requested_gate = canonical_pipeline_gate(params.get("gate") or primary_gate(state) or "")
        # Host adapters may retry a completed composite call after a timeout.
        # Treat that exact gate transition as idempotent instead of trying to
        # consume a one-use result receipt a second time and opening a false
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
            gate_commit = record_gate({
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
        return {"recorded": True, "atomic": True, "mode": mode, "outcome": outcome, "evidence": evidence, "gate": gate_commit, "state": gate_commit["state"]}


def close_audit(params: dict[str, Any]) -> dict[str, Any]:
    """Summarize finalized canonical AttemptResults at close time."""
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        # A transaction replay can arrive after the final gate was already
        # projected. Re-run the invariant against SQLite rather than treating
        # that projection as proof of durable worker completion.
        validate_completion_invariants(state, artifact_root=root)
        result_refs = [
            safe_id(str(item.get("attempt_result_ref") or ""))
            for item in state.get("attempts", [])
            if isinstance(item, dict) and str(item.get("attempt_result_ref") or "").strip()
        ]
        return {
            "atomic": True,
            "result_refs": result_refs,
            "result_count": len(result_refs),
            "state": state,
        }


def resume_task(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        if state["status"] != "blocked":
            raise ValueError("only blocked tasks can be resumed")
        state["status"] = "active"
        state.pop("blocked_reason", None)
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
        invalidate_reworked_result_bindings(task_dir, state)
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
        # ``replan_count`` is lifetime audit data, not an execution budget.
        # Public material replans are already tied to durable worker evidence,
        # plan approval, idempotent operation receipts, and bounded per-gate /
        # per-strategy recovery.  A task-wide cap made long but progressing
        # tasks impossible to finish after two legitimate review findings.
        # Task-wide limits never block an evidence-backed replan.
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
            invalidate_reworked_result_bindings(task_dir, state)
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
        pending_attempt_ids = _active_facade_attempts_missing_finalized_results(
            task_dir,
            [
                item for item in state.get("attempts", [])
                if isinstance(item, dict)
                and not item.get("invalidated")
                and item.get("status") not in TERMINAL_ATTEMPT_STATUSES
            ],
        )
        if pending_attempt_ids:
            # A generic handoff is a completed coordination artifact, not a
            # replacement for the recovery-only context handoff.  Do not
            # persist it while a host child is still missing its canonical
            # result: a later coordinator could otherwise mistake the task
            # for safely handed off and stop waiting for that exact child.
            active_host_checkpoints = [
                item["attempt_id"]
                for item in state.get("attempts", [])
                if isinstance(item, dict)
                and item.get("attempt_id") in pending_attempt_ids
                and str((item.get("host_spawn") or {}).get("agent_id") or "").strip()
            ]
            return {
                "recorded": False,
                "reason": "active_attempt_result_pending",
                "recoverable": True,
                "next_action": "wait_for_exact_worker_or_recover_attempt",
                "candidate_attempt_ids": pending_attempt_ids,
                # This is deliberately an opaque attempt identifier rather
                # than a native child id. The child identity stays in the
                # host-private recovery context.
                "active_host_checkpoint_attempt_ids": active_host_checkpoints,
                "state": state,
            }
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
PUBLIC_ORCHESTRATION_SCHEMA = "cortex/orchestration/v5"
COORDINATOR_LOCK = (
    "COORDINATOR LOCK: root is coordination-only. Never inspect, search, read, edit, build, test, or run the target project, "
    "Cortex plugin source/cache, .codex state, or runtime internals. The public MCP schema and this response are authoritative. "
    "Use only Cortex lifecycle, exact dispatches, waiting, result evaluation, user communication, and safe recovery. "
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
        "gate_outcomes", "future_waves", "allow_rework", "reason", "rework_gate",
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
            diagnostics.append(_request_diagnostic("task", "start requires a task object", "an object containing task_id, user_request, and complexity"))
        else:
            require_identifier("task.task_id", task.get("task_id"))
            if not isinstance(task.get("user_request"), str) or not task.get("user_request", "").strip():
                diagnostics.append(_request_diagnostic("task.user_request", "task.user_request is required", "the exact non-empty user-authored task"))
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
                "objective", "ownership", "context_files", "context_result_refs", "context_gates", "allowed_paths",
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
                diagnostics.append(_request_diagnostic("completions", "every completion must be an object", "completion objects with attempt_id and result_ref"))

    elif operation in {"lane", "resource", "question", "plan_approval"} and not isinstance(params.get("payload"), dict):
        diagnostics.append(_request_diagnostic("payload", f"{operation} requires an operation-specific payload object", "an object with a supported command"))

    return diagnostics


def orchestrate(params: dict[str, Any]) -> dict[str, Any]:
    """Internal engine facade retained for v5 lifecycle composition."""
    from cortex_runtime.orchestration_engine import orchestrate as _orchestrate

    return _orchestrate(params)


# These helpers remain importable for the v5 projection and compaction module,
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


def _auto_handoff(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    next_action: str,
) -> dict[str, Any]:
    """Expose the engine's automatic handoff helper to existing v5 tests."""
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


def _v3_start_state_blocked_error(message: object) -> dict[str, Any]:
    """Return a terminal start result when no task was safely created.

    A registry instable happens before Cortex can reserve a task or
    return an opaque task reference.  Treating it as ordinary caller input led
    coordinators to call unscoped recovery, which could select an unrelated
    active task under the same project root.
    """
    result = _v3_error("start_state_incompatible", message, outcome="blocked")
    result["retryable"] = False
    result["task_created"] = False
    result["recovery"] = "user_authorized_ledger_maintenance_required"
    result["next_action"] = (
        f"{COORDINATOR_LOCK} Cortex did not create a task and returned no task_ref. Do not call "
        "manage_orchestration, continue_orchestration, read_worker_result, inspect, select another task, or "
        "dispatch a worker. Stop and result that the project ledger requires user-authorized maintenance."
    )
    return result


def _v3_task_ref_required_error(operation: str) -> dict[str, Any]:
    """Refuse project-wide fallback selection for task-scoped public calls."""
    result = _v3_error(
        "task_ref_required",
        f"{operation} requires the exact task_ref returned by a successful Cortex lifecycle response.",
    )
    result["next_action"] = (
        f"{COORDINATOR_LOCK} Do not inspect, list, infer, or select another task from this project root. "
        "Use only the task_ref returned by the task being recovered; if no task_ref was returned, stop and result "
        "the blocker."
    )
    return result


def _v3_task_ref(task_id: str) -> str:
    return "task-" + digest_text(task_id)[:12]


class OperationRegistryError(ValueError):
    """The project-wide replay registry cannot safely serve lifecycle calls."""


COORDINATOR_CAPABILITY_RE = re.compile(r"^[0-9a-f]{64}$")
# A coordinator bearer is deliberately a short-lived verifier for a *single*
# active task.  It is not a project administration credential merely because
# it was returned by a project-local orchestration call.  Keep the claim
# vocabulary local to this public-facade boundary: workers never receive it
# and the governance domain is not asked to trust caller-authored roles.
COORDINATOR_CAPABILITY_CLAIMS_SCHEMA = "cortex/coordinator-capability/v2"
COORDINATOR_CAPABILITY_TTL_SECONDS = 8 * 60 * 60
TASK_COORDINATOR_CAPABILITY_ACTIONS = frozenset({
    "inspect", "inspect_initiative",
    "history", "list_records", "snapshot", "snapshot_inspect",
    "link_task", "link",
    "add_dependency", "dependency", "transition", "transition_initiative",
    "create_record", "record_create",
    "evaluate_promotion", "promotion_evaluate", "promotion_inspect",
})
PROJECT_ADMIN_CAPABILITY_ACTIONS = frozenset({"*"})
CAPABILITY_RECOVERY_ACTIONS = frozenset({
    "recover_coordinator_capability", "rotate_coordinator_capability",
    "acknowledge_coordinator_recovery",
})
COORDINATOR_RECOVERY_PROOF_RE = re.compile(r"^[0-9a-f]{64}$")
COORDINATOR_RECOVERY_DELIVERY_SCHEMA = "cortex/coordinator-recovery-delivery/v1"


def _operation_registry_path(root: Path) -> Path:
    return root / "cortex.db"


def _operation_registry(root: Path) -> dict[str, Any]:
    registry = db_get_global(
        root,
        "operation_registry",
        {"schema": PUBLIC_ORCHESTRATION_SCHEMA, "starts": {}, "tasks": {}, "updated_at": now()},
    )
    if registry.get("schema") != PUBLIC_ORCHESTRATION_SCHEMA:
        raise OperationRegistryError("orchestration operation registry schema is not supported")
    if not isinstance(registry.get("starts"), dict) or not isinstance(registry.get("tasks"), dict):
        raise OperationRegistryError("orchestration operation registry is invalid")
    # 9.2.2 briefly persisted a reusable governance bearer in this project
    # document. Remove and invalidate that retired material on first access;
    # hashing a bearer that may already have been read would preserve a
    # compromised credential. Affected active tasks therefore fail closed and
    # require a fresh start to receive a new one-response capability.
    scrubbed_retired_capability = False
    for reservation in registry["starts"].values():
        if isinstance(reservation, dict):
            if "coordinator_capability" in reservation:
                reservation.pop("coordinator_capability", None)
                reservation.pop("coordinator_capability_digest", None)
                scrubbed_retired_capability = True
            if "coordinator_recovery_proof" in reservation:
                reservation.pop("coordinator_recovery_proof", None)
                reservation.pop("coordinator_recovery_proof_digest", None)
                scrubbed_retired_capability = True
    for record in registry["tasks"].values():
        start = record.get("start") if isinstance(record, dict) else None
        if isinstance(start, dict):
            if "coordinator_capability" in start:
                start.pop("coordinator_capability", None)
                start.pop("coordinator_capability_digest", None)
                scrubbed_retired_capability = True
            if "coordinator_recovery_proof" in start:
                start.pop("coordinator_recovery_proof", None)
                start.pop("coordinator_recovery_proof_digest", None)
                scrubbed_retired_capability = True
    if scrubbed_retired_capability:
        registry["updated_at"] = now()
        db_put_global(root, "operation_registry", registry)
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


_PENDING_COORDINATOR_CAPABILITIES: dict[tuple[str, str], tuple[str, str]] = {}
_PENDING_COORDINATOR_CAPABILITIES_LOCK = threading.Lock()


def _coordinator_capability_digest(capability: str) -> str:
    return hashlib.sha256(str(capability).encode("ascii")).hexdigest()


def _coordinator_recovery_proof_digest(proof: str) -> str:
    """Return the durable verifier for the coordinator-only recovery proof."""
    return hashlib.sha256(str(proof).encode("ascii")).hexdigest()


def _pending_coordinator_capability_key(root: Path, task_id: str) -> tuple[str, str]:
    return str(root.resolve()), str(task_id)


def _pending_recovery_delivery_metadata(
    task_id: str,
    *,
    source_generation: int,
    recovery_proof: str,
) -> dict[str, Any]:
    """Derive a redeliverable pair and retain only non-secret metadata.

    The current recovery proof is the HMAC key.  Cortex does not persist a
    replacement bearer/proof: a retry that carries the same valid old proof
    deterministically recomputes the same pair after a crash.  The ledger
    retains only an opaque nonce and SHA-256 verifiers.
    """
    delivery_id = secrets.token_hex(32)
    context = json.dumps({
        "schema": COORDINATOR_RECOVERY_DELIVERY_SCHEMA,
        "task_id": str(task_id),
        "source_generation": int(source_generation),
        "target_generation": int(source_generation) + 1,
        "delivery_id": delivery_id,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    key = bytes.fromhex(recovery_proof)
    capability = hmac.new(key, b"cortex/recovery-capability/v1\x00" + context, hashlib.sha256).hexdigest()
    next_proof = hmac.new(key, b"cortex/recovery-proof/v1\x00" + context, hashlib.sha256).hexdigest()
    return {
        "schema": COORDINATOR_RECOVERY_DELIVERY_SCHEMA,
        "delivery_id": delivery_id,
        "source_generation": int(source_generation),
        "target_generation": int(source_generation) + 1,
        "coordinator_capability_digest": _coordinator_capability_digest(capability),
        "coordinator_recovery_proof_digest": _coordinator_recovery_proof_digest(next_proof),
        "created_at": now(),
    }


def _pending_recovery_delivery_credentials(
    task_id: str,
    pending: object,
    *,
    recovery_proof: str,
) -> dict[str, Any]:
    """Recompute and verify a pending pair from a valid old recovery proof."""
    if not isinstance(pending, dict) or pending.get("schema") != COORDINATOR_RECOVERY_DELIVERY_SCHEMA:
        raise GovernanceError("coordinator recovery delivery is invalid", code="coordinator_recovery_delivery_unavailable")
    source_generation = pending.get("source_generation")
    target_generation = pending.get("target_generation")
    if not isinstance(source_generation, int) or not isinstance(target_generation, int) or target_generation != source_generation + 1:
        raise GovernanceError("coordinator recovery delivery is invalid", code="coordinator_recovery_delivery_unavailable")
    delivery_id = str(pending.get("delivery_id") or "").lower()
    if not COORDINATOR_CAPABILITY_RE.fullmatch(delivery_id):
        raise GovernanceError("coordinator recovery delivery is invalid", code="coordinator_recovery_delivery_unavailable")
    context = json.dumps({
        "schema": COORDINATOR_RECOVERY_DELIVERY_SCHEMA,
        "task_id": str(task_id),
        "source_generation": source_generation,
        "target_generation": target_generation,
        "delivery_id": delivery_id,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    key = bytes.fromhex(recovery_proof)
    capability = hmac.new(key, b"cortex/recovery-capability/v1\x00" + context, hashlib.sha256).hexdigest()
    next_proof = hmac.new(key, b"cortex/recovery-proof/v1\x00" + context, hashlib.sha256).hexdigest()
    expected_capability_digest = str(pending.get("coordinator_capability_digest") or "").lower()
    expected_proof_digest = str(pending.get("coordinator_recovery_proof_digest") or "").lower()
    if (
        not COORDINATOR_CAPABILITY_RE.fullmatch(expected_capability_digest)
        or not COORDINATOR_RECOVERY_PROOF_RE.fullmatch(expected_proof_digest)
        or not hmac.compare_digest(_coordinator_capability_digest(capability), expected_capability_digest)
        or not hmac.compare_digest(_coordinator_recovery_proof_digest(next_proof), expected_proof_digest)
    ):
        raise GovernanceError("coordinator recovery delivery is invalid", code="coordinator_recovery_delivery_unavailable")
    return {"coordinator_capability": capability, "coordinator_recovery_proof": next_proof}


def _coordinator_capability_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=COORDINATOR_CAPABILITY_TTL_SECONDS)).isoformat()


def _capability_action(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _capability_claims(
    *,
    task_id: str,
    principal: str,
    thread_id: str,
    initiative_ref: str | None,
    kind: str = "task",
    allowed_actions: frozenset[str] | None = None,
    generation: int = 1,
) -> dict[str, Any]:
    """Return durable, non-secret metadata for one coordinator bearer.

    The bearer itself is intentionally excluded.  A SHA-256 verifier is kept
    beside this claim only in the start receipt, never in the claim or audit
    history, so diagnostics and normal registry reads cannot become a bearer
    recovery channel.
    """
    selected_actions = (
        PROJECT_ADMIN_CAPABILITY_ACTIONS
        if kind == "project_admin"
        else (allowed_actions or TASK_COORDINATOR_CAPABILITY_ACTIONS)
    )
    issued_at = now()
    return {
        "schema": COORDINATOR_CAPABILITY_CLAIMS_SCHEMA,
        "kind": kind,
        "task_id": str(task_id),
        "principal": str(principal),
        "thread_id": str(thread_id),
        "initiative_ref": str(initiative_ref or "") or None,
        "allowed_actions": sorted(selected_actions),
        "generation": int(generation),
        "issued_at": issued_at,
        "expires_at": _coordinator_capability_expiry(),
        "revoked_generations": [],
        "rotation_audit": [],
    }


def _stage_coordinator_capability(root: Path, task_id: str) -> tuple[str, str, str]:
    """Create a one-response bearer and recovery proof with durable verifiers.

    Only digests enter the project ledger. The raw bearer and separate proof
    live in this process just long enough for the successful start response;
    a lost or cross-process response fails closed instead of making a reusable
    secret recoverable from the worker-readable project database.
    """
    capability = secrets.token_hex(32)
    recovery_proof = secrets.token_hex(32)
    digest = _coordinator_capability_digest(capability)
    recovery_proof_digest = _coordinator_recovery_proof_digest(recovery_proof)
    with _PENDING_COORDINATOR_CAPABILITIES_LOCK:
        _PENDING_COORDINATOR_CAPABILITIES[_pending_coordinator_capability_key(root, task_id)] = (
            capability,
            recovery_proof,
        )
    return capability, digest, recovery_proof_digest


def _take_coordinator_capability(root: Path, task_id: str) -> tuple[str, str] | None:
    with _PENDING_COORDINATOR_CAPABILITIES_LOCK:
        return _PENDING_COORDINATOR_CAPABILITIES.pop(
            _pending_coordinator_capability_key(root, task_id),
            None,
        )


def _coordinator_capability_matches(root: Path, task_id: str, capability: str) -> bool:
    supplied = str(capability or "").strip().lower()
    if not COORDINATOR_CAPABILITY_RE.fullmatch(supplied):
        return False
    registry = _operation_registry(root)
    record = registry.get("tasks", {}).get(str(task_id))
    start = record.get("start") if isinstance(record, dict) else None
    expected_digest = str(start.get("coordinator_capability_digest") if isinstance(start, dict) else "").strip().lower()
    claims = start.get("coordinator_capability_claims") if isinstance(start, dict) else None
    if not _valid_coordinator_capability_claims(claims, task_id=task_id):
        return False
    return bool(
        COORDINATOR_CAPABILITY_RE.fullmatch(expected_digest)
        and hmac.compare_digest(_coordinator_capability_digest(supplied), expected_digest)
    )


def _valid_coordinator_capability_claims(
    claims: object,
    *,
    task_id: str,
    require_unexpired: bool = True,
) -> bool:
    if not isinstance(claims, dict):
        return False
    if claims.get("schema") != COORDINATOR_CAPABILITY_CLAIMS_SCHEMA:
        return False
    if claims.get("kind") not in {"task", "project_admin"}:
        return False
    if str(claims.get("task_id") or "") != str(task_id):
        return False
    if not str(claims.get("principal") or "").strip() or not str(claims.get("thread_id") or "").strip():
        return False
    actions = claims.get("allowed_actions")
    if not isinstance(actions, list) or not actions or any(not isinstance(item, str) for item in actions):
        return False
    if not isinstance(claims.get("generation"), int) or int(claims["generation"]) < 1:
        return False
    expiry = _prune_timestamp(claims.get("expires_at"))
    return bool(
        expiry
        and not claims.get("revoked_at")
        and (not require_unexpired or expiry > datetime.now(timezone.utc))
    )


def _coordinator_capability_claims_for_task(root: Path, task_id: str) -> dict[str, Any] | None:
    """Return validated server-owned claims for one active task capability."""
    registry = _operation_registry(root)
    record = registry.get("tasks", {}).get(str(task_id))
    start = record.get("start") if isinstance(record, dict) else None
    claims = start.get("coordinator_capability_claims") if isinstance(start, dict) else None
    if not _valid_coordinator_capability_claims(claims, task_id=str(task_id)):
        return None
    return dict(claims)


def _record_capability_rotation(
    claims: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Advance a claim generation without retaining a prior bearer verifier."""
    generation = int(claims["generation"])
    revoked = list(claims.get("revoked_generations") or [])[-31:]
    revoked.append({"generation": generation, "revoked_at": now(), "reason": reason})
    rotations = list(claims.get("rotation_audit") or [])[-31:]
    rotations.append({
        "event": "capability_rotated",
        "reason": reason,
        "from_generation": generation,
        "to_generation": generation + 1,
        "at": now(),
    })
    claims["generation"] = generation + 1
    claims["issued_at"] = now()
    claims["expires_at"] = _coordinator_capability_expiry()
    claims["revoked_generations"] = revoked
    claims["rotation_audit"] = rotations
    return claims


def _begin_coordinator_capability_recovery(
    root: Path,
    *,
    task_id: str,
    principal: str,
    thread_id: str,
    recovery_proof: str,
    expected_generation: int | None = None,
) -> tuple[dict[str, str], dict[str, Any], bool]:
    """Create or redeliver a pending replacement without retiring old access.

    This is intentionally not a rotation yet.  A response can be lost after
    this durable operation; repeating it with the *same old proof* derives
    precisely the same new pair.  The active bearer/proof remain authoritative
    until :func:`_acknowledge_coordinator_capability_recovery` receives both
    delivered replacement values.
    """
    with state_lock(root):
        registry = _operation_registry(root)
        record = registry.get("tasks", {}).get(str(task_id))
        start = record.get("start") if isinstance(record, dict) else None
        claims = start.get("coordinator_capability_claims") if isinstance(start, dict) else None
        if not _valid_coordinator_capability_claims(claims, task_id=str(task_id), require_unexpired=False):
            raise GovernanceError("coordinator capability cannot be recovered for this task", code="coordinator_capability_invalid")
        if str(claims.get("principal")) != principal or str(claims.get("thread_id")) != thread_id:
            raise GovernanceError("capability recovery identity does not match its active task", code="coordinator_authorization_required")
        supplied_proof = str(recovery_proof or "").strip().lower()
        expected_proof_digest = str(start.get("coordinator_recovery_proof_digest") if isinstance(start, dict) else "").strip().lower()
        if (
            not COORDINATOR_RECOVERY_PROOF_RE.fullmatch(supplied_proof)
            or not COORDINATOR_RECOVERY_PROOF_RE.fullmatch(expected_proof_digest)
            or not hmac.compare_digest(_coordinator_recovery_proof_digest(supplied_proof), expected_proof_digest)
        ):
            raise GovernanceError(
                "coordinator capability recovery requires the original non-durable recovery proof",
                code="coordinator_recovery_proof_required",
            )
        generation = int(claims["generation"])
        if expected_generation is not None and int(expected_generation) != generation:
            raise GovernanceError("capability recovery generation is stale", code="coordinator_capability_stale")
        pending = start.get("pending_coordinator_recovery") if isinstance(start, dict) else None
        redelivered = isinstance(pending, dict)
        if redelivered:
            if pending.get("source_generation") != generation:
                raise GovernanceError("coordinator recovery delivery is stale", code="coordinator_capability_stale")
        else:
            pending = _pending_recovery_delivery_metadata(
                task_id,
                source_generation=generation,
                recovery_proof=supplied_proof,
            )
            start["pending_coordinator_recovery"] = pending
            record["start"] = start
            registry["tasks"][str(task_id)] = record
            for reservation in registry.get("starts", {}).values():
                if isinstance(reservation, dict) and str(reservation.get("task_id") or "") == str(task_id):
                    reservation["pending_coordinator_recovery"] = dict(pending)
            _write_operation_registry(root, registry)
        credentials = _pending_recovery_delivery_credentials(
            task_id, pending, recovery_proof=supplied_proof,
        )
        return credentials, dict(claims), redelivered


def _acknowledge_coordinator_capability_recovery(
    root: Path,
    *,
    task_id: str,
    principal: str,
    thread_id: str,
    replacement_capability: str,
    replacement_recovery_proof: str,
    previous_recovery_proof: str,
    expected_generation: int | None = None,
) -> dict[str, Any]:
    """Activate a delivered pair and retire the old generation atomically."""
    with state_lock(root):
        registry = _operation_registry(root)
        record = registry.get("tasks", {}).get(str(task_id))
        start = record.get("start") if isinstance(record, dict) else None
        claims = start.get("coordinator_capability_claims") if isinstance(start, dict) else None
        if not _valid_coordinator_capability_claims(claims, task_id=str(task_id), require_unexpired=False):
            raise GovernanceError("coordinator capability cannot be acknowledged for this task", code="coordinator_capability_invalid")
        if str(claims.get("principal")) != principal or str(claims.get("thread_id")) != thread_id:
            raise GovernanceError("capability recovery identity does not match its active task", code="coordinator_authorization_required")
        generation = int(claims["generation"])
        if expected_generation is not None and int(expected_generation) != generation + 1:
            raise GovernanceError("capability recovery generation is stale", code="coordinator_capability_stale")
        pending = start.get("pending_coordinator_recovery") if isinstance(start, dict) else None
        if not isinstance(pending, dict) or pending.get("source_generation") != generation or pending.get("target_generation") != generation + 1:
            raise GovernanceError("coordinator recovery delivery is unavailable", code="coordinator_recovery_delivery_unavailable")
        capability = str(replacement_capability or "").strip().lower()
        proof = str(replacement_recovery_proof or "").strip().lower()
        old_proof = str(previous_recovery_proof or "").strip().lower()
        active_old_proof_digest = str(start.get("coordinator_recovery_proof_digest") or "").strip().lower()
        expected_capability_digest = str(pending.get("coordinator_capability_digest") or "").strip().lower()
        expected_proof_digest = str(pending.get("coordinator_recovery_proof_digest") or "").strip().lower()
        if (
            not COORDINATOR_CAPABILITY_RE.fullmatch(capability)
            or not COORDINATOR_RECOVERY_PROOF_RE.fullmatch(proof)
            or not COORDINATOR_RECOVERY_PROOF_RE.fullmatch(old_proof)
            or not COORDINATOR_RECOVERY_PROOF_RE.fullmatch(active_old_proof_digest)
            or not COORDINATOR_CAPABILITY_RE.fullmatch(expected_capability_digest)
            or not COORDINATOR_RECOVERY_PROOF_RE.fullmatch(expected_proof_digest)
            or not hmac.compare_digest(_coordinator_recovery_proof_digest(old_proof), active_old_proof_digest)
            or not hmac.compare_digest(_coordinator_capability_digest(capability), expected_capability_digest)
            or not hmac.compare_digest(_coordinator_recovery_proof_digest(proof), expected_proof_digest)
        ):
            raise GovernanceError("coordinator recovery acknowledgement requires the delivered replacement pair", code="coordinator_recovery_acknowledgement_required")
        derived = _pending_recovery_delivery_credentials(task_id, pending, recovery_proof=old_proof)
        if (
            not hmac.compare_digest(capability, derived["coordinator_capability"])
            or not hmac.compare_digest(proof, derived["coordinator_recovery_proof"])
        ):
            raise GovernanceError("coordinator recovery acknowledgement is invalid", code="coordinator_recovery_acknowledgement_required")
        rotated_claims = _record_capability_rotation(dict(claims), reason="lost_response_recovery_acknowledged")
        start["coordinator_capability_digest"] = expected_capability_digest
        start["coordinator_recovery_proof_digest"] = expected_proof_digest
        start["coordinator_capability_claims"] = rotated_claims
        start.pop("pending_coordinator_recovery", None)
        record["start"] = start
        registry["tasks"][str(task_id)] = record
        for reservation in registry.get("starts", {}).values():
            if isinstance(reservation, dict) and str(reservation.get("task_id") or "") == str(task_id):
                reservation["coordinator_capability_digest"] = expected_capability_digest
                reservation["coordinator_recovery_proof_digest"] = expected_proof_digest
                reservation["coordinator_capability_claims"] = dict(rotated_claims)
                reservation.pop("pending_coordinator_recovery", None)
        _write_operation_registry(root, registry)
        return dict(rotated_claims)


def _revoke_coordinator_capability(root: Path, task_id: str, *, reason: str) -> None:
    """Invalidate a task capability at terminal/deactivation boundaries.

    Revocation removes the verifier as well as marking the server-owned claim
    terminal.  The compact audit event deliberately records no bearer or
    verifier, so it remains safe in the normal durable registry.
    """
    with state_lock(root):
        registry = _operation_registry(root)
        changed = False
        for record in registry.get("tasks", {}).values():
            start = record.get("start") if isinstance(record, dict) else None
            claims = start.get("coordinator_capability_claims") if isinstance(start, dict) else None
            if not isinstance(claims, dict) or str(claims.get("task_id") or "") != str(task_id):
                continue
            claims["revoked_at"] = now()
            rotations = list(claims.get("rotation_audit") or [])[-31:]
            rotations.append({"event": "capability_revoked", "reason": reason, "at": now()})
            claims["rotation_audit"] = rotations
            start.pop("coordinator_capability_digest", None)
            start.pop("coordinator_recovery_proof_digest", None)
            start.pop("pending_coordinator_recovery", None)
            start["coordinator_capability_claims"] = claims
            record["start"] = start
            changed = True
        for reservation in registry.get("starts", {}).values():
            if not isinstance(reservation, dict) or str(reservation.get("task_id") or "") != str(task_id):
                continue
            claims = reservation.get("coordinator_capability_claims")
            if isinstance(claims, dict):
                claims["revoked_at"] = now()
                rotations = list(claims.get("rotation_audit") or [])[-31:]
                rotations.append({"event": "capability_revoked", "reason": reason, "at": now()})
                claims["rotation_audit"] = rotations
                reservation["coordinator_capability_claims"] = claims
            reservation.pop("coordinator_capability_digest", None)
            reservation.pop("coordinator_recovery_proof_digest", None)
            reservation.pop("pending_coordinator_recovery", None)
            changed = True
        if changed:
            _write_operation_registry(root, registry)


def _issue_project_admin_coordinator_capability(
    root: Path,
    *,
    task_id: str,
    principal: str,
    thread_id: str,
    explicit_server_grant: bool = False,
) -> str:
    """Issue a project-admin bearer only from an explicit trusted server grant.

    There is deliberately no public MCP parameter that can request this.  The
    helper exists for a future host-side administrator integration and tests;
    ordinary task capabilities can never self-upgrade through caller JSON.
    """
    if not explicit_server_grant:
        raise ValueError("project-admin capability requires an explicit trusted server grant")
    with state_lock(root):
        registry = _operation_registry(root)
        record = registry.get("tasks", {}).get(str(task_id))
        start = record.get("start") if isinstance(record, dict) else None
        claims = start.get("coordinator_capability_claims") if isinstance(start, dict) else None
        if not _valid_coordinator_capability_claims(claims, task_id=str(task_id)):
            raise ValueError("task has no active coordinator capability claims")
        if str(claims.get("principal")) != principal or str(claims.get("thread_id")) != thread_id:
            raise ValueError("project-admin grant identity does not match the active task")
        bearer = secrets.token_hex(32)
        digest = _coordinator_capability_digest(bearer)
        admin_claims = _capability_claims(
            task_id=str(task_id),
            principal=principal,
            thread_id=thread_id,
            initiative_ref=None,
            kind="project_admin",
            generation=int(claims["generation"]) + 1,
        )
        admin_claims["revoked_generations"] = list(claims.get("revoked_generations") or [])[-31:] + [{
            "generation": int(claims["generation"]),
            "revoked_at": now(),
            "reason": "explicit_server_project_admin_grant",
        }]
        admin_claims["rotation_audit"] = list(claims.get("rotation_audit") or [])[-31:] + [{
            "event": "project_admin_capability_issued",
            "from_generation": int(claims["generation"]),
            "to_generation": int(claims["generation"]) + 1,
            "at": now(),
        }]
        start["coordinator_capability_digest"] = digest
        # A project-admin grant is issued only by a trusted server integration.
        # It intentionally does not inherit a task-level lost-response proof.
        # Retaining it could let an old task-session recovery credential rotate
        # the newly elevated bearer.
        start.pop("coordinator_recovery_proof_digest", None)
        start.pop("pending_coordinator_recovery", None)
        start["coordinator_capability_claims"] = admin_claims
        record["start"] = start
        registry["tasks"][str(task_id)] = record
        for reservation in registry.get("starts", {}).values():
            if isinstance(reservation, dict) and str(reservation.get("task_id") or "") == str(task_id):
                reservation["coordinator_capability_digest"] = digest
                reservation.pop("coordinator_recovery_proof_digest", None)
                reservation.pop("pending_coordinator_recovery", None)
                reservation["coordinator_capability_claims"] = dict(admin_claims)
        _write_operation_registry(root, registry)
        return bearer


def _capability_scope_ref(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _authorize_governance_capability_claim(
    claims: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Enforce a server-owned capability's action and task/initiative scope.

    This is intentionally performed before entering the governance service.
    The service validates domain relationships; this boundary prevents a
    valid coordinator bearer for task A from becoming a generic project
    mutation token by merely changing task_id or initiative_ref in JSON.
    """
    action = _capability_action(payload.get("action") or payload.get("intent"))
    allowed_actions = {str(item) for item in claims.get("allowed_actions", [])}
    if "*" not in allowed_actions and action not in allowed_actions:
        raise GovernanceError(
            "coordinator capability is not authorized for this governance action",
            code="coordinator_capability_action_denied",
        )
    if claims.get("kind") == "project_admin":
        return

    # Task capabilities are never project policy/promotion/exception
    # authorities, even if a caller tries to reach them through the generic
    # record create action.
    if action in {
        "approve_promotion", "promotion_approve", "approve",
        "reject_promotion", "promotion_reject", "reject",
        "request_exception", "exception_request",
        "create", "create_initiative",
    }:
        raise GovernanceError(
            "task-scoped coordinator capability cannot administer project governance",
            code="coordinator_capability_scope_denied",
        )
    if action in {"create_record", "record_create"} and str(payload.get("record_type") or "").strip().lower() in {
        "policy", "promotion", "exception",
    }:
        raise GovernanceError(
            "task-scoped coordinator capability cannot create policy, promotion, or exception records",
            code="coordinator_capability_scope_denied",
        )

    task_id = str(claims.get("task_id") or "")
    initiative_ref = _capability_scope_ref(claims.get("initiative_ref"))
    supplied_task = _capability_scope_ref(payload.get("task_id"))
    supplied_initiative = _capability_scope_ref(payload.get("initiative_ref"))
    if supplied_task is not None and supplied_task != task_id:
        raise GovernanceError(
            "task-scoped coordinator capability cannot operate on another task",
            code="coordinator_capability_scope_denied",
        )
    if initiative_ref is None:
        # C3 auto-governance is task-scoped when no initiative was supplied.
        # It may record and inspect its own non-policy evidence, but it must
        # not turn that narrow scope into an initiative or project mutation.
        task_local_actions = {
            "history", "list_records", "snapshot", "snapshot_inspect",
            "create_record", "record_create",
        }
        if (
            action not in task_local_actions
            or supplied_task != task_id
            or supplied_initiative is not None
        ):
            raise GovernanceError(
                "task capability has no server-bound initiative governance scope",
                code="coordinator_capability_scope_denied",
            )
        return
    if supplied_initiative != initiative_ref:
        raise GovernanceError(
            "task-scoped coordinator capability must name its server-bound initiative",
            code="coordinator_capability_scope_denied",
        )
    if action in {"link_task", "link"} and supplied_task != task_id:
        raise GovernanceError(
            "task link must name the capability's task",
            code="coordinator_capability_scope_denied",
        )
    if action in {"add_dependency", "dependency"}:
        source_type = _capability_action(payload.get("source_type") or "initiative")
        target_type = _capability_action(payload.get("target_type") or "initiative")
        source_ref = _capability_scope_ref(payload.get("source_ref"))
        target_ref = _capability_scope_ref(payload.get("target_ref"))
        if (
            (source_type == "initiative" and source_ref != initiative_ref)
            or (target_type == "initiative" and target_ref != initiative_ref)
            or (source_type == "task" and source_ref != task_id)
            or (target_type == "task" and target_ref != task_id)
        ):
            raise GovernanceError(
                "task-scoped coordinator capability cannot add dependencies outside its task/initiative scope",
                code="coordinator_capability_scope_denied",
            )


def _recovery_task_identity(params: dict[str, Any], project: Path) -> tuple[Path, str, str, str, str]:
    """Resolve the active task identity for either phase of recovery."""
    principal = str(params.get("principal") or "").strip()
    thread_id = str(params.get("thread_id") or "").strip()
    task_ref = str(params.get("task_ref") or "").strip()
    if not principal or not thread_id or not task_ref:
        raise GovernanceError(
            "capability recovery requires the exact task_ref, principal, and thread_id",
            code="coordinator_authorization_required",
        )
    root = ledger_root({"project_root": str(project)})
    registry = _operation_registry(root)
    matching = [
        str(task_id)
        for task_id in registry.get("tasks", {})
        if _v3_task_ref(str(task_id)) == task_ref
    ]
    if len(matching) != 1:
        raise GovernanceError("capability recovery task_ref is not active", code="coordinator_capability_invalid")
    task_id = matching[0]
    try:
        activation = require_activation(
            {"project_root": str(project), "principal": principal, "thread_id": thread_id}, task_id,
        )
    except ValueError as exc:
        raise GovernanceError(str(exc), code="coordinator_authorization_required") from exc
    if str(activation.get("task_id") or "") != task_id:
        raise GovernanceError("capability recovery task is not the active coordinator task", code="coordinator_authorization_required")
    return root, task_id, task_ref, principal, thread_id


def _recover_coordinator_capability(params: dict[str, Any], project: Path) -> dict[str, Any]:
    """Stage or redeliver a replacement pair without rotating active access.

    Recovery has a deliberately narrower identity contract than ordinary
    governance calls: the exact task_ref, principal, thread_id, and a
    coordinator-only recovery proof are all required and must still identify
    the active activation.  Public identifiers alone are never recovery
    authority.  Callers cannot request a project-admin elevation through this
    route.
    """
    recovery_proof = str(params.get("coordinator_recovery_proof") or "").strip().lower()
    if not COORDINATOR_RECOVERY_PROOF_RE.fullmatch(recovery_proof):
        raise GovernanceError(
            "capability recovery requires the original non-durable coordinator recovery proof",
            code="coordinator_recovery_proof_required",
        )
    root, task_id, task_ref, principal, thread_id = _recovery_task_identity(params, project)
    generation_raw = params.get("capability_generation")
    if generation_raw is not None and (not isinstance(generation_raw, int) or generation_raw < 1):
        raise GovernanceError("capability_generation must be a positive integer", code="coordinator_capability_invalid")
    authorization_update, claims, redelivered = _begin_coordinator_capability_recovery(
        root,
        task_id=task_id,
        principal=principal,
        thread_id=thread_id,
        recovery_proof=recovery_proof,
        expected_generation=generation_raw,
    )
    return {
        "schema": "cortex/governance/v1",
        "ok": True,
        "outcome": "coordinator_capability_recovery_redelivered" if redelivered else "coordinator_capability_recovery_pending",
        "action": "recover_coordinator_capability",
        "task_ref": task_ref,
        "authorization": {
            "actor": "coordinator",
            "source": "server_activation_capability_recovery_pending",
            "principal": principal,
            "thread_id": thread_id,
            "capability_kind": claims["kind"],
            "generation": int(claims["generation"]) + 1,
        },
        # This is intentionally the only non-start response path that
        # contains raw coordinator authorization material.  Neither registry
        # nor rotation audit receives either value, and worker transport never
        # exposes this operation.
        "authorization_update": {
            **authorization_update,
        },
        "next_action": (
            "Call manage_governance once with action=acknowledge_coordinator_recovery, the exact task_ref/principal/thread_id, "
            "both authorization_update values, and previous_coordinator_recovery_proof set to the old proof. Until acknowledgement, retain and use the old capability/proof."
        ),
    }


def _acknowledge_coordinator_recovery(params: dict[str, Any], project: Path) -> dict[str, Any]:
    """Commit a pending recovery only after its delivered replacement pair returns."""
    root, task_id, task_ref, principal, thread_id = _recovery_task_identity(params, project)
    generation_raw = params.get("capability_generation")
    if generation_raw is not None and (not isinstance(generation_raw, int) or generation_raw < 1):
        raise GovernanceError("capability_generation must be a positive integer", code="coordinator_capability_invalid")
    claims = _acknowledge_coordinator_capability_recovery(
        root,
        task_id=task_id,
        principal=principal,
        thread_id=thread_id,
        replacement_capability=str(params.get("coordinator_capability") or ""),
        replacement_recovery_proof=str(params.get("coordinator_recovery_proof") or ""),
        previous_recovery_proof=str(params.get("previous_coordinator_recovery_proof") or ""),
        expected_generation=generation_raw,
    )
    return {
        "schema": "cortex/governance/v1",
        "ok": True,
        "outcome": "coordinator_capability_recovery_acknowledged",
        "action": "acknowledge_coordinator_recovery",
        "task_ref": task_ref,
        "authorization": {
            "actor": "coordinator",
            "source": "server_activation_capability_recovery_acknowledgement",
            "principal": principal,
            "thread_id": thread_id,
            "capability_kind": claims["kind"],
            "generation": claims["generation"],
        },
    }


def _coordinator_identity_for_capability(
    root: Path,
    capability: str,
) -> tuple[str, str, str] | None:
    """Resolve the one server-owned coordinator identity bound to a capability."""
    supplied = str(capability or "").strip().lower()
    if not COORDINATOR_CAPABILITY_RE.fullmatch(supplied):
        return None
    supplied_digest = _coordinator_capability_digest(supplied)
    registry = _operation_registry(root)
    matches: list[tuple[str, str, str]] = []
    for task_id, record in registry.get("tasks", {}).items():
        if not isinstance(record, dict):
            continue
        start = record.get("start")
        expected_digest = str(
            start.get("coordinator_capability_digest") if isinstance(start, dict) else ""
        ).strip().lower()
        claims = start.get("coordinator_capability_claims") if isinstance(start, dict) else None
        if (
            not COORDINATOR_CAPABILITY_RE.fullmatch(expected_digest)
            or not _valid_coordinator_capability_claims(claims, task_id=str(task_id))
            or not hmac.compare_digest(expected_digest, supplied_digest)
        ):
            continue
        principal = str(claims.get("principal") or "").strip()
        thread_id = str(claims.get("thread_id") or principal).strip()
        if principal and thread_id:
            matches.append((str(task_id), principal, thread_id))
    if len(matches) != 1:
        return None
    return matches[0]


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
            legacy_root = project / ".codex" / "cortex"
            legacy_info = _lstat_or_none(legacy_root)
            if legacy_info is not None and stat.S_ISLNK(legacy_info.st_mode):
                raise ValueError("full reset refuses a symlinked legacy Cortex root")
            # Resolve an existing private store without creating a fresh
            # database merely because the user asked to reset.
            root = existing_ledger_root({"project_root": str(project)})
            root_info = _lstat_or_none(root)
            if root_info is not None and (stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode)):
                raise ValueError("full reset refuses a symlinked or non-directory Cortex host ledger")
            database_info = _lstat_or_none(root / "cortex.db") if root_info is not None else None
            if database_info is not None and (stat.S_ISLNK(database_info.st_mode) or not stat.S_ISREG(database_info.st_mode)):
                raise ValueError("full reset refuses an unsafe Cortex host database")
            active = []
            if database_info is not None:
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
            operation_id = digest_text(str(root) + ":full-reset")[:16]
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
            "user_request": redact(task.get("user_request", ""), 300),
            "status": state.get("status"),
            "created_at": task.get("created_at"),
        })
    return candidates


def _v3_resolve_task(
    params: dict[str, Any],
    *,
    include_completed: bool = False,
    require_task_ref: bool = False,
) -> tuple[Path, dict[str, Any], dict[str, Any], str] | dict[str, Any]:
    root = ledger_root(params)
    candidates = _v3_task_candidates(params, include_completed=include_completed)
    requested = str(params.get("task_ref") or "").strip()
    if require_task_ref and not requested:
        return _v3_task_ref_required_error("task-scoped Cortex call")
    if requested:
        selected = next((item for item in candidates if item["task_ref"] == requested), None)
        if selected is None:
            return _v3_error("unknown_task_ref", "task_ref does not identify a selectable Cortex task")
    elif len(candidates) == 1:
        selected = candidates[0]
    elif not candidates:
        return _v3_error("no_active_task", "No active Cortex task exists in this project root.")
    else:
        public_candidates = [{key: item[key] for key in ("task_ref", "user_request", "status")} for item in candidates]
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
    allow_visible_threads: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(raw_waves, list) or not raw_waves:
        raise ValueError("waves must be a non-empty array when supplied")
    result: list[dict[str, Any]] = []
    allowed_worker_keys = {
        "phase", "profile", "objective", "paths", "allowed_paths", "acceptance", "verification",
        "model", "user_requested_model", "effort", "visible", "isolated_checkout", "depends_on", "context_files",
        "context_result_refs",
        "strategy",
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
            if visible and not allow_visible_threads:
                raise ValueError(
                    "visible thread requires an explicit user-authorized task; "
                    "set task.visible_thread_requested=true only when the user requested a visible task"
                )
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
            if "context_result_refs" in worker:
                raw_result_refs = worker["context_result_refs"]
                if (
                    not isinstance(raw_result_refs, list)
                    or len(raw_result_refs) > 32
                    or any(not isinstance(item, str) or not item.strip() for item in raw_result_refs)
                ):
                    raise ValueError("worker context_result_refs must be a non-empty-string array of at most 32 items")
                result_refs = [safe_id(item.strip()) for item in raw_result_refs]
                if len(result_refs) != len(set(result_refs)):
                    raise ValueError("worker context_result_refs must be unique")
                spec["context_result_refs"] = result_refs
            for source, target in (
                ("objective", "objective"), ("paths", "allowed_paths"),
                ("allowed_paths", "allowed_paths"),
                ("acceptance", "acceptance_criteria"), ("verification", "verification"),
                ("context_files", "context_files"), ("strategy", "strategy"),
            ):
                if source in worker:
                    if source == "context_files" and project_root is not None:
                        spec[target] = _project_knowledge_context(project_root, worker[source])[0]
                    elif source == "allowed_paths":
                        # Keep the canonical field server-owned: unlike the
                        # broad `paths` hint, the explicit field is a
                        # validated narrow write scope and cannot broaden a
                        # worker to the whole project.
                        paths = _planning_paths_list(worker[source], "worker allowed_paths")
                        if any(path in {".", "*"} for path in paths):
                            raise ValueError("worker allowed_paths must be explicit and non-broad")
                        spec[target] = paths
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
    classification_params: dict[str, Any] = {
        "complexity": task["complexity"],
        "requirements": _task_routing_items(task),
    }
    if _is_external_lifecycle_only_task(task):
        # The control-plane action is performed by this coordinator at task
        # creation.  Do not later dispatch an implementation worker whose
        # result contract requires a project change that the task forbids.
        classification_params["pipeline"] = [
            gate for gate in BASE_PIPELINES[str(task["complexity"])]
            if gate not in {"implementation", "qa"}
        ]
    classified = classify(classification_params)
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
    waves = [
        {
            "wave_id": f"wave-{index:02d}",
            "delegations": [automatic_spec(gate) for gate in group],
        }
        for index, group in enumerate(groups, 1)
    ]
    return _append_governance_waves(waves, task)


def _append_governance_waves(waves: list[dict[str, Any]], task: dict[str, Any]) -> list[dict[str, Any]]:
    """Add the bounded full-governance activation/close review waves.

    The resolver is the only authority that can request ``full``.  Once it
    does, these two server-owned waves surround the ordinary user pipeline;
    caller-supplied waves cannot silently omit either review.  Existing
    governance phases are preserved for idempotent retries and explicit
    coordinator plans.
    """
    governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}
    if governance.get("effective_mode") != "full":
        return waves
    governance_locations: dict[str, list[int]] = {
        "governance_activation": [],
        "governance_close": [],
    }
    for index, wave in enumerate(waves):
        delegations = [item for item in (wave.get("delegations") or []) if isinstance(item, dict)]
        governance_items = [
            item for item in delegations
            if str(item.get("gate") or "") in governance_locations
        ]
        if not governance_items:
            continue
        if len(governance_items) != 1 or len(delegations) != 1:
            raise ValueError("server-owned governance review waves must contain exactly one delegation")
        item = governance_items[0]
        gate = str(item.get("gate") or "")
        if str(item.get("agent") or "") != "code_reviewer":
            raise ValueError(f"{gate} is server-owned by code_reviewer")
        governance_locations[gate].append(index)
    for gate, locations in governance_locations.items():
        if len(locations) > 1:
            raise ValueError(f"full governance may contain only one {gate} wave")

    def governance_wave(gate: str, wave_id: str, position: str) -> dict[str, Any]:
        return {
            "wave_id": wave_id,
            "delegations": [{
                "gate": gate,
                "agent": "code_reviewer",
                "selection_reason": (
                    "Full governance requires an independent code_reviewer-owned "
                    f"{position} review before ordinary orchestration can proceed."
                ),
            }],
        }

    result = list(waves)
    if not governance_locations["governance_activation"]:
        result.insert(0, governance_wave("governance_activation", "governance-activation", "activation"))
    else:
        activation_index = governance_locations["governance_activation"][0]
        if activation_index:
            activation_wave = result.pop(activation_index)
            result.insert(0, activation_wave)
    if not governance_locations["governance_close"]:
        close_index = next(
            (
                index
                for index, wave in enumerate(result)
                if any(
                    isinstance(item, dict) and str(item.get("gate") or "") == "close"
                    for item in (wave.get("delegations") or [])
                )
            ),
            len(result),
        )
        result.insert(close_index, governance_wave("governance_close", "governance-close", "close"))
    else:
        close_index = next(
            (
                index
                for index, wave in enumerate(result)
                if any(
                    isinstance(item, dict) and str(item.get("gate") or "") == "close"
                    for item in (wave.get("delegations") or [])
                )
            ),
            len(result),
        )
        governance_close_index = next(
            (
                index
                for index, wave in enumerate(result)
                if any(
                    isinstance(item, dict) and str(item.get("gate") or "") == "governance_close"
                    for item in (wave.get("delegations") or [])
                )
            ),
            len(result),
        )
        if governance_close_index > close_index:
            governance_close_wave = result.pop(governance_close_index)
            result.insert(close_index, governance_close_wave)
    if len(result) > len(waves) + 2:
        raise ValueError("full governance may add at most two lifecycle waves")
    # The public v5 facade uses the trailing ordinal in ``wave_id`` as the
    # relative ``step`` accepted by ``continue_orchestration``.  Server-owned
    # governance waves used to carry symbolic ids (``governance-activation``
    # and ``governance-close``), which rendered as ``step=None`` and made the
    # first governance result impossible to submit because the public adapter
    # accepts integer steps only.  Re-number the complete server-resolved
    # sequence after insertion/reordering so response rendering and continue
    # validation share one unambiguous relative-step contract.
    return [
        {**wave, "wave_id": f"wave-{index:02d}"}
        for index, wave in enumerate(result, 1)
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
    response = render_v3_response(
        old,
        task_ref,
        native_arguments=_v3_native_arguments,
        public_schema=PUBLIC_ORCHESTRATION_SCHEMA,
        coordinator_lock=COORDINATOR_LOCK,
        include_result=include_result,
        start_replayed=start_replayed,
    )
    if not response.get("ok", False):
        response["attempt_budget_consumed"] = False
    return response



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
    # Governance inputs are part of the immutable start contract.  A retry
    # with the same prose but a different initiative or requested floor must
    # not silently replay an already-created task under a weaker/other policy.
    start_digest = _orchestrate_request_digest({
        "user_request": task.get("user_request"),
        "initiative_ref": task.get("initiative_ref"),
        "governance_mode": task.get("governance_mode"),
        "risk_triggers": task.get("risk_triggers"),
        "governance_triggers": task.get("governance_triggers"),
        "multiple_repositories": task.get("multiple_repositories"),
        "related_tasks": task.get("related_tasks"),
        "long_lived_lanes": task.get("long_lived_lanes"),
        "conflicting_resources": task.get("conflicting_resources"),
        "multi_session_handoff": task.get("multi_session_handoff"),
    })
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
        objective_slug = v3_task_slug(task["user_request"])
        task_id = safe_id(f"{objective_slug}-{secrets.token_hex(4)}")
        task_ref = _v3_task_ref(task_id)
        principal = safe_id("orchestration-" + task_ref)
        thread_id = _codex_host_session_id() or principal
        submission_id = safe_id("orchestration-start-" + secrets.token_hex(8))
        _, capability_digest, recovery_proof_digest = _stage_coordinator_capability(root, task_id)
        capability_claims = _capability_claims(
            task_id=task_id,
            principal=principal,
            thread_id=thread_id,
            initiative_ref=str(task.get("initiative_ref") or "") or None,
        )
        reservation = {
            "task_id": task_id,
            "task_ref": task_ref,
            "principal": principal,
            "thread_id": thread_id,
            "submission_id": submission_id,
            "coordinator_capability_digest": capability_digest,
            "coordinator_recovery_proof_digest": recovery_proof_digest,
            "coordinator_capability_claims": capability_claims,
            "created_at": now(),
        }
        registry["starts"][start_digest] = reservation
        registry["tasks"].setdefault(task_id, {})["start"] = {"digest": start_digest, **reservation}
        try:
            _write_operation_registry(root, registry)
        except Exception:
            # The durable reservation did not become a usable start receipt.
            # Drop the in-memory pair as well, so a later retry cannot turn a
            # failed persistence path into raw-authorization delivery.
            _take_coordinator_capability(root, task_id)
            raise
        return task_id, task_ref, principal, thread_id, submission_id, False


def start_orchestration(params: dict[str, Any]) -> dict[str, Any]:
    """Start public Cortex orchestration without caller-managed lifecycle identifiers."""
    staged_authorization_task_id: str | None = None
    try:
        selected_project_root = select_project_root(params)
        if set(params) - {"project_root", "task", "waves", "_follow_up"}:
            raise ValueError("start_orchestration accepts only project_root, task, and optional waves")
        raw_task = params.get("task")
        if not isinstance(raw_task, dict):
            raise ValueError("task must be an object containing the exact user_request")
        allowed_task = {
            "user_request", "requirements", "constraints", "acceptance_criteria", "scope", "allowed_paths",
            "verification", "budget", "pause_conditions", "user_language", "language",
            "complexity", "replan_limit", "plan_approval", "initiative_ref", "governance_mode",
            "risk_triggers", "governance_triggers", "multiple_repositories", "related_tasks",
            "long_lived_lanes", "conflicting_resources", "multi_session_handoff",
            "communication_profile", "visible_thread_requested",
        }
        unknown_task = sorted(set(raw_task) - allowed_task)
        if unknown_task:
            raise ValueError("unsupported task fields: " + ", ".join(unknown_task))
        user_request = canonicalize_desktop_cortex_request(raw_task.get("user_request"))
        if not user_request:
            raise ValueError(
                "task.user_request is required and must preserve the exact user-authored task without coordinator expansion"
            )
        intent_required, intent_reason = _intent_clarification_preflight(user_request)
        task = dict(raw_task)
        task["user_request"] = user_request
        # Public task text collections are persisted as current canonical
        # arrays, preserving traceability without scalar normalization.
        for field in (
            "requirements", "constraints", "acceptance_criteria", "scope", "allowed_paths",
            "verification", "pause_conditions",
        ):
            task[field] = (
                normalize_task_requirements(raw_task.get(field))
                if field == "requirements"
                else normalize_init_text_list(raw_task.get(field), field)
            )
        task["intent_clarification_required"] = intent_required
        task["intent_clarification_reason"] = intent_reason
        if "_follow_up" in params:
            if not isinstance(params["_follow_up"], dict):
                raise ValueError("internal follow_up context must be an object")
            task["follow_up"] = sanitize_structured(params["_follow_up"])
        task["complexity"] = _v3_complexity(raw_task.get("complexity"))
        task["acceptance_criteria"], task["verification"] = _required_task_result_contract(task)
        governance = resolve_governance(
            ledger_root(params),
            complexity=task["complexity"],
            requested_mode=raw_task.get("governance_mode", "auto"),
            objective=user_request,
            requirements=task.get("requirements", []),
            scope=task.get("scope", []),
            allowed_paths=task.get("allowed_paths", []),
            task=task,
            initiative_ref=raw_task.get("initiative_ref"),
        )
        task["initiative_ref"] = governance.get("initiative_ref")
        task["governance_mode"] = governance.get("requested_mode")
        task["governance"] = governance
        task["plan_approval"] = (
            "auto"
            if _is_knowledge_harvest_task(task)
            else _v3_plan_approval(raw_task.get("plan_approval"), task["complexity"])
        )
        language_alias = task.pop("language", None)
        task["user_language"] = normalize_user_language(
            task.get("user_language") or language_alias,
            user_request,
        )
        task["communication_profile"] = select_communication_profile(task)
        visible_thread_requested = raw_task.get("visible_thread_requested", False)
        if not isinstance(visible_thread_requested, bool):
            raise ValueError("task.visible_thread_requested must be a boolean")
        task["visible_thread_requested"] = visible_thread_requested
        waves = (
            _append_governance_waves(
                _v3_compact_waves(
                    params["waves"], task, project_root=selected_project_root,
                    allow_visible_threads=visible_thread_requested,
                ),
                task,
            )
            if params.get("waves") is not None else _v3_auto_waves(task)
        )
        task_id, task_ref, principal, thread_id, submission_id, replayed = _v3_start_reservation(params, task)
        if not replayed:
            staged_authorization_task_id = task_id
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
            # An idempotent start is a status replay, never a credential
            # delivery channel.  In particular, a worker that knows the user
            # request cannot race or retry a start to obtain coordinator-only
            # material staged for the original coordinator response.
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
        if isinstance(old, dict):
            old["governance"] = governance
        response = _v3_response(old, task_ref, start_replayed=replayed)
        authorization = _take_coordinator_capability(ledger_root(params), task_id)
        staged_authorization_task_id = None
        if authorization and response.get("ok"):
            capability, recovery_proof = authorization
            response["authorization"] = {
                "coordinator_capability": capability,
                "coordinator_recovery_proof": recovery_proof,
            }
        elif authorization:
            # The one response that could have safely delivered both raw
            # secrets did not succeed.  Do not leave an idempotent retry as a
            # bearer/recovery oracle; invalidate the staged claims instead.
            _revoke_coordinator_capability(
                ledger_root(params), task_id, reason="start_authorization_response_unavailable"
            )
        return response
    except OperationRegistryError as exc:
        if staged_authorization_task_id:
            _take_coordinator_capability(ledger_root(params), staged_authorization_task_id)
            _revoke_coordinator_capability(
                ledger_root(params), staged_authorization_task_id,
                reason="start_authorization_response_unavailable",
            )
        return _v3_start_state_blocked_error(exc)
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        if staged_authorization_task_id:
            _take_coordinator_capability(ledger_root(params), staged_authorization_task_id)
            _revoke_coordinator_capability(
                ledger_root(params), staged_authorization_task_id,
                reason="start_authorization_response_unavailable",
            )
        return _v3_error("start_validation_failed", exc)


def _v3_status(value: object, *, has_attempt_result: bool) -> str:
    if value in {None, ""} and has_attempt_result:
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
    # A SubagentStop without a result is terminalized before recovery, but a
    # mixed wave still contains live workers.  Keep that exact failed slot in
    # the relative result contract; otherwise the handoff asks the
    # coordinator to submit a failed receipt that this adapter rejects as an
    # unknown slot.  Other terminal attempts (notably passed attempts kept
    # during repeated gate rework) remain omitted when a live retry exists.
    attempt_result_absent_failure_ids = {
        str(item.get("attempt_id") or "")
        for item in state.get("attempts", [])
        if item.get("gate") in wave.get("gates", [])
        and item.get("status") == "failed"
        and item.get("host_stop_outcome") == "native_worker_stopped_without_result"
        and not item.get("invalidated")
    }
    wave_attempt_ids = [
        str(attempt_id)
        for attempt_id in (wave.get("attempt_ids") or [])
        if str(attempt_id or "").strip()
    ]
    if active_attempt_ids:
        eligible = set(active_attempt_ids) | attempt_result_absent_failure_ids
        attempt_ids = [attempt_id for attempt_id in wave_attempt_ids if attempt_id in eligible]
        if not attempt_ids:
            attempt_ids = active_attempt_ids + [
                attempt_id for attempt_id in attempt_result_absent_failure_ids
                if attempt_id not in active_attempt_ids
            ]
    else:
        # Once the final live retry in a gate has stopped without a canonical result,
        # only that current non-invalidated failed slot remains addressable.
        # Older invalidated attempt IDs stay in the immutable wave history but
        # must not inflate the result cardinality for the retry receipt.
        attempt_ids = [attempt_id for attempt_id in wave_attempt_ids if attempt_id in attempt_result_absent_failure_ids]
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
                # Every field of a recovery request contributes to the
                # operation identity.  In particular, replacement waves,
                # their rework mode, and the coordinator's reason must not be
                # swapped after gate results have been recorded under the
                # same in-flight submission.
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


def _governance_boundary_recheck(
    params: dict[str, Any],
    task: dict[str, Any],
    state: dict[str, Any],
    *,
    future_waves: Any = None,
    results: Any = None,
) -> dict[str, Any]:
    """Re-resolve stated governance triggers before accepting a new plan.

    Governance is not a one-time start classification.  A coordinator can
    introduce a security, migration, cross-session, or multi-repository
    concern in a later result or replacement wave.  Such a promotion must be
    visible at the boundary and cannot be smuggled through a light pipeline;
    the caller must submit the replacement with the server-owned full review
    waves.
    """
    current = state.get("governance") if isinstance(state.get("governance"), dict) else {}
    current_mode = str(current.get("effective_mode") or "minimal")

    # Generated gate/agent names and opaque result references are not user
    # statements.  Scanning their serialized JSON for words such as
    # ``security`` promoted an ordinary smoke pipeline merely because it had
    # a security gate.  Re-evaluation accepts only the explicit structured
    # trigger fields that the public task contract defines.
    trigger_keys = {
        "risk_triggers", "governance_triggers", "security", "privacy", "credentials",
        "sensitive_data", "destructive", "migration", "external_action", "public_contract",
        "authorization", "artifact_integrity", "result_integrity", "verification_integrity",
        "multiple_repositories", "related_tasks", "long_lived_lanes", "conflicting_resources",
        "multi_session_handoff",
    }

    def explicit_triggers(value: Any) -> list[Any]:
        found: list[Any] = []
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = str(key).strip().lower().replace("-", "_")
                if normalized in {"risk_triggers", "governance_triggers"}:
                    if isinstance(nested, (list, tuple, set)):
                        found.extend(item for item in nested if item)
                    elif isinstance(nested, dict):
                        found.extend(key for key, enabled in nested.items() if enabled)
                    elif nested:
                        found.append(nested)
                elif normalized in trigger_keys and nested:
                    found.append(normalized)
                elif isinstance(nested, (dict, list, tuple)):
                    found.extend(explicit_triggers(nested))
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                found.extend(explicit_triggers(item))
        return found

    boundary_triggers = explicit_triggers(future_waves) + explicit_triggers(results)
    if not boundary_triggers:
        return current
    task_probe = dict(task)
    task_probe["risk_triggers"] = boundary_triggers
    resolved = resolve_governance(
        ledger_root(params),
        complexity=task.get("complexity", state.get("complexity", "C2")),
        requested_mode=current.get("requested_mode", task.get("governance_mode", "auto")),
        objective=task_probe["user_request"],
        requirements=task.get("requirements", []),
        scope=task.get("scope", []),
        allowed_paths=task.get("allowed_paths", []),
        task=task_probe,
        initiative_ref=task.get("initiative_ref"),
    )
    if resolved.get("effective_mode") == "full" and current_mode != "full":
        raise ValueError(
            "new governance trigger detected at the continue boundary; submit a replacement pipeline with "
            "server-owned governance_activation and governance_close waves"
        )
    return resolved


def continue_orchestration(params: dict[str, Any]) -> dict[str, Any]:
    """Advance exactly the active Cortex wave using relative worker slots."""
    resolved_task_ref = str(params.get("task_ref") or "").strip() or None
    try:
        select_project_root(params)
        allowed = {"project_root", "task_ref", "step", "results", "future_waves", "rework", "reason"}
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ValueError("unsupported continue fields: " + ", ".join(unknown))
        if not resolved_task_ref:
            return _v3_task_ref_required_error("continue_orchestration")
        results = params.get("results")
        if not isinstance(results, list) or not results:
            raise ValueError("results must be a non-empty array")
        completed_replay = _v3_completed_replay(params)
        if completed_replay is not None:
            return completed_replay
        resolved = _v3_resolve_task(params, require_task_ref=True)
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
        _governance_boundary_recheck(
            params,
            task,
            state,
            future_waves=params.get("future_waves"),
            results=results,
        )
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
                allow_visible_threads=bool(task.get("visible_thread_requested", False)),
            )
            if params.get("future_waves") is not None else None
        )
        rework_scope = (
            set(state.get("completed_gates", []))
            | set(state.get("skipped_gates", []))
            | set(active_gates(state))
        )
        inferred_rework = bool(
            future_waves
            and rework_scope.intersection(
                str(delegation.get("gate") or "")
                for wave in future_waves
                for delegation in wave.get("delegations", [])
            )
        )
        effective_rework = bool(params.get("rework", False)) or inferred_rework
        if len(results) != len(attempt_ids):
            raise ValueError(f"active wave requires exactly {len(attempt_ids)} result(s)")
        slots: dict[int, dict[str, Any]] = {}
        multiple = len(attempt_ids) > 1
        for index, result in enumerate(results, 1):
            if not isinstance(result, dict):
                raise ValueError("every result must be an object")
            allowed_result = {"worker", "attempt_result_ref", "dispatch_ref", "status", "reason", "next_strategy"}
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
            result_ref = str(result.get("attempt_result_ref") or "").strip()
            has_result = bool(result_ref)
            status_value = _v3_status(result.get("status"), has_attempt_result=has_result)
            if status_value == "passed":
                if not result_ref:
                    raise ValueError("successful results require attempt_result_ref from complete_attempt")
                if str(result.get("dispatch_ref") or "").strip():
                    raise ValueError("successful results use attempt_result_ref only; do not supply dispatch_ref")
                attempt = _attempt(state, attempt_ids[slot - 1])
                if result_ref != str(attempt.get("attempt_result_ref") or ""):
                    raise ValueError("successful result does not match the exact active attempt")
                canonical = attempt_protocol.get_attempt_result(
                    _task_document_root(task_dir, state["task_id"]),
                    task_id=state["task_id"], attempt_id=attempt_ids[slot - 1],
                )
                if (
                    canonical is None
                    or canonical.get("result_ref") != result_ref
                    or canonical.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
                ):
                    raise ValueError("successful results require a finalized canonical attempt result")
                if str(result.get("reason") or "").strip():
                    raise ValueError("successful results must not include reason")
                if str(result.get("next_strategy") or "").strip():
                    raise ValueError("successful results must not include next_strategy")
            else:
                if result_ref:
                    raise ValueError("non-success results must omit attempt_result_ref")
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
        # Reserve the server-owned transaction only after all slots, canonical results,
        # statuses, and future-wave overrides pass validation.
        old_params, reserved_attempt_ids, _, request_digest, replay = _v3_continue_context(params, task_dir, state, task_ref)
        if replay is not None:
            return replay
        if reserved_attempt_ids != attempt_ids:
            raise ValueError("the active wave changed while continue was being validated; retry with the latest step")
        completions: list[dict[str, Any]] = []
        for slot, attempt_id in enumerate(attempt_ids, 1):
            result = slots[slot]
            result_ref = str(result.get("attempt_result_ref") or "").strip()
            status_value = _v3_status(
                result.get("status"),
                has_attempt_result=bool(result_ref),
            )
            completion = {
                "attempt_id": attempt_id,
                "host_observation_source": "unattested_parent_result",
                "status": status_value,
            }
            if status_value == "passed":
                completion["attempt_result_ref"] = result_ref
            else:
                completion["reason"] = str(result["reason"])
                if str(result.get("next_strategy") or "").strip():
                    completion["next_strategy"] = redact(result["next_strategy"], 1000)
            completions.append(completion)
        old_params["completions"] = completions
        if future_waves is not None:
            old_params["future_waves"] = future_waves
            old_params["allow_rework"] = effective_rework
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
            resolved = _v3_resolve_task(params, require_task_ref=True)
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
        set(payload) & {"task_id", "principal", "thread_id", "attempt_id", "profile", "submission_id"}
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
    # ordinary-chat route; it is not a single-question answer call.
    if command == "answer" and "canonical_answers" in payload:
        command = "ask"
    if "answer" in payload or "answer_en" in payload:
        if command not in {"ask", "answer"}:
            raise ValueError("question translation must use answer and answer_en with the same question_ref")
        if "answer" not in payload:
            raise ValueError("question answer requires the original answer copied from result.answer_original")
        command = "answer"
    payload["command"] = command
    if command == "ask" and not payload.get("question_id") and not str(payload.get("question") or "").strip():
        raise ValueError("question ask requires the worker's exact question_ref")
    if command == "answer" and not payload.get("question_id"):
        raise ValueError("question answer requires question_ref")
    if command == "answer" and "answer" not in payload:
        raise ValueError("question answer requires answer")
    return payload


def _v3_question_resume_contract(
    result: dict[str, Any],
    state: dict[str, Any],
) -> tuple[dict[str, str] | None, str | None]:
    """Derive the only resumable worker identity from a durable answer record.

    A coordinator can route the already-existing native child, but it must not
    reconstruct or choose its worker identity.  In particular, an answered
    stale question is useful audit history, not an authorization to wake a
    replacement or an old worker.  The public response therefore carries a
    contract only when the answer record and current active slot agree.
    """
    is_batch = bool(result.get("batch_ref"))
    durable = result.get("durable") if isinstance(result.get("durable"), dict) else {}
    record = durable.get("batch") if is_batch else result.get("question")
    if not isinstance(record, dict):
        return None, "question_answered_without_canonical_resume_record"

    ref_key = "batch_ref" if is_batch else "question_ref"
    record_ref_key = "batch_id" if is_batch else "question_id"
    result_ref = result.get("batch_ref") if is_batch else result.get("question_id")
    try:
        record_ref = safe_id(str(record.get(record_ref_key) or ""))
        announced_ref = safe_id(str(result_ref or ""))
        attempt_id = safe_id(str(record.get("attempt_id") or ""))
        profile = canonical_profile(record.get("profile") or "")
    except ValueError:
        return None, "question_answered_with_invalid_resume_identity"
    if announced_ref != record_ref or profile not in AGENTS:
        return None, "question_answered_with_invalid_resume_identity"

    attempt = next(
        (
            item for item in state.get("attempts", [])
            if isinstance(item, dict) and str(item.get("attempt_id") or "") == attempt_id
        ),
        None,
    )
    if (
        not isinstance(attempt, dict)
        or attempt.get("invalidated")
        or attempt.get("status") not in {AWAITING_HOST_SPAWN, "running"}
        or attempt.get("gate") not in set(active_gates(state))
        or canonical_profile(attempt.get("profile") or "") != profile
    ):
        return None, "question_answered_for_noncurrent_attempt"

    return {
        ref_key: record_ref,
        "attempt_id": attempt_id,
        "profile": profile,
        "poll_action": "poll_batch" if is_batch else "poll",
    }, None


def _v3_question_response(response: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    status_value = str(result.get("status") or "").strip()
    if status_value == "answered":
        resume_contract, resume_reason = _v3_question_resume_contract(result, state)
        if resume_contract is None:
            response["outcome"] = "question_answered_not_resumable"
            response["resume_reason"] = resume_reason
            response["next_action"] = (
                f"{COORDINATOR_LOCK} The durable answer is not bound to a current active worker slot. Do not use "
                "followup_task, spawn a replacement, or advance the wave from this response; inspect the exact task "
                "state through Cortex before any recovery."
            )
            return response
        response["outcome"] = "question_answered"
        response["resume_contract"] = resume_contract
        response["next_action"] = (
            f"{COORDINATOR_LOCK} Resume the exact same already-existing native worker with followup_task; the parent "
            "retains its original native target. Copy resume_contract verbatim into worker_question and require that "
            "exact poll on the same attempt. Do not supply a host target, reconstruct any field, spawn a replacement, "
            "or advance the wave before its result is recorded."
        )
    elif status_value == "awaiting_translation":
        response["outcome"] = "awaiting_translation"
        question_ref = str(result.get("batch_ref") or result.get("question_id") or "")
        if result.get("batch_ref"):
            required_keys = [str(item) for item in result.get("translation_required_for") or []]
            translation_payload: dict[str, Any] = {
                "question_ref": question_ref,
                "canonical_answers": {
                    key: "<canonical English translation of that free-text answer or custom response>"
                    for key in required_keys
                },
                "translated_by": "coordinator",
            }
        else:
            translation_payload = {
                "question_ref": question_ref,
                "answer": result.get("answer_original"),
                "answer_en": "<canonical English translation of the free-text answer>",
            }
        response["translation_request"] = {
            "intent": "question",
            "payload": translation_payload,
            "answer_option_ids": result.get("answer_option_ids") or [],
            "answer_custom_original": result.get("answer_custom_original") or {},
        }
        response["next_action"] = (
            f"{COORDINATOR_LOCK} Translate only result.answer_original free text and result.answer_custom_original custom responses into canonical English, preserve "
            "result.answer_option_ids, then call manage_orchestration exactly once with translation_request. "
            "Do not inspect skills, plugin source/cache, or runtime code to infer fields, and do not resume the worker "
            "until Cortex records both representations."
        )
    elif status_value == "superseded":
        response["outcome"] = "batch_superseded"
        response["next_action"] = (
            f"{COORDINATOR_LOCK} Do not resume the worker from this superseded batch. Keep the durable task revision "
            "as the source of truth and wait for its current dispatch or question batch."
        )
    elif status_value in {"invalid_answer", "pending_user_input", "pending_user_message"}:
        response["outcome"] = "awaiting_user"
        if isinstance(result.get("chat_interaction"), dict):
            response["chat_interaction"] = result["chat_interaction"]
        response["next_action"] = (
            f"{COORDINATOR_LOCK} Render result.chat_interaction.user_view only in the user's language as one ordinary "
            "final assistant message. Show one concrete decision, its options, trade-offs, and recommendation; do not "
            "copy question keys, IDs, cursors, tool instructions, or the remaining batch. Do not call a UI/input/"
            "approval/elicitation tool. End this turn. On the user's next message, preserve the exact answer, record it "
            "against this same durable question_ref, then resume the exact same worker; never replace it or advance the "
            "wave first."
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
        "localized_custom_label",
    }
    unknown = sorted(set(payload) - {"decision", "feedback", "request_id", *localization_fields})
    if unknown:
        raise ValueError("unsupported plan_approval payload fields: " + ", ".join(unknown))
    raw = str(payload.get("decision") or "prompt").strip().lower().replace("-", "_").replace(" ", "_")
    decision = {
        "prompt": "prompt", "ask": "prompt", "review": "prompt",
        "approve": "approve", "approved": "approve", "accept": "approve",
        "cancel": "cancel", "canceled": "cancel", "cancelled": "cancel",
        "revise": "revise", "changes": "revise", "request_changes": "revise",
    }.get(raw)
    if not decision:
        raise ValueError("plan_approval decision must be prompt, approve, cancel, or revise")
    feedback = str(payload.get("feedback") or "").strip()
    request_id = str(payload.get("request_id") or "").strip()
    if decision == "prompt" and (feedback or request_id):
        raise ValueError("plan_approval prompt does not accept feedback")
    if decision == "revise" and not feedback:
        raise ValueError("plan_approval revise requires non-empty feedback")
    if decision != "prompt" and any(str(payload.get(field) or "").strip() for field in localization_fields):
        raise ValueError("plan approval localization fields are accepted only with decision=prompt")
    normalized = {
        "decision": decision,
        **({"feedback": feedback} if feedback else {}),
        **({"request_id": redact(request_id, 200)} if request_id else {}),
    }
    for field in localization_fields:
        if str(payload.get(field) or "").strip():
            normalized[field] = redact(str(payload[field]).strip(), 300)
    return normalized


def _v3_plan_approval_request_id(state: dict[str, Any], approval: dict[str, Any]) -> str:
    """Derive the opaque id bound to one pending plan approval revision."""
    pending_basis = approval.get("pending_basis")
    if not isinstance(pending_basis, dict):
        pending_basis = {
            key: approval.get(key)
            for key in (
                "pipeline_contract_version", "plan_revision", "plan_result_ref",
                "verified_predecessor_digest", "semantic_pipeline_version",
                "semantic_future_pipeline_digest",
            )
            if approval.get(key) is not None
        }
    seed = {
        "task_id": str(state.get("task_id") or ""),
        "pending_basis": pending_basis,
    }
    return "plan-approval-" + digest_text(json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")))[:32]


PLAN_APPROVAL_TRANSLATIONS: dict[str, tuple[str, str, str, str, str]] = {
    "en": ("Approve the completed plan?", "Plan review", "Approve", "Cancel", "Other answer / requested plan changes"),
    "ru": ("Утвердить завершённый план?", "Проверка плана", "Утвердить", "Отмена", "Другой ответ / требуемые изменения плана"),
    "uk": ("Затвердити завершений план?", "Перевірка плану", "Затвердити", "Скасувати", "Інша відповідь / потрібні зміни плану"),
    "ro": ("Aprobați planul finalizat?", "Revizuirea planului", "Aprobă", "Anulează", "Alt răspuns / modificări solicitate ale planului"),
    "de": ("Den fertigen Plan genehmigen?", "Planprüfung", "Genehmigen", "Abbrechen", "Andere Antwort / gewünschte Planänderungen"),
    "fr": ("Approuver le plan finalisé ?", "Examen du plan", "Approuver", "Annuler", "Autre réponse / modifications demandées au plan"),
    "es": ("¿Aprobar el plan finalizado?", "Revisión del plan", "Aprobar", "Cancelar", "Otra respuesta / cambios solicitados al plan"),
    "it": ("Approvare il piano completato?", "Revisione del piano", "Approva", "Annulla", "Altra risposta / modifiche richieste al piano"),
    "pt": ("Aprovar o plano concluído?", "Revisão do plano", "Aprovar", "Cancelar", "Outra resposta / alterações solicitadas ao plano"),
    "pl": ("Zatwierdzić ukończony plan?", "Przegląd planu", "Zatwierdź", "Anuluj", "Inna odpowiedź / wymagane zmiany planu"),
    "zh": ("批准已完成的计划？", "计划审核", "批准", "取消", "其他答复 / 要求的计划更改"),
    "ja": ("完成した計画を承認しますか？", "計画の確認", "承認", "キャンセル", "その他の回答 / 必要な計画変更"),
    "ko": ("완료된 계획을 승인하시겠습니까?", "계획 검토", "승인", "취소", "기타 답변 / 요청한 계획 변경"),
    "el": ("Έγκριση του ολοκληρωμένου σχεδίου;", "Έλεγχος σχεδίου", "Έγκριση", "Ακύρωση", "Άλλη απάντηση / απαιτούμενες αλλαγές σχεδίου"),
}


def _v3_plan_approval_copy(state: dict[str, Any], localization: dict[str, Any]) -> tuple[str, str, str, str, str]:
    language = str(state.get("user_language") or "en").lower().split("-", 1)[0]
    translated = PLAN_APPROVAL_TRANSLATIONS.get(language)
    supplied = tuple(
        str(localization.get(field) or "").strip()
        for field in (
            "localized_prompt", "localized_title", "localized_approve", "localized_cancel",
            "localized_custom_label",
        )
    )
    if all(supplied):
        return supplied
    if language != "en" and translated is None:
        raise ValueError(
            "non-English plan approval requires localized_prompt, localized_title, localized_approve, "
            "localized_cancel, and localized_custom_label"
        )
    if any(supplied):
        raise ValueError("plan approval localization requires all five localized fields")
    return translated or PLAN_APPROVAL_TRANSLATIONS["en"]


def _v3_prompt_plan_approval(
    state: dict[str, Any],
    task_ref: str,
    localization: dict[str, Any],
    project_root: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return a detailed ordinary-chat approval boundary without nested UI."""
    approval = _plan_approval(state)
    if approval.get("policy") != "required":
        raise ValueError("this task does not require post-plan approval")
    if approval.get("status") != "awaiting_user":
        raise ValueError("there is no pending plan approval for this task")
    review = dict(approval.get("review") or {})
    prompt, title, approve_label, cancel_label, custom_label = _v3_plan_approval_copy(state, localization)
    request_id = str(approval.get("request_id") or _v3_plan_approval_request_id(state, approval))
    material_concerns = [*(review.get("findings") or []), *(review.get("uncertainty") or [])]
    planner_recommendation = str(review.get("recommendation") or "approve").strip().lower()
    recommended_decision = "revise" if material_concerns or planner_recommendation == "revise" else "approve"
    recommendation_rationale = (
        "Request revision because the current plan still records material findings or uncertainty that should be "
        "resolved before implementation."
        if material_concerns else
        str(
            review.get("recommendation_rationale")
            or (
                "Request revision because the Planner marked the plan for revision even though no separate finding "
                "or uncertainty was recorded."
                if planner_recommendation == "revise" else
                "Approve because the reviewed plan defines executable work packages and verification without "
                "recording an unresolved material concern."
            )
        )
    )
    interaction = {
        "schema": "cortex/chat-interaction/v1",
        "kind": "plan_approval",
        "interaction_ref": request_id,
        "title": title,
        "prompt": prompt,
        "plan": {
            "objective": review.get("objective"),
            "summary": review.get("summary"),
            "work_packages": review.get("work_packages") or [],
            "verification": review.get("verification") or [],
            "risks_and_uncertainty": [*(review.get("findings") or []), *(review.get("uncertainty") or [])],
            "risks": review.get("risks") or [],
            "requirement_coverage": review.get("requirement_coverage") or [],
            "resolved_questions": review.get("resolved_questions") or [],
            "tracker": review.get("plan_tracker"),
            "remaining_phases": review.get("remaining_phases") or [],
            "result_ref": review.get("result_ref"),
        },
        "choices": [
            {"id": "approve", "label": approve_label, "meaning": "Approve this exact plan revision and continue with its implementation dispatches."},
            {"id": "revise", "label": custom_label, "meaning": "Describe the required changes; Cortex will preserve the exact text as Planner feedback and request a revised plan."},
            {"id": "cancel", "label": cancel_label, "meaning": "Keep the plan pending and stop until a later user message."},
        ],
        "actions": [
            {
                "id": "approve",
                "arguments": {
                    "project_root": project_root,
                    "task_ref": task_ref,
                    "intent": "plan_approval",
                    "payload": {"decision": "approve", "request_id": request_id},
                },
            },
            {
                "id": "cancel",
                "arguments": {
                    "project_root": project_root,
                    "task_ref": task_ref,
                    "intent": "plan_approval",
                    "payload": {"decision": "cancel", "request_id": request_id},
                },
            },
        ],
        "llm_recommendation": {
            "choice_id": recommended_decision,
            "rationale": recommendation_rationale,
            "planner_recommendation": planner_recommendation,
        },
        "response_instructions": (
            "Reply in your next ordinary chat message with approval, cancellation, or the exact changes you want. "
            "Any substantive change request is treated as revise, not approval."
        ),
        "coordinator_contract": (
            "Use only interaction.user_view for the final ordinary user-language message. Never copy internal plan "
            "objects, paths, dependencies, result or request identifiers, dispatch instructions, or validation details "
            "into that message. Show its bounded summary, the single question, and the recommendation from "
            "llm_recommendation; then wait for one unambiguous approve/revise/cancel response. Preserve requested "
            "changes verbatim as revise feedback and use the exact interaction_ref internally. End the turn immediately "
            "after presenting this user_view; do not continue orchestration in the same turn."
        ),
    }
    language = str(state.get("user_language") or "en")
    is_ru = language.lower().startswith("ru")
    public_steps = [
        "Проверить цель и границы задачи." if is_ru else "Confirm the requested outcome and scope.",
        "Выполнить запланированную работу." if is_ru else "Complete the planned work.",
        "Запустить предусмотренные проверки." if is_ru else "Run the planned verification.",
        "Проверить результат и закрыть задачу." if is_ru else "Review the result and close the task.",
    ]
    public_recommendation = (
        "Рекомендация: доработать план — остаётся существенная неопределённость."
        if recommended_decision == "revise" and is_ru else
        "Рекомендация: утвердить план — существенные неопределённости закрыты и проверки конкретны."
        if is_ru else
        "Recommendation: revise — a material uncertainty remains."
        if recommended_decision == "revise" else
        "Recommendation: approve — material uncertainties are closed and verification is concrete."
    )
    interaction["user_view"] = render_plan(
        str(review.get("summary") or review.get("objective") or ("Проверка плана" if is_ru else "Plan review")),
        public_steps,
        question=("Утвердить план, запросить доработку или отменить?" if is_ru else "Approve the plan, request a revision, or cancel?"),
        recommendation=public_recommendation,
        config={
            "communication_profile": state.get("communication_profile") or "natural",
            "user_language": language,
        },
    )
    plan_config = {
        "communication_profile": state.get("communication_profile") or "natural",
        "user_language": language,
    }
    recommendation_rendered = render(
        public_recommendation,
        kind="question",
        next_step=("Утвердите план, запросите доработку или отмените." if is_ru else "Approve, revise, or cancel the plan."),
        config=plan_config,
    )
    why_rendered = render(
        "Решение определяет, можно ли перейти к выполнению плана." if is_ru else
        "Your decision determines whether the plan can move to implementation.",
        kind="question",
        next_step=("Утвердите план, запросите доработку или отмените." if is_ru else "Approve, revise, or cancel the plan."),
        config=plan_config,
    )
    interaction["user_view"]["requires_user_decision"] = True
    interaction["user_view"]["recommendation"] = recommendation_rendered["message"]
    interaction["user_view"]["risks"] = public_risks(
        review.get("risks") or review.get("uncertainty") or review.get("findings"),
        config=plan_config,
        limit=4,
    )
    interaction["user_view"]["why_it_matters"] = why_rendered["message"]
    quality = dict(interaction["user_view"].get("quality") or {})
    quality["fallback_applied"] = bool(
        quality.get("fallback_applied")
        or recommendation_rendered["quality"].get("fallback_applied")
        or why_rendered["quality"].get("fallback_applied")
    )
    quality["ok"] = bool(
        quality.get("ok")
        and recommendation_rendered["quality"].get("ok")
        and why_rendered["quality"].get("ok")
    )
    interaction["user_view"]["quality"] = quality
    interaction["internal"] = {
        "interaction_ref": request_id,
        "plan": interaction["plan"],
        "choices": interaction["choices"],
        "actions": interaction["actions"],
        "llm_recommendation": interaction["llm_recommendation"],
    }
    return None, {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": True,
        "outcome": "awaiting_plan_approval",
        "task_ref": task_ref,
        "dispatches": [],
        "plan_review": review,
        "chat_interaction": interaction,
        "plan_approval_interaction": interaction,
        "next_action": (
            f"{COORDINATOR_LOCK} Render chat_interaction.user_view only in the user's language as one ordinary final "
            "assistant message. Include its bounded plan summary and explicit approve/revise/cancel instructions. "
            "Do not call a UI/input/approval/elicitation tool. End the turn and wait for the user's next message; never "
            "infer approval from silence or from this prompt call."
        ),
    }


def _v3_follow_up_payload(value: object) -> dict[str, Any]:
    """Normalize a user-authored corrective task without reopening its source."""
    if not isinstance(value, dict):
        raise ValueError("follow_up requires payload with the exact corrective user_request")
    allowed = {
        "user_request", "requirements", "constraints", "acceptance_criteria", "scope", "allowed_paths",
        "verification", "budget", "pause_conditions", "user_language", "language",
        "complexity", "replan_limit", "plan_approval", "result_refs", "task_ref",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unsupported follow_up payload fields: " + ", ".join(unknown))
    user_request = canonicalize_desktop_cortex_request(value.get("user_request"))
    if not user_request:
        raise ValueError("follow_up payload.user_request must preserve the exact corrective user request")
    result_refs = value.get("result_refs", [])
    if not isinstance(result_refs, list) or len(result_refs) > 32:
        raise ValueError("follow_up payload.result_refs must be an array of at most 32 source result refs")
    normalized_refs = [safe_id(str(item)) for item in result_refs]
    if not all(normalized_refs) or len(normalized_refs) != len(set(normalized_refs)):
        raise ValueError("follow_up payload.result_refs must contain unique non-empty source result refs")
    task = {key: item for key, item in value.items() if key not in {"result_refs", "task_ref"}}
    task["user_request"] = user_request
    return {"task": task, "result_refs": normalized_refs}


def _v3_follow_up_context(
    source_dir: Path,
    source_state: dict[str, Any],
    source_task: dict[str, Any],
    source_task_ref: str,
    requested_result_refs: list[str],
) -> dict[str, Any]:
    """Build only source-derived, Desktop-openable corrective-task context."""
    if source_state.get("status") != "completed":
        raise ValueError("follow_up requires a completed source task; use rework while the original task is still active")
    available = [
        safe_id(str(item.get("attempt_result_ref") or ""))
        for item in source_state.get("attempts", [])
        if isinstance(item, dict) and str(item.get("attempt_result_ref") or "").strip()
    ]
    selected = requested_result_refs or available[-16:]
    unknown = sorted(set(selected) - set(available))
    if unknown:
        raise ValueError("follow_up result_refs do not belong to the completed source task: " + ", ".join(unknown))
    handoff_paths = sorted(
        path for path in (source_dir / "handoffs").glob("*.json")
        if path.is_file() and not path.is_symlink() and not path.name.endswith("-manifest.json")
    )
    return {
        "schema": "cortex/follow-up/v1",
        "source_task_ref": source_task_ref,
        "source_task_id": source_state["task_id"],
        "source_task_directory": str(source_dir),
        "source_user_request": redact(source_task.get("user_request", ""), 1000),
        "source_handoff_path": str(handoff_paths[-1]) if handoff_paths else None,
        "source_result_refs": selected,
        "created_at": now(),
    }


def _revision_replacement_waves(
    plan: dict[str, Any],
    impact: dict[str, Any],
    task: dict[str, Any],
    *,
    full_governance: bool,
) -> list[dict[str, Any]]:
    """Build a compact replacement from the first materially affected gate."""
    gate_order = {
        gate: index for index, gate in enumerate((
            "scope", "discover", "architecture", "database_architecture", "plan",
            "implementation", "qa", "security", "performance", "accessibility",
            "ux", "review", "documentation", "governance_close", "close",
        ))
    }
    compact: list[dict[str, Any]] = []
    for wave in plan.get("waves") or []:
        delegations = []
        for item in wave.get("delegations") or []:
            if not isinstance(item, dict):
                continue
            gate = str(item.get("gate") or "")
            if gate in GOVERNANCE_FULL_GATES:
                continue
            delegations.append({
                "gate": gate,
                "agent": str(item.get("agent") or _default_profile_for_gate(gate)),
                **(
                    {"selection_reason": str(item["selection_reason"])}
                    if item.get("selection_reason") else {}
                ),
            })
        if delegations:
            compact.append({"wave_id": str(wave.get("wave_id") or "wave"), "delegations": delegations})

    required = str(impact.get("earliest_affected_gate") or "")

    def gates() -> list[str]:
        return [
            str(item.get("gate") or "")
            for wave in compact for item in wave.get("delegations") or []
        ]

    def insert_gate(gate: str, *, before: set[str]) -> None:
        if gate in gates():
            return
        position = next(
            (
                index for index, wave in enumerate(compact)
                if any(str(item.get("gate") or "") in before for item in wave.get("delegations") or [])
            ),
            len(compact),
        )
        compact.insert(position, {
            "wave_id": "semantic-" + gate.replace("_", "-"),
            "delegations": [{
                "gate": gate,
                "agent": _default_profile_for_gate(gate),
                "selection_reason": f"A material active-task revision requires the `{gate}` contract.",
            }],
        })

    if required and required not in gates():
        required_rank = gate_order.get(required, 0)
        insert_gate(required, before={gate for gate, rank in gate_order.items() if rank > required_rank})
    if "security" in (impact.get("categories") or []):
        insert_gate("security", before={"review", "documentation", "close"})

    start = next(
        (
            index for index, wave in enumerate(compact)
            if any(str(item.get("gate") or "") == required for item in wave.get("delegations") or [])
        ),
        0,
    )
    replacement = compact[start:]
    if full_governance:
        replacement = _append_governance_waves(
            replacement,
            {**task, "governance": task.get("governance") or {"effective_mode": "full"}},
        )
    return replacement


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
        impact = classify_revision_impact(
            canonical,
            pipeline=state.get("current_pipeline") or [],
            current_gates=current_gates,
            active_attempt_ids=[str(item.get("attempt_id")) for item in active_attempts],
        )
        current_governance = (
            task_definition.get("governance")
            if isinstance(task_definition.get("governance"), dict)
            else {}
        )
        resolved_governance = resolve_governance(
            root,
            complexity=task_definition.get("complexity", state.get("complexity", "C2")),
            requested_mode=current_governance.get(
                "requested_mode", task_definition.get("governance_mode", "auto")
            ),
            objective="\n".join(
                item for item in (str(task_definition.get("user_request") or "").strip(), canonical) if item
            ),
            requirements=[*(task_definition.get("requirements") or []), canonical],
            scope=task_definition.get("scope", []),
            allowed_paths=task_definition.get("allowed_paths", []),
            task=task_definition,
            initiative_ref=task_definition.get("initiative_ref"),
        )
        governance_escalated = (
            resolved_governance.get("effective_mode") == "full"
            and current_governance.get("effective_mode") != "full"
        )
        if governance_escalated:
            task_definition["governance"] = resolved_governance
            state["governance"] = resolved_governance
            # Full governance requires every gate decision to be bound to the
            # immutable worker result that informed it.  A C1 task normally
            # has no delegation-receipt requirement, but an active material
            # steer can promote it after its first worker is already live.
            # Enable the existing receipt path before that worker completes
            # so its result and every replacement gate can satisfy the full
            # governance audit contract without caller-supplied lifecycle
            # data or a synthetic evidence record.
            state["require_delegation"] = True
            impact["governance_escalation"] = {
                "from": current_governance.get("effective_mode") or "minimal",
                "to": "full",
                "reasons": list(resolved_governance.get("reasons") or []),
            }
        impact["active_attempt_actions"] = [
            {"attempt_id": str(item.get("attempt_id")), "action": "resume_worker"}
            for item in active_attempts if (item.get("host_spawn") or {}).get("agent_id")
        ]
        plan = _load_orchestrate_plan(task_dir, state)
        if governance_escalated or impact.get("required_gate_missing"):
            replacement = _revision_replacement_waves(
                plan,
                impact,
                task_definition,
                full_governance=resolved_governance.get("effective_mode") == "full",
            )
            if replacement:
                impact["replacement_waves"] = replacement
        plan_revision = None
        if impact.get("requires_plan_revision"):
            plan_revision = db_append_plan_revision(
                root, state["task_id"], task_revision=revision_number,
                impact=impact, plan=plan, status="active",
            )
        earliest_affected = str(impact.get("earliest_affected_gate") or "")
        active_gate = current_gates[0] if current_gates else ""
        if earliest_affected and (
            earliest_affected != active_gate
            or governance_escalated
            or impact.get("replacement_waves")
        ):
            # Preserve the same live worker for the user's steer.  The engine
            # consumes this receipt after that worker AttemptResults and reopens the
            # earliest affected gate before any downstream dispatch.  A
            # governance escalation (or any server-built replacement) must
            # remain durable even when the earliest affected gate is the
            # active gate: that worker can finish before the boundary, but
            # the replacement still has to insert its required waves.
            state["pending_revision_impact"] = {
                **impact,
                "task_revision": revision_number,
                "active_gate_at_revision": active_gate,
                "recorded_at": now(),
            }
        task_definition["task_revision"] = revision_number
        # Keep one canonical latest-intent projection alongside the immutable
        # revision history.  Workers and scheduling decisions must consult
        # this projection, so a later correction (for example production ->
        # local) cannot be shadowed by the original task objective.
        task_definition["current_user_intent"] = canonical
        task_definition["current_user_intent_revision"] = revision_number
        retained_requirements = list(task_definition.get("requirements") or [])
        if canonical not in retained_requirements:
            retained_requirements.append(canonical)
        task_definition["requirements"] = retained_requirements[-100:]
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
                f"Continue the same attempt {attempt['attempt_id']}. Reconcile this steer with completed work; "
                f"Cortex classified the earliest affected gate as {impact.get('earliest_affected_gate') or active_gate}. "
                f"include exactly `Task revision reviewed: {revision_number}` in the final AttemptResult claims. Do not spawn or "
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
            "recover_inspect": "recover_inspect", "recover_lifecycle": "recover_inspect",
            "resume": "resume", "retry": "resume", "continue_blocked": "resume",
            "deactivate": "deactivate", "normal": "deactivate", "stop_session": "deactivate",
            "lane": "lane", "resource": "resource", "question": "question",
            "plan_approval": "plan_approval", "approve_plan": "plan_approval", "plan_review": "plan_approval",
            "follow_up": "follow_up", "followup": "follow_up", "correct": "follow_up", "corrective_task": "follow_up",
            "steer": "steer", "amend": "steer", "revise_active_task": "steer",
            "prune": "prune", "cleanup": "prune",
            "maintenance": "maintenance", "health": "maintenance", "sqlite_health": "maintenance",
            "artifacts": "artifacts", "artifact": "artifacts", "documents": "artifacts",
        }
        intent = aliases.get(intent_raw)
        if not intent:
            suggestions = difflib.get_close_matches(intent_raw, sorted(aliases), n=3)
            raise ValueError("management intent is not recognized" + (f"; try {', '.join(suggestions)}" if suggestions else ""))
        if intent == "prune":
            return prune_orchestration_state(params)
        if intent == "maintenance":
            # Health inspection must not initialize or migrate a missing
            # ledger. The controlled module validates the existing root and
            # permits writes only through explicit, confirmed actions.
            root = existing_ledger_root(params)
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
        if not str(params.get("task_ref") or "").strip():
            return _v3_task_ref_required_error(f"manage_orchestration intent '{intent}'")
        resolved = _v3_resolve_task(
            params,
            include_completed=bool(str(params.get("task_ref") or "").strip()) and intent in {"inspect", "recover_inspect", "deactivate", "follow_up"},
            require_task_ref=True,
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
                follow_up["result_refs"],
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
                "source_result_refs": source_context["source_result_refs"],
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
        if intent == "recover_inspect":
            # Lifecycle recovery is deliberately distinct from ordinary
            # inspection: status reads must not contend on the mutation lock
            # or silently expire/retire attempts.  The server derives the
            # exact repair scope from current durable state; callers cannot
            # select attempts, receipts, or identities to mutate.
            return _v3_response(
                _orchestrate_inspect({**common, "payload": {"mode": "recover_lifecycle"}}),
                task_ref,
                include_result=True,
            )
        normalized_payload = None
        if intent == "question":
            normalized_payload = _v3_question_management_payload(params.get("payload"))
            if normalized_payload.get("command") == "answer":
                normalized_payload["resume_context"] = {
                    "source": "manage_orchestration",
                    "user_language": str(state.get("user_language") or "en"),
                }
        elif intent == "plan_approval":
            normalized_payload = _v3_plan_approval_payload(params.get("payload"))
            if normalized_payload["decision"] == "prompt":
                normalized_payload, prompt_response = _v3_prompt_plan_approval(
                    state,
                    task_ref,
                    normalized_payload,
                    str(params.get("project_root") or ""),
                )
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
            recovery: dict[str, Any] = {}
            if intent == "resume" and params.get("payload") is not None:
                raw_recovery = params.get("payload")
                if not isinstance(raw_recovery, dict):
                    raise ValueError("resume payload must be an object")
                unknown_recovery = sorted(set(raw_recovery) - {"future_waves", "rework"})
                if unknown_recovery:
                    raise ValueError("unsupported resume recovery fields: " + ", ".join(unknown_recovery))
                if raw_recovery.get("future_waves") is None:
                    raise ValueError("resume recovery payload requires future_waves")
                if not str(params.get("reason") or "").strip():
                    raise ValueError("resume recovery future_waves requires a concise reason")
                recovery["future_waves"] = _v3_compact_waves(
                    raw_recovery["future_waves"],
                    task_definition,
                    completed_gates=(
                        set(state.get("completed_gates", []))
                        | set(state.get("skipped_gates", []))
                        | set(active_gates(state))
                    ),
                    project_root=select_project_root(params),
                    allow_visible_threads=bool(task_definition.get("visible_thread_requested", False)),
                )
                recovery["allow_rework"] = True
                if raw_recovery.get("rework") is not None:
                    requested_rework = canonical_pipeline_gate(str(raw_recovery["rework"]).strip())
                    if requested_rework not in AVAILABLE_GATES:
                        raise ValueError("resume recovery rework must name a supported gate")
                    recovery["rework_gate"] = requested_rework
            old = orchestrate({
                **common,
                "operation": intent,
                "submission_id": submission_id,
                "reason": params.get("reason"),
                **recovery,
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
            return _v3_question_response(response, state)
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


def manage_governance(params: dict[str, Any]) -> dict[str, Any]:
    """Expose the additive v11 governance ledger without widening lifecycle calls."""
    try:
        project = select_project_root(params)
        allowed = {
            "project_root", "action", "principal", "thread_id", "coordinator_capability", "coordinator_recovery_proof", "previous_coordinator_recovery_proof", "entity", "initiative_ref", "parent_ref", "title", "goal",
            "owner", "risk", "acceptance_oracle_artifact_ref", "task_id", "lane_id", "relationship", "milestone",
            "deliverable", "corrective", "expected_revision", "status", "evidence", "source_type", "source_ref",
            "target_type", "target_ref", "dependency_type", "dependency_ref", "record_ref", "record_type", "content",
            "created_by", "supersedes", "expires_at", "approval_basis", "content_artifact_ref", "link_ref", "finding_fingerprint", "evidence_ref", "fingerprint",
            "findings", "threshold", "window_days", "proposal_ref", "trigger", "reason",
            "limit", "offset", "task_ref", "capability_generation", "submission_id",
        }
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise GovernanceError("unsupported governance fields: " + ", ".join(unknown), code="unsupported_fields")
        requested_action = _capability_action(params.get("action"))
        if requested_action in CAPABILITY_RECOVERY_ACTIONS:
            if requested_action == "acknowledge_coordinator_recovery":
                return _acknowledge_coordinator_recovery(params, project)
            return _recover_coordinator_capability(params, project)
        principal = str(params.get("principal") or "").strip()
        thread_id = str(params.get("thread_id") or "").strip()
        supplied_capability = str(params.get("coordinator_capability") or "").strip().lower()
        if not principal or not thread_id:
            if principal or thread_id:
                raise GovernanceError(
                    "governance requests must provide both principal and thread_id, or neither",
                    code="coordinator_authorization_required",
                )
            resolved_identity = _coordinator_identity_for_capability(
                ledger_root({"project_root": str(project)}),
                supplied_capability,
            )
            if resolved_identity is None:
                raise GovernanceError(
                    "governance capability is not bound to one active coordinator task",
                    code="coordinator_capability_invalid",
                )
            _, principal, thread_id = resolved_identity
        if not COORDINATOR_CAPABILITY_RE.fullmatch(supplied_capability):
            raise GovernanceError(
                "governance mutations require the server-issued coordinator capability from start_orchestration",
                code="coordinator_capability_required",
            )
        try:
            activation = require_activation(
                {"project_root": str(project), "principal": principal, "thread_id": thread_id}
            )
        except ValueError as exc:
            raise GovernanceError(str(exc), code="coordinator_authorization_required") from exc
        bound_principal = str(activation.get("principal") or "").strip()
        bound_thread = str(activation.get("thread_id") or "").strip()
        if not bound_principal or not bound_thread:
            raise GovernanceError("active coordinator activation has no bound identity", code="coordinator_authorization_required")
        if principal != bound_principal or thread_id != bound_thread:
            raise GovernanceError(
                "governance request identity does not match the active coordinator activation",
                code="coordinator_authorization_required",
            )
        bound_task_id = str(activation.get("task_id") or "").strip()
        if not _coordinator_capability_matches(
            ledger_root({"project_root": str(project)}),
            bound_task_id,
            supplied_capability,
        ):
            raise GovernanceError(
                "governance request does not carry the server-issued coordinator capability",
                code="coordinator_capability_invalid",
            )
        claims = _coordinator_capability_claims_for_task(
            ledger_root({"project_root": str(project)}),
            bound_task_id,
        )
        if claims is None:
            raise GovernanceError(
                "governance capability has no valid server-owned claims",
                code="coordinator_capability_invalid",
            )
        payload = {
            key: value
            for key, value in params.items()
            if key not in {"project_root", "principal", "thread_id", "coordinator_capability"}
        }
        # The active server activation, never caller JSON, owns the actor
        # identity used in immutable governance rows and approvals.
        payload["created_by"] = bound_principal
        _authorize_governance_capability_claim(claims, payload)
        result = manage_governance_service(
            ledger_root({"project_root": str(project)}), payload, actor_role="coordinator"
        )
        return {
            "schema": "cortex/governance/v1",
            "ok": True,
            "outcome": "governance_updated",
            "action": payload.get("action"),
            "authorization": {
                "actor": "coordinator",
                "source": "server_activation",
                "principal": bound_principal,
                "thread_id": bound_thread,
                "capability_kind": claims["kind"],
                "generation": claims["generation"],
            },
            "result": result,
        }
    except GovernanceError as exc:
        return {
            "schema": "cortex/governance/v1", "ok": False, "outcome": "needs_input", "code": exc.code,
            "diagnostics": [{"code": exc.code, "message": redact(str(exc), 1000)}],
            "next_action": f"{COORDINATOR_LOCK} Correct the named governance input and retry the same action without changing unrelated task state.",
        }
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        return {
            "schema": "cortex/governance/v1", "ok": False, "outcome": "needs_input", "code": "governance_failed",
            "diagnostics": [{"code": "governance_failed", "message": redact(str(exc), 1000)}],
            "next_action": f"{COORDINATOR_LOCK} Correct the governance request or result the bounded ledger error.",
        }


# ``sync-cortex.sh`` validates the server through ``importlib`` without
# pre-registering that transient module.  Runtime adapters rely on the public
# facade, so provide a snapshot only for that validation path.
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
from cortex_runtime.delegation_service import (
    record_delegation as _record_delegation_service,
    rehydrate_dispatch_spawn_request as _rehydrate_dispatch_spawn_request,
)
from cortex_runtime.context_handoff import _context_handoff as _context_handoff_service
from cortex_runtime.artifact_transport import manage_task_artifacts
from cortex_runtime.health_maintenance import manage_health_maintenance
from cortex_runtime.questions import (
    _localized_question_view,
    _normalize_question_answer,
    _question_answer_from_content,
    _question_form_schema,
    _question_record_for_main,
    _question_record_view,
    answer_worker_question,
    cortex_question,
    get_worker_question_updates,
    list_worker_questions,
    publish_worker_question,
    worker_question,
)
from cortex_runtime.dispatch_briefing import (
    read_dispatch_briefing,
)
from cortex_runtime import attempt_protocol
from cortex_runtime.attempt_facade import (
    complete_attempt as complete_worker_attempt,
    record_attempt_event as record_worker_attempt_event,
    read_worker_result,
)


PIPELINE_OPERATION_SCHEMA = {"type": "object", "properties": {"op": {"type": "string", "enum": ["add", "remove", "move", "replace", "rework"]}, "gate": {"type": "string"}, "before": {"type": "string"}, "after": {"type": "string"}, "index": {"type": "integer"}, "with": {"type": "array", "items": {"type": "string"}}}, "required": ["op", "gate"]}
QUESTION_OPTION_SCHEMA = {
    "anyOf": [
        {"type": "string", "minLength": 1},
        {"type": "object", "additionalProperties": False, "properties": {"option_id": {"type": "string", "minLength": 1}, "label": {"type": "string", "minLength": 1}, "label_en": {"type": "string", "minLength": 1}, "label_localized": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "description_localized": {"type": "string"}}, "anyOf": [{"required": ["label"]}, {"required": ["label_en"]}]},
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
        "communication_profile": {"type": "string", "enum": ["natural", "neutral", "compact", "technical"], "default": "natural", "description": "User-facing message style for direct question projections; task state remains authoritative on the managed route."},
        "localized_question": {"type": "string", "description": "Main-coordinator display translation into the task's original user language; durable worker content remains English and unchanged."},
        "localized_header": {"type": "string"},
        "localized_options": {"type": "array", "maxItems": 32, "items": QUESTION_OPTION_SCHEMA},
        "localized_custom_label": {"type": "string"},
        "localized_questions": {"type": "array", "maxItems": 32, "items": {"type": "object"}, "description": "Batch-only ordered localized form projection. Use localized_question, localized_header, localized_options, and optional localized_custom_label; question/header/options/custom_label are stable aliases. Copy every canonical choice position, but use concrete outcome-based labels rather than placeholders."},
        "localized_batch": {"type": "object", "description": "Batch-only alias containing localized_questions under questions."},
        "answer_submission_id": {"type": "string", "description": "Stable id for an answer replay."},
        "canonical_answers": {"type": "object", "description": "Batch-only map of localized free-text or choice custom-response question_key to its canonical English translation. Choice labels derive English from stable option_id and must not be translated."},
        "translated_by": {"type": "string", "description": "Audit label for the coordinator that supplied batch free-text translations."},
        "attempt_id": {"type": "string", "description": "Worker attempt. Supplying it routes the durable question to the coordinator's ordinary-chat pause/resume flow."},
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
        "user_request": {"type": "string", "minLength": 1, "description": "Exact user-authored task text."},
        "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "scope": {"type": "array", "items": {"type": "string"}},
        "allowed_paths": {"type": "array", "items": {"type": "string"}},
        "verification": {"type": "array", "items": {"type": "string"}},
        "budget": {"type": "string"},
        "pause_conditions": {"type": "array", "items": {"type": "string"}},
        "plan_approval": {"type": "string", "enum": ["auto", "required"]},
        "initiative_ref": {"type": "string"},
        "governance_mode": {"type": "string", "enum": ["auto", "required", "off"]},
        "governance": {"type": "object"},
        "risk_triggers": {"type": ["array", "object"]},
        "governance_triggers": {"type": ["array", "object"]},
        "multiple_repositories": {"type": "boolean"},
        "related_tasks": {"type": "boolean"},
        "long_lived_lanes": {"type": "boolean"},
        "conflicting_resources": {"type": "boolean"},
        "multi_session_handoff": {"type": "boolean"},
        "user_language": {"type": "string"},
        "visible_thread_requested": {
            "type": "boolean",
            "default": False,
            "description": "Explicit user authorization for visible task creation; hidden subagents remain the default.",
        },
        "replan_limit": {
            "type": "integer",
            "minimum": 0,
            "description": "Deprecated stable metadata; never a lifetime cap on evidence-backed replans.",
        },
    },
    "required": ["task_id", "user_request", "complexity"],
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
        "context_result_refs": {"type": "array", "items": {"type": "string"}},
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
        "attempt_result_ref": {"type": "string", "minLength": 1},
    },
    "required": ["attempt_id", "host_tool", "host_agent_id", "host_task_name", "host_model", "host_reasoning_effort", "status"],
}
ORCHESTRATE_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "operation": {"type": "string", "enum": sorted(ORCHESTRATE_OPERATIONS)},
        "submission_id": {"type": "string", "description": "Required for every mutating operation; identical retries are replayed exactly."},
        "project_root": {"type": "string", "minLength": 1, "description": "Absolute project workspace. Cortex derives its opaque host-private control state from this path; callers cannot select a ledger directory."},
        "principal": {"type": "string", "minLength": 1},
        "thread_id": {"type": "string"},
        "task_id": {"type": "string"},
        "wave_id": {"type": "string"},
        "task": {**ORCHESTRATE_TASK_SCHEMA, "description": "Start-only immutable task contract."},
        "waves": {"type": "array", "minItems": 1, "items": ORCHESTRATE_WAVE_SCHEMA, "description": "Start-only full ordered execution-wave plan."},
        "host_capabilities": {**ORCHESTRATE_HOST_CAPABILITIES_SCHEMA, "description": "Start-only native model catalogs plus the optional confirmed spawn_agent_default_model."},
        "completions": {"type": "array", "minItems": 1, "items": ORCHESTRATE_COMPLETION_SCHEMA, "description": "Advance-only host completions with canonical attempt_result_ref values created by complete_attempt."},
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
    max_work_packages=MAX_WORK_PACKAGES,
    max_microtasks_per_package=MAX_MICROTASKS_PER_PACKAGE,
    max_discovery_domains=MAX_DISCOVERY_DOMAINS,
    question_option_schema=QUESTION_OPTION_SCHEMA,
)
START_ORCHESTRATION_SCHEMA = PUBLIC_SCHEMA_REGISTRY["start_orchestration"]
CONTINUE_ORCHESTRATION_SCHEMA = PUBLIC_SCHEMA_REGISTRY["continue_orchestration"]
MANAGE_ORCHESTRATION_SCHEMA = PUBLIC_SCHEMA_REGISTRY["manage_orchestration"]
MANAGE_GOVERNANCE_SCHEMA = PUBLIC_SCHEMA_REGISTRY["manage_governance"]
WORKER_QUESTION_SCHEMA = PUBLIC_SCHEMA_REGISTRY["worker_question"]
WORKER_RECORD_ATTEMPT_EVENT_SCHEMA = PUBLIC_SCHEMA_REGISTRY["record_attempt_event"]
WORKER_COMPLETE_ATTEMPT_SCHEMA = PUBLIC_SCHEMA_REGISTRY["complete_attempt"]
READ_DISPATCH_BRIEFING_SCHEMA = PUBLIC_SCHEMA_REGISTRY["read_dispatch_briefing"]
READ_WORKER_RESULT_SCHEMA = PUBLIC_SCHEMA_REGISTRY["read_worker_result"]


TOOLS = {
    "start_orchestration": (start_orchestration, START_ORCHESTRATION_SCHEMA),
    "continue_orchestration": (continue_orchestration, CONTINUE_ORCHESTRATION_SCHEMA),
    "manage_orchestration": (manage_orchestration, MANAGE_ORCHESTRATION_SCHEMA),
    "orchestrate": (orchestrate, ORCHESTRATE_TOOL_SCHEMA),
    "activate_orchestration": (activate_orchestration, {"type": "object", "additionalProperties": False, "properties": {"user_command": {"type": "string", "const": "/cortex"}, "thread_id": {"type": "string", "minLength": 1}, "principal": {"type": "string", "minLength": 1}}, "required": ["user_command", "thread_id", "principal"]}),
    "deactivate_orchestration": (deactivate_orchestration, {"type": "object", "additionalProperties": False, "properties": {"user_command": {"type": "string", "const": "/normal"}, "thread_id": {"type": "string"}, "principal": {"type": "string"}}, "required": ["user_command"]}),
    "get_activation_status": (activation_status, {"type": "object", "properties": {"thread_id": {"type": "string"}, "principal": {"type": "string"}}, "required": []}),
    "classify_task": (classify_task, {"type": "object", "properties": {"complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "requirements": {"type": "array", "items": {"type": "string"}}, "pipeline": {"type": "array", "items": {"type": "string"}, "description": "Full gate proposal selected by the orchestrator; Cortex appends only documentation and close when missing."}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "Ordered executable waves selected by the orchestrator; gates in one wave may run concurrently."}, "thread_id": {"type": "string"}, "principal": {"type": "string"}}, "required": ["complexity"]}),
    "init_task": (init_task, {"type": "object", "properties": {"task_id": {"type": "string"}, "user_request": {"type": "string", "description": "Exact user-authored task text."}, "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "classification_id": {"type": "string"}, "requirements": {"type": "array", "items": {"type": "string"}}, "acceptance_criteria": {"type": "array", "items": {"type": "string"}}, "scope": {"type": "array", "items": {"type": "string"}}, "allowed_paths": {"type": "array", "items": {"type": "string"}}, "verification": {"type": "array", "items": {"type": "string"}}, "budget": {"type": "string"}, "pause_conditions": {"type": "array", "items": {"type": "string"}}, "plan_approval": {"type": "string", "enum": ["auto", "required"]}, "pipeline": {"type": "array", "items": {"type": "string"}}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}, "thread_id": {"type": "string"}, "principal": {"type": "string"}, "user_language": {"type": "string"}, "replan_limit": {"type": "integer", "minimum": 0}}, "required": ["task_id", "user_request", "classification_id"]}),
    "get_task_status": (status, {"type": "object", "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}}, "required": ["task_id", "principal"]}),
    "resolve_dispatch_route": (resolve_dispatch_route, {"type": "object", "additionalProperties": False, "properties": {"agent": {"type": "string", "enum": sorted(AGENTS)}, "task_kind": {"type": "string"}, "risk": {"type": "string", "enum": ["low", "moderate", "high", "critical"]}, "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS)}, "user_requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Exact model explicitly requested by the user; required for non-security Sol."}, "configured_default_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Host-configured agents.default_subagent_model used when native model is omitted."}, "available_models": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}, "description": "Exact model identifiers currently accepted by the native spawn_agent host tool."}, "requested_reasoning_effort": {"type": "string"}}, "required": ["agent", "task_kind", "risk"]}),
    "record_delegation": (record_delegation, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "status_receipt": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "gate": {"type": "string"}, "agent": {"type": "string", "enum": sorted(AGENTS)}, "task_kind": {"type": "string"}, "risk": {"type": "string", "enum": ["low", "moderate", "high", "critical"]}, "requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS)}, "user_requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Exact model explicitly requested by the user; required for non-security Sol."}, "configured_default_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Confirmed host agents.default_subagent_model used when native model is omitted."}, "available_models": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}, "description": "Exact model identifiers currently accepted by the native spawn_agent host tool."}, "dispatch_mode": {"type": "string", "enum": ["hidden_subagent", "visible_thread"], "description": "visible_thread is an explicit user-owned task request and is never an automatic fallback."}, "luna_fallback": {"type": "string", "enum": ["terra"], "description": "Unavailable Luna hidden dispatches fall back to an explicit hidden Terra spawn_agent request."}, "available_thread_models": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}, "description": "Exact model identifiers currently accepted by native create_thread; required only for an explicit visible_thread dispatch."}, "thread_environment": {"type": "string", "enum": ["local", "worktree"], "default": "local", "description": "Workspace for an explicitly requested visible_thread."}, "requested_reasoning_effort": {"type": "string"}, "retry": {"type": "integer"}, "parallel": {"type": "boolean"}, "objective": {"type": "string"}, "ownership": {"type": "string", "minLength": 1}, "context_files": {"type": "array", "items": {"type": "string"}}, "context_result_refs": {"type": "array", "items": {"type": "string"}}, "allowed_paths": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}, "acceptance_criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}, "verification": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}}, "required": ["task_id", "gate", "agent", "task_kind", "risk", "objective", "ownership", "allowed_paths", "acceptance_criteria", "verification"]}),
    "prepare_delegation": (prepare_delegation, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "delegation": {"type": "object"}}, "required": ["task_id", "principal", "delegation"]}),
    "prepare_delegations": (prepare_delegations, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "delegations": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "object"}}}, "required": ["task_id", "principal", "delegations"]}),
    "confirm_host_spawn": (confirm_host_spawn, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "host_tool": {"type": "string", "enum": ["spawn_agent", "create_thread"]}, "host_agent_id": {"type": "string", "minLength": 1, "description": "Native child id; for create_thread pass the returned threadId here."}, "host_task_name": {"type": "string", "minLength": 1}, "host_model": {"type": "string"}, "host_reasoning_effort": {"type": "string"}}, "required": ["task_id", "expected_revision", "attempt_id", "host_agent_id", "host_task_name"]}),
    "finalize_attempt": (finalize_attempt, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "status": {"type": "string", "enum": sorted(TERMINAL_ATTEMPT_STATUSES)}, "reason": {"type": "string"}}, "required": ["task_id", "expected_revision", "attempt_id", "status"]}),
    "cortex.question": (cortex_question, QUESTION_TOOL_SCHEMA),
    "publish_worker_question": (publish_worker_question, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "submission_id": {"type": "string"}, "question": {"type": "string", "minLength": 1}, "header": {"type": "string"}, "options": {"type": "array", "maxItems": 32, "items": QUESTION_OPTION_SCHEMA}, "multiple": {"type": "boolean"}, "custom_label": {"type": "string"}, "context": {}, "blocking": {"type": "boolean"}}, "required": ["task_id", "principal", "attempt_id", "submission_id", "question"]}),
    "list_worker_questions": (list_worker_questions, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "status": {"type": "string", "enum": ["open", "answered"]}}, "required": ["task_id", "principal"]}),
    "answer_worker_question": (answer_worker_question, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "question_id": {"type": "string"}, "submission_id": {"type": "string"}, "answer": {"type": "string", "minLength": 1}, "resume_context": {}}, "required": ["task_id", "principal", "question_id", "submission_id", "answer", "resume_context"]}),
    "get_worker_question_updates": (get_worker_question_updates, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "after_sequence": {"type": "integer", "minimum": 0}}, "required": ["task_id", "principal", "attempt_id"]}),
    "record_evidence": (record_evidence, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "gate": {"type": "string"}, "attempt_id": {"type": "string"}, "result_binding": {"type": "string"}, "kind": {"type": "string"}, "summary": {"type": "string"}, "digest": {"type": "string"}, "command": {"type": "string"}, "exit_code": {"type": "integer"}, "decision": {"type": "string", "enum": ["updated", "not_applicable"]}, "justification": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "governance_obligations": {"type": ["string", "array"], "items": {"type": "string"}}, "governance_scope_ref": {"type": "string"}, "scope_ref": {"type": "string"}, "reviewer_identity": {"type": "string"}, "reviewer_role": {"type": "string"}, "independent_reviewer": {"type": "boolean"}}, "required": ["task_id", "expected_revision", "gate", "summary"]}),
    "execute_verification_command": (execute_verification, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "gate": {"type": "string"}, "attempt_id": {"type": "string"}, "result_binding": {"type": "string"}, "summary": {"type": "string"}, "verification_id": {"type": "string", "enum": sorted(VERIFICATION_COMMANDS)}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120}, "paths": {"type": "array", "items": {"type": "string"}}}, "required": ["task_id", "expected_revision", "gate", "summary", "verification_id"]}),
    "record_gate_outcome": (record_gate, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "gate": {"type": "string"}, "outcome": {"type": "string", "enum": ["passed", "failed", "blocked", "skipped"]}, "summary": {"type": "string"}, "skip_reason": {"type": "string"}, "signals": {"type": "array", "items": {"type": "string"}}, "pipeline_reason": {"type": "string"}, "pipeline_operations": {"type": "array", "items": PIPELINE_OPERATION_SCHEMA}, "allow_rework": {"type": "boolean"}}, "required": ["task_id", "expected_revision", "gate", "outcome"]}),
    "commit_gate": (commit_gate, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "gate": {"type": "string"}, "mode": {"type": "string", "enum": ["verification", "documentation"]}, "attempt_id": {"type": "string"}, "result_binding": {"type": "string"}, "summary": {"type": "string"}, "verification_id": {"type": "string", "enum": sorted(VERIFICATION_COMMANDS)}, "timeout_seconds": {"type": "integer"}, "decision": {"type": "string", "enum": ["updated", "not_applicable"]}, "justification": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "outcome": {"type": "string", "enum": ["passed", "failed", "blocked", "skipped"]}}, "required": ["task_id", "principal", "gate", "summary"]}),
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
    record_attempt_event=record_worker_attempt_event,
    record_attempt_event_schema=WORKER_RECORD_ATTEMPT_EVENT_SCHEMA,
    complete_attempt=complete_worker_attempt,
    complete_attempt_schema=WORKER_COMPLETE_ATTEMPT_SCHEMA,
    read_dispatch_briefing=read_dispatch_briefing,
    read_dispatch_briefing_schema=READ_DISPATCH_BRIEFING_SCHEMA,
    read_worker_result=read_worker_result,
    read_worker_result_schema=READ_WORKER_RESULT_SCHEMA,
    manage_governance=manage_governance,
    manage_governance_schema=MANAGE_GOVERNANCE_SCHEMA,
)


def main() -> None:
    """Keep the executable facade thin; transport lives in cortex_runtime.mcp_api."""
    audience = DEFAULT_MCP_AUDIENCE
    arguments = sys.argv[1:]
    if len(arguments) == 1 and arguments[0].startswith("--mcp-audience="):
        audience = arguments[0].split("=", 1)[1].strip().lower()
    elif arguments:
        raise SystemExit("usage: cortex.py [--mcp-audience=default|coordinator|worker]")
    # The launch environment is host-controlled, unlike JSON-RPC request
    # data.  It supports hosts that cannot add command arguments.  The
    # The default serves the fresh union; hosts that need strict audience
    # separation opt in explicitly.
    if not arguments:
        configured_audience = str(os.environ.get("CORTEX_MCP_AUDIENCE") or "").strip().lower()
        if configured_audience:
            audience = configured_audience
    if audience not in MCP_AUDIENCES:
        raise SystemExit("CORTEX MCP audience must be default, coordinator, or worker")
    # Load the complete runtime package before accepting requests. Installed
    # cache directories may be renamed during plugin replacement while this
    # already-running process still serves a host session.
    import cortex_runtime.orchestration_engine  # noqa: F401

    serve_stdio(
        public_tools=public_tools_for_audience(PUBLIC_TOOLS, audience),
        internal_handlers=TOOLS,
        server_version=SERVER_VERSION,
        instructions=MCP_SERVER_INSTRUCTIONS,
        log_tool_error=log_tool_error,
        audience=audience,
    )


if __name__ == "__main__":
    main()
