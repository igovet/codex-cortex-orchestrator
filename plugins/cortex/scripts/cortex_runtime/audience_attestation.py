"""Signed one-shot host audience attestations for Cortex MCP connections."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any


THREAD_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_BYTES = 32
_KEY_NAME = "audience-attestation.key"
_CANDIDATE_MAX_AGE_NS = 5 * 60 * 1_000_000_000


class AudienceAttestationError(RuntimeError):
    pass


def thread_digest(thread_id: object) -> str | None:
    if not isinstance(thread_id, str) or THREAD_ID_RE.fullmatch(thread_id) is None:
        return None
    return hashlib.sha256(thread_id.encode("ascii")).hexdigest()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")


def _private_directory(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise AudienceAttestationError("audience directory is not owner-only")


def _private_file(path: Path) -> None:
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise AudienceAttestationError("audience record is not owner-only")


def _activation_root(plugin_data: Path, *, create: bool = False) -> Path:
    _private_directory(plugin_data, create=create)
    root = plugin_data / "activation"
    _private_directory(root, create=create)
    return root


def _key(plugin_data: Path, *, create: bool) -> bytes:
    root = _activation_root(plugin_data, create=create)
    path = root / _KEY_NAME
    if create and not path.exists():
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(secrets.token_bytes(_KEY_BYTES))
                stream.flush()
                os.fsync(stream.fileno())
    _private_file(path)
    value = path.read_bytes()
    if len(value) != _KEY_BYTES:
        raise AudienceAttestationError("audience signing key is invalid")
    return value


def _sign(value: Mapping[str, Any], key: bytes) -> str:
    unsigned = {name: item for name, item in value.items() if name != "signature"}
    return hmac.new(key, _canonical(unsigned), hashlib.sha256).hexdigest()


def issue_worker_candidate(plugin_data: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    """Return one signed SubagentStart-bound worker-candidate record."""
    value = dict(record)
    for name in (
        "session_digest", "assignment_ref_digest", "worker_task_ref_digest",
        "worker_agent_digest", "worker_turn_digest", "worker_thread_digest",
    ):
        if _DIGEST_RE.fullmatch(str(value.get(name, ""))) is None:
            raise AudienceAttestationError("worker candidate identity is invalid")
    value.update({
        "version": 4,
        "state": "worker_candidate",
        "audience": "worker_candidate",
        "attestation_nonce": secrets.token_hex(32),
        "attested_at": time.time_ns(),
    })
    key = _key(plugin_data, create=True)
    value["signature"] = _sign(value, key)
    return value


def _verified_candidate(
    value: object, key: bytes, *, states: frozenset[str],
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    record = dict(value)
    signature = record.get("signature")
    if not isinstance(signature, str) or not hmac.compare_digest(signature, _sign(record, key)):
        return None
    if (
        record.get("version") != 4
        or record.get("state") not in states
        or record.get("audience") != "worker_candidate"
        or not isinstance(record.get("attested_at"), int)
        or isinstance(record.get("attested_at"), bool)
    ):
        return None
    for name in (
        "session_digest", "assignment_ref_digest", "worker_task_ref_digest",
        "worker_agent_digest", "worker_turn_digest", "worker_thread_digest",
        "attestation_nonce",
    ):
        if _DIGEST_RE.fullmatch(str(record.get(name, ""))) is None:
            return None
    return record


def _fresh(record: Mapping[str, Any]) -> bool:
    attested_at = record.get("attested_at")
    return (
        isinstance(attested_at, int)
        and not isinstance(attested_at, bool)
        and 0 <= time.time_ns() - attested_at <= _CANDIDATE_MAX_AGE_NS
    )


def fresh_worker_candidate_available(plugin_data: Path) -> bool:
    """Return true when at least one fresh signed SubagentStart candidate exists."""
    try:
        key = _key(plugin_data, create=False)
        sessions = _activation_root(plugin_data) / "sessions"
        _private_directory(sessions)
        for path in sessions.glob("*/dispatch/dispatch-*.json"):
            _private_file(path)
            record = _verified_candidate(
                json.loads(path.read_text(encoding="utf-8")), key,
                states=frozenset({"worker_candidate", "worker_call_authorized"}),
            )
            if record is not None and _fresh(record):
                return True
        return False
    except (AudienceAttestationError, OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return False


def authorize_worker_candidate_call(
    plugin_data: Path, *, task_ref: object, agent_id: object,
    turn_id: object, session_id: object, tool_use_id: object,
) -> bool:
    """Sign the exact host-observed first worker assignment read once."""
    if not all(isinstance(value, str) and value for value in (
        task_ref, agent_id, turn_id, session_id, tool_use_id,
    )):
        return False
    expected = {
        "worker_task_ref_digest": hashlib.sha256(task_ref.encode()).hexdigest(),
        "worker_agent_digest": hashlib.sha256(agent_id.encode()).hexdigest(),
        "worker_turn_digest": hashlib.sha256(turn_id.encode()).hexdigest(),
        "session_digest": hashlib.sha256(session_id.encode()).hexdigest(),
    }
    try:
        key = _key(plugin_data, create=False)
        sessions = _activation_root(plugin_data) / "sessions"
        matches: list[Path] = []
        for path in sessions.glob("*/dispatch/dispatch-*.json"):
            _private_file(path)
            record = _verified_candidate(
                json.loads(path.read_text(encoding="utf-8")), key,
                states=frozenset({"worker_candidate", "worker_call_authorized"}),
            )
            if (
                record is not None and _fresh(record)
                and all(record.get(name) == digest for name, digest in expected.items())
            ):
                matches.append(path)
        if len(matches) != 1:
            return False
        path = matches[0]
        lock_path = path.with_suffix(".server.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "r+") as lock:
            os.chmod(lock_path, 0o600); fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = _verified_candidate(
                json.loads(path.read_text(encoding="utf-8")), key,
                states=frozenset({"worker_candidate", "worker_call_authorized"}),
            )
            tool_digest = hashlib.sha256(tool_use_id.encode()).hexdigest()
            if current is None or not _fresh(current) or any(current.get(name) != digest for name, digest in expected.items()):
                return False
            if current.get("state") == "worker_call_authorized":
                if hmac.compare_digest(str(current.get("authorized_tool_use_digest", "")), tool_digest):
                    return True
                # PreToolUse calls are ordered for one native child. If a
                # schema-level rejection produces no failure hook, the prior
                # authorization was never server-claimed. Replace only that
                # unused same-child authorization; a claimed record has a
                # distinct signed state and cannot reach this branch.
            authorized = dict(current)
            authorized.update({
                "state": "worker_call_authorized",
                "authorized_tool_use_digest": tool_digest,
                "authorized_at": time.time_ns(),
            })
            authorized["signature"] = _sign(authorized, key)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(_canonical(authorized).decode("ascii"), encoding="ascii")
            os.chmod(temporary, 0o600); os.replace(temporary, path); os.chmod(path, 0o600)
            return True
    except (AudienceAttestationError, OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return False


def revoke_worker_candidate_call(
    plugin_data: Path, *, task_ref: object, agent_id: object,
    turn_id: object, session_id: object, tool_use_id: object,
) -> bool:
    """Remove an exact unused host call authorization after tool failure."""
    if not all(isinstance(value, str) and value for value in (
        task_ref, agent_id, turn_id, session_id, tool_use_id,
    )):
        return False
    expected = {
        "worker_task_ref_digest": hashlib.sha256(task_ref.encode()).hexdigest(),
        "worker_agent_digest": hashlib.sha256(agent_id.encode()).hexdigest(),
        "worker_turn_digest": hashlib.sha256(turn_id.encode()).hexdigest(),
        "session_digest": hashlib.sha256(session_id.encode()).hexdigest(),
        "authorized_tool_use_digest": hashlib.sha256(tool_use_id.encode()).hexdigest(),
    }
    try:
        key = _key(plugin_data, create=False)
        sessions = _activation_root(plugin_data) / "sessions"
        for path in sessions.glob("*/dispatch/dispatch-*.json"):
            _private_file(path)
            record = _verified_candidate(
                json.loads(path.read_text(encoding="utf-8")), key,
                states=frozenset({"worker_call_authorized"}),
            )
            if record is None or any(record.get(name) != digest for name, digest in expected.items()):
                continue
            lock_path = path.with_suffix(".server.lock")
            descriptor = os.open(
                lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600,
            )
            with os.fdopen(descriptor, "r+") as lock:
                os.chmod(lock_path, 0o600)
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                current = _verified_candidate(
                    json.loads(path.read_text(encoding="utf-8")), key,
                    states=frozenset({"worker_call_authorized"}),
                )
                if current is None or any(current.get(name) != digest for name, digest in expected.items()):
                    return False
                restored = dict(current)
                restored["state"] = "worker_candidate"
                restored.pop("authorized_tool_use_digest", None)
                restored.pop("authorized_at", None)
                restored["signature"] = _sign(restored, key)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(_canonical(restored).decode("ascii"), encoding="ascii")
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
                os.chmod(path, 0o600)
                return True
        return False
    except (
        AudienceAttestationError, OSError, UnicodeError, json.JSONDecodeError,
        ValueError, TypeError,
    ):
        return False


def claim_worker_candidate(
    plugin_data: Path, *, task_ref: object, connection_nonce: str,
) -> dict[str, str] | None:
    """Atomically claim one exact PreToolUse-authorized candidate once."""
    expected_task_digest = (
        hashlib.sha256(task_ref.encode()).hexdigest()
        if isinstance(task_ref, str) and task_ref else None
    )
    if expected_task_digest is None or not isinstance(connection_nonce, str) or not connection_nonce:
        return None
    try:
        key = _key(plugin_data, create=False)
        sessions = _activation_root(plugin_data) / "sessions"
        _private_directory(sessions)
        matches: list[Path] = []
        for path in sessions.glob("*/dispatch/dispatch-*.json"):
            _private_file(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            record = _verified_candidate(
                value, key, states=frozenset({"worker_call_authorized"}),
            )
            if record is not None and _fresh(record) and record.get("worker_task_ref_digest") == expected_task_digest:
                matches.append(path)
        if len(matches) != 1:
            return None
        path = matches[0]
        lock_path = path.with_suffix(".server.lock")
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "r+") as lock:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            _private_file(path)
            current = _verified_candidate(
                json.loads(path.read_text(encoding="utf-8")),
                key,
                states=frozenset({"worker_call_authorized"}),
            )
            if current is None or not _fresh(current) or current.get("worker_task_ref_digest") != expected_task_digest:
                return None
            claimed = dict(current)
            claimed.update({
                "state": "server_candidate_claimed",
                "server_connection_digest": hashlib.sha256(
                    connection_nonce.encode("ascii")
                ).hexdigest(),
                "server_claimed_at": time.time_ns(),
            })
            claimed["signature"] = _sign(claimed, key)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(_canonical(claimed).decode("ascii"), encoding="ascii")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return {
                "session_digest": str(claimed["session_digest"]),
                "assignment_ref_digest": str(claimed["assignment_ref_digest"]),
                "worker_task_ref_digest": str(claimed["worker_task_ref_digest"]),
                "worker_thread_digest": str(claimed["worker_thread_digest"]),
                "server_connection_digest": str(claimed["server_connection_digest"]),
            }
    except (
        AudienceAttestationError, OSError, UnicodeError, json.JSONDecodeError,
        ValueError, TypeError,
    ):
        return None


def release_worker_candidate_claim(
    plugin_data: Path, *, claim: object, connection_nonce: str,
) -> bool:
    """Restore an authorized candidate after an unsuccessful bootstrap call."""
    if not isinstance(claim, Mapping) or not isinstance(connection_nonce, str) or not connection_nonce:
        return False
    expected_connection = hashlib.sha256(connection_nonce.encode("ascii")).hexdigest()
    if not hmac.compare_digest(str(claim.get("server_connection_digest", "")), expected_connection):
        return False
    expected_task = str(claim.get("worker_task_ref_digest", ""))
    try:
        key = _key(plugin_data, create=False)
        sessions = _activation_root(plugin_data) / "sessions"
        _private_directory(sessions)
        for path in sessions.glob("*/dispatch/dispatch-*.json"):
            _private_file(path)
            record = _verified_candidate(
                json.loads(path.read_text(encoding="utf-8")), key,
                states=frozenset({"server_candidate_claimed"}),
            )
            if (
                record is None
                or record.get("worker_task_ref_digest") != expected_task
                or not hmac.compare_digest(
                    str(record.get("server_connection_digest", "")), expected_connection
                )
            ):
                continue
            lock_path = path.with_suffix(".server.lock")
            descriptor = os.open(
                lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600,
            )
            with os.fdopen(descriptor, "r+") as lock:
                os.chmod(lock_path, 0o600)
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                current = _verified_candidate(
                    json.loads(path.read_text(encoding="utf-8")), key,
                    states=frozenset({"server_candidate_claimed"}),
                )
                if (
                    current is None
                    or current.get("worker_task_ref_digest") != expected_task
                    or not hmac.compare_digest(
                        str(current.get("server_connection_digest", "")), expected_connection
                    )
                ):
                    return False
                restored = dict(current)
                restored["state"] = "worker_call_authorized"
                restored.pop("server_connection_digest", None)
                restored.pop("server_claimed_at", None)
                restored["signature"] = _sign(restored, key)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(_canonical(restored).decode("ascii"), encoding="ascii")
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
                os.chmod(path, 0o600)
                return True
        return False
    except (
        AudienceAttestationError, OSError, UnicodeError, json.JSONDecodeError,
        ValueError, TypeError,
    ):
        return False


def claim_matches_task(claim: object, task_ref_value: object) -> bool:
    if not isinstance(claim, Mapping) or not isinstance(task_ref_value, str):
        return False
    return hmac.compare_digest(
        str(claim.get("worker_task_ref_digest", "")),
        hashlib.sha256(task_ref_value.encode("utf-8")).hexdigest(),
    )
