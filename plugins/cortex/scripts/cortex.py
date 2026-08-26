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
from typing import Any, Callable, Iterator, Mapping, Sequence
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
from cortex_runtime import ledger_db
from cortex_runtime.host_workspace_binding import (
    bound_workspaces_for_private_lookup,
    workspace_for_session,
)
from cortex_runtime.host_model_attestation import attest_host_default_model
from cortex_runtime.mcp_api import (
    DEFAULT_MCP_AUDIENCE,
    MCP_AUDIENCES,
    configure_internal_schemas,
    current_host_thread_id,
    project_public_governance_semantic,
    project_public_response,
    public_tools as build_public_tools,
    public_tools_for_audience,
    serve_stdio,
    private_lifecycle_response as render_private_lifecycle_response,
)
from cortex_runtime.public_contracts import (
    CANONICAL_COMPLEXITIES,
    backend_schema_for,
    build_public_contracts,
    public_input_schemas,
)
from cortex_runtime.v11_responses import (
    ResponseValidationError,
    WAIT_LOOP_INSTRUCTION,
    validate_private_response as validate_private_v11_response,
    validate_response as validate_v11_response,
)
from cortex_runtime.pagination import decode_cursor, encode_cursor, page_utf8_text, scope_digest
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
    page_durable_questions as db_page_durable_questions,
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
    put_durable_question as db_put_durable_question,
    savepoint as db_savepoint,
    list_artifacts as db_list_artifacts,
    read_artifact_content as db_read_artifact_content,
    read_artifact_range as db_read_artifact_range,
    task_index as db_task_index,
    transaction as db_transaction,
    update_task_definition as db_update_task_definition,
    update_task_plan as db_update_task_plan,
    update_task_state as db_update_task_state,
    upsert_task_finding as db_upsert_task_finding,
    append_task_revision as db_append_task_revision,
    append_plan_revision as db_append_plan_revision,
    find_plan_revision_by_impact as db_find_plan_revision_by_impact,
    append_attempt_message as db_append_attempt_message,
    list_worker_sessions as db_list_worker_sessions,
    put_worker_session as db_put_worker_session,
    reconcile_terminal_worker_session as db_reconcile_terminal_worker_session,
    list_task_findings as db_list_task_findings,
    task_findings_blockers as db_task_findings_blockers,
    require_no_task_finding_blockers as db_require_no_task_finding_blockers,
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
from cortex_runtime.model_routing import (
    model_effort_pair_is_allowed,
    model_effort_pair_text,
    model_effort_registry,
    model_recommendation_registry,
    supported_effort_sequence,
)
from cortex_runtime.revision_impact import classify_revision_impact
from cortex_runtime.assignment_compiler import (
    acceptance_contract_digest,
    effective_result_contract,
)
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
QUESTION_SCHEMA = "cortex/question-text/v1"
SERVER_BASELINE_ACCEPTANCE_OBLIGATIONS = (
    "The delivered result satisfies the exact user_request without dropping an explicit constraint.",
)
SERVER_BASELINE_VERIFICATION_OBLIGATIONS = (
    "Verify the delivered result directly against the exact user_request and the executed task checks.",
)
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
def _mcp_server_instructions(audience: str) -> str:
    common = (
        "Use each Cortex tool only through its current closed public schema. Arbitrary Unicode semantic content is valid. "
        "A failed MCP result may contain normal structured recovery. Retry only when that result explicitly authorizes "
        "a deterministic same-operation correction without mutation; otherwise stop. Never inspect implementation or private state. "
        "After any returned native spawn_agent or followup_task dispatch, invoke it exactly once and then wait_agent for its exact bound child. "
        + WAIT_LOOP_INSTRUCTION
    )
    if audience == "worker":
        return (
            "Worker calls use only the exact authority carried by the native dispatch. "
            "Read the dispatch briefing before work and preserve server-returned pagination, repair, and predecessor authority exactly. "
            + common
        )
    if audience == "coordinator":
        return (
            "Coordinator calls use only the exact server-issued task and coordinator capabilities. "
            "Follow returned lifecycle actions and read canonical worker results before continuation. "
            "When the semantic request explicitly chooses a canonical task complexity, copy that choice through the start tool's currently advertised field; never infer complexity from governance mode. "
            "For invoke_dispatches, invoke each returned native call exactly once with its complete arguments unchanged, preserve every exact returned child identifier, then invoke wait_agent for those exact bound children. "
            "A present model override is mandatory and must not be omitted or changed; an absent override is the deliberate verified Luna-default route. "
            "A native model mismatch cannot be repaired with followup_task; only a newly server-issued replacement dispatch can recover it, otherwise stop. "
            + common
        )
    return "Follow only the selected tool's current schema and returned lifecycle direction. " + common

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
    operation_kinds = contract.get("operation_kinds")
    if (
        not isinstance(operation_kinds, dict)
        or list(operation_kinds) != ["inspect", "modify", "verify", "close"]
        or not all(isinstance(value, str) and value.strip() for value in operation_kinds.values())
    ):
        raise RuntimeError("bundled Cortex operation-kind contract is invalid")
    canonical_operation_kinds = set(operation_kinds)
    profiles: dict[str, dict[str, Any]] = {}
    reliability_fallback_owners: dict[str, list[str]] = {
        operation: [] for operation in operation_kinds
    }
    for item in contract["profiles"]:
        if not isinstance(item, dict) or not SAFE_ID_RE.fullmatch(str(item.get("name", ""))):
            raise RuntimeError("bundled Cortex profile contract contains an invalid profile")
        required_fields = {
            "name", "filename", "sandbox", "operation_kinds", "route_category", "gates",
            "description", "select_when", "avoid_when",
        }
        if not required_fields.issubset(item):
            raise RuntimeError("bundled Cortex profile contract contains incomplete routing metadata")
        name = str(item["name"])
        if name in profiles:
            raise RuntimeError("bundled Cortex profile contract contains duplicate profiles")
        if item.get("sandbox") not in {"read-only", "workspace-write"}:
            raise RuntimeError(f"bundled Cortex profile sandbox is invalid: {name}")
        profile_operation_kinds = item.get("operation_kinds")
        if (
            not isinstance(profile_operation_kinds, list)
            or not profile_operation_kinds
            or len(profile_operation_kinds) != len(set(profile_operation_kinds))
            or not set(profile_operation_kinds).issubset(canonical_operation_kinds)
            or "inspect" not in profile_operation_kinds
        ):
            raise RuntimeError(f"bundled Cortex profile operation kinds are invalid: {name}")
        if ("modify" in profile_operation_kinds) != (item.get("sandbox") == "workspace-write"):
            raise RuntimeError(f"bundled Cortex profile modify capability and sandbox disagree: {name}")
        capability_family = item.get("capability_family")
        if capability_family is not None and not SAFE_ID_RE.fullmatch(str(capability_family)):
            raise RuntimeError(f"bundled Cortex profile capability family is invalid: {name}")
        if item.get("route_category") not in {"automatic", "manual"}:
            raise RuntimeError(f"bundled Cortex profile route category is invalid: {name}")
        if not isinstance(item.get("gates"), list) or not all(isinstance(gate, str) for gate in item["gates"]):
            raise RuntimeError(f"bundled Cortex profile gates are invalid: {name}")
        if not all(isinstance(item.get(field), str) and item[field].strip() for field in ("description", "select_when", "avoid_when")):
            raise RuntimeError(f"bundled Cortex profile routing text is invalid: {name}")
        fallback_operations = item.get("reliability_fallback_for", [])
        if (
            not isinstance(fallback_operations, list)
            or len(fallback_operations) != len(set(fallback_operations))
            or not set(fallback_operations).issubset(canonical_operation_kinds)
            or not set(fallback_operations).issubset(set(profile_operation_kinds))
        ):
            raise RuntimeError(f"bundled Cortex reliability fallback metadata is invalid: {name}")
        for operation in fallback_operations:
            reliability_fallback_owners[operation].append(name)
        profiles[name] = item
    ambiguous_fallbacks = {
        operation: owners
        for operation, owners in reliability_fallback_owners.items()
        if len(owners) != 1
    }
    if ambiguous_fallbacks:
        raise RuntimeError(
            "bundled Cortex reliability fallback ownership must be unique for every operation"
        )
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
PROMPT_COMPACTION_GUIDANCE = SHARED_WORKER_CONTRACT.get("prompt_compaction_guidance", {})
_PROFILE_PUBLIC_CONTRACTS = build_public_contracts(
    agents=PROFILES,
    operation_kinds=PROFILE_CONTRACT.get("operation_kinds", {}),
    model_routing=PROFILE_CONTRACT.get("model_routing", {}),
)
_PROFILE_WORKER_OPERATIONS = {
    name for name, contract in _PROFILE_PUBLIC_CONTRACTS.items()
    if contract.get("audience") == "worker"
}
if (
    SHARED_WORKER_CONTRACT.get("repository_intelligence")
    != "codebase_memory_first_when_available_then_source_confirmed_with_bounded_fallback"
    or SHARED_WORKER_CONTRACT.get("codebase_memory_project_resolution")
    != "derive_canonical_path_key_then_single_exact_root_list_fallback"
    or SHARED_WORKER_CONTRACT.get("codebase_memory_project_key_algorithm")
    != "cbm_project_name_from_path_safe_ascii_utf8hex_fnv1a200"
    or SHARED_WORKER_CONTRACT.get("codebase_memory_fallback")
    != "one_bounded_attempt_then_repository_native_tools_without_looping"
    or not isinstance(SHARED_WORKER_CONTRACT.get("worker_lifecycle"), str)
    or not SHARED_WORKER_CONTRACT["worker_lifecycle"].strip()
    or set(SHARED_WORKER_CONTRACT.get("worker_operations") or []) != _PROFILE_WORKER_OPERATIONS
    or not isinstance(SHARED_WORKER_CONTRACT.get("caller_correctable_tool_errors"), str)
    or not SHARED_WORKER_CONTRACT["caller_correctable_tool_errors"].strip()
    or SHARED_WORKER_CONTRACT.get("read_only_workspace_delta")
    != "ordinary_source_changes_are_concurrency_evidence_all_ignored_side_effects_are_audited_nonblocking_recognized_ephemeral_artifacts_classified"
    or CODEBASE_MEMORY_REFRESH_PROFILES != {"planner", "explorer", "architect", "database_architect"}
    or not CODEBASE_MEMORY_REFRESH_PROFILES.issubset(AGENTS)
    or "retry_policy" in SHARED_WORKER_CONTRACT
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
    """Return the exact assignment capability selected by canonical routing."""
    routed = attempt.get("read_only")
    if isinstance(routed, bool):
        return routed
    profile_name = str(attempt.get("profile") or attempt.get("agent") or "")
    return PROFILES.get(profile_name, {}).get("sandbox") == "read-only"


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
    for field in ("user_request", "requirements", "acceptance_criteria", "scope", "verification"):
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
    python_runtime = any(
        signal in corpus for signal in ("runtime", "plugin", "service", "server", "cortex", "python")
    )
    if python_runtime:
        return {
            "profile": "backend_dev",
            "reason": "Python runtime/service paths indicate backend implementation rather than a generic full-stack owner.",
            "matched_signals": [signal for signal in ("runtime", "plugin", "service", "server", "cortex", "python") if signal in corpus][:8],
            "source": "bounded_task_signals",
        }
    application_paths: list[str] = []
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
# This bounds only repeated failures inside the private atomic commit adapter;
# it is not a pipeline, QA, gate, worker, or rework-attempt budget.
MAX_GATE_RECOVERY_FAILURES = 3
MAX_GATE_RECOVERY_EVENTS = 64
MAX_TOOL_ERROR_LOG_FIELDS = 64
MAX_TOOL_ERROR_LOG_BYTES = 10 * 1024 * 1024
MAX_QUESTIONS_PER_ATTEMPT = 64
MAX_QUESTIONS_PER_TASK = 512
MODEL_RESOLUTIONS = {"configured_default", "explicit_override"}
MODEL_ROUTING = PROFILE_CONTRACT.get("model_routing")
if not isinstance(MODEL_ROUTING, dict) or MODEL_ROUTING.get("schema") != "cortex/model-routing/v1":
    raise RuntimeError("bundled Cortex model routing contract is invalid")
try:
    MODEL_EFFORTS = model_effort_registry(MODEL_ROUTING)
except ValueError as exc:
    raise RuntimeError("bundled Cortex model/effort registry is invalid") from exc
# These are Cortex policy models, not a claim about the native host catalog.
# Both public validation and native routing consume this exact pair registry.
SUPPORTED_MODELS = set(MODEL_EFFORTS)
REQUESTABLE_MODELS = SUPPORTED_MODELS
SUPPORTED_EFFORT_SEQUENCE = supported_effort_sequence(MODEL_ROUTING)
SUPPORTED_EFFORTS = set(SUPPORTED_EFFORT_SEQUENCE)
MODEL_RECOMMENDED_EFFORTS = model_recommendation_registry(MODEL_ROUTING)
MODEL_PAIR_GUIDANCE = (
    "Select one exact canonical pair: " + model_effort_pair_text(MODEL_ROUTING) + "."
)
CONFIGURED_DEFAULT_MODEL = str(MODEL_ROUTING.get("configured_default_model", ""))
if CONFIGURED_DEFAULT_MODEL != "gpt-5.6-luna":
    raise RuntimeError("bundled Cortex model routing must use Luna as the configured default")


# Routing metadata is a capability registry.  The orchestrator owns the
# concrete profile/model/effort choice, so no profile matrix, security lock,
# task-kind override, or complexity/risk effort floor is loaded here.
SECURITY_MODEL = "gpt-5.6-sol"
EXPLORER_MODEL = CONFIGURED_DEFAULT_MODEL
SECURITY_EFFORT_BY_COMPLEXITY: dict[str, str] = {}
EXPLORER_EFFORT_BY_RISK: dict[str, str] = {}
LUNA_BOUNDED_EFFORT_BY_COMPLEXITY: dict[str, str] = {}
LUNA_EFFICIENT_EFFORT_BY_COMPLEXITY: dict[str, str] = {}
TERRA_EFFORT_BY_COMPLEXITY: dict[str, str] = {}
MODEL_EFFORT_FLOOR_BY_RISK: dict[str, str] = {}
MODEL_PROFILE_CLASSES: dict[str, set[str]] = {}
TERRA_TASK_KINDS: set[str] = set()
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
RESULT_READY = "result_ready"
CAPABILITY_SOURCE = "host_spawn_agent_contract_2026-08-13"
ROUTING_POLICY = {
    "supported_models": SUPPORTED_MODELS,
    "requestable_models": REQUESTABLE_MODELS,
    "supported_efforts": SUPPORTED_EFFORTS,
    "model_efforts": MODEL_EFFORTS,
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
    "manifest_reconciliation": {
        "verification_kind": "manifest_reconciliation",
    },
}
SENSITIVE_KEY_RE = re.compile(r"(?i)(?:^|[_ -])(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|token|password|passwd|secret|private[_ -]?key|authorization|coordinator[_ -]?capability|coordinator[_ -]?recovery[_ -]?proof)(?:$|[_ -])")
SENSITIVE_LOG_KEY_NAMES = {
    "apikey", "accesstoken", "refreshtoken", "clientsecret", "token",
    "password", "passwd", "secret", "privatekey", "authorization",
    "coordinatorcapability", "coordinatorrecoveryproof", "coordinatorref", "assignmentref",
}
ASSIGNMENT_VALUE_RE = re.compile(r"assignment-v1-[0-9a-f]{64}")


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
    """Resolve the workspace exclusively from the trusted SessionStart binding.

    ``project_root`` is deliberately not a public workspace selector.  A
    caller may repeat the already-bound value for compatibility with private
    server plumbing, but it can never choose a different workspace.
    """
    if os.environ.get("CORTEX_ROOT"):
        raise ValueError(
            "CORTEX_ROOT is not supported; Cortex control state is derived from the "
            "current MCP thread's SessionStart workspace binding"
        )
    bound = workspace_for_session(current_host_thread_id())
    if bound is None:
        raise ValueError("host_workspace_unavailable: no validated SessionStart workspace for this MCP thread")
    requested = str((params or {}).get("project_root") or "").strip()
    if not requested:
        return bound
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
    if path != bound:
        raise ValueError(
            "workspace_binding_mismatch: supplied project_root does not match the current "
            "MCP thread's trusted SessionStart workspace"
        )
    return bound


def _select_internal_project_root(value: object) -> Path:
    """Validate a server-owned persisted workspace path.

    This is intentionally separate from ``select_project_root``: only paths
    read from an already-authorized host ledger may use it.  Public MCP
    arguments must always resolve through the current SessionStart binding.
    """
    requested = str(value or "").strip()
    if not requested:
        raise ValueError("host_workspace_unavailable: server-owned workspace path is empty")
    requested_path = Path(os.path.normpath(str(Path(requested).expanduser())))
    if not requested_path.is_absolute():
        raise ValueError("server-owned project root must be an absolute path")
    path = _reject_symlink_ancestry(requested_path, "server-owned project root")
    if not path.is_dir():
        raise ValueError(f"server-owned project root is not a directory: {path}")
    if path == PLUGIN_ROOT or PLUGIN_ROOT in path.parents:
        raise ValueError("server-owned project root must not be the Cortex plugin directory")
    if path == Path.home().absolute() or path in SYSTEM_PROJECT_ROOTS:
        raise ValueError("server-owned project root must be a specific repository or worktree")
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


def ledger_root_path_internal(project_root: object, *, create: bool = False) -> Path:
    """Resolve a host ledger from a server-owned persisted workspace path."""
    project = _select_internal_project_root(project_root)
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
                root = _select_internal_project_root(root_value)
                expected = ledger_root_path_internal(root, create=False)
                if expected != candidate:
                    continue
                matches.append(root)
        except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError):
            continue
    if len(matches) > 1:
        raise ValueError("task_ref resolves to multiple host-private project ledgers; Cortex refuses ambiguous host binding")
    return matches[0] if matches else None


def _bound_project_root_for_dispatch_ref(dispatch_ref: str) -> Path | None:
    """Resolve one worker dispatch capability to exactly one private ledger."""
    requested = str(dispatch_ref or "").strip()
    if re.fullmatch(r"dispatch-[0-9a-f]{24}", requested) is None:
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
                loaded = _v11_task_state(candidate, str(task_id))
                if loaded is None:
                    continue
                _, state, task = loaded
                if not any(
                    isinstance(attempt, Mapping)
                    and str(attempt.get("dispatch_ref") or "") == requested
                    for attempt in state.get("attempts") or []
                ):
                    continue
                root_value = str(task.get("project_root") or "").strip()
                if not root_value:
                    continue
                root = _select_internal_project_root(root_value)
                if ledger_root_path_internal(root, create=False) != candidate:
                    continue
                matches.append(root)
        except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError):
            continue
    if len(matches) > 1:
        raise ValueError("dispatch_ref resolves to multiple host-private project ledgers")
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
        "id", "call_id", "task_id", "attempt_id", "question_ref", "submission_id",
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
    del profile_name
    return "explicit"


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
    from cortex_runtime.questions import permitted_question_categories

    root = _task_document_root(task_dir, str(state["task_id"]))
    decisions: list[dict[str, Any]] = []
    offset = 0
    while True:
        page, has_more = db_page_durable_questions(
            root, str(state["task_id"]), offset=offset, limit=64, status="answered",
            categories=permitted_question_categories(),
        )
        decisions.extend(page)
        if not has_more:
            return decisions
        offset += len(page)



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
    package_allowed = {"id", "title", "objective", "depends_on", "status", "order", "gates", "required_artifacts", "microtasks"}
    micro_allowed = {"id", "title", "objective", "profile", "depends_on", "status", "order", "gates", "acceptance_criteria", "verification", "required_artifacts"}
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
            for key in ("id", "title", "objective", "profile", "acceptance_criteria", "verification"):
                if key not in micro:
                    diagnostics.append(_planning_diag(f"planning microtask requires {key}", path=f"{m}.{key}"))
            for key in ("acceptance_criteria", "verification", "depends_on", "gates", "required_artifacts"):
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
        unknown = sorted(set(raw_package) - {"id", "title", "objective", "depends_on", "status", "order", "gates", "required_artifacts", "microtasks"})
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
            allowed = {"id", "title", "objective", "profile", "depends_on", "status", "order", "gates", "acceptance_criteria", "verification", "required_artifacts"}
            unknown_micro = sorted(set(raw_microtask) - allowed)
            missing_micro = sorted({"id", "title", "objective", "profile", "acceptance_criteria", "verification"} - set(raw_microtask))
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


PLAN_TRACKER_SCHEMA = "cortex/plan-tracker/v2"


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
        "plan_items": rows,
        "items": [],
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


def _plan_tracker_occurrence_items(
    plan: Mapping[str, Any], state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project one tracker row per exact compiled assignment occurrence.

    A semantic phase kind may repeat.  It is therefore presentation data only
    and can never drive tracker status.  Every row is keyed by the compiler's
    server-issued wave/phase identity plus the exact logical slot and plan
    lineage.  Technical generations are projected onto that slot; product
    rework creates a new occurrence instead of reopening an earlier row.
    """
    from cortex_runtime.assignment_compiler import compiled_wave_execution_order

    attempts = [item for item in state.get("attempts") or [] if isinstance(item, dict)]
    completed = {str(item) for item in state.get("completed_orchestration_wave_ids") or []}
    skipped = {str(item) for item in state.get("skipped_orchestration_wave_ids") or []}
    rows: list[dict[str, Any]] = []
    raw_waves = list(plan.get("waves") or [])
    if any(not isinstance(wave, Mapping) for wave in raw_waves):
        raise ValueError("plan tracker requires compiled wave objects")
    waves = list(raw_waves)
    compiled_wave_execution_order(waves)
    for execution_order, wave in enumerate(waves, 1):
        if not isinstance(wave, Mapping):
            raise ValueError("plan tracker requires compiled wave objects")
        wave_ref = str(wave.get("wave_ref") or "").strip()
        phase_ref = str(wave.get("phase_ref") or "").strip()
        phase_kind = str(wave.get("phase_kind") or "").strip()
        wave_index = wave.get("wave_index")
        if (
            isinstance(wave_index, bool)
            or not isinstance(wave_index, int)
            or wave_ref != f"wave-{wave_index:02d}"
            or str(wave.get("wave_id") or "") != wave_ref
            or phase_ref != f"phase-{wave_index:04d}"
            or not phase_kind
        ):
            raise ValueError("plan tracker requires canonical compiled wave identity")
        for slot, spec in enumerate(wave.get("delegations") or [], 1):
            if not isinstance(spec, Mapping):
                raise ValueError("plan tracker requires compiled assignment objects")
            logical_key = str(spec.get("logical_delegation_key") or "").strip()
            lineage = str(spec.get("plan_assignment_lineage_digest") or "").strip()
            operation_kind = str(spec.get("operation_kind") or "").strip()
            if (
                str(spec.get("wave_ref") or "") != wave_ref
                or str(spec.get("phase_ref") or "") != phase_ref
                or spec.get("wave_index") != wave_index
                or str(spec.get("phase_kind") or "") != phase_kind
                or operation_kind not in {"inspect", "modify", "verify", "close"}
                or not logical_key
                or not lineage
            ):
                raise ValueError("plan tracker assignment identity differs from its compiled wave")
            matching = [
                attempt for attempt in attempts
                if str(attempt.get("wave_ref") or "") == wave_ref
                and str(attempt.get("phase_ref") or "") == phase_ref
                and str(attempt.get("logical_delegation_key") or "") == logical_key
                and str(attempt.get("plan_assignment_lineage_digest") or "") == lineage
            ]
            active = [attempt for attempt in matching if not attempt.get("invalidated")]
            if len(active) > 1:
                raise ValueError("plan tracker found multiple active generations for one compiled slot")
            attempt = active[0] if active else (matching[-1] if matching else None)
            status = "pending"
            if wave_ref in skipped:
                status = "skipped"
            elif isinstance(attempt, Mapping):
                acceptance_status = str(attempt.get("acceptance_status") or "")
                protocol_status = str(attempt.get("protocol_status") or "")
                attempt_status = str(attempt.get("status") or "")
                invalidation_reason = str(attempt.get("invalidation_reason") or "")
                if invalidation_reason == "superseded_by_product_rework":
                    status = "needs_rework"
                elif acceptance_status in {"needs_rework", "blocked", "failed"}:
                    status = acceptance_status
                elif protocol_status in {"blocked", "failed"}:
                    status = protocol_status
                elif acceptance_status == "passed" and attempt.get("continuation_consumed_at"):
                    status = "completed"
                elif attempt_status in {"running", "waiting_question"}:
                    status = "running" if attempt_status == "running" else "paused"
                elif attempt_status == RESULT_READY:
                    status = "result_ready"
                elif attempt_status in {"blocked", "failed", "cancelled", "superseded"}:
                    status = "blocked" if attempt_status == "blocked" else "failed"
                elif attempt_status == "passed":
                    status = "completed" if wave_ref in completed else "result_ready"
                elif attempt_status == AWAITING_HOST_SPAWN:
                    status = "pending"
                elif attempt.get("invalidated"):
                    status = "replaced"
            elif wave_ref in completed:
                status = "completed"
            rows.append({
                "kind": "assignment_occurrence",
                "id": logical_key,
                "status": status,
                "order": execution_order,
                "phase_kind": phase_kind,
                "phase_ref": phase_ref,
                "wave_ref": wave_ref,
                "wave_index": wave_index,
                "operation_kind": operation_kind,
                "profile": str(spec.get("profile") or spec.get("agent") or ""),
                "objective": str(spec.get("objective") or ""),
                "logical_delegation_key": logical_key,
                "plan_assignment_lineage_digest": lineage,
                "predecessor_wave_refs": list(spec.get("predecessor_wave_refs") or []),
                "gates": [phase_kind],
                "depends_on": list(spec.get("predecessor_wave_refs") or []),
                **(
                    {
                        "attempt_id": str(attempt.get("attempt_id") or ""),
                        "attempt_result_ref": str(attempt.get("attempt_result_ref") or ""),
                    }
                    if isinstance(attempt, Mapping) else {}
                ),
                "updated_at": now(),
            })
    return rows


def _sync_plan_tracker_document(task_dir: Path, state: dict[str, Any], *, event: str, detail: str) -> None:
    """Synchronize the live tracker from exact compiled plan occurrences."""
    root = _task_document_root(task_dir, str(state["task_id"]))
    tracker = db_get_task_document(root, str(state["task_id"]), "plan_tracker_current")
    if not isinstance(tracker, dict):
        return
    if tracker.get("schema") == "cortex/plan-tracker/v1":
        tracker["plan_items"] = [
            dict(item) for item in tracker.get("items") or [] if isinstance(item, Mapping)
        ]
        tracker["items"] = []
        tracker["schema"] = PLAN_TRACKER_SCHEMA
    if tracker.get("schema") != PLAN_TRACKER_SCHEMA:
        return
    loaded = db_load_task(root, str(state["task_id"]))
    plan = loaded[2] if loaded is not None and isinstance(loaded[2], Mapping) else None
    if isinstance(plan, Mapping):
        tracker["items"] = _plan_tracker_occurrence_items(plan, state)
        tracker["plan_revision"] = plan.get("plan_revision")
        tracker["plan_digest"] = state.get("plan_digest")
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


def _write_question_record(task_dir: Path, state: dict[str, Any], record: dict[str, Any]) -> None:
    question_ref = str(record.get("question_ref") or "")
    if re.fullmatch(r"question-[A-Za-z0-9._:-]{1,160}", question_ref) is None:
        raise ValueError("question record identity is invalid")
    root = _task_document_root(task_dir, str(state["task_id"]))
    db_put_durable_question(root, record)


def _open_blocking_questions(
    task_dir: Path,
    state: dict[str, Any],
    attempt_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return unanswered material questions that pause an attempt or wave."""
    from cortex_runtime.questions import permitted_question_categories

    # Question records are canonical SQLite task documents.  Their optional
    # filesystem projections may be absent on a fresh ledger or while an
    # outbox job is pending, so export layout must never decide whether a
    # material question blocks the task.
    root = _task_document_root(task_dir, str(state["task_id"]))
    offset = 0
    blockers: list[dict[str, Any]] = []
    while len(blockers) < 64:
        page, has_more = db_page_durable_questions(
            root, str(state["task_id"]), offset=offset, limit=min(64 - len(blockers), 16),
            attempt_id=str(attempt_id or ""), status="open",
            categories=permitted_question_categories(),
        )
        for item in page:
            if len(blockers) >= 64:
                break
            blockers.append(item)
        if not has_more or not page:
            break
        offset += len(page)
    return blockers


def _resolved_user_decisions(task_dir: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return answered plain-text question/answer pairs for a new dispatch."""
    from cortex_runtime.questions import permitted_question_categories

    decisions: list[dict[str, Any]] = []
    root = _task_document_root(task_dir, str(state["task_id"]))
    offset = 0
    while True:
        page, has_more = db_page_durable_questions(
            root, str(state["task_id"]), offset=offset, limit=64, status="answered",
            categories=permitted_question_categories(),
        )
        for record in page:
            if record.get("status") != "answered":
                continue
            question_text = record.get("question_text")
            answer = record.get("answer")
            if not isinstance(question_text, str) or not question_text:
                raise ValueError("answered durable question has no question_text")
            if not isinstance(answer, str) or not answer:
                raise ValueError("answered durable question has no answer")
            question_ref = safe_id(str(record.get("question_ref") or ""))
            if not question_ref:
                continue
            decision = {
                "source_type": "question",
                "question_ref": question_ref,
                "question_text": question_text,
                "answer": answer,
                "answered_at": record.get("answered_at"),
            }
            decision["decision_digest"] = digest_text(canonical_json.dumps(
                {key: decision[key] for key in ("source_type", "question_ref", "question_text", "answer")},
            ))
            decisions.append(decision)
        if not has_more:
            break
        offset += len(page)
    decisions.sort(key=lambda item: (str(item.get("answered_at") or ""), item["question_ref"]))
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

    authored_acceptance = items("acceptance_criteria")
    authored_verification = items("verification")
    server_acceptance = items("server_acceptance_obligations")
    server_verification = items("server_verification_obligations")
    acceptance, verification = effective_result_contract(
        authored_acceptance, authored_verification,
        server_acceptance_obligations=server_acceptance,
        server_verification_obligations=server_verification,
    )
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
    root = _ledger_root_for_artifact(task_dir)
    plan_receipt = ledger_db.get_active_plan_revision(root, str(state.get("task_id") or ""))
    if plan_receipt is not None:
        if plan_receipt.get("current_plan_matches") is not True:
            raise ValueError("active plan revision does not match the current orchestration plan")
        state["plan_revision"] = int(plan_receipt["plan_revision"])
        state["plan_digest"] = str(plan_receipt.get("plan_digest") or "")
    state["revision"] += 1
    state["updated_at"] = now()
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
                current.get("worker_authority")
                if isinstance(current, dict) and isinstance(current.get("worker_authority"), dict)
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
    occurrence_wave = active_gates(state)
    if state.get("orchestration_wave_occurrences"):
        return occurrence_wave[0] if occurrence_wave else None
    done = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    return next((gate for gate in state["current_pipeline"] if gate not in done), None)


def active_gates(state: dict[str, Any]) -> list[str]:
    """Return the unfinished, unpaused gates in the first executable wave.

    A no-progress pause belongs to one corrective gate, not to the task.  A
    sibling in the same parallel wave (and later independent waves) must keep
    running while that gate awaits a Planner-backed recovery decision.
    """
    pauses = state.get("rework_pauses")
    paused = {
        str(occurrence_gate_key)
        for occurrence_gate_key, pause in pauses.items()
        if isinstance(pause, dict) and pause.get("status") == "planner_recovery_pending"
    } if isinstance(pauses, dict) else set()
    occurrences = state.get("orchestration_wave_occurrences")
    if isinstance(occurrences, list) and occurrences:
        completed_wave_ids = set(state.get("completed_orchestration_wave_ids") or [])
        skipped_wave_ids = set(state.get("skipped_orchestration_wave_ids") or [])
        for occurrence in occurrences:
            if not isinstance(occurrence, dict):
                continue
            wave_id = str(occurrence.get("wave_id") or "")
            if not wave_id or wave_id in completed_wave_ids or wave_id in skipped_wave_ids:
                continue
            occurrence_key = str(occurrence.get("occurrence_key") or "")
            return [
                str(gate) for gate in occurrence.get("gates") or []
                if str(gate).strip()
                and f"{occurrence_key}:{gate}" not in paused
            ]
        return []
    paused_gates = {
        str(pause.get("gate") or pause.get("phase_kind") or "")
        for pause in (pauses or {}).values()
        if isinstance(pause, dict) and pause.get("status") == "planner_recovery_pending"
    } if isinstance(pauses, dict) else set()
    done = set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
    groups = state.get("parallel_groups") or [[gate] for gate in state["current_pipeline"]]
    for group in groups:
        unfinished = [gate for gate in group if gate not in done]
        if not unfinished:
            continue
        pending = [gate for gate in unfinished if gate not in paused_gates]
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


def terminal_handoff_identity(state: Mapping[str, Any]) -> dict[str, str] | None:
    """Return the exact compiled terminal occurrence for handoff authority."""
    occurrences = state.get("orchestration_wave_occurrences")
    if not isinstance(occurrences, list) or not occurrences:
        return None
    completed = set(state.get("completed_orchestration_wave_ids") or [])
    candidates = [
        item for item in occurrences
        if isinstance(item, Mapping)
        and str(item.get("wave_id") or "") in completed
        and str(item.get("phase_ref") or "")
        and str(item.get("phase_kind") or "")
    ]
    if not candidates:
        return None
    item = candidates[-1]
    return {
        "wave_ref": str(item["wave_id"]),
        "phase_ref": str(item["phase_ref"]),
        "phase_kind": str(item["phase_kind"]),
    }


def _validated_product_rework_supersession(
    state: Mapping[str, Any],
    *,
    artifact_root: Path,
    plan: Mapping[str, Any],
    occurrence: Mapping[str, Any],
    lineage: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return exact verified supersession evidence for one failed occurrence."""
    from cortex_runtime.assignment_evaluator import requires_product_rework
    from cortex_runtime.orchestration_engine import _closure_rework_occurrence_matches

    wave_ref = str(occurrence.get("wave_ref") or occurrence.get("wave_id") or "")
    phase_ref = str(occurrence.get("phase_ref") or "")
    logical_key = str(lineage.get("logical_delegation_key") or "")
    plan_lineage = str(lineage.get("plan_assignment_lineage_digest") or "")
    sources = [
        item for item in state.get("attempts") or []
        if isinstance(item, Mapping)
        and str(item.get("wave_ref") or "") == wave_ref
        and str(item.get("phase_ref") or "") == phase_ref
        and str(item.get("logical_delegation_key") or "") == logical_key
        and str(item.get("plan_assignment_lineage_digest") or "") == plan_lineage
        and item.get("invalidated") is True
        and str(item.get("invalidation_reason") or "")
        == "superseded_by_product_rework"
        and str(item.get("attempt_result_ref") or "")
    ]
    if not sources:
        return None
    if len(sources) != 1:
        raise ValueError("completed handoff product source occurrence is ambiguous")
    source = sources[0]
    source_result_ref = str(source.get("attempt_result_ref") or "")
    evaluation = source.get("acceptance_evaluation")
    if (
        not isinstance(evaluation, Mapping)
        or not requires_product_rework(evaluation)
        or str(source.get("acceptance_status") or "") != "needs_rework"
        or not str(source.get("continuation_consumed_at") or "")
    ):
        raise ValueError("completed handoff product source lacks canonical consumption")
    canonical_source = attempt_protocol.get_attempt_result(
        artifact_root,
        task_id=str(state.get("task_id") or ""),
        attempt_id=str(source.get("attempt_id") or ""),
    )
    if (
        not isinstance(canonical_source, Mapping)
        or str(canonical_source.get("result_ref") or "") != source_result_ref
        or canonical_source.get("lifecycle_status")
        != attempt_protocol.LIFECYCLE_COMPLETED
    ):
        raise ValueError("completed handoff product source lacks canonical result")

    append_receipts = [
        item for item in plan.get("rework_appends") or []
        if isinstance(item, Mapping)
        and str(item.get("source_result_ref") or "") == source_result_ref
    ]
    if len(append_receipts) != 1:
        raise ValueError("completed handoff product source lacks exact append receipt")
    append_receipt = append_receipts[0]
    if (
        str(append_receipt.get("source_wave_ref") or "") != wave_ref
        or str(append_receipt.get("source_phase_ref") or "") != phase_ref
        or str(append_receipt.get("source_logical_delegation_key") or "") != logical_key
        or str(append_receipt.get("source_plan_assignment_lineage_digest") or "")
        != plan_lineage
    ):
        raise ValueError("completed handoff append receipt changed source identity")
    routes = [
        item for item in (state.get("product_rework_routes") or {}).values()
        if isinstance(item, Mapping)
        and str(item.get("source_result_ref") or "") == source_result_ref
    ]
    if len(routes) != 1 or str(routes[0].get("status") or "") != "resolved":
        raise ValueError("completed handoff product rework route is not resolved")
    route = routes[0]
    if (
        str(route.get("source_wave_ref") or "") != wave_ref
        or str(route.get("source_phase_ref") or "") != phase_ref
        or str(route.get("source_logical_delegation_key") or "") != logical_key
        or str(route.get("source_plan_assignment_lineage_digest") or "") != plan_lineage
    ):
        raise ValueError("completed handoff product route changed source identity")

    role_results: dict[str, dict[str, Any]] = {}
    required_roles = ["corrective", "verifier"] + (
        ["close"] if str(route.get("close_wave_ref") or "") else []
    )
    for role in required_roles:
        result_ref = str(route.get(f"{role}_result_ref") or "")
        evidence = route.get(f"{role}_evidence")
        matches = [
            item for item in state.get("attempts") or []
            if isinstance(item, Mapping)
            and not item.get("invalidated")
            and str(item.get("acceptance_status") or "") == "passed"
            and str(item.get("continuation_consumed_at") or "")
            and str(item.get("attempt_result_ref") or "") == result_ref
            and _closure_rework_occurrence_matches(route, item, role)
        ]
        if len(matches) != 1 or not isinstance(evidence, Mapping):
            raise ValueError(f"completed handoff {role} route evidence is incomplete")
        attempt = matches[0]
        expected_evidence = {
            "wave_ref": str(attempt.get("wave_ref") or ""),
            "phase_ref": str(attempt.get("phase_ref") or ""),
            "logical_delegation_key": str(
                attempt.get("logical_delegation_key") or ""
            ),
            "plan_assignment_lineage_digest": str(
                attempt.get("plan_assignment_lineage_digest") or ""
            ),
            "assignment_ref": str(attempt.get("dispatch_ref") or ""),
            "attempt_result_ref": result_ref,
        }
        if dict(evidence) != expected_evidence:
            raise ValueError(f"completed handoff {role} evidence changed exact binding")
        canonical = attempt_protocol.get_attempt_result(
            artifact_root,
            task_id=str(state.get("task_id") or ""),
            attempt_id=str(attempt.get("attempt_id") or ""),
        )
        if (
            not isinstance(canonical, Mapping)
            or str(canonical.get("result_ref") or "") != result_ref
            or canonical.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
        ):
            raise ValueError(f"completed handoff {role} result is not canonical")
        role_results[role] = {
            "wave_ref": expected_evidence["wave_ref"],
            "phase_ref": expected_evidence["phase_ref"],
            "logical_delegation_key": expected_evidence["logical_delegation_key"],
            "plan_assignment_lineage_digest": expected_evidence[
                "plan_assignment_lineage_digest"
            ],
            "attempt_result_ref": result_ref,
        }

    finding_fingerprints = [
        str(item) for item in route.get("finding_fingerprints") or [] if str(item)
    ]
    if finding_fingerprints:
        closure_routes = [
            item for item in (state.get("closure_rework") or {}).values()
            if isinstance(item, Mapping)
            and source_result_ref in {
                str(value) for value in item.get("source_result_refs") or []
            }
        ]
        if len(closure_routes) != 1 or closure_routes[0].get("status") != "resolved":
            raise ValueError("completed handoff finding route is not exactly resolved")
        closure_route = closure_routes[0]
        for role in ("corrective", "verifier"):
            for field in (
                "wave_ref", "phase_ref", "logical_delegation_key",
                "plan_assignment_lineage_digest", "result_ref",
            ):
                if str(closure_route.get(f"{role}_{field}") or "") != str(
                    route.get(f"{role}_{field}") or ""
                ):
                    raise ValueError("finding and product rework route bindings diverged")
        findings = {
            str(item.get("fingerprint") or ""): item
            for item in db_list_task_findings(
                artifact_root,
                str(state.get("task_id") or ""),
                include_resolved=True,
            )
            if isinstance(item, Mapping)
        }
        if any(
            not isinstance(findings.get(fingerprint), Mapping)
            or str(findings[fingerprint].get("status") or "") != "resolved"
            for fingerprint in finding_fingerprints
        ):
            raise ValueError("completed handoff retains an unresolved product finding")

    return {
        "wave_ref": wave_ref,
        "wave_index": occurrence.get("wave_index"),
        "phase_ref": phase_ref,
        "phase_kind": str(occurrence.get("phase_kind") or ""),
        "occurrence_key": str(occurrence.get("occurrence_key") or ""),
        "assignment_lineage_digest": str(
            occurrence.get("assignment_lineage_digest") or ""
        ),
        "logical_delegation_key": logical_key,
        "plan_assignment_lineage_digest": plan_lineage,
        "attempt_id": str(source.get("attempt_id") or ""),
        "attempt_result_ref": source_result_ref,
        "protocol_status": str(source.get("protocol_status") or ""),
        "acceptance_status": "superseded_by_verified_product_rework",
        "original_acceptance_status": "needs_rework",
        "finding_fingerprints": finding_fingerprints,
        "rework": role_results,
        "corrective_history": list(route.get("corrective_binding_history") or []),
        "verifier_history": list(route.get("verifier_binding_history") or []),
        "close_history": list(route.get("close_binding_history") or []),
    }


def _completed_handoff_occurrences(
    state: Mapping[str, Any],
    *,
    artifact_root: Path,
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project every completed compiled assignment occurrence into a handoff."""
    completed_wave_ids = set(state.get("completed_orchestration_wave_ids") or [])
    entries: list[dict[str, Any]] = []
    for occurrence in state.get("orchestration_wave_occurrences") or []:
        if not isinstance(occurrence, Mapping):
            continue
        wave_ref = str(occurrence.get("wave_ref") or occurrence.get("wave_id") or "").strip()
        phase_ref = str(occurrence.get("phase_ref") or "").strip()
        occurrence_key = str(occurrence.get("occurrence_key") or "").strip()
        lineage_digest = str(occurrence.get("assignment_lineage_digest") or "").strip()
        wave_index = occurrence.get("wave_index")
        lineages = occurrence.get("assignment_lineages")
        if wave_ref not in completed_wave_ids:
            continue
        if (
            not wave_ref
            or not phase_ref
            or not occurrence_key
            or not lineage_digest
            or isinstance(wave_index, bool)
            or not isinstance(wave_index, int)
            or not isinstance(lineages, list)
            or not lineages
        ):
            raise ValueError("completed handoff occurrence lacks compiled identity")
        for lineage in lineages:
            if not isinstance(lineage, Mapping):
                raise ValueError("completed handoff occurrence has invalid assignment lineage")
            logical_key = str(lineage.get("logical_delegation_key") or "").strip()
            plan_lineage = str(lineage.get("plan_assignment_lineage_digest") or "").strip()
            candidates = [
                item for item in state.get("attempts") or []
                if isinstance(item, Mapping)
                and str(item.get("wave_ref") or "") == wave_ref
                and str(item.get("orchestration_wave_id") or "") == wave_ref
                and str(item.get("phase_ref") or "") == phase_ref
                and str(item.get("logical_delegation_key") or "") == logical_key
                and str(item.get("plan_assignment_lineage_digest") or "") == plan_lineage
                and str(item.get("acceptance_status") or "") == "passed"
                and str(item.get("continuation_consumed_at") or "").strip()
                and str(item.get("attempt_result_ref") or "").strip()
            ]
            if len(candidates) != 1:
                supersession = _validated_product_rework_supersession(
                    state,
                    artifact_root=artifact_root,
                    plan=plan,
                    occurrence=occurrence,
                    lineage=lineage,
                )
                if supersession is not None:
                    entries.append(supersession)
                    continue
                raise ValueError(
                    "completed handoff occurrence must resolve one accepted assignment result"
                )
            attempt = candidates[0]
            attempt_id = str(attempt.get("attempt_id") or "").strip()
            result_ref = str(attempt.get("attempt_result_ref") or "").strip()
            canonical = attempt_protocol.get_attempt_result(
                artifact_root,
                task_id=str(state.get("task_id") or ""),
                attempt_id=attempt_id,
            )
            if (
                not isinstance(canonical, Mapping)
                or str(canonical.get("result_ref") or "") != result_ref
                or canonical.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
            ):
                raise ValueError("completed handoff occurrence lacks its canonical AttemptResult")
            entries.append({
                "wave_ref": wave_ref,
                "wave_index": wave_index,
                "phase_ref": phase_ref,
                # Semantic phase is descriptive only; exact occurrence and
                # assignment lineage fields above are the durable identity.
                "phase_kind": str(occurrence.get("phase_kind") or ""),
                "occurrence_key": occurrence_key,
                "assignment_lineage_digest": lineage_digest,
                "logical_delegation_key": logical_key,
                "plan_assignment_lineage_digest": plan_lineage,
                "attempt_id": attempt_id,
                "attempt_result_ref": result_ref,
                "protocol_status": str(attempt.get("protocol_status") or ""),
                "acceptance_status": "passed",
            })
    # ``orchestration_wave_occurrences`` is synchronized from canonical plan
    # list order. Preserve that execution order; wave_index is immutable
    # occurrence identity and is intentionally not sortable after insertion.
    if completed_wave_ids and not entries:
        raise ValueError("completed handoff has no canonical assignment occurrences")
    return entries


def terminal_handoff_gate(state: Mapping[str, Any]) -> str | None:
    identity = terminal_handoff_identity(state)
    return identity["phase_kind"] if identity is not None else None


def validate_terminal_handoff_acceptance(
    state: Mapping[str, Any],
    *,
    artifact_root: Path,
    handoff_identity: Mapping[str, Any],
) -> None:
    """Require current canonical acceptance for every terminal-wave worker."""
    from cortex_runtime.assignment_evaluator import evaluate_assignment

    terminal_attempts = [
        item for item in state.get("attempts") or []
        if isinstance(item, Mapping)
        and not item.get("invalidated")
        and str(item.get("wave_ref") or item.get("orchestration_wave_id") or "")
        == str(handoff_identity.get("wave_ref") or "")
        and str(item.get("phase_ref") or "")
        == str(handoff_identity.get("phase_ref") or "")
    ]
    if not terminal_attempts:
        raise ValueError("handoff_verification_incomplete: terminal assignment is unavailable")
    for attempt in terminal_attempts:
        result_ref = str(attempt.get("attempt_result_ref") or "")
        attempt_id = str(attempt.get("attempt_id") or "")
        if not result_ref or not attempt_id:
            raise ValueError("handoff_verification_incomplete: canonical terminal result is unavailable")
        canonical = attempt_protocol.get_attempt_result(
            artifact_root,
            task_id=str(state.get("task_id") or ""),
            attempt_id=attempt_id,
        )
        native_stop = attempt.get("native_terminal_stop")
        if (
            not isinstance(native_stop, Mapping)
            or native_stop.get("observed") is not True
            or str(native_stop.get("result_digest") or "")
            != hashlib.sha256(result_ref.encode("utf-8")).hexdigest()
        ):
            raise ValueError(
                "handoff_verification_incomplete: exact native terminal Stop is unavailable"
            )
        evaluation = evaluate_assignment(artifact_root, state, attempt, canonical)
        if (
            evaluation.get("acceptance_status") != "passed"
            or list(evaluation.get("missing_verification_kinds") or [])
        ):
            raise ValueError(
                "handoff_verification_incomplete: terminal assignment acceptance is not passed"
            )


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


def _validated_governance_receipt(state: Mapping[str, Any]) -> dict[str, Any]:
    governance = state.get("governance")
    if not isinstance(governance, dict) or not governance:
        raise ValueError("governance receipt is missing")
    if governance.get("effective_mode") not in _GOVERNANCE_OBLIGATION_DEFAULTS:
        raise ValueError("governance receipt effective_mode is missing or invalid")
    if not str(governance.get("requested_mode") or "").strip():
        raise ValueError("governance receipt requested_mode is missing")
    return governance


def _governance_obligations_for_gate(state: dict[str, Any], gate: str) -> tuple[str, ...]:
    if gate not in {"governance_activation", "governance_close", "close"}:
        return ()
    governance = _validated_governance_receipt(state)
    mode = str(governance.get("effective_mode") or "").strip().lower()
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
    evidence_gate = "governance_close" if gate == "close" else gate
    seen: dict[str, dict[str, Any]] = {}
    for item in evidence:
        if (
            not isinstance(item, dict)
            or item.get("invalidated")
            or str(item.get("gate") or "") != evidence_gate
        ):
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
                # Evidence is append-only; the newest exact gate occurrence
                # supersedes an older receipt for continuation authority.
                seen[obligation] = item
    missing = [item for item in required if item not in seen]
    if missing:
        raise ValueError(
            f"{gate} requires typed governance obligation evidence: " + ", ".join(missing)
        )
    review = seen.get("independent_governance_review")
    if review is not None:
        review_gate = "governance_close" if gate == "close" else gate
        reviewer = str(
            review.get("reviewer_identity")
            or review.get("reviewer_id")
            or review.get("reviewer")
            or ""
        ).strip()
        reviewer_role = str(review.get("reviewer_role") or review.get("reviewer_role_name") or "").strip().lower()
        owner = str(governance.get("owner") or state.get("principal") or "").strip()
        review_attempt = next((
            item for item in state.get("attempts", [])
            if isinstance(item, dict)
            and not item.get("invalidated")
            and item.get("gate") == review_gate
            and item.get("attempt_id") == review.get("attempt_id")
        ), None)
        expected_reviewer = str(
            (review_attempt or {}).get("host_agent_id")
            or (review_attempt or {}).get("dispatch_ref")
            or (review_attempt or {}).get("attempt_id")
            or ""
        ).strip()
        expected_role = str(
            (review_attempt or {}).get("agent")
            or (review_attempt or {}).get("profile")
            or ""
        ).strip().lower()
        if (
            not reviewer
            or reviewer == owner
            or review.get("independent_reviewer") is not True
            or not expected_reviewer
            or reviewer != expected_reviewer
            or not expected_role
            or reviewer_role != expected_role
        ):
            raise ValueError("independent_governance_review requires the distinct server-issued governance assignment identity for this gate")
    verification = seen.get("verification_evidence")
    if verification is not None and not (
        verification.get("evidence_class") == "worker_attested"
        and str(verification.get("attempt_result_ref") or "").strip()
    ):
        raise ValueError(
            "verification_evidence is a worker attestation and requires its canonical result reference"
        )
    receipt = seen.get("audit_receipt")
    if receipt is not None and not str(receipt.get("attempt_result_ref") or "").strip():
        raise ValueError("audit_receipt requires a canonical attempt_result_ref")


def _governance_closure_basis_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _governance_digest_ref(value: object) -> str:
    digest = str(value or "").strip().lower()
    if digest.startswith("sha256:"):
        digest = digest[7:]
    return "sha256:" + digest if re.fullmatch(r"[0-9a-f]{64}", digest) else ""


def validate_current_governance_closure_basis(
    state: dict[str, Any],
    basis: Mapping[str, Any],
    *,
    artifact_root: Path,
    attempt: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a closure basis to the exact active plan receipt and policy."""
    if basis.get("schema") != "cortex/governance-closure-basis/v3":
        raise ValueError("governance closure basis schema is unsupported")
    required_kinds = set(basis.get("required_verification_kinds") or [])
    server_observed_kinds = set(basis.get("server_observed_verification_kinds") or [])
    worker_attested_kinds = set(basis.get("worker_attested_verification_kinds") or [])
    missing_kinds = {
        kind for kind in required_kinds
        if (
            kind == "manifest_reconciliation" and kind not in server_observed_kinds
        ) or (
            kind != "manifest_reconciliation" and kind not in worker_attested_kinds
        )
    }
    if missing_kinds or sorted(missing_kinds) != sorted(basis.get("missing_verification_kinds") or []):
        raise ValueError("governance closure verification evidence is incomplete")
    receipts = basis.get("verification_evidence_receipts")
    if not isinstance(receipts, list) or any(
        not isinstance(item, Mapping)
        or item.get("evidence_class") not in {"server_observed", "worker_attested"}
        for item in receipts
    ):
        raise ValueError("governance closure verification evidence provenance is invalid")
    governance = _validated_governance_receipt(state)
    if governance.get("effective_mode") != "full" or basis.get("effective_mode") != "full":
        raise ValueError("governance closure requires effective_mode=full")
    task_id = str(state.get("task_id") or "")
    plan_revision = int(basis.get("plan_revision") or 0)
    receipt = ledger_db.get_plan_revision(artifact_root, task_id, plan_revision)
    active_receipt = ledger_db.get_active_plan_revision(artifact_root, task_id)
    if not isinstance(receipt, dict) or not isinstance(active_receipt, dict):
        raise ValueError("governance closure requires exact plan receipt authority")
    if active_receipt.get("current_plan_matches") is not True:
        raise ValueError("active plan receipt does not match the current plan")
    plan_digest = _governance_digest_ref(receipt.get("plan_digest"))
    if not plan_revision or not plan_digest:
        raise ValueError("active plan receipt is incomplete")
    if not (
        int(receipt.get("plan_revision") or 0) == plan_revision
        and _governance_digest_ref(basis.get("plan_digest")) == plan_digest
        and ledger_db.executable_plan_projection(receipt.get("plan") or {})
        == ledger_db.executable_plan_projection(active_receipt.get("current_plan") or {})
    ):
        raise ValueError("governance closure basis is stale for executable plan authority")
    policy_snapshot = governance.get("policy_snapshot")
    persisted_policy_digest = _governance_digest_ref(governance.get("policy_snapshot_digest"))
    canonical_policy_digest = (
        _governance_digest_ref(canonical_json.digest(policy_snapshot))
        if isinstance(policy_snapshot, dict) else ""
    )
    if not (
        isinstance(policy_snapshot, dict)
        and policy_snapshot
        and persisted_policy_digest
        and persisted_policy_digest == canonical_policy_digest
        and _governance_digest_ref(basis.get("policy_digest")) == persisted_policy_digest
    ):
        raise ValueError("governance closure policy authority is missing, stale, or inconsistent")
    from cortex_runtime.delegation_service import governance_closure_frontier_projection

    predecessor_result_refs = [
        safe_id(str(item)) for item in attempt.get("predecessor_result_refs") or []
    ]
    frontier = governance_closure_frontier_projection(
        artifact_root,
        state,
        predecessor_result_refs,
        active_receipt.get("current_plan") if isinstance(active_receipt.get("current_plan"), dict) else {},
    )
    if frontier.get("issues") or _governance_digest_ref(frontier.get("frontier_digest")) != _governance_digest_ref(
        basis.get("frontier_digest")
    ):
        raise ValueError("governance closure predecessor frontier is missing, stale, or inconsistent")
    loaded = db_load_task(artifact_root, safe_id(task_id))
    task_definition = loaded[0] if loaded is not None else None
    if not isinstance(task_definition, dict):
        raise ValueError("governance closure acceptance contract authority is unavailable")
    expected_acceptance_digest = acceptance_contract_digest(
        task_definition.get("acceptance_criteria") or [],
        task_definition.get("verification") or [],
        server_acceptance_obligations=(
            task_definition.get("server_acceptance_obligations") or []
        ),
        server_verification_obligations=(
            task_definition.get("server_verification_obligations") or []
        ),
    )
    if not (
        str(task_definition.get("acceptance_contract_digest") or "")
        == expected_acceptance_digest
        and str(state.get("acceptance_contract_digest") or "")
        == expected_acceptance_digest
        and str(attempt.get("acceptance_contract_digest") or "")
        == expected_acceptance_digest
        and _governance_digest_ref(basis.get("acceptance_contract_digest"))
        == expected_acceptance_digest
    ):
        raise ValueError("governance closure acceptance contract is missing, stale, or inconsistent")
    project_root = (
        Path(str(task_definition.get("project_root") or ""))
        if isinstance(task_definition, dict) else None
    )
    if project_root is None or not project_root.is_absolute():
        raise ValueError("governance closure project manifest authority is unavailable")
    current_manifest = capture_project_manifest(project_root)
    if _governance_digest_ref(current_manifest.get("digest")) != _governance_digest_ref(
        basis.get("manifest_digest")
    ):
        raise ValueError("governance closure project manifest is stale")
    return receipt


def validate_governance_closure_authority(
    state: dict[str, Any],
    *,
    artifact_root: Path,
    require_evidence: bool,
    current_attempt: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Verify one positive governance-close authority from canonical state."""
    governance = _validated_governance_receipt(state)
    completed_wave_refs = set(state.get("completed_orchestration_wave_ids") or [])
    closure_occurrences = [
        item for item in state.get("orchestration_wave_occurrences") or []
        if isinstance(item, Mapping)
        and str(item.get("phase_kind") or "") == "governance_close"
        and (
            (
                isinstance(current_attempt, Mapping)
                and str(item.get("wave_id") or "") == str(current_attempt.get("wave_ref") or "")
                and str(item.get("phase_ref") or "") == str(current_attempt.get("phase_ref") or "")
            )
            or str(item.get("wave_id") or "") in completed_wave_refs
        )
        and str(item.get("phase_ref") or "")
    ]
    closure_occurrence = closure_occurrences[-1] if closure_occurrences else None
    candidates = [
        item for item in state.get("attempts", [])
        if isinstance(item, dict)
        and not item.get("invalidated")
        and item.get("gate") == "governance_close"
        and str(item.get("attempt_result_ref") or "").strip()
        and (
            not isinstance(current_attempt, Mapping)
            or str(item.get("attempt_id") or "") == str(current_attempt.get("attempt_id") or "")
        )
        and isinstance(closure_occurrence, Mapping)
        and str(item.get("wave_ref") or "") == str(closure_occurrence.get("wave_id") or "")
        and str(item.get("phase_ref") or "") == str(closure_occurrence.get("phase_ref") or "")
    ]
    if not candidates and governance.get("effective_mode") != "full":
        return None
    if governance.get("effective_mode") != "full":
        raise ValueError("governance_closure_not_verified: effective_mode=full authority is absent")
    if not candidates:
        raise ValueError("governance_closure_not_verified: canonical governance-close result is absent")
    attempt = candidates[-1]
    result = attempt_protocol.get_attempt_result(
        artifact_root,
        task_id=str(state.get("task_id") or ""),
        attempt_id=str(attempt.get("attempt_id") or ""),
    )
    if not isinstance(result, dict) or not (
        result.get("result_ref") == attempt.get("attempt_result_ref")
        and result.get("status") == "completed"
        and result.get("lifecycle_status") == attempt_protocol.LIFECYCLE_COMPLETED
    ):
        raise ValueError("governance_closure_not_verified: canonical result is not finalized success")
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    identity = metadata.get("identity") if isinstance(metadata.get("identity"), dict) else {}
    closure = metadata.get("governance_closure") if isinstance(metadata.get("governance_closure"), dict) else {}
    try:
        ledger_db.validate_close_assignment_revision_authority(
            artifact_root,
            task_id=str(state.get("task_id") or ""),
            attempt=attempt,
            result_metadata=metadata,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "governance_closure_not_verified: private assignment revision authority is stale or invalid"
        ) from exc
    basis = attempt.get("governance_closure_basis")
    if not isinstance(basis, Mapping):
        raise ValueError("governance_closure_not_verified: server closure basis is absent")
    validate_current_governance_closure_basis(
        state, basis, artifact_root=artifact_root, attempt=attempt,
    )
    required_digests = (
        "plan_digest", "frontier_digest", "policy_digest", "manifest_digest",
        "acceptance_contract_digest",
    )
    if not (
        basis.get("complete") is True
        and basis.get("effective_mode") == "full"
        and basis.get("execution_verified") is True
        and not list(basis.get("issues") or [])
        and str(basis.get("plan_revision") or "").strip()
        and all(re.fullmatch(r"sha256:[0-9a-f]{64}", str(basis.get(key) or "")) for key in required_digests)
    ):
        raise ValueError("governance_closure_not_verified: server closure basis is incomplete or stale")
    if not (
        metadata.get("phase") == "governance_close"
        and metadata.get("phase_ref") == attempt.get("phase_ref")
        and metadata.get("wave_ref") == attempt.get("wave_ref")
        and metadata.get("operation_kind") == "close"
        and metadata.get("acceptance_contract_digest")
        == basis.get("acceptance_contract_digest")
        and metadata.get("plan_revision") == attempt.get("plan_revision")
        and _governance_digest_ref(metadata.get("plan_digest")) == _governance_digest_ref(attempt.get("plan_digest"))
        and identity.get("attempt_id") == attempt.get("attempt_id")
        and identity.get("dispatch_ref") == attempt.get("dispatch_ref")
        and identity.get("profile") == attempt.get("profile")
        and identity.get("agent") == attempt.get("agent")
        and closure.get("closure_outcome") == "verified"
        and closure.get("closure_basis_digest") == _governance_closure_basis_digest(basis)
        and str(closure.get("plan_revision") or "") == str(basis.get("plan_revision") or "")
        and str(attempt.get("acceptance_status") or "") == "passed"
        and isinstance(attempt.get("acceptance_evaluation"), Mapping)
        and attempt["acceptance_evaluation"].get("phase_ref") == attempt.get("phase_ref")
        and attempt["acceptance_evaluation"].get("wave_ref") == attempt.get("wave_ref")
        and not list(attempt["acceptance_evaluation"].get("missing_verification_kinds") or [])
    ):
        raise ValueError("governance_closure_not_verified: canonical closure attestation is invalid")
    if any(result.get(field) for field in ("findings", "decisions_needed", "unresolved")):
        raise ValueError("governance_closure_not_verified: canonical closure contains blocking semantics")
    if db_task_findings_blockers(artifact_root, str(state.get("task_id") or "")):
        raise ValueError("governance_closure_not_verified: canonical findings remain open")
    task_dir = db_task_artifact_path(artifact_root, str(state.get("task_id") or ""))
    if task_dir is None:
        raise ValueError("governance_closure_not_verified: task artifact directory is unavailable")
    if _open_blocking_questions(task_dir, state, str(attempt.get("attempt_id") or "")):
        raise ValueError("governance_closure_not_verified: blocking questions remain open")
    if require_evidence:
        evidence = [
            item for item in state.get("evidence", [])
            if isinstance(item, dict)
            and not item.get("invalidated")
            and item.get("gate") == "governance_close"
            and item.get("attempt_id") == attempt.get("attempt_id")
            and item.get("attempt_result_ref") == result.get("result_ref")
            and item.get("evidence_class") == "worker_attested"
        ]
        if not evidence:
            raise ValueError("governance_closure_not_verified: canonical worker attestation is absent")
        validate_governance_obligation_evidence(
            state, "governance_close", evidence, artifact_root=artifact_root,
        )
    return {"attempt": attempt, "result": result, "basis": dict(basis)}


def validate_completion_invariants(
    state: dict[str, Any],
    *,
    artifact_root: Path | None = None,
) -> None:
    _validated_governance_receipt(state)
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
        expected_handoff = terminal_handoff_identity(state)
        if (
            not state.get("handoff_created")
            or not expected_handoff
            or state.get("handoff_gate") != expected_handoff["phase_kind"]
            or state.get("handoff_wave_ref") != expected_handoff["wave_ref"]
            or state.get("handoff_phase_ref") != expected_handoff["phase_ref"]
        ):
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
    # Lane identity and materialization coordinates are host-owned.  Public
    # callers provide only semantic intent; private callers may still supply a
    # pre-issued lane_id for idempotent recovery.
    lane_id = safe_id(str(params.get("lane_id") or f"lane-{secrets.token_hex(8)}"))
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
        if not declarations:
            # Keep the model out of repo/worktree/branch ACL construction.  A
            # later materialize operation can use these server-generated
            # coordinates; no filesystem is created at lane creation time.
            project = select_project_root(params)
            repo = project
            branch = f"cortex/{lane_id}"
            worktree = project.parent / f"{project.name}.cortex.{lane_id}"
            sync_probe = run_git(repo, ["branch", "--show-current"])
            sync_from = sync_probe.stdout.strip() if sync_probe.returncode == 0 else ""
            declarations = [{
                "repo_path": str(repo),
                "worktree_path": str(worktree),
                "branch": branch,
                "sync_from": sync_from,
            }]
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
        expires_at = parse_expiry(
            params.get("expires_at")
            or (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        )
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
        run_id = safe_id(str(params.get("run_id") or f"run-{secrets.token_hex(8)}"))
        state["lease"] = {"owner": str(params.get("principal") or "local"), "run_id": redact(run_id, 256), "expires_at": expires_at.isoformat(), "acquired_at": now()}
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
    roles = {"scope": ["planner"], "plan": ["planner"], "discover": ["explorer"], "architecture": ["architect"], "database_architecture": ["database_architect"], "implementation": [implementation_selection["profile"]], "qa": ["qa_engineer", "build_verification"], "security": ["security_auditor"], "performance": ["performance_engineer"], "accessibility": ["accessibility_auditor", "accessibility_fixer"], "ux": ["ux_designer"], "review": ["code_reviewer"], "documentation": ["technical_writer"], "close": ["build_verification"]}
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
            requested_acceptance = normalize_init_text_list(
                params.get("acceptance_criteria"), "acceptance_criteria",
            )
            requested_verification = normalize_init_text_list(
                params.get("verification"), "verification",
            )
            requested_server_acceptance = normalize_init_text_list(
                params.get("server_acceptance_obligations"), "server_acceptance_obligations",
            )
            requested_server_verification = normalize_init_text_list(
                params.get("server_verification_obligations"), "server_verification_obligations",
            )
            requested_contract_digest = acceptance_contract_digest(
                requested_acceptance, requested_verification,
                server_acceptance_obligations=requested_server_acceptance,
                server_verification_obligations=requested_server_verification,
            )
            if (
                requested_acceptance != list(task_definition.get("acceptance_criteria") or [])
                or requested_verification != list(task_definition.get("verification") or [])
                or requested_server_acceptance
                != list(task_definition.get("server_acceptance_obligations") or [])
                or requested_server_verification
                != list(task_definition.get("server_verification_obligations") or [])
                or requested_contract_digest
                != str(task_definition.get("acceptance_contract_digest") or "")
            ):
                raise ValueError("existing task_id belongs to a different acceptance contract")
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
        init_server_acceptance = normalize_init_text_list(
            params.get("server_acceptance_obligations"), "server_acceptance_obligations",
        )
        init_scope = normalize_init_text_list(params.get("scope"), "scope")
        init_constraints = normalize_init_text_list(params.get("constraints"), "constraints")
        init_verification = normalize_init_text_list(params.get("verification"), "verification")
        init_server_verification = normalize_init_text_list(
            params.get("server_verification_obligations"), "server_verification_obligations",
        )
        init_pause_conditions = normalize_init_text_list(params.get("pause_conditions"), "pause_conditions")
        follow_up = params.get("follow_up") if isinstance(params.get("follow_up"), dict) else None
        baseline_ref = store_manifest_snapshot(task_dir, baseline)
        exact_user_request = str(params.get("user_request") or "").strip()
        if not exact_user_request:
            raise ValueError("init_task requires the exact non-empty user request")
        exact_user_request_digest = digest_text(exact_user_request)
        exact_acceptance_contract_digest = acceptance_contract_digest(
            init_acceptance_criteria, init_verification,
            server_acceptance_obligations=init_server_acceptance,
            server_verification_obligations=init_server_verification,
        )
        supplied_acceptance_digest = str(params.get("acceptance_contract_digest") or "").strip()
        if supplied_acceptance_digest and supplied_acceptance_digest != exact_acceptance_contract_digest:
            raise ValueError("init_task acceptance contract digest does not match the exact retained contract")
        task = {"schema": SCHEMA, "pipeline_contract_version": PIPELINE_CONTRACT_VERSION, "task_id": task_id, "task_number": task_number, "user_request": exact_user_request, "user_request_digest": exact_user_request_digest, "user_request_projection": redact(exact_user_request, 4000), "intent_clarification_required": bool(params.get("intent_clarification_required", False)), "intent_clarification_reason": redact(params.get("intent_clarification_reason", ""), 500) or None, "complexity": classification["complexity"], "base_pipeline": classification["base_pipeline"], "initial_pipeline": pipeline, "parallel_groups": parallel_groups, "requirements": receipt_requirements, "constraints": [redact(item, 1000) for item in init_constraints], "acceptance_criteria": list(init_acceptance_criteria), "server_acceptance_obligations": list(init_server_acceptance), "scope": [redact(item, 500) for item in init_scope], "verification": list(init_verification), "server_verification_obligations": list(init_server_verification), "acceptance_contract_digest": exact_acceptance_contract_digest, "budget": redact(params.get("budget", ""), 500), "pause_conditions": [redact(item, 1000) for item in init_pause_conditions], "plan_approval": plan_approval_policy, "plan_approval_user_requested": trusted_plan_review_requested, "initiative_ref": redact(params.get("initiative_ref", ""), 200) or None, "governance": sanitize_structured(params.get("governance")) if isinstance(params.get("governance"), dict) else None, "principal": principal, "user_language": user_language, "communication_profile": select_communication_profile(params), "internal_language": "en", "classification_id": classification_id, "project_root": baseline["project_root"], "initial_manifest_ref": baseline_ref, "tracker_policy": TRACKER_POLICY, "created_at": now()}
        if follow_up is not None:
            task["follow_up"] = sanitize_structured(follow_up)
        state = {"schema": SCHEMA, "pipeline_contract_version": PIPELINE_CONTRACT_VERSION, "task_id": task_id, "task_number": task_number, "status": "active", "principal": principal, "user_language": user_language, "communication_profile": select_communication_profile(params), "internal_language": "en", "complexity": classification["complexity"], "initiative_ref": redact(params.get("initiative_ref", ""), 200) or None, "governance": sanitize_structured(params.get("governance")) if isinstance(params.get("governance"), dict) else None, "acceptance_contract_digest": exact_acceptance_contract_digest, "current_pipeline": pipeline, "pipeline_obligations": list(pipeline), "parallel_groups": parallel_groups, "current_gates": active_gates({"current_pipeline": pipeline, "parallel_groups": parallel_groups, "completed_gates": [], "skipped_gates": []}), "completed_gates": [], "skipped_gates": [], "gates": {}, "attempts": [], "evidence": [], "locks": {}, "pipeline_changes": [], "adaptive_events": [], "recovery_events": [], "resume_events": [], "reassessment_receipts": [], "documentation_receipt": None, "manifest_receipts": [], "initial_manifest_ref": baseline_ref, "initial_manifest_digest": baseline["digest"], "manifest_snapshot_cleanup": {"status": "active", "at": now()}, "classification_receipt": classification_id, "handoff_created": False, "replan_count": 0, "require_delegation": classification["complexity"] in {"C2", "C3"}, "require_handoff": classification["complexity"] in {"C2", "C3"}, "plan_approval": {"policy": plan_approval_policy, "status": "pending_plan" if trusted_plan_review_requested else "not_required", "user_requested": trusted_plan_review_requested, "history": []}, "plan_approval_user_requested": trusted_plan_review_requested, "coordinator": activation["coordinator"], "parent_project_operations": activation["parent_project_operations"], "worker_visibility": activation["worker_visibility"], "worker_return_route": activation["worker_return_route"], "revision": 0, "updated_at": now()}
        coordinator_host_thread_id = str(params.get("coordinator_host_thread_id") or "")
        coordinator_epoch = params.get("coordinator_native_host_epoch")
        coordinator_epoch_fingerprint = str(
            params.get("coordinator_native_host_epoch_fingerprint") or ""
        )
        if (
            not coordinator_host_thread_id
            or isinstance(coordinator_epoch, bool)
            or not isinstance(coordinator_epoch, int)
            or coordinator_epoch < 1
            or not coordinator_epoch_fingerprint.startswith("hmac-sha256:")
        ):
            raise ValueError("init_task requires authenticated coordinator host-epoch ownership")
        state["coordinator_host_thread_id"] = coordinator_host_thread_id
        state["coordinator_native_host_epoch"] = coordinator_epoch
        state["coordinator_native_host_epoch_fingerprint"] = coordinator_epoch_fingerprint
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
        result_contract_payload = {
            "schema": "cortex/immutable-result-contract/v1",
            "task_id": task_id,
            "user_request": exact_user_request,
            "acceptance_criteria": list(init_acceptance_criteria),
            "verification": list(init_verification),
            "server_acceptance_obligations": list(init_server_acceptance),
            "server_verification_obligations": list(init_server_verification),
            "acceptance_contract_digest": exact_acceptance_contract_digest,
        }
        result_contract_artifact = store_immutable_artifact(
            task_dir,
            task_id,
            kind="result_contract",
            title="intent/result-contract.json",
            mime_type="application/json",
            content=canonical_json.dumps(result_contract_payload),
            export_path="intent/result-contract.json",
        )
        task["result_contract_artifact_ref"] = result_contract_artifact["artifact_ref"]
        task["result_contract_artifact_path"] = "intent/result-contract.json"
        task["result_contract_artifact_digest"] = result_contract_artifact["digest_sha256"]
        task["result_contract_artifact_byte_size"] = result_contract_artifact["byte_size"]
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
    """Persist one server-compiled delegation inside the orchestration runtime."""
    return _record_delegation_service(params)



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
        if attempt.get("status") == RESULT_READY:
            canonical = attempt_protocol.get_attempt_result(
                root, task_id=state["task_id"], attempt_id=attempt_id,
            )
            stop = attempt.get("native_terminal_stop")
            result_ref = str(attempt.get("attempt_result_ref") or "")
            if (
                status != "passed"
                or not isinstance(canonical, dict)
                or canonical.get("lifecycle_status") != attempt_protocol.LIFECYCLE_COMPLETED
                or str(canonical.get("result_ref") or "") != result_ref
                or not isinstance(stop, Mapping)
                or stop.get("observed") is not True
                or str(stop.get("result_digest") or "") != digest_text(result_ref)
            ):
                raise ValueError(
                    "result-ready attempt requires its exact completed canonical result and terminal native Stop"
                )
        elif attempt.get("status") not in {"running", AWAITING_HOST_SPAWN}:
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
    lets the host retain the native-child recovery binding after compaction, but it must never
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



def _record_evidence_locked(task_dir: Path, state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
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
    # Preserve the normal C2 evidence workflow while keeping semantic records
    # distinct from server observations. Caller-authored fields never create
    # command/browser/console/network execution authority.
    if state_governance.get("effective_mode") == "light":
        if gate == "documentation" or kind == "documentation":
            if "policy_snapshot" not in governance_obligations:
                governance_obligations.append("policy_snapshot")
        if kind not in {"command", "documentation"}:
            for obligation in ("decision_assumption_risk_evidence", "process_reflection"):
                if obligation not in governance_obligations:
                    governance_obligations.append(obligation)
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
        "digest": redact(params.get("digest", ""), 256) or digest_text(params.get("command", summary)),
        "evidence_class": (
            "worker_attested"
            if kind == "command" or "verification_evidence" in governance_obligations
            else "semantic_record"
        ),
        "command": redact(params.get("command", ""), 1000),
        "exit_code": int(exit_code) if exit_code is not None else None,
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
                    "next_action": ("continue_orchestration" if not eligible else "select_attempt_id"),
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
    del params
    raise ValueError(
        "Cortex has no host-owned command, browser, console, or network observer; "
        "verification execution cannot be promoted to server evidence"
    )


def record_gate(params: dict[str, Any]) -> dict[str, Any]:
    """Public delegating entrypoint for the independently testable gate policy."""
    from cortex_runtime.gate_transitions import record_gate as _record_gate

    return _record_gate(params)


def _append_closure_rework(
    params: dict[str, Any],
    *,
    source_result_refs: Sequence[str],
) -> dict[str, Any]:
    """Compile an unresolved closure result through the atomic rework path."""
    refs = list(dict.fromkeys(
        str(item).strip() for item in source_result_refs if str(item).strip()
    ))
    if len(refs) != 1:
        raise ValueError("closure rework requires one exact canonical source result")
    root = ledger_root(params)
    task_id = safe_id(str(params.get("task_id") or ""))
    with state_lock(root, operation="append_closure_rework", task_id=task_id):
        loaded = db_load_task(root, task_id)
        if loaded is None:
            raise ValueError("closure rework task became unavailable")
        task, state, _plan, artifact_dir = loaded
        source = next((
            item for item in state.get("attempts") or []
            if isinstance(item, dict)
            and str(item.get("attempt_result_ref") or "") == refs[0]
        ), None)
        if not isinstance(source, dict):
            raise ValueError("closure rework source is not the current canonical occurrence")
        requested_profile = canonical_profile(
            source.get("requested_profile")
            or source.get("profile")
            or source.get("agent")
            or ""
        )
        model = _v11_model(source.get("selected_model") or source.get("model"))
        effort = str(
            source.get("selected_reasoning_effort")
            or source.get("reasoning_effort")
            or ""
        ).strip().lower()
        if not requested_profile or not model or not effort:
            raise ValueError("closure rework source execution route is incomplete")
        task_dir = root / artifact_dir
        response = _v11_append_rework_wave_locked(
            params,
            task_dir,
            state,
            task,
            _v11_task_ref(task_id),
            {
                "source_result_ref": refs[0],
                "objective": (
                    "Correct every unresolved item in the exact canonical source result "
                    "without regressing previously verified behavior."
                ),
                "acceptance": (
                    "Every finding bound to the source result is corrected and the exact "
                    "independent verifier occurrence proves it closed."
                ),
                "profile": requested_profile,
                "model": model,
                "reasoning_effort": effort,
            },
            internal_response=True,
        )
        receipt = response.get("rework_receipt")
        plan = response.get("plan_record")
        corrective_wave_ref = (
            str(receipt.get("wave_ref") or "") if isinstance(receipt, Mapping) else ""
        )
        corrective_wave = next((
            wave for wave in (plan.get("waves") or [])
            if isinstance(wave, Mapping)
            and str(wave.get("wave_ref") or "") == corrective_wave_ref
        ), None) if isinstance(plan, Mapping) else None
        target_gate = str(
            next(iter(corrective_wave.get("gates") or []), "")
            if isinstance(corrective_wave, Mapping) else ""
        )
        if not target_gate:
            raise ValueError("compiled closure corrective occurrence is unavailable")
        response["target_gate"] = target_gate
        return response


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
    """Private fail-closed evidence/gate adapter.

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
        plan: Mapping[str, Any] = {}
        if state.get("orchestration_wave_occurrences"):
            plan = _load_orchestrate_plan(task_dir, state)
        mode = str(params.get("mode") or "verification").strip().lower()
        requested_gate = canonical_pipeline_gate(params.get("gate") or primary_gate(state) or "")
        completed_wave_ids = set(state.get("completed_orchestration_wave_ids") or [])
        skipped_wave_ids = set(state.get("skipped_orchestration_wave_ids") or [])
        active_occurrence = next((
            item for item in state.get("orchestration_wave_occurrences") or []
            if isinstance(item, dict)
            and str(item.get("wave_id") or item.get("wave_ref") or "") not in completed_wave_ids
            and str(item.get("wave_id") or item.get("wave_ref") or "") not in skipped_wave_ids
        ), None)
        exact_gate_receipt = None
        if (
            isinstance(active_occurrence, dict)
            and requested_gate in active_occurrence.get("gates", [])
        ):
            occurrence_key = str(active_occurrence.get("occurrence_key") or "")
            candidate = (state.get("gate_occurrences") or {}).get(
                f"{occurrence_key}:{requested_gate}"
            )
            if (
                isinstance(candidate, dict)
                and candidate.get("outcome") in {"passed", "skipped"}
                and str(candidate.get("wave_ref") or "")
                == str(active_occurrence.get("wave_ref") or active_occurrence.get("wave_id") or "")
                and str(candidate.get("phase_ref") or "")
                == str(active_occurrence.get("phase_ref") or "")
                and str(candidate.get("assignment_lineage_digest") or "")
                == str(active_occurrence.get("assignment_lineage_digest") or "")
            ):
                exact_gate_receipt = candidate
        # Host adapters may retry a completed composite call after a timeout.
        # Treat that exact gate transition as idempotent instead of trying to
        # consume a one-use result receipt a second time and opening a false
        # recovery event (the common source of "commit gate keeps failing").
        semantic_terminal = requested_gate in (
            set(state.get("completed_gates", [])) | set(state.get("skipped_gates", []))
        )
        if exact_gate_receipt is not None or (
            not state.get("orchestration_wave_occurrences") and semantic_terminal
        ):
            existing_gate = exact_gate_receipt or state.get("gates", {}).get(requested_gate, {})
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
                **({
                    "occurrence_key": active_occurrence.get("occurrence_key"),
                    "wave_ref": active_occurrence.get("wave_ref") or active_occurrence.get("wave_id"),
                    "phase_ref": active_occurrence.get("phase_ref"),
                    "assignment_lineage_digest": active_occurrence.get("assignment_lineage_digest"),
                } if isinstance(active_occurrence, dict) else {}),
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
        prior_close_verified = state.get("close_verified") is True
        cleanup_changed = False
        validate_completion_invariants(state, artifact_root=root)
        closure_authority = validate_governance_closure_authority(
            state, artifact_root=root, require_evidence=True,
        )
        expected_handoff = terminal_handoff_identity(state)
        if closure_authority is not None and (
            not expected_handoff
            or state.get("handoff_gate") != expected_handoff["phase_kind"]
            or state.get("handoff_wave_ref") != expected_handoff["wave_ref"]
            or state.get("handoff_phase_ref") != expected_handoff["phase_ref"]
        ):
            raise ValueError(
                "governance_closure_not_verified: final handoff is not bound to the exact selected terminal gate"
            )
        manifest_receipt = state.get("final_manifest_receipt")
        if (
            state.get("status") == "completed"
            and state.get("handoff_created") is True
            and isinstance(manifest_receipt, dict)
            and manifest_receipt.get("complete") is True
        ):
            state["close_verified"] = True
            state.setdefault("close_verified_at", now())
            if (state.get("manifest_snapshot_cleanup") or {}).get("status") != "completed":
                cleanup_completed_manifest_snapshots(task_dir, state)
                cleanup_changed = True
        if (
            state.get("status") != prior_status
            or len(state.get("completion_advice") or []) != prior_advice_count
            or (state.get("close_verified") is True) != prior_close_verified
            or cleanup_changed
        ):
            save_state(
                task_dir,
                task_dir / "state.sqlite",
                state,
                "close_audit",
                "recorded durable lifecycle close audit",
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
        plan = _load_orchestrate_plan(task_dir, state)
        guard_revision(state, params.get("expected_revision"))
        _validated_governance_receipt(state)
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
            # for safely handed off and stop waiting for that native child.
            active_host_checkpoints = [
                item["attempt_id"]
                for item in state.get("attempts", [])
                if isinstance(item, dict)
                and item.get("attempt_id") in pending_attempt_ids
                and str(item.get("worker_host_thread_id") or "").strip()
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
        active_handoff_gate = primary_gate(state)
        from cortex_runtime.orchestration_engine import (
            _has_open_orchestration_closure_obligations,
        )
        if _has_open_orchestration_closure_obligations(state):
            raise ValueError("handoff requires every exact rework route to be resolved")
        # The orchestrator-selected route may end at a verified dedicated
        # governance_close without carrying an advisory ``close`` wave.  Once
        # that last gate has completed, the executable frontier is empty; the
        # server-owned terminal handoff binds to that exact last completed
        # selected gate, not to a synthetic gate or a gate-less artifact.
        handoff_identity = terminal_handoff_identity(state)
        if handoff_identity is None:
            raise ValueError("handoff requires an exact completed terminal plan occurrence")
        handoff_gate = handoff_identity["phase_kind"]
        validate_terminal_handoff_acceptance(
            state, artifact_root=root, handoff_identity=handoff_identity,
        )
        loaded_task = db_load_task(root, str(state.get("task_id") or ""))
        task_definition = loaded_task[0] if loaded_task is not None else None
        if not isinstance(task_definition, dict):
            raise ValueError("handoff requires the immutable task acceptance contract")
        task_acceptance = list(task_definition.get("acceptance_criteria") or [])
        task_verification = list(task_definition.get("verification") or [])
        server_acceptance = list(
            task_definition.get("server_acceptance_obligations") or []
        )
        server_verification = list(
            task_definition.get("server_verification_obligations") or []
        )
        handoff_acceptance_digest = acceptance_contract_digest(
            task_acceptance,
            task_verification,
            server_acceptance_obligations=server_acceptance,
            server_verification_obligations=server_verification,
        )
        if not (
            str(task_definition.get("acceptance_contract_digest") or "")
            == handoff_acceptance_digest
            and str(state.get("acceptance_contract_digest") or "")
            == handoff_acceptance_digest
        ):
            raise ValueError("handoff acceptance contract is missing, stale, or inconsistent")
        db_require_no_task_finding_blockers(
            root, str(state.get("task_id") or ""), operation="handoff",
        )
        if active_handoff_gate is None or handoff_gate == "close":
            validate_governance_closure_authority(
                state, artifact_root=root, require_evidence=True,
            )
        completed = [redact(item, 1000) for item in params.get("completed", [])]
        completed_occurrences = _completed_handoff_occurrences(
            state,
            artifact_root=root,
            plan=plan,
        )
        next_action = redact(params.get("next_action", ""), 4000)
        if not completed or not completed_occurrences or not next_action:
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
        payload = {"schema": SCHEMA, "task_id": state["task_id"], "created_at": now(), "source_revision": state["revision"], "gate": handoff_gate, "wave_ref": handoff_identity["wave_ref"], "phase_ref": handoff_identity["phase_ref"], "acceptance_criteria": task_acceptance, "verification": task_verification, "server_acceptance_obligations": server_acceptance, "server_verification_obligations": server_verification, "acceptance_contract_digest": handoff_acceptance_digest, "completed": completed, "completed_occurrences": completed_occurrences, "files": [redact(item, 500) for item in files], "file_manifest_receipt": receipt, "decisions": [redact(item, 2000) for item in params.get("decisions", [])], "risks": [redact(item, 2000) for item in params.get("risks", [])], "next_action": next_action}
        path = task_dir / "handoffs" / f"{name}.json"
        write_json(path, payload)
        artifact = store_immutable_artifact(
            task_dir, state["task_id"], kind="handoff", title=f"handoffs/{name}.json",
            mime_type="application/json", content=_json_text(payload, label="handoff artifact", max_bytes=MAX_JSON_BYTES),
            export_path=f"handoffs/{name}.json",
        )
        state["handoff_created"] = True
        state["handoff_gate"] = handoff_gate
        state["handoff_wave_ref"] = handoff_identity["wave_ref"]
        state["handoff_phase_ref"] = handoff_identity["phase_ref"]
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
        expires_at = params.get("expires_at") or (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        entry = _claim_global_resource(root, resource, owner, "task", state["task_id"], expires_at, str(params.get("kind", "resource")))
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
    """Give a caller-correctable error a schema-owned retry direction."""
    del task_ref, project_root
    paths = [str(item.get("path")) for item in diagnostics if isinstance(item, dict) and item.get("path")]
    path_text = f" Fix these exact paths first: {', '.join(paths)}." if paths else ""
    return (
        f"Retry the same {operation} operation only after applying every diagnostic against its current advertised schema."
        + path_text
        + " Preserve unrelated valid values and do not invent authority or additional input."
    )


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
        diagnostics: list[dict[str, Any]] = []
        for source in exc.diagnostics:
            item = dict(source)
            # The wave compiler is shared with management operations.  At
            # start, keep its exact pointer/schema while projecting the
            # correction through the start form.
            item["code"] = "start_orchestration_validation_failed"
            item["phase"] = "preflight"
            item["fix"] = (
                "Correct this exact start_orchestration field and preserve "
                "every unrelated valid field."
            )
            diagnostics.append(item)
        return diagnostics

    message = redact(str(exc), 1000)
    raw_task = dict(params) if isinstance(params, Mapping) else None
    task_schema: dict[str, Any] = {}
    registry = globals().get("PUBLIC_SCHEMA_REGISTRY")
    start_schema = registry.get("start_orchestration") if isinstance(registry, dict) else None
    if isinstance(start_schema, dict):
        properties = start_schema.get("properties")
        if isinstance(properties, dict):
            task_schema = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    key: value
                    for key, value in properties.items()
                    if key != "waves"
                },
            }
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
        path = "request"
        field_schema = start_schema
        received = None

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


def _start_backend_failure(exc: BaseException) -> dict[str, Any] | None:
    """Project recognized storage failures without calling them form errors."""
    message = str(exc)
    is_storage_failure = isinstance(exc, (OSError, sqlite3.Error)) or message.startswith(
        ("Cortex database ", "Cortex migration ", "Cortex ledger ")
    )
    if not is_storage_failure:
        return None
    return _v11_error(
        "start_storage_unavailable",
        "Cortex could not open a safe current orchestration ledger for this project.",
        outcome="blocked",
    )


def _collect_lifecycle_diagnostics(lifecycle: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the complete public facade envelope before any ledger write.

    The runtime handlers still perform authoritative checks, but this pass is
    intentionally non-throwing and aggregates independent request mistakes so
    a coordinator can repair one payload instead of discovering errors one at
    a time.
    """
    diagnostics: list[dict[str, Any]] = []
    allowed_top_level = {
        "submission_id", "principal",
        "project_root", "task", "task_id", "wave_id", "waves", "host_capabilities", "result_refs", "payload",
        "reason", "terminal_recovery",
        "_materialization_fence",
    }
    for key in sorted(set(params) - allowed_top_level):
        diagnostics.append(_request_diagnostic(key, "unsupported lifecycle parameter", "a field accepted by this v11 lifecycle"))
    if lifecycle not in V11_LIFECYCLES:
        raise ValueError("unsupported internal v11 lifecycle")

    if os.environ.get("CORTEX_ROOT"):
        diagnostics.append(_request_diagnostic("environment.CORTEX_ROOT", "CORTEX_ROOT is not supported; use SessionStart workspace binding", "no CORTEX_ROOT override"))

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
                        diagnostics.append(_request_diagnostic(delegation_path, "delegation must be an object", "{gate, agent, objective, ownership, acceptance_criteria, verification}"))
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


def _engine_recover_native_dispatch_attestation_failure(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    attempt_id: str,
    dispatch_ref: str,
) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import recover_native_dispatch_attestation_failure

    return recover_native_dispatch_attestation_failure(
        params, task_dir, state, plan,
        attempt_id=attempt_id, dispatch_ref=dispatch_ref,
    )


def _engine_recover_answered_epoch_question(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    question_ref: str,
) -> dict[str, Any] | None:
    from cortex_runtime.orchestration_engine import recover_answered_epoch_question

    return recover_answered_epoch_question(params, task_dir, state, question_ref)


def _v11_resumed_host_epoch_orphans(
    task_dir: Path,
    state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    from cortex_runtime.orchestration_engine import resumed_host_epoch_orphans

    return resumed_host_epoch_orphans(
        task_dir, state, _load_orchestrate_plan(task_dir, dict(state)),
    )


def _v11_resumed_host_epoch_recovery_required(
    task_dir: Path,
    state: Mapping[str, Any],
) -> bool:
    from cortex_runtime.orchestration_engine import resumed_host_epoch_recovery_required

    return resumed_host_epoch_recovery_required(
        task_dir, state, _load_orchestrate_plan(task_dir, dict(state)),
    )


def _v11_host_epoch_fence_response(
    task_dir: Path,
    state: Mapping[str, Any],
    task_ref: str,
) -> dict[str, Any] | None:
    """Fence every non-resume lifecycle before replaying a dead child."""
    try:
        required = _v11_resumed_host_epoch_recovery_required(task_dir, state)
    except ValueError:
        return _v11_error(
            "native_host_epoch_recovery_unavailable",
            "This active task has no authenticated recoverable host epoch. Stop task-scoped calls; "
            "do not wait, spawn, continue, or infer ownership.",
            outcome="failed",
            task_ref=task_ref,
            retryable=False,
            state_mutated=False,
        )
    if not required:
        boundary = _v11_pending_context_boundary(task_dir, state)
        if boundary is None:
            return None
        return _v11_response({
            "ok": True,
            "state": "context_inspection_required",
            "task_id": str(state.get("task_id") or ""),
            "content": (
                "Call inspect_orchestration now with the exact task_ref and coordinator_ref, then consume "
                "every returned lifecycle page until complete=true. Do not wait, replay a followup_task, "
                "continue, resume, or create a child before the final inspection page."
            ),
        }, task_ref)
    return _v11_response({
        "ok": True,
        "state": "host_epoch_resume_required",
        "task_id": str(state.get("task_id") or ""),
        "content": (
            "Call resume_orchestration exactly once now with the exact task_ref and coordinator_ref. "
            "Do not wait, read, continue, replay a followup_task, or create a child first."
        ),
    }, task_ref)


def _v11_pending_context_boundary(
    task_dir: Path,
    state: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return one authenticated same-incarnation compaction boundary."""
    session_id = _required_host_thread_id()
    project = workspace_for_session(session_id)
    if project is None:
        raise ValueError("native context boundary workspace is unavailable")
    root = ledger_root_path_internal(project, create=False)
    loaded = _v11_task_state(root, str(state.get("task_id") or ""))
    if loaded is None or loaded[0].resolve() != task_dir.resolve():
        raise ValueError("native context boundary task binding is unavailable")
    boundary = ledger_db.context_boundary_pending(
        root, session_id, str(state.get("task_id") or ""),
    )
    if boundary is None:
        return None
    if (
        int(boundary.get("epoch") or 0) != int(state.get("coordinator_native_host_epoch") or 0)
        or not hmac.compare_digest(
            str(boundary.get("fingerprint") or ""),
            str(state.get("coordinator_native_host_epoch_fingerprint") or ""),
        )
    ):
        raise ValueError("native context boundary does not match the task host epoch")
    return boundary


def _load_orchestrate_plan(task_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import _load_orchestrate_plan as _implementation

    return _implementation(task_dir, state)


def _orchestrate_summary(state: dict[str, Any]) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import _orchestrate_summary as _implementation

    return _implementation(state)


def _orchestrate_pipeline_snapshot(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    from cortex_runtime.orchestration_engine import _orchestrate_pipeline_snapshot as _implementation

    return _implementation(state, plan)


def _effective_plan_frontier(
    plan: Mapping[str, Any], state: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    from cortex_runtime.orchestration_engine import _effective_plan_frontier as _implementation

    return _implementation(plan, state)


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


CANONICAL_PLAN_APPROVAL_POLICIES = {"auto", "required"}
CANONICAL_STATUS_VALUES = set(TERMINAL_ATTEMPT_STATUSES)


def _v11_error(
    code: str,
    message: object,
    *,
    outcome: str = "needs_input",
    candidates: list[dict[str, Any]] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    task_ref: str | None = None,
    retryable: bool | None = None,
    state_mutated: bool | None = None,
) -> dict[str, Any]:
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
    if retryable is not None:
        result["retryable"] = retryable
    if state_mutated is not None:
        result["state_mutated"] = state_mutated
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

    # The active step and canonical result references are server-owned.  They
    # are resolved only after the task identity and native terminal barriers
    # have been checked; the coordinator must not manufacture either value.
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
            or (response.get("visible_output") or {}).get("requires_user_decision")
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


# A server-issued dispatch_ref is the sole model-visible worker capability.
# Task, assignment, project, and attempt identity are resolved from private
# state and are never replayed by the model.
WORKER_DISPATCH_AUTHORITY_SCHEMA = "cortex/worker-dispatch-authority/v1"
WORKER_DISPATCH_OPERATIONS = frozenset({
    "worker_question",
    "record_attempt_event",
    "record_worker_finding",
    "complete_attempt",
    "read_dispatch_briefing",
    "read_worker_result",
})


class WorkerAssignmentError(ValueError):
    """Fail-closed worker authorization without an identity oracle."""

    def __init__(self, code: str = "worker_dispatch_unavailable") -> None:
        self.code = code
        super().__init__("worker dispatch is unavailable; coordinator recovery is required")


def _required_host_thread_id() -> str:
    """Return the exact transport-bound host identity or fail closed."""
    value = current_host_thread_id()
    if not isinstance(value, str) or not value:
        raise ValueError("host thread identity is unavailable")
    return value


def _audit_mcp_host_metadata(
    root: Path,
    host_thread_id: str,
    *,
    task_id: str,
    attempt_id: str | None = None,
    wave_id: str | None = None,
    equality: Mapping[str, bool] | None = None,
) -> None:
    """Persist only keyed identity digests after strict transport validation."""
    digests = {
        "mcp_thread": ledger_db.private_lifecycle_audit_digest(root, "mcp-thread", host_thread_id),
        "task": ledger_db.private_lifecycle_audit_digest(root, "task", task_id),
    }
    if attempt_id:
        digests["attempt"] = ledger_db.private_lifecycle_audit_digest(root, "attempt", attempt_id)
    if wave_id:
        digests["wave"] = ledger_db.private_lifecycle_audit_digest(root, "wave", wave_id)
    ledger_db.append_private_lifecycle_audit(root, {
        "event": "mcp_call", "tool": "public_tool", "outcome": "accepted",
        "reason": "metadata_valid", "digests": digests,
        "equality": dict(equality or {}), "observed_at": now(),
    }, task_id=task_id)


def _bind_worker_host_thread(
    project: Path,
    task_dir: Path,
    state: dict[str, Any],
    attempt: dict[str, Any],
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Atomically join one exact dispatch to its trusted native child thread."""
    host_thread_id = _required_host_thread_id()
    root = ledger_root_path_internal(project, create=False)
    task_id = str(state.get("task_id") or "")
    attempt_id = str(attempt.get("attempt_id") or "")
    dispatch_ref = str(attempt.get("dispatch_ref") or "")
    pending_start = True
    for delay in (0.0, 0.05, 0.10, 0.20, 0.40, 0.80):
        if delay:
            time.sleep(delay)
        probe = _v11_task_state(root, task_id)
        pending_start = ledger_db.get_native_host_start(root, host_thread_id) is None
        if not pending_start:
            break
    if pending_start:
        raise WorkerAssignmentError("native_subagent_start_required")
    with state_lock(root):
        loaded = _v11_task_state(root, task_id)
        if loaded is None:
            raise WorkerAssignmentError()
        fresh_dir, fresh_state, _task = loaded
        fresh_attempts = [
            candidate for candidate in fresh_state.get("attempts") or []
            if isinstance(candidate, dict)
            and str(candidate.get("attempt_id") or "") == attempt_id
            and str(candidate.get("dispatch_ref") or "") == dispatch_ref
            and not candidate.get("invalidated")
        ]
        if len(fresh_attempts) != 1:
            raise WorkerAssignmentError()
        fresh_attempt = fresh_attempts[0]
        # The primary Start identity is immutable, but a resumed question turn
        # has a newer exact Start boundary for the same native child.  Bind to
        # that latest boundary so generation/turn state cannot regress to the
        # original turn.  A sibling cannot replace it because the lookup is
        # constrained to this exact agent identity and session below.
        start_evidence = ledger_db.get_latest_native_host_start(root, host_thread_id)
        coordinator_thread = str(fresh_state.get("coordinator_host_thread_id") or "")
        if not isinstance(start_evidence, dict):
            raise WorkerAssignmentError("native_subagent_start_required")
        if (
            str(start_evidence.get("agent_id") or "") != host_thread_id
            or not coordinator_thread
            or not hmac.compare_digest(str(start_evidence.get("session_id") or ""), coordinator_thread)
        ):
            raise WorkerAssignmentError("native_subagent_start_mismatch")
        spawn_request = (
            fresh_attempt.get("spawn_request")
            if isinstance(fresh_attempt.get("spawn_request"), Mapping)
            else {}
        )
        selected_models = {
            str(value).strip()
            for value in (
                fresh_attempt.get("expected_model"),
                fresh_attempt.get("selected_model"),
                spawn_request.get("expected_model"),
            )
            if isinstance(value, str) and value.strip()
        }
        observed_model = start_evidence.get("model")
        host_epoch = ledger_db.get_native_host_epoch(root, coordinator_thread)
        attempt_epoch = fresh_attempt.get("native_host_epoch")
        if (
            not isinstance(host_epoch, Mapping)
            or isinstance(attempt_epoch, bool)
            or not isinstance(attempt_epoch, int)
            or attempt_epoch != int(host_epoch.get("epoch") or 0)
            or str(fresh_attempt.get("native_host_epoch_fingerprint") or "")
            != str(host_epoch.get("fingerprint") or "")
        ):
            raise WorkerAssignmentError("native_subagent_host_epoch_mismatch")
        if len(selected_models) != 1 or not isinstance(observed_model, str) or not observed_model:
            raise WorkerAssignmentError("native_subagent_model_unavailable")
        expected_model = next(iter(selected_models))
        if not hmac.compare_digest(observed_model, expected_model):
            raise WorkerAssignmentError("native_subagent_model_mismatch")
        for indexed_task_id in sorted(db_task_index(root)):
            indexed = _v11_task_state(root, str(indexed_task_id))
            if indexed is None:
                continue
            for candidate in indexed[1].get("attempts") or []:
                if not isinstance(candidate, dict) or candidate.get("invalidated"):
                    continue
                if str(candidate.get("worker_host_thread_id") or "") != host_thread_id:
                    continue
                if (
                    str(indexed_task_id) != task_id
                    or str(candidate.get("attempt_id") or "") != attempt_id
                ):
                    raise WorkerAssignmentError("native_subagent_thread_reuse_rejected")
        existing = str(fresh_attempt.get("worker_host_thread_id") or "")
        if existing and not hmac.compare_digest(existing, host_thread_id):
            raise WorkerAssignmentError("native_subagent_thread_mismatch")
        if not existing and ledger_db.native_child_binding_exists(
            root, host_thread_id, task_id=task_id, attempt_id=attempt_id,
        ):
            raise WorkerAssignmentError("native_subagent_thread_reuse_rejected")
        if not existing:
            fresh_attempt["worker_host_thread_id"] = host_thread_id
            fresh_attempt["worker_host_start_turn_id"] = str(start_evidence.get("turn_id") or "")
            fresh_attempt["worker_host_start_observed"] = True
            fresh_attempt["worker_host_model_attested"] = True
            fresh_attempt["worker_host_session_generation"] = 1
            fresh_attempt["worker_host_epoch"] = attempt_epoch
            fresh_attempt["worker_host_last_seen_at"] = str(start_evidence.get("observed_at") or now())
            if fresh_attempt.get("status") == AWAITING_HOST_SPAWN:
                fresh_attempt["status"] = "running"
                fresh_attempt["lifecycle_status"] = "running"
                fresh_attempt["host_resumable"] = True
            claim = fresh_attempt.get("worker_authority") if isinstance(fresh_attempt.get("worker_authority"), dict) else {}
            ledger_db.put_worker_session(root, {
                "task_id": task_id,
                "attempt_id": attempt_id,
                "generation": int(claim.get("generation") or 1),
                "host_agent_id": host_thread_id,
                "host_task_name": "",
                "host_tool": "spawn_agent",
                "status": "running",
                "resumable": True,
                "started_at": str(start_evidence.get("observed_at") or now()),
            })
            save_state(
                fresh_dir, fresh_dir / "state.sqlite", fresh_state,
                "worker_host_thread_bound", "private native worker thread bound",
            )
        else:
            previous_turn = str(fresh_attempt.get("worker_host_start_turn_id") or "")
            current_turn = str(start_evidence.get("turn_id") or "")
            generation = int(fresh_attempt.get("worker_host_session_generation") or 1)
            resumed_pending_dispatch = fresh_attempt.get("status") == AWAITING_HOST_SPAWN
            if resumed_pending_dispatch:
                fresh_attempt["status"] = "running"
                fresh_attempt["lifecycle_status"] = "running"
                fresh_attempt["host_resumable"] = True
            if current_turn and current_turn != previous_turn:
                generation += 1
                fresh_attempt["worker_host_session_generation"] = generation
                fresh_attempt["worker_host_start_turn_id"] = current_turn
                fresh_attempt["worker_host_start_observed"] = True
                fresh_attempt["worker_host_model_attested"] = True
                fresh_attempt["worker_host_last_seen_at"] = str(start_evidence.get("observed_at") or now())
                # A same-child follow-up is a new native turn.  A Stop from
                # the prior question/repair turn cannot authorize the new
                # turn's result, nor may it keep the task projected as
                # stopped while the child is actively running again.
                fresh_attempt.pop("native_incomplete_stop_evidence", None)
                fresh_attempt.pop("host_stopped_at", None)
                fresh_attempt.pop("host_stop_outcome", None)
                if fresh_attempt.get("status") == "waiting_question":
                    fresh_attempt["status"] = "running"
                    fresh_attempt["lifecycle_status"] = "running"
                    fresh_attempt["host_resumable"] = True
                if fresh_state.get("status") == "needs_input":
                    fresh_state["status"] = "active"
                sessions = [
                    item for item in db_list_worker_sessions(root, task_id)
                    if str(item.get("attempt_id") or "") == attempt_id
                    and str(item.get("host_agent_id") or "") == host_thread_id
                ]
                if sessions:
                    latest = max(sessions, key=lambda item: int(item.get("generation") or 1))
                    db_put_worker_session(root, {
                        **latest,
                        "generation": generation,
                        "status": "running",
                        "resumable": True,
                        "started_at": latest.get("started_at") or start_evidence.get("observed_at") or now(),
                        "terminated_at": None,
                    })
                save_state(
                    fresh_dir, fresh_dir / "state.sqlite", fresh_state,
                    "worker_host_turn_resumed", "same native child resumed after durable question",
                )
            elif resumed_pending_dispatch:
                save_state(
                    fresh_dir, fresh_dir / "state.sqlite", fresh_state,
                    "worker_host_turn_resumed", "same native child began a linked deficit-repair attempt",
                )
        _audit_mcp_host_metadata(
            root, host_thread_id, task_id=task_id, attempt_id=attempt_id,
            wave_id=str(fresh_attempt.get("orchestration_wave_id") or "") or None,
            equality={"thread_matches": True, "session_matches": True, "task_matches": True, "attempt_matches": True},
        )
        return fresh_dir, fresh_state, fresh_attempt


def _embedded_dispatch_refs(value: object, *, root: bool = True) -> frozenset[str]:
    """Return capability-shaped values copied outside the authority field."""
    if isinstance(value, str):
        return frozenset(re.findall(r"dispatch-[0-9a-f]{24}", value))
    if isinstance(value, Mapping):
        found: set[str] = set()
        for key, item in value.items():
            if root and str(key) == "dispatch_ref":
                continue
            found.update(_embedded_dispatch_refs(item, root=False))
        return frozenset(found)
    if isinstance(value, (list, tuple)):
        found: set[str] = set()
        for item in value:
            found.update(_embedded_dispatch_refs(item, root=False))
        return frozenset(found)
    return frozenset()


def _correctable_current_report_exposure(
    params: Mapping[str, Any],
    operation: str,
    supplied: str,
) -> bool:
    """Allow completion validation to repair one exact current-authority copy.

    This is not an authorization relaxation.  The dedicated authority field
    must already contain the exact current dispatch, the current branch must
    be the semantic submit form, and every capability-shaped value outside
    that field must be the same value inside the report text.  Any foreign
    value, repair-branch copy, or copy in another operation stays fail-closed.
    """
    if operation != "complete_attempt" or params.get("action") not in {"submit", "governance_closure"}:
        return False
    report = params.get("report")
    if not isinstance(report, str):
        return False
    report_refs = frozenset(re.findall(r"dispatch-[0-9a-f]{24}", report))
    return report_refs == frozenset({supplied}) and _embedded_dispatch_refs(params) == report_refs


def issue_worker_dispatch_authority(
    project_root: str | Path,
    *,
    task_id: str,
    attempt_id: str,
    dispatch_ref: str,
    profile: str,
    sandbox: str,
    access: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the private binding authenticated by one opaque dispatch_ref."""
    canonical_task_id = safe_id(task_id)
    canonical_attempt_id = safe_id(attempt_id)
    canonical_dispatch_ref = safe_id(dispatch_ref)
    canonical_worker_profile = canonical_profile(profile)
    canonical_sandbox = str(sandbox or "").strip()
    access_digest = digest_text(json.dumps(dict(access), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if not canonical_task_id or not canonical_attempt_id or not canonical_dispatch_ref or canonical_worker_profile not in AGENTS or not canonical_sandbox:
        raise ValueError("worker assignment identity is incomplete")
    claim = {
        "schema": WORKER_DISPATCH_AUTHORITY_SCHEMA,
        "task_id": canonical_task_id,
        "attempt_id": canonical_attempt_id,
        "dispatch_ref": canonical_dispatch_ref,
        "generation": 1,
        "profile": canonical_worker_profile,
        "audience": "worker",
        "sandbox": canonical_sandbox,
        "access_digest": access_digest,
        "operations": sorted(WORKER_DISPATCH_OPERATIONS),
        "issued_at": now(),
    }
    claim["dispatch_digest"] = hashlib.sha256(canonical_dispatch_ref.encode("ascii")).hexdigest()
    return claim


def authorize_worker_assignment(
    params: Mapping[str, Any],
    operation: str,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any], str]:
    """Resolve one dispatch_ref to exactly one current worker attempt."""
    supplied = str(params.get("dispatch_ref") or "").strip()
    if operation not in WORKER_DISPATCH_OPERATIONS:
        raise WorkerAssignmentError()
    if re.fullmatch(r"dispatch-[0-9a-f]{24}", supplied) is None:
        raise WorkerAssignmentError()
    embedded_refs = _embedded_dispatch_refs(params)
    if embedded_refs and not _correctable_current_report_exposure(
        params, operation, supplied,
    ):
        raise WorkerAssignmentError("worker_dispatch_exposure_rejected")
    try:
        project = _bound_project_root_for_dispatch_ref(supplied)
        if project is None:
            raise WorkerAssignmentError()
        root = ledger_root_path_internal(project, create=False)
        matches: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
        for task_id in sorted(db_task_index(root)):
            loaded = _v11_task_state(root, str(task_id))
            if loaded is None:
                continue
            task_dir, state, _task = loaded
            for candidate in state.get("attempts") or []:
                if not isinstance(candidate, dict) or str(candidate.get("dispatch_ref") or "") != supplied:
                    continue
                claim = candidate.get("worker_authority")
                if not isinstance(claim, dict) or claim.get("schema") != WORKER_DISPATCH_AUTHORITY_SCHEMA:
                    continue
                if (
                    str(claim.get("task_id") or "") != str(state.get("task_id") or "")
                    or str(claim.get("attempt_id") or "") != str(candidate.get("attempt_id") or "")
                    or str(claim.get("dispatch_ref") or "") != supplied
                    or str(claim.get("dispatch_digest") or "") != hashlib.sha256(supplied.encode("ascii")).hexdigest()
                    or int(claim.get("generation") or 0) != 1
                    or str(claim.get("profile") or "") != str(candidate.get("profile") or candidate.get("agent") or "")
                    or str(claim.get("audience") or "") != "worker"
                    or str(claim.get("sandbox") or "") != str((candidate.get("spawn_request") or {}).get("sandbox") or "")
                    or str(claim.get("access_digest") or "") != digest_text(json.dumps({
                        "route_category": (candidate.get("spawn_request") or {}).get("route_category"),
                    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                    or operation not in set(str(item) for item in (claim.get("operations") or []))
                ):
                    continue
                matches.append((task_dir, state, candidate))
        if len(matches) != 1:
            raise WorkerAssignmentError()
        task_dir, state, attempt = matches[0]
        if attempt.get("invalidated"):
            raise WorkerAssignmentError()
        profile = canonical_profile(str(attempt.get("profile") or attempt.get("agent") or ""))
        if profile not in AGENTS:
            raise WorkerAssignmentError()
        task_dir, state, attempt = _bind_worker_host_thread(
            Path(project), task_dir, state, attempt,
        )
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
    "record_worker_finding": frozenset({"record_attempt_event_closed"}),
    "complete_attempt": frozenset({"complete_attempt_repair_rejected"}),
    "read_dispatch_briefing": frozenset({
        "dispatch_briefing_response_invalid",
        "dispatch_briefing_unavailable",
    }),
    "read_worker_result": frozenset({"read_worker_result_not_authorized"}),
}
TERMINAL_FAILURE_CATEGORIES = frozenset({"authority", "integrity", "stale", "unavailable"})
_WORKER_RESPONSE_FAMILY = {
    "worker_question": "private.worker.question",
    "record_attempt_event": "private.worker.event",
    "record_worker_finding": "private.worker.event",
    "complete_attempt": "private.worker.completion",
    "read_dispatch_briefing": "private.worker.briefing",
    "read_worker_result": "private.result.read",
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
        claim = attempt.get("worker_authority") if isinstance(attempt.get("worker_authority"), dict) else {}
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
                current.get("worker_authority")
                if isinstance(current.get("worker_authority"), dict) else {}
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
                or current.get("dispatch_delivery_status") != "delivered"
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
    return validate_private_v11_response(_WORKER_RESPONSE_FAMILY[operation], updated)


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
    """Resolve one bearer bound to the exact current coordinator host thread."""
    host_thread_id = _required_host_thread_id()
    task_ref = str(params.get("task_ref") or "").strip()
    coordinator_ref = str(params.get("coordinator_ref") or "").strip().lower()
    if operation not in {
        "continue_orchestration", "manage_orchestration", "manage_governance",
        "read_worker_result",
    }:
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
    project = _select_internal_project_root(task.get("project_root"))
    root = ledger_root({"project_root": str(project)})
    bound_host_thread_id = str(state.get("coordinator_host_thread_id") or "")
    if (
        resolved_ref != task_ref
        or not bound_host_thread_id
        or not hmac.compare_digest(bound_host_thread_id, host_thread_id)
        or not _coordinator_capability_matches(root, state["task_id"], coordinator_ref)
    ):
        raise ValueError("coordinator authorization is unavailable")
    claims = _coordinator_capability_claims_for_task(root, state["task_id"])
    if not isinstance(claims, dict) or claims.get("audience") != "coordinator":
        raise ValueError("coordinator authorization is unavailable")
    _audit_mcp_host_metadata(
        root, host_thread_id, task_id=str(state["task_id"]),
        equality={"thread_matches": True, "task_matches": True},
    )
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
        if not include_completed and state.get("status") not in {
            "active", "blocked", "needs_input", "terminal_blocked",
        }:
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
    """Return the generated flat start authority and irreducible wave forms."""
    registry = globals().get("PUBLIC_SCHEMA_REGISTRY")
    start_schema = registry.get("start_orchestration") if isinstance(registry, dict) else None
    start_properties = start_schema.get("properties") if isinstance(start_schema, dict) else None
    waves_schema = start_properties.get("waves") if isinstance(start_properties, dict) else None
    wave_schema = waves_schema.get("items") if isinstance(waves_schema, dict) else None
    wave_properties = wave_schema.get("properties") if isinstance(wave_schema, dict) else None
    workers_schema = wave_properties.get("workers") if isinstance(wave_properties, dict) else None
    worker_schema = workers_schema.get("items") if isinstance(workers_schema, dict) else None
    task_properties = {
        key: value
        for key, value in start_properties.items()
        if key != "waves"
    }
    start_required = start_schema.get("required")
    task_required = [
        key for key in start_required
        if isinstance(key, str) and key != "waves"
    ] if isinstance(start_required, list) else []
    task_schema = {
        "type": "object", "additionalProperties": False,
        "properties": task_properties, "required": task_required,
    }
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
    """Validate the flat public task scalars from the tools/list registry."""
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
        add("", "must be an object", _v11_schema_object_card(task_schema), {"type": type(raw_task).__name__})
        return diagnostics

    # Schema-derived scalar and nested collection validation is deliberately
    # first so e.g. a malformed boolean, enum, and text list are repairable
    # in one request rather than leaking through to normalization one at a
    # time.  Required/route rules remain below because they depend on anyOf.
    diagnostics.extend(_v11_schema_value_diagnostics(
        {key: raw_task[key] for key in task_properties if key in raw_task},
        task_schema, pointer="", operation="start_orchestration",
    ))

    user_request_schema = task_properties["user_request"]
    if "user_request" not in raw_task or (isinstance(raw_task.get("user_request"), str) and not raw_task["user_request"].strip()):
        add("/user_request", "is required", _v11_schema_field_card(user_request_schema, "type", "minLength"))
    return diagnostics


def _v11_start_wave_preflight(raw_waves: object) -> list[dict[str, Any]]:
    """Validate model-facing wave structure before opening or reserving state."""
    diagnostics: list[dict[str, Any]] = []
    _start_schema, _task_schema, waves_schema, wave_schema, worker_schema = _v11_start_public_schema_forms()
    wave_properties = wave_schema["properties"]
    workers_schema = wave_properties["workers"]
    worker_properties = worker_schema["properties"]
    waves_field_card = _v11_schema_field_card(waves_schema, "type", "minItems")
    workers_field_card = _v11_schema_field_card(workers_schema, "type", "minItems", "maxItems")

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
            model = worker.get("model")
            effort = worker.get("reasoning_effort")
            if (
                isinstance(model, str)
                and model in MODEL_EFFORTS
                and isinstance(effort, str)
                and effort in SUPPORTED_EFFORTS
                and not model_effort_pair_is_allowed(MODEL_EFFORTS, model, effort)
            ):
                add(
                    f"{worker_pointer}/reasoning_effort",
                    f"reasoning_effort is not supported for {model}",
                    {
                        "type": "string",
                        "enum": list(MODEL_EFFORTS[model]),
                        "description": f"Choose an effort allowed for the selected {model} model.",
                    },
                    effort,
                )
    return diagnostics


def _v11_compact_waves(
    raw_waves: object,
    task: dict[str, Any],
    *,
    completed_gates: set[str] | None = None,
    prior_wave_phases: list[str] | None = None,
) -> list[dict[str, Any]]:
    # Validate the whole compact wave envelope before the mutating compiler
    # starts. The compiler below intentionally remains strict, but this pass
    # reports every independently invalid public field in one non-mutating
    # response instead of revealing one error per retry.
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
            "fix": "Correct this field in the same operation; do not resend unrelated fields.",
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
    canonical_prior_phases: list[str] = []
    for phase in prior_wave_phases or []:
        canonical_phase = canonical_pipeline_gate(str(phase or "").strip())
        if canonical_phase not in AVAILABLE_GATES:
            raise ValueError("prior wave phases must be canonical pipeline phases")
        canonical_prior_phases.append(canonical_phase)
    global_wave_offset = len(canonical_prior_phases)
    for local_wave_index, raw_wave in enumerate(raw_waves, 1):
        wave_index = global_wave_offset + local_wave_index
        wave_path = f"waves[{local_wave_index - 1}]"
        if not isinstance(raw_wave, dict):
            wave_diag(wave_path, "wave must be an object", "{phase_kind: ..., workers: [...]}")
            continue
        for key in sorted(set(raw_wave) - wave_public_fields):
            wave_diag(
                f"{wave_path}.{key}", "unsupported wave field",
                "a wave containing only phase and workers", received=raw_wave.get(key),
            )
        raw_phase = str(raw_wave.get("phase_kind") or "").strip()
        if not raw_phase:
            wave_diag(
                f"{wave_path}.phase_kind", "phase_kind is required on the wave",
                f"one of: {', '.join(sorted(AVAILABLE_GATES))}",
                field_schema={"type": "string", "enum": sorted(AVAILABLE_GATES)},
            )
            continue
        if raw_phase not in AVAILABLE_GATES:
            wave_diag(
                f"{wave_path}.phase_kind", f"unknown wave phase_kind {raw_phase!r}",
                f"one canonical phase: {', '.join(sorted(AVAILABLE_GATES))}",
                received=raw_phase,
                field_schema={"type": "string", "enum": sorted(AVAILABLE_GATES)},
            )
            continue
        gate = canonical_pipeline_gate(raw_phase)
        workers = raw_wave.get("workers")
        if not isinstance(workers, list) or not workers or len(workers) > 8:
            wave_diag(f"{wave_path}.workers", "workers must contain 1..8 worker objects", "an array of 1..8 worker objects")
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
            for model_key in ("model",):
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
            raw_model = worker.get("model")
            raw_effort = worker.get("reasoning_effort")
            if (
                isinstance(raw_model, str)
                and raw_model in MODEL_EFFORTS
                and isinstance(raw_effort, str)
                and raw_effort in SUPPORTED_EFFORTS
                and not model_effort_pair_is_allowed(MODEL_EFFORTS, raw_model, raw_effort)
            ):
                wave_diag(
                    f"{worker_path}.reasoning_effort",
                    f"reasoning_effort is not supported for {raw_model}",
                    f"one of: {', '.join(MODEL_EFFORTS[raw_model])}",
                    received=raw_effort,
                    field_schema={"type": "string", "enum": list(MODEL_EFFORTS[raw_model])},
                )
    if wave_diagnostics:
        raise ValidationFailure(wave_diagnostics)

    result: list[dict[str, Any]] = []
    for local_wave_index, raw_wave in enumerate(raw_waves, 1):
        wave_index = global_wave_offset + local_wave_index
        if not isinstance(raw_wave, dict) or set(raw_wave) != {"phase_kind", "workers"}:
            raise ValueError(f"waves[{local_wave_index - 1}] must contain exactly phase_kind and workers")
        raw_phase = str(raw_wave.get("phase_kind") or "").strip()
        if not raw_phase:
            raise ValueError(f"waves[{local_wave_index - 1}].phase_kind is required")
        gate = canonical_pipeline_gate(raw_phase)
        if gate not in AVAILABLE_GATES:
            suggestions = difflib.get_close_matches(gate, sorted(AVAILABLE_GATES), n=3)
            suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
            raise ValueError(f"unknown wave phase {raw_phase!r}" + suffix)
        workers = raw_wave.get("workers")
        if not isinstance(workers, list) or not workers or len(workers) > 8:
            raise ValueError(f"waves[{local_wave_index - 1}].workers must contain 1..8 workers")
        delegations: list[dict[str, Any]] = []
        for worker_index, worker in enumerate(workers, 1):
            if not isinstance(worker, dict):
                raise ValueError(f"waves[{local_wave_index - 1}].workers[{worker_index - 1}] must be an object")
            unsupported = sorted(set(worker) - worker_public_fields)
            if unsupported:
                raise ValueError(
                    f"waves[{local_wave_index - 1}].workers[{worker_index - 1}] contains unsupported field(s): "
                    + ", ".join(unsupported)
                )
            raw_profile = str(worker.get("profile") or "").strip()
            profile = canonical_profile(raw_profile) if raw_profile else _default_profile_for_gate(gate)
            if profile not in AGENTS:
                suggestions = difflib.get_close_matches(profile, sorted(AGENTS), n=3)
                suffix = f"; try {', '.join(suggestions)}" if suggestions else ""
                raise ValueError(f"unknown worker profile {raw_profile!r}" + suffix)
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
            for source, target in (("objective", "objective"),):
                if source in worker:
                    spec[target] = worker[source]
            selected_model = _v11_model(worker.get("model"))
            if selected_model:
                spec["model"] = selected_model
            if str(worker.get("reasoning_effort") or "").strip():
                spec["reasoning_effort"] = str(worker["reasoning_effort"]).strip().lower()
            spec["operation_kind"] = str(worker.get("operation_kind") or "").strip()
            spec["phase_kind"] = gate
            spec["phase_ref"] = f"phase-{wave_index:04d}"
            spec["wave_ref"] = f"wave-{wave_index:02d}"
            spec["wave_index"] = wave_index
            delegations.append(spec)
        result.append({
            "wave_id": f"wave-{wave_index:02d}",
            "wave_ref": f"wave-{wave_index:02d}",
            "wave_index": wave_index,
            "phase_ref": f"phase-{wave_index:04d}",
            "phase_kind": gate,
            "delegations": delegations,
        })
    return result


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
    attestation = attest_host_default_model()
    capabilities: dict[str, Any] = {
        "spawn_agent_models": sorted(SUPPORTED_MODELS),
    }
    # Only the exact Luna observation is a Cortex transport capability. An
    # arbitrary host default is neither rejected here nor republished as if it
    # could authorize omission; explicit Terra/Sol dispatches remain valid.
    if attestation.attested and attestation.model == CONFIGURED_DEFAULT_MODEL:
        capabilities["spawn_agent_default_model"] = CONFIGURED_DEFAULT_MODEL
    return capabilities


def _v11_luna_default_failure(status: str) -> dict[str, Any]:
    """Return a terminal, non-mutating receipt without exposing host config."""
    return _v11_error(
        "luna_default_route_unavailable",
        (
            "The current Codex host could not attest agents.default_subagent_model "
            "as gpt-5.6-luna. Restore that exact host setting, restart the Codex "
            "session so the runtime reloads it, and submit a new orchestration start. "
            f"Attestation status: {status}."
        ),
        outcome="failed",
        retryable=False,
        state_mutated=False,
    )


def _v11_native_arguments(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("host_tool") not in {None, "", "spawn_agent"}:
        raise WorkerAssignmentError("native_spawn_agent_transport_required")
    selected_model = str(request.get("model") or request.get("expected_model") or "").strip()
    configured_default = str(
        request.get("configured_default_model")
        or request.get("spawn_agent_default_model")
        or ""
    ).strip()
    arguments: dict[str, Any] = {
        "task_name": request.get("task_name"),
        "message": request.get("message"),
        "reasoning_effort": request.get("reasoning_effort"),
        "fork_turns": request.get("fork_turns") or "none",
    }
    if selected_model == CONFIGURED_DEFAULT_MODEL:
        # Codex's native Luna route is the configured default.  Passing model
        # explicitly is not a valid Luna selection in this host, and silently
        # falling through to another default would violate the coordinator's
        # canonical choice.
        attestation = attest_host_default_model()
        if (
            not attestation.attested
            or attestation.model != CONFIGURED_DEFAULT_MODEL
            or configured_default != CONFIGURED_DEFAULT_MODEL
        ):
            raise WorkerAssignmentError("luna_default_route_unavailable")
    elif selected_model:
        arguments["model"] = selected_model
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
        prepared_recovery: dict[str, Any] | None = None
        validated_response: dict[str, Any] | None = None
        bound = _bind_task_project_root({"task_ref": task_ref}, include_completed=True)
        if not isinstance(bound, dict):
            raise WorkerAssignmentError("worker_dispatch_scope_unavailable")
        root = ledger_root(bound)
        # Native dispatch delivery is a one-shot durable fence.  The engine
        # persists only a non-secret template.  Validate the complete private
        # response before consuming that fence: a response-construction defect
        # must never claim that an instruction was delivered when no MCP
        # response could be emitted.
        with state_lock(root, operation="native_spawn_dispatch_delivery"):
            resolved = _v11_resolve_task(bound, require_task_ref=True)
            if isinstance(resolved, dict):
                raise WorkerAssignmentError("worker_dispatch_rehydration_unavailable")
            task_dir, state, _project, _resolved_ref = resolved
            task_definition = load_task_definition(task_dir)
            hydrated: list[dict[str, Any]] = []
            delivered: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()

            def recover_luna_dispatch(attempt: dict[str, Any]) -> dict[str, Any]:
                return _engine_recover_native_dispatch_attestation_failure(
                    {
                        **bound,
                        "task_id": state["task_id"],
                        "principal": state.get("principal"),
                    },
                    task_dir,
                    state,
                    _load_orchestrate_plan(task_dir, state),
                    attempt_id=str(attempt.get("attempt_id") or ""),
                    dispatch_ref=str(attempt.get("dispatch_ref") or ""),
                )

            original_batch: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for request in requests:
                if not isinstance(request, dict):
                    raise WorkerAssignmentError("worker_dispatch_template_invalid")
                attempt_id = str(request.get("attempt_id") or "").strip()
                dispatch_ref = str(request.get("dispatch_ref") or "").strip()
                try:
                    valid_attempt_id = bool(attempt_id and safe_id(attempt_id) == attempt_id)
                except ValueError:
                    valid_attempt_id = False
                if not valid_attempt_id or not re.fullmatch(r"dispatch-[0-9a-f]{24}", dispatch_ref):
                    raise WorkerAssignmentError("worker_dispatch_template_invalid")
                identity = (attempt_id, dispatch_ref)
                if identity in seen:
                    raise WorkerAssignmentError("worker_dispatch_template_duplicate")
                seen.add(identity)
                candidates = [
                    item for item in state.get("attempts") or []
                    if isinstance(item, dict)
                    and str(item.get("attempt_id") or "") == attempt_id
                    and str(item.get("dispatch_ref") or "") == dispatch_ref
                ]
                if len(candidates) != 1:
                    raise WorkerAssignmentError("worker_dispatch_delivery_unavailable")
                original_batch.append((request, candidates[0]))

            # Resolve an already-prepared native-attestation recovery before
            # rejecting any individual delivery state. An unaffected sibling
            # may precede the retired Luna slot and already be delivered; its
            # position must not determine whether replay can discover the
            # canonical replacement frontier. Validate every retired Luna
            # against its exact durable receipt, then use the first canonical
            # occurrence only as an idempotent projection of that shared
            # current frontier.
            recovered_luna = sorted(
                (
                    candidate for _request, candidate in original_batch
                    if candidate.get("invalidated")
                    and candidate.get("status") == "superseded"
                    and candidate.get("dispatch_delivery_status") == "superseded"
                    and str(
                        candidate.get("selected_model")
                        or candidate.get("expected_model")
                        or ""
                    ) == CONFIGURED_DEFAULT_MODEL
                ),
                key=lambda item: (
                    str(item.get("attempt_id") or ""),
                    str(item.get("dispatch_ref") or ""),
                ),
            )
            for recovered_attempt in recovered_luna:
                recovery_projection = recover_luna_dispatch(recovered_attempt)
                if prepared_recovery is None:
                    prepared_recovery = recovery_projection

            if prepared_recovery is None:
                for _request, candidate in original_batch:
                    if (
                        candidate.get("status") != AWAITING_HOST_SPAWN
                        or candidate.get("dispatch_delivery_status") != "pending"
                    ):
                        raise WorkerAssignmentError(
                            "worker_dispatch_delivery_unavailable_"
                            + safe_id(str(candidate.get("status") or "missing"))
                            + "_"
                            + safe_id(str(candidate.get("dispatch_delivery_status") or "missing"))
                        )
                    hydrated.append({
                        **_rehydrate_dispatch_spawn_request(
                            task_dir, task_definition, candidate,
                        ),
                        "attempt_id": str(candidate.get("attempt_id") or ""),
                    })
                    delivered.append(candidate)
            if prepared_recovery is None:
                # Validate the final native wire shape before consuming the
                # one-shot delivery fence. Luna host-default drift is a
                # server-owned technical failure: retire only that exact
                # occurrence and advance it through Terra/Sol recovery.
                for request_index, request in enumerate(hydrated):
                    try:
                        _v11_native_arguments(request)
                    except WorkerAssignmentError as exc:
                        if exc.code != "luna_default_route_unavailable":
                            raise
                        prepared_recovery = recover_luna_dispatch(
                            delivered[request_index]
                        )
                        break
            if prepared_recovery is None:
                rendered_source = {**old, "spawn_requests": hydrated}
                validated_response = render_private_lifecycle_response(
                    rendered_source,
                    task_ref,
                    native_arguments=_v11_native_arguments,
                    public_schema=PUBLIC_ORCHESTRATION_SCHEMA,
                    coordinator_lock=COORDINATOR_LOCK,
                    include_result=include_result,
                    start_replayed=start_replayed,
                )
                from cortex_runtime.native_lifecycle_observer import ensure_current_host_epoch

                host_epoch = ensure_current_host_epoch(
                    root,
                    str(state.get("coordinator_host_thread_id") or ""),
                    source="dispatch_delivery",
                    allow_handoff=False,
                )
                if not isinstance(host_epoch, Mapping):
                    raise WorkerAssignmentError("native_host_epoch_unavailable")
                for attempt in delivered:
                    attempt["dispatch_delivery_status"] = "delivered"
                    attempt["assignment_delivered_at"] = now()
                    attempt["dispatch_response_status"] = "validated"
                    attempt["native_host_epoch"] = int(host_epoch["epoch"])
                    attempt["native_host_epoch_fingerprint"] = str(
                        host_epoch["fingerprint"]
                    )
                save_state(
                    task_dir,
                    task_dir / "state.sqlite",
                    state,
                    "native_spawn_dispatch_delivery",
                    ", ".join(str(item.get("attempt_id") or "") for item in delivered),
                )
        if prepared_recovery is not None:
            # Re-enter only after releasing the delivery lock so the durable
            # replacement response is serialized and consumed normally.
            return _v11_response(
                prepared_recovery,
                task_ref,
                include_result=include_result,
                start_replayed=start_replayed,
            )
        if validated_response is None:
            raise WorkerAssignmentError("worker_dispatch_response_unavailable")
        return validated_response
    response = render_private_lifecycle_response(
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
    return validate_private_v11_response("private.coordinator.lifecycle", {
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
    coordinator_host_thread_id = _required_host_thread_id()
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
            if not hmac.compare_digest(
                str(prior.get("coordinator_host_thread_id") or ""),
                coordinator_host_thread_id,
            ):
                raise ValueError("start transport identity does not match its immutable coordinator host thread")
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
                    "coordinator_host_thread_id": coordinator_host_thread_id,
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
                    "coordinator_host_thread_id": coordinator_host_thread_id,
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
            "coordinator_host_thread_id": coordinator_host_thread_id,
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
        raw_task_probe = params if isinstance(params, dict) else None
        envelope.extend(_v11_start_task_preflight(raw_task_probe))
        if "waves" in params:
            envelope.extend(_v11_start_wave_preflight(params.get("waves")))
        if envelope:
            return _v11_envelope_error("start_orchestration", envelope)
        try:
            selected_project_root = select_project_root({})
        except ValueError as exc:
            return _v11_error(
                "host_workspace_unavailable",
                str(exc),
                outcome="needs_input",
                retryable=True,
                state_mutated=False,
            )
        if set(params) - (start_public_fields | internal_start_fields):
            raise ValueError("start_orchestration accepts only the advertised flat fields")
        # The workspace is server-owned and may be carried internally after
        # resolving the current MCP thread's trusted SessionStart binding.
        params = {**params, "project_root": str(selected_project_root)}
        raw_task = {
            key: params[key]
            for key in task_public_fields
            if key in params
        }
        user_request = str(raw_task.get("user_request") or "").strip()
        if not user_request:
            raise ValueError(
                "user_request is required and must preserve the exact user-authored task without coordinator expansion"
            )
        intent_required, intent_reason = _intent_clarification_preflight(user_request)
        task = dict(raw_task)
        task["user_request"] = user_request
        # Public task text collections are persisted as current canonical
        # arrays, preserving traceability without scalar normalization.
        for field in (
            "requirements", "constraints", "acceptance_criteria", "scope",
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
        # Preserve coordinator-authored result criteria exactly. Backend
        # baselines are separate server-owned obligations so they can never
        # replace, reorder, or weaken the caller contract.
        task["server_acceptance_obligations"] = [
            item for item in SERVER_BASELINE_ACCEPTANCE_OBLIGATIONS
            if item not in task["acceptance_criteria"]
        ]
        task["server_verification_obligations"] = [
            item for item in SERVER_BASELINE_VERIFICATION_OBLIGATIONS
            if item not in task["verification"]
        ]
        task["acceptance_contract_digest"] = acceptance_contract_digest(
            task["acceptance_criteria"], task["verification"],
            server_acceptance_obligations=task["server_acceptance_obligations"],
            server_verification_obligations=task["server_verification_obligations"],
        )
        governance = resolve_governance(
            ledger_root(params),
            complexity=task["complexity"],
            requested_mode=params.get("governance_mode", "auto"),
            objective=user_request,
            requirements=task.get("requirements", []),
            scope=task.get("scope", []),
            task=task,
            initiative_ref=raw_task.get("initiative_ref"),
        )
        task["initiative_ref"] = governance.get("initiative_ref")
        task["governance"] = governance
        task["plan_approval"] = (
            "auto"
            if _is_knowledge_harvest_task(task)
            else _v11_plan_approval(params.get("plan_approval"), task["complexity"])
        )
        task["user_language"] = normalize_user_language(
            task.get("user_language"),
            user_request,
        )
        task["communication_profile"] = select_communication_profile(task)
        if params.get("waves") is None:
            raise ValueError("waves are required; the orchestrator must author every worker wave")
        waves = _append_governance_waves(
            _v11_compact_waves(params["waves"], task),
            task,
        )
        # Host configuration is observed by the server immediately before any
        # durable start reservation.  Bundled policy and model-visible inputs
        # can describe Luna, but neither can authorize omission of the native
        # ``model`` argument. Terra and Sol remain explicit and do not depend
        # on this host default.
        host_capabilities = _v11_host_capabilities()
        selected_models = {
            str(worker.get("model") or "").strip()
            for wave in params["waves"] if isinstance(wave, dict)
            for worker in (wave.get("workers") or []) if isinstance(worker, dict)
        }
        if CONFIGURED_DEFAULT_MODEL in selected_models:
            attestation = attest_host_default_model()
            if not attestation.attested or attestation.model != CONFIGURED_DEFAULT_MODEL:
                return _v11_luna_default_failure(attestation.status)
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
        current_host_thread = _required_host_thread_id()
        from cortex_runtime.native_lifecycle_observer import ensure_current_host_epoch

        initial_host_epoch = ensure_current_host_epoch(
            ledger_root(params),
            current_host_thread,
            source="start_materialization",
            allow_handoff=False,
        )
        if not isinstance(initial_host_epoch, Mapping):
            raise RuntimeError("start native host epoch is unavailable")
        old = _engine_start_lifecycle({
            "submission_id": submission_id,
            "project_root": params["project_root"],
            "principal": principal,
            "task": {
                **task,
                "task_id": task_id,
                "coordinator_host_thread_id": current_host_thread,
                "coordinator_native_host_epoch": int(initial_host_epoch["epoch"]),
                "coordinator_native_host_epoch_fingerprint": str(initial_host_epoch["fingerprint"]),
            },
            "waves": waves,
            "host_capabilities": host_capabilities,
            "_materialization_fence": lambda: _v11_materialization_fence(
                ledger_root(params), task_id, materialization_owner, materialization_generation,
            ),
        })
        with state_lock(ledger_root(params)):
            loaded = _v11_task_state(ledger_root(params), task_id)
            if loaded is None:
                raise RuntimeError("materialized start task state is unavailable")
            bound_task_dir, bound_state, _bound_task = loaded
            existing_host_thread = str(bound_state.get("coordinator_host_thread_id") or "")
            current_host_thread = _required_host_thread_id()
            if existing_host_thread and not hmac.compare_digest(existing_host_thread, current_host_thread):
                raise RuntimeError("materialized task coordinator host identity mismatch")
            if not existing_host_thread:
                bound_state["coordinator_host_thread_id"] = current_host_thread
            from cortex_runtime.native_lifecycle_observer import ensure_current_host_epoch
            # The first task ownership and authenticated process epoch are one
            # SQLite commit. A failed epoch write cannot strand a durable task
            # whose one-shot coordinator capability was never deliverable.
            with ledger_db.transaction(ledger_root(params)):
                host_epoch = ensure_current_host_epoch(
                    ledger_root(params),
                    current_host_thread,
                    source="start_materialization",
                    allow_handoff=False,
                )
                if not isinstance(host_epoch, Mapping):
                    raise RuntimeError("materialized task native host epoch is unavailable")
                bound_state["coordinator_native_host_epoch"] = int(host_epoch["epoch"])
                bound_state["coordinator_native_host_epoch_fingerprint"] = str(
                    host_epoch["fingerprint"]
                )
                save_state(
                    bound_task_dir,
                    bound_task_dir / "state.sqlite",
                    bound_state,
                    "coordinator_host_thread_bound",
                    "private coordinator host thread and process epoch bound",
                )
            _audit_mcp_host_metadata(
                ledger_root(params), current_host_thread, task_id=task_id,
                equality={"thread_matches": True, "task_matches": True},
            )
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
            response = validate_private_v11_response("private.coordinator.start", response)
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
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError, RuntimeError) as exc:
        if reserved_task_id and materialization_owner:
            _v11_release_start_materialization_lease(ledger_root(params), reserved_task_id, materialization_owner)
        if staged_authorization_task_id:
            _take_coordinator_capability(ledger_root(params), staged_authorization_task_id)
            _revoke_coordinator_capability(
                ledger_root(params), staged_authorization_task_id,
                reason="start_authorization_response_unavailable",
            )
        backend_failure = _start_backend_failure(exc)
        if backend_failure is not None:
            return backend_failure
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
    wave, frontier_attempts = _effective_plan_frontier(plan, state)
    if wave is None:
        raise ValueError("The active Cortex task has no current wave.")
    from cortex_runtime.assignment_compiler import compiled_wave_execution_position

    expected_step = compiled_wave_execution_position(
        [item for item in plan.get("waves") or [] if isinstance(item, Mapping)],
        str(wave.get("wave_ref") or ""),
    )
    # ``expected_step`` is derived from the canonical active wave.  It is
    # intentionally not accepted from the coordinator: stale relative steps
    # are reconciled from durable state rather than exposed as a model input.
    active_attempt_ids = [
        str(item.get("attempt_id") or "") for item in frontier_attempts
        if item.get("status") in {AWAITING_HOST_SPAWN, "running", RESULT_READY}
        and str(item.get("attempt_id") or "").strip()
    ]
    # A SubagentStop without a result is terminalized before recovery, but a
    # mixed wave still contains live workers.  Keep that exact failed slot in
    # the relative result contract; otherwise the handoff asks the
    # coordinator to submit a failed receipt that this adapter rejects as an
    # unknown slot.  Other terminal attempts (notably passed attempts kept
    # during repeated gate rework) remain omitted when a live retry exists.
    attempt_result_absent_failure_ids = {
        str(item.get("attempt_id") or "")
        for item in frontier_attempts
        if item.get("status") == "failed"
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
        for item in frontier_attempts
        if item.get("status") in {"failed", "blocked", "cancelled", "superseded"}
        and str(item.get("attempt_result_ref") or "").strip()
        and str(item.get("lifecycle_status") or "").strip().lower()
        in {"failed", "blocked", "cancelled", "superseded"}
        and not item.get("invalidated")
    }
    wave_attempt_ids = [
        str(item.get("attempt_id") or "") for item in frontier_attempts
        if str(item.get("attempt_id") or "").strip()
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


def _v11_continue_public_request_digest(params: Mapping[str, Any]) -> str:
    """Return the stable public portion of one continuation identity."""
    return _orchestrate_request_digest({
        key: value for key, value in params.items()
        if key not in {"task_ref", "results"}
    })


def _v11_continue_frontier_digest(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    *,
    wave: Mapping[str, Any] | None = None,
    attempt_ids: Sequence[str] | None = None,
) -> str:
    """Bind one private continuation key to its immutable active frontier.

    The public continuation intentionally carries only task/coordinator
    authority.  Wave, slot, assignment-generation, and canonical-result
    identity are therefore derived from the durable task.  This makes an
    exact retry within one frontier idempotent without conflating a later
    wave that uses the same public request shape.
    """
    selected_wave = wave
    selected_attempt_ids = list(attempt_ids) if attempt_ids is not None else None
    expected_step: int | None = None
    if selected_wave is None or selected_attempt_ids is None:
        selected_wave, selected_attempt_ids, expected_step = _v11_active_wave_context(
            params, task_dir, state,
        )
    else:
        plan = _load_orchestrate_plan(task_dir, state)
        from cortex_runtime.assignment_compiler import compiled_wave_execution_position
        expected_step = compiled_wave_execution_position(
            [item for item in plan.get("waves") or [] if isinstance(item, Mapping)],
            str(selected_wave.get("wave_ref") or ""),
        )
    attempts_by_id = {
        str(item.get("attempt_id") or ""): item
        for item in state.get("attempts") or []
        if isinstance(item, Mapping) and str(item.get("attempt_id") or "")
    }
    slots: list[dict[str, Any]] = []
    for slot, attempt_id in enumerate(selected_attempt_ids, 1):
        attempt = attempts_by_id.get(str(attempt_id))
        if not isinstance(attempt, Mapping):
            raise ValueError("active continuation frontier attempt is unavailable")
        authority = attempt.get("worker_authority")
        generation = (
            authority.get("generation")
            if isinstance(authority, Mapping) else None
        )
        slots.append({
            "slot": slot,
            "attempt_id": str(attempt_id),
            "assignment_generation": generation if type(generation) is int else None,
            "attempt_result_ref": str(attempt.get("attempt_result_ref") or "") or None,
        })
    frontier = {
        "task_id": str(state.get("task_id") or ""),
        "wave_id": str(selected_wave.get("wave_id") or ""),
        "step": expected_step,
        "gates": [str(item) for item in selected_wave.get("gates") or []],
        "slots": slots,
    }
    return _orchestrate_request_digest({
        "public_request_digest": _v11_continue_public_request_digest(params),
        "frontier": frontier,
    })


def _v11_continue_context(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    task_ref: str,
) -> tuple[dict[str, Any], list[str], str, str, dict[str, Any] | None]:
    root = ledger_root(params)
    with state_lock(root, operation="continue_native_completion_barrier", task_id=str(state.get("task_id") or "")):
        loaded = db_load_task(root, safe_id(str(state.get("task_id") or "")))
        if loaded is None:
            raise ValueError("active task became unavailable while continuation was being validated")
        _fresh_task, fresh_state, fresh_plan, artifact_dir = loaded
        fresh_task_dir = root / artifact_dir
        wave, attempt_ids, _ = _v11_active_wave_context(params, fresh_task_dir, fresh_state)
        require_wave_native_completion_observed(root, fresh_state, attempt_ids)
        request_digest = _v11_continue_frontier_digest(
            params,
            fresh_task_dir,
            fresh_state,
            wave=wave,
            attempt_ids=attempt_ids,
        )
        registry = _operation_registry(root)
        task_record = registry["tasks"].setdefault(fresh_state["task_id"], {})
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
        submission_id = safe_id("orchestration-continue-" + digest_text(fresh_state["task_id"] + ":" + str(wave["wave_id"]) + ":" + request_digest)[:20])
        old_params = {
            "submission_id": submission_id,
            "project_root": params["project_root"],
            "principal": fresh_state.get("principal"),
            "task_id": fresh_state["task_id"],
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
                "public_digest": _v11_continue_public_request_digest(params),
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
    request_digest = _v11_continue_public_request_digest(params)
    requested_ref = str(params.get("task_ref") or "").strip()
    registry = _operation_registry(ledger_root(params))
    matches = []
    for task_id, record in registry.get("tasks", {}).items():
        if requested_ref and _v11_task_ref(str(task_id)) != requested_ref:
            continue
        last = record.get("last_continue") if isinstance(record, dict) else None
        if (
            isinstance(last, dict)
            and (last.get("public_digest") or last.get("digest")) == request_digest
            and isinstance(last.get("response"), dict)
        ):
            matches.append(dict(last["response"]))
    return matches[0] if len(matches) == 1 else None


def _v11_active_replay(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    request_digest = _v11_continue_frontier_digest(params, task_dir, state)
    task_id = str(state.get("task_id") or "")
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
    current = _validated_governance_receipt(state)
    current_mode = str(current["effective_mode"])

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
        requested_mode=str(current["requested_mode"]),
        objective=task_probe["user_request"],
        requirements=task.get("requirements", []),
        scope=task.get("scope", []),
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


def _v11_unemitted_same_child_followup_attempt(
    root: Path,
    task_dir: Path,
    state: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Select the sole orphaned same-child response, without changing state."""
    plan = _load_orchestrate_plan(task_dir, dict(state))
    wave, frontier_attempts = _effective_plan_frontier(plan, state)
    if not isinstance(wave, Mapping):
        return None
    wave_ref = str(wave.get("wave_ref") or "")
    frontier: dict[str, Mapping[str, Any]] = {
        str(item.get("attempt_id") or ""): item
        for item in frontier_attempts
        if isinstance(item, Mapping) and str(item.get("attempt_id") or "")
    }
    if not wave_ref or not frontier:
        return None
    task_record = (
        _operation_registry(root).get("tasks", {}).get(str(state.get("task_id") or ""), {})
    )
    if not isinstance(task_record, dict) or isinstance(task_record.get("inflight_continue"), dict):
        return None
    last = task_record.get("last_continue")
    last_completed_at = str(last.get("completed_at") or "") if isinstance(last, dict) else ""
    candidates: list[dict[str, Any]] = []
    for attempt in state.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        spawn = attempt.get("spawn_request")
        delivered_at = str(attempt.get("assignment_delivered_at") or "")
        attempt_id = str(attempt.get("attempt_id") or "")
        current = frontier.get(attempt_id)
        if (
            not isinstance(current, Mapping)
            or str(attempt.get("wave_ref") or attempt.get("orchestration_wave_id") or "") != wave_ref
            or str(current.get("wave_ref") or current.get("orchestration_wave_id") or "") != wave_ref
            or not str(attempt.get("phase_ref") or "")
            or str(current.get("phase_ref") or "") != str(attempt.get("phase_ref") or "")
            or not str(attempt.get("plan_assignment_lineage_digest") or "")
            or str(current.get("plan_assignment_lineage_digest") or "")
            != str(attempt.get("plan_assignment_lineage_digest") or "")
            or attempt.get("invalidated")
            or attempt.get("status") != AWAITING_HOST_SPAWN
            or attempt.get("dispatch_delivery_status") != "delivered"
            or attempt.get("dispatch_response_status") not in {None, "response_contract_failed"}
            or attempt.get("same_child_deficit_repair_consumed") is not True
            or not isinstance(spawn, Mapping)
            or str(spawn.get("native_call") or "") != "followup_task"
            or not str(attempt.get("worker_host_thread_id") or "")
            or not delivered_at
            or (last_completed_at and last_completed_at >= delivered_at)
        ):
            continue
        candidates.append(attempt)
    if not candidates:
        return None
    if len(candidates) != 1:
        raise RuntimeError("unemitted same-child followup delivery is ambiguous")
    return candidates[0]


def _v11_reconcile_unemitted_same_child_followup(
    params: dict[str, Any],
    task_ref: str,
) -> dict[str, Any] | None:
    """Recover one server-render failure without duplicating a native turn."""
    root = ledger_root(params)
    recovered: dict[str, Any] | None = None
    with state_lock(root, operation="reconcile_unemitted_same_child_followup"):
        resolved = _v11_resolve_task(params, require_task_ref=True)
        if isinstance(resolved, dict):
            return None
        task_dir, state, _task, _resolved_ref = resolved
        attempt = _v11_unemitted_same_child_followup_attempt(root, task_dir, state)
        if attempt is None:
            return None
        attempt["dispatch_delivery_status"] = "pending"
        attempt.pop("assignment_delivered_at", None)
        attempt["dispatch_response_recovery"] = "unemitted_followup_reissued"
        save_state(
            task_dir,
            task_dir / "state.sqlite",
            state,
            "unemitted_same_child_followup_reconciled",
            str(attempt.get("attempt_id") or ""),
        )
        task_definition = load_task_definition(task_dir)
        recovered = {
            "ok": True,
            "state": "ready_to_spawn",
            "task_id": state["task_id"],
            "wave_id": str(attempt.get("wave_ref") or attempt.get("orchestration_wave_id") or ""),
            "spawn_requests": [{
                **_rehydrate_dispatch_spawn_request(task_dir, task_definition, attempt),
                "attempt_id": str(attempt.get("attempt_id") or ""),
            }],
        }
    return recovered


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
        epoch_fence = _v11_host_epoch_fence_response(task_dir, state, task_ref)
        if epoch_fence is not None:
            return epoch_fence
        active_replay = _v11_active_replay(params, task_dir, state)
        if active_replay is not None:
            return active_replay
        unemitted_followup = _v11_reconcile_unemitted_same_child_followup(params, task_ref)
        if unemitted_followup is not None:
            return _v11_response(unemitted_followup, task_ref)
        if _plan_approval_is_pending(state):
            raise ValueError(
                "the completed plan is awaiting explicit user approval; use manage_orchestration "
                "with action='plan_prompt' to read the approval card before continuing"
            )
        _, attempt_ids, _ = _v11_active_wave_context(params, task_dir, state)
        # Native terminal Stop is the completion boundary.  Once it is
        # observed, resolve each exact canonical AttemptResult from the task
        # ledger; no result reference is accepted from the model.
        require_wave_native_completion_observed(ledger_root(params), state, attempt_ids)
        derived_results: list[dict[str, Any]] = []
        for slot, attempt_id in enumerate(attempt_ids, 1):
            attempt = _attempt(state, attempt_id)
            result_ref = str(attempt.get("attempt_result_ref") or "").strip()
            if not result_ref:
                raise ValueError("the active native worker has no canonical AttemptResult")
            derived_results.append({"worker": slot, "attempt_result_ref": result_ref})
        params = {**params, "results": derived_results}
        results = derived_results
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
            refs = ", ".join(str(item["question_ref"]) for item in open_questions)
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
        approval_state: dict[str, Any] | None = None
        if old.get("ok") and old.get("state") == "awaiting_plan_approval":
            loaded = db_load_task(ledger_root(params), safe_id(str(state["task_id"])))
            if loaded is not None:
                approval_state = loaded[1]
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
        if approval_state is not None and response.get("ok"):
            _, prompt_response = _v11_prompt_plan_approval(
                approval_state, task_ref,
            )
            if prompt_response is not None:
                page = _page_management_report(
                    ledger_root(params),
                    {"task_ref": task_ref, "action": "plan_prompt"},
                    str(prompt_response.get("content") or ""),
                )
                response["content"] = str(page.get("report") or "")
                if page.get("next_cursor"):
                    response["next_cursor"] = str(page["next_cursor"])
                response = validate_private_v11_response("private.coordinator.lifecycle", response)
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
    except NativeCompletionObservationRequired as exc:
        response = _v11_error(
            "native_completion_observation_required",
            exc,
            outcome="native_completion_observation_required",
            task_ref=resolved_task_ref,
        )
        response.update({
            "retryable": True,
            "state_mutated": False,
            "action": {"kind": "wait_for_bound_workers"},
        })
        return response
    except NativeCompletionObservationUnavailable as exc:
        response = _v11_error(
            "native_completion_observation_unavailable",
            exc,
            outcome="native_completion_observation_unavailable",
            task_ref=resolved_task_ref,
        )
        response.update({
            "retryable": True,
            "state_mutated": False,
            "action": {"kind": "server_recovery"},
        })
        return response
    except LedgerBusyError as exc:
        response = _v11_error("ledger_busy", exc, outcome="ledger_busy", task_ref=resolved_task_ref)
        response.update({
            "retryable": True,
            "state_mutated": False,
            "action": {"kind": "retry_same_operation"},
        })
        return response
    except ResponseValidationError as exc:
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
        error = _v11_error(
            "continue_response_contract_failed",
            "Cortex could not render the validated continuation response; use server recovery without changing the request.",
            outcome="failed",
            task_ref=resolved_task_ref,
        )
        error.update({
            "retryable": True,
            "state_mutated": True,
            "action": {"kind": "server_recovery"},
        })
        return error
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
    """Accept only a durable question ref and an optional exact answer blob."""
    if not isinstance(value, dict):
        raise ValueError("question management requires payload with question_ref")
    payload = dict(value)
    unknown = sorted(set(payload) - {"question_ref", "answer"})
    if unknown:
        raise ValueError("unsupported question payload fields: " + ", ".join(unknown))
    question_ref = payload.get("question_ref")
    if not isinstance(question_ref, str) or re.fullmatch(r"question-[A-Za-z0-9._:-]{1,160}", question_ref) is None:
        raise ValueError("question_ref must be the exact Cortex-issued question reference")
    if "answer" not in payload:
        return {"command": "ask", "question_ref": question_ref}
    answer = payload["answer"]
    if not isinstance(answer, str) or not answer:
        raise ValueError("answer must be a non-empty Unicode string")
    return {"command": "answer", "question_ref": question_ref, "answer": answer}


def _v11_question_resume_contract(
    result: dict[str, Any],
    state: dict[str, Any],
    *,
    root: Path,
) -> tuple[dict[str, str] | None, str | None]:
    """Derive the only resumable assignment target from a durable answer record.

    A coordinator can route the already-existing native child, but it must not
    reconstruct or choose assignment authority.  In particular, an answered
    stale question is useful audit history, not an authorization to wake a
    replacement or an old worker.  The public response therefore carries a
    contract only when the answer record and current active slot agree.
    """
    try:
        announced_ref = safe_id(str(result.get("question_ref") or ""))
        record = ledger_db.get_durable_question(
            root, safe_id(str(state.get("task_id") or "")), announced_ref,
        )
        if not isinstance(record, dict):
            return None, "question_answered_without_canonical_resume_record"
        record_ref = safe_id(str(record.get("question_ref") or ""))
        attempt_id = safe_id(str(record.get("attempt_id") or ""))
        profile = canonical_profile(record.get("profile") or "")
    except ValueError:
        return None, "question_answered_with_invalid_resume_identity"
    if (
        announced_ref != record_ref
        or profile not in AGENTS
        or record.get("status") != "answered"
        or not isinstance(record.get("answer"), str)
        or not record.get("answer")
    ):
        return None, "question_answered_with_invalid_resume_identity"

    attempt = next(
        (
            item for item in state.get("attempts", [])
            if isinstance(item, dict) and str(item.get("attempt_id") or "") == attempt_id
        ),
        None,
    )
    spawn_request = attempt.get("spawn_request") if isinstance(attempt, dict) else None
    worker_authority = attempt.get("worker_authority") if isinstance(attempt, dict) else None
    plan_authority = ledger_db.get_executable_plan_authority(
        root, str(state.get("task_id") or ""),
    )
    current_task_revision = (
        plan_authority.get("current_task_revision")
        if isinstance(plan_authority, Mapping) else None
    )
    current_plan = (
        plan_authority.get("current_plan")
        if isinstance(plan_authority, Mapping) else None
    )
    if (
        not isinstance(plan_authority, Mapping)
        or plan_authority.get("current_plan_matches") is not True
        or not isinstance(current_plan, Mapping)
    ):
        return None, "question_answered_without_current_plan_authority"
    _wave, current_frontier = _effective_plan_frontier(current_plan, state)
    current_attempt_ids = {
        str(item.get("attempt_id") or "")
        for item in current_frontier
        if isinstance(item, Mapping)
    }
    if (
        not isinstance(attempt, dict)
        or attempt.get("invalidated")
        or attempt.get("status") not in {AWAITING_HOST_SPAWN, "running", "waiting_question"}
        or attempt_id not in current_attempt_ids
        or canonical_profile(attempt.get("profile") or "") != profile
        or str(record.get("dispatch_ref") or "")
        != str(attempt.get("dispatch_ref") or "")
        or not str(attempt.get("worker_host_thread_id") or "").strip()
        or not isinstance(spawn_request, Mapping)
        or not str(spawn_request.get("task_name") or "").strip()
        or not isinstance(worker_authority, Mapping)
        or type(record.get("attempt_generation")) is not int
        or record.get("attempt_generation") != worker_authority.get("generation")
        or type(current_task_revision) is not int
        or type(record.get("task_revision")) is not int
        or record.get("task_revision") != current_task_revision
    ):
        return None, "question_answered_for_noncurrent_attempt"

    return {
        "question_ref": record_ref,
        "attempt_id": attempt_id,
        "profile": profile,
        "dispatch_ref": str(attempt.get("dispatch_ref") or ""),
        "task_name": str(spawn_request.get("task_name") or ""),
    }, None


QUESTION_RESUME_CONTRACT_SCHEMA = "cortex/question-resume-contract/v1"


def _v11_question_display(result: dict[str, Any]) -> str | None:
    question_text = result.get("question_text")
    if not isinstance(question_text, str) or not question_text:
        return None
    return question_text


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
    return validate_private_v11_response("private.coordinator.question_management", {
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


def _v11_issue_question_resume_instruction(
    root: Path,
    task_dir: Path,
    state: dict[str, Any],
    response: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """Fence the one legal same-child answer resume before exposing it.

    The durable answer and this issuance fence are separate records because
    the native follow-up is host-owned.  Persisting the fence before returning
    the model-visible action prevents a later wave read or idempotent answer
    replay from issuing a second follow-up. The exact dispatch-authorized
    poll_worker_question call acknowledges the resumed same child; hosts are
    not required to emit another SubagentStart for a follow-up turn.
    """
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    if str(result.get("status") or "") != "answered":
        return state, None
    with state_lock(
        root,
        operation="question_resume_instruction",
        task_id=str(state["task_id"]),
    ):
        loaded = _v11_task_state(root, safe_id(str(state["task_id"])))
        if loaded is None:
            raise ValueError("answered question task state is unavailable")
        fresh_dir, fresh_state, _task = loaded
        if fresh_dir != task_dir:
            raise ValueError("answered question task directory changed")
        resume_contract, _reason = _v11_question_resume_contract(
            result,
            fresh_state,
            root=root,
        )
        if resume_contract is None:
            return fresh_state, None
        attempt = _attempt(fresh_state, resume_contract["attempt_id"])
        existing = attempt.get("native_question_resume_offer")
        question_ref = resume_contract["question_ref"]
        if isinstance(existing, Mapping):
            if str(existing.get("question_ref") or "") == question_ref:
                return fresh_state, "pending"
            raise ValueError("another same-child question resume instruction is already active")
        evidence = attempt.get("native_incomplete_stop_evidence")
        attempt["native_question_resume_offer"] = {
            "question_ref": question_ref,
            "stop_sequence": int(evidence.get("sequence") or 0) if isinstance(evidence, Mapping) else 0,
            "session_generation": int(attempt.get("worker_host_session_generation") or 1),
            "issued_at": now(),
        }
        save_state(
            fresh_dir,
            fresh_dir / "state.sqlite",
            fresh_state,
            "native_question_resume_instruction_issued",
            "issued one same-child durable-answer follow-up instruction",
        )
        return fresh_state, "issued"


def _v11_question_response(
    response: dict[str, Any],
    state: dict[str, Any],
    task_ref: str,
    *,
    root: Path,
    resume_instruction: str | None = None,
) -> dict[str, Any]:
    if not response.get("ok"):
        return _v11_question_management_failure(task_ref, "The question operation could not be completed.")
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    status_value = str(result.get("status") or "").strip()
    if status_value == "answered":
        resume_contract, resume_reason = _v11_question_resume_contract(
            result,
            state,
            root=root,
        )
        if resume_contract is None:
            question_ref = str(result.get("question_ref") or "")
            if re.fullmatch(r"question-[A-Za-z0-9._:-]{1,160}", question_ref) is None:
                return _v11_question_management_failure(
                    task_ref, resume_reason or "The answered question is not resumable.",
                )
            return {
                "schema": "cortex/question-management/v11",
                "ok": True,
                "outcome": "question_answered_not_resumable",
                "question_ref": question_ref,
            }
        if resume_instruction == "pending":
            return {
                "schema": "cortex/question-management/v11",
                "ok": True,
                "outcome": "question_resume_pending",
                "question_ref": resume_contract["question_ref"],
            }
        resume_message = (
            "Resume the same Cortex assignment. Poll the durable answered question "
            f"{resume_contract['question_ref']} with poll_worker_question, use the complete "
            "canonical answer as task context, finish the assigned work, and call submit_attempt "
            "before returning."
        )
        return {
            "schema": "cortex/question-management/v11",
            "ok": True,
            "outcome": "question_answered",
            "question_ref": resume_contract["question_ref"],
            "resume": {
                "kind": "poll",
                "question_ref": resume_contract["question_ref"],
                "dispatch_ref": resume_contract["dispatch_ref"],
                "task_name": resume_contract["task_name"],
                "message": resume_message,
            },
        }
    elif status_value == "superseded":
        question_ref = str(result.get("question_ref") or "")
        if re.fullmatch(r"question-[A-Za-z0-9._:-]{1,160}", question_ref) is None:
            return _v11_question_management_failure(task_ref, "The superseded question is not resumable.")
        return {
            "schema": "cortex/question-management/v11",
            "ok": True,
            "outcome": "question_superseded",
            "question_ref": question_ref,
        }
    elif status_value == "pending_user_message":
        question_text = _v11_question_display(result)
        if question_text is None:
            return _v11_question_management_failure(task_ref, "The display question is unavailable.")
        question_ref = str(result.get("question_ref") or "")
        return {
            "schema": "cortex/question-management/v11",
            "ok": True,
            "outcome": "awaiting_user",
            "question_ref": question_ref,
            "question_text": question_text,
        }
    return _v11_question_management_failure(task_ref, "Question management returned no supported public state.")


def _v11_plan_approval_payload(value: object) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("plan_approval requires a payload object")
    payload = dict(value)
    unknown = sorted(set(payload) - {"decision", "approval_mode", "feedback", "request_id"})
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
    normalized = {
        "decision": "approve" if decision.startswith("approve_") else decision,
        **({"approval_mode": decision} if decision.startswith("approve_") else ({"approval_mode": approval_mode or "approve_with_recommendations"} if decision == "approve" else {})),
        **({"feedback": feedback} if feedback else {}),
        **({"request_id": redact(request_id, 200)} if request_id else {}),
    }
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


def _v11_plan_approval_copy(state: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Return only server-owned copy, with the canonical locale as fallback."""
    language = str(state.get("user_language") or "en").lower().split("-", 1)[0]
    return PLAN_APPROVAL_TRANSLATIONS.get(language, PLAN_APPROVAL_TRANSLATIONS["en"])


def _v11_plan_approval_content(
    review: Mapping[str, Any],
    visible_output: Mapping[str, Any],
    request_id: str,
) -> str:
    """Render one plain-text, page-safe plan card with only flat public calls."""
    lines = [
        "Cortex plan approval",
        f"request_id: {request_id}",
        "Reuse the exact task_ref and coordinator_ref already held for this task.",
        "If next_cursor is present, read the next page with manage_orchestration action='plan_prompt' and cursor=<exact next_cursor>.",
        "Approve with recommendations using manage_orchestration action='plan', the exact request_id, and decision='approve_with_recommendations'.",
        "Approve without recommendations using manage_orchestration action='plan', the exact request_id, and decision='approve_without_recommendations'.",
        "Cancel using manage_orchestration action='plan', the exact request_id, and decision='cancel'.",
        "Request changes using manage_orchestration action='plan_revise', the exact request_id, and text=<the user's requested changes verbatim>.",
    ]
    user_message = str(visible_output.get("message") or "").strip()
    if user_message:
        lines.extend(("", "User-facing plan review", user_message))
    summary = str(review.get("summary") or review.get("objective") or "").strip()
    if summary:
        lines.extend(("", "Plan summary", summary))
    packages = [item for item in review.get("work_packages") or [] if isinstance(item, Mapping)]
    if packages:
        lines.extend(("", "Work packages"))
        for package_index, package in enumerate(packages, 1):
            title = str(package.get("title") or package.get("id") or f"Package {package_index}").strip()
            objective = str(package.get("objective") or "").strip()
            lines.append(f"{package_index}. {title}" + (f": {objective}" if objective else ""))
            for task_index, task in enumerate(
                (item for item in package.get("microtasks") or [] if isinstance(item, Mapping)),
                1,
            ):
                task_title = str(task.get("title") or task.get("id") or f"Task {task_index}").strip()
                task_objective = str(task.get("objective") or "").strip()
                lines.append(
                    f"   {package_index}.{task_index} {task_title}"
                    + (f": {task_objective}" if task_objective else "")
                )
    verification = [str(item).strip() for item in review.get("verification") or [] if str(item).strip()]
    if verification:
        lines.extend(("", "Verification"))
        lines.extend(f"- {item}" for item in verification)
    risks = [
        str(item.get("summary") or item.get("message") or item.get("title") or "")
        if isinstance(item, Mapping) else str(item)
        for field in ("risks", "findings", "uncertainty")
        for item in review.get(field) or []
    ]
    risks = [item.strip() for item in risks if item.strip()]
    if risks:
        lines.extend(("", "Risks and uncertainty"))
        lines.extend(f"- {item}" for item in risks)
    recommendation = str(review.get("recommendation") or "").strip()
    rationale = str(review.get("recommendation_rationale") or "").strip()
    if recommendation or rationale:
        lines.extend(("", "Planner recommendation"))
        if recommendation:
            lines.append(recommendation)
        if rationale:
            lines.append(rationale)
    return "\n".join(lines)


def _v11_prompt_plan_approval(
    state: dict[str, Any],
    task_ref: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return a detailed ordinary-chat approval boundary without nested UI."""
    approval = _plan_approval(state)
    if approval.get("policy") != "required":
        raise ValueError("this task does not require post-plan approval")
    if approval.get("status") != "awaiting_user":
        raise ValueError("there is no pending plan approval for this task")
    review = dict(approval.get("review") or {})
    prompt, title, _approve_label, cancel_label, custom_label = _v11_plan_approval_copy(state)
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
        "llm_recommendation": {
            "choice_id": recommended_decision,
            "rationale": recommendation_rationale,
            "planner_recommendation": planner_recommendation,
        },
        "response_instructions": (
            "Reply in your next ordinary chat message with approval, cancellation, or the exact changes you want. "
            "For approval or cancellation, call manage_orchestration with action='plan', the exact request_id, and "
            "decision='approve_with_recommendations', decision='approve_without_recommendations', or decision='cancel'. "
            "For substantive changes, call manage_orchestration with action='plan_revise', the exact request_id, and "
            "text containing the user's requested changes verbatim."
        ),
        "coordinator_contract": (
            "Use only interaction.visible_output for the final ordinary user-language message. Never copy internal plan "
            "objects, paths, dependencies, result or request identifiers, dispatch instructions, or validation details "
            "into that message. Show its bounded summary, the single question, and the recommendation from "
            "llm_recommendation; then wait for one unambiguous approve-with-recommendations, approve-without-recommendations, revise, or cancel response. Preserve requested "
            "changes verbatim as plan_revise text and use the exact interaction_ref internally. End the turn immediately "
            "after presenting this visible output; do not continue orchestration in the same turn."
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
    interaction["visible_output"] = render_plan(
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
    interaction["visible_output"]["requires_user_decision"] = True
    interaction["visible_output"]["recommendation"] = recommendation_rendered["message"]
    interaction["visible_output"]["risks"] = public_risks(
        review.get("risks") or review.get("uncertainty") or review.get("findings"),
        config=plan_config,
        limit=4,
    )
    interaction["visible_output"]["why_it_matters"] = why_rendered["message"]
    quality = dict(interaction["visible_output"].get("quality") or {})
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
    interaction["visible_output"]["quality"] = quality
    approval_content = _v11_plan_approval_content(
        review,
        interaction["visible_output"],
        request_id,
    )
    interaction["internal"] = {
        "interaction_ref": request_id,
        "plan": interaction["plan"],
        "choices": interaction["choices"],
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
        "content": approval_content,
        "next_action": (
            f"{COORDINATOR_LOCK} Render chat_interaction.visible_output only in the user's language as one ordinary final "
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
        "user_request", "requirements", "constraints", "acceptance_criteria", "scope",
        "verification", "budget", "pause_conditions", "user_language", "language",
        "complexity", "plan_approval", "waves",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unsupported follow_up payload fields: " + ", ".join(unknown))
    user_request = str(value.get("user_request") or "").strip()
    if not user_request:
        raise ValueError("follow_up payload.user_request must preserve the exact corrective user request")
    if not isinstance(value.get("waves"), list) or not value["waves"]:
        raise ValueError("follow_up payload.waves must be a non-empty coordinator-authored wave array")
    task = {key: item for key, item in value.items() if key != "task_ref"}
    task["user_request"] = user_request
    return {"task": task}


def _v11_follow_up_context(
    source_dir: Path,
    source_state: dict[str, Any],
    source_task: dict[str, Any],
    source_task_ref: str,
) -> dict[str, Any]:
    """Build only source-derived, Desktop-openable corrective-task context."""
    if source_state.get("status") != "completed":
        raise ValueError(
            "follow_up requires a completed source task; while the original task is active, use "
            "start_auxiliary_worker when a durable worker question is pending"
        )
    # Select the terminal canonical wave server-side.  The model supplies no
    # result references, avoiding ambiguous/stale cross-wave selections.
    attempts = [
        item for item in source_state.get("attempts", [])
        if isinstance(item, dict)
        and str(item.get("attempt_result_ref") or "").strip()
        and str(item.get("lifecycle_status") or item.get("status") or "") in {"completed", "passed"}
    ]
    if not attempts:
        raise ValueError("follow_up requires at least one finalized canonical source result")
    wave_groups: dict[str, list[dict[str, Any]]] = {}
    for item in attempts:
        wave_groups.setdefault(str(item.get("orchestration_wave_id") or ""), []).append(item)
    def wave_order(wave_id: str) -> tuple[int, str]:
        match = re.search(r"(\d+)$", wave_id)
        return (int(match.group(1)) if match else -1, wave_id)
    selected_group = wave_groups[max(wave_groups, key=wave_order)]
    selected = [safe_id(str(item.get("attempt_result_ref") or "")) for item in selected_group][-16:]
    if not selected:
        raise ValueError("follow_up source result selection is unavailable")
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


def _v11_pending_question_auxiliary_steer(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    task_definition: dict[str, Any],
    task_ref: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch one distinct coordinator-authored wave without consuming a question."""
    from cortex_runtime.orchestration_engine import (
        _normalize_orchestrate_waves,
        _write_orchestrate_plan,
    )

    root = ledger_root(params)
    with state_lock(root, operation="pending_question_auxiliary", task_id=str(state.get("task_id") or "")):
        loaded = _v11_task_state(root, str(state["task_id"]))
        if loaded is None:
            raise ValueError("active steer task is unavailable")
        task_dir, state, task_definition = loaded
        current_gates = active_gates(state)
        questions = _open_blocking_questions(task_dir, state)
        attempts_by_id = {
            str(item.get("attempt_id") or ""): item
            for item in state.get("attempts", [])
            if isinstance(item, dict) and not item.get("invalidated")
        }
        pending = [
            question for question in questions
            if isinstance(question, dict)
            and str(question.get("status") or "") == "open"
            and isinstance(attempts_by_id.get(str(question.get("attempt_id") or "")), dict)
            and attempts_by_id[str(question.get("attempt_id") or "")].get("gate") in current_gates
            and (
                attempts_by_id[str(question.get("attempt_id") or "")].get("status") == "waiting_question"
                or attempts_by_id[str(question.get("attempt_id") or "")].get("lifecycle_status") == "paused_awaiting_user"
                or attempts_by_id[str(question.get("attempt_id") or "")].get("host_stop_outcome") == "awaiting_user"
            )
        ]
        if state.get("status") != "needs_input" or len(pending) != 1:
            raise ValueError(
                "an auxiliary steer wave requires exactly one durable open worker question on the current active wave"
            )
        question = pending[0]
        question_ref = str(question.get("question_ref") or "")
        question_attempt_id = str(question.get("attempt_id") or "")
        question_snapshot = {
            key: question.get(key)
            for key in (
                "question_ref", "attempt_id", "dispatch_ref", "status", "question_text",
                "answer", "answer_submission_id", "answer_digest", "answered_sequence", "answered_at",
            )
        }
        worker = payload.get("worker")
        if not isinstance(worker, dict):
            raise ValueError("start_auxiliary_worker requires one canonical worker")
        active_gate = current_gates[0] if len(current_gates) == 1 else ""
        if not active_gate:
            raise ValueError("an auxiliary steer wave requires one unambiguous active phase")
        raw_waves = [{"phase_kind": active_gate, "workers": [worker]}]

        plan = _load_orchestrate_plan(task_dir, state)
        request_digest = digest_text(json.dumps({
            "question_ref": question_ref,
            "worker": worker,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        auxiliary_waves = plan.setdefault("auxiliary_waves", [])
        existing = next(
            (
                item for item in auxiliary_waves
                if isinstance(item, dict) and item.get("request_digest") == request_digest
            ),
            None,
        )
        if isinstance(existing, dict):
            existing_ids = {str(item) for item in existing.get("attempt_ids") or []}
            requests = [
                {**_rehydrate_dispatch_spawn_request(task_dir, task_definition, item), "attempt_id": item["attempt_id"]}
                for item in state.get("attempts", [])
                if str(item.get("attempt_id") or "") in existing_ids
                and item.get("status") == AWAITING_HOST_SPAWN
                and item.get("dispatch_delivery_status") == "pending"
                and not item.get("invalidated")
            ]
            return _v11_response({
                "ok": True,
                "state": "ready_to_spawn" if requests else "waiting_workers",
                "task_id": state["task_id"],
                "wave_id": str(existing.get("wave_id") or ""),
                "spawn_requests": requests,
            }, task_ref)
        if any(
            isinstance(item, dict) and item.get("status") not in {"completed", "retired"}
            for item in auxiliary_waves
        ):
            return _v11_response({
                "ok": True, "state": "waiting_workers", "task_id": state["task_id"],
                "wave_id": None, "spawn_requests": [],
            }, task_ref)

        compiled = _v11_compact_waves(
            raw_waves,
            task_definition,
            completed_gates=set(state.get("completed_gates") or []),
        )
        normalized, _classification = _normalize_orchestrate_waves(
            compiled,
            task_definition,
            plan.get("host_capabilities") or {},
            str(task_definition["project_root"]),
        )
        auxiliary = normalized[0]
        if list(auxiliary.get("gates") or []) != [active_gate]:
            raise ValueError("an auxiliary steer wave must use the exact current active phase")
        auxiliary_id = "auxiliary-" + request_digest[:24]
        auxiliary["wave_id"] = auxiliary_id
        auxiliary["wave_ref"] = auxiliary_id
        auxiliary["phase_ref"] = "phase-" + request_digest[:24]
        auxiliary["status"] = "active"
        auxiliary["request_digest"] = request_digest
        auxiliary["question_ref"] = question_ref
        auxiliary["question_attempt_id"] = question_attempt_id
        for slot, spec in enumerate(auxiliary.get("delegations") or [], 1):
            spec["orchestration_wave_id"] = auxiliary_id
            spec["wave_ref"] = auxiliary_id
            spec["phase_ref"] = auxiliary["phase_ref"]
            spec["orchestration_delegation_key"] = f"{auxiliary_id}-{active_gate}-{slot:02d}"
            spec["assignment_lineage_digest"] = _assignment_lineage_digest(spec)
            spec["plan_assignment_lineage_digest"] = spec["assignment_lineage_digest"]

        spawn_requests: list[dict[str, Any]] = []
        attempt_ids: list[str] = []
        for spec in auxiliary.get("delegations") or []:
            observed = status({
                "project_root": params["project_root"],
                "principal": state.get("principal"),
                "task_id": state["task_id"],
            })
            delegated = record_delegation({
                "project_root": params["project_root"],
                "principal": state.get("principal"),
                "task_id": state["task_id"],
                "expected_revision": observed["state"]["revision"],
                "status_receipt": observed["status_receipt"],
                "_pending_question_auxiliary": True,
                **spec,
            })
            if delegated.get("recorded") is False:
                raise ValueError(str(delegated.get("reason") or "auxiliary delegation was not recorded"))
            state = delegated["state"]
            attempt_ids.append(str(delegated["attempt_id"]))
            spawn_requests.append({**dict(delegated["spawn_request"]), "attempt_id": delegated["attempt_id"]})
        auxiliary["attempt_ids"] = attempt_ids
        auxiliary_waves.append(auxiliary)
        plan["auxiliary_waves"] = auxiliary_waves[-8:]
        _write_orchestrate_plan(task_dir, plan)

        after = _open_blocking_questions(task_dir, state, question_attempt_id)
        current = next((item for item in after if item.get("question_ref") == question_ref), None)
        if not isinstance(current, dict) or any(current.get(key) != value for key, value in question_snapshot.items()):
            raise RuntimeError("auxiliary dispatch changed the bound durable question")
        return _v11_response({
            "ok": True,
            "state": "ready_to_spawn",
            "task_id": state["task_id"],
            "wave_id": auxiliary_id,
            "spawn_requests": spawn_requests,
        }, task_ref)


def _v11_active_steer(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    task_definition: dict[str, Any],
    task_ref: str,
) -> dict[str, Any]:
    """Amend an active task and resume addressable native workers in place."""
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    unknown = sorted(set(payload) - {"user_message", "user_language", "received_at"})
    if unknown:
        raise ValueError("unsupported steer payload fields: " + ", ".join(unknown))
    original = str(payload.get("user_message") or "").strip()
    language = normalize_user_language(
        payload.get("user_language") or task_definition.get("user_language"), original
    )
    canonical = original
    if not original:
        raise ValueError("steer payload.user_message is required")
    if not canonical:
        canonical = original
    if state.get("status") not in {"active", "blocked"}:
        raise ValueError(
            "steer applies only to active work; for a durable pending worker question, either "
            "answer_orchestration_question or use start_auxiliary_worker"
        )

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
        current_governance = _validated_governance_receipt(state)
        resolved_governance = resolve_governance(
            root,
            complexity=task_definition.get("complexity", state.get("complexity", "C2")),
            requested_mode=str(current_governance["requested_mode"]),
            objective="\n".join(
                item for item in (str(task_definition.get("user_request") or "").strip(), canonical) if item
            ),
            requirements=[*(task_definition.get("requirements") or []), canonical],
            scope=task_definition.get("scope", []),
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
                "from": str(current_governance["effective_mode"]),
                "to": "full",
                "reasons": list(resolved_governance.get("reasons") or []),
            }
        impact["active_attempt_actions"] = [
            {"attempt_id": str(item.get("attempt_id")), "action": "resume_worker"}
            for item in active_attempts if item.get("worker_host_thread_id")
        ]
        plan = _load_orchestrate_plan(task_dir, state)
        if not isinstance(plan, dict):
            raise ValueError("active steer requires the canonical compiled plan")
        from cortex_runtime.assignment_compiler import compiled_wave_execution_order
        compiled_waves = [
            item for item in plan.get("waves") or [] if isinstance(item, dict)
        ]
        compiled_wave_execution_order(compiled_waves)
        frontier_wave, _frontier_attempts = _effective_plan_frontier(plan, state)
        if not isinstance(frontier_wave, dict):
            raise ValueError("active steer requires one exact executable occurrence")
        frontier_wave_ref = str(frontier_wave.get("wave_ref") or "")
        frontier_position = next((
            index for index, wave in enumerate(compiled_waves)
            if str(wave.get("wave_ref") or "") == frontier_wave_ref
        ), -1)
        if frontier_position < 0:
            raise ValueError("active steer frontier is outside the canonical plan")

        def occurrence_identity(wave: Mapping[str, Any]) -> dict[str, Any]:
            gates = [
                str(item) for item in wave.get("gates") or [] if str(item).strip()
            ]
            identity = {
                "wave_ref": str(wave.get("wave_ref") or ""),
                "wave_index": wave.get("wave_index"),
                "phase_ref": str(wave.get("phase_ref") or ""),
                "phase_kind": str(wave.get("phase_kind") or ""),
                "gates": gates,
            }
            if (
                not identity["wave_ref"]
                or isinstance(identity["wave_index"], bool)
                or not isinstance(identity["wave_index"], int)
                or not identity["phase_ref"]
                or not identity["phase_kind"]
                or not gates
            ):
                raise ValueError("active steer occurrence identity is incomplete")
            return identity

        # Governance escalation and a revision whose preferred gate is absent
        # are advisory findings.  They must never manufacture a replacement
        # wave or rewrite the coordinator's chosen pipeline; that would leave
        # the engine waiting for a route the coordinator had not selected.
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
        earliest_affected = str(impact.get("earliest_affected_gate") or "")
        active_gate = current_gates[0] if current_gates else ""
        if earliest_affected and earliest_affected in (state.get("current_pipeline") or []) and (
            earliest_affected != active_gate or not active_attempts
        ):
            exact_matches = [
                (index, wave)
                for index, wave in enumerate(compiled_waves)
                if earliest_affected in {
                    str(item) for item in wave.get("gates") or []
                }
                or str(wave.get("phase_kind") or "") == earliest_affected
            ]
            current_match = [
                item for item in exact_matches if item[0] == frontier_position
            ]
            completed = [
                item for item in exact_matches if item[0] < frontier_position
            ]
            future = [item for item in exact_matches if item[0] > frontier_position]
            selected = (
                current_match[0]
                if current_match
                else future[0]
                if future
                else completed[-1]
                if completed
                else None
            )
            if selected is None:
                raise ValueError("material steer target has no exact compiled occurrence")
            _target_position, target_wave = selected
            # Preserve the same live worker for the user's steer. The engine
            # consumes this exact occurrence-bound receipt only after that
            # worker's canonical result and native completion have been
            # durably reduced. Repeated phase names can therefore never reset
            # all historical occurrences.
            state["pending_revision_impact"] = {
                **impact,
                "task_revision": revision_number,
                "active_gate_at_revision": active_gate,
                "target_occurrence": occurrence_identity(target_wave),
                "frontier_occurrence_at_revision": occurrence_identity(frontier_wave),
                "recorded_at": now(),
            }
        if (
            impact.get("requires_plan_revision")
            and not isinstance(state.get("pending_revision_impact"), dict)
        ):
            # An occurrence-changing revision is committed only by the
            # post-result occurrence compiler. Recording an unchanged plan
            # here would create a false durable winner and split the steer
            # across two plan revisions. Advisory/no-rework revisions may
            # still snapshot the unchanged chosen plan immediately.
            plan_revision = db_append_plan_revision(
                root, state["task_id"], task_revision=revision_number,
                impact=impact, plan=plan, status="active",
            )
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
            "message": canonical,
            "received_at": payload.get("received_at") or now(),
        })
        db_update_task_definition(root, task_definition)

        dispatches = []
        for attempt in active_attempts:
            attempt.setdefault("task_revision_started", revision_number - 1)
            attempt["latest_material_revision"] = revision_number
            host_agent_id = str(attempt.get("worker_host_thread_id") or "")
            spawn_request = attempt.get("spawn_request")
            host_task_name = str(
                spawn_request.get("task_name") or ""
            ).strip() if isinstance(spawn_request, Mapping) else ""
            if not host_agent_id or not host_task_name:
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
                "original_language": str(language), "canonical": canonical,
                "task_revision": revision_number,
            })
            dispatches.append({
                "worker": len(dispatches) + 1,
                "dispatch_kind": "resume_worker",
                "dispatch_ref": str(attempt.get("dispatch_ref") or ""),
                "attempt_id": attempt["attempt_id"],
                "host_agent_id": host_agent_id,
                "host_task_name": host_task_name,
                "message_id": durable["message_id"],
                "call": "followup_task",
                "arguments": {"target": host_task_name, "message": message},
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
            or attempt.get("dispatch_delivery_status") not in {"pending", "delivered"}
            or post_bootstrap_evidence
        ):
            raise ValueError("bootstrap failure target has post-bootstrap server evidence; follow the returned structured recovery on the same worker")

        terminal_at = now()
        attempt["status"] = "failed"
        attempt["lifecycle_status"] = "bootstrap_terminal_failure"
        attempt["dispatch_delivery_status"] = "bootstrap_terminal_failure"
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
        claim = attempt.get("worker_authority") if isinstance(attempt.get("worker_authority"), dict) else {}
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
                or attempt.get("dispatch_delivery_status") != "delivered"
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
            attempt["dispatch_delivery_status"] = "worker_terminal_failure"
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


def _v11_revise_future_pipeline(
    params: dict[str, Any],
    task_dir: Path,
    state: dict[str, Any],
    task_definition: dict[str, Any],
    task_ref: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Replace only pending future waves using completed canonical evidence."""
    from cortex_runtime.orchestration_engine import (
        _normalize_orchestrate_waves,
        _semantic_future_pipeline_digest,
        _write_orchestrate_plan,
    )
    from cortex_runtime.assignment_compiler import (
        compiled_wave_execution_position,
        next_compiled_wave_index,
    )

    root = ledger_root(params)
    task_id = str(state.get("task_id") or "")
    with state_lock(root, operation="revise_future_pipeline", task_id=task_id):
        loaded = db_load_task(root, safe_id(task_id))
        if loaded is None:
            raise ValueError("active task became unavailable during future-pipeline revision")
        fresh_task, fresh_state, plan, artifact_dir = loaded
        if not isinstance(plan, dict):
            raise ValueError("canonical orchestration plan is unavailable")
        fresh_dir = root / artifact_dir
        current_wave, _current_frontier = _effective_plan_frontier(plan, fresh_state)
        if not isinstance(current_wave, dict):
            raise ValueError("current executing wave is unavailable")
        waves = list(plan.get("waves") or [])
        current_step = compiled_wave_execution_position(
            waves, str(current_wave.get("wave_ref") or ""),
        )
        immutable = waves[:current_step]
        retired_future = waves[current_step:]
        attempted_wave_refs = {
            str(attempt.get("wave_ref") or "")
            for attempt in fresh_state.get("attempts") or []
            if isinstance(attempt, Mapping) and str(attempt.get("wave_ref") or "")
        }
        if any(
            not isinstance(wave, dict)
            or wave.get("status") not in {None, "pending"}
            or str(wave.get("wave_ref") or "") in attempted_wave_refs
            for wave in retired_future
        ):
            raise ValueError("only unexecuted pending future waves may be replaced")
        immutable_wave_ids = {str(wave.get("wave_id") or "") for wave in immutable if isinstance(wave, dict)}
        canonical_evidence: set[str] = set()
        for attempt in fresh_state.get("attempts") or []:
            if (
                not isinstance(attempt, dict)
                or attempt.get("invalidated")
                or str(attempt.get("orchestration_wave_id") or "") not in immutable_wave_ids
            ):
                continue
            result_ref = str(attempt.get("attempt_result_ref") or "")
            canonical = attempt_protocol.get_attempt_result(
                root, task_id=task_id, attempt_id=str(attempt.get("attempt_id") or ""),
            )
            if (
                result_ref
                and isinstance(canonical, dict)
                and str(canonical.get("result_ref") or "") == result_ref
                and canonical.get("lifecycle_status") in attempt_protocol.TERMINAL_LIFECYCLES
            ):
                canonical_evidence.add(result_ref)
        evidence_refs = [str(item) for item in payload.get("evidence_result_refs") or []]
        if not evidence_refs or len(evidence_refs) != len(set(evidence_refs)):
            raise ValueError("completed canonical evidence refs must be non-empty and unique")
        if any(ref not in canonical_evidence for ref in evidence_refs):
            raise ValueError("future-pipeline evidence must be a canonical result from an immutable wave")
        evidence_attempt_ids: list[str] = []
        for ref in evidence_refs:
            matches = [
                str(attempt.get("attempt_id") or "")
                for attempt in fresh_state.get("attempts") or []
                if isinstance(attempt, dict)
                and not attempt.get("invalidated")
                and str(attempt.get("attempt_result_ref") or "") == ref
            ]
            if len(matches) != 1:
                raise ValueError("future-pipeline evidence result identity is ambiguous")
            evidence_attempt_ids.append(matches[0])
        # Canonical evidence is admissible for replanning only after the exact
        # bound native child has emitted terminal SubagentStop. Generic waits
        # remain required model control flow but cannot replace this boundary.
        require_wave_native_completion_observed(root, fresh_state, evidence_attempt_ids)
        evidence_digests = sorted(digest_text(ref) for ref in evidence_refs)
        request_digest = digest_text(json.dumps({
            "current_step": current_step,
            "evidence_result_refs": sorted(evidence_refs),
            "waves": payload.get("waves"),
            "reason": str(payload.get("reason") or ""),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        durable_winner = db_find_plan_revision_by_impact(
            root,
            task_id,
            classification="future_pipeline_revision",
            selectors={"step": current_step, "evidence_digests": evidence_digests},
        )
        if durable_winner is not None:
            winner_impact = durable_winner["impact"]
            if winner_impact.get("request_digest") == request_digest:
                return {
                    "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                    "ok": True,
                    "outcome": "future_pipeline_revised",
                    "task_ref": task_ref,
                    "current_step": current_step,
                    "pipeline_digest": winner_impact.get("pipeline_digest"),
                    "future_wave_count": winner_impact.get("future_wave_count"),
                    "governance_required": winner_impact.get("governance_required") is True,
                    "idempotent": True,
                    "state_mutated": False,
                }
            return {
                "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": False,
                "outcome": "future_pipeline_revision_conflict",
                "code": "future_pipeline_revision_conflict",
                "message": "A different future-pipeline decision already won for this completed evidence frontier.",
                "task_ref": task_ref,
                "retryable": False,
                "state_mutated": False,
            }
        prior_revisions = [
            item for item in plan.get("future_pipeline_revisions") or []
            if isinstance(item, dict)
            and item.get("step") == current_step
            and item.get("evidence_digests") == evidence_digests
        ]
        if prior_revisions:
            winner = prior_revisions[-1]
            if winner.get("request_digest") == request_digest:
                return {
                    "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                    "ok": True,
                    "outcome": "future_pipeline_revised",
                    "task_ref": task_ref,
                    "current_step": current_step,
                    "pipeline_digest": winner.get("pipeline_digest"),
                    "future_wave_count": winner.get("future_wave_count"),
                    "governance_required": winner.get("governance_required") is True,
                    "idempotent": True,
                    "state_mutated": False,
                }
            return {
                "schema": PUBLIC_ORCHESTRATION_SCHEMA,
                "ok": False,
                "outcome": "future_pipeline_revision_conflict",
                "code": "future_pipeline_revision_conflict",
                "message": "A different future-pipeline decision already won for this completed evidence frontier.",
                "task_ref": task_ref,
                "retryable": False,
                "state_mutated": False,
            }
        base_pipeline_digest = str(
            plan.get("semantic_future_pipeline_digest")
            or _semantic_future_pipeline_digest(plan)
        )
        revision_key = digest_text(json.dumps({
            "current_step": current_step,
            "base_pipeline_digest": base_pipeline_digest,
            "evidence_digests": evidence_digests,
        }, sort_keys=True, separators=(",", ":")))
        completed_gates = set(fresh_state.get("completed_gates") or [])
        immutable_phases: list[str] = []
        for wave in immutable:
            gates = list(wave.get("gates") or []) if isinstance(wave, dict) else []
            if len(gates) != 1:
                raise ValueError("each immutable pipeline wave must own exactly one canonical phase")
            phase = canonical_pipeline_gate(str(gates[0] or ""))
            if phase not in AVAILABLE_GATES:
                raise ValueError("immutable pipeline wave contains an unsupported phase")
            immutable_phases.append(phase)
        compiled = _v11_compact_waves(
            payload.get("waves"), fresh_task,
            completed_gates=completed_gates | set(active_gates(fresh_state)),
            prior_wave_phases=immutable_phases,
        )
        compiled = _append_governance_waves(compiled, fresh_task)
        first_new_identity = next_compiled_wave_index(waves)[0]
        first_compiled_spec = next((
            spec
            for wave in compiled if isinstance(wave, Mapping)
            for spec in wave.get("delegations") or [] if isinstance(spec, dict)
        ), None)
        if not isinstance(first_compiled_spec, dict):
            raise ValueError("future-pipeline revision produced no compiled assignments")
        first_compiled_spec["wave_index"] = first_new_identity
        normalized, _classification = _normalize_orchestrate_waves(
            compiled, fresh_task, plan.get("host_capabilities") or {}, str(fresh_task["project_root"]),
            prior_wave_refs=[
                str(wave.get("wave_ref") or "") for wave in immutable
            ],
        )
        for wave in normalized:
            wave["status"] = "pending"
            wave.pop("attempt_ids", None)
        chosen_pipeline = [
            str(gate)
            for wave in [*immutable, *normalized]
            for gate in (wave.get("gates") or [])
        ]
        semantic_pipeline = list(dict.fromkeys(chosen_pipeline))
        change = apply_pipeline_operations(
            fresh_state, pipeline=semantic_pipeline, operations=[], allow_rework=False,
            parallel_groups=[[gate] for gate in semantic_pipeline],
        )
        append_pipeline_change(
            fresh_state, change, str(payload.get("reason") or "future pipeline revised"),
            ["canonical_result:" + digest_text(ref) for ref in evidence_refs],
        )
        plan["waves"] = [*immutable, *normalized]
        plan["pipeline_contract_version"] = fresh_state.get("pipeline_contract_version")
        plan["chosen_pipeline"] = chosen_pipeline
        plan["chosen_parallel_groups"] = [list(wave.get("gates") or []) for wave in plan["waves"]]
        plan["semantic_future_pipeline_digest"] = _semantic_future_pipeline_digest(plan)
        governance_required = any(
            gate in {"governance_activation", "governance_close"}
            for gate in chosen_pipeline
        )
        retired_unexecuted_routes: list[dict[str, Any]] = []
        retired_unexecuted_route_count = 0
        for retired_wave in retired_future:
            for delegation in retired_wave.get("delegations") or []:
                if not isinstance(delegation, dict):
                    continue
                retired_unexecuted_route_count += 1
                if len(retired_unexecuted_routes) >= 64:
                    continue
                retired_unexecuted_routes.append({
                    "wave_id": str(retired_wave.get("wave_id") or ""),
                    "gates": [str(gate) for gate in retired_wave.get("gates") or []],
                    "delegation_key": str(delegation.get("delegation_key") or ""),
                    "profile": str(delegation.get("profile") or delegation.get("agent") or ""),
                    "model": str(delegation.get("model") or delegation.get("selected_model") or ""),
                    "reasoning_effort": str(
                        delegation.get("reasoning_effort")
                        or delegation.get("selected_reasoning_effort")
                        or ""
                    ),
                    "execution_status": "retired_unexecuted",
                })
        revision_record = {
            "step": current_step,
            "revision_key": revision_key,
            "base_pipeline_digest": base_pipeline_digest,
            "request_digest": request_digest,
            "evidence_digests": evidence_digests,
            "pipeline_digest": plan["semantic_future_pipeline_digest"],
            "future_wave_count": len(normalized),
            "governance_required": governance_required,
            "retired_unexecuted_routes": retired_unexecuted_routes,
            "retired_unexecuted_route_count": retired_unexecuted_route_count,
            "retired_unexecuted_routes_truncated": retired_unexecuted_route_count > len(retired_unexecuted_routes),
            "reason": redact(payload.get("reason") or "", 2_000),
            "at": now(),
        }
        plan.setdefault("future_pipeline_revisions", []).append(revision_record)
        plan["future_pipeline_revisions"] = plan["future_pipeline_revisions"][-32:]
        receipt_impact = {
            "classification": "future_pipeline_revision",
            **revision_record,
        }
        with db_transaction(root):
            plan["updated_at"] = now()
            receipt = db_append_plan_revision(
                root,
                task_id,
                task_revision=int(fresh_state.get("task_revision") or 1),
                impact=receipt_impact,
                plan=plan,
                status="active",
            )
            plan["plan_revision"] = receipt["plan_revision"]
            fresh_state["plan_revision"] = receipt["plan_revision"]
            fresh_state["plan_digest"] = receipt["plan_digest"]
            from cortex_runtime.orchestration_engine import _sync_orchestration_wave_occurrences
            _sync_orchestration_wave_occurrences(fresh_state, plan)
            _write_orchestrate_plan(fresh_dir, plan, preserve_updated_at=True)
            save_state(
                fresh_dir, fresh_dir / "state.sqlite", fresh_state,
                "future_pipeline_revision", "replaced only unexecuted future waves",
            )
        return {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "outcome": "future_pipeline_revised",
            "task_ref": task_ref,
            "current_step": current_step,
            "pipeline_digest": plan["semantic_future_pipeline_digest"],
            "future_wave_count": len(normalized),
            "governance_required": governance_required,
            "idempotent": False,
            "state_mutated": True,
            "state": fresh_state,
        }


def _roll_forward_active_product_rework_routes(
    route_roles: Sequence[tuple[dict[str, Any], list[str]]],
    *,
    source_result_ref: str,
    corrective_assignment: Mapping[str, Any],
    verifier_assignment: Mapping[str, Any],
    close_assignment: Mapping[str, Any] | None,
) -> int:
    """Rebind an exact active semantic-rework suffix to fresh occurrences.

    Existing compiled occurrences are immutable.  When one active role is
    itself product-defective, its replacement and every still-pending
    downstream role receive fresh compiler identities.  Prior identities and
    capabilities remain append-only audit history; a never-dispatched future
    role is recorded as such instead of inventing an assignment capability.
    """
    rebound_count = 0

    def rebind_role(
        route: dict[str, Any], role: str, assignment: Mapping[str, Any],
    ) -> None:
        prior_assignment_ref = str(route.get(f"{role}_assignment_ref") or "")
        prior = {
            "wave_ref": str(route.get(f"{role}_wave_ref") or ""),
            "phase_ref": str(route.get(f"{role}_phase_ref") or ""),
            "logical_delegation_key": str(
                route.get(f"{role}_logical_delegation_key") or ""
            ),
            "plan_assignment_lineage_digest": str(
                route.get(f"{role}_plan_assignment_lineage_digest") or ""
            ),
            "assignment_ref": prior_assignment_ref,
            "attempt_result_ref": str(route.get(f"{role}_result_ref") or ""),
            "attempt_result_digest": str(route.get(f"{role}_result_digest") or ""),
            "never_dispatched": not bool(prior_assignment_ref),
            "superseded_by_source_result_ref": source_result_ref,
            "at": now(),
        }
        if not all(prior.get(field) for field in (
            "wave_ref", "phase_ref", "logical_delegation_key",
            "plan_assignment_lineage_digest",
        )):
            raise ValueError("active rework route has an incomplete exact role binding")
        replacement = {
            "wave_ref": str(assignment.get("wave_ref") or ""),
            "phase_ref": str(assignment.get("phase_ref") or ""),
            "logical_delegation_key": str(
                assignment.get("logical_delegation_key") or ""
            ),
            "plan_assignment_lineage_digest": str(
                assignment.get("plan_assignment_lineage_digest") or ""
            ),
        }
        if not all(replacement.values()):
            raise ValueError("fresh rework occurrence has incomplete compiler identity")
        history_field = f"{role}_binding_history"
        history = route.get(history_field) or []
        if not isinstance(history, list) or any(
            not isinstance(item, Mapping) for item in history
        ):
            raise ValueError("active rework route role history is invalid")
        route[history_field] = [*map(dict, history), prior]
        for field, value in replacement.items():
            route[f"{role}_{field}"] = value
        route.pop(f"{role}_assignment_ref", None)
        route.pop(f"{role}_result_ref", None)
        route.pop(f"{role}_result_digest", None)
        route.pop(f"{role}_evidence", None)
        route.pop(
            "corrective_resolution_receipt"
            if role == "corrective" else "verification_resolution_receipt"
            if role == "verifier" else "close_resolution_receipt",
            None,
        )

    for route, matched_roles in route_roles:
        if str(route.get("status") or "") not in {
            "rework_required", "active", "awaiting_close",
        }:
            raise ValueError("completed or inactive product rework route cannot roll forward")
        if len(matched_roles) != 1 or matched_roles[0] not in {
            "corrective", "verifier", "close",
        }:
            raise ValueError("product rework source has an ambiguous active route role")
        matched_role = matched_roles[0]
        rebind_role(route, "corrective", corrective_assignment)
        rebind_role(route, "verifier", verifier_assignment)
        has_close = bool(str(route.get("close_wave_ref") or ""))
        if has_close:
            if close_assignment is None:
                raise ValueError(
                    f"{matched_role} roll-forward lacks a fresh close occurrence"
                )
            rebind_role(route, "close", close_assignment)
        elif matched_role == "close":
            raise ValueError("close roll-forward route lacks exact close authority")
        if str(route.get("schema") or "") == "cortex/product-rework-route/v1":
            route["status"] = "active"
            route["updated_at"] = now()
        rebound_count += 1
    return rebound_count


def _v11_append_rework_wave(
    params: dict[str, Any], task_dir: Path, state: dict[str, Any],
    task: dict[str, Any], task_ref: str, payload: dict[str, Any],
) -> dict[str, Any]:
    """Append one evidence-bound corrective route from canonical state."""
    del task_dir, task
    root = ledger_root(params)
    task_id = safe_id(str(state.get("task_id") or ""))
    with state_lock(root, operation="append_rework_wave", task_id=task_id):
        loaded = db_load_task(root, task_id)
        if loaded is None:
            raise ValueError("active task became unavailable during rework append")
        fresh_task, fresh_state, _plan, artifact_dir = loaded
        fresh_dir = root / artifact_dir
        return _v11_append_rework_wave_locked(
            params, fresh_dir, fresh_state, fresh_task, task_ref, payload,
        )


def _v11_append_rework_wave_locked(
    params: dict[str, Any], task_dir: Path, state: dict[str, Any],
    task: dict[str, Any], task_ref: str, payload: dict[str, Any],
    *, internal_response: bool = False,
) -> dict[str, Any]:
    """Compile, persist, and reconcile a rework append while state_lock is held."""
    from cortex_runtime.orchestration_engine import (
        _effective_plan_frontier,
        _closure_rework_occurrence_matches,
        _normalize_orchestrate_waves,
        _orchestrate_response,
        _prepare_orchestrate_wave_transaction,
        _require_wave_occurrence_consumption,
        _semantic_future_pipeline_digest,
        _sync_orchestration_wave_occurrences,
        _write_orchestrate_plan,
    )
    from cortex_runtime.assignment_compiler import (
        compiled_wave_execution_order,
        next_compiled_wave_index,
        resolve_profile_for_operation,
        resolve_reliability_fallback_profile,
    )
    from cortex_runtime.assignment_evaluator import requires_product_rework
    root = ledger_root(params)
    task_id = str(state["task_id"])
    source_ref = str(payload.get("source_result_ref") or "").strip()
    objective = str(payload.get("objective") or "").strip()
    acceptance = str(payload.get("acceptance") or "").strip()
    profile = canonical_profile(payload.get("profile") or "")
    model = _v11_model(payload.get("model"))
    effort = str(payload.get("reasoning_effort") or "").strip().lower()
    if not source_ref or not objective or not acceptance or not profile or not model or not effort:
        raise ValueError("append_rework_wave requires a complete semantic assignment")
    request_digest = digest_text(canonical_json.dumps({
        "source_result_ref": source_ref, "objective": objective, "acceptance": acceptance,
        "profile": profile, "model": model, "reasoning_effort": effort,
    }))

    def lifecycle_response(
        current_state: dict[str, Any], current_plan: dict[str, Any],
        receipt: dict[str, Any], *, spawn_requests: list[dict[str, Any]] | None = None,
        wave_id: str | None = None, idempotent: bool = False,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Project the canonical frontier without allocating a replay attempt."""
        requests = list(spawn_requests or [])
        if wave_id is None:
            frontier, _assignments = _effective_plan_frontier(current_plan, current_state)
            wave_id = str((frontier or {}).get("wave_ref") or "") or None
        runtime = _orchestrate_response(
            "append_rework_wave", current_state, wave_id=wave_id,
            spawn_requests=requests, plan=current_plan, result=result,
        )
        if runtime.get("state") == "ready_to_spawn" and not requests:
            # Native dispatch delivery is one-shot and belongs to the
            # operation that prepared it.  An append replay may report the
            # canonical recovery frontier but must never allocate or reissue
            # that dispatch, nor mislabel a childless state as waiting.
            runtime.update({
                "state": "recovery_pending",
                "next_action": (
                    "inspect the current orchestration lifecycle; this idempotent rework receipt "
                    "does not reissue a native dispatch"
                ),
                "spawn_requests": [],
            })
        if internal_response:
            runtime["rework_receipt"] = dict(receipt)
            runtime["state_record"] = current_state
            runtime["plan_record"] = current_plan
            runtime["spawn_requests"] = requests
            runtime["idempotent"] = idempotent
            return runtime
        runtime["rework_receipt"] = canonical_json.dumps(receipt)
        runtime["idempotent"] = idempotent
        return _v11_response(runtime, task_ref)

    plan = _load_orchestrate_plan(task_dir, state)
    pending_queue = [
        item for item in state.get("pending_product_reworks") or []
        if isinstance(item, dict) and str(item.get("source_result_ref") or "")
    ]
    durable_winner = db_find_plan_revision_by_impact(
        root,
        task_id,
        classification="append_rework_wave",
        selectors={"source_result_ref": source_ref},
    )
    existing = [
        item for item in plan.get("rework_appends") or []
        if isinstance(item, dict) and item.get("source_result_ref") == source_ref
    ]
    if durable_winner is not None:
        winner_impact = durable_winner["impact"]
        if winner_impact.get("request_digest") != request_digest:
            raise ValueError("a different rework decision already exists for source_result_ref")
        receipt = {
            key: winner_impact[key]
            for key in (
                "source_result_ref", "source_result_digest", "request_digest", "wave_ref",
                "verification_wave_ref", "close_wave_ref", "at",
            )
            if key in winner_impact
        }
        if (
            receipt.get("source_result_ref") != source_ref
            or not receipt.get("wave_ref")
            or not receipt.get("verification_wave_ref")
        ):
            raise ValueError("durable rework append receipt is incomplete")
        return lifecycle_response(
            state, plan, receipt, idempotent=True,
            result=(
                {"product_rework": pending_queue[0]}
                if pending_queue else None
            ),
        )
    if existing:
        raise ValueError("rework append plan state has no durable revision receipt")
    pending_rework = next((
        item for item in pending_queue
        if str(item.get("source_result_ref") or "") == source_ref
    ), None)
    if pending_queue and pending_rework is None:
        raise ValueError("append_rework_wave must select a queued canonical source result")
    matches = [
        item for item in state.get("attempts") or []
        if isinstance(item, dict) and not item.get("invalidated")
        and str(item.get("attempt_result_ref") or "") == source_ref
    ]
    if len(matches) != 1:
        raise ValueError("source_result_ref must identify one current canonical task result")
    source = matches[0]
    source_operation_kind = str(source.get("operation_kind") or "")
    if source_operation_kind not in {"inspect", "modify", "verify", "close"}:
        raise ValueError(
            "append_rework_wave source must be a compiled inspect, modify, verify, or close assignment"
        )
    if isinstance(pending_rework, dict):
        if str(pending_rework.get("source_result_ref") or "") != source_ref:
            raise ValueError("append_rework_wave does not match the pending canonical rework finding")
        evaluation = pending_rework.get("acceptance_evaluation")
    else:
        from cortex_runtime.assignment_evaluator import evaluate_assignment
        evaluation = evaluate_assignment(root, state, source)
    accepted_rework_status = (
        isinstance(evaluation, dict)
        and requires_product_rework(evaluation)
    )
    if not accepted_rework_status:
        raise ValueError("append_rework_wave requires a canonical product-rework evaluation")
    finding_fingerprints = sorted({
        str(item).strip()
        for item in evaluation.get("blocking_finding_fingerprints") or []
        if str(item).strip()
    })
    canonical = attempt_protocol.get_attempt_result(
        root, task_id=task_id, attempt_id=str(source.get("attempt_id") or ""),
    )
    if (
        not isinstance(canonical, dict)
        or str(canonical.get("result_ref") or "") != source_ref
        or canonical.get("lifecycle_status") not in attempt_protocol.TERMINAL_LIFECYCLES
    ):
        raise ValueError("source_result_ref is not a terminal canonical result")
    source_result_digest = "sha256:" + digest_text(canonical_json.dumps(canonical))
    require_wave_native_completion_observed(root, state, [str(source.get("attempt_id") or "")])
    waves = [item for item in plan.get("waves") or [] if isinstance(item, dict)]
    compiled_wave_execution_order(waves)
    source_wave_ref = str(source.get("wave_ref") or "")
    source_index = next((
        index for index, wave in enumerate(waves)
        if str(wave.get("wave_ref") or "") == source_wave_ref
    ), -1)
    if source_index < 0:
        raise ValueError("source_result_ref is not bound to the effective pipeline")

    # Discover every still-active route that binds this exact defective
    # occurrence before compiling its successor.  A corrective or verifier
    # can belong to a closure route whose final close occurrence is already
    # compiled.  That old suffix cannot be reused: its predecessor authority
    # remains bound to the superseded occurrence.  The replacement compiler
    # therefore emits the complete remaining suffix and preserves the old
    # occurrence identities only in history.
    active_roll_forward_routes = [
        route for route in (state.get("closure_rework") or {}).values()
        if isinstance(route, dict) and route.get("status") == "rework_required"
    ] + [
        route for route in (state.get("product_rework_routes") or {}).values()
        if isinstance(route, dict) and route.get("status") in {"active", "awaiting_close"}
    ]
    route_roll_forward_roles = [
        (
            route,
            [
                role for role in ("corrective", "verifier", "close")
                if _closure_rework_occurrence_matches(route, source, role)
            ],
        )
        for route in active_roll_forward_routes
    ]
    route_roll_forward_roles = [
        (route, roles) for route, roles in route_roll_forward_roles if roles
    ]
    if any(len(roles) != 1 for _route, roles in route_roll_forward_roles):
        raise ValueError("product rework source matches multiple roles in one active route")

    retained_close_identities = {
        (
            str(route.get("close_wave_ref") or ""),
            str(route.get("close_phase_ref") or ""),
            str(route.get("close_logical_delegation_key") or ""),
            str(route.get("close_plan_assignment_lineage_digest") or ""),
        )
        for route, _roles in route_roll_forward_roles
        if any(str(route.get(f"close_{field}") or "") for field in (
            "wave_ref", "phase_ref", "logical_delegation_key",
            "plan_assignment_lineage_digest",
        ))
    }
    if any(not all(identity) for identity in retained_close_identities):
        raise ValueError("active product rework route has incomplete close identity")
    if len(retained_close_identities) > 1:
        raise ValueError("product rework source has ambiguous active close successors")

    close_source_spec: Mapping[str, Any] | None = (
        source if source_operation_kind == "close" else None
    )
    if close_source_spec is None and retained_close_identities:
        close_identity_tuple = next(iter(retained_close_identities))
        close_matches = [
            assignment
            for wave in waves
            for assignment in wave.get("delegations") or []
            if isinstance(assignment, Mapping)
            and (
                str(assignment.get("wave_ref") or ""),
                str(assignment.get("phase_ref") or ""),
                str(assignment.get("logical_delegation_key") or ""),
                str(assignment.get("plan_assignment_lineage_digest") or ""),
            ) == close_identity_tuple
        ]
        if len(close_matches) != 1:
            raise ValueError("active product rework close occurrence is unavailable")
        close_source_spec = close_matches[0]
    requested_profile_record = PROFILES.get(profile) or {}
    if "modify" in (requested_profile_record.get("operation_kinds") or []):
        mutator_profile, _mutator_resolution = resolve_profile_for_operation(
            profile, "modify", "implementation", PROFILES,
        )
    elif str(requested_profile_record.get("capability_family") or "").strip():
        mutator_profile, _mutator_resolution = resolve_profile_for_operation(
            profile, "modify", "implementation", PROFILES,
        )
    else:
        mutator_profile = resolve_reliability_fallback_profile("modify", PROFILES)
        _mutator_resolution = "registry_owned_modify_fallback"
    verifier_spec: dict[str, Any]
    # A precompiled future verifier is an immutable occurrence.  Reusing it
    # would either leave its predecessor authority bound only to the defective
    # source or mutate an existing dependency identity.  Compile a fresh
    # verifier after the corrective occurrence and retain every pre-existing
    # future verifier byte-for-byte in its original later position.
    if (
        source_operation_kind in {"inspect", "verify", "close"}
        and str(source.get("profile") or source.get("agent") or "") != mutator_profile
        and "verify" in (
            PROFILES.get(str(source.get("profile") or source.get("agent") or ""), {})
            .get("operation_kinds") or []
        )
    ):
        verifier_spec = source
    else:
        fallback_verifier = resolve_reliability_fallback_profile("verify", PROFILES)
        fallback_record = PROFILES.get(fallback_verifier) or {}
        if fallback_verifier == mutator_profile or fallback_record.get("sandbox") != "read-only":
            raise ValueError("independent rework verifier route is unavailable")
        fallback_gates = [str(item) for item in fallback_record.get("gates") or [] if str(item)]
        source_phase = str(source.get("phase_kind") or source.get("gate") or "")
        verifier_phase = (
            source_phase if source_phase in fallback_gates else
            "qa" if "qa" in fallback_gates else
            fallback_gates[0] if fallback_gates else source_phase
        )
        verifier_spec = {
            "phase_kind": verifier_phase,
            "profile": fallback_verifier,
            "model": source.get("model") or source.get("selected_model"),
            "reasoning_effort": (
                source.get("reasoning_effort") or source.get("selected_reasoning_effort")
            ),
        }
    verifier_phase_kind = str(verifier_spec.get("phase_kind") or "").strip()
    verifier_profile = canonical_profile(verifier_spec.get("profile") or "")
    verifier_model = _v11_model(verifier_spec.get("model"))
    verifier_effort = str(verifier_spec.get("reasoning_effort") or "").strip().lower()
    if not verifier_phase_kind or not verifier_profile or not verifier_model or not verifier_effort:
        raise ValueError("append_rework_wave source verifier route is incomplete")
    completed_before_append = set(state.get("completed_orchestration_wave_ids") or [])
    source_occurrence_receipt = _require_wave_occurrence_consumption(
        state,
        waves[source_index],
        source_result_ref=source_ref,
    )
    queued_sibling_append = bool(pending_rework and source_wave_ref in completed_before_append)
    if any(
        str(wave.get("status") or "") == "completed"
        or str(wave.get("wave_ref") or "") in completed_before_append
        for wave in waves[source_index + 1:]
    ):
        raise ValueError("append_rework_wave cannot rewrite a completed successor occurrence")
    successor_wave_refs = {
        str(wave.get("wave_ref") or "")
        for wave in waves[source_index + 1:]
    }
    if any(
        isinstance(attempt, dict)
        and not attempt.get("invalidated")
        and str(attempt.get("wave_ref") or "")
        in successor_wave_refs
        for attempt in state.get("attempts") or []
    ):
        raise ValueError("append_rework_wave cannot rewrite a dispatched successor occurrence")
    insertion = source_index + 1
    if queued_sibling_append:
        source_wave_by_result_ref = {
            str(item.get("attempt_result_ref") or ""): str(item.get("wave_ref") or "")
            for item in state.get("attempts") or []
            if isinstance(item, dict) and str(item.get("attempt_result_ref") or "")
        }
        sibling_end_refs = [
            str(item.get("close_wave_ref") or item.get("verification_wave_ref") or "")
            for item in plan.get("rework_appends") or []
            if isinstance(item, dict)
            and source_wave_by_result_ref.get(str(item.get("source_result_ref") or ""))
            == source_wave_ref
        ]
        sibling_end_indices = [
            index for index, wave in enumerate(waves)
            if str(wave.get("wave_ref") or "") in set(sibling_end_refs)
        ]
        if not sibling_end_indices:
            raise ValueError("queued sibling rework has no compiled predecessor route")
        insertion = max(sibling_end_indices) + 1
    new_occurrence_count = 2 + (1 if close_source_spec is not None else 0)
    allocated_indices = next_compiled_wave_index(
        waves, count=new_occurrence_count,
    )
    modify_identity = allocated_indices[0]
    next_allocated = 1
    verify_identity = allocated_indices[next_allocated]
    next_allocated += 1
    close_identity = (
        allocated_indices[next_allocated]
        if close_source_spec is not None else None
    )
    execution_predecessor_refs = [
        str(wave.get("wave_ref") or "") for wave in waves[:insertion]
    ]
    internal = [
        {"wave_id": f"wave-{modify_identity:02d}", "delegations": [{
            "gate": "implementation", "agent": mutator_profile, "objective": objective,
            "acceptance_criteria": [acceptance],
            "verification": ["Verify the exact evidence-bound repair against its acceptance contract."],
            "operation_kind": "modify", "model": model, "reasoning_effort": effort,
            "phase_kind": "implementation",
            "phase_ref": f"phase-{modify_identity:04d}",
            "wave_ref": f"wave-{modify_identity:02d}", "wave_index": modify_identity,
        }]},
    ]
    internal.append({"wave_id": f"wave-{verify_identity:02d}", "delegations": [{
        "gate": verifier_phase_kind, "agent": verifier_profile,
        "objective": "Independently verify the evidence-bound repair: " + objective,
        "acceptance_criteria": [acceptance],
        "verification": ["Read the repair result and prove the cited finding is closed without regression."],
        "operation_kind": "verify", "model": verifier_model,
        "reasoning_effort": verifier_effort,
        "phase_kind": verifier_phase_kind, "phase_ref": f"phase-{verify_identity:04d}",
        "wave_ref": f"wave-{verify_identity:02d}", "wave_index": verify_identity,
    }]})
    if close_identity is not None:
        source_close_profile = canonical_profile(
            close_source_spec.get("profile") or close_source_spec.get("agent") or ""
        )
        source_close_model = _v11_model(
            close_source_spec.get("model") or close_source_spec.get("selected_model")
        )
        source_close_effort = str(
            close_source_spec.get("reasoning_effort")
            or close_source_spec.get("selected_reasoning_effort")
            or ""
        ).strip().lower()
        if not source_close_profile or not source_close_model or not source_close_effort:
            raise ValueError("close rework source route is incomplete")
        internal.append({
            "wave_id": f"wave-{close_identity:02d}",
            "delegations": [{
                "gate": verifier_phase_kind,
                "agent": source_close_profile,
                "objective": (
                    "Perform a fresh closure after the exact corrective and independent "
                    "verification occurrences have completed."
                ),
                "acceptance_criteria": [acceptance],
                "verification": [
                    "Re-evaluate closure from canonical predecessor results and leave no open finding."
                ],
                "operation_kind": "close",
                "model": source_close_model,
                "reasoning_effort": source_close_effort,
                "phase_kind": verifier_phase_kind,
                "phase_ref": f"phase-{close_identity:04d}",
                "wave_ref": f"wave-{close_identity:02d}",
                "wave_index": close_identity,
            }],
        })
    inserted, _ = _normalize_orchestrate_waves(
        internal, task, plan.get("host_capabilities") or {}, str(task["project_root"]),
        prior_wave_refs=execution_predecessor_refs,
    )
    retained_future = list(waves[insertion:])
    effective = [*waves[:insertion], *inserted, *retained_future]
    compiled_wave_execution_order(effective)
    verification_wave_ref = str(inserted[1].get("wave_ref") or "")
    corrective_assignment = next((
        item for item in inserted[0].get("delegations") or [] if isinstance(item, dict)
    ), None)
    verifier_assignment = next((
        item for item in inserted[1].get("delegations") or []
        if isinstance(item, dict)
    ), None)
    if not isinstance(corrective_assignment, dict) or not isinstance(verifier_assignment, dict):
        raise ValueError("compiled rework occurrence assignments are unavailable")
    receipt = {
        "source_result_ref": source_ref,
        "source_result_digest": source_result_digest,
        "request_digest": request_digest,
        "wave_ref": str(inserted[0].get("wave_ref") or ""),
        "verification_wave_ref": verification_wave_ref,
        "source_wave_ref": source_wave_ref,
        "source_phase_ref": str(source.get("phase_ref") or ""),
        "source_logical_delegation_key": str(
            source.get("logical_delegation_key") or ""
        ),
        "source_plan_assignment_lineage_digest": str(
            source.get("plan_assignment_lineage_digest") or ""
        ),
        "corrective_phase_ref": str(corrective_assignment.get("phase_ref") or ""),
        "corrective_logical_delegation_key": str(
            corrective_assignment.get("logical_delegation_key") or ""
        ),
        "corrective_plan_assignment_lineage_digest": str(
            corrective_assignment.get("plan_assignment_lineage_digest") or ""
        ),
        "verifier_phase_ref": str(verifier_assignment.get("phase_ref") or ""),
        "verifier_logical_delegation_key": str(
            verifier_assignment.get("logical_delegation_key") or ""
        ),
        "verifier_plan_assignment_lineage_digest": str(
            verifier_assignment.get("plan_assignment_lineage_digest") or ""
        ),
        "at": now(),
    }
    close_assignment: dict[str, Any] | None = None
    if close_identity is not None:
        receipt["close_wave_ref"] = str(inserted[-1].get("wave_ref") or "")
        close_assignment = next((
            item for item in inserted[-1].get("delegations") or []
            if isinstance(item, dict)
        ), None)
        if not isinstance(close_assignment, dict):
            raise ValueError("compiled rework close occurrence is unavailable")
        receipt.update({
            "close_phase_ref": str(close_assignment.get("phase_ref") or ""),
            "close_logical_delegation_key": str(
                close_assignment.get("logical_delegation_key") or ""
            ),
            "close_plan_assignment_lineage_digest": str(
                close_assignment.get("plan_assignment_lineage_digest") or ""
            ),
        })

    # A semantic defect in a corrective/verifier/close successor creates a
    # new compiled route.  Rebind only active routes that name this exact
    # source occurrence; semantic gate equality is never sufficient.  The
    # immutable prior binding remains in role history for handoff/audit.
    _roll_forward_active_product_rework_routes(
        route_roll_forward_roles,
        source_result_ref=source_ref,
        corrective_assignment=corrective_assignment,
        verifier_assignment=verifier_assignment,
        close_assignment=close_assignment,
    )

    product_routes = state.get("product_rework_routes")
    if product_routes is None:
        product_routes = {}
    if not isinstance(product_routes, dict):
        raise ValueError("product rework routes are invalid")
    product_route_key = "product-route-" + digest_text(source_ref)[:32]
    if product_route_key in product_routes:
        raise ValueError("product rework source already has a non-idempotent route")
    product_routes[product_route_key] = {
        "schema": "cortex/product-rework-route/v1",
        "status": "active",
        "source_result_ref": source_ref,
        "source_result_digest": source_result_digest,
        "source_attempt_id": str(source.get("attempt_id") or ""),
        "source_wave_ref": source_wave_ref,
        "source_phase_ref": str(source.get("phase_ref") or ""),
        "source_logical_delegation_key": str(
            source.get("logical_delegation_key") or ""
        ),
        "source_plan_assignment_lineage_digest": str(
            source.get("plan_assignment_lineage_digest") or ""
        ),
        "corrective_wave_ref": str(corrective_assignment.get("wave_ref") or ""),
        "corrective_phase_ref": str(corrective_assignment.get("phase_ref") or ""),
        "corrective_logical_delegation_key": str(
            corrective_assignment.get("logical_delegation_key") or ""
        ),
        "corrective_plan_assignment_lineage_digest": str(
            corrective_assignment.get("plan_assignment_lineage_digest") or ""
        ),
        "verifier_wave_ref": str(verifier_assignment.get("wave_ref") or ""),
        "verifier_phase_ref": str(verifier_assignment.get("phase_ref") or ""),
        "verifier_logical_delegation_key": str(
            verifier_assignment.get("logical_delegation_key") or ""
        ),
        "verifier_plan_assignment_lineage_digest": str(
            verifier_assignment.get("plan_assignment_lineage_digest") or ""
        ),
        **(
            {
                "close_wave_ref": str(close_assignment.get("wave_ref") or ""),
                "close_phase_ref": str(close_assignment.get("phase_ref") or ""),
                "close_logical_delegation_key": str(
                    close_assignment.get("logical_delegation_key") or ""
                ),
                "close_plan_assignment_lineage_digest": str(
                    close_assignment.get("plan_assignment_lineage_digest") or ""
                ),
            }
            if isinstance(close_assignment, dict) else {}
        ),
        "finding_fingerprints": finding_fingerprints,
        "task_revision": int(state.get("task_revision") or 1),
        "request_digest": request_digest,
        "created_at": now(),
        "updated_at": now(),
    }
    state["product_rework_routes"] = product_routes
    plan["waves"] = effective
    plan["chosen_pipeline"] = [str(gate) for wave in effective for gate in wave.get("gates") or []]
    plan["chosen_parallel_groups"] = [list(wave.get("gates") or []) for wave in effective]
    plan.setdefault("rework_appends", []).append(receipt)
    plan["rework_appends"] = plan["rework_appends"][-64:]
    plan["semantic_future_pipeline_digest"] = _semantic_future_pipeline_digest(plan)
    completed_occurrences = list(state.get("completed_orchestration_wave_ids") or [])
    if source_wave_ref not in completed_occurrences:
        completed_occurrences.append(source_wave_ref)
    state["completed_orchestration_wave_ids"] = completed_occurrences
    completion_receipts = state.get("completed_orchestration_wave_receipts")
    if completion_receipts is None:
        completion_receipts = {}
    if not isinstance(completion_receipts, dict):
        raise ValueError("completed orchestration wave receipts are invalid")
    prior_completion_receipt = completion_receipts.get(source_wave_ref)
    if (
        prior_completion_receipt is not None
        and prior_completion_receipt != source_occurrence_receipt
    ):
        raise ValueError("completed source wave occurrence receipt changed")
    completion_receipts[source_wave_ref] = source_occurrence_receipt
    state["completed_orchestration_wave_receipts"] = completion_receipts
    waves[source_index]["status"] = "completed"
    source["invalidated"] = True
    source["invalidated_at"] = now()
    source["invalidation_reason"] = "superseded_by_product_rework"
    if pending_rework is not None:
        remaining_reworks = [
            item for item in pending_queue
            if str(item.get("source_result_ref") or "") != source_ref
        ]
        if remaining_reworks:
            state["pending_product_reworks"] = remaining_reworks
        else:
            state.pop("pending_product_reworks", None)
    else:
        remaining_reworks = []
    if finding_fingerprints:
        origin_gate = str(source.get("gate") or source.get("phase_kind") or "").strip()
        if not origin_gate:
            raise ValueError("canonical finding rework source gate is unavailable")
        routes = state.setdefault("closure_rework", {})
        route_key = "rework-" + digest_text(source_ref)[:32]
        prior_route = routes.get(route_key)
        prior_fingerprints = (
            list(prior_route.get("finding_fingerprints") or [])
            if isinstance(prior_route, dict) else []
        )
        prior_result_refs = (
            list(prior_route.get("source_result_refs") or [])
            if isinstance(prior_route, dict) else []
        )
        prior_finding_origins = (
            dict(prior_route.get("finding_origin_result_refs") or {})
            if isinstance(prior_route, dict) else {}
        )
        for fingerprint in prior_fingerprints:
            prior_finding_origins.setdefault(fingerprint, list(prior_result_refs))
        for fingerprint in finding_fingerprints:
            prior_finding_origins[fingerprint] = list(dict.fromkeys([
                *(
                    prior_finding_origins.get(fingerprint)
                    if isinstance(prior_finding_origins.get(fingerprint), list)
                    else []
                ),
                source_ref,
            ]))
        routes[route_key] = {
            "status": "rework_required",
            "origin_gate": origin_gate,
            "target_gate": "implementation",
            "verifier_gate": verifier_phase_kind,
            "finding_fingerprints": sorted(set(prior_fingerprints + finding_fingerprints)),
            "source_result_refs": list(dict.fromkeys(prior_result_refs + [source_ref])),
            "finding_origin_result_refs": prior_finding_origins,
            "corrective_wave_ref": str(corrective_assignment.get("wave_ref") or ""),
            "corrective_phase_ref": str(corrective_assignment.get("phase_ref") or ""),
            "corrective_logical_delegation_key": str(
                corrective_assignment.get("logical_delegation_key") or ""
            ),
            "corrective_plan_assignment_lineage_digest": str(
                corrective_assignment.get("plan_assignment_lineage_digest") or ""
            ),
            "verifier_wave_ref": str(verifier_assignment.get("wave_ref") or ""),
            "verifier_phase_ref": str(verifier_assignment.get("phase_ref") or ""),
            "verifier_logical_delegation_key": str(
                verifier_assignment.get("logical_delegation_key") or ""
            ),
            "verifier_plan_assignment_lineage_digest": str(
                verifier_assignment.get("plan_assignment_lineage_digest") or ""
            ),
            **(
                {
                    "close_wave_ref": str(close_assignment.get("wave_ref") or ""),
                    "close_phase_ref": str(close_assignment.get("phase_ref") or ""),
                    "close_logical_delegation_key": str(
                        close_assignment.get("logical_delegation_key") or ""
                    ),
                    "close_plan_assignment_lineage_digest": str(
                        close_assignment.get("plan_assignment_lineage_digest") or ""
                    ),
                }
                if isinstance(close_assignment, dict) else {}
            ),
            "task_revision": int(state.get("task_revision") or 1),
            "iteration": int((prior_route or {}).get("iteration") or 0) + 1,
            "at": now(),
        }
    state["status"] = (
        "rework_preflight_required" if remaining_reworks else "active"
    )
    inserted_phase_kinds = {
        str(gate)
        for inserted_wave in inserted
        for gate in inserted_wave.get("gates") or []
        if str(gate).strip()
    }
    state["completed_gates"] = [
        str(gate) for gate in state.get("completed_gates") or []
        if str(gate) not in inserted_phase_kinds
    ]
    state["skipped_gates"] = [
        str(gate) for gate in state.get("skipped_gates") or []
        if str(gate) not in inserted_phase_kinds
    ]
    _sync_orchestration_wave_occurrences(state, plan)
    with db_transaction(root):
        plan["updated_at"] = now()
        revision = db_append_plan_revision(
            root, task_id, task_revision=int(state.get("task_revision") or 1),
            impact={"classification": "append_rework_wave", **receipt}, plan=plan, status="active",
        )
        plan["plan_revision"] = revision["plan_revision"]
        state["plan_revision"] = revision["plan_revision"]
        state["plan_digest"] = revision["plan_digest"]
        _write_orchestrate_plan(task_dir, plan, preserve_updated_at=True)
        save_state(
            task_dir, task_dir / "state.sqlite", state, "append_rework_wave",
            "appended evidence-bound modify and verification instances",
        )
        if remaining_reworks:
            prepared = {
                "state": state,
                "wave_id": None,
                "spawn_requests": [],
                "attempt_ids": [],
            }
        else:
            prepared = _prepare_orchestrate_wave_transaction(
                {
                    **params,
                    "task_id": task_id,
                    "principal": state.get("principal"),
                },
                task_dir,
                state,
                plan,
            )
    return lifecycle_response(
        prepared.get("state") or state, plan, receipt,
        spawn_requests=prepared.get("spawn_requests") or [],
        wave_id=prepared.get("wave_id"),
        result=(
            {"product_rework": remaining_reworks[0]}
            if remaining_reworks else None
        ),
    )


def _manage_orchestration_input_diagnostics(params: Any) -> list[dict[str, Any]]:
    """Validate one management request from its canonical public contract."""
    schema = backend_schema_for(
        PUBLIC_CONTRACTS, "manage_orchestration",
        params if isinstance(params, Mapping) else None,
    )
    diagnostics = _v11_schema_value_diagnostics(
        params, schema, pointer="", operation="manage_orchestration",
    )
    if not isinstance(params, Mapping):
        return diagnostics

    def add_pair(worker: Mapping[str, Any], pointer: str) -> None:
        model = worker.get("model")
        effort = worker.get("reasoning_effort")
        if (
            not isinstance(model, str)
            or model not in MODEL_EFFORTS
            or not isinstance(effort, str)
            or effort not in SUPPORTED_EFFORTS
            or model_effort_pair_is_allowed(MODEL_EFFORTS, model, effort)
        ):
            return
        effort_pointer = f"{pointer}/reasoning_effort"
        diagnostics.append({
            "code": "manage_orchestration_validation_failed",
            "phase": "preflight",
            "path": effort_pointer.lstrip("/").replace("/", "."),
            "json_pointer": effort_pointer,
            "message": f"reasoning_effort is not supported for {model}",
            "received": effort,
            "expected": {"type": "string", "enum": list(MODEL_EFFORTS[model])},
            "field_schema": {"type": "string", "enum": list(MODEL_EFFORTS[model])},
            "fix": "Replace only this reasoning_effort with one allowed for the selected model.",
        })

    action = str(params.get("action") or "")
    if action in {"append_rework_wave", "auxiliary_start"}:
        add_pair(params, "")
    elif action in {"future_pipeline_revise", "follow_up"}:
        waves = params.get("waves")
        if isinstance(waves, list):
            for wave_index, wave in enumerate(waves):
                workers = wave.get("workers") if isinstance(wave, Mapping) else None
                if not isinstance(workers, list):
                    continue
                for worker_index, worker in enumerate(workers):
                    if isinstance(worker, Mapping):
                        add_pair(worker, f"/waves/{wave_index}/workers/{worker_index}")
    return diagnostics


def _flat_manage_orchestration_request(params: Mapping[str, Any]) -> dict[str, Any]:
    """Translate the hard-cut public form to the existing private engine ports."""
    action = str(params["action"])
    base = {"task_ref": params["task_ref"], "coordinator_ref": params["coordinator_ref"]}
    if action in {"inspect", "recover_inspect", "recover_blocked", "resume", "deactivate"}:
        return {**base, "intent": action, **({"reason": params["text"]} if "text" in params else {})}
    if action == "read_lifecycle_page":
        return {**base, "intent": "inspect"}
    if action in {"question_show", "question_answer"}:
        payload = {"question_ref": params["question_ref"]}
        if action == "question_answer":
            payload["answer"] = params["answer"]
        return {**base, "intent": "question", "payload": payload}
    if action == "plan_prompt":
        return {**base, "intent": "plan_approval", "payload": {"decision": "prompt"}}
    if action == "plan":
        return {**base, "intent": "plan_approval", "payload": {
            "decision": params["decision"], "request_id": params["request_id"],
        }}
    if action == "plan_revise":
        return {**base, "intent": "plan_approval", "payload": {
            "decision": "revise", "request_id": params["request_id"], "feedback": params["text"],
        }}
    if action == "future_pipeline_revise":
        return {**base, "intent": "future_pipeline_revise", "payload": {
            "evidence_result_refs": list(params["evidence_result_refs"]),
            "waves": list(params["waves"]),
            "reason": params["reason"],
        }}
    if action == "append_rework_wave":
        return {**base, "intent": "append_rework_wave", "payload": {
            key: params[key]
            for key in (
                "source_result_ref", "objective", "acceptance", "profile",
                "model", "reasoning_effort",
            )
        }}
    if action == "follow_up":
        request = str(params["user_request"])
        return {**base, "intent": "follow_up", "payload": {
            "user_request": request,
            "waves": list(params["waves"]),
            "acceptance_criteria": ["Complete the exact follow-up user_request."],
            "verification": ["Verify the result directly against the exact follow-up user_request."],
        }}
    if action == "steer":
        return {**base, "intent": "steer", "payload": {"user_message": params["text"]}}
    if action == "auxiliary_start":
        return {**base, "intent": "auxiliary_start", "payload": {"worker": {
            key: params[key]
            for key in ("objective", "operation_kind", "profile", "model", "reasoning_effort")
        }}}
    if action.startswith("artifact_"):
        command = action.removeprefix("artifact_")
        payload = {"action": command}
        for field in ("kind", "artifact_ref", "cursor"):
            if field in params:
                payload[field] = params[field]
        return {**base, "intent": "artifacts", "payload": payload}
    if action.startswith("lane_"):
        commands = {"lane_bind_task": "bind_task"}
        payload: dict[str, Any] = {"command": commands.get(action, action.removeprefix("lane_"))}
        for field in ("lane_id", "mode", "run_id", "expires_at", "reclaim", "clean", "confirm", "cursor"):
            if field in params:
                payload[field] = params[field]
        if "text" in params:
            payload["purpose"] = params["text"]
        declaration = {
            field: params[field]
            for field in ("repo_path", "worktree_path", "branch", "sync_from")
            if field in params
        }
        if declaration:
            payload["declarations"] = [declaration]
        return {**base, "intent": "lane", "payload": payload}
    if action in {"resource_claim", "resource_release", "resource_lock", "resource_unlock"}:
        command = {
            "resource_claim": "claim", "resource_release": "release",
            "resource_lock": "acquire_lock", "resource_unlock": "release_lock",
        }[action]
        payload = {"command": command, "owner": "coordinator"}
        for field in ("path", "kind"):
            if field in params:
                payload[field] = params[field]
        return {**base, "intent": "resource", "payload": payload}
    if action in {"finalize_bootstrap_failure", "finalize_worker_failure"}:
        reason_code = (
            "bootstrap_missing_identity" if action == "finalize_bootstrap_failure"
            else "worker_nonretryable_terminal"
        )
        return {**base, "intent": action, "payload": {
            "dispatch_ref": params["dispatch_ref"], "reason_code": reason_code,
        }}
    raise ValueError("unsupported management action")


def _manage_orchestration_impl(params: dict[str, Any]) -> dict[str, Any]:
    """Keep recovery and rare control-plane capabilities outside the normal flow."""
    resolved_task_ref = str(params.get("task_ref") or "").strip() or None if isinstance(params, dict) else None
    caller_supplied_project_root = isinstance(params, dict) and "project_root" in params
    try:
        public_params = dict(params) if isinstance(params, dict) else {}
        envelope = _manage_orchestration_input_diagnostics(params)
        if envelope:
            response = _v11_envelope_error("manage_orchestration", envelope)
            response["outcome"] = "needs_correction"
            return response
        params = _flat_manage_orchestration_request(params)
        intent = str(params["intent"])
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
        public_action = str(public_params.get("action") or "")
        public_root = ledger_root({"project_root": params["project_root"]})
        if intent == "future_pipeline_revise":
            payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
            return _v11_revise_future_pipeline(
                params, task_dir, state, task_definition, task_ref, payload,
            )
        if intent == "append_rework_wave":
            payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
            return _v11_append_rework_wave(
                params, task_dir, state, task_definition, task_ref, payload,
            )
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
        if intent == "auxiliary_start":
            payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
            return _v11_pending_question_auxiliary_steer(
                params, task_dir, state, task_definition, task_ref, payload,
            )
        if intent == "follow_up":
            follow_up = _v11_follow_up_payload(params.get("payload"))
            source_context = _v11_follow_up_context(
                task_dir,
                state,
                task_definition,
                task_ref,
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
                "user_request": str(follow_up_task.get("user_request") or ""),
                "complexity": _v11_complexity(task_definition.get("complexity")),
                "waves": follow_up_task["waves"],
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
        if intent not in {"inspect", "resume"}:
            epoch_fence = _v11_host_epoch_fence_response(task_dir, state, task_ref)
            if epoch_fence is not None:
                return epoch_fence
        elif intent == "resume":
            compact_boundary = _v11_pending_context_boundary(task_dir, state)
            if compact_boundary is not None:
                return _v11_response({
                    "ok": True,
                    "state": "context_inspection_required",
                    "task_id": str(state.get("task_id") or ""),
                    "content": (
                        "Call inspect_orchestration now and consume every page until complete=true. "
                        "A same-incarnation context boundary does not authorize host-epoch resume."
                    ),
                }, task_ref)
        if intent == "inspect":
            compact_boundary = _v11_pending_context_boundary(task_dir, state)
            inspection = _v11_compact_inspection_projection(
                task_dir, state, _engine_inspect_lifecycle(common),
                boundary=compact_boundary,
            )
            page = _page_management_report(
                public_root, public_params, inspection,
                cursor_action="read_lifecycle_page",
            )
            epoch_unavailable = False
            try:
                resumed_epoch = _v11_resumed_host_epoch_recovery_required(task_dir, state)
            except ValueError:
                resumed_epoch = False
                epoch_unavailable = True
            if epoch_unavailable and page.get("complete") is True:
                page = {
                    "ok": False,
                    "outcome": "failed",
                    "action": {"kind": "none"},
                    "task_ref": task_ref,
                    "code": "native_host_epoch_recovery_unavailable",
                    "retryable": False,
                    "state_mutated": False,
                    "complete": True,
                    "content": (
                        "This active task has no authenticated recoverable host epoch. Stop task-scoped "
                        "calls; do not wait, spawn, continue, or infer ownership."
                    ),
                }
            elif resumed_epoch and page.get("complete") is True:
                page["action"] = {"kind": "resume_orchestration"}
                page["content"] = (
                    "Call resume_orchestration exactly once now with the same exact task_ref and "
                    "coordinator_ref. Cortex authenticated a new exclusive Codex host epoch and will "
                    "replace every unfinished prior-epoch assignment with new native children. Do not "
                    "call wait_agent, read_worker_wave, continue_orchestration, or create a child first."
                )
            elif page.get("complete") is True:
                orphaned_followup = _v11_unemitted_same_child_followup_attempt(
                    public_root, task_dir, state,
                )
                if orphaned_followup is not None:
                    page["action"] = {"kind": "continue"}
                    page["content"] = (
                        "Call continue_orchestration exactly once now with the same exact task_ref and "
                        "coordinator_ref. Do not call wait_agent, read_worker_wave, or create a new child first."
                    )
                elif str(inspection.get("_context_action_kind") or "") == "invoke_dispatches":
                    delivery = _v11_response(dict(inspection), task_ref)
                    page["action"] = {"kind": "invoke_dispatches"}
                    page["dispatches"] = list(delivery.get("dispatches") or [])
                    page["step"] = int(delivery.get("step") or 1)
            if compact_boundary is not None:
                if page.get("complete") is not True:
                    page.pop("action", None)
                else:
                    ledger_db.acknowledge_context_boundary(
                        public_root,
                        _required_host_thread_id(),
                        str(state.get("task_id") or ""),
                        compact_boundary,
                    )
            return page
        if intent == "recover_inspect":
            # Lifecycle recovery is deliberately distinct from ordinary
            # inspection: status reads must not contend on the mutation lock
            # or silently expire/retire attempts.  The server derives the
            # exact repair scope from current durable state; callers cannot
            # select attempts, receipts, or identities to mutate.
            recovery = _engine_inspect_lifecycle({
                **common, "payload": {"mode": "recover_lifecycle"},
            })
            refreshed = _v11_task_state(public_root, str(state.get("task_id") or ""))
            if refreshed is None:
                raise ValueError("recovery inspection task disappeared")
            refreshed_dir, refreshed_state, _refreshed_task = refreshed
            projection = _v11_compact_inspection_projection(
                refreshed_dir, refreshed_state, recovery,
            )
            page = _page_management_report(public_root, public_params, projection)
            if page.get("complete") is not True:
                page.pop("action", None)
                return page
            context_action = str(projection.get("_context_action_kind") or "")
            if context_action == "invoke_dispatches":
                delivery = _v11_response(dict(projection), task_ref)
                page["action"] = {"kind": "invoke_dispatches"}
                page["dispatches"] = list(delivery.get("dispatches") or [])
                page["step"] = int(delivery.get("step") or 1)
            elif context_action == "inspect_orchestration_recovery":
                page["action"] = {"kind": "inspect_orchestration_recovery"}
                page["content"] = (
                    "The exact delivered native dispatch has no trusted child-start observation yet. "
                    "Call inspect_orchestration_recovery again after the bounded observer lease; do not "
                    "invoke the dispatch again, wait on a fabricated child id, or call continue_orchestration."
                )
            elif context_action and context_action != "none":
                page["action"] = {"kind": context_action}
            return page
        normalized_payload = None
        if intent == "question":
            normalized_payload = _v11_question_management_payload(params.get("payload"))
        elif intent == "plan_approval":
            normalized_payload = _v11_plan_approval_payload(params.get("payload"))
            if normalized_payload["decision"] == "prompt":
                normalized_payload, prompt_response = _v11_prompt_plan_approval(
                    state,
                    task_ref,
                )
                if prompt_response is not None:
                    page = _page_management_report(
                        public_root,
                        public_params,
                        str(prompt_response.get("content") or ""),
                    )
                    page["request_id"] = str(
                        _plan_approval(state).get("request_id") or ""
                    )
                    return page
        submission_id = safe_id("orchestration-manage-" + intent + "-" + digest_text(state["task_id"] + ":" + str(state.get("revision")) + ":" + json.dumps({**params, "payload": normalized_payload if normalized_payload is not None else params.get("payload")}, sort_keys=True, default=str))[:16])
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
            resume_instruction = None
            if public_action == "question_answer":
                state, resume_instruction = _v11_issue_question_resume_instruction(
                    public_root, task_dir, state, old,
                )
                result = old.get("result") if isinstance(old.get("result"), Mapping) else {}
                if str(result.get("status") or "") == "answered":
                    epoch_replacement = _engine_recover_answered_epoch_question(
                        {**common, "submission_id": submission_id},
                        task_dir,
                        state,
                        str(result.get("question_ref") or ""),
                    )
                    if epoch_replacement is not None:
                        return _v11_response(epoch_replacement, task_ref, include_result=True)
            question_response = _v11_question_response(
                old,
                state,
                task_ref,
                root=public_root,
                resume_instruction=resume_instruction,
            )
            if public_action == "question_show":
                return _page_question_text(public_root, public_params, question_response)
            return question_response
        response = _v11_response(old, task_ref, include_result=True)
        if intent == "plan_approval" and (old.get("result") or {}).get("decision") == "approved":
            response["approval_message"] = "Plan approved."
            response["next_action"] = (
                f"{COORDINATOR_LOCK} Tell the user in their language that the plan was approved, then execute every "
                "returned dispatch exactly once and continue the normal Cortex wave workflow."
            )
        if public_action in {"lane_inspect", "lane_reconcile"}:
            return _page_management_report(public_root, public_params, response)
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
    """Validate one governance request from its canonical public contract."""
    schema = backend_schema_for(
        PUBLIC_CONTRACTS, "manage_governance",
        params if isinstance(params, Mapping) else None,
    )
    return _v11_schema_value_diagnostics(
        params, schema, pointer="", operation="manage_governance",
    )


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


PUBLIC_READ_PAGE_ITEMS = 32
PUBLIC_READ_PAGE_CHARS = 16_384


def _public_cursor_scope(tool: str, action: str, params: Mapping[str, Any]) -> str:
    visible = {
        key: value
        for key, value in params.items()
        if key not in {"cursor", "coordinator_ref", "assignment_ref"}
    }
    return scope_digest({"tool": tool, "action": action, "scope": visible})


def _encode_public_cursor(
    root: Path,
    *,
    tool: str,
    action: str,
    params: Mapping[str, Any],
    offset: int,
    mode: str,
) -> str:
    return encode_cursor(
        _governance_lifecycle_hmac_key(root, create=False),
        selector=f"{tool}.{action}.{mode}", audience="coordinator",
        digest=_public_cursor_scope(tool, action, params), offset=offset,
    )


def _decode_public_cursor(
    root: Path,
    cursor: object,
    *,
    tool: str,
    action: str,
    params: Mapping[str, Any],
    mode: str,
) -> int:
    if cursor in {None, ""}:
        return 0
    try:
        return decode_cursor(
            cursor, _governance_lifecycle_hmac_key(root, create=False),
            selector=f"{tool}.{action}.{mode}", audience="coordinator",
            digest=_public_cursor_scope(tool, action, params),
        )
    except ValueError as exc:
        raise ValueError("cursor is invalid or stale; restart the same read without cursor") from exc


def _nested_public_scalar(value: object, key: str) -> object:
    if isinstance(value, Mapping):
        candidate = value.get(key)
        if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
            return candidate
        for child in value.values():
            found = _nested_public_scalar(child, key)
            if found not in {None, ""}:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _nested_public_scalar(child, key)
            if found not in {None, ""}:
                return found
    return None


_MANAGEMENT_PAGE_PUBLIC_FIELDS = frozenset({
    "action", "advice", "answered", "choices", "complete", "completed_gates",
    "content", "current_gates", "current_pipeline", "gate", "kind", "lane",
    "lanes", "label", "message", "ok", "outcome", "phase",
    "pipeline", "profile", "question_ref", "question_text",
    "recommendation", "report", "request_id", "result", "results", "run",
    "runs", "description", "state", "status", "step", "summary", "title", "total", "waves",
    "workers", "frontier_ref", "catalog_ref",
})
_MANAGEMENT_PAGE_PRIVATE_KEY_PARTS = (
    "assignment", "capability", "coordinator", "database", "directory",
    "dispatch", "event_key", "host", "ledger", "path", "principal",
    "project_root", "session", "task_id", "task_ref", "attempt_id",
)


def _management_page_projection(value: object) -> object:
    """Build a minimal public semantic value before JSON pagination."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_management_page_projection(item) for item in value]
    if not isinstance(value, Mapping):
        return str(value)
    projected: dict[str, object] = {}
    for raw_key, item in value.items():
        key = str(raw_key)
        lowered = key.lower()
        if (
            key not in _MANAGEMENT_PAGE_PUBLIC_FIELDS
            or any(part in lowered for part in _MANAGEMENT_PAGE_PRIVATE_KEY_PARTS)
        ):
            continue
        projected[key] = _management_page_projection(item)
    return projected


def _v11_compact_inspection_projection(
    task_dir: Path,
    state: Mapping[str, Any],
    value: object,
    *,
    boundary: Mapping[str, Any] | None = None,
) -> object:
    """Attach content-addressed compact frontier/catalog identities."""
    del task_dir
    if not isinstance(value, Mapping):
        return value
    public_view = _management_page_projection(value)
    frontier_json = json.dumps({
        "frontier": public_view,
        "context_boundary_ref": str((boundary or {}).get("boundary_ref") or ""),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    catalog_basis = sorted({
        str(item.get("attempt_result_ref") or "")
        for item in state.get("attempts") or []
        if isinstance(item, Mapping) and str(item.get("attempt_result_ref") or "")
    })
    lifecycle_state = str(value.get("state") or "")
    context_action = {
        "waiting_workers": "wait_for_bound_workers",
        "waiting": "wait_for_bound_workers",
        "completion_pending": "read_worker_wave",
        "recovery_pending": "resume_orchestration",
        "awaiting_plan_approval": "obtain_plan_approval",
        "needs_input": "obtain_user_decision",
        "completed": "terminal_continue",
        "terminal_blocked": "terminal_continue",
    }.get(lifecycle_state)
    if lifecycle_state == "ready_to_spawn":
        spawn_attempt_ids = {
            str(item.get("attempt_id") or "")
            for item in value.get("spawn_requests") or []
            if isinstance(item, Mapping) and str(item.get("attempt_id") or "")
        }
        current_attempts = [
            item for item in state.get("attempts") or []
            if isinstance(item, Mapping)
            and not item.get("invalidated")
            and str(item.get("attempt_id") or "") in spawn_attempt_ids
        ]
        deliveries = {str(item.get("dispatch_delivery_status") or "") for item in current_attempts}
        if current_attempts and deliveries == {"pending"}:
            context_action = "invoke_dispatches"
        elif current_attempts and deliveries.issubset({"delivered"}):
            context_action = "inspect_orchestration_recovery"
        else:
            context_action = "resume_orchestration"
    return {
        **dict(value),
        "frontier_ref": "frontier-v1-" + digest_text(frontier_json),
        "catalog_ref": "catalog-v1-" + digest_text(json.dumps(catalog_basis, separators=(",", ":"))),
        "_context_action_kind": context_action or "none",
    }


def _page_management_report(
    root: Path,
    public_params: Mapping[str, Any],
    value: object,
    *,
    cursor_action: str | None = None,
) -> dict[str, Any]:
    action = str(cursor_action or public_params["action"])
    cursor_params = dict(public_params)
    if cursor_action:
        cursor_params = {"task_ref": public_params.get("task_ref"), "action": cursor_action}
        if public_params.get("cursor") is not None:
            cursor_params["cursor"] = public_params["cursor"]
    full = (
        value
        if isinstance(value, str)
        else json.dumps(
            _management_page_projection(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    # Management reports are regenerated from live durable state. Bind every
    # continuation to the exact rendered snapshot so an old byte offset can
    # never be applied after an auxiliary dispatch/result changes the report.
    cursor_params["snapshot_digest"] = digest_text(full)
    try:
        offset = _decode_public_cursor(
            root, public_params.get("cursor"), tool="manage_orchestration",
            action=action, params=cursor_params, mode="text",
        )
    except ValueError as exc:
        if public_params.get("cursor") is None:
            raise
        raise ValidationFailure([{
            "code": "stale_management_cursor",
            "phase": "management_read",
            "path": "cursor",
            "json_pointer": "/cursor",
            "message": redact(str(exc), 1000),
            "expected": "a cursor issued for the current immutable report snapshot",
            "field_schema": {"type": "string"},
            "fix": "Remove cursor and retry the same public read operation to restart from the current snapshot.",
        }]) from exc
    report, next_offset, complete = page_utf8_text(
        full, offset, maximum_bytes=PUBLIC_READ_PAGE_CHARS,
    )
    response: dict[str, Any] = {
        "ok": True, "outcome": "management_read", "report": report,
        "complete": complete,
    }
    for field in ("question_ref", "request_id"):
        scalar = _nested_public_scalar(value, field)
        if scalar not in {None, ""}:
            response[field] = str(scalar)
    if complete and action in {"inspect", "read_lifecycle_page"} and isinstance(value, Mapping):
        lifecycle_action = str(value.get("_context_action_kind") or "")
        if lifecycle_action:
            response["action"] = {"kind": lifecycle_action}
    if response.get("question_ref") and complete and action in {"inspect", "read_lifecycle_page"}:
        response["action"] = {"kind": "obtain_user_decision"}
    if not complete:
        response["next_cursor"] = _encode_public_cursor(
            root, tool="manage_orchestration", action=action,
            params=cursor_params, offset=next_offset, mode="text",
        )
    return response


def _page_question_text(
    root: Path,
    public_params: Mapping[str, Any],
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Page one displayed question as exact text, never as a JSON UI wrapper."""
    if value.get("outcome") != "awaiting_user":
        return _page_management_report(root, public_params, dict(value))
    question_ref = value.get("question_ref")
    question_text = value.get("question_text")
    if (
        not isinstance(question_ref, str)
        or re.fullmatch(r"question-[A-Za-z0-9._:-]{1,160}", question_ref) is None
        or not isinstance(question_text, str)
        or not question_text
    ):
        raise ValueError("question display record is invalid")
    offset = _decode_public_cursor(
        root, public_params.get("cursor"), tool="manage_orchestration",
        action="question_show", params=public_params, mode="text",
    )
    report, next_offset, complete = page_utf8_text(
        question_text, offset, maximum_bytes=PUBLIC_READ_PAGE_CHARS,
    )
    response: dict[str, Any] = {
        "ok": True,
        "outcome": "management_read",
        "question_ref": question_ref,
        "report": report,
    }
    if not complete:
        response["next_cursor"] = _encode_public_cursor(
            root, tool="manage_orchestration", action="question_show",
            params=public_params, offset=next_offset, mode="text",
        )
    return response


def _flat_governance_payload(
    params: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
    root: Path,
) -> tuple[dict[str, Any], int, str]:
    action = str(params["action"])
    mode = "text" if action == "inspect_initiative" else "rows"
    offset = _decode_public_cursor(
        root, params.get("cursor"), tool="manage_governance", action=action,
        params=params, mode=mode,
    )
    payload: dict[str, Any] = {"action": action}
    if action == "transition":
        payload["action"] = "transition_initiative"
    for field in (
        "initiative_ref", "relationship", "expected_revision", "source_type", "source_ref",
        "target_type", "target_ref", "dependency_type", "status", "record_type",
        "supersedes", "expires_at", "content_artifact_ref", "fingerprint", "record_ref",
    ):
        if field in params:
            payload[field] = params[field]
    if action == "link_task":
        payload["task_id"] = str(state["task_id"])
        relationship = str(params.get("relationship") or "deliverable")
        if relationship == "milestone" and "text" in params:
            payload["milestone"] = params["text"]
        elif relationship == "deliverable" and "text" in params:
            payload["deliverable"] = params["text"]
        elif relationship == "corrective":
            payload["corrective"] = True
    elif action == "transition" and "text" in params:
        payload["evidence"] = {"text": params["text"]}
    elif action == "create_record":
        payload["content"] = params["text"]
    if action in {"create_record", "list_records", "snapshot"}:
        scope = str(params["scope"])
        if scope in {"task", "initiative_task"}:
            payload["task_id"] = str(state["task_id"])
        if scope == "task":
            payload.pop("initiative_ref", None)
    if action in {"list_records", "snapshot", "promotion_inspect"}:
        payload["limit"] = PUBLIC_READ_PAGE_ITEMS + 1
        payload["offset"] = offset
    return payload, offset, mode


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
        public_params = dict(params)
        action = str(params["action"])
        project, task_dir, state, _task, task_ref = authorize_coordinator_ref(
            params, "manage_governance",
        )
        epoch_fence = _v11_host_epoch_fence_response(task_dir, state, task_ref)
        if epoch_fence is not None:
            return epoch_fence
        root = ledger_root({"project_root": str(project)})
        claims = _coordinator_capability_claims_for_task(
            root,
            str(state["task_id"]),
        )
        if claims is None:
            raise GovernanceError(
                "active coordinator session has no valid server-owned claims",
                code="coordinator_authorization_required",
            )
        payload, offset, page_mode = _flat_governance_payload(
            public_params, state=state, root=root,
        )
        payload["created_by"] = str(claims.get("principal") or "coordinator")
        _authorize_governance_capability_claim(claims, payload)
        result = manage_governance_service(
            root, payload, actor_role="coordinator"
        )
        public_result = project_public_governance_semantic(result)
        read_actions = {"inspect_initiative", "list_records", "snapshot", "promotion_inspect"}
        if action in read_actions:
            if page_mode == "text":
                full_text = json.dumps(
                    public_result, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                )
                report, next_offset, complete = page_utf8_text(
                    full_text, offset, maximum_bytes=PUBLIC_READ_PAGE_CHARS,
                )
                has_more = not complete
            else:
                projected = public_result if isinstance(public_result, Mapping) else {}
                if action == "list_records":
                    rows = list(projected.get("records") or [])
                    key = "records"
                elif action == "promotion_inspect":
                    rows = list(projected.get("proposals") or [])
                    key = "proposals"
                else:
                    snapshot = projected.get("snapshot") if isinstance(projected.get("snapshot"), dict) else {}
                    rows = list(snapshot.get("records") or [])
                    key = "records"
                has_more = len(rows) > PUBLIC_READ_PAGE_ITEMS
                rows = rows[:PUBLIC_READ_PAGE_ITEMS]
                if action == "snapshot":
                    page_value = {**snapshot, "records": rows}
                else:
                    page_value = {key: rows}
                report = json.dumps(
                    page_value, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                )
                next_offset = offset + len(rows)
            response = {
                "schema": "cortex/governance/v1", "ok": True,
                "outcome": "governance_read", "action": action, "report": report,
            }
            if has_more:
                response["next_cursor"] = _encode_public_cursor(
                    root, tool="manage_governance", action=action,
                    params=public_params, offset=next_offset, mode=page_mode,
                )
            return response
        return {
            "schema": "cortex/governance/v1",
            "ok": True,
            "outcome": "governance_updated",
            "action": action,
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
    NativeCompletionObservationRequired,
    NativeCompletionObservationUnavailable,
    complete_attempt as complete_worker_attempt,
    record_attempt_event as record_worker_attempt_event,
    record_worker_finding as record_worker_finding_receipt,
    read_worker_result,
    require_wave_native_completion_observed,
    _v11_pending_repair_response,
)

# Wrap the five assignment-bound public operations at the facade boundary.
# Their domain handlers stay focused on their own transaction; only this
# boundary may turn a verified terminal response into coordinator-consumable
# private control evidence.
_worker_question_operation = worker_question
_record_worker_attempt_event_operation = record_worker_attempt_event
_record_worker_finding_operation = record_worker_finding_receipt
_complete_worker_attempt_operation = complete_worker_attempt
_read_dispatch_briefing_operation = read_dispatch_briefing
_read_worker_result_operation = read_worker_result


def _worker_context_unavailable() -> dict[str, Any]:
    """Return the sole non-enumerating host-bound refresh failure."""
    return {
        "schema": PUBLIC_ORCHESTRATION_SCHEMA,
        "ok": False,
        "action": "none",
        "retryable": False,
        "state_mutated": False,
        "error_code": "worker_context_unavailable",
        "error": "The current native child has no refreshable Cortex assignment.",
    }


def _worker_context_stale_cursor() -> dict[str, Any]:
    return {
        "ok": False,
        "action": "retry_same_operation", "retryable": True,
        "state_mutated": False, "error_code": "stale_worker_context_cursor",
        "error": "The worker context snapshot changed. Retry refresh_worker_context once without cursor.",
        "allowed_changes": [{"path": "/cursor", "op": "remove", "expected": "omit cursor"}],
    }


def _worker_refresh_bound_question(
    root: Path,
    task_id: str,
    attempt: Mapping[str, Any],
    dispatch_ref: str,
) -> Mapping[str, Any] | None:
    """Resolve the sole durable question authorized for one refreshed worker.

    Ordinary workers may see only a question issued by their exact current
    attempt/dispatch/generation.  A server-owned host-epoch replacement may
    instead see the exact retired source question named by all three immutable
    recovery bindings.  Those authorities are mutually exclusive; historical
    task questions never participate in selection.
    """
    from cortex_runtime.questions import permitted_question_categories

    categories = permitted_question_categories()
    ordinary, ordinary_more = ledger_db.page_durable_questions(
        root, task_id,
        attempt_id=str(attempt.get("attempt_id") or ""),
        offset=0,
        limit=2,
        categories=categories,
        dispatch_ref=dispatch_ref,
        attempt_generation=int(attempt.get("worker_host_session_generation") or 1),
        statuses=("open", "answered"),
    )
    ordinary = [
        item for item in ordinary
        if str(item.get("status") or "") in {"open", "answered"}
    ]
    if ordinary_more or len(ordinary) > 1:
        raise WorkerAssignmentError()

    recovery_fields = {
        "question_ref": str(attempt.get("recovery_question_ref") or ""),
        "attempt_id": str(attempt.get("recovery_question_source_attempt_id") or ""),
        "dispatch_ref": str(attempt.get("recovery_question_source_dispatch_ref") or ""),
    }
    if any(recovery_fields.values()) and not all(recovery_fields.values()):
        raise WorkerAssignmentError()
    recovery: Mapping[str, Any] | None = None
    if all(recovery_fields.values()):
        candidate = ledger_db.get_durable_question(
            root, task_id, recovery_fields["question_ref"],
        )
        if (
            not isinstance(candidate, Mapping)
            or str(candidate.get("attempt_id") or "") != recovery_fields["attempt_id"]
            or str(candidate.get("dispatch_ref") or "") != recovery_fields["dispatch_ref"]
            or str(candidate.get("status") or "") not in {"open", "answered"}
            or str(candidate.get("question_category") or "") not in categories
        ):
            raise WorkerAssignmentError()
        recovery = candidate

    if ordinary and recovery is not None:
        raise WorkerAssignmentError()
    return recovery if recovery is not None else (ordinary[0] if ordinary else None)


def _authorize_host_bound_worker_refresh(
) -> tuple[Path, Path, dict[str, Any], dict[str, Any], str, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """Resolve one established assignment solely from trusted host identity."""
    host_thread_id = _required_host_thread_id()
    matches: list[tuple[Path, dict[str, Any]]] = []
    for project in bound_workspaces_for_private_lookup():
        try:
            root = ledger_root_path_internal(project, create=False)
            for session in ledger_db.worker_sessions_for_host_agent(
                root, host_thread_id, limit=2,
            ):
                matches.append((project, session))
                if len(matches) > 1:
                    raise WorkerAssignmentError()
        except FileNotFoundError:
            continue
    if len(matches) != 1:
        raise WorkerAssignmentError()
    project, session = matches[0]
    root = ledger_root_path_internal(project, create=False)
    task_id = str(session.get("task_id") or "")
    attempt_id = str(session.get("attempt_id") or "")
    loaded = _v11_task_state(root, task_id)
    if loaded is None:
        raise WorkerAssignmentError()
    task_dir, state, _task = loaded
    attempts = [
        item for item in state.get("attempts") or []
        if isinstance(item, dict)
        and not item.get("invalidated")
        and str(item.get("attempt_id") or "") == attempt_id
        and str(item.get("worker_host_thread_id") or "") == host_thread_id
    ]
    if len(attempts) != 1:
        raise WorkerAssignmentError()
    attempt = attempts[0]
    claim = attempt.get("worker_authority")
    dispatch_ref = str(attempt.get("dispatch_ref") or "")
    if (
        not isinstance(claim, Mapping)
        or claim.get("schema") != WORKER_DISPATCH_AUTHORITY_SCHEMA
        or str(claim.get("task_id") or "") != task_id
        or str(claim.get("attempt_id") or "") != attempt_id
        or str(claim.get("dispatch_ref") or "") != dispatch_ref
        or str(claim.get("dispatch_digest") or "")
        != hashlib.sha256(dispatch_ref.encode("ascii", errors="strict")).hexdigest()
    ):
        raise WorkerAssignmentError()
    # Refresh is read-only. The exact native child/session must already be
    # bound by the normal dispatch bootstrap or same-child transition.
    sessions = [
        item for item in ledger_db.worker_sessions_for_host_agent(
            root, host_thread_id, limit=2,
        )
        if str(item.get("task_id") or "") == task_id
        and str(item.get("attempt_id") or "") == attempt_id
        and int(item.get("generation") or 0)
        == int(attempt.get("worker_host_session_generation") or 1)
    ]
    if (
        len(sessions) != 1
        or str(sessions[0].get("status") or "") in {"terminated_unavailable", "completed"}
        or not bool(sessions[0].get("resumable"))
    ):
        raise WorkerAssignmentError()
    pending_repair = ledger_db.get_pending_repair_escrow(
        root, task_id=task_id, attempt_id=attempt_id,
    )
    if (
        isinstance(pending_repair, Mapping)
        and str(pending_repair.get("dispatch_ref_digest") or "")
        != v11_submission.canonical_digest(dispatch_ref)
    ):
        raise WorkerAssignmentError()
    pending_question = _worker_refresh_bound_question(
        root, task_id, attempt, dispatch_ref,
    )
    plan = _load_orchestrate_plan(task_dir, state)
    _wave, frontier_attempts = _effective_plan_frontier(plan, state)
    in_frontier = any(
        str(item.get("attempt_id") or "") == attempt_id
        for item in frontier_attempts if isinstance(item, Mapping)
    )
    if not in_frontier and pending_question is None and pending_repair is None:
        raise WorkerAssignmentError()
    canonical_result = attempt_protocol.get_attempt_result(
        root, task_id=task_id, attempt_id=attempt_id,
    )
    if canonical_result is not None:
        raise WorkerAssignmentError()
    stopped = (
        isinstance(attempt.get("native_terminal_stop"), Mapping)
        or isinstance(attempt.get("native_incomplete_stop_evidence"), Mapping)
    )
    if stopped and pending_question is None and pending_repair is None:
        raise WorkerAssignmentError()
    receipts = attempt_protocol.attempt_receipts(
        root, task_id=task_id, attempt_id=attempt_id,
    )
    if not isinstance(receipts.get("briefing_receipt"), Mapping):
        # Initial bootstrap remains exclusively dispatch_ref + briefing read.
        raise WorkerAssignmentError()
    profile = canonical_profile(str(attempt.get("profile") or attempt.get("agent") or ""))
    if profile not in AGENTS:
        raise WorkerAssignmentError()
    return project, task_dir, state, attempt, profile, pending_question, pending_repair


def read_worker_context(params: dict[str, Any]) -> dict[str, Any]:
    """Refresh one established worker under one exact task snapshot lock."""
    try:
        project, _task_dir, state, _attempt, _profile, _question, _escrow = (
            _authorize_host_bound_worker_refresh()
        )
        root = ledger_root_path_internal(project, create=False)
        with state_lock(
            root, operation="refresh_worker_context", task_id=str(state.get("task_id") or ""),
        ):
            return _read_worker_context_locked(params)
    except (WorkerAssignmentError, ValueError, TypeError, OSError, RuntimeError, sqlite3.Error):
        return _worker_context_unavailable()


def _read_worker_context_locked(params: dict[str, Any]) -> dict[str, Any]:
    """Assemble and revalidate one refresh while its task lock is held."""
    try:
        if set(params) - {"cursor"}:
            raise WorkerAssignmentError()
        project, task_dir, state, attempt, profile, question, escrow = (
            _authorize_host_bound_worker_refresh()
        )
        root = ledger_root_path_internal(project, create=False)
        task_id = str(state.get("task_id") or "")
        attempt_id = str(attempt.get("attempt_id") or "")
        receipts = attempt_protocol.attempt_receipts(
            root, task_id=task_id, attempt_id=attempt_id,
        )
        predecessor_receipts = receipts.get("predecessor_receipts")
        predecessor_receipts = predecessor_receipts if isinstance(predecessor_receipts, Mapping) else {}
        required_refs = [str(item) for item in attempt.get("predecessor_result_refs") or []]
        optional_refs = [str(item) for item in attempt.get("optional_report_result_refs") or []]
        pending_repair = None
        if isinstance(escrow, Mapping):
            repair_projection = _v11_pending_repair_response(root, escrow)
            pending_repair = repair_projection.get("repair")
            if not isinstance(pending_repair, Mapping):
                raise WorkerAssignmentError()
        frontier = {
            "lifecycle_status": str(attempt.get("lifecycle_status") or attempt.get("status") or ""),
            "objective": str(attempt.get("objective") or ""),
            "profile": profile,
            "operation_kind": str(attempt.get("operation_kind") or ""),
            "phase_ref": str(attempt.get("phase_ref") or ""),
            "wave_ref": str(attempt.get("wave_ref") or attempt.get("orchestration_wave_id") or ""),
            "briefing_read": isinstance(receipts.get("briefing_receipt"), Mapping),
            "required_report_count": len(required_refs),
            "required_report_read_count": sum(item in predecessor_receipts for item in required_refs),
            "optional_report_count": len(optional_refs),
            "question": (
                {
                    "question_ref": str(question.get("question_ref") or ""),
                    "status": str(question.get("status") or ""),
                }
                if isinstance(question, Mapping) else None
            ),
            "pending_repair": dict(pending_repair) if isinstance(pending_repair, Mapping) else None,
        }
        catalog_basis = {
            "required": required_refs,
            "optional": optional_refs,
            "required_reads": sorted(str(item) for item in predecessor_receipts),
        }
        frontier_text = json.dumps(frontier, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        frontier_ref = "frontier-v1-" + digest_text(frontier_text)
        catalog_ref = "catalog-v1-" + digest_text(
            json.dumps(catalog_basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        cursor_binding = scope_digest({
            "task_id": task_id,
            "attempt_id": attempt_id,
            "generation": int(attempt.get("worker_host_session_generation") or 1),
            "frontier_ref": frontier_ref,
            "catalog_ref": catalog_ref,
        })
        secret = _governance_lifecycle_hmac_key(root, create=False)
        offset = 0
        if "cursor" in params:
            cursor = params.get("cursor")
            if not isinstance(cursor, str) or not cursor:
                raise WorkerAssignmentError()
            try:
                offset = decode_cursor(
                    cursor, secret, selector="refresh_worker_context",
                    audience="worker:" + digest_text(task_id + "\0" + attempt_id),
                    digest=cursor_binding,
                )
            except ValueError:
                return _worker_context_stale_cursor()
        content, next_offset, complete = page_utf8_text(
            frontier_text, offset, maximum_bytes=8_192,
        )
        response = {
            "schema": PUBLIC_ORCHESTRATION_SCHEMA,
            "ok": True,
            "action": (
                "read_more" if not complete else
                "poll_worker_question" if isinstance(question, Mapping) and question.get("status") == "answered" else
                "none" if isinstance(question, Mapping) and question.get("status") == "open" else
                "use_result_as_context"
            ),
            "retryable": False,
            "state_mutated": False,
            "dispatch_ref": str(attempt.get("dispatch_ref") or ""),
            "frontier_ref": frontier_ref,
            "catalog_ref": catalog_ref,
            "content": content,
            "complete": complete,
        }
        if isinstance(question, Mapping):
            response["question_ref"] = str(question.get("question_ref") or "")
            response["question_status"] = str(question.get("status") or "")
        if not complete:
            response["next_cursor"] = encode_cursor(
                secret,
                selector="refresh_worker_context",
                audience="worker:" + digest_text(task_id + "\0" + attempt_id),
                digest=cursor_binding,
                offset=next_offset,
            )
        fresh = _authorize_host_bound_worker_refresh()
        fresh_question = fresh[5]
        fresh_escrow = fresh[6]
        if (
            str(fresh[3].get("attempt_id") or "") != attempt_id
            or str((fresh_question or {}).get("question_ref") or "")
            != str((question or {}).get("question_ref") or "")
            or str((fresh_question or {}).get("status") or "")
            != str((question or {}).get("status") or "")
            or str((fresh_escrow or {}).get("escrow_digest") or "")
            != str((escrow or {}).get("escrow_digest") or "")
        ):
            return _worker_context_stale_cursor() if "cursor" in params else _worker_context_unavailable()
        return response
    except (WorkerAssignmentError, ValueError, TypeError, OSError, RuntimeError, sqlite3.Error):
        return _worker_context_unavailable()


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


def record_worker_finding(params: dict[str, Any]) -> dict[str, Any]:
    return _run_worker_public_operation(
        "record_worker_finding", _record_worker_finding_operation, params,
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
QUESTION_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "task_id": {"type": "string", "minLength": 1},
        "principal": {"type": "string", "minLength": 1},
        "question_ref": {"type": "string", "minLength": 1},
    },
    "required": ["task_id", "principal", "question_ref"],
}
PUBLIC_CONTRACTS = build_public_contracts(
    agents=PROFILES,
    operation_kinds=PROFILE_CONTRACT.get("operation_kinds", {}),
    model_routing=MODEL_ROUTING,
    available_gates=AVAILABLE_GATES,
)
PUBLIC_SCHEMA_REGISTRY = public_input_schemas(PUBLIC_CONTRACTS)
START_ORCHESTRATION_SCHEMA = PUBLIC_SCHEMA_REGISTRY["start_orchestration"]
CONTINUE_ORCHESTRATION_SCHEMA = PUBLIC_SCHEMA_REGISTRY["continue_orchestration"]


TOOLS = {
    "start_orchestration": (start_orchestration, START_ORCHESTRATION_SCHEMA),
    "continue_orchestration": (continue_orchestration, CONTINUE_ORCHESTRATION_SCHEMA),
    "manage_orchestration": (manage_orchestration, {"type": "object"}),
    "activate_orchestration": (activate_orchestration, {"type": "object", "additionalProperties": False, "properties": {"user_command": {"type": "string", "const": "/cortex"}, "principal": {"type": "string", "minLength": 1}}, "required": ["user_command", "principal"]}),
    "deactivate_orchestration": (deactivate_orchestration, {"type": "object", "additionalProperties": False, "properties": {"user_command": {"type": "string", "const": "/normal"}, "principal": {"type": "string"}}, "required": ["user_command"]}),
    "classify_task": (classify_task, {"type": "object", "properties": {"complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "requirements": {"type": "array", "items": {"type": "string"}}, "pipeline": {"type": "array", "items": {"type": "string"}, "description": "Full gate proposal selected by the orchestrator; documentation and close recommendations are advisory, and the chosen pipeline remains authoritative."}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "Ordered executable waves selected by the orchestrator; gates in one wave may run concurrently."}, "principal": {"type": "string"}}, "required": ["complexity"]}),
    "init_task": (init_task, {"type": "object", "properties": {"task_id": {"type": "string"}, "user_request": {"type": "string", "description": "Exact user-authored task text."}, "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "classification_id": {"type": "string"}, "requirements": {"type": "array", "items": {"type": "string"}}, "acceptance_criteria": {"type": "array", "items": {"type": "string"}}, "scope": {"type": "array", "items": {"type": "string"}}, "verification": {"type": "array", "items": {"type": "string"}}, "budget": {"type": "string"}, "pause_conditions": {"type": "array", "items": {"type": "string"}}, "plan_approval": {"type": "string", "enum": ["auto", "required"]}, "pipeline": {"type": "array", "items": {"type": "string"}}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}, "principal": {"type": "string"}, "user_language": {"type": "string"}}, "required": ["task_id", "user_request", "classification_id"]}),
    "get_task_status": (status, {"type": "object", "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}}, "required": ["task_id", "principal"]}),
    "resolve_dispatch_route": (resolve_dispatch_route, {"type": "object", "additionalProperties": False, "properties": {"agent": {"type": "string", "enum": sorted(AGENTS)}, "task_kind": {"type": "string"}, "risk": {"type": "string", "enum": ["low", "moderate", "high", "critical"]}, "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "model": {"type": "string", "enum": list(MODEL_EFFORTS), "description": MODEL_PAIR_GUIDANCE}, "configured_default_model": {"type": "string", "enum": list(MODEL_EFFORTS), "description": "Host-configured agents.default_subagent_model used when native model is omitted."}, "reasoning_effort": {"type": "string", "enum": list(SUPPORTED_EFFORT_SEQUENCE), "description": MODEL_PAIR_GUIDANCE}}, "required": ["agent", "task_kind", "risk"]}),
    "finalize_attempt": (finalize_attempt, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "attempt_id": {"type": "string"}, "status": {"type": "string", "enum": sorted(TERMINAL_ATTEMPT_STATUSES)}, "reason": {"type": "string"}}, "required": ["task_id", "expected_revision", "attempt_id", "status"]}),
    "cortex.question": (cortex_question, QUESTION_TOOL_SCHEMA),
    "publish_worker_question": (publish_worker_question, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "attempt_id": {"type": "string"}, "submission_id": {"type": "string"}, "question_text": {"type": "string", "minLength": 1}}, "required": ["task_id", "principal", "attempt_id", "submission_id", "question_text"]}),
    "list_worker_questions": (list_worker_questions, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "attempt_id": {"type": "string"}, "status": {"type": "string", "enum": ["open", "answered", "superseded"]}, "cursor": {"type": "string"}}, "required": ["task_id", "principal"]}),
    "answer_worker_question": (answer_worker_question, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "question_ref": {"type": "string"}, "submission_id": {"type": "string"}, "answer": {"type": "string", "minLength": 1}}, "required": ["task_id", "principal", "question_ref", "submission_id", "answer"]}),
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
    contracts=PUBLIC_CONTRACTS,
    worker_question=worker_question,
    record_attempt_event=record_worker_attempt_event,
    record_worker_finding=record_worker_finding,
    complete_attempt=complete_worker_attempt,
    read_dispatch_briefing=read_dispatch_briefing,
    read_worker_result=read_worker_result,
    read_worker_context=read_worker_context,
    manage_governance=manage_governance,
)


def _audit_transport_metadata_arrival(
    _tool_name: str,
    arguments: Mapping[str, Any],
    host_thread_id: str | None,
    valid: bool,
) -> None:
    """Record only bounded private metadata facts after public schema validation."""
    root: Path | None = None
    task_id: str | None = None
    task_ref = str(arguments.get("task_ref") or "")
    if task_ref:
        bound = _bind_task_project_root({"task_ref": task_ref}, include_completed=True)
        if isinstance(bound, dict):
            root = ledger_root(bound)
            resolved = _v11_resolve_task(bound, include_completed=True, require_task_ref=True)
            if not isinstance(resolved, dict):
                task_id = str(resolved[1].get("task_id") or "") or None
    if root is None:
        return
    digests: dict[str, str] = {}
    if valid and host_thread_id:
        digests["mcp_thread"] = ledger_db.private_lifecycle_audit_digest(
            root, "mcp-thread", host_thread_id,
        )
    if task_id:
        digests["task"] = ledger_db.private_lifecycle_audit_digest(root, "task", task_id)
    ledger_db.append_private_lifecycle_audit(root, {
        "event": "mcp_call", "tool": "public_tool",
        "outcome": "received" if valid else "rejected",
        "reason": "metadata_valid" if valid else "metadata_mismatch",
        "digests": digests, "equality": {"thread_matches": bool(valid)},
        "observed_at": now(),
    }, task_id=task_id)


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
        public_tools=PUBLIC_TOOLS,
        internal_handlers=TOOLS,
        server_version=SERVER_VERSION,
        instructions=_mcp_server_instructions(audience),
        log_tool_error=log_tool_error,
        transport_metadata_audit=_audit_transport_metadata_arrival,
        audience=audience,
    )


if __name__ == "__main__":
    main()
