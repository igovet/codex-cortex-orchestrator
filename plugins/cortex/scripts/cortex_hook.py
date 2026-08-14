#!/usr/bin/env python3
"""Privacy-preserving lifecycle telemetry for an active orchestration task."""
from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
SCHEMA = "cortex/v7"
PROFILE_CONTRACT = Path(__file__).resolve().parents[1] / "profiles.json"


def profile_names() -> set[str]:
    try:
        payload = json.loads(PROFILE_CONTRACT.read_text(encoding="utf-8"))
        return {str(item["name"]) for item in payload.get("profiles", [])}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return set()


PROFILES = profile_names()
HOOK_NAMES = {"SessionStart", "SubagentStart", "SubagentStop", "PostToolUse"}
TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
MAX_LIFECYCLE_EVENTS = 1000
MAX_LIFECYCLE_BYTES = 256 * 1024
WORKER_CONTEXT = (
    "You are an internal worker, never user-facing. Stay within delegated ownership and allowed paths; "
    "All internal worker communication, Cortex tool arguments, reports, questions, findings, and handoffs must be in English. "
    "The main coordinator translates user-facing content into the task's requested language; do not address the user directly. "
    "do not subdelegate unless the main agent explicitly authorized it. Do not cause external side effects "
    "without explicit authority and applicable approval. Never expose or persist secrets, credentials, personal "
    "data, or secret canaries. Return questions, blockers, approval needs, and handoff through the native parent "
    "channel. Do not call Cortex tools and do not invent task, wave, attempt, project, or tool identifiers. Return "
    "your final sanitized cortex/report/v1 directly to the parent with summary, findings, questions, changed_files, "
    "tests, evidence, uncertainty, and next_action. Use only tools that are actually available in this worker "
    "context; record unavailable capabilities as limitations and use a safe available fallback."
)


def reject_symlink_ancestry(path: Path, label: str) -> Path:
    candidate = path.expanduser().absolute()
    current = Path(candidate.anchor)
    parts = candidate.parts[1:] if candidate.anchor else candidate.parts
    for part in parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"{label} must not traverse symlinks")
    return candidate


def root() -> Path:
    configured = os.environ.get("CORTEX_ROOT")
    project_root = os.environ.get("CORTEX_PROJECT_ROOT")
    path = Path(configured or (Path(project_root).expanduser() if project_root else Path.cwd()) / ".codex" / "cortex")
    return reject_symlink_ancestry(path, "Cortex root")


def valid_task_id(value: object) -> str | None:
    candidate = str(value or "")
    return candidate if SAFE_ID_RE.fullmatch(candidate) else None


def active_task(ledger: Path, thread_id: str) -> str | None:
    index_path = ledger / "active-tasks.json"
    if thread_id and index_path.exists():
        try:
            found = valid_task_id(json.loads(index_path.read_text(encoding="utf-8")).get(thread_id))
            if found:
                return found
        except (OSError, json.JSONDecodeError):
            pass
    return None


def task_directory(ledger: Path, task_id: str) -> Path:
    """Resolve a v7 numbered task directory without trusting index paths."""
    tasks = ledger / "tasks"
    candidate: Path | None = None
    try:
        indexed = json.loads((ledger / "task-index.json").read_text(encoding="utf-8")).get(task_id)
        directory = indexed.get("directory") if isinstance(indexed, dict) else None
        if isinstance(directory, str) and Path(directory).name == directory and directory not in {"", ".", ".."}:
            candidate = tasks / directory
    except (OSError, json.JSONDecodeError):
        pass
    tasks_absolute = tasks.absolute()
    if candidate is not None:
        try:
            candidate.absolute().relative_to(tasks_absolute)
            candidate = reject_symlink_ancestry(candidate, "task directory")
            if not candidate.is_dir():
                raise ValueError("task directory is unavailable")
            task_file = candidate / "task.json"
            reject_symlink_ancestry(task_file, "task file")
            task = json.loads(task_file.read_text(encoding="utf-8"))
            if task.get("schema") == SCHEMA and task.get("task_id") == task_id:
                return candidate
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return tasks / f"missing-{task_id}"


def activation(ledger: Path, thread_id: str) -> dict | None:
    if not thread_id:
        return None
    path = ledger / "activations.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8")).get(thread_id)
        if isinstance(record, dict) and record.get("schema") == SCHEMA and record.get("coordinator") == "main" and record.get("mode") == "main-orchestrator":
            return record
        return None
    except (OSError, json.JSONDecodeError):
        return None


def canonical_agent_name(event: dict) -> str | None:
    """Return the profile name supplied by Codex, without accepting free text."""
    candidate = str(event.get("agent_type", "")).strip()
    return candidate if candidate in PROFILES else None


def append_lifecycle_event(task_dir: Path, event: dict) -> None:
    event_path = reject_symlink_ancestry(task_dir / "lifecycle-events.jsonl", "lifecycle event file")
    meta_path = reject_symlink_ancestry(task_dir / "lifecycle-events-meta.json", "lifecycle event metadata")
    lock_path = reject_symlink_ancestry(task_dir / ".lifecycle-events.lock", "lifecycle event lock")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise OSError("O_NOFOLLOW is required for lifecycle telemetry")
    lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | nofollow, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(lock_descriptor).st_mode):
            raise ValueError("lifecycle event lock must be a regular file")
        os.fchmod(lock_descriptor, 0o600)
        if fcntl is not None:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        lines = []
        if event_path.exists():
            descriptor = os.open(event_path, os.O_RDONLY | nofollow)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("lifecycle event file must be a regular file")
                content = b""
                while True:
                    chunk = os.read(descriptor, 65536)
                    if not chunk:
                        break
                    content += chunk
                    if len(content) > MAX_LIFECYCLE_BYTES * 4:
                        break
                lines = [line for line in content.splitlines() if line]
            finally:
                os.close(descriptor)
        lines.append(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        dropped = 0
        while len(lines) > MAX_LIFECYCLE_EVENTS or sum(len(line) + 1 for line in lines) > MAX_LIFECYCLE_BYTES:
            lines.pop(0)
            dropped += 1
        prior_dropped = 0
        if meta_path.exists():
            prior_dropped = int(json.loads(meta_path.read_text(encoding="utf-8")).get("dropped", 0))

        def atomic(path: Path, payload: bytes) -> None:
            if path.exists() and (path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode)):
                raise ValueError("lifecycle telemetry target must be a regular file")
            descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            try:
                os.fchmod(descriptor, 0o600)
                written = 0
                while written < len(payload):
                    count = os.write(descriptor, payload[written:])
                    if count <= 0:
                        raise OSError("lifecycle telemetry write made no progress")
                    written += count
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = -1
                os.replace(temporary, path)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                if os.path.exists(temporary):
                    os.unlink(temporary)
        atomic(event_path, b"\n".join(lines) + b"\n")
        atomic(meta_path, (json.dumps({"dropped": prior_dropped + dropped}, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(lock_descriptor)


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        print("{}")
        return
    try:
        ledger = root()
        thread_id = str(event.get("thread_id", ""))
        agent_name = canonical_agent_name(event)
        active = activation(ledger, thread_id)
        task_id = active_task(ledger, thread_id)
    except Exception as exc:
        print(f"orchestration_hook warning: {type(exc).__name__}", file=sys.stderr)
        print("{}")
        return
    if not active or not task_id or active.get("task_id") != task_id:
        print("{}")
        return
    task_dir = task_directory(ledger, task_id)
    state_file = task_dir / "current.json"
    try:
        task_dir = reject_symlink_ancestry(task_dir, "task directory")
        state_file = reject_symlink_ancestry(state_file, "task state file")
        if not state_file.exists():
            raise FileNotFoundError(str(state_file))
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if state.get("schema") != SCHEMA or state.get("task_id") != task_id:
            raise ValueError("unsupported or mismatched task state")
        safe = {
            "at": datetime.now(timezone.utc).isoformat(),
            "hook": str(event.get("hook_event_name")) if str(event.get("hook_event_name")) in HOOK_NAMES else "unknown",
            "agent_type": agent_name,
            "display_name": agent_name,
            "thread_id_digest": hashlib.sha256(str(event.get("thread_id", "")).encode("utf-8")).hexdigest() if event.get("thread_id") else None,
            "tool_name": str(event.get("tool_name")) if TOOL_NAME_RE.fullmatch(str(event.get("tool_name", ""))) else None,
        }
        append_lifecycle_event(task_dir, safe)
        if safe["hook"] in {"SessionStart", "SubagentStart"} and agent_name:
            print(json.dumps({"additionalContext": f"Canonical agent name: {agent_name}. Use exactly this value as the subagent display name and thread label. {WORKER_CONTEXT}"}, ensure_ascii=False))
            return
        if safe["hook"] == "SessionStart":
            current_gates = state.get("current_gates") or [state.get("current_gate", "unknown")]
            context = f"Active orchestration task: {task_id}; status: {state.get('status', 'unknown')}; current executable gates: {', '.join(str(item) for item in current_gates)}. Use cortex before dispatching or closing a gate."
            context += " Main-agent coordination contract: delegate all project inspection, search, execution, testing, and editing to hidden workers; route worker questions, escalations, blockers, and handoffs through the main chat."
            print(json.dumps({"additionalContext": context}, ensure_ascii=False))
            return
    except Exception as exc:
        # Hooks are telemetry-only; never inject untrusted exception text into model context.
        print(f"orchestration_hook warning: {type(exc).__name__}", file=sys.stderr)
    print("{}")


if __name__ == "__main__":
    main()
