"""Bounded signed observations of supported native agent projections.

Only the host hook issues these records. They contain routing digests and
states, never prompts, messages, tool output, credentials or agent reports.
They attest a host observation, not filesystem isolation or process ancestry.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time
from collections.abc import Mapping
from typing import Any

from cortex_runtime.audience_attestation import _key, _private_directory, _private_file, _sign, _canonical
from cortex_runtime.host_boundary import normalize_agent_projection


DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_AGE_NS = 300 * 1_000_000_000


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _path(plugin_data: Path, task_digest: str, *, create: bool) -> Path:
    if not isinstance(task_digest, str) or DIGEST.fullmatch(task_digest) is None:
        raise ValueError("invalid task binding")
    root = plugin_data / "activation" / "native-observations"
    for directory in (plugin_data, plugin_data / "activation", root):
        if directory.is_symlink():
            raise ValueError("native observation directory is a symlink")
    _key(plugin_data, create=create)
    _private_directory(root, create=create)
    return root / (task_digest + ".json")


def _write(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(12) + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read(path: Path, key: bytes) -> dict[str, Any]:
    _private_file(path)
    if path.stat().st_size > 64 * 1024:
        raise ValueError("native observation bound exceeded")
    value = json.loads(path.read_text(encoding="ascii"))
    if not isinstance(value, dict) or not hmac.compare_digest(str(value.get("signature", "")), _sign(value, key)):
        raise ValueError("native observation signature invalid")
    return value


def bind_task(plugin_data: Path, *, task_digest: str, session_digest: str) -> bool:
    """Bind the task to its creating native coordinator, never a copied task."""
    if DIGEST.fullmatch(session_digest) is None:
        return False
    try:
        import fcntl
        path = _path(plugin_data, task_digest, create=True)
        lock_path = path.with_suffix(".lock")
        with os.fdopen(os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            key = _key(plugin_data, create=False)
            if path.exists():
                existing = _read(path, key)
                return existing.get("task") == task_digest and existing.get("session") == session_digest
            value = {"version": 1, "task": task_digest, "session": session_digest, "observation": None}
            _write(path, {**value, "signature": _sign(value, key)})
        return True
    except (OSError, ValueError, TypeError, RuntimeError):
        return False


def record_projection(plugin_data: Path, *, task_digest: str, session_digest: str,
                      revision: int, barrier_epoch: int, response: Any, arguments: Any) -> bool:
    agents = normalize_agent_projection(response, arguments)
    if agents is None or type(revision) is not int or revision < 1 or type(barrier_epoch) is not int or barrier_epoch < 0:
        return False
    try:
        import fcntl
        path = _path(plugin_data, task_digest, create=False)
        with os.fdopen(os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_NOFOLLOW), "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            key = _key(plugin_data, create=False)
            value = _read(path, key)
            if value.get("task") != task_digest or value.get("session") != session_digest:
                return False
            value["observation"] = {"revision": revision, "barrier_epoch": barrier_epoch,
                "observed_at": time.time_ns(), "agents": agents}
            value["signature"] = _sign(value, key)
            _write(path, value)
        return True
    except (OSError, ValueError, TypeError, RuntimeError):
        return False


def owns_task(plugin_data: Path, *, task_digest: str, session_digest: str) -> bool:
    try:
        value = _read(_path(plugin_data, task_digest, create=False), _key(plugin_data, create=False))
        return value.get("task") == task_digest and value.get("session") == session_digest
    except (OSError, ValueError, TypeError, RuntimeError):
        return False


def verified_projection(plugin_data: Path, *, task_digest: str, revision: int, barrier_epoch: int) -> dict[str, Any] | None:
    try:
        path = _path(plugin_data, task_digest, create=False)
        value = _read(path, _key(plugin_data, create=False))
        observation = value.get("observation")
        if (value.get("task") != task_digest or not isinstance(observation, dict)
                or observation.get("revision") != revision or observation.get("barrier_epoch") != barrier_epoch
                or type(observation.get("observed_at")) is not int
                or not 0 <= time.time_ns() - observation["observed_at"] <= MAX_AGE_NS):
            return None
        return observation
    except (OSError, ValueError, TypeError, RuntimeError):
        return None


def quiescent(observation: Mapping[str, Any], protected_task_name: str) -> bool:
    matches = [item for item in observation["agents"] if item["name"] == digest(protected_task_name)]
    return not matches or (len(matches) == 1 and matches[0]["state"] == "idle")
