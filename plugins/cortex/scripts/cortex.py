#!/usr/bin/env python3
"""Local stdio MCP server for a durable, evidence-driven orchestration ledger."""
from __future__ import annotations

import contextlib
import html
import hashlib
import json
import math
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback; atomic replace still applies.
    fcntl = None

SCHEMA = "cortex/v7"
REPORT_SCHEMA = "cortex/report/v1"
QUESTION_SCHEMA = "cortex/question/v2"
LEGACY_QUESTION_SCHEMA = "cortex/question/v1"
QUESTION_SCHEMAS = {QUESTION_SCHEMA, LEGACY_QUESTION_SCHEMA}
ACTIVATION_COMMAND = "/cortex"
NORMAL_COMMAND = "/normal"
PROFILE_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "profiles.json"
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
PLUGIN_ROOT = PROFILE_CONTRACT_PATH.parent
PLUGIN_MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
MCP_PROJECT_ROOT: Path | None = None
MCP_OPENAI_FORM = False
_STATE_LOCK_LOCAL = threading.local()

try:
    SERVER_VERSION = str(json.loads(PLUGIN_MANIFEST_PATH.read_text(encoding="utf-8"))["version"])
except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
    raise RuntimeError("bundled Cortex plugin manifest is unreadable") from exc


def load_profile_contract() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
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
        name = str(item["name"])
        if name in profiles:
            raise RuntimeError("bundled Cortex profile contract contains duplicate profiles")
        profiles[name] = item
    return contract, profiles


PROFILE_CONTRACT, PROFILES = load_profile_contract()
AGENTS = set(PROFILES)
AVAILABLE_GATES = {
    "plan", "discover", "architecture", "database_architecture", "implementation",
    "qa", "security", "performance", "accessibility", "ux", "review",
    "documentation", "close",
}
# Gate IDs are part of the MCP contract.  The orchestrator sometimes emits
# human-facing labels (for example, ``planning``) even though the durable
# ledger uses the canonical IDs above.  Keep this compatibility map explicit
# and bounded: unknown IDs must still fail closed instead of being guessed.
PIPELINE_GATE_ALIASES = {
    "planning": "plan",
    "discovery": "discover",
    "architecture_design": "architecture",
    "database_design": "database_architecture",
    "testing": "qa",
    "verification": "qa",
    "quality_assurance": "qa",
    "code_review": "review",
    "documentation_sync": "documentation",
    "finalization": "close",
    "closing": "close",
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
    "C2": ["plan", "discover", "implementation", "qa", "review", "documentation", "close"],
    "C3": ["plan", "discover", "implementation", "qa", "review", "documentation", "close"],
}
GATE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
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
MAX_REPORT_GRANTS = 256
MAX_GATE_RECOVERY_FAILURES = 3
MAX_GATE_RECOVERY_EVENTS = 64
MAX_TOOL_ERROR_LOG_INPUT_BYTES = 16384
MAX_QUESTIONS_PER_ATTEMPT = 64
MAX_QUESTIONS_PER_TASK = 512
MAX_METRIC_EVENTS = 1000
MAX_METRIC_BYTES = 512 * 1024
SUPPORTED_MODELS = {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
REQUESTABLE_MODELS = SUPPORTED_MODELS
SUPPORTED_EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra"}
LIGHTWEIGHT_TASK_KINDS = {
    "read_only", "read-only", "reading", "discover", "discovery",
    "read_discovery", "read-discovery", "audit", "comparison",
    "comparative_audit", "comparative-audit",
    "data_gathering", "data-gathering", "crud", "crud_edit", "crud-edit",
    "small_fix", "small-fix",
}
# Analysis intent must not be inferred only from low risk.  A high-risk
# investigation is still a Luna investigation; the reasoning floor changes,
# but the worker model does not fall back to Terra.  Keep this list bounded and
# explicit because task_kind is model-supplied input at the MCP boundary.
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
ANALYSIS_REASONING_FLOORS = {
    "low": "medium",
    "moderate": "medium",
    "high": "high",
    "critical": "xhigh",
}
REASONING_EFFORT_ORDER = {name: index for index, name in enumerate(("low", "medium", "high", "xhigh", "max", "ultra"))}
AUDITABLE_EXTREME_CRITERIA = {
    "irreversible_multi_system_recovery",
    "safety_critical_incident_response",
    "novel_cross_system_failure_without_bounded_rollback",
}
TERMINAL_ATTEMPT_STATUSES = {"passed", "failed", "blocked", "cancelled", "superseded"}
AWAITING_HOST_SPAWN = "awaiting_host_spawn"
CAPABILITY_SOURCE = "host_spawn_agent_contract_2026-08-13"
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
    "scope": "all non-directory entries below project_root",
    "ignored_roots": [".git", ".codex/cortex"],
    "ignored_directory_names": ["__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"],
    "ignored_file_suffixes": [".pyc", ".pyo"],
    "symlinks": "record link target and never follow",
    "special_files": "record type and metadata without reading content",
}
VERIFICATION_COMMANDS = {
    "benign_success": {"argv": ["/usr/bin/true"], "cwd": "."},
    "benign_failure": {"argv": ["/usr/bin/false"], "cwd": "."},
}
SENSITIVE_KEY_RE = re.compile(r"(?i)(?:^|[_ -])(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|token|password|passwd|secret|private[_ -]?key|authorization)(?:$|[_ -])")
SENSITIVE_LOG_KEY_NAMES = {
    "apikey", "accesstoken", "refreshtoken", "clientsecret", "token",
    "password", "passwd", "secret", "privatekey", "authorization",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(value: str) -> str:
    if "/" in value or "\\" in value or value.strip() in {".", ".."}:
        raise ValueError("identifier must not contain path separators")
    candidate = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    if not candidate or not SAFE_ID_RE.fullmatch(candidate):
        raise ValueError("identifier must contain only lowercase letters, numbers, hyphens, or underscores")
    return candidate


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


def normalize_user_language(value: object, fallback_text: object = "") -> str:
    """Return a bounded language tag for user-facing coordinator messages."""
    requested = str(value or "").strip().lower().replace("_", "-")
    if requested:
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


def capture_project_manifest(root: Path | None = None) -> dict[str, Any]:
    """Capture every non-ignored project entry without following symlinks."""
    if root is None:
        raise ValueError("project root is required for manifest capture")
    base = root
    base = _reject_symlink_ancestry(base, "project root")
    entries: dict[str, dict[str, Any]] = {}
    ignored_roots = {tuple(Path(value).parts) for value in TRACKER_POLICY["ignored_roots"]}
    ignored_dirs = set(TRACKER_POLICY["ignored_directory_names"])
    ignored_suffixes = tuple(TRACKER_POLICY["ignored_file_suffixes"])

    def ignored(parts: tuple[str, ...], is_dir: bool) -> bool:
        if any(parts[: len(prefix)] == prefix for prefix in ignored_roots):
            return True
        if is_dir and parts and parts[-1] in ignored_dirs:
            return True
        return bool(not is_dir and parts and parts[-1].endswith(ignored_suffixes))

    def walk(directory: Path, relative: tuple[str, ...] = ()) -> None:
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda item: item.name)
        for child in children:
            parts = (*relative, child.name)
            info = child.stat(follow_symlinks=False)
            mode = info.st_mode
            is_directory = stat.S_ISDIR(mode)
            if ignored(parts, is_directory):
                continue
            rel = Path(*parts).as_posix()
            path = Path(child.path)
            if stat.S_ISLNK(mode):
                entries[rel] = {"kind": "symlink", "target": os.readlink(path), "mode": stat.S_IMODE(mode)}
            elif is_directory:
                walk(path, parts)
            elif stat.S_ISREG(mode):
                entries[rel] = _manifest_file(path, info)
            else:
                entries[rel] = {"kind": "special", "file_type": stat.S_IFMT(mode), "mode": stat.S_IMODE(mode), "size": info.st_size}
    walk(base)
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    policy = {**TRACKER_POLICY, "effective_ignored_roots": sorted(Path(*parts).as_posix() for parts in ignored_roots)}
    return {"schema": TRACKER_POLICY["schema"], "project_root": str(base), "policy": policy, "entries": entries, "entry_count": len(entries), "digest": digest_text(encoded), "captured_at": now()}


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


def reconcile_manifest(task_dir: Path, state: dict[str, Any], reported_paths: list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline_path = task_dir / "baseline-manifest.json"
    if not baseline_path.exists():
        raise ValueError("v7 task is missing its baseline manifest")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = capture_project_manifest(Path(baseline["project_root"]))
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
    activation_file = activation_path(root)
    if activation_file.exists():
        try:
            activations = json.loads(activation_file.read_text(encoding="utf-8"))
            changed = False
            for key, record in activations.items():
                if isinstance(record, dict) and record.get("task_id") == task_id:
                    record["task_id"] = None
                    record.pop("initialized_at", None)
                    activations[key] = record
                    changed = True
            if changed:
                write_json(activation_file, activations)
        except (OSError, json.JSONDecodeError):
            pass
    index_path = root / "active-tasks.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            changed = False
            for key, value in list(index.items()):
                if value == task_id and (not thread_id or key == thread_id):
                    del index[key]
                    changed = True
            if changed:
                if index:
                    write_json(index_path, index)
                else:
                    index_path.unlink()
        except (OSError, json.JSONDecodeError):
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
    (path / "tasks").mkdir(exist_ok=True, mode=0o700)
    (path / "tasks").chmod(0o700)
    return path


def activation_key(params: dict[str, Any]) -> str:
    key = str(params.get("thread_id") or params.get("principal") or "").strip()
    if not key:
        raise ValueError("explicit orchestration activation requires thread_id or principal")
    return redact(key, 256)


def activation_path(root: Path) -> Path:
    return root / "activations.json"


def activation_record(root: Path, params: dict[str, Any], task_id: str | None = None) -> dict[str, Any] | None:
    key = activation_key(params)
    path = activation_path(root)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8")).get(key)
    except (OSError, json.JSONDecodeError):
        return None
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
        raise ValueError("orchestration is inactive; send exact standalone /cortex text in the main chat first")
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
            "next_action": f"retry activate_orchestration with user_command={ACTIVATION_COMMAND!r}",
            "recoverable": True,
            "ledger_root": str(ledger_root(params)),
        }
    if activation_token != ACTIVATION_COMMAND:
        raise ValueError("explicit orchestration activation requires the exact standalone /cortex text trigger")
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
        path = activation_path(root)
        activations = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
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
        write_json(path, activations)
        return {"active": True, "key": key, "activation": activations[key], "ledger_root": str(root)}


def deactivate_orchestration(params: dict[str, Any]) -> dict[str, Any]:
    if str(params.get("user_command", "")).strip() != NORMAL_COMMAND:
        raise ValueError("explicit normal-mode transition requires the exact /normal command")
    root = ledger_root(params)
    key = activation_key(params)
    with state_lock(root):
        path = activation_path(root)
        activations = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        removed = activations.pop(key, None)
        if activations:
            write_json(path, activations)
        elif path.exists():
            path.unlink()
        return {"active": False, "key": key, "removed": bool(removed)}


def activation_status(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    if not str(params.get("thread_id") or params.get("principal") or "").strip():
        path = activation_path(root)
        if not path.exists():
            return {"active": False, "activation": None, "ledger_root": str(root), "identity_inferred": False}
        try:
            activations = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            activations = {}
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
        receipt_path = root / "classification-receipts" / f"{receipt_id}.json"
        write_json(receipt_path, payload)
        return {**result, "classification_id": receipt_id}


@contextlib.contextmanager
def state_lock(root: Path) -> Iterator[None]:
    """Serialize the entire read/validate/write mutation across MCP processes."""
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


def write_json(path: Path, value: Any) -> None:
    write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


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


def write_json_exclusive(path: Path, value: Any) -> None:
    write_text_exclusive(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


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
    try:
        append_journal(directory, event, detail)
    except (OSError, ValueError):
        pass


def task_index_path(root: Path) -> Path:
    return root / "task-index.json"


def read_task_index(root: Path) -> dict[str, Any]:
    path = task_index_path(root)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("task index is unreadable") from exc
    return value if isinstance(value, dict) else {}


def task_paths(task_id: str, params: dict[str, Any]) -> tuple[Path, Path, Path]:
    root = ledger_root(params)
    normalized = safe_id(task_id)
    tasks_dir = root / "tasks"
    index = read_task_index(root)
    indexed = index.get(normalized)
    directory = indexed.get("directory") if isinstance(indexed, dict) else None
    if isinstance(directory, str) and directory not in {"", ".", ".."} and Path(directory).name == directory:
        task_dir = _contained_path(tasks_dir, tasks_dir / directory, "indexed task directory")
    else:
        task_dir = _contained_path(tasks_dir, tasks_dir / f"missing-{normalized}", "task directory")
    if task_dir.exists():
        _reject_symlink_ancestry(task_dir, "task directory")
        for filename in ("task.json", "current.json"):
            candidate = task_dir / filename
            if candidate.exists() and candidate.is_symlink():
                raise ValueError(f"{filename} must not be a symlink")
    return root, task_dir, task_dir / "current.json"


def allocate_task_directory(root: Path, task_id: str) -> tuple[int, Path]:
    tasks_dir = root / "tasks"
    index = read_task_index(root)
    numbers = []
    for entry in index.values():
        if isinstance(entry, dict) and isinstance(entry.get("number"), int):
            numbers.append(entry["number"])
    for candidate in tasks_dir.iterdir():
        match = re.match(r"^(\d+)-", candidate.name)
        if match:
            numbers.append(int(match.group(1)))
    number = max(numbers, default=0) + 1
    return number, tasks_dir / f"{number:04d}-{task_id}"


def lane_paths(lane_id: str, params: dict[str, Any]) -> tuple[Path, Path, Path]:
    root = ledger_root(params)
    normalized = safe_id(lane_id)
    lane_dir = root / "lanes" / normalized
    if lane_dir.exists() and lane_dir.is_symlink():
        raise ValueError("lane directory must not be a symlink")
    return root, lane_dir, lane_dir / "current.json"


def load_lane(lane_id: str, params: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    root, lane_dir, state_path = lane_paths(lane_id, params)
    if not state_path.exists():
        raise ValueError(f"lane '{lane_id}' does not exist")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("schema") != SCHEMA:
        raise ValueError("lane ledger schema is not supported; create a new v7 lane")
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


def log_tool_error(request: Any, request_id: Any, raw_line: str, error: BaseException, structured_result: Any = None) -> None:
    """Append a redacted MCP tool failure without masking the original error."""
    try:
        context = _tool_error_context(request, request_id, raw_line)
        if structured_result is not None:
            context["structured_result"] = _bounded_error_input(structured_result)
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
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
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


def _tool_result_error(value: Any) -> str | None:
    """Identify recoverable error-shaped tool results that never raise."""
    if not isinstance(value, dict):
        return None
    if value.get("recorded") is False:
        return str(value.get("error") or value.get("reason") or value.get("next_action") or "tool returned recorded=false")
    if value.get("status") in {"invalid_answer", "invalid_declaration"}:
        return str(value.get("error") or value.get("reason") or f"tool returned {value['status']}")
    if value.get("atomic") is False:
        confirmation = value.get("confirmed")
        if isinstance(confirmation, dict) and (
            confirmation.get("confirmed") is False or confirmation.get("error") or confirmation.get("reason")
        ):
            return str(confirmation.get("error") or confirmation.get("reason") or "host confirmation failed")
    return None


def profiles_for_gate(gate: str) -> list[str]:
    """Return only explicitly routed profiles; unknown gates never imply a writer."""
    return sorted(
        name
        for name, profile in PROFILES.items()
        if profile.get("route_category") == "automatic" and gate in profile.get("gates", [])
    )


def resolve_sol_escalation(params: dict[str, Any]) -> dict[str, str] | None:
    """Validate a structured, auditable exception for non-security Sol work."""
    raw = params.get("sol_escalation")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("sol_escalation must be a structured object")
    kind = str(raw.get("kind", "")).strip()
    if kind == "auditable_extreme":
        criterion = str(raw.get("criterion", "")).strip()
        audit_ref = str(raw.get("audit_ref", "")).strip()
        if criterion not in AUDITABLE_EXTREME_CRITERIA or not audit_ref:
            raise ValueError("auditable_extreme Sol escalation requires a supported criterion and audit_ref")
        return {"kind": kind, "criterion": criterion, "audit_ref": redact(audit_ref, 500)}
    if kind == "terra_failure":
        attempt_id = safe_id(str(raw.get("prior_terra_attempt_id", "")))
        validated = params.get("_validated_terra_failure")
        if not isinstance(validated, dict) or validated.get("attempt_id") != attempt_id:
            raise ValueError("terra_failure Sol escalation requires a validated failed Terra attempt in this task ledger")
        return {"kind": kind, "prior_terra_attempt_id": attempt_id}
    raise ValueError("sol_escalation.kind must be auditable_extreme or terra_failure")


def is_analysis_task_kind(task_kind: str) -> bool:
    return task_kind in ANALYSIS_TASK_KINDS or any(
        task_kind.startswith(prefix) for prefix in ANALYSIS_TASK_KIND_PREFIXES
    )


def resolve_dispatch_route(params: dict[str, Any]) -> dict[str, Any]:
    select_project_root(params)
    profile_name = str(params.get("agent") or params.get("profile") or "").strip()
    profile = PROFILES.get(profile_name)
    if profile is None:
        raise ValueError(f"unknown agent '{profile_name}'")
    task_kind = normalize_routing_id(params.get("task_kind"), "task_kind")
    risk = str(params.get("risk", "")).strip().lower()
    if risk not in {"low", "moderate", "high", "critical"}:
        raise ValueError("risk must be low, moderate, high, or critical")
    complexity = str(params.get("complexity") or "C1").strip().upper()
    if complexity not in {"C1", "C2", "C3"}:
        raise ValueError("complexity must be C1, C2, or C3")
    read_only = profile.get("sandbox") == "read-only"
    # Security gates are validated by record_delegation before this private
    # marker is supplied. The profile and explicit security kind are also
    # trusted routing context, so a contradictory lightweight kind cannot
    # downgrade security work to a lightweight route.
    security_context = (
        task_kind == "security"
        or profile_name == "security_auditor"
        or params.get("_security_gate") is True
    )
    if security_context:
        task_kind = "security"
    sol_escalation = None if security_context else resolve_sol_escalation(params)
    lightweight_dispatch = (
        task_kind in LIGHTWEIGHT_TASK_KINDS
        or task_kind.startswith("read_only")
        or task_kind.startswith("read_discovery")
        or task_kind.startswith("data_gather")
        or task_kind.startswith("audit")
    )
    # Model choice follows the declared work intent, not the sandbox alone.
    # A read-only profile can still own architecture, review, or another
    # non-analysis task where Terra is appropriate; the orchestrator must use
    # an explicit analysis/discovery task_kind to select the Luna route.
    analysis_dispatch = lightweight_dispatch or is_analysis_task_kind(task_kind)
    # Read-only is a property of the dispatched work, not only of the worker
    # profile.  Documentation/verification profiles may be allowed to touch
    # docs in normal work, but a read-only audit must remain read-only in the
    # durable attempt record regardless of which profile owns its gate.
    read_only = read_only or analysis_dispatch
    if security_context:
        policy_model, policy_reason = "gpt-5.6-sol", "security_context"
    elif sol_escalation:
        policy_model, policy_reason = "gpt-5.6-sol", f"authorized_{sol_escalation['kind']}_sol_escalation"
    elif analysis_dispatch:
        policy_model, policy_reason = "gpt-5.6-luna", "analysis_or_lightweight_work"
    else:
        policy_model, policy_reason = "gpt-5.6-terra", "default_non_security_work"
    requested_model = str(params.get("requested_model") or policy_model).strip()
    if requested_model not in REQUESTABLE_MODELS:
        raise ValueError("requested_model is not supported by Cortex routing policy")
    requested_effort = str(params.get("requested_reasoning_effort") or "").strip().lower() or (
        "high" if policy_model == "gpt-5.6-sol"
        else ANALYSIS_REASONING_FLOORS[risk] if analysis_dispatch
        else "medium"
    )
    selected_effort = "low" if requested_effort == "none" else requested_effort
    if selected_effort not in SUPPORTED_EFFORTS:
        raise ValueError("requested_reasoning_effort cannot be resolved to a supported effort")
    if policy_model == "gpt-5.6-sol" and selected_effort in {"low", "medium"}:
        selected_effort = "high"
    elif analysis_dispatch:
        minimum_effort = ANALYSIS_REASONING_FLOORS[risk]
        if REASONING_EFFORT_ORDER[selected_effort] < REASONING_EFFORT_ORDER[minimum_effort]:
            selected_effort = minimum_effort
    fallback_reason = None
    escalation_reason = redact(params.get("escalation_reason", ""), 1000) or None
    if policy_model == "gpt-5.6-sol":
        selected_model = "gpt-5.6-sol"
        if requested_model != selected_model:
            fallback_reason = "policy_model_enforced"
    elif policy_model == "gpt-5.6-terra":
        if requested_model == "gpt-5.6-sol":
            raise ValueError("non-security gpt-5.6-sol requires a structured auditable_extreme or validated terra_failure escalation")
        selected_model = "gpt-5.6-terra"
        if requested_model != selected_model:
            fallback_reason = "policy_model_enforced"
    else:
        if requested_model == "gpt-5.6-sol":
            raise ValueError("non-security gpt-5.6-sol requires a structured auditable_extreme or validated terra_failure escalation")
        selected_model = "gpt-5.6-luna"
        if requested_model != selected_model:
            fallback_reason = "policy_model_enforced"
    if selected_model not in SUPPORTED_MODELS:
        raise ValueError("dispatch route cannot be resolved to a host-supported model")
    return {
        "requested_model": requested_model,
        "selected_model": selected_model,
        "requested_reasoning_effort": requested_effort,
        "selected_reasoning_effort": selected_effort,
        "task_kind": task_kind,
        "risk": risk,
        "complexity": complexity,
        "read_only": read_only,
        "capability_source": CAPABILITY_SOURCE,
        "policy_model": policy_model,
        "policy_reason": policy_reason,
        "fallback_reason": fallback_reason,
        "escalation_reason": escalation_reason,
        "sol_escalation": sol_escalation,
    }


REPORT_FIELDS = set(PROFILE_CONTRACT.get("shared_worker_contract", {}).get("required_report_fields", []))
if REPORT_FIELDS != {"summary", "findings", "questions", "changed_files", "tests", "evidence", "uncertainty", "next_action"}:
    raise RuntimeError("bundled Cortex shared worker report contract is invalid")


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
    if not isinstance(value, dict) or set(value) != REPORT_FIELDS:
        missing = sorted(REPORT_FIELDS - set(value) if isinstance(value, dict) else REPORT_FIELDS)
        unknown = sorted(set(value) - REPORT_FIELDS) if isinstance(value, dict) else []
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if unknown:
            detail.append("unknown: " + ", ".join(unknown))
        raise ValueError("report must contain exactly the cortex/report/v1 fields" + (" (" + "; ".join(detail) + ")" if detail else ""))
    summary = str(value["summary"]).strip()
    next_action = str(value["next_action"]).strip()
    if not summary or not next_action:
        raise ValueError("report summary and next_action are required")
    result: dict[str, Any] = {"summary": redact(summary, 4000)}
    for field in ("findings", "questions", "tests", "evidence", "uncertainty"):
        items = value[field]
        if not isinstance(items, list) or len(items) > MAX_REPORT_ITEMS:
            raise ValueError(f"report {field} must be an array with at most {MAX_REPORT_ITEMS} items")
        result[field] = [sanitize_structured(item) for item in items]
    changed_files = value["changed_files"]
    if not isinstance(changed_files, list) or len(changed_files) > MAX_REPORT_ITEMS:
        raise ValueError(f"report changed_files must be an array with at most {MAX_REPORT_ITEMS} items")
    result["changed_files"] = [_safe_project_relative_path(item) for item in changed_files]
    result["next_action"] = redact(next_action, 4000)
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise ValueError(f"report exceeds the {MAX_REPORT_BYTES}-byte limit")
    return result


def report_bus_paths(task_dir: Path) -> dict[str, Path]:
    reports = _contained_path(task_dir, task_dir / "reports", "report bus")
    paths = {
        "root": reports,
        "records": reports / "records",
        "markdown": reports / "markdown",
        "receipts": reports / "receipts",
        "consumptions": reports / "consumptions",
        "delegations": reports / "delegations",
        "grants": reports / "grants",
        "index": reports / "index.json",
    }
    for key in ("root", "records", "markdown", "receipts", "consumptions", "delegations", "grants"):
        path = _contained_path(task_dir, paths[key], f"report bus {key}")
        if path.exists():
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"report bus {key} must be a real directory")
        else:
            path.mkdir(parents=True, exist_ok=False, mode=0o700)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"report bus {key} must be a real directory")
        os.chmod(path, 0o700, follow_symlinks=False)
    return paths


def _resolve_report_receipt_hint(
    task_dir: Path,
    state: dict[str, Any],
    gate: str,
    attempt_id: str | None,
    value: object,
) -> tuple[str, dict[str, str] | None]:
    """Resolve a stale adapter hint without weakening receipt ownership.

    Some host adapters previously passed a context-grant id where the
    one-use report receipt was required.  A grant is safe to resolve only
    when it belongs to this task/attempt and names exactly one report whose
    receipt still matches the current gate.  Ambiguous or foreign hints are
    returned unchanged and are handled by the normal bounded recovery path.
    """
    raw = str(value or "").strip()
    if not raw:
        return raw, None
    candidate = safe_id(raw)
    paths = report_bus_paths(task_dir)
    receipt_path = paths["receipts"] / f"{candidate}.json"
    if receipt_path.exists() and not receipt_path.is_symlink():
        return candidate, None

    # The task report index deliberately exposes report ids to the coordinator,
    # while the one-use receipt is returned to the worker that published the
    # report.  Treat an exact report id as a recoverable *hint*, never as the
    # receipt itself: resolve it only when it belongs to this task, gate, and
    # attempt and its matching receipt is still usable.  This lets a resumed
    # coordinator recover from a report/receipt handoff without broadening
    # access to another attempt's receipt.
    record_path = paths["records"] / f"{candidate}.json"
    if record_path.exists() and not record_path.is_symlink():
        record = _read_private_json(record_path, "report record")
        if (
            record.get("schema") == REPORT_SCHEMA
            and record.get("task_id") == state["task_id"]
            and record.get("gate") == gate
            and record.get("attempt_id") == attempt_id
        ):
            resolved = f"report-receipt-{candidate}"
            resolved_path = paths["receipts"] / f"{resolved}.json"
            if resolved_path.exists() and not resolved_path.is_symlink():
                receipt = _read_private_json(resolved_path, "report receipt")
                if (
                    receipt.get("schema") == REPORT_SCHEMA
                    and receipt.get("task_id") == state["task_id"]
                    and receipt.get("gate") == gate
                    and receipt.get("attempt_id") == attempt_id
                    and receipt.get("report_id") == candidate
                    and not receipt.get("invalidated")
                    and not receipt.get("consumed_at")
                    and not receipt.get("consumed_by_evidence_id")
                ):
                    return resolved, {
                        "requested": candidate,
                        "used": resolved,
                        "reason": "report id resolved to its exact active report receipt",
                    }

    grant_path = paths["grants"] / f"{candidate}.json"
    if not grant_path.exists() or grant_path.is_symlink():
        return candidate, None
    grant = _read_private_json(grant_path, "report grant")
    if (
        grant.get("schema") != REPORT_SCHEMA
        or grant.get("task_id") != state["task_id"]
        or grant.get("attempt_id") != attempt_id
        or not isinstance(grant.get("report_ids"), list)
        or len(grant["report_ids"]) != 1
    ):
        return candidate, None
    report_id = safe_id(str(grant["report_ids"][0]))
    resolved = f"report-receipt-{report_id}"
    resolved_path = paths["receipts"] / f"{resolved}.json"
    if not resolved_path.exists() or resolved_path.is_symlink():
        return candidate, None
    receipt = _read_private_json(resolved_path, "report receipt")
    if (
        receipt.get("schema") != REPORT_SCHEMA
        or receipt.get("task_id") != state["task_id"]
        or receipt.get("gate") != gate
        or receipt.get("attempt_id") != attempt_id
        or receipt.get("invalidated")
        or receipt.get("consumed_at")
        or receipt.get("consumed_by_evidence_id")
    ):
        return candidate, None
    return resolved, {"requested": candidate, "used": resolved, "reason": "context grant resolved to its unique report receipt"}


def question_bus_paths(task_dir: Path) -> dict[str, Path]:
    root = _contained_path(task_dir, task_dir / "questions", "question bus")
    records = _contained_path(root, root / "records", "question records")
    for label, path in (("question bus", root), ("question records", records)):
        if path.exists():
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"{label} must be a real directory")
        else:
            path.mkdir(parents=True, exist_ok=False, mode=0o700)
        os.chmod(path, 0o700, follow_symlinks=False)
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
        elif isinstance(item, dict):
            label = redact(str(item.get("label", "")).strip(), 120)
            description = redact(str(item.get("description", "")).strip(), 400) or label
        else:
            raise ValueError("question options must be strings or objects")
        if not label:
            raise ValueError("question options require a non-empty label")
        options.append({"label": label, "description": description})
    if len({item["label"] for item in options}) != len(options):
        raise ValueError("question option labels must be unique")
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
    digest = digest_text(json.dumps({"question": sanitized_question, "context": context, "blocking": blocking, "config": config}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return sanitized_question, context, blocking, config, digest


def _question_records(paths: dict[str, Path], state: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for path in sorted(paths["records"].glob("question-*.json")):
        match = re.fullmatch(r"question-(\d+)\.json", path.name)
        if path.is_symlink() or not match:
            raise ValueError("question record namespace contains an unsafe entry")
        record = _read_private_json(path, "question record")
        question_id = str(record.get("question_id", ""))
        if record.get("schema") not in QUESTION_SCHEMAS or record.get("task_id") != state["task_id"] or f"{question_id}.json" != path.name:
            raise ValueError(f"question record failed validation: {path.name}")
        _attempt(state, safe_id(str(record.get("attempt_id", ""))))
        records.append(record)
    return records


def _question_sequence(records: list[dict[str, Any]]) -> int:
    return max(
        (max(int(item.get("published_sequence", 0)), int(item.get("answered_sequence") or 0)) for item in records),
        default=0,
    )


def _read_private_json(path: Path, label: str) -> Any:
    path = _reject_symlink_ancestry(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{label} must be a regular file")
        chunks = []
        size = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_REPORT_BYTES * 4:
                raise ValueError(f"{label} is oversized")
            chunks.append(chunk)
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    finally:
        os.close(descriptor)


def _receipt_identity(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("schema"), value.get("receipt_id"), value.get("report_id"), value.get("task_id"),
        value.get("gate"), value.get("attempt_id"), value.get("content_digest"),
    )


def _consumption_path(paths: dict[str, Path], receipt_id: str) -> Path:
    receipt = safe_id(receipt_id)
    return _contained_path(paths["consumptions"], paths["consumptions"] / f"{receipt}.json", "report consumption tombstone")


def _consumption_tombstone(receipt: dict[str, Any], evidence_id: str, consumed_at: str | None = None) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "receipt_id": receipt["receipt_id"],
        "report_id": receipt["report_id"],
        "task_id": receipt["task_id"],
        "gate": receipt["gate"],
        "attempt_id": receipt["attempt_id"],
        "content_digest": receipt["content_digest"],
        "consumed_at": consumed_at or now(),
        "consumed_by_evidence_id": safe_id(evidence_id),
    }


def _read_consumption(paths: dict[str, Path], receipt: dict[str, Any]) -> dict[str, Any] | None:
    path = _consumption_path(paths, str(receipt.get("receipt_id", "")))
    if not path.exists():
        return None
    tombstone = _read_private_json(path, "report consumption tombstone")
    if _receipt_identity(tombstone) != _receipt_identity(receipt):
        raise ValueError(f"report consumption tombstone failed validation: {path.name}")
    if not tombstone.get("consumed_at") or not SAFE_ID_RE.fullmatch(str(tombstone.get("consumed_by_evidence_id", ""))):
        raise ValueError(f"report consumption tombstone is incomplete: {path.name}")
    return tombstone


def _report_markdown(record: dict[str, Any]) -> str:
    def escaped(value: Any) -> str:
        text = html.escape(str(value), quote=True)
        return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", text)

    report = record["report"]
    lines = [f"# Report {escaped(record['report_id'])}", "", f"**Producer:** {escaped(record['producer']['profile'])}", "", "## Summary", "", escaped(report["summary"])]
    for field in ("findings", "questions", "changed_files", "tests", "evidence", "uncertainty"):
        lines.extend(["", f"## {field.replace('_', ' ').title()}", ""])
        items = report[field]
        if not items:
            lines.append("- None")
        else:
            for item in items:
                rendered = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
                lines.append(f"- {escaped(rendered)}")
    lines.extend(["", "## Next Action", "", escaped(report["next_action"]), ""])
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
        "created_at": record["created_at"],
    }


def _recover_report_receipt(
    paths: dict[str, Path], record: dict[str, Any], state: dict[str, Any], invalidated: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Recover a receipt without ever reversing a durable consumption decision."""
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
    existing = _read_private_json(receipt_path, "report receipt") if receipt_path.exists() else None
    if existing is not None and _receipt_identity(existing) != _receipt_identity(base):
        raise ValueError(f"report receipt failed reconciliation: {receipt_path.name}")
    evidence = next((item for item in state.get("evidence", []) if item.get("report_id") == report_id), None)
    tombstone = _read_consumption(paths, base)
    if tombstone is None and evidence is not None:
        tombstone = _consumption_tombstone(base, str(evidence["evidence_id"]), str(evidence.get("created_at") or now()))
        write_json_exclusive(_consumption_path(paths, base["receipt_id"]), tombstone)
    if tombstone is None and existing is not None and (existing.get("consumed_at") or existing.get("consumed_by_evidence_id")):
        evidence_id = safe_id(str(existing.get("consumed_by_evidence_id") or "unknown-consumption"))
        tombstone = _consumption_tombstone(base, evidence_id, str(existing.get("consumed_at") or now()))
        write_json_exclusive(_consumption_path(paths, base["receipt_id"]), tombstone)
    if tombstone is not None:
        base["consumed_at"] = tombstone["consumed_at"]
        base["consumed_by_evidence_id"] = tombstone["consumed_by_evidence_id"]
    if existing is not None:
        base["invalidated"] = bool(existing.get("invalidated") or invalidated)
        base["created_at"] = existing.get("created_at") or base["created_at"]
    repaired = existing != base
    if existing is None:
        write_json_exclusive(receipt_path, base)
    elif repaired:
        write_json(receipt_path, base)
    return base, repaired


def _report_index(paths: dict[str, Path], task_id: str) -> dict[str, Any]:
    if not paths["index"].exists():
        return {"schema": REPORT_SCHEMA, "task_id": task_id, "reports": [], "submissions": {}, "updated_at": now()}
    value = _read_private_json(paths["index"], "report index")
    if value.get("schema") != REPORT_SCHEMA or value.get("task_id") != task_id:
        raise ValueError("report index does not belong to this task")
    if len(value.get("reports", [])) > MAX_REPORTS_PER_TASK or len(value.get("submissions", {})) > MAX_REPORTS_PER_TASK:
        raise ValueError("report index exceeds its bounded capacity")
    return value


def _delegation_report_index(paths: dict[str, Path], task_id: str, attempt_id: str) -> tuple[Path, dict[str, Any]]:
    attempt = safe_id(attempt_id)
    directory = _contained_path(paths["delegations"], paths["delegations"] / attempt, "delegation report index")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "index.json"
    if path.exists():
        value = _read_private_json(path, "delegation report index")
    else:
        value = {"schema": REPORT_SCHEMA, "task_id": task_id, "attempt_id": attempt, "owned_report_ids": [], "context_report_ids": [], "grant_ids": [], "updated_at": now()}
    if value.get("task_id") != task_id or value.get("attempt_id") != attempt:
        raise ValueError("delegation report index scope mismatch")
    if len(value.get("owned_report_ids", [])) > MAX_REPORTS_PER_ATTEMPT or len(value.get("context_report_ids", [])) > MAX_REPORTS_PER_TASK or len(value.get("grant_ids", [])) > MAX_REPORT_GRANTS:
        raise ValueError("delegation report index exceeds its bounded capacity")
    return path, value


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
    write_json(state_path, state)
    append_journal_best_effort(lane_dir, event, detail)
    return state


def load_state(task_id: str, params: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    root, task_dir, state_path = task_paths(task_id, params)
    if not state_path.exists():
        raise ValueError(f"task '{task_id}' does not exist")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    if state.get("schema") != SCHEMA or task.get("schema") != SCHEMA:
        raise ValueError("task ledger schema is not supported; create a new v7 task")
    return root, task_dir, state


def guard_revision(state: dict[str, Any], expected: int | None) -> None:
    if expected is not None and state["revision"] != expected:
        raise ValueError(f"stale revision: expected {expected}, actual {state['revision']}")


def save_state(task_dir: Path, state_path: Path, state: dict[str, Any], event: str, detail: str) -> dict[str, Any]:
    state["revision"] += 1
    state["updated_at"] = now()
    write_json(state_path, state)
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
    """Keep the legacy current_gate alias and the explicit wave in sync."""
    wave = active_gates(state)
    state["current_gates"] = wave
    state["current_gate"] = wave[0] if wave else None
    return wave


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
    path = root / "resource-claims.json"
    claims = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
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
    write_json(path, claims)
    return entry


def _release_global_resource(root: Path, resource: str, owner: str, scope_kind: str, scope_id: str) -> None:
    path = root / "resource-claims.json"
    claims = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    key = digest_text(resource)
    existing = claims.get(key)
    if not existing or (existing.get("owner_digest"), existing.get("scope_kind"), existing.get("scope_id")) != (digest_text(owner), scope_kind, scope_id):
        raise ValueError("global resource is not held by this owner and scope")
    del claims[key]
    if claims:
        write_json(path, claims)
    elif path.exists():
        path.unlink()


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
    paths = report_bus_paths(task_dir)
    invalidated_attempts = {item["attempt_id"] for item in state.get("attempts", []) if item.get("invalidated")}
    for path in paths["receipts"].glob("report-receipt-*.json"):
        if path.is_symlink():
            raise ValueError("report receipt must not be a symlink")
        receipt = _read_private_json(path, "report receipt")
        if receipt.get("attempt_id") in invalidated_attempts and not receipt.get("invalidated"):
            receipt["invalidated"] = True
            receipt["invalidated_at"] = now()
            write_json(path, receipt)


def create_lane(params: dict[str, Any]) -> dict[str, Any]:
    lane_id = safe_id(str(params["lane_id"]))
    root = ledger_root(params)
    require_activation(params)
    with state_lock(root):
        _, lane_dir, state_path = lane_paths(lane_id, params)
        if state_path.exists():
            existing = json.loads(state_path.read_text(encoding="utf-8"))
            if existing.get("schema") != SCHEMA:
                raise ValueError("task ledger schema is not supported; create a new v7 task")
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
        write_json(lane_dir / "lane.json", {"schema": SCHEMA, "lane_id": lane_id, "owner": owner, "mode": mode, "purpose": state["purpose"], "declarations": state["declarations"], "created_at": state["created_at"]})
        write_json(state_path, state)
        append_journal_best_effort(lane_dir, "initialized", f"{mode} lane")
        return {"created": True, "lane_id": lane_id, "state": state}


def lane_status(params: dict[str, Any]) -> dict[str, Any]:
    _, lane_dir, state = load_lane(str(params["lane_id"]), params)
    authorize_principal({"principal": state.get("owner")}, params)
    return {"lane": json.loads((lane_dir / "lane.json").read_text(encoding="utf-8")), "state": state}


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
        save_lane(lane_dir, lane_dir / "current.json", state, "lease", f"claimed by {state['lease']['owner']}")
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
        save_lane(lane_dir, lane_dir / "current.json", state, "release", "lane lease released")
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
        save_lane(lane_dir, lane_dir / "current.json", state, "materialize", f"materialized {len(results)} declaration(s)")
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
        save_lane(lane_dir, lane_dir / "current.json", state, "reconcile", f"reconciled {len(results)} declaration(s)")
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
        save_lane(lane_dir, lane_dir / "current.json", state, "retire", "clean retirement")
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
        save_state(task_dir, task_dir / "current.json", state, "lane", f"bound to {lane['lane_id']}")
        save_lane(lane_dir, lane_dir / "current.json", lane, "bind", state["task_id"])
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
        save_lane(lane_dir, lane_dir / "current.json", state, "resource", f"{redact(path, 300)} → {redact(owner, 128)}")
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
        save_lane(lane_dir, lane_dir / "current.json", state, "resource_release", f"{redact(path, 300)} released")
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
        return any((word in requirements) if (" " in word or "-" in word) else (word in tokens) for word in words)

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

    if proposed_pipeline is None and has("security", "auth", "permission", "secret", "privacy"):
        add_before("security", "review", "security or authorization concerns")
    if proposed_pipeline is None and has("architecture", "design", "contract", "refactor", "cross-cutting"):
        add_before("architecture", "implementation", "architecture, design, contract, or cross-cutting change")

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
    if proposed_pipeline is None and database_reason:
        add_before("database_architecture", "implementation", database_reason)
    if proposed_pipeline is None and has("performance", "latency", "load", "benchmark"):
        add_before("performance", "review", "performance or load concern")
    if proposed_pipeline is None and has("accessibility", "a11y", "screen reader", "keyboard"):
        add_before("accessibility", "review", "accessibility requirement")
    if proposed_pipeline is None and has("frontend", "ui", "ux", "design system"):
        add_before("ux", "implementation", "UI/UX or design-system work")
    if proposed_pipeline is None and has("documentation", "docs", "runbook", "adr"):
        add_before("documentation", "close", "explicit documentation deliverable")
    parallel_groups = normalize_parallel_groups(params.get("parallel_groups"), pipeline)
    roles = {"plan": ["planner"], "discover": ["explorer"], "architecture": ["architect"], "database_architecture": ["database_architect"], "implementation": ["general"], "qa": ["qa_engineer", "build_verification"], "security": ["security_auditor"], "performance": ["performance_engineer"], "accessibility": ["accessibility_engineer"], "ux": ["ux_designer"], "review": ["code_reviewer"], "documentation": ["technical_writer"], "close": ["build_verification"]}
    return {"complexity": complexity, "base_pipeline": BASE_PIPELINES[complexity], "pipeline": pipeline, "parallel_groups": parallel_groups, "pipeline_source": pipeline_source, "pipeline_corrections": pipeline_corrections, "conditional_gates": additions, "conditional_gate_reasons": addition_reasons, "available_gates": sorted(AVAILABLE_GATES), "suggested_roles": {gate: roles.get(gate, profiles_for_gate(gate)) for gate in pipeline}}


def init_task(params: dict[str, Any]) -> dict[str, Any]:
    task_id = safe_id(str(params["task_id"]))
    root = ledger_root(params)
    activation = require_activation(params, task_id)
    with state_lock(root):
        _, task_dir, state_path = task_paths(task_id, params)
        if state_path.exists():
            existing = json.loads(state_path.read_text(encoding="utf-8"))
            if existing.get("schema") != SCHEMA:
                raise ValueError("task ledger schema is not supported; create a new v7 task")
            authorize_principal(existing, params)
            task_definition = _read_private_json(task_dir / "task.json", "task definition")
            requested_objective = str(params.get("objective", "")).strip()
            stored_objective = str(task_definition.get("objective", ""))
            objective_correction = (
                {"requested": requested_objective, "used": stored_objective, "source": "immutable_task_definition"}
                if requested_objective and requested_objective != stored_objective else None
            )
            activation_file = activation_path(root)
            activations = json.loads(activation_file.read_text(encoding="utf-8"))
            activation_id = activation_key(params)
            current_activation = activations.get(activation_id)
            if not isinstance(current_activation, dict) or current_activation.get("schema") != SCHEMA:
                raise ValueError("orchestration activation disappeared while resuming task initialization")
            current_activation["task_id"] = task_id
            current_activation["initialized_at"] = current_activation.get("initialized_at") or now()
            current_activation["resumed_at"] = now()
            activations[activation_id] = current_activation
            write_json(activation_file, activations)
            return {"created": False, "resumed": True, "task_id": task_id, "state": existing, "objective_correction": objective_correction, "ledger_root": str(root)}
        if not str(params.get("classification_id", "")).strip():
            raise ValueError("init_task requires a prior classify_task classification_id")
        classification_id = safe_id(str(params["classification_id"]))
        receipt_path = root / "classification-receipts" / f"{classification_id}.json"
        if not receipt_path.exists() or receipt_path.is_symlink():
            raise ValueError("init_task requires a prior classify_task classification_id")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("schema") != SCHEMA:
            raise ValueError("classification receipt schema is not supported; classify the v7 task again")
        if receipt.get("consumed_by"):
            raise ValueError("classification receipt has already been consumed")
        mismatch = []
        if receipt.get("activation_key") != activation_key(params):
            mismatch.append("activation")
        if mismatch:
            raise ValueError("classification receipt does not match this " + ", ".join(mismatch))
        classification = receipt["classification"]
        receipt_requirements = receipt.get("requirements")
        if receipt_requirements is None:
            # Receipts written before the self-contained contract was added
            # cannot support omission safely.  They remain usable only when
            # their original, digest-checked requirements are explicitly
            # supplied; otherwise the coordinator must classify again.
            if "requirements" not in params:
                raise ValueError("legacy classification receipt has no reusable requirements; classify the task again")
            receipt_requirements = [redact(item, 500) for item in params["requirements"]][:100]
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
        state_path = task_dir / "current.json"
        task_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        preflight_journal(task_dir)
        for name in ("delegations", "reports", "handoffs", "locks", "evidence"):
            (task_dir / name).mkdir(exist_ok=True, mode=0o700)
        thread_id = str(params.get("thread_id", "")).strip()
        principal = redact(params.get("principal") or thread_id or "local", 256)
        baseline = capture_project_manifest(select_project_root(params))
        user_language = normalize_user_language(params.get("user_language"), params.get("objective", ""))
        task = {"schema": SCHEMA, "task_id": task_id, "task_number": task_number, "objective": redact(params.get("objective", "")), "complexity": classification["complexity"], "base_pipeline": classification["base_pipeline"], "initial_pipeline": pipeline, "parallel_groups": parallel_groups, "requirements": receipt_requirements, "acceptance_criteria": [redact(item, 1000) for item in params.get("acceptance_criteria", [])][:100], "scope": [redact(item, 500) for item in params.get("scope", [])][:100], "allowed_paths": [redact(item, 500) for item in params.get("allowed_paths", [])][:100], "verification": [redact(item, 1000) for item in params.get("verification", [])][:100], "budget": redact(params.get("budget", ""), 500), "pause_conditions": [redact(item, 1000) for item in params.get("pause_conditions", [])][:100], "thread_id": redact(thread_id, 256), "principal": principal, "user_language": user_language, "internal_language": "en", "classification_id": classification_id, "project_root": baseline["project_root"], "tracker_policy": TRACKER_POLICY, "created_at": now()}
        state = {"schema": SCHEMA, "task_id": task_id, "task_number": task_number, "status": "active", "principal": principal, "thread_id": redact(thread_id, 256), "user_language": user_language, "internal_language": "en", "complexity": classification["complexity"], "current_pipeline": pipeline, "parallel_groups": parallel_groups, "current_gate": pipeline[0], "current_gates": active_gates({"current_pipeline": pipeline, "parallel_groups": parallel_groups, "completed_gates": [], "skipped_gates": []}), "completed_gates": [], "skipped_gates": [], "gates": {}, "attempts": [], "evidence": [], "locks": {}, "pipeline_changes": [], "adaptive_events": [], "recovery_events": [], "resume_events": [], "reassessment_receipts": [], "documentation_receipt": None, "manifest_receipts": [], "classification_receipt": classification_id, "handoff_created": False, "replan_count": 0, "replan_limit": int(params.get("replan_limit", 2)), "require_delegation": classification["complexity"] in {"C2", "C3"}, "require_handoff": classification["complexity"] in {"C2", "C3"}, "coordinator": activation["coordinator"], "parent_project_operations": activation["parent_project_operations"], "worker_visibility": activation["worker_visibility"], "worker_return_route": activation["worker_return_route"], "revision": 0, "updated_at": now()}
        write_json(task_dir / "task.json", task)
        write_json(task_dir / "baseline-manifest.json", baseline)
        write_json(state_path, state)
        write_json(task_dir / "metrics.json", {"schema": SCHEMA, "task_id": task_id, "gate_outcomes": [], "evidence_count": 0, "delegation_count": 0, "retries": 0, "gate_recovery_failures": 0, "telemetry": []})
        report_paths = report_bus_paths(task_dir)
        write_json(report_paths["index"], {"schema": REPORT_SCHEMA, "task_id": task_id, "reports": [], "submissions": {}, "updated_at": now()})
        index = read_task_index(root)
        index[task_id] = {"number": task_number, "directory": task_dir.name}
        write_json(task_index_path(root), index)
        if thread_id:
            index_path = root / "active-tasks.json"
            index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
            index[thread_id] = task_id
            write_json(index_path, index)
        activation_file = activation_path(root)
        activations = json.loads(activation_file.read_text(encoding="utf-8"))
        activation_id = activation_key(params)
        current_activation = activations.get(activation_id)
        if not isinstance(current_activation, dict) or current_activation.get("schema") != SCHEMA:
            raise ValueError("orchestration activation disappeared during task initialization")
        current_activation["task_id"] = task_id
        current_activation["initialized_at"] = now()
        activations[activation_id] = current_activation
        write_json(activation_file, activations)
        append_journal_best_effort(task_dir, "initialized", f"{classification['complexity']} pipeline: {', '.join(pipeline)}")
        receipt["consumed_by"] = task_id
        receipt["consumed_at"] = now()
        write_json(receipt_path, receipt)
        return {"created": True, "task_id": task_id, "task_number": task_number, "task_directory": task_dir.name, "state": state, "classification": classification, "pipeline_correction": pipeline_correction, "ledger_root": str(root)}


def status(params: dict[str, Any]) -> dict[str, Any]:
    root, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    receipt_id = f"status-{secrets.token_hex(12)}"
    receipt = {"status_receipt": receipt_id, "task_id": state["task_id"], "principal": state.get("principal", "local"), "thread_id": state.get("thread_id", ""), "revision": state["revision"], "created_at": now()}
    write_json(task_dir / "status-receipts" / f"{receipt_id}.json", receipt)
    active = activation_record(root, {"thread_id": state.get("thread_id"), "principal": state.get("principal")}, state["task_id"])
    return {"task": task, "state": state, "active": bool(active), "status_receipt": receipt_id, "ledger_root": str(root)}


def _attempt(state: dict[str, Any], attempt_id: str) -> dict[str, Any]:
    attempt = next((item for item in state.get("attempts", []) if item.get("attempt_id") == attempt_id), None)
    if not attempt:
        raise ValueError("attempt_id does not belong to this task")
    return attempt


def host_spawn_prompt(agent: str, package: dict[str, Any]) -> str:
    """Build the exact bounded briefing for Codex's native spawn_agent tool."""
    profile = PROFILES[agent]
    profile_path = _contained_path(
        PLUGIN_ROOT / "agents",
        PLUGIN_ROOT / "agents" / str(profile["filename"]),
        "agent profile",
    )
    try:
        profile_data = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"agent profile is unreadable: {agent}") from exc
    instructions = str(profile_data.get("developer_instructions", "")).strip()
    if not instructions:
        raise ValueError(f"agent profile has no developer instructions: {agent}")
    return "\n".join((
        f"You are the internal Cortex worker with profile `{agent}`.",
        "Follow this profile exactly:",
        instructions,
        "",
        f"Cortex task: {package['task_id']}; gate: {package['gate']}; attempt: {package['attempt_id']}.",
        f"When calling Cortex MCP tools, use project_root={package.get('project_root') or '(the coordinator-provided project root)'!r}, "
        f"principal={package.get('coordinator_principal')!r}, and thread_id={package.get('coordinator_thread_id')!r}. "
        "Do not substitute the worker profile name for the coordinator principal.",
        f"Objective: {package['objective']}",
        f"Ownership: {package['ownership']}",
        "Allowed paths: " + ", ".join(package["allowed_paths"]),
        "Acceptance criteria: " + "; ".join(package["acceptance_criteria"]),
        "Verification: " + "; ".join(package["verification"]),
        "Internal worker protocol: English only. Write worker-to-worker messages, Cortex tool arguments, reports, findings, questions, and handoffs in English. Do not expose internal worker text directly to the user.",
        f"User-facing language: {package.get('user_language', 'en')}. The main coordinator must translate questions, blockers, and summaries into this language (or the explicit language requested by the user) before showing them in the main chat.",
        "Codebase-memory protocol (mandatory for code search): 1) call mcp__codebase_memory__list_projects with {}; 2) parse the projects array and select the record whose root_path exactly matches the absolute project_root; 3) pass that record's name as project to every subsequent codebase_memory call—never guess or synthesize a project id. Use index_status(project) when freshness/index availability must be verified. For discovery use search_graph(project, query=...); use name_pattern for exact symbol patterns, semantic_query only as an array of keywords on moderate/full indexes, and filters such as label, file_pattern, relationship, include_connected, limit, and offset when needed. For text matches use search_code(project, pattern, regex, mode=compact|full|files, path_filter, file_pattern, context, limit). For callers/dependencies/data flow use trace_path(project, function_name=<qualified_name from search_graph>, mode=calls|data_flow|cross_service, direction, depth, include_tests, parameter_name, risk_labels). For source reading use get_code_snippet(project, qualified_name=<exact qualified_name from search_graph>, include_neighbors); do not use it as the initial search. Use get_architecture(project, aspects) for a high-level structure overview and query_graph(project, query, max_rows) only for explicit multi-hop Cypher analysis. Do not call index_repository, ingest_traces, manage_adr, or delete_project for ordinary discovery; they change indexed state or durable knowledge and require explicit authorization. Do not start with grep, rg, glob, or ad-hoc filesystem scans while codebase_memory is available. If list_projects fails, codebase_memory is unavailable, or no indexed project matches project_root, do not call other codebase_memory tools or pretend they were used: record the limitation and use another search method only as a documented fallback.",
        "Do not subdelegate or communicate with the user. Return questions, blockers, and your final handoff to the main chat. "
        "Before finishing, publish exactly one cortex/report/v1 report for this attempt. "
        f"Use attempt_id={package['attempt_id']!r} exactly and a stable lowercase submission_id such as "
        f"{package['attempt_id']}-report-1; never substitute the profile name for the attempt id. "
        "If a requirement, branch, trade-off, missing fact, or implementation choice needs user approval, "
        "do not decide silently: call cortex.question with this task_id, the coordinator principal, "
        f"attempt_id={package['attempt_id']!r}, and a stable lowercase submission_id such as "
        f"{package['attempt_id']}-question-1. Include concrete options when useful, set multiple=true "
        "only when more than one option may be selected, and explain the decision in context. "
        "The worker call records a pending question; it must not open a worker-local UI. "
        "The coordinator will surface the question in the main chat, answer it, and you must poll "
        "get_worker_question_updates before continuing. Never choose a user decision on the user's behalf. "
        "If a Cortex call returns an error, preserve the exact error in report.questions or report.findings, "
        "then retry only with the returned correction fields and the same submission_id.",
    ))


def record_report(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        raw_attempt_id = str(params.get("attempt_id") or "").strip()
        candidate_attempt_id = safe_id(raw_attempt_id) if raw_attempt_id else ""
        supplied_identity = str(params.get("principal") or params.get("thread_id") or "").strip()
        identity_candidates = []
        if supplied_identity:
            for item in state.get("attempts", []):
                if item.get("invalidated") or item.get("status") not in {"running", AWAITING_HOST_SPAWN}:
                    continue
                aliases = {
                    str(item.get("agent") or "").strip(),
                    str(item.get("profile") or "").strip(),
                    str(item.get("display_name") or "").strip(),
                    str((item.get("spawn_request") or {}).get("task_name") or "").strip(),
                }
                if supplied_identity in aliases:
                    identity_candidates.append(item)
        principal_correction = None
        try:
            authorize(state, params)
        except ValueError as exc:
            candidate_attempt = _attempt(state, candidate_attempt_id) if candidate_attempt_id else None
            if not candidate_attempt and len(identity_candidates) == 1:
                candidate_attempt = identity_candidates[0]
                candidate_attempt_id = candidate_attempt["attempt_id"]
            worker_aliases = set()
            if candidate_attempt:
                worker_aliases.update({
                    str(candidate_attempt.get("agent") or "").strip(),
                    str(candidate_attempt.get("profile") or "").strip(),
                    str(candidate_attempt.get("display_name") or "").strip(),
                    str((candidate_attempt.get("spawn_request") or {}).get("task_name") or "").strip(),
                })
            if "different principal" not in str(exc) or not supplied_identity or not identity_candidates:
                raise
            if candidate_attempt and supplied_identity not in worker_aliases:
                raise
            # A native worker may identify itself by its exact canonical
            # profile.  It can publish only for its own active attempt; all
            # task mutations remain bound to the coordinator principal.
            authorize(state, {
                "principal": state.get("principal"),
                "thread_id": state.get("thread_id"),
                "project_root": str(select_project_root(params)),
            })
            principal_correction = {"requested": supplied_identity, "used": state.get("principal")}
        preflight_journal(task_dir)
        current_wave = active_gates(state)
        if not candidate_attempt_id:
            eligible = [
                item for item in state.get("attempts", [])
                if item.get("gate") in current_wave
                and item.get("status") in {"running", AWAITING_HOST_SPAWN}
                and not item.get("invalidated")
            ]
            if len(identity_candidates) > 1:
                return {
                    "recorded": False,
                    "reason": "delegation_attempt_required",
                    "candidate_attempt_ids": [item["attempt_id"] for item in identity_candidates],
                    "next_action": "retry_record_report_with_attempt_id",
                    "recoverable": True,
                    "principal_correction": principal_correction,
                    "state": state,
                }
            if len(identity_candidates) == 1:
                candidate_attempt_id = identity_candidates[0]["attempt_id"]
            elif len(eligible) == 1:
                candidate_attempt_id = eligible[0]["attempt_id"]
            else:
                return {
                    "recorded": False,
                    "reason": "delegation_attempt_required",
                    "candidate_attempt_ids": [item["attempt_id"] for item in identity_candidates or eligible],
                    "next_action": "retry_record_report_with_attempt_id",
                    "recoverable": True,
                    "principal_correction": principal_correction,
                    "state": state,
                }
        attempt_id = safe_id(candidate_attempt_id)
        attempt = _attempt(state, attempt_id)
        host_confirmation_pending = attempt.get("status") == AWAITING_HOST_SPAWN
        if attempt.get("invalidated") or attempt.get("status") not in {"running", AWAITING_HOST_SPAWN}:
            raise ValueError("cannot publish a report for an invalidated or terminal attempt")
        report = sanitize_report_payload(params.get("report"))
        content_digest = digest_text(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        raw_submission_id = str(params.get("submission_id") or "").strip()
        submission_id = safe_id(raw_submission_id) if raw_submission_id else f"submission-{attempt_id}-report-{content_digest[:16]}"
        paths = report_bus_paths(task_dir)
        index = _report_index(paths, state["task_id"])
        submission_key = f"{attempt_id}:{submission_id}"
        authoritative: list[dict[str, Any]] = []
        authoritative_numbers: list[int] = []
        for record_path in sorted(paths["records"].glob("report-*.json")):
            if record_path.is_symlink() or not (match := re.fullmatch(r"report-(\d+)\.json", record_path.name)):
                raise ValueError("report record namespace contains an unsafe entry")
            authoritative_numbers.append(int(match.group(1)))
            authoritative.append(_read_private_json(record_path, "report record"))
        existing = next((item for item in authoritative if f"{item.get('attempt_id')}:{item.get('submission_id')}" == submission_key), None)
        existing_id = existing.get("report_id") if existing else None
        if existing_id:
            existing_path = _contained_path(paths["records"], paths["records"] / f"{safe_id(existing_id)}.json", "report record")
            existing = _read_private_json(existing_path, "report record")
            if existing.get("content_digest") != content_digest:
                raise ValueError("idempotent report submission_id was reused with different content")
            attempt = _attempt(state, safe_id(str(existing["attempt_id"])))
            receipt, _ = _recover_report_receipt(paths, existing, state, bool(attempt.get("invalidated")))
            markdown_path = paths["markdown"] / f"{existing_id}.md"
            if not markdown_path.exists():
                write_text_exclusive(markdown_path, _report_markdown(existing))
            return {"idempotent": True, "report": existing, "receipt": receipt, "host_confirmation_pending": host_confirmation_pending, "principal_correction": principal_correction, "state": state}
        attempt_count = sum(1 for item in authoritative if item.get("attempt_id") == attempt_id)
        aggregate_bytes = sum(len(json.dumps(item.get("report", {}), ensure_ascii=False, sort_keys=True).encode("utf-8")) for item in authoritative)
        report_bytes = len(json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if attempt_count >= MAX_REPORTS_PER_ATTEMPT or len(authoritative) >= MAX_REPORTS_PER_TASK:
            raise ValueError("report count quota exhausted")
        if aggregate_bytes + report_bytes > MAX_REPORT_AGGREGATE_BYTES:
            raise ValueError("report aggregate byte quota exhausted")
        report_id = f"report-{max(authoritative_numbers, default=0) + 1:04d}"
        record = {
            "schema": REPORT_SCHEMA, "report_id": report_id, "task_id": state["task_id"],
            "gate": attempt["gate"], "attempt_id": attempt_id, "submission_id": submission_id,
            "producer": {"profile": attempt["profile"], "model": attempt["selected_model"], "reasoning_effort": attempt["selected_reasoning_effort"]},
            "report": report, "content_digest": content_digest, "created_at": now(),
        }
        receipt = {
            "schema": REPORT_SCHEMA, "receipt_id": f"report-receipt-{report_id}", "report_id": report_id,
            "task_id": state["task_id"], "gate": attempt["gate"], "attempt_id": attempt_id,
            "content_digest": content_digest, "consumed_at": None, "consumed_by_evidence_id": None,
            "invalidated": False, "created_at": now(),
        }
        write_json_exclusive(paths["records"] / f"{report_id}.json", record)
        write_text_exclusive(paths["markdown"] / f"{report_id}.md", _report_markdown(record))
        write_json_exclusive(paths["receipts"] / f"{receipt['receipt_id']}.json", receipt)
        index.setdefault("reports", []).append(_report_metadata(record))
        index.setdefault("submissions", {})[submission_key] = report_id
        index["updated_at"] = now()
        write_json(paths["index"], index)
        delegation_path, delegation_index = _delegation_report_index(paths, state["task_id"], attempt_id)
        delegation_index["owned_report_ids"] = sorted(set(delegation_index.get("owned_report_ids", [])) | {report_id})
        delegation_index["updated_at"] = now()
        write_json(delegation_path, delegation_index)
        append_journal_best_effort(task_dir, "report", f"{attempt_id} published {report_id}")
        return {"idempotent": False, "report": record, "receipt": receipt, "host_confirmation_pending": host_confirmation_pending, "principal_correction": principal_correction, "state": state}


def list_task_reports(params: dict[str, Any]) -> dict[str, Any]:
    _, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    index = _report_index(report_bus_paths(task_dir), state["task_id"])
    return {"schema": REPORT_SCHEMA, "task_id": state["task_id"], "reports": index.get("reports", [])}


def _grant_reports_locked(task_dir: Path, state: dict[str, Any], attempt_id: str, report_ids: list[Any], reason: object) -> dict[str, Any]:
    target = _attempt(state, safe_id(attempt_id))
    paths = report_bus_paths(task_dir)
    index = _report_index(paths, state["task_id"])
    available = {item["report_id"] for item in index.get("reports", [])}
    requested = [safe_id(str(item)) for item in report_ids]
    if len(requested) != len(set(requested)):
        raise ValueError("context_report_ids must be unique")
    missing = sorted(set(requested) - available)
    if missing:
        raise ValueError("context reports do not belong to this task: " + ", ".join(missing))
    if len(requested) > MAX_REPORTS_PER_TASK:
        raise ValueError("report grant request exceeds bounded capacity")
    grant_numbers = []
    for path in paths["grants"].glob("grant-*.json"):
        match = re.fullmatch(r"grant-(\d+)\.json", path.name)
        if path.is_symlink() or not match:
            raise ValueError("report grant namespace contains an unsafe entry")
        grant_numbers.append(int(match.group(1)))
    if len(grant_numbers) >= MAX_REPORT_GRANTS:
        raise ValueError("report grant quota exhausted")
    grant_id = f"grant-{max(grant_numbers, default=0) + 1:04d}"
    grant = {"schema": REPORT_SCHEMA, "grant_id": grant_id, "task_id": state["task_id"], "attempt_id": target["attempt_id"], "report_ids": requested, "reason": redact(reason, 2000), "created_at": now()}
    write_json_exclusive(paths["grants"] / f"{grant_id}.json", grant)
    delegation_path, delegation_index = _delegation_report_index(paths, state["task_id"], target["attempt_id"])
    delegation_index["context_report_ids"] = sorted(set(delegation_index.get("context_report_ids", [])) | set(requested))
    delegation_index["grant_ids"] = sorted(set(delegation_index.get("grant_ids", [])) | {grant_id})
    delegation_index["updated_at"] = now()
    write_json(delegation_path, delegation_index)
    package_path = task_dir / "delegations" / f"{target['attempt_id']}.json"
    package = _read_private_json(package_path, "delegation package")
    package["context_report_ids"] = delegation_index["context_report_ids"]
    package["report_index"] = "reports/index.json"
    write_json(package_path, package)
    return grant


def grant_report_context(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        preflight_journal(task_dir)
        grant = _grant_reports_locked(task_dir, state, str(params.get("attempt_id", "")), params.get("report_ids", []), params.get("reason", ""))
        append_journal_best_effort(task_dir, "report_context", f"{grant['attempt_id']} received {len(grant['report_ids'])} report(s)")
        return {"grant": grant, "state": state}


def get_delegation_reports(params: dict[str, Any]) -> dict[str, Any]:
    _, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    attempt_id = safe_id(str(params.get("attempt_id", "")))
    _attempt(state, attempt_id)
    paths = report_bus_paths(task_dir)
    _, delegation_index = _delegation_report_index(paths, state["task_id"], attempt_id)
    allowed = set(delegation_index.get("owned_report_ids", [])) | set(delegation_index.get("context_report_ids", []))
    requested = [safe_id(str(item)) for item in params.get("report_ids", [])]
    if not requested:
        raise ValueError("get_delegation_reports requires explicit report_ids")
    denied = sorted(set(requested) - allowed)
    if denied:
        raise ValueError("delegation is not granted the requested report bodies: " + ", ".join(denied))
    reports = []
    for report_id in requested:
        record = _read_private_json(_contained_path(paths["records"], paths["records"] / f"{report_id}.json", "report record"), "report record")
        if record.get("task_id") != state["task_id"]:
            raise ValueError("report record crosses task scope")
        # A coordinator completing the producer attempt needs the one-use
        # receipt, but a downstream context grant must not transfer that
        # capability.  Return it only when this attempt owns the report and
        # validate the durable binding before exposing it.
        if record.get("attempt_id") == attempt_id:
            receipt_id = f"report-receipt-{report_id}"
            receipt = _read_private_json(
                _contained_path(paths["receipts"], paths["receipts"] / f"{receipt_id}.json", "report receipt"),
                "report receipt",
            )
            if (
                receipt.get("schema") != REPORT_SCHEMA
                or receipt.get("task_id") != state["task_id"]
                or receipt.get("report_id") != report_id
                or receipt.get("attempt_id") != attempt_id
                or receipt.get("gate") != record.get("gate")
            ):
                raise ValueError("report receipt does not match its owned report")
            record = {**record, "receipt": receipt}
        reports.append(record)
    return {"schema": REPORT_SCHEMA, "task_id": state["task_id"], "attempt_id": attempt_id, "reports": reports}


def publish_worker_question(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        preflight_journal(task_dir)
        attempt_id = safe_id(str(params.get("attempt_id", "")))
        attempt = _attempt(state, attempt_id)
        if attempt.get("invalidated") or attempt.get("status") != "running":
            raise ValueError("cannot publish a question for an invalidated or terminal attempt")
        submission_id = safe_id(str(params.get("submission_id", "")))
        question, context, blocking, config, content_digest = _question_payload(params)
        paths = question_bus_paths(task_dir)
        records = _question_records(paths, state)
        existing = next(
            (item for item in records if item.get("attempt_id") == attempt_id and item.get("submission_id") == submission_id),
            None,
        )
        if existing is not None:
            if existing.get("content_digest") != content_digest:
                raise ValueError("idempotent question submission_id was reused with different content")
            return {"idempotent": True, "question": existing, "cursor": _question_sequence(records)}
        if len(records) >= MAX_QUESTIONS_PER_TASK or sum(item.get("attempt_id") == attempt_id for item in records) >= MAX_QUESTIONS_PER_ATTEMPT:
            raise ValueError("question count quota exhausted")
        numbers = [int(str(item["question_id"]).removeprefix("question-")) for item in records]
        question_id = f"question-{max(numbers, default=0) + 1:04d}"
        sequence = _question_sequence(records) + 1
        record = {
            "schema": QUESTION_SCHEMA,
            "question_id": question_id,
            "task_id": state["task_id"],
            "gate": attempt["gate"],
            "attempt_id": attempt_id,
            "submission_id": submission_id,
            "profile": attempt["profile"],
            "question": question,
            "context": context,
            "blocking": blocking,
            "header": config["header"],
            "options": config["options"],
            "multiple": config["multiple"],
            "custom_label": config["custom_label"],
            "custom_response": True,
            "status": "open",
            "content_digest": content_digest,
            "published_sequence": sequence,
            "answer": None,
            "answer_text": None,
            "resume_context": None,
            "answer_submission_id": None,
            "answer_digest": None,
            "answered_sequence": None,
            "created_at": now(),
            "answered_at": None,
        }
        write_json_exclusive(paths["records"] / f"{question_id}.json", record)
        append_journal_best_effort(task_dir, "worker_question", f"{attempt_id} published {question_id}")
        return {"idempotent": False, "question": record, "cursor": sequence}


def _question_record_view(record: dict[str, Any]) -> dict[str, Any]:
    """Return a forward-compatible question record for old v1 ledgers."""
    view = dict(record)
    view.setdefault("header", "Question")
    view.setdefault("options", [])
    view.setdefault("multiple", False)
    view.setdefault("custom_label", "Your answer / additional context")
    view.setdefault("custom_response", True)
    view.setdefault("answer_text", None)
    return view


def _normalize_question_answer(value: object) -> tuple[Any, str]:
    """Keep structured host extensions (for example image attachments) intact."""
    if isinstance(value, (dict, list)):
        answer = sanitize_structured(value)
        answer_text = redact(json.dumps(answer, ensure_ascii=False, sort_keys=True), 8000)
    else:
        answer = redact(str(value or "").strip(), 8000)
        answer_text = answer
    return answer, answer_text


def list_worker_questions(params: dict[str, Any]) -> dict[str, Any]:
    _, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    all_records = _question_records(question_bus_paths(task_dir), state)
    records = list(all_records)
    attempt_id = str(params.get("attempt_id", "")).strip()
    if attempt_id:
        attempt_id = safe_id(attempt_id)
        _attempt(state, attempt_id)
        records = [item for item in records if item["attempt_id"] == attempt_id]
    requested_status = str(params.get("status", "")).strip()
    if requested_status:
        records = [item for item in records if item["status"] == requested_status]
    return {
        "schema": QUESTION_SCHEMA,
        "task_id": state["task_id"],
        "questions": [_question_record_view(item) for item in records],
        "cursor": _question_sequence(all_records),
        "open_count": sum(item.get("status") == "open" for item in all_records),
        "open_question_ids": [item["question_id"] for item in all_records if item.get("status") == "open"],
        "next_action": "answer each open question in published_sequence order; do not choose on the user's behalf" if any(item.get("status") == "open" for item in all_records) else "continue worker monitoring",
    }


def answer_worker_question(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        preflight_journal(task_dir)
        question_id = safe_id(str(params.get("question_id", "")))
        submission_id = safe_id(str(params.get("submission_id", "")))
        answer, answer_text = _normalize_question_answer(params.get("answer"))
        resume_context = sanitize_structured(params.get("resume_context"))
        if not answer_text:
            raise ValueError("worker question answer is required")
        if resume_context in (None, "", [], {}):
            raise ValueError("worker question resume_context is required")
        answer_digest = digest_text(json.dumps({"answer": answer, "resume_context": resume_context}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        paths = question_bus_paths(task_dir)
        records = _question_records(paths, state)
        record = next((item for item in records if item.get("question_id") == question_id), None)
        if record is None:
            raise ValueError("question_id does not belong to this task")
        if record.get("status") == "answered":
            if record.get("answer_submission_id") != submission_id:
                raise ValueError("worker question has already been answered")
            if record.get("answer_digest") != answer_digest:
                raise ValueError("idempotent answer submission_id was reused with different content")
            return {"idempotent": True, "question": record, "cursor": _question_sequence(records)}
        record.update({
            "status": "answered",
            "answer": answer,
            "answer_text": answer_text,
            "resume_context": resume_context,
            "answer_submission_id": submission_id,
            "answer_digest": answer_digest,
            "answered_sequence": _question_sequence(records) + 1,
            "answered_at": now(),
        })
        write_json(paths["records"] / f"{question_id}.json", record)
        append_journal_best_effort(task_dir, "worker_answer", f"{question_id} answered for {record['attempt_id']}")
        return {"idempotent": False, "question": record, "cursor": record["answered_sequence"]}


def _question_form_schema(config: dict[str, Any]) -> dict[str, Any]:
    """Build a native MCP form with optional single/multi-select and a final free-form field."""
    properties: dict[str, Any] = {}
    options = list(config.get("options") or [])
    if options:
        titled_options = [{"const": item["label"], "title": item["description"]} for item in options]
        if config.get("multiple"):
            properties["selections"] = {
                "type": "array",
                "title": config.get("header") or "Select all that apply",
                "items": {"anyOf": titled_options},
            }
        else:
            properties["selection"] = {
                "type": "string",
                "title": config.get("header") or "Select one",
                "oneOf": titled_options,
            }
    properties["custom_response"] = {
        "type": "string",
        "title": config.get("custom_label") or "Your answer / additional context",
        "description": "Optional free-form response. Add context, paste a screenshot/path, or explain another choice.",
    }
    return {"type": "object", "properties": properties}


def _request_mcp_elicitation(message: str, requested_schema: dict[str, Any], *, thread_id: str = "", turn_id: str = "") -> tuple[str, dict[str, Any] | None, str]:
    """Ask the Codex host to render its native MCP elicitation UI."""
    request_id = f"cortex-question-{secrets.token_hex(12)}"
    respond({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "elicitation/create",
        "params": {
            "message": message,
            # Codex's OpenAI extension can render richer form fallbacks (such
            # as attachment-capable free-form input). Use it only when the
            # connected host advertised the extension; otherwise remain
            # standards-compliant with MCP form mode.
            "mode": "openai/form" if MCP_OPENAI_FORM else "form",
            "requestedSchema": requested_schema,
            "_meta": {"cortex": {"schema": QUESTION_SCHEMA, "thread_id": thread_id, "turn_id": turn_id or None}},
        },
    })
    while True:
        line = sys.stdin.readline()
        if not line:
            raise RuntimeError("MCP client closed before answering cortex.question")
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            continue
        if response.get("id") != request_id:
            if response.get("id") is not None and response.get("method"):
                respond({"jsonrpc": "2.0", "id": response.get("id"), "error": {"code": -32601, "message": "Cortex is waiting for the active user question"}})
            continue
        if "error" in response:
            error = response.get("error") or {}
            raise RuntimeError(redact(str(error.get("message") or "MCP elicitation was rejected"), 1000))
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("MCP elicitation returned an invalid response")
        action = str(result.get("action") or "cancel").strip().lower()
        content = result.get("content") if isinstance(result.get("content"), dict) else None
        return action, content, request_id


def _question_answer_from_content(content: dict[str, Any] | None, config: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    content = content or {}
    options = {item["label"] for item in config.get("options") or []}
    multiple = bool(config.get("multiple"))
    if multiple:
        raw_selections = content.get("selections", [])
        selections = raw_selections if isinstance(raw_selections, list) else [raw_selections]
        selections = [redact(item, 200) for item in selections if str(item).strip()]
        if options and any(item not in options for item in selections):
            raise ValueError("MCP elicitation returned an unknown question option")
    else:
        raw_selection = content.get("selection")
        selections = [redact(raw_selection, 200)] if raw_selection not in (None, "") else []
        if options and selections and selections[0] not in options:
            raise ValueError("MCP elicitation returned an unknown question option")
    custom = content.get("custom_response", "")
    normalized_custom, custom_text = _normalize_question_answer(custom)
    if not selections and not custom_text:
        return None, ""
    answer: dict[str, Any] = {
        "selections": selections if multiple else (selections[0] if selections else None),
        "custom_response": normalized_custom,
    }
    extras = {key: value for key, value in content.items() if key not in {"selection", "selections", "custom_response"}}
    if extras:
        answer["host_fields"] = sanitize_structured(extras)
    return answer, redact(json.dumps(answer, ensure_ascii=False, sort_keys=True), 8000)


def _question_record_for_main(params: dict[str, Any], question_id: str) -> dict[str, Any]:
    listed = list_worker_questions({"task_id": params["task_id"], "principal": params["principal"], "thread_id": params.get("thread_id"), "project_root": params.get("project_root")})
    record = next((item for item in listed["questions"] if item.get("question_id") == question_id), None)
    if record is None:
        raise ValueError("question_id does not belong to this task")
    return _question_record_view(record)


def _localized_question_view(record: dict[str, Any], params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Allow the coordinator to localize only the user-facing projection."""
    question = redact(params.get("localized_question") or record["question"], 4000)
    config = _question_config(record)
    if params.get("localized_header"):
        config["header"] = redact(params["localized_header"], 200)
    if isinstance(params.get("localized_options"), list):
        config["options"] = _question_options(params["localized_options"])
    if params.get("localized_custom_label"):
        config["custom_label"] = redact(params["localized_custom_label"], 200)
    return question, config


def cortex_question(params: dict[str, Any]) -> dict[str, Any]:
    """Route worker questions to the coordinator and render main-chat UI questions."""
    task_id = str(params.get("task_id") or "").strip()
    principal = str(params.get("principal") or "").strip()
    question_id = str(params.get("question_id") or "").strip()
    question = redact(str(params.get("question") or "").strip(), 4000)
    if not task_id or not principal or (not question and not question_id):
        raise ValueError("cortex.question requires task_id, principal, and question or question_id")

    durable: dict[str, Any] | None = None
    if question_id:
        question_id = safe_id(question_id)
        record = _question_record_for_main(params, question_id)
        question, config = _localized_question_view(record, params)
        durable = {"question": record}
    else:
        config = _question_config(params)
        attempt_id = str(params.get("attempt_id") or "").strip()
        if attempt_id:
            attempt_id = safe_id(attempt_id)
            submission_id = str(params.get("submission_id") or "").strip()
            if not submission_id:
                submission_id = f"question-{attempt_id}-{digest_text(question)[:12]}"
            durable = publish_worker_question({
                **params,
                "attempt_id": attempt_id,
                "submission_id": submission_id,
                "question": question,
                "context": {**(params.get("context") if isinstance(params.get("context"), dict) else {}), "ui": config},
            })
            return {
                "schema": QUESTION_SCHEMA,
                "status": "pending_user_input",
                "question_id": durable["question"]["question_id"],
                "question": question,
                "ui": config,
                "next_action": "coordinator must list_worker_questions and invoke cortex.question in the main chat with this question_id",
                "recoverable": True,
                "durable": durable,
            }

    if not bool(params.get("interactive", True)):
        return {
            "schema": QUESTION_SCHEMA,
            "status": "pending_user_input",
            "question_id": (durable or {}).get("question", {}).get("question_id"),
            "question": question,
            "ui": config,
            "next_action": "invoke cortex.question with interactive=true from the main Codex chat",
            "recoverable": True,
            "durable": durable,
        }
    try:
        action, content, elicitation_id = _request_mcp_elicitation(
            question,
            _question_form_schema(config),
            thread_id=str(params.get("thread_id") or ""),
            turn_id=str(params.get("turn_id") or ""),
        )
    except RuntimeError as exc:
        return {
            "schema": QUESTION_SCHEMA,
            "status": "elicitation_unavailable",
            "question_id": (durable or {}).get("question", {}).get("question_id"),
            "question": question,
            "error": redact(str(exc), 1000),
            "next_action": "surface this question with a host-native user-input UI or retry from the main chat",
            "recoverable": True,
            "durable": durable,
        }
    if action != "accept":
        return {
            "schema": QUESTION_SCHEMA,
            "status": action if action in {"decline", "cancel"} else "cancel",
            "question_id": (durable or {}).get("question", {}).get("question_id"),
            "question": question,
            "elicitation_id": elicitation_id,
            "answer": None,
            "durable": durable,
        }
    try:
        answer, answer_text = _question_answer_from_content(content, config)
    except ValueError as exc:
        return {"schema": QUESTION_SCHEMA, "status": "invalid_answer", "question": question, "error": str(exc), "recoverable": True, "durable": durable}
    if answer is None:
        return {
            "schema": QUESTION_SCHEMA,
            "status": "invalid_answer",
            "question_id": (durable or {}).get("question", {}).get("question_id"),
            "question": question,
            "elicitation_id": elicitation_id,
            "next_action": "retry cortex.question and choose an option or enter a custom response",
            "recoverable": True,
            "durable": durable,
        }
    answered = None
    if question_id:
        answer_submission_id = str(params.get("answer_submission_id") or "").strip()
        if not answer_submission_id:
            answer_submission_id = f"answer-{question_id}-{digest_text(answer_text)[:16]}"
        answered = answer_worker_question({
            **params,
            "question_id": question_id,
            "submission_id": safe_id(answer_submission_id),
            "answer": answer,
            "resume_context": {"source": "cortex.question", "elicitation_id": elicitation_id, "ui": config},
        })
    return {
        "schema": QUESTION_SCHEMA,
        "status": "answered",
        "question_id": question_id or (durable or {}).get("question", {}).get("question_id"),
        "question": question,
        "elicitation_id": elicitation_id,
        "answer": answer,
        "answer_text": answer_text,
        "durable": answered or durable,
    }


def get_worker_question_updates(params: dict[str, Any]) -> dict[str, Any]:
    _, task_dir, state = load_state(str(params["task_id"]), params)
    authorize_principal(state, params)
    attempt_id = safe_id(str(params.get("attempt_id", "")))
    _attempt(state, attempt_id)
    after_sequence = int(params.get("after_sequence", 0))
    if after_sequence < 0:
        raise ValueError("after_sequence must be nonnegative")
    records = _question_records(question_bus_paths(task_dir), state)
    attempt_records = [item for item in records if item["attempt_id"] == attempt_id]
    updates = []
    for record in attempt_records:
        if int(record["published_sequence"]) > after_sequence:
            updates.append({
                "sequence": record["published_sequence"],
                "kind": "question_published",
                "question_id": record["question_id"],
                "status": record["status"],
                "created_at": record["created_at"],
            })
        if record.get("answered_sequence") and int(record["answered_sequence"]) > after_sequence:
            updates.append({
                "sequence": record["answered_sequence"],
                "kind": "question_answered",
                "question_id": record["question_id"],
                "answer": record["answer"],
                "answer_text": record.get("answer_text"),
                "resume_context": record["resume_context"],
                "answered_at": record["answered_at"],
            })
    updates.sort(key=lambda item: int(item["sequence"]))
    return {
        "schema": QUESTION_SCHEMA,
        "task_id": state["task_id"],
        "attempt_id": attempt_id,
        "after_sequence": after_sequence,
        "updates": updates,
        "next_sequence": _question_sequence(attempt_records),
    }


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
        for path in sorted(paths["records"].glob("report-*.json")):
            if path.is_symlink():
                raise ValueError("report record must not be a symlink")
            record = _read_private_json(path, "report record")
            report_id = safe_id(str(record.get("report_id", "")))
            attempt = _attempt(state, safe_id(str(record.get("attempt_id", ""))))
            sanitized = sanitize_report_payload(record.get("report"))
            digest = digest_text(json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            if record.get("schema") != REPORT_SCHEMA or record.get("task_id") != state["task_id"] or report_id + ".json" != path.name or record.get("gate") != attempt.get("gate") or record.get("content_digest") != digest:
                raise ValueError(f"report record failed reconciliation: {path.name}")
            records.append(_report_metadata(record))
            submissions[f"{record['attempt_id']}:{safe_id(str(record['submission_id']))}"] = report_id
            by_attempt.setdefault(record["attempt_id"], []).append(report_id)
            if len(records) > MAX_REPORTS_PER_TASK or len(by_attempt[record["attempt_id"]]) > MAX_REPORTS_PER_ATTEMPT:
                raise ValueError("authoritative reports exceed count quota")
            if sum(len(json.dumps(item.get("report", {}), ensure_ascii=False, sort_keys=True).encode("utf-8")) for item in [_read_private_json(item, "report record") for item in paths["records"].glob("report-*.json")]) > MAX_REPORT_AGGREGATE_BYTES:
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
        write_json(paths["index"], {"schema": REPORT_SCHEMA, "task_id": state["task_id"], "reports": records, "submissions": submissions, "updated_at": now()})
        for attempt in state.get("attempts", []):
            delegation_path, delegation_index = _delegation_report_index(paths, state["task_id"], attempt["attempt_id"])
            delegation_index["owned_report_ids"] = sorted(by_attempt.get(attempt["attempt_id"], []))
            delegation_index["updated_at"] = now()
            write_json(delegation_path, delegation_index)
        append_journal_best_effort(task_dir, "report_reconcile", f"{len(records)} record(s); {len(repaired)} repair(s)")
        return {"schema": REPORT_SCHEMA, "task_id": state["task_id"], "report_count": len(records), "repaired": repaired, "state": state}


def record_delegation(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        requested_revision = params.get("expected_revision")
        revision_correction = (
            {"requested": requested_revision, "used": state["revision"]}
            if requested_revision is not None and requested_revision != state["revision"] else None
        )
        raw_status_receipt = str(params.get("status_receipt", "")).strip()
        status_receipt = safe_id(raw_status_receipt) if raw_status_receipt else ""
        status_path = task_dir / "status-receipts" / f"{status_receipt}.json" if status_receipt else None
        observed = None
        if status_path is not None and status_path.exists() and not status_path.is_symlink():
            candidate = json.loads(status_path.read_text(encoding="utf-8"))
            if candidate.get("task_id") == state["task_id"] and candidate.get("principal") == state.get("principal") and candidate.get("revision") == state["revision"] and not candidate.get("consumed_at"):
                observed = candidate
        receipt_correction = observed is None
        wave = active_gates(state)
        gate = str(state["current_gate"])
        requested_gate = str(params.get("gate") or gate)
        if requested_gate in wave:
            gate = requested_gate
        default_agents = {
            "plan": "planner", "discover": "explorer", "architecture": "architect",
            "database_architecture": "database_architect", "implementation": "general",
            "qa": "qa_engineer", "security": "security_auditor",
            "performance": "performance_engineer", "accessibility": "accessibility_engineer",
            "ux": "ux_designer", "review": "code_reviewer",
            "documentation": "technical_writer", "close": "build_verification",
        }
        requested_agent = str(params.get("agent") or "").strip()
        agent = requested_agent or default_agents.get(gate) or (profiles_for_gate(gate) or ["general"])[0]
        agent_correction = ({"requested": requested_agent or None, "used": agent} if requested_agent != agent else None)
        if agent not in AGENTS:
            raise ValueError(f"unknown agent '{agent}'")
        if state["status"] != "active":
            raise ValueError(f"cannot delegate while task status is '{state['status']}'")
        if gate == "documentation" and agent != "technical_writer":
            raise ValueError("documentation gate must be delegated to technical_writer")
        retry = int(params.get("retry", 0))
        if retry < 0 or retry > 2:
            raise ValueError("retry must be between 0 and 2")
        if gate == "documentation":
            existing = [
                item for item in state["attempts"]
                if item.get("gate") == gate
                and item.get("status") in {AWAITING_HOST_SPAWN, "running", "passed"}
                and not item.get("invalidated")
            ]
            if existing:
                evidence = [
                    item for item in state.get("evidence", [])
                    if item.get("gate") == gate
                    and item.get("attempt_id") in {attempt["attempt_id"] for attempt in existing}
                    and item.get("kind") in DOCUMENTATION_EVIDENCE_KINDS
                    and item.get("decision") in {"updated", "not_applicable"}
                ]
                return {
                    "recorded": False,
                    "reason": "documentation_attempt_already_available",
                    "candidate_attempt_ids": [item["attempt_id"] for item in existing],
                    "next_action": (
                        "confirm_host_spawn"
                        if any(item.get("status") == AWAITING_HOST_SPAWN for item in existing)
                        else "record_gate_outcome" if evidence else "record_evidence"
                    ),
                    "recoverable": True,
                    "state": state,
                }
        prior_failures = sum(
            1 for attempt in state["attempts"]
            if attempt["gate"] == gate and attempt["status"] == "failed" and not attempt.get("invalidated")
        )
        if prior_failures >= 2:
            raise ValueError(f"retry budget exhausted for gate '{gate}'")
        task_definition = _read_private_json(task_dir / "task.json", "task definition")
        ownership = str(params.get("ownership", "")).strip() or f"Own the {gate} gate as {agent}"
        default_task_kind = {
            "plan": "planning", "discover": "discovery", "architecture": "architecture",
            "database_architecture": "database", "implementation": "implementation",
            "qa": "testing", "security": "security", "review": "review",
            "documentation": "documentation", "close": "verification",
        }.get(gate, gate)
        requested_task_kind = str(params.get("task_kind") or "").strip()
        task_kind = requested_task_kind or default_task_kind
        requested_risk = str(params.get("risk") or "").strip().lower()
        risk = requested_risk or ("high" if gate == "security" else "low" if gate in {"plan", "discover", "documentation"} else "moderate")
        route_params = {
            **params,
            "agent": agent,
            "task_kind": task_kind,
            "risk": risk,
            "complexity": state.get("complexity", "C1"),
            # The gate is ledger-validated above; do not let an arbitrary
            # direct routing parameter claim this trusted security context.
            "_security_gate": gate == "security",
        }
        raw_escalation = params.get("sol_escalation")
        if isinstance(raw_escalation, dict) and raw_escalation.get("kind") == "terra_failure":
            prior_attempt_id = safe_id(str(raw_escalation.get("prior_terra_attempt_id", "")))
            prior_attempt = next((item for item in state["attempts"] if item.get("attempt_id") == prior_attempt_id), None)
            if not prior_attempt or prior_attempt.get("selected_model") != "gpt-5.6-terra" or prior_attempt.get("status") != "failed":
                raise ValueError("terra_failure Sol escalation requires a validated failed Terra attempt in this task ledger")
            route_params["_validated_terra_failure"] = {"attempt_id": prior_attempt_id}
        route = resolve_dispatch_route(route_params)
        def delegation_list(field: str, fallback: list[str]) -> list[str]:
            supplied = params.get(field)
            if isinstance(supplied, list):
                cleaned = [item.strip() for item in supplied if isinstance(item, str) and item.strip()]
                if cleaned:
                    return cleaned
            inherited = task_definition.get(field)
            if isinstance(inherited, list):
                cleaned = [item.strip() for item in inherited if isinstance(item, str) and item.strip()]
                if cleaned:
                    return cleaned
            return fallback

        required_lists = {
            "allowed_paths": delegation_list("allowed_paths", ["."]),
            "acceptance_criteria": delegation_list("acceptance_criteria", ["Complete the current gate and publish a strict Cortex report."]),
            "verification": delegation_list("verification", ["Publish report-backed evidence for the current gate."]),
        }
        context_report_ids = [safe_id(str(item)) for item in params.get("context_report_ids", [])]
        report_paths = report_bus_paths(task_dir)
        available_reports = {item["report_id"] for item in _report_index(report_paths, state["task_id"]).get("reports", [])}
        if len(context_report_ids) != len(set(context_report_ids)) or not set(context_report_ids).issubset(available_reports):
            raise ValueError("context_report_ids must be unique reports from this task")
        attempt_id = f"{gate}-{len(state['attempts']) + 1:02d}"
        # `spawn_agent.task_name` is the only naming field exposed by the
        # native Codex adapter.  It must therefore carry the canonical Cortex
        # profile name; `attempt_id` remains the durable unique correlation
        # key and must never leak into the worker label.
        task_name = agent
        spawn_request = {
            "host_tool": "spawn_agent",
            "profile": agent,
            "display_name": agent,
            "task_name": task_name,
            "model": route["selected_model"],
            "reasoning_effort": route["selected_reasoning_effort"],
        }
        question_route = {
            "mode": "pull",
            "worker_tool": "cortex.question",
            "publish_tool": "publish_worker_question",
            "updates_tool": "get_worker_question_updates",
            "coordinator_list_tool": "list_worker_questions",
            "coordinator_answer_tool": "answer_worker_question",
            "coordinator_ui_tool": "cortex.question",
            "answer_location": "main_chat",
        }
        package = {"schema": SCHEMA, "task_id": state["task_id"], "gate": gate, "attempt_id": attempt_id, "agent": agent, "profile": agent, "display_name": agent, "spawn_request": spawn_request, **route, "retry": retry, "parallel": bool(params.get("parallel", False)), "objective": redact(params.get("objective", "")), "ownership": redact(ownership, 1000), "context_files": [redact(item, 500) for item in params.get("context_files", [])][:50], "context_report_ids": context_report_ids, "report_index": "reports/index.json", "allowed_paths": [redact(item, 500) for item in required_lists["allowed_paths"]][:50], "acceptance_criteria": [redact(item, 1000) for item in required_lists["acceptance_criteria"]][:50], "verification": [redact(item, 1000) for item in required_lists["verification"]][:50], "project_root": str(select_project_root(params)), "coordinator_principal": state.get("principal", "local"), "coordinator_thread_id": state.get("thread_id", ""), "user_language": task_definition.get("user_language", "en"), "internal_language": "en", "visibility": "hidden", "user_facing": False, "question_route": question_route, "escalation_route": "main_chat", "handoff_route": "main_chat", "subdelegation": "forbidden_unless_explicitly_authorized", "report_contract": REPORT_SCHEMA, "question_contract": QUESTION_SCHEMA, "status_receipt": status_receipt, "dispatch_correlation": "host_spawn_required", "spawn_status": "requested", "created_at": now()}
        spawn_request["message"] = host_spawn_prompt(agent, package)
        package_path = task_dir / "delegations" / f"{attempt_id}.json"
        write_json(package_path, package)
        if observed is not None and status_path is not None:
            observed["consumed_at"] = now()
            observed["attempt_id"] = attempt_id
            write_json(status_path, observed)
        state["attempts"].append({"attempt_id": attempt_id, "gate": gate, "agent": agent, "profile": agent, "display_name": agent, "spawn_request": spawn_request, **route, "ownership": package["ownership"], "allowed_paths": package["allowed_paths"], "acceptance_criteria": package["acceptance_criteria"], "verification": package["verification"], "context_report_ids": context_report_ids, "visibility": "hidden", "user_facing": False, "return_route": "main_chat", "status": AWAITING_HOST_SPAWN, "parallel": bool(params.get("parallel", False)), "evidence_ids": [], "report_ids": [], "created_at": now()})
        delegation_index_path, delegation_index = _delegation_report_index(report_paths, state["task_id"], attempt_id)
        delegation_index["context_report_ids"] = context_report_ids
        delegation_index["updated_at"] = now()
        write_json(delegation_index_path, delegation_index)
        metrics = json.loads((task_dir / "metrics.json").read_text(encoding="utf-8"))
        metrics["delegation_count"] = int(metrics.get("delegation_count", 0)) + 1
        metrics["retries"] = int(metrics.get("retries", 0)) + (1 if retry else 0)
        write_json(task_dir / "metrics.json", metrics)
        save_state(task_dir, task_dir / "current.json", state, "delegation", f"{gate} → {agent} ({package_path.name})")
        return {
            "delegation_file": str(package_path),
            "attempt_id": attempt_id,
            "spawn_request": spawn_request,
            "state": state,
            "gate_correction": ({"requested": requested_gate, "used": gate} if requested_gate != gate else None),
            "revision_correction": revision_correction,
            "receipt_correction": receipt_correction,
            "agent_correction": agent_correction,
            "task_kind_correction": ({"requested": requested_task_kind or None, "used": task_kind} if requested_task_kind != task_kind else None),
            "risk_correction": ({"requested": requested_risk or None, "used": risk} if requested_risk != risk else None),
        }


def prepare_delegation(params: dict[str, Any]) -> dict[str, Any]:
    """Prepare status receipt and delegation under one MCP round-trip/lock."""
    root = ledger_root(params)
    with state_lock(root):
        spec = params.get("delegation") if isinstance(params.get("delegation"), dict) else {}
        merged = {**params, **spec}
        observed = status(merged)
        merged["expected_revision"] = observed["state"]["revision"]
        merged["status_receipt"] = observed["status_receipt"]
        result = record_delegation(merged)
        return {"status": observed, "delegation": result, "state": result["state"], "atomic": True}


def prepare_delegations(params: dict[str, Any]) -> dict[str, Any]:
    """Prepare independent delegations across the current executable wave."""
    specs = params.get("delegations")
    if not isinstance(specs, list) or not specs or len(specs) > 32:
        raise ValueError("prepare_delegations requires 1..32 delegation specs")
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, current_state = load_state(str(params["task_id"]), params)
        authorize(current_state, params)
        current_wave = active_gates(current_state)
        snapshot = {
            path.relative_to(task_dir).as_posix(): path.read_text(encoding="utf-8")
            for path in task_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        prepared: list[dict[str, Any]] = []
        for index, raw in enumerate(specs):
            if not isinstance(raw, dict):
                return {"recorded": False, "reason": "invalid_batch_spec", "index": index, "prepared": prepared, "recoverable": True}
            if raw.get("parallel") is not True:
                return {"recorded": False, "reason": "batch_requires_parallel_true", "index": index, "prepared": prepared, "recoverable": True}
            gate = str(raw.get("gate") or (current_wave[0] if current_wave else "")).strip()
            if gate not in current_wave:
                return {"recorded": False, "reason": "batch_requires_one_gate", "current_gates": current_wave, "index": index, "prepared": prepared, "recoverable": True}
            try:
                result = prepare_delegation({**params, "delegation": raw})
            except Exception as exc:
                for path in sorted(task_dir.rglob("*"), reverse=True):
                    if path.is_file() and not path.is_symlink() and path.relative_to(task_dir).as_posix() not in snapshot:
                        path.unlink()
                for relative, content in snapshot.items():
                    restore = task_dir / relative
                    write_text_atomic(restore, content)
                append_journal_best_effort(task_dir, "delegation_batch_rollback", f"spec {index}: {type(exc).__name__}")
                return {
                    "recorded": False,
                    "atomic": True,
                    "reason": "partial_failure",
                    "index": index,
                    "error": redact(str(exc), 1000),
                    "prepared": [],
                    "recoverable": True,
                    "next_action": "retry the batch after correcting the returned error; no batch attempts were committed",
                }
            prepared.append(result)
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
    """Bind a ledger attempt to a native Codex spawn after the host confirms it."""
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
        if not host_agent_id or not host_task_name:
            return {
                "confirmed": False,
                "attempt_id": attempt_id,
                "next_action": "retry confirm_host_spawn with the native spawn_agent host_agent_id and host_task_name",
                "recoverable": True,
                "revision_correction": revision_correction,
                "state": state,
            }
        expected_task_name = str((attempt.get("spawn_request") or {}).get("task_name", ""))
        task_name_correction = (
            {"requested": host_task_name, "used": expected_task_name}
            if host_task_name != expected_task_name else None
        )
        requested_model = str(
            attempt.get("selected_model")
            or (attempt.get("spawn_request") or {}).get("model")
            or ""
        ).strip()
        host_model = redact(str(params.get("host_model", "")).strip(), 128) or None
        if requested_model and not host_model:
            return {
                "confirmed": False,
                "attempt_id": attempt_id,
                "reason": "host_model_required",
                "next_action": "retry confirm_host_spawn with the actual host_model returned by native spawn_agent",
                "required_fields": ["host_model"],
                "recoverable": True,
                "task_name_correction": task_name_correction,
                "revision_correction": revision_correction,
                "state": state,
            }
        host_spawn = {
            "tool": "spawn_agent",
            "agent_id": host_agent_id,
            "task_name": expected_task_name,
            "model": host_model,
            "reasoning_effort": redact(str(params.get("host_reasoning_effort", "")).strip(), 64) or None,
            "requested_model": requested_model or None,
            "confirmed_at": now(),
        }
        model_verification = "not_requested"
        if requested_model:
            model_verification = "verified" if host_model == requested_model else "mismatch"
        host_spawn["model_verification"] = model_verification
        if attempt.get("status") == "running":
            existing = attempt.get("host_spawn") or {}
            if all(existing.get(key) == host_spawn.get(key) for key in ("tool", "agent_id", "task_name", "model", "reasoning_effort", "requested_model", "model_verification")):
                return {"attempt_id": attempt_id, "idempotent": True, "revision_correction": revision_correction, "state": state}
            raise ValueError("running attempt already has a different host spawn binding")
        if attempt.get("status") != AWAITING_HOST_SPAWN or attempt.get("invalidated"):
            raise ValueError("only an active attempt awaiting host spawn can be confirmed")
        if model_verification == "mismatch":
            mismatch_reason = f"host_model_mismatch: requested {requested_model}, actual {host_model}"
            attempt["host_spawn"] = host_spawn
            attempt["model_verification"] = model_verification
            attempt["status"] = "failed"
            attempt["finalized_at"] = host_spawn["confirmed_at"]
            attempt["finalization_reason"] = mismatch_reason
            package_path = task_dir / "delegations" / f"{attempt_id}.json"
            package = _read_private_json(package_path, "delegation package")
            package["spawn_status"] = "confirmed_model_mismatch"
            package["dispatch_correlation"] = "coordinator_recorded_host_spawn"
            package["host_spawn"] = host_spawn
            package["model_verification"] = model_verification
            write_json(package_path, package)
            save_state(task_dir, task_dir / "current.json", state, "host_spawn_model_mismatch", f"{attempt_id}: {mismatch_reason}")
            return {
                "confirmed": False,
                "failed": True,
                "attempt_id": attempt_id,
                "reason": "host_model_mismatch",
                "detail": mismatch_reason,
                "expected_model": requested_model,
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
        package_path = task_dir / "delegations" / f"{attempt_id}.json"
        package = _read_private_json(package_path, "delegation package")
        package["spawn_status"] = "confirmed"
        package["dispatch_correlation"] = "coordinator_recorded_host_spawn"
        package["host_spawn"] = host_spawn
        write_json(package_path, package)
        save_state(task_dir, task_dir / "current.json", state, "host_spawn", f"{attempt_id}: {expected_task_name}")
        return {"confirmed": True, "attempt_id": attempt_id, "idempotent": False, "host_spawn": host_spawn, "task_name_correction": task_name_correction, "revision_correction": revision_correction, "state": state}


def _attempt_evidence(state: dict[str, Any], attempt_id: str) -> list[dict[str, Any]]:
    return [
        item for item in state.get("evidence", [])
        if item.get("attempt_id") == attempt_id and not item.get("invalidated")
    ]


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
        save_state(task_dir, task_dir / "current.json", state, "attempt", f"{attempt_id}: {status}")
        return {"attempt_id": attempt_id, "status": status, "idempotent": False, "revision_correction": revision_correction, "state": state}


def _repair_documentation_receipt(
    task_dir: Path,
    state: dict[str, Any],
    gate_attempts: list[dict[str, Any]],
    gate_evidence: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Repair a legacy documentation label without dispatching another worker."""
    technical_writer_attempts = {
        item["attempt_id"]
        for item in gate_attempts
        if item.get("agent") == "technical_writer"
        and item.get("status") in {"running", "passed"}
    }
    candidates = [
        item for item in gate_evidence
        if item.get("attempt_id") in technical_writer_attempts
        and item.get("kind") in DOCUMENTATION_EVIDENCE_KINDS
        and item.get("decision") in {"updated", "not_applicable"}
        and item.get("report_id")
        and item.get("report_receipt")
    ]
    if not candidates:
        return None
    evidence = candidates[-1]
    if evidence.get("kind") != "documentation":
        evidence["kind"] = "documentation"
        write_json(task_dir / "evidence" / f"{evidence['evidence_id']}.json", evidence)
    receipt = {
        "evidence_id": evidence["evidence_id"],
        "attempt_id": evidence["attempt_id"],
        "decision": evidence.get("decision"),
        "justification": evidence.get("justification"),
    }
    state["documentation_receipt"] = receipt
    save_state(task_dir, task_dir / "current.json", state, "documentation_receipt_repair", evidence["evidence_id"])
    return receipt


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
    report_receipt_path = None
    report_paths = None
    if state.get("require_delegation"):
        receipt_id = safe_id(str(params.get("report_receipt", "")))
        report_paths = report_bus_paths(task_dir)
        report_receipt_path = report_paths["receipts"] / f"{receipt_id}.json"
        tombstone_path = _consumption_path(report_paths, receipt_id)
        if tombstone_path.exists():
            raise ValueError("report receipt is consumed and cannot be replayed")
        if not report_receipt_path.exists() or report_receipt_path.is_symlink():
            raise ValueError("C2/C3 evidence requires an attempt-tied report receipt")
        report_receipt = _read_private_json(report_receipt_path, "report receipt")
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
    if report_receipt is not None and report_receipt_path is not None and report_paths is not None:
        tombstone = _consumption_tombstone(report_receipt, evidence_id)
        write_json_exclusive(_consumption_path(report_paths, report_receipt["receipt_id"]), tombstone)
        report_receipt["consumed_at"] = tombstone["consumed_at"]
        report_receipt["consumed_by_evidence_id"] = tombstone["consumed_by_evidence_id"]
        write_json(report_receipt_path, report_receipt)
    write_json(task_dir / "evidence" / f"{evidence_id}.json", evidence)
    state["evidence"].append(evidence)
    if kind == "documentation":
        state["documentation_receipt"] = {"evidence_id": evidence_id, "attempt_id": attempt_id, "decision": decision, "justification": evidence["justification"]}
    for attempt in state["attempts"]:
        if attempt["attempt_id"] == attempt_id:
            attempt["evidence_ids"].append(evidence_id)
            if evidence.get("report_id"):
                attempt.setdefault("report_ids", []).append(evidence["report_id"])
    metrics_path = task_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["evidence_count"] = int(metrics.get("evidence_count", 0)) + 1
    write_json(metrics_path, metrics)
    save_state(task_dir, task_dir / "current.json", state, "evidence", f"{gate}: {evidence_id}")
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
            receipt_path = report_bus_paths(task_dir)["receipts"] / f"{receipt_id}.json"
            if receipt_path.exists() and not receipt_path.is_symlink():
                receipt = _read_private_json(receipt_path, "report receipt")
                if receipt.get("task_id") == state["task_id"] and receipt.get("gate") == resolved["gate"] and not receipt.get("consumed_at") and not receipt.get("invalidated"):
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
            paths = report_bus_paths(task_dir)
            receipts = []
            for receipt_path in paths["receipts"].glob("report-receipt-*.json"):
                if receipt_path.is_symlink():
                    continue
                receipt = _read_private_json(receipt_path, "report receipt")
                if receipt.get("attempt_id") == resolved.get("attempt_id") and receipt.get("gate") == resolved["gate"] and not receipt.get("consumed_at") and not receipt.get("invalidated"):
                    receipts.append(receipt)
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
            resolved["report_receipt"], receipt_correction = _resolve_report_receipt_hint(
                task_dir, state, resolved["gate"], resolved.get("attempt_id"), resolved["report_receipt"]
            )
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
            paths = report_bus_paths(task_dir)
            receipts = []
            for receipt_path in paths["receipts"].glob("report-receipt-*.json"):
                if receipt_path.is_symlink():
                    continue
                receipt = _read_private_json(receipt_path, "report receipt")
                if receipt.get("attempt_id") == resolved.get("attempt_id") and receipt.get("gate") == resolved["gate"] and not receipt.get("consumed_at") and not receipt.get("invalidated"):
                    receipts.append(receipt)
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
            resolved["report_receipt"], receipt_correction = _resolve_report_receipt_hint(
                task_dir, state, resolved["gate"], resolved.get("attempt_id"), resolved["report_receipt"]
            )
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
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
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
    root = ledger_root(params)
    with state_lock(root):
        root, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        expected_revision = params.get("expected_revision")
        revision_correction = None
        if expected_revision is not None and state["revision"] != expected_revision:
            revision_correction = {"requested": expected_revision, "used": state["revision"]}
        requested_gate, outcome = str(params["gate"]), str(params["outcome"])
        current_wave = active_gates(state)
        gate = requested_gate if requested_gate in current_wave else (current_wave[0] if current_wave else str(state.get("current_gate")))
        if outcome not in {"passed", "failed", "blocked", "skipped"}:
            raise ValueError("outcome must be passed, failed, blocked, or skipped")
        if outcome == "skipped":
            if gate in {"documentation", "close"} and state.get("require_delegation"):
                return {
                    "recorded": False,
                    "reason": "mandatory_gate",
                    "gate": gate,
                    "next_action": "record_delegation",
                    "recoverable": True,
                    "revision_correction": revision_correction,
                    "state": state,
                }
            if gate == "close":
                return {
                    "recorded": False,
                    "reason": "mandatory_gate",
                    "gate": gate,
                    "next_action": "record_delegation",
                    "recoverable": True,
                    "revision_correction": revision_correction,
                    "state": state,
                }
            if state.get("require_delegation") and not str(params.get("skip_reason", "")).strip():
                raise ValueError("C2/C3 skipped gates require an explicit skip_reason")
        gate_evidence = [item for item in state.get("evidence", []) if item.get("gate") == gate and not item.get("invalidated")]
        gate_attempts = [item for item in state.get("attempts", []) if item.get("gate") == gate and not item.get("invalidated")]
        non_terminal_attempts = [item for item in gate_attempts if item.get("status") not in TERMINAL_ATTEMPT_STATUSES]
        terminal_non_success_attempts = [
            item for item in gate_attempts
            if item.get("status") in TERMINAL_ATTEMPT_STATUSES - {"passed"}
        ]
        passed_attempts = [item for item in gate_attempts if item.get("status") == "passed"]
        if outcome == "passed" and not gate_evidence:
            if not (state.get("require_delegation") and gate_attempts and len(terminal_non_success_attempts) == len(gate_attempts)):
                return {
                    "recorded": False,
                    "reason": "evidence_required",
                    "gate": gate,
                    "gate_correction": ({"requested": requested_gate, "used": gate} if requested_gate != gate else None),
                    "candidate_attempt_ids": [item["attempt_id"] for item in non_terminal_attempts + passed_attempts],
                    "next_action": ("record_delegation" if not gate_attempts else "record_evidence"),
                    "revision_correction": revision_correction,
                    "state": state,
                }
        if outcome == "passed" and state.get("require_delegation"):
            if not gate_attempts:
                if gate == "documentation":
                    return {
                        "recorded": False,
                        "reason": "documentation_attempt_required",
                        "gate": gate,
                        "next_action": "record_delegation",
                        "recoverable": True,
                        "revision_correction": revision_correction,
                        "state": state,
                    }
                raise ValueError("C2/C3 gates require at least one delegation attempt")
            missing = [
                item["attempt_id"] for item in passed_attempts
                if not any(e.get("attempt_id") == item["attempt_id"] for e in gate_evidence)
            ]
            if missing:
                if gate == "documentation":
                    return {
                        "recorded": False,
                        "reason": "documentation_evidence_required",
                        "gate": gate,
                        "candidate_attempt_ids": missing,
                        "next_action": "record_evidence",
                        "recoverable": True,
                        "revision_correction": revision_correction,
                        "state": state,
                    }
                raise ValueError("every passed attempt needs linked evidence before the gate can pass: " + ", ".join(missing))
            missing_reports = [
                item["attempt_id"] for item in passed_attempts
                if not any(e.get("attempt_id") == item["attempt_id"] and e.get("report_id") and e.get("report_receipt") for e in gate_evidence)
            ]
            if missing_reports:
                if gate == "documentation":
                    return {
                        "recorded": False,
                        "reason": "documentation_report_receipt_required",
                        "gate": gate,
                        "candidate_attempt_ids": missing_reports,
                        "next_action": "record_evidence",
                        "recoverable": True,
                        "revision_correction": revision_correction,
                        "state": state,
                    }
                raise ValueError("every passed attempt needs a consumed report receipt before the gate can pass: " + ", ".join(missing_reports))
            evidence_attempt_ids = {item.get("attempt_id") for item in gate_evidence}
            unexplained = [item["attempt_id"] for item in non_terminal_attempts if item["attempt_id"] not in evidence_attempt_ids]
            if unexplained:
                if gate == "documentation":
                    return {
                        "recorded": False,
                        "reason": "documentation_evidence_required",
                        "gate": gate,
                        "candidate_attempt_ids": unexplained,
                        "next_action": "record_evidence",
                        "recoverable": True,
                        "revision_correction": revision_correction,
                        "state": state,
                    }
                raise ValueError("every active delegated attempt needs linked evidence before the gate can pass: " + ", ".join(unexplained))
            eligible_attempt_ids = {
                item["attempt_id"] for item in gate_attempts
                if item["attempt_id"] in evidence_attempt_ids and item.get("status") in {"running", "passed"}
            }
            if passed_attempts and not eligible_attempt_ids:
                if gate == "documentation":
                    return {
                        "recorded": False,
                        "reason": "documentation_evidence_required",
                        "gate": gate,
                        "candidate_attempt_ids": [item["attempt_id"] for item in passed_attempts],
                        "next_action": "record_evidence",
                        "recoverable": True,
                        "revision_correction": revision_correction,
                        "state": state,
                    }
                raise ValueError("a passed gate requires linked evidence for at least one delegated attempt")
            current_attempt_evidence = [item for item in gate_evidence if item.get("attempt_id") in eligible_attempt_ids]
        else:
            current_attempt_evidence = gate_evidence
        if outcome == "passed" and any(item.get("kind") == "command" and (item.get("exit_code") != 0 or not item.get("verified_execution")) for item in current_attempt_evidence):
            raise ValueError("cannot pass a gate with failed or self-attested command evidence; use execute_verification_command")
        if outcome == "passed" and gate == "documentation" and state.get("require_delegation"):
            documentation = state.get("documentation_receipt")
            technical_writer_attempt_ids = {
                item["attempt_id"] for item in gate_attempts if item.get("agent") == "technical_writer"
            }
            if not documentation or documentation.get("attempt_id") not in technical_writer_attempt_ids:
                documentation = _repair_documentation_receipt(task_dir, state, gate_attempts, gate_evidence)
            if not documentation or documentation.get("attempt_id") not in technical_writer_attempt_ids:
                return {
                    "recorded": False,
                    "reason": "documentation_evidence_required",
                    "gate": gate,
                    "candidate_attempt_ids": [item["attempt_id"] for item in gate_attempts if item.get("agent") == "technical_writer"],
                    "next_action": "record_evidence",
                    "recoverable": True,
                    "revision_correction": revision_correction,
                    "state": state,
                }
        if outcome == "blocked" and state.get("require_handoff") and (not state.get("handoff_created") or state.get("handoff_gate") != gate):
            raise ValueError("C2/C3 pause requires a current-gate handoff")
        if outcome == "passed" and gate == "close" and state.get("require_handoff") and (not state.get("handoff_created") or state.get("handoff_gate") != "close"):
            raise ValueError("C2/C3 close requires a final handoff")
        if outcome == "passed" and gate == "close" and state.get("require_handoff"):
            if "documentation" not in state.get("completed_gates", []) or not state.get("documentation_receipt"):
                raise ValueError("C2/C3 close requires completed documentation decision evidence")
            if not state.get("reassessment_receipts"):
                raise ValueError("C2/C3 close requires a recorded reassessment decision")
            if not any(item.get("kind") == "command" and item.get("verified_execution") and item.get("exit_code") == 0 for item in current_attempt_evidence):
                raise ValueError("C2/C3 close requires successful server-observed command evidence")
            manifest = state.get("final_manifest_receipt")
            if not manifest or not manifest.get("complete"):
                raise ValueError("C2/C3 close requires a complete handoff file-manifest receipt")
            current_manifest = capture_project_manifest(Path(json.loads((task_dir / "task.json").read_text(encoding="utf-8"))["project_root"]))
            if current_manifest["digest"] != manifest.get("current_digest"):
                raise ValueError("project files changed after the final handoff; create a new complete handoff")
        state["gates"][gate] = {"outcome": outcome, "at": now(), "summary": redact(params.get("summary", ""), 2000), "skip_reason": redact(params.get("skip_reason", ""), 2000), "evidence_ids": [item["evidence_id"] for item in gate_evidence]}
        if outcome == "passed":
            if gate not in state["completed_gates"]:
                state["completed_gates"].append(gate)
            for attempt in state["attempts"]:
                if attempt["gate"] == gate and attempt["status"] == "running":
                    attempt["status"] = "passed"
        elif outcome == "skipped":
            if gate not in state["skipped_gates"]:
                state["skipped_gates"].append(gate)
        elif outcome == "blocked":
            state["status"] = "blocked"
        else:
            for attempt in state["attempts"]:
                if attempt["gate"] == gate and attempt["status"] in {"running", AWAITING_HOST_SPAWN}:
                    attempt["status"] = "failed"
        operations = params.get("pipeline_operations", [])
        if operations:
            change = apply_pipeline_operations(state, operations=operations, allow_rework=bool(params.get("allow_rework", False)))
            append_pipeline_change(state, change, str(params.get("pipeline_reason", "adaptive gate outcome")), params.get("signals", []))
            invalidate_reworked_report_receipts(task_dir, state)
        if outcome in {"passed", "skipped"}:
            candidate_wave = sync_current_wave(state)
            if not candidate_wave:
                validate_completion_invariants(state)
                state["status"] = "completed"
        else:
            sync_current_wave(state)
        metrics_path = task_dir / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        metrics["gate_outcomes"].append({"gate": gate, "outcome": outcome, "at": now(), "pipeline_changed": bool(operations), "parallel_wave": current_wave})
        write_json(metrics_path, metrics)
        save_state(task_dir, task_dir / "current.json", state, "gate", f"{gate}: {outcome}" + ("; pipeline adapted" if operations else ""))
        if state["status"] == "completed":
            task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
            remove_active_mapping(root, state["task_id"], str(task.get("thread_id", "")))
        return {"state": state, "revision_correction": revision_correction}


def _record_commit_gate_recovery(
    task_dir: Path,
    state: dict[str, Any],
    params: dict[str, Any],
    mode: str,
    error: str,
) -> dict[str, Any]:
    """Persist bounded fast-path failures so a bad adapter cannot hang a task."""
    gate = str(params.get("gate") or state.get("current_gate") or "unknown")
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
    metrics_path = task_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["gate_recovery_failures"] = int(metrics.get("gate_recovery_failures", 0)) + 1
    write_json(metrics_path, metrics)
    save_state(task_dir, task_dir / "current.json", state, "gate_recovery", f"{gate}: {reason}")
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
        evidence = None
        outcome = str(params.get("outcome") or "failed")
        try:
            if mode == "verification":
                evidence = execute_verification({**params, "gate": params.get("gate") or ""})
                state = evidence["state"]
                if evidence.get("recorded") is False:
                    raise ValueError(str(evidence.get("reason") or "verification evidence was not recorded"))
                exit_code = evidence.get("execution", {}).get("exit_code")
                outcome = str(params.get("outcome") or ("passed" if exit_code == 0 else "failed"))
            elif mode == "documentation":
                evidence_params = {**params, "gate": params.get("gate") or "documentation", "kind": "documentation"}
                evidence = record_evidence(evidence_params)
                state = evidence["state"]
                if evidence.get("recorded") is False:
                    raise ValueError(str(evidence.get("reason") or "documentation evidence was not recorded"))
                outcome = str(params.get("outcome") or "passed")
            else:
                raise ValueError("commit_gate mode must be verification or documentation")
            gate_result = record_gate({
                **params,
                "gate": params.get("gate") or state["current_gate"],
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
        save_state(task_dir, task_dir / "current.json", state, "resume", redact(params.get("reason", "task resumed")))
        return {"state": state}


def update_pipeline(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        if state["status"] == "completed" and not params.get("allow_rework", False):
            raise ValueError("completed task cannot change pipeline without allow_rework=true")
        requested, operations = params.get("pipeline"), params.get("operations", [])
        if requested is None and not operations:
            raise ValueError("provide pipeline or operations")
        requested_gates = normalize_pipeline(requested) if requested is not None else list(state["current_pipeline"])
        change = apply_pipeline_operations(state, pipeline=requested, operations=operations, allow_rework=bool(params.get("allow_rework", False)), parallel_groups=params.get("parallel_groups"))
        append_pipeline_change(state, change, str(params.get("reason", "pipeline updated")), params.get("signals", []))
        invalidate_reworked_report_receipts(task_dir, state)
        sync_current_wave(state)
        if state["current_gate"] is not None and state["status"] == "completed":
            state["status"] = "active"
        save_state(task_dir, task_dir / "current.json", state, "pipeline", str(params.get("reason", "pipeline updated")))
        return {"state": state, "change": change}


def reassess_pipeline(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        if state["status"] == "completed":
            raise ValueError("completed task cannot be reassessed")
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
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
            gate = str(params.get("gate", state["current_gate"]))
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
            if state.get("require_handoff") and (not state.get("handoff_created") or state.get("handoff_gate") != state.get("current_gate")):
                raise ValueError("C2/C3 stop reassessment requires a current-gate handoff")
            state["status"] = "blocked"
            state["replan_count"] += 1
            state.setdefault("reassessment_receipts", []).append(receipt)
            save_state(task_dir, task_dir / "current.json", state, "reassess", "pipeline stopped by policy")
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
            save_state(task_dir, task_dir / "current.json", state, "reassess", str(params.get("reason", "reassessment")))
            result.update({"applied": True, "state": state, "change": change})
        else:
            if state.get("require_delegation") and decision == "updated":
                raise ValueError("decision=updated requires applied pipeline operations")
            state.setdefault("reassessment_receipts", []).append(receipt)
            save_state(task_dir, task_dir / "current.json", state, "reassess", reason or "pipeline unchanged")
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
        entry = {"path": redact(path, 500), "owner": redact(owner, 256), "owner_digest": digest_text(owner), "gate": redact(params.get("gate", state["current_gate"]), 64), "acquired_at": now(), "expires_at": params.get("expires_at"), "advisory": bool(params.get("advisory", True))}
        state["locks"][key] = entry
        write_json(task_dir / "locks" / f"{key}.json", entry)
        save_state(task_dir, task_dir / "current.json", state, "lock", f"{redact(path, 300)} → {redact(owner, 128)}")
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
        lock_file = task_dir / "locks" / f"{key}.json"
        if lock_file.exists():
            lock_file.unlink()
        save_state(task_dir, task_dir / "current.json", state, "unlock", f"{redact(path, 300)} released by {redact(owner, 128)}")
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
        receipt, current_manifest = reconcile_manifest(task_dir, state, files)
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
        payload = {"schema": SCHEMA, "task_id": state["task_id"], "created_at": now(), "source_revision": state["revision"], "gate": state.get("current_gate"), "completed": completed, "files": [redact(item, 500) for item in files], "file_manifest_receipt": receipt, "decisions": [redact(item, 2000) for item in params.get("decisions", [])][:100], "risks": [redact(item, 2000) for item in params.get("risks", [])][:100], "next_action": next_action}
        path = task_dir / "handoffs" / f"{name}.json"
        manifest_path = task_dir / "handoffs" / f"{name}-manifest.json"
        write_json(path, payload)
        write_json(manifest_path, current_manifest)
        state["handoff_created"] = True
        state["handoff_gate"] = state.get("current_gate")
        state["handoff_source_revision"] = payload["source_revision"]
        state["final_manifest_receipt"] = receipt
        state.setdefault("manifest_receipts", []).append({"handoff": path.name, **receipt})
        save_state(task_dir, task_dir / "current.json", state, "handoff", path.name)
        return {"handoff_file": str(path), "manifest_file": str(manifest_path), "file_manifest_receipt": receipt, "state": state}


def reconcile_project_files(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        paths = params.get("paths", [])
        if not isinstance(paths, list) or any(not isinstance(item, str) for item in paths):
            raise ValueError("paths must be an array of project-relative strings")
        receipt, current = reconcile_manifest(task_dir, state, paths)
        receipt_id = f"manifest-{len(state.get('manifest_receipts', [])) + 1:04d}"
        write_json(task_dir / "evidence" / f"{receipt_id}.json", receipt)
        write_json(task_dir / "evidence" / f"{receipt_id}-snapshot.json", current)
        state.setdefault("manifest_receipts", []).append({"receipt_id": receipt_id, **receipt})
        save_state(task_dir, task_dir / "current.json", state, "manifest", f"{receipt_id}: {'complete' if receipt['complete'] else 'incomplete'}")
        return {"receipt_id": receipt_id, "receipt": receipt, "state": state}


def record_metrics(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        attempt_id = str(params.get("attempt_id", ""))
        if attempt_id and not any(item["attempt_id"] == attempt_id for item in state["attempts"]):
            raise ValueError("metrics attempt_id does not belong to the task")
        for field in ("input_tokens", "output_tokens", "elapsed_ms"):
            value = params.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"metrics {field} must be a nonnegative integer")
        estimated_cost = params.get("estimated_cost")
        if estimated_cost is not None and (isinstance(estimated_cost, bool) or not isinstance(estimated_cost, (int, float)) or not math.isfinite(float(estimated_cost)) or float(estimated_cost) < 0):
            raise ValueError("metrics estimated_cost must be finite and nonnegative")
        metric = {"at": now(), "attempt_id": attempt_id or None, "model": redact(params.get("model", ""), 128), "reasoning_effort": redact(params.get("reasoning_effort", ""), 64), "input_tokens": params.get("input_tokens"), "output_tokens": params.get("output_tokens"), "elapsed_ms": params.get("elapsed_ms"), "estimated_cost": params.get("estimated_cost"), "verdict": redact(params.get("verdict", ""), 64)}
        metrics_path = task_dir / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        telemetry = metrics.setdefault("telemetry", [])
        telemetry.append(metric)
        dropped = 0
        while len(telemetry) > MAX_METRIC_EVENTS or len(json.dumps(telemetry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_METRIC_BYTES:
            telemetry.pop(0)
            dropped += 1
        metrics["telemetry_dropped"] = int(metrics.get("telemetry_dropped", 0)) + dropped
        write_json(metrics_path, metrics)
        save_state(task_dir, task_dir / "current.json", state, "metrics", f"telemetry for {attempt_id or 'task'}")
        return {"metric": metric, "state": state}


def claim_resource(params: dict[str, Any]) -> dict[str, Any]:
    root = ledger_root(params)
    with state_lock(root):
        _, task_dir, state = load_state(str(params["task_id"]), params)
        authorize(state, params)
        guard_revision(state, params.get("expected_revision"))
        resource, owner = str(params["path"]), str(params["owner"])
        entry = _claim_global_resource(root, resource, owner, "task", state["task_id"], params.get("expires_at"), str(params.get("kind", "resource")))
        key = lock_key(resource)
        state["locks"][key] = {"path": redact(resource, 500), "owner": redact(owner, 256), "owner_digest": digest_text(owner), "gate": redact(params.get("gate", state["current_gate"]), 64), "acquired_at": entry["claimed_at"], "expires_at": entry["expires_at"], "advisory": False, "global": True}
        write_json(task_dir / "locks" / f"{key}.json", state["locks"][key])
        save_state(task_dir, task_dir / "current.json", state, "resource", f"{redact(resource, 300)} → {redact(owner, 128)}")
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
        lock_file = task_dir / "locks" / f"{key}.json"
        if lock_file.exists():
            lock_file.unlink()
        save_state(task_dir, task_dir / "current.json", state, "resource_release", f"{redact(resource, 300)} released")
        return {"state": state}


PIPELINE_OPERATION_SCHEMA = {"type": "object", "properties": {"op": {"type": "string", "enum": ["add", "remove", "move", "replace", "rework"]}, "gate": {"type": "string"}, "before": {"type": "string"}, "after": {"type": "string"}, "index": {"type": "integer"}, "with": {"type": "array", "items": {"type": "string"}}}, "required": ["op", "gate"]}
QUESTION_OPTION_SCHEMA = {
    "anyOf": [
        {"type": "string", "minLength": 1},
        {"type": "object", "additionalProperties": False, "properties": {"label": {"type": "string", "minLength": 1}, "description": {"type": "string"}}, "required": ["label"]},
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
        "localized_question": {"type": "string", "description": "Coordinator-localized display text; durable worker content remains unchanged."},
        "localized_header": {"type": "string"},
        "localized_options": {"type": "array", "maxItems": 32, "items": QUESTION_OPTION_SCHEMA},
        "localized_custom_label": {"type": "string"},
        "answer_submission_id": {"type": "string", "description": "Stable id for an answer replay."},
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
TOOLS = {
    "activate_orchestration": (activate_orchestration, {"type": "object", "additionalProperties": False, "properties": {"user_command": {"type": "string", "const": "/cortex"}, "thread_id": {"type": "string", "minLength": 1}, "principal": {"type": "string", "minLength": 1}}, "required": ["user_command", "thread_id", "principal"]}),
    "deactivate_orchestration": (deactivate_orchestration, {"type": "object", "additionalProperties": False, "properties": {"user_command": {"type": "string", "const": "/normal"}, "thread_id": {"type": "string"}, "principal": {"type": "string"}}, "required": ["user_command"]}),
    "get_activation_status": (activation_status, {"type": "object", "properties": {"thread_id": {"type": "string"}, "principal": {"type": "string"}}, "required": []}),
    "classify_task": (classify_task, {"type": "object", "properties": {"complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "requirements": {"type": "array", "items": {"type": "string"}}, "pipeline": {"type": "array", "items": {"type": "string"}, "description": "Full gate proposal selected by the orchestrator; Cortex appends only documentation and close when missing."}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "Ordered executable waves selected by the orchestrator; gates in one wave may run concurrently."}, "thread_id": {"type": "string"}, "principal": {"type": "string"}}, "required": ["complexity"]}),
    "init_task": (init_task, {"type": "object", "properties": {"task_id": {"type": "string"}, "objective": {"type": "string"}, "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "classification_id": {"type": "string"}, "requirements": {"type": "array", "items": {"type": "string"}}, "acceptance_criteria": {"type": "array", "items": {"type": "string"}}, "scope": {"type": "array", "items": {"type": "string"}}, "allowed_paths": {"type": "array", "items": {"type": "string"}}, "verification": {"type": "array", "items": {"type": "string"}}, "budget": {"type": "string"}, "pause_conditions": {"type": "array", "items": {"type": "string"}}, "pipeline": {"type": "array", "items": {"type": "string"}}, "parallel_groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}, "thread_id": {"type": "string"}, "principal": {"type": "string"}, "user_language": {"type": "string"}, "replan_limit": {"type": "integer", "minimum": 0}}, "required": ["task_id", "objective", "classification_id"]}),
    "get_task_status": (status, {"type": "object", "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}}, "required": ["task_id", "principal"]}),
    "resolve_dispatch_route": (resolve_dispatch_route, {"type": "object", "additionalProperties": False, "properties": {"agent": {"type": "string", "enum": sorted(AGENTS)}, "task_kind": {"type": "string"}, "risk": {"type": "string", "enum": ["low", "moderate", "high", "critical"]}, "complexity": {"type": "string", "enum": ["C1", "C2", "C3"]}, "requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS)}, "requested_reasoning_effort": {"type": "string"}, "escalation_reason": {"type": "string"}, "sol_escalation": {"type": "object", "additionalProperties": False, "properties": {"kind": {"type": "string", "enum": ["auditable_extreme", "terra_failure"]}, "criterion": {"type": "string", "enum": sorted(AUDITABLE_EXTREME_CRITERIA)}, "audit_ref": {"type": "string", "minLength": 1}, "prior_terra_attempt_id": {"type": "string", "minLength": 1}}, "required": ["kind"]}}, "required": ["agent", "task_kind", "risk"]}),
    "record_delegation": (record_delegation, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "status_receipt": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "gate": {"type": "string"}, "agent": {"type": "string", "enum": sorted(AGENTS)}, "task_kind": {"type": "string"}, "risk": {"type": "string", "enum": ["low", "moderate", "high", "critical"]}, "requested_model": {"type": "string", "enum": sorted(REQUESTABLE_MODELS)}, "requested_reasoning_effort": {"type": "string"}, "escalation_reason": {"type": "string"}, "sol_escalation": {"type": "object", "additionalProperties": False, "properties": {"kind": {"type": "string", "enum": ["auditable_extreme", "terra_failure"]}, "criterion": {"type": "string", "enum": sorted(AUDITABLE_EXTREME_CRITERIA)}, "audit_ref": {"type": "string", "minLength": 1}, "prior_terra_attempt_id": {"type": "string", "minLength": 1}}, "required": ["kind"]}, "retry": {"type": "integer"}, "parallel": {"type": "boolean"}, "objective": {"type": "string"}, "ownership": {"type": "string", "minLength": 1}, "context_files": {"type": "array", "items": {"type": "string"}}, "context_report_ids": {"type": "array", "items": {"type": "string"}}, "allowed_paths": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}, "acceptance_criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}, "verification": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}}, "required": ["task_id", "gate", "agent", "task_kind", "risk", "objective", "ownership", "allowed_paths", "acceptance_criteria", "verification"]}),
    "prepare_delegation": (prepare_delegation, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "delegation": {"type": "object"}}, "required": ["task_id", "principal", "delegation"]}),
    "prepare_delegations": (prepare_delegations, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "delegations": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "object"}}}, "required": ["task_id", "principal", "delegations"]}),
    "confirm_host_spawn": (confirm_host_spawn, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "host_agent_id": {"type": "string", "minLength": 1}, "host_task_name": {"type": "string", "minLength": 1}, "host_model": {"type": "string"}, "host_reasoning_effort": {"type": "string"}}, "required": ["task_id", "expected_revision", "attempt_id", "host_agent_id", "host_task_name"]}),
    "finalize_attempt": (finalize_attempt, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "status": {"type": "string", "enum": sorted(TERMINAL_ATTEMPT_STATUSES)}, "reason": {"type": "string"}}, "required": ["task_id", "expected_revision", "attempt_id", "status"]}),
    "complete_attempt": (complete_attempt, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "host_agent_id": {"type": "string"}, "host_task_name": {"type": "string"}, "host_model": {"type": "string"}, "host_reasoning_effort": {"type": "string"}, "status": {"type": "string", "enum": sorted(TERMINAL_ATTEMPT_STATUSES)}, "reason": {"type": "string"}, "submission_id": {"type": "string"}, "report": {"type": "object"}}, "required": ["task_id", "principal", "attempt_id"]}),
    "record_report": (record_report, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string", "description": "Optional when the worker identity maps to exactly one active attempt; Cortex infers it."}, "submission_id": {"type": "string", "description": "Optional; Cortex derives a deterministic id from the attempt and report digest."}, "report": {"type": "object", "additionalProperties": False, "properties": {"summary": {"type": "string"}, "findings": {"type": "array"}, "questions": {"type": "array"}, "changed_files": {"type": "array", "items": {"type": "string"}}, "tests": {"type": "array"}, "evidence": {"type": "array"}, "uncertainty": {"type": "array"}, "next_action": {"type": "string"}}, "required": sorted(REPORT_FIELDS)}}, "required": ["task_id", "principal", "report"]}),
    "cortex.question": (cortex_question, QUESTION_TOOL_SCHEMA),
    "publish_worker_question": (publish_worker_question, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "submission_id": {"type": "string"}, "question": {"type": "string", "minLength": 1}, "header": {"type": "string"}, "options": {"type": "array", "maxItems": 32, "items": QUESTION_OPTION_SCHEMA}, "multiple": {"type": "boolean"}, "custom_label": {"type": "string"}, "context": {}, "blocking": {"type": "boolean"}}, "required": ["task_id", "principal", "attempt_id", "submission_id", "question"]}),
    "list_worker_questions": (list_worker_questions, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "status": {"type": "string", "enum": ["open", "answered"]}}, "required": ["task_id", "principal"]}),
    "answer_worker_question": (answer_worker_question, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "question_id": {"type": "string"}, "submission_id": {"type": "string"}, "answer": {"type": "string", "minLength": 1}, "resume_context": {}}, "required": ["task_id", "principal", "question_id", "submission_id", "answer", "resume_context"]}),
    "get_worker_question_updates": (get_worker_question_updates, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "after_sequence": {"type": "integer", "minimum": 0}}, "required": ["task_id", "principal", "attempt_id"]}),
    "grant_report_context": (grant_report_context, {"type": "object", "additionalProperties": False, "properties": {"task_id": {"type": "string"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "attempt_id": {"type": "string"}, "report_ids": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}}, "required": ["task_id", "principal", "attempt_id", "report_ids", "reason"]}),
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
    "reconcile_project_files": (reconcile_project_files, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "principal": {"type": "string"}, "thread_id": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}}, "required": ["task_id", "expected_revision", "paths"]}),
    "record_metrics": (record_metrics, {"type": "object", "properties": {"task_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "attempt_id": {"type": "string"}, "model": {"type": "string"}, "reasoning_effort": {"type": "string"}, "input_tokens": {"type": "integer"}, "output_tokens": {"type": "integer"}, "elapsed_ms": {"type": "integer"}, "estimated_cost": {"type": "number"}, "verdict": {"type": "string"}}, "required": ["task_id", "expected_revision"]}),
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

# Keep the public MCP contract aligned with runtime authorization and validation.
AUTHORIZED_TOOLS = {
    "init_task", "get_task_status", "record_delegation", "prepare_delegation", "prepare_delegations", "confirm_host_spawn", "finalize_attempt", "complete_attempt", "record_evidence", "execute_verification_command",
    "record_report", "cortex.question", "publish_worker_question", "list_worker_questions", "answer_worker_question", "get_worker_question_updates",
    "grant_report_context", "list_task_reports", "get_delegation_reports", "reconcile_report_bus", "close_audit",
    "record_gate_outcome", "commit_gate", "resume_task", "update_pipeline", "reassess_pipeline", "acquire_lock", "release_lock",
    "create_handoff", "reconcile_project_files", "record_metrics", "claim_resource", "release_resource",
    "create_lane", "get_lane_status", "claim_lane", "release_lane", "retire_lane", "bind_task_lane",
    "claim_lane_resource", "release_lane_resource", "materialize_lane", "reconcile_lane",
}
for _tool_name in AUTHORIZED_TOOLS:
    _schema = TOOLS[_tool_name][1]
    _schema.setdefault("properties", {}).setdefault("principal", {"type": "string", "minLength": 1})
    if "principal" not in _schema.setdefault("required", []):
        _schema["required"].append("principal")
# A plugin-local MCP process deliberately runs from the plugin directory. The
# first tool call binds an immutable workspace; later calls may omit the root
# because the process can safely restore that already-validated value.
for _, _schema in TOOLS.values():
    _schema.setdefault("properties", {}).setdefault("project_root", {
        "type": "string",
        "minLength": 1,
        "description": "Absolute project workspace path. Cortex writes only to project_root/.codex/cortex.",
    })
if "project_root" not in TOOLS["activate_orchestration"][1].setdefault("required", []):
    TOOLS["activate_orchestration"][1]["required"].append("project_root")
for _tool_name, _required in {
    "claim_resource": ["expires_at"], "claim_lane": ["expires_at"], "claim_lane_resource": ["expires_at"],
    "create_handoff": ["completed", "next_action"], "retire_lane": ["confirm"],
}.items():
    for _field in _required:
        if _field not in TOOLS[_tool_name][1]["required"]:
            TOOLS[_tool_name][1]["required"].append(_field)
TOOLS["retire_lane"][1]["properties"]["confirm"] = {"type": "boolean"}
TOOLS["record_delegation"][1]["required"] = [
    field for field in TOOLS["record_delegation"][1]["required"]
    if field not in {"expected_revision", "status_receipt", "gate", "agent", "task_kind", "risk", "objective", "ownership", "allowed_paths", "acceptance_criteria", "verification"}
]
for _field in ("allowed_paths", "acceptance_criteria", "verification"):
    TOOLS["record_delegation"][1]["properties"][_field].pop("minItems", None)


def respond(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    global MCP_PROJECT_ROOT, MCP_OPENAI_FORM
    # Use readline rather than a ``for line`` iterator because cortex.question
    # performs a nested JSON-RPC read while the originating tools/call is
    # suspended for host elicitation.
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        request_id = None
        request: Any = None
        try:
            request = json.loads(line)
            method, request_id = request.get("method"), request.get("id")
            if method == "initialize":
                capabilities = request.get("params", {}).get("capabilities", {})
                extensions = capabilities.get("extensions", {}) if isinstance(capabilities, dict) else {}
                MCP_OPENAI_FORM = bool(
                    capabilities.get("mcpServerOpenaiFormElicitation")
                    or (isinstance(extensions, dict) and "openai/form" in extensions)
                )
                result = {
                    "protocolVersion": request.get("params", {}).get("protocolVersion", "2025-06-18"),
                    # Cortex publishes no resources, but advertises an empty resource
                    # catalogue so MCP hosts that probe the standard discovery methods
                    # receive a valid result instead of an avoidable protocol error.
                    "capabilities": {"tools": {}, "resources": {"subscribe": False, "listChanged": False}},
                    "serverInfo": {"name": "cortex", "version": SERVER_VERSION},
                }
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                result = {"tools": [{"name": name, "description": name.replace("_", " "), "inputSchema": schema} for name, (_, schema) in TOOLS.items()]}
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "resources/templates/list":
                result = {"resourceTemplates": []}
            elif method == "tools/call":
                name = request.get("params", {}).get("name")
                if name not in TOOLS:
                    raise ValueError(f"unknown tool '{name}'")
                arguments = request.get("params", {}).get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
                if not str(arguments.get("project_root") or "").strip() and MCP_PROJECT_ROOT is not None:
                    arguments = {**arguments, "project_root": str(MCP_PROJECT_ROOT)}
                requested_root = select_project_root(arguments)
                if MCP_PROJECT_ROOT is not None and requested_root != MCP_PROJECT_ROOT:
                    raise ValueError("Cortex MCP process is already bound to a different project_root")
                MCP_PROJECT_ROOT = requested_root
                value = TOOLS[name][0](arguments)
                structured_error = _tool_result_error(value)
                if structured_error:
                    log_tool_error(request, request_id, line.rstrip("\n"), ValueError(structured_error), value)
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}], "structuredContent": value}
            elif method == "ping":
                result = {}
            else:
                raise ValueError(f"unsupported method '{method}'")
            if request_id is not None:
                respond({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:
            log_tool_error(request, request_id, line.rstrip("\n"), exc)
            if request_id is not None:
                respond({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}})


if __name__ == "__main__":
    main()
