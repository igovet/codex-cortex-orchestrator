"""Filesystem materialization for SQLite-backed projections.

This module deliberately knows nothing about projection policy or the ledger.
It accepts canonical bytes and a task-relative destination, and provides the
small, crash-safe filesystem operation used by projection workers.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Union


BytesLike = Union[bytes, bytearray, memoryview]


class ProjectionPathError(ValueError):
    """The destination is not a safe path inside the selected task directory."""


class ProjectionDigestError(ValueError):
    """Canonical content or an existing projection failed digest validation."""


@dataclass(frozen=True)
class ProjectionMaterialization:
    """Pure description of a materialization result."""

    path: Path
    digest: str
    materialized: bool


@dataclass(frozen=True)
class ProjectionRemoval:
    """Pure description of an optional projection removal."""

    path: Path
    removed: bool


@dataclass(frozen=True)
class ProjectionVerification:
    """Digest state of one existing filesystem projection.

    A missing or altered export is deliberately a normal repairable state:
    canonical bytes remain in SQLite, so callers can schedule an outbox job
    instead of treating the local file as evidence.  Unsafe paths still fail
    closed with :class:`ProjectionPathError`.
    """

    path: Path
    expected_digest: str
    present: bool
    valid: bool
    actual_digest: str | None


def _task_root(task_dir: os.PathLike[str] | str) -> Path:
    root = Path(task_dir)
    if not root.is_absolute():
        root = root.absolute()
    try:
        info = root.lstat()
    except FileNotFoundError as exc:
        raise ProjectionPathError("task directory does not exist") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ProjectionPathError("task directory must be a real directory")
    return root


def _safe_destination(task_dir: os.PathLike[str] | str, export_path: os.PathLike[str] | str) -> tuple[Path, Path]:
    """Validate an export path without following symlinks."""
    root = _task_root(task_dir)
    raw = os.fspath(export_path)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ProjectionPathError("export path must be a non-empty task-relative path")
    candidate = Path(raw)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ProjectionPathError("export path must not be absolute or contain traversal")
    destination = root.joinpath(candidate)
    # lstat every existing component.  This catches both ordinary and dangling
    # symlinks before any directory is created or file is replaced.
    current = root
    for part in candidate.parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ProjectionPathError("export path must not contain symlinks")
    return root, destination


def _private_parents(root: Path, destination: Path) -> Path:
    parent = destination.parent
    missing: list[Path] = []
    current = parent
    # Walk all the way to the selected task root.  A valid projection often
    # targets an already-created directory such as ``delegations/``; that is
    # not an escape.  Validate each existing ancestor with lstat and remember
    # only absent directories for private creation below.
    while current != root:
        try:
            info = current.lstat()
        except FileNotFoundError:
            missing.append(current)
        else:
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ProjectionPathError("export parent must be a real directory")
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    current = parent
    while True:
        if current.is_symlink() or not current.is_dir():
            raise ProjectionPathError("export parent must be a real directory")
        current.chmod(0o700)
        if current == root:
            break
        current = current.parent
    return parent


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def materialize_projection(
    task_dir: os.PathLike[str] | str,
    export_path: os.PathLike[str] | str,
    canonical_bytes: BytesLike,
    expected_digest: str,
) -> ProjectionMaterialization:
    """Atomically materialize canonical bytes at a safe task-relative path.

    Existing files are left untouched when their digest matches.  A different
    existing file is treated as tampering and is never silently overwritten.
    """
    root, destination = _safe_destination(task_dir, export_path)
    if not isinstance(canonical_bytes, (bytes, bytearray, memoryview)):
        raise TypeError("canonical_bytes must be bytes-like")
    data = bytes(canonical_bytes)
    if not isinstance(expected_digest, str) or _digest(data) != expected_digest.lower():
        raise ProjectionDigestError("canonical projection bytes do not match expected digest")
    parent = _private_parents(root, destination)

    try:
        info = destination.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ProjectionPathError("projection destination must be a regular file")
        existing = destination.read_bytes()
        if _digest(existing) != expected_digest.lower():
            raise ProjectionDigestError("existing projection digest does not match expected digest")
        destination.chmod(0o600)
        return ProjectionMaterialization(destination, expected_digest.lower(), False)

    temporary: Path | None = None
    try:
        fd, name = __import__("tempfile").mkstemp(prefix=f".{destination.name}.tmp-", dir=parent)
        temporary = Path(name)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
        destination.chmod(0o600)
        with destination.open("rb") as stream:
            if _digest(stream.read()) != expected_digest.lower():
                raise ProjectionDigestError("materialized projection failed final digest check")
        try:
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        return ProjectionMaterialization(destination, expected_digest.lower(), True)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def verify_projection(
    task_dir: os.PathLike[str] | str,
    export_path: os.PathLike[str] | str,
    expected_digest: str,
) -> ProjectionVerification:
    """Read and classify an optional projection without mutating it.

    The validation uses the exact same no-symlink containment checks as the
    materializer.  It intentionally reports a digest mismatch rather than
    overwriting it, leaving policy (fail, remove, or schedule a repair) to the
    SQLite-backed projection service.
    """
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise ProjectionDigestError("projection expected digest is invalid")
    try:
        int(expected_digest, 16)
    except ValueError as exc:
        raise ProjectionDigestError("projection expected digest is invalid") from exc
    _root, destination = _safe_destination(task_dir, export_path)
    try:
        info = destination.lstat()
    except FileNotFoundError:
        return ProjectionVerification(destination, expected_digest.lower(), False, False, None)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ProjectionPathError("projection destination must be a regular file")
    actual = _digest(destination.read_bytes())
    return ProjectionVerification(
        destination, expected_digest.lower(), True, actual == expected_digest.lower(), actual,
    )


def remove_optional_projection(
    task_dir: os.PathLike[str] | str,
    export_path: os.PathLike[str] | str,
) -> ProjectionRemoval:
    """Remove one optional regular-file projection, with the same path checks."""
    _root, destination = _safe_destination(task_dir, export_path)
    try:
        info = destination.lstat()
    except FileNotFoundError:
        return ProjectionRemoval(destination, False)
    if stat.S_ISLNK(info.st_mode):
        raise ProjectionPathError("optional projection must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise ProjectionPathError("optional projection must be a regular file")
    destination.unlink()
    return ProjectionRemoval(destination, True)
