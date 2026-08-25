#!/usr/bin/env python3
"""Source-mode fixtures and an optional bounded Luna-high evaluator.

This evaluator is development evidence only.  It never launches a Cortex
worker on the server's behalf and never substitutes for the audited installed
plugin acceptance flow.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
from collections import namedtuple
import json
import os
import re
import selectors
import secrets
import signal
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "plugins/cortex/scripts/cortex.py"
LIVE_QUESTION_FIXTURES_PATH = ROOT / "plugins/cortex/prompt-evals/live-question-fixtures.json"
sys.path.insert(0, str(ROOT / "plugins/cortex/scripts"))
import cortex  # noqa: E402


LIVE_TIMEOUT_SECONDS = 1800
HEARTBEAT_SECONDS = 15
TERMINATION_GRACE_SECONDS = 10
MAX_STREAM_LINE_BYTES = 64 * 1024
MAX_RETAINED_STREAM_EVENTS = 512
MAX_EMITTED_STREAM_EVENTS = 512
MAX_AUTH_FILE_BYTES = 1024 * 1024
AUTH_ENVIRONMENT_VARIABLES = ("OPENAI_API_KEY", "CODEX_API_KEY")
SAFE_RUNTIME_ENVIRONMENT_VARIABLES = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM")
SAFE_TOOL_NAMES = {
    "start_orchestration", "continue_orchestration", "manage_orchestration",
    "read_worker_result", "complete_attempt", "worker_question", "record_attempt_event",
    "read_dispatch_briefing",
}
SAFE_NATIVE_TOOL_NAMES = {
    "spawn_agent", "wait", "send_message", "followup_task", "interrupt_agent", "list_agents", "close_agent",
}
# The host stream has used these transport-level spellings for the same
# supported native continuation operation.  Normalize only named, harmless
# collaboration operations; unknown names remain ``other`` and no arguments
# or child identifiers ever leave the harness.
SAFE_NATIVE_TOOL_ALIASES = {
    "send_input": "followup_task",
    "resume_agent": "followup_task",
}
SAFE_LEDGER_EVENTS = {
    "delegation", "orchestrate_wave", "attempt_result", "attempt", "evidence", "gate",
    "task_started", "task_completed", "task_blocked", "task_resumed",
}
SAFE_GATE_NAMES = {
    "scope", "plan", "discover", "architecture", "implementation", "qa", "security", "performance",
    "accessibility", "ux", "review", "documentation", "close",
}
SAFE_TASK_STATUSES = {"active", "blocked", "completed", "failed", "unknown"}
SAFE_ATTEMPT_STATUSES = {
    "awaiting_host_spawn", "running", "passed", "failed", "blocked", "idle_resumable",
    "stopped_recoverable", "terminated_unavailable", "completed", "unknown",
}
SAFE_SESSION_STATUSES = {
    "awaiting_spawn", "running", "idle_resumable", "stopped_recoverable",
    "terminated_unavailable", "completed", "unknown",
}
SAFE_NATIVE_AGENT_STATUSES = {
    "pending", "running", "completed", "failed", "cancelled", "shut_down", "unknown",
}
SAFE_QUESTION_MANAGEMENT_OUTCOMES = {
    "question_answered", "awaiting_user", "awaiting_translation", "question_unavailable",
}

BOOTSTRAP_MISSING_MARKER = "CORTEX_WORKER_BOOTSTRAP_MISSING"
BOOTSTRAP_RECOVERY_RESULT = "Bootstrap pair recovered."
BOOTSTRAP_MISSING_FIELDS_PATTERN = r"(?:task_ref|assignment_ref|task_ref,assignment_ref)"


def load_live_question_fixtures(path: Path = LIVE_QUESTION_FIXTURES_PATH) -> dict[str, object]:
    """Load the narrow evaluator-only authority for scripted question answers.

    A live evaluator normally has no authority to answer a durable worker
    question. This separate fixture is the sole exception: it records a
    scenario-owned answer before the run and binds it to a small, explicit
    question scope. Do not infer a policy from the task prompt or from a
    worker's question at runtime.
    """
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("live question fixture is unreadable") from exc
    if not isinstance(decoded, dict) or decoded.get("schema") != "cortex/live-question-fixtures/v1":
        raise RuntimeError("live question fixture schema is invalid")
    scenarios = decoded.get("scenarios")
    if not isinstance(scenarios, dict):
        raise RuntimeError("live question fixture scenarios are invalid")
    for scenario, policy in scenarios.items():
        if not isinstance(scenario, str) or not scenario or not isinstance(policy, dict):
            raise RuntimeError("live question fixture scenario is invalid")
        if set(policy) != {"maximum_questions", "required_question_markers", "preauthorized_answer"}:
            raise RuntimeError("live question fixture policy fields are invalid")
        maximum = policy.get("maximum_questions")
        markers = policy.get("required_question_markers")
        answer = policy.get("preauthorized_answer")
        if (
            type(maximum) is not int or maximum < 1 or maximum > 8
            or not isinstance(markers, list) or not markers
            or not all(isinstance(marker, str) and marker.strip() for marker in markers)
            or len({marker.casefold().strip() for marker in markers}) != len(markers)
            or not isinstance(answer, str) or not answer.strip()
        ):
            raise RuntimeError("live question fixture policy is invalid")
    return decoded


def live_question_policy(scenario: str) -> dict[str, object] | None:
    """Return one fully validated pre-authorized answer policy, if any."""
    scenarios = load_live_question_fixtures().get("scenarios")
    if not isinstance(scenarios, dict):  # Guarded above; preserve type safety.
        raise RuntimeError("live question fixture scenarios are invalid")
    policy = scenarios.get(scenario)
    return dict(policy) if isinstance(policy, dict) else None


def question_matches_pre_authorized_policy(question: object, policy: dict[str, object]) -> bool:
    """Fail closed unless the durable question stays inside fixture authority."""
    if not isinstance(question, dict):
        return False
    text = str(question.get("question") or "").casefold()
    markers = policy.get("required_question_markers")
    return isinstance(markers, list) and all(str(marker).casefold() in text for marker in markers)

RESULT_FAILURE_PATTERNS = (
    ("non-success completion requires an explicit reason", "missing_failure_reason"),
    ("dispatch_ref", "dispatch_identity"),
    ("unanswered blocking worker question", "open_worker_question"),
    ("attempt_result_ref does not belong", "wrong_attempt_result_ref"),
    ("attempt_result_invalid", "attempt_result_invalid"),
)
SAFE_PUBLIC_FAILURE_CODES = {
    "continue_validation_failed", "management_failed", "task_ref_required",
    "task_not_found", "task_ambiguous", "authorization_failed", "validation_failed",
    "orchestrate_validation_failed",
}
SAFE_ORCHESTRATE_FAILURE_PHASES = {
    "preflight", "started", "gates_recorded", "next_wave_prepared", "validation", "dispatch_preflight",
}
# These labels intentionally describe protocol invariants rather than replaying
# a server diagnostic.  They are the complete safe diagnostic vocabulary that
# may leave an isolated evaluator run.
SAFE_CONTINUE_FAILURE_REASONS = (
    ("continue step must match the active relative step", "step_mismatch"),
    ("active wave requires exactly", "cardinality"),
    ("successful result does not match the exact active attempt", "ref_mismatch"),
    ("successful results require attempt_result_ref", "ref_mismatch"),
    ("successful results require a finalized canonical attempt result", "nonfinal"),
    ("unsupported continue fields", "illegal_field"),
    ("unsupported result fields", "illegal_field"),
    ("successful results use attempt_result_ref only", "illegal_field"),
    ("successful results must not include", "illegal_field"),
    ("parallel results require", "illegal_field"),
    ("single active worker slot", "illegal_field"),
)
SAFE_CONTINUE_RESULT_FIELDS = {
    "worker", "attempt_result_ref", "dispatch_ref", "status", "reason", "next_strategy",
}
ATTEMPT_RESULT_FIELDS = ("summary", "findings", "decisions_needed", "unresolved")
NATIVE_TERMINAL_PATTERNS = (
    ("attempt_result_invalid", "attempt_result_invalid"),
    ("attempt_evidence_incomplete", "attempt_evidence_incomplete"),
    ("attempt_result_identity_invalid", "attempt_result_identity_invalid"),
    ("attempt_changed_files_invalid", "artifact_delta_error"),
    ("worker_verification_failed", "test_evidence_error"),
    ("dispatch_briefing_invalid", "dispatch_briefing_error"),
    ("worker_output_language_violation", "output_language_error"),
    ("blocking_question_open", "open_worker_question"),
    ("unresolved_result_questions", "open_worker_question"),
    ("intent_clarification_required", "open_worker_question"),
    ("dispatch briefing", "dispatch_briefing_error"),
    ("briefing digest", "dispatch_briefing_error"),
    ("briefing_digest", "dispatch_briefing_error"),
    ("gate acceptance", "evidence_marker_error"),
    ("gate verification", "evidence_marker_error"),
    ("predecessor review", "evidence_marker_error"),
    ("knowledge reviewed", "evidence_marker_error"),
    ("changed_files", "artifact_delta_error"),
    ("read-only result gate", "artifact_delta_error"),
    ("executed check", "test_evidence_error"),
    ("result.checks", "test_evidence_error"),
    ("mcp", "mcp_access_error"),
    ("permission", "filesystem_access_error"),
    ("unreadable", "filesystem_access_error"),
    ("not found", "filesystem_access_error"),
    ("complete_attempt", "complete_attempt_error"),
)

TEMP_OWNERSHIP_SCHEMA = "cortex/luna-high-temp-owner/v1"
TEMP_OWNERSHIP_MARKER = ".cortex-luna-high-owner.json"
MAX_TEMP_OWNERSHIP_MARKER_BYTES = 2048
TEMP_OWNERSHIP_PURPOSES = frozenset({"evaluation_base", "host_store", "codex_runtime", "failure_metadata"})
TEMP_OWNERSHIP_NONCE_RE = re.compile(r"[0-9a-f]{64}")


TempOwnership = namedtuple(
    "TempOwnership",
    ("run_nonce", "owner_pid", "owner_pgid", "owner_starttime_source", "owner_starttime", "purpose"),
)
TempOwnership.__doc__ = "Exact private identity for one evaluator-created temporary directory."


def configured_codex_home() -> Path:
    """Resolve the current private Codex home without reading its contents."""
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def copy_private_regular_file(source: Path, destination: Path) -> None:
    """Copy a credential file through no-follow descriptors with strict permissions."""
    source_info = os.lstat(source)
    if (
        not stat.S_ISREG(source_info.st_mode)
        or source_info.st_mode & 0o077
        or source_info.st_size > MAX_AUTH_FILE_BYTES
    ):
        raise RuntimeError("Codex authentication source must be a regular non-symlink file")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, os.O_RDONLY | nofollow)
    destination_fd: int | None = None
    completed = False
    try:
        opened_source = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened_source.st_mode)
            or opened_source.st_mode & 0o077
            or opened_source.st_size > MAX_AUTH_FILE_BYTES
            or (opened_source.st_dev, opened_source.st_ino) != (source_info.st_dev, source_info.st_ino)
        ):
            raise RuntimeError("Codex authentication source changed while being opened")
        destination_fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600,
        )
        copied = 0
        while True:
            block = os.read(source_fd, 64 * 1024)
            if not block:
                break
            copied += len(block)
            if copied > MAX_AUTH_FILE_BYTES:
                raise RuntimeError("Codex authentication source exceeds the evaluator size limit")
            remaining = memoryview(block)
            while remaining:
                written = os.write(destination_fd, remaining)
                if written <= 0:
                    raise OSError("unable to write private Codex authentication file")
                remaining = remaining[written:]
        os.fchmod(destination_fd, 0o600)
        completed = True
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)
        if not completed and destination.exists() and not destination.is_symlink():
            destination.unlink()


def _absolute_path(path: Path) -> Path:
    """Normalize a path lexically without resolving a possible symlink target."""
    return Path(os.path.abspath(os.fspath(path)))


def _linux_process_starttime(pid: int) -> str | None:
    """Read Linux ``/proc`` start ticks without accepting a PID-reuse race."""
    if not sys.platform.startswith("linux") or pid < 1:
        return None
    try:
        # ``comm`` can contain spaces and parentheses, so split only after its
        # final closing parenthesis.  Field 22 is offset 19 from field 3.
        suffix = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        value = suffix[19]
    except (OSError, IndexError, ValueError):
        return None
    return value if value.isdecimal() else None


def _current_temp_owner_identity() -> tuple[str, str]:
    """Return a verifiable owner incarnation, or a self-only portable token."""
    starttime = _linux_process_starttime(os.getpid())
    if starttime is not None:
        return "linux_proc_stat", starttime
    # A portable interpreter cannot reliably distinguish a reused PID after it
    # exits.  Its own finally blocks can still remove an exact self-owned path;
    # all cross-process cleanup fails closed instead.
    return "portable_self_only", str(time.monotonic_ns())


def _marker_payload(ownership: TempOwnership) -> dict[str, object]:
    return {
        "schema": TEMP_OWNERSHIP_SCHEMA,
        "run_nonce": ownership.run_nonce,
        "owner_pid": ownership.owner_pid,
        "owner_pgid": ownership.owner_pgid,
        "owner_starttime_source": ownership.owner_starttime_source,
        "owner_starttime": ownership.owner_starttime,
        "purpose": ownership.purpose,
    }


def _validate_ownership_payload(value: object) -> TempOwnership | None:
    """Parse a bounded marker shape without returning its opaque nonce."""
    if not isinstance(value, dict) or set(value) != {
        "schema", "run_nonce", "owner_pid", "owner_pgid", "owner_starttime_source", "owner_starttime", "purpose",
    }:
        return None
    nonce = value.get("run_nonce")
    pid = value.get("owner_pid")
    pgid = value.get("owner_pgid")
    source = value.get("owner_starttime_source")
    starttime = value.get("owner_starttime")
    purpose = value.get("purpose")
    if (
        value.get("schema") != TEMP_OWNERSHIP_SCHEMA
        or not isinstance(nonce, str)
        or TEMP_OWNERSHIP_NONCE_RE.fullmatch(nonce) is None
        or type(pid) is not int
        or pid < 1
        or type(pgid) is not int
        or pgid < 1
        or source not in {"linux_proc_stat", "portable_self_only"}
        or not isinstance(starttime, str)
        or not starttime.isdecimal()
        or purpose not in TEMP_OWNERSHIP_PURPOSES
    ):
        return None
    return TempOwnership(
        run_nonce=nonce,
        owner_pid=pid,
        owner_pgid=pgid,
        owner_starttime_source=source,
        owner_starttime=starttime,
        purpose=purpose,
    )


def _lstat_private_directory(path: Path, *, allow_parent_owner: bool = False) -> os.stat_result:
    """Validate one non-symlink private directory without dereferencing it."""
    try:
        information = os.lstat(path)
    except OSError as exc:
        raise RuntimeError("refusing an unavailable evaluator temporary directory") from exc
    if not stat.S_ISDIR(information.st_mode) or path.is_symlink():
        raise RuntimeError("refusing a non-directory or symlink evaluator temporary path")
    if not allow_parent_owner and information.st_uid != os.geteuid():
        raise RuntimeError("refusing an evaluator temporary directory with an unexpected owner")
    if not allow_parent_owner and stat.S_IMODE(information.st_mode) & 0o077:
        raise RuntimeError("refusing an evaluator temporary directory with unsafe permissions")
    return information


def _write_private_ownership_marker(directory: Path, ownership: TempOwnership) -> None:
    """Write an exact, private marker once; never replace an existing marker."""
    _lstat_private_directory(directory)
    marker = directory / TEMP_OWNERSHIP_MARKER
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(marker, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
            stream.write(json.dumps(_marker_payload(ownership), sort_keys=True, separators=(",", ":")))
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, 0o600)
    except OSError as exc:
        raise RuntimeError("unable to create evaluator temporary ownership marker") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def create_owned_temp_directory(parent: Path, *, prefix: str, purpose: str) -> tuple[Path, TempOwnership]:
    """Create one private evaluator directory with a cryptographically unique owner marker."""
    if purpose not in TEMP_OWNERSHIP_PURPOSES:
        raise ValueError("unsupported evaluator temporary directory purpose")
    parent = _absolute_path(parent)
    _lstat_private_directory(parent, allow_parent_owner=True)
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    try:
        os.chmod(directory, 0o700)
        source, starttime = _current_temp_owner_identity()
        ownership = TempOwnership(
            run_nonce=secrets.token_hex(32),
            owner_pid=os.getpid(),
            owner_pgid=os.getpgrp(),
            owner_starttime_source=source,
            owner_starttime=starttime,
            purpose=purpose,
        )
        _write_private_ownership_marker(directory, ownership)
        return directory, ownership
    except Exception:
        # This path was created by this process just above.  It has not yet
        # acquired an ownership marker, so fail closed for every other path.
        if directory.parent == parent and directory.exists() and not directory.is_symlink():
            shutil.rmtree(directory)
        raise


def _read_private_ownership_marker(directory: Path) -> TempOwnership:
    """Read one no-follow marker and reject races, links, or malformed data."""
    marker = directory / TEMP_OWNERSHIP_MARKER
    try:
        before = os.lstat(marker)
    except OSError as exc:
        raise RuntimeError("refusing an evaluator temporary directory without an ownership marker") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o077
        or before.st_size < 2
        or before.st_size > MAX_TEMP_OWNERSHIP_MARKER_BYTES
        or marker.is_symlink()
    ):
        raise RuntimeError("refusing an unsafe evaluator temporary ownership marker")
    descriptor: int | None = None
    try:
        descriptor = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError("refusing a changed evaluator temporary ownership marker")
        raw = os.read(descriptor, MAX_TEMP_OWNERSHIP_MARKER_BYTES + 1)
    except OSError as exc:
        raise RuntimeError("unable to read evaluator temporary ownership marker") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(raw) > MAX_TEMP_OWNERSHIP_MARKER_BYTES:
        raise RuntimeError("refusing an oversized evaluator temporary ownership marker")
    try:
        parsed = _validate_ownership_payload(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    if parsed is None:
        raise RuntimeError("refusing a malformed evaluator temporary ownership marker")
    return parsed


def _same_ownership(left: TempOwnership, right: TempOwnership) -> bool:
    return secrets.compare_digest(left.run_nonce, right.run_nonce) and left == right


def _owner_liveness(ownership: TempOwnership) -> str:
    """Classify only exact process ownership; PID reuse is never treated as stale."""
    if ownership.owner_starttime_source == "linux_proc_stat":
        current_starttime = _linux_process_starttime(ownership.owner_pid)
        if current_starttime is None:
            try:
                os.kill(ownership.owner_pid, 0)
            except ProcessLookupError:
                return "exited"
            except PermissionError:
                return "unknown"
            return "mismatched"
        if not secrets.compare_digest(current_starttime, ownership.owner_starttime):
            return "mismatched"
        try:
            if os.getpgid(ownership.owner_pid) != ownership.owner_pgid:
                return "mismatched"
        except ProcessLookupError:
            return "exited"
        except OSError:
            return "unknown"
        return "self" if ownership.owner_pid == os.getpid() else "live"
    if ownership.owner_pid == os.getpid() and ownership.owner_pgid == os.getpgrp():
        return "self"
    try:
        os.kill(ownership.owner_pid, 0)
    except ProcessLookupError:
        return "unknown"
    except PermissionError:
        return "unknown"
    return "live"


def remove_private_runtime_home(
    runtime_home: Path,
    base: Path,
    ownership: TempOwnership,
    *,
    allow_current_owner: bool = False,
) -> None:
    """Remove one exact evaluator-owned directory, never a selected/globbed peer.

    Only a marker matching the caller-held nonce is eligible.  A live foreign
    owner, an identity mismatch, and every portable cross-process marker are
    rejected.  Internal evaluator ``finally`` blocks set ``allow_current_owner``
    only for their exact current-process allocation.
    """
    runtime_home = _absolute_path(runtime_home)
    base = _absolute_path(base)
    _lstat_private_directory(base, allow_parent_owner=True)
    if runtime_home.parent != base:
        raise RuntimeError("refusing to remove an evaluator temporary directory outside its exact base")
    _lstat_private_directory(runtime_home)
    observed = _read_private_ownership_marker(runtime_home)
    if not _same_ownership(observed, ownership):
        raise RuntimeError("refusing an evaluator temporary directory with mismatched ownership")
    liveness = _owner_liveness(observed)
    if liveness == "self" and allow_current_owner:
        pass
    elif liveness == "exited":
        pass
    elif liveness in {"self", "live"}:
        raise RuntimeError("refusing to remove a live evaluator temporary directory")
    else:
        raise RuntimeError("refusing an evaluator temporary directory with unverifiable owner identity")
    shutil.rmtree(runtime_home)


@contextlib.contextmanager
def isolated_cortex_host_store(base: Path, *, keep: bool = False):
    """Provide one disposable, mode-0700 host control store for source checks."""
    base_info = os.lstat(base)
    if not stat.S_ISDIR(base_info.st_mode) or base.is_symlink():
        raise RuntimeError("evaluator base must be a non-symlink directory")
    host_store, host_ownership = create_owned_temp_directory(
        base, prefix="cortex-luna-high-host-store-", purpose="host_store",
    )
    original = os.environ.get("CORTEX_HOST_STATE_DIR")
    os.environ["CORTEX_HOST_STATE_DIR"] = str(host_store)
    try:
        yield host_store
    finally:
        if original is None:
            os.environ.pop("CORTEX_HOST_STATE_DIR", None)
        else:
            os.environ["CORTEX_HOST_STATE_DIR"] = original
        if not keep and (host_store.exists() or host_store.is_symlink()):
            remove_private_runtime_home(host_store, base, host_ownership, allow_current_owner=True)


@contextlib.contextmanager
def isolated_codex_runtime(base: Path, *, host_store: Path | None = None):
    """Provide an ephemeral 0700 Codex home without loading global configuration."""
    base_info = os.lstat(base)
    if not stat.S_ISDIR(base_info.st_mode) or base.is_symlink():
        raise RuntimeError("evaluator base must be a non-symlink directory")
    runtime_home, runtime_ownership = create_owned_temp_directory(
        base, prefix="cortex-luna-high-codex-", purpose="codex_runtime",
    )
    runtime_tmp = runtime_home / "tmp"
    runtime_cache = runtime_home / "cache"
    runtime_config = runtime_home / "config"
    runtime_data = runtime_home / "data"
    runtime_state = runtime_home / "state"
    for directory in (runtime_tmp, runtime_cache, runtime_config, runtime_data, runtime_state):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    environment = {
        name: value
        for name in SAFE_RUNTIME_ENVIRONMENT_VARIABLES
        if (value := os.environ.get(name))
    }
    environment["HOME"] = str(runtime_home)
    environment["CODEX_HOME"] = str(runtime_home)
    environment["TMPDIR"] = str(runtime_tmp)
    environment["XDG_CACHE_HOME"] = str(runtime_cache)
    environment["XDG_CONFIG_HOME"] = str(runtime_config)
    environment["XDG_DATA_HOME"] = str(runtime_data)
    environment["XDG_STATE_HOME"] = str(runtime_state)
    if host_store is not None:
        environment["CORTEX_HOST_STATE_DIR"] = str(host_store)
    try:
        environment_auth = next(
            (name for name in AUTH_ENVIRONMENT_VARIABLES if os.environ.get(name)), None,
        )
        if environment_auth is not None:
            environment[environment_auth] = os.environ[environment_auth]
        else:
            source = configured_codex_home() / "auth.json"
            copy_private_regular_file(source, runtime_home / "auth.json")
        yield environment
    finally:
        if runtime_home.exists() or runtime_home.is_symlink():
            remove_private_runtime_home(runtime_home, base, runtime_ownership, allow_current_owner=True)


def emit_live_progress(scenario: str, event: str, **metadata: object) -> None:
    """Stream one machine-readable, privacy-preserving live-eval progress event."""
    payload = {"type": "cortex_live_progress", "scenario": scenario, "event": event, **metadata}
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


def safe_tool_name(value: object) -> str:
    """Keep only public Cortex lifecycle names from an untrusted host event."""
    candidate = str(value or "").rsplit("__", 1)[-1]
    return candidate if candidate in SAFE_TOOL_NAMES else "other"


def safe_status(value: object, allowed: set[str]) -> str:
    candidate = str(value or "unknown")
    return candidate if candidate in allowed else "unknown"


def structured_ok(value: object) -> bool | None:
    """Read an ``ok`` flag without retaining arbitrary response content."""
    queue = [value]
    visited = 0
    while queue and visited < 32:
        item = queue.pop(0)
        visited += 1
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("ok"), bool):
            return item["ok"]
        for key in ("structuredContent", "structured_content", "result"):
            if key in item:
                queue.append(item[key])
    return None


def bounded_mapping(value: object) -> dict[str, object]:
    """Decode a tool object without preserving arbitrary argument values."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or len(value.encode("utf-8", errors="ignore")) > MAX_STREAM_LINE_BYTES:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def public_response_mapping(value: object) -> dict[str, object]:
    """Find the bounded public response envelope without retaining its values."""
    queue: list[object] = [value]
    visited = 0
    while queue and visited < 32:
        candidate = queue.pop(0)
        visited += 1
        if not isinstance(candidate, dict):
            continue
        if any(key in candidate for key in ("ok", "code", "diagnostics")):
            return candidate
        for key in ("structuredContent", "structured_content", "result"):
            if key in candidate:
                queue.append(candidate[key])
    return {}


def safe_question_management_metadata(item: dict[str, object], result: object) -> dict[str, object] | None:
    """Project a question-management call without retaining its identity or answer.

    The evaluator needs to prove the ordering boundary between a durable answer
    and a native follow-up, but must not retain either the durable question ref
    or the user-provided answer.  ``resume_contract`` is consequently a boolean
    derived transiently from the server response, not a copy of that response.
    """
    if safe_tool_name(item.get("tool") or item.get("name")) != "manage_orchestration":
        return None
    arguments = bounded_mapping(item.get("arguments", item.get("input")))
    if str(arguments.get("intent") or "").strip().lower().replace("-", "_") != "question":
        return None
    response = public_response_mapping(result)
    outcome = str(response.get("outcome") or "")
    safe_outcome = outcome if outcome in SAFE_QUESTION_MANAGEMENT_OUTCOMES else "other"
    metadata: dict[str, object] = {
        "management_intent": "question",
        "outcome": safe_outcome,
    }
    if safe_outcome == "question_answered":
        # Validate only the public contract shape while it is in scope. Do not
        # classify a resume from mutable explanatory prose, and do not copy its
        # question/batch reference, attempt identity, or profile into retained
        # telemetry.
        metadata["resume_contract"] = _has_safe_resume_contract(response.get("resume_contract"))
    return metadata


def safe_terminal_management_metadata(item: dict[str, object], result: object) -> dict[str, object] | None:
    """Classify only the two fixed dispatch-scoped terminal cleanup intents."""
    if safe_tool_name(item.get("tool") or item.get("name")) != "manage_orchestration":
        return None
    arguments = bounded_mapping(item.get("arguments", item.get("input")))
    intent = str(arguments.get("intent") or "").strip()
    expected = {
        "finalize_bootstrap_failure": "bootstrap_missing_identity",
        "finalize_worker_failure": "worker_nonretryable_terminal",
    }
    if intent not in expected:
        return None
    payload = bounded_mapping(arguments.get("payload"))
    if set(payload) != {"dispatch_ref", "reason_code"} or payload.get("reason_code") != expected[intent]:
        return {"management_intent": intent, "terminal_cleanup": False}
    response = public_response_mapping(result)
    failure = response.get("failure") if isinstance(response.get("failure"), dict) else {}
    expected_code = "bootstrap_terminal_failure" if intent == "finalize_bootstrap_failure" else "worker_terminal_failure"
    cleanup = response.get("ok") is True or (
        response.get("ok") is False and failure.get("code") == expected_code
    )
    return {"management_intent": intent, "terminal_cleanup": cleanup}


def safe_dispatch_authorization_metadata(item: dict[str, object], result: object) -> dict[str, object] | None:
    """Retain only the count of dispatches authorized by a Cortex response.

    The live evaluator deliberately does not retain dispatch arguments, task
    names, result refs, or child identities.  A bounded dispatch count is
    enough to reject an extra generic host spawn: it was never authorized by
    a successful ``start_orchestration`` or ``continue_orchestration`` result
    and therefore cannot be treated as a Cortex worker cycle.
    """
    tool = safe_tool_name(item.get("tool") or item.get("name"))
    if tool not in {"start_orchestration", "continue_orchestration"}:
        return None
    response = public_response_mapping(result)
    if response.get("ok") is not True:
        return None
    dispatches = response.get("dispatches")
    if not isinstance(dispatches, list) or len(dispatches) > 32:
        return None
    if not all(isinstance(dispatch, dict) for dispatch in dispatches):
        return None
    repair_messages = [dispatch.get("bootstrap_repair_message") for dispatch in dispatches]
    if not all(isinstance(message, str) and message for message in repair_messages):
        return None
    return {
        "authorized_dispatch_count": len(dispatches),
        # A one-way digest proves that a later native follow-up byte-copied a
        # server-built message without retaining a bearer-bearing payload.
        "bootstrap_repair_message_digests": [
            hashlib.sha256(str(message).encode("utf-8")).hexdigest()
            for message in repair_messages
        ],
    }


def safe_native_bootstrap_repair_metadata(item: dict[str, object]) -> dict[str, object] | None:
    """Digest only a native follow-up message; never retain its text or target."""
    if safe_tool_name(item.get("tool") or item.get("name")) not in {"followup_task", "send_input", "resume_agent"}:
        return None
    arguments = bounded_mapping(item.get("arguments", item.get("input")))
    message = arguments.get("message")
    if not isinstance(message, str) or not message:
        return None
    return {"bootstrap_repair_message_digest": hashlib.sha256(message.encode("utf-8")).hexdigest()}


def safe_planning_repair_metadata(item: dict[str, object], result: object) -> dict[str, object] | None:
    """Audit planner repair call shape without retaining payload values.

    Focused live repair scenarios must prove that repair calls are patch-only
    and that caller-correctable retries reissue the same pending contract.
    Inspect transient MCP arguments and responses here, then retain only
    booleans/counts; no plan values, digests, refs, capsules, or patch values
    are written to evaluator telemetry.
    """
    if safe_tool_name(item.get("tool") or item.get("name")) != "complete_attempt":
        return None
    arguments = bounded_mapping(item.get("arguments", item.get("input")))
    plan = arguments.get("plan")
    outcome = arguments.get("outcome")
    patches = arguments.get("patches")
    response = public_response_mapping(result)
    repair = response.get("repair") if isinstance(response.get("repair"), dict) else {}
    patch_paths = repair.get("patch_paths") if isinstance(repair.get("patch_paths"), list) else []
    supplied_capsule = arguments.get("repair_capsule")
    returned_capsule = repair.get("repair_capsule")
    supplied_digest = arguments.get("base_payload_digest")
    returned_digest = repair.get("base_payload_digest")
    return {
        "has_planning": isinstance(plan, dict),
        "has_outcome": isinstance(outcome, dict),
        "has_base_payload_digest": isinstance(arguments.get("base_payload_digest"), str)
        and bool(str(arguments.get("base_payload_digest") or "").strip()),
        "has_patches": isinstance(patches, list) and bool(patches),
        "patch_count": len(patches) if isinstance(patches, list) and len(patches) <= 32 else None,
        "patch_paths_are_json_pointers": (
            isinstance(patches, list)
            and all(isinstance(patch, dict) and str(patch.get("path") or "").startswith("/") for patch in patches)
        ),
        "repair_contract_exposed": bool(patch_paths)
        and repair.get("retry_strategy") == "repair_patch_only"
        and repair.get("retryable") is True,
        "same_pending_contract_reissued": (
            isinstance(supplied_capsule, str)
            and isinstance(returned_capsule, str)
            and supplied_capsule == returned_capsule
            and isinstance(supplied_digest, str)
            and supplied_digest == returned_digest
        ),
        "accepted": response.get("ok") is True,
    }


def _has_safe_resume_contract(value: object) -> bool:
    """Return whether an answer response has one canonical public resume shape.

    This consumes opaque values transiently and retains only a boolean. A
    single question and a batch differ only in their exclusive durable ref and
    corresponding poll action.
    """
    if not isinstance(value, dict):
        return False
    keys = set(value)
    common = {"attempt_id", "profile", "poll_action"}
    ref_keys = keys & {"question_ref", "batch_ref"}
    if len(ref_keys) != 1 or keys != common | ref_keys:
        return False
    ref_key = next(iter(ref_keys))
    if not all(isinstance(value.get(key), str) and value[key].strip() for key in (*common, ref_key)):
        return False
    return (
        (ref_key == "question_ref" and value.get("poll_action") == "poll")
        or (ref_key == "batch_ref" and value.get("poll_action") == "poll_batch")
    )


def safe_failure_text(value: object) -> str:
    """Use response text transiently to classify it, never return it."""
    queue: list[object] = [value]
    fragments: list[str] = []
    visited = 0
    while queue and visited < 64 and len(fragments) < 8:
        candidate = queue.pop(0)
        visited += 1
        if isinstance(candidate, dict):
            queue.extend(candidate.values())
        elif isinstance(candidate, (list, tuple)):
            queue.extend(candidate)
        elif isinstance(candidate, str):
            fragments.append(candidate[:2048].lower())
    return "\n".join(fragments)


def safe_public_failure_metadata(item: dict[str, object], result: object) -> dict[str, object] | None:
    """Project a failed public call to machine-safe protocol diagnostics only.

    In particular this does not retain task references, result references,
    summaries, diagnostic prose, worker payloads, or arbitrary argument keys.
    ``canonical_ref_match`` is emitted only when the server rejection itself
    establishes a mismatch; otherwise its value is ``None`` rather than an
    invented positive assertion.
    """
    response = public_response_mapping(result)
    code = str(response.get("code") or "")
    safe_code = code if code in SAFE_PUBLIC_FAILURE_CODES else "unknown"
    text = safe_failure_text(response)
    tool = safe_tool_name(item.get("tool") or item.get("name"))
    metadata: dict[str, object] = {"error_code": safe_code}
    phase = str(response.get("phase") or "")
    if phase in SAFE_ORCHESTRATE_FAILURE_PHASES:
        metadata["phase"] = phase
    if tool != "continue_orchestration":
        metadata["reason"] = "unclassified"
        return metadata
    reason = "unclassified"
    for pattern, category in SAFE_CONTINUE_FAILURE_REASONS:
        if pattern in text:
            reason = category
            break
    arguments = bounded_mapping(item.get("arguments", item.get("input")))
    requested_step = arguments.get("step")
    metadata["reason"] = reason
    metadata["requested_step"] = requested_step if type(requested_step) is int and 0 <= requested_step <= 999 else None
    expected = re.search(r"active relative step\s+(\d{1,3})", text)
    metadata["expected_step"] = int(expected.group(1)) if expected else None
    results = arguments.get("results")
    safe_results = results if isinstance(results, list) and len(results) <= 128 else []
    metadata["result_count"] = len(safe_results) if isinstance(results, list) and len(results) <= 128 else None
    field_names = {
        key
        for item_result in safe_results
        for key in (item_result.keys() if isinstance(item_result, dict) else ())
        if key in SAFE_CONTINUE_RESULT_FIELDS
    }
    metadata["result_field_names"] = sorted(field_names)
    metadata["canonical_ref_match"] = False if reason == "ref_mismatch" else None
    return metadata


def is_gates_recorded_lifecycle_failure(event: object) -> bool:
    """Fail closed after a post-gate public lifecycle failure.

    The evaluator must not let an untrusted native parent turn a recoverable
    post-gate failure into an invented pipeline replacement.  This consumes
    only the allow-listed sanitized event envelope; it never inspects tool
    arguments, references, diagnostics, or worker content.
    """
    if not isinstance(event, dict):
        return False
    if (
        event.get("event") != "cortex_mcp_call"
        or event.get("tool") not in {"continue_orchestration", "manage_orchestration"}
        or event.get("status") != "completed"
        or event.get("ok") is not False
    ):
        return False
    failure = event.get("failure")
    return (
        isinstance(failure, dict)
        and failure.get("error_code") == "orchestrate_validation_failed"
        and failure.get("phase") == "gates_recorded"
    )


def classified_result_failure(value: object) -> str | None:
    """Classify a known lifecycle rejection without retaining response text."""
    queue = [value]
    visited = 0
    while queue and visited < 64:
        item = queue.pop(0)
        visited += 1
        if isinstance(item, dict):
            queue.extend(item.values())
        elif isinstance(item, (list, tuple)):
            queue.extend(item)
        elif isinstance(item, str):
            lowered = item.lower()
            for pattern, category in RESULT_FAILURE_PATTERNS:
                if pattern in lowered:
                    return category
    return None


def evaluation_planning_manifest(task_dir: Path | None) -> dict[str, object]:
    """Read the optional planner projection without turning absence into a crash.

    ``planning_current`` is produced only after the server accepts a valid
    Planner payload.  A failed or interrupted Planner therefore legitimately
    leaves no document behind.  The live evaluator must report the resulting
    scenario checks as FAIL and retain no diagnostic payload, rather than
    dereferencing ``None`` while building its safe audit.
    """
    if task_dir is None:
        return {}
    try:
        value = cortex.current_planning_manifest(task_dir)
    except (OSError, ValueError, sqlite3.Error):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def planner_repair_storage_audit(
    ledger: Path, task_dir: Path | None, state: dict[str, object],
) -> dict[str, object]:
    """Audit the durable malformed-draft -> repaired-plan transition.

    Worker MCP arguments are intentionally not retained by this evaluator, so
    the live proof comes from the ledger: one immutable rejected draft, one
    canonical result, and a current plan whose fields that were valid in the
    rejected draft are unchanged.  Only aggregate booleans/counts leave this
    helper.
    """
    if task_dir is None:
        return {"rejected_draft_count": 0, "valid_fields_preserved": False, "only_diagnostic_fields_repaired": False}
    task_id = str(state.get("task_id") or "")
    drafts = cortex.db_list_task_documents(ledger, task_id, "planning_rejected_draft:")
    if len(drafts) != 1:
        return {"rejected_draft_count": len(drafts), "valid_fields_preserved": False, "only_diagnostic_fields_repaired": False}
    draft = drafts[0][1]
    current = evaluation_planning_manifest(task_dir)
    if not isinstance(draft, dict) or not isinstance(draft.get("planning"), dict) or not isinstance(current, dict):
        return {"rejected_draft_count": 1, "valid_fields_preserved": False, "only_diagnostic_fields_repaired": False}
    planning = draft["planning"]
    package_rows: list[dict[str, object]] = []
    for package in current.get("work_packages") or []:
        if not isinstance(package, dict) or not package.get("artifact_ref"):
            return {"rejected_draft_count": 1, "valid_fields_preserved": False, "only_diagnostic_fields_repaired": False}
        try:
            content = cortex.db_read_artifact_content(ledger, task_id, str(package["artifact_ref"]))
            decoded = json.loads(content) if isinstance(content, str) else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {"rejected_draft_count": 1, "valid_fields_preserved": False, "only_diagnostic_fields_repaired": False}
        if not isinstance(decoded, dict) or not isinstance(decoded.get("package"), dict):
            return {"rejected_draft_count": 1, "valid_fields_preserved": False, "only_diagnostic_fields_repaired": False}
        package_rows.append(decoded["package"])
    repaired = {
        "overview": current.get("overview"),
        "work_packages": package_rows,
        "requirement_coverage": current.get("requirement_coverage") or [],
        "recommendation": current.get("recommendation", "approve"),
        "recommendation_rationale": current.get("recommendation_rationale", ""),
        "resolved_questions": current.get("resolved_questions") or [],
        "risks": current.get("risks") or [],
    }
    valid_fields_preserved = (
        repaired["overview"] == planning.get("overview")
        and len(package_rows) == len(planning.get("work_packages") or [])
        and all(
            all(package_rows[index].get(field) == original.get(field) for field in ("id", "title", "objective", "allowed_paths"))
            and len(package_rows[index].get("microtasks") or []) == len(original.get("microtasks") or [])
            and all(
                all(
                    (package_rows[index].get("microtasks") or [])[micro_index].get(field)
                    == (original.get("microtasks") or [])[micro_index].get(field)
                    for field in ("id", "title", "objective", "profile", "allowed_paths", "acceptance_criteria", "verification")
                )
                for micro_index in range(len(original.get("microtasks") or []))
            )
            for index, original in enumerate(planning.get("work_packages") or [])
        )
        and len(repaired["requirement_coverage"]) == len(planning.get("requirement_coverage") or [])
        and all(
            repaired["requirement_coverage"][index].get(field) == original.get(field)
            for index, original in enumerate(planning.get("requirement_coverage") or [])
            for field in ("requirement", "verification", "status")
        )
    )
    diagnostic_paths = cortex.planning_diagnostic_patch_paths(draft.get("diagnostics") or [])
    only_diagnostic_fields_repaired = (
        valid_fields_preserved
        and len(repaired["work_packages"]) == 1
        and repaired["work_packages"][0].get("gates") != (planning.get("work_packages") or [{}])[0].get("gates")
        and repaired["requirement_coverage"][0].get("plan_refs") != (planning.get("requirement_coverage") or [{}])[0].get("plan_refs")
        and "/work_packages/0/gates" in diagnostic_paths
        and "/requirement_coverage/0/plan_refs" in diagnostic_paths
    )
    return {
        "rejected_draft_count": 1,
        "valid_fields_preserved": bool(valid_fields_preserved),
        "only_diagnostic_fields_repaired": bool(only_diagnostic_fields_repaired),
    }


def classified_native_outcome(value: object) -> str | None:
    """Return only a safe durable-result class from native agent state."""
    if not isinstance(value, dict):
        return None
    saw_message = False
    for agent in value.values():
        if not isinstance(agent, dict):
            continue
        message = str(agent.get("message") or "").strip()
        if not message:
            continue
        saw_message = True
        if message == "ATTEMPT_COMPLETED":
            return "attempt_completed"
        if message == "CORTEX_ATTEMPT_FAILED retryable=false":
            return "attempt_failed_nonretryable"
        if message.startswith("QUESTION_RECORDED question_ref="):
            return "question_recorded"
        if re.fullmatch(
            BOOTSTRAP_MISSING_MARKER + r" missing_fields=\[" + BOOTSTRAP_MISSING_FIELDS_PATTERN + r"\] retryable=true",
            message,
        ):
            return "bootstrap_missing"
        lowered = message.lower()
        for pattern, category in NATIVE_TERMINAL_PATTERNS:
            if pattern in lowered:
                return category
    return "other_terminal_message" if saw_message else None


def sanitize_codex_stream_line(line: str) -> dict[str, object]:
    """Classify a Codex JSON event while never returning its text or arguments."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {"event": "host_output", "format": "non_json"}
    if not isinstance(payload, dict):
        return {"event": "host_output", "format": "other"}
    item = payload.get("item")
    if not isinstance(item, dict):
        event_type = str(payload.get("type") or "")
        if event_type in {"thread.started", "thread.completed"}:
            return {"event": "parent_started" if event_type.endswith("started") else "parent_completed"}
        if event_type.startswith("turn."):
            return {"event": "parent_turn", "status": "started" if event_type.endswith("started") else "completed"}
        return {"event": "host_event"}
    item_type = str(item.get("type") or "")
    if item_type == "collab_tool_call":
        tool = str(item.get("tool") or "")
        normalized_tool = SAFE_NATIVE_TOOL_ALIASES.get(tool, tool)
        safe_native: dict[str, object] = {
            "event": "native_tool_call",
            "tool": normalized_tool if normalized_tool in SAFE_NATIVE_TOOL_NAMES else "other",
            "status": safe_status(item.get("status"), {"started", "in_progress", "completed", "failed"}),
        }
        repair_metadata = safe_native_bootstrap_repair_metadata(item)
        if repair_metadata is not None:
            safe_native.update(repair_metadata)
        states = item.get("agents_states")
        native_outcome = classified_native_outcome(states)
        if native_outcome is not None:
            safe_native["outcome"] = native_outcome
        if isinstance(states, dict):
            statuses: dict[str, int] = {}
            for agent in states.values():
                if not isinstance(agent, dict):
                    continue
                status = safe_status(agent.get("status"), SAFE_NATIVE_AGENT_STATUSES)
                statuses[status] = statuses.get(status, 0) + 1
            if statuses:
                safe_native["agent_statuses"] = statuses
        return safe_native
    if item_type not in {"mcp_tool_call", "tool_call"}:
        if item_type in {"agent_message", "reasoning", "message"}:
            return {"event": "parent_activity", "kind": "model"}
        if item_type in {"command_execution", "function_call"}:
            return {"event": "parent_activity", "kind": "host_tool"}
        return {"event": "parent_activity", "kind": "other"}
    status = str(item.get("status") or "unknown")
    result = item.get("result")
    safe: dict[str, object] = {
        "event": "cortex_mcp_call",
        "tool": safe_tool_name(item.get("tool") or item.get("name")),
        "status": status if status in {"started", "in_progress", "completed", "failed"} else "unknown",
    }
    if safe["status"] in {"completed", "failed"}:
        ok = structured_ok(result)
        if ok is not None:
            safe["ok"] = ok
        question_metadata = safe_question_management_metadata(item, result)
        if question_metadata is not None:
            safe.update(question_metadata)
        terminal_metadata = safe_terminal_management_metadata(item, result)
        if terminal_metadata is not None:
            safe.update(terminal_metadata)
        dispatch_metadata = safe_dispatch_authorization_metadata(item, result)
        if dispatch_metadata is not None:
            safe.update(dispatch_metadata)
        repair_metadata = safe_planning_repair_metadata(item, result)
        if repair_metadata is not None:
            safe["planning_repair"] = repair_metadata
        if ok is False:
            failure = safe_public_failure_metadata(item, result)
            if failure is not None:
                safe["failure"] = failure
            failure_class = classified_result_failure(result)
            if failure_class is not None:
                safe["failure_class"] = failure_class
    return safe


def observed_native_lifecycle(events: list[dict[str, object]], *, workers: int = 4) -> bool:
    """Prove source-mode native worker cycles without asserting trusted hook binding.

    Source-mode runs deliberately use ``--ignore-user-config``.  Their native
    spawn events are observable, but the installed trusted SubagentStart hook
    is absent, so an attempt cannot honestly claim durable child-id/model
    binding.  Codex may emit an adjacent duplicate completion observation for
    a single native operation; collapse only those duplicates before checking
    the lifecycle order.
    """
    operations: list[tuple[str, str | None]] = []
    for event in events:
        if (
            event.get("event") != "native_tool_call"
            or event.get("status") != "completed"
            or event.get("tool") not in {"spawn_agent", "wait", "close_agent"}
        ):
            continue
        tool = str(event["tool"])
        # ``wait`` is the only lifecycle operation whose terminal outcome is
        # semantically relevant.  Hosts commonly echo that same outcome when
        # closing an already-completed child; treating it as a distinct close
        # operation made a successful source-mode cycle look malformed.
        operation = (tool, str(event.get("outcome") or "") or None if tool == "wait" else None)
        if not operations or operation != operations[-1]:
            operations.append(operation)
    if len(operations) < workers * 3 or len(operations) % 3:
        return False
    return all(
        operations[index:index + 3] == [
            ("spawn_agent", None), ("wait", "attempt_completed"), ("close_agent", None),
        ]
        for index in range(0, len(operations), 3)
    )


def safe_native_terminal_audit(events: list[dict[str, object]]) -> dict[str, object]:
    """Audit the public terminal handling of every observable native worker.

    Source-mode telemetry intentionally has no child identifiers or result
    references.  It can nevertheless prove, without retaining either, that
    each observed terminal wait was followed by a successful canonical
    ``read_worker_result`` and a successful server-derived
    ``continue_orchestration`` audit before the child was closed.  A native
    ``ATTEMPT_COMPLETED`` marker is useful telemetry, but it is not the
    authority: hosts can instead expose an unclassified terminal message.
    Such a wait is *provisional* until this exact aggregate sequence reaches
    a successful server read, continuation, and close.  The counters are an
    aggregate proof only: durable attempt/result identity is checked
    independently by :func:`safe_terminal_result_audit` below.

    Adjacent identical host observations are ambiguous: source-mode telemetry
    cannot distinguish a transport echo from a second parallel child.  Count
    neither as completion evidence and fail closed.  The release sequential
    gate therefore accepts only an unambiguous one-to-one aggregate mapping;
    parallel identity proof requires the separate trusted-hook integration
    environment.
    """
    spawned = waited = read = continued = closed = 0
    pending_reads = pending_continuations = pending_closes = 0
    violation_count = 0
    ambiguous_observations = 0
    authorized_dispatches = 0
    dispatch_authorization_observed = False
    unmatched_native_spawns = 0
    last_operation: str | None = None
    for event in events:
        operation: str | None = None
        if event.get("event") == "native_tool_call" and event.get("status") == "completed":
            tool = str(event.get("tool") or "")
            outcome = str(event.get("outcome") or "")
            if tool == "spawn_agent":
                operation = "spawn"
            elif tool == "wait":
                if outcome == "attempt_completed":
                    operation = "wait_result"
                elif outcome == "other_terminal_message":
                    # The native child did stop, but its retained message is
                    # deliberately not evidence of an AttemptResult.  Keep
                    # this as a provisional wait and require the public
                    # server read below to prove the canonical result.
                    operation = "wait_provisional_result"
                elif outcome:
                    operation = "wait_terminal_other"
            elif tool == "close_agent":
                operation = "close"
        elif event.get("event") == "cortex_mcp_call" and event.get("status") == "completed":
            tool = str(event.get("tool") or "")
            authorized_count = event.get("authorized_dispatch_count")
            if (
                tool in {"start_orchestration", "continue_orchestration"}
                and type(authorized_count) is int
                and 0 <= authorized_count <= 32
                and event.get("ok") is True
            ):
                dispatch_authorization_observed = True
                authorized_dispatches += authorized_count
            if tool == "read_worker_result":
                operation = "read_result" if event.get("ok") is True else "read_result_other"
            elif tool == "continue_orchestration":
                operation = "continue_result" if event.get("ok") is True else "continue_result_other"
        if operation is None:
            continue
        if operation == last_operation and operation in {
            "spawn", "wait_result", "wait_provisional_result", "read_result", "continue_result", "close",
        }:
            ambiguous_observations += 1
            continue
        last_operation = operation
        if operation == "spawn":
            spawned += 1
            if dispatch_authorization_observed and spawned > authorized_dispatches:
                # This native child was not returned by a successful Cortex
                # dispatch response.  It remains generic host work and may
                # not satisfy a Cortex gate even if it later emits terminal
                # text resembling a worker result.
                unmatched_native_spawns += 1
        elif operation in {"wait_result", "wait_provisional_result"}:
            if waited >= spawned:
                violation_count += 1
            else:
                waited += 1
                pending_reads += 1
        elif operation == "read_result":
            if pending_reads < 1:
                violation_count += 1
            else:
                pending_reads -= 1
                read += 1
                pending_continuations += 1
        elif operation == "continue_result":
            if pending_continuations < 1:
                violation_count += 1
            else:
                pending_continuations -= 1
                continued += 1
                pending_closes += 1
        elif operation == "close":
            if pending_closes < 1:
                violation_count += 1
            else:
                pending_closes -= 1
                closed += 1
        else:
            # A stopped worker must not be accepted on terminal text alone,
            # after an unsuccessful canonical read, or without Cortex's
            # server-derived continuation audit.
            violation_count += 1
    return {
        "spawned_worker_observations": spawned,
        "terminal_wait_observations": waited,
        "canonical_result_reads": read,
        "server_continuation_audits": continued,
        "terminal_closes": closed,
        "pending_canonical_reads": pending_reads,
        "pending_server_continuation_audits": pending_continuations,
        "pending_terminal_closes": pending_closes,
        "protocol_violations": violation_count,
        "ambiguous_native_observations": ambiguous_observations,
        "authorized_native_dispatches": authorized_dispatches,
        "dispatch_authorization_observed": dispatch_authorization_observed,
        "unmatched_native_spawns": unmatched_native_spawns,
        "all_observed_workers_terminally_audited": (
            spawned > 0
            and spawned == waited == read == continued == closed
            and pending_reads == pending_continuations == pending_closes == violation_count == 0
            and ambiguous_observations == unmatched_native_spawns == 0
        ),
    }


def safe_terminal_result_audit(
    state: dict[str, object], result_records: list[dict[str, object]],
) -> dict[str, object]:
    """Prove accepted attempts have one matching, finalized canonical result.

    This is a ledger-only postcondition and never returns attempt ids, refs,
    summaries, or worker payload.  Invalidated attempts are historical
    recovery records, not accepted work; all remaining attempts must have one
    state-linked canonical result in ``COMPLETED`` lifecycle state before the
    evaluator can accept a completed task.
    """
    attempts = [
        item for item in state.get("attempts", [])
        if isinstance(item, dict) and not item.get("invalidated")
    ]
    all_attempt_ids = {
        str(item.get("attempt_id") or "")
        for item in state.get("attempts", [])
        if isinstance(item, dict) and str(item.get("attempt_id") or "")
    }
    attempt_ids = [str(item.get("attempt_id") or "") for item in attempts]
    expected_ids = {attempt_id for attempt_id in attempt_ids if attempt_id}
    malformed_attempts = len(attempts) - len(expected_ids)
    records_by_attempt: dict[str, list[dict[str, object]]] = {}
    foreign_records = 0
    invalidated_historical_results = 0
    finalized_records = 0
    mismatched_records = 0
    for record in result_records:
        attempt_id = str(record.get("attempt_id") or "")
        if attempt_id not in expected_ids:
            if attempt_id in all_attempt_ids:
                invalidated_historical_results += 1
            else:
                foreign_records += 1
            continue
        records_by_attempt.setdefault(attempt_id, []).append(record)
        result = record.get("result")
        if (
            isinstance(result, dict)
            and result.get("lifecycle_status") == "COMPLETED"
            and result.get("status") == "completed"
            and str(result.get("result_ref") or "") == str(record.get("attempt_result_ref") or "")
        ):
            finalized_records += 1
        else:
            mismatched_records += 1
    missing_records = sum(1 for attempt_id in expected_ids if len(records_by_attempt.get(attempt_id, [])) == 0)
    duplicate_records = sum(
        len(records) - 1 for records in records_by_attempt.values() if len(records) > 1
    )
    terminal_attempts = sum(
        1 for item in attempts
        if str(item.get("status") or "") in cortex.TERMINAL_ATTEMPT_STATUSES
    )
    expected_count = len(expected_ids)
    return {
        "accepted_attempts": expected_count,
        "terminal_accepted_attempts": terminal_attempts,
        "finalized_canonical_results": finalized_records,
        "missing_canonical_results": missing_records,
        "duplicate_canonical_results": duplicate_records,
        "mismatched_canonical_results": mismatched_records,
        "foreign_result_records": foreign_records,
        "invalidated_historical_results": invalidated_historical_results,
        "malformed_accepted_attempts": malformed_attempts,
        "all_accepted_attempts_have_terminal_canonical_results": (
            expected_count > 0
            and terminal_attempts == expected_count
            and finalized_records == expected_count
            and missing_records == duplicate_records == mismatched_records == foreign_records == malformed_attempts == 0
        ),
    }


def observed_bootstrap_repair_lifecycle(
    events: list[dict[str, object]], *, recovered: bool,
) -> bool:
    """Prove one zero-call bootstrap failure and at most one same-child repair.

    Native telemetry intentionally exposes no child or capability values.  The
    closed aggregate is therefore strict: one spawn, a sanitized missing-pair
    final, one follow-up, and either the canonical successful-result route or
    one terminal repair failure followed by server cleanup. Any public
    Cortex call before the follow-up, replacement spawn, ambient-inspection
    tool, second follow-up, or post-failure task call invalidates the proof.
    """
    operations: list[str] = []
    server_repair_digests: set[str] = set()
    exact_repair_copied = False
    for event in events:
        operation: str | None = None
        if event.get("event") == "native_tool_call" and event.get("status") == "completed":
            tool = str(event.get("tool") or "")
            outcome = str(event.get("outcome") or "")
            if tool == "spawn_agent":
                operation = "spawn"
            elif tool == "followup_task":
                operation = "followup"
                message_digest = str(event.get("bootstrap_repair_message_digest") or "")
                exact_repair_copied = bool(message_digest and message_digest in server_repair_digests)
            elif tool == "wait" and outcome == "bootstrap_missing":
                operation = "bootstrap_missing"
            elif tool == "wait" and outcome == "other_terminal_message":
                operation = "bootstrap_invalid"
            elif tool == "wait" and outcome == "attempt_completed":
                operation = "wait_result"
            elif tool == "wait" and not outcome:
                continue
            elif tool == "close_agent":
                operation = "close"
            else:
                operation = "native_other"
        elif event.get("event") == "cortex_mcp_call" and event.get("status") == "completed":
            tool = str(event.get("tool") or "")
            if tool == "start_orchestration" and not operations:
                digests = event.get("bootstrap_repair_message_digests")
                if isinstance(digests, list):
                    server_repair_digests.update(
                        str(digest) for digest in digests
                        if re.fullmatch(r"[0-9a-f]{64}", str(digest))
                    )
                continue
            if tool == "read_worker_result" and event.get("ok") is True:
                operation = "read_result"
            elif tool == "continue_orchestration" and event.get("ok") is True:
                operation = "continue"
            elif (
                tool == "manage_orchestration"
                and event.get("management_intent") == "finalize_bootstrap_failure"
                and (event.get("terminal_cleanup") is True or event.get("ok") is True)
            ):
                operation = "finalize_bootstrap_failure"
            else:
                operation = "cortex_other"
        if operation is not None:
            operations.append(operation)

    if recovered:
        expected = ["spawn", "bootstrap_missing", "followup", "wait_result", "read_result", "continue", "close"]
        return operations == expected and exact_repair_copied
    # The exact second missing marker and any malformed/gate-ack terminal both
    # require the one server-owned cleanup. No result read is legal.
    return (
        operations in (
            ["spawn", "bootstrap_missing", "followup", "bootstrap_missing", "finalize_bootstrap_failure"],
            ["spawn", "bootstrap_missing", "followup", "bootstrap_missing", "finalize_bootstrap_failure", "close"],
            ["spawn", "bootstrap_missing", "followup", "bootstrap_invalid", "finalize_bootstrap_failure"],
            ["spawn", "bootstrap_missing", "followup", "bootstrap_invalid", "finalize_bootstrap_failure", "close"],
        )
        and exact_repair_copied
    )


def observed_worker_failure_lifecycle(events: list[dict[str, object]]) -> bool:
    """Require exact nonretryable child final -> one server cleanup and stop."""
    operations: list[str] = []
    for event in events:
        operation: str | None = None
        if event.get("event") == "native_tool_call" and event.get("status") == "completed":
            tool = str(event.get("tool") or "")
            if tool == "spawn_agent":
                operation = "spawn"
            elif tool == "wait" and event.get("outcome") == "attempt_failed_nonretryable":
                operation = "attempt_failed_nonretryable"
            elif tool == "close_agent":
                operation = "close"
            elif tool in {"followup_task"}:
                operation = "forbidden_native"
        elif event.get("event") == "cortex_mcp_call" and event.get("status") == "completed":
            tool = str(event.get("tool") or "")
            if (
                tool == "manage_orchestration"
                and event.get("management_intent") == "finalize_worker_failure"
                and event.get("terminal_cleanup") is True
            ):
                operation = "finalize_worker_failure"
            elif tool in {"read_worker_result", "continue_orchestration"}:
                operation = "forbidden_cortex"
        if operation is not None:
            operations.append(operation)
    return operations in (
        ["spawn", "attempt_failed_nonretryable", "finalize_worker_failure"],
        ["spawn", "attempt_failed_nonretryable", "finalize_worker_failure", "close"],
    )


def observed_question_resume_lifecycle(events: list[dict[str, object]]) -> bool:
    """Prove answer-before-follow-up ordering from privacy-safe live telemetry.

    Source mode cannot expose native child identifiers or question content. It
    can still prove the only legal protocol: native question pause, one public
    ``manage_orchestration(intent=question)`` presentation returning
    ``awaiting_user``, then the fixture-authorized durable answer with Cortex's
    resume contract, exact native follow-up, canonical result read, successful
    server continuation audit, and close. A read is not completion evidence;
    the resumed same attempt must be consumed by Cortex before its child closes.
    Repeated adjacent host observations are transport echoes and are collapsed;
    every non-adjacent reordering fails closed.
    """
    operations: list[str] = []
    for event in events:
        operation: str | None = None
        if event.get("event") == "native_tool_call" and event.get("status") == "completed":
            tool = str(event.get("tool") or "")
            outcome = str(event.get("outcome") or "")
            if tool == "spawn_agent":
                operation = "spawn"
            elif tool == "followup_task":
                operation = "followup"
            elif tool == "close_agent":
                operation = "close"
            elif tool == "wait" and outcome == "question_recorded":
                operation = "wait_question"
            elif tool == "wait" and outcome == "attempt_completed":
                operation = "wait_result"
            elif tool == "wait" and outcome:
                operation = "wait_other_terminal"
        elif event.get("event") == "cortex_mcp_call" and event.get("status") == "completed":
            tool = str(event.get("tool") or "")
            if tool == "manage_orchestration" and event.get("management_intent") == "question":
                if event.get("ok") is True and event.get("outcome") == "awaiting_user":
                    operation = "question_presented"
                elif (
                    event.get("ok") is True
                    and event.get("outcome") == "question_answered"
                    and event.get("resume_contract") is True
                ):
                    operation = "question_answered"
                else:
                    operation = "question_management_other"
            elif tool == "read_worker_result":
                operation = "read_result" if event.get("ok") is True else "read_result_other"
            elif tool == "continue_orchestration":
                operation = "continue_audited" if event.get("ok") is True else "continue_other"
        if operation is not None and (not operations or operation != operations[-1]):
            operations.append(operation)

    # A question can occur in a late governance or close wave, after several
    # ordinary spawn/wait/read/continue/close cycles.  Start the proof at the
    # durable question pause rather than at the first worker in the entire
    # session.  From that pause onward, however, the route is deliberately
    # rigid: the answer must be submitted before *any* parent completion, and
    # no replacement dispatch may intervene before the original child closes.
    expected = (
        "wait_question", "question_presented", "question_answered", "followup",
        "wait_result", "read_result", "continue_audited", "close",
    )
    for start, operation in enumerate(operations):
        if operation != "wait_question":
            continue
        stage = 1
        for candidate in operations[start + 1:]:
            if candidate == expected[stage]:
                stage += 1
                if stage == len(expected):
                    return True
                continue
            # Before a terminal result is recorded, there is no harmless
            # alternate path: a second spawn, another question presentation,
            # an unrelated terminal response, a failed read/continue, or a
            # close all prove that the authorized same-child resume was not
            # consumed.  In particular, `awaiting_user` alone reaches this
            # branch by exhausting the stream and cannot be treated as a
            # successful evaluator stop.
            if candidate in {
                "spawn", "wait_question", "question_presented", "followup",
                "question_answered", "question_management_other", "wait_result",
                "wait_other_terminal", "read_result", "read_result_other", "continue_audited",
                "continue_other", "close",
            }:
                break
    return False


def safe_question_resolution_audit(
    ledger: Path,
    state: dict[str, object],
    result_records: list[dict[str, object]],
) -> dict[str, object]:
    """Audit question resolution before the same attempt's canonical result.

    Only aggregate event counts and booleans leave this function.  The ordered
    per-attempt event stream is read solely to prove the durable state machine:
    ``question_created < question_answered < decision_resolved < work_completed``.
    """
    task_id = str(state.get("task_id") or "")
    result_attempt_ids = {
        str(record.get("attempt_id") or "")
        for record in result_records
        if str(record.get("attempt_id") or "")
    }
    question_attempt_count = 0
    question_created_count = 0
    question_answered_count = 0
    decision_resolved_count = 0
    resolved_before_result_count = 0
    audited = True
    for item in state.get("attempts", []):
        if not isinstance(item, dict) or item.get("invalidated"):
            continue
        attempt_id = str(item.get("attempt_id") or "")
        if not task_id or not attempt_id:
            continue
        try:
            events = cortex.attempt_protocol.list_attempt_events(
                ledger, task_id=task_id, attempt_id=attempt_id, limit=1024,
            )
        except (OSError, ValueError, sqlite3.Error):
            audited = False
            continue
        event_types = [str(event.get("event_type") or "") for event in events]
        created = [index for index, value in enumerate(event_types) if value == "question_created"]
        if not created:
            continue
        question_attempt_count += 1
        answers = [index for index, value in enumerate(event_types) if value == "question_answered"]
        decisions = [index for index, value in enumerate(event_types) if value == "decision_resolved"]
        results = [
            index for index, value in enumerate(event_types)
            if value in {"work_completed", "finalizing", "completed"}
        ]
        question_created_count += len(created)
        question_answered_count += len(answers)
        decision_resolved_count += len(decisions)
        if (
            len(answers) >= len(created)
            and len(decisions) >= len(created)
            and results
            and attempt_id in result_attempt_ids
            and max(created) < min(answers) < min(decisions) < min(results)
        ):
            resolved_before_result_count += 1
        else:
            audited = False
    return {
        "question_attempt_count": question_attempt_count,
        "question_created_count": question_created_count,
        "question_answered_count": question_answered_count,
        "decision_resolved_count": decision_resolved_count,
        "resolved_before_result_count": resolved_before_result_count,
        "all_question_attempts_resolved_before_result": (
            audited and question_attempt_count == resolved_before_result_count
        ),
    }


def automatic_sequential_question_audit(question_records: object) -> dict[str, object]:
    """Fail closed when the decision-complete sequential fixture gets a question.

    The automatic-sequential task intentionally has no user decision surface.
    Keep this audit privacy-safe: only the record count and availability are
    retained, never question text, refs, or answers. A malformed or unavailable
    question projection is also a failure rather than evidence that no question
    occurred.
    """
    if not isinstance(question_records, list):
        return {
            "question_state_available": False,
            "question_count": None,
            "no_unexpected_questions": False,
        }
    return {
        "question_state_available": True,
        "question_count": len(question_records),
        "no_unexpected_questions": len(question_records) == 0,
    }


def safe_ledger_progress(project: Path) -> dict[str, object] | None:
    """Return bounded aggregate lifecycle state from a source-mode SQLite ledger."""
    try:
        # Resolve the opaque host-private mapping without opening/migrating the
        # database.  Progress is a read-only best-effort diagnostic and must
        # not turn a partially initialized store into a lifecycle failure.
        database = cortex.ledger_root_path({"project_root": str(project)}) / "cortex.db"
    except (OSError, ValueError, sqlite3.Error):
        return None
    if not database.is_file():
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        rows = connection.execute("SELECT status, state_json FROM tasks").fetchall()
        task_statuses: dict[str, int] = {}
        attempt_statuses: dict[str, int] = {}
        gates: dict[str, int] = {}
        for status, state_json in rows:
            task_status = safe_status(status, SAFE_TASK_STATUSES)
            task_statuses[task_status] = task_statuses.get(task_status, 0) + 1
            try:
                state = json.loads(str(state_json))
            except (TypeError, json.JSONDecodeError):
                state = {}
            for attempt in state.get("attempts", []) if isinstance(state, dict) else []:
                if not isinstance(attempt, dict):
                    continue
                attempt_status = safe_status(attempt.get("status"), SAFE_ATTEMPT_STATUSES)
                attempt_statuses[attempt_status] = attempt_statuses.get(attempt_status, 0) + 1
                gate = str(attempt.get("gate") or "")
                if gate in SAFE_GATE_NAMES:
                    gates[gate] = gates.get(gate, 0) + 1
        result_count = connection.execute(
            "SELECT COUNT(*) FROM logical_artifacts WHERE kind='attempt_result'"
        ).fetchone()[0]
        session_rows = connection.execute(
            "SELECT status, COUNT(*) FROM worker_sessions GROUP BY status"
        ).fetchall()
        sessions: dict[str, int] = {}
        for status, count in session_rows:
            session_status = safe_status(status, SAFE_SESSION_STATUSES)
            sessions[session_status] = sessions.get(session_status, 0) + int(count)
        latest = connection.execute(
            "SELECT event FROM ledger_events ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        latest_event = str(latest[0]) if latest and str(latest[0]) in SAFE_LEDGER_EVENTS else "none"
        return {
            "tasks": len(rows), "task_statuses": task_statuses,
            "attempt_statuses": attempt_statuses, "gates": gates,
            "attempt_results": int(result_count), "worker_sessions": sessions,
            "latest_ledger_event": latest_event,
        }
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None
    finally:
        if connection is not None:
            connection.close()


def safe_ledger_audit_record(sequence: int, progress: object) -> dict[str, object]:
    """Freeze one allow-listed SQLite progress observation for final audit.

    The progress reader deliberately exposes only aggregate counts/statuses.
    Re-projecting it here prevents future additions to a progress response from
    accidentally becoming retained evaluator data.
    """
    source = progress if isinstance(progress, dict) else {}
    count = lambda key: int(source.get(key, 0)) if type(source.get(key)) is int else 0
    status_map = lambda key, allowed: {
        str(name): int(value)
        for name, value in (source.get(key) or {}).items()
        if str(name) in allowed and type(value) is int and 0 <= value <= 100_000
    } if isinstance(source.get(key), dict) else {}
    return {
        "sequence": int(sequence),
        "tasks": count("tasks"),
        "attempt_results": count("attempt_results"),
        "task_statuses": status_map("task_statuses", SAFE_TASK_STATUSES),
        "attempt_statuses": status_map("attempt_statuses", SAFE_ATTEMPT_STATUSES),
        "gates": status_map("gates", SAFE_GATE_NAMES),
        "worker_sessions": status_map("worker_sessions", SAFE_SESSION_STATUSES),
        "latest_ledger_event": safe_status(source.get("latest_ledger_event"), SAFE_LEDGER_EVENTS | {"none"}),
    }


def safe_attempt_event_key_audit(project: Path) -> dict[str, object]:
    """Audit AttemptEvent idempotency at its actual database uniqueness scope.

    ``event_key`` is deliberately scoped to one task attempt.  Reusing a key
    in another attempt is useful aggregate context, not an idempotency error.
    This reader returns counts only: no task IDs, attempt IDs, event keys,
    event payloads, or worker-authored data are retained.
    """
    try:
        database = cortex.ledger_root_path({"project_root": str(project)}) / "cortex.db"
    except (OSError, ValueError, sqlite3.Error):
        return {"status": "unavailable"}
    if not database.is_file():
        return {"status": "unavailable"}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        same_attempt = connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(row_count), 0)
            FROM (
                SELECT task_id, attempt_id, event_key, COUNT(*) AS row_count
                FROM attempt_events
                GROUP BY task_id, attempt_id, event_key
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()
        cross_attempt = connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(row_count), 0)
            FROM (
                SELECT task_id, event_key, COUNT(*) AS row_count
                FROM attempt_events
                GROUP BY task_id, event_key
                HAVING COUNT(DISTINCT attempt_id) > 1
            )
            """
        ).fetchone()
        return {
            "status": "ok",
            "same_attempt_duplicate_groups": int(same_attempt[0] or 0),
            "same_attempt_duplicate_rows": int(same_attempt[1] or 0),
            "cross_attempt_reused_key_groups": int(cross_attempt[0] or 0),
            "cross_attempt_reused_key_rows": int(cross_attempt[1] or 0),
        }
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return {"status": "unavailable"}
    finally:
        if connection is not None:
            connection.close()


def attempt_event_key_audit_passed(audit: object) -> bool:
    """Return whether the per-attempt idempotency invariant holds."""
    return (
        isinstance(audit, dict)
        and audit.get("status") == "ok"
        and audit.get("same_attempt_duplicate_groups") == 0
        and audit.get("same_attempt_duplicate_rows") == 0
    )


def canonical_task_directories(project: Path) -> list[Path]:
    """Resolve task artifacts from the private SQLite ledger, never a workspace scan."""
    try:
        ledger = cortex.ledger_root({"project_root": str(project)})
        directories = [
            path
            for task_id in cortex.db_task_index(ledger)
            if isinstance(path := cortex.db_task_artifact_path(ledger, str(task_id)), Path)
            and path.is_dir()
        ]
    except (OSError, ValueError, sqlite3.Error):
        return []
    return directories


def record_failure_metadata(
    result: dict[str, object],
    events: list[dict[str, object]],
    *,
    retain: bool,
) -> None:
    """Retain only bounded, already-sanitized failure evidence when opted in."""
    if retain:
        failure_dir, _failure_ownership = create_owned_temp_directory(
            Path(tempfile.gettempdir()), prefix="cortex-luna-high-failure-", purpose="failure_metadata",
        )
        progress = failure_dir / "progress.json"
        progress.write_text(
            json.dumps({"result": result, "events": events[-100:]}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.chmod(progress, 0o600)
        result["failure_artifacts"] = str(failure_dir)
    else:
        result["failure_metadata"] = "not_retained"


def terminate_process_group(process: subprocess.Popen[str], scenario: str, reason: str) -> None:
    """Terminate and reap a live evaluator process group without leaking descendants."""
    process_group = process.pid
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        if process.poll() is None:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
        return
    emit_live_progress(scenario, "termination_requested", reason=reason)
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is None:
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        emit_live_progress(scenario, "termination_escalated", reason=reason)
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    emit_live_progress(scenario, "terminated", reason=reason, exit_code=process.returncode)


def run_live_command(
    command: list[str], project: Path, scenario: str, *, timeout_seconds: int,
    environment: dict[str, str] | None = None,
) -> dict[str, object]:
    """Stream sanitized Codex progress while supervising its complete process group."""
    started = time.monotonic()
    last_activity = started
    last_activity_kind = "launch"
    events: list[dict[str, object]] = []
    dropped_events = 0
    emitted_stream_events = 0
    suppression_emitted = False
    interrupted: dict[str, int | None] = {"signal": None}

    def request_termination(signum: int, _frame: object) -> None:
        interrupted["signal"] = signum

    previous_term = signal.signal(signal.SIGTERM, request_termination)
    previous_int = signal.signal(signal.SIGINT, request_termination)
    process: subprocess.Popen[str] | None = None
    selector: selectors.BaseSelector | None = None
    stream_buffers: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    next_heartbeat = started + HEARTBEAT_SECONDS
    last_ledger_signature = ""
    ledger_audit_records: list[dict[str, object]] = []
    termination_reason: str | None = None

    def retain_event(event: dict[str, object]) -> None:
        nonlocal dropped_events
        if len(events) == MAX_RETAINED_STREAM_EVENTS:
            events.pop(0)
            dropped_events += 1
        events.append(event)

    def emit_stream_event(event: dict[str, object], elapsed_seconds: int) -> None:
        nonlocal emitted_stream_events, suppression_emitted
        if emitted_stream_events < MAX_EMITTED_STREAM_EVENTS:
            emitted_stream_events += 1
            emit_live_progress(scenario, str(event["event"]), elapsed_seconds=elapsed_seconds, **{
                key: value for key, value in event.items() if key != "event"
            })
            return
        if not suppression_emitted:
            suppression_emitted = True
            emit_live_progress(scenario, "host_events_suppressed", elapsed_seconds=elapsed_seconds)

    def handle_stream_line(source: str, line: str) -> None:
        """Retain and emit only a bounded, sanitized representation of one line."""
        nonlocal last_activity, last_activity_kind, termination_reason
        now = time.monotonic()
        if source == "stdout":
            safe_event = (
                {"event": "host_output", "format": "oversized"}
                if len(line.encode("utf-8", errors="ignore")) > MAX_STREAM_LINE_BYTES
                else sanitize_codex_stream_line(line)
            )
            retain_event(safe_event)
            last_activity, last_activity_kind = now, str(safe_event.get("event") or "host")
            emit_stream_event(safe_event, int(now - started))
            if is_gates_recorded_lifecycle_failure(safe_event):
                # A normal continuation is server-derived.  Once Cortex has
                # recorded a gate but rejected the successor preparation, the
                # evaluator has no authority to reconstruct later pipeline
                # work. Stop the native parent before it can mutate the task
                # with speculative recovery calls.
                termination_reason = "gates_recorded_public_failure"
            return
        # Preserve observability without exposing arbitrary host diagnostics.
        last_activity, last_activity_kind = now, "stderr"
        safe_event = {"event": "host_stderr"}
        retain_event(safe_event)
        emit_stream_event(safe_event, int(now - started))

    def handle_oversized_stream_line(source: str) -> None:
        """Account for an unterminated over-limit line without reading its text."""
        nonlocal last_activity, last_activity_kind
        now = time.monotonic()
        last_activity, last_activity_kind = now, source
        safe_event = (
            {"event": "host_output", "format": "oversized"}
            if source == "stdout" else {"event": "host_stderr"}
        )
        retain_event(safe_event)
        emit_stream_event(safe_event, int(now - started))

    def drain_stream(source: str, stream: object, *, final: bool = False) -> None:
        """Consume complete bounded lines without letting ``readline`` block.

        A text ``readline`` can wait forever after ``select`` sees an initial
        fragment of a JSON event.  This would bypass the live deadline.  Read
        only ready file descriptors, retain a byte buffer, and process a final
        fragment at EOF; no arbitrary child output is emitted or retained.
        """
        buffer = stream_buffers[source]
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            raw = bytes(buffer[:newline])
            del buffer[:newline + 1]
            if len(raw) > MAX_STREAM_LINE_BYTES:
                handle_oversized_stream_line(source)
            else:
                handle_stream_line(source, raw.decode("utf-8", errors="replace"))
        if len(buffer) > MAX_STREAM_LINE_BYTES:
            buffer.clear()
            handle_oversized_stream_line(source)
        elif final and buffer:
            raw = bytes(buffer)
            buffer.clear()
            handle_stream_line(source, raw.decode("utf-8", errors="replace"))

    try:
        process = subprocess.Popen(
            command, cwd=project, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=1, start_new_session=True, env=environment,
        )
        emit_live_progress(scenario, "parent_started", elapsed_seconds=0)
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if interrupted["signal"] is not None:
                termination_reason = f"signal_{interrupted['signal']}"
            elif now - started >= timeout_seconds:
                termination_reason = "timeout"
            if termination_reason:
                terminate_process_group(process, scenario, termination_reason)
                break
            ledger = safe_ledger_progress(project)
            if ledger is not None:
                signature = json.dumps(ledger, sort_keys=True, separators=(",", ":"))
                if signature != last_ledger_signature:
                    last_ledger_signature = signature
                    ledger_audit_records.append(
                        safe_ledger_audit_record(len(ledger_audit_records) + 1, ledger)
                    )
                    last_activity, last_activity_kind = now, "ledger"
                    emit_live_progress(scenario, "ledger_progress", elapsed_seconds=int(now - started), **ledger)
            if now >= next_heartbeat:
                emit_live_progress(
                    scenario, "heartbeat", elapsed_seconds=int(now - started),
                    last_activity=last_activity_kind, last_activity_seconds_ago=int(now - last_activity),
                    process_running=process.poll() is None,
                )
                next_heartbeat = now + HEARTBEAT_SECONDS
            for key, _mask in selector.select(timeout=1):
                stream = key.fileobj
                source = str(key.data)
                try:
                    chunk = os.read(stream.fileno(), 16 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    drain_stream(source, stream, final=True)
                    selector.unregister(stream)
                    continue
                stream_buffers[source].extend(chunk)
                drain_stream(source, stream)
        if process.poll() is None:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
    finally:
        try:
            if selector is not None:
                for key in list(selector.get_map().values()):
                    selector.unregister(key.fileobj)
                selector.close()
            if process is not None:
                # The direct Codex parent can exit before an inherited pipe holder.
                # Always probe and clean the known session/process group first.
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    process_group_alive = False
                else:
                    process_group_alive = True
                if process_group_alive or process.poll() is None:
                    terminate_process_group(process, scenario, termination_reason or "cleanup")
                # Process-group cleanup closes descendants' inherited descriptors. Drain
                # the remaining buffered lines before closing our own file objects so
                # their sanitized lifecycle events are not silently lost on timeout.
                for stream, source in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                    if stream is None or stream.closed:
                        continue
                    while True:
                        line = stream.readline(MAX_STREAM_LINE_BYTES + 1)
                        if not line:
                            break
                        handle_stream_line(source, line)
        finally:
            if process is not None:
                for stream in (process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()
            # The handlers remain installed until process-group and pipe cleanup
            # finishes, preventing a signal from skipping the reap/close sequence.
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
    if process is None:
        raise RuntimeError("live evaluator failed to create its parent process")
    elapsed = int(time.monotonic() - started)
    emit_live_progress(
        scenario, "parent_finished", elapsed_seconds=elapsed, exit_code=process.returncode,
        termination=termination_reason,
    )
    return {
        "returncode": process.returncode, "events": events, "elapsed_seconds": elapsed,
        "termination": termination_reason, "dropped_stream_events": dropped_events,
        "ledger_audit_records": ledger_audit_records,
    }


def workspace_summary(project: Path) -> dict[str, object]:
    """Describe the actual fixture workspace without treating it as a blocker."""
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        return {"modified": [], "untracked": [], "staged": [], "committed": "not_required"}
    modified: list[str] = []
    untracked: list[str] = []
    staged: list[str] = []
    for line in completed.stdout.splitlines():
        if line.startswith("?? "):
            untracked.append(line[3:])
            continue
        if len(line) < 4:
            continue
        index, worktree, path = line[0], line[1], line[3:]
        if index != " ":
            staged.append(path)
        if worktree != " ":
            modified.append(path)
    has_changes = bool(modified or untracked or staged)
    return {
        "modified": sorted(modified),
        "untracked": sorted(untracked),
        "staged": sorted(staged),
        "committed": False if has_changes else "not_required",
    }


def passing_attempt_result(project: Path, gate: str) -> dict[str, object]:
    """Build a deterministic completed AttemptResult for fixture checks."""
    return {
        "status": "completed",
        "summary": f"Deterministic Luna-high fixture completed for the {gate} attempt.",
        "findings": [],
        "decisions_needed": [],
        "unresolved": [],
        "claims": [],
        "evidence": [f"Deterministic Luna-high fixture verification completed for the {gate} attempt."],
        "changed_files": workspace_summary(project).get("untracked", []),
    }


def canonical_artifacts(
    ledger: Path, task_id: str, *, kind: str | None = None
) -> list[dict[str, object]]:
    """List canonical SQLite artifacts without relying on local projections."""
    artifacts: list[dict[str, object]] = []
    offset = 0
    while True:
        page, next_offset = cortex.db_list_artifacts(
            ledger, task_id, kind=kind, offset=offset, page_size=100,
        )
        artifacts.extend(page)
        if next_offset is None:
            return artifacts
        offset = next_offset


def canonical_attempt_result_records(
    ledger: Path,
    state: dict[str, object],
) -> list[dict[str, object]]:
    """Read finalized AttemptResult rows, never a retired artifact projection."""
    task_id = str(state.get("task_id") or "")
    records: list[dict[str, object]] = []
    for attempt in state.get("attempts", []):
        if not isinstance(attempt, dict):
            continue
        attempt_id = str(attempt.get("attempt_id") or "")
        result_ref = str(attempt.get("attempt_result_ref") or "")
        if not attempt_id or not result_ref:
            continue
        result = cortex.attempt_protocol.get_attempt_result(
            ledger, task_id=task_id, attempt_id=attempt_id,
        )
        if result is None or str(result.get("result_ref") or "") != result_ref:
            continue
        records.append({
            "attempt_id": attempt_id,
            "gate": str(attempt.get("gate") or ""),
            "attempt_result_ref": result_ref,
            "result": result,
        })
    return records


def canonical_results_are_strict(records: list[dict[str, object]]) -> bool:
    """Validate the fresh semantic AttemptResult transport used by v11."""
    required = {"status", "summary", "findings", "decisions_needed", "unresolved", "claims"}
    return bool(records) and all(
        isinstance(record.get("result"), dict)
        and required.issubset(record["result"])
        and record["result"].get("lifecycle_status") == "COMPLETED"
        and record["result"].get("status") == "completed"
        for record in records
    )


def result(label: str, project: Path, changed_files: list[str] | None = None) -> dict[str, object]:
    return {
        "summary": label,
        "findings": [],
        "decisions_needed": [],
        "unresolved": [],
        "evidence": [label],
    }


def task(user_request: str, complexity: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "user_request": user_request,
        "acceptance_criteria": ["The requested fixture lifecycle completes with a verified handoff."],
        "verification": ["Run and record the deterministic fixture check through the close gate."],
    }
    if complexity is not None:
        value["complexity"] = complexity
    return value


def planning(label: str) -> dict[str, object]:
    return {
        "overview": f"Deterministic work breakdown for {label}.",
        "work_packages": [{
            "id": "fixture_core",
            "title": "Complete fixture lifecycle",
            "objective": "Exercise the current durable result and close contract.",
            "allowed_paths": ["."],
            "microtasks": [{
                "id": "fixture_verify",
                "title": "Verify fixture lifecycle",
                "objective": "Record a complete, reproducible fixture result.",
                "profile": "backend_dev",
                "allowed_paths": ["result.md"],
                "acceptance_criteria": ["The fixture result is recorded."],
                "verification": ["Run the deterministic fixture command."],
            }],
        }],
    }


def scoping(label: str) -> dict[str, object]:
    return {
        "overview": f"Deterministic discovery brief for {label}.",
        "context_files": [],
        "discovery_domains": [{
            "id": "fixture_runtime",
            "title": "Fixture runtime",
            "objective": "Trace the deterministic fixture lifecycle and evidence boundaries.",
            "paths": ["."],
            "context": ["Current source, tests, and executable fixture configuration are authoritative."],
            "depends_on": [],
            "acceptance_criteria": ["The discovery result maps the complete fixture lifecycle."],
            "verification": ["Confirm the lifecycle against current fixture source and tests."],
        }],
    }


def finish(project: Path, current: dict[str, object]) -> dict[str, object]:
    if not current.get("ok"):
        raise AssertionError(current)
    coordinator_ref = str(current.get("coordinator_ref") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", coordinator_ref):
        raise AssertionError("start response omitted its coordinator capability")
    while current.get("outcome") != "completed":
        if current.get("outcome") == "awaiting_plan_approval":
            prompt = cortex.manage_orchestration({
                "task_ref": current["task_ref"],
                "coordinator_ref": coordinator_ref,
                "intent": "plan_approval",
                "payload": {"decision": "prompt"},
            })
            decision = prompt.get("decision") or {}
            request_id = str(decision.get("request_id") or "")
            if prompt.get("outcome") != "plan_approval" or not request_id:
                raise AssertionError("plan approval response omitted its bounded decision receipt")
            current = cortex.manage_orchestration({
                "task_ref": current["task_ref"],
                "coordinator_ref": coordinator_ref,
                "intent": "plan_approval",
                "payload": {"decision": "approve", "request_id": request_id},
            })
            if not current.get("ok"):
                raise AssertionError(current)
            continue
        dispatches = current.get("dispatches") or []
        ledger = cortex.ledger_root({"project_root": str(project)})
        registry = cortex._operation_registry(ledger)
        task_id = next(
            candidate for candidate, record in registry["tasks"].items()
            if record.get("start", {}).get("task_ref") == current["task_ref"]
        )
        task_dir, state, _ = cortex._v11_task_state(ledger, task_id)
        task_definition = cortex.load_task_definition(task_dir, state)
        active_attempts = [
            item for item in state["attempts"]
            if item.get("status") not in cortex.TERMINAL_ATTEMPT_STATUSES
            and item.get("gate") in cortex.active_gates(state)
        ][-len(dispatches):]
        for worker, (dispatch, attempt) in enumerate(zip(dispatches, active_attempts), 1):
            label = f"step {current['step']} worker {worker}"
            message = str(((dispatch.get("arguments") or {}).get("message")) or "")
            assignment_refs = re.findall(r"assignment-v1-[0-9a-f]{64}", message)
            if len(set(assignment_refs)) != 1:
                raise AssertionError("native dispatch omitted its single worker assignment capability")
            assignment_ref = assignment_refs[0]
            briefing_read = cortex.read_dispatch_briefing({
                "task_ref": current["task_ref"],
                "assignment_ref": assignment_ref,
            })
            if not briefing_read.get("ok"):
                raise AssertionError(briefing_read)
            for predecessor_ref in attempt.get("context_result_refs") or []:
                predecessor_read = cortex.read_worker_result({
                    "task_ref": current["task_ref"],
                    "assignment_ref": assignment_ref,
                    "attempt_result_ref": predecessor_ref,
                })
                if not predecessor_read.get("ok"):
                    raise AssertionError(predecessor_read)
            changed_files: list[str] = []
            if attempt.get("gate") == "implementation":
                (project / "result.md").write_text("Verified Luna-high fixture result.\n", encoding="utf-8")
                changed_files = ["result.md"]
            worker_result = result(label, project, changed_files)
            evidence = worker_result["evidence"]
            predecessor_result_refs = list(attempt.get("context_result_refs") or [])
            if predecessor_result_refs:
                evidence.append("Predecessor result context: " + ", ".join(predecessor_result_refs))
            for index, _criterion in enumerate(attempt.get("acceptance_criteria") or [], 1):
                evidence.append(f"Gate acceptance {index}: PASS - Deterministic fixture lifecycle produced the required recorded result.")
            for index, _criterion in enumerate(attempt.get("verification") or [], 1):
                evidence.append(f"Gate verification {index}: PASS - Exact deterministic fixture command completed with exit code zero.")
            if attempt.get("gate") == "close":
                for index, _criterion in enumerate(task_definition.get("acceptance_criteria") or [], 1):
                    evidence.append(f"Task acceptance {index}: PASS - Completed fixture lifecycle produced a durable verified handoff.")
                for index, _criterion in enumerate(task_definition.get("verification") or [], 1):
                    evidence.append(f"Task verification {index}: PASS - Final deterministic fixture check completed with exit code zero.")
            evidence.append("Dispatch briefing reviewed: " + str(attempt["briefing_digest"]))
            publication: dict[str, object] = {
                "task_ref": current["task_ref"],
                "assignment_ref": assignment_ref,
            }
            if attempt.get("gate") == "plan":
                # Server-owned diagnostic recovery dispatches a real Planner
                # attempt.  Publish the required planning payload so the
                # fixture exercises the same contract as a native worker;
                # no coordinator-side recovery or management loop is needed.
                publication["plan"] = planning(label)
            else:
                publication["outcome"] = {
                    "status": "completed",
                    "summary": worker_result["summary"],
                    "findings": worker_result["findings"],
                    "decisions_needed": worker_result["decisions_needed"],
                    "unresolved": worker_result["unresolved"],
                }
            published = cortex.complete_worker_attempt(publication)
            if not published.get("ok"):
                raise AssertionError(published)
            # Completion is deliberately compact: the child no longer
            # receives or transports an attempt_result_ref.  The coordinator
            # must ask Cortex to derive the complete current wave from its
            # canonical ledger state after every worker has completed.
        canonical_read = cortex.read_worker_result({
            "task_ref": current["task_ref"],
            "coordinator_ref": coordinator_ref,
            "step": current["step"],
        })
        if not canonical_read.get("ok"):
            raise AssertionError(canonical_read)
        continuation = canonical_read.get("continuation")
        if not isinstance(continuation, dict):
            raise AssertionError("coordinator result read omitted its server-derived continuation")
        current = cortex.continue_orchestration({
            "task_ref": current["task_ref"],
            "coordinator_ref": coordinator_ref,
            "step": continuation.get("step"),
            "results": continuation.get("results"),
        })
        if not current.get("ok"):
            raise AssertionError(current)
    return current


def fixture_eval(base: Path) -> list[dict[str, object]]:
    """Run source fixtures without touching the user's host control store."""
    with isolated_cortex_host_store(base):
        return _fixture_eval(base)


def _fixture_eval(base: Path) -> list[dict[str, object]]:
    scenarios: list[dict[str, object]] = []

    sequential = base / "sequential"
    sequential.mkdir()
    current = cortex.start_orchestration({
        "project_root": str(sequential), "task": task("sequential Luna fixture", "C1"),
    })
    completed = finish(sequential, current)
    scenarios.append({"name": "automatic_sequential", "outcome": completed["outcome"]})

    parallel = base / "parallel"
    parallel.mkdir()
    current = cortex.start_orchestration({
        "project_root": str(parallel),
        "task": {**task("parallel Luna fixture", "C1"), "plan_approval": "auto"},
        "waves": [{"phase": "discover", "workers": [
            {"profile": "explorer"},
            {"profile": "explorer"},
        ]}],
    })
    if len(current.get("dispatches") or []) != 2:
        raise AssertionError("parallel fixture did not return two relative worker slots")
    completed = finish(parallel, current)
    scenarios.append({"name": "compact_parallel", "outcome": completed["outcome"]})

    for project in (sequential, parallel):
        task_dir = canonical_task_directories(project)[0]
        state = cortex.load_task_state_for_artifact(task_dir)
        ledger = cortex.ledger_root({"project_root": str(project)})
        result_records = canonical_attempt_result_records(ledger, state)
        if not canonical_results_are_strict(result_records):
            raise AssertionError(f"{project.name} has no finalized canonical AttemptResult rows")
        terminal_result_audit = safe_terminal_result_audit(state, result_records)
        if terminal_result_audit["all_accepted_attempts_have_terminal_canonical_results"] is not True:
            raise AssertionError(f"{project.name} has an incomplete terminal canonical-result audit")
        snapshot_cleanup = state.get("manifest_snapshot_cleanup") or {}
        if state.get("status") != "completed":
            raise AssertionError(f"{project.name} did not reach completed state")
    return scenarios


def live_prompt(scenario: str, project: Path, source_task_ref: str | None = None) -> str:
    if scenario == "follow_up_partial":
        return (
            "Use only the public Cortex tools for this isolated partial smoke test. "
            f"The exact project_root is {project}. The completed source task_ref is {source_task_ref!r}. "
            "Call manage_orchestration exactly once with intent=follow_up, that task_ref, and payload exactly "
            "{\"user_request\":\"Correct the fixture result because the completed task produced the wrong behavior.\","
            "\"complexity\":\"C1\",\"acceptance_criteria\":[\"The linked corrective task is created without "
            "mutating the completed source task.\"],\"verification\":[\"Inspect the returned follow-up linkage and "
            "the unchanged source-task state.\"],\"plan_approval\":\"auto\"}. "
            "Do not call start_orchestration, continue_orchestration, or any private Cortex tool. Do not execute the returned "
            "worker dispatch: this test must stop after Cortex has created the linked corrective task. You may inspect that new task "
            "once with manage_orchestration to confirm it is awaiting its first worker."
        )
    result_field_names = ", ".join(ATTEMPT_RESULT_FIELDS)
    result_contract = f"exactly {len(ATTEMPT_RESULT_FIELDS)} AttemptResult fields: {result_field_names}"
    if scenario == "planner_patch_repair":
        malformed = {
            "overview": "Immediate malformed planner fixture.",
            "work_packages": [{
                "id": "inspect_source",
                "title": "Inspect source",
                "objective": "Inspect README.md.",
                "allowed_paths": ["README.md"],
                "gates": ["not_a_gate"],
                "microtasks": [{
                    "id": "read_readme",
                    "title": "Read README",
                    "objective": "Inspect README.md.",
                    "profile": "explorer",
                    "allowed_paths": ["README.md"],
                    "acceptance_criteria": ["README.md is inspected."],
                    "verification": ["Read README.md."],
                }],
            }],
            "requirement_coverage": [{
                "requirement": "README must be inspected",
                "plan_refs": ["missing_plan_item"],
                "verification": ["Read README.md."],
                "status": "covered",
            }],
        }
        return (
            "You are the Luna-high parent for one isolated Cortex planner repair test. "
            f"The exact project_root is {project}. Call start_orchestration exactly once with this task contract: "
            "{\"user_request\":\"Produce a planner work breakdown for the fixture.\",\"complexity\":\"C1\","
            "\"acceptance_criteria\":[\"The planner attempt is completed on the same worker after validation repair.\"],"
            "\"verification\":[\"Verify the canonical planner result and retained rejected draft.\"],\"plan_approval\":\"auto\"}. "
            "Call start_orchestration with exactly this initial waves array: [{\"phase\":\"plan\",\"workers\":[{\"profile\":\"planner\",\"objective\":\"Submit the malformed planner fixture immediately.\"}]}]. "
            "Use the returned dispatch and native wait. On that worker's first turn, call complete_attempt immediately: "
            "do not inspect the project, run commands, ask questions, or spend time making the report correct. "
            "The first completion must contain exactly this malformed planning object, while retaining the required "
            f"AttemptResult fields: {json.dumps(malformed, ensure_ascii=False, separators=(',', ':'))}. "
            "After the server responds, follow only its public response/schema and the same-attempt lifecycle. "
            "Do not create a replacement worker, and do not stop before the canonical result is read. "
            "Close the completed native worker only after the result is read. "
            "This focused test ends after that planner result: do not call continue_orchestration or dispatch any "
            "successor wave."
        )
    if scenario == "bootstrap_missing_inputs":
        return (
            "You are the live Cortex parent for one isolated worker-bootstrap recovery smoke. There is no existing task. "
            "Your FIRST and only initial lifecycle action MUST be one public Cortex MCP call: start_orchestration. "
            "Do not call continue_orchestration, manage_orchestration, read_worker_result, a native host tool, or inspect "
            "the project before that successful start. Call start_orchestration exactly once with project_root="
            f"{project}, task=<cortex_task_contract>, and waves=<cortex_initial_waves>. "
            "<cortex_task_contract>{\"user_request\":\"Create result.md only after the same native worker receives its "
            "server-issued bootstrap capability pair through the one allowed recovery follow-up.\",\"complexity\":\"C1\","
            "\"acceptance_criteria\":[\"The initial worker makes zero Cortex and project calls when its bootstrap pair is missing.\","
            "\"Exactly one same-child followup_task byte-copies the original server-built bootstrap repair message.\","
            "\"result.md contains exactly one line: Bootstrap pair recovered.\"],\"verification\":[\"Audit one spawn, one "
            "sanitized CORTEX_WORKER_BOOTSTRAP_MISSING final, one same-child followup, and no replacement spawn or ambient "
            "reconstruction.\",\"Read result.md and verify its exact one-line content only after recovery.\"],"
            "\"plan_approval\":\"auto\"}</cortex_task_contract> "
            "<cortex_initial_waves>[{\"phase\":\"implementation\",\"workers\":[{\"profile\":\"general\","
            "\"objective\":\"Apply the bootstrap gate before any Cortex or project call. After the exact capability pair is present, "
            "read the briefing, create result.md containing only Bootstrap pair recovered., verify it, and complete the attempt.\","
            "\"allowed_paths\":[\"result.md\"]}]}]</cortex_initial_waves>. "
            "This is a bounded transport-fault fixture: retain the exact server-generated bootstrap_repair_message privately, but omit assignment_ref only from the first native spawn message so the child must make zero "
            "Cortex/project calls and return exactly `CORTEX_WORKER_BOOTSTRAP_MISSING "
            "missing_fields=[assignment_ref] retryable=true` without capability values. Do not use worker_question, "
            "manage_orchestration, a session, environment, thread, path, database, hook, or project read to repair it. "
            "Call followup_task exactly once for that same native child, byte-copying the exact original bootstrap_repair_message unchanged. "
            "With both repaired refs valid, the child must emit no gate-passed acknowledgement: it immediately calls "
            "read_dispatch_briefing with the exact pair, consumes the complete briefing, and continues the original assignment "
            "through complete_attempt to exactly ATTEMPT_COMPLETED. Any gate-passed prose final is a nonterminal protocol failure. "
            "Never spawn a replacement. Wait again. If the same child reports a still missing or invalid pair, make no second follow-up "
            "and call manage_orchestration finalize_bootstrap_failure exactly once with the original structured dispatch_ref; never read a worker result. "
            "Apply that same cleanup to gate-passed prose, malformed terminal output, or termination without canonical progress, so no resumable orphan remains. On successful same-child recovery, "
            "accept only ATTEMPT_COMPLETED, then read the complete current-wave results as coordinator using only the current server step, "
            "continue with only the server-provided continuation, and require a successful "
            "server continuation/terminal audit before closing that completed child. Stop after that continuation response; do not execute "
            "any successor dispatch. Never use a private Cortex API or create another worker."
        )
    common = (
        "Use the Cortex MCP public tools to complete this isolated task. "
        "You are the parent orchestrator. The exact task contract is the content inside <cortex_task_contract>; "
        "do not copy any surrounding host metadata into the task. Call start_orchestration exactly once with that contract, "
        "and use one continue_orchestration per wave; "
        f"never call orchestrate or any private Cortex tool. Execute every native dispatch; workers must persist {result_contract} with complete_attempt and return only the bare canonical attempt_result_ref plus a short summary. A complete_attempt response may also contain projection_ref: it is a generated view identifier, never a result lookup token, and must never be passed to read_worker_result. When both fields appear, copy only the bare value of the attempt_result_ref field into read_worker_result. Do not use a formatted 'attempt_result_ref=<id>' string, a projection_ref, a summary token, or a stale ref from another child. "
        f"For every review, governance review, or close dispatch, complete_attempt must use only the canonical AttemptResult fields; do not add gate-specific noncanonical envelopes or prose protocol markers. "
        "Read every ref with read_worker_result and advance only from its server-provided continuation object: verify its task_id matches the active task, "
        "then copy its step and results verbatim alongside the existing project_root and task_ref. Never reconstruct a continuation from a projection, summary, "
        "result reference, dispatch reference, or remembered step; if the read response provides no legal continuation object, do not call continue_orchestration. If a "
        "public lifecycle call is rejected after gates are recorded, stop the scenario and retain only the safe machine classification; never call recovery, alter later pipeline work, "
        "or set rework in response. Do not send a self-authored reason or rework field. After a durable result was read and no "
        "question or follow-up remains for that child, require the successful server-derived continuation/terminal audit from "
        "continue_orchestration, then close the completed native child with close_agent before "
        "dispatching a later wave; never close a running or question-paused child. Before every new spawn, FIRST close every known "
        "leftover completed child only after its durable result was read and consumed by that successful server audit or its exact "
        "failed result was accepted by Cortex. If recovery may have missed a terminal child, use "
        "list_agents defensively and apply the same rule; THEN spawn. Do not close active or question-paused children. "
        "Treat a native child as successful only "
        "when its final response starts with ATTEMPT_COMPLETED and the referenced result was read successfully. A canonical "
        "non-success AttemptResult is different: if read_worker_result returns result_view.status=blocked or failed, "
        "its continuation is intentionally absent and that is not permission to submit the result ref as success or to "
        "invent a continuation. Reuse the exact current step from the immediately preceding successful Cortex dispatch "
        "response, submit one terminal receipt with status=blocked or failed, the exact dispatch_ref from that dispatch, "
        "and a concise reason copied from the canonical result summary; omit attempt_result_ref. This terminal receipt "
        "stops the current gate and lets Cortex expose its durable recovery path. If a "
        "native wait returns QUESTION_RECORDED, it is neither success nor a resultless failure. Do not call followup_task, "
        "read_worker_result, continue_orchestration, close_agent, or any management operation for a child from an earlier wave after a later "
        "wave has been dispatched; process each active child target returned by the current dispatch and keep each child's lifecycle ordered. "
        "While that exact child is paused, route only its exact durable question_ref through manage_orchestration intent=question. "
        "Do not invent an answer, identity, ref, child target, or resume message: an ordinary user answer must be durably submitted "
        "through the same question route. Resume only after its answer response has outcome=question_answered and the server next_action "
        "explicitly requires followup_task for the exact same native worker and worker_question(action=poll) for that exact question. "
        "Use only that server-provided resume contract and the original child target, wait for ATTEMPT_COMPLETED, then read the canonical "
        "result and its continuation before close or advance. If the question route cannot provide that answered resume contract, stop; "
        "never send a follow-up before the durable answer or after the child has returned another terminal response. If a "
        "stopped child returns anything else, first verify the absence of a canonical result: when it supplied an exact "
        "attempt_result_ref, call read_worker_result with that exact lookup token. If Cortex returns a legal continuation, "
        "verify task_id and copy its step/results verbatim into continue_orchestration; never synthesize a success from terminal "
        "text or its fields. Emit a status=failed slot only after Cortex has verified that no canonical result/continuation exists; "
        "then call continue_orchestration once for that current wave with status=failed, the exact dispatch_ref from the dispatch, "
        "and the child's exact failure text as reason; never submit an empty result or a resultless success, and let Cortex issue any "
        "corrective dispatch with escalated effort. Finish only after close evidence and handoff. Do not ask for manual argument "
        "corrections. "
        f"The exact project_root is {project}. "
    )
    if scenario == "automatic_governance":
        question_policy = live_question_policy(scenario)
        if question_policy is None:
            raise RuntimeError("automatic governance requires an explicit live question fixture")
        question_markers = question_policy["required_question_markers"]
        preauthorized_answer = question_policy["preauthorized_answer"]
        return common + (
            "<cortex_task_contract>"
            "{\"user_request\":\"Inspect README.md and create result.txt containing exactly one line: Automatic governance fixture completed. Treat this as a high-impact cross-system release change requiring independent oversight.\","
            "\"complexity\":\"C3\","
            "\"acceptance_criteria\":[\"README.md is inspected before implementation.\","
            "\"result.txt contains exactly one line: Automatic governance fixture completed.\","
            "\"Cortex automatically activates full governance and completes independent governance review before final close.\"],"
            "\"verification\":[\"Read README.md and result.txt and verify the exact result.txt content.\","
            "\"Verify the server-added governance activation and governance close evidence before final handoff.\"],"
            "\"plan_approval\":\"auto\"}"
            "</cortex_task_contract> "
            "Do not pass governance_mode, governance_triggers, risk_triggers, or initiative_ref: the server default "
            "auto mode must resolve solely from C3 complexity. Call start_orchestration exactly once with that exact "
            "task and these exact caller waves: [{\"phase\":\"implementation\",\"workers\":[{}]},"
            "{\"phase\":\"documentation\",\"workers\":[{}]},{\"phase\":\"close\",\"workers\":[{}]}]. "
            "Before spawning, confirm requested_mode=auto, effective_mode=full, governance_activation is first, and "
            "governance_close is immediately before close. Execute every returned wave in order. Do not call "
            "manage_governance to force or simulate activation. Do not call manage_orchestration to force governance or "
            "alter the pipeline; the durable QUESTION_RECORDED route described above is the sole question-only exception. "
            "Deterministic decision policy authored for this fixture: README.md, the exact result.txt line, the listed "
            "acceptance criteria, and the listed verification are the complete authorized facts and scope. No external "
            "system, credential, deployment, product preference, or additional user choice is required. Do not ask a "
            "worker_question for ordinary implementation, review, governance, documentation, or close choices: apply the "
            "stated contract and current repository evidence. This live evaluator has one narrow scenario-owned answer "
            "authorization, recorded before execution in <evaluator_question_authorization>; it is not ordinary user "
            "authority and it overrides neither the task contract nor an unrecognized worker question. If and only if a "
            "paused worker's presented durable question contains every required scope marker, do this exact two-call route: "
            "(1) call manage_orchestration intent=question with only its exact question_ref and expect outcome=awaiting_user; "
            "do not stop, render that interaction, request input, or end the parent turn. UNATTENDED FIXTURE-RESUME RULE: "
            "for this one pre-authorized fixture, awaiting_user is an intermediate receipt, not a control transfer to a user. "
            "The very next action in the same parent turn MUST be (2) call manage_orchestration again with command=answer, the exact same "
            "question_ref, and exactly the preauthorized_answer. That second call is mandatory only under this explicit "
            "fixture authorization and must return outcome=question_answered plus resume_contract. A final message, ordinary "
            "wait, native close, replacement spawn, or any other tool call between those two receipts is a failed evaluator run. Then use that exact "
            "resume_contract and the original native child target: followup_task, wait for ATTEMPT_COMPLETED, read_worker_result, "
            "call continue_orchestration once with its server-provided continuation, and only then close_agent. If any marker "
            "is absent, a second question appears, the first route does not await the user, the answer is not accepted, or any "
            "resume/read/continue receipt is missing, stop the scenario transparently without inventing user authority. "
            "<evaluator_question_authorization>"
            f"{{\"maximum_questions\":{question_policy['maximum_questions']},"
            f"\"required_question_markers\":{json.dumps(question_markers)},"
            f"\"preauthorized_answer\":{json.dumps(preauthorized_answer)}}}"
            "</evaluator_question_authorization> "
            "Use this strict state machine for all five sequential server waves when no worker question is paused: "
            "dispatch.call -> wait(target returned by that dispatch) -> read_worker_result "
            "-> continue_orchestration(existing project_root/task_ref plus server continuation step/results verbatim) -> close_agent(completed child) -> next dispatch.call. "
            "After each successful continue_orchestration response, the only legal next tool call is close_agent for "
            "the completed child whose exact result that continuation consumed. Invoke close_agent immediately; do not "
            "reason, inspect, list agents, wait, dispatch, or call any Cortex tool between that continuation and the "
            "close. This rule includes the final close wave: even when the continuation outcome=completed and no successor "
            "dispatch is returned, close_agent the completed child before stopping. The terminal close count must equal the "
            "native spawn count. Only after that close succeeds, when the continuation outcome=ready_to_spawn, the only legal next "
            "tool call is every returned dispatch.call with its exact arguments. A native wait is legal only immediately "
            "after a successful native dispatch and must use the new child target returned by that exact dispatch. Never "
            "reuse a closed child target, never call continue_orchestration twice for one step, never request artifacts "
            "or alter later pipeline work after an accepted continuation, and never call wait without the child target returned "
            "by the immediately preceding native dispatch. If Cortex returns retryable=false for task identity or step "
            "mismatch, stop the scenario immediately; do not retry continue, inspect broadly, or synthesize a recovery. "
            "Governance reviewers must follow their immutable "
            "briefings and publish the canonical AttemptResult plus all evidence they observed. Stop only after Cortex "
            "results completion with a final handoff."
        )
    if scenario == "automatic_sequential":
        return common + (
            "<cortex_task_contract>"
            "{\"user_request\":\"Inspect README.md and append exactly 'Verified note: README heading is Luna high Cortex fixture.' as one new line to result.md, creating the file if absent. This fixture is decision-complete: no material user decision or clarification is required; workers must apply this exact contract and must not call worker_question.\","
            "\"complexity\":\"C2\","
            "\"acceptance_criteria\":[\"README.md is inspected and its heading is confirmed as Luna high Cortex fixture.\","
            "\"result.md contains exactly one appended line: Verified note: README heading is Luna high Cortex fixture.\","
            "\"The final handoff identifies the changed file and includes evidence that the append was verified.\","
            "\"No material user decision or clarification is required for this fixture; the worker must not publish a question.\"],"
            "\"verification\":[\"Read README.md and confirm its heading, then read result.md and confirm the exact appended line.\","
            "\"Inspect the resulting diff or equivalent file evidence to verify only result.md received the intended line.\"],"
            "\"plan_approval\":\"auto\"}"
            "</cortex_task_contract> "
            "This automatic-sequential fixture is decision-complete: the task contract, the current README.md evidence, "
            "its acceptance criteria, and its verification instructions are the complete authority and scope. No material "
            "input, policy choice, clarification, or user decision is missing. Workers MUST NOT call worker_question or "
            "ask the parent for a material decision. If a worker nevertheless returns QUESTION_RECORDED or any durable "
            "question is observed, do not invent an answer, guess, route, resume, replace the worker, or widen the scope: "
            "stop the scenario transparently and let the evaluator mark it FAIL. The evaluator rejects any question record "
            "for this scenario."
        )
    if scenario == "compact_parallel":
        return common + (
            "<cortex_task_contract>"
            "{\"user_request\":\"Inspect README.md, then create result.md containing exactly one line: Parallel discovery fixture completed.\","
            "\"complexity\":\"C1\","
            "\"acceptance_criteria\":[\"Two independent discovery workers inspect README.md in the first wave.\","
            "\"result.md contains exactly one line: Parallel discovery fixture completed.\","
            "\"Close evidence and the final handoff verify the intended file change.\"],"
            "\"verification\":[\"Read README.md and both discovery results before implementation.\","
            "\"Read result.md and inspect the resulting diff or equivalent file evidence.\"],"
            "\"plan_approval\":\"auto\"}"
            "</cortex_task_contract> "
            "Call start_orchestration exactly once with that exact task and these exact waves: "
            "[{\"phase\":\"discover\",\"workers\":[{\"profile\":\"explorer\",\"objective\":\"Confirm the README heading and relevant context.\"},"
            "{\"profile\":\"explorer\",\"objective\":\"Independently verify the required result.md content and constraints.\"}]},"
            "{\"phase\":\"implementation\",\"workers\":[{}]},{\"phase\":\"review\",\"workers\":[{}]},"
            "{\"phase\":\"documentation\",\"workers\":[{}]},{\"phase\":\"close\",\"workers\":[{}]}]."
        )
    if scenario == "planner_work_breakdown":
        return common + (
            "Exercise the Planner work-breakdown contract deterministically. "
            "<cortex_task_contract>"
            "{\"user_request\":\"Inspect README.md, then create result.md containing exactly one line: Planner fixture completed.\","
            "\"complexity\":\"C2\",\"acceptance_criteria\":[\"README.md is inspected.\","
            "\"result.md contains exactly one line: Planner fixture completed.\",\"A final handoff is created.\"],"
            "\"verification\":[\"Read README.md and result.md and verify the exact result.md content.\"],"
            "\"plan_approval\":\"required\"}"
            "</cortex_task_contract> "
            "Call start_orchestration exactly once with that exact task and waves exactly "
            "[{\"phase\":\"discover\",\"workers\":[{}]},{\"phase\":\"plan\",\"workers\":[{}]},"
            "{\"phase\":\"implementation\",\"workers\":[{}]},{\"phase\":\"qa\",\"workers\":[{}]},"
            "{\"phase\":\"review\",\"workers\":[{}]},{\"phase\":\"documentation\",\"workers\":[{}]},"
            "{\"phase\":\"close\",\"workers\":[{}]}]. Complete discovery before the singleton final Planner. "
            "The Planner must read its supplied discovery result and publish the strict result plus this exact planning sibling: "
            "{\"overview\":\"Inspect the source before producing and verifying the requested result.\","
            "\"work_packages\":[{\"id\":\"inspect_source\",\"title\":\"Inspect source\","
            "\"objective\":\"Inspect README.md.\",\"allowed_paths\":[\"README.md\"],\"microtasks\":[{"
            "\"id\":\"read_readme\",\"title\":\"Read README\",\"objective\":\"Inspect README.md.\","
            "\"profile\":\"explorer\",\"allowed_paths\":[\"README.md\"],"
            "\"acceptance_criteria\":[\"README.md is inspected.\"],\"verification\":[\"Read README.md.\"]}]},{"
            "\"id\":\"deliver_result\",\"title\":\"Deliver result\",\"objective\":\"Create and verify result.md.\","
            "\"depends_on\":[\"inspect_source\"],\"allowed_paths\":[\"result.md\"],\"microtasks\":[{"
            "\"id\":\"write_result\",\"title\":\"Write result\",\"objective\":\"Create the exact result.md line.\","
            "\"profile\":\"general\",\"allowed_paths\":[\"result.md\"],"
            "\"acceptance_criteria\":[\"result.md contains exactly the required Planner fixture line.\"],"
            "\"verification\":[\"Read result.md and compare its exact content.\"]}]}]}. "
            "Do not add, remove, rename, or reorder packages or microtasks. After the Planner completes, read its exact "
            "attempt_result_ref with read_worker_result and treat result_view as a non-authoritative display projection. "
            "Follow the server-returned next_action and management contract verbatim: call manage_orchestration with "
            "intent=plan_approval and payload.decision=prompt; do not call continue_orchestration for this plan wave, "
            "do not invent a step/results continuation, and do not close the Planner before the approval management "
            "receipt. Render the returned chat_interaction as the bounded plan review. The user pre-authorized this "
            "fixture, so after that prompt receipt submit only the embedded Approve action arguments (including the "
            "server-provided request_id), never a self-authored request_id or approval payload. Follow the approval "
            "response next_action exactly: close the completed Planner child only when the server permits it, then "
            "invoke every returned implementation dispatch in order. Implementation creates result.md. Do not bypass "
            "approval or edit .codex/cortex."
        )
    raise ValueError(f"unsupported v11 live-evaluator scenario: {scenario}")


def _live_eval(
    base: Path, scenarios: tuple[str, ...] | None = None, *, timeout_seconds: int = LIVE_TIMEOUT_SECONDS,
    retain_failure_metadata: bool = False, host_store: Path,
) -> list[dict[str, object]]:
    codex = shutil.which("codex")
    if not codex:
        return [{"status": "SKIP", "reason": "codex runtime unavailable; no live evidence"}]
    results: list[dict[str, object]] = []
    for scenario in scenarios or (
        "automatic_sequential", "compact_parallel",
        "planner_work_breakdown", "planner_patch_repair", "automatic_governance",
    ):
        project = base / f"live-{scenario}"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        (project / "README.md").write_text("# Luna high Cortex fixture\n", encoding="utf-8")
        source_task_ref = None
        source_snapshot: tuple[dict[str, object], dict[str, object]] | None = None
        if scenario == "follow_up_partial":
            source = cortex.start_orchestration({
                "project_root": str(project),
                "task": task("Complete a deterministic source fixture before follow-up testing.", "C1"),
            })
            completed_source = finish(project, source)
            if completed_source.get("outcome") != "completed":
                raise AssertionError(completed_source)
            source_task_ref = str(source["task_ref"])
            source_dir = canonical_task_directories(project)[0]
            source_snapshot = (
                cortex.load_task_definition(source_dir),
                cortex.load_task_state_for_artifact(source_dir),
            )
        # This is a private development source evaluator, not installed-plugin
        # validation.  It must never use the destructive approval/sandbox
        # bypass; installed evidence uses its separately audited CLI shape.
        command = [
            codex, "exec", "--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
            "-C", str(project),
            "-m", "gpt-5.6-luna", "-c", 'model_reasoning_effort="high"',
            "-c", f'mcp_servers.cortex.command="{sys.executable}"',
            # Codex starts stdio MCP servers with their declared server
            # environment, rather than inheriting every evaluator variable.
            # Make the disposable host store an explicit *launch* setting so
            # the subprocess and this read-only verifier resolve the same
            # opaque project ledger.  This is not a JSON-RPC/MCP tool input.
            "-c", f'mcp_servers.cortex.env.CORTEX_HOST_STATE_DIR="{host_store}"',
            # Desktop gives the root and spawned native workers this same MCP
            # definition.  Leave its audience unspecified so both can use the
            # fresh default union registry; explicit trusted hosts still select a
            # strict coordinator or worker projection themselves.
            "-c", f'mcp_servers.cortex.args=["{SERVER}"]',
            live_prompt(scenario, project, source_task_ref),
        ]
        with isolated_codex_runtime(base, host_store=host_store) as isolated_environment:
            streamed = run_live_command(
                command, project, scenario, timeout_seconds=timeout_seconds,
                environment=isolated_environment,
            )
        events = list(streamed["events"])
        ledger_audit_records = list(streamed.get("ledger_audit_records") or [])
        attempt_event_key_audit = safe_attempt_event_key_audit(project)
        tool_names: list[str] = []
        completed_tool_names: list[str] = []
        native_tool_names: list[str] = []
        completed_native_tool_names: list[str] = []
        failed_public_calls: list[str] = []
        for event in events:
            if not isinstance(event, dict) or event.get("event") != "cortex_mcp_call":
                continue
            name = event.get("tool")
            if isinstance(name, str):
                tool_names.append(name)
                if event.get("status") == "completed":
                    completed_tool_names.append(name)
                    if event.get("ok") is False:
                        failed_public_calls.append(name)
        for event in events:
            if not isinstance(event, dict) or event.get("event") != "native_tool_call":
                continue
            name = event.get("tool")
            if isinstance(name, str):
                native_tool_names.append(name)
                if event.get("status") == "completed":
                    completed_native_tool_names.append(name)
        task_dirs = canonical_task_directories(project)
        task_dir = task_dirs[0] if len(task_dirs) == 1 else None
        if scenario == "follow_up_partial":
            task_dir = next(
                (path for path in task_dirs if isinstance(cortex.load_task_definition(path).get("follow_up"), dict)),
                None,
            )
        if task_dir is None:
            failure_result = {
                "scenario": scenario, "status": "FAIL",
                "launch_model": "gpt-5.6-luna", "launch_reasoning_effort": "high",
                "exit_code": streamed["returncode"], "elapsed_seconds": streamed["elapsed_seconds"],
                "termination": streamed["termination"], "dropped_stream_events": streamed["dropped_stream_events"],
                "ledger_audit_records": ledger_audit_records,
                "attempt_event_key_audit": attempt_event_key_audit,
                "tool_names": tool_names, "native_tool_names": native_tool_names,
                "failed_public_calls": failed_public_calls,
                "checks": {
                    "process_ok": streamed["returncode"] == 0,
                    "task_state_available": False,
                    "single_task": False,
                    "no_failed_public_calls": not failed_public_calls,
                },
                "state_diagnostics": {"status": "unavailable", "task_count": len(task_dirs)},
            }
            record_failure_metadata(failure_result, events, retain=retain_failure_metadata)
            results.append(failure_result)
            break
        try:
            state = cortex.load_task_state_for_artifact(task_dir)
        except (OSError, ValueError, sqlite3.Error):
            failure_result = {
                "scenario": scenario, "status": "FAIL",
                "launch_model": "gpt-5.6-luna", "launch_reasoning_effort": "high",
                "exit_code": streamed["returncode"], "elapsed_seconds": streamed["elapsed_seconds"],
                "termination": streamed["termination"], "dropped_stream_events": streamed["dropped_stream_events"],
                "ledger_audit_records": ledger_audit_records,
                "attempt_event_key_audit": attempt_event_key_audit,
                "tool_names": tool_names, "native_tool_names": native_tool_names,
                "failed_public_calls": failed_public_calls,
                "checks": {
                    "process_ok": streamed["returncode"] == 0,
                    "task_state_available": False,
                    "single_task": len(task_dirs) == 1,
                    "no_failed_public_calls": not failed_public_calls,
                },
                "state_diagnostics": {"status": "unavailable", "task_count": len(task_dirs)},
            }
            record_failure_metadata(failure_result, events, retain=retain_failure_metadata)
            results.append(failure_result)
            break
        ledger = cortex.ledger_root({"project_root": str(project)})
        result_records = canonical_attempt_result_records(ledger, state) if task_dir else []
        strict_results = canonical_results_are_strict(result_records)
        terminal_result_audit = safe_terminal_result_audit(state, result_records)
        native_terminal_audit = safe_native_terminal_audit(events)
        question_resolution_audit = safe_question_resolution_audit(ledger, state, result_records)
        question_records = cortex._question_records(cortex.question_bus_paths(task_dir), state)
        sequential_question_audit = automatic_sequential_question_audit(question_records)
        attempt_results_valid = all(
            isinstance(record.get("result"), dict)
            and record["result"].get("lifecycle_status") == "COMPLETED"
            and record["result"].get("status") == "completed"
            and isinstance(record["result"].get("findings"), list)
            and isinstance(record["result"].get("unresolved"), list)
            and not record["result"].get("unresolved")
            for record in result_records
            if record.get("gate") in {
                "review", "governance_activation", "governance_close", "close",
            }
        )
        attempts_by_wave: dict[str, set[str]] = {}
        for attempt in state.get("attempts", []):
            if attempt.get("invalidated"):
                continue
            attempts_by_wave.setdefault(str(attempt.get("orchestration_wave_id") or ""), set()).add(str(attempt.get("gate") or ""))
        parallel_exercised = any(len(gates) > 1 for gates in attempts_by_wave.values())
        close_evidence = any(
            item.get("gate") == "close" and item.get("verified_execution") and item.get("exit_code") == 0
            for item in state.get("evidence", [])
        )
        planning_manifest = evaluation_planning_manifest(task_dir)
        checks = {
            "process_ok": streamed["returncode"] == 0,
            "used_start": "start_orchestration" in tool_names,
            "used_continue": "continue_orchestration" in tool_names,
            "avoided_private_tools": "orchestrate" not in tool_names,
            "single_task": len(task_dirs) == 1,
            "completed": state.get("status") == "completed",
            "close_evidence": close_evidence,
            "handoff": bool(state.get("handoff_created")),
            "manifest_snapshots_cleaned": (
                (state.get("manifest_snapshot_cleanup") or {}).get("status") == "completed"
                and bool(task_dir)
                and not any(
                    cortex.db_get_manifest_snapshot(
                        cortex.ledger_root({"project_root": str(project)}), str(reference)
                    ) is not None
                    for reference in [
                        state.get("initial_manifest_ref"),
                        *[
                            item.get("result_baseline_ref")
                            for item in state.get("attempts", [])
                            if isinstance(item, dict)
                        ],
                    ]
                    if reference
                )
            ),
            "strict_worker_results": strict_results,
            "terminal_canonical_result_audit": (
                terminal_result_audit["all_accepted_attempts_have_terminal_canonical_results"] is True
            ),
            "native_terminal_result_audit": (
                native_terminal_audit["all_observed_workers_terminally_audited"] is True
                and native_terminal_audit["spawned_worker_observations"]
                == terminal_result_audit["accepted_attempts"]
                and streamed["dropped_stream_events"] == 0
            ),
            "review_close_attempt_results": attempt_results_valid,
            "no_failed_public_calls": not failed_public_calls,
            "one_start": completed_tool_names.count("start_orchestration") == 1,
            "native_dispatch_exercised": "spawn_agent" in completed_native_tool_names,
            "native_wait_exercised": "wait" in completed_native_tool_names,
            "native_cleanup_exercised": "close_agent" in completed_native_tool_names,
            "attempt_event_keys_unique_per_attempt": attempt_event_key_audit_passed(attempt_event_key_audit),
        }
        if scenario == "compact_parallel":
            checks["parallel_wave_exercised"] = parallel_exercised
            # The source command deliberately runs without trusted native
            # hooks and its privacy-safe telemetry has no child identity.
            # Two adjacent parallel spawns are therefore indistinguishable
            # from transport echoes.  Do not let an aggregate result count
            # masquerade as an exact per-child terminal audit.
            checks["parallel_native_identity_verifiable"] = False
        if scenario == "automatic_sequential":
            checks["decision_complete_fixture_has_no_questions"] = (
                sequential_question_audit["no_unexpected_questions"] is True
            )
        if scenario == "planner_work_breakdown":
            package_artifacts = planning_manifest.get("work_packages") if isinstance(planning_manifest, dict) else []
            checks["plan_approval_exercised"] = state.get("plan_approval", {}).get("status") == "approved"
            checks["planning_manifest"] = (
                planning_manifest.get("schema") == "cortex/planning/v1"
                and len(package_artifacts) >= 2
                and all(
                    isinstance(package, dict)
                    and cortex.db_get_artifact_for_export_path(
                        ledger, str(state["task_id"]), str(package.get("artifact_path") or ""),
                    ) is not None
                    for package in package_artifacts
                )
            )
        if scenario == "planner_patch_repair":
            repair_storage = planner_repair_storage_audit(ledger, task_dir, state)
            current_manifest = evaluation_planning_manifest(task_dir)
            repair_projection = current_manifest.get("repair") if isinstance(current_manifest, dict) else {}
            repair_paths = repair_projection.get("patch_paths") if isinstance(repair_projection, dict) else []
            planner_results = [
                record for record in result_records
                if record.get("gate") == "plan" and isinstance(record.get("result"), dict)
            ]
            checks = {
                "process_ok": streamed["returncode"] == 0,
                "used_one_start": completed_tool_names.count("start_orchestration") == 1,
                "single_task": len(task_dirs) == 1,
                "one_planner_attempt": len([item for item in state.get("attempts", []) if item.get("gate") == "plan" and not item.get("invalidated")]) == 1,
                "initial_malformed_draft_retained": repair_storage.get("rejected_draft_count") == 1,
                "patch_only_repair_once": isinstance(repair_projection, dict) and repair_projection.get("mode") == "same_attempt_patch" and repair_projection.get("patch_count") == 2,
                "repair_has_json_pointer_paths": isinstance(repair_paths, list) and len(repair_paths) == 2 and all(isinstance(path, str) and path.startswith("/") for path in repair_paths),
                "same_attempt_finalized": len(planner_results) == 1 and planner_results[0]["result"].get("lifecycle_status") == "COMPLETED",
                "rejected_draft_retained": repair_storage.get("rejected_draft_count") == 1,
                "valid_fields_preserved": repair_storage.get("valid_fields_preserved") is True,
                "only_diagnostic_fields_repaired": repair_storage.get("only_diagnostic_fields_repaired") is True,
                "no_replacement_worker": completed_native_tool_names.count("spawn_agent") == 1,
                "no_failed_public_calls": not failed_public_calls,
            }
            checks["storage_repair_audit"] = repair_storage
        if scenario == "automatic_governance":
            task_definition = cortex.load_task_definition(task_dir, state) if task_dir else {}
            governance = state.get("governance") if isinstance(state.get("governance"), dict) else {}
            governance_attempts = [
                item for item in state.get("attempts", [])
                if isinstance(item, dict)
                and not item.get("invalidated")
                and item.get("gate") in {"governance_activation", "governance_close"}
            ]
            governance_attempts_by_gate = {
                gate: [item for item in governance_attempts if item.get("gate") == gate]
                for gate in ("governance_activation", "governance_close")
            }
            resultless_governance_attempts = [
                item for item in state.get("attempts", [])
                if isinstance(item, dict)
                and item.get("gate") in {"governance_activation", "governance_close"}
                and item.get("completion_transport_status") == "not_recorded"
            ]
            governance_evidence = [
                item for item in state.get("evidence", [])
                if isinstance(item, dict)
                and not item.get("invalidated")
                and item.get("gate") in {"governance_activation", "governance_close"}
            ]
            observed_obligations = {
                str(obligation)
                for item in governance_evidence
                for obligation in item.get("governance_obligations", [])
            }
            required_obligations = set(
                cortex._governance_obligations_for_gate(state, "governance_close")
            )
            question_policy = live_question_policy(scenario)
            if question_policy is None:
                raise RuntimeError("automatic governance requires an explicit live question fixture")
            question_records = cortex._question_records(cortex.question_bus_paths(task_dir), state)
            authorized_question_records = (
                len(question_records) <= int(question_policy["maximum_questions"])
                and all(isinstance(question, dict) for question in question_records)
                and all(
                    question_matches_pre_authorized_policy(question, question_policy)
                    and question.get("status") == "answered"
                    and question.get("answer_text") == question_policy["preauthorized_answer"]
                    for question in question_records
                    if isinstance(question, dict)
                )
            )
            question_resume_completed = (
                not question_records
                or (
                    authorized_question_records
                    and question_resolution_audit["all_question_attempts_resolved_before_result"] is True
                    and observed_question_resume_lifecycle(events)
                )
            )
            checks.update({
                "governance_not_forced": "manage_governance" not in tool_names,
                "governance_requested_auto": governance.get("requested_mode") == "auto",
                "governance_resolved_full": governance.get("effective_mode") == "full",
                "governance_reason_is_c3": (
                    task_definition.get("complexity") == "C3"
                    and "complexity:C3" in governance.get("reasons", [])
                ),
                "server_governance_pipeline": state.get("current_pipeline") == [
                    "governance_activation", "implementation", "documentation",
                    "governance_close", "close",
                ],
                "governance_gates_completed": {
                    "governance_activation", "governance_close",
                }.issubset(set(state.get("completed_gates", []))),
                "independent_governance_reviewers": (
                    all(
                        len(attempts) == 1
                        and attempts[0].get("agent") == "code_reviewer"
                        and attempts[0].get("status") == "passed"
                        for attempts in governance_attempts_by_gate.values()
                    )
                ),
                "resultless_governance_recovery_contained": all(
                    item.get("status") in {"failed", "cancelled", "superseded"}
                    and item.get("invalidated") is True
                    and item.get("invalidation_reason") == "retry_after_failure"
                    and not item.get("attempt_result_refs")
                    for item in resultless_governance_attempts
                ),
                "typed_immutable_governance_evidence": (
                    len(governance_evidence) == 2
                    and required_obligations.issubset(observed_obligations)
                    and all(item.get("artifact_immutable") is True for item in governance_evidence)
                    and all(item.get("artifact_verified") is True for item in governance_evidence)
                    and all(item.get("verified_execution") is True for item in governance_evidence)
                ),
                "question_authority_is_fixture_bound": authorized_question_records,
                "question_resume_consumed_before_close": question_resume_completed,
            })
        if scenario == "follow_up_partial":
            source_dir = next((path for path in task_dirs if path != task_dir), None)
            corrective_task = cortex.load_task_definition(task_dir) if task_dir else {}
            source_unchanged = bool(source_dir and source_snapshot and (
                cortex.load_task_definition(source_dir),
                cortex.load_task_state_for_artifact(source_dir),
            ) == source_snapshot)
            checks = {
                "process_ok": streamed["returncode"] == 0,
                "used_follow_up": "manage_orchestration" in tool_names,
                "avoided_start_and_continue": "start_orchestration" not in tool_names and "continue_orchestration" not in tool_names,
                "avoided_private_tools": "orchestrate" not in tool_names,
                "created_one_linked_corrective_task": len(task_dirs) == 2 and task_dir is not None,
                "source_unchanged": source_unchanged,
                "corrective_task_active": state.get("status") == "active",
                "follow_up_link": corrective_task.get("follow_up", {}).get("source_task_ref") == source_task_ref,
                "first_corrective_dispatch_prepared": bool(state.get("attempts")) and state["attempts"][0].get("gate") in {"plan", "discover"},
                "no_failed_public_calls": not failed_public_calls,
            }
        if scenario == "bootstrap_missing_inputs":
            checks = {
                "process_ok": streamed["returncode"] == 0,
                "used_one_start": completed_tool_names.count("start_orchestration") == 1,
                "one_native_spawn": completed_native_tool_names.count("spawn_agent") == 1,
                "one_same_child_repair": completed_native_tool_names.count("followup_task") == 1,
                "no_question_or_management_route": (
                    not question_records
                    and "worker_question" not in completed_tool_names
                    and "manage_orchestration" not in completed_tool_names
                ),
                "no_replacement_or_ambient_native_route": (
                    completed_native_tool_names.count("spawn_agent") == 1
                    and completed_native_tool_names.count("followup_task") == 1
                    and not ({"send_message", "interrupt_agent", "list_agents"} & set(completed_native_tool_names))
                ),
                "avoided_private_tools": "orchestrate" not in tool_names,
                "single_task": len(task_dirs) == 1,
                "bootstrap_result_created_after_repair": (
                    (project / "result.md").read_text(encoding="utf-8") == BOOTSTRAP_RECOVERY_RESULT + "\n"
                    if (project / "result.md").is_file() else False
                ),
                "strict_worker_result": strict_results and len(result_records) == 1,
                "terminal_canonical_result_audit": (
                    terminal_result_audit["all_accepted_attempts_have_terminal_canonical_results"] is True
                    and terminal_result_audit["accepted_attempts"] == 1
                ),
                "native_terminal_result_audit": (
                    native_terminal_audit["all_observed_workers_terminally_audited"] is True
                    and native_terminal_audit["spawned_worker_observations"] == 1
                    and streamed["dropped_stream_events"] == 0
                ),
                "native_bootstrap_repair_lifecycle": observed_bootstrap_repair_lifecycle(events, recovered=True),
                "no_failed_public_calls": not failed_public_calls,
            }
        passed = all(checks.values())
        safe_rework_pause = state.get("rework_pause") if isinstance(state.get("rework_pause"), dict) else {}
        safe_closure_rework = state.get("closure_rework") if isinstance(state.get("closure_rework"), dict) else {}
        state_diagnostics = {
            "status": state.get("status"),
            "active_gates": list(cortex.active_gates(state)) if state else [],
            "completed_gates": list(state.get("completed_gates", [])),
            "attempts": [
                {
                    "gate": item.get("gate"),
                    "status": item.get("status"),
                    "invalidated": bool(item.get("invalidated")),
                    "completion_transport_status": item.get("completion_transport_status"),
                    "gate_decision": item.get("gate_decision"),
                }
                for item in state.get("attempts", [])
                if isinstance(item, dict)
            ],
            "gate_failure_counts": dict(
                state.get("orchestrate_gate_failure_counts")
                if isinstance(state.get("orchestrate_gate_failure_counts"), dict)
                else {}
            ),
            "rework_pause": {
                key: safe_rework_pause.get(key)
                for key in (
                    "status", "gate", "consecutive_identical_iterations",
                    "repeat_limit", "failure_class",
                )
                if key in safe_rework_pause
            },
            "closure_rework": [
                {
                    "source_gate": source_gate,
                    "target_gate": item.get("target_gate"),
                    "status": item.get("status"),
                    "finding_count": len(item.get("finding_fingerprints") or []),
                }
                for source_gate, item in safe_closure_rework.items()
                if isinstance(item, dict)
            ],
        }
        results.append({
            "scenario": scenario, "status": "PASS" if passed else "FAIL",
            "launch_model": "gpt-5.6-luna", "launch_reasoning_effort": "high",
            "exit_code": streamed["returncode"], "elapsed_seconds": streamed["elapsed_seconds"],
            "termination": streamed["termination"], "dropped_stream_events": streamed["dropped_stream_events"],
            "ledger_audit_records": ledger_audit_records,
            "attempt_event_key_audit": attempt_event_key_audit,
            "terminal_result_audit": terminal_result_audit,
            "native_terminal_audit": native_terminal_audit,
            "question_resolution_audit": question_resolution_audit,
            "question_audit": (
                sequential_question_audit
                if scenario == "automatic_sequential"
                else {"not_applicable": True}
            ),
            "tool_names": tool_names,
            "native_tool_names": native_tool_names,
            "checks": checks, "failed_public_calls": failed_public_calls,
            "state_diagnostics": state_diagnostics,
            "native_identity_assurance": (
                "identity_unverifiable"
                if scenario == "compact_parallel"
                else "unambiguous_aggregate_terminal_audit"
                if checks.get("native_terminal_result_audit") is True
                else "identity_unverifiable"
            ),
            **({
                "evidence_scope": "source_mode_native_lifecycle_observed",
                "host_binding": "unavailable_without_trusted_hooks",
            } if scenario == "bootstrap_missing_inputs" else {}),
        })
        if not passed:
            record_failure_metadata(results[-1], events, retain=retain_failure_metadata)
            break
    return results


def live_eval(
    base: Path, scenarios: tuple[str, ...] | None = None, *, timeout_seconds: int = LIVE_TIMEOUT_SECONDS,
    retain_failure_metadata: bool = False,
    keep: bool = False,
) -> list[dict[str, object]]:
    """Run live source checks against one private host control store.

    ``keep`` is an explicit diagnostic opt-in for the evaluator-owned source
    store.  The ephemeral Codex home (including copied auth) is always
    removed; normal runs also remove the host store after ledger audit.
    """
    with isolated_cortex_host_store(base, keep=keep) as host_store:
        return _live_eval(
            base, scenarios, timeout_seconds=timeout_seconds,
            retain_failure_metadata=retain_failure_metadata, host_store=host_store,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="store_true",
        help="run bounded source-mode Luna-high scenarios; never installed-plugin evidence",
    )
    parser.add_argument(
        "--scenario", choices=(
            "automatic_sequential", "compact_parallel",
            "planner_work_breakdown", "planner_patch_repair", "automatic_governance", "follow_up_partial",
            "bootstrap_missing_inputs",
        ),
        help="run one live scenario for diagnosis; the default release run still requires all five",
    )
    parser.add_argument(
        "--live-timeout-seconds", type=int,
        help="bound each live source-mode scenario and terminate its complete process group on expiry",
    )
    parser.add_argument(
        "--retain-failure-metadata", action="store_true",
        help="opt in to retaining bounded sanitized live-failure metadata under /tmp",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="retain only the evaluator-owned source-mode project and host ledger after final safe audit",
    )
    args = parser.parse_args()
    timeout_seconds = args.live_timeout_seconds if args.live_timeout_seconds is not None else LIVE_TIMEOUT_SECONDS
    if timeout_seconds < 10 or timeout_seconds > 7200:
        parser.error("--live-timeout-seconds must be between 10 and 7200")
    base, base_ownership = create_owned_temp_directory(
        Path(tempfile.gettempdir()), prefix="cortex-luna-high-", purpose="evaluation_base",
    )
    output: dict[str, object]
    try:
        fixtures = fixture_eval(base)
        if args.live or os.environ.get("CORTEX_RUN_LIVE_LUNA") == "1":
            live = live_eval(
                base, (args.scenario,) if args.scenario else None,
                timeout_seconds=timeout_seconds,
                retain_failure_metadata=args.retain_failure_metadata,
                keep=args.keep,
            )
        else:
            live = [{"status": "SKIP", "reason": "live flag not supplied; no live release evidence"}]
        successful = all(item.get("status") in {"PASS", "SKIP"} for item in live)
        output = {
            "status": "PASS" if successful else "FAIL", "fixtures": fixtures, "live": live,
            **({"isolated_evaluation_path": str(base)} if args.keep else {}),
        }
    finally:
        if not args.keep and (base.exists() or base.is_symlink()):
            remove_private_runtime_home(base, base.parent, base_ownership, allow_current_owner=True)
    print(json.dumps(output, sort_keys=True))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
