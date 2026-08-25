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
from typing import Any, Callable, Iterator, Mapping
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
    _public_next_action,
    build_public_schemas,
    configure_internal_schemas,
    project_public_response,
    public_tools as build_public_tools,
    public_tools_for_audience,
    serve_stdio,
    v11_response as render_v11_response,
)
from cortex_runtime.v11_responses import validate_response as validate_v11_response
from cortex_runtime.ledger_db import (
    DATABASE_SCHEMA_VERSION,
    all_lanes as db_all_lanes,
    artifact_path as db_task_artifact_path,
    create_task as db_create_task,
    delete_classifications as db_delete_classifications,
    delete_global as db_delete_global,
    delete_operations_for_tasks as db_delete_operations_for_tasks,
    delete_task_manifest_snapshots as db_delete_task_manifest_snapshots,
    delete_tasks as db_delete_tasks,
    delete_task_document as db_delete_task_document,
    ensure_database as ensure_ledger_database,
    get_classification as db_get_classification,
    get_global as db_get_global,
    get_lane as db_get_lane,
    get_manifest_snapshot as db_get_manifest_snapshot,
    get_pending_repair_escrow as db_get_pending_repair_escrow,
    get_artifact_for_export_path as db_get_artifact_for_export_path,
    get_artifact_metadata as db_get_artifact_metadata,
    manifest_snapshot_refs as db_manifest_snapshot_refs,
    get_operation as db_get_operation,
    get_task_document as db_get_task_document,
    list_task_documents as db_list_task_documents,
    load_task as db_load_task,
    migration_history as db_migration_history,
    _governance_lifecycle_hmac_key,
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
from cortex_runtime.validation import ValidationFailure
from cortex_runtime.v11_submission import COORDINATOR_REF_PATTERN
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
from cortex_runtime import canonical_json
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback; atomic replace still applies.
    fcntl = None
SCHEMA = "cortex/v11"
RESULT_VALIDATION_SCHEMA = "cortex/result-validation/v1"
PLANNING_SCHEMA = "cortex/planning/v1"
SCOPING_SCHEMA = "cortex/scoping/v1"
PIPELINE_CONTRACT_VERSION = 2
QUESTION_SCHEMA = "cortex/question/v3"
ACTIVATION_COMMAND = "/cortex"
NORMAL_COMMAND = "/normal"
SKILL_ROUTE_HINT = "select `cortex:orchestrator` in the Skills picker or mention `$cortex:orchestrator` in the main chat"
PROFILE_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "profiles.json"
# Desktop inserts this local Markdown link when a user selects the Cortex
# Orchestrator skill. It is host transport metadata, not user task content;
# retaining its absolute plugin-cache path in task labels or durable ledgers
# leaks a machine-local path and makes the label depend on the cache version.
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
    "After resume, clear, or compaction inspect once with task_ref; use context_handoff and never restart. "
    "An MCP isError result may contain the normal structured Cortex error/recovery. Retry the same operation only when "
    "retryable=true, state_mutated=false, and allowed_changes is nonempty; otherwise stop. Never inspect implementation or private state."
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
    != "retry_same_operation_only_when_retryable_state_unmutated_and_allowed_changes_nonempty_otherwise_stop"
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
        "bootstrap_target_bytes": 1024,
        "ordinary_briefing_target_bytes": 12 * 1024,
        "harvest_briefing_target_bytes": 18 * 1024,
        "semantics": "prompt_only_advisory; never a backend admission, storage, truncation, or rejection rule",
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
# These numeric names are retained as prompt/test guidance values only.  They
# are never used to reject, truncate, or omit canonical task content.
MAX_TEXT = 4000
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_PLANNING_BYTES = MAX_JSON_BYTES
MAX_SCOPING_BYTES = MAX_JSON_BYTES
MAX_DISCOVERY_DOMAINS = 8
MAX_BRIEFING_BYTES = 128 * 1024
MAX_WORK_PACKAGES = 32
MAX_MICROTASKS_PER_PACKAGE = 32
MAX_MICROTASKS_PER_PLAN = 128
MAX_TASK_STATE_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
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
MODEL_RESOLUTIONS = {"configured_default", "explicit_override"}
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
        "mode": "prompt_only_advisory",
        "max_entries": None,
        "max_hashed_bytes": None,
        "max_seconds": None,
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
    "coordinatorcapability", "coordinatorrecoveryproof", "coordinatorref", "assignmentref",
}
ASSIGNMENT_VALUE_RE = re.compile(r"assignment-v1-[0-9a-f]{64}")
INTERNAL_NON_ENGLISH_SCRIPT_RE = re.compile(
    r"[\u0370-\u052f\u0530-\u058f\u0590-\u08ff\u0900-\u0fff"
    r"\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def v11_task_slug(value: object) -> str:
    """Build a concise durable-ID label from the exact task text."""
    raw = str(value or "").strip()
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:48] or "task"


def normalize_routing_id(value: Any, field: str = "routing id") -> str:
    raw = str(value or "").strip().lower()
    normalized = re.sub(r"[\s-]+", "_", raw)
    if not normalized or not GATE_RE.fullmatch(normalized):
        raise ValueError(f"{field} must contain only letters, digits, spaces, hyphens, or underscores and start with a letter")
    return normalized


def redact(value: object, limit: int | None = MAX_TEXT) -> str:
    text = str(value or "")
    # Redaction protects secrets; it is never a content-size policy.  Keep
    # complete non-sensitive data in canonical artifacts and records.
    del limit
    text = AUTHORIZATION_RE.sub(lambda match: f"{match.group(1)}<REDACTED>", text)
    text = BEARER_RE.sub(lambda match: f"{match.group(1)}<REDACTED>", text)
    text = URI_CREDENTIAL_RE.sub(r"\1<REDACTED>@", text)
    text = ENV_SECRET_RE.sub(r"\1<REDACTED>", text)
    text = ASSIGNMENT_VALUE_RE.sub("<REDACTED-ASSIGNMENT-REF>", text)
    return SENSITIVE_RE.sub(lambda match: f"{match.group(1)}=<REDACTED>", text)


def normalize_init_text_list(value: object, field: str, *, item_limit: int = 100) -> list[str]:
    """Validate one current canonical init-task text-array field."""
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a current canonical text array")
    del item_limit
    return [item.strip() for item in value]


def normalize_task_requirements(value: object) -> list[str]:
    """Return the only persistable representation of ``task.requirements``.

    No backend size or item count defines the canonical task domain.  Prompt
    guidance may request concise requirements, but durable input stays intact.
    """
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("requirements must be a current canonical text array")
    return [redact(item, limit=None) for item in value]


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
    return "p-" + canonical_json.digest(identity)


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


def _host_control_projects_for_lookup() -> Path | None:
    """Return the trusted host project namespace without a caller root.

    Task-scoped coordinator calls already carry an opaque ``task_ref``.  The
    project ledger is host-owned, so resolving that ref may safely inspect the
    private namespace rather than requiring the coordinator to copy the
    original workspace path into every subsequent request.  This helper never
    creates state and returns ``None`` when the host store has not been used.
    """
    configured = str(os.environ.get(HOST_CONTROL_STORE_ENV) or "").strip()
    base = Path(os.path.normpath(str(Path(configured).expanduser()))) if configured else Path.home().absolute() / ".codex" / "cortex"
    if configured and not base.is_absolute():
        raise ValueError(f"{HOST_CONTROL_STORE_ENV} must be an absolute host-private directory")
    if _lstat_or_none(base) is None:
        return None
    _assert_private_directory(base, "Cortex host control-store root")
    projects = base / "projects"
    if _lstat_or_none(projects) is None:
        return None
    return _assert_private_directory(projects, "Cortex host projects directory")


def _bound_project_root_for_task_ref(task_ref: str, *, include_completed: bool = False) -> Path | None:
    """Resolve one task ref to its immutable project root in host-owned state.

    A ref must resolve to exactly one private project ledger.  Ambiguous or
    malformed matches fail closed; callers must repair the task identity.
    """
    requested = str(task_ref or "").strip()
    if not requested:
        return None
    projects = _host_control_projects_for_lookup()
    if projects is None:
        return None
    matches: list[Path] = []
    for candidate in sorted(projects.iterdir(), key=lambda item: item.name):
        if not candidate.name.startswith("p-"):
            continue
        try:
            _assert_private_directory(candidate, "Cortex host project ledger")
            for task_id in sorted(db_task_index(candidate)):
                if _v11_task_ref(str(task_id)) != requested:
                    continue
                loaded = _v11_task_state(candidate, str(task_id))
                if loaded is None:
                    continue
                _, state, task = loaded
                if not include_completed and state.get("status") not in {"active", "blocked", "needs_input"}:
                    continue
                root_value = str(task.get("project_root") or "").strip()
                if not root_value:
                    continue
                root = select_project_root({"project_root": root_value})
                expected = ledger_root_path({"project_root": str(root)}, create=False)
                if expected != candidate:
                    continue
                matches.append(root)
        except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError):
            continue
    if len(matches) > 1:
        raise ValueError("task_ref resolves to multiple host-private project ledgers; Cortex refuses ambiguous host binding")
    return matches[0] if matches else None


def _bind_task_project_root(params: dict[str, Any], *, include_completed: bool = False) -> dict[str, Any] | None:
    """Return params with a server-derived root, or retain a project-scoped root."""
    if str(params.get("project_root") or "").strip():
        select_project_root(params)
        return params
    task_ref = str(params.get("task_ref") or "").strip()
    if not task_ref:
        return None
    root = _bound_project_root_for_task_ref(task_ref, include_completed=include_completed)
    if root is None:
        return None
    return {**params, "project_root": str(root)}


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

    parts = tuple(Path(path).parts)
    if not parts:
        return False
    name = parts[-1]
    kind = str(entry.get("kind") or "")
    if kind == "directory":
        if name in {str(item) for item in policy.get("ignored_directory_names", [])}:
            return True
        relative = Path(*parts).as_posix()
        for root in policy.get("ignored_relative_roots", []):
            root_text = Path(str(root)).as_posix().strip("/")
            if relative == root_text or relative.startswith(root_text + "/"):
                return True
        prefixes = tuple(str(item) for item in policy.get("virtual_environment_prefixes", []))
        if any(name.startswith(prefix) for prefix in prefixes):
            return True
        # Maven, Gradle, Rust, .NET, C/C++, and similar projects commonly
        # ignore these roots directly, which prevents marker inspection.
        if name in {str(item) for item in policy.get("build_output_directory_names", [])}:
            return True
        return False
    if kind == "file":
        if name.endswith(tuple(str(item) for item in policy.get("ignored_file_suffixes", []))):
            return True
        return _manifest_ephemeral_file_reason(parts, policy) is not None
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
    # A manifest is canonical data: never stop walking because of entry count,
    # cumulative bytes, or elapsed time.
    max_entries = None
    # File-content volume is not a backend admission rule; every non-ignored
    # regular file is hashed and represented in the canonical manifest.
    max_hashed_bytes = None
    max_seconds = None
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
            if stat.S_ISLNK(mode):
                entries[rel] = {"kind": "symlink", "target": os.readlink(path), "mode": stat.S_IMODE(mode)}
            elif is_directory:
                walk(path, parts, rules)
            elif stat.S_ISREG(mode):
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
    encoded = canonical_json.dumps(digest_payload)
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
                "max_hashed_bytes": None,
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
    snapshot_digest = digest_text(canonical_json.dumps({
        "schema": manifest.get("schema"),
        "project_root": manifest.get("project_root"),
        "policy": manifest.get("policy"),
        "entries": manifest.get("entries"),
        "entry_count": manifest.get("entry_count"),
        "manifest_digest": manifest_digest,
    }))
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


def remove_active_mapping(root: Path, task_id: str) -> None:
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


def authorize(state: dict[str, Any], params: dict[str, Any]) -> None:
    authorize_principal(state, params)
    require_activation(params, state.get("task_id"))


def _canonical_principal(value: object) -> str:
    """Normalize the host's two spellings for the main/root coordinator."""
    principal = str(value or "").strip()
    return "root" if principal == "/root" else principal


def authorize_principal(state: dict[str, Any], params: dict[str, Any]) -> None:
    expected = str(state.get("principal") or "local")
    supplied_principal = str(params.get("principal") or "").strip()
    if not supplied_principal:
        raise ValueError("server-owned task principal is required")
    if expected != "local" and _canonical_principal(supplied_principal) != _canonical_principal(expected):
        raise ValueError("task is owned by a different principal")
    if expected != "local" and supplied_principal != expected:
        params["principal"] = expected


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
    # Task artifacts and optional exports remain beneath the same private host
    # store. A worker receives only an exact briefing path or the scoped public
    # artifact fallback; it never receives a browsable
    # workspace-local control directory.
    _ensure_private_directory(path / "tasks", "Cortex host task-artifact directory")
    _ensure_private_directory(path / "lanes", "Cortex host lane directory")
    return path


def activation_key(params: dict[str, Any]) -> str:
    principal = str(params.get("principal") or "").strip()
    if not principal:
        raise ValueError("explicit orchestration activation requires a server-owned principal")
    return redact(principal, 256)


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
    if not principal:
        return {
            "active": False,
            "next_action": "retry activate_orchestration with the server-owned principal",
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
            "principal": redact(principal, 256),
            "coordinator": "main",
            "mode": mode,
            "parent_project_operations": "delegated",
            "worker_visibility": "hidden",
            "worker_return_route": "main_chat",
            "identity_assurance": "server_owned_principal",
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
            "requirements_digest": digest_text(canonical_json.dumps(normalized_requirements)),
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
    """Serialize complete JSON for an atomic write without a byte quota."""
    del label, max_bytes
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


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
        return [sanitize_structured(item) for item in value]
    # Preserve JSON scalar types.  Governance policy snapshots are hashed
    # before they enter init_task; stringifying numeric values here would make
    # the persisted snapshot differ from the server-owned digest.  Unknown
    # Python objects still get the historical string representation so this
    # helper remains safe for incidental host metadata.
    if value is None or isinstance(value, (bool, int)) or (isinstance(value, float) and math.isfinite(value)):
        return value
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
    if arguments:
        input_value, input_source = arguments, "arguments"
    elif params:
        input_value, input_source = params, "params"
    else:
        input_value, input_source = raw_line, "raw_line"
    return {
        "method": redact(request_dict.get("method", ""), 128) or None,
        "tool": redact(params.get("name", ""), 128) or None,
        "request_id": redact(request_id, 256) if request_id is not None else None,
        "ids": ids,
        "input_summary": _tool_error_input_summary(input_value, source=input_source),
    }


def log_tool_error(request: Any, request_id: Any, raw_line: str, error: BaseException) -> None:
    """Append a redacted MCP tool failure without masking the original error."""
    try:
        context = _tool_error_context(request, request_id, raw_line)
        request_params = request.get("params") if isinstance(request, dict) and isinstance(request.get("params"), dict) else {}
        request_arguments = request_params.get("arguments") if isinstance(request_params.get("arguments"), dict) else {}
        supplied_coordinator_ref = str(request_arguments.get("coordinator_ref") or "").strip().lower()
        error_text = redact(str(error), 2000)
        if COORDINATOR_CAPABILITY_RE.fullmatch(supplied_coordinator_ref):
            error_text = error_text.replace(supplied_coordinator_ref, "<REDACTED-COORDINATOR-REF>")
        record = {
            "timestamp": now(),
            "event": "tool_error",
            "server_version": SERVER_VERSION,
            "pid": os.getpid(),
            "error_type": type(error).__name__,
            "error": error_text,
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
    # Prompt prose can recommend concise planning input, but canonical planner
    # and scope artifacts do not enforce a backend content-volume quota.
    del maximum
    text = redact(value, None).strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _planning_string_list(value: Any, label: str, *, maximum: int | None = None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    del maximum
    result = [_planning_text(item, f"{label} item") for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{label} items must be unique")
    return result


def _planning_paths_list(value: Any, label: str) -> list[str]:
    if value is None:
        return ["."]
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty array")
    paths = ["." if str(item).strip() == "." else _safe_project_relative_path(item) for item in value]
    if len(paths) != len(set(paths)):
        raise ValueError(f"{label} paths must be unique")
    return paths


def _validate_planning_dependency_graph(
    nodes: set[str],
    dependencies: dict[str, list[str]],
    label: str,
    *,
    node_paths: Mapping[str, str] | None = None,
) -> None:
    node_paths = node_paths or {}
    diagnostics: list[dict[str, Any]] = []
    for node, items in dependencies.items():
        unknown = sorted(set(items) - nodes)
        if unknown:
            diagnostics.append(_planning_diag(
                f"{label} {node!r} depends on unknown item(s): " + ", ".join(unknown),
                path=node_paths.get(node),
                code="planning_dependency_invalid",
            ))
        if node in items:
            diagnostics.append(_planning_diag(
                f"{label} {node!r} cannot depend on itself",
                path=node_paths.get(node),
                code="planning_dependency_invalid",
            ))
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            diagnostics.append(_planning_diag(
                f"{label} dependencies must be acyclic",
                path=node_paths.get(node),
                code="planning_dependency_cycle",
            ))
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in dependencies.get(node, []):
            if dependency in nodes:
                visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in nodes:
        visit(node)
    if diagnostics:
        # Stable de-duplication prevents a cycle encountered from several
        # roots from producing noisy duplicate diagnostics.
        unique = list({(d.get("code"), d.get("message")): d for d in diagnostics}.values())
        raise PlanningValidationError(unique)


class PlanningValidationError(ValueError):
    """A deterministic, ordered collection of planner input diagnostics.

    Keeping the collection as an exception lets the public facade reject the
    entire request before it opens a ledger write transaction, while still
    exposing every independent correction in one response.
    """

    def __init__(self, diagnostics: list[dict[str, Any]]) -> None:
        self.diagnostics = diagnostics
        super().__init__("; ".join(str(item.get("message") or "") for item in diagnostics))


def _planning_diag(message: str, *, path: str | None = None, code: str = "planning_validation_failed") -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if path:
        item["path"] = path
    return item


_REQUIRED_ARTIFACT_KINDS = {
    "file", "test_suite", "fixture", "cli", "document", "report", "schema", "config", "other",
}


def _normalize_required_artifacts(value: Any, path: str, *, default_gate: str = "implementation") -> list[dict[str, Any]]:
    """Validate and canonicalize the machine-readable deliverable manifest."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{item_path} must be an object")
        allowed = {"path", "kind", "owner_gate", "verification"}
        unknown = sorted(set(item) - allowed)
        missing = sorted(allowed - set(item))
        if unknown or missing:
            details = ([] if not unknown else ["unknown: " + ", ".join(unknown)]) + ([] if not missing else ["missing: " + ", ".join(missing)])
            raise ValueError(f"{item_path} is invalid (" + "; ".join(details) + ")")
        artifact_path = _safe_project_relative_path(item.get("path"))
        kind = str(item.get("kind") or "").strip().lower()
        if not kind:
            raise ValueError(f"{item_path}.kind must be a non-empty string")
        if kind not in _REQUIRED_ARTIFACT_KINDS:
            raise ValueError(
                f"{item_path}.kind must be one of: {', '.join(sorted(_REQUIRED_ARTIFACT_KINDS))}"
            )
        owner_gate = canonical_pipeline_gate(item.get("owner_gate") or default_gate)
        if owner_gate not in AVAILABLE_GATES:
            raise ValueError(f"{item_path}.owner_gate references unknown gate {owner_gate!r}")
        verification = _planning_text(item.get("verification"), f"{item_path}.verification")
        key = (artifact_path, owner_gate)
        if key in seen:
            raise ValueError(f"{item_path} duplicates required artifact {artifact_path!r} for gate {owner_gate!r}")
        seen.add(key)
        result.append({"path": artifact_path, "kind": kind, "owner_gate": owner_gate, "verification": verification})
    return result


def _planning_base_diagnostics(value: Any) -> list[dict[str, Any]]:
    """Collect independent shape errors without attempting cross-field checks."""
    diagnostics: list[dict[str, Any]] = []
    if not isinstance(value, dict):
        return [_planning_diag("planning must be an object", path="planning")]
    allowed = {"overview", "work_packages", "requirement_coverage", "recommendation", "recommendation_rationale", "recommendation_actions", "resolved_questions", "risks"}
    for key in sorted(set(value) - allowed):
        diagnostics.append(_planning_diag("unsupported planning field", path=f"planning.{key}"))
    for key in ("overview", "work_packages"):
        if key not in value:
            diagnostics.append(_planning_diag(f"planning requires {key}", path=f"planning.{key}"))
    for key, label in (("overview", "planning overview"), ("recommendation_rationale", "planning recommendation_rationale")):
        if key in value and not isinstance(value[key], str):
            diagnostics.append(_planning_diag(f"{label} must be a string", path=f"planning.{key}"))
    recommendation = str(value.get("recommendation") or "approve").strip().lower()
    if recommendation not in {"approve", "revise"}:
        diagnostics.append(_planning_diag("planning recommendation must be approve or revise", path="planning.recommendation"))
    for key in ("resolved_questions", "risks", "requirement_coverage"):
        if key in value and not isinstance(value[key], list):
            diagnostics.append(_planning_diag(f"planning {key} must be an array", path=f"planning.{key}"))
    actions = value.get("recommendation_actions")
    if actions is not None and (not isinstance(actions, list) or any(
        not isinstance(item, dict) or not str(item.get("issue") or "").strip()
        or not str(item.get("action") or "").strip()
        or not isinstance(item.get("plan_refs", []), list)
        or not str(item.get("verification") or "").strip()
        for item in actions
    )):
        diagnostics.append(_planning_diag(
            "planning recommendation_actions items require issue, action, plan_refs, and verification",
            path="planning.recommendation_actions",
        ))
    if recommendation == "revise" and not actions:
        diagnostics.append(_planning_diag(
            "planning recommendation_actions is required when recommendation is revise",
            path="planning.recommendation_actions",
        ))
    packages = value.get("work_packages")
    if not isinstance(packages, list):
        return diagnostics
    if not packages:
        diagnostics.append(_planning_diag("planning work_packages must be a non-empty array", path="planning.work_packages"))
        return diagnostics
    package_allowed = {"id", "title", "objective", "allowed_paths", "depends_on", "status", "order", "gates", "required_artifacts", "microtasks"}
    micro_allowed = {"id", "title", "objective", "profile", "allowed_paths", "depends_on", "status", "order", "gates", "acceptance_criteria", "verification", "required_artifacts"}
    for pi, package in enumerate(packages):
        p = f"planning.work_packages[{pi}]"
        if not isinstance(package, dict):
            diagnostics.append(_planning_diag(f"{p} must be an object", path=p)); continue
        for key in sorted(set(package) - package_allowed):
            diagnostics.append(_planning_diag("unsupported planning package field", path=f"{p}.{key}"))
        for key in ("id", "title", "objective", "microtasks"):
            if key not in package:
                diagnostics.append(_planning_diag(f"planning package requires {key}", path=f"{p}.{key}"))
        microtasks = package.get("microtasks")
        if not isinstance(microtasks, list):
            continue
        if not microtasks:
            diagnostics.append(_planning_diag("planning package must contain a non-empty microtasks array", path=f"{p}.microtasks"))
        for mi, micro in enumerate(microtasks):
            m = f"{p}.microtasks[{mi}]"
            if not isinstance(micro, dict):
                diagnostics.append(_planning_diag(f"{m} must be an object", path=m)); continue
            for key in sorted(set(micro) - micro_allowed):
                diagnostics.append(_planning_diag("unsupported planning microtask field", path=f"{m}.{key}"))
            for key in ("id", "title", "objective", "profile", "allowed_paths", "acceptance_criteria", "verification"):
                if key not in micro:
                    diagnostics.append(_planning_diag(f"planning microtask requires {key}", path=f"{m}.{key}"))
            for key in ("acceptance_criteria", "verification", "allowed_paths", "depends_on", "gates", "required_artifacts"):
                if key in micro and not isinstance(micro[key], list):
                    diagnostics.append(_planning_diag(f"planning microtask {key} must be an array", path=f"{m}.{key}"))
        if "required_artifacts" in package and not isinstance(package["required_artifacts"], list):
            diagnostics.append(_planning_diag("planning package required_artifacts must be an array", path=f"{p}.required_artifacts"))
    return diagnostics


def sanitize_scoping_payload(value: Any, *, persisted: bool = False) -> dict[str, Any]:
    """Validate the Planner Scope discovery brief without widening AttemptResult."""
    if persisted and isinstance(value, dict) and value.get("schema") == SCOPING_SCHEMA:
        value = {key: item for key, item in value.items() if key != "schema"}
    if not isinstance(value, dict) or set(value) != {"overview", "context_files", "discovery_domains"}:
        raise ValueError("scoping must contain exactly overview, context_files, and discovery_domains")
    overview = _planning_text(value.get("overview"), "scoping overview")
    raw_context_files = value.get("context_files")
    if not isinstance(raw_context_files, list):
        raise ValueError("scoping context_files must be an array")
    context_files = [_safe_project_relative_path(item) for item in raw_context_files]
    if len(context_files) != len(set(context_files)):
        raise ValueError("scoping context_files must be unique")
    raw_domains = value.get("discovery_domains")
    if (
        not isinstance(raw_domains, list)
        or not raw_domains
    ):
        raise ValueError("scoping discovery_domains must be a non-empty array")
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
    # Run all independent shape checks before semantic parsing.  This keeps a
    # malformed submission atomic and avoids the frustrating one-error-per-
    # retry loop; dependency/reference checks below run only for a structurally
    # valid document.
    base_diagnostics = _planning_base_diagnostics(value)
    if base_diagnostics:
        raise PlanningValidationError(base_diagnostics)
    allowed_top_level = {
        "overview", "work_packages", "requirement_coverage", "recommendation",
        "recommendation_rationale", "recommendation_actions", "resolved_questions", "risks",
    }
    if not isinstance(value, dict) or not {"overview", "work_packages"}.issubset(value) or set(value) - allowed_top_level:
        raise ValueError("planning must contain overview and work_packages, with only documented traceability fields")
    overview = _planning_text(value.get("overview"), "planning overview")
    recommendation = str(value.get("recommendation") or "approve").strip().lower()
    if recommendation not in {"approve", "revise"}:
        raise ValueError("planning recommendation must be approve or revise")
    recommendation_rationale = str(value.get("recommendation_rationale") or "").strip()
    recommendation_actions: list[dict[str, Any]] = []
    for index, item in enumerate(value.get("recommendation_actions", []), 1):
        if not isinstance(item, dict):
            raise ValueError(f"planning recommendation_actions[{index - 1}] must be an object")
        issue = _planning_text(item.get("issue"), f"planning.recommendation_actions[{index - 1}].issue")
        action = _planning_text(item.get("action"), f"planning.recommendation_actions[{index - 1}].action")
        verification = _planning_text(item.get("verification"), f"planning.recommendation_actions[{index - 1}].verification")
        refs = item.get("plan_refs", [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise ValueError(f"planning recommendation_actions[{index - 1}].plan_refs must be an array of non-empty strings")
        recommendation_actions.append({"issue": issue, "action": action, "plan_refs": list(dict.fromkeys(ref.strip() for ref in refs)), "verification": verification})
    if recommendation == "revise" and not recommendation_actions:
        raise ValueError("planning recommendation_actions is required when recommendation is revise")
    # Recommendation prose is canonical planning data. Prompt compactness is
    # advisory only; do not reject a valid rationale because of its byte size.
    raw_resolved_questions = value.get("resolved_questions", [])
    if not isinstance(raw_resolved_questions, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_resolved_questions
    ):
        raise ValueError("planning resolved_questions must be an array of non-empty strings")
    resolved_questions = list(dict.fromkeys(str(item).strip() for item in raw_resolved_questions))
    raw_risks = value.get("risks", [])
    if not isinstance(raw_risks, list) or any(not isinstance(item, str) or not item.strip() for item in raw_risks):
        raise ValueError("planning risks must be an array of non-empty strings")
    risks = list(dict.fromkeys(str(item).strip() for item in raw_risks))
    # Keep the structural list/cardinality contract, but preserve each risk
    # string losslessly regardless of its rendered byte size.
    raw_coverage = value.get("requirement_coverage", [])
    if not isinstance(raw_coverage, list):
        raise ValueError("planning requirement_coverage must be an array")
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
    if not isinstance(raw_packages, list) or not raw_packages:
        raise ValueError("planning work_packages must be a non-empty array")
    packages: list[dict[str, Any]] = []
    package_ids: set[str] = set()
    microtask_ids: set[str] = set()
    microtask_dependencies: dict[str, list[str]] = {}
    package_dependency_paths: dict[str, str] = {}
    microtask_dependency_paths: dict[str, str] = {}
    planning_diagnostics: list[dict[str, Any]] = []
    total_microtasks = 0
    for index, raw_package in enumerate(raw_packages, 1):
        if not isinstance(raw_package, dict):
            raise ValueError(f"planning work_packages[{index - 1}] must be an object")
        unknown = sorted(set(raw_package) - {"id", "title", "objective", "allowed_paths", "depends_on", "status", "order", "gates", "required_artifacts", "microtasks"})
        missing = sorted({"id", "title", "objective", "microtasks"} - set(raw_package))
        if unknown or missing:
            details = ([] if not unknown else ["unknown: " + ", ".join(unknown)]) + ([] if not missing else ["missing: " + ", ".join(missing)])
            raise ValueError(f"planning work_packages[{index - 1}] is invalid (" + "; ".join(details) + ")")
        package_id = _planning_identifier(raw_package.get("id"), "planning package id")
        if package_id in package_ids:
            raise ValueError("planning package ids must be unique")
        package_ids.add(package_id)
        raw_microtasks = raw_package.get("microtasks")
        if not isinstance(raw_microtasks, list) or not raw_microtasks:
            raise ValueError(f"planning package {package_id!r} must contain a non-empty microtasks array")
        microtasks: list[dict[str, Any]] = []
        for micro_index, raw_microtask in enumerate(raw_microtasks, 1):
            if not isinstance(raw_microtask, dict):
                raise ValueError(f"planning package {package_id!r} microtask {micro_index} must be an object")
            allowed = {"id", "title", "objective", "profile", "allowed_paths", "depends_on", "status", "order", "gates", "acceptance_criteria", "verification", "required_artifacts"}
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
            microtask_dependency_paths[microtask_id] = (
                f"planning.work_packages[{index - 1}].microtasks[{micro_index - 1}].depends_on"
            )
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
            try:
                microtask_artifacts = _normalize_required_artifacts(
                    raw_microtask.get("required_artifacts"),
                    f"planning.work_packages[{index - 1}].microtasks[{micro_index - 1}].required_artifacts",
                    default_gate=(gates[0] if gates else "implementation"),
                )
            except ValueError as exc:
                planning_diagnostics.append(_planning_diag(
                    str(exc),
                    path=f"planning.work_packages[{index - 1}].microtasks[{micro_index - 1}].required_artifacts",
                    code="planning_artifacts_invalid",
                ))
                microtask_artifacts = []
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
                "required_artifacts": microtask_artifacts,
            })
        total_microtasks += len(microtasks)
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
        package_dependency_paths[package_id] = f"planning.work_packages[{index - 1}].depends_on"
        try:
            package_gates = _planning_package_gates(raw_package_gates, microtasks, package_id)
        except ValueError as exc:
            # Keep validating independent coverage/reference fields in the
            # same request.  The package is never materialized while this
            # collection is non-empty; the derived microtask gates are only a
            # temporary validation projection so later diagnostics can be
            # reported without turning an invalid package into an executable
            # gate.
            planning_diagnostics.append(_planning_diag(
                str(exc),
                path=f"planning.work_packages[{index - 1}].gates",
                code="planning_gates_invalid",
            ))
            package_gates = sorted({gate for microtask in microtasks for gate in microtask.get("gates", [])}) or ["implementation"]
        try:
            package_artifacts = _normalize_required_artifacts(
                raw_package.get("required_artifacts"),
                f"planning.work_packages[{index - 1}].required_artifacts",
                default_gate=(package_gates[0] if package_gates else "implementation"),
            )
        except ValueError as exc:
            planning_diagnostics.append(_planning_diag(
                str(exc),
                path=f"planning.work_packages[{index - 1}].required_artifacts",
                code="planning_artifacts_invalid",
            ))
            package_artifacts = []
        packages.append({
            "id": package_id,
            "title": _planning_text(raw_package.get("title"), "planning package title"),
            "objective": _planning_text(raw_package.get("objective"), "planning package objective"),
            "status": package_status,
            "order": package_order,
            "gates": package_gates,
            "allowed_paths": _planning_paths_list(raw_package.get("allowed_paths"), "planning package allowed_paths"),
            "depends_on": dependencies,
            "required_artifacts": package_artifacts,
            "microtasks": microtasks,
        })
    _validate_planning_dependency_graph(
        package_ids,
        {item["id"]: item["depends_on"] for item in packages},
        "planning package",
        node_paths=package_dependency_paths,
    )
    _validate_planning_dependency_graph(
        microtask_ids,
        microtask_dependencies,
        "planning microtask",
        node_paths=microtask_dependency_paths,
    )
    valid_plan_refs = package_ids | microtask_ids
    coverage_diagnostics: list[dict[str, Any]] = []
    for action_index, action in enumerate(recommendation_actions, 1):
        unknown_refs = sorted(set(action["plan_refs"]) - valid_plan_refs)
        if unknown_refs:
            coverage_diagnostics.append(_planning_diag(
                f"planning recommendation_actions[{action_index - 1}] references unknown plan items: "
                + ", ".join(unknown_refs),
                path=f"planning.recommendation_actions[{action_index - 1}].plan_refs",
                code="planning_recommendation_action_invalid",
            ))
    for coverage_index, item in enumerate(coverage, 1):
        unknown_refs = sorted(set(item["plan_refs"]) - valid_plan_refs)
        if unknown_refs:
            coverage_diagnostics.append(_planning_diag(
                f"planning requirement_coverage[{coverage_index - 1}] references unknown plan items: "
                + ", ".join(unknown_refs),
                path=f"planning.requirement_coverage[{coverage_index - 1}].plan_refs",
                code="planning_coverage_invalid",
            ))
    if planning_diagnostics or coverage_diagnostics:
        raise PlanningValidationError(planning_diagnostics + coverage_diagnostics)
    result = {
        "schema": PLANNING_SCHEMA,
        "overview": overview,
        "work_packages": packages,
        "requirement_coverage": coverage,
        "recommendation": recommendation,
        "recommendation_rationale": recommendation_rationale,
        "recommendation_actions": recommendation_actions,
        "resolved_questions": resolved_questions,
        "risks": risks,
    }
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
            for artifact in microtask.get("required_artifacts") or []:
                lines.append(f"  - deliverable `{artifact['path']}` ({artifact['kind']}, {artifact['owner_gate']}): {artifact['verification']}")
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
            "required_artifacts": list(package.get("required_artifacts") or []),
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
                "required_artifacts": list(microtask.get("required_artifacts") or []),
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
        "recommendation_actions": list(manifest.get("recommendation_actions") or []),
        "requirement_coverage": list(manifest.get("requirement_coverage") or []),
        "resolved_questions": list(manifest.get("resolved_questions") or []),
        "risks": list(manifest.get("risks") or []),
        "items": rows,
        "updated_at": now(),
        "last_event": "plan_created",
    }


def planning_payload_digest(value: Any) -> str:
    """Return the stable digest used by same-attempt planning repair."""
    return "sha256:" + canonical_json.digest(value)


def planning_rejected_draft_document(
    task_dir: Path,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
    planning: Any,
    diagnostics: list[dict[str, Any]],
    result_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one immutable rejected planner draft, without a result row."""
    task_id = str(state.get("task_id") or "")
    root = _task_document_root(task_dir, task_id)
    digest = planning_payload_digest(planning)
    document_type = f"planning_rejected_draft:{safe_id(str(attempt.get('attempt_id') or ''))}"
    existing = db_get_task_document(root, task_id, document_type)
    if isinstance(existing, dict):
        if existing.get("base_payload_digest") != digest:
            raise ValueError("planning rejected draft is immutable; base_payload_digest differs")
        return existing
    document = {
        "schema": "cortex/planning-rejected-draft/v1",
        "task_id": task_id,
        "attempt_id": str(attempt.get("attempt_id") or ""),
        "base_payload_digest": digest,
        "planning": json.loads(json.dumps(planning, ensure_ascii=False)),
        "diagnostics": json.loads(json.dumps(diagnostics, ensure_ascii=False)),
        "result_payload": json.loads(json.dumps(dict(result_payload or {}), ensure_ascii=False)),
        "created_at": now(),
    }
    db_put_task_document(root, task_id, document_type, document)
    return document


def get_planning_rejected_draft(task_dir: Path, task_id: str, attempt_id: str) -> dict[str, Any] | None:
    root = _task_document_root(task_dir, task_id)
    return db_get_task_document(root, task_id, f"planning_rejected_draft:{safe_id(attempt_id)}")


def _planning_pointer_tokens(path: str) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("planning patch path must be an RFC6901 JSON pointer")
    return [item.replace("~1", "/").replace("~0", "~") for item in path[1:].split("/")]


def apply_planning_repair(draft: Mapping[str, Any], patches: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Apply restricted RFC6902-like patches to a rejected planning draft."""
    value = json.loads(json.dumps(draft.get("planning"), ensure_ascii=False))
    allowed_roots = {"overview", "work_packages", "requirement_coverage", "recommendation", "recommendation_rationale", "recommendation_actions", "resolved_questions", "risks"}
    for patch in patches:
        if not isinstance(patch, Mapping) or patch.get("op") not in {"replace", "add", "remove"}:
            raise ValueError("planning patches support only replace, add, and remove")
        tokens = _planning_pointer_tokens(str(patch.get("path") or ""))
        if not tokens or tokens[0] not in allowed_roots:
            raise ValueError("planning_correction_scope_violation: patch path is outside planning fields")
        cursor: Any = value
        for token in tokens[:-1]:
            if isinstance(cursor, list):
                if token == "-" or not token.isdigit() or int(token) >= len(cursor):
                    raise ValueError("planning patch path does not exist")
                cursor = cursor[int(token)]
            elif isinstance(cursor, dict) and token in cursor:
                cursor = cursor[token]
            else:
                raise ValueError("planning patch path does not exist")
        leaf = tokens[-1]
        if isinstance(cursor, list):
            if patch.get("op") == "add" and leaf == "-": cursor.append(patch.get("value"))
            elif leaf.isdigit() and int(leaf) < len(cursor):
                if patch.get("op") == "remove": cursor.pop(int(leaf))
                else: cursor[int(leaf)] = patch.get("value")
            else: raise ValueError("planning patch list index does not exist")
        elif isinstance(cursor, dict):
            if patch.get("op") == "remove":
                if leaf not in cursor: raise ValueError("planning patch field does not exist")
                del cursor[leaf]
            elif patch.get("op") == "replace" and leaf not in cursor:
                raise ValueError("planning patch field does not exist")
            else: cursor[leaf] = patch.get("value")
        else: raise ValueError("planning patch parent is not an object or array")
    return value


def planning_changed_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    """Return deterministic JSON-pointer paths changed between two drafts."""
    if type(before) is not type(after):
        return [prefix or "/"]
    if isinstance(before, dict):
        paths: list[str] = []
        for key in sorted(set(before) | set(after)):
            child = (prefix + "/" + str(key).replace("~", "~0").replace("/", "~1"))
            if key not in before or key not in after:
                paths.append(child)
            else:
                paths.extend(planning_changed_paths(before[key], after[key], child))
        return paths
    if isinstance(before, list):
        paths: list[str] = []
        for index in range(max(len(before), len(after))):
            child = f"{prefix}/{index}"
            if index >= len(before) or index >= len(after): paths.append(child)
            else: paths.extend(planning_changed_paths(before[index], after[index], child))
        return paths
    return [] if before == after else [prefix or "/"]


def planning_diagnostic_pointer(raw: str) -> str:
    """Convert a planner diagnostic path to the canonical RFC6901 pointer."""
    raw = str(raw or "").strip()
    if raw.startswith("/"):
        # Public planning repairs operate on the planning sibling itself, so
        # tolerate a request-level ``/planning/...`` pointer but normalize it
        # to the draft root before it reaches apply_planning_repair.
        if raw == "/planning":
            return "/"
        if raw.startswith("/planning/"):
            return raw[len("/planning"):]
        return raw
    if raw.startswith("$."):
        raw = raw[2:]
    elif raw == "$":
        raw = "planning"
    if raw == "planning":
        return "/"
    if raw.startswith("planning."):
        raw = raw[9:]
    parts: list[str] = []
    for segment in raw.split("."):
        match = re.fullmatch(r"([^\[]+)((?:\[\d+\])*)", segment)
        if not match:
            parts.append(segment)
            continue
        parts.append(match.group(1))
        indices = re.findall(r"\[(\d+)\]", match.group(2))
        parts.extend(indices)
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts if part)


def planning_diagnostic_patch_paths(diagnostics: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return stable JSON Pointer paths for all path-addressable diagnostics."""
    paths: list[str] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, Mapping):
            continue
        # ``patch_path`` is the pointer inside the server-owned planning draft
        # (which omits the outer ``planning`` envelope).  It is used for
        # misplaced top-level fields; canonical_path remains the public move
        # target and canonical_json_pointer remains the request-level pointer.
        raw = diagnostic.get("patch_path") or diagnostic.get("path")
        if diagnostic.get("patch_path"):
            pointer = planning_diagnostic_pointer(str(diagnostic["patch_path"]))
        else:
            raw = diagnostic.get("canonical_path") or raw
            pointer = planning_diagnostic_pointer(str(raw)) if raw else ""
        if not pointer:
            continue
        if pointer not in paths:
            paths.append(pointer)
    return paths


def planning_diagnostic_scope_allows(diagnostics: list[Mapping[str, Any]], changed: list[str]) -> bool:
    scopes = planning_diagnostic_patch_paths(diagnostics)

    return bool(scopes) and all(any(scope == "/" or path == scope or path.startswith(scope + "/") for scope in scopes) for path in changed)


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
        if isinstance(value, dict) and value.get("status") == "planner_recovery_pending"
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
        "recommendation_actions": list(planning.get("recommendation_actions") or []),
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
                content=canonical_json.dumps(package_record),
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


def required_artifact_diagnostics(
    project_root: Path,
    task_dir: Path,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Check declared deliverables before implementation/QA completion.

    Planning artifacts are immutable and therefore the manifest is loaded from
    the task ledger, while existence is checked only in the assigned project
    workspace.  An undeclared plan remains valid; once a deliverable is
    declared, a completed gate cannot silently claim success before it exists.
    """
    gate = canonical_pipeline_gate(attempt.get("gate") or "")
    if gate not in {"implementation", "qa"}:
        return []
    try:
        manifest = current_planning_manifest(task_dir)
    except (ValueError, OSError):
        # A direct facade unit harness may supply only the project root.  The
        # artifact gate is additive and must not turn that bounded test seam
        # into a synthetic task-corruption failure; real task worktrees always
        # have an immutable Cortex ledger and continue through the checks below.
        return []
    if not isinstance(manifest, dict):
        return []
    required: list[dict[str, Any]] = []
    for summary in manifest.get("work_packages") or []:
        if not isinstance(summary, dict) or not summary.get("artifact_path"):
            continue
        try:
            record, _ = read_immutable_json_artifact(
                task_dir, str(state.get("task_id") or ""), str(summary["artifact_path"]), kinds={"planning_revision"},
            )
        except (ValueError, OSError):
            continue
        package = record.get("package") if isinstance(record, dict) else None
        if not isinstance(package, dict):
            continue
        required.extend(item for item in package.get("required_artifacts") or [] if isinstance(item, dict))
        for microtask in package.get("microtasks") or []:
            if isinstance(microtask, dict):
                required.extend(item for item in microtask.get("required_artifacts") or [] if isinstance(item, dict))
    diagnostics: list[dict[str, Any]] = []
    root = Path(project_root).resolve()
    for index, artifact in enumerate(required):
        owner_gate = canonical_pipeline_gate(artifact.get("owner_gate") or gate)
        if owner_gate != gate:
            continue
        relative = str(artifact.get("path") or "").replace("\\", "/")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            diagnostics.append({
                "code": "required_artifact_invalid_path",
                "phase": gate,
                "path": f"$.required_artifacts[{index}].path",
                "json_pointer": f"/required_artifacts/{index}/path",
                "message": f"required artifact path escapes project workspace: {relative}",
                "received": relative,
                "expected": {"type": "string", "format": "project-relative-path"},
                "field_schema": {"type": "string", "minLength": 1, "format": "project-relative-path"},
                "fix": "Change the declared artifact path to a project-relative path and retry the same gate.",
            })
            continue
        if not candidate.exists():
            diagnostics.append({
                "code": "required_artifact_missing",
                "phase": gate,
                "path": f"$.required_artifacts[{index}]",
                "json_pointer": f"/required_artifacts/{index}",
                "message": f"required artifact is missing: {relative}",
                "received": {"path": relative, "kind": artifact.get("kind"), "owner_gate": owner_gate},
                "expected": {
                    "path": relative,
                    "kind": artifact.get("kind"),
                    "owner_gate": owner_gate,
                    "verification": artifact.get("verification"),
                    "exists": True,
                },
                "field_schema": {
                    "type": "object", "required": ["path", "kind", "owner_gate", "verification"],
                    "properties": {
                        "path": {"type": "string", "format": "project-relative-path"},
                        "kind": {"type": "string"}, "owner_gate": {"type": "string"},
                        "verification": {"type": "string"},
                    },
                },
                "fix": f"Create or restore {relative}, run {artifact.get('verification')}, then retry the same {gate} attempt.",
            })
    return diagnostics


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
    if not isinstance(value, list):
        raise ValueError("question options must be a list")
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
        "question_type": "multi_select" if multiple else ("single_select" if options else "text"),
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
    digest = digest_text(canonical_json.dumps({"question": sanitized_question, "context": context, "blocking": blocking, "config": config}))
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
        decision["decision_digest"] = digest_text(canonical_json.dumps(
            {key: decision[key] for key in ("source_type", "source_ref", "question_key", "question_en", "answer_en", "answer_option_ids")},
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
            decision["decision_digest"] = digest_text(canonical_json.dumps(
                {key: decision[key] for key in ("source_type", "source_ref", "question_key", "question_en", "answer_en", "answer_option_ids")},
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
    return merged, indexes



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
        if not isinstance(value, list):
            raise ValueError(f"task.{field} must be an array of non-empty strings")
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


def _terminal_failure_evidence_expired(value: object) -> bool:
    """Return whether one private control record has an aware past expiry."""
    if not isinstance(value, Mapping):
        return False
    try:
        expires_at = datetime.fromisoformat(str(value.get("expires_at") or ""))
    except (TypeError, ValueError):
        return False
    return bool(
        expires_at.tzinfo is not None
        and expires_at <= datetime.now(timezone.utc)
    )


def save_state(task_dir: Path, state_path: Path, state: dict[str, Any], event: str, detail: str) -> dict[str, Any]:
    state["revision"] += 1
    state["updated_at"] = now()
    root = _ledger_root_for_artifact(task_dir)
    db_update_task_state(root, state, event=event, detail=detail)
    # Private terminal-failure evidence is valid only while its exact attempt
    # generation remains the current nonterminal assignment.  Any ordinary
    # task terminalization, attempt completion, invalidation, or reissue
    # removes it as audit/control cleanup; it never becomes domain history.
    evidence_key = globals().get("TERMINAL_FAILURE_EVIDENCE_KEY")
    evidence_schema = globals().get("TERMINAL_FAILURE_EVIDENCE_SCHEMA")
    if isinstance(evidence_key, str) and isinstance(evidence_schema, str):
        evidence = db_get_task_document(root, str(state.get("task_id") or ""), evidence_key)
        if isinstance(evidence, dict) and evidence.get("schema") == evidence_schema:
            attempt_id = str(evidence.get("attempt_id") or "")
            matches = [
                item for item in state.get("attempts") or []
                if isinstance(item, dict) and str(item.get("attempt_id") or "") == attempt_id
            ]
            current = matches[0] if len(matches) == 1 else None
            claim = (
                current.get("worker_assignment")
                if isinstance(current, dict) and isinstance(current.get("worker_assignment"), dict)
                else {}
            )
            remains_current = bool(
                isinstance(current, dict)
                and not _terminal_failure_evidence_expired(evidence)
                and state.get("status") == "active"
                and current.get("status") in {AWAITING_HOST_SPAWN, "running", "waiting_question"}
                and not current.get("invalidated")
                and str(current.get("dispatch_ref") or "") == str(evidence.get("dispatch_ref") or "")
                and int(claim.get("generation") or 0) == int(evidence.get("assignment_generation") or 0)
            )
            if not remains_current:
                db_delete_task_document(root, str(state.get("task_id") or ""), evidence_key)
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
        if isinstance(pause, dict) and pause.get("status") == "planner_recovery_pending"
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
        and bool(
            approval.get("user_requested")
            or state.get("plan_approval_user_requested")
            or state.get("user_requested_plan_approval")
        )
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
    """Return a gate only when it is already in the canonical wire format."""
    return gate if isinstance(gate, str) else str(gate)


_PLANNING_GATE_PROSE_MARKERS = (
    "missing", "timed_out", "inaccessible", "ambiguous", "production",
    "release_blocker", "autonomous", "no_production_mutation", "restart",
    "deployment", "order", "enable", "release", "warmup",
)

# These are deliberately a closed vocabulary.  A planner sentence such as
# ``enable release warmup`` may be tokenized into the optional package-gates
# field, but an arbitrary unknown identifier must continue to fail closed.
_PLANNING_PACKAGE_PROSE_GATE_TOKENS = frozenset({
    *_PLANNING_GATE_PROSE_MARKERS,
    "a_missing", "production_fact", "autonomous_averaging",
})


def _planning_package_gates(
    raw_gates: Any,
    microtasks: list[dict[str, Any]],
    package_id: str,
) -> list[str]:
    """Validate package gates without treating a planner prose leak as IDs.

    Package gates are a projection of the executable microtask gates.  Some
    model responses have put a comma-separated release-policy sentence in
    this optional field, tokenized as underscore IDs.  That sentence must not
    become executable gates, but genuine unknown gate IDs still fail closed.
    """
    if not isinstance(raw_gates, list) or not raw_gates or any(not str(item).strip() for item in raw_gates):
        raise ValueError(f"planning package {package_id!r} gates must be a non-empty array")
    package_gates = list(dict.fromkeys(canonical_pipeline_gate(item) for item in raw_gates))
    unknown = sorted(set(package_gates) - AVAILABLE_GATES)
    if unknown:
        joined = "_".join(unknown)
        marker_hits = sum(marker in joined for marker in _PLANNING_GATE_PROSE_MARKERS)
        if (
            len(unknown) >= 2
            and marker_hits >= 1
            and set(unknown).issubset(_PLANNING_PACKAGE_PROSE_GATE_TOKENS)
        ):
            derived = sorted({gate for microtask in microtasks for gate in microtask.get("gates", [])})
            return derived or ["implementation"]
        raise ValueError(
            f"planning package {package_id!r} references unknown gates: "
            + ", ".join(unknown)
        )
    return package_gates


def canonical_profile(profile: Any) -> str:
    """Return a profile only when it is already in the canonical wire format."""
    return profile if isinstance(profile, str) else str(profile)


def normalize_pipeline(pipeline: list[Any]) -> list[str]:
    result = [canonical_pipeline_gate(gate) for gate in pipeline]
    if not result or len(result) != len(set(result)) or any(not GATE_RE.fullmatch(gate) for gate in result):
        raise ValueError("pipeline gates must be unique lowercase ids matching [a-z][a-z0-9_-]{0,63}")
    return result


def canonicalize_full_governance_pipeline(
    state: dict[str, Any],
    pipeline: list[Any],
) -> list[str]:
    """Normalize the selected route without changing its executable choice.

    Full governance can recommend an activation/close envelope, but it is an
    advisory assessment and not an authorization boundary.  This helper is
    called by pipeline mutation paths, so returning a reordered route here
    would silently replace the orchestrator's decision.  Record the canonical
    recommendation for audit and always return the normalized input route.
    """
    current = normalize_pipeline(pipeline)
    governance = state.get("governance") if isinstance(state.get("governance"), dict) else {}
    if governance.get("effective_mode") != "full":
        return current
    ordinary = [
        gate for gate in current
        if gate not in {*GOVERNANCE_FULL_GATES, "close"}
    ]
    recommended = ["governance_activation", *ordinary, "governance_close", "close"]
    if current != recommended:
        advice = {
            "code": "governance_pipeline_recommendation",
            "severity": "warning",
            "message": "Full governance recommends activation and close boundaries; the orchestrator-selected pipeline remains authoritative.",
            "recommended_pipeline": recommended,
            "chosen_pipeline_unchanged": True,
        }
        existing = state.setdefault("pipeline_advice", [])
        if isinstance(existing, list) and advice not in existing:
            existing.append(advice)
    return current


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
    # Preserve the chosen parallel grouping.  A full-governance assessment
    # may recommend singleton review boundaries, but it must not rewrite the
    # orchestrator's executable choice.
    return normalized


def validate_pipeline_invariants(state: dict[str, Any], pipeline: list[str] | None = None) -> None:
    candidate = pipeline or state["current_pipeline"]
    governance = state.get("governance") if isinstance(state.get("governance"), dict) else {}
    advice: list[dict[str, Any]] = []
    if governance.get("effective_mode") == "full":
        if set(GOVERNANCE_FULL_GATES) - set(candidate) or "close" not in candidate:
            advice.append({
                "code": "governance_envelope_recommended",
                "severity": "warning",
                "message": "Full governance activation and close phases are recommended, but the orchestrator selected a different executable route.",
            })
        elif candidate[0] != "governance_activation" or candidate[-2:] != ["governance_close", "close"]:
            advice.append({
                "code": "governance_order_recommended",
                "severity": "warning",
                "message": "Full governance review boundaries are recommended in canonical order; the orchestrator choice remains executable.",
            })
    if state.get("require_handoff"):
        missing = {"documentation", "close"} - set(candidate)
        if missing or ("documentation" in candidate and "close" in candidate and candidate.index("documentation") > candidate.index("close")):
            advice.append({
                "code": "documentation_close_recommended",
                "severity": "warning",
                "message": "Documentation before close is recommended for this governance level; the selected pipeline remains executable.",
            })
    if advice:
        existing = state.setdefault("pipeline_advice", [])
        if isinstance(existing, list):
            for item in advice:
                if item not in existing:
                    existing.append(item)
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
    # Recovery/replay is defined only for the current canonical ledger state.
    # A facade projection marked ``passed`` is not state that can be
    # reconciled: it is unsupported unless its exact AttemptResult is already
    # finalized. Reject the projection before any completion processing rather
    # than interpreting an old/malformed row as a successful attempt. Non-
    # success terminal attempts legitimately have no successful AttemptResult,
    # so this guard remains scoped to successful facade rows.
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
    completion_advice = state.setdefault("completion_advice", [])

    def advise(code: str, message: str, recommended_next: str) -> None:
        item = {
            "code": code,
            "severity": "warning",
            "message": message,
            "recommended_next": recommended_next,
        }
        if isinstance(completion_advice, list) and item not in completion_advice:
            completion_advice.append(item)
    attempts = [item for item in state.get("attempts", []) if not item.get("invalidated")]
    non_terminal = [item["attempt_id"] for item in attempts if item.get("status") not in TERMINAL_ATTEMPT_STATUSES]
    if non_terminal:
        advise(
            "completion_attempts_still_running",
            "Completion encountered non-terminal worker attempts: " + ", ".join(non_terminal),
            "continue_selected_pipeline",
        )
        # This is a lifecycle projection, not evidence corruption. Keep the
        # task executable so the orchestration engine can consume the worker
        # result or dispatch the bounded corrective retry. Integrity checks
        # below still fail closed for mismatched/corrupt canonical results.
        if state.get("status") in {"completed", "blocked", "needs_input"}:
            state["status"] = "active"
            state.pop("blocked_reason", None)
    evidence = [item for item in state.get("evidence", []) if not item.get("invalidated")]
    missing_evidence = [
        item["attempt_id"]
        for item in attempts
        if item.get("status") == "passed"
        if not any(record.get("attempt_id") == item["attempt_id"] for record in evidence)
    ]
    if missing_evidence:
        advise(
            "completion_evidence_recommended",
            "Governed completion is missing evidence for: " + ", ".join(missing_evidence),
            "record_evidence",
        )
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
        advise(
            "canonical_result_evidence_recommended",
            "Governed completion evidence is not bound to canonical results for: " + ", ".join(missing_results),
            "record_evidence",
        )
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
            advise(
                "closure_attempt_unresolved",
                "A closure result still reports unresolved work: " + ", ".join(unresolved_closure_attempts),
                "dispatch_corrective_worker",
            )
    if state.get("require_handoff"):
        if "documentation" not in state.get("completed_gates", []) or not state.get("documentation_receipt"):
            advise(
                "documentation_decision_recommended",
                "Documentation decision evidence is recommended before completion.",
                "record_evidence",
            )
        if "close" not in state.get("completed_gates", []):
            advise(
                "close_gate_recommended",
                "The close gate is recommended before completion.",
                "record_gate",
            )
        if not state.get("reassessment_receipts"):
            advise(
                "reassessment_recommended",
                "A reassessment receipt is recommended before completion.",
                "record_reassessment",
            )
        if not state.get("handoff_created") or state.get("handoff_gate") != "close":
            advise(
                "final_handoff_recommended",
                "A final close handoff is recommended before completion.",
                "record_handoff",
            )
    if governance_full:
        completed = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
        missing_governance = sorted(set(GOVERNANCE_FULL_GATES) - completed)
        if missing_governance:
            advise(
                "governance_review_recommended",
                "Full governance review phases were not run: " + ", ".join(missing_governance),
                "record_gate",
            )
    # Full and light governance obligations are enforced from actual evidence
    # receipts, not merely from the resolver's metadata list.
    if governance_full:
        try:
            validate_governance_obligation_evidence(
                state, "governance_close", evidence, artifact_root=artifact_root
            )
        except ValueError as exc:
            if "requires typed governance obligation evidence" in str(exc):
                advise("governance_evidence_recommended", str(exc), "record_evidence")
            else:
                raise
    elif governance.get("effective_mode") == "light":
        try:
            validate_governance_obligation_evidence(
                state, "close", evidence, artifact_root=artifact_root
            )
        except ValueError as exc:
            if "requires typed governance obligation evidence" in str(exc):
                advise("governance_evidence_recommended", str(exc), "record_evidence")
            else:
                raise


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
            # Historical mandatory-gate insertion was a policy veto in
            # disguise: it silently rewrote the orchestrator's chosen route.
            # Keep the recommendation visible while preserving the choice.
            pipeline_corrections.append({"gate": gate, "reason": reason, "advisory": True})

    if proposed_pipeline is not None:
        for gate in MANDATORY_PIPELINE_GATES[complexity]:
            ensure_mandatory(gate, f"mandatory {complexity} audit gate")
        if "documentation" in pipeline and "close" in pipeline and pipeline.index("documentation") > pipeline.index("close"):
            pipeline_corrections.append({"gate": "documentation", "reason": "documentation is recommended before close", "advisory": True})
        if "close" in pipeline and pipeline[-1] != "close":
            pipeline_corrections.append({"gate": "close", "reason": "close is recommended as the final gate", "advisory": True})

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
        # Documentation/close are governance recommendations.  Preserve the
        # coordinator-selected pipeline and expose the missing convention in
        # the durable advisory projection instead of rejecting task creation.
        task_number, task_dir = allocate_task_directory(root, task_id)
        state_path = task_dir / "state.sqlite"
        principal = redact(params.get("principal") or "local", 256)
        baseline = capture_project_manifest(select_project_root(params))
        baseline_preflight = dict(baseline)
        baseline_preflight.pop("captured_at", None)
        _json_text(baseline_preflight, label="baseline manifest", max_bytes=MAX_MANIFEST_BYTES)
        user_language = normalize_user_language(params.get("user_language"), params.get("user_request", ""))
        plan_approval_policy = str(params.get("plan_approval") or "auto")
        if plan_approval_policy not in {"auto", "required"}:
            raise ValueError("plan_approval must be auto or required")
        # ``plan_approval=required`` is a policy/configuration value, not
        # proof that the user asked to pause for a review.  Only an explicit
        # trusted ingress marker may be persisted as user intent.
        trusted_plan_review_requested = any(
            params.get(marker) is True
            for marker in (
                "plan_approval_user_requested",
                "user_requested_plan_approval",
                "plan_review_requested",
                "explicit_plan_approval_requested",
            )
        )
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
        task = {"schema": SCHEMA, "pipeline_contract_version": PIPELINE_CONTRACT_VERSION, "task_id": task_id, "task_number": task_number, "user_request": exact_user_request, "user_request_digest": exact_user_request_digest, "user_request_projection": redact(exact_user_request, 4000), "intent_clarification_required": bool(params.get("intent_clarification_required", False)), "intent_clarification_reason": redact(params.get("intent_clarification_reason", ""), 500) or None, "complexity": classification["complexity"], "base_pipeline": classification["base_pipeline"], "initial_pipeline": pipeline, "parallel_groups": parallel_groups, "requirements": receipt_requirements, "constraints": [redact(item, 1000) for item in init_constraints], "acceptance_criteria": [redact(item, 1000) for item in init_acceptance_criteria], "scope": [redact(item, 500) for item in init_scope], "allowed_paths": [redact(item, 500) for item in init_allowed_paths], "verification": [redact(item, 1000) for item in init_verification], "budget": redact(params.get("budget", ""), 500), "pause_conditions": [redact(item, 1000) for item in init_pause_conditions], "plan_approval": plan_approval_policy, "plan_approval_user_requested": trusted_plan_review_requested, "initiative_ref": redact(params.get("initiative_ref", ""), 200) or None, "governance_mode": str(params.get("governance_mode") or "auto"), "governance": sanitize_structured(params.get("governance")) if isinstance(params.get("governance"), dict) else None, "principal": principal, "user_language": user_language, "communication_profile": select_communication_profile(params), "internal_language": "en", "classification_id": classification_id, "project_root": baseline["project_root"], "initial_manifest_ref": baseline_ref, "tracker_policy": TRACKER_POLICY, "created_at": now()}
        if follow_up is not None:
            task["follow_up"] = sanitize_structured(follow_up)
        state = {"schema": SCHEMA, "pipeline_contract_version": PIPELINE_CONTRACT_VERSION, "task_id": task_id, "task_number": task_number, "status": "active", "principal": principal, "user_language": user_language, "communication_profile": select_communication_profile(params), "internal_language": "en", "complexity": classification["complexity"], "initiative_ref": redact(params.get("initiative_ref", ""), 200) or None, "governance_mode": str(params.get("governance_mode") or "auto"), "governance": sanitize_structured(params.get("governance")) if isinstance(params.get("governance"), dict) else None, "current_pipeline": pipeline, "pipeline_obligations": list(pipeline), "parallel_groups": parallel_groups, "current_gates": active_gates({"current_pipeline": pipeline, "parallel_groups": parallel_groups, "completed_gates": [], "skipped_gates": []}), "completed_gates": [], "skipped_gates": [], "gates": {}, "attempts": [], "evidence": [], "locks": {}, "pipeline_changes": [], "adaptive_events": [], "recovery_events": [], "resume_events": [], "reassessment_receipts": [], "documentation_receipt": None, "manifest_receipts": [], "initial_manifest_ref": baseline_ref, "initial_manifest_digest": baseline["digest"], "manifest_snapshot_cleanup": {"status": "active", "at": now()}, "classification_receipt": classification_id, "handoff_created": False, "replan_count": 0, "require_delegation": classification["complexity"] in {"C2", "C3"}, "require_handoff": classification["complexity"] in {"C2", "C3"}, "plan_approval": {"policy": plan_approval_policy, "status": "pending_plan" if trusted_plan_review_requested else "not_required", "user_requested": trusted_plan_review_requested, "history": []}, "plan_approval_user_requested": trusted_plan_review_requested, "coordinator": activation["coordinator"], "parent_project_operations": activation["parent_project_operations"], "worker_visibility": activation["worker_visibility"], "worker_return_route": activation["worker_return_route"], "revision": 0, "updated_at": now()}
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
    receipt_id = "status-" + digest_text(canonical_json.dumps({
        "task_id": state["task_id"],
        "principal": state.get("principal", "local"),
        "revision": state["revision"],
    }))[:24]
    active = activation_record(root, {"principal": state.get("principal")}, state["task_id"])
    return {"task": task, "state": state, "active": bool(active), "status_receipt": receipt_id, "ledger_root": str(root)}


def _attempt(state: dict[str, Any], attempt_id: str) -> dict[str, Any]:
    attempt = next((item for item in state.get("attempts", []) if item.get("attempt_id") == attempt_id), None)
    if not attempt:
        raise ValueError("attempt_id does not belong to this task")
    return attempt


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
        status_receipt = "status-" + digest_text(canonical_json.dumps({
            "task_id": current_state["task_id"],
            "principal": current_state.get("principal", "local"),
            "revision": current_state["revision"],
        }))[:24]
        observed = {
            "task": load_task_definition(task_dir, current_state),
            "state": current_state,
            "active": bool(activation_record(
                root,
                {"principal": current_state.get("principal")},
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
        # Lifecycle recovery may have retired the mutable attempt after its
        # canonical AttemptResult was already completed.  The immutable result
        # is authoritative in this case: acknowledge the receipt idempotently
        # without mutating the invalidated attempt projection.  Orchestration
        # will reconcile its own projection and continue through the normal
        # corrective/gate route.
        if status == "passed" and attempt.get("invalidated"):
            canonical = attempt_protocol.get_attempt_result(
                root, task_id=state["task_id"], attempt_id=attempt_id,
            )
            if (
                isinstance(canonical, dict)
                and str(canonical.get("result_ref") or "") == str(attempt.get("attempt_result_ref") or "")
                and canonical.get("lifecycle_status") == attempt_protocol.LIFECYCLE_COMPLETED
            ):
                return {
                    "attempt_id": attempt_id,
                    "status": status,
                    "idempotent": True,
                    "recovered_invalidated_attempt": True,
                    "attempt_result_ref": canonical.get("result_ref"),
                    "state": state,
                }
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
        "digest": redact(params.get("digest", ""), 256) or digest_text(canonical_json.dumps(execution) if execution else params.get("command", summary)),
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
    canonical_content = canonical_json.dumps(evidence)
    artifact = store_immutable_artifact(
        task_dir,
        state["task_id"],
        kind="evidence",
        title=f"evidence/{evidence_id}.json",
        mime_type="application/json",
        content=canonical_content,
        export_path=f"evidence/{evidence_id}.json",
    )
    # Governance close/activation validation must be able to resolve the
    # evidence back to canonical SQLite content.  These fields are produced
    # only after the immutable artifact write and are never accepted from the
    # public evidence payload.  Keep them in the task-state/catalog binding;
    # do not append them to the export, because the export is an immutable
    # byte-for-byte projection of the canonical SQLite artifact.
    evidence["artifact_ref"] = artifact["artifact_ref"]
    evidence["artifact_digest"] = artifact["digest_sha256"]
    evidence["artifact_immutable"] = bool(artifact["immutable"])
    evidence["artifact_verified"] = True
    # Materialize the export with the exact canonical bytes.  The catalog
    # binding above belongs to task state, not to the immutable artifact body.
    write_text_atomic(task_dir / "evidence" / f"{evidence_id}.json", canonical_content)
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


def _activate_closure_rework(
    state: dict[str, Any],
    *,
    gate: str,
    findings: list[dict[str, Any]],
    source_result_refs: list[str],
) -> str:
    """Expose the gate policy's server-owned closure rework transition."""
    from cortex_runtime.gate_transitions import _activate_closure_rework as _implementation

    return _implementation(
        state,
        gate=gate,
        findings=findings,
        source_result_refs=source_result_refs,
    )


def _activate_closure_rework(
    state: dict[str, Any],
    *,
    gate: str,
    findings: list[dict[str, Any]],
    source_result_refs: list[str],
) -> str:
    """Expose the gate policy's server-owned closure recovery to the engine.

    Runtime modules are wired through the composition root.  Keeping this
    narrow facade wrapper lets the orchestration engine invoke the same
    canonical rework transition that ``record_gate`` uses without importing
    the executable facade back from the runtime package.
    """
    from cortex_runtime.gate_transitions import _activate_closure_rework as _impl

    return _impl(
        state,
        gate=gate,
        findings=findings,
        source_result_refs=source_result_refs,
    )


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
    failure_key = digest_text(canonical_json.dumps({"gate": gate, "mode": mode, "error": reason}))
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
        # Exhausting the fast-path retry budget is an internal adapter
        # diagnostic, not a Cortex/user blocker.  Preserve the evidence and
        # route the same task through the server-owned diagnostic recovery
        # Planner on its next lifecycle advance.  Do not mark the task
        # blocked, which previously stranded it behind a handoff-only path.
        state.pop("blocked_gate", None)
        state.pop("blocked_reason", None)
        state["status"] = "active"
        state["commit_gate_recovery"] = {
            "schema": "cortex/recovery-contract/v1",
            "status": "diagnostic_recovery_pending",
            "gate": gate,
            "mode": mode,
            "failure_key": failure_key,
            "replacement_worker_authorized": False,
            "at": now(),
        }
        next_action = "server_owned_diagnostic_recovery_then_retry_commit_gate"
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
    three failures for the same gate/mode the adapter records a diagnostic
    recovery receipt and leaves the task active for the server-owned Planner
    route; it never creates a terminal Cortex block. This limit is
    intentionally unrelated to pipeline, QA, review, worker,
    finding-remediation, or closure rework attempts.
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
        prior_status = state.get("status")
        prior_advice_count = len(state.get("completion_advice") or []) if isinstance(state.get("completion_advice"), list) else 0
        validate_completion_invariants(state, artifact_root=root)
        if state.get("status") != prior_status or len(state.get("completion_advice") or []) != prior_advice_count:
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "completion_advisory",
                "recorded lifecycle completion advice and retained the task as executable",
            )
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


def reopen_blocked_lifecycle_state(params: dict[str, Any]) -> dict[str, Any]:
    """Transition an authorized blocked v11 task back to active execution.

    This is an engine implementation port, not a model-facing operation.
    Public callers use the typed ``manage_orchestration`` resume intent.
    """
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
        signals = [redact(item, 500) for item in params.get("signals", [])]
        decision = str(params.get("decision", "")).strip()
        reason = redact(params.get("reason", ""), 2000)
        intent = str(params.get("intent", "add_specialist"))
        if intent not in {"add_specialist", "resequence", "rework_gate", "stop"}:
            raise ValueError("intent must be add_specialist, resequence, rework_gate, or stop")
        if state.get("require_delegation") and intent != "stop" and decision not in {"unchanged", "updated", "stop"}:
            raise ValueError("C2/C3 reassessment requires decision unchanged, updated, or stop")
        if state.get("require_delegation") and intent != "stop" and not reason:
            raise ValueError("C2/C3 reassessment requires an explicit reason")
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
            # Internal policy/recovery signals may recommend stopping or
            # pausing a route, but they may not stop the Cortex task. A task
            # stop is a user decision only; callers must opt in explicitly so
            # an internal worker outcome cannot become a hidden blocker.
            explicit_user_stop = (
                decision == "stop"
                and (
                    params.get("user_decision") is True
                    or params.get("origin") == "user"
                    or params.get("user_requested_stop") is True
                )
            )
            if not explicit_user_stop:
                advice = {
                    "code": "task_stop_requires_user_decision",
                    "severity": "warning",
                    "message": "Stop was proposed by orchestration policy, but the task remains executable until the user explicitly decides to stop.",
                    "recommended_next": "dispatch_corrective_worker",
                }
                state.setdefault("pipeline_advice", []).append(advice)
                state.setdefault("reassessment_receipts", []).append({**receipt, "advisory": advice})
                save_state(task_dir, task_dir / "state.sqlite", state, "reassess_advisory", "internal stop recommendation recorded as advice")
                result.update({"state": state, "advisory": advice})
                return result
            state["status"] = "blocked"
            state["user_stop_requested"] = True
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
        completed = [redact(item, 1000) for item in params.get("completed", [])]
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
        payload = {"schema": SCHEMA, "task_id": state["task_id"], "created_at": now(), "source_revision": state["revision"], "gate": primary_gate(state), "completed": completed, "files": [redact(item, 500) for item in files], "file_manifest_receipt": receipt, "decisions": [redact(item, 2000) for item in params.get("decisions", [])], "risks": [redact(item, 2000) for item in params.get("risks", [])], "next_action": next_action}
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


LIFECYCLE_RUNTIME_SCHEMA = "cortex/lifecycle-runtime/v11"
ORCHESTRATION_TRANSACTION_SCHEMA = "cortex/orchestration-transaction/v1"
ORCHESTRATION_PLAN_SCHEMA = "cortex/orchestration-plan/v1"
V11_LIFECYCLES = frozenset({"start", "continue", "inspect", "resume", "deactivate", "lane", "resource", "question", "plan_approval"})
V11_MUTATING_LIFECYCLES = frozenset({"start", "continue", "resume", "deactivate", "lane", "resource", "question", "plan_approval"})
PUBLIC_ORCHESTRATION_SCHEMA = "cortex/orchestration/v11"
# This prefix used to contain a visible ``COORDINATOR LOCK`` message.  That
# leaked an internal routing guard into every public ``next_action`` and made
# ordinary recoverable lifecycle work look like a Cortex blocker.  Keep the
# coordinator-only constraint in the bundled skills and machine state; public
# actions must contain only the concrete server-derived operation to perform.
COORDINATOR_LOCK = ""


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


def _validation_next_action(
    operation: str,
    diagnostics: list[dict[str, Any]],
    *,
    task_ref: str | None = None,
    project_root: str | None = None,
) -> str:
    """Give a caller-correctable validation error an executable repair.

    ``COORDINATOR_LOCK`` is an internal routing instruction, not a repair
    contract.  Returning it for malformed user/tool input caused workers to
    retry with guessed fields and exposed implementation-only policy text in
    the visible transcript.  Validation responses therefore name the same
    public tool, its required identity, and the complete value/rule set.
    """
    suffix = (
        " Correct every listed diagnostic in this same request before retrying; "
        "do not apply a partial mutation or invent additional fields."
    )
    paths = [str(item.get("path")) for item in diagnostics if isinstance(item, dict) and item.get("path")]
    path_text = f" Fix these exact paths first: {', '.join(paths)}." if paths else ""
    if operation == "start_orchestration":
        task_rule = (
            " For task paths, use the nested task field_schema: requirements, constraints, scope, "
            "allowed_paths, and pause_conditions are arrays of strings; acceptance_criteria and "
            "verification are non-empty arrays of non-empty strings."
            if any(path == "task" or path.startswith("task.") for path in paths)
            else ""
        )
        return (
            "Retry start_orchestration with the same project_root and task.user_request. "
            "Correct every listed path according to the advertised start_orchestration schema; "
            "preserve every valid field and do not create a task until all listed paths are valid."
            + task_rule + path_text + suffix
        )
    if operation in {"manage_orchestration", "management_failed", "manage_orchestration_validation_failed"}:
        identity = (
            f" Keep task_ref={task_ref!r} and the exact unchanged coordinator_ref returned by start_orchestration."
            if task_ref else
            " Include the exact task_ref and coordinator_ref returned by start_orchestration."
        )
        return (
            "Retry manage_orchestration with the same intent and corrected explicit references."
            + identity
            + " The request never accepts project_root, caller-authored replacement waves, completion bodies, "
              "or project-wide maintenance authority."
            + path_text
            + suffix
        )
    if operation in {"complete_attempt", "record_attempt_event", "read_worker_result", "read_dispatch_briefing", "worker_question"}:
        tool_fields = {
            "complete_attempt": "exact task_ref and assignment_ref plus one compact plan or outcome, or repair_capsule, base_payload_digest, and diagnostic-scoped patches",
            "record_attempt_event": "exact task_ref and assignment_ref, event_type, payload, and optional event_key",
            "read_worker_result": "coordinator: task_ref, coordinator_ref, and current step; worker: task_ref, assignment_ref, and one granted predecessor attempt_result_ref",
            "read_dispatch_briefing": "exact task_ref and assignment_ref plus optional cursor",
            "worker_question": "exact task_ref and assignment_ref plus action, question/options, or the exact returned question_ref",
        }[operation]
        planning_nesting = ""
        if operation == "complete_attempt" and any(
            path in {"$.overview", "$.work_packages"} for path in paths
        ):
            planning_nesting = (
                " `overview` and `work_packages` are planning fields: move them under "
                "`planning.overview` and `planning.work_packages`; do not submit them at the "
                "complete_attempt top level."
            )
        return (
            f"Retry {operation} on the same attempt. Correct the exact diagnostic paths listed in diagnostics; "
            f"send only the documented fields ({tool_fields})."
            + planning_nesting + path_text + suffix
        )
    return f"Correct every listed diagnostic and retry the same {operation} call without changing unrelated fields." + path_text + suffix


def _validation_contract(
    operation: str,
    diagnostics: list[dict[str, Any]],
    *,
    task_ref: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Return machine-readable field schemas beside human diagnostics."""
    contract: dict[str, Any] = {
        "schema": "cortex/validation-error/v1",
        "operation": operation,
        "diagnostics_are_complete": True,
        "retry": {"same_call": True, "preserve_valid_fields": True, "replacement_worker_authorized": False},
        "invalid_paths": [item.get("path") for item in diagnostics if isinstance(item, dict) and item.get("path")],
    }
    if operation in {"manage_orchestration", "management_failed", "manage_orchestration_validation_failed"}:
        registry = globals().get("PUBLIC_SCHEMA_REGISTRY")
        public_schema = registry.get("manage_orchestration") if isinstance(registry, dict) else None
        if isinstance(public_schema, dict):
            # Never maintain a reduced second schema in an error receipt: it
            # silently omits terminal intents and payload discriminators.
            contract["request_schema"] = {"tool": "manage_orchestration", **public_schema}
    else:
        # Start/continue and the worker-facing coordinator tools use the same
        # public JSON Schema advertised through MCP.  Returning that exact
        # schema in a validation receipt lets the caller repair the submitted
        # fields without reconstructing the rest of the request.
        registry = globals().get("PUBLIC_SCHEMA_REGISTRY")
        public_schema = registry.get(operation) if isinstance(registry, dict) else None
        if isinstance(public_schema, dict):
            contract["request_schema"] = {"tool": operation, **public_schema}
    return contract


def _enrich_validation_diagnostics(
    operation: str,
    diagnostics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach the public field schema and received value to boundary errors."""
    registry = globals().get("PUBLIC_SCHEMA_REGISTRY")
    schema = registry.get(operation) if isinstance(registry, dict) else None
    if not isinstance(schema, dict):
        return diagnostics
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    enriched: list[dict[str, Any]] = []
    for item in diagnostics:
        diagnostic = dict(item) if isinstance(item, dict) else {"message": str(item)}
        path = str(diagnostic.get("path") or "$")
        top = path[2:] if path.startswith("$.") else path
        top = top.split(".", 1)[0].split("[", 1)[0]
        field_schema = properties.get(top)
        if not isinstance(field_schema, dict):
            field_schema = {"type": "object", "properties": properties}
        diagnostic.setdefault("received", None)
        diagnostic.setdefault("expected", field_schema)
        diagnostic.setdefault("field_schema", field_schema)
        diagnostic.setdefault("fix", f"Correct {path} according to field_schema and retry {operation} with unrelated fields unchanged.")
        enriched.append(diagnostic)
    return enriched


def _start_exception_diagnostics(exc: BaseException, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Turn late start validation exceptions into field-level form errors.

    The initial envelope pass catches missing/unknown keys, but normalization
    and wave compilation happen later in the same atomic start transaction.
    Those checks must use the same public field contract instead of falling
    through to the coordinator-only lock text. ValidationFailure already
    carries complete nested diagnostics; the mapping below covers scalar/list
    checks that intentionally remain strict helpers.
    """
    if isinstance(exc, ValidationFailure):
        return [dict(item) for item in exc.diagnostics]

    message = redact(str(exc), 1000)
    raw_task = params.get("task") if isinstance(params, Mapping) else None
    task_schema: dict[str, Any] = {}
    registry = globals().get("PUBLIC_SCHEMA_REGISTRY")
    start_schema = registry.get("start_orchestration") if isinstance(registry, dict) else None
    if isinstance(start_schema, dict):
        properties = start_schema.get("properties")
        task_schema = properties.get("task", {}) if isinstance(properties, dict) and isinstance(properties.get("task"), dict) else {}
    task_properties = task_schema.get("properties", {}) if isinstance(task_schema, dict) else {}

    path: str | None = None
    received: Any = None
    field_schema: dict[str, Any] | None = None
    match = re.fullmatch(r"(?P<field>[a-z_]+) must be a current canonical text array", message)
    if match:
        field = match.group("field")
        path = f"task.{field}"
        received = raw_task.get(field) if isinstance(raw_task, dict) else None
        field_schema = task_properties.get(field) if isinstance(task_properties, dict) else None
    elif message.startswith("task requirements must be a current canonical text array"):
        path = "task.requirements"
        received = raw_task.get("requirements") if isinstance(raw_task, dict) else None
        field_schema = task_properties.get("requirements") if isinstance(task_properties, dict) else None
    else:
        match = re.fullmatch(r"task\.(?P<field>[a-z_]+) must be (?:an array of non-empty strings|a boolean|a BCP-47-like lowercase language tag)", message)
        if match:
            field = match.group("field")
            path = f"task.{field}"
            received = raw_task.get(field) if isinstance(raw_task, dict) else None
            field_schema = task_properties.get(field) if isinstance(task_properties, dict) else None
        elif message.startswith("task.acceptance_criteria") or message.startswith("task.verification"):
            field = "acceptance_criteria" if message.startswith("task.acceptance_criteria") else "verification"
            path = f"task.{field}"
            received = raw_task.get(field) if isinstance(raw_task, dict) else None
            field_schema = task_properties.get(field) if isinstance(task_properties, dict) else None
        elif message.startswith("waves must be"):
            path = "waves"
            received = params.get("waves") if isinstance(params, Mapping) else None
            properties = start_schema.get("properties", {}) if isinstance(start_schema, dict) else {}
            field_schema = properties.get("waves") if isinstance(properties, dict) else None

    if path is None:
        # Keep a deterministic field location even for a late environmental
        # or governance check. The full message remains available, but the
        # caller receives a repairable start form rather than an internal lock.
        path = "task" if message.startswith(("task ", "task.", "orchestration requires")) else "request"
        field_schema = task_schema if path == "task" and isinstance(task_schema, dict) else start_schema
        received = raw_task if path == "task" else None

    diagnostic: dict[str, Any] = {
        "code": "start_orchestration_validation_failed",
        "phase": "preflight",
        "path": path,
        "message": message,
        "received": received,
        "expected": field_schema or "a value accepted by the advertised start_orchestration schema",
        "fix": f"Correct {path} according to field_schema and retry start_orchestration without changing unrelated fields.",
    }
    if isinstance(field_schema, dict):
        diagnostic["field_schema"] = field_schema
    return [diagnostic]


def _collect_lifecycle_diagnostics(lifecycle: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the complete public facade envelope before any ledger write.

    The runtime handlers still perform authoritative checks, but this pass is
    intentionally non-throwing and aggregates independent request mistakes so
    a coordinator can repair one payload instead of discovering errors one at
    a time.
    """
    diagnostics: list[dict[str, Any]] = []
    allowed_top_level = {
        "submission_id", "project_root", "principal",
        "task", "task_id", "wave_id", "waves", "host_capabilities", "result_refs", "payload",
        "reason", "terminal_recovery",
        "_materialization_fence",
    }
    for key in sorted(set(params) - allowed_top_level):
        diagnostics.append(_request_diagnostic(key, "unsupported lifecycle parameter", "a field accepted by this v11 lifecycle"))
    if lifecycle not in V11_LIFECYCLES:
        raise ValueError("unsupported internal v11 lifecycle")

    root = params.get("project_root")
    if not isinstance(root, str) or not root.strip():
        diagnostics.append(_request_diagnostic("project_root", "project_root is required", "an existing absolute project directory"))
    elif not Path(root).expanduser().is_absolute():
        diagnostics.append(_request_diagnostic("project_root", "project_root must be an absolute path", "an existing absolute project directory"))

    if os.environ.get("CORTEX_ROOT"):
        diagnostics.append(_request_diagnostic("environment.CORTEX_ROOT", "CORTEX_ROOT is not supported; use project_root", "no CORTEX_ROOT override"))

    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    mutating = lifecycle in V11_MUTATING_LIFECYCLES
    if lifecycle == "lane" and str(payload.get("command", "")) == "inspect":
        mutating = False
    if lifecycle == "question" and str(payload.get("command", "ask")) in {"list", "updates"}:
        mutating = False
    if mutating:
        submission_id = params.get("submission_id")
        if not isinstance(submission_id, str) or not submission_id.strip():
            diagnostics.append(_request_diagnostic("submission_id", f"{lifecycle} requires submission_id", "a stable lowercase submission id"))
        else:
            require_submission = submission_id.strip()
            if "/" in require_submission or "\\" in require_submission or not SAFE_ID_RE.fullmatch(require_submission.lower()):
                diagnostics.append(_request_diagnostic("submission_id", "submission_id is not a valid identifier", "lowercase letters, numbers, hyphens, or underscores only"))

    if lifecycle in {"start", "continue", "inspect", "resume", "lane", "resource", "question", "plan_approval"}:
        if not isinstance(params.get("principal"), str) or not str(params.get("principal")).strip():
            diagnostics.append(_request_diagnostic("principal", f"{lifecycle} requires principal", "the server-owned task principal"))
    def require_identifier(path: str, value: Any) -> None:
        raw = str(value or "").strip()
        if not raw:
            diagnostics.append(_request_diagnostic(path, f"{path} is required", "a non-empty lowercase identifier using letters, numbers, hyphens, or underscores"))
        elif "/" in raw or "\\" in raw or not SAFE_ID_RE.fullmatch(raw.lower()):
            diagnostics.append(_request_diagnostic(path, f"{path} is not a valid identifier: {raw!r}", "lowercase letters, numbers, hyphens, or underscores only"))

    if lifecycle == "start":
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
            for index, wave in enumerate(waves, 1):
                wave_path = f"waves[{index - 1}]"
                if not isinstance(wave, dict):
                    diagnostics.append(_request_diagnostic(wave_path, "wave must be an object", "{wave_id, delegations}"))
                    continue
                if not str(wave.get("wave_id", "")).strip():
                    diagnostics.append(_request_diagnostic(f"{wave_path}.wave_id", "wave_id is required; do not use id", "a stable lowercase wave identifier"))
                else:
                    require_identifier(f"{wave_path}.wave_id", wave.get("wave_id"))
                delegations = wave.get("delegations")
                if not isinstance(delegations, list) or not delegations:
                    diagnostics.append(_request_diagnostic(f"{wave_path}.delegations", "wave requires a non-empty delegations array", "an array of delegation objects"))
                    continue
                for delegation_index, delegation in enumerate(delegations, 1):
                    delegation_path = f"{wave_path}.delegations[{delegation_index - 1}]"
                    if not isinstance(delegation, dict):
                        diagnostics.append(_request_diagnostic(delegation_path, "delegation must be an object", "{gate, agent, objective, ownership, allowed_paths, acceptance_criteria, verification}"))
                        continue
                    if not str(delegation.get("gate", "")).strip():
                        diagnostics.append(_request_diagnostic(f"{delegation_path}.gate", "delegation requires gate", "a supported pipeline gate"))

        host = params.get("host_capabilities")
        if not isinstance(host, dict):
            diagnostics.append(_request_diagnostic("host_capabilities", "start requires host_capabilities", "an object with spawn_agent_models and optional confirmed default"))
        else:
            models = host.get("spawn_agent_models")
            if not isinstance(models, list) or not models:
                diagnostics.append(_request_diagnostic("host_capabilities.spawn_agent_models", "host_capabilities requires a non-empty spawn_agent_models array", "exact native spawn_agent model identifiers"))
            for key in ("spawn_agent_models",):
                if key in host and not isinstance(host[key], list):
                    diagnostics.append(_request_diagnostic(f"host_capabilities.{key}", "model catalog must be an array", "an array of model identifiers"))
            if "spawn_agent_default_model" in host and not isinstance(host["spawn_agent_default_model"], str):
                diagnostics.append(_request_diagnostic("host_capabilities.spawn_agent_default_model", "configured default model must be a string", "a supported model identifier"))

    elif lifecycle in {"continue", "inspect", "resume"}:
        require_identifier("task_id", params.get("task_id"))
        if lifecycle == "continue":
            require_identifier("wave_id", params.get("wave_id"))
            result_refs = params.get("result_refs")
            if not isinstance(result_refs, list) or not result_refs:
                diagnostics.append(_request_diagnostic("result_refs", "continue requires a non-empty result_refs array", "one canonical AttemptResult ref per active attempt"))
            elif any(not isinstance(item, dict) for item in result_refs):
                diagnostics.append(_request_diagnostic("result_refs", "every result ref must be an object", "objects with attempt_id and attempt_result_ref only"))
            else:
                for index, item in enumerate(result_refs):
                    unknown = sorted(set(item) - {"attempt_id", "attempt_result_ref"})
                    for key in unknown:
                        diagnostics.append(_request_diagnostic(
                            f"result_refs[{index}].{key}",
                            "caller-authored completion fields are forbidden",
                            "attempt_id and attempt_result_ref only",
                        ))
                    require_identifier(f"result_refs[{index}].attempt_id", item.get("attempt_id"))
                    if not str(item.get("attempt_result_ref") or "").strip():
                        diagnostics.append(_request_diagnostic(
                            f"result_refs[{index}].attempt_result_ref",
                            "attempt_result_ref is required",
                            "the exact canonical ref returned by read_worker_result",
                        ))

    elif lifecycle in {"lane", "resource", "question", "plan_approval"} and not isinstance(params.get("payload"), dict):
        diagnostics.append(_request_diagnostic("payload", f"{lifecycle} requires a lifecycle-specific payload object", "an object with a supported command"))

    return diagnostics


def _engine_start_lifecycle(params: dict[str, Any]) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import start_lifecycle
    return start_lifecycle(params)


def _engine_continue_lifecycle(params: dict[str, Any]) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import continue_lifecycle
    return continue_lifecycle(params)


def _engine_manage_lifecycle(intent: str, params: dict[str, Any]) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import manage_lifecycle
    return manage_lifecycle(intent, params)


# These late-bound helpers keep the executable facade acyclic while the v11
# lifecycle implementation remains in the extracted engine module.
def _orchestrate_request_digest(params: dict[str, Any]) -> str:
    from cortex_runtime.orchestration_engine import _orchestrate_request_digest as _implementation

    return _implementation(params)


def _engine_inspect_lifecycle(params: dict[str, Any]) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import inspect_lifecycle
    return inspect_lifecycle(params)


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


CANONICAL_COMPLEXITIES = {"C1", "C2", "C3"}
CANONICAL_PLAN_APPROVAL_POLICIES = {"auto", "required"}
CANONICAL_STATUS_VALUES = set(TERMINAL_ATTEMPT_STATUSES)


def _v11_error(code: str, message: object, *, outcome: str = "needs_input", candidates: list[dict[str, Any]] | None = None, diagnostics: list[dict[str, Any]] | None = None, task_ref: str | None = None) -> dict[str, Any]:
    diagnostic_list = diagnostics or [{"code": code, "message": redact(message, 1000)}]
    result = {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "outcome": outcome,
        "code": code,
        "diagnostics": diagnostic_list,
        "dispatches": [],
        "next_action": (
            _validation_next_action(code, diagnostic_list)
            if diagnostics else f"{COORDINATOR_LOCK} {redact(message, 1000)}"
        ),
    }
    if diagnostics:
        result["validation"] = _validation_contract(code, diagnostic_list)
    if candidates is not None:
        result["candidates"] = candidates
    if task_ref:
        result["task_ref"] = task_ref
    return result


def _v11_envelope_error(
    operation: str,
    diagnostics: list[dict[str, Any]],
    *,
    task_ref: str | None = None,
) -> dict[str, Any]:
    """Return one atomic, retryable boundary receipt for independent errors."""
    # Preserve the established public wire codes while allowing the
    # diagnostics themselves to identify the precise ingress and path.
    code = {
        "start_orchestration": "start_validation_failed",
    }.get(operation, f"{operation}_validation_failed")
    # Several layers may independently observe the same malformed leaf
    # (schema form and a semantic compiler).  One deterministic card per RFC
    # 6901 pointer is actionable; duplicates merely make an LLM retry worse.
    unique: list[dict[str, Any]] = []
    seen_pointers: set[str] = set()
    for item in diagnostics:
        pointer = str(item.get("json_pointer") or item.get("path") or "") if isinstance(item, dict) else ""
        if pointer and pointer in seen_pointers:
            continue
        if pointer:
            seen_pointers.add(pointer)
        unique.append(item)
    complete_diagnostics = _enrich_validation_diagnostics(operation, unique)
    result = _v11_error(
        code,
        "request failed envelope validation",
        diagnostics=complete_diagnostics,
    )
    if task_ref:
        result["task_ref"] = task_ref
    result["next_action"] = _validation_next_action(
        operation,
        complete_diagnostics,
        task_ref=task_ref,
    )
    result["validation"] = _validation_contract(
        operation,
        complete_diagnostics,
        task_ref=task_ref,
    )
    result["retryable"] = True
    result["attempt_budget_consumed"] = False
    result["worker_replacement_authorized"] = False
    return result


def _v11_collect_fields(
    params: Any,
    allowed: set[str],
    *,
    operation: str,
    public_schema: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    if not isinstance(params, dict):
        return [{"code": f"{operation}_validation_failed", "path": "request", "message": "request must be an object", "received": params, "expected": "object"}]
    for key in sorted(set(params) - allowed):
        item: dict[str, Any] = {
            "code": f"{operation}_validation_failed",
            "path": key,
            "json_pointer": "/" + key.replace("~", "~0").replace("/", "~1"),
            "message": "unsupported field",
            "received": params.get(key),
        }
        if isinstance(public_schema, Mapping):
            card = _v11_schema_object_card(public_schema)
            item["field_schema"] = card
            item["expected"] = card
        diagnostics.append(item)
    return diagnostics


def _v11_pointer_path(pointer: str) -> str:
    """Render an RFC 6901 pointer as the familiar diagnostic dotted path."""
    if not pointer:
        return "request"
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.lstrip("/").split("/")]
    rendered: list[str] = []
    for part in parts:
        if part.isdigit() and rendered:
            rendered[-1] += f"[{part}]"
        else:
            rendered.append(part)
    return ".".join(rendered)


def _v11_schema_value_diagnostics(
    value: Any,
    schema: Mapping[str, Any],
    *,
    pointer: str,
    operation: str,
) -> list[dict[str, Any]]:
    """Validate every *supplied* public JSON-schema leaf without side effects.

    MCP's tools/list schema is the boundary authority.  The lifecycle
    normalizers deliberately have stricter semantic checks later, but all
    ordinary shape/type/enum/pattern/range failures must be returned together
    before capability lookup.  This compact evaluator intentionally covers
    the JSON-Schema vocabulary used by the public coordinator forms.
    """
    diagnostics: list[dict[str, Any]] = []

    def add(
        at: str,
        message: str,
        card: Mapping[str, Any],
        received: Any,
        sink: list[dict[str, Any]],
    ) -> None:
        # A diagnostic describes the violated leaf, not its whole descendants.
        # This keeps array/object cards executable and stable while nested
        # item failures retain their own exact item schema.
        declared = card.get("type")
        facets = [
            "type", "enum", "const", "minLength", "maxLength", "pattern", "format",
            "minimum", "maximum", "minItems", "maxItems", "uniqueItems",
            "minProperties", "maxProperties",
        ]
        field_schema = {facet: card[facet] for facet in facets if facet in card}
        if declared == "object" and card.get("additionalProperties") is False:
            field_schema["additionalProperties"] = False
            properties = card.get("properties")
            field_schema["properties"] = sorted(properties) if isinstance(properties, Mapping) else properties
        if not field_schema:
            field_schema = dict(card)
        sink.append({
            "code": f"{operation}_validation_failed",
            "phase": "payload",
            "path": _v11_pointer_path(at),
            "json_pointer": at,
            "message": message,
            "received": received,
            "expected": field_schema,
            "field_schema": field_schema,
            "fix": f"Correct {at or '/'} according to field_schema and retry {operation} with unrelated fields unchanged.",
        })

    def type_matches(candidate: Any, declared: Any) -> bool:
        types = declared if isinstance(declared, list) else [declared]
        for kind in types:
            if kind == "object" and isinstance(candidate, dict): return True
            if kind == "array" and isinstance(candidate, list): return True
            if kind == "string" and isinstance(candidate, str): return True
            if kind == "boolean" and type(candidate) is bool: return True
            if kind == "integer" and type(candidate) is int: return True
            if kind == "number" and type(candidate) in {int, float}: return True
            if kind == "null" and candidate is None: return True
        return False

    def variant_score(candidate: Any, node: Mapping[str, Any]) -> int:
        """Prefer the branch whose explicit discriminators match the request."""
        if not isinstance(candidate, dict):
            return 0
        properties = node.get("properties")
        if not isinstance(properties, Mapping):
            return 0
        score = 0
        for key, child in properties.items():
            if not isinstance(child, Mapping) or "const" not in child or key not in candidate:
                continue
            score += 100 if candidate[key] == child["const"] else -100
        return score

    def visit(
        candidate: Any,
        node: Mapping[str, Any],
        at: str,
        sink: list[dict[str, Any]],
    ) -> None:
        variants = node.get("oneOf")
        if isinstance(variants, list) and variants:
            # JSON Schema applies sibling constraints together with ``oneOf``.
            # Validate the common closed object first, then select exactly one
            # conditional branch.  The start wave schema uses this to keep its
            # inherited phase/profile ownership model-visible without
            # duplicating the complete worker form in every phase branch.
            common = {key: item for key, item in node.items() if key != "oneOf"}
            if common:
                common_diagnostics: list[dict[str, Any]] = []
                visit(candidate, common, at, common_diagnostics)
                if common_diagnostics:
                    sink.extend(common_diagnostics)
                    return
            evaluated: list[tuple[int, list[dict[str, Any]]]] = []
            for variant in variants:
                if not isinstance(variant, Mapping):
                    continue
                branch_diagnostics: list[dict[str, Any]] = []
                visit(candidate, variant, at, branch_diagnostics)
                evaluated.append((variant_score(candidate, variant), branch_diagnostics))
            matching = [item for _score, item in evaluated if not item]
            if len(matching) == 1:
                return
            if evaluated:
                highest = max(score for score, _items in evaluated)
                candidates = [items for score, items in evaluated if score == highest]
                best = min(candidates, key=len)
                if best:
                    sink.extend(best)
                else:
                    add(at, "must match exactly one published branch", node, candidate, sink)
            return
        declared = node.get("type")
        if declared is not None and not type_matches(candidate, declared):
            add(at, "must match the published type", node, candidate, sink)
            return
        if "enum" in node and candidate not in node["enum"]:
            add(at, "must be one of the published enum values", node, candidate, sink)
        if "const" in node and candidate != node["const"]:
            add(at, "must equal the published constant", node, candidate, sink)
        if isinstance(candidate, str):
            if isinstance(node.get("minLength"), int) and len(candidate) < node["minLength"]:
                add(at, "is shorter than the published minimum length", node, candidate, sink)
            if isinstance(node.get("maxLength"), int) and len(candidate) > node["maxLength"]:
                add(at, "is longer than the published maximum length", node, candidate, sink)
            pattern = node.get("pattern")
            if isinstance(pattern, str) and re.fullmatch(pattern, candidate) is None:
                add(at, "does not match the published pattern", node, candidate, sink)
        if type(candidate) is int:
            if isinstance(node.get("minimum"), (int, float)) and candidate < node["minimum"]:
                add(at, "is below the published minimum", node, candidate, sink)
            if isinstance(node.get("maximum"), (int, float)) and candidate > node["maximum"]:
                add(at, "is above the published maximum", node, candidate, sink)
        if isinstance(candidate, list):
            if isinstance(node.get("minItems"), int) and len(candidate) < node["minItems"]:
                add(at, "contains fewer items than the published minimum", node, candidate, sink)
            if isinstance(node.get("maxItems"), int) and len(candidate) > node["maxItems"]:
                add(at, "contains more items than the published maximum", node, candidate, sink)
            if node.get("uniqueItems") is True:
                encoded = [json.dumps(item, sort_keys=True, default=str) for item in candidate]
                if len(encoded) != len(set(encoded)):
                    add(at, "must contain unique items", node, candidate, sink)
            item_schema = node.get("items")
            if isinstance(item_schema, Mapping):
                for index, item in enumerate(candidate):
                    visit(item, item_schema, f"{at}/{index}", sink)
        if isinstance(candidate, dict):
            if isinstance(node.get("minProperties"), int) and len(candidate) < node["minProperties"]:
                add(at, "contains fewer properties than the published minimum", node, candidate, sink)
            if isinstance(node.get("maxProperties"), int) and len(candidate) > node["maxProperties"]:
                add(at, "contains more properties than the published maximum", node, candidate, sink)
            properties = node.get("properties")
            if isinstance(properties, Mapping):
                for key in node.get("required", []):
                    if key not in candidate:
                        escaped = str(key).replace("~", "~0").replace("/", "~1")
                        child_schema = properties.get(key)
                        add(
                            f"{at}/{escaped}", "is required",
                            child_schema if isinstance(child_schema, Mapping) else {"type": "object"},
                            None, sink,
                        )
                if node.get("additionalProperties") is False:
                    for key in sorted(set(candidate) - set(properties)):
                        escaped = str(key).replace("~", "~0").replace("/", "~1")
                        add(f"{at}/{escaped}", "is not a supported field", {
                            "type": "object", "additionalProperties": False,
                            "properties": sorted(properties),
                        }, candidate[key], sink)
                elif isinstance(node.get("additionalProperties"), Mapping):
                    additional = node["additionalProperties"]
                    for key in sorted(set(candidate) - set(properties)):
                        escaped = str(key).replace("~", "~0").replace("/", "~1")
                        visit(candidate[key], additional, f"{at}/{escaped}", sink)
                for key, child_schema in properties.items():
                    if key in candidate and isinstance(child_schema, Mapping):
                        escaped = str(key).replace("~", "~0").replace("/", "~1")
                        visit(candidate[key], child_schema, f"{at}/{escaped}", sink)

    visit(value, schema, pointer, diagnostics)
    return diagnostics


def _continue_form_diagnostics(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Collect independent, caller-correctable continue shape errors.

    This deliberately stops at the public form boundary.  Checks which need
    the active task (slot cardinality, dispatch/result identity, and blocking
    questions) remain lifecycle checks and must not be presented as malformed
    user input.
    """
    diagnostics: list[dict[str, Any]] = []
    schema = PUBLIC_SCHEMA_REGISTRY.get("continue_orchestration", {})
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}

    def add(path: str, message: str, *, received: Any = None, expected: Any = None) -> None:
        pointer = "/" + "/".join(
            part.replace("~", "~0").replace("/", "~1")
            for part in path.replace("[", ".").replace("]", "").split(".")
            if part
        )
        top = path.split(".", 1)[0].split("[", 1)[0]
        field_schema = properties.get(top) if isinstance(properties, dict) else None
        item: dict[str, Any] = {
            "code": "continue_orchestration_validation_failed",
            "phase": "payload",
            "path": path,
            "json_pointer": pointer,
            "message": message,
            "received": received,
            "expected": expected if expected is not None else field_schema,
            "field_schema": field_schema or {"type": "object"},
            "fix": f"Correct {path} according to field_schema and retry continue_orchestration with unrelated fields unchanged.",
        }
        diagnostics.append(item)

    step = params.get("step")
    if type(step) is not int or step < 1:
        add("step", "must be an integer greater than or equal to 1", received=step, expected={"type": "integer", "minimum": 1})
    results = params.get("results")
    if not isinstance(results, list) or not results:
        return diagnostics
    allowed_result = {"worker", "attempt_result_ref"}
    for index, result in enumerate(results):
        path = f"results[{index}]"
        if not isinstance(result, dict):
            add(path, "must be an object", received=result, expected={"type": "object"})
            continue
        for key in sorted(set(result) - allowed_result):
            add(f"{path}.{key}", "unsupported field", received=result.get(key), expected="one of the advertised result fields")
        if "worker" in result and (type(result["worker"]) is not int or result["worker"] < 1):
            add(f"{path}.worker", "must be an integer greater than or equal to 1", received=result.get("worker"), expected={"type": "integer", "minimum": 1})
        if not isinstance(result.get("attempt_result_ref"), str) or not str(result.get("attempt_result_ref") or "").strip():
            add(
                f"{path}.attempt_result_ref",
                "is required and must be the exact non-empty canonical result reference returned by Cortex",
                received=result.get("attempt_result_ref"),
                expected={"type": "string", "minLength": 1},
            )
    return diagnostics


def _v11_continue_stop(error: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Turn a stale/foreign continuation into a resumable coordinator receipt.

    A coordinator that loses the response after a successful continuation may
    retry the old relative step.  Treating that diagnostic as an ordinary
    correctable input invites a model to repeat ``continue`` (and often to
    invent artifacts or replacement waves).  The active task is already
    server-owned, so this class of error is a server-reconciliation signal:
    the coordinator performs one bounded inspection and follows the durable
    receipt.  It is never a user decision and never authorizes a replacement.
    """
    result = dict(error)
    result["outcome"] = "needs_input"
    result["retryable"] = True
    result["stop_reason"] = reason
    result["next_action"] = (
        "Cortex detected a stale continuation and will reconcile it from the durable ledger. "
        "Do not replay the consumed step/results, supply a replacement pipeline, or spawn a replacement. "
        "If the caller must re-enter the lifecycle, call manage_orchestration exactly once "
        "with intent=inspect for this same task_ref and follow the returned server-owned "
        "receipt or exact task question."
    )
    return result


def _v11_reconcile_stale_continue(params: dict[str, Any], error: dict[str, Any]) -> dict[str, Any]:
    """Reconcile a stale continuation from durable state in one server read.

    A lost/late coordinator receipt is an internal transport race, not a
    caller decision.  The previous facade returned ``needs_input`` and made
    the coordinator issue a second management call before it could see the
    already-issued dispatch.  Rehydrate the current projection here and
    return its server-owned next action directly.  If the projection contains
    a real worker question, the normal question response remains the only
    visible pause; all other states continue through the returned dispatch or
    waiting receipt.
    """
    try:
        resolved = _v11_resolve_task(params, require_task_ref=True)
        if isinstance(resolved, dict):
            return _v11_continue_stop(error, reason="stale_relative_step")
        _, state, _, task_ref = resolved
        common = {
            "project_root": params["project_root"],
            "principal": state.get("principal"),
            "task_id": state["task_id"],
        }
        snapshot = _engine_inspect_lifecycle(common)
        response = _v11_response(snapshot, task_ref, include_result=True)
        response["recovery"] = {
            "mode": "server_reconcile",
            "source": "stale_relative_step",
            "automatic": True,
        }
        response["retryable"] = True
        response["requires_user_decision"] = bool(
            response.get("requires_user_decision")
            or (response.get("user_view") or {}).get("requires_user_decision")
        )
        if not response["requires_user_decision"]:
            response["next_action"] = (
                "Cortex reconciled the stale continuation from its durable ledger. "
                "Invoke only the returned native dispatches, or wait for the exact "
                "persisted workers; do not issue another lifecycle call or replay the old step."
            )
        return response
    except Exception:
        # The original diagnostic remains a retryable internal receipt if a
        # concurrent state mutation prevents the bounded read.  It must still
        # never be classified as a user decision or replacement authorization.
        return _v11_continue_stop(error, reason="stale_relative_step")


def _v11_start_state_blocked_error(message: object) -> dict[str, Any]:
    """Return a resumable start result when no task was safely created.

    A registry instable happens before Cortex can reserve a task or
    return an opaque task reference.  Treating it as ordinary caller input led
    coordinators to call unscoped recovery, which could select an unrelated
    active task under the same project root.
    """
    result = _v11_error("start_state_incompatible", message, outcome="needs_input")
    result["retryable"] = True
    result["task_created"] = False
    result["recovery"] = "user_authorized_ledger_maintenance_required"
    result["next_action"] = (
        "Cortex did not create a task and returned no task_ref. Record this internal "
        "reconciliation diagnostic, retry the same start_orchestration request once "
        "after the server-owned ledger repair, and do not select another task or "
        "dispatch a worker without a successful task_ref. This is not a user-facing block."
    )
    return result


def _v11_start_materialization_pending(task_ref: str) -> dict[str, Any]:
    """Return a durable retry receipt while another caller owns materialization."""
    result = _v11_error(
        "start_materialization_pending",
        "the same transport start is currently being materialized by Cortex",
        outcome="materialization_pending",
    )
    result.update({
        "ok": False,
        "task_ref": task_ref,
        "task_created": False,
        "retryable": True,
        "requires_retry": True,
        "attempt_budget_consumed": False,
        "worker_replacement_authorized": False,
        "replayed": True,
        "recovery": {"mode": "materialization_lease", "automatic": True},
        "next_action": (
            "Cortex is materializing this exact task under its durable transport lease. "
            "Retry the identical start request with the same transport request id after the next receipt; "
            "do not create a new request or select another task."
        ),
    })
    return result


def _v11_release_start_materialization_lease(
    root: Path,
    task_id: str,
    owner: str | None,
) -> None:
    """Make a failed owner retryable without disturbing another owner."""
    if not owner:
        return
    try:
        with state_lock(root):
            registry = _operation_registry(root)
            changed = False
            for reservation in registry.get("starts", {}).values():
                if (
                    isinstance(reservation, dict)
                    and str(reservation.get("task_id") or "") == str(task_id)
                    and str(reservation.get("materialization_owner") or "") == owner
                ):
                    reservation["materialization_status"] = "retryable"
                    reservation.pop("materialization_owner", None)
                    reservation.pop("materialization_lease_expires_at", None)
                    changed = True
            record = registry.get("tasks", {}).get(str(task_id))
            start = record.get("start") if isinstance(record, dict) else None
            if isinstance(start, dict) and str(start.get("materialization_owner") or "") == owner:
                start["materialization_status"] = "retryable"
                start.pop("materialization_owner", None)
                start.pop("materialization_lease_expires_at", None)
                changed = True
            if changed:
                _write_operation_registry(root, registry)
    except Exception:
        # The original start failure is authoritative; cleanup must never
        # replace it with a secondary persistence diagnostic. The in-memory
        # bearer was already removed by the caller before this helper runs.
        return


def _v11_task_ref_required_error(operation: str) -> dict[str, Any]:
    """Refuse project-wide fallback selection for task-scoped public calls."""
    result = _v11_error(
        "task_ref_required",
        f"{operation} requires the exact task_ref returned by a successful Cortex lifecycle response.",
    )
    result["next_action"] = (
        "Do not inspect, list, infer, or select another task from this project root. "
        "Use only the task_ref returned by the task being recovered; if no task_ref was returned, "
        "record the internal diagnostic and retry the originating lifecycle request once so "
        "Cortex can reconcile its durable receipt. Do not turn this technical correction into a user-facing block."
    )
    return result


def _v11_task_ref(task_id: str) -> str:
    return "task-" + digest_text(task_id)[:12]


# v11 worker authority is carried only by an explicit, attempt-scoped bearer
# delivered in the native spawn prompt.  Host sessions, hook receipts,
# environment variables, and process identity are telemetry and never enter
# this verifier.
WORKER_ASSIGNMENT_SCHEMA = "cortex/worker-assignment/v1"
WORKER_ASSIGNMENT_REF_RE = ASSIGNMENT_VALUE_RE
WORKER_ASSIGNMENT_OPERATIONS = frozenset({
    "worker_question",
    "record_attempt_event",
    "complete_attempt",
    "read_dispatch_briefing",
    "read_worker_result",
})


class WorkerAssignmentError(ValueError):
    """Fail-closed worker authorization without an identity oracle."""

    def __init__(self, code: str = "worker_assignment_unavailable") -> None:
        self.code = code
        super().__init__("worker assignment is unavailable; coordinator recovery is required")


def _contains_embedded_assignment_ref(value: object, *, root: bool = True) -> bool:
    """Reject bearer copies in semantic payloads before any durable write."""
    if isinstance(value, str):
        return WORKER_ASSIGNMENT_REF_RE.search(value) is not None
    if isinstance(value, Mapping):
        return any(
            _contains_embedded_assignment_ref(item, root=False)
            for key, item in value.items()
            if not (root and str(key) == "assignment_ref")
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_embedded_assignment_ref(item, root=False) for item in value)
    return False


def _worker_assignment_payload(claim: Mapping[str, Any]) -> bytes:
    """Return the exact HMAC audience for one durable non-secret claim."""
    return json.dumps(
        {
            "schema": WORKER_ASSIGNMENT_SCHEMA,
            "task_id": str(claim.get("task_id") or ""),
            "task_ref": str(claim.get("task_ref") or ""),
            "attempt_id": str(claim.get("attempt_id") or ""),
            "dispatch_ref": str(claim.get("dispatch_ref") or ""),
            "generation": int(claim.get("generation") or 0),
            "profile": str(claim.get("profile") or ""),
            "audience": str(claim.get("audience") or ""),
            "sandbox": str(claim.get("sandbox") or ""),
            "access_digest": str(claim.get("access_digest") or ""),
            "operations": sorted(str(item) for item in (claim.get("operations") or [])),
            "delivery_nonce": str(claim.get("delivery_nonce") or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def worker_assignment_ref(
    project_root: str | Path,
    claim: Mapping[str, Any],
    *,
    create_key: bool = False,
) -> str:
    """Derive the opaque bearer without storing or logging its raw value."""
    root = ledger_root({"project_root": str(project_root)})
    digest = hmac.new(
        _governance_lifecycle_hmac_key(root, create=create_key),
        b"cortex/worker-assignment/v1\0" + _worker_assignment_payload(claim),
        hashlib.sha256,
    ).hexdigest()
    bearer = "assignment-v1-" + digest
    if not create_key:
        verifier = str(claim.get("verifier_sha256") or "")
        observed = hashlib.sha256(bearer.encode("ascii")).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", verifier) or not hmac.compare_digest(verifier, observed):
            raise WorkerAssignmentError("legacy_worker_assignment_quarantined")
    return bearer


def issue_worker_assignment(
    project_root: str | Path,
    *,
    task_id: str,
    attempt_id: str,
    dispatch_ref: str,
    profile: str,
    sandbox: str,
    access: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    """Create one durable non-secret claim and its ephemeral native bearer."""
    canonical_task_id = safe_id(task_id)
    canonical_attempt_id = safe_id(attempt_id)
    canonical_dispatch_ref = safe_id(dispatch_ref)
    canonical_worker_profile = canonical_profile(profile)
    canonical_sandbox = str(sandbox or "").strip()
    access_digest = digest_text(json.dumps(dict(access), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if not canonical_task_id or not canonical_attempt_id or not canonical_dispatch_ref or canonical_worker_profile not in AGENTS or not canonical_sandbox:
        raise ValueError("worker assignment identity is incomplete")
    claim = {
        "schema": WORKER_ASSIGNMENT_SCHEMA,
        "task_id": canonical_task_id,
        "task_ref": _v11_task_ref(canonical_task_id),
        "attempt_id": canonical_attempt_id,
        "dispatch_ref": canonical_dispatch_ref,
        "generation": 1,
        "profile": canonical_worker_profile,
        "audience": "worker",
        "sandbox": canonical_sandbox,
        "access_digest": access_digest,
        "operations": sorted(WORKER_ASSIGNMENT_OPERATIONS),
        # The nonce is not a bearer.  It is safe to persist because the HMAC
        # key lives outside the ledger and is never model-facing.
        "delivery_nonce": secrets.token_hex(32),
        "issued_at": now(),
    }
    bearer = worker_assignment_ref(project_root, claim, create_key=True)
    claim["verifier_sha256"] = hashlib.sha256(bearer.encode("ascii")).hexdigest()
    return claim, bearer


def authorize_worker_assignment(
    params: Mapping[str, Any],
    operation: str,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any], str]:
    """Resolve explicit refs to exactly one current v11 attempt, read-only.

    A legacy attempt without a v11 claim is quarantined by construction: it
    cannot be selected by task singleton, host session, hook arrival, or
    environment identity and must be replaced through coordinator recovery.
    """
    task_ref = str(params.get("task_ref") or "").strip()
    supplied = str(params.get("assignment_ref") or "").strip()
    if operation not in WORKER_ASSIGNMENT_OPERATIONS:
        raise WorkerAssignmentError()
    if not re.fullmatch(r"task-[0-9a-f]{12}", task_ref) or not WORKER_ASSIGNMENT_REF_RE.fullmatch(supplied):
        raise WorkerAssignmentError()
    if _contains_embedded_assignment_ref(params):
        raise WorkerAssignmentError("worker_assignment_exposure_rejected")
    try:
        bound = _bind_task_project_root({"task_ref": task_ref}, include_completed=True)
        if not isinstance(bound, dict):
            raise WorkerAssignmentError()
        resolved = _v11_resolve_task(bound, require_task_ref=True)
        if isinstance(resolved, dict):
            raise WorkerAssignmentError()
        task_dir, state, task, resolved_ref = resolved
        project = select_project_root({"project_root": str(task.get("project_root") or "")})
        if resolved_ref != task_ref:
            raise WorkerAssignmentError()
        matches: list[dict[str, Any]] = []
        for candidate in state.get("attempts") or []:
            if not isinstance(candidate, dict):
                continue
            claim = candidate.get("worker_assignment")
            if not isinstance(claim, dict) or claim.get("schema") != WORKER_ASSIGNMENT_SCHEMA:
                continue
            if (
                str(claim.get("task_id") or "") != str(state.get("task_id") or "")
                or str(claim.get("task_ref") or "") != task_ref
                or str(claim.get("attempt_id") or "") != str(candidate.get("attempt_id") or "")
                or str(claim.get("dispatch_ref") or "") != str(candidate.get("dispatch_ref") or "")
                or int(claim.get("generation") or 0) != 1
                or str(claim.get("profile") or "") != str(candidate.get("profile") or candidate.get("agent") or "")
                or str(claim.get("audience") or "") != "worker"
                or str(claim.get("sandbox") or "") != str((candidate.get("spawn_request") or {}).get("sandbox") or "")
                or str(claim.get("access_digest") or "") != digest_text(json.dumps({
                    "allowed_paths": candidate.get("allowed_paths") or [],
                    "route_category": (candidate.get("spawn_request") or {}).get("route_category"),
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                or operation not in set(str(item) for item in (claim.get("operations") or []))
            ):
                continue
            expected = worker_assignment_ref(project, claim, create_key=False)
            if hmac.compare_digest(expected, supplied):
                matches.append(candidate)
        if len(matches) != 1:
            raise WorkerAssignmentError("legacy_worker_assignment_quarantined")
        attempt = matches[0]
        if attempt.get("invalidated"):
            raise WorkerAssignmentError()
        profile = canonical_profile(str(attempt.get("profile") or attempt.get("agent") or ""))
        if profile not in AGENTS:
            raise WorkerAssignmentError()
        return Path(project), task_dir, state, attempt, profile
    except WorkerAssignmentError:
        raise
    except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error) as exc:
        raise WorkerAssignmentError() from exc


# A native child's final text is model-authored and therefore cannot be task
# failure authority.  The only authority accepted by finalize_worker_failure
# is this private, single-use control record, created after an authenticated
# assignment-bound MCP operation has itself produced a closed nonretryable
# terminal response.  ``recovery.state_mutated=false`` continues to describe
# the rejected domain operation: this audit/control evidence does not apply
# that operation's requested state change or consume its attempt budget.
TERMINAL_FAILURE_EVIDENCE_SCHEMA = "cortex/terminal-failure-evidence/v1"
TERMINAL_FAILURE_EVIDENCE_KEY = "terminal_failure_evidence"
TERMINAL_FAILURE_EVIDENCE_TTL_SECONDS = 60 * 60
TERMINAL_FAILURE_ACTION = {
    "evidence": "server_bound",
    "coordinator_intent": "finalize_worker_failure",
    "reason_code": "worker_nonretryable_terminal",
}
TERMINAL_FAILURE_CODES_BY_OPERATION: dict[str, frozenset[str]] = {
    "worker_question": frozenset({
        "worker_question_reference_mismatch",
        "worker_question_unavailable",
    }),
    "record_attempt_event": frozenset({"record_attempt_event_closed"}),
    "complete_attempt": frozenset({"complete_attempt_repair_rejected"}),
    "read_dispatch_briefing": frozenset({
        "dispatch_briefing_response_invalid",
        "dispatch_briefing_unavailable",
    }),
    "read_worker_result": frozenset({"read_worker_result_not_authorized"}),
}
TERMINAL_FAILURE_CATEGORIES = frozenset({"authority", "integrity", "stale", "unavailable"})
_WORKER_RESPONSE_FAMILY = {
    "worker_question": "worker.question",
    "record_attempt_event": "worker.event",
    "complete_attempt": "worker.completion",
    "read_dispatch_briefing": "worker.briefing",
    "read_worker_result": "result.read",
}


def _terminal_failure_fact(
    operation: str,
    response: Mapping[str, Any],
) -> tuple[str, str] | None:
    """Return one allowlisted server terminal classification, never prose."""
    if response.get("ok") is not False:
        return None
    recovery = response.get("recovery")
    error = response.get("error")
    if not isinstance(recovery, Mapping) or not isinstance(error, Mapping):
        return None
    if (
        recovery.get("kind") != "terminal_stop"
        or recovery.get("retryable") is not False
        or recovery.get("state_mutated") is not False
        or str(recovery.get("operation") or "") != operation
    ):
        return None
    category = str(error.get("category") or "")
    code = str(error.get("code") or "")
    if category not in TERMINAL_FAILURE_CATEGORIES:
        return None
    if code not in TERMINAL_FAILURE_CODES_BY_OPERATION.get(operation, frozenset()):
        return None
    return category, code


def _with_terminal_failure_evidence(
    operation: str,
    params: dict[str, Any],
    response: dict[str, Any],
    *,
    authority: tuple[Path, Path, dict[str, Any], dict[str, Any], str] | None = None,
) -> dict[str, Any]:
    """Persist or clear the current private terminal control evidence.

    There is deliberately no receipt identifier in the public response.  The
    coordinator already has the original dispatch reference, while Cortex can
    resolve the exact task/attempt/generation from the authenticated worker
    call and later compare that binding under the task mutation lock.
    """
    if operation not in _WORKER_RESPONSE_FAMILY or not isinstance(response, dict):
        return response
    fact = _terminal_failure_fact(operation, response)
    try:
        if authority is None:
            authority = authorize_worker_assignment(params, operation)
        project, task_dir, state, attempt, _profile = authority
        root = ledger_root({"project_root": str(project)})
        task_id = str(state.get("task_id") or "")
        attempt_id = str(attempt.get("attempt_id") or "")
        dispatch_ref = str(attempt.get("dispatch_ref") or "")
        claim = attempt.get("worker_assignment") if isinstance(attempt.get("worker_assignment"), dict) else {}
        generation = int(claim.get("generation") or 0)
        with state_lock(root, operation="terminal_failure_evidence", task_id=task_id):
            fresh = _v11_task_state(root, task_id)
            if fresh is None or fresh[0] != task_dir:
                return response
            _fresh_dir, fresh_state, _fresh_task = fresh
            matches = [
                item for item in fresh_state.get("attempts") or []
                if isinstance(item, dict) and str(item.get("attempt_id") or "") == attempt_id
            ]
            if len(matches) != 1:
                return response
            current = matches[0]
            current_claim = (
                current.get("worker_assignment")
                if isinstance(current.get("worker_assignment"), dict) else {}
            )
            current_binding = (
                str(current.get("dispatch_ref") or "") == dispatch_ref
                and int(current_claim.get("generation") or 0) == generation
                and not current.get("invalidated")
                and str(current.get("gate") or "") in set(active_gates(fresh_state))
            )
            existing_evidence = db_get_task_document(
                root, task_id, TERMINAL_FAILURE_EVIDENCE_KEY,
            )
            if _terminal_failure_evidence_expired(existing_evidence):
                db_delete_task_document(root, task_id, TERMINAL_FAILURE_EVIDENCE_KEY)
            if fact is None:
                # A later authenticated current worker response (including a
                # successful completion or a retryable repair) makes any
                # older terminal observation stale. This also cleans an old
                # attempt's record when a replacement generation starts.
                if current_binding:
                    db_delete_task_document(root, task_id, TERMINAL_FAILURE_EVIDENCE_KEY)
                return response
            if (
                not current_binding
                or fresh_state.get("status") != "active"
                or current.get("status") not in {AWAITING_HOST_SPAWN, "running", "waiting_question"}
                or current.get("assignment_delivery_status") != "delivered"
                or current.get("attempt_result_ref")
                or attempt_protocol.get_attempt_result(root, task_id=task_id, attempt_id=attempt_id) is not None
            ):
                return response
            issued_at = now()
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=TERMINAL_FAILURE_EVIDENCE_TTL_SECONDS)
            ).isoformat()
            category, code = fact
            db_put_task_document(root, task_id, TERMINAL_FAILURE_EVIDENCE_KEY, {
                "schema": TERMINAL_FAILURE_EVIDENCE_SCHEMA,
                "task_id": task_id,
                "attempt_id": attempt_id,
                "dispatch_ref": dispatch_ref,
                "assignment_generation": generation,
                "operation": operation,
                "error_category": category,
                "error_code": code,
                "issued_at": issued_at,
                "expires_at": expires_at,
                "updated_at": issued_at,
            })
    except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error):
        # A terminal response without durable evidence remains a terminal stop
        # for the worker, but must not advertise coordinator finalization.
        return response

    updated = json.loads(json.dumps(response, ensure_ascii=False))
    recovery = updated.get("recovery")
    if isinstance(recovery, dict):
        recovery["terminal_failure"] = dict(TERMINAL_FAILURE_ACTION)
    return validate_v11_response(_WORKER_RESPONSE_FAMILY[operation], updated)


class OperationRegistryError(ValueError):
    """The project-wide replay registry cannot safely serve lifecycle calls."""


COORDINATOR_CAPABILITY_RE = re.compile(COORDINATOR_REF_PATTERN)
# A coordinator bearer is deliberately a short-lived verifier for a *single*
# active task.  It is not a project administration credential merely because
# it was returned by a project-local orchestration call.  Keep the claim
# vocabulary local to this public-facade boundary: workers never receive it
# and the governance domain is not asked to trust caller-authored roles.
COORDINATOR_CAPABILITY_CLAIMS_SCHEMA = "cortex/coordinator-capability/v11"
COORDINATOR_CAPABILITY_TTL_SECONDS = 8 * 60 * 60
# Keep the ownership window longer than a normal engine start so concurrent
# retries cannot take over while the original caller is still materializing;
# an interrupted caller becomes recoverable after this bounded lease.
START_MATERIALIZATION_LEASE_SECONDS = 5 * 60
TASK_COORDINATOR_CAPABILITY_ACTIONS = frozenset({
    "inspect_initiative", "list_records", "snapshot", "link_task",
    "add_dependency", "transition_initiative", "create_record",
    "evaluate_promotion", "promotion_inspect",
})


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
    return registry


def _write_operation_registry(root: Path, registry: dict[str, Any]) -> None:
    """Persist the current operation registry contract."""
    registry["updated_at"] = now()
    db_put_global(root, "operation_registry", registry)


_PENDING_COORDINATOR_CAPABILITIES: dict[tuple[str, str], str] = {}
_PENDING_COORDINATOR_CAPABILITIES_LOCK = threading.Lock()


def _coordinator_capability_digest(capability: str) -> str:
    return hashlib.sha256(str(capability).encode("ascii")).hexdigest()


def _pending_coordinator_capability_key(root: Path, task_id: str) -> tuple[str, str]:
    return str(root.resolve()), str(task_id)


def _coordinator_capability_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=COORDINATOR_CAPABILITY_TTL_SECONDS)).isoformat()


def _capability_action(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _capability_claims(
    *,
    task_id: str,
    initiative_ref: str | None,
    allowed_actions: frozenset[str] | None = None,
    generation: int = 1,
) -> dict[str, Any]:
    """Return durable, non-secret metadata for one coordinator bearer.

    The bearer itself is intentionally excluded.  A SHA-256 verifier is kept
    beside this claim only in the start receipt, never in the claim or audit
    history, so diagnostics and normal registry reads cannot become a bearer
    recovery channel.
    """
    selected_actions = allowed_actions or TASK_COORDINATOR_CAPABILITY_ACTIONS
    issued_at = now()
    return {
        "schema": COORDINATOR_CAPABILITY_CLAIMS_SCHEMA,
        "kind": "task",
        "audience": "coordinator",
        "task_id": str(task_id),
        "task_ref": _v11_task_ref(str(task_id)),
        "initiative_ref": str(initiative_ref or "") or None,
        "allowed_actions": sorted(selected_actions),
        "generation": int(generation),
        "issued_at": issued_at,
        "expires_at": _coordinator_capability_expiry(),
        "revoked_generations": [],
        "rotation_audit": [],
    }


def _coordinator_claims_mac(
    root: Path,
    claims: Mapping[str, Any],
    *,
    create_key: bool = False,
) -> str:
    """Bind every authority-bearing coordinator claim to the host-private key."""
    payload = json.dumps(
        dict(claims), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        _governance_lifecycle_hmac_key(root, create=create_key),
        b"cortex/coordinator-capability-claims/v1\0" + payload,
        hashlib.sha256,
    ).hexdigest()


def _coordinator_claims_match(
    root: Path,
    claims: object,
    verifier: object,
) -> bool:
    if not isinstance(claims, Mapping):
        return False
    supplied = str(verifier or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", supplied):
        return False
    try:
        expected = _coordinator_claims_mac(root, claims)
    except (ValueError, OSError, RuntimeError):
        return False
    return hmac.compare_digest(supplied, expected)


def _stage_coordinator_capability(root: Path, task_id: str) -> tuple[str, str]:
    """Create a one-response bearer and durable one-way verifier.

    The raw bearer lives in this process only long enough for the successful
    start response. A lost or cross-process response fails closed; no ambient
    runtime recovery credential is created or retained.
    """
    capability = secrets.token_hex(32)
    digest = _coordinator_capability_digest(capability)
    with _PENDING_COORDINATOR_CAPABILITIES_LOCK:
        _PENDING_COORDINATOR_CAPABILITIES[_pending_coordinator_capability_key(root, task_id)] = capability
    return capability, digest


def _start_materialization_lease_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=START_MATERIALIZATION_LEASE_SECONDS)).isoformat()


def _start_materialization_lease_active(value: object) -> bool:
    try:
        return parse_expiry(value, "materialization_lease_expires_at") > datetime.now(timezone.utc)
    except ValueError:
        return False


def _v11_materialization_fence(
    root: Path,
    task_id: str,
    owner: str | None,
    generation: int | None,
) -> bool:
    """Check the current owner/generation before engine durable mutations."""
    if not owner or not isinstance(generation, int):
        return False
    registry = _operation_registry(root)
    reservations = registry.get("starts", {})
    records = [
        value for value in reservations.values()
        if isinstance(value, dict) and str(value.get("task_id") or "") == str(task_id)
    ]
    if not records:
        record = registry.get("tasks", {}).get(str(task_id))
        start = record.get("start") if isinstance(record, dict) else None
        records = [start] if isinstance(start, dict) else []
    return any(
        str(item.get("materialization_status") or "") == "reserved"
        and str(item.get("materialization_owner") or "") == owner
        and item.get("materialization_generation") == generation
        and _start_materialization_lease_active(item.get("materialization_lease_expires_at"))
        for item in records
    )


def _take_coordinator_capability(root: Path, task_id: str) -> str | None:
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
    claims_mac = start.get("coordinator_capability_claims_mac") if isinstance(start, dict) else None
    if (
        not _valid_coordinator_capability_claims(claims, task_id=task_id)
        or not _coordinator_claims_match(root, claims, claims_mac)
    ):
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
    if claims.get("kind") != "task":
        return False
    if claims.get("audience") != "coordinator" or str(claims.get("task_ref") or "") != _v11_task_ref(str(task_id)):
        return False
    if str(claims.get("task_id") or "") != str(task_id):
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
    claims_mac = start.get("coordinator_capability_claims_mac") if isinstance(start, dict) else None
    if (
        not _valid_coordinator_capability_claims(claims, task_id=str(task_id))
        or not _coordinator_claims_match(root, claims, claims_mac)
    ):
        return None
    return dict(claims)


def authorize_coordinator_ref(
    params: Mapping[str, Any], operation: str,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any], str]:
    """Resolve one explicit coordinator bearer without ambient identity."""
    task_ref = str(params.get("task_ref") or "").strip()
    coordinator_ref = str(params.get("coordinator_ref") or "").strip().lower()
    if operation not in {"continue_orchestration", "manage_orchestration", "manage_governance", "read_worker_result"}:
        raise ValueError("coordinator_ref operation is unsupported")
    if not re.fullmatch(r"task-[0-9a-f]{12}", task_ref) or not COORDINATOR_CAPABILITY_RE.fullmatch(coordinator_ref):
        raise ValueError("coordinator authorization is unavailable")
    bound = _bind_task_project_root({"task_ref": task_ref}, include_completed=True)
    if not isinstance(bound, dict):
        raise ValueError("coordinator authorization is unavailable")
    resolved = _v11_resolve_task(bound, require_task_ref=True)
    if isinstance(resolved, dict):
        raise ValueError("coordinator authorization is unavailable")
    task_dir, state, task, resolved_ref = resolved
    project = select_project_root({"project_root": str(task.get("project_root") or "")})
    root = ledger_root({"project_root": str(project)})
    if resolved_ref != task_ref or not _coordinator_capability_matches(root, state["task_id"], coordinator_ref):
        raise ValueError("coordinator authorization is unavailable")
    claims = _coordinator_capability_claims_for_task(root, state["task_id"])
    if not isinstance(claims, dict) or claims.get("audience") != "coordinator":
        raise ValueError("coordinator authorization is unavailable")
    return Path(project), task_dir, state, task, task_ref


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
            changed = True
        if changed:
            _write_operation_registry(root, registry)


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
    if action not in allowed_actions:
        raise GovernanceError(
            "coordinator capability is not authorized for this governance action",
            code="coordinator_capability_action_denied",
        )
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
    if action == "create_record" and str(payload.get("record_type") or "").strip().lower() in {
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
            "list_records", "snapshot", "create_record",
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
    if action == "link_task" and supplied_task != task_id:
        raise GovernanceError(
            "task link must name the capability's task",
            code="coordinator_capability_scope_denied",
        )
    if action == "add_dependency":
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
                        active.append(_v11_task_ref(task_id))
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
            "pruned_task_refs": [_v11_task_ref(task_id) for task_id in sorted(stale_ids)],
            "pruned_statuses": {status: list(statuses.values()).count(status) for status in sorted(set(statuses.values()))},
            "removed_operations": finalized["removed_operations"],
            "removed_classification_receipts": finalized["removed_classifications"],
            "removed_manifest_snapshots": finalized["removed_manifest_snapshots"],
            "updated_lanes": lane_updates,
            "retained_count": len(task_index) - len(stale_ids),
            "retained_nonterminal_count": retained_nonterminal_count,
            "next_action": "Prune completed; active/blocked tasks, recent tasks, and all project source/documentation were preserved.",
        }


def _v11_task_state(root: Path, task_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]] | None:
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


def _v11_task_candidates(params: dict[str, Any], *, include_completed: bool = False) -> list[dict[str, Any]]:
    root = ledger_root(params)
    candidates: list[dict[str, Any]] = []
    for task_id in sorted(read_task_index(root)):
        loaded = _v11_task_state(root, task_id)
        if loaded is None:
            continue
        _, state, task = loaded
        if not include_completed and state.get("status") not in {"active", "blocked", "needs_input"}:
            continue
        candidates.append({
            "task_id": task_id,
            "task_ref": _v11_task_ref(task_id),
            "user_request": redact(task.get("user_request", ""), 300),
            "status": state.get("status"),
            "created_at": task.get("created_at"),
        })
    return candidates


def _v11_resolve_task(
    params: dict[str, Any],
    *,
    include_completed: bool = False,
    require_task_ref: bool = True,
) -> tuple[Path, dict[str, Any], dict[str, Any], str] | dict[str, Any]:
    root = ledger_root(params)
    candidates = _v11_task_candidates(params, include_completed=include_completed)
    requested = str(params.get("task_ref") or "").strip()
    if require_task_ref and not requested:
        return _v11_task_ref_required_error("task-scoped Cortex call")
    if requested:
        selected = next((item for item in candidates if item["task_ref"] == requested), None)
        if selected is None:
            return _v11_error("unknown_task_ref", "task_ref does not identify a selectable Cortex task")
    elif not candidates:
        return _v11_error("no_active_task", "No active Cortex task exists in this project root.")
    else:
        return _v11_error(
            "task_ref_required",
            "task-scoped Cortex calls require the exact task_ref returned by start_orchestration.",
        )
    loaded = _v11_task_state(root, str(selected["task_id"]))
    if loaded is None:
        return _v11_error("task_unavailable", "The selected Cortex task is unavailable.")
    task_dir, state, task = loaded
    return task_dir, state, task, str(selected["task_ref"])


def _v11_complexity(value: object) -> str:
    raw = "C2" if value in {None, ""} else value
    if isinstance(raw, str) and raw in CANONICAL_COMPLEXITIES:
        return raw
    suggestions = difflib.get_close_matches(str(raw), sorted(CANONICAL_COMPLEXITIES), n=3)
    suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
    raise ValueError("task.complexity must be one of the canonical values C1, C2, C3" + suffix)


def _v11_plan_approval(value: object, complexity: str) -> str:
    """Normalize the post-plan review policy selected for a public task.

    Governance complexity is an assessment and must never silently turn into
    a user-interaction requirement.  A plan review is therefore automatic by
    default for every complexity; only an explicit ``plan_approval=required``
    request opts the task into a user approval step.
    """
    if value in {None, ""}:
        del complexity
        return "auto"
    raw = value
    if isinstance(raw, str) and raw in CANONICAL_PLAN_APPROVAL_POLICIES:
        return raw
    suggestions = difflib.get_close_matches(str(raw), sorted(CANONICAL_PLAN_APPROVAL_POLICIES), n=3)
    suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
    raise ValueError("task.plan_approval must be auto or required" + suffix)


def _v11_model(value: object) -> str | None:
    if value in {None, ""}:
        return None
    model = value
    if isinstance(model, str) and model in SUPPORTED_MODELS:
        return model
    suggestions = difflib.get_close_matches(str(model), sorted(SUPPORTED_MODELS), n=3)
    suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
    raise ValueError("worker model must use a canonical supported model identifier" + suffix)


def _v11_start_public_schema_forms() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any],
]:
    """Return the generated public authority for start/task/wave forms."""
    registry = globals().get("PUBLIC_SCHEMA_REGISTRY")
    start_schema = registry.get("start_orchestration") if isinstance(registry, dict) else None
    start_properties = start_schema.get("properties") if isinstance(start_schema, dict) else None
    task_schema = start_properties.get("task") if isinstance(start_properties, dict) else None
    waves_schema = start_properties.get("waves") if isinstance(start_properties, dict) else None
    wave_schema = waves_schema.get("items") if isinstance(waves_schema, dict) else None
    wave_properties = wave_schema.get("properties") if isinstance(wave_schema, dict) else None
    workers_schema = wave_properties.get("workers") if isinstance(wave_properties, dict) else None
    worker_schema = workers_schema.get("items") if isinstance(workers_schema, dict) else None
    task_properties = task_schema.get("properties") if isinstance(task_schema, dict) else None
    worker_properties = worker_schema.get("properties") if isinstance(worker_schema, dict) else None
    if not all(isinstance(value, dict) for value in (
        start_schema, start_properties, task_schema, task_properties, waves_schema,
        wave_schema, wave_properties, workers_schema, worker_schema, worker_properties,
    )):
        raise RuntimeError("public start schema forms are incomplete")
    return start_schema, task_schema, waves_schema, wave_schema, worker_schema


def _v11_schema_field_card(schema: Mapping[str, Any], *facets: str) -> dict[str, Any]:
    """Select a minimal diagnostic card from one generated public field."""
    return {facet: schema[facet] for facet in facets if facet in schema}


def _v11_schema_object_card(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return the public object-card used for unsupported nested fields."""
    properties = schema.get("properties")
    return {
        "type": schema.get("type", "object"),
        "additionalProperties": schema.get("additionalProperties", True),
        "properties": sorted(properties) if isinstance(properties, dict) else [],
    }


def _v11_start_task_preflight(raw_task: object) -> list[dict[str, Any]]:
    """Validate the public task form from the same registry as MCP tools/list."""
    diagnostics: list[dict[str, Any]] = []
    _start_schema, task_schema, _waves_schema, _wave_schema, _worker_schema = _v11_start_public_schema_forms()
    task_properties = task_schema["properties"]

    def add(pointer: str, message: str, field_schema: Mapping[str, Any], received: object = None) -> None:
        card = dict(field_schema)
        item: dict[str, Any] = {
            "code": "start_orchestration_validation_failed",
            "phase": "preflight",
            "path": pointer.lstrip("/").replace("/", "."),
            "json_pointer": pointer,
            "message": message,
            "field_schema": card,
            "expected": card,
            "fix": "Correct this exact start_orchestration field and preserve every unrelated valid field.",
        }
        if received is not None:
            item["received"] = received
        diagnostics.append(item)

    if not isinstance(raw_task, dict):
        add("/task", "must be an object", _v11_schema_object_card(task_schema), {"type": type(raw_task).__name__})
        return diagnostics

    # Schema-derived scalar and nested collection validation is deliberately
    # first so e.g. a malformed boolean, enum, and text list are repairable
    # in one request rather than leaking through to normalization one at a
    # time.  Required/route rules remain below because they depend on anyOf.
    diagnostics.extend(_v11_schema_value_diagnostics(
        raw_task, task_schema, pointer="/task", operation="start_orchestration",
    ))

    user_request_schema = task_properties["user_request"]
    if "user_request" not in raw_task or (isinstance(raw_task.get("user_request"), str) and not raw_task["user_request"].strip()):
        add("/task/user_request", "is required", _v11_schema_field_card(user_request_schema, "type", "minLength"))

    # The ordinary-task branch is encoded in the first public anyOf form;
    # harvest remains the only alternate branch and is evaluated separately.
    ordinary_required = next(
        (
            branch.get("required", [])
            for branch in task_schema.get("anyOf", [])
            if isinstance(branch, dict) and isinstance(branch.get("required"), list)
        ),
        [],
    )
    if not _is_knowledge_harvest_task(raw_task):
        missing = [
            field for field in ordinary_required
            if raw_task.get(field) is None or (isinstance(raw_task.get(field), list) and not raw_task.get(field))
        ]
        if missing:
            missing_text = ", ".join(f"task.{field}" for field in missing)
            for field in missing:
                field_schema = task_properties[field]
                value = raw_task.get(field)
                add(
                    f"/task/{field}",
                    f"{missing_text} are required and must contain at least one non-empty string",
                    _v11_schema_field_card(field_schema, "type", "minItems", "items"),
                    {"type": "null"} if value is None else {"type": "array", "length": 0},
                )
    return diagnostics


def _v11_start_wave_preflight(raw_waves: object) -> list[dict[str, Any]]:
    """Validate model-facing wave structure before opening or reserving state."""
    diagnostics: list[dict[str, Any]] = []
    _start_schema, _task_schema, waves_schema, wave_schema, worker_schema = _v11_start_public_schema_forms()
    wave_properties = wave_schema["properties"]
    workers_schema = wave_properties["workers"]
    worker_properties = worker_schema["properties"]
    allowed_paths_schema = worker_properties["allowed_paths"]
    allowed_path_item_schema = allowed_paths_schema["items"]
    allowed_path_pattern = str(allowed_path_item_schema["pattern"])
    waves_field_card = _v11_schema_field_card(waves_schema, "type", "minItems")
    workers_field_card = _v11_schema_field_card(workers_schema, "type", "minItems", "maxItems")
    allowed_paths_field_card = _v11_schema_field_card(allowed_paths_schema, "type", "minItems")
    allowed_path_item_card = _v11_schema_field_card(
        allowed_path_item_schema, "type", "minLength", "pattern", "format",
    )

    def add(pointer: str, message: str, field_schema: dict[str, Any], received: object = None) -> None:
        item: dict[str, Any] = {
            "code": "start_orchestration_validation_failed",
            "phase": "preflight",
            "path": pointer.lstrip("/").replace("/", "."),
            "json_pointer": pointer,
            "message": message,
            "field_schema": field_schema,
            "expected": field_schema,
            "fix": "Correct this exact start_orchestration field and preserve every unrelated valid field.",
        }
        if received is not None:
            item["received"] = received
        diagnostics.append(item)

    if raw_waves is None:
        return diagnostics
    # Validate every declared public leaf (phase/profile/model/dependencies,
    # boolean flags, and path constraints) from the same tools/list schema.
    # The compiler below adds only cross-wave semantic rules.
    diagnostics.extend(_v11_schema_value_diagnostics(
        raw_waves, waves_schema, pointer="/waves", operation="start_orchestration",
    ))
    if not isinstance(raw_waves, list):
        add("/waves", "must be an array", waves_field_card, {"type": type(raw_waves).__name__})
        return diagnostics
    if not raw_waves:
        add("/waves", "must contain at least one wave", waves_field_card, {"type": "array", "length": 0})
        return diagnostics
    for wave_index, wave in enumerate(raw_waves):
        wave_pointer = f"/waves/{wave_index}"
        if not isinstance(wave, dict):
            add(wave_pointer, "must be an object", _v11_schema_object_card(wave_schema), {"type": type(wave).__name__})
            continue
        for key in sorted(set(wave) - set(wave_properties)):
            field_pointer = f"{wave_pointer}/{key}"
            if not any(item.get("json_pointer") == field_pointer for item in diagnostics):
                add(
                    field_pointer,
                    "unsupported wave field",
                    _v11_schema_object_card(wave_schema),
                )
        workers = wave.get("workers")
        workers_pointer = f"{wave_pointer}/workers"
        if not isinstance(workers, list):
            add(workers_pointer, "must be an array", workers_field_card, {"type": type(workers).__name__})
            continue
        if not workers:
            add(workers_pointer, "must contain at least one worker", workers_field_card, {"type": "array", "length": 0})
            continue
        for worker_index, worker in enumerate(workers):
            worker_pointer = f"{workers_pointer}/{worker_index}"
            if not isinstance(worker, dict):
                add(worker_pointer, "must be an object", _v11_schema_object_card(worker_schema), {"type": type(worker).__name__})
                continue
            for key in sorted(set(worker) - set(worker_properties)):
                field_pointer = f"{worker_pointer}/{key}"
                if not any(item.get("json_pointer") == field_pointer for item in diagnostics):
                    add(
                        field_pointer,
                        "unsupported worker field",
                        _v11_schema_object_card(worker_schema),
                    )
            if "allowed_paths" not in worker:
                continue
            allowed_paths = worker.get("allowed_paths")
            allowed_pointer = f"{worker_pointer}/allowed_paths"
            if not isinstance(allowed_paths, list):
                add(allowed_pointer, "must be an array", allowed_paths_field_card, {"type": type(allowed_paths).__name__})
                continue
            if not allowed_paths:
                add(allowed_pointer, "must contain at least one project-relative path", allowed_paths_field_card, {"type": "array", "length": 0})
                continue
            for path_index, value in enumerate(allowed_paths):
                path_pointer = f"{allowed_pointer}/{path_index}"
                if not isinstance(value, str) or re.fullmatch(allowed_path_pattern, value) is None:
                    add(
                        path_pointer,
                        "must be a safe non-empty project-relative path without traversal or wildcards",
                        allowed_path_item_card,
                        {"type": type(value).__name__},
                    )
    return diagnostics


def _v11_compact_waves(
    raw_waves: object,
    task: dict[str, Any],
    *,
    completed_gates: set[str] | None = None,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    # Validate the whole compact wave envelope before the mutating compiler
    # starts.  The compiler below intentionally remains strict, but this pass
    # prevents the caller from discovering one bad phase/profile/dependency per
    # retry.  All diagnostics are independent and no ledger state is touched.
    wave_diagnostics: list[dict[str, Any]] = []
    _start_schema, _task_schema, _waves_schema, wave_schema, worker_schema = _v11_start_public_schema_forms()
    wave_public_fields = set(wave_schema.get("properties") or {})
    worker_public_fields = set(worker_schema.get("properties") or {})

    def wave_diag(
        path: str,
        message: str,
        expected: str | None = None,
        *,
        received: Any = None,
        field_schema: dict[str, Any] | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "code": "management_failed",
            "phase": "payload",
            "path": path,
            "json_pointer": "/" + "/".join(
                part.replace("~", "~0").replace("/", "~1")
                for part in path.replace("[", ".").replace("]", "").split(".") if part
            ),
            "message": redact(message, 1000),
            "fix": "Correct this field in the same manage_orchestration request; do not resend unrelated fields.",
        }
        if expected:
            item["expected"] = redact(expected, 1000)
        if received is not None:
            item["received"] = received
        if field_schema is not None:
            item["field_schema"] = field_schema
        wave_diagnostics.append(item)

    if not isinstance(raw_waves, list) or not raw_waves:
        raise ValueError("waves must be a non-empty array when supplied")
    seen_phases: dict[str, tuple[int, str]] = {}
    available_context_gates = set(completed_gates or set())
    for wave_index, raw_wave in enumerate(raw_waves, 1):
        wave_path = f"waves[{wave_index - 1}]"
        if not isinstance(raw_wave, dict):
            wave_diag(wave_path, "wave must be an object", "{phase: ..., workers: [...]}")
            continue
        for key in sorted(set(raw_wave) - wave_public_fields):
            wave_diag(
                f"{wave_path}.{key}", "unsupported wave field",
                "a wave containing only phase and workers", received=raw_wave.get(key),
            )
        raw_phase = str(raw_wave.get("phase") or "").strip()
        if not raw_phase:
            wave_diag(
                f"{wave_path}.phase", "phase is required on the wave",
                f"one of: {', '.join(sorted(AVAILABLE_GATES))}",
                field_schema={"type": "string", "enum": sorted(AVAILABLE_GATES)},
            )
            continue
        if raw_phase not in AVAILABLE_GATES:
            wave_diag(
                f"{wave_path}.phase", f"unknown wave phase {raw_phase!r}",
                f"one canonical phase: {', '.join(sorted(AVAILABLE_GATES))}",
                received=raw_phase,
                field_schema={"type": "string", "enum": sorted(AVAILABLE_GATES)},
            )
            continue
        gate = canonical_pipeline_gate(raw_phase)
        previous = seen_phases.get(gate)
        if previous is not None and previous[0] != wave_index:
            wave_diag(
                f"{wave_path}.phase",
                f"waves repeat phase {gate!r}: {previous[1]!r} and {raw_phase!r}",
                "put multiple owners of one phase in the same wave",
                received=raw_phase,
                field_schema={"type": "string", "enum": sorted(AVAILABLE_GATES)},
            )
        seen_phases.setdefault(gate, (wave_index, raw_phase))
        workers = raw_wave.get("workers")
        if not isinstance(workers, list) or not workers or len(workers) > 32:
            wave_diag(f"{wave_path}.workers", "workers must contain 1..32 worker objects", "an array of 1..32 worker objects")
            continue
        for worker_index, worker in enumerate(workers, 1):
            worker_path = f"{wave_path}.workers[{worker_index - 1}]"
            if not isinstance(worker, dict):
                wave_diag(worker_path, "worker must be an object", "{profile, objective, ...}")
                continue
            unsupported_worker_fields = sorted(set(worker) - worker_public_fields)
            for key in unsupported_worker_fields:
                wave_diag(
                    f"{worker_path}.{key}", "unsupported worker field",
                    "use only fields published for a worker; phase belongs on the containing wave",
                    received=worker.get(key),
                )
            raw_profile = str(worker.get("profile") or "").strip()
            if raw_profile:
                profile = raw_profile
                if profile not in AGENTS:
                    suggestions = difflib.get_close_matches(profile, sorted(AGENTS), n=3)
                    wave_diag(
                        f"{worker_path}.profile",
                        f"unknown worker profile {raw_profile!r}" + (f"; try {', '.join(suggestions)}" if suggestions else ""),
                        "a supported Cortex worker profile",
                        received=raw_profile,
                        field_schema={"type": "string", "enum": sorted(AGENTS)},
                    )
                elif not profile_can_own_gate(profile, gate):
                    allowed_profiles = sorted(
                        candidate for candidate in AGENTS
                        if profile_can_own_gate(candidate, gate)
                    )
                    wave_diag(
                        f"{worker_path}.profile",
                        f"worker profile {profile!r} cannot own phase {gate!r}",
                        f"one exact profile allowed for phase {gate!r}: {', '.join(allowed_profiles)}",
                        received=raw_profile,
                        field_schema={"type": "string", "enum": allowed_profiles},
                    )
            for model_key in ("model", "user_requested_model"):
                if model_key in worker:
                    raw_model = worker.get(model_key)
                    if not isinstance(raw_model, str) or raw_model.strip() not in SUPPORTED_MODELS:
                        wave_diag(
                            f"{worker_path}.{model_key}",
                            "model must use a canonical supported model identifier",
                            f"one of: {', '.join(sorted(SUPPORTED_MODELS))}",
                            received=raw_model,
                            field_schema={"type": "string", "enum": sorted(SUPPORTED_MODELS)},
                        )
            raw_dependencies = worker.get("depends_on")
            if raw_dependencies is not None:
                dependency_path = f"{worker_path}.depends_on"
                if not isinstance(raw_dependencies, list) or len(raw_dependencies) > len(AVAILABLE_GATES):
                    wave_diag(dependency_path, "depends_on must be an array of prerequisite phases", "an array of supported phase names")
                else:
                    dependencies = [item for item in raw_dependencies if isinstance(item, str)]
                    invalid_dependencies = sorted(set(dependencies) - AVAILABLE_GATES)
                    if invalid_dependencies or len(dependencies) != len(raw_dependencies):
                        wave_diag(
                            dependency_path,
                            "worker depends_on must contain only canonical phase names",
                            f"only: {', '.join(sorted(AVAILABLE_GATES))}",
                            received=raw_dependencies,
                            field_schema={"type": "array", "items": {"type": "string", "enum": sorted(AVAILABLE_GATES)}},
                        )
                        continue
                    unknown_dependencies = sorted(set(dependencies) - AVAILABLE_GATES)
                    if unknown_dependencies:
                        wave_diag(
                            dependency_path,
                            "worker depends_on contains unknown phases: " + ", ".join(unknown_dependencies),
                            f"only: {', '.join(sorted(AVAILABLE_GATES))}",
                            received=raw_dependencies,
                            field_schema={"type": "array", "items": {"type": "string", "enum": sorted(AVAILABLE_GATES)}},
                        )
                    # Do not report a second cross-field error for an unknown
                    # dependency.  Basic enum validation owns that field
                    # first; only known phases can be checked for ordering.
                    unavailable = sorted((set(dependencies) & AVAILABLE_GATES) - available_context_gates)
                    if unavailable:
                        wave_diag(
                            dependency_path,
                            "worker depends_on may reference only completed or earlier-wave phases: " + ", ".join(unavailable),
                            "completed or earlier-wave phases only",
                            received=raw_dependencies,
                            field_schema={"type": "array", "items": {"type": "string", "enum": sorted(AVAILABLE_GATES)}, "rule": "completed or earlier-wave phases only"},
                        )
        available_context_gates.add(gate)
    if wave_diagnostics:
        raise ValidationFailure(wave_diagnostics)

    result: list[dict[str, Any]] = []
    phase_waves: dict[str, tuple[int, str]] = {}
    available_context_gates = set(completed_gates or set())
    for wave_index, raw_wave in enumerate(raw_waves, 1):
        if not isinstance(raw_wave, dict) or set(raw_wave) != {"phase", "workers"}:
            raise ValueError(f"waves[{wave_index - 1}] must contain exactly phase and workers")
        raw_phase = str(raw_wave.get("phase") or "").strip()
        if not raw_phase:
            raise ValueError(f"waves[{wave_index - 1}].phase is required")
        gate = canonical_pipeline_gate(raw_phase)
        if gate not in AVAILABLE_GATES:
            suggestions = difflib.get_close_matches(gate, sorted(AVAILABLE_GATES), n=3)
            suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
            raise ValueError(f"unknown wave phase {raw_phase!r}" + suffix)
        prior_phase = phase_waves.get(gate)
        if prior_phase is not None and prior_phase[0] != wave_index:
            raise ValueError(
                f"waves repeat canonical phase {gate!r}: {prior_phase[1]!r} and {raw_phase!r}; "
                "put multiple owners of one phase in the same wave"
            )
        phase_waves.setdefault(gate, (wave_index, raw_phase))
        workers = raw_wave.get("workers")
        if not isinstance(workers, list) or not workers or len(workers) > 32:
            raise ValueError(f"waves[{wave_index - 1}].workers must contain 1..32 workers")
        delegations: list[dict[str, Any]] = []
        for worker_index, worker in enumerate(workers, 1):
            if not isinstance(worker, dict):
                raise ValueError(f"waves[{wave_index - 1}].workers[{worker_index - 1}] must be an object")
            unsupported = sorted(set(worker) - worker_public_fields)
            if unsupported:
                raise ValueError(
                    f"waves[{wave_index - 1}].workers[{worker_index - 1}] contains unsupported field(s): "
                    + ", ".join(unsupported)
                )
            raw_profile = str(worker.get("profile") or "").strip()
            profile = canonical_profile(raw_profile) if raw_profile else _default_profile_for_gate(gate)
            if profile not in AGENTS:
                suggestions = difflib.get_close_matches(profile, sorted(AGENTS), n=3)
                suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
                raise ValueError(f"unknown worker profile {raw_profile!r}" + suffix)
            if not profile_can_own_gate(profile, gate):
                supported = PROFILES[profile].get("gates", []) or ["implementation"]
                raise ValueError(
                    f"worker profile {profile!r} cannot own phase {gate!r}; "
                    f"supported phase(s): {', '.join(supported)}"
                )
            spec: dict[str, Any] = {
                "gate": gate,
                "agent": profile,
                "selection_reason": (
                    f"The coordinator explicitly selected `{profile}` for the `{gate}` phase."
                    if raw_profile else
                    f"`{profile}` is the canonical automatic owner for the `{gate}` phase."
                ),
                "dispatch_mode": "hidden_subagent",
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
                    or any(not isinstance(item, str) or not item.strip() for item in raw_result_refs)
                ):
                    raise ValueError("worker context_result_refs must be a non-empty-string array")
                result_refs = [safe_id(item.strip()) for item in raw_result_refs]
                if len(result_refs) != len(set(result_refs)):
                    raise ValueError("worker context_result_refs must be unique")
                spec["context_result_refs"] = result_refs
            for source, target in (
                ("objective", "objective"), ("paths", "allowed_paths"),
                ("allowed_paths", "allowed_paths"),
                ("acceptance", "acceptance_criteria"), ("verification", "verification"),
                ("context_files", "context_files"),
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
            model = _v11_model(worker.get("model"))
            if model:
                spec["requested_model"] = model
            user_requested_model = _v11_model(worker.get("user_requested_model"))
            if user_requested_model:
                if model and model != user_requested_model:
                    raise ValueError("worker model and user_requested_model must match")
                spec["requested_model"] = user_requested_model
                spec["user_requested_model"] = user_requested_model
            if str(worker.get("effort") or "").strip():
                spec["requested_reasoning_effort"] = str(worker["effort"]).strip().lower()
            delegations.append(spec)
        result.append({"wave_id": f"wave-{wave_index:02d}", "delegations": delegations})
        available_context_gates.add(gate)
    return result


def _v11_auto_waves(task: dict[str, Any]) -> list[dict[str, Any]]:
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
    classified_groups = (
        [["scope"], ["discover"], ["architecture"], ["plan"], ["documentation"], ["review"], ["close"]]
        if knowledge_harvest else classified["parallel_groups"]
    )
    # A wave owns exactly one phase. Independent phases remain distinct waves;
    # parallelism within a wave is reserved for multiple workers sharing that
    # phase and therefore one inherited profile-ownership contract.
    groups = [[gate] for group in classified_groups for gate in group]
    waves = [
        {
            "wave_id": f"wave-{index:02d}",
            "delegations": [automatic_spec(gate) for gate in group],
        }
        for index, group in enumerate(groups, 1)
    ]
    return _append_governance_waves(waves, task)

def _append_governance_waves(waves: list[dict[str, Any]], task: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the orchestrator-selected waves intact and record governance advice.

    Full governance is an assessment profile. It may recommend activation and
    close review, but it never injects, reorders, or rejects the selected
    pipeline. Any explicit governance workers supplied by the orchestrator are
    normalized by the ordinary wave validator.
    """
    del task
    return list(waves)


def _v11_host_capabilities() -> dict[str, Any]:
    return {
        "spawn_agent_models": sorted(SUPPORTED_MODELS),
        "spawn_agent_default_model": CONFIGURED_DEFAULT_MODEL,
    }


def _v11_native_arguments(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("host_tool") not in {None, "", "spawn_agent"}:
        raise WorkerAssignmentError("native_spawn_agent_transport_required")
    arguments: dict[str, Any] = {
        "task_name": request.get("task_name"),
        "message": request.get("message"),
        "reasoning_effort": request.get("reasoning_effort"),
        "fork_turns": request.get("fork_turns") or "none",
    }
    if request.get("model"):
        arguments["model"] = request["model"]
    return {key: value for key, value in arguments.items() if value is not None}


def _v11_response(
    old: dict[str, Any],
    task_ref: str,
    *,
    include_result: bool = False,
    start_replayed: bool | None = None,
) -> dict[str, Any]:
    """Public delegating entrypoint for the orchestration response adapter."""
    # Durable engine/operation receipts contain only logical dispatch
    # templates.  Rehydrate the bearer-bearing native message at this final,
    # non-persisted response boundary after the transaction has committed.
    requests = old.get("spawn_requests") if isinstance(old, dict) else None
    if isinstance(requests, list) and requests:
        bound = _bind_task_project_root({"task_ref": task_ref}, include_completed=True)
        if not isinstance(bound, dict):
            raise WorkerAssignmentError("worker_dispatch_rehydration_unavailable")
        root = ledger_root(bound)
        # Native dispatch delivery is a one-shot durable fence.  The engine
        # persists only a non-secret template; this final response boundary
        # verifies exact template identity, renders the bearer once, then
        # consumes delivery before any response can be replayed.  A lost
        # response therefore fails closed and requires a fresh attempt.
        with state_lock(root, operation="native_spawn_dispatch_delivery"):
            resolved = _v11_resolve_task(bound, require_task_ref=True)
            if isinstance(resolved, dict):
                raise WorkerAssignmentError("worker_dispatch_rehydration_unavailable")
            task_dir, state, _project, _resolved_ref = resolved
            task_definition = load_task_definition(task_dir)
            hydrated: list[dict[str, Any]] = []
            delivered: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for request in requests:
                if not isinstance(request, dict):
                    raise WorkerAssignmentError("worker_dispatch_rehydration_unavailable")
                attempt_id = str(request.get("attempt_id") or "").strip()
                dispatch_ref = str(request.get("dispatch_ref") or "").strip()
                try:
                    valid_attempt_id = bool(attempt_id and safe_id(attempt_id) == attempt_id)
                except ValueError:
                    valid_attempt_id = False
                if not valid_attempt_id or not re.fullmatch(r"dispatch-[0-9a-f]{24}", dispatch_ref):
                    raise WorkerAssignmentError("worker_dispatch_rehydration_unavailable")
                identity = (attempt_id, dispatch_ref)
                if identity in seen:
                    raise WorkerAssignmentError("worker_dispatch_rehydration_unavailable")
                seen.add(identity)
                candidates = [
                    item for item in state.get("attempts") or []
                    if isinstance(item, dict)
                    and str(item.get("attempt_id") or "") == attempt_id
                    and str(item.get("dispatch_ref") or "") == dispatch_ref
                ]
                if (
                    len(candidates) != 1
                    or candidates[0].get("status") != AWAITING_HOST_SPAWN
                    or candidates[0].get("assignment_delivery_status") != "pending"
                ):
                    raise WorkerAssignmentError("worker_dispatch_rehydration_unavailable")
                hydrated.append({
                    **_rehydrate_dispatch_spawn_request(task_dir, task_definition, candidates[0]),
                    "attempt_id": attempt_id,
                })
                delivered.append(candidates[0])
            for attempt in delivered:
                attempt["assignment_delivery_status"] = "delivered"
                attempt["assignment_delivered_at"] = now()
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "native_spawn_dispatch_delivery",
                ", ".join(str(item.get("attempt_id") or "") for item in delivered),
            )
            old = {**old, "spawn_requests": hydrated}
    response = render_v11_response(
        old,
        task_ref,
        native_arguments=_v11_native_arguments,
        public_schema=PUBLIC_ORCHESTRATION_SCHEMA,
        coordinator_lock=COORDINATOR_LOCK,
        include_result=include_result,
        start_replayed=start_replayed,
    )
    return response



def _v11_compact_continue_replay(response: dict[str, Any]) -> dict[str, Any]:
    """Turn a completed continue response into a non-dispatching receipt."""
    task_ref = str(response.get("task_ref") or "")
    return validate_v11_response("coordinator.lifecycle", {
        "schema": "cortex/lifecycle-response/v11",
        "ok": False,
        "outcome": "failed",
        "task_ref": task_ref,
        "action": {"kind": "inspect_or_retry"},
        "error": {
            "code": "continue_response_already_consumed",
            "category": "unavailable",
            "message": "The exact continuation response was already delivered and cannot reissue native dispatches.",
            "diagnostics": [{
                "code": "continue_response_already_consumed",
                "json_pointer": "",
                "message": "The exact continuation response was already delivered and cannot reissue native dispatches.",
                "field_schema": {"type": "object"},
            }],
        },
        "recovery": {
            "kind": "terminal_stop",
            "operation": "continue_orchestration",
            "retryable": False,
            "state_mutated": False,
        },
    })


def _v11_continue_receipt_digest(params: dict[str, Any]) -> str:
    """Identify the exact canonical result slots consumed by one continuation."""
    slots: list[dict[str, Any]] = []
    raw_results = params.get("results")
    if isinstance(raw_results, list):
        for index, raw in enumerate(raw_results, 1):
            if not isinstance(raw, dict):
                # Validation owns malformed result entries.  This placeholder
                # only makes the guard deterministic before that validation.
                slots.append({"slot": index, "invalid": True})
                continue
            worker = raw.get("worker")
            slot = worker if type(worker) is int else index
            slots.append({
                "slot": slot,
                "attempt_result_ref": str(raw.get("attempt_result_ref") or "").strip() or None,
                "dispatch_ref": str(raw.get("dispatch_ref") or "").strip() or None,
            })
    return _orchestrate_request_digest({
        "step": params.get("step"),
        # Ordering is not semantic once each slot is explicit.  Sorting also
        # closes an avoidable replay bypass for reordered parallel receipts.
        "slots": sorted(slots, key=lambda item: (str(item.get("slot")), str(item))),
    })


def _v11_consumed_continue_error(task_ref: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a deterministic server-reconciliation receipt for reused receipts."""
    result = _v11_error(
        "continue_receipts_already_consumed",
        "the submitted canonical result receipts were already consumed for this task wave; "
        "Cortex will not replan or dispatch from them again",
        # This is a recoverable idempotency receipt, not a public lifecycle
        # block.  Reconcile from the durable task rather than asking the
        # coordinator to enter a manage/inspect loop.
        outcome="needs_input",
    )
    result["task_ref"] = task_ref
    result["retryable"] = True
    result["recovery"] = "inspect_exact_task"
    if isinstance(params, dict):
        reconciled = _v11_reconcile_stale_continue(params, result)
        reconciled["task_ref"] = task_ref
        reconciled["recovery"] = {
            "mode": "server_reconcile",
            "source": "consumed_continue_receipt",
            "automatic": True,
        }
        return reconciled
    result["next_action"] = (
        "Cortex recorded these result receipts already. Reconcile the same task from its durable ledger and "
        "use only the returned current dispatches or continuation; do not resubmit the consumed receipts."
    )
    return result


def _v11_consumed_continue_replay(
    params: dict[str, Any],
    task_id: str,
    task_ref: str,
) -> dict[str, Any] | None:
    """Fail closed when a prior successful continue already consumed receipts.

    The registry is deliberately keyed by task plus relative step/result slots,
    not by the full public request.  Exact request replays remain handled by
    ``last_continue`` before this guard; a changed future-wave proposal gets a
    stable stop instead of a second state mutation.
    """
    receipt_digest = _v11_continue_receipt_digest(params)
    record = _operation_registry(ledger_root(params)).get("tasks", {}).get(task_id, {})
    consumed = record.get("consumed_continue_receipts") if isinstance(record, dict) else None
    if not isinstance(consumed, list):
        return None
    if any(
        isinstance(item, dict) and item.get("receipt_digest") == receipt_digest
        for item in consumed
    ):
        return _v11_consumed_continue_error(task_ref, params)
    return None


class StartRequestConflict(ValueError):
    """A transport request id was reused for a different start payload."""

    def __init__(self, *, request_id: str, expected_digest: str, received_digest: str) -> None:
        self.request_id = request_id
        self.expected_digest = expected_digest
        self.received_digest = received_digest
        super().__init__("transport request identity was reused with a different start payload")


def _v11_start_reservation(
    params: dict[str, Any],
    task: dict[str, Any],
    *,
    request_digest: str,
) -> tuple[str, str, str, bool, str | None, int, bool]:
    root = ledger_root(params)
    # Start idempotency belongs to the transport request, not to semantic
    # task text.  A new thread can legitimately submit the same task wording
    # and must receive a fresh durable task.  Only a repeated request on the
    # same MCP connection (the private transport identity injected by
    # serve_stdio) may replay its original receipt.
    transport_request_id = str(params.get("_transport_request_id") or "").strip()
    with state_lock(root):
        registry = _operation_registry(root)
        prior = registry["starts"].get(transport_request_id) if transport_request_id else None
        if transport_request_id and isinstance(prior, dict):
            prior_digest = str(prior.get("request_digest") or "")
            if prior_digest and prior_digest != request_digest:
                raise StartRequestConflict(
                    request_id=transport_request_id,
                    expected_digest=prior_digest,
                    received_digest=request_digest,
                )
            task_id = str(prior.get("task_id") or "")
            loaded = _v11_task_state(root, task_id) if task_id else None
            # A reservation without a task ledger is not a successful replay:
            # the first caller may have died between reservation and engine
            # materialization. Re-stage fresh one-response credentials and
            # let the same task id resume materialization atomically. Once a
            # task exists, every lifecycle status (including completed and
            # cancelled) is an immutable replay of that task identity.
            if loaded is None:
                if (
                    str(prior.get("materialization_status") or "") == "reserved"
                    and _start_materialization_lease_active(prior.get("materialization_lease_expires_at"))
                    and str(prior.get("materialization_owner") or "").strip()
                ):
                    return (
                        task_id,
                        str(prior["task_ref"]),
                        str(prior["submission_id"]),
                        False,
                        None,
                        int(prior.get("materialization_generation") or 1),
                        True,
                    )
                owner = secrets.token_hex(16)
                generation = int(prior.get("materialization_generation") or 0) + 1
                capability_claims = _capability_claims(
                    task_id=task_id,
                    initiative_ref=str(task.get("initiative_ref") or "") or None,
                )
                _, capability_digest = _stage_coordinator_capability(root, task_id)
                capability_claims_mac = _coordinator_claims_mac(
                    root, capability_claims, create_key=True,
                )
                prior.update({
                    "materialization_status": "reserved",
                    "materialization_owner": owner,
                    "materialization_generation": generation,
                    "materialization_lease_expires_at": _start_materialization_lease_expiry(),
                    "coordinator_capability_digest": capability_digest,
                    "coordinator_capability_claims": capability_claims,
                    "coordinator_capability_claims_mac": capability_claims_mac,
                    "updated_at": now(),
                })
                registry["starts"][transport_request_id] = prior
                task_record = registry["tasks"].setdefault(task_id, {})
                start_record = task_record.setdefault("start", {})
                start_record.update({
                    "materialization_status": "reserved",
                    "materialization_owner": owner,
                    "materialization_generation": generation,
                    "materialization_lease_expires_at": prior["materialization_lease_expires_at"],
                    "coordinator_capability_digest": capability_digest,
                    "coordinator_capability_claims": prior["coordinator_capability_claims"],
                    "coordinator_capability_claims_mac": capability_claims_mac,
                })
                try:
                    _write_operation_registry(root, registry)
                except Exception:
                    # A recovery reservation must not leave a raw bearer in
                    # process memory when its durable CAS did not commit.
                    _take_coordinator_capability(root, task_id)
                    raise
                return (
                    task_id,
                    str(prior["task_ref"]),
                    str(prior["submission_id"]),
                    False,
                    owner,
                    generation,
                    False,
                )
            else:
                if str(loaded[1].get("status") or "") in {"completed", "cancelled"}:
                    # A terminal replay must never leave an undelivered
                    # in-memory coordinator bearer eligible for later pickup.
                    _take_coordinator_capability(root, task_id)
                return (
                    task_id,
                    str(prior["task_ref"]),
                    str(prior["submission_id"]),
                    True,
                    None,
                    int(prior.get("materialization_generation") or 1),
                    False,
                )
        objective_slug = v11_task_slug(task["user_request"])
        task_id = safe_id(f"{objective_slug}-{secrets.token_hex(4)}")
        task_ref = _v11_task_ref(task_id)
        submission_id = safe_id("orchestration-start-" + secrets.token_hex(8))
        capability_claims = _capability_claims(
            task_id=task_id,
            initiative_ref=str(task.get("initiative_ref") or "") or None,
        )
        _, capability_digest = _stage_coordinator_capability(root, task_id)
        capability_claims_mac = _coordinator_claims_mac(
            root, capability_claims, create_key=True,
        )
        reservation = {
            "task_id": task_id,
            "task_ref": task_ref,
            "submission_id": submission_id,
            "coordinator_capability_digest": capability_digest,
            "coordinator_capability_claims": capability_claims,
            "coordinator_capability_claims_mac": capability_claims_mac,
            "materialization_status": "reserved",
            "materialization_owner": secrets.token_hex(16),
            "materialization_generation": 1,
            "materialization_lease_expires_at": _start_materialization_lease_expiry(),
            "created_at": now(),
        }
        if transport_request_id:
            registry["starts"][transport_request_id] = {
                "request_digest": request_digest,
                **reservation,
            }
        registry["tasks"].setdefault(task_id, {})["start"] = {
            "digest": request_digest,
            "transport_request_id": transport_request_id or None,
            **reservation,
        }
        try:
            _write_operation_registry(root, registry)
        except Exception:
            # The durable reservation did not become a usable start receipt.
            # Drop the in-memory pair as well, so a later retry cannot turn a
            # failed persistence path into raw-authorization delivery.
            _take_coordinator_capability(root, task_id)
            raise
        return task_id, task_ref, submission_id, False, reservation["materialization_owner"], 1, False


def _v11_start_request_digest(
    *,
    project_root: Path,
    task: dict[str, Any],
    waves: list[dict[str, Any]],
) -> str:
    """Digest only stable canonical start semantics for transport replay.

    Follow-up context contains a server-created timestamp for auditability;
    that timestamp is not request semantics and must not make a retry conflict
    with its own server-owned stable transport identity.
    """
    canonical_task = dict(task)
    follow_up = canonical_task.get("follow_up")
    if isinstance(follow_up, dict) and "created_at" in follow_up:
        canonical_task["follow_up"] = {
            key: value for key, value in follow_up.items() if key != "created_at"
        }
    return _orchestrate_request_digest({
        "project_root": str(project_root),
        "task": canonical_task,
        "waves": waves,
    })


def _start_orchestration_impl(params: dict[str, Any]) -> dict[str, Any]:
    """Start public Cortex orchestration without caller-managed lifecycle identifiers."""
    staged_authorization_task_id: str | None = None
    reserved_task_id: str | None = None
    materialization_owner: str | None = None
    try:
        start_schema, task_form_schema, _waves_schema, _wave_schema, _worker_schema = _v11_start_public_schema_forms()
        start_public_fields = set(start_schema["properties"])
        task_public_fields = set(task_form_schema["properties"])
        internal_start_fields = {"_follow_up", "_transport_request_id"}
        envelope = _v11_collect_fields(
            params,
            start_public_fields | internal_start_fields,
            operation="start_orchestration",
            public_schema=start_schema,
        )
        if not isinstance(params, dict):
            return _v11_envelope_error("start_orchestration", envelope)
        project_root_schema = start_schema["properties"].get("project_root")
        if "project_root" not in params:
            envelope.append({
                "code": "start_orchestration_validation_failed", "phase": "payload", "path": "project_root",
                "json_pointer": "/project_root", "message": "is required", "received": None,
                "expected": dict(project_root_schema or {"type": "string", "minLength": 1}),
                "field_schema": dict(project_root_schema or {"type": "string", "minLength": 1}),
                "fix": "Supply the exact absolute project_root for this start request.",
            })
        elif isinstance(project_root_schema, Mapping):
            envelope.extend(_v11_schema_value_diagnostics(
                params.get("project_root"), project_root_schema,
                pointer="/project_root", operation="start_orchestration",
            ))
        raw_task_probe = params.get("task") if isinstance(params, dict) else None
        envelope.extend(_v11_start_task_preflight(raw_task_probe))
        if "waves" in params:
            envelope.extend(_v11_start_wave_preflight(params.get("waves")))
        if envelope:
            return _v11_envelope_error("start_orchestration", envelope)
        selected_project_root = select_project_root(params)
        if set(params) - (start_public_fields | internal_start_fields):
            raise ValueError("start_orchestration accepts only project_root, task, waves, and optional _follow_up")
        raw_task = params.get("task")
        if not isinstance(raw_task, dict):
            raise ValueError("task must be an object containing the exact user_request")
        unknown_task = sorted(set(raw_task) - task_public_fields)
        if unknown_task:
            raise RuntimeError("public start preflight admitted an unsupported task field")
        user_request = str(raw_task.get("user_request") or "").strip()
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
        task["complexity"] = _v11_complexity(raw_task.get("complexity"))
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
            else _v11_plan_approval(raw_task.get("plan_approval"), task["complexity"])
        )
        task["user_language"] = normalize_user_language(
            task.get("user_language"),
            user_request,
        )
        task["communication_profile"] = select_communication_profile(task)
        waves = (
            _append_governance_waves(
                _v11_compact_waves(
                    params["waves"], task, project_root=selected_project_root,
                ),
                task,
            )
            if params.get("waves") is not None else _v11_auto_waves(task)
        )
        request_digest = _v11_start_request_digest(
            project_root=selected_project_root,
            task=task,
            waves=waves,
        )
        task_id, task_ref, submission_id, replayed, materialization_owner, materialization_generation, materialization_pending = _v11_start_reservation(
            params,
            task,
            request_digest=request_digest,
        )
        principal = safe_id("orchestration-" + task_ref)
        reserved_task_id = task_id
        if materialization_pending:
            return _v11_start_materialization_pending(task_ref)
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
                }
                if not activation_record(ledger_root(params), replay_params, task_id):
                    activated = activate_orchestration({
                        **replay_params,
                        "user_command": ACTIVATION_COMMAND,
                    })
                    if not activated.get("active"):
                        raise ValueError("linked corrective-task replay could not restore its Cortex activation")
            loaded = _v11_task_state(ledger_root(params), task_id)
            if loaded is None:
                old = {
                    "ok": True,
                    "state": "waiting_workers",
                    "wave_id": None,
                    "spawn_requests": [],
                }
            else:
                old = _engine_inspect_lifecycle({
                    "project_root": params["project_root"],
                    "principal": principal,
                    "task_id": task_id,
                })
            # An idempotent start is a status replay, never a credential
            # delivery channel.  In particular, a worker that knows the user
            # request cannot race or retry a start to obtain coordinator-only
            # material staged for the original coordinator response.
            return _v11_response(old, task_ref, start_replayed=True)
        old = _engine_start_lifecycle({
            "submission_id": submission_id,
            "project_root": params["project_root"],
            "principal": principal,
            "task": {**task, "task_id": task_id},
            "waves": waves,
            "host_capabilities": _v11_host_capabilities(),
            "_materialization_fence": lambda: _v11_materialization_fence(
                ledger_root(params), task_id, materialization_owner, materialization_generation,
            ),
        })
        with state_lock(ledger_root(params)):
            registry = _operation_registry(ledger_root(params))
            reservation_key = str(params.get("_transport_request_id") or "").strip()
            reservation = (
                registry.get("starts", {}).get(reservation_key)
                if reservation_key
                else None
            )
            task_record = registry["tasks"].setdefault(task_id, {})
            task_start = task_record.setdefault("start", {})

            # Materialization is committed only by the lease owner that fenced
            # the engine call.  This applies to both transport-backed starts
            # and direct/source-mode starts (which have no ``starts`` entry).
            # A late owner must never clear a newer takeover's reservation.
            def owned(record: Any) -> bool:
                return bool(
                    isinstance(record, dict)
                    and str(record.get("materialization_status") or "") == "reserved"
                    and str(record.get("materialization_owner") or "") == str(materialization_owner or "")
                    and record.get("materialization_generation") == materialization_generation
                )

            materialized_at = now()
            changed = False
            if owned(reservation):
                reservation["materialization_status"] = "materialized"
                reservation.pop("materialization_owner", None)
                reservation.pop("materialization_lease_expires_at", None)
                reservation["materialized_at"] = materialized_at
                changed = True
            if owned(task_start):
                task_start["materialization_status"] = "materialized"
                task_start.pop("materialization_owner", None)
                task_start.pop("materialization_lease_expires_at", None)
                task_start["materialized_at"] = materialized_at
                changed = True
            if changed:
                _write_operation_registry(ledger_root(params), registry)
        if isinstance(old, dict):
            old["governance"] = governance
        response = _v11_response(old, task_ref, start_replayed=replayed)
        authorization = _take_coordinator_capability(ledger_root(params), task_id)
        staged_authorization_task_id = None
        if authorization and response.get("ok"):
            # This is the sole explicit coordinator-capability delivery path.
            response["coordinator_ref"] = authorization
            response = validate_v11_response("coordinator.start", response)
        elif authorization:
            _revoke_coordinator_capability(ledger_root(params), task_id, reason="start_authorization_response_unavailable")
        elif response.get("ok"):
            raise RuntimeError("successful start could not deliver its coordinator capability")
        return response
    except OperationRegistryError as exc:
        if reserved_task_id and materialization_owner:
            _v11_release_start_materialization_lease(ledger_root(params), reserved_task_id, materialization_owner)
        if staged_authorization_task_id:
            _take_coordinator_capability(ledger_root(params), staged_authorization_task_id)
            _revoke_coordinator_capability(
                ledger_root(params), staged_authorization_task_id,
                reason="start_authorization_response_unavailable",
            )
        return _v11_start_state_blocked_error(exc)
    except StartRequestConflict as exc:
        diagnostic = {
            "code": "start_request_identity_conflict",
            "phase": "transport",
            "path": "request",
            "json_pointer": "",
            "message": "the transport request identity was already used with a different canonical start payload",
            "received": {"digest": exc.received_digest},
            "expected": {"digest": exc.expected_digest},
            "field_schema": {"type": "object", "additionalProperties": False},
            "fix": "Resend the original payload unchanged for this request identity, or issue a new transport request with a new request id to create a new task.",
        }
        response = _v11_envelope_error("start_orchestration", [diagnostic])
        response["outcome"] = "needs_correction"
        response["retryable"] = True
        response["task_created"] = False
        return response
    except ValidationFailure as exc:
        if reserved_task_id and materialization_owner:
            _v11_release_start_materialization_lease(ledger_root(params), reserved_task_id, materialization_owner)
        if staged_authorization_task_id:
            _take_coordinator_capability(ledger_root(params), staged_authorization_task_id)
            _revoke_coordinator_capability(
                ledger_root(params), staged_authorization_task_id,
                reason="start_authorization_response_unavailable",
            )
        return _v11_envelope_error("start_orchestration", _start_exception_diagnostics(exc, params))
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        if reserved_task_id and materialization_owner:
            _v11_release_start_materialization_lease(ledger_root(params), reserved_task_id, materialization_owner)
        if staged_authorization_task_id:
            _take_coordinator_capability(ledger_root(params), staged_authorization_task_id)
            _revoke_coordinator_capability(
                ledger_root(params), staged_authorization_task_id,
                reason="start_authorization_response_unavailable",
            )
        return _v11_envelope_error("start_orchestration", _start_exception_diagnostics(exc, params))


def _v11_status(value: object, *, has_attempt_result: bool) -> str:
    if value in {None, ""} and has_attempt_result:
        return "passed"
    raw = value
    if isinstance(raw, str) and raw in CANONICAL_STATUS_VALUES:
        return raw
    suggestions = difflib.get_close_matches(str(raw), sorted(CANONICAL_STATUS_VALUES), n=3)
    suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
    raise ValueError("result status must be one of the canonical terminal values" + suffix)


def _v11_active_wave_context(
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
    # A worker may publish a canonical non-success AttemptResult before the
    # coordinator consumes the current wave.  ``read_worker_result``
    # intentionally does not manufacture a success continuation for BLOCKED
    # or FAILED results, but the exact terminal slot must still be addressable
    # by ``continue_orchestration`` as a non-success receipt.  Without this
    # set, a blocked Planner disappears from the active-wave cardinality and
    # the coordinator either submits a fabricated success or receives the
    # misleading ``requires 0 result(s)`` error.
    terminal_result_ids = {
        str(item.get("attempt_id") or "")
        for item in state.get("attempts", [])
        if item.get("gate") in wave.get("gates", [])
        and item.get("status") in {"failed", "blocked", "cancelled", "superseded"}
        and str(item.get("attempt_result_ref") or "").strip()
        and str(item.get("lifecycle_status") or "").strip().lower()
        in {"failed", "blocked", "cancelled", "superseded"}
        and not item.get("invalidated")
    }
    wave_attempt_ids = [
        str(attempt_id)
        for attempt_id in (wave.get("attempt_ids") or [])
        if str(attempt_id or "").strip()
    ]
    if active_attempt_ids:
        eligible = set(active_attempt_ids) | attempt_result_absent_failure_ids | terminal_result_ids
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
        attempt_ids = [
            attempt_id for attempt_id in wave_attempt_ids
            if attempt_id in attempt_result_absent_failure_ids or attempt_id in terminal_result_ids
        ]
    if not attempt_ids:
        attempt_ids = active_attempt_ids
    return wave, attempt_ids, expected_step


def _v11_continue_context(
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
        wave, attempt_ids, _ = _v11_active_wave_context(params, task_dir, state)
        submission_id = safe_id("orchestration-continue-" + digest_text(state["task_id"] + ":" + str(wave["wave_id"]) + ":" + request_digest)[:20])
        old_params = {
            "submission_id": submission_id,
            "project_root": params["project_root"],
            "principal": state.get("principal"),
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


def _v11_store_continue(params: dict[str, Any], task_id: str, request_digest: str, response: dict[str, Any], *, clear_only: bool = False) -> None:
    root = ledger_root(params)
    with state_lock(root):
        registry = _operation_registry(root)
        task_record = registry["tasks"].setdefault(task_id, {})
        task_record.pop("inflight_continue", None)
        if not clear_only:
            task_record["last_continue"] = {
                "digest": request_digest,
                "response": _v11_compact_continue_replay(response),
                "completed_at": now(),
            }
            # A worker receipt is single-use. Persist a separate bounded
            # consumption record so reordered retries cannot consume it twice.
            receipt_digest = _v11_continue_receipt_digest(params)
            consumed = task_record.setdefault("consumed_continue_receipts", [])
            if not any(
                isinstance(item, dict) and item.get("receipt_digest") == receipt_digest
                for item in consumed
            ):
                consumed.append({
                    "receipt_digest": receipt_digest,
                    "step": params.get("step"),
                    "consumed_at": now(),
                })
                task_record["consumed_continue_receipts"] = [
                    item for item in consumed[-128:]
                    if isinstance(item, dict)
                ]
        _write_operation_registry(root, registry)


def _v11_completed_replay(params: dict[str, Any]) -> dict[str, Any] | None:
    """Replay a final continue after task completion removed the active mapping."""
    if _v11_task_candidates(params):
        return None
    request_digest = _orchestrate_request_digest({key: value for key, value in params.items() if key != "task_ref"})
    requested_ref = str(params.get("task_ref") or "").strip()
    registry = _operation_registry(ledger_root(params))
    matches = []
    for task_id, record in registry.get("tasks", {}).items():
        if requested_ref and _v11_task_ref(str(task_id)) != requested_ref:
            continue
        last = record.get("last_continue") if isinstance(record, dict) else None
        if isinstance(last, dict) and last.get("digest") == request_digest and isinstance(last.get("response"), dict):
            matches.append(dict(last["response"]))
    return matches[0] if len(matches) == 1 else None


def _v11_active_replay(params: dict[str, Any], task_id: str) -> dict[str, Any] | None:
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
    results: Any = None,
) -> dict[str, Any]:
    """Re-resolve governance triggers as an advisory boundary assessment.

    Governance is not a one-time start classification. A later result may
    recommend a deeper review, but that recommendation cannot replace the
    orchestrator-selected pipeline or stop continuation. Objective integrity,
    capability, authorization, and safety checks remain separate hard
    boundaries.
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

    boundary_triggers = explicit_triggers(results)
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
        advice = {
            "code": "governance_boundary_recommendation",
            "severity": "high",
            "recommended_mode": "full",
            "recommended_pipeline": list(
                state.get("recommended_pipeline")
                or state.get("chosen_pipeline")
                or state.get("current_pipeline")
                or []
            ),
            "message": "A later task signal recommends full governance review; the orchestrator-selected pipeline remains executable.",
            "recommended_next": "review_governance_advice_and_continue_selected_pipeline",
            "chosen_pipeline_unchanged": True,
        }
        existing = state.setdefault("orchestration_advice", [])
        if isinstance(existing, list) and advice not in existing:
            existing.append(advice)
        return {**resolved, "advisory": advice}
    return resolved


def _continue_orchestration_impl(params: dict[str, Any]) -> dict[str, Any]:
    """Advance exactly the active Cortex wave using relative worker slots."""
    resolved_task_ref = str(params.get("task_ref") or "").strip() or None if isinstance(params, dict) else None
    try:
        schema = PUBLIC_SCHEMA_REGISTRY.get("continue_orchestration", {})
        envelope = _v11_schema_value_diagnostics(params, schema, pointer="", operation="continue_orchestration")
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if not isinstance(params, dict):
            return _v11_envelope_error("continue_orchestration", envelope)
        for field in ("task_ref", "coordinator_ref"):
            if not isinstance(params.get(field), str) or not params.get(field, "").strip():
                envelope.append({
                    "code": "continue_orchestration_validation_failed", "phase": "payload", "path": field,
                    "json_pointer": f"/{field}", "message": "is required", "received": params.get(field),
                    "expected": dict(properties.get(field) or {"type": "string", "minLength": 1}),
                    "field_schema": dict(properties.get(field) or {"type": "string", "minLength": 1}),
                    "fix": f"Set /{field} to the exact Cortex-issued reference; do not synthesize a replacement value.",
                })
        if "results" not in params:
            card = dict(properties.get("results") or {"type": "array", "minItems": 1})
            envelope.append({"code": "continue_orchestration_validation_failed", "phase": "payload", "path": "results", "json_pointer": "/results", "message": "is required", "received": None, "expected": card, "field_schema": card, "fix": "Supply only the server-derived result refs returned by Cortex."})
        envelope.extend(_continue_form_diagnostics(params))
        if envelope:
            response = _v11_envelope_error(
                "continue_orchestration", envelope, task_ref=resolved_task_ref,
            )
            response["outcome"] = "needs_correction"
            return response
        if not resolved_task_ref:
            return _v11_task_ref_required_error("continue_orchestration")
        coordinator_project, _coordinator_task_dir, _coordinator_state, _coordinator_task, _ = authorize_coordinator_ref(
            params, "continue_orchestration",
        )
        params = {**params, "project_root": str(coordinator_project)}
        bound_params = _bind_task_project_root(params)
        if bound_params is None:
            return _v11_error(
                "task_scope_unavailable",
                "task_ref could not be resolved to one canonical project root; use the exact task_ref returned by start_orchestration",
                outcome="needs_input",
                task_ref=resolved_task_ref,
            )
        params = bound_params
        results = params.get("results")
        if not isinstance(results, list) or not results:
            raise ValueError("results must be a non-empty array")
        completed_replay = _v11_completed_replay(params)
        if completed_replay is not None:
            return completed_replay
        resolved = _v11_resolve_task(params, require_task_ref=True)
        if isinstance(resolved, dict):
            if resolved.get("code") in {"unknown_task_ref", "no_active_task", "task_unavailable", "task_ref_required"}:
                return _v11_continue_stop(resolved, reason="task_identity_or_lifecycle_mismatch")
            return resolved
        task_dir, state, task, task_ref = resolved
        resolved_task_ref = task_ref
        active_replay = _v11_active_replay(params, state["task_id"])
        if active_replay is not None:
            return active_replay
        if _plan_approval_is_pending(state):
            raise ValueError(
                "the completed plan is awaiting explicit user approval; use manage_orchestration "
                "with intent=plan_approval before continuing"
            )
        _, attempt_ids, _ = _v11_active_wave_context(params, task_dir, state)
        # Check the current relative step before the receipt-consumption
        # guard.  A stale prior step has its own terminal diagnostic; the
        # single-use receipt guard handles the pathological case where a
        # replacement plan retains the same numeric relative step.
        consumed_replay = _v11_consumed_continue_replay(params, state["task_id"], task_ref)
        if consumed_replay is not None:
            return consumed_replay
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
        governance_assessment = _governance_boundary_recheck(
            params,
            task,
            state,
            results=results,
        )
        if isinstance(governance_assessment, dict) and isinstance(governance_assessment.get("advisory"), dict):
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "governance_advisory",
                "recorded a governance recommendation without replacing the chosen pipeline",
            )
        if len(results) != len(attempt_ids):
            raise ValueError(f"active wave requires exactly {len(attempt_ids)} result(s)")
        slots: dict[int, dict[str, Any]] = {}
        multiple = len(attempt_ids) > 1
        for index, result in enumerate(results, 1):
            if not isinstance(result, dict):
                raise ValueError("every result must be an object")
            allowed_result = {"worker", "attempt_result_ref"}
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
            if not result_ref:
                raise ValueError("every result requires attempt_result_ref from read_worker_result")
            attempt = _attempt(state, attempt_ids[slot - 1])
            if result_ref != str(attempt.get("attempt_result_ref") or ""):
                raise ValueError("result does not match the exact active attempt")
            canonical = attempt_protocol.get_attempt_result(
                _task_document_root(task_dir, state["task_id"]),
                task_id=state["task_id"], attempt_id=attempt_ids[slot - 1],
            )
            if (
                canonical is None
                or canonical.get("result_ref") != result_ref
                or canonical.get("lifecycle_status") not in {
                    attempt_protocol.LIFECYCLE_COMPLETED,
                    attempt_protocol.LIFECYCLE_FAILED,
                    attempt_protocol.LIFECYCLE_BLOCKED,
                }
            ):
                raise ValueError("result requires a finalized canonical AttemptResult")
            slots[slot] = result
        # Reserve the server-owned transaction only after every immutable
        # canonical result ref has passed task/attempt/lifecycle validation.
        old_params, reserved_attempt_ids, _, request_digest, replay = _v11_continue_context(params, task_dir, state, task_ref)
        if replay is not None:
            return replay
        if reserved_attempt_ids != attempt_ids:
            raise ValueError("the active wave changed while continue was being validated; retry with the latest step")
        result_refs: list[dict[str, Any]] = []
        for slot, attempt_id in enumerate(attempt_ids, 1):
            result = slots[slot]
            result_ref = str(result.get("attempt_result_ref") or "").strip()
            result_refs.append({
                "attempt_id": attempt_id,
                "attempt_result_ref": result_ref,
            })
        old_params["result_refs"] = result_refs
        old = _engine_continue_lifecycle(old_params)
        response = _v11_response(
            old,
            task_ref,
            include_result=(
                old.get("state") == "awaiting_plan_approval"
                or (
                    isinstance(old.get("result"), dict)
                    and (
                        isinstance(old["result"].get("recovery"), dict)
                        or bool(old["result"].get("requires_user_decision"))
                    )
                )
            ),
        )
        if old.get("ok"):
            _v11_store_continue(params, state["task_id"], request_digest, response)
        elif str(old.get("phase")) in {"preflight", "started", "validation"}:
            _v11_store_continue(params, state["task_id"], request_digest, response, clear_only=True)
        return response
    except ValidationFailure as exc:
        response = _v11_envelope_error(
            "continue_orchestration",
            [dict(item) for item in exc.diagnostics],
            task_ref=resolved_task_ref,
        )
        response["outcome"] = "needs_correction"
        return response
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        try:
            resolved = _v11_resolve_task(params, require_task_ref=True)
            if not isinstance(resolved, dict):
                _, state, _, _ = resolved
                root = ledger_root(params)
                with state_lock(root):
                    registry = _operation_registry(root)
                    registry["tasks"].setdefault(state["task_id"], {}).pop("inflight_continue", None)
                    _write_operation_registry(root, registry)
        except Exception:
            pass
        error = _v11_error("continue_validation_failed", exc)
        if str(exc).startswith("continue step must match the active relative step"):
            return _v11_reconcile_stale_continue(params, error)
        if resolved_task_ref:
            error["task_ref"] = resolved_task_ref
        return error


def _v11_question_management_payload(value: object) -> dict[str, Any]:
    """Normalize the compact coordinator question contract.

    The public path accepts only a durable ``question_ref`` plus optional
    localization. Caller-supplied lifecycle identity is rejected rather than
    guessed or allowed to override the selected task.
    """
    if not isinstance(value, dict):
        raise ValueError("question management requires payload with question_ref")
    payload = dict(value)
    forbidden_identity = sorted(
        set(payload) & {"task_id", "principal", "attempt_id", "profile", "submission_id"}
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


def _v11_question_resume_contract(
    result: dict[str, Any],
    state: dict[str, Any],
) -> tuple[dict[str, str] | None, str | None]:
    """Derive the only resumable assignment target from a durable answer record.

    A coordinator can route the already-existing native child, but it must not
    reconstruct or choose assignment authority.  In particular, an answered
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


QUESTION_RESUME_CONTRACT_SCHEMA = "cortex/question-resume-contract/v1"


def _v11_question_display(result: dict[str, Any]) -> dict[str, Any] | None:
    interaction = result.get("chat_interaction") if isinstance(result.get("chat_interaction"), dict) else {}
    user_view = interaction.get("user_view") if isinstance(interaction.get("user_view"), dict) else {}
    prompt = str(user_view.get("question") or user_view.get("message") or result.get("question") or "").strip()
    if not prompt:
        return None
    question: dict[str, Any] = {"prompt": prompt[:8000]}
    raw_options = user_view.get("options")
    if isinstance(raw_options, list):
        options: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_options[:16], 1):
            if not isinstance(raw, dict):
                continue
            label = str(raw.get("label") or "").strip()
            if not label:
                continue
            item: dict[str, Any] = {
                "number": raw.get("number") if isinstance(raw.get("number"), int) else index,
                "label": label[:1000],
            }
            description = str(raw.get("description") or "").strip()
            if description:
                item["description"] = description[:2000]
            options.append(item)
        if options:
            question["options"] = options
    return question


def _v11_question_progress(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    answered = value.get("answered")
    total = value.get("total")
    if isinstance(answered, bool) or not isinstance(answered, int) or answered < 0:
        return None
    if isinstance(total, bool) or not isinstance(total, int) or total < 1:
        return None
    progress: dict[str, Any] = {"answered": answered, "total": total}
    next_key = str(value.get("next_question_key") or "").strip()
    if next_key:
        progress["next_question_key"] = next_key[:160]
    return progress


def _v11_question_management_failure(
    task_ref: str,
    message: str,
    *,
    retryable: bool = False,
    code: str = "question_management_unavailable",
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    safe_diagnostics = diagnostics or [{
        "code": code,
        "json_pointer": "",
        "message": redact(message, 1000),
        "field_schema": {"type": "object"},
    }]
    allowed_changes: list[dict[str, Any]] = []
    seen_pointers: set[str] = set()
    for diagnostic in safe_diagnostics:
        pointer = str(diagnostic.get("json_pointer") or "")
        if not pointer.startswith("/") or pointer in seen_pointers:
            continue
        message_lower = str(diagnostic.get("message") or "").lower()
        operation = (
            "remove" if any(marker in message_lower for marker in ("forbidden", "unsupported", "remove ")) else
            "add" if any(marker in message_lower for marker in ("is required", "requires ", "missing")) else
            "replace"
        )
        seen_pointers.add(pointer)
        allowed_changes.append({"json_pointer": pointer, "allowed_ops": [operation]})
    effective_retryable = bool(retryable and allowed_changes)
    recovery: dict[str, Any] = {
        "kind": "same_operation" if effective_retryable else "terminal_stop",
        "operation": "manage_orchestration",
        "retryable": effective_retryable,
        "state_mutated": False,
    }
    if effective_retryable:
        recovery["allowed_changes"] = allowed_changes
    return validate_v11_response("coordinator.question_management", {
        "schema": "cortex/question-management/v11",
        "ok": False,
        "outcome": "needs_correction",
        "error": {
                "code": code,
                "category": "validation" if effective_retryable else "unavailable",
                "message": redact(message, 512),
                "diagnostics": safe_diagnostics,
            },
        "recovery": recovery,
    })


def _v11_question_response(response: dict[str, Any], state: dict[str, Any], task_ref: str) -> dict[str, Any]:
    if not response.get("ok"):
        # Question rendering validates a localized display projection only; a
        # failed projection must remain a same-operation correction rather
        # than becoming a terminal unavailable state.  In particular, a
        # non-English task may need the coordinator to supply the four
        # display-only localization fields under ``payload``.  The durable
        # question itself remains unchanged and no answer/mutation occurred.
        raw_diagnostics = response.get("diagnostics")
        if bool(response.get("recoverable")) and isinstance(raw_diagnostics, list):
            localized_fields = {
                "localized_question": {"type": "string", "minLength": 1},
                "localized_header": {"type": "string", "minLength": 1},
                "localized_options": {"type": "array", "minItems": 1},
                "localized_custom_label": {"type": "string", "minLength": 1},
            }
            diagnostics: list[dict[str, Any]] = []
            seen_pointers: set[str] = set()
            for raw in raw_diagnostics[:16]:
                message = redact(
                    str(raw.get("message") or "question display input is invalid")
                    if isinstance(raw, dict) else str(raw),
                    1000,
                )
                raw_pointer = str(raw.get("json_pointer") or "") if isinstance(raw, dict) else ""
                field = next((name for name in localized_fields if name in message or name in raw_pointer), None)
                pointer = raw_pointer if raw_pointer.startswith("/payload/") else (
                    f"/payload/{field}" if field else "/payload"
                )
                if pointer in seen_pointers:
                    continue
                seen_pointers.add(pointer)
                diagnostics.append({
                    "code": "question_management_validation_failed",
                    "json_pointer": pointer,
                    "message": message,
                    "field_schema": dict(raw.get("field_schema") or localized_fields.get(field, {"type": "object"})) if isinstance(raw, dict) else localized_fields.get(field, {"type": "object"}),
                })
            if diagnostics:
                return _v11_question_management_failure(
                    task_ref,
                    "question display input is invalid",
                    retryable=True,
                    code="question_management_validation_failed",
                    diagnostics=diagnostics,
                )
        return _v11_question_management_failure(task_ref, "The question operation could not be completed.")
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    status_value = str(result.get("status") or "").strip()
    if status_value == "answered":
        resume_contract, resume_reason = _v11_question_resume_contract(result, state)
        if resume_contract is None:
            question_ref = str(result.get("question_id") or "").strip()
            if not question_ref.startswith("question-"):
                return _v11_question_management_failure(
                    task_ref, resume_reason or "The answered question is not resumable.",
                )
            return validate_v11_response("coordinator.question_management", {
                "schema": "cortex/question-management/v11",
                "ok": True,
                "outcome": "question_answered_not_resumable",
                "question_ref": question_ref,
            })
        resume = (
            {"kind": "poll_batch", "batch_ref": resume_contract["batch_ref"]}
            if resume_contract.get("poll_action") == "poll_batch" else
            {"kind": "poll", "question_ref": resume_contract["question_ref"]}
        )
        return validate_v11_response("coordinator.question_management", {
            "schema": "cortex/question-management/v11",
            "ok": True,
            "outcome": "question_answered",
            "resume": resume,
        })
    elif status_value == "awaiting_translation":
        batch_ref = str(result.get("batch_ref") or "").strip()
        question_ref = str(result.get("question_id") or "").strip()
        translation: dict[str, Any]
        if result.get("batch_ref"):
            required_keys = [str(item) for item in result.get("translation_required_for") or []]
            originals = result.get("answer_custom_original")
            originals = originals if isinstance(originals, dict) else result.get("answer_original")
            originals = originals if isinstance(originals, dict) else {}
            source_text_by_question = {
                key: str(originals.get(key) or "")[:8000]
                for key in required_keys
                if str(originals.get(key) or "").strip()
            }
            if not source_text_by_question:
                return _v11_question_management_failure(task_ref, "Translation source text is unavailable.")
            translation = {"batch_ref": batch_ref, "source_text_by_question": source_text_by_question}
        else:
            source_text = result.get("answer_original")
            if isinstance(source_text, (dict, list)):
                source_text = json.dumps(source_text, ensure_ascii=False, sort_keys=True)
            translation = {"question_ref": question_ref, "source_text": str(source_text or "")[:8000]}
        return validate_v11_response("coordinator.question_management", {
            "schema": "cortex/question-management/v11",
            "ok": True,
            "outcome": "awaiting_translation",
            "translation": translation,
        })
    elif status_value == "superseded":
        batch_ref = str(result.get("batch_ref") or "").strip()
        if not batch_ref:
            return _v11_question_management_failure(task_ref, "The superseded question is not resumable.")
        return validate_v11_response("coordinator.question_management", {
            "schema": "cortex/question-management/v11",
            "ok": True,
            "outcome": "batch_superseded",
            "batch_ref": batch_ref,
        })
    elif status_value in {"invalid_answer", "pending_user_input", "pending_user_message"}:
        question = _v11_question_display(result)
        if question is None:
            return _v11_question_management_failure(task_ref, "The display question is unavailable.")
        batch_ref = str(result.get("batch_ref") or "").strip()
        if batch_ref:
            progress = _v11_question_progress(result.get("progress"))
            if progress is None:
                return _v11_question_management_failure(task_ref, "Question batch progress is unavailable.")
            return validate_v11_response("coordinator.question_management", {
                "schema": "cortex/question-management/v11",
                "ok": True,
                "outcome": "awaiting_user",
                "batch_ref": batch_ref,
                "progress": progress,
                "question": question,
            })
        question_ref = str(result.get("question_id") or "").strip()
        return validate_v11_response("coordinator.question_management", {
            "schema": "cortex/question-management/v11",
            "ok": True,
            "outcome": "awaiting_user",
            "question_ref": question_ref,
            "question": question,
        })
    return _v11_question_management_failure(task_ref, "Question management returned no supported public state.")


def _v11_plan_approval_payload(value: object) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("plan_approval requires a payload object")
    payload = dict(value)
    localization_fields = {
        "localized_prompt", "localized_title", "localized_approve", "localized_cancel",
        "localized_custom_label",
    }
    unknown = sorted(set(payload) - {"decision", "approval_mode", "feedback", "request_id", *localization_fields})
    if unknown:
        raise ValueError("unsupported plan_approval payload fields: " + ", ".join(unknown))
    raw = str(payload.get("decision") or "prompt").strip().lower().replace("-", "_").replace(" ", "_")
    decision = {
        "prompt": "prompt", "ask": "prompt", "review": "prompt",
        "approve": "approve", "approved": "approve", "accept": "approve",
        "approve_with_recommendations": "approve_with_recommendations",
        "approve_without_recommendations": "approve_without_recommendations",
        "cancel": "cancel", "canceled": "cancel", "cancelled": "cancel",
        "revise": "revise", "changes": "revise", "request_changes": "revise",
    }.get(raw)
    if not decision:
        raise ValueError("plan_approval decision must be prompt, approve_with_recommendations, approve_without_recommendations, cancel, or revise")
    feedback = str(payload.get("feedback") or "").strip()
    request_id = str(payload.get("request_id") or "").strip()
    approval_mode = str(payload.get("approval_mode") or "").strip().lower().replace("-", "_")
    if approval_mode and approval_mode not in {"approve_with_recommendations", "approve_without_recommendations"}:
        raise ValueError("plan_approval approval_mode must be approve_with_recommendations or approve_without_recommendations")
    if decision == "prompt" and (feedback or request_id):
        raise ValueError("plan_approval prompt does not accept feedback")
    if decision == "revise" and not feedback:
        raise ValueError("plan_approval revise requires non-empty feedback")
    if decision != "prompt" and any(str(payload.get(field) or "").strip() for field in localization_fields):
        raise ValueError("plan approval localization fields are accepted only with decision=prompt")
    normalized = {
        "decision": "approve" if decision.startswith("approve_") else decision,
        **({"approval_mode": decision} if decision.startswith("approve_") else ({"approval_mode": approval_mode or "approve_with_recommendations"} if decision == "approve" else {})),
        **({"feedback": feedback} if feedback else {}),
        **({"request_id": redact(request_id, 200)} if request_id else {}),
    }
    for field in localization_fields:
        if str(payload.get(field) or "").strip():
            normalized[field] = redact(str(payload[field]).strip(), 300)
    return normalized


def _v11_plan_approval_request_id(state: dict[str, Any], approval: dict[str, Any]) -> str:
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
    return "approval-" + digest_text(canonical_json.dumps(seed))[:32]


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


def _v11_plan_approval_copy(state: dict[str, Any], localization: dict[str, Any]) -> tuple[str, str, str, str, str]:
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


def _v11_prompt_plan_approval(
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
    prompt, title, approve_label, cancel_label, custom_label = _v11_plan_approval_copy(state, localization)
    request_id = str(approval.get("request_id") or _v11_plan_approval_request_id(state, approval))
    planner_recommendation = str(review.get("recommendation") or "approve").strip().lower()
    recommendation_actions = list(review.get("recommendation_actions") or [])
    # The planner owns the recommendation. Findings/uncertainty are not a
    # second recommendation: when the planner supplied concrete corrective
    # actions, the single coherent recommendation is approve-with-actions.
    if recommendation_actions:
        recommended_decision = "approve_with_recommendations"
    elif planner_recommendation == "revise":
        recommended_decision = "revise"
    else:
        recommended_decision = "approve_without_recommendations"
    recommendation_rationale = (
        str(
            review.get("recommendation_rationale")
            or (
                "Approve with the planner's concrete corrective actions; each action is bound to plan items and verification."
                if recommendation_actions else
                "Request a planner revision because no concrete corrective action was supplied."
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
            "recommendation": review.get("recommendation") or "approve",
            "recommendation_rationale": review.get("recommendation_rationale") or "",
            "recommendation_actions": recommendation_actions,
            "result_ref": review.get("result_ref"),
        },
        "choices": [
            {"id": "approve_with_recommendations", "label": "Утвердить с рекомендациями" if str(state.get("user_language") or "").lower().startswith("ru") else "Approve with recommendations", "meaning": "Approve the plan and require the concrete corrective actions in recommendation_actions."},
            {"id": "approve_without_recommendations", "label": "Утвердить без рекомендаций" if str(state.get("user_language") or "").lower().startswith("ru") else "Approve without recommendations", "meaning": "Approve the plan without applying its advisory recommendations."},
            {"id": "revise", "label": custom_label, "meaning": "Describe the required changes; Cortex will preserve the exact text as Planner feedback and request a revised plan."},
            {"id": "cancel", "label": cancel_label, "meaning": "Keep the plan pending and stop until a later user message."},
        ],
        "actions": [
            {
                "id": "approve_with_recommendations",
                "arguments": {
                    "project_root": project_root,
                    "task_ref": task_ref,
                    "intent": "plan_approval",
                    "payload": {"decision": "approve_with_recommendations", "request_id": request_id},
                },
            },
            {
                "id": "approve_without_recommendations",
                "arguments": {
                    "project_root": project_root,
                    "task_ref": task_ref,
                    "intent": "plan_approval",
                    "payload": {"decision": "approve_without_recommendations", "request_id": request_id},
                },
            },
            {
                "id": "revise",
                "arguments": {
                    "project_root": project_root,
                    "task_ref": task_ref,
                    "intent": "plan_approval",
                    "payload": {"decision": "revise", "request_id": request_id, "feedback": "<describe concrete plan changes>"},
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
            "Use approve_with_recommendations or approve_without_recommendations for the two approval modes. "
            "Any substantive change request is treated as revise, not approval."
        ),
        "coordinator_contract": (
            "Use only interaction.user_view for the final ordinary user-language message. Never copy internal plan "
            "objects, paths, dependencies, result or request identifiers, dispatch instructions, or validation details "
            "into that message. Show its bounded summary, the single question, and the recommendation from "
            "llm_recommendation; then wait for one unambiguous approve-with-recommendations, approve-without-recommendations, revise, or cancel response. Preserve requested "
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
    for action in (review.get("recommendation_actions") or [])[:5]:
        if isinstance(action, dict):
            public_steps.append(
                (("Рекомендация: " + str(action.get("action") or "").strip()) if is_ru else
                 ("Recommendation: " + str(action.get("action") or "").strip()))
            )
    action_count = len(review.get("recommendation_actions") or [])
    public_recommendation = (
        ("Рекомендация планировщика: утвердить с рекомендациями — план содержит " + str(action_count) + " конкретных корректирующих действий." if is_ru else "Planner recommendation: approve with recommendations — the plan contains " + str(action_count) + " concrete corrective actions.")
        if recommended_decision == "approve_with_recommendations" and action_count else
        "Рекомендация: доработать план — планировщик не передал конкретные действия для исправления." if is_ru and recommended_decision == "revise" else
        "Рекомендация: утвердить без рекомендаций — план готов к выполнению." if is_ru else
        "Recommendation: revise — the planner did not provide concrete corrective actions." if recommended_decision == "revise" else
        "Recommendation: approve without recommendations — the plan is ready for execution."
    )
    interaction["user_view"] = render_plan(
        str(review.get("summary") or review.get("objective") or ("Проверка плана" if is_ru else "Plan review")),
        public_steps,
        question=("Утвердить с рекомендациями, утвердить без рекомендаций, доработать или отменить?" if is_ru else "Approve with recommendations, approve without recommendations, request a revision, or cancel?"),
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
        next_step=("Выберите утверждение с рекомендациями, без рекомендаций, доработку или отмену." if is_ru else "Choose approve with recommendations, approve without recommendations, revise, or cancel."),
        config=plan_config,
    )
    why_rendered = render(
        "Решение определяет, можно ли перейти к выполнению плана." if is_ru else
        "Your decision determines whether the plan can move to implementation.",
        kind="question",
        next_step=("Выберите утверждение с рекомендациями, без рекомендаций, доработку или отмену." if is_ru else "Choose approve with recommendations, approve without recommendations, revise, or cancel."),
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


def _v11_follow_up_payload(value: object) -> dict[str, Any]:
    """Normalize a user-authored corrective task without reopening its source."""
    if not isinstance(value, dict):
        raise ValueError("follow_up requires payload with the exact corrective user_request")
    allowed = {
        "user_request", "requirements", "constraints", "acceptance_criteria", "scope", "allowed_paths",
        "verification", "budget", "pause_conditions", "user_language", "language",
        "complexity", "plan_approval", "result_refs", "task_ref",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unsupported follow_up payload fields: " + ", ".join(unknown))
    user_request = str(value.get("user_request") or "").strip()
    if not user_request:
        raise ValueError("follow_up payload.user_request must preserve the exact corrective user request")
    result_refs = value.get("result_refs", [])
    if not isinstance(result_refs, list):
        raise ValueError("follow_up payload.result_refs must be an array of source result refs")
    normalized_refs = [safe_id(str(item)) for item in result_refs]
    if not all(normalized_refs) or len(normalized_refs) != len(set(normalized_refs)):
        raise ValueError("follow_up payload.result_refs must contain unique non-empty source result refs")
    task = {key: item for key, item in value.items() if key not in {"result_refs", "task_ref"}}
    task["user_request"] = user_request
    return {"task": task, "result_refs": normalized_refs}


def _v11_follow_up_context(
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


def _v11_active_steer(
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
        loaded = _v11_task_state(root, state["task_id"])
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
        # Governance escalation and a revision whose preferred gate is absent
        # are advisory findings.  They must never manufacture a replacement
        # wave or rewrite the coordinator's chosen pipeline.  The old code
        # called ``_revision_replacement_waves`` here, which silently injected
        # policy/governance work behind the orchestrator's back and left the
        # engine waiting for a route the coordinator had not selected.
        if governance_escalated:
            impact["pipeline_recommendation"] = {
                "code": "governance_escalation_advisory",
                "severity": "high",
                "recommended_action": "review the governance recommendation and continue the chosen pipeline",
                "chosen_pipeline_unchanged": True,
            }
        if impact.get("required_gate_missing"):
            impact["pipeline_deviation"] = {
                "code": "required_gate_missing_advisory",
                "severity": "warning",
                "gate": str(impact.get("earliest_affected_gate") or ""),
                "recommended_action": "record the missing gate and let the orchestrator decide whether to revise the pipeline",
                "chosen_pipeline_unchanged": True,
            }
        plan_revision = None
        if impact.get("requires_plan_revision"):
            plan_revision = db_append_plan_revision(
                root, state["task_id"], task_revision=revision_number,
                impact=impact, plan=plan, status="active",
            )
        earliest_affected = str(impact.get("earliest_affected_gate") or "")
        active_gate = current_gates[0] if current_gates else ""
        if earliest_affected and earliest_affected in (state.get("current_pipeline") or []) and (
            earliest_affected != active_gate or not active_attempts
        ):
            # Preserve the same live worker for the user's steer.  The engine
            # consumes this receipt after that worker AttemptResult and may
            # reopen only an existing affected gate before downstream dispatch.
            # Governance escalation never creates a server-owned replacement
            # route; the coordinator remains free to keep its chosen pipeline.
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


def _v11_finalize_bootstrap_failure(
    *,
    task_dir: Path,
    state: dict[str, Any],
    task_ref: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Terminally close the one never-authorized native worker after bootstrap loss.

    This is intentionally narrower than ordinary recovery: no worker was
    authorized to call Cortex, so it must not manufacture an AttemptResult,
    briefing receipt, event, repair escrow, or replacement dispatch.  The
    coordinator can only bind the server-issued dispatch already awaiting its
    native spawn acknowledgement.
    """
    payload = params.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("finalize_bootstrap_failure requires payload")
    unknown = sorted(set(payload) - {"dispatch_ref", "reason_code"})
    if unknown:
        raise ValueError("finalize_bootstrap_failure payload has unsupported fields")
    dispatch_ref = str(payload.get("dispatch_ref") or "").strip()
    reason_code = str(payload.get("reason_code") or "").strip()
    if not re.fullmatch(r"dispatch-[0-9a-f]{24}", dispatch_ref):
        raise ValueError("finalize_bootstrap_failure requires the exact server-issued dispatch_ref")
    if reason_code != "bootstrap_missing_identity":
        raise ValueError("finalize_bootstrap_failure reason_code is invalid")

    root = ledger_root(params)
    task_id = str(state.get("task_id") or "")
    with state_lock(root, operation="finalize_bootstrap_failure", task_id=task_id):
        # Resolution happened before taking the mutation lock. Reload the
        # canonical projection while holding it so a stale coordinator cannot
        # terminalize a different/replaced attempt.
        fresh = _v11_task_state(root, task_id)
        if fresh is None:
            raise ValueError("bootstrap failure task is unavailable")
        fresh_dir, fresh_state, _fresh_task = fresh
        if fresh_dir != task_dir:
            raise ValueError("bootstrap failure task projection is inconsistent")
        matching = [
            item for item in fresh_state.get("attempts") or []
            if isinstance(item, dict) and str(item.get("dispatch_ref") or "") == dispatch_ref
        ]
        prior = fresh_state.get("bootstrap_terminal_failure")
        if isinstance(prior, dict) and prior == {
            "dispatch_ref": dispatch_ref,
            "reason_code": reason_code,
        }:
            return {
                "schema": LIFECYCLE_RUNTIME_SCHEMA,
                "ok": True,
                "lifecycle": "finalize_bootstrap_failure",
                "transaction_id": None,
                "task_id": task_id,
                "wave_id": None,
                "state": "bootstrap_terminal_failure",
                "spawn_requests": [],
                "diagnostics": [],
                "result": {"finalized": True, "idempotent": True},
                "code": "bootstrap_terminal_failure",
                "recoverable": False,
                "next_action": "Bootstrap failure is already terminal; do not spawn, continue, or read a worker result for this dispatch.",
            }
        if len(matching) != 1:
            raise ValueError("bootstrap failure dispatch does not identify exactly one task attempt")
        attempt = matching[0]
        # A server-issued briefing receipt proves that this is no longer a
        # bootstrap-only failure.  Never let the coordinator relabel a later
        # worker tool/schema problem as missing bootstrap identity: that would
        # discard a valid explicit authorization and strand the real protocol
        # state under the wrong terminal reason.
        attempt_id = str(attempt.get("attempt_id") or "")
        receipts = attempt_protocol.attempt_receipts(
            root,
            task_id=task_id,
            attempt_id=attempt_id,
        )
        post_bootstrap_evidence = (
            bool(receipts.get("briefing_receipt"))
            or bool(attempt_protocol.list_attempt_events(root, task_id=task_id, attempt_id=attempt_id))
            or db_get_pending_repair_escrow(root, task_id=task_id, attempt_id=attempt_id) is not None
        )
        if (
            fresh_state.get("status") != "active"
            or attempt.get("status") != AWAITING_HOST_SPAWN
            or attempt.get("lifecycle_status") != "awaiting_spawn_ack"
            or attempt.get("assignment_delivery_status") not in {"pending", "delivered"}
            or post_bootstrap_evidence
        ):
            raise ValueError("bootstrap failure target has post-bootstrap server evidence; follow the returned structured recovery on the same worker")

        terminal_at = now()
        attempt["status"] = "failed"
        attempt["lifecycle_status"] = "bootstrap_terminal_failure"
        attempt["assignment_delivery_status"] = "bootstrap_terminal_failure"
        attempt["host_resumable"] = False
        attempt["finalized_at"] = terminal_at
        attempt["finalization_reason"] = reason_code
        fresh_state["status"] = "blocked"
        fresh_state["blocked_gate"] = str(attempt.get("gate") or "")
        fresh_state["blocked_reason"] = reason_code
        fresh_state["bootstrap_terminal_failure"] = {
            "dispatch_ref": dispatch_ref,
            "reason_code": reason_code,
        }
        # Both writes run in the state_lock-owned SQLite transaction: a
        # coordinator cannot observe a blocked task with a resumable native
        # worker (or vice versa).
        db_put_worker_session(root, {
            "task_id": task_id,
            "attempt_id": str(attempt.get("attempt_id") or ""),
            "host_task_name": str((attempt.get("spawn_request") or {}).get("task_name") or ""),
            "host_tool": str((attempt.get("spawn_request") or {}).get("host_tool") or "spawn_agent"),
            "status": "terminated_unavailable",
            "resumable": False,
            "started_at": attempt.get("spawn_requested_at"),
            "terminated_at": terminal_at,
        })
        save_state(
            fresh_dir,
            fresh_dir / "state.sqlite",
            fresh_state,
            "bootstrap_terminal_failure",
            "awaiting native worker bootstrap could not be authorized",
        )
    return {
        "schema": LIFECYCLE_RUNTIME_SCHEMA,
        "ok": True,
        "lifecycle": "finalize_bootstrap_failure",
        "transaction_id": None,
        "task_id": task_id,
        "wave_id": None,
        "state": "bootstrap_terminal_failure",
        "spawn_requests": [],
        "diagnostics": [],
        "result": {"finalized": True, "idempotent": False},
        "code": "bootstrap_terminal_failure",
        "recoverable": False,
        "next_action": "Bootstrap failure is terminal; do not spawn, continue, or read a worker result for this dispatch.",
    }


def _v11_finalize_worker_failure(
    *,
    task_dir: Path,
    state: dict[str, Any],
    task_ref: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Consume current server evidence and close its exact worker once."""
    if str(params.get("reason") or "").strip():
        raise ValueError("finalize_worker_failure accepts only the fixed sanitized reason_code")
    payload = params.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("finalize_worker_failure requires payload")
    if sorted(set(payload) - {"dispatch_ref", "reason_code"}):
        raise ValueError("finalize_worker_failure payload has unsupported fields")
    dispatch_ref = str(payload.get("dispatch_ref") or "").strip()
    reason_code = str(payload.get("reason_code") or "").strip()
    if not re.fullmatch(r"dispatch-[0-9a-f]{24}", dispatch_ref):
        raise ValueError("finalize_worker_failure requires the exact server-issued dispatch_ref")
    if reason_code != "worker_nonretryable_terminal":
        raise ValueError("finalize_worker_failure reason_code is invalid")

    root = ledger_root(params)
    task_id = str(state.get("task_id") or "")
    expired_evidence = False
    with state_lock(root, operation="finalize_worker_failure", task_id=task_id):
        fresh = _v11_task_state(root, task_id)
        if fresh is None:
            raise ValueError("worker failure task is unavailable")
        fresh_dir, fresh_state, _fresh_task = fresh
        if fresh_dir != task_dir:
            raise ValueError("worker failure task projection is inconsistent")
        matching = [
            item for item in fresh_state.get("attempts") or []
            if isinstance(item, dict) and str(item.get("dispatch_ref") or "") == dispatch_ref
        ]
        if len(matching) != 1:
            raise ValueError("worker failure dispatch does not identify exactly one task attempt")
        attempt = matching[0]
        attempt_id = str(attempt.get("attempt_id") or "")
        claim = attempt.get("worker_assignment") if isinstance(attempt.get("worker_assignment"), dict) else {}
        assignment_generation = int(claim.get("generation") or 0)
        evidence = db_get_task_document(root, task_id, TERMINAL_FAILURE_EVIDENCE_KEY)
        expected_evidence_fields = {
            "schema", "task_id", "attempt_id", "dispatch_ref", "assignment_generation",
            "operation", "error_category", "error_code", "issued_at", "expires_at", "updated_at",
        }
        if not isinstance(evidence, dict) or set(evidence) != expected_evidence_fields:
            raise ValueError("worker terminal failure has no current server-bound evidence")
        error_category = str(evidence.get("error_category") or "")
        error_code = str(evidence.get("error_code") or "")
        operation = str(evidence.get("operation") or "")
        try:
            expires_at = datetime.fromisoformat(str(evidence.get("expires_at") or ""))
            if expires_at.tzinfo is None:
                raise ValueError("naive expiry")
        except (TypeError, ValueError) as exc:
            raise ValueError("worker terminal failure evidence expiry is invalid") from exc
        if (
            evidence.get("schema") != TERMINAL_FAILURE_EVIDENCE_SCHEMA
            or str(evidence.get("task_id") or "") != task_id
            or str(evidence.get("attempt_id") or "") != attempt_id
            or str(evidence.get("dispatch_ref") or "") != dispatch_ref
            or int(evidence.get("assignment_generation") or 0) != assignment_generation
            or error_category not in TERMINAL_FAILURE_CATEGORIES
            or error_code not in TERMINAL_FAILURE_CODES_BY_OPERATION.get(operation, frozenset())
        ):
            raise ValueError("worker terminal failure evidence is stale or bound to another assignment")
        if expires_at <= datetime.now(timezone.utc):
            # Commit deletion of this exact, fully bound expired control
            # record, then report the lifecycle rejection outside the
            # transaction. Raising here would roll the bounded cleanup back.
            if not db_delete_task_document(root, task_id, TERMINAL_FAILURE_EVIDENCE_KEY):
                raise ValueError("worker terminal failure evidence was already consumed")
            expired_evidence = True
        else:
            sessions = [
                item for item in db_list_worker_sessions(root, task_id)
                if str(item.get("attempt_id") or "") == attempt_id
            ]
            if (
                fresh_state.get("status") != "active"
                or attempt.get("invalidated")
                or attempt.get("status") not in {AWAITING_HOST_SPAWN, "running", "waiting_question"}
                or attempt.get("assignment_delivery_status") != "delivered"
                or str(attempt.get("gate") or "") not in set(active_gates(fresh_state))
                or attempt.get("attempt_result_ref")
                or attempt_protocol.get_attempt_result(root, task_id=task_id, attempt_id=attempt_id) is not None
                or not sessions
                or not any(bool(item.get("resumable")) for item in sessions)
            ):
                raise ValueError("worker failure target is not the current bound nonterminal assignment")

            # Consumption and task terminalization share the same SQLite-backed
            # state lock transaction. A crash cannot publish a blocked task while
            # leaving replayable control evidence, or consume evidence without the
            # corresponding terminal state transition.
            if not db_delete_task_document(root, task_id, TERMINAL_FAILURE_EVIDENCE_KEY):
                raise ValueError("worker terminal failure evidence was already consumed")

            terminal_at = now()
            attempt["status"] = "failed"
            attempt["lifecycle_status"] = "worker_terminal_failure"
            attempt["assignment_delivery_status"] = "worker_terminal_failure"
            attempt["host_resumable"] = False
            attempt["finalized_at"] = terminal_at
            attempt["finalization_reason"] = reason_code
            fresh_state["status"] = "blocked"
            fresh_state["blocked_gate"] = str(attempt.get("gate") or "")
            fresh_state["blocked_reason"] = reason_code
            fresh_state["worker_terminal_failure"] = {
                "dispatch_ref": dispatch_ref,
                "reason_code": reason_code,
                "error_category": error_category,
                "error_code": error_code,
            }
            for session in sessions:
                db_put_worker_session(root, {
                    **session,
                    "status": "terminated_unavailable",
                    "resumable": False,
                    "terminated_at": terminal_at,
                })
            save_state(
                fresh_dir,
                fresh_dir / "state.sqlite",
                fresh_state,
                "worker_terminal_failure",
                "current native worker returned a nonretryable terminal failure",
            )
    if expired_evidence:
        raise ValueError("worker terminal failure evidence is expired")
    return {
        "schema": LIFECYCLE_RUNTIME_SCHEMA,
        "ok": True,
        "lifecycle": "finalize_worker_failure",
        "transaction_id": None,
        "task_id": task_id,
        "wave_id": None,
        "state": "worker_terminal_failure",
        "spawn_requests": [],
        "diagnostics": [],
        "result": {"finalized": True, "idempotent": False},
        "code": "worker_terminal_failure",
        "recoverable": False,
        "next_action": "Worker failure is terminal; do not continue or read a worker result for this dispatch.",
    }


def _manage_orchestration_input_diagnostics(params: Any) -> list[dict[str, Any]]:
    """Validate the exact selected public management-union branch."""
    schema = PUBLIC_SCHEMA_REGISTRY.get("manage_orchestration", {})
    diagnostics = _v11_schema_value_diagnostics(
        params, schema, pointer="", operation="manage_orchestration",
    )
    if not isinstance(params, dict):
        return diagnostics

    branches = schema.get("oneOf") if isinstance(schema, Mapping) else None
    intents = sorted({
        str(intent_schema["const"])
        for branch in branches or []
        if isinstance(branch, Mapping)
        for properties in [branch.get("properties")]
        if isinstance(properties, Mapping)
        for intent_schema in [properties.get("intent")]
        if isinstance(intent_schema, Mapping) and isinstance(intent_schema.get("const"), str)
    })
    intent = params.get("intent")
    if "intent" in params and (not isinstance(intent, str) or intent not in intents):
        diagnostics = [item for item in diagnostics if item.get("json_pointer") != "/intent"]
        intent_schema = {"type": "string", "enum": intents}
        diagnostics.insert(0, {
            "code": "manage_orchestration_validation_failed", "phase": "payload",
            "path": "intent", "json_pointer": "/intent",
            "message": "must be one canonical published intent", "received": intent,
            "expected": intent_schema, "field_schema": intent_schema,
            "fix": "Replace /intent with one exact enum value; aliases are not accepted.",
        })
    return diagnostics


def _manage_orchestration_impl(params: dict[str, Any]) -> dict[str, Any]:
    """Keep recovery and rare control-plane capabilities outside the normal flow."""
    resolved_task_ref = str(params.get("task_ref") or "").strip() or None if isinstance(params, dict) else None
    caller_supplied_project_root = isinstance(params, dict) and "project_root" in params
    try:
        envelope = _manage_orchestration_input_diagnostics(params)
        if envelope:
            response = _v11_envelope_error("manage_orchestration", envelope)
            response["outcome"] = "needs_correction"
            return response
        management_schema = PUBLIC_SCHEMA_REGISTRY.get("manage_orchestration", {})
        canonical_intents = {
            str(intent_schema["const"])
            for branch in management_schema.get("oneOf", [])
            if isinstance(branch, Mapping)
            for properties in [branch.get("properties")]
            if isinstance(properties, Mapping)
            for intent_schema in [properties.get("intent")]
            if isinstance(intent_schema, Mapping) and isinstance(intent_schema.get("const"), str)
        }
        if not isinstance(params.get("intent"), str) or not params.get("intent").strip():
            diagnostics = [{
                "code": "manage_orchestration_validation_failed",
                "phase": "payload",
                "path": "intent",
                "json_pointer": "/intent",
                "message": "intent is required",
                "received": params.get("intent"),
                "expected": sorted(canonical_intents),
                "field_schema": {"type": "string", "enum": sorted(canonical_intents)},
                "fix": "Set intent to one canonical value from diagnostics.expected; do not omit it.",
            }]
            response = _v11_envelope_error("manage_orchestration", diagnostics, task_ref=resolved_task_ref)
            response["outcome"] = "needs_correction"
            return response
        # Canonical public enum values are case- and punctuation-sensitive;
        # accepting normalized spellings here would silently revive the
        # Management operation names are canonical and case-sensitive.
        intent_raw = str(params.get("intent") or "inspect").strip()
        intent = intent_raw if intent_raw in canonical_intents else None
        if intent is None:
            diagnostics = [{
                "code": "manage_orchestration_validation_failed",
                "phase": "payload",
                "path": "intent",
                "json_pointer": "/intent",
                "message": "intent is not a canonical management operation",
                "received": params.get("intent"),
                "expected": sorted(canonical_intents),
                "field_schema": {"type": "string", "enum": sorted(canonical_intents)},
                "fix": "Replace intent with one canonical value from diagnostics.expected; aliases are not accepted.",
            }]
            response = _v11_envelope_error("manage_orchestration", diagnostics, task_ref=resolved_task_ref)
            response["outcome"] = "needs_correction"
            return response
        coordinator_project, _coordinator_task_dir, _coordinator_state, _coordinator_task, _ = authorize_coordinator_ref(
            params, "manage_orchestration",
        )
        params = {**params, "project_root": str(coordinator_project)}
        if intent == "recover_blocked" and "payload" in params:
            diagnostics = [{
                "code": "manage_orchestration_validation_failed",
                "phase": "payload",
                "path": "payload",
                "json_pointer": "/payload",
                "message": "recover_blocked is server-owned and does not accept payload",
                "received": params.get("payload"),
                "expected": "omit payload; Cortex derives the recovery scope from task_ref",
                "field_schema": {"not": {"required": ["payload"]}},
                "fix": "Remove payload and retry recover_blocked with the same task_ref and coordinator_ref; Cortex derives the corrective dispatch.",
            }]
            response = _v11_envelope_error("manage_orchestration", diagnostics, task_ref=resolved_task_ref)
            response["outcome"] = "needs_correction"
            return response
        if not str(params.get("task_ref") or "").strip():
            return _v11_task_ref_required_error(f"manage_orchestration intent '{intent}'")
        bound_params = _bind_task_project_root(
            params,
            include_completed=intent in {"inspect", "recover_inspect", "deactivate", "follow_up", "finalize_bootstrap_failure", "finalize_worker_failure"},
        )
        if bound_params is None:
            return _v11_error(
                "task_scope_unavailable",
                "task_ref could not be resolved to one canonical project root; use the exact task_ref returned by start_orchestration",
                outcome="needs_input",
                task_ref=resolved_task_ref,
            )
        params = bound_params
        resolved = _v11_resolve_task(
            params,
            include_completed=bool(str(params.get("task_ref") or "").strip()) and intent in {"inspect", "recover_inspect", "deactivate", "follow_up", "finalize_bootstrap_failure", "finalize_worker_failure"},
            require_task_ref=True,
        )
        if isinstance(resolved, dict):
            return resolved
        task_dir, state, task_definition, task_ref = resolved
        resolved_task_ref = task_ref
        if intent == "artifacts":
            return manage_task_artifacts(params, task_dir, state, task_ref)
        if intent == "finalize_bootstrap_failure":
            finalized = _v11_finalize_bootstrap_failure(
                task_dir=task_dir,
                state=state,
                task_ref=task_ref,
                params=params,
            )
            return _v11_response(finalized, task_ref, include_result=True)
        if intent == "finalize_worker_failure":
            finalized = _v11_finalize_worker_failure(
                task_dir=task_dir,
                state=state,
                task_ref=task_ref,
                params=params,
            )
            return _v11_response(finalized, task_ref, include_result=True)
        if intent == "steer":
            return _v11_active_steer(params, task_dir, state, task_definition, task_ref)
        if intent == "follow_up":
            follow_up = _v11_follow_up_payload(params.get("payload"))
            source_context = _v11_follow_up_context(
                task_dir,
                state,
                task_definition,
                task_ref,
                follow_up["result_refs"],
            )
            follow_up_task = dict(follow_up["task"])
            follow_up_task["user_language"] = task_definition.get("user_language") or state.get("user_language") or "en"
            # Follow-up creation is a new task, but retries of the same
            # completed-source operation must not create duplicate corrective
            # tasks.  Supply a server-owned transport identity derived from
            # the immutable source task, selected result refs, and exact
            # corrective payload.  It is intentionally not exposed to the
            # public start schema or copied from caller input.
            follow_up_request_id = "follow-up-" + digest_text(_orchestrate_request_digest({
                "source_task_id": state["task_id"],
                "source_result_refs": source_context["source_result_refs"],
                "corrective_task": follow_up_task,
            }))[:48]
            started = start_orchestration({
                "project_root": params["project_root"],
                "task": follow_up_task,
                "_follow_up": source_context,
                "_transport_request_id": follow_up_request_id,
            })
            # ``start_orchestration`` already returns the only admissible
            # start-delivery envelope.  Do not append source-task metadata or
            # prose: that would invalidate the closed union and could strip
            # the new task's one-shot coordinator capability on re-projection.
            return started
        common = {
            "project_root": params["project_root"],
            "principal": state.get("principal"),
            "task_id": state["task_id"],
        }
        if intent == "inspect":
            return _v11_response(_engine_inspect_lifecycle(common), task_ref, include_result=True)
        if intent == "recover_inspect":
            # Lifecycle recovery is deliberately distinct from ordinary
            # inspection: status reads must not contend on the mutation lock
            # or silently expire/retire attempts.  The server derives the
            # exact repair scope from current durable state; callers cannot
            # select attempts, receipts, or identities to mutate.
            return _v11_response(
                _engine_inspect_lifecycle({**common, "payload": {"mode": "recover_lifecycle"}}),
                task_ref,
                include_result=True,
            )
        normalized_payload = None
        if intent == "question":
            normalized_payload = _v11_question_management_payload(params.get("payload"))
            if normalized_payload.get("command") == "answer":
                normalized_payload["resume_context"] = {
                    "source": "manage_orchestration",
                    "user_language": str(state.get("user_language") or "en"),
                }
        elif intent == "plan_approval":
            normalized_payload = _v11_plan_approval_payload(params.get("payload"))
            if normalized_payload["decision"] == "prompt":
                normalized_payload, prompt_response = _v11_prompt_plan_approval(
                    state,
                    task_ref,
                    normalized_payload,
                    str(params.get("project_root") or ""),
                )
                if prompt_response is not None:
                    prompt_response["next_action"] = _public_next_action(prompt_response.get("next_action"))
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
        if intent in {"resume", "recover_blocked", "deactivate"}:
            if intent == "recover_blocked":
                old = _engine_manage_lifecycle("resume", {
                    **common,
                    "submission_id": submission_id,
                    "reason": params.get("reason") or "Server-derived corrective recovery for terminal worker result.",
                    "terminal_recovery": True,
                })
            else:
                if intent == "resume" and params.get("payload") is not None:
                    raise ValueError("resume is server-derived and does not accept a caller-authored payload")
                old = _engine_manage_lifecycle(intent, {
                    **common,
                    "submission_id": submission_id,
                    "reason": params.get("reason"),
                })
        else:
            payload = normalized_payload if normalized_payload is not None else params.get("payload")
            if not isinstance(payload, dict):
                raise ValueError(f"{intent} management requires payload")
            old = _engine_manage_lifecycle(intent, {
                **common,
                "submission_id": submission_id,
                "payload": payload,
            })
        if intent == "question":
            return _v11_question_response(old, state, task_ref)
        response = _v11_response(old, task_ref, include_result=True)
        if intent == "plan_approval" and (old.get("result") or {}).get("decision") == "approved":
            response["approval_message"] = "Plan approved."
            response["next_action"] = (
                f"{COORDINATOR_LOCK} Tell the user in their language that the plan was approved, then execute every "
                "returned dispatch exactly once and continue the normal Cortex wave workflow."
            )
        return response
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        collected = getattr(exc, "diagnostics", None)
        diagnostics = collected if isinstance(collected, list) and collected else [{
            "code": "management_failed",
            "phase": "management",
            "message": redact(str(exc), 1000),
        }]
        error = _v11_error("management_failed", "management request validation failed", diagnostics=diagnostics)
        error["retryable"] = True
        error["attempt_budget_consumed"] = False
        error["worker_replacement_authorized"] = False
        error["next_action"] = _validation_next_action(
            "manage_orchestration",
            diagnostics,
            task_ref=resolved_task_ref,
            project_root=str(params.get("project_root") or "") or None,
        )
        error["validation"] = _validation_contract(
            "manage_orchestration",
            diagnostics,
            task_ref=resolved_task_ref,
            project_root=str(params.get("project_root") or "") or None,
        )
        if resolved_task_ref:
            error["task_ref"] = resolved_task_ref
        return error


def start_orchestration(params: dict[str, Any]) -> dict[str, Any]:
    """Return only the closed start response and its one-shot capability."""
    return project_public_response(
        "start_orchestration", _start_orchestration_impl(params), arguments=params,
    )


def continue_orchestration(params: dict[str, Any]) -> dict[str, Any]:
    """Return only the closed coordinator lifecycle response."""
    return project_public_response(
        "continue_orchestration", _continue_orchestration_impl(params), arguments=params,
    )


def manage_orchestration(params: dict[str, Any]) -> dict[str, Any]:
    """Return only the selected closed lifecycle or question-management response."""
    return project_public_response(
        "manage_orchestration", _manage_orchestration_impl(params), arguments=params,
    )


def _manage_governance_input_diagnostics(params: Any) -> list[dict[str, Any]]:
    """Validate the exact selected public governance-union branch."""
    schema = MANAGE_GOVERNANCE_SCHEMA if isinstance(globals().get("MANAGE_GOVERNANCE_SCHEMA"), dict) else {}
    diagnostics = _v11_schema_value_diagnostics(params, schema, pointer="", operation="manage_governance")
    if not isinstance(params, dict):
        return diagnostics
    branches = schema.get("oneOf") if isinstance(schema, Mapping) else None
    actions = sorted({
        str(action_schema["const"])
        for branch in branches or []
        if isinstance(branch, Mapping)
        for properties in [branch.get("properties")]
        if isinstance(properties, Mapping)
        for action_schema in [properties.get("action")]
        if isinstance(action_schema, Mapping) and isinstance(action_schema.get("const"), str)
    })
    action = params.get("action")
    if "action" in params and (not isinstance(action, str) or action not in actions):
        diagnostics = [
            item for item in diagnostics
            if item.get("json_pointer") != "/action"
            and (
                item.get("json_pointer") in {"/task_ref", "/coordinator_ref"}
                or item.get("received") is not None
            )
        ]
        action_schema = {"type": "string", "enum": actions}
        diagnostics.insert(0, {
            "code": "manage_governance_validation_failed", "phase": "payload",
            "path": "action", "json_pointer": "/action",
            "message": "must be one canonical published action", "received": action,
            "expected": action_schema, "field_schema": action_schema,
            "fix": "Replace /action with one exact enum value; compatibility aliases are not accepted.",
        })
    return diagnostics


def _manage_governance_validation_error(params: Any) -> dict[str, Any]:
    diagnostics = _manage_governance_input_diagnostics(params)
    result = _v11_envelope_error("manage_governance", diagnostics)
    result["schema"] = "cortex/validation-error/v1"
    result["outcome"] = "needs_correction"
    result["code"] = "manage_governance_validation_failed"
    result["next_action"] = (
        "Correct every listed diagnostics.path using its diagnostics.field_schema and diagnostics.expected, "
        "preserve all valid fields, then retry manage_governance once with the same request scope. "
        "No ledger mutation was performed."
    )
    result["validation"] = {
        **_validation_contract("manage_governance", diagnostics),
        "apply_all_diagnostics_atomically": True, "retry_same_request": True,
        "patch_paths": [item.get("json_pointer") for item in diagnostics if item.get("json_pointer")],
    }
    return result


_GOVERNANCE_DIAGNOSTIC_FIELDS: dict[str, tuple[str, dict[str, Any], str]] = {
    "task_scope_unavailable": ("task_ref", {"type": "string", "minLength": 1}, "Provide the exact active task_ref returned by start_orchestration."),
    "coordinator_authorization_required": ("coordinator_ref", {"type": "string", "pattern": COORDINATOR_REF_PATTERN}, "Retry with the exact task_ref and coordinator_ref returned by start_orchestration."),
    "coordinator_capability_required": ("coordinator_ref", {"type": "string", "pattern": COORDINATOR_REF_PATTERN}, "Provide the exact coordinator_ref returned by start_orchestration; no ambient identity can replace it."),
    "coordinator_capability_invalid": ("coordinator_ref", {"type": "string", "pattern": COORDINATOR_REF_PATTERN}, "Retry with the exact unmodified coordinator_ref for this task or fail closed with coordinator_capability_lost."),
    "coordinator_capability_scope_denied": ("action", {"type": "string", "minLength": 1}, "Choose an action allowed by the server-bound coordinator scope and retry the same task."),
    "coordinator_capability_action_denied": ("action", {"type": "string", "minLength": 1}, "Choose an action allowed by the server-bound coordinator scope and retry the same task."),
    "unknown_action": ("action", {"type": "string", "minLength": 1}, "Set action to one of the enum values published in the manage_governance tool schema."),
    "unsupported_fields": ("request", {"type": "object", "additionalProperties": False}, "Remove only the unsupported fields named by diagnostics and retry the same action."),
}


def _governance_error_receipt(exc: GovernanceError) -> dict[str, Any]:
    """Convert a service error into the common form-style correction receipt.

    Governance service exceptions are intentionally kept separate from MCP
    transport errors.  Callers get a stable path/schema/expected/fix tuple;
    internal authorization and ledger failures remain machine-readable and do
    not leak the coordinator-only lock text into the visible transcript.
    """
    code = str(exc.code or "governance_invalid")
    path, field_schema, fix = _GOVERNANCE_DIAGNOSTIC_FIELDS.get(
        code,
        ("request", {"type": "object", "additionalProperties": False}, "Retry the same governance action after applying the server-provided correction."),
    )
    diagnostic = {
        "code": code,
        "phase": "authorization" if code.startswith("coordinator_") else "service",
        "path": path,
        "json_pointer": "" if path == "request" else "/" + path.replace(".", "/"),
        "message": redact(str(exc), 1000),
        "received": None,
        "expected": field_schema.get("enum") or field_schema,
        "field_schema": field_schema,
        "fix": fix,
    }
    result = _v11_envelope_error("manage_governance", [diagnostic])
    result.update({
        "schema": "cortex/validation-error/v1",
        "outcome": "needs_correction" if not code.startswith("ledger_") else "recovery_advice",
        "code": code,
        "retryable": True,
        "attempt_budget_consumed": False,
        "worker_replacement_authorized": False,
        "next_action": f"Retry manage_governance with the same action after applying diagnostics[0].fix at diagnostics[0].json_pointer. {fix}",
        "validation": {
            "schema": "cortex/validation-error/v1",
            "diagnostics_are_complete": True,
            "apply_all_diagnostics_atomically": True,
            "retry_same_request": True,
            "patch_paths": [diagnostic["json_pointer"]] if diagnostic["json_pointer"] else [],
        },
    })
    return result


def _manage_governance_impl(params: dict[str, Any]) -> dict[str, Any]:
    """Expose the additive v11 governance ledger without widening lifecycle calls."""
    try:
        preflight = _manage_governance_input_diagnostics(params)
        if preflight:
            return _manage_governance_validation_error(params)
        governance_schema = PUBLIC_SCHEMA_REGISTRY.get("manage_governance", {})
        action = params.get("action")
        selected_branches = [
            branch
            for branch in governance_schema.get("oneOf", [])
            if isinstance(branch, Mapping)
            and isinstance(branch.get("properties"), Mapping)
            and isinstance(branch["properties"].get("action"), Mapping)
            and branch["properties"]["action"].get("const") == action
        ]
        governance_allowed = {
            field
            for branch in selected_branches
            for field in branch["properties"]
        }
        envelope = _v11_collect_fields(params, governance_allowed, operation="manage_governance")
        if not str(params.get("action") or "").strip():
            envelope.append({"code": "manage_governance_validation_failed", "path": "action", "message": "is required"})
        if envelope:
            return _v11_envelope_error("manage_governance", envelope)
        project, _task_dir, state, _task, _task_ref = authorize_coordinator_ref(
            params, "manage_governance",
        )
        unknown = sorted(set(params) - governance_allowed)
        if unknown:
            raise GovernanceError("unsupported governance fields: " + ", ".join(unknown), code="unsupported_fields")
        claims = _coordinator_capability_claims_for_task(
            ledger_root({"project_root": str(project)}),
            str(state["task_id"]),
        )
        if claims is None:
            raise GovernanceError(
                "active coordinator session has no valid server-owned claims",
                code="coordinator_authorization_required",
            )
        payload = {
            key: value
            for key, value in params.items()
            if key not in {"task_ref", "coordinator_ref"}
        }
        payload["created_by"] = str(claims.get("principal") or "coordinator")
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
                "source": "explicit_coordinator_ref",
                "capability_kind": claims["kind"],
                "generation": claims["generation"],
            },
            "result": result,
        }
    except GovernanceError as exc:
        return _governance_error_receipt(exc)
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        return {
            "schema": "cortex/governance/v1", "ok": False, "outcome": "needs_input", "code": "governance_failed",
            "diagnostics": [{"code": "governance_failed", "message": redact(str(exc), 1000)}],
            "next_action": f"{COORDINATOR_LOCK} Correct the governance request or result the bounded ledger error.",
        }


def manage_governance(params: dict[str, Any]) -> dict[str, Any]:
    """Return one closed governance receipt or explicit inspection projection."""
    return project_public_response(
        "manage_governance", _manage_governance_impl(params), arguments=params,
    )


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
    host_bootstrap_repair_message,
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

# Wrap the five assignment-bound public operations at the facade boundary.
# Their domain handlers stay focused on their own transaction; only this
# boundary may turn a verified terminal response into coordinator-consumable
# private control evidence.
_worker_question_operation = worker_question
_record_worker_attempt_event_operation = record_worker_attempt_event
_complete_worker_attempt_operation = complete_worker_attempt
_read_dispatch_briefing_operation = read_dispatch_briefing
_read_worker_result_operation = read_worker_result


def _run_worker_public_operation(
    operation: str,
    handler: Callable[[dict[str, Any]], dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    authority = None
    with contextlib.suppress(WorkerAssignmentError):
        authority = authorize_worker_assignment(params, operation)
    return _with_terminal_failure_evidence(
        operation, params, handler(params), authority=authority,
    )


def worker_question(params: dict[str, Any]) -> dict[str, Any]:
    return _run_worker_public_operation("worker_question", _worker_question_operation, params)


def record_worker_attempt_event(params: dict[str, Any]) -> dict[str, Any]:
    return _run_worker_public_operation(
        "record_attempt_event", _record_worker_attempt_event_operation, params,
    )


def complete_worker_attempt(params: dict[str, Any]) -> dict[str, Any]:
    return _run_worker_public_operation("complete_attempt", _complete_worker_attempt_operation, params)


def read_dispatch_briefing(params: dict[str, Any]) -> dict[str, Any]:
    return _run_worker_public_operation(
        "read_dispatch_briefing", _read_dispatch_briefing_operation, params,
    )


def read_worker_result(params: dict[str, Any]) -> dict[str, Any]:
    return _run_worker_public_operation("read_worker_result", _read_worker_result_operation, params)


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
        "turn_id": {"type": "string"},
        "question_id": {"type": "string", "description": "Existing worker question to surface and answer in the main chat."},
        "user_language": {"type": "string", "description": "Language requested by the user for the main-chat projection."},
        "communication_profile": {"type": "string", "enum": ["natural", "compact", "technical"], "default": "natural", "description": "User-facing message style for direct question projections; task state remains authoritative on the managed route."},
        "localized_question": {"type": "string", "description": "Main-coordinator display translation into the task's original user language; durable worker content remains English and unchanged."},
        "localized_header": {"type": "string"},
        "localized_options": {"type": "array", "maxItems": 32, "items": QUESTION_OPTION_SCHEMA},
        "localized_custom_label": {"type": "string"},
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
PUBLIC_SCHEMA_REGISTRY = build_public_schemas(
    agents=PROFILES,
    max_work_packages=MAX_WORK_PACKAGES,
    max_microtasks_per_package=MAX_MICROTASKS_PER_PACKAGE,
    max_discovery_domains=MAX_DISCOVERY_DOMAINS,
    question_option_schema=QUESTION_OPTION_SCHEMA,
    available_gates=AVAILABLE_GATES,
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
    "activate_orchestration": (activate_orchestration, {"type": "object", "additionalProperties": False, "properties": {"user_command": {"type": "string", "const": "/cortex"}, "principal": {"type": "string", "minLength": 1}}, "required": ["user_command", "principal"]}),
    "deactivate_orchestration": (deactivate_orchestration, {"type": "object", "additionalProperties": False, "properties": {"user_command": {"type": "string", "const": "/normal"}, "principal": {"type": "string"}}, "required": ["user_command"]}),
    "classify_task": (classify_task, {"type": "object", "properties": {"complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "requirements": {"type": "array", "items": {"type": "string"}}, "pipeline": {"type": "array", "items": {"type": "string"}, "description": "Full gate proposal selected by the orchestrator; documentation and close recommendations are advisory, and the chosen pipeline remains authoritative."}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "Ordered executable waves selected by the orchestrator; gates in one wave may run concurrently."}, "principal": {"type": "string"}}, "required": ["complexity"]}),
    "init_task": (init_task, {"type": "object", "properties": {"task_id": {"type": "string"}, "user_request": {"type": "string", "description": "Exact user-authored task text."}, "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "classification_id": {"type": "string"}, "requirements": {"type": "array", "items": {"type": "string"}}, "acceptance_criteria": {"type": "array", "items": {"type": "string"}}, "scope": {"type": "array", "items": {"type": "string"}}, "allowed_paths": {"type": "array", "items": {"type": "string"}}, "verification": {"type": "array", "items": {"type": "string"}}, "budget": {"type": "string"}, "pause_conditions": {"type": "array", "items": {"type": "string"}}, "plan_approval": {"type": "string", "enum": ["auto", "required"]}, "pipeline": {"type": "array", "items": {"type": "string"}}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}, "principal": {"type": "string"}, "user_language": {"type": "string"}}, "required": ["task_id", "user_request", "classification_id"]}),
    "get_task_status": (status, {"type": "object", "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}}, "required": ["task_id", "principal"]}),
    "resolve_dispatch_route": (resolve_dispatch_route, {"type": "object", "additionalProperties": False, "properties": {"agent": {"type": "string", "enum": sorted(AGENTS)}, "task_kind": {"type": "string"}, "risk": {"type": "string", "enum": ["low", "moderate", "high", "critical"]}, "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS)}, "user_requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Exact model explicitly requested by the user; required for non-security Sol."}, "configured_default_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Host-configured agents.default_subagent_model used when native model is omitted."}, "requested_reasoning_effort": {"type": "string"}}, "required": ["agent", "task_kind", "risk"]}),
    "record_delegation": (record_delegation, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "status_receipt": {"type": "string"}, "principal": {"type": "string"}, "gate": {"type": "string"}, "agent": {"type": "string", "enum": sorted(AGENTS)}, "task_kind": {"type": "string"}, "risk": {"type": "string", "enum": ["low", "moderate", "high", "critical"]}, "requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS)}, "user_requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Exact model explicitly requested by the user; required for non-security Sol."}, "configured_default_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS), "description": "Confirmed host agents.default_subagent_model used when native model is omitted."}, "dispatch_mode": {"type": "string", "enum": ["hidden_subagent"], "description": "Native hidden spawn_agent dispatch; alternate worker transports are unsupported."}, "luna_fallback": {"type": "string", "enum": ["terra"], "description": "Unavailable Luna hidden dispatches fall back to an explicit hidden Terra spawn_agent request."}, "thread_environment": {"type": "string", "enum": ["local"], "default": "local", "description": "Native hidden subagents share the current workspace."}, "requested_reasoning_effort": {"type": "string"}, "retry": {"type": "integer"}, "parallel": {"type": "boolean"}, "objective": {"type": "string"}, "ownership": {"type": "string", "minLength": 1}, "context_files": {"type": "array", "items": {"type": "string"}}, "context_result_refs": {"type": "array", "items": {"type": "string"}}, "allowed_paths": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}, "acceptance_criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}, "verification": {"type": "array", "items": {"type": "string", "minLength": 1}}}, "required": ["task_id", "gate", "agent", "task_kind", "risk", "objective", "ownership", "allowed_paths", "acceptance_criteria", "verification"]}),
    "prepare_delegation": (prepare_delegation, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "delegation": {"type": "object"}}, "required": ["task_id", "principal", "delegation"]}),
    "prepare_delegations": (prepare_delegations, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "delegations": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "object"}}}, "required": ["task_id", "principal", "delegations"]}),
    "finalize_attempt": (finalize_attempt, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "attempt_id": {"type": "string"}, "status": {"type": "string", "enum": sorted(TERMINAL_ATTEMPT_STATUSES)}, "reason": {"type": "string"}}, "required": ["task_id", "expected_revision", "attempt_id", "status"]}),
    "cortex.question": (cortex_question, QUESTION_TOOL_SCHEMA),
    "publish_worker_question": (publish_worker_question, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "attempt_id": {"type": "string"}, "submission_id": {"type": "string"}, "question": {"type": "string", "minLength": 1}, "header": {"type": "string"}, "options": {"type": "array", "maxItems": 32, "items": QUESTION_OPTION_SCHEMA}, "multiple": {"type": "boolean"}, "custom_label": {"type": "string"}, "context": {}, "blocking": {"type": "boolean"}}, "required": ["task_id", "principal", "attempt_id", "submission_id", "question"]}),
    "list_worker_questions": (list_worker_questions, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "attempt_id": {"type": "string"}, "status": {"type": "string", "enum": ["open", "answered"]}}, "required": ["task_id", "principal"]}),
    "answer_worker_question": (answer_worker_question, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "question_id": {"type": "string"}, "submission_id": {"type": "string"}, "answer": {"type": "string", "minLength": 1}, "resume_context": {}}, "required": ["task_id", "principal", "question_id", "submission_id", "answer", "resume_context"]}),
    "get_worker_question_updates": (get_worker_question_updates, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "attempt_id": {"type": "string"}, "after_sequence": {"type": "integer", "minimum": 0}}, "required": ["task_id", "principal", "attempt_id"]}),
    "record_evidence": (record_evidence, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "gate": {"type": "string"}, "attempt_id": {"type": "string"}, "result_binding": {"type": "string"}, "kind": {"type": "string"}, "summary": {"type": "string"}, "digest": {"type": "string"}, "command": {"type": "string"}, "exit_code": {"type": "integer"}, "decision": {"type": "string", "enum": ["updated", "not_applicable"]}, "justification": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "governance_obligations": {"type": ["string", "array"], "items": {"type": "string"}}, "governance_scope_ref": {"type": "string"}, "scope_ref": {"type": "string"}, "reviewer_identity": {"type": "string"}, "reviewer_role": {"type": "string"}, "independent_reviewer": {"type": "boolean"}}, "required": ["task_id", "expected_revision", "gate", "summary"]}),
    "execute_verification_command": (execute_verification, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "gate": {"type": "string"}, "attempt_id": {"type": "string"}, "result_binding": {"type": "string"}, "summary": {"type": "string"}, "verification_id": {"type": "string", "enum": sorted(VERIFICATION_COMMANDS)}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120}, "paths": {"type": "array", "items": {"type": "string"}}}, "required": ["task_id", "expected_revision", "gate", "summary", "verification_id"]}),
    "commit_gate": (commit_gate, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "expected_revision": {"type": "integer"}, "gate": {"type": "string"}, "mode": {"type": "string", "enum": ["verification", "documentation"]}, "attempt_id": {"type": "string"}, "result_binding": {"type": "string"}, "summary": {"type": "string"}, "verification_id": {"type": "string", "enum": sorted(VERIFICATION_COMMANDS)}, "timeout_seconds": {"type": "integer"}, "decision": {"type": "string", "enum": ["updated", "not_applicable"]}, "justification": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "outcome": {"type": "string", "enum": ["passed", "failed", "blocked", "skipped"]}}, "required": ["task_id", "principal", "gate", "summary"]}),
    "update_pipeline": (update_pipeline, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "pipeline": {"type": "array", "items": {"type": "string"}}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}, "operations": {"type": "array", "items": PIPELINE_OPERATION_SCHEMA}, "signals": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}, "allow_rework": {"type": "boolean"}}, "required": ["task_id", "expected_revision"]}),
    "reassess_pipeline": (reassess_pipeline, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "signals": {"type": "array", "items": {"type": "string"}}, "pipeline": {"type": "array", "items": {"type": "string"}, "description": "Full replacement selected by the orchestrator; documentation and close recommendations are advisory, and the chosen pipeline remains authoritative."}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "Executable waves selected by the orchestrator."}, "intent": {"type": "string", "enum": ["add_specialist", "resequence", "rework_gate", "stop"]}, "decision": {"type": "string", "enum": ["unchanged", "updated", "stop"]}, "gate": {"type": "string"}, "reason": {"type": "string"}, "allow_rework": {"type": "boolean"}, "apply": {"type": "boolean"}}, "required": ["task_id", "expected_revision", "signals"]}),
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
