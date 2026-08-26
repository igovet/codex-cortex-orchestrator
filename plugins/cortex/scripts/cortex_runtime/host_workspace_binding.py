"""Trusted host binding between a Codex session and its workspace.

The MCP stdio process does not have a reliable working directory for the
addressed Codex thread.  The host's SessionStart envelope is therefore the
only accepted source for the coordinator workspace.  This module keeps that
small mapping in the private host state directory; it never stores task,
dispatch, or worker data.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping


STATE_FILE = "session-workspaces.json"
LOCK_FILE = "session-workspaces.lock"
MAX_SESSIONS = 256
MAX_ID = 256


def _state_root() -> Path:
    configured = str(os.environ.get("CORTEX_HOST_STATE_DIR") or "").strip()
    root = Path(configured) if configured else Path.home() / ".codex" / "cortex"
    if not root.is_absolute():
        raise ValueError("host state root must be absolute")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("host state root is not private")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError("host state root ownership is unsafe")
    os.chmod(root, 0o700)
    return root


def _valid_session(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_ID and "\x00" not in value


def _safe_workspace(value: object) -> Path | None:
    if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        return None
    try:
        current = Path(candidate.anchor)
        for part in candidate.parts[1:]:
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                return None
        resolved = candidate.resolve(strict=True)
        if not resolved.is_dir():
            return None
        return resolved
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _load(root: Path) -> dict[str, str]:
    path = root / STATE_FILE
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if _valid_session(key) and _safe_workspace(value) is not None
    }


def _with_lock(root: Path):
    descriptor = os.open(root / LOCK_FILE, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.chmod(root / LOCK_FILE, 0o600)
    try:
        try:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except ImportError:
            pass
        yield descriptor
    finally:
        try:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        os.close(descriptor)


def bind_session_workspace(session_id: object, cwd: object) -> bool:
    """Persist one validated SessionStart session-to-workspace binding."""
    if not _valid_session(session_id):
        return False
    workspace = _safe_workspace(cwd)
    if workspace is None:
        return False
    root = _state_root()
    from contextlib import contextmanager
    locked = contextmanager(_with_lock)
    with locked(root):
        mappings = _load(root)
        existing = mappings.get(str(session_id))
        if existing and existing != str(workspace):
            return False
        mappings[str(session_id)] = str(workspace)
        while len(mappings) > MAX_SESSIONS:
            mappings.pop(next(iter(mappings)))
        target = root / STATE_FILE
        temporary = root / f".{STATE_FILE}.{os.getpid()}.tmp"
        temporary.write_text(json.dumps(mappings, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    return True


def workspace_for_session(session_id: object) -> Path | None:
    if not _valid_session(session_id):
        return None
    try:
        value = _load(_state_root()).get(str(session_id))
    except (OSError, TypeError, ValueError):
        return None
    return _safe_workspace(value)


def bound_workspaces_for_private_lookup() -> tuple[Path, ...]:
    """Return the bounded trusted workspace set for private host resolution."""
    try:
        values = _load(_state_root()).values()
    except (OSError, TypeError, ValueError):
        return ()
    unique: dict[str, Path] = {}
    for value in values:
        workspace = _safe_workspace(value)
        if workspace is not None:
            unique[str(workspace)] = workspace
    return tuple(unique[key] for key in sorted(unique))


def session_start_keyset(value: Mapping[str, Any]) -> str:
    """Return a sanitized keyset label for diagnostics and focused tests."""
    return ",".join(sorted(str(key) for key in value))
